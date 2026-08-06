from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import os

import requests

TOKENROUTER_DEFAULT_BASE_URL = "https://api.tokenrouter.com/v1"
TOKENROUTER_DEFAULT_MODEL = "moonshotai/kimi-k3-free"
TOKENROUTER_DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
TOKENROUTER_DEFAULT_READ_TIMEOUT_SECONDS = 180.0
TOKENROUTER_TIMEOUT_SECONDS = TOKENROUTER_DEFAULT_READ_TIMEOUT_SECONDS

TOKENROUTER_MODEL_LABELS: dict[str, str] = {
    "moonshotai/kimi-k3-free": "Kimi K3 Free",
}
TOKENROUTER_MODEL_IDS = list(TOKENROUTER_MODEL_LABELS.keys())


class TokenRouterError(RuntimeError):
    """Base error for TokenRouter failures."""


class TokenRouterConfigError(TokenRouterError):
    """Raised when the client is not configured correctly."""


class TokenRouterAuthenticationError(TokenRouterError):
    """Raised when TokenRouter rejects authentication."""


class TokenRouterRateLimitError(TokenRouterError):
    """Raised when TokenRouter rate limits the request."""


class TokenRouterInsufficientBalanceError(TokenRouterError):
    """Raised when the account has insufficient balance or credits."""


class TokenRouterUnsupportedModelError(TokenRouterError):
    """Raised when the requested model is not supported."""


class TokenRouterConnectionError(TokenRouterError):
    """Raised when the request cannot reach TokenRouter."""


class TokenRouterTimeoutError(TokenRouterError):
    """Raised when the request times out."""


class TokenRouterResponseError(TokenRouterError):
    """Raised when TokenRouter returns an invalid or empty response."""

    def __init__(self, message: str, diagnostic: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic or {}


@dataclass(slots=True)
class TokenRouterChatResult:
    content: str
    model: str
    raw_response: dict[str, Any]
    usage: dict[str, Any] | None = None
    usage_metadata: dict[str, Any] | None = None
    response_diagnostic: dict[str, Any] | None = None


@dataclass(slots=True)
class TokenRouterClient:
    api_key: str | None = None
    base_url: str = TOKENROUTER_DEFAULT_BASE_URL
    timeout_seconds: float = TOKENROUTER_TIMEOUT_SECONDS
    connect_timeout_seconds: float = TOKENROUTER_DEFAULT_CONNECT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        self.base_url = _normalize_base_url(self.base_url or TOKENROUTER_DEFAULT_BASE_URL)
        if self.api_key is None:
            self.api_key = os.environ.get("TOKENROUTER_API_KEY")
        self.timeout_seconds = _sanitize_timeout_seconds(self.timeout_seconds, TOKENROUTER_DEFAULT_READ_TIMEOUT_SECONDS)
        self.connect_timeout_seconds = _sanitize_timeout_seconds(
            self.connect_timeout_seconds,
            TOKENROUTER_DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(str(self.api_key or "").strip())

    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
        top_p: float | None = None,
    ) -> TokenRouterChatResult:
        model_id = str(model).strip()
        if not model_id:
            raise TokenRouterConfigError("No TokenRouter model was selected.")
        if model_id not in TOKENROUTER_MODEL_IDS:
            raise TokenRouterUnsupportedModelError(
                f"Unsupported TokenRouter model: {model_id}. Choose one of the documented model identifiers."
            )
        if not self.has_api_key:
            raise TokenRouterConfigError("TOKENROUTER_API_KEY is missing.")

        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if top_p is not None:
            payload["top_p"] = float(top_p)

        response = self._post_json("/chat/completions", payload)
        content = _extract_chat_content(response)
        if not str(content or "").strip():
            raise TokenRouterResponseError(
                "TokenRouter returned an empty analysis response.",
                diagnostic=_response_shape_diagnostic(response),
            )

        return TokenRouterChatResult(
            content=str(content),
            model=str(response.get("model", model_id)),
            raw_response=response,
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else None,
            usage_metadata=_extract_usage_metadata(response),
            response_diagnostic=_response_shape_diagnostic(response),
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = _join_url(self.base_url, path)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=(self.connect_timeout_seconds, self.timeout_seconds),
            )
        except requests.Timeout as exc:
            raise TokenRouterTimeoutError("TokenRouter request timed out.") from exc
        except requests.ConnectionError as exc:
            raise TokenRouterConnectionError(f"TokenRouter connection failed: {exc}") from exc
        except requests.RequestException as exc:
            raise TokenRouterError(f"TokenRouter request failed: {exc}") from exc

        if response.status_code >= 400:
            raise _map_http_error(response.status_code, response.text)

        response_bytes = response.content

        if not response_bytes:
            raise TokenRouterResponseError("TokenRouter returned an empty response body.")

        try:
            response_json = json.loads(response_bytes.decode("utf-8"))
        except Exception as exc:
            raise TokenRouterResponseError("TokenRouter returned malformed JSON.") from exc

        if not isinstance(response_json, dict):
            raise TokenRouterResponseError("TokenRouter returned an unexpected response shape.")

        response_json.setdefault("_http_status", response.status_code)
        return response_json


def _normalize_base_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    return cleaned or TOKENROUTER_DEFAULT_BASE_URL


def _join_url(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url)}{path}"


def _extract_chat_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        top_level_fallback = _extract_text_value(response.get("output_text")) or _extract_text_value(response.get("content"))
        if top_level_fallback:
            return top_level_fallback
        raise TokenRouterResponseError(
            "TokenRouter returned no choices.",
            diagnostic=_response_shape_diagnostic(response),
        )

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise TokenRouterResponseError(
            "TokenRouter returned an invalid choice entry.",
            diagnostic=_response_shape_diagnostic(response),
        )

    message = first_choice.get("message")
    message_dict = message if isinstance(message, dict) else {}

    final_candidates = [
        message_dict.get("content"),
        message_dict.get("output_text"),
        message_dict.get("text"),
        first_choice.get("content"),
        first_choice.get("text"),
        first_choice.get("output_text"),
        response.get("output_text"),
        response.get("content"),
    ]
    for candidate in final_candidates:
        text = _extract_text_value(candidate)
        if text:
            return text

    reasoning_candidates = [
        message_dict.get("reasoning_content"),
        message_dict.get("reasoning"),
        first_choice.get("reasoning_content"),
        first_choice.get("reasoning"),
        response.get("reasoning_content"),
        response.get("reasoning"),
    ]
    for candidate in reasoning_candidates:
        text = _extract_text_value(candidate)
        if text:
            return text

    raise TokenRouterResponseError(
        "TokenRouter returned no extractable assistant content.",
        diagnostic=_response_shape_diagnostic(response),
    )


def _extract_text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _extract_text_value(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "output_text", "value"):
            if key in value:
                text = _extract_text_value(value.get(key))
                if text:
                    return text
        return ""
    return str(value).strip()


def _extract_usage_metadata(response: dict[str, Any]) -> dict[str, Any] | None:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    prompt_tokens = int(float(usage.get("prompt_tokens", 0) or 0))
    completion_tokens = int(float(usage.get("completion_tokens", 0) or 0))
    total_tokens = int(float(usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens)))

    cost_fields = (
        usage.get("estimated_cost"),
        usage.get("cost"),
        response.get("estimated_cost"),
        response.get("cost"),
    )
    estimated_cost: float | None = None
    for field in cost_fields:
        if isinstance(field, (int, float)):
            estimated_cost = float(field)
            break
        if isinstance(field, str):
            try:
                estimated_cost = float(field)
                break
            except ValueError:
                continue
        if isinstance(field, dict):
            for key in ("total", "total_cost", "usd", "amount"):
                raw_value = field.get(key)
                if isinstance(raw_value, (int, float)):
                    estimated_cost = float(raw_value)
                    break
                if isinstance(raw_value, str):
                    try:
                        estimated_cost = float(raw_value)
                        break
                    except ValueError:
                        continue
            if estimated_cost is not None:
                break

    metadata = {
        "model": str(response.get("model", "") or ""),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if estimated_cost is not None:
        metadata["estimated_cost"] = estimated_cost
    return metadata


def _response_shape_diagnostic(response: dict[str, Any]) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "http_status": response.get("_http_status"),
        "top_level_keys": sorted(str(key) for key in response.keys()),
    }
    choices = response.get("choices")
    diagnostic["choices_count"] = len(choices) if isinstance(choices, list) else 0
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return diagnostic

    first_choice = choices[0]
    diagnostic["choice_keys"] = sorted(str(key) for key in first_choice.keys())

    message = first_choice.get("message")
    if not isinstance(message, dict):
        diagnostic["message_type"] = type(message).__name__
        return diagnostic

    diagnostic["message_keys"] = sorted(str(key) for key in message.keys())
    content = message.get("content")
    diagnostic["content_type"] = type(content).__name__
    if isinstance(content, str):
        diagnostic["content_length"] = len(content)
    elif isinstance(content, list):
        diagnostic["content_items"] = len(content)

    output_text = message.get("output_text")
    diagnostic["output_text_type"] = type(output_text).__name__
    if isinstance(output_text, str):
        diagnostic["output_text_length"] = len(output_text)

    reasoning = message.get("reasoning")
    diagnostic["reasoning_type"] = type(reasoning).__name__
    if isinstance(reasoning, str):
        diagnostic["reasoning_length"] = len(reasoning)

    reasoning_content = message.get("reasoning_content")
    diagnostic["reasoning_content_type"] = type(reasoning_content).__name__
    if isinstance(reasoning_content, str):
        diagnostic["reasoning_content_length"] = len(reasoning_content)
    elif isinstance(reasoning_content, list):
        diagnostic["reasoning_content_items"] = len(reasoning_content)

    return diagnostic


def _map_http_error(status: int | None, body: str) -> TokenRouterError:
    detail = _extract_error_detail(body) or body or f"HTTP {status}"

    if status in (401, 403):
        return TokenRouterAuthenticationError("TokenRouter rejected the API key.")
    if status == 402:
        return TokenRouterInsufficientBalanceError("TokenRouter reports insufficient balance or credits.")
    if status == 429:
        return TokenRouterRateLimitError("TokenRouter rate limited the request.")
    if status in (400, 404):
        lower_detail = detail.lower()
        if "model" in lower_detail or "not found" in lower_detail or "unsupported" in lower_detail:
            return TokenRouterUnsupportedModelError("TokenRouter rejected the selected model.")
        return TokenRouterResponseError(f"TokenRouter rejected the request: {detail}")
    if status is not None and status >= 500:
        return TokenRouterError(f"TokenRouter provider error: {detail}")

    return TokenRouterError(f"TokenRouter request failed: {detail}")


def _sanitize_timeout_seconds(value: object, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if numeric <= 0:
        return fallback
    return numeric


def _extract_error_detail(body: str) -> str:
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except Exception:
        return body.strip()

    if isinstance(parsed, dict):
        error_obj = parsed.get("error")
        if isinstance(error_obj, dict):
            for key in ("message", "detail", "type"):
                value = error_obj.get(key)
                if value:
                    return str(value)
        for key in ("message", "detail", "error"):
            value = parsed.get(key)
            if value:
                return str(value)
    return body.strip()

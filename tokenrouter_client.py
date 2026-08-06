from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import os
import socket
from urllib import error, parse, request

TOKENROUTER_DEFAULT_BASE_URL = "https://tokenrouter.me/v1"
TOKENROUTER_DEFAULT_MODEL = "kimi-k2p6"
TOKENROUTER_TIMEOUT_SECONDS = 30.0

TOKENROUTER_MODEL_LABELS: dict[str, str] = {
    "kimi-k2p7-code": "Kimi 2.7 Code",
    "kimi-k2p7-code-fast": "Kimi K2.7 Code Fast",
    "kimi-k2p6": "Kimi K2.6",
    "kimi-k2p5": "Kimi K2.5",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "qwen3p7-plus": "Qwen 3.7 Plus",
    "qwen3p6-plus": "Qwen 3.6 Plus",
    "glm-5p1": "GLM 5.1",
    "glm-5p1-fast": "GLM 5.1 Fast",
    "gpt-oss-120b": "GPT-OSS 120B",
    "minimax-m3": "MiniMax M3",
    "minimax-m2p7": "MiniMax M2.7",
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


@dataclass(slots=True)
class TokenRouterChatResult:
    content: str
    model: str
    raw_response: dict[str, Any]
    usage: dict[str, Any] | None = None


@dataclass(slots=True)
class TokenRouterClient:
    api_key: str | None = None
    base_url: str = TOKENROUTER_DEFAULT_BASE_URL
    timeout_seconds: float = TOKENROUTER_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        self.base_url = _normalize_base_url(self.base_url or TOKENROUTER_DEFAULT_BASE_URL)
        if self.api_key is None:
            self.api_key = os.environ.get("TOKENROUTER_API_KEY")

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
            raise TokenRouterResponseError("TokenRouter returned an empty analysis response.")

        return TokenRouterChatResult(
            content=str(content),
            model=str(response.get("model", model_id)),
            raw_response=response,
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else None,
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = _join_url(self.base_url, path)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        req = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                response_bytes = response.read()
        except error.HTTPError as exc:
            raise _map_http_error(exc) from exc
        except error.URLError as exc:
            raise _map_url_error(exc) from exc
        except TimeoutError as exc:
            raise TokenRouterTimeoutError("TokenRouter request timed out.") from exc

        if not response_bytes:
            raise TokenRouterResponseError("TokenRouter returned an empty response body.")

        try:
            response_json = json.loads(response_bytes.decode("utf-8"))
        except Exception as exc:
            raise TokenRouterResponseError("TokenRouter returned malformed JSON.") from exc

        if not isinstance(response_json, dict):
            raise TokenRouterResponseError("TokenRouter returned an unexpected response shape.")

        return response_json


def _normalize_base_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    return cleaned or TOKENROUTER_DEFAULT_BASE_URL


def _join_url(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url)}{path}"


def _extract_chat_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TokenRouterResponseError("TokenRouter returned no choices.")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise TokenRouterResponseError("TokenRouter returned an invalid choice entry.")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise TokenRouterResponseError("TokenRouter returned an invalid message entry.")

    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(item) for item in content if item is not None)
    return str(content or "")


def _map_http_error(exc: error.HTTPError) -> TokenRouterError:
    status = getattr(exc, "code", None)
    body = _read_error_body(exc)
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


def _map_url_error(exc: error.URLError) -> TokenRouterError:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.timeout):
        return TokenRouterTimeoutError("TokenRouter request timed out.")
    if isinstance(reason, TimeoutError):
        return TokenRouterTimeoutError("TokenRouter request timed out.")
    if isinstance(reason, OSError):
        return TokenRouterConnectionError(f"TokenRouter connection failed: {reason}")
    return TokenRouterConnectionError(f"TokenRouter connection failed: {reason or exc}")


def _read_error_body(exc: error.HTTPError) -> str:
    try:
        raw = exc.read()
    except Exception:
        raw = b""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return str(raw)


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

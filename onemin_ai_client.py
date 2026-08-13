from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from typing import Any

import httpx

ONEMIN_AI_DEFAULT_BASE_URL = "https://api.1min.ai"
ONEMIN_AI_DEFAULT_MODEL = "gpt-5.6-sol"
ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
ONEMIN_AI_DEFAULT_READ_TIMEOUT_SECONDS = 180.0
ONEMIN_AI_TIMEOUT_SECONDS = ONEMIN_AI_DEFAULT_READ_TIMEOUT_SECONDS
ONEMIN_AI_CHAT_ENDPOINT_PATH = "/api/chat-with-ai"


class OneMinAIError(RuntimeError):
    """Base error for 1min.AI failures."""


class OneMinAIConfigError(OneMinAIError):
    """Raised when 1min.AI configuration is invalid."""


class OneMinAIAuthenticationError(OneMinAIError):
    """Raised when 1min.AI rejects authentication."""


class OneMinAIRateLimitError(OneMinAIError):
    """Raised when 1min.AI rate limits a request."""


class OneMinAIInsufficientCreditsError(OneMinAIError):
    """Raised when the account has insufficient credits."""


class OneMinAIUnsupportedModelError(OneMinAIError):
    """Raised when the requested model is unsupported."""


class OneMinAIConnectionError(OneMinAIError):
    """Raised when the request cannot reach 1min.AI."""


class OneMinAITimeoutError(OneMinAIError):
    """Raised when a 1min.AI request times out."""


class OneMinAIResponseError(OneMinAIError):
    """Raised when 1min.AI returns an invalid or empty response."""

    def __init__(self, message: str, diagnostic: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic or {}


@dataclass(slots=True)
class OneMinAIChatResult:
    content: str
    model: str
    record_id: str
    status: str
    raw_response: dict[str, Any]
    response_diagnostic: dict[str, Any]


@dataclass(slots=True)
class OneMinAIClient:
    api_key: str | None = None
    base_url: str = ONEMIN_AI_DEFAULT_BASE_URL
    timeout_seconds: float = ONEMIN_AI_TIMEOUT_SECONDS
    connect_timeout_seconds: float = ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        self.base_url = normalize_base_url(self.base_url)
        if self.api_key is None:
            self.api_key = os.environ.get("ONEMIN_AI_API_KEY")
        self.timeout_seconds = sanitize_timeout_seconds(self.timeout_seconds, ONEMIN_AI_DEFAULT_READ_TIMEOUT_SECONDS)
        self.connect_timeout_seconds = sanitize_timeout_seconds(
            self.connect_timeout_seconds,
            ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(str(self.api_key or "").strip())

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}{ONEMIN_AI_CHAT_ENDPOINT_PATH}"

    def chat_with_ai(self, *, model: str, prompt: str, web_search: bool = False) -> OneMinAIChatResult:
        model_id = str(model or "").strip()
        prompt_text = str(prompt or "")
        if not self.has_api_key:
            raise OneMinAIConfigError("ONEMIN_AI_API_KEY is missing.")
        if not model_id:
            raise OneMinAIConfigError("No 1min.AI model was selected.")
        if not prompt_text.strip():
            raise OneMinAIConfigError("The 1min.AI prompt is empty.")

        payload = {
            "type": "UNIFY_CHAT_WITH_AI",
            "model": model_id,
            "promptObject": {
                "prompt": prompt_text,
                "settings": {
                    "withMemories": False,
                    "webSearchSettings": {"webSearch": bool(web_search)},
                },
            },
        }
        timeout = httpx.Timeout(
            timeout=self.timeout_seconds,
            connect=self.connect_timeout_seconds,
            read=self.timeout_seconds,
            write=self.timeout_seconds,
        )
        try:
            response = httpx.post(
                self.chat_url,
                headers={"Content-Type": "application/json", "API-KEY": str(self.api_key)},
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise OneMinAITimeoutError("1min.AI request timed out.") from exc
        except httpx.RequestError as exc:
            raise OneMinAIConnectionError("Could not reach 1min.AI.") from exc

        if not response.content:
            if response.is_error:
                raise _map_http_error(response.status_code, _response_error_payload(response), self.api_key)
            raise OneMinAIResponseError(
                "1min.AI returned an empty response body.",
                diagnostic={"http_status": response.status_code},
            )
        if response.is_error:
            raise _map_http_error(response.status_code, _response_error_payload(response), self.api_key)

        try:
            response_data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OneMinAIResponseError(
                "1min.AI returned invalid JSON.",
                diagnostic={"http_status": response.status_code},
            ) from exc
        return _parse_success_response(response_data, model_id, response.status_code)


def messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Convert ordered chat messages to a self-contained 1min.AI prompt."""
    sections: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        role = str(message.get("role", "message") or "message").strip().upper()
        label = "SYSTEM INSTRUCTIONS" if role == "SYSTEM" else "USER REQUEST" if role == "USER" else f"{role} MESSAGE"
        sections.append(f"{label}:\n{content}")
    return "\n\n".join(sections)


def normalize_base_url(base_url: str | None) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    return cleaned or ONEMIN_AI_DEFAULT_BASE_URL


def sanitize_timeout_seconds(value: object, default: float = ONEMIN_AI_DEFAULT_READ_TIMEOUT_SECONDS) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if math.isfinite(parsed) and parsed > 0 else float(default)


def _parse_success_response(response_data: object, requested_model: str, http_status: int) -> OneMinAIChatResult:
    if not isinstance(response_data, dict):
        raise OneMinAIResponseError(
            "1min.AI returned a JSON value instead of an object.",
            diagnostic={"http_status": http_status, "response_type": type(response_data).__name__},
        )
    top_level_keys = sorted(str(key) for key in response_data.keys())
    ai_record = response_data.get("aiRecord")
    if not isinstance(ai_record, dict):
        raise OneMinAIResponseError(
            "1min.AI response did not contain an aiRecord object.",
            diagnostic={"http_status": http_status, "top_level_keys": top_level_keys, "ai_record_present": False},
        )
    status = str(ai_record.get("status") or "").strip()
    if status != "SUCCESS":
        raise OneMinAIResponseError(
            "1min.AI did not complete the analysis successfully.",
            diagnostic={
                "http_status": http_status,
                "top_level_keys": top_level_keys,
                "ai_record_present": True,
                "record_status": status or "missing",
                "record_id": str(ai_record.get("uuid") or ""),
            },
        )
    detail = ai_record.get("aiRecordDetail")
    if not isinstance(detail, dict):
        raise OneMinAIResponseError(
            "1min.AI response did not contain an aiRecordDetail object.",
            diagnostic={
                "http_status": http_status,
                "top_level_keys": top_level_keys,
                "ai_record_present": True,
                "ai_record_detail_present": False,
                "record_status": status,
                "record_id": str(ai_record.get("uuid") or ""),
            },
        )
    result_object = detail.get("resultObject")
    if not isinstance(result_object, list):
        raise OneMinAIResponseError(
            "1min.AI response did not contain a valid result object.",
            diagnostic=_response_diagnostic(http_status, top_level_keys, ai_record, requested_model, True, 0, 0),
        )
    result_items = [item for item in result_object if isinstance(item, str) and item.strip()]
    content = "\n\n".join(item.strip() for item in result_items)
    if not content:
        raise OneMinAIResponseError(
            "1min.AI returned an empty analysis result.",
            diagnostic=_response_diagnostic(http_status, top_level_keys, ai_record, requested_model, True, len(result_object), 0),
        )
    resolved_model = str(ai_record.get("model") or requested_model)
    return OneMinAIChatResult(
        content=content,
        model=resolved_model,
        record_id=str(ai_record.get("uuid") or ""),
        status=status,
        raw_response=response_data,
        response_diagnostic=_response_diagnostic(http_status, top_level_keys, ai_record, requested_model, True, len(result_object), len(content)),
    )


def _response_diagnostic(
    http_status: int,
    top_level_keys: list[str],
    ai_record: dict[str, Any],
    requested_model: str,
    detail_present: bool,
    result_items: int,
    result_characters: int,
) -> dict[str, Any]:
    return {
        "http_status": http_status,
        "top_level_keys": top_level_keys,
        "ai_record_present": True,
        "ai_record_detail_present": detail_present,
        "record_status": str(ai_record.get("status") or "missing"),
        "record_id": str(ai_record.get("uuid") or ""),
        "requested_model": requested_model,
        "resolved_model": str(ai_record.get("model") or ""),
        "result_items": result_items,
        "result_characters": result_characters,
    }


def _response_error_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _map_http_error(status_code: int, payload: dict[str, Any], api_key: str | None) -> OneMinAIError:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    provider_code = _sanitize_provider_message(error.get("code"), api_key)
    code = provider_code.lower()
    message = _sanitize_provider_message(error.get("message"), api_key)
    combined = f"{code} {message}".lower()
    if status_code in {401, 403}:
        return OneMinAIAuthenticationError("1min.AI rejected the API key.")
    if status_code == 402 or any(term in combined for term in ("insufficient credit", "insufficient balance", "credit exhausted", "balance exhausted")):
        return OneMinAIInsufficientCreditsError("1min.AI reports insufficient credits.")
    if status_code == 429:
        return OneMinAIRateLimitError("1min.AI rate limited the request.")
    if status_code in {400, 404, 422} and "model" in combined:
        return OneMinAIUnsupportedModelError("The configured 1min.AI model is not supported.")
    if 500 <= status_code <= 599:
        return OneMinAIError(f"1min.AI provider failure (HTTP {status_code}).")
    if status_code in {400, 404, 422}:
        suffix = f": {message}" if message else ""
        diagnostic: dict[str, Any] = {
            "http_status": status_code,
            "provider_error_code": provider_code,
            "provider_error_message": message,
        }
        validation_details = _sanitize_provider_validation_details(error.get("details"), api_key)
        if validation_details:
            diagnostic["provider_validation_details"] = validation_details
        return OneMinAIResponseError(
            f"1min.AI rejected the request (HTTP {status_code}){suffix}.",
            diagnostic=diagnostic,
        )
    return OneMinAIError(f"1min.AI request failed (HTTP {status_code}).")


def _sanitize_provider_message(value: object, api_key: str | None) -> str:
    message = " ".join(str(value or "").split())[:300]
    if api_key:
        message = message.replace(str(api_key), "[redacted]")
    return message


def _sanitize_provider_validation_details(value: object, api_key: str | None) -> list[dict[str, str]]:
    details = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    sanitized: list[dict[str, str]] = []
    for detail in details:
        if not isinstance(detail, dict) or not set(detail).issubset({"field", "message"}):
            continue
        field = detail.get("field")
        message = detail.get("message")
        if not isinstance(field, str) or not isinstance(message, str):
            continue
        sanitized.append(
            {
                "field": _sanitize_provider_message(field, api_key),
                "message": _sanitize_provider_message(message, api_key),
            }
        )
    return sanitized
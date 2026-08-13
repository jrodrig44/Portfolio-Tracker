from __future__ import annotations

from datetime import datetime
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import re
from typing import Any

_SECRET_PATTERN = re.compile(r"(?i)(api[-_ ]?key|authorization|bearer|token|password|secret)\s*[:=]?\s*(?:bearer\s+)?([^,;\s]+)")
_SENSITIVE_KEYS = {"api_key", "authorization", "password", "token", "secret", "prompt", "response", "raw_response", "portfolio", "holdings", "shares", "cost_basis", "portfolio_value", "contribution", "espp"}
_STANDARD_RECORD_KEYS = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)


def _sanitize_text(value: object, limit: int = 500) -> str:
    text = str(value or "")
    text = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return text[:limit]


def _safe_value(key: str, value: object) -> object:
    normalized_key = key.casefold()
    if normalized_key in _SENSITIVE_KEYS or any(part in normalized_key for part in ("api_key", "authorization", "password", "token", "secret")):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): _safe_value(str(item_key), item_value) for item_key, item_value in value.items() if str(item_key).casefold() not in _SENSITIVE_KEYS}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(key, item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _sanitize_text(value) if isinstance(value, str) else value
    return _sanitize_text(value)


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "process": getattr(record, "process_name", "unknown"),
            "logger": record.name,
            "event": getattr(record, "event", "log_message"),
            "message": _sanitize_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD_RECORD_KEYS or key in fields or key == "process_name":
                continue
            fields[key] = _safe_value(key, value)
        try:
            return json.dumps(fields, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            fields = {key: _safe_value(key, value) for key, value in fields.items()}
            return json.dumps(fields, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"{record.levelname} {record.name}: {_sanitize_text(record.getMessage())}"


def configure_structured_logging(
    logger_name: str,
    log_path: Path,
    process_name: str,
    level: int = logging.INFO,
) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False
    try:
        resolved_path = Path(log_path).resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        handler_exists = any(getattr(handler, "_structured_log_path", None) == str(resolved_path) for handler in logger.handlers)
        if not handler_exists:
            file_handler = TimedRotatingFileHandler(
                resolved_path,
                when="midnight",
                interval=1,
                backupCount=14,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setFormatter(StructuredJsonFormatter())
            file_handler._structured_log_path = str(resolved_path)
            file_handler._structured_process_name = process_name
            logger.addHandler(file_handler)
        if not any(getattr(handler, "_structured_console", False) for handler in logger.handlers):
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            console_handler.setFormatter(_ConsoleFormatter())
            console_handler._structured_console = True
            logger.addHandler(console_handler)
    except Exception as exc:
        if not any(getattr(handler, "_structured_fallback", False) for handler in logger.handlers):
            fallback = logging.StreamHandler()
            fallback.setLevel(logging.WARNING)
            fallback.setFormatter(_ConsoleFormatter())
            fallback._structured_fallback = True
            logger.addHandler(fallback)
        logger.warning("Structured logging initialization failed: %s", _sanitize_text(exc))
    logger._structured_process_name = process_name
    return logger


def log_event(logger: logging.Logger, event: str, message: str, level: int = logging.INFO, **fields: object) -> None:
    safe_fields = {"event": event, "process_name": getattr(logger, "_structured_process_name", "unknown")}
    safe_fields.update(fields)
    logger.log(level, message, extra=safe_fields)

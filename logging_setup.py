from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import json
import logging
import os
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
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "pid": record.process,
            "thread": record.threadName,
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


class DateStampedFileHandler(logging.FileHandler):
    """Write directly to a daily per-process file without renaming active files.

    Do not replace this with ``TimedRotatingFileHandler``. NiceGUI and its
    research child can overlap temporarily, and Windows locks open files;
    rename-based midnight rollover previously failed with ``WinError 32``.
    Files named by date and PID eliminate active-file rename contention, while
    best-effort retention leaves a locked old file for a later cleanup pass.
    """

    def __init__(
        self,
        logical_path: Path,
        *,
        retention_days: int = 14,
        date_provider: Callable[[], date] | None = None,
        process_id: int | None = None,
    ) -> None:
        self.logical_path = Path(logical_path).resolve()
        self.retention_days = retention_days
        self._date_provider = date_provider or (lambda: datetime.now().astimezone().date())
        self.process_id = process_id if process_id is not None else os.getpid()
        self._active_date = self._date_provider()
        super().__init__(self._path_for_date(self._active_date), mode="a", encoding="utf-8", delay=True)
        self._cleanup_expired_files(self._active_date)

    def _path_for_date(self, day: date) -> Path:
        return self.logical_path.with_name(
            f"{self.logical_path.stem}.{day.isoformat()}.{self.process_id}{self.logical_path.suffix}"
        )

    def _cleanup_expired_files(self, current_day: date) -> None:
        cutoff = current_day - timedelta(days=self.retention_days - 1)
        prefix = f"{self.logical_path.stem}."
        suffix = self.logical_path.suffix
        legacy_prefix = f"{self.logical_path.name}."
        candidates: dict[Path, str] = {}
        for candidate in self.logical_path.parent.glob(f"{prefix}*{suffix}"):
            stamped_text = candidate.name[len(prefix):-len(suffix)] if suffix else candidate.name[len(prefix):]
            parts = stamped_text.split(".", 1)
            if len(parts) == 1 or parts[1].isdigit():
                candidates[candidate] = parts[0]
        for candidate in self.logical_path.parent.glob(f"{legacy_prefix}*"):
            candidates[candidate] = candidate.name[len(legacy_prefix):]
        for candidate, date_text in candidates.items():
            try:
                candidate_day = date.fromisoformat(date_text)
            except ValueError:
                continue
            if candidate_day >= cutoff or candidate == Path(self.baseFilename):
                continue
            try:
                candidate.unlink()
            except OSError:
                # Another process may still own an expired file on Windows.
                # It remains eligible for cleanup during the next initialization/day change.
                continue

    def _switch_date_if_needed(self) -> None:
        current_day = self._date_provider()
        if current_day == self._active_date:
            return
        if self.stream is not None:
            self.stream.flush()
            self.stream.close()
            self.stream = None
        self._active_date = current_day
        self.baseFilename = str(self._path_for_date(current_day))
        self._cleanup_expired_files(current_day)

    def emit(self, record: logging.LogRecord) -> None:
        self._switch_date_if_needed()
        super().emit(record)


def configure_structured_logging(
    logger_name: str,
    log_path: Path,
    process_name: str,
    level: int = logging.INFO,
    *,
    date_provider: Callable[[], date] | None = None,
    process_id: int | None = None,
) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False
    try:
        resolved_path = Path(log_path).resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        handler_exists = any(getattr(handler, "_structured_log_path", None) == str(resolved_path) for handler in logger.handlers)
        if not handler_exists:
            file_handler = DateStampedFileHandler(
                resolved_path,
                retention_days=14,
                date_provider=date_provider,
                process_id=process_id,
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

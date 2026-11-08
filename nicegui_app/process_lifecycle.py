from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from logging_setup import configure_structured_logging, log_event


SHUTDOWN_MESSAGE = "Portfolio Tracker is shutting down. No new changes can be started."
PROTECTED_OPERATION_DRAIN_TIMEOUT_SECONDS = 20.0
SHUTDOWN_LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "nicegui.jsonl"
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0

LOGGER = logging.getLogger("portfolio_tracker")
_LOG_LOCK = threading.Lock()


class ShutdownInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class ActiveOperation:
    operation_id: str
    name: str
    started_at: float
    elapsed_seconds: float


class LifecycleCoordinator:
    """Reject new protected work and track admitted operations through completion."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._operations: dict[str, tuple[str, float]] = {}
        self._shutting_down = False
        self._shutdown_started_at: float | None = None
        self._shutdown_issues: set[str] = set()
        self._forced_shutdown_stages: set[str] = set()

    @property
    def is_shutting_down(self) -> bool:
        with self._condition:
            return self._shutting_down

    @property
    def active_operations(self) -> int:
        with self._condition:
            return len(self._operations)

    def active_operation_details(self) -> tuple[ActiveOperation, ...]:
        now = time.monotonic()
        with self._condition:
            return tuple(
                ActiveOperation(operation_id, name, started_at, max(now - started_at, 0.0))
                for operation_id, (name, started_at) in self._operations.items()
            )

    @property
    def shutdown_elapsed_seconds(self) -> float | None:
        with self._condition:
            started_at = self._shutdown_started_at
        return None if started_at is None else max(time.monotonic() - started_at, 0.0)

    def record_shutdown_issue(self, stage: str) -> None:
        with self._condition:
            self._shutdown_issues.add(stage)

    def shutdown_issues(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(sorted(self._shutdown_issues))

    def record_forced_shutdown(self, stage: str) -> None:
        with self._condition:
            self._forced_shutdown_stages.add(stage)

    def forced_shutdown_stages(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(sorted(self._forced_shutdown_stages))

    @contextmanager
    def critical_operation(self, name: str) -> Iterator[None]:
        operation_id = uuid.uuid4().hex
        started_at = time.monotonic()
        with self._condition:
            if self._shutting_down:
                raise ShutdownInProgress(SHUTDOWN_MESSAGE)
            self._operations[operation_id] = (name, started_at)
        log_event(
            LOGGER,
            "protected_operation_started",
            "Protected operation started",
            operation_id=operation_id,
            operation_name=name,
        )
        try:
            yield
        finally:
            elapsed = max(time.monotonic() - started_at, 0.0)
            with self._condition:
                self._operations.pop(operation_id, None)
                self._condition.notify_all()
            log_event(
                LOGGER,
                "protected_operation_completed",
                "Protected operation completed",
                operation_id=operation_id,
                operation_name=name,
                duration_ms=round(elapsed * 1000, 3),
            )

    def begin_shutdown(self) -> bool:
        with self._condition:
            if not self._shutting_down:
                self._shutting_down = True
                self._shutdown_started_at = time.monotonic()
            return bool(self._operations)

    def wait_for_critical_operations(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(float(timeout), 0.0)
        with self._condition:
            while self._operations:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


lifecycle = LifecycleCoordinator()


def configure_nicegui_logging() -> None:
    with _LOG_LOCK:
        configure_structured_logging(
            "portfolio_tracker",
            SHUTDOWN_LOG_PATH,
            "nicegui",
        )


def _operation_fields(operations: tuple[ActiveOperation, ...]) -> list[dict[str, object]]:
    return [
        {
            "operation_id": operation.operation_id,
            "operation_name": operation.name,
            "elapsed_ms": round(operation.elapsed_seconds * 1000, 3),
        }
        for operation in operations
    ]


def _log_final_shutdown_outcome() -> None:
    elapsed = lifecycle.shutdown_elapsed_seconds
    if elapsed is None:
        return
    issues = lifecycle.shutdown_issues()
    forced_stages = lifecycle.forced_shutdown_stages()
    operations = lifecycle.active_operation_details()
    final_outcome = (
        "unconfirmed"
        if issues or operations
        else "forced"
        if forced_stages
        else "graceful"
    )
    log_event(
        LOGGER,
        "shutdown_process_exit",
        "Portfolio Tracker process is exiting after shutdown request",
        final_shutdown_outcome=final_outcome,
        duration_ms=round(elapsed * 1000, 3),
        shutdown_issues=list(issues),
        forced_shutdown_stages=list(forced_stages),
        active_operations=_operation_fields(operations),
    )


def write_shutdown_status(outcome: str, workers: dict[str, str]) -> bool:
    """Atomically publish this launcher's worker shutdown outcome when requested."""
    raw_path = os.environ.get("NICEGUI_SHUTDOWN_STATUS_FILE", "").strip()
    if not raw_path:
        return True
    if outcome not in {"graceful", "forced", "unconfirmed", "failed"}:
        raise ValueError(f"Unsupported shutdown outcome: {outcome}")
    status_path = Path(raw_path)
    temporary_path = status_path.with_name(f"{status_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(
                {"schema_version": 1, "outcome": outcome, "workers": workers},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary_path, status_path)
        return True
    except OSError as exc:
        lifecycle.record_shutdown_issue("shutdown_status_write")
        log_event(
            LOGGER,
            "shutdown_status_write_failed",
            "Unable to publish application shutdown status to the launcher",
            error_type=type(exc).__name__,
        )
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


atexit.register(_log_final_shutdown_outcome)


def start_shutdown_request_watcher(
    shutdown_callback: Callable[[], None],
    *,
    drain_timeout_seconds: float = PROTECTED_OPERATION_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Acknowledge, boundedly drain protected work, then request server shutdown."""
    raw_path = os.environ.get("NICEGUI_SHUTDOWN_REQUEST_FILE", "").strip()
    if not raw_path:
        return
    configure_nicegui_logging()
    request_path = Path(raw_path)

    def watch() -> None:
        while not lifecycle.is_shutting_down:
            if request_path.is_file():
                requested_at = time.monotonic()
                has_active_work = lifecycle.begin_shutdown()
                log_event(
                    LOGGER,
                    "shutdown_requested",
                    "Application shutdown request detected",
                    active_operation_count=lifecycle.active_operations,
                    drain_timeout_seconds=drain_timeout_seconds,
                )
                wait_marker = os.environ.get("NICEGUI_SHUTDOWN_WAIT_FILE", "").strip()
                if wait_marker:
                    try:
                        Path(wait_marker).touch(exist_ok=True)
                    except OSError as exc:
                        lifecycle.record_shutdown_issue("request_acknowledgement")
                        log_event(
                            LOGGER,
                            "shutdown_acknowledgement_failed",
                            "Unable to write the launcher shutdown acknowledgement",
                            error_type=type(exc).__name__,
                        )
                log_event(
                    LOGGER,
                    "shutdown_request_acknowledged",
                    "Application acknowledged the shutdown request",
                    active_operation_count=lifecycle.active_operations,
                    active_operations=_operation_fields(lifecycle.active_operation_details()),
                )
                log_event(
                    LOGGER,
                    "protected_operation_drain_started",
                    "Protected-operation drain started",
                    active_operation_count=lifecycle.active_operations,
                    had_active_work=has_active_work,
                )
                drained = lifecycle.wait_for_critical_operations(drain_timeout_seconds)
                operations = lifecycle.active_operation_details()
                if drained:
                    log_event(
                        LOGGER,
                        "protected_operation_drain_completed",
                        "Protected-operation drain completed",
                        duration_ms=round((time.monotonic() - requested_at) * 1000, 3),
                    )
                else:
                    lifecycle.record_shutdown_issue("protected_operation_drain")
                    log_event(
                        LOGGER,
                        "protected_operation_drain_timed_out",
                        "Protected operations exceeded the graceful drain budget",
                        duration_ms=round((time.monotonic() - requested_at) * 1000, 3),
                        active_operation_count=len(operations),
                        active_operations=_operation_fields(operations),
                    )
                log_event(
                    LOGGER,
                    "application_shutdown_callback_requested",
                    "Requesting NiceGUI application shutdown",
                    active_operations=_operation_fields(operations),
                )
                try:
                    # NiceGUI's supported shutdown() implementation sets
                    # Server.instance.should_exit; it is safe to signal here
                    # without touching event-loop-owned objects.
                    shutdown_callback()
                except Exception as exc:
                    lifecycle.record_shutdown_issue("application_shutdown_callback")
                    log_event(
                        LOGGER,
                        "application_shutdown_callback_failed",
                        "NiceGUI application shutdown callback failed",
                        error_type=type(exc).__name__,
                    )
                    return
                log_event(
                    LOGGER,
                    "application_shutdown_callback_completed",
                    "NiceGUI application shutdown was signaled",
                    duration_ms=round((time.monotonic() - requested_at) * 1000, 3),
                )
                return
            time.sleep(0.1)

    threading.Thread(target=watch, name="nicegui-shutdown-request", daemon=True).start()


def start_launcher_watchdog() -> None:
    """Exit this server if its explicitly recorded PowerShell launcher exits."""
    raw_pid = os.environ.get("NICEGUI_LAUNCHER_PID", "").strip()
    try:
        launcher_pid = int(raw_pid)
    except ValueError:
        return
    if launcher_pid <= 0 or os.name != "nt":
        return

    def watch() -> None:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, launcher_pid)
        if not handle:
            if not lifecycle.is_shutting_down:
                os._exit(0)
            return
        try:
            while True:
                if kernel32.WaitForSingleObject(handle, 1000) == WAIT_OBJECT_0:
                    if not lifecycle.is_shutting_down:
                        os._exit(0)
                    return
                time.sleep(0.25)
        finally:
            kernel32.CloseHandle(handle)

    threading.Thread(target=watch, name="nicegui-launcher-watchdog", daemon=True).start()

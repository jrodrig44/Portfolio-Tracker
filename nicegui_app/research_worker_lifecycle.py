from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from logging_setup import log_event
from nicegui_app.process_lifecycle import lifecycle


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = PROJECT_ROOT / "supplemental_research_worker.py"
_LOCK = threading.Lock()
_STOP_LOCK = threading.Lock()
_worker: subprocess.Popen[bytes] | None = None
_worker_stop_file: Path | None = None
LOGGER = logging.getLogger("portfolio_tracker")
WORKER_GRACEFUL_STOP_TIMEOUT_SECONDS = 10.0
WORKER_TERMINATE_TIMEOUT_SECONDS = 2.0
WORKER_KILL_TIMEOUT_SECONDS = 2.0


def start_research_worker() -> None:
    """Start exactly one Supplemental Research child owned by this server.

    A child process isolates long-running AI work from NiceGUI's UI/event loop.
    This is not the in-process Yahoo Market Refresh worker; persisted leases and
    enrollment/due state provide coordination beyond this local process handle.
    """
    global _worker, _worker_stop_file
    with _LOCK:
        if _worker is not None and _worker.poll() is None:
            return
        stop_file = Path(
            os.path.join(
                tempfile.gettempdir(),
                f"portfolio-tracker-research-stop-{uuid4().hex}.request",
            )
        )
        child_environment = os.environ.copy()
        child_environment["PORTFOLIO_RESEARCH_STOP_FILE"] = str(stop_file)
        _worker = subprocess.Popen(
            [sys.executable, str(WORKER_PATH)],
            cwd=PROJECT_ROOT,
            env=child_environment,
        )
        _worker_stop_file = stop_file


def _clear_worker_if_exited(worker: subprocess.Popen[bytes], stop_file: Path | None) -> None:
    global _worker, _worker_stop_file
    if worker.poll() is None:
        return
    with _LOCK:
        if _worker is worker:
            _worker = None
            _worker_stop_file = None
    if stop_file is not None:
        stop_file.unlink(missing_ok=True)


def stop_research_worker(
    graceful_timeout_seconds: float = WORKER_GRACEFUL_STOP_TIMEOUT_SECONDS,
    terminate_timeout_seconds: float = WORKER_TERMINATE_TIMEOUT_SECONDS,
    kill_timeout_seconds: float = WORKER_KILL_TIMEOUT_SECONDS,
) -> bool:
    """Request prompt exit, then use bounded terminate/kill waits on this child."""
    with _STOP_LOCK:
        with _LOCK:
            worker = _worker
            stop_file = _worker_stop_file
        if worker is None:
            return True

        started = time.monotonic()
        log_event(
            LOGGER,
            "research_worker_stop_started",
            "Supplemental research worker stop started",
            graceful_timeout_seconds=graceful_timeout_seconds,
        )
        if worker.poll() is None and stop_file is not None:
            try:
                stop_file.touch(exist_ok=True)
            except OSError as exc:
                lifecycle.record_shutdown_issue("supplemental_research_worker_stop_request")
                log_event(
                    LOGGER,
                    "research_worker_stop_request_failed",
                    "Unable to request graceful Supplemental Research worker exit",
                    error_type=type(exc).__name__,
                )

        if worker.poll() is None:
            try:
                worker.wait(timeout=max(graceful_timeout_seconds, 0.0))
            except subprocess.TimeoutExpired:
                log_event(
                    LOGGER,
                    "research_worker_graceful_stop_timed_out",
                    "Supplemental Research worker did not exit after its stop request",
                    timeout_seconds=graceful_timeout_seconds,
                )
                if worker.poll() is None:
                    worker.terminate()
                    try:
                        worker.wait(timeout=max(terminate_timeout_seconds, 0.0))
                    except subprocess.TimeoutExpired:
                        log_event(
                            LOGGER,
                            "research_worker_terminate_timed_out",
                            "Supplemental Research worker did not exit after terminate",
                            timeout_seconds=terminate_timeout_seconds,
                        )
                        if worker.poll() is None:
                            worker.kill()
                            try:
                                worker.wait(timeout=max(kill_timeout_seconds, 0.0))
                            except subprocess.TimeoutExpired:
                                pass

        if worker.poll() is None:
            lifecycle.record_shutdown_issue("supplemental_research_worker")
            log_event(
                LOGGER,
                "research_worker_stop_timed_out",
                "Supplemental Research worker remains active after bounded stop attempts",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                graceful_timeout_seconds=graceful_timeout_seconds,
                terminate_timeout_seconds=terminate_timeout_seconds,
                kill_timeout_seconds=kill_timeout_seconds,
            )
            return False

        _clear_worker_if_exited(worker, stop_file)
        log_event(
            LOGGER,
            "research_worker_stop_completed",
            "Supplemental Research worker stopped",
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )
        return True


atexit.register(stop_research_worker)

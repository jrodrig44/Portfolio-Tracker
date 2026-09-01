from __future__ import annotations

import ctypes
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


SHUTDOWN_MESSAGE = "Portfolio Tracker is shutting down. No new changes can be started."
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


class ShutdownInProgress(RuntimeError):
    pass


class LifecycleCoordinator:
    """Reject new protected writes during shutdown and drain admitted writes.

    The launcher requests a graceful server stop first. Tracking mutations here
    prevents process-tree termination from interrupting a database transaction;
    targeted forced termination is only the launcher's bounded-time last resort.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_operations = 0
        self._shutting_down = False

    @property
    def is_shutting_down(self) -> bool:
        with self._condition:
            return self._shutting_down

    @property
    def active_operations(self) -> int:
        with self._condition:
            return self._active_operations

    @contextmanager
    def critical_operation(self, name: str) -> Iterator[None]:
        del name
        with self._condition:
            if self._shutting_down:
                raise ShutdownInProgress(SHUTDOWN_MESSAGE)
            self._active_operations += 1
        try:
            yield
        finally:
            with self._condition:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._condition.notify_all()

    def begin_shutdown(self) -> bool:
        with self._condition:
            self._shutting_down = True
            return self._active_operations > 0

    def wait_for_critical_operations(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(float(timeout), 0.0)
        with self._condition:
            while self._active_operations:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


lifecycle = LifecycleCoordinator()


def start_shutdown_request_watcher(shutdown_callback: Callable[[], None]) -> None:
    """Watch the launcher's private request file and initiate a normal app stop."""
    raw_path = os.environ.get("NICEGUI_SHUTDOWN_REQUEST_FILE", "").strip()
    if not raw_path:
        return
    request_path = Path(raw_path)

    def watch() -> None:
        while not lifecycle.is_shutting_down:
            if request_path.is_file():
                has_active_work = lifecycle.begin_shutdown()
                if has_active_work:
                    wait_marker = os.environ.get("NICEGUI_SHUTDOWN_WAIT_FILE", "").strip()
                    if wait_marker:
                        try:
                            Path(wait_marker).touch(exist_ok=True)
                        except OSError:
                            pass
                if lifecycle.wait_for_critical_operations():
                    shutdown_callback()
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

from __future__ import annotations

import asyncio
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nicegui_app.main import critical_io

from nicegui_app.process_lifecycle import (
    LifecycleCoordinator,
    ShutdownInProgress,
    start_launcher_watchdog,
    start_shutdown_request_watcher,
)


class LifecycleCoordinatorTests(unittest.TestCase):
    def test_starts_running_with_no_operations(self) -> None:
        coordinator = LifecycleCoordinator()
        self.assertFalse(coordinator.is_shutting_down)
        self.assertEqual(coordinator.active_operations, 0)

    def test_critical_count_increments_and_decrements(self) -> None:
        coordinator = LifecycleCoordinator()
        with coordinator.critical_operation("save"):
            self.assertEqual(coordinator.active_operations, 1)
            with coordinator.critical_operation("nested"):
                self.assertEqual(coordinator.active_operations, 2)
        self.assertEqual(coordinator.active_operations, 0)

    def test_exception_releases_operation(self) -> None:
        coordinator = LifecycleCoordinator()
        with self.assertRaisesRegex(ValueError, "failed"):
            with coordinator.critical_operation("save"):
                raise ValueError("failed")
        self.assertEqual(coordinator.active_operations, 0)

    def test_shutdown_prevents_new_operations(self) -> None:
        coordinator = LifecycleCoordinator()
        coordinator.begin_shutdown()
        with self.assertRaises(ShutdownInProgress):
            with coordinator.critical_operation("late write"):
                pass

    def test_shutdown_waits_for_active_operation(self) -> None:
        coordinator = LifecycleCoordinator()
        entered = threading.Event()
        release = threading.Event()

        def write() -> None:
            with coordinator.critical_operation("save"):
                entered.set()
                release.wait(1)

        worker = threading.Thread(target=write)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(coordinator.begin_shutdown())
        threading.Timer(0.03, release.set).start()
        self.assertTrue(coordinator.wait_for_critical_operations(0.5))
        worker.join(1)

    def test_shutdown_without_work_is_immediate(self) -> None:
        coordinator = LifecycleCoordinator()
        started = time.monotonic()
        self.assertFalse(coordinator.begin_shutdown())
        self.assertTrue(coordinator.wait_for_critical_operations(1))
        self.assertLess(time.monotonic() - started, 0.1)

    def test_timeout_is_bounded(self) -> None:
        coordinator = LifecycleCoordinator()
        entered = threading.Event()
        release = threading.Event()

        def write() -> None:
            with coordinator.critical_operation("save"):
                entered.set()
                release.wait(1)

        worker = threading.Thread(target=write)
        worker.start()
        self.assertTrue(entered.wait(1))
        coordinator.begin_shutdown()
        started = time.monotonic()
        self.assertFalse(coordinator.wait_for_critical_operations(0.03))
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.02)
        self.assertLess(elapsed, 0.2)
        release.set()
        worker.join(1)

    def test_request_watcher_drains_before_callback(self) -> None:
        coordinator = LifecycleCoordinator()
        callback = threading.Event()
        release = threading.Event()
        entered = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "stop.request"
            waiting = Path(directory) / "stop.waiting"

            def write() -> None:
                with coordinator.critical_operation("save"):
                    entered.set()
                    release.wait(1)

            worker = threading.Thread(target=write)
            worker.start()
            self.assertTrue(entered.wait(1))
            with patch("nicegui_app.process_lifecycle.lifecycle", coordinator), patch.dict(
                "os.environ",
                {"NICEGUI_SHUTDOWN_REQUEST_FILE": str(request), "NICEGUI_SHUTDOWN_WAIT_FILE": str(waiting)},
            ):
                start_shutdown_request_watcher(callback.set)
                request.touch()
                deadline = time.monotonic() + 1
                while not coordinator.is_shutting_down and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(coordinator.is_shutting_down)
                self.assertTrue(waiting.is_file())
                self.assertFalse(callback.is_set())
                release.set()
                self.assertTrue(callback.wait(1))
            worker.join(1)

    def test_invalid_launcher_pid_does_not_start_watchdog(self) -> None:
        with patch.dict("os.environ", {"NICEGUI_LAUNCHER_PID": "not-a-pid"}), patch(
            "nicegui_app.process_lifecycle.threading.Thread"
        ) as thread:
            start_launcher_watchdog()
        thread.assert_not_called()

    def test_rejected_critical_io_emits_one_warning_and_no_error(self) -> None:
        coordinator = LifecycleCoordinator()
        coordinator.begin_shutdown()
        with patch("nicegui_app.main.lifecycle", coordinator), patch("nicegui_app.main.ui.notify") as notify:
            with self.assertRaises(ShutdownInProgress):
                asyncio.run(critical_io("late write", lambda: None))
        notify.assert_called_once_with(
            "Portfolio Tracker is shutting down. No new changes can be started.",
            type="warning",
        )
        self.assertNotEqual(notify.call_args.kwargs.get("type"), "negative")

    def test_request_watcher_does_not_print_launcher_messages(self) -> None:
        coordinator = LifecycleCoordinator()
        callback = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "stop.request"
            request.touch()
            with patch("nicegui_app.process_lifecycle.lifecycle", coordinator), patch.dict(
                "os.environ", {"NICEGUI_SHUTDOWN_REQUEST_FILE": str(request)}, clear=False
            ), patch("builtins.print") as output:
                start_shutdown_request_watcher(callback.set)
                self.assertTrue(callback.wait(1))
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import threading
import time
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from nicegui_app.main import critical_io, stop_background_workers
from nicegui_app.research_worker_lifecycle import ResearchWorkerStopOutcome

from nicegui_app.process_lifecycle import (
    LifecycleCoordinator,
    ShutdownInProgress,
    start_launcher_watchdog,
    start_shutdown_request_watcher,
    write_shutdown_status,
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

    def test_concurrent_operations_with_same_name_have_unique_durations(self) -> None:
        coordinator = LifecycleCoordinator()
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()

        def operation(index: int) -> None:
            with coordinator.critical_operation("same_write"):
                entered[index].set()
                release.wait(1)

        workers = [threading.Thread(target=operation, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        self.assertTrue(all(event.wait(1) for event in entered))
        details = coordinator.active_operation_details()
        self.assertEqual(coordinator.active_operations, 2)
        self.assertEqual([item.name for item in details], ["same_write", "same_write"])
        self.assertEqual(len({item.operation_id for item in details}), 2)
        self.assertTrue(all(item.elapsed_seconds >= 0 for item in details))
        release.set()
        for worker in workers:
            worker.join(1)
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

    def test_idle_request_is_acknowledged_and_shutdown_callback_runs_promptly(self) -> None:
        coordinator = LifecycleCoordinator()
        callback = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "stop.request"
            waiting = Path(directory) / "stop.waiting"
            with patch("nicegui_app.process_lifecycle.lifecycle", coordinator), patch(
                "nicegui_app.process_lifecycle.configure_nicegui_logging"
            ), patch("nicegui_app.process_lifecycle.log_event") as event, patch.dict(
                "os.environ",
                {"NICEGUI_SHUTDOWN_REQUEST_FILE": str(request), "NICEGUI_SHUTDOWN_WAIT_FILE": str(waiting)},
            ):
                start = time.monotonic()
                start_shutdown_request_watcher(callback.set)
                request.touch()
                self.assertTrue(callback.wait(1))
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertTrue(waiting.is_file())
            event_names = [call.args[1] for call in event.call_args_list]
            self.assertIn("shutdown_requested", event_names)
            self.assertIn("shutdown_request_acknowledged", event_names)
            self.assertIn("protected_operation_drain_completed", event_names)
            self.assertIn("application_shutdown_callback_requested", event_names)

    def test_stalled_operation_timeout_logs_names_and_elapsed_duration(self) -> None:
        coordinator = LifecycleCoordinator()
        entered = threading.Event()
        release = threading.Event()
        callback = threading.Event()

        def slow_operation() -> None:
            with coordinator.critical_operation("statement_import"):
                entered.set()
                release.wait(1)

        operation_thread = threading.Thread(target=slow_operation)
        operation_thread.start()
        self.assertTrue(entered.wait(1))
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "stop.request"
            with patch("nicegui_app.process_lifecycle.lifecycle", coordinator), patch(
                "nicegui_app.process_lifecycle.configure_nicegui_logging"
            ), patch("nicegui_app.process_lifecycle.log_event") as event, patch.dict(
                "os.environ", {"NICEGUI_SHUTDOWN_REQUEST_FILE": str(request)}
            ):
                start_shutdown_request_watcher(callback.set, drain_timeout_seconds=0.03)
                request.touch()
                self.assertTrue(callback.wait(1))
            timeout_event = next(
                call for call in event.call_args_list
                if call.args[1] == "protected_operation_drain_timed_out"
            )
            self.assertEqual(timeout_event.kwargs["active_operation_count"], 1)
            operation = timeout_event.kwargs["active_operations"][0]
            self.assertEqual(operation["operation_name"], "statement_import")
            self.assertGreater(operation["elapsed_ms"], 0)
        release.set()
        operation_thread.join(1)
        self.assertFalse(operation_thread.is_alive())

    def test_invalid_launcher_pid_does_not_start_watchdog(self) -> None:
        with patch.dict("os.environ", {"NICEGUI_LAUNCHER_PID": "not-a-pid"}), patch(
            "nicegui_app.process_lifecycle.threading.Thread"
        ) as thread:
            start_launcher_watchdog()
        thread.assert_not_called()

    def test_shutdown_status_is_atomic_and_invocation_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "unique-status.json"
            with patch.dict(
                "os.environ",
                {"NICEGUI_SHUTDOWN_STATUS_FILE": str(status_path)},
            ):
                self.assertTrue(
                    write_shutdown_status(
                        "forced",
                        {"supplemental_research_worker": "force_stopped"},
                    )
                )
            self.assertEqual(
                json.loads(status_path.read_text(encoding="utf-8")),
                {
                    "schema_version": 1,
                    "outcome": "forced",
                    "workers": {"supplemental_research_worker": "force_stopped"},
                },
            )
            self.assertEqual(list(Path(directory).iterdir()), [status_path])

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

    def test_cancelled_critical_io_keeps_protected_operation_until_thread_finishes(self) -> None:
        async def run_test() -> None:
            coordinator = LifecycleCoordinator()
            started = threading.Event()
            release = threading.Event()

            def slow_write() -> None:
                started.set()
                release.wait(1)

            with patch("nicegui_app.main.lifecycle", coordinator):
                task = asyncio.create_task(critical_io("holdings_save", slow_write))
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                task.cancel()
                asyncio.get_running_loop().call_later(0.01, task.cancel)
                await asyncio.sleep(0.03)
                self.assertEqual(coordinator.active_operations, 1)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(coordinator.active_operations, 0)

        asyncio.run(run_test())

    def test_worker_shutdown_reports_a_timeout_after_attempting_each_worker(self) -> None:
        coordinator = LifecycleCoordinator()
        attempted = set()

        def stop_market() -> bool:
            attempted.add("market")
            return False

        def stop_research() -> ResearchWorkerStopOutcome:
            attempted.add("research")
            return ResearchWorkerStopOutcome.STOPPED

        async def run_test() -> None:
            async def fake_to_thread(operation):
                return operation()

            with patch("nicegui_app.main.lifecycle", coordinator), patch(
                "nicegui_app.main.stop_market_refresh_worker", stop_market
            ), patch("nicegui_app.main.stop_research_worker", stop_research), patch(
                "nicegui_app.main.asyncio.to_thread", fake_to_thread
            ), patch("nicegui_app.main.log_event") as event, patch(
                "nicegui_app.main.write_shutdown_status"
            ) as write_status:
                await stop_background_workers()
            self.assertEqual(attempted, {"market", "research"})
            self.assertEqual(coordinator.shutdown_issues(), ("market_refresh_worker",))
            write_status.assert_called_once_with(
                "unconfirmed",
                {"market_refresh_worker": "unconfirmed", "supplemental_research_worker": "stopped"},
            )
            self.assertIn(
                "worker_shutdown_incomplete",
                [call.args[1] for call in event.call_args_list],
            )
            self.assertEqual(event.call_args.args[1], "application_shutdown_workers_completed")

        asyncio.run(run_test())

    def test_force_stopped_research_worker_is_reported_without_exception(self) -> None:
        coordinator = LifecycleCoordinator()

        async def run_test() -> None:
            async def fake_to_thread(operation):
                return operation()

            with patch("nicegui_app.main.lifecycle", coordinator), patch(
                "nicegui_app.main.stop_market_refresh_worker", return_value=True
            ), patch(
                "nicegui_app.main.stop_research_worker",
                return_value=ResearchWorkerStopOutcome.FORCE_STOPPED,
            ), patch("nicegui_app.main.asyncio.to_thread", fake_to_thread), patch(
                "nicegui_app.main.log_event"
            ) as event, patch("nicegui_app.main.write_shutdown_status") as write_status:
                await stop_background_workers()
            self.assertEqual(coordinator.forced_shutdown_stages(), ("supplemental_research_worker",))
            write_status.assert_called_once_with(
                "forced",
                {"market_refresh_worker": "stopped", "supplemental_research_worker": "force_stopped"},
            )
            self.assertIn(
                "worker_shutdown_force_stopped",
                [call.args[1] for call in event.call_args_list],
            )

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nicegui_app import research_worker_lifecycle
from nicegui_app.research_worker_lifecycle import ResearchWorkerStopOutcome
from supplemental_research_worker import _watch_stop_request


class ResearchWorkerShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        with research_worker_lifecycle._LOCK:
            research_worker_lifecycle._worker = None
            research_worker_lifecycle._worker_stop_file = None

    def tearDown(self) -> None:
        with research_worker_lifecycle._LOCK:
            stop_file = research_worker_lifecycle._worker_stop_file
            research_worker_lifecycle._worker = None
            research_worker_lifecycle._worker_stop_file = None
        if stop_file is not None:
            stop_file.unlink(missing_ok=True)

    def test_idle_child_receives_stop_request_and_exits_without_termination(self) -> None:
        worker = Mock()
        worker.poll.side_effect = [None, None, 0, 0]
        stop_file = None
        with patch(
            "nicegui_app.research_worker_lifecycle.subprocess.Popen",
            return_value=worker,
        ) as popen, patch("nicegui_app.research_worker_lifecycle.log_event") as event:
            research_worker_lifecycle.start_research_worker()
            stop_file = research_worker_lifecycle._worker_stop_file
            self.assertIsNotNone(stop_file)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(
                popen.call_args.kwargs["env"]["PORTFOLIO_RESEARCH_STOP_FILE"],
                str(stop_file),
            )
            self.assertIs(
                research_worker_lifecycle.stop_research_worker(),
                ResearchWorkerStopOutcome.STOPPED,
            )
        self.assertTrue(stop_file is not None and not stop_file.exists())
        worker.wait.assert_called_once_with(timeout=10.0)
        worker.terminate.assert_not_called()
        worker.kill.assert_not_called()
        self.assertEqual(event.call_args_list[0].args[1], "research_worker_stop_started")
        self.assertEqual(event.call_args_list[-1].args[1], "research_worker_stop_completed")

    def test_stalled_child_uses_bounded_terminate_and_kill_waits(self) -> None:
        worker = Mock()
        worker.poll.return_value = None
        worker.wait.side_effect = [
            subprocess.TimeoutExpired("research", 0.01),
            subprocess.TimeoutExpired("research", 0.02),
            subprocess.TimeoutExpired("research", 0.03),
        ]
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "stop.request"
            with research_worker_lifecycle._LOCK:
                research_worker_lifecycle._worker = worker
                research_worker_lifecycle._worker_stop_file = stop_file
            with patch("nicegui_app.research_worker_lifecycle.log_event") as event:
                self.assertIs(
                    research_worker_lifecycle.stop_research_worker(
                        graceful_timeout_seconds=0.01,
                        terminate_timeout_seconds=0.02,
                        kill_timeout_seconds=0.03,
                    ),
                    ResearchWorkerStopOutcome.UNCONFIRMED,
                )
            self.assertTrue(stop_file.is_file())
            worker.terminate.assert_called_once_with()
            worker.kill.assert_called_once_with()
            self.assertEqual(
                [call.kwargs["timeout"] for call in worker.wait.call_args_list],
                [0.01, 0.02, 0.03],
            )
            self.assertEqual(event.call_args_list[-1].args[1], "research_worker_stop_timed_out")

    def test_terminate_fallback_is_reported_as_force_stopped(self) -> None:
        worker = Mock()
        worker.poll.side_effect = [None, None, None, 0, 0]
        worker.wait.side_effect = [
            subprocess.TimeoutExpired("research", 0.01),
            None,
        ]
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "stop.request"
            with research_worker_lifecycle._LOCK:
                research_worker_lifecycle._worker = worker
                research_worker_lifecycle._worker_stop_file = stop_file
            with patch("nicegui_app.research_worker_lifecycle.log_event") as event:
                outcome = research_worker_lifecycle.stop_research_worker(
                    graceful_timeout_seconds=0.01,
                    terminate_timeout_seconds=0.02,
                    kill_timeout_seconds=0.03,
                )
            self.assertIs(outcome, ResearchWorkerStopOutcome.FORCE_STOPPED)
            worker.terminate.assert_called_once_with()
            worker.kill.assert_not_called()
            self.assertEqual(event.call_args_list[-1].args[1], "research_worker_force_stopped")

    def test_kill_fallback_is_reported_as_force_stopped(self) -> None:
        worker = Mock()
        worker.poll.side_effect = [None, None, None, None, 0, 0]
        worker.wait.side_effect = [
            subprocess.TimeoutExpired("research", 0.01),
            subprocess.TimeoutExpired("research", 0.02),
            None,
        ]
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "stop.request"
            with research_worker_lifecycle._LOCK:
                research_worker_lifecycle._worker = worker
                research_worker_lifecycle._worker_stop_file = stop_file
            with patch("nicegui_app.research_worker_lifecycle.log_event") as event:
                outcome = research_worker_lifecycle.stop_research_worker(
                    graceful_timeout_seconds=0.01,
                    terminate_timeout_seconds=0.02,
                    kill_timeout_seconds=0.03,
                )
            self.assertIs(outcome, ResearchWorkerStopOutcome.FORCE_STOPPED)
            worker.terminate.assert_called_once_with()
            worker.kill.assert_called_once_with()
            self.assertEqual(event.call_args_list[-1].args[1], "research_worker_force_stopped")

    def test_child_stop_file_wakes_idle_event_wait(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "stop.request"
            stop_event = threading.Event()
            with patch("supplemental_research_worker.log_event") as event:
                watcher = threading.Thread(
                    target=_watch_stop_request,
                    args=(stop_event, str(stop_file)),
                )
                watcher.start()
                stop_file.touch()
                self.assertTrue(stop_event.wait(1))
                watcher.join(1)
            self.assertFalse(watcher.is_alive())
            event.assert_called_once_with(
                unittest.mock.ANY,
                "worker_stop_requested",
                "Supplemental research worker stop request received",
            )


if __name__ == "__main__":
    unittest.main()

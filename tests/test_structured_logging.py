from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from logging_setup import DateStampedFileHandler, configure_structured_logging, log_event
from supplemental_research import SupplementalResearchService, connect_database, initialize_database
from supplemental_research_worker import run_once


class StructuredLoggingTests(unittest.TestCase):
    def _close_logger(self, logger: logging.Logger) -> None:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    def _dated_path(self, logical_path: Path, day: datetime | None = None, process_id: int | None = None) -> Path:
        selected = (day or datetime.now().astimezone()).date()
        selected_process = process_id if process_id is not None else os.getpid()
        return logical_path.with_name(
            f"{logical_path.stem}.{selected.isoformat()}.{selected_process}{logical_path.suffix}"
        )

    def test_json_lines_have_common_and_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = configure_structured_logging("test.structured.json", Path(temp_dir) / "events.jsonl", "test_process")
            log_event(logger, "test_event", "Test event", ticker="AAPL", duration_ms=12, non_serializable=object())
            for handler in logger.handlers:
                handler.flush()
            line = self._dated_path(Path(temp_dir) / "events.jsonl").read_text(encoding="utf-8").strip()
            payload = json.loads(line)
            self.assertEqual(payload["process"], "test_process")
            self.assertEqual(payload["event"], "test_event")
            self.assertEqual(payload["ticker"], "AAPL")
            self.assertEqual(payload["duration_ms"], 12)
            self.assertIn("non_serializable", payload)
            self._close_logger(logger)

    def test_sensitive_fields_are_redacted_and_duplicate_configuration_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            logger = configure_structured_logging("test.structured.redaction", path, "test_process")
            same_logger = configure_structured_logging("test.structured.redaction", path, "test_process")
            self.assertIs(logger, same_logger)
            self.assertEqual(sum(getattr(handler, "_structured_console", False) for handler in logger.handlers), 1)
            self.assertEqual(sum(getattr(handler, "_structured_log_path", None) == str(path.resolve()) for handler in logger.handlers), 1)
            log_event(logger, "secret_event", "authorization: Bearer secret-value api_key=private-key", api_key="private-key", authorization="Bearer private-key")
            for handler in logger.handlers:
                handler.flush()
            content = self._dated_path(path).read_text(encoding="utf-8")
            self.assertNotIn("private-key", content)
            self.assertNotIn("secret-value", content)
            self._close_logger(logger)

    def test_date_transition_opens_new_file_without_renaming_active_file(self) -> None:
        """Midnight rollover must never rename a Windows-locked active log."""
        with tempfile.TemporaryDirectory() as temp_dir:
            logical_path = Path(temp_dir) / "events.jsonl"
            days = iter((datetime(2026, 8, 31).date(), datetime(2026, 9, 1).date()))
            current_day = [next(days)]
            logger = configure_structured_logging(
                "test.structured.rotation",
                logical_path,
                "test_process",
                date_provider=lambda: current_day[0],
                process_id=1234,
            )
            file_handlers = [handler for handler in logger.handlers if isinstance(handler, DateStampedFileHandler)]
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].retention_days, 14)
            with patch.object(Path, "rename", side_effect=PermissionError("active file is locked")):
                log_event(logger, "first_day", "First day")
                current_day[0] = next(days)
                log_event(logger, "second_day", "Second day")
            for handler in logger.handlers:
                handler.flush()
            self.assertIn("first_day", (Path(temp_dir) / "events.2026-08-31.1234.jsonl").read_text(encoding="utf-8"))
            self.assertIn("second_day", (Path(temp_dir) / "events.2026-09-01.1234.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(logical_path.exists())
            self._close_logger(logger)

    def test_multiple_instances_append_to_same_daily_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logical_path = Path(temp_dir) / "shared.jsonl"
            day = datetime(2026, 9, 1).date()
            first = configure_structured_logging(
                "test.structured.multi.first", logical_path, "first", date_provider=lambda: day
            )
            second = configure_structured_logging(
                "test.structured.multi.second", logical_path, "second", date_provider=lambda: day
            )
            log_event(first, "first_event", "First process event")
            log_event(second, "second_event", "Second process event")
            for logger in (first, second):
                for handler in logger.handlers:
                    handler.flush()
            records = [
                json.loads(line)
                for line in self._dated_path(logical_path).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual({record["process"] for record in records}, {"first", "second"})
            self._close_logger(first)
            self._close_logger(second)

    def test_overlapping_processes_write_same_daily_file_without_rollover_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logical_path = Path(temp_dir) / "shared.jsonl"
            script = (
                "import logging,sys,time;"
                "from pathlib import Path;"
                "from logging_setup import configure_structured_logging,log_event;"
                "logger=configure_structured_logging(sys.argv[2],Path(sys.argv[1]),sys.argv[2]);"
                "log_event(logger,'process_event','Overlapping process event');"
                "[handler.flush() for handler in logger.handlers];"
                "time.sleep(0.2);"
                "[handler.close() for handler in logger.handlers]"
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(logical_path), f"process_{index}"],
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for index in range(2)
            ]
            errors = []
            for process in processes:
                _, stderr = process.communicate(timeout=10)
                if process.returncode:
                    errors.append(stderr.decode("utf-8", errors="replace"))
            self.assertEqual(errors, [])
            records = []
            for path in Path(temp_dir).glob("shared.*.*.jsonl"):
                records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
            self.assertEqual({record["process"] for record in records}, {"process_0", "process_1"})

    def test_retention_removes_expired_files_and_tolerates_locked_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expired = root / "events.2026-08-18.1001.jsonl"
            locked = root / "events.2026-08-17.1002.jsonl"
            retained = root / "events.2026-08-19.1003.jsonl"
            legacy_expired = root / "events.jsonl.2026-08-16"
            legacy_retained = root / "events.jsonl.2026-08-20"
            active_legacy = root / "events.jsonl"
            for path in (expired, locked, retained, legacy_expired, legacy_retained, active_legacy):
                path.write_text("old\n", encoding="utf-8")
            real_unlink = Path.unlink

            def unlink(path: Path, *args: object, **kwargs: object) -> None:
                if path.name == locked.name:
                    raise PermissionError("locked")
                real_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", autospec=True, side_effect=unlink):
                logger = configure_structured_logging(
                    "test.structured.retention",
                    root / "events.jsonl",
                    "test_process",
                    date_provider=lambda: datetime(2026, 9, 1).date(),
                )
            self.assertFalse(expired.exists())
            self.assertTrue(locked.exists())
            self.assertTrue(retained.exists())
            self.assertFalse(legacy_expired.exists())
            self.assertTrue(legacy_retained.exists())
            self.assertTrue(active_legacy.exists())
            log_event(logger, "cleanup_survived", "Logging remains available")
            self._close_logger(logger)

    def test_worker_no_due_check_writes_structured_event_without_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "portfolio.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE holdings (ticker TEXT, asset_type TEXT, sector TEXT, annual_div_per_share REAL, dividend_yield_pct REAL)")
                connection.execute("INSERT INTO holdings VALUES ('O', 'REIT', 'Real Estate', 3.2, 5.1)")
                connection.commit()
            finally:
                connection.close()
            log_path = root / "worker.jsonl"
            test_logger = configure_structured_logging("test.worker.no_due", log_path, "supplemental_research_worker")
            with patch("supplemental_research_worker.LOGGER", test_logger):
                self.assertEqual(run_once(root), 0)
            for handler in test_logger.handlers:
                handler.flush()
            events = [json.loads(line) for line in self._dated_path(log_path).read_text(encoding="utf-8").splitlines()]
            no_due = next(event for event in events if event["event"] == "no_research_due")
            self.assertEqual(no_due["due_holdings_count"], 0)
            self._close_logger(test_logger)

    def test_worker_due_check_logs_research_start_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "portfolio.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE holdings (ticker TEXT, asset_type TEXT, sector TEXT, annual_div_per_share REAL, dividend_yield_pct REAL)")
                connection.execute("INSERT INTO holdings VALUES ('O', 'REIT', 'Real Estate', 3.2, 5.1)")
                connection.commit()
            finally:
                connection.close()
            initialize_database(db_path)
            SupplementalResearchService(db_path).sync_holdings(["O"])
            now = datetime.now(timezone.utc)
            with connect_database(db_path) as connection:
                connection.execute(
                    "UPDATE supplemental_holding_research SET research_status = 'current', manual_initial_research_completed = 1, last_success_at = ?, next_due_at = ? WHERE ticker = ?",
                    ((now - timedelta(days=31)).isoformat(), (now - timedelta(days=1)).isoformat(), "O"),
                )
            response = {
                "ticker": "O",
                "entity_name": "Realty Income Corporation",
                "retrieved_at": now.isoformat(),
                "verified_facts": [{"field": "property_focus", "value": "Net lease", "source_url": "https://www.realtyincome.com/", "source_title": "Realty Income", "source_type": "official_company", "source_date": "", "confidence": "high"}],
                "unresolved_fields": [],
                "interpretation": {"summary": "Supplemental context.", "portfolio_relevance": "Informational only.", "risks_to_monitor": []},
                "limitations": [],
            }
            log_path = root / "worker.jsonl"
            test_logger = configure_structured_logging("test.worker.due", log_path, "supplemental_research_worker")
            with patch("supplemental_research_worker.LOGGER", test_logger), patch("supplemental_research.OneMinAIClient.chat_with_ai", return_value=type("Result", (), {"content": json.dumps(response), "record_id": "record-1"})()):
                self.assertEqual(run_once(root), 0)
            for handler in test_logger.handlers:
                handler.flush()
            events = [json.loads(line) for line in self._dated_path(log_path).read_text(encoding="utf-8").splitlines()]
            event_names = [event["event"] for event in events]
            self.assertIn("holding_research_started", event_names)
            self.assertIn("holding_research_completed", event_names)
            self.assertIn("research_batch_completed", event_names)
            self._close_logger(test_logger)


if __name__ == "__main__":
    unittest.main()

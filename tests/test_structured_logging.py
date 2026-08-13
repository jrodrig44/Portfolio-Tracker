from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from logging.handlers import TimedRotatingFileHandler

from logging_setup import configure_structured_logging, log_event
from supplemental_research import SupplementalResearchService, connect_database, initialize_database
from supplemental_research_worker import run_once


class StructuredLoggingTests(unittest.TestCase):
    def _close_logger(self, logger: logging.Logger) -> None:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    def test_json_lines_have_common_and_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = configure_structured_logging("test.structured.json", Path(temp_dir) / "events.jsonl", "test_process")
            log_event(logger, "test_event", "Test event", ticker="AAPL", duration_ms=12, non_serializable=object())
            for handler in logger.handlers:
                handler.flush()
            line = (Path(temp_dir) / "events.jsonl").read_text(encoding="utf-8").strip()
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
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("private-key", content)
            self.assertNotIn("secret-value", content)
            self._close_logger(logger)

    def test_rotation_policy_is_daily_and_retains_fourteen_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = configure_structured_logging("test.structured.rotation", Path(temp_dir) / "events.jsonl", "test_process")
            file_handlers = [handler for handler in logger.handlers if isinstance(handler, TimedRotatingFileHandler)]
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].when, "MIDNIGHT")
            self.assertEqual(file_handlers[0].backupCount, 14)
            self.assertTrue(file_handlers[0].delay)
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
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
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
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            event_names = [event["event"] for event in events]
            self.assertIn("holding_research_started", event_names)
            self.assertIn("holding_research_completed", event_names)
            self.assertIn("research_batch_completed", event_names)
            self._close_logger(test_logger)


if __name__ == "__main__":
    unittest.main()

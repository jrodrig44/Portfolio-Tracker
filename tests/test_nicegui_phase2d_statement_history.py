from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portfolio_statement_history import StatementHistoryService, statement_display_records
from stash_history import import_validated_history, prepare_history_database, scan_state


def valid_record(source_hash: str = "valid-hash", end_date: str = "2026-01-31", value: float = 1000.0) -> dict[str, object]:
    return {
        "source_hash": source_hash,
        "statement_start_date": "2026-01-01",
        "statement_end_date": end_date,
        "portfolio_value": value,
        "invested_value": value - 100,
        "cash_value": 100.0,
        "status": "valid",
        "warning": "Ready to import.",
        "diagnostics": {
            "verified_header_found": True,
            "nearby_date_range_found": True,
            "nearby_account_marker_found": True,
            "verified_account_summary_found": True,
            "balance_rows_found": True,
            "accounting_equation_passed": True,
        },
    }


class Phase2DStatementHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.statements = root / "Statements"
        self.statements.mkdir()
        self.history_db = root / "history.sqlite"
        self.holdings_db = root / "portfolio.sqlite"
        with sqlite3.connect(self.holdings_db) as connection:
            connection.execute("CREATE TABLE holdings (ticker TEXT, shares REAL)")
            connection.execute("INSERT INTO holdings VALUES ('UNCHANGED', 1.25)")
            connection.execute("CREATE TABLE supplemental_research (ticker TEXT, status TEXT)")
            connection.execute("INSERT INTO supplemental_research VALUES ('UNCHANGED', 'stored')")
        prepare_history_database(self.history_db)
        self.service = StatementHistoryService(self.statements, self.history_db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_empty_folder_and_pdf_discovery_with_duplicate_file_detection(self) -> None:
        initial = self.service.folder_status()
        self.assertTrue(initial["exists"])
        self.assertEqual(initial["pdf_count"], 0)
        self.assertEqual(scan_state(self.history_db)["scan_status"], "not_started")
        (self.statements / "one.pdf").write_bytes(b"same-pdf")
        (self.statements / "copy.pdf").write_bytes(b"same-pdf")
        with patch("stash_history._parse_statement_document", return_value=valid_record()):
            state = self.service.scan(restart=True)
        self.assertEqual(state["detected_pdf_count"], 2)
        self.assertEqual(state["unique_pdf_count"], 1)
        self.assertEqual(state["duplicate_file_count"], 1)
        self.assertEqual(state["processed_count"], 2)
        self.assertEqual(state["ready"], 1)
        self.assertEqual(state["scan_status"], "completed")
        self.assertGreater(state["monthly_continuity"]["missing_month_count"], 0)

    def test_missing_folder_has_readable_error_and_no_automatic_scan(self) -> None:
        missing = StatementHistoryService(self.statements / "missing", self.history_db)
        self.assertEqual(missing.folder_status(), {"exists": False, "pdf_count": 0, "status": "Missing"})
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            missing.scan()

    def test_display_records_hide_hashes_and_raw_diagnostics(self) -> None:
        display = statement_display_records([valid_record("sensitive-source-hash")])[0]
        self.assertNotIn("source_hash", display)
        self.assertNotIn("diagnostics", display)
        self.assertEqual(display["statement_end_date"], "2026-01-31")
        self.assertEqual(display["portfolio_value"], 1000)

    def test_manual_review_validation_and_ready_transition_reuse_production_service(self) -> None:
        manual_id = self.service.save_review("2021-08", "2021-08-31", 900, 100, 1000, True)
        state = self.service.state()
        manual = next(row for row in state["records"] if row.get("source_hash") == manual_id)
        self.assertEqual(manual["status"], "valid")
        self.assertTrue(manual["confirmed"])
        with self.assertRaises(ValueError):
            self.service.save_review("2021-09", "2021-09-30", 900, 50, 1000, True)
        self.service.remove_review(manual_id)
        self.assertNotIn(manual_id, {row.get("source_hash") for row in self.service.state()["records"]})

    def test_import_guard_duplicate_submission_and_production_result(self) -> None:
        fake_state = {"can_import": True, "records": [valid_record()]}
        expected = {"imported": 1, "duplicates": 0, "needs_review": 0, "unparseable": 0}
        with patch.object(self.service, "state", return_value=fake_state), patch("portfolio_statement_history.import_validated_history", return_value=expected) as importer:
            self.assertEqual(self.service.import_ready(submission_token="same-click"), expected)
            self.assertIsNone(self.service.import_ready(submission_token="same-click"))
        importer.assert_called_once_with(fake_state["records"], self.history_db)

    def test_history_reload_confirmed_delete_and_write_isolation(self) -> None:
        before_schema = sqlite3.connect(self.history_db).execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        result = import_validated_history([valid_record()], self.history_db)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.service.performance_history(), [{"history_date": "2026-01-31", "portfolio_value": 1000.0, "source_type": "stash_statement"}])
        with self.assertRaises(PermissionError):
            self.service.delete_imported(confirmed=False)
        self.service.delete_imported(confirmed=True)
        self.assertEqual(self.service.performance_history(), [])
        after_schema = sqlite3.connect(self.history_db).execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        self.assertEqual(before_schema, after_schema)
        with sqlite3.connect(self.holdings_db) as connection:
            self.assertEqual(connection.execute("SELECT * FROM holdings").fetchall(), [("UNCHANGED", 1.25)])
            self.assertEqual(connection.execute("SELECT * FROM supplemental_research").fetchall(), [("UNCHANGED", "stored")])

    def test_ui_contract_is_explicit_async_responsive_and_preserves_history_range(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"Statement History"', source)
        self.assertIn('ui.button("Scan Statements"', source)
        self.assertIn('ui.button("Import Ready Statements"', source)
        self.assertIn('ui.button("Save Manual Review"', source)
        self.assertIn('critical_io("statement_scan", statement_service.scan', source)
        self.assertIn('critical_io("statement_import", statement_service.import_ready', source)
        self.assertIn("w-full sm:w-auto min-h-11", source)
        self.assertIn("render_history(history_range)", source)
        self.assertIn("holdings were not reloaded", source)
        self.assertIn("prior AI history context is stale", source)


if __name__ == "__main__":
    unittest.main()

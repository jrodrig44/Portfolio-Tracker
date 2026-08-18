import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import sqlite3
import pandas as pd

import app
from stash_history import _closing_value_from_row, classify_scan, clear_scan_preview, delete_imported_history, history_summary, import_validated_history, load_performance_history, load_statement_history, parse_statement_text, prepare_history_database, run_statement_scan, scan_can_import, scan_state, upsert_live_portfolio_snapshot


class StashStatementHistoryTests(unittest.TestCase):
    def test_placeholder_aapl_jepi_behavior_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "statement-2026-07.pdf").write_bytes(b"stub")
            db_path = root / "history.sqlite"
            record = {"source_hash": "safe-hash", "statement_start_date": "2026-07-01", "statement_end_date": "2026-07-31", "portfolio_value": 123.45, "status": "valid"}
            result = import_validated_history([record], db_path)
            self.assertEqual(result["imported"], 1)
            self.assertEqual(load_statement_history(db_path), [{"history_date": "2026-07-31", "portfolio_value": 123.45, "source_type": "stash_statement"}])

    def test_statements_directory_is_project_relative_and_created(self) -> None:
        self.assertEqual(app.STATEMENTS_DIR, app.PROJECT_ROOT / "Statements")
        self.assertTrue(app.STATEMENTS_DIR.is_dir())
        self.assertEqual(app.DATA_DIR, app.PROJECT_ROOT / "data")

    def test_missing_or_empty_statements_folder_does_not_break_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_statements_dir = Path(temp_dir) / "Statements"
            with patch.object(app, "STATEMENTS_DIR", missing_statements_dir):
                self.assertEqual(app._local_statement_status(), (0, (app.DATA_DIR / "stash_statement_history.sqlite").exists()))

            missing_statements_dir.mkdir()
            with patch.object(app, "STATEMENTS_DIR", missing_statements_dir):
                self.assertEqual(app._local_statement_status(), (0, (app.DATA_DIR / "stash_statement_history.sqlite").exists()))

    def test_parser_extracts_statement_period_and_value_from_multiple_layouts(self) -> None:
        first = parse_statement_text("Statement Period: August 1, 2021 - August 31, 2021\nEnding Account Value: $1,234.56\nCash Balance: $34.56")
        second = parse_statement_text("Period September 1, 2021 through September 30, 2021\nTotal Portfolio Value $2,000.00\nInvested Securities Value: $1,950.00\nCash Value: $50.00")
        self.assertEqual(first["statement_start_date"], "2021-08-01")
        self.assertEqual(first["statement_end_date"], "2021-08-31")
        self.assertEqual(first["portfolio_value"], 1234.56)
        self.assertEqual(second["statement_end_date"], "2021-09-30")
        self.assertEqual(second["portfolio_value"], 2000.0)

        header_layout = parse_statement_text("AUGUST 1 2021 - AUGUST 31 2021\nEnding Account Value: $2,900.00")
        self.assertEqual(header_layout["statement_start_date"], "2021-08-01")
        self.assertEqual(header_layout["statement_end_date"], "2021-08-31")
        self.assertEqual(header_layout["portfolio_value"], 2900.0)

    def test_scan_does_not_import_and_duplicate_dates_require_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "synthetic.pdf"
            pdf_path.write_bytes(b"synthetic")
            records = [{"source_hash": "synthetic", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0, "status": "valid", "warning": "Ready to import."}]
            self.assertFalse((root / "history.sqlite").exists())
            db_path = root / "history.sqlite"
            import_validated_history(records, db_path)
            conflicting = dict(records[0])
            conflicting["source_hash"] = "different-hash"
            conflicting["portfolio_value"] = 200.0
            classified = classify_scan([conflicting], db_path)
            self.assertEqual(classified[0]["status"], "needs_review")
            self.assertEqual(history_summary(db_path)["history_records"], 1)

    def test_invalid_values_and_unparseable_pdfs_do_not_create_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = [{"source_hash": "synthetic", "status": "needs_review", "warning": "Ending portfolio value is missing or invalid."}]
            db_path = root / "history.sqlite"
            result = import_validated_history(records, db_path)
            self.assertEqual(result["needs_review"], 1)
            self.assertEqual(history_summary(db_path)["history_records"], 0)

    def test_sequential_scan_processes_all_synthetic_documents_and_checkpoints_each(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(61):
                (root / f"synthetic-{index}.pdf").write_bytes(f"synthetic-{index}".encode())
            db_path = root / "history.sqlite"
            completed_counts: list[int] = []
            def parse_document(_path: Path) -> dict[str, object]:
                return {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": None, "portfolio_value": 100.0}
            with patch("stash_history._parse_statement_document", side_effect=parse_document):
                state = run_statement_scan(root, db_path=db_path, on_document_complete=lambda *_args: completed_counts.append(scan_state(db_path)["processed_count"]))
            self.assertEqual(state["processed_count"], 61)
            self.assertEqual(state["scan_status"], "completed")
            self.assertEqual(completed_counts, list(range(1, 62)))

    def test_interrupted_scan_resumes_without_duplicate_preview_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(3):
                (root / f"synthetic-{index}.pdf").write_bytes(f"synthetic-{index}".encode())
            db_path = root / "history.sqlite"
            calls = 0
            def interrupted_parse(_path: Path) -> dict[str, object]:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt()
                return {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": None, "portfolio_value": 100.0}
            with patch("stash_history._parse_statement_document", side_effect=interrupted_parse):
                with self.assertRaises(KeyboardInterrupt):
                    run_statement_scan(root, db_path=db_path)
            self.assertEqual(scan_state(db_path)["processed_count"], 1)
            self.assertEqual(scan_state(db_path)["scan_status"], "interrupted")
            with patch("stash_history._parse_statement_document", return_value={"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": None, "portfolio_value": 100.0}):
                resumed = run_statement_scan(root, db_path=db_path)
            self.assertEqual(resumed["processed_count"], 3)
            self.assertEqual(len(resumed["records"]), 3)

    def test_clear_preview_does_not_delete_imported_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            record = {"source_hash": "imported", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0, "status": "valid"}
            import_validated_history([record], db_path)
            clear_scan_preview(db_path)
            self.assertEqual(history_summary(db_path)["imported"], 1)
            self.assertEqual(scan_state(db_path)["processed_count"], 0)

    def test_partial_scan_cannot_enable_import(self) -> None:
        self.assertFalse(scan_can_import({"scan_status": "interrupted", "processed_count": 5, "ready": 5}, 61))
        self.assertFalse(scan_can_import({"scan_status": "completed", "processed_count": 60, "ready": 60}, 61))
        self.assertTrue(scan_can_import({"scan_status": "completed", "processed_count": 61, "ready": 1}, 61))

    def test_failed_import_rolls_back_and_does_not_modify_portfolio_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_db = root / "history.sqlite"
            portfolio_db = root / "portfolio.db"
            with closing(sqlite3.connect(portfolio_db)) as connection:
                connection.execute("CREATE TABLE holdings (ticker TEXT)")
                connection.execute("INSERT INTO holdings VALUES ('UNCHANGED')")
                connection.commit()
            valid = {"source_hash": "first", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0, "status": "valid"}
            invalid = {"source_hash": "second", "statement_start_date": "2021-09-01", "statement_end_date": "2021-09-30", "status": "valid"}
            with self.assertRaises(KeyError):
                import_validated_history([valid, invalid], history_db)
            self.assertEqual(history_summary(history_db)["history_records"], 0)
            with closing(sqlite3.connect(portfolio_db)) as connection:
                self.assertEqual(connection.execute("SELECT ticker FROM holdings").fetchone()[0], "UNCHANGED")

    def test_verified_placeholder_database_is_replaced_without_touching_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_db = root / "history.sqlite"
            with closing(sqlite3.connect(history_db)) as connection:
                connection.execute("CREATE TABLE statement_history_imports (id INTEGER)")
                connection.execute("CREATE TABLE statement_history_holdings (ticker TEXT, shares REAL, market_value REAL)")
                connection.execute("INSERT INTO statement_history_holdings VALUES ('AAPL', 10.0, 1000.0)")
                connection.execute("INSERT INTO statement_history_holdings VALUES ('JEPI', 5.0, 250.0)")
                connection.commit()
            prepare_history_database(history_db)
            with closing(sqlite3.connect(history_db)) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("statement_imports", tables)
            self.assertIn("statement_portfolio_history", tables)

    def test_live_snapshots_persist_across_restarts_and_upsert_same_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            upsert_live_portfolio_snapshot(100.0, "2026-08-17", db_path)
            upsert_live_portfolio_snapshot(125.0, "2026-08-17", db_path)
            self.assertEqual(load_performance_history(db_path), [{"history_date": "2026-08-17", "portfolio_value": 125.0, "source_type": "live_portfolio_snapshot"}])
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM live_portfolio_snapshots").fetchone()[0], 1)

    def test_live_snapshots_reject_incomplete_prices_and_export_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            incomplete = pd.DataFrame([{"shares": 1.0, "current_price": 0.0}])
            complete = pd.DataFrame([{"shares": 1.0, "current_price": 100.0}])
            self.assertFalse(app._persist_live_snapshot_if_ready(incomplete, 100.0, db_path=db_path, use_export_json_data=False, strict_export_accuracy_mode=False))
            self.assertFalse(app._persist_live_snapshot_if_ready(complete, 100.0, db_path=db_path, use_export_json_data=True, strict_export_accuracy_mode=False))
            self.assertFalse(app._persist_live_snapshot_if_ready(complete, 100.0, db_path=db_path, use_export_json_data=False, strict_export_accuracy_mode=True))
            self.assertEqual(load_performance_history(db_path), [])
            self.assertTrue(app._persist_live_snapshot_if_ready(complete, 100.0, db_path=db_path, use_export_json_data=False, strict_export_accuracy_mode=False))

    def test_history_insert_conflict_does_not_leave_orphaned_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            existing = {"source_hash": "existing", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0, "status": "valid"}
            conflicting = {"source_hash": "conflict", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 200.0, "status": "valid"}
            import_validated_history([existing], db_path)
            result = import_validated_history([conflicting], db_path)
            self.assertEqual(result["duplicates"], 1)
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM statement_imports").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM statement_portfolio_history").fetchone()[0], 1)

    def test_delete_imported_history_persists_after_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            record = {"source_hash": "delete-me", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0, "status": "valid"}
            import_validated_history([record], db_path)
            delete_imported_history(db_path)
            self.assertEqual(history_summary(db_path)["imported"], 0)
            self.assertEqual(load_statement_history(db_path), [])

    def test_history_summary_prepares_legacy_placeholder_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE statement_history_imports (id INTEGER)")
                connection.execute("CREATE TABLE statement_history_holdings (ticker TEXT, shares REAL, market_value REAL)")
                connection.execute("INSERT INTO statement_history_holdings VALUES ('AAPL', 10.0, 1000.0)")
                connection.commit()
            self.assertEqual(history_summary(db_path)["history_records"], 0)

    def test_sanitized_stash_balance_rows_use_closing_column(self) -> None:
        cash_row = [
            {"text": "NET", "top": 10}, {"text": "ACCOUNT", "top": 10}, {"text": "BALANCE", "top": 10},
            {"text": "$1.07", "top": 10}, {"text": "$0.26", "top": 10},
        ]
        equity_row = [
            {"text": "Total", "top": 20}, {"text": "Equity", "top": 20}, {"text": "Holdings", "top": 20},
            {"text": "$5,910.78", "top": 20}, {"text": "$5,775.49", "top": 20},
        ]
        self.assertEqual(_closing_value_from_row(cash_row, ("NET", "ACCOUNT", "BALANCE")), 0.26)
        self.assertEqual(_closing_value_from_row(equity_row, ("TOTAL", "EQUITY", "HOLDINGS")), 5775.49)

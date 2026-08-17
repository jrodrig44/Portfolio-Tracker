import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import sqlite3

import app
from stash_history import classify_scan, history_summary, import_validated_history, load_statement_history, parse_statement_text, prepare_history_database, scan_local_statements


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

        header_layout = parse_statement_text("AUGUST 1 2021 - AUGUST 31 2021\nNET ACCOUNT BALANCE $3,000.00 $2,900.00")
        self.assertEqual(header_layout["statement_start_date"], "2021-08-01")
        self.assertEqual(header_layout["statement_end_date"], "2021-08-31")
        self.assertEqual(header_layout["portfolio_value"], 3000.0)

    def test_scan_does_not_import_and_duplicate_dates_require_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "synthetic.pdf"
            pdf_path.write_bytes(b"synthetic")
            with patch("stash_history.extract_pdf_text", return_value="Statement Period: August 1, 2021 - August 31, 2021\nEnding Account Value: $100.00"):
                records = scan_local_statements(root)
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
            pdf_path = root / "synthetic.pdf"
            pdf_path.write_bytes(b"synthetic")
            with patch("stash_history.extract_pdf_text", return_value="not a statement"):
                records = scan_local_statements(root)
            db_path = root / "history.sqlite"
            result = import_validated_history(records, db_path)
            self.assertEqual(result["needs_review"], 1)
            self.assertEqual(history_summary(db_path)["history_records"], 0)

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

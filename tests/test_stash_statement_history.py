import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import sqlite3
import pandas as pd
import calendar

import app
from stash_history import _closing_value_from_row, _validate_record, classify_scan, clear_scan_preview, delete_imported_history, extract_statement_layout_values_from_pages, history_summary, import_validated_history, load_manual_statement_reviews, load_performance_history, load_statement_history, parse_statement_header_chars, parse_statement_header_words, parse_statement_text, prepare_history_database, remove_manual_statement_review, run_statement_scan, save_manual_statement_review, scan_can_import, scan_state, upsert_live_portfolio_snapshot


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
        first = parse_statement_text("Statement Period: August 1, 2021 - August 31, 2021\nACCOUNT HEADER\nEnding Account Value: $1,234.56\nCash Balance: $34.56")
        second = parse_statement_text("ACCOUNT STATEMENT\nSeptember 1, 2021 through September 30, 2021\nTotal Portfolio Value $2,000.00\nInvested Securities Value: $1,950.00\nCash Value: $50.00")
        self.assertEqual(first["statement_start_date"], "2021-08-01")
        self.assertEqual(first["statement_end_date"], "2021-08-31")
        self.assertEqual(first["portfolio_value"], 1234.56)
        self.assertEqual(second["statement_end_date"], "2021-09-30")
        self.assertEqual(second["portfolio_value"], 2000.0)

        header_layout = parse_statement_text("ACCOUNT STATEMENT\nAUGUST 1 2021 - AUGUST 31 2021\nEnding Account Value: $2,900.00")
        self.assertEqual(header_layout["statement_start_date"], "2021-08-01")
        self.assertEqual(header_layout["statement_end_date"], "2021-08-31")
        self.assertEqual(header_layout["portfolio_value"], 2900.0)

    def test_verified_monthly_header_wins_over_later_disclosure_dates(self) -> None:
        text = "\n".join(
            [
                "ACCOUNT STATEMENT",
                "August 1, 2021 - August 31, 2021",
                "Account Summary",
                "Ending Account Value: $1,234.56",
                "",
                "Statement of Financial Condition June 30, 2021",
                "Audited report December 31, 2021",
            ]
        )
        record = parse_statement_text(text)
        self.assertEqual(record["statement_start_date"], "2021-08-01")
        self.assertEqual(record["statement_end_date"], "2021-08-31")
        self.assertTrue(record["diagnostics"]["verified_header_found"])
        self.assertTrue(record["diagnostics"]["statement_date_accepted"])

    def test_missing_verified_header_needs_review_without_disclosure_fallback(self) -> None:
        record = parse_statement_text("Statement of Financial Condition June 30, 2021 - December 31, 2021")
        self.assertIsNone(record["statement_end_date"])
        self.assertEqual(_validate_record(record)[1], "Verified statement-period header was not found.")

    def test_coordinate_header_parser_accepts_reordered_first_page_words_only(self) -> None:
        words = [
            {"text": "August", "top": 18, "x0": 40, "x1": 80}, {"text": "1,", "top": 18, "x0": 82, "x1": 95}, {"text": "2021", "top": 18, "x0": 97, "x1": 125},
            {"text": "-", "top": 18, "x0": 130, "x1": 135}, {"text": "August", "top": 18, "x0": 140, "x1": 180}, {"text": "31,", "top": 18, "x0": 182, "x1": 200}, {"text": "2021", "top": 18, "x0": 202, "x1": 230},
            {"text": "ACCOUNT", "top": 64, "x0": 40, "x1": 95}, {"text": "HEADER", "top": 64, "x0": 100, "x1": 145},
            {"text": "June", "top": 500, "x0": 40, "x1": 65}, {"text": "30,", "top": 500, "x0": 67, "x1": 85}, {"text": "2021", "top": 500, "x0": 87, "x1": 115},
        ]
        record = parse_statement_header_words(list(reversed(words)), 800)
        self.assertEqual(record["statement_end_date"], "2021-08-31")
        self.assertTrue(record["diagnostics"]["verified_header_found"])
        self.assertTrue(record["diagnostics"]["nearby_date_range_found"])

    def test_character_coordinate_header_parser_handles_word_extraction_gap(self) -> None:
        text = "August 1, 2021 - August 31, 2021"
        chars = []
        for index, character in enumerate(text):
            chars.append({"text": character, "top": 22, "x0": index * 6, "x1": index * 6 + 5, "size": 10})
        for index, character in enumerate("ACCOUNT HEADER"):
            chars.append({"text": character, "top": 70, "x0": index * 6, "x1": index * 6 + 5, "size": 10})
        record = parse_statement_header_chars(chars, 800)
        self.assertEqual(record["statement_end_date"], "2021-08-31")
        self.assertTrue(record["diagnostics"]["verified_header_found"])

    def test_summary_requires_nearby_balance_rows_not_entire_page(self) -> None:
        class Page:
            height = 800
            def extract_words(self) -> list[dict[str, object]]:
                return [
                    {"text": "TOTAL", "top": 400, "x0": 10, "x1": 40}, {"text": "EQUITY", "top": 400, "x0": 45, "x1": 85}, {"text": "HOLDINGS", "top": 400, "x0": 90, "x1": 145}, {"text": "$100.00", "top": 400, "x0": 200, "x1": 250}, {"text": "$120.00", "top": 400, "x0": 260, "x1": 310},
                    {"text": "NET", "top": 460, "x0": 10, "x1": 30}, {"text": "ACCOUNT", "top": 460, "x0": 35, "x1": 90}, {"text": "BALANCE", "top": 460, "x0": 95, "x1": 150}, {"text": "$1.00", "top": 460, "x0": 200, "x1": 240}, {"text": "$2.00", "top": 460, "x0": 250, "x1": 290},
                ]
        values = extract_statement_layout_values_from_pages([Page()])
        self.assertTrue(values["summary_found"])
        self.assertEqual(values["portfolio_value"], 122.0)

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

    def test_duplicate_files_are_accounted_for_without_duplicate_preview_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(61):
                content = b"duplicate" if index in {0, 60} else f"unique-{index}".encode()
                (root / f"synthetic-{index}.pdf").write_bytes(content)
            db_path = root / "history.sqlite"
            parsed_count = 0
            def parse_unique_document(_path: Path) -> dict[str, object]:
                nonlocal parsed_count
                parsed_count += 1
                year = 2021 + (7 + parsed_count - 1) // 12
                month = (7 + parsed_count - 1) % 12 + 1
                return {"status": "valid", "warning": "Ready to import.", "statement_start_date": f"{year:04d}-{month:02d}-01", "statement_end_date": f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}", "portfolio_value": 100.0}
            with patch("stash_history._parse_statement_document", side_effect=parse_unique_document):
                state = run_statement_scan(root, db_path=db_path)
            self.assertEqual(state["detected_pdf_count"], 61)
            self.assertEqual(state["unique_pdf_count"], 60)
            self.assertEqual(state["processed_unique_count"], 60)
            self.assertEqual(state["duplicate_file_count"], 1)
            self.assertEqual(state["processed_count"], 61)
            self.assertEqual(len(state["records"]), 60)
            self.assertEqual(state["scan_status"], "completed")
            self.assertTrue(scan_can_import(state, 61))

    def test_preview_warning_persists_after_reopening_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "synthetic.pdf").write_bytes(b"warning-source")
            db_path = root / "history.sqlite"
            with patch("stash_history._parse_statement_document", return_value={"status": "needs_review", "warning": "Statement ending date was not found."}):
                run_statement_scan(root, db_path=db_path)
            reopened = scan_state(db_path)
            self.assertEqual(reopened["records"][0]["warning"], "Statement ending date was not found.")

    def test_safe_parser_diagnostics_persist_after_reopening_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "synthetic.pdf").write_bytes(b"diagnostic-source")
            db_path = root / "history.sqlite"
            parsed = {"status": "needs_review", "warning": "Verified statement-period header was not found.", "diagnostics": {"verified_header_found": False, "nearby_date_range_found": False, "verified_account_summary_found": False, "balance_rows_found": False}}
            with patch("stash_history._parse_statement_document", return_value=parsed):
                run_statement_scan(root, db_path=db_path)
            reopened = scan_state(db_path)
            self.assertEqual(reopened["diagnostics"], {"header_found": 0, "nearby_date_range_found": 0, "account_summary_found": 0, "balance_rows_found": 0})

    def test_manual_review_validates_persists_edits_and_removes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            with self.assertRaises(ValueError):
                save_manual_statement_review("2021-08", "2021-08-30", 10.0, 1.0, True, db_path=db_path)
            with self.assertRaises(ValueError):
                save_manual_statement_review("2021-08", "2021-08-31", -1.0, 1.0, True, db_path=db_path)
            with self.assertRaises(ValueError):
                save_manual_statement_review("2021-08", "2021-08-31", 10.0, 1.0, False, db_path=db_path)
            manual_id = save_manual_statement_review("2021-08", "2021-08-31", 10.0, 1.5, True, db_path=db_path)
            record = load_manual_statement_reviews(db_path)[0]
            self.assertEqual(record["portfolio_value"], 11.5)
            self.assertEqual(record["warning"], "Manually verified against the local statement.")
            self.assertEqual(record["source_type"], "manual_statement_review")
            with self.assertRaises(ValueError):
                save_manual_statement_review("2021-08", "2021-08-31", 10.0, 1.5, True, db_path=db_path)
            save_manual_statement_review("2021-08", "2021-08-31", 20.0, 2.0, True, manual_id=manual_id, db_path=db_path)
            self.assertEqual(load_manual_statement_reviews(db_path)[0]["portfolio_value"], 22.0)
            remove_manual_statement_review(manual_id, db_path)
            self.assertEqual(load_manual_statement_reviews(db_path), [])

    def test_manual_reviews_complete_continuity_and_preserve_source_type_on_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            year, month = 2021, 8
            for _ in range(60):
                ending = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
                save_manual_statement_review(f"{year:04d}-{month:02d}", ending, 10.0, 1.0, True, db_path=db_path)
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            state = scan_state(db_path)
            self.assertTrue(state["monthly_continuity"]["complete"])
            records = load_manual_statement_reviews(db_path)
            result = import_validated_history(records, db_path)
            self.assertEqual(result["imported"], 60)
            with closing(sqlite3.connect(db_path)) as connection:
                source_types = {row[0] for row in connection.execute("SELECT DISTINCT source_type FROM statement_portfolio_history")}
            self.assertEqual(source_types, {"manual_statement_review"})

    def test_unconfirmed_manual_record_is_not_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "history.sqlite"
            record = {"source_hash": "manual-unconfirmed", "statement_end_date": "2021-08-31", "portfolio_value": 10.0, "invested_value": 9.0, "cash_value": 1.0, "status": "valid", "source_type": "manual_statement_review", "confirmed": False}
            result = import_validated_history([record], db_path)
            self.assertEqual(result["imported"], 0)
            self.assertEqual(result["needs_review"], 1)

    def test_matching_statement_dates_keep_one_canonical_preview_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "first.pdf").write_bytes(b"first-content")
            (root / "second.pdf").write_bytes(b"second-content")
            db_path = root / "history.sqlite"
            parsed = {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0}
            with patch("stash_history._parse_statement_document", return_value=parsed):
                state = run_statement_scan(root, db_path=db_path)
            self.assertEqual(sorted(record["status"] for record in state["records"]), ["duplicate_statement", "valid"])
            self.assertFalse(scan_can_import(state, 2))

    def test_conflicting_statement_dates_persist_as_review_without_arbitrary_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "first.pdf").write_bytes(b"first-content")
            (root / "second.pdf").write_bytes(b"second-content")
            db_path = root / "history.sqlite"
            parsed = [
                {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0},
                {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 200.0},
            ]
            with patch("stash_history._parse_statement_document", side_effect=parsed):
                state = run_statement_scan(root, db_path=db_path)
            self.assertEqual({record["status"] for record in state["records"]}, {"needs_review"})
            self.assertTrue(all(record["warning"] == "Multiple statements contain different values for this ending date." for record in state["records"]))
            reopened = scan_state(db_path)
            self.assertEqual({record["status"] for record in reopened["records"]}, {"needs_review"})
            result = import_validated_history(reopened["records"], db_path)
            self.assertEqual(result["imported"], 0)
            self.assertEqual(history_summary(db_path)["history_records"], 0)

    def test_imported_statement_date_matching_and_conflicting_values_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "history.sqlite"
            imported = {"source_hash": "imported-source", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0, "status": "valid"}
            import_validated_history([imported], db_path)
            for name, content in (("match.pdf", b"matching"), ("conflict.pdf", b"conflicting")):
                (root / name).write_bytes(content)
            parsed = [
                {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 100.0},
                {"status": "valid", "warning": "Ready to import.", "statement_start_date": "2021-08-01", "statement_end_date": "2021-08-31", "portfolio_value": 200.0},
            ]
            with patch("stash_history._parse_statement_document", side_effect=parsed):
                state = run_statement_scan(root, db_path=db_path)
            statuses = sorted(record["status"] for record in state["records"])
            self.assertEqual(statuses, ["duplicate", "needs_review"])

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
        self.assertFalse(scan_can_import({"scan_status": "completed", "processed_count": 61, "ready": 1, "monthly_continuity": {"complete": False}}, 61))
        self.assertTrue(scan_can_import({"scan_status": "completed", "processed_count": 61, "ready": 1, "monthly_continuity": {"complete": True}}, 61))

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

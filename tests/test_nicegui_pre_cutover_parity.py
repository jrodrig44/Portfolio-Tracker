from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from nicegui_app.services.portfolio_data import load_holdings
from portfolio_holdings_write import HoldingNotFoundError, HoldingsWriteService
from portfolio_statement_history import statement_scan_diagnostic_records, statement_verified_display_records
from stash_history import (
    import_validated_history,
    prepare_history_database,
    remove_manual_statement_review,
    save_manual_statement_review,
    scan_state,
)


COLUMNS = """
    ticker TEXT, shares REAL DEFAULT 0, amount_invested REAL DEFAULT 0,
    annual_div_per_share REAL DEFAULT 0, regular_biweekly_contribution REAL DEFAULT 0,
    espp_biweekly_contribution REAL DEFAULT 0, espp_contribution_biweekly REAL DEFAULT 0,
    special_contribution REAL DEFAULT 0, employer_stock INTEGER DEFAULT 0,
    espp_enabled INTEGER DEFAULT 0, is_espp INTEGER DEFAULT 0,
    espp_discount REAL DEFAULT 0, espp_discount_pct REAL DEFAULT 0,
    espp_purchase_frequency_months REAL DEFAULT 6, espp_purchase_frequency TEXT DEFAULT '',
    espp_acquired_shares REAL DEFAULT 0, sector TEXT, asset_type TEXT,
    preserved_note TEXT DEFAULT 'keep'
"""


def holding(ticker: str, shares: float = 1.123456789) -> dict[str, object]:
    return {
        "ticker": ticker, "shares": shares, "espp_acquired_shares": 0,
        "amount_invested": 50, "annual_div_per_share": 2.5,
        "regular_biweekly_contribution": 25, "espp_contribution_biweekly": 0,
        "special_contribution": 10, "is_espp": False, "espp_discount": 0,
        "espp_purchase_frequency_months": 6, "sector": "Technology", "asset_type": "ETF",
    }


def add_scan_preview(db: Path, source_hash: str, month: str, status: str = "needs_review") -> None:
    prepare_history_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO statement_scan_preview "
            "(source_hash, scan_status, statement_end_date, validation_status, warning, parser_version, updated_at) "
            "VALUES (?, ?, ?, ?, 'Parser could not verify this statement.', 'test', 'now')",
            (source_hash, status, f"{month}-28", status),
        )


def manual_review(db: Path, month: str) -> str:
    year, month_number = (int(part) for part in month.split("-"))
    import calendar
    ending = f"{month}-{calendar.monthrange(year, month_number)[1]:02d}"
    return save_manual_statement_review(month, ending, 10.0, 1.0, 11.0, True, db_path=db)


class HoldingsGridSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "portfolio.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(f"CREATE TABLE holdings ({COLUMNS})")
        self.service = HoldingsWriteService(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_batch_add_edit_delete_targets_rowids_and_preserves_columns(self) -> None:
        first = self.service.add(holding("DUP"))
        second = self.service.add(holding("DUP", 2.987654321))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE holdings SET preserved_note='private' WHERE rowid=?", (second["_holding_id"],))
        expected = load_holdings(self.db)
        edited_second = {**expected[1], "shares": 3.123456789}
        result = self.service.save_grid(
            [edited_second, holding("NEW", 0.123456789)], expected_rows=expected,
        )
        rows = load_holdings(self.db)
        self.assertEqual(result, {"added": 1, "updated": 1, "unchanged": 0, "deleted": 1})
        self.assertEqual({row["_holding_id"] for row in rows if row["ticker"] == "DUP"}, {second["_holding_id"]})
        self.assertAlmostEqual(next(row for row in rows if row["ticker"] == "DUP")["shares"], 3.123456789)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT preserved_note FROM holdings WHERE rowid=?", (second["_holding_id"],)).fetchone()[0], "private")

    def test_unchanged_rows_are_not_updated_and_failed_batch_rolls_back(self) -> None:
        self.service.add(holding("KEEP"))
        expected = load_holdings(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TRIGGER reject_updates BEFORE UPDATE ON holdings BEGIN SELECT RAISE(ABORT, 'no update'); END")
        result = self.service.save_grid(expected, expected_rows=expected)
        self.assertEqual(result["unchanged"], 1)
        changed = [{**expected[0], "shares": 9.5}, holding("NEW")]
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.save_grid(changed, expected_rows=expected)
        rows = load_holdings(self.db)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["shares"], 1.123456789)

    def test_unchanged_legacy_defaults_and_new_bogus_ticker_save_and_reload(self) -> None:
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO holdings (ticker, shares, sector, asset_type) VALUES ('KEEP', 1, NULL, NULL)"
            )
        expected = load_holdings(self.db)
        new_row = holding("ZZZZ")
        new_row["_holding_id"] = None
        result = self.service.save_grid([*expected, new_row], expected_rows=[*expected, new_row])
        self.assertEqual(result, {"added": 1, "updated": 0, "unchanged": 1, "deleted": 0})
        reloaded = load_holdings(self.db)
        bogus = next(row for row in reloaded if row["ticker"] == "ZZZZ")
        self.assertEqual({row["ticker"] for row in reloaded}, {"KEEP", "ZZZZ"})
        self.service.delete(bogus["_holding_id"], confirmed=True, expected_ticker="ZZZZ")
        self.assertEqual([row["ticker"] for row in load_holdings(self.db)], ["KEEP"])

    def test_alias_and_default_differences_do_not_conflict(self) -> None:
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO holdings (ticker, shares, espp_biweekly_contribution, espp_contribution_biweekly, "
                "espp_enabled, is_espp, espp_discount, espp_discount_pct, espp_purchase_frequency_months, sector, asset_type) "
                "VALUES ('ALIAS', 1, 25, 0, 1, 0, .10, 15, NULL, NULL, NULL)"
            )
        expected = load_holdings(self.db)
        submitted = [{
            **expected[0],
            "espp_contribution_biweekly": expected[0]["espp_biweekly_contribution"],
            "is_espp": expected[0]["espp_enabled"],
        }]
        result = self.service.save_grid(submitted, expected_rows=expected)
        self.assertEqual(result, {"added": 0, "updated": 0, "unchanged": 1, "deleted": 0})

    def test_dynamic_field_change_does_not_trigger_concurrency_failure(self) -> None:
        self.service.add(holding("DYNAMIC"))
        expected = load_holdings(self.db)
        with sqlite3.connect(self.db) as connection:
            for definition in (
                "current_price REAL", "market_value REAL", "unrealized_gain REAL",
                "annualized_income REAL", "dividend_yield_pct REAL",
            ):
                connection.execute(f"ALTER TABLE holdings ADD COLUMN {definition}")
            connection.execute(
                "UPDATE holdings SET current_price=999, market_value=999, unrealized_gain=999, "
                "annualized_income=999, dividend_yield_pct=99 WHERE rowid=?",
                (expected[0]["_holding_id"],),
            )
        result = self.service.save_grid(expected, expected_rows=expected)
        self.assertEqual(result["unchanged"], 1)

    def test_real_external_edit_is_detected_and_new_insert_rolls_back(self) -> None:
        existing = self.service.add(holding("KEEP"))
        expected = load_holdings(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE holdings SET shares=9 WHERE rowid=?", (existing["_holding_id"],))
        with self.assertRaisesRegex(HoldingNotFoundError, "A holding changed after the grid loaded"):
            self.service.save_grid([*expected, holding("ZZZZ")], expected_rows=expected)
        reloaded = load_holdings(self.db)
        self.assertEqual([row["ticker"] for row in reloaded], ["KEEP"])
        self.assertEqual(reloaded[0]["shares"], 9)

    def test_stale_grid_and_hd_values_are_safely_normalized(self) -> None:
        row = self.service.add(holding("HD"))
        expected = load_holdings(self.db)
        changed = {**expected[0], "regular_biweekly_contribution": 99, "special_contribution": 88, "espp_biweekly_contribution": 0, "espp_contribution_biweekly": 0, "is_espp": False, "espp_discount": 0, "espp_purchase_frequency_months": 3}
        self.service.save_grid([changed], expected_rows=expected)
        saved = self.service.get(row["_holding_id"])
        self.assertEqual((saved["regular_biweekly_contribution"], saved["special_contribution"], saved["espp_contribution_biweekly"]), (0, 0, 100))
        self.assertEqual((saved["is_espp"], saved["espp_discount"], saved["espp_purchase_frequency_months"]), (1, 0.15, 6))
        stale_expected = load_holdings(self.db)
        self.service.edit(row["_holding_id"], {**holding("HD"), "shares": 7})
        with self.assertRaises(HoldingNotFoundError):
            self.service.save_grid(stale_expected, expected_rows=stale_expected)

    def test_nicegui_uses_one_editable_grid_and_separate_live_fields(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        active = source.rsplit("    def render_holdings() -> None:", 1)[1].split("    async def render_statement_history", 1)[0]
        self.assertIn('ui.label("Manage Holdings")', active)
        self.assertIn("Add, edit, or remove rows, then click Save when finished.", active)
        self.assertIn("ui.aggrid", active)
        self.assertIn('ui.button("Add Row"', active)
        self.assertIn('ui.button("Remove Selected Rows"', active)
        self.assertIn('ui.button("Save Holdings"', active)
        self.assertIn('ui.label("Live Calculated Fields")', active)
        self.assertIn("holding_grid_column_defs()", active)
        self.assertIn('{"field": "_holding_id", "hide": True}', source)
        self.assertNotIn('ui.button("Edit"', active)
        self.assertNotIn("Edit or delete a holding", active)


class StatementReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "history.sqlite"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unresolved_parser_flag_is_actionable(self) -> None:
        add_scan_preview(self.db, "pdf-a", "2022-01")
        state = scan_state(self.db)
        self.assertEqual((state["needs_review"], state["manually_resolved"]), (1, 0))
        self.assertEqual(state["records"][0]["raw_status"], "needs_review")

    def test_undated_parser_diagnostic_is_separate_from_manual_verified_values(self) -> None:
        add_scan_preview(self.db, "pdf-a", "2025-01")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE statement_scan_preview SET statement_end_date=NULL WHERE source_hash='pdf-a'")
        manual_id = save_manual_statement_review("2025-01", "2025-01-31", 1000.0, 50.0, 1050.0, True, db_path=self.db)
        state = scan_state(self.db)
        verified = statement_verified_display_records(state["verified_records"])
        diagnostic = statement_scan_diagnostic_records(state["scan_diagnostics"])
        self.assertEqual(verified, [{
            "statement_start_date": "—", "statement_end_date": "2025-01-31",
            "total_priced_portfolio": 1000.0, "net_account_balance": 50.0,
            "total_equity_holdings": 1050.0, "source_resolution": "Manually Verified",
            "current_status": "Verified",
        }])
        self.assertEqual(diagnostic[0]["parser_end_date"], "—")
        self.assertEqual(diagnostic[0]["raw_parser_status"], "needs_review")
        self.assertNotIn("source_hash", verified[0])
        save_manual_statement_review("2025-01", "2025-01-31", 1200.0, 75.0, 1275.0, True, manual_id=manual_id, db_path=self.db)
        refreshed = statement_verified_display_records(scan_state(self.db)["verified_records"])
        self.assertEqual((refreshed[0]["total_priced_portfolio"], refreshed[0]["net_account_balance"], refreshed[0]["total_equity_holdings"]), (1200.0, 75.0, 1275.0))
        remove_manual_statement_review(manual_id, self.db)
        removed = scan_state(self.db)
        self.assertEqual(removed["verified_records"], [])
        self.assertIn("2025-01", removed["monthly_continuity"]["missing_months"])

    def test_manual_save_edit_and_remove_reconcile_immediately(self) -> None:
        add_scan_preview(self.db, "pdf-a", "2022-01")
        manual_id = manual_review(self.db, "2022-01")
        state = scan_state(self.db)
        self.assertEqual((state["needs_review"], state["manually_resolved"], state["parser_review_flags"]), (0, 1, 1))
        self.assertEqual(state["records"][0]["resolved_by"], "manual_review")
        remove_manual_statement_review(manual_id, self.db)
        self.assertEqual(scan_state(self.db)["needs_review"], 1)

    def test_nine_manual_reviews_resolve_nine_old_parser_flags(self) -> None:
        for number in range(1, 10):
            month = f"2022-{number:02d}"
            add_scan_preview(self.db, f"pdf-{number}", month)
            manual_review(self.db, month)
        state = scan_state(self.db)
        self.assertEqual((state["needs_review"], state["manually_resolved"], state["parser_review_flags"]), (0, 9, 9))

    def test_imported_month_resolves_old_preview_without_losing_raw_status(self) -> None:
        add_scan_preview(self.db, "old-pdf", "2022-01")
        import_validated_history([{
            "source_hash": "verified-import", "statement_start_date": "2022-01-01",
            "statement_end_date": "2022-01-31", "portfolio_value": 11.0,
            "invested_value": 10.0, "cash_value": 1.0, "status": "valid",
        }], self.db)
        state = scan_state(self.db)
        preview = state["records"][0]
        self.assertEqual(state["needs_review"], 0)
        self.assertEqual(preview["raw_status"], "needs_review")
        self.assertEqual((preview["effective_status"], preview["resolved_by"]), ("resolved", "imported_history"))
        self.assertNotIn("2022-01", state["monthly_continuity"]["missing_months"])

    def test_complete_verified_coverage_makes_undated_parser_flags_diagnostic_only(self) -> None:
        months = []
        year, month_number = 2021, 8
        while (year, month_number) <= (2026, 7):
            months.append(f"{year:04d}-{month_number:02d}")
            year, month_number = (year + 1, 1) if month_number == 12 else (year, month_number + 1)
        for number, month in enumerate(months[:9]):
            add_scan_preview(self.db, f"undated-{number}", month)
            with sqlite3.connect(self.db) as connection:
                connection.execute("UPDATE statement_scan_preview SET statement_end_date=NULL WHERE source_hash=?", (f"undated-{number}",))
            manual_review(self.db, month)
        manual_records = [row for row in scan_state(self.db)["records"] if row.get("source_type") == "manual_statement_review"]
        import_validated_history(manual_records, self.db)
        valid_records = []
        import calendar
        for number, month in enumerate(months[9:], start=9):
            year, month_number = (int(part) for part in month.split("-"))
            valid_records.append({
                "source_hash": f"valid-{number}", "statement_start_date": f"{month}-01",
                "statement_end_date": f"{month}-{calendar.monthrange(year, month_number)[1]:02d}",
                "portfolio_value": 11.0, "invested_value": 10.0, "cash_value": 1.0,
                "status": "valid",
            })
        import_validated_history(valid_records, self.db)
        state = scan_state(self.db)
        self.assertEqual((state["needs_review"], state["manually_resolved"], state["parser_review_flags"]), (0, 9, 9))
        self.assertTrue(state["monthly_continuity"]["complete"])
        manual_rows = statement_verified_display_records(state["verified_records"])
        self.assertEqual(len([row for row in manual_rows if row["source_resolution"] == "Manually Verified"]), 9)
        self.assertTrue(all(row["statement_end_date"] != "—" and row["total_priced_portfolio"] is not None and row["net_account_balance"] is not None and row["total_equity_holdings"] is not None for row in manual_rows))
        self.assertTrue(all(row["raw_status"] == "needs_review" for row in state["records"][:9]))
        self.assertTrue(all(row["resolved_by"] == "verified_history_coverage" for row in state["records"][:9]))

    def test_imported_manual_review_remains_visible_with_verified_values(self) -> None:
        add_scan_preview(self.db, "pdf-a", "2025-01")
        manual_review(self.db, "2025-01")
        manual_records = [row for row in scan_state(self.db)["records"] if row.get("source_type") == "manual_statement_review"]
        import_validated_history(manual_records, self.db)
        displayed = statement_verified_display_records(scan_state(self.db)["verified_records"])
        self.assertEqual(len(displayed), 1)
        self.assertEqual(displayed[0]["source_resolution"], "Manually Verified")
        self.assertEqual(displayed[0]["current_status"], "Previously Imported")
        self.assertEqual((displayed[0]["total_priced_portfolio"], displayed[0]["net_account_balance"], displayed[0]["total_equity_holdings"]), (10.0, 1.0, 11.0))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from nicegui_app.services.portfolio_data import load_holdings
from portfolio_holdings_write import (
    HoldingConfirmationError,
    HoldingValidationError,
    HoldingsWriteService,
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


def valid(ticker: str = "schd") -> dict[str, object]:
    return {
        "ticker": ticker, "shares": 1.123456789, "espp_acquired_shares": 0,
        "amount_invested": 50, "annual_div_per_share": 2.5,
        "regular_biweekly_contribution": 25, "espp_contribution_biweekly": 0,
        "special_contribution": 10, "is_espp": False, "espp_discount": 0,
        "espp_purchase_frequency_months": 6, "sector": "Technology", "asset_type": "ETF",
    }


class Phase2BHoldingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "portfolio.db"
        self.history = Path(self.temp.name) / "history.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(f"CREATE TABLE holdings ({COLUMNS})")
        with sqlite3.connect(self.history) as connection:
            connection.execute("CREATE TABLE marker (value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('unchanged')")
        self.service = HoldingsWriteService(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_add_valid_fractional_holding_and_production_duplicate_behavior(self) -> None:
        first = self.service.add(valid(), submission_token="first")
        second = self.service.add(valid(" SCHD "), submission_token="second")
        self.assertEqual(first["ticker"], "SCHD")
        self.assertEqual(second["ticker"], "SCHD")
        rows = load_holdings(self.db)
        self.assertEqual(len(rows), 2)  # Streamlit's dynamic editor intentionally permits duplicate rows.
        self.assertAlmostEqual(rows[0]["shares"], 1.123456789)

    def test_invalid_ticker_and_numeric_inputs_are_rejected_without_writes(self) -> None:
        for field, value in (("ticker", "  "), ("shares", "bad"), ("shares", -1), ("shares", float("inf"))):
            payload = valid()
            payload[field] = value
            with self.assertRaises(HoldingValidationError):
                self.service.add(payload)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 0)

    def test_hd_to_hd_edit_preserves_defaults_and_unrelated_columns(self) -> None:
        row = self.service.add(valid("HD"))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE holdings SET preserved_note='secret' WHERE rowid=?", (row["_holding_id"],))
        changed = valid("hd")
        changed.update({"shares": 2.987654321, "regular_biweekly_contribution": 280, "special_contribution": 500, "espp_contribution_biweekly": 0})
        result = self.service.edit(row["_holding_id"], changed)
        self.assertAlmostEqual(result["shares"], 2.987654321)
        self.assertEqual(result["preserved_note"], "secret")
        self.assertEqual(result["regular_biweekly_contribution"], 0)
        self.assertEqual(result["special_contribution"], 0)
        self.assertEqual(result["espp_contribution_biweekly"], 100)
        self.assertEqual(result["employer_stock"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_discount"], .15)
        self.assertEqual(result["espp_purchase_frequency_months"], 6)
        self.assertEqual(result["espp_purchase_frequency"], "approximately_every_6_months")
        self.assertAlmostEqual(result["espp_acquired_shares"], 2.987654321)

    def test_hd_to_non_hd_clears_only_hd_derived_metadata(self) -> None:
        row = self.service.add(valid("HD"))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE holdings SET preserved_note='still-here' WHERE rowid=?", (row["_holding_id"],))
        changed = valid("SCHD")
        changed.update({"is_espp": True, "espp_discount": 0.12, "espp_acquired_shares": 0.5, "espp_purchase_frequency_months": 3})
        result = self.service.edit(row["_holding_id"], changed)
        self.assertEqual(result["employer_stock"], 0)
        self.assertEqual(result["espp_purchase_frequency"], "")
        self.assertEqual(result["preserved_note"], "still-here")
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_discount"], 0.12)
        self.assertEqual(result["espp_discount_pct"], 12)
        self.assertEqual(result["espp_acquired_shares"], 0.5)
        self.assertEqual(result["espp_purchase_frequency_months"], 3)

    def test_non_hd_to_hd_establishes_production_defaults(self) -> None:
        row = self.service.add(valid("SCHD"))
        changed = valid("HD")
        changed.update({"shares": 4, "regular_biweekly_contribution": 280, "special_contribution": 500, "espp_contribution_biweekly": 0})
        result = self.service.edit(row["_holding_id"], changed)
        self.assertEqual(result["employer_stock"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["regular_biweekly_contribution"], 0)
        self.assertEqual(result["special_contribution"], 0)
        self.assertEqual(result["espp_contribution_biweekly"], 100)
        self.assertEqual(result["espp_discount"], 0.15)
        self.assertEqual(result["espp_discount_pct"], 15)
        self.assertEqual(result["espp_purchase_frequency_months"], 6)
        self.assertEqual(result["espp_purchase_frequency"], "approximately_every_6_months")
        self.assertEqual(result["espp_acquired_shares"], 4)

    def test_non_hd_generic_espp_fields_survive_edit(self) -> None:
        payload = valid("ACME")
        payload.update({"is_espp": True, "espp_contribution_biweekly": 75, "espp_discount": 0.10, "espp_acquired_shares": 0.75, "espp_purchase_frequency_months": 3})
        row = self.service.add(payload)
        result = self.service.edit(row["_holding_id"], {**payload, "shares": 2})
        self.assertEqual(result["employer_stock"], 0)
        self.assertEqual(result["espp_purchase_frequency"], "")
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_contribution_biweekly"], 75)
        self.assertEqual(result["espp_discount"], 0.10)
        self.assertEqual(result["espp_acquired_shares"], 0.75)
        self.assertEqual(result["espp_purchase_frequency_months"], 3)

    def test_delete_requires_confirmation_and_removes_only_stable_row(self) -> None:
        first = self.service.add(valid("SCHD"))
        second = self.service.add(valid("SCHD"))
        with self.assertRaises(HoldingConfirmationError):
            self.service.delete(first["_holding_id"], confirmed=False)
        self.service.delete(first["_holding_id"], confirmed=True, expected_ticker="SCHD")
        remaining = load_holdings(self.db)
        self.assertEqual([row["_holding_id"] for row in remaining], [second["_holding_id"]])

    def test_failed_write_rolls_back_and_duplicate_submission_is_idempotent(self) -> None:
        self.service.add(valid(), submission_token="same-click")
        self.assertIsNone(self.service.add(valid(), submission_token="same-click"))
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TRIGGER fail_edit BEFORE UPDATE ON holdings BEGIN SELECT RAISE(ABORT, 'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.edit(1, {**valid(), "shares": 99})
        self.assertAlmostEqual(load_holdings(self.db)[0]["shares"], 1.123456789)

    def test_no_schema_migration_or_history_write(self) -> None:
        with sqlite3.connect(self.db) as connection:
            before = connection.execute("PRAGMA table_info(holdings)").fetchall()
        self.service.add(valid())
        with sqlite3.connect(self.db) as connection:
            after = connection.execute("PRAGMA table_info(holdings)").fetchall()
        with sqlite3.connect(self.history) as connection:
            marker = connection.execute("SELECT value FROM marker").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(marker, "unchanged")

    def test_ui_contract_has_mobile_actions_confirmation_and_invalidation(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('ui.button("Add Holding"', source)
        self.assertIn('ui.button("Edit"', source)
        self.assertIn('ui.button("Delete"', source)
        self.assertIn("from the portfolio?", source)
        self.assertIn("w-full sm:w-auto min-h-11", source)
        self.assertIn("refresh_after_holding_write", source)
        self.assertIn('"AI Analysis"', source)
        self.assertIn("prior prepared output is stale", source)


if __name__ == "__main__":
    unittest.main()

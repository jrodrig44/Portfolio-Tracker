from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portfolio_holdings_write import HoldingValidationError, HoldingsWriteService, summarize_contributions


SCHEMA = """
    ticker TEXT, shares REAL, amount_invested REAL, annual_div_per_share REAL,
    regular_biweekly_contribution REAL DEFAULT 0, special_contribution REAL DEFAULT 0,
    espp_biweekly_contribution REAL DEFAULT 0, espp_contribution_biweekly REAL DEFAULT 0,
    is_espp INTEGER DEFAULT 0, espp_enabled INTEGER DEFAULT 0, employer_stock INTEGER DEFAULT 0,
    espp_discount REAL DEFAULT 0, espp_discount_pct REAL DEFAULT 0,
    espp_purchase_frequency_months REAL DEFAULT 6, espp_purchase_frequency TEXT DEFAULT '',
    espp_acquired_shares REAL DEFAULT 0, sector TEXT, asset_type TEXT,
    preserved_note TEXT DEFAULT 'untouched'
"""


class Phase2CContributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "portfolio.db"
        self.history = Path(self.temp.name) / "history.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(f"CREATE TABLE holdings ({SCHEMA})")
            connection.execute("INSERT INTO holdings (ticker,shares,amount_invested,sector,asset_type) VALUES ('SCHD',1.123456789,80,'Technology','ETF')")
            connection.execute("INSERT INTO holdings (ticker,shares,amount_invested,sector,asset_type) VALUES ('SCHD',9,900,'Other','ETF')")
            connection.execute("INSERT INTO holdings (ticker,shares,amount_invested,employer_stock,is_espp,espp_enabled,espp_discount,espp_discount_pct,espp_purchase_frequency_months,espp_purchase_frequency,espp_acquired_shares,sector,asset_type) VALUES ('HD',4,300,1,1,1,.15,15,6,'approximately_every_6_months',4,'Consumer Cyclical','Stock')")
        with sqlite3.connect(self.history) as connection:
            connection.execute("CREATE TABLE marker (value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('unchanged')")
        self.service = HoldingsWriteService(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def row(self, holding_id: int) -> dict[str, object]:
        return self.service.get(holding_id)

    def test_regular_and_special_update_clear_precision_and_targeting(self) -> None:
        self.service.update_contributions(1, {"regular_biweekly_contribution": 280.125, "special_contribution": 500.55})
        result = self.row(1)
        self.assertEqual(result["regular_biweekly_contribution"], 280.125)
        self.assertEqual(result["special_contribution"], 500.55)
        self.assertEqual(self.row(2)["regular_biweekly_contribution"], 0)
        self.service.update_contributions(1, {"regular_biweekly_contribution": 0, "special_contribution": 0})
        self.assertEqual(self.row(1)["regular_biweekly_contribution"], 0)
        self.assertEqual(self.row(1)["special_contribution"], 0)
        self.assertEqual(result["shares"], 1.123456789)
        self.assertEqual(result["amount_invested"], 80)
        self.assertEqual(result["preserved_note"], "untouched")

    def test_summary_keeps_special_separate(self) -> None:
        summary = summarize_contributions([
            {"regular_biweekly_contribution": 280, "espp_biweekly_contribution": 100, "special_contribution": 500}
        ])
        self.assertEqual(summary["combined_biweekly"], 380)
        self.assertEqual(summary["combined_annual"], 9880)
        self.assertEqual(summary["regular_annual"], 7280)
        self.assertEqual(summary["espp_annual"], 2600)
        self.assertEqual(summary["special_contribution"], 500)
        self.assertNotEqual(summary["combined_annual"], (280 + 100 + 500) * 26)

    def test_hd_rules_amount_fallback_discount_and_frequency(self) -> None:
        result = self.service.update_contributions(3, {
            "regular_biweekly_contribution": 280, "special_contribution": 500,
            "espp_contribution_biweekly": 125.25, "espp_discount": 10,
            "espp_purchase_frequency_months": 3, "is_espp": False,
        })
        self.assertEqual(result["regular_biweekly_contribution"], 0)
        self.assertEqual(result["special_contribution"], 0)
        self.assertEqual(result["espp_contribution_biweekly"], 125.25)
        self.assertEqual(result["espp_biweekly_contribution"], 125.25)
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["employer_stock"], 1)
        self.assertEqual(result["espp_discount"], .15)
        self.assertEqual(result["espp_discount_pct"], 15)
        self.assertEqual(result["espp_purchase_frequency_months"], 6)
        self.assertEqual(result["espp_purchase_frequency"], "approximately_every_6_months")
        fallback = self.service.update_contributions(3, {"espp_contribution_biweekly": 0})
        self.assertEqual(fallback["espp_contribution_biweekly"], 100)
        self.assertEqual(fallback["espp_acquired_shares"], 4)

    def test_discount_normalization_and_validation(self) -> None:
        decimal = self.service.update_contributions(1, {"is_espp": True, "espp_discount": .15})
        self.assertEqual((decimal["espp_discount"], decimal["espp_discount_pct"]), (.15, 15))
        percent = self.service.update_contributions(1, {"espp_discount": 15})
        self.assertEqual((percent["espp_discount"], percent["espp_discount_pct"]), (.15, 15))
        zero = self.service.update_contributions(1, {"espp_discount": 0})
        self.assertEqual((zero["espp_discount"], zero["espp_discount_pct"]), (0, 0))
        for invalid in (-1, 101, float("inf"), "bad"):
            with self.assertRaises(HoldingValidationError):
                self.service.update_contributions(1, {"espp_discount": invalid})

    def test_generic_non_hd_espp_and_frequency_companion(self) -> None:
        result = self.service.update_contributions(1, {
            "is_espp": True, "espp_contribution_biweekly": 75.75,
            "espp_discount": 12, "espp_purchase_frequency_months": 3,
        })
        self.assertEqual(result["employer_stock"], 0)
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_contribution_biweekly"], 75.75)
        self.assertEqual(result["espp_discount"], .12)
        self.assertEqual(result["espp_discount_pct"], 12)
        self.assertEqual(result["espp_purchase_frequency_months"], 3)
        self.assertEqual(result["espp_purchase_frequency"], "")
        inferred = self.service.update_contributions(2, {"is_espp": False, "espp_contribution_biweekly": 25, "espp_discount": 10})
        self.assertEqual(inferred["is_espp"], 1)
        self.assertEqual(inferred["espp_enabled"], 1)
        inactive = self.service.update_contributions(2, {"is_espp": False, "espp_contribution_biweekly": 0, "espp_discount": 10})
        self.assertEqual(inactive["is_espp"], 0)
        self.assertEqual(inactive["espp_enabled"], 0)
        self.assertEqual(inactive["espp_discount"], 0)
        self.assertEqual(inactive["espp_discount_pct"], 0)

    def test_failure_rolls_back_and_duplicate_submit_is_idempotent(self) -> None:
        self.service.update_contributions(1, {"regular_biweekly_contribution": 10}, submission_token="one-click")
        self.assertIsNone(self.service.update_contributions(1, {"regular_biweekly_contribution": 20}, submission_token="one-click"))
        self.assertEqual(self.row(1)["regular_biweekly_contribution"], 10)
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TRIGGER contribution_failure BEFORE UPDATE ON holdings BEGIN SELECT RAISE(ABORT, 'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.update_contributions(1, {"regular_biweekly_contribution": 99, "special_contribution": 88})
        result = self.row(1)
        self.assertEqual(result["regular_biweekly_contribution"], 10)
        self.assertEqual(result["special_contribution"], 0)

    def test_no_schema_migration_or_history_write(self) -> None:
        with sqlite3.connect(self.db) as connection:
            before = connection.execute("PRAGMA table_info(holdings)").fetchall()
        self.service.update_contributions(1, {"regular_biweekly_contribution": 1})
        with sqlite3.connect(self.db) as connection:
            after = connection.execute("PRAGMA table_info(holdings)").fetchall()
        with sqlite3.connect(self.history) as connection:
            marker = connection.execute("SELECT value FROM marker").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(marker, "unchanged")

    def test_shared_full_edit_and_contribution_update_store_equivalent_contribution_state(self) -> None:
        other = Path(self.temp.name) / "other.db"
        with sqlite3.connect(other) as connection:
            connection.execute(f"CREATE TABLE holdings ({SCHEMA})")
            connection.execute("INSERT INTO holdings (ticker,shares,amount_invested,sector,asset_type) VALUES ('SCHD',1.123456789,80,'Technology','ETF')")
        contribution_values = {"regular_biweekly_contribution": 25.25, "special_contribution": 40.5, "espp_contribution_biweekly": 60.75, "is_espp": True, "espp_discount": .10, "espp_purchase_frequency_months": 3}
        self.service.update_contributions(1, contribution_values)
        full_values = {"ticker": "SCHD", "shares": 1.123456789, "espp_acquired_shares": 0, "amount_invested": 80, "annual_div_per_share": 0, "sector": "Technology", "asset_type": "ETF", **contribution_values}
        other_row = HoldingsWriteService(other).edit(1, full_values)
        current = self.row(1)
        fields = ("regular_biweekly_contribution", "special_contribution", "espp_contribution_biweekly", "espp_biweekly_contribution", "is_espp", "espp_enabled", "espp_discount", "espp_discount_pct", "espp_purchase_frequency_months", "espp_purchase_frequency")
        self.assertEqual({key: current[key] for key in fields}, {key: other_row[key] for key in fields})

    def test_ui_contract_has_dedicated_mobile_surface_and_invalidation(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('ui.expansion("Contribution Settings"', source)
        self.assertIn('ui.button("Edit Contributions"', source)
        self.assertIn('ui.button("Save Contributions"', source)
        self.assertIn("update_contributions", source)
        self.assertIn("w-full sm:w-auto min-h-11", source)
        self.assertIn("refresh_after_holding_write", source)
        self.assertIn("prior prepared output is stale", source)


if __name__ == "__main__":
    unittest.main()

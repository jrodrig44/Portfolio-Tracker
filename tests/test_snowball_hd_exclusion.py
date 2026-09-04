from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

import pandas as pd

from beneficial_exposure import beneficial_metrics_frame
from portfolio_analysis import build_analysis, snowball_tracker


def hd_context(equate: str = "10.318744", merrill: str = "12") -> dict:
    return {"available": True, "reconciliation": {"current_custody": {
        "equate_shares": Decimal(equate),
        "merrill_shares": Decimal(merrill),
        "combined_beneficial_shares": Decimal(equate) + Decimal(merrill),
        "reconciliation_complete": True,
        "as_of_date": "2026-06-30",
    }}}


class SnowballHDExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = pd.DataFrame([
            {"ticker": "HD", "shares": 12.0, "current_price": 300.0, "market_value": 3600.0, "cost_basis": 3000.0, "unrealized_gain": 600.0, "annual_income": 108.0, "annual_div_per_share": 9.0, "asset_type": "Stock", "sector": "Consumer Cyclical", "espp_biweekly_contribution": 100.0, "special_contribution": 500.0},
            {"ticker": "ABC", "shares": 10.0, "current_price": 50.0, "market_value": 500.0, "cost_basis": 400.0, "unrealized_gain": 100.0, "annual_income": 20.0, "annual_div_per_share": 2.0, "asset_type": "Stock", "sector": "Technology", "regular_biweekly_contribution": 280.0},
        ])

    def test_current_custody_and_combined_beneficial_hd_are_fully_excluded(self) -> None:
        combined = beneficial_metrics_frame(self.rows, hd_context())
        self.assertAlmostEqual(float(combined.loc[combined.ticker == "HD", "shares"].iloc[0]), 22.318744)

        result = snowball_tracker(combined)

        self.assertEqual(result["current_annual_income"], 20.0)
        self.assertEqual(result["current_monthly_income"], 20.0 / 12)
        self.assertEqual(result["snowball_portfolio_value"], 500.0)
        self.assertEqual(result["weighted_yield_pct"], 4.0)

    def test_hd_share_price_dividend_and_all_activity_shapes_cannot_change_snowball(self) -> None:
        baseline = snowball_tracker(self.rows)
        mutations = []
        for shares, price, dividend in ((1000, 1, 500), (1, 10000, 0.01)):
            changed = self.rows.copy()
            changed.loc[0, ["ticker", "shares", "current_price", "market_value", "annual_income", "annual_div_per_share"]] = [" hd ", shares, price, shares * price, shares * dividend, dividend]
            mutations.append(changed)
        # Purchases, DRIPs, sales, and custody transfers ultimately change HD's
        # shares/value/income; all such resulting states remain irrelevant here.
        for changed in mutations:
            self.assertEqual(snowball_tracker(changed), baseline)

    def test_non_hd_change_updates_income_value_yield_and_milestones(self) -> None:
        baseline = snowball_tracker(self.rows)
        changed = self.rows.copy()
        changed.loc[1, ["shares", "market_value", "annual_income"]] = [20, 1000, 40]
        result = snowball_tracker(changed)
        self.assertEqual(result["current_annual_income"], 40)
        self.assertEqual(result["snowball_portfolio_value"], 1000)
        self.assertGreater(result["milestones"][0]["progress_pct"], baseline["milestones"][0]["progress_pct"])

    def test_regular_contribution_and_final_milestone_rules_are_preserved(self) -> None:
        result = snowball_tracker(self.rows)
        self.assertEqual(result["annual_contribution"], 280 * 26)
        self.assertEqual(result["milestones"][-1]["name"], "Compounding Dominates (200%)")
        self.assertEqual(result["milestones"][-1]["target_annual_income"], 14560)
        self.assertNotEqual(result["annual_contribution"], (280 + 100 + 500) * 26)

    def test_hd_remains_in_full_analysis_and_only_snowball_is_filtered(self) -> None:
        package = build_analysis(self.rows, hd_historical_context=hd_context())
        self.assertGreater(package["totals"]["total_portfolio_value"], 500)
        self.assertGreater(package["totals"]["total_estimated_annual_income"], 20)
        self.assertEqual(package["concentration"]["largest_holding_by_value"], "HD")
        self.assertEqual(package["snowball"]["current_annual_income"], 20)
        self.assertEqual(package["snowball"]["snowball_portfolio_value"], 500)

    def test_streamlit_and_nicegui_disclose_and_use_shared_model(self) -> None:
        root = Path(__file__).resolve().parents[1]
        streamlit = (root / "app.py").read_text(encoding="utf-8")
        nicegui = (root / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        disclosure = "Snowball calculations exclude HD employer stock and ESPP activity"
        self.assertIn("shared_snowball_tracker(analysis_df", streamlit)
        self.assertIn(disclosure, streamlit)
        self.assertIn(disclosure, nicegui)


if __name__ == "__main__":
    unittest.main()

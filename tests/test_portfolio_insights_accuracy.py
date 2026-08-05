import unittest

import pandas as pd

from app import (
    ESPP_CONTRIB_BIWEEKLY,
    _build_insights_asset_allocation,
    _build_insights_contribution_view,
    _build_insights_yield_summary,
    _resolve_insights_income_goal_state,
)


class PortfolioInsightsAccuracyTests(unittest.TestCase):
    def test_build_insights_yield_summary_uses_holding_yield_not_income_share(self) -> None:
        rows = pd.DataFrame(
            [
                {"ticker": "A", "current_price": 100.0, "dividend_yield_pct": 2.0, "effective_annual_div_per_share": 2.0},
                {"ticker": "B", "current_price": 100.0, "dividend_yield_pct": 7.0, "effective_annual_div_per_share": 7.0},
                {"ticker": "C", "current_price": 0.0, "dividend_yield_pct": 0.0, "effective_annual_div_per_share": 0.0},
            ]
        )
        summary = _build_insights_yield_summary(rows)
        self.assertEqual(summary["highest_yield_ticker"], "B")
        self.assertEqual(summary["highest_yield_pct"], 7.0)
        self.assertEqual(summary["yield_spread_pct"], 5.0)

    def test_build_insights_yield_summary_excludes_zero_and_invalid_yields(self) -> None:
        rows = pd.DataFrame(
            [
                {"ticker": "A", "current_price": 100.0, "dividend_yield_pct": 0.0, "effective_annual_div_per_share": 0.0},
                {"ticker": "B", "current_price": 100.0, "dividend_yield_pct": 2.0, "effective_annual_div_per_share": 2.0},
                {"ticker": "C", "current_price": 100.0, "dividend_yield_pct": 5.0, "effective_annual_div_per_share": 5.0},
            ]
        )
        summary = _build_insights_yield_summary(rows)
        self.assertEqual(summary["lowest_positive_yield_ticker"], "B")
        self.assertEqual(summary["yield_spread_pct"], 3.0)

    def test_build_insights_contribution_view_uses_actual_holdings_contributions(self) -> None:
        metrics = pd.DataFrame(
            [
                {"ticker": "A", "market_value": 1000.0, "regular_biweekly_contribution": 280.0, "espp_biweekly_contribution": 0.0, "special_contribution": 0.0},
                {"ticker": "HD", "market_value": 5000.0, "regular_biweekly_contribution": 0.0, "espp_biweekly_contribution": 100.0, "special_contribution": 0.0},
                {"ticker": "NVDA", "market_value": 200.0, "regular_biweekly_contribution": 0.0, "espp_biweekly_contribution": 0.0, "special_contribution": 0.0},
            ]
        )
        view = _build_insights_contribution_view(metrics, 280.0, ESPP_CONTRIB_BIWEEKLY, "6_months", 0.0)
        contribution_rows = view["rows"]
        summary = view["summary"]
        self.assertEqual(contribution_rows.loc[contribution_rows["ticker"] == "A", "total_contribution"].iloc[0], 280.0)
        self.assertEqual(contribution_rows.loc[contribution_rows["ticker"] == "HD", "total_contribution"].iloc[0], 100.0)
        self.assertEqual(contribution_rows.loc[contribution_rows["ticker"] == "NVDA", "total_contribution"].iloc[0], 0.0)
        self.assertEqual(summary["regular_biweekly"], 280.0)
        self.assertEqual(summary["espp_biweekly"], 100.0)
        self.assertEqual(summary["combined_biweekly"], 380.0)
        self.assertEqual(summary["regular_annual"], 7280.0)
        self.assertEqual(summary["espp_annual"], 2600.0)
        self.assertEqual(summary["combined_annual"], 9880.0)
        self.assertEqual(view["active_contributors"], 2)

    def test_build_insights_asset_allocation_includes_all_resolved_categories(self) -> None:
        metrics = pd.DataFrame(
            [
                {"ticker": "AAPL", "market_value": 100.0, "asset_type": "Stock"},
                {"ticker": "AGG", "market_value": 200.0, "asset_type": "ETF"},
                {"ticker": "O", "market_value": 300.0, "asset_type": "REIT"},
                {"ticker": "BND", "market_value": 400.0, "asset_type": "Bond Fund"},
                {"ticker": "CASH", "market_value": 500.0, "asset_type": "Cash/Cash Equivalent"},
            ]
        )
        allocation = _build_insights_asset_allocation(metrics)
        self.assertEqual(set(allocation["asset_type"].tolist()), {"Stock", "ETF/Fund", "REIT", "Bond/Fixed Income", "Cash/Cash Equivalent"})
        self.assertAlmostEqual(allocation["market_value"].sum(), 1500.0)
        self.assertAlmostEqual(allocation["allocation_pct"].sum(), 100.0)

    def test_build_insights_asset_allocation_handles_empty_metrics(self) -> None:
        allocation = _build_insights_asset_allocation(pd.DataFrame(columns=["ticker", "market_value", "asset_type"]))
        self.assertTrue(allocation.empty)

    def test_resolve_insights_income_goal_state_uses_configured_target_when_available(self) -> None:
        goal_state = _resolve_insights_income_goal_state(12000.0, 6000.0)
        self.assertTrue(goal_state["configured"])
        self.assertEqual(goal_state["target_annual_income"], 12000.0)
        self.assertEqual(goal_state["progress_pct"], 50.0)

    def test_resolve_insights_income_goal_state_returns_neutral_message_when_unset(self) -> None:
        goal_state = _resolve_insights_income_goal_state(None, 0.0)
        self.assertFalse(goal_state["configured"])
        self.assertEqual(goal_state["message"], "Set an income goal in Goal Tracker to display progress here.")


if __name__ == "__main__":
    unittest.main()

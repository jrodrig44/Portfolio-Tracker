import unittest

import pandas as pd

from app import (
    ESPP_CONTRIB_BIWEEKLY,
    _build_portfolio_analysis_summary,
    _resolve_holding_yield_pct,
    _select_highest_valid_yield,
    _split_espp_market_value,
    _summarize_contribution_totals,
)


class PortfolioAnalysisAccuracyTests(unittest.TestCase):
    def test_resolve_holding_yield_uses_dividend_yield_and_not_portfolio_value(self) -> None:
        row = {"current_price": 100.0, "effective_annual_div_per_share": 5.0, "dividend_yield_pct": 5.0}
        self.assertEqual(_resolve_holding_yield_pct(row), 5.0)

        fallback_row = {"current_price": 100.0, "effective_annual_div_per_share": 5.0, "dividend_yield_pct": 0.0}
        self.assertEqual(_resolve_holding_yield_pct(fallback_row), 5.0)

    def test_select_highest_valid_yield_prefers_highest_actual_yield(self) -> None:
        rows = [
            {"ticker": "A", "current_price": 100.0, "effective_annual_div_per_share": 3.0, "dividend_yield_pct": 3.0},
            {"ticker": "B", "current_price": 100.0, "effective_annual_div_per_share": 6.0, "dividend_yield_pct": 6.0},
            {"ticker": "C", "current_price": 0.0, "effective_annual_div_per_share": 5.0, "dividend_yield_pct": 0.0},
        ]
        selected = _select_highest_valid_yield(rows)
        self.assertEqual(selected["ticker"], "B")
        self.assertEqual(selected["yield_pct"], 6.0)

    def test_summarize_contributions_uses_regular_and_espp_split(self) -> None:
        contribution_rows = [
            {"regular_biweekly_contribution": 280.0, "espp_biweekly_contribution": 100.0, "special_contribution": 0.0, "total_contribution": 380.0},
        ]
        summary = _summarize_contribution_totals(contribution_rows)
        self.assertEqual(summary["regular_biweekly"], 280.0)
        self.assertEqual(summary["espp_biweekly"], 100.0)
        self.assertEqual(summary["combined_biweekly"], 380.0)
        self.assertEqual(summary["regular_annual"], 7280.0)
        self.assertEqual(summary["espp_annual"], 2600.0)
        self.assertEqual(summary["combined_annual"], 9880.0)

    def test_split_espp_market_value_uses_espp_share_count(self) -> None:
        hd_split = _split_espp_market_value(pd.Series({"shares": 16.670123, "espp_acquired_shares": 16.670123, "current_price": 400.0}))
        self.assertAlmostEqual(hd_split["espp_shares"], 16.670123)
        self.assertAlmostEqual(hd_split["non_espp_shares"], 0.0)
        self.assertAlmostEqual(hd_split["espp_market_value"], 16.670123 * 400.0)
        self.assertAlmostEqual(hd_split["non_espp_market_value"], 0.0)

        partial_split = _split_espp_market_value(pd.Series({"shares": 10.0, "espp_acquired_shares": 6.0, "current_price": 50.0}))
        self.assertEqual(partial_split["espp_market_value"], 300.0)
        self.assertEqual(partial_split["non_espp_market_value"], 200.0)

    def test_build_portfolio_analysis_summary_handles_empty_and_invalid_data(self) -> None:
        empty_summary = _build_portfolio_analysis_summary(pd.DataFrame(columns=["ticker"]), 280.0, 8.0, ESPP_CONTRIB_BIWEEKLY)
        self.assertEqual(empty_summary["highest_yield_ticker"], "N/A")
        self.assertEqual(empty_summary["average_holding_yield_pct"], 0.0)
        self.assertEqual(empty_summary["weighted_portfolio_yield_pct"], 0.0)
        self.assertEqual(empty_summary["active_contributors"], 0)


if __name__ == "__main__":
    unittest.main()

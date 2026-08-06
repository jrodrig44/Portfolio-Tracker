import unittest

import pandas as pd

from app import _build_monthly_income_seasonality, _normalize_active_tab


class DividendIncomeSeasonalityTests(unittest.TestCase):
    def test_historical_monthly_distribution_allocates_annual_income_by_history(self) -> None:
        metrics = pd.DataFrame([{"ticker": "ABC", "annual_income": 120.0}])
        weights_map = {"ABC": [0.0, 0.0, 0.25, 0.0, 0.0, 0.25, 0.0, 0.0, 0.25, 0.0, 0.0, 0.25]}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["monthly_income"]["Mar"], 30.0)
        self.assertEqual(result["monthly_income"]["Jun"], 30.0)
        self.assertEqual(result["monthly_income"]["Sep"], 30.0)
        self.assertEqual(result["monthly_income"]["Dec"], 30.0)
        self.assertEqual(result["monthly_income"]["Jan"], 0.0)
        self.assertAlmostEqual(sum(result["monthly_income"].values()), 120.0)
        self.assertEqual(result["covered_annual_income"], 120.0)
        self.assertEqual(result["uncovered_annual_income"], 0.0)

    def test_monthly_payer_allocates_evenly_across_all_months(self) -> None:
        metrics = pd.DataFrame([{"ticker": "MON", "annual_income": 120.0}])
        weights_map = {"MON": [1 / 12] * 12}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        for month in result["monthly_income"].values():
            self.assertEqual(month, 10.0)
        self.assertAlmostEqual(sum(result["monthly_income"].values()), 120.0)

    def test_uneven_history_allocates_proportionally_and_reconciles(self) -> None:
        metrics = pd.DataFrame([{"ticker": "XYZ", "annual_income": 100.0}])
        weights_map = {"XYZ": [0.1, 0.2, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.3]}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["monthly_income"]["Jan"], 10.0)
        self.assertEqual(result["monthly_income"]["Feb"], 20.0)
        self.assertEqual(result["monthly_income"]["May"], 30.0)
        self.assertEqual(result["monthly_income"]["Nov"], 10.0)
        self.assertEqual(result["monthly_income"]["Dec"], 30.0)
        self.assertAlmostEqual(sum(result["monthly_income"].values()), 100.0)

    def test_no_usable_timing_data_reports_uncovered_income_without_fabricated_months(self) -> None:
        metrics = pd.DataFrame([{"ticker": "NONE", "annual_income": 80.0}])
        weights_map = {"NONE": [0.0] * 12}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["covered_annual_income"], 0.0)
        self.assertEqual(result["uncovered_annual_income"], 80.0)
        self.assertEqual(result["coverage_pct"], 0.0)
        self.assertTrue(result["chart_available"] is False)
        self.assertTrue(all(value == 0.0 for value in result["monthly_income"].values()))

    def test_partial_coverage_reports_coverage_and_uncovered_income(self) -> None:
        metrics = pd.DataFrame(
            [
                {"ticker": "A", "annual_income": 80.0},
                {"ticker": "B", "annual_income": 20.0},
            ]
        )
        weights_map = {"A": [0.0, 0.0, 0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "B": [0.0] * 12}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["covered_annual_income"], 80.0)
        self.assertEqual(result["uncovered_annual_income"], 20.0)
        self.assertAlmostEqual(result["coverage_pct"], 0.8)
        self.assertTrue(result["chart_available"] is True)

    def test_invalid_history_is_ignored_safely(self) -> None:
        metrics = pd.DataFrame([{"ticker": "BAD", "annual_income": 50.0}])
        weights_map = {"BAD": [0.0, float("nan"), -0.1, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["covered_annual_income"], 0.0)
        self.assertEqual(result["uncovered_annual_income"], 50.0)
        self.assertEqual(result["coverage_pct"], 0.0)

    def test_non_finite_history_weights_are_treated_as_unusable(self) -> None:
        metrics = pd.DataFrame([{"ticker": "INF", "annual_income": 60.0}])
        weights_map = {"INF": [float("inf"), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["covered_annual_income"], 0.0)
        self.assertEqual(result["uncovered_annual_income"], 60.0)
        self.assertEqual(result["coverage_pct"], 0.0)
        self.assertTrue(all(value == 0.0 for value in result["monthly_income"].values()))

    def test_month_order_is_january_through_december(self) -> None:
        metrics = pd.DataFrame([{"ticker": "ABC", "annual_income": 120.0}])
        weights_map = {"ABC": [0.0, 0.0, 0.25, 0.0, 0.0, 0.25, 0.0, 0.0, 0.25, 0.0, 0.0, 0.25]}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(list(result["monthly_income"].keys()), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])

    def test_navigation_fallback_replaces_dividend_calendar(self) -> None:
        self.assertEqual(_normalize_active_tab("Dividend Calendar"), "Portfolio Analysis")
        self.assertEqual(_normalize_active_tab(None), "Portfolio Analysis")
        self.assertEqual(_normalize_active_tab("Portfolio Analysis"), "Portfolio Analysis")
        self.assertEqual(_normalize_active_tab("AI Analysis"), "Portfolio Analysis")

    def test_seasonality_preparation_does_not_mutate_hd_values(self) -> None:
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "annual_income": 100.0,
                    "shares": 16.670123,
                    "amount_invested": 5629.00,
                    "espp_biweekly_contribution": 100.0,
                }
            ]
        )
        weights_map = {"HD": [0.0, 0.0, 0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}

        result = _build_monthly_income_seasonality(metrics, month_weights_map=weights_map)

        self.assertEqual(result["monthly_income"]["Mar"], 50.0)
        self.assertEqual(result["monthly_income"]["Jun"], 50.0)
        self.assertEqual(metrics.iloc[0]["shares"], 16.670123)
        self.assertEqual(metrics.iloc[0]["amount_invested"], 5629.00)
        self.assertEqual(metrics.iloc[0]["espp_biweekly_contribution"], 100.0)


if __name__ == "__main__":
    unittest.main()

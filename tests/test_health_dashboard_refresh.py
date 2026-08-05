import unittest
from unittest.mock import patch

import pandas as pd

import app
from app import _compute_metrics, resolve_value


class HealthDashboardRefreshTests(unittest.TestCase):
    def test_resolve_value_prefers_fresh_annualized_income_over_stale_db_snapshot(self) -> None:
        stale_holdings = pd.DataFrame([
            {"ticker": "AAPL", "annualized_income": 10.0}
        ])

        with (
            patch("app._use_export_json_data", return_value=False),
            patch("app._strict_export_accuracy_mode", return_value=False),
            patch.dict(app.st.session_state, {"holdings": stale_holdings}, clear=True),
        ):
            value = resolve_value("annualized_income", "AAPL", 20.0)

        self.assertEqual(value, 20.0)

    def test_resolve_value_prefers_fresh_effective_dividend_over_stale_db_snapshot(self) -> None:
        stale_holdings = pd.DataFrame([
            {"ticker": "AAPL", "effective_annual_div_per_share": 0.5}
        ])

        with (
            patch("app._use_export_json_data", return_value=False),
            patch("app._strict_export_accuracy_mode", return_value=False),
            patch.dict(app.st.session_state, {"holdings": stale_holdings}, clear=True),
        ):
            value = resolve_value("effective_annual_div_per_share", "AAPL", 1.0)

        self.assertEqual(value, 1.0)

    def test_compute_metrics_recalculates_income_when_shares_change(self) -> None:
        stale_holdings = pd.DataFrame([
            {"ticker": "AAPL", "annualized_income": 10.0, "effective_annual_div_per_share": 0.5}
        ])
        holdings = pd.DataFrame([
            {"ticker": "AAPL", "shares": 20, "amount_invested": 200.0, "annual_div_per_share": 1.0}
        ])

        with (
            patch("app._use_export_json_data", return_value=False),
            patch("app._strict_export_accuracy_mode", return_value=False),
            patch.dict(app.st.session_state, {"holdings": stale_holdings}, clear=True),
        ):
            metrics = _compute_metrics(holdings, {"AAPL": 10.0}, {"AAPL": 10.0})

        self.assertEqual(metrics.loc[0, "annualized_income"], 20.0)
        self.assertEqual(metrics.loc[0, "annual_income"], 20.0)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock, patch

from nicegui_app.services.calculations import metrics_frame, portfolio_totals
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator


class NiceGuiPhase1ParityTests(unittest.TestCase):
    def test_runtime_market_maps_drive_streamlit_equivalent_metrics(self):
        holdings=[{"ticker":"abc","shares":2,"amount_invested":80,"current_price":0,"annual_div_per_share":0,"effective_annual_div_per_share":0,"dividend_yield_pct":0,"asset_type":"Stock","sector":"Technology"}]
        metrics=metrics_frame(holdings,{"ABC":50.0},{"ABC":5.0})
        row=metrics.iloc[0]
        self.assertEqual(row["current_price"],50.0)
        self.assertEqual(row["market_value"],100.0)
        self.assertEqual(row["effective_annual_div_per_share"],2.5)
        self.assertEqual(row["annual_income"],5.0)
        self.assertEqual(portfolio_totals(metrics)["weighted_portfolio_yield"],5.0)

    def test_hd_espp_defaults_match_streamlit_normalization(self):
        metrics=metrics_frame([{"ticker":"HD","shares":3,"amount_invested":300,"current_price":100,"annual_div_per_share":4,"regular_biweekly_contribution":25,"special_contribution":10,"espp_biweekly_contribution":0,"espp_acquired_shares":0}])
        row=metrics.iloc[0]
        self.assertTrue(row["employer_stock"] and row["espp_enabled"])
        self.assertEqual(row["regular_biweekly_contribution"],0.0)
        self.assertEqual(row["special_contribution"],0.0)
        self.assertEqual(row["espp_biweekly_contribution"],100.0)
        self.assertEqual(row["espp_discount_pct"],15.0)
        self.assertEqual(row["espp_acquired_shares"],3.0)

    def test_market_loader_is_injectable_and_only_runs_on_portfolio_reload(self):
        market_loader=Mock(return_value=({"ABC":50.0},{"ABC":5.0}))
        coordinator=ReadOnlyRefreshCoordinator(
            holdings_loader=Mock(return_value=[{"ticker":"ABC","shares":2,"amount_invested":80}]),
            history_loader=Mock(return_value=[]),
            market_loader=market_loader,
        )
        with patch("nicegui_app.services.refresh.database_fingerprint", side_effect=[(1,1),(2,2),(1,1),(2,2)]):
            first=coordinator.refresh()
            second=coordinator.refresh()
        self.assertEqual(first.metrics.iloc[0]["market_value"],100.0)
        self.assertFalse(second.portfolio_changed)
        market_loader.assert_called_once_with(["ABC"])


if __name__ == "__main__":
    unittest.main()

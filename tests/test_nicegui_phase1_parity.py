import unittest
import math
from unittest.mock import Mock, patch

import pandas as pd

from nicegui_app.services.calculations import metrics_frame, portfolio_totals
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator
from nicegui_app.services import yahoo


class NiceGuiPhase1ParityTests(unittest.TestCase):
    def test_runtime_market_maps_drive_streamlit_equivalent_metrics(self):
        holdings=[{"ticker":"abc","shares":2,"amount_invested":80,"current_price":45,"annual_div_per_share":0,"effective_annual_div_per_share":0,"dividend_yield_pct":4,"asset_type":"Stock","sector":"Technology"}]
        metrics=metrics_frame(holdings,{"ABC":50.0},{"ABC":5.0})
        row=metrics.iloc[0]
        self.assertEqual(row["current_price"],50.0)
        self.assertEqual(row["market_value"],100.0)
        self.assertEqual(row["effective_annual_div_per_share"],2.5)
        self.assertEqual(row["annual_income"],5.0)
        self.assertEqual(portfolio_totals(metrics)["weighted_portfolio_yield"],5.0)

    def test_invalid_live_values_retain_stored_market_data(self):
        holding={"ticker":"ABC","shares":2,"amount_invested":80,"current_price":45,"annual_div_per_share":2,"dividend_yield_pct":4}
        for price,yield_pct in ((0,0),(None,None),(math.nan,math.nan)):
            with self.subTest(price=price,yield_pct=yield_pct):
                row=metrics_frame([holding],{"ABC":price},{"ABC":yield_pct}).iloc[0]
                self.assertEqual(row["current_price"],45.0)
                self.assertEqual(row["dividend_yield_pct"],4.0)
        missing=metrics_frame([holding],{},{}).iloc[0]
        self.assertEqual(missing["current_price"],45.0)
        self.assertEqual(missing["dividend_yield_pct"],4.0)

    def test_hd_espp_defaults_match_streamlit_normalization(self):
        metrics=metrics_frame([{"ticker":"HD","shares":3,"amount_invested":300,"current_price":100,"annual_div_per_share":4,"regular_biweekly_contribution":25,"special_contribution":10,"espp_biweekly_contribution":0,"espp_acquired_shares":0}])
        row=metrics.iloc[0]
        self.assertTrue(row["employer_stock"] and row["espp_enabled"])
        self.assertEqual(row["regular_biweekly_contribution"],0.0)
        self.assertEqual(row["special_contribution"],0.0)
        self.assertEqual(row["espp_biweekly_contribution"],100.0)
        self.assertEqual(row["espp_discount_pct"],15.0)
        self.assertEqual(row["espp_acquired_shares"],3.0)

    def test_market_refresh_recalculates_without_reloading_holdings(self):
        holdings_loader=Mock(return_value=[{"ticker":"ABC","shares":2,"amount_invested":80,"current_price":45}])
        market_loader=Mock(side_effect=[({"ABC":45.0},{"ABC":4.0}),({"ABC":50.0},{"ABC":5.0})])
        coordinator=ReadOnlyRefreshCoordinator(
            holdings_loader=holdings_loader,
            history_loader=Mock(return_value=[]),
            market_loader=market_loader,
        )
        with patch("nicegui_app.services.refresh.database_fingerprint", side_effect=[(1,1),(2,2),(1,1),(2,2)]):
            first=coordinator.refresh()
            second=coordinator.refresh()
        self.assertEqual(first.metrics.iloc[0]["market_value"],90.0)
        self.assertFalse(second.portfolio_changed)
        self.assertTrue(second.metrics_changed)
        self.assertEqual(second.metrics.iloc[0]["market_value"],100.0)
        holdings_loader.assert_called_once()
        self.assertEqual(market_loader.call_count,2)

    def test_market_cache_prevents_redundant_yahoo_calls_inside_ttl(self):
        yahoo._cache.clear()
        history=pd.DataFrame({"Close":[49.0,50.0]})
        ticker=Mock()
        ticker.info={"trailingAnnualDividendYield":0.05}
        with patch.object(yahoo.yf,"download",return_value=history) as download, patch.object(yahoo.yf,"Ticker",return_value=ticker) as ticker_factory, patch.object(yahoo.time,"monotonic",side_effect=[100.0,100.0,120.0,120.0]):
            first=yahoo.current_market_data(["ABC"])
            second=yahoo.current_market_data(["ABC"])
        self.assertEqual(first,({"ABC":50.0},{"ABC":5.0}))
        self.assertEqual(second,first)
        download.assert_called_once()
        ticker_factory.assert_called_once_with("ABC")


if __name__ == "__main__":
    unittest.main()

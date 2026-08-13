from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import pandas as pd

import app


class MetadataTicker:
    info_calls = 0
    created = 0

    def __init__(self, ticker: str) -> None:
        type(self).created += 1
        self.ticker = ticker
        self._info = {
            "sector": "Technology" if ticker == "AAPL" else "Real Estate",
            "industry": "Software" if ticker == "AAPL" else "REIT",
            "quoteType": "EQUITY" if ticker == "AAPL" else "REIT",
            "longName": "Apple Inc." if ticker == "AAPL" else "Realty Income",
            "dividendYield": 0.0325,
            "trailingAnnualDividendYield": 0.0325,
        }

    @property
    def info(self) -> dict[str, object]:
        type(self).info_calls += 1
        return dict(self._info)

    @property
    def fast_info(self) -> dict[str, float]:
        return {"lastPrice": 100.0, "marketCap": 1000000.0}

    def history(self, period: str = "5d") -> pd.DataFrame:
        return pd.DataFrame({"Close": [99.0, 100.0]})

    earnings_estimate = None
    revenue_estimate = None
    analyst_price_targets = {}
    recommendations = pd.DataFrame()
    funds_data = None


class YahooPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        for function in (
            app._latest_prices,
            app._latest_yahoo_metadata,
            app._latest_sectors,
            app._latest_dividend_yields,
            app._latest_asset_type_hints,
            app._latest_investment_intelligence,
        ):
            function.clear()
        MetadataTicker.info_calls = 0
        MetadataTicker.created = 0

    def tearDown(self) -> None:
        self.setUp()

    def test_derived_metadata_consumers_share_one_info_fetch_per_ticker(self) -> None:
        with patch("app.yf.Ticker", side_effect=MetadataTicker):
            sectors = app._latest_sectors(("AAPL", "O"))
            yields = app._latest_dividend_yields(("AAPL", "O"))
            types = app._latest_asset_type_hints(("AAPL", "O"))

        self.assertEqual(MetadataTicker.info_calls, 2)
        self.assertEqual(sectors["AAPL"], "Technology")
        self.assertEqual(types["AAPL"], "Stock")
        self.assertAlmostEqual(yields["AAPL"], 3.25)

    def test_metadata_is_serializable_and_failed_ticker_does_not_discard_success(self) -> None:
        class PartiallyFailingTicker(MetadataTicker):
            @property
            def info(self) -> dict[str, object]:
                if self.ticker == "BAD":
                    raise RuntimeError("Yahoo failure")
                return super().info

        with patch("app.yf.Ticker", side_effect=PartiallyFailingTicker):
            metadata = app._latest_yahoo_metadata(("AAPL", "BAD"), include_fast_info=True)

        json.dumps(metadata, allow_nan=False)
        self.assertIn("sector", metadata["AAPL"]["info"])
        self.assertEqual(metadata["BAD"], {})

    def test_price_batch_resolves_multiple_tickers_without_fallback(self) -> None:
        batch = pd.DataFrame(
            {"AAPL": [100.0, 100.0], "O": [100.0, 101.0]},
            index=pd.date_range("2026-01-01", periods=2),
        )
        batch.columns = pd.MultiIndex.from_tuples([("AAPL", "Close"), ("O", "Close")])
        with patch("app.yf.download", return_value=batch) as download, patch("app.yf.Ticker") as ticker:
            prices = app._latest_prices(("AAPL", "O"))

        self.assertEqual(prices, {"AAPL": 100.0, "O": 101.0})
        download.assert_called_once()
        ticker.assert_not_called()

    def test_single_price_and_missing_ticker_fallback(self) -> None:
        single = pd.DataFrame({"Close": [100.0, 102.0]}, index=pd.date_range("2026-01-01", periods=2))
        single.columns = pd.MultiIndex.from_tuples([("AAPL", "Close")])
        fallback = MetadataTicker("MISSING")
        with patch("app.yf.download", return_value=single), patch("app.yf.Ticker", return_value=fallback) as ticker:
            prices = app._latest_prices(("AAPL", "MISSING"))

        self.assertEqual(prices["AAPL"], 102.0)
        self.assertEqual(prices["MISSING"], 100.0)
        ticker.assert_called_once_with("MISSING")

    def test_single_ticker_flat_batch_response_is_parsed(self) -> None:
        batch = pd.DataFrame({"Close": [100.0, 102.0]}, index=pd.date_range("2026-01-01", periods=2))
        with patch("app.yf.download", return_value=batch), patch("app.yf.Ticker") as ticker:
            prices = app._latest_prices(("AAPL",))

        self.assertEqual(prices, {"AAPL": 102.0})
        ticker.assert_not_called()

    def test_failed_fallback_and_nonfinite_batch_values_return_zero(self) -> None:
        batch = pd.DataFrame(
            {"Close": [float("inf"), float("nan")]},
            index=pd.date_range("2026-01-01", periods=2),
        )
        failing_ticker = type("FailingTicker", (), {"history": lambda self, period="5d": (_ for _ in ()).throw(RuntimeError("failed"))})()
        with patch("app.yf.download", return_value=batch), patch("app.yf.Ticker", return_value=failing_ticker):
            prices = app._latest_prices(("BAD",))

        self.assertEqual(prices["BAD"], 0.0)

    def test_investment_intelligence_reuses_metadata_and_is_cached(self) -> None:
        with patch("app.yf.Ticker", side_effect=MetadataTicker) as ticker:
            first = app._latest_investment_intelligence(("AAPL",))
            second = app._latest_investment_intelligence(("AAPL",))

        self.assertEqual(first, second)
        self.assertEqual(MetadataTicker.info_calls, 1)
        self.assertEqual(ticker.call_count, 2)


if __name__ == "__main__":
    unittest.main()

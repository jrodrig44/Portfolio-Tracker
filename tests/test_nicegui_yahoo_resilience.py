from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from yfinance.exceptions import YFDataException

from nicegui_app.services import ai_context, yahoo


class FundDataTicker:
    fund_accesses: dict[str, int] = {}

    def __init__(self, ticker: str, *, weights: dict[str, float] | None = None, fail_fund_data: bool = False) -> None:
        self.ticker = ticker
        self.weights = weights
        self.fail_fund_data = fail_fund_data

    @property
    def info(self) -> dict[str, object]:
        return {"sector": "Technology" if self.ticker == "APLE" else None, "trailingAnnualDividendYield": 0.025, "dividendRate": 2.5, "payoutRatio": 0.45}

    def history(self, period: str = "2y") -> pd.DataFrame:
        return pd.DataFrame()

    @property
    def funds_data(self) -> SimpleNamespace:
        self.fund_accesses[self.ticker] = self.fund_accesses.get(self.ticker, 0) + 1
        if self.fail_fund_data:
            raise YFDataException(f"{self.ticker}: No Fund data found.")
        return SimpleNamespace(sector_weightings=self.weights)


class NiceGuiYahooResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        yahoo._cache.clear()
        FundDataTicker.fund_accesses.clear()

    def tearDown(self) -> None:
        self.setUp()

    @staticmethod
    def metrics(*rows: dict[str, object]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_non_fund_holding_does_not_request_fund_data(self) -> None:
        proxy = FundDataTicker("APLE", fail_fund_data=True)

        weights = yahoo._load_fund_sector_weights("APLE", proxy, "Stock")

        self.assertEqual(weights, {})
        self.assertEqual(FundDataTicker.fund_accesses, {})

    def test_fund_data_exception_is_optional_and_cached(self) -> None:
        metrics = self.metrics({"ticker": "MISSING", "asset_type": "ETF", "market_value": 100.0, "annual_income": 1.0})
        with patch("nicegui_app.services.yahoo.yf.Ticker", side_effect=lambda ticker: FundDataTicker(ticker, fail_fund_data=True)):
            first = yahoo.yahoo_intelligence_package(metrics)
            second = yahoo.yahoo_intelligence_package(metrics)

        self.assertEqual(first["fund_weights"], {})
        self.assertEqual(second["fund_weights"], {})
        self.assertEqual(FundDataTicker.fund_accesses["MISSING"], 1)

    def test_etf_sector_weights_are_unchanged(self) -> None:
        expected = {"technology": 0.25, "financial_services": 0.75}
        proxy = FundDataTicker("JEPI", weights=expected)

        weights = yahoo._load_fund_sector_weights("JEPI", proxy, "ETF")

        self.assertEqual(weights, expected)

    def test_package_continues_when_one_holding_has_no_fund_data(self) -> None:
        metrics = self.metrics(
            {"ticker": "APLE", "asset_type": "Stock", "sector": "Real Estate", "market_value": 100.0, "annual_income": 2.0},
            {"ticker": "JEPI", "asset_type": "ETF", "sector": "", "market_value": 200.0, "annual_income": 8.0},
        )

        def ticker_factory(ticker: str) -> FundDataTicker:
            return FundDataTicker(ticker, weights={"technology": 1.0} if ticker == "JEPI" else None, fail_fund_data=ticker == "APLE")

        with patch("nicegui_app.services.yahoo.yf.Ticker", side_effect=ticker_factory):
            package = yahoo.yahoo_intelligence_package(metrics)

        self.assertEqual(package["fund_weights"], {"JEPI": {"technology": 1.0}})
        self.assertEqual({row["ticker"] for row in package["investment_intelligence"]}, {"APLE", "JEPI"})
        self.assertEqual(package["dividend_metadata"][0]["dividend_yield_pct"], 2.5)
        self.assertEqual(package["dividend_metadata"][0]["dividend_rate"], 2.5)
        self.assertEqual(package["dividend_metadata"][0]["payout_ratio"], 0.45)
        self.assertNotIn("APLE", FundDataTicker.fund_accesses)

    def test_export_context_completes_and_ai_provider_layer_is_reached(self) -> None:
        holdings = [{"ticker": "APLE", "shares": 1.0, "amount_invested": 100.0, "annual_div_per_share": 2.0, "asset_type": "Stock", "sector": "Real Estate"}]
        metrics = self.metrics({
            "ticker": "APLE",
            "shares": 1.0,
            "amount_invested": 100.0,
            "current_price": 110.0,
            "market_value": 110.0,
            "cost_basis": 100.0,
            "unrealized_gain": 10.0,
            "annual_div_per_share": 2.0,
            "annual_income": 2.0,
            "dividend_yield_pct": 1.82,
            "portfolio_yield_pct": 1.82,
            "asset_type": "Stock",
            "sector": "Real Estate",
        })
        expected_state = SimpleNamespace(last_stage="success")

        with (
            patch("nicegui_app.services.yahoo.yf.Ticker", side_effect=lambda ticker: FundDataTicker(ticker, fail_fund_data=True)),
            patch("nicegui_app.services.ai_context.shared_run_ai_analysis", return_value=expected_state) as provider_layer,
        ):
            payload = ai_context._production_export_payload(holdings, metrics, [], {})
            state = ai_context.run_ai_analysis(holdings, metrics, "Risk Analysis", "gpt-4.1-mini", "", history=[])

        self.assertEqual(payload["totals"]["largest_holding"], "APLE")
        self.assertIs(state, expected_state)
        provider_layer.assert_called_once()
        self.assertNotIn("APLE", FundDataTicker.fund_accesses)


if __name__ == "__main__":
    unittest.main()

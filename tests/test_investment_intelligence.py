import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app import _build_ai_export_payload, _build_export_markdown, _build_investment_intelligence_rows


class FakeFundsData:
    def __init__(self) -> None:
        self.description = "A broad market fund"
        self.top_holdings = pd.DataFrame(
            [
                {"Holding": "AAPL", "Weight (%)": 10.0},
                {"Holding": "MSFT", "Weight (%)": 8.0},
            ]
        )
        self.equity_holdings = pd.DataFrame(
            [{"Holding": "AAPL", "Weight (%)": 10.0}]
        )
        self.bond_holdings = pd.DataFrame(columns=["Holding", "Weight (%)"])
        self.bond_ratings = {"AAA": 0.2}
        self.sector_weightings = {"Technology": 0.6, "Healthcare": 0.4}
        self.fund_operations = pd.DataFrame([{"Expense Ratio": 0.03, "Net Assets": 1000000000}])


class NoFundamentalsTicker:
    def __init__(self, ticker: str) -> None:
        self.info = {}
        self.fast_info = {}
        self.income_stmt = pd.DataFrame()
        self.balance_sheet = pd.DataFrame()
        self.cashflow = pd.DataFrame()
        self.earnings_estimate = None
        self.revenue_estimate = None
        self.analyst_price_targets = {}
        self.recommendations = pd.DataFrame()
        self.funds_data = None


class FakeTicker:
    def __init__(self, ticker: str) -> None:
        self.info = {
            "forwardPE": 20.0,
            "trailingPE": 18.0,
            "priceToBook": 3.5,
            "profitMargins": 0.22,
            "operatingMargins": 0.25,
            "returnOnEquity": 0.30,
            "debtToEquity": 0.6,
            "marketCap": 1000000000,
            "dividendYield": 0.01,
            "category": "Large Blend",
        }
        self.fast_info = {"marketCap": 1000000000, "lastPrice": 100.0}
        self.income_stmt = pd.DataFrame(
            [[100.0, 90.0], [20.0, 18.0], [30.0, 28.0]],
            index=["Total Revenue", "Net Income", "Operating Income"],
            columns=["2024-01-01", "2023-01-01"],
        )
        self.balance_sheet = pd.DataFrame(
            [[50.0, 60.0], [10.0, 12.0]],
            index=["Total Debt", "Total Cash"],
            columns=["2024-01-01", "2023-01-01"],
        )
        self.cashflow = pd.DataFrame(
            [[15.0]],
            index=["Free Cash Flow"],
            columns=["2024-01-01"],
        )
        self.earnings_estimate = {
            "avg": {"+1y": 2.5},
            "growth": {"+1y": 0.08},
            "numberOfAnalysts": {"+1y": 20},
        }
        self.revenue_estimate = {
            "avg": {"+1y": 120000000000},
            "growth": {"+1y": 0.06},
            "numberOfAnalysts": {"+1y": 15},
        }
        self.analyst_price_targets = {
            "targetMeanPrice": 110.0,
            "recommendationMean": 1.8,
        }
        self.recommendations = pd.DataFrame([{"To Grade": "Buy"}])
        if ticker in {"JEPI"}:
            self.funds_data = FakeFundsData()
        else:
            self.funds_data = None


class InvestmentIntelligenceTests(unittest.TestCase):
    def test_ai_payload_includes_supplemental_yahoo_financial_data(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.96,
                    "sector": "Technology",
                    "asset_type": "Stock",
                },
                {
                    "ticker": "JEPI",
                    "shares": 5,
                    "amount_invested": 500,
                    "annual_div_per_share": 0.0,
                    "sector": "",
                    "asset_type": "ETF",
                },
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "current_price": 200.0,
                    "market_value": 2000.0,
                    "annual_income": 9.6,
                    "portfolio_yield_pct": 0.48,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 1000.0,
                    "sector": "Technology",
                    "asset_type": "Stock",
                },
                {
                    "ticker": "JEPI",
                    "current_price": 100.0,
                    "market_value": 500.0,
                    "annual_income": 0.0,
                    "portfolio_yield_pct": 0.0,
                    "cost_basis": 500.0,
                    "unrealized_gain": 0.0,
                    "sector": "",
                    "asset_type": "ETF",
                },
            ]
        )

        with patch("app.yf.Ticker", side_effect=FakeTicker):
            payload = _build_ai_export_payload(holdings, metrics)

        self.assertIn("supplemental_yahoo_financial_data", payload)
        self.assertEqual(payload["supplemental_yahoo_financial_data"][0]["ticker"], "AAPL")
        self.assertIn("fundamental_metrics", payload["supplemental_yahoo_financial_data"][0])
        self.assertEqual(payload["supplemental_yahoo_financial_data"][0]["fundamental_metrics"]["pe_ratio"], 20.0)
        self.assertEqual(payload["supplemental_yahoo_financial_data"][1]["ticker"], "JEPI")
        self.assertIn("fund_data", payload["supplemental_yahoo_financial_data"][1])

    def test_investment_intelligence_rows_use_nulls_for_missing_values(self) -> None:
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "current_price": 200.0,
                    "market_value": 2000.0,
                    "annual_income": 9.6,
                    "portfolio_yield_pct": 0.48,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 1000.0,
                    "sector": "Technology",
                    "asset_type": "Stock",
                }
            ]
        )

        with patch("app.yf.Ticker", side_effect=FakeTicker):
            rows = _build_investment_intelligence_rows(metrics)

        self.assertEqual(rows[0]["PE"], 20.0)
        self.assertEqual(rows[0]["Revenue Growth"], 0.06)

    def test_tickers_with_no_yahoo_intelligence_are_omitted(self) -> None:
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "NOPE",
                    "current_price": 200.0,
                    "market_value": 2000.0,
                    "annual_income": 9.6,
                    "portfolio_yield_pct": 0.48,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 1000.0,
                    "sector": "Technology",
                    "asset_type": "Stock",
                }
            ]
        )

        with patch("app.yf.Ticker", side_effect=NoFundamentalsTicker):
            rows = _build_investment_intelligence_rows(metrics)

        self.assertEqual(rows, [])

    def test_missing_yahoo_data_renders_graceful_empty_sections(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "NOPE",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.0,
                    "sector": "",
                    "asset_type": "Stock",
                }
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "NOPE",
                    "current_price": 100.0,
                    "market_value": 1000.0,
                    "annual_income": 0.0,
                    "portfolio_yield_pct": 0.0,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 0.0,
                    "sector": "",
                    "asset_type": "Stock",
                }
            ]
        )

        with patch("app.yf.Ticker", side_effect=NoFundamentalsTicker):
            content = _build_export_markdown(holdings, metrics)

        self.assertIn("## Supplemental Yahoo Finance Fundamental Data", content)
        self.assertIn("## Supplemental Yahoo Finance Earnings and Estimates", content)
        self.assertIn("## Supplemental Yahoo Finance Fund Data", content)
        self.assertIn("(No data available)", content)

    def test_export_markdown_renders_stock_and_fund_intelligence_details(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.96,
                    "sector": "Technology",
                    "asset_type": "Stock",
                },
                {
                    "ticker": "JEPI",
                    "shares": 5,
                    "amount_invested": 500,
                    "annual_div_per_share": 0.0,
                    "sector": "",
                    "asset_type": "ETF",
                },
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "current_price": 200.0,
                    "market_value": 2000.0,
                    "annual_income": 9.6,
                    "portfolio_yield_pct": 0.48,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 1000.0,
                    "sector": "Technology",
                    "asset_type": "Stock",
                },
                {
                    "ticker": "JEPI",
                    "current_price": 100.0,
                    "market_value": 500.0,
                    "annual_income": 0.0,
                    "portfolio_yield_pct": 0.0,
                    "cost_basis": 500.0,
                    "unrealized_gain": 0.0,
                    "sector": "",
                    "asset_type": "ETF",
                },
            ]
        )

        with patch("app.yf.Ticker", side_effect=FakeTicker):
            content = _build_export_markdown(holdings, metrics)

        self.assertIn("- AAPL: PE 20.0", content)
        self.assertIn("- JEPI: A broad market fund", content)
        self.assertIn("- Sector Weights:", content)

    def test_export_markdown_includes_new_investment_intelligence_sections(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.96,
                    "sector": "Technology",
                    "asset_type": "Stock",
                }
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "current_price": 200.0,
                    "market_value": 2000.0,
                    "annual_income": 9.6,
                    "portfolio_yield_pct": 0.48,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 1000.0,
                    "sector": "Technology",
                    "asset_type": "Stock",
                }
            ]
        )

        with patch("app.yf.Ticker", side_effect=FakeTicker):
            content = _build_export_markdown(holdings, metrics)

        self.assertIn("## Supplemental Yahoo Finance Fundamental Data", content)
        self.assertIn("## Supplemental Yahoo Finance Earnings and Estimates", content)
        self.assertIn("## Supplemental Yahoo Finance Fund Data", content)

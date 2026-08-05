import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app import _build_ai_export_payload, _build_export_markdown, _enable_periodic_refresh, _latest_historical_context, _normalize_columns


class AutoRefreshTests(unittest.TestCase):
    @patch("app.st_autorefresh")
    def test_enable_periodic_refresh_uses_streamlit_autorefresh(self, autorefresh_mock) -> None:
        _enable_periodic_refresh(60000)

        autorefresh_mock.assert_called_once_with(interval=60000, limit=None, key="market_data_refresh")

    def test_normalize_columns_preserves_regular_and_special_contributions(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "biweekly contribution": 50.0,
                    "special contribution": 25.0,
                }
            ]
        )

        normalized = _normalize_columns(holdings)

        self.assertEqual(normalized.loc[0, "regular_biweekly_contribution"], 50.0)
        self.assertEqual(normalized.loc[0, "special_contribution"], 25.0)

    def test_ai_export_payload_includes_contribution_context(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.96,
                    "regular_biweekly_contribution": 50.0,
                    "special_contribution": 25.0,
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

        payload = _build_ai_export_payload(holdings, metrics)

        self.assertIn("contribution_context", payload)
        self.assertEqual(payload["contribution_context"]["holdings"][0]["ticker"], "AAPL")
        self.assertEqual(payload["contribution_context"]["holdings"][0]["regular_biweekly_contribution"], 50.0)

    def test_build_export_markdown_includes_contribution_context(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.96,
                    "regular_biweekly_contribution": 50.0,
                    "special_contribution": 25.0,
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

        markdown = _build_export_markdown(holdings, metrics)

        self.assertIn("Total Regular Biweekly Contributions", markdown)
        self.assertIn("Estimated Annual HD ESPP Contributions", markdown)
        self.assertIn("12. Evaluate income quality separately from headline yield.", markdown)
        self.assertIn("Tracker Health Score Metadata", markdown)

    def test_contribution_context_marks_hd_as_special_espp(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 10, "amount_invested": 1000}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 2000.0, "annual_income": 9.6}])

        payload = _build_ai_export_payload(holdings, metrics)
        hd_contribution = payload["contribution_context"]["holdings"][0]

        self.assertEqual(hd_contribution["contribution_type"], "espp")
        self.assertEqual(hd_contribution["contribution_frequency"], "biweekly")
        self.assertEqual(hd_contribution["purchase_frequency"], "6_months")
        self.assertIn("every biweekly paycheck", hd_contribution["contribution_note"])
        self.assertIn("espp_biweekly_contribution", hd_contribution)
        self.assertIn("special_contribution", hd_contribution)
        self.assertEqual(hd_contribution["special_contribution"], 0.0)
        self.assertEqual(payload["contribution_context"]["summary"]["annual_hd_espp_contribution"], 2600.0)

    def test_projection_note_explains_regular_contribution_assumption(self) -> None:
        holdings = pd.DataFrame([
            {
                "ticker": "AAPL",
                "shares": 10,
                "amount_invested": 1000,
                "annual_div_per_share": 0.96,
                "regular_biweekly_contribution": 50.0,
                "special_contribution": 25.0,
            }
        ])
        metrics = pd.DataFrame([
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
        ])

        payload = _build_ai_export_payload(holdings, metrics, biweekly_contribution=280.0)
        markdown = _build_export_markdown(holdings, metrics, biweekly_contribution=280.0)

        self.assertIn("projection_contribution_note", payload["portfolio_snapshot"])
        self.assertIn("regular $280 biweekly contribution assumption", payload["portfolio_snapshot"]["projection_contribution_note"])
        self.assertIn("regular $280 biweekly contribution assumption", markdown)

    def test_ai_export_payload_includes_recommendation_context(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000,
                    "annual_div_per_share": 0.96,
                    "regular_biweekly_contribution": 50.0,
                    "special_contribution": 25.0,
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

        payload = _build_ai_export_payload(holdings, metrics)

        self.assertIn("recommendation_context", payload)
        self.assertIn("recommended_actions", payload["recommendation_context"])
        self.assertTrue(payload["recommendation_context"]["recommended_actions"])
        self.assertEqual(
            payload["recommendation_context"]["decision_objective"],
            "Recommend whether to increase, maintain, or reduce contributions and how to allocate new capital across holdings.",
        )
        self.assertIn("output_schema", payload["recommendation_context"])
        self.assertIn("recommended_action", payload["recommendation_context"]["output_schema"])
        self.assertIn("contribution_changes", payload["recommendation_context"]["output_schema"])
        self.assertNotIn("allocation_change_pct", payload["recommendation_context"]["output_schema"])
        self.assertNotIn("contribution_change_pct", payload["recommendation_context"]["output_schema"])
        self.assertEqual(
            payload["recommendation_context"]["output_schema"]["contribution_changes"][0]["ticker"],
            "string",
        )

    def test_latest_historical_context_marks_yahoo_as_supplemental_history(self) -> None:
        fake_history = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
                "Open": [100.0, 105.0],
                "High": [101.0, 106.0],
                "Low": [99.0, 104.0],
                "Close": [100.0, 110.0],
                "Adj Close": [100.0, 110.0],
                "Volume": [1000, 1200],
            },
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        fake_ticker = SimpleNamespace(
            history=lambda *args, **kwargs: fake_history,
            dividends=pd.Series(dtype=float),
        )

        with patch("app.yf.Ticker", return_value=fake_ticker):
            context = _latest_historical_context(("AAPL",), history_start_date="2024-01-01")

        self.assertEqual(context["AAPL"]["history_source"], "Yahoo Finance")
        self.assertEqual(context["AAPL"]["history_type"], "supplemental")
        self.assertEqual(context["AAPL"]["history_start_date"], "2024-01-01")
        self.assertIn("supplemental", context["AAPL"]["history_summary"].lower())

    def test_ai_export_payload_includes_supplemental_yahoo_historical_price_data(self) -> None:
        fake_history = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
                "Open": [100.0, 105.0],
                "High": [101.0, 106.0],
                "Low": [99.0, 104.0],
                "Close": [100.0, 110.0],
                "Adj Close": [100.0, 110.0],
                "Volume": [1000, 1200],
            },
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        fake_ticker = SimpleNamespace(
            history=lambda *args, **kwargs: fake_history,
            dividends=pd.Series(dtype=float),
        )

        with patch("app.yf.Ticker", return_value=fake_ticker):
            payload = _build_ai_export_payload(
                pd.DataFrame([{"ticker": "AAPL", "shares": 10, "amount_invested": 1000}]),
                pd.DataFrame([{"ticker": "AAPL", "current_price": 200.0, "market_value": 2000.0, "annual_income": 9.6}]),
            )

        section = payload["supplemental_yahoo_historical_price_data"]
        self.assertEqual(section[0]["ticker"], "AAPL")
        self.assertEqual(section[0]["history_type"], "supplemental")
        self.assertEqual(section[0]["date_range"]["start"], "2024-01-01")
        self.assertEqual(section[0]["historical_price_data"][0]["date"], "2024-01-01")
        self.assertEqual(section[0]["historical_price_data"][0]["close"], 100.0)
        self.assertEqual(section[0]["historical_price_data"][0]["adjusted_close"], 100.0)

    def test_build_export_markdown_includes_supplemental_yahoo_historical_price_data(self) -> None:
        fake_history = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
                "Open": [100.0, 105.0],
                "High": [101.0, 106.0],
                "Low": [99.0, 104.0],
                "Close": [100.0, 110.0],
                "Adj Close": [100.0, 110.0],
                "Volume": [1000, 1200],
            },
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        fake_ticker = SimpleNamespace(
            history=lambda *args, **kwargs: fake_history,
            dividends=pd.Series(dtype=float),
        )

        with patch("app.yf.Ticker", return_value=fake_ticker):
            markdown = _build_export_markdown(
                pd.DataFrame([{"ticker": "AAPL", "shares": 10, "amount_invested": 1000}]),
                pd.DataFrame([{"ticker": "AAPL", "current_price": 200.0, "market_value": 2000.0, "annual_income": 9.6}]),
            )

        self.assertIn("## Supplemental Yahoo Finance Historical Price Data", markdown)
        self.assertIn("supplemental historical market-price context", markdown.lower())
        self.assertIn("AAPL: supplemental Yahoo history", markdown)

    @patch("app._latest_historical_context")
    def test_ai_export_payload_includes_historical_context(self, historical_context_mock) -> None:
        historical_context_mock.return_value = {
            "AAPL": {
                "ticker": "AAPL",
                "price_trend": {"change_3m_pct": 3.5, "change_1y_pct": 12.0},
                "dividend_trend": {"change_1y_pct": 5.0},
                "history_summary": "Price up over the last year.",
            }
        }
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 10, "amount_invested": 1000, "annual_div_per_share": 0.96}])
        metrics = pd.DataFrame([
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
        ])

        payload = _build_ai_export_payload(holdings, metrics)

        self.assertIn("historical_context", payload)
        self.assertEqual(payload["historical_context"][0]["ticker"], "AAPL")
        self.assertEqual(payload["historical_context"][0]["price_trend"]["change_1y_pct"], 12.0)

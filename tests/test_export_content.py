import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app import _build_ai_export_json, _build_data_completeness, _build_direct_sector_classification_records, _build_export_markdown, _coerce_numeric_columns, _latest_dividend_yields


class ExportMarkdownTests(unittest.TestCase):
    def test_coerce_numeric_columns_defaults_missing_contribution_columns_to_zero(self) -> None:
        frame = pd.DataFrame([{"ticker": "AAPL"}])

        result = _coerce_numeric_columns(frame, ["regular_contribution", "total_contribution"])

        self.assertEqual(result["regular_contribution"].iloc[0], 0.0)
        self.assertEqual(result["total_contribution"].iloc[0], 0.0)

    def test_export_contains_ai_summary_and_data_sections(self) -> None:
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

        content = _build_export_markdown(
            holdings,
            metrics,
            biweekly_contribution=280.0,
            projection_return_pct=8.0,
        )

        self.assertIn("# Dividend Portfolio Tracker Export", content)
        self.assertIn("## AI Context Summary", content)
        self.assertIn("## Employer Stock / ESPP Context", content)
        self.assertIn("employer-sponsored Employee Stock Purchase Plan (ESPP)", content)
        self.assertIn("Total Portfolio Value", content)
        self.assertIn("Tracker Health Score:", content)
        self.assertIn("Health Score Basis:", content)
        self.assertIn("Data Completeness:", content)
        self.assertIn("Strongest Areas:", content)
        self.assertIn("Weakest Areas:", content)
        self.assertIn("Primary Alert:", content)
        self.assertIn("## Asset Allocation", content)
        self.assertIn("## Exposure Interpretation", content)
        self.assertIn("Direct sector exposure represents individually held securities with a resolved sector classification.", content)
        self.assertIn("Look-through sector exposure represents estimated underlying sector exposure from funds using available Yahoo Finance fund-sector weights.", content)
        self.assertIn("Unresolved fund look-through exposure represents fund assets whose underlying sector weights could not be resolved.", content)
        self.assertIn("## Direct Sector Exposure", content)
        self.assertIn("## Direct Holdings by Sector", content)
        self.assertIn("## Look-Through Sector Exposure", content)
        self.assertIn("## Unresolved Fund Look-Through Exposure", content)
        self.assertIn("## Income Allocation by Holding", content)
        self.assertIn("## Income Allocation by Asset Type", content)
        self.assertIn("## Economic Exposure Allocation", content)
        self.assertIn("## Dividend Data Metadata", content)
        self.assertIn("## Data Completeness", content)
        self.assertNotIn("## Dividend Calendar Metadata", content)
        self.assertNotIn("TokenRouter", content)
        self.assertIn("## Top 5 Holdings", content)
        self.assertIn("## Top 5 Income Contributors", content)
        self.assertIn("## Holdings Data (CSV)", content)
        self.assertIn("AAPL", content)
        self.assertNotIn("## Projected Income Calendar", content)

    def test_hd_primary_alert_includes_espp_context(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "shares": 10,
                    "amount_invested": 100,
                    "annual_div_per_share": 1.0,
                    "sector": "Consumer Cyclical",
                    "asset_type": "Stock",
                },
                {
                    "ticker": "O",
                    "shares": 1,
                    "amount_invested": 10,
                    "annual_div_per_share": 1.0,
                    "sector": "Real Estate",
                    "asset_type": "REIT",
                },
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "current_price": 100.0,
                    "market_value": 1000.0,
                    "annual_income": 10.0,
                    "portfolio_yield_pct": 1.0,
                    "cost_basis": 900.0,
                    "unrealized_gain": 100.0,
                    "sector": "Consumer Cyclical",
                    "asset_type": "Stock",
                },
                {
                    "ticker": "O",
                    "current_price": 10.0,
                    "market_value": 10.0,
                    "annual_income": 1.0,
                    "portfolio_yield_pct": 10.0,
                    "cost_basis": 10.0,
                    "unrealized_gain": 0.0,
                    "sector": "Real Estate",
                    "asset_type": "REIT",
                },
            ]
        )

        content = _build_export_markdown(holdings, metrics)

        self.assertIn("Primary Alert: Concentration alert: HD", content)
        self.assertIn("employer-sponsored Employee Stock Purchase Plan (ESPP)", content)
        self.assertIn("4. Evaluate concentration by holding, sector, asset type, income source.", content)
        self.assertIn("- HD (The Home Depot) is the user's employer stock", content)
        self.assertIn("13. Recognize ESPP discount and employer exposure.", content)

    def test_suspicious_dividend_yield_values_are_sanitized(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "NVDA",
                    "shares": 1,
                    "amount_invested": 0.35,
                    "annual_div_per_share": 96.91,
                    "sector": "Technology",
                    "asset_type": "Stock",
                }
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "NVDA",
                    "current_price": 0.35,
                    "market_value": 0.35,
                    "annual_income": 0.18,
                    "portfolio_yield_pct": 51.0,
                    "cost_basis": 0.35,
                    "unrealized_gain": 0.0,
                    "sector": "Technology",
                    "asset_type": "Stock",
                }
            ]
        )

        content = _build_export_markdown(holdings, metrics)
        self.assertIn("Data Quality Note", content)
        self.assertIn("NVDA", content)

    def test_ai_json_export_contains_structured_sections(self) -> None:
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

        payload = json.loads(_build_ai_export_json(holdings, metrics))

        self.assertIn("portfolio_snapshot", payload)
        self.assertIn("portfolio_health", payload)
        self.assertIn("asset_allocation", payload)
        self.assertIn("direct_sector_exposure", payload)
        self.assertIn("direct_holdings_by_classification", payload)
        self.assertIn("look_through_sector_exposure", payload)
        self.assertIn("unresolved_fund_look_through_exposure", payload)
        self.assertIn("economic_exposure_allocation", payload)
        self.assertIn("dividend_data_metadata", payload)
        self.assertNotIn("dividend_calendar", payload)
        self.assertIn("data_quality_issues", payload)
        self.assertIn("data_completeness", payload)
        self.assertIn("analysis_guidance", payload)
        self.assertIn("concentration_context", payload)
        self.assertIn("context_summary", payload["portfolio_health"])
        self.assertIn("health_score_breakdown", payload["portfolio_health"])
        self.assertIn("health_score_methodology", payload["portfolio_health"])
        self.assertEqual(payload["portfolio_snapshot"]["top_holding"], "AAPL")
        self.assertEqual(payload["investor_profile"]["portfolio_strategy"], "Dividend growth and income")
        self.assertEqual(payload["investor_profile"]["primary_goal"], "Build long-term wealth while increasing dividend income")
        self.assertTrue(payload["recommendation_constraints"]["avoid_forced_selling"])
        self.assertTrue(payload["recommendation_constraints"]["prefer_new_contributions_for_rebalancing"])
        self.assertNotIn("source_annual_dividend_per_share", payload["holdings"][0])
        self.assertIn("estimated annual distribution per share", payload["field_definitions"]["annualized_income"].lower())
        self.assertIn("estimated_annual_distribution_per_share", payload["holdings"][0])
        self.assertIn("forward distribution", payload["field_definitions"]["estimated_annual_distribution_per_share"].lower())
        self.assertIn("annualized_income", payload["holdings"][0])
        self.assertIn("capital concentration", payload["concentration_context"]["guidance"].lower())
        self.assertIn(
            "Direct sector exposure represents individually held securities with a resolved sector classification.",
            payload["analysis_guidance"],
        )
        self.assertIn(
            "Look-through sector exposure represents estimated underlying sector exposure from funds using available Yahoo Finance fund-sector weights.",
            payload["analysis_guidance"],
        )
        self.assertEqual(payload["asset_allocation"][0]["asset_type"], "Stocks")
        self.assertEqual(payload["portfolio_health"]["context_summary"]["strongest_areas"][0]["area"], "Data Quality")
        self.assertIn("overall_resolved_exposure_coverage_pct", payload["data_completeness"])

    def test_outlier_dividend_yield_uses_trailing_value_when_available(self) -> None:
        class FakeTicker:
            def __init__(self, ticker: str) -> None:
                self.info = {
                    "dividendYield": 0.51,
                    "trailingAnnualDividendYield": 0.00020303538,
                }

        with patch("app.yf.Ticker", side_effect=FakeTicker):
            yields = _latest_dividend_yields(("NVDA",))

        self.assertAlmostEqual(yields["NVDA"], 0.0203, places=4)

    def test_positive_dividend_yield_falls_back_when_trailing_value_is_zero(self) -> None:
        class FakeTicker:
            def __init__(self, ticker: str) -> None:
                self.info = {
                    "dividendYield": 8.11,
                    "trailingAnnualDividendYield": 0.0,
                }

        with patch("app.yf.Ticker", side_effect=FakeTicker):
            yields = _latest_dividend_yields(("JEPI",))

        self.assertAlmostEqual(yields["JEPI"], 8.11, places=4)

    def test_direct_classification_and_look_through_exposure_split_fund_holdings(self) -> None:
        holdings = pd.DataFrame(
            [{"ticker": "JEPI", "shares": 10, "amount_invested": 1000, "annual_div_per_share": 0.0, "sector": "", "asset_type": "ETF"}]
        )
        metrics = pd.DataFrame(
            [{"ticker": "JEPI", "current_price": 100.0, "market_value": 1000.0, "annual_income": 8.0, "portfolio_yield_pct": 0.8, "cost_basis": 1000.0, "unrealized_gain": 0.0, "sector": "", "asset_type": "ETF"}]
        )

        fake_ticker = SimpleNamespace(
            funds_data=SimpleNamespace(
                sector_weightings={"technology": 0.25, "financial_services": 0.75}
            )
        )

        with patch("app.yf.Ticker", return_value=fake_ticker):
            payload = json.loads(_build_ai_export_json(holdings, metrics))

        self.assertEqual(payload["direct_sector_exposure"], [])
        self.assertEqual(payload["direct_holdings_by_classification"][0]["classification"], "ETFs")
        look_through_sectors = [entry["sector"] for entry in payload["look_through_sector_exposure"]]
        self.assertIn("Technology", look_through_sectors)
        self.assertIn("Financial Services", look_through_sectors)
        self.assertEqual(payload["unresolved_fund_look_through_exposure"], [])

    def test_direct_sector_exposure_includes_both_portfolio_and_resolved_weights(self) -> None:
        metrics = pd.DataFrame(
            [
                {"ticker": "HD", "market_value": 600.0, "annual_income": 10.0, "cost_basis": 500.0, "unrealized_gain": 100.0, "portfolio_yield_pct": 1.67, "sector": "Consumer Cyclical", "asset_type": "Stock"},
                {"ticker": "O", "market_value": 300.0, "annual_income": 12.0, "cost_basis": 280.0, "unrealized_gain": 20.0, "portfolio_yield_pct": 4.0, "sector": "Real Estate", "asset_type": "REIT"},
                {"ticker": "JEPI", "market_value": 500.0, "annual_income": 25.0, "cost_basis": 490.0, "unrealized_gain": 10.0, "portfolio_yield_pct": 5.0, "sector": "", "asset_type": "ETF"},
            ]
        )

        records = _build_direct_sector_classification_records(metrics)
        consumer_cyclical = next(record for record in records if record["Sector"] == "Consumer Cyclical")

        self.assertAlmostEqual(consumer_cyclical["Portfolio_Weight_Pct"], 42.8571, places=3)
        self.assertAlmostEqual(consumer_cyclical["Pct_of_Resolved_Direct_Sector_Exposure"], 66.6667, places=3)

        payload = json.loads(_build_ai_export_json(pd.DataFrame(), metrics))
        consumer_cyclical_json = next(entry for entry in payload["direct_sector_exposure"] if entry["sector"] == "Consumer Cyclical")

        self.assertAlmostEqual(consumer_cyclical_json["portfolio_weight_pct"], 42.8571, places=3)
        self.assertAlmostEqual(consumer_cyclical_json["pct_of_resolved_direct_sector_exposure"], 66.6667, places=3)

    def test_data_completeness_distinguishes_direct_and_fund_coverage(self) -> None:
        metrics = pd.DataFrame(
            [
                {"ticker": "HD", "market_value": 600.0, "annual_income": 10.0, "cost_basis": 500.0, "unrealized_gain": 100.0, "portfolio_yield_pct": 1.67, "sector": "Consumer Cyclical", "asset_type": "Stock"},
                {"ticker": "UNK", "market_value": 200.0, "annual_income": 0.0, "cost_basis": 200.0, "unrealized_gain": 0.0, "portfolio_yield_pct": 0.0, "sector": "", "asset_type": "Stock"},
                {"ticker": "JEPI", "market_value": 500.0, "annual_income": 25.0, "cost_basis": 490.0, "unrealized_gain": 10.0, "portfolio_yield_pct": 5.0, "sector": "", "asset_type": "ETF"},
                {"ticker": "AGG", "market_value": 300.0, "annual_income": 10.0, "cost_basis": 300.0, "unrealized_gain": 0.0, "portfolio_yield_pct": 3.3, "sector": "", "asset_type": "Bond Fund"},
            ]
        )

        def fake_ticker_factory(symbol: str) -> SimpleNamespace:
            if symbol == "JEPI":
                return SimpleNamespace(funds_data=SimpleNamespace(sector_weightings={"technology": 1.0}))
            return SimpleNamespace(funds_data=SimpleNamespace(sector_weightings=None))

        with patch("app.yf.Ticker", side_effect=fake_ticker_factory):
            completeness = _build_data_completeness(metrics)
            payload = json.loads(_build_ai_export_json(pd.DataFrame(), metrics))

        self.assertAlmostEqual(completeness["direct_sector_classification_coverage_pct"], 75.0, places=3)
        self.assertAlmostEqual(completeness["fund_lookthrough_coverage_pct"], 62.5, places=3)
        self.assertAlmostEqual(completeness["overall_resolved_exposure_coverage_pct"], 68.75, places=3)

    def test_sdiv_is_not_classified_as_option_income_but_jepi_remains_option_income(self) -> None:
        holdings = pd.DataFrame(
            [
                {"ticker": "SDIV", "shares": 10, "amount_invested": 1000, "annual_div_per_share": 1.2, "sector": "Real Estate", "asset_type": "ETF"},
                {"ticker": "JEPI", "shares": 5, "amount_invested": 500, "annual_div_per_share": 2.4, "sector": "Financial Services", "asset_type": "ETF"},
            ]
        )
        metrics = pd.DataFrame(
            [
                {"ticker": "SDIV", "current_price": 100.0, "market_value": 1000.0, "annual_income": 12.0, "portfolio_yield_pct": 1.2, "cost_basis": 1000.0, "unrealized_gain": 0.0, "sector": "Real Estate", "asset_type": "ETF", "dividend_yield_pct": 12.0},
                {"ticker": "JEPI", "current_price": 100.0, "market_value": 500.0, "annual_income": 12.0, "portfolio_yield_pct": 2.4, "cost_basis": 500.0, "unrealized_gain": 0.0, "sector": "Financial Services", "asset_type": "ETF", "dividend_yield_pct": 12.0},
            ]
        )

        payload = json.loads(_build_ai_export_json(holdings, metrics))
        classification_map = {item["ticker"]: item["income_quality_classification"] for item in payload["income_quality_classification"]}
        holding_classifications = payload["portfolio_health"]["income_quality"]["holding_classifications"]

        self.assertNotEqual(classification_map["SDIV"], "option-income ETF distribution")
        self.assertEqual(classification_map["JEPI"], "Option-income ETF distribution")
        self.assertEqual(holding_classifications["SDIV"]["income_type"], "Variable / Unstable Distribution")
        self.assertEqual(holding_classifications["JEPI"]["income_type"], "Option-Income ETF Distribution")

    def test_concentration_context_uses_largest_holdings_own_income_share(self) -> None:
        holdings = pd.DataFrame(
            [
                {"ticker": "HD", "shares": 10, "amount_invested": 1000, "annual_div_per_share": 1.0, "sector": "Consumer Cyclical", "asset_type": "Stock"},
                {"ticker": "JEPI", "shares": 5, "amount_invested": 500, "annual_div_per_share": 1.0, "sector": "Financial Services", "asset_type": "ETF"},
                {"ticker": "O", "shares": 2, "amount_invested": 200, "annual_div_per_share": 1.0, "sector": "Real Estate", "asset_type": "REIT"},
            ]
        )
        metrics = pd.DataFrame(
            [
                {"ticker": "HD", "current_price": 100.0, "market_value": 1000.0, "annual_income": 10.0, "portfolio_yield_pct": 1.0, "cost_basis": 1000.0, "unrealized_gain": 0.0, "sector": "Consumer Cyclical", "asset_type": "Stock"},
                {"ticker": "JEPI", "current_price": 100.0, "market_value": 500.0, "annual_income": 20.0, "portfolio_yield_pct": 4.0, "cost_basis": 500.0, "unrealized_gain": 0.0, "sector": "Financial Services", "asset_type": "ETF"},
                {"ticker": "O", "current_price": 100.0, "market_value": 100.0, "annual_income": 5.0, "portfolio_yield_pct": 5.0, "cost_basis": 100.0, "unrealized_gain": 0.0, "sector": "Real Estate", "asset_type": "REIT"},
            ]
        )

        payload = json.loads(_build_ai_export_json(holdings, metrics))
        markdown = _build_export_markdown(holdings, metrics)

        self.assertAlmostEqual(payload["concentration_context"]["largest_holding_income_weight_pct"], 28.5714, places=3)
        self.assertAlmostEqual(payload["concentration_context"]["largest_income_weight_pct"], 57.1429, places=3)
        self.assertIn("That same holding contributes 28.57% of estimated annual income.", markdown)
        self.assertIn("Largest income contributor: JEPI (57.14% of estimated annual income).", markdown)

    def test_sector_normalization_merges_realestate_labels_in_direct_and_lookthrough_exports(self) -> None:
        holdings = pd.DataFrame(
            [
                {"ticker": "HD", "shares": 1, "amount_invested": 100, "annual_div_per_share": 1.0, "sector": "Realestate", "asset_type": "Stock"},
                {"ticker": "O", "shares": 1, "amount_invested": 200, "annual_div_per_share": 1.0, "sector": "Real Estate", "asset_type": "REIT"},
                {"ticker": "REET", "shares": 1, "amount_invested": 300, "annual_div_per_share": 0.0, "sector": "", "asset_type": "ETF"},
            ]
        )
        metrics = pd.DataFrame(
            [
                {"ticker": "HD", "current_price": 100.0, "market_value": 100.0, "annual_income": 5.0, "portfolio_yield_pct": 5.0, "cost_basis": 100.0, "unrealized_gain": 0.0, "sector": "Realestate", "asset_type": "Stock"},
                {"ticker": "O", "current_price": 200.0, "market_value": 200.0, "annual_income": 10.0, "portfolio_yield_pct": 5.0, "cost_basis": 200.0, "unrealized_gain": 0.0, "sector": "Real Estate", "asset_type": "REIT"},
                {"ticker": "REET", "current_price": 300.0, "market_value": 300.0, "annual_income": 12.0, "portfolio_yield_pct": 4.0, "cost_basis": 300.0, "unrealized_gain": 0.0, "sector": "", "asset_type": "ETF"},
            ]
        )
        with patch("app._latest_fund_sector_weights", return_value={"REET": {"realestate": 1.0}}):
            payload = json.loads(_build_ai_export_json(holdings, metrics))

        direct_real_estate = next(item for item in payload["direct_sector_exposure"] if item["sector"] == "Real Estate")

        self.assertEqual(len([item for item in payload["direct_sector_exposure"] if item["sector"] == "Real Estate"]), 1)
        self.assertAlmostEqual(direct_real_estate["market_value"], 300.0, places=3)
        self.assertEqual(payload["look_through_sector_exposure"][0]["sector"], "Real Estate")

    @patch("app._latest_historical_context")
    def test_export_sanitizes_non_finite_values_in_json_and_markdown(self, historical_context_mock) -> None:
        historical_context_mock.return_value = {
            "SDIV": {
                "ticker": "SDIV",
                "price_trend": {"change_3m_pct": float("nan")},
                "dividend_trend": {"change_1y_pct": float("inf")},
                "start_price": float("nan"),
                "end_price": float("inf"),
                "return_pct": float("-inf"),
                "52w_high": float("nan"),
                "52w_low": float("inf"),
                "drawdown": float("-inf"),
                "volatility": float("nan"),
                "history_summary": "Historical context unavailable.",
            }
        }
        holdings = pd.DataFrame([{"ticker": "SDIV", "shares": 1, "amount_invested": 100, "annual_div_per_share": 1.0, "sector": "Real Estate", "asset_type": "ETF"}])
        metrics = pd.DataFrame([{"ticker": "SDIV", "current_price": 100.0, "market_value": 100.0, "annual_income": 10.0, "portfolio_yield_pct": 10.0, "cost_basis": 100.0, "unrealized_gain": 0.0, "sector": "Real Estate", "asset_type": "ETF"}])

        payload = json.loads(_build_ai_export_json(holdings, metrics))
        markdown = _build_export_markdown(holdings, metrics)

        self.assertIsNone(payload["historical_context"][0]["start_price"])
        self.assertIsNone(payload["historical_context"][0]["end_price"])
        self.assertIsNone(payload["historical_context"][0]["return_pct"])
        self.assertNotRegex(markdown, r"\b(?:nan|NaN|Infinity|-Infinity)\b")
        self.assertNotRegex(json.dumps(payload), r"\b(?:nan|NaN|Infinity|-Infinity)\b")
        self.assertIn("start_price Unavailable", markdown)
        self.assertIn("return_pct Unavailable", markdown)

    def test_health_score_sections_match_and_concentration_alert_is_not_duplicated(self) -> None:
        holdings = pd.DataFrame(
            [
                {"ticker": "HD", "shares": 10, "amount_invested": 1000, "annual_div_per_share": 1.0, "sector": "Consumer Cyclical", "asset_type": "Stock"},
                {"ticker": "JEPI", "shares": 1, "amount_invested": 10, "annual_div_per_share": 1.0, "sector": "Financial Services", "asset_type": "ETF"},
            ]
        )
        metrics = pd.DataFrame(
            [
                {"ticker": "HD", "current_price": 100.0, "market_value": 1000.0, "annual_income": 10.0, "portfolio_yield_pct": 1.0, "cost_basis": 1000.0, "unrealized_gain": 0.0, "sector": "Consumer Cyclical", "asset_type": "Stock", "dividend_yield_pct": 1.0},
                {"ticker": "JEPI", "current_price": 10.0, "market_value": 10.0, "annual_income": 11.0, "portfolio_yield_pct": 110.0, "cost_basis": 10.0, "unrealized_gain": 0.0, "sector": "Financial Services", "asset_type": "ETF", "dividend_yield_pct": 11.0},
            ]
        )

        payload = json.loads(_build_ai_export_json(holdings, metrics))
        markdown = _build_export_markdown(holdings, metrics)
        score = payload["portfolio_health"]["health_score"]

        self.assertEqual(payload["health_score"]["health_score"], score)
        self.assertIn(f"Tracker Health Score: {score:.1f}/100", markdown)
        self.assertIn(f"- Health score: {score:.1f}/100", markdown)
        self.assertEqual(markdown.count("Concentration alert: HD is 99.0% of portfolio."), 1)
        self.assertLess(markdown.index("Concentration alert: HD is 99.0% of portfolio."), markdown.index("High-yield alert: one or more holdings exceed 10% yield."))

    @patch("app._latest_historical_context")
    def test_missing_historical_values_render_as_unavailable_not_zero(self, historical_context_mock) -> None:
        historical_context_mock.return_value = {
            "AAPL": {
                "ticker": "AAPL",
                "price_trend": {},
                "dividend_trend": {},
                "start_price": None,
                "end_price": None,
                "return_pct": None,
                "52w_high": None,
                "52w_low": None,
                "drawdown": None,
                "volatility": None,
                "history_summary": "Historical context unavailable.",
            }
        }
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100, "annual_div_per_share": 1.0, "sector": "Technology", "asset_type": "Stock"}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "current_price": 100.0, "market_value": 100.0, "annual_income": 10.0, "portfolio_yield_pct": 10.0, "cost_basis": 100.0, "unrealized_gain": 0.0, "sector": "Technology", "asset_type": "Stock"}])

        payload = json.loads(_build_ai_export_json(holdings, metrics))
        markdown = _build_export_markdown(holdings, metrics)

        self.assertIsNone(payload["historical_context"][0]["return_pct"])
        self.assertIn("return_pct Unavailable", markdown)
        self.assertNotIn("return_pct 0.00%", markdown)

    def test_unresolved_fund_look_through_exposure_is_grouped_by_fund_type(self) -> None:
        holdings = pd.DataFrame(
            [
                {"ticker": "IEFA", "shares": 10, "amount_invested": 1000, "annual_div_per_share": 0.0, "sector": "", "asset_type": "ETF"},
                {"ticker": "AGG", "shares": 10, "amount_invested": 500, "annual_div_per_share": 0.0, "sector": "", "asset_type": "Bond Fund"},
            ]
        )
        metrics = pd.DataFrame(
            [
                {"ticker": "IEFA", "current_price": 100.0, "market_value": 1000.0, "annual_income": 8.0, "portfolio_yield_pct": 0.8, "cost_basis": 1000.0, "unrealized_gain": 0.0, "sector": "", "asset_type": "ETF"},
                {"ticker": "AGG", "current_price": 50.0, "market_value": 500.0, "annual_income": 4.0, "portfolio_yield_pct": 0.8, "cost_basis": 500.0, "unrealized_gain": 0.0, "sector": "", "asset_type": "Bond Fund"},
            ]
        )

        fake_ticker = SimpleNamespace(funds_data=SimpleNamespace(sector_weightings=None))

        with patch("app.yf.Ticker", return_value=fake_ticker):
            payload = json.loads(_build_ai_export_json(holdings, metrics))

        unresolved = payload["unresolved_fund_look_through_exposure"]
        fund_types = [entry["fund_type"] for entry in unresolved]
        self.assertIn("ETFs", fund_types)
        self.assertIn("Bond Funds", fund_types)
        self.assertEqual(payload["look_through_sector_exposure"], [])
        self.assertTrue(
            any(
                "Look-through sector exposure is incomplete where Yahoo Finance does not provide underlying fund sector weights."
                in issue
                for issue in payload["data_quality_issues"]
            )
        )


if __name__ == "__main__":
    unittest.main()

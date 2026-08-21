import os
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

import app
from ai_analysis_prompt import AI_ANALYSIS_TYPES
from nicegui_app.services.ai_context import AIAnalysisState, ai_options, build_ai_context, compact_context
from nicegui_app.services.exports import build_export_payload
from onemin_ai_client import OneMinAIConfigError, OneMinAIUnsupportedModelError
from portfolio_ai import ONEMIN_AI_RECOMMENDED_MODELS, build_ai_request, compact_ai_context, run_ai_analysis


class NiceGuiPhase1GAITests(unittest.TestCase):
    def setUp(self) -> None:
        self.holdings = [
            {"ticker": "HD", "shares": 4.0, "current_price": 250.0, "amount_invested": 800.0, "annual_div_per_share": 8.0, "dividend_yield_pct": 3.2, "asset_type": "Stock", "sector": "Consumer Cyclical", "regular_biweekly_contribution": 0.0, "espp_biweekly_contribution": 100.0, "special_contribution": 0.0, "employer_stock": True, "espp_enabled": True, "espp_acquired_shares": 2.0},
            {"ticker": "ABC", "shares": 10.0, "current_price": 50.0, "amount_invested": 400.0, "annual_div_per_share": 2.0, "dividend_yield_pct": 4.0, "asset_type": "Stock", "sector": "Technology", "regular_biweekly_contribution": 280.0, "espp_biweekly_contribution": 0.0, "special_contribution": 500.0, "employer_stock": False, "espp_enabled": False, "espp_acquired_shares": 0.0},
        ]
        self.metrics = pd.DataFrame(self.holdings)
        self.metrics["market_value"] = self.metrics["shares"] * self.metrics["current_price"]
        self.metrics["cost_basis"] = self.metrics["amount_invested"]
        self.metrics["unrealized_gain"] = self.metrics["market_value"] - self.metrics["cost_basis"]
        self.metrics["annual_income"] = self.metrics["shares"] * self.metrics["annual_div_per_share"]
        self.metrics["portfolio_weight_pct"] = self.metrics["market_value"] / self.metrics["market_value"].sum() * 100
        self.metrics["portfolio_yield_pct"] = self.metrics["annual_income"] / self.metrics["market_value"] * 100
        self.history = [{"history_date": "2025-01-01", "portfolio_value": 1000}, {"history_date": "2026-01-01", "portfolio_value": 1500}]

    def payload(self):
        return build_export_payload(self.holdings, self.metrics, history=self.history, external_context={"investor_profile": {"primary_goal": "Income growth", "risk_tolerance": "Moderate", "investment_horizon_years": 20}, "recommendation_constraints": {"avoid_forced_selling": True}, "supplemental_investment_intelligence": [{"ticker": "HD", "fundamental_metrics": {"trailing_pe": 20}}], "historical_security_context": [{"ticker": "HD", "return_pct": 8, "drawdown": -10}], "dividend_seasonality": {}, "yahoo_metadata": []})

    def test_wrapper_matches_shared_context_and_contains_required_context(self) -> None:
        payload = self.payload()
        expected = compact_ai_context(payload, "Comprehensive Portfolio Review", "")
        actual = compact_context(payload, "Comprehensive Portfolio Review", "", self.history)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["portfolio_snapshot"]["portfolio_value"], 1500.0)
        self.assertEqual({row["ticker"] for row in actual["calculated_metrics"]}, {"HD", "ABC"})
        summary = actual["contribution_context"]["summary"]
        self.assertEqual(summary["annual_regular_contributions"], 7280.0)
        self.assertEqual(summary["annual_espp_contributions"], 2600.0)
        self.assertEqual(summary["estimated_combined_annual_contributions"], 9880.0)
        self.assertEqual(summary["special_contribution"], 500.0)
        self.assertEqual(summary["hd_espp_discount_pct"], 15.0)
        self.assertEqual(actual["contribution_context"]["employer_stock_concentration_context"]["primary_employer_ticker"], "HD")
        self.assertIn("health_score_breakdown", actual["portfolio_health"])
        self.assertIn("income_quality_summary", actual)
        self.assertIn("data_completeness", actual)
        self.assertIn("portfolio_history_summary", actual)
        self.assertEqual(actual["investor_profile"]["primary_goal"], "Income growth")
        self.assertTrue(actual["recommendation_constraints"]["avoid_forced_selling"])

    def test_streamlit_and_shared_compaction_and_provider_prompt_match(self) -> None:
        payload = self.payload()
        self.assertEqual(app._compact_analysis_context(payload, "Risk Analysis", "Focus on HD"), compact_ai_context(payload, "Risk Analysis", "Focus on HD"))
        streamlit_prompt = build_ai_request(payload, "Risk Analysis", "Focus on HD")[0]["model_prompt"]
        nicegui_prompt = build_ai_request(payload, "Risk Analysis", "Focus on HD")[0]["model_prompt"]
        self.assertEqual(streamlit_prompt, nicegui_prompt)
        self.assertIn("employer stock acquired through an ESPP", streamlit_prompt)
        self.assertIn("Regular biweekly contributions", streamlit_prompt)

    def test_canonical_income_quality_and_concentration_keys_survive(self) -> None:
        payload = copy.deepcopy(self.payload())
        payload["holdings"].extend([
            {"ticker": "JEPI", "market_value": 300.0, "annual_income": 24.0, "asset_type": "ETF"},
            {"ticker": "SDIV", "market_value": 200.0, "annual_income": 20.0, "asset_type": "ETF"},
        ])
        payload["income"]["quality"] = [
            {"ticker": "JEPI", "income_quality_classification": "Option Income", "option_income": True},
            {"ticker": "SDIV", "income_quality_classification": "High Yield", "option_income": False},
        ]
        payload["concentration"]["largest_income_contributor"] = "HD"
        payload["concentration"]["largest_income_weight_pct"] = 61.25
        payload["concentration"]["largest_income_weight"] = 9.0
        hd_employer = payload["contributions"]["employer_stock_context"][0]
        hd_employer["capital_concentration_pct"] = 66.67
        hd_employer["portfolio_weight_pct"] = 12.0

        compact = compact_ai_context(payload, "Comprehensive Portfolio Review", "")
        holdings = {row["ticker"]: row for row in compact["calculated_metrics"]}
        self.assertEqual(holdings["JEPI"]["income_quality_classification"], "Option Income")
        self.assertTrue(holdings["JEPI"]["option_income"])
        self.assertEqual(holdings["SDIV"]["income_quality_classification"], "High Yield")
        self.assertFalse(holdings["SDIV"]["option_income"])
        self.assertEqual(compact["concentration_context"]["largest_income_weight_pct"], 61.25)
        employer = compact["contribution_context"]["employer_stock_concentration_context"]
        self.assertEqual(employer["primary_employer_ticker"], "HD")
        self.assertEqual(employer["primary_employer_capital_concentration_pct"], 66.67)
        summary = compact["contribution_context"]["summary"]
        self.assertEqual(summary["annual_espp_contributions"], 2600.0)
        self.assertEqual(summary["hd_espp_discount_pct"], 15.0)

        prompt = build_ai_request(payload, "Comprehensive Portfolio Review", "")[0]["model_prompt"]
        self.assertIn("Option Income", prompt)
        self.assertIn("High Yield", prompt)
        self.assertIn("61.25%", prompt)
        self.assertIn("66.67%", prompt)
        self.assertEqual(app._compact_analysis_context(payload, "Comprehensive Portfolio Review", ""), compact)

    def test_legacy_production_shape_fallbacks_remain_supported(self) -> None:
        legacy = {
            "portfolio_snapshot": {"portfolio_value": 1000.0},
            "calculated_metrics": [
                {"ticker": "JEPI", "market_value": 600.0, "income_quality_classification": "Option Income", "option_income": True},
                {"ticker": "SDIV", "market_value": 400.0, "income_quality_classification": "High Yield", "option_income": False},
            ],
            "income_quality": {"income_quality_score": 72.0},
            "concentration_context": {"largest_income_contributor": "JEPI", "largest_income_weight_pct": 60.0},
            "contribution_context": {
                "summary": {"estimated_annual_espp_contributions": 2600.0, "hd_espp_discount_pct": 15.0},
                "employer_stock_concentration_context": {"primary_employer_ticker": "HD", "primary_employer_capital_concentration_pct": 40.0},
            },
        }
        compact = compact_ai_context(legacy, "Comprehensive Portfolio Review", "")
        self.assertEqual(compact["income_quality_summary"]["income_quality_score"], 72.0)
        self.assertEqual(compact["concentration_context"]["largest_income_weight_pct"], 60.0)
        self.assertEqual(compact["contribution_context"]["employer_stock_concentration_context"]["primary_employer_capital_concentration_pct"], 40.0)
        self.assertEqual({row["ticker"] for row in compact["calculated_metrics"]}, {"JEPI", "SDIV"})

    def test_analysis_types_and_model_configuration_match_production(self) -> None:
        with patch.dict(os.environ, {"ONEMIN_AI_MODEL": "custom-model", "ONEMIN_AI_API_KEY": "key"}, clear=False):
            options = ai_options()
        self.assertEqual(options["analysis_types"], AI_ANALYSIS_TYPES)
        self.assertEqual(options["models"], ["custom-model", *ONEMIN_AI_RECOMMENDED_MODELS])
        self.assertEqual(options["default_model"], "custom-model")
        self.assertTrue(options["configured"])

    def test_success_failure_missing_key_invalid_model_and_duplicate_guard(self) -> None:
        payload = self.payload()
        client = Mock()
        client.chat_with_ai.return_value = SimpleNamespace(content="# Result", model="gpt-4.1-mini", record_id="r1", response_diagnostic={"record_id": "r1"})
        factory = Mock(return_value=client)
        with patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "key"}, clear=False):
            state = run_ai_analysis(payload, AI_ANALYSIS_TYPES[0], "gpt-4.1-mini", "", client_factory=factory)
        self.assertEqual(state.result_markdown, "# Result")
        self.assertEqual(state.last_stage, "success")
        self.assertEqual(client.chat_with_ai.call_args.kwargs["model"], "gpt-4.1-mini")
        self.assertNotIn("web_search", client.chat_with_ai.call_args.kwargs)

        previous = state.result_markdown
        failing_factory = Mock(side_effect=OneMinAIUnsupportedModelError("bad model"))
        with patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "key"}, clear=False):
            run_ai_analysis(payload, AI_ANALYSIS_TYPES[0], "bad", "", state=state, client_factory=failing_factory)
        self.assertEqual(state.result_markdown, previous)
        self.assertIn("not supported", state.error_message)

        missing_factory = Mock(side_effect=OneMinAIConfigError("missing key"))
        with patch.dict(os.environ, {"ONEMIN_AI_API_KEY": ""}, clear=False):
            run_ai_analysis(payload, AI_ANALYSIS_TYPES[0], "gpt-4.1-mini", "", state=state, client_factory=missing_factory)
        self.assertIn("ONEMIN_AI_API_KEY", state.error_message)
        state.busy = True; state.last_stage = "requesting"
        guarded_factory = Mock()
        run_ai_analysis(payload, AI_ANALYSIS_TYPES[0], "gpt-4.1-mini", "", state=state, client_factory=guarded_factory)
        guarded_factory.assert_not_called()

    def test_clear_and_explicit_only_read_only_source_contract(self) -> None:
        state = AIAnalysisState(result_markdown="old", error_message="error", busy=True, last_stage="success", context_diagnostics={"after_chars": 2})
        state.clear()
        self.assertEqual(state, AIAnalysisState())
        main_source = (Path(__file__).resolve().parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('ui.button("Analyze", on_click=analyze', main_source)
        self.assertIn('if request_active["value"]', main_source)
        self.assertNotIn("run_ai_analysis(holdings, metrics", main_source.split("async def analyze()", 1)[0])
        self.assertNotIn("INSERT ", main_source.upper())
        self.assertNotIn("UPDATE ", main_source.upper())


if __name__ == "__main__":
    unittest.main()

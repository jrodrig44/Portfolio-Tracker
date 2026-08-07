import contextlib
import importlib
import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

import app
from ai_analysis_prompt import AI_ANALYSIS_TYPES, build_ai_analysis_markdown, build_ai_analysis_messages
from tokenrouter_client import (
    TOKENROUTER_DEFAULT_CONNECT_TIMEOUT_SECONDS,
    TOKENROUTER_DEFAULT_BASE_URL,
    TOKENROUTER_DEFAULT_MODEL,
    TOKENROUTER_TIMEOUT_SECONDS,
    TOKENROUTER_MODEL_IDS,
    TokenRouterAuthenticationError,
    TokenRouterClient,
    TokenRouterConfigError,
    TokenRouterConnectionError,
    TokenRouterInsufficientBalanceError,
    TokenRouterRateLimitError,
    TokenRouterResponseError,
    TokenRouterTimeoutError,
    TokenRouterUnsupportedModelError,
)


def _sample_full_ai_payload() -> dict[str, object]:
    return {
        "portfolio_snapshot": {
            "portfolio_value": 25000.0,
            "cost_basis": 20000.0,
            "unrealized_gain": 5000.0,
            "annual_income": 900.0,
            "weighted_yield_pct": 3.6,
            "holdings_count": 3,
            "top_holding": "HD",
            "top_holding_weight_pct": 35.0,
            "projection_contribution_note": "Uses regular and ESPP contributions.",
            "projected_10y_value": 150000.0,
            "projected_20y_value": 450000.0,
        },
        "investor_profile": {
            "portfolio_strategy": "Dividend growth and income",
            "primary_goal": "Long-term compounding",
            "risk_tolerance": "Moderate",
            "investment_horizon_years": 20,
            "contribution_frequency": "Biweekly",
            "contribution_amount": 280.0,
        },
        "portfolio_health": {
            "health_score_breakdown": {
                "diversification": 75,
                "concentration": 61,
                "income_stability": 70,
                "dividend_growth_potential": 74,
                "total_return_potential": 72,
                "risk": 59,
                "data_quality": 88,
            },
            "context_summary": {
                "score": 72,
                "score_label": "Tracker-generated score",
                "strongest_areas": [{"area": "Data Quality", "score": 88}],
                "weakest_areas": [{"area": "Concentration", "score": 61}],
                "primary_alert": "Concentration alert",
            },
        },
        "concentration_context": {
            "largest_holding_by_value": "HD",
            "largest_holding_value_weight_pct": 35.0,
            "largest_holding_income_weight_pct": 18.0,
            "largest_income_contributor": "JEPI",
            "largest_income_weight_pct": 30.0,
            "guidance": "Evaluate capital and income concentration separately.",
        },
        "contribution_context": {
            "summary": {
                "total_regular_biweekly_contribution": 280.0,
                "total_espp_biweekly_contributions": 100.0,
                "estimated_combined_annual_contributions": 9880.0,
                "hd_espp_contribution_per_paycheck": 100.0,
                "hd_espp_purchase_frequency": "6_months",
                "hd_espp_discount_pct": 15.0,
            },
            "employer_stock_concentration_context": {
                "primary_employer_ticker": "HD",
                "primary_employer_capital_concentration_pct": 35.0,
                "primary_employer_income_concentration_pct": 18.0,
                "is_concentration_alert": True,
            },
        },
        "calculated_metrics": [
            {
                "ticker": "HD",
                "shares": 20,
                "market_value": 8750.0,
                "portfolio_weight_pct": 35.0,
                "cost_basis": 6800.0,
                "unrealized_gain": 1950.0,
                "annual_income": 160.0,
                "portfolio_yield_pct": 1.8,
                "dividend_yield_pct": 2.0,
                "regular_biweekly_contribution": 0.0,
                "espp_biweekly_contribution": 100.0,
                "total_contribution": 100.0,
                "sector": "Consumer Cyclical",
                "asset_type": "Stock",
                "employer_stock": True,
                "espp_enabled": True,
                "internal_display_only": "x",
            },
            {
                "ticker": "JEPI",
                "shares": 120,
                "market_value": 7000.0,
                "portfolio_weight_pct": 28.0,
                "cost_basis": 6200.0,
                "unrealized_gain": 800.0,
                "annual_income": 450.0,
                "portfolio_yield_pct": 6.4,
                "dividend_yield_pct": 7.0,
                "regular_biweekly_contribution": 140.0,
                "espp_biweekly_contribution": 0.0,
                "total_contribution": 140.0,
                "sector": "ETF",
                "asset_type": "ETF",
                "employer_stock": False,
                "espp_enabled": False,
            },
            {
                "ticker": "SCHD",
                "shares": 60,
                "market_value": 5000.0,
                "portfolio_weight_pct": 20.0,
                "cost_basis": 4800.0,
                "unrealized_gain": 200.0,
                "annual_income": 290.0,
                "portfolio_yield_pct": 5.8,
                "dividend_yield_pct": 3.5,
                "regular_biweekly_contribution": 140.0,
                "espp_biweekly_contribution": 0.0,
                "total_contribution": 140.0,
                "sector": "ETF",
                "asset_type": "ETF",
                "employer_stock": False,
                "espp_enabled": False,
            },
        ],
        "income_allocation_by_holding": [
            {"ticker": "JEPI", "annual_income": 450.0, "market_value": 7000.0, "income_allocation_pct": 50.0},
            {"ticker": "SCHD", "annual_income": 290.0, "market_value": 5000.0, "income_allocation_pct": 32.2},
            {"ticker": "HD", "annual_income": 160.0, "market_value": 8750.0, "income_allocation_pct": 17.8},
        ],
        "asset_allocation": [
            {"asset_type": "Stock", "market_value": 8750.0, "annual_income": 160.0, "portfolio_weight_pct": 35.0},
            {"asset_type": "ETF", "market_value": 12000.0, "annual_income": 740.0, "portfolio_weight_pct": 48.0},
        ],
        "direct_sector_exposure": [
            {"sector": "Consumer Cyclical", "market_value": 8750.0, "annual_income": 160.0, "portfolio_weight_pct": 35.0},
            {"sector": "Financial Services", "market_value": 7000.0, "annual_income": 450.0, "portfolio_weight_pct": 28.0},
        ],
        "look_through_sector_exposure": [
            {"sector": "Financial Services", "source_holdings": "JEPI", "market_value": 2200.0, "portfolio_weight_pct": 8.8},
            {"sector": "Technology", "source_holdings": "SCHD", "market_value": 1800.0, "portfolio_weight_pct": 7.2},
        ],
        "supplemental_yahoo_financial_data": [
            {
                "ticker": "HD",
                "fundamental_metrics": {"dividend_yield": 0.020, "payout_ratio": 0.55, "trailing_pe": 25.1, "raw_api": {"too": "large"}},
                "earnings_and_estimates": {"earnings_growth": 0.08, "revenue_growth": 0.05},
                "analyst_targets": {"recommendation_key": "buy", "target_mean_price": 430.0},
                "fund_data": {"sector_weight_summary": [{"sector": "Consumer", "weight_pct": 100.0}], "metadata": {"raw": "value"}},
                "data_source": "Yahoo Finance",
            }
        ],
        "historical_context": [
            {"ticker": "HD", "return_pct": 20.5, "drawdown": -11.2, "volatility": 19.0, "history_summary": "Recovered after pullback."},
        ],
        "data_completeness": {
            "direct_sector_classification_coverage_pct": 95.0,
            "fund_lookthrough_coverage_pct": 82.0,
            "overall_resolved_exposure_coverage_pct": 88.0,
        },
        "unresolved_exposure": {
            "unresolved_fund_exposure": 1250.0,
            "resolved_lookthrough_value": 10750.0,
            "fund_total_value": 12000.0,
        },
        "tracker_health_score_metadata": {
            "income_stability_explanation": "Income quality framework note.",
            "data_quality_explanation": "Data quality framework note.",
            "risk_explanation": "Risk framework note.",
        },
        "warnings": {
            "snapshot_validation_errors": ["test validation warning"],
            "missing_export_fields": ["field_a"],
        },
        "data_quality_issues": ["missing sector on one ETF"],
        "unified_snapshot": {"raw": "X" * 20000},
        "statement_history": {"raw": "Y" * 12000},
        "supplemental_yahoo_historical_price_data": [{"raw": "Z" * 15000}],
        "holdings": [{"raw": "display-only duplicate"}],
    }


class TokenRouterClientTests(unittest.TestCase):
    class _FakeSDKObject:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def model_dump(self) -> dict[str, object]:
            return self._payload

    def _fake_sdk_client(self, create_result_or_side_effect: object, *, side_effect: bool = False):
        create_mock = MagicMock(side_effect=create_result_or_side_effect) if side_effect else MagicMock(return_value=create_result_or_side_effect)
        completions = SimpleNamespace(create=create_mock)
        chat = SimpleNamespace(completions=completions)
        return SimpleNamespace(chat=chat), create_mock

    def test_default_base_url_points_to_tokenrouter_com(self) -> None:
        self.assertEqual(TOKENROUTER_DEFAULT_BASE_URL, "https://api.tokenrouter.com/v1")

    def test_default_timeout_is_180_seconds(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        self.assertEqual(client.timeout_seconds, 180.0)

    def test_chat_completions_sends_bearer_header_and_keeps_key_out_of_payload(self) -> None:
        response_payload = self._FakeSDKObject(
            {
                "model": TOKENROUTER_DEFAULT_MODEL,
                "choices": [{"message": {"content": "# Analysis"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        )
        sdk_client, create_mock = self._fake_sdk_client(response_payload)

        client = TokenRouterClient(api_key="sk-test-key", base_url=TOKENROUTER_DEFAULT_BASE_URL, timeout_seconds=1.0)
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "# Analysis")
        self.assertEqual(result.model, TOKENROUTER_DEFAULT_MODEL)
        called_kwargs = create_mock.call_args.kwargs
        self.assertEqual(called_kwargs["model"], TOKENROUTER_DEFAULT_MODEL)
        self.assertEqual(called_kwargs["messages"][0]["content"], "Hello")
        self.assertFalse(called_kwargs["stream"])

    def test_chat_completions_parses_successful_response(self) -> None:
        response_payload = self._FakeSDKObject(
            {
                "model": TOKENROUTER_DEFAULT_MODEL,
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 28, "total_tokens": 34},
            }
        )
        sdk_client, _ = self._fake_sdk_client(response_payload)
        client = TokenRouterClient(api_key="sk-test-key")
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "OK")
        self.assertEqual(result.raw_response["usage"]["total_tokens"], 34)

    def test_missing_api_key_raises_config_error(self) -> None:
        client = TokenRouterClient(api_key="")

        with self.assertRaises(TokenRouterConfigError):
            client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_unsupported_model_is_rejected_locally(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")

        with self.assertRaises(TokenRouterUnsupportedModelError):
            client.chat_completions(model="not-a-real-model", messages=[{"role": "user", "content": "Hello"}])

    def test_authentication_error_is_mapped(self) -> None:
        class FakeAuthenticationError(Exception):
            pass

        sdk_client, _ = self._fake_sdk_client(FakeAuthenticationError("bad key"), side_effect=True)
        client = TokenRouterClient(api_key="sk-test-key")

        with patch("tokenrouter_client.AuthenticationError", FakeAuthenticationError), patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            with self.assertRaises(TokenRouterAuthenticationError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_balance_error_is_mapped(self) -> None:
        class FakeAPIStatusError(Exception):
            def __init__(self, message: str) -> None:
                super().__init__(message)
                self.status_code = 402
                self.response = SimpleNamespace(status_code=402, text='{"error":{"message":"insufficient credits"}}')

        sdk_client, _ = self._fake_sdk_client(FakeAPIStatusError("payment required"), side_effect=True)
        client = TokenRouterClient(api_key="sk-test-key")

        with patch("tokenrouter_client.APIStatusError", FakeAPIStatusError), patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            with self.assertRaises(TokenRouterInsufficientBalanceError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_rate_limit_error_is_mapped(self) -> None:
        class FakeRateLimitError(Exception):
            pass

        sdk_client, _ = self._fake_sdk_client(FakeRateLimitError("slow down"), side_effect=True)
        client = TokenRouterClient(api_key="sk-test-key")

        with patch("tokenrouter_client.RateLimitError", FakeRateLimitError), patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            with self.assertRaises(TokenRouterRateLimitError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_timeout_and_connection_errors_are_mapped(self) -> None:
        class FakeAPITimeoutError(Exception):
            pass

        class FakeAPIConnectionError(Exception):
            pass

        timeout_client, _ = self._fake_sdk_client(FakeAPITimeoutError("timed out"), side_effect=True)
        connection_client, _ = self._fake_sdk_client(FakeAPIConnectionError("connection refused"), side_effect=True)

        client = TokenRouterClient(api_key="sk-test-key")
        with patch("tokenrouter_client.APITimeoutError", FakeAPITimeoutError), patch.object(TokenRouterClient, "_openai_client", return_value=timeout_client):
            with self.assertRaises(TokenRouterTimeoutError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        with patch("tokenrouter_client.APIConnectionError", FakeAPIConnectionError), patch.object(TokenRouterClient, "_openai_client", return_value=connection_client):
            with self.assertRaises(TokenRouterConnectionError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_malformed_json_is_rejected(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        sdk_client, _ = self._fake_sdk_client(SimpleNamespace(model_dump=MagicMock(return_value=["not-a-dict"])))
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            with self.assertRaises(TokenRouterResponseError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_extracts_message_content_as_string(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        payload = {
            "model": TOKENROUTER_DEFAULT_MODEL,
            "choices": [{"message": {"role": "assistant", "content": "Final analysis text"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        }
        sdk_client, _ = self._fake_sdk_client(self._FakeSDKObject(payload))
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "Final analysis text")
        self.assertEqual(result.usage_metadata["prompt_tokens"], 100)
        self.assertEqual(result.usage_metadata["completion_tokens"], 200)
        self.assertEqual(result.usage_metadata["total_tokens"], 300)

    def test_extracts_message_content_as_content_blocks(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        payload = {
            "model": TOKENROUTER_DEFAULT_MODEL,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "First paragraph."},
                            {"type": "text", "text": "Second paragraph."},
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        sdk_client, _ = self._fake_sdk_client(self._FakeSDKObject(payload))
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        self.assertIn("First paragraph.", result.content)
        self.assertIn("Second paragraph.", result.content)

    def test_extracts_output_text_when_reasoning_and_final_are_split(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        payload = {
            "model": TOKENROUTER_DEFAULT_MODEL,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "reasoning_content": "Reasoning summary",
                        "content": None,
                        "output_text": "Final answer from output_text",
                    }
                }
            ],
            "usage": {"prompt_tokens": 55, "completion_tokens": 66, "total_tokens": 121},
            "cost": {"total": 0.0},
        }
        sdk_client, _ = self._fake_sdk_client(self._FakeSDKObject(payload))
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "Final answer from output_text")
        self.assertEqual(result.usage_metadata["estimated_cost"], 0.0)

    def test_empty_message_content_includes_sanitized_diagnostic(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        payload = {
            "model": TOKENROUTER_DEFAULT_MODEL,
            "choices": [{"message": {"role": "assistant", "content": ""}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        sdk_client, _ = self._fake_sdk_client(self._FakeSDKObject(payload))
        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            with self.assertRaises(TokenRouterResponseError) as ctx:
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        diagnostic = getattr(ctx.exception, "diagnostic", {})
        self.assertIn("http_status", diagnostic)
        self.assertIn("top_level_keys", diagnostic)
        self.assertIn("choices_count", diagnostic)

    def test_streaming_chunks_are_joined_in_order(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        chunks = iter(
            [
                self._FakeSDKObject({"model": TOKENROUTER_DEFAULT_MODEL, "choices": [{"delta": {"content": "Part A "}, "finish_reason": None}]}),
                self._FakeSDKObject({"model": TOKENROUTER_DEFAULT_MODEL, "choices": [{"delta": {"content": "Part B"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}}),
            ]
        )
        sdk_client, create_mock = self._fake_sdk_client(chunks)

        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(
                model=TOKENROUTER_DEFAULT_MODEL,
                messages=[{"role": "user", "content": "Hello"}],
                stream=True,
                stream_options={"include_usage": True},
            )

        self.assertEqual(result.content, "Part A Part B")
        self.assertEqual(result.usage_metadata["total_tokens"], 19)
        self.assertTrue(result.response_diagnostic["streaming"])
        self.assertEqual(result.response_diagnostic["chunks_received"], 2)
        called_kwargs = create_mock.call_args.kwargs
        self.assertTrue(called_kwargs["stream"])
        self.assertEqual(called_kwargs["stream_options"], {"include_usage": True})

    def test_streaming_ignores_empty_chunks_and_handles_usage_only_final_chunk(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        chunks = iter(
            [
                self._FakeSDKObject({"model": TOKENROUTER_DEFAULT_MODEL, "choices": [{"delta": {"content": ""}, "finish_reason": None}]}),
                self._FakeSDKObject({"model": TOKENROUTER_DEFAULT_MODEL, "choices": [{"delta": {"content": "Final"}, "finish_reason": "stop"}]}),
                self._FakeSDKObject({"model": TOKENROUTER_DEFAULT_MODEL, "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}}),
            ]
        )
        sdk_client, _ = self._fake_sdk_client(chunks)

        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            result = client.chat_completions(
                model=TOKENROUTER_DEFAULT_MODEL,
                messages=[{"role": "user", "content": "Hello"}],
                stream=True,
                stream_options={"include_usage": True},
            )

        self.assertEqual(result.content, "Final")
        self.assertEqual(result.usage_metadata["prompt_tokens"], 20)
        self.assertEqual(result.response_diagnostic["content_chunk_count"], 1)

    def test_empty_completed_stream_raises_useful_error(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")
        chunks = iter([self._FakeSDKObject({"model": TOKENROUTER_DEFAULT_MODEL, "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}]})])
        sdk_client, _ = self._fake_sdk_client(chunks)

        with patch.object(TokenRouterClient, "_openai_client", return_value=sdk_client):
            with self.assertRaises(TokenRouterResponseError) as ctx:
                client.chat_completions(
                    model=TOKENROUTER_DEFAULT_MODEL,
                    messages=[{"role": "user", "content": "Hello"}],
                    stream=True,
                    stream_options={"include_usage": True},
                )

        diagnostic = getattr(ctx.exception, "diagnostic", {})
        self.assertTrue(diagnostic.get("streaming"))
        self.assertIn("chunks_received", diagnostic)
        self.assertIn("final_content_chars", diagnostic)


class AiAnalysisPromptTests(unittest.TestCase):
    def test_prompt_includes_investor_profile_and_hd_espp_context(self) -> None:
        payload = {
            "portfolio_snapshot": {
                "portfolio_value": 10000.0,
                "cost_basis": 8000.0,
                "unrealized_gain": 2000.0,
                "annual_income": 320.0,
                "weighted_yield_pct": 3.2,
                "holdings_count": 2,
                "top_holding": "HD",
                "top_holding_weight_pct": 35.0,
                "projection_contribution_note": "Projection note",
                "projected_10y_value": 100000.0,
                "projected_20y_value": 250000.0,
            },
            "portfolio_health": {
                "health_score_breakdown": {
                    "diversification": 80,
                    "concentration": 65,
                    "income_stability": 70,
                    "dividend_growth_potential": 75,
                    "total_return_potential": 82,
                    "risk": 60,
                    "data_quality": 90,
                },
                "context_summary": {
                    "score": 78,
                    "score_label": "Tracker-generated score",
                    "strongest_areas": [{"area": "Data Quality", "score": 90}],
                    "weakest_areas": [{"area": "Concentration", "score": 65}],
                },
            },
            "concentration_context": {
                "largest_holding_by_value": "HD",
                "largest_holding_value_weight_pct": 35.0,
                "largest_holding_income_weight_pct": 20.0,
                "largest_income_contributor": "HD",
                "largest_income_weight_pct": 20.0,
                "guidance": "Capital concentration and income concentration should be evaluated separately.",
            },
            "contribution_context": {
                "summary": {
                    "total_regular_biweekly_contribution": 280.0,
                    "total_espp_biweekly_contributions": 100.0,
                    "estimated_combined_annual_contributions": 9880.0,
                    "hd_espp_contribution_per_paycheck": 100.0,
                    "hd_espp_purchase_frequency": "6_months",
                    "hd_espp_discount_pct": 15.0,
                },
                "employer_stock_concentration_context": {"is_concentration_alert": True},
            },
            "asset_allocation": [],
            "direct_sector_exposure": [],
            "direct_holdings_by_classification": [],
            "look_through_sector_exposure": [],
            "unresolved_fund_look_through_exposure": [],
            "income_allocation_by_holding": [],
            "income_allocation_by_asset_type": [],
            "economic_exposure_allocation": [],
            "calculated_metrics": [],
            "supplemental_yahoo_financial_data": [],
            "historical_context": [],
            "data_completeness": {
                "direct_sector_classification_coverage_pct": 100.0,
                "fund_lookthrough_coverage_pct": 100.0,
                "overall_resolved_exposure_coverage_pct": 100.0,
            },
            "unresolved_exposure": {
                "unresolved_fund_exposure": 0.0,
                "fund_total_value": 0.0,
            },
            "tracker_health_score_metadata": {
                "income_stability_explanation": "Income stability note",
                "data_quality_explanation": "Data quality note",
                "risk_explanation": "Risk note",
            },
            "warnings": {"snapshot_validation_errors": [], "missing_export_fields": []},
            "recommendation_context": {"decision_objective": "Test objective", "recommended_actions": []},
            "recommendation_constraints": {"avoid_forced_selling": True},
        }

        markdown = build_ai_analysis_markdown(payload, "Diversification and Concentration", "")
        messages = build_ai_analysis_messages(payload, "Diversification and Concentration", "Focus on concentration")

        self.assertIn("Analysis type: Diversification and Concentration", markdown)
        self.assertIn("The Home Depot ESPP", markdown)
        self.assertIn("$100 biweekly contribution", markdown)
        self.assertIn("15% employee discount", markdown)
        self.assertIn("purchases approximately every six months", markdown)
        self.assertIn("2-3 year historical holding pattern", markdown)
        self.assertIn("dividend growth and long-term income", messages[0]["content"])
        self.assertIn("do not claim guaranteed returns", messages[0]["content"])
        self.assertIn("Focus on concentration", messages[1]["content"])


class AiAnalysisFeatureFlagTests(unittest.TestCase):
    def _sample_holdings_metrics(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])
        return holdings, metrics

    def test_ai_controls_unavailable_when_disabled_and_no_client_invoked(self) -> None:
        holdings, metrics = self._sample_holdings_metrics()

        with patch.object(app, "ENABLE_AI_ANALYSIS", False), \
            patch.dict(app.st.session_state, {}, clear=True), \
            patch.object(app.st, "subheader") as subheader_mock, \
            patch.object(app.st, "caption") as caption_mock, \
            patch.object(app.st, "selectbox") as selectbox_mock, \
            patch.object(app.st, "text_area") as text_area_mock, \
            patch.object(app.st, "columns") as columns_mock, \
            patch.object(app.TokenRouterClient, "chat_completions") as client_mock:
            app._render_ai_analysis_tab(holdings, metrics)

        subheader_mock.assert_not_called()
        caption_mock.assert_not_called()
        selectbox_mock.assert_not_called()
        text_area_mock.assert_not_called()
        columns_mock.assert_not_called()
        client_mock.assert_not_called()

    def test_main_does_not_render_access_log_expander(self) -> None:
        def _fake_columns(spec: object):
            count = spec if isinstance(spec, int) else len(spec)
            return [contextlib.nullcontext() for _ in range(count)]

        def _fake_tabs(labels: list[str]):
            return [contextlib.nullcontext() for _ in labels]

        empty_holdings = pd.DataFrame(columns=["ticker", "shares", "amount_invested"])
        expander_labels: list[str] = []

        def _fake_expander(label: str, *args: object, **kwargs: object):
            expander_labels.append(label)
            return contextlib.nullcontext()

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(app, "ENABLE_AI_ANALYSIS", False))
            stack.enter_context(patch.object(app, "_enable_periodic_refresh"))
            stack.enter_context(patch.object(app, "_initialize_export_state"))
            stack.enter_context(patch.object(app, "_reset_export_missing_field_warnings"))
            stack.enter_context(patch.object(app, "_detect_mobile_client", return_value=False))
            stack.enter_context(patch.object(app, "_render_last_updated_indicator"))
            stack.enter_context(patch.object(app, "_load_saved_holdings", return_value=empty_holdings))
            stack.enter_context(patch.object(app, "_render_holdings_tab", return_value=empty_holdings))
            stack.enter_context(patch.object(app, "_render_ai_analysis_tab"))
            stack.enter_context(patch.object(app.st, "set_page_config"))
            stack.enter_context(patch.object(app.st, "title"))
            stack.enter_context(patch.object(app.st, "caption"))
            stack.enter_context(patch.object(app.st, "subheader"))
            stack.enter_context(patch.object(app.st, "write"))
            stack.enter_context(patch.object(app.st, "toggle", return_value=False))
            stack.enter_context(patch.object(app.st, "button", return_value=False))
            stack.enter_context(patch.object(app.st, "warning"))
            stack.enter_context(patch.object(app.st, "success"))
            stack.enter_context(patch.object(app.st, "info"))
            stack.enter_context(patch.object(app.st, "error"))
            stack.enter_context(patch.object(app.st, "metric"))
            stack.enter_context(patch.object(app.st, "dataframe"))
            stack.enter_context(patch.object(app.st, "columns", side_effect=_fake_columns))
            stack.enter_context(patch.object(app.st, "expander", side_effect=_fake_expander))
            stack.enter_context(patch.object(app.st, "tabs", side_effect=_fake_tabs))
            stack.enter_context(patch.object(app.st, "sidebar", contextlib.nullcontext()))
            app.main()

        self.assertNotIn("Live app access logs", expander_labels)

    def test_main_starts_without_api_key_when_ai_disabled(self) -> None:
        def _fake_columns(spec: object):
            count = spec if isinstance(spec, int) else len(spec)
            return [contextlib.nullcontext() for _ in range(count)]

        def _fake_tabs(labels: list[str]):
            return [contextlib.nullcontext() for _ in labels]

        empty_holdings = pd.DataFrame(columns=["ticker", "shares", "amount_invested"])
        session_state: dict[str, object] = {}

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(app, "ENABLE_AI_ANALYSIS", False))
            stack.enter_context(patch.object(app.st, "session_state", session_state))
            stack.enter_context(patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": ""}, clear=False))
            stack.enter_context(patch.object(app, "_enable_periodic_refresh"))
            stack.enter_context(patch.object(app, "_initialize_export_state"))
            stack.enter_context(patch.object(app, "_reset_export_missing_field_warnings"))
            stack.enter_context(patch.object(app, "_detect_mobile_client", return_value=False))
            stack.enter_context(patch.object(app, "_render_last_updated_indicator"))
            stack.enter_context(patch.object(app, "_load_saved_holdings", return_value=empty_holdings))
            stack.enter_context(patch.object(app, "_render_holdings_tab", return_value=empty_holdings))
            render_ai_mock = stack.enter_context(patch.object(app, "_render_ai_analysis_tab"))
            tokenrouter_ctor_mock = stack.enter_context(patch.object(app, "TokenRouterClient"))
            stack.enter_context(patch.object(app.st, "set_page_config"))
            stack.enter_context(patch.object(app.st, "title"))
            stack.enter_context(patch.object(app.st, "caption"))
            stack.enter_context(patch.object(app.st, "subheader"))
            stack.enter_context(patch.object(app.st, "write"))
            stack.enter_context(patch.object(app.st, "toggle", return_value=False))
            stack.enter_context(patch.object(app.st, "button", return_value=False))
            stack.enter_context(patch.object(app.st, "warning"))
            stack.enter_context(patch.object(app.st, "success"))
            stack.enter_context(patch.object(app.st, "info"))
            stack.enter_context(patch.object(app.st, "error"))
            stack.enter_context(patch.object(app.st, "metric"))
            stack.enter_context(patch.object(app.st, "dataframe"))
            stack.enter_context(patch.object(app.st, "columns", side_effect=_fake_columns))
            stack.enter_context(patch.object(app.st, "expander", return_value=contextlib.nullcontext()))
            tabs_mock = stack.enter_context(patch.object(app.st, "tabs", side_effect=_fake_tabs))
            stack.enter_context(patch.object(app.st, "sidebar", contextlib.nullcontext()))
            app.main()

        tabs_mock.assert_called_once()
        tab_labels = tabs_mock.call_args.args[0]
        self.assertNotIn("AI Analysis", tab_labels)
        tokenrouter_ctor_mock.assert_not_called()
        render_ai_mock.assert_not_called()

    def test_manual_exports_work_when_ai_disabled(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "shares": 10,
                    "amount_invested": 1000.0,
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

        with patch.object(app, "ENABLE_AI_ANALYSIS", False), patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": ""}, clear=False):
            markdown_export = app._build_export_markdown(holdings, metrics)
            json_export = app._build_ai_export_json(holdings, metrics)

        payload = json.loads(json_export)
        self.assertIn("# Dividend Portfolio Tracker Export", markdown_export)
        self.assertIn("portfolio_snapshot", payload)
        self.assertIn("asset_allocation", payload)

    def test_enabling_flag_restores_ai_tab_and_active_tab_mapping(self) -> None:
        with patch.object(app, "ENABLE_AI_ANALYSIS", True):
            self.assertIn("AI Analysis", app._primary_tab_labels())
            self.assertEqual(app._normalize_active_tab("AI Analysis"), "AI Analysis")


class AiAnalysisTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ai_flag_patcher = patch.object(app, "ENABLE_AI_ANALYSIS", True)
        self._ai_flag_patcher.start()

    def tearDown(self) -> None:
        self._ai_flag_patcher.stop()

    def _patch_ai_tab_widgets(self, analyze_clicked: bool, clear_clicked: bool = False):
        action_button = SimpleNamespace(button=MagicMock(return_value=analyze_clicked))
        clear_button = SimpleNamespace(button=MagicMock(return_value=clear_clicked))
        patches = [
            patch.object(app.st, "subheader"),
            patch.object(app.st, "caption"),
            patch.object(app.st, "info"),
            patch.object(app.st, "error"),
            patch.object(app.st, "markdown"),
            patch.object(app.st, "download_button"),
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]),
            patch.object(app.st, "text_area", return_value=""),
            patch.object(app.st, "columns", return_value=[action_button, clear_button]),
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()),
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()),
        ]
        return contextlib.ExitStack(), patches

    def test_render_does_not_call_tokenrouter_before_button_click(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])

        with patch.dict(app.st.session_state, {}, clear=True), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics") as payload_mock, \
            patch.object(app, "build_ai_analysis_messages") as messages_mock, \
            patch.object(app.TokenRouterClient, "chat_completions") as client_mock, \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info"), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "markdown"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[SimpleNamespace(button=MagicMock(return_value=False)), SimpleNamespace(button=MagicMock(return_value=False))]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            app._render_ai_analysis_tab(holdings, metrics)

        payload_mock.assert_not_called()
        messages_mock.assert_not_called()
        client_mock.assert_not_called()

    def test_render_calls_tokenrouter_only_after_analyze_click(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])

        fake_result = SimpleNamespace(content="# Analysis\n\n- OK", model=TOKENROUTER_DEFAULT_MODEL, raw_response={}, usage={})
        fake_session_state: dict[str, object] = {}

        with patch.object(app.st, "session_state", fake_session_state), \
            patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": "sk-test-key"}, clear=False), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 100, "after_tokens": 25, "before_chars": 200, "before_tokens": 50, "reduction_pct": 50.0})) as payload_mock, \
            patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]) as messages_mock, \
            patch.object(app.TokenRouterClient, "chat_completions", return_value=fake_result) as client_mock, \
            patch.object(app.st, "markdown") as markdown_mock, \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info"), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[SimpleNamespace(button=MagicMock(return_value=True)), SimpleNamespace(button=MagicMock(return_value=False))]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            app._render_ai_analysis_tab(holdings, metrics)

        payload_mock.assert_called_once()
        messages_mock.assert_called_once()
        client_mock.assert_called_once()
        markdown_mock.assert_any_call("# Analysis\n\n- OK")
        self.assertEqual(fake_session_state.get("tokenrouter_analysis"), "# Analysis\n\n- OK")

    def test_saved_analysis_remains_visible_after_rerun_without_new_request(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])
        fake_result = SimpleNamespace(
            content="# Persistent Analysis",
            model=TOKENROUTER_DEFAULT_MODEL,
            raw_response={},
            usage={},
            usage_metadata={"model": TOKENROUTER_DEFAULT_MODEL, "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        fake_session_state: dict[str, object] = {}

        with patch.object(app.st, "session_state", fake_session_state), \
            patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": "sk-test-key"}, clear=False), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 100, "after_tokens": 25, "before_chars": 200, "before_tokens": 50, "reduction_pct": 50.0})), \
            patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]), \
            patch.object(app.TokenRouterClient, "chat_completions", return_value=fake_result), \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info"), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0], TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[1]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", side_effect=[
                [SimpleNamespace(button=MagicMock(return_value=True)), SimpleNamespace(button=MagicMock(return_value=False))],
                [SimpleNamespace(button=MagicMock(return_value=False)), SimpleNamespace(button=MagicMock(return_value=False))],
            ]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "markdown") as markdown_mock:
            app._render_ai_analysis_tab(holdings, metrics)
            app._render_ai_analysis_tab(holdings, metrics)

        self.assertEqual(fake_session_state.get("tokenrouter_analysis"), "# Persistent Analysis")
        self.assertGreaterEqual(markdown_mock.call_count, 2)

    def test_malformed_response_shows_sanitized_error_detail(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])

        malformed_error = TokenRouterResponseError(
            "TokenRouter returned no extractable assistant content.",
            diagnostic={
                "http_status": 200,
                "top_level_keys": ["choices", "model", "usage"],
                "choices_count": 1,
                "message_keys": ["content", "reasoning_content"],
                "content_type": "list",
                "content_items": 0,
            },
        )
        fake_session_state: dict[str, object] = {}

        with patch.object(app.st, "session_state", fake_session_state), \
            patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": "sk-test-key"}, clear=False), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 100, "after_tokens": 25, "before_chars": 200, "before_tokens": 50, "reduction_pct": 50.0})), \
            patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]), \
            patch.object(app.TokenRouterClient, "chat_completions", side_effect=malformed_error), \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info"), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "markdown"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[SimpleNamespace(button=MagicMock(return_value=True)), SimpleNamespace(button=MagicMock(return_value=False))]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            app._render_ai_analysis_tab(holdings, metrics)

        detail = str(fake_session_state.get("ai_analysis_error_detail", ""))
        self.assertIn("HTTP status", detail)
        self.assertIn("Top-level keys", detail)
        self.assertIn("Number of choices", detail)

    def test_missing_api_key_shows_setup_message(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])

        info_mock = MagicMock()
        action_button = SimpleNamespace(button=MagicMock(return_value=False))
        clear_button = SimpleNamespace(button=MagicMock(return_value=False))
        with patch.dict(app.st.session_state, {}, clear=True), \
            patch.dict("os.environ", {"TOKENROUTER_API_KEY": ""}, clear=False), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics") as payload_mock, \
            patch.object(app.TokenRouterClient, "chat_completions") as client_mock, \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info", info_mock), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "markdown"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[action_button, clear_button]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            app._render_ai_analysis_tab(holdings, metrics)

        info_messages = " ".join(str(call.args[0]) for call in info_mock.call_args_list if call.args)
        self.assertIn("Set TOKENROUTER_API_KEY", info_messages)
        self.assertTrue(action_button.button.call_args.kwargs["disabled"])
        payload_mock.assert_not_called()
        client_mock.assert_not_called()

    def test_timeout_override_is_passed_to_client(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])
        fake_result = SimpleNamespace(content="# Analysis", model=TOKENROUTER_DEFAULT_MODEL, raw_response={}, usage={})

        with patch.dict(app.st.session_state, {}, clear=True), \
            patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": "sk-test-key", "TOKENROUTER_TIMEOUT_SECONDS": "222"}, clear=False), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 100, "after_tokens": 25, "before_chars": 200, "before_tokens": 50, "reduction_pct": 50.0})), \
            patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]), \
            patch.object(app, "TokenRouterClient") as client_ctor, \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info"), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "markdown"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[SimpleNamespace(button=MagicMock(return_value=True)), SimpleNamespace(button=MagicMock(return_value=False))]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            client_instance = MagicMock()
            client_instance.chat_completions.return_value = fake_result
            client_ctor.return_value = client_instance
            app._render_ai_analysis_tab(holdings, metrics)

        client_ctor.assert_called_once()
        self.assertEqual(client_ctor.call_args.kwargs["timeout_seconds"], 222.0)
        self.assertEqual(client_ctor.call_args.kwargs["connect_timeout_seconds"], TOKENROUTER_DEFAULT_CONNECT_TIMEOUT_SECONDS)

    def test_timeout_error_surfaces_clear_message(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])
        st_error = MagicMock()

        with patch.dict(app.st.session_state, {}, clear=True), \
            patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": "sk-test-key"}, clear=False), \
            patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 100, "after_tokens": 25, "before_chars": 200, "before_tokens": 50, "reduction_pct": 50.0})), \
            patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]), \
            patch.object(app.TokenRouterClient, "chat_completions", side_effect=TokenRouterTimeoutError("TokenRouter request timed out.")), \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info"), \
            patch.object(app.st, "error", st_error), \
            patch.object(app.st, "markdown"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[SimpleNamespace(button=MagicMock(return_value=True)), SimpleNamespace(button=MagicMock(return_value=False))]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            app._render_ai_analysis_tab(holdings, metrics)

        error_messages = " ".join(str(call.args[0]) for call in st_error.call_args_list if call.args)
        self.assertIn("TokenRouter request timed out", error_messages)


class AiAnalysisContextCompactionTests(unittest.TestCase):
    def test_compact_context_excludes_redundant_and_raw_fields(self) -> None:
        full_payload = _sample_full_ai_payload()
        compact = app._compact_analysis_context(full_payload, "Comprehensive Portfolio Review", "")

        self.assertNotIn("unified_snapshot", compact)
        self.assertNotIn("statement_history", compact)
        self.assertNotIn("supplemental_yahoo_historical_price_data", compact)
        self.assertNotIn("holdings", compact)
        self.assertIn("supplemental_yahoo_financial_data", compact)

    def test_compact_context_preserves_critical_hd_espp_facts(self) -> None:
        full_payload = _sample_full_ai_payload()
        compact = app._compact_analysis_context(full_payload, "Comprehensive Portfolio Review", "")
        facts = compact["contribution_context"]["hd_espp_facts"]

        self.assertEqual(facts["biweekly_contribution_usd"], 100)
        self.assertEqual(facts["discount_pct"], 15)
        self.assertIn("every six months", facts["purchase_cadence"])
        self.assertIn("2-3 years", facts["historical_holding_pattern"])
        self.assertIn("employment-income", facts["employer_income_concentration_relationship"])

    def test_analysis_type_specific_context_prioritizes_expected_sections(self) -> None:
        full_payload = _sample_full_ai_payload()
        income_payload = app._compact_analysis_context(full_payload, "Income and Dividend Analysis", "")
        diversification_payload = app._compact_analysis_context(full_payload, "Diversification and Concentration", "")

        self.assertGreaterEqual(
            len(income_payload.get("income_allocation_by_holding", [])),
            len(diversification_payload.get("income_allocation_by_holding", [])),
        )
        self.assertGreaterEqual(
            len(diversification_payload.get("direct_sector_exposure", [])),
            len(income_payload.get("direct_sector_exposure", [])),
        )

    def test_compaction_diagnostics_show_smaller_context(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])

        with patch.object(app, "_build_ai_export_payload", return_value=_sample_full_ai_payload()):
            compact_payload, diagnostics = app._build_ai_analysis_payload_with_diagnostics(
                holdings,
                metrics,
                "Comprehensive Portfolio Review",
                "",
            )

        self.assertIsInstance(compact_payload, dict)
        self.assertGreater(diagnostics["before_chars"], diagnostics["after_chars"])
        self.assertGreater(diagnostics["before_tokens"], diagnostics["after_tokens"])
        self.assertGreater(diagnostics["reduction_pct"], 0.0)


class DotenvLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(app.__file__).resolve().parent
        self.env_path = self.project_root / ".env"
        self.original_exists = self.env_path.exists()
        self.original_content = self.env_path.read_text(encoding="utf-8") if self.original_exists else ""
        self.original_env = {
            "TOKENROUTER_API_KEY": os.environ.get("TOKENROUTER_API_KEY"),
            "TOKENROUTER_MODEL": os.environ.get("TOKENROUTER_MODEL"),
            "TOKENROUTER_BASE_URL": os.environ.get("TOKENROUTER_BASE_URL"),
            "TOKENROUTER_TIMEOUT_SECONDS": os.environ.get("TOKENROUTER_TIMEOUT_SECONDS"),
        }

    def tearDown(self) -> None:
        if self.original_exists:
            self.env_path.write_text(self.original_content, encoding="utf-8")
        elif self.env_path.exists():
            self.env_path.unlink()

        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        importlib.reload(app)

    def test_tokenrouter_api_key_loads_from_project_env(self) -> None:
        os.environ.pop("TOKENROUTER_API_KEY", None)
        self.env_path.write_text("TOKENROUTER_API_KEY=sk-from-dotenv\n", encoding="utf-8")

        reloaded_app = importlib.reload(app)

        self.assertEqual(os.environ.get("TOKENROUTER_API_KEY"), "sk-from-dotenv")
        self.assertTrue(reloaded_app._tokenrouter_has_api_key())

    def test_os_environment_variable_takes_priority_over_env_file(self) -> None:
        os.environ["TOKENROUTER_API_KEY"] = "sk-from-os"
        self.env_path.write_text("TOKENROUTER_API_KEY=sk-from-dotenv\n", encoding="utf-8")

        reloaded_app = importlib.reload(app)

        self.assertEqual(os.environ.get("TOKENROUTER_API_KEY"), "sk-from-os")
        self.assertTrue(reloaded_app._tokenrouter_has_api_key())

    def test_analyze_button_enabled_when_key_loaded_from_env_file(self) -> None:
        os.environ.pop("TOKENROUTER_API_KEY", None)
        self.env_path.write_text("TOKENROUTER_API_KEY=sk-from-dotenv\n", encoding="utf-8")
        reloaded_app = importlib.reload(app)

        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])
        action_button = SimpleNamespace(button=MagicMock(return_value=False))
        clear_button = SimpleNamespace(button=MagicMock(return_value=False))

        with patch.object(reloaded_app, "ENABLE_AI_ANALYSIS", True), \
            patch.dict(reloaded_app.st.session_state, {}, clear=True), \
            patch.object(reloaded_app, "_build_ai_analysis_payload_with_diagnostics") as payload_mock, \
            patch.object(reloaded_app, "build_ai_analysis_messages") as messages_mock, \
            patch.object(reloaded_app.TokenRouterClient, "chat_completions") as client_mock, \
            patch.object(reloaded_app.st, "subheader"), \
            patch.object(reloaded_app.st, "caption"), \
            patch.object(reloaded_app.st, "info"), \
            patch.object(reloaded_app.st, "error"), \
            patch.object(reloaded_app.st, "markdown"), \
            patch.object(reloaded_app.st, "download_button"), \
            patch.object(reloaded_app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(reloaded_app.st, "text_area", return_value=""), \
            patch.object(reloaded_app.st, "columns", return_value=[action_button, clear_button]), \
            patch.object(reloaded_app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(reloaded_app.st, "spinner", return_value=contextlib.nullcontext()):
            reloaded_app._render_ai_analysis_tab(holdings, metrics)

        self.assertFalse(action_button.button.call_args.kwargs["disabled"])
        payload_mock.assert_not_called()
        messages_mock.assert_not_called()
        client_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

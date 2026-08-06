import contextlib
import io
import json
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib import error

import pandas as pd

import app
from ai_analysis_prompt import AI_ANALYSIS_TYPES, build_ai_analysis_markdown, build_ai_analysis_messages
from tokenrouter_client import (
    TOKENROUTER_DEFAULT_BASE_URL,
    TOKENROUTER_DEFAULT_MODEL,
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


class TokenRouterClientTests(unittest.TestCase):
    def test_default_base_url_points_to_tokenrouter_com(self) -> None:
        self.assertEqual(TOKENROUTER_DEFAULT_BASE_URL, "https://api.tokenrouter.com/v1")

    def test_chat_completions_sends_bearer_header_and_keeps_key_out_of_payload(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request_obj, timeout):
            captured["url"] = request_obj.full_url
            captured["headers"] = dict(request_obj.header_items())
            captured["body"] = request_obj.data.decode("utf-8") if request_obj.data else ""
            return FakeResponse(
                json.dumps(
                    {
                        "model": TOKENROUTER_DEFAULT_MODEL,
                        "choices": [{"message": {"content": "# Analysis"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                    }
                ).encode("utf-8")
            )

        client = TokenRouterClient(api_key="sk-test-key", base_url=TOKENROUTER_DEFAULT_BASE_URL, timeout_seconds=1.0)
        with patch("tokenrouter_client.request.urlopen", side_effect=fake_urlopen):
            result = client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "# Analysis")
        self.assertEqual(result.model, TOKENROUTER_DEFAULT_MODEL)
        self.assertEqual(captured["url"], f"{TOKENROUTER_DEFAULT_BASE_URL}/chat/completions")
        self.assertIn("Authorization", captured["headers"])
        self.assertEqual(captured["body"].find("sk-test-key"), -1)
        self.assertIn(f'"model": "{TOKENROUTER_DEFAULT_MODEL}"', str(captured["body"]))

    def test_chat_completions_parses_successful_response(self) -> None:
        class FakeResponse:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "model": TOKENROUTER_DEFAULT_MODEL,
                        "choices": [{"message": {"content": "OK"}}],
                        "usage": {"prompt_tokens": 6, "completion_tokens": 28, "total_tokens": 34},
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        client = TokenRouterClient(api_key="sk-test-key")
        with patch("tokenrouter_client.request.urlopen", return_value=FakeResponse()):
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
        http_error = error.HTTPError(
            url=f"{TOKENROUTER_DEFAULT_BASE_URL}/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"invalid key"}}'),
        )
        client = TokenRouterClient(api_key="sk-test-key")

        with patch("tokenrouter_client.request.urlopen", side_effect=http_error):
            with self.assertRaises(TokenRouterAuthenticationError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_balance_error_is_mapped(self) -> None:
        http_error = error.HTTPError(
            url=f"{TOKENROUTER_DEFAULT_BASE_URL}/chat/completions",
            code=402,
            msg="Payment Required",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"insufficient credits"}}'),
        )
        client = TokenRouterClient(api_key="sk-test-key")

        with patch("tokenrouter_client.request.urlopen", side_effect=http_error):
            with self.assertRaises(TokenRouterInsufficientBalanceError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_rate_limit_error_is_mapped(self) -> None:
        http_error = error.HTTPError(
            url=f"{TOKENROUTER_DEFAULT_BASE_URL}/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"slow down"}}'),
        )
        client = TokenRouterClient(api_key="sk-test-key")

        with patch("tokenrouter_client.request.urlopen", side_effect=http_error):
            with self.assertRaises(TokenRouterRateLimitError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_timeout_and_connection_errors_are_mapped(self) -> None:
        client = TokenRouterClient(api_key="sk-test-key")

        with patch(
            "tokenrouter_client.request.urlopen",
            side_effect=error.URLError(socket.timeout("timed out")),
        ):
            with self.assertRaises(TokenRouterTimeoutError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

        with patch(
            "tokenrouter_client.request.urlopen",
            side_effect=error.URLError(OSError("connection refused")),
        ):
            with self.assertRaises(TokenRouterConnectionError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])

    def test_malformed_json_is_rejected(self) -> None:
        class FakeResponse:
            def read(self) -> bytes:
                return b"not-json"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        client = TokenRouterClient(api_key="sk-test-key")
        with patch("tokenrouter_client.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(TokenRouterResponseError):
                client.chat_completions(model=TOKENROUTER_DEFAULT_MODEL, messages=[{"role": "user", "content": "Hello"}])


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


class AiAnalysisTabTests(unittest.TestCase):
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
            patch.object(app, "_build_ai_analysis_request_payload") as payload_mock, \
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

        with patch.dict(app.st.session_state, {}, clear=True), \
            patch.dict(app.os.environ, {"TOKENROUTER_API_KEY": "sk-test-key"}, clear=False), \
            patch.object(app, "_build_ai_analysis_request_payload", return_value={"portfolio_snapshot": {}}) as payload_mock, \
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

    def test_missing_api_key_shows_setup_message(self) -> None:
        holdings = pd.DataFrame([{"ticker": "AAPL", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "AAPL", "market_value": 100.0, "annual_income": 1.0}])

        info_mock = MagicMock()
        with patch.dict(app.st.session_state, {}, clear=True), \
            patch.dict("os.environ", {"TOKENROUTER_API_KEY": ""}, clear=False), \
            patch.object(app, "_build_ai_analysis_request_payload") as payload_mock, \
            patch.object(app.TokenRouterClient, "chat_completions") as client_mock, \
            patch.object(app.st, "subheader"), \
            patch.object(app.st, "caption"), \
            patch.object(app.st, "info", info_mock), \
            patch.object(app.st, "error"), \
            patch.object(app.st, "markdown"), \
            patch.object(app.st, "download_button"), \
            patch.object(app.st, "selectbox", side_effect=[TOKENROUTER_DEFAULT_MODEL, AI_ANALYSIS_TYPES[0]]), \
            patch.object(app.st, "text_area", return_value=""), \
            patch.object(app.st, "columns", return_value=[SimpleNamespace(button=MagicMock(return_value=False)), SimpleNamespace(button=MagicMock(return_value=False))]), \
            patch.object(app.st, "expander", return_value=contextlib.nullcontext()), \
            patch.object(app.st, "spinner", return_value=contextlib.nullcontext()):
            app._render_ai_analysis_tab(holdings, metrics)

        info_messages = " ".join(str(call.args[0]) for call in info_mock.call_args_list if call.args)
        self.assertIn("Set TOKENROUTER_API_KEY", info_messages)
        payload_mock.assert_not_called()
        client_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

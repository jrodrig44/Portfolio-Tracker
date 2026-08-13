import contextlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd

import app
from ai_analysis_prompt import AI_ANALYSIS_TYPES, build_ai_analysis_messages
from onemin_ai_client import (
    ONEMIN_AI_DEFAULT_BASE_URL,
    ONEMIN_AI_DEFAULT_MODEL,
    OneMinAIAuthenticationError,
    OneMinAIClient,
    OneMinAIConfigError,
    OneMinAIConnectionError,
    OneMinAIError,
    OneMinAIInsufficientCreditsError,
    OneMinAIRateLimitError,
    OneMinAIResponseError,
    OneMinAITimeoutError,
    OneMinAIUnsupportedModelError,
    messages_to_prompt,
)


def _response(payload: object, status_code: int = 200, content: bytes | None = None) -> SimpleNamespace:
    body = content if content is not None else json.dumps(payload).encode("utf-8")
    return SimpleNamespace(
        status_code=status_code,
        content=body,
        is_error=status_code >= 400,
        json=MagicMock(return_value=payload),
    )


def _success_response(result: object = None, model: str = ONEMIN_AI_DEFAULT_MODEL) -> dict[str, object]:
    if result is None:
        result = ["# Analysis"]
    return {
        "aiRecord": {
            "uuid": "record-123",
            "model": model,
            "status": "SUCCESS",
            "teamUser": {"email": "private@example.test", "teamId": "team-secret"},
            "aiRecordDetail": {"resultObject": result},
        }
    }


class OneMinAIClientTests(unittest.TestCase):
    def _client(self, **kwargs: object) -> OneMinAIClient:
        return OneMinAIClient(api_key="private-api-key", **kwargs)

    def test_request_uses_documented_endpoint_headers_and_payload(self) -> None:
        client = self._client(base_url=" https://api.1min.ai/// ")
        with patch("onemin_ai_client.httpx.post", return_value=_response(_success_response())) as post_mock:
            result = client.chat_with_ai(model="configured-model", prompt="System\n\nPortfolio: $12,345")

        self.assertEqual(result.content, "# Analysis")
        self.assertEqual(post_mock.call_args.args[0], "https://api.1min.ai/api/chat-with-ai")
        headers = post_mock.call_args.kwargs["headers"]
        self.assertEqual(headers["API-KEY"], "private-api-key")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Authorization", headers)
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["type"], "UNIFY_CHAT_WITH_AI")
        self.assertEqual(payload["model"], "configured-model")
        self.assertEqual(payload["promptObject"]["prompt"], "System\n\nPortfolio: $12,345")
        self.assertFalse(payload["promptObject"]["settings"]["withMemories"])
        self.assertFalse(payload["promptObject"]["settings"]["webSearchSettings"]["webSearch"])
        self.assertNotIn("conversationId", payload["promptObject"])
        self.assertNotIn("attachments", payload["promptObject"])

    def test_default_configuration_and_timeout_fallbacks(self) -> None:
        self.assertEqual(OneMinAIClient(api_key="key").base_url, ONEMIN_AI_DEFAULT_BASE_URL)
        self.assertEqual(ONEMIN_AI_DEFAULT_MODEL, "gpt-5.6-sol")
        self.assertEqual(OneMinAIClient(api_key="key").timeout_seconds, 180.0)
        for invalid in ("", "bad", "0", "-5", 0, -1, float("nan"), float("inf"), float("-inf")):
            self.assertEqual(OneMinAIClient(api_key="key", timeout_seconds=invalid).timeout_seconds, 180.0)

    def test_missing_key_blank_model_and_empty_prompt_fail_before_http(self) -> None:
        cases = [
            (OneMinAIClient(api_key=""), "gpt-4o-mini", "prompt"),
            (self._client(), " ", "prompt"),
            (self._client(), "gpt-4o-mini", " \n "),
        ]
        with patch("onemin_ai_client.httpx.post") as post_mock:
            for client, model, prompt in cases:
                with self.assertRaises(OneMinAIConfigError):
                    client.chat_with_ai(model=model, prompt=prompt)
        post_mock.assert_not_called()

    def test_successful_response_and_multiple_results_are_parsed(self) -> None:
        client = self._client()
        with patch("onemin_ai_client.httpx.post", return_value=_response(_success_response(["First", "\n", "Second"]))):
            result = client.chat_with_ai(model="requested", prompt="prompt")
        self.assertEqual(result.content, "First\n\nSecond")
        self.assertEqual(result.model, ONEMIN_AI_DEFAULT_MODEL)
        self.assertEqual(result.record_id, "record-123")
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.response_diagnostic["requested_model"], "requested")
        self.assertNotIn("private@example.test", str(result.response_diagnostic))
        self.assertNotIn("team-secret", str(result.response_diagnostic))
        self.assertNotIn("private-api-key", str(result.response_diagnostic))

    def test_response_shape_failures_are_rejected(self) -> None:
        invalid_payloads = [
            _success_response([]),
            {},
            {"aiRecord": {"status": "SUCCESS"}},
            {"aiRecord": {"status": "FAILED", "uuid": "record-123"}},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), patch("onemin_ai_client.httpx.post", return_value=_response(payload)):
                with self.assertRaises(OneMinAIResponseError):
                    self._client().chat_with_ai(model="model", prompt="prompt")

    def test_invalid_json_and_empty_body_are_rejected(self) -> None:
        invalid_json = _response({}, content=b"not-json")
        invalid_json.json.side_effect = json.JSONDecodeError("bad", "x", 0)
        with patch("onemin_ai_client.httpx.post", return_value=invalid_json):
            with self.assertRaises(OneMinAIResponseError):
                self._client().chat_with_ai(model="model", prompt="prompt")
        with patch("onemin_ai_client.httpx.post", return_value=_response({}, content=b"")):
            with self.assertRaises(OneMinAIResponseError):
                self._client().chat_with_ai(model="model", prompt="prompt")

    def test_http_errors_are_mapped_without_key_disclosure(self) -> None:
        cases = [
            (401, "UNAUTHORIZED", "bad key", OneMinAIAuthenticationError),
            (402, "INSUFFICIENT_CREDITS", "credits exhausted", OneMinAIInsufficientCreditsError),
            (429, "RATE_LIMITED", "too many", OneMinAIRateLimitError),
            (422, "MODEL_INVALID", "unknown model", OneMinAIUnsupportedModelError),
            (422, "PROMPT_INVALID", "bad prompt", OneMinAIResponseError),
            (500, "SERVER_ERROR", "private-api-key", OneMinAIError),
        ]
        for status, code, message, error_type in cases:
            with self.subTest(status=status), patch(
                "onemin_ai_client.httpx.post",
                return_value=_response({"success": False, "error": {"code": code, "message": message}}, status),
            ):
                with self.assertRaises(error_type) as context:
                    self._client().chat_with_ai(model="model", prompt="prompt")
            self.assertNotIn("private-api-key", str(context.exception))

    def test_validation_error_diagnostic_retains_only_safe_provider_fields(self) -> None:
        raw_prompt = "Portfolio holdings: HD, JEPI"
        response_payload = {
            "success": False,
            "teamUser": {"email": "private@example.test", "teamId": "team-secret"},
            "error": {
                "code": "PROMPT_OBJECT_VALIDATION_FAILED",
                "message": "Invalid prompt object for private-api-key",
                "details": [
                    {"field": "promptObject.prompt", "message": "must be a string"},
                    {"field": "prompt", "message": raw_prompt, "request": {"prompt": raw_prompt}},
                ],
            },
        }
        with patch("onemin_ai_client.httpx.post", return_value=_response(response_payload, 422)):
            with self.assertRaises(OneMinAIResponseError) as context:
                self._client().chat_with_ai(model="model", prompt=raw_prompt)

        diagnostic = context.exception.diagnostic
        self.assertEqual(diagnostic["http_status"], 422)
        self.assertEqual(diagnostic["provider_error_code"], "PROMPT_OBJECT_VALIDATION_FAILED")
        self.assertEqual(diagnostic["provider_error_message"], "Invalid prompt object for [redacted]")
        self.assertEqual(diagnostic["provider_validation_details"], [{"field": "promptObject.prompt", "message": "must be a string"}])
        diagnostic_text = str(diagnostic)
        for secret_or_private_value in ("private-api-key", raw_prompt, "private@example.test", "team-secret"):
            self.assertNotIn(secret_or_private_value, diagnostic_text)

    def test_unsupported_model_detection_uses_provider_code_or_message(self) -> None:
        cases = [
            (400, "MODEL_NOT_AVAILABLE", "The request was rejected"),
            (422, "VALIDATION_FAILED", "The configured model is unsupported"),
        ]
        for status, code, message in cases:
            with self.subTest(code=code), patch(
                "onemin_ai_client.httpx.post",
                return_value=_response({"success": False, "error": {"code": code, "message": message}}, status),
            ):
                with self.assertRaises(OneMinAIUnsupportedModelError):
                    self._client().chat_with_ai(model="model", prompt="prompt")

    def test_connection_and_timeout_are_mapped(self) -> None:
        request = httpx.Request("POST", "https://api.1min.ai/api/chat-with-ai")
        with patch("onemin_ai_client.httpx.post", side_effect=httpx.ConnectError("offline", request=request)):
            with self.assertRaises(OneMinAIConnectionError):
                self._client().chat_with_ai(model="model", prompt="prompt")
        with patch("onemin_ai_client.httpx.post", side_effect=httpx.ReadTimeout("slow", request=request)):
            with self.assertRaises(OneMinAITimeoutError):
                self._client().chat_with_ai(model="model", prompt="prompt")


class PromptConversionTests(unittest.TestCase):
    def test_system_user_whitespace_and_portfolio_figures_are_retained(self) -> None:
        messages = [
            {"role": "system", "content": "System rule:\n  retain whitespace."},
            {"role": "user", "content": "Portfolio value: $12,345.67\n\nHD shares: 20"},
            {"role": "assistant", "content": "Prior clarification"},
        ]
        prompt = messages_to_prompt(messages)
        self.assertIn("SYSTEM INSTRUCTIONS:\nSystem rule:\n  retain whitespace.", prompt)
        self.assertIn("USER REQUEST:\nPortfolio value: $12,345.67\n\nHD shares: 20", prompt)
        self.assertIn("ASSISTANT MESSAGE:\nPrior clarification", prompt)

    def test_existing_prompt_builder_retains_analysis_and_custom_question(self) -> None:
        messages = build_ai_analysis_messages(
            {"portfolio_snapshot": {"portfolio_value": 25000, "holdings_count": 1, "top_holding": "HD"}},
            "Custom Question",
            "How durable is the income?",
        )
        prompt = messages_to_prompt(messages)
        self.assertIn("Do not present the response as personalized fiduciary advice", prompt)
        self.assertIn("How durable is the income?", prompt)
        self.assertIn("$25,000.00", prompt)


class AppAIConfigurationTests(unittest.TestCase):
    def test_onemin_environment_helpers_and_enabled_tab(self) -> None:
        with patch.dict(os.environ, {"ONEMIN_AI_API_KEY": " key ", "ONEMIN_AI_MODEL": " custom-model ", "ONEMIN_AI_BASE_URL": " https://example.test/// ", "ONEMIN_AI_TIMEOUT_SECONDS": "0"}, clear=False):
            self.assertTrue(app._onemin_ai_has_api_key())
            self.assertEqual(app._onemin_ai_default_model(), "custom-model")
            self.assertEqual(app._onemin_ai_base_url(), "https://example.test")
            self.assertEqual(app._onemin_ai_timeout_seconds(), 180.0)
        self.assertTrue(app.ENABLE_AI_ANALYSIS)
        self.assertIn("AI Analysis", app._primary_tab_labels())

    def test_model_presets_include_configured_custom_model(self) -> None:
        options = app._onemin_ai_model_options("custom-provider-model")
        self.assertEqual(options[0], "custom-provider-model")
        self.assertIn("gpt-5.6-sol", options)
        self.assertIn("gpt-5.6-terra", options)
        self.assertIn("gpt-5.6-luna", options)

    def test_nonfinite_app_timeout_uses_default(self) -> None:
        for invalid in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(invalid=invalid), patch.dict(os.environ, {"ONEMIN_AI_TIMEOUT_SECONDS": invalid}, clear=False):
                self.assertEqual(app._onemin_ai_timeout_seconds(), 180.0)

    def test_compaction_is_retained_and_manual_export_is_independent(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        with patch.object(app, "_build_ai_export_payload", return_value={"portfolio_snapshot": {"portfolio_value": 100}, "unified_snapshot": {"raw": "x" * 1000}}):
            compact, diagnostics = app._build_ai_analysis_payload_with_diagnostics(holdings, metrics, AI_ANALYSIS_TYPES[0], "")
        self.assertNotIn("unified_snapshot", compact)
        self.assertGreater(diagnostics["before_chars"], diagnostics["after_chars"])
        self.assertIn("# Dividend Portfolio Tracker Export", app._build_export_markdown(holdings, metrics))

    def test_clear_analysis_removes_neutral_state(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state = {
            "ai_analysis_result_markdown": "result",
            "ai_analysis_response_diagnostic": {"record_id": "record"},
            "ai_analysis_result_model": "gpt-5.6-sol",
            "ai_analysis_result_type": AI_ANALYSIS_TYPES[0],
            "ai_analysis_error_message": "error",
            "ai_analysis_error_detail": "detail",
            "ai_analysis_generated_at": "timestamp",
            "ai_analysis_context_diagnostics": {"after_chars": 1},
            "ai_analysis_busy": True,
            "ai_analysis_last_attempt_at": "attempt",
            "ai_analysis_last_stage": "success",
        }
        action = SimpleNamespace(button=MagicMock(return_value=False))
        clear = SimpleNamespace(button=MagicMock(return_value=True))
        with patch.object(app.st, "session_state", state), patch.object(app.st, "subheader"), patch.object(app.st, "caption"), patch.object(app.st, "info"), patch.object(app.st, "selectbox"), patch.object(app.st, "text_area"), patch.object(app.st, "columns", return_value=[action, clear]), patch.object(app.st, "rerun") as rerun:
            app._render_ai_analysis_tab(holdings, metrics)
        for key in ("ai_analysis_busy", "ai_analysis_last_attempt_at", "ai_analysis_last_stage", "ai_analysis_result_markdown", "ai_analysis_response_diagnostic", "ai_analysis_result_model", "ai_analysis_result_type", "ai_analysis_error_message", "ai_analysis_error_detail", "ai_analysis_generated_at", "ai_analysis_context_diagnostics"):
            self.assertNotIn(key, state)
        rerun.assert_called_once()

    def test_successful_callback_stores_result_and_makes_one_request(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state: dict[str, object] = {
            "ai_analysis_model": "gpt-5.6-sol",
            "ai_analysis_type": AI_ANALYSIS_TYPES[0],
            "ai_analysis_custom_question": "Review income durability.",
        }
        result = SimpleNamespace(
            content="# Completed Analysis",
            model="gpt-5.6-sol",
            response_diagnostic={"http_status": 200, "record_status": "SUCCESS"},
        )
        with patch.object(app.st, "session_state", state), patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "test-key"}, clear=False), patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 10})), patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}]), patch.object(app, "OneMinAIClient") as client_constructor, patch.object(app.st, "spinner", return_value=contextlib.nullcontext()), patch.object(app.st, "rerun") as rerun:
            client_constructor.return_value.chat_with_ai.return_value = result
            app._run_onemin_ai_analysis(holdings, metrics)

        self.assertEqual(state["ai_analysis_result_markdown"], "# Completed Analysis")
        self.assertEqual(state["ai_analysis_result_model"], "gpt-5.6-sol")
        self.assertEqual(state["ai_analysis_result_type"], AI_ANALYSIS_TYPES[0])
        self.assertIn("ai_analysis_generated_at", state)
        self.assertIn("ai_analysis_last_attempt_at", state)
        self.assertEqual(state["ai_analysis_last_stage"], "success")
        self.assertFalse(state["ai_analysis_busy"])
        client_constructor.return_value.chat_with_ai.assert_called_once()
        rerun.assert_not_called()

        action = SimpleNamespace(button=MagicMock(return_value=False))
        clear = SimpleNamespace(button=MagicMock(return_value=False))
        with patch.object(app.st, "session_state", state), patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "test-key"}, clear=False), patch.object(app, "OneMinAIClient") as next_client_constructor, patch.object(app.st, "subheader"), patch.object(app.st, "caption"), patch.object(app.st, "info"), patch.object(app.st, "selectbox"), patch.object(app.st, "text_area"), patch.object(app.st, "columns", return_value=[action, clear]), patch.object(app.st, "markdown") as markdown, patch.object(app.st, "expander", return_value=contextlib.nullcontext()), patch.object(app.st, "download_button"):
            app._render_ai_analysis_tab(holdings, metrics)

        next_client_constructor.assert_not_called()
        markdown.assert_any_call("# Completed Analysis")

    def test_failed_callback_stores_error_clears_busy_and_does_not_rerun(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state: dict[str, object] = {
            "ai_analysis_model": "gpt-5.6-sol",
            "ai_analysis_type": AI_ANALYSIS_TYPES[0],
            "ai_analysis_custom_question": "",
        }
        failure = OneMinAIResponseError("1min.AI rejected the request.", diagnostic={"http_status": 400, "provider_error_code": "INVALID_REQUEST"})
        with patch.object(app.st, "session_state", state), patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "test-key"}, clear=False), patch.object(app, "_build_ai_analysis_payload_with_diagnostics", return_value=({"portfolio_snapshot": {}}, {"after_chars": 10})), patch.object(app, "build_ai_analysis_messages", return_value=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}]), patch.object(app, "OneMinAIClient") as client_constructor, patch.object(app.st, "spinner", return_value=contextlib.nullcontext()), patch.object(app.st, "rerun") as rerun:
            client_constructor.return_value.chat_with_ai.side_effect = failure
            app._run_onemin_ai_analysis(holdings, metrics)

        self.assertEqual(state["ai_analysis_error_message"], "1min.AI returned an invalid or empty response.")
        self.assertIn("Provider error code: INVALID_REQUEST", str(state["ai_analysis_error_detail"]))
        self.assertFalse(state["ai_analysis_busy"])
        self.assertEqual(state["ai_analysis_last_stage"], "error")
        client_constructor.return_value.chat_with_ai.assert_called_once()
        rerun.assert_not_called()

    def test_stale_busy_flag_does_not_disable_button_or_submit_request(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state: dict[str, object] = {"ai_analysis_busy": True}
        action = SimpleNamespace(button=MagicMock(return_value=False))
        clear = SimpleNamespace(button=MagicMock(return_value=False))
        with patch.object(app.st, "session_state", state), patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "test-key"}, clear=False), patch.object(app, "OneMinAIClient") as client_constructor, patch.object(app.st, "subheader"), patch.object(app.st, "caption"), patch.object(app.st, "info"), patch.object(app.st, "selectbox"), patch.object(app.st, "text_area"), patch.object(app.st, "columns", return_value=[action, clear]):
            app._render_ai_analysis_tab(holdings, metrics)

        self.assertTrue(state["ai_analysis_busy"])
        self.assertFalse(action.button.call_args.kwargs["disabled"])
        client_constructor.assert_not_called()

    def test_analyze_button_uses_callback_with_copied_dataframes(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state: dict[str, object] = {}
        action = SimpleNamespace(button=MagicMock(return_value=False))
        clear = SimpleNamespace(button=MagicMock(return_value=False))
        with patch.object(app.st, "session_state", state), patch.dict(os.environ, {"ONEMIN_AI_API_KEY": "test-key"}, clear=False), patch.object(app.st, "subheader"), patch.object(app.st, "caption"), patch.object(app.st, "info"), patch.object(app.st, "selectbox"), patch.object(app.st, "text_area"), patch.object(app.st, "columns", return_value=[action, clear]):
            app._render_ai_analysis_tab(holdings, metrics)

        callback_kwargs = action.button.call_args.kwargs
        self.assertIs(callback_kwargs["on_click"], app._run_onemin_ai_analysis)
        copied_holdings, copied_metrics = callback_kwargs["args"]
        self.assertIsNot(copied_holdings, holdings)
        self.assertIsNot(copied_metrics, metrics)

    def test_missing_key_keeps_tab_visible_and_disables_analysis(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state: dict[str, object] = {}
        action = SimpleNamespace(button=MagicMock(return_value=False))
        clear = SimpleNamespace(button=MagicMock(return_value=False))
        info_mock = MagicMock()
        with patch.object(app.st, "session_state", state), patch.dict(os.environ, {"ONEMIN_AI_API_KEY": ""}, clear=False), patch.object(app.st, "subheader") as subheader, patch.object(app.st, "caption"), patch.object(app.st, "info", info_mock), patch.object(app.st, "selectbox"), patch.object(app.st, "text_area"), patch.object(app.st, "columns", return_value=[action, clear]):
            app._render_ai_analysis_tab(holdings, metrics)
        subheader.assert_called_once_with("AI Analysis")
        self.assertIn("Set ONEMIN_AI_API_KEY", str(info_mock.call_args))
        self.assertTrue(action.button.call_args.kwargs["disabled"])

    def test_download_uses_onemin_heading(self) -> None:
        holdings = pd.DataFrame([{"ticker": "HD", "shares": 1, "amount_invested": 100.0}])
        metrics = pd.DataFrame([{"ticker": "HD", "market_value": 100.0, "annual_income": 2.0}])
        state = {
            "ai_analysis_result_markdown": "# Analysis\n\nContent",
            "ai_analysis_result_model": "gpt-5.6-sol",
            "ai_analysis_result_type": AI_ANALYSIS_TYPES[0],
            "ai_analysis_generated_at": "2026-08-12T12:00:00",
        }
        action = SimpleNamespace(button=MagicMock(return_value=False))
        clear = SimpleNamespace(button=MagicMock(return_value=False))
        with patch.object(app.st, "session_state", state), patch.object(app.st, "subheader"), patch.object(app.st, "caption"), patch.object(app.st, "info"), patch.object(app.st, "selectbox"), patch.object(app.st, "text_area"), patch.object(app.st, "columns", return_value=[action, clear]), patch.object(app.st, "markdown"), patch.object(app.st, "download_button") as download_button:
            app._render_ai_analysis_tab(holdings, metrics)
        downloaded = download_button.call_args.kwargs["data"].decode("utf-8")
        self.assertIn("# 1min.AI Portfolio Analysis", downloaded)
        self.assertIn("gpt-5.6-sol", downloaded)
        self.assertIn("# Analysis", downloaded)

    def test_diagnostic_formatter_displays_safe_provider_error_fields(self) -> None:
        formatted = app._format_onemin_ai_response_diagnostic(
            {
                "http_status": 400,
                "provider_error_code": "INVALID_REQUEST",
                "provider_error_message": "Missing prompt object",
                "provider_validation_details": [{"field": "promptObject.prompt", "message": "is required"}],
            }
        )
        self.assertIn("Provider error code: INVALID_REQUEST", formatted)
        self.assertIn("Provider error message: Missing prompt object", formatted)
        self.assertIn("Validation: promptObject.prompt: is required", formatted)


if __name__ == "__main__":
    unittest.main()
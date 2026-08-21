from __future__ import annotations

from typing import Any

import pandas as pd

from nicegui_app.services.exports import build_export_payload
from nicegui_app.services.portfolio_data import load_history
from nicegui_app.services.yahoo import yahoo_intelligence_package
from portfolio_ai import AIAnalysisState, ai_options, build_portfolio_history_summary, compact_ai_context, run_ai_analysis as shared_run_ai_analysis


def compact_context(payload: dict[str, Any], analysis_type: str, instructions: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    return compact_ai_context(payload, analysis_type, instructions, portfolio_history_summary=build_portfolio_history_summary(history))


def _production_export_payload(holdings: list[dict[str, Any]], metrics: pd.DataFrame, history: list[dict[str, Any]], export_options: dict[str, Any]) -> dict[str, Any]:
    options = dict(export_options)
    if "external_context" not in options:
        yahoo = yahoo_intelligence_package(metrics)
        options["external_context"] = {
            "supplemental_investment_intelligence": yahoo.get("investment_intelligence", []),
            "historical_security_context": yahoo.get("historical_context", []),
            "dividend_seasonality": yahoo.get("seasonality", {}),
            "yahoo_metadata": [],
            "notes": ["Yahoo context was loaded on demand for this explicit AI request."],
        }
        options["fund_weights"] = yahoo.get("fund_weights", {})
    return build_export_payload(holdings, metrics, history=history, **options)


def build_ai_context(holdings: list[dict[str, Any]], metrics: pd.DataFrame, analysis_type: str, instructions: str, **export_options: Any) -> dict[str, Any]:
    history = export_options.pop("history", None)
    if history is None:
        history = load_history()
    return compact_ai_context(_production_export_payload(holdings, metrics, history, export_options), analysis_type, instructions)


def run_ai_analysis(holdings: list[dict[str, Any]], metrics: pd.DataFrame, analysis_type: str, model: str, instructions: str, *, state: AIAnalysisState | None = None, **export_options: Any) -> AIAnalysisState:
    history = export_options.pop("history", None)
    if history is None:
        history = load_history()
    payload = _production_export_payload(holdings, metrics, history, export_options)
    return shared_run_ai_analysis(payload, analysis_type, model, instructions, state=state)


__all__ = ["AIAnalysisState", "ai_options", "build_ai_context", "compact_context", "run_ai_analysis"]

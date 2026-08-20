from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ai_analysis_prompt import AI_ANALYSIS_TYPES, build_ai_analysis_messages, format_ai_analysis_markdown
from onemin_ai_client import OneMinAIClient, messages_to_prompt
from nicegui_app.services.portfolio_data import load_history


def _production_logic():
    # Importing app exposes calculation helpers but does not execute app.main().
    import app

    return app


def metrics_frame(holdings: list[dict[str, Any]]) -> pd.DataFrame:
    app = _production_logic()
    frame = pd.DataFrame(holdings)
    if frame.empty:
        return frame
    prices = {str(row["ticker"]): float(row.get("current_price", 0.0) or 0.0) for row in holdings}
    yields = {str(row["ticker"]): float(row.get("dividend_yield_pct", 0.0) or 0.0) for row in holdings}
    return app._compute_metrics(frame, prices, yields)


def analysis_package(metrics: pd.DataFrame) -> dict[str, Any]:
    app = _production_logic()
    totals = app._build_portfolio_level_calculations(metrics)
    analysis = app._build_portfolio_analysis_summary(
        metrics,
        app.DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
        8.0,
        app.ESPP_CONTRIB_BIWEEKLY,
    )
    snapshot = app._build_portfolio_snapshot(metrics)
    return {
        "totals": totals,
        "summary": analysis,
        "snapshot": snapshot,
        "concentration": app.evaluate_concentration(metrics),
        "asset_allocation": app._build_insights_asset_allocation(metrics).to_dict(orient="records"),
        "direct_sector": app._build_direct_sector_classification_records(metrics),
        "income_quality": app._build_income_quality_records(metrics),
        "contributions": app._build_contribution_context(metrics, metrics),
    }


def yahoo_intelligence_package(metrics: pd.DataFrame) -> dict[str, Any]:
    """Load existing Yahoo-backed analysis only after explicit user interaction."""
    app = _production_logic()
    tickers = tuple(metrics.get("ticker", pd.Series(dtype=str)).dropna().astype(str))
    lookthrough = app._resolve_lookthrough_sector_exposure(metrics)
    weights = app._latest_dividend_month_weights(tickers) if tickers else {}
    seasonality = app._build_monthly_income_seasonality(metrics, weights)
    historical = app._latest_historical_context(tickers) if tickers else {}
    return {
        "investment_intelligence": app._build_investment_intelligence_rows(metrics),
        "lookthrough": lookthrough.get("records", []),
        "unresolved_lookthrough": app._build_unresolved_fund_look_through_records(metrics),
        "seasonality": seasonality,
        "historical_context": [{**record, "ticker": ticker} for ticker, record in historical.items()],
    }


def insights_package(metrics: pd.DataFrame) -> dict[str, Any]:
    app = _production_logic()
    yield_summary = app._build_insights_yield_summary(metrics)
    contribution = app._build_insights_contribution_view(
        metrics,
        app.DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
        app.ESPP_CONTRIB_BIWEEKLY,
        app.ESPP_PURCHASE_FREQUENCY,
        app.ESPP_DISCOUNT,
    )
    allocation = app._build_insights_asset_allocation(metrics)
    concentration = app.evaluate_concentration(metrics)
    observations = [
        concentration.get("guidance", ""),
        f"Largest holding: {concentration.get('largest_holding_by_value', 'N/A')} ({float(concentration.get('largest_holding_weight_pct', 0.0) or 0.0):.1f}%).",
        f"Highest resolved yield: {yield_summary.get('highest_yield_ticker', 'N/A')} ({float(yield_summary.get('highest_yield_pct', 0.0) or 0.0):.2f}%).",
        f"Active contribution destinations: {int(contribution.get('active_contributors', 0) or 0)}.",
    ]
    return {
        "yield": yield_summary,
        "contribution": {**contribution, "rows": contribution["rows"].to_dict(orient="records")},
        "allocation": allocation.to_dict(orient="records"),
        "concentration": concentration,
        "observations": [item for item in observations if item],
    }


def health_package(metrics: pd.DataFrame) -> dict[str, Any]:
    return _production_logic()._build_portfolio_health(metrics)


def export_package(holdings: list[dict[str, Any]], metrics: pd.DataFrame) -> dict[str, str]:
    app = _production_logic()
    holdings_frame = pd.DataFrame(holdings)
    payload = app._build_ai_export_payload(holdings_frame, metrics)
    return {
        "holdings.csv": metrics.to_csv(index=False),
        "portfolio_export.md": app.generate_export(payload),
        "portfolio_export.json": json.dumps(payload, indent=2, default=str, allow_nan=False),
    }


def ai_options() -> dict[str, list[str] | str | bool]:
    app = _production_logic()
    default_model = app._onemin_ai_default_model()
    return {
        "analysis_types": list(AI_ANALYSIS_TYPES),
        "models": app._onemin_ai_model_options(default_model),
        "default_model": default_model,
        "configured": bool(os.environ.get("ONEMIN_AI_API_KEY", "").strip()),
    }


@dataclass(frozen=True)
class AIResult:
    markdown: str
    model: str
    record_id: str


def run_ai_analysis(
    holdings: list[dict[str, Any]],
    metrics: pd.DataFrame,
    analysis_type: str,
    model: str,
    instructions: str,
) -> AIResult:
    """Run only from an explicit UI event; performs no database operations."""
    app = _production_logic()
    full_payload = app._build_ai_export_payload(pd.DataFrame(holdings), metrics)
    history_summary = app._build_compact_portfolio_history_summary(load_history())
    seasonality_summary: dict[str, Any] = {}
    if analysis_type in {"Comprehensive Portfolio Review", "Income and Dividend Analysis"}:
        tickers = tuple(metrics.get("ticker", pd.Series(dtype=str)).dropna().astype(str))
        weights = app._latest_dividend_month_weights(tickers) if tickers else {}
        seasonality_summary = app._build_compact_seasonality_summary(
            app._build_monthly_income_seasonality(metrics, weights)
        )
    payload = app._compact_analysis_context(
        full_payload, analysis_type, instructions,
        portfolio_history_summary=history_summary,
        dividend_seasonality_summary=seasonality_summary,
    )
    messages = build_ai_analysis_messages(payload, analysis_type, instructions)
    client = OneMinAIClient(
        api_key=os.environ.get("ONEMIN_AI_API_KEY"),
        base_url=app._onemin_ai_base_url(),
        timeout_seconds=app._onemin_ai_timeout_seconds(),
    )
    response = client.chat_with_ai(model=model, prompt=messages_to_prompt(messages), web_search=False)
    return AIResult(
        markdown=format_ai_analysis_markdown(response.content),
        model=response.model,
        record_id=response.record_id,
    )

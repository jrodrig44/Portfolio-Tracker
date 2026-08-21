from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import pandas as pd

from portfolio_analysis import build_analysis
from portfolio_health import build_portfolio_health
from portfolio_history import build_history_package
from portfolio_insights import build_portfolio_insights


def _sanitize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _sanitize(value.item())
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    return str(value)


def build_export_payload(holdings: list[dict[str, Any]], metrics: pd.DataFrame, *, history: list[dict[str, Any]] | None = None, fund_weights: dict[str, dict[str, float]] | None = None, external_context: dict[str, Any] | None = None, generated_at: str | None = None) -> dict[str, Any]:
    """Build a comprehensive read-only export from the shared portfolio packages."""
    analysis = build_analysis(metrics, fund_weights=fund_weights)
    health = build_portfolio_health(metrics, fund_weights=fund_weights)
    insights = build_portfolio_insights(metrics, fund_weights=fund_weights)
    historical = build_history_package(history or [], "ALL")
    totals = analysis.get("totals", {})
    contribution = insights["contribution"]
    summary = contribution["summary"]
    payload = {
        "snapshot_timestamp": generated_at or datetime.now().isoformat(timespec="seconds"), "currency": "USD", "export_version": "2.0", "schema_version": "2.0",
        "totals": {"portfolio_value": totals.get("total_portfolio_value", 0.0), "cost_basis": totals.get("total_cost_basis", 0.0), "unrealized_gain": totals.get("total_unrealized_gain", 0.0), "annual_income": totals.get("total_estimated_annual_income", 0.0), "weighted_yield_pct": totals.get("weighted_portfolio_yield", 0.0), "holdings_count": len(metrics.index), "largest_holding": totals.get("largest_holding", "N/A"), "largest_holding_weight_pct": totals.get("largest_holding_weight", 0.0)},
        "holdings": metrics.to_dict(orient="records"),
        "allocation": {"asset_type": analysis.get("asset_allocation", []), "economic_exposure": totals.get("economic_exposure_allocation", [])},
        "exposure": {"direct_sector_exposure": analysis.get("direct_sector", []), "look_through_exposure": analysis.get("fund_lookthrough", {}).get("records", []), "unresolved_fund_exposure": analysis.get("fund_lookthrough", {}).get("unresolved_records", []), "data_completeness": analysis.get("data_completeness", {})},
        "income": {"by_asset_type": totals.get("income_allocation_by_asset_type", []), "quality": analysis.get("income_quality", [])},
        "contributions": {"regular_biweekly": summary["regular_biweekly"], "espp_biweekly": summary["espp_biweekly"], "combined_recurring_biweekly": summary["combined_biweekly"], "regular_annual": summary["regular_annual"], "espp_annual": summary["espp_annual"], "combined_recurring_annual": summary["combined_annual"], "special_contribution": summary.get("special_contribution", 0.0), "distribution_by_holding": contribution["rows"], "employer_stock_context": analysis.get("contributions", {}).get("employer_stock_rows", [])},
        "projections": analysis.get("snapshot", {}), "health_score": health, "insights": {"observations": insights["observations"]}, "historical": historical,
        "external_context": external_context or {"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "notes": ["External Yahoo enrichment was not supplied for this export."]},
        "data_limitations": ["Historical portfolio values are nominal account values, not contribution-adjusted investment returns.", "Missing historical periods are not interpolated.", "Fund look-through is limited to supplied underlying sector weights."],
    }
    return _sanitize(payload)


def build_export_json(payload: dict[str, Any]) -> str:
    return json.dumps(_sanitize(payload), indent=2, allow_nan=False)


def _money(value: Any) -> str:
    return f"${float(value or 0.0):,.2f}"


def build_export_markdown(payload: dict[str, Any]) -> str:
    payload = _sanitize(payload); totals = payload["totals"]; health = payload["health_score"]; contributions = payload["contributions"]; history = payload["historical"]
    sections = ["# Dividend Portfolio Tracker Export", "", "## AI Context Summary", f"- Total Portfolio Value: {_money(totals['portfolio_value'])}", f"- Cost Basis: {_money(totals['cost_basis'])}", f"- Unrealized Gain/Loss: {_money(totals['unrealized_gain'])}", f"- Estimated Annual Income: {_money(totals['annual_income'])}", f"- Weighted Portfolio Yield: {float(totals['weighted_yield_pct'] or 0.0):.2f}%", f"- Number of Holdings: {totals['holdings_count']}", f"- Largest Holding: {totals['largest_holding']} ({float(totals['largest_holding_weight_pct'] or 0.0):.2f}%)", "", "## Portfolio Health", f"- Tracker Health Score: {float(health.get('health_score', 0.0) or 0.0):.1f}/100", f"- Health Score Basis: {health.get('methodology', health.get('health_score_methodology', 'Shared production health engine'))}", f"- Strongest Areas: {json.dumps(health.get('strongest_areas', []), allow_nan=False)}", f"- Weakest Areas: {json.dumps(health.get('weakest_areas', []), allow_nan=False)}", f"- Disclaimer: {health.get('disclaimer', '')}", "", "## Employer Stock / ESPP Context", f"- Regular recurring biweekly: {_money(contributions['regular_biweekly'])}", f"- ESPP recurring biweekly: {_money(contributions['espp_biweekly'])}", f"- Combined recurring annual: {_money(contributions['combined_recurring_annual'])}", f"- Special contribution (not annualized): {_money(contributions['special_contribution'])}", "", "## History / Performance Context", "- Historical change below is nominal portfolio value change, not investment return.", f"- Starting portfolio value: {_money(history['summary']['starting_portfolio_value'])}", f"- Ending portfolio value: {_money(history['summary']['ending_portfolio_value'])}", f"- Nominal value change: {_money(history['summary']['absolute_change'])} ({float(history['summary']['percentage_change'] or 0.0):.2f}%)"]
    json_sections = (("Holdings Detail", payload["holdings"]), ("Asset Allocation", payload["allocation"]["asset_type"]), ("Economic Exposure Allocation", payload["allocation"]["economic_exposure"]), ("Direct Sector Exposure", payload["exposure"]["direct_sector_exposure"]), ("Look-Through Sector Exposure", payload["exposure"]["look_through_exposure"]), ("Unresolved Fund Look-Through Exposure", payload["exposure"]["unresolved_fund_exposure"]), ("Data Completeness", payload["exposure"]["data_completeness"]), ("Income Allocation and Quality", payload["income"]), ("Contribution Allocation", contributions["distribution_by_holding"]), ("Projections", payload["projections"]), ("Historical Portfolio Values", history["records"]), ("Supplemental External Context", payload["external_context"]))
    for title, content in json_sections:
        sections.extend(["", f"## {title}", "```json", json.dumps(content, indent=2, allow_nan=False), "```"])
    sections.extend(["", "## Data Limitations", *[f"- {note}" for note in payload["data_limitations"]]])
    return "\n".join(sections)


def build_export_package(holdings: list[dict[str, Any]], metrics: pd.DataFrame, **kwargs: Any) -> dict[str, str]:
    payload = build_export_payload(holdings, metrics, **kwargs)
    return {"holdings.csv": metrics.to_csv(index=False), "portfolio_export.md": build_export_markdown(payload), "portfolio_export.json": build_export_json(payload)}

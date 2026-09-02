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
from hd_espp_integration import load_hd_historical_integration_context


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


def build_export_payload(holdings: list[dict[str, Any]], metrics: pd.DataFrame, *, history: list[dict[str, Any]] | None = None, fund_weights: dict[str, dict[str, float]] | None = None, external_context: dict[str, Any] | None = None, hd_historical_context: dict[str, Any] | None = None, generated_at: str | None = None, use_export_json_data: bool = False, strict_export_accuracy: bool = False, current_live_export: bool = True, projection_biweekly: float = 280.0, projection_return_pct: float = 8.0, espp_biweekly: float = 100.0, espp_purchase_frequency: str = "6_months", espp_discount: float = 0.15) -> dict[str, Any]:
    """Build a comprehensive read-only export from the shared portfolio packages."""
    prepared_metrics = metrics.copy()
    if not prepared_metrics.empty and "ticker" in prepared_metrics:
        hd = prepared_metrics["ticker"].fillna("").astype(str).str.upper().eq("HD")
        if hd.any():
            prepared_metrics.loc[hd, "regular_biweekly_contribution"] = 0.0
            prepared_metrics.loc[hd, "special_contribution"] = 0.0
            prepared_metrics.loc[hd, "espp_biweekly_contribution"] = float(espp_biweekly)
            prepared_metrics.loc[hd, "espp_discount_pct"] = float(espp_discount) * 100.0 if abs(float(espp_discount)) <= 1.0 else float(espp_discount)
            prepared_metrics.loc[hd, "employer_stock"] = True
            prepared_metrics.loc[hd, "espp_enabled"] = True
    hd_context = hd_historical_context if isinstance(hd_historical_context, dict) else load_hd_historical_integration_context(prepared_metrics)
    analysis = build_analysis(prepared_metrics, fund_weights=fund_weights, expected_return=projection_return_pct, projection_biweekly=projection_biweekly, hd_historical_context=hd_context)
    health = build_portfolio_health(prepared_metrics, fund_weights=fund_weights)
    insights = build_portfolio_insights(prepared_metrics, fund_weights=fund_weights, hd_historical_context=hd_context)
    historical = build_history_package(history or [], "ALL")
    totals = analysis.get("totals", {})
    contribution = insights["contribution"]
    summary = contribution["summary"]
    contribution_rows = [dict(row) for row in contribution["rows"]]
    selected_discount_pct = float(espp_discount) * 100.0 if abs(float(espp_discount)) <= 1.0 else float(espp_discount)
    for row in contribution_rows:
        if str(row.get("ticker", "")).strip().upper() == "HD":
            row["espp_discount_pct"] = selected_discount_pct
    external = external_context or {"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "yahoo_metadata": [], "notes": ["External Yahoo enrichment was not supplied for this export."]}
    data_quality = analysis.get("data_completeness", {})
    payload = {
        "snapshot_timestamp": generated_at or datetime.now().isoformat(timespec="seconds"), "currency": "USD", "export_version": "2.0", "schema_version": "2.0",
        "export_accuracy": {"use_export_json_data": bool(use_export_json_data), "strict_export_accuracy": bool(strict_export_accuracy), "current_live_export": bool(current_live_export), "market_data_resolution": "already_resolved_metrics"},
        "totals": {"portfolio_value": totals.get("total_portfolio_value", 0.0), "cost_basis": totals.get("total_cost_basis", 0.0), "unrealized_gain": totals.get("total_unrealized_gain", 0.0), "annual_income": totals.get("total_estimated_annual_income", 0.0), "weighted_yield_pct": totals.get("weighted_portfolio_yield", 0.0), "holdings_count": len(metrics.index), "largest_holding": totals.get("largest_holding", "N/A"), "largest_holding_weight_pct": totals.get("largest_holding_weight", 0.0)},
        "holdings": prepared_metrics.to_dict(orient="records"),
        "allocation": {"asset_type": analysis.get("asset_allocation", []), "economic_exposure": totals.get("economic_exposure_allocation", [])},
        "exposure": {"direct_sector_exposure": analysis.get("direct_sector", []), "look_through_exposure": analysis.get("fund_lookthrough", {}).get("records", []), "unresolved_fund_exposure": analysis.get("fund_lookthrough", {}).get("unresolved_records", []), "data_completeness": analysis.get("data_completeness", {})},
        "income": {"by_asset_type": totals.get("income_allocation_by_asset_type", []), "quality": analysis.get("income_quality", [])},
        "contributions": {"regular_biweekly": summary["regular_biweekly"], "espp_biweekly": summary["espp_biweekly"], "combined_recurring_biweekly": summary["combined_biweekly"], "regular_annual": summary["regular_annual"], "espp_annual": summary["espp_annual"], "combined_recurring_annual": summary["combined_annual"], "special_contribution": summary.get("special_contribution", 0.0), "distribution_by_holding": contribution_rows, "employer_stock_context": analysis.get("contributions", {}).get("employer_stock_rows", []), "espp_context": {"biweekly_contribution": float(espp_biweekly), "purchase_frequency": str(espp_purchase_frequency), "discount_pct": selected_discount_pct}},
        "concentration": analysis.get("concentration", {}), "projections": analysis.get("snapshot", {}), "health_score": health, "alerts": {"health_alerts": health.get("alerts", [])}, "insights": {"observations": insights["observations"]}, "historical": historical,
        "external_context": external,
        "hd_historical_context": hd_context,
        "hd_historical_metadata": {"hd_historical_data_available": bool(hd_context.get("available")), "hd_historical_document_count": (hd_context.get("summary") or {}).get("hd_historical_document_count", 0), "hd_historical_phase3a_status": (hd_context.get("summary") or {}).get("phase3a_status"), "hd_historical_lifetime_return_status": (hd_context.get("summary") or {}).get("lifetime_return_status")},
        "dividend_data_metadata": external.get("yahoo_metadata", []),
        "supplemental_investment_intelligence": external.get("supplemental_investment_intelligence", []),
        "historical_security_context": external.get("historical_security_context", []),
        "dividend_seasonality": external.get("dividend_seasonality", {}),
        "data_quality_issues": health.get("notes", []),
        "data_completeness": data_quality,
        "analysis_guidance": ["Evaluate capital concentration and income concentration separately.", "Direct sector exposure and fund look-through exposure use distinct classifications."],
        "investor_profile": external.get("investor_profile", {}),
        "recommendation_constraints": external.get("recommendation_constraints", {}),
        "field_definitions": {"portfolio_value": "Current nominal market value from already-resolved metrics.", "weighted_yield_pct": "Estimated annual income divided by current portfolio value, expressed from 0 to 100.", "special_contribution": "Separate additional contribution excluded from recurring annual totals."},
        "export_assumptions": {"projection_biweekly": float(projection_biweekly), "projection_return_pct": float(projection_return_pct), "espp_biweekly": float(espp_biweekly), "espp_purchase_frequency": str(espp_purchase_frequency), "espp_discount_pct": selected_discount_pct},
        "data_limitations": ["Historical portfolio values are nominal account values, not contribution-adjusted investment returns.", "Missing historical periods are not interpolated.", "Fund look-through is limited to supplied underlying sector weights."],
    }
    if payload["projections"]:
        payload["projections"]["projection_contribution_note"] = f"Projection calculations use the regular ${float(projection_biweekly):,.2f} biweekly contribution assumption at {float(projection_return_pct):g}% expected annual return and exclude ESPP contributions unless otherwise stated."
    return _sanitize(payload)


def build_export_json(payload: dict[str, Any]) -> str:
    return json.dumps(_sanitize(payload), indent=2, allow_nan=False)


def _money(value: Any) -> str:
    return f"${float(value or 0.0):,.2f}"


def _display(value: Any) -> str:
    return "Unknown" if value is None or value == "" else str(value)


def _pct(value: Any) -> str:
    if value is None or value == "":
        return "Unknown"
    number = float(value)
    if 0 <= abs(number) <= 1:
        number *= 100
    return f"{number:.2f}%"


def build_export_markdown(payload: dict[str, Any]) -> str:
    payload = _sanitize(payload); totals = payload["totals"]; health = payload["health_score"]; contributions = payload["contributions"]; history = payload["historical"]
    sections = ["# Dividend Portfolio Tracker Export", "", "## AI Context Summary", f"- Total Portfolio Value: {_money(totals['portfolio_value'])}", f"- Cost Basis: {_money(totals['cost_basis'])}", f"- Unrealized Gain/Loss: {_money(totals['unrealized_gain'])}", f"- Estimated Annual Income: {_money(totals['annual_income'])}", f"- Weighted Portfolio Yield: {float(totals['weighted_yield_pct'] or 0.0):.2f}%", f"- Number of Holdings: {totals['holdings_count']}", f"- Largest Holding: {totals['largest_holding']} ({float(totals['largest_holding_weight_pct'] or 0.0):.2f}%)", "", "## Portfolio Health", f"- Tracker Health Score: {float(health.get('health_score', 0.0) or 0.0):.1f}/100", f"- Health Score Basis: {health.get('methodology', health.get('health_score_methodology', 'Shared production health engine'))}", f"- Strongest Areas: {json.dumps(health.get('strongest_areas', []), allow_nan=False)}", f"- Weakest Areas: {json.dumps(health.get('weakest_areas', []), allow_nan=False)}", f"- Disclaimer: {health.get('disclaimer', '')}", "", "## Employer Stock / ESPP Context", f"- Regular recurring biweekly: {_money(contributions['regular_biweekly'])}", f"- ESPP recurring biweekly: {_money(contributions['espp_biweekly'])}", f"- Combined recurring annual: {_money(contributions['combined_recurring_annual'])}", f"- Special contribution (not annualized): {_money(contributions['special_contribution'])}", "", "## History / Performance Context", "- Historical change below is nominal portfolio value change, not investment return.", f"- Starting portfolio value: {_money(history['summary']['starting_portfolio_value'])}", f"- Ending portfolio value: {_money(history['summary']['ending_portfolio_value'])}", f"- Nominal value change: {_money(history['summary']['absolute_change'])} ({float(history['summary']['percentage_change'] or 0.0):.2f}%)"]
    json_sections = (("Holdings Detail", payload["holdings"]), ("Asset Allocation", payload["allocation"]["asset_type"]), ("Economic Exposure Allocation", payload["allocation"]["economic_exposure"]), ("Exposure Interpretation", payload["analysis_guidance"]), ("Direct Sector Exposure", payload["exposure"]["direct_sector_exposure"]), ("Direct Holdings by Sector", payload["holdings"]), ("Look-Through Sector Exposure", payload["exposure"]["look_through_exposure"]), ("Unresolved Fund Look-Through Exposure", payload["exposure"]["unresolved_fund_exposure"]), ("Data Completeness", payload["exposure"]["data_completeness"]), ("Concentration", payload["concentration"]), ("Income Allocation and Quality", payload["income"]), ("Income Allocation by Holding", payload["holdings"]), ("Income Allocation by Asset Type", payload["income"]["by_asset_type"]), ("Income Quality", payload["income"]["quality"]), ("Contribution Allocation", contributions["distribution_by_holding"]), ("Projections", payload["projections"]), ("Historical Portfolio Values", history["records"]), ("Dividend Data Metadata", payload["dividend_data_metadata"]), ("Supplemental Investment Intelligence", payload["supplemental_investment_intelligence"]), ("Historical Security Context", payload["historical_security_context"]), ("Dividend Seasonality", payload["dividend_seasonality"]), ("Supplemental External Context", payload["external_context"]))
    hd_context = payload.get("hd_historical_context", {}) if isinstance(payload.get("hd_historical_context"), dict) else {}
    if hd_context.get("available"):
        economics = hd_context.get("documented_economics", {})
        realized = hd_context.get("realized", {})
        coverage = hd_context.get("coverage", {})
        reconciliation = hd_context.get("reconciliation", {})
        summary = hd_context.get("summary", {})
        sections.extend(["", "## HD / ESPP Historical Economics", "- Coverage: Source-bounded documented HD / ESPP history.", f"- Documented ESPP contributions: {_money(economics.get('espp_employee_contributions'))}", f"- ESPP shares purchased: {_display(economics.get('espp_shares'))}", f"- Purchase discount benefit: {_money(economics.get('espp_purchase_discount_benefit'))}", f"- Effective documented discount: {_pct(economics.get('effective_documented_discount_pct'))}", f"- DRIP gross dividends: {_money(economics.get('drip_gross_dividends'))}; net reinvested: {_money(economics.get('drip_net_reinvested'))}; shares: {_display(economics.get('drip_shares'))}", f"- Documented cash dividends: {_money(economics.get('documented_cash_dividend_total'))}", f"- Documented sale proceeds: {_money(realized.get('aggregate_sale_proceeds'))}", f"- Documented realized gain: {_money(realized.get('documented_realized_gain'))} ({_pct(coverage.get('basis_coverage_pct'))} basis coverage)", f"- Origin coverage: {_pct(coverage.get('origin_coverage_pct'))}", f"- Phase 3A status: {reconciliation.get('phase3a_status')}", f"- Historical/live difference: {_display(reconciliation.get('historical_live_difference'))} shares", f"- Lifetime total return: {summary.get('lifetime_return_status')}", "", "### HD Realized By Origin", "```json", json.dumps(hd_context.get('realized', {}).get('by_origin', []), indent=2, allow_nan=False), "```", "", "### HD Performance Availability", "```json", json.dumps(hd_context.get('performance_availability', []), indent=2, allow_nan=False), "```", "", "### HD Historical Issues", "```json", json.dumps(hd_context.get('issues', []), indent=2, allow_nan=False), "```"])
    else:
        sections.extend(["", "## HD / ESPP Historical Economics", f"- {_display((hd_context.get('summary') or {}).get('reason', 'No HD historical data imported'))}"])
    for title, content in json_sections:
        sections.extend(["", f"## {title}", "```json", json.dumps(content, indent=2, allow_nan=False), "```"])
    sections.extend(["", "## Data Limitations", *[f"- {note}" for note in payload["data_limitations"]]])
    return "\n".join(sections)


def build_export_package(holdings: list[dict[str, Any]], metrics: pd.DataFrame, **kwargs: Any) -> dict[str, str]:
    payload = build_export_payload(holdings, metrics, **kwargs)
    return {"holdings.csv": metrics.to_csv(index=False), "portfolio_export.md": build_export_markdown(payload), "portfolio_export.json": build_export_json(payload)}

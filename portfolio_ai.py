from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from ai_analysis_prompt import AI_ANALYSIS_TYPES, build_ai_analysis_messages, format_ai_analysis_markdown
from onemin_ai_client import (
    ONEMIN_AI_DEFAULT_BASE_URL,
    ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ONEMIN_AI_DEFAULT_MODEL,
    ONEMIN_AI_TIMEOUT_SECONDS,
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


ONEMIN_AI_RECOMMENDED_MODELS = [
    "gpt-4.1-mini",
    "gemini-2.5-flash",
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
]
ONEMIN_AI_MODEL_DETAILS = {
    "gpt-4.1-mini": ("GPT-4.1 Mini", "Recommended balance of analysis quality, speed, and credit usage."),
    "gemini-2.5-flash": ("Gemini 2.5 Flash", "Fast analysis for larger portfolio contexts."),
    "claude-haiku-4-5-20251001": ("Claude Haiku 4.5", "Careful explanations and detailed portfolio/ESPP instructions."),
    "gpt-4o-mini": ("GPT-4o Mini", "Economical fallback for basic summaries and simple questions."),
}


def ai_options() -> dict[str, Any]:
    default = str(os.environ.get("ONEMIN_AI_MODEL", "")).strip() or ONEMIN_AI_DEFAULT_MODEL
    return {
        "analysis_types": list(AI_ANALYSIS_TYPES),
        "models": list(dict.fromkeys([default, *ONEMIN_AI_RECOMMENDED_MODELS])),
        "default_model": default,
        "configured": bool(str(os.environ.get("ONEMIN_AI_API_KEY", "")).strip()),
        "model_details": {
            key: {"label": value[0], "description": value[1]} for key, value in ONEMIN_AI_MODEL_DETAILS.items()
        },
    }


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (list, dict)) and not value)


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): cleaned for key, item in value.items() if not _empty(cleaned := _strip(item))}
    if isinstance(value, list):
        return [cleaned for item in value if not _empty(cleaned := _strip(item))]
    return value


def _pick(source: Any, fields: list[str]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields if isinstance(source, dict) and not _empty(source.get(field))}


def _records(records: Any, fields: list[str], limit: int, sort_by: str | None = None) -> list[dict[str, Any]]:
    rows = [row for row in records if isinstance(row, dict)] if isinstance(records, list) else []
    if sort_by:
        rows.sort(key=lambda row: _finite(row.get(sort_by)), reverse=True)
    return [cleaned for row in rows[:limit] if (cleaned := _strip({key: row.get(key) for key in fields if key in row}))]


def _supplemental(records: Any, priority: list[str], limit: int) -> list[dict[str, Any]]:
    by_ticker = {str(row.get("ticker", "")).strip().upper(): row for row in records if isinstance(row, dict) and str(row.get("ticker", "")).strip()} if isinstance(records, list) else {}
    order = list(dict.fromkeys([*(ticker for ticker in priority if ticker in by_ticker), *by_ticker]))
    output = []
    for ticker in order[:limit]:
        row = by_ticker[ticker]
        fund = row.get("fund_data", {}) if isinstance(row.get("fund_data"), dict) else {}
        fundamental_source = row.get("fundamental_metrics", {}) if isinstance(row.get("fundamental_metrics"), dict) else {}
        if not fundamental_source:
            fundamental_source = {"trailing_pe": row.get("trailing_pe"), "market_cap": row.get("market_cap")}
        output.append(_strip({
            "ticker": ticker,
            "fundamentals": _pick(fundamental_source, ["dividend_yield", "dividend_rate", "payout_ratio", "trailing_pe", "forward_pe", "beta", "debt_to_equity", "interest_coverage", "market_cap"]),
            "growth_and_estimates": _pick(row.get("earnings_and_estimates", {}), ["earnings_growth", "revenue_growth", "eps_current_year", "eps_next_year", "next_earnings_date"]),
            "analyst_signal": _pick(row.get("analyst_targets", {}), ["recommendation_key", "target_mean_price", "target_high_price", "target_low_price"]),
            "fund_sector_weights": _records(fund.get("sector_weight_summary", []), ["sector", "weight_pct"], 4, "weight_pct"),
            "data_source": row.get("data_source", "Yahoo Finance"),
        }))
    return output


def _warnings(full: dict[str, Any]) -> list[str]:
    output = [str(item).strip() for item in full.get("data_quality_issues", []) if str(item).strip()] if isinstance(full.get("data_quality_issues"), list) else []
    warning_data = full.get("warnings", {})
    if isinstance(warning_data, dict):
        for key in ("snapshot_validation_errors", "missing_export_fields"):
            if isinstance(warning_data.get(key), list): output.extend(str(item).strip() for item in warning_data[key] if str(item).strip())
    completeness = full.get("data_completeness", {})
    if isinstance(completeness, dict):
        for key, label in (("direct_sector_classification_coverage_pct", "Direct sector classification"), ("fund_lookthrough_coverage_pct", "Fund look-through"), ("overall_resolved_exposure_coverage_pct", "Overall resolved exposure")):
            value = _finite(completeness.get(key))
            if value < 90: output.append(f"{label} coverage is low ({value:.1f}%).")
    unresolved = full.get("unresolved_exposure", {})
    if isinstance(unresolved, dict) and _finite(unresolved.get("unresolved_fund_exposure")) > 0:
        output.append(f"Unresolved fund look-through exposure remains in portfolio totals (${_finite(unresolved.get('unresolved_fund_exposure')):,.2f}).")
    return list(dict.fromkeys(output))


def build_portfolio_history_summary(history: Any) -> dict[str, Any]:
    if not isinstance(history, list):
        return {}
    points = []
    for row in history:
        if not isinstance(row, dict):
            continue
        date = pd.to_datetime(row.get("history_date"), errors="coerce")
        value = pd.to_numeric(row.get("portfolio_value"), errors="coerce")
        if not pd.isna(date) and not pd.isna(value):
            points.append((date.normalize(), float(value)))
    if not points:
        return {}
    points.sort(key=lambda item: item[0])
    earliest_date, earliest_value = points[0]
    latest_date, latest_value = points[-1]

    def baseline(months: int, minimum_days: int) -> dict[str, Any]:
        if (latest_date - earliest_date).days < minimum_days:
            return {}
        target = latest_date - pd.DateOffset(months=months)
        date, value = min(points, key=lambda item: (abs((item[0] - target).days), item[0]))
        if abs((date - target).days) > 35:
            return {}
        return {"date": date.date().isoformat(), "value": round(value, 2), "nominal_change_pct": round((latest_value / value - 1) * 100, 2) if value else None}

    result = {
        "latest_date": latest_date.date().isoformat(), "latest_value": round(latest_value, 2),
        "earliest_date": earliest_date.date().isoformat(), "earliest_value": round(earliest_value, 2),
        "point_count": len(points),
        "interpretation": "Nominal account-value change includes contributions/withdrawals and is not investment return.",
    }
    if value := baseline(12, 330):
        result["one_year_baseline"] = value
    if value := baseline(3, 75):
        result["three_month_baseline"] = value
    return result


def build_seasonality_summary(seasonality: Any) -> dict[str, Any]:
    if not isinstance(seasonality, dict) or not seasonality.get("chart_available") or not isinstance(seasonality.get("monthly_income"), dict):
        return {}
    monthly = {str(month): _finite(value) for month, value in seasonality["monthly_income"].items()}
    if not monthly:
        return {}
    high, low = max(monthly.values()), min(monthly.values())
    return {"highest_income_months": [m for m, v in monthly.items() if v == high], "lowest_income_months": [m for m, v in monthly.items() if v == low], "highest_month_income": round(high, 2), "lowest_month_income": round(low, 2), "timing_coverage_pct": round(_finite(seasonality.get("coverage_pct")) * 100, 2), "uncovered_annual_income": round(_finite(seasonality.get("uncovered_annual_income")), 2)}


def _production_shape(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the Phase 1F canonical export to the production AI-export shape."""
    if "portfolio_snapshot" in payload:
        return payload
    totals, contributions = payload.get("totals", {}), payload.get("contributions", {})
    health, exposure = payload.get("health_score", {}), payload.get("exposure", {})
    concentration = payload.get("concentration", {})
    employer_rows = contributions.get("employer_stock_context", []) if isinstance(contributions, dict) else []
    employer = employer_rows[0] if employer_rows else {}
    annual_regular = _finite(contributions.get("regular_annual"))
    annual_espp = _finite(contributions.get("espp_annual"))
    external = payload.get("external_context", {}) if isinstance(payload.get("external_context"), dict) else {}
    history = payload.get("historical", {}) if isinstance(payload.get("historical"), dict) else {}
    return {
        "portfolio_snapshot": {**totals, "top_holding": totals.get("largest_holding"), "top_holding_weight_pct": totals.get("largest_holding_weight_pct"), **(payload.get("projections", {}) or {})},
        "calculated_metrics": payload.get("holdings", []),
        "asset_allocation": (payload.get("allocation", {}) or {}).get("asset_type", []),
        "economic_exposure_allocation": (payload.get("allocation", {}) or {}).get("economic_exposure", []),
        "direct_sector_exposure": exposure.get("direct_sector_exposure", []),
        "look_through_sector_exposure": exposure.get("look_through_exposure", []),
        "unresolved_fund_look_through_exposure": exposure.get("unresolved_fund_exposure", []),
        "data_completeness": exposure.get("data_completeness", payload.get("data_completeness", {})),
        "unresolved_exposure": {"unresolved_fund_exposure": sum(_finite(row.get("market_value", row.get("Market_Value"))) for row in exposure.get("unresolved_fund_exposure", []) if isinstance(row, dict))},
        "income_allocation_by_holding": [{"ticker": row.get("ticker"), "annual_income": row.get("annual_income"), "market_value": row.get("market_value")} for row in payload.get("holdings", []) if isinstance(row, dict)],
        "income_quality": health.get("income_quality", {}),
        "portfolio_health": {**health, "health_score_breakdown": health.get("subscores", health.get("health_score_breakdown", {})), "context_summary": {"score": health.get("health_score", health.get("overall_score", 0)), "score_label": "Tracker-generated score", "strongest_areas": health.get("strongest_areas", []), "weakest_areas": health.get("weakest_areas", []), "primary_alert": health.get("primary_alert", "")}},
        "concentration_context": {"largest_holding_by_value": concentration.get("largest_holding", totals.get("largest_holding")), "largest_holding_value_weight_pct": concentration.get("largest_holding_weight", totals.get("largest_holding_weight_pct")), "largest_income_contributor": concentration.get("largest_income_contributor"), "largest_income_weight_pct": concentration.get("largest_income_weight"), "guidance": "Capital concentration and income concentration should be evaluated separately."},
        "contribution_context": {"summary": {"total_regular_biweekly_contribution": contributions.get("regular_biweekly"), "total_espp_biweekly_contributions": contributions.get("espp_biweekly"), "estimated_annual_regular_contributions": annual_regular, "estimated_annual_espp_contributions": annual_espp, "estimated_combined_annual_contributions": contributions.get("combined_recurring_annual"), "special_contribution": contributions.get("special_contribution"), "hd_espp_purchase_frequency": (contributions.get("espp_context", {}) or {}).get("purchase_frequency"), "hd_espp_discount_pct": (contributions.get("espp_context", {}) or {}).get("discount_pct")}, "employer_stock_concentration_context": {"primary_employer_ticker": employer.get("ticker", "HD" if employer_rows else ""), "primary_employer_capital_concentration_pct": employer.get("portfolio_weight_pct"), "is_concentration_alert": bool(employer_rows)}},
        "supplemental_yahoo_financial_data": payload.get("supplemental_investment_intelligence", external.get("supplemental_investment_intelligence", [])),
        "historical_context": payload.get("historical_security_context", external.get("historical_security_context", [])),
        "portfolio_history_summary": build_portfolio_history_summary(history.get("records", [])),
        "dividend_seasonality_summary": build_seasonality_summary(payload.get("dividend_seasonality", external.get("dividend_seasonality", {}))),
        "investor_profile": payload.get("investor_profile", external.get("investor_profile", {})),
        "recommendation_constraints": payload.get("recommendation_constraints", external.get("recommendation_constraints", {})),
        "recommendation_context": payload.get("recommendation_context", {}),
        "data_quality_issues": payload.get("data_quality_issues", []),
    }


def compact_ai_context(payload: dict[str, Any], analysis_type: str, custom_question: str, portfolio_history_summary: dict[str, Any] | None = None, dividend_seasonality_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    full = _production_shape(payload)
    selected = analysis_type if analysis_type in AI_ANALYSIS_TYPES else AI_ANALYSIS_TYPES[0]
    question = str(custom_question or "").lower()
    limits = {"holdings": 18, "income": 10, "exposure": 8, "history": 6, "supplemental": 6}
    profiles = {
        "Comprehensive Portfolio Review": (24, 14, 12, 8, 8), "Income and Dividend Analysis": (16, 16, 5, 6, 8),
        "Diversification and Concentration": (18, 8, 14, 4, 5), "Risk Analysis": (18, 10, 12, 6, 6),
        "Contribution Allocation Review": (14, 10, 6, 4, 4), "Long-Term Outlook": (16, 10, 8, 8, 6),
    }
    if selected in profiles:
        limits = dict(zip(limits, profiles[selected]))
    if selected == "Custom Question":
        if any(word in question for word in ("income", "dividend", "yield", "payout")): limits["income"], limits["supplemental"] = 16, 8
        if any(word in question for word in ("divers", "sector", "allocation", "concentration", "weight")): limits["exposure"] = 14
        if any(word in question for word in ("risk", "drawdown", "volatility")): limits["history"] = 8
        if any(word in question for word in ("contribution", "espp", "biweekly", "outlook", "projection")): limits["holdings"] = 20
    snapshot = full.get("portfolio_snapshot", {}); health = full.get("portfolio_health", {}); context = health.get("context_summary", {}) if isinstance(health, dict) else {}
    contribution = full.get("contribution_context", {}); source_summary = contribution.get("summary", {}) if isinstance(contribution, dict) else {}
    holding_fields = ["ticker", "shares", "current_price", "market_value", "portfolio_weight_pct", "cost_basis", "unrealized_gain", "annual_income", "portfolio_yield_pct", "dividend_yield_pct", "regular_biweekly_contribution", "espp_biweekly_contribution", "special_contribution", "total_contribution", "sector", "asset_type", "employer_stock", "espp_enabled", "espp_acquired_shares"]
    holdings = _records(full.get("calculated_metrics", []), holding_fields, limits["holdings"], "market_value")
    supplemental = _supplemental(full.get("supplemental_yahoo_financial_data", []), [str(row.get("ticker", "")).strip().upper() for row in holdings], limits["supplemental"])
    history_summary = portfolio_history_summary if portfolio_history_summary is not None else full.get("portfolio_history_summary", {})
    seasonality = dividend_seasonality_summary if dividend_seasonality_summary is not None else full.get("dividend_seasonality_summary", {})
    result = {
        "portfolio_snapshot": _pick(snapshot, ["portfolio_value", "cost_basis", "unrealized_gain", "annual_income", "weighted_yield_pct", "holdings_count", "top_holding", "top_holding_weight_pct", "projection_contribution_note", "projected_10y_value", "projected_20y_value"]),
        "investor_profile": _pick(full.get("investor_profile", {}), ["primary_goal", "risk_tolerance", "investment_horizon_years"]),
        "calculated_metrics": holdings,
        "income_allocation_by_holding": _records(full.get("income_allocation_by_holding", []), ["ticker", "annual_income", "market_value", "income_allocation_pct"], limits["income"], "annual_income"),
        "asset_allocation": _records(full.get("asset_allocation", []), ["asset_type", "market_value", "annual_income", "portfolio_weight_pct"], limits["exposure"], "market_value"),
        "direct_sector_exposure": _records(full.get("direct_sector_exposure", []), ["sector", "market_value", "annual_income", "portfolio_weight_pct"], limits["exposure"], "market_value"),
        "look_through_sector_exposure": _records(full.get("look_through_sector_exposure", []), ["sector", "source_holdings", "market_value", "portfolio_weight_pct"], max(4, limits["exposure"] // 2), "market_value"),
        "concentration_context": _pick(full.get("concentration_context", {}), ["largest_holding_by_value", "largest_holding_value_weight_pct", "largest_holding_income_weight_pct", "largest_income_contributor", "largest_income_weight_pct", "guidance"]),
        "portfolio_health": {"health_score_breakdown": _pick(health.get("health_score_breakdown", {}), ["diversification", "concentration", "income_stability", "dividend_growth_potential", "total_return_potential", "risk", "data_quality"]), "context_summary": _pick(context, ["score", "score_label", "strongest_areas", "weakest_areas", "primary_alert"])},
        "tracker_health_score_metadata": _pick(full.get("tracker_health_score_metadata", {}), ["income_stability_explanation", "data_quality_explanation", "risk_explanation"]),
        "income_quality_summary": _pick(full.get("income_quality", {}), ["income_quality_score", "distribution_stability_score", "yield_sustainability_score", "payout_risk_score", "alerts"]) if selected in {"Comprehensive Portfolio Review", "Income and Dividend Analysis"} else {},
        "contribution_context": {"summary": {"total_regular_biweekly_contribution": source_summary.get("total_regular_biweekly_contribution"), "total_espp_biweekly_contributions": source_summary.get("total_espp_biweekly_contributions"), "annual_regular_contributions": source_summary.get("estimated_annual_regular_contributions"), "annual_espp_contributions": source_summary.get("estimated_annual_espp_contributions"), "estimated_combined_annual_contributions": source_summary.get("estimated_combined_annual_contributions"), "special_contribution": source_summary.get("special_contribution"), "hd_espp_purchase_frequency": source_summary.get("hd_espp_purchase_frequency"), "hd_espp_discount_pct": source_summary.get("hd_espp_discount_pct")}, "employer_stock_concentration_context": _pick(contribution.get("employer_stock_concentration_context", {}), ["primary_employer_ticker", "primary_employer_capital_concentration_pct", "primary_employer_income_concentration_pct", "is_concentration_alert"])},
        "data_completeness": _pick(full.get("data_completeness", {}), ["direct_sector_classification_coverage_pct", "fund_lookthrough_coverage_pct", "overall_resolved_exposure_coverage_pct"]),
        "unresolved_exposure": _pick(full.get("unresolved_exposure", {}), ["unresolved_fund_exposure", "resolved_lookthrough_value", "fund_total_value"]),
        "supplemental_yahoo_financial_data": supplemental,
        "historical_context": _records(full.get("historical_context", []), ["ticker", "return_pct", "drawdown", "volatility", "52w_high", "52w_low"], limits["history"]),
        "portfolio_history_summary": history_summary if selected in {"Comprehensive Portfolio Review", "Long-Term Outlook"} or selected == "Custom Question" and any(word in question for word in ("history", "historical", "outlook", "growth")) else {},
        "dividend_seasonality_summary": seasonality if selected in {"Comprehensive Portfolio Review", "Income and Dividend Analysis"} else {},
        "recommendation_context": _pick(full.get("recommendation_context", {}), ["decision_objective"]),
        "recommendation_constraints": full.get("recommendation_constraints", {}),
        "warnings": {"important": _warnings(full)[:8]},
    }
    alerts = result["income_quality_summary"].get("alerts") if isinstance(result["income_quality_summary"], dict) else None
    if isinstance(alerts, list): result["income_quality_summary"]["alerts"] = alerts[:3]
    summary = result["contribution_context"]["summary"]; employer = result["contribution_context"]["employer_stock_concentration_context"]
    annual_total, annual_regular, annual_espp = _finite(summary.get("estimated_combined_annual_contributions")), _finite(summary.get("annual_regular_contributions")), _finite(summary.get("annual_espp_contributions"))
    if annual_total > 0:
        summary["employer_stock_contribution_pct"] = round(annual_espp / annual_total * 100, 2); summary["non_employer_contribution_pct"] = round(100 - annual_espp / annual_total * 100, 2)
        raw_discount = summary.get("hd_espp_discount_pct")
        discount = _finite(raw_discount, float("nan")); discount = discount * 100 if math.isfinite(discount) and 0 <= discount <= 1 else discount
        current = _finite(employer.get("primary_employer_capital_concentration_pct"), float("nan"))
        if math.isfinite(discount) and 0 <= discount < 100 and math.isfinite(current) and 0 <= current <= 100:
            espp_value = annual_espp / (1 - discount / 100); new_total = annual_regular + espp_value
            weight = espp_value / new_total * 100 if new_total else current
            summary["concentration_direction"] = "increase employer-stock concentration" if weight > current else "dilute employer-stock concentration" if weight < current else "neutral"
        else: summary["concentration_direction"] = "indeterminate"
    else: summary["concentration_direction"] = "indeterminate"
    return _strip(result)


def build_ai_request(payload: dict[str, Any], analysis_type: str, custom_question: str) -> tuple[dict[str, Any], dict[str, Any]]:
    compact = compact_ai_context(payload, analysis_type, custom_question)
    before = json.dumps(payload, default=str, separators=(",", ":"), allow_nan=False)
    after = json.dumps(compact, default=str, separators=(",", ":"), allow_nan=False)
    diagnostics = {"before_chars": len(before), "before_tokens": math.ceil(len(before) / 4), "after_chars": len(after), "after_tokens": math.ceil(len(after) / 4), "reduction_pct": round((len(before) - len(after)) / len(before) * 100, 1) if before else 0.0}
    messages = build_ai_analysis_messages(compact, analysis_type, custom_question)
    return {"model_prompt": messages_to_prompt(messages), "context": compact}, diagnostics


@dataclass
class AIAnalysisState:
    busy: bool = False
    last_attempt_at: str = ""
    last_stage: str = ""
    result_markdown: str = ""
    response_diagnostic: dict[str, Any] = field(default_factory=dict)
    result_model: str = ""
    result_type: str = ""
    generated_at: str = ""
    context_diagnostics: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    error_detail: str = ""

    def clear(self) -> None:
        self.__dict__.update(AIAnalysisState().__dict__)


def _safe_error(exc: Exception) -> tuple[str, str]:
    mappings = ((OneMinAIConfigError, "Set ONEMIN_AI_API_KEY in your local environment to run AI Analysis."), (OneMinAIAuthenticationError, "1min.AI rejected the API key. Check ONEMIN_AI_API_KEY and try again."), (OneMinAIInsufficientCreditsError, "1min.AI reports insufficient credits."), (OneMinAIRateLimitError, "1min.AI rate limited the request. Try again later."), (OneMinAIUnsupportedModelError, "The selected 1min.AI model is not supported. Choose another model."), (OneMinAITimeoutError, "The 1min.AI request timed out. Try again or reduce the analysis context."), (OneMinAIConnectionError, "Could not reach 1min.AI. Check your connection or base URL."), (OneMinAIResponseError, "1min.AI returned an invalid or empty response."), (OneMinAIError, "1min.AI provider failure."))
    for error_type, message in mappings:
        if isinstance(exc, error_type): return message, str(exc)
    return "Unexpected error while running AI Analysis.", "No additional provider diagnostics were available."


def run_ai_analysis(payload: dict[str, Any], analysis_type: str, model: str, custom_question: str, *, state: AIAnalysisState | None = None, client_factory: Callable[..., OneMinAIClient] = OneMinAIClient) -> AIAnalysisState:
    state = state or AIAnalysisState()
    if state.busy and state.last_stage in {"preparing", "requesting"}: return state
    state.busy = True; state.last_attempt_at = datetime.now().isoformat(timespec="seconds"); state.last_stage = "preparing"; state.error_message = ""; state.error_detail = ""
    try:
        request, diagnostics = build_ai_request(payload, analysis_type, custom_question); state.context_diagnostics = diagnostics
        timeout = _finite(os.environ.get("ONEMIN_AI_TIMEOUT_SECONDS"), ONEMIN_AI_TIMEOUT_SECONDS)
        if timeout <= 0: timeout = ONEMIN_AI_TIMEOUT_SECONDS
        client = client_factory(api_key=os.environ.get("ONEMIN_AI_API_KEY"), base_url=str(os.environ.get("ONEMIN_AI_BASE_URL", "")).strip().rstrip("/") or ONEMIN_AI_DEFAULT_BASE_URL, timeout_seconds=timeout, connect_timeout_seconds=ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS)
        state.last_stage = "requesting"
        response = client.chat_with_ai(model=str(model or "").strip(), prompt=request["model_prompt"])
        content = getattr(response, "content", "")
        if not isinstance(content, str) or not content.strip(): raise OneMinAIResponseError("1min.AI returned an empty analysis result.")
        state.result_markdown = format_ai_analysis_markdown(content.strip()); state.response_diagnostic = getattr(response, "response_diagnostic", {}) or {}; state.result_model = str(getattr(response, "model", model) or model); state.result_type = analysis_type; state.generated_at = datetime.now().isoformat(timespec="seconds"); state.last_stage = "success"
    except Exception as exc:
        state.error_message, state.error_detail = _safe_error(exc); state.last_stage = "error"
    finally:
        state.busy = False
    return state

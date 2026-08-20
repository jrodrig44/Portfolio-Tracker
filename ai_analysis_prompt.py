from __future__ import annotations

import re
from typing import Any

import pandas as pd

AI_ANALYSIS_TYPES = [
    "Comprehensive Portfolio Review",
    "Income and Dividend Analysis",
    "Diversification and Concentration",
    "Risk Analysis",
    "Contribution Allocation Review",
    "Long-Term Outlook",
    "Custom Question",
]

_ANALYSIS_TYPE_GUIDANCE: dict[str, str] = {
    "Comprehensive Portfolio Review": "Provide a full review across the entire portfolio.",
    "Income and Dividend Analysis": "Focus on dividend durability, income quality, income concentration, and sustainability of projected income.",
    "Diversification and Concentration": "Focus on holding, sector, asset-type, and income concentration, including look-through exposure and employer-stock concentration.",
    "Risk Analysis": "Focus on concentration, downside risks, income stability, data limitations, and portfolio-specific risk factors.",
    "Contribution Allocation Review": "Focus on how new contributions and ESPP contributions are allocated, and whether the allocation supports the long-term objective.",
    "Long-Term Outlook": "Focus on long-term compounding, projected portfolio growth, income trajectory, and whether the current structure fits a 20-year horizon.",
    "Custom Question": "Answer the user's custom question directly, while still mentioning closely related material risks and data limitations.",
}


def build_ai_analysis_messages(
    portfolio_payload: dict[str, Any],
    analysis_type: str,
    custom_question: str,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": _build_user_prompt(portfolio_payload, analysis_type, custom_question)},
    ]


def build_ai_analysis_markdown(
    portfolio_payload: dict[str, Any],
    analysis_type: str,
    custom_question: str,
) -> str:
    return _build_user_prompt(portfolio_payload, analysis_type, custom_question)


def _build_system_prompt() -> str:
    return "\n".join(
        [
            "You are analyzing a local portfolio tracker for informational use only.",
            "Do not present the response as personalized fiduciary advice, and do not claim guaranteed returns.",
            "Distinguish tracker-generated scores and heuristics from objective financial ratings.",
            "If data is missing, stale, inconsistent, or unreliable, say so explicitly instead of inventing values.",
            "Treat the provided holdings and metrics as the source of truth for this app session.",
            "Use the supplied investor profile and contribution context; do not substitute hard-coded contribution or ESPP facts.",
            "The user wants practical, portfolio-specific observations.",
            "Do not treat employer stock acquired through an ESPP as an ordinary freely allocated contribution. Evaluate it with employer concentration, the supplied ESPP terms, tax considerations, and holding strategy where supplied.",
            "When applicable, organize the answer with these sections: Executive Summary, Portfolio Strengths, Primary Risks, Income Quality and Sustainability, Diversification and Concentration, HD ESPP and Employer-Stock Considerations, Contribution Allocation Review, Long-Term Outlook, Potential Actions to Consider, and Data Limitations.",
            "For narrower analysis types, focus on the selected topic while still mentioning any closely related material risk.",
            "Use concise, direct markdown. Render currency as escaped dollar values such as \\$1,234.56; never wrap currency in math delimiters or inline-code markers.",
        ]
    )


def _build_user_prompt(portfolio_payload: dict[str, Any], analysis_type: str, custom_question: str) -> str:
    heading = str(analysis_type or "Comprehensive Portfolio Review").strip()
    if heading not in AI_ANALYSIS_TYPES:
        heading = "Comprehensive Portfolio Review"
    focus = _ANALYSIS_TYPE_GUIDANCE[heading]
    custom_question = str(custom_question or "").strip()

    sections: list[str] = [
        f"Analysis type: {heading}",
        f"Focus guidance: {focus}",
    ]
    if custom_question:
        sections.append(f"Custom question or extra instructions: {custom_question}")

    sections.extend(
        [
            "",
            "Portfolio data context:",
            _build_portfolio_context(portfolio_payload),
            "",
            "Response instructions:",
            "- Write the answer in markdown.",
            "- Be specific to the portfolio data provided.",
            "- Call out missing, stale, or uncertain inputs directly.",
            "- Distinguish tracker-generated scores from objective ratings.",
            "- Mention the most relevant risks even when answering a narrow question.",
            "- Do not invent values or assume missing data.",
        ]
    )
    return "\n".join(sections)


def _build_portfolio_context(portfolio_payload: dict[str, Any]) -> str:
    snapshot = _as_dict(portfolio_payload.get("portfolio_snapshot"))
    investor_profile = _as_dict(portfolio_payload.get("investor_profile"))
    portfolio_health = _as_dict(portfolio_payload.get("portfolio_health"))
    health_context = _as_dict(portfolio_health.get("context_summary"))
    concentration_context = _as_dict(portfolio_payload.get("concentration_context"))
    contribution_context = _as_dict(portfolio_payload.get("contribution_context"))
    asset_allocation = _as_list(portfolio_payload.get("asset_allocation"))
    direct_sector_exposure = _as_list(portfolio_payload.get("direct_sector_exposure"))
    direct_holdings = _as_list(portfolio_payload.get("direct_holdings_by_classification"))
    look_through_exposure = _as_list(portfolio_payload.get("look_through_sector_exposure"))
    unresolved_lookthrough = _as_list(portfolio_payload.get("unresolved_fund_look_through_exposure"))
    income_by_holding = _as_list(portfolio_payload.get("income_allocation_by_holding"))
    income_by_asset_type = _as_list(portfolio_payload.get("income_allocation_by_asset_type"))
    economic_exposure = _as_list(portfolio_payload.get("economic_exposure_allocation"))
    holdings = _as_list(portfolio_payload.get("calculated_metrics"))
    supplemental_financial = _as_list(portfolio_payload.get("supplemental_yahoo_financial_data"))
    historical_context = _as_list(portfolio_payload.get("historical_context"))
    portfolio_history = _as_dict(portfolio_payload.get("portfolio_history_summary"))
    income_quality = _as_dict(portfolio_payload.get("income_quality_summary"))
    seasonality = _as_dict(portfolio_payload.get("dividend_seasonality_summary"))
    data_completeness = _as_dict(portfolio_payload.get("data_completeness"))
    unresolved_exposure = _as_dict(portfolio_payload.get("unresolved_exposure"))
    tracker_metadata = _as_dict(portfolio_payload.get("tracker_health_score_metadata"))
    warnings = _as_dict(portfolio_payload.get("warnings"))
    recommendation_context = _as_dict(portfolio_payload.get("recommendation_context"))
    recommendation_constraints = _as_dict(portfolio_payload.get("recommendation_constraints"))

    sections = [
        "1) Portfolio overview",
        f"- Total portfolio value: {_format_currency(snapshot.get('portfolio_value'))}",
        f"- Cost basis: {_format_currency(snapshot.get('cost_basis'))}",
        f"- Unrealized gain/loss: {_format_currency(snapshot.get('unrealized_gain'))}",
        f"- Estimated annual income: {_format_currency(snapshot.get('annual_income'))}",
        f"- Weighted portfolio yield: {_format_percent(snapshot.get('weighted_yield_pct'))}",
        f"- Number of holdings: {int(_safe_float(snapshot.get('holdings_count'), 0.0))}",
        f"- Largest holding: {snapshot.get('top_holding', 'N/A')} ({_format_percent(snapshot.get('top_holding_weight_pct'))} of portfolio value)",
        f"- Tracker health score: {_safe_float(health_context.get('score', portfolio_health.get('health_score', 0.0))):.1f}/100",
        f"- Health score label: {health_context.get('score_label', 'Tracker-generated score')}",
        f"- Investor objective: {investor_profile.get('primary_goal', 'N/A')}",
        f"- Risk tolerance: {investor_profile.get('risk_tolerance', 'N/A')}",
        f"- Investment horizon: {investor_profile.get('investment_horizon_years', 'N/A')} years",
        "",
        "2) Holdings snapshot",
        _format_table_block(
            holdings,
            [
                "ticker",
                "shares",
                "current_price",
                "market_value",
                "portfolio_weight_pct",
                "cost_basis",
                "annual_income",
                "portfolio_yield_pct",
                "dividend_yield_pct",
                "effective_annual_div_per_share",
                "annual_div_per_share",
                "regular_biweekly_contribution",
                "espp_biweekly_contribution",
                "special_contribution",
                "total_contribution",
                "sector",
                "asset_type",
                "employer_stock",
                "espp_enabled",
                "espp_acquired_shares",
            ],
        ),
        "",
        "3) Concentration and exposure",
        *(_format_score_lines(health_context.get("strongest_areas"), "Strongest area") or ["- No strongest-area summary available."]),
        *(_format_score_lines(health_context.get("weakest_areas"), "Weakest area") or ["- No weakest-area summary available."]),
        f"- {str(concentration_context.get('guidance', 'Capital concentration and income concentration should be evaluated separately.'))}",
        f"- Largest holding by value: {concentration_context.get('largest_holding_by_value', 'N/A')} ({_format_percent(concentration_context.get('largest_holding_value_weight_pct'))} of portfolio value)",
        f"- Largest holding income share: {_format_percent(concentration_context.get('largest_holding_income_weight_pct'))}",
        f"- Largest income contributor: {concentration_context.get('largest_income_contributor', 'N/A')} ({_format_percent(concentration_context.get('largest_income_weight_pct'))} of estimated annual income)",
        "- Asset allocation table:",
        _format_table_block(asset_allocation, ["asset_type", "market_value", "annual_income", "portfolio_weight_pct"]),
        "- Direct sector exposure table:",
        _format_table_block(direct_sector_exposure, ["sector", "market_value", "annual_income", "portfolio_weight_pct", "pct_of_resolved_direct_sector_exposure"]),
        "- Direct holdings by classification table:",
        _format_table_block(direct_holdings, ["classification", "holdings", "market_value", "annual_income", "portfolio_weight_pct"]),
        "- Look-through sector exposure table:",
        _format_table_block(look_through_exposure, ["sector", "source_holdings", "market_value", "portfolio_weight_pct"]),
        "- Unresolved fund look-through exposure table:",
        _format_table_block(unresolved_lookthrough, ["fund_type", "holdings", "market_value", "portfolio_weight_pct"]),
        "- Income allocation by holding table:",
        _format_table_block(income_by_holding, ["ticker", "annual_income", "market_value", "income_allocation_pct"]),
        "- Income allocation by asset type table:",
        _format_table_block(income_by_asset_type, ["asset_type", "annual_income", "market_value", "income_allocation_pct"]),
        "- Economic exposure table:",
        _format_table_block(economic_exposure, ["economic_exposure_category", "market_value", "annual_income", "portfolio_weight"]),
        "",
        "4) Contribution allocation and ESPP",
        f"- Regular biweekly contributions: {_format_currency(contribution_context.get('summary', {}).get('total_regular_biweekly_contribution'))}",
        f"- ESPP biweekly contributions: {_format_currency(contribution_context.get('summary', {}).get('total_espp_biweekly_contributions'))}",
        f"- Annualized regular contributions: {_format_currency(contribution_context.get('summary', {}).get('annual_regular_contributions'))}",
        f"- Annualized ESPP contributions: {_format_currency(contribution_context.get('summary', {}).get('annual_espp_contributions'))}",
        f"- Total annual contributions: {_format_currency(contribution_context.get('summary', {}).get('estimated_combined_annual_contributions'))}",
        f"- New contributions to employer stock: {_format_percent(contribution_context.get('summary', {}).get('employer_stock_contribution_pct'))}",
        f"- New contributions outside employer stock: {_format_percent(contribution_context.get('summary', {}).get('non_employer_contribution_pct'))}",
        f"- Contribution concentration direction: {contribution_context.get('summary', {}).get('concentration_direction', 'indeterminate')}",
        f"- Employer ticker: {contribution_context.get('employer_stock_concentration_context', {}).get('primary_employer_ticker', 'N/A')}",
        f"- Current employer-stock weight: {_format_percent(contribution_context.get('employer_stock_concentration_context', {}).get('primary_employer_capital_concentration_pct'))}",
        f"- ESPP purchase frequency: {contribution_context.get('summary', {}).get('hd_espp_purchase_frequency', 'n/a')}",
        f"- ESPP discount: {_format_percent(contribution_context.get('summary', {}).get('hd_espp_discount_pct'))}",
        f"- Employer stock concentration alert: {contribution_context.get('employer_stock_concentration_context', {}).get('is_concentration_alert', False)}",
        "",
        "5) Health score and methodology",
        f"- Health score components: {_format_health_components(portfolio_health.get('health_score_breakdown'))}",
        f"- Income quality explanation: {tracker_metadata.get('income_stability_explanation', '')}",
        f"- Data quality explanation: {tracker_metadata.get('data_quality_explanation', '')}",
        f"- Risk explanation: {tracker_metadata.get('risk_explanation', '')}",
        *(_format_income_quality(income_quality) if income_quality else []),
        *(_format_seasonality(seasonality) if seasonality else []),
        "",
        "6) Supplemental Yahoo Finance intelligence",
        *(_format_supplemental_intelligence(supplemental_financial) or ["- No supplemental Yahoo Finance intelligence available."]),
        "",
        "7) Historical context and projections",
        *(_format_historical_context(historical_context) or ["- No supplemental historical context available."]),
        *(_format_portfolio_history(portfolio_history) if portfolio_history else []),
        f"- Projection note: {snapshot.get('projection_contribution_note', '')}",
        f"- 10-year projected value: {_format_currency(snapshot.get('projected_10y_value'))}",
        f"- 20-year projected value: {_format_currency(snapshot.get('projected_20y_value'))}",
        "",
        "8) Data warnings and limitations",
        *(_format_warnings(portfolio_payload, warnings, unresolved_exposure, data_completeness) or ["- No obvious data-quality issues were detected."]),
        "",
        "9) Recommendation context",
        f"- Decision objective: {recommendation_context.get('decision_objective', 'N/A')}",
        f"- Recommended actions: {len(recommendation_context.get('recommended_actions', [])) if isinstance(recommendation_context.get('recommended_actions', []), list) else 0}",
        f"- Constraints: {', '.join(str(key) for key, value in recommendation_constraints.items() if value) if recommendation_constraints else 'N/A'}",
    ]

    return "\n".join(sections)


def _format_health_components(breakdown: Any) -> str:
    if not isinstance(breakdown, dict):
        return "N/A"
    parts = []
    for key in ["diversification", "concentration", "income_stability", "dividend_growth_potential", "total_return_potential", "risk", "data_quality"]:
        if key in breakdown:
            parts.append(f"{key}={_safe_float(breakdown.get(key), 0.0):.1f}")
    return ", ".join(parts) if parts else "N/A"


def _format_score_lines(items: Any, label: str) -> list[str]:
    if not isinstance(items, list):
        return []
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        area = item.get("area", "N/A")
        score = item.get("score", "N/A")
        lines.append(f"- {label}: {area} ({score})")
    return lines


def _format_supplemental_intelligence(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        detail_parts: list[str] = []
        fundamentals = _as_dict(item.get("fundamentals"))
        earnings = _as_dict(item.get("growth_and_estimates"))
        analyst_targets = _as_dict(item.get("analyst_signal"))
        fund_sector_weights = _as_list(item.get("fund_sector_weights"))

        if fundamentals:
            if fundamentals.get("trailing_pe") not in [None, ""]:
                detail_parts.append(f"Trailing PE {fundamentals.get('trailing_pe')}")
            if fundamentals.get("forward_pe") not in [None, ""]:
                detail_parts.append(f"Forward PE {fundamentals.get('forward_pe')}")
            if fundamentals.get("dividend_yield") not in [None, ""]:
                detail_parts.append(f"Yield {fundamentals.get('dividend_yield')}")
        if earnings:
            if earnings.get("earnings_growth") not in [None, ""]:
                detail_parts.append(f"Earnings growth {earnings.get('earnings_growth')}")
            if earnings.get("revenue_growth") not in [None, ""]:
                detail_parts.append(f"Revenue growth {earnings.get('revenue_growth')}")
        if analyst_targets:
            if analyst_targets.get("target_mean_price") not in [None, ""]:
                detail_parts.append(f"Target {analyst_targets.get('target_mean_price')}")
            if analyst_targets.get("recommendation_key") not in [None, ""]:
                detail_parts.append(f"Recommendation {analyst_targets.get('recommendation_key')}")
        if fund_sector_weights:
            weights = ", ".join(f"{row.get('sector')} {row.get('weight_pct')}%" for row in fund_sector_weights if isinstance(row, dict))
            if weights:
                detail_parts.append(f"Fund sectors: {weights}")

        lines.append(f"- {ticker}: " + ("; ".join(detail_parts) if detail_parts else "No supplemental intelligence available."))
    return lines


def _format_historical_context(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        lines.append(
            "- "
            + f"{ticker}: period return {_format_percent(item.get('return_pct'))}, drawdown {_format_percent(item.get('drawdown'))}, volatility {_format_percent(item.get('volatility'))}, "
            + f"52-week range {_format_currency(item.get('52w_low'))} to {_format_currency(item.get('52w_high'))}"
        )
    return lines


def _format_income_quality(summary: dict[str, Any]) -> list[str]:
    lines = [
        "- Tracker income-quality heuristics: "
        + f"overall {_safe_float(summary.get('income_quality_score')):.1f}, distribution stability {_safe_float(summary.get('distribution_stability_score')):.1f}, "
        + f"yield sustainability {_safe_float(summary.get('yield_sustainability_score')):.1f}, payout risk {_safe_float(summary.get('payout_risk_score')):.1f}.",
    ]
    for alert in _as_list(summary.get("alerts"))[:3]:
        lines.append(f"- Income-quality alert: {alert}")
    return lines


def _format_seasonality(summary: dict[str, Any]) -> list[str]:
    return [
        "- Dividend timing summary: "
        + f"highest month(s) {', '.join(summary.get('highest_income_months', [])) or 'N/A'}; "
        + f"lowest month(s) {', '.join(summary.get('lowest_income_months', [])) or 'N/A'}; "
        + f"timing coverage {_format_percent(summary.get('timing_coverage_pct'))}; "
        + f"uncovered annual income {_format_currency(summary.get('uncovered_annual_income'))}."
    ]


def _format_portfolio_history(summary: dict[str, Any]) -> list[str]:
    one_year = _as_dict(summary.get("one_year_baseline"))
    three_month = _as_dict(summary.get("three_month_baseline"))
    lines = [
        f"- Portfolio account-value history: {summary.get('point_count', 0)} actual points; earliest {summary.get('earliest_date', 'N/A')} {_format_currency(summary.get('earliest_value'))}; latest {summary.get('latest_date', 'N/A')} {_format_currency(summary.get('latest_value'))}.",
    ]
    if one_year:
        lines.append(f"- Nominal one-year account-value change: {_format_percent(one_year.get('nominal_change_pct'))} from actual point {one_year.get('date', 'N/A')} ({_format_currency(one_year.get('value'))}).")
    if three_month:
        lines.append(f"- Nominal three-month account-value change: {_format_percent(three_month.get('nominal_change_pct'))} from actual point {three_month.get('date', 'N/A')} ({_format_currency(three_month.get('value'))}).")
    lines.append(f"- {summary.get('interpretation', 'Nominal account-value change includes contributions/withdrawals and is not investment return.')}")
    return lines


def _format_warnings(
    portfolio_payload: dict[str, Any],
    warnings: dict[str, Any],
    unresolved_exposure: dict[str, Any],
    data_completeness: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    for issue in _as_list(portfolio_payload.get("data_quality_issues")):
        lines.append(f"- {issue}")
    for issue in _as_list(warnings.get("snapshot_validation_errors")):
        lines.append(f"- Snapshot validation: {issue}")
    for field_name in _as_list(warnings.get("missing_export_fields")):
        lines.append(f"- Missing export field: {field_name}")
    for issue in _as_list(warnings.get("important")):
        lines.append(f"- {issue}")
    if unresolved_exposure:
        lines.append(
            f"- Unresolved exposure: {_format_currency(unresolved_exposure.get('unresolved_fund_exposure'))} unresolved against {_format_currency(unresolved_exposure.get('fund_total_value'))} fund value."
        )
    if data_completeness:
        lines.append(
            "- Data completeness: "
            + f"direct sector coverage {_format_percent(data_completeness.get('direct_sector_classification_coverage_pct'))}, "
            + f"fund look-through coverage {_format_percent(data_completeness.get('fund_lookthrough_coverage_pct'))}, "
            + f"overall resolved exposure {_format_percent(data_completeness.get('overall_resolved_exposure_coverage_pct'))}."
        )
    return lines


def _format_table_block(records: list[dict[str, Any]], preferred_columns: list[str]) -> str:
    if not records:
        return "(No data available)"
    filtered = [item for item in records if isinstance(item, dict)]
    if not filtered:
        return "(No data available)"
    df = pd.DataFrame(filtered)
    cols = [column for column in preferred_columns if column in df.columns]
    if cols:
        df = df[cols]
    df = df.astype(object).where(pd.notna(df), "")
    return f"```text\n{df.to_string(index=False)}\n```"


def _format_currency(value: Any) -> str:
    if value in [None, ""]:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return str(value)


def format_ai_analysis_markdown(markdown: str) -> str:
    """Keep model-provided currency literal when Streamlit renders Markdown."""
    text = str(markdown or "")
    currency = r"\\?\$(\d[\d,]*(?:\.\d{1,2})?)"
    text = re.sub(rf"`\s*{currency}\s*`", r"\\$\1", text)
    text = re.sub(rf"(?<!\\){currency}\$(?!\w)", r"\\$\1", text)
    return re.sub(r"(?<!\\)\$(?=\d)", r"\\$", text)


def _format_percent(value: Any) -> str:
    if value in [None, ""]:
        return "N/A"
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in [None, ""]:
            return default
        return float(value)
    except Exception:
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

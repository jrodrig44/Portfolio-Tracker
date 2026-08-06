from __future__ import annotations

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
            "The investor profile is: dividend growth and long-term income, about a 20-year time horizon, moderate risk tolerance, regular contributions of $280 biweekly, and a separate HD ESPP contribution of $100 biweekly.",
            "The user wants practical, portfolio-specific observations.",
            "HD is employer stock acquired through The Home Depot ESPP: $100 biweekly contribution, 15% employee discount, purchases approximately every six months, and the holding has historically been retained for about two to three years.",
            "Do not treat HD as an ordinary freely allocated portfolio contribution. Evaluate it in the context of employer concentration, the ESPP discount, tax considerations, and the user's holding strategy.",
            "When applicable, organize the answer with these sections: Executive Summary, Portfolio Strengths, Primary Risks, Income Quality and Sustainability, Diversification and Concentration, HD ESPP and Employer-Stock Considerations, Contribution Allocation Review, Long-Term Outlook, Potential Actions to Consider, and Data Limitations.",
            "For narrower analysis types, focus on the selected topic while still mentioning any closely related material risk.",
            "Use concise, direct markdown.",
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
        f"- Total annual contributions: {_format_currency(contribution_context.get('summary', {}).get('estimated_combined_annual_contributions'))}",
        f"- HD ESPP contribution per paycheck: {_format_currency(contribution_context.get('summary', {}).get('hd_espp_contribution_per_paycheck'))}",
        f"- HD ESPP purchase frequency: {contribution_context.get('summary', {}).get('hd_espp_purchase_frequency', 'n/a')}",
        f"- HD ESPP discount: {_format_percent(contribution_context.get('summary', {}).get('hd_espp_discount_pct'))}",
        f"- Employer stock concentration alert: {contribution_context.get('employer_stock_concentration_context', {}).get('is_concentration_alert', False)}",
        "- HD employer-stock context: The Home Depot ESPP, $100 biweekly contribution, 15% employee discount, purchases approximately every six months, and a 2-3 year historical holding pattern.",
        "",
        "5) Health score and methodology",
        f"- Health score components: {_format_health_components(portfolio_health.get('health_score_breakdown'))}",
        f"- Income quality explanation: {tracker_metadata.get('income_stability_explanation', '')}",
        f"- Data quality explanation: {tracker_metadata.get('data_quality_explanation', '')}",
        f"- Risk explanation: {tracker_metadata.get('risk_explanation', '')}",
        "",
        "6) Supplemental Yahoo Finance intelligence",
        *(_format_supplemental_intelligence(supplemental_financial) or ["- No supplemental Yahoo Finance intelligence available."]),
        "",
        "7) Historical context and projections",
        *(_format_historical_context(historical_context) or ["- No supplemental historical context available."]),
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
        fundamentals = _as_dict(item.get("fundamental_metrics"))
        earnings = _as_dict(item.get("earnings_and_estimates"))
        analyst_targets = _as_dict(item.get("analyst_targets"))
        fund_data = _as_dict(item.get("fund_data"))

        if fundamentals:
            if fundamentals.get("pe_ratio") not in [None, ""]:
                detail_parts.append(f"PE {fundamentals.get('pe_ratio')}")
            if fundamentals.get("price_to_book") not in [None, ""]:
                detail_parts.append(f"PB {fundamentals.get('price_to_book')}")
            if fundamentals.get("dividend_yield") not in [None, ""]:
                detail_parts.append(f"Yield {fundamentals.get('dividend_yield')}")
        if earnings:
            if earnings.get("avg_eps_estimate") not in [None, ""]:
                detail_parts.append(f"EPS est {earnings.get('avg_eps_estimate')}")
            if earnings.get("earnings_growth_pct") not in [None, ""]:
                detail_parts.append(f"Earnings growth {earnings.get('earnings_growth_pct')}")
            if earnings.get("revenue_growth_pct") not in [None, ""]:
                detail_parts.append(f"Revenue growth {earnings.get('revenue_growth_pct')}")
        if analyst_targets:
            if analyst_targets.get("target_mean_price") not in [None, ""]:
                detail_parts.append(f"Target {analyst_targets.get('target_mean_price')}")
            if analyst_targets.get("recommendation_mean") not in [None, ""]:
                detail_parts.append(f"Rec {analyst_targets.get('recommendation_mean')}")
        if fund_data:
            description = str(fund_data.get("description", "")).strip()
            if description:
                detail_parts.append(description)
            sector_weightings = _as_dict(fund_data.get("sector_weightings"))
            if sector_weightings:
                weights = ", ".join(
                    f"{str(sector).title()} {float(weight) * 100:.1f}%" for sector, weight in list(sector_weightings.items())[:4]
                )
                detail_parts.append(f"Sector weights: {weights}")

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
            + f"{ticker}: return {_format_percent(item.get('return_pct'))}, drawdown {_format_percent(item.get('drawdown'))}, volatility {_format_percent(item.get('volatility'))}, "
            + f"start {_format_currency(item.get('start_price'))}, end {_format_currency(item.get('end_price'))}"
        )
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

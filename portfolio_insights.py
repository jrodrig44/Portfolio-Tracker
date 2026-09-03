from __future__ import annotations

from typing import Any

import pandas as pd

from portfolio_analysis import build_analysis, canonical_asset_type, finite
from beneficial_exposure import beneficial_metrics_frame


def _holding_yield(row: dict[str, Any]) -> float:
    price = finite(row.get("current_price"))
    if price <= 0:
        return 0.0
    reported = finite(row.get("dividend_yield_pct"))
    if reported > 0:
        return reported
    annual = finite(row.get("effective_annual_div_per_share"))
    return annual / price * 100.0 if annual > 0 else 0.0


def _insights_asset_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"", "nan", "none", "unknown", "unassigned"}:
        return "Other/Unassigned"
    if raw in {"cash", "cash equivalent", "cash/cash equivalent", "money market"}:
        return "Cash/Cash Equivalent"
    canonical = canonical_asset_type(value)
    if canonical == "ETF": return "ETF/Fund"
    if canonical == "REIT": return "REIT"
    if canonical in {"Bond Fund", "CEF", "BDC"} or raw in {"fixed income", "fixed-income", "treasury"}: return "Bond/Fixed Income"
    if canonical == "Stock": return "Stock"
    return "Other/Unassigned"


def build_portfolio_insights(metrics: pd.DataFrame, *, fund_weights: dict[str, dict[str, float]] | None = None, hd_historical_context: dict[str, Any] | None = None) -> dict[str, Any]:
    frame = beneficial_metrics_frame(metrics, hd_historical_context)
    if frame.empty:
        return {
            "totals": {"portfolio_value": 0.0, "annual_income": 0.0, "weighted_yield_pct": 0.0, "average_monthly_income": 0.0},
            "yield": {"highest_yield_ticker": "N/A", "highest_yield_pct": 0.0, "lowest_positive_yield_ticker": "N/A", "lowest_positive_yield_pct": 0.0, "yield_spread_pct": None},
            "contribution": {"rows": [], "summary": {"regular_biweekly": 0.0, "espp_biweekly": 0.0, "special_contribution": 0.0, "special_total": 0.0, "special_biweekly": 0.0, "combined_biweekly": 0.0, "regular_annual": 0.0, "espp_annual": 0.0, "combined_annual": 0.0}, "active_contributors": 0},
            "allocation": [], "economic_exposure": [], "direct_sector": [], "income_allocation": [], "income_quality": [], "concentration": {}, "fund_lookthrough": {}, "data_completeness": {}, "observations": [],
        }

    analysis = build_analysis(frame, fund_weights=fund_weights, hd_historical_context=hd_historical_context)
    value = finite(frame.get("market_value", pd.Series(dtype=float)).sum())
    income = finite(frame.get("annual_income", pd.Series(dtype=float)).sum())
    yields = [{"ticker": str(row.get("ticker", "N/A")), "yield_pct": _holding_yield(row)} for row in frame.to_dict(orient="records")]
    positive = [row for row in yields if row["yield_pct"] > 0]
    highest = max(positive, key=lambda row: row["yield_pct"], default={"ticker": "N/A", "yield_pct": 0.0})
    lowest = min(positive, key=lambda row: row["yield_pct"], default={"ticker": "N/A", "yield_pct": 0.0})
    yield_summary = {"highest_yield_ticker": highest["ticker"], "highest_yield_pct": highest["yield_pct"], "lowest_positive_yield_ticker": lowest["ticker"], "lowest_positive_yield_pct": lowest["yield_pct"], "yield_spread_pct": highest["yield_pct"] - lowest["yield_pct"] if len(positive) >= 2 else None}

    allocation_source = frame.copy()
    allocation_source["asset_type"] = allocation_source["asset_type"].map(_insights_asset_type) if "asset_type" in allocation_source else "Other/Unassigned"
    allocation_source["market_value"] = pd.to_numeric(allocation_source.get("market_value", 0.0), errors="coerce").fillna(0.0)
    allocation = allocation_source.groupby("asset_type", dropna=False).agg(market_value=("market_value", "sum")).reset_index()
    allocation["allocation_pct"] = allocation["market_value"] / value * 100.0 if value else 0.0
    allocation = allocation.sort_values("market_value", ascending=False)

    contributions = analysis["contributions"]
    contribution_rows = contributions["holdings"]
    regular = finite(contributions.get("regular_biweekly")); espp = finite(contributions.get("espp_biweekly")); special = finite(contributions.get("special_biweekly"))
    contribution_summary = {
        "regular_biweekly": regular,
        "espp_biweekly": espp,
        "combined_biweekly": regular + espp,
        "regular_annual": regular * 26,
        "espp_annual": espp * 26,
        "combined_annual": (regular + espp) * 26,
        "special_contribution": special,
        "special_total": special,
        # Backward-compatible alias; special contributions are not recurring.
        "special_biweekly": special,
        "special_contribution_note": "Separate additional contribution; excluded from recurring biweekly and annual totals.",
    }
    active = sum(finite(row.get("total_contribution")) > 0 for row in contribution_rows)

    concentration = analysis["concentration"]
    income_sorted = frame.sort_values("annual_income", ascending=False)
    top3_income_pct = finite(income_sorted.head(3)["annual_income"].sum()) / income * 100.0 if income else 0.0
    observations = [
        f"Portfolio concentration: Largest holding is {concentration['largest_holding_by_value']} at {concentration['largest_holding_weight_pct']:.2f}% of portfolio value.",
        f"Highest dividend yield: {highest['ticker']} at {highest['yield_pct']:.2f}%.",
        "Dividend yield spread: Unavailable (fewer than two positive-yield holdings)." if yield_summary["yield_spread_pct"] is None else f"Dividend yield spread: {yield_summary['yield_spread_pct']:.2f} percentage points.",
        f"Income concentration: Top 3 holdings generate {top3_income_pct:.2f}% of annual income.",
        f"Contribution profile: {active} holdings receiving biweekly contributions.",
        f"Growth outlook: 10-year projected value ${analysis['snapshot']['projected_10y_value']:,.2f}, 20-year projected value ${analysis['snapshot']['projected_20y_value']:,.2f}.",
    ]
    if concentration.get("espp_concentration_flag"):
        observations.append("Employer-stock context: HD remains a concentration exposure while receiving ESPP contributions with a 15.00% discount.")
    if isinstance(hd_historical_context, dict) and hd_historical_context.get("available"):
        economics = hd_historical_context.get("documented_economics", {})
        realized = hd_historical_context.get("realized", {})
        coverage = hd_historical_context.get("coverage", {})
        reconciliation = hd_historical_context.get("reconciliation", {})
        summary = hd_historical_context.get("summary", {})
        observations.append(f"HD historical ESPP context: documented employee contributions ${finite(economics.get('espp_employee_contributions')):,.2f}, purchase discount benefit ${finite(economics.get('espp_purchase_discount_benefit')):,.2f}.")
        observations.append(f"HD documented realized gain is source-bounded: ${finite(realized.get('documented_realized_gain')):,.2f} with {finite(coverage.get('basis_coverage_pct')) * 100 if 0 <= finite(coverage.get('basis_coverage_pct')) <= 1 else finite(coverage.get('basis_coverage_pct')):.2f}% basis coverage.")
        observations.append(f"Complete lifetime HD return remains {summary.get('lifetime_return_status', 'UNKNOWN')} because Phase 3A status is {reconciliation.get('phase3a_status', 'UNKNOWN')} and historical/live difference is {reconciliation.get('historical_live_difference', 'Unknown')} shares.")
    lookthrough = analysis["fund_lookthrough"]
    if finite(lookthrough.get("unresolved_value")) > 0:
        observations.append(f"Fund look-through is incomplete: ${finite(lookthrough.get('unresolved_value')):,.2f} remains unresolved ({finite(lookthrough.get('fund_lookthrough_completeness_pct')):.2f}% coverage).")
    else:
        observations.append(f"Fund look-through coverage: {finite(lookthrough.get('fund_lookthrough_completeness_pct')):.2f}%.")

    return {
        "totals": {"portfolio_value": value, "annual_income": income, "weighted_yield_pct": income / value * 100.0 if value else 0.0, "average_monthly_income": income / 12.0 if income else 0.0},
        "yield": yield_summary,
        "contribution": {"rows": contribution_rows, "summary": contribution_summary, "active_contributors": active},
        "allocation": allocation.to_dict(orient="records"),
        "economic_exposure": analysis["totals"]["economic_exposure_allocation"],
        "direct_sector": analysis["direct_sector"],
        "income_allocation": analysis["totals"]["income_allocation_by_asset_type"],
        "income_quality": analysis["income_quality"],
        "concentration": {**concentration, "top3_income_pct": top3_income_pct},
        "fund_lookthrough": lookthrough,
        "data_completeness": analysis["data_completeness"],
        "hd_historical_context": hd_historical_context or {"available": False},
        "observations": observations,
    }

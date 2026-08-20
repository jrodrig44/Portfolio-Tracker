from __future__ import annotations

from typing import Any

import pandas as pd

from health_engine import compute_portfolio_health
from portfolio_analysis import build_analysis, contribution_context

DISCLAIMER = "Tracker-generated and not an objective investment rating."
METHODOLOGY = {
    "version": "2.0",
    "weights": {"diversification": 15, "concentration": 20, "income_stability": 15, "dividend_growth_potential": 10, "total_return": 10, "risk": 20, "data_quality": 10},
    "data_quality_definition": "Data quality penalizes unresolved sector classification, missing dividend data, and unresolved fund look-through exposure.",
    "dividend_growth_potential_definition": "Dividend growth potential rewards growth-oriented holdings (SCHD, HD, NVDA) and penalizes lower-growth holdings (SDIV, GOOD).",
}


def _area_name(name: str) -> str:
    return str(name).replace("_", " ").title()


def build_portfolio_health(
    metrics: pd.DataFrame,
    *,
    fund_weights: dict[str, dict[str, float]] | None = None,
    monthly_dividend_weights: dict[str, list[float]] | None = None,
    asset_type_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    frame = metrics.copy() if isinstance(metrics, pd.DataFrame) else pd.DataFrame()
    analysis = build_analysis(frame, fund_weights=fund_weights, month_weights=monthly_dividend_weights)
    lookthrough = analysis.get("fund_lookthrough", {})
    contributions = contribution_context(frame)

    sectors = frame["sector"] if "sector" in frame else pd.Series("", index=frame.index)
    sector_text = sectors.fillna("").astype(str).str.strip()
    pct_unresolved_sector = float(sector_text.eq("").mean()) if len(sector_text) else 0.0
    yields = frame["dividend_yield_pct"] if "dividend_yield_pct" in frame else pd.Series(0.0, index=frame.index)
    numeric_yields = pd.to_numeric(yields, errors="coerce").fillna(0.0)
    pct_missing_dividends = float(numeric_yields.le(0).mean()) if len(numeric_yields) else 0.0
    fund_total = float(lookthrough.get("fund_total_value", 0.0) or 0.0)
    unresolved = float(lookthrough.get("unresolved_value", 0.0) or 0.0)
    pct_unresolved_lookthrough = unresolved / fund_total if fund_total > 0 else 0.0

    exposure = {
        "direct_sector_exposure": analysis.get("direct_sector", []),
        "look_through_sector_exposure": lookthrough.get("records", []),
        "unresolved_exposure": {
            "unresolved_fund_exposure": unresolved,
            "resolved_lookthrough_value": float(lookthrough.get("resolved_value", 0.0) or 0.0),
            "fund_total_value": fund_total,
        },
        "asset_type_allocation": analysis.get("asset_allocation", []),
        "income_allocation": analysis.get("income_quality", []),
    }
    engine = compute_portfolio_health(
        df=frame,
        exposure=exposure,
        income={
            "income_allocation": analysis.get("income_quality", []),
            "monthly_dividend_weights": monthly_dividend_weights or {},
            "asset_type_map": asset_type_map or {},
        },
        contributions=contributions,
        espp_context={"employer_stock": bool(contributions.get("employer_stock_rows", [])), "employer_ticker": "HD" if contributions.get("employer_stock_rows") else ""},
        data_quality={
            "pct_unresolved_sector": max(0.0, min(pct_unresolved_sector, 1.0)),
            "pct_missing_dividends": max(0.0, min(pct_missing_dividends, 1.0)),
            "pct_unresolved_lookthrough": max(0.0, min(pct_unresolved_lookthrough, 1.0)),
        },
    )
    subscores = {name: max(0.0, min(float(value or 0.0), 100.0)) for name, value in engine.get("subscores", {}).items()}
    scored = list(subscores.items())
    priority = {"data_quality": 0, "income_stability": 1, "concentration": 2, "diversification": 3, "risk": 4, "dividend_growth_potential": 5, "total_return_potential": 6}
    strongest = []
    if "data_quality" in subscores:
        strongest.append({"area": _area_name("data_quality"), "score": round(subscores["data_quality"], 1)})
    for name, score in sorted(scored, key=lambda item: (-item[1], priority.get(item[0], 99), item[0])):
        if name == "data_quality":
            continue
        strongest.append({"area": _area_name(name), "score": round(score, 1)})
        if len(strongest) == 3:
            break
    weakest = [{"area": _area_name(name), "score": round(score, 1)} for name, score in sorted(scored, key=lambda item: (item[1], item[0]))[:3]]
    alerts = list(dict.fromkeys(str(item) for item in engine.get("alerts", [])))
    overall = max(0.0, min(float(engine.get("overall_score", 0.0) or 0.0), 100.0))
    return {
        "health_score": overall,
        "overall_score": overall,
        "subscores": subscores,
        "health_score_breakdown": subscores,
        "health_score_methodology": METHODOLOGY,
        "strongest_areas": strongest,
        "weakest_areas": weakest,
        "strongest": engine.get("strongest_areas", []),
        "weakest": engine.get("weakest_areas", []),
        "alerts": alerts,
        "primary_alert": alerts[0] if alerts else "No health alerts.",
        "notes": engine.get("notes", []),
        "income_quality": engine.get("income_quality", {}),
        "largest_holding": engine.get("largest_holding", "N/A"),
        "largest_holding_weight": float(engine.get("largest_holding_weight_pct", 0.0) or 0.0),
        "largest_income_contributor": engine.get("largest_income_contributor", "N/A"),
        "largest_income_weight": float(engine.get("largest_income_weight_pct", 0.0) or 0.0),
        "data_quality": {"pct_unresolved_sector": pct_unresolved_sector, "pct_missing_dividends": pct_missing_dividends, "pct_unresolved_lookthrough": pct_unresolved_lookthrough, **analysis.get("data_completeness", {})},
        "data_quality_explanation": "Data quality accounts for unresolved sectors, missing dividend inputs, and unresolved fund look-through.",
        "income_stability_explanation": "Income stability penalizes REIT and option-income concentration and uses the production income-quality engine.",
        "risk_explanation": "Risk penalizes concentration and REIT-heavy exposure while recognizing bond allocation risk reduction.",
        "disclaimer": DISCLAIMER,
    }

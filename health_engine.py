from __future__ import annotations

import math
from typing import Any

import pandas as pd

from income_quality_engine import compute_income_quality


OPTION_INCOME_ETFS = {"JEPI", "QYLD", "RYLD", "XYLD"}
BROAD_ETFS = {"VTI", "VOO", "SPY", "IVV", "SCHD"}
GROWTH_TICKERS = {"SCHD", "HD", "NVDA"}
LOW_GROWTH_TICKERS = {"SDIV", "GOOD"}
HIGH_YIELD_ETFS = {"SDIV", "JEPI", "QYLD", "RYLD", "XYLD"}
TECH_SECTORS = {"TECHNOLOGY", "INFORMATION TECHNOLOGY"}
BOND_ASSET_TYPES = {"BOND FUND"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in [None, ""]:
            return default
        numeric_value = float(value)
        return numeric_value if math.isfinite(numeric_value) else default
    except (TypeError, ValueError):
        return default


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, score))


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["ticker", "asset_type", "sector", "market_value", "annual_income", "dividend_yield_pct"])

    out = df.copy()
    for col in ["ticker", "asset_type", "sector"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str).str.strip()
    out["ticker"] = out["ticker"].str.upper()
    out["asset_type"] = out["asset_type"].str.upper()
    out["sector_upper"] = out["sector"].str.upper()

    for col in ["market_value", "annual_income", "dividend_yield_pct"]:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    total_value = float(out["market_value"].sum())
    out["weight"] = out["market_value"] / total_value if total_value > 0 else 0.0
    return out


def _average_monthly_weight_variance(month_weights: Any) -> float:
    if not isinstance(month_weights, dict) or not month_weights:
        return 0.0

    variances: list[float] = []
    for raw_weights in month_weights.values():
        if not isinstance(raw_weights, list) or len(raw_weights) != 12:
            continue
        weights = pd.to_numeric(pd.Series(raw_weights), errors="coerce").fillna(0.0)
        if float(weights.sum()) <= 0:
            continue
        variances.append(float(weights.var(ddof=0)))

    if not variances:
        return 0.0
    return float(sum(variances) / len(variances))


def _format_area_name(key: str) -> str:
    return key.replace("_", " ").title()


def compute_portfolio_health(
    df: pd.DataFrame,
    exposure: dict[str, Any],
    income: dict[str, Any],
    contributions: dict[str, Any],
    espp_context: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    """Return full health score package."""
    normalized = _normalize_df(df)
    if normalized.empty:
        return {
            "overall_score": 0.0,
            "subscores": {
                "diversification": 0.0,
                "concentration": 0.0,
                "income_stability": 0.0,
                "dividend_growth_potential": 0.0,
                "total_return_potential": 0.0,
                "risk": 0.0,
                "data_quality": 0.0,
            },
            "strongest_areas": [],
            "weakest_areas": [],
            "alerts": [],
            "notes": [
                "Income values are annualized estimates.",
                "Dividend yields sourced from Yahoo Finance.",
                "ESPP concentration evaluated separately.",
                "Look-through sector exposure incomplete for some funds.",
            ],
            "largest_holding": "N/A",
            "largest_holding_weight_pct": 0.0,
            "largest_income_contributor": "N/A",
            "largest_income_weight_pct": 0.0,
        }

    total_income = float(normalized["annual_income"].sum())
    num_holdings = int(len(normalized))

    largest_idx = int(normalized["weight"].idxmax())
    largest_holding = str(normalized.loc[largest_idx, "ticker"])
    largest_holding_weight = float(normalized.loc[largest_idx, "weight"])
    largest_holding_weight_pct = largest_holding_weight * 100.0

    if total_income > 0:
        income_weights = normalized["annual_income"] / total_income
        income_idx = int(income_weights.idxmax())
        largest_income_contributor = str(normalized.loc[income_idx, "ticker"])
        largest_income_weight = float(income_weights.loc[income_idx])
    else:
        largest_income_contributor = "N/A"
        largest_income_weight = 0.0

    largest_income_weight_pct = largest_income_weight * 100.0

    reit_weight_pct = float(normalized[normalized["asset_type"].eq("REIT")]["weight"].sum()) * 100.0
    option_income_weight_pct = float(normalized[normalized["ticker"].isin(OPTION_INCOME_ETFS)]["weight"].sum()) * 100.0

    growth_weight = float(normalized[normalized["ticker"].isin(GROWTH_TICKERS)]["weight"].sum())
    low_growth_weight = float(normalized[normalized["ticker"].isin(LOW_GROWTH_TICKERS)]["weight"].sum())

    tech_weight = float(normalized[normalized["sector_upper"].isin(TECH_SECTORS)]["weight"].sum())
    broad_etf_weight = float(normalized[normalized["ticker"].isin(BROAD_ETFS)]["weight"].sum())
    high_yield_weight = float(normalized[normalized["ticker"].isin(HIGH_YIELD_ETFS)]["weight"].sum())

    bond_weight = float(normalized[normalized["asset_type"].isin(BOND_ASSET_TYPES)]["weight"].sum())

    month_weights_map = income.get("monthly_dividend_weights", {}) if isinstance(income, dict) else {}
    asset_type_map = income.get("asset_type_map", {}) if isinstance(income, dict) else {}
    income_quality = compute_income_quality(normalized, month_weights_map, asset_type_map)
    monthly_variance = _average_monthly_weight_variance(month_weights_map)

    pct_unresolved_sector = _to_float(data_quality.get("pct_unresolved_sector", 0.0)) if isinstance(data_quality, dict) else 0.0
    pct_missing_dividends = _to_float(data_quality.get("pct_missing_dividends", 0.0)) if isinstance(data_quality, dict) else 0.0
    pct_unresolved_lookthrough = _to_float(data_quality.get("pct_unresolved_lookthrough", 0.0)) if isinstance(data_quality, dict) else 0.0

    diversification = min(100.0, (num_holdings / 15.0) * 100.0)

    concentration = 100.0 - (largest_holding_weight_pct * 1.2)
    concentration = _clamp(concentration)

    income_stability = 100.0
    income_stability -= (reit_weight_pct * 0.4)
    income_stability -= (option_income_weight_pct * 0.5)
    income_stability -= (monthly_variance * 100.0)
    income_stability = _clamp(income_stability)
    income_stability = _clamp(
        (income_stability * 0.50)
        + (_to_float(income_quality.get("distribution_stability_score", 0.0)) * 0.30)
        + (_to_float(income_quality.get("income_quality_score", 0.0)) * 0.20)
    )

    dividend_growth_potential = 50.0
    dividend_growth_potential += (growth_weight * 40.0)
    dividend_growth_potential -= (low_growth_weight * 30.0)
    dividend_growth_potential = _clamp(dividend_growth_potential)

    total_return_potential = 50.0
    total_return_potential += (tech_weight * 40.0)
    total_return_potential += (broad_etf_weight * 20.0)
    total_return_potential -= (high_yield_weight * 30.0)
    total_return_potential = _clamp(total_return_potential)

    risk = 100.0
    risk -= (largest_holding_weight * 100.0)
    risk -= (reit_weight_pct * 30.0 / 100.0)
    risk += (bond_weight * 20.0)
    risk = _clamp(risk)

    data_quality_score = 100.0
    data_quality_score -= (pct_unresolved_sector * 50.0)
    data_quality_score -= (pct_missing_dividends * 30.0)
    data_quality_score -= (pct_unresolved_lookthrough * 20.0)
    data_quality_score = _clamp(data_quality_score)

    subscores = {
        "diversification": round(diversification, 2),
        "concentration": round(concentration, 2),
        "income_stability": round(income_stability, 2),
        "dividend_growth_potential": round(dividend_growth_potential, 2),
        "total_return_potential": round(total_return_potential, 2),
        "risk": round(risk, 2),
        "data_quality": round(data_quality_score, 2),
    }

    overall_score = (
        subscores["diversification"] * 0.15
        + subscores["concentration"] * 0.20
        + subscores["income_stability"] * 0.15
        + subscores["dividend_growth_potential"] * 0.10
        + subscores["total_return_potential"] * 0.10
        + subscores["risk"] * 0.20
        + subscores["data_quality"] * 0.10
    )

    sorted_desc = sorted(subscores.items(), key=lambda item: item[1], reverse=True)
    sorted_asc = sorted(subscores.items(), key=lambda item: item[1])
    strongest_areas = [_format_area_name(name) for name, _ in sorted_desc[:3]]
    weakest_areas = [_format_area_name(name) for name, _ in sorted_asc[:3]]

    alerts: list[str] = []
    if largest_holding_weight_pct > 40.0:
        alerts.append(f"Concentration alert: {largest_holding} is {largest_holding_weight_pct:.1f}% of portfolio.")

    employer_stock = False
    if "employer_stock" in normalized.columns:
        employer_stock = bool(normalized["employer_stock"].fillna(False).astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"}).any())
    if isinstance(espp_context, dict):
        employer_stock = employer_stock or bool(espp_context.get("employer_stock", False))
    if employer_stock:
        alerts.append("Employer stock concentration should be evaluated separately due to ESPP participation.")

    if bool(normalized["dividend_yield_pct"].gt(10.0).any()):
        alerts.append("High-yield alert: one or more holdings exceed 10% yield.")

    unresolved_value = _to_float(exposure.get("unresolved_exposure", {}).get("unresolved_fund_exposure", 0.0)) if isinstance(exposure, dict) else 0.0
    fund_total_value = _to_float(exposure.get("unresolved_exposure", {}).get("fund_total_value", 0.0)) if isinstance(exposure, dict) else 0.0
    unresolved_ratio = unresolved_value / fund_total_value if fund_total_value > 0 else 0.0
    if unresolved_ratio > 0.05:
        alerts.append("Unresolved sector exposure alert.")
    for alert in income_quality.get("alerts", []) if isinstance(income_quality, dict) else []:
        if alert not in alerts:
            alerts.append(str(alert))

    notes = [
        "Income values are annualized estimates.",
        "Dividend yields sourced from Yahoo Finance.",
        "ESPP concentration evaluated separately.",
        "Look-through sector exposure incomplete for some funds.",
    ]
    for note in income_quality.get("notes", []) if isinstance(income_quality, dict) else []:
        note_text = str(note)
        if note_text not in notes:
            notes.append(note_text)

    return {
        "overall_score": round(overall_score, 1),
        "subscores": subscores,
        "strongest_areas": strongest_areas,
        "weakest_areas": weakest_areas,
        "alerts": alerts,
        "notes": notes,
        "income_quality": income_quality,
        "largest_holding": largest_holding,
        "largest_holding_weight_pct": round(largest_holding_weight_pct, 2),
        "largest_income_contributor": largest_income_contributor,
        "largest_income_weight_pct": round(largest_income_weight_pct, 2),
    }

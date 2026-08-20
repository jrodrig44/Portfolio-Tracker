from __future__ import annotations

from typing import Any
import pandas as pd


def metrics_frame(holdings: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(holdings).copy()
    if frame.empty:
        return frame
    numeric = ["shares", "current_price", "amount_invested", "annual_div_per_share", "effective_annual_div_per_share", "dividend_yield_pct", "regular_biweekly_contribution", "espp_biweekly_contribution", "special_contribution", "espp_acquired_shares"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame.get(column, 0.0), errors="coerce").fillna(0.0)
    frame["ticker"] = frame["ticker"].fillna("").astype(str).str.strip().str.upper()
    frame["cost_basis"] = frame["amount_invested"]
    frame["avg_cost"] = (frame["amount_invested"] / frame["shares"].replace(0, pd.NA)).fillna(0.0)
    frame["market_value"] = frame["shares"] * frame["current_price"]
    frame["unrealized_gain"] = frame["market_value"] - frame["cost_basis"]
    estimated = frame["current_price"] * frame["dividend_yield_pct"] / 100.0
    effective = frame["annual_div_per_share"].where(frame["annual_div_per_share"] > 0, frame["effective_annual_div_per_share"])
    frame["effective_annual_div_per_share"] = effective.where(effective > 0, estimated)
    frame["annual_income"] = frame["shares"] * frame["effective_annual_div_per_share"]
    frame["annualized_income"] = frame["annual_income"]
    total_value = float(frame["market_value"].sum())
    total_income = float(frame["annual_income"].sum())
    frame["portfolio_weight_pct"] = frame["market_value"] / total_value if total_value else 0.0
    frame["income_allocation_pct"] = frame["annual_income"] / total_income if total_income else 0.0
    frame["yield_on_cost_pct"] = (frame["effective_annual_div_per_share"] / frame["avg_cost"].replace(0, pd.NA) * 100).fillna(0.0)
    return frame.fillna({"asset_type": "Other", "sector": "Unclassified"})


def portfolio_totals(metrics: pd.DataFrame) -> dict[str, Any]:
    value = float(metrics.get("market_value", pd.Series(dtype=float)).sum())
    cost = float(metrics.get("cost_basis", pd.Series(dtype=float)).sum())
    income = float(metrics.get("annual_income", pd.Series(dtype=float)).sum())
    largest = metrics.sort_values("market_value", ascending=False).iloc[0] if not metrics.empty else {}
    largest_value = float(largest.get("market_value", 0.0) or 0.0)
    return {"total_portfolio_value": value, "total_cost_basis": cost, "total_unrealized_gain": value-cost, "total_estimated_annual_income": income, "weighted_portfolio_yield": income/value*100 if value else 0.0, "largest_holding": largest.get("ticker", "N/A"), "largest_holding_weight": largest_value/value*100 if value else 0.0}


def future_value(current: float, annual_return: float, biweekly: float, years: int) -> float:
    periods = years * 26
    rate = annual_return / 26
    return current * (1 + rate) ** periods + biweekly * (((1 + rate) ** periods - 1) / rate) if rate else current + biweekly * periods

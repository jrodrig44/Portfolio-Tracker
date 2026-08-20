from __future__ import annotations

from typing import Any
import pandas as pd

from nicegui_app.services.configuration import ESPP_CONTRIB_BIWEEKLY


def _percentage(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number * 100.0 if 0 < number <= 1 else number


def metrics_frame(
    holdings: list[dict[str, Any]],
    price_map: dict[str, float] | None = None,
    yield_map: dict[str, float] | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(holdings).copy()
    if frame.empty:
        return frame
    numeric = ["shares", "current_price", "amount_invested", "annual_div_per_share", "effective_annual_div_per_share", "dividend_yield_pct", "regular_biweekly_contribution", "espp_biweekly_contribution", "special_contribution", "espp_acquired_shares", "espp_discount_pct"]
    for column in numeric:
        source = frame[column] if column in frame else pd.Series(0.0, index=frame.index)
        frame[column] = pd.to_numeric(source, errors="coerce").fillna(0.0)
    frame["ticker"] = frame["ticker"].fillna("").astype(str).str.strip().str.upper()
    if price_map is not None:
        live_prices = pd.to_numeric(frame["ticker"].map(price_map), errors="coerce").fillna(0.0)
        frame.loc[frame["current_price"].le(0) & live_prices.gt(0), "current_price"] = live_prices
    if yield_map is not None:
        live_yields = pd.to_numeric(frame["ticker"].map(yield_map), errors="coerce").fillna(0.0)
        frame.loc[frame["dividend_yield_pct"].le(0) & live_yields.gt(0), "dividend_yield_pct"] = live_yields
    employer = frame["employer_stock"] if "employer_stock" in frame else pd.Series(False, index=frame.index)
    espp = frame["espp_enabled"] if "espp_enabled" in frame else pd.Series(False, index=frame.index)
    frame["employer_stock"] = employer.fillna(False).astype(bool)
    frame["espp_enabled"] = espp.fillna(False).astype(bool)
    hd = frame["ticker"].eq("HD")
    frame.loc[hd, ["employer_stock", "espp_enabled"]] = True
    frame.loc[hd & frame["espp_biweekly_contribution"].le(0), "espp_biweekly_contribution"] = ESPP_CONTRIB_BIWEEKLY
    frame.loc[hd, ["regular_biweekly_contribution", "special_contribution"]] = 0.0
    frame.loc[hd, "espp_discount_pct"] = 15.0
    frame["espp_discount_pct"] = frame["espp_discount_pct"].map(_percentage)
    frame["espp_acquired_shares"] = frame[["shares", "espp_acquired_shares"]].min(axis=1).clip(lower=0.0)
    frame.loc[hd & frame["espp_acquired_shares"].le(0), "espp_acquired_shares"] = frame.loc[hd, "shares"]
    frame["regular_shares"] = (frame["shares"] - frame["espp_acquired_shares"]).clip(lower=0.0)
    frame["cost_basis"] = frame["amount_invested"]
    frame["avg_cost"] = (frame["amount_invested"] / frame["shares"].replace(0, pd.NA)).fillna(0.0)
    frame["market_value"] = frame["shares"] * frame["current_price"]
    frame["current_value"] = frame["market_value"]
    frame["unrealized_gain"] = frame["market_value"] - frame["cost_basis"]
    estimated = frame["current_price"] * frame["dividend_yield_pct"] / 100.0
    effective = frame["annual_div_per_share"].where(frame["annual_div_per_share"] > 0, frame["effective_annual_div_per_share"])
    frame["effective_annual_div_per_share"] = effective.where(effective > 0, estimated)
    frame["annual_income"] = frame["shares"] * frame["effective_annual_div_per_share"]
    frame["annualized_income"] = frame["annual_income"]
    frame["estimated_annual_distribution_per_share"] = frame["effective_annual_div_per_share"]
    total_value = float(frame["market_value"].sum())
    total_income = float(frame["annual_income"].sum())
    frame["portfolio_weight_pct"] = frame["market_value"] / total_value if total_value else 0.0
    frame["portfolio_yield_pct"] = frame["annual_income"] / total_value * 100 if total_value else 0.0
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

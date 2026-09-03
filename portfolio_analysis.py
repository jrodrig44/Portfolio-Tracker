from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd
from beneficial_exposure import beneficial_metrics_frame

from income_quality_engine import compute_income_quality

OPTION_INCOME_ETFS = {"JEPI", "JEPQ", "QYLD", "RYLD", "XYLD"}
FUND_ASSET_TYPES = {"ETF", "Bond Fund", "CEF"}
SECTOR_ALIASES = {"realestate": "Real Estate"}
ECONOMIC_EXPOSURE = {"Stock": "Equities", "ETF": "Equities", "REIT": "REITs", "CEF": "Alternative Income", "BDC": "Alternative Income", "Bond Fund": "Bonds", "Other": "Other"}


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def percentage(value: Any, default: float = 0.0) -> float:
    number = finite(value, default)
    return number * 100.0 if 0.0 < abs(number) < 1.0 else number


def canonical_asset_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"reit", "reits", "real estate", "real estate investment trust"}: return "REIT"
    if text in {"etf", "etfs"}: return "ETF"
    if text in {"stock", "stocks", "equity", "equities", "etf/stock", ""}: return "Stock"
    if text in {"cef", "cefs"}: return "CEF"
    if text in {"bdc", "bdcs"}: return "BDC"
    if text in {"bond", "bond fund", "bond funds"}: return "Bond Fund"
    return "Other"


def normalize_sector(value: Any, unresolved: str = "Unresolved Direct Sector") -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "nan", "none", "n/a"}: return unresolved
    return SECTOR_ALIASES.get(re.sub(r"[^a-z]", "", text.lower()), text)


def future_value(current: float, annual_return: float, biweekly: float, years: int) -> float:
    periods = years * 26
    if periods <= 0:
        return current
    rate = (annual_return / 100.0 if annual_return > 1 else annual_return) / 26
    return current * (1 + rate) ** periods + biweekly * (((1 + rate) ** periods - 1) / rate) if rate else current + biweekly * periods


SNOWBALL_MILESTONES = (
    ("Early Snowball (25%)", 0.25),
    ("Momentum Building (50%)", 0.50),
    ("Real Snowball (100%)", 1.00),
    ("Compounding Dominates (200%)", 2.00),
)


def years_to_income_target(current_income: float, target_income: float, annual_income_growth: float) -> float:
    """Return the original Streamlit linear estimate, or infinity if unreachable."""
    if target_income <= current_income:
        return 0.0
    if annual_income_growth <= 0:
        return float("inf")
    return (target_income - current_income) / annual_income_growth


def snowball_tracker(
    current_annual_income: float,
    weighted_yield_pct: float,
    regular_biweekly_contribution: float = 280.0,
) -> dict[str, Any]:
    """Build the portfolio-income snowball model used by Portfolio Analysis.

    Targets are multiples of the regular Portfolio Analysis contribution
    assumption. ESPP payroll and special contributions—including HD ESPP—are
    excluded, matching Streamlit. Estimated years intentionally assume linear
    income growth from one year's contribution at today's weighted yield; this
    is not a DRIP or compound-growth simulation.
    """
    income = finite(current_annual_income)
    annual_contribution = finite(regular_biweekly_contribution) * 26
    annual_income_growth = annual_contribution * (finite(weighted_yield_pct) / 100)
    milestones = []
    for name, multiple in SNOWBALL_MILESTONES:
        target = annual_contribution * multiple
        raw_progress = income / target * 100 if target > 0 else 0.0
        milestones.append({
            "name": name,
            "target_annual_income": target,
            "progress_pct": raw_progress,
            "display_progress_pct": min(max(raw_progress, 0.0), 100.0),
            "years_to_reach": years_to_income_target(income, target, annual_income_growth),
        })
    next_milestone = milestones[-1]
    for milestone in milestones:
        if income < milestone["target_annual_income"]:
            next_milestone = milestone
            break
    years_to_next = next_milestone["years_to_reach"]
    return {
        "annual_contribution": annual_contribution,
        "current_annual_income": income,
        "income_vs_contribution_pct": income / annual_contribution * 100 if annual_contribution > 0 else 0.0,
        "annual_income_growth": annual_income_growth,
        "milestones": milestones,
        "next_milestone": {
            "name": next_milestone["name"],
            "target_annual_income": next_milestone["target_annual_income"],
            "amount_still_needed": max(next_milestone["target_annual_income"] - income, 0.0),
            "estimated_months_away": None if math.isinf(years_to_next) else int(round(years_to_next * 12)),
        },
    }


def contribution_context(metrics: pd.DataFrame, default_espp: float = 100.0) -> dict[str, Any]:
    rows=[]
    for raw in metrics.to_dict(orient="records"):
        ticker=str(raw.get("ticker","")).strip().upper()
        regular=finite(raw.get("regular_biweekly_contribution"));espp=finite(raw.get("espp_biweekly_contribution"));special=finite(raw.get("special_contribution"))
        employer=bool(raw.get("employer_stock",False) or ticker=="HD");enabled=bool(raw.get("espp_enabled",False) or ticker=="HD")
        if ticker=="HD": regular=0.0;special=0.0;espp=espp if espp>0 else default_espp
        rows.append({"ticker":ticker,"regular_biweekly_contribution":regular,"espp_biweekly_contribution":espp,"special_contribution":special,"total_contribution":regular+espp+special,"contribution_type":"espp" if ticker=="HD" else "regular","employer_stock":employer,"espp_enabled":enabled,"espp_discount_pct":15.0 if ticker=="HD" else percentage(raw.get("espp_discount_pct"))})
    regular=sum(r["regular_biweekly_contribution"] for r in rows);espp=sum(r["espp_biweekly_contribution"] for r in rows);special=sum(r["special_contribution"] for r in rows)
    total_value=float(metrics.get("market_value",pd.Series(dtype=float)).sum());total_income=float(metrics.get("annual_income",pd.Series(dtype=float)).sum())
    employers=[]
    for row in rows:
        if not row["employer_stock"]:continue
        match=metrics[metrics["ticker"].astype(str).str.upper().eq(row["ticker"])]
        value=finite(match.iloc[0].get("market_value")) if not match.empty else 0;income=finite(match.iloc[0].get("annual_income")) if not match.empty else 0
        employers.append({"ticker":row["ticker"],"capital_concentration_pct":value/total_value*100 if total_value else 0,"income_concentration_pct":income/total_income*100 if total_income else 0})
    return {"holdings":rows,"regular_biweekly":regular,"espp_biweekly":espp,"special_biweekly":special,"total_annual_contributions":(regular+espp+special)*26,"summary":{"total_regular_biweekly_contribution":regular,"annual_regular_contribution":regular*26,"total_espp_biweekly_contribution":espp,"annual_espp_contribution":espp*26,"special_biweekly_contribution":special,"estimated_combined_annual_contributions":(regular+espp+special)*26,"holdings_with_regular_contributions":sum(r["regular_biweekly_contribution"]>0 for r in rows),"holdings_with_espp_contributions":sum(r["espp_biweekly_contribution"]>0 for r in rows),"hd_espp_discount_pct":15.0},"employer_stock_rows":employers}


def resolve_fund_lookthrough(metrics: pd.DataFrame, weights: dict[str,dict[str,float]]|None=None) -> dict[str,Any]:
    weights=weights or {};total=finite(metrics.get("market_value",pd.Series(dtype=float)).sum());sector_values={};sources={};unresolved=[];resolved_value=0.0;fund_total=0.0
    for row in metrics.to_dict(orient="records"):
        asset=canonical_asset_type(row.get("asset_type"));ticker=str(row.get("ticker","")).upper();value=finite(row.get("market_value"))
        if asset not in FUND_ASSET_TYPES or value<=0:continue
        fund_total+=value;resolved=0.0
        valid_weights={}
        for sector,raw_weight in weights.get(ticker,{}).items():
            weight=finite(raw_weight);weight=weight/100 if weight>1 else weight
            if weight<=0:continue
            label=normalize_sector(str(sector).replace("_"," ").title(),"Unresolved Fund Exposure")
            valid_weights[label]=valid_weights.get(label,0.0)+weight
        weight_total=sum(valid_weights.values())
        scale=1.0/weight_total if weight_total>1.0 else 1.0
        for label,weight in valid_weights.items():
            amount=value*weight*scale
            if amount<=0:continue
            sector_values[label]=sector_values.get(label,0)+amount;sources.setdefault(label,set()).add(ticker);resolved+=amount
        resolved=min(resolved,value)
        resolved_value+=resolved
        missing=max(value-resolved,0)
        if missing:unresolved.append({"Fund_Type":asset,"Holdings":ticker,"Market_Value":missing,"Portfolio_Weight":missing/total*100 if total else 0})
    records=[{"Sector":sector,"Source_Holdings":", ".join(sorted(sources[sector])),"Market_Value":value,"Portfolio_Weight":value/total*100 if total else 0} for sector,value in sorted(sector_values.items(),key=lambda item:item[1],reverse=True)]
    resolved_value=min(resolved_value,fund_total);coverage=resolved_value/fund_total*100 if fund_total else 100.0
    return {"records":records,"unresolved_records":unresolved,"resolved_value":resolved_value,"unresolved_value":max(fund_total-resolved_value,0),"fund_total_value":fund_total,"fund_lookthrough_completeness_pct":max(0.0,min(coverage,100.0))}


def build_analysis(metrics: pd.DataFrame, fund_weights: dict[str,dict[str,float]]|None=None, expected_return:float=.08, projection_biweekly:float=280.0, month_weights:dict[str,list[float]]|None=None, hd_historical_context: dict[str,Any]|None=None) -> dict[str,Any]:
    # Analysis describes economic exposure, so reconciled Equate HD supplements
    # (and never replaces or duplicates) the Merrill custody row.
    df=beneficial_metrics_frame(metrics, hd_historical_context)
    if df.empty:return {"totals":{},"summary":{},"snapshot":{},"snowball":snowball_tracker(0.0,0.0,projection_biweekly),"concentration":{},"asset_allocation":[],"direct_sector":[],"income_quality":[],"income_quality_summary":compute_income_quality(df,{},{}),"contributions":contribution_context(df),"fund_lookthrough":resolve_fund_lookthrough(df,fund_weights)}
    asset_types=df["asset_type"] if "asset_type" in df else pd.Series("Stock",index=df.index)
    sectors=df["sector"] if "sector" in df else pd.Series("",index=df.index)
    df["asset_type"]=asset_types.map(canonical_asset_type);df["sector"]=sectors.map(normalize_sector)
    for col in ("market_value","cost_basis","unrealized_gain","annual_income"):df[col]=pd.to_numeric(df.get(col,0),errors="coerce").fillna(0.0)
    value=float(df.market_value.sum());cost=float(df.cost_basis.sum());income=float(df.annual_income.sum());top=df.sort_values("market_value",ascending=False).iloc[0];top_income=df.sort_values("annual_income",ascending=False).iloc[0]
    assets=df.groupby("asset_type",dropna=False).agg(market_value=("market_value","sum"),annualized_income=("annual_income","sum")).reset_index();assets["allocation_pct"]=assets.market_value/value*100 if value else 0;assets["income_allocation_pct"]=assets.annualized_income/income*100 if income else 0
    econ=df.assign(economic_exposure_category=df.asset_type.map(ECONOMIC_EXPOSURE)).groupby("economic_exposure_category").agg(market_value=("market_value","sum"),annualized_income=("annual_income","sum")).reset_index();econ["portfolio_weight_pct"]=econ.market_value/value*100 if value else 0
    direct=df[~df.asset_type.isin(FUND_ASSET_TYPES)].groupby("sector").agg(market_value=("market_value","sum"),annualized_income=("annual_income","sum")).reset_index();direct["portfolio_weight_pct"]=direct.market_value/value*100 if value else 0
    contributions=contribution_context(df);quality=compute_income_quality(df,month_weights or {},dict(zip(df.ticker,df.asset_type)))
    quality_rows=[]
    for _,row in df.iterrows():
        classification=quality["holding_classifications"].get(str(row.ticker).upper(),{})
        quality_rows.append({"ticker":row.ticker,"asset_type":row.asset_type,"dividend_yield_pct":finite(row.get("dividend_yield_pct")),"annualized_income":finite(row.annual_income),"income_quality_classification":classification.get("income_type","Unknown"),**{k:v for k,v in classification.items() if k!="income_type"}})
    employer=contributions["employer_stock_rows"]
    historical = hd_historical_context if isinstance(hd_historical_context, dict) else None
    historical_note = " Historical ESPP activity is documented separately and does not change forward contribution assumptions." if historical and historical.get("available") else ""
    concentration={"largest_holding_by_value":top.ticker,"largest_holding_weight_pct":finite(top.market_value)/value*100 if value else 0,"largest_income_contributor":top_income.ticker,"largest_income_weight_pct":finite(top_income.annual_income)/income*100 if income else 0,"espp_concentration_flag":bool(employer),"employer_stock":employer,"guidance":("Employer-stock concentration detected. Apply ESPP-specific context." + historical_note) if employer else "Capital concentration and income concentration should be evaluated separately."}
    lookthrough=resolve_fund_lookthrough(df,fund_weights)
    direct_resolved=float(df[~df.asset_type.isin(FUND_ASSET_TYPES)&~df.sector.eq("Unresolved Direct Sector")].market_value.sum());direct_total=float(df[~df.asset_type.isin(FUND_ASSET_TYPES)].market_value.sum())
    overall_coverage=(direct_resolved+lookthrough["resolved_value"])/value*100 if value else 0
    completeness={"direct_sector_classification_coverage_pct":direct_resolved/direct_total*100 if direct_total else 100,"fund_lookthrough_coverage_pct":lookthrough["fund_lookthrough_completeness_pct"],"overall_resolved_exposure_coverage_pct":max(0.0,min(overall_coverage,100.0))}
    totals={"total_portfolio_value":value,"total_cost_basis":cost,"total_unrealized_gain":value-cost,"total_estimated_annual_income":income,"weighted_portfolio_yield":income/value*100 if value else 0,"largest_holding":top.ticker,"largest_holding_weight":finite(top.market_value)/value*100 if value else 0,"economic_exposure_allocation":econ.to_dict("records"),"income_allocation_by_asset_type":assets.to_dict("records")}
    return {"totals":totals,"summary":{"active_contributors":sum(r["total_contribution"]>0 for r in contributions["holdings"])},"snapshot":{"projected_10y_value":future_value(value,expected_return,projection_biweekly,10),"projected_20y_value":future_value(value,expected_return,projection_biweekly,20),"projection_contribution_note":"Projection calculations use the regular $280 biweekly contribution assumption and do not include ESPP contributions unless otherwise stated."},"snowball":snowball_tracker(income,totals["weighted_portfolio_yield"],projection_biweekly),"concentration":concentration,"hd_historical_context":historical or {"available":False},"asset_allocation":assets.to_dict("records"),"direct_sector":direct.to_dict("records"),"income_quality":quality_rows,"income_quality_summary":quality,"contributions":contributions,"fund_lookthrough":lookthrough,"data_completeness":completeness}

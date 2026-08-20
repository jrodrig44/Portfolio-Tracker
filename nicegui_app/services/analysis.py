from __future__ import annotations

from typing import Any
import pandas as pd

from nicegui_app.services.calculations import future_value, portfolio_totals
from nicegui_app.services.configuration import DEFAULT_EXPECTED_RETURN, DEFAULT_PROJECTION_BIWEEKLY_CONTRIB


def _economic(asset_type: str) -> str:
    return {"Stock": "Equities", "ETF": "Equities", "REIT": "REITs", "CEF": "Alternative Income", "BDC": "Alternative Income", "Bond Fund": "Bonds"}.get(str(asset_type), "Other")


def concentration(metrics: pd.DataFrame) -> dict[str, Any]:
    totals = portfolio_totals(metrics)
    income = float(metrics["annual_income"].sum()) if not metrics.empty else 0.0
    top_income = metrics.sort_values("annual_income", ascending=False).iloc[0] if not metrics.empty else {}
    employer = bool(metrics.get("employer_stock", pd.Series(False, index=metrics.index)).fillna(False).astype(bool).any())
    return {"largest_holding_by_value": totals["largest_holding"], "largest_holding_weight_pct": totals["largest_holding_weight"], "largest_income_contributor": top_income.get("ticker", "N/A"), "largest_income_weight_pct": float(top_income.get("annual_income", 0.0) or 0.0)/income*100 if income else 0.0, "espp_concentration_flag": employer, "guidance": "Employer-stock concentration detected. Apply ESPP-specific context." if employer else "Capital concentration and income concentration should be evaluated separately."}


def contribution_context(metrics: pd.DataFrame) -> dict[str, Any]:
    rows=[]
    for row in metrics.to_dict(orient="records"):
        regular=float(row.get("regular_biweekly_contribution",0) or 0); espp=float(row.get("espp_biweekly_contribution",0) or 0); special=float(row.get("special_contribution",0) or 0)
        rows.append({"ticker":row.get("ticker"),"regular_biweekly_contribution":regular,"espp_biweekly_contribution":espp,"special_contribution":special,"total_contribution":regular+espp+special})
    regular=sum(r["regular_biweekly_contribution"] for r in rows); espp=sum(r["espp_biweekly_contribution"] for r in rows)
    return {"holdings":rows,"regular_biweekly":regular,"espp_biweekly":espp,"total_annual_contributions":(regular+espp)*26,"employer_stock_rows":[r for r,row in zip(rows,metrics.to_dict(orient="records")) if row.get("employer_stock")]}


def income_quality_records(metrics: pd.DataFrame) -> list[dict[str, Any]]:
    def quality(row: pd.Series) -> str:
        asset=str(row.get("asset_type", "")); y=float(row.get("dividend_yield_pct",0) or 0)
        if asset == "REIT": return "REIT distribution"
        if asset == "Bond Fund": return "Bond fund distribution"
        if y > 10: return "High-yield distribution"
        return "Traditional dividend / distribution"
    return [{"ticker":row.get("ticker"),"asset_type":row.get("asset_type"),"dividend_yield_pct":float(row.get("dividend_yield_pct",0) or 0),"annualized_income":float(row.get("annual_income",0) or 0),"income_quality_classification":quality(row)} for _,row in metrics.iterrows()]


def analysis_package(metrics: pd.DataFrame) -> dict[str, Any]:
    totals=portfolio_totals(metrics); value=totals["total_portfolio_value"]
    assets=metrics.groupby("asset_type",dropna=False).agg(market_value=("market_value","sum"),annualized_income=("annual_income","sum")).reset_index(); assets["allocation_pct"]=assets["market_value"]/value*100 if value else 0
    econ=metrics.assign(economic_exposure_category=metrics["asset_type"].map(_economic)).groupby("economic_exposure_category",dropna=False).agg(market_value=("market_value","sum")).reset_index(); econ["portfolio_weight_pct"]=econ["market_value"]/value*100 if value else 0
    sectors=metrics.groupby("sector",dropna=False).agg(market_value=("market_value","sum"),annual_income=("annual_income","sum")).reset_index(); sectors["portfolio_weight_pct"]=sectors["market_value"]/value*100 if value else 0
    contributions=contribution_context(metrics)
    snapshot={"projected_10y_value":future_value(value,DEFAULT_EXPECTED_RETURN,DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,10),"projected_20y_value":future_value(value,DEFAULT_EXPECTED_RETURN,DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,20),"projection_contribution_note":"Projection calculations use the regular $280 biweekly contribution assumption and do not include ESPP contributions unless otherwise stated."}
    return {"totals":{**totals,"economic_exposure_allocation":econ.to_dict(orient="records"),"income_allocation_by_asset_type":assets.to_dict(orient="records")},"summary":{"active_contributors":sum(r["total_contribution"]>0 for r in contributions["holdings"])},"snapshot":snapshot,"concentration":concentration(metrics),"asset_allocation":assets.to_dict(orient="records"),"direct_sector":sectors.to_dict(orient="records"),"income_quality":income_quality_records(metrics),"contributions":contributions}

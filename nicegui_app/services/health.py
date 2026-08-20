from __future__ import annotations

from typing import Any
import pandas as pd

from nicegui_app.services.analysis import concentration, income_quality_records


def _clamp(value: float) -> float: return max(0.0,min(100.0,value))


def health_package(metrics: pd.DataFrame) -> dict[str, Any]:
    if metrics.empty: return {"health_score":0.0,"subscores":{},"strongest_areas":[],"weakest_areas":[],"alerts":[],"notes":[]}
    frame=metrics.copy(); total=float(frame["market_value"].sum()); frame["weight"]=frame["market_value"]/total if total else 0
    conc=concentration(frame); largest=conc["largest_holding_weight_pct"]
    reit=float(frame.loc[frame["asset_type"].eq("REIT"),"weight"].sum())*100
    option=float(frame.loc[frame["ticker"].isin({"JEPI","QYLD","RYLD","XYLD"}),"weight"].sum())*100
    growth=float(frame.loc[frame["ticker"].isin({"SCHD","HD","NVDA"}),"weight"].sum()); low=float(frame.loc[frame["ticker"].isin({"SDIV","GOOD"}),"weight"].sum())
    tech=float(frame.loc[frame["sector"].fillna("").str.upper().isin({"TECHNOLOGY","INFORMATION TECHNOLOGY"}),"weight"].sum()); broad=float(frame.loc[frame["ticker"].isin({"VTI","VOO","SPY","IVV","SCHD"}),"weight"].sum()); high=float(frame.loc[frame["ticker"].isin({"SDIV","JEPI","QYLD","RYLD","XYLD"}),"weight"].sum()); bond=float(frame.loc[frame["asset_type"].eq("Bond Fund"),"weight"].sum())
    unresolved_sector=float(frame["sector"].fillna("").astype(str).str.strip().isin({"","Unclassified"}).mean()); missing_div=float(pd.to_numeric(frame["dividend_yield_pct"],errors="coerce").fillna(0).le(0).mean())
    scores={"diversification":min(100,len(frame)/15*100),"concentration":_clamp(100-largest*1.2),"income_stability":_clamp(100-reit*.4-option*.5),"dividend_growth_potential":_clamp(50+growth*40-low*30),"total_return_potential":_clamp(50+tech*40+broad*20-high*30),"risk":_clamp(100-largest-reit*.3+bond*20),"data_quality":_clamp(100-unresolved_sector*50-missing_div*30)}
    scores={k:round(v,2) for k,v in scores.items()}; weights={"diversification":.15,"concentration":.20,"income_stability":.15,"dividend_growth_potential":.10,"total_return_potential":.10,"risk":.20,"data_quality":.10}; overall=round(sum(scores[k]*weights[k] for k in scores),1)
    strongest=sorted(scores,key=scores.get,reverse=True)[:3]; weakest=sorted(scores,key=scores.get)[:3]; alerts=[]
    if largest>40: alerts.append(f"Concentration alert: {conc['largest_holding_by_value']} is {largest:.1f}% of portfolio.")
    if conc["espp_concentration_flag"]: alerts.append("Employer stock concentration should be evaluated separately due to ESPP participation.")
    if bool(frame["dividend_yield_pct"].gt(10).any()): alerts.append("High-yield alert: one or more holdings exceed 10% yield.")
    return {"health_score":overall,"overall_score":overall,"subscores":scores,"strongest_areas":[{"area":k.replace('_',' ').title(),"score":scores[k]} for k in strongest],"weakest_areas":[{"area":k.replace('_',' ').title(),"score":scores[k]} for k in weakest],"alerts":alerts,"notes":["Income values are annualized estimates.","ESPP concentration evaluated separately.","Health is a tracker-generated heuristic."],"income_quality":income_quality_records(frame),"largest_holding":conc["largest_holding_by_value"],"largest_holding_weight":largest,"data_quality_explanation":"Data quality accounts for unresolved sectors and missing dividend inputs."}

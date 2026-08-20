from __future__ import annotations

from typing import Any
import pandas as pd

from nicegui_app.services.analysis import concentration, contribution_context


def insights_package(metrics: pd.DataFrame) -> dict[str, Any]:
    resolved=metrics[pd.to_numeric(metrics.get("dividend_yield_pct",0),errors="coerce").fillna(0)>0].copy()
    high=resolved.sort_values("dividend_yield_pct",ascending=False).iloc[0] if not resolved.empty else {}
    low=resolved.sort_values("dividend_yield_pct").iloc[0] if not resolved.empty else {}
    yield_summary={"highest_yield_ticker":high.get("ticker","N/A"),"highest_yield_pct":float(high.get("dividend_yield_pct",0) or 0),"lowest_positive_yield_ticker":low.get("ticker","N/A"),"yield_spread_pct":float(high.get("dividend_yield_pct",0) or 0)-float(low.get("dividend_yield_pct",0) or 0)}
    value=float(metrics["market_value"].sum()) if not metrics.empty else 0
    allocation=metrics.groupby("asset_type",dropna=False).agg(market_value=("market_value","sum")).reset_index(); allocation["allocation_pct"]=allocation["market_value"]/value*100 if value else 0
    contrib=contribution_context(metrics); active=sum(row["total_contribution"]>0 for row in contrib["holdings"]); conc=concentration(metrics)
    summary={"regular_biweekly":contrib["regular_biweekly"],"espp_biweekly":contrib["espp_biweekly"],"combined_biweekly":contrib["regular_biweekly"]+contrib["espp_biweekly"],"regular_annual":contrib["regular_biweekly"]*26,"espp_annual":contrib["espp_biweekly"]*26,"combined_annual":contrib["total_annual_contributions"]}
    observations=[conc["guidance"],f"Largest holding: {conc['largest_holding_by_value']} ({conc['largest_holding_weight_pct']:.1f}%).",f"Highest resolved yield: {yield_summary['highest_yield_ticker']} ({yield_summary['highest_yield_pct']:.2f}%).",f"Active contribution destinations: {active}."]
    return {"yield":yield_summary,"contribution":{"rows":contrib["holdings"],"summary":summary,"active_contributors":active},"allocation":allocation.to_dict(orient="records"),"concentration":conc,"observations":observations}

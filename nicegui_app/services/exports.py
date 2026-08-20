from __future__ import annotations

import json
from datetime import datetime
from typing import Any
import pandas as pd

from nicegui_app.services.analysis import analysis_package
from nicegui_app.services.health import health_package


def build_export_payload(holdings: list[dict[str,Any]], metrics: pd.DataFrame) -> dict[str,Any]:
    analysis=analysis_package(metrics); totals=analysis["totals"]
    return {"snapshot_timestamp":datetime.now().isoformat(timespec="seconds"),"currency":"USD","export_version":"2.0","schema_version":"2.0","totals":{"portfolio_value":totals["total_portfolio_value"],"cost_basis":totals["total_cost_basis"],"unrealized_gain":totals["total_unrealized_gain"],"annual_income":totals["total_estimated_annual_income"],"weighted_yield_pct":totals["weighted_portfolio_yield"]},"holdings":metrics.to_dict(orient="records"),"allocation":{"asset_type":analysis["asset_allocation"],"economic_exposure":totals["economic_exposure_allocation"]},"exposure":{"direct_sector_exposure":analysis["direct_sector"]},"income":{"by_holding":analysis["income_quality"],"by_asset_type":totals["income_allocation_by_asset_type"]},"contributions":analysis["contributions"],"health_score":health_package(metrics),"alerts":{"health_alerts":health_package(metrics).get("alerts",[])}}


def export_package(holdings: list[dict[str,Any]],metrics: pd.DataFrame)->dict[str,str]:
    payload=build_export_payload(holdings,metrics); totals=payload["totals"]
    markdown="\n".join(["# Dividend Portfolio Tracker Export","",f"- Portfolio Value: ${totals['portfolio_value']:,.2f}",f"- Cost Basis: ${totals['cost_basis']:,.2f}",f"- Estimated Annual Income: ${totals['annual_income']:,.2f}","","## Holdings","```csv",metrics.to_csv(index=False),"```","","## Structured Context","```json",json.dumps(payload,indent=2,default=str,allow_nan=False),"```"])
    return {"holdings.csv":metrics.to_csv(index=False),"portfolio_export.md":markdown,"portfolio_export.json":json.dumps(payload,indent=2,default=str,allow_nan=False)}

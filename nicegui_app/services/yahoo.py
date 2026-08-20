from __future__ import annotations

import time
from typing import Any
import pandas as pd
import yfinance as yf

from nicegui_app.services.configuration import YAHOO_CACHE_TTL_SECONDS

_cache: dict[tuple[str,str],tuple[float,Any]]={}


def _cached(key:tuple[str,str],loader):
    now=time.monotonic(); existing=_cache.get(key)
    if existing and now-existing[0]<YAHOO_CACHE_TTL_SECONDS:return existing[1]
    value=loader();_cache[key]=(now,value);return value


def yahoo_intelligence_package(metrics:pd.DataFrame)->dict[str,Any]:
    intelligence=[]; historical=[]; lookthrough=[]; monthly={month:0.0 for month in ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")}
    for row in metrics.to_dict(orient="records"):
        ticker=str(row.get("ticker","")).upper(); proxy=yf.Ticker(ticker)
        info=_cached((ticker,"info"),lambda p=proxy: p.info or {})
        history=_cached((ticker,"history"),lambda p=proxy: p.history(period="2y"))
        intelligence.append({"ticker":ticker,"asset_type":row.get("asset_type"),"sector":info.get("sector") or row.get("sector"),"market_cap":info.get("marketCap"),"trailing_pe":info.get("trailingPE"),"analyst_target":info.get("targetMeanPrice")})
        if isinstance(history,pd.DataFrame) and not history.empty:
            start=float(history["Close"].iloc[0]);end=float(history["Close"].iloc[-1]);historical.append({"ticker":ticker,"start_price":start,"end_price":end,"return_pct":((end/start)-1)*100 if start else 0})
            if "Dividends" in history:
                dividends=history[history["Dividends"]>0]["Dividends"]
                counts=dividends.groupby(dividends.index.month).sum(); total=float(counts.sum())
                if total>0:
                    for month,value in counts.items():monthly[list(monthly)[int(month)-1]]+=float(row.get("annual_income",0) or 0)*float(value)/total
        funds=info.get("sectorWeightings") or []
        if isinstance(funds,list):
            for entry in funds:
                if isinstance(entry,dict):
                    for sector,weight in entry.items():lookthrough.append({"ticker":ticker,"sector":sector,"weight":float(weight or 0)})
    return {"investment_intelligence":intelligence,"lookthrough":lookthrough,"unresolved_lookthrough":[],"seasonality":{"monthly_income":monthly,"chart_available":any(monthly.values()),"message":"Estimated distribution of current annual portfolio income based on available historical dividend timing."},"historical_context":historical}

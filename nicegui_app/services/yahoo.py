from __future__ import annotations

import time
from typing import Any
import pandas as pd
import yfinance as yf

from nicegui_app.services.configuration import PRICE_CACHE_TTL_SECONDS,YAHOO_CACHE_TTL_SECONDS,YAHOO_METADATA_CACHE_TTL_SECONDS
from portfolio_analysis import resolve_fund_lookthrough

_cache: dict[tuple[str,str],tuple[float,Any]]={}


def _cached(key:tuple[str,str],loader,ttl:float=YAHOO_CACHE_TTL_SECONDS):
    now=time.monotonic(); existing=_cache.get(key)
    if existing and now-existing[0]<ttl:return existing[1]
    value=loader();_cache[key]=(now,value);return value


def current_market_data(tickers:list[str]|tuple[str,...])->tuple[dict[str,float],dict[str,float]]:
    """Match Streamlit's batched current-price and Yahoo yield interpretation."""
    normalized=tuple(sorted({str(t or "").strip().upper() for t in tickers if str(t or "").strip()}))
    if not normalized:return {},{}
    def prices_loader():
        output={ticker:0.0 for ticker in normalized}
        try:
            history=yf.download(list(normalized),period="5d",interval="1d",auto_adjust=False,progress=False,group_by="column",threads=True)
            if isinstance(history,pd.DataFrame) and not history.empty:
                if isinstance(history.columns,pd.MultiIndex):
                    for ticker in normalized:
                        candidates=[column for column in history.columns if "CLOSE" in [str(part).upper() for part in column] and ticker in [str(part).upper() for part in column]]
                        if candidates:
                            values=pd.to_numeric(history[candidates[0]],errors="coerce").dropna()
                            if not values.empty:output[ticker]=float(values.iloc[-1])
                elif len(normalized)==1 and "Close" in history:
                    values=pd.to_numeric(history["Close"],errors="coerce").dropna()
                    if not values.empty:output[normalized[0]]=float(values.iloc[-1])
        except Exception:
            pass
        return output
    prices=_cached(("|".join(normalized),"prices"),prices_loader,PRICE_CACHE_TTL_SECONDS)
    yields={}
    for ticker in normalized:
        try:
            info=_cached((ticker,"info"),lambda t=ticker: yf.Ticker(t).info or {},YAHOO_METADATA_CACHE_TTL_SECONDS)
            raw=info.get("trailingAnnualDividendYield")
            fallback=info.get("dividendYield")
            if raw is None or (raw in {0,0.0} and fallback not in {None,0,0.0}):raw=fallback
            value=max(float(raw or 0.0),0.0)
            if value<=1:value*=100.0
            yields[ticker]=0.0 if value>50 else value
        except Exception:
            yields[ticker]=0.0
    return dict(prices),yields


def yahoo_intelligence_package(metrics:pd.DataFrame)->dict[str,Any]:
    intelligence=[]; historical=[]; fund_weights={}; monthly={month:0.0 for month in ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")}
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
        def load_fund_weights(p=proxy):
            funds_data=getattr(p,"funds_data",None);raw=getattr(funds_data,"sector_weightings",None) if funds_data is not None else None
            return dict(raw) if hasattr(raw,"items") else {}
        weights=_cached((ticker,"fund_sector_weights"),load_fund_weights,3600.0)
        if weights:fund_weights[ticker]=weights
    resolved=resolve_fund_lookthrough(metrics,fund_weights)
    return {"investment_intelligence":intelligence,"lookthrough":resolved["records"],"unresolved_lookthrough":resolved["unresolved_records"],"fund_lookthrough":resolved,"seasonality":{"monthly_income":monthly,"chart_available":any(monthly.values()),"message":"Estimated distribution of current annual portfolio income based on available historical dividend timing."},"historical_context":historical}

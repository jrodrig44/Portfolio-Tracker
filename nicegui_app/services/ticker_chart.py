from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict
from typing import Any, Callable

import pandas as pd

from nicegui_app.services.configuration import TICKER_CHART_CACHE_TTL_SECONDS
from nicegui_app.services.yahoo import ticker_price_history


LOGGER = logging.getLogger("portfolio_tracker")
TICKER_CHART_RANGES = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "5Y": "5y"}
DEFAULT_TICKER_CHART_RANGE = "3M"
TICKER_CHART_INTERVAL = "1d"
TICKER_CHART_CACHE_MAX_ENTRIES = 128
_chart_cache: OrderedDict[tuple[str, str, str], tuple[float, dict[str, Any]]] = OrderedDict()


def clear_ticker_chart_cache() -> None:
    _chart_cache.clear()


def normalize_ticker_history(frame: Any) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "Close" not in frame:
        return []
    records: list[dict[str, Any]] = []
    close = pd.to_numeric(frame["Close"], errors="coerce")
    for index, value in close.items():
        try:
            price = float(value)
            timestamp = pd.Timestamp(index)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or pd.isna(timestamp):
            continue
        records.append({"date": timestamp.date().isoformat(), "price": price})
    records.sort(key=lambda record: record["date"])
    return records


def ticker_chart_summary(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {"first_price": 0.0, "latest_price": 0.0, "change": 0.0, "change_pct": 0.0}
    first = float(records[0]["price"])
    latest = float(records[-1]["price"])
    change = latest - first
    return {
        "first_price": first,
        "latest_price": latest,
        "change": change,
        "change_pct": (change / first * 100.0) if first else 0.0,
    }


def ticker_chart_options(ticker: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "animation": False,
        "grid": {"left": 64, "right": 20, "top": 24, "bottom": 52, "containLabel": True},
        "tooltip": {"trigger": "axis", ":valueFormatter": "value => '$' + Number(value).toFixed(2)"},
        "xAxis": {"type": "category", "name": "Date", "data": [record["date"] for record in records], "axisLabel": {"hideOverlap": True}},
        "yAxis": {"type": "value", "name": "Price", "scale": True, "axisLabel": {"formatter": "${value}"}},
        "series": [{"name": ticker, "type": "line", "showSymbol": False, "data": [record["price"] for record in records], "lineStyle": {"width": 2}}],
    }


def load_ticker_chart(
    ticker: str,
    range_name: str = DEFAULT_TICKER_CHART_RANGE,
    *,
    loader: Callable[[str, str, str], Any] = ticker_price_history,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_range = range_name if range_name in TICKER_CHART_RANGES else DEFAULT_TICKER_CHART_RANGE
    cache_key = (normalized_ticker, normalized_range, TICKER_CHART_INTERVAL)
    current_time = now()
    cached = _chart_cache.get(cache_key)
    if cached and current_time - cached[0] < TICKER_CHART_CACHE_TTL_SECONDS:
        _chart_cache.move_to_end(cache_key)
        return cached[1]

    records: list[dict[str, Any]] = []
    try:
        records = normalize_ticker_history(
            loader(normalized_ticker, TICKER_CHART_RANGES[normalized_range], TICKER_CHART_INTERVAL)
        )
    except Exception as exc:
        LOGGER.info("Price history unavailable for %s (%s): %s", normalized_ticker, normalized_range, exc)
    result = {
        "ticker": normalized_ticker,
        "range": normalized_range,
        "records": records,
        "summary": ticker_chart_summary(records),
        "options": ticker_chart_options(normalized_ticker, records) if records else None,
        "error": None if records else "Price history is not available for this ticker.",
    }
    _chart_cache[cache_key] = (current_time, result)
    _chart_cache.move_to_end(cache_key)
    while len(_chart_cache) > TICKER_CHART_CACHE_MAX_ENTRIES:
        _chart_cache.popitem(last=False)
    return result

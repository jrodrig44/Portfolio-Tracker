from __future__ import annotations

import math
from typing import Any

import pandas as pd


HISTORY_RANGE_OPTIONS = ("1M", "3M", "1Y", "ALL")
DEFAULT_HISTORY_RANGE = "ALL"


def normalize_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return finite, dated history rows in production chart order.

    Production combines statement and live rows with UNION ALL, so same-day rows
    intentionally remain distinct. Missing dates and non-finite values are not
    chartable and are omitted rather than converted into fabricated zero values.
    """
    dated: list[tuple[pd.Timestamp, int, dict[str, Any]]] = []
    for position, record in enumerate(history):
        history_date = pd.to_datetime(record.get("history_date"), errors="coerce")
        try:
            portfolio_value = float(record.get("portfolio_value"))
        except (TypeError, ValueError):
            continue
        if pd.isna(history_date) or not math.isfinite(portfolio_value):
            continue
        if history_date.tzinfo is not None:
            history_date = history_date.tz_convert("UTC").tz_localize(None)
        normalized = dict(record)
        normalized["history_date"] = history_date.normalize().date().isoformat()
        normalized["portfolio_value"] = portfolio_value
        dated.append((history_date.normalize(), position, normalized))
    dated.sort(key=lambda item: (item[0], item[1]))
    return [record for _, _, record in dated]


def filter_history_range(
    history: list[dict[str, Any]], selected_range: str = DEFAULT_HISTORY_RANGE
) -> list[dict[str, Any]]:
    """Apply calendar ranges anchored to the newest available history point.

    ALL retains the complete imported-statement baseline plus live points.
    1M/3M/1Y are deliberately anchored to the data, not wall-clock today, so a
    temporarily stale portfolio still presents a stable and meaningful range.
    """
    if selected_range not in HISTORY_RANGE_OPTIONS:
        raise ValueError(f"Unsupported history range: {selected_range}")
    normalized = normalize_history(history)
    if selected_range == "ALL" or not normalized:
        return normalized
    latest = pd.Timestamp(normalized[-1]["history_date"])
    cutoff = latest - {
        "1M": pd.DateOffset(months=1),
        "3M": pd.DateOffset(months=3),
        "1Y": pd.DateOffset(years=1),
    }[selected_range]
    return [row for row in normalized if pd.Timestamp(row["history_date"]) >= cutoff]


def calculate_history_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_history(history)
    if not normalized:
        return {
            "point_count": 0,
            "starting_date": None,
            "ending_date": None,
            "starting_portfolio_value": 0.0,
            "ending_portfolio_value": 0.0,
            "absolute_change": 0.0,
            "percentage_change": 0.0,
        }
    starting = float(normalized[0]["portfolio_value"])
    ending = float(normalized[-1]["portfolio_value"])
    change = ending - starting
    return {
        "point_count": len(normalized),
        "starting_date": normalized[0]["history_date"],
        "ending_date": normalized[-1]["history_date"],
        "starting_portfolio_value": starting,
        "ending_portfolio_value": ending,
        "absolute_change": change,
        "percentage_change": change / starting * 100.0 if starting else 0.0,
    }


def build_history_series(history: list[dict[str, Any]]) -> dict[str, list[Any]]:
    normalized = normalize_history(history)
    return {
        "dates": [row["history_date"] for row in normalized],
        "portfolio_values": [row["portfolio_value"] for row in normalized],
    }


def build_history_package(
    history: list[dict[str, Any]], selected_range: str = DEFAULT_HISTORY_RANGE
) -> dict[str, Any]:
    filtered = filter_history_range(history, selected_range)
    return {
        "selected_range": selected_range,
        "ranges": HISTORY_RANGE_OPTIONS,
        "records": filtered,
        "series": build_history_series(filtered),
        "summary": calculate_history_summary(filtered),
    }

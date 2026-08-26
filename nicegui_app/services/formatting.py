from __future__ import annotations

import math
import re
from typing import Any, Iterable


EMPTY_VALUE = "—"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def format_currency(value: Any) -> str:
    number = _number(value)
    return EMPTY_VALUE if number is None else f"${number:,.2f}"


def format_percentage(value: Any) -> str:
    number = _number(value)
    return EMPTY_VALUE if number is None else f"{number:,.2f}%"


def format_score(value: Any, *, out_of_100: bool = False) -> str:
    number = _number(value)
    if number is None:
        return EMPTY_VALUE
    formatted = f"{number:,.1f}"
    return f"{formatted}/100" if out_of_100 else formatted


def format_shares(value: Any) -> str:
    number = _number(value)
    if number is None:
        return EMPTY_VALUE
    return f"{number:,.6f}".rstrip("0").rstrip(".")


def format_count(value: Any) -> str:
    number = _number(value)
    return EMPTY_VALUE if number is None else f"{int(round(number)):,}"


def format_number(value: Any, decimals: int = 2) -> str:
    number = _number(value)
    return EMPTY_VALUE if number is None else f"{number:,.{decimals}f}"


def format_status(value: Any, *, enabled: str = "Yes", disabled: str = "No") -> str:
    if value is None:
        return EMPTY_VALUE
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "enabled", "active"}:
            return enabled
        if lowered in {"false", "no", "0", "disabled", "inactive"}:
            return disabled
        return value.strip() or EMPTY_VALUE
    return enabled if bool(value) else disabled


def human_label(field: Any) -> str:
    text = str(field or "").strip().strip("_")
    if not text:
        return ""
    aliases = {
        "ticker": "Ticker", "asset_type": "Asset Type", "market_value": "Market Value",
        "annual_income": "Annual Income", "annualized_income": "Annual Income",
        "portfolio_weight_pct": "Portfolio Weight", "allocation_pct": "Allocation",
        "income_allocation_pct": "Income Share", "current_price": "Current Price",
        "avg_cost": "Average Cost", "average_cost": "Average Cost", "cost_basis": "Cost Basis",
        "unrealized_gain": "Unrealized Gain/Loss", "unrealized_gain_loss": "Unrealized Gain/Loss",
        "dividend_yield_pct": "Dividend Yield", "portfolio_yield_pct": "Portfolio Yield",
        "regular_biweekly_contribution": "Regular Biweekly", "espp_biweekly_contribution": "ESPP Biweekly",
        "special_contribution": "Special Contribution", "employer_stock": "Employer Stock",
        "espp_enabled": "ESPP", "income_quality_classification": "Income Type",
        "fund_lookthrough_completeness_pct": "Look-Through Coverage",
    }
    return aliases.get(text, re.sub(r"\s+", " ", text.replace("_", " ")).title())


_CURRENCY_PARTS = ("price", "value", "cost", "income", "gain", "contribution", "amount", "distribution")
_PERCENT_PARTS = ("pct", "percent", "weight", "yield", "coverage", "concentration", "discount", "allocation", "drawdown", "volatility", "return")


def format_field_value(field: Any, value: Any) -> str:
    key = str(field or "").strip().lower()
    if value is None or value == "":
        return EMPTY_VALUE
    if isinstance(value, bool):
        if key == "employer_stock":
            return format_status(value, enabled="Employer stock", disabled="No")
        if key in {"espp", "espp_enabled", "is_espp"}:
            return format_status(value, enabled="Active", disabled="Not active")
        return format_status(value)
    if key == "shares" or key.endswith("_shares"):
        return format_shares(value)
    if key == "score" or key.endswith("_score"):
        return format_score(value)
    if key.endswith("_count") or key in {"count", "points", "holdings"} and _number(value) is not None:
        return format_count(value)
    if any(part in key for part in _PERCENT_PARTS):
        return format_percentage(value)
    if any(part in key for part in _CURRENCY_PARTS):
        return format_currency(value)
    if _number(value) is not None:
        return format_number(value)
    if isinstance(value, dict):
        return "; ".join(f"{human_label(k)}: {format_field_value(k, v)}" for k, v in value.items()) or EMPTY_VALUE
    if isinstance(value, (list, tuple, set)):
        rendered = [format_field_value("detail", item) for item in value]
        return "; ".join(item for item in rendered if item != EMPTY_VALUE) or EMPTY_VALUE
    return str(value).strip() or EMPTY_VALUE


def format_table_records(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    source = [row for row in records if isinstance(row, dict)]
    keys: list[str] = []
    for row in source:
        for raw_key in row:
            key = str(raw_key or "").strip()
            if key and not key.lower().startswith("unnamed") and key not in keys:
                keys.append(key)
    keys = [key for key in keys if any(row.get(key) not in (None, "") for row in source)]
    columns = [{"name": key, "label": human_label(key), "field": key, "sortable": True, "align": "left"} for key in keys]
    rows = [{key: format_field_value(key, row.get(key)) for key in keys} for row in source]
    return columns, rows


def dividend_seasonality_chart_options(monthly_income: dict[str, Any]) -> dict[str, Any]:
    """Build presentation-only chart options for estimated monthly income dollars."""
    months = list(monthly_income)
    values = [float(monthly_income[month] or 0.0) for month in months]
    currency_formatter = "value => '$' + Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})"
    return {
        "title": {"text": "Dividend Seasonality", "left": "center"},
        "tooltip": {"trigger": "axis", ":valueFormatter": currency_formatter},
        "grid": {"left": 64, "right": 20, "top": 56, "bottom": 64, "containLabel": True},
        "xAxis": {"type": "category", "name": "Month", "nameLocation": "middle", "nameGap": 36, "data": months},
        "yAxis": {"type": "value", "name": "Estimated Dividend Income", "axisLabel": {"formatter": "${value}"}},
        "series": [{"name": "Estimated Dividend Income", "type": "bar", "data": values}],
    }


RESPONSIVE_CARD_GRID = "w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3"
SYSTEM_CARD_GRID = "w-full max-w-full grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3"
SYSTEM_CARD = "w-full min-w-0 max-w-full h-auto py-3"
SYSTEM_LABEL = "text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 whitespace-normal break-words"
SYSTEM_VALUE = "w-full min-w-0 max-w-full text-base font-medium whitespace-normal break-words"
SYSTEM_PATH_VALUE = f"{SYSTEM_VALUE} break-all"
RESPONSIVE_ACTIONS = "w-full flex flex-col sm:flex-row gap-2 sm:items-center"
SCROLL_TABLE = "w-full max-w-full overflow-x-auto"
FULL_WIDTH_CONTROL = "w-full sm:max-w-xl"
DIALOG_CARD = "w-[min(96vw,48rem)] max-h-[90dvh] overflow-y-auto p-4 sm:p-6 dark:bg-gray-900 dark:text-gray-100"
CONFIRM_DIALOG_CARD = "w-[min(94vw,30rem)] max-h-[90dvh] overflow-y-auto p-4 sm:p-6 dark:bg-gray-900 dark:text-gray-100"
SNAPSHOT_VIEWER_CARD = "w-[min(96vw,72rem)] max-h-[90dvh] overflow-hidden flex flex-col p-4 sm:p-6 dark:bg-gray-900 dark:text-gray-100"
SAFE_RICH_OUTPUT = "w-full max-w-full min-w-0 break-words overflow-hidden safe-rich-output"
EMPTY_STATE = "w-full rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-600 break-words dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"

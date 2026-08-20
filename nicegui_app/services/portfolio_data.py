from __future__ import annotations

import calendar
import math
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_DB_PATH = PROJECT_ROOT / "data" / "portfolio.db"
HISTORY_DB_PATH = PROJECT_ROOT / "data" / "stash_statement_history.sqlite"
HISTORY_RANGE_OPTIONS = ("1M", "3M", "1Y", "ALL")
DEFAULT_HISTORY_RANGE = "ALL"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0.0)
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _boolean(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def load_holdings(db_path: Path = PORTFOLIO_DB_PATH) -> list[dict[str, Any]]:
    """Read holdings without creating, migrating, or updating the portfolio DB."""
    with closing(_read_only_connection(db_path)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holdings'"
        ).fetchone()
        if table is None:
            raise RuntimeError("Portfolio database has no holdings table.")
        records = [dict(row) for row in connection.execute("SELECT * FROM holdings")]

    holdings: list[dict[str, Any]] = []
    for raw in records:
        row = {str(key).lower(): value for key, value in raw.items()}
        shares = _number(row.get("shares"))
        amount_invested = _number(row.get("amount_invested"))
        average_cost = _number(row.get("avg_cost")) or (amount_invested / shares if shares else 0.0)
        current_price = _number(row.get("current_price"))
        market_value = _number(row.get("market_value")) or shares * current_price
        cost_basis = amount_invested or shares * average_cost
        annual_income = _number(row.get("annualized_income"))
        if not annual_income:
            annual_per_share = (
                _number(row.get("effective_annual_div_per_share"))
                or _number(row.get("estimated_annual_distribution_per_share"))
                or _number(row.get("annual_div_per_share"))
            )
            annual_income = shares * annual_per_share
        dividend_yield = _number(row.get("dividend_yield_pct"))
        if not dividend_yield and market_value > 0:
            dividend_yield = annual_income / market_value * 100
        holdings.append(
            {
                "ticker": str(row.get("ticker") or "").strip().upper(),
                "shares": shares,
                "current_price": current_price,
                "market_value": market_value,
                "average_cost": average_cost,
                "cost_basis": cost_basis,
                "unrealized_gain": _number(row.get("unrealized_gain")) or market_value - cost_basis,
                "annual_income": annual_income,
                "dividend_yield_pct": dividend_yield,
                "asset_type": str(row.get("asset_type") or "Unclassified"),
                "sector": str(row.get("sector") or "Unclassified"),
                "amount_invested": amount_invested,
                "annual_div_per_share": _number(row.get("annual_div_per_share")),
                "effective_annual_div_per_share": _number(row.get("effective_annual_div_per_share")),
                "annualized_income": annual_income,
                "regular_biweekly_contribution": _number(row.get("regular_biweekly_contribution")),
                "espp_biweekly_contribution": _number(row.get("espp_biweekly_contribution") or row.get("espp_contribution_biweekly")),
                "special_contribution": _number(row.get("special_contribution")),
                "employer_stock": _boolean(row.get("employer_stock")),
                "espp_enabled": _boolean(row.get("espp_enabled")) or _boolean(row.get("is_espp")),
                "espp_acquired_shares": _number(row.get("espp_acquired_shares")),
                "espp_discount_pct": _number(row.get("espp_discount_pct") or row.get("espp_discount")),
                "espp_purchase_frequency": str(row.get("espp_purchase_frequency") or ""),
            }
        )
    return sorted((row for row in holdings if row["ticker"]), key=lambda row: row["ticker"])


def portfolio_summary(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    total_value = sum(_number(row.get("market_value")) for row in holdings)
    cost_basis = sum(_number(row.get("cost_basis")) for row in holdings)
    annual_income = sum(_number(row.get("annual_income")) for row in holdings)
    largest = max(holdings, key=lambda row: _number(row.get("market_value")), default={})
    largest_value = _number(largest.get("market_value"))
    return {
        "portfolio_value": total_value,
        "cost_basis": cost_basis,
        "unrealized_gain": total_value - cost_basis,
        "annual_income": annual_income,
        "weighted_yield_pct": annual_income / total_value * 100 if total_value else 0.0,
        "holding_count": len(holdings),
        "largest_holding": largest.get("ticker", "—"),
        "largest_weight_pct": largest_value / total_value * 100 if total_value else 0.0,
    }


def load_history(db_path: Path = HISTORY_DB_PATH) -> list[dict[str, Any]]:
    """Read the two production history sources without running schema preparation."""
    with closing(_read_only_connection(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        required = {"statement_portfolio_history", "live_portfolio_snapshots"}
        if not required.issubset(tables):
            raise RuntimeError("History database does not have the production history schema.")
        rows = connection.execute(
            "SELECT history_date, portfolio_value, source_type FROM statement_portfolio_history "
            "UNION ALL SELECT snapshot_date, portfolio_value, source_type FROM live_portfolio_snapshots "
            "ORDER BY 1"
        ).fetchall()
    return [
        {"history_date": row[0], "portfolio_value": _number(row[1]), "source_type": row[2]}
        for row in rows
    ]


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def filter_history(
    history: list[dict[str, Any]], selected_range: str = DEFAULT_HISTORY_RANGE
) -> list[dict[str, Any]]:
    """Match Streamlit's newest-record-anchored DateOffset range behavior."""
    if selected_range not in HISTORY_RANGE_OPTIONS:
        raise ValueError(f"Unsupported history range: {selected_range}")
    dated: list[tuple[date, dict[str, Any]]] = []
    for record in history:
        try:
            parsed = date.fromisoformat(str(record.get("history_date", ""))[:10])
        except ValueError:
            continue
        dated.append((parsed, dict(record)))
    dated.sort(key=lambda item: item[0])
    if selected_range == "ALL" or not dated:
        return [record for _, record in dated]
    latest = dated[-1][0]
    cutoff = _subtract_months(latest, {"1M": 1, "3M": 3, "1Y": 12}[selected_range])
    return [record for history_date, record in dated if history_date >= cutoff]

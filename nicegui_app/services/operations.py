"""NiceGUI read-only discovery for user-facing operational status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui_app.services.configuration import (
    DIVIDEND_TIMING_CACHE_TTL_SECONDS,
    FUND_SECTOR_WEIGHTS_CACHE_TTL_SECONDS,
    HISTORICAL_CONTEXT_CACHE_TTL_SECONDS,
    INVESTMENT_INTELLIGENCE_CACHE_TTL_SECONDS,
    PRICE_CACHE_TTL_SECONDS,
    YAHOO_METADATA_CACHE_TTL_SECONDS,
)


CACHE_INTERVALS_SECONDS = {
    "Price": PRICE_CACHE_TTL_SECONDS,
    "Yahoo metadata": YAHOO_METADATA_CACHE_TTL_SECONDS,
    "Investment Intelligence": INVESTMENT_INTELLIGENCE_CACHE_TTL_SECONDS,
    "Dividend timing": DIVIDEND_TIMING_CACHE_TTL_SECONDS,
    "Historical context": HISTORICAL_CONTEXT_CACHE_TTL_SECONDS,
    "Fund sector weights": FUND_SECTOR_WEIGHTS_CACHE_TTL_SECONDS,
}


def format_interval(seconds: int | float) -> str:
    value = int(seconds)
    for unit_seconds, singular in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if value >= unit_seconds and value % unit_seconds == 0:
            count = value // unit_seconds
            return f"{count} {singular}{'' if count == 1 else 's'}"
    return f"{value} second{'' if value == 1 else 's'}"


def _file_status(path: Path) -> dict[str, Any]:
    """Describe a file without opening, creating, or modifying it."""
    try:
        available = path.is_file()
    except OSError:
        available = False
    return {"path": str(path), "available": available, "status": "Available" if available else "Not available"}


def _statement_folder_status(path: Path) -> dict[str, Any]:
    """Count PDF directory entries without reading or parsing statement contents."""
    try:
        available = path.is_dir()
        pdf_count = sum(1 for entry in path.iterdir() if entry.is_file() and entry.suffix.lower() == ".pdf") if available else 0
    except OSError:
        available = False
        pdf_count = 0
    return {
        "path": str(path),
        "available": available,
        "status": "Ready" if available else "Not available",
        "pdf_count": pdf_count,
    }


def operational_status(
    portfolio_db_path: Path,
    statements_path: Path,
    statement_history_db_path: Path,
    *,
    application_port: int,
    database_refresh_interval_seconds: float,
) -> dict[str, Any]:
    """Build a deliberately small, secret-free status payload."""
    return {
        "application": {
            "framework": "NiceGUI",
            "port": int(application_port),
            "database_refresh_interval_seconds": int(database_refresh_interval_seconds),
        },
        "portfolio_database": _file_status(Path(portfolio_db_path)),
        "statement_storage": {
            "folder": _statement_folder_status(Path(statements_path)),
            "history_database": _file_status(Path(statement_history_db_path)),
        },
        "cache_intervals_seconds": {name: int(seconds) for name, seconds in CACHE_INTERVALS_SECONDS.items()},
    }


__all__ = ["CACHE_INTERVALS_SECONDS", "format_interval", "operational_status"]

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


HISTORY_DB_SCHEMA_VERSION = 1


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _ensure_history_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS statement_history_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            statement_date TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            source_path TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS statement_history_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            market_value REAL NOT NULL,
            FOREIGN KEY(import_id) REFERENCES statement_history_imports(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_statement_history_imports_date ON statement_history_imports(statement_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_statement_history_holdings_ticker ON statement_history_holdings(ticker)")


def import_stash_statement_history(source_dir: str | Path, db_path: str | Path | None = None) -> dict[str, Any]:
    """Disable statement imports until a real local Stash parser is implemented."""
    return {"import_count": 0, "holding_count": 0, "status": "disabled"}


def build_statement_history_reconciliation(current_holdings: pd.DataFrame, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    db = Path(db_path) if db_path is not None else Path("data") / "stash_statement_history.sqlite"
    if not db.exists():
        return []

    with closing(sqlite3.connect(str(db))) as conn:
        rows = conn.execute(
            """
            SELECT ticker, SUM(shares) AS shares, SUM(market_value) AS market_value
            FROM statement_history_holdings
            GROUP BY ticker
            """
        ).fetchall()

    current = current_holdings.copy() if isinstance(current_holdings, pd.DataFrame) else pd.DataFrame(current_holdings or [])
    if current.empty:
        current = pd.DataFrame(columns=["ticker", "shares", "market_value"])
    else:
        current = current.copy()
        if "ticker" in current.columns:
            current["ticker"] = current["ticker"].fillna("").astype(str).str.strip().str.upper()
        else:
            current["ticker"] = ""
        if "shares" in current.columns:
            current["shares"] = pd.to_numeric(current["shares"], errors="coerce").fillna(0.0)
        else:
            current["shares"] = 0.0
        if "market_value" in current.columns:
            current["market_value"] = pd.to_numeric(current["market_value"], errors="coerce").fillna(0.0)
        else:
            current["market_value"] = 0.0

    current_lookup = {
        str(row.get("ticker", "")).strip().upper(): row for _, row in current.iterrows() if str(row.get("ticker", "")).strip()
    }
    statement_lookup = {
        str(ticker).strip().upper(): {"statement_shares": float(shares or 0.0), "statement_market_value": float(market_value or 0.0)}
        for ticker, shares, market_value in rows
    }
    all_tickers = sorted(set(current_lookup) | set(statement_lookup))
    reconciliation: list[dict[str, Any]] = []
    for ticker_key in all_tickers:
        current_row = current_lookup.get(ticker_key, {})
        statement_row = statement_lookup.get(ticker_key, {})
        if hasattr(current_row, "get"):
            current_shares = float(current_row.get("shares", 0.0) or 0.0)
            current_market_value = float(current_row.get("market_value", 0.0) or 0.0)
        else:
            current_shares = 0.0
            current_market_value = 0.0
        excluded = ticker_key in {"HD", "JEPI"}
        reconciliation.append(
            {
                "ticker": ticker_key,
                "statement_shares": float(statement_row.get("statement_shares", 0.0) or 0.0),
                "statement_market_value": float(statement_row.get("statement_market_value", 0.0) or 0.0),
                "current_shares": current_shares,
                "current_market_value": current_market_value,
                "excluded": excluded,
                "reconciliation_note": "Excluded from reconciliation" if excluded else "Pending review",
            }
        )

    return reconciliation

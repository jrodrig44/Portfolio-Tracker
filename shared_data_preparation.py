from __future__ import annotations

import math
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.portfolio_data import load_history, load_holdings
from stash_history import upsert_live_portfolio_snapshot


PORTFOLIO_REQUIRED_TABLES = {"holdings"}
HISTORY_REQUIRED_TABLES = {"statement_portfolio_history", "live_portfolio_snapshots"}
_PREPARATION_LOCK = threading.Lock()


class SharedDataPreparationError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"{stage} failed: {message}")


class SharedDataPreparationInProgress(SharedDataPreparationError):
    def __init__(self) -> None:
        super().__init__("Preparation", "another shared-data preparation is already running")


@dataclass(frozen=True)
class SharedDataPreparationResult:
    ready: bool
    holdings_count: int
    holdings_status: str
    portfolio_database_status: str
    history_database_status: str
    market_refresh_status: str
    live_snapshot_status: str
    sqlite_checkpoint_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _database_tables(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _verify_database(path: Path, required_tables: set[str], stage: str) -> None:
    if not path.is_file():
        raise SharedDataPreparationError(stage, f"database not found: {path}")
    try:
        with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            missing = required_tables - _database_tables(connection)
            if missing:
                raise SharedDataPreparationError(stage, f"required tables are missing: {', '.join(sorted(missing))}")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or str(quick_check[0]).lower() != "ok":
                raise SharedDataPreparationError(stage, "SQLite quick_check did not return ok")
    except SharedDataPreparationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SharedDataPreparationError(stage, str(exc)) from exc


def _checkpoint_database(path: Path, stage: str) -> None:
    try:
        with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=rw", uri=True, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            result = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            if result is None or len(result) != 3:
                raise SharedDataPreparationError(stage, "SQLite did not return a checkpoint result")
    except SharedDataPreparationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SharedDataPreparationError(stage, str(exc)) from exc


def persist_live_snapshot_if_ready(
    metrics: pd.DataFrame,
    history_db_path: Path,
    *,
    total_value: float | None = None,
    snapshot_writer: Callable[..., None] = upsert_live_portfolio_snapshot,
) -> str:
    if metrics.empty:
        return "Not applicable"
    shares = pd.to_numeric(metrics.get("shares", pd.Series(0.0, index=metrics.index)), errors="coerce").fillna(0.0)
    prices = pd.to_numeric(metrics.get("current_price", pd.Series(0.0, index=metrics.index)), errors="coerce")
    if bool(((shares > 0) & (prices.isna() | ~prices.apply(math.isfinite) | (prices <= 0))).any()):
        return "Not applicable"
    if total_value is None:
        total_value = float(pd.to_numeric(metrics.get("market_value", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    if not math.isfinite(total_value) or total_value <= 0:
        return "Not applicable"
    snapshot_date = date.today().isoformat()
    previous_value: float | None = None
    if history_db_path.is_file():
        try:
            with closing(sqlite3.connect(str(history_db_path), timeout=30)) as connection:
                if "live_portfolio_snapshots" in _database_tables(connection):
                    row = connection.execute(
                        "SELECT portfolio_value FROM live_portfolio_snapshots WHERE snapshot_date = ?",
                        (snapshot_date,),
                    ).fetchone()
                    previous_value = float(row[0]) if row else None
        except (TypeError, ValueError, sqlite3.Error):
            previous_value = None
    snapshot_writer(total_value, snapshot_date=snapshot_date, db_path=history_db_path)
    return "Already current" if previous_value is not None and math.isclose(previous_value, total_value, rel_tol=0.0, abs_tol=0.005) else "Updated"


def prepare_shared_data(
    portfolio_db_path: Path | str,
    history_db_path: Path | str,
    *,
    market_loader: Callable[[list[str]], tuple[dict[str, float], dict[str, float]]],
    market_cache_clearer: Callable[[], None] | None = None,
    holdings_loader: Callable[[Path], list[dict[str, Any]]] = load_holdings,
    history_loader: Callable[[Path], list[dict[str, Any]]] = load_history,
    metrics_builder: Callable[..., pd.DataFrame] = metrics_frame,
    snapshot_writer: Callable[..., None] = upsert_live_portfolio_snapshot,
) -> SharedDataPreparationResult:
    if not _PREPARATION_LOCK.acquire(blocking=False):
        raise SharedDataPreparationInProgress()
    portfolio_path = Path(portfolio_db_path)
    history_path = Path(history_db_path)
    try:
        try:
            holdings = holdings_loader(portfolio_path)
        except Exception as exc:
            raise SharedDataPreparationError("Holdings read", str(exc)) from exc

        tickers = [str(row.get("ticker", "")).strip().upper() for row in holdings if str(row.get("ticker", "")).strip()]
        try:
            if market_cache_clearer is not None:
                market_cache_clearer()
            prices, yields = market_loader(tickers) if tickers else ({}, {})
            metrics = metrics_builder(holdings, prices, yields) if holdings else pd.DataFrame()
        except Exception as exc:
            raise SharedDataPreparationError("Market refresh", str(exc)) from exc

        try:
            snapshot_status = persist_live_snapshot_if_ready(metrics, history_path, snapshot_writer=snapshot_writer)
        except Exception as exc:
            raise SharedDataPreparationError("Live snapshot", str(exc)) from exc

        try:
            history_loader(history_path)
        except Exception as exc:
            raise SharedDataPreparationError("History read", str(exc)) from exc

        _verify_database(portfolio_path, PORTFOLIO_REQUIRED_TABLES, "Portfolio database verification")
        _verify_database(history_path, HISTORY_REQUIRED_TABLES, "History database verification")
        _checkpoint_database(portfolio_path, "Portfolio database checkpoint")
        _checkpoint_database(history_path, "History database checkpoint")

        return SharedDataPreparationResult(
            ready=True,
            holdings_count=len(holdings),
            holdings_status="Ready",
            portfolio_database_status="OK",
            history_database_status="OK",
            market_refresh_status="Complete",
            live_snapshot_status=snapshot_status,
            sqlite_checkpoint_status="Complete",
        )
    finally:
        _PREPARATION_LOCK.release()

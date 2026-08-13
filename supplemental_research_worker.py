from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sqlite3
import time

from dotenv import load_dotenv

from supplemental_research import (
    WORKER_CHECK_INTERVAL,
    SupplementalResearchService,
    acquire_worker_lease,
    complete_worker_job,
    initialize_database,
    validated_research_model,
    worker_due_tickers,
)

LOGGER = logging.getLogger("supplemental_research_worker")


def _load_holdings_contexts(db_path: Path) -> dict[str, dict[str, object]]:
    if not db_path.exists():
        return {}
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT * FROM holdings WHERE COALESCE(ticker, '') != ''").fetchall()
        except sqlite3.Error:
            return {}
    finally:
        connection.close()
    contexts: dict[str, dict[str, object]] = {}
    for row in rows:
        ticker = str(row["ticker"] or "").strip().upper()
        if not ticker:
            continue
        contexts[ticker] = {
            "ticker": ticker,
            "entity_name": ticker,
            "asset_type": row["asset_type"] or "Unknown",
            "sector": row["sector"] or "Unknown",
            "missing_fields": ["strategy_objective", "risk_characteristics", "recent_structural_developments"],
            "existing_yahoo_information": {
                "sector": row["sector"] or "",
                "asset_type": row["asset_type"] or "",
                "annual_div_per_share": row["annual_div_per_share"] or 0,
                "dividend_yield_pct": row["dividend_yield_pct"] or 0,
            },
        }
    return contexts


def run_once(project_root: Path) -> int:
    db_path = project_root / "data" / "portfolio.db"
    initialize_database(db_path)
    contexts = _load_holdings_contexts(db_path)
    service = SupplementalResearchService(db_path)
    service.sync_holdings(contexts)
    due_tickers = worker_due_tickers(db_path)
    if not due_tickers:
        LOGGER.info("No eligible supplemental research is due.")
        return 0
    if not acquire_worker_lease(db_path):
        LOGGER.info("Another supplemental research job already holds the lease.")
        return 0
    model = validated_research_model(db_path)
    results = service.refresh_due(contexts, model=model)
    completed = [str(result["ticker"]) for result in results if result.get("status") == "current"]
    failed = [str(result["ticker"]) for result in results if result.get("status") == "failed"]
    complete_worker_job(db_path, completed, failed)
    LOGGER.info("Supplemental research job finished: %d completed, %d failed.", len(completed), len(failed))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Portfolio Tracker supplemental research worker.")
    parser.add_argument("--once", action="store_true", help="Check and process due research once, then exit.")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    load_dotenv(project_root / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.once:
        return run_once(project_root)
    while True:
        try:
            run_once(project_root)
        except Exception:
            LOGGER.exception("Supplemental research worker check failed.")
        time.sleep(WORKER_CHECK_INTERVAL.total_seconds())


if __name__ == "__main__":
    raise SystemExit(main())

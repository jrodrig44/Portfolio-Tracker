from __future__ import annotations

import argparse
from datetime import datetime
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
    get_research_rows,
    initialize_database,
    validated_research_model,
    worker_due_tickers,
)
from logging_setup import configure_structured_logging, log_event

WORKER_LOG_PATH = Path(__file__).resolve().parent / "logs" / "supplemental_research_worker.jsonl"
LOGGER = configure_structured_logging("portfolio_tracker.supplemental_worker", WORKER_LOG_PATH, "supplemental_research_worker")


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
    check_started_at = time.perf_counter()
    log_event(LOGGER, "worker_check_started", "Supplemental research worker check started")
    db_path = project_root / "data" / "portfolio.db"
    initialize_database(db_path)
    contexts = _load_holdings_contexts(db_path)
    service = SupplementalResearchService(db_path)
    service.sync_holdings(contexts)
    due_tickers = worker_due_tickers(db_path)
    eligible_count = sum(1 for row in get_research_rows(db_path) if row.get("manual_initial_research_completed") and not row.get("archived"))
    next_check_at = (datetime.now().astimezone() + WORKER_CHECK_INTERVAL).isoformat(timespec="seconds")
    common_check_fields = {
        "active_holdings_count": len(contexts),
        "eligible_holdings_count": eligible_count,
        "due_holdings_count": len(due_tickers),
        "duration_ms": round((time.perf_counter() - check_started_at) * 1000),
        "next_check_at": next_check_at,
    }
    if not due_tickers:
        log_event(LOGGER, "no_research_due", "No eligible supplemental research is due", **common_check_fields)
        log_event(LOGGER, "worker_check_completed", "Supplemental research worker check completed", status="completed", **common_check_fields)
        return 0
    if not acquire_worker_lease(db_path):
        log_event(LOGGER, "worker_lease_unavailable", "Another supplemental research job already holds the lease", **common_check_fields)
        log_event(LOGGER, "worker_check_completed", "Supplemental research worker check completed", status="lease_unavailable", **common_check_fields)
        return 0
    log_event(LOGGER, "worker_lease_acquired", "Supplemental research worker lease acquired", **common_check_fields)
    model = validated_research_model(db_path)
    batch_started_at = time.perf_counter()
    log_event(LOGGER, "research_batch_started", "Supplemental research batch started", model=model, due_holdings_count=len(due_tickers))
    for ticker in due_tickers:
        log_event(LOGGER, "holding_research_started", "Supplemental research started", ticker=ticker, model=model)
    results = service.refresh_due(contexts, model=model)
    for result in results:
        ticker = str(result.get("ticker", ""))
        result_status = str(result.get("status", "failed"))
        if result_status == "current":
            validated = result.get("result") if isinstance(result.get("result"), dict) else {}
            log_event(LOGGER, "holding_research_completed", "Supplemental research completed", ticker=ticker, model=model, verified_facts_count=len(validated.get("verified_facts", [])), status=result_status)
        else:
            log_event(LOGGER, "holding_research_failed", "Supplemental research failed", ticker=ticker, model=model, status=result_status, sanitized_error=str(result.get("error") or result.get("reason") or "Research failed")[:500])
    completed = [str(result["ticker"]) for result in results if result.get("status") == "current"]
    failed = [str(result["ticker"]) for result in results if result.get("status") == "failed"]
    complete_worker_job(db_path, completed, failed)
    log_event(LOGGER, "research_batch_completed", "Supplemental research batch completed", model=model, completed_count=len(completed), failed_count=len(failed), duration_ms=round((time.perf_counter() - batch_started_at) * 1000), status="completed" if not failed else "partially_completed")
    common_check_fields["duration_ms"] = round((time.perf_counter() - check_started_at) * 1000)
    common_check_fields["completed_count"] = len(completed)
    common_check_fields["failed_count"] = len(failed)
    log_event(LOGGER, "worker_check_completed", "Supplemental research worker check completed", status="completed", **common_check_fields)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Portfolio Tracker supplemental research worker.")
    parser.add_argument("--once", action="store_true", help="Check and process due research once, then exit.")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    load_dotenv(project_root / ".env")
    log_event(LOGGER, "worker_started", "Supplemental research worker started", status="once" if args.once else "running")
    if args.once:
        try:
            return run_once(project_root)
        finally:
            log_event(LOGGER, "worker_stopping", "Supplemental research worker stopping", status="once")
    while True:
        try:
            run_once(project_root)
        except Exception:
            log_event(LOGGER, "worker_unhandled_error", "Supplemental research worker check failed", level=logging.ERROR, exception_type="unknown", sanitized_error="Worker check failed")
            LOGGER.exception("Supplemental research worker check failed.")
        next_check_at = (datetime.now().astimezone() + WORKER_CHECK_INTERVAL).isoformat(timespec="seconds")
        log_event(LOGGER, "worker_sleeping", "Supplemental research worker sleeping", next_check_at=next_check_at)
        time.sleep(WORKER_CHECK_INTERVAL.total_seconds())


if __name__ == "__main__":
    raise SystemExit(main())

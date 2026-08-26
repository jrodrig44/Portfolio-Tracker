from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any, Iterable

from supplemental_research import (
    FAILED_RETRY_DELAY,
    GENERAL_RESEARCH_EXPIRY,
    SUPPORTED_RESEARCH_MODELS,
    WORKER_CHECK_INTERVAL,
    SupplementalResearchService,
    connect_database,
    get_research_rows,
    parse_datetime,
    research_is_due,
    sanitize_error,
    set_setting,
    sync_active_holdings,
    utc_now,
    validated_research_model,
)


def research_contexts(
    holdings: Iterable[dict[str, Any]], metrics: Iterable[dict[str, Any]] = ()
) -> dict[str, dict[str, Any]]:
    """Build the same constrained research context used by the production UI."""
    metrics_by_ticker = {
        str(row.get("ticker") or "").strip().upper(): row for row in metrics
    }
    contexts: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        ticker = str(holding.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        metric = metrics_by_ticker.get(ticker, {})
        contexts[ticker] = {
            "ticker": ticker,
            "entity_name": ticker,
            "asset_type": str(holding.get("asset_type") or "Unknown"),
            "sector": str(holding.get("sector") or "Unknown"),
            "missing_fields": [
                "strategy_objective",
                "risk_characteristics",
                "recent_structural_developments",
            ],
            "existing_yahoo_information": {
                "sector": holding.get("sector") or "",
                "asset_type": holding.get("asset_type") or "",
                "current_price": metric.get("current_price", ""),
                "dividend_yield_pct": metric.get("dividend_yield_pct", ""),
                "annual_div_per_share": holding.get("annual_div_per_share", ""),
            },
        }
    return contexts


def _safe_json(value: object, expected: type) -> object:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def research_display_row(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return display-safe state without provider metadata, lease tokens, or raw JSON."""
    current = now or utc_now()
    archived = bool(row.get("archived"))
    enrolled = bool(row.get("manual_initial_research_completed"))
    status = str(row.get("research_status") or "missing")
    next_due = parse_datetime(row.get("next_due_at"))
    if archived:
        display_status = "Archived"
    elif status == "running":
        display_status = "Running"
    elif status == "failed" and next_due and next_due > current:
        display_status = "Retry pending"
    elif research_is_due(row, current):
        display_status = "Due for refresh"
    elif enrolled and row.get("last_success_at"):
        display_status = "Current"
    else:
        display_status = "Not enrolled"
    result = _safe_json(row.get("research_json"), dict)
    facts = result.get("verified_facts", []) if isinstance(result, dict) else []
    return {
        "ticker": str(row.get("ticker") or ""),
        "status": display_status,
        "enrolled": enrolled,
        "archived": archived,
        "last_success_at": row.get("last_success_at") or "Never",
        "next_due_at": row.get("next_due_at") or "Not scheduled",
        "model_used": row.get("model_used") or "—",
        "verified_facts": len(facts) if isinstance(facts, list) else 0,
        "error": sanitize_error(str(row.get("error_message") or "")),
        "research_markdown": str(row.get("research_markdown") or ""),
        "can_update": not archived and status != "running",
    }


class PortfolioResearchService:
    """NiceGUI-facing orchestration over the production research implementation."""

    _action_lock = threading.Lock()
    _active_tickers: set[str] = set()

    def __init__(self, db_path: Path | str, service: SupplementalResearchService | None = None) -> None:
        self.db_path = Path(db_path)
        self.service = service or SupplementalResearchService(self.db_path)

    def state(self, contexts: dict[str, dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
        # Production performs this synchronization on display. It creates no enrollment
        # and archives research retained for holdings no longer in the portfolio.
        sync_active_holdings(self.db_path, contexts.keys())
        rows = get_research_rows(self.db_path)
        active = [research_display_row(row, now) for row in rows if not row.get("archived")]
        archived = [research_display_row(row, now) for row in rows if row.get("archived")]
        jobs: list[dict[str, Any]] = []
        with connect_database(self.db_path) as connection:
            for row in connection.execute(
                "SELECT job_type, status, started_at, completed_at, error_message "
                "FROM supplemental_research_jobs ORDER BY id DESC LIMIT 10"
            ):
                jobs.append({
                    "job_type": row["job_type"],
                    "status": row["status"],
                    "started_at": row["started_at"] or "—",
                    "completed_at": row["completed_at"] or "—",
                    "error": sanitize_error(str(row["error_message"] or "")),
                })
        return {
            "model": validated_research_model(self.db_path),
            "models": list(SUPPORTED_RESEARCH_MODELS),
            "active": active,
            "archived": archived,
            "jobs": jobs,
            "expiry_days": GENERAL_RESEARCH_EXPIRY.days,
            "retry_hours": int(FAILED_RETRY_DELAY.total_seconds() // 3600),
            "worker_check_hours": int(WORKER_CHECK_INTERVAL.total_seconds() // 3600),
        }

    def successful_export_rows(self, tickers: Iterable[str]) -> list[dict[str, Any]]:
        """Read persisted successful research in an export-safe shape without starting work."""
        active = {str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()}
        exported: list[dict[str, Any]] = []
        for row in get_research_rows(self.db_path):
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker not in active or row.get("archived") or not row.get("last_success_at"):
                continue
            display = research_display_row(row)
            markdown = str(display.get("research_markdown") or "").strip()
            if not markdown:
                continue
            exported.append({
                "ticker": ticker,
                "source_type": "persisted_supplemental_research",
                "last_success_at": display["last_success_at"],
                "research_summary": markdown,
            })
        return exported

    def set_model(self, model: str) -> str:
        selected = str(model or "").strip()
        if selected not in SUPPORTED_RESEARCH_MODELS:
            raise ValueError("Select a supported supplemental research model.")
        set_setting(self.db_path, "supplemental_research_model", selected)
        return selected

    def update(self, ticker: str, context: dict[str, Any], model: str) -> dict[str, Any]:
        normalized = str(ticker or "").strip().upper()
        if normalized != str(context.get("ticker") or "").strip().upper():
            raise ValueError("Research context does not match the selected holding.")
        if model not in SUPPORTED_RESEARCH_MODELS:
            raise ValueError("Select a supported supplemental research model.")
        with self._action_lock:
            if normalized in self._active_tickers:
                return {"ticker": normalized, "status": "skipped", "reason": "already running"}
            self._active_tickers.add(normalized)
        try:
            return self.service.research_ticker(normalized, context, model, manual=True, force=True)
        finally:
            with self._action_lock:
                self._active_tickers.discard(normalized)

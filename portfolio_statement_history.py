from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from stash_history import (
    STATEMENT_HISTORY_DB_PATH,
    STATEMENTS_DIR,
    clear_scan_preview,
    delete_imported_history,
    history_summary,
    import_validated_history,
    load_performance_history,
    remove_manual_statement_review,
    run_statement_scan,
    save_manual_statement_review,
    scan_can_import,
    scan_state,
)


def statement_display_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return production review fields without exposing source hashes or parser dictionaries."""
    return [
        {
            "statement_start_date": row.get("statement_start_date") or "—",
            "statement_end_date": row.get("statement_end_date") or "—",
            "portfolio_value": row.get("portfolio_value"),
            "invested_value": row.get("invested_value"),
            "cash_value": row.get("cash_value"),
            "status": row.get("effective_status", row.get("status")),
            "raw_parser_status": row.get("raw_status", row.get("status")),
            "resolved_by": row.get("resolved_by") or "—",
            "warning": row.get("warning") or "",
        }
        for row in records
    ]


class StatementHistoryService:
    """Thin orchestration around the production statement parser and history store."""

    _guard = threading.Lock()
    _completed_tokens: set[tuple[str, str]] = set()

    def __init__(self, source_dir: Path = STATEMENTS_DIR, db_path: Path = STATEMENT_HISTORY_DB_PATH):
        self.source_dir = Path(source_dir)
        self.db_path = Path(db_path)

    def folder_status(self) -> dict[str, Any]:
        exists = self.source_dir.is_dir()
        count = len(list(self.source_dir.glob("*.pdf"))) if exists else 0
        return {"exists": exists, "pdf_count": count, "status": "Ready" if exists else "Missing"}

    def state(self) -> dict[str, Any]:
        state = scan_state(self.db_path)
        state["history_summary"] = history_summary(self.db_path)
        state["folder"] = self.folder_status()
        state["can_import"] = scan_can_import(state, int(state["folder"]["pdf_count"]))
        return state

    def scan(self, *, restart: bool = True) -> dict[str, Any]:
        if not self.source_dir.is_dir():
            raise FileNotFoundError("The configured Statements folder does not exist.")
        return run_statement_scan(self.source_dir, db_path=self.db_path, restart=restart)

    def clear_scan(self) -> None:
        clear_scan_preview(self.db_path)

    def save_review(self, review_month: str, statement_end_date: str, invested_value: float, cash_value: float, portfolio_value: float, confirmed: bool, *, manual_id: str | None = None) -> str:
        return save_manual_statement_review(
            review_month, statement_end_date, invested_value, cash_value,
            portfolio_value, confirmed, manual_id=manual_id, db_path=self.db_path,
        )

    def remove_review(self, manual_id: str) -> None:
        remove_manual_statement_review(manual_id, self.db_path)

    def import_ready(self, *, submission_token: str | None = None) -> dict[str, int] | None:
        key = (str(self.db_path.resolve()), str(submission_token))
        with self._guard:
            if submission_token and key in self._completed_tokens:
                return None
            state = self.state()
            if not state["can_import"]:
                raise ValueError("The completed scan is not ready to import. Resolve continuity and review items first.")
            result = import_validated_history(state["records"], self.db_path)
            if submission_token:
                self._completed_tokens.add(key)
            return result

    def performance_history(self) -> list[dict[str, Any]]:
        return load_performance_history(self.db_path)

    def delete_imported(self, *, confirmed: bool) -> None:
        if not confirmed:
            raise PermissionError("Deleting imported history requires explicit confirmation.")
        delete_imported_history(self.db_path)


__all__ = ["StatementHistoryService", "statement_display_records"]

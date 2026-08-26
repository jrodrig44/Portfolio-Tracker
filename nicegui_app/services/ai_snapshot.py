from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_ID = 1
_TABLE_NAME = "ai_analysis_snapshot"
_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
    snapshot_id INTEGER PRIMARY KEY CHECK (snapshot_id = {SNAPSHOT_ID}),
    schema_version INTEGER NOT NULL,
    analysis_type TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    model TEXT NOT NULL,
    result_markdown TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class AIAnalysisSnapshot:
    analysis_type: str
    completed_at_utc: str
    model: str
    result_markdown: str
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def friendly_utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return str(value or "")
    return parsed.strftime("%B %d, %Y at %I:%M %p UTC").replace(" 0", " ")


class AIAnalysisSnapshotService:
    """Persist exactly one display-ready AI Analysis result in the portfolio database."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _table_exists(self, connection: sqlite3.Connection) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_TABLE_NAME,),
        ).fetchone() is not None

    def load(self) -> AIAnalysisSnapshot | None:
        if not self.db_path.is_file():
            return None
        with closing(self._connect()) as connection:
            if not self._table_exists(connection):
                return None
            row = connection.execute(
                f"SELECT schema_version, analysis_type, completed_at_utc, model, result_markdown FROM {_TABLE_NAME} WHERE snapshot_id = ?",
                (SNAPSHOT_ID,),
            ).fetchone()
        if row is None:
            return None
        return AIAnalysisSnapshot(
            analysis_type=str(row["analysis_type"]),
            completed_at_utc=str(row["completed_at_utc"]),
            model=str(row["model"]),
            result_markdown=str(row["result_markdown"]),
            schema_version=int(row["schema_version"]),
        )

    def save(
        self,
        analysis_type: str,
        model: str,
        result_markdown: str,
        *,
        completed_at: datetime | None = None,
    ) -> AIAnalysisSnapshot:
        result = str(result_markdown or "").strip()
        if not result:
            raise ValueError("A successful AI analysis result is required before saving a snapshot.")
        snapshot = AIAnalysisSnapshot(
            analysis_type=str(analysis_type or "").strip(),
            completed_at_utc=utc_timestamp(completed_at),
            model=str(model or "").strip(),
            result_markdown=result,
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(_CREATE_TABLE_SQL)
                connection.execute(
                    f"""
                    INSERT INTO {_TABLE_NAME} (snapshot_id, schema_version, analysis_type, completed_at_utc, model, result_markdown)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        analysis_type = excluded.analysis_type,
                        completed_at_utc = excluded.completed_at_utc,
                        model = excluded.model,
                        result_markdown = excluded.result_markdown
                    """,
                    (
                        SNAPSHOT_ID,
                        snapshot.schema_version,
                        snapshot.analysis_type,
                        snapshot.completed_at_utc,
                        snapshot.model,
                        snapshot.result_markdown,
                    ),
                )
        return snapshot

    def clear(self) -> bool:
        if not self.db_path.is_file():
            return False
        with closing(self._connect()) as connection:
            with connection:
                if not self._table_exists(connection):
                    return False
                cursor = connection.execute(f"DELETE FROM {_TABLE_NAME} WHERE snapshot_id = ?", (SNAPSHOT_ID,))
        return cursor.rowcount > 0

    def count(self) -> int:
        if not self.db_path.is_file():
            return 0
        with closing(self._connect()) as connection:
            if not self._table_exists(connection):
                return 0
            row = connection.execute(f"SELECT COUNT(*) FROM {_TABLE_NAME}").fetchone()
        return int(row[0]) if row else 0


def save_successful_analysis_snapshot(
    service: AIAnalysisSnapshotService,
    state: Any,
) -> AIAnalysisSnapshot | None:
    """Persist only a provider-validated, fully successful analysis state."""
    if not is_successful_analysis_state(state):
        return None
    return service.save(
        str(getattr(state, "result_type", "")),
        str(getattr(state, "result_model", "")),
        str(getattr(state, "result_markdown", "")),
    )


def is_successful_analysis_state(state: Any) -> bool:
    """Use one success contract for snapshot persistence and current-result downloads."""
    return not (
        bool(getattr(state, "busy", False))
        or str(getattr(state, "last_stage", "")) != "success"
        or str(getattr(state, "error_message", "")).strip()
        or not str(getattr(state, "result_markdown", "")).strip()
    )


def resolve_analysis_download(service: AIAnalysisSnapshotService, state: Any) -> str | None:
    """Resolve display-ready Markdown without creating or modifying persistence."""
    if is_successful_analysis_state(state):
        return str(getattr(state, "result_markdown", ""))
    snapshot = service.load()
    if snapshot is not None and snapshot.result_markdown.strip():
        return snapshot.result_markdown
    return None

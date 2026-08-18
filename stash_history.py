from __future__ import annotations

import hashlib
import math
import multiprocessing
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
STATEMENTS_DIR = PROJECT_ROOT / "Statements"
STATEMENT_HISTORY_DB_PATH = DATA_DIR / "stash_statement_history.sqlite"
PARSER_VERSION = "stash-pdf-v1"
DOCUMENT_TIMEOUT_SECONDS = 30.0
EARLIEST_EXPECTED_DATE = date(2021, 8, 1)
_DATE_PATTERN = r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})"
_RANGE_PATTERN = re.compile(rf"(?:statement\s+period|period|from)\s*:?\s*({_DATE_PATTERN})\s*(?:-|to|through)\s*({_DATE_PATTERN})", re.IGNORECASE)
_HEADER_RANGE_PATTERN = re.compile(rf"({_DATE_PATTERN})\s*(?:-|to|through)\s*({_DATE_PATTERN})", re.IGNORECASE)


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_date(value: str) -> date | None:
    cleaned = value.strip().replace(".", "")
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def _parse_currency(value: str) -> float | None:
    normalized = value.replace("$", "").replace(",", "").strip()
    negative = normalized.startswith("(") and normalized.endswith(")")
    try:
        parsed = float(normalized.strip("()"))
    except ValueError:
        return None
    return (-parsed if negative else parsed) if math.isfinite(parsed) else None


def _value_after_label(text: str, labels: tuple[str, ...]) -> float | None:
    currency = r"(\(?\s*\$?\s*[\d,]+(?:\.\d{2})?\s*\)?)"
    for label in labels:
        match = re.search(rf"{label}\s*:?\s*{currency}", text, re.IGNORECASE)
        if match:
            value = _parse_currency(match.group(1))
            if value is not None:
                return value
    return None


def _closing_value_from_row(words: list[dict[str, Any]], row_label: tuple[str, ...]) -> float | None:
    normalized_label = tuple(word.upper() for word in row_label)
    for word in words:
        if str(word.get("text", "")).upper() != normalized_label[0]:
            continue
        row = [item for item in words if abs(float(item.get("top", 0.0)) - float(word.get("top", 0.0))) < 2]
        row_text = [str(item.get("text", "")).upper() for item in row]
        if not all(label in row_text for label in normalized_label):
            continue
        values = [_parse_currency(str(item.get("text", ""))) for item in row]
        numeric_values = [value for value in values if value is not None]
        if numeric_values:
            return numeric_values[-1]
    return None


def extract_statement_layout_values(pdf_path: Path) -> dict[str, float | None]:
    with pdfplumber.open(pdf_path) as pdf:
        return extract_statement_layout_values_from_pages(pdf.pages)


def extract_statement_layout_values_from_pages(pages: Any) -> dict[str, float | None]:
    invested_value = None
    cash_value = None
    for page in pages:
        words = page.extract_words()
        invested_value = invested_value if invested_value is not None else _closing_value_from_row(words, ("TOTAL", "EQUITY", "HOLDINGS"))
        cash_value = cash_value if cash_value is not None else _closing_value_from_row(words, ("NET", "ACCOUNT", "BALANCE"))
    portfolio_value = invested_value + cash_value if invested_value is not None and cash_value is not None else invested_value
    return {"portfolio_value": portfolio_value, "invested_value": invested_value, "cash_value": cash_value}


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _parse_statement_document_worker(pdf_path_text: str, queue: Any) -> None:
    """Run all PDF work in a child so the parent can terminate a stuck document."""
    pdf_path = Path(pdf_path_text)
    try:
        queue.put({"kind": "stage", "stage": "reading bytes"})
        content = pdf_path.read_bytes()
        queue.put({"kind": "stage", "stage": "pdfplumber text extraction"})
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if not text.strip():
                queue.put({"kind": "result", "record": {"source_hash": _hash_bytes(content), "status": "unparseable", "warning": "No extractable statement text."}})
                return
            record = parse_statement_text(text)
            queue.put({"kind": "stage", "stage": "pdfplumber layout extraction"})
            record.update({key: value for key, value in extract_statement_layout_values_from_pages(pdf.pages).items() if value is not None})
        record["source_hash"] = _hash_bytes(content)
        record["status"], record["warning"] = _validate_record(record)
        queue.put({"kind": "result", "record": record})
    except Exception:
        queue.put({"kind": "result", "record": {"source_hash": None, "status": "unparseable", "warning": "Statement could not be parsed locally."}})


def parse_statement_text(text: str) -> dict[str, Any]:
    period = _HEADER_RANGE_PATTERN.search("\n".join(text.splitlines()[:10])) or _RANGE_PATTERN.search(text)
    start_date = _parse_date(period.group(1)) if period else None
    end_date = _parse_date(period.group(2)) if period else None
    if end_date is None:
        ending_match = re.search(rf"(?:ending|as\s+of)\s*:?\s*({_DATE_PATTERN})", text, re.IGNORECASE)
        end_date = _parse_date(ending_match.group(1)) if ending_match else None
    return {
        "statement_start_date": start_date.isoformat() if start_date else None,
        "statement_end_date": end_date.isoformat() if end_date else None,
        "portfolio_value": _value_after_label(text, ("ending\\s+(?:account|portfolio)\\s+value", "total\\s+(?:account|portfolio)\\s+value", "account\\s+value", "portfolio\\s+value")),
        "invested_value": _value_after_label(text, ("invested\\s+(?:securities\\s+)?value", "securities\\s+value")),
        "cash_value": _value_after_label(text, ("cash\\s+balance", "cash\\s+value")),
        "cost_basis": _value_after_label(text, ("total\\s+cost\\s+basis", "cost\\s+basis")),
        "contribution_amount": _value_after_label(text, ("(?:contributions|deposits)\\s+(?:during|for)\\s+(?:the\\s+)?period",)),
        "withdrawal_amount": _value_after_label(text, ("withdrawals?\\s+(?:during|for)\\s+(?:the\\s+)?period",)),
    }


def _validate_record(record: dict[str, Any]) -> tuple[str, str]:
    end_date_text = record.get("statement_end_date")
    end_date = _parse_date(str(end_date_text)) if end_date_text else None
    start_date_text = record.get("statement_start_date")
    start_date = _parse_date(str(start_date_text)) if start_date_text else None
    value = record.get("portfolio_value")
    if end_date is None:
        return "needs_review", "Statement ending date was not found."
    if end_date < EARLIEST_EXPECTED_DATE:
        return "needs_review", "Statement ending date is outside expected coverage."
    if start_date and end_date < start_date:
        return "needs_review", "Statement ending date precedes its start date."
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        return "needs_review", "Ending portfolio value is missing or invalid."
    invested_value, cash_value = record.get("invested_value"), record.get("cash_value")
    if all(isinstance(item, (int, float)) for item in (invested_value, cash_value)):
        if abs(float(invested_value) + float(cash_value) - float(value)) > max(1.0, float(value) * 0.01):
            return "needs_review", "Invested and cash values differ materially from the ending total."
    return "valid", "Ready to import."


def scan_local_statements(
    source_dir: Path = STATEMENTS_DIR,
    *,
    timeout_seconds: float = DOCUMENT_TIMEOUT_SECONDS,
    on_document_started: Any = None,
    on_document_complete: Any = None,
    worker_target: Any = _parse_statement_document_worker,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    total_count = len(pdf_paths)
    for sequence, pdf_path in enumerate(pdf_paths, start=1):
        started_at = time.perf_counter()
        last_stage = "reading bytes"
        if callable(on_document_started):
            on_document_started(sequence, total_count)
        queue: Any = multiprocessing.Queue()
        process = multiprocessing.Process(target=worker_target, args=(str(pdf_path), queue))
        process.start()
        record: dict[str, Any] | None = None
        deadline = started_at + timeout_seconds
        while time.perf_counter() < deadline and record is None:
            try:
                message = queue.get(timeout=min(0.2, max(0.01, deadline - time.perf_counter())))
                if message.get("kind") == "stage":
                    last_stage = str(message.get("stage", last_stage))
                elif message.get("kind") == "result":
                    record = dict(message["record"])
            except Exception:
                continue
        if record is None:
            if process.is_alive():
                process.terminate()
            record = {"source_hash": None, "status": "unparseable", "warning": f"Local parsing timed out during {last_stage}."}
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()
        queue.close()
        record["source_path"] = pdf_path
        records.append(record)
        if callable(on_document_complete):
            on_document_complete(sequence, total_count, record, round((time.perf_counter() - started_at) * 1000))
    return records


def ensure_history_schema(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS statement_imports (id INTEGER PRIMARY KEY AUTOINCREMENT, source_hash TEXT NOT NULL UNIQUE, statement_start_date TEXT, statement_end_date TEXT NOT NULL, imported_at TEXT NOT NULL, parser_version TEXT NOT NULL, status TEXT NOT NULL, warning_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS statement_portfolio_history (id INTEGER PRIMARY KEY AUTOINCREMENT, import_id INTEGER NOT NULL, history_date TEXT NOT NULL, portfolio_value REAL NOT NULL, invested_value REAL, cash_value REAL, cost_basis REAL, contribution_amount REAL, withdrawal_amount REAL, source_type TEXT NOT NULL DEFAULT 'stash_statement', FOREIGN KEY(import_id) REFERENCES statement_imports(id), UNIQUE(history_date, source_type));
            CREATE TABLE IF NOT EXISTS live_portfolio_snapshots (snapshot_date TEXT PRIMARY KEY, portfolio_value REAL NOT NULL, source_type TEXT NOT NULL DEFAULT 'live_portfolio_snapshot');
            CREATE INDEX IF NOT EXISTS idx_statement_history_date ON statement_portfolio_history(history_date);
            CREATE INDEX IF NOT EXISTS idx_live_snapshot_date ON live_portfolio_snapshots(snapshot_date);
        """)


def history_summary(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> dict[str, int]:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        return {"imported": int(conn.execute("SELECT COUNT(*) FROM statement_imports").fetchone()[0]), "history_records": int(conn.execute("SELECT COUNT(*) FROM statement_portfolio_history").fetchone()[0]), "live_snapshots": int(conn.execute("SELECT COUNT(*) FROM live_portfolio_snapshots").fetchone()[0])}


def classify_scan(records: list[dict[str, Any]], db_path: Path = STATEMENT_HISTORY_DB_PATH) -> list[dict[str, Any]]:
    imported_hashes: set[str] = set()
    existing_dates: dict[str, float] = {}
    if db_path.exists():
        with closing(sqlite3.connect(db_path)) as conn:
            imported_hashes = {str(row[0]) for row in conn.execute("SELECT source_hash FROM statement_imports")}
            existing_dates = {str(row[0]): float(row[1]) for row in conn.execute("SELECT history_date, portfolio_value FROM statement_portfolio_history")}
    classified: list[dict[str, Any]] = []
    for record in records:
        safe_record = dict(record)
        if safe_record.get("source_hash") in imported_hashes:
            safe_record["status"], safe_record["warning"] = "duplicate", "Previously imported."
        elif safe_record.get("status") == "valid" and safe_record.get("statement_end_date") in existing_dates:
            if abs(existing_dates[str(safe_record["statement_end_date"])] - float(safe_record["portfolio_value"])) > 0.01:
                safe_record["status"], safe_record["warning"] = "needs_review", "A different value already exists for this date."
            else:
                safe_record["status"], safe_record["warning"] = "duplicate", "A matching statement date already exists."
        classified.append(safe_record)
    return classified


def import_validated_history(records: list[dict[str, Any]], db_path: Path = STATEMENT_HISTORY_DB_PATH) -> dict[str, int]:
    prepare_history_database(db_path)
    result = {"imported": 0, "duplicates": 0, "needs_review": 0, "unparseable": 0}
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN")
            for record in records:
                status = record.get("status")
                if status == "duplicate": result["duplicates"] += 1; continue
                if status == "unparseable": result["unparseable"] += 1; continue
                if status != "valid": result["needs_review"] += 1; continue
                try:
                    conn.execute("SAVEPOINT statement_record")
                    cursor = conn.execute("INSERT INTO statement_imports (source_hash, statement_start_date, statement_end_date, imported_at, parser_version, status, warning_count) VALUES (?, ?, ?, ?, ?, ?, ?)", (record["source_hash"], record.get("statement_start_date"), record["statement_end_date"], datetime.now(timezone.utc).isoformat(timespec="seconds"), PARSER_VERSION, "imported", 0))
                    conn.execute("INSERT INTO statement_portfolio_history (import_id, history_date, portfolio_value, invested_value, cash_value, cost_basis, contribution_amount, withdrawal_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (cursor.lastrowid, record["statement_end_date"], record["portfolio_value"], record.get("invested_value"), record.get("cash_value"), record.get("cost_basis"), record.get("contribution_amount"), record.get("withdrawal_amount")))
                    conn.execute("RELEASE SAVEPOINT statement_record")
                    result["imported"] += 1
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK TO SAVEPOINT statement_record")
                    conn.execute("RELEASE SAVEPOINT statement_record")
                    result["duplicates"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return result


def load_statement_history(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> list[dict[str, Any]]:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT history_date, portfolio_value, source_type FROM statement_portfolio_history ORDER BY history_date").fetchall()
    return [{"history_date": row[0], "portfolio_value": float(row[1]), "source_type": row[2]} for row in rows]


def upsert_live_portfolio_snapshot(portfolio_value: float, snapshot_date: str | None = None, db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    if not isinstance(portfolio_value, (int, float)) or not math.isfinite(float(portfolio_value)) or portfolio_value <= 0:
        raise ValueError("Live portfolio snapshot requires a positive finite value.")
    prepare_history_database(db_path)
    snapshot_date = snapshot_date or date.today().isoformat()
    if _parse_date(snapshot_date) is None:
        raise ValueError("Live portfolio snapshot date is invalid.")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("INSERT INTO live_portfolio_snapshots (snapshot_date, portfolio_value) VALUES (?, ?) ON CONFLICT(snapshot_date) DO UPDATE SET portfolio_value = excluded.portfolio_value", (snapshot_date, float(portfolio_value)))
        conn.commit()


def load_performance_history(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> list[dict[str, Any]]:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT history_date, portfolio_value, source_type FROM statement_portfolio_history UNION ALL SELECT snapshot_date, portfolio_value, source_type FROM live_portfolio_snapshots ORDER BY 1").fetchall()
    return [{"history_date": row[0], "portfolio_value": float(row[1]), "source_type": row[2]} for row in rows]


def delete_imported_history(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM statement_portfolio_history")
        conn.execute("DELETE FROM statement_imports")
        conn.commit()


def is_placeholder_database(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> bool:
    if not db_path.exists(): return False
    with closing(sqlite3.connect(db_path)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"statement_history_imports", "statement_history_holdings"}.issubset(tables)


def prepare_history_database(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    """Create the current schema, replacing only the known fabricated legacy database."""
    if is_placeholder_database(db_path):
        with closing(sqlite3.connect(db_path)) as conn:
            fabricated_only = conn.execute(
                "SELECT COUNT(*) FROM statement_history_holdings WHERE ticker NOT IN ('AAPL', 'JEPI') OR shares NOT IN (10.0, 5.0) OR market_value NOT IN (1000.0, 250.0)"
            ).fetchone()[0] == 0
        if not fabricated_only:
            raise RuntimeError("The existing statement history database cannot be identified as placeholder data.")
        db_path.unlink()
        for suffix in ("-wal", "-shm", "-journal"):
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    ensure_history_schema(db_path)

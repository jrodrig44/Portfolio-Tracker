from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
STATEMENTS_DIR = PROJECT_ROOT / "Statements"
STATEMENT_HISTORY_DB_PATH = DATA_DIR / "stash_statement_history.sqlite"
PARSER_VERSION = "stash-pdf-v1"
EARLIEST_EXPECTED_DATE = date(2021, 8, 1)
_DATE_PATTERN = r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})"
_HEADER_RANGE_PATTERN = re.compile(rf"({_DATE_PATTERN})\s*(?:[-\u2013\u2014]|to|through)\s*({_DATE_PATTERN})", re.IGNORECASE)


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


def _word_lines(words: list[dict[str, Any]], tolerance: float = 3.0) -> list[tuple[float, float, str]]:
    grouped: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item.get("top", 0.0)), float(item.get("x0", 0.0)))):
        if not grouped or abs(float(grouped[-1][0].get("top", 0.0)) - float(word.get("top", 0.0))) > tolerance:
            grouped.append([word])
        else:
            grouped[-1].append(word)
    return [
        (float(line[0].get("top", 0.0)), max(float(item.get("x1", 0.0)) for item in line), " ".join(str(item.get("text", "")) for item in sorted(line, key=lambda item: float(item.get("x0", 0.0)))))
        for line in grouped
    ]


def parse_statement_header_words(words: list[dict[str, Any]], page_height: float) -> dict[str, Any]:
    header_limit = float(page_height) * 0.25
    lines = [line for line in _word_lines(words) if line[0] <= header_limit]
    account_tops = [float(word.get("top", 0.0)) for word in words if float(word.get("top", 0.0)) <= header_limit and str(word.get("text", "")).upper() == "ACCOUNT"]
    period = None
    period_top = None
    for top, _right, line_text in lines:
        candidate = _HEADER_RANGE_PATTERN.search(line_text)
        if candidate:
            period = candidate
            period_top = top
            break
    nearby_account_found = period_top is not None and any(abs(account_top - period_top) <= 160 for account_top in account_tops)
    start_date = _parse_date(period.group(1)) if period else None
    end_date = _parse_date(period.group(2)) if period else None
    return {
        "statement_start_date": start_date.isoformat() if start_date else None,
        "statement_end_date": end_date.isoformat() if end_date else None,
        "diagnostics": {
            "verified_header_found": period is not None,
            "nearby_date_range_found": period is not None,
            "nearby_account_marker_found": nearby_account_found,
            "verified_account_summary_found": False,
            "balance_rows_found": False,
            "statement_date_accepted": end_date is not None,
        },
    }


def _character_lines(chars: list[dict[str, Any]], tolerance: float = 2.0) -> list[tuple[float, str]]:
    grouped: list[list[dict[str, Any]]] = []
    for character in sorted(chars, key=lambda item: (float(item.get("top", 0.0)), float(item.get("x0", 0.0)))):
        if not grouped or abs(float(grouped[-1][0].get("top", 0.0)) - float(character.get("top", 0.0))) > tolerance:
            grouped.append([character])
        else:
            grouped[-1].append(character)
    lines: list[tuple[float, str]] = []
    for line in grouped:
        ordered = sorted(line, key=lambda item: float(item.get("x0", 0.0)))
        text_parts: list[str] = []
        previous_right = None
        for character in ordered:
            gap = float(character.get("x0", 0.0)) - previous_right if previous_right is not None else 0.0
            size = float(character.get("size", 10.0) or 10.0)
            if previous_right is not None and gap > max(1.5, size * 0.35):
                text_parts.append(" ")
            text_parts.append(str(character.get("text", "")))
            previous_right = float(character.get("x1", character.get("x0", 0.0)))
        lines.append((float(ordered[0].get("top", 0.0)), "".join(text_parts)))
    return lines


def parse_statement_header_chars(chars: list[dict[str, Any]], page_height: float) -> dict[str, Any]:
    header_limit = float(page_height) * 0.25
    lines = [line for line in _character_lines(chars) if line[0] <= header_limit]
    period = None
    period_top = None
    for top, line_text in lines:
        candidate = _HEADER_RANGE_PATTERN.search(line_text)
        if candidate:
            period, period_top = candidate, top
            break
    account_tops = [top for top, line_text in lines if re.search(r"\baccount\b", line_text, re.IGNORECASE)]
    nearby_account_found = period_top is not None and any(abs(account_top - period_top) <= 160 for account_top in account_tops)
    start_date = _parse_date(period.group(1)) if period else None
    end_date = _parse_date(period.group(2)) if period else None
    return {
        "statement_start_date": start_date.isoformat() if start_date else None,
        "statement_end_date": end_date.isoformat() if end_date else None,
        "diagnostics": {
            "verified_header_found": period is not None,
            "nearby_date_range_found": period is not None,
            "nearby_account_marker_found": nearby_account_found,
            "verified_account_summary_found": False,
            "balance_rows_found": False,
            "statement_date_accepted": end_date is not None,
        },
    }


def _balance_row(words: list[dict[str, Any]], row_label: tuple[str, ...]) -> tuple[float | None, float | None]:
    normalized_label = tuple(word.upper() for word in row_label)
    for word in words:
        if str(word.get("text", "")).upper() != normalized_label[0]:
            continue
        row = [item for item in words if abs(float(item.get("top", 0.0)) - float(word.get("top", 0.0))) < 2]
        row_text = [str(item.get("text", "")).upper() for item in row]
        if all(label in row_text for label in normalized_label):
            return _closing_value_from_row(row, row_label), float(word.get("top", 0.0))
    return None, None


def extract_statement_layout_values_from_pages(pages: Any) -> dict[str, float | None]:
    invested_value = cash_value = portfolio_value = None
    summary_found = balance_rows_found = accounting_passed = False
    for page in pages:
        words = page.extract_words()
        invested_value, priced_top = _balance_row(words, ("TOTAL", "PRICED", "PORTFOLIO"))
        cash_value, cash_top = _balance_row(words, ("NET", "ACCOUNT", "BALANCE"))
        portfolio_value, equity_top = _balance_row(words, ("TOTAL", "EQUITY", "HOLDINGS"))
        balance_rows_found = all(value is not None for value in (invested_value, cash_value, portfolio_value))
        row_tops = [top for top in (priced_top, cash_top, equity_top) if top is not None]
        summary_found = balance_rows_found and len(row_tops) == 3 and max(row_tops) - min(row_tops) <= 220
        accounting_passed = summary_found and abs(float(invested_value) + float(cash_value) - float(portfolio_value)) <= 0.01
        if not accounting_passed:
            invested_value = cash_value = portfolio_value = None
        break
    return {"portfolio_value": portfolio_value, "invested_value": invested_value, "cash_value": cash_value, "summary_found": summary_found, "balance_rows_found": balance_rows_found, "accounting_passed": accounting_passed}


def _parse_statement_document(pdf_path: Path) -> dict[str, Any]:
    try:
        content = pdf_path.read_bytes()
        with pdfplumber.open(pdf_path) as pdf:
            first_page = pdf.pages[0]
            first_page_words = first_page.extract_words()
            if not first_page_words:
                return {"source_hash": _hash_bytes(content), "status": "unparseable", "warning": "No extractable statement text."}
            record = parse_statement_header_words(first_page_words, float(first_page.height))
            if record["statement_end_date"] is None:
                record = parse_statement_header_chars(first_page.chars, float(first_page.height))
            if record["statement_end_date"] is not None:
                summary_values = extract_statement_layout_values_from_pages([first_page])
                record.update({key: value for key, value in summary_values.items() if key in {"portfolio_value", "invested_value", "cash_value"} and value is not None})
                record["diagnostics"]["verified_account_summary_found"] = bool(summary_values["summary_found"])
                record["diagnostics"]["balance_rows_found"] = bool(summary_values["balance_rows_found"])
                record["diagnostics"]["accounting_equation_passed"] = bool(summary_values["accounting_passed"])
        record["source_hash"] = _hash_bytes(content)
        record["status"], record["warning"] = _validate_record(record)
        return record
    except Exception:
        return {"source_hash": None, "status": "unparseable", "warning": "Statement could not be parsed locally."}


def parse_statement_text(text: str) -> dict[str, Any]:
    """Parse only a verified account-statement header region, never disclosure text."""
    header_lines = text.splitlines()[:8]
    header_region = "\n".join(header_lines)
    period = _HEADER_RANGE_PATTERN.search("\n".join(header_lines[:3]))
    account_header_found = any(re.search(r"\baccount\b", line, re.IGNORECASE) for line in header_lines[:6])
    header_found = period is not None and account_header_found
    period = period if header_found else None
    start_date = _parse_date(period.group(1)) if period else None
    end_date = _parse_date(period.group(2)) if period else None
    return {
        "statement_start_date": start_date.isoformat() if start_date else None,
        "statement_end_date": end_date.isoformat() if end_date else None,
        "portfolio_value": _value_after_label(header_region, ("ending\\s+(?:account|portfolio)\\s+value", "total\\s+(?:account|portfolio)\\s+value", "account\\s+value", "portfolio\\s+value")),
        "invested_value": _value_after_label(header_region, ("invested\\s+(?:securities\\s+)?value", "securities\\s+value")),
        "cash_value": _value_after_label(header_region, ("cash\\s+balance", "cash\\s+value")),
        "cost_basis": _value_after_label(header_region, ("total\\s+cost\\s+basis", "cost\\s+basis")),
        "contribution_amount": _value_after_label(header_region, ("(?:contributions|deposits)\\s+(?:during|for)\\s+(?:the\\s+)?period",)),
        "withdrawal_amount": _value_after_label(header_region, ("withdrawals?\\s+(?:during|for)\\s+(?:the\\s+)?period",)),
        "diagnostics": {"verified_header_found": header_found, "verified_account_summary_found": False, "statement_date_accepted": end_date is not None},
    }


def _validate_record(record: dict[str, Any]) -> tuple[str, str]:
    end_date_text = record.get("statement_end_date")
    end_date = _parse_date(str(end_date_text)) if end_date_text else None
    start_date_text = record.get("statement_start_date")
    start_date = _parse_date(str(start_date_text)) if start_date_text else None
    value = record.get("portfolio_value")
    if end_date is None:
        return "needs_review", "Verified statement-period header was not found."
    if end_date < EARLIEST_EXPECTED_DATE:
        return "needs_review", "Statement ending date is outside expected coverage."
    if start_date and end_date < start_date:
        return "needs_review", "Statement ending date precedes its start date."
    if end_date and end_date.day != __import__("calendar").monthrange(end_date.year, end_date.month)[1]:
        return "needs_review", "Statement ending date is not the final calendar day of its month."
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        return "needs_review", "Ending portfolio value is missing or invalid."
    invested_value, cash_value = record.get("invested_value"), record.get("cash_value")
    if all(isinstance(item, (int, float)) for item in (invested_value, cash_value)):
        if abs(float(invested_value) + float(cash_value) - float(value)) > 0.01:
            return "needs_review", "Invested and cash values differ materially from the ending total."
    return "valid", "Ready to import."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_history_schema(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS statement_imports (id INTEGER PRIMARY KEY AUTOINCREMENT, source_hash TEXT NOT NULL UNIQUE, statement_start_date TEXT, statement_end_date TEXT NOT NULL, imported_at TEXT NOT NULL, parser_version TEXT NOT NULL, status TEXT NOT NULL, warning_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS statement_portfolio_history (id INTEGER PRIMARY KEY AUTOINCREMENT, import_id INTEGER NOT NULL, history_date TEXT NOT NULL, portfolio_value REAL NOT NULL, invested_value REAL, cash_value REAL, cost_basis REAL, contribution_amount REAL, withdrawal_amount REAL, source_type TEXT NOT NULL DEFAULT 'stash_statement', FOREIGN KEY(import_id) REFERENCES statement_imports(id), UNIQUE(history_date, source_type));
            CREATE TABLE IF NOT EXISTS live_portfolio_snapshots (snapshot_date TEXT PRIMARY KEY, portfolio_value REAL NOT NULL, source_type TEXT NOT NULL DEFAULT 'live_portfolio_snapshot');
            CREATE TABLE IF NOT EXISTS statement_scan_jobs (job_id TEXT PRIMARY KEY, total_count INTEGER NOT NULL, processed_count INTEGER NOT NULL DEFAULT 0, detected_pdf_count INTEGER NOT NULL DEFAULT 0, unique_pdf_count INTEGER NOT NULL DEFAULT 0, processed_unique_count INTEGER NOT NULL DEFAULT 0, duplicate_file_count INTEGER NOT NULL DEFAULT 0, scan_status TEXT NOT NULL, parser_version TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS statement_scan_preview (source_hash TEXT PRIMARY KEY, scan_status TEXT NOT NULL, statement_start_date TEXT, statement_end_date TEXT, portfolio_value REAL, invested_value REAL, cash_value REAL, cost_basis REAL, contribution_amount REAL, withdrawal_amount REAL, validation_status TEXT NOT NULL, warning TEXT NOT NULL DEFAULT '', header_found INTEGER NOT NULL DEFAULT 0, nearby_date_range_found INTEGER NOT NULL DEFAULT 0, nearby_account_marker_found INTEGER NOT NULL DEFAULT 0, account_summary_found INTEGER NOT NULL DEFAULT 0, balance_rows_found INTEGER NOT NULL DEFAULT 0, accounting_equation_passed INTEGER NOT NULL DEFAULT 0, parser_version TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manual_statement_reviews (manual_id TEXT PRIMARY KEY, review_month TEXT NOT NULL UNIQUE, statement_end_date TEXT NOT NULL, portfolio_value REAL NOT NULL, invested_value REAL NOT NULL, cash_value REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, validation_status TEXT NOT NULL, warning TEXT NOT NULL, accounting_verified INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_statement_history_date ON statement_portfolio_history(history_date);
            CREATE INDEX IF NOT EXISTS idx_live_snapshot_date ON live_portfolio_snapshots(snapshot_date);
            CREATE INDEX IF NOT EXISTS idx_scan_preview_status ON statement_scan_preview(validation_status);
        """)
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(statement_scan_jobs)")}
        for column in ("detected_pdf_count", "unique_pdf_count", "processed_unique_count", "duplicate_file_count"):
            if column not in job_columns:
                conn.execute(f"ALTER TABLE statement_scan_jobs ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        preview_columns = {row[1] for row in conn.execute("PRAGMA table_info(statement_scan_preview)")}
        if "warning" not in preview_columns:
            conn.execute("ALTER TABLE statement_scan_preview ADD COLUMN warning TEXT NOT NULL DEFAULT ''")
        for column in ("header_found", "nearby_date_range_found", "nearby_account_marker_found", "account_summary_found", "balance_rows_found", "accounting_equation_passed"):
            if column not in preview_columns:
                conn.execute(f"ALTER TABLE statement_scan_preview ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        manual_columns = {row[1] for row in conn.execute("PRAGMA table_info(manual_statement_reviews)")}
        if "accounting_verified" not in manual_columns:
            conn.execute("ALTER TABLE manual_statement_reviews ADD COLUMN accounting_verified INTEGER NOT NULL DEFAULT 0")
            conn.execute("DELETE FROM manual_statement_reviews")
        conn.commit()


def _scan_job(db_path: Path) -> dict[str, Any]:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT detected_pdf_count, unique_pdf_count, processed_unique_count, duplicate_file_count, scan_status, updated_at FROM statement_scan_jobs WHERE job_id = 'local_statements'").fetchone()
    if not row:
        return {"detected_pdf_count": 0, "unique_pdf_count": 0, "processed_unique_count": 0, "duplicate_file_count": 0, "processed_count": 0, "total_count": 0, "scan_status": "not_started", "updated_at": ""}
    detected_pdf_count, unique_pdf_count, processed_unique_count, duplicate_file_count = (int(value) for value in row[:4])
    return {"detected_pdf_count": detected_pdf_count, "unique_pdf_count": unique_pdf_count, "processed_unique_count": processed_unique_count, "duplicate_file_count": duplicate_file_count, "processed_count": processed_unique_count + duplicate_file_count, "total_count": detected_pdf_count, "scan_status": str(row[4]), "updated_at": str(row[5])}


def load_scan_preview(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> list[dict[str, Any]]:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT source_hash, scan_status, statement_start_date, statement_end_date, portfolio_value, invested_value, cash_value, cost_basis, contribution_amount, withdrawal_amount, validation_status, warning, header_found, nearby_date_range_found, nearby_account_marker_found, account_summary_found, balance_rows_found, accounting_equation_passed FROM statement_scan_preview ORDER BY statement_end_date, source_hash").fetchall()
    keys = ["source_hash", "scan_status", "statement_start_date", "statement_end_date", "portfolio_value", "invested_value", "cash_value", "cost_basis", "contribution_amount", "withdrawal_amount", "status", "warning", "header_found", "nearby_date_range_found", "nearby_account_marker_found", "account_summary_found", "balance_rows_found", "accounting_equation_passed"]
    return [dict(zip(keys, row)) for row in rows]


def load_manual_statement_reviews(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> list[dict[str, Any]]:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT manual_id, review_month, statement_end_date, portfolio_value, invested_value, cash_value, confirmed, validation_status, warning FROM manual_statement_reviews ORDER BY review_month").fetchall()
    return [{"source_hash": row[0], "review_month": row[1], "statement_start_date": None, "statement_end_date": row[2], "portfolio_value": float(row[3]), "invested_value": float(row[4]), "cash_value": float(row[5]), "confirmed": bool(row[6]), "status": row[7], "warning": row[8], "source_type": "manual_statement_review"} for row in rows]


def missing_statement_months(records: list[dict[str, Any]]) -> list[str]:
    expected = []
    year, month = 2021, 8
    while (year, month) <= (2026, 7):
        expected.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    represented = {str(record["statement_end_date"])[:7] for record in records if record.get("status") == "valid" and record.get("statement_end_date")}
    return sorted(set(expected) - represented)


def save_manual_statement_review(review_month: str, statement_end_date: str, invested_value: float, cash_value: float, portfolio_value: float, confirmed: bool, *, manual_id: str | None = None, db_path: Path = STATEMENT_HISTORY_DB_PATH) -> str:
    prepare_history_database(db_path)
    month_date = _parse_date(f"{review_month}-01")
    end_date = _parse_date(statement_end_date)
    if month_date is None or end_date is None or end_date.strftime("%Y-%m") != review_month or end_date.day != __import__("calendar").monthrange(end_date.year, end_date.month)[1]:
        raise ValueError("Statement ending date must be the final calendar day of the selected missing month.")
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) >= 0 for value in (invested_value, cash_value, portfolio_value)):
        raise ValueError("Closing values must be finite and nonnegative.")
    if abs(float(invested_value) + float(cash_value) - float(portfolio_value)) > 0.01:
        raise ValueError("Total Priced Portfolio plus Net Account Balance must equal Total Equity Holdings.")
    if not confirmed:
        raise ValueError("Manual statement values require explicit confirmation.")
    existing = load_scan_preview(db_path) + [record for record in load_manual_statement_reviews(db_path) if record["source_hash"] != manual_id]
    existing.extend({"statement_end_date": row["history_date"], "status": "valid"} for row in load_statement_history(db_path))
    if review_month not in missing_statement_months(existing):
        raise ValueError("Selected month is already represented.")
    manual_id = manual_id or f"manual-{uuid.uuid4().hex}"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("INSERT INTO manual_statement_reviews (manual_id, review_month, statement_end_date, portfolio_value, invested_value, cash_value, confirmed, validation_status, warning, accounting_verified, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', 'Manually verified against the local statement.', 1, ?) ON CONFLICT(manual_id) DO UPDATE SET review_month=excluded.review_month, statement_end_date=excluded.statement_end_date, portfolio_value=excluded.portfolio_value, invested_value=excluded.invested_value, cash_value=excluded.cash_value, confirmed=excluded.confirmed, validation_status='valid', warning='Manually verified against the local statement.', accounting_verified=1, updated_at=excluded.updated_at", (manual_id, review_month, statement_end_date, float(portfolio_value), float(invested_value), float(cash_value), int(confirmed), _utc_now()))
        conn.commit()
    return manual_id


def remove_manual_statement_review(manual_id: str, db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM manual_statement_reviews WHERE manual_id=?", (manual_id,))
        conn.commit()


def clear_manual_statement_reviews(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM manual_statement_reviews")
        conn.commit()


def scan_state(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> dict[str, Any]:
    state = _scan_job(db_path)
    if state["scan_status"] == "completed":
        reclassify_scan_preview(db_path)
    preview_records = load_scan_preview(db_path)
    manual_records = load_manual_statement_reviews(db_path)
    records = preview_records + manual_records
    state.update({
        "ready": sum(1 for record in records if record["status"] == "valid"),
        "needs_review": sum(1 for record in records if record["status"] == "needs_review"),
        "unparseable": sum(1 for record in records if record["status"] == "unparseable"),
        "records": records,
    })
    expected_months = []
    year, month = 2021, 8
    while (year, month) <= (2026, 7):
        expected_months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    valid_months = [str(record["statement_end_date"])[:7] for record in records if record["status"] == "valid" and record.get("statement_end_date")]
    duplicate_months = sorted({month for month in valid_months if valid_months.count(month) > 1})
    state["monthly_continuity"] = {"complete": not (set(expected_months) - set(valid_months)) and not duplicate_months and all(record["confirmed"] for record in manual_records), "missing_month_count": len(set(expected_months) - set(valid_months)), "duplicate_month_count": len(duplicate_months), "missing_months": missing_statement_months(records)}
    state["diagnostics"] = {"header_found": sum(int(record.get("header_found", 0)) for record in records), "nearby_date_range_found": sum(int(record.get("nearby_date_range_found", 0)) for record in records), "nearby_account_marker_found": sum(int(record.get("nearby_account_marker_found", 0)) for record in records), "account_summary_found": sum(int(record.get("account_summary_found", 0)) for record in records), "balance_rows_found": sum(int(record.get("balance_rows_found", 0)) for record in records), "accounting_equation_passed": sum(int(record.get("accounting_equation_passed", 0)) for record in records)}
    return state


def scan_can_import(state: dict[str, Any], detected_count: int) -> bool:
    return bool(
        state.get("scan_status") == "completed"
        and int(state.get("processed_count", 0)) == int(detected_count)
        and int(state.get("ready", 0)) > 0
        and bool(state.get("monthly_continuity", {}).get("complete"))
    )


def clear_scan_preview(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DELETE FROM statement_scan_preview")
        conn.execute("DELETE FROM statement_scan_jobs WHERE job_id = 'local_statements'")
        conn.commit()


def _save_scan_record(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO statement_scan_preview (source_hash, scan_status, statement_start_date, statement_end_date, portfolio_value, invested_value, cash_value, cost_basis, contribution_amount, withdrawal_amount, validation_status, warning, header_found, nearby_date_range_found, nearby_account_marker_found, account_summary_found, balance_rows_found, accounting_equation_passed, parser_version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_hash) DO UPDATE SET scan_status=excluded.scan_status, statement_start_date=excluded.statement_start_date, statement_end_date=excluded.statement_end_date, portfolio_value=excluded.portfolio_value, invested_value=excluded.invested_value, cash_value=excluded.cash_value, cost_basis=excluded.cost_basis, contribution_amount=excluded.contribution_amount, withdrawal_amount=excluded.withdrawal_amount, validation_status=excluded.validation_status, warning=excluded.warning, header_found=excluded.header_found, nearby_date_range_found=excluded.nearby_date_range_found, nearby_account_marker_found=excluded.nearby_account_marker_found, account_summary_found=excluded.account_summary_found, balance_rows_found=excluded.balance_rows_found, accounting_equation_passed=excluded.accounting_equation_passed, parser_version=excluded.parser_version, updated_at=excluded.updated_at",
        (record["source_hash"], str(record.get("status") or "unparseable"), record.get("statement_start_date"), record.get("statement_end_date"), record.get("portfolio_value"), record.get("invested_value"), record.get("cash_value"), record.get("cost_basis"), record.get("contribution_amount"), record.get("withdrawal_amount"), record["status"], str(record.get("warning") or ""), int(bool(record.get("diagnostics", {}).get("verified_header_found"))), int(bool(record.get("diagnostics", {}).get("nearby_date_range_found"))), int(bool(record.get("diagnostics", {}).get("nearby_account_marker_found"))), int(bool(record.get("diagnostics", {}).get("verified_account_summary_found"))), int(bool(record.get("diagnostics", {}).get("balance_rows_found"))), int(bool(record.get("diagnostics", {}).get("accounting_equation_passed"))), PARSER_VERSION, _utc_now()),
    )


def reclassify_scan_preview(db_path: Path = STATEMENT_HISTORY_DB_PATH) -> None:
    """Apply durable cross-record date/value checks without exposing source identifiers."""
    prepare_history_database(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT source_hash, scan_status, statement_end_date, portfolio_value, validation_status, warning FROM statement_scan_preview ORDER BY source_hash").fetchall()
        imported = {str(row[0]): float(row[1]) for row in conn.execute("SELECT history_date, portfolio_value FROM statement_portfolio_history")}
        updates: dict[str, tuple[str, str]] = {}
        candidates: dict[str, list[tuple[str, float]]] = {}
        for source_hash, scan_status, statement_end_date, portfolio_value, validation_status, warning in rows:
            base_status = str(validation_status) if str(scan_status) == "processed" else str(scan_status or validation_status)
            if base_status not in {"valid", "processed"}:
                updates[str(source_hash)] = (base_status, str(warning or ""))
                continue
            if not statement_end_date or portfolio_value is None:
                updates[str(source_hash)] = ("needs_review", "Statement ending date or value is missing.")
                continue
            date_key = str(statement_end_date)
            value = float(portfolio_value)
            if date_key in imported:
                if abs(imported[date_key] - value) <= 0.01:
                    updates[str(source_hash)] = ("duplicate", "A matching statement date and value is already represented.")
                else:
                    updates[str(source_hash)] = ("needs_review", "Multiple statements contain different values for this ending date.")
                continue
            candidates.setdefault(date_key, []).append((str(source_hash), value))
        for date_key, items in candidates.items():
            values = {round(value, 2) for _, value in items}
            if len(values) > 1:
                for source_hash, _value in items:
                    updates[source_hash] = ("needs_review", "Multiple statements contain different values for this ending date.")
            else:
                canonical_hash = min(source_hash for source_hash, _value in items)
                for source_hash, _value in items:
                    updates[source_hash] = ("valid", "Ready to import.") if source_hash == canonical_hash else ("duplicate_statement", "A matching statement date and value is already represented.")
        for source_hash, (status, warning) in updates.items():
            conn.execute("UPDATE statement_scan_preview SET validation_status=?, warning=?, updated_at=? WHERE source_hash=?", (status, warning, _utc_now(), source_hash))
        conn.commit()


def run_statement_scan(
    source_dir: Path = STATEMENTS_DIR,
    *,
    db_path: Path = STATEMENT_HISTORY_DB_PATH,
    restart: bool = False,
    on_document_started: Any = None,
    on_document_complete: Any = None,
) -> dict[str, Any]:
    """Sequentially scan local PDFs and persist each preview before continuing."""
    prepare_history_database(db_path)
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if restart:
        clear_scan_preview(db_path)
    unique_paths: dict[str, tuple[int, Path]] = {}
    for sequence, pdf_path in enumerate(pdf_paths, start=1):
        source_hash = _hash_bytes(pdf_path.read_bytes())
        unique_paths.setdefault(source_hash, (sequence, pdf_path))
    detected_pdf_count = len(pdf_paths)
    unique_pdf_count = len(unique_paths)
    duplicate_file_count = detected_pdf_count - unique_pdf_count
    with closing(sqlite3.connect(db_path)) as conn:
        existing_hashes = {str(row[0]) for row in conn.execute("SELECT source_hash FROM statement_scan_preview")}
        existing_hashes.intersection_update(unique_paths)
        processed_unique_count = len(existing_hashes)
        conn.execute(
            "INSERT INTO statement_scan_jobs (job_id, total_count, processed_count, detected_pdf_count, unique_pdf_count, processed_unique_count, duplicate_file_count, scan_status, parser_version, updated_at) VALUES ('local_statements', ?, ?, ?, ?, ?, ?, 'running', ?, ?) ON CONFLICT(job_id) DO UPDATE SET total_count=excluded.total_count, processed_count=excluded.processed_count, detected_pdf_count=excluded.detected_pdf_count, unique_pdf_count=excluded.unique_pdf_count, processed_unique_count=excluded.processed_unique_count, duplicate_file_count=excluded.duplicate_file_count, scan_status='running', parser_version=excluded.parser_version, updated_at=excluded.updated_at",
            (detected_pdf_count, processed_unique_count + duplicate_file_count, detected_pdf_count, unique_pdf_count, processed_unique_count, duplicate_file_count, PARSER_VERSION, _utc_now()),
        )
        conn.commit()
    try:
        for source_hash, (sequence, pdf_path) in unique_paths.items():
            if source_hash in existing_hashes:
                continue
            if callable(on_document_started):
                on_document_started(sequence, detected_pdf_count)
            try:
                record = _parse_statement_document(pdf_path)
            except Exception:
                record = {"status": "unparseable", "warning": "Statement could not be parsed locally."}
            record["source_hash"] = source_hash
            with closing(sqlite3.connect(db_path)) as conn:
                _save_scan_record(conn, record)
                existing_hashes.add(source_hash)
                processed_unique_count = len(existing_hashes)
                conn.execute("UPDATE statement_scan_jobs SET processed_count=?, processed_unique_count=?, updated_at=? WHERE job_id='local_statements'", (processed_unique_count + duplicate_file_count, processed_unique_count, _utc_now()))
                conn.commit()
            if callable(on_document_complete):
                on_document_complete(sequence, detected_pdf_count, record)
        with closing(sqlite3.connect(db_path)) as conn:
            processed_unique_count = len(existing_hashes)
            conn.execute("UPDATE statement_scan_jobs SET processed_count=?, processed_unique_count=?, scan_status='completed', updated_at=? WHERE job_id='local_statements'", (processed_unique_count + duplicate_file_count, processed_unique_count, _utc_now()))
            conn.commit()
    except BaseException:
        with closing(sqlite3.connect(db_path)) as conn:
            processed_unique_count = len(existing_hashes)
            conn.execute("UPDATE statement_scan_jobs SET processed_count=?, processed_unique_count=?, scan_status='interrupted', updated_at=? WHERE job_id='local_statements'", (processed_unique_count + duplicate_file_count, processed_unique_count, _utc_now()))
            conn.commit()
        raise
    return scan_state(db_path)


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
                if status in {"duplicate", "duplicate_statement"}: result["duplicates"] += 1; continue
                if status == "unparseable": result["unparseable"] += 1; continue
                if status != "valid": result["needs_review"] += 1; continue
                if record.get("source_type") == "manual_statement_review" and not record.get("confirmed"):
                    result["needs_review"] += 1
                    continue
                if record.get("diagnostics") and not record["diagnostics"].get("accounting_equation_passed", False):
                    result["needs_review"] += 1
                    continue
                try:
                    conn.execute("SAVEPOINT statement_record")
                    cursor = conn.execute("INSERT INTO statement_imports (source_hash, statement_start_date, statement_end_date, imported_at, parser_version, status, warning_count) VALUES (?, ?, ?, ?, ?, ?, ?)", (record["source_hash"], record.get("statement_start_date"), record["statement_end_date"], datetime.now(timezone.utc).isoformat(timespec="seconds"), PARSER_VERSION, "imported", 0))
                    conn.execute("INSERT INTO statement_portfolio_history (import_id, history_date, portfolio_value, invested_value, cash_value, cost_basis, contribution_amount, withdrawal_amount, source_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (cursor.lastrowid, record["statement_end_date"], record["portfolio_value"], record.get("invested_value"), record.get("cash_value"), record.get("cost_basis"), record.get("contribution_amount"), record.get("withdrawal_amount"), record.get("source_type", "stash_statement")))
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

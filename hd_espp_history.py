"""Local-only HD/ESPP statement parsing and transaction history persistence.

This module deliberately does not update holdings, portfolio history, projections,
or contribution assumptions.  Decimal values are stored as text so fractional
shares and statement prices survive a SQLite round trip without binary rounding.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pdfplumber


PROJECT_ROOT = Path(__file__).resolve().parent
HD_STATEMENTS_DIR = PROJECT_ROOT / "Statements" / "HD"
HD_HISTORY_DB_PATH = PROJECT_ROOT / "data" / "stash_statement_history.sqlite"
PARSER_VERSION = "hd-espp-v1"
TYPES = {"ESPP_PURCHASE", "DIVIDEND", "DIVIDEND_REINVESTMENT", "TRANSFER_OUT", "TRANSFER_IN", "SALE", "ADJUSTMENT"}
DATE_WORD = r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
MONEY = r"\(?\d+(?:[ ,]\d{3})*(?:\.\d+)?\)?"


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = str(value).replace("USD", "").replace("$", "").replace(",", "").replace(" ", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    try:
        result = Decimal(cleaned.strip("()"))
    except InvalidOperation:
        return None
    return -result if negative else result


def _date(value: str) -> date | None:
    cleaned = re.sub(r"\s+", " ", value.strip().replace(",", ""))
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%m/%d/%y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class HDTransaction:
    transaction_date: date
    transaction_type: str
    source: str
    source_account_type: str
    shares: Decimal | None = None
    amount: Decimal | None = None
    purchase_price: Decimal | None = None
    fair_market_value: Decimal | None = None
    fees: Decimal | None = None
    taxes: Decimal | None = None
    cost_basis: Decimal | None = None
    acquisition_date: date | None = None
    source_location: str | None = None
    destination_location: str | None = None
    source_document: str | None = None
    source_period_start: date | None = None
    source_period_end: date | None = None
    notes: str = ""
    ambiguous: bool = False
    ticker: str = "HD"
    transaction_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.transaction_type not in TYPES:
            raise ValueError(f"Unsupported HD transaction type: {self.transaction_type}")
        self.transaction_fingerprint = transaction_fingerprint(self)


def transaction_fingerprint(row: HDTransaction) -> str:
    """Stable economic identity; filenames and statement periods are excluded."""
    parts = (
        row.ticker.upper(), row.source.lower(), row.transaction_type,
        row.transaction_date.isoformat(), str(row.shares or ""), str(row.amount or ""),
        str(row.purchase_price or ""), row.acquisition_date.isoformat() if row.acquisition_date else "",
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def detect_document_family(text: str) -> str:
    lowered = text.lower()
    if "employee plan statement" in lowered and "home depot" in lowered and "espp" in lowered:
        return "computershare_legacy"
    if "purchase activity statement" in lowered and ("equateplus" in lowered or "employee stock purchase plan" in lowered or "espp-purchase" in lowered):
        return "computershare_equateplus"
    if "merrill" in lowered and ("home depot inc" in lowered or re.search(r"\bhd\b", lowered)):
        return "merrill_hd"
    return "unknown"


def _period(text: str) -> tuple[date | None, date | None]:
    match = re.search(rf"({DATE_WORD}|[A-Z][a-z]+ \d{{1,2}},? \d{{4}})\s*[-–]\s*({DATE_WORD}|[A-Z][a-z]+ \d{{1,2}},? \d{{4}})", text)
    return (_date(match.group(1)), _date(match.group(2))) if match else (None, None)


def _legacy(text: str, document: str) -> list[HDTransaction]:
    rows: list[HDTransaction] = []
    start, end = _period(text)
    purchase = re.compile(rf"({DATE_WORD}) Purchase ({MONEY}) ({MONEY}) ({DATE_WORD}) ({MONEY}) ({DATE_WORD}) ({MONEY}) ({MONEY}) ({MONEY})", re.I)
    drip = re.compile(rf"({DATE_WORD}) Dividend Reinvestment ({MONEY}) ({MONEY}) ({MONEY}) ({MONEY}) ({MONEY})", re.I)
    transfer = re.compile(rf"({DATE_WORD}) (?:Plan Transfer Confirm|Transfer) (-?{MONEY})(?: ({MONEY}))?", re.I)
    for match in purchase.finditer(text):
        rows.append(HDTransaction(_date(match[1]), "ESPP_PURCHASE", "Computershare", "ESPP", shares=_decimal(match[9]), amount=_decimal(match[2]), fair_market_value=_decimal(match[7]), purchase_price=_decimal(match[8]), acquisition_date=_date(match[1]), source_location="Computershare", source_document=document, source_period_start=start, source_period_end=end, notes=f"grant_date={_date(match[4])}; grant_fmv={_decimal(match[5])}"))
    for match in drip.finditer(text):
        rows.append(HDTransaction(_date(match[1]), "DIVIDEND_REINVESTMENT", "Computershare", "ESPP", shares=_decimal(match[6]), amount=_decimal(match[4]), purchase_price=_decimal(match[5]), fees=_decimal(match[3]), source_location="Computershare", source_document=document, source_period_start=start, source_period_end=end, notes=f"gross_dividend={_decimal(match[2])}"))
    for match in transfer.finditer(text):
        shares = abs(_decimal(match[2]) or Decimal())
        rows.append(HDTransaction(_date(match[1]), "TRANSFER_OUT", "Computershare", "ESPP", shares=shares, cost_basis=_decimal(match[3]), source_location="Computershare", destination_location="Merrill", source_document=document, source_period_start=start, source_period_end=end))
    return rows


def _equateplus(text: str, document: str) -> list[HDTransaction]:
    rows: list[HDTransaction] = []
    start, end = _period(text)
    purchase_text, _, drip_text = text.partition("Dividend Reinvestments")
    for line in purchase_text.splitlines():
        dates = re.findall(DATE_WORD, line, re.I)
        tail = line
        for found_date in dates:
            tail = tail.replace(found_date, "", 1)
        tail = re.sub(r"(?<=\d) (?=\d{3}\.)", "", tail)
        values = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", tail)
        if len(dates) >= 3 and len(values) >= 7:
            rows.append(HDTransaction(_date(dates[2]), "ESPP_PURCHASE", "EquatePlus/Computershare", "ESPP", shares=_decimal(values[-2]), amount=_decimal(values[0]), fair_market_value=_decimal(values[-4]), purchase_price=_decimal(values[-3]), acquisition_date=_date(dates[2]), source_location="EquatePlus", source_document=document, source_period_start=start, source_period_end=end))
    for line in drip_text.splitlines():
        dates = re.findall(DATE_WORD, line, re.I)
        if not dates:
            continue
        tail = line[line.find(dates[0]) + len(dates[0]):]
        tail = re.sub(r"(?<=\d) (?=\d{3}\.)", "", tail)
        values = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", tail)
        if len(values) >= 8 and "USD" in line:
            rows.append(HDTransaction(_date(dates[0]), "DIVIDEND_REINVESTMENT", "EquatePlus/Computershare", "ESPP", shares=_decimal(values[6]), amount=_decimal(values[4]), fees=_decimal(values[3]), taxes=_decimal(values[2]), purchase_price=_decimal(values[5]), source_location="EquatePlus", source_document=document, source_period_start=start, source_period_end=end, notes=f"gross_dividend={_decimal(values[1])}"))
    return rows


def _merrill(text: str, document: str) -> list[HDTransaction]:
    rows: list[HDTransaction] = []
    start, end = _period(text)
    year = (end or start or date.today()).year
    def merrill_date(short: str) -> date:
        month, day = map(int, short.split("/")); return date(year, month, day)
    for match in re.finditer(rf"(\d{{2}}/\d{{2}}) HOME DEPOT INC \* Dividend ({MONEY})", text, re.I):
        rows.append(HDTransaction(merrill_date(match[1]), "DIVIDEND", "Merrill", "Brokerage", amount=_decimal(match[2]), source_location="Merrill", source_document=document, source_period_start=start, source_period_end=end))
    for match in re.finditer(rf"(\d{{2}}/\d{{2}}) HOME DEPOT INC (Security Transfer In|Transfer / Adjustment) ({MONEY}) ({MONEY})", text, re.I):
        kind = "TRANSFER_IN" if "security" in match[2].lower() else "ADJUSTMENT"
        rows.append(HDTransaction(merrill_date(match[1]), kind, "Merrill", "Brokerage", shares=_decimal(match[3]), cost_basis=_decimal(match[4]), source_location="External" if kind == "TRANSFER_IN" else "Restricted share plan", destination_location="Merrill", source_document=document, source_period_start=start, source_period_end=end, notes=match[2]))
    for match in re.finditer(rf"(\d{{2}}/\d{{2}}) HOME DEPOT INC Sale -({MONEY}) ({MONEY})", text, re.I):
        shares, amount = _decimal(match[2]), _decimal(match[3])
        rows.append(HDTransaction(merrill_date(match[1]), "SALE", "Merrill", "Brokerage", shares=shares, amount=amount, purchase_price=(amount / shares if shares else None), source_location="Merrill", source_document=document, source_period_start=start, source_period_end=end))
    lot_re = re.compile(rf"HOME DEPOT INC ({MONEY}) (\d{{2}}/\d{{2}}/\d{{2}}|N/A) (\d{{2}}/\d{{2}}/\d{{2}}) ({MONEY}) ({MONEY}|N/A)", re.I)
    for match in lot_re.finditer(text):
        if match[2].upper() == "N/A":
            continue
        sold = _date(match[3]); acquired = _date(match[2])
        rows.append(HDTransaction(sold, "SALE", "Merrill", "Realized lot", shares=_decimal(match[1]), amount=_decimal(match[4]), cost_basis=_decimal(match[5]), acquisition_date=acquired, source_location="Merrill", source_document=document, source_period_start=start, source_period_end=end, notes="realized_lot"))
    return rows


def parse_hd_statement_text(text: str, source_document: str = "sanitized.txt") -> tuple[str, list[HDTransaction], list[str]]:
    family = detect_document_family(text)
    parser = {"computershare_legacy": _legacy, "computershare_equateplus": _equateplus, "merrill_hd": _merrill}.get(family)
    if not parser:
        return family, [], ["Document family could not be identified"]
    rows = parser(text, Path(source_document).name)
    issues = [] if rows else [f"{family} document contained no confidently parsed HD transactions"]
    return family, rows, issues


SCHEMA = """
CREATE TABLE IF NOT EXISTS hd_espp_documents (source_document TEXT PRIMARY KEY, document_hash TEXT NOT NULL, document_family TEXT NOT NULL, period_start TEXT, period_end TEXT, parser_version TEXT NOT NULL, imported_at TEXT NOT NULL, issue_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hd_espp_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, transaction_date TEXT NOT NULL, transaction_type TEXT NOT NULL, source TEXT NOT NULL, source_account_type TEXT NOT NULL, shares TEXT, amount TEXT, purchase_price TEXT, fair_market_value TEXT, fees TEXT, taxes TEXT, cost_basis TEXT, acquisition_date TEXT, source_location TEXT, destination_location TEXT, source_document TEXT NOT NULL, source_period_start TEXT, source_period_end TEXT, transaction_fingerprint TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '', ambiguous INTEGER NOT NULL DEFAULT 0, transfer_match_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hd_espp_import_issues (id INTEGER PRIMARY KEY AUTOINCREMENT, source_document TEXT NOT NULL, issue TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_hd_espp_date ON hd_espp_transactions(transaction_date);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path); connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA); return connection


def match_custody_transfers(rows: Iterable[HDTransaction], window_days: int = 7, tolerance: Decimal = Decimal("0.000001")) -> list[tuple[str, str]]:
    outgoing = [row for row in rows if row.transaction_type == "TRANSFER_OUT"]
    incoming = [row for row in rows if row.transaction_type == "TRANSFER_IN"]
    matches = []
    for out in outgoing:
        candidate = next((item for item in incoming if abs((item.shares or Decimal()) - (out.shares or Decimal())) <= tolerance and timedelta(0) <= item.transaction_date - out.transaction_date <= timedelta(days=window_days)), None)
        if candidate:
            matches.append((out.transaction_fingerprint, candidate.transaction_fingerprint)); incoming.remove(candidate)
    return matches


class HDESPPHistoryService:
    def __init__(self, source_dir: Path = HD_STATEMENTS_DIR, db_path: Path = HD_HISTORY_DB_PATH):
        self.source_dir, self.db_path = Path(source_dir), Path(db_path)

    def import_statements(self) -> dict[str, int]:
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"HD statement folder does not exist: {self.source_dir}")
        parsed: list[HDTransaction] = []; documents = issues_total = 0
        with closing(_connect(self.db_path)) as connection, connection:
            for path in sorted(self.source_dir.glob("*.pdf")):
                with pdfplumber.open(path) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                family, rows, issues = parse_hd_statement_text(text, path.name)
                start, end = _period(text); now = datetime.now(timezone.utc).isoformat()
                connection.execute("INSERT OR REPLACE INTO hd_espp_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (path.name, hashlib.sha256(path.read_bytes()).hexdigest(), family, start.isoformat() if start else None, end.isoformat() if end else None, PARSER_VERSION, now, len(issues)))
                connection.execute("DELETE FROM hd_espp_import_issues WHERE source_document=?", (path.name,))
                connection.executemany("INSERT INTO hd_espp_import_issues (source_document, issue, created_at) VALUES (?, ?, ?)", [(path.name, issue, now) for issue in issues])
                parsed.extend(rows); documents += 1; issues_total += len(issues)
            inserted = 0
            for row in parsed:
                values = [row.ticker, row.transaction_date.isoformat(), row.transaction_type, row.source, row.source_account_type, row.shares, row.amount, row.purchase_price, row.fair_market_value, row.fees, row.taxes, row.cost_basis, row.acquisition_date.isoformat() if row.acquisition_date else None, row.source_location, row.destination_location, row.source_document, row.source_period_start.isoformat() if row.source_period_start else None, row.source_period_end.isoformat() if row.source_period_end else None, row.transaction_fingerprint, row.notes, int(row.ambiguous), None, datetime.now(timezone.utc).isoformat()]
                cursor = connection.execute(f"INSERT OR IGNORE INTO hd_espp_transactions ({','.join(['ticker','transaction_date','transaction_type','source','source_account_type','shares','amount','purchase_price','fair_market_value','fees','taxes','cost_basis','acquisition_date','source_location','destination_location','source_document','source_period_start','source_period_end','transaction_fingerprint','notes','ambiguous','transfer_match_id','created_at'])}) VALUES ({','.join('?' for _ in values)})", [str(v) if isinstance(v, Decimal) else v for v in values])
                inserted += cursor.rowcount
            stored = [HDTransaction(transaction_date=_date(row["transaction_date"]), transaction_type=row["transaction_type"], source=row["source"], source_account_type=row["source_account_type"], shares=_decimal(row["shares"]), amount=_decimal(row["amount"]), purchase_price=_decimal(row["purchase_price"]), acquisition_date=_date(row["acquisition_date"]) if row["acquisition_date"] else None) for row in connection.execute("SELECT * FROM hd_espp_transactions")]
            for left, right in match_custody_transfers(stored):
                match_id = hashlib.sha256((left + right).encode()).hexdigest()[:20]
                connection.execute("UPDATE hd_espp_transactions SET transfer_match_id=? WHERE transaction_fingerprint IN (?,?)", (match_id, left, right))
        return {"documents": documents, "parsed": len(parsed), "inserted": inserted, "issues": issues_total}

    def state(self) -> dict[str, Any]:
        with closing(_connect(self.db_path)) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM hd_espp_transactions ORDER BY transaction_date DESC, id DESC")]
            documents = connection.execute("SELECT COUNT(*) FROM hd_espp_documents").fetchone()[0]
            issues = connection.execute("SELECT COUNT(*) FROM hd_espp_import_issues").fetchone()[0]
        def total(kind: str, field: str) -> Decimal:
            return sum((_decimal(row[field]) or Decimal()) for row in rows if row["transaction_type"] == kind and row["source_account_type"] != "Realized lot")
        matched = {row["transfer_match_id"] for row in rows if row.get("transfer_match_id")}
        economic_shares = sum((_decimal(row["shares"]) or Decimal()) * Decimal({"ESPP_PURCHASE": 1, "DIVIDEND_REINVESTMENT": 1, "SALE": -1, "ADJUSTMENT": 1}.get(row["transaction_type"], 0)) for row in rows if row["source_account_type"] != "Realized lot")
        return {"transactions": rows, "summary": {"total_espp_contributions": total("ESPP_PURCHASE", "amount"), "total_espp_shares": total("ESPP_PURCHASE", "shares"), "total_drip_shares": total("DIVIDEND_REINVESTMENT", "shares"), "total_dividends_reinvested": total("DIVIDEND_REINVESTMENT", "amount"), "total_shares_transferred": total("TRANSFER_OUT", "shares"), "total_shares_sold": total("SALE", "shares"), "documents": documents, "transactions": len(rows), "issues": issues, "matched_transfers": len(matched), "economic_shares": economic_shares}}


__all__ = ["HDESPPHistoryService", "HDTransaction", "detect_document_family", "match_custody_transfers", "parse_hd_statement_text", "transaction_fingerprint"]

"""Local-only HD/ESPP statement parsing and transaction history persistence.

This module deliberately does not update holdings, portfolio history, projections,
or contribution assumptions.  Decimal values are stored as text so fractional
shares and statement prices survive a SQLite round trip without binary rounding.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
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
PARSER_VERSION = "hd-espp-v3"
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
    transfer_value: Decimal | None = None
    lot_sequence: int | None = None
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


@dataclass
class HDTransferLot:
    parent_transfer_fingerprint: str
    transfer_date: date
    acquisition_date: date
    transferred_shares: Decimal
    source_type: str
    reported_value: Decimal | None
    source_document: str
    sequence: int
    provenance: str = "Computershare transfer detail"
    ambiguous: bool = False
    transfer_lot_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        identity = "|".join((
            self.parent_transfer_fingerprint, self.transfer_date.isoformat(),
            self.acquisition_date.isoformat(), str(self.transferred_shares), str(self.sequence),
        ))
        self.transfer_lot_fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()


def transaction_fingerprint(row: HDTransaction) -> str:
    """Stable economic identity; filenames and statement periods are excluded."""
    parts = (
        row.ticker.upper(), row.source.lower(), row.transaction_type,
        row.transaction_date.isoformat(), str(row.shares or ""), str(row.amount or ""),
        str(row.purchase_price or ""), row.acquisition_date.isoformat() if row.acquisition_date else "",
        str(row.lot_sequence or "") if row.source_account_type == "Realized lot" else "",
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
    transfer = re.compile(rf"({DATE_WORD}) (Plan Transfer Confirm|Transfer) (-?{MONEY})(?: ({MONEY}))?", re.I)
    for match in purchase.finditer(text):
        rows.append(HDTransaction(_date(match[1]), "ESPP_PURCHASE", "Computershare", "ESPP", shares=_decimal(match[9]), amount=_decimal(match[2]), fair_market_value=_decimal(match[7]), purchase_price=_decimal(match[8]), acquisition_date=_date(match[1]), source_location="Computershare", source_document=document, source_period_start=start, source_period_end=end, notes=f"grant_date={_date(match[4])}; grant_fmv={_decimal(match[5])}"))
    for line in text.splitlines():
        if "Dividend Reinvestment" not in line:
            continue
        match = re.match(rf"^\s*({DATE_WORD})\s+Dividend Reinvestment\s+(.*)$", line, re.I)
        if match is None:
            continue
        values = re.findall(r"[-+]?\d+(?:[ ,]\d{3})*(?:\.\d+)?", match.group(2))
        if len(values) < 5:
            continue
        normalized = [_decimal(value) for value in values]
        if len(normalized) >= 6:
            gross, fee, net, purchase_price, shares, total_shares = normalized[:6]
        elif len(normalized) == 5:
            gross, net, purchase_price, shares, total_shares = normalized[:5]
            fee = Decimal("0")
        else:
            continue
        if gross is None or net is None or purchase_price is None or shares is None:
            continue
        rows.append(HDTransaction(_date(match.group(1)), "DIVIDEND_REINVESTMENT", "Computershare", "ESPP", shares=shares, amount=net, purchase_price=purchase_price, fees=fee or Decimal("0"), taxes=Decimal("0"), source_location="Computershare", source_document=document, source_period_start=start, source_period_end=end, notes=f"gross_dividend={gross}"))
    for match in transfer.finditer(text):
        shares = abs(_decimal(match[3]) or Decimal())
        transfer_value = _decimal(match[4]) if match[2].lower() == "transfer" else None
        rows.append(HDTransaction(_date(match[1]), "TRANSFER_OUT", "Computershare", "ESPP", shares=shares, transfer_value=transfer_value, source_location="Computershare", destination_location="Merrill", source_document=document, source_period_start=start, source_period_end=end))
    return rows


def parse_hd_transfer_lots(text: str, rows: Iterable[HDTransaction], source_document: str = "sanitized.txt") -> list[HDTransferLot]:
    """Parse Computershare's detail rows beneath an aggregate Transfer line."""
    transfer = next((row for row in rows if row.transaction_type == "TRANSFER_OUT" and row.transfer_value is not None), None)
    if transfer is None:
        return []
    detail = re.compile(rf"(?im)^\s*({DATE_WORD})\s+-({MONEY})\s+({MONEY})\s+(Covered|Noncovered|Unknown)\s*$", re.I)
    return [
        HDTransferLot(
            parent_transfer_fingerprint=transfer.transaction_fingerprint,
            transfer_date=transfer.transaction_date,
            acquisition_date=_date(match[1]),
            transferred_shares=abs(_decimal(match[2]) or Decimal()),
            source_type=match[4].upper(),
            reported_value=_decimal(match[3]),
            source_document=Path(source_document).name,
            sequence=sequence,
        )
        for sequence, match in enumerate(detail.finditer(text), start=1)
    ]


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
        if "Dividend Reinvestments" in line or "Record Date" in line or "Purchase Date" in line or "Gross Dividend" in line or "ESPP-PURCHASE at" in line:
            continue
        date_match = re.search(rf"({DATE_WORD})\s+", line, re.I)
        if date_match is None:
            continue
        remainder = line[date_match.end():]
        tokens = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", remainder)
        if len(tokens) < 7:
            continue
        # Real EquatePlus table layout after the date: entitled quantity, gross dividend, taxes, fees, net reinvested, purchase price, purchased shares, residual amount.
        entitled, gross, taxes, fees, net, price, shares, *rest = tokens[:8]
        gross_value = _decimal(gross)
        taxes_value = _decimal(taxes)
        fees_value = _decimal(fees)
        net_value = _decimal(net)
        price_value = _decimal(price)
        shares_value = _decimal(shares)
        if gross_value is None or net_value is None or price_value is None or shares_value is None:
            continue
        rows.append(HDTransaction(_date(date_match.group(1)), "DIVIDEND_REINVESTMENT", "EquatePlus/Computershare", "ESPP", shares=shares_value, amount=net_value, purchase_price=price_value, fees=fees_value or Decimal("0"), taxes=taxes_value or Decimal("0"), source_location="EquatePlus", source_document=document, source_period_start=start, source_period_end=end, notes=f"gross_dividend={gross_value}"))
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
        rows.append(HDTransaction(merrill_date(match[1]), kind, "Merrill", "Brokerage", shares=_decimal(match[3]), transfer_value=_decimal(match[4]), source_location="External" if kind == "TRANSFER_IN" else "Restricted share plan", destination_location="Merrill", source_document=document, source_period_start=start, source_period_end=end, notes=match[2]))
    for match in re.finditer(rf"(\d{{2}}/\d{{2}}) HOME DEPOT INC Sale -({MONEY}) ({MONEY})", text, re.I):
        shares, amount = _decimal(match[2]), _decimal(match[3])
        rows.append(HDTransaction(merrill_date(match[1]), "SALE", "Merrill", "Brokerage", shares=shares, amount=amount, purchase_price=(amount / shares if shares else None), source_location="Merrill", source_document=document, source_period_start=start, source_period_end=end))
    for sequence, match in enumerate(_merrill_lot_matches(text), start=1):
        sold = _date(match[3]); acquired = None if match[2].upper() == "N/A" else _date(match[2])
        basis = None if match[5].upper() == "N/A" else _decimal(match[5])
        notes = "realized_lot" if acquired and basis is not None else "realized_lot; acquisition_date_and_cost_basis_unavailable"
        rows.append(HDTransaction(sold, "SALE", "Merrill", "Realized lot", shares=_decimal(match[1]), amount=_decimal(match[4]), cost_basis=basis, acquisition_date=acquired, lot_sequence=sequence, source_location="Merrill", source_document=document, source_period_start=start, source_period_end=end, notes=notes, ambiguous=acquired is None or basis is None))
    return rows


def _merrill_lot_matches(text: str) -> list[re.Match[str]]:
    shares = r"(?:\d+(?:\.\d+)?|\.\d+)"
    pattern = re.compile(rf"(?im)^\s*(?:x\s+)?HOME DEPOT INC\s+({shares})\s+(\d{{2}}/\d{{2}}/\d{{2}}|N/A)\s+(\d{{2}}/\d{{2}}/\d{{2}})\s+({MONEY})\s+({MONEY}|N/A)(?:\s+(?:{MONEY}|N/A|\([^\r\n]+\)))?\s*$", re.I)
    return list(pattern.finditer(text))


def parse_hd_statement_text(text: str, source_document: str = "sanitized.txt") -> tuple[str, list[HDTransaction], list[str]]:
    family = detect_document_family(text)
    parser = {"computershare_legacy": _legacy, "computershare_equateplus": _equateplus, "merrill_hd": _merrill}.get(family)
    if not parser:
        return family, [], ["Document family could not be identified"]
    rows = parser(text, Path(source_document).name)
    issues = [] if rows else [f"{family} document contained no confidently parsed HD transactions"]
    issues.extend(_section_completeness_issues(family, text, rows))
    return family, rows, issues


def _section_completeness_issues(family: str, text: str, rows: list[HDTransaction]) -> list[str]:
    """Flag supported activity visibly present in text but absent from output."""
    lowered = text.lower()
    produced = {row.transaction_type for row in rows}
    checks: list[tuple[str, bool, set[str]]] = []
    if family == "computershare_legacy":
        checks = [
            ("Purchases", bool(re.search(r"(?im)^Purchases\b|\bPurchase\s+[\d,]+\.\d{2}\b", text)), {"ESPP_PURCHASE"}),
            ("Dividend Reinvestments", "dividend reinvestment" in lowered or bool(re.search(r"(?im)^Dividends\b", text)), {"DIVIDEND_REINVESTMENT"}),
            ("Transfers", bool(re.search(r"(?i)\b(?:plan transfer confirm|\d{1,2}\s+\w+\s+\d{4}\s+transfer\s+-)", text)), {"TRANSFER_OUT"}),
        ]
    elif family == "computershare_equateplus":
        checks = [
            ("Purchases", bool(re.search(r"(?im)^Purchases\s*$|Employee Stock Purchase Plan", text)), {"ESPP_PURCHASE"}),
            ("Dividend Reinvestments", "dividend reinvestments" in lowered, {"DIVIDEND_REINVESTMENT"}),
        ]
    elif family == "merrill_hd":
        checks = [
            ("Dividends", bool(re.search(r"(?i)HOME DEPOT INC \* Dividend", text)), {"DIVIDEND"}),
            ("Security Transfers In", bool(re.search(r"(?i)HOME DEPOT INC Security Transfer In", text)), {"TRANSFER_IN"}),
            ("Transfer / Adjustment", bool(re.search(r"(?i)HOME DEPOT INC Transfer / Adjustment", text)), {"ADJUSTMENT"}),
            ("Sales", bool(re.search(r"(?i)HOME DEPOT INC Sale -", text)), {"SALE"}),
            ("Realized Lots", bool(re.search(r"(?i)HOME DEPOT INC\s+\d+(?:\.\d+)?\s+(?:\d{2}/\d{2}/\d{2}|N/A)\s+\d{2}/\d{2}/\d{2}", text)), {"SALE"}),
        ]
    issues = []
    for label, detected, expected in checks:
        if label == "Realized Lots":
            recognized = len(_merrill_lot_matches(text))
            parsed = sum(row.source_account_type == "Realized lot" for row in rows)
            if recognized != parsed:
                issues.append(f"Recognized {recognized} HD realized-lot rows but parsed {parsed}; review skipped lot rows")
            continue
        if label == "Sales":
            accounted = any(row.transaction_type == "SALE" and row.source_account_type == "Brokerage" for row in rows)
        else:
            accounted = any(row.transaction_type in expected for row in rows)
        if detected and not accounted:
            issues.append(f"Detected supported {label} section/activity but parsed no corresponding transaction")
    for row in rows:
        if row.transaction_type != "DIVIDEND_REINVESTMENT":
            continue
        if row.transaction_date is None or row.amount is None or row.purchase_price is None or row.shares is None:
            issues.append(f"DRIP row on {row.transaction_date or 'unknown date'} missing core fields; review required")
            continue
        if row.purchase_price <= Decimal("0") or row.shares <= Decimal("0"):
            issues.append(f"DRIP row on {row.transaction_date} has non-positive purchase-price/share fields; review required")
        if row.purchase_price > Decimal("1000") and row.shares < Decimal("0.01"):
            issues.append(f"DRIP row on {row.transaction_date} has suspicious price/share inversion; review required")
        if row.shares > Decimal("100") and row.purchase_price < Decimal("1"):
            issues.append(f"DRIP row on {row.transaction_date} has suspicious share/price inversion; review required")
    return issues


SCHEMA = """
CREATE TABLE IF NOT EXISTS hd_espp_documents (source_document TEXT PRIMARY KEY, document_hash TEXT NOT NULL, document_family TEXT NOT NULL, period_start TEXT, period_end TEXT, parser_version TEXT NOT NULL, imported_at TEXT NOT NULL, issue_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hd_espp_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, transaction_date TEXT NOT NULL, transaction_type TEXT NOT NULL, source TEXT NOT NULL, source_account_type TEXT NOT NULL, shares TEXT, amount TEXT, purchase_price TEXT, fair_market_value TEXT, fees TEXT, taxes TEXT, cost_basis TEXT, transfer_value TEXT, lot_sequence INTEGER, acquisition_date TEXT, source_location TEXT, destination_location TEXT, source_document TEXT NOT NULL, source_period_start TEXT, source_period_end TEXT, transaction_fingerprint TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '', ambiguous INTEGER NOT NULL DEFAULT 0, transfer_match_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hd_espp_transfer_lots (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_transfer_fingerprint TEXT NOT NULL, transfer_date TEXT NOT NULL, acquisition_date TEXT NOT NULL, transferred_shares TEXT NOT NULL, source_type TEXT NOT NULL, reported_value TEXT, source_document TEXT NOT NULL, sequence INTEGER NOT NULL, provenance TEXT NOT NULL, ambiguous INTEGER NOT NULL DEFAULT 0, transfer_lot_fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hd_espp_import_issues (id INTEGER PRIMARY KEY AUTOINCREMENT, source_document TEXT NOT NULL, issue TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_hd_espp_date ON hd_espp_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_hd_espp_transfer_lot_parent ON hd_espp_transfer_lots(parent_transfer_fingerprint);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path); connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(hd_espp_transactions)")}
    for name, kind in (("transfer_value", "TEXT"), ("lot_sequence", "INTEGER")):
        if name not in columns:
            connection.execute(f"ALTER TABLE hd_espp_transactions ADD COLUMN {name} {kind}")
    return connection


def _transfer_provenance_ok(out: HDTransaction, incoming: HDTransaction) -> bool:
    out_source = (out.source_location or out.source).lower()
    out_destination = (out.destination_location or "").lower()
    in_source = (incoming.source_location or "").lower()
    in_destination = (incoming.destination_location or incoming.source).lower()
    return "computershare" in out_source and "merrill" in out_destination and incoming.source.lower() == "merrill" and "merrill" in in_destination and (not in_source or in_source in {"external", "computershare"})


def reconcile_custody_transfers(rows: Iterable[HDTransaction], window_days: int = 7, tolerance: Decimal = Decimal("0.000001")) -> tuple[list[tuple[str, str]], list[str]]:
    outgoing = [row for row in rows if row.transaction_type == "TRANSFER_OUT"]
    incoming = [row for row in rows if row.transaction_type == "TRANSFER_IN"]
    matches: list[tuple[str, str]] = []
    issues: list[str] = []
    for out in outgoing:
        candidates = [item for item in incoming if _transfer_provenance_ok(out, item) and abs((item.shares or Decimal()) - (out.shares or Decimal())) <= tolerance and timedelta(0) <= item.transaction_date - out.transaction_date <= timedelta(days=window_days)]
        if len(candidates) == 1:
            candidate = candidates[0]
            matches.append((out.transaction_fingerprint, candidate.transaction_fingerprint)); incoming.remove(candidate)
        elif len(candidates) > 1:
            basis_matches = [item for item in candidates if out.cost_basis is not None and item.cost_basis is not None and abs(out.cost_basis - item.cost_basis) <= Decimal("0.01")]
            if len(basis_matches) == 1:
                candidate = basis_matches[0]
                matches.append((out.transaction_fingerprint, candidate.transaction_fingerprint)); incoming.remove(candidate)
            else:
                issues.append(f"Ambiguous custody transfer on {out.transaction_date.isoformat()} for {out.shares} HD shares; left unmatched")
    return matches, issues


def match_custody_transfers(rows: Iterable[HDTransaction], window_days: int = 7, tolerance: Decimal = Decimal("0.000001")) -> list[tuple[str, str]]:
    return reconcile_custody_transfers(rows, window_days, tolerance)[0]


def transaction_record_type(row: HDTransaction | dict[str, Any]) -> str:
    value = lambda name: getattr(row, name, None) if isinstance(row, HDTransaction) else row.get(name)
    if value("source_account_type") == "Realized lot":
        return "Realized Lot"
    if value("transaction_type") in {"TRANSFER_IN", "TRANSFER_OUT"}:
        return "Custody Transfer"
    if value("transaction_type") == "ADJUSTMENT":
        return "Restricted Share Activity"
    if value("transaction_type") == "DIVIDEND_REINVESTMENT":
        return "Dividend Reinvestment"
    if value("source_account_type") == "ESPP":
        return "ESPP Transaction"
    return "Brokerage Transaction"


def economic_share_ownership(rows: Iterable[HDTransaction | dict[str, Any]]) -> Decimal:
    total = Decimal()
    effects = {"ESPP_PURCHASE": Decimal(1), "DIVIDEND_REINVESTMENT": Decimal(1), "SALE": Decimal(-1), "ADJUSTMENT": Decimal(1)}
    for row in rows:
        value = lambda name: getattr(row, name, None) if isinstance(row, HDTransaction) else row.get(name)
        if value("source_account_type") != "Realized lot":
            total += (_decimal(value("shares")) or Decimal()) * effects.get(value("transaction_type"), Decimal())
    return total


class HDESPPHistoryService:
    _import_lock = threading.Lock()

    def __init__(self, source_dir: Path = HD_STATEMENTS_DIR, db_path: Path = HD_HISTORY_DB_PATH):
        self.source_dir, self.db_path = Path(source_dir), Path(db_path)

    def import_statements(self) -> dict[str, int]:
        if not self._import_lock.acquire(blocking=False):
            raise RuntimeError("An HD statement import is already running")
        try:
            return self._rebuild_from_statements()
        finally:
            self._import_lock.release()

    def _rebuild_from_statements(self) -> dict[str, int]:
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"HD statement folder does not exist: {self.source_dir}")
        occurrences: list[HDTransaction] = []
        transfer_lot_occurrences: list[HDTransferLot] = []
        document_rows: list[tuple[Any, ...]] = []
        issue_rows: list[tuple[str, str]] = []
        now = datetime.now(timezone.utc).isoformat()
        paths = sorted(self.source_dir.glob("*.pdf"), key=lambda path: path.name.lower())
        for path in paths:
            with pdfplumber.open(path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            family, rows, issues = parse_hd_statement_text(text, path.name)
            parsed_transfer_lots = parse_hd_transfer_lots(text, rows, path.name)
            if re.search(r"(?im)^\s*\d{1,2}\s+\w+\s+\d{4}\s+-[\d.]+\s+[\d,]+\.\d{2}\s+(?:Covered|Noncovered|Unknown)\s*$", text) and not parsed_transfer_lots:
                issues.append("Detected Computershare transfer-lot detail but parsed no normalized transfer lots")
            start, end = _period(text)
            document_rows.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest(), family, start.isoformat() if start else None, end.isoformat() if end else None, PARSER_VERSION, now, len(issues)))
            issue_rows.extend((path.name, issue) for issue in issues)
            occurrences.extend(rows)
            transfer_lot_occurrences.extend(parsed_transfer_lots)

        canonical: dict[str, HDTransaction] = {}
        for row in occurrences:
            existing = canonical.get(row.transaction_fingerprint)
            if existing is None or self._canonical_rank(row) > self._canonical_rank(existing):
                canonical[row.transaction_fingerprint] = row
        canonical_rows = sorted(canonical.values(), key=lambda row: (row.transaction_date, row.transaction_fingerprint))
        canonical_transfer_lots = {row.transfer_lot_fingerprint: row for row in transfer_lot_occurrences}
        transfer_matches, transfer_issues = reconcile_custody_transfers(canonical_rows)
        issue_rows.extend(("Transfer reconciliation", issue) for issue in transfer_issues)
        match_ids: dict[str, str] = {}
        for left, right in transfer_matches:
            match_id = hashlib.sha256((left + right).encode()).hexdigest()[:20]
            match_ids[left] = match_ids[right] = match_id

        with closing(_connect(self.db_path)) as connection, connection:
            connection.execute("DELETE FROM hd_espp_import_issues")
            connection.execute("DELETE FROM hd_espp_transfer_lots")
            connection.execute("DELETE FROM hd_espp_transactions")
            connection.execute("DELETE FROM hd_espp_documents")
            connection.executemany("INSERT INTO hd_espp_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)", document_rows)
            connection.executemany("INSERT INTO hd_espp_import_issues (source_document, issue, created_at) VALUES (?, ?, ?)", [(document, issue, now) for document, issue in issue_rows])
            for row in canonical_rows:
                values = [row.ticker, row.transaction_date.isoformat(), row.transaction_type, row.source, row.source_account_type, row.shares, row.amount, row.purchase_price, row.fair_market_value, row.fees, row.taxes, row.cost_basis, row.transfer_value, row.lot_sequence, row.acquisition_date.isoformat() if row.acquisition_date else None, row.source_location, row.destination_location, row.source_document, row.source_period_start.isoformat() if row.source_period_start else None, row.source_period_end.isoformat() if row.source_period_end else None, row.transaction_fingerprint, row.notes, int(row.ambiguous), match_ids.get(row.transaction_fingerprint), now]
                connection.execute(f"INSERT INTO hd_espp_transactions ({','.join(['ticker','transaction_date','transaction_type','source','source_account_type','shares','amount','purchase_price','fair_market_value','fees','taxes','cost_basis','transfer_value','lot_sequence','acquisition_date','source_location','destination_location','source_document','source_period_start','source_period_end','transaction_fingerprint','notes','ambiguous','transfer_match_id','created_at'])}) VALUES ({','.join('?' for _ in values)})", [str(value) if isinstance(value, Decimal) else value for value in values])
            for row in sorted(canonical_transfer_lots.values(), key=lambda item: (item.transfer_date, item.sequence)):
                values = [row.parent_transfer_fingerprint, row.transfer_date.isoformat(), row.acquisition_date.isoformat(), row.transferred_shares, row.source_type, row.reported_value, row.source_document, row.sequence, row.provenance, int(row.ambiguous), row.transfer_lot_fingerprint, now]
                connection.execute("INSERT INTO hd_espp_transfer_lots (parent_transfer_fingerprint,transfer_date,acquisition_date,transferred_shares,source_type,reported_value,source_document,sequence,provenance,ambiguous,transfer_lot_fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [str(value) if isinstance(value, Decimal) else value for value in values])
        return {"documents": len(paths), "parsed": len(occurrences), "inserted": len(canonical_rows), "transfer_lots": len(canonical_transfer_lots), "issues": len(issue_rows)}

    @staticmethod
    def _canonical_rank(row: HDTransaction) -> tuple[int, date, str]:
        completeness = sum(value is not None and value != "" for value in (row.fair_market_value, row.fees, row.taxes, row.cost_basis, row.transfer_value, row.source_location, row.destination_location, row.notes))
        return completeness, row.source_period_end or date.min, row.source_document or ""

    def state(self) -> dict[str, Any]:
        with closing(_connect(self.db_path)) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM hd_espp_transactions ORDER BY transaction_date DESC, id DESC")]
            documents = connection.execute("SELECT COUNT(*) FROM hd_espp_documents").fetchone()[0]
            issues = connection.execute("SELECT COUNT(*) FROM hd_espp_import_issues").fetchone()[0]
            issue_records = [dict(row) for row in connection.execute("SELECT source_document, issue FROM hd_espp_import_issues ORDER BY source_document, id")]
            transfer_lots = [dict(row) for row in connection.execute("SELECT * FROM hd_espp_transfer_lots ORDER BY transfer_date, sequence")]
        def total(kind: str, field: str) -> Decimal:
            return sum((_decimal(row[field]) or Decimal()) for row in rows if row["transaction_type"] == kind and row["source_account_type"] != "Realized lot")
        matched = {row["transfer_match_id"] for row in rows if row.get("transfer_match_id")}
        economic_shares = economic_share_ownership(rows)
        return {"transactions": rows, "transfer_lots": transfer_lots, "issues": issue_records, "summary": {"total_espp_contributions": total("ESPP_PURCHASE", "amount"), "total_espp_shares": total("ESPP_PURCHASE", "shares"), "total_drip_shares": total("DIVIDEND_REINVESTMENT", "shares"), "total_dividends_reinvested": total("DIVIDEND_REINVESTMENT", "amount"), "total_shares_transferred": total("TRANSFER_OUT", "shares"), "total_shares_sold": total("SALE", "shares"), "documents": documents, "transactions": len(rows), "transfer_lots": len(transfer_lots), "issues": issues, "matched_transfers": len(matched), "economic_shares": economic_shares}}


__all__ = ["HDESPPHistoryService", "HDTransaction", "HDTransferLot", "detect_document_family", "economic_share_ownership", "match_custody_transfers", "parse_hd_statement_text", "parse_hd_transfer_lots", "reconcile_custody_transfers", "transaction_fingerprint", "transaction_record_type"]

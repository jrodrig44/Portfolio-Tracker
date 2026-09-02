"""Deterministic HD share-provenance reconstruction from normalized transactions."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


SHARE_TOLERANCE = Decimal("0.000001")
LOT_DISPLAY_TOLERANCE = Decimal("0.0005")
LIVE_TOLERANCE = Decimal("0.0001")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


@dataclass
class OwnershipLot:
    lot_id: str
    origin_type: str
    acquisition_date: date
    original_shares: Decimal
    remaining_shares: Decimal
    purchase_price: Decimal | None
    fair_market_value: Decimal | None
    employee_contribution: Decimal | None
    reinvested_dividend_amount: Decimal | None
    fees: Decimal | None
    taxes: Decimal | None
    known_cost_basis: Decimal | None
    cost_basis_status: str
    transfer_value: Decimal | None
    source: str
    source_transaction_fingerprint: str
    source_document: str
    original_custodian: str
    current_custodian: str
    status: str = "OPEN"
    shares_sold: Decimal = Decimal()
    shares_transferred: Decimal = Decimal()
    notes: str = ""

    def display(self) -> dict[str, Any]:
        row = asdict(self)
        row["acquisition_date"] = self.acquisition_date.isoformat()
        return row


@dataclass
class SoldLot:
    sale_fingerprint: str
    sale_date: date
    acquisition_date: date | None
    origin_type: str
    shares_sold: Decimal
    sale_amount: Decimal | None
    cost_basis: Decimal | None
    matched_lot_id: str | None
    match_status: str
    remaining_shares: Decimal | None
    notes: str = ""

    def display(self) -> dict[str, Any]:
        return {
            "sale_date": self.sale_date.isoformat(),
            "acquisition_date": self.acquisition_date.isoformat() if self.acquisition_date else "Unknown",
            "origin": self.origin_type,
            "shares_sold": self.shares_sold,
            "sale_amount": self.sale_amount,
            "known_cost_basis": self.cost_basis if self.cost_basis is not None else "Unknown",
            "remaining_shares": self.remaining_shares,
            "match_status": self.match_status,
            "notes": self.notes,
        }


@dataclass
class OwnershipReconstruction:
    lots: list[OwnershipLot]
    sold_lots: list[SoldLot]
    issues: list[str]
    summary: dict[str, Any]
    origin_breakdown: list[dict[str, Any]]


def _lot_id(fingerprint: str) -> str:
    return "HD-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def _origin(row: dict[str, Any]) -> str | None:
    kind = row.get("transaction_type")
    if kind == "ESPP_PURCHASE":
        return "ESPP_PURCHASE"
    if kind == "DIVIDEND_REINVESTMENT":
        return "DIVIDEND_REINVESTMENT"
    if kind == "ADJUSTMENT" and "restricted" in f"{row.get('source_location', '')} {row.get('notes', '')}".lower():
        return "RESTRICTED_SHARE"
    if kind == "TRANSFER_IN" and not row.get("transfer_match_id"):
        return "UNKNOWN"
    return None


def _acquisition_lots(rows: Iterable[dict[str, Any]]) -> list[OwnershipLot]:
    lots: list[OwnershipLot] = []
    for row in rows:
        origin = _origin(row)
        shares = _decimal(row.get("shares"))
        acquired = _date(row.get("acquisition_date")) or _date(row.get("transaction_date"))
        if not origin or not shares or shares <= 0 or acquired is None:
            continue
        amount = _decimal(row.get("amount"))
        basis = _decimal(row.get("cost_basis"))
        if basis is None and origin in {"ESPP_PURCHASE", "DIVIDEND_REINVESTMENT"}:
            basis = amount
        custodian = "Merrill" if row.get("source") == "Merrill" else str(row.get("source_location") or row.get("source") or "Unknown")
        fingerprint = str(row["transaction_fingerprint"])
        lots.append(OwnershipLot(
            lot_id=_lot_id(fingerprint), origin_type=origin, acquisition_date=acquired,
            original_shares=shares, remaining_shares=shares,
            purchase_price=_decimal(row.get("purchase_price")), fair_market_value=_decimal(row.get("fair_market_value")),
            employee_contribution=amount if origin == "ESPP_PURCHASE" else None,
            reinvested_dividend_amount=amount if origin == "DIVIDEND_REINVESTMENT" else None,
            fees=_decimal(row.get("fees")), taxes=_decimal(row.get("taxes")),
            known_cost_basis=basis, cost_basis_status="KNOWN" if basis is not None else "UNKNOWN",
            transfer_value=_decimal(row.get("transfer_value")), source=str(row.get("source") or ""),
            source_transaction_fingerprint=fingerprint, source_document=str(row.get("source_document") or ""),
            original_custodian=custodian, current_custodian=custodian, notes=str(row.get("notes") or ""),
        ))
    return lots


def _known_sale_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (row for row in rows if row.get("transaction_type") == "SALE" and row.get("source_account_type") == "Realized lot"),
        key=lambda row: (str(row.get("transaction_date")), int(row.get("lot_sequence") or 0)),
    )


def _apply_custody_transfers(rows: list[dict[str, Any]], lots: list[OwnershipLot], sale_rows: list[dict[str, Any]], issues: list[str]) -> tuple[int, Decimal, Decimal]:
    match_ids = {str(row["transfer_match_id"]) for row in rows if row.get("transfer_match_id")}
    transferred = Decimal()
    processed = 0
    precision_difference = Decimal()
    for match_id in sorted(match_ids):
        outgoing = next((row for row in rows if row.get("transfer_match_id") == match_id and row.get("transaction_type") == "TRANSFER_OUT"), None)
        incoming = next((row for row in rows if row.get("transfer_match_id") == match_id and row.get("transaction_type") == "TRANSFER_IN"), None)
        if not outgoing or not incoming:
            issues.append(f"Custody match {match_id} is missing one side")
            continue
        target = abs(_decimal(outgoing.get("shares")) or Decimal())
        transfer_date = _date(outgoing.get("transaction_date"))
        allocations: list[tuple[OwnershipLot, Decimal]] = []
        for sale in sale_rows:
            acquired = _date(sale.get("acquisition_date"))
            if acquired is None or transfer_date is None or acquired > transfer_date:
                continue
            candidates = [lot for lot in lots if lot.acquisition_date == acquired and lot.original_custodian in {"Computershare", "EquatePlus"}]
            if len(candidates) == 1:
                quantity = min(_decimal(sale.get("shares")) or Decimal(), candidates[0].original_shares)
                allocations.append((candidates[0], quantity))
        allocated = sum((quantity for _, quantity in allocations), Decimal())
        difference = target - allocated
        if abs(difference) > LOT_DISPLAY_TOLERANCE:
            issues.append(f"Custody transfer {match_id} documents {target} shares but normalized lot lineage accounts for {allocated}")
            continue
        for lot, quantity in allocations:
            lot.shares_transferred += quantity
            lot.current_custodian = "Merrill" if lot.original_shares - quantity <= SHARE_TOLERANCE else "Computershare / Merrill"
        processed += 1
        transferred += target
        precision_difference += difference
    return processed, transferred, precision_difference


def _match_sale_lot(lots: list[OwnershipLot], acquired: date, quantity: Decimal) -> list[OwnershipLot]:
    exact = [lot for lot in lots if lot.acquisition_date == acquired and lot.remaining_shares + LOT_DISPLAY_TOLERANCE >= quantity]
    if exact:
        return exact
    return [lot for lot in lots if lot.origin_type == "RESTRICTED_SHARE" and abs((lot.acquisition_date - acquired).days) <= 3 and lot.remaining_shares + LOT_DISPLAY_TOLERANCE >= quantity]


def reconstruct_hd_ownership(rows: Iterable[dict[str, Any]], live_shares: Decimal | str | float | None = None) -> OwnershipReconstruction:
    transactions = [dict(row) for row in rows]
    lots = _acquisition_lots(transactions)
    sale_rows = _known_sale_rows(transactions)
    issues: list[str] = []
    transfers_processed, shares_transferred, transfer_precision_difference = _apply_custody_transfers(transactions, lots, sale_rows, issues)
    sold: list[SoldLot] = []
    sale_precision_difference = Decimal()
    for row in sale_rows:
        quantity = _decimal(row.get("shares")) or Decimal()
        acquired = _date(row.get("acquisition_date"))
        sale_date = _date(row.get("transaction_date")) or date.min
        if acquired is None:
            sold.append(SoldLot(str(row["transaction_fingerprint"]), sale_date, None, "UNRESOLVED", quantity, _decimal(row.get("amount")), None, None, "UNRESOLVED", None, "Source reports acquisition date and basis as N/A"))
            continue
        candidates = _match_sale_lot(lots, acquired, quantity)
        if len(candidates) != 1:
            issues.append(f"Realized lot on {sale_date.isoformat()} for {quantity} shares acquired {acquired.isoformat()} has {len(candidates)} provenance matches")
            sold.append(SoldLot(str(row["transaction_fingerprint"]), sale_date, acquired, "UNRESOLVED", quantity, _decimal(row.get("amount")), _decimal(row.get("cost_basis")), None, "UNRESOLVED", None, "No unique acquisition lot match"))
            continue
        lot = candidates[0]
        applied = min(quantity, lot.remaining_shares)
        precision_difference = quantity - applied
        sale_precision_difference += precision_difference
        lot.remaining_shares -= applied
        lot.shares_sold += applied
        lot.status = "SOLD" if lot.remaining_shares <= SHARE_TOLERANCE else "PARTIALLY_SOLD"
        if lot.current_custodian == "Computershare / Merrill" and lot.shares_sold + SHARE_TOLERANCE >= lot.shares_transferred:
            lot.current_custodian = "Computershare" if lot.remaining_shares > SHARE_TOLERANCE else "Merrill"
        note = "Matched by documented acquisition date and custody lineage"
        if precision_difference:
            note += f"; {precision_difference} share source-display difference accepted within {LOT_DISPLAY_TOLERANCE} tolerance"
        sold.append(SoldLot(str(row["transaction_fingerprint"]), sale_date, acquired, lot.origin_type, quantity, _decimal(row.get("amount")), _decimal(row.get("cost_basis")), lot.lot_id, "MATCHED", lot.remaining_shares, note))

    acquired_totals = {origin: sum((lot.original_shares for lot in lots if lot.origin_type == origin), Decimal()) for origin in ("ESPP_PURCHASE", "DIVIDEND_REINVESTMENT", "RESTRICTED_SHARE", "OTHER_DOCUMENTED_ACQUISITION", "UNKNOWN")}
    aggregate_sales = [row for row in transactions if row.get("transaction_type") == "SALE" and row.get("source_account_type") == "Brokerage"]
    total_sold = sum((_decimal(row.get("shares")) or Decimal() for row in aggregate_sales), Decimal())
    detailed_sold = sum((entry.shares_sold for entry in sold), Decimal())
    if aggregate_sales and abs(total_sold - detailed_sold) > SHARE_TOLERANCE:
        issues.append(f"Aggregate sale quantity {total_sold} does not reconcile to realized-lot quantity {detailed_sold}")
    total_acquired = sum(acquired_totals.values(), Decimal())
    reconstructed = total_acquired - total_sold
    unresolved_sold = sum((entry.shares_sold for entry in sold if entry.match_status == "UNRESOLVED"), Decimal())
    known_open = sum((lot.remaining_shares for lot in lots), Decimal())
    live = _decimal(live_shares)
    difference = reconstructed - live if live is not None else None
    if difference is None:
        live_status = "LIVE_QUANTITY_UNAVAILABLE"
    elif difference == 0:
        live_status = "MATCHED"
    elif abs(difference) <= LIVE_TOLERANCE:
        live_status = "WITHIN_TOLERANCE"
    else:
        live_status = "DIFFERENCE_REQUIRES_REVIEW"
    summary = {
        "espp_lots": sum(lot.origin_type == "ESPP_PURCHASE" for lot in lots), "total_espp_shares": acquired_totals["ESPP_PURCHASE"],
        "drip_lots": sum(lot.origin_type == "DIVIDEND_REINVESTMENT" for lot in lots), "total_drip_shares": acquired_totals["DIVIDEND_REINVESTMENT"],
        "restricted_lots": sum(lot.origin_type == "RESTRICTED_SHARE" for lot in lots), "total_restricted_shares": acquired_totals["RESTRICTED_SHARE"],
        "other_unknown_lots": sum(lot.origin_type in {"OTHER_DOCUMENTED_ACQUISITION", "UNKNOWN"} for lot in lots),
        "total_other_unknown_shares": acquired_totals["OTHER_DOCUMENTED_ACQUISITION"] + acquired_totals["UNKNOWN"],
        "total_economic_shares_acquired": total_acquired, "custody_transfers_processed": transfers_processed,
        "shares_transferred": shares_transferred, "realized_lots_processed": len(sold),
        "known_origin_sold_lots": sum(entry.match_status == "MATCHED" for entry in sold),
        "unresolved_origin_sold_lots": sum(entry.match_status == "UNRESOLVED" for entry in sold),
        "total_shares_sold": total_sold, "reconstructed_remaining_shares": reconstructed,
        "known_open_lot_shares": known_open, "unresolved_origin_remaining_shares": acquired_totals["UNKNOWN"],
        "unresolved_sold_lot_shares": unresolved_sold, "live_hd_shares": live,
        "reconciliation_difference": difference, "reconciliation_status": live_status,
        "share_tolerance": SHARE_TOLERANCE, "live_tolerance": LIVE_TOLERANCE,
        "lot_display_tolerance": LOT_DISPLAY_TOLERANCE,
        "transfer_precision_difference": transfer_precision_difference,
        "sale_precision_difference": sale_precision_difference,
    }
    origin_breakdown = [{"origin": origin, "lots": sum(lot.origin_type == origin for lot in lots), "shares_acquired": acquired_totals[origin], "shares_remaining_before_unresolved_sale_offset": sum((lot.remaining_shares for lot in lots if lot.origin_type == origin), Decimal())} for origin in acquired_totals]
    return OwnershipReconstruction(lots, sold, issues, summary, origin_breakdown)


__all__ = ["LIVE_TOLERANCE", "LOT_DISPLAY_TOLERANCE", "SHARE_TOLERANCE", "OwnershipLot", "OwnershipReconstruction", "SoldLot", "reconstruct_hd_ownership"]

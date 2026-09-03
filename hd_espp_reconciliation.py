"""Phase 3A HD historical-to-live share reconciliation.

This module derives reconciliation views from normalized HD/ESPP history,
position checkpoints, and ownership reconstruction. It never creates plug
transactions or changes live portfolio accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from hd_ownership import LIVE_TOLERANCE, LOT_DISPLAY_TOLERANCE, OwnershipReconstruction, reconstruct_hd_ownership


ROUNDING_TOLERANCE = Decimal("0.0005")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _source_label(family: str) -> tuple[str, str, str]:
    if family == "computershare_legacy":
        return "Computershare", "Computershare / employee plan", "Legacy employee-plan statement"
    if family == "computershare_equateplus":
        return "EquatePlus / Computershare", "Computershare / employee plan", "Newer purchase-activity statement"
    if family == "merrill_hd":
        return "Merrill", "Merrill brokerage", "Merrill brokerage statement"
    return "Unknown", "Unknown", "Unknown statement"


@dataclass
class HDPhase3AReconciliation:
    summary: dict[str, Any]
    coverage_timeline: list[dict[str, Any]]
    coverage_gaps: list[dict[str, Any]]
    coverage_overlaps: list[dict[str, Any]]
    custody_timeline: list[dict[str, Any]]
    share_bridge: list[dict[str, Any]]
    surviving_lots: list[dict[str, Any]]
    position_checkpoints: list[dict[str, Any]]
    checkpoint_reconciliation: list[dict[str, Any]]
    total_checkpoint_reconciliation: list[dict[str, Any]]
    unresolved_sale_investigation: dict[str, Any]
    unknown_acquisition_investigation: dict[str, Any]
    explained_components: list[dict[str, Any]]
    issues: list[dict[str, Any]] = field(default_factory=list)


def _merge_intervals(intervals: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    ordered = sorted(intervals)
    if not ordered:
        return []
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + timedelta(days=1):
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _gaps_between_intervals(intervals: Iterable[tuple[date, date]], scope: str) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    merged = _merge_intervals(intervals)
    for previous, current in zip(merged, merged[1:]):
        gap_start = previous[1] + timedelta(days=1)
        gap_end = current[0] - timedelta(days=1)
        if gap_start <= gap_end:
            gaps.append({
                "issue_type": "STATEMENT_COVERAGE_GAP",
                "scope": scope,
                "gap_start": gap_start,
                "gap_end": gap_end,
                "coverage_status": "NO_DOCUMENT_COVERAGE",
                "notes": f"No {scope.lower()} source document covers this interval; this does not prove there was no activity.",
            })
    return gaps


def build_statement_coverage(documents: Iterable[dict[str, Any]], transactions: Iterable[dict[str, Any]], checkpoints: Iterable[dict[str, Any]] = ()) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tx_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in transactions:
        tx_by_document.setdefault(str(row.get("source_document") or ""), []).append(dict(row))
    checkpoint_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in checkpoints:
        checkpoint_by_document.setdefault(str(row.get("source_document") or ""), []).append(dict(row))

    timeline: list[dict[str, Any]] = []
    for document in documents:
        family = str(document.get("document_family") or "unknown")
        source, custodian, document_type = _source_label(family)
        source_document = str(document.get("source_document") or "")
        canonical_rows = tx_by_document.get(source_document, [])
        parsed_count = int(document.get("parsed_transaction_occurrence_count") or len(canonical_rows))
        earliest_parsed = _date(document.get("earliest_parsed_transaction_date"))
        latest_parsed = _date(document.get("latest_parsed_transaction_date"))
        period_start = _date(document.get("period_start"))
        period_end = _date(document.get("period_end"))
        timeline.append({
            "period": f"{period_start or 'Unknown'} to {period_end or 'Unknown'}",
            "period_start": period_start,
            "period_end": period_end,
            "source": source,
            "custodian": custodian,
            "document_type": document_type,
            "source_document": source_document,
            "coverage_status": "DOCUMENT_COVERAGE_WITH_ACTIVITY" if parsed_count else "DOCUMENT_COVERAGE_WITH_NO_ACTIVITY",
            "activity_period": f"{period_start or 'Unknown'} to {period_end or 'Unknown'}",
            "earliest_transaction": earliest_parsed,
            "latest_transaction": latest_parsed,
            "transactions": parsed_count,
            "canonical_transactions": len(canonical_rows),
            "parsed_transaction_types": document.get("parsed_transaction_types") or "",
            "position_checkpoint": ", ".join(f"{row.get('custodian')} {row.get('shares')}" for row in checkpoint_by_document.get(source_document, [])) or "None",
            "notes": "Covered period has no parsed supported HD activity" if not parsed_count else "Covered period has parsed supported HD activity",
        })
    timeline.sort(key=lambda row: (row["period_start"] or date.min, row["period_end"] or date.min, row["source_document"]))

    coverage_with_dates = [row for row in timeline if row["period_start"] is not None and row["period_end"] is not None]
    intervals = [(row["period_start"], row["period_end"]) for row in coverage_with_dates]
    gaps = _gaps_between_intervals(intervals, "GLOBAL_HD")
    by_scope: dict[str, list[tuple[date, date]]] = {}
    for row in coverage_with_dates:
        by_scope.setdefault(str(row["source"]), []).append((row["period_start"], row["period_end"]))
    for scope, scoped_intervals in sorted(by_scope.items()):
        gaps.extend(_gaps_between_intervals(scoped_intervals, scope))

    overlaps: list[dict[str, Any]] = []
    for index, left in enumerate(coverage_with_dates):
        for right in coverage_with_dates[index + 1:]:
            if left["period_end"] is None or right["period_start"] is None:
                continue
            if right["period_start"] > left["period_end"]:
                break
            overlap_start = max(left["period_start"], right["period_start"])
            overlap_end = min(left["period_end"], right["period_end"])
            if overlap_start <= overlap_end:
                overlaps.append({
                    "overlap_start": overlap_start,
                    "overlap_end": overlap_end,
                    "left_source": left["source"],
                    "right_source": right["source"],
                    "notes": "Overlapping document coverage; activity remains source-specific by custodian.",
                })
    return timeline, gaps, overlaps


def build_share_bridge(ownership: OwnershipReconstruction) -> list[dict[str, Any]]:
    summary = ownership.summary
    return [
        {"component": "Beginning Documented Ownership", "shares": Decimal("0"), "economic_effect": Decimal("0"), "source_confidence": "Explicit zero baseline before available HD statements"},
        {"component": "ESPP Acquisitions", "shares": summary["total_espp_shares"], "economic_effect": summary["total_espp_shares"], "source_confidence": "Normalized Computershare/EquatePlus ESPP purchases"},
        {"component": "DRIP Acquisitions", "shares": summary["total_drip_shares"], "economic_effect": summary["total_drip_shares"], "source_confidence": "Normalized dividend reinvestments"},
        {"component": "Restricted Shares", "shares": summary["total_restricted_shares"], "economic_effect": summary["total_restricted_shares"], "source_confidence": "Merrill restricted-share transfer/adjustment rows"},
        {"component": "Other/Unknown Acquisitions", "shares": summary["total_other_unknown_shares"], "economic_effect": summary["total_other_unknown_shares"], "source_confidence": "Merrill security transfers without matched source provenance"},
        {"component": "Gross Documented Acquisitions", "shares": summary["total_economic_shares_acquired"], "economic_effect": summary["total_economic_shares_acquired"], "source_confidence": "Derived subtotal"},
        {"component": "Custody Transfers", "shares": summary["shares_transferred"], "economic_effect": Decimal("0"), "source_confidence": "Matched Computershare outflow to Merrill receipt; zero economic ownership change"},
        {"component": "Shares Sold", "shares": -summary["total_shares_sold"], "economic_effect": -summary["total_shares_sold"], "source_confidence": "Merrill aggregate sale, counted once"},
        {"component": "Reconstructed Remaining", "shares": summary["reconstructed_remaining_shares"], "economic_effect": summary["reconstructed_remaining_shares"], "source_confidence": "Acquisitions less sales"},
        {"component": "Live Holding", "shares": summary["live_hd_shares"], "economic_effect": Decimal("0"), "source_confidence": "Read-only current Portfolio Tracker holding"},
        {"component": "Historical/Live Difference", "shares": summary["reconciliation_difference"], "economic_effect": Decimal("0"), "source_confidence": "Calculated difference; not an adjustment transaction"},
    ]


def build_surviving_lot_audit(ownership: OwnershipReconstruction) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lot in sorted(ownership.lots, key=lambda item: (item.acquisition_date, item.origin_type, item.lot_id)):
        if lot.origin_type == "UNKNOWN":
            confidence = "UNRESOLVED_ACQUISITION_PROVENANCE"
            role = "May contribute to unresolved historical/live difference; source proves receipt but not origin"
        elif lot.remaining_shares > Decimal("0"):
            confidence = "SOURCE_DOCUMENTED"
            role = "Contributes to reconstructed historical remaining shares"
        else:
            confidence = "SOURCE_DOCUMENTED"
            role = "Fully consumed by documented sale or transfer state"
        rows.append({
            "origin": lot.origin_type,
            "acquisition_date": lot.acquisition_date,
            "original_shares": lot.original_shares,
            "source_custodian": lot.original_custodian,
            "shares_transferred": lot.shares_transferred,
            "known_shares_sold": lot.shares_sold,
            "remaining_documented_shares": lot.remaining_shares,
            "current_reconstructed_custodian": lot.current_custodian,
            "provenance_confidence": confidence,
            "basis_status": lot.cost_basis_status,
            "reconciliation_role": role,
            "source_document": lot.source_document,
        })
    return rows


def build_custody_timeline(transactions: Iterable[dict[str, Any]], transfer_lots: Iterable[dict[str, Any]], ownership: OwnershipReconstruction) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in sorted(transactions, key=lambda item: (str(item.get("transaction_date")), str(item.get("transaction_fingerprint")))):
        kind = row.get("transaction_type")
        if kind not in {"TRANSFER_OUT", "TRANSFER_IN", "ADJUSTMENT"}:
            continue
        shares = abs(_decimal(row.get("shares")) or Decimal("0"))
        effect = Decimal("0") if row.get("transfer_match_id") and kind in {"TRANSFER_OUT", "TRANSFER_IN"} else shares
        rows.append({
            "date": row.get("transaction_date"),
            "activity": kind,
            "source": row.get("source"),
            "from": row.get("source_location"),
            "to": row.get("destination_location"),
            "shares": shares,
            "economic_effect": effect,
            "notes": "Matched custody transfer; no economic ownership change" if effect == 0 and kind in {"TRANSFER_OUT", "TRANSFER_IN"} else row.get("notes") or "Documented acquisition activity",
        })
    for detail in transfer_lots:
        rows.append({
            "date": detail.get("transfer_date"),
            "activity": "TRANSFER_LOT_DETAIL",
            "source": "Computershare",
            "from": "Computershare",
            "to": "Merrill",
            "shares": _decimal(detail.get("transferred_shares")) or Decimal("0"),
            "economic_effect": Decimal("0"),
            "notes": f"Underlying transfer lot acquired {detail.get('acquisition_date')} ({detail.get('source_type') or 'Unknown'})",
        })
    return sorted(rows, key=lambda item: (str(item["date"]), str(item["activity"]), str(item["notes"])))


def _checkpoint_status(difference: Decimal) -> str:
    if difference == 0:
        return "MATCHED"
    if abs(difference) <= ROUNDING_TOLERANCE:
        return "WITHIN_SOURCE_ROUNDING"
    return "DIFFERENCE_REQUIRES_REVIEW"


def _reconstruct_as_of(transactions: list[dict[str, Any]], transfer_lots: list[dict[str, Any]], checkpoint_date: date) -> OwnershipReconstruction:
    filtered_transactions = [row for row in transactions if (_date(row.get("transaction_date")) or date.min) <= checkpoint_date]
    fingerprints = {str(row.get("transaction_fingerprint")) for row in filtered_transactions}
    filtered_transfer_lots = [row for row in transfer_lots if str(row.get("parent_transfer_fingerprint")) in fingerprints and (_date(row.get("transfer_date")) or date.min) <= checkpoint_date]
    return reconstruct_hd_ownership(filtered_transactions, transfer_lots=filtered_transfer_lots)


def _custodian_balances(ownership: OwnershipReconstruction) -> dict[str, Decimal]:
    balances = {"Computershare": Decimal("0"), "Merrill": Decimal("0")}
    for lot in ownership.lots:
        if lot.current_custodian == "Computershare / Merrill":
            merrill_remaining = max(lot.shares_transferred - lot.shares_sold, Decimal("0"))
            computershare_remaining = max(lot.remaining_shares - merrill_remaining, Decimal("0"))
            balances["Merrill"] += merrill_remaining
            balances["Computershare"] += computershare_remaining
        elif "Merrill" in lot.current_custodian:
            balances["Merrill"] += lot.remaining_shares
        elif "Computershare" in lot.current_custodian or "EquatePlus" in lot.current_custodian:
            balances["Computershare"] += lot.remaining_shares
    unresolved_sold = ownership.summary.get("unresolved_sold_lot_shares") or Decimal("0")
    balances["Merrill"] = max(balances["Merrill"] - unresolved_sold, Decimal("0"))
    return balances


def reconcile_position_checkpoints(transactions: Iterable[dict[str, Any]], transfer_lots: Iterable[dict[str, Any]], checkpoints: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], date | None, Decimal | None, date | None, Decimal | None]:
    tx_rows = [dict(row) for row in transactions]
    transfer_rows = [dict(row) for row in transfer_lots]
    checkpoint_rows = [dict(row) for row in checkpoints]
    detail: list[dict[str, Any]] = []
    total_by_date: dict[date, list[dict[str, Any]]] = {}
    conflicts_by_key: dict[tuple[date, str], set[Decimal]] = {}
    for checkpoint in checkpoint_rows:
        checkpoint_date = _date(checkpoint.get("checkpoint_date"))
        documented = _decimal(checkpoint.get("shares"))
        custodian = str(checkpoint.get("custodian") or "")
        if checkpoint_date is not None and documented is not None:
            conflicts_by_key.setdefault((checkpoint_date, custodian), set()).add(documented)
    conflict_keys = {key for key, values in conflicts_by_key.items() if len(values) > 1}
    for checkpoint in checkpoint_rows:
        checkpoint_date = _date(checkpoint.get("checkpoint_date"))
        documented = _decimal(checkpoint.get("shares"))
        custodian = str(checkpoint.get("custodian") or "")
        if checkpoint_date is None or documented is None:
            continue
        as_of = _reconstruct_as_of(tx_rows, transfer_rows, checkpoint_date)
        reconstructed = _custodian_balances(as_of).get(custodian, Decimal("0"))
        difference = reconstructed - documented
        conflict = (checkpoint_date, custodian) in conflict_keys
        row = {
            "checkpoint_date": checkpoint_date,
            "custodian": custodian,
            "documented_position_shares": documented,
            "reconstructed_custodian_shares": reconstructed,
            "difference": difference,
            "status": "POSITION_CHECKPOINT_CONFLICT" if conflict else _checkpoint_status(difference),
            "source_document": checkpoint.get("source_document"),
            "source_documents": checkpoint.get("source_documents") or checkpoint.get("source_document"),
            "occurrence_count": int(checkpoint.get("occurrence_count") or 1),
            "confidence": checkpoint.get("confidence"),
            "notes": checkpoint.get("notes"),
        }
        detail.append(row)
        if not conflict:
            total_by_date.setdefault(checkpoint_date, []).append(row)

    totals: list[dict[str, Any]] = []
    last_total_reconciled_date: date | None = None
    latest_reconciled_position: Decimal | None = None
    latest_documented_checkpoint_date: date | None = None
    latest_documented_position: Decimal | None = None
    for checkpoint_date, rows in sorted(total_by_date.items()):
        as_of = _reconstruct_as_of(tx_rows, transfer_rows, checkpoint_date)
        documented_total = sum((_decimal(row["documented_position_shares"]) or Decimal("0") for row in rows), Decimal("0"))
        reconstructed_total = as_of.summary["reconstructed_remaining_shares"]
        custodian_balance = _custodian_balances(as_of)
        documented_custodians = {str(row["custodian"]) for row in rows}
        missing_custodians = [name for name, shares in custodian_balance.items() if shares > ROUNDING_TOLERANCE and name not in documented_custodians]
        difference = reconstructed_total - documented_total
        if missing_custodians:
            status = "PARTIAL_CUSTODIAN_COVERAGE"
        else:
            status = _checkpoint_status(difference)
        if latest_documented_checkpoint_date is None or checkpoint_date >= latest_documented_checkpoint_date:
            latest_documented_checkpoint_date = checkpoint_date
            latest_documented_position = documented_total
        if status in {"MATCHED", "WITHIN_SOURCE_ROUNDING"}:
            last_total_reconciled_date = checkpoint_date
            latest_reconciled_position = documented_total
        totals.append({
            "checkpoint_date": checkpoint_date,
            "documented_position_shares": documented_total,
            "reconstructed_total_shares": reconstructed_total,
            "difference": difference,
            "status": status,
            "documented_custodians": ", ".join(sorted(documented_custodians)),
            "missing_custodian_checkpoints": ", ".join(missing_custodians),
        })
    for checkpoint_date, custodian in sorted(conflict_keys):
        conflicting = [row for row in detail if row["checkpoint_date"] == checkpoint_date and row["custodian"] == custodian]
        totals.append({
            "checkpoint_date": checkpoint_date,
            "documented_position_shares": ", ".join(str(row["documented_position_shares"]) for row in conflicting),
            "reconstructed_total_shares": _reconstruct_as_of(tx_rows, transfer_rows, checkpoint_date).summary["reconstructed_remaining_shares"],
            "difference": None,
            "status": "POSITION_CHECKPOINT_CONFLICT",
            "documented_custodians": custodian,
            "missing_custodian_checkpoints": "",
            "source_documents": "; ".join(str(row.get("source_documents") or row.get("source_document")) for row in conflicting),
        })
    return sorted(detail, key=lambda row: (row["checkpoint_date"], row["custodian"], str(row["documented_position_shares"]))), sorted(totals, key=lambda row: row["checkpoint_date"]), last_total_reconciled_date, latest_reconciled_position, latest_documented_checkpoint_date, latest_documented_position


def _issue(issue_type: str, message: str, severity: str = "REVIEW") -> dict[str, Any]:
    return {"issue_type": issue_type, "severity": severity, "message": message}


def roll_forward_current_custody(transactions: Iterable[dict[str, Any]], checkpoints: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Roll each custodian's latest documented position through later activity."""
    tx_rows, checkpoint_rows = [dict(row) for row in transactions], [dict(row) for row in checkpoints]
    result: dict[str, Decimal] = {}
    dates: dict[str, date] = {}
    aliases = {"Computershare": "Equate", "EquatePlus": "Equate", "Equate": "Equate", "Merrill": "Merrill"}
    for row in checkpoint_rows:
        custodian = aliases.get(str(row.get("custodian")))
        at, shares = _date(row.get("checkpoint_date")), _decimal(row.get("shares"))
        if custodian and at and shares is not None and (custodian not in dates or at >= dates[custodian]):
            dates[custodian], result[custodian] = at, shares
    for custodian, start in dates.items():
        for row in tx_rows:
            at, shares = _date(row.get("transaction_date")), _decimal(row.get("shares")) or Decimal("0")
            if not at or at <= start: continue
            kind = row.get("transaction_type")
            source = f"{row.get('source_location') or ''} {row.get('source') or ''}"
            if kind in {"ESPP_PURCHASE", "DIVIDEND_REINVESTMENT", "ADJUSTMENT"} and aliases.get("Merrill" if "Merrill" in source else "EquatePlus") == custodian:
                result[custodian] += shares
            # Aggregate brokerage rows are the economic disposition; realized lots are detail.
            elif kind == "SALE" and custodian == "Merrill" and row.get("source_account_type") == "Brokerage":
                result[custodian] -= shares
            elif kind == "TRANSFER_OUT" and aliases.get("Merrill" if "Merrill" in source else "EquatePlus") == custodian:
                result[custodian] -= shares
            elif kind == "TRANSFER_IN" and custodian == "Merrill" and row.get("transfer_match_id"):
                result[custodian] += shares
    combined = sum(result.values(), Decimal("0"))
    return {"equate_shares": result.get("Equate"), "merrill_shares": result.get("Merrill"), "combined_beneficial_shares": combined, "unresolved_quantity": Decimal("0") if len(result) == 2 else None, "checkpoint_dates": dates}


def build_hd_phase3a_reconciliation(transactions: Iterable[dict[str, Any]], transfer_lots: Iterable[dict[str, Any]], documents: Iterable[dict[str, Any]], position_checkpoints: Iterable[dict[str, Any]], ownership: OwnershipReconstruction, live_hd_shares: Decimal | str | float | None) -> HDPhase3AReconciliation:
    tx_rows = [dict(row) for row in transactions]
    transfer_rows = [dict(row) for row in transfer_lots]
    document_rows = [dict(row) for row in documents]
    checkpoint_rows = [dict(row) for row in position_checkpoints]
    live = _decimal(live_hd_shares)
    current_custody = roll_forward_current_custody(tx_rows, checkpoint_rows)
    reconstructed = current_custody["combined_beneficial_shares"] if current_custody["unresolved_quantity"] is not None else ownership.summary["reconstructed_remaining_shares"]
    # The normal holdings row is Merrill custody only, not combined beneficial ownership.
    if current_custody["unresolved_quantity"] is None:
        reconstructed = ownership.summary["reconstructed_remaining_shares"]
        raw_difference = reconstructed - live if live is not None else None
    else:
        raw_difference = (current_custody["merrill_shares"] - live) if live is not None and current_custody["merrill_shares"] is not None else current_custody["unresolved_quantity"]
    coverage, gaps, overlaps = build_statement_coverage(document_rows, tx_rows, checkpoint_rows)
    checkpoint_detail, checkpoint_totals, last_reconciled_date, last_reconciled_position, latest_documented_checkpoint_date, latest_documented_position = reconcile_position_checkpoints(tx_rows, transfer_rows, checkpoint_rows)
    unresolved_sold = [lot for lot in ownership.sold_lots if lot.match_status == "UNRESOLVED"]
    unknown_lots = [lot for lot in ownership.lots if lot.origin_type == "UNKNOWN"]
    unresolved_sold_shares = sum((lot.shares_sold for lot in unresolved_sold), Decimal("0"))
    unknown_acquisition_shares = sum((lot.original_shares for lot in unknown_lots), Decimal("0"))
    can_link_unknown_to_sales = False
    relationship_status = "NO_SUPPORTED_LINK"
    if unresolved_sold_shares == unknown_acquisition_shares and unresolved_sold_shares > Decimal("0"):
        relationship_status = "QUANTITY_MATCH_ONLY_INSUFFICIENT"

    issues = [_issue("STATEMENT_COVERAGE_GAP", f"No {gap['scope']} coverage from {gap['gap_start']} to {gap['gap_end']}; activity unknown.") for gap in gaps]
    issues.extend(_issue("POSITION_CHECKPOINT_MISMATCH", f"{row['custodian']} checkpoint on {row['checkpoint_date']} differs by {row['difference']} shares.") for row in checkpoint_detail if row["status"] == "DIFFERENCE_REQUIRES_REVIEW")
    issues.extend(_issue("POSITION_CHECKPOINT_CONFLICT", f"{row['custodian']} checkpoint on {row['checkpoint_date']} has conflicting reported share values.") for row in checkpoint_detail if row["status"] == "POSITION_CHECKPOINT_CONFLICT")
    issues.extend(_issue("CUSTODY_RECONCILIATION_GAP", issue) for issue in ownership.issues if "Custody" in issue or "transfer" in issue.lower())
    if unresolved_sold_shares:
        issues.append(_issue("UNRESOLVED_SALE_PROVENANCE", f"{unresolved_sold_shares} sold shares have N/A acquisition date/basis and remain unassigned."))
    if unknown_acquisition_shares:
        issues.append(_issue("UNRESOLVED_ACQUISITION_PROVENANCE", f"{unknown_acquisition_shares} acquired shares are documented Merrill receipts without source-origin provenance."))
    if raw_difference is not None and abs(raw_difference) > LIVE_TOLERANCE:
        issues.append(_issue("LIVE_POSITION_DIFFERENCE", f"Historical reconstruction differs from the live HD holding by {raw_difference} shares."))
    if any(row["status"] == "PARTIAL_CUSTODIAN_COVERAGE" for row in checkpoint_totals):
        issues.append(_issue("CURRENT_POSITION_NOT_HISTORICALLY_COVERED", "Latest source checkpoints do not cover every custodian with reconstructed shares."))

    explained_components: list[dict[str, Any]] = []
    explained_difference = sum((_decimal(row.get("shares")) or Decimal("0") for row in explained_components), Decimal("0"))
    remaining_unexplained = None if raw_difference is None else raw_difference - explained_difference
    categories = sorted({issue["issue_type"] for issue in issues}) or ["EXPLAINED_DOCUMENTED_ACTIVITY"]
    if current_custody["unresolved_quantity"] == 0 and (raw_difference is None or abs(raw_difference) <= LIVE_TOLERANCE):
        categories = ["CURRENT_CUSTODY_RECONCILED"]
    summary = {
        "historical_reconstructed_shares": reconstructed,
        "current_custody": current_custody,
        "live_hd_shares": live,
        "raw_historical_live_difference": raw_difference,
        "explained_difference": explained_difference,
        "remaining_unexplained_difference": remaining_unexplained,
        "last_reconciled_historical_date": last_reconciled_date,
        "last_fully_reconciled_date": last_reconciled_date,
        "last_fully_reconciled_position_quantity": last_reconciled_position,
        "latest_documented_checkpoint_date": latest_documented_checkpoint_date,
        "latest_documented_position_quantity": latest_documented_position,
        "difference_introduced_after": last_reconciled_date,
        "earliest_unresolved_period": gaps[0]["gap_start"] if gaps else None,
        "unresolved_sold_shares": unresolved_sold_shares,
        "unknown_acquisition_shares": unknown_acquisition_shares,
        "reconciliation_categories": categories,
        "phase3a_status": "INSUFFICIENT_HISTORICAL_DOCUMENTATION" if remaining_unexplained is None or abs(remaining_unexplained) > LIVE_TOLERANCE else ("RECONCILED" if current_custody["unresolved_quantity"] is not None else "RECONCILED_TO_LIVE"),
        "plug_transaction_created": False,
        "lifetime_return_withheld": True,
    }
    return HDPhase3AReconciliation(
        summary=summary,
        coverage_timeline=coverage,
        coverage_gaps=gaps,
        coverage_overlaps=overlaps,
        custody_timeline=build_custody_timeline(tx_rows, transfer_rows, ownership),
        share_bridge=build_share_bridge(ownership),
        surviving_lots=build_surviving_lot_audit(ownership),
        position_checkpoints=checkpoint_rows,
        checkpoint_reconciliation=checkpoint_detail,
        total_checkpoint_reconciliation=checkpoint_totals,
        unresolved_sale_investigation={
            "unresolved_sold_shares": unresolved_sold_shares,
            "unresolved_lot_count": len(unresolved_sold),
            "evidence": "Merrill realized-lot rows report acquisition date and basis as N/A; no source row provides origin.",
        },
        unknown_acquisition_investigation={
            "unknown_acquisition_shares": unknown_acquisition_shares,
            "unknown_lot_count": len(unknown_lots),
            "can_link_to_unresolved_sales": can_link_unknown_to_sales,
            "relationship_status": relationship_status,
            "evidence": "Quantity equality alone is insufficient; no dates, basis, lot identifiers, or transfer references link the unknown Merrill receipts to the N/A sale lots.",
        },
        explained_components=explained_components,
        issues=issues,
    )


__all__ = [
    "HDPhase3AReconciliation",
    "ROUNDING_TOLERANCE",
    "build_hd_phase3a_reconciliation",
    "build_share_bridge",
    "build_statement_coverage",
    "build_surviving_lot_audit",
    "reconcile_position_checkpoints",
    "roll_forward_current_custody",
]

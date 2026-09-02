"""Deterministic HD / ESPP economics analysis built only from normalized source data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from hd_ownership import OwnershipReconstruction


CONTRIBUTION_TOLERANCE = Decimal("0.01")
DRIP_TOLERANCE = Decimal("0.01")
DRIP_PURCHASE_VALUE_ABSOLUTE_TOLERANCE = Decimal("0.02")
DRIP_PURCHASE_VALUE_RELATIVE_TOLERANCE = Decimal("0.005")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class HDEconomicsAnalysis:
    espp_summary: dict[str, Any]
    discount_summary: dict[str, Any]
    drip_summary: dict[str, Any]
    dividend_summary: dict[str, Any]
    realized_summary: dict[str, Any]
    realized_by_origin: dict[str, Any]
    coverage: dict[str, Any]
    reconciliation: dict[str, Any]
    issues: list[str]
    per_purchase_discount: list[dict[str, Any]] = field(default_factory=list)
    drip_detail: list[dict[str, Any]] = field(default_factory=list)
    realized_detail: list[dict[str, Any]] = field(default_factory=list)
    open_lot_economic_cost: dict[str, Any] = field(default_factory=dict)
    current_snapshot: dict[str, Any] = field(default_factory=dict)


def _sum_decimals(values: Iterable[Decimal | None]) -> Decimal:
    total = Decimal("0")
    for value in values:
        if value is not None:
            total += value
    return total


def _identity_date(value: Any) -> date | None:
    parsed = _date(value)
    if parsed is not None:
        return parsed
    return None


def _first_last_dates(rows: Iterable[dict[str, Any]], field: str) -> tuple[date | None, date | None]:
    values = [_identity_date(row.get(field)) for row in rows if row.get(field)]
    if not values:
        return None, None
    return min(values), max(values)


def _extract_gross_dividend(row: dict[str, Any]) -> tuple[Decimal | None, bool]:
    notes = str(row.get("notes") or "")
    match = re.search(r"gross_dividend\s*=\s*([-+]?\d+(?:,\d{3})*(?:\.\d+)?)", notes, re.I)
    if match:
        return _decimal(match.group(1)), True
    net = _decimal(row.get("amount"))
    fees = _decimal(row.get("fees"))
    taxes = _decimal(row.get("taxes"))
    if net is not None and fees is not None and taxes is not None:
        return net + fees + taxes, True
    return None, False


def _drip_purchase_value_tolerance(amount: Decimal | None) -> Decimal:
    if amount is None:
        return DRIP_PURCHASE_VALUE_ABSOLUTE_TOLERANCE
    relative = abs(amount) * DRIP_PURCHASE_VALUE_RELATIVE_TOLERANCE
    return max(DRIP_PURCHASE_VALUE_ABSOLUTE_TOLERANCE, relative)


def analyze_hd_economics(transactions: Iterable[dict[str, Any]], ownership: OwnershipReconstruction | None = None, live_hd_shares: Decimal | str | float | None = None, current_hd_price: Decimal | str | float | None = None) -> HDEconomicsAnalysis:
    rows = [dict(row) for row in transactions]
    espp_rows = [row for row in rows if row.get("transaction_type") == "ESPP_PURCHASE"]
    drip_rows = [row for row in rows if row.get("transaction_type") == "DIVIDEND_REINVESTMENT"]
    dividend_rows = [row for row in rows if row.get("transaction_type") == "DIVIDEND"]
    realized_rows = [row for row in rows if row.get("transaction_type") == "SALE" and row.get("source_account_type") == "Realized lot"]
    aggregate_sale_rows = [row for row in rows if row.get("transaction_type") == "SALE" and row.get("source_account_type") == "Brokerage"]

    total_employee_contributions = _sum_decimals(_decimal(row.get("amount")) for row in espp_rows)
    total_espp_shares = _sum_decimals(_decimal(row.get("shares")) for row in espp_rows)
    espp_purchase_dates = _first_last_dates(espp_rows, "transaction_date")
    weighted_purchase_price_numerator = _sum_decimals(((_decimal(row.get("shares")) or Decimal("0")) * (_decimal(row.get("purchase_price")) or Decimal("0"))) for row in espp_rows if _decimal(row.get("shares")) is not None and _decimal(row.get("purchase_price")) is not None)
    weighted_purchase_price_denominator = _sum_decimals(_decimal(row.get("shares")) for row in espp_rows if _decimal(row.get("purchase_price")) is not None)
    weighted_average_purchase_price = weighted_purchase_price_numerator / weighted_purchase_price_denominator if weighted_purchase_price_denominator else Decimal("0")

    contribution_issues: list[str] = []
    contribution_status = "OK"
    for row in espp_rows:
        shares = _decimal(row.get("shares"))
        employee = _decimal(row.get("amount"))
        purchase_price = _decimal(row.get("purchase_price"))
        if shares is None or employee is None or purchase_price is None:
            continue
        purchase_cost = shares * purchase_price
        difference = employee - purchase_cost
        if abs(difference) > CONTRIBUTION_TOLERANCE:
            contribution_issues.append(f"ESPP purchase on {row.get('transaction_date')} differs by {difference} from shares × price; review required")
            contribution_status = "REVIEW_REQUIRED"

    purchase_discount_rows: list[dict[str, Any]] = []
    discount_total_value = Decimal("0")
    discount_total_market_value = Decimal("0")
    complete_fmv_rows = 0
    missing_fmv_or_price_rows = 0
    for row in espp_rows:
        shares = _decimal(row.get("shares"))
        price = _decimal(row.get("purchase_price"))
        fmv = _decimal(row.get("fair_market_value"))
        employee = _decimal(row.get("amount"))
        if shares is None or price is None or fmv is None:
            missing_fmv_or_price_rows += 1
            continue
        market_value_at_purchase = shares * fmv
        employee_purchase_cost = shares * price
        discount_value = market_value_at_purchase - employee_purchase_cost
        discount_pct = (fmv - price) / fmv if fmv else Decimal("0")
        purchase_discount_rows.append({
            "transaction_date": row.get("transaction_date"),
            "shares": shares,
            "employee_contribution": employee,
            "purchase_price": price,
            "fair_market_value": fmv,
            "purchase_cost": employee_purchase_cost,
            "discount_value": discount_value,
            "discount_pct": discount_pct,
            "contribution_difference": (employee - employee_purchase_cost) if employee is not None else None,
            "status": "OK" if employee is not None and abs(employee - employee_purchase_cost) <= CONTRIBUTION_TOLERANCE else "REVIEW_REQUIRED",
        })
        discount_total_value += discount_value
        discount_total_market_value += market_value_at_purchase
        complete_fmv_rows += 1
    weighted_effective_discount_pct = discount_total_value / discount_total_market_value if discount_total_market_value else Decimal("0")

    drip_event_count = len(drip_rows)
    total_drip_fees = _sum_decimals(_decimal(row.get("fees")) for row in drip_rows)
    total_drip_taxes = _sum_decimals(_decimal(row.get("taxes")) for row in drip_rows)
    total_net_reinvested = _sum_decimals(_decimal(row.get("amount")) for row in drip_rows)
    total_drip_shares = _sum_decimals(_decimal(row.get("shares")) for row in drip_rows)
    drip_price_numerator = _sum_decimals(((_decimal(row.get("shares")) or Decimal("0")) * (_decimal(row.get("purchase_price")) or Decimal("0"))) for row in drip_rows if _decimal(row.get("shares")) is not None and _decimal(row.get("purchase_price")) is not None)
    drip_price_denominator = _sum_decimals(_decimal(row.get("shares")) for row in drip_rows if _decimal(row.get("purchase_price")) is not None)
    weighted_average_drip_price = drip_price_numerator / drip_price_denominator if drip_price_denominator else Decimal("0")

    drip_detail: list[dict[str, Any]] = []
    drip_issues: list[str] = []
    gross_dividend_known_count = 0
    gross_dividend_missing_count = 0
    total_gross_dividend = Decimal("0")
    for row in drip_rows:
        shares = _decimal(row.get("shares"))
        amount = _decimal(row.get("amount"))
        fees = _decimal(row.get("fees"))
        taxes = _decimal(row.get("taxes"))
        gross, gross_known = _extract_gross_dividend(row)
        if gross_known:
            gross_dividend_known_count += 1
            total_gross_dividend += gross or Decimal("0")
        else:
            gross_dividend_missing_count += 1
        net_reinvested = amount if amount is not None else (gross or Decimal("0")) - (fees or Decimal("0")) - (taxes or Decimal("0"))
        price = _decimal(row.get("purchase_price"))
        reconciliation_delta = Decimal("0") if gross is None else ((gross - (fees or Decimal("0")) - (taxes or Decimal("0"))) - (net_reinvested or Decimal("0")))
        share_purchase_value = (shares * price) if shares is not None and price is not None else None
        purchase_value_delta = None if share_purchase_value is None or net_reinvested is None else share_purchase_value - net_reinvested
        purchase_value_tolerance = _drip_purchase_value_tolerance(net_reinvested)
        source_reconciliation_exception = gross is not None and abs(reconciliation_delta) > DRIP_TOLERANCE
        purchase_value_exception = purchase_value_delta is not None and abs(purchase_value_delta) > purchase_value_tolerance
        if source_reconciliation_exception:
            status = "SOURCE_RECONCILIATION_EXCEPTION"
        elif purchase_value_exception:
            status = "SOURCE_ECONOMICS_REVIEW"
        else:
            status = "OK"
        drip_detail.append({
            "transaction_date": row.get("transaction_date"),
            "gross_dividend": gross,
            "fees": fees,
            "taxes": taxes,
            "net_reinvested": net_reinvested,
            "shares_acquired": shares,
            "purchase_price": price,
            "share_purchase_value": share_purchase_value,
            "gross_reconciliation_delta": reconciliation_delta,
            "purchase_value_delta": purchase_value_delta,
            "purchase_value_tolerance": purchase_value_tolerance,
            "reconciliation_status": status,
        })
        if source_reconciliation_exception:
            drip_issues.append(f"DRIP row on {row.get('transaction_date')} source gross/fee/tax/net values do not reconcile; source delta {reconciliation_delta}")
        if purchase_value_exception:
            drip_issues.append(f"DRIP row on {row.get('transaction_date')} shares × purchase price differs from documented reinvested amount by {purchase_value_delta}")

    dividend_total = _sum_decimals(_decimal(row.get("amount")) for row in dividend_rows)
    dividend_event_count = len(dividend_rows)

    known_basis_rows = [row for row in realized_rows if _decimal(row.get("cost_basis")) is not None and _decimal(row.get("amount")) is not None]
    known_basis_proceeds = _sum_decimals(_decimal(row.get("amount")) for row in known_basis_rows)
    documented_basis_total = _sum_decimals(_decimal(row.get("cost_basis")) for row in known_basis_rows)
    known_basis_shares = _sum_decimals(_decimal(row.get("shares")) for row in known_basis_rows)
    documented_realized_gain = _sum_decimals(((_decimal(row.get("amount")) or Decimal("0")) - (_decimal(row.get("cost_basis")) or Decimal("0"))) for row in known_basis_rows)
    realized_return_on_basis = documented_realized_gain / documented_basis_total if documented_basis_total else Decimal("0")

    total_sale_proceeds = _sum_decimals(_decimal(row.get("amount")) for row in aggregate_sale_rows if _decimal(row.get("amount")) is not None)
    total_shares_sold = _sum_decimals(_decimal(row.get("shares")) for row in aggregate_sale_rows if _decimal(row.get("shares")) is not None)
    if total_shares_sold == Decimal("0"):
        total_shares_sold = _sum_decimals(_decimal(row.get("shares")) for row in realized_rows if _decimal(row.get("shares")) is not None)
    if total_sale_proceeds == Decimal("0"):
        total_sale_proceeds = known_basis_proceeds

    known_basis_lot_count = len(known_basis_rows)
    total_realized_lot_count = len(realized_rows)
    share_basis_coverage_pct = known_basis_shares / total_shares_sold if total_shares_sold else Decimal("0")
    proceeds_coverage_pct = known_basis_proceeds / total_sale_proceeds if total_sale_proceeds else Decimal("0")
    lot_basis_coverage_pct = Decimal(known_basis_lot_count) / Decimal(total_realized_lot_count) if total_realized_lot_count else Decimal("0")

    sale_fingerprint_map = {str(sold_lot.sale_fingerprint): sold_lot for sold_lot in (ownership.sold_lots if ownership is not None else [])}
    realized_detail: list[dict[str, Any]] = []
    for row in realized_rows:
        sale_fingerprint = str(row.get("transaction_fingerprint") or "")
        sold_lot = sale_fingerprint_map.get(sale_fingerprint)
        shares = _decimal(row.get("shares"))
        sale_amount = _decimal(row.get("amount"))
        basis = _decimal(row.get("cost_basis"))
        gain = None if sale_amount is None or basis is None else sale_amount - basis
        realized_detail.append({
            "sale_date": row.get("transaction_date"),
            "acquisition_date": row.get("acquisition_date"),
            "origin": sold_lot.origin_type if sold_lot is not None else "UNRESOLVED",
            "shares_sold": shares,
            "sale_proceeds": sale_amount,
            "documented_basis": basis,
            "documented_realized_gain": gain,
            "gain_pct": None if gain is None or basis in (None, Decimal("0")) else (gain / basis),
            "status": "Known basis" if basis is not None else "Unknown basis",
        })

    realized_by_origin: dict[str, Any] = {"records": [], "origin_coverage_pct": Decimal("0"), "known_origin_sold_shares": Decimal("0"), "unresolved_origin_sold_shares": Decimal("0")}
    if ownership is not None:
        origin_summary: dict[str, dict[str, Any]] = {}
        for sold_lot in ownership.sold_lots:
            origin = sold_lot.origin_type or "UNRESOLVED"
            bucket = origin_summary.setdefault(origin, {
                "shares_sold": Decimal("0"),
                "proceeds": Decimal("0"),
                "sale_proceeds": Decimal("0"),
                "documented_cost_basis": Decimal("0"),
                "documented_realized_gain": Decimal("0"),
                "known_basis_rows": 0,
                "known_basis_lot_count": 0,
                "unknown_basis_lot_count": 0,
                "total_lot_count": 0,
                "known_basis_shares": Decimal("0"),
                "unknown_basis_shares": Decimal("0"),
                "status": "Unknown / unresolved" if origin in {"UNRESOLVED", "UNKNOWN"} else "Known origin",
                "provenance_status": "UNRESOLVED" if origin in {"UNRESOLVED", "UNKNOWN"} else "KNOWN",
            })
            shares = sold_lot.shares_sold
            sale_amount = sold_lot.sale_amount
            basis = sold_lot.cost_basis
            bucket["total_lot_count"] += 1
            if shares is not None:
                bucket["shares_sold"] += shares
            if sale_amount is not None:
                bucket["proceeds"] += sale_amount
                bucket["sale_proceeds"] += sale_amount
            if basis is not None:
                bucket["documented_cost_basis"] += basis
                bucket["known_basis_rows"] += 1
                bucket["known_basis_lot_count"] += 1
                if shares is not None:
                    bucket["known_basis_shares"] += shares
            else:
                bucket["unknown_basis_lot_count"] += 1
                if shares is not None:
                    bucket["unknown_basis_shares"] += shares
            if sale_amount is not None and basis is not None:
                bucket["documented_realized_gain"] += sale_amount - basis
        for key, value in sorted(origin_summary.items()):
            if value["known_basis_shares"] == Decimal("0"):
                value["basis_status"] = "UNKNOWN"
                value["documented_cost_basis"] = None
                value["documented_realized_gain"] = None
                value["documented_realized_gain_pct"] = None
            elif value["unknown_basis_shares"] > Decimal("0"):
                value["basis_status"] = "PARTIAL"
                value["documented_realized_gain_pct"] = (value["documented_realized_gain"] / value["documented_cost_basis"]) if value["documented_cost_basis"] else None
            else:
                value["basis_status"] = "COMPLETE"
                value["documented_realized_gain_pct"] = (value["documented_realized_gain"] / value["documented_cost_basis"]) if value["documented_cost_basis"] else None
            value["basis_coverage_pct"] = (value["known_basis_shares"] / value["shares_sold"]) if value["shares_sold"] and value["known_basis_shares"] else Decimal("0")
            realized_by_origin["records"].append({"origin": key, **value})
        known_origin_sold_shares = _sum_decimals((entry["shares_sold"] for entry in realized_by_origin["records"] if entry["origin"] not in {"UNRESOLVED", "UNKNOWN"}))
        unresolved_origin_sold_shares = total_shares_sold - known_origin_sold_shares if total_shares_sold else Decimal("0")
        realized_by_origin["known_origin_sold_shares"] = known_origin_sold_shares
        realized_by_origin["unresolved_origin_sold_shares"] = unresolved_origin_sold_shares
        realized_by_origin["origin_coverage_pct"] = known_origin_sold_shares / total_shares_sold if total_shares_sold else Decimal("0")
    else:
        realized_by_origin["records"] = []

    open_lot_economic_cost = {
        "lifetime_documented_acquisition_cash": Decimal("0"),
        "open_lot_economic_cash": Decimal("0"),
        "economic_employee_cash_invested": Decimal("0"),
        "economic_reinvested_cash": Decimal("0"),
    }
    if ownership is not None:
        total_life_employee = _sum_decimals((lot.employee_contribution for lot in ownership.lots if lot.origin_type == "ESPP_PURCHASE" and lot.employee_contribution is not None))
        total_life_reinvested = _sum_decimals((lot.reinvested_dividend_amount for lot in ownership.lots if lot.origin_type == "DIVIDEND_REINVESTMENT" and lot.reinvested_dividend_amount is not None))
        open_employee = Decimal("0")
        for lot in ownership.lots:
            if lot.origin_type == "ESPP_PURCHASE" and lot.employee_contribution is not None and lot.original_shares > Decimal("0"):
                open_employee += lot.employee_contribution * (lot.remaining_shares / lot.original_shares)
        open_drip = Decimal("0")
        for lot in ownership.lots:
            if lot.origin_type == "DIVIDEND_REINVESTMENT" and lot.reinvested_dividend_amount is not None and lot.original_shares > Decimal("0"):
                open_drip += lot.reinvested_dividend_amount * (lot.remaining_shares / lot.original_shares)
        open_lot_economic_cost["lifetime_documented_acquisition_cash"] = total_life_employee + total_life_reinvested
        open_lot_economic_cost["economic_employee_cash_invested"] = open_employee
        open_lot_economic_cost["economic_reinvested_cash"] = open_drip
        open_lot_economic_cost["open_lot_economic_cash"] = open_employee + open_drip

    issues: list[str] = []
    if ownership is not None and ownership.summary.get("reconciliation_status") == "DIFFERENCE_REQUIRES_REVIEW":
        issues.append("Historical analytics are based on documented reconstructed transactions. The reconstructed remaining-share balance does not currently match the live HD holding, so lifetime/open-position return metrics are intentionally withheld.")
    issues.extend(contribution_issues)
    issues.extend(drip_issues)
    if not purchase_discount_rows and espp_rows:
        issues.append("No ESPP purchase rows contained both purchase price and fair market value; discount benefit is unavailable.")

    coverage = {
        "total_shares_sold": total_shares_sold,
        "shares_with_documented_basis": known_basis_shares,
        "shares_without_documented_basis": (total_shares_sold - known_basis_shares) if total_shares_sold else Decimal("0"),
        "share_basis_coverage_pct": share_basis_coverage_pct,
        "realized_gain_coverage_pct": share_basis_coverage_pct,
        "proceeds_coverage_pct": proceeds_coverage_pct,
        "lot_basis_coverage_pct": lot_basis_coverage_pct,
        "known_origin_sold_shares": realized_by_origin.get("known_origin_sold_shares", Decimal("0")),
        "unresolved_origin_sold_shares": realized_by_origin.get("unresolved_origin_sold_shares", Decimal("0")),
        "origin_coverage_pct": realized_by_origin.get("origin_coverage_pct", Decimal("0")),
        "total_sale_proceeds": total_sale_proceeds,
        "known_basis_proceeds": known_basis_proceeds,
    }

    reconciliation = {
        "contribution_reconciliation_status": contribution_status,
        "contribution_reconciliation_issues": len(contribution_issues),
        "drip_reconciliation_status": "OK" if all((row["reconciliation_status"] == "OK") for row in drip_detail) else "REVIEW_REQUIRED",
        "drip_reconciliation_issues": len(drip_issues),
        "drip_source_exception_count": sum(row["reconciliation_status"] == "SOURCE_RECONCILIATION_EXCEPTION" for row in drip_detail),
        "drip_economics_review_count": sum(row["reconciliation_status"] == "SOURCE_ECONOMICS_REVIEW" for row in drip_detail),
        "live_reconciliation_status": ownership.summary.get("reconciliation_status") if ownership is not None else "UNAVAILABLE",
    }

    current_snapshot = {
        "current_hd_market_price": current_hd_price,
        "current_live_hd_shares": live_hd_shares,
        "current_hd_market_value": (Decimal(str(live_hd_shares)) * Decimal(str(current_hd_price))) if live_hd_shares is not None and current_hd_price is not None else None,
        "label": "Current snapshot (read-only live portfolio data)" if live_hd_shares is not None or current_hd_price is not None else "Current snapshot unavailable",
    }

    analysis = HDEconomicsAnalysis(
        espp_summary={
            "total_employee_contributions": total_employee_contributions,
            "purchase_count": len(espp_rows),
            "total_espp_shares": total_espp_shares,
            "weighted_average_purchase_price": weighted_average_purchase_price,
            "average_employee_contribution_per_purchase": (total_employee_contributions / len(espp_rows)) if espp_rows else Decimal("0"),
            "first_espp_purchase_date": espp_purchase_dates[0],
            "latest_espp_purchase_date": espp_purchase_dates[1],
            "purchase_price_completeness_count": len([row for row in espp_rows if _decimal(row.get("purchase_price")) is not None]),
            "missing_purchase_price_count": sum(1 for row in espp_rows if _decimal(row.get("purchase_price")) is None),
            "missing_purchase_price_rows": [row.get("transaction_date") for row in espp_rows if _decimal(row.get("purchase_price")) is None],
        },
        discount_summary={
            "purchases_with_complete_fmv_and_price": complete_fmv_rows,
            "purchases_missing_fmv_or_price": missing_fmv_or_price_rows,
            "total_discount_value": discount_total_value,
            "weighted_effective_discount_pct": weighted_effective_discount_pct,
            "per_purchase_discount": purchase_discount_rows,
        },
        drip_summary={
            "event_count": drip_event_count,
            "total_gross_dividend": total_gross_dividend,
            "total_dividend_fees": total_drip_fees,
            "total_dividend_taxes": total_drip_taxes,
            "total_net_reinvested": total_net_reinvested,
            "total_drip_shares": total_drip_shares,
            "weighted_average_drip_price": weighted_average_drip_price,
            "purchase_price_completeness_count": len([row for row in drip_rows if _decimal(row.get("purchase_price")) is not None]),
            "missing_purchase_price_count": sum(1 for row in drip_rows if _decimal(row.get("purchase_price")) is None),
            "gross_dividend_known_count": gross_dividend_known_count,
            "gross_dividend_missing_count": gross_dividend_missing_count,
        },
        dividend_summary={
            "cash_dividend_event_count": dividend_event_count,
            "total_documented_cash_dividends": dividend_total,
            "coverage_label": "Documented in available statements",
        },
        realized_summary={
            "total_realized_lot_count": total_realized_lot_count,
            "known_basis_lot_count": known_basis_lot_count,
            "known_basis_shares": known_basis_shares,
            "known_basis_proceeds": known_basis_proceeds,
            "documented_basis_total": documented_basis_total,
            "documented_realized_gain": documented_realized_gain,
            "realized_return_on_basis": realized_return_on_basis,
            "unknown_basis_lot_count": len(realized_rows) - len(known_basis_rows),
            "unknown_basis_shares": _sum_decimals(_decimal(row.get("shares")) for row in realized_rows if _decimal(row.get("cost_basis")) is None),
            "unknown_basis_proceeds": _sum_decimals(_decimal(row.get("amount")) for row in realized_rows if _decimal(row.get("cost_basis")) is None),
            "total_sale_proceeds": total_sale_proceeds,
        },
        realized_by_origin=realized_by_origin,
        coverage=coverage,
        reconciliation=reconciliation,
        issues=issues,
        per_purchase_discount=purchase_discount_rows,
        drip_detail=drip_detail,
        realized_detail=realized_detail,
        open_lot_economic_cost=open_lot_economic_cost,
        current_snapshot=current_snapshot,
    )
    return analysis


__all__ = ["HDEconomicsAnalysis", "analyze_hd_economics"]

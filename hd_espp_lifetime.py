"""Phase 3B source-bounded HD/ESPP lifetime documented economics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from hd_espp_analytics import HDEconomicsAnalysis
from hd_espp_reconciliation import HDPhase3AReconciliation
from hd_ownership import LIVE_TOLERANCE, OwnershipReconstruction


PERFORMANCE_AVAILABLE = "AVAILABLE"
PERFORMANCE_PARTIAL = "PARTIAL"
PERFORMANCE_WITHHELD = "WITHHELD"


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


def _sum_decimals(values: Iterable[Decimal | None]) -> Decimal:
    total = Decimal("0")
    for value in values:
        if value is not None:
            total += value
    return total


@dataclass
class HDLifetimeEconomics:
    summary: dict[str, Any]
    cash_flows: list[dict[str, Any]]
    cash_flow_summary: dict[str, Any]
    espp_lifetime: dict[str, Any]
    drip_lifetime: dict[str, Any]
    dividend_lifetime: dict[str, Any]
    sales_lifetime: dict[str, Any]
    realized_by_origin: list[dict[str, Any]]
    open_economic_cost: dict[str, Any]
    coverage: dict[str, Any]
    performance_gating: list[dict[str, Any]]
    issues: list[dict[str, Any]] = field(default_factory=list)


def _gross_drip_from_detail(phase2: HDEconomicsAnalysis, transaction_date: Any) -> Decimal | None:
    for detail in phase2.drip_detail:
        if str(detail.get("transaction_date")) == str(transaction_date):
            return _decimal(detail.get("gross_dividend"))
    return None


def _cash_flow(event_date: Any, event_type: str, origin: str, amount: Decimal, shares: Decimal | None, source: Any, source_document: Any, notes: str) -> dict[str, Any]:
    return {
        "event_date": _date(event_date) or event_date,
        "event_type": event_type,
        "origin": origin,
        "cash_direction": "INFLOW" if amount > 0 else "OUTFLOW" if amount < 0 else "NON_CASH",
        "amount": amount,
        "shares": shares,
        "source": source,
        "source_document": source_document,
        "economic_effect": amount,
        "tax_basis_effect": "NOT_CALCULATED",
        "confidence_coverage": "Documented in available statements",
        "notes": notes,
    }


def build_lifetime_cash_flows(transactions: Iterable[dict[str, Any]], phase2: HDEconomicsAnalysis) -> list[dict[str, Any]]:
    cash_flows: list[dict[str, Any]] = []
    for row in sorted((dict(item) for item in transactions), key=lambda item: (str(item.get("transaction_date")), str(item.get("transaction_fingerprint")))):
        kind = row.get("transaction_type")
        account_type = row.get("source_account_type")
        amount = _decimal(row.get("amount"))
        shares = _decimal(row.get("shares"))
        source = row.get("source")
        source_document = row.get("source_document")
        event_date = row.get("transaction_date")
        if kind == "ESPP_PURCHASE" and amount is not None:
            cash_flows.append(_cash_flow(event_date, "ESPP_CONTRIBUTION", "ESPP_PURCHASE", -abs(amount), shares, source, source_document, "Employee ESPP contribution; not tax basis"))
        elif kind == "DIVIDEND_REINVESTMENT":
            gross = _gross_drip_from_detail(phase2, event_date)
            fees = _decimal(row.get("fees")) or Decimal("0")
            taxes = _decimal(row.get("taxes")) or Decimal("0")
            if gross is not None:
                cash_flows.append(_cash_flow(event_date, "DRIP_DIVIDEND", "DIVIDEND_REINVESTMENT", gross, shares, source, source_document, "Gross documented DRIP dividend income"))
            if amount is not None:
                cash_flows.append(_cash_flow(event_date, "DRIP_REINVESTMENT", "DIVIDEND_REINVESTMENT", -abs(amount), shares, source, source_document, "Documented DRIP reinvestment outflow"))
            if fees:
                cash_flows.append(_cash_flow(event_date, "FEES", "DIVIDEND_REINVESTMENT", -abs(fees), None, source, source_document, "Documented DRIP fee"))
            if taxes:
                cash_flows.append(_cash_flow(event_date, "TAX_WITHHOLDING", "DIVIDEND_REINVESTMENT", -abs(taxes), None, source, source_document, "Documented DRIP tax withholding"))
        elif kind == "DIVIDEND" and amount is not None:
            cash_flows.append(_cash_flow(event_date, "CASH_DIVIDEND", "CASH_DIVIDEND", amount, None, source, source_document, "Documented Merrill cash dividend"))
        elif kind == "SALE" and account_type == "Brokerage" and amount is not None:
            cash_flows.append(_cash_flow(event_date, "SALE_PROCEEDS", "AGGREGATE_SALE", amount, shares, source, source_document, "Aggregate Merrill sale proceeds; realized-lot rows are detail only"))
    return cash_flows


def _performance_metric(metric: str, status: str, coverage: str, reason: str, value: Any = None, reasons: list[str] | None = None) -> dict[str, Any]:
    return {
        "metric": metric,
        "status": status,
        "coverage": coverage,
        "reason": reason,
        "value": value if status != PERFORMANCE_WITHHELD else None,
        "gating_reasons": reasons or [],
    }


def _build_performance_gating(phase2: HDEconomicsAnalysis, phase3a: HDPhase3AReconciliation) -> list[dict[str, Any]]:
    phase3a_summary = phase3a.summary
    remaining_difference = _decimal(phase3a_summary.get("remaining_unexplained_difference"))
    phase3a_categories = list(phase3a_summary.get("reconciliation_categories") or [])
    withheld_reasons = ["HISTORICAL_LIVE_RECONCILIATION_INCOMPLETE", *phase3a_categories]
    if phase2.coverage.get("share_basis_coverage_pct") != Decimal("1"):
        withheld_reasons.append("INCOMPLETE_COST_BASIS_COVERAGE")
    if phase2.coverage.get("origin_coverage_pct") != Decimal("1"):
        withheld_reasons.append("UNRESOLVED_SALE_PROVENANCE")
    withheld_reasons = list(dict.fromkeys(withheld_reasons))
    lifetime_blocked = remaining_difference is not None and abs(remaining_difference) > LIVE_TOLERANCE

    discount_status = PERFORMANCE_AVAILABLE if phase2.discount_summary.get("purchases_missing_fmv_or_price") == 0 else PERFORMANCE_PARTIAL
    realized_status = PERFORMANCE_AVAILABLE if phase2.coverage.get("share_basis_coverage_pct") == Decimal("1") else PERFORMANCE_PARTIAL
    dividend_status = PERFORMANCE_PARTIAL if phase3a_summary.get("phase3a_status") != "RECONCILED_TO_LIVE" else PERFORMANCE_AVAILABLE
    net_cash_status = PERFORMANCE_PARTIAL if phase3a_summary.get("phase3a_status") != "RECONCILED_TO_LIVE" else PERFORMANCE_AVAILABLE
    total_return_status = PERFORMANCE_WITHHELD if lifetime_blocked else PERFORMANCE_PARTIAL
    total_return_reason = "Unexplained historical/live share difference remains" if lifetime_blocked else "Still source-bounded; review coverage before promoting"

    return [
        _performance_metric("ESPP Purchase Discount Benefit", discount_status, "Source-reported FMV and purchase price", "Source values from ESPP purchase rows", phase2.discount_summary.get("total_discount_value")),
        _performance_metric("Documented Realized Gain", realized_status, f"{phase2.coverage.get('share_basis_coverage_pct')} share-basis coverage", "Known-basis Merrill realized-lot rows only", phase2.realized_summary.get("documented_realized_gain"), ["INCOMPLETE_COST_BASIS_COVERAGE"] if realized_status == PERFORMANCE_PARTIAL else []),
        _performance_metric("Documented Dividend Economics", dividend_status, "Documented in available statements", "Cash dividends and DRIP gross dividends are source-bounded", None),
        _performance_metric("Documented Net Cash Flow", net_cash_status, "Documented cash movements in available statements", "Not total return", None),
        _performance_metric("Lifetime Total Return", total_return_status, "Historical/live reconciliation required", total_return_reason, None, withheld_reasons if lifetime_blocked else []),
        _performance_metric("IRR", PERFORMANCE_WITHHELD, "Complete lifetime cash-flow/current-position reconciliation required", "Historical/live reconciliation is incomplete", None, withheld_reasons),
        _performance_metric("XIRR", PERFORMANCE_WITHHELD, "Complete dated cash-flow/current-position reconciliation required", "Historical/live reconciliation is incomplete", None, withheld_reasons),
        _performance_metric("Lifetime CAGR", PERFORMANCE_WITHHELD, "Complete lifetime total return required", "Historical/live reconciliation is incomplete", None, withheld_reasons),
        _performance_metric("Current Unrealized Return From Historical Lots", PERFORMANCE_WITHHELD, "Historical lots must reconcile to live holding", "Live HD holding remains authoritative", None, withheld_reasons),
        _performance_metric("Total Current Value Of Reconstructed Historical Ownership", PERFORMANCE_WITHHELD, "Would value unreconciled historical shares", "Do not present reconstructed shares as current holdings", None, withheld_reasons),
    ]


def build_hd_lifetime_economics(transactions: Iterable[dict[str, Any]], ownership: OwnershipReconstruction, phase2: HDEconomicsAnalysis, phase3a: HDPhase3AReconciliation, current_live_hd_shares: Decimal | str | float | None = None, current_hd_price: Decimal | str | float | None = None) -> HDLifetimeEconomics:
    rows = [dict(row) for row in transactions]
    espp_rows = [row for row in rows if row.get("transaction_type") == "ESPP_PURCHASE"]
    fmv_total = _sum_decimals(((_decimal(row.get("shares")) or Decimal("0")) * (_decimal(row.get("fair_market_value")) or Decimal("0")) for row in espp_rows if _decimal(row.get("shares")) is not None and _decimal(row.get("fair_market_value")) is not None))
    cash_flows = build_lifetime_cash_flows(rows, phase2)
    total_inflows = _sum_decimals(_decimal(row.get("amount")) for row in cash_flows if (_decimal(row.get("amount")) or Decimal("0")) > 0)
    total_outflows = _sum_decimals(abs(_decimal(row.get("amount")) or Decimal("0")) for row in cash_flows if (_decimal(row.get("amount")) or Decimal("0")) < 0)
    net_cash_flow = _sum_decimals(_decimal(row.get("amount")) for row in cash_flows)
    lifetime_cash = phase2.open_lot_economic_cost.get("lifetime_documented_acquisition_cash", Decimal("0"))
    open_cash = phase2.open_lot_economic_cost.get("open_lot_economic_cash", Decimal("0"))
    sold_consumed_cash = lifetime_cash - open_cash
    performance_gating = _build_performance_gating(phase2, phase3a)
    issues: list[dict[str, Any]] = []
    if phase2.reconciliation.get("drip_source_exception_count", 0):
        issues.append({"issue_type": "SOURCE_DRIP_RECONCILIATION_EXCEPTION", "severity": "REVIEW", "message": f"{phase2.reconciliation['drip_source_exception_count']} DRIP source reconciliation exception remains documented."})
    if phase2.coverage.get("share_basis_coverage_pct") != Decimal("1"):
        issues.append({"issue_type": "INCOMPLETE_BASIS_COVERAGE", "severity": "INFO", "message": "Documented realized gain covers known-basis lots only."})
    if phase2.coverage.get("origin_coverage_pct") != Decimal("1"):
        issues.append({"issue_type": "INCOMPLETE_ORIGIN_COVERAGE", "severity": "INFO", "message": "Some sold shares retain unresolved origin provenance."})
    if phase3a.summary.get("phase3a_status") != "RECONCILED_TO_LIVE":
        issues.append({"issue_type": "HISTORICAL_LIVE_RECONCILIATION_INCOMPLETE", "severity": "REVIEW", "message": "Phase 3A historical/live reconciliation remains incomplete."})
    if any(row["metric"] == "Lifetime Total Return" and row["status"] == PERFORMANCE_WITHHELD for row in performance_gating):
        issues.append({"issue_type": "LIFETIME_RETURN_WITHHELD", "severity": "INFO", "message": "Complete lifetime return metrics are withheld until historical/live reconciliation is complete."})

    espp_lifetime = {
        **phase2.espp_summary,
        "total_source_reported_fmv_at_purchase_value": fmv_total,
        "total_purchase_discount_benefit": phase2.discount_summary.get("total_discount_value", Decimal("0")),
        "weighted_effective_discount_pct": phase2.discount_summary.get("weighted_effective_discount_pct", Decimal("0")),
        "complete_fmv_purchase_count": phase2.discount_summary.get("purchases_with_complete_fmv_and_price", 0),
        "incomplete_fmv_purchase_count": phase2.discount_summary.get("purchases_missing_fmv_or_price", 0),
        "coverage_label": "Source-reported values; not assumed fixed discount",
    }
    drip_lifetime = {
        **phase2.drip_summary,
        "source_reconciliation_exception_count": phase2.reconciliation.get("drip_source_exception_count", 0),
        "source_economics_review_count": phase2.reconciliation.get("drip_economics_review_count", 0),
        "parser_failure_count": 0,
        "coverage_label": "Documented/reconstructable DRIP values in available statements",
    }
    dividend_lifetime = {
        "documented_cash_dividend_event_count": phase2.dividend_summary.get("cash_dividend_event_count", 0),
        "total_documented_cash_dividends": phase2.dividend_summary.get("total_documented_cash_dividends", Decimal("0")),
        "total_documented_dividend_cash_flows_in_available_statements": (phase2.dividend_summary.get("total_documented_cash_dividends", Decimal("0")) or Decimal("0")) + (phase2.drip_summary.get("total_gross_dividend", Decimal("0")) or Decimal("0")),
        "coverage_label": "Documented in available statements; not complete lifetime dividends",
    }
    sales_lifetime = {
        **phase2.realized_summary,
        "aggregate_shares_sold": phase2.coverage.get("total_shares_sold", Decimal("0")),
        "aggregate_documented_sale_proceeds": phase2.realized_summary.get("total_sale_proceeds", Decimal("0")),
        "shares_with_documented_basis": phase2.coverage.get("shares_with_documented_basis", Decimal("0")),
        "shares_without_documented_basis": phase2.coverage.get("shares_without_documented_basis", Decimal("0")),
        "proceeds_associated_with_documented_basis_lots": phase2.realized_summary.get("known_basis_proceeds", Decimal("0")),
        "proceeds_associated_with_unknown_basis_lots": phase2.realized_summary.get("unknown_basis_proceeds", Decimal("0")),
        "coverage_label": "Aggregate sale counted once; realized lots provide basis/origin detail",
    }
    open_economic_cost = {
        **phase2.open_lot_economic_cost,
        "sold_or_consumed_economic_acquisition_cash": sold_consumed_cash,
        "unknown_unavailable_economic_acquisition_cash": None,
        "unknown_unavailable_shares": ownership.summary.get("total_other_unknown_shares", Decimal("0")) + ownership.summary.get("total_restricted_shares", Decimal("0")),
        "coverage_label": "Economic acquisition cash only; not tax basis",
    }
    coverage = {
        **phase2.coverage,
        "phase3a_status": phase3a.summary.get("phase3a_status"),
        "phase3a_reconciliation_categories": phase3a.summary.get("reconciliation_categories"),
        "historical_reconstructed_shares": phase3a.summary.get("historical_reconstructed_shares"),
        "live_hd_shares": phase3a.summary.get("live_hd_shares"),
        "remaining_unexplained_difference": phase3a.summary.get("remaining_unexplained_difference"),
        "basis_status": "PARTIAL" if phase2.coverage.get("share_basis_coverage_pct") != Decimal("1") else "AVAILABLE",
        "origin_status": "PARTIAL" if phase2.coverage.get("origin_coverage_pct") != Decimal("1") else "AVAILABLE",
    }
    summary = {
        "total_employee_cash_contributed": phase2.espp_summary.get("total_employee_contributions", Decimal("0")),
        "total_documented_drip_cash_reinvested": phase2.drip_summary.get("total_net_reinvested", Decimal("0")),
        "total_documented_cash_dividends": phase2.dividend_summary.get("total_documented_cash_dividends", Decimal("0")),
        "total_documented_sale_proceeds": phase2.realized_summary.get("total_sale_proceeds", Decimal("0")),
        "documented_realized_gain_known_basis": phase2.realized_summary.get("documented_realized_gain", Decimal("0")),
        "espp_purchase_discount_benefit": phase2.discount_summary.get("total_discount_value", Decimal("0")),
        "documented_economic_benefit_components": (phase2.discount_summary.get("total_discount_value", Decimal("0")) or Decimal("0")) + (phase2.realized_summary.get("documented_realized_gain", Decimal("0")) or Decimal("0")) + (phase2.dividend_summary.get("total_documented_cash_dividends", Decimal("0")) or Decimal("0")) + (phase2.drip_summary.get("total_gross_dividend", Decimal("0")) or Decimal("0")),
        "cash_flow_sign_convention": "Employee/out-of-pocket investment is negative; cash received is positive.",
        "lifetime_total_return_status": next(row["status"] for row in performance_gating if row["metric"] == "Lifetime Total Return"),
        "current_live_hd_shares": _decimal(current_live_hd_shares),
        "current_hd_price": _decimal(current_hd_price),
        "current_live_hd_market_value": (_decimal(current_live_hd_shares) * _decimal(current_hd_price)) if _decimal(current_live_hd_shares) is not None and _decimal(current_hd_price) is not None else None,
        "phase3a_status": phase3a.summary.get("phase3a_status"),
    }
    cash_flow_summary = {
        "total_documented_cash_inflows": total_inflows,
        "total_documented_cash_outflows": total_outflows,
        "net_documented_cash_flow": net_cash_flow,
        "total_employee_cash_contributed": phase2.espp_summary.get("total_employee_contributions", Decimal("0")),
        "total_documented_dividend_cash_received": dividend_lifetime["total_documented_dividend_cash_flows_in_available_statements"],
        "total_documented_drip_cash_reinvested": phase2.drip_summary.get("total_net_reinvested", Decimal("0")),
        "total_documented_sale_proceeds": phase2.realized_summary.get("total_sale_proceeds", Decimal("0")),
        "total_documented_fees": phase2.drip_summary.get("total_dividend_fees", Decimal("0")),
        "total_documented_taxes": phase2.drip_summary.get("total_dividend_taxes", Decimal("0")),
        "coverage_label": "Documented cash movement, not total return",
    }
    return HDLifetimeEconomics(
        summary=summary,
        cash_flows=cash_flows,
        cash_flow_summary=cash_flow_summary,
        espp_lifetime=espp_lifetime,
        drip_lifetime=drip_lifetime,
        dividend_lifetime=dividend_lifetime,
        sales_lifetime=sales_lifetime,
        realized_by_origin=list(phase2.realized_by_origin.get("records", [])),
        open_economic_cost=open_economic_cost,
        coverage=coverage,
        performance_gating=performance_gating,
        issues=issues,
    )


__all__ = ["HDLifetimeEconomics", "build_hd_lifetime_economics", "build_lifetime_cash_flows"]

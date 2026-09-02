"""Shared gated HD historical integration payload for reporting surfaces."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from hd_espp_analytics import analyze_hd_economics
from hd_espp_history import HDESPPHistoryService, HD_HISTORY_DB_PATH
from hd_espp_lifetime import build_hd_lifetime_economics
from hd_espp_reconciliation import build_hd_phase3a_reconciliation
from hd_ownership import reconstruct_hd_ownership


LOGGER = logging.getLogger("portfolio_tracker")
SAFE_UNAVAILABLE_REASON = "HD historical context is temporarily unavailable"


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _hd_live_values(metrics: pd.DataFrame | None) -> tuple[Decimal | None, Decimal | None]:
    if not isinstance(metrics, pd.DataFrame) or metrics.empty or "ticker" not in metrics:
        return None, None
    rows = metrics[metrics["ticker"].astype(str).str.upper().eq("HD")]
    if rows.empty:
        return None, None
    row = rows.iloc[0]
    shares = _decimal(row.get("shares"))
    price = _decimal(row.get("current_price"))
    return shares, price


def _performance_status(lifetime: dict[str, Any], metric: str) -> dict[str, Any]:
    for row in lifetime.get("performance_gating", []):
        if row.get("metric") == metric:
            return dict(row)
    return {"metric": metric, "status": "WITHHELD", "value": None, "reason": "Unavailable", "gating_reasons": ["HD_HISTORICAL_DATA_UNAVAILABLE"]}


def unavailable_hd_historical_context(reason: str = "No HD historical data imported") -> dict[str, Any]:
    return {
        "available": False,
        "summary": {
            "hd_historical_data_available": False,
            "reason": reason,
            "hd_historical_document_count": 0,
            "normalized_transaction_count": 0,
        },
        "documented_economics": {},
        "realized": {"by_origin": []},
        "coverage": {},
        "reconciliation": {},
        "performance_availability": [],
        "issues": [],
    }


def build_hd_historical_integration_context(state: dict[str, Any], metrics: pd.DataFrame | None = None) -> dict[str, Any]:
    transactions = list(state.get("transactions") or [])
    summary = dict(state.get("summary") or {})
    if not transactions:
        return unavailable_hd_historical_context("No HD historical transactions are available")

    live_shares, current_price = _hd_live_values(metrics)
    ownership = reconstruct_hd_ownership(transactions, live_shares, state.get("transfer_lots") or [])
    phase2 = analyze_hd_economics(transactions, ownership, live_shares, current_price)
    phase3a = build_hd_phase3a_reconciliation(transactions, state.get("transfer_lots") or [], state.get("documents") or [], state.get("position_checkpoints") or [], ownership, live_shares)
    lifetime = build_hd_lifetime_economics(transactions, ownership, phase2, phase3a, live_shares, current_price)
    lifetime_total_return = _performance_status({"performance_gating": lifetime.performance_gating}, "Lifetime Total Return")
    irr = _performance_status({"performance_gating": lifetime.performance_gating}, "IRR")
    xirr = _performance_status({"performance_gating": lifetime.performance_gating}, "XIRR")

    return {
        "available": True,
        "summary": {
            "hd_historical_data_available": True,
            "hd_historical_document_count": summary.get("documents", 0),
            "normalized_transaction_count": len(transactions),
            "phase3a_status": phase3a.summary.get("phase3a_status"),
            "lifetime_return_status": lifetime_total_return.get("status"),
            "irr_status": irr.get("status"),
            "xirr_status": xirr.get("status"),
        },
        "documented_economics": {
            "espp_employee_contributions": lifetime.espp_lifetime.get("total_employee_contributions"),
            "espp_purchase_count": lifetime.espp_lifetime.get("purchase_count"),
            "espp_shares": lifetime.espp_lifetime.get("total_espp_shares"),
            "weighted_average_espp_purchase_price": lifetime.espp_lifetime.get("weighted_average_purchase_price"),
            "espp_purchase_discount_benefit": lifetime.espp_lifetime.get("total_purchase_discount_benefit"),
            "effective_documented_discount_pct": lifetime.espp_lifetime.get("weighted_effective_discount_pct"),
            "drip_event_count": lifetime.drip_lifetime.get("event_count"),
            "drip_gross_dividends": lifetime.drip_lifetime.get("total_gross_dividend"),
            "drip_fees": lifetime.drip_lifetime.get("total_dividend_fees"),
            "drip_taxes": lifetime.drip_lifetime.get("total_dividend_taxes"),
            "drip_net_reinvested": lifetime.drip_lifetime.get("total_net_reinvested"),
            "drip_shares": lifetime.drip_lifetime.get("total_drip_shares"),
            "documented_cash_dividend_count": lifetime.dividend_lifetime.get("documented_cash_dividend_event_count"),
            "documented_cash_dividend_total": lifetime.dividend_lifetime.get("total_documented_cash_dividends"),
            "documented_sale_proceeds": lifetime.sales_lifetime.get("aggregate_documented_sale_proceeds"),
            "documented_realized_gain": lifetime.sales_lifetime.get("documented_realized_gain"),
        },
        "realized": {
            "aggregate_shares_sold": lifetime.sales_lifetime.get("aggregate_shares_sold"),
            "aggregate_sale_proceeds": lifetime.sales_lifetime.get("aggregate_documented_sale_proceeds"),
            "shares_with_documented_basis": lifetime.sales_lifetime.get("shares_with_documented_basis"),
            "shares_without_documented_basis": lifetime.sales_lifetime.get("shares_without_documented_basis"),
            "documented_basis_total": lifetime.sales_lifetime.get("documented_basis_total"),
            "documented_realized_gain": lifetime.sales_lifetime.get("documented_realized_gain"),
            "by_origin": list(lifetime.realized_by_origin),
        },
        "coverage": {
            "basis_coverage_pct": lifetime.coverage.get("share_basis_coverage_pct"),
            "origin_coverage_pct": lifetime.coverage.get("origin_coverage_pct"),
            "basis_status": lifetime.coverage.get("basis_status"),
            "origin_status": lifetime.coverage.get("origin_status"),
        },
        "reconciliation": {
            "historical_reconstructed_shares": phase3a.summary.get("historical_reconstructed_shares"),
            "live_hd_shares": phase3a.summary.get("live_hd_shares"),
            "historical_live_difference": phase3a.summary.get("raw_historical_live_difference"),
            "remaining_unexplained_difference": phase3a.summary.get("remaining_unexplained_difference"),
            "phase3a_status": phase3a.summary.get("phase3a_status"),
            "reconciliation_categories": phase3a.summary.get("reconciliation_categories"),
        },
        "performance_availability": list(lifetime.performance_gating),
        "issues": list(lifetime.issues),
    }


def load_hd_historical_integration_context(metrics: pd.DataFrame | None = None, db_path: Path = HD_HISTORY_DB_PATH) -> dict[str, Any]:
    path = Path(db_path)
    if not path.is_file():
        return unavailable_hd_historical_context("HD history database is unavailable")
    try:
        service = HDESPPHistoryService(db_path=path)
        return build_hd_historical_integration_context(service.state(), metrics)
    except Exception:
        LOGGER.exception("HD historical integration context loading failed")
        return unavailable_hd_historical_context(SAFE_UNAVAILABLE_REASON)


def compact_hd_historical_context(context: dict[str, Any]) -> dict[str, Any]:
    if not context.get("available"):
        return {"available": False, "reason": (context.get("summary") or {}).get("reason", "No HD historical data imported")}
    economics = context.get("documented_economics", {})
    realized = context.get("realized", {})
    coverage = context.get("coverage", {})
    reconciliation = context.get("reconciliation", {})
    summary = context.get("summary", {})
    return {
        "available": True,
        "source_bounded": True,
        "espp_employee_contributions": economics.get("espp_employee_contributions"),
        "espp_shares": economics.get("espp_shares"),
        "purchase_discount_benefit": economics.get("espp_purchase_discount_benefit"),
        "documented_realized_gain": realized.get("documented_realized_gain"),
        "basis_coverage_pct": coverage.get("basis_coverage_pct"),
        "origin_coverage_pct": coverage.get("origin_coverage_pct"),
        "historical_live_difference": reconciliation.get("historical_live_difference"),
        "phase3a_status": reconciliation.get("phase3a_status"),
        "lifetime_return_status": summary.get("lifetime_return_status"),
        "withheld_metrics": [row.get("metric") for row in context.get("performance_availability", []) if row.get("status") == "WITHHELD"],
        "issue_types": [row.get("issue_type") for row in context.get("issues", []) if isinstance(row, dict)],
        "realized_by_origin": [
            {
                "origin": row.get("origin"),
                "shares_sold": row.get("shares_sold"),
                "documented_realized_gain": row.get("documented_realized_gain"),
                "basis_status": row.get("basis_status"),
                "basis_coverage_pct": row.get("basis_coverage_pct"),
                "provenance_status": row.get("provenance_status"),
            }
            for row in context.get("realized", {}).get("by_origin", [])
        ],
    }


__all__ = [
    "build_hd_historical_integration_context",
    "compact_hd_historical_context",
    "load_hd_historical_integration_context",
    "unavailable_hd_historical_context",
    "SAFE_UNAVAILABLE_REASON",
]

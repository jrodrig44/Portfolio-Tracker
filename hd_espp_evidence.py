"""Authoritative HD custody and realized-gain evidence supplied by the account owner."""
from __future__ import annotations
from decimal import Decimal
from typing import Any

D = Decimal
MARCH_2025_TRANSFER = (("0.021341", "292.6040"), ("0.000132", "378.2174"), ("0.000110", "406.7922"), ("3.978417", "330.6415"))
JULY_2025_TRANSFER = (("0.255776", "330.6415"), ("3.744224", "311.6440"))
JULY_2024_COMPONENTS = tuple(map(D, ("4.763283", "5.302147", "0.055252", "4.752731", "0.032549", "0.063428")))

def _basis(parts: tuple[tuple[str, str], ...]) -> Decimal:
    return sum((D(shares) * D(unit_basis) for shares, unit_basis in parts), D("0"))

def authoritative_hd_reconciliation() -> dict[str, Any]:
    """Return source-supported current balances without inferred plug transactions."""
    equate = D("5.572451") + D("0.038210") + D("0.037960") + D("4.670123")
    merrill = D("38") - D("28") + D("11") - D("9")
    first_basis, second_basis = _basis(MARCH_2025_TRANSFER), _basis(JULY_2025_TRANSFER)
    broker_gain = D("1822.48")
    # Merrill's N/A basis can be reconstructed, but remains explicitly Equate-derived.
    reconstructed_gain = broker_gain + D("1567.46") * 2 - first_basis - second_basis
    return {
        "as_of": "2026-06-30/current evidence",
        "custody": {"equate_shares": equate, "merrill_shares": merrill, "combined_beneficial_shares": equate + merrill},
        "transfers": {"successful_shares": D("8"), "failed_shares_ignored": D("5"), "march_2025_shares": sum((D(q) for q, _ in MARCH_2025_TRANSFER), D("0")), "july_2025_shares": sum((D(q) for q, _ in JULY_2025_TRANSFER), D("0")), "july_2025_purchase_remainder": D("4.492305") - D("3.744224"), "july_2024_component_total": sum(JULY_2024_COMPONENTS, D("0"))},
        "realized": {"broker_reported_realized_gain": broker_gain, "reconstructed_realized_gain": reconstructed_gain, "february_transfer_lot_basis": first_basis + second_basis, "basis_provenance": "reconstructed_from_equate_allocations", "broker_basis_provenance": "merrill_reported", "february_basis_coverage_pct": D("1"), "remaining_unknown_basis_shares": D("0"), "june_2026": {"shares": D("9"), "gross_proceeds": D("2819.52"), "fees": D("0.06"), "net_proceeds": D("2819.46"), "basis": D("3160.35"), "realized_gain_loss": D("-340.89")}},
        "unresolved_quantity": D("0"), "merrill_chain": (D("38"), D("-28"), D("11"), D("-9")), "equate_chain": (D("5.572451"), D("0.038210"), D("0.037960"), D("4.670123")),
    }

__all__ = ["JULY_2024_COMPONENTS", "JULY_2025_TRANSFER", "MARCH_2025_TRANSFER", "authoritative_hd_reconciliation"]

"""Load owner-supplied normalized HD evidence that is not present in imported PDFs."""
from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

EVIDENCE_PATH = Path(__file__).resolve().parent / "data" / "hd_espp_evidence.json"

def _convert(value: Any) -> Any:
    if isinstance(value, dict): return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, list): return [_convert(item) for item in value]
    if isinstance(value, str) and value.startswith("decimal:"): return Decimal(value[8:])
    return value

def load_hd_evidence(path: Path = EVIDENCE_PATH) -> dict[str, Any]:
    """Read editable, source-labelled normalized evidence without deriving balances."""
    return _convert(json.loads(path.read_text(encoding="utf-8")))

def merge_hd_evidence(state: dict[str, Any], path: Path = EVIDENCE_PATH) -> dict[str, Any]:
    """Merge evidence into parser state and link known transfer receipts."""
    evidence, merged = load_hd_evidence(path), dict(state)
    transactions = [dict(row) for row in state.get("transactions", [])]
    # A receipt explicitly linked to an Equate transfer changes custody, not ownership.
    for link in evidence.get("transfer_links", []):
        for row in transactions:
            shares = Decimal(str(row.get("shares") or "0"))
            if row.get("transaction_type") == "TRANSFER_IN" and row.get("transaction_date") == link["receipt_date"] and shares == link["shares"]:
                row["transfer_match_id"], row["source_location"] = link["transfer_id"], "EquatePlus"
    fingerprints = {str(row.get("transaction_fingerprint")) for row in transactions}
    economic_keys = {(row.get("transaction_type"), row.get("transaction_date"), Decimal(str(row.get("shares") or "0")), row.get("source_account_type")) for row in transactions}
    # Owner evidence may overlap a parsed PDF row; economic identity wins over filename/fingerprint.
    for row in evidence.get("transactions", []):
        key = (row.get("transaction_type"), row.get("transaction_date"), Decimal(str(row.get("shares") or "0")), row.get("source_account_type"))
        if str(row.get("transaction_fingerprint")) not in fingerprints and key not in economic_keys:
            transactions.append(row)
            economic_keys.add(key)
    merged["transactions"] = transactions
    merged["transfer_lots"] = list(state.get("transfer_lots", [])) + list(evidence.get("transfer_lots", []))
    merged["position_checkpoints"] = list(state.get("position_checkpoints", [])) + list(evidence.get("position_checkpoints", []))
    # Failed transfers are retained for audit but excluded from active transactions.
    merged["failed_transfers"], merged["evidence"] = evidence.get("failed_transfers", []), evidence
    return merged

__all__ = ["EVIDENCE_PATH", "load_hd_evidence", "merge_hd_evidence"]

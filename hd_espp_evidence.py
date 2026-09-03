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

def _same_economic_event(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Match cross-source economic identity, not merely file fingerprints."""
    if left.get("transaction_type") != right.get("transaction_type") or left.get("source_account_type") != right.get("source_account_type"):
        return False
    shares_equal = Decimal(str(left.get("shares") or "0")) == Decimal(str(right.get("shares") or "0"))
    if not shares_equal: return False
    kind = left.get("transaction_type")
    if kind in {"ESPP_PURCHASE", "DIVIDEND_REINVESTMENT"}:
        fields = ("transaction_date", "amount", "purchase_price")
        values_match = all(str(left.get(field) or "") == str(right.get(field) or "") for field in fields)
        left_source, right_source = str(left.get("source") or ""), str(right.get("source") or "")
        return values_match and (("Equate" in left_source or "Computershare" in left_source) == ("Equate" in right_source or "Computershare" in right_source))
    if kind == "SALE":
        from datetime import date
        try: date_gap = abs((date.fromisoformat(str(left.get("transaction_date"))) - date.fromisoformat(str(right.get("transaction_date")))).days)
        except ValueError: return False
        if date_gap > 1 or "Merrill" not in f"{left.get('source')} {right.get('source')}": return False
        left_amount, right_amount = Decimal(str(left.get("amount") or "0")), Decimal(str(right.get("amount") or "0"))
        left_fee, right_fee = Decimal(str(left.get("fees") or "0")), Decimal(str(right.get("fees") or "0"))
        # Trade-date and settlement-date rows may report gross versus net; fee bridges them.
        amount_matches = left_amount == right_amount or left_amount + left_fee == right_amount or right_amount + right_fee == left_amount
        lot_fields_match = str(left.get("acquisition_date") or "") == str(right.get("acquisition_date") or "") and str(left.get("lot_sequence") or "") == str(right.get("lot_sequence") or "")
        return amount_matches and lot_fields_match
    return False

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
    # Evidence can overlap a PDF on trade versus settlement date, while two real
    # same-day trades must remain separate; use type-specific economics, not a broad tuple.
    for row in evidence.get("transactions", []):
        if str(row.get("transaction_fingerprint")) not in fingerprints and not any(_same_economic_event(row, existing) for existing in transactions):
            transactions.append(row)
    merged["transactions"] = transactions
    merged["transfer_lots"] = list(state.get("transfer_lots", [])) + list(evidence.get("transfer_lots", []))
    merged["position_checkpoints"] = list(state.get("position_checkpoints", [])) + list(evidence.get("position_checkpoints", []))
    # Failed transfers are retained for audit but excluded from active transactions.
    merged["failed_transfers"], merged["evidence"] = evidence.get("failed_transfers", []), evidence
    return merged

__all__ = ["EVIDENCE_PATH", "_same_economic_event", "load_hd_evidence", "merge_hd_evidence"]

from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from hd_espp_history import parse_hd_position_checkpoints
from hd_ownership import reconstruct_hd_ownership
from hd_espp_reconciliation import (
    build_hd_phase3a_reconciliation,
    build_share_bridge,
    build_statement_coverage,
    reconcile_position_checkpoints,
)


def tx(fingerprint: str, kind: str, tx_date: str, shares: str, **values):
    return {
        "transaction_fingerprint": fingerprint,
        "transaction_type": kind,
        "transaction_date": tx_date,
        "shares": Decimal(shares),
        "source": values.pop("source", "Computershare"),
        "source_location": values.pop("source_location", "Computershare"),
        "source_account_type": values.pop("source_account_type", "ESPP"),
        "source_document": values.pop("source_document", "doc.pdf"),
        **values,
    }


class HDPhase3AReconciliationTests(unittest.TestCase):
    def test_statement_coverage_orders_gaps_overlaps_and_no_activity(self) -> None:
        documents = [
            {"source_document": "b.pdf", "document_family": "merrill_hd", "period_start": "2024-03-01", "period_end": "2024-03-31"},
            {"source_document": "a.pdf", "document_family": "computershare_legacy", "period_start": "2024-01-01", "period_end": "2024-01-31"},
            {"source_document": "c.pdf", "document_family": "computershare_equateplus", "period_start": "2024-03-15", "period_end": "2024-04-15"},
            {"source_document": "d.pdf", "document_family": "merrill_hd", "period_start": "2024-05-01", "period_end": "2024-05-31"},
        ]
        transactions = [tx("a", "ESPP_PURCHASE", "2024-01-15", "1", source_document="a.pdf")]

        timeline, gaps, overlaps = build_statement_coverage(documents, transactions)

        self.assertEqual([row["source_document"] for row in timeline], ["a.pdf", "b.pdf", "c.pdf", "d.pdf"])
        self.assertEqual(timeline[0]["coverage_status"], "DOCUMENT_COVERAGE_WITH_ACTIVITY")
        self.assertEqual(timeline[1]["coverage_status"], "DOCUMENT_COVERAGE_WITH_NO_ACTIVITY")
        self.assertEqual(gaps[0]["coverage_status"], "NO_DOCUMENT_COVERAGE")
        self.assertIn("does not prove there was no activity", gaps[0]["notes"])
        self.assertEqual(overlaps[0]["left_source"], "Merrill")
        self.assertEqual(overlaps[0]["right_source"], "EquatePlus / Computershare")

    def test_coverage_gaps_use_merged_intervals_not_adjacent_documents(self) -> None:
        documents = [
            {"source_document": "long.pdf", "document_family": "computershare_legacy", "period_start": "2024-01-01", "period_end": "2024-06-30", "parsed_transaction_occurrence_count": 1},
            {"source_document": "nested.pdf", "document_family": "merrill_hd", "period_start": "2024-03-01", "period_end": "2024-03-31", "parsed_transaction_occurrence_count": 1},
            {"source_document": "contiguous.pdf", "document_family": "merrill_hd", "period_start": "2024-07-01", "period_end": "2024-09-30", "parsed_transaction_occurrence_count": 1},
            {"source_document": "later.pdf", "document_family": "computershare_equateplus", "period_start": "2024-10-15", "period_end": "2024-12-31", "parsed_transaction_occurrence_count": 1},
        ]

        _, gaps, overlaps = build_statement_coverage(documents, [])
        global_gaps = [gap for gap in gaps if gap["scope"] == "GLOBAL_HD"]

        self.assertEqual(global_gaps, [{
            "issue_type": "STATEMENT_COVERAGE_GAP",
            "scope": "GLOBAL_HD",
            "gap_start": __import__("datetime").date(2024, 10, 1),
            "gap_end": __import__("datetime").date(2024, 10, 14),
            "coverage_status": "NO_DOCUMENT_COVERAGE",
            "notes": "No global_hd source document covers this interval; this does not prove there was no activity.",
        }])
        self.assertTrue(any(row["left_source"] == "Computershare" and row["right_source"] == "Merrill" for row in overlaps))

    def test_source_occurrence_metadata_prevents_canonical_dedup_false_inactivity(self) -> None:
        documents = [
            {"source_document": "canonical-owner.pdf", "document_family": "computershare_legacy", "period_start": "2024-01-01", "period_end": "2024-03-31", "parsed_transaction_occurrence_count": 1, "earliest_parsed_transaction_date": "2024-01-15", "latest_parsed_transaction_date": "2024-01-15", "parsed_transaction_types": "ESPP_PURCHASE"},
            {"source_document": "cumulative-repeat.pdf", "document_family": "computershare_legacy", "period_start": "2024-01-01", "period_end": "2024-06-30", "parsed_transaction_occurrence_count": 1, "earliest_parsed_transaction_date": "2024-01-15", "latest_parsed_transaction_date": "2024-01-15", "parsed_transaction_types": "ESPP_PURCHASE"},
        ]
        canonical_transactions = [tx("same", "ESPP_PURCHASE", "2024-01-15", "1", source_document="canonical-owner.pdf")]

        timeline, _, _ = build_statement_coverage(documents, canonical_transactions)
        repeat = next(row for row in timeline if row["source_document"] == "cumulative-repeat.pdf")

        self.assertEqual(repeat["coverage_status"], "DOCUMENT_COVERAGE_WITH_ACTIVITY")
        self.assertEqual(repeat["transactions"], 1)
        self.assertEqual(repeat["canonical_transactions"], 0)

    def test_position_checkpoint_parsing_and_reconciliation_statuses(self) -> None:
        legacy = """The Home Depot, Inc ESPP - Employee Plan Statement
01 Jan 2024 - 31 Mar 2024
The Home Depot Inc ESPP 10.000000 0.500000 10.500000 300.00 3,150.00
"""
        merrill = """Merrill Lynch
January 01, 2026 - February 27, 2026
HOME DEPOT INC HD 5.0000 1,000.00 300.0000 1,500.00 500.00 99
"""
        legacy_checkpoint = parse_hd_position_checkpoints(legacy, "legacy.pdf")[0]
        merrill_checkpoint = parse_hd_position_checkpoints(merrill, "merrill.pdf")[0]

        self.assertEqual(legacy_checkpoint.shares, Decimal("10.500000"))
        self.assertEqual(legacy_checkpoint.custodian, "Computershare")
        self.assertEqual(merrill_checkpoint.shares, Decimal("5.0000"))
        self.assertEqual(merrill_checkpoint.custodian, "Merrill")

        rows = [tx("buy", "ESPP_PURCHASE", "2026-01-01", "5", source="Merrill", source_location="Merrill", source_account_type="Brokerage")]
        checkpoints = [
            {"checkpoint_date": "2026-02-27", "custodian": "Merrill", "shares": "5.0000", "source_document": "merrill.pdf", "confidence": "HIGH"},
            {"checkpoint_date": "2026-02-28", "custodian": "Merrill", "shares": "5.0004", "source_document": "rounding.pdf", "confidence": "HIGH"},
            {"checkpoint_date": "2026-03-01", "custodian": "Merrill", "shares": "4.9000", "source_document": "mismatch.pdf", "confidence": "HIGH"},
        ]
        original = deepcopy(rows)
        detail, _, _, _, latest_documented_date, latest_documented_position = reconcile_position_checkpoints(rows, [], checkpoints)

        self.assertEqual([row["status"] for row in detail], ["MATCHED", "WITHIN_SOURCE_ROUNDING", "DIFFERENCE_REQUIRES_REVIEW"])
        self.assertEqual(latest_documented_date.isoformat(), "2026-03-01")
        self.assertEqual(latest_documented_position, Decimal("4.9000"))
        self.assertEqual(rows, original)

    def test_duplicate_checkpoints_do_not_double_count_and_conflicts_are_explicit(self) -> None:
        rows = [tx("buy", "ESPP_PURCHASE", "2026-01-01", "5", source="Merrill", source_location="Merrill", source_account_type="Brokerage")]
        duplicate_checkpoints = [
            {"checkpoint_date": "2026-02-27", "custodian": "Merrill", "shares": "5.0000", "source_document": "a.pdf", "source_documents": "a.pdf,b.pdf", "occurrence_count": 2, "confidence": "HIGH"},
        ]
        detail, totals, last_date, last_position, _, _ = reconcile_position_checkpoints(rows, [], duplicate_checkpoints)

        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["occurrence_count"], 2)
        self.assertEqual(totals[0]["documented_position_shares"], Decimal("5.0000"))
        self.assertEqual(last_date.isoformat(), "2026-02-27")
        self.assertEqual(last_position, Decimal("5.0000"))

        conflicting = duplicate_checkpoints + [{"checkpoint_date": "2026-02-27", "custodian": "Merrill", "shares": "4.7500", "source_document": "conflict.pdf", "confidence": "HIGH"}]
        conflict_detail, conflict_totals, _, _, _, _ = reconcile_position_checkpoints(rows, [], conflicting)

        self.assertTrue(all(row["status"] == "POSITION_CHECKPOINT_CONFLICT" for row in conflict_detail))
        self.assertEqual(conflict_totals[0]["status"], "POSITION_CHECKPOINT_CONFLICT")

    def test_partial_custodian_coverage_is_not_fully_reconciled(self) -> None:
        rows = [
            tx("plan", "ESPP_PURCHASE", "2026-01-01", "1"),
            tx("brokerage", "ESPP_PURCHASE", "2026-01-01", "5", source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
        ]
        checkpoints = [{"checkpoint_date": "2026-02-27", "custodian": "Merrill", "shares": "5", "source_document": "merrill.pdf", "confidence": "HIGH"}]
        _, totals, last_date, last_position, latest_date, latest_position = reconcile_position_checkpoints(rows, [], checkpoints)

        self.assertEqual(totals[0]["status"], "PARTIAL_CUSTODIAN_COVERAGE")
        self.assertIsNone(last_date)
        self.assertIsNone(last_position)
        self.assertEqual(latest_date.isoformat(), "2026-02-27")
        self.assertEqual(latest_position, Decimal("5"))

    def test_share_bridge_counts_acquisitions_sales_and_zero_effect_transfers(self) -> None:
        rows = [
            tx("espp", "ESPP_PURCHASE", "2024-01-01", "5", amount=Decimal("100")),
            tx("drip", "DIVIDEND_REINVESTMENT", "2024-02-01", "0.5", amount=Decimal("10")),
            tx("restricted", "ADJUSTMENT", "2024-03-01", "10", source="Merrill", source_location="Restricted share award", source_account_type="Brokerage"),
            tx("unknown", "TRANSFER_IN", "2024-04-01", "4", source="Merrill", source_location="External", source_account_type="Brokerage"),
            tx("out", "TRANSFER_OUT", "2024-05-01", "5", transfer_match_id="move", destination_location="Merrill"),
            tx("in", "TRANSFER_IN", "2024-05-02", "5", source="Merrill", source_location="Computershare", destination_location="Merrill", source_account_type="Brokerage", transfer_match_id="move"),
            tx("sale", "SALE", "2024-06-01", "6", source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
        ]
        ownership = reconstruct_hd_ownership(rows)
        bridge = build_share_bridge(ownership)
        by_component = {row["component"]: row for row in bridge}

        self.assertEqual(by_component["ESPP Acquisitions"]["shares"], Decimal("5"))
        self.assertEqual(by_component["DRIP Acquisitions"]["shares"], Decimal("0.5"))
        self.assertEqual(by_component["Restricted Shares"]["shares"], Decimal("10"))
        self.assertEqual(by_component["Other/Unknown Acquisitions"]["shares"], Decimal("4"))
        self.assertEqual(by_component["Custody Transfers"]["economic_effect"], Decimal("0"))
        self.assertEqual(by_component["Shares Sold"]["shares"], Decimal("-6"))
        self.assertEqual(by_component["Reconstructed Remaining"]["shares"], Decimal("13.5"))

    def test_unresolved_eight_share_sale_is_counted_once_and_not_auto_matched(self) -> None:
        rows = [
            tx("unknown-1", "TRANSFER_IN", "2025-03-31", "4", source="Merrill", source_location="External", source_account_type="Brokerage"),
            tx("unknown-2", "TRANSFER_IN", "2025-07-21", "4", source="Merrill", source_location="External", source_account_type="Brokerage"),
            tx("sale-total", "SALE", "2026-02-13", "8", source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
            tx("na-1", "SALE", "2026-02-12", "4", source="Merrill", source_location="Merrill", source_account_type="Realized lot", acquisition_date=None, lot_sequence=1),
            tx("na-2", "SALE", "2026-02-12", "4", source="Merrill", source_location="Merrill", source_account_type="Realized lot", acquisition_date=None, lot_sequence=2),
        ]
        ownership = reconstruct_hd_ownership(rows)
        result = build_hd_phase3a_reconciliation(rows, [], [], [], ownership, Decimal("0"))

        self.assertEqual(ownership.summary["unresolved_sold_lot_shares"], Decimal("8"))
        self.assertEqual(ownership.summary["total_shares_sold"], Decimal("8"))
        self.assertEqual(result.unknown_acquisition_investigation["unknown_acquisition_shares"], Decimal("8"))
        self.assertEqual(result.unresolved_sale_investigation["unresolved_sold_shares"], Decimal("8"))
        self.assertFalse(result.unknown_acquisition_investigation["can_link_to_unresolved_sales"])
        self.assertEqual(result.unknown_acquisition_investigation["relationship_status"], "QUANTITY_MATCH_ONLY_INSUFFICIENT")
        self.assertIn("Quantity equality alone is insufficient", result.unknown_acquisition_investigation["evidence"])

    def test_live_reconciliation_preserves_historical_and_live_values_without_plug(self) -> None:
        rows = [tx("buy", "ESPP_PURCHASE", "2024-01-01", "10", amount=Decimal("100"))]
        documents = [{"source_document": "legacy.pdf", "document_family": "computershare_legacy", "period_start": "2024-01-01", "period_end": "2024-01-31"}]
        checkpoints = [{"checkpoint_date": "2024-01-31", "custodian": "Computershare", "shares": "10", "source_document": "legacy.pdf", "confidence": "HIGH"}]
        ownership = reconstruct_hd_ownership(rows, Decimal("8"))

        result = build_hd_phase3a_reconciliation(rows, [], documents, checkpoints, ownership, Decimal("8"))

        self.assertEqual(result.summary["historical_reconstructed_shares"], Decimal("10"))
        self.assertEqual(result.summary["live_hd_shares"], Decimal("8"))
        self.assertEqual(result.summary["raw_historical_live_difference"], Decimal("2"))
        self.assertEqual(result.summary["explained_difference"], Decimal("0"))
        self.assertEqual(result.summary["remaining_unexplained_difference"], Decimal("2"))
        self.assertEqual(result.summary["last_reconciled_historical_date"].isoformat(), "2024-01-31")
        self.assertEqual(result.summary["latest_documented_checkpoint_date"].isoformat(), "2024-01-31")
        self.assertEqual(result.summary["last_fully_reconciled_position_quantity"], Decimal("10"))
        self.assertFalse(result.summary["plug_transaction_created"])
        self.assertTrue(result.summary["lifetime_return_withheld"])
        self.assertTrue(any(issue["issue_type"] == "LIVE_POSITION_DIFFERENCE" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()

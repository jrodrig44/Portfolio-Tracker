from decimal import Decimal
import unittest
import pandas as pd
from hd_espp_evidence import _same_economic_event, load_hd_evidence, merge_hd_evidence
from hd_espp_integration import build_hd_historical_integration_context
from hd_espp_reconciliation import roll_forward_current_custody
from hd_ownership import reconstruct_hd_ownership

def tx(fp, kind, when, shares, **extra):
    return {"transaction_fingerprint":fp,"transaction_type":kind,"transaction_date":when,"shares":Decimal(shares),"source":extra.pop("source","Merrill"),"source_location":extra.pop("source_location","Merrill"),"source_account_type":extra.pop("source_account_type","Brokerage"),"source_document":"test evidence",**extra}

def state():
    rows=[tx("receipt-mar","TRANSFER_IN","2025-03-31","4.0000",source_location="External"),tx("receipt-jul","TRANSFER_IN","2025-07-21","4.0000",source_location="External"),tx("sale-feb","SALE","2026-02-13","28",amount=Decimal("10972.22")),tx("lapse","ADJUSTMENT","2026-03-20","11",source_location="Restricted share award")]
    return {"transactions":rows,"transfer_lots":[],"documents":[{"source_document":"test evidence","document_family":"merrill_hd","period_start":"2025-01-01","period_end":"2026-06-30"}],"position_checkpoints":[],"summary":{"documents":1}}

class AuthoritativeHDEvidenceTests(unittest.TestCase):
    def test_normalized_pipeline_reconciles_current_custody(self):
        context=build_hd_historical_integration_context(merge_hd_evidence(state()),pd.DataFrame([{"ticker":"HD","shares":12,"current_price":300}]))
        custody=context["reconciliation"]["current_custody"]
        self.assertEqual(custody["equate_shares"],Decimal("10.318744")); self.assertEqual(custody["merrill_shares"],Decimal("12")); self.assertEqual(custody["combined_beneficial_shares"],Decimal("22.318744")); self.assertEqual(custody["unresolved_quantity"],Decimal("0"))
        self.assertEqual(context["reconciliation"]["historical_reconstructed_shares"],Decimal("22.318744")); self.assertEqual(context["reconciliation"]["historical_live_difference"],Decimal("0")); self.assertEqual(context["reconciliation"]["phase3a_status"],"RECONCILED"); self.assertNotEqual(context["reconciliation"]["historical_reconstructed_shares"],Decimal("25.800646"))

    def test_receipts_are_custody_moves_and_failed_transfer_is_inactive(self):
        merged=merge_hd_evidence(state()); receipts=[row for row in merged["transactions"] if row["transaction_type"]=="TRANSFER_IN"]
        self.assertEqual({row["transfer_match_id"] for row in receipts},{"TV14250327000038","TV14250720000079"})
        ownership=reconstruct_hd_ownership(merged["transactions"],transfer_lots=merged["transfer_lots"])
        self.assertEqual(ownership.summary["unknown_origin_acquired_shares"],Decimal("0")); self.assertFalse(any(row.get("transaction_date")=="2026-02-18" for row in merged["transactions"])); self.assertEqual(merged["failed_transfers"][0]["status"],"Payment failed")

    def test_basis_and_realized_values_flow_from_normalized_rows(self):
        context=build_hd_historical_integration_context(merge_hd_evidence(state())); realized=context["realized"]
        self.assertEqual(realized["broker_reported_realized_gain"],Decimal("1822.48")); self.assertEqual(realized["february_transfer_lot_basis"],Decimal("2573.2040028683")); self.assertEqual(realized["reconstructed_realized_gain"],Decimal("2384.1959971317")); self.assertEqual(realized["basis_provenance"],"reconstructed_from_equate_allocations"); self.assertEqual(realized["june_2026"]["realized_gain_loss"],Decimal("-340.89"))
        self.assertEqual(realized["june_2026"],{"shares":Decimal("9"),"gross_proceeds":Decimal("2819.52"),"fees":Decimal("0.06"),"net_proceeds":Decimal("2819.46"),"basis":Decimal("3160.35"),"realized_gain_loss":Decimal("-340.89")}); self.assertEqual(context["coverage"]["basis_coverage_pct"],Decimal("1")); self.assertEqual(context["coverage"]["basis_status"],"COMPLETE")

    def test_transfer_components_are_detail_not_duplicate_transactions(self):
        evidence=load_hd_evidence(); self.assertEqual(sum((row["transferred_shares"] for row in evidence["transfer_lots"][:4]),Decimal()),Decimal("4.000000")); self.assertEqual(sum((row["transferred_shares"] for row in evidence["transfer_lots"][4:]),Decimal()),Decimal("4.000000")); self.assertEqual(sum(row["transaction_type"]=="TRANSFER_OUT" for row in evidence["transactions"]),2)

    def test_checkpoints_do_not_hide_unsupported_or_unmatched_events(self):
        checkpoints=[{"checkpoint_date":"2026-01-01","custodian":"Computershare","shares":"10"},{"checkpoint_date":"2026-01-01","custodian":"Merrill","shares":"12"}]
        unsupported=roll_forward_current_custody([tx("admin","ADJUSTMENT","2026-02-01","3",source_location="administrative correction")],checkpoints)
        unmatched=roll_forward_current_custody([tx("move","TRANSFER_IN","2026-02-01","4",source_location="External")],checkpoints)
        self.assertFalse(unsupported["reconciliation_complete"]); self.assertEqual(unsupported["unresolved_quantity"],Decimal("3")); self.assertFalse(unmatched["reconciliation_complete"]); self.assertEqual(unmatched["unresolved_quantity"],Decimal("4"))

    def test_failed_transfer_is_explicit_zero_effect_and_realized_detail_is_ignored(self):
        checkpoints=[{"checkpoint_date":"2026-01-01","custodian":"Computershare","shares":"10"},{"checkpoint_date":"2026-01-01","custodian":"Merrill","shares":"21"}]
        rows=[tx("aggregate","SALE","2026-06-04","9",amount=Decimal("2819.46")),tx("detail","SALE","2026-06-04","9",amount=Decimal("2819.46"),source_account_type="Realized lot")]
        result=roll_forward_current_custody(rows,checkpoints,[{"transaction_date":"2026-02-18","shares":Decimal("5"),"status":"Payment failed"}])
        self.assertTrue(result["reconciliation_complete"]); self.assertEqual(result["merrill_shares"],Decimal("12")); self.assertEqual(result["ignored_detail_event_count"],2); self.assertEqual(result["unresolved_quantity"],Decimal("0"))

    def test_trade_settlement_dedup_is_precise(self):
        trade=tx("trade","SALE","2026-06-04","9",amount=Decimal("2819.46"),fees=Decimal("0.06"))
        settlement=tx("settle","SALE","2026-06-05","9",amount=Decimal("2819.52"))
        separate=tx("other","SALE","2026-06-04","9",amount=Decimal("2800.00"),fees=Decimal("0.06"))
        self.assertTrue(_same_economic_event(trade,settlement)); self.assertFalse(_same_economic_event(trade,separate))

    def test_only_proven_restricted_adjustment_is_acquisition(self):
        checkpoints=[{"checkpoint_date":"2026-01-01","custodian":"Computershare","shares":"10"},{"checkpoint_date":"2026-01-01","custodian":"Merrill","shares":"10"}]
        known=roll_forward_current_custody([tx("lapse","ADJUSTMENT","2026-03-20","11",source_location="Restricted share award")],checkpoints)
        unknown=roll_forward_current_custody([tx("admin","ADJUSTMENT","2026-03-20","11",source_location="Administrative")],checkpoints)
        self.assertEqual(known["merrill_shares"],Decimal("21")); self.assertTrue(known["reconciliation_complete"]); self.assertEqual(unknown["merrill_shares"],Decimal("10")); self.assertFalse(unknown["reconciliation_complete"])

if __name__=="__main__": unittest.main()

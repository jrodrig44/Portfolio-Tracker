from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from hd_espp_analytics import analyze_hd_economics
from hd_espp_lifetime import build_hd_lifetime_economics
from hd_espp_reconciliation import build_hd_phase3a_reconciliation
from hd_ownership import reconstruct_hd_ownership


def tx(fingerprint: str, kind: str, tx_date: str, shares: str | None = None, **values):
    row = {
        "transaction_fingerprint": fingerprint,
        "transaction_type": kind,
        "transaction_date": tx_date,
        "shares": Decimal(shares) if shares is not None else None,
        "source": values.pop("source", "Computershare"),
        "source_location": values.pop("source_location", "Computershare"),
        "source_account_type": values.pop("source_account_type", "ESPP"),
        "source_document": values.pop("source_document", "source.pdf"),
    }
    row.update(values)
    return row


class HDLifetimeEconomicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transactions = [
            tx("espp-1", "ESPP_PURCHASE", "2024-01-01", "10", amount=Decimal("80"), purchase_price=Decimal("8"), fair_market_value=Decimal("10"), source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
            tx("espp-2", "ESPP_PURCHASE", "2024-06-01", "5", amount=Decimal("50"), purchase_price=Decimal("10"), fair_market_value=None),
            tx("drip-1", "DIVIDEND_REINVESTMENT", "2025-03-12", "0.02693", amount=Decimal("9.79"), purchase_price=Decimal("358.05"), fees=Decimal("0.14"), taxes=Decimal("0.00"), notes="gross_dividend=9.78"),
            tx("cash-div", "DIVIDEND", "2025-06-01", None, amount=Decimal("35"), source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
            tx("transfer-out", "TRANSFER_OUT", "2025-07-01", "2", transfer_value=Decimal("700"), transfer_match_id="move", destination_location="Merrill"),
            tx("transfer-in", "TRANSFER_IN", "2025-07-02", "2", source="Merrill", source_location="Computershare", source_account_type="Brokerage", transfer_match_id="move", transfer_value=Decimal("700")),
            tx("restricted", "ADJUSTMENT", "2025-08-01", "4", source="Merrill", source_location="Restricted share award", source_account_type="Brokerage"),
            tx("sale-agg", "SALE", "2026-02-13", "8", amount=Decimal("1000"), source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
            tx("sale-lot-known", "SALE", "2026-02-12", "5", amount=Decimal("700"), cost_basis=Decimal("500"), acquisition_date="2024-01-01", lot_sequence=1, source="Merrill", source_location="Merrill", source_account_type="Realized lot"),
            tx("sale-lot-unknown", "SALE", "2026-02-12", "3", amount=Decimal("300"), cost_basis=None, acquisition_date=None, lot_sequence=2, source="Merrill", source_location="Merrill", source_account_type="Realized lot"),
        ]

    def _result(self, *, live_shares: Decimal = Decimal("4")):
        ownership = reconstruct_hd_ownership(self.transactions, live_shares)
        phase2 = analyze_hd_economics(self.transactions, ownership, live_shares, Decimal("100"))
        phase3a = build_hd_phase3a_reconciliation(self.transactions, [], [], [], ownership, live_shares)
        return build_hd_lifetime_economics(self.transactions, ownership, phase2, phase3a, live_shares, Decimal("100")), ownership, phase2, phase3a

    def test_espp_lifetime_uses_source_values_and_not_fixed_discount(self) -> None:
        result, _, _, _ = self._result()

        self.assertEqual(result.espp_lifetime["purchase_count"], 2)
        self.assertEqual(result.espp_lifetime["total_employee_contributions"], Decimal("130"))
        self.assertEqual(result.espp_lifetime["total_espp_shares"], Decimal("15"))
        self.assertEqual(result.espp_lifetime["weighted_average_purchase_price"], Decimal("8.666666666666666666666666667"))
        self.assertEqual(result.espp_lifetime["total_source_reported_fmv_at_purchase_value"], Decimal("100"))
        self.assertEqual(result.espp_lifetime["total_purchase_discount_benefit"], Decimal("20"))
        self.assertEqual(result.espp_lifetime["weighted_effective_discount_pct"], Decimal("0.2"))
        self.assertEqual(result.espp_lifetime["complete_fmv_purchase_count"], 1)
        self.assertEqual(result.espp_lifetime["incomplete_fmv_purchase_count"], 1)
        self.assertIn("not assumed fixed discount", result.espp_lifetime["coverage_label"])

    def test_drip_lifetime_preserves_source_exception_and_employee_separation(self) -> None:
        result, _, _, _ = self._result()

        self.assertEqual(result.drip_lifetime["event_count"], 1)
        self.assertEqual(result.drip_lifetime["total_gross_dividend"], Decimal("9.78"))
        self.assertEqual(result.drip_lifetime["total_dividend_fees"], Decimal("0.14"))
        self.assertEqual(result.drip_lifetime["total_dividend_taxes"], Decimal("0.00"))
        self.assertEqual(result.drip_lifetime["total_net_reinvested"], Decimal("9.79"))
        self.assertEqual(result.drip_lifetime["total_drip_shares"], Decimal("0.02693"))
        self.assertEqual(result.drip_lifetime["source_reconciliation_exception_count"], 1)
        self.assertEqual(result.drip_lifetime["source_economics_review_count"], 0)
        self.assertEqual(result.drip_lifetime["parser_failure_count"], 0)
        self.assertEqual(result.summary["total_employee_cash_contributed"], Decimal("130"))

    def test_sales_and_realized_by_origin_count_aggregate_sale_once(self) -> None:
        result, _, _, _ = self._result()
        by_origin = {row["origin"]: row for row in result.realized_by_origin}

        self.assertEqual(result.sales_lifetime["aggregate_shares_sold"], Decimal("8"))
        self.assertEqual(result.sales_lifetime["aggregate_documented_sale_proceeds"], Decimal("1000"))
        self.assertEqual(result.sales_lifetime["total_realized_lot_count"], 2)
        self.assertEqual(result.sales_lifetime["shares_with_documented_basis"], Decimal("5"))
        self.assertEqual(result.sales_lifetime["shares_without_documented_basis"], Decimal("3"))
        self.assertEqual(result.sales_lifetime["documented_basis_total"], Decimal("500"))
        self.assertEqual(result.sales_lifetime["documented_realized_gain"], Decimal("200"))
        self.assertEqual(result.sales_lifetime["documented_realized_gain"] / result.sales_lifetime["documented_basis_total"], Decimal("0.4"))
        self.assertEqual(result.coverage["share_basis_coverage_pct"], Decimal("0.625"))
        self.assertEqual(result.coverage["proceeds_coverage_pct"], Decimal("0.7"))
        self.assertEqual(result.coverage["origin_coverage_pct"], Decimal("0.625"))
        self.assertEqual(by_origin["ESPP_PURCHASE"]["documented_realized_gain"], Decimal("200"))
        self.assertEqual(by_origin["UNRESOLVED"]["documented_realized_gain"], Decimal("0"))

    def test_cash_flow_model_uses_consistent_signs_and_excludes_custody_and_restricted(self) -> None:
        result, _, _, _ = self._result()
        events = [row["event_type"] for row in result.cash_flows]

        self.assertEqual(events.count("ESPP_CONTRIBUTION"), 2)
        self.assertEqual(events.count("DRIP_DIVIDEND"), 1)
        self.assertEqual(events.count("DRIP_REINVESTMENT"), 1)
        self.assertEqual(events.count("FEES"), 1)
        self.assertEqual(events.count("CASH_DIVIDEND"), 1)
        self.assertEqual(events.count("SALE_PROCEEDS"), 1)
        self.assertNotIn("TRANSFER_OUT", events)
        self.assertNotIn("TRANSFER_IN", events)
        self.assertNotIn("ADJUSTMENT", events)
        self.assertEqual(result.cash_flow_summary["total_documented_cash_inflows"], Decimal("1044.78"))
        self.assertEqual(result.cash_flow_summary["total_documented_cash_outflows"], Decimal("139.93"))
        self.assertEqual(result.cash_flow_summary["net_documented_cash_flow"], Decimal("904.85"))
        self.assertEqual(result.cash_flow_summary["total_documented_fees"], Decimal("0.14"))
        self.assertEqual(result.cash_flow_summary["total_documented_taxes"], Decimal("0.00"))

    def test_open_economic_cash_prorates_espp_and_drip_without_inventing_costs(self) -> None:
        result, _, _, _ = self._result()

        self.assertEqual(result.open_economic_cost["lifetime_documented_acquisition_cash"], Decimal("139.79"))
        self.assertEqual(result.open_economic_cost["open_lot_economic_cash"], Decimal("99.79"))
        self.assertEqual(result.open_economic_cost["sold_or_consumed_economic_acquisition_cash"], Decimal("40.00"))
        self.assertIsNone(result.open_economic_cost["unknown_unavailable_economic_acquisition_cash"])
        self.assertEqual(result.open_economic_cost["unknown_unavailable_shares"], Decimal("4"))
        self.assertIn("not tax basis", result.open_economic_cost["coverage_label"])

    def test_performance_gating_withholds_total_return_without_zero_placeholders(self) -> None:
        transactions = [
            tx("espp", "ESPP_PURCHASE", "2024-01-01", "10", amount=Decimal("80"), purchase_price=Decimal("8"), fair_market_value=Decimal("10")),
            tx("sale-agg", "SALE", "2026-02-13", "5", amount=Decimal("700"), source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
            tx("sale-lot", "SALE", "2026-02-12", "5", amount=Decimal("700"), cost_basis=Decimal("500"), acquisition_date="2024-01-01", lot_sequence=1, source="Merrill", source_location="Merrill", source_account_type="Realized lot"),
        ]
        ownership = reconstruct_hd_ownership(transactions, Decimal("1"))
        phase2 = analyze_hd_economics(transactions, ownership, Decimal("1"), Decimal("100"))
        phase3a = build_hd_phase3a_reconciliation(transactions, [], [], [], ownership, Decimal("1"))
        result = build_hd_lifetime_economics(transactions, ownership, phase2, phase3a, Decimal("1"), Decimal("100"))
        gating = {row["metric"]: row for row in result.performance_gating}

        self.assertEqual(gating["ESPP Purchase Discount Benefit"]["status"], "AVAILABLE")
        self.assertEqual(gating["Documented Realized Gain"]["status"], "AVAILABLE")
        self.assertEqual(gating["Lifetime Total Return"]["status"], "WITHHELD")
        self.assertIsNone(gating["Lifetime Total Return"]["value"])
        self.assertIn("HISTORICAL_LIVE_RECONCILIATION_INCOMPLETE", gating["Lifetime Total Return"]["gating_reasons"])
        self.assertEqual(gating["IRR"]["status"], "WITHHELD")
        self.assertEqual(gating["XIRR"]["status"], "WITHHELD")
        self.assertEqual(gating["Current Unrealized Return From Historical Lots"]["status"], "WITHHELD")
        self.assertTrue(any(issue["issue_type"] == "LIFETIME_RETURN_WITHHELD" for issue in result.issues))

    def test_phase3b_does_not_mutate_inputs_or_upstream_results(self) -> None:
        ownership = reconstruct_hd_ownership(self.transactions, Decimal("4"))
        phase2 = analyze_hd_economics(self.transactions, ownership, Decimal("4"), Decimal("100"))
        phase3a = build_hd_phase3a_reconciliation(self.transactions, [], [], [], ownership, Decimal("4"))
        transactions_before = deepcopy(self.transactions)
        lot_shares_before = [lot.remaining_shares for lot in ownership.lots]
        phase3a_summary_before = deepcopy(phase3a.summary)

        build_hd_lifetime_economics(self.transactions, ownership, phase2, phase3a, Decimal("4"), Decimal("100"))

        self.assertEqual(self.transactions, transactions_before)
        self.assertEqual([lot.remaining_shares for lot in ownership.lots], lot_shares_before)
        self.assertEqual(phase3a.summary, phase3a_summary_before)


if __name__ == "__main__":
    unittest.main()

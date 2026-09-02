from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from hd_ownership import OwnershipLot, SoldLot, reconstruct_hd_ownership
from hd_espp_analytics import analyze_hd_economics


class HDEconomicsAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transactions = [
            {
                "transaction_date": "2023-06-30",
                "transaction_type": "ESPP_PURCHASE",
                "source": "Computershare",
                "source_account_type": "ESPP",
                "shares": "5.302147",
                "amount": "1400.00",
                "purchase_price": "264.044000",
                "fair_market_value": "310.640000",
                "source_document": "legacy.pdf",
                "transaction_fingerprint": "fp-espp-1",
            },
            {
                "transaction_date": "2023-09-15",
                "transaction_type": "DIVIDEND_REINVESTMENT",
                "source": "Computershare",
                "source_account_type": "ESPP",
                "shares": "0.032549",
                "amount": "10.64",
                "purchase_price": "326.887800",
                "fees": "0.44",
                "taxes": "0.00",
                "source_document": "legacy.pdf",
                "notes": "gross_dividend=11.08",
                "transaction_fingerprint": "fp-drip-1",
            },
            {
                "transaction_date": "2026-02-12",
                "transaction_type": "SALE",
                "source": "Merrill",
                "source_account_type": "Realized lot",
                "shares": "5.3021",
                "amount": "2077.71",
                "cost_basis": "1399.99",
                "acquisition_date": "2023-06-30",
                "lot_sequence": 1,
                "source_document": "merrill.pdf",
                "transaction_fingerprint": "fp-sale-1",
            },
            {
                "transaction_date": "2026-02-13",
                "transaction_type": "SALE",
                "source": "Merrill",
                "source_account_type": "Brokerage",
                "shares": "28.0000",
                "amount": "10972.22",
                "source_document": "merrill.pdf",
                "transaction_fingerprint": "fp-sale-agg",
            },
            {
                "transaction_date": "2026-02-13",
                "transaction_type": "DIVIDEND",
                "source": "Merrill",
                "source_account_type": "Brokerage",
                "shares": None,
                "amount": "35.00",
                "source_document": "merrill.pdf",
                "transaction_fingerprint": "fp-div-1",
            },
        ]

    def test_espp_summary_and_discount_are_calculated(self) -> None:
        analysis = analyze_hd_economics(self.transactions)
        self.assertEqual(analysis.espp_summary["total_employee_contributions"], Decimal("1400.00"))
        self.assertEqual(analysis.espp_summary["total_espp_shares"], Decimal("5.302147"))
        self.assertAlmostEqual(float(analysis.espp_summary["weighted_average_purchase_price"]), float(Decimal("264.044000")))
        self.assertEqual(analysis.discount_summary["purchases_with_complete_fmv_and_price"], 1)
        self.assertEqual(analysis.discount_summary["total_discount_value"], Decimal("247.058841612000"))
        self.assertEqual(analysis.discount_summary["weighted_effective_discount_pct"], Decimal("0.15"))

    def test_drip_reconciliation_and_no_employee_contamination(self) -> None:
        analysis = analyze_hd_economics(self.transactions)
        self.assertEqual(analysis.drip_summary["total_drip_shares"], Decimal("0.032549"))
        self.assertEqual(analysis.drip_summary["total_net_reinvested"], Decimal("10.64"))
        self.assertEqual(analysis.drip_summary["total_gross_dividend"], Decimal("11.08"))
        self.assertEqual(analysis.drip_summary["total_dividend_fees"], Decimal("0.44"))
        self.assertEqual(analysis.drip_summary["total_dividend_taxes"], Decimal("0.00"))
        self.assertEqual(analysis.espp_summary["total_employee_contributions"], Decimal("1400.00"))
        self.assertNotEqual(analysis.espp_summary["total_employee_contributions"], analysis.drip_summary["total_net_reinvested"])

    def test_realized_gain_uses_documented_basis(self) -> None:
        analysis = analyze_hd_economics(self.transactions)
        self.assertEqual(analysis.realized_summary["known_basis_shares"], Decimal("5.3021"))
        self.assertEqual(analysis.realized_summary["documented_basis_total"], Decimal("1399.99"))
        self.assertEqual(analysis.realized_summary["documented_realized_gain"], Decimal("677.72"))
        self.assertEqual(analysis.coverage["realized_gain_coverage_pct"], Decimal("0.1893607142857142857142857143"))

    def test_ownership_is_not_mutated_by_analytics(self) -> None:
        live_shares = Decimal("20")
        ownership = reconstruct_hd_ownership(self.transactions, live_shares)
        before = [lot.remaining_shares for lot in ownership.lots]
        analysis = analyze_hd_economics(self.transactions, ownership)
        after = [lot.remaining_shares for lot in ownership.lots]
        self.assertEqual(before, after)
        self.assertEqual(analysis.open_lot_economic_cost["economic_employee_cash_invested"], Decimal("1400.00"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from hd_ownership import LOT_DISPLAY_TOLERANCE, reconstruct_hd_ownership


def transaction(fingerprint: str, kind: str, tx_date: str, shares: str, **values):
    return {
        "transaction_fingerprint": fingerprint,
        "transaction_type": kind,
        "transaction_date": tx_date,
        "shares": Decimal(shares),
        "source": values.pop("source", "Computershare"),
        "source_location": values.pop("source_location", "Computershare"),
        **values,
    }


class HDOwnershipTests(unittest.TestCase):
    def test_origins_basis_and_live_reconciliation_are_derived_without_mutation(self):
        rows = [
            transaction("espp", "ESPP_PURCHASE", "2023-06-30", "5.25", amount=Decimal("1400"), purchase_price=Decimal("266.67")),
            transaction("drip", "DIVIDEND_REINVESTMENT", "2023-09-15", ".03", amount=Decimal("10.50")),
            transaction("rs", "ADJUSTMENT", "2024-07-17", "11", source="Merrill", source_location="Merrill restricted share award"),
            transaction("unknown", "TRANSFER_IN", "2025-03-31", "4", source="Merrill", source_location="External", destination_location="Merrill"),
        ]
        original = deepcopy(rows)
        result = reconstruct_hd_ownership(rows, Decimal("20.28"))

        self.assertEqual(result.summary["total_economic_shares_acquired"], Decimal("20.28"))
        self.assertEqual(result.summary["reconstructed_remaining_shares"], Decimal("20.28"))
        self.assertEqual(result.summary["reconciliation_status"], "MATCHED")
        self.assertEqual({lot.origin_type for lot in result.lots}, {"ESPP_PURCHASE", "DIVIDEND_REINVESTMENT", "RESTRICTED_SHARE", "UNKNOWN"})
        self.assertEqual(next(lot for lot in result.lots if lot.origin_type == "ESPP_PURCHASE").known_cost_basis, Decimal("1400"))
        self.assertEqual(rows, original)

    def test_matched_transfer_changes_custody_not_economic_shares(self):
        rows = [
            transaction("buy", "ESPP_PURCHASE", "2023-06-30", "5", amount=Decimal("1000")),
            transaction("out", "TRANSFER_OUT", "2024-07-15", "-5", transfer_match_id="move", destination_location="Merrill", transfer_value=Decimal("1500")),
            transaction("in", "TRANSFER_IN", "2024-07-16", "5", transfer_match_id="move", source="Merrill", source_location="Computershare", destination_location="Merrill", transfer_value=Decimal("1500")),
            transaction("sale", "SALE", "2026-02-12", "5", source="Merrill", source_location="Merrill", source_account_type="Realized lot", acquisition_date="2023-06-30", lot_sequence=1, cost_basis=Decimal("1000")),
            transaction("sale-total", "SALE", "2026-02-12", "5", source="Merrill", source_location="Merrill", source_account_type="Brokerage"),
        ]
        result = reconstruct_hd_ownership(rows)

        self.assertEqual(result.summary["total_economic_shares_acquired"], Decimal("5"))
        self.assertEqual(result.summary["shares_transferred"], Decimal("5"))
        self.assertEqual(result.summary["total_shares_sold"], Decimal("5"))
        self.assertEqual(result.summary["reconstructed_remaining_shares"], Decimal("0"))
        self.assertEqual(result.lots[0].known_cost_basis, Decimal("1000"))
        self.assertNotEqual(result.lots[0].known_cost_basis, Decimal("1500"))

    def test_realized_lots_support_partial_sales_unknown_rows_and_display_tolerance(self):
        rows = [
            transaction("drip", "DIVIDEND_REINVESTMENT", "2024-06-14", ".063428", amount=Decimal("22.12")),
            transaction("restricted", "ADJUSTMENT", "2025-03-24", "15", source="Merrill", source_location="Restricted share award"),
            transaction("drip-sale", "SALE", "2026-02-12", ".0637", source="Merrill", source_account_type="Realized lot", acquisition_date="2024-06-14", lot_sequence=1),
            transaction("rs-sale", "SALE", "2026-02-12", "5", source="Merrill", source_account_type="Realized lot", acquisition_date="2025-03-22", lot_sequence=2),
            transaction("na-sale", "SALE", "2026-02-12", "4", source="Merrill", source_account_type="Realized lot", acquisition_date=None, lot_sequence=3),
            transaction("total", "SALE", "2026-02-12", "9.0637", source="Merrill", source_account_type="Brokerage"),
        ]
        result = reconstruct_hd_ownership(rows)

        self.assertEqual(result.summary["known_origin_sold_lots"], 2)
        self.assertEqual(result.summary["unresolved_origin_sold_lots"], 1)
        self.assertEqual(result.summary["sale_precision_difference"], Decimal(".000272"))
        self.assertLess(result.summary["sale_precision_difference"], LOT_DISPLAY_TOLERANCE)
        self.assertEqual(next(lot for lot in result.lots if lot.origin_type == "RESTRICTED_SHARE").remaining_shares, Decimal("10"))
        self.assertEqual(result.summary["total_shares_sold"], Decimal("9.0637"))
        self.assertEqual(result.summary["reconstructed_remaining_shares"], Decimal("5.999728"))


if __name__ == "__main__":
    unittest.main()

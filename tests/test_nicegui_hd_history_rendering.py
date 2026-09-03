from __future__ import annotations

import unittest
from decimal import Decimal

from nicegui_app.main import build_hd_transaction_display_records


class HDHistoryRenderingTests(unittest.TestCase):
    def test_heterogeneous_normalized_transactions_have_stable_optional_fields(self) -> None:
        transactions = [
            {"transaction_date": "2025-03-27", "transaction_type": "TRANSFER_OUT", "shares": Decimal("4"), "source": "EquatePlus", "source_location": "EquatePlus", "destination_location": "Merrill"},
            {"transaction_date": "2025-03-31", "transaction_type": "TRANSFER_IN", "shares": Decimal("4"), "source": "Merrill", "source_location": "EquatePlus", "destination_location": "Merrill"},
            {"transaction_date": "2026-03-20", "transaction_type": "ADJUSTMENT", "shares": Decimal("11"), "source": "Merrill", "status": "documented"},
            {"transaction_date": "2026-06-30", "transaction_type": "ESPP_PURCHASE", "shares": Decimal("4.670123"), "amount": Decimal("1400"), "purchase_price": Decimal("299.78"), "source": "EquatePlus"},
            {"transaction_date": "2026-06-03", "transaction_type": "DIVIDEND_REINVESTMENT", "shares": Decimal("0.037960"), "amount": Decimal("12.55"), "purchase_price": Decimal("330.62"), "fees": Decimal("0.52"), "source": "EquatePlus"},
            {"transaction_date": "2026-06-04", "settlement_date": "2026-06-05", "transaction_type": "SALE", "shares": Decimal("9"), "amount": Decimal("2819.46"), "source": "Merrill", "source_account_type": "Brokerage"},
            {"transaction_date": "2026-06-04", "transaction_type": "SALE", "shares": Decimal("9"), "amount": Decimal("2819.46"), "cost_basis": Decimal("3160.35"), "source": "Merrill", "source_account_type": "Realized lot", "basis_provenance": "merrill_reported"},
        ]

        records = build_hd_transaction_display_records(transactions)

        self.assertEqual(len(records), len(transactions))
        self.assertEqual(set(records[0]), set(records[-1]))
        self.assertIsNone(records[0]["amount"])
        self.assertIsNone(records[0]["purchase_price"])
        self.assertIsNone(records[0]["cost_basis"])
        self.assertIsNone(records[0]["fees"])
        self.assertEqual(records[0]["source_destination"], "EquatePlus → Merrill")
        self.assertEqual(records[3]["amount"], Decimal("1400"))
        self.assertEqual(records[4]["fees"], Decimal("0.52"))
        self.assertEqual(records[5]["settlement_date"], "2026-06-05")
        self.assertEqual(records[6]["cost_basis"], Decimal("3160.35"))
        self.assertEqual(records[6]["basis_provenance"], "merrill_reported")


if __name__ == "__main__":
    unittest.main()

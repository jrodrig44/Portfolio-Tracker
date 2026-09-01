from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from hd_espp_history import HDESPPHistoryService, HDTransaction, detect_document_family, match_custody_transfers, parse_hd_statement_text, transaction_fingerprint


LEGACY = """The Home Depot, Inc ESPP - Employee Plan Statement
01 Jan 2023 - 29 Dec 2023
30 Jun 2023 Purchase 1,400.00 1,400.00 01 Jan 2023 315.860000 30 Jun 2023 310.640000 264.044000 5.302147 5.302147
15 Sep 2023 Dividend Reinvestment 11.08 0.44 10.64 326.887800 0.032549 5.334696
15 Jul 2024 Plan Transfer Confirm -15.000000 0.021341
"""
EQUATE = """Purchase activity statement EquatePlus
Employee Stock Purchase Plan (SHARE)
1 Apr 2026 - 30 Jun 2026
30 Jun 2026 30 Jun 2026 30 Jun 2026 1 Jul 2026 1 400.00 USD 0.00 USD 0.00 USD 352.68 USD 299.78 USD 4.670123 0.00 USD
Dividend Reinvestments
3 Jun 2026 5.610661 13.07 USD 0.00 USD 0.52 USD 12.55 USD 330.62 USD 0.03796 0.00 USD
"""
MERRILL = """Merrill Lynch
January 01, 2026 - February 27, 2026
HOME DEPOT INC HD
02/13 HOME DEPOT INC Sale -28.0000 10,972.22 10,972.22
HOME DEPOT INC 5.3021 06/30/23 02/12/26 2,077.71 1,399.99 677.72
"""


class HDESPPHistoryTests(unittest.TestCase):
    def test_detection_and_legacy_precision(self) -> None:
        self.assertEqual(detect_document_family(LEGACY), "computershare_legacy")
        _, rows, issues = parse_hd_statement_text(LEGACY)
        self.assertFalse(issues)
        self.assertEqual([r.transaction_type for r in rows], ["ESPP_PURCHASE", "DIVIDEND_REINVESTMENT", "TRANSFER_OUT"])
        self.assertEqual(rows[0].shares, Decimal("5.302147"))
        self.assertEqual(rows[1].fees, Decimal("0.44"))

    def test_equateplus_purchase_and_drip(self) -> None:
        self.assertEqual(detect_document_family(EQUATE), "computershare_equateplus")
        _, rows, _ = parse_hd_statement_text(EQUATE)
        purchase = next(row for row in rows if row.transaction_type == "ESPP_PURCHASE")
        drip = next(row for row in rows if row.transaction_type == "DIVIDEND_REINVESTMENT" and row.transaction_date == date(2026, 6, 3))
        self.assertEqual(purchase.shares, Decimal("4.670123"))
        self.assertEqual(purchase.purchase_price, Decimal("299.78"))
        self.assertEqual(drip.amount, Decimal("12.55"))

    def test_merrill_sale_and_realized_lot(self) -> None:
        self.assertEqual(detect_document_family(MERRILL), "merrill_hd")
        _, rows, _ = parse_hd_statement_text(MERRILL)
        sale = next(r for r in rows if r.source_account_type == "Brokerage")
        lot = next(r for r in rows if r.source_account_type == "Realized lot")
        self.assertEqual(sale.shares, Decimal("28.0000"))
        self.assertEqual(sale.amount, Decimal("10972.22"))
        self.assertEqual(lot.acquisition_date, date(2023, 6, 30))

    def test_fingerprint_is_filename_independent_and_deduplicates(self) -> None:
        _, first, _ = parse_hd_statement_text(LEGACY, "quarter-one.pdf")
        _, second, _ = parse_hd_statement_text(LEGACY, "quarter-two.pdf")
        self.assertEqual(transaction_fingerprint(first[0]), transaction_fingerprint(second[0]))
        with tempfile.TemporaryDirectory() as directory:
            service = HDESPPHistoryService(Path(directory), Path(directory) / "history.sqlite")
            connection = __import__("sqlite3").connect(service.db_path)
            try:
                connection.executescript(__import__("hd_espp_history").SCHEMA)
                connection.execute("INSERT INTO hd_espp_transactions (ticker,transaction_date,transaction_type,source,source_account_type,source_document,transaction_fingerprint,created_at) VALUES ('HD','2023-06-30','ESPP_PURCHASE','Computershare','ESPP','a.pdf',?,'now')", (first[0].transaction_fingerprint,))
                connection.execute("INSERT OR IGNORE INTO hd_espp_transactions (ticker,transaction_date,transaction_type,source,source_account_type,source_document,transaction_fingerprint,created_at) VALUES ('HD','2023-06-30','ESPP_PURCHASE','Computershare','ESPP','b.pdf',?,'now')", (second[0].transaction_fingerprint,))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM hd_espp_transactions").fetchone()[0], 1)
            finally:
                connection.close()

    def test_transfer_matching_prevents_duplicate_economic_ownership(self) -> None:
        outgoing = HDTransaction(date(2024, 7, 15), "TRANSFER_OUT", "Computershare", "ESPP", shares=Decimal("15"))
        incoming = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"))
        self.assertEqual(len(match_custody_transfers([outgoing, incoming])), 1)

    def test_unknown_document_is_flagged_not_guessed(self) -> None:
        family, rows, issues = parse_hd_statement_text("unrecognized row 12.34")
        self.assertEqual(family, "unknown"); self.assertFalse(rows); self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()

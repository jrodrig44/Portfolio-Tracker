from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hd_espp_history import HDESPPHistoryService, HDTransaction, detect_document_family, economic_share_ownership, match_custody_transfers, parse_hd_statement_text, reconcile_custody_transfers, transaction_fingerprint, transaction_record_type


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
    class _PDF:
        def __init__(self, text: str):
            self.pages = [type("Page", (), {"extract_text": lambda page: text})()]
        def __enter__(self): return self
        def __exit__(self, *args): return None

    def _import_texts(self, service: HDESPPHistoryService, texts: dict[str, str]) -> dict[str, int]:
        service.source_dir.mkdir(parents=True, exist_ok=True)
        for name in texts:
            (service.source_dir / name).write_bytes(texts[name].encode())
        with patch("hd_espp_history.pdfplumber.open", side_effect=lambda path: self._PDF(texts[Path(path).name])):
            return service.import_statements()

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
        outgoing = HDTransaction(date(2024, 7, 15), "TRANSFER_OUT", "Computershare", "ESPP", shares=Decimal("15"), source_location="Computershare", destination_location="Merrill")
        incoming = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), source_location="External", destination_location="Merrill")
        self.assertEqual(len(match_custody_transfers([outgoing, incoming])), 1)
        purchase = HDTransaction(date(2024, 6, 28), "ESPP_PURCHASE", "Computershare", "ESPP", shares=Decimal("15"))
        self.assertEqual(economic_share_ownership([purchase]), economic_share_ownership([purchase, outgoing, incoming]))

    def test_transfer_matching_requires_provenance_and_surfaces_ambiguity(self) -> None:
        outgoing = HDTransaction(date(2024, 7, 15), "TRANSFER_OUT", "Computershare", "ESPP", shares=Decimal("15"), source_location="Computershare", destination_location="Merrill")
        wrong = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Other Broker", "Brokerage", shares=Decimal("15"), source_location="External", destination_location="Other Broker")
        right = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), source_location="Computershare", destination_location="Merrill")
        self.assertEqual(match_custody_transfers([outgoing, wrong]), [])
        self.assertEqual(match_custody_transfers([outgoing, wrong, right]), [(outgoing.transaction_fingerprint, right.transaction_fingerprint)])
        second = HDTransaction(date(2024, 7, 17), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), source_location="External", destination_location="Merrill", cost_basis=Decimal("5000"))
        matches, issues = reconcile_custody_transfers([outgoing, right, second])
        self.assertFalse(matches); self.assertEqual(len(issues), 1)

    def test_cost_basis_can_disambiguate_transfer_candidates(self) -> None:
        outgoing = HDTransaction(date(2024, 7, 15), "TRANSFER_OUT", "Computershare", "ESPP", shares=Decimal("15"), cost_basis=Decimal("4259.06"), source_location="Computershare", destination_location="Merrill")
        exact = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), cost_basis=Decimal("4259.06"), source_location="External", destination_location="Merrill")
        other = HDTransaction(date(2024, 7, 17), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), cost_basis=Decimal("5000"), source_location="External", destination_location="Merrill")
        self.assertEqual(match_custody_transfers([outgoing, other, exact]), [(outgoing.transaction_fingerprint, exact.transaction_fingerprint)])

    def test_section_completeness_flags_partial_parser_result(self) -> None:
        partial = """The Home Depot, Inc ESPP - Employee Plan Statement
Purchases
Dividends
15 Sep 2023 Dividend Reinvestment 11.08 0.44 10.64 326.887800 0.032549 5.334696
"""
        _, rows, issues = parse_hd_statement_text(partial)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("Purchases" in issue for issue in issues))

    def test_record_type_distinguishes_aggregate_sale_and_realized_lot(self) -> None:
        _, rows, _ = parse_hd_statement_text(MERRILL)
        aggregate = next(row for row in rows if row.source_account_type == "Brokerage")
        lot = next(row for row in rows if row.source_account_type == "Realized lot")
        self.assertEqual(transaction_record_type(aggregate), "Brokerage Transaction")
        self.assertEqual(transaction_record_type(lot), "Realized Lot")

    def test_authoritative_rebuild_replaces_stale_fields_and_is_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            self._import_texts(service, {"one.pdf": LEGACY})
            corrected = LEGACY.replace("310.640000", "311.640000")
            self._import_texts(service, {"one.pdf": corrected})
            state = service.state()
            purchase = next(row for row in state["transactions"] if row["transaction_type"] == "ESPP_PURCHASE")
            self.assertEqual(purchase["fair_market_value"], "311.640000")
            before = [(row["transaction_fingerprint"], row["fair_market_value"], row["fees"], row["notes"]) for row in state["transactions"]]
            self._import_texts(service, {"one.pdf": corrected})
            after = [(row["transaction_fingerprint"], row["fair_market_value"], row["fees"], row["notes"]) for row in service.state()["transactions"]]
            self.assertEqual(before, after)

    def test_removed_and_replaced_documents_leave_no_stale_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            self._import_texts(service, {"first.pdf": LEGACY, "second.pdf": EQUATE})
            (service.source_dir / "first.pdf").unlink()
            self._import_texts(service, {"second.pdf": EQUATE})
            self.assertFalse(any(row["transaction_date"].startswith("2023") for row in service.state()["transactions"]))
            replacement = MERRILL
            (service.source_dir / "second.pdf").write_bytes(replacement.encode())
            self._import_texts(service, {"second.pdf": replacement})
            state = service.state()
            self.assertFalse(any(row["transaction_type"] == "ESPP_PURCHASE" for row in state["transactions"]))
            self.assertTrue(any(row["source_account_type"] == "Brokerage" for row in state["transactions"]))

    def test_cumulative_documents_rebuild_to_one_canonical_transaction_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            result = self._import_texts(service, {"a.pdf": LEGACY, "b.pdf": LEGACY})
            self.assertEqual(result["parsed"], 6)
            self.assertEqual(result["inserted"], 3)

    def test_service_rejects_concurrent_import(self) -> None:
        service = HDESPPHistoryService()
        self.assertTrue(service._import_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                service.import_statements()
        finally:
            service._import_lock.release()

    def test_nicegui_import_action_has_double_click_guard_and_record_type(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        block = source.split("async def render_hd_espp_history", 1)[1].split("with ui.header()", 1)[0]
        self.assertIn('if importing["value"]:', block)
        self.assertIn("import_button.disable()", block)
        self.assertIn("import_button.enable()", block)
        self.assertIn('"record_type": transaction_record_type(row)', block)

    def test_unknown_document_is_flagged_not_guessed(self) -> None:
        family, rows, issues = parse_hd_statement_text("unrecognized row 12.34")
        self.assertEqual(family, "unknown"); self.assertFalse(rows); self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import sqlite3
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hd_espp_history import HDESPPHistoryService, HDTransaction, PARSER_VERSION, _connect, detect_document_family, economic_share_ownership, match_custody_transfers, parse_hd_statement_text, parse_hd_transfer_lots, reconcile_custody_transfers, transaction_fingerprint, transaction_record_type


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
x HOME DEPOT INC .0325 09/15/23 02/12/26 12.74 10.64 2.10
x HOME DEPOT INC .0306 12/15/23 02/12/26 11.99 10.71 1.28
x HOME DEPOT INC 4.7527 12/29/23 02/12/26 1,862.41 1,399.99 462.42
x HOME DEPOT INC .0552 03/22/24 02/12/26 21.63 21.89 (.26)
x HOME DEPOT INC .0637 06/14/24 02/12/26 24.96 22.12 2.84
x HOME DEPOT INC 4.7632 06/28/24 02/12/26 1,866.53 1,393.73 472.80
HOME DEPOT INC 4.0000 N/A 02/12/26 1,567.46 N/A N/A
HOME DEPOT INC 4.0000 N/A 02/12/26 1,567.46 N/A N/A
HOME DEPOT INC 5.0000 03/22/25 02/12/26 1,959.33 1,755.75 203.58
"""
MERRILL_TRANSFERS = """Merrill Lynch
June 29, 2024 - September 30, 2024
HOME DEPOT INC HD
07/16 HOME DEPOT INC Security Transfer In 15.0000 5,536.80
07/17 HOME DEPOT INC Transfer / Adjustment 11.0000 3,639.90
"""
COMPUTERSHARE_TRANSFER = """The Home Depot, Inc ESPP - Employee Plan Statement
01 Jan 2024 - 30 Sep 2024
15 Jul 2024 Plan Transfer Confirm -15.000000 0.021341
15 Jul 2024 Transfer -15.000000 4,259.06
30 Jun 2023 -5.302147 1,400.00 Covered
15 Sep 2023 -0.032549 10.65 Covered
15 Dec 2023 -0.030610 10.71 Covered
29 Dec 2023 -4.752731 1,400.00 Covered
22 Mar 2024 -0.055252 21.91 Covered
14 Jun 2024 -0.063428 22.03 Covered
28 Jun 2024 -4.763283 1,393.76 Covered
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

    def test_computershare_transfer_lot_details_are_normalized(self) -> None:
        _, rows, issues = parse_hd_statement_text(COMPUTERSHARE_TRANSFER, "transfer.pdf")
        details = parse_hd_transfer_lots(COMPUTERSHARE_TRANSFER, rows, "transfer.pdf")

        self.assertFalse(issues)
        self.assertEqual(len(details), 7)
        self.assertEqual(sum((row.transferred_shares for row in details), Decimal()), Decimal("15.000000"))
        self.assertEqual(details[-1].transferred_shares, Decimal("4.763283"))
        self.assertEqual(details[-1].reported_value, Decimal("1393.76"))
        self.assertEqual(details[-1].source_type, "COVERED")
        self.assertEqual({row.parent_transfer_fingerprint for row in details}, {next(row.transaction_fingerprint for row in rows if row.transfer_value is not None)})

    def test_merrill_sale_and_realized_lot(self) -> None:
        self.assertEqual(detect_document_family(MERRILL), "merrill_hd")
        _, rows, _ = parse_hd_statement_text(MERRILL)
        sale = next(r for r in rows if r.source_account_type == "Brokerage")
        lot = next(r for r in rows if r.source_account_type == "Realized lot")
        self.assertEqual(sale.shares, Decimal("28.0000"))
        self.assertEqual(sale.amount, Decimal("10972.22"))
        self.assertEqual(lot.acquisition_date, date(2023, 6, 30))
        lots = [row for row in rows if row.source_account_type == "Realized lot"]
        self.assertEqual(len(lots), 10)
        self.assertEqual(sum(row.shares for row in lots), Decimal("28.0000"))
        self.assertEqual(sum(row.acquisition_date is not None and row.cost_basis is not None for row in lots), 8)
        unavailable = [row for row in lots if row.acquisition_date is None]
        self.assertEqual(len(unavailable), 2)
        self.assertTrue(all(row.ambiguous and "unavailable" in row.notes for row in unavailable))
        self.assertEqual(len({row.transaction_fingerprint for row in unavailable}), 2)

    def test_legacy_drip_parsing_handles_blank_fee_and_tiny_dividend(self) -> None:
        text = """The Home Depot, Inc ESPP - Employee Plan Statement
01 Jan 2024 - 30 Sep 2024
15 Sep 2023 Dividend Reinvestment 11.08 0.44 10.64 326.887800 0.032549 5.334696
13 Sep 2024 Dividend Reinvestment 0.05 0.05 378.217400 0.000132 0.021473
"""
        _, rows, issues = parse_hd_statement_text(text)
        self.assertFalse(issues)
        self.assertEqual(len(rows), 2)
        normal = next(row for row in rows if row.transaction_date == date(2023, 9, 15))
        tiny = next(row for row in rows if row.transaction_date == date(2024, 9, 13))
        self.assertEqual(normal.amount, Decimal("10.64"))
        self.assertEqual(normal.fees, Decimal("0.44"))
        self.assertEqual(normal.purchase_price, Decimal("326.887800"))
        self.assertEqual(tiny.amount, Decimal("0.05"))
        self.assertEqual(tiny.fees, Decimal("0.00"))
        self.assertEqual(tiny.purchase_price, Decimal("378.217400"))
        self.assertEqual(tiny.shares, Decimal("0.000132"))

    def test_equateplus_drip_parsing_uses_table_columns(self) -> None:
        text = """Purchase activity statement Jeffrey Rodriguez
1 Jan 2025 - 31 Mar 2025 User ID: 204474190
Dividend Reinvestments
1 Jan 2025 to 31 Mar 2025
Gross Dividend Dividend Purchase Purchased Residual
Record Date Purchase Date Entitled Quantity Payment Taxes Fees Reinvested Price Shares Amount
12 Mar 2025 4.255534 9.78 USD 0.00 USD 0.14 USD 9.79 USD 358.05 USD 0.02693 0.00 USD
ESPP-PURCHASE at 2.3 USD
rate
"""
        _, rows, issues = parse_hd_statement_text(text)
        self.assertFalse(issues)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.transaction_date, date(2025, 3, 12))
        self.assertEqual(row.amount, Decimal("9.79"))
        self.assertEqual(row.fees, Decimal("0.14"))
        self.assertEqual(row.taxes, Decimal("0.00"))
        self.assertEqual(str(row.taxes), "0.00")
        self.assertEqual(row.purchase_price, Decimal("358.05"))
        self.assertEqual(row.shares, Decimal("0.02693"))
        self.assertEqual(row.notes, "gross_dividend=9.78; entitled_quantity=4.255534; residual_amount=0.00")

    def test_equateplus_drip_multiline_record_is_assembled_deterministically(self) -> None:
        text = """Purchase activity statement Jeffrey Rodriguez
1 Jan 2025 - 31 Mar 2025 User ID: 204474190
Dividend Reinvestments
Gross Dividend Dividend Purchase Purchased Residual
Record Date Purchase Date Entitled Quantity Payment Taxes Fees Reinvested Price Shares Amount
12 Mar 2025 4.255534 9.78 USD
0.00 USD 0.14 USD 9.79 USD
358.05 USD 0.02693 0.00 USD
ESPP-PURCHASE at 2.3 USD
"""
        _, rows, issues = parse_hd_statement_text(text)

        self.assertFalse(issues)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.transaction_date, date(2025, 3, 12))
        self.assertEqual(row.amount, Decimal("9.79"))
        self.assertEqual(row.fees, Decimal("0.14"))
        self.assertEqual(str(row.taxes), "0.00")
        self.assertEqual(row.purchase_price, Decimal("358.05"))
        self.assertEqual(row.shares, Decimal("0.02693"))

    def test_parser_version_tracks_material_drip_mapping_change(self) -> None:
        self.assertEqual(PARSER_VERSION, "hd-espp-v4")

    def test_transfer_values_are_not_cost_basis(self) -> None:
        _, merrill, issues = parse_hd_statement_text(MERRILL_TRANSFERS)
        self.assertFalse(issues)
        self.assertEqual([row.transfer_value for row in merrill], [Decimal("5536.80"), Decimal("3639.90")])
        self.assertTrue(all(row.cost_basis is None for row in merrill))
        _, computershare, _ = parse_hd_statement_text(COMPUTERSHARE_TRANSFER)
        transfers = [row for row in computershare if row.transaction_type == "TRANSFER_OUT"]
        self.assertTrue(all(row.cost_basis is None for row in transfers))
        self.assertEqual([row.transfer_value for row in transfers], [None, Decimal("4259.06")])

    def test_existing_hd_transaction_table_migrates_new_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "history.sqlite"
            connection = sqlite3.connect(db)
            try:
                connection.execute("""CREATE TABLE hd_espp_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, transaction_date TEXT NOT NULL, transaction_type TEXT NOT NULL, source TEXT NOT NULL, source_account_type TEXT NOT NULL, shares TEXT, amount TEXT, purchase_price TEXT, fair_market_value TEXT, fees TEXT, taxes TEXT, cost_basis TEXT, acquisition_date TEXT, source_location TEXT, destination_location TEXT, source_document TEXT NOT NULL, source_period_start TEXT, source_period_end TEXT, transaction_fingerprint TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '', ambiguous INTEGER NOT NULL DEFAULT 0, transfer_match_id TEXT, created_at TEXT NOT NULL)""")
                connection.commit()
            finally:
                connection.close()
            connection = _connect(db)
            try:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(hd_espp_transactions)")}
                self.assertTrue({"transfer_value", "lot_sequence"}.issubset(columns))
            finally:
                connection.close()
            reopened = sqlite3.connect(db)
            try:
                persisted = {row[1] for row in reopened.execute("PRAGMA table_info(hd_espp_transactions)")}
            finally:
                reopened.close()
            self.assertTrue({"transfer_value", "lot_sequence"}.issubset(persisted))

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
        outgoing = HDTransaction(date(2024, 7, 15), "TRANSFER_OUT", "Computershare", "ESPP", shares=Decimal("15"), transfer_value=Decimal("4259.06"), source_location="Computershare", destination_location="Merrill")
        incoming = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), source_location="External", destination_location="Merrill")
        self.assertEqual(len(match_custody_transfers([outgoing, incoming])), 1)
        purchase = HDTransaction(date(2024, 6, 28), "ESPP_PURCHASE", "Computershare", "ESPP", shares=Decimal("15"))
        self.assertEqual(economic_share_ownership([purchase]), economic_share_ownership([purchase, outgoing, incoming]))

    def test_transfer_matching_requires_provenance_and_surfaces_ambiguity(self) -> None:
        outgoing = HDTransaction(date(2024, 7, 15), "TRANSFER_OUT", "Computershare", "ESPP", shares=Decimal("15"), transfer_value=Decimal("4259.06"), source_location="Computershare", destination_location="Merrill")
        wrong = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Other Broker", "Brokerage", shares=Decimal("15"), source_location="External", destination_location="Other Broker")
        right = HDTransaction(date(2024, 7, 16), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), transfer_value=Decimal("4259.06"), source_location="Computershare", destination_location="Merrill")
        self.assertEqual(match_custody_transfers([outgoing, wrong]), [])
        self.assertEqual(match_custody_transfers([outgoing, wrong, right]), [(outgoing.transaction_fingerprint, right.transaction_fingerprint)])
        second = HDTransaction(date(2024, 7, 17), "TRANSFER_IN", "Merrill", "Brokerage", shares=Decimal("15"), transfer_value=Decimal("5000"), source_location="External", destination_location="Merrill")
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

    def test_realized_lot_completeness_counts_recognizable_rows(self) -> None:
        aggregate = HDTransaction(date(2026, 2, 13), "SALE", "Merrill", "Brokerage", shares=Decimal("28"), amount=Decimal("10972.22"))
        one_lot = HDTransaction(date(2026, 2, 12), "SALE", "Merrill", "Realized lot", shares=Decimal("5.3021"), amount=Decimal("2077.71"), cost_basis=Decimal("1399.99"), acquisition_date=date(2023, 6, 30), lot_sequence=1)
        with patch("hd_espp_history._merrill", return_value=[aggregate, one_lot]):
            _, _, issues = parse_hd_statement_text(MERRILL)
        self.assertTrue(any("Recognized 10" in issue and "parsed 1" in issue for issue in issues))

    def test_record_type_distinguishes_aggregate_sale_and_realized_lot(self) -> None:
        _, rows, _ = parse_hd_statement_text(MERRILL)
        aggregate = next(row for row in rows if row.source_account_type == "Brokerage")
        lot = next(row for row in rows if row.source_account_type == "Realized lot")
        self.assertEqual(transaction_record_type(aggregate), "Brokerage Transaction")
        self.assertEqual(transaction_record_type(lot), "Realized Lot")

    def test_realized_lots_do_not_inflate_shares_sold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            self._import_texts(service, {"sale.pdf": MERRILL})
            state = service.state()
            self.assertEqual(state["summary"]["total_shares_sold"], Decimal("28.0000"))
            self.assertEqual(sum(row["source_account_type"] == "Realized lot" for row in state["transactions"]), 10)

    def test_transfer_lots_are_persisted_and_linked_to_aggregate_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            result = self._import_texts(service, {"transfer.pdf": COMPUTERSHARE_TRANSFER})
            state = service.state()
            aggregate = next(row for row in state["transactions"] if row["transfer_value"] is not None)

            self.assertEqual(result["transfer_lots"], 7)
            self.assertEqual(state["summary"]["transfer_lots"], 7)
            self.assertEqual(sum(Decimal(row["transferred_shares"]) for row in state["transfer_lots"]), Decimal("15.000000"))
            self.assertEqual({row["parent_transfer_fingerprint"] for row in state["transfer_lots"]}, {aggregate["transaction_fingerprint"]})
            self.assertTrue(all(row["reported_value"] and row["provenance"] for row in state["transfer_lots"]))

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

    def test_hd_position_checkpoints_are_persisted_separately_from_transactions(self) -> None:
        text = """The Home Depot, Inc ESPP - Employee Plan Statement
01 Jan 2024 - 31 Mar 2024
30 Jun 2023 Purchase 1,400.00 1,400.00 01 Jan 2023 315.860000 30 Jun 2023 310.640000 264.044000 5.302147 5.302147
The Home Depot Inc ESPP 5.302147 0.055252 5.357399 300.00 1,607.22
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            result = self._import_texts(service, {"checkpoint.pdf": text})
            state = service.state()

        self.assertEqual(result["position_checkpoints"], 1)
        self.assertEqual(result["position_checkpoint_occurrences"], 1)
        self.assertEqual(state["summary"]["position_checkpoints"], 1)
        self.assertEqual(state["summary"]["position_checkpoint_occurrences"], 1)
        self.assertEqual(state["documents"][0]["source_document"], "checkpoint.pdf")
        self.assertEqual(state["documents"][0]["parsed_transaction_occurrence_count"], 1)
        self.assertEqual(state["documents"][0]["earliest_parsed_transaction_date"], "2023-06-30")
        self.assertEqual(state["documents"][0]["latest_parsed_transaction_date"], "2023-06-30")
        self.assertEqual(state["documents"][0]["parsed_transaction_types"], "ESPP_PURCHASE")
        self.assertEqual(state["position_checkpoints"][0]["custodian"], "Computershare")
        self.assertEqual(state["position_checkpoints"][0]["shares"], "5.357399")
        self.assertEqual(len(state["transactions"]), 1)

    def test_duplicate_hd_position_checkpoints_keep_occurrence_provenance(self) -> None:
        first = """The Home Depot, Inc ESPP - Employee Plan Statement
01 Jan 2024 - 31 Mar 2024
The Home Depot Inc ESPP 5.302147 0.055252 5.357399 300.00 1,607.22
"""
        second = first.replace("Employee Plan Statement", "Employee Plan Statement")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); service = HDESPPHistoryService(root / "pdfs", root / "history.sqlite")
            result = self._import_texts(service, {"a.pdf": first, "b.pdf": second})
            state = service.state()

        self.assertEqual(result["position_checkpoints"], 1)
        self.assertEqual(result["position_checkpoint_occurrences"], 2)
        self.assertEqual(state["summary"]["position_checkpoints"], 1)
        self.assertEqual(state["summary"]["position_checkpoint_occurrences"], 2)
        self.assertEqual(state["position_checkpoints"][0]["occurrence_count"], 2)
        self.assertEqual(state["position_checkpoints"][0]["source_documents"], "a.pdf,b.pdf")

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

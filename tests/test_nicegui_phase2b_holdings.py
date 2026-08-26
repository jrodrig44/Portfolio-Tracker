from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from nicegui_app.services.portfolio_data import load_holdings
from nicegui_app.main import (
    HOLDING_NUMERIC_FIELDS,
    HOLDING_NUMERIC_VALUE_PARSER,
    add_aggrid_holding_row,
    current_aggrid_rows,
    holding_grid_column_defs,
    remove_selected_aggrid_rows,
)
from portfolio_holdings_write import (
    HoldingConfirmationError,
    HoldingValidationError,
    HoldingsWriteService,
)


COLUMNS = """
    ticker TEXT, shares REAL DEFAULT 0, amount_invested REAL DEFAULT 0,
    annual_div_per_share REAL DEFAULT 0, regular_biweekly_contribution REAL DEFAULT 0,
    espp_biweekly_contribution REAL DEFAULT 0, espp_contribution_biweekly REAL DEFAULT 0,
    special_contribution REAL DEFAULT 0, employer_stock INTEGER DEFAULT 0,
    espp_enabled INTEGER DEFAULT 0, is_espp INTEGER DEFAULT 0,
    espp_discount REAL DEFAULT 0, espp_discount_pct REAL DEFAULT 0,
    espp_purchase_frequency_months REAL DEFAULT 6, espp_purchase_frequency TEXT DEFAULT '',
    espp_acquired_shares REAL DEFAULT 0, sector TEXT, asset_type TEXT,
    preserved_note TEXT DEFAULT 'keep'
"""


def valid(ticker: str = "schd") -> dict[str, object]:
    return {
        "ticker": ticker, "shares": 1.123456789, "espp_acquired_shares": 0,
        "amount_invested": 50, "annual_div_per_share": 2.5,
        "regular_biweekly_contribution": 25, "espp_contribution_biweekly": 0,
        "special_contribution": 10, "is_espp": False, "espp_discount": 0,
        "espp_purchase_frequency_months": 6, "sector": "Technology", "asset_type": "ETF",
    }


class Phase2BHoldingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.temp.name) / "portfolio.db"
        self.history = Path(self.temp.name) / "history.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(f"CREATE TABLE holdings ({COLUMNS})")
        with sqlite3.connect(self.history) as connection:
            connection.execute("CREATE TABLE marker (value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('unchanged')")
        self.service = HoldingsWriteService(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_add_valid_fractional_holding_and_production_duplicate_behavior(self) -> None:
        first = self.service.add(valid(), submission_token="first")
        second = self.service.add(valid(" SCHD "), submission_token="second")
        self.assertEqual(first["ticker"], "SCHD")
        self.assertEqual(second["ticker"], "SCHD")
        rows = load_holdings(self.db)
        self.assertEqual(len(rows), 2)  # Streamlit's dynamic editor intentionally permits duplicate rows.
        self.assertAlmostEqual(rows[0]["shares"], 1.123456789)

    def test_invalid_ticker_and_numeric_inputs_are_rejected_without_writes(self) -> None:
        for field, value in (("ticker", "  "), ("shares", "bad"), ("shares", -1), ("shares", float("inf")), ("espp_discount", 1.01)):
            payload = valid()
            payload[field] = value
            with self.assertRaises(HoldingValidationError):
                self.service.add(payload)
        acquired = valid()
        acquired.update({"shares": 1, "espp_acquired_shares": 2})
        with self.assertRaises(HoldingValidationError):
            self.service.add(acquired)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 0)

    def test_hd_to_hd_edit_preserves_defaults_and_unrelated_columns(self) -> None:
        row = self.service.add(valid("HD"))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE holdings SET preserved_note='secret' WHERE rowid=?", (row["_holding_id"],))
        changed = valid("hd")
        changed.update({"shares": 2.987654321, "regular_biweekly_contribution": 280, "special_contribution": 500, "espp_contribution_biweekly": 0})
        result = self.service.edit(row["_holding_id"], changed)
        self.assertAlmostEqual(result["shares"], 2.987654321)
        self.assertEqual(result["preserved_note"], "secret")
        self.assertEqual(result["regular_biweekly_contribution"], 0)
        self.assertEqual(result["special_contribution"], 0)
        self.assertEqual(result["espp_contribution_biweekly"], 100)
        self.assertEqual(result["employer_stock"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_discount"], .15)
        self.assertEqual(result["espp_purchase_frequency_months"], 6)
        self.assertEqual(result["espp_purchase_frequency"], "approximately_every_6_months")
        self.assertAlmostEqual(result["espp_acquired_shares"], 2.987654321)

    def test_hd_to_non_hd_clears_only_hd_derived_metadata(self) -> None:
        row = self.service.add(valid("HD"))
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE holdings SET preserved_note='still-here' WHERE rowid=?", (row["_holding_id"],))
        changed = valid("SCHD")
        changed.update({"is_espp": True, "espp_discount": 0.12, "espp_acquired_shares": 0.5, "espp_purchase_frequency_months": 3})
        result = self.service.edit(row["_holding_id"], changed)
        self.assertEqual(result["employer_stock"], 0)
        self.assertEqual(result["espp_purchase_frequency"], "")
        self.assertEqual(result["preserved_note"], "still-here")
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_discount"], 0.12)
        self.assertEqual(result["espp_discount_pct"], 12)
        self.assertEqual(result["espp_acquired_shares"], 0.5)
        self.assertEqual(result["espp_purchase_frequency_months"], 3)

    def test_non_hd_to_hd_establishes_production_defaults(self) -> None:
        row = self.service.add(valid("SCHD"))
        changed = valid("HD")
        changed.update({"shares": 4, "regular_biweekly_contribution": 280, "special_contribution": 500, "espp_contribution_biweekly": 0})
        result = self.service.edit(row["_holding_id"], changed)
        self.assertEqual(result["employer_stock"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["regular_biweekly_contribution"], 0)
        self.assertEqual(result["special_contribution"], 0)
        self.assertEqual(result["espp_contribution_biweekly"], 100)
        self.assertEqual(result["espp_discount"], 0.15)
        self.assertEqual(result["espp_discount_pct"], 15)
        self.assertEqual(result["espp_purchase_frequency_months"], 6)
        self.assertEqual(result["espp_purchase_frequency"], "approximately_every_6_months")
        self.assertEqual(result["espp_acquired_shares"], 4)

    def test_non_hd_generic_espp_fields_survive_edit(self) -> None:
        payload = valid("ACME")
        payload.update({"is_espp": True, "espp_contribution_biweekly": 75, "espp_discount": 0.10, "espp_acquired_shares": 0.75, "espp_purchase_frequency_months": 3})
        row = self.service.add(payload)
        result = self.service.edit(row["_holding_id"], {**payload, "shares": 2})
        self.assertEqual(result["employer_stock"], 0)
        self.assertEqual(result["espp_purchase_frequency"], "")
        self.assertEqual(result["is_espp"], 1)
        self.assertEqual(result["espp_enabled"], 1)
        self.assertEqual(result["espp_contribution_biweekly"], 75)
        self.assertEqual(result["espp_discount"], 0.10)
        self.assertEqual(result["espp_acquired_shares"], 0.75)
        self.assertEqual(result["espp_purchase_frequency_months"], 3)

    def test_delete_requires_confirmation_and_removes_only_stable_row(self) -> None:
        first = self.service.add(valid("SCHD"))
        second = self.service.add(valid("SCHD"))
        with self.assertRaises(HoldingConfirmationError):
            self.service.delete(first["_holding_id"], confirmed=False)
        self.service.delete(first["_holding_id"], confirmed=True, expected_ticker="SCHD")
        remaining = load_holdings(self.db)
        self.assertEqual([row["_holding_id"] for row in remaining], [second["_holding_id"]])

    def test_failed_write_rolls_back_and_duplicate_submission_is_idempotent(self) -> None:
        self.service.add(valid(), submission_token="same-click")
        self.assertIsNone(self.service.add(valid(), submission_token="same-click"))
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TRIGGER fail_edit BEFORE UPDATE ON holdings BEGIN SELECT RAISE(ABORT, 'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.edit(1, {**valid(), "shares": 99})
        self.assertAlmostEqual(load_holdings(self.db)[0]["shares"], 1.123456789)

    def test_no_schema_migration_or_history_write(self) -> None:
        with sqlite3.connect(self.db) as connection:
            before = connection.execute("PRAGMA table_info(holdings)").fetchall()
        self.service.add(valid())
        with sqlite3.connect(self.db) as connection:
            after = connection.execute("PRAGMA table_info(holdings)").fetchall()
        with sqlite3.connect(self.history) as connection:
            marker = connection.execute("SELECT value FROM marker").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(marker, "unchanged")

    def test_ui_contract_has_spreadsheet_actions_and_invalidation(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("ui.aggrid", source)
        self.assertIn('ui.button("Add Row"', source)
        self.assertIn('ui.button("Remove Selected Rows"', source)
        self.assertIn('ui.button("Save Holdings"', source)
        self.assertNotIn('ui.button("Add Holding"', source)
        self.assertNotIn("Edit or delete a holding", source)
        self.assertIn("w-full sm:w-auto min-h-11", source)
        self.assertIn("refresh_after_holding_write", source)
        self.assertIn('"AI Analysis"', source)
        self.assertIn("prior prepared output is stale", source)
        holdings_ui = source.split("def render_holdings()", 1)[1].split("async def render_statement_history", 1)[0]
        self.assertIn('":getRowId": "params => params.data._grid_key"', holdings_ui)
        self.assertIn("add_aggrid_holding_row(grid)", holdings_ui)
        self.assertIn("remove_selected_aggrid_rows(grid)", holdings_ui)
        self.assertNotIn('grid.options["rowData"].append', holdings_ui)
        self.assertNotIn("grid.update()", holdings_ui)
        self.assertIn("Removed rows are not deleted from the database until you click Save Holdings.", holdings_ui)

    def test_holding_column_definitions_use_valid_dynamic_numeric_parser(self) -> None:
        columns = {column["field"]: column for column in holding_grid_column_defs()}
        expected_numeric = {
            "shares", "espp_acquired_shares", "amount_invested", "annual_div_per_share",
            "regular_biweekly_contribution", "espp_contribution_biweekly", "special_contribution",
            "espp_discount", "espp_purchase_frequency_months",
        }
        self.assertEqual(HOLDING_NUMERIC_FIELDS, expected_numeric)
        for field in expected_numeric:
            self.assertEqual(columns[field]["type"], "numericColumn")
            self.assertEqual(columns[field][":valueParser"], HOLDING_NUMERIC_VALUE_PARSER)
            self.assertNotIn("valueParser", columns[field])
        self.assertNotIn("Number(params.newValue)", HOLDING_NUMERIC_VALUE_PARSER)
        self.assertIn("Number.isFinite(parsed)", HOLDING_NUMERIC_VALUE_PARSER)
        self.assertIn("params.oldValue", HOLDING_NUMERIC_VALUE_PARSER)
        self.assertNotIn(":valueParser", columns["ticker"])
        self.assertEqual(columns["is_espp"]["cellEditor"], "agCheckboxCellEditor")
        self.assertEqual(columns["is_espp"]["cellRenderer"], "agCheckboxCellRenderer")
        self.assertEqual(columns["asset_type"]["cellEditor"], "agSelectCellEditor")
        self.assertIn("ETF", columns["asset_type"]["cellEditorParams"]["values"])

        parser = lambda new_value: float(new_value)
        self.assertEqual(parser("1.123456789"), 1.123456789)
        self.assertEqual(parser("10"), 10.0)

    def test_add_and_remove_sync_server_membership_with_client_transactions(self) -> None:
        existing_edit = {"_holding_id": 3, "_grid_key": "existing-3", "ticker": "KEEP", "shares": 9.75}
        removed_row = {"_holding_id": 7, "_grid_key": "existing-7", "ticker": "DUP", "shares": 1}

        class FakeProps:
            def __init__(self, grid: object) -> None:
                self.grid = grid

            @contextmanager
            def suspend_updates(self):
                self.grid.calls.append(("suspend_updates", "enter"))
                self.grid.updates_suspended = True
                try:
                    yield
                finally:
                    self.grid.updates_suspended = False
                    self.grid.calls.append(("suspend_updates", "exit"))

        class FakeGrid:
            def __init__(self) -> None:
                self.calls = []
                self.updates_suspended = False
                grid = self

                class SuspendedList(list):
                    def append(self, item: object) -> None:
                        if not grid.updates_suspended:
                            raise AssertionError("rowData append was not suspended")
                        super().append(item)

                    def __delitem__(self, index: object) -> None:
                        if not grid.updates_suspended:
                            raise AssertionError("rowData removal was not suspended")
                        super().__delitem__(index)

                self.options = {"rowData": SuspendedList([existing_edit, removed_row])}
                self.selected = [removed_row.copy()]
                self.props = FakeProps(self)

            def run_grid_method(self, name: str, transaction: dict[str, object]) -> None:
                self.calls.append((name, transaction))

            async def get_selected_rows(self) -> list[dict[str, object]]:
                return self.selected

        grid = FakeGrid()
        added = asyncio.run(add_aggrid_holding_row(grid))
        removed = asyncio.run(remove_selected_aggrid_rows(grid))
        self.assertTrue(str(added["_grid_key"]).startswith("new-"))
        self.assertEqual(removed, 1)
        self.assertEqual(grid.options["rowData"], [existing_edit, added])
        self.assertIs(grid.options["rowData"][0], existing_edit)
        self.assertEqual(existing_edit["shares"], 9.75)
        self.assertEqual(grid.calls, [
            ("suspend_updates", "enter"),
            ("suspend_updates", "exit"),
            ("applyTransaction", {"add": [added]}),
            ("suspend_updates", "enter"),
            ("suspend_updates", "exit"),
            ("applyTransaction", {"remove": grid.selected}),
        ])
        self.assertFalse(hasattr(grid, "update"))

    def test_combined_grid_save_adds_edits_and_deletes_by_stable_id(self) -> None:
        edited = self.service.add(valid("DUP"))
        removed = self.service.add(valid("DUP"))
        expected = load_holdings(self.db)
        edited_row = {**expected[0], "shares": 9.75, "espp_contribution_biweekly": 0, "is_espp": False}
        new_row = {**valid("NEW"), "_holding_id": None, "_grid_key": "new-combined"}

        result = self.service.save_grid([edited_row, new_row], expected_rows=expected)

        self.assertEqual(result, {"added": 1, "updated": 1, "unchanged": 0, "deleted": 1})
        reloaded = load_holdings(self.db)
        self.assertEqual({row["ticker"] for row in reloaded}, {"DUP", "NEW"})
        surviving_duplicate = next(row for row in reloaded if row["ticker"] == "DUP")
        self.assertEqual(surviving_duplicate["_holding_id"], edited["_holding_id"])
        self.assertNotEqual(surviving_duplicate["_holding_id"], removed["_holding_id"])
        self.assertEqual(surviving_duplicate["shares"], 9.75)

    def test_combined_grid_save_rolls_back_every_operation_on_failure(self) -> None:
        existing = self.service.add(valid("KEEP"))
        expected = load_holdings(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TRIGGER fail_grid_edit BEFORE UPDATE ON holdings BEGIN SELECT RAISE(ABORT, 'failure'); END")
        new_row = {**valid("NEW"), "_holding_id": None, "_grid_key": "new-rollback"}
        edited_row = {**expected[0], "shares": 99, "espp_contribution_biweekly": 0, "is_espp": False}

        with self.assertRaises(sqlite3.IntegrityError):
            self.service.save_grid([new_row, edited_row], expected_rows=expected)

        reloaded = load_holdings(self.db)
        self.assertEqual([(row["_holding_id"], row["ticker"], row["shares"]) for row in reloaded], [(existing["_holding_id"], "KEEP", expected[0]["shares"])])

    def test_client_grid_rows_commit_active_edit_and_return_browser_values(self) -> None:
        class FakeGrid:
            def __init__(self) -> None:
                self.options = {"rowData": [{"_holding_id": None, "ticker": "", "shares": 0}]}
                self.calls: list[object] = []
                self.committed = False

            async def run_grid_method(self, name: str) -> None:
                self.calls.append(("run_grid_method", name))
                self.committed = True

            async def get_client_data(self, *, method: str) -> list[dict[str, object]]:
                self.calls.append(("get_client_data", method))
                if not self.committed:
                    return list(self.options["rowData"])
                return [{
                    "_holding_id": None,
                    "_grid_key": "new-test",
                    "ticker": "ZZZZ",
                    "shares": 1,
                    "amount_invested": 1,
                    "asset_type": "Stock",
                }]

        grid = FakeGrid()
        rows = asyncio.run(current_aggrid_rows(grid))
        self.assertEqual(grid.calls, [
            ("run_grid_method", "stopEditing"),
            ("get_client_data", "all_unsorted"),
        ])
        self.assertEqual(rows[0]["ticker"], "ZZZZ")
        self.assertEqual(rows[0]["shares"], 1)
        self.assertEqual(rows[0]["amount_invested"], 1)
        self.assertEqual(rows[0]["asset_type"], "Stock")
        self.assertEqual(grid.options["rowData"][0]["ticker"], "")

    def test_ui_save_uses_returned_client_rows_not_server_row_data(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        handler = source.split("async def save_grid()", 1)[1].split("def clear_ai_outputs", 1)[0]
        self.assertIn("client_rows = await current_aggrid_rows(grid)", handler)
        self.assertIn("holdings_writer.save_grid,\n                        client_rows,", handler)
        self.assertNotIn('list(grid.options["rowData"])', handler)

    def test_browser_rows_persist_existing_edit_and_exact_new_row(self) -> None:
        existing = self.service.add(valid("KEEP"))
        expected = load_holdings(self.db)
        edited = {
            **expected[0],
            "shares": 3.25,
            "espp_contribution_biweekly": expected[0]["espp_biweekly_contribution"],
            "is_espp": expected[0]["espp_enabled"],
        }
        new_row = {
            "_holding_id": None, "_grid_key": "new-zzzz", "ticker": "ZZZZ",
            "shares": 1, "espp_acquired_shares": 0, "amount_invested": 1,
            "annual_div_per_share": 0, "regular_biweekly_contribution": 0,
            "espp_contribution_biweekly": 0, "special_contribution": 0,
            "is_espp": False, "espp_discount": 0,
            "espp_purchase_frequency_months": 6, "sector": "", "asset_type": "Stock",
        }

        class FakeGrid:
            async def run_grid_method(self, name: str) -> None:
                self.stopped = name == "stopEditing"

            async def get_client_data(self, *, method: str) -> list[dict[str, object]]:
                self.assertions = (self.stopped, method)
                return [edited, new_row]

        grid = FakeGrid()
        client_rows = asyncio.run(current_aggrid_rows(grid))
        self.assertEqual(grid.assertions, (True, "all_unsorted"))
        self.service.save_grid(client_rows, expected_rows=expected)
        reloaded = load_holdings(self.db)
        keep = next(row for row in reloaded if row["ticker"] == "KEEP")
        bogus = next(row for row in reloaded if row["ticker"] == "ZZZZ")
        self.assertEqual(keep["_holding_id"], existing["_holding_id"])
        self.assertEqual(keep["shares"], 3.25)
        self.assertEqual((bogus["shares"], bogus["amount_invested"], bogus["asset_type"]), (1, 1, "Stock"))
        self.service.delete(bogus["_holding_id"], confirmed=True, expected_ticker="ZZZZ")
        self.assertEqual([row["ticker"] for row in load_holdings(self.db)], ["KEEP"])


if __name__ == "__main__":
    unittest.main()

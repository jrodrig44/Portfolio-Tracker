import sqlite3
import tempfile
import unittest
from pathlib import Path

from nicegui_app.services.portfolio_data import filter_history, load_history, load_holdings, portfolio_summary


class NiceGuiReadOnlyPocTests(unittest.TestCase):
    def _portfolio_db(self, root: Path) -> Path:
        path = root / "portfolio.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE holdings (ticker TEXT, shares REAL, amount_invested REAL, current_price REAL, market_value REAL, annualized_income REAL, dividend_yield_pct REAL, asset_type TEXT, sector TEXT)")
            connection.execute("INSERT INTO holdings VALUES ('ABC', 2, 80, 50, 100, 5, 5, 'Stock', 'Tech')")
        return path

    def _history_db(self, root: Path) -> Path:
        path = root / "history.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE statement_portfolio_history (history_date TEXT, portfolio_value REAL, source_type TEXT)")
            connection.execute("CREATE TABLE live_portfolio_snapshots (snapshot_date TEXT, portfolio_value REAL, source_type TEXT)")
            connection.execute("INSERT INTO statement_portfolio_history VALUES ('2024-02-29', 100, 'stash_statement')")
            connection.execute("INSERT INTO live_portfolio_snapshots VALUES ('2024-05-31', 120, 'live_portfolio_snapshot')")
        return path

    def test_portfolio_load_and_summary_use_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            holdings = load_holdings(self._portfolio_db(Path(directory)))
        self.assertEqual(holdings[0]["ticker"], "ABC")
        self.assertEqual(portfolio_summary(holdings)["portfolio_value"], 100)
        self.assertEqual(portfolio_summary(holdings)["unrealized_gain"], 20)

    def test_history_load_reads_both_existing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = load_history(self._history_db(Path(directory)))
        self.assertEqual([row["portfolio_value"] for row in rows], [100, 120])

    def test_ranges_match_streamlit_behavior_and_invalid_fails(self) -> None:
        history = [{"history_date": date, "portfolio_value": value} for date, value in (("2023-05-31", 1), ("2024-02-29", 2), ("2024-04-30", 3), ("2024-05-31", 4))]
        self.assertEqual(len(filter_history(history, "1M")), 2)
        self.assertEqual(len(filter_history(history, "3M")), 3)
        self.assertEqual(len(filter_history(history, "1Y")), 4)
        self.assertEqual(len(filter_history(history, "ALL")), 4)
        with self.assertRaisesRegex(ValueError, "Unsupported history range"):
            filter_history(history, "6M")

    def test_database_schemas_are_unchanged_by_page_data_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio = self._portfolio_db(root)
            history = self._history_db(root)
            before = [path.read_bytes() for path in (portfolio, history)]
            load_holdings(portfolio)
            load_history(history)
            self.assertEqual(before, [path.read_bytes() for path in (portfolio, history)])

    def test_main_configuration_and_forbidden_side_effects(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('HOST = "0.0.0.0"', source)
        self.assertIn("PORT = 8502", source)
        self.assertIn("ui.toggle(list(HISTORY_RANGE_OPTIONS)", source)
        for forbidden in ("upsert_live_portfolio_snapshot", "run_statement_scan", "import_validated_history", "supplemental_research_worker", "start_worker"):
            self.assertNotIn(forbidden, source)

    def test_no_copied_database_is_required(self) -> None:
        self.assertFalse((Path(__file__).parents[1] / "nicegui_app" / "data").exists())


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from nicegui_app.services.portfolio_data import load_history, load_holdings
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator


ROOT = Path(__file__).resolve().parents[1]


class NiceGuiLiveHistoryRefreshTests(unittest.TestCase):
    def databases(self, root: Path) -> tuple[Path, Path]:
        portfolio = root / "portfolio.db"
        history = root / "history.sqlite"
        with closing(sqlite3.connect(portfolio)) as connection:
            connection.execute(
                "CREATE TABLE holdings (ticker TEXT, shares REAL, amount_invested REAL, current_price REAL)"
            )
            connection.execute("INSERT INTO holdings VALUES ('ABC', 2, 80, 45)")
            connection.commit()
        with closing(sqlite3.connect(history)) as connection:
            connection.execute(
                "CREATE TABLE statement_portfolio_history "
                "(history_date TEXT, portfolio_value REAL, source_type TEXT)"
            )
            connection.execute(
                "CREATE TABLE live_portfolio_snapshots "
                "(snapshot_date TEXT PRIMARY KEY, portfolio_value REAL NOT NULL, "
                "source_type TEXT NOT NULL DEFAULT 'live_portfolio_snapshot')"
            )
            connection.execute(
                "INSERT INTO statement_portfolio_history VALUES "
                "('2026-07-31', 75, 'stash_statement')"
            )
            connection.commit()
        return portfolio, history

    def test_price_only_refresh_upserts_today_and_reloads_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portfolio, history_db = self.databases(Path(directory))
            history_loader = Mock(side_effect=load_history)
            market_loader = Mock(side_effect=[({"ABC": 50.0}, {}), ({"ABC": 62.5}, {})])
            coordinator = ReadOnlyRefreshCoordinator(
                portfolio,
                history_db,
                holdings_loader=load_holdings,
                history_loader=history_loader,
                market_loader=market_loader,
            )

            first = coordinator.refresh()
            second = coordinator.refresh()

            self.assertTrue(first.live_snapshot_changed)
            self.assertTrue(second.metrics_changed)
            self.assertFalse(second.portfolio_changed)
            self.assertTrue(second.live_snapshot_changed)
            self.assertTrue(second.history_changed)
            self.assertEqual(second.history[-1]["history_date"], date.today().isoformat())
            self.assertEqual(second.history[-1]["portfolio_value"], 125.0)
            self.assertEqual(history_loader.call_count, 2)
            with closing(sqlite3.connect(history_db)) as connection:
                live_rows = connection.execute(
                    "SELECT snapshot_date, portfolio_value FROM live_portfolio_snapshots"
                ).fetchall()
                statement_rows = connection.execute(
                    "SELECT history_date, portfolio_value, source_type FROM statement_portfolio_history"
                ).fetchall()
            self.assertEqual(live_rows, [(date.today().isoformat(), 125.0)])
            self.assertEqual(statement_rows, [("2026-07-31", 75.0, "stash_statement")])

    def test_incomplete_or_invalid_live_prices_do_not_write_history(self) -> None:
        for prices in ({}, {"ABC": float("nan")}, {"ABC": 0.0}):
            with self.subTest(prices=prices), tempfile.TemporaryDirectory() as directory:
                portfolio, history_db = self.databases(Path(directory))
                coordinator = ReadOnlyRefreshCoordinator(
                    portfolio,
                    history_db,
                    market_loader=Mock(return_value=(prices, {})),
                )
                result = coordinator.refresh()
                self.assertFalse(result.live_snapshot_changed)
                with closing(sqlite3.connect(history_db)) as connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM live_portfolio_snapshots"
                    ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_loaded_history_is_rerendered_when_coordinator_reports_change(self) -> None:
        source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        refresh_block = source[source.index("    async def refresh_data"):source.index("    async def refresh_after_holding_write")]
        self.assertIn('if result.history_changed:', refresh_block)
        self.assertIn('if loaded["History"]: render_history(history_range)', refresh_block)


if __name__ == "__main__":
    unittest.main()

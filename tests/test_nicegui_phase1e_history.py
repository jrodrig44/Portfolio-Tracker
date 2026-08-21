import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from nicegui_app.services.history import history_package
from nicegui_app.services.portfolio_data import load_history
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator
from portfolio_history import (
    HISTORY_RANGE_OPTIONS,
    build_history_package,
    filter_history_range,
    normalize_history,
)


class NiceGuiPhase1EHistoryTests(unittest.TestCase):
    def history(self):
        return [
            {"history_date": "2025-05-19", "portfolio_value": 80.0, "source_type": "statement"},
            {"history_date": "2025-08-20", "portfolio_value": 90.0, "source_type": "statement"},
            {"history_date": "2026-05-20", "portfolio_value": 100.0, "source_type": "statement"},
            {"history_date": "2026-07-19", "portfolio_value": 105.0, "source_type": "live"},
            {"history_date": "2026-07-20", "portfolio_value": 110.0, "source_type": "statement"},
            {"history_date": "2026-08-20", "portfolio_value": 120.0, "source_type": "live"},
        ]

    def databases(self, root: Path):
        portfolio = root / "portfolio.db"
        history = root / "history.sqlite"
        with closing(sqlite3.connect(portfolio)) as connection:
            connection.execute("CREATE TABLE holdings (ticker TEXT, shares REAL, amount_invested REAL, current_price REAL)")
            connection.execute("INSERT INTO holdings VALUES ('ABC', 1, 80, 100)")
            connection.commit()
        with closing(sqlite3.connect(history)) as connection:
            connection.execute("CREATE TABLE statement_portfolio_history (history_date TEXT, portfolio_value REAL, source_type TEXT)")
            connection.execute("CREATE TABLE live_portfolio_snapshots (snapshot_date TEXT, portfolio_value REAL, source_type TEXT)")
            connection.execute("INSERT INTO statement_portfolio_history VALUES ('2026-07-20', 100, 'stash_statement')")
            connection.commit()
        return portfolio, history

    def test_normalization_sorts_and_preserves_production_union_all_duplicates(self):
        rows = [
            {"history_date": "2026-02-01", "portfolio_value": 120},
            {"history_date": "2026-01-01", "portfolio_value": 100, "source_type": "statement"},
            {"history_date": "2026-01-01", "portfolio_value": 101, "source_type": "live"},
        ]
        normalized = normalize_history(rows)
        self.assertEqual([row["portfolio_value"] for row in normalized], [100.0, 101.0, 120.0])
        self.assertEqual(len(normalized), 3)

    def test_calendar_boundaries_match_streamlit_for_every_range(self):
        expected = {
            "1M": ["2026-07-20", "2026-08-20"],
            "3M": ["2026-05-20", "2026-07-19", "2026-07-20", "2026-08-20"],
            "1Y": ["2025-08-20", "2026-05-20", "2026-07-19", "2026-07-20", "2026-08-20"],
            "ALL": [row["history_date"] for row in self.history()],
        }
        self.assertEqual(HISTORY_RANGE_OPTIONS, ("1M", "3M", "1Y", "ALL"))
        for selected_range, dates in expected.items():
            with self.subTest(selected_range=selected_range):
                package = history_package(list(reversed(self.history())), selected_range)
                self.assertEqual(package["series"]["dates"], dates)
                self.assertEqual(package["summary"]["starting_date"], dates[0])
                self.assertEqual(package["summary"]["ending_date"], dates[-1])

    def test_summary_uses_first_and_last_nominal_values(self):
        summary = history_package(self.history(), "3M")["summary"]
        self.assertEqual(summary["starting_portfolio_value"], 100.0)
        self.assertEqual(summary["ending_portfolio_value"], 120.0)
        self.assertEqual(summary["absolute_change"], 20.0)
        self.assertEqual(summary["percentage_change"], 20.0)
        self.assertEqual(summary["point_count"], 4)

    def test_empty_single_point_invalid_null_and_missing_months(self):
        invalid = [
            {"history_date": None, "portfolio_value": 1},
            {"history_date": "bad", "portfolio_value": 2},
            {"history_date": "2026-01-01", "portfolio_value": None},
            {"history_date": "2026-02-01", "portfolio_value": "nan"},
            {"history_date": "2026-03-01", "portfolio_value": "invalid"},
        ]
        self.assertEqual(history_package(invalid)["records"], [])
        empty = history_package([])["summary"]
        self.assertEqual((empty["point_count"], empty["absolute_change"]), (0, 0.0))
        single = history_package([{"history_date": "2026-08-20", "portfolio_value": 125}], "1Y")
        self.assertEqual(single["summary"]["percentage_change"], 0.0)
        self.assertEqual(single["series"]["portfolio_values"], [125.0])
        missing_months = filter_history_range([
            {"history_date": "2025-08-20", "portfolio_value": 80},
            {"history_date": "2026-08-20", "portfolio_value": 120},
        ], "ALL")
        self.assertEqual(len(missing_months), 2)

    def test_wrapper_matches_shared_builder_and_has_no_unproduced_series(self):
        wrapped = history_package(self.history(), "1M")
        self.assertEqual(wrapped, build_history_package(self.history(), "1M"))
        self.assertEqual(set(wrapped["series"]), {"dates", "portfolio_values"})

    def test_range_changes_filter_memory_without_sqlite_or_yahoo(self):
        history_loader = Mock()
        yahoo_loader = Mock()
        rows = self.history()
        for selected_range in HISTORY_RANGE_OPTIONS:
            history_package(rows, selected_range)
        history_loader.assert_not_called()
        yahoo_loader.assert_not_called()

    def test_history_fingerprint_reload_rules_and_manual_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            portfolio, history_db = self.databases(Path(directory))
            history_loader = Mock(side_effect=load_history)
            coordinator = ReadOnlyRefreshCoordinator(portfolio, history_db, history_loader=history_loader)
            first = coordinator.refresh()
            unchanged = coordinator.refresh()
            self.assertTrue(first.history_changed)
            self.assertFalse(unchanged.history_changed)
            self.assertEqual(history_loader.call_count, 1)
            with closing(sqlite3.connect(history_db)) as connection:
                connection.execute("INSERT INTO live_portfolio_snapshots VALUES ('2026-08-20', 120, 'live_portfolio_snapshot')")
                connection.commit()
            os.utime(history_db, None)
            changed = coordinator.refresh()
            self.assertTrue(changed.history_changed)
            self.assertEqual(history_loader.call_count, 2)
            forced = coordinator.refresh(force=True)
            self.assertTrue(forced.history_changed)
            self.assertEqual(history_loader.call_count, 3)

    def test_history_reader_is_read_only_and_drops_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            _, history_db = self.databases(Path(directory))
            with closing(sqlite3.connect(history_db)) as connection:
                connection.execute("INSERT INTO live_portfolio_snapshots VALUES ('2026-08-21', 'nan', 'live')")
                connection.commit()
            before = history_db.read_bytes()
            rows = load_history(history_db)
            self.assertEqual([row["portfolio_value"] for row in rows], [100.0])
            self.assertEqual(history_db.read_bytes(), before)

    def test_streamlit_and_nicegui_use_shared_filter(self):
        import app

        for selected_range in HISTORY_RANGE_OPTIONS:
            self.assertEqual(
                app._filter_performance_history(self.history(), selected_range),
                history_package(self.history(), selected_range)["records"],
            )


if __name__ == "__main__":
    unittest.main()

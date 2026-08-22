from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from nicegui_app.main import critical_io
from nicegui_app.process_lifecycle import LifecycleCoordinator, ShutdownInProgress
from shared_data_preparation import (
    SharedDataPreparationError,
    SharedDataPreparationInProgress,
    _PREPARATION_LOCK,
    prepare_shared_data,
)


ROOT = Path(__file__).resolve().parents[1]


class SharedDataPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.portfolio_db = root / "portfolio.db"
        self.history_db = root / "stash_statement_history.sqlite"
        with closing(sqlite3.connect(self.portfolio_db)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE holdings (ticker TEXT, shares REAL, amount_invested REAL, annual_div_per_share REAL, asset_type TEXT, sector TEXT)"
                )
                connection.execute("INSERT INTO holdings VALUES ('APLE', 2, 100, 2, 'REIT', 'Real Estate')")
        with closing(sqlite3.connect(self.history_db)) as connection:
            with connection:
                connection.execute("CREATE TABLE statement_portfolio_history (history_date TEXT, portfolio_value REAL, source_type TEXT)")
                connection.execute(
                    "CREATE TABLE live_portfolio_snapshots (snapshot_date TEXT PRIMARY KEY, portfolio_value REAL, source_type TEXT NOT NULL DEFAULT 'live_portfolio_snapshot')"
                )
                connection.execute("INSERT INTO statement_portfolio_history VALUES ('2026-01-01', 100, 'stash_statement')")
        self.market_loader = Mock(return_value=({"APLE": 75.0}, {"APLE": 2.0}))
        self.cache_clearer = Mock()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def prepare(self, **kwargs):
        return prepare_shared_data(
            self.portfolio_db,
            self.history_db,
            market_loader=self.market_loader,
            market_cache_clearer=self.cache_clearer,
            **kwargs,
        )

    def test_valid_shared_databases_market_refresh_snapshot_and_verification(self) -> None:
        result = self.prepare()

        self.assertTrue(result.ready)
        self.assertEqual(result.holdings_count, 1)
        self.assertEqual(result.portfolio_database_status, "OK")
        self.assertEqual(result.history_database_status, "OK")
        self.assertEqual(result.market_refresh_status, "Complete")
        self.assertEqual(result.live_snapshot_status, "Updated")
        self.assertEqual(result.sqlite_checkpoint_status, "Complete")
        self.cache_clearer.assert_called_once_with()
        self.market_loader.assert_called_once_with(["APLE"])
        with closing(sqlite3.connect(self.history_db)) as connection:
            snapshots = connection.execute("SELECT portfolio_value FROM live_portfolio_snapshots").fetchall()
        self.assertEqual(snapshots, [(150.0,)])

    def test_repeated_preparation_is_idempotent_and_does_not_duplicate_data(self) -> None:
        first = self.prepare()
        second = self.prepare()

        self.assertEqual(first.live_snapshot_status, "Updated")
        self.assertEqual(second.live_snapshot_status, "Already current")
        with closing(sqlite3.connect(self.portfolio_db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM holdings").fetchone()[0], 1)
        with closing(sqlite3.connect(self.history_db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM statement_portfolio_history").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM live_portfolio_snapshots").fetchone()[0], 1)

    def test_existing_live_snapshot_writer_is_reused(self) -> None:
        writer = Mock()

        result = self.prepare(snapshot_writer=writer)

        self.assertEqual(result.live_snapshot_status, "Updated")
        writer.assert_called_once()
        self.assertEqual(writer.call_args.args[0], 150.0)
        self.assertEqual(writer.call_args.kwargs["db_path"], self.history_db)

    def test_checkpoint_targets_only_authoritative_databases(self) -> None:
        checkpoints: list[Path] = []

        with patch("shared_data_preparation._checkpoint_database", side_effect=lambda path, stage: checkpoints.append(path)):
            self.prepare()

        self.assertEqual(checkpoints, [self.portfolio_db, self.history_db])

    def test_history_verification_failure_prevents_ready_result(self) -> None:
        with closing(sqlite3.connect(self.history_db)) as connection:
            with connection:
                connection.execute("DROP TABLE statement_portfolio_history")
        self.market_loader.return_value = ({"APLE": 0.0}, {"APLE": 2.0})

        with self.assertRaises(SharedDataPreparationError) as raised:
            self.prepare()

        self.assertIn(raised.exception.stage, {"History read", "History database verification"})

    def test_conflicting_preparation_is_rejected_without_distributed_files(self) -> None:
        self.assertTrue(_PREPARATION_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(SharedDataPreparationInProgress):
                self.prepare()
        finally:
            _PREPARATION_LOCK.release()
        self.assertEqual(
            sorted(path.name for path in self.portfolio_db.parent.iterdir()),
            ["portfolio.db", "stash_statement_history.sqlite"],
        )

    def test_both_interfaces_call_the_same_service_and_nicegui_is_lifecycle_protected(self) -> None:
        streamlit_source = (ROOT / "app.py").read_text(encoding="utf-8")
        nicegui_source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("from shared_data_preparation import", streamlit_source)
        self.assertIn("from shared_data_preparation import prepare_shared_data", nicegui_source)
        self.assertIn('st.button("Prepare Shared Data"', streamlit_source)
        self.assertIn('ui.button("Prepare Shared Data"', nicegui_source)
        self.assertIn('"prepare_shared_data",\n                        prepare_shared_data,', nicegui_source)

        coordinator = LifecycleCoordinator()
        coordinator.begin_shutdown()
        with patch("nicegui_app.main.lifecycle", coordinator), patch("nicegui_app.main.ui.notify"):
            with self.assertRaises(ShutdownInProgress):
                asyncio.run(critical_io("prepare_shared_data", self.prepare))
        self.market_loader.assert_not_called()

    def test_preparation_does_not_trigger_research_or_expose_credentials(self) -> None:
        source = (ROOT / "shared_data_preparation.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("supplemental_research", source)
        self.assertNotIn("onemin", source)
        self.assertNotIn("api_key", source)
        self.assertNotIn("authorization", source)
        self.assertNotIn("environ", source)

    def test_runtime_databases_are_ignored_and_not_tracked(self) -> None:
        ignored = subprocess.run(
            ["git", "check-ignore", "data/portfolio.db", "data/stash_statement_history.sqlite"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--", "data/portfolio.db", "data/stash_statement_history.sqlite"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("data/portfolio.db", ignored.stdout)
        self.assertIn("data/stash_statement_history.sqlite", ignored.stdout)
        self.assertEqual(tracked.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()

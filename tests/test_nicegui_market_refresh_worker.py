from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

from nicegui_app.market_refresh_worker import MarketRefreshWorker, market_session_state
from nicegui_app.services import yahoo
from shared_data_preparation import persist_live_snapshot_if_ready
from stash_history import ensure_history_schema


EASTERN = ZoneInfo("America/New_York")


class MarketCalendarTests(unittest.TestCase):
    def test_normal_session_weekend_and_holiday(self) -> None:
        regular = market_session_state(datetime(2026, 8, 31, 12, 0, tzinfo=EASTERN))
        self.assertTrue(regular.is_open)
        for value in (
            datetime(2026, 8, 30, 12, 0, tzinfo=EASTERN),  # Sunday
            datetime(2026, 9, 7, 12, 0, tzinfo=EASTERN),  # Labor Day
        ):
            state = market_session_state(value)
            self.assertFalse(state.is_open)
            self.assertIsNone(state.session_date)

    def test_early_close_uses_exchange_schedule(self) -> None:
        before = market_session_state(datetime(2025, 11, 28, 12, 59, tzinfo=EASTERN))
        after = market_session_state(datetime(2025, 11, 28, 13, 2, tzinfo=EASTERN), close_grace_seconds=60)
        self.assertTrue(before.is_open)
        self.assertEqual(before.session_close.astimezone(EASTERN).hour, 13)
        self.assertFalse(after.is_open)
        self.assertTrue(after.final_refresh_due)

    def test_dst_conversion_is_calendar_driven(self) -> None:
        winter = market_session_state(datetime(2026, 1, 5, 10, 0, tzinfo=EASTERN))
        summer = market_session_state(datetime(2026, 7, 6, 10, 0, tzinfo=EASTERN))
        self.assertEqual(winter.session_open.hour, 14)
        self.assertEqual(summer.session_open.hour, 13)


class WorkerTests(unittest.TestCase):
    @staticmethod
    def state(now: datetime, *, opened: bool, final: bool = False, session_day=None, **_):
        session_day = session_day or now.astimezone(EASTERN).date()
        return type("State", (), {
            "now": now, "session_date": session_day,
            "session_open": now - timedelta(hours=1),
            "session_close": now + timedelta(hours=1),
            "is_open": opened, "final_refresh_due": final,
        })()

    def test_open_refresh_cadence_uses_coordinator(self) -> None:
        now = [datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)]
        coordinator = Mock()
        worker = MarketRefreshWorker(
            coordinator=coordinator, refresh_interval_seconds=300,
            clock=lambda: now[0], state_loader=lambda value, **kw: self.state(value, opened=True),
        )
        worker.run_once()
        worker.run_once()
        self.assertEqual(coordinator.refresh.call_count, 1)
        coordinator.refresh.assert_called_with(force=True)
        now[0] += timedelta(seconds=301)
        worker.run_once()
        self.assertEqual(coordinator.refresh.call_count, 2)

    def test_closed_days_do_not_refresh(self) -> None:
        coordinator = Mock()
        now = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
        worker = MarketRefreshWorker(
            coordinator=coordinator, clock=lambda: now,
            state_loader=lambda value, **kw: self.state(value, opened=False, session_day=None),
        )
        # An actual non-session has no date; use a concrete state to avoid the helper default.
        worker.state_loader = lambda value, **kw: type("State", (), {
            "now": value, "session_date": None, "session_open": None, "session_close": None,
            "is_open": False, "final_refresh_due": False,
        })()
        self.assertIsNone(worker.run_once())
        coordinator.refresh.assert_not_called()

    def test_final_refresh_runs_once_and_resets_next_session(self) -> None:
        now = [datetime(2026, 8, 31, 20, 3, tzinfo=timezone.utc)]
        day = [datetime(2026, 8, 31, tzinfo=timezone.utc).date()]
        coordinator = Mock()
        clear = Mock()
        worker = MarketRefreshWorker(
            coordinator=coordinator, clock=lambda: now[0], price_cache_clearer=clear,
            state_loader=lambda value, **kw: self.state(value, opened=False, final=True, session_day=day[0]),
        )
        worker.run_once()
        worker.run_once()
        self.assertEqual(coordinator.refresh.call_count, 1)
        self.assertEqual(clear.call_count, 1)
        day[0] += timedelta(days=1)
        now[0] += timedelta(days=1)
        worker.run_once()
        self.assertEqual(coordinator.refresh.call_count, 2)

    def test_failure_is_caught_and_later_iteration_retries(self) -> None:
        now = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
        coordinator = Mock()
        coordinator.refresh.side_effect = [RuntimeError("temporary"), Mock()]
        worker = MarketRefreshWorker(
            coordinator=coordinator, clock=lambda: now,
            state_loader=lambda value, **kw: self.state(value, opened=True),
        )
        with self.assertLogs("portfolio_tracker", level="ERROR"):
            self.assertIsNone(worker.run_once())
        self.assertIsNotNone(worker.run_once())
        self.assertEqual(coordinator.refresh.call_count, 2)

    def test_worker_lifecycle_is_idempotent_and_client_independent(self) -> None:
        coordinator = Mock()
        worker = MarketRefreshWorker(
            coordinator=coordinator, check_interval_seconds=0.01,
            state_loader=lambda value, **kw: self.state(value, opened=False),
        )
        worker.start()
        thread = worker._thread
        worker.start()
        self.assertIs(worker._thread, thread)
        worker.stop()
        self.assertFalse(thread.is_alive())


class PersistenceAndCacheTests(unittest.TestCase):
    def test_same_day_snapshot_is_updated_not_duplicated(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            ensure_history_schema(path)
            metrics = pd.DataFrame([{"shares": 1.0, "current_price": 100.0, "market_value": 100.0}])
            persist_live_snapshot_if_ready(metrics, path)
            metrics.loc[0, ["current_price", "market_value"]] = [105.0, 105.0]
            persist_live_snapshot_if_ready(metrics, path)
            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute("SELECT portfolio_value FROM live_portfolio_snapshots").fetchall()
            self.assertEqual(rows, [(105.0,)])

    def test_price_cache_clear_preserves_enrichment(self) -> None:
        original = dict(yahoo._cache)
        try:
            yahoo._cache.clear()
            yahoo._cache[("ABC", "prices")] = (1.0, {"ABC": 1.0})
            yahoo._cache[("ABC", "info")] = (1.0, {"sector": "Technology"})
            yahoo._cache[("ABC", "investment_intelligence")] = (1.0, {"ticker": "ABC"})
            yahoo.clear_price_cache()
            self.assertNotIn(("ABC", "prices"), yahoo._cache)
            self.assertIn(("ABC", "info"), yahoo._cache)
            self.assertIn(("ABC", "investment_intelligence"), yahoo._cache)
        finally:
            yahoo._cache.clear()
            yahoo._cache.update(original)

    def test_server_lifecycle_registration_and_ui_timer_remain_separate(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        worker_source = (Path(__file__).parents[1] / "nicegui_app" / "market_refresh_worker.py").read_text(encoding="utf-8")
        self.assertIn("app.on_startup(start_market_refresh_worker)", source)
        self.assertIn("app.on_shutdown(stop_market_refresh_worker)", source)
        self.assertIn("ui.timer(DB_REFRESH_INTERVAL_SECONDS", source)
        self.assertNotIn("ui.timer", worker_source)
        self.assertNotIn("ui.context.client", worker_source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import atexit
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from nicegui_app.services.configuration import (
    MARKET_BACKGROUND_CHECK_INTERVAL_SECONDS,
    MARKET_BACKGROUND_REFRESH_INTERVAL_SECONDS,
    MARKET_CLOSE_GRACE_SECONDS,
)
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator, RefreshResult
from nicegui_app.services.yahoo import clear_price_cache, current_market_data


LOGGER = logging.getLogger("portfolio_tracker")
EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketSessionState:
    now: datetime
    session_date: date | None
    session_open: datetime | None
    session_close: datetime | None
    is_open: bool
    final_refresh_due: bool


def market_session_state(
    now: datetime,
    *,
    calendar=None,
    close_grace_seconds: float = MARKET_CLOSE_GRACE_SECONDS,
) -> MarketSessionState:
    """Return today's NYSE regular-session state using timezone-aware UTC times."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    calendar = calendar or xcals.get_calendar("XNYS")
    now_utc = now.astimezone(timezone.utc)
    local_date = now.astimezone(EASTERN).date()
    session = pd.Timestamp(local_date)
    if not calendar.is_session(session):
        return MarketSessionState(now_utc, None, None, None, False, False)
    session_open = calendar.session_open(session).to_pydatetime().astimezone(timezone.utc)
    session_close = calendar.session_close(session).to_pydatetime().astimezone(timezone.utc)
    final_due = now_utc >= session_close + timedelta(seconds=close_grace_seconds)
    return MarketSessionState(
        now_utc,
        local_date,
        session_open,
        session_close,
        session_open <= now_utc < session_close,
        final_due,
    )


class MarketRefreshWorker:
    """In-process, browser-independent Yahoo price and live-snapshot refresher."""

    def __init__(
        self,
        *,
        coordinator: ReadOnlyRefreshCoordinator | None = None,
        refresh_interval_seconds: float = MARKET_BACKGROUND_REFRESH_INTERVAL_SECONDS,
        check_interval_seconds: float = MARKET_BACKGROUND_CHECK_INTERVAL_SECONDS,
        close_grace_seconds: float = MARKET_CLOSE_GRACE_SECONDS,
        clock: Callable[[], datetime] | None = None,
        state_loader: Callable[..., MarketSessionState] = market_session_state,
        price_cache_clearer: Callable[[], None] = clear_price_cache,
    ) -> None:
        self.coordinator = coordinator or ReadOnlyRefreshCoordinator(market_loader=current_market_data)
        self.refresh_interval_seconds = refresh_interval_seconds
        self.check_interval_seconds = check_interval_seconds
        self.close_grace_seconds = close_grace_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state_loader = state_loader
        self.price_cache_clearer = price_cache_clearer
        self.last_refresh_at: datetime | None = None
        self.final_refresh_session: date | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._last_logged_session: date | None = None

    def run_once(self) -> RefreshResult | None:
        now = self.clock()
        state = self.state_loader(now, close_grace_seconds=self.close_grace_seconds)
        if state.session_date != self._last_logged_session and state.session_date is not None:
            LOGGER.info(
                "NYSE session detected: session=%s open=%s close=%s",
                state.session_date, state.session_open, state.session_close,
            )
            self._last_logged_session = state.session_date
        closing = bool(
            state.session_date is not None
            and state.final_refresh_due
            and self.final_refresh_session != state.session_date
        )
        regular_due = bool(
            state.is_open
            and (
                self.last_refresh_at is None
                or (state.now - self.last_refresh_at).total_seconds() >= self.refresh_interval_seconds
            )
        )
        if not regular_due and not closing:
            return None
        try:
            if closing:
                self.price_cache_clearer()
            result = self.coordinator.refresh(force=True)
        except Exception:
            LOGGER.exception("Background market refresh temporarily failed; a later check will retry.")
            return None
        if closing:
            self.final_refresh_session = state.session_date
            LOGGER.info("Final NYSE closing market refresh completed: session=%s", state.session_date)
        else:
            self.last_refresh_at = state.now
            LOGGER.info("Background market refresh completed: session=%s", state.session_date)
        return result

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("Background market worker check failed; a later check will retry.")
            self._stop_event.wait(self.check_interval_seconds)

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="market-refresh-worker", daemon=True)
            self._thread.start()
        LOGGER.info("Background market refresh worker started.")

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(self.check_interval_seconds + 1.0, 2.0))
        if thread is not None:
            LOGGER.info("Background market refresh worker stopped.")


_worker = MarketRefreshWorker()


def start_market_refresh_worker() -> None:
    _worker.start()


def stop_market_refresh_worker() -> None:
    _worker.stop()


atexit.register(stop_market_refresh_worker)

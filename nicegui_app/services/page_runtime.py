from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from nicegui import ui
from nicegui_app.process_lifecycle import LifecycleCoordinator, ShutdownInProgress
from nicegui_app.services.refresh import RefreshResult


logger = logging.getLogger(__name__)


class PageRefreshGuard:
    """Serialize refreshes for one page and reject work once shutdown starts."""

    def __init__(self, lifecycle: LifecycleCoordinator) -> None:
        self._lifecycle = lifecycle
        self._lock = asyncio.Lock()

    async def run(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        wait_if_busy: bool,
    ) -> RefreshResult | None:
        if self._lifecycle.is_shutting_down or (self._lock.locked() and not wait_if_busy):
            return None
        async with self._lock:
            if self._lifecycle.is_shutting_down:
                return None
            try:
                with self._lifecycle.critical_operation("page_refresh"):
                    result = await operation()
            except ShutdownInProgress:
                return None
            if not isinstance(result, RefreshResult):
                logger.warning("Portfolio refresh returned %s instead of RefreshResult", type(result).__name__)
                return None
            return result


def client_is_alive(client: Any, lifecycle: LifecycleCoordinator) -> bool:
    """Return whether callbacks may still mutate this page's UI."""
    if lifecycle.is_shutting_down:
        return False
    client_id = getattr(client, "id", None)
    instances = getattr(type(client), "instances", None)
    return client_id is not None and isinstance(instances, dict) and instances.get(client_id) is client


def notify_live_page(
    client: Any,
    lifecycle: LifecycleCoordinator,
    message: Any,
    *,
    notification_type: Literal["positive", "negative", "warning", "info", "ongoing"],
    timeout: int | None = None,
) -> None:
    """Notify only while the owning page client is alive and active in context."""
    if not client_is_alive(client, lifecycle):
        return
    with client:
        if timeout is None:
            ui.notify(message, type=notification_type)
        else:
            ui.notify(message, type=notification_type, timeout=timeout)

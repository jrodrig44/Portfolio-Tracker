from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from nicegui_app.process_lifecycle import LifecycleCoordinator
from nicegui_app.services.page_runtime import PageRefreshGuard, client_is_alive, notify_live_page
from nicegui_app.services.refresh import RefreshResult


class _Client:
    instances: dict[str, object] = {}

    def __init__(self, client_id: str = "page") -> None:
        self.id = client_id
        self.entered = False

    def __enter__(self) -> _Client:
        self.entered = True
        return self

    def __exit__(self, *_: object) -> None:
        self.entered = False


class PageRefreshGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_result_is_rejected_without_dereference(self) -> None:
        guard = PageRefreshGuard(LifecycleCoordinator())
        with self.assertLogs("nicegui_app.services.page_runtime", level="WARNING"):
            result = await guard.run(AsyncMock(return_value=None), wait_if_busy=True)
        self.assertIsNone(result)

    async def test_timer_tick_skips_while_manual_refresh_is_active(self) -> None:
        lifecycle = LifecycleCoordinator()
        guard = PageRefreshGuard(lifecycle)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_refresh() -> RefreshResult:
            entered.set()
            await release.wait()
            return RefreshResult(False, False)

        manual = asyncio.create_task(guard.run(slow_refresh, wait_if_busy=True))
        await entered.wait()
        timer_operation = AsyncMock(return_value=RefreshResult(False, False))
        self.assertIsNone(await guard.run(timer_operation, wait_if_busy=False))
        timer_operation.assert_not_awaited()
        release.set()
        self.assertIsInstance(await manual, RefreshResult)

    async def test_shutdown_skips_new_refresh_work(self) -> None:
        lifecycle = LifecycleCoordinator()
        lifecycle.begin_shutdown()
        operation = AsyncMock(return_value=RefreshResult(False, False))
        self.assertIsNone(await PageRefreshGuard(lifecycle).run(operation, wait_if_busy=True))
        operation.assert_not_awaited()


class ClientLivenessTests(unittest.TestCase):
    def tearDown(self) -> None:
        _Client.instances.clear()

    def test_deleted_client_is_not_alive(self) -> None:
        client = _Client()
        self.assertFalse(client_is_alive(client, LifecycleCoordinator()))

    def test_registered_client_is_alive_until_shutdown(self) -> None:
        client = _Client()
        _Client.instances[client.id] = client
        lifecycle = LifecycleCoordinator()
        self.assertTrue(client_is_alive(client, lifecycle))
        lifecycle.begin_shutdown()
        self.assertFalse(client_is_alive(client, lifecycle))

    def test_supplemental_research_notification_uses_live_client_context(self) -> None:
        client = _Client()
        _Client.instances[client.id] = client
        lifecycle = LifecycleCoordinator()

        with patch("nicegui_app.services.page_runtime.ui.notify") as notify:
            notify_live_page(
                client,
                lifecycle,
                "Supplemental research completed for HD.",
                notification_type="positive",
            )

        notify.assert_called_once_with("Supplemental research completed for HD.", type="positive")
        self.assertFalse(client.entered)

    def test_notification_after_page_deletion_is_skipped(self) -> None:
        client = _Client()

        with patch("nicegui_app.services.page_runtime.ui.notify") as notify:
            notify_live_page(
                client,
                LifecycleCoordinator(),
                "Supplemental research completed for HD.",
                notification_type="positive",
            )

        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from nicegui_app.services.ui_state import (
    NAVIGATION_AREAS,
    PageUIState,
    canonical_navigation,
    invalidate_section_views,
    refresh_view_plan,
)


class FakeTab:
    def __init__(self, name: str) -> None:
        self.props = {"name": name}

    def __str__(self) -> str:
        return "Tab object whose string is not a navigation name"


class RefreshHarness:
    def __init__(self, selected: object = "Holdings", history_range: str = "ALL") -> None:
        self.state = PageUIState(history_range=history_range)
        self.state.selected_navigation = canonical_navigation(selected)
        self.loaded = {name: True for name in NAVIGATION_AREAS}
        self.content = {name: f"populated:{name}" for name in NAVIGATION_AREAS}
        self.renders = {name: 0 for name in NAVIGATION_AREAS}
        self.metrics_version = 1

    async def render(self, name: str) -> None:
        self.renders[name] += 1
        self.loaded[name] = True
        self.content[name] = f"populated:{name}:metrics-{self.metrics_version}"

    def placeholder(self, name: str) -> None:
        self.content[name] = "Data changed; this section will refresh when opened."

    async def metrics_refresh(self) -> None:
        self.metrics_version += 1
        plan = refresh_view_plan(
            self.state.selected_navigation,
            metrics_changed=True,
            history_changed=False,
        )
        await invalidate_section_views(
            plan["stale"],
            self.state.selected_navigation,
            self.loaded,
            self.render,
            self.placeholder,
        )


class NiceGUIPostSaveRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_holdings_save_reloads_metrics_and_renders_once_without_placeholder(self) -> None:
        """Visible Holdings must rerender after Save without a tab switch."""
        page = RefreshHarness(FakeTab("Holdings"))

        await page.metrics_refresh()  # successful write followed by the forced refresh

        self.assertEqual(page.metrics_version, 2)
        self.assertEqual(page.state.selected_navigation, "Holdings")
        self.assertEqual(page.renders["Holdings"], 1)
        self.assertEqual(page.content["Holdings"], "populated:Holdings:metrics-2")
        self.assertNotIn("refresh when opened", page.content["Holdings"])
        self.assertFalse(page.loaded["Portfolio Analysis"])

    async def test_active_analysis_renders_while_holdings_remains_lazily_stale(self) -> None:
        """Only the visible derived section is eager during invalidation."""
        page = RefreshHarness("Portfolio Analysis")

        await page.metrics_refresh()

        self.assertEqual(page.renders["Portfolio Analysis"], 1)
        self.assertIn("populated:Portfolio Analysis", page.content["Portfolio Analysis"])
        self.assertEqual(page.renders["Holdings"], 0)
        self.assertFalse(page.loaded["Holdings"])
        self.assertIn("refresh when opened", page.content["Holdings"])

    async def test_history_refresh_preserves_selected_range(self) -> None:
        page = RefreshHarness("History", history_range="1Y")
        plan = refresh_view_plan("History", metrics_changed=False, history_changed=True)

        await invalidate_section_views(
            plan["stale"], "History", page.loaded, page.render, page.placeholder
        )

        self.assertEqual(page.state.history_range, "1Y")
        self.assertEqual(page.renders["History"], 1)
        self.assertNotIn("refresh when opened", page.content["History"])

    async def test_tab_value_change_keeps_navigation_canonical(self) -> None:
        page = RefreshHarness()
        for event_value in (FakeTab("Portfolio Health"), "Holdings"):
            page.state.selected_navigation = canonical_navigation(event_value)
            self.assertIn(page.state.selected_navigation, NAVIGATION_AREAS)

        self.assertEqual(page.state.selected_navigation, "Holdings")
        with self.assertRaises(ValueError):
            canonical_navigation(FakeTab("Not a section"))

        object_valued_plan = refresh_view_plan(
            FakeTab("Holdings"), metrics_changed=True, history_changed=False
        )
        self.assertEqual(object_valued_plan["active"], ("Holdings",))


if __name__ == "__main__":
    unittest.main()

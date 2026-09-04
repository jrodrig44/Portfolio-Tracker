from __future__ import annotations

import unittest
from pathlib import Path

from nicegui.elements.drawer import Drawer
from nicegui_app.services.formatting import HEADER_FOREGROUND, HEADER_ICON_CONTROL, HEADER_PRIMARY_TAB

from nicegui_app.services.ui_state import (
    NAVIGATION_AREAS,
    PRIMARY_NAVIGATION_AREAS,
    SECONDARY_NAVIGATION_AREAS,
    PageUIState,
    canonical_navigation,
    primary_navigation_value,
)


MAIN = (Path(__file__).resolve().parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")


class NavigationDrawerTests(unittest.TestCase):
    def test_header_navigation_has_explicit_high_contrast_foregrounds(self) -> None:
        header = MAIN.split("with ui.header()", 1)[1].split("with ui.column().classes", 1)[0]
        self.assertIn("text-white", HEADER_FOREGROUND)
        self.assertIn("text-white", HEADER_ICON_CONTROL)
        self.assertIn("hover:bg-white/10", HEADER_ICON_CONTROL)
        self.assertIn("focus-visible:ring", HEADER_ICON_CONTROL)
        self.assertIn("text-white", HEADER_PRIMARY_TAB)
        self.assertIn("focus-visible:ring", HEADER_PRIMARY_TAB)
        self.assertIn("HEADER_FOREGROUND", header)
        self.assertIn("HEADER_PRIMARY_TAB", header)
        self.assertEqual(header.count("HEADER_ICON_CONTROL"), 2)
        self.assertGreaterEqual(header.count("color=white"), 2)
        self.assertIn("active-color=white", header)
        self.assertIn("indicator-color=accent", header)

    def test_navigation_groups_cover_every_area_once_and_exclude_system_from_tabs_and_drawer(self) -> None:
        self.assertEqual(PRIMARY_NAVIGATION_AREAS, (
            "Holdings", "Portfolio Analysis", "Portfolio Insights", "Portfolio Health",
        ))
        self.assertEqual(SECONDARY_NAVIGATION_AREAS, tuple(
            name for name in NAVIGATION_AREAS if name not in (*PRIMARY_NAVIGATION_AREAS, "System")
        ))
        self.assertNotIn("System", PRIMARY_NAVIGATION_AREAS)
        self.assertNotIn("System", SECONDARY_NAVIGATION_AREAS)
        self.assertEqual(set(PRIMARY_NAVIGATION_AREAS) | set(SECONDARY_NAVIGATION_AREAS) | {"System"}, set(NAVIGATION_AREAS))

    def test_secondary_sections_do_not_select_a_primary_tab(self) -> None:
        for name in PRIMARY_NAVIGATION_AREAS:
            self.assertEqual(primary_navigation_value(name), name)
        for name in (*SECONDARY_NAVIGATION_AREAS, "System"):
            self.assertIsNone(primary_navigation_value(name))

    def test_all_entry_points_use_one_canonical_selection_path(self) -> None:
        selection = MAIN.split("async def select_navigation", 1)[1].split("async def refresh_data", 1)[0]
        self.assertIn("name = canonical_navigation(value)", selection)
        self.assertIn("ui_state.selected_navigation = name", selection)
        self.assertIn("section_panels.value = name", selection)
        self.assertEqual(selection.count("await refresh_section(name)"), 1)
        self.assertIn("await select_navigation(event.value)", selection)
        self.assertIn("select_navigation(name)", selection)
        self.assertIn('select_navigation("System")', selection)

    def test_drawer_toggle_has_no_render_refresh_or_external_work(self) -> None:
        construction = MAIN.split("with ui.left_drawer", 1)[1].split("with ui.column().classes", 1)[0]
        self.assertIn("navigation_drawer.toggle", construction)
        for forbidden in ("refresh_section", "refresh_data", "current_market_data", "research_service", "run_ai_analysis", "yahoo"):
            self.assertNotIn(forbidden, construction)

    def test_drawer_selection_closes_and_deep_sections_remain_reachable(self) -> None:
        selection = MAIN.split("async def select_navigation", 1)[1].split("async def navigate_primary", 1)[0]
        self.assertIn("navigation_drawer.set_value(False)", selection)
        self.assertTrue(hasattr(Drawer, "set_value"))
        self.assertNotIn("navigation_drawer.close", MAIN)
        for name in ("History", "HD / ESPP History", "Supplemental Research", "AI Analysis", "Statement History"):
            self.assertIn(name, SECONDARY_NAVIGATION_AREAS)
            self.assertEqual(canonical_navigation(name), name)

    def test_primary_secondary_primary_preserves_page_state_and_history_range(self) -> None:
        state = PageUIState(selected_navigation="Portfolio Analysis", history_range="1Y")
        for destination in ("History", "Supplemental Research", "Portfolio Health"):
            state.selected_navigation = canonical_navigation(destination)
        self.assertEqual(state.selected_navigation, "Portfolio Health")
        self.assertEqual(state.history_range, "1Y")

    def test_system_gear_is_accessible_and_uses_central_navigation(self) -> None:
        header = MAIN.split("with ui.header()", 1)[1].split("with ui.column().classes", 1)[0]
        self.assertIn('icon="settings"', header)
        self.assertIn("System status and settings", header)
        self.assertNotIn('ui.tab(name="System"', header)
        self.assertIn('system_button.on_click(lambda: select_navigation("System"))', MAIN)


if __name__ == "__main__":
    unittest.main()

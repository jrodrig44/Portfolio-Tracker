from __future__ import annotations

from pathlib import Path
import unittest

from nicegui_app.main import NAVIGATION_AREAS
from nicegui_app.services.formatting import (
    CONFIRM_DIALOG_CARD,
    DIALOG_CARD,
    EMPTY_STATE,
    RICH_OUTPUT_CSS,
    RESPONSIVE_ACTIONS,
    SAFE_RICH_OUTPUT,
    SCROLL_TABLE,
    SNAPSHOT_RICH_OUTPUT,
    SNAPSHOT_VIEWER_BODY,
    SNAPSHOT_VIEWER_CARD,
)
from nicegui_app.services.ui_state import PageUIState, refresh_view_plan


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")


class NiceGUIPhase2HPolishTests(unittest.TestCase):
    def test_single_destination_mapping_drives_desktop_and_mobile_navigation(self) -> None:
        self.assertEqual(len(NAVIGATION_AREAS), 9)
        self.assertEqual(len(set(NAVIGATION_AREAS)), 9)
        self.assertIn('tabs = ui.tabs().classes("w-max min-w-full")', MAIN)
        self.assertIn('tab_objects = {name: ui.tab(name) for name in NAVIGATION_AREAS}', MAIN)
        self.assertIn("mobile-arrows outside-arrows", MAIN)
        self.assertIn('ui.element("nav").classes("w-full max-w-full overflow-x-auto")', MAIN)
        self.assertNotIn("hidden lg:block", MAIN)
        self.assertNotIn("mobile_navigation", MAIN)
        self.assertIn("tabs.on_value_change(navigate)", MAIN)
        self.assertIn("ui_state.selected_navigation = name", MAIN)

    def test_hd_grid_rules_are_clearly_disclosed_and_backend_enforced(self) -> None:
        holdings = MAIN.split("def render_holdings", 1)[1].split("async def render_statement_history", 1)[0]
        self.assertIn("HD rows are normalized on Save", holdings)
        self.assertIn("regular/special contributions are zero", holdings)
        self.assertIn("15% discount", holdings)
        self.assertIn("six-month cadence", holdings)
        self.assertIn("non-positive ESPP contribution uses $100", holdings)

    def test_dialogs_and_actions_are_viewport_and_touch_safe(self) -> None:
        self.assertIn("96vw", DIALOG_CARD)
        self.assertIn("90dvh", DIALOG_CARD)
        self.assertIn("overflow-y-auto", DIALOG_CARD)
        self.assertIn("96vw", SNAPSHOT_VIEWER_CARD)
        self.assertIn("72rem", SNAPSHOT_VIEWER_CARD)
        self.assertIn("90dvh", SNAPSHOT_VIEWER_CARD)
        self.assertIn("h-[90dvh]", SNAPSHOT_VIEWER_CARD)
        self.assertIn("max-w-[72rem]", SNAPSHOT_VIEWER_CARD)
        self.assertIn("overflow-hidden", SNAPSHOT_VIEWER_CARD)
        self.assertIn("flex flex-col", SNAPSHOT_VIEWER_CARD)
        self.assertNotIn("overflow-y-auto", SNAPSHOT_VIEWER_CARD)
        self.assertIn("dark:bg-gray-900", SNAPSHOT_VIEWER_CARD)
        self.assertIn("flex-1", SNAPSHOT_VIEWER_BODY)
        self.assertIn("min-h-0", SNAPSHOT_VIEWER_BODY)
        self.assertIn("overflow-y-auto", SNAPSHOT_VIEWER_BODY)
        self.assertIn("overflow-x-hidden", SNAPSHOT_VIEWER_BODY)
        self.assertIn("90dvh", CONFIRM_DIALOG_CARD)
        self.assertGreaterEqual(MAIN.count("classes(DIALOG_CARD)"), 1)
        self.assertGreaterEqual(MAIN.count("classes(CONFIRM_DIALOG_CARD)"), 1)
        self.assertIn("flex-col", RESPONSIVE_ACTIONS)
        self.assertIn("sm:flex-row", RESPONSIVE_ACTIONS)
        self.assertGreaterEqual(MAIN.count("min-h-11"), 20)

    def test_tables_markdown_paths_and_charts_are_overflow_safe(self) -> None:
        self.assertIn("overflow-x-auto", SCROLL_TABLE)
        self.assertIn("min-w-0", SAFE_RICH_OUTPUT)
        self.assertIn("snapshot-rich-output", SNAPSHOT_RICH_OUTPUT)
        for selector in (".snapshot-rich-output table", ".snapshot-rich-output pre", ".snapshot-rich-output code", ".snapshot-rich-output img", ".snapshot-rich-output a"):
            self.assertIn(selector, RICH_OUTPUT_CSS)
        self.assertIn("overflow-x: auto", RICH_OUTPUT_CSS)
        self.assertIn("overflow-wrap: anywhere", RICH_OUTPUT_CSS)
        self.assertIn("max-width: 100%", RICH_OUTPUT_CSS)
        self.assertGreaterEqual(MAIN.count("classes(SCROLL_TABLE)"), 2)
        self.assertGreaterEqual(MAIN.count("classes(SAFE_RICH_OUTPUT)"), 2)
        self.assertIn('classes("w-full min-w-0 h-72 sm:h-96")', MAIN)
        self.assertIn('classes("w-full min-w-0 h-64 sm:h-80")', MAIN)

    def test_user_friendly_empty_states_cover_major_on_demand_content(self) -> None:
        self.assertIn("border-dashed", EMPTY_STATE)
        for message in (
            "Your portfolio has no holdings",
            "No statement scan has been run",
            "No portfolio history is available yet",
            "No analysis has been run yet",
            "Add a holding before requesting supplemental research",
        ):
            self.assertIn(message, MAIN)

    def test_holdings_and_contributions_use_one_spreadsheet_workflow(self) -> None:
        holdings = MAIN.split("def render_holdings", 1)[1].split("async def render_statement_history", 1)[0]
        self.assertIn("ui.aggrid", holdings)
        self.assertIn('ui.button("Add Row"', holdings)
        self.assertIn('ui.button("Remove Selected Rows"', holdings)
        self.assertIn('ui.button("Save Holdings"', holdings)
        self.assertIn("HOLDING_EDITABLE_FIELDS", holdings)
        self.assertIn("regular_biweekly_contribution", MAIN)
        self.assertIn("espp_contribution_biweekly", MAIN)
        self.assertNotIn("Edit Contributions", holdings)
        self.assertNotIn("Edit or delete a holding", holdings)
        self.assertNotIn("open_holding_dialog", holdings)

    def test_navigation_and_history_state_survive_rerender(self) -> None:
        state = PageUIState(history_range="ALL")
        collapsed_rerender = PageUIState(
            selected_navigation=state.selected_navigation,
            history_range=state.history_range,
        )
        collapsed_rerender.selected_navigation = "Portfolio Health"
        collapsed_rerender.history_range = "1Y"
        self.assertEqual(collapsed_rerender.selected_navigation, "Portfolio Health")
        self.assertEqual(collapsed_rerender.history_range, "1Y")
        self.assertNotIn("dialog", PageUIState.__dataclass_fields__)
        self.assertNotIn("busy", PageUIState.__dataclass_fields__)
        self.assertIn("ui_state.selected_navigation = name", MAIN)
        self.assertIn("ui_state.history_range = range_name", MAIN)
        self.assertIn('tabs.bind_value(ui_state, "selected_navigation")', MAIN)

    def test_refresh_plan_preserves_active_view_and_lazily_invalidates_inactive_views(self) -> None:
        analysis = refresh_view_plan("Portfolio Analysis", metrics_changed=True, history_changed=False)
        self.assertEqual(analysis["active"], ("Portfolio Analysis",))
        self.assertIn("Holdings", analysis["inactive"])
        self.assertNotIn("Portfolio Analysis", analysis["inactive"])

        history = refresh_view_plan("History", metrics_changed=False, history_changed=True)
        self.assertEqual(history["active"], ("History",))
        self.assertEqual(history["inactive"], ())

        forced = refresh_view_plan("AI Analysis", metrics_changed=True, history_changed=True)
        self.assertEqual(forced["active"], ())
        self.assertIn("Holdings", forced["inactive"])
        self.assertIn("History", forced["inactive"])
        self.assertEqual("AI Analysis", PageUIState(selected_navigation="AI Analysis").selected_navigation)

    def test_invalidation_rerenders_visible_section_and_keeps_hidden_sections_lazy(self) -> None:
        self.assertIn("async def invalidate_sections(names: Any, message: str)", MAIN)
        self.assertIn("if name == ui_state.selected_navigation:", MAIN)
        self.assertIn("await refresh_section(name)", MAIN)
        self.assertIn("areas[name].clear()", MAIN)
        self.assertIn("await invalidate_sections(\n            plan[\"stale\"]", MAIN)
        self.assertNotIn('if result.history_changed and ui_state.selected_navigation == "History"', MAIN)

    def test_write_refreshes_use_central_invalidation_without_duplicate_metric_renders(self) -> None:
        self.assertIn('(\n            ("AI Analysis", "Supplemental Research"),', MAIN)
        self.assertIn('(\n            ("History", "AI Analysis"),', MAIN)
        self.assertNotIn('for name in ("Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "Supplemental Research")', MAIN)
        self.assertEqual(MAIN.count('"Data changed; this section will refresh when opened."'), 1)

    def test_phase_boundaries_and_canonical_runtime_remain_intact(self) -> None:
        self.assertIn('PORT = int(os.environ.get("PORTFOLIO_TRACKER_PORT", "8501"))', MAIN)
        self.assertEqual(MAIN.count('ui.button("Refresh Data"'), 1)
        self.assertIn("ui.timer(DB_REFRESH_INTERVAL_SECONDS", MAIN)
        self.assertNotIn("Use Export JSON Data", MAIN)
        self.assertNotIn("Strict Export Accuracy Mode", MAIN)


if __name__ == "__main__":
    unittest.main()

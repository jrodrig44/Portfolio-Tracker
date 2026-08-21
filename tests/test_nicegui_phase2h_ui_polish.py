from __future__ import annotations

from pathlib import Path
import unittest

from nicegui_app.main import NAVIGATION_AREAS
from nicegui_app.services.formatting import (
    CONFIRM_DIALOG_CARD,
    DIALOG_CARD,
    EMPTY_STATE,
    RESPONSIVE_ACTIONS,
    SAFE_RICH_OUTPUT,
    SCROLL_TABLE,
)
from nicegui_app.services.ui_state import PageUIState


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

    def test_hd_forced_controls_are_visible_disabled_and_transition_aware(self) -> None:
        form = MAIN.split("def holding_form", 1)[1].split("def open_holding_dialog", 1)[0]
        self.assertIn('ticker.on_value_change', form)
        self.assertIn('== "HD"', form)
        self.assertIn("regular.set_value(0)", form)
        self.assertIn("special.set_value(0)", form)
        self.assertIn("discount.set_value(0.15)", form)
        self.assertIn("frequency.set_value(6)", form)
        self.assertIn("is_espp.set_value(True)", form)
        self.assertIn("control.disable()", form)
        self.assertIn("control.enable()", form)
        contribution = MAIN.split("def open_contribution_dialog", 1)[1].split("busy =", 1)[0]
        self.assertIn("control.disable()", contribution)

    def test_dialogs_and_actions_are_viewport_and_touch_safe(self) -> None:
        self.assertIn("96vw", DIALOG_CARD)
        self.assertIn("90dvh", DIALOG_CARD)
        self.assertIn("overflow-y-auto", DIALOG_CARD)
        self.assertIn("90dvh", CONFIRM_DIALOG_CARD)
        self.assertGreaterEqual(MAIN.count("classes(DIALOG_CARD)"), 3)
        self.assertGreaterEqual(MAIN.count("classes(CONFIRM_DIALOG_CARD)"), 2)
        self.assertIn("flex-col", RESPONSIVE_ACTIONS)
        self.assertIn("sm:flex-row", RESPONSIVE_ACTIONS)
        self.assertGreaterEqual(MAIN.count("min-h-11"), 20)

    def test_tables_markdown_paths_and_charts_are_overflow_safe(self) -> None:
        self.assertIn("overflow-x-auto", SCROLL_TABLE)
        self.assertIn("min-w-0", SAFE_RICH_OUTPUT)
        self.assertIn("safe-rich-output pre", MAIN)
        self.assertIn("overflow-wrap: anywhere", MAIN)
        self.assertGreaterEqual(MAIN.count("classes(SCROLL_TABLE)"), 2)
        self.assertGreaterEqual(MAIN.count("classes(SAFE_RICH_OUTPUT)"), 2)
        self.assertIn('classes("w-full min-w-0 h-72 sm:h-96")', MAIN)
        self.assertIn('classes("w-full min-w-0 h-64 sm:h-80")', MAIN)

    def test_user_friendly_empty_states_cover_major_on_demand_content(self) -> None:
        self.assertIn("border-dashed", EMPTY_STATE)
        for message in (
            "Your portfolio has no holdings yet",
            "Add a holding to configure recurring or special contributions",
            "No statement scan has been run",
            "No portfolio history is available yet",
            "No analysis has been run yet",
            "Add a holding before requesting supplemental research",
        ):
            self.assertIn(message, MAIN)

    def test_holdings_and_contributions_use_compact_rows_not_per_holding_cards(self) -> None:
        contributions = MAIN.split('with ui.column().classes("w-full gap-0 divide-y divide-gray-200")', 1)[1].split("fields =", 1)[0]
        management = MAIN.split('management_expansion = ui.expansion', 1)[1].split('ui.label("Read-only exports")', 1)[0]
        self.assertIn("for stored in holdings", contributions)
        self.assertIn('with ui.row().classes("w-full min-w-0 flex-wrap items-center gap-2 py-2")', contributions)
        self.assertNotIn("ui.card()", contributions)
        self.assertIn('ui.button("Edit Contributions"', contributions)
        self.assertIn("for stored in holdings", management)
        self.assertNotIn("ui.card()", management.split("def confirm_delete", 1)[0])
        self.assertIn('int(row["_holding_id"])', management)
        self.assertIn("expected_ticker", management)

    def test_holdings_expansion_navigation_and_history_state_survive_rerender(self) -> None:
        state = PageUIState(history_range="ALL")
        self.assertTrue(state.expansion_value("contributions"))
        self.assertFalse(state.expansion_value("management"))
        state.set_expansion("contributions", False)
        collapsed_rerender = PageUIState(
            selected_navigation=state.selected_navigation,
            history_range=state.history_range,
            holdings_contributions_expanded=state.holdings_contributions_expanded,
            holdings_management_expanded=state.holdings_management_expanded,
        )
        self.assertFalse(collapsed_rerender.expansion_value("contributions"))
        collapsed_rerender.set_expansion("contributions", True)
        collapsed_rerender.selected_navigation = "Portfolio Health"
        collapsed_rerender.history_range = "1Y"
        self.assertTrue(collapsed_rerender.expansion_value("contributions"))
        self.assertEqual(collapsed_rerender.selected_navigation, "Portfolio Health")
        self.assertEqual(collapsed_rerender.history_range, "1Y")
        self.assertNotIn("dialog", PageUIState.__dataclass_fields__)
        self.assertNotIn("busy", PageUIState.__dataclass_fields__)
        self.assertIn('value=ui_state.expansion_value("contributions")', MAIN)
        self.assertIn('ui_state.set_expansion("contributions", event.value)', MAIN)
        self.assertIn('value=ui_state.expansion_value("management")', MAIN)
        self.assertIn('ui_state.set_expansion("management", event.value)', MAIN)
        self.assertIn("ui_state.selected_navigation = name", MAIN)
        self.assertIn("ui_state.history_range = range_name", MAIN)

    def test_phase_boundaries_and_canonical_runtime_remain_intact(self) -> None:
        self.assertIn("PORT = 8502", MAIN)
        self.assertEqual(MAIN.count('ui.button("Refresh Data"'), 1)
        self.assertIn("ui.timer(DB_REFRESH_INTERVAL_SECONDS", MAIN)
        self.assertNotIn("Use Export JSON Data", MAIN)
        self.assertNotIn("Strict Export Accuracy Mode", MAIN)


if __name__ == "__main__":
    unittest.main()

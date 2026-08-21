from __future__ import annotations

import ast
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


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")


class NiceGUIPhase2HPolishTests(unittest.TestCase):
    def test_single_destination_mapping_drives_desktop_and_mobile_navigation(self) -> None:
        self.assertEqual(len(NAVIGATION_AREAS), 9)
        self.assertEqual(len(set(NAVIGATION_AREAS)), 9)
        self.assertIn('ui.select(list(NAVIGATION_AREAS), value="Holdings", label="Current section")', MAIN)
        self.assertIn('classes("w-full lg:hidden")', MAIN)
        self.assertIn('classes("hidden lg:block w-full max-w-full overflow-x-auto")', MAIN)
        self.assertNotIn('classes("w-full md:hidden")', MAIN)
        self.assertNotIn('classes("hidden md:block w-full max-w-full overflow-x-auto")', MAIN)
        self.assertIn("mobile_navigation.on_value_change", MAIN)
        self.assertIn("mobile_navigation.set_value(name)", MAIN)
        self.assertNotIn("mobile-arrows", MAIN)

    def test_mobile_select_converts_immutable_navigation_tuple_to_list_at_api_boundary(self) -> None:
        tree = ast.parse(MAIN)
        navigation_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ui"
            and node.func.attr == "select"
            and any(keyword.arg == "label" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "Current section" for keyword in node.keywords)
        ]
        self.assertEqual(len(navigation_calls), 1)
        options = navigation_calls[0].args[0]
        self.assertIsInstance(options, ast.Call)
        self.assertIsInstance(options.func, ast.Name)
        self.assertEqual(options.func.id, "list")
        self.assertEqual(len(options.args), 1)
        self.assertIsInstance(options.args[0], ast.Name)
        self.assertEqual(options.args[0].id, "NAVIGATION_AREAS")

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

    def test_phase_boundaries_and_canonical_runtime_remain_intact(self) -> None:
        self.assertIn("PORT = 8502", MAIN)
        self.assertEqual(MAIN.count('ui.button("Refresh Data"'), 1)
        self.assertIn("ui.timer(DB_REFRESH_INTERVAL_SECONDS", MAIN)
        self.assertNotIn("Use Export JSON Data", MAIN)
        self.assertNotIn("Strict Export Accuracy Mode", MAIN)


if __name__ == "__main__":
    unittest.main()

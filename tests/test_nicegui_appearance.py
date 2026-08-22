from __future__ import annotations

from pathlib import Path
import unittest

from nicegui_app.services.appearance import (
    APPEARANCE_OPTIONS,
    APPEARANCE_STORAGE_KEY,
    restore_appearance,
)


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")


class NiceGUIAppearanceTests(unittest.TestCase):
    def test_mode_values_match_nicegui_native_dark_mode(self) -> None:
        self.assertEqual(APPEARANCE_OPTIONS[None], "System")
        self.assertEqual(APPEARANCE_OPTIONS[False], "Light")
        self.assertEqual(APPEARANCE_OPTIONS[True], "Dark")

    def test_first_visit_defaults_to_system(self) -> None:
        storage = {}
        self.assertIsNone(restore_appearance(storage))
        self.assertIn(APPEARANCE_STORAGE_KEY, storage)
        self.assertIsNone(storage[APPEARANCE_STORAGE_KEY])

    def test_saved_preferences_are_restored(self) -> None:
        for saved in (False, True, None):
            with self.subTest(saved=saved):
                storage = {APPEARANCE_STORAGE_KEY: saved}
                self.assertIs(restore_appearance(storage), saved)

    def test_invalid_saved_value_falls_back_to_system(self) -> None:
        storage = {APPEARANCE_STORAGE_KEY: "dark"}
        self.assertIsNone(restore_appearance(storage))
        self.assertIsNone(storage[APPEARANCE_STORAGE_KEY])

    def test_native_dark_mode_and_system_control_share_user_storage(self) -> None:
        self.assertIn("from nicegui import app, run, ui", MAIN)
        self.assertIn("app.storage.user", MAIN)
        self.assertIn("ui.dark_mode(value=appearance_mode).bind_value", MAIN)
        self.assertIn("ui.toggle(APPEARANCE_OPTIONS", MAIN)
        self.assertGreaterEqual(MAIN.count("APPEARANCE_STORAGE_KEY"), 3)
        self.assertIn("System follows your device's appearance setting.", MAIN)
        self.assertIn("storage_secret=STORAGE_SECRET", MAIN)

    def test_system_status_functionality_remains_present(self) -> None:
        self.assertIn("operational_status(", MAIN)
        self.assertIn('ui.label("Runtime")', MAIN)
        self.assertIn('ui.label("Portfolio database")', MAIN)
        self.assertIn('ui.label("Statement storage")', MAIN)
        self.assertIn('ui.label("Cache behavior")', MAIN)


if __name__ == "__main__":
    unittest.main()

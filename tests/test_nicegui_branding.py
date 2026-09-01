import struct
import unittest
from pathlib import Path

from nicegui_app.main import APPLE_TOUCH_ICON_ROUTES, app


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "nicegui_app" / "static"
MAIN_SOURCE = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path.name} is not a valid PNG")
    return struct.unpack(">II", data[16:24])


class NiceGuiBrandingTests(unittest.TestCase):
    def test_favicon_configuration_points_to_valid_project_assets(self) -> None:
        favicon = STATIC_DIR / "favicon.ico"
        self.assertTrue(favicon.is_file())
        self.assertEqual(favicon.read_bytes()[:4], b"\x00\x00\x01\x00")
        self.assertEqual(png_dimensions(STATIC_DIR / "favicon-32x32.png"), (32, 32))
        self.assertIn("favicon=FAVICON_PATH", MAIN_SOURCE)

    def test_apple_touch_icon_is_conventional_size(self) -> None:
        self.assertEqual(png_dimensions(STATIC_DIR / "apple-touch-icon.png"), (180, 180))

    def test_required_icon_routes_and_static_mount_are_registered(self) -> None:
        self.assertIn('app.add_static_files("/branding", STATIC_DIR)', MAIN_SOURCE)
        self.assertIn('url_path="/favicon.ico"', MAIN_SOURCE)
        expected_apple_routes = (
            "/apple-touch-icon.png",
            "/apple-touch-icon-precomposed.png",
            "/apple-touch-icon-120x120.png",
            "/apple-touch-icon-120x120-precomposed.png",
            "/apple-touch-icon-152x152.png",
            "/apple-touch-icon-152x152-precomposed.png",
            "/apple-touch-icon-167x167.png",
            "/apple-touch-icon-167x167-precomposed.png",
            "/apple-touch-icon-180x180.png",
            "/apple-touch-icon-180x180-precomposed.png",
        )
        for route in expected_apple_routes:
            self.assertIn(f'"{route}"', MAIN_SOURCE)
        self.assertIn("for icon_route in APPLE_TOUCH_ICON_ROUTES:", MAIN_SOURCE)
        self.assertIn(
            "app.add_static_file(local_file=APPLE_TOUCH_ICON_PATH, url_path=icon_route)",
            MAIN_SOURCE,
        )
        self.assertEqual(
            MAIN_SOURCE.count('APPLE_TOUCH_ICON_PATH = STATIC_DIR / "apple-touch-icon.png"'),
            1,
        )
        registered_routes = {route.path for route in app.routes}
        self.assertTrue(set(APPLE_TOUCH_ICON_ROUTES).issubset(registered_routes))
        self.assertIn("/favicon.ico", registered_routes)

    def test_head_metadata_and_clean_production_title(self) -> None:
        self.assertIn('rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"', MAIN_SOURCE)
        self.assertIn('title="Portfolio Tracker"', MAIN_SOURCE)
        self.assertNotIn('title="Portfolio Tracker · NiceGUI"', MAIN_SOURCE)


if __name__ == "__main__":
    unittest.main()

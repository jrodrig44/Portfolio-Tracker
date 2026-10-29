import json
import mimetypes
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
        self.assertEqual(png_dimensions(STATIC_DIR / "favicon-16x16.png"), (16, 16))
        self.assertEqual(png_dimensions(STATIC_DIR / "favicon-32x32.png"), (32, 32))
        self.assertIn("favicon=FAVICON_PATH", MAIN_SOURCE)

    def test_pwa_icons_have_required_dimensions(self) -> None:
        expected = {
            "android-chrome-192x192.png": (192, 192),
            "android-chrome-512x512.png": (512, 512),
            "maskable-icon-192x192.png": (192, 192),
            "maskable-icon-512x512.png": (512, 512),
        }
        for filename, dimensions in expected.items():
            self.assertEqual(png_dimensions(STATIC_DIR / filename), dimensions)

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
        health_route = next(route for route in app.routes if route.path == "/healthz")
        self.assertEqual(health_route.endpoint(), {"status": "ok"})

    def test_head_metadata_and_clean_production_title(self) -> None:
        self.assertIn('rel="icon" href="/favicon.ico" sizes="any"', MAIN_SOURCE)
        self.assertIn('sizes="16x16" href="/branding/favicon-16x16.png"', MAIN_SOURCE)
        self.assertIn('sizes="32x32" href="/branding/favicon-32x32.png"', MAIN_SOURCE)
        self.assertIn('rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"', MAIN_SOURCE)
        self.assertIn('rel="manifest" href="/branding/site.webmanifest"', MAIN_SOURCE)
        self.assertIn('name="theme-color" content="#071f4d"', MAIN_SOURCE)
        self.assertIn('title="Portfolio Tracker"', MAIN_SOURCE)
        self.assertNotIn('title="Portfolio Tracker · NiceGUI"', MAIN_SOURCE)

    def test_web_manifest_is_valid_and_uses_relative_portfolio_tracker_assets(self) -> None:
        manifest_path = STATIC_DIR / "site.webmanifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "Portfolio Tracker")
        self.assertEqual(manifest["short_name"], "Portfolio")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        icons = {(icon["sizes"], icon["purpose"]): icon["src"] for icon in manifest["icons"]}
        self.assertEqual(icons[("192x192", "any")], "/branding/android-chrome-192x192.png")
        self.assertEqual(icons[("512x512", "any")], "/branding/android-chrome-512x512.png")
        self.assertEqual(icons[("192x192", "maskable")], "/branding/maskable-icon-192x192.png")
        self.assertEqual(icons[("512x512", "maskable")], "/branding/maskable-icon-512x512.png")
        for icon in manifest["icons"]:
            self.assertTrue(icon["src"].startswith("/branding/"))
            self.assertNotIn("://", icon["src"])
            self.assertTrue((STATIC_DIR / Path(icon["src"]).name).is_file())
        self.assertEqual(mimetypes.guess_type(manifest_path.name)[0], "application/manifest+json")
        branding_configuration = MAIN_SOURCE + manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("192.168.1.20", branding_configuration)
        self.assertNotIn("localhost", branding_configuration)


if __name__ == "__main__":
    unittest.main()

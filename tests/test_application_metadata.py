from pathlib import Path
import unittest

from application_metadata import APPLICATION_NAME, APPLICATION_VERSION


ROOT = Path(__file__).resolve().parents[1]


class ApplicationMetadataTests(unittest.TestCase):
    def test_product_identity_and_release_version_are_centralized(self) -> None:
        self.assertEqual(APPLICATION_NAME, "Portfolio Tracker")
        self.assertEqual(APPLICATION_VERSION, "2.1.1")

    def test_both_interfaces_and_startup_logs_use_shared_metadata(self) -> None:
        nicegui_source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        streamlit_source = (ROOT / "app.py").read_text(encoding="utf-8")
        worker_source = (ROOT / "nicegui_app" / "market_refresh_worker.py").read_text(encoding="utf-8")
        self.assertIn("from application_metadata import APPLICATION_NAME, APPLICATION_VERSION", nicegui_source)
        self.assertIn("from application_metadata import APPLICATION_NAME, APPLICATION_VERSION", streamlit_source)
        self.assertIn("from application_metadata import APPLICATION_NAME, APPLICATION_VERSION", worker_source)
        self.assertIn("application_version=APPLICATION_VERSION", streamlit_source)
        self.assertIn("st.caption(f\"{APPLICATION_NAME} v{APPLICATION_VERSION}\")", streamlit_source)
        self.assertIn("APPLICATION_VERSION,", worker_source)


if __name__ == "__main__":
    unittest.main()

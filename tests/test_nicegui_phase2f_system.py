from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nicegui_app.services.configuration import (
    DB_REFRESH_INTERVAL_SECONDS,
    DIVIDEND_TIMING_CACHE_TTL_SECONDS,
    FUND_SECTOR_WEIGHTS_CACHE_TTL_SECONDS,
    HISTORICAL_CONTEXT_CACHE_TTL_SECONDS,
    INVESTMENT_INTELLIGENCE_CACHE_TTL_SECONDS,
    PRICE_CACHE_TTL_SECONDS,
    YAHOO_METADATA_CACHE_TTL_SECONDS,
)
from portfolio_operations import CACHE_INTERVALS_SECONDS, format_interval, operational_status


ROOT = Path(__file__).resolve().parents[1]


class NiceGUIPhase2FSystemTests(unittest.TestCase):
    def test_status_reads_paths_and_pdf_count_without_scanning_or_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portfolio_db = root / "portfolio.db"
            history_db = root / "history.sqlite"
            statements = root / "Statements"
            portfolio_db.write_bytes(b"portfolio unchanged")
            history_db.write_bytes(b"history unchanged")
            statements.mkdir()
            (statements / "one.pdf").write_bytes(b"not parsed")
            (statements / "TWO.PDF").write_bytes(b"not parsed either")
            before = (portfolio_db.read_bytes(), history_db.read_bytes())
            with patch("stash_history.run_statement_scan") as scan, patch("yfinance.Ticker") as provider:
                payload = operational_status(
                    portfolio_db, statements, history_db,
                    application_port=8502,
                    database_refresh_interval_seconds=DB_REFRESH_INTERVAL_SECONDS,
                )
            self.assertEqual(payload["statement_storage"]["folder"]["pdf_count"], 2)
            self.assertTrue(payload["portfolio_database"]["available"])
            self.assertTrue(payload["statement_storage"]["history_database"]["available"])
            self.assertEqual(before, (portfolio_db.read_bytes(), history_db.read_bytes()))
            scan.assert_not_called()
            provider.assert_not_called()

    def test_missing_statement_storage_is_represented_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_folder = root / "Statements"
            missing_history = root / "history.sqlite"
            payload = operational_status(
                root / "portfolio.db", missing_folder, missing_history,
                application_port=8502,
                database_refresh_interval_seconds=20,
            )
            self.assertEqual(payload["statement_storage"]["folder"]["status"], "Not available")
            self.assertEqual(payload["statement_storage"]["folder"]["pdf_count"], 0)
            self.assertEqual(payload["statement_storage"]["history_database"]["status"], "Not available")
            self.assertFalse(missing_folder.exists())
            self.assertFalse(missing_history.exists())

    def test_payload_is_fixed_and_does_not_read_environment_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"ONEMIN_AI_API_KEY": "top-secret", "LEASE_TOKEN": "private-id"}, clear=False
        ):
            root = Path(directory)
            payload = operational_status(root / "p.db", root / "Statements", root / "h.db", application_port=8502, database_refresh_interval_seconds=20)
        rendered = repr(payload)
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("private-id", rendered)
        self.assertEqual(payload["application"]["framework"], "NiceGUI")

    def test_cache_intervals_come_from_configuration(self) -> None:
        self.assertEqual(CACHE_INTERVALS_SECONDS, {
            "Price": PRICE_CACHE_TTL_SECONDS,
            "Yahoo metadata": YAHOO_METADATA_CACHE_TTL_SECONDS,
            "Investment Intelligence": INVESTMENT_INTELLIGENCE_CACHE_TTL_SECONDS,
            "Dividend timing": DIVIDEND_TIMING_CACHE_TTL_SECONDS,
            "Historical context": HISTORICAL_CONTEXT_CACHE_TTL_SECONDS,
            "Fund sector weights": FUND_SECTOR_WEIGHTS_CACHE_TTL_SECONDS,
        })
        self.assertEqual([format_interval(value) for value in CACHE_INTERVALS_SECONDS.values()], [
            "1 minute", "1 day", "6 hours", "1 day", "10 minutes", "1 hour"
        ])

    def test_system_navigation_refresh_and_exclusions(self) -> None:
        source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"System"', source)
        self.assertIn("operational_status(", source)
        self.assertIn('refresh_button.on_click(lambda: refresh_data(True))', source)
        self.assertIn("ui.timer(DB_REFRESH_INTERVAL_SECONDS", source)
        self.assertEqual(source.count('ui.button("Refresh Data"'), 1)
        self.assertNotIn("Use Export JSON Data", source)
        self.assertNotIn("Strict Export Accuracy Mode", source)
        self.assertNotIn("run_statement_scan", source)
        self.assertNotIn("yfinance", source)
        self.assertIn("PORT = 8502", source)


if __name__ == "__main__":
    unittest.main()

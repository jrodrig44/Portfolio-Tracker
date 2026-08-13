from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from supplemental_research import SupplementalResearchService, connect_database, get_research_rows, initialize_database, set_setting, get_setting
from supplemental_research_worker import _load_holdings_contexts, run_once


class SupplementalResearchWorkerTests(unittest.TestCase):
    def test_context_loader_uses_database_holdings_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "portfolio.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE holdings (ticker TEXT, asset_type TEXT, sector TEXT, annual_div_per_share REAL, dividend_yield_pct REAL)")
                connection.execute("INSERT INTO holdings VALUES ('O', 'REIT', 'Real Estate', 3.2, 5.1)")
                connection.commit()
            finally:
                connection.close()
            contexts = _load_holdings_contexts(db_path)
            self.assertEqual(list(contexts), ["O"])
            self.assertEqual(contexts["O"]["asset_type"], "REIT")
            self.assertEqual(contexts["O"]["existing_yahoo_information"]["dividend_yield_pct"], 5.1)

    def test_once_with_no_due_holdings_makes_no_request_and_initializes_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "portfolio.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE holdings (ticker TEXT, asset_type TEXT, sector TEXT, annual_div_per_share REAL, dividend_yield_pct REAL)")
                connection.execute("INSERT INTO holdings VALUES ('O', 'REIT', 'Real Estate', 3.2, 5.1)")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(run_once(root), 0)
            initialize_database(db_path)
            row = get_research_rows(db_path, ["O"])[0]
            self.assertEqual(row["research_status"], "missing")
            self.assertEqual(row["manual_initial_research_completed"], 0)

    def test_once_repairs_invalid_persisted_model_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "portfolio.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE holdings (ticker TEXT, asset_type TEXT, sector TEXT, annual_div_per_share REAL, dividend_yield_pct REAL)")
                connection.execute("INSERT INTO holdings VALUES ('O', 'REIT', 'Real Estate', 3.2, 5.1)")
                connection.commit()
            finally:
                connection.close()

            initialize_database(db_path)
            SupplementalResearchService(db_path).sync_holdings(["O"])
            set_setting(db_path, "supplemental_research_model", "<MagicMock name='selectbox()' id='123'>")
            now = datetime.now(timezone.utc)
            with connect_database(db_path) as connection:
                connection.execute(
                    "UPDATE supplemental_holding_research SET research_status = 'current', manual_initial_research_completed = 1, last_success_at = ?, next_due_at = ? WHERE ticker = ?",
                    ((now - timedelta(days=31)).isoformat(), (now - timedelta(days=1)).isoformat(), "O"),
                )

            response = {
                "ticker": "O",
                "entity_name": "Realty Income Corporation",
                "retrieved_at": now.isoformat(),
                "verified_facts": [{
                    "field": "property_focus",
                    "value": "Net lease real estate",
                    "source_url": "https://www.realtyincome.com/",
                    "source_title": "Realty Income",
                    "source_type": "official_company",
                    "source_date": "",
                    "confidence": "high",
                }],
                "unresolved_fields": [],
                "interpretation": {"summary": "Supplemental context.", "portfolio_relevance": "Informational only.", "risks_to_monitor": []},
                "limitations": [],
            }
            client = MagicMock()
            client.chat_with_ai.return_value = SimpleNamespace(content=json.dumps(response), record_id="record-1")
            with patch("onemin_ai_client.OneMinAIClient.chat_with_ai", return_value=client.chat_with_ai.return_value) as chat_with_ai:
                self.assertEqual(run_once(root), 0)

            self.assertEqual(chat_with_ai.call_args.kwargs["model"], "gpt-4.1-mini")
            self.assertTrue(chat_with_ai.call_args.kwargs["web_search"])
            self.assertEqual(get_setting(db_path, "supplemental_research_model"), "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()

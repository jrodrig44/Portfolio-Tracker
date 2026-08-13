from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from supplemental_research import (
    DEFAULT_RESEARCH_MODEL,
    FAILED_RETRY_DELAY,
    SupplementalResearchService,
    acquire_worker_lease,
    get_research_rows,
    initialize_database,
    research_is_due,
    validate_research_result,
    worker_due_tickers,
)


class SupplementalResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "portfolio.db"
        self.now = datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc)
        self.response = {
            "ticker": "O",
            "entity_name": "Realty Income Corporation",
            "retrieved_at": "2026-08-12T21:00:00+00:00",
            "verified_facts": [{
                "field": "property_focus",
                "value": "Net lease real estate",
                "source_url": "https://www.realtyincome.com/",
                "source_title": "Realty Income",
                "source_type": "official_company",
                "source_date": "2026-08-01",
                "confidence": "high",
            }],
            "unresolved_fields": [],
            "interpretation": {"summary": "A diversified net lease REIT.", "portfolio_relevance": "Descriptive context only.", "risks_to_monitor": []},
            "limitations": [],
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_new_holding_is_missing_and_not_due(self) -> None:
        initialize_database(self.db_path)
        service = SupplementalResearchService(self.db_path, now_factory=lambda: self.now)
        service.sync_holdings(["O"])
        row = get_research_rows(self.db_path, ["O"])[0]
        self.assertEqual(row["research_status"], "missing")
        self.assertFalse(row["manual_initial_research_completed"])
        self.assertEqual(worker_due_tickers(self.db_path, self.now), [])

    def test_manual_success_enrolls_holding_and_sends_web_search(self) -> None:
        client = MagicMock()
        client.chat_with_ai.return_value = SimpleNamespace(content=json.dumps(self.response), record_id="record-1")
        service = SupplementalResearchService(self.db_path, client_factory=lambda **_: client, now_factory=lambda: self.now)
        result = service.research_ticker("O", {"ticker": "O", "missing_fields": ["property_focus"]}, DEFAULT_RESEARCH_MODEL, manual=True, force=True)
        self.assertEqual(result["status"], "current")
        row = get_research_rows(self.db_path, ["O"])[0]
        self.assertEqual(row["research_status"], "current")
        self.assertEqual(row["manual_initial_research_completed"], 1)
        self.assertEqual(row["provider_record_id"], "record-1")
        client.chat_with_ai.assert_called_once()
        self.assertEqual(client.chat_with_ai.call_args.kwargs["model"], DEFAULT_RESEARCH_MODEL)
        self.assertTrue(client.chat_with_ai.call_args.kwargs["web_search"])

    def test_current_research_is_skipped_without_request(self) -> None:
        client = MagicMock()
        client.chat_with_ai.return_value = SimpleNamespace(content=json.dumps(self.response), record_id="record-1")
        service = SupplementalResearchService(self.db_path, client_factory=lambda **_: client, now_factory=lambda: self.now)
        service.research_ticker("O", {"ticker": "O"}, DEFAULT_RESEARCH_MODEL, manual=True, force=True)
        result = service.research_ticker("O", {"ticker": "O"}, DEFAULT_RESEARCH_MODEL, manual=False, force=False)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(client.chat_with_ai.call_count, 1)

    def test_failed_refresh_preserves_previous_success(self) -> None:
        client = MagicMock()
        client.chat_with_ai.return_value = SimpleNamespace(content=json.dumps(self.response), record_id="record-1")
        service = SupplementalResearchService(self.db_path, client_factory=lambda **_: client, now_factory=lambda: self.now)
        service.research_ticker("O", {"ticker": "O"}, DEFAULT_RESEARCH_MODEL, manual=True, force=True)
        client.chat_with_ai.side_effect = RuntimeError("provider failure")
        row = get_research_rows(self.db_path, ["O"])[0]
        row["next_due_at"] = (self.now - timedelta(days=1)).isoformat()
        with service.db_path.open("ab"):
            pass
        with __import__("supplemental_research").connect_database(self.db_path) as connection:
            connection.execute("UPDATE supplemental_holding_research SET next_due_at = ? WHERE ticker = 'O'", (row["next_due_at"],))
        result = service.research_ticker("O", {"ticker": "O"}, DEFAULT_RESEARCH_MODEL, manual=False)
        updated = get_research_rows(self.db_path, ["O"])[0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(updated["research_status"], "failed")
        self.assertIn("property_focus", updated["research_json"])
        self.assertEqual(updated["next_due_at"], (self.now + FAILED_RETRY_DELAY).isoformat())

    def test_validation_rejects_bad_ticker_url_and_prohibited_field(self) -> None:
        with self.assertRaises(ValueError):
            validate_research_result({**self.response, "ticker": "KO"}, "O")
        with self.assertRaises(ValueError):
            validate_research_result({**self.response, "verified_facts": [{**self.response["verified_facts"][0], "source_url": "http://example.com"}]}, "O")
        with self.assertRaises(ValueError):
            validate_research_result({**self.response, "verified_facts": [{**self.response["verified_facts"][0], "field": "current_price"}]}, "O")

    def test_lease_blocks_duplicate_worker_and_recovers_stale_lease(self) -> None:
        initialize_database(self.db_path)
        self.assertTrue(acquire_worker_lease(self.db_path, now=self.now))
        self.assertFalse(acquire_worker_lease(self.db_path, now=self.now + timedelta(minutes=1)))
        self.assertTrue(acquire_worker_lease(self.db_path, now=self.now + timedelta(minutes=31)))

    def test_due_policy_requires_manual_initial_success(self) -> None:
        current = {"archived": 0, "manual_initial_research_completed": 0, "research_status": "missing"}
        self.assertFalse(research_is_due(current, self.now))
        due = {"archived": 0, "manual_initial_research_completed": 1, "research_status": "current", "last_success_at": (self.now - timedelta(days=31)).isoformat(), "next_due_at": None}
        self.assertTrue(research_is_due(due, self.now))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from portfolio_research import PortfolioResearchService, research_contexts, research_display_row
from supplemental_research import (
    DEFAULT_RESEARCH_MODEL,
    GENERAL_RESEARCH_EXPIRY,
    SUPPORTED_RESEARCH_MODELS,
    WORKER_CHECK_INTERVAL,
    SupplementalResearchService,
    get_research_rows,
)


class NiceGUIPhase2EResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "portfolio.db"
        self.now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
        self.context = research_contexts([{"ticker": "O", "asset_type": "REIT", "sector": "Real Estate"}])
        self.response = {
            "ticker": "O", "entity_name": "Realty Income", "retrieved_at": self.now.isoformat(),
            "verified_facts": [{"field": "property_focus", "value": "Net lease", "source_url": "https://example.com/o", "source_title": "Issuer", "source_type": "official", "source_date": "2026-08-01", "confidence": "high"}],
            "unresolved_fields": [],
            "interpretation": {"summary": "Descriptive context", "portfolio_relevance": "Context only", "risks_to_monitor": []},
            "limitations": [],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def service_with_client(self) -> tuple[PortfolioResearchService, MagicMock]:
        client = MagicMock()
        client.chat_with_ai.return_value = SimpleNamespace(content=json.dumps(self.response), record_id="record-1")
        shared = SupplementalResearchService(self.db_path, client_factory=lambda **_: client, now_factory=lambda: self.now)
        return PortfolioResearchService(self.db_path, shared), client

    def test_page_read_syncs_state_without_enrolling_or_calling_provider(self) -> None:
        service, client = self.service_with_client()
        package = service.state(self.context, self.now)
        self.assertEqual(package["active"][0]["status"], "Not enrolled")
        self.assertFalse(package["active"][0]["enrolled"])
        client.chat_with_ai.assert_not_called()

    def test_explicit_manual_research_enrolls_and_duplicate_enrollment_is_safe(self) -> None:
        service, client = self.service_with_client()
        first = service.update("O", self.context["O"], DEFAULT_RESEARCH_MODEL)
        second = service.update("O", self.context["O"], DEFAULT_RESEARCH_MODEL)
        self.assertEqual(first["status"], "current")
        self.assertEqual(second["status"], "current")
        rows = get_research_rows(self.db_path, ["O"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["manual_initial_research_completed"], 1)
        self.assertEqual(client.chat_with_ai.call_count, 2)

    def test_due_and_retry_status_use_production_timestamps(self) -> None:
        due = {"ticker": "O", "archived": 0, "manual_initial_research_completed": 1, "research_status": "current", "last_success_at": (self.now - GENERAL_RESEARCH_EXPIRY - timedelta(seconds=1)).isoformat()}
        retry = {**due, "research_status": "failed", "next_due_at": (self.now + timedelta(hours=23)).isoformat(), "error_message": "provider failed"}
        self.assertEqual(research_display_row(due, self.now)["status"], "Due for refresh")
        self.assertEqual(research_display_row(retry, self.now)["status"], "Retry pending")

    def test_failed_refresh_preserves_prior_successful_display(self) -> None:
        service, client = self.service_with_client()
        service.update("O", self.context["O"], DEFAULT_RESEARCH_MODEL)
        client.chat_with_ai.side_effect = RuntimeError("Authorization: secret https://provider.invalid")
        failed = service.update("O", self.context["O"], DEFAULT_RESEARCH_MODEL)
        display = service.state(self.context, self.now)["active"][0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Verified Supplemental Facts", display["research_markdown"])
        self.assertNotIn("secret", display["error"])
        self.assertNotIn("provider.invalid", display["error"])

    def test_deleted_holding_is_archived_and_readding_restores_without_deleting_result(self) -> None:
        service, _ = self.service_with_client()
        service.update("O", self.context["O"], DEFAULT_RESEARCH_MODEL)
        archived = service.state({}, self.now)
        self.assertEqual(archived["archived"][0]["status"], "Archived")
        restored = service.state(self.context, self.now)
        self.assertTrue(restored["active"][0]["enrolled"])
        self.assertIn("Verified Supplemental Facts", restored["active"][0]["research_markdown"])

    def test_model_setting_is_validated_and_persisted(self) -> None:
        service, _ = self.service_with_client()
        selected = SUPPORTED_RESEARCH_MODELS[-1]
        self.assertEqual(service.set_model(selected), selected)
        self.assertEqual(service.state(self.context)["model"], selected)
        with self.assertRaises(ValueError):
            service.set_model("arbitrary-provider-model")

    def test_duplicate_update_guard_does_not_start_second_provider_call(self) -> None:
        started = threading.Event()
        release = threading.Event()
        shared = MagicMock()
        def slow_update(*_args: object, **_kwargs: object) -> dict[str, str]:
            started.set(); release.wait(2)
            return {"ticker": "O", "status": "current"}
        shared.research_ticker.side_effect = slow_update
        service = PortfolioResearchService(self.db_path, shared)
        thread = threading.Thread(target=service.update, args=("O", self.context["O"], DEFAULT_RESEARCH_MODEL))
        thread.start(); self.assertTrue(started.wait(1))
        duplicate = service.update("O", self.context["O"], DEFAULT_RESEARCH_MODEL)
        release.set(); thread.join(2)
        self.assertEqual(duplicate["reason"], "already running")
        self.assertEqual(shared.research_ticker.call_count, 1)

    def test_holdings_table_and_separate_history_database_are_unchanged(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("CREATE TABLE holdings (ticker TEXT, shares REAL)")
            connection.execute("INSERT INTO holdings VALUES ('O', 10)")
            connection.commit()
        history_path = Path(self.temp.name) / "history.sqlite"
        with closing(sqlite3.connect(history_path)) as connection:
            connection.execute("CREATE TABLE history_marker (value TEXT)")
            connection.execute("INSERT INTO history_marker VALUES ('unchanged')")
            connection.commit()
        service, _ = self.service_with_client()
        service.state(self.context)
        service.set_model(DEFAULT_RESEARCH_MODEL)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM holdings").fetchall(), [("O", 10.0)])
        with closing(sqlite3.connect(history_path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM history_marker").fetchall(), [("unchanged",)])

    def test_context_and_display_exclude_prohibited_and_internal_fields(self) -> None:
        context = research_contexts([{"ticker": "O", "shares": 99, "amount_invested": 5000}])["O"]
        self.assertNotIn("shares", context)
        self.assertNotIn("amount_invested", context)
        display = research_display_row({"ticker": "O", "research_status": "missing", "provider_record_id": "secret", "sources_json": "[]"}, self.now)
        self.assertNotIn("provider_record_id", display)
        self.assertNotIn("sources_json", display)

    def test_ui_contract_is_async_responsive_and_does_not_start_worker(self) -> None:
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        worker_source = (Path(__file__).parents[1] / "supplemental_research_worker.py").read_text(encoding="utf-8")
        self.assertIn('"Supplemental Research"', source)
        self.assertIn('await critical_io("research_update", research_service.update', source)
        self.assertIn("grid-cols-1 lg:grid-cols-2", source)
        self.assertIn("w-full sm:w-auto min-h-11", source)
        self.assertNotIn("supplemental_research_worker", source)
        self.assertEqual(WORKER_CHECK_INTERVAL, timedelta(hours=6))
        self.assertIn("acquire_worker_lease", worker_source)


if __name__ == "__main__":
    unittest.main()

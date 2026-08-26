from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from nicegui_app.main import critical_io
from nicegui_app.process_lifecycle import LifecycleCoordinator, ShutdownInProgress
from nicegui_app.services.ai_snapshot import (
    AIAnalysisSnapshotService,
    SNAPSHOT_SCHEMA_VERSION,
    friendly_utc_timestamp,
    resolve_analysis_download,
    save_successful_analysis_snapshot,
)
from portfolio_ai import AIAnalysisState


class NiceGuiAIAnalysisSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "portfolio.db"
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute("CREATE TABLE holdings (ticker TEXT PRIMARY KEY, shares REAL)")
                connection.execute("INSERT INTO holdings VALUES ('APLE', 2)")
        self.service = AIAnalysisSnapshotService(self.db_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def successful_state(result: str = "# Snapshot one", model: str = "gpt-4o-mini") -> AIAnalysisState:
        return AIAnalysisState(
            last_stage="success",
            result_markdown=result,
            result_model=model,
            result_type="Contribution Allocation Review",
        )

    def test_no_snapshot_empty_state_and_read_does_not_create_table(self) -> None:
        self.assertIsNone(self.service.load())
        self.assertEqual(self.service.count(), 0)
        with closing(sqlite3.connect(self.db_path)) as connection:
            table = connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'ai_analysis_snapshot'").fetchone()
        self.assertIsNone(table)

    def test_success_creates_single_snapshot_and_second_success_replaces_it(self) -> None:
        first = save_successful_analysis_snapshot(self.service, self.successful_state())
        second = save_successful_analysis_snapshot(self.service, self.successful_state("# Snapshot two", "gpt-4.1-mini"))

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(self.service.count(), 1)
        restored = self.service.load()
        self.assertEqual(restored.result_markdown, "# Snapshot two")
        self.assertEqual(restored.model, "gpt-4.1-mini")
        self.assertEqual(restored.analysis_type, "Contribution Allocation Review")
        self.assertEqual(restored.schema_version, SNAPSHOT_SCHEMA_VERSION)

    def test_snapshot_survives_service_recreation_and_shared_clients(self) -> None:
        self.service.save("Risk Analysis", "gpt-4o-mini", "# Shared result")

        another_browser = AIAnalysisSnapshotService(self.db_path)

        self.assertEqual(another_browser.load().result_markdown, "# Shared result")
        self.assertEqual(another_browser.count(), 1)

    def test_download_prefers_current_success_over_older_snapshot(self) -> None:
        self.service.save("Old", "old-model", "# Persisted result")
        state = self.successful_state("# Current result")

        self.assertEqual(resolve_analysis_download(self.service, state), "# Current result")

    def test_download_falls_back_to_exact_snapshot_after_current_is_cleared(self) -> None:
        expected = "# Persisted result\n\nExact saved body."
        self.service.save("Risk Analysis", "model", expected)
        state = self.successful_state("# Current result")
        state.clear()

        self.assertEqual(resolve_analysis_download(self.service, state), expected)

    def test_failed_incomplete_and_busy_current_states_do_not_override_snapshot(self) -> None:
        self.service.save("Prior", "model", "# Last known good")
        states = (
            AIAnalysisState(last_stage="error", error_message="failed", result_markdown="# Partial"),
            AIAnalysisState(last_stage="preparing", result_markdown="# Partial"),
            AIAnalysisState(last_stage="success", busy=True, result_markdown="# Partial"),
            AIAnalysisState(last_stage="success", error_message="failed", result_markdown="# Partial"),
        )

        for state in states:
            with self.subTest(stage=state.last_stage, busy=state.busy):
                self.assertEqual(resolve_analysis_download(self.service, state), "# Last known good")

    def test_no_download_when_current_and_snapshot_are_unavailable(self) -> None:
        state = AIAnalysisState()
        self.assertIsNone(resolve_analysis_download(self.service, state))
        self.service.save("Risk Analysis", "model", "# Saved")
        self.assertTrue(self.service.clear())
        self.assertIsNone(resolve_analysis_download(self.service, state))

    def test_snapshot_download_survives_service_and_view_recreation(self) -> None:
        self.service.save("Risk Analysis", "model", "# Recreated view result")
        recreated_service = AIAnalysisSnapshotService(self.db_path)
        recreated_state = AIAnalysisState()

        self.assertEqual(resolve_analysis_download(recreated_service, recreated_state), "# Recreated view result")

    def test_timestamp_is_stored_in_utc_and_has_friendly_fallback(self) -> None:
        local_time = datetime(2026, 8, 22, 16, 18, tzinfo=timezone(timedelta(hours=-4)))

        snapshot = self.service.save("Risk Analysis", "model", "result", completed_at=local_time)

        parsed = datetime.fromisoformat(snapshot.completed_at_utc)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertEqual(parsed.hour, 20)
        self.assertEqual(friendly_utc_timestamp(snapshot.completed_at_utc), "August 22, 2026 at 8:18 PM UTC")

    def test_failed_cancelled_and_invalid_states_preserve_previous_snapshot(self) -> None:
        self.service.save("Prior", "prior-model", "# Last known good")
        states = (
            AIAnalysisState(last_stage="error", error_message="provider failed"),
            AIAnalysisState(last_stage="preparing", busy=False),
            AIAnalysisState(last_stage="success", result_markdown="", result_model="model", result_type="Risk Analysis"),
        )

        for state in states:
            with self.subTest(stage=state.last_stage, error=state.error_message):
                self.assertIsNone(save_successful_analysis_snapshot(self.service, state))
                self.assertEqual(self.service.load().result_markdown, "# Last known good")
                self.assertEqual(self.service.count(), 1)

    def test_snapshot_schema_contains_no_provider_credentials_or_payloads(self) -> None:
        self.service.save("Risk Analysis", "fake-model", "# Fake test response")
        with closing(sqlite3.connect(self.db_path)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_analysis_snapshot)")}

        self.assertEqual(
            columns,
            {"snapshot_id", "schema_version", "analysis_type", "completed_at_utc", "model", "result_markdown"},
        )
        for prohibited in ("api_key", "authorization", "headers", "environment", "request", "response", "context", "payload"):
            self.assertNotIn(prohibited, columns)

    def test_clear_removes_only_snapshot_and_preserves_portfolio_data(self) -> None:
        self.service.save("Risk Analysis", "model", "result")

        self.assertTrue(self.service.clear())

        self.assertIsNone(self.service.load())
        with closing(sqlite3.connect(self.db_path)) as connection:
            holding = connection.execute("SELECT ticker, shares FROM holdings").fetchone()
        self.assertEqual(holding, ("APLE", 2.0))

    def test_shutdown_rejects_snapshot_save_and_preserves_previous(self) -> None:
        self.service.save("Prior", "model", "prior")
        coordinator = LifecycleCoordinator()
        coordinator.begin_shutdown()
        with patch("nicegui_app.main.lifecycle", coordinator), patch("nicegui_app.main.ui.notify"):
            with self.assertRaises(ShutdownInProgress):
                asyncio.run(critical_io("ai_analysis_snapshot_save", self.service.save, "New", "model", "new"))
        self.assertEqual(self.service.load().result_markdown, "prior")

    def test_shutdown_rejects_snapshot_clear_and_preserves_previous(self) -> None:
        self.service.save("Prior", "model", "prior")
        coordinator = LifecycleCoordinator()
        coordinator.begin_shutdown()
        with patch("nicegui_app.main.lifecycle", coordinator), patch("nicegui_app.main.ui.notify"):
            with self.assertRaises(ShutdownInProgress):
                asyncio.run(critical_io("ai_analysis_snapshot_clear", self.service.clear))
        self.assertEqual(self.service.load().result_markdown, "prior")

    def test_ui_contract_has_compact_summary_viewer_confirmation_and_protected_writes(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('ui.label("Last Analysis Snapshot")', source)
        self.assertIn("No saved AI analysis yet.", source)
        self.assertIn("Run an analysis to create the first snapshot.", source)
        self.assertIn("Clear the saved AI Analysis snapshot?", source)
        self.assertIn("It does not affect your portfolio data.", source)
        self.assertIn('critical_io("ai_analysis_snapshot_clear"', source)
        self.assertIn('"ai_analysis_snapshot_save",', source)
        self.assertIn('ui.button("View", icon="visibility", on_click=open_snapshot_viewer)', source)
        self.assertIn("def open_snapshot_viewer()", source)
        self.assertIn("SNAPSHOT_VIEWER_CARD", source)
        self.assertIn('for heading in ("Analysis Type", "Run Date/Time", "Model", "Action")', source)
        self.assertEqual(source.count("with ui.card().classes(\"w-full min-w-0 overflow-hidden p-0\")"), 1)
        self.assertIn("ui.markdown(snapshot.result_markdown).classes(SAFE_RICH_OUTPUT)", source)
        viewer = source.split("def open_snapshot_viewer()", 1)[1].split("viewer.open()", 1)[0]
        scroll_region = 'classes("w-full flex-1 min-h-0 overflow-y-auto overflow-x-hidden overscroll-contain")'
        self.assertIn(scroll_region, viewer)
        self.assertLess(viewer.index(scroll_region), viewer.index("ui.markdown(snapshot.result_markdown)"))
        self.assertLess(viewer.index("ui.markdown(snapshot.result_markdown)"), viewer.index("Clear Snapshot…"))
        self.assertIn('classes(f"{RESPONSIVE_ACTIONS} shrink-0")', viewer)
        summary = source.split("with ui.card().classes(\"w-full min-w-0 overflow-hidden p-0\")", 1)[1].split("async def analyze", 1)[0]
        self.assertNotIn("ui.markdown(snapshot.result_markdown)", summary)
        self.assertIn("Intl.DateTimeFormat", source)
        self.assertIn('on_click=download_analysis', source)
        self.assertIn('resolve_analysis_download(snapshot_service, state)', source)
        self.assertIn('No AI analysis is available to download.', source)
        self.assertNotIn('ui.download(state.result_markdown.encode("utf-8")', source)


if __name__ == "__main__":
    unittest.main()

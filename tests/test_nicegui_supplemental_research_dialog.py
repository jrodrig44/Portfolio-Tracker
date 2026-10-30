from __future__ import annotations

import unittest
from pathlib import Path

from nicegui_app.services.formatting import SNAPSHOT_RICH_OUTPUT, SNAPSHOT_VIEWER_BODY, SNAPSHOT_VIEWER_CARD
from portfolio_research import research_viewer_record


MAIN = (Path(__file__).resolve().parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")


class SupplementalResearchDialogTests(unittest.TestCase):
    def test_async_research_notifications_use_live_page_context(self) -> None:
        notify_helper = MAIN.split("def notify_page", 1)[1].split("ui.add_head_html", 1)[0]
        research_handlers = MAIN.split("async def render_supplemental_research", 1)[1].split("def open_existing_research", 1)[0]

        self.assertIn("client_is_alive(page_client, lifecycle)", notify_helper)
        self.assertIn("with page_client:", notify_helper)
        self.assertIn("notify_page(", research_handlers)
        self.assertNotIn("ui.notify(", research_handlers)

    def test_viewer_record_preserves_clicked_holding_and_persisted_metadata(self) -> None:
        first = research_viewer_record({
            "ticker": "aaa", "research_markdown": "# First research", "last_success_at": "2026-08-01",
            "model_used": "model-a", "status": "Current", "next_due_at": "2026-08-31",
        })
        second = research_viewer_record({
            "ticker": "bbb", "research_markdown": "# Second research", "last_success_at": "2026-08-02",
            "model_used": "model-b", "status": "Due for refresh", "next_due_at": "2026-09-01",
        })

        self.assertEqual(first["ticker"], "AAA")
        self.assertEqual(first["research_markdown"], "# First research")
        self.assertEqual(second["ticker"], "BBB")
        self.assertEqual(second["research_markdown"], "# Second research")
        self.assertEqual(second["status"], "Due for refresh")

    def test_missing_research_does_not_create_viewer_data(self) -> None:
        self.assertIsNone(research_viewer_record({"ticker": "EMPTY"}))
        self.assertIsNone(research_viewer_record({"ticker": "EMPTY", "research_markdown": "  "}))

    def test_existing_research_uses_read_only_scrollable_dialog_not_inline_expansion(self) -> None:
        block = MAIN.split("def open_existing_research", 1)[1].split('ui.label("Portfolio Holdings")', 1)[0]
        holdings_block = MAIN.split('ui.label("Portfolio Holdings")', 1)[1].split('if package["archived"]', 1)[0]

        self.assertIn("with ui.dialog() as viewer", block)
        self.assertIn("SNAPSHOT_VIEWER_CARD", block)
        self.assertIn("classes(SNAPSHOT_VIEWER_BODY)", block)
        self.assertIn("SNAPSHOT_RICH_OUTPUT", block)
        self.assertIn('ui.button("Close", on_click=viewer.close)', block)
        self.assertIn('ui.button("Existing Research"', holdings_block)
        self.assertNotIn('ui.expansion("Existing Research"', holdings_block)
        for forbidden in ("research_service.update", "render_supplemental_research", "current_market_data", "critical_io", "run.io_bound"):
            self.assertNotIn(forbidden, block)

    def test_reused_viewer_styles_are_responsive_and_scroll_internally(self) -> None:
        self.assertIn("96vw", SNAPSHOT_VIEWER_CARD)
        self.assertIn("90dvh", SNAPSHOT_VIEWER_CARD)
        self.assertIn("overflow-hidden", SNAPSHOT_VIEWER_CARD)
        self.assertIn("overflow-y-auto", SNAPSHOT_VIEWER_BODY)
        self.assertIn("overflow-x-hidden", SNAPSHOT_VIEWER_BODY)
        self.assertIn("break-words", SNAPSHOT_RICH_OUTPUT)


if __name__ == "__main__":
    unittest.main()

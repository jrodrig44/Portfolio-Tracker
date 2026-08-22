from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nicegui_app.services.portfolio_data import load_holdings
from portfolio_holdings_write import HoldingNotFoundError, HoldingsWriteService
from portfolio_research import PortfolioResearchService, research_contexts
from portfolio_statement_history import StatementHistoryService
from stash_history import import_validated_history, load_statement_history, prepare_history_database
from supplemental_research import DEFAULT_RESEARCH_MODEL, SupplementalResearchService, get_research_rows, set_setting


HOLDINGS_SCHEMA = """
    ticker TEXT, shares REAL DEFAULT 0, amount_invested REAL DEFAULT 0,
    annual_div_per_share REAL DEFAULT 0, regular_biweekly_contribution REAL DEFAULT 0,
    espp_biweekly_contribution REAL DEFAULT 0, espp_contribution_biweekly REAL DEFAULT 0,
    special_contribution REAL DEFAULT 0, employer_stock INTEGER DEFAULT 0,
    espp_enabled INTEGER DEFAULT 0, is_espp INTEGER DEFAULT 0,
    espp_discount REAL DEFAULT 0, espp_discount_pct REAL DEFAULT 0,
    espp_purchase_frequency_months REAL DEFAULT 6, espp_purchase_frequency TEXT DEFAULT '',
    espp_acquired_shares REAL DEFAULT 0, sector TEXT, asset_type TEXT,
    preserved_note TEXT DEFAULT 'preserved'
"""


def holding(ticker: str = "AAA", shares: float = 1.123456789) -> dict[str, object]:
    return {
        "ticker": ticker, "shares": shares, "amount_invested": 80.25,
        "annual_div_per_share": 2.5, "regular_biweekly_contribution": 10,
        "espp_contribution_biweekly": 0, "special_contribution": 5,
        "espp_acquired_shares": 0, "is_espp": False, "espp_discount": 0,
        "espp_purchase_frequency_months": 6, "sector": "Technology", "asset_type": "ETF",
    }


def statement(source_hash: str = "statement-one") -> dict[str, object]:
    return {
        "source_hash": source_hash, "statement_start_date": "2026-01-01",
        "statement_end_date": "2026-01-31", "portfolio_value": 1000.0,
        "invested_value": 900.0, "cash_value": 100.0, "status": "valid",
        "diagnostics": {"accounting_equation_passed": True},
    }


def provider_result(ticker: str) -> dict[str, object]:
    return {
        "ticker": ticker, "entity_name": ticker, "retrieved_at": "2026-08-21T12:00:00+00:00",
        "verified_facts": [{"field": "strategy_objective", "value": "Retained fact", "source_url": "https://example.com/fact", "source_title": "Issuer", "source_type": "official", "source_date": "2026-08-01", "confidence": "high"}],
        "unresolved_fields": [], "interpretation": {"summary": "Retained analysis", "portfolio_relevance": "Context", "risks_to_monitor": []}, "limitations": [],
    }


class NiceGUIPhase2GWriteSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        self.portfolio_db = root / "portfolio.db"
        self.history_db = root / "history.sqlite"
        self.statements = root / "Statements"
        self.statements.mkdir()
        with sqlite3.connect(self.portfolio_db) as connection:
            connection.execute(f"CREATE TABLE holdings ({HOLDINGS_SCHEMA})")
        prepare_history_database(self.history_db)
        self.holdings = HoldingsWriteService(self.portfolio_db)
        self.statement_service = StatementHistoryService(self.statements, self.history_db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def research_service(self) -> tuple[PortfolioResearchService, MagicMock]:
        client = MagicMock()
        client.chat_with_ai.side_effect = lambda **kwargs: SimpleNamespace(
            content=json.dumps(provider_result(json.loads(kwargs["prompt"].split("REQUESTED CONTEXT:\n", 1)[1])["ticker"])),
            record_id="provider-private-id",
        )
        shared = SupplementalResearchService(self.portfolio_db, client_factory=lambda **_: client)
        return PortfolioResearchService(self.portfolio_db, shared), client

    def portfolio_rows(self) -> list[tuple[object, ...]]:
        with sqlite3.connect(self.portfolio_db) as connection:
            return connection.execute("SELECT rowid, * FROM holdings ORDER BY rowid").fetchall()

    def test_holding_contribution_edit_sequence_preserves_identity_precision_and_unrelated_fields(self) -> None:
        created = self.holdings.add(holding())
        rowid = created["_holding_id"]
        with sqlite3.connect(self.portfolio_db) as connection:
            connection.execute("UPDATE holdings SET preserved_note='custom' WHERE rowid=?", (rowid,))
        before = self.holdings.get(rowid)
        changed = self.holdings.update_contributions(rowid, {
            "regular_biweekly_contribution": 25.25, "special_contribution": 55.5,
            "espp_contribution_biweekly": 12.75, "is_espp": True,
            "espp_discount": 15, "espp_purchase_frequency_months": 3,
        }, submission_token="contribution-once")
        for field in ("_holding_id", "ticker", "shares", "amount_invested", "annual_div_per_share", "sector", "asset_type", "preserved_note"):
            self.assertEqual(changed[field], before[field])
        self.assertEqual(len(self.portfolio_rows()), 1)
        edited = self.holdings.edit(rowid, {"shares": 2.987654321}, submission_token="edit-after-contribution")
        self.assertEqual(edited["_holding_id"], rowid)
        self.assertEqual(edited["shares"], 2.987654321)
        self.assertEqual(edited["regular_biweekly_contribution"], 25.25)
        self.assertEqual(edited["espp_contribution_biweekly"], 12.75)
        self.assertEqual(edited["preserved_note"], "custom")

    def test_stale_rowid_edit_contribution_and_expected_ticker_delete_are_safe(self) -> None:
        first = self.holdings.add(holding("DUP", 1))
        second = self.holdings.add(holding("DUP", 2))
        self.holdings.delete(first["_holding_id"], confirmed=True, expected_ticker="DUP")
        with self.assertRaises(HoldingNotFoundError):
            self.holdings.update_contributions(first["_holding_id"], {"regular_biweekly_contribution": 999})
        with self.assertRaises(HoldingNotFoundError):
            self.holdings.edit(first["_holding_id"], {"shares": 999})
        self.assertEqual(self.holdings.get(second["_holding_id"])["shares"], 2)
        with sqlite3.connect(self.portfolio_db) as connection:
            connection.execute("UPDATE holdings SET ticker='CHANGED' WHERE rowid=?", (second["_holding_id"],))
        with self.assertRaises(HoldingNotFoundError):
            self.holdings.delete(second["_holding_id"], confirmed=True, expected_ticker="DUP")
        self.assertEqual(self.holdings.get(second["_holding_id"])["ticker"], "CHANGED")

    def test_duplicate_tokens_cover_every_holding_operation_and_allow_distinct_actions(self) -> None:
        created = self.holdings.add(holding(), submission_token="add")
        self.assertIsNone(self.holdings.add(holding(), submission_token="add"))
        rowid = created["_holding_id"]
        self.assertIsNotNone(self.holdings.edit(rowid, {"shares": 2}, submission_token="edit"))
        self.assertIsNone(self.holdings.edit(rowid, {"shares": 3}, submission_token="edit"))
        self.assertIsNotNone(self.holdings.edit(rowid, {"shares": 3}, submission_token="edit-distinct"))
        self.assertIsNotNone(self.holdings.update_contributions(rowid, {"regular_biweekly_contribution": 20}, submission_token="contribution"))
        self.assertIsNone(self.holdings.update_contributions(rowid, {"regular_biweekly_contribution": 30}, submission_token="contribution"))
        self.assertIsNotNone(self.holdings.delete(rowid, confirmed=True, expected_ticker="AAA", submission_token="delete"))
        self.assertIsNone(self.holdings.delete(rowid, confirmed=True, expected_ticker="AAA", submission_token="delete"))
        self.assertEqual(self.portfolio_rows(), [])

    def test_failed_holding_transaction_rolls_back_recovers_and_preserves_other_features(self) -> None:
        created = self.holdings.add(holding())
        research, _ = self.research_service()
        set_setting(self.portfolio_db, "integration_marker", "research-preserved")
        import_validated_history([statement()], self.history_db)
        with sqlite3.connect(self.portfolio_db) as connection:
            connection.execute("CREATE TRIGGER fail_holding_update BEFORE UPDATE ON holdings BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.holdings.edit(created["_holding_id"], {"shares": 99, "sector": "Broken"})
        self.assertEqual(self.holdings.get(created["_holding_id"])["shares"], 1.123456789)
        self.assertEqual(load_statement_history(self.history_db)[0]["portfolio_value"], 1000)
        self.assertEqual(research.state(research_contexts(load_holdings(self.portfolio_db)))["active"][0]["status"], "Not enrolled")
        with sqlite3.connect(self.portfolio_db) as connection:
            self.assertEqual(connection.execute("SELECT setting_value FROM supplemental_research_settings WHERE setting_key='integration_marker'").fetchone()[0], "research-preserved")
            connection.execute("DROP TRIGGER fail_holding_update")
        self.assertEqual(self.holdings.edit(created["_holding_id"], {"shares": 4})["shares"], 4)

    def test_statement_manual_import_delete_and_recovery_are_portfolio_isolated(self) -> None:
        self.holdings.add(holding())
        research, _ = self.research_service()
        research.state(research_contexts(load_holdings(self.portfolio_db)))
        set_setting(self.portfolio_db, "isolation", "keep")
        portfolio_before = self.portfolio_rows()
        manual_id = self.statement_service.save_review("2021-08", "2021-08-31", 900, 100, 1000, True)
        self.statement_service.save_review("2021-08", "2021-08-31", 800, 100, 900, True, manual_id=manual_id)
        self.statement_service.remove_review(manual_id)
        rejected_token = "retryable-import"
        with patch.object(self.statement_service, "state", return_value={"can_import": False, "records": []}):
            with self.assertRaises(ValueError):
                self.statement_service.import_ready(submission_token=rejected_token)
        ready = {"can_import": True, "records": [statement()]}
        with patch.object(self.statement_service, "state", return_value=ready):
            first = self.statement_service.import_ready(submission_token=rejected_token)
            duplicate_click = self.statement_service.import_ready(submission_token=rejected_token)
        self.assertEqual(first["imported"], 1)
        self.assertIsNone(duplicate_click)
        self.assertEqual(import_validated_history([statement()], self.history_db)["duplicates"], 1)
        self.statement_service.delete_imported(confirmed=True)
        self.assertEqual(load_statement_history(self.history_db), [])
        self.assertEqual(self.portfolio_rows(), portfolio_before)
        with sqlite3.connect(self.portfolio_db) as connection:
            self.assertEqual(connection.execute("SELECT setting_value FROM supplemental_research_settings WHERE setting_key='isolation'").fetchone()[0], "keep")

    def test_delete_readd_and_ticker_edit_archive_restore_research_without_provider_calls(self) -> None:
        created = self.holdings.add(holding("AAA"))
        research, client = self.research_service()
        contexts = research_contexts(load_holdings(self.portfolio_db))
        research.state(contexts)
        research.update("AAA", contexts["AAA"], DEFAULT_RESEARCH_MODEL)
        self.assertEqual(client.chat_with_ai.call_count, 1)
        self.holdings.delete(created["_holding_id"], confirmed=True, expected_ticker="AAA")
        archived = research.state(research_contexts(load_holdings(self.portfolio_db)))
        self.assertEqual(archived["archived"][0]["status"], "Archived")
        self.assertIn("Retained analysis", archived["archived"][0]["research_markdown"])
        restored_holding = self.holdings.add(holding("AAA"))
        restored = research.state(research_contexts(load_holdings(self.portfolio_db)))
        self.assertEqual(restored["active"][0]["status"], "Current")
        self.assertIn("Retained analysis", restored["active"][0]["research_markdown"])
        self.assertEqual(client.chat_with_ai.call_count, 1)
        self.holdings.edit(restored_holding["_holding_id"], {"ticker": "BBB"})
        changed = research.state(research_contexts(load_holdings(self.portfolio_db)))
        self.assertEqual({row["ticker"] for row in changed["archived"]}, {"AAA"})
        b = next(row for row in changed["active"] if row["ticker"] == "BBB")
        self.assertEqual(b["status"], "Not enrolled")
        self.assertNotIn("Retained analysis", b["research_markdown"])
        self.assertEqual(client.chat_with_ai.call_count, 1)

    def test_research_same_ticker_guard_failure_release_retry_and_different_tickers(self) -> None:
        for ticker in ("AAA", "BBB"):
            self.holdings.add(holding(ticker))
        contexts = research_contexts(load_holdings(self.portfolio_db))
        shared = MagicMock()
        entered = threading.Event(); release = threading.Event()
        def slow_update(ticker: str, *_args, **_kwargs):
            entered.set(); release.wait(2); return {"ticker": ticker, "status": "current"}
        shared.research_ticker.side_effect = slow_update
        research = PortfolioResearchService(self.portfolio_db, shared)
        thread = threading.Thread(target=research.update, args=("AAA", contexts["AAA"], DEFAULT_RESEARCH_MODEL))
        thread.start(); self.assertTrue(entered.wait(1))
        self.assertEqual(research.update("AAA", contexts["AAA"], DEFAULT_RESEARCH_MODEL)["reason"], "already running")
        self.assertEqual(research.update("BBB", contexts["BBB"], DEFAULT_RESEARCH_MODEL)["status"], "current")
        release.set(); thread.join(2)
        self.assertEqual(shared.research_ticker.call_count, 2)
        shared.research_ticker.side_effect = RuntimeError("provider failed")
        with self.assertRaises(RuntimeError):
            research.update("AAA", contexts["AAA"], DEFAULT_RESEARCH_MODEL)
        shared.research_ticker.side_effect = lambda ticker, *_args, **_kwargs: {"ticker": ticker, "status": "current"}
        self.assertEqual(research.update("AAA", contexts["AAA"], DEFAULT_RESEARCH_MODEL)["status"], "current")

    def test_schema_and_nicegui_invalidation_contracts_remain_stable(self) -> None:
        with sqlite3.connect(self.portfolio_db) as connection:
            holdings_schema = connection.execute("PRAGMA table_info(holdings)").fetchall()
        with sqlite3.connect(self.history_db) as connection:
            history_schema = connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        created = self.holdings.add(holding())
        self.holdings.update_contributions(created["_holding_id"], {"regular_biweekly_contribution": 40})
        import_validated_history([statement()], self.history_db)
        with sqlite3.connect(self.portfolio_db) as connection:
            self.assertEqual(connection.execute("PRAGMA table_info(holdings)").fetchall(), holdings_schema)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall(), [])
        with sqlite3.connect(self.history_db) as connection:
            self.assertEqual(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), history_schema)
        source = (Path(__file__).parents[1] / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        holding_refresh = source.split("async def refresh_after_holding_write", 1)[1].split("async def refresh_history_after_statement_write", 1)[0]
        for area in ("Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "Supplemental Research"):
            self.assertIn(area, holding_refresh)
        history_refresh = source.split("async def refresh_history_after_statement_write", 1)[1].split("refresh_button.on_click", 1)[0]
        self.assertIn("render_history(history_range)", history_refresh)
        self.assertIn('loaded["AI Analysis"] = False', history_refresh)
        self.assertNotIn("current_market_data", history_refresh)
        self.assertNotIn("coordinator.refresh", history_refresh)
        self.assertEqual(source.count('ui.button("Refresh Data"'), 1)
        self.assertIn('PORT = int(os.environ.get("PORTFOLIO_TRACKER_PORT", "8501"))', source)


if __name__ == "__main__":
    unittest.main()

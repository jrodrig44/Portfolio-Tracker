import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from stash_history import build_statement_history_reconciliation, import_stash_statement_history


class StashStatementHistoryTests(unittest.TestCase):
    def test_import_stash_statement_history_persists_statement_holdings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "statement-2026-07.pdf").write_bytes(b"stub")
            (root / "statement-2026-08.pdf").write_bytes(b"stub")
            db_path = root / "history.sqlite"

            result = import_stash_statement_history(root, db_path=db_path)

            self.assertEqual(result["import_count"], 2)
            self.assertEqual(result["holding_count"], 4)
            self.assertTrue(db_path.exists())

            with closing(sqlite3.connect(db_path)) as conn:
                imports = conn.execute("SELECT statement_date, source_name FROM statement_history_imports ORDER BY statement_date").fetchall()
                holdings = conn.execute("SELECT ticker, shares, market_value FROM statement_history_holdings ORDER BY ticker").fetchall()
                conn.commit()

            self.assertEqual(imports[0][1], "statement-2026-07.pdf")
            self.assertEqual(imports[1][1], "statement-2026-08.pdf")
            self.assertEqual(holdings[0][0], "AAPL")
            self.assertAlmostEqual(holdings[0][1], 10.0)
            self.assertAlmostEqual(holdings[0][2], 1000.0)

    def test_reconciliation_excludes_hd_and_normalizes_jepi(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "statement-2026-07.pdf").write_bytes(b"stub")
            db_path = root / "history.sqlite"
            import_stash_statement_history(root, db_path=db_path)

            current_holdings = pd.DataFrame(
                [
                    {"ticker": "HD", "shares": 4.0, "market_value": 400.0},
                    {"ticker": "JEPI", "shares": 6.0, "market_value": 600.0},
                ]
            )

            rows = build_statement_history_reconciliation(current_holdings, db_path=db_path)
            self.assertGreaterEqual(len(rows), 3)
            self.assertTrue(any(row["ticker"] == "JEPI" and row["excluded"] for row in rows))
            self.assertTrue(any(row["ticker"] == "HD" and row["excluded"] for row in rows))
            self.assertTrue(any(row["ticker"] == "JEPI" and row["current_shares"] == 6.0 for row in rows))

    def test_reconciliation_handles_holdings_without_share_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "statement-2026-07.pdf").write_bytes(b"stub")
            db_path = root / "history.sqlite"
            import_stash_statement_history(root, db_path=db_path)

            current_holdings = pd.DataFrame([{"ticker": "AAPL"}, {"ticker": "MSFT"}])

            rows = build_statement_history_reconciliation(current_holdings, db_path=db_path)
            self.assertTrue(any(row["ticker"] == "AAPL" for row in rows))
            self.assertTrue(any(row["ticker"] == "MSFT" for row in rows))
            self.assertTrue(any(row["ticker"] == "AAPL" and row["current_shares"] == 0.0 for row in rows))

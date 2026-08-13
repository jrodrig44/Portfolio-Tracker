from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from supplemental_research import get_research_rows, initialize_database
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


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app


class StreamlitRollbackCompatibilityTests(unittest.TestCase):
    def test_holdings_edit_preserves_newer_unowned_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "portfolio.db"
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE holdings (
                            ticker TEXT,
                            shares REAL,
                            amount_invested REAL,
                            nicegui_metadata TEXT,
                            reconciliation_version INTEGER
                        )
                        """
                    )
                    connection.execute(
                        "INSERT INTO holdings VALUES (?, ?, ?, ?, ?)",
                        ("KO", 10.0, 500.0, "keep-this", 7),
                    )

            edited = pd.DataFrame(
                [{"ticker": "KO", "shares": 12.0, "amount_invested": 500.0}]
            )
            with (
                patch.object(app, "DEFAULT_DB_PATH", database),
                patch.object(app, "SupplementalResearchService") as research_service,
            ):
                app._save_holdings(edited)

            with closing(sqlite3.connect(database)) as connection:
                saved = connection.execute(
                    "SELECT ticker, shares, amount_invested, nicegui_metadata, reconciliation_version "
                    "FROM holdings"
                ).fetchone()

            self.assertEqual(saved, ("KO", 12.0, 500.0, "keep-this", 7))
            research_service.return_value.sync_holdings.assert_called_once_with(["KO"])


if __name__ == "__main__":
    unittest.main()

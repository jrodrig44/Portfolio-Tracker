import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
from stash_history import build_statement_history_reconciliation, import_stash_statement_history


class StashStatementHistoryTests(unittest.TestCase):
    def test_placeholder_importer_is_disabled_and_does_not_read_or_create_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "statement-2026-07.pdf").write_bytes(b"stub")
            db_path = root / "history.sqlite"

            result = import_stash_statement_history(root, db_path=db_path)

            self.assertEqual(result, {"import_count": 0, "holding_count": 0, "status": "disabled"})
            self.assertFalse(db_path.exists())

    def test_statements_directory_is_project_relative_and_created(self) -> None:
        self.assertEqual(app.STATEMENTS_DIR, app.PROJECT_ROOT / "Statements")
        self.assertTrue(app.STATEMENTS_DIR.is_dir())
        self.assertEqual(app.DATA_DIR, app.PROJECT_ROOT / "data")

    def test_missing_or_empty_statements_folder_does_not_break_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_statements_dir = Path(temp_dir) / "Statements"
            with patch.object(app, "STATEMENTS_DIR", missing_statements_dir):
                self.assertEqual(app._local_statement_status(), (0, (app.DATA_DIR / "stash_statement_history.sqlite").exists()))

            missing_statements_dir.mkdir()
            with patch.object(app, "STATEMENTS_DIR", missing_statements_dir):
                self.assertEqual(app._local_statement_status(), (0, (app.DATA_DIR / "stash_statement_history.sqlite").exists()))

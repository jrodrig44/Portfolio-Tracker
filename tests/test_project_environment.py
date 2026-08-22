from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_environment import ai_provider_status, initialize_project_environment


ROOT = Path(__file__).resolve().parents[1]


class ProjectEnvironmentTests(unittest.TestCase):
    def tearDown(self) -> None:
        initialize_project_environment.cache_clear()

    def test_project_root_dotenv_loads_independently_of_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory, tempfile.TemporaryDirectory() as other_directory:
            project_root = Path(project_directory)
            (project_root / ".env").write_text(
                "ONEMIN_AI_API_KEY=fake-test-key\nONEMIN_AI_MODEL=fake-test-model\n",
                encoding="utf-8",
            )
            initialize_project_environment.cache_clear()
            original = Path.cwd()
            with patch.dict(os.environ, {}, clear=True):
                os.chdir(other_directory)
                try:
                    state = initialize_project_environment(project_root)
                finally:
                    os.chdir(original)
                self.assertTrue(state.dotenv_exists)
                self.assertTrue(state.dotenv_loaded)
                self.assertEqual(os.environ["ONEMIN_AI_API_KEY"], "fake-test-key")
                self.assertEqual(ai_provider_status(state)["status"], "Ready")

    def test_missing_key_is_safe_and_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            initialize_project_environment.cache_clear()
            state = initialize_project_environment(Path(directory))
            status = ai_provider_status(state)
        self.assertEqual(status["api_configuration"], "Not configured")
        self.assertEqual(status["status"], "Unavailable")

    def test_status_never_exposes_api_key(self) -> None:
        secret = "fake-secret-that-must-not-appear"
        with patch.dict(os.environ, {"ONEMIN_AI_API_KEY": secret, "ONEMIN_AI_MODEL": "safe-model"}, clear=True):
            status = ai_provider_status()
        self.assertNotIn(secret, repr(status))
        self.assertEqual(status["api_configuration"], "Configured")
        self.assertEqual(status["model"], "safe-model")

    def test_environment_initialization_is_centralized(self) -> None:
        helper = (ROOT / "project_environment.py").read_text(encoding="utf-8")
        streamlit = (ROOT / "app.py").read_text(encoding="utf-8")
        nicegui = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertEqual(helper.count("load_dotenv("), 1)
        self.assertNotIn("load_dotenv", streamlit)
        self.assertNotIn("load_dotenv", nicegui)
        self.assertIn("initialize_project_environment(PROJECT_ROOT)", streamlit)
        self.assertIn("initialize_project_environment(PROJECT_ROOT)", nicegui)

    def test_local_environment_files_remain_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore.splitlines())


if __name__ == "__main__":
    unittest.main()

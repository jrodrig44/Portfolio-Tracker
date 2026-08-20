import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NiceGuiStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.batch = (ROOT / "Start NiceGUI Portfolio Tracker.bat").read_text(encoding="utf-8")
        cls.script = (ROOT / "run_nicegui.ps1").read_text(encoding="utf-8")
        cls.main = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")

    def test_batch_uses_only_nicegui_powershell_launcher(self) -> None:
        self.assertIn("run_nicegui.ps1", self.batch)
        for forbidden in ("run.ps1", "start_streamlit.py", "app.py"):
            self.assertNotIn(forbidden, self.batch)

    def test_script_launches_only_nicegui_entrypoint(self) -> None:
        self.assertIn('"nicegui_app\\main.py"', self.script)
        for forbidden in ("start_streamlit.py", "supplemental_research_worker.py"):
            self.assertNotIn(forbidden, self.script)

    def test_shared_environment_and_executable_health_probe_are_used(self) -> None:
        self.assertIn('Join-Path $projectRoot ".venv"', self.script)
        self.assertIn("function Test-VenvPython", self.script)
        self.assertRegex(self.script, r'& \$PythonPath -c "import sys;')
        self.assertIn("Test-VenvPython -PythonPath $venvPython", self.script)
        self.assertIn("Remove-Item -LiteralPath $resolvedVenv -Recurse -Force", self.script)
        self.assertIn("& py -3 -m venv $venvPath", self.script)

    def test_dependency_fingerprint_is_compared_and_written_after_pip_success(self) -> None:
        self.assertIn('Join-Path $runtimeDataPath ".requirements.sha256"', self.script)
        self.assertIn("Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256", self.script)
        self.assertIn("$installedHash -ne $requirementsHash", self.script)
        pip_position = self.script.index("& $venvPython -m pip install -r $requirementsPath")
        failure_position = self.script.index("if ($pipExitCode -ne 0)", pip_position)
        fingerprint_write_position = self.script.index("Set-Content -LiteralPath $requirementsFingerprintPath", failure_position)
        self.assertLess(pip_position, failure_position)
        self.assertLess(failure_position, fingerprint_write_position)

    def test_nicegui_network_configuration_remains_isolated(self) -> None:
        self.assertIn('HOST = "0.0.0.0"', self.main)
        self.assertIn("PORT = 8502", self.main)

    def test_production_startup_chain_still_targets_streamlit(self) -> None:
        production_batch = (ROOT / "Start Portfolio Tracker.bat").read_text(encoding="utf-8")
        production_script = (ROOT / "run.ps1").read_text(encoding="utf-8")
        self.assertIn("run.ps1", production_batch)
        self.assertIn("start_streamlit.py", production_script)
        self.assertNotIn("run_nicegui.ps1", production_batch + production_script)


if __name__ == "__main__":
    unittest.main()

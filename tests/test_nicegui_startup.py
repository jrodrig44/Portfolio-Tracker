import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nicegui_app import research_worker_lifecycle


ROOT = Path(__file__).resolve().parents[1]


class NiceGuiStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.batch = (ROOT / "Start Portfolio Tracker.bat").read_text(encoding="utf-8")
        cls.rollback_batch = (ROOT / "Start Portfolio Tracker - Streamlit Rollback.bat").read_text(encoding="utf-8")
        cls.rollback_script = (ROOT / "run.ps1").read_text(encoding="utf-8")
        cls.script = (ROOT / "run_nicegui.ps1").read_text(encoding="utf-8")
        cls.main = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        cls.lifecycle = (ROOT / "nicegui_app" / "process_lifecycle.py").read_text(encoding="utf-8")

    def test_batch_uses_only_nicegui_powershell_launcher(self) -> None:
        self.assertIn("run_nicegui.ps1", self.batch)
        self.assertNotIn("-NoExit", self.batch)
        self.assertIn("powershell -NoProfile -ExecutionPolicy Bypass -File", self.batch)
        self.assertNotIn("Start-Process", self.batch)
        for forbidden in ("run.ps1", "start_streamlit.py", "app.py"):
            self.assertNotIn(forbidden, self.batch)

    def test_script_launches_only_nicegui_entrypoint(self) -> None:
        self.assertIn('"nicegui_app\\main.py"', self.script)
        for forbidden in ("start_streamlit.py", "supplemental_research_worker.py"):
            self.assertNotIn(forbidden, self.script)

    def test_machine_local_environment_and_executable_health_probe_are_used(self) -> None:
        self.assertIn('Join-Path $localAppData "PortfolioTracker\\venvs"', self.script)
        self.assertIn('$venvPath = Join-Path $venvRoot $environmentName', self.script)
        self.assertIn("SHA256]::Create()", self.script)
        self.assertIn('$venvPath = Join-Path $venvRoot $environmentName', self.rollback_script)
        self.assertIn("SHA256]::Create()", self.rollback_script)
        self.assertIn("function Test-PythonCommand", self.script)
        self.assertRegex(self.script, r'& \$PythonPath @PrefixArguments -c "import sys;')
        self.assertIn("Test-PythonCommand -PythonPath $venvPython", self.script)
        self.assertIn("Remove-Item -LiteralPath $resolvedVenv -Recurse -Force", self.script)
        self.assertIn("& $basePython.Path @($basePython.Arguments) -m venv $venvPath", self.script)

    def test_missing_python_is_installed_for_the_current_user_and_rediscovered(self) -> None:
        install_position = self.script.index("--id Python.Python.3.12")
        rediscovery_position = self.script.index(
            "$basePython = Find-BasePython -VirtualEnvironmentPath $venvPath",
            install_position,
        )
        self.assertIn("Get-Command winget.exe", self.script)
        self.assertIn("--scope user --silent", self.script)
        self.assertIn("--accept-package-agreements --accept-source-agreements", self.script)
        self.assertLess(install_position, rediscovery_position)
        self.assertIn("Python installation through winget failed", self.script)
        self.assertIn("Windows Package Manager (winget) is unavailable", self.script)

    def test_base_python_discovery_does_not_require_py_launcher(self) -> None:
        self.assertIn("function Find-BasePython", self.script)
        cfg_position = self.script.index("$homeLine = $configuration")
        registry_position = self.script.index("HKCU:\\Software\\Python\\PythonCore")
        python_position = self.script.index("foreach ($commandName in @('python.exe', 'python3.exe'))")
        py_position = self.script.index("Get-Command py.exe")
        self.assertLess(cfg_position, registry_position)
        self.assertLess(registry_position, python_position)
        self.assertLess(python_position, py_position)
        self.assertIn("StartsWith($resolvedVenvPrefix", self.script)
        self.assertIn("Test-BasePythonCommand -PythonPath $candidate.Path", self.script)
        self.assertIn("-m venv --help", self.script)
        self.assertNotIn("& py -3 -m venv", self.script)

    def test_invalid_candidates_are_skipped_and_missing_python_error_is_actionable(self) -> None:
        self.assertIn("Rejected $($candidate.Source)", self.script)
        self.assertIn("No usable Python 3 interpreter with venv support", self.script)
        self.assertIn("Candidates: $candidateSummary", self.script)

    def test_repair_is_scoped_and_partial_environment_is_removed_on_failure(self) -> None:
        self.assertIn("$resolvedVenv.Equals($expectedVenv", self.script)
        self.assertIn("Refusing to remove an unexpected virtual-environment path", self.script)
        self.assertGreaterEqual(self.script.count("Remove-Item -LiteralPath $venvPath -Recurse -Force"), 1)
        self.assertIn("partial .venv could not be removed", self.script)

    def test_healthy_environment_is_reused_and_recreated_environment_forces_install(self) -> None:
        self.assertIn("if (-not (Test-PythonCommand -PythonPath $venvPython))", self.script)
        self.assertIn("$dependenciesNeedInstall = $environmentCreated -or", self.script)
        self.assertIn('Write-Host "Dependencies unchanged; skipping installation."', self.script)

    def test_pip_and_required_runtime_imports_are_validated(self) -> None:
        self.assertIn("$venvPython -m pip --version", self.script)
        self.assertIn("$venvPython -m ensurepip --upgrade", self.script)
        self.assertIn("import nicegui, pandas, yfinance, exchange_calendars", self.script)
        validation_position = self.script.index("Dependency validation failed after installation")
        fingerprint_position = self.script.index("Set-Content -LiteralPath $requirementsFingerprintPath")
        self.assertLess(validation_position, fingerprint_position)

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
        self.assertIn('PORT = int(os.environ.get("PORTFOLIO_TRACKER_PORT", "8501"))', self.main)

    def test_launcher_rejects_duplicate_port_and_tracks_its_child_lifecycle(self) -> None:
        self.assertIn("$productionPort = 8501", self.script)
        self.assertIn("Get-NetTCPConnection -LocalPort $productionPort", self.script)
        self.assertIn("Port $productionPort is already in use", self.script)
        self.assertIn("Invoke-WebRequest -Uri $productionUrl", self.script)
        self.assertIn("NiceGUI ready to go on $productionUrl", self.script)
        self.assertIn("NICEGUI_LAUNCHER_PID", self.script)
        self.assertIn("Start-Process -FilePath $venvPython", self.script)
        self.assertIn("-PassThru", self.script)
        self.assertIn("$child.Id", self.script)

    def test_main_starts_launcher_watchdog_and_watchdog_exits_with_launcher(self) -> None:
        self.assertIn("start_launcher_watchdog()", self.main)
        self.assertIn('os.environ.get("NICEGUI_LAUNCHER_PID"', self.lifecycle)
        self.assertIn("WaitForSingleObject", self.lifecycle)
        self.assertIn("os._exit(0)", self.lifecycle)
        self.assertIn("daemon=True", self.lifecycle)

    def test_nicegui_launch_chain_has_no_detachment_or_restart_path(self) -> None:
        launch_chain = self.batch + self.script + self.lifecycle + self.main
        for forbidden in ("schtasks", "New-Service", "Startup\\", "while ($true)"):
            self.assertNotIn(forbidden, launch_chain)
        self.assertEqual(self.batch.count("run_nicegui.ps1"), 1)
        self.assertEqual(self.script.count("Start-Process -FilePath $venvPython"), 1)

    def test_launcher_has_bounded_exact_pid_shutdown(self) -> None:
        self.assertIn("$gracefulShutdownTimeoutSeconds = 15", self.script)
        self.assertIn("NICEGUI_SHUTDOWN_REQUEST_FILE", self.script)
        self.assertIn("[Console]::TreatControlCAsInput = $true", self.script)
        self.assertIn("taskkill.exe /PID $child.Id /T /F", self.script)
        self.assertNotIn("taskkill /IM", self.script)
        self.assertIn("Portfolio Tracker stopped safely.", self.script)
        self.assertIn("Portfolio Tracker was force-stopped.", self.script)
        self.assertIn("exit 2", self.script)

    def test_intentional_and_failed_exits_remain_distinct(self) -> None:
        self.assertIn("$intentionalShutdown = $true", self.script)
        self.assertIn("-not $intentionalShutdown -and $child.ExitCode -ne 0", self.script)
        self.assertIn('throw "NiceGUI exited with code $($child.ExitCode)."', self.script)

    def test_production_startup_chain_targets_only_nicegui(self) -> None:
        self.assertIn("run_nicegui.ps1", self.batch)
        self.assertNotIn("run.ps1", self.batch.replace("run_nicegui.ps1", ""))
        self.assertNotIn("start_streamlit.py", self.batch + self.script)
        self.assertFalse((ROOT / "Start NiceGUI Portfolio Tracker.bat").exists())

    def test_explicit_rollback_launcher_targets_streamlit_on_alternate_port(self) -> None:
        self.assertIn("temporary Streamlit rollback", self.rollback_batch)
        self.assertIn("run.ps1", self.rollback_batch)
        self.assertNotIn("run_nicegui.ps1", self.rollback_batch + self.rollback_script)
        self.assertIn("start_streamlit.py", self.rollback_script)
        self.assertIn("$rollbackPort = 8502", self.rollback_script)
        self.assertIn("STREAMLIT_SERVER_PORT", self.rollback_script)
        self.assertIn("Get-NetTCPConnection -LocalPort $rollbackPort", self.rollback_script)

    def test_production_starts_one_tracked_research_worker_child(self) -> None:
        worker_lifecycle = (ROOT / "nicegui_app" / "research_worker_lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("app.on_startup(start_research_worker)", self.main)
        self.assertIn("app.on_shutdown(stop_research_worker)", self.main)
        self.assertEqual(worker_lifecycle.count("subprocess.Popen("), 1)
        self.assertIn("worker.terminate()", worker_lifecycle)
        self.assertIn("worker.wait(timeout=10)", worker_lifecycle)

    def test_research_worker_start_is_idempotent_and_shutdown_targets_tracked_child(self) -> None:
        worker = Mock()
        worker.poll.return_value = None
        research_worker_lifecycle._worker = None
        try:
            with patch("nicegui_app.research_worker_lifecycle.subprocess.Popen", return_value=worker) as popen:
                research_worker_lifecycle.start_research_worker()
                research_worker_lifecycle.start_research_worker()
                popen.assert_called_once()
                research_worker_lifecycle.stop_research_worker()
            worker.terminate.assert_called_once_with()
            worker.wait.assert_called_once_with(timeout=10)
        finally:
            research_worker_lifecycle._worker = None


if __name__ == "__main__":
    unittest.main()

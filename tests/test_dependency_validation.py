from __future__ import annotations

import tempfile
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from importlib.metadata import PackageNotFoundError

from dependency_validation import validate_dependencies


class DependencyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requirements = self.root / "requirements.txt"
        self.lock = self.root / "requirements-lock.txt"
        self.requirements.write_text("pandas>=2.2.2,<3.0\n", encoding="utf-8")
        self.lock.write_text("pandas==2.3.3\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self, installed_version: str = "2.3.3", pip_check: CompletedProcess[str] | None = None):
        with (
            patch("dependency_validation.version", return_value=installed_version),
            patch("dependency_validation.importlib.util.find_spec", return_value=ModuleSpec("pandas", None)),
            patch(
                "dependency_validation.subprocess.run",
                return_value=pip_check or CompletedProcess([], 0, "", ""),
            ),
        ):
            return validate_dependencies(self.requirements, self.lock)

    def test_matching_declared_and_locked_versions_pass(self) -> None:
        self.assertTrue(self.validate().valid)

    def test_missing_package_is_reported_for_repair(self) -> None:
        with (
            patch("dependency_validation.version", side_effect=PackageNotFoundError),
            patch("dependency_validation.importlib.util.find_spec", return_value=None),
            patch("dependency_validation.subprocess.run", return_value=CompletedProcess([], 0, "", "")),
        ):
            result = validate_dependencies(self.requirements, self.lock)
        self.assertFalse(result.valid)
        self.assertTrue(any("not installed" in error for error in result.errors))

    def test_incompatible_installed_and_locked_versions_fail(self) -> None:
        result = self.validate("3.1.0")
        self.assertFalse(result.valid)
        self.assertTrue(any("does not satisfy" in error for error in result.errors))
        self.assertTrue(any("does not match lock" in error for error in result.errors))

    def test_dependency_conflicts_are_reported(self) -> None:
        conflict = CompletedProcess([], 1, "", "sample-package requires a different version")
        result = self.validate(pip_check=conflict)
        self.assertFalse(result.valid)
        self.assertTrue(any("pip check found dependency conflicts" in error for error in result.errors))

    def test_lock_must_satisfy_declared_requirement(self) -> None:
        self.lock.write_text("pandas==3.1.0\n", encoding="utf-8")
        result = self.validate("3.1.0")
        self.assertFalse(result.valid)
        self.assertTrue(any("violates declared requirement" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()

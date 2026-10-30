from __future__ import annotations

import argparse
import importlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


IMPORT_NAMES = {
    "exchange-calendars": "exchange_calendars",
    "openpyxl": "openpyxl",
    "pdfplumber": "pdfplumber",
    "python-dotenv": "dotenv",
    "pypdf": "pypdf",
}
RUNTIME_IMPORTS = ("nicegui", "pandas", "yfinance", "exchange_calendars")


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def _read_requirements(path: Path) -> tuple[list[Requirement], list[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"Cannot read {path}: {exc}"]

    requirements: list[Requirement] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            requirements.append(Requirement(line))
        except InvalidRequirement as exc:
            errors.append(f"{path.name}:{line_number} is not a valid requirement: {exc}")
    return requirements, errors


def _read_lock(path: Path) -> tuple[dict[str, Requirement], list[str]]:
    requirements, errors = _read_requirements(path)
    locked: dict[str, Requirement] = {}
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        pins = list(requirement.specifier)
        if requirement.url or requirement.marker or requirement.extras or len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            errors.append(f"{path.name} must contain exact package pins; invalid entry: {requirement}")
            continue
        if name in locked:
            errors.append(f"{path.name} pins {requirement.name} more than once")
            continue
        locked[name] = requirement
    return locked, errors


def validate_dependencies(requirements_path: Path, lock_path: Path) -> ValidationResult:
    requirements, errors = _read_requirements(requirements_path)
    locked, lock_errors = _read_lock(lock_path)
    errors.extend(lock_errors)

    for requirement in requirements:
        if requirement.marker and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        locked_requirement = locked.get(name)
        if locked_requirement is None:
            errors.append(f"{requirement.name} is declared in {requirements_path.name} but not pinned in {lock_path.name}")
        elif not requirement.specifier.contains(
            next(iter(locked_requirement.specifier)).version,
            prereleases=True,
        ):
            errors.append(
                f"{lock_path.name} pins {locked_requirement.name}{locked_requirement.specifier}, "
                f"which violates declared requirement {requirement}"
            )

        try:
            installed_version = version(requirement.name)
        except PackageNotFoundError:
            errors.append(f"Required package {requirement.name} is not installed")
            continue
        if not requirement.specifier.contains(installed_version, prereleases=True):
            errors.append(
                f"Installed {requirement.name} {installed_version} does not satisfy {requirement.specifier}"
            )

        import_name = IMPORT_NAMES.get(name, name.replace("-", "_"))
        if importlib.util.find_spec(import_name) is None:
            errors.append(f"Required import {import_name} for {requirement.name} is unavailable")

    for name, requirement in locked.items():
        try:
            installed_version = version(requirement.name)
        except PackageNotFoundError:
            errors.append(f"Locked package {requirement.name} is not installed")
            continue
        locked_version = next(iter(requirement.specifier)).version
        if installed_version != locked_version:
            errors.append(f"Installed {requirement.name} {installed_version} does not match lock {locked_version}")

    for import_name in RUNTIME_IMPORTS:
        try:
            importlib.import_module(import_name)
        except (ImportError, OSError) as exc:
            errors.append(f"Required runtime import {import_name} failed: {exc}")

    try:
        check = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        errors.append(f"Unable to run pip check: {exc}")
    else:
        if check.returncode:
            details = (check.stdout + check.stderr).strip()
            errors.append(f"pip check found dependency conflicts: {details}")

    return ValidationResult(tuple(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate installed Portfolio Tracker dependencies against its lock.")
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    arguments = parser.parse_args()

    result = validate_dependencies(arguments.requirements, arguments.lock)
    if not result.valid:
        for error in result.errors:
            print(f"Dependency validation failed: {error}", file=sys.stderr)
        return 1
    print("All declared and locked dependencies are installed and conflict-free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

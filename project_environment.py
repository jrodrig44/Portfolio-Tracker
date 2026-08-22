from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class EnvironmentInitialization:
    dotenv_exists: bool
    dotenv_loaded: bool


@lru_cache(maxsize=None)
def initialize_project_environment(project_root: str | Path = PROJECT_ROOT) -> EnvironmentInitialization:
    """Load the project's local environment once from a deterministic path."""
    root = Path(project_root).resolve()
    dotenv_path = root / ".env"
    exists = dotenv_path.is_file()
    loaded = bool(load_dotenv(dotenv_path=dotenv_path, override=False)) if exists else False
    return EnvironmentInitialization(dotenv_exists=exists, dotenv_loaded=loaded)


def ai_provider_status(environment: EnvironmentInitialization | None = None) -> dict[str, Any]:
    """Return secret-free 1min.AI readiness information for diagnostics."""
    from onemin_ai_client import ONEMIN_AI_DEFAULT_MODEL

    state = environment or initialize_project_environment()
    configured = bool(str(os.environ.get("ONEMIN_AI_API_KEY", "")).strip())
    model = str(os.environ.get("ONEMIN_AI_MODEL", "")).strip() or ONEMIN_AI_DEFAULT_MODEL
    return {
        "provider": "1min.AI",
        "api_configuration": "Configured" if configured else "Not configured",
        "status": "Ready" if configured else "Unavailable",
        "model": model,
        "configuration_source": "Local environment loaded" if state.dotenv_exists else "Process environment",
    }

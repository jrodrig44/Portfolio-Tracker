from __future__ import annotations

from typing import Any

from portfolio_history import build_history_package


def history_package(
    history: list[dict[str, Any]], selected_range: str = "ALL"
) -> dict[str, Any]:
    """Thin NiceGUI adapter over the shared production history calculations."""
    return build_history_package(history, selected_range)

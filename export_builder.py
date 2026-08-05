from __future__ import annotations

from app import generate_export as _generate_export


def generate_export(export_payload: dict[str, object]) -> str:
    """Build deterministic markdown export from the precomputed payload."""
    return _generate_export(export_payload)

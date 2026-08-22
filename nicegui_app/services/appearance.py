from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


APPEARANCE_STORAGE_KEY = "appearance_mode"
APPEARANCE_OPTIONS = {None: "System", False: "Light", True: "Dark"}


def restore_appearance(storage: MutableMapping[str, Any]) -> bool | None:
    """Return the persisted NiceGUI dark-mode value, defaulting safely to System."""
    value = storage.get(APPEARANCE_STORAGE_KEY)
    if value is not None and type(value) is not bool:
        value = None
    storage[APPEARANCE_STORAGE_KEY] = value
    return value

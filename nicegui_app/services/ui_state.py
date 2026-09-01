"""Ephemeral per-page presentation state; never persisted to application data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Callable


METRICS_DERIVED_VIEWS = (
    "Holdings",
    "Portfolio Analysis",
    "Portfolio Insights",
    "Portfolio Health",
)


def canonical_navigation(value: object) -> str:
    """Return the canonical area name represented by a NiceGUI tab value.

    NiceGUI value-change events can contain either the configured string name or
    a ``ui.tab`` object, depending on framework version and binding path.
    ``str(tab)`` is a debug representation, not a stable navigation identifier.
    Keeping this conversion at the boundary prevents an active Holdings page
    from being mistaken for an inactive view and replaced by the lazy-refresh
    placeholder immediately after Save.
    """
    candidate: object = value
    if not isinstance(candidate, str):
        props = getattr(candidate, "props", None) or getattr(candidate, "_props", None)
        if props is not None:
            try:
                candidate = props["name"]
            except (KeyError, TypeError):
                pass
    if not isinstance(candidate, str) or candidate not in NAVIGATION_AREAS:
        raise ValueError(f"Unknown navigation value: {value!r}")
    return candidate


NAVIGATION_AREAS = (
    "Holdings",
    "Portfolio Analysis",
    "AI Analysis",
    "Portfolio Insights",
    "Portfolio Health",
    "History",
    "Statement History",
    "Supplemental Research",
    "System",
)


async def invalidate_section_views(
    names: Iterable[str],
    selected_navigation: object,
    loaded: dict[str, bool],
    refresh_section: Callable[[str], Any],
    replace_with_placeholder: Callable[[str], Any],
) -> None:
    """Rebuild the visible stale section and lazily invalidate hidden sections.

    The active view must be refreshed in place; only hidden derived views receive
    the "refresh when opened" placeholder. Deduplicating ``names`` also prevents
    one data change from creating duplicate render blocks.
    """
    active = canonical_navigation(selected_navigation)
    stale_names = tuple(dict.fromkeys(names))
    for name in stale_names:
        loaded[name] = False
    for name in stale_names:
        action = refresh_section(name) if name == active else replace_with_placeholder(name)
        if isawaitable(action):
            await action


def refresh_view_plan(selected_navigation: object, *, metrics_changed: bool, history_changed: bool) -> dict[str, tuple[str, ...]]:
    """Identify stale views while preserving the active tab and its local state.

    This is a server-data refresh plan, not a browser reload or navigation. In
    particular, History's selected range remains in ``PageUIState`` while its
    data is rebuilt.
    """
    active_navigation = canonical_navigation(selected_navigation)
    stale = list(METRICS_DERIVED_VIEWS if metrics_changed else ())
    if history_changed:
        stale.append("History")
    return {
        "stale": tuple(stale),
        "active": tuple(name for name in stale if name == active_navigation),
        "inactive": tuple(name for name in stale if name != active_navigation),
    }


@dataclass
class PageUIState:
    selected_navigation: str = "Holdings"
    history_range: str = "ALL"
    holdings_contributions_expanded: bool = True
    holdings_management_expanded: bool = False

    def expansion_value(self, name: str) -> bool:
        if name == "contributions":
            return self.holdings_contributions_expanded
        if name == "management":
            return self.holdings_management_expanded
        raise KeyError(name)

    def set_expansion(self, name: str, value: object) -> None:
        if name == "contributions":
            self.holdings_contributions_expanded = bool(value)
        elif name == "management":
            self.holdings_management_expanded = bool(value)
        else:
            raise KeyError(name)


__all__ = [
    "METRICS_DERIVED_VIEWS",
    "NAVIGATION_AREAS",
    "PageUIState",
    "canonical_navigation",
    "invalidate_section_views",
    "refresh_view_plan",
]

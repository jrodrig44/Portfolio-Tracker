"""Ephemeral per-page presentation state; never persisted to application data."""

from __future__ import annotations

from dataclasses import dataclass


METRICS_DERIVED_VIEWS = (
    "Holdings",
    "Portfolio Analysis",
    "Portfolio Insights",
    "Portfolio Health",
)


def refresh_view_plan(selected_navigation: str, *, metrics_changed: bool, history_changed: bool) -> dict[str, tuple[str, ...]]:
    """Identify stale views while preserving the currently visible DOM."""
    stale = list(METRICS_DERIVED_VIEWS if metrics_changed else ())
    if history_changed:
        stale.append("History")
    return {
        "stale": tuple(stale),
        "active": tuple(name for name in stale if name == selected_navigation),
        "inactive": tuple(name for name in stale if name != selected_navigation),
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


__all__ = ["METRICS_DERIVED_VIEWS", "PageUIState", "refresh_view_plan"]

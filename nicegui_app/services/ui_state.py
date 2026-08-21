"""Ephemeral per-page presentation state; never persisted to application data."""

from __future__ import annotations

from dataclasses import dataclass


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


__all__ = ["PageUIState"]

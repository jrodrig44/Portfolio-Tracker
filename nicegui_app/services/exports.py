from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import pandas as pd

from portfolio_export import build_export_package, build_export_payload as shared_build_export_payload


def yahoo_export_enrichment(
    yahoo_package: dict[str, Any] | None,
    supplemental_research: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Map already-loaded optional enrichment into the shared export contract.

    Export preparation is strict about surprise network work: absent cached
    Yahoo data is reported rather than fetched. Supplemental Research remains
    distinguishable from Yahoo financial and market data.
    """
    research = list(supplemental_research or [])
    if yahoo_package is None:
        return {}, {
            "supplemental_investment_intelligence": research,
            "historical_security_context": [],
            "dividend_seasonality": {},
            "yahoo_metadata": [],
            "investor_profile": {},
            "recommendation_constraints": {},
            "notes": ["Yahoo enrichment has not been loaded for the current portfolio session."],
        }
    yahoo_intelligence = [dict(row) for row in yahoo_package.get("investment_intelligence", []) if isinstance(row, dict)]
    return dict(yahoo_package.get("fund_weights") or {}), {
        "supplemental_investment_intelligence": yahoo_intelligence + research,
        "historical_security_context": [dict(row) for row in yahoo_package.get("historical_context", []) if isinstance(row, dict)],
        "dividend_seasonality": dict(yahoo_package.get("seasonality") or {}),
        "yahoo_metadata": [dict(row) for row in yahoo_package.get("dividend_metadata", []) if isinstance(row, dict)],
        "investor_profile": {},
        "recommendation_constraints": {},
        "notes": [],
    }


def _metrics_signature(metrics: pd.DataFrame) -> tuple[tuple[Any, ...], ...]:
    # Yahoo identity/classification inputs plus the annual-income weight used by
    # dividend seasonality. Live prices and derived market values are excluded.
    columns = ("ticker", "asset_type", "sector", "annual_income")
    records = metrics.to_dict(orient="records") if metrics is not None and not metrics.empty else []
    signature = [tuple(row.get(column) for column in columns) for row in records]
    return tuple(sorted(signature, key=lambda row: str(row[0]).strip().upper()))


@dataclass
class YahooExportEnrichmentState:
    package: dict[str, Any] | None = None
    signature: tuple[tuple[Any, ...], ...] | None = None

    def retain(self, metrics: pd.DataFrame, package: dict[str, Any]) -> None:
        self.signature = _metrics_signature(metrics)
        self.package = package

    def invalidate(self) -> None:
        self.package = None
        self.signature = None

    def invalidate_if_changed(self, metrics: pd.DataFrame) -> None:
        if self.signature is not None and self.signature != _metrics_signature(metrics):
            self.invalidate()

    def available(self, metrics: pd.DataFrame) -> dict[str, Any] | None:
        return self.package if self.signature == _metrics_signature(metrics) else None

    def export_inputs(
        self, metrics: pd.DataFrame, supplemental_research: list[dict[str, Any]] | None = None
    ) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
        return yahoo_export_enrichment(self.available(metrics), supplemental_research)


def build_export_payload(holdings: list[dict[str,Any]], metrics: pd.DataFrame, **kwargs: Any) -> dict[str,Any]:
    """Build the unified current/live payload used by report exports."""
    return shared_build_export_payload(holdings, metrics, **kwargs)


def export_package(holdings: list[dict[str,Any]],metrics: pd.DataFrame, **kwargs: Any)->dict[str,str]:
    """Render comprehensive formats; AI Analysis Markdown remains separate."""
    return build_export_package(holdings, metrics, **kwargs)

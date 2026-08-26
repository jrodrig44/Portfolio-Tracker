from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import pandas as pd

from portfolio_export import build_export_package, build_export_payload as shared_build_export_payload


def yahoo_export_enrichment(
    yahoo_package: dict[str, Any] | None,
    supplemental_research: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Map loaded NiceGUI enrichment into the shared export contract."""
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
        "yahoo_metadata": {
            "source": "Yahoo Finance",
            "data_type": "cached_on_demand_intelligence",
            "method": "Yahoo intelligence explicitly loaded for the current NiceGUI portfolio state",
            "verified": False,
        },
        "investor_profile": {},
        "recommendation_constraints": {},
        "notes": [],
    }


def _metrics_signature(metrics: pd.DataFrame) -> tuple[tuple[Any, ...], ...]:
    columns = ("ticker", "shares", "current_price", "market_value", "annual_income", "asset_type", "sector")
    records = metrics.to_dict(orient="records") if metrics is not None and not metrics.empty else []
    return tuple(tuple(row.get(column) for column in columns) for row in records)


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

    def available(self, metrics: pd.DataFrame) -> dict[str, Any] | None:
        return self.package if self.signature == _metrics_signature(metrics) else None

    def export_inputs(
        self, metrics: pd.DataFrame, supplemental_research: list[dict[str, Any]] | None = None
    ) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
        return yahoo_export_enrichment(self.available(metrics), supplemental_research)


def build_export_payload(holdings: list[dict[str,Any]], metrics: pd.DataFrame, **kwargs: Any) -> dict[str,Any]:
    return shared_build_export_payload(holdings, metrics, **kwargs)


def export_package(holdings: list[dict[str,Any]],metrics: pd.DataFrame, **kwargs: Any)->dict[str,str]:
    return build_export_package(holdings, metrics, **kwargs)

from __future__ import annotations

from typing import Any
import pandas as pd

from portfolio_analysis import build_analysis, contribution_context as shared_contribution_context


def concentration(metrics: pd.DataFrame) -> dict[str, Any]:
    return build_analysis(metrics)["concentration"]


def contribution_context(metrics: pd.DataFrame) -> dict[str, Any]:
    return shared_contribution_context(metrics)


def income_quality_records(metrics: pd.DataFrame) -> list[dict[str, Any]]:
    """Compatibility wrapper around the shared production income-quality path."""
    return build_analysis(metrics)["income_quality"]


def analysis_package(metrics: pd.DataFrame, **context: Any) -> dict[str, Any]:
    return build_analysis(metrics, **context)


def analysis_with_fund_weights(metrics: pd.DataFrame, fund_weights: dict[str,dict[str,float]]) -> dict[str, Any]:
    return build_analysis(metrics, fund_weights=fund_weights)

from __future__ import annotations

from typing import Any
import pandas as pd

from portfolio_insights import build_portfolio_insights


def insights_package(metrics: pd.DataFrame, **context: Any) -> dict[str, Any]:
    return build_portfolio_insights(metrics, **context)

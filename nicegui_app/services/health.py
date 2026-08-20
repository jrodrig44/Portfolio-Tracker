from __future__ import annotations

from typing import Any
import pandas as pd

from portfolio_health import build_portfolio_health


def health_package(metrics: pd.DataFrame, **context: Any) -> dict[str, Any]:
    return build_portfolio_health(metrics, **context)

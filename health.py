from __future__ import annotations

from typing import Any

import pandas as pd

from health_engine import compute_portfolio_health


def compute_health_score(portfolio_df: pd.DataFrame, exposure_data: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for legacy callers that expect the old health payload shape."""
    package = compute_portfolio_health(
        df=portfolio_df,
        exposure=exposure_data,
        income={},
        contributions={},
        espp_context={},
        data_quality={},
    )

    subscores = package.get("subscores", {}) if isinstance(package, dict) else {}
    return {
        "health_score": float(package.get("overall_score", 0.0) or 0.0),
        "subscores": {
            "diversification": float(subscores.get("diversification", 0.0) or 0.0),
            "concentration": float(subscores.get("concentration", 0.0) or 0.0),
            "income_stability": float(subscores.get("income_stability", 0.0) or 0.0),
            "dividend_growth_potential": float(subscores.get("dividend_growth_potential", 0.0) or 0.0),
            "total_return_potential": float(subscores.get("total_return_potential", 0.0) or 0.0),
            "risk": float(subscores.get("risk", 0.0) or 0.0),
            "data_quality": float(subscores.get("data_quality", 0.0) or 0.0),
        },
        "alerts": package.get("alerts", []),
        "notes": package.get("notes", []),
        "strongest_areas": package.get("strongest_areas", []),
        "weakest_areas": package.get("weakest_areas", []),
        "largest_holding": str(package.get("largest_holding", "N/A")),
        "largest_holding_weight": float(package.get("largest_holding_weight_pct", 0.0) or 0.0),
        "largest_income_contributor": str(package.get("largest_income_contributor", "N/A")),
        "largest_income_weight": float(package.get("largest_income_weight_pct", 0.0) or 0.0),
    }

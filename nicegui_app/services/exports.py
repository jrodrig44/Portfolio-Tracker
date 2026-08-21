from __future__ import annotations

from typing import Any
import pandas as pd

from portfolio_export import build_export_package, build_export_payload as shared_build_export_payload


def build_export_payload(holdings: list[dict[str,Any]], metrics: pd.DataFrame, **kwargs: Any) -> dict[str,Any]:
    return shared_build_export_payload(holdings, metrics, **kwargs)


def export_package(holdings: list[dict[str,Any]],metrics: pd.DataFrame, **kwargs: Any)->dict[str,str]:
    return build_export_package(holdings, metrics, **kwargs)

from __future__ import annotations

from app import _resolve_lookthrough_sector_exposure


def resolve_fund_sector_exposure(metrics_df):
    """Compatibility module for fund look-through sector resolution."""
    return _resolve_lookthrough_sector_exposure(metrics_df)

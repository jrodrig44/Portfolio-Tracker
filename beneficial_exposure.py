"""Shared custody-versus-beneficial portfolio exposure calculations."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value in (None, "") else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def beneficial_hd_exposure(metrics: pd.DataFrame, hd_context: dict[str, Any] | None) -> dict[str, Any]:
    """Value external HD without changing the custody-oriented holdings data."""
    frame = metrics if isinstance(metrics, pd.DataFrame) else pd.DataFrame()
    tickers = frame["ticker"] if "ticker" in frame else pd.Series("", index=frame.index, dtype=str)
    hd_rows = frame[tickers.astype(str).str.upper().eq("HD")] if not frame.empty else frame
    custody_shares = _decimal(hd_rows.iloc[0].get("shares")) if not hd_rows.empty else Decimal("0")
    price = _decimal(hd_rows.iloc[0].get("current_price")) if not hd_rows.empty else None
    custody_value = _decimal(hd_rows.iloc[0].get("market_value")) if not hd_rows.empty else Decimal("0")
    custody = ((hd_context or {}).get("reconciliation") or {}).get("current_custody") or {}
    equate = _decimal(custody.get("equate_shares")) or Decimal("0")
    merrill = _decimal(custody.get("merrill_shares"))
    combined = _decimal(custody.get("combined_beneficial_shares"))
    reconciled = bool((hd_context or {}).get("available")) and custody.get("reconciliation_complete", True) and combined is not None
    # Holdings is custody-oriented. External Equate shares supplement portfolio
    # risk/value, while Merrill custody already present in Holdings is not added again.
    external_shares = equate if reconciled and (merrill is None or custody_shares == merrill) else Decimal("0")
    external_value = external_shares * price if price is not None else Decimal("0")
    custody_total = Decimal(str(pd.to_numeric(frame.get("market_value", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))
    return {
        "custody_hd_shares": custody_shares,
        "equate_shares": equate,
        "merrill_shares": merrill if merrill is not None else custody_shares,
        "combined_beneficial_shares": combined if combined is not None else custody_shares,
        "hd_price": price,
        "custody_hd_market_value": custody_value,
        "external_hd_market_value": external_value,
        "combined_hd_market_value": (combined * price) if combined is not None and price is not None else custody_value,
        "custody_portfolio_value": custody_total,
        "total_beneficial_portfolio_value": custody_total + external_value,
        "supplemented": external_shares > 0,
    }


def beneficial_metrics_frame(metrics: pd.DataFrame, hd_context: dict[str, Any] | None) -> pd.DataFrame:
    """Return an analysis-only frame with HD expressed as beneficial exposure."""
    frame = metrics.copy() if isinstance(metrics, pd.DataFrame) else pd.DataFrame()
    exposure = beneficial_hd_exposure(frame, hd_context)
    if frame.empty or not exposure["supplemented"]:
        return frame
    mask = frame["ticker"].astype(str).str.upper().eq("HD")
    if not mask.any():
        return frame
    index = frame.index[mask][0]
    old_shares = float(frame.at[index, "shares"] or 0)
    new_shares = float(exposure["combined_beneficial_shares"])
    frame.at[index, "shares"] = new_shares
    frame.at[index, "market_value"] = float(exposure["combined_hd_market_value"])
    frame.at[index, "current_value"] = float(exposure["combined_hd_market_value"])
    if "annual_income" in frame and old_shares > 0:
        frame.at[index, "annual_income"] = float(frame.at[index, "annual_income"] or 0) * new_shares / old_shares
    # External open-position basis is included only when normalized evidence supplies it.
    external_basis = _decimal(((hd_context or {}).get("open_position") or {}).get("equate_cost_basis"))
    if external_basis is not None and "cost_basis" in frame:
        frame.at[index, "cost_basis"] = float(frame.at[index, "cost_basis"] or 0) + float(external_basis)
    if "unrealized_gain" in frame and "cost_basis" in frame:
        frame.at[index, "unrealized_gain"] = float(frame.at[index, "market_value"] or 0) - float(frame.at[index, "cost_basis"] or 0)
    total = float(pd.to_numeric(frame["market_value"], errors="coerce").fillna(0).sum())
    if "portfolio_weight_pct" in frame:
        frame["portfolio_weight_pct"] = frame["market_value"] / total if total else 0.0
    return frame


__all__ = ["beneficial_hd_exposure", "beneficial_metrics_frame"]

from __future__ import annotations

import math
from typing import Any

import pandas as pd


OPTION_INCOME_ETFS = {"JEPI", "JEPQ", "QYLD", "RYLD", "XYLD"}
TRADITIONAL_DIVIDEND_ETFS = {"SCHD", "VOO", "VTI", "SPY", "IVV"}
HIGH_YIELD_OPTION_ETFS = {"JEPI", "JEPQ", "QYLD", "RYLD", "XYLD"}
REIT_ASSET_TYPES = {"REIT"}
BOND_ASSET_TYPES = {"BOND FUND"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in [None, ""]:
            return default
        numeric_value = float(value)
        return numeric_value if math.isfinite(numeric_value) else default
    except (TypeError, ValueError):
        return default


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, score))


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=["ticker", "asset_type", "sector", "dividend_yield_pct", "market_value", "annual_income"])

    out = df.copy()
    for col in ["ticker", "asset_type", "sector"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str).str.strip()

    out["ticker"] = out["ticker"].str.upper()
    out["asset_type"] = out["asset_type"].str.upper()

    for col in ["dividend_yield_pct", "market_value", "annual_income"]:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    total_value = float(out["market_value"].sum())
    out["weight"] = out["market_value"] / total_value if total_value > 0 else 0.0
    return out


def _average_monthly_weight_variance(month_weights_map: dict[str, list[float]]) -> float:
    if not isinstance(month_weights_map, dict) or not month_weights_map:
        return 0.0

    variances: list[float] = []
    for weights in month_weights_map.values():
        if not isinstance(weights, list) or len(weights) != 12:
            continue
        s = pd.to_numeric(pd.Series(weights), errors="coerce").fillna(0.0)
        if float(s.sum()) <= 0:
            continue
        variances.append(float(s.var(ddof=0)))

    if not variances:
        return 0.0
    return float(sum(variances) / len(variances))


def _is_dividend_trend_negative(row: pd.Series) -> bool:
    if "dividend_trend_negative" in row.index:
        return bool(row.get("dividend_trend_negative", False))
    if "dividend_trend_pct" in row.index:
        return _to_float(row.get("dividend_trend_pct", 0.0)) < 0
    if "dividend_trend" in row.index:
        trend_text = str(row.get("dividend_trend", "")).strip().lower()
        return any(token in trend_text for token in ["down", "declin", "negative", "cut"])
    return False


def _is_volatility_high(row: pd.Series) -> bool:
    if "volatility" not in row.index:
        return False
    vol = _to_float(row.get("volatility", 0.0))
    return vol > 5.0


def _classify_income_type(row: pd.Series, asset_type_map: dict[str, str]) -> str:
    ticker = str(row.get("ticker", "")).strip().upper()
    asset_type = str(row.get("asset_type", "")).strip().upper()
    hinted_asset_type = str(asset_type_map.get(ticker, "")).strip().upper() if isinstance(asset_type_map, dict) else ""
    resolved_asset_type = hinted_asset_type or asset_type
    sector = str(row.get("sector", "")).strip()
    yield_pct = _to_float(row.get("dividend_yield_pct", 0.0))

    if not ticker or (not resolved_asset_type and not sector and yield_pct <= 0):
        return "Unknown"

    if ticker in OPTION_INCOME_ETFS:
        return "Option-Income ETF Distribution"
    if resolved_asset_type in REIT_ASSET_TYPES or "REIT" in resolved_asset_type:
        return "REIT Distribution"
    if resolved_asset_type in BOND_ASSET_TYPES or "BOND" in resolved_asset_type:
        return "Bond Fund Distribution"
    if ticker in TRADITIONAL_DIVIDEND_ETFS:
        return "Traditional Dividend"

    if yield_pct > 10.0:
        return "Variable / Unstable Distribution"

    if resolved_asset_type in {"STOCK", "ETF"}:
        return "Traditional Dividend"

    return "Unknown"


def compute_income_quality(df: pd.DataFrame, month_weights_map: dict[str, list[float]], asset_type_map: dict[str, str]) -> dict[str, Any]:
    """Return income quality score, classification per holding, and stability metrics."""
    normalized = _normalize_df(df)
    if normalized.empty:
        return {
            "income_quality_score": 0.0,
            "distribution_stability_score": 0.0,
            "yield_sustainability_score": 0.0,
            "payout_risk_score": 0.0,
            "holding_classifications": {},
            "monthly_weight_variance": 0.0,
            "alerts": [],
            "notes": [
                "Income values are annualized estimates.",
                "High-yield ETFs may have unstable distributions.",
                "Option-income ETFs generate variable monthly payouts.",
                "REIT distributions may fluctuate based on property income.",
                "Dividend trends sourced from Yahoo Finance historical data.",
            ],
        }

    monthly_weight_variance = _average_monthly_weight_variance(month_weights_map)
    option_income_weight = float(normalized[normalized["ticker"].isin(OPTION_INCOME_ETFS)]["weight"].sum())
    reit_weight = float(normalized[normalized["asset_type"].isin(REIT_ASSET_TYPES)]["weight"].sum())

    distribution_stability_score = 100.0
    distribution_stability_score -= (monthly_weight_variance * 100.0)
    distribution_stability_score -= (option_income_weight * 0.5 * 100.0)
    distribution_stability_score -= (reit_weight * 0.3 * 100.0)
    distribution_stability_score = _clamp(distribution_stability_score)

    holding_classifications: dict[str, dict[str, Any]] = {}
    sustainability_scores: list[float] = []
    payout_risk_scores: list[float] = []

    for _, row in normalized.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        yield_pct = _to_float(row.get("dividend_yield_pct", 0.0))
        dividend_trend_negative = _is_dividend_trend_negative(row)
        price_volatility_high = _is_volatility_high(row)

        income_type = _classify_income_type(row, asset_type_map)

        yield_sustainability_score = 100.0
        if yield_pct > 10.0:
            yield_sustainability_score -= 30.0
        if yield_pct > 7.0:
            yield_sustainability_score -= 15.0
        if dividend_trend_negative:
            yield_sustainability_score -= 20.0
        if price_volatility_high:
            yield_sustainability_score -= 15.0
        yield_sustainability_score = _clamp(yield_sustainability_score)

        payout_risk_score = 100.0
        if income_type == "Option-Income ETF Distribution":
            payout_risk_score -= 40.0
        if income_type == "REIT Distribution":
            payout_risk_score -= 20.0
        if dividend_trend_negative:
            payout_risk_score -= 20.0
        if yield_pct > 10.0:
            payout_risk_score -= 30.0
        payout_risk_score = _clamp(payout_risk_score)

        stability_score = distribution_stability_score
        ticker_weights = month_weights_map.get(ticker, []) if isinstance(month_weights_map, dict) else []
        if isinstance(ticker_weights, list) and len(ticker_weights) == 12:
            s = pd.to_numeric(pd.Series(ticker_weights), errors="coerce").fillna(0.0)
            if float(s.sum()) > 0:
                stability_score = _clamp(100.0 - float(s.var(ddof=0)) * 100.0)

        notes: list[str] = []
        if yield_pct > 10.0:
            notes.append("Yield exceeds 10% and may indicate elevated payout risk.")
        if dividend_trend_negative:
            notes.append("Historical dividend trend appears negative.")
        if price_volatility_high:
            notes.append("Price volatility appears elevated.")
        if income_type == "Option-Income ETF Distribution":
            notes.append("Option-income distributions can vary month to month.")
        if income_type == "REIT Distribution":
            notes.append("REIT distributions can fluctuate with property income and rates.")

        holding_classifications[ticker] = {
            "income_type": income_type,
            "stability_score": round(stability_score, 2),
            "yield_sustainability_score": round(yield_sustainability_score, 2),
            "payout_risk_score": round(payout_risk_score, 2),
            "notes": notes,
        }

        sustainability_scores.append(yield_sustainability_score)
        payout_risk_scores.append(payout_risk_score)

    yield_sustainability_score = float(sum(sustainability_scores) / len(sustainability_scores)) if sustainability_scores else 0.0
    payout_risk_score = float(sum(payout_risk_scores) / len(payout_risk_scores)) if payout_risk_scores else 0.0

    income_quality_score = (
        distribution_stability_score * 0.40
        + yield_sustainability_score * 0.40
        + payout_risk_score * 0.20
    )

    alerts: list[str] = []
    if bool(normalized["dividend_yield_pct"].gt(10.0).any()):
        alerts.append("High-yield alert: one or more holdings exceed 10% yield.")
    if monthly_weight_variance > 0.10:
        alerts.append("Distribution stability alert: monthly dividend variance is elevated.")
    if option_income_weight > 0:
        alerts.append("Option-income ETF alert: income may be unstable.")
    if reit_weight > 0:
        alerts.append("REIT distribution alert: REITs may reduce stability.")

    notes = [
        "Income values are annualized estimates.",
        "High-yield ETFs may have unstable distributions.",
        "Option-income ETFs generate variable monthly payouts.",
        "REIT distributions may fluctuate based on property income.",
        "Dividend trends sourced from Yahoo Finance historical data.",
    ]

    return {
        "income_quality_score": round(income_quality_score, 1),
        "distribution_stability_score": round(distribution_stability_score, 1),
        "yield_sustainability_score": round(yield_sustainability_score, 1),
        "payout_risk_score": round(payout_risk_score, 1),
        "holding_classifications": holding_classifications,
        "monthly_weight_variance": round(monthly_weight_variance, 4),
        "alerts": alerts,
        "notes": notes,
    }

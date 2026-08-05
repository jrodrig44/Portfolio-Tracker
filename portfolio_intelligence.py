from __future__ import annotations

import math
from typing import Any

import pandas as pd


HIGH_VOLATILITY_ETFS = {"JEPI", "QYLD", "RYLD", "XYLD"}
EMPLOYER_TICKER = "HD"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric_value = float(value)
        return numeric_value if math.isfinite(numeric_value) else default
    except (TypeError, ValueError):
        return default


def _clamp_score(score: float) -> float:
    return max(0.0, min(100.0, score))


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame()


def _prepare_metrics_df(df: pd.DataFrame) -> pd.DataFrame:
    metrics = _as_dataframe(df)
    if metrics.empty:
        return metrics

    if "ticker" not in metrics.columns:
        metrics["ticker"] = ""
    if "asset_type" not in metrics.columns:
        metrics["asset_type"] = ""
    if "sector" not in metrics.columns:
        metrics["sector"] = ""

    for column in ["market_value", "annual_income", "dividend_yield_pct", "portfolio_weight_pct"]:
        if column not in metrics.columns:
            metrics[column] = 0.0
        metrics[column] = pd.to_numeric(metrics[column], errors="coerce").fillna(0.0)

    metrics["ticker"] = metrics["ticker"].fillna("").astype(str).str.strip().str.upper()
    metrics["asset_type"] = metrics["asset_type"].fillna("").astype(str).str.strip()
    metrics["sector"] = metrics["sector"].fillna("").astype(str).str.strip()

    total_value = float(metrics["market_value"].sum())
    if total_value > 0:
        metrics["computed_weight_pct"] = metrics["market_value"] / total_value * 100.0
    else:
        metrics["computed_weight_pct"] = 0.0

    return metrics


def _extract_unresolved_pct(lookthrough_df: Any, total_portfolio_value: float) -> float:
    if isinstance(lookthrough_df, dict):
        unresolved_pct_keys = [
            "unresolved_portfolio_weight_pct",
            "unresolved_pct",
            "unresolved_exposure_pct",
        ]
        for key in unresolved_pct_keys:
            if key in lookthrough_df:
                return max(0.0, _to_float(lookthrough_df.get(key)))

        unresolved_value_keys = [
            "unresolved_value",
            "unresolved_fund_exposure",
        ]
        for key in unresolved_value_keys:
            unresolved_value = _to_float(lookthrough_df.get(key))
            if total_portfolio_value > 0:
                return max(0.0, unresolved_value / total_portfolio_value * 100.0)

    unresolved_df = _as_dataframe(lookthrough_df)
    if unresolved_df.empty:
        return 0.0

    for column in ["Portfolio_Weight", "portfolio_weight_pct"]:
        if column in unresolved_df.columns:
            return max(0.0, _to_float(pd.to_numeric(unresolved_df[column], errors="coerce").fillna(0.0).sum()))

    if total_portfolio_value > 0:
        for column in ["Market_Value", "market_value"]:
            if column in unresolved_df.columns:
                unresolved_value = _to_float(pd.to_numeric(unresolved_df[column], errors="coerce").fillna(0.0).sum())
                return max(0.0, unresolved_value / total_portfolio_value * 100.0)

    return 0.0


def compute_income_quality(df: pd.DataFrame) -> float:
    metrics = _prepare_metrics_df(df)
    if metrics.empty:
        return 0.0

    total_income = float(metrics["annual_income"].sum())
    if total_income > 0:
        income_weights = metrics["annual_income"] / total_income * 100.0
    else:
        income_weights = pd.Series(0.0, index=metrics.index)

    reits_income_pct = float(income_weights[metrics["asset_type"].str.upper().eq("REIT")].sum())
    high_vol_etf_income_pct = float(income_weights[metrics["ticker"].isin(HIGH_VOLATILITY_ETFS)].sum())
    single_stock_income_pct = float(income_weights[metrics["ticker"].eq(EMPLOYER_TICKER)].sum())
    high_yield_count = int((metrics["dividend_yield_pct"] > 10.0).sum())

    score = 100.0
    score -= reits_income_pct * 0.4
    score -= high_vol_etf_income_pct * 0.5
    score -= single_stock_income_pct * 0.3
    score -= high_yield_count * 5.0
    return round(_clamp_score(score), 2)


def compute_distribution_stability(df: pd.DataFrame) -> float:
    metrics = _prepare_metrics_df(df)
    if metrics.empty:
        return 0.0

    total_income = float(metrics["annual_income"].sum())
    if total_income > 0:
        income_weights = metrics["annual_income"] / total_income
        monthly_weight_variance = float(income_weights.var(ddof=0))
    else:
        monthly_weight_variance = 1.0

    stable_count = int(((metrics["annual_income"] > 0) & (metrics["dividend_yield_pct"] <= 10.0)).sum())
    stable_ratio = stable_count / len(metrics) if len(metrics) > 0 else 0.0
    has_variable_etf = bool(metrics["ticker"].isin({"JEPI"}).any())

    score = 100.0
    score -= monthly_weight_variance * 100.0
    score -= (1.0 - stable_ratio) * 20.0
    if has_variable_etf:
        score -= 10.0
    return round(_clamp_score(score), 2)


def compute_concentration_risk(df: pd.DataFrame) -> float:
    metrics = _prepare_metrics_df(df)
    if metrics.empty:
        return 0.0

    largest_holding_pct = float(metrics["computed_weight_pct"].max()) if not metrics.empty else 0.0
    top3_pct = float(metrics.nlargest(3, "computed_weight_pct")["computed_weight_pct"].sum()) if not metrics.empty else 0.0
    holding_is_employer_stock = bool(metrics["ticker"].eq(EMPLOYER_TICKER).any())

    score = 100.0
    score -= largest_holding_pct * 1.2
    score -= top3_pct * 0.5
    if holding_is_employer_stock:
        score -= 15.0
    return round(_clamp_score(score), 2)


def compute_asset_type_risk(df: pd.DataFrame) -> float:
    metrics = _prepare_metrics_df(df)
    if metrics.empty:
        return 0.0

    weight_by_asset_type = metrics.groupby("asset_type", dropna=False)["computed_weight_pct"].sum()

    score = 100.0
    for asset_type_pct in weight_by_asset_type.tolist():
        pct = _to_float(asset_type_pct)
        if pct > 50.0:
            score -= (pct - 50.0) * 1.5
    return round(_clamp_score(score), 2)


def compute_sector_balance(df: pd.DataFrame, lookthrough_df: Any) -> float:
    metrics = _prepare_metrics_df(df)
    if metrics.empty:
        return 0.0

    total_portfolio_value = float(metrics["market_value"].sum())
    direct_sector_weights = (
        metrics[metrics["sector"].ne("")]
        .groupby("sector", dropna=False)["computed_weight_pct"]
        .sum()
        .to_dict()
    )

    lookthrough_records: list[dict[str, Any]] = []
    if isinstance(lookthrough_df, dict):
        raw_records = lookthrough_df.get("records", [])
        if isinstance(raw_records, list):
            lookthrough_records = [row for row in raw_records if isinstance(row, dict)]
    elif isinstance(lookthrough_df, list):
        lookthrough_records = [row for row in lookthrough_df if isinstance(row, dict)]
    else:
        lookthrough_records = _as_dataframe(lookthrough_df).to_dict(orient="records")

    combined_sector_weights = {k: _to_float(v) for k, v in direct_sector_weights.items()}
    for row in lookthrough_records:
        sector = str(row.get("Sector", row.get("sector", ""))).strip()
        if not sector:
            continue
        sector_weight = _to_float(row.get("Portfolio_Weight", row.get("portfolio_weight_pct", 0.0)))
        combined_sector_weights[sector] = combined_sector_weights.get(sector, 0.0) + sector_weight

    unresolved_pct = _extract_unresolved_pct(lookthrough_df, total_portfolio_value)

    score = 100.0
    for sector_pct in combined_sector_weights.values():
        if sector_pct > 40.0:
            score -= (sector_pct - 40.0) * 1.2
    score -= unresolved_pct * 0.5

    return round(_clamp_score(score), 2)


def compute_espp_context(hd_row: dict[str, Any], espp_config: dict[str, Any]) -> dict[str, Any]:
    hd_weight = _to_float(hd_row.get("portfolio_weight_pct", 0.0))
    hd_income = _to_float(hd_row.get("annual_income", 0.0))
    total_income = _to_float(hd_row.get("total_income", 0.0))

    hd_income_pct = (hd_income / total_income) if total_income > 0 else 0.0
    biweekly_contribution = _to_float(espp_config.get("biweekly_contribution", 100.0))
    discount = _to_float(espp_config.get("discount", 0.15))
    purchase_frequency_months = int(_to_float(espp_config.get("purchase_frequency_months", 6), default=6.0) or 6)

    return {
        "annual_contribution": round(biweekly_contribution * 26.0, 2),
        "discount": discount,
        "purchase_frequency_months": purchase_frequency_months,
        "hd_weight": round(hd_weight, 2),
        "hd_income_pct": round(hd_income_pct, 4),
        "notes": [
            "HD is employer stock.",
            "ESPP discount applies.",
            "Concentration must be evaluated separately.",
        ],
    }


def compute_portfolio_health_score(metrics: dict[str, Any]) -> float:
    return round(
        _clamp_score(
            _to_float(metrics.get("income_quality_score", 0.0)) * 0.20
            + _to_float(metrics.get("distribution_stability_score", 0.0)) * 0.20
            + _to_float(metrics.get("concentration_risk_score", 0.0)) * 0.25
            + _to_float(metrics.get("asset_type_risk_score", 0.0)) * 0.15
            + _to_float(metrics.get("sector_balance_score", 0.0)) * 0.20
        ),
        2,
    )


def compute_portfolio_intelligence(df: pd.DataFrame, lookthrough_df: Any, espp_config: dict[str, Any]) -> dict[str, Any]:
    metrics = _prepare_metrics_df(df)

    income_quality_score = compute_income_quality(metrics)
    distribution_stability_score = compute_distribution_stability(metrics)
    concentration_risk_score = compute_concentration_risk(metrics)
    asset_type_risk_score = compute_asset_type_risk(metrics)
    sector_balance_score = compute_sector_balance(metrics, lookthrough_df)

    total_income = float(metrics["annual_income"].sum()) if not metrics.empty else 0.0
    hd_data = {
        "portfolio_weight_pct": 0.0,
        "annual_income": 0.0,
        "total_income": total_income,
    }
    if not metrics.empty and metrics["ticker"].eq(EMPLOYER_TICKER).any():
        hd_row = metrics[metrics["ticker"].eq(EMPLOYER_TICKER)].iloc[0]
        hd_data = {
            "portfolio_weight_pct": _to_float(hd_row.get("computed_weight_pct", 0.0)),
            "annual_income": _to_float(hd_row.get("annual_income", 0.0)),
            "total_income": total_income,
        }

    espp_context = compute_espp_context(hd_data, espp_config)
    health_metrics = {
        "income_quality_score": income_quality_score,
        "distribution_stability_score": distribution_stability_score,
        "concentration_risk_score": concentration_risk_score,
        "asset_type_risk_score": asset_type_risk_score,
        "sector_balance_score": sector_balance_score,
    }
    health_score = compute_portfolio_health_score(health_metrics)

    unresolved_pct = _extract_unresolved_pct(lookthrough_df, float(metrics["market_value"].sum()) if not metrics.empty else 0.0)
    high_yield_present = bool((metrics["dividend_yield_pct"] > 10.0).any()) if not metrics.empty else False
    hd_weight = _to_float(espp_context.get("hd_weight", 0.0))

    alerts: list[str] = []
    if hd_weight > 40.0:
        alerts.append("HD concentration alert: employer stock exceeds 40% of portfolio.")
    if high_yield_present:
        alerts.append("High-yield alert: one or more holdings exceed 10% yield.")
    if unresolved_pct > 5.0:
        alerts.append("Unresolved sector exposure alert.")

    notes = [
        "Income values are annualized estimates.",
        "Dividend yields sourced from Yahoo Finance.",
        "ESPP concentration evaluated separately.",
        "Look-through sector exposure incomplete for some funds.",
    ]

    return {
        "income_quality_score": income_quality_score,
        "distribution_stability_score": distribution_stability_score,
        "concentration_risk_score": concentration_risk_score,
        "asset_type_risk_score": asset_type_risk_score,
        "sector_balance_score": sector_balance_score,
        "espp_context": espp_context,
        "health_score": health_score,
        "alerts": alerts,
        "notes": notes,
    }

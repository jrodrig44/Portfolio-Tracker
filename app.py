from __future__ import annotations

from datetime import datetime, timedelta
import calendar
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable

from dotenv import load_dotenv
import pandas as pd
import datetime as dt
import streamlit as st
import yfinance as yf

from ai_analysis_prompt import AI_ANALYSIS_TYPES, build_ai_analysis_markdown, build_ai_analysis_messages, format_ai_analysis_markdown
from health_engine import compute_portfolio_health as compute_portfolio_health_engine
from portfolio_intelligence import compute_portfolio_intelligence
from portfolio_analysis import canonical_asset_type as shared_canonical_asset_type
from portfolio_analysis import future_value as shared_future_value
from portfolio_analysis import normalize_sector as shared_normalize_sector
from portfolio_history import DEFAULT_HISTORY_RANGE, HISTORY_RANGE_OPTIONS, filter_history_range
from portfolio_export import build_export_package as build_shared_export_package
from stash_history import clear_scan_preview, delete_imported_history, history_summary, import_validated_history, load_performance_history, load_statement_history, remove_manual_statement_review, run_statement_scan, save_manual_statement_review, scan_can_import, scan_state, upsert_live_portfolio_snapshot
from onemin_ai_client import (
    ONEMIN_AI_DEFAULT_BASE_URL,
    ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ONEMIN_AI_DEFAULT_MODEL,
    ONEMIN_AI_TIMEOUT_SECONDS,
    OneMinAIAuthenticationError,
    OneMinAIClient,
    OneMinAIConfigError,
    OneMinAIConnectionError,
    OneMinAIError,
    OneMinAIInsufficientCreditsError,
    OneMinAIRateLimitError,
    OneMinAIResponseError,
    OneMinAITimeoutError,
    OneMinAIUnsupportedModelError,
    messages_to_prompt,
)
from supplemental_research import (
    DEFAULT_RESEARCH_MODEL,
    SupplementalResearchService,
    get_research_rows,
    research_is_due,
    set_setting,
    SUPPORTED_RESEARCH_MODELS,
    validated_research_model,
)
from logging_setup import configure_structured_logging, log_event

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATEMENTS_DIR = PROJECT_ROOT / "Statements"
STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
STATEMENT_HISTORY_DB_PATH = DATA_DIR / "stash_statement_history.sqlite"
DEFAULT_DB_PATH = DATA_DIR / "portfolio.db"
LEGACY_CSV_PATH = DATA_DIR / "holdings.csv"
HOLDINGS_TABLE = "holdings"
EXPORT_JSON_PATH = DATA_DIR / "portfolio_export.json"

ESPP_DISCOUNT = 0.15
ESPP_CONTRIB_BIWEEKLY = 100.0
ESPP_PURCHASE_FREQUENCY = "6_months"
DEFAULT_EXPECTED_RETURN = 0.08
DEFAULT_PROJECTION_BIWEEKLY_CONTRIB = 280.0
EXPORT_VERSION = "2026.08"
ENABLE_AI_ANALYSIS = True
PRICE_CACHE_TTL_SECONDS = 60
YAHOO_METADATA_CACHE_TTL_SECONDS = 86400
INVESTMENT_INTELLIGENCE_CACHE_TTL_SECONDS = 21600
DIVIDEND_TIMING_CACHE_TTL_SECONDS = 86400
HISTORICAL_CONTEXT_CACHE_TTL_SECONDS = 600
FUND_SECTOR_WEIGHTS_CACHE_TTL_SECONDS = 3600
ONEMIN_AI_RECOMMENDED_MODELS = [
    "gpt-4.1-mini",
    "gemini-2.5-flash",
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
]
ONEMIN_AI_MODEL_DETAILS = {
    "gpt-4.1-mini": {
        "label": "GPT-4.1 Mini",
        "description": (
            "Recommended for everyday portfolio analysis. Offers the best balance "
            "of instruction-following, analytical quality, speed, and credit usage."
        ),
    },
    "gemini-2.5-flash": {
        "label": "Gemini 2.5 Flash",
        "description": (
            "Best for fast analysis of larger portfolio contexts and obtaining a "
            "second analytical perspective while keeping credit usage relatively low."
        ),
    },
    "claude-haiku-4-5-20251001": {
        "label": "Claude Haiku 4.5",
        "description": (
            "Best for careful explanations, nuanced wording, and following detailed "
            "portfolio and ESPP instructions."
        ),
    },
    "gpt-4o-mini": {
        "label": "GPT-4o Mini",
        "description": (
            "Economical fallback for basic summaries and simple questions. It uses "
            "fewer resources but may produce weaker or overstated portfolio conclusions."
        ),
    },
}

OPTION_INCOME_ETF_TICKERS = {"JEPI", "JEPQ", "QYLD", "RYLD", "XYLD"}
SECTOR_LABEL_ALIASES = {
    "realestate": "Real Estate",
}

REQUIRED_COLUMNS = [
    "ticker",
    "shares",
    "amount_invested",
    "annual_div_per_share",
    "regular_biweekly_contribution",
    "espp_biweekly_contribution",
    "espp_contribution_biweekly",
    "special_contribution",
    "employer_stock",
    "espp_enabled",
    "is_espp",
    "espp_discount",
    "espp_discount_pct",
    "espp_purchase_frequency_months",
    "espp_purchase_frequency",
    "estimated_annual_distribution_per_share",
    "annualized_income",
    "espp_acquired_shares",
    "sector",
    "asset_type",
]

HOLDINGS_SCHEMA_COLUMNS = [
    "ticker",
    "shares",
    "amount_invested",
    "annual_div_per_share",
    "regular_biweekly_contribution",
    "espp_biweekly_contribution",
    "espp_contribution_biweekly",
    "special_contribution",
    "employer_stock",
    "espp_enabled",
    "is_espp",
    "espp_discount",
    "espp_discount_pct",
    "espp_purchase_frequency_months",
    "espp_purchase_frequency",
    "estimated_annual_distribution_per_share",
    "annualized_income",
    "espp_acquired_shares",
    "effective_annual_div_per_share",
    "reported_annual_div_per_share",
    "dividend_yield_pct",
    "avg_cost",
    "current_price",
    "market_value",
    "unrealized_gain",
    "portfolio_yield_pct",
    "sector",
    "asset_type",
]

VALID_TICKER_PATTERN = re.compile(r"^(?=.*[A-Z])[A-Z0-9.\-^]+$")
STREAMLIT_LOG_PATH = Path(__file__).resolve().parent / "logs" / "streamlit.jsonl"
LOGGER = configure_structured_logging("portfolio_tracker", STREAMLIT_LOG_PATH, "streamlit")


def _use_export_json_data() -> bool:
    return bool(st.session_state.get("use_export_json_data", False))


def _strict_export_accuracy_mode() -> bool:
    strict_enabled = bool(st.session_state.get("strict_export_accuracy_mode", False))
    export_enabled = bool(st.session_state.get("use_export_json_data", False))
    export_payload = st.session_state.get("export_payload", {})
    has_export_payload = isinstance(export_payload, dict) and bool(export_payload)
    return strict_enabled and export_enabled and has_export_payload


def _reset_export_missing_field_warnings() -> None:
    st.session_state["_missing_export_fields"] = set()


def _add_missing_export_field_warning(field_name: str) -> None:
    missing = st.session_state.get("_missing_export_fields", set())
    if not isinstance(missing, set):
        missing = set(missing) if isinstance(missing, (list, tuple)) else set()
    missing.add(str(field_name))
    st.session_state["_missing_export_fields"] = missing


def load_export_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Unable to parse export JSON %s: %s", path, exc)
        return {}

    required_sections = {
        "portfolio summary": ["portfolio_snapshot", "portfolio_summary", "export_summary"],
        "holdings": ["holdings"],
        "calculated metrics": ["calculated_metrics"],
        "contribution context": ["contribution_context", "export_contributions"],
    }

    for label, keys in required_sections.items():
        if any(key in payload for key in keys):
            continue
        LOGGER.warning("Export JSON is missing required section: %s", label)

    return payload if isinstance(payload, dict) else {}


def _initialize_export_state(path: Path = EXPORT_JSON_PATH) -> None:
    payload = load_export_json(path)
    st.session_state["export_payload"] = payload

    summary = payload.get("portfolio_snapshot") or payload.get("portfolio_summary") or payload.get("export_summary") or {}
    holdings = payload.get("holdings") or []
    metrics = payload.get("calculated_metrics") or []
    contributions = payload.get("contribution_context") or payload.get("export_contributions") or {}

    st.session_state["export_summary"] = summary if isinstance(summary, dict) else {}
    st.session_state["export_holdings"] = holdings if isinstance(holdings, list) else []
    st.session_state["export_metrics"] = metrics if isinstance(metrics, list) else []
    st.session_state["export_contributions"] = contributions if isinstance(contributions, dict) else {}


def _export_holdings_df() -> pd.DataFrame:
    return pd.DataFrame(st.session_state.get("export_holdings", []))


def _export_metrics_df() -> pd.DataFrame:
    return pd.DataFrame(st.session_state.get("export_metrics", []))


def _export_lookup_row(ticker: str, source: str = "metrics") -> dict[str, object]:
    ticker_key = str(ticker).strip().upper()
    if not ticker_key:
        return {}

    source_df = _export_metrics_df() if source == "metrics" else _export_holdings_df()
    if source_df.empty or "ticker" not in source_df.columns:
        return {}
    source_df = source_df.copy()
    source_df["ticker"] = source_df["ticker"].fillna("").astype(str).str.strip().str.upper()
    row = source_df[source_df["ticker"].eq(ticker_key)]
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def resolve_value(key: str, ticker: str, live_value: float | str | None) -> float | str | None:
    # 1) Export JSON when enabled
    use_export = _use_export_json_data()
    strict_export = _strict_export_accuracy_mode()
    key_name = str(key)
    ticker_key = str(ticker).strip().upper()

    export_value = None
    if use_export and ticker_key:
        metric_row = _export_lookup_row(ticker_key, source="metrics")
        holding_row = _export_lookup_row(ticker_key, source="holdings")
        if key_name in metric_row and metric_row.get(key_name) not in [None, ""]:
            export_value = metric_row.get(key_name)
        elif key_name in holding_row and holding_row.get(key_name) not in [None, ""]:
            export_value = holding_row.get(key_name)
        if export_value not in [None, ""]:
            return export_value

    # 2) In normal mode, prefer fresh live calculation fields over stored snapshots.
    live_preferred_keys = {
        "current_price",
        "dividend_yield_pct",
        "sector",
        "asset_type",
        "effective_annual_div_per_share",
        "reported_annual_div_per_share",
        "annualized_income",
    }
    if key_name in live_preferred_keys and live_value not in [None, ""] and not strict_export:
        return live_value

    # 3) DB/session holdings
    db_holdings = st.session_state.get("holdings")
    if isinstance(db_holdings, pd.DataFrame) and not db_holdings.empty and ticker_key:
        holdings_df = db_holdings.copy()
        if "ticker" in holdings_df.columns:
            holdings_df["ticker"] = holdings_df["ticker"].fillna("").astype(str).str.strip().str.upper()
            match = holdings_df[holdings_df["ticker"].eq(ticker_key)]
            if not match.empty and key_name in match.columns:
                db_value = match.iloc[0].get(key_name)
                if key_name == "current_price":
                    db_price = _coerce_optional_number(db_value)
                    if db_price is not None and db_price > 0:
                        return db_price
                    db_value = None
                if db_value not in [None, ""]:
                    return db_value

    # 4) Live value
    if live_value not in [None, ""] and not strict_export:
        return live_value

    # 5) strict mode warning or full miss
    if strict_export and use_export:
        _add_missing_export_field_warning(f"{ticker_key}:{key_name}")
    return None


def _resolved_price_map(tickers: tuple[str, ...]) -> dict[str, float]:
    live_prices = {} if _strict_export_accuracy_mode() else _latest_prices(tickers)
    resolved: dict[str, float] = {}
    for ticker in tickers:
        value = resolve_value("current_price", ticker, live_prices.get(ticker))
        try:
            resolved[ticker] = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            resolved[ticker] = 0.0
    return resolved


def _resolved_dividend_yield_map(tickers: tuple[str, ...]) -> dict[str, float]:
    live_yields = {} if _strict_export_accuracy_mode() else _latest_dividend_yields(tickers)
    resolved: dict[str, float] = {}
    for ticker in tickers:
        value = resolve_value("dividend_yield_pct", ticker, live_yields.get(ticker))
        try:
            resolved[ticker] = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            resolved[ticker] = 0.0
    return resolved


def _resolved_sector_map(tickers: tuple[str, ...]) -> dict[str, str]:
    live_sectors = {} if _strict_export_accuracy_mode() else _latest_sectors(tickers)
    resolved: dict[str, str] = {}
    for ticker in tickers:
        value = resolve_value("sector", ticker, live_sectors.get(ticker, ""))
        resolved[ticker] = str(value or "")
    return resolved


def _resolved_asset_type_map(tickers: tuple[str, ...]) -> dict[str, str]:
    live_types = {} if _strict_export_accuracy_mode() else _latest_asset_type_hints(tickers)
    resolved: dict[str, str] = {}
    for ticker in tickers:
        value = resolve_value("asset_type", ticker, live_types.get(ticker, "Stock"))
        resolved[ticker] = _canonical_asset_type(str(value or "Stock"))
    return resolved


def _resolved_historical_context(tickers: tuple[str, ...], history_start_date: str | None = None) -> dict[str, dict[str, object]]:
    export_payload = st.session_state.get("export_payload", {}) if _use_export_json_data() else {}
    export_history = export_payload.get("historical_context", []) if isinstance(export_payload, dict) else []
    export_map: dict[str, dict[str, object]] = {}
    if isinstance(export_history, list):
        for item in export_history:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker", "")).strip().upper()
            if ticker:
                export_map[ticker] = item

    if _strict_export_accuracy_mode():
        strict_map: dict[str, dict[str, object]] = {}
        for ticker in tickers:
            if ticker in export_map:
                strict_map[ticker] = export_map[ticker]
            else:
                _add_missing_export_field_warning(f"{ticker}:historical_context")
                strict_map[ticker] = {
                    "ticker": ticker,
                    "price_trend": {},
                    "dividend_trend": {},
                    "history_summary": "Missing historical context in export.",
                }
        return strict_map

    live_map = _latest_historical_context(tickers, history_start_date)
    if not _use_export_json_data():
        return live_map

    merged = live_map.copy()
    for ticker in tickers:
        if ticker in export_map:
            merged[ticker] = export_map[ticker]
    return merged


def _infer_device_type(user_agent: str) -> str:
    ua = str(user_agent or "").lower()
    if any(token in ua for token in ["iphone", "android", "mobile"]):
        return "Mobile"
    if any(token in ua for token in ["ipad", "tablet"]):
        return "Tablet"
    if ua == "":
        return "Unknown"
    return "Desktop"


def _get_context_headers() -> dict[str, str]:
    context = getattr(st, "context", None)
    headers = getattr(context, "headers", None)
    if headers is None:
        return {}

    try:
        if hasattr(headers, "items"):
            return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:
        return {}
    return {}


def _resolve_client_ip(headers: dict[str, str]) -> str:
    x_forwarded_for = headers.get("x-forwarded-for", "").strip()
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    x_real_ip = headers.get("x-real-ip", "").strip()
    if x_real_ip:
        return x_real_ip

    cf_ip = headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip

    return ""


def _canonical_asset_type(value: str) -> str:
    return shared_canonical_asset_type(value)


def _infer_sector_from_info(info: dict) -> str:
    # Prefer Yahoo's sector values and keep a conservative fallback.
    sector = str(info.get("sector") or info.get("sectorDisp") or "").strip()
    if sector:
        return _normalize_sector_label(sector, unresolved_label="")

    industry = str(info.get("industry") or info.get("industryDisp") or "").strip()
    return _normalize_sector_label(industry, unresolved_label="")


def _sanitize_ticker_series(series: pd.Series) -> pd.Series:
    cleaned = series.fillna("").astype(str).str.upper().str.strip()
    cleaned = cleaned.replace({"NAN": "", "NONE": ""})
    # Reject entries like 0/123 and keep only symbol-like values.
    return cleaned.where(cleaned.apply(lambda value: bool(VALID_TICKER_PATTERN.match(value))), "")


def _to_ticker_tuple(series: pd.Series) -> tuple[str, ...]:
    cleaned = _sanitize_ticker_series(series)
    return tuple(sorted(cleaned[cleaned.ne("")].unique().tolist()))


def _parse_espp_frequency_months(value: object) -> int:
    if value is None:
        return 6
    text = str(value).strip().lower()
    mapping = {
        "3_months": 3,
        "6_months": 6,
        "12_months": 12,
    }
    if text in mapping:
        return mapping[text]
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return 6
    return parsed if parsed > 0 else 6


def _coerce_finite_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        numeric_value = float(value)
        return numeric_value if math.isfinite(numeric_value) else None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower() in {"", "n/a", "nan", "none", "infinity", "-infinity", "inf", "-inf"}:
            return None
        try:
            numeric_value = float(cleaned)
        except ValueError:
            return None
        return numeric_value if math.isfinite(numeric_value) else None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if math.isfinite(numeric_value) else None


def _normalize_percentage_value(value: object, default_pct: float = 0.0) -> float:
    numeric_value = _coerce_finite_number(value)
    if numeric_value is None:
        return default_pct
    if 0.0 < abs(numeric_value) < 1.0:
        return numeric_value * 100.0
    return numeric_value


def _normalize_sector_alias(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    alias_key = re.sub(r"[^a-z]", "", text.lower())
    return SECTOR_LABEL_ALIASES.get(alias_key)


def _dedupe_text_items(items: object) -> list[str]:
    if not isinstance(items, list):
        return []

    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        identity = re.sub(r"\s+", " ", text).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(text)
    return deduped


def _sanitize_export_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _sanitize_export_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_export_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_export_value(item) for item in value]
    if isinstance(value, set):
        return [_sanitize_export_value(item) for item in sorted(value, key=str)]
    if isinstance(value, pd.DataFrame):
        return _sanitize_export_value(value.to_dict(orient="records"))
    if isinstance(value, pd.Series):
        return _sanitize_export_value(value.tolist())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_value = float(value)
        return numeric_value if math.isfinite(numeric_value) else None
    if isinstance(value, str) and value in {"nan", "NaN", "Infinity", "-Infinity", "inf", "-inf"}:
        return None
    return value


def _format_optional_metric(value: object, decimals: int = 2, suffix: str = "", unavailable: str = "Unavailable") -> str:
    numeric_value = _coerce_finite_number(value)
    if numeric_value is None:
        return unavailable
    return f"{numeric_value:.{decimals}f}{suffix}"


def _resolve_health_score_value(health: dict[str, object]) -> float:
    if not isinstance(health, dict):
        return 0.0
    return _safe_float(health.get("health_score", health.get("overall", 0.0)), 0.0)

def _normalize_columns(df: pd.DataFrame, drop_blank_tickers: bool = True) -> pd.DataFrame:
    output = pd.DataFrame()
    lowered = {str(col).strip().lower(): col for col in df.columns}

    amount_invested_key = "amount_invested"
    if amount_invested_key not in lowered and "amount invested" in lowered:
        amount_invested_key = "amount invested"

    output["ticker"] = df[lowered["ticker"]] if "ticker" in lowered else ""
    output["shares"] = df[lowered["shares"]] if "shares" in lowered else 0.0
    output["annual_div_per_share"] = df[lowered["annual_div_per_share"]] if "annual_div_per_share" in lowered else 0.0
    output["sector"] = df[lowered["sector"]] if "sector" in lowered else ""
    output["asset_type"] = df[lowered["asset_type"]] if "asset_type" in lowered else "Stock"

    regular_contrib_key = "regular_biweekly_contribution"
    legacy_regular_contrib_key = "biweekly contribution"
    special_contrib_key = "special_contribution"
    legacy_special_contrib_key = "special contribution"
    espp_contrib_key = "espp_biweekly_contribution"
    espp_contrib_key_v2 = "espp_contribution_biweekly"
    espp_shares_key = "espp_acquired_shares"
    is_espp_key = "is_espp"
    employer_stock_key = "employer_stock"
    espp_enabled_key = "espp_enabled"
    espp_discount_key = "espp_discount"
    espp_discount_pct_key = "espp_discount_pct"
    espp_frequency_months_key = "espp_purchase_frequency_months"
    espp_frequency_key = "espp_purchase_frequency"
    estimated_distribution_key = "estimated_annual_distribution_per_share"
    annualized_income_key = "annualized_income"
    effective_div_key = "effective_annual_div_per_share"
    reported_div_key = "reported_annual_div_per_share"
    dividend_yield_key = "dividend_yield_pct"
    avg_cost_key = "avg_cost"
    current_price_key = "current_price"
    market_value_key = "market_value"
    unrealized_gain_key = "unrealized_gain"
    portfolio_yield_key = "portfolio_yield_pct"

    if regular_contrib_key in lowered:
        output["regular_biweekly_contribution"] = df[lowered[regular_contrib_key]]
    elif legacy_regular_contrib_key in lowered:
        output["regular_biweekly_contribution"] = df[lowered[legacy_regular_contrib_key]]
    else:
        output["regular_biweekly_contribution"] = 0.0

    if espp_contrib_key in lowered:
        output["espp_biweekly_contribution"] = df[lowered[espp_contrib_key]]
    elif espp_contrib_key_v2 in lowered:
        output["espp_biweekly_contribution"] = df[lowered[espp_contrib_key_v2]]
    else:
        output["espp_biweekly_contribution"] = 0.0
    output["espp_contribution_biweekly"] = output["espp_biweekly_contribution"]

    if special_contrib_key in lowered:
        output["special_contribution"] = df[lowered[special_contrib_key]]
    elif legacy_special_contrib_key in lowered:
        output["special_contribution"] = df[lowered[legacy_special_contrib_key]]
    else:
        output["special_contribution"] = 0.0

    if estimated_distribution_key in lowered:
        output["estimated_annual_distribution_per_share"] = df[lowered[estimated_distribution_key]]
    else:
        output["estimated_annual_distribution_per_share"] = 0.0

    if annualized_income_key in lowered:
        output["annualized_income"] = df[lowered[annualized_income_key]]
    else:
        output["annualized_income"] = 0.0

    if espp_shares_key in lowered:
        output["espp_acquired_shares"] = df[lowered[espp_shares_key]]
    else:
        output["espp_acquired_shares"] = 0.0

    if is_espp_key in lowered:
        output["is_espp"] = df[lowered[is_espp_key]]
    else:
        output["is_espp"] = False

    if employer_stock_key in lowered:
        output["employer_stock"] = df[lowered[employer_stock_key]]
    else:
        output["employer_stock"] = False

    if espp_enabled_key in lowered:
        output["espp_enabled"] = df[lowered[espp_enabled_key]]
    else:
        output["espp_enabled"] = False

    if espp_discount_key in lowered:
        output["espp_discount"] = df[lowered[espp_discount_key]]
    else:
        output["espp_discount"] = ESPP_DISCOUNT

    if espp_discount_pct_key in lowered:
        output["espp_discount_pct"] = df[lowered[espp_discount_pct_key]]
    else:
        output["espp_discount_pct"] = output["espp_discount"].apply(lambda value: _normalize_percentage_value(value, ESPP_DISCOUNT * 100.0))

    if espp_frequency_months_key in lowered:
        output["espp_purchase_frequency_months"] = df[lowered[espp_frequency_months_key]]
    elif espp_frequency_key in lowered:
        output["espp_purchase_frequency_months"] = df[lowered[espp_frequency_key]].apply(_parse_espp_frequency_months)
    else:
        output["espp_purchase_frequency_months"] = _parse_espp_frequency_months(ESPP_PURCHASE_FREQUENCY)
    if espp_frequency_key in lowered:
        output["espp_purchase_frequency"] = df[lowered[espp_frequency_key]].fillna("").astype(str)
    else:
        output["espp_purchase_frequency"] = ""

    output["effective_annual_div_per_share"] = df[lowered[effective_div_key]] if effective_div_key in lowered else 0.0
    output["reported_annual_div_per_share"] = df[lowered[reported_div_key]] if reported_div_key in lowered else 0.0
    output["dividend_yield_pct"] = df[lowered[dividend_yield_key]] if dividend_yield_key in lowered else 0.0
    output["avg_cost"] = df[lowered[avg_cost_key]] if avg_cost_key in lowered else 0.0
    output["current_price"] = df[lowered[current_price_key]] if current_price_key in lowered else 0.0
    output["market_value"] = df[lowered[market_value_key]] if market_value_key in lowered else 0.0
    output["unrealized_gain"] = df[lowered[unrealized_gain_key]] if unrealized_gain_key in lowered else 0.0
    output["portfolio_yield_pct"] = df[lowered[portfolio_yield_key]] if portfolio_yield_key in lowered else 0.0

    if amount_invested_key in lowered:
        output["amount_invested"] = df[lowered[amount_invested_key]]
    elif "avg_cost" in lowered:
        legacy_avg_cost = pd.to_numeric(df[lowered["avg_cost"]], errors="coerce").fillna(0.0)
        legacy_shares = pd.to_numeric(output["shares"], errors="coerce").fillna(0.0)
        output["amount_invested"] = legacy_shares * legacy_avg_cost
    else:
        output["amount_invested"] = 0.0

    output["ticker"] = _sanitize_ticker_series(output["ticker"])
    output["sector"] = output["sector"].astype(str).str.strip()
    output["sector"] = output["sector"].replace({"nan": "", "None": ""})
    output["sector"] = output["sector"].apply(lambda value: _normalize_sector_label(value, unresolved_label=""))
    output["asset_type"] = output["asset_type"].apply(_canonical_asset_type)

    for numeric_col in [
        "shares",
        "amount_invested",
        "annual_div_per_share",
        "regular_biweekly_contribution",
        "espp_biweekly_contribution",
        "espp_contribution_biweekly",
        "special_contribution",
        "estimated_annual_distribution_per_share",
        "annualized_income",
        "espp_acquired_shares",
        "espp_discount",
        "espp_discount_pct",
        "espp_purchase_frequency_months",
        "effective_annual_div_per_share",
        "reported_annual_div_per_share",
        "dividend_yield_pct",
        "avg_cost",
        "current_price",
        "market_value",
        "unrealized_gain",
        "portfolio_yield_pct",
    ]:
        output[numeric_col] = pd.to_numeric(output[numeric_col], errors="coerce").fillna(0.0)

    output["espp_discount_pct"] = output["espp_discount_pct"].apply(lambda value: _normalize_percentage_value(value, 0.0))

    output["is_espp"] = output["is_espp"].fillna(False).astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})
    output["employer_stock"] = output["employer_stock"].fillna(False).astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})
    output["espp_enabled"] = output["espp_enabled"].fillna(False).astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})
    inferred_is_espp = output["ticker"].eq("HD") | output["espp_contribution_biweekly"].gt(0) | output["espp_acquired_shares"].gt(0)
    output.loc[~output["is_espp"], "is_espp"] = inferred_is_espp[~output["is_espp"]]
    output.loc[~output["is_espp"], "espp_discount"] = 0.0
    output.loc[output["ticker"].eq("HD"), "employer_stock"] = True
    output.loc[output["ticker"].eq("HD"), "espp_enabled"] = True
    output.loc[output["ticker"].eq("HD") & output["espp_contribution_biweekly"].le(0), "espp_contribution_biweekly"] = ESPP_CONTRIB_BIWEEKLY
    output.loc[output["ticker"].eq("HD"), "espp_biweekly_contribution"] = output.loc[output["ticker"].eq("HD"), "espp_contribution_biweekly"]
    output.loc[output["ticker"].eq("HD"), "regular_biweekly_contribution"] = 0.0
    output.loc[output["ticker"].eq("HD"), "special_contribution"] = 0.0
    output.loc[output["ticker"].eq("HD"), "espp_discount_pct"] = 15.0
    output.loc[output["ticker"].eq("HD"), "espp_discount"] = 0.15
    output.loc[output["ticker"].eq("HD"), "espp_purchase_frequency"] = "approximately_every_6_months"
    output.loc[output["ticker"].eq("HD"), "espp_purchase_frequency_months"] = 6.0
    output.loc[output["espp_enabled"], "is_espp"] = True
    output.loc[output["espp_enabled"] & output["espp_discount_pct"].le(0), "espp_discount_pct"] = output.loc[output["espp_enabled"], "espp_discount"] * 100.0
    output["espp_purchase_frequency"] = output["espp_purchase_frequency"].fillna("").astype(str)

    output["espp_acquired_shares"] = output[["shares", "espp_acquired_shares"]].min(axis=1)
    output["espp_acquired_shares"] = output["espp_acquired_shares"].clip(lower=0.0)
    hd_espp_shares_mask = output["ticker"].eq("HD") & output["espp_acquired_shares"].le(0)
    if hd_espp_shares_mask.any():
        output.loc[hd_espp_shares_mask, "espp_acquired_shares"] = output.loc[hd_espp_shares_mask, "shares"]

    hd_mask = output["ticker"].eq("HD")
    if hd_mask.any():
        output.loc[hd_mask, "regular_biweekly_contribution"] = 0.0
        output.loc[hd_mask, "special_contribution"] = 0.0
        output.loc[hd_mask & output["espp_biweekly_contribution"].le(0), "espp_biweekly_contribution"] = ESPP_CONTRIB_BIWEEKLY
        output.loc[hd_mask & output["espp_contribution_biweekly"].le(0), "espp_contribution_biweekly"] = ESPP_CONTRIB_BIWEEKLY
        output.loc[hd_mask, "espp_biweekly_contribution"] = output.loc[hd_mask, "espp_contribution_biweekly"]

    if drop_blank_tickers:
        output = output[output["ticker"].ne("")].copy()
    return output.reset_index(drop=True)


def _load_saved_holdings() -> pd.DataFrame:
    if _use_export_json_data():
        export_holdings = _export_holdings_df()
        if not export_holdings.empty:
            normalized_export = _normalize_columns(export_holdings)
            if not normalized_export.empty:
                return normalized_export

    if DEFAULT_DB_PATH.exists():
        try:
            with sqlite3.connect(DEFAULT_DB_PATH) as conn:
                _ensure_holdings_table_schema(conn)
                df = pd.read_sql_query(f"SELECT * FROM {HOLDINGS_TABLE}", conn)
            normalized = _normalize_columns(df)
            existing_columns = {str(col).strip().lower() for col in df.columns}
            required_columns = {col.lower() for col in HOLDINGS_SCHEMA_COLUMNS}
            if required_columns - existing_columns:
                # Rewrite storage into the current schema during migration.
                _save_holdings(normalized)
            if not normalized.empty:
                log_event(LOGGER, "database_loaded", "Portfolio database loaded", active_holdings_count=len(normalized))
                return normalized
        except Exception:
            pass

    # One-time migration path from legacy CSV storage.
    if LEGACY_CSV_PATH.exists():
        try:
            legacy_df = pd.read_csv(LEGACY_CSV_PATH)
            normalized_legacy = _normalize_columns(legacy_df)
            if not normalized_legacy.empty:
                _save_holdings(normalized_legacy)
            return normalized_legacy
        except Exception:
            return pd.DataFrame(columns=REQUIRED_COLUMNS)

    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def _ensure_holdings_table_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HOLDINGS_TABLE} (
            ticker TEXT,
            shares REAL DEFAULT 0,
            amount_invested REAL DEFAULT 0,
            annual_div_per_share REAL DEFAULT 0,
            regular_biweekly_contribution REAL DEFAULT 0,
            espp_biweekly_contribution REAL DEFAULT 0,
            espp_contribution_biweekly REAL DEFAULT 0,
            special_contribution REAL DEFAULT 0,
            employer_stock INTEGER DEFAULT 0,
            espp_enabled INTEGER DEFAULT 0,
            is_espp INTEGER DEFAULT 0,
            espp_discount REAL DEFAULT 0,
            espp_discount_pct REAL DEFAULT 0,
            espp_purchase_frequency_months REAL DEFAULT 6,
            espp_purchase_frequency TEXT DEFAULT '',
            estimated_annual_distribution_per_share REAL DEFAULT 0,
            annualized_income REAL DEFAULT 0,
            espp_acquired_shares REAL DEFAULT 0,
            effective_annual_div_per_share REAL DEFAULT 0,
            reported_annual_div_per_share REAL DEFAULT 0,
            dividend_yield_pct REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            current_price REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            unrealized_gain REAL DEFAULT 0,
            portfolio_yield_pct REAL DEFAULT 0,
            sector TEXT,
            asset_type TEXT
        )
        """
    )

    existing_columns = {
        str(row[1]).strip().lower()
        for row in conn.execute(f"PRAGMA table_info({HOLDINGS_TABLE})").fetchall()
    }
    required_columns = {
        "regular_biweekly_contribution": "REAL DEFAULT 0",
        "espp_biweekly_contribution": "REAL DEFAULT 0",
        "espp_contribution_biweekly": "REAL DEFAULT 0",
        "special_contribution": "REAL DEFAULT 0",
        "employer_stock": "INTEGER DEFAULT 0",
        "espp_enabled": "INTEGER DEFAULT 0",
        "is_espp": "INTEGER DEFAULT 0",
        "espp_discount": "REAL DEFAULT 0",
        "espp_discount_pct": "REAL DEFAULT 0",
        "espp_purchase_frequency_months": "REAL DEFAULT 6",
        "espp_purchase_frequency": "TEXT DEFAULT ''",
        "estimated_annual_distribution_per_share": "REAL DEFAULT 0",
        "annualized_income": "REAL DEFAULT 0",
        "espp_acquired_shares": "REAL DEFAULT 0",
        "effective_annual_div_per_share": "REAL DEFAULT 0",
        "reported_annual_div_per_share": "REAL DEFAULT 0",
        "dividend_yield_pct": "REAL DEFAULT 0",
        "avg_cost": "REAL DEFAULT 0",
        "current_price": "REAL DEFAULT 0",
        "market_value": "REAL DEFAULT 0",
        "unrealized_gain": "REAL DEFAULT 0",
        "portfolio_yield_pct": "REAL DEFAULT 0",
    }
    for column_name, column_type in required_columns.items():
        if column_name in existing_columns:
            continue
        conn.execute(
            f"ALTER TABLE {HOLDINGS_TABLE} ADD COLUMN {column_name} {column_type}"
        )


def _save_holdings(df: pd.DataFrame) -> None:
    normalized = _normalize_columns(df, drop_blank_tickers=True)
    with sqlite3.connect(DEFAULT_DB_PATH) as conn:
        _ensure_holdings_table_schema(conn)
        conn.execute(f"DELETE FROM {HOLDINGS_TABLE}")
        normalized.to_sql(HOLDINGS_TABLE, conn, if_exists="append", index=False)
    SupplementalResearchService(DEFAULT_DB_PATH).sync_holdings(normalized["ticker"].tolist())
    log_event(LOGGER, "holdings_saved", "Holdings saved", active_holdings_count=len(normalized))


def _normalize_yahoo_tickers(tickers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()}))


@st.cache_data(ttl=PRICE_CACHE_TTL_SECONDS)
def _latest_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    normalized_tickers = _normalize_yahoo_tickers(tickers)
    if not normalized_tickers:
        return {}

    prices = {ticker: 0.0 for ticker in normalized_tickers}
    unresolved = set(normalized_tickers)
    started_at = time.perf_counter()
    log_event(LOGGER, "yahoo_price_batch_started", "Yahoo batch price retrieval started", requested_tickers_count=len(normalized_tickers))

    try:
        batch_history = yf.download(
            list(normalized_tickers),
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="column",
            threads=True,
        )
        if isinstance(batch_history, pd.DataFrame) and not batch_history.empty:
            close_columns: dict[str, object] = {}
            if isinstance(batch_history.columns, pd.MultiIndex):
                for column in batch_history.columns:
                    parts = [str(part).strip().upper() for part in column]
                    if "CLOSE" not in parts:
                        continue
                    ticker_part = next((part for part in parts if part in normalized_tickers), "")
                    if ticker_part:
                        close_columns[ticker_part] = column
            else:
                if len(normalized_tickers) == 1:
                    close_columns[normalized_tickers[0]] = "Close" if "Close" in batch_history.columns else None
                else:
                    for column in batch_history.columns:
                        ticker_part = str(column).strip().upper()
                        if ticker_part in normalized_tickers:
                            close_columns[ticker_part] = column

            for ticker, column in close_columns.items():
                if column is None:
                    continue
                values = pd.to_numeric(batch_history[column], errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
                if values.empty:
                    continue
                prices[ticker] = float(values.iloc[-1])
                unresolved.discard(ticker)
    except Exception:
        pass

    log_event(
        LOGGER,
        "yahoo_price_batch_completed",
        "Yahoo batch price retrieval completed",
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        requested_tickers_count=len(normalized_tickers),
        resolved_tickers_count=len(normalized_tickers) - len(unresolved),
        fallback_tickers_count=len(unresolved),
        status="completed",
    )

    fallback_started_at = time.perf_counter()
    if unresolved:
        log_event(LOGGER, "yahoo_price_fallback_started", "Yahoo per-ticker price fallback started", fallback_tickers_count=len(unresolved))
    for ticker in sorted(unresolved):
        try:
            history = yf.Ticker(ticker).history(period="5d")
            close_series = history.get("Close") if isinstance(history, pd.DataFrame) else None
            if close_series is None:
                continue
            close = pd.to_numeric(close_series, errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
            if not close.empty:
                prices[ticker] = float(close.iloc[-1])
        except Exception:
            pass

    if unresolved:
        log_event(
            LOGGER,
            "yahoo_price_fallback_completed",
            "Yahoo per-ticker price fallback completed",
            duration_ms=round((time.perf_counter() - fallback_started_at) * 1000),
            fallback_tickers_count=len(unresolved),
            unresolved_tickers=sorted(unresolved),
            status="completed",
        )

    return prices


@st.cache_data(ttl=YAHOO_METADATA_CACHE_TTL_SECONDS)
def _latest_yahoo_metadata(
    tickers: tuple[str, ...],
    include_fast_info: bool = False,
) -> dict[str, dict[str, object]]:
    normalized_tickers = _normalize_yahoo_tickers(tickers)
    metadata: dict[str, dict[str, object]] = {}
    started_at = time.perf_counter()
    log_event(LOGGER, "yahoo_metadata_cache_miss_started", "Yahoo metadata cache miss retrieval started", requested_tickers_count=len(normalized_tickers))
    for ticker in normalized_tickers:
        try:
            proxy = yf.Ticker(ticker)
            info = getattr(proxy, "info", {}) or {}
            if not isinstance(info, dict):
                info = dict(info)
            record: dict[str, object] = {"info": _sanitize_export_value(dict(info))}
            if include_fast_info:
                fast_info = getattr(proxy, "fast_info", {}) or {}
                record["fast_info"] = _sanitize_export_value(dict(fast_info)) if hasattr(fast_info, "items") else {}
            metadata[ticker] = record
        except Exception:
            metadata[ticker] = {}
    log_event(
        LOGGER,
        "yahoo_metadata_cache_miss_completed",
        "Yahoo metadata cache miss retrieval completed",
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        requested_tickers_count=len(normalized_tickers),
        resolved_tickers_count=sum(bool(record) for record in metadata.values()),
        status="completed",
    )
    return metadata


@st.cache_data(ttl=YAHOO_METADATA_CACHE_TTL_SECONDS)
def _latest_sectors(tickers: tuple[str, ...]) -> dict[str, str]:
    normalized_tickers = _normalize_yahoo_tickers(tickers)
    if not normalized_tickers:
        return {}

    metadata = _latest_yahoo_metadata(normalized_tickers)
    return {ticker: _infer_sector_from_info(metadata.get(ticker, {}).get("info", {})) for ticker in normalized_tickers}


@st.cache_data(ttl=YAHOO_METADATA_CACHE_TTL_SECONDS)
def _latest_dividend_yields(tickers: tuple[str, ...]) -> dict[str, float]:
    """Return annual dividend yield percentages by ticker, e.g. 3.25 for 3.25%."""
    normalized_tickers = _normalize_yahoo_tickers(tickers)
    if not normalized_tickers:
        return {}

    metadata = _latest_yahoo_metadata(normalized_tickers)
    yields: dict[str, float] = {}
    for ticker in normalized_tickers:
        try:
            info = metadata.get(ticker, {}).get("info", {})
            raw_yield = info.get("trailingAnnualDividendYield")
            fallback_yield = info.get("dividendYield")

            if raw_yield is None:
                raw_yield = fallback_yield
            elif raw_yield in {0, 0.0} and fallback_yield not in {None, 0, 0.0}:
                raw_yield = fallback_yield

            if raw_yield is None:
                yields[ticker] = 0.0
            else:
                value = float(raw_yield)
                # Yahoo can return decimal form (0.082) or percent form (8.2).
                if value < 0:
                    value = 0.0
                elif value <= 1:
                    value = value * 100

                if value > 50:
                    value = 0.0
                yields[ticker] = value
        except Exception:
            yields[ticker] = 0.0

    return yields


@st.cache_data(ttl=DIVIDEND_TIMING_CACHE_TTL_SECONDS)
def _latest_dividend_month_weights(tickers: tuple[str, ...]) -> dict[str, list[float]]:
    """Return per-ticker month weights (12 values summing to 1.0) from dividend history."""
    if not tickers:
        return {}

    month_weights: dict[str, list[float]] = {}
    lookback_start = datetime.now() - timedelta(days=730)

    for ticker in tickers:
        try:
            dividends = yf.Ticker(ticker).dividends
            if dividends is None or dividends.empty:
                month_weights[ticker] = [0.0] * 12
                continue

            recent = dividends[dividends.index >= lookback_start]
            if recent.empty:
                month_weights[ticker] = [0.0] * 12
                continue

            by_month = recent.groupby(recent.index.month).sum()
            total = float(by_month.sum())
            if total <= 0:
                month_weights[ticker] = [0.0] * 12
                continue

            weights = []
            for month in range(1, 13):
                month_amt = float(by_month.get(month, 0.0))
                weights.append(month_amt / total)
            month_weights[ticker] = weights
        except Exception:
            month_weights[ticker] = [0.0] * 12

    return month_weights


def _infer_asset_type_from_info(info: dict) -> str:
    quote_type = str(info.get("quoteType") or "").strip().lower()
    industry = str(info.get("industry") or "").strip().lower()
    long_name = str(info.get("longName") or "").strip().lower()
    category = str(info.get("category") or "").strip().lower()

    text_blob = f"{industry} {long_name} {category}"

    if "reit" in text_blob:
        return "REIT"
    if "closed-end" in text_blob or "closed end" in text_blob:
        return "CEF"
    if "business development" in text_blob or "bdc" in text_blob:
        return "BDC"
    if "bond" in text_blob:
        return "Bond Fund"
    if quote_type in {"equity", "commonstock", "stock"}:
        return "Stock"
    if quote_type in {"etf", "mutualfund", "fund"}:
        return "ETF"
    return "Stock"


@st.cache_data(ttl=YAHOO_METADATA_CACHE_TTL_SECONDS)
def _latest_asset_type_hints(tickers: tuple[str, ...]) -> dict[str, str]:
    normalized_tickers = _normalize_yahoo_tickers(tickers)
    if not normalized_tickers:
        return {}

    metadata = _latest_yahoo_metadata(normalized_tickers)
    hints: dict[str, str] = {}
    for ticker in normalized_tickers:
        hints[ticker] = _infer_asset_type_from_info(metadata.get(ticker, {}).get("info", {}))
    return hints


@st.cache_data(ttl=HISTORICAL_CONTEXT_CACHE_TTL_SECONDS)
def _latest_historical_context(
    tickers: tuple[str, ...],
    history_start_date: str | None = None,
) -> dict[str, dict[str, object]]:
    """Return lightweight historical trend context for each ticker for AI recommendations."""
    if not tickers:
        return {}

    context_map: dict[str, dict[str, object]] = {}
    for ticker in tickers:
        try:
            proxy = yf.Ticker(ticker)
            history_kwargs = {"period": "2y"}
            if history_start_date:
                try:
                    parsed_start = datetime.fromisoformat(history_start_date)
                    history_kwargs = {"start": parsed_start.date().isoformat(), "end": datetime.now().date().isoformat()}
                except ValueError:
                    history_kwargs = {"period": "2y"}

            history = proxy.history(**history_kwargs)
            if history.empty:
                context_map[ticker] = {
                    "ticker": ticker,
                    "price_trend": {},
                    "dividend_trend": {},
                    "start_price": None,
                    "end_price": None,
                    "return_pct": None,
                    "52w_high": None,
                    "52w_low": None,
                    "drawdown": None,
                    "volatility": None,
                    "history_source": "Yahoo Finance",
                    "history_type": "supplemental",
                    "history_start_date": history_start_date,
                    "history_summary": "No recent Yahoo price history available.",
                }
                continue

            close = pd.to_numeric(history["Close"], errors="coerce").dropna()
            if close.empty:
                context_map[ticker] = {
                    "ticker": ticker,
                    "price_trend": {},
                    "dividend_trend": {},
                    "start_price": None,
                    "end_price": None,
                    "return_pct": None,
                    "52w_high": None,
                    "52w_low": None,
                    "drawdown": None,
                    "volatility": None,
                    "history_source": "Yahoo Finance",
                    "history_type": "supplemental",
                    "history_start_date": history_start_date,
                    "history_summary": "No recent Yahoo price history available.",
                }
                continue

            latest = float(close.iloc[-1])
            start_price = float(close.iloc[0])
            three_month_ago = close.iloc[-63] if len(close) >= 63 else close.iloc[0]
            one_year_ago = close.iloc[-252] if len(close) >= 252 else close.iloc[0]
            three_month_change = ((latest - float(three_month_ago)) / float(three_month_ago) * 100) if float(three_month_ago) else 0.0
            one_year_change = ((latest - float(one_year_ago)) / float(one_year_ago) * 100) if float(one_year_ago) else 0.0
            return_pct = ((latest - start_price) / start_price * 100) if start_price else 0.0

            trailing_52w = close.iloc[-252:] if len(close) >= 252 else close
            high_52w = float(trailing_52w.max()) if not trailing_52w.empty else latest
            low_52w = float(trailing_52w.min()) if not trailing_52w.empty else latest

            rolling_peak = close.cummax()
            drawdowns = ((close / rolling_peak) - 1.0) * 100
            max_drawdown = float(drawdowns.min()) if not drawdowns.empty else 0.0

            returns = close.pct_change().dropna()
            volatility = float(returns.std(ddof=0) * 100) if not returns.empty else 0.0

            dividend_history = proxy.dividends
            dividend_trend: dict[str, float] = {}
            if dividend_history is not None and not dividend_history.empty:
                recent_dividends = dividend_history[dividend_history.index >= datetime.now() - timedelta(days=365)]
                if not recent_dividends.empty:
                    dividend_trend["change_1y_pct"] = round(float(recent_dividends.iloc[-1] - recent_dividends.iloc[0]) / max(float(recent_dividends.iloc[0]), 1e-9) * 100, 2)

            history_summary = (
                f"Supplemental Yahoo Finance history is available from {history.index[0].date().isoformat()} onward."
                + f" Price is {('up' if latest >= float(close.iloc[0]) else 'down')} over the available history."
                + (f" 3-month change: {three_month_change:.2f}%." if dividend_trend else "")
            )

            context_map[ticker] = {
                "ticker": ticker,
                "price_trend": {
                    "change_3m_pct": round(three_month_change, 2),
                    "change_1y_pct": round(one_year_change, 2),
                },
                "dividend_trend": dividend_trend,
                "start_price": round(start_price, 4),
                "end_price": round(latest, 4),
                "return_pct": round(return_pct, 2),
                "52w_high": round(high_52w, 4),
                "52w_low": round(low_52w, 4),
                "drawdown": round(max_drawdown, 2),
                "volatility": round(volatility, 2),
                "history_source": "Yahoo Finance",
                "history_type": "supplemental",
                "history_start_date": history_start_date,
                "history_summary": history_summary,
            }
        except Exception:
            context_map[ticker] = {
                "ticker": ticker,
                "price_trend": {},
                "dividend_trend": {},
                "start_price": None,
                "end_price": None,
                "return_pct": None,
                "52w_high": None,
                "52w_low": None,
                "drawdown": None,
                "volatility": None,
                "history_source": "Yahoo Finance",
                "history_type": "supplemental",
                "history_start_date": history_start_date,
                "history_summary": "Historical context unavailable.",
            }

    return context_map


def _coerce_optional_number(value: object) -> float | None:
    return _coerce_finite_number(value)


def _is_fund_like_quote_type(quote_type: object) -> bool:
    normalized = str(quote_type or "").strip().upper()
    return normalized in {"ETF", "MUTUALFUND", "INDEX", "FUND"}


def _extract_etf_intelligence(ticker_obj: object, fast_info: object | None = None) -> dict[str, object] | None:
    try:
        funds_data = getattr(ticker_obj, "funds_data", None)
        if funds_data is None:
            return None

        fast_info = fast_info if fast_info is not None else getattr(ticker_obj, "fast_info", {}) or {}
        if hasattr(fast_info, "get"):
            market_cap = fast_info.get("marketCap")
            last_price = fast_info.get("lastPrice")
        else:
            market_cap = None
            last_price = None

        def _normalize_value(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, pd.DataFrame):
                if value.empty:
                    return []
                return value.head(5).to_dict(orient="records")
            if isinstance(value, dict):
                return {str(k): v for k, v in value.items() if v is not None and v != ""}
            if isinstance(value, str):
                cleaned = value.strip()
                return cleaned if cleaned else None
            if isinstance(value, (int, float)):
                numeric_value = _coerce_optional_number(value)
                return numeric_value
            return value

        payload = {
            "description": _normalize_value(getattr(funds_data, "description", None)),
            "top_holdings": _normalize_value(getattr(funds_data, "top_holdings", None)),
            "equity_holdings": _normalize_value(getattr(funds_data, "equity_holdings", None)),
            "bond_holdings": _normalize_value(getattr(funds_data, "bond_holdings", None)),
            "bond_ratings": _normalize_value(getattr(funds_data, "bond_ratings", None)),
            "sector_weightings": _normalize_value(getattr(funds_data, "sector_weightings", None)),
            "fund_operations": _normalize_value(getattr(funds_data, "fund_operations", None)),
            "market_cap": _coerce_optional_number(market_cap),
            "last_price": _coerce_optional_number(last_price),
        }
        return {key: value for key, value in payload.items() if value not in {None, "", [], {}, ()}}
    except Exception:
        return None


@st.cache_data(ttl=INVESTMENT_INTELLIGENCE_CACHE_TTL_SECONDS)
def _latest_investment_intelligence(tickers: tuple[str, ...]) -> dict[str, dict[str, object]]:
    normalized_tickers = _normalize_yahoo_tickers(tickers)
    if not normalized_tickers:
        return {}

    metadata = _latest_yahoo_metadata(normalized_tickers, include_fast_info=True)
    started_at = time.perf_counter()
    log_event(LOGGER, "investment_intelligence_cache_miss_started", "Investment Intelligence cache miss retrieval started", requested_tickers_count=len(normalized_tickers))
    intelligence_map: dict[str, dict[str, object]] = {}
    for ticker in normalized_tickers:
        try:
            proxy = yf.Ticker(ticker)
            ticker_metadata = metadata.get(ticker, {})
            info = ticker_metadata.get("info", {})
            fast_info = ticker_metadata.get("fast_info", {})

            if not info and not fast_info:
                continue

            quote_type = str(info.get("quoteType", "") or "").strip().upper()
            if _is_fund_like_quote_type(quote_type):
                etf_intelligence = _extract_etf_intelligence(proxy, fast_info=fast_info)
                if etf_intelligence:
                    intelligence_map[ticker] = {
                        "ticker": ticker,
                        "type": "Fund",
                        "fund_data": etf_intelligence,
                        "data_source": "Yahoo Finance",
                    }
                continue

            fundamental_metrics: dict[str, object] = {}
            for key, source_key in [
                ("pe_ratio", "forwardPE"),
                ("trailing_pe_ratio", "trailingPE"),
                ("price_to_book", "priceToBook"),
                ("profit_margin", "profitMargins"),
                ("operating_margin", "operatingMargins"),
                ("return_on_equity", "returnOnEquity"),
                ("debt_to_equity", "debtToEquity"),
                ("market_cap", "marketCap"),
                ("current_price", "currentPrice"),
            ]:
                value = info.get(source_key)
                if value is None and key == "current_price":
                    value = fast_info.get("lastPrice")
                if value is None and key == "market_cap":
                    value = fast_info.get("marketCap")
                if value is not None:
                    numeric_value = _coerce_optional_number(value)
                    if numeric_value is not None:
                        fundamental_metrics[key] = round(numeric_value, 4)

            dividend_yield = info.get("dividendYield")
            if dividend_yield is not None:
                numeric_dividend_yield = _coerce_optional_number(dividend_yield)
                if numeric_dividend_yield is not None:
                    fundamental_metrics["dividend_yield"] = round(numeric_dividend_yield, 4)

            earnings_estimates: dict[str, object] = {}
            earnings_estimate = getattr(proxy, "earnings_estimate", None)
            if isinstance(earnings_estimate, dict):
                avg_est = earnings_estimate.get("avg") or {}
                growth_est = earnings_estimate.get("growth") or {}
                analyst_count = earnings_estimate.get("numberOfAnalysts") or {}
                if isinstance(avg_est, dict):
                    earnings_estimates["avg_eps_estimate"] = _coerce_optional_number(avg_est.get("+1y"))
                if isinstance(growth_est, dict):
                    earnings_estimates["earnings_growth_pct"] = _coerce_optional_number(growth_est.get("+1y"))
                if isinstance(analyst_count, dict):
                    earnings_estimates["analyst_count"] = _coerce_optional_number(analyst_count.get("+1y"))

            revenue_estimate = getattr(proxy, "revenue_estimate", None)
            if isinstance(revenue_estimate, dict):
                avg_rev = revenue_estimate.get("avg") or {}
                growth_rev = revenue_estimate.get("growth") or {}
                analyst_count = revenue_estimate.get("numberOfAnalysts") or {}
                if isinstance(avg_rev, dict):
                    earnings_estimates["avg_revenue_estimate"] = _coerce_optional_number(avg_rev.get("+1y"))
                if isinstance(growth_rev, dict):
                    earnings_estimates["revenue_growth_pct"] = _coerce_optional_number(growth_rev.get("+1y"))
                if isinstance(analyst_count, dict):
                    earnings_estimates["revenue_analyst_count"] = _coerce_optional_number(analyst_count.get("+1y"))

            analyst_targets: dict[str, object] = {}
            price_target = getattr(proxy, "analyst_price_targets", None)
            if isinstance(price_target, dict):
                if "targetMeanPrice" in price_target:
                    analyst_targets["target_mean_price"] = _coerce_optional_number(price_target.get("targetMeanPrice"))
                if "recommendationMean" in price_target:
                    analyst_targets["recommendation_mean"] = _coerce_optional_number(price_target.get("recommendationMean"))

            recommendations = getattr(proxy, "recommendations", None)
            if isinstance(recommendations, pd.DataFrame) and not recommendations.empty:
                first_row = recommendations.iloc[0].to_dict()
                if "To Grade" in first_row:
                    analyst_targets["recommendation_grade"] = first_row.get("To Grade")

            fund_data: dict[str, object] = {}
            funds_data = getattr(proxy, "funds_data", None)
            if funds_data is not None:
                fund_data["description"] = getattr(funds_data, "description", "") or ""
                for field in ["top_holdings", "equity_holdings", "bond_holdings", "bond_ratings", "sector_weightings", "fund_operations"]:
                    value = getattr(funds_data, field, None)
                    if value is None:
                        continue
                    if isinstance(value, pd.DataFrame):
                        if value.empty:
                            fund_data[field] = []
                        else:
                            fund_data[field] = value.head(5).to_dict(orient="records")
                    elif isinstance(value, dict):
                        fund_data[field] = value
                    else:
                        fund_data[field] = value

            if not fundamental_metrics and not earnings_estimates and not analyst_targets and not fund_data:
                continue

            if not fundamental_metrics and not earnings_estimates and not analyst_targets and not fund_data:
                continue

            intelligence_map[ticker] = {
                "ticker": ticker,
                "type": "Stock",
                "fundamental_metrics": fundamental_metrics,
                "earnings_and_estimates": earnings_estimates,
                "analyst_targets": analyst_targets,
                "fund_data": fund_data,
                "data_source": "Yahoo Finance",
            }
        except Exception:
            continue

    log_event(
        LOGGER,
        "investment_intelligence_cache_miss_completed",
        "Investment Intelligence cache miss retrieval completed",
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        requested_tickers_count=len(normalized_tickers),
        resolved_tickers_count=len(intelligence_map),
        status="completed",
    )
    return intelligence_map


def _compute_metrics(df: pd.DataFrame, price_map: dict[str, float], yield_map: dict[str, float]) -> pd.DataFrame:
    def _as_float(value: object, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    result = _normalize_columns(df.copy(), drop_blank_tickers=False)
    result["ticker"] = _sanitize_ticker_series(result["ticker"])
    result["current_price"] = result["ticker"].map(price_map).fillna(0.0)
    result["dividend_yield_pct"] = result["ticker"].map(yield_map).fillna(0.0)
    result["cost_basis"] = result["amount_invested"]
    result["market_value"] = result["shares"] * result["current_price"]
    result["regular_biweekly_contribution"] = result.get("regular_biweekly_contribution", 0.0)
    result["espp_biweekly_contribution"] = result.get("espp_biweekly_contribution", 0.0)
    result["special_contribution"] = result.get("special_contribution", 0.0)
    result["employer_stock"] = result.get("employer_stock", False)
    result["espp_enabled"] = result.get("espp_enabled", False)
    result["espp_purchase_frequency"] = result.get("espp_purchase_frequency", "")
    discount_source = result.get("espp_discount_pct")
    if discount_source is None:
        discount_source = result.get("espp_discount")
    if discount_source is None:
        discount_source = pd.Series([ESPP_DISCOUNT] * len(result), index=result.index, dtype=float)
    result["espp_discount_pct"] = pd.Series(discount_source, index=result.index).apply(
        lambda value: _normalize_percentage_value(value, ESPP_DISCOUNT * 100.0)
    )
    result["espp_acquired_shares"] = result.get("espp_acquired_shares", 0.0)
    result["espp_acquired_shares"] = pd.to_numeric(result["espp_acquired_shares"], errors="coerce").fillna(0.0)
    result["espp_acquired_shares"] = result[["shares", "espp_acquired_shares"]].min(axis=1).clip(lower=0.0)
    result["regular_shares"] = (result["shares"] - result["espp_acquired_shares"]).clip(lower=0.0)
    result["current_value"] = result["market_value"]
    result["avg_cost"] = (result["amount_invested"] / result["shares"].replace(0, pd.NA)).fillna(0.0)
    result["est_annual_div_per_share"] = result["current_price"] * result["dividend_yield_pct"] / 100
    result["effective_annual_div_per_share"] = result["annual_div_per_share"]
    blank_manual = result["effective_annual_div_per_share"] <= 0
    result.loc[blank_manual, "effective_annual_div_per_share"] = result.loc[blank_manual, "est_annual_div_per_share"]
    result["reported_annual_div_per_share"] = result["annual_div_per_share"].where(result["annual_div_per_share"] > 0, None)

    for idx, row in result.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        resolved_price = resolve_value("current_price", ticker, row.get("current_price", 0.0))
        resolved_yield = resolve_value("dividend_yield_pct", ticker, row.get("dividend_yield_pct", 0.0))
        resolved_sector = resolve_value("sector", ticker, row.get("sector", ""))
        resolved_asset_type = resolve_value("asset_type", ticker, row.get("asset_type", "Stock"))
        resolved_effective_div = resolve_value("effective_annual_div_per_share", ticker, row.get("effective_annual_div_per_share", 0.0))
        resolved_reported_div = resolve_value("reported_annual_div_per_share", ticker, row.get("reported_annual_div_per_share", row.get("annual_div_per_share", 0.0)))

        result.at[idx, "current_price"] = _as_float(resolved_price, _as_float(row.get("current_price", 0.0)))
        result.at[idx, "dividend_yield_pct"] = _as_float(resolved_yield, _as_float(row.get("dividend_yield_pct", 0.0)))
        result.at[idx, "sector"] = str(resolved_sector or row.get("sector", ""))
        result.at[idx, "asset_type"] = _canonical_asset_type(str(resolved_asset_type or row.get("asset_type", "Stock")))
        if resolved_effective_div not in [None, ""]:
            result.at[idx, "effective_annual_div_per_share"] = _as_float(resolved_effective_div, _as_float(row.get("effective_annual_div_per_share", 0.0)))
        if resolved_reported_div not in [None, ""]:
            result.at[idx, "reported_annual_div_per_share"] = _as_float(resolved_reported_div, _as_float(row.get("annual_div_per_share", 0.0)))

    if _strict_export_accuracy_mode():
        # Strict mode disables inferred dividend estimation.
        result["est_annual_div_per_share"] = 0.0
        missing_effective = pd.to_numeric(result["effective_annual_div_per_share"], errors="coerce").fillna(0.0).le(0)
        result.loc[missing_effective, "effective_annual_div_per_share"] = 0.0

    result["market_value"] = result["shares"] * result["current_price"]
    result["unrealized_gain"] = result["market_value"] - result["cost_basis"]
    result["annual_income"] = result["shares"] * result["effective_annual_div_per_share"]
    result["estimated_annual_distribution_per_share"] = result["effective_annual_div_per_share"]
    result["annualized_income"] = result["annual_income"]

    for idx, row in result.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        resolved_annual_income = resolve_value("annualized_income", ticker, row.get("annualized_income", row.get("annual_income", 0.0)))
        if resolved_annual_income not in [None, ""]:
            result.at[idx, "annualized_income"] = _as_float(resolved_annual_income, _as_float(row.get("annualized_income", 0.0)))
            result.at[idx, "annual_income"] = _as_float(resolved_annual_income, _as_float(row.get("annual_income", 0.0)))
    result["yield_on_cost_pct"] = (
        (result["effective_annual_div_per_share"] / result["avg_cost"].replace(0, pd.NA)).fillna(0) * 100
    )
    total_portfolio_value = float(result["market_value"].sum()) if not result.empty else 0.0
    result["portfolio_yield_pct"] = (
        (result["annualized_income"] / total_portfolio_value).fillna(0) * 100
        if total_portfolio_value > 0
        else 0.0
    )

    reported_dividend_values = result.get("reported_annual_div_per_share")
    employer_stock_values = result.get("employer_stock")
    espp_enabled_values = result.get("espp_enabled")
    espp_purchase_frequency_values = result.get("espp_purchase_frequency")
    espp_discount_pct_values = result.get("espp_discount_pct")
    result = result.fillna(0)
    if reported_dividend_values is not None:
        result["reported_annual_div_per_share"] = reported_dividend_values.where(reported_dividend_values.notna(), None)
    if employer_stock_values is not None:
        result["employer_stock"] = employer_stock_values
    if espp_enabled_values is not None:
        result["espp_enabled"] = espp_enabled_values
    if espp_purchase_frequency_values is not None:
        result["espp_purchase_frequency"] = espp_purchase_frequency_values.where(espp_purchase_frequency_values.notna(), "")
    if espp_discount_pct_values is not None:
        result["espp_discount_pct"] = pd.to_numeric(espp_discount_pct_values, errors="coerce").fillna(0.0)

    total_portfolio_income = float(result["annual_income"].sum()) if not result.empty else 0.0
    total_portfolio_value = float(result["market_value"].sum()) if not result.empty else 0.0
    result["income_allocation_pct"] = (
        result["annual_income"] / total_portfolio_income if total_portfolio_income > 0 else 0.0
    )
    result["portfolio_weight_pct"] = (
        result["market_value"] / total_portfolio_value if total_portfolio_value > 0 else 0.0
    )
    return result


def _build_portfolio_level_calculations(metrics: pd.DataFrame) -> dict[str, object]:
    if metrics.empty:
        return {
            "total_portfolio_value": 0.0,
            "total_cost_basis": 0.0,
            "total_unrealized_gain": 0.0,
            "total_estimated_annual_income": 0.0,
            "weighted_portfolio_yield": 0.0,
            "largest_holding": "N/A",
            "largest_holding_weight": 0.0,
            "largest_income_contributor": "N/A",
            "income_allocation_by_asset_type": [],
            "economic_exposure_allocation": [],
        }

    totals = {}
    metrics = metrics.copy()
    if "asset_type" not in metrics.columns:
        metrics["asset_type"] = "Other"
    totals["total_portfolio_value"] = float(pd.to_numeric(metrics["market_value"], errors="coerce").fillna(0.0).sum())
    if "cost_basis" in metrics.columns:
        cost_basis_series = pd.to_numeric(metrics["cost_basis"], errors="coerce").fillna(0.0)
    elif "amount_invested" in metrics.columns:
        cost_basis_series = pd.to_numeric(metrics["amount_invested"], errors="coerce").fillna(0.0)
    else:
        cost_basis_series = pd.Series(0.0, index=metrics.index)
    totals["total_cost_basis"] = float(cost_basis_series.sum())
    if "unrealized_gain" in metrics.columns:
        unrealized_series = pd.to_numeric(metrics["unrealized_gain"], errors="coerce").fillna(0.0)
    else:
        unrealized_series = pd.Series(0.0, index=metrics.index)
    totals["total_unrealized_gain"] = float(unrealized_series.sum())
    income_source = "annualized_income" if "annualized_income" in metrics.columns else "annual_income"
    totals["total_estimated_annual_income"] = float(pd.to_numeric(metrics.get(income_source, 0.0), errors="coerce").fillna(0.0).sum())
    totals["weighted_portfolio_yield"] = (
        totals["total_estimated_annual_income"] / totals["total_portfolio_value"] * 100
        if totals["total_portfolio_value"] > 0
        else 0.0
    )

    by_value = metrics.sort_values("market_value", ascending=False)
    by_income = metrics.sort_values("annualized_income" if "annualized_income" in metrics.columns else "annual_income", ascending=False)
    top_value_row = by_value.iloc[0]
    top_income_row = by_income.iloc[0]
    totals["largest_holding"] = str(top_value_row.get("ticker", "N/A"))
    totals["largest_holding_weight"] = (
        float(top_value_row.get("market_value", 0.0) or 0.0) / totals["total_portfolio_value"] * 100
        if totals["total_portfolio_value"] > 0
        else 0.0
    )
    totals["largest_income_contributor"] = str(top_income_row.get("ticker", "N/A"))

    income_metric_col = "annualized_income" if "annualized_income" in metrics.columns else "annual_income"
    asset_alloc = metrics.groupby("asset_type", dropna=False).agg(annualized_income=(income_metric_col, "sum")).reset_index()
    total_income = float(asset_alloc["annualized_income"].sum()) if not asset_alloc.empty else 0.0
    asset_alloc["income_allocation_pct"] = asset_alloc["annualized_income"] / total_income * 100 if total_income > 0 else 0.0
    totals["income_allocation_by_asset_type"] = asset_alloc.to_dict(orient="records")

    econ = metrics.copy()
    econ["economic_exposure_category"] = econ["asset_type"].replace(
        {"Stock": "Equities", "ETF": "Equities", "REIT": "REITs", "CEF": "Alternative Income", "BDC": "Alternative Income", "Bond Fund": "Bonds", "Other": "Other"}
    )
    econ = econ.groupby("economic_exposure_category", dropna=False).agg(market_value=("market_value", "sum")).reset_index()
    total_mv = float(econ["market_value"].sum()) if not econ.empty else 0.0
    econ["portfolio_weight_pct"] = econ["market_value"] / total_mv * 100 if total_mv > 0 else 0.0
    totals["economic_exposure_allocation"] = econ.to_dict(orient="records")
    return totals


def evaluate_concentration(df: pd.DataFrame) -> dict[str, object]:
    if df.empty:
        return {
            "largest_holding_by_value": "N/A",
            "largest_income_contributor": "N/A",
            "espp_concentration_flag": False,
            "guidance": "No concentration data available.",
        }

    total_value = float(df["market_value"].sum()) if "market_value" in df.columns else 0.0
    total_income = float(df.get("annualized_income", df.get("annual_income", 0.0)).sum()) if not df.empty else 0.0
    top_value_row = df.sort_values("market_value", ascending=False).iloc[0]
    top_income_row = df.sort_values("annualized_income" if "annualized_income" in df.columns else "annual_income", ascending=False).iloc[0]
    top_ticker = str(top_value_row.get("ticker", "N/A"))
    employer_stock_flag = False
    if "employer_stock" in df.columns:
        employer_stock_flag = bool(df["employer_stock"].fillna(False).astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"}).any())
    espp_concentration_flag = employer_stock_flag or top_ticker == "HD"

    guidance = (
        "Employer-stock concentration detected. Apply ESPP-specific context (discount, purchase schedule, and employment exposure) and avoid automatic sell recommendations."
        if espp_concentration_flag
        else "Capital concentration and income concentration should be evaluated separately."
    )

    return {
        "largest_holding_by_value": top_ticker,
        "largest_holding_weight_pct": (float(top_value_row.get("market_value", 0.0) or 0.0) / total_value * 100) if total_value > 0 else 0.0,
        "largest_holding_income_weight_pct": (
            float(top_value_row.get("annualized_income", top_value_row.get("annual_income", 0.0)) or 0.0) / total_income * 100
        ) if total_income > 0 else 0.0,
        "largest_income_contributor": str(top_income_row.get("ticker", "N/A")),
        "largest_income_weight_pct": (float(top_income_row.get("annualized_income", top_income_row.get("annual_income", 0.0)) or 0.0) / total_income * 100) if total_income > 0 else 0.0,
        "espp_concentration_flag": espp_concentration_flag,
        "guidance": guidance,
    }


def resolve_fund_sector_weights(ticker: str) -> dict[str, float]:
    return _latest_fund_sector_weightings(ticker)


def _classify_income_quality(row: pd.Series) -> str:
    asset_type = _canonical_asset_type(str(row.get("asset_type", "")))
    ticker = str(row.get("ticker", "")).upper().strip()
    yield_pct = float(row.get("dividend_yield_pct", 0.0) or 0.0)

    if asset_type == "REIT":
        return "REIT distribution"
    if asset_type == "Bond Fund":
        return "Bond fund distribution"
    if asset_type == "ETF" and any(token in ticker for token in ["JEPI", "JEPQ", "QYLD", "XYLD", "RYLD", "DIVO"]):
        return "Option-income ETF distribution"
    if asset_type in {"ETF", "CEF", "BDC"}:
        return "Variable distribution"
    if yield_pct >= 7:
        return "Variable distribution"
    return "Stable dividend"


def _format_currency(value: float) -> str:
    return f"${value:,.2f}"


def _format_percent(value: float) -> str:
    return f"{value:,.2f}%"


def _derive_yahoo_historical_price_context(entry: dict[str, object]) -> dict[str, object] | None:
    ticker = str(entry.get("ticker", "UNKNOWN"))
    history = entry.get("historical_price_data") or []
    if not history:
        return None

    try:
        records = [dict(item) for item in history]
        if not records:
            return None

        closes = [float(item.get("close", 0.0) or 0.0) for item in records]
        starts = [float(item.get("open", 0.0) or 0.0) for item in records]
        start_price = float(starts[0]) if starts else 0.0
        end_price = float(closes[-1]) if closes else 0.0
        start_date = str(entry.get("date_range", {}).get("start", "")) if isinstance(entry.get("date_range"), dict) else ""
        end_date = str(entry.get("date_range", {}).get("end", "")) if isinstance(entry.get("date_range"), dict) else ""
        period_return = ((end_price / start_price) - 1) * 100 if start_price else 0.0

        dated_records = [(pd.to_datetime(item.get("date"), errors="coerce"), item) for item in records]
        valid_dates = [date for date, _ in dated_records if not pd.isna(date)]
        latest_record_date = max(valid_dates) if valid_dates else None
        trailing_year = [item for date, item in dated_records if latest_record_date is not None and not pd.isna(date) and date >= latest_record_date - pd.DateOffset(years=1)]
        range_records = trailing_year or records
        high_price = max(float(item.get("high", 0.0) or 0.0) for item in range_records)
        low_price = min(float(item.get("low", 0.0) or 0.0) for item in range_records)

        if len(records) >= 2:
            returns = [
                ((float(item.get("close", 0.0) or 0.0) / float(records[idx - 1].get("close", 0.0) or 0.0)) - 1) * 100
                for idx, item in enumerate(records)
                if idx > 0
            ]
            if returns:
                volatility = round(float(pd.Series(returns).std(ddof=0)), 2)
            else:
                volatility = 0.0
        else:
            volatility = 0.0

        drawdown = 0.0
        peak = None
        for item in records:
            close = float(item.get("close", 0.0) or 0.0)
            if peak is None or close > peak:
                peak = close
            if peak:
                current_drawdown = ((close / peak) - 1) * 100
                if current_drawdown < drawdown:
                    drawdown = current_drawdown

        return {
            "ticker": ticker,
            "start_date": start_date or None,
            "end_date": end_date or None,
            "start_price": round(start_price, 4),
            "end_price": round(end_price, 4),
            "return_pct": round(period_return, 4),
            "52w_high": round(high_price, 4),
            "52w_low": round(low_price, 4),
            "drawdown": round(drawdown, 4),
            "volatility": round(volatility, 4),
            "source": "Yahoo Finance",
            "history_type": "supplemental_per_security_price_history",
        }
    except Exception:
        return None


def _canonical_supplemental_history_record(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    derived = _derive_yahoo_historical_price_context(entry)
    if derived:
        return {**entry, **derived}

    ticker = str(entry.get("ticker", "")).strip().upper()
    start_price = _coerce_finite_number(entry.get("start_price"))
    end_price = _coerce_finite_number(entry.get("end_price"))
    if not ticker or start_price is None or end_price is None or start_price <= 0 or end_price <= 0:
        return None
    return {
        **entry,
        "ticker": ticker,
        "start_price": start_price,
        "end_price": end_price,
        "return_pct": _coerce_finite_number(entry.get("return_pct", entry.get("period_return_pct"))),
        "52w_high": _coerce_finite_number(entry.get("52w_high")),
        "52w_low": _coerce_finite_number(entry.get("52w_low")),
        "drawdown": _coerce_finite_number(entry.get("drawdown")),
        "volatility": _coerce_finite_number(entry.get("volatility")),
        "source": str(entry.get("source", "Yahoo Finance") or "Yahoo Finance"),
        "history_type": "supplemental_per_security_price_history",
    }


def _summarize_yahoo_historical_price_context(entry: dict[str, object]) -> str:
    derived = _canonical_supplemental_history_record(entry)
    ticker = str(entry.get("ticker", "UNKNOWN"))
    if not derived:
        return f"{ticker}: no supplemental Yahoo price history available."
    summary_parts = [
        f"{ticker}: supplemental Yahoo history {derived.get('start_date')} to {derived.get('end_date')}",
        f"start {_safe_float(derived.get('start_price')):.2f}, end {_safe_float(derived.get('end_price')):.2f}",
        f"period return {_safe_float(derived.get('return_pct')):.2f}%",
        f"52w high {_safe_float(derived.get('52w_high')):.2f}, low {_safe_float(derived.get('52w_low')):.2f}",
        f"drawdown {_safe_float(derived.get('drawdown')):.2f}%",
        f"volatility {_safe_float(derived.get('volatility')):.2f}%",
    ]
    return "; ".join(summary_parts)


def _detect_mobile_client() -> bool:
    """Best-effort user-agent check to default mobile-friendly layouts."""
    try:
        ctx = getattr(st, "context", None)
        headers = getattr(ctx, "headers", {}) if ctx is not None else {}
        if isinstance(headers, dict):
            user_agent = str(headers.get("user-agent", ""))
        else:
            user_agent = str(headers)
    except Exception:
        user_agent = ""

    ua = user_agent.lower()
    if not ua:
        return False

    mobile_indicators = ["android", "iphone", "ipad", "ipod", "mobile", "opera mini", "windows phone"]
    return any(token in ua for token in mobile_indicators)


def _future_value_with_contributions(
    current_value: float,
    annual_return_pct: float,
    biweekly_contribution: float,
    years: int,
) -> float:
    return shared_future_value(current_value, annual_return_pct, biweekly_contribution, years)


def _years_to_income_target(current_income: float, target_income: float, annual_income_growth: float) -> float:
    if target_income <= current_income:
        return 0.0
    if annual_income_growth <= 0:
        return float("inf")
    return (target_income - current_income) / annual_income_growth


def _format_table_for_export(df: pd.DataFrame) -> str:
    if df.empty:
        return "(No data)"
    return df.to_string(index=False)


def _build_allocation_table(metrics: pd.DataFrame) -> str:
    if metrics.empty:
        return "(No data)"

    allocation_df = metrics.groupby("asset_type", dropna=False).agg(
        Market_Value=("market_value", "sum"),
        Annual_Income=("annual_income", "sum"),
    ).reset_index()
    allocation_df["asset_type"] = allocation_df["asset_type"].replace("", "Unresolved Asset Type")
    allocation_df["asset_type"] = allocation_df["asset_type"].map(_display_asset_type_label)
    total_value = float(allocation_df["Market_Value"].sum()) if not allocation_df.empty else 0.0
    allocation_df["Portfolio_Weight"] = allocation_df["Market_Value"] / total_value * 100 if total_value > 0 else 0.0
    allocation_df = allocation_df.sort_values("Market_Value", ascending=False)
    allocation_df["Market_Value"] = allocation_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    allocation_df["Portfolio_Weight"] = allocation_df["Portfolio_Weight"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(allocation_df.rename(columns={"asset_type": "Asset Type"}))


def _is_fund_like_asset_type(asset_type: str) -> bool:
    return _canonical_asset_type(asset_type) in {"ETF", "Bond Fund", "CEF"}


def _display_asset_type_label(asset_type: str) -> str:
    normalized_asset_type = _canonical_asset_type(asset_type)
    return {
        "Stock": "Stocks",
        "ETF": "ETFs",
        "REIT": "REITs",
        "Bond Fund": "Bond Funds",
        "CEF": "Closed-End Funds",
        "BDC": "Business Development Companies",
        "Other": "Other",
    }.get(normalized_asset_type, "Unresolved Asset Type")


def _normalize_sector_label(value: object, unresolved_label: str = "Unresolved Direct Sector") -> str:
    return shared_normalize_sector(value, unresolved_label)


def _classification_label_for_holding(asset_type: str, sector: object) -> str:
    normalized_asset_type = _canonical_asset_type(asset_type)
    if normalized_asset_type == "ETF":
        return _display_asset_type_label(normalized_asset_type)
    if normalized_asset_type == "Bond Fund":
        return _display_asset_type_label(normalized_asset_type)
    if normalized_asset_type == "CEF":
        return _display_asset_type_label(normalized_asset_type)
    return _normalize_sector_label(sector)


def _humanize_sector_weight_label(value: object) -> str:
    text = str(value or "").strip().replace("_", " ")
    if not text:
        return "Unresolved Fund Exposure"
    alias = _normalize_sector_alias(text)
    if alias:
        return alias
    return " ".join(part.capitalize() for part in text.split())


def _build_direct_sector_classification_records(metrics: pd.DataFrame) -> list[dict[str, object]]:
    if metrics.empty:
        return []

    sector_df = metrics.copy()
    if "asset_type" not in sector_df.columns:
        sector_df["asset_type"] = ""
    if "sector" not in sector_df.columns:
        sector_df["sector"] = ""

    total_portfolio_value = float(pd.to_numeric(sector_df["market_value"], errors="coerce").fillna(0.0).sum()) if not sector_df.empty else 0.0
    sector_df["asset_type"] = sector_df["asset_type"].apply(_canonical_asset_type)
    sector_df = sector_df[~sector_df["asset_type"].apply(_is_fund_like_asset_type)].copy()
    if sector_df.empty:
        return []

    sector_df["sector"] = sector_df["sector"].apply(_normalize_sector_label)
    sector_df = sector_df.groupby("sector", dropna=False).agg(
        Market_Value=("market_value", "sum"),
        Annual_Income=("annual_income", "sum"),
    ).reset_index()
    resolved_direct_sector_value = float(sector_df["Market_Value"].sum()) if not sector_df.empty else 0.0
    sector_df["Portfolio_Weight_Pct"] = sector_df["Market_Value"] / total_portfolio_value * 100 if total_portfolio_value > 0 else 0.0
    sector_df["Pct_of_Resolved_Direct_Sector_Exposure"] = (
        sector_df["Market_Value"] / resolved_direct_sector_value * 100 if resolved_direct_sector_value > 0 else 0.0
    )
    sector_df = sector_df.sort_values("Market_Value", ascending=False)
    return sector_df.rename(columns={"sector": "Sector"}).to_dict(orient="records")


def _build_direct_sector_table(metrics: pd.DataFrame) -> str:
    sector_records = _build_direct_sector_classification_records(metrics)
    if not sector_records:
        return "(No data)"

    sector_df = pd.DataFrame(sector_records)
    sector_df["Market_Value"] = sector_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    sector_df["Portfolio_Weight_Pct"] = sector_df["Portfolio_Weight_Pct"].map(lambda value: _format_percent(float(value)))
    sector_df["Pct_of_Resolved_Direct_Sector_Exposure"] = sector_df["Pct_of_Resolved_Direct_Sector_Exposure"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(sector_df)


def _build_direct_holdings_by_classification_records(metrics: pd.DataFrame) -> list[dict[str, object]]:
    if metrics.empty:
        return []

    classification_columns = [col for col in ["ticker", "asset_type", "sector", "market_value", "annual_income"] if col in metrics.columns]
    classification_df = metrics[classification_columns].copy()
    if "asset_type" not in classification_df.columns:
        classification_df["asset_type"] = ""
    if "sector" not in classification_df.columns:
        classification_df["sector"] = ""
    classification_df["asset_type"] = classification_df["asset_type"].apply(_canonical_asset_type)
    classification_df["classification"] = classification_df.apply(
        lambda row: _classification_label_for_holding(row.get("asset_type", ""), row.get("sector", "")),
        axis=1,
    )
    grouped = classification_df.groupby("classification", dropna=False).agg(
        Holdings=("ticker", lambda values: ", ".join(sorted({str(value).strip().upper() for value in values if str(value).strip()}))),
        Market_Value=("market_value", "sum"),
        Annual_Income=("annual_income", "sum"),
    ).reset_index()
    total_value = float(grouped["Market_Value"].sum()) if not grouped.empty else 0.0
    grouped["Portfolio_Weight"] = grouped["Market_Value"] / total_value * 100 if total_value > 0 else 0.0
    grouped = grouped.sort_values("Market_Value", ascending=False)
    return grouped.rename(columns={"classification": "Classification"}).to_dict(orient="records")


def _build_direct_holdings_by_classification_table(metrics: pd.DataFrame) -> str:
    classification_records = _build_direct_holdings_by_classification_records(metrics)
    if not classification_records:
        return "(No data)"

    classification_df = pd.DataFrame(classification_records)
    classification_df["Market_Value"] = classification_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    classification_df["Portfolio_Weight"] = classification_df["Portfolio_Weight"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(classification_df)


def _latest_fund_sector_weightings(ticker: str) -> dict[str, float]:
    try:
        funds_data = getattr(yf.Ticker(ticker), "funds_data", None)
        sector_weightings = getattr(funds_data, "sector_weightings", None) if funds_data is not None else None
    except Exception:
        return {}

    if not sector_weightings:
        return {}

    cleaned: dict[str, float] = {}
    for sector_name, raw_weight in sector_weightings.items():
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        if weight > 1:
            weight = weight / 100.0
        cleaned[_humanize_sector_weight_label(sector_name)] = weight
    return cleaned


@st.cache_data(ttl=3600)
def _latest_fund_sector_weights(tickers: tuple[str, ...]) -> dict[str, dict[str, float]]:
    if not tickers:
        return {}

    output: dict[str, dict[str, float]] = {}
    for ticker in tickers:
        output[str(ticker).strip().upper()] = _latest_fund_sector_weightings(str(ticker).strip().upper())
    return output


def _export_fund_sector_weights_map() -> dict[str, dict[str, float]]:
    payload = st.session_state.get("export_payload", {})
    supplemental = payload.get("supplemental_yahoo_financial_data", []) if isinstance(payload, dict) else []
    output: dict[str, dict[str, float]] = {}
    if not isinstance(supplemental, list):
        return output

    for item in supplemental:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        fund_data = item.get("fund_data", {}) if isinstance(item.get("fund_data", {}), dict) else {}
        sector_weightings = fund_data.get("sector_weightings", {}) if isinstance(fund_data, dict) else {}
        if not isinstance(sector_weightings, dict):
            continue
        cleaned: dict[str, float] = {}
        for sector_name, raw_weight in sector_weightings.items():
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                continue
            if weight <= 0:
                continue
            if weight > 1:
                weight = weight / 100.0
            cleaned[_humanize_sector_weight_label(sector_name)] = max(0.0, min(weight, 1.0))
        if cleaned:
            output[ticker] = cleaned
    return output


def _resolve_lookthrough_sector_exposure(metrics: pd.DataFrame) -> dict[str, object]:
    if metrics.empty:
        return {
            "records": [],
            "unresolved_records": [],
            "resolved_value": 0.0,
            "unresolved_value": 0.0,
            "fund_total_value": 0.0,
        }

    look_through_columns = [col for col in ["ticker", "asset_type", "market_value"] if col in metrics.columns]
    look_through_df = metrics[look_through_columns].copy()
    if "ticker" not in look_through_df.columns:
        look_through_df["ticker"] = ""
    if "asset_type" not in look_through_df.columns:
        look_through_df["asset_type"] = ""
    if "market_value" not in look_through_df.columns:
        look_through_df["market_value"] = 0.0
    look_through_df["asset_type"] = look_through_df["asset_type"].apply(_canonical_asset_type)
    look_through_df = look_through_df[look_through_df["asset_type"].apply(_is_fund_like_asset_type)].copy()
    if look_through_df.empty:
        return {
            "records": [],
            "unresolved_records": [],
            "resolved_value": 0.0,
            "unresolved_value": 0.0,
            "fund_total_value": 0.0,
        }

    total_portfolio_value = float(metrics["market_value"].sum()) if not metrics.empty else 0.0
    fund_total_value = float(look_through_df["market_value"].sum()) if not look_through_df.empty else 0.0
    sector_market_values: dict[str, float] = {}
    sector_sources: dict[str, set[str]] = {}
    unresolved_records: dict[str, dict[str, object]] = {}

    fund_tickers = _to_ticker_tuple(look_through_df["ticker"])
    fund_sector_weights = {} if _strict_export_accuracy_mode() else _latest_fund_sector_weights(fund_tickers)
    export_fund_sector_weights = _export_fund_sector_weights_map()

    resolved_value = 0.0
    unresolved_value = 0.0
    for _, row in look_through_df.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        market_value = float(row.get("market_value", 0.0) or 0.0)
        if not ticker or market_value <= 0:
            continue

        sector_source = "unresolved"
        sector_weightings = export_fund_sector_weights.get(ticker, {})
        if sector_weightings:
            sector_source = "export"
        else:
            sector_weightings = fund_sector_weights.get(ticker, {})
            if not sector_weightings and not _strict_export_accuracy_mode():
                sector_weightings = resolve_fund_sector_weights(ticker)
            if sector_weightings:
                sector_source = "yahoo"
        resolved_for_fund = 0.0
        for sector_name, sector_weight in sector_weightings.items():
            normalized_sector_name = _humanize_sector_weight_label(sector_name)
            safe_weight = max(0.0, min(float(sector_weight), 1.0))
            sector_value = market_value * safe_weight
            if sector_value <= 0:
                continue
            resolved_for_fund += sector_value
            sector_market_values[normalized_sector_name] = sector_market_values.get(normalized_sector_name, 0.0) + sector_value
            sector_sources.setdefault(normalized_sector_name, set()).add(f"{ticker} ({sector_source})")

        unresolved_for_fund = max(market_value - resolved_for_fund, 0.0)
        resolved_value += resolved_for_fund
        unresolved_value += unresolved_for_fund
        if unresolved_for_fund <= 0:
            continue

        asset_type_label = _display_asset_type_label(str(row.get("asset_type", "")))
        unresolved_entry = unresolved_records.setdefault(
            asset_type_label,
            {"Fund_Type": asset_type_label, "Holdings": set(), "Market_Value": 0.0, "Portfolio_Weight": 0.0},
        )
        unresolved_entry["Holdings"].add(ticker)
        unresolved_entry["Market_Value"] = float(unresolved_entry["Market_Value"]) + unresolved_for_fund

    records = [
        {
            "Sector": sector_name,
            "Source_Holdings": ", ".join(sorted(sector_sources.get(sector_name, set()))),
            "Market_Value": market_value,
            "Portfolio_Weight": (market_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0.0,
            "Source": "export" if any("(export)" in src for src in sector_sources.get(sector_name, set())) else "yahoo",
        }
        for sector_name, market_value in sorted(sector_market_values.items(), key=lambda item: item[1], reverse=True)
    ]

    unresolved_rows: list[dict[str, object]] = []
    for record in unresolved_records.values():
        market_value = float(record["Market_Value"])
        unresolved_rows.append(
            {
                "Fund_Type": str(record["Fund_Type"]),
                "Holdings": ", ".join(sorted(record["Holdings"])),
                "Market_Value": market_value,
                "Portfolio_Weight": (market_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0.0,
            }
        )
    unresolved_rows.sort(key=lambda row: float(row["Market_Value"]), reverse=True)

    return {
        "records": records,
        "unresolved_records": unresolved_rows,
        "resolved_value": resolved_value,
        "unresolved_value": unresolved_value,
        "fund_total_value": fund_total_value,
        "fund_lookthrough_completeness_pct": (resolved_value / fund_total_value * 100) if fund_total_value > 0 else 100.0,
    }


def _build_look_through_sector_exposure_records(metrics: pd.DataFrame) -> list[dict[str, object]]:
    return _resolve_lookthrough_sector_exposure(metrics).get("records", [])


def _build_unresolved_fund_look_through_records(metrics: pd.DataFrame) -> list[dict[str, object]]:
    return _resolve_lookthrough_sector_exposure(metrics).get("unresolved_records", [])


def _build_unresolved_fund_look_through_table(metrics: pd.DataFrame) -> str:
    unresolved_records = _build_unresolved_fund_look_through_records(metrics)
    if not unresolved_records:
        return "(No data)"

    unresolved_df = pd.DataFrame(unresolved_records)
    unresolved_df["Market_Value"] = unresolved_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    unresolved_df["Portfolio_Weight"] = unresolved_df["Portfolio_Weight"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(unresolved_df)


def _build_look_through_sector_exposure_table(metrics: pd.DataFrame) -> str:
    exposure_records = _build_look_through_sector_exposure_records(metrics)
    if not exposure_records:
        return "(No data)"

    exposure_df = pd.DataFrame(exposure_records)
    exposure_df["Market_Value"] = exposure_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    exposure_df["Portfolio_Weight"] = exposure_df["Portfolio_Weight"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(exposure_df)


def _build_income_by_holding_table(metrics: pd.DataFrame) -> str:
    if metrics.empty:
        return "(No data)"

    holding_df = metrics[["ticker", "annual_income", "market_value"]].copy()
    holding_df = holding_df.sort_values("annual_income", ascending=False)
    total_income = float(holding_df["annual_income"].sum()) if not holding_df.empty else 0.0
    holding_df["Income_Allocation"] = holding_df["annual_income"] / total_income * 100 if total_income > 0 else 0.0
    holding_df["annual_income"] = holding_df["annual_income"].map(lambda value: _format_currency(float(value)))
    holding_df["market_value"] = holding_df["market_value"].map(lambda value: _format_currency(float(value)))
    holding_df["Income_Allocation"] = holding_df["Income_Allocation"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(holding_df.rename(columns={"ticker": "Ticker", "annual_income": "Annual Income", "market_value": "Market Value", "Income_Allocation": "Income Allocation"}))


def _build_income_by_asset_type_table(metrics: pd.DataFrame) -> str:
    if metrics.empty:
        return "(No data)"

    asset_income_df = metrics.groupby("asset_type", dropna=False).agg(
        Annual_Income=("annual_income", "sum"),
        Market_Value=("market_value", "sum"),
    ).reset_index()
    asset_income_df["asset_type"] = asset_income_df["asset_type"].replace("", "Unresolved Asset Type")
    asset_income_df["asset_type"] = asset_income_df["asset_type"].map(_display_asset_type_label)
    total_income = float(asset_income_df["Annual_Income"].sum()) if not asset_income_df.empty else 0.0
    asset_income_df["Income_Allocation"] = asset_income_df["Annual_Income"] / total_income * 100 if total_income > 0 else 0.0
    asset_income_df = asset_income_df.sort_values("Annual_Income", ascending=False)
    asset_income_df["Annual_Income"] = asset_income_df["Annual_Income"].map(lambda value: _format_currency(float(value)))
    asset_income_df["Market_Value"] = asset_income_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    asset_income_df["Income_Allocation"] = asset_income_df["Income_Allocation"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(asset_income_df.rename(columns={"asset_type": "Asset Type", "Annual_Income": "Annual Income", "Market_Value": "Market Value", "Income_Allocation": "Income Allocation"}))


def _build_income_quality_records(metrics: pd.DataFrame) -> list[dict[str, object]]:
    if metrics.empty:
        return []

    quality_df = metrics[[col for col in ["ticker", "asset_type", "dividend_yield_pct", "annualized_income", "market_value"] if col in metrics.columns]].copy()
    quality_df["income_quality_classification"] = quality_df.apply(_classify_income_quality, axis=1)
    return quality_df.to_dict(orient="records")


def _build_income_quality_table(metrics: pd.DataFrame) -> str:
    quality_records = _build_income_quality_records(metrics)
    if not quality_records:
        return "(No data)"

    quality_df = pd.DataFrame(quality_records)
    if "annualized_income" in quality_df.columns:
        quality_df["annualized_income"] = quality_df["annualized_income"].map(lambda value: _format_currency(float(value)))
    if "market_value" in quality_df.columns:
        quality_df["market_value"] = quality_df["market_value"].map(lambda value: _format_currency(float(value)))
    if "dividend_yield_pct" in quality_df.columns:
        quality_df["dividend_yield_pct"] = quality_df["dividend_yield_pct"].map(lambda value: _format_percent(float(value)))

    return _format_table_for_export(
        quality_df.rename(
            columns={
                "ticker": "Ticker",
                "asset_type": "Asset Type",
                "dividend_yield_pct": "Dividend Yield",
                "annualized_income": "Annualized Income",
                "market_value": "Market Value",
                "income_quality_classification": "Income Quality",
            }
        )
    )


def _build_top_holdings_table(metrics: pd.DataFrame) -> str:
    if metrics.empty:
        return "(No data)"

    top_holdings_df = metrics[["ticker", "market_value", "annual_income"]].copy()
    top_holdings_df = top_holdings_df.sort_values("market_value", ascending=False).head(5)
    total_value = float(top_holdings_df["market_value"].sum()) if not top_holdings_df.empty else 0.0
    top_holdings_df["market_value"] = top_holdings_df["market_value"].map(lambda value: _format_currency(float(value)))
    top_holdings_df["annual_income"] = top_holdings_df["annual_income"].map(lambda value: _format_currency(float(value)))
    return _format_table_for_export(top_holdings_df.rename(columns={"ticker": "Ticker", "market_value": "Market Value", "annual_income": "Annual Income"}))


def _build_top_income_table(metrics: pd.DataFrame) -> str:
    if metrics.empty:
        return "(No data)"

    top_income_df = metrics[["ticker", "annual_income", "market_value"]].copy()
    top_income_df = top_income_df.sort_values("annual_income", ascending=False).head(5)
    total_income = float(top_income_df["annual_income"].sum()) if not top_income_df.empty else 0.0
    top_income_df["annual_income"] = top_income_df["annual_income"].map(lambda value: _format_currency(float(value)))
    top_income_df["market_value"] = top_income_df["market_value"].map(lambda value: _format_currency(float(value)))
    return _format_table_for_export(top_income_df.rename(columns={"ticker": "Ticker", "annual_income": "Annual Income", "market_value": "Market Value"}))


def _build_concentration_context(metrics: pd.DataFrame) -> dict[str, object]:
    if metrics.empty:
        return {}

    concentration = evaluate_concentration(metrics)
    largest_holding_ticker = str(concentration.get("largest_holding_by_value", "N/A"))
    largest_holding_row = metrics.sort_values("market_value", ascending=False).iloc[0]
    largest_holding_value = float(largest_holding_row.get("market_value", 0.0) or 0.0)
    total_value = float(metrics["market_value"].sum()) if not metrics.empty else 0.0
    total_income = float(metrics.get("annualized_income", metrics.get("annual_income", 0.0)).sum()) if not metrics.empty else 0.0
    largest_income_value = float(
        metrics.sort_values("annualized_income" if "annualized_income" in metrics.columns else "annual_income", ascending=False)
        .iloc[0]
        .get("annualized_income", metrics.sort_values("annual_income", ascending=False).iloc[0].get("annual_income", 0.0))
        or 0.0
    )

    return {
        "largest_holding": largest_holding_value,
        "largest_holding_pct": (largest_holding_value / total_value) if total_value > 0 else 0.0,
        "largest_income_pct": (largest_income_value / total_income) if total_income > 0 else 0.0,
        "flag_employer_stock": bool(concentration.get("espp_concentration_flag", False)),
        "largest_holding_by_value": largest_holding_ticker,
        "largest_holding_value_weight_pct": float(concentration.get("largest_holding_weight_pct", 0.0) or 0.0),
        "largest_holding_income_weight_pct": float(concentration.get("largest_holding_income_weight_pct", 0.0) or 0.0),
        "largest_income_contributor": str(concentration.get("largest_income_contributor", "N/A")),
        "largest_income_weight_pct": float(concentration.get("largest_income_weight_pct", 0.0) or 0.0),
        "guidance": str(concentration.get("guidance", "Capital concentration and income concentration should be evaluated separately.")),
        "espp_concentration_flag": bool(concentration.get("espp_concentration_flag", False)),
        "concentration_alert": {
            "is_concentration_alert": float(concentration.get("largest_holding_weight_pct", 0.0) or 0.0) >= 40.0,
            "largest_holding_ticker": largest_holding_ticker,
            "largest_holding_weight_pct": float(concentration.get("largest_holding_weight_pct", 0.0) or 0.0),
            "largest_income_ticker": str(concentration.get("largest_income_contributor", "N/A")),
            "largest_income_weight_pct": float(concentration.get("largest_income_weight_pct", 0.0) or 0.0),
        },
    }


def _build_economic_exposure_table(metrics: pd.DataFrame) -> str:
    if metrics.empty:
        return "(No data)"

    exposure_df = metrics.copy()
    exposure_df["economic_exposure_category"] = exposure_df["asset_type"].replace(
        {"Stock": "Equities", "ETF": "Equities", "REIT": "REITs", "CEF": "Alternative Income", "BDC": "Alternative Income", "Bond Fund": "Bonds", "Other": "Other"}
    )
    exposure_df = exposure_df.groupby("economic_exposure_category", dropna=False).agg(
        Market_Value=("market_value", "sum"),
        Annual_Income=("annual_income", "sum"),
    ).reset_index()
    total_value = float(exposure_df["Market_Value"].sum()) if not exposure_df.empty else 0.0
    exposure_df["Portfolio_Weight"] = exposure_df["Market_Value"] / total_value * 100 if total_value > 0 else 0.0
    exposure_df = exposure_df.sort_values("Market_Value", ascending=False)
    exposure_df["Market_Value"] = exposure_df["Market_Value"].map(lambda value: _format_currency(float(value)))
    exposure_df["Portfolio_Weight"] = exposure_df["Portfolio_Weight"].map(lambda value: _format_percent(float(value)))
    return _format_table_for_export(exposure_df.rename(columns={"economic_exposure_category": "Economic Exposure Category"}))


def _flag_data_quality_issues(metrics: pd.DataFrame, holdings: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    if metrics.empty or holdings.empty:
        return issues

    holdings_norm = _normalize_columns(holdings.copy(), drop_blank_tickers=True)
    source_dividends = pd.to_numeric(holdings_norm.get("annual_div_per_share", 0.0), errors="coerce").fillna(0.0)
    if source_dividends.eq(0).all() and not metrics.empty:
        issues.append("Dividend income is estimated from current Yahoo Finance dividend yield data and is not guaranteed.")

    incomplete_sector_data = False
    for _, row in metrics.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        annual_income = float(row.get("annual_income", 0.0) or 0.0)
        market_value = float(row.get("market_value", 0.0) or 0.0)
        portfolio_yield_pct = float(row.get("portfolio_yield_pct", 0.0) or 0.0)
        dividend_yield_pct = float(row.get("dividend_yield_pct", 0.0) or 0.0)
        effective_dividend = float(row.get("effective_annual_div_per_share", 0.0) or 0.0)
        sector = str(row.get("sector", "") or "").strip()
        if not sector:
            incomplete_sector_data = True

        if market_value <= 0:
            continue

        if portfolio_yield_pct > 50 or dividend_yield_pct > 50:
            issues.append(
                f"{ticker}: dividend yield appears implausibly high ({portfolio_yield_pct:.2f}% portfolio yield / {dividend_yield_pct:.2f}% dividend yield)."
            )
        if annual_income > 0 and market_value > 0 and annual_income / market_value > 1.5:
            issues.append(
                f"{ticker}: annual income is larger than the current market value, which suggests a data-quality issue."
            )
        if effective_dividend > 0 and market_value > 0 and effective_dividend > market_value * 10:
            issues.append(
                f"{ticker}: effective annual dividend per share is unusually large relative to market value."
            )

    if incomplete_sector_data:
        issues.append(
            "Look-through sector exposure is incomplete where Yahoo Finance does not provide underlying fund sector weights. "
            "The affected fund holdings remain included in portfolio and asset-allocation totals; only their underlying sector decomposition is unresolved."
        )
    if not issues:
        issues.append("Dividend income is estimated from current Yahoo Finance dividend yield data and is not guaranteed.")

    return issues


def _build_portfolio_snapshot(
    metrics: pd.DataFrame,
    biweekly_contribution: float = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
    projection_return_pct: float = 8.0,
) -> dict[str, object]:
    if "cost_basis" in metrics.columns:
        cost_basis_values = pd.to_numeric(metrics["cost_basis"], errors="coerce").fillna(0.0)
    elif "amount_invested" in metrics.columns:
        cost_basis_values = pd.to_numeric(metrics["amount_invested"], errors="coerce").fillna(0.0)
    else:
        cost_basis_values = pd.Series(0.0, index=metrics.index)

    total_cost = float(cost_basis_values.sum()) if not metrics.empty else 0.0
    total_value = float(pd.to_numeric(metrics["market_value"], errors="coerce").fillna(0.0).sum()) if not metrics.empty else 0.0
    total_income = float(pd.to_numeric(metrics["annual_income"], errors="coerce").fillna(0.0).sum()) if not metrics.empty else 0.0
    gain = total_value - total_cost
    weighted_yield = (total_income / total_value * 100) if total_value > 0 else 0.0
    annual_contribution = biweekly_contribution * 26
    expected_return = DEFAULT_EXPECTED_RETURN
    projection_biweekly_contribution = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB
    projected_10y_value = _future_value_with_contributions(total_value, expected_return, projection_biweekly_contribution, 10)
    projected_20y_value = _future_value_with_contributions(total_value, expected_return, projection_biweekly_contribution, 20)

    if not metrics.empty:
        top_row = metrics.sort_values("market_value", ascending=False).iloc[0]
        top_holding = str(top_row.get("ticker", "N/A"))
        top_weight = float(top_row.get("market_value", 0.0) / total_value * 100) if total_value > 0 else 0.0
    else:
        top_holding = "N/A"
        top_weight = 0.0

    projection_contribution_note = (
        "Projection calculations use the regular $280 biweekly contribution assumption and do not include ESPP contributions unless otherwise stated."
    )

    return {
        "portfolio_value": total_value,
        "cost_basis": total_cost,
        "unrealized_gain": gain,
        "annual_income": total_income,
        "weighted_yield_pct": weighted_yield,
        "top_holding": top_holding,
        "top_holding_weight_pct": top_weight,
        "biweekly_contribution": biweekly_contribution,
        "annual_contribution": annual_contribution,
        "projected_annual_return_pct": projection_return_pct,
        "expected_return": expected_return,
        "projection_biweekly_contribution": projection_biweekly_contribution,
        "projected_10y_value": projected_10y_value,
        "projected_20y_value": projected_20y_value,
        "projection_contribution_note": projection_contribution_note,
    }


def compute_health_score(df: pd.DataFrame, lookthrough_exposure: list[dict[str, object]]) -> dict[str, object]:
    if df.empty:
        return {
            "health_score": 0.0,
            "scores": {
                "diversification_score": 0.0,
                "concentration_score": 0.0,
                "income_stability_score": 0.0,
                "dividend_growth_potential_score": 0.0,
                "total_return_potential_score": 0.0,
                "risk_score": 0.0,
                "data_quality_score": 0.0,
            },
            "strongest_areas": [],
            "weakest_areas": [],
            "primary_alert": "No health alerts.",
            "alerts": [],
            "data_quality_explanation": "No holdings available.",
            "income_stability_explanation": "No holdings available.",
            "risk_explanation": "No holdings available.",
        }

    health_df = df.copy()
    total_value = float(health_df["market_value"].sum()) if not health_df.empty else 0.0
    total_income = float(health_df["annual_income"].sum()) if not health_df.empty else 0.0

    if total_value > 0:
        health_df["weight_pct"] = health_df["market_value"] / total_value * 100
    else:
        health_df["weight_pct"] = 0.0

    top_weight = float(health_df["weight_pct"].max()) if not health_df.empty else 0.0
    top3_income_pct = (
        float(health_df.sort_values("annual_income", ascending=False).head(3)["annual_income"].sum()) / total_income * 100
        if total_income > 0
        else 0.0
    )
    weighted_yield = (total_income / total_value * 100) if total_value > 0 else 0.0
    lookthrough_sector_count = len([row for row in lookthrough_exposure if float(row.get("Market_Value", 0.0) or 0.0) > 0])

    diversification_score = min((len(health_df) / 12) * 100, 100) if len(health_df) > 0 else 0.0
    concentration_score = max(0.0, 100 - max(top_weight - 15, 0) * 2.5)
    income_stability_score = max(0.0, 100 - max(top3_income_pct - 55, 0) * 1.5)
    dividend_growth_potential_score = max(0.0, 100 - abs(weighted_yield - 6) * 12)
    total_return_potential_score = min(100.0, max(0.0, 65 + (lookthrough_sector_count * 2) - max(top_weight - 20, 0) * 1.2))
    risk_score = max(0.0, 100 - max(top_weight - 15, 0) * 2.8)
    missing_sector_ratio = float(health_df["sector"].fillna("").astype(str).str.strip().eq("").mean()) if "sector" in health_df.columns else 1.0
    data_quality_score = max(0.0, 100 - (missing_sector_ratio * 40))

    weighted_scores = {
        "diversification_score": diversification_score,
        "concentration_score": concentration_score,
        "income_stability_score": income_stability_score,
        "dividend_growth_potential_score": dividend_growth_potential_score,
        "total_return_potential_score": total_return_potential_score,
        "risk_score": risk_score,
        "data_quality_score": data_quality_score,
    }
    weights = {
        "diversification_score": 0.20,
        "concentration_score": 0.20,
        "income_stability_score": 0.15,
        "dividend_growth_potential_score": 0.15,
        "total_return_potential_score": 0.15,
        "risk_score": 0.10,
        "data_quality_score": 0.05,
    }
    health_score = sum(weighted_scores[key] * weights[key] for key in weighted_scores)
    health_score = max(0.0, min(100.0, health_score))

    alerts: list[str] = []
    if top_weight > 25:
        top_name = str(health_df.sort_values("weight_pct", ascending=False).iloc[0].get("ticker", "N/A"))
        alerts.append(f"Concentration alert: {top_name} is {top_weight:.1f}% of portfolio.")
    if top3_income_pct > 70:
        alerts.append(f"Income concentration alert: top 3 holdings contribute {top3_income_pct:.1f}% of income.")
    if weighted_yield < 3:
        alerts.append(f"Yield alert: weighted portfolio yield is low at {weighted_yield:.2f}%.")
    if weighted_yield > 10:
        alerts.append(f"Yield alert: weighted portfolio yield is elevated at {weighted_yield:.2f}%.")

    ranked = sorted(weighted_scores.items(), key=lambda item: item[1], reverse=True)
    strongest_areas = [
        {"area": _format_health_dimension_name(name.replace("_score", "")), "score": round(score, 1)}
        for name, score in ranked[:3]
    ]
    weakest_areas = [
        {"area": _format_health_dimension_name(name.replace("_score", "")), "score": round(score, 1)}
        for name, score in sorted(weighted_scores.items(), key=lambda item: item[1])[:3]
    ]

    return {
        "health_score": health_score,
        "scores": weighted_scores,
        "strongest_areas": strongest_areas,
        "weakest_areas": weakest_areas,
        "primary_alert": alerts[0] if alerts else "No health alerts.",
        "alerts": alerts,
        "data_quality_explanation": "Data quality reflects resolved sector/classification coverage and availability of market/dividend inputs.",
        "income_stability_explanation": "Income stability reflects how concentrated annual income is across top contributors.",
        "risk_explanation": "Risk reflects single-position concentration, yield extremes, and overall exposure balance.",
    }


class HealthEngine:
    def __init__(self, holdings: pd.DataFrame, export_data: dict | None = None):
        self.holdings = holdings.copy() if isinstance(holdings, pd.DataFrame) else pd.DataFrame()
        self.export_data = export_data or {}

    def compute(self) -> dict:
        if self.holdings.empty:
            return {
                "diversification": 0.0,
                "concentration": 0.0,
                "income_stability": 0.0,
                "total_return_potential": 0.0,
                "risk": 0.0,
                "data_quality": 0.0,
                "completeness": 0.0,
                "overall": 0.0,
            }

        holdings = self.holdings.copy()
        if "market_value" not in holdings.columns:
            holdings["market_value"] = 0.0
        if "annualized_income" not in holdings.columns:
            holdings["annualized_income"] = holdings.get("annual_income", 0.0)
        if "sector" not in holdings.columns:
            holdings["sector"] = ""

        total_value = float(pd.to_numeric(holdings["market_value"], errors="coerce").fillna(0.0).sum())
        total_income = float(pd.to_numeric(holdings["annualized_income"], errors="coerce").fillna(0.0).sum())
        holdings["weight_pct"] = holdings["market_value"] / total_value * 100 if total_value > 0 else 0.0
        top_weight = float(holdings["weight_pct"].max()) if not holdings.empty else 0.0
        top3_income_pct = (
            float(holdings.sort_values("annualized_income", ascending=False).head(3)["annualized_income"].sum()) / total_income * 100
            if total_income > 0
            else 0.0
        )

        diversification_score = min((len(holdings) / 12) * 100, 100) if len(holdings) > 0 else 0.0
        concentration_score = max(0.0, 100 - max(top_weight - 15, 0) * 2.5)
        income_stability_score = max(0.0, 100 - max(top3_income_pct - 55, 0) * 1.5)
        total_return_potential_score = min(100.0, max(0.0, 100 - max(top_weight - 20, 0) * 1.8))
        risk_score = max(0.0, 100 - max(top_weight - 15, 0) * 2.8)

        populated_fields = 0
        expected_fields = 6
        for field in ["ticker", "asset_type", "sector", "market_value", "annualized_income", "current_price"]:
            if field in holdings.columns and holdings[field].notna().any():
                populated_fields += 1
        data_quality_score = populated_fields / expected_fields * 100

        completeness = _build_data_completeness(holdings)
        completeness_score = float(completeness.get("overall_resolved_exposure_pct", 0.0) or 0.0)

        weighted = {
            "diversification": diversification_score,
            "concentration": concentration_score,
            "income_stability": income_stability_score,
            "total_return_potential": total_return_potential_score,
            "risk": risk_score,
            "data_quality": data_quality_score,
            "completeness": completeness_score,
        }
        weights = {
            "diversification": 0.18,
            "concentration": 0.18,
            "income_stability": 0.15,
            "total_return_potential": 0.15,
            "risk": 0.12,
            "data_quality": 0.12,
            "completeness": 0.10,
        }
        overall = sum(weighted[name] * weights[name] for name in weighted)

        return {
            "diversification": round(diversification_score, 2),
            "concentration": round(concentration_score, 2),
            "income_stability": round(income_stability_score, 2),
            "total_return_potential": round(total_return_potential_score, 2),
            "risk": round(risk_score, 2),
            "data_quality": round(data_quality_score, 2),
            "completeness": round(completeness_score, 2),
            "overall": round(max(0.0, min(100.0, overall)), 2),
        }


def _build_portfolio_health(metrics: pd.DataFrame) -> dict[str, object]:
    lookthrough_summary = _resolve_lookthrough_sector_exposure(metrics)
    asset_type_allocation: list[dict[str, object]] = []
    if not metrics.empty and "asset_type" in metrics.columns:
        asset_alloc_df = metrics.groupby("asset_type", dropna=False)["market_value"].sum().reset_index()
        total_value = float(asset_alloc_df["market_value"].sum()) if not asset_alloc_df.empty else 0.0
        asset_alloc_df["portfolio_weight_pct"] = asset_alloc_df["market_value"] / total_value * 100 if total_value > 0 else 0.0
        asset_type_allocation = asset_alloc_df.to_dict(orient="records")

    exposure_data = {
        "direct_sector_exposure": _build_direct_sector_classification_records(metrics),
        "look_through_sector_exposure": lookthrough_summary.get("records", []),
        "unresolved_exposure": {
            "unresolved_fund_exposure": float(lookthrough_summary.get("unresolved_value", 0.0) or 0.0),
            "resolved_lookthrough_value": float(lookthrough_summary.get("resolved_value", 0.0) or 0.0),
            "fund_total_value": float(lookthrough_summary.get("fund_total_value", 0.0) or 0.0),
        },
        "asset_type_allocation": asset_type_allocation,
        "income_allocation": _build_income_quality_records(metrics),
    }
    tickers = tuple(str(ticker).strip().upper() for ticker in metrics.get("ticker", pd.Series(dtype=str)).dropna().astype(str).tolist() if str(ticker).strip())
    monthly_dividend_weights = _latest_dividend_month_weights(tickers) if tickers else {}
    asset_type_map = _latest_asset_type_hints(tickers) if tickers else {}
    contribution_context = _build_contribution_context(
        metrics,
        metrics,
        DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
        ESPP_CONTRIB_BIWEEKLY,
        ESPP_PURCHASE_FREQUENCY,
        ESPP_DISCOUNT,
    )

    sector_series = metrics.get("sector", pd.Series("", index=metrics.index)).fillna("").astype(str).str.strip() if not metrics.empty else pd.Series(dtype=str)
    pct_unresolved_sector = float(sector_series.eq("").mean()) if len(sector_series) > 0 else 0.0
    yield_source = metrics.get("dividend_yield_pct", pd.Series(0.0, index=metrics.index)) if not metrics.empty else pd.Series(dtype=float)
    yield_series = pd.to_numeric(yield_source, errors="coerce").fillna(0.0) if not metrics.empty else pd.Series(dtype=float)
    pct_missing_dividends = float(yield_series.le(0).mean()) if len(yield_series) > 0 else 0.0
    unresolved_value = float(exposure_data.get("unresolved_exposure", {}).get("unresolved_fund_exposure", 0.0) or 0.0)
    fund_total_value = float(exposure_data.get("unresolved_exposure", {}).get("fund_total_value", 0.0) or 0.0)
    total_portfolio_value = float(pd.to_numeric(metrics.get("market_value", 0.0), errors="coerce").fillna(0.0).sum()) if not metrics.empty else 0.0
    if fund_total_value > 0:
        pct_unresolved_lookthrough = unresolved_value / fund_total_value
    elif total_portfolio_value > 0:
        pct_unresolved_lookthrough = unresolved_value / total_portfolio_value
    else:
        pct_unresolved_lookthrough = 0.0

    score_payload = compute_portfolio_health_engine(
        df=metrics,
        exposure=exposure_data,
        income={
            "income_allocation": _build_income_quality_records(metrics),
            "monthly_dividend_weights": monthly_dividend_weights,
            "asset_type_map": asset_type_map,
        },
        contributions=contribution_context,
        espp_context={
            "employer_stock": bool(contribution_context.get("employer_stock_rows", [])),
            "employer_ticker": str(contribution_context.get("employer_stock_concentration_context", {}).get("primary_employer_ticker", "")),
        },
        data_quality={
            "pct_unresolved_sector": pct_unresolved_sector,
            "pct_missing_dividends": pct_missing_dividends,
            "pct_unresolved_lookthrough": pct_unresolved_lookthrough,
        },
    )
    subscores = score_payload.get("subscores", {}) if isinstance(score_payload, dict) else {}
    health_score_breakdown = {
        "diversification": float(subscores.get("diversification", 0.0) or 0.0),
        "concentration": float(subscores.get("concentration", 0.0) or 0.0),
        "income_stability": float(subscores.get("income_stability", 0.0) or 0.0),
        "dividend_growth_potential": float(subscores.get("dividend_growth_potential", 0.0) or 0.0),
        "total_return_potential": float(subscores.get("total_return_potential", 0.0) or 0.0),
        "risk": float(subscores.get("risk", 0.0) or 0.0),
        "data_quality": float(subscores.get("data_quality", 0.0) or 0.0),
    }
    health_score_methodology = {
        "version": "2.0",
        "weights": {
            "diversification": 15,
            "concentration": 20,
            "income_stability": 15,
            "dividend_growth_potential": 10,
            "total_return": 10,
            "risk": 20,
            "data_quality": 10,
        },
        "data_quality_definition": "Data quality penalizes unresolved sector classification, missing dividend data, and unresolved fund look-through exposure.",
        "dividend_growth_potential_definition": "Dividend growth potential rewards growth-oriented holdings (SCHD, HD, NVDA) and penalizes lower-growth holdings (SDIV, GOOD).",
    }

    scored_dimensions = [
        (name, float(value))
        for name, value in health_score_breakdown.items()
        if name in {"diversification", "concentration", "income_stability", "dividend_growth_potential", "total_return_potential", "risk", "data_quality"}
    ]
    strongest_from_engine = score_payload.get("strongest_areas", []) if isinstance(score_payload, dict) else []
    weakest_from_engine = score_payload.get("weakest_areas", []) if isinstance(score_payload, dict) else []
    strongest_labels = [str(item) for item in strongest_from_engine] if isinstance(strongest_from_engine, list) else []
    weakest_labels = [str(item) for item in weakest_from_engine] if isinstance(weakest_from_engine, list) else []

    priority_order = {
        "data_quality": 0,
        "income_stability": 1,
        "concentration": 2,
        "diversification": 3,
        "risk": 4,
        "dividend_growth_potential": 5,
        "total_return_potential": 6,
    }
    strongest_sorted = sorted(scored_dimensions, key=lambda item: (-item[1], priority_order.get(item[0], 99), item[0]))
    strongest_areas = []
    if "data_quality" in health_score_breakdown:
        strongest_areas.append(
            {
                "area": _format_health_dimension_name("data_quality"),
                "score": round(float(health_score_breakdown.get("data_quality", 0.0) or 0.0), 1),
            }
        )
    for name, score in strongest_sorted:
        if name == "data_quality":
            continue
        strongest_areas.append({"area": _format_health_dimension_name(name), "score": round(score, 1)})
        if len(strongest_areas) >= 3:
            break
    weakest_areas = [
        {"area": _format_health_dimension_name(name), "score": round(score, 1)}
        for name, score in sorted(scored_dimensions, key=lambda item: (item[1], item[0]))[:3]
    ]

    alerts = _dedupe_text_items(score_payload.get("alerts", []) if isinstance(score_payload, dict) else [])
    notes = score_payload.get("notes", []) if isinstance(score_payload, dict) else []
    income_quality_package = score_payload.get("income_quality", {}) if isinstance(score_payload, dict) else {}
    largest_holding = str(score_payload.get("largest_holding", "N/A")) if isinstance(score_payload, dict) else "N/A"
    largest_holding_weight = float(score_payload.get("largest_holding_weight_pct", 0.0) or score_payload.get("largest_holding_weight", 0.0) or 0.0) if isinstance(score_payload, dict) else 0.0
    primary_alert = alerts[0] if alerts else "No health alerts."

    return {
        "health_score": float(score_payload.get("overall_score", score_payload.get("health_score", 0.0)) or 0.0),
        "alerts": alerts,
        "notes": notes,
        "income_quality": income_quality_package,
        "health_score_breakdown": health_score_breakdown,
        "health_score_methodology": health_score_methodology,
        "subscores": health_score_breakdown,
        "largest_holding": largest_holding,
        "largest_holding_weight": largest_holding_weight,
        "largest_income_contributor": str(score_payload.get("largest_income_contributor", "N/A")) if isinstance(score_payload, dict) else "N/A",
        "largest_income_weight": float(score_payload.get("largest_income_weight_pct", score_payload.get("largest_income_weight", 0.0)) or 0.0) if isinstance(score_payload, dict) else 0.0,
        "overall_score": float(score_payload.get("overall_score", score_payload.get("health_score", 0.0)) or 0.0),
        "strongest": strongest_labels,
        "weakest": weakest_labels,
        "strongest_areas": strongest_areas,
        "weakest_areas": weakest_areas,
        "primary_alert": primary_alert,
        "data_quality_explanation": "Data quality accounts for unresolved sectors, missing dividend inputs, and unresolved fund look-through.",
        "income_stability_explanation": "Income stability penalizes REIT and option-income concentration and rewards broad ETF exposure.",
        "risk_explanation": "Risk penalizes concentration and REIT-heavy exposure while recognizing bond allocation risk reduction.",
    }


def _format_health_dimension_name(name: str) -> str:
    return str(name).replace("_", " ").title()


def _build_health_context_summary(health: dict[str, object]) -> dict[str, object]:
    if isinstance(health, dict) and health.get("strongest_areas") and health.get("weakest_areas"):
        return {
            "score": float(health.get("health_score", 0.0) or 0.0),
            "score_label": "Tracker-generated health score based on this export's methodology; not an objective portfolio rating.",
            "strongest_areas": health.get("strongest_areas", []),
            "weakest_areas": health.get("weakest_areas", []),
            "primary_alert": str(health.get("primary_alert", "No health alerts.")),
        }

    breakdown = health.get("health_score_breakdown", {}) if isinstance(health, dict) else {}
    if not isinstance(breakdown, dict) or not breakdown:
        return {
            "score": float(health.get("health_score", 0.0) or 0.0) if isinstance(health, dict) else 0.0,
            "score_label": "Tracker-generated health score based on this export's methodology; not an objective portfolio rating.",
            "strongest_areas": [],
            "weakest_areas": [],
            "primary_alert": "No health alerts.",
        }

    scored_dimensions = []
    for name, raw_score in breakdown.items():
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        scored_dimensions.append((str(name), score))

    strongest = sorted(scored_dimensions, key=lambda item: (-item[1], item[0]))[:3]
    weakest = sorted(scored_dimensions, key=lambda item: (item[1], item[0]))[:3]
    alerts = health.get("alerts", []) if isinstance(health, dict) else []

    return {
        "score": float(health.get("health_score", 0.0) or 0.0),
        "score_label": "Tracker-generated health score based on this export's methodology; not an objective portfolio rating.",
        "strongest_areas": [{"area": _format_health_dimension_name(name), "score": round(score, 1)} for name, score in strongest],
        "weakest_areas": [{"area": _format_health_dimension_name(name), "score": round(score, 1)} for name, score in weakest],
        "primary_alert": str(alerts[0]) if alerts else "No health alerts.",
    }


def _build_data_completeness(metrics: pd.DataFrame) -> dict[str, float]:
    if metrics.empty:
        return {
            "overall_resolved_exposure_coverage_pct": 0.0,
            "direct_sector_classification_coverage_pct": 0.0,
            "fund_lookthrough_coverage_pct": 0.0,
            "direct_sector_coverage_pct": 0.0,
            "overall_resolved_exposure_pct": 0.0,
        }

    completeness_columns = [col for col in ["ticker", "asset_type", "sector", "market_value"] if col in metrics.columns]
    completeness_df = metrics[completeness_columns].copy()
    if "asset_type" in completeness_df.columns:
        completeness_df["asset_type"] = completeness_df["asset_type"].apply(_canonical_asset_type)
    if "market_value" in completeness_df.columns:
        completeness_df["market_value"] = pd.to_numeric(completeness_df["market_value"], errors="coerce").fillna(0.0)
    else:
        completeness_df["market_value"] = 0.0

    total_portfolio_value = float(completeness_df["market_value"].sum()) if not completeness_df.empty else 0.0

    if "asset_type" not in completeness_df.columns:
        completeness_df["asset_type"] = ""
    if "sector" not in completeness_df.columns:
        completeness_df["sector"] = ""

    direct_df = completeness_df[~completeness_df["asset_type"].apply(_is_fund_like_asset_type)].copy()
    direct_total_value = float(direct_df["market_value"].sum()) if not direct_df.empty else 0.0
    direct_resolved_value = float(direct_df[direct_df["sector"].fillna("").astype(str).str.strip().ne("")]["market_value"].sum()) if not direct_df.empty else 0.0

    lookthrough_summary = _resolve_lookthrough_sector_exposure(metrics)
    fund_total_value = float(lookthrough_summary.get("fund_total_value", 0.0) or 0.0)
    fund_resolved_value = float(lookthrough_summary.get("resolved_value", 0.0) or 0.0)

    overall_resolved_value = direct_resolved_value + fund_resolved_value

    overall_pct = (overall_resolved_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0.0
    direct_pct = (direct_resolved_value / direct_total_value * 100) if direct_total_value > 0 else 100.0
    fund_pct = (fund_resolved_value / fund_total_value * 100) if fund_total_value > 0 else 100.0

    return {
        "overall_resolved_exposure_coverage_pct": overall_pct,
        "direct_sector_classification_coverage_pct": direct_pct,
        "fund_lookthrough_coverage_pct": fund_pct,
        "direct_sector_coverage_pct": direct_pct,
        "overall_resolved_exposure_pct": overall_pct,
    }


def _classify_income_type(asset_type: str, ticker: str) -> str:
    ticker_key = str(ticker).strip().upper()
    asset = _canonical_asset_type(str(asset_type))
    if asset == "REIT":
        return "REIT distribution"
    if asset == "Bond Fund":
        return "bond fund distribution"
    if ticker_key in OPTION_INCOME_ETF_TICKERS:
        return "option-income ETF distribution"
    return "traditional dividend"


def _income_quality_snapshot(metrics: pd.DataFrame) -> list[dict[str, object]]:
    if metrics.empty:
        return []

    rows: list[dict[str, object]] = []
    for _, row in metrics.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        income_type = _classify_income_type(str(row.get("asset_type", "")), ticker)
        dividend_yield_pct = float(row.get("dividend_yield_pct", 0.0) or 0.0)
        stability = 80.0
        growth = 60.0
        risk_notes: list[str] = []

        if income_type == "option-income ETF distribution":
            stability -= 20
            growth -= 10
            risk_notes.append("Option-income ETFs can have variable payouts.")
        if income_type == "REIT distribution":
            stability -= 10
            risk_notes.append("REIT distributions can be sensitive to rate cycles.")
        if income_type == "bond fund distribution":
            stability += 5
            growth -= 5
            risk_notes.append("Bond fund distributions are typically steadier but slower growing.")
        if ticker in {"SCHD", "HD", "NVDA"}:
            growth += 15
        if ticker in {"SDIV", "GOOD"}:
            growth -= 20
            risk_notes.append("Lower expected dividend-growth profile.")
        if dividend_yield_pct > 10.0:
            stability -= 15
            risk_notes.append("High yield may indicate elevated income sustainability risk.")

        rows.append(
            {
                "ticker": ticker,
                "income_type_classification": income_type,
                "stability_rating": round(max(0.0, min(100.0, stability)), 1),
                "growth_potential_rating": round(max(0.0, min(100.0, growth)), 1),
                "risk_notes": risk_notes,
            }
        )

    return rows


def _historical_contract_records(historical_context_map: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for ticker, entry in historical_context_map.items():
        price_trend = entry.get("price_trend", {}) if isinstance(entry.get("price_trend", {}), dict) else {}
        records.append({
            "ticker": ticker,
            "change_3m_pct": _coerce_optional_number(price_trend.get("change_3m_pct")),
            "change_1y_pct": _coerce_optional_number(price_trend.get("change_1y_pct")),
            "start_price": _coerce_optional_number(entry.get("start_price")),
            "end_price": _coerce_optional_number(entry.get("end_price")),
            "period_return_pct": _coerce_optional_number(entry.get("return_pct")),
            "52w_high": _coerce_optional_number(entry.get("52w_high")),
            "52w_low": _coerce_optional_number(entry.get("52w_low")),
            "volatility": _coerce_optional_number(entry.get("volatility")),
            "drawdown": _coerce_optional_number(entry.get("drawdown")),
            "source": str(entry.get("source", "Yahoo Finance")),
            "history_type": str(entry.get("history_type", "supplemental_per_security_price_history")),
            "supplemental_history_summary": str(entry.get("history_summary", "")),
        })
    return records


def compute_full_portfolio_snapshot(
    df: pd.DataFrame,
    holdings: pd.DataFrame | None = None,
    lookthrough_summary: dict[str, object] | None = None,
    contribution_context: dict[str, object] | None = None,
    historical_context_map: dict[str, dict[str, object]] | None = None,
    health: dict[str, object] | None = None,
    intelligence_layer_output: dict[str, object] | None = None,
) -> dict[str, object]:
    metrics = _prepare_export_metrics(df.copy())
    if "asset_type" not in metrics.columns:
        metrics["asset_type"] = ""
    if "sector" not in metrics.columns:
        metrics["sector"] = ""

    if not metrics.empty and "market_value" in metrics.columns:
        total_value = float(pd.to_numeric(metrics["market_value"], errors="coerce").fillna(0.0).sum())
    else:
        total_value = 0.0

    if not metrics.empty and "annual_income" in metrics.columns:
        total_income = float(pd.to_numeric(metrics["annual_income"], errors="coerce").fillna(0.0).sum())
    else:
        total_income = 0.0

    if not metrics.empty and "cost_basis" in metrics.columns:
        cost_basis_series = pd.to_numeric(metrics["cost_basis"], errors="coerce").fillna(0.0)
    elif not metrics.empty and "amount_invested" in metrics.columns:
        cost_basis_series = pd.to_numeric(metrics["amount_invested"], errors="coerce").fillna(0.0)
    else:
        cost_basis_series = pd.Series(0.0, index=metrics.index)
    total_cost = float(cost_basis_series.sum()) if not metrics.empty else 0.0

    lookthrough = lookthrough_summary or _resolve_lookthrough_sector_exposure(metrics)
    contribution_ctx = contribution_context or _build_contribution_context(
        holdings if isinstance(holdings, pd.DataFrame) else metrics,
        metrics,
        DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
        ESPP_CONTRIB_BIWEEKLY,
        ESPP_PURCHASE_FREQUENCY,
        ESPP_DISCOUNT,
    )

    if historical_context_map is None:
        tickers = tuple(str(ticker).strip().upper() for ticker in metrics.get("ticker", pd.Series(dtype=str)).dropna().astype(str).tolist() if str(ticker).strip())
        historical_context_map = _resolved_historical_context(tickers)

    health_payload = health or _build_portfolio_health(metrics)
    intelligence_payload = intelligence_layer_output or {}

    if isinstance(holdings, pd.DataFrame):
        holdings_records = _build_ai_holdings_export_frame(holdings, metrics).to_dict(orient="records")
    elif not metrics.empty:
        holdings_records = metrics.to_dict(orient="records")
    else:
        holdings_records = []

    asset_allocation = []
    if not metrics.empty:
        alloc_df = metrics.groupby("asset_type", dropna=False).agg(market_value=("market_value", "sum")).reset_index()
        alloc_df["portfolio_weight_pct"] = alloc_df["market_value"] / total_value * 100 if total_value > 0 else 0.0
        asset_allocation = alloc_df.to_dict(orient="records")

    income_by_holding = []
    income_by_asset_type = []
    if not metrics.empty:
        holding_df = metrics[["ticker", "annual_income", "market_value"]].copy()
        holding_df["income_allocation_pct"] = holding_df["annual_income"] / total_income * 100 if total_income > 0 else 0.0
        income_by_holding = holding_df.to_dict(orient="records")

        income_asset_df = metrics.groupby("asset_type", dropna=False).agg(annual_income=("annual_income", "sum")).reset_index()
        income_asset_df["income_allocation_pct"] = income_asset_df["annual_income"] / total_income * 100 if total_income > 0 else 0.0
        income_by_asset_type = income_asset_df.to_dict(orient="records")

    direct_sector_exposure = _build_direct_sector_classification_records(metrics)
    lookthrough_records = lookthrough.get("records", []) if isinstance(lookthrough, dict) else []
    unresolved_summary = {
        "unresolved_fund_exposure": float(lookthrough.get("unresolved_value", 0.0) or 0.0) if isinstance(lookthrough, dict) else 0.0,
        "resolved_lookthrough_value": float(lookthrough.get("resolved_value", 0.0) or 0.0) if isinstance(lookthrough, dict) else 0.0,
        "fund_total_value": float(lookthrough.get("fund_total_value", 0.0) or 0.0) if isinstance(lookthrough, dict) else 0.0,
    }

    combined_exposure: dict[str, float] = {}
    for item in direct_sector_exposure:
        sector = str(item.get("Sector", "")).strip()
        if sector:
            combined_exposure[sector] = combined_exposure.get(sector, 0.0) + float(item.get("Portfolio_Weight_Pct", 0.0) or 0.0)
    for item in lookthrough_records if isinstance(lookthrough_records, list) else []:
        sector = str(item.get("Sector", "")).strip()
        if sector:
            combined_exposure[sector] = combined_exposure.get(sector, 0.0) + float(item.get("Portfolio_Weight", 0.0) or 0.0)
    combined_total = sum(combined_exposure.values())
    if combined_total > 100.0:
        scale = 100.0 / combined_total
        combined_exposure = {name: value * scale for name, value in combined_exposure.items()}

    contribution_holdings = contribution_ctx.get("holdings", []) if isinstance(contribution_ctx, dict) else []
    employer_stock_concentration_context = (
        contribution_ctx.get("employer_stock_concentration_context", {}) if isinstance(contribution_ctx, dict) else {}
    )
    contribution_weights = []
    total_regular = float(contribution_ctx.get("summary", {}).get("total_regular_biweekly_contribution", 0.0) or 0.0)
    total_espp = float(contribution_ctx.get("summary", {}).get("total_espp_biweekly_contributions", 0.0) or 0.0)
    total_biweekly = total_regular + total_espp
    market_weight_map = {str(row.get("ticker", "")).strip().upper(): float(row.get("market_value", 0.0) or 0.0) / total_value * 100 if total_value > 0 else 0.0 for _, row in metrics.iterrows()} if not metrics.empty else {}
    for item in contribution_holdings if isinstance(contribution_holdings, list) else []:
        ticker = str(item.get("ticker", "")).strip().upper()
        row_total = float(item.get("total_contribution", 0.0) or 0.0)
        contribution_weight = row_total / total_biweekly * 100 if total_biweekly > 0 else 0.0
        market_weight = market_weight_map.get(ticker, 0.0)
        contribution_weights.append(
            {
                "ticker": ticker,
                "contribution_biweekly": row_total,
                "contribution_weight_pct": contribution_weight,
                "market_weight_pct": market_weight,
                "contribution_weight_vs_market_weight": contribution_weight - market_weight,
            }
        )

    historical_records = _historical_contract_records(historical_context_map or {})

    completeness = _build_data_completeness(metrics)

    snapshot = {
        "meta": {
            "snapshot_timestamp": datetime.now().isoformat(timespec="seconds"),
            "currency": "USD",
            "export_version": "2.0",
            "schema_version": "2.0",
        },
        "totals": {
            "portfolio_value": total_value,
            "cost_basis": total_cost,
            "unrealized_gain": total_value - total_cost,
            "annual_income": total_income,
        },
        "holdings": holdings_records,
        "income": {
            "by_holding": income_by_holding,
            "by_asset_type": income_by_asset_type,
            "quality": _income_quality_snapshot(metrics),
        },
        "allocation": {
            "asset_type": asset_allocation,
            "economic_exposure": _build_economic_exposure_table(metrics),
        },
        "exposure": {
            "direct_sector_exposure": direct_sector_exposure,
            "look_through_exposure": lookthrough_records,
            "unresolved_exposure": unresolved_summary,
            "combined_exposure_summary": combined_exposure,
            "exposure_completeness_pct": float(completeness.get("overall_resolved_exposure_coverage_pct", 0.0) or 0.0),
        },
        "contributions": {
            "regular_biweekly": total_regular,
            "espp_biweekly": total_espp,
            "total_annual_contributions": (total_regular + total_espp) * 26,
            "distribution_by_holding": contribution_holdings,
            "contribution_weight_vs_market_weight": contribution_weights,
            "contribution_context": contribution_ctx,
            "employer_stock_concentration_context": employer_stock_concentration_context,
            "espp": {
                "ticker": str(employer_stock_concentration_context.get("primary_employer_ticker", "HD") or "HD"),
                "biweekly_contribution": total_espp,
                "annualized_contribution": total_espp * 26,
                "purchase_discount": _normalize_percentage_value(
                    contribution_ctx.get("summary", {}).get("hd_espp_discount_pct", ESPP_DISCOUNT * 100.0),
                    ESPP_DISCOUNT * 100.0,
                ) / 100.0,
                "purchase_frequency": str(contribution_ctx.get("summary", {}).get("hd_espp_purchase_frequency", ESPP_PURCHASE_FREQUENCY)),
                "employer_stock_concentration_context": employer_stock_concentration_context,
                "income_vs_capital_concentration_comparison": {
                    "largest_holding_weight": float(health_payload.get("largest_holding_weight", 0.0) or 0.0),
                    "largest_income_weight": float(health_payload.get("largest_income_weight", 0.0) or 0.0),
                },
            },
        },
        "historical": {
            "by_ticker": historical_records,
        },
        "health_score": {
            "overall": float(health_payload.get("overall_score", health_payload.get("health_score", 0.0)) or 0.0) if isinstance(health_payload, dict) else 0.0,
            "health_score": float(health_payload.get("overall_score", health_payload.get("health_score", 0.0)) or 0.0) if isinstance(health_payload, dict) else 0.0,
            "subscores": health_payload.get("subscores", health_payload.get("health_score_breakdown", {})) if isinstance(health_payload, dict) else {},
            "strongest": health_payload.get("strongest", []) if isinstance(health_payload, dict) else [],
            "weakest": health_payload.get("weakest", []) if isinstance(health_payload, dict) else [],
            "alerts": _dedupe_text_items(health_payload.get("alerts", []) if isinstance(health_payload, dict) else []),
            "notes": health_payload.get("notes", []) if isinstance(health_payload, dict) else [],
            "income_quality": float(intelligence_payload.get("income_quality_score", 0.0) or 0.0),
            "completeness": float(completeness.get("overall_resolved_exposure_coverage_pct", 0.0) or 0.0),
        },
        "income_quality": health_payload.get("income_quality", {}) if isinstance(health_payload, dict) else {},
        "alerts": {
            "health_alerts": _dedupe_text_items(health_payload.get("alerts", []) if isinstance(health_payload, dict) else []),
            "intelligence_alerts": _dedupe_text_items(intelligence_payload.get("alerts", []) if isinstance(intelligence_payload, dict) else []),
            "concentration_alert": employer_stock_concentration_context,
        },
    }

    return snapshot


def validate_snapshot(snapshot: dict[str, object]) -> None:
    errors: list[str] = []
    holdings = snapshot.get("holdings", []) if isinstance(snapshot, dict) else []
    for row in holdings if isinstance(holdings, list) else []:
        if not isinstance(row, dict):
            errors.append("holding row is not an object")
            continue
        for field in ["ticker", "shares", "market_value"]:
            value = row.get(field)
            if value in [None, ""]:
                errors.append(f"holding missing required field: {field}")

    income = snapshot.get("income", {}) if isinstance(snapshot, dict) else {}
    income_holding = income.get("by_holding", []) if isinstance(income, dict) else []
    for row in income_holding if isinstance(income_holding, list) else []:
        if not isinstance(row, dict):
            errors.append("income row is not an object")
            continue
        try:
            float(row.get("annual_income", 0.0) or 0.0)
        except (TypeError, ValueError):
            errors.append("income annual_income must be numeric")

    exposure = snapshot.get("exposure", {}) if isinstance(snapshot, dict) else {}
    combined = exposure.get("combined_exposure_summary", {}) if isinstance(exposure, dict) else {}
    if isinstance(combined, dict):
        combined_sum = sum(float(value or 0.0) for value in combined.values())
        if combined_sum > 100.0 + 1e-6:
            errors.append("combined exposure exceeds 100%")

    holding_tickers = {str(row.get("ticker", "")).strip().upper() for row in holdings if isinstance(row, dict)} if isinstance(holdings, list) else set()
    if "HD" in holding_tickers:
        contributions = snapshot.get("contributions", {}) if isinstance(snapshot, dict) else {}
        espp = contributions.get("espp") if isinstance(contributions, dict) else None
        if not isinstance(espp, dict):
            errors.append("ESPP section must exist when employer stock exists")

    if errors:
        raise ValueError("Snapshot validation failed: " + "; ".join(errors))


def generate_portfolio_export_json(snapshot: dict[str, object]) -> dict[str, object]:
    meta = snapshot.get("meta", {}) if isinstance(snapshot, dict) else {}
    return _sanitize_export_value({
        "snapshot_timestamp": meta.get("snapshot_timestamp", datetime.now().isoformat(timespec="seconds")),
        "currency": meta.get("currency", "USD"),
        "export_version": meta.get("export_version", "2.0"),
        "schema_version": meta.get("schema_version", "2.0"),
        "totals": snapshot.get("totals", {}),
        "holdings": snapshot.get("holdings", []),
        "allocation": snapshot.get("allocation", {}),
        "exposure": snapshot.get("exposure", {}),
        "income": snapshot.get("income", {}),
        "contributions": snapshot.get("contributions", {}),
        "historical": snapshot.get("historical", {}),
        "health_score": snapshot.get("health_score", {}),
        "income_quality": snapshot.get("income_quality", {}),
        "alerts": snapshot.get("alerts", {}),
    })


def generate_portfolio_export(snapshot: dict[str, object], include_alerts: bool = True) -> str:
    snapshot = _sanitize_export_value(snapshot)
    totals = snapshot.get("totals", {}) if isinstance(snapshot, dict) else {}
    contributions = snapshot.get("contributions", {}) if isinstance(snapshot, dict) else {}
    exposure = snapshot.get("exposure", {}) if isinstance(snapshot, dict) else {}
    income = snapshot.get("income", {}) if isinstance(snapshot, dict) else {}
    historical = snapshot.get("historical", {}) if isinstance(snapshot, dict) else {}
    health = snapshot.get("health_score", {}) if isinstance(snapshot, dict) else {}
    alerts = snapshot.get("alerts", {}) if isinstance(snapshot, dict) else {}

    holdings_df = pd.DataFrame(snapshot.get("holdings", [])) if isinstance(snapshot, dict) else pd.DataFrame()
    holdings_csv = holdings_df.to_csv(index=False) if not holdings_df.empty else ""

    historical_rows = historical.get("by_ticker", []) if isinstance(historical, dict) else []
    available_history_rows = [
        row for row in historical_rows
        if isinstance(row, dict) and row.get("start_price") is not None and row.get("end_price") is not None
    ]
    historical_coverage = (len(available_history_rows) / len(holdings_df) * 100.0) if len(holdings_df) > 0 else 100.0
    income_rows = income.get("by_holding", []) if isinstance(income, dict) else []
    resolved_income_rows = [row for row in income_rows if isinstance(row, dict) and float(row.get("annual_income", 0.0) or 0.0) >= 0]
    income_resolution = (len(resolved_income_rows) / len(income_rows) * 100.0) if isinstance(income_rows, list) and len(income_rows) > 0 else 100.0
    health_alerts = _dedupe_text_items(alerts.get("health_alerts", []) if isinstance(alerts, dict) else [])

    lines = [
        "## AI Context Summary",
        f"- Portfolio Value: {_format_currency(float(totals.get('portfolio_value', 0.0) or 0.0))}",
        f"- Estimated Annual Income: {_format_currency(float(totals.get('annual_income', 0.0) or 0.0))}",
        "",
        "## Portfolio Health",
        f"- Health score: {_resolve_health_score_value(health):.1f}/100",
        "",
        "## Alerts",
        *([f"- {item}" for item in health_alerts] if include_alerts and health_alerts else (["- Alerts are summarized in the primary export sections above."] if not include_alerts else ["- No alerts."])),
        "",
        "## ESPP Context",
        f"- ESPP ticker: {contributions.get('espp', {}).get('ticker', 'HD') if isinstance(contributions.get('espp', {}), dict) else 'HD'}",
        f"- biweekly contribution: {_format_currency(float(contributions.get('espp', {}).get('biweekly_contribution', ESPP_CONTRIB_BIWEEKLY) or ESPP_CONTRIB_BIWEEKLY))}",
        f"- annualized contribution: {_format_currency(float(contributions.get('espp', {}).get('annualized_contribution', ESPP_CONTRIB_BIWEEKLY * 26) or ESPP_CONTRIB_BIWEEKLY * 26))}",
        f"- purchase discount: {_normalize_percentage_value(contributions.get('espp', {}).get('purchase_discount', ESPP_DISCOUNT), ESPP_DISCOUNT * 100.0):.1f}%",
        f"- purchase frequency: {contributions.get('espp', {}).get('purchase_frequency', ESPP_PURCHASE_FREQUENCY) if isinstance(contributions.get('espp', {}), dict) else ESPP_PURCHASE_FREQUENCY}",
        "",
        "## Contribution Context",
        f"- Regular biweekly: {_format_currency(float(contributions.get('regular_biweekly', DEFAULT_PROJECTION_BIWEEKLY_CONTRIB) or DEFAULT_PROJECTION_BIWEEKLY_CONTRIB))}",
        f"- ESPP biweekly: {_format_currency(float(contributions.get('espp_biweekly', ESPP_CONTRIB_BIWEEKLY) or ESPP_CONTRIB_BIWEEKLY))}",
        f"- Combined annual contributions: {_format_currency(float(contributions.get('total_annual_contributions', (DEFAULT_PROJECTION_BIWEEKLY_CONTRIB + ESPP_CONTRIB_BIWEEKLY) * 26) or (DEFAULT_PROJECTION_BIWEEKLY_CONTRIB + ESPP_CONTRIB_BIWEEKLY) * 26))}",
        "",
        "## Asset Allocation",
        "```json",
        json.dumps(snapshot.get("allocation", {}), indent=2, default=str, allow_nan=False),
        "```",
        "",
        "## Sector Exposure",
        "```json",
        json.dumps(exposure, indent=2, default=str, allow_nan=False),
        "```",
        "",
        "## Income Allocation",
        "```json",
        json.dumps(income, indent=2, default=str, allow_nan=False),
        "```",
        "",
        "## Economic Exposure",
        "```json",
        json.dumps(snapshot.get("allocation", {}).get("economic_exposure", {}), indent=2, default=str, allow_nan=False),
        "```",
        "",
        "## Historical Context",
        "```json",
        json.dumps(historical, indent=2, default=str, allow_nan=False),
        "```",
        "",
        "## Holdings Table",
        "```csv",
        holdings_csv,
        "```",
        "",
        "## Calculated Metrics Table",
        "```csv",
        holdings_csv,
        "```",
        "",
        "## Notes",
        "- Export generated from unified compute_full_portfolio_snapshot output.",
        "",
        "Export Integrity Check:",
        "- Snapshot completeness: 100%",
        f"- Sector resolution: {float(exposure.get('exposure_completeness_pct', 0.0) or 0.0):.1f}%",
        f"- Income resolution: {income_resolution:.1f}%",
        f"- Supplemental ticker-history coverage: {historical_coverage:.1f}% of holdings",
    ]

    return "\n".join(lines)


def _build_ai_holdings_export_frame(holdings: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    normalized_holdings = _normalize_columns(holdings.copy(), drop_blank_tickers=True)
    metrics_df = metrics.copy().fillna(0)

    if metrics_df.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "shares",
                "estimated_annual_distribution_per_share",
                "annualized_income",
            ]
        )

    metric_lookup = {}
    for _, row in metrics_df.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if ticker:
            metric_lookup[ticker] = row

    export_rows: list[dict[str, object]] = []
    for _, row in normalized_holdings.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        metric_row = metric_lookup.get(ticker, {})
        estimated_annual_distribution = float(metric_row.get("effective_annual_div_per_share", 0.0) or 0.0)
        shares = float(row.get("shares", 0.0) or 0.0)
        annualized_income = float(
            metric_row.get("annual_income", shares * estimated_annual_distribution) or (shares * estimated_annual_distribution)
        )

        raw_discount_pct = row.get("espp_discount_pct", row.get("espp_discount", ESPP_DISCOUNT) or ESPP_DISCOUNT)
        espp_discount_pct = _normalize_percentage_value(raw_discount_pct, ESPP_DISCOUNT * 100.0)

        export_rows.append(
            {
                "ticker": ticker,
                "shares": shares,
                "estimated_annual_distribution_per_share": estimated_annual_distribution,
                "annualized_income": annualized_income,
                "regular_biweekly_contribution": float(metric_row.get("regular_biweekly_contribution", 0.0) or 0.0),
                "espp_biweekly_contribution": float(metric_row.get("espp_biweekly_contribution", 0.0) or 0.0),
                "market_value": float(metric_row.get("market_value", 0.0) or 0.0),
                "asset_type": str(metric_row.get("asset_type", row.get("asset_type", "")) or ""),
                "sector": _normalize_sector_label(metric_row.get("sector", row.get("sector", "")) or "", unresolved_label=""),
                "employer_stock": bool(row.get("employer_stock", False) or (ticker == "HD")),
                "espp_enabled": bool(row.get("espp_enabled", False) or (ticker == "HD")),
                "espp_purchase_frequency": str(row.get("espp_purchase_frequency", "") or ""),
                "espp_discount_pct": espp_discount_pct,
            }
        )

    return pd.DataFrame(export_rows)


def _build_contribution_context(
    holdings: pd.DataFrame,
    metrics: pd.DataFrame,
    biweekly_contribution: float = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
    espp_contrib_biweekly: float = ESPP_CONTRIB_BIWEEKLY,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> dict[str, object]:
    normalized_holdings = _normalize_columns(holdings.copy(), drop_blank_tickers=True)
    metrics_df = metrics.copy().fillna(0)

    metric_lookup: dict[str, pd.Series] = {}
    for _, row in metrics_df.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if ticker:
            metric_lookup[ticker] = row

    contribution_rows: list[dict[str, object]] = []
    for _, row in normalized_holdings.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        metric_row = metric_lookup.get(ticker, {})
        regular_contrib = float(
            metric_row.get("regular_biweekly_contribution", row.get("regular_biweekly_contribution", 0.0))
            or row.get("regular_biweekly_contribution", 0.0)
        )
        espp_contrib = float(
            metric_row.get("espp_biweekly_contribution", row.get("espp_biweekly_contribution", 0.0))
            or row.get("espp_biweekly_contribution", 0.0)
        )
        special_contrib = float(
            metric_row.get("special_contribution", row.get("special_contribution", 0.0))
            or row.get("special_contribution", 0.0)
        )
        if ticker == "HD":
            regular_contrib = 0.0
            special_contrib = 0.0
            if espp_contrib <= 0:
                espp_contrib = espp_contrib_biweekly
        contribution_note = ""
        contribution_frequency = None
        contribution_schedule = None
        contribution_type = "regular"
        employer_stock = bool(row.get("employer_stock", False) or (ticker == "HD"))
        espp_enabled = bool(row.get("espp_enabled", False) or employer_stock or ticker == "HD")
        espp_frequency_label = str(row.get("espp_purchase_frequency", "")).strip() or (
            "approximately_every_6_months" if espp_enabled else ""
        )
        espp_discount_pct = _normalize_percentage_value(
            row.get("espp_discount_pct", row.get("espp_discount", espp_discount)),
            espp_discount * 100.0,
        )
        if ticker == "HD":
            contribution_note = "HD is treated as an ESPP contribution made every biweekly paycheck, with purchases occurring approximately every six months."
            contribution_frequency = "biweekly"
            contribution_schedule = espp_purchase_frequency
            contribution_type = "espp"

        contribution_rows.append(
            {
                "ticker": ticker,
                "regular_biweekly_contribution": regular_contrib,
                "espp_biweekly_contribution": espp_contrib,
                "special_contribution": special_contrib,
                "total_contribution": regular_contrib + espp_contrib + special_contrib,
                "contribution_type": contribution_type,
                "contribution_frequency": contribution_frequency,
                "purchase_frequency": contribution_schedule,
                "employer_stock": employer_stock,
                "espp_enabled": espp_enabled,
                "espp_purchase_frequency": espp_frequency_label if espp_enabled else None,
                "espp_discount_pct": espp_discount_pct if espp_enabled else None,
                "contribution_note": contribution_note,
            }
        )

    regular_contribution_total = sum(float(item.get("regular_biweekly_contribution", 0.0) or 0.0) for item in contribution_rows)
    espp_contribution_total = sum(float(item.get("espp_biweekly_contribution", 0.0) or 0.0) for item in contribution_rows)
    annual_regular_contrib = regular_contribution_total * 26
    annual_espp_contrib = espp_contribution_total * 26
    combined_annual_contrib = annual_regular_contrib + annual_espp_contrib

    metrics_df_numeric = metrics_df.copy()
    if "market_value" not in metrics_df_numeric.columns:
        metrics_df_numeric["market_value"] = 0.0
    if "annual_income" not in metrics_df_numeric.columns:
        metrics_df_numeric["annual_income"] = 0.0
    metrics_df_numeric["market_value"] = pd.to_numeric(metrics_df_numeric["market_value"], errors="coerce").fillna(0.0)
    metrics_df_numeric["annual_income"] = pd.to_numeric(metrics_df_numeric["annual_income"], errors="coerce").fillna(0.0)
    total_portfolio_value = float(metrics_df_numeric["market_value"].sum()) if not metrics_df_numeric.empty else 0.0
    total_portfolio_income = float(metrics_df_numeric["annual_income"].sum()) if not metrics_df_numeric.empty else 0.0

    employer_rows = [item for item in contribution_rows if bool(item.get("employer_stock", False))]
    employer_concentration_rows: list[dict[str, object]] = []
    for item in employer_rows:
        ticker = str(item.get("ticker", "")).strip().upper()
        metric_row = metric_lookup.get(ticker, {})
        market_value = float(metric_row.get("market_value", 0.0) or 0.0)
        annual_income = float(metric_row.get("annual_income", 0.0) or 0.0)
        employer_concentration_rows.append(
            {
                "ticker": ticker,
                "capital_concentration_pct": (market_value / total_portfolio_value * 100.0) if total_portfolio_value > 0 else 0.0,
                "income_concentration_pct": (annual_income / total_portfolio_income * 100.0) if total_portfolio_income > 0 else 0.0,
            }
        )

    primary_employer = employer_concentration_rows[0] if employer_concentration_rows else {"ticker": "", "capital_concentration_pct": 0.0, "income_concentration_pct": 0.0}
    concentration_alert = {
        "primary_employer_ticker": str(primary_employer.get("ticker", "")),
        "primary_employer_capital_concentration_pct": float(primary_employer.get("capital_concentration_pct", 0.0) or 0.0),
        "primary_employer_income_concentration_pct": float(primary_employer.get("income_concentration_pct", 0.0) or 0.0),
        "is_concentration_alert": float(primary_employer.get("capital_concentration_pct", 0.0) or 0.0) >= 40.0,
    }

    espp_purchase_frequency_value = str(espp_purchase_frequency)
    for item in contribution_rows:
        value = str(item.get("espp_purchase_frequency", "") or "").strip()
        if value:
            espp_purchase_frequency_value = value
            break

    hd_espp_contribution_per_paycheck = espp_contribution_total if espp_contribution_total > 0 else espp_contrib_biweekly
    hd_espp_discount_pct = 0.0
    for item in contribution_rows:
        if bool(item.get("espp_enabled", False)) and item.get("espp_discount_pct") not in [None, ""]:
            hd_espp_discount_pct = _normalize_percentage_value(item.get("espp_discount_pct", 0.0), 0.0)
            break
    if hd_espp_discount_pct <= 0:
        hd_espp_discount_pct = _normalize_percentage_value(espp_discount, ESPP_DISCOUNT * 100.0)

    summary = {
        "total_regular_biweekly_contribution": round(regular_contribution_total, 2),
        "total_regular_biweekly_contributions": round(regular_contribution_total, 2),
        "annual_regular_contribution": round(annual_regular_contrib, 2),
        "annual_regular_contributions": round(annual_regular_contrib, 2),
        "total_espp_biweekly_contributions": round(espp_contribution_total, 2),
        "total_espp_biweekly_contribution": round(espp_contribution_total, 2),
        "estimated_annual_regular_contributions": round(annual_regular_contrib, 2),
        "estimated_annual_espp_contributions": round(annual_espp_contrib, 2),
        "estimated_combined_annual_contributions": round(combined_annual_contrib, 2),
        "annual_espp_contribution": round(annual_espp_contrib, 2),
        "annual_espp_contributions": round(annual_espp_contrib, 2),
        "combined_annual_contribution": round(combined_annual_contrib, 2),
        "combined_annual_contributions": round(combined_annual_contrib, 2),
        "hd_espp_contribution_frequency": "biweekly",
        "hd_espp_purchase_frequency": espp_purchase_frequency_value,
        "hd_espp_contribution_per_paycheck": round(hd_espp_contribution_per_paycheck, 2),
        "annual_hd_espp_contribution": round(annual_espp_contrib, 2),
        "hd_espp_discount_pct": round(hd_espp_discount_pct, 2),
        "estimated_combined_annual_contribution": round(combined_annual_contrib, 2),
        "holdings_with_regular_contributions": sum(1 for item in contribution_rows if float(item.get("regular_biweekly_contribution", 0.0) or 0.0) > 0),
        "holdings_with_espp_contributions": sum(1 for item in contribution_rows if float(item.get("espp_biweekly_contribution", 0.0) or 0.0) > 0),
    }
    return {
        "holdings": contribution_rows,
        "summary": summary,
        "employer_stock_concentration_context": concentration_alert,
        "employer_stock_rows": employer_concentration_rows,
    }


def _build_ai_export_payload(
    holdings: pd.DataFrame,
    metrics: pd.DataFrame,
    biweekly_contribution: float = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
    projection_return_pct: float = 8.0,
    espp_contrib_biweekly: float = ESPP_CONTRIB_BIWEEKLY,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> dict[str, object]:
    metrics = _prepare_export_metrics(metrics)
    snapshot = _build_portfolio_snapshot(metrics, biweekly_contribution, projection_return_pct)
    health = _build_portfolio_health(metrics)
    health_context_summary = _build_health_context_summary(health)
    data_completeness = _build_data_completeness(metrics)
    metrics_tickers = set(metrics["ticker"].fillna("").astype(str).str.strip().str.upper()) if "ticker" in metrics.columns else set()
    has_hd_position = "HD" in metrics_tickers
    primary_alert_text = str(health_context_summary["primary_alert"])
    if has_hd_position and primary_alert_text.startswith("Concentration alert: HD"):
        primary_alert_text = (
            primary_alert_text
            + " HD shares are acquired through the user's employer-sponsored Employee Stock Purchase Plan (ESPP), "
            + "so the concentration should be evaluated in the context of ESPP participation, employer exposure, "
            + "purchase discounts, holding strategy, and investment goals rather than treated as an automatic sell signal."
        )
    issues = _flag_data_quality_issues(metrics, holdings)
    concentration_context = _build_concentration_context(metrics)
    lookthrough_summary = _resolve_lookthrough_sector_exposure(metrics)
    portfolio_level = _build_portfolio_level_calculations(metrics)
    espp_config = {
        "discount": espp_discount,
        "biweekly_contribution": espp_contrib_biweekly,
        "purchase_frequency_months": _parse_espp_frequency_months(espp_purchase_frequency),
    }
    intelligence_layer_output = compute_portfolio_intelligence(metrics, lookthrough_summary, espp_config)

    if "asset_type" not in metrics.columns:
        metrics = metrics.copy()
        metrics["asset_type"] = ""

    asset_allocation = []
    if not metrics.empty:
        allocation_df = metrics.groupby("asset_type", dropna=False).agg(
            market_value=("market_value", "sum"),
            annual_income=("annual_income", "sum"),
        ).reset_index()
        allocation_df["asset_type"] = allocation_df["asset_type"].replace("", "Unresolved Asset Type")
        allocation_df["asset_type"] = allocation_df["asset_type"].map(_display_asset_type_label)
        total_value = float(allocation_df["market_value"].sum()) if not allocation_df.empty else 0.0
        allocation_df["portfolio_weight_pct"] = allocation_df["market_value"] / total_value * 100 if total_value > 0 else 0.0
        asset_allocation = allocation_df.to_dict(orient="records")

    direct_sector_exposure = []
    if not metrics.empty:
        for row in _build_direct_sector_classification_records(metrics):
            direct_sector_exposure.append(
                {
                    "sector": row["Sector"],
                    "market_value": row["Market_Value"],
                    "annual_income": row["Annual_Income"],
                    "portfolio_weight_pct": row["Portfolio_Weight_Pct"],
                    "pct_of_resolved_direct_sector_exposure": row["Pct_of_Resolved_Direct_Sector_Exposure"],
                }
            )

    direct_holdings_by_classification = []
    if not metrics.empty:
        for row in _build_direct_holdings_by_classification_records(metrics):
            direct_holdings_by_classification.append(
                {
                    "classification": row["Classification"],
                    "holdings": row["Holdings"],
                    "market_value": row["Market_Value"],
                    "annual_income": row["Annual_Income"],
                    "portfolio_weight_pct": row["Portfolio_Weight"],
                }
            )

    look_through_sector_exposure = []
    if not metrics.empty:
        for row in lookthrough_summary.get("records", []):
            look_through_sector_exposure.append(
                {
                    "sector": row["Sector"],
                    "source_holdings": row["Source_Holdings"],
                    "market_value": row["Market_Value"],
                    "portfolio_weight_pct": row["Portfolio_Weight"],
                }
            )

    unresolved_fund_look_through_exposure = []
    if not metrics.empty:
        for row in lookthrough_summary.get("unresolved_records", []):
            unresolved_fund_look_through_exposure.append(
                {
                    "fund_type": row["Fund_Type"],
                    "holdings": row["Holdings"],
                    "market_value": row["Market_Value"],
                    "portfolio_weight_pct": row["Portfolio_Weight"],
                }
            )

    income_by_holding = []
    if not metrics.empty:
        holding_df = metrics[["ticker", "annual_income", "market_value"]].copy()
        holding_df = holding_df.sort_values("annual_income", ascending=False)
        total_income = float(holding_df["annual_income"].sum()) if not holding_df.empty else 0.0
        holding_df["income_allocation_pct"] = holding_df["annual_income"] / total_income * 100 if total_income > 0 else 0.0
        income_by_holding = holding_df.to_dict(orient="records")

    income_by_asset_type = []
    if not metrics.empty:
        asset_income_df = metrics.groupby("asset_type", dropna=False).agg(
            annual_income=("annual_income", "sum"),
            market_value=("market_value", "sum"),
        ).reset_index()
        asset_income_df["asset_type"] = asset_income_df["asset_type"].replace("", "Unresolved Asset Type")
        asset_income_df["asset_type"] = asset_income_df["asset_type"].map(_display_asset_type_label)
        total_income = float(asset_income_df["annual_income"].sum()) if not asset_income_df.empty else 0.0
        asset_income_df["income_allocation_pct"] = asset_income_df["annual_income"] / total_income * 100 if total_income > 0 else 0.0
        income_by_asset_type = asset_income_df.to_dict(orient="records")

    top_holdings = []
    if not metrics.empty:
        top_holdings_df = metrics[["ticker", "market_value", "annual_income"]].copy()
        top_holdings_df = top_holdings_df.sort_values("market_value", ascending=False).head(5)
        top_holdings = top_holdings_df.to_dict(orient="records")

    top_income_contributors = []
    if not metrics.empty:
        top_income_df = metrics[["ticker", "annual_income", "market_value"]].copy()
        top_income_df = top_income_df.sort_values("annual_income", ascending=False).head(5)
        top_income_contributors = top_income_df.to_dict(orient="records")

    economic_exposure = []
    if not metrics.empty:
        exposure_df = metrics.copy()
        exposure_df["economic_exposure_category"] = exposure_df["asset_type"].replace(
            {"Stock": "Equities", "ETF": "Equities", "REIT": "REITs", "CEF": "Alternative Income", "BDC": "Alternative Income", "Bond Fund": "Bonds", "Other": "Other"}
        )
        exposure_df = exposure_df.groupby("economic_exposure_category", dropna=False).agg(
            market_value=("market_value", "sum"),
            annual_income=("annual_income", "sum"),
        ).reset_index()
        total_value = float(exposure_df["market_value"].sum()) if not exposure_df.empty else 0.0
        exposure_df["portfolio_weight_pct"] = exposure_df["market_value"] / total_value * 100 if total_value > 0 else 0.0
        economic_exposure = exposure_df.to_dict(orient="records")

    export_holdings = _build_ai_holdings_export_frame(holdings, metrics)
    contribution_context = _build_contribution_context(
        holdings,
        metrics,
        biweekly_contribution,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    espp_context = {
        **(intelligence_layer_output.get("espp_context", {}) if isinstance(intelligence_layer_output, dict) else {}),
        "espp_discount": espp_discount,
        "espp_contrib_biweekly": espp_contrib_biweekly,
        "espp_purchase_frequency": espp_purchase_frequency,
        "employer_ticker": "HD",
    }

    supplemental_yahoo_financial_data = []
    if _strict_export_accuracy_mode() and _use_export_json_data():
        export_payload = st.session_state.get("export_payload", {})
        supplemental_yahoo_financial_data = export_payload.get("supplemental_yahoo_financial_data", []) if isinstance(export_payload, dict) else []
    elif not metrics.empty:
        tickers = tuple(str(ticker).strip().upper() for ticker in metrics["ticker"].dropna().astype(str).tolist() if str(ticker).strip())
        intelligence_map = _latest_investment_intelligence(tickers)
        for ticker in tickers:
            entry = intelligence_map.get(ticker)
            if not entry:
                continue
            supplemental_yahoo_financial_data.append(
                {
                    "ticker": ticker,
                    "fundamental_metrics": entry.get("fundamental_metrics", {}),
                    "earnings_and_estimates": entry.get("earnings_and_estimates", {}),
                    "analyst_targets": entry.get("analyst_targets", {}),
                    "fund_data": entry.get("fund_data", {}),
                    "data_source": entry.get("data_source", "Yahoo Finance"),
                }
            )

    historical_context = []
    if not metrics.empty:
        tickers = tuple(str(ticker).strip().upper() for ticker in metrics["ticker"].dropna().astype(str).tolist() if str(ticker).strip())
        history_map = _resolved_historical_context(tickers)
        for ticker in tickers:
            entry = history_map.get(ticker, {})
            if entry:
                historical_context.append(
                    {
                        "ticker": ticker,
                        "price_trend": entry.get("price_trend", {}),
                        "dividend_trend": entry.get("dividend_trend", {}),
                        "start_price": entry.get("start_price"),
                        "end_price": entry.get("end_price"),
                        "return_pct": entry.get("return_pct"),
                        "52w_high": entry.get("52w_high"),
                        "52w_low": entry.get("52w_low"),
                        "drawdown": entry.get("drawdown"),
                        "volatility": entry.get("volatility"),
                        "history_summary": entry.get("history_summary", ""),
                    }
                )

    statement_history_db = DATA_DIR / "stash_statement_history.sqlite"

    supplemental_yahoo_historical_price_data = []
    if _strict_export_accuracy_mode() and _use_export_json_data():
        export_payload = st.session_state.get("export_payload", {})
        supplemental_yahoo_historical_price_data = export_payload.get("supplemental_yahoo_historical_price_data", []) if isinstance(export_payload, dict) else []
    elif not metrics.empty:
        tickers = tuple(str(ticker).strip().upper() for ticker in metrics["ticker"].dropna().astype(str).tolist() if str(ticker).strip())
        for ticker in tickers:
            try:
                proxy = yf.Ticker(ticker)
                history = proxy.history(period="2y")
                if history.empty:
                    continue

                history = history.reset_index()
                history_records = []
                for _, row in history.iterrows():
                    history_records.append(
                        {
                            "date": row["Date"].date().isoformat() if hasattr(row["Date"], "date") else str(row["Date"]),
                            "open": float(row.get("Open", 0.0) or 0.0),
                            "high": float(row.get("High", 0.0) or 0.0),
                            "low": float(row.get("Low", 0.0) or 0.0),
                            "close": float(row.get("Close", 0.0) or 0.0),
                            "adjusted_close": float(row.get("Adj Close", row.get("Close", 0.0) or 0.0)),
                            "volume": int(row.get("Volume", 0) or 0),
                        }
                    )

                supplemental_yahoo_historical_price_data.append(
                    {
                        "ticker": ticker,
                        "history_type": "supplemental",
                        "source": "Yahoo Finance",
                        "label": "Supplemental Yahoo Finance Historical Price Data",
                        "date_range": {
                            "start": history_records[0]["date"] if history_records else None,
                            "end": history_records[-1]["date"] if history_records else None,
                        },
                        "historical_price_data": history_records,
                    }
                )
            except Exception:
                continue

    dividend_data_metadata = {
        "source": "Yahoo Finance",
        "data_type": "current_yield",
        "method": "Current dividend yield used to estimate annualized income",
        "verified": False,
    }

    investor_profile = {
        "portfolio_strategy": "Dividend growth and income",
        "primary_goal": "Build long-term wealth while increasing dividend income",
        "risk_tolerance": "Moderate",
        "investment_horizon_years": 20,
        "contribution_frequency": "Biweekly",
        "contribution_amount": biweekly_contribution,
    }

    recommendation_context = {
        "decision_objective": "Recommend whether to increase, maintain, or reduce contributions and how to allocate new capital across holdings.",
        "recommended_actions": [
            {
                "action": "Review contribution sizing",
                "reason": "Use the regular and special contribution context to decide whether to increase, maintain, or reduce future contributions based on current portfolio balance and concentration.",
                "priority": "medium",
            },
            {
                "action": "Assess rebalancing through new contributions",
                "reason": "Prefer new contributions as the first lever for repositioning the portfolio rather than forcing sales when concentration or income allocation is off-target.",
                "priority": "medium",
            },
        ],
        "focus_areas": [
            "concentration_risk",
            "income_stability",
            "dividend_growth",
            "special_contribution_timing",
        ],
        "must_consider": [
            "current concentration risks",
            "income stability",
            "dividend growth potential",
            "special contribution timing and employer-related exposure",
            "preference for new contributions over selling",
        ],
        "prompt": "Using the portfolio snapshot, contribution context, historical context, and constraints, produce a concise recommendation with recommended_action, rationale, expected_tradeoffs, and next_steps.",
        "output_schema": {
            "recommended_action": "string",
            "priority": "low|medium|high",
            "target_tickers": ["string"],
            "contribution_changes": [
                {
                    "ticker": "string",
                    "current_biweekly": "number",
                    "recommended_biweekly": "number",
                    "change": "number",
                    "reason": "string",
                }
            ],
            "rationale": "string",
            "expected_tradeoffs": ["string"],
            "next_steps": ["string"],
        },
    }

    recommendation_constraints = {
        "avoid_forced_selling": True,
        "prefer_new_contributions_for_rebalancing": True,
        "prioritize_dividend_growth": True,
        "prioritize_total_return": True,
        "consider_income_stability": True,
        "do_not_recommend_based_on_yield_alone": True,
    }

    analysis_guidance = [
        "Treat direct sector exposure separately from look-through sector exposure for fund holdings.",
        "Direct sector exposure represents individually held securities with a resolved sector classification.",
        "Look-through sector exposure represents estimated underlying sector exposure from funds using available Yahoo Finance fund-sector weights.",
        "Unresolved fund look-through exposure represents fund assets whose underlying sector weights could not be resolved. These assets remain included in portfolio totals but are excluded from resolved look-through sector calculations.",
        "Treat unresolved fund look-through exposure as invested capital with missing underlying sector decomposition, not as an unknown sector.",
        "Evaluate capital concentration separately from income concentration. A holding may represent a large percentage of portfolio value without representing an equivalent percentage of portfolio income.",
    ]

    strict_mode = _strict_export_accuracy_mode()
    use_export_data = _use_export_json_data()
    missing_export_fields = st.session_state.get("_missing_export_fields", set()) if strict_mode and use_export_data else set()
    if not isinstance(missing_export_fields, set):
        missing_export_fields = set(missing_export_fields) if isinstance(missing_export_fields, (list, tuple)) else set()

    historical_context_map = {str(item.get("ticker", "")).strip().upper(): item for item in historical_context if isinstance(item, dict)}
    unified_snapshot = compute_full_portfolio_snapshot(
        metrics,
        holdings=holdings,
        lookthrough_summary=lookthrough_summary,
        contribution_context=contribution_context,
        historical_context_map=historical_context_map,
        health=health,
        intelligence_layer_output=intelligence_layer_output,
    )
    snapshot_validation_errors: list[str] = []
    try:
        validate_snapshot(unified_snapshot)
    except ValueError as exc:
        snapshot_validation_errors.append(str(exc))

    json_contract = generate_portfolio_export_json(unified_snapshot)

    return {
        "snapshot_timestamp": json_contract.get("snapshot_timestamp"),
        "currency": json_contract.get("currency", "USD"),
        "schema_version": json_contract.get("schema_version", "2.0"),
        "totals": json_contract.get("totals", {}),
        "allocation": json_contract.get("allocation", {}),
        "exposure": json_contract.get("exposure", {}),
        "income": json_contract.get("income", {}),
        "contributions": json_contract.get("contributions", {}),
        "historical": json_contract.get("historical", {}),
        "health_score": json_contract.get("health_score", {}),
        "alerts": json_contract.get("alerts", {}),
        "export_version": EXPORT_VERSION,
        "export_generated_timestamp": datetime.now().isoformat(timespec="seconds"),
        "unified_snapshot": unified_snapshot,
        "snapshot_validation": {
            "valid": len(snapshot_validation_errors) == 0,
            "errors": snapshot_validation_errors,
        },
        "strict_export_accuracy_mode": strict_mode,
        "data_authority_flags": {
            "use_export_json_data": use_export_data,
            "strict_export_accuracy_mode": strict_mode,
            "live_market_fallback_enabled": not strict_mode,
            "inference_enabled": not strict_mode,
        },
        "warnings": {
            "missing_export_fields": sorted(str(field_name) for field_name in missing_export_fields),
            "snapshot_validation_errors": snapshot_validation_errors,
        },
        "portfolio_snapshot": snapshot,
        "portfolio_intelligence_layer": intelligence_layer_output,
        "intelligence_summary": {
            "income_quality_score": float(intelligence_layer_output.get("income_quality_score", 0.0) or 0.0),
            "income_quality_score_label": "Portfolio Intelligence Income Score",
            "distribution_stability_score": float(intelligence_layer_output.get("distribution_stability_score", 0.0) or 0.0),
            "concentration_risk_score": float(intelligence_layer_output.get("concentration_risk_score", 0.0) or 0.0),
            "asset_type_risk_score": float(intelligence_layer_output.get("asset_type_risk_score", 0.0) or 0.0),
            "sector_balance_score": float(intelligence_layer_output.get("sector_balance_score", 0.0) or 0.0),
            "health_score": float(intelligence_layer_output.get("health_score", 0.0) or 0.0),
        },
        "intelligence_alerts": intelligence_layer_output.get("alerts", []),
        "intelligence_notes": intelligence_layer_output.get("notes", []),
        "portfolio_level_calculations": portfolio_level,
        "portfolio_health": {
            **health,
            "context_summary": health_context_summary,
        },
        "income_quality": health.get("income_quality", {}) if isinstance(health, dict) else {},
        "income_score_metadata": {
            "portfolio_intelligence_income_score_label": "Portfolio Intelligence Income Score",
            "income_quality_engine_score_label": "Income Quality Engine Score",
            "methodologies_are_distinct": True,
        },
        "asset_allocation": asset_allocation,
        "direct_sector_exposure": direct_sector_exposure,
        "direct_holdings_by_classification": direct_holdings_by_classification,
        "look_through_sector_exposure": look_through_sector_exposure,
        "unresolved_fund_look_through_exposure": unresolved_fund_look_through_exposure,
        "exposure_engine": {
            "direct_sector_exposure": direct_sector_exposure,
            "look_through_sector_exposure": look_through_sector_exposure,
            "unresolved_exposure": {
                "unresolved_fund_exposure": float(lookthrough_summary.get("unresolved_value", 0.0) or 0.0),
                "resolved_lookthrough_value": float(lookthrough_summary.get("resolved_value", 0.0) or 0.0),
                "fund_total_value": float(lookthrough_summary.get("fund_total_value", 0.0) or 0.0),
            },
            "asset_type_allocation": asset_allocation,
            "income_allocation": {
                "by_holding": income_by_holding,
                "by_asset_type": income_by_asset_type,
            },
        },
        "income_allocation_by_holding": income_by_holding,
        "income_allocation_by_asset_type": income_by_asset_type,
        "income_quality_classification": _build_income_quality_records(metrics),
        "top_holdings": top_holdings,
        "top_income_contributors": top_income_contributors,
        "economic_exposure_allocation": economic_exposure,
        "espp_context": espp_context,
        "dividend_data_metadata": dividend_data_metadata,
        "data_quality_issues": issues,
        "data_completeness": data_completeness,
        "unresolved_exposure": {
            "unresolved_fund_exposure": float(lookthrough_summary.get("unresolved_value", 0.0) or 0.0),
            "resolved_lookthrough_value": float(lookthrough_summary.get("resolved_value", 0.0) or 0.0),
            "fund_total_value": float(lookthrough_summary.get("fund_total_value", 0.0) or 0.0),
        },
        "concentration_context": concentration_context,
        "contribution_context": contribution_context,
        "historical_context": historical_context,
        "supplemental_yahoo_financial_data": supplemental_yahoo_financial_data,
        "supplemental_yahoo_historical_price_data": supplemental_yahoo_historical_price_data,
        "analysis_guidance": analysis_guidance,
        "tracker_health_score_metadata": {
            "data_quality_explanation": health.get("data_quality_explanation", ""),
            "income_stability_explanation": health.get("income_stability_explanation", ""),
            "risk_explanation": health.get("risk_explanation", ""),
        },
        "holdings": export_holdings.to_dict(orient="records"),
        "calculated_metrics": metrics.to_dict(orient="records"),
        "investor_profile": investor_profile,
        "recommendation_context": recommendation_context,
        "recommendation_constraints": recommendation_constraints,
        "field_definitions": {
            "estimated_annual_distribution_per_share": "Estimated annual distribution per share calculated from the current Yahoo Finance yield and current market price. This is an estimate and may differ from the issuer's actual forward distribution.",
            "annualized_income": "Annualized portfolio income estimated from shares multiplied by the tracker-calculated estimated annual distribution per share.",
        },
    }


def _prepare_export_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    export_metrics = metrics.copy()
    if "annual_div_per_share" in export_metrics.columns:
        reported_dividend_values = export_metrics["annual_div_per_share"].apply(
            lambda value: None if pd.isna(value) or float(value) <= 0 else float(value)
        )
        export_metrics["reported_annual_div_per_share"] = reported_dividend_values
        export_metrics["annual_div_per_share"] = None
    if "sector" in export_metrics.columns:
        export_metrics["sector"] = export_metrics["sector"].apply(lambda value: _normalize_sector_label(value, unresolved_label=""))
    export_metrics = export_metrics.fillna(0)
    if "reported_annual_div_per_share" in export_metrics.columns:
        export_metrics["reported_annual_div_per_share"] = export_metrics["reported_annual_div_per_share"].where(
            export_metrics["reported_annual_div_per_share"].notna(), None
        )
    return export_metrics


def _canonicalize_export_historical_context(payload: dict[str, object]) -> dict[str, object]:
    supplemental = payload.get("supplemental_yahoo_historical_price_data", [])
    if not isinstance(supplemental, list):
        return payload
    raw_by_ticker = {
        str(item.get("ticker", "")).strip().upper(): item
        for item in supplemental
        if isinstance(item, dict) and str(item.get("ticker", "")).strip()
    }
    existing_by_ticker = {
        str(item.get("ticker", "")).strip().upper(): item
        for item in payload.get("historical_context", [])
        if isinstance(item, dict) and str(item.get("ticker", "")).strip()
    }
    holding_tickers = [
        str(item.get("ticker", "")).strip().upper()
        for item in payload.get("holdings", [])
        if isinstance(item, dict) and str(item.get("ticker", "")).strip()
    ]
    ordered_tickers = list(dict.fromkeys([*holding_tickers, *raw_by_ticker, *existing_by_ticker]))
    finalized_supplemental: list[dict[str, object]] = []
    canonical_by_ticker: dict[str, dict[str, object]] = {}
    for ticker in ordered_tickers:
        raw = raw_by_ticker.get(ticker, {"ticker": ticker})
        canonical = _canonical_supplemental_history_record(raw)
        if canonical is None:
            canonical = _canonical_supplemental_history_record(existing_by_ticker.get(ticker))
        finalized = {**raw, **canonical} if canonical else raw
        finalized["ticker"] = ticker
        finalized_supplemental.append(finalized)
        if canonical:
            canonical_by_ticker[ticker] = canonical

    for ticker, derived in canonical_by_ticker.items():
        existing_by_ticker[ticker] = {
            **existing_by_ticker.get(ticker, {}),
            **derived,
            "history_summary": _summarize_yahoo_historical_price_context(derived),
        }

    result = dict(payload)
    result["supplemental_yahoo_historical_price_data"] = finalized_supplemental
    result["historical_context"] = list(existing_by_ticker.values())
    compatibility_historical = {"by_ticker": _historical_contract_records(existing_by_ticker)}
    result["historical"] = compatibility_historical
    unified_snapshot = result.get("unified_snapshot", {})
    if isinstance(unified_snapshot, dict):
        result["unified_snapshot"] = {**unified_snapshot, "historical": compatibility_historical}
    return result


def _build_ai_export_json(
    holdings: pd.DataFrame,
    metrics: pd.DataFrame,
    biweekly_contribution: float = 280.0,
    projection_return_pct: float = 8.0,
    espp_contrib_biweekly: float = ESPP_CONTRIB_BIWEEKLY,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> str:
    payload = _build_ai_export_payload(
        holdings,
        metrics,
        biweekly_contribution,
        projection_return_pct,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    payload = _canonicalize_export_historical_context(payload)
    unified_snapshot = payload.get("unified_snapshot", {}) if isinstance(payload, dict) else {}
    contract_payload = generate_portfolio_export_json(unified_snapshot) if isinstance(unified_snapshot, dict) else {}
    if isinstance(contract_payload, dict) and contract_payload:
        payload = {**contract_payload, **payload}
    payload["generated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["currency"] = "USD"
    payload["export_version"] = "2.0"
    payload["schema_version"] = "2.0"
    payload["export_generated_timestamp"] = datetime.now().isoformat(timespec="seconds")
    payload["export_generated_at"] = datetime.now().isoformat(timespec="seconds")
    return json.dumps(_sanitize_export_value(payload), indent=2, default=str, allow_nan=False)


_AI_ANALYSIS_INSTRUCTIONS_BLOCK = [
    "1. Treat holdings data as authoritative.",
    "2. Use AI Context Summary for convenience.",
    "3. Identify discrepancies.",
    "4. Evaluate concentration by holding, sector, asset type, income source.",
    "5. Treat employer stock separately.",
    "6. Separate facts from projections.",
    "7. Do not assume missing information.",
    "8. Explain trade-offs.",
    "9. Do not recommend buy/sell solely on yield.",
    "10. Consider total return, diversification, risk, income sustainability.",
    "11. Evaluate capital concentration separately from income concentration.",
    "12. Evaluate income quality separately from headline yield.",
    "13. Recognize ESPP discount and employer exposure.",
]


def _safe_float(value: object, default: float = 0.0) -> float:
    numeric_value = _coerce_finite_number(value)
    return default if numeric_value is None else numeric_value


def _records_table_text(records: object, preferred_columns: list[str] | None = None) -> str:
    if not isinstance(records, list) or not records:
        return "(No data available)"
    normalized = [_sanitize_export_value(item) for item in records if isinstance(item, dict)]
    if not normalized:
        return "(No data available)"
    df = pd.DataFrame(normalized)
    if preferred_columns:
        ordered = [col for col in preferred_columns if col in df.columns]
        extras = [col for col in df.columns if col not in ordered]
        df = df[ordered + extras]
    df = df.astype(object).where(pd.notna(df), "N/A")
    return _format_table_for_export(df)


def build_context_summary(export_payload: dict[str, object]) -> list[str]:
    snapshot = export_payload.get("portfolio_snapshot", {}) if isinstance(export_payload, dict) else {}
    portfolio_health = export_payload.get("portfolio_health", {}) if isinstance(export_payload, dict) else {}
    context_summary = portfolio_health.get("context_summary", {}) if isinstance(portfolio_health, dict) else {}
    data_completeness = export_payload.get("data_completeness", {}) if isinstance(export_payload, dict) else {}

    strongest = context_summary.get("strongest_areas", []) if isinstance(context_summary, dict) else []
    weakest = context_summary.get("weakest_areas", []) if isinstance(context_summary, dict) else []

    return [
        "# Dividend Portfolio Tracker Export",
        "",
        "## AI Context Summary",
        "This section is written first so AI tools can understand the portfolio snapshot immediately.",
        "",
        f"Export Version: {str(export_payload.get('export_version', EXPORT_VERSION))}",
        f"Generated: {str(export_payload.get('export_generated_timestamp', export_payload.get('generated', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))}",
        "Currency: USD",
        "",
        f"Total Portfolio Value: {_format_currency(_safe_float(snapshot.get('portfolio_value', 0.0)))}",
        f"Cost Basis: {_format_currency(_safe_float(snapshot.get('cost_basis', 0.0)))}",
        f"Unrealized Gain/Loss: {_format_currency(_safe_float(snapshot.get('unrealized_gain', 0.0)))}",
        f"Estimated Annual Income: {_format_currency(_safe_float(snapshot.get('annual_income', 0.0)))}",
        f"Weighted Portfolio Yield: {_format_percent(_safe_float(snapshot.get('weighted_yield_pct', 0.0)))}",
        f"Projection contribution note: {str(snapshot.get('projection_contribution_note', ''))}",
        f"Number of Holdings: {int(snapshot.get('holdings_count', len(export_payload.get('holdings', [])) if isinstance(export_payload.get('holdings', []), list) else 0))}",
        f"Largest Holding: {str(snapshot.get('top_holding', 'N/A'))}",
        f"Largest Holding Weight: {_format_percent(_safe_float(snapshot.get('top_holding_weight_pct', 0.0)))}",
        f"Tracker Health Score: {_safe_float(context_summary.get('score', portfolio_health.get('health_score', 0.0))):.1f}/100",
        f"Health Score Basis: {str(context_summary.get('score_label', 'Tracker-generated health score based on this export\'s methodology; not an objective portfolio rating.'))}",
        "Data Completeness: "
        + f"Direct sector classification coverage {_format_percent(_safe_float(data_completeness.get('direct_sector_classification_coverage_pct', 0.0)))} of direct holdings by market value; "
        + f"fund look-through coverage {_format_percent(_safe_float(data_completeness.get('fund_lookthrough_coverage_pct', 0.0)))} of fund holdings by market value; "
        + f"overall resolved exposure coverage {_format_percent(_safe_float(data_completeness.get('overall_resolved_exposure_coverage_pct', 0.0)))} of total portfolio market value.",
        "Strongest Areas: " + ", ".join(
            f"{str(item.get('area', 'N/A'))} {_safe_float(item.get('score', 0.0)):.1f}" for item in strongest if isinstance(item, dict)
        ),
        "Weakest Areas: " + ", ".join(
            f"{str(item.get('area', 'N/A'))} {_safe_float(item.get('score', 0.0)):.1f}" for item in weakest if isinstance(item, dict)
        ),
        f"Primary Alert: {str(context_summary.get('primary_alert', 'No health alerts.'))}",
        "",
    ]


def build_concentration_context(export_payload: dict[str, object]) -> list[str]:
    concentration_context = export_payload.get("concentration_context", {}) if isinstance(export_payload, dict) else {}
    return [
        "## Concentration Context",
        f"- Largest holding by value: {str(concentration_context.get('largest_holding_by_value', 'N/A'))} ({_format_percent(_safe_float(concentration_context.get('largest_holding_value_weight_pct', 0.0)))} of portfolio value).",
        f"- That same holding contributes {_format_percent(_safe_float(concentration_context.get('largest_holding_income_weight_pct', 0.0)))} of estimated annual income.",
        f"- Largest income contributor: {str(concentration_context.get('largest_income_contributor', 'N/A'))} ({_format_percent(_safe_float(concentration_context.get('largest_income_weight_pct', 0.0)))} of estimated annual income).",
        f"- {str(concentration_context.get('guidance', 'Capital concentration and income concentration should be evaluated separately.'))}",
        "",
    ]


def build_espp_context(export_payload: dict[str, object]) -> list[str]:
    contribution_context = export_payload.get("contribution_context", {}) if isinstance(export_payload, dict) else {}
    summary = contribution_context.get("summary", {}) if isinstance(contribution_context, dict) else {}
    employer_ctx = contribution_context.get("employer_stock_concentration_context", {}) if isinstance(contribution_context, dict) else {}

    return [
        "## ESPP Context",
        f"- ESPP contribution per paycheck: {_format_currency(_safe_float(summary.get('hd_espp_contribution_per_paycheck', 0.0)))}",
        f"- ESPP annualized contribution: {_format_currency(_safe_float(summary.get('annual_hd_espp_contribution', 0.0)))}",
        f"- ESPP purchase frequency: {str(summary.get('hd_espp_purchase_frequency', 'n/a'))}",
        f"- ESPP discount: {_normalize_percentage_value(summary.get('hd_espp_discount_pct', 0.0), 0.0):.1f}%",
        f"- ESPP holding weight: {_format_percent(_safe_float(employer_ctx.get('primary_employer_capital_concentration_pct', 0.0)))}",
        f"- ESPP income contribution: {_format_percent(_safe_float(employer_ctx.get('primary_employer_income_concentration_pct', 0.0)))}",
        "- Employer exposure notes: Treat employer-stock concentration separately and avoid automatic sell recommendations.",
        "",
        "## Employer Stock / ESPP Context",
        f"- Employer stock ticker: {str(employer_ctx.get('primary_employer_ticker', 'N/A'))}",
        f"- Employer capital concentration: {_format_percent(_safe_float(employer_ctx.get('primary_employer_capital_concentration_pct', 0.0)))}",
        f"- Employer income concentration: {_format_percent(_safe_float(employer_ctx.get('primary_employer_income_concentration_pct', 0.0)))}",
        f"- Concentration alert triggered: {'Yes' if bool(employer_ctx.get('is_concentration_alert', False)) else 'No'}",
        "- HD (The Home Depot) is the user's employer stock and is acquired through an employer-sponsored Employee Stock Purchase Plan (ESPP).",
        "",
    ]


def build_contribution_context(export_payload: dict[str, object]) -> list[str]:
    contribution_context = export_payload.get("contribution_context", {}) if isinstance(export_payload, dict) else {}
    summary = contribution_context.get("summary", {}) if isinstance(contribution_context, dict) else {}
    holdings = contribution_context.get("holdings", []) if isinstance(contribution_context, dict) else []

    lines = [
        "## Contribution Context",
        f"- Regular biweekly contributions: {_format_currency(_safe_float(summary.get('total_regular_biweekly_contribution', 0.0)))}",
        f"- ESPP contributions: {_format_currency(_safe_float(summary.get('total_espp_biweekly_contributions', 0.0)))}",
        f"- Total annual contributions: {_format_currency(_safe_float(summary.get('estimated_combined_annual_contributions', summary.get('estimated_combined_annual_contribution', 0.0))))}",
        f"- Total Regular Biweekly Contributions: {_format_currency(_safe_float(summary.get('total_regular_biweekly_contribution', 0.0)))}",
        f"- Estimated Annual HD ESPP Contributions: {_format_currency(_safe_float(summary.get('annual_hd_espp_contribution', 0.0)))}",
        f"- HD ESPP Purchase Frequency: {str(summary.get('hd_espp_purchase_frequency', 'n/a'))}",
        f"- HD ESPP Discount: {_normalize_percentage_value(summary.get('hd_espp_discount_pct', 0.0), 0.0):.1f}%",
        "- Per-holding contribution breakdown:",
    ]

    if isinstance(holdings, list) and holdings:
        for item in holdings:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {str(item.get('ticker', 'N/A'))}: regular {_format_currency(_safe_float(item.get('regular_biweekly_contribution', 0.0)))}, "
                + f"espp {_format_currency(_safe_float(item.get('espp_biweekly_contribution', 0.0)))}, "
                + f"special {_format_currency(_safe_float(item.get('special_contribution', 0.0)))}, "
                + f"total {_format_currency(_safe_float(item.get('total_contribution', 0.0)))}"
            )
    else:
        lines.append("(No data available)")

    lines.append("")
    return lines


def build_health_score_section(export_payload: dict[str, object]) -> list[str]:
    portfolio_health = export_payload.get("portfolio_health", {}) if isinstance(export_payload, dict) else {}
    breakdown = portfolio_health.get("health_score_breakdown", {}) if isinstance(portfolio_health, dict) else {}
    context_summary = portfolio_health.get("context_summary", {}) if isinstance(portfolio_health, dict) else {}
    primary_alert = str(context_summary.get("primary_alert", "")).strip() if isinstance(context_summary, dict) else ""
    alerts = [
        alert for alert in _dedupe_text_items(portfolio_health.get("alerts", []) if isinstance(portfolio_health, dict) else [])
        if alert != primary_alert
    ]
    intelligence_summary = export_payload.get("intelligence_summary", {}) if isinstance(export_payload, dict) else {}

    lines = [
        "## Health Score",
        f"- diversification: {_safe_float(breakdown.get('diversification', 0.0)):.1f}",
        f"- concentration: {_safe_float(breakdown.get('concentration', 0.0)):.1f}",
        f"- income stability: {_safe_float(breakdown.get('income_stability', 0.0)):.1f}",
        f"- dividend growth potential: {_safe_float(breakdown.get('dividend_growth_potential', 0.0)):.1f}",
        f"- total return potential: {_safe_float(breakdown.get('total_return_potential', 0.0)):.1f}",
        f"- risk: {_safe_float(breakdown.get('risk', 0.0)):.1f}",
        f"- data quality: {_safe_float(breakdown.get('data_quality', 0.0)):.1f}",
        "",
        "## Portfolio Health and Alerts",
        f"- Health score: {_resolve_health_score_value(portfolio_health):.1f}/100",
        f"- Portfolio Intelligence Income Score: {_safe_float(intelligence_summary.get('income_quality_score', 0.0)):.1f}",
        f"- Largest holding concentration: {str(portfolio_health.get('largest_holding', 'N/A'))} is {_safe_float(portfolio_health.get('largest_holding_weight', 0.0)):.1f}% of portfolio.",
    ]
    if alerts:
        lines.extend([f"- {str(item)}" for item in alerts])
    lines.extend([
        "",
        "## Tracker Health Score Metadata",
        f"- data_quality_explanation: {str(export_payload.get('tracker_health_score_metadata', {}).get('data_quality_explanation', ''))}",
        f"- income_stability_explanation: {str(export_payload.get('tracker_health_score_metadata', {}).get('income_stability_explanation', ''))}",
        f"- risk_explanation: {str(export_payload.get('tracker_health_score_metadata', {}).get('risk_explanation', ''))}",
        "",
    ])
    return lines


def build_data_completeness_section(export_payload: dict[str, object]) -> list[str]:
    completeness = export_payload.get("data_completeness", {}) if isinstance(export_payload, dict) else {}
    unresolved = export_payload.get("unresolved_exposure", {}) if isinstance(export_payload, dict) else {}
    fund_total = _safe_float(unresolved.get("fund_total_value", 0.0))
    unresolved_value = _safe_float(unresolved.get("unresolved_fund_exposure", 0.0))
    unresolved_pct = unresolved_value / fund_total * 100 if fund_total > 0 else 0.0
    holdings = export_payload.get("holdings", []) if isinstance(export_payload.get("holdings", []), list) else []
    supplemental_history = export_payload.get("supplemental_yahoo_historical_price_data", []) if isinstance(export_payload.get("supplemental_yahoo_historical_price_data", []), list) else []
    exported_tickers = {
        str(item.get("ticker", "")).strip().upper()
        for item in holdings
        if isinstance(item, dict) and str(item.get("ticker", "")).strip()
    }
    available_history_tickers = {
        str(item.get("ticker", "")).strip().upper()
        for item in supplemental_history
        if isinstance(item, dict)
        and str(item.get("ticker", "")).strip().upper() in exported_tickers
        and _canonical_supplemental_history_record(item)
    }
    ticker_history_coverage = len(available_history_tickers) / len(holdings) * 100.0 if holdings else 100.0
    validation = export_payload.get("snapshot_validation", {}) if isinstance(export_payload.get("snapshot_validation", {}), dict) else {}

    return [
        "## Data Completeness",
        f"- direct sector coverage: {_format_percent(_safe_float(completeness.get('direct_sector_classification_coverage_pct', 0.0)))}",
        f"- fund look-through coverage: {_format_percent(_safe_float(completeness.get('fund_lookthrough_coverage_pct', 0.0)))}",
        f"- unresolved exposure percentage: {_format_percent(unresolved_pct)}",
        f"- overall resolved exposure: {_format_percent(_safe_float(completeness.get('overall_resolved_exposure_coverage_pct', 0.0)))}",
        "",
        "## Data Quality Note",
        "- Dividend yield data is sourced dynamically from Yahoo Finance.",
        "- Annualized income is estimated from the current Yahoo Finance dividend yield and current market price.",
        "- Dividend yields may change over time and should not be interpreted as guaranteed future income.",
        "- Dividend payment schedules and future distribution amounts are not guaranteed.",
        *([f"- {str(issue)}" for issue in export_payload.get("data_quality_issues", [])] if export_payload.get("data_quality_issues", []) else ["- No obvious data-quality issues were detected."]),
        "",
        "## Export Integrity",
        f"- Snapshot validation: {'valid' if validation.get('valid', True) else 'invalid'}",
        f"- Supplemental ticker-history coverage: {ticker_history_coverage:.1f}% of holdings with usable Yahoo price history",
        "",
    ]


def build_asset_allocation_section(export_payload: dict[str, object]) -> list[str]:
    return [
        "## Asset Allocation",
        "",
        "## Asset Allocation Table",
        "```text",
        _records_table_text(
            export_payload.get("asset_allocation", []),
            ["asset_type", "market_value", "annual_income", "portfolio_weight_pct"],
        ),
        "```",
        "",
    ]


def build_sector_exposure_section(export_payload: dict[str, object]) -> list[str]:
    return [
        "## Exposure Interpretation",
        "- Direct sector exposure represents individually held securities with a resolved sector classification.",
        "- Look-through sector exposure represents estimated underlying sector exposure from funds using available Yahoo Finance fund-sector weights.",
        "- Unresolved fund look-through exposure represents fund assets whose underlying sector weights could not be resolved.",
        "",
        "## Direct Sector Exposure",
        "",
        "## Direct Sector Exposure Table",
        "```text",
        _records_table_text(
            export_payload.get("direct_sector_exposure", []),
            ["sector", "market_value", "annual_income", "portfolio_weight_pct", "pct_of_resolved_direct_sector_exposure"],
        ),
        "```",
        "",
        "## Direct Holdings by Sector",
        "",
        "## Direct Holdings by Sector Table",
        "```text",
        _records_table_text(
            export_payload.get("direct_holdings_by_classification", []),
            ["classification", "holdings", "market_value", "annual_income", "portfolio_weight"],
        ),
        "```",
        "",
        "## Look-Through Sector Exposure",
        "",
        "## Look-Through Sector Exposure Table",
        "```text",
        _records_table_text(
            export_payload.get("look_through_sector_exposure", []),
            ["sector", "source_holdings", "market_value", "portfolio_weight", "source"],
        ),
        "```",
        "",
        "## Unresolved Fund Look-Through Exposure",
        "",
        "## Unresolved Fund Exposure Table",
        "```text",
        _records_table_text(
            export_payload.get("unresolved_fund_look_through_exposure", []),
            ["fund_type", "holdings", "market_value", "portfolio_weight"],
        ),
        "```",
        "",
    ]


def build_income_allocation_section(export_payload: dict[str, object]) -> list[str]:
    return [
        "## Income Allocation by Holding",
        "",
        "## Income Allocation by Holding Table",
        "```text",
        _records_table_text(
            export_payload.get("income_allocation_by_holding", []),
            ["ticker", "annual_income", "market_value", "income_allocation_pct"],
        ),
        "```",
        "",
        "## Income Allocation by Asset Type",
        "",
        "## Income Allocation by Asset Type Table",
        "```text",
        _records_table_text(
            export_payload.get("income_allocation_by_asset_type", []),
            ["asset_type", "annual_income", "market_value", "income_allocation_pct"],
        ),
        "```",
        "",
        "## Income Quality Classification",
        "```text",
        _records_table_text(
            export_payload.get("income_quality_classification", []),
            ["ticker", "asset_type", "dividend_yield_pct", "annualized_income", "market_value", "income_quality_classification"],
        ),
        "```",
        "",
    ]


def build_income_quality_analysis_section(export_payload: dict[str, object]) -> list[str]:
    income_quality = export_payload.get("income_quality", {}) if isinstance(export_payload, dict) else {}
    classifications = income_quality.get("holding_classifications", {}) if isinstance(income_quality, dict) else {}
    classification_rows: list[dict[str, object]] = []
    if isinstance(classifications, dict):
        for ticker, detail in classifications.items():
            if not isinstance(detail, dict):
                continue
            classification_rows.append(
                {
                    "ticker": str(ticker),
                    "income_type": str(detail.get("income_type", "Unknown")),
                    "stability_score": _safe_float(detail.get("stability_score", 0.0)),
                    "yield_sustainability_score": _safe_float(detail.get("yield_sustainability_score", 0.0)),
                    "payout_risk_score": _safe_float(detail.get("payout_risk_score", 0.0)),
                    "notes": "; ".join(str(note) for note in detail.get("notes", []) if note),
                }
            )

    alerts = income_quality.get("alerts", []) if isinstance(income_quality, dict) else []
    notes = income_quality.get("notes", []) if isinstance(income_quality, dict) else []

    return [
        "## Income Quality Analysis",
        f"- Income Quality Engine Score: {_safe_float(income_quality.get('income_quality_score', 0.0)):.1f}",
        f"- Distribution Stability Score: {_safe_float(income_quality.get('distribution_stability_score', 0.0)):.1f}",
        f"- Yield Sustainability Score: {_safe_float(income_quality.get('yield_sustainability_score', 0.0)):.1f}",
        f"- Payout Risk Score: {_safe_float(income_quality.get('payout_risk_score', 0.0)):.1f}",
        f"- Monthly Dividend Weight Variance: {_safe_float(income_quality.get('monthly_weight_variance', 0.0)):.4f}",
        "",
        "### Income Type Classification Table",
        "```text",
        _records_table_text(
            classification_rows,
            ["ticker", "income_type", "stability_score", "yield_sustainability_score", "payout_risk_score", "notes"],
        ),
        "```",
        "",
        "### Income Quality Alerts",
        *([f"- {str(item)}" for item in alerts] if isinstance(alerts, list) and alerts else ["(No data available)"]),
        "",
        "### Income Quality Notes",
        *([f"- {str(item)}" for item in notes] if isinstance(notes, list) and notes else ["(No data available)"]),
        "",
    ]


def build_economic_exposure_section(export_payload: dict[str, object]) -> list[str]:
    return [
        "## Economic Exposure Allocation",
        "",
        "## Economic Exposure Table",
        "```text",
        _records_table_text(
            export_payload.get("economic_exposure_allocation", []),
            ["economic_exposure_category", "market_value", "annual_income", "portfolio_weight"],
        ),
        "```",
        "",
    ]


def build_historical_context_section(export_payload: dict[str, object]) -> list[str]:
    supplemental_history = export_payload.get("supplemental_yahoo_historical_price_data", []) if isinstance(export_payload, dict) else []

    lines = [
        "## Supplemental Historical Price Data",
        "",
        "## Supplemental Yahoo Finance Historical Price Data",
        "- This section contains compact supplemental historical market-price context from Yahoo Finance for the holdings in this portfolio.",
        "- Your existing portfolio history remains the authoritative source of truth for portfolio-level historical data; the Yahoo data is supplemental market-price context only.",
    ]
    if isinstance(supplemental_history, list) and supplemental_history:
        lines.extend([f"- {_summarize_yahoo_historical_price_context(item)}" for item in supplemental_history if isinstance(item, dict)])
    else:
        lines.append("(No data available)")

    lines.append("")
    return lines


def build_holdings_tables(export_payload: dict[str, object]) -> list[str]:
    top_holdings = export_payload.get("top_holdings", []) if isinstance(export_payload, dict) else []
    top_income = export_payload.get("top_income_contributors", []) if isinstance(export_payload, dict) else []
    holdings = export_payload.get("holdings", []) if isinstance(export_payload, dict) else []

    holdings_df = pd.DataFrame(holdings) if isinstance(holdings, list) else pd.DataFrame()
    if holdings_df.empty:
        holdings_csv = "(No data available)"
    else:
        preferred = [
            "ticker",
            "shares",
            "estimated_annual_distribution_per_share",
            "annualized_income",
            "regular_biweekly_contribution",
            "espp_biweekly_contribution",
            "employer_stock",
            "espp_enabled",
            "espp_purchase_frequency",
            "espp_discount_pct",
        ]
        cols = [col for col in preferred if col in holdings_df.columns]
        if cols:
            holdings_df = holdings_df[cols]
        holdings_csv = holdings_df.to_csv(index=False)

    return [
        "## Top 5 Holdings",
        "```text",
        _records_table_text(top_holdings),
        "```",
        "",
        "## Top 5 Income Contributors",
        "```text",
        _records_table_text(top_income),
        "```",
        "",
        "## Holdings Data Table",
        "```text",
        _records_table_text(holdings),
        "```",
        "",
        "## Holdings Data (CSV)",
        "```csv",
        holdings_csv,
        "```",
        "",
    ]


def build_calculated_metrics_table(export_payload: dict[str, object]) -> list[str]:
    metrics = export_payload.get("calculated_metrics", []) if isinstance(export_payload, dict) else []
    metrics_df = pd.DataFrame(metrics) if isinstance(metrics, list) else pd.DataFrame()
    metrics_csv = metrics_df.to_csv(index=False) if not metrics_df.empty else "(No data available)"

    return [
        "## Calculated Metrics Table",
        "```text",
        _records_table_text(metrics),
        "```",
        "",
        "## Calculated Metrics (CSV)",
        "```csv",
        metrics_csv,
        "```",
        "",
    ]


def build_metadata_section(export_payload: dict[str, object]) -> list[str]:
    dividend_metadata = export_payload.get("dividend_data_metadata", {}) if isinstance(export_payload, dict) else {}
    supplemental_financial = export_payload.get("supplemental_yahoo_financial_data", []) if isinstance(export_payload, dict) else []

    fundamentals_lines: list[str] = []
    earnings_lines: list[str] = []
    fund_lines: list[str] = []
    if isinstance(supplemental_financial, list):
        for item in supplemental_financial:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker:
                continue

            fundamentals = item.get("fundamental_metrics", {}) if isinstance(item.get("fundamental_metrics", {}), dict) else {}
            if fundamentals:
                detail_parts: list[str] = []
                if fundamentals.get("pe_ratio") not in [None, ""]:
                    detail_parts.append(f"PE {fundamentals.get('pe_ratio')}")
                if fundamentals.get("price_to_book") not in [None, ""]:
                    detail_parts.append(f"PB {fundamentals.get('price_to_book')}")
                fundamentals_lines.append(f"- {ticker}: " + (", ".join(detail_parts) if detail_parts else "No data available"))

            earnings = item.get("earnings_and_estimates", {}) if isinstance(item.get("earnings_and_estimates", {}), dict) else {}
            if earnings:
                detail_parts = []
                if earnings.get("avg_eps_estimate") not in [None, ""]:
                    detail_parts.append(f"EPS Est {earnings.get('avg_eps_estimate')}")
                if earnings.get("earnings_growth_pct") not in [None, ""]:
                    detail_parts.append(f"Earnings Growth {earnings.get('earnings_growth_pct')}")
                earnings_lines.append(f"- {ticker}: " + (", ".join(detail_parts) if detail_parts else "No data available"))

            fund_data = item.get("fund_data", {}) if isinstance(item.get("fund_data", {}), dict) else {}
            if fund_data:
                description = str(fund_data.get("description", "No data available"))
                fund_lines.append(f"- {ticker}: {description}")
                sector_weightings = fund_data.get("sector_weightings", {}) if isinstance(fund_data.get("sector_weightings", {}), dict) else {}
                if sector_weightings:
                    fund_lines.append("- Sector Weights:")
                    for sector_name, weight in sector_weightings.items():
                        fund_lines.append(f"  - {str(sector_name)}: {_safe_float(weight) * 100:.2f}%")

    return [
        "## Dividend Data Metadata",
        f"- source: {str(dividend_metadata.get('source', 'Yahoo Finance'))}",
        f"- data_type: {str(dividend_metadata.get('data_type', 'Current yield'))}",
        f"- methodology: {str(dividend_metadata.get('method', 'Current dividend yield used to estimate annualized income'))}",
        f"- verification_status: {str(dividend_metadata.get('verification_status', 'Not verified'))}",
        "",
        "## Supplemental Yahoo Finance Fundamental Data",
        *(fundamentals_lines if fundamentals_lines else ["(No data available)"]),
        "",
        "## Supplemental Yahoo Finance Earnings and Estimates",
        *(earnings_lines if earnings_lines else ["(No data available)"]),
        "",
        "## Supplemental Yahoo Finance Fund Data",
        *(fund_lines if fund_lines else ["(No data available)"]),
        "",
    ]


def assemble_export(section_blocks: list[list[str]], export_payload: dict[str, object]) -> str:
    export_payload = _sanitize_export_value(export_payload)
    lines: list[str] = []
    for block in section_blocks:
        lines.extend(block)

    lines.extend(
        [
            "## Investor Profile",
            f"- Portfolio strategy: {str(export_payload.get('investor_profile', {}).get('portfolio_strategy', 'N/A'))}",
            f"- Primary goal: {str(export_payload.get('investor_profile', {}).get('primary_goal', 'N/A'))}",
            f"- Risk tolerance: {str(export_payload.get('investor_profile', {}).get('risk_tolerance', 'N/A'))}",
            f"- Investment horizon: {str(export_payload.get('investor_profile', {}).get('investment_horizon_years', 'N/A'))} years",
            "",
            "## AI Analysis Instructions",
            "AI Analysis Instructions:",
            *_AI_ANALYSIS_INSTRUCTIONS_BLOCK,
            "",
            "## Notes",
            "- Income values in this export are annualized estimates based on the current dividend yield.",
            "- The export does not claim a verified payment schedule or known calendar of dividends.",
        ]
    )

    return "\n".join(lines)


def generate_export(export_payload: dict[str, object]) -> str:
    export_payload = _sanitize_export_value(export_payload)
    section_blocks = [
        build_context_summary(export_payload),
        build_concentration_context(export_payload),
        build_espp_context(export_payload),
        build_contribution_context(export_payload),
        build_health_score_section(export_payload),
        build_data_completeness_section(export_payload),
        [
            "## Data Definitions",
            "- Total Portfolio Value: Current market value of all holdings.",
            "- Contribution Context: Includes regular biweekly contributions and special contributions per holding so the AI can evaluate whether reallocation or contribution changes are warranted.",
            "",
        ],
        [
            "## Contribution Context Table",
            "```text",
            _records_table_text(
                export_payload.get("contribution_context", {}).get("holdings", []) if isinstance(export_payload.get("contribution_context", {}), dict) else [],
                ["ticker", "regular_biweekly_contribution", "espp_biweekly_contribution", "special_contribution", "total_contribution", "contribution_type", "contribution_frequency", "purchase_frequency"],
            ),
            "```",
            "",
        ],
        build_asset_allocation_section(export_payload),
        build_sector_exposure_section(export_payload),
        build_income_allocation_section(export_payload),
        build_economic_exposure_section(export_payload),
        build_metadata_section(export_payload),
        build_historical_context_section(export_payload),
        build_income_quality_analysis_section(export_payload),
        build_holdings_tables(export_payload),
        build_calculated_metrics_table(export_payload),
    ]
    return assemble_export(section_blocks, export_payload)


def _build_export_markdown(
    holdings_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    biweekly_contribution: float = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
    projection_return_pct: float = 8.0,
    espp_contrib_biweekly: float = ESPP_CONTRIB_BIWEEKLY,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> str:
    payload = _build_ai_export_payload(
        holdings_df,
        metrics_df,
        biweekly_contribution,
        projection_return_pct,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    payload = _canonicalize_export_historical_context(payload)
    return generate_export(payload)


def _build_shared_production_export_package(
    holdings_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    *,
    history: list[dict[str, object]] | None = None,
    fund_weights: dict[str, dict[str, float]] | None = None,
    external_context: dict[str, object] | None = None,
    generated_at: str | None = None,
    projection_biweekly: float = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
    projection_return_pct: float = 8.0,
    espp_biweekly: float = ESPP_CONTRIB_BIWEEKLY,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> dict[str, str]:
    """Canonical pure export used by the Streamlit and NiceGUI download paths.

    Streamlit-specific accuracy controls determine how metrics and enrichment are
    resolved before this call. The shared builder never performs market lookup or
    database writes.
    """
    records = holdings_df.to_dict(orient="records") if isinstance(holdings_df, pd.DataFrame) else []
    if fund_weights is None or external_context is None:
        resolved_weights, resolved_context = _resolve_shared_export_enrichment(metrics_df, biweekly_contribution=projection_biweekly)
        if fund_weights is None:
            fund_weights = resolved_weights
        if external_context is None:
            external_context = resolved_context
    return build_shared_export_package(
        records,
        metrics_df,
        history=history,
        fund_weights=fund_weights,
        external_context=external_context,
        generated_at=generated_at,
        use_export_json_data=_use_export_json_data(),
        strict_export_accuracy=_strict_export_accuracy_mode(),
        current_live_export=not _strict_export_accuracy_mode(),
        projection_biweekly=projection_biweekly,
        projection_return_pct=projection_return_pct,
        espp_biweekly=espp_biweekly,
        espp_purchase_frequency=espp_purchase_frequency,
        espp_discount=espp_discount,
    )


def _resolve_shared_export_enrichment(
    metrics_df: pd.DataFrame,
    *,
    biweekly_contribution: float = DEFAULT_PROJECTION_BIWEEKLY_CONTRIB,
) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    """Reuse production-cached enrichment before calling the pure export builder."""
    tickers = _to_ticker_tuple(metrics_df.get("ticker", pd.Series(dtype=str))) if not metrics_df.empty else ()
    fund_tickers = tuple(
        str(row.get("ticker", "")).strip().upper()
        for row in metrics_df.to_dict(orient="records")
        if _canonical_asset_type(str(row.get("asset_type", ""))) in {"ETF", "Bond Fund", "CEF"} and str(row.get("ticker", "")).strip()
    )
    fund_weights = _latest_fund_sector_weights(fund_tickers) if fund_tickers else {}

    intelligence_rows: list[dict[str, object]] = []
    intelligence_map = _latest_investment_intelligence(tickers) if tickers else {}
    for ticker in tickers:
        entry = intelligence_map.get(ticker, {})
        if entry:
            intelligence_rows.append({
                "ticker": ticker,
                "fundamental_metrics": entry.get("fundamental_metrics", {}),
                "earnings_and_estimates": entry.get("earnings_and_estimates", {}),
                "analyst_targets": entry.get("analyst_targets", {}),
                "fund_data": entry.get("fund_data", {}),
                "data_source": entry.get("data_source", "Yahoo Finance"),
            })

    history_map = _resolved_historical_context(tickers) if tickers else {}
    historical_rows = [dict(history_map[ticker]) for ticker in tickers if history_map.get(ticker)]
    investor_profile = {
        "portfolio_strategy": "Dividend growth and income",
        "primary_goal": "Build long-term wealth while increasing dividend income",
        "risk_tolerance": "Moderate",
        "investment_horizon_years": 20,
        "contribution_frequency": "Biweekly",
        "contribution_amount": biweekly_contribution,
    }
    recommendation_constraints = {
        "avoid_forced_selling": True,
        "prefer_new_contributions_for_rebalancing": True,
        "prioritize_dividend_growth": True,
        "prioritize_total_return": True,
        "consider_income_stability": True,
        "do_not_recommend_based_on_yield_alone": True,
    }
    external_context: dict[str, object] = {
        "supplemental_investment_intelligence": intelligence_rows,
        "historical_security_context": historical_rows,
        "dividend_seasonality": {},
        "yahoo_metadata": {
            "source": "Yahoo Finance",
            "data_type": "current_yield",
            "method": "Current dividend yield used to estimate annualized income",
            "verified": False,
        },
        "investor_profile": investor_profile,
        "recommendation_constraints": recommendation_constraints,
        "notes": [],
    }
    if not fund_weights and not intelligence_rows and not historical_rows:
        external_context["notes"] = ["External Yahoo enrichment was not supplied for this export."]
    return fund_weights, external_context


def _prepare_shared_exports_on_demand(
    requested: bool,
    holdings_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    *,
    projection_biweekly: float,
    projection_return_pct: float,
    espp_biweekly: float,
    espp_purchase_frequency: str,
    espp_discount: float,
) -> dict[str, str] | None:
    """Build comprehensive downloads only after the explicit prepare action."""
    if not requested:
        return None
    return _build_shared_production_export_package(
        holdings_df,
        metrics_df,
        history=load_performance_history(STATEMENT_HISTORY_DB_PATH),
        projection_biweekly=projection_biweekly,
        projection_return_pct=projection_return_pct,
        espp_biweekly=espp_biweekly,
        espp_purchase_frequency=espp_purchase_frequency,
        espp_discount=espp_discount,
    )


def _normalize_espp_acquired_shares(holdings: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    cleaned = holdings.copy()
    cleaned["espp_acquired_shares"] = pd.to_numeric(cleaned["espp_acquired_shares"], errors="coerce").fillna(0.0)
    cleaned["shares"] = pd.to_numeric(cleaned["shares"], errors="coerce").fillna(0.0)
    invalid_negative = cleaned["espp_acquired_shares"] < 0
    exceeds_total = cleaned["espp_acquired_shares"] > cleaned["shares"]
    if invalid_negative.any():
        cleaned.loc[invalid_negative, "espp_acquired_shares"] = 0.0
    if exceeds_total.any():
        cleaned.loc[exceeds_total, "espp_acquired_shares"] = cleaned.loc[exceeds_total, "shares"]
    return cleaned, bool(invalid_negative.any() or exceeds_total.any())


def _render_holdings_tab(current_holdings: pd.DataFrame, mobile_mode: bool = False) -> pd.DataFrame:
    st.subheader("Manage Holdings")
    st.caption("Add, edit, or remove rows, then click Save when finished.")

    normalized = _normalize_columns(current_holdings, drop_blank_tickers=False)
    # Keep the editor stable with only user-editable fields to avoid lost first keystroke on reruns.
    editor_df = normalized[
        [
            "ticker",
            "shares",
            "espp_acquired_shares",
            "amount_invested",
            "annual_div_per_share",
            "regular_biweekly_contribution",
            "espp_contribution_biweekly",
            "special_contribution",
            "is_espp",
            "espp_discount",
            "espp_purchase_frequency_months",
            "sector",
            "asset_type",
        ]
    ].copy()
    editor_df["sector"] = editor_df["sector"].fillna("").astype(str).str.strip()
    editor_df["asset_type"] = editor_df["asset_type"].fillna("Stock").astype(str).str.strip()
    with st.form("holdings_form", clear_on_submit=False):
        edited = st.data_editor(
            editor_df,
            num_rows="dynamic",
            width="stretch",
            column_config={
                "ticker": st.column_config.TextColumn("Ticker", help="Stock symbol, e.g. KO"),
                "shares": st.column_config.NumberColumn("Shares", min_value=0.0, step=0.000001, format="%.6f"),
                "espp_acquired_shares": st.column_config.NumberColumn(
                    "ESPP-Acquired Shares",
                    min_value=0.0,
                    step=0.000001,
                    format="%.6f",
                    help="Subset of total shares acquired through ESPP.",
                ),
                "amount_invested": st.column_config.NumberColumn(
                    "Amount Invested", min_value=0.0, step=0.01, format="$%.2f"
                ),
                "annual_div_per_share": st.column_config.NumberColumn(
                    "Annual Div/Share (Manual Override)", min_value=0.0, step=0.01, format="$%.2f"
                ),
                "regular_biweekly_contribution": st.column_config.NumberColumn(
                    "Regular Bi-Weekly Contribution",
                    min_value=0.0,
                    step=0.01,
                    format="$%.2f",
                    help="Recurring contribution for this holding each pay period.",
                ),
                "special_contribution": st.column_config.NumberColumn(
                    "Special Contribution",
                    min_value=0.0,
                    step=0.01,
                    format="$%.2f",
                    help="Separate additional contribution. Do not duplicate an ESPP contribution.",
                ),
                "espp_contribution_biweekly": st.column_config.NumberColumn(
                    "ESPP Bi-Weekly Contribution",
                    min_value=0.0,
                    step=0.01,
                    format="$%.2f",
                    help="Employer stock purchase plan contribution per paycheck.",
                ),
                "is_espp": st.column_config.CheckboxColumn("ESPP Holding", help="Marks this row as an ESPP position."),
                "espp_discount": st.column_config.NumberColumn("ESPP Discount (decimal)", min_value=0.0, max_value=1.0, step=0.01, format="%.2f"),
                "espp_purchase_frequency_months": st.column_config.NumberColumn(
                    "ESPP Purchase Frequency (months)",
                    min_value=1.0,
                    step=1.0,
                    format="%.0f",
                    help="Purchase cadence in months (for example: 3, 6, 12).",
                ),
                "sector": st.column_config.TextColumn("Sector"),
                "asset_type": st.column_config.SelectboxColumn(
                    "Asset Type",
                    options=["Stock", "ETF", "REIT", "CEF", "BDC", "Bond Fund", "Other"],
                    default="Stock",
                ),
            },
            hide_index=True,
            key="holdings_editor",
        )

        b1, b2, b3 = st.columns(3)
        save_clicked = b1.form_submit_button("Save Holdings", type="primary", key="holdings_form_save")
        save_recalc_clicked = b2.form_submit_button(
            "Save Holdings",
            help="Saves the current holdings while preserving historical amount invested/cost basis.",
            key="holdings_form_save_recalc",
        )
        save_refresh_clicked = b3.form_submit_button(
            "Save + Refresh Sector/Type",
            help="Backfills sector and refreshes classification for each ticker using latest Yahoo metadata.",
            key="holdings_form_save_refresh",
        )

    persist_df = _normalize_columns(current_holdings, drop_blank_tickers=True)
    price_map: dict[str, float] = {}
    yield_map: dict[str, float] = {}
    submitted = save_clicked or save_recalc_clicked or save_refresh_clicked

    if submitted:
        cleaned = _normalize_columns(edited, drop_blank_tickers=False)
        cleaned["asset_type"] = cleaned["asset_type"].apply(_canonical_asset_type)

        cleaned, invalid_espp_share_values = _normalize_espp_acquired_shares(cleaned)
        if invalid_espp_share_values:
            st.error("ESPP-acquired shares cannot be negative or exceed total shares. Values were adjusted to stay within the valid range.")

        tickers = _to_ticker_tuple(cleaned["ticker"])
        price_map = _resolved_price_map(tickers)
        sector_map = _resolved_sector_map(tickers)
        yield_map = _resolved_dividend_yield_map(tickers)
        asset_type_hints = _resolved_asset_type_map(tickers)

        # Preserve historical cost basis and only use market price for preview values.
        missing_cost_basis = cleaned["shares"].gt(0) & cleaned["amount_invested"].le(0)
        cleaned.loc[missing_cost_basis, "amount_invested"] = cleaned.loc[missing_cost_basis, "amount_invested"].fillna(0.0)

        if save_recalc_clicked:
            st.info("The standard save path preserves historical cost basis. This action now only saves the edited holdings without changing Amount Invested.")

        cleaned["sector"] = cleaned["sector"].fillna("").astype(str).str.strip()
        blank_sector = cleaned["sector"].eq("")
        if not _strict_export_accuracy_mode():
            cleaned.loc[blank_sector, "sector"] = cleaned.loc[blank_sector, "ticker"].map(sector_map).fillna("")

        # Auto-classify likely REIT/ETF/Stock/CEF/BDC/Bond Fund when row is still at default Stock.
        default_asset_rows = cleaned["asset_type"].eq("Stock")
        if not _strict_export_accuracy_mode():
            cleaned.loc[default_asset_rows, "asset_type"] = (
                cleaned.loc[default_asset_rows, "ticker"].map(asset_type_hints).fillna("Stock")
            )
        cleaned["asset_type"] = cleaned["asset_type"].apply(_canonical_asset_type)

        if save_refresh_clicked:
            market_sector = cleaned["ticker"].map(sector_map).fillna("").astype(str).str.strip()
            market_asset_type = cleaned["ticker"].map(asset_type_hints).fillna("Stock")
            has_market_sector = market_sector.ne("")
            cleaned.loc[has_market_sector, "sector"] = market_sector[has_market_sector]
            cleaned["asset_type"] = market_asset_type.apply(_canonical_asset_type)
            st.info("Sector and Asset Type refreshed from latest market data.")

        persist_df = cleaned[cleaned["ticker"].ne("")].reset_index(drop=True)
        _save_holdings(persist_df)
        st.success("Changes saved. Historical amount invested/cost basis was preserved.")

    if not price_map or not yield_map:
        preview_tickers = _to_ticker_tuple(persist_df["ticker"]) if not persist_df.empty else tuple()
        price_map = _resolved_price_map(preview_tickers)
        yield_map = _resolved_dividend_yield_map(preview_tickers)

    preview = persist_df.copy()
    preview["current_price"] = preview["ticker"].map(price_map).fillna(0.0)
    preview["current_value"] = preview["shares"] * preview["current_price"]
    preview["avg_cost"] = (preview["amount_invested"] / preview["shares"].replace(0, pd.NA)).fillna(0.0)
    preview["dividend_yield_pct"] = preview["ticker"].map(yield_map).fillna(0.0)
    preview["est_annual_div_per_share"] = preview["current_price"] * preview["dividend_yield_pct"] / 100
    preview["effective_annual_div_per_share"] = preview["annual_div_per_share"]
    blank_manual = preview["effective_annual_div_per_share"] <= 0
    preview.loc[blank_manual, "effective_annual_div_per_share"] = preview.loc[blank_manual, "est_annual_div_per_share"]
    st.markdown("#### Live Calculated Fields")
    full_preview_view = preview[
            [
                "ticker",
                "shares",
                "amount_invested",
                "avg_cost",
                "current_price",
                "current_value",
                "espp_acquired_shares",
                "dividend_yield_pct",
                "est_annual_div_per_share",
                "annual_div_per_share",
                "effective_annual_div_per_share",
                "regular_biweekly_contribution",
                "espp_biweekly_contribution",
                "espp_contribution_biweekly",
                "special_contribution",
                "is_espp",
                "espp_discount",
                "espp_purchase_frequency_months",
                "sector",
                "asset_type",
            ]
        ]
    compact_preview_view = preview[["ticker", "current_price", "current_value", "dividend_yield_pct", "asset_type"]]

    preview_style_map = {
        "shares": "{:.6f}",
        "amount_invested": "${:,.2f}",
        "avg_cost": "${:,.2f}",
        "current_price": "${:,.2f}",
        "current_value": "${:,.2f}",
        "dividend_yield_pct": "{:.2f}%",
        "est_annual_div_per_share": "${:,.2f}",
        "annual_div_per_share": "${:,.2f}",
        "effective_annual_div_per_share": "${:,.2f}",
    }

    if mobile_mode:
        st.dataframe(
            compact_preview_view.style.format(
                {
                    "current_price": "${:,.2f}",
                    "current_value": "${:,.2f}",
                    "dividend_yield_pct": "{:.2f}%",
                }
            ),
            width="stretch",
            hide_index=True,
        )
        with st.expander("Show full calculated fields", expanded=False):
            st.dataframe(
                full_preview_view.style.format(preview_style_map),
                width="stretch",
                hide_index=True,
            )
    else:
        st.dataframe(
            full_preview_view.style.format(preview_style_map),
            width="stretch",
            hide_index=True,
        )
    st.caption(_manual_input_data_note())

    export_metrics = _compute_metrics(persist_df, price_map, yield_map)
    projection_biweekly = float(st.session_state.get("analysis_biweekly_contribution", DEFAULT_PROJECTION_BIWEEKLY_CONTRIB))
    projection_return_pct = float(st.session_state.get("analysis_projection_return", 8.0))
    espp_biweekly = float(st.session_state.get("analysis_espp_contrib_biweekly", ESPP_CONTRIB_BIWEEKLY))
    espp_purchase_frequency = str(st.session_state.get("analysis_espp_purchase_frequency", ESPP_PURCHASE_FREQUENCY))
    espp_discount = float(st.session_state.get("analysis_espp_discount", ESPP_DISCOUNT))
    export_signature = (
        export_metrics.to_json(orient="split", date_format="iso"), projection_biweekly,
        projection_return_pct, espp_biweekly, espp_purchase_frequency, espp_discount,
        _use_export_json_data(), _strict_export_accuracy_mode(),
    )
    if st.session_state.get("shared_export_signature") != export_signature:
        st.session_state.pop("shared_export_package", None)
    prepare_exports = st.button("Prepare comprehensive exports", key="prepare_shared_exports")
    prepared = _prepare_shared_exports_on_demand(
        prepare_exports, persist_df, export_metrics,
        projection_biweekly=projection_biweekly,
        projection_return_pct=projection_return_pct,
        espp_biweekly=espp_biweekly,
        espp_purchase_frequency=espp_purchase_frequency,
        espp_discount=espp_discount,
    )
    if prepared is not None:
        st.session_state["shared_export_package"] = prepared
        st.session_state["shared_export_signature"] = export_signature
    shared_exports = st.session_state.get("shared_export_package")
    left, middle, right = st.columns([1, 1, 1])
    with left:
        st.download_button(
            label="Download holdings CSV",
            data=persist_df.to_csv(index=False).encode("utf-8"),
            file_name="holdings.csv",
            mime="text/csv",
        )
    with middle:
        if isinstance(shared_exports, dict):
            st.download_button(label="Export AI-friendly summary", data=shared_exports["portfolio_export.md"].encode("utf-8"), file_name="portfolio_export.md", mime="text/markdown")
            st.download_button(label="Export AI JSON payload", data=shared_exports["portfolio_export.json"].encode("utf-8"), file_name="portfolio_export.json", mime="application/json")
        else:
            st.caption("Prepare comprehensive exports to resolve cached export enrichment and enable downloads.")
    with right:
        if st.button("Clear all holdings", type="secondary"):
            empty = pd.DataFrame(columns=REQUIRED_COLUMNS)
            _save_holdings(empty)
            st.session_state["holdings"] = empty
            st.session_state.pop("holdings_editor", None)
            st.rerun()

    return persist_df


def _build_investment_intelligence_rows(metrics: pd.DataFrame) -> list[dict[str, object]]:
    if metrics.empty:
        return []

    if _strict_export_accuracy_mode() and _use_export_json_data():
        payload = st.session_state.get("export_payload", {})
        supplemental = payload.get("supplemental_yahoo_financial_data", []) if isinstance(payload, dict) else []
        rows: list[dict[str, object]] = []
        for item in supplemental if isinstance(supplemental, list) else []:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            fundamentals = item.get("fundamental_metrics", {}) if isinstance(item.get("fundamental_metrics", {}), dict) else {}
            estimates = item.get("earnings_and_estimates", {}) if isinstance(item.get("earnings_and_estimates", {}), dict) else {}
            fund_data = item.get("fund_data", {}) if isinstance(item.get("fund_data", {}), dict) else {}
            if fund_data:
                rows.append(
                    {
                        "Ticker": ticker,
                        "Type": "Fund",
                        "Description": fund_data.get("description"),
                        "Market Cap": fund_data.get("market_cap"),
                        "Price": fund_data.get("last_price"),
                        "Top Holdings": fund_data.get("top_holdings"),
                        "Equity Holdings": fund_data.get("equity_holdings"),
                        "Bond Holdings": fund_data.get("bond_holdings"),
                        "Bond Ratings": fund_data.get("bond_ratings"),
                        "Sector Weights": fund_data.get("sector_weightings"),
                        "Fund Operations": fund_data.get("fund_operations"),
                    }
                )
            else:
                rows.append(
                    {
                        "Ticker": ticker,
                        "Type": "Stock",
                        "PE": fundamentals.get("pe_ratio"),
                        "Price/Book": fundamentals.get("price_to_book"),
                        "Dividend Yield": fundamentals.get("dividend_yield"),
                        "Earnings Growth": estimates.get("earnings_growth_pct"),
                        "Revenue Growth": estimates.get("revenue_growth_pct"),
                    }
                )
        return rows

    rows: list[dict[str, object]] = []
    tickers = tuple(str(ticker).strip().upper() for ticker in metrics["ticker"].dropna().astype(str).tolist() if str(ticker).strip())
    intelligence_map = _latest_investment_intelligence(tickers)
    for ticker in tickers:
        entry = intelligence_map.get(ticker)
        if not entry:
            continue
        if entry.get("type") == "Fund":
            fund_data = entry.get("fund_data", {}) or {}
            rows.append(
                {
                    "Ticker": ticker,
                    "Type": "Fund",
                    "Description": fund_data.get("description"),
                    "Market Cap": fund_data.get("market_cap"),
                    "Price": fund_data.get("last_price"),
                    "Top Holdings": fund_data.get("top_holdings"),
                    "Equity Holdings": fund_data.get("equity_holdings"),
                    "Bond Holdings": fund_data.get("bond_holdings"),
                    "Bond Ratings": fund_data.get("bond_ratings"),
                    "Sector Weights": fund_data.get("sector_weightings"),
                    "Fund Operations": fund_data.get("fund_operations"),
                }
            )
            continue

        fundamentals = entry.get("fundamental_metrics", {}) or {}
        estimates = entry.get("earnings_and_estimates", {}) or {}
        rows.append(
            {
                "Ticker": ticker,
                "Type": "Stock",
                "PE": fundamentals.get("pe_ratio"),
                "Price/Book": fundamentals.get("price_to_book"),
                "Dividend Yield": fundamentals.get("dividend_yield"),
                "Earnings Growth": estimates.get("earnings_growth_pct"),
                "Revenue Growth": estimates.get("revenue_growth_pct"),
            }
        )
    return rows


def _manual_input_data_note() -> str:
    return (
        "Some fields here are intentionally driven by manual holdings edits rather than live market data. "
        "That includes dividend-per-share inputs, sector, asset type, contribution fields, and ESPP settings. "
        "If those cells are blank in the holdings editor, the related analysis field may stay empty or only show the live-calculated portion."
    )


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_holding_yield_pct(row: dict[str, object] | pd.Series) -> float:
    if isinstance(row, pd.Series):
        row = row.to_dict()

    current_price = _coerce_float(row.get("current_price", 0.0), 0.0)
    if current_price <= 0:
        return 0.0

    dividend_yield_pct = _coerce_float(row.get("dividend_yield_pct", 0.0), 0.0)
    if dividend_yield_pct > 0:
        return dividend_yield_pct

    effective_annual_div_per_share = _coerce_float(row.get("effective_annual_div_per_share", 0.0), 0.0)
    if effective_annual_div_per_share <= 0:
        return 0.0

    return (effective_annual_div_per_share / current_price) * 100.0


def _select_highest_valid_yield(rows: list[dict[str, object]] | pd.DataFrame) -> dict[str, object]:
    if isinstance(rows, pd.DataFrame):
        rows = [row.to_dict() for _, row in rows.iterrows()]

    valid_rows = []
    for row in rows:
        yield_pct = _resolve_holding_yield_pct(row)
        if yield_pct > 0:
            valid_rows.append({"ticker": row.get("ticker", "N/A"), "yield_pct": yield_pct})

    if not valid_rows:
        return {"ticker": "N/A", "yield_pct": 0.0}

    return max(valid_rows, key=lambda item: float(item.get("yield_pct", 0.0)))


def _select_lowest_positive_yield(rows: list[dict[str, object]] | pd.DataFrame) -> dict[str, object]:
    if isinstance(rows, pd.DataFrame):
        rows = [row.to_dict() for _, row in rows.iterrows()]

    valid_rows = []
    for row in rows:
        yield_pct = _resolve_holding_yield_pct(row)
        if yield_pct > 0:
            valid_rows.append({"ticker": row.get("ticker", "N/A"), "yield_pct": yield_pct})

    if not valid_rows:
        return {"ticker": "N/A", "yield_pct": 0.0}

    return min(valid_rows, key=lambda item: float(item.get("yield_pct", 0.0)))


def _split_espp_market_value(row: dict[str, object] | pd.Series) -> dict[str, float]:
    if isinstance(row, pd.Series):
        row = row.to_dict()

    total_shares = max(_coerce_float(row.get("shares", 0.0), 0.0), 0.0)
    espp_shares = _coerce_float(row.get("espp_acquired_shares", 0.0), 0.0)
    if espp_shares < 0:
        espp_shares = 0.0
    if espp_shares > total_shares:
        espp_shares = total_shares
    non_espp_shares = max(total_shares - espp_shares, 0.0)

    tolerance = 1e-9
    if abs(total_shares) <= tolerance:
        total_shares = 0.0
    if abs(espp_shares) <= tolerance:
        espp_shares = 0.0
    if abs(non_espp_shares) <= tolerance:
        non_espp_shares = 0.0

    current_price = _coerce_float(row.get("current_price", 0.0), 0.0)
    espp_market_value = espp_shares * current_price
    non_espp_market_value = non_espp_shares * current_price
    if abs(espp_market_value) <= tolerance:
        espp_market_value = 0.0
    if abs(non_espp_market_value) <= tolerance:
        non_espp_market_value = 0.0

    return {
        "espp_shares": espp_shares,
        "non_espp_shares": non_espp_shares,
        "espp_market_value": espp_market_value,
        "non_espp_market_value": non_espp_market_value,
    }


def _summarize_contribution_totals(contribution_rows: list[dict[str, object]]) -> dict[str, float]:
    regular_biweekly = sum(_coerce_float(item.get("regular_biweekly_contribution", 0.0), 0.0) for item in contribution_rows)
    espp_biweekly = sum(_coerce_float(item.get("espp_biweekly_contribution", 0.0), 0.0) for item in contribution_rows)
    special_biweekly = sum(_coerce_float(item.get("special_contribution", 0.0), 0.0) for item in contribution_rows)
    combined_biweekly = regular_biweekly + espp_biweekly + special_biweekly
    return {
        "regular_biweekly": regular_biweekly,
        "espp_biweekly": espp_biweekly,
        "special_biweekly": special_biweekly,
        "combined_biweekly": combined_biweekly,
        "regular_annual": regular_biweekly * 26,
        "espp_annual": espp_biweekly * 26,
        "combined_annual": combined_biweekly * 26,
    }


def _build_portfolio_analysis_summary(
    metrics: pd.DataFrame,
    biweekly_contribution: float,
    projection_return_pct: float,
    espp_contrib_biweekly: float,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> dict[str, object]:
    if metrics.empty:
        return {
            "total_portfolio_value": 0.0,
            "total_cost_basis": 0.0,
            "total_income": 0.0,
            "gain": 0.0,
            "average_holding_yield_pct": 0.0,
            "weighted_portfolio_yield_pct": 0.0,
            "highest_yield_ticker": "N/A",
            "highest_yield_pct": 0.0,
            "lowest_positive_yield_ticker": "N/A",
            "lowest_positive_yield_pct": 0.0,
            "active_contributors": 0,
            "regular_annual_contribution": biweekly_contribution * 26,
            "espp_annual_contribution": espp_contrib_biweekly * 26,
            "combined_annual_contribution": (biweekly_contribution + espp_contrib_biweekly) * 26,
            "projected_10y_value": 0.0,
            "projected_20y_value": 0.0,
            "growth_10y_pct": 0.0,
            "growth_20y_pct": 0.0,
        }

    analysis_df = metrics.copy()
    for column in ["market_value", "annual_income", "current_price", "shares", "espp_acquired_shares", "regular_biweekly_contribution", "espp_biweekly_contribution", "special_contribution"]:
        if column in analysis_df.columns:
            analysis_df[column] = pd.to_numeric(analysis_df[column], errors="coerce").fillna(0.0)
        else:
            analysis_df[column] = 0.0
    analysis_df["asset_type"] = analysis_df["asset_type"].apply(_canonical_asset_type) if "asset_type" in analysis_df.columns else "Other"
    analysis_df["holding_yield_pct"] = analysis_df.apply(_resolve_holding_yield_pct, axis=1)
    analysis_df["holding_yield_pct"] = pd.to_numeric(analysis_df["holding_yield_pct"], errors="coerce").fillna(0.0)

    total_cost = float(analysis_df.get("cost_basis", analysis_df.get("amount_invested", 0.0)).sum()) if not analysis_df.empty else 0.0
    total_value = float(analysis_df["market_value"].sum()) if not analysis_df.empty else 0.0
    total_income = float(analysis_df["annual_income"].sum()) if not analysis_df.empty else 0.0
    gain = total_value - total_cost
    weighted_portfolio_yield_pct = (total_income / total_value * 100) if total_value > 0 else 0.0
    average_holding_yield_pct = float(analysis_df["holding_yield_pct"].mean()) if not analysis_df.empty else 0.0

    contribution_context = _build_contribution_context(
        analysis_df,
        analysis_df,
        biweekly_contribution,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    contribution_rows = contribution_context.get("holdings", [])
    contribution_summary = _summarize_contribution_totals(contribution_rows)
    active_contributors = int(sum(1 for row in contribution_rows if float(row.get("total_contribution", 0.0) or 0.0) > 0))

    highest_yield = _select_highest_valid_yield(analysis_df)
    lowest_positive_yield = _select_lowest_positive_yield(analysis_df)
    projected_10y_value = _future_value_with_contributions(total_value, projection_return_pct, biweekly_contribution, 10)
    projected_20y_value = _future_value_with_contributions(total_value, projection_return_pct, biweekly_contribution, 20)
    growth_10y_pct = ((projected_10y_value / total_value) - 1) * 100 if total_value > 0 else 0.0
    growth_20y_pct = ((projected_20y_value / total_value) - 1) * 100 if total_value > 0 else 0.0

    return {
        "total_portfolio_value": total_value,
        "total_cost_basis": total_cost,
        "total_income": total_income,
        "gain": gain,
        "average_holding_yield_pct": average_holding_yield_pct,
        "weighted_portfolio_yield_pct": weighted_portfolio_yield_pct,
        "highest_yield_ticker": str(highest_yield.get("ticker", "N/A")),
        "highest_yield_pct": float(highest_yield.get("yield_pct", 0.0) or 0.0),
        "lowest_positive_yield_ticker": str(lowest_positive_yield.get("ticker", "N/A")),
        "lowest_positive_yield_pct": float(lowest_positive_yield.get("yield_pct", 0.0) or 0.0),
        "active_contributors": active_contributors,
        "regular_annual_contribution": float(contribution_summary.get("regular_annual", 0.0) or 0.0),
        "espp_annual_contribution": float(contribution_summary.get("espp_annual", 0.0) or 0.0),
        "combined_annual_contribution": float(contribution_summary.get("combined_annual", 0.0) or 0.0),
        "projected_10y_value": projected_10y_value,
        "projected_20y_value": projected_20y_value,
        "growth_10y_pct": growth_10y_pct,
        "growth_20y_pct": growth_20y_pct,
    }


def _statement_preview_rows(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{"Statement start date": row.get("statement_start_date") or "-", "Statement end date": row.get("statement_end_date") or "-", "Ending portfolio value": row.get("portfolio_value"), "Invested value": row.get("invested_value"), "Cash value": row.get("cash_value"), "Validation status": row.get("status"), "Warning": row.get("warning")} for row in records]


def _filter_performance_history(
    history: list[dict[str, object]],
    selected_range: str = DEFAULT_HISTORY_RANGE,
) -> list[dict[str, object]]:
    return filter_history_range(history, selected_range)


def _persist_live_snapshot_if_ready(metrics: pd.DataFrame, total_value: float, *, db_path: Path = STATEMENT_HISTORY_DB_PATH, use_export_json_data: bool | None = None, strict_export_accuracy_mode: bool | None = None) -> bool:
    use_export_json_data = _use_export_json_data() if use_export_json_data is None else use_export_json_data
    strict_export_accuracy_mode = bool(st.session_state.get("strict_export_accuracy_mode", False)) if strict_export_accuracy_mode is None else strict_export_accuracy_mode
    if use_export_json_data or strict_export_accuracy_mode or total_value <= 0:
        return False
    shares = pd.to_numeric(metrics["shares"] if "shares" in metrics.columns else pd.Series(0.0, index=metrics.index), errors="coerce").fillna(0.0)
    prices = pd.to_numeric(metrics["current_price"] if "current_price" in metrics.columns else pd.Series(0.0, index=metrics.index), errors="coerce")
    if bool(((shares > 0) & (prices.isna() | ~prices.apply(math.isfinite) | (prices <= 0))).any()):
        return False
    upsert_live_portfolio_snapshot(total_value, db_path=db_path)
    return True


def _render_statement_history_import(total_value: float) -> None:
    with st.expander("Statement History Import", expanded=False):
        pdf_count, _ = _local_statement_status()
        summary = history_summary(STATEMENT_HISTORY_DB_PATH)
        state = scan_state(STATEMENT_HISTORY_DB_PATH)
        records = state["records"]
        continuity = state["monthly_continuity"]
        diagnostics = state["diagnostics"]
        for label, value in (("Local statement folder", "Ready"), ("PDFs detected", state["detected_pdf_count"] or pdf_count), ("Unique PDFs", state["unique_pdf_count"]), ("Processed unique", f"{state['processed_unique_count']} of {state['unique_pdf_count']}"), ("Duplicate files", state["duplicate_file_count"]), ("Accounted for", f"{state['processed_count']} of {state['detected_pdf_count'] or pdf_count}"), ("Remaining", max((state["detected_pdf_count"] or pdf_count) - state["processed_count"], 0)), ("Ready", state["ready"]), ("Needs review", state["needs_review"]), ("Unparseable", state["unparseable"]), ("Missing months", continuity["missing_month_count"]), ("Duplicate months", continuity["duplicate_month_count"]), ("Monthly continuity", "Complete" if continuity["complete"] else "Needs review"), ("Header markers found", diagnostics["header_found"]), ("Nearby date ranges found", diagnostics["nearby_date_range_found"]), ("Nearby account markers found", diagnostics["nearby_account_marker_found"]), ("Account summaries found", diagnostics["account_summary_found"]), ("Balance rows found", diagnostics["balance_rows_found"]), ("Accounting equations passed", diagnostics["accounting_equation_passed"]), ("Scan status", state["scan_status"]), ("Previously imported", summary["imported"])):
            st.caption(f"{label}: {value}")

        def render_progress(sequence: int, total_count: int) -> None:
            progress_text.caption(f"Processing statement {sequence} of {total_count}")

        def persist_progress(_sequence: int, total_count: int, _record: dict[str, object]) -> None:
            current = scan_state(STATEMENT_HISTORY_DB_PATH)
            progress_bar.progress(current["processed_count"] / total_count if total_count else 1.0)
            progress_text.caption(f"Processed {current['processed_count']} of {total_count} | Remaining: {max(total_count - current['processed_count'], 0)} | Duplicates: {current['duplicate_file_count']} | Ready: {current['ready']} | Needs review: {current['needs_review']} | Unparseable: {current['unparseable']}")
            live_preview.dataframe(pd.DataFrame(_statement_preview_rows(current["records"])), width="stretch", hide_index=True)

        start_scan = st.button("Start Scan", key="start_local_statements")
        resume_scan = st.button("Resume Scan", key="resume_local_statements", disabled=state["scan_status"] not in {"interrupted", "running"})
        if st.button("Clear Scan Preview", type="secondary", key="clear_statement_preview"):
            clear_scan_preview(STATEMENT_HISTORY_DB_PATH)
            st.rerun()
        if start_scan or resume_scan:
            started_at = time.perf_counter()
            log_event(LOGGER, "statement_scan_started", "Local statement scan started", pdf_count=pdf_count, parser_version="stash-pdf-v1")
            progress_text = st.empty()
            progress_bar = st.progress(0)
            live_preview = st.empty()
            try:
                state = run_statement_scan(STATEMENTS_DIR, db_path=STATEMENT_HISTORY_DB_PATH, restart=start_scan, on_document_started=render_progress, on_document_complete=persist_progress)
                records = state["records"]
            except Exception:
                st.error("The local statement scan could not complete. No statement data was imported.")
            finally:
                final_state = scan_state(STATEMENT_HISTORY_DB_PATH)
                progress_bar.progress(1.0)
                progress_text.caption(f"{final_state['scan_status'].title()} | Processed {final_state['processed_count']} of {final_state['total_count']} | Ready: {final_state['ready']} | Needs review: {final_state['needs_review']} | Unparseable: {final_state['unparseable']}")
                log_event(LOGGER, "statement_scan_completed", "Local statement scan completed", completed_count=final_state["processed_count"], failed_count=final_state["unparseable"], needs_review_count=final_state["needs_review"], duration_ms=round((time.perf_counter() - started_at) * 1000), parser_version="stash-pdf-v1", status=final_state["scan_status"])
        if records:
            st.dataframe(pd.DataFrame(_statement_preview_rows(records)).style.format({"Ending portfolio value": "${:,.2f}", "Invested value": "${:,.2f}", "Cash value": "${:,.2f}"}, na_rep="-"), width="stretch", hide_index=True)
        with st.expander("Manual Statement Review", expanded=False):
            missing_months = continuity["missing_months"]
            st.caption("Missing months: " + (", ".join(missing_months) if missing_months else "None"))
            manual_records = [record for record in records if record.get("source_type") == "manual_statement_review"]
            if missing_months:
                selected_month = st.selectbox("Missing month", missing_months, key="manual_statement_month")
                selected_year, selected_month_number = (int(part) for part in selected_month.split("-"))
                default_ending_date = datetime(selected_year, selected_month_number, calendar.monthrange(selected_year, selected_month_number)[1]).date()
                ending_date = st.date_input("Statement ending date", value=default_ending_date, key="manual_statement_ending_date")
                invested_value = st.number_input("Closing Total Priced Portfolio", min_value=0.0, value=0.0, step=0.01, format="%.2f", key="manual_statement_priced_portfolio")
                cash_value = st.number_input("Closing Net Account Balance", min_value=0.0, value=0.0, step=0.01, format="%.2f", key="manual_statement_cash")
                portfolio_value = st.number_input("Closing Total Equity Holdings", min_value=0.0, value=0.0, step=0.01, format="%.2f", key="manual_statement_total_equity")
                st.caption(f"Calculated expected total: {_format_currency(invested_value + cash_value)} | Entered Total Equity Holdings: {_format_currency(portfolio_value)}")
                confirmed = st.checkbox("I verified these values against the original statement.", key="manual_statement_confirmed")
                if st.button("Save Manual Review", key="save_manual_statement_review"):
                    try:
                        save_manual_statement_review(selected_month, ending_date.isoformat(), invested_value, cash_value, portfolio_value, confirmed, db_path=STATEMENT_HISTORY_DB_PATH)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            if manual_records:
                st.dataframe(pd.DataFrame(_statement_preview_rows(manual_records)).style.format({"Ending portfolio value": "${:,.2f}", "Invested value": "${:,.2f}", "Cash value": "${:,.2f}"}, na_rep="-"), width="stretch", hide_index=True)
                editable_months = {str(record["review_month"]): record for record in manual_records}
                edit_month = st.selectbox("Edit manual review month", sorted(editable_months), key="edit_manual_statement_month")
                edit_record = editable_months[edit_month]
                edit_date = st.date_input("Edited statement ending date", value=datetime.fromisoformat(str(edit_record["statement_end_date"])).date(), key="edit_manual_statement_date")
                edit_invested = st.number_input("Edited Closing Total Priced Portfolio", min_value=0.0, value=float(edit_record["invested_value"]), step=0.01, format="%.2f", key="edit_manual_statement_priced_portfolio")
                edit_cash = st.number_input("Edited Closing Net Account Balance", min_value=0.0, value=float(edit_record["cash_value"]), step=0.01, format="%.2f", key="edit_manual_statement_cash")
                edit_equity = st.number_input("Edited Closing Total Equity Holdings", min_value=0.0, value=float(edit_record["portfolio_value"]), step=0.01, format="%.2f", key="edit_manual_statement_equity")
                edit_confirmed = st.checkbox("I verified these edited values against the original statement.", value=bool(edit_record["confirmed"]), key="edit_manual_statement_confirmed")
                if st.button("Save Edited Manual Review", key="edit_manual_statement_review"):
                    try:
                        save_manual_statement_review(edit_month, edit_date.isoformat(), edit_invested, edit_cash, edit_equity, edit_confirmed, manual_id=str(edit_record["source_hash"]), db_path=STATEMENT_HISTORY_DB_PATH)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                for record in manual_records:
                    if st.button(f"Remove {record['review_month']}", key=f"remove_manual_{record['source_hash']}"):
                        remove_manual_statement_review(str(record["source_hash"]), STATEMENT_HISTORY_DB_PATH)
                        st.rerun()
            import_enabled = scan_can_import(state, pdf_count)
            if st.button("Import Validated History", key="import_validated_history", disabled=not import_enabled):
                started_at = time.perf_counter()
                log_event(LOGGER, "statement_import_started", "Validated statement import started", ready_count=state["ready"], parser_version="stash-pdf-v1")
                try:
                    result = import_validated_history(records, STATEMENT_HISTORY_DB_PATH)
                    st.session_state["statement_history_import_result"] = result
                    log_event(LOGGER, "statement_import_completed", "Validated statement import completed", imported_count=result["imported"], duplicate_count=result["duplicates"], failed_count=result["needs_review"] + result["unparseable"], duration_ms=round((time.perf_counter() - started_at) * 1000), parser_version="stash-pdf-v1", status="completed")
                except Exception:
                    log_event(LOGGER, "statement_import_failed", "Validated statement import failed", duration_ms=round((time.perf_counter() - started_at) * 1000), parser_version="stash-pdf-v1", status="failed")
                    st.error("The local import failed and no partial statement history was retained.")
        result = st.session_state.get("statement_history_import_result")
        if isinstance(result, dict):
            summary = history_summary(STATEMENT_HISTORY_DB_PATH)
            history = load_statement_history(STATEMENT_HISTORY_DB_PATH)
            dates = [str(item["history_date"]) for item in history]
            st.success(f"Successfully imported: {result.get('imported', 0)} | Duplicate statements skipped: {result.get('duplicates', 0)} | Statements requiring review: {result.get('needs_review', 0)} | Historical records stored locally: {summary['history_records']}")
            st.caption(f"Import coverage: {dates[0]} through {dates[-1]}" if dates else "Import coverage: no imported statement dates")
            st.caption("After verifying the imported dates and values, the original PDFs may be removed from the local Statements folder. Do not remove your only external backup.")
        if st.checkbox("Enable deletion of imported local history", key="enable_history_delete") and st.button("Delete Imported History", type="secondary", key="delete_imported_history"):
            delete_imported_history(STATEMENT_HISTORY_DB_PATH)
            st.session_state.pop("statement_history_import_result", None)
            st.success("Imported local statement history was deleted. Original PDFs were not changed.")
    history = load_performance_history(STATEMENT_HISTORY_DB_PATH)
    if history:
        st.markdown("#### Historical Portfolio Value")
        selected_range = st.radio(
            "History range",
            options=HISTORY_RANGE_OPTIONS,
            index=HISTORY_RANGE_OPTIONS.index(DEFAULT_HISTORY_RANGE),
            horizontal=True,
            key="portfolio_history_range",
            label_visibility="collapsed",
        )
        filtered_history = _filter_performance_history(history, selected_range)
        st.line_chart(pd.DataFrame(filtered_history).set_index("history_date")[["portfolio_value"]])
        st.caption("Historical statement points are verified statement-ending values, not daily market values. Live points are local daily snapshots that update as prices change.")


def _render_analysis_tab(metrics: pd.DataFrame, mobile_mode: bool = False) -> None:
    st.subheader("Portfolio Analysis")

    with st.expander("Assumptions", expanded=False):
        biweekly_contribution = st.number_input(
            "Bi-Weekly Contribution",
            min_value=0.0,
            step=10.0,
            value=280.0,
            format="%.2f",
            key="analysis_biweekly_contribution",
        )
        projection_return_pct = st.number_input(
            "Projection Return % (annual)",
            min_value=0.0,
            max_value=30.0,
            step=0.1,
            value=8.0,
            format="%.2f",
            key="analysis_projection_return",
        )
        st.number_input(
            "ESPP Contribution Per Paycheck",
            min_value=0.0,
            step=10.0,
            value=float(ESPP_CONTRIB_BIWEEKLY),
            format="%.2f",
            key="analysis_espp_contrib_biweekly",
        )
        st.selectbox(
            "ESPP Purchase Frequency",
            options=["6_months", "3_months", "12_months"],
            index=0,
            key="analysis_espp_purchase_frequency",
        )
        st.number_input(
            "ESPP Discount (decimal)",
            min_value=0.0,
            max_value=0.5,
            step=0.01,
            value=float(ESPP_DISCOUNT),
            format="%.2f",
            key="analysis_espp_discount",
        )

    biweekly_contribution = st.session_state.get("analysis_biweekly_contribution", 280.0)
    projection_return_pct = st.session_state.get("analysis_projection_return", 8.0)
    espp_contrib_biweekly = float(st.session_state.get("analysis_espp_contrib_biweekly", ESPP_CONTRIB_BIWEEKLY))
    espp_purchase_frequency = str(st.session_state.get("analysis_espp_purchase_frequency", ESPP_PURCHASE_FREQUENCY))
    espp_discount = float(st.session_state.get("analysis_espp_discount", ESPP_DISCOUNT))

    analysis_df = metrics.copy()
    analysis_df["asset_type"] = analysis_df["asset_type"].apply(_canonical_asset_type) if "asset_type" in analysis_df.columns else "Other"
    analysis_df["market_value"] = pd.to_numeric(analysis_df.get("market_value", 0.0), errors="coerce").fillna(0.0)
    analysis_df["annual_income"] = pd.to_numeric(analysis_df.get("annual_income", 0.0), errors="coerce").fillna(0.0)
    analysis_df["current_price"] = pd.to_numeric(analysis_df.get("current_price", 0.0), errors="coerce").fillna(0.0)
    analysis_df["shares"] = pd.to_numeric(analysis_df.get("shares", 0.0), errors="coerce").fillna(0.0)
    analysis_df["espp_acquired_shares"] = pd.to_numeric(analysis_df.get("espp_acquired_shares", 0.0), errors="coerce").fillna(0.0)
    analysis_df["regular_biweekly_contribution"] = pd.to_numeric(analysis_df.get("regular_biweekly_contribution", 0.0), errors="coerce").fillna(0.0)
    analysis_df["espp_biweekly_contribution"] = pd.to_numeric(analysis_df.get("espp_biweekly_contribution", 0.0), errors="coerce").fillna(0.0)
    analysis_df["special_contribution"] = pd.to_numeric(analysis_df.get("special_contribution", 0.0), errors="coerce").fillna(0.0)
    analysis_df["holding_yield_pct"] = analysis_df.apply(_resolve_holding_yield_pct, axis=1)
    analysis_df["holding_yield_pct"] = pd.to_numeric(analysis_df["holding_yield_pct"], errors="coerce").fillna(0.0)

    total_cost = float(analysis_df.get("cost_basis", analysis_df.get("amount_invested", 0.0)).sum()) if not analysis_df.empty else 0.0
    total_value = float(analysis_df["market_value"].sum()) if not analysis_df.empty else 0.0
    total_income = float(analysis_df["annual_income"].sum()) if not analysis_df.empty else 0.0
    gain = total_value - total_cost
    annual_contribution = biweekly_contribution * 26
    annual_regular_contribution = biweekly_contribution * 26

    _persist_live_snapshot_if_ready(analysis_df, total_value)
    _render_statement_history_import(total_value)

    weighted_yield_pct = (total_income / total_value * 100) if total_value > 0 else 0.0
    average_holding_yield_pct = float(analysis_df["holding_yield_pct"].mean()) if not analysis_df.empty else 0.0
    est_annual_income_with_contrib = total_income + (annual_regular_contribution * (weighted_yield_pct / 100))
    est_monthly_income_current = total_income / 12 if total_income > 0 else 0.0

    projected_10y_value = _future_value_with_contributions(total_value, projection_return_pct, biweekly_contribution, 10)
    projected_20y_value = _future_value_with_contributions(total_value, projection_return_pct, biweekly_contribution, 20)
    growth_10y_pct = ((projected_10y_value / total_value) - 1) * 100 if total_value > 0 else 0.0
    growth_20y_pct = ((projected_20y_value / total_value) - 1) * 100 if total_value > 0 else 0.0

    if mobile_mode:
        c1, c2 = st.columns(2)
        c1.metric("Total Cost Basis", _format_currency(total_cost))
        c2.metric("Market Value", _format_currency(total_value), delta=_format_currency(gain))
        c3, c4 = st.columns(2)
        c3.metric("Annual Income", _format_currency(total_income))
        c4.metric("Portfolio Yield", _format_percent((total_income / total_value * 100) if total_value > 0 else 0.0))
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Cost Basis", _format_currency(total_cost))
        c2.metric("Current Market Value", _format_currency(total_value), delta=_format_currency(gain))
        c3.metric("Est. Annual Dividend Income", _format_currency(total_income))
        c4.metric("Portfolio Yield", _format_percent((total_income / total_value * 100) if total_value > 0 else 0.0))

    contribution_context = _build_contribution_context(
        analysis_df,
        analysis_df,
        biweekly_contribution,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    contribution_rows = contribution_context.get("holdings", [])
    contribution_summary = _summarize_contribution_totals(contribution_rows)

    st.markdown("#### Portfolio Summary")
    summary_rows = [
        {"Metric": "Total Portfolio Value", "Value": _format_currency(total_value)},
        {"Metric": "Regular Biweekly Contribution", "Value": _format_currency(biweekly_contribution)},
        {"Metric": "ESPP Biweekly Contribution", "Value": _format_currency(espp_contrib_biweekly)},
        {"Metric": "Combined Biweekly Contribution", "Value": _format_currency(biweekly_contribution + espp_contrib_biweekly)},
        {"Metric": "Est. Added Annual Income From One Year of Regular Contributions", "Value": _format_currency(est_annual_income_with_contrib)},
        {"Metric": "Total Est. Annual Income (Current)", "Value": _format_currency(total_income)},
        {"Metric": "Total Est. Monthly Income (Current)", "Value": _format_currency(est_monthly_income_current)},
        {"Metric": "Average Holding Yield", "Value": _format_percent(average_holding_yield_pct)},
        {"Metric": "Weighted Portfolio Yield", "Value": _format_percent(weighted_yield_pct)},
        {"Metric": "Number of Holdings", "Value": f"{len(analysis_df)}"},
        {"Metric": "10-Year Projected Value", "Value": _format_currency(projected_10y_value)},
        {"Metric": "20-Year Projected Value", "Value": _format_currency(projected_20y_value)},
        {"Metric": "ESPP Purchase Frequency", "Value": espp_purchase_frequency},
        {"Metric": "ESPP Discount", "Value": _format_percent(espp_discount * 100)},
    ]
    st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)
    st.caption("Projections use regular portfolio contributions only and exclude ESPP payroll contributions unless explicitly stated otherwise.")
    st.caption("This estimate uses the current portfolio yield and one year of regular contributions only; it is not the portfolio's current annual income.")
    st.caption(_manual_input_data_note())
    total_market_value = float(analysis_df["market_value"].sum())
    if total_market_value > 0:
        analysis_df["weight_pct"] = analysis_df["market_value"] / total_market_value * 100
    else:
        analysis_df["weight_pct"] = 0.0

    st.markdown("#### Position Breakdown")
    display_cols = [
        "ticker",
        "shares",
        "amount_invested",
        "avg_cost",
        "current_price",
        "current_value",
        "dividend_yield_pct",
        "est_annual_div_per_share",
        "effective_annual_div_per_share",
        "cost_basis",
        "market_value",
        "weight_pct",
        "unrealized_gain",
        "annual_div_per_share",
        "annual_income",
        "yield_on_cost_pct",
        "portfolio_yield_pct",
        "sector",
    ]
    if mobile_mode:
        display_cols = [
            "ticker",
            "asset_type",
            "market_value",
            "annual_income",
            "portfolio_yield_pct",
            "weight_pct",
            "unrealized_gain",
        ]
    st.dataframe(
        analysis_df[display_cols].style.format(
            {
                "shares": "{:.4f}",
                "amount_invested": "${:,.2f}",
                "avg_cost": "${:,.2f}",
                "current_price": "${:,.2f}",
                "current_value": "${:,.2f}",
                "dividend_yield_pct": "{:.2f}%",
                "est_annual_div_per_share": "${:,.2f}",
                "effective_annual_div_per_share": "${:,.2f}",
                "cost_basis": "${:,.2f}",
                "market_value": "${:,.2f}",
                "weight_pct": "{:.2f}%",
                "unrealized_gain": "${:,.2f}",
                "annual_div_per_share": "${:,.2f}",
                "annual_income": "${:,.2f}",
                "yield_on_cost_pct": "{:.2f}%",
                "portfolio_yield_pct": "{:.2f}%",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### Asset Allocation By Type")
    analysis_df["asset_type"] = (
        analysis_df["asset_type"].fillna("Stock").astype(str).str.strip().replace({"": "Stock", "0": "Stock", "nan": "Stock", "None": "Stock"})
    )
    asset_group = (
        analysis_df.groupby("asset_type", dropna=False)
        .agg(market_value=("market_value", "sum"), annual_income=("annual_income", "sum"))
        .reset_index()
    )
    asset_group["asset_type"] = asset_group["asset_type"].replace("", "Stock")
    total_asset_value = float(asset_group["market_value"].sum())
    if total_asset_value > 0:
        asset_group["allocation_pct"] = asset_group["market_value"] / total_asset_value * 100
    else:
        asset_group["allocation_pct"] = 0.0
    st.dataframe(asset_group.sort_values("market_value", ascending=False), width="stretch", hide_index=True)

    stock_value = float(asset_group.loc[asset_group["asset_type"].eq("Stock"), "market_value"].sum())
    etf_value = float(asset_group.loc[asset_group["asset_type"].eq("ETF"), "market_value"].sum())
    reit_value = float(asset_group.loc[asset_group["asset_type"].eq("REIT"), "market_value"].sum())
    stock_alloc_pct = (stock_value / total_value * 100) if total_value > 0 else 0.0
    etf_alloc_pct = (etf_value / total_value * 100) if total_value > 0 else 0.0
    reit_alloc_pct = (reit_value / total_value * 100) if total_value > 0 else 0.0

    alloc_rows = [
        {"Metric": "Stock Allocation %", "Value": _format_percent(stock_alloc_pct)},
        {"Metric": "ETF Allocation %", "Value": _format_percent(etf_alloc_pct)},
        {"Metric": "REIT Allocation %", "Value": _format_percent(reit_alloc_pct)},
    ]
    st.dataframe(pd.DataFrame(alloc_rows), width="stretch", hide_index=True)

    st.markdown("#### Investment Intelligence")
    intelligence_rows = _build_investment_intelligence_rows(analysis_df)
    if intelligence_rows:
        intelligence_df = pd.DataFrame(intelligence_rows)
        intelligence_df = intelligence_df.replace({"n/a": None, "N/A": None})
        st.dataframe(intelligence_df, width="stretch", hide_index=True)
    else:
        st.caption("No supplemental Yahoo Finance intelligence available for the current holdings.")

    st.markdown("#### Direct Holdings Sector Exposure")
    direct_sector_records = _build_direct_sector_classification_records(analysis_df)
    if direct_sector_records:
        direct_sector_df = pd.DataFrame(direct_sector_records)
        direct_sector_df = direct_sector_df.rename(columns={"Sector": "Sector", "Market_Value": "market_value"})
        direct_sector_df["Sector"] = direct_sector_df["Sector"].replace({"Unresolved Direct Sector": "Unassigned"})
        direct_sector_df = direct_sector_df.sort_values("market_value", ascending=False)
        display_direct_sector_df = direct_sector_df.copy()
        display_direct_sector_df["Market_Value"] = display_direct_sector_df["market_value"].map(lambda value: _format_currency(float(value)))
        display_direct_sector_df["Portfolio_Weight_Pct"] = display_direct_sector_df["Portfolio_Weight_Pct"].map(lambda value: _format_percent(float(value)))
        display_direct_sector_df["Pct_of_Resolved_Direct_Sector_Exposure"] = display_direct_sector_df["Pct_of_Resolved_Direct_Sector_Exposure"].map(lambda value: _format_percent(float(value)))
        st.dataframe(display_direct_sector_df[["Sector", "Market_Value", "Portfolio_Weight_Pct", "Pct_of_Resolved_Direct_Sector_Exposure"]], width="stretch", hide_index=True)
        if not direct_sector_df.empty:
            st.bar_chart(direct_sector_df.set_index("Sector")[["market_value"]])
    else:
        st.caption("No direct sector exposure available for the current holdings.")

    st.markdown("#### Concentration")
    concentration_df = analysis_df[["ticker", "market_value", "annualized_income"]].copy() if "annualized_income" in analysis_df.columns else analysis_df[["ticker", "market_value", "annual_income"]].copy()
    concentration_df = concentration_df.sort_values("market_value", ascending=False).head(10)
    st.dataframe(concentration_df, width="stretch", hide_index=True)
    if not concentration_df.empty:
        st.bar_chart(concentration_df.set_index("ticker")[["market_value"]])

    st.markdown("#### ESPP vs Non-ESPP Holdings")
    espp_split = analysis_df.copy()
    espp_split["espp_split"] = espp_split.apply(_split_espp_market_value, axis=1)
    espp_split = pd.concat([espp_split.reset_index(drop=True), pd.DataFrame(espp_split["espp_split"].tolist())], axis=1)
    split_summary = pd.DataFrame(
        [
            {"Bucket": "ESPP Holdings", "Shares": float(espp_split["espp_shares"].sum()), "Market Value": float(espp_split["espp_market_value"].sum())},
            {"Bucket": "Non-ESPP Holdings", "Shares": float(espp_split["non_espp_shares"].sum()), "Market Value": float(espp_split["non_espp_market_value"].sum())},
        ]
    )
    st.dataframe(split_summary, width="stretch", hide_index=True)
    st.bar_chart(split_summary.set_index("Bucket")[["Market Value"]])

    st.markdown("#### Fund Look-Through Exposure")
    lookthrough_records = _build_look_through_sector_exposure_records(analysis_df)
    lookthrough_df = pd.DataFrame(lookthrough_records)
    if lookthrough_df.empty:
        st.caption("No look-through sector exposure available.")
    else:
        st.dataframe(lookthrough_df, width="stretch", hide_index=True)

    st.markdown("#### Income Quality")
    income_quality_df = pd.DataFrame(_build_income_quality_records(analysis_df))
    if income_quality_df.empty:
        st.caption("No income quality data available.")
    else:
        st.dataframe(income_quality_df, width="stretch", hide_index=True)

    st.markdown("#### Contribution Breakdown")
    contribution_context = _build_contribution_context(
        analysis_df,
        analysis_df,
        biweekly_contribution,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    contribution_df = pd.DataFrame(contribution_context.get("holdings", []))
    if contribution_df.empty:
        st.caption("No contribution breakdown available.")
    else:
        st.dataframe(contribution_df, width="stretch", hide_index=True)

    st.markdown("#### Yield Analysis")
    highest_yield = _select_highest_valid_yield(analysis_df)
    lowest_positive_yield = _select_lowest_positive_yield(analysis_df)
    highest_yield_name = str(highest_yield.get("ticker", "N/A"))
    lowest_yield_name = str(lowest_positive_yield.get("ticker", "N/A"))
    highest_yield_pct = float(highest_yield.get("yield_pct", 0.0) or 0.0)
    lowest_yield_pct = float(lowest_positive_yield.get("yield_pct", 0.0) or 0.0)
    yield_spread_pct = highest_yield_pct - lowest_yield_pct
    yield_rows = [
        {"Metric": "Highest Current Yield Holding", "Value": highest_yield_name},
        {"Metric": "Highest Current Yield", "Value": _format_percent(highest_yield_pct)},
        {"Metric": "Lowest Positive Current Yield Holding", "Value": lowest_yield_name},
        {"Metric": "Lowest Positive Current Yield", "Value": _format_percent(lowest_yield_pct)},
        {"Metric": "Yield Spread (High-Low)", "Value": _format_percent(yield_spread_pct)},
    ]
    st.dataframe(pd.DataFrame(yield_rows), width="stretch", hide_index=True)

    st.markdown("#### Income Analysis")
    largest_income = analysis_df.sort_values("annual_income", ascending=False).head(1)
    largest_income_name = largest_income.iloc[0]["ticker"] if not largest_income.empty else "N/A"
    largest_income_value = float(largest_income.iloc[0]["annual_income"]) if not largest_income.empty else 0.0
    top3_income = float(analysis_df.sort_values("annual_income", ascending=False).head(3)["annual_income"].sum())
    top3_income_pct = (top3_income / total_income * 100) if total_income > 0 else 0.0
    passive_holders = max(len(analysis_df) - contribution_context.get("summary", {}).get("holdings_with_regular_contributions", 0), 0)
    active_contributors = int(sum(1 for row in contribution_rows if float(row.get("total_contribution", 0.0) or 0.0) > 0))
    income_rows = [
        {"Metric": "Largest Income Producer", "Value": largest_income_name},
        {"Metric": "Its Annual Income", "Value": _format_currency(largest_income_value)},
        {"Metric": "Top 3 Income % of Total", "Value": _format_percent(top3_income_pct)},
        {"Metric": "Active Biweekly Contributions", "Value": f"{active_contributors}"},
        {"Metric": "Passive Holdings (No contribution)", "Value": f"{passive_holders}"},
    ]
    st.dataframe(pd.DataFrame(income_rows), width="stretch", hide_index=True)

    st.markdown("#### Estimated Income by Month")
    seasonality = _build_monthly_income_seasonality(
        analysis_df,
        month_weights_map=_latest_dividend_month_weights(_to_ticker_tuple(analysis_df["ticker"])) if not analysis_df.empty else {},
    )
    monthly_rows = [{"Month": month, "Estimated Income": seasonality["monthly_income"][month]} for month in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]]
    monthly_df = pd.DataFrame(monthly_rows)
    if seasonality["chart_available"]:
        st.caption(
            "Estimated distribution of current annual portfolio income based on available historical dividend timing. "
            "This is not a schedule of confirmed future payments, and actual payment dates and amounts may vary."
        )
        st.dataframe(monthly_df.style.format({"Estimated Income": "${:,.2f}"}), width="stretch", hide_index=True)
        st.bar_chart(monthly_df.set_index("Month")[["Estimated Income"]])
        st.caption(
            f"Historical timing coverage: {_format_percent(seasonality['coverage_pct'])} of estimated annual income. "
            f"Income without usable timing data: {_format_currency(seasonality['uncovered_annual_income'])} annually."
        )
    else:
        st.info(seasonality["message"])
        if seasonality["uncovered_annual_income"] > 0:
            st.caption(f"Estimated annual income without usable timing data: {_format_currency(seasonality['uncovered_annual_income'])}.")

    st.markdown("#### Growth Projections")
    growth_rows = [
        {"Metric": "10-Year Growth (from current)", "Value": _format_percent(growth_10y_pct)},
        {"Metric": "20-Year Growth (from current)", "Value": _format_percent(growth_20y_pct)},
        {"Metric": "Projection Assumption", "Value": "Regular contributions only; ESPP excluded"},
    ]
    st.dataframe(pd.DataFrame(growth_rows), width="stretch", hide_index=True)

    st.markdown("#### Snowball Tracker")
    income_vs_contrib_pct = (total_income / annual_contribution * 100) if annual_contribution > 0 else 0.0
    snowball_rows = [
        {"Metric": "Annual Contributions", "Value": _format_currency(annual_contribution)},
        {"Metric": "Current Annual Income", "Value": _format_currency(total_income)},
        {"Metric": "Income as % of Contributions", "Value": _format_percent(income_vs_contrib_pct)},
    ]
    st.dataframe(pd.DataFrame(snowball_rows), width="stretch", hide_index=True)

    st.markdown("#### Milestones")
    milestones = [
        ("Early Snowball (25%)", annual_contribution * 0.25),
        ("Momentum Building (50%)", annual_contribution * 0.50),
        ("Real Snowball (100%)", annual_contribution * 1.00),
        ("Compounding Dominates (200%)", annual_contribution * 2.00),
    ]
    milestone_rows = [{"Milestone": name, "Target Annual Income": _format_currency(target)} for name, target in milestones]
    st.dataframe(pd.DataFrame(milestone_rows), width="stretch", hide_index=True)

    st.markdown("#### Your Progress")
    for milestone_name, milestone_target in milestones:
        progress_pct = (total_income / milestone_target * 100) if milestone_target > 0 else 0.0
        progress_pct = min(max(progress_pct, 0.0), 100.0)
        st.write(f"{milestone_name}: {_format_percent(progress_pct)}")
        st.progress(progress_pct / 100)

    st.markdown("#### Est. Years To Reach")
    annual_income_growth = annual_contribution * (weighted_yield_pct / 100)
    years_rows = []
    for milestone_name, milestone_target in milestones:
        years_needed = _years_to_income_target(total_income, milestone_target, annual_income_growth)
        years_text = "N/A" if years_needed == float("inf") else f"{years_needed:,.1f}"
        years_rows.append({"Milestone": milestone_name, "Years": years_text})
    st.dataframe(pd.DataFrame(years_rows), width="stretch", hide_index=True)

    st.markdown("#### Next Milestone")
    next_name = "Compounding Dominates (200%)"
    next_target = milestones[-1][1]
    for milestone_name, milestone_target in milestones:
        if total_income < milestone_target:
            next_name = milestone_name
            next_target = milestone_target
            break
    amount_still_needed = max(next_target - total_income, 0.0)
    years_to_next = _years_to_income_target(total_income, next_target, annual_income_growth)
    months_to_next = "N/A" if years_to_next == float("inf") else f"{int(round(years_to_next * 12))}"
    next_rows = [
        {"Metric": "Your Next Target", "Value": next_name},
        {"Metric": "Amount Still Needed", "Value": _format_currency(amount_still_needed)},
        {"Metric": "Est. Months Away", "Value": months_to_next},
    ]
    st.dataframe(pd.DataFrame(next_rows), width="stretch", hide_index=True)


def _onemin_ai_base_url() -> str:
    override = str(os.environ.get("ONEMIN_AI_BASE_URL", "")).strip().rstrip("/")
    return override or ONEMIN_AI_DEFAULT_BASE_URL


def _onemin_ai_default_model() -> str:
    return str(os.environ.get("ONEMIN_AI_MODEL", "")).strip() or ONEMIN_AI_DEFAULT_MODEL


def _onemin_ai_has_api_key() -> bool:
    return bool(str(os.environ.get("ONEMIN_AI_API_KEY", "")).strip())


def _onemin_ai_model_options(default_model: str) -> list[str]:
    configured_model = str(default_model or "").strip() or ONEMIN_AI_DEFAULT_MODEL
    return list(dict.fromkeys([configured_model, *ONEMIN_AI_RECOMMENDED_MODELS]))


def _onemin_ai_model_label(model: str) -> str:
    model_id = str(model or "").strip()
    details = ONEMIN_AI_MODEL_DETAILS.get(model_id)
    return str(details.get("label") if details else model_id)


def _onemin_ai_model_description(model: str) -> str:
    model_id = str(model or "").strip()
    details = ONEMIN_AI_MODEL_DETAILS.get(model_id)
    return str(
        details.get("description")
        if details
        else "Custom model configured through the local environment. Availability, quality, and credit usage depend on 1min.AI."
    )


def _onemin_ai_model_index(default_model: str) -> int:
    options = _onemin_ai_model_options(default_model)
    try:
        return options.index(str(default_model or "").strip())
    except ValueError:
        return 0


def _analysis_type_index(default_analysis_type: str) -> int:
    try:
        return AI_ANALYSIS_TYPES.index(default_analysis_type)
    except ValueError:
        return 0


def _onemin_ai_timeout_seconds() -> float:
    raw_value = os.environ.get("ONEMIN_AI_TIMEOUT_SECONDS")
    if raw_value is None:
        return float(ONEMIN_AI_TIMEOUT_SECONDS)
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return float(ONEMIN_AI_TIMEOUT_SECONDS)
    if not math.isfinite(parsed) or parsed <= 0:
        return float(ONEMIN_AI_TIMEOUT_SECONDS)
    return parsed


def _estimated_tokens_from_chars(char_count: int) -> int:
    return int(math.ceil(max(char_count, 0) / 4.0))


def _is_empty_context_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _strip_empty_context_values(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, nested in value.items():
            normalized = _strip_empty_context_values(nested)
            if not _is_empty_context_value(normalized):
                cleaned[str(key)] = normalized
        return cleaned
    if isinstance(value, list):
        cleaned_list = [_strip_empty_context_values(item) for item in value]
        return [item for item in cleaned_list if not _is_empty_context_value(item)]
    return value


def _compact_records(
    records: object,
    fields: list[str],
    limit: int,
    sort_by: str | None = None,
) -> list[dict[str, object]]:
    if not isinstance(records, list) or limit <= 0:
        return []

    normalized_records = [item for item in records if isinstance(item, dict)]
    if sort_by:
        normalized_records = sorted(
            normalized_records,
            key=lambda row: _safe_float(row.get(sort_by), 0.0),
            reverse=True,
        )

    compacted: list[dict[str, object]] = []
    for row in normalized_records[:limit]:
        trimmed = {field: row.get(field) for field in fields if field in row}
        cleaned = _strip_empty_context_values(trimmed)
        if isinstance(cleaned, dict) and cleaned:
            compacted.append(cleaned)
    return compacted


def _pick_non_empty_fields(source: object, fields: list[str]) -> dict[str, object]:
    if not isinstance(source, dict):
        return {}
    out: dict[str, object] = {}
    for field in fields:
        value = source.get(field)
        if not _is_empty_context_value(value):
            out[field] = value
    return out


def _extract_data_quality_warnings(full_payload: dict[str, object]) -> list[str]:
    warnings: list[str] = []

    warnings_payload = full_payload.get("warnings")
    if isinstance(warnings_payload, dict):
        for key in ("snapshot_validation_errors", "missing_export_fields"):
            values = warnings_payload.get(key)
            if isinstance(values, list):
                for value in values:
                    text = str(value).strip()
                    if text:
                        warnings.append(text)

    for issue in full_payload.get("data_quality_issues", []) if isinstance(full_payload.get("data_quality_issues"), list) else []:
        text = str(issue).strip()
        if text:
            warnings.append(text)

    data_completeness = full_payload.get("data_completeness")
    if isinstance(data_completeness, dict):
        direct_cov = _safe_float(data_completeness.get("direct_sector_classification_coverage_pct"), 0.0)
        lookthrough_cov = _safe_float(data_completeness.get("fund_lookthrough_coverage_pct"), 0.0)
        total_cov = _safe_float(data_completeness.get("overall_resolved_exposure_coverage_pct"), 0.0)
        if direct_cov < 90:
            warnings.append(f"Direct sector classification coverage is low ({direct_cov:.1f}%).")
        if lookthrough_cov < 90:
            warnings.append(f"Fund look-through coverage is low ({lookthrough_cov:.1f}%).")
        if total_cov < 90:
            warnings.append(f"Overall resolved exposure coverage is low ({total_cov:.1f}%).")

    unresolved_exposure = full_payload.get("unresolved_exposure")
    if isinstance(unresolved_exposure, dict):
        unresolved_value = _safe_float(unresolved_exposure.get("unresolved_fund_exposure"), 0.0)
        if unresolved_value > 0:
            warnings.append(
                f"Unresolved fund look-through exposure remains in portfolio totals (${unresolved_value:,.2f})."
            )

    deduped = []
    seen: set[str] = set()
    for warning in warnings:
        if warning not in seen:
            deduped.append(warning)
            seen.add(warning)
    return deduped


def _summarize_supplemental_yahoo_data(
    records: object,
    priority_tickers: list[str],
    limit: int,
) -> list[dict[str, object]]:
    if not isinstance(records, list) or limit <= 0:
        return []

    by_ticker: dict[str, dict[str, object]] = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        by_ticker[ticker] = row

    ordered_tickers = []
    for ticker in priority_tickers:
        upper = str(ticker).strip().upper()
        if upper and upper in by_ticker and upper not in ordered_tickers:
            ordered_tickers.append(upper)
    for ticker in by_ticker:
        if ticker not in ordered_tickers:
            ordered_tickers.append(ticker)

    summarized: list[dict[str, object]] = []
    for ticker in ordered_tickers[:limit]:
        source = by_ticker.get(ticker, {})
        fundamentals = _pick_non_empty_fields(
            source.get("fundamental_metrics", {}),
            [
                "dividend_yield",
                "dividend_rate",
                "payout_ratio",
                "trailing_pe",
                "forward_pe",
                "beta",
                "debt_to_equity",
                "interest_coverage",
            ],
        )
        growth = _pick_non_empty_fields(
            source.get("earnings_and_estimates", {}),
            ["earnings_growth", "revenue_growth", "eps_current_year", "eps_next_year", "next_earnings_date"],
        )
        analyst = _pick_non_empty_fields(
            source.get("analyst_targets", {}),
            ["recommendation_key", "target_mean_price", "target_high_price", "target_low_price"],
        )
        fund_data = source.get("fund_data", {})
        fund_sector_weights = []
        if isinstance(fund_data, dict):
            sector_weights = fund_data.get("sector_weight_summary")
            fund_sector_weights = _compact_records(
                sector_weights,
                ["sector", "weight_pct"],
                limit=4,
                sort_by="weight_pct",
            )

        entry = {
            "ticker": ticker,
            "fundamentals": fundamentals,
            "growth_and_estimates": growth,
            "analyst_signal": analyst,
            "fund_sector_weights": fund_sector_weights,
            "data_source": source.get("data_source", "Yahoo Finance"),
        }
        cleaned = _strip_empty_context_values(entry)
        if isinstance(cleaned, dict) and cleaned:
            summarized.append(cleaned)
    return summarized


def _build_compact_portfolio_history_summary(history: object) -> dict[str, object]:
    if not isinstance(history, list):
        return {}
    points: list[tuple[pd.Timestamp, float]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        date_value = pd.to_datetime(row.get("history_date"), errors="coerce")
        value = pd.to_numeric(row.get("portfolio_value"), errors="coerce")
        if pd.isna(date_value) or pd.isna(value):
            continue
        points.append((date_value.normalize(), float(value)))
    if not points:
        return {}
    points.sort(key=lambda item: item[0])
    earliest_date, earliest_value = points[0]
    latest_date, latest_value = points[-1]

    def baseline(months: int, minimum_span_days: int, target_tolerance_days: int = 35) -> dict[str, object]:
        # Require meaningful period coverage, then allow roughly one monthly
        # statement interval around the calendar target. Values are never interpolated.
        if (latest_date - earliest_date).days < minimum_span_days:
            return {}
        target = latest_date - pd.DateOffset(months=months)
        baseline_date, baseline_value = min(points, key=lambda item: (abs((item[0] - target).days), item[0]))
        if abs((baseline_date - target).days) > target_tolerance_days:
            return {}
        return {
            "date": baseline_date.date().isoformat(),
            "value": round(baseline_value, 2),
            "nominal_change_pct": round((latest_value / baseline_value - 1.0) * 100.0, 2) if baseline_value else None,
        }

    summary = {
        "latest_date": latest_date.date().isoformat(),
        "latest_value": round(latest_value, 2),
        "earliest_date": earliest_date.date().isoformat(),
        "earliest_value": round(earliest_value, 2),
        "point_count": len(points),
        "interpretation": "Nominal account-value change includes contributions/withdrawals and is not investment return.",
    }
    one_year = baseline(12, minimum_span_days=330)
    three_month = baseline(3, minimum_span_days=75)
    if one_year:
        summary["one_year_baseline"] = one_year
    if three_month:
        summary["three_month_baseline"] = three_month
    return summary


def _build_compact_seasonality_summary(seasonality: object) -> dict[str, object]:
    if not isinstance(seasonality, dict) or not seasonality.get("chart_available"):
        return {}
    monthly = seasonality.get("monthly_income", {})
    if not isinstance(monthly, dict) or not monthly:
        return {}
    usable = {str(month): float(value or 0.0) for month, value in monthly.items()}
    highest = max(usable.values())
    lowest = min(usable.values())
    return {
        "highest_income_months": [month for month, value in usable.items() if value == highest],
        "lowest_income_months": [month for month, value in usable.items() if value == lowest],
        "highest_month_income": round(highest, 2),
        "lowest_month_income": round(lowest, 2),
        "timing_coverage_pct": round(float(seasonality.get("coverage_pct", 0.0) or 0.0) * 100.0, 2),
        "uncovered_annual_income": round(float(seasonality.get("uncovered_annual_income", 0.0) or 0.0), 2),
    }


def _compact_analysis_context(
    full_payload: dict[str, object],
    analysis_type: str,
    custom_question: str,
    portfolio_history_summary: dict[str, object] | None = None,
    dividend_seasonality_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    selected_type = str(analysis_type or AI_ANALYSIS_TYPES[0]).strip()
    if selected_type not in AI_ANALYSIS_TYPES:
        selected_type = AI_ANALYSIS_TYPES[0]

    custom_lower = str(custom_question or "").lower()
    profile = {
        "holdings_limit": 18,
        "income_limit": 10,
        "exposure_limit": 8,
        "historical_limit": 6,
        "supplemental_limit": 6,
    }
    if selected_type == "Comprehensive Portfolio Review":
        profile = {"holdings_limit": 24, "income_limit": 14, "exposure_limit": 12, "historical_limit": 8, "supplemental_limit": 8}
    elif selected_type == "Income and Dividend Analysis":
        profile = {"holdings_limit": 16, "income_limit": 16, "exposure_limit": 5, "historical_limit": 6, "supplemental_limit": 8}
    elif selected_type == "Diversification and Concentration":
        profile = {"holdings_limit": 18, "income_limit": 8, "exposure_limit": 14, "historical_limit": 4, "supplemental_limit": 5}
    elif selected_type == "Risk Analysis":
        profile = {"holdings_limit": 18, "income_limit": 10, "exposure_limit": 12, "historical_limit": 6, "supplemental_limit": 6}
    elif selected_type == "Contribution Allocation Review":
        profile = {"holdings_limit": 14, "income_limit": 10, "exposure_limit": 6, "historical_limit": 4, "supplemental_limit": 4}
    elif selected_type == "Long-Term Outlook":
        profile = {"holdings_limit": 16, "income_limit": 10, "exposure_limit": 8, "historical_limit": 8, "supplemental_limit": 6}
    elif selected_type == "Custom Question":
        profile = {"holdings_limit": 18, "income_limit": 10, "exposure_limit": 8, "historical_limit": 6, "supplemental_limit": 5}
        if any(keyword in custom_lower for keyword in ["income", "dividend", "yield", "payout"]):
            profile["income_limit"] = 16
            profile["supplemental_limit"] = 8
        if any(keyword in custom_lower for keyword in ["divers", "sector", "allocation", "concentration", "weight"]):
            profile["exposure_limit"] = 14
        if any(keyword in custom_lower for keyword in ["risk", "drawdown", "volatility"]):
            profile["historical_limit"] = 8
        if any(keyword in custom_lower for keyword in ["contribution", "espp", "biweekly", "outlook", "projection"]):
            profile["holdings_limit"] = 20

    snapshot = full_payload.get("portfolio_snapshot", {}) if isinstance(full_payload.get("portfolio_snapshot", {}), dict) else {}
    investor_profile = full_payload.get("investor_profile", {}) if isinstance(full_payload.get("investor_profile", {}), dict) else {}
    portfolio_health = full_payload.get("portfolio_health", {}) if isinstance(full_payload.get("portfolio_health", {}), dict) else {}
    context_summary = portfolio_health.get("context_summary", {}) if isinstance(portfolio_health, dict) else {}
    contribution_context = full_payload.get("contribution_context", {}) if isinstance(full_payload.get("contribution_context", {}), dict) else {}
    contribution_summary = contribution_context.get("summary", {}) if isinstance(contribution_context.get("summary", {}), dict) else {}

    holdings = _compact_records(
        full_payload.get("calculated_metrics", []),
        [
            "ticker",
            "shares",
            "current_price",
            "market_value",
            "portfolio_weight_pct",
            "cost_basis",
            "unrealized_gain",
            "annual_income",
            "portfolio_yield_pct",
            "dividend_yield_pct",
            "regular_biweekly_contribution",
            "espp_biweekly_contribution",
            "special_contribution",
            "total_contribution",
            "sector",
            "asset_type",
            "employer_stock",
            "espp_enabled",
            "espp_acquired_shares",
        ],
        limit=profile["holdings_limit"],
        sort_by="market_value",
    )

    income_by_holding = _compact_records(
        full_payload.get("income_allocation_by_holding", []),
        ["ticker", "annual_income", "market_value", "income_allocation_pct"],
        limit=profile["income_limit"],
        sort_by="annual_income",
    )

    asset_allocation = _compact_records(
        full_payload.get("asset_allocation", []),
        ["asset_type", "market_value", "annual_income", "portfolio_weight_pct"],
        limit=profile["exposure_limit"],
        sort_by="market_value",
    )
    direct_sector = _compact_records(
        full_payload.get("direct_sector_exposure", []),
        ["sector", "market_value", "annual_income", "portfolio_weight_pct"],
        limit=profile["exposure_limit"],
        sort_by="market_value",
    )
    lookthrough_sector = _compact_records(
        full_payload.get("look_through_sector_exposure", []),
        ["sector", "source_holdings", "market_value", "portfolio_weight_pct"],
        limit=max(4, profile["exposure_limit"] // 2),
        sort_by="market_value",
    )

    top_holding_tickers = [str(row.get("ticker", "")).strip().upper() for row in holdings if isinstance(row, dict)]
    supplemental_intelligence = _summarize_supplemental_yahoo_data(
        full_payload.get("supplemental_yahoo_financial_data", []),
        priority_tickers=top_holding_tickers,
        limit=profile["supplemental_limit"],
    )

    historical_context = _compact_records(
        full_payload.get("historical_context", []),
        ["ticker", "return_pct", "drawdown", "volatility", "52w_high", "52w_low"],
        limit=profile["historical_limit"],
    )

    compact_payload: dict[str, object] = {
        "portfolio_snapshot": _pick_non_empty_fields(
            snapshot,
            [
                "portfolio_value",
                "cost_basis",
                "unrealized_gain",
                "annual_income",
                "weighted_yield_pct",
                "holdings_count",
                "top_holding",
                "top_holding_weight_pct",
                "projection_contribution_note",
                "projected_10y_value",
                "projected_20y_value",
            ],
        ),
        "investor_profile": _pick_non_empty_fields(investor_profile, ["primary_goal", "risk_tolerance", "investment_horizon_years"]),
        "calculated_metrics": holdings,
        "income_allocation_by_holding": income_by_holding,
        "asset_allocation": asset_allocation,
        "direct_sector_exposure": direct_sector,
        "look_through_sector_exposure": lookthrough_sector,
        "concentration_context": _pick_non_empty_fields(
            full_payload.get("concentration_context", {}),
            [
                "largest_holding_by_value",
                "largest_holding_value_weight_pct",
                "largest_holding_income_weight_pct",
                "largest_income_contributor",
                "largest_income_weight_pct",
                "guidance",
            ],
        ),
        "portfolio_health": {
            "health_score_breakdown": _pick_non_empty_fields(
                portfolio_health.get("health_score_breakdown", {}),
                [
                    "diversification",
                    "concentration",
                    "income_stability",
                    "dividend_growth_potential",
                    "total_return_potential",
                    "risk",
                    "data_quality",
                ],
            ),
            "context_summary": _pick_non_empty_fields(
                context_summary,
                ["score", "score_label", "strongest_areas", "weakest_areas", "primary_alert"],
            ),
        },
        "tracker_health_score_metadata": _pick_non_empty_fields(
            full_payload.get("tracker_health_score_metadata", {}),
            ["income_stability_explanation", "data_quality_explanation", "risk_explanation"],
        ),
        "income_quality_summary": _pick_non_empty_fields(
            full_payload.get("income_quality", {}),
            ["income_quality_score", "distribution_stability_score", "yield_sustainability_score", "payout_risk_score", "alerts"],
        ) if selected_type in {"Comprehensive Portfolio Review", "Income and Dividend Analysis"} else {},
        "contribution_context": {
            "summary": {
                "total_regular_biweekly_contribution": contribution_summary.get("total_regular_biweekly_contribution"),
                "total_espp_biweekly_contributions": contribution_summary.get("total_espp_biweekly_contributions"),
                "annual_regular_contributions": contribution_summary.get("estimated_annual_regular_contributions"),
                "annual_espp_contributions": contribution_summary.get("estimated_annual_espp_contributions"),
                "estimated_combined_annual_contributions": contribution_summary.get("estimated_combined_annual_contributions"),
                "hd_espp_purchase_frequency": contribution_summary.get("hd_espp_purchase_frequency"),
                "hd_espp_discount_pct": contribution_summary.get("hd_espp_discount_pct"),
            },
            "employer_stock_concentration_context": _pick_non_empty_fields(
                contribution_context.get("employer_stock_concentration_context", {}),
                [
                    "primary_employer_ticker",
                    "primary_employer_capital_concentration_pct",
                    "primary_employer_income_concentration_pct",
                    "is_concentration_alert",
                ],
            ),
        },
        "data_completeness": _pick_non_empty_fields(
            full_payload.get("data_completeness", {}),
            [
                "direct_sector_classification_coverage_pct",
                "fund_lookthrough_coverage_pct",
                "overall_resolved_exposure_coverage_pct",
            ],
        ),
        "unresolved_exposure": _pick_non_empty_fields(
            full_payload.get("unresolved_exposure", {}),
            ["unresolved_fund_exposure", "resolved_lookthrough_value", "fund_total_value"],
        ),
        "supplemental_yahoo_financial_data": supplemental_intelligence,
        "historical_context": historical_context,
        "portfolio_history_summary": (portfolio_history_summary or {}) if (
            selected_type in {"Comprehensive Portfolio Review", "Long-Term Outlook"}
            or (selected_type == "Custom Question" and any(word in custom_lower for word in ["history", "historical", "outlook", "growth"]))
        ) else {},
        "dividend_seasonality_summary": dividend_seasonality_summary if selected_type in {"Comprehensive Portfolio Review", "Income and Dividend Analysis"} else {},
        "warnings": {
            "important": _extract_data_quality_warnings(full_payload)[:8],
        },
    }

    income_summary = compact_payload.get("income_quality_summary")
    if isinstance(income_summary, dict) and isinstance(income_summary.get("alerts"), list):
        income_summary["alerts"] = income_summary["alerts"][:3]

    contribution = compact_payload["contribution_context"]
    summary = contribution["summary"]
    employer = contribution["employer_stock_concentration_context"]
    annual_total = float(summary.get("estimated_combined_annual_contributions") or 0.0)
    annual_regular = float(summary.get("annual_regular_contributions") or 0.0)
    annual_employer = float(summary.get("annual_espp_contributions") or 0.0)
    if annual_total > 0:
        employer_cash_pct = annual_employer / annual_total * 100.0
        summary["employer_stock_contribution_pct"] = round(employer_cash_pct, 2)
        summary["non_employer_contribution_pct"] = round(100.0 - employer_cash_pct, 2)
        discount_pct = _normalize_percentage_value(summary.get("hd_espp_discount_pct"), default_pct=float("nan"))
        current_pct = _coerce_finite_number(employer.get("primary_employer_capital_concentration_pct"))
        if math.isfinite(discount_pct) and 0.0 <= discount_pct < 100.0 and current_pct is not None and 0.0 <= current_pct <= 100.0:
            estimated_espp_market_value = annual_employer / (1.0 - discount_pct / 100.0)
            estimated_new_market_value = annual_regular + estimated_espp_market_value
            if estimated_new_market_value > 0:
                employer_new_money_weight = estimated_espp_market_value / estimated_new_market_value * 100.0
                summary["concentration_direction"] = (
                    "increase employer-stock concentration"
                    if employer_new_money_weight > current_pct
                    else "dilute employer-stock concentration"
                    if employer_new_money_weight < current_pct
                    else "neutral"
                )
            else:
                summary["concentration_direction"] = "indeterminate"
        else:
            summary["concentration_direction"] = "indeterminate"
    else:
        summary["concentration_direction"] = "indeterminate"

    return _strip_empty_context_values(compact_payload) if isinstance(_strip_empty_context_values(compact_payload), dict) else compact_payload


def _build_ai_analysis_payload_with_diagnostics(
    holdings: pd.DataFrame,
    metrics: pd.DataFrame,
    analysis_type: str,
    custom_question: str,
) -> tuple[dict[str, object], dict[str, object]]:
    full_payload = _build_ai_export_payload(
        holdings,
        metrics,
        biweekly_contribution=float(st.session_state.get("analysis_biweekly_contribution", DEFAULT_PROJECTION_BIWEEKLY_CONTRIB)),
        projection_return_pct=float(st.session_state.get("analysis_projection_return", 8.0)),
        espp_contrib_biweekly=float(st.session_state.get("analysis_espp_contrib_biweekly", ESPP_CONTRIB_BIWEEKLY)),
        espp_purchase_frequency=str(st.session_state.get("analysis_espp_purchase_frequency", ESPP_PURCHASE_FREQUENCY)),
        espp_discount=float(st.session_state.get("analysis_espp_discount", ESPP_DISCOUNT)),
    )

    try:
        portfolio_history_summary = _build_compact_portfolio_history_summary(load_performance_history(STATEMENT_HISTORY_DB_PATH))
    except (OSError, sqlite3.Error):
        portfolio_history_summary = {}
    seasonality_summary: dict[str, object] = {}
    selected_type = str(analysis_type or "")
    if selected_type in {"Comprehensive Portfolio Review", "Income and Dividend Analysis"}:
        tickers = _to_ticker_tuple(metrics.get("ticker", pd.Series(dtype=str))) if not metrics.empty else ()
        weights = _latest_dividend_month_weights(tickers) if tickers else {}
        seasonality_summary = _build_compact_seasonality_summary(_build_monthly_income_seasonality(metrics, weights))
    compact_payload = _compact_analysis_context(
        full_payload,
        analysis_type,
        custom_question,
        portfolio_history_summary=portfolio_history_summary,
        dividend_seasonality_summary=seasonality_summary,
    )

    before_text = json.dumps(_sanitize_export_value(full_payload), default=str, separators=(",", ":"), allow_nan=False)
    after_text = json.dumps(_sanitize_export_value(compact_payload), default=str, separators=(",", ":"), allow_nan=False)
    before_chars = len(before_text)
    after_chars = len(after_text)
    reduction_pct = ((before_chars - after_chars) / before_chars * 100.0) if before_chars > 0 else 0.0
    diagnostics = {
        "before_chars": before_chars,
        "before_tokens": _estimated_tokens_from_chars(before_chars),
        "after_chars": after_chars,
        "after_tokens": _estimated_tokens_from_chars(after_chars),
        "reduction_pct": round(reduction_pct, 1),
    }
    return compact_payload, diagnostics


def _build_ai_analysis_request_payload(
    holdings: pd.DataFrame,
    metrics: pd.DataFrame,
    analysis_type: str,
    custom_question: str,
) -> dict[str, object]:
    payload, _ = _build_ai_analysis_payload_with_diagnostics(holdings, metrics, analysis_type, custom_question)
    return payload


def _format_onemin_ai_response_diagnostic(diagnostic: object) -> str:
    if not isinstance(diagnostic, dict) or not diagnostic:
        return "No additional response-shape diagnostics were available."

    lines = ["Sanitized 1min.AI response diagnostic:"]
    if "http_status" in diagnostic:
        lines.append(f"- HTTP status: {diagnostic.get('http_status')}")
    if diagnostic.get("provider_error_code"):
        lines.append(f"- Provider error code: {diagnostic.get('provider_error_code')}")
    if diagnostic.get("provider_error_message"):
        lines.append(f"- Provider error message: {diagnostic.get('provider_error_message')}")
    if isinstance(diagnostic.get("provider_validation_details"), list):
        for detail in diagnostic["provider_validation_details"]:
            if isinstance(detail, dict) and detail.get("field") and detail.get("message"):
                lines.append(f"- Validation: {detail.get('field')}: {detail.get('message')}")
    if "record_status" in diagnostic:
        lines.append(f"- API record status: {diagnostic.get('record_status')}")
    if diagnostic.get("record_id"):
        lines.append(f"- Record ID: {diagnostic.get('record_id')}")
    if diagnostic.get("requested_model"):
        lines.append(f"- Requested model: {diagnostic.get('requested_model')}")
    if diagnostic.get("resolved_model"):
        lines.append(f"- Resolved model: {diagnostic.get('resolved_model')}")
    if "top_level_keys" in diagnostic:
        lines.append(f"- Top-level keys: {diagnostic.get('top_level_keys')}")
    if "ai_record_present" in diagnostic:
        lines.append(f"- aiRecord present: {bool(diagnostic.get('ai_record_present'))}")
    if "ai_record_detail_present" in diagnostic:
        lines.append(f"- aiRecordDetail present: {bool(diagnostic.get('ai_record_detail_present'))}")
    if "result_items" in diagnostic:
        lines.append(f"- Result items: {diagnostic.get('result_items')}")
    if "result_characters" in diagnostic:
        lines.append(f"- Final result characters: {diagnostic.get('result_characters')}")

    return "\n".join(lines)


def _supplemental_research_contexts(holdings: pd.DataFrame, metrics: pd.DataFrame) -> dict[str, dict[str, object]]:
    contexts: dict[str, dict[str, object]] = {}
    normalized = _normalize_columns(holdings, drop_blank_tickers=True)
    for _, holding in normalized.iterrows():
        ticker = str(holding.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        matching_metrics = metrics[metrics["ticker"].eq(ticker)] if "ticker" in metrics.columns else pd.DataFrame()
        metric = matching_metrics.iloc[0].to_dict() if not matching_metrics.empty else {}
        contexts[ticker] = {
            "ticker": ticker,
            "entity_name": ticker,
            "asset_type": str(holding.get("asset_type") or "Unknown"),
            "sector": str(holding.get("sector") or "Unknown"),
            "missing_fields": ["strategy_objective", "risk_characteristics", "recent_structural_developments"],
            "existing_yahoo_information": {
                "sector": holding.get("sector") or "",
                "asset_type": holding.get("asset_type") or "",
                "current_price": metric.get("current_price", ""),
                "dividend_yield_pct": metric.get("dividend_yield_pct", ""),
                "annual_div_per_share": holding.get("annual_div_per_share", ""),
            },
        }
    return contexts


def _render_supplemental_research(holdings: pd.DataFrame, metrics: pd.DataFrame) -> None:
    st.markdown("#### Supplemental Investment Research")
    st.caption("Yahoo Finance remains the primary source for prices, dividends, calculations, and classifications. This feature researches only missing descriptive context through 1min.AI web search.")
    contexts = _supplemental_research_contexts(holdings, metrics)
    service = SupplementalResearchService(DEFAULT_DB_PATH)
    service.sync_holdings(contexts.keys())
    tickers = sorted(contexts)
    if not tickers:
        st.info("Add a holding before requesting supplemental research.")
        return
    configured_model = validated_research_model(DEFAULT_DB_PATH)
    model_options = list(SUPPORTED_RESEARCH_MODELS)
    selected_model = st.selectbox("Supplemental Research Model", options=model_options, index=model_options.index(configured_model), format_func=_onemin_ai_model_label, key="supplemental_research_model_selector")
    if isinstance(selected_model, str) and selected_model in model_options and selected_model != configured_model:
        set_setting(DEFAULT_DB_PATH, "supplemental_research_model", selected_model)
        configured_model = selected_model
    selected_ticker = st.selectbox("Research Holding", options=tickers, key="supplemental_research_ticker")
    research_clicked = st.button("Research Selected Holding", key="supplemental_research_selected")
    due_clicked = st.button("Refresh Due Holdings", key="supplemental_research_due")
    force_confirmed = st.checkbox("I understand that refreshing all eligible holdings may use more credits.", key="supplemental_research_force_confirm")
    force_clicked = st.button("Force Refresh All Holdings", key="supplemental_research_force")
    if research_clicked:
        with st.spinner(f"Researching {selected_ticker} through 1min.AI..."):
            result = service.research_ticker(selected_ticker, contexts[selected_ticker], configured_model, manual=True, force=True)
        if result.get("status") == "current":
            st.success(f"Supplemental research completed for {selected_ticker}.")
        else:
            st.error(
                str(
                    result.get("error")
                    or result.get("reason")
                    or "Supplemental research did not complete."
                )
            )
    if due_clicked:
        results = service.refresh_due(contexts, configured_model)
        if results:
            st.info(f"Processed {len(results)} due holding(s).")
        else:
            st.info("No supplemental research is due.")
    if force_clicked:
        if not force_confirmed:
            st.warning("Confirm the credit-use warning before forcing a refresh.")
        else:
            results = service.refresh_due(contexts, configured_model, force=True)
            st.info(f"Processed {len(results)} eligible holding(s).")
    rows = get_research_rows(DEFAULT_DB_PATH, tickers)
    table = []
    for row in rows:
        parsed = json.loads(row.get("research_json") or "{}")
        sources = json.loads(row.get("sources_json") or "[]")
        status = str(row.get("research_status", "missing"))
        if status == "current" and research_is_due(row):
            status = "due"
        table.append({"Ticker": row["ticker"], "Status": status.title(), "Last Successful Research": row.get("last_success_at") or "Never", "Next Scheduled Refresh": row.get("next_due_at") or "Not scheduled", "Model Used": row.get("model_used") or "-", "Verified Facts": len(parsed.get("verified_facts", [])), "Sources": len(sources), "Last Error": str(row.get("error_message") or "")[:160]})
    st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
    selected_rows = [row for row in rows if row["ticker"] == selected_ticker]
    if selected_rows and selected_rows[0].get("research_markdown"):
        st.markdown(selected_rows[0]["research_markdown"])


def _run_onemin_ai_analysis(holdings: pd.DataFrame, metrics: pd.DataFrame) -> None:
    selected_model = str(st.session_state["ai_analysis_model"])
    selected_type = str(st.session_state["ai_analysis_type"])
    custom_question = str(st.session_state["ai_analysis_custom_question"])
    st.session_state["ai_analysis_busy"] = True
    st.session_state["ai_analysis_last_attempt_at"] = datetime.now().isoformat(timespec="seconds")
    st.session_state["ai_analysis_last_stage"] = "preparing"
    st.session_state.pop("ai_analysis_error_message", None)
    st.session_state.pop("ai_analysis_error_detail", None)

    try:
        analysis_payload, context_stats = _build_ai_analysis_payload_with_diagnostics(
            holdings,
            metrics,
            selected_type,
            custom_question,
        )
        st.session_state["ai_analysis_context_diagnostics"] = context_stats
        messages = build_ai_analysis_messages(analysis_payload, selected_type, custom_question)
        prompt = messages_to_prompt(messages)
        client = OneMinAIClient(
            api_key=os.environ.get("ONEMIN_AI_API_KEY"),
            base_url=_onemin_ai_base_url(),
            timeout_seconds=_onemin_ai_timeout_seconds(),
            connect_timeout_seconds=ONEMIN_AI_DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        st.session_state["ai_analysis_last_stage"] = "requesting"
        with st.spinner("Analyzing portfolio through 1min.AI..."):
            result = client.chat_with_ai(model=selected_model, prompt=prompt)

        analysis_text = getattr(result, "content", "")
        if not isinstance(analysis_text, str) or not analysis_text.strip():
            raise OneMinAIResponseError("1min.AI returned an empty analysis result.")

        response_diagnostic = getattr(result, "response_diagnostic", {})
        st.session_state["ai_analysis_result_markdown"] = format_ai_analysis_markdown(analysis_text.strip())
        st.session_state["ai_analysis_response_diagnostic"] = response_diagnostic if isinstance(response_diagnostic, dict) else {}
        st.session_state["ai_analysis_result_model"] = str(getattr(result, "model", selected_model) or selected_model)
        st.session_state["ai_analysis_result_type"] = selected_type
        st.session_state["ai_analysis_generated_at"] = datetime.now().isoformat(timespec="seconds")
        st.session_state["ai_analysis_last_stage"] = "success"
        st.session_state.pop("ai_analysis_error_message", None)
        st.session_state.pop("ai_analysis_error_detail", None)
    except OneMinAIConfigError as exc:
        st.session_state["ai_analysis_error_message"] = "Set ONEMIN_AI_API_KEY in your local environment to run AI Analysis."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIAuthenticationError as exc:
        st.session_state["ai_analysis_error_message"] = "1min.AI rejected the API key. Check ONEMIN_AI_API_KEY and try again."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIInsufficientCreditsError as exc:
        st.session_state["ai_analysis_error_message"] = "1min.AI reports insufficient credits."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIRateLimitError as exc:
        st.session_state["ai_analysis_error_message"] = "1min.AI rate limited the request. Try again later."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIUnsupportedModelError as exc:
        st.session_state["ai_analysis_error_message"] = "The selected 1min.AI model is not supported. Choose another model."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAITimeoutError as exc:
        st.session_state["ai_analysis_error_message"] = "The 1min.AI request timed out. Try again or reduce the analysis context."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIConnectionError as exc:
        st.session_state["ai_analysis_error_message"] = "Could not reach 1min.AI. Check your connection or base URL."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIResponseError as exc:
        st.session_state["ai_analysis_error_message"] = "1min.AI returned an invalid or empty response."
        diagnostic_text = _format_onemin_ai_response_diagnostic(getattr(exc, "diagnostic", {}))
        st.session_state["ai_analysis_error_detail"] = f"{str(exc)}\n\n{diagnostic_text}" if diagnostic_text else str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except OneMinAIError as exc:
        st.session_state["ai_analysis_error_message"] = "1min.AI provider failure."
        st.session_state["ai_analysis_error_detail"] = str(exc)
        st.session_state["ai_analysis_last_stage"] = "error"
    except Exception:
        st.session_state["ai_analysis_error_message"] = "Unexpected error while running AI Analysis."
        st.session_state["ai_analysis_error_detail"] = "No additional provider diagnostics were available."
        st.session_state["ai_analysis_last_stage"] = "error"
    finally:
        st.session_state["ai_analysis_busy"] = False

def _render_ai_analysis_tab(holdings: pd.DataFrame, metrics: pd.DataFrame, mobile_mode: bool = False) -> None:
    if not _ai_analysis_enabled():
        return

    st.subheader("AI Analysis")
    st.caption("This analysis is generated through 1min.AI from portfolio data already loaded in memory. No export file is required.")

    if not _onemin_ai_has_api_key():
        st.info("Set ONEMIN_AI_API_KEY in your local environment to enable AI Analysis.")

    default_model = _onemin_ai_default_model()
    default_analysis_type = str(st.session_state.get("ai_analysis_type", AI_ANALYSIS_TYPES[0]))

    model_options = _onemin_ai_model_options(default_model)
    selected_model = st.selectbox(
        "Model",
        options=model_options,
        index=_onemin_ai_model_index(str(st.session_state.get("ai_analysis_model", default_model))),
        key="ai_analysis_model",
        format_func=_onemin_ai_model_label,
    )
    st.caption(_onemin_ai_model_description(selected_model))
    st.selectbox(
        "Analysis Type",
        options=AI_ANALYSIS_TYPES,
        index=_analysis_type_index(default_analysis_type),
        key="ai_analysis_type",
    )
    st.text_area(
        "Custom Question or Additional Instructions",
        value=str(st.session_state.get("ai_analysis_custom_question", "")),
        height=120,
        key="ai_analysis_custom_question",
        placeholder="Optional: add a custom question or any extra guidance for the model.",
    )

    action_col, clear_col = st.columns(2)
    active_request = bool(st.session_state.get("ai_analysis_busy", False)) and str(st.session_state.get("ai_analysis_last_stage", "")) in {"preparing", "requesting"}
    analyze_disabled = not _onemin_ai_has_api_key() or active_request
    action_col.button(
        "Analyze Portfolio",
        type="primary",
        disabled=analyze_disabled,
        on_click=_run_onemin_ai_analysis,
        args=(holdings.copy(deep=True), metrics.copy(deep=True)),
    )
    clear_clicked = clear_col.button("Clear Displayed Analysis")

    existing_context_stats = st.session_state.get("ai_analysis_context_diagnostics")
    if isinstance(existing_context_stats, dict):
        st.caption(
            "AI context size: "
            + f"{int(existing_context_stats.get('after_chars', 0)):,} chars (~{int(existing_context_stats.get('after_tokens', 0)):,} tokens) "
            + f"after compaction, from {int(existing_context_stats.get('before_chars', 0)):,} chars "
            + f"(~{int(existing_context_stats.get('before_tokens', 0)):,} tokens), "
            + f"{float(existing_context_stats.get('reduction_pct', 0.0)):.1f}% smaller."
        )

    if clear_clicked:
        st.session_state.pop("ai_analysis_busy", None)
        st.session_state.pop("ai_analysis_last_attempt_at", None)
        st.session_state.pop("ai_analysis_last_stage", None)
        st.session_state.pop("ai_analysis_result_markdown", None)
        st.session_state.pop("ai_analysis_response_diagnostic", None)
        st.session_state.pop("ai_analysis_result_model", None)
        st.session_state.pop("ai_analysis_result_type", None)
        st.session_state.pop("ai_analysis_error_message", None)
        st.session_state.pop("ai_analysis_error_detail", None)
        st.session_state.pop("ai_analysis_generated_at", None)
        st.session_state.pop("ai_analysis_context_diagnostics", None)
        st.rerun()

    error_message = st.session_state.get("ai_analysis_error_message")
    error_detail = st.session_state.get("ai_analysis_error_detail")
    if error_message:
        st.error(error_message)
        if error_detail:
            with st.expander("Details", expanded=False):
                st.caption(str(error_detail))

    result_markdown = st.session_state.get("ai_analysis_result_markdown")
    if result_markdown:
        st.markdown("#### Latest Analysis")
        st.markdown(result_markdown)
        download_model = str(st.session_state.get("ai_analysis_result_model", default_model))
        download_type = str(st.session_state.get("ai_analysis_result_type", AI_ANALYSIS_TYPES[0]))
        generated_at = str(st.session_state.get("ai_analysis_generated_at", datetime.now().isoformat(timespec="seconds")))
        response_diag = st.session_state.get("ai_analysis_response_diagnostic", {})
        if isinstance(response_diag, dict) and response_diag:
            with st.expander("Response Shape Diagnostics (Sanitized)", expanded=False):
                st.caption(_format_onemin_ai_response_diagnostic(response_diag))
        download_markdown = "\n".join(
            [
                "# 1min.AI Portfolio Analysis",
                f"- Generated: {generated_at}",
                f"- Model: {download_model}",
                f"- Analysis Type: {download_type}",
                "",
                result_markdown,
            ]
        )
        st.download_button(
            label="Download Analysis as Markdown",
            data=download_markdown.encode("utf-8"),
            file_name="ai_analysis.md",
            mime="text/markdown",
        )

    _render_supplemental_research(holdings, metrics)


def _render_health_tab(metrics: pd.DataFrame, mobile_mode: bool = False) -> None:
    st.subheader("Portfolio Health")
    st.caption("Minimal dashboard for health, alerts, goals, and quick filtered views.")

    health_df = metrics.copy()
    total_value = float(health_df["market_value"].sum())
    total_income = float(health_df["annual_income"].sum())
    weighted_yield = (total_income / total_value * 100) if total_value > 0 else 0.0
    health_package = _build_portfolio_health(health_df)
    health_score = float(
        health_package.get(
            "overall_score",
            health_package.get("health_score", 0.0),
        ) or 0.0
    )
    alerts = [str(alert) for alert in health_package.get("alerts", [])]

    st.markdown("#### Health Score")
    score_col, details_col = st.columns([1, 2]) if not mobile_mode else st.columns([1, 1])
    with score_col:
        st.metric("Portfolio Health", f"{health_score:,.1f}/100")
    with details_col:
        st.write("Score uses the current holdings snapshot and the same health engine used by your exports.")
    st.caption(
        "This score is a rule-based health estimate, not a market benchmark. It updates from the current holdings snapshot and may reflect manual holding fields such as sector, asset type, ESPP, and dividend inputs where they are provided."
    )

    st.markdown("#### One-Line Alerts")
    if alerts:
        st.info(" | ".join(alerts))
    else:
        st.success("No immediate health alerts detected.")

    st.markdown("#### Goal Tracker Card")
    target_income = st.number_input(
        "Target Annual Income",
        min_value=0.0,
        value=12000.0,
        step=100.0,
        format="%.2f",
        key="health_target_annual_income",
    )
    progress_pct = (total_income / target_income * 100) if target_income > 0 else 0.0
    progress_pct_clamped = max(0.0, min(100.0, progress_pct))

    biweekly_contribution = float(st.session_state.get("analysis_biweekly_contribution", 280.0))
    annual_contribution = biweekly_contribution * 26
    annual_income_growth = annual_contribution * (weighted_yield / 100)
    years_to_goal = _years_to_income_target(total_income, target_income, annual_income_growth)
    years_text = "Reached" if target_income <= total_income else ("N/A" if years_to_goal == float("inf") else f"{years_to_goal:,.1f}")

    if mobile_mode:
        g1, g2 = st.columns(2)
        g1.metric("Current Income", _format_currency(total_income))
        g2.metric("Goal Progress", _format_percent(progress_pct))
        st.metric("Est. Years To Goal", years_text)
    else:
        g1, g2, g3 = st.columns(3)
        g1.metric("Current Annual Income", _format_currency(total_income))
        g2.metric("Goal Progress", _format_percent(progress_pct))
        g3.metric("Est. Years To Goal", years_text)
    st.progress(progress_pct_clamped / 100)
    st.caption(
        "Goal progress is based on your selected target income and the bi-weekly contribution setting from Portfolio Analysis. The years-to-goal estimate changes when those inputs or the portfolio yield change."
    )

    st.markdown("#### Quick Filters")
    if mobile_mode:
        asset_filter = st.selectbox(
            "Asset Type",
            options=["All", "Stock", "ETF", "REIT", "CEF", "BDC", "Bond Fund", "Other"],
            key="health_filter_asset_type",
        )
        min_yield = st.number_input(
            "Min Yield (%)",
            min_value=0.0,
            max_value=25.0,
            value=0.0,
            step=0.5,
            format="%.2f",
            key="health_filter_min_yield",
        )
        top_n = st.number_input(
            "Top N by Income",
            min_value=1,
            max_value=max(len(health_df), 1),
            value=min(max(len(health_df), 1), 5),
            step=1,
            key="health_filter_top_n",
        )
    else:
        f1, f2, f3 = st.columns(3)
        with f1:
            asset_filter = st.selectbox(
                "Asset Type",
                options=["All", "Stock", "ETF", "REIT", "CEF", "BDC", "Bond Fund", "Other"],
                key="health_filter_asset_type",
            )
        with f2:
            min_yield = st.number_input(
                "Min Yield (%)",
                min_value=0.0,
                max_value=25.0,
                value=0.0,
                step=0.5,
                format="%.2f",
                key="health_filter_min_yield",
            )
        with f3:
            top_n = st.number_input(
                "Top N by Income",
                min_value=1,
                max_value=max(len(health_df), 1),
                value=min(max(len(health_df), 1), 5),
                step=1,
                key="health_filter_top_n",
            )

    filtered = health_df.copy()
    if asset_filter != "All":
        filtered = filtered[filtered["asset_type"].eq(asset_filter)]
    filtered = filtered[filtered["portfolio_yield_pct"] >= float(min_yield)]
    filtered = filtered.sort_values("annual_income", ascending=False).head(int(top_n))
    if "weight_pct" not in filtered.columns:
        total_value = float(filtered.get("market_value", 0.0).sum()) if not filtered.empty else 0.0
        filtered["weight_pct"] = (filtered["market_value"] / total_value * 100.0) if total_value > 0 else 0.0

    quick_cols = [
        "ticker",
        "asset_type",
        "market_value",
        "annual_income",
        "portfolio_yield_pct",
        "weight_pct",
        "unrealized_gain",
    ]
    st.dataframe(
        filtered[quick_cols].style.format(
            {
                "market_value": "${:,.2f}",
                "annual_income": "${:,.2f}",
                "portfolio_yield_pct": "{:.2f}%",
                "weight_pct": "{:.2f}%",
                "unrealized_gain": "${:,.2f}",
            }
        ),
        width="stretch",
        hide_index=True,
    )


def _build_insights_yield_summary(metrics: pd.DataFrame) -> dict[str, object]:
    if metrics.empty:
        return {
            "highest_yield_ticker": "N/A",
            "highest_yield_pct": 0.0,
            "lowest_positive_yield_ticker": "N/A",
            "lowest_positive_yield_pct": 0.0,
            "yield_spread_pct": None,
        }

    analysis_df = metrics.copy()
    for column in ["market_value", "annual_income", "current_price", "shares", "espp_acquired_shares", "regular_biweekly_contribution", "espp_biweekly_contribution", "special_contribution"]:
        if column in analysis_df.columns:
            analysis_df[column] = pd.to_numeric(analysis_df[column], errors="coerce").fillna(0.0)
        else:
            analysis_df[column] = 0.0
    analysis_df["asset_type"] = analysis_df["asset_type"].apply(_canonical_asset_type) if "asset_type" in analysis_df.columns else "Other"
    analysis_df["holding_yield_pct"] = analysis_df.apply(_resolve_holding_yield_pct, axis=1)
    analysis_df["holding_yield_pct"] = pd.to_numeric(analysis_df["holding_yield_pct"], errors="coerce").fillna(0.0)

    highest_yield = _select_highest_valid_yield(analysis_df)
    lowest_positive_yield = _select_lowest_positive_yield(analysis_df)
    highest_yield_pct = float(highest_yield.get("yield_pct", 0.0) or 0.0)
    lowest_positive_yield_pct = float(lowest_positive_yield.get("yield_pct", 0.0) or 0.0)
    valid_positive_rows = [row for row in analysis_df.to_dict(orient="records") if _resolve_holding_yield_pct(row) > 0]
    yield_spread_pct = None
    if len(valid_positive_rows) >= 2:
        yield_spread_pct = highest_yield_pct - lowest_positive_yield_pct

    return {
        "highest_yield_ticker": str(highest_yield.get("ticker", "N/A")),
        "highest_yield_pct": highest_yield_pct,
        "lowest_positive_yield_ticker": str(lowest_positive_yield.get("ticker", "N/A")),
        "lowest_positive_yield_pct": lowest_positive_yield_pct,
        "yield_spread_pct": yield_spread_pct,
    }


def _build_insights_contribution_view(
    metrics: pd.DataFrame,
    biweekly_contribution: float,
    espp_contrib_biweekly: float,
    espp_purchase_frequency: str = ESPP_PURCHASE_FREQUENCY,
    espp_discount: float = ESPP_DISCOUNT,
) -> dict[str, object]:
    contribution_context = _build_contribution_context(
        metrics.copy(),
        metrics.copy(),
        biweekly_contribution,
        espp_contrib_biweekly,
        espp_purchase_frequency,
        espp_discount,
    )
    contribution_rows = contribution_context.get("holdings", [])
    if not contribution_rows:
        return {
            "rows": pd.DataFrame(columns=["ticker", "regular_contribution", "espp_contribution", "special_contribution", "total_contribution"]),
            "summary": {
                "regular_biweekly": 0.0,
                "espp_biweekly": 0.0,
                "special_biweekly": 0.0,
                "combined_biweekly": 0.0,
                "regular_annual": 0.0,
                "espp_annual": 0.0,
                "combined_annual": 0.0,
            },
            "active_contributors": 0,
        }

    rows_df = pd.DataFrame(contribution_rows)
    rows_df = rows_df[
        [
            "ticker",
            "regular_biweekly_contribution",
            "espp_biweekly_contribution",
            "special_contribution",
            "total_contribution",
        ]
    ].copy()
    rows_df = rows_df.rename(
        columns={
            "regular_biweekly_contribution": "regular_contribution",
            "espp_biweekly_contribution": "espp_contribution",
            "special_contribution": "special_contribution",
            "total_contribution": "total_contribution",
        }
    )
    rows_df = rows_df.fillna(0.0)
    summary = _summarize_contribution_totals(contribution_rows)
    active_contributors = int(sum(1 for row in contribution_rows if float(row.get("total_contribution", 0.0) or 0.0) > 0))
    return {
        "rows": rows_df,
        "summary": summary,
        "active_contributors": active_contributors,
    }


def _build_insights_asset_allocation(metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame(columns=["asset_type", "market_value", "allocation_pct"])

    allocation_df = metrics.copy()
    allocation_df["market_value"] = pd.to_numeric(allocation_df.get("market_value", 0.0), errors="coerce").fillna(0.0)
    allocation_df["asset_type_input"] = allocation_df.get("asset_type", "")
    allocation_df["asset_type"] = allocation_df["asset_type_input"].apply(_normalize_insights_asset_type)
    allocation_df = allocation_df.groupby("asset_type", dropna=False).agg(market_value=("market_value", "sum")).reset_index()
    if allocation_df.empty:
        return pd.DataFrame(columns=["asset_type", "market_value", "allocation_pct"])

    total_market_value = float(allocation_df["market_value"].sum()) if not allocation_df.empty else 0.0
    if total_market_value > 0:
        allocation_df["allocation_pct"] = allocation_df["market_value"] / total_market_value * 100.0
    else:
        allocation_df["allocation_pct"] = 0.0
    return allocation_df.sort_values("market_value", ascending=False).reset_index(drop=True)


def _normalize_insights_asset_type(value: object) -> str:
    raw_text = str(value or "").strip().lower()
    if raw_text in {"", "nan", "none", "unknown", "unassigned"}:
        return "Other/Unassigned"

    canonical = _canonical_asset_type(value)
    if canonical == "ETF":
        return "ETF/Fund"
    if canonical == "REIT":
        return "REIT"
    if canonical == "Bond Fund" or raw_text in {"cef", "cefs", "bdc", "bdcs", "bond", "bond fund", "bond funds", "fixed income", "fixed-income", "treasury"}:
        return "Bond/Fixed Income"
    if canonical == "Stock":
        return "Stock"
    if raw_text in {"cash", "cash equivalent", "cash/cash equivalent", "treasury", "money market"}:
        return "Cash/Cash Equivalent"
    return "Other/Unassigned"


def _resolve_insights_income_goal_state(target_annual_income: object, current_annual_income: object) -> dict[str, object]:
    try:
        parsed_target = float(target_annual_income) if target_annual_income not in [None, "", "None"] else None
    except (TypeError, ValueError):
        parsed_target = None

    try:
        parsed_current = float(current_annual_income) if current_annual_income not in [None, "", "None"] else 0.0
    except (TypeError, ValueError):
        parsed_current = 0.0

    if parsed_target is None or parsed_target <= 0:
        return {
            "configured": False,
            "target_annual_income": 0.0,
            "progress_pct": 0.0,
            "message": "Set an income goal in Goal Tracker to display progress here.",
        }

    progress_pct = (parsed_current / parsed_target * 100.0) if parsed_target > 0 else 0.0
    progress_pct = max(0.0, min(100.0, progress_pct))
    return {
        "configured": True,
        "target_annual_income": parsed_target,
        "current_annual_income": parsed_current,
        "progress_pct": progress_pct,
        "message": "Goal progress uses the configured annual income target and the current portfolio income.",
    }


def _coerce_numeric_columns(frame: pd.DataFrame, columns: list[str], default_value: float = 0.0) -> pd.DataFrame:
    normalized = frame.copy()
    for column in columns:
        if column not in normalized.columns:
            normalized[column] = default_value
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(default_value)
    return normalized


def _render_insights_tab(metrics: pd.DataFrame, mobile_mode: bool = False) -> None:
    st.subheader("Portfolio Insights & Trends")

    insights = metrics.copy()
    insights["asset_type"] = insights["asset_type"].apply(_canonical_asset_type) if "asset_type" in insights.columns else "Other"
    insights["market_value"] = pd.to_numeric(insights.get("market_value", 0.0), errors="coerce").fillna(0.0)
    insights["annual_income"] = pd.to_numeric(insights.get("annual_income", 0.0), errors="coerce").fillna(0.0)
    insights["current_value"] = pd.to_numeric(insights.get("current_value", insights.get("market_value", 0.0)), errors="coerce").fillna(0.0)
    total_value = float(insights["market_value"].sum())
    total_income = float(insights["annual_income"].sum())
    average_monthly_income = total_income / 12 if total_income > 0 else 0.0
    weighted_yield = (total_income / total_value * 100) if total_value > 0 else 0.0

    biweekly_contribution = float(st.session_state.get("analysis_biweekly_contribution", 280.0))
    espp_contrib_biweekly = float(st.session_state.get("analysis_espp_contrib_biweekly", ESPP_CONTRIB_BIWEEKLY))
    espp_purchase_frequency = str(st.session_state.get("analysis_espp_purchase_frequency", ESPP_PURCHASE_FREQUENCY))
    espp_discount = float(st.session_state.get("analysis_espp_discount", ESPP_DISCOUNT))
    annual_contribution = biweekly_contribution * 26
    projection_return_pct = float(st.session_state.get("analysis_projection_return", 8.0))
    value_10y = _future_value_with_contributions(total_value, projection_return_pct, biweekly_contribution, 10)
    value_20y = _future_value_with_contributions(total_value, projection_return_pct, biweekly_contribution, 20)

    insights = insights.sort_values("market_value", ascending=False).reset_index(drop=True)
    insights["portfolio_weight_pct"] = (insights["market_value"] / total_value * 100) if total_value > 0 else 0.0
    insights["holding_yield_pct"] = insights.apply(_resolve_holding_yield_pct, axis=1)
    insights["holding_yield_pct"] = pd.to_numeric(insights["holding_yield_pct"], errors="coerce").fillna(0.0)

    contribution_view = _build_insights_contribution_view(insights, biweekly_contribution, espp_contrib_biweekly, espp_purchase_frequency, espp_discount)
    contribution_rows = contribution_view["rows"]
    contribution_summary = contribution_view["summary"]
    active_holders = int(contribution_view.get("active_contributors", 0) or 0)

    insights = insights.merge(contribution_rows, on="ticker", how="left")
    insights = _coerce_numeric_columns(
        insights,
        ["regular_contribution", "espp_contribution", "special_contribution", "total_contribution"],
    )
    insights["annual_income_est"] = insights["annual_income"]
    insights["monthly_income_est"] = insights["annual_income_est"] / 12

    top_position = insights.head(1)
    yield_summary = _build_insights_yield_summary(insights)
    high_name = yield_summary.get("highest_yield_ticker", "N/A")
    high_pct = float(yield_summary.get("highest_yield_pct", 0.0) or 0.0)
    low_name = yield_summary.get("lowest_positive_yield_ticker", "N/A")
    low_pct = float(yield_summary.get("lowest_positive_yield_pct", 0.0) or 0.0)
    yield_spread = float(yield_summary.get("yield_spread_pct") or 0.0)

    st.markdown("#### Key Portfolio Metrics")
    key_metrics = pd.DataFrame(
        [
            {"Metric": "Total Portfolio Value", "Value": _format_currency(total_value)},
            {"Metric": "Total Annual Income", "Value": _format_currency(total_income)},
            {"Metric": "Weighted Avg Yield", "Value": _format_percent(weighted_yield)},
            {"Metric": "10-Year Projection", "Value": _format_currency(value_10y)},
            {"Metric": "20-Year Projection", "Value": _format_currency(value_20y)},
        ]
    )
    st.dataframe(key_metrics, width="stretch", hide_index=True)
    st.caption(
        "Dividend yield comparisons use the canonical holding-yield helper. Contribution allocations come from the saved per-holding contribution values rather than market-value estimates."
    )

    if mobile_mode:
        st.markdown("#### Holdings By Current Value")
        st.dataframe(
            insights[["ticker", "current_value"]].rename(columns={"ticker": "Investment", "current_value": "Current Value ($)"}),
            width="stretch",
            hide_index=True,
        )
        st.markdown("#### Yield Comparison By Holding")
        yield_table = insights[["ticker", "holding_yield_pct"]].rename(columns={"ticker": "Investment", "holding_yield_pct": "Dividend Yield (%)"})
        st.dataframe(yield_table.style.format({"Dividend Yield (%)": "{:.2f}%"}), width="stretch", hide_index=True)
    else:
        left_table, right_table = st.columns(2)
        with left_table:
            st.markdown("#### Holdings By Current Value")
            st.dataframe(
                insights[["ticker", "current_value"]].rename(columns={"ticker": "Investment", "current_value": "Current Value ($)"}),
                width="stretch",
                hide_index=True,
            )

        with right_table:
            st.markdown("#### Yield Comparison By Holding")
            yield_table = insights[["ticker", "holding_yield_pct"]].rename(columns={"ticker": "Investment", "holding_yield_pct": "Dividend Yield (%)"})
            st.dataframe(yield_table.style.format({"Dividend Yield (%)": "{:.2f}%"}), width="stretch", hide_index=True)

    asset_allocation = _build_insights_asset_allocation(insights)
    contribution_chart = contribution_rows[["ticker", "total_contribution"]].rename(columns={"ticker": "Investment", "total_contribution": "Biweekly Contribution ($)"})
    contribution_view_table = insights[["ticker", "regular_contribution", "espp_contribution", "special_contribution", "total_contribution"]].rename(
        columns={
            "ticker": "Investment",
            "regular_contribution": "Regular Contribution ($)",
            "espp_contribution": "ESPP Contribution ($)",
            "special_contribution": "Special Contribution ($)",
            "total_contribution": "Total Contribution ($)",
        }
    )

    if mobile_mode:
        st.markdown("#### Asset Allocation")
        st.caption("Allocation by the resolved asset type of each holding.")
        st.dataframe(asset_allocation, width="stretch", hide_index=True)
        if asset_allocation["market_value"].sum() > 0:
            st.bar_chart(asset_allocation.set_index("asset_type")[["market_value"]])
        st.markdown("#### Contribution Allocations")
        st.dataframe(
            contribution_view_table.style.format(
                {
                    "Regular Contribution ($)": "${:,.2f}",
                    "ESPP Contribution ($)": "${:,.2f}",
                    "Special Contribution ($)": "${:,.2f}",
                    "Total Contribution ($)": "${:,.2f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        left_mix, right_mix = st.columns(2)
        with left_mix:
            st.markdown("#### Asset Allocation")
            st.caption("Allocation by the resolved asset type of each holding.")
            st.dataframe(asset_allocation, width="stretch", hide_index=True)
            if asset_allocation["market_value"].sum() > 0:
                st.bar_chart(asset_allocation.set_index("asset_type")[["market_value"]])

        with right_mix:
            st.markdown("#### Contribution Allocations")
            st.dataframe(
                contribution_view_table.style.format(
                    {
                        "Regular Contribution ($)": "${:,.2f}",
                        "ESPP Contribution ($)": "${:,.2f}",
                        "Special Contribution ($)": "${:,.2f}",
                        "Total Contribution ($)": "${:,.2f}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

    if mobile_mode:
        st.markdown("#### Portfolio Value By Holding")
        if not insights.empty:
            st.bar_chart(insights.set_index("ticker")[["current_value"]])
        st.markdown("#### Contributions By Holding")
        if not contribution_chart.empty:
            st.bar_chart(contribution_chart.set_index("Investment")[["Biweekly Contribution ($)"]])
    else:
        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.markdown("#### Portfolio Value By Holding")
            if not insights.empty:
                st.bar_chart(insights.set_index("ticker")[["current_value"]])
        with chart_col2:
            st.markdown("#### Contributions By Holding")
            if not contribution_chart.empty:
                st.bar_chart(contribution_chart.set_index("Investment")[["Biweekly Contribution ($)"]])

    st.markdown("#### Key Insights & Trends (Auto-Updating)")
    top_name = top_position.iloc[0]["ticker"] if not top_position.empty else "N/A"
    top_weight = float(top_position.iloc[0]["portfolio_weight_pct"]) if not top_position.empty else 0.0
    top3_income_pct = (
        float(insights.sort_values("annual_income_est", ascending=False).head(3)["annual_income_est"].sum()) / total_income * 100
        if total_income > 0
        else 0.0
    )

    st.write(f"- Portfolio concentration: Largest holding is **{top_name}** at {_format_percent(top_weight)} of portfolio value.")
    st.write(f"- Highest dividend yield: **{high_name}** at {_format_percent(high_pct)}.")
    if yield_spread is None:
        st.write("- Dividend yield spread: Unavailable (fewer than two positive-yield holdings).")
    else:
        st.write(f"- Dividend yield spread: {yield_spread:.2f} percentage points.")
    st.write(f"- Income concentration: Top 3 holdings generate {_format_percent(top3_income_pct)} of annual income.")
    st.write(f"- Contribution profile: {active_holders} holdings receiving biweekly contributions.")
    st.write(
        f"- Growth outlook: 10-year projected value {_format_currency(value_10y)}, "
        f"20-year projected value {_format_currency(value_20y)}."
    )

    st.markdown("#### Updated Portfolio Analysis - Current Snapshot")
    goal_state = _resolve_insights_income_goal_state(
        st.session_state.get("health_target_annual_income"),
        total_income,
    )
    snapshot_rows = [
        {
            "Area": "Portfolio Size",
            "Current Readout": _format_currency(total_value),
            "What it means": "Current market value across all holdings.",
            "Watch / Consider": "Keep contributions consistent.",
        },
        {
            "Area": "Current Annual Income",
            "Current Readout": _format_currency(total_income),
            "What it means": "Estimated yearly dividend income.",
            "Watch / Consider": "Track income growth over time.",
        },
        {
            "Area": "Est. Average Monthly Dividend Income",
            "Current Readout": _format_currency(average_monthly_income),
            "What it means": "Estimated annual portfolio income divided by 12; actual payment timing may vary.",
            "Watch / Consider": "Compare with your average monthly cash needs.",
        },
        {
            "Area": "Weighted Yield",
            "Current Readout": _format_percent(weighted_yield),
            "What it means": "Income return on current market value.",
            "Watch / Consider": "Balance yield with diversification.",
        },
        {
            "Area": "10-Year Projection",
            "Current Readout": _format_currency(value_10y),
            "What it means": "Projected portfolio value with regular contributions only; ESPP payroll contributions are excluded.",
            "Watch / Consider": "Projection depends on return and contribution assumptions.",
        },
    ]
    st.dataframe(pd.DataFrame(snapshot_rows), width="stretch", hide_index=True)

    st.markdown("#### Contribution Summary")
    contribution_summary_rows = [
        {"Metric": "Regular Biweekly Contribution", "Value": _format_currency(contribution_summary.get("regular_biweekly", 0.0))},
        {"Metric": "ESPP Biweekly Contribution", "Value": _format_currency(contribution_summary.get("espp_biweekly", 0.0))},
        {"Metric": "Combined Biweekly Contribution", "Value": _format_currency(contribution_summary.get("combined_biweekly", 0.0))},
        {"Metric": "Regular Annual Contribution", "Value": _format_currency(contribution_summary.get("regular_annual", 0.0))},
        {"Metric": "ESPP Annual Contribution", "Value": _format_currency(contribution_summary.get("espp_annual", 0.0))},
        {"Metric": "Combined Annual Contribution", "Value": _format_currency(contribution_summary.get("combined_annual", 0.0))},
    ]
    st.dataframe(pd.DataFrame(contribution_summary_rows), width="stretch", hide_index=True)

    st.markdown("#### Growth Projections")
    growth_rows = [
        {"Metric": "10-Year Growth (from current)", "Value": _format_percent(((value_10y / total_value) - 1) * 100 if total_value > 0 else 0.0)},
        {"Metric": "20-Year Growth (from current)", "Value": _format_percent(((value_20y / total_value) - 1) * 100 if total_value > 0 else 0.0)},
        {"Metric": "Projection Assumption", "Value": "Regular contributions only; ESPP payroll contributions excluded"},
    ]
    st.dataframe(pd.DataFrame(growth_rows), width="stretch", hide_index=True)
    st.caption("Projections use regular portfolio contributions only and exclude ESPP payroll contributions unless explicitly stated otherwise.")

    st.markdown("#### Goal Tracker Context")
    if goal_state["configured"]:
        st.write(f"Configured income goal: {_format_currency(goal_state['target_annual_income'])} annual income.")
        st.progress(max(0.0, min(1.0, float(goal_state.get("progress_pct", 0.0)) / 100.0)))
    else:
        st.info(goal_state["message"])

    st.markdown("#### Portfolio Considerations")
    considerations = [
        f"Top concentration is {top_name} at {_format_percent(top_weight)} of portfolio value.",
        f"Highest holding yield is {high_name} at {_format_percent(high_pct)}.",
        f"The current contribution view uses resolved per-holding contribution values and excludes market-weight estimates.",
        f"The projected growth view uses regular contributions only and excludes ESPP payroll contributions.",
    ]
    for item in considerations:
        st.write(f"- {item}")


def _ai_analysis_enabled() -> bool:
    return bool(ENABLE_AI_ANALYSIS)


def _primary_tab_labels() -> list[str]:
    labels = ["Holdings", "Portfolio Analysis"]
    if _ai_analysis_enabled():
        labels.append("AI Analysis")
    labels.extend(["Portfolio Insights", "Portfolio Health"])
    return labels


def _normalize_active_tab(active_tab: object) -> str:
    if active_tab in {None, "", "Dividend Calendar", "dividend calendar", "dividend-calendar"}:
        return "Portfolio Analysis"

    if isinstance(active_tab, str):
        cleaned = active_tab.strip()
        enabled_tabs = _primary_tab_labels()
        if cleaned in enabled_tabs:
            return cleaned

        normalized_tabs = {label.lower(): label for label in enabled_tabs}
        normalized_value = normalized_tabs.get(cleaned.lower())
        if normalized_value:
            return normalized_value

    return "Portfolio Analysis"


def _build_monthly_income_seasonality(
    metrics: pd.DataFrame,
    month_weights_map: dict[str, list[float]] | None = None,
) -> dict[str, object]:
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    monthly_income = {month: 0.0 for month in month_names}
    covered_annual_income = 0.0
    uncovered_annual_income = 0.0

    if metrics is None or metrics.empty:
        return {
            "monthly_income": monthly_income,
            "covered_annual_income": 0.0,
            "uncovered_annual_income": 0.0,
            "coverage_pct": 0.0,
            "chart_available": False,
            "message": "Historical dividend timing is unavailable, so a monthly income estimate cannot be produced reliably.",
        }

    weights_map = month_weights_map or {}
    for _, row in metrics.iterrows():
        ticker = str(row.get("ticker", "")).strip().upper()
        annual_income = float(row.get("annual_income", row.get("annualized_income", 0.0)) or 0.0)
        if ticker == "" or annual_income <= 0:
            continue

        raw_weights = weights_map.get(ticker, []) if weights_map else []
        safe_weights: list[float] = []
        for value in raw_weights[:12]:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = 0.0
            if not pd.notna(parsed) or parsed < 0 or not (parsed == parsed and parsed != float("inf") and parsed != float("-inf")):
                parsed = 0.0
            safe_weights.append(parsed)

        if len(safe_weights) != 12:
            safe_weights = [0.0] * 12

        total_weight = sum(safe_weights)
        if total_weight <= 0:
            uncovered_annual_income += annual_income
            continue

        normalized_weights = [weight / total_weight for weight in safe_weights]
        for month_idx, month in enumerate(month_names):
            monthly_income[month] += annual_income * normalized_weights[month_idx]
        covered_annual_income += annual_income

    total_income = covered_annual_income + uncovered_annual_income
    coverage_pct = (covered_annual_income / total_income) if total_income > 0 else 0.0
    chart_available = covered_annual_income > 0.0 and any(value > 0.0 for value in monthly_income.values())

    return {
        "monthly_income": monthly_income,
        "covered_annual_income": covered_annual_income,
        "uncovered_annual_income": uncovered_annual_income,
        "coverage_pct": coverage_pct,
        "chart_available": chart_available,
        "message": (
            "Historical dividend timing is unavailable, so a monthly income estimate cannot be produced reliably."
            if not chart_available
            else "Estimated distribution of current annual portfolio income based on available historical dividend timing."
        ),
    }


def _render_last_updated_indicator() -> None:
    """Show the last refresh time so the user can see background updates are happening."""
    last_updated = st.session_state.get("last_market_data_update")
    if last_updated:
        st.caption(f"Last updated: {last_updated}")
    else:
        st.caption("Last updated: just now")


def _local_statement_status() -> tuple[int, bool]:
    try:
        return sum(1 for path in STATEMENTS_DIR.glob("*.pdf") if path.is_file()), STATEMENT_HISTORY_DB_PATH.exists()
    except OSError:
        return 0, STATEMENT_HISTORY_DB_PATH.exists()


def main() -> None:
    if not getattr(LOGGER, "_application_started_logged", False):
        log_event(LOGGER, "application_started", "Portfolio Tracker started")
        LOGGER._application_started_logged = True
    st.set_page_config(page_title="Dividend Portfolio Tracker", page_icon="📈", layout="wide")
    _initialize_export_state()
    st.session_state["last_market_data_update"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _reset_export_missing_field_warnings()
    mobile_mode = _detect_mobile_client()
    st.title("Dividend Portfolio Tracker")
    st.caption("Self-contained tracker: edit holdings locally and view analysis/insights in separate tabs.")
    _render_last_updated_indicator()

    with st.sidebar:
        st.subheader("Data")
        st.write(f"Storage file: `{DEFAULT_DB_PATH}`")
        statement_count, statement_db_available = _local_statement_status()
        st.caption("Local statement folder: Ready")
        st.caption(f"Statements detected: {statement_count}")
        st.caption(f"Statement history database: {'Available' if statement_db_available else 'Not created'}")
        st.toggle("Use Export JSON Data", key="use_export_json_data", value=False)
        st.toggle("Strict Export Accuracy Mode (100% export-aligned)", key="strict_export_accuracy_mode", value=False)
        st.caption(f"Export JSON path: {EXPORT_JSON_PATH}")
        if _use_export_json_data() and not isinstance(st.session_state.get("export_payload"), dict):
            st.warning("Export mode is enabled but export payload is unavailable. Falling back to local holdings + live market data.")
        elif _use_export_json_data() and not st.session_state.get("export_payload"):
            st.warning("Export mode is enabled but export JSON is empty. Falling back to local holdings + live market data.")
        if mobile_mode:
            st.caption("Compact mobile layouts are enabled.")
        if st.button("Force refresh all market data"):
            _latest_prices.clear()
            _latest_yahoo_metadata.clear()
            _latest_sectors.clear()
            _latest_dividend_yields.clear()
            _latest_asset_type_hints.clear()
            _latest_dividend_month_weights.clear()
            _latest_historical_context.clear()
            _latest_fund_sector_weights.clear()
            _latest_investment_intelligence.clear()
            log_event(LOGGER, "market_cache_cleared", "All Yahoo market-data caches cleared", cache_name="all_market_data", operation="clear")
            st.success("All market-data caches cleared. Data will be fetched fresh now.")
        if st.button("Force price refresh"):
            _latest_prices.clear()
            log_event(LOGGER, "market_cache_cleared", "Yahoo price cache cleared", cache_name="prices", operation="clear")
            st.success("Price cache cleared. Metrics will refresh now.")
        if st.button("Force sector refresh"):
            _latest_yahoo_metadata.clear()
            _latest_sectors.clear()
            _latest_investment_intelligence.clear()
            log_event(LOGGER, "market_cache_cleared", "Yahoo sector caches cleared", cache_name="metadata_and_sector", operation="clear")
            st.success("Sector cache cleared. Blank sectors will refill now.")
        if st.button("Force dividend refresh"):
            _latest_yahoo_metadata.clear()
            _latest_dividend_yields.clear()
            _latest_dividend_month_weights.clear()
            log_event(LOGGER, "market_cache_cleared", "Yahoo dividend caches cleared", cache_name="metadata_and_dividends", operation="clear")
            st.success("Dividend yield cache cleared. Dividend fields will refresh now.")
        if st.button("Force asset-type refresh"):
            _latest_yahoo_metadata.clear()
            _latest_asset_type_hints.clear()
            _latest_investment_intelligence.clear()
            log_event(LOGGER, "market_cache_cleared", "Yahoo asset-type caches cleared", cache_name="metadata_and_asset_type", operation="clear")
            st.success("Asset-type cache cleared. Default rows will be reclassified now.")
        if st.button("Reload holdings from disk"):
            st.session_state["holdings"] = _load_saved_holdings()
            st.rerun()


    if "holdings" not in st.session_state:
        st.session_state["holdings"] = _load_saved_holdings()

    active_tab = _normalize_active_tab(st.session_state.get("active_tab"))
    st.session_state["active_tab"] = active_tab

    tab_labels = _primary_tab_labels()
    tab_entries = st.tabs(tab_labels)
    tabs_by_name = dict(zip(tab_labels, tab_entries))

    tab_holdings = tabs_by_name["Holdings"]
    tab_analysis = tabs_by_name["Portfolio Analysis"]
    tab_ai_analysis = tabs_by_name.get("AI Analysis")
    tab_insights = tabs_by_name["Portfolio Insights"]
    tab_health = tabs_by_name["Portfolio Health"]

    with tab_holdings:
        holdings = _render_holdings_tab(st.session_state["holdings"], mobile_mode=mobile_mode)

    st.session_state["holdings"] = holdings

    if holdings.empty:
        with tab_analysis:
            st.info("Add at least one holding to calculate analysis.")
        if tab_ai_analysis is not None:
            with tab_ai_analysis:
                st.info("Add at least one holding to run AI Analysis.")
        with tab_insights:
            st.info("Add at least one holding to generate insights.")
        return

    holdings["ticker"] = _sanitize_ticker_series(holdings["ticker"])
    tickers = _to_ticker_tuple(holdings["ticker"])
    metrics = _compute_metrics(holdings, _resolved_price_map(tickers), _resolved_dividend_yield_map(tickers))

    unresolved_prices = metrics[
        pd.to_numeric(metrics.get("shares", 0.0), errors="coerce").fillna(0.0).gt(0)
        & pd.to_numeric(metrics.get("current_price", 0.0), errors="coerce").fillna(0.0).le(0)
    ]
    if not unresolved_prices.empty and not _strict_export_accuracy_mode():
        unresolved_tickers = sorted(
            {
                str(value).strip().upper()
                for value in unresolved_prices.get("ticker", pd.Series([], dtype=str)).tolist()
                if str(value).strip()
            }
        )
        if unresolved_tickers:
            preview = ", ".join(unresolved_tickers[:8])
            suffix = "" if len(unresolved_tickers) <= 8 else f" (+{len(unresolved_tickers) - 8} more)"
            st.warning(
                "Live market data missing for some tickers. "
                f"Current price resolved to $0 for: {preview}{suffix}. "
                "Check ticker symbols and use 'Force price refresh' in the sidebar."
            )

    missing_fields = st.session_state.get("_missing_export_fields", set())
    if _strict_export_accuracy_mode() and missing_fields:
        for field_name in sorted(missing_fields):
            st.error(f"Missing in export: {field_name}")

    with tab_analysis:
        _render_analysis_tab(metrics, mobile_mode=mobile_mode)

    if tab_ai_analysis is not None:
        with tab_ai_analysis:
            _render_ai_analysis_tab(holdings, metrics, mobile_mode=mobile_mode)

    with tab_insights:
        _render_insights_tab(metrics, mobile_mode=mobile_mode)

    with tab_health:
        _render_health_tab(metrics, mobile_mode=mobile_mode)


if __name__ == "__main__":
    main()

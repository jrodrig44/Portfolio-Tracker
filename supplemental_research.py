from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from onemin_ai_client import OneMinAIClient

RESEARCH_STATUSES = {"missing", "current", "due", "running", "failed", "archived"}
JOB_STATUSES = {"pending", "running", "completed", "partially_completed", "failed"}
GENERAL_RESEARCH_EXPIRY = timedelta(days=30)
FAILED_RETRY_DELAY = timedelta(hours=24)
STALE_JOB_LEASE = timedelta(minutes=30)
WORKER_CHECK_INTERVAL = timedelta(hours=6)
DEFAULT_RESEARCH_MODEL = "gpt-4.1-mini"
SUPPORTED_RESEARCH_MODELS = (
    "gpt-4.1-mini",
    "gemini-2.5-flash",
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
)
MAX_RESEARCH_BATCH_SIZE = 3

PROHIBITED_RESEARCH_FIELDS = {
    "current_price", "market_value", "shares", "amount_invested", "cost_basis", "avg_cost",
    "unrealized_gain", "annual_income", "annualized_income", "dividend_yield_pct",
    "portfolio_yield_pct", "weighted_yield_pct", "annual_div_per_share", "effective_annual_div_per_share",
    "reported_annual_div_per_share", "regular_biweekly_contribution", "espp_biweekly_contribution",
    "espp_contribution_biweekly", "special_contribution", "total_contribution", "employer_stock",
    "is_espp", "espp_enabled", "espp_discount", "espp_discount_pct", "espp_purchase_frequency",
    "espp_purchase_frequency_months", "espp_acquired_shares", "projected_10y_value", "projected_20y_value",
    "health_score", "tracker_generated_score_components",
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS supplemental_holding_research (
    ticker TEXT PRIMARY KEY,
    research_status TEXT NOT NULL DEFAULT 'missing',
    research_json TEXT,
    research_markdown TEXT,
    sources_json TEXT,
    model_used TEXT,
    provider_record_id TEXT,
    last_attempt_at TEXT,
    last_success_at TEXT,
    next_due_at TEXT,
    error_message TEXT,
    source_retrieved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    manual_initial_research_completed INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS supplemental_research_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_tickers_json TEXT,
    completed_tickers_json TEXT,
    failed_tickers_json TEXT,
    model_used TEXT,
    started_at TEXT,
    completed_at TEXT,
    lease_expires_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS supplemental_research_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def connect_database(db_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=30, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as connection:
        connection.executescript(_SCHEMA_SQL)


def normalize_ticker(ticker: object) -> str:
    return str(ticker or "").strip().upper()


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _json_loads(value: object, default: object) -> object:
    try:
        parsed = json.loads(str(value or ""))
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def sync_active_holdings(db_path: Path | str, active_tickers: Iterable[str]) -> None:
    initialize_database(db_path)
    tickers = sorted({normalize_ticker(ticker) for ticker in active_tickers if normalize_ticker(ticker)})
    now = iso_datetime(utc_now())
    with connect_database(db_path) as connection:
        for ticker in tickers:
            connection.execute(
                """INSERT INTO supplemental_holding_research
                (ticker, research_status, created_at, updated_at)
                VALUES (?, 'missing', ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET archived = 0, updated_at = ?,
                    research_status = CASE WHEN supplemental_holding_research.research_status = 'archived'
                    THEN CASE WHEN supplemental_holding_research.manual_initial_research_completed = 1
                        AND supplemental_holding_research.last_success_at IS NOT NULL THEN 'current' ELSE 'missing' END
                    ELSE supplemental_holding_research.research_status END""",
                (ticker, now, now, now),
            )
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            connection.execute(
                f"UPDATE supplemental_holding_research SET archived = 1, research_status = 'archived', updated_at = ? WHERE ticker NOT IN ({placeholders}) AND archived = 0",
                (now, *tickers),
            )
        else:
            connection.execute(
                "UPDATE supplemental_holding_research SET archived = 1, research_status = 'archived', updated_at = ? WHERE archived = 0",
                (now,),
            )


def ensure_research_ticker(db_path: Path | str, ticker: str) -> None:
    initialize_database(db_path)
    normalized = normalize_ticker(ticker)
    if not normalized:
        return
    now = iso_datetime(utc_now())
    with connect_database(db_path) as connection:
        connection.execute(
            """INSERT INTO supplemental_holding_research (ticker, research_status, created_at, updated_at)
            VALUES (?, 'missing', ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET archived = 0, updated_at = ?""",
            (normalized, now, now, now),
        )


def get_research_rows(db_path: Path | str, tickers: Iterable[str] | None = None) -> list[dict[str, Any]]:
    initialize_database(db_path)
    normalized = sorted({normalize_ticker(ticker) for ticker in tickers or () if normalize_ticker(ticker)})
    with connect_database(db_path) as connection:
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            rows = connection.execute(
                f"SELECT * FROM supplemental_holding_research WHERE ticker IN ({placeholders}) ORDER BY ticker",
                normalized,
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM supplemental_holding_research ORDER BY ticker").fetchall()
    return [dict(row) for row in rows]


def get_setting(db_path: Path | str, key: str, default: str = "") -> str:
    initialize_database(db_path)
    with connect_database(db_path) as connection:
        row = connection.execute("SELECT setting_value FROM supplemental_research_settings WHERE setting_key = ?", (key,)).fetchone()
    return str(row[0]) if row else default


def set_setting(db_path: Path | str, key: str, value: str) -> None:
    initialize_database(db_path)
    now = iso_datetime(utc_now())
    with connect_database(db_path) as connection:
        connection.execute(
            "INSERT INTO supplemental_research_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?, updated_at = ?",
            (key, str(value), now, str(value), now),
        )


def validated_research_model(db_path: Path | str) -> str:
    configured = get_setting(db_path, "supplemental_research_model", "")
    if configured in SUPPORTED_RESEARCH_MODELS:
        return configured
    set_setting(db_path, "supplemental_research_model", DEFAULT_RESEARCH_MODEL)
    return DEFAULT_RESEARCH_MODEL


def research_is_due(row: dict[str, Any], now: datetime | None = None) -> bool:
    current = now or utc_now()
    if row.get("archived") or not row.get("manual_initial_research_completed"):
        return False
    if row.get("research_status") in {"missing", "running", "archived"}:
        return False
    next_due = parse_datetime(row.get("next_due_at"))
    last_success = parse_datetime(row.get("last_success_at"))
    return bool((next_due and next_due <= current) or (last_success and last_success + GENERAL_RESEARCH_EXPIRY <= current))


def _missing_fields(context: dict[str, Any]) -> list[str]:
    explicit = context.get("missing_fields")
    if isinstance(explicit, list) and explicit:
        return [str(item).strip() for item in explicit if str(item).strip()]
    missing = []
    for field in ("strategy_objective", "expense_ratio", "sector_composition", "top_holdings", "property_focus", "geographic_exposure", "distribution_policy", "risk_characteristics", "recent_structural_developments"):
        if not str(context.get("existing_yahoo_information", {}).get(field, "") or "").strip():
            missing.append(field)
    return missing or ["descriptive_context"]


def build_research_prompt(context: dict[str, Any], current_date: str | None = None) -> str:
    ticker = normalize_ticker(context.get("ticker"))
    safe_context = {
        "ticker": ticker,
        "entity_name": str(context.get("entity_name") or ticker),
        "asset_type": str(context.get("asset_type") or "Unknown"),
        "sector": str(context.get("sector") or "Unknown"),
        "missing_fields": _missing_fields(context),
        "existing_yahoo_information": context.get("existing_yahoo_information") if isinstance(context.get("existing_yahoo_information"), dict) else {},
        "current_date": current_date or utc_now().date().isoformat(),
    }
    return """You are performing supplemental investment research for a local portfolio tracker. Yahoo Finance remains the primary structured financial-data provider. Research only the requested missing or stale descriptive fields using current web research; do not replace Yahoo prices, dividends, yields, fundamentals, classifications, shares, cost basis, contributions, ESPP information, portfolio math, projections, or tracker scores. Do not estimate missing financial numbers or invent unavailable values. Separate verified facts from AI interpretation. Every verified fact must have an HTTPS source URL, source title, source type, source date when available, retrieval date, and confidence. Prefer official company investor-relations pages, official fund-provider pages, SEC filings, official exchange pages, and official REIT investor-relations pages. Secondary sources must be labeled. Avoid personalized fiduciary advice and guaranteed outcomes. Return strict JSON only in this shape: {"ticker":"...","entity_name":"...","retrieved_at":"ISO-8601","verified_facts":[{"field":"...","value":"...","source_url":"https://...","source_title":"...","source_type":"...","source_date":"...","confidence":"high|medium|low"}],"unresolved_fields":[{"field":"...","reason":"..."}],"interpretation":{"summary":"...","portfolio_relevance":"...","risks_to_monitor":["..."]},"limitations":["..."]}. Mark unresolved fields unavailable.\n\nREQUESTED CONTEXT:\n""" + _json_dumps(safe_context)


def _contains_nonfinite(value: object) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_nonfinite(key) or _contains_nonfinite(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_nonfinite(item) for item in value)
    return False


def validate_research_result(raw: object, requested_ticker: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        result = raw
    else:
        text = str(raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            result = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Supplemental research response was not valid JSON.") from exc
    ticker = normalize_ticker(result.get("ticker") if isinstance(result, dict) else "")
    if not isinstance(result, dict) or ticker != normalize_ticker(requested_ticker):
        raise ValueError("Supplemental research response ticker did not match the requested holding.")
    facts = result.get("verified_facts")
    if not isinstance(facts, list):
        raise ValueError("Supplemental research response did not contain a verified_facts list.")
    if _contains_nonfinite(result):
        raise ValueError("Supplemental research response contained a non-finite value.")
    clean_facts: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict) or not str(fact.get("field") or "").strip():
            raise ValueError("Supplemental research contained an invalid verified fact.")
        field = str(fact["field"]).strip().lower()
        if field in PROHIBITED_RESEARCH_FIELDS:
            raise ValueError(f"Supplemental research attempted to overwrite prohibited field: {field}.")
        url = str(fact.get("source_url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Every verified fact must include an HTTPS source URL.")
        clean_facts.append({
            "field": field,
            "value": fact.get("value"),
            "source_url": url,
            "source_title": str(fact.get("source_title") or "Source").strip()[:240],
            "source_type": str(fact.get("source_type") or "secondary").strip()[:80],
            "source_date": str(fact.get("source_date") or "").strip()[:40],
            "retrieved_at": str(result.get("retrieved_at") or utc_now().isoformat()),
            "confidence": str(fact.get("confidence") or "low").strip()[:20],
        })
    unresolved = result.get("unresolved_fields")
    interpretation = result.get("interpretation")
    limitations = result.get("limitations")
    if not isinstance(unresolved, list) or not isinstance(interpretation, dict) or not isinstance(limitations, list):
        raise ValueError("Supplemental research response has an invalid structure.")
    return {
        "ticker": ticker,
        "entity_name": str(result.get("entity_name") or ticker).strip()[:240],
        "retrieved_at": str(result.get("retrieved_at") or utc_now().isoformat()),
        "verified_facts": clean_facts,
        "unresolved_fields": unresolved,
        "interpretation": interpretation,
        "limitations": limitations,
    }


def render_research_markdown(result: dict[str, Any]) -> str:
    def safe_text(value: object, limit: int = 1000) -> str:
        return re.sub(r"<[^>]*>", "", str(value or "")).replace("`", "'")[:limit]

    lines = [f"### Verified Supplemental Facts for {result['ticker']}"]
    facts = result.get("verified_facts", [])
    if facts:
        for fact in facts:
            title = safe_text(fact.get("source_title") or "Source", 240).replace("[", "(").replace("]", ")")
            url = str(fact.get("source_url") or "")
            lines.append(f"- **{safe_text(fact.get('field'), 120)}**: {safe_text(fact.get('value') or 'Unavailable')} ([{title}]({url}))")
    else:
        lines.append("- No verified supplemental facts were found.")
    interpretation = result.get("interpretation", {})
    lines.extend(["", "### AI Interpretation", safe_text(interpretation.get("summary") or "No interpretation provided.")])
    if interpretation.get("portfolio_relevance"):
        lines.append(safe_text(interpretation["portfolio_relevance"]))
    lines.extend(["", "### Limitations", *[f"- {safe_text(item)}" for item in result.get("limitations", [])]])
    lines.extend(["", "### Unresolved Fields", *[f"- {safe_text(item.get('field', 'Unknown'), 120)}: {safe_text(item.get('reason', 'Unavailable'))}" for item in result.get("unresolved_fields", []) if isinstance(item, dict)]])
    return "\n".join(lines)


class SupplementalResearchService:
    def __init__(self, db_path: Path | str, client_factory: Callable[..., Any] | None = None, now_factory: Callable[[], datetime] = utc_now) -> None:
        self.db_path = Path(db_path)
        self.client_factory = client_factory or OneMinAIClient
        self.now_factory = now_factory
        initialize_database(self.db_path)

    def sync_holdings(self, active_tickers: Iterable[str]) -> None:
        sync_active_holdings(self.db_path, active_tickers)

    def _set_running(self, ticker: str, now: datetime) -> bool:
        with connect_database(self.db_path) as connection:
            row = connection.execute("SELECT research_status, archived FROM supplemental_holding_research WHERE ticker = ?", (ticker,)).fetchone()
            if not row or row["archived"]:
                return False
            updated = connection.execute(
                "UPDATE supplemental_holding_research SET research_status = 'running', last_attempt_at = ?, updated_at = ? WHERE ticker = ? AND research_status != 'running' AND archived = 0",
                (iso_datetime(now), iso_datetime(now), ticker),
            ).rowcount
            return bool(updated)

    def research_ticker(self, ticker: str, context: dict[str, Any], model: str, manual: bool = False, force: bool = False) -> dict[str, Any]:
        normalized = normalize_ticker(ticker)
        now = self.now_factory()
        ensure_research_ticker(self.db_path, normalized)
        rows = get_research_rows(self.db_path, [normalized])
        previous = rows[0] if rows else {}
        if not force and not manual and not research_is_due(previous, now):
            return {"ticker": normalized, "status": "skipped", "reason": "current"}
        if not self._set_running(normalized, now):
            return {"ticker": normalized, "status": "skipped", "reason": "already running"}
        try:
            client = self.client_factory(
                api_key=None,
                base_url=os.environ.get("ONEMIN_AI_BASE_URL", ""),
                timeout_seconds=os.environ.get("ONEMIN_AI_TIMEOUT_SECONDS"),
            )
            result = client.chat_with_ai(model=str(model).strip(), prompt=build_research_prompt(context), web_search=True)
            validated = validate_research_result(getattr(result, "content", ""), normalized)
            success_at = self.now_factory()
            next_due = success_at + GENERAL_RESEARCH_EXPIRY
            with connect_database(self.db_path) as connection:
                connection.execute(
                    """UPDATE supplemental_holding_research SET research_status = 'current', research_json = ?, research_markdown = ?, sources_json = ?, model_used = ?, provider_record_id = ?, last_success_at = ?, next_due_at = ?, source_retrieved_at = ?, error_message = NULL, manual_initial_research_completed = CASE WHEN ? THEN 1 ELSE manual_initial_research_completed END, updated_at = ? WHERE ticker = ?""",
                    (_json_dumps(validated), render_research_markdown(validated), _json_dumps(validated["verified_facts"]), str(model), str(getattr(result, "record_id", "")), iso_datetime(success_at), iso_datetime(next_due), str(validated.get("retrieved_at") or iso_datetime(success_at)), int(manual), iso_datetime(success_at), normalized),
                )
            return {"ticker": normalized, "status": "current", "result": validated}
        except Exception as exc:
            message = sanitize_error(str(exc))
            failed_next = self.now_factory() + (FAILED_RETRY_DELAY if not manual else GENERAL_RESEARCH_EXPIRY)
            with connect_database(self.db_path) as connection:
                connection.execute(
                    "UPDATE supplemental_holding_research SET research_status = 'failed', error_message = ?, next_due_at = ?, updated_at = ? WHERE ticker = ?",
                    (message, iso_datetime(failed_next), iso_datetime(self.now_factory()), normalized),
                )
            return {"ticker": normalized, "status": "failed", "error": message}

    def refresh_due(self, contexts: dict[str, dict[str, Any]], model: str, force: bool = False) -> list[dict[str, Any]]:
        rows = get_research_rows(self.db_path, contexts.keys())
        due = [row["ticker"] for row in rows if force or research_is_due(row, self.now_factory())]
        return [self.research_ticker(ticker, contexts[ticker], model, manual=False, force=force) for ticker in due]


def sanitize_error(message: str, limit: int = 500) -> str:
    text = re.sub(r"(?i)(api[-_ ]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", str(message or ""))
    text = re.sub(r"https?://[^\s]+", "[url redacted]", text)
    return text[:limit]


def worker_due_tickers(db_path: Path | str, now: datetime | None = None) -> list[str]:
    current = now or utc_now()
    return [str(row["ticker"]) for row in get_research_rows(db_path) if research_is_due(row, current)]


def acquire_worker_lease(db_path: Path | str, job_key: str = "monthly-supplemental-research", now: datetime | None = None) -> bool:
    initialize_database(db_path)
    current = now or utc_now()
    lease_until = current + STALE_JOB_LEASE
    with connect_database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status, lease_expires_at FROM supplemental_research_jobs WHERE job_key = ?", (job_key,)).fetchone()
        if row and row["status"] == "running" and (parse_datetime(row["lease_expires_at"]) or current) > current:
            return False
        connection.execute(
            """INSERT INTO supplemental_research_jobs (job_key, job_type, status, started_at, lease_expires_at, created_at, updated_at)
            VALUES (?, 'monthly', 'running', ?, ?, ?, ?)
            ON CONFLICT(job_key) DO UPDATE SET status='running', started_at=?, lease_expires_at=?, completed_at=NULL, error_message=NULL, updated_at=?""",
            (job_key, iso_datetime(current), iso_datetime(lease_until), iso_datetime(current), iso_datetime(current), iso_datetime(current), iso_datetime(lease_until), iso_datetime(current)),
        )
        return True


def complete_worker_job(db_path: Path | str, completed: list[str], failed: list[str], job_key: str = "monthly-supplemental-research") -> None:
    now = iso_datetime(utc_now())
    status = "completed" if not failed else "partially_completed" if completed else "failed"
    with connect_database(db_path) as connection:
        connection.execute(
            "UPDATE supplemental_research_jobs SET status = ?, completed_tickers_json = ?, failed_tickers_json = ?, completed_at = ?, lease_expires_at = NULL, updated_at = ? WHERE job_key = ?",
            (status, _json_dumps(completed), _json_dumps(failed), now, now, job_key),
        )

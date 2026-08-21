from __future__ import annotations

import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping


HOLDINGS_TABLE = "holdings"
ASSET_TYPES = {"Stock", "ETF", "REIT", "CEF", "BDC", "Bond Fund", "Other"}
EDITABLE_FIELDS = (
    "ticker", "shares", "espp_acquired_shares", "amount_invested",
    "annual_div_per_share", "regular_biweekly_contribution",
    "espp_contribution_biweekly", "special_contribution", "is_espp",
    "espp_discount", "espp_purchase_frequency_months", "sector", "asset_type",
)
NUMERIC_FIELDS = {
    "shares", "espp_acquired_shares", "amount_invested", "annual_div_per_share",
    "regular_biweekly_contribution", "espp_contribution_biweekly",
    "special_contribution", "espp_discount", "espp_purchase_frequency_months",
}


class HoldingValidationError(ValueError):
    pass


class HoldingNotFoundError(LookupError):
    pass


class HoldingConfirmationError(PermissionError):
    pass


def normalize_holding_input(values: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Apply the production editor's field normalization without importing Streamlit."""
    merged = dict(existing or {})
    merged.update({key: values[key] for key in EDITABLE_FIELDS if key in values})
    ticker = str(merged.get("ticker") or "").strip().upper()
    if not ticker:
        raise HoldingValidationError("Ticker is required.")
    output: dict[str, Any] = {"ticker": ticker}
    for field in NUMERIC_FIELDS:
        raw = merged.get(field, 0.0)
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise HoldingValidationError(f"{field.replace('_', ' ').title()} must be a valid number.") from exc
        if not math.isfinite(number) or number < 0:
            raise HoldingValidationError(f"{field.replace('_', ' ').title()} must be a finite non-negative number.")
        output[field] = number
    if output["espp_discount"] > 1:
        raise HoldingValidationError("ESPP discount must be between 0 and 1.")
    if output["espp_purchase_frequency_months"] < 1:
        raise HoldingValidationError("ESPP purchase frequency must be at least one month.")
    if output["espp_acquired_shares"] > output["shares"]:
        raise HoldingValidationError("ESPP-acquired shares cannot exceed total shares.")
    output["is_espp"] = bool(merged.get("is_espp", False))
    output["sector"] = str(merged.get("sector") or "").strip()
    asset_type = str(merged.get("asset_type") or "Stock").strip()
    output["asset_type"] = asset_type if asset_type in ASSET_TYPES else "Other"
    if ticker == "HD":
        output.update({
            "regular_biweekly_contribution": 0.0,
            "special_contribution": 0.0,
            "espp_contribution_biweekly": output["espp_contribution_biweekly"] or 100.0,
            "is_espp": True,
            "espp_discount": 0.15,
            "espp_purchase_frequency_months": 6.0,
        })
        if output["espp_acquired_shares"] <= 0:
            output["espp_acquired_shares"] = output["shares"]
    return output


class HoldingsWriteService:
    """Atomic, schema-preserving writes for explicit holdings actions only."""

    _guard = threading.Lock()
    _completed_tokens: set[tuple[str, str]] = set()

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _columns(connection: sqlite3.Connection) -> set[str]:
        table = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (HOLDINGS_TABLE,)).fetchone()
        if table is None:
            raise RuntimeError("Portfolio database has no holdings table; Phase 2B does not create or migrate schemas.")
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({HOLDINGS_TABLE})")}

    def _once(self, token: str | None, operation: Any) -> dict[str, Any] | None:
        key = (str(self.db_path.resolve()), str(token))
        with self._guard:
            if token and key in self._completed_tokens:
                return None
            result = operation()
            if token:
                self._completed_tokens.add(key)
            return result

    def add(self, values: Mapping[str, Any], *, submission_token: str | None = None) -> dict[str, Any] | None:
        normalized = normalize_holding_input(values)
        def operation() -> dict[str, Any]:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                columns = self._columns(connection)
                record = self._storage_values(normalized, columns, include_defaults=True)
                names = list(record)
                placeholders = ", ".join("?" for _ in names)
                cursor = connection.execute(
                    f"INSERT INTO {HOLDINGS_TABLE} ({', '.join(names)}) VALUES ({placeholders})",
                    [record[name] for name in names],
                )
                result = self.get(cursor.lastrowid, connection=connection)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return self._once(submission_token, operation)

    def edit(self, holding_id: int, values: Mapping[str, Any], *, submission_token: str | None = None) -> dict[str, Any] | None:
        def operation() -> dict[str, Any]:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                columns = self._columns(connection)
                current = self.get(holding_id, connection=connection)
                normalized = normalize_holding_input(values, existing=current)
                record = self._storage_values(normalized, columns, include_defaults=False)
                assignments = ", ".join(f"{name}=?" for name in record)
                cursor = connection.execute(
                    f"UPDATE {HOLDINGS_TABLE} SET {assignments} WHERE rowid=?",
                    [*record.values(), int(holding_id)],
                )
                if cursor.rowcount != 1:
                    raise HoldingNotFoundError("The holding changed or no longer exists. Refresh and try again.")
                result = self.get(holding_id, connection=connection)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return self._once(submission_token, operation)

    def delete(self, holding_id: int, *, confirmed: bool, expected_ticker: str | None = None, submission_token: str | None = None) -> dict[str, Any] | None:
        if not confirmed:
            raise HoldingConfirmationError("Deletion requires explicit confirmation.")
        def operation() -> dict[str, Any]:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._columns(connection)
                current = self.get(holding_id, connection=connection)
                if expected_ticker and current["ticker"].strip().upper() != expected_ticker.strip().upper():
                    raise HoldingNotFoundError("The holding changed before deletion. Refresh and try again.")
                cursor = connection.execute(f"DELETE FROM {HOLDINGS_TABLE} WHERE rowid=?", (int(holding_id),))
                if cursor.rowcount != 1:
                    raise HoldingNotFoundError("The holding changed or no longer exists. Refresh and try again.")
                connection.commit()
                return current
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return self._once(submission_token, operation)

    def get(self, holding_id: int, *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute(f"SELECT rowid AS _holding_id, * FROM {HOLDINGS_TABLE} WHERE rowid=?", (int(holding_id),)).fetchone()
            if row is None:
                raise HoldingNotFoundError("The holding changed or no longer exists. Refresh and try again.")
            return dict(row)
        finally:
            if owns_connection:
                connection.close()

    @staticmethod
    def _storage_values(values: Mapping[str, Any], columns: set[str], *, include_defaults: bool) -> dict[str, Any]:
        record = {key: values[key] for key in EDITABLE_FIELDS if key in columns and key in values}
        if "espp_biweekly_contribution" in columns:
            record["espp_biweekly_contribution"] = values["espp_contribution_biweekly"]
        if "employer_stock" in columns and (include_defaults or values["ticker"] == "HD"):
            record["employer_stock"] = int(values["ticker"] == "HD")
        if "espp_enabled" in columns:
            record["espp_enabled"] = int(bool(values["is_espp"]))
        if "espp_discount_pct" in columns:
            record["espp_discount_pct"] = values["espp_discount"] * 100.0
        if "espp_purchase_frequency" in columns and values["ticker"] == "HD":
            record["espp_purchase_frequency"] = "approximately_every_6_months"
        return record


__all__ = [
    "ASSET_TYPES", "EDITABLE_FIELDS", "HoldingConfirmationError", "HoldingNotFoundError",
    "HoldingValidationError", "HoldingsWriteService", "normalize_holding_input",
]

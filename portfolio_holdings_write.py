from __future__ import annotations

import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence


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
CONTRIBUTION_FIELDS = (
    "regular_biweekly_contribution", "special_contribution",
    "espp_contribution_biweekly", "is_espp", "espp_discount",
    "espp_purchase_frequency_months",
)


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
        # HD is the production ESPP holding. Preserve its payroll-plan contract
        # across both editors so a generic grid save cannot silently convert it
        # into an ordinary holding or double-count regular contributions.
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


def _nonnegative_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HoldingValidationError(f"{label} must be a valid number.") from exc
    if not math.isfinite(number) or number < 0:
        raise HoldingValidationError(f"{label} must be a finite non-negative number.")
    return number


def _comparison_number(value: Any, default: float = 0.0) -> float:
    """Canonicalize a persisted editor number without applying write-time policy."""
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _editable_comparison_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return storage-faithful semantic values for fields owned by the grid.

    The read model supplies friendly defaults and canonical aliases, while older
    database rows can retain NULLs or differing alias columns. These values are
    intentionally not run through write normalization because doing so expands
    aliases and production defaults which were not changes made by another user.
    """
    espp_contribution = values.get("espp_biweekly_contribution")
    if espp_contribution in (None, "", 0, 0.0):
        espp_contribution = values.get("espp_contribution_biweekly")
    sector = str(values.get("sector") or "").strip()
    asset_type = str(values.get("asset_type") or "").strip()
    if sector.casefold() == "unclassified":
        sector = ""
    if asset_type.casefold() == "unclassified":
        asset_type = ""
    return {
        "ticker": str(values.get("ticker") or "").strip().upper(),
        "shares": _comparison_number(values.get("shares")),
        "espp_acquired_shares": _comparison_number(values.get("espp_acquired_shares")),
        "amount_invested": _comparison_number(values.get("amount_invested")),
        "annual_div_per_share": _comparison_number(values.get("annual_div_per_share")),
        "regular_biweekly_contribution": _comparison_number(values.get("regular_biweekly_contribution")),
        "espp_contribution_biweekly": _comparison_number(espp_contribution),
        "special_contribution": _comparison_number(values.get("special_contribution")),
        "is_espp": bool(values.get("is_espp")) or bool(values.get("espp_enabled")),
        "espp_discount": _comparison_number(values.get("espp_discount")),
        "espp_purchase_frequency_months": _comparison_number(values.get("espp_purchase_frequency_months"), 6.0),
        "sector": sector,
        "asset_type": asset_type,
    }


def normalize_contribution_input(values: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the production contribution fields without touching holding data."""
    ticker = str(current.get("ticker") or "").strip().upper()
    if not ticker:
        raise HoldingValidationError("The stored holding has no ticker.")
    regular = _nonnegative_number(values.get("regular_biweekly_contribution", current.get("regular_biweekly_contribution", 0)), "Regular contribution")
    special = _nonnegative_number(values.get("special_contribution", current.get("special_contribution", 0)), "Special contribution")
    espp = _nonnegative_number(values.get("espp_contribution_biweekly", current.get("espp_contribution_biweekly", current.get("espp_biweekly_contribution", 0))), "ESPP contribution")
    raw_discount = _nonnegative_number(values.get("espp_discount", current.get("espp_discount", current.get("espp_discount_pct", 0))), "ESPP discount")
    if raw_discount > 100:
        raise HoldingValidationError("ESPP discount must be between 0 and 100 percent.")
    discount = raw_discount / 100.0 if raw_discount > 1 else raw_discount
    frequency = _nonnegative_number(values.get("espp_purchase_frequency_months", current.get("espp_purchase_frequency_months", 6)), "ESPP purchase frequency")
    if frequency < 1:
        raise HoldingValidationError("ESPP purchase frequency must be at least one month.")
    is_espp = bool(values.get("is_espp", current.get("is_espp", current.get("espp_enabled", False))))
    is_espp = is_espp or espp > 0 or _nonnegative_number(current.get("espp_acquired_shares", 0) or 0, "ESPP-acquired shares") > 0
    if not is_espp:
        discount = 0.0
    if ticker == "HD":
        regular = 0.0
        special = 0.0
        espp = espp or 100.0
        is_espp = True
        discount = 0.15
        frequency = 6.0
    return {
        "ticker": ticker,
        "regular_biweekly_contribution": regular,
        "special_contribution": special,
        "espp_contribution_biweekly": espp,
        "is_espp": is_espp,
        "espp_discount": discount,
        "espp_purchase_frequency_months": frequency,
    }


def summarize_contributions(holdings: list[Mapping[str, Any]]) -> dict[str, float]:
    regular = sum(_nonnegative_number(row.get("regular_biweekly_contribution", 0) or 0, "Regular contribution") for row in holdings)
    espp = sum(_nonnegative_number(row.get("espp_biweekly_contribution", row.get("espp_contribution_biweekly", 0)) or 0, "ESPP contribution") for row in holdings)
    special = sum(_nonnegative_number(row.get("special_contribution", 0) or 0, "Special contribution") for row in holdings)
    return {
        "regular_biweekly": regular,
        "regular_annual": regular * 26,
        "espp_biweekly": espp,
        "espp_annual": espp * 26,
        "combined_biweekly": regular + espp,
        "combined_annual": (regular + espp) * 26,
        "special_contribution": special,
    }


class HoldingsWriteService:
    """Validate and atomically persist explicit holdings actions exactly once.

    Submission tokens plus the process guard protect against duplicate browser
    clicks. The UI owns the subsequent forced reload/recalculation and invalidates
    every metrics-derived view because shares, basis, income, and contributions
    can affect all of them.
    """

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
                record = self._storage_values(normalized, columns)
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
                record = self._storage_values(normalized, columns)
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

    def update_contributions(self, holding_id: int, values: Mapping[str, Any], *, submission_token: str | None = None) -> dict[str, Any] | None:
        """Atomically update only production contribution and ESPP columns."""
        def operation() -> dict[str, Any]:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                columns = self._columns(connection)
                current = self.get(holding_id, connection=connection)
                normalized = normalize_contribution_input(values, current)
                record = self._contribution_storage_values(normalized, columns)
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

    def save_grid(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        expected_rows: Sequence[Mapping[str, Any]],
        submission_token: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically apply a spreadsheet edit without rewriting unchanged rows."""
        expected_by_id = {
            int(row["_holding_id"]): dict(row)
            for row in expected_rows
            if row.get("_holding_id") not in (None, "")
        }

        def operation() -> dict[str, Any]:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                columns = self._columns(connection)
                current_by_id: dict[int, dict[str, Any]] = {}
                for holding_id, expected in expected_by_id.items():
                    current = self.get(holding_id, connection=connection)
                    current_by_id[holding_id] = current
                    if _editable_comparison_values(current) != _editable_comparison_values(expected):
                        raise HoldingNotFoundError("A holding changed after the grid loaded. Refresh and try again.")
                submitted_ids: set[int] = set()
                normalized_rows: list[tuple[int | None, Mapping[str, Any], dict[str, Any]]] = []
                for values in rows:
                    raw_id = values.get("_holding_id")
                    holding_id = int(raw_id) if raw_id not in (None, "") else None
                    if holding_id is not None:
                        if holding_id in submitted_ids or holding_id not in expected_by_id:
                            raise HoldingValidationError("The holdings grid contains an invalid or duplicate internal row reference.")
                        submitted_ids.add(holding_id)
                        current = current_by_id[holding_id]
                        normalized_rows.append((holding_id, values, normalize_holding_input(values, existing=current)))
                    else:
                        normalized_rows.append((None, values, normalize_holding_input(values)))

                removed_ids = set(expected_by_id) - submitted_ids
                for holding_id in removed_ids:
                    self.get(holding_id, connection=connection)

                added = updated = unchanged = deleted = 0
                for holding_id, submitted, normalized in normalized_rows:
                    record = self._storage_values(normalized, columns)
                    if holding_id is None:
                        names = list(record)
                        placeholders = ", ".join("?" for _ in names)
                        connection.execute(
                            f"INSERT INTO {HOLDINGS_TABLE} ({', '.join(names)}) VALUES ({placeholders})",
                            [record[name] for name in names],
                        )
                        added += 1
                        continue
                    current = current_by_id[holding_id]
                    if _editable_comparison_values(current) == _editable_comparison_values(submitted):
                        unchanged += 1
                        continue
                    changed = {name: value for name, value in record.items() if current.get(name) != value}
                    if not changed:
                        unchanged += 1
                        continue
                    assignments = ", ".join(f"{name}=?" for name in changed)
                    cursor = connection.execute(
                        f"UPDATE {HOLDINGS_TABLE} SET {assignments} WHERE rowid=?",
                        [*changed.values(), holding_id],
                    )
                    if cursor.rowcount != 1:
                        raise HoldingNotFoundError("A holding changed or no longer exists. Refresh and try again.")
                    updated += 1
                for holding_id in removed_ids:
                    cursor = connection.execute(f"DELETE FROM {HOLDINGS_TABLE} WHERE rowid=?", (holding_id,))
                    if cursor.rowcount != 1:
                        raise HoldingNotFoundError("A holding changed or no longer exists. Refresh and try again.")
                    deleted += 1
                connection.commit()
                return {"added": added, "updated": updated, "unchanged": unchanged, "deleted": deleted}
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
    def _storage_values(values: Mapping[str, Any], columns: set[str]) -> dict[str, Any]:
        record = {key: values[key] for key in EDITABLE_FIELDS if key in columns and key in values}
        if "espp_biweekly_contribution" in columns:
            record["espp_biweekly_contribution"] = values["espp_contribution_biweekly"]
        if "employer_stock" in columns:
            record["employer_stock"] = int(values["ticker"] == "HD")
        if "espp_enabled" in columns:
            record["espp_enabled"] = int(bool(values["is_espp"]))
        if "espp_discount_pct" in columns:
            record["espp_discount_pct"] = values["espp_discount"] * 100.0
        if "espp_purchase_frequency" in columns:
            record["espp_purchase_frequency"] = "approximately_every_6_months" if values["ticker"] == "HD" else ""
        return record

    @staticmethod
    def _contribution_storage_values(values: Mapping[str, Any], columns: set[str]) -> dict[str, Any]:
        record = {key: values[key] for key in CONTRIBUTION_FIELDS if key in columns}
        espp_amount = values["espp_contribution_biweekly"]
        if "espp_biweekly_contribution" in columns:
            record["espp_biweekly_contribution"] = espp_amount
        if "espp_enabled" in columns:
            record["espp_enabled"] = int(bool(values["is_espp"]))
        if "espp_discount_pct" in columns:
            record["espp_discount_pct"] = values["espp_discount"] * 100.0
        if "employer_stock" in columns:
            record["employer_stock"] = int(values["ticker"] == "HD")
        if "espp_purchase_frequency" in columns:
            record["espp_purchase_frequency"] = "approximately_every_6_months" if values["ticker"] == "HD" else ""
        return record


__all__ = [
    "ASSET_TYPES", "EDITABLE_FIELDS", "HoldingConfirmationError", "HoldingNotFoundError",
    "HoldingValidationError", "HoldingsWriteService", "normalize_contribution_input",
    "normalize_holding_input", "summarize_contributions",
]

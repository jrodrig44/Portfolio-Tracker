from __future__ import annotations

from app import _latest_historical_context


def get_historical_context(tickers: tuple[str, ...], history_start_date: str | None = None) -> dict[str, dict[str, object]]:
    """Compatibility module exposing historical context engine output."""
    kwargs: dict[str, object] = {}
    if history_start_date:
        kwargs["history_start_date"] = history_start_date
    return _latest_historical_context(tickers, **kwargs)

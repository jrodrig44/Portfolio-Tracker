from __future__ import annotations

import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import run, ui

from nicegui_app.services.portfolio_data import (
    DEFAULT_HISTORY_RANGE,
    HISTORY_RANGE_OPTIONS,
    filter_history,
    load_history,
    load_holdings,
    portfolio_summary,
)


HOST = "0.0.0.0"
PORT = 8502


def currency(value: float) -> str:
    return f"${value:,.2f}"


def percent(value: float) -> str:
    return f"{value:,.2f}%"


@ui.page("/")
async def index() -> None:
    ui.colors(primary="#2563eb", secondary="#0f766e", accent="#f59e0b")
    holdings: list[dict] = []
    history: list[dict] = []

    with ui.header().classes("items-center justify-between"):
        ui.label("Portfolio Tracker · NiceGUI POC").classes("text-xl font-semibold")
        ui.label("Read only").classes("rounded bg-white/20 px-3 py-1 text-sm")

    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-4 gap-4"):
        with ui.row().classes("items-center"):
            spinner = ui.spinner(size="lg")
            status = ui.label("Loading existing portfolio data…")

        with ui.tabs().classes("w-full") as tabs:
            overview_tab = ui.tab("Overview")
            holdings_tab = ui.tab("Holdings")
            history_tab = ui.tab("History")

        with ui.tab_panels(tabs, value=overview_tab).classes("w-full"):
            with ui.tab_panel(overview_tab):
                overview_area = ui.row().classes("w-full gap-3")
            with ui.tab_panel(holdings_tab):
                holdings_area = ui.column().classes("w-full")
            with ui.tab_panel(history_tab):
                with ui.row().classes("items-center gap-3"):
                    ui.label("Range")
                    range_select = ui.toggle(HISTORY_RANGE_OPTIONS, value=DEFAULT_HISTORY_RANGE)
                history_area = ui.column().classes("w-full")

    def render_overview() -> None:
        overview_area.clear()
        summary = portfolio_summary(holdings)
        metrics = (
            ("Portfolio value", currency(summary["portfolio_value"])),
            ("Cost basis", currency(summary["cost_basis"])),
            ("Unrealized gain/loss", currency(summary["unrealized_gain"])),
            ("Estimated annual income", currency(summary["annual_income"])),
            ("Weighted yield", percent(summary["weighted_yield_pct"])),
            ("Holdings", str(summary["holding_count"])),
            ("Largest holding", str(summary["largest_holding"])),
            ("Largest weight", percent(summary["largest_weight_pct"])),
        )
        with overview_area:
            for label, value in metrics:
                with ui.card().classes("min-w-52 grow"):
                    ui.label(label).classes("text-sm text-gray-500")
                    ui.label(value).classes("text-2xl font-semibold")

    def render_holdings() -> None:
        holdings_area.clear()
        columns = [
            {"name": "ticker", "label": "Ticker", "field": "ticker", "sortable": True},
            *[
                {"name": key, "label": label, "field": key, "align": "right", "sortable": True}
                for key, label in (
                    ("shares_display", "Shares"), ("price_display", "Price"),
                    ("value_display", "Market value"), ("cost_display", "Avg. cost"),
                    ("gain_display", "Unrealized G/L"), ("income_display", "Annual income"),
                    ("yield_display", "Dividend yield"),
                )
            ],
            {"name": "asset_type", "label": "Asset type", "field": "asset_type", "sortable": True},
            {"name": "sector", "label": "Sector", "field": "sector", "sortable": True},
        ]
        rows = [dict(row, shares_display=f'{row["shares"]:,.4f}', price_display=currency(row["current_price"]), value_display=currency(row["market_value"]), cost_display=currency(row["average_cost"]), gain_display=currency(row["unrealized_gain"]), income_display=currency(row["annual_income"]), yield_display=percent(row["dividend_yield_pct"])) for row in holdings]
        with holdings_area:
            ui.table(columns=columns, rows=rows, row_key="ticker", pagination=25).classes("w-full").props("dense flat bordered")

    def render_history(selected_range: str) -> None:
        history_area.clear()
        rows = filter_history(history, selected_range)
        with history_area:
            if not rows:
                ui.label("No historical portfolio values are available.").classes("text-gray-500")
                return
            ui.echart({
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 70, "right": 25, "top": 25, "bottom": 55},
                "xAxis": {"type": "category", "data": [row["history_date"] for row in rows]},
                "yAxis": {"type": "value", "scale": True, "axisLabel": {"formatter": "${value}"}},
                "series": [{"name": "Portfolio value", "type": "line", "smooth": False, "showSymbol": True, "data": [row["portfolio_value"] for row in rows], "areaStyle": {"opacity": 0.12}}],
            }).classes("w-full h-96")

    range_select.on_value_change(lambda event: render_history(str(event.value)))
    try:
        holdings, history = await asyncio.gather(
            run.io_bound(load_holdings), run.io_bound(load_history)
        )
        render_overview()
        render_holdings()
        render_history(DEFAULT_HISTORY_RANGE)
        status.set_text(f"Loaded {len(holdings)} holdings and {len(history)} history points")
    except Exception as exc:
        status.set_text("Unable to load the existing read-only data sources.")
        ui.notify(str(exc), type="negative", timeout=0)
    finally:
        spinner.set_visibility(False)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host=HOST, port=PORT, title="Portfolio Tracker · NiceGUI POC", reload=False, show=False)

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import run, ui
from nicegui_app.process_lifecycle import start_launcher_watchdog
from nicegui_app.services.ai_context import ai_options,run_ai_analysis
from nicegui_app.services.analysis import analysis_package
from nicegui_app.services.configuration import DB_REFRESH_INTERVAL_SECONDS
from nicegui_app.services.exports import export_package
from nicegui_app.services.health import health_package
from nicegui_app.services.insights import insights_package
from nicegui_app.services.portfolio_data import DEFAULT_HISTORY_RANGE,HISTORY_RANGE_OPTIONS,filter_history,portfolio_summary
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator
from nicegui_app.services.yahoo import current_market_data,yahoo_intelligence_package

HOST = "0.0.0.0"
PORT = 8502
NAVIGATION_AREAS = ("Holdings", "Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "History")


def currency(value: float) -> str:
    return f"${float(value or 0.0):,.2f}"


def percent(value: float) -> str:
    return f"{float(value or 0.0):,.2f}%"


def metric_cards(items: list[tuple[str, str]]) -> None:
    with ui.row().classes("w-full gap-3"):
        for label, value in items:
            with ui.card().classes("min-w-48 grow"):
                ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500")
                ui.label(value).classes("text-2xl font-semibold")


def records_table(records: list[dict[str, Any]], title: str | None = None) -> None:
    if title:
        ui.label(title).classes("text-lg font-semibold mt-3")
    if not records:
        ui.label("No data available.").classes("text-gray-500")
        return
    keys = list(records[0].keys())
    columns = [{"name": key, "label": key.replace("_", " ").title(), "field": key, "sortable": True} for key in keys]
    ui.table(columns=columns, rows=records, pagination=15).classes("w-full").props("dense flat bordered")


@ui.page("/")
async def index() -> None:
    ui.colors(primary="#1d4ed8", secondary="#0f766e", accent="#d97706")
    holdings: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    metrics = None
    coordinator = ReadOnlyRefreshCoordinator(market_loader=current_market_data)
    loaded = {name: False for name in NAVIGATION_AREAS}

    with ui.header().classes("items-center justify-between shadow-sm"):
        ui.label("Portfolio Tracker · NiceGUI Mirror").classes("text-xl font-semibold")
        ui.label("Read only · Experimental").classes("rounded bg-white/20 px-3 py-1 text-sm")
    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-4 gap-4"):
        with ui.row().classes("items-center"):
            spinner = ui.spinner(size="lg")
            status = ui.label("Loading portfolio and history from read-only databases…")
            refresh_button = ui.button("Refresh Data", icon="refresh")
        with ui.tabs().classes("w-full") as tabs:
            tab_objects = {name: ui.tab(name) for name in NAVIGATION_AREAS}
        with ui.tab_panels(tabs, value=tab_objects["Holdings"]).classes("w-full"):
            areas: dict[str, Any] = {}
            for name in NAVIGATION_AREAS:
                with ui.tab_panel(tab_objects[name]):
                    areas[name] = ui.column().classes("w-full gap-3")

    def render_holdings() -> None:
        area = areas["Holdings"]
        area.clear()
        display_holdings = metrics.to_dict(orient="records") if metrics is not None else holdings
        summary = portfolio_summary(display_holdings)
        with area:
            ui.label("Holdings").classes("text-2xl font-semibold")
            ui.label("Current stored portfolio data. Editing is intentionally disabled.").classes("text-gray-500")
            metric_cards([("Portfolio value", currency(summary["portfolio_value"])), ("Cost basis", currency(summary["cost_basis"])), ("Unrealized gain/loss", currency(summary["unrealized_gain"])), ("Estimated annual income", currency(summary["annual_income"])), ("Weighted yield", percent(summary["weighted_yield_pct"])), ("Holdings", str(summary["holding_count"])), ("Largest holding", str(summary["largest_holding"])), ("Largest weight", percent(summary["largest_weight_pct"]))])
            fields = (("ticker", "Ticker"), ("shares", "Shares"), ("current_price", "Current Price"), ("market_value", "Market Value"), ("average_cost", "Average Cost"), ("cost_basis", "Cost Basis"), ("unrealized_gain", "Unrealized G/L"), ("dividend_yield_pct", "Dividend Yield %"), ("annual_income", "Annual Income"), ("asset_type", "Asset Type"), ("sector", "Sector"), ("regular_biweekly_contribution", "Regular Biweekly"), ("espp_biweekly_contribution", "ESPP Biweekly"), ("special_contribution", "Special Contribution"), ("employer_stock", "Employer Stock"), ("espp_enabled", "ESPP"))
            columns = [{"name": key, "label": label, "field": key, "sortable": True} for key, label in fields]
            ui.table(columns=columns, rows=display_holdings, row_key="ticker", pagination=25).classes("w-full").props("dense flat bordered")
            ui.label("Read-only exports").classes("text-lg font-semibold mt-3")
            async def download_export(filename: str) -> None:
                try:
                    package = await run.io_bound(export_package, holdings, metrics)
                    ui.download(package[filename].encode("utf-8"), filename)
                except Exception as exc:
                    ui.notify(f"Export failed: {exc}", type="negative")
            with ui.row().classes("gap-2"):
                ui.button("Holdings CSV", on_click=lambda: download_export("holdings.csv"))
                ui.button("Comprehensive Markdown", on_click=lambda: download_export("portfolio_export.md"))
                ui.button("Structured JSON", on_click=lambda: download_export("portfolio_export.json"))
        loaded["Holdings"] = True

    async def render_analysis() -> None:
        if loaded["Portfolio Analysis"]:
            return
        area = areas["Portfolio Analysis"]
        with area:
            ui.spinner(size="lg")
            ui.label("Calculating portfolio analysis…")
        try:
            package = await run.io_bound(analysis_package, metrics)
            area.clear()
            totals, summary = package["totals"], package["summary"]
            with area:
                ui.label("Portfolio Analysis").classes("text-2xl font-semibold")
                metric_cards([("Valuation", currency(totals["total_portfolio_value"])), ("Cost basis", currency(totals["total_cost_basis"])), ("Unrealized gain/loss", currency(totals["total_unrealized_gain"])), ("Annual income", currency(totals["total_estimated_annual_income"])), ("Weighted yield", percent(totals["weighted_portfolio_yield"])), ("Largest holding", str(totals["largest_holding"]))])
                with ui.expansion("Allocation and economic exposure", value=True).classes("w-full"):
                    records_table(package["asset_allocation"], "Asset allocation")
                    records_table(totals["economic_exposure_allocation"], "Economic exposure")
                with ui.expansion("Direct sector exposure").classes("w-full"):
                    records_table(package["direct_sector"])
                with ui.expansion("Concentration and ESPP context").classes("w-full"):
                    ui.json_editor({"content": {"json": package["concentration"]}}).props("read-only")
                with ui.expansion("Contribution allocation").classes("w-full"):
                    records_table(package["contributions"].get("holdings", []))
                with ui.expansion("Income allocation and quality").classes("w-full"):
                    records_table(totals["income_allocation_by_asset_type"], "Income by asset type")
                    records_table(package["income_quality"], "Income quality")
                with ui.expansion("Projections and milestones").classes("w-full"):
                    snapshot = package["snapshot"]
                    metric_cards([("Projected 10-year value", currency(snapshot["projected_10y_value"])), ("Projected 20-year value", currency(snapshot["projected_20y_value"])), ("Active contributors", str(summary.get("active_contributors", 0)))])
                    ui.label(snapshot["projection_contribution_note"]).classes("text-gray-500")
                with ui.expansion("Yahoo intelligence and supplemental historical context").classes("w-full"):
                    ui.label("Network-heavy Yahoo intelligence is demand-driven. No research refresh controls are exposed.")
                    yahoo_area = ui.column().classes("w-full")
                    async def load_yahoo_intelligence() -> None:
                        yahoo_button.disable()
                        with yahoo_area:
                            pending = ui.label("Loading existing Yahoo-backed intelligence…")
                        try:
                            intelligence = await run.io_bound(yahoo_intelligence_package, metrics)
                            yahoo_area.clear()
                            with yahoo_area:
                                fund_context = intelligence.get("fund_lookthrough", {})
                                metric_cards([("Fund look-through coverage", percent(fund_context.get("fund_lookthrough_completeness_pct", 0.0))), ("Resolved fund exposure", currency(fund_context.get("resolved_value", 0.0))), ("Unresolved fund exposure", currency(fund_context.get("unresolved_value", 0.0)))])
                                records_table(intelligence["lookthrough"], "Fund look-through exposure")
                                records_table(intelligence["unresolved_lookthrough"], "Unresolved fund exposure")
                                records_table(intelligence["investment_intelligence"], "Investment intelligence")
                                records_table(intelligence["historical_context"], "Historical security context")
                                seasonality = intelligence["seasonality"]
                                if seasonality.get("chart_available"):
                                    monthly = seasonality.get("monthly_income", {})
                                    ui.echart({"xAxis": {"type": "category", "data": list(monthly)}, "yAxis": {"type": "value"}, "series": [{"name": "Estimated income", "type": "bar", "data": list(monthly.values())}]}).classes("w-full h-80")
                                else:
                                    ui.label(str(seasonality.get("message", "Dividend seasonality is unavailable."))).classes("text-gray-500")
                        except Exception as exc:
                            pending.set_text(f"Yahoo intelligence could not be loaded: {exc}")
                        finally:
                            yahoo_button.enable()
                    yahoo_button = ui.button("Load Yahoo intelligence", on_click=load_yahoo_intelligence)
        except Exception as exc:
            area.clear()
            with area:
                ui.label(f"Analysis could not be loaded: {exc}").classes("text-red-600")
        loaded["Portfolio Analysis"] = True

    def render_ai() -> None:
        if loaded["AI Analysis"]:
            return
        options = ai_options()
        area = areas["AI Analysis"]
        with area:
            ui.label("AI Analysis").classes("text-2xl font-semibold")
            ui.label("No request is sent until Analyze is clicked. Uses existing compact context and the 1min.AI provider.").classes("text-gray-500")
            if not options["configured"]:
                ui.label("ONEMIN_AI_API_KEY is not configured in the local environment.").classes("text-amber-700")
            analysis_type = ui.select(options["analysis_types"], value=options["analysis_types"][0], label="Analysis type").classes("w-full max-w-xl")
            model = ui.select(options["models"], value=options["default_model"], label="Model").classes("w-full max-w-xl")
            instructions = ui.textarea(label="Optional instructions").classes("w-full")
            output = ui.markdown("").classes("w-full")
            result_text = {"value": ""}
            async def analyze() -> None:
                button.disable()
                output.set_content("_Analyzing through 1min.AI…_")
                try:
                    result = await run.io_bound(run_ai_analysis, holdings, metrics, analysis_type.value, model.value, instructions.value or "")
                    result_text["value"] = result.markdown
                    output.set_content(result.markdown)
                    ui.notify(f"Analysis completed with {result.model}", type="positive")
                except Exception as exc:
                    output.set_content(f"**Analysis failed:** {exc}")
                finally:
                    button.enable()
            button = ui.button("Analyze", on_click=analyze, color="primary")
            ui.button("Download AI Markdown", on_click=lambda: ui.download(result_text["value"].encode("utf-8"), "ai_analysis.md"))
        loaded["AI Analysis"] = True

    async def render_insights() -> None:
        if loaded["Portfolio Insights"]:
            return
        area = areas["Portfolio Insights"]
        with area:
            ui.spinner(size="lg")
        package = await run.io_bound(insights_package, metrics)
        area.clear()
        with area:
            ui.label("Portfolio Insights").classes("text-2xl font-semibold")
            for observation in package["observations"]:
                ui.label(f"• {observation}")
            with ui.expansion("Allocation and diversification", value=True).classes("w-full"):
                records_table(package["allocation"])
                records_table(package.get("economic_exposure", []), "Economic exposure")
                records_table(package.get("direct_sector", []), "Direct sector exposure")
            with ui.expansion("Contribution observations").classes("w-full"):
                records_table(package["contribution"]["rows"])
            with ui.expansion("Yield and income observations").classes("w-full"):
                ui.json_editor({"content": {"json": package["yield"]}}).props("read-only")
                records_table(package.get("income_allocation", []), "Income allocation by asset type")
                records_table(package.get("income_quality", []), "Income quality")
            with ui.expansion("Fund look-through and completeness").classes("w-full"):
                fund_context = package.get("fund_lookthrough", {})
                records_table(fund_context.get("records", []), "Resolved fund exposure")
                records_table(fund_context.get("unresolved_records", []), "Unresolved fund exposure")
                ui.json_editor({"content": {"json": package.get("data_completeness", {})}}).props("read-only")
        loaded["Portfolio Insights"] = True

    async def render_health() -> None:
        if loaded["Portfolio Health"]:
            return
        area = areas["Portfolio Health"]
        with area:
            ui.spinner(size="lg")
            ui.label("Calculating tracker health…")
        try:
            package = await run.io_bound(health_package, metrics)
            area.clear()
            with area:
                ui.label("Portfolio Health").classes("text-2xl font-semibold")
                ui.label(str(package.get("disclaimer", "Tracker-generated and not an objective investment rating."))).classes("font-semibold text-amber-700")
                metric_cards([("Overall health score", f"{float(package.get('health_score', 0.0)):.1f}/100"), ("Largest holding", str(package.get("largest_holding", "N/A"))), ("Largest holding weight", percent(package.get("largest_holding_weight", 0.0)))])
                records_table([{"component": key, "score": value} for key, value in package.get("subscores", {}).items()], "Component scores")
                records_table(package.get("strongest_areas", []), "Strongest areas")
                records_table(package.get("weakest_areas", []), "Weakest areas")
                ui.label("Alerts").classes("text-lg font-semibold")
                for alert in package.get("alerts", []) or ["No health alerts."]:
                    ui.label(f"• {alert}")
                for note in package.get("notes", []):
                    ui.label(f"• {note}")
                ui.label(str(package.get("data_quality_explanation", ""))).classes("text-gray-600")
        except Exception as exc:
            area.clear()
            with area:
                ui.label(f"Health could not be loaded: {exc}").classes("text-red-600")
        loaded["Portfolio Health"] = True

    def render_history(selected_range: str = DEFAULT_HISTORY_RANGE) -> None:
        area = areas["History"]
        area.clear()
        with area:
            ui.label("Historical Portfolio Value").classes("text-2xl font-semibold")
            ui.label("Nominal account value only—not investment return. No interpolation; monthly and live snapshot points are retained.").classes("text-gray-500")
            selector = ui.toggle(list(HISTORY_RANGE_OPTIONS), value=selected_range)
            chart_area = ui.column().classes("w-full")
            def update_chart(range_name: str) -> None:
                chart_area.clear()
                selected = filter_history(history, range_name)
                with chart_area:
                    if not selected:
                        ui.label("No historical values are available.")
                        return
                    ui.echart({"tooltip": {"trigger": "axis"}, "xAxis": {"type": "category", "data": [row["history_date"] for row in selected]}, "yAxis": {"type": "value", "scale": True, "axisLabel": {"formatter": "${value}"}}, "series": [{"name": "Portfolio value", "type": "line", "data": [row["portfolio_value"] for row in selected], "areaStyle": {"opacity": 0.12}}]}).classes("w-full h-96")
            selector.on_value_change(lambda event: update_chart(str(event.value)))
            update_chart(selected_range)
        loaded["History"] = True

    async def navigate(event: Any) -> None:
        name = str(event.value)
        if name == "Portfolio Analysis": await render_analysis()
        elif name == "AI Analysis": render_ai()
        elif name == "Portfolio Insights": await render_insights()
        elif name == "Portfolio Health": await render_health()
        elif name == "History" and not loaded["History"]: render_history()

    tabs.on_value_change(navigate)
    async def refresh_data(force: bool = False) -> None:
        nonlocal holdings,history,metrics
        result = await run.io_bound(coordinator.refresh,force)
        if not result.portfolio_changed and not result.history_changed and not result.metrics_changed:
            return
        if result.portfolio_changed:
            holdings=result.holdings or []
        if result.metrics_changed:
            metrics=result.metrics
            render_holdings()
            for name in ("Portfolio Analysis","Portfolio Insights","Portfolio Health"):
                loaded[name]=False;areas[name].clear()
                with areas[name]: ui.label("Portfolio values changed; this section will refresh when opened.").classes("text-gray-500")
        if result.history_changed:
            history=result.history or []
            if loaded["History"]: render_history()
        status.set_text(f"Refreshed {len(holdings)} holdings and {len(history)} history points")
    refresh_button.on_click(lambda: refresh_data(True))
    ui.timer(DB_REFRESH_INTERVAL_SECONDS,lambda: refresh_data(False))
    try:
        initial=await run.io_bound(coordinator.refresh,True)
        holdings=initial.holdings or [];history=initial.history or [];metrics=initial.metrics
        render_holdings()
        status.set_text(f"Loaded {len(holdings)} holdings and {len(history)} history points")
    except Exception as exc:
        status.set_text("Unable to load the read-only portfolio sources.")
        ui.notify(str(exc), type="negative", timeout=0)
    finally:
        spinner.set_visibility(False)


if __name__ in {"__main__", "__mp_main__"}:
    start_launcher_watchdog()
    ui.run(host=HOST, port=PORT, title="Portfolio Tracker · NiceGUI Mirror", reload=False, show=False)

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import run, ui
from nicegui_app.process_lifecycle import start_launcher_watchdog
from nicegui_app.services.ai_context import AIAnalysisState,ai_options,run_ai_analysis
from nicegui_app.services.analysis import analysis_package
from nicegui_app.services.configuration import DB_REFRESH_INTERVAL_SECONDS
from nicegui_app.services.exports import export_package
from nicegui_app.services.formatting import FULL_WIDTH_CONTROL, RESPONSIVE_ACTIONS, RESPONSIVE_CARD_GRID, SCROLL_TABLE, format_count, format_currency, format_field_value, format_percentage, format_score, format_status, format_table_records, human_label
from nicegui_app.services.health import health_package
from nicegui_app.services.history import history_package
from nicegui_app.services.insights import insights_package
from nicegui_app.services.portfolio_data import DEFAULT_HISTORY_RANGE,HISTORY_RANGE_OPTIONS,portfolio_summary
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator
from nicegui_app.services.yahoo import current_market_data,yahoo_intelligence_package

HOST = "0.0.0.0"
PORT = 8502
NAVIGATION_AREAS = ("Holdings", "Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "History")


def currency(value: Any) -> str:
    return format_currency(value)


def percent(value: Any) -> str:
    return format_percentage(value)


def metric_cards(items: list[tuple[str, str]]) -> None:
    with ui.element("div").classes(RESPONSIVE_CARD_GRID):
        for label, value in items:
            with ui.card().classes("w-full min-w-0"):
                ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500")
                ui.label(value).classes("text-xl sm:text-2xl font-semibold break-words")


def detail_cards(items: list[tuple[str, Any]]) -> None:
    with ui.element("div").classes(RESPONSIVE_CARD_GRID):
        for label, value in items:
            with ui.card().classes("w-full min-w-0 py-3"):
                ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500")
                ui.label(str(value)).classes("text-base font-medium break-words")


def guidance_text(text: Any) -> None:
    if str(text or "").strip():
        ui.label(str(text).strip()).classes("w-full rounded-lg bg-blue-50 p-3 text-sm leading-relaxed text-blue-900")


def records_table(records: list[dict[str, Any]], title: str | None = None) -> None:
    if title:
        ui.label(title).classes("text-lg font-semibold mt-3")
    if not records:
        ui.label("No data available.").classes("text-gray-500")
        return
    columns, rows = format_table_records(records)
    if not columns:
        ui.label("No displayable values are available.").classes("text-gray-500")
        return
    with ui.element("div").classes(SCROLL_TABLE):
        ui.table(columns=columns, rows=rows, pagination=15).classes("w-full min-w-max").props("dense flat bordered")


@ui.page("/")
async def index() -> None:
    ui.colors(primary="#1d4ed8", secondary="#0f766e", accent="#d97706")
    holdings: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    history_range = DEFAULT_HISTORY_RANGE
    metrics = None
    coordinator = ReadOnlyRefreshCoordinator(market_loader=current_market_data)
    loaded = {name: False for name in NAVIGATION_AREAS}

    with ui.header().classes("flex-wrap items-center justify-between gap-2 shadow-sm px-3 sm:px-5"):
        ui.label("Portfolio Tracker").classes("text-lg sm:text-xl font-semibold")
        ui.label("Read only · NiceGUI").classes("rounded bg-white/20 px-3 py-1 text-xs sm:text-sm")
    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-2 sm:p-4 gap-4"):
        with ui.row().classes("w-full flex-wrap items-center gap-2"):
            spinner = ui.spinner(size="lg")
            status = ui.label("Loading portfolio and history from read-only databases…").classes("grow text-sm break-words")
            refresh_button = ui.button("Refresh Data", icon="refresh").classes("min-h-11")
        with ui.element("div").classes("w-full max-w-full overflow-x-auto"):
            tabs = ui.tabs().classes("w-max min-w-full").props("mobile-arrows outside-arrows")
        with tabs:
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
            metric_cards([("Portfolio value", currency(summary["portfolio_value"])), ("Cost basis", currency(summary["cost_basis"])), ("Unrealized gain/loss", currency(summary["unrealized_gain"])), ("Estimated annual income", currency(summary["annual_income"])), ("Weighted yield", percent(summary["weighted_yield_pct"])), ("Holdings", format_count(summary["holding_count"])), ("Largest holding", str(summary["largest_holding"])), ("Largest weight", percent(summary["largest_weight_pct"]))])
            fields = (("ticker", "Ticker"), ("shares", "Shares"), ("current_price", "Current Price"), ("market_value", "Market Value"), ("average_cost", "Average Cost"), ("cost_basis", "Cost Basis"), ("unrealized_gain", "Unrealized G/L"), ("dividend_yield_pct", "Dividend Yield %"), ("annual_income", "Annual Income"), ("asset_type", "Asset Type"), ("sector", "Sector"), ("regular_biweekly_contribution", "Regular Biweekly"), ("espp_biweekly_contribution", "ESPP Biweekly"), ("special_contribution", "Special Contribution"), ("employer_stock", "Employer Stock"), ("espp_enabled", "ESPP"))
            selected_rows = [{key: row.get(key) for key, _ in fields} for row in display_holdings]
            columns, rows = format_table_records(selected_rows)
            labels = dict(fields)
            for column in columns:
                column["label"] = labels.get(column["name"], column["label"])
            with ui.element("div").classes(SCROLL_TABLE):
                ui.table(columns=columns, rows=rows, row_key="ticker", pagination=25).classes("w-full min-w-max").props("dense flat bordered")
            ui.label("Read-only exports").classes("text-lg font-semibold mt-3")
            ui.label("Generate a fresh read-only snapshot, then download it in the format you need.").classes("text-sm text-gray-500")
            async def download_export(filename: str) -> None:
                try:
                    package = await run.io_bound(export_package, holdings, metrics, history=history)
                    ui.download(package[filename].encode("utf-8"), filename)
                except Exception as exc:
                    ui.notify(f"Export failed: {exc}", type="negative")
            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                ui.button("Holdings CSV", icon="table_view", on_click=lambda: download_export("holdings.csv")).classes("w-full sm:w-auto min-h-11")
                ui.button("Portfolio report", icon="description", on_click=lambda: download_export("portfolio_export.md")).classes("w-full sm:w-auto min-h-11")
                ui.button("Structured data", icon="data_object", on_click=lambda: download_export("portfolio_export.json")).classes("w-full sm:w-auto min-h-11")
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
                    concentration = package.get("concentration", {})
                    employers = concentration.get("employer_stock", []) if isinstance(concentration, dict) else []
                    employer = employers[0] if employers and isinstance(employers[0], dict) else {}
                    detail_cards([
                        ("Largest holding", f"{concentration.get('largest_holding_by_value', 'N/A')} — {percent(concentration.get('largest_holding_weight_pct'))}"),
                        ("Largest income source", f"{concentration.get('largest_income_contributor', 'N/A')} — {percent(concentration.get('largest_income_weight_pct'))}"),
                        ("Employer stock", employer.get("ticker", "None identified")),
                        ("Capital concentration", percent(employer.get("capital_concentration_pct")) if employer else "Not applicable"),
                        ("Income concentration", percent(employer.get("income_concentration_pct")) if employer else "Not applicable"),
                        ("ESPP", format_status(bool(concentration.get("espp_concentration_flag")), enabled="Active", disabled="Not active")),
                    ])
                    guidance_text(concentration.get("guidance"))
                with ui.expansion("Contribution allocation").classes("w-full"):
                    records_table(package["contributions"].get("holdings", []))
                with ui.expansion("Income allocation and quality").classes("w-full"):
                    records_table(totals["income_allocation_by_asset_type"], "Income by asset type")
                    records_table(package["income_quality"], "Income quality")
                with ui.expansion("Projections and milestones").classes("w-full"):
                    snapshot = package["snapshot"]
                    metric_cards([("Projected 10-year value", currency(snapshot["projected_10y_value"])), ("Projected 20-year value", currency(snapshot["projected_20y_value"])), ("Active contributors", format_count(summary.get("active_contributors", 0)))])
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
                    yahoo_button = ui.button("Load Yahoo intelligence", on_click=load_yahoo_intelligence).classes("w-full sm:w-auto min-h-11")
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
            with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 gap-3"):
                analysis_type = ui.select(options["analysis_types"], value=options["analysis_types"][0], label="Analysis type").classes(FULL_WIDTH_CONTROL)
                model = ui.select(options["models"], value=options["default_model"], label="Model").classes(FULL_WIDTH_CONTROL)
            instructions = ui.textarea(label="Optional instructions").classes("w-full").props("autogrow")
            with ui.card().classes("w-full min-h-32"):
                ui.label("Latest analysis").classes("text-lg font-semibold")
                output = ui.markdown("No analysis has been run yet.").classes("w-full max-w-none break-words")
            status = ui.label("").classes("w-full text-sm text-gray-600 break-words")
            context_size = ui.label("").classes("w-full text-xs text-gray-500 break-words")
            state = AIAnalysisState()
            request_active = {"value": False}
            async def analyze() -> None:
                if request_active["value"]:
                    return
                request_active["value"] = True
                button.disable()
                try:
                    status.set_text("Analyzing through 1min.AI…")
                    await run.io_bound(run_ai_analysis, holdings, metrics, analysis_type.value, model.value, instructions.value or "", state=state)
                    stats = state.context_diagnostics
                    if stats:
                        context_size.set_text(f"AI context: {int(stats.get('after_chars', 0)):,} chars (~{int(stats.get('after_tokens', 0)):,} tokens), {float(stats.get('reduction_pct', 0)):.1f}% smaller after compaction.")
                    if state.error_message:
                        status.set_text(state.error_message)
                        ui.notify(state.error_message, type="negative")
                    else:
                        output.set_content(state.result_markdown)
                        status.set_text(f"Completed with {state.result_model} at {state.generated_at}.")
                        ui.notify(f"Analysis completed with {state.result_model}", type="positive")
                finally:
                    request_active["value"] = False
                    button.enable()
            def clear_analysis() -> None:
                state.clear()
                output.set_content("No analysis has been run yet.")
                status.set_text("")
                context_size.set_text("")
            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                button = ui.button("Analyze", on_click=analyze, icon="auto_awesome", color="primary").classes("w-full sm:w-auto min-h-11")
                ui.button("Clear analysis", icon="clear", on_click=clear_analysis).classes("w-full sm:w-auto min-h-11")
                ui.button("Download Markdown", icon="download", on_click=lambda: ui.download(state.result_markdown.encode("utf-8"), "ai_analysis.md")).classes("w-full sm:w-auto min-h-11")
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
                yield_context = package.get("yield", {})
                detail_cards([(human_label(key), format_field_value(key, value)) for key, value in yield_context.items() if not isinstance(value, (dict, list))])
                records_table(package.get("income_allocation", []), "Income allocation by asset type")
                records_table(package.get("income_quality", []), "Income quality")
            with ui.expansion("Fund look-through and completeness").classes("w-full"):
                fund_context = package.get("fund_lookthrough", {})
                records_table(fund_context.get("records", []), "Resolved fund exposure")
                records_table(fund_context.get("unresolved_records", []), "Unresolved fund exposure")
                completeness = package.get("data_completeness", {})
                ui.label("Data completeness").classes("text-lg font-semibold mt-3")
                detail_cards([(human_label(key), format_field_value(key, value)) for key, value in completeness.items()])
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
                metric_cards([("Overall health score", format_score(package.get("health_score"), out_of_100=True)), ("Largest holding", str(package.get("largest_holding", "N/A"))), ("Largest holding weight", percent(package.get("largest_holding_weight", 0.0)))])
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
        nonlocal history_range
        history_range = selected_range
        area = areas["History"]
        area.clear()
        with area:
            ui.label("Historical Portfolio Value").classes("text-2xl font-semibold")
            ui.label("Nominal account value only—not investment return. No interpolation; monthly and live snapshot points are retained.").classes("text-gray-500")
            with ui.element("div").classes("w-full max-w-full overflow-x-auto"):
                selector = ui.toggle(list(HISTORY_RANGE_OPTIONS), value=selected_range).classes("min-w-max")
            chart_area = ui.column().classes("w-full")
            def update_chart(range_name: str) -> None:
                nonlocal history_range
                history_range = range_name
                chart_area.clear()
                package = history_package(history, range_name)
                with chart_area:
                    if not package["records"]:
                        ui.label("No historical values are available.")
                        return
                    summary = package["summary"]
                    metric_cards([("Starting value", currency(summary["starting_portfolio_value"])), ("Ending value", currency(summary["ending_portfolio_value"])), ("Value change", currency(summary["absolute_change"])), ("Value change %", percent(summary["percentage_change"])), ("History points", format_count(summary["point_count"]))])
                    ui.echart({"tooltip": {"trigger": "axis"}, "xAxis": {"type": "category", "data": package["series"]["dates"]}, "yAxis": {"type": "value", "scale": True, "axisLabel": {"formatter": "${value}"}}, "series": [{"name": "Portfolio value", "type": "line", "data": package["series"]["portfolio_values"], "areaStyle": {"opacity": 0.12}}]}).classes("w-full h-96")
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
            if loaded["History"]: render_history(history_range)
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

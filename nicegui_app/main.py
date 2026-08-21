from __future__ import annotations

import sys
import uuid
import calendar
from datetime import date
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
from nicegui_app.services.formatting import CONFIRM_DIALOG_CARD, DIALOG_CARD, EMPTY_STATE, FULL_WIDTH_CONTROL, RESPONSIVE_ACTIONS, RESPONSIVE_CARD_GRID, SAFE_RICH_OUTPUT, SCROLL_TABLE, format_count, format_currency, format_field_value, format_percentage, format_score, format_status, format_table_records, human_label
from nicegui_app.services.health import health_package
from nicegui_app.services.history import history_package
from nicegui_app.services.insights import insights_package
from nicegui_app.services.portfolio_data import DEFAULT_HISTORY_RANGE,HISTORY_RANGE_OPTIONS,portfolio_summary
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator
from nicegui_app.services.yahoo import current_market_data,yahoo_intelligence_package
from portfolio_holdings_write import ASSET_TYPES, HoldingsWriteService, summarize_contributions
from nicegui_app.services.operations import format_interval, operational_status
from portfolio_research import PortfolioResearchService, research_contexts
from portfolio_statement_history import StatementHistoryService, statement_display_records

HOST = "0.0.0.0"
PORT = 8502
NAVIGATION_AREAS = ("Holdings", "Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "History", "Statement History", "Supplemental Research", "System")


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
        ui.label("No records are available for this section yet.").classes(EMPTY_STATE)
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
    ui.add_css(".safe-rich-output, .safe-rich-output * { max-width: 100%; overflow-wrap: anywhere; word-break: break-word; } .safe-rich-output pre { overflow-x: auto; white-space: pre-wrap; }")
    ui.query("body").classes("overflow-x-hidden")
    holdings: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    history_range = DEFAULT_HISTORY_RANGE
    metrics = None
    coordinator = ReadOnlyRefreshCoordinator(market_loader=current_market_data)
    holdings_writer = HoldingsWriteService(coordinator.portfolio_path)
    statement_service = StatementHistoryService(db_path=coordinator.history_path)
    research_service = PortfolioResearchService(coordinator.portfolio_path)
    loaded = {name: False for name in NAVIGATION_AREAS}

    with ui.header().classes("flex-wrap items-center justify-between gap-2 shadow-sm px-3 sm:px-5"):
        ui.label("Portfolio Tracker").classes("text-lg sm:text-xl font-semibold")
        ui.label("Holdings writable · NiceGUI").classes("rounded bg-white/20 px-3 py-1 text-xs sm:text-sm")
    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-2 sm:p-4 gap-4"):
        with ui.row().classes("w-full flex-wrap items-center gap-2"):
            spinner = ui.spinner(size="lg")
            status = ui.label("Loading portfolio and history from read-only databases…").classes("grow text-sm break-words")
            refresh_button = ui.button("Refresh Data", icon="refresh").classes("min-h-11")
        mobile_navigation = ui.select(NAVIGATION_AREAS, value="Holdings", label="Current section").classes("w-full md:hidden").props("outlined options-dense")
        with ui.element("nav").classes("hidden md:block w-full max-w-full overflow-x-auto").props('aria-label="Portfolio sections"'):
            tabs = ui.tabs().classes("w-max min-w-full").props("outside-arrows")
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
            ui.label("Add, edit, or remove holdings. Other portfolio data remains read-only.").classes("text-gray-500")
            def holding_form(dialog: Any, existing: dict[str, Any] | None = None) -> None:
                source = existing or {}
                ticker = ui.input("Ticker", value=source.get("ticker", "")).classes(FULL_WIDTH_CONTROL)
                with ui.element("div").classes("w-full grid grid-cols-1 sm:grid-cols-2 gap-3"):
                    shares = ui.number("Shares", value=source.get("shares", 0.0), min=0, step=0.000001, format="%.6f").classes(FULL_WIDTH_CONTROL)
                    espp_shares = ui.number("ESPP-acquired shares", value=source.get("espp_acquired_shares", 0.0), min=0, step=0.000001, format="%.6f").classes(FULL_WIDTH_CONTROL)
                    invested = ui.number("Amount invested", value=source.get("amount_invested", source.get("cost_basis", 0.0)), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    annual_dividend = ui.number("Annual dividend per share", value=source.get("annual_div_per_share", 0.0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    regular = ui.number("Regular biweekly contribution", value=source.get("regular_biweekly_contribution", 0.0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    espp = ui.number("ESPP biweekly contribution", value=source.get("espp_biweekly_contribution", source.get("espp_contribution_biweekly", 0.0)), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    special = ui.number("Special contribution", value=source.get("special_contribution", 0.0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    discount_pct = source.get("espp_discount", 0.0)
                    if not discount_pct:
                        raw_pct = float(source.get("espp_discount_pct", 0.0) or 0.0)
                        discount_pct = raw_pct / 100.0 if raw_pct > 1 else raw_pct
                    discount = ui.number("ESPP discount (decimal)", value=discount_pct, min=0, max=1, step=0.01).classes(FULL_WIDTH_CONTROL)
                    frequency = ui.number("ESPP purchase frequency (months)", value=source.get("espp_purchase_frequency_months", 6.0) or 6.0, min=1, step=1).classes(FULL_WIDTH_CONTROL)
                    asset_type = ui.select(sorted(ASSET_TYPES), value=source.get("asset_type", "Stock") if source.get("asset_type", "Stock") in ASSET_TYPES else "Other", label="Asset type").classes(FULL_WIDTH_CONTROL)
                sector = ui.input("Sector", value=source.get("sector", "")).classes(FULL_WIDTH_CONTROL)
                is_espp = ui.checkbox("ESPP holding", value=bool(source.get("espp_enabled", source.get("is_espp", False))))
                hd_notice = ui.label("HD production rules enforce zero regular/special contributions, ESPP enabled, a 15% discount, and a six-month purchase cadence. A non-positive ESPP amount uses the $100 fallback.").classes("hidden w-full rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900")
                def apply_hd_presentation(value: Any) -> None:
                    forced = str(value or "").strip().upper() == "HD"
                    controls = (regular, special, discount, frequency, is_espp)
                    if forced:
                        regular.set_value(0); special.set_value(0); discount.set_value(0.15); frequency.set_value(6); is_espp.set_value(True)
                        for control in controls: control.disable()
                        hd_notice.classes(remove="hidden")
                    else:
                        for control in controls: control.enable()
                        hd_notice.classes(add="hidden")
                ticker.on_value_change(lambda event: apply_hd_presentation(event.value))
                apply_hd_presentation(ticker.value)
                busy = {"value": False}
                async def save() -> None:
                    if busy["value"]:
                        return
                    busy["value"] = True
                    save_button.disable()
                    values = {"ticker": ticker.value, "shares": shares.value, "espp_acquired_shares": espp_shares.value, "amount_invested": invested.value, "annual_div_per_share": annual_dividend.value, "regular_biweekly_contribution": regular.value, "espp_contribution_biweekly": espp.value, "special_contribution": special.value, "is_espp": is_espp.value, "espp_discount": discount.value, "espp_purchase_frequency_months": frequency.value, "sector": sector.value, "asset_type": asset_type.value}
                    try:
                        token = uuid.uuid4().hex
                        if existing:
                            await run.io_bound(holdings_writer.edit, int(existing["_holding_id"]), values, submission_token=token)
                        else:
                            await run.io_bound(holdings_writer.add, values, submission_token=token)
                        dialog.close()
                        await refresh_after_holding_write()
                        ui.notify(f"{str(values['ticker']).strip().upper()} saved.", type="positive")
                    except Exception as exc:
                        ui.notify(str(exc), type="negative", timeout=0)
                    finally:
                        busy["value"] = False
                        save_button.enable()
                with ui.element("div").classes(RESPONSIVE_ACTIONS):
                    save_button = ui.button("Save Holding", icon="save", on_click=save).classes("w-full sm:w-auto min-h-11")
                    ui.button("Cancel", on_click=dialog.close).props("flat").classes("w-full sm:w-auto min-h-11")
            def open_holding_dialog(existing: dict[str, Any] | None = None) -> None:
                with ui.dialog() as dialog, ui.card().classes(DIALOG_CARD):
                    ui.label("Edit Holding" if existing else "Add Holding").classes("text-xl font-semibold")
                    holding_form(dialog, existing)
                dialog.open()
            ui.button("Add Holding", icon="add", on_click=lambda: open_holding_dialog()).classes("w-full sm:w-auto min-h-11")
            metric_cards([("Portfolio value", currency(summary["portfolio_value"])), ("Cost basis", currency(summary["cost_basis"])), ("Unrealized gain/loss", currency(summary["unrealized_gain"])), ("Estimated annual income", currency(summary["annual_income"])), ("Weighted yield", percent(summary["weighted_yield_pct"])), ("Holdings", format_count(summary["holding_count"])), ("Largest holding", str(summary["largest_holding"])), ("Largest weight", percent(summary["largest_weight_pct"]))])
            contribution_totals = summarize_contributions(holdings)
            with ui.expansion("Contribution Settings", value=True).classes("w-full"):
                ui.label("Regular and ESPP amounts repeat across 26 pay periods each year. Special contributions are one-time amounts and are never annualized.").classes("w-full rounded-lg bg-blue-50 p-3 text-sm text-blue-900 break-words")
                metric_cards([
                    ("Regular biweekly", currency(contribution_totals["regular_biweekly"])),
                    ("Regular annual", currency(contribution_totals["regular_annual"])),
                    ("ESPP biweekly", currency(contribution_totals["espp_biweekly"])),
                    ("ESPP annual", currency(contribution_totals["espp_annual"])),
                    ("Combined recurring biweekly", currency(contribution_totals["combined_biweekly"])),
                    ("Combined recurring annual", currency(contribution_totals["combined_annual"])),
                    ("Special contributions", currency(contribution_totals["special_contribution"])),
                ])
                def open_contribution_dialog(stored: dict[str, Any]) -> None:
                    with ui.dialog() as dialog, ui.card().classes(DIALOG_CARD):
                        ticker_text = str(stored.get("ticker", ""))
                        ui.label(f"Contribution Settings · {ticker_text}").classes("text-xl font-semibold")
                        ui.label("Special contribution is a separate amount and is not annualized.").classes("text-sm text-gray-500")
                        regular = ui.number("Regular biweekly contribution", value=stored.get("regular_biweekly_contribution", 0.0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                        special = ui.number("Special contribution (not recurring)", value=stored.get("special_contribution", 0.0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                        espp_amount = ui.number("ESPP biweekly contribution", value=stored.get("espp_biweekly_contribution", 0.0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                        discount = ui.number("ESPP discount (%)", value=stored.get("espp_discount_pct", 0.0), min=0, max=100, step=0.01).classes(FULL_WIDTH_CONTROL)
                        frequency = ui.number("ESPP purchase frequency (months)", value=stored.get("espp_purchase_frequency_months", 6.0), min=1, step=1).classes(FULL_WIDTH_CONTROL)
                        is_espp = ui.checkbox("ESPP enabled", value=bool(stored.get("espp_enabled", stored.get("is_espp", False))))
                        if ticker_text.strip().upper() == "HD":
                            ui.label("HD always uses zero regular/special contributions, a 15% discount, and a six-month cadence. A non-positive ESPP amount uses the $100 production fallback.").classes("rounded bg-amber-50 p-3 text-sm text-amber-900")
                            regular.set_value(0); special.set_value(0); discount.set_value(15); frequency.set_value(6); is_espp.set_value(True)
                            for control in (regular, special, discount, frequency, is_espp): control.disable()
                        busy = {"value": False}
                        async def save_contributions() -> None:
                            if busy["value"]:
                                return
                            busy["value"] = True
                            save_button.disable()
                            values = {"regular_biweekly_contribution": regular.value, "special_contribution": special.value, "espp_contribution_biweekly": espp_amount.value, "espp_discount": discount.value, "espp_purchase_frequency_months": frequency.value, "is_espp": is_espp.value}
                            try:
                                await run.io_bound(holdings_writer.update_contributions, int(stored["_holding_id"]), values, submission_token=uuid.uuid4().hex)
                                dialog.close()
                                await refresh_after_holding_write()
                                ui.notify(f"{ticker_text} contributions saved.", type="positive")
                            except Exception as exc:
                                ui.notify(str(exc), type="negative", timeout=0)
                            finally:
                                busy["value"] = False
                                save_button.enable()
                        with ui.element("div").classes(RESPONSIVE_ACTIONS):
                            save_button = ui.button("Save Contributions", icon="save", on_click=save_contributions).classes("w-full sm:w-auto min-h-11")
                            ui.button("Cancel", on_click=dialog.close).props("flat").classes("w-full sm:w-auto min-h-11")
                    dialog.open()
                with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3"):
                    for stored in holdings:
                        with ui.card().classes("w-full"):
                            ui.label(str(stored.get("ticker", ""))).classes("text-lg font-semibold")
                            ui.label(f"Regular {currency(stored.get('regular_biweekly_contribution', 0))} · ESPP {currency(stored.get('espp_biweekly_contribution', 0))} · Special {currency(stored.get('special_contribution', 0))}").classes("text-sm text-gray-600")
                            ui.button("Edit Contributions", icon="payments", on_click=lambda row=stored: open_contribution_dialog(row)).classes("w-full sm:w-auto min-h-11")
                if not holdings:
                    ui.label("Add a holding to configure recurring or special contributions.").classes(EMPTY_STATE)
            fields = (("ticker", "Ticker"), ("shares", "Shares"), ("current_price", "Current Price"), ("market_value", "Market Value"), ("average_cost", "Average Cost"), ("cost_basis", "Cost Basis"), ("unrealized_gain", "Unrealized G/L"), ("dividend_yield_pct", "Dividend Yield %"), ("annual_income", "Annual Income"), ("asset_type", "Asset Type"), ("sector", "Sector"), ("regular_biweekly_contribution", "Regular Biweekly"), ("espp_biweekly_contribution", "ESPP Biweekly"), ("special_contribution", "Special Contribution"), ("employer_stock", "Employer Stock"), ("espp_enabled", "ESPP"))
            selected_rows = [{key: row.get(key) for key, _ in fields} for row in display_holdings]
            columns, rows = format_table_records(selected_rows)
            labels = dict(fields)
            for column in columns:
                column["label"] = labels.get(column["name"], column["label"])
            if rows:
                with ui.element("div").classes(SCROLL_TABLE):
                    ui.table(columns=columns, rows=rows, row_key="ticker", pagination=25).classes("w-full min-w-max").props("dense flat bordered")
            else:
                ui.label("Your portfolio has no holdings yet. Use Add Holding to create the first one.").classes(EMPTY_STATE)
            with ui.expansion("Edit or delete a holding").classes("w-full"):
                with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3"):
                    for stored in holdings:
                        with ui.card().classes("w-full"):
                            ui.label(str(stored.get("ticker", ""))).classes("text-lg font-semibold")
                            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                                ui.button("Edit", icon="edit", on_click=lambda row=stored: open_holding_dialog(row)).classes("w-full sm:w-auto min-h-11")
                                def confirm_delete(row: dict[str, Any] = stored) -> None:
                                    with ui.dialog() as confirm, ui.card().classes(CONFIRM_DIALOG_CARD):
                                        ui.label(f"Delete {row.get('ticker', '')} from the portfolio?").classes("text-lg font-semibold")
                                        ui.label("This removes only the current holding. Statement history is not changed.").classes("text-sm text-gray-600")
                                        deleting = {"value": False}
                                        async def remove() -> None:
                                            if deleting["value"]:
                                                return
                                            deleting["value"] = True
                                            delete_button.disable()
                                            try:
                                                await run.io_bound(holdings_writer.delete, int(row["_holding_id"]), confirmed=True, expected_ticker=str(row.get("ticker", "")), submission_token=uuid.uuid4().hex)
                                                confirm.close()
                                                await refresh_after_holding_write()
                                                ui.notify(f"{row.get('ticker', '')} deleted.", type="positive")
                                            except Exception as exc:
                                                ui.notify(str(exc), type="negative", timeout=0)
                                            finally:
                                                deleting["value"] = False
                                                delete_button.enable()
                                        with ui.element("div").classes(RESPONSIVE_ACTIONS):
                                            delete_button = ui.button("Delete Holding", icon="delete", color="negative", on_click=remove).classes("w-full sm:w-auto min-h-11")
                                            ui.button("Cancel", on_click=confirm.close).props("flat").classes("w-full sm:w-auto min-h-11")
                                    confirm.open()
                                ui.button("Delete", icon="delete", color="negative", on_click=confirm_delete).classes("w-full sm:w-auto min-h-11")
                if not holdings:
                    ui.label("There are no holdings to edit or delete.").classes(EMPTY_STATE)
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

    async def render_statement_history() -> None:
        if loaded["Statement History"]:
            return
        area = areas["Statement History"]
        area.clear()
        with area:
            ui.spinner(size="lg")
            ui.label("Loading statement history status…")
        try:
            state = await run.io_bound(statement_service.state)
        except Exception as exc:
            area.clear()
            with area:
                ui.label("Statement history status could not be loaded.").classes("text-red-600")
                ui.label(str(exc)).classes("text-sm text-gray-600 break-words")
            loaded["Statement History"] = True
            return
        area.clear()
        folder = state["folder"]
        continuity = state["monthly_continuity"]
        diagnostics = state["diagnostics"]
        records = state["records"]
        manual_records = [row for row in records if row.get("source_type") == "manual_statement_review"]
        with area:
            ui.label("Statement History").classes("text-2xl font-semibold")
            ui.label("Scanning and importing occur only when explicitly requested. Original PDFs are never moved or deleted.").classes("text-gray-500")
            metric_cards([
                ("Folder", str(folder["status"])),
                ("PDFs detected", format_count(state["detected_pdf_count"] or folder["pdf_count"])),
                ("Unique PDFs", format_count(state["unique_pdf_count"])),
                ("Processed unique", f"{state['processed_unique_count']} of {state['unique_pdf_count']}"),
                ("Duplicate files", format_count(state["duplicate_file_count"])),
                ("Ready", format_count(state["ready"])),
                ("Needs review", format_count(state["needs_review"])),
                ("Unparseable", format_count(state["unparseable"])),
                ("Missing months", format_count(continuity["missing_month_count"])),
                ("Duplicate months", format_count(continuity["duplicate_month_count"])),
                ("Monthly continuity", "Complete" if continuity["complete"] else "Needs review"),
                ("Previously imported", format_count(state["history_summary"]["imported"])),
            ])
            detail_cards([
                ("Accounted for", f"{state['processed_count']} of {state['detected_pdf_count'] or folder['pdf_count']}"),
                ("Remaining", max((state["detected_pdf_count"] or folder["pdf_count"]) - state["processed_count"], 0)),
                ("Scan status", state["scan_status"]),
                ("Header markers found", diagnostics["header_found"]),
                ("Nearby date ranges found", diagnostics["nearby_date_range_found"]),
                ("Nearby account markers found", diagnostics["nearby_account_marker_found"]),
                ("Account summaries found", diagnostics["account_summary_found"]),
                ("Balance rows found", diagnostics["balance_rows_found"]),
                ("Accounting equations passed", diagnostics["accounting_equation_passed"]),
            ])
            action_busy = {"value": False}
            async def reload_statement_area() -> None:
                loaded["Statement History"] = False
                await render_statement_history()
            async def scan_statements(restart: bool) -> None:
                if action_busy["value"]:
                    return
                action_busy["value"] = True
                scan_button.disable()
                resume_button.disable()
                scan_status.set_text("Scanning statements…")
                try:
                    await run.io_bound(statement_service.scan, restart=restart)
                    ui.notify("Statement scan completed.", type="positive")
                    await reload_statement_area()
                except Exception as exc:
                    scan_status.set_text("The statement scan could not complete. Prior scan results were retained where production supports resume.")
                    ui.notify(str(exc), type="negative", timeout=0)
                finally:
                    action_busy["value"] = False
                    scan_button.enable()
                    if state["scan_status"] in {"interrupted", "running"}:
                        resume_button.enable()
            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                scan_button = ui.button("Scan Statements", icon="document_scanner", on_click=lambda: scan_statements(True)).classes("w-full sm:w-auto min-h-11")
                resume_button = ui.button("Resume Scan", icon="play_arrow", on_click=lambda: scan_statements(False)).classes("w-full sm:w-auto min-h-11")
                if state["scan_status"] not in {"interrupted", "running"}:
                    resume_button.disable()
                async def clear_preview() -> None:
                    await run.io_bound(statement_service.clear_scan)
                    await reload_statement_area()
                ui.button("Clear Scan Preview", icon="clear", on_click=clear_preview).classes("w-full sm:w-auto min-h-11")
            scan_status = ui.label("").classes("text-sm text-gray-600")
            if records:
                records_table(statement_display_records(records), "Scan results")
            else:
                ui.label("No statement scan has been run. Statements are scanned only when you choose Scan Statements.").classes(EMPTY_STATE)

            def review_dialog(existing: dict[str, Any] | None = None) -> None:
                missing = list(continuity.get("missing_months", []))
                existing_month = str(existing.get("review_month", "")) if existing else ""
                options = sorted(set(missing + ([existing_month] if existing_month else [])))
                if not options:
                    ui.notify("There are no missing months available for manual review.", type="warning")
                    return
                selected_default = existing_month or options[0]
                year, month = (int(part) for part in selected_default.split("-"))
                default_date = str(existing.get("statement_end_date")) if existing else date(year, month, calendar.monthrange(year, month)[1]).isoformat()
                with ui.dialog() as dialog, ui.card().classes(DIALOG_CARD):
                    ui.label("Edit Manual Statement Review" if existing else "Manual Statement Review").classes("text-xl font-semibold")
                    ui.label("Enter only values verified against the original statement. Total Priced Portfolio plus Net Account Balance must equal Total Equity Holdings.").classes("text-sm text-gray-600")
                    review_month = ui.select(options, value=selected_default, label="Missing month").classes(FULL_WIDTH_CONTROL)
                    ending_date = ui.input("Statement ending date (YYYY-MM-DD)", value=default_date).classes(FULL_WIDTH_CONTROL)
                    invested = ui.number("Closing Total Priced Portfolio", value=float(existing.get("invested_value", 0) if existing else 0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    cash = ui.number("Closing Net Account Balance", value=float(existing.get("cash_value", 0) if existing else 0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    total = ui.number("Closing Total Equity Holdings", value=float(existing.get("portfolio_value", 0) if existing else 0), min=0, step=0.01).classes(FULL_WIDTH_CONTROL)
                    confirmed = ui.checkbox("I verified these values against the original statement.", value=bool(existing.get("confirmed", False)) if existing else False)
                    saving = {"value": False}
                    async def save_review() -> None:
                        if saving["value"]:
                            return
                        saving["value"] = True
                        save_button.disable()
                        try:
                            await run.io_bound(statement_service.save_review, str(review_month.value), str(ending_date.value), float(invested.value), float(cash.value), float(total.value), bool(confirmed.value), manual_id=str(existing["source_hash"]) if existing else None)
                            dialog.close()
                            ui.notify("Manual review saved as Ready.", type="positive")
                            await reload_statement_area()
                        except Exception as exc:
                            ui.notify(str(exc), type="negative", timeout=0)
                        finally:
                            saving["value"] = False
                            save_button.enable()
                    with ui.element("div").classes(RESPONSIVE_ACTIONS):
                        save_button = ui.button("Save Manual Review", icon="save", on_click=save_review).classes("w-full sm:w-auto min-h-11")
                        ui.button("Cancel", on_click=dialog.close).props("flat").classes("w-full sm:w-auto min-h-11")
                dialog.open()
            with ui.expansion("Manual Statement Review").classes("w-full"):
                missing_months = continuity.get("missing_months", [])
                ui.label("Missing months: " + (", ".join(missing_months) if missing_months else "None")).classes("text-sm break-words")
                ui.button("Add Manual Review", icon="fact_check", on_click=lambda: review_dialog()).classes("w-full sm:w-auto min-h-11")
                for manual in manual_records:
                    with ui.card().classes("w-full"):
                        ui.label(str(manual.get("review_month", ""))).classes("font-semibold")
                        ui.label(f"{currency(manual.get('portfolio_value'))} · {manual.get('warning', '')}").classes("text-sm text-gray-600 break-words")
                        with ui.element("div").classes(RESPONSIVE_ACTIONS):
                            ui.button("Edit Review", icon="edit", on_click=lambda row=manual: review_dialog(row)).classes("w-full sm:w-auto min-h-11")
                            async def remove_review(row: dict[str, Any] = manual) -> None:
                                await run.io_bound(statement_service.remove_review, str(row["source_hash"]))
                                await reload_statement_area()
                            ui.button("Remove Review", icon="delete", on_click=remove_review).classes("w-full sm:w-auto min-h-11")

            async def import_ready() -> None:
                if action_busy["value"]:
                    return
                action_busy["value"] = True
                import_button.disable()
                try:
                    result = await run.io_bound(statement_service.import_ready, submission_token=uuid.uuid4().hex)
                    await refresh_history_after_statement_write()
                    ui.notify(f"Imported {int((result or {}).get('imported', 0))} statement history records.", type="positive")
                    await reload_statement_area()
                except Exception as exc:
                    ui.notify(str(exc), type="negative", timeout=0)
                finally:
                    action_busy["value"] = False
                    import_button.enable()
            import_button = ui.button("Import Ready Statements", icon="publish", on_click=import_ready).classes("w-full sm:w-auto min-h-11")
            if not state["can_import"]:
                import_button.disable()
                ui.label("Import remains disabled until the scan is complete, all PDFs are accounted for, and monthly continuity is complete.").classes("text-sm text-amber-700")

            def confirm_delete_history() -> None:
                with ui.dialog() as dialog, ui.card().classes(CONFIRM_DIALOG_CARD):
                    ui.label("Delete all imported statement history?").classes("text-lg font-semibold")
                    ui.label("Original PDFs and holdings will not be changed. Live snapshots remain intact.").classes("text-sm text-gray-600")
                    async def remove_imported() -> None:
                        await run.io_bound(statement_service.delete_imported, confirmed=True)
                        dialog.close()
                        await refresh_history_after_statement_write()
                        await reload_statement_area()
                    with ui.element("div").classes(RESPONSIVE_ACTIONS):
                        ui.button("Delete Imported History", icon="delete_forever", color="negative", on_click=remove_imported).classes("w-full sm:w-auto min-h-11")
                        ui.button("Cancel", on_click=dialog.close).props("flat").classes("w-full sm:w-auto min-h-11")
                dialog.open()
            ui.button("Delete Imported History…", icon="delete", on_click=confirm_delete_history).classes("w-full sm:w-auto min-h-11")
        loaded["Statement History"] = True

    async def render_supplemental_research(force: bool = False) -> None:
        if loaded["Supplemental Research"] and not force:
            return
        area = areas["Supplemental Research"]
        area.clear()
        metric_records = metrics.to_dict(orient="records") if metrics is not None else []
        contexts = research_contexts(holdings, metric_records)
        try:
            package = await run.io_bound(research_service.state, contexts)
        except Exception as exc:
            with area:
                ui.label("Supplemental Research").classes("text-2xl font-semibold")
                ui.label("Research state could not be loaded.").classes("text-red-700")
                ui.label(str(exc)).classes("text-sm break-words")
            loaded["Supplemental Research"] = True
            return
        with area:
            ui.label("Supplemental Research").classes("text-2xl font-semibold")
            ui.label("Yahoo remains the primary financial-data source. Research runs only after an explicit action and supplements missing descriptive context.").classes("text-gray-500")
            metric_cards([
                ("Active holdings", format_count(len(package["active"]))),
                ("Enrolled", format_count(sum(bool(row["enrolled"]) for row in package["active"]))),
                ("Archived", format_count(len(package["archived"]))),
                ("Research expiry", f'{package["expiry_days"]} days'),
                ("Failure retry", f'{package["retry_hours"]} hours'),
                ("Worker check", f'{package["worker_check_hours"]} hours'),
            ])
            with ui.card().classes("w-full min-w-0"):
                ui.label("Research Settings").classes("text-lg font-semibold")
                ui.label("Only the production supplemental-research model setting is managed here. The existing background worker and cadence are unchanged.").classes("text-sm text-gray-500")
                model = ui.select(package["models"], value=package["model"], label="Supplemental Research Model").classes(FULL_WIDTH_CONTROL)
                async def save_model() -> None:
                    try:
                        await run.io_bound(research_service.set_model, str(model.value or ""))
                        ui.notify("Supplemental research model saved.", type="positive")
                    except Exception as exc:
                        ui.notify(str(exc), type="negative", timeout=0)
                ui.button("Save Research Model", icon="save", on_click=save_model).classes("w-full sm:w-auto min-h-11")

            async def update_ticker(ticker: str, button: Any) -> None:
                button.disable()
                try:
                    result = await run.io_bound(research_service.update, ticker, contexts[ticker], str(model.value or ""))
                    if result.get("status") == "current":
                        ui.notify(f"Supplemental research completed for {ticker}.", type="positive")
                    elif result.get("reason") == "already running":
                        ui.notify(f"Research for {ticker} is already running.", type="warning")
                    else:
                        ui.notify(str(result.get("error") or result.get("reason") or "Research did not complete."), type="negative", timeout=0)
                    await render_supplemental_research(True)
                except Exception as exc:
                    ui.notify(str(exc), type="negative", timeout=0)
                    button.enable()

            ui.label("Portfolio Holdings").classes("text-xl font-semibold mt-2")
            if not package["active"]:
                ui.label("Add a holding before requesting supplemental research.").classes("text-gray-500")
            with ui.element("div").classes("w-full grid grid-cols-1 lg:grid-cols-2 gap-3"):
                for row in package["active"]:
                    ticker = row["ticker"]
                    with ui.card().classes("w-full min-w-0 overflow-hidden"):
                        with ui.row().classes("w-full flex-wrap items-center justify-between gap-2"):
                            ui.label(ticker).classes("text-xl font-semibold")
                            ui.badge(row["status"], color="positive" if row["status"] == "Current" else "warning")
                        detail_cards([
                            ("Enrollment", "Enrolled" if row["enrolled"] else "Manual research required"),
                            ("Last success", row["last_success_at"]),
                            ("Next refresh", row["next_due_at"]),
                            ("Model", row["model_used"]),
                            ("Verified facts", row["verified_facts"]),
                        ])
                        if row["error"]:
                            ui.label(f'Latest attempt: {row["error"]}').classes("w-full rounded bg-red-50 p-2 text-sm text-red-800 break-words")
                        action = ui.button("Refresh Research" if row["enrolled"] else "Enroll & Research", icon="science").classes("w-full sm:w-auto min-h-11")
                        action.on_click(lambda _event, t=ticker, b=action: update_ticker(t, b))
                        if not row["can_update"]:
                            action.disable()
                        if row["research_markdown"]:
                            with ui.expansion("Existing Research", icon="description").classes("w-full"):
                                ui.markdown(row["research_markdown"]).classes(SAFE_RICH_OUTPUT)
            if package["archived"]:
                ui.label("Archived Research").classes("text-xl font-semibold mt-2")
                ui.label("Production retains research when a holding leaves the portfolio. Re-adding that ticker restores it through the normal holding synchronization path.").classes("text-sm text-gray-500")
                records_table([{key: row[key] for key in ("ticker", "status", "last_success_at", "model_used", "verified_facts")} for row in package["archived"]])
            with ui.expansion("Background Worker Status", icon="schedule").classes("w-full"):
                ui.label(f'The existing worker checks every {package["worker_check_hours"]} hours. This page does not start or schedule a worker.').classes("text-sm text-gray-500")
                records_table(package["jobs"])
        loaded["Supplemental Research"] = True

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
                                    ui.echart({"grid": {"left": 48, "right": 12, "top": 20, "bottom": 36, "containLabel": True}, "xAxis": {"type": "category", "data": list(monthly)}, "yAxis": {"type": "value"}, "series": [{"name": "Estimated income", "type": "bar", "data": list(monthly.values())}]}).classes("w-full min-w-0 h-64 sm:h-80")
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
            with ui.card().classes("w-full min-w-0 min-h-32 overflow-hidden"):
                ui.label("Latest analysis").classes("text-lg font-semibold")
                output = ui.markdown("No analysis has been run yet. Choose an analysis type and select Analyze when you are ready.").classes(SAFE_RICH_OUTPUT)
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
                output.set_content("No analysis has been run yet. Choose an analysis type and select Analyze when you are ready.")
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
                ui.label(str(package.get("disclaimer", "Tracker-generated and not an objective investment rating."))).classes("w-full rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-medium text-amber-900 break-words")
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
                        ui.label("No portfolio history is available yet. Import verified statements or wait for a live snapshot.").classes(EMPTY_STATE)
                        return
                    summary = package["summary"]
                    metric_cards([("Starting value", currency(summary["starting_portfolio_value"])), ("Ending value", currency(summary["ending_portfolio_value"])), ("Value change", currency(summary["absolute_change"])), ("Value change %", percent(summary["percentage_change"])), ("History points", format_count(summary["point_count"]))])
                    ui.echart({"tooltip": {"trigger": "axis"}, "grid": {"left": 56, "right": 16, "top": 24, "bottom": 48, "containLabel": True}, "xAxis": {"type": "category", "data": package["series"]["dates"], "axisLabel": {"hideOverlap": True}}, "yAxis": {"type": "value", "scale": True, "axisLabel": {"formatter": "${value}"}}, "series": [{"name": "Portfolio value", "type": "line", "data": package["series"]["portfolio_values"], "areaStyle": {"opacity": 0.12}}]}).classes("w-full min-w-0 h-72 sm:h-96")
            selector.on_value_change(lambda event: update_chart(str(event.value)))
            update_chart(selected_range)
        loaded["History"] = True

    def render_system() -> None:
        if loaded["System"]:
            return
        package = operational_status(
            coordinator.portfolio_path,
            statement_service.source_dir,
            coordinator.history_path,
            application_port=PORT,
            database_refresh_interval_seconds=DB_REFRESH_INTERVAL_SECONDS,
        )
        portfolio_db = package["portfolio_database"]
        storage = package["statement_storage"]
        statement_folder = storage["folder"]
        history_db = storage["history_database"]
        application = package["application"]
        area = areas["System"]
        with area:
            ui.label("System").classes("text-2xl font-semibold")
            ui.label("Read-only application and local storage status. Use the global Refresh Data action to refresh portfolio data.").classes("text-gray-500")
            ui.label("Runtime").classes("text-lg font-semibold mt-2")
            detail_cards([
                ("Application", application["framework"]),
                ("Application port", application["port"]),
                ("Database refresh interval", f'{application["database_refresh_interval_seconds"]} seconds'),
            ])
            ui.label("Portfolio database").classes("text-lg font-semibold mt-2")
            detail_cards([
                ("Status", portfolio_db["status"]),
                ("Configured path", portfolio_db["path"]),
            ])
            ui.label("Statement storage").classes("text-lg font-semibold mt-2")
            detail_cards([
                ("Statements folder", statement_folder["status"]),
                ("Statements folder path", statement_folder["path"]),
                ("PDFs detected", format_count(statement_folder["pdf_count"])),
                ("Statement history database", history_db["status"]),
                ("Statement history database path", history_db["path"]),
            ])
            ui.label("Cache behavior").classes("text-lg font-semibold mt-2")
            ui.label("These intervals are informational. Cache refreshes continue to use the application’s existing behavior.").classes("text-gray-500")
            detail_cards([(name, format_interval(seconds)) for name, seconds in package["cache_intervals_seconds"].items()])
        loaded["System"] = True

    async def navigate(event: Any) -> None:
        name = str(event.value)
        if mobile_navigation.value != name:
            mobile_navigation.set_value(name)
        if name == "Portfolio Analysis": await render_analysis()
        elif name == "AI Analysis": render_ai()
        elif name == "Portfolio Insights": await render_insights()
        elif name == "Portfolio Health": await render_health()
        elif name == "History" and not loaded["History"]: render_history()
        elif name == "Statement History": await render_statement_history()
        elif name == "Supplemental Research": await render_supplemental_research()
        elif name == "System": render_system()

    tabs.on_value_change(navigate)
    mobile_navigation.on_value_change(lambda event: tabs.set_value(str(event.value)))
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
    async def refresh_after_holding_write() -> None:
        """Reload writes and invalidate every holdings-derived lazy view."""
        await refresh_data(True)
        for name in ("Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "Supplemental Research"):
            loaded[name] = False
            areas[name].clear()
            with areas[name]:
                ui.label("Holdings changed; prior prepared output is stale and this section will refresh when opened.").classes("text-gray-500")
    async def refresh_history_after_statement_write() -> None:
        """Reload only history-derived state; do not reload holdings or Yahoo data."""
        nonlocal history
        history = await run.io_bound(statement_service.performance_history)
        if loaded["History"]:
            render_history(history_range)
        loaded["AI Analysis"] = False
        areas["AI Analysis"].clear()
        with areas["AI Analysis"]:
            ui.label("Statement history changed; prior AI history context is stale and will refresh when opened.").classes("text-gray-500")
        status.set_text(f"History refreshed with {len(history)} points; holdings were not reloaded")
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

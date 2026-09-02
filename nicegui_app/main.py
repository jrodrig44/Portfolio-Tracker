from __future__ import annotations

import os
import sys
import uuid
import calendar
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_environment import ai_provider_status, initialize_project_environment

ENVIRONMENT_STATUS = initialize_project_environment(PROJECT_ROOT)

from nicegui import app, run, ui
from nicegui_app.process_lifecycle import ShutdownInProgress, lifecycle, start_launcher_watchdog, start_shutdown_request_watcher
from nicegui_app.research_worker_lifecycle import start_research_worker, stop_research_worker
from nicegui_app.market_refresh_worker import start_market_refresh_worker, stop_market_refresh_worker
from nicegui_app.services.ai_context import AIAnalysisState,ai_options,run_ai_analysis
from nicegui_app.services.ai_snapshot import AIAnalysisSnapshotService, friendly_utc_timestamp, resolve_analysis_download, save_successful_analysis_snapshot
from nicegui_app.services.appearance import APPEARANCE_OPTIONS, APPEARANCE_STORAGE_KEY, restore_appearance
from nicegui_app.services.analysis import analysis_package
from nicegui_app.services.configuration import DB_REFRESH_INTERVAL_SECONDS
from nicegui_app.services.exports import YahooExportEnrichmentState,export_package
from nicegui_app.services.formatting import CONFIRM_DIALOG_CARD, DIALOG_CARD, EMPTY_STATE, FULL_WIDTH_CONTROL, RESPONSIVE_ACTIONS, RESPONSIVE_CARD_GRID, RICH_OUTPUT_CSS, SAFE_RICH_OUTPUT, SCROLL_TABLE, SNAPSHOT_RICH_OUTPUT, SNAPSHOT_VIEWER_BODY, SNAPSHOT_VIEWER_CARD, SYSTEM_CARD, SYSTEM_CARD_GRID, SYSTEM_LABEL, SYSTEM_PATH_VALUE, SYSTEM_VALUE, TICKER_CHART_BODY, TICKER_CHART_CARD, dividend_seasonality_chart_options, format_count, format_currency, format_field_value, format_percentage, format_score, format_status, format_table_records, human_label
from nicegui_app.services.health import health_package
from nicegui_app.services.history import history_package
from nicegui_app.services.insights import insights_package
from nicegui_app.services.portfolio_data import DEFAULT_HISTORY_RANGE,HISTORY_RANGE_OPTIONS,portfolio_summary
from nicegui_app.services.page_runtime import PageRefreshGuard, client_is_alive
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator
from nicegui_app.services.ui_state import (
    NAVIGATION_AREAS,
    PageUIState,
    canonical_navigation,
    invalidate_section_views,
    refresh_view_plan,
)
from nicegui_app.services.yahoo import clear_market_data_cache,current_market_data,yahoo_intelligence_package
from nicegui_app.services.ticker_chart import DEFAULT_TICKER_CHART_RANGE, TICKER_CHART_RANGES, load_ticker_chart
from portfolio_holdings_write import ASSET_TYPES, HoldingsWriteService, summarize_contributions
from nicegui_app.services.operations import format_interval, operational_status
from portfolio_research import PortfolioResearchService, research_contexts
from portfolio_statement_history import StatementHistoryService, statement_scan_diagnostic_records, statement_verified_display_records
from hd_espp_analytics import analyze_hd_economics
from hd_espp_history import HDESPPHistoryService, transaction_record_type
from hd_ownership import SHARE_TOLERANCE, reconstruct_hd_ownership
from hd_espp_reconciliation import build_hd_phase3a_reconciliation
from shared_data_preparation import prepare_shared_data

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORTFOLIO_TRACKER_PORT", "8501"))
STORAGE_SECRET = os.environ.get("NICEGUI_STORAGE_SECRET", "portfolio-tracker-local-appearance-storage")
STATIC_DIR = Path(__file__).resolve().parent / "static"
FAVICON_PATH = STATIC_DIR / "favicon.ico"
APPLE_TOUCH_ICON_PATH = STATIC_DIR / "apple-touch-icon.png"
# Browser favicons, Apple filename probes, and the PWA manifest all stay on the
# Portfolio Tracker origin. Multiple Apple aliases deliberately serve one file;
# relative routes need neither device sniffing nor a separate static service.
APPLE_TOUCH_ICON_ROUTES = (
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
    "/apple-touch-icon-120x120.png",
    "/apple-touch-icon-120x120-precomposed.png",
    "/apple-touch-icon-152x152.png",
    "/apple-touch-icon-152x152-precomposed.png",
    "/apple-touch-icon-167x167.png",
    "/apple-touch-icon-167x167-precomposed.png",
    "/apple-touch-icon-180x180.png",
    "/apple-touch-icon-180x180-precomposed.png",
)
HOLDING_EDITABLE_FIELDS = (
    "ticker", "shares", "espp_acquired_shares", "amount_invested",
    "annual_div_per_share", "regular_biweekly_contribution",
    "espp_contribution_biweekly", "special_contribution", "is_espp",
    "espp_discount", "espp_purchase_frequency_months", "sector", "asset_type",
)
HOLDING_FIELD_LABELS = {
    "ticker": "Ticker", "shares": "Shares", "espp_acquired_shares": "ESPP-Acquired Shares",
    "amount_invested": "Amount Invested", "annual_div_per_share": "Annual Div/Share (Manual Override)",
    "regular_biweekly_contribution": "Regular Bi-Weekly Contribution",
    "espp_contribution_biweekly": "ESPP Bi-Weekly Contribution", "special_contribution": "Special Contribution",
    "is_espp": "ESPP Holding", "espp_discount": "ESPP Discount (decimal)",
    "espp_purchase_frequency_months": "ESPP Purchase Frequency (months)", "sector": "Sector", "asset_type": "Asset Type",
}
HOLDING_NUMERIC_FIELDS = set(HOLDING_EDITABLE_FIELDS) - {"ticker", "sector", "asset_type", "is_espp"}
HOLDING_NUMERIC_VALUE_PARSER = (
    "params => { const input = String(params.newValue).trim(); "
    "const parsed = Number(input); return input !== '' && Number.isFinite(parsed) ? parsed : params.oldValue; }"
)

app.on_startup(start_research_worker)
app.on_shutdown(stop_research_worker)
app.on_startup(start_market_refresh_worker)
app.on_shutdown(stop_market_refresh_worker)
app.add_static_files("/branding", STATIC_DIR)
app.add_static_file(local_file=FAVICON_PATH, url_path="/favicon.ico")
for icon_route in APPLE_TOUCH_ICON_ROUTES:
    app.add_static_file(local_file=APPLE_TOUCH_ICON_PATH, url_path=icon_route)


def currency(value: Any) -> str:
    return format_currency(value)


def percent(value: Any) -> str:
    return format_percentage(value)


def metric_cards(items: list[tuple[str, str]]) -> None:
    with ui.element("div").classes(RESPONSIVE_CARD_GRID):
        for label, value in items:
            with ui.card().classes("w-full min-w-0"):
                ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400")
                ui.label(value).classes("text-xl sm:text-2xl font-semibold break-words")


def detail_cards(items: list[tuple[str, Any]]) -> None:
    with ui.element("div").classes(RESPONSIVE_CARD_GRID):
        for label, value in items:
            with ui.card().classes("w-full min-w-0 py-3"):
                ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400")
                ui.label(str(value)).classes("text-base font-medium break-words")


def system_detail_cards(items: list[tuple[str, Any, bool]]) -> None:
    with ui.element("div").classes(SYSTEM_CARD_GRID):
        for label, value, is_path in items:
            with ui.card().classes(SYSTEM_CARD):
                ui.label(label).classes(SYSTEM_LABEL)
                ui.label(str(value)).classes(SYSTEM_PATH_VALUE if is_path else SYSTEM_VALUE)


def guidance_text(text: Any) -> None:
    if str(text or "").strip():
        ui.label(str(text).strip()).classes("w-full rounded-lg bg-blue-50 p-3 text-sm leading-relaxed text-blue-900 dark:bg-blue-950 dark:text-blue-100")


def records_table(records: list[dict[str, Any]], title: str | None = None) -> None:
    if title:
        ui.label(title).classes("text-lg font-semibold mt-3")
    if not records:
        ui.label("No records are available for this section yet.").classes(EMPTY_STATE)
        return
    columns, rows = format_table_records(records)
    if not columns:
        ui.label("No displayable values are available.").classes("text-gray-500 dark:text-gray-400")
        return
    with ui.element("div").classes(SCROLL_TABLE):
        ui.table(columns=columns, rows=rows, pagination=15).classes("w-full min-w-max").props("dense flat bordered")


async def critical_io(name: str, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one admitted mutation to completion, including its blocking worker."""
    try:
        with lifecycle.critical_operation(name):
            return await run.io_bound(operation, *args, **kwargs)
    except ShutdownInProgress as exc:
        ui.notify(str(exc), type="warning")
        raise


async def current_aggrid_rows(grid: Any) -> list[dict[str, Any]]:
    """Commit the active editor and return the browser's authoritative row data."""
    await grid.run_grid_method("stopEditing")
    rows = await grid.get_client_data(method="all_unsorted")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("Holdings grid data could not be read from the browser. Please try again.")
    return rows


def new_holding_grid_row() -> dict[str, Any]:
    return {
        "_holding_id": None, "_grid_key": f"new-{uuid.uuid4().hex}", "ticker": "", "shares": 0.0, "espp_acquired_shares": 0.0,
        "amount_invested": 0.0, "annual_div_per_share": 0.0,
        "regular_biweekly_contribution": 0.0, "espp_contribution_biweekly": 0.0,
        "special_contribution": 0.0, "is_espp": False, "espp_discount": 0.0,
        "espp_purchase_frequency_months": 6.0, "sector": "", "asset_type": "Stock",
    }


def holding_grid_column_defs() -> list[dict[str, Any]]:
    column_defs: list[dict[str, Any]] = [{"field": "_holding_id", "hide": True}, {"field": "_grid_key", "hide": True}]
    for field in HOLDING_EDITABLE_FIELDS:
        definition: dict[str, Any] = {"field": field, "headerName": HOLDING_FIELD_LABELS[field], "editable": True, "minWidth": 145}
        if field in HOLDING_NUMERIC_FIELDS:
            definition["type"] = "numericColumn"
            definition[":valueParser"] = HOLDING_NUMERIC_VALUE_PARSER
        elif field == "is_espp":
            definition.update({"cellDataType": "boolean", "cellEditor": "agCheckboxCellEditor", "cellRenderer": "agCheckboxCellRenderer"})
        elif field == "asset_type":
            definition.update({"cellEditor": "agSelectCellEditor", "cellEditorParams": {"values": sorted(ASSET_TYPES)}})
        column_defs.append(definition)
    return column_defs


def release_save_guard(saving: dict[str, bool], button: Any, *, ui_will_be_rebuilt: bool) -> None:
    """Release double-submit protection without touching an element about to be deleted."""
    saving["value"] = False
    if not ui_will_be_rebuilt:
        button.enable()


async def add_aggrid_holding_row(grid: Any) -> dict[str, Any]:
    row = new_holding_grid_row()
    with grid.props.suspend_updates():
        grid.options["rowData"].append(row)
    grid.run_grid_method("applyTransaction", {"add": [row]})
    return row


async def remove_selected_aggrid_rows(grid: Any) -> int:
    selected = await grid.get_selected_rows()
    if selected:
        selected_keys = {row["_grid_key"] for row in selected}
        with grid.props.suspend_updates():
            row_data = grid.options["rowData"]
            for index in range(len(row_data) - 1, -1, -1):
                if row_data[index].get("_grid_key") in selected_keys:
                    del row_data[index]
        grid.run_grid_method("applyTransaction", {"remove": selected})
    return len(selected)


@ui.page("/")
async def index() -> None:
    page_client = ui.context.client
    ui.add_head_html(
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        '<link rel="icon" type="image/png" sizes="16x16" href="/branding/favicon-16x16.png">'
        '<link rel="icon" type="image/png" sizes="32x32" href="/branding/favicon-32x32.png">'
        '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">'
        '<link rel="manifest" href="/branding/site.webmanifest">'
        '<meta name="theme-color" content="#071f4d">'
    )
    ui.colors(primary="#1d4ed8", secondary="#0f766e", accent="#d97706")
    appearance_storage = app.storage.user
    appearance_mode = restore_appearance(appearance_storage)
    ui.dark_mode(value=appearance_mode).bind_value(appearance_storage, APPEARANCE_STORAGE_KEY)
    ui.add_css(RICH_OUTPUT_CSS)
    ui.query("body").classes("overflow-x-hidden")
    holdings: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    ui_state = PageUIState(history_range=DEFAULT_HISTORY_RANGE)
    history_range = ui_state.history_range
    metrics = None
    coordinator = ReadOnlyRefreshCoordinator(market_loader=current_market_data)
    refresh_guard = PageRefreshGuard(lifecycle)
    holdings_writer = HoldingsWriteService(coordinator.portfolio_path)
    snapshot_service = AIAnalysisSnapshotService(coordinator.portfolio_path)
    statement_service = StatementHistoryService(db_path=coordinator.history_path)
    hd_history_service = HDESPPHistoryService(db_path=coordinator.history_path)
    research_service = PortfolioResearchService(coordinator.portfolio_path)
    yahoo_export_state = YahooExportEnrichmentState()
    loaded = {name: False for name in NAVIGATION_AREAS}

    async def render_hd_espp_history() -> None:
        if loaded["HD / ESPP History"]:
            return
        area = areas["HD / ESPP History"]
        area.clear()
        state = await run.io_bound(hd_history_service.state)
        summary = state["summary"]
        live_hd_shares = next((row.get("shares") for row in holdings if str(row.get("ticker", "")).upper() == "HD"), None)
        ownership = reconstruct_hd_ownership(state["transactions"], live_hd_shares, state["transfer_lots"])
        ownership_summary = ownership.summary
        current_hd_price = next((row.get("current_price") for row in holdings if str(row.get("ticker", "")).upper() == "HD"), None)
        analytics = analyze_hd_economics(state["transactions"], ownership, live_hd_shares, current_hd_price)
        phase3a = build_hd_phase3a_reconciliation(state["transactions"], state["transfer_lots"], state.get("documents", []), state.get("position_checkpoints", []), ownership, live_hd_shares)
        with area:
            ui.label("HD / ESPP History").classes("text-2xl font-semibold")
            ui.label("Local transaction reconstruction only. Live HD quantity, cost basis, portfolio history, and forward contribution assumptions remain authoritative and unchanged.").classes("text-sm text-gray-600 dark:text-gray-300")
            metric_cards([
                ("Employee ESPP Contributions", currency(analytics.espp_summary["total_employee_contributions"])),
                ("ESPP Shares Purchased", str(analytics.espp_summary["total_espp_shares"])),
                ("Weighted Avg ESPP Purchase Price", currency(analytics.espp_summary["weighted_average_purchase_price"])),
                ("Purchase Discount Benefit", currency(analytics.discount_summary["total_discount_value"])),
                ("DRIP Cash Reinvested", currency(analytics.drip_summary["total_net_reinvested"])),
                ("DRIP Shares Acquired", str(analytics.drip_summary["total_drip_shares"])),
                ("Documented Cash Dividends", currency(analytics.dividend_summary["total_documented_cash_dividends"])),
                ("Known-Basis Realized Gain", currency(analytics.realized_summary["documented_realized_gain"])),
            ])
            if ownership_summary["reconciliation_status"] == "DIFFERENCE_REQUIRES_REVIEW":
                ui.label("Historical analytics are based on documented reconstructed transactions. The reconstructed remaining-share balance does not currently match the live HD holding, so lifetime/open-position return metrics are intentionally withheld.").classes("rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100")
            ui.label(f"Reconciliation: {ownership_summary['reconciliation_status'].replace('_', ' ').title()}").classes("text-sm font-medium")
            ui.separator()
            ui.label("HD / ESPP Economics").classes("text-xl font-semibold")
            ui.label("Source-bounded historical economics only. No synthetic return history, no Yahoo lookups, and no tax or lifetime-return claims are produced here.").classes("text-sm text-gray-600 dark:text-gray-300")
            ui.label(f"ESPP Contribution Reconciliation: {analytics.reconciliation['contribution_reconciliation_status'].replace('_', ' ').title()} · DRIP Reconciliation: {analytics.reconciliation['drip_reconciliation_status'].replace('_', ' ').title()}").classes("text-sm font-medium")
            metric_cards([
                ("ESPP Contributions", currency(summary["total_espp_contributions"])),
                ("ESPP Shares", str(summary["total_espp_shares"])),
                ("DRIP Shares", str(summary["total_drip_shares"])),
                ("Dividends Reinvested", currency(summary["total_dividends_reinvested"])),
                ("Shares Transferred", str(summary["total_shares_transferred"])),
                ("Shares Sold", str(summary["total_shares_sold"])),
                ("Documents", str(summary["documents"])),
                ("Unresolved", str(summary["issues"])),
            ])
            if current_hd_price is not None or live_hd_shares is not None:
                ui.label("Current Snapshot").classes("text-lg font-semibold")
                metric_cards([
                    ("Current HD Price", currency(current_hd_price) if current_hd_price is not None else "Unavailable"),
                    ("Live HD Shares", str(live_hd_shares) if live_hd_shares is not None else "Unavailable"),
                    ("Current HD Market Value", currency(analytics.current_snapshot["current_hd_market_value"]) if analytics.current_snapshot["current_hd_market_value"] is not None else "Unavailable"),
                ])
            ui.label("Coverage").classes("text-lg font-semibold")
            metric_cards([
                ("Total Shares Sold", str(analytics.coverage["total_shares_sold"])),
                ("Shares With Documented Basis", str(analytics.coverage["shares_with_documented_basis"])),
                ("Shares Without Documented Basis", str(analytics.coverage["shares_without_documented_basis"])),
                ("Basis Share Coverage", percent(analytics.coverage["share_basis_coverage_pct"])),
                ("Proceeds Coverage", percent(analytics.coverage["proceeds_coverage_pct"])),
                ("Known-Origin Sold Shares", str(analytics.coverage["known_origin_sold_shares"])),
                ("Unresolved-Origin Sold Shares", str(analytics.coverage["unresolved_origin_sold_shares"])),
                ("Origin Coverage", percent(analytics.coverage["origin_coverage_pct"])),
            ])
            ui.label("ESPP Purchase Economics").classes("text-lg font-semibold")
            records_table(analytics.per_purchase_discount, "ESPP Purchase Economics")
            ui.label("DRIP Economics").classes("text-lg font-semibold")
            records_table(analytics.drip_detail, "DRIP Economics")
            ui.label("Documented Realized Gain").classes("text-lg font-semibold")
            records_table(analytics.realized_detail, "Documented Realized Gain")
            ui.label("Realized Gain by Origin").classes("text-lg font-semibold")
            records_table(analytics.realized_by_origin.get("records", []), "Realized Gain by Origin")
            if analytics.issues:
                records_table([{"issue": issue} for issue in analytics.issues], "Analytics Issues")
            import_status = ui.label("").classes("text-sm")
            importing = {"value": False}
            async def import_hd_statements() -> None:
                if importing["value"]:
                    return
                importing["value"] = True
                import_button.disable()
                import_status.set_text("Importing local HD statements…")
                rebuilt = False
                try:
                    result = await critical_io("hd_espp_import", hd_history_service.import_statements)
                    ui.notify(f"Rebuilt {result['inserted']} unique HD transactions from {result['documents']} documents.", type="positive")
                    loaded["HD / ESPP History"] = False
                    await render_hd_espp_history()
                    rebuilt = True
                except Exception as exc:
                    import_status.set_text(f"HD statement import failed: {exc}")
                    ui.notify("HD statement import failed; live portfolio data was not changed.", type="negative")
                finally:
                    importing["value"] = False
                    if not rebuilt:
                        import_button.enable()
            import_button = ui.button("Import HD Statements", icon="document_scanner", on_click=import_hd_statements).classes("w-full sm:w-auto min-h-11")
            ui.separator()
            ui.label("Historical Ownership Reconstruction").classes("text-xl font-semibold")
            ui.label("Derived deterministically from normalized statement transactions. Custody transfers do not change economic ownership; the live portfolio quantity is shown read-only for reconciliation.").classes("text-sm text-gray-600 dark:text-gray-300")
            metric_cards([
                ("ESPP Acquired", str(ownership_summary["total_espp_shares"])),
                ("DRIP Acquired", str(ownership_summary["total_drip_shares"])),
                ("Restricted Acquired", str(ownership_summary["total_restricted_shares"])),
                ("Other / Unknown", str(ownership_summary["total_other_unknown_shares"])),
                ("Total Sold", str(ownership_summary["total_shares_sold"])),
                ("Known Open Lots", str(ownership_summary["known_open_lot_shares"])),
                ("Unresolved Sold Offset", str(ownership_summary["unresolved_sold_lot_shares"])),
                ("Reconstructed Remaining", str(ownership_summary["reconstructed_remaining_shares"])),
                ("Live HD Shares", str(ownership_summary["live_hd_shares"] if ownership_summary["live_hd_shares"] is not None else "Unavailable")),
                ("Difference", str(ownership_summary["reconciliation_difference"] if ownership_summary["reconciliation_difference"] is not None else "Unavailable")),
            ])
            ui.label(f'Reconciliation: {ownership_summary["reconciliation_status"].replace("_", " ").title()}').classes("text-sm font-medium")
            ui.label(f'Lot ledger: {ownership_summary["lot_ledger_status"].replace("_", " ").title()}').classes("text-sm font-medium")
            ui.separator()
            ui.label("Historical → Live Reconciliation").classes("text-xl font-semibold")
            metric_cards([
                ("Historical Reconstructed Shares", str(phase3a.summary["historical_reconstructed_shares"])),
                ("Live HD Shares", str(phase3a.summary["live_hd_shares"] if phase3a.summary["live_hd_shares"] is not None else "Unavailable")),
                ("Difference", str(phase3a.summary["raw_historical_live_difference"] if phase3a.summary["raw_historical_live_difference"] is not None else "Unavailable")),
                ("Latest Documented Position", str(phase3a.summary["latest_documented_checkpoint_date"] or "Unavailable")),
                ("Last Fully Reconciled Position", str(phase3a.summary["last_fully_reconciled_date"] or "Unavailable")),
                ("Unresolved Sold Shares", str(phase3a.summary["unresolved_sold_shares"])),
                ("Coverage Status", str(phase3a.summary["phase3a_status"]).replace("_", " ").title()),
            ])
            records_table(phase3a.share_bridge, "Historical-to-Live Share Bridge")
            records_table(phase3a.coverage_timeline, "Statement Coverage / Custody Timeline")
            records_table(phase3a.custody_timeline, "Documented Custody Movements")
            records_table(phase3a.checkpoint_reconciliation, "HD Position Checkpoint Reconciliation")
            records_table(phase3a.surviving_lots, "Surviving Lots / Difference Audit")
            if phase3a.issues:
                records_table(phase3a.issues, "Phase 3A Reconciliation Issues")
            records_table(ownership.origin_breakdown, "Share Origin Breakdown")
            records_table(state["transfer_lots"], "Normalized Custody Transfer Lot Details")
            open_lots = [lot.display() for lot in ownership.lots if lot.remaining_shares > SHARE_TOLERANCE]
            records_table(open_lots, "Documented Lot Balances Before Unresolved-Sale and Precision Offsets")
            records_table([lot.display() for lot in ownership.sold_lots], "Realized Lot Provenance")
            if ownership.issues:
                records_table([{"issue": issue} for issue in ownership.issues], "Ownership Reconstruction Issues")
            ui.separator()
            display = [{
                "date": row["transaction_date"], "record_type": transaction_record_type(row), "type": row["transaction_type"], "source": row["source"],
                "shares": row["shares"], "amount": row["amount"], "purchase_price": row["purchase_price"],
                "fmv": row["fair_market_value"], "fees": row["fees"], "cost_basis": row["cost_basis"], "transfer_value": row["transfer_value"],
                "acquisition_date": row["acquisition_date"],
                "source_destination": " → ".join(part for part in (row["source_location"], row["destination_location"]) if part),
            } for row in state["transactions"]]
            records_table(display, "Normalized HD Transactions")
            if state["issues"]:
                records_table(state["issues"], "Review Issues")
        loaded["HD / ESPP History"] = True

    with ui.header().classes("flex-wrap items-center justify-between gap-2 shadow-sm px-3 sm:px-5"):
        ui.label("Portfolio Tracker").classes("text-lg sm:text-xl font-semibold")
        ui.label("NiceGUI Production").classes("rounded bg-white/20 px-3 py-1 text-xs sm:text-sm")
    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-2 sm:p-4 gap-4"):
        with ui.row().classes("w-full flex-wrap items-center gap-2"):
            spinner = ui.spinner(size="lg")
            status = ui.label("Loading portfolio and history from read-only databases…").classes("grow text-sm break-words")
            refresh_button = ui.button("Refresh Data", icon="refresh").classes("min-h-11")
        with ui.element("nav").classes("w-full max-w-full overflow-x-auto").props('aria-label="Portfolio sections"'):
            tabs = ui.tabs().classes("w-max min-w-full").props("mobile-arrows outside-arrows")
        with tabs:
            tab_objects = {name: ui.tab(name) for name in NAVIGATION_AREAS}
        tabs.bind_value(
            ui_state,
            "selected_navigation",
            forward=canonical_navigation,
            backward=canonical_navigation,
        )
        with ui.tab_panels(tabs, value=tab_objects[ui_state.selected_navigation]).classes("w-full"):
            areas: dict[str, Any] = {}
            for name in NAVIGATION_AREAS:
                with ui.tab_panel(tab_objects[name]):
                    areas[name] = ui.column().classes("w-full gap-3")

    def render_holdings() -> None:
        """Render the production-style single-grid holdings workflow."""
        area = areas["Holdings"]
        area.clear()
        display_holdings = metrics.to_dict(orient="records") if metrics is not None else holdings
        summary = portfolio_summary(display_holdings)
        editor_rows = []
        for stored in holdings:
            editor_rows.append({
                "_holding_id": stored["_holding_id"],
                "_grid_key": f"existing-{stored['_holding_id']}",
                **{field: stored.get(field, "") for field in HOLDING_EDITABLE_FIELDS},
                "espp_contribution_biweekly": stored.get("espp_biweekly_contribution", stored.get("espp_contribution_biweekly", 0.0)),
                "is_espp": bool(stored.get("espp_enabled", stored.get("is_espp", False))),
            })
        column_defs = holding_grid_column_defs()
        with area:
            ui.label("Holdings").classes("text-2xl font-semibold")
            ui.label("Manage Holdings").classes("text-xl font-semibold")
            ui.label("Add, edit, or remove rows, then click Save when finished.").classes("text-gray-500 dark:text-gray-400")
            ui.label("HD rows are normalized on Save: regular/special contributions are zero, ESPP is enabled with a 15% discount and six-month cadence, and a non-positive ESPP contribution uses $100.").classes("w-full rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100")
            grid = ui.aggrid({
                "columnDefs": column_defs,
                "rowData": editor_rows,
                ":getRowId": "params => params.data._grid_key",
                "defaultColDef": {"resizable": True, "sortable": True},
                "rowSelection": {"mode": "multiRow"},
                "stopEditingWhenCellsLoseFocus": True,
            }).classes("w-full h-96")
            saving = {"value": False}

            async def add_grid_row() -> None:
                await add_aggrid_holding_row(grid)

            async def remove_grid_rows() -> None:
                removed = await remove_selected_aggrid_rows(grid)
                if not removed:
                    ui.notify("Select one or more grid rows to remove.", type="warning")

            async def save_grid() -> None:
                if saving["value"]:
                    return
                saving["value"] = True
                save_button.disable()
                try:
                    client_rows = await current_aggrid_rows(grid)
                    result = await critical_io(
                        "holdings_save",
                        holdings_writer.save_grid,
                        client_rows,
                        expected_rows=list(holdings),
                        submission_token=uuid.uuid4().hex,
                    )
                except ShutdownInProgress:
                    release_save_guard(saving, save_button, ui_will_be_rebuilt=False)
                except Exception as exc:
                    ui.notify(str(exc), type="negative", timeout=0)
                    release_save_guard(saving, save_button, ui_will_be_rebuilt=False)
                else:
                    result = result or {"added": 0, "updated": 0, "deleted": 0}
                    ui.notify(f"Holdings saved: {result['added']} added, {result['updated']} updated, {result['deleted']} removed.", type="positive")
                    release_save_guard(saving, save_button, ui_will_be_rebuilt=True)
                    await refresh_after_holding_write()

            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                ui.button("Add Row", icon="add", on_click=add_grid_row).classes("w-full sm:w-auto min-h-11")
                ui.button("Remove Selected Rows", icon="delete", color="negative", on_click=remove_grid_rows).classes("w-full sm:w-auto min-h-11")
                save_button = ui.button("Save Holdings", icon="save", on_click=save_grid).classes("w-full sm:w-auto min-h-11")
            ui.label("Removed rows are not deleted from the database until you click Save Holdings.").classes("text-xs text-gray-500 dark:text-gray-400")

            metric_cards([("Portfolio value", currency(summary["portfolio_value"])), ("Cost basis", currency(summary["cost_basis"])), ("Unrealized gain/loss", currency(summary["unrealized_gain"])), ("Estimated annual income", currency(summary["annual_income"])), ("Weighted yield", percent(summary["weighted_yield_pct"])), ("Holdings", format_count(summary["holding_count"])), ("Largest holding", str(summary["largest_holding"])), ("Largest weight", percent(summary["largest_weight_pct"]))])
            ui.label("Live Calculated Fields").classes("text-xl font-semibold mt-3")
            ui.label("Read-only market and portfolio calculations refresh after a successful Save.").classes("text-sm text-gray-500 dark:text-gray-400")
            live_fields = (("ticker", "Ticker"), ("shares", "Shares"), ("current_price", "Current Price"), ("market_value", "Market Value"), ("average_cost", "Average Cost"), ("cost_basis", "Cost Basis"), ("unrealized_gain", "Unrealized G/L"), ("dividend_yield_pct", "Dividend Yield %"), ("annual_income", "Annual Income"), ("employer_stock", "Employer Stock"), ("espp_enabled", "ESPP"))
            live_rows = [{key: row.get(key) for key, _label in live_fields} for row in display_holdings]
            live_columns, formatted_live_rows = format_table_records(live_rows)
            live_labels = dict(live_fields)
            for column in live_columns:
                column["label"] = live_labels.get(column["name"], column["label"])
            current_prices = {str(row.get("ticker") or "").strip().upper(): row.get("current_price") for row in live_rows}

            async def open_ticker_chart(ticker: str) -> None:
                normalized_ticker = str(ticker or "").strip().upper()
                if not normalized_ticker:
                    return
                with ui.dialog() as chart_dialog, ui.card().classes(TICKER_CHART_CARD):
                    with ui.row().classes("w-full shrink-0 items-start justify-between gap-3"):
                        with ui.column().classes("min-w-0 gap-0"):
                            ui.label(f"{normalized_ticker} Price Chart").classes("text-xl sm:text-2xl font-semibold break-words")
                            ui.label(f"Current price: {currency(current_prices.get(normalized_ticker))}").classes("text-sm text-gray-500 dark:text-gray-400")
                        ui.button(icon="close", on_click=chart_dialog.close).props("flat round aria-label='Close price chart'")
                    range_selector = ui.toggle(list(TICKER_CHART_RANGES), value=DEFAULT_TICKER_CHART_RANGE).classes("w-full shrink-0 overflow-x-auto")
                    chart_body = ui.column().classes(TICKER_CHART_BODY)

                    async def load_chart_range(range_name: str) -> None:
                        chart_body.clear()
                        with chart_body:
                            ui.spinner(size="lg")
                            ui.label(f"Loading {normalized_ticker} price history…").classes("text-sm text-gray-500 dark:text-gray-400")
                        package = await run.io_bound(load_ticker_chart, normalized_ticker, range_name)
                        chart_body.clear()
                        with chart_body:
                            if package["error"]:
                                ui.label("Price history is not available for this ticker.").classes(EMPTY_STATE)
                                return
                            summary = package["summary"]
                            metric_cards([
                                ("First price", currency(summary["first_price"])),
                                ("Latest price", currency(summary["latest_price"])),
                                ("Period change", currency(summary["change"])),
                                ("Period change %", percent(summary["change_pct"])),
                            ])
                            ui.echart(package["options"]).classes("w-full min-w-0 h-72 sm:h-96")

                    range_selector.on_value_change(lambda event: load_chart_range(str(event.value)))
                    with ui.element("div").classes(f"{RESPONSIVE_ACTIONS} shrink-0 justify-end"):
                        ui.button("Close", on_click=chart_dialog.close).props("outline").classes("w-full sm:w-auto min-h-11")
                chart_dialog.open()
                await load_chart_range(DEFAULT_TICKER_CHART_RANGE)

            if formatted_live_rows:
                with ui.element("div").classes(SCROLL_TABLE):
                    live_table = ui.table(columns=live_columns, rows=formatted_live_rows, pagination=25).classes("w-full min-w-max").props("dense flat bordered")
                    live_table.add_slot("body-cell-ticker", r'''
                        <q-td :props="props">
                            <q-btn
                                :label="props.value"
                                :title="'View ' + props.value + ' price chart'"
                                flat dense no-caps
                                class="ticker-chart-link"
                                @click.stop="$parent.$emit('ticker-chart', props.value)"
                            />
                        </q-td>
                    ''')
                    live_table.on("ticker-chart", lambda event: open_ticker_chart(str(event.args)))
            else:
                ui.label("Your portfolio has no holdings. Add a row in Manage Holdings to begin.").classes(EMPTY_STATE)
            ui.label("Read-only exports").classes("text-lg font-semibold mt-3")
            async def download_export(filename: str) -> None:
                try:
                    export_kwargs: dict[str, Any] = {"history": history}
                    if filename != "holdings.csv":
                        tickers = [str(row.get("ticker") or "") for row in holdings]
                        research_rows = await run.io_bound(research_service.successful_export_rows, tickers)
                        fund_weights, external_context = yahoo_export_state.export_inputs(metrics, research_rows)
                        export_kwargs.update(fund_weights=fund_weights, external_context=external_context)
                    package = await run.io_bound(export_package, holdings, metrics, **export_kwargs)
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
                ui.label("Statement history status could not be loaded.").classes("text-red-600 dark:text-red-400")
                ui.label(str(exc)).classes("text-sm text-gray-600 dark:text-gray-300 break-words")
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
            ui.label("Scanning and importing occur only when explicitly requested. Original PDFs are never moved or deleted.").classes("text-gray-500 dark:text-gray-400")
            metric_cards([
                ("Folder", str(folder["status"])),
                ("PDFs detected", format_count(state["detected_pdf_count"] or folder["pdf_count"])),
                ("Unique PDFs", format_count(state["unique_pdf_count"])),
                ("Processed unique", f"{state['processed_unique_count']} of {state['unique_pdf_count']}"),
                ("Duplicate files", format_count(state["duplicate_file_count"])),
                ("Ready", format_count(state["ready"])),
                ("Needs review", format_count(state["needs_review"])),
                ("Manually resolved", format_count(state["manually_resolved"])),
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
                ("Parser review flags", state["parser_review_flags"]),
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
                ui_will_be_rebuilt = False
                try:
                    await critical_io("statement_scan", statement_service.scan, restart=restart)
                    ui.notify("Statement scan completed.", type="positive")
                    action_busy["value"] = False
                    ui_will_be_rebuilt = True
                    await reload_statement_area()
                except ShutdownInProgress:
                    pass
                except Exception as exc:
                    scan_status.set_text("The statement scan could not complete. Prior scan results were retained where production supports resume.")
                    ui.notify(str(exc), type="negative", timeout=0)
                finally:
                    if not ui_will_be_rebuilt:
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
                    try:
                        await critical_io("statement_scan_clear", statement_service.clear_scan)
                    except ShutdownInProgress:
                        return
                    await reload_statement_area()
                ui.button("Clear Scan Preview", icon="clear", on_click=clear_preview).classes("w-full sm:w-auto min-h-11")
            scan_status = ui.label("").classes("text-sm text-gray-600 dark:text-gray-300")
            verified_records = state.get("verified_records", [])
            if verified_records:
                records_table(statement_verified_display_records(verified_records), "Verified / Resolved Statements")
            else:
                ui.label("No verified or resolved statement history is available yet.").classes(EMPTY_STATE)
            scan_diagnostics = state.get("scan_diagnostics", [])
            if scan_diagnostics:
                records_table(statement_scan_diagnostic_records(scan_diagnostics), "Scan Diagnostics")
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
                    ui.label("Enter only values verified against the original statement. Total Priced Portfolio plus Net Account Balance must equal Total Equity Holdings.").classes("text-sm text-gray-600 dark:text-gray-300")
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
                        ui_will_be_rebuilt = False
                        try:
                            await critical_io("statement_review_save", statement_service.save_review, str(review_month.value), str(ending_date.value), float(invested.value), float(cash.value), float(total.value), bool(confirmed.value), manual_id=str(existing["source_hash"]) if existing else None)
                            dialog.close()
                            ui.notify("Manual review saved as Ready.", type="positive")
                            saving["value"] = False
                            ui_will_be_rebuilt = True
                            await reload_statement_area()
                        except ShutdownInProgress:
                            pass
                        except Exception as exc:
                            ui.notify(str(exc), type="negative", timeout=0)
                        finally:
                            if not ui_will_be_rebuilt:
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
                        ui.label(f"{currency(manual.get('portfolio_value'))} · {manual.get('warning', '')}").classes("text-sm text-gray-600 dark:text-gray-300 break-words")
                        with ui.element("div").classes(RESPONSIVE_ACTIONS):
                            ui.button("Edit Review", icon="edit", on_click=lambda row=manual: review_dialog(row)).classes("w-full sm:w-auto min-h-11")
                            async def remove_review(row: dict[str, Any] = manual) -> None:
                                try:
                                    await critical_io("statement_review_remove", statement_service.remove_review, str(row["source_hash"]))
                                except ShutdownInProgress:
                                    return
                                await reload_statement_area()
                            ui.button("Remove Review", icon="delete", on_click=remove_review).classes("w-full sm:w-auto min-h-11")

            async def import_ready() -> None:
                if action_busy["value"]:
                    return
                action_busy["value"] = True
                import_button.disable()
                ui_will_be_rebuilt = False
                try:
                    result = await critical_io("statement_import", statement_service.import_ready, submission_token=uuid.uuid4().hex)
                    await refresh_history_after_statement_write()
                    ui.notify(f"Imported {int((result or {}).get('imported', 0))} statement history records.", type="positive")
                    action_busy["value"] = False
                    ui_will_be_rebuilt = True
                    await reload_statement_area()
                except ShutdownInProgress:
                    pass
                except Exception as exc:
                    ui.notify(str(exc), type="negative", timeout=0)
                finally:
                    if not ui_will_be_rebuilt:
                        action_busy["value"] = False
                        import_button.enable()
            import_button = ui.button("Import Ready Statements", icon="publish", on_click=import_ready).classes("w-full sm:w-auto min-h-11")
            if not state["can_import"]:
                import_button.disable()
                ui.label("Import remains disabled until the scan is complete, all PDFs are accounted for, and monthly continuity is complete.").classes("text-sm text-amber-700 dark:text-amber-300")

            def confirm_delete_history() -> None:
                with ui.dialog() as dialog, ui.card().classes(CONFIRM_DIALOG_CARD):
                    ui.label("Delete all imported statement history?").classes("text-lg font-semibold")
                    ui.label("Original PDFs and holdings will not be changed. Live snapshots remain intact.").classes("text-sm text-gray-600 dark:text-gray-300")
                    async def remove_imported() -> None:
                        try:
                            await critical_io("statement_history_delete", statement_service.delete_imported, confirmed=True)
                        except ShutdownInProgress:
                            return
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
                ui.label("Research state could not be loaded.").classes("text-red-700 dark:text-red-300")
                ui.label(str(exc)).classes("text-sm break-words")
            loaded["Supplemental Research"] = True
            return
        with area:
            ui.label("Supplemental Research").classes("text-2xl font-semibold")
            ui.label("Yahoo remains the primary financial-data source. Research runs only after an explicit action and supplements missing descriptive context.").classes("text-gray-500 dark:text-gray-400")
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
                ui.label("Only the production supplemental-research model setting is managed here. The existing background worker and cadence are unchanged.").classes("text-sm text-gray-500 dark:text-gray-400")
                model = ui.select(package["models"], value=package["model"], label="Supplemental Research Model").classes(FULL_WIDTH_CONTROL)
                async def save_model() -> None:
                    try:
                        await critical_io("research_settings_save", research_service.set_model, str(model.value or ""))
                        ui.notify("Supplemental research model saved.", type="positive")
                    except ShutdownInProgress:
                        pass
                    except Exception as exc:
                        ui.notify(str(exc), type="negative", timeout=0)
                ui.button("Save Research Model", icon="save", on_click=save_model).classes("w-full sm:w-auto min-h-11")

            async def update_ticker(ticker: str, button: Any) -> None:
                button.disable()
                try:
                    result = await critical_io("research_update", research_service.update, ticker, contexts[ticker], str(model.value or ""))
                    if result.get("status") == "current":
                        ui.notify(f"Supplemental research completed for {ticker}.", type="positive")
                    elif result.get("reason") == "already running":
                        ui.notify(f"Research for {ticker} is already running.", type="warning")
                    else:
                        ui.notify(str(result.get("error") or result.get("reason") or "Research did not complete."), type="negative", timeout=0)
                    await render_supplemental_research(True)
                except ShutdownInProgress:
                    button.enable()
                except Exception as exc:
                    ui.notify(str(exc), type="negative", timeout=0)
                    button.enable()

            ui.label("Portfolio Holdings").classes("text-xl font-semibold mt-2")
            if not package["active"]:
                ui.label("Add a holding before requesting supplemental research.").classes("text-gray-500 dark:text-gray-400")
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
                            ui.label(f'Latest attempt: {row["error"]}').classes("w-full rounded bg-red-50 p-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200 break-words")
                        action = ui.button("Refresh Research" if row["enrolled"] else "Enroll & Research", icon="science").classes("w-full sm:w-auto min-h-11")
                        action.on_click(lambda _event, t=ticker, b=action: update_ticker(t, b))
                        if not row["can_update"]:
                            action.disable()
                        if row["research_markdown"]:
                            with ui.expansion("Existing Research", icon="description").classes("w-full"):
                                ui.markdown(row["research_markdown"]).classes(SAFE_RICH_OUTPUT)
            if package["archived"]:
                ui.label("Archived Research").classes("text-xl font-semibold mt-2")
                ui.label("Production retains research when a holding leaves the portfolio. Re-adding that ticker restores it through the normal holding synchronization path.").classes("text-sm text-gray-500 dark:text-gray-400")
                records_table([{key: row[key] for key in ("ticker", "status", "last_success_at", "model_used", "verified_facts")} for row in package["archived"]])
            with ui.expansion("Background Worker Status", icon="schedule").classes("w-full"):
                ui.label(f'The existing worker checks every {package["worker_check_hours"]} hours. This page does not start or schedule a worker.').classes("text-sm text-gray-500 dark:text-gray-400")
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
                    ui.label(snapshot["projection_contribution_note"]).classes("text-gray-500 dark:text-gray-400")
                    snowball = package["snowball"]
                    ui.label("Snowball Tracker").classes("text-xl font-semibold mt-3")
                    metric_cards([
                        ("Annual Contributions", currency(snowball["annual_contribution"])),
                        ("Current Annual Income", currency(snowball["current_annual_income"])),
                        ("Income as % of Contributions", percent(snowball["income_vs_contribution_pct"])),
                    ])
                    ui.label("Milestones and Your Progress").classes("text-lg font-semibold mt-2")
                    for milestone in snowball["milestones"]:
                        with ui.card().classes("w-full min-w-0 gap-2"):
                            with ui.row().classes("w-full justify-between items-start gap-2"):
                                ui.label(milestone["name"]).classes("font-medium break-words")
                                ui.label(currency(milestone["target_annual_income"])).classes("font-medium")
                            ui.linear_progress(value=milestone["display_progress_pct"] / 100).classes("w-full")
                            years = milestone["years_to_reach"]
                            years_text = "N/A" if math.isinf(years) else f"{years:,.1f}"
                            ui.label(f'Progress: {percent(milestone["display_progress_pct"])} · Estimated years to reach: {years_text}').classes("text-sm text-gray-500 dark:text-gray-400")
                    next_milestone = snowball["next_milestone"]
                    ui.label("Next Milestone").classes("text-lg font-semibold mt-2")
                    detail_cards([
                        ("Your Next Target", next_milestone["name"]),
                        ("Amount Still Needed", currency(next_milestone["amount_still_needed"])),
                        ("Estimated Months Away", "N/A" if next_milestone["estimated_months_away"] is None else format_count(next_milestone["estimated_months_away"])),
                    ])
                with ui.expansion("Yahoo intelligence and supplemental historical context").classes("w-full"):
                    ui.label("Network-heavy Yahoo intelligence is demand-driven. No research refresh controls are exposed.")
                    yahoo_area = ui.column().classes("w-full")
                    async def load_yahoo_intelligence() -> None:
                        yahoo_button.disable()
                        with yahoo_area:
                            pending = ui.label("Loading existing Yahoo-backed intelligence…")
                        try:
                            intelligence = await run.io_bound(yahoo_intelligence_package, metrics)
                            yahoo_export_state.retain(metrics, intelligence)
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
                                    ui.label("Dividend Seasonality shows how your portfolio's estimated annual dividend income is distributed throughout the year based on expected payment timing. Taller bars indicate months when more dividend income is expected. This estimate uses available dividend data and may differ from actual payments.").classes("w-full text-sm leading-relaxed text-gray-600 dark:text-gray-300")
                                    ui.echart(dividend_seasonality_chart_options(monthly)).classes("w-full min-w-0 h-64 sm:h-80")
                                else:
                                    ui.label(str(seasonality.get("message", "Dividend seasonality is unavailable."))).classes("text-gray-500 dark:text-gray-400")
                        except Exception as exc:
                            pending.set_text(f"Yahoo intelligence could not be loaded: {exc}")
                        finally:
                            yahoo_button.enable()
                    yahoo_button = ui.button("Load Yahoo intelligence", on_click=load_yahoo_intelligence).classes("w-full sm:w-auto min-h-11")
        except Exception as exc:
            area.clear()
            with area:
                ui.label(f"Analysis could not be loaded: {exc}").classes("text-red-600 dark:text-red-400")
        loaded["Portfolio Analysis"] = True

    def render_ai() -> None:
        if loaded["AI Analysis"]:
            return
        options = ai_options()
        area = areas["AI Analysis"]
        with area:
            ui.label("AI Analysis").classes("text-2xl font-semibold")
            ui.label("No request is sent until Analyze is clicked. Uses existing compact context and the 1min.AI provider.").classes("text-gray-500 dark:text-gray-400")
            if not options["configured"]:
                ui.label("1min.AI is not configured. ONEMIN_AI_API_KEY is missing from the local environment.").classes("text-amber-700 dark:text-amber-300")
            with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 gap-3"):
                analysis_type = ui.select(options["analysis_types"], value=options["analysis_types"][0], label="Analysis type").classes(FULL_WIDTH_CONTROL)
                model = ui.select(options["models"], value=options["default_model"], label="Model").classes(FULL_WIDTH_CONTROL)
            instructions = ui.textarea(label="Optional instructions").classes("w-full").props("autogrow")
            with ui.card().classes("w-full min-w-0 min-h-32 overflow-hidden"):
                ui.label("Latest analysis").classes("text-lg font-semibold")
                output = ui.markdown("No analysis has been run yet. Choose an analysis type and select Analyze when you are ready.").classes(SAFE_RICH_OUTPUT)
            status = ui.label("").classes("w-full text-sm text-gray-600 dark:text-gray-300 break-words")
            context_size = ui.label("").classes("w-full text-xs text-gray-500 dark:text-gray-400 break-words")
            state = AIAnalysisState()
            request_active = {"value": False}
            snapshot_area = ui.column().classes("w-full min-w-0 gap-2")

            def localize_snapshot_timestamp(label: Any, timestamp: str) -> None:
                ui.run_javascript(
                    f"""const target = document.getElementById('c{label.id}');
                    if (target) {{
                        const value = new Date({json.dumps(timestamp)});
                        target.textContent = new Intl.DateTimeFormat(undefined, {{dateStyle: 'medium', timeStyle: 'short'}}).format(value);
                    }}"""
                )

            def render_snapshot() -> None:
                snapshot_area.clear()
                snapshot = snapshot_service.load()
                with snapshot_area:
                    ui.label("Last Analysis Snapshot").classes("text-lg font-semibold mt-2")
                    if snapshot is None:
                        with ui.card().classes("w-full min-w-0"):
                            ui.label("No saved AI analysis yet.").classes("font-medium")
                            ui.label("Run an analysis to create the first snapshot.").classes("text-sm text-gray-500 dark:text-gray-400")
                        return

                    def confirm_clear_snapshot(viewer: Any | None = None) -> None:
                        with ui.dialog() as dialog, ui.card().classes(CONFIRM_DIALOG_CARD):
                            ui.label("Clear the saved AI Analysis snapshot?").classes("text-lg font-semibold")
                            ui.label("This removes the saved last analysis result. It does not affect your portfolio data.").classes("text-sm text-gray-600 dark:text-gray-300")

                            async def clear_snapshot() -> None:
                                try:
                                    await critical_io("ai_analysis_snapshot_clear", snapshot_service.clear)
                                except ShutdownInProgress:
                                    return
                                except Exception as exc:
                                    ui.notify(f"Snapshot could not be cleared: {exc}", type="negative", timeout=0)
                                    return
                                dialog.close()
                                if viewer is not None:
                                    viewer.close()
                                render_snapshot()
                                ui.notify("Last AI Analysis snapshot cleared.", type="positive")

                            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                                ui.button("Clear Snapshot", icon="delete", color="negative", on_click=clear_snapshot).classes("w-full sm:w-auto min-h-11")
                                ui.button("Cancel", on_click=dialog.close).props("flat").classes("w-full sm:w-auto min-h-11")
                        dialog.open()

                    def open_snapshot_viewer() -> None:
                        with ui.dialog() as viewer, ui.card().classes(SNAPSHOT_VIEWER_CARD):
                            with ui.row().classes("w-full shrink-0 items-start justify-between gap-3"):
                                ui.label("Last Analysis Snapshot").classes("text-xl sm:text-2xl font-semibold")
                                ui.button(icon="close", on_click=viewer.close).props("flat round aria-label='Close snapshot viewer'")
                            with ui.element("div").classes("w-full shrink-0 grid grid-cols-1 sm:grid-cols-3 gap-3 rounded-lg bg-gray-50 p-3 dark:bg-gray-800"):
                                with ui.column().classes("min-w-0 gap-1"):
                                    ui.label("Analysis Type").classes("text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400")
                                    ui.label(snapshot.analysis_type).classes("font-medium break-words")
                                with ui.column().classes("min-w-0 gap-1"):
                                    ui.label("Run Date/Time").classes("text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400")
                                    viewer_run_label = ui.label(friendly_utc_timestamp(snapshot.completed_at_utc)).classes("break-words")
                                    localize_snapshot_timestamp(viewer_run_label, snapshot.completed_at_utc)
                                with ui.column().classes("min-w-0 gap-1"):
                                    ui.label("Model").classes("text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400")
                                    ui.label(snapshot.model).classes("break-words")
                            ui.separator().classes("shrink-0")
                            with ui.element("div").classes(SNAPSHOT_VIEWER_BODY):
                                ui.markdown(snapshot.result_markdown).classes(SNAPSHOT_RICH_OUTPUT)
                            with ui.element("div").classes(f"{RESPONSIVE_ACTIONS} shrink-0"):
                                ui.button("Clear Snapshot…", icon="delete_outline", on_click=lambda: confirm_clear_snapshot(viewer)).props("outline color=negative").classes("w-full sm:w-auto min-h-11")
                                ui.button("Close", on_click=viewer.close).props("outline").classes("w-full sm:w-auto min-h-11")
                        viewer.open()

                    with ui.card().classes("w-full min-w-0 overflow-hidden p-0"):
                        with ui.element("div").classes("hidden md:grid w-full grid-cols-[minmax(0,2fr)_minmax(0,1.4fr)_minmax(0,1fr)_auto] gap-3 border-b px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:border-gray-700 dark:text-gray-400"):
                            for heading in ("Analysis Type", "Run Date/Time", "Model", "Action"):
                                ui.label(heading)
                        with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-[minmax(0,2fr)_minmax(0,1.4fr)_minmax(0,1fr)_auto] items-center gap-3 px-4 py-3"):
                            with ui.column().classes("min-w-0 gap-0"):
                                ui.label("Analysis Type").classes("md:hidden text-xs uppercase text-gray-500 dark:text-gray-400")
                                ui.label(snapshot.analysis_type).classes("font-medium break-words")
                            with ui.column().classes("min-w-0 gap-0"):
                                ui.label("Run Date/Time").classes("md:hidden text-xs uppercase text-gray-500 dark:text-gray-400")
                                run_label = ui.label(friendly_utc_timestamp(snapshot.completed_at_utc)).classes("text-sm break-words")
                                localize_snapshot_timestamp(run_label, snapshot.completed_at_utc)
                            with ui.column().classes("min-w-0 gap-0"):
                                ui.label("Model").classes("md:hidden text-xs uppercase text-gray-500 dark:text-gray-400")
                                ui.label(snapshot.model).classes("text-sm break-words")
                            with ui.element("div").classes("flex flex-col sm:flex-row md:flex-col lg:flex-row gap-2"):
                                ui.button("View", icon="visibility", on_click=open_snapshot_viewer).classes("min-h-11")
                                ui.button("Clear…", icon="delete_outline", on_click=confirm_clear_snapshot).props("flat color=negative").classes("min-h-11")

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
                        await critical_io(
                            "ai_analysis_snapshot_save",
                            save_successful_analysis_snapshot,
                            snapshot_service,
                            state,
                        )
                        output.set_content(state.result_markdown)
                        render_snapshot()
                        status.set_text(f"Completed with {state.result_model} at {state.generated_at}.")
                        ui.notify(f"Analysis completed with {state.result_model}", type="positive")
                except ShutdownInProgress:
                    pass
                except Exception as exc:
                    status.set_text("AI Analysis could not be completed and saved.")
                    ui.notify(str(exc), type="negative", timeout=0)
                finally:
                    request_active["value"] = False
                    button.enable()
            def clear_analysis() -> None:
                state.clear()
                output.set_content("No analysis has been run yet. Choose an analysis type and select Analyze when you are ready.")
                status.set_text("")
                context_size.set_text("")
            def download_analysis() -> None:
                markdown = resolve_analysis_download(snapshot_service, state)
                if markdown is None:
                    ui.notify("No AI analysis is available to download.", type="warning")
                    return
                ui.download(markdown.encode("utf-8"), "ai_analysis.md")
            with ui.element("div").classes(RESPONSIVE_ACTIONS):
                button = ui.button("Analyze", on_click=analyze, icon="auto_awesome", color="primary").classes("w-full sm:w-auto min-h-11")
                ui.button("Clear analysis", icon="clear", on_click=clear_analysis).classes("w-full sm:w-auto min-h-11")
                ui.button("Download Markdown", icon="download", on_click=download_analysis).classes("w-full sm:w-auto min-h-11")
            render_snapshot()
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
                ui.label(str(package.get("disclaimer", "Tracker-generated and not an objective investment rating."))).classes("w-full rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-medium text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100 break-words")
                metric_cards([("Overall health score", format_score(package.get("health_score"), out_of_100=True)), ("Largest holding", str(package.get("largest_holding", "N/A"))), ("Largest holding weight", percent(package.get("largest_holding_weight", 0.0)))])
                records_table([{"component": key, "score": value} for key, value in package.get("subscores", {}).items()], "Component scores")
                records_table(package.get("strongest_areas", []), "Strongest areas")
                records_table(package.get("weakest_areas", []), "Weakest areas")
                ui.label("Alerts").classes("text-lg font-semibold")
                for alert in package.get("alerts", []) or ["No health alerts."]:
                    ui.label(f"• {alert}")
                for note in package.get("notes", []):
                    ui.label(f"• {note}")
                ui.label(str(package.get("data_quality_explanation", ""))).classes("text-gray-600 dark:text-gray-300")
        except Exception as exc:
            area.clear()
            with area:
                ui.label(f"Health could not be loaded: {exc}").classes("text-red-600 dark:text-red-400")
        loaded["Portfolio Health"] = True

    def render_history(selected_range: str = DEFAULT_HISTORY_RANGE) -> None:
        nonlocal history_range
        history_range = selected_range
        ui_state.history_range = selected_range
        area = areas["History"]
        area.clear()
        with area:
            ui.label("Historical Portfolio Value").classes("text-2xl font-semibold")
            ui.label("Nominal account value only—not investment return. No interpolation; monthly and live snapshot points are retained.").classes("text-gray-500 dark:text-gray-400")
            with ui.element("div").classes("w-full max-w-full overflow-x-auto"):
                selector = ui.toggle(list(HISTORY_RANGE_OPTIONS), value=selected_range).classes("min-w-max")
            chart_area = ui.column().classes("w-full")
            def update_chart(range_name: str) -> None:
                nonlocal history_range
                history_range = range_name
                ui_state.history_range = range_name
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
        ai_status = ai_provider_status(ENVIRONMENT_STATUS)
        area = areas["System"]
        with area:
            ui.label("System").classes("text-2xl font-semibold")
            ui.label("Production application and shared local-storage status. Use the global Refresh Data action to refresh portfolio data.").classes("text-gray-500 dark:text-gray-400")
            ui.label("Appearance").classes("text-lg font-semibold mt-2")
            ui.toggle(APPEARANCE_OPTIONS, value=appearance_storage[APPEARANCE_STORAGE_KEY]).bind_value(
                appearance_storage, APPEARANCE_STORAGE_KEY
            ).classes("w-full sm:w-auto")
            ui.label("System follows your device's appearance setting.").classes("text-sm text-gray-500 dark:text-gray-400")
            ui.label("Shared Data").classes("text-lg font-semibold mt-2")
            ui.label("Refreshes and verifies shared Portfolio Tracker data before switching between NiceGUI and Streamlit.").classes("text-sm text-gray-500 dark:text-gray-400")
            preparation_status = ui.column().classes("w-full min-w-0 gap-1")
            preparation_active = {"value": False}

            async def prepare_for_switch() -> None:
                if preparation_active["value"]:
                    return
                if lifecycle.active_operations:
                    ui.notify("Another protected write is active. Try preparing shared data again when it finishes.", type="warning")
                    return
                preparation_active["value"] = True
                prepare_button.disable()
                preparation_status.clear()
                with preparation_status:
                    ui.label("Preparing shared Portfolio Tracker data...").classes("text-sm text-blue-700 dark:text-blue-300")
                try:
                    result = await critical_io(
                        "prepare_shared_data",
                        prepare_shared_data,
                        coordinator.portfolio_path,
                        coordinator.history_path,
                        market_loader=current_market_data,
                        market_cache_clearer=clear_market_data_cache,
                    )
                    preparation_status.clear()
                    with preparation_status:
                        ui.label("Shared data is ready.").classes("font-semibold text-green-700 dark:text-green-300")
                        system_detail_cards([
                            ("Holdings", f"{result.holdings_status} ({result.holdings_count})", False),
                            ("Portfolio database", result.portfolio_database_status, False),
                            ("History database", result.history_database_status, False),
                            ("Market refresh", result.market_refresh_status, False),
                            ("Live snapshot", result.live_snapshot_status, False),
                            ("SQLite checkpoint", result.sqlite_checkpoint_status, False),
                        ])
                        ui.label("You can safely stop this app and start the other Portfolio Tracker interface.").classes("text-sm text-green-700 dark:text-green-300")
                    ui.notify("Shared data is ready.", type="positive")
                except ShutdownInProgress:
                    pass
                except Exception as exc:
                    preparation_status.clear()
                    stage = getattr(exc, "stage", "Shared data preparation")
                    with preparation_status:
                        ui.label("Shared data preparation did not complete.").classes("font-semibold text-red-600 dark:text-red-400")
                        ui.label(f"{stage} failed. Your existing data was not replaced.").classes("text-sm text-red-600 dark:text-red-400 break-words")
                    ui.notify(f"{stage} failed.", type="negative", timeout=0)
                finally:
                    preparation_active["value"] = False
                    prepare_button.enable()

            prepare_button = ui.button("Prepare Shared Data", icon="fact_check", on_click=prepare_for_switch).classes("w-full sm:w-auto min-h-11")
            ui.label("Runtime").classes("text-lg font-semibold mt-2")
            system_detail_cards([
                ("Application", application["framework"], False),
                ("Application port", application["port"], False),
                ("Database refresh interval", f'{application["database_refresh_interval_seconds"]} seconds', False),
            ])
            ui.label("AI Analysis").classes("text-lg font-semibold mt-2")
            system_detail_cards([
                ("Provider", ai_status["provider"], False),
                ("API configuration", ai_status["api_configuration"], False),
                ("Status", ai_status["status"], False),
                ("Current/default model", ai_status["model"], False),
                ("Configuration source", ai_status["configuration_source"], False),
            ])
            ui.label("Portfolio database").classes("text-lg font-semibold mt-2")
            system_detail_cards([
                ("Status", portfolio_db["status"], False),
                ("Configured path", portfolio_db["path"], True),
            ])
            ui.label("Statement storage").classes("text-lg font-semibold mt-2")
            system_detail_cards([
                ("Statements folder", statement_folder["status"], False),
                ("Statements folder path", statement_folder["path"], True),
                ("PDFs detected", format_count(statement_folder["pdf_count"]), False),
                ("Statement history database", history_db["status"], False),
                ("Statement history database path", history_db["path"], True),
            ])
            ui.label("Cache behavior").classes("text-lg font-semibold mt-2")
            ui.label("These intervals are informational. Cache refreshes continue to use the application’s existing behavior.").classes("text-gray-500 dark:text-gray-400")
            system_detail_cards([(name, format_interval(seconds), False) for name, seconds in package["cache_intervals_seconds"].items()])
        loaded["System"] = True

    async def refresh_section(name: str) -> None:
        """Render one section through its single lazy-render entry point.

        Centralizing dispatch prevents post-write refreshes from appending a
        second Holdings block. History is rebuilt with the range retained in
        page state rather than resetting the user's selection.
        """
        if name == "Holdings" and not loaded["Holdings"]: render_holdings()
        elif name == "Portfolio Analysis": await render_analysis()
        elif name == "AI Analysis": render_ai()
        elif name == "Portfolio Insights": await render_insights()
        elif name == "Portfolio Health": await render_health()
        elif name == "History" and not loaded["History"]: render_history(history_range)
        elif name == "Statement History": await render_statement_history()
        elif name == "HD / ESPP History": await render_hd_espp_history()
        elif name == "Supplemental Research": await render_supplemental_research()
        elif name == "System": render_system()

    async def invalidate_sections(names: Any, message: str) -> None:
        """Mark derived sections stale, immediately rebuilding only the visible one."""
        def replace_with_placeholder(name: str) -> None:
            areas[name].clear()
            with areas[name]:
                ui.label(message).classes("text-gray-500 dark:text-gray-400")

        await invalidate_section_views(
            names,
            ui_state.selected_navigation,
            loaded,
            refresh_section,
            replace_with_placeholder,
        )

    async def navigate(event: Any) -> None:
        name = canonical_navigation(event.value)
        ui_state.selected_navigation = name
        await refresh_section(name)

    tabs.on_value_change(navigate)
    async def refresh_data(force: bool = False, *, timer_tick: bool = False) -> None:
        nonlocal holdings,history,metrics
        result = await refresh_guard.run(
            lambda: run.io_bound(coordinator.refresh, force),
            wait_if_busy=not timer_tick,
        )
        if result is None or not client_is_alive(page_client, lifecycle):
            return
        if not result.portfolio_changed and not result.history_changed and not result.metrics_changed:
            return
        if result.portfolio_changed:
            holdings=result.holdings or []
        if result.metrics_changed:
            metrics=result.metrics
            yahoo_export_state.invalidate_if_changed(metrics)
        if result.history_changed:
            history=result.history or []
        plan = refresh_view_plan(
            ui_state.selected_navigation,
            metrics_changed=result.metrics_changed,
            history_changed=result.history_changed,
        )
        await invalidate_sections(
            plan["stale"],
            "Data changed; this section will refresh when opened.",
        )
        if client_is_alive(page_client, lifecycle):
            status.set_text(f"Refreshed {len(holdings)} holdings and {len(history)} history points")
    async def refresh_after_holding_write() -> None:
        """Complete validate -> persist -> forced refresh -> view invalidation.

        The forced refresh reloads the committed rows and recalculates metrics.
        ``invalidate_sections`` immediately rebuilds visible Holdings while
        leaving hidden derived views lazy; this ordering prevents the former
        blank-placeholder-until-tab-switch regression.
        """
        await refresh_data(True)
        if not client_is_alive(page_client, lifecycle):
            return
        await invalidate_sections(
            ("AI Analysis", "Supplemental Research"),
            "Holdings changed; prior prepared output is stale and this section will refresh when opened.",
        )
    async def refresh_history_after_statement_write() -> None:
        """Reload only history-derived state; do not reload holdings or Yahoo data."""
        nonlocal history
        if lifecycle.is_shutting_down:
            return
        history = await run.io_bound(statement_service.performance_history)
        if not client_is_alive(page_client, lifecycle):
            return
        await invalidate_sections(
            ("History", "AI Analysis"),
            "Statement history changed; prior AI history context is stale and will refresh when opened.",
        )
        status.set_text(f"History refreshed with {len(history)} points; holdings were not reloaded")
    refresh_button.on_click(lambda: refresh_data(True))
    refresh_timer = ui.timer(DB_REFRESH_INTERVAL_SECONDS, lambda: refresh_data(False, timer_tick=True))
    page_client.on_disconnect(refresh_timer.deactivate)
    try:
        initial = await refresh_guard.run(
            lambda: run.io_bound(coordinator.refresh, True),
            wait_if_busy=True,
        )
        if initial is None or not client_is_alive(page_client, lifecycle):
            return
        holdings=initial.holdings or [];history=initial.history or [];metrics=initial.metrics
        render_holdings()
        status.set_text(f"Loaded {len(holdings)} holdings and {len(history)} history points")
    except Exception as exc:
        if client_is_alive(page_client, lifecycle):
            status.set_text("Unable to load the read-only portfolio sources.")
            ui.notify(str(exc), type="negative", timeout=0)
    finally:
        if client_is_alive(page_client, lifecycle):
            spinner.set_visibility(False)


if __name__ in {"__main__", "__mp_main__"}:
    start_launcher_watchdog()
    start_shutdown_request_watcher(app.shutdown)
    ui.run(host=HOST, port=PORT, title="Portfolio Tracker", favicon=FAVICON_PATH, reload=False, show=False, storage_secret=STORAGE_SECRET)

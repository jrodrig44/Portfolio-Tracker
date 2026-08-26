from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from nicegui_app.services import ticker_chart
from nicegui_app.services.formatting import RICH_OUTPUT_CSS, TICKER_CHART_BODY, TICKER_CHART_CARD, format_currency
from nicegui_app.services.yahoo import clear_market_data_cache


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")


def history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": [110.0, 100.0, float("nan"), 121.0]},
        index=pd.to_datetime(["2026-01-02", "2026-01-01", "2026-01-03", "2026-01-04"]),
    )


class TickerChartServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        ticker_chart.clear_ticker_chart_cache()

    def test_history_is_normalized_in_date_order_and_summary_is_correct(self) -> None:
        records = ticker_chart.normalize_ticker_history(history_frame())
        self.assertEqual([row["date"] for row in records], ["2026-01-01", "2026-01-02", "2026-01-04"])
        self.assertEqual([row["price"] for row in records], [100.0, 110.0, 121.0])
        self.assertEqual(ticker_chart.ticker_chart_summary(records), {
            "first_price": 100.0,
            "latest_price": 121.0,
            "change": 21.0,
            "change_pct": 21.0,
        })
        self.assertEqual(format_currency(records[-1]["price"]), "$121.00")

    def test_same_ticker_and_range_reuses_bounded_ttl_cache(self) -> None:
        loader = Mock(return_value=history_frame())
        first = ticker_chart.load_ticker_chart("hd", "3M", loader=loader, now=lambda: 10.0)
        second = ticker_chart.load_ticker_chart("HD", "3M", loader=loader, now=lambda: 20.0)
        self.assertIs(first, second)
        loader.assert_called_once_with("HD", "3mo", "1d")

        clear_market_data_cache()
        third = ticker_chart.load_ticker_chart("HD", "3M", loader=loader, now=lambda: 30.0)
        self.assertIs(first, third)
        loader.assert_called_once()

    def test_different_ranges_use_expected_yahoo_periods(self) -> None:
        loader = Mock(return_value=history_frame())
        ticker_chart.load_ticker_chart("HD", "1M", loader=loader, now=lambda: 1.0)
        ticker_chart.load_ticker_chart("HD", "5Y", loader=loader, now=lambda: 1.0)
        self.assertEqual(loader.call_args_list[0].args, ("HD", "1mo", "1d"))
        self.assertEqual(loader.call_args_list[1].args, ("HD", "5y", "1d"))

    def test_duplicate_ticker_requests_share_ticker_level_cache(self) -> None:
        loader = Mock(return_value=history_frame())
        for _duplicate_holding in range(2):
            ticker_chart.load_ticker_chart("DUP", "3M", loader=loader, now=lambda: 1.0)
        loader.assert_called_once()

    def test_empty_and_failed_history_return_safe_message(self) -> None:
        empty = ticker_chart.load_ticker_chart("ZZZ", "3M", loader=lambda *_: pd.DataFrame(), now=lambda: 1.0)
        self.assertEqual(empty["records"], [])
        self.assertIsNone(empty["options"])
        self.assertEqual(empty["error"], "Price history is not available for this ticker.")

        ticker_chart.clear_ticker_chart_cache()
        failed = ticker_chart.load_ticker_chart("ZZZ", "3M", loader=Mock(side_effect=TimeoutError("Yahoo timeout")), now=lambda: 1.0)
        self.assertEqual(failed["error"], "Price history is not available for this ticker.")
        self.assertNotIn("timeout", failed["error"].lower())

    def test_chart_options_have_date_price_axes_and_currency_tooltip(self) -> None:
        records = ticker_chart.normalize_ticker_history(history_frame())
        options = ticker_chart.ticker_chart_options("HD", records)
        self.assertEqual(options["xAxis"]["name"], "Date")
        self.assertEqual(options["yAxis"]["name"], "Price")
        self.assertEqual(options["series"][0]["data"], [100.0, 110.0, 121.0])
        self.assertIn("toFixed(2)", options["tooltip"][":valueFormatter"])


class TickerChartUIContractTests(unittest.TestCase):
    def test_only_live_calculated_ticker_cell_is_clickable(self) -> None:
        holdings = MAIN.split("def render_holdings()", 1)[1].split("async def render_statement_history", 1)[0]
        manage, live = holdings.split('ui.label("Live Calculated Fields")', 1)
        self.assertNotIn("ticker-chart", manage)
        self.assertIn('live_table.add_slot("body-cell-ticker"', live)
        self.assertIn("ticker-chart-link", live)
        self.assertIn("View ' + props.value + ' price chart", live)
        self.assertIn('@click.stop="$parent.$emit(\'ticker-chart\', props.value)"', live)
        self.assertNotIn("mouseenter", live)
        self.assertNotIn("mouseover", live)

    def test_history_loading_exists_only_inside_explicit_click_dialog_path(self) -> None:
        holdings = MAIN.split("def render_holdings()", 1)[1].split("async def render_statement_history", 1)[0]
        before_dialog, dialog = holdings.split("async def open_ticker_chart", 1)
        self.assertNotIn("load_ticker_chart", before_dialog)
        self.assertIn("await run.io_bound(load_ticker_chart, normalized_ticker, range_name)", dialog)
        self.assertIn('live_table.on("ticker-chart"', dialog)
        self.assertEqual(MAIN.count("load_ticker_chart,"), 1)

    def test_dialog_is_responsive_bounded_and_does_not_touch_crud_grid(self) -> None:
        self.assertIn("96vw", TICKER_CHART_CARD)
        self.assertIn("64rem", TICKER_CHART_CARD)
        self.assertIn("86dvh", TICKER_CHART_CARD)
        self.assertIn("overflow-hidden", TICKER_CHART_CARD)
        self.assertIn("flex-1", TICKER_CHART_BODY)
        self.assertIn("min-h-0", TICKER_CHART_BODY)
        self.assertIn("overflow-y-auto", TICKER_CHART_BODY)
        self.assertIn(".ticker-chart-link:focus-visible", RICH_OUTPUT_CSS)
        dialog = MAIN.split("async def open_ticker_chart", 1)[1].split("if formatted_live_rows", 1)[0]
        self.assertIn("chart_dialog.open()", dialog)
        self.assertIn('ui.button("Close", on_click=chart_dialog.close)', dialog)
        self.assertNotIn("render_holdings()", dialog)
        self.assertNotIn("grid.update()", dialog)
        self.assertNotIn('grid.options["rowData"]', dialog)


if __name__ == "__main__":
    unittest.main()

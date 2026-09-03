import copy
import unittest
from pathlib import Path

from nicegui_app.services.formatting import (
    EMPTY_VALUE,
    FULL_WIDTH_CONTROL,
    RESPONSIVE_ACTIONS,
    RESPONSIVE_CARD_GRID,
    SCROLL_TABLE,
    dividend_seasonality_chart_options,
    format_count,
    format_currency,
    format_percentage,
    format_score,
    format_shares,
    format_status,
    format_table_records,
)


ROOT = Path(__file__).resolve().parents[1]


class NiceGuiPhase2APresentationTests(unittest.TestCase):
    def test_number_formatting_contract(self) -> None:
        self.assertEqual(format_currency(214.94887198997495), "$214.95")
        self.assertEqual(format_currency(100), "$100.00")
        self.assertEqual(format_percentage(40.80483217149903), "40.80%")
        self.assertEqual(format_score(73.7432), "73.7")
        self.assertEqual(format_score(73.7432, out_of_100=True), "73.7/100")
        self.assertEqual(format_count(11.0), "11")

    def test_share_precision_and_trailing_zero_contract(self) -> None:
        self.assertEqual(format_shares(214.94887198997495), "214.948872")
        self.assertEqual(format_shares(20.0), "20")
        self.assertEqual(format_shares(4.5), "4.5")
        self.assertEqual(format_shares(0.123456789), "0.123457")

    def test_null_and_boolean_values_are_readable(self) -> None:
        self.assertEqual(format_currency(None), EMPTY_VALUE)
        self.assertEqual(format_percentage(float("nan")), EMPTY_VALUE)
        self.assertEqual(format_status(True), "Yes")
        self.assertEqual(format_status(False), "No")
        self.assertEqual(format_status(True, enabled="Active", disabled="Not active"), "Active")

    def test_table_cleanup_formats_values_without_mutating_source(self) -> None:
        source = [{"ticker": "ABC", "shares": 214.94887198997495, "market_value": 100.123456, "portfolio_weight_pct": 40.80483217149903, "espp_enabled": True, "Unnamed: 0": 9, "": "blank", "empty": None}]
        original = copy.deepcopy(source)
        columns, rows = format_table_records(source)
        self.assertEqual(source, original)
        self.assertEqual([column["name"] for column in columns], ["ticker", "shares", "market_value", "portfolio_weight_pct", "espp_enabled"])
        self.assertEqual(rows[0]["shares"], "214.948872")
        self.assertEqual(rows[0]["market_value"], "$100.12")
        self.assertEqual(rows[0]["portfolio_weight_pct"], "40.80%")
        self.assertEqual(rows[0]["espp_enabled"], "Active")
        self.assertNotIn("100.123456", str(rows))

        _, nested_rows = format_table_records([{"ticker": "FUND", "details": [{"sector": "Technology", "weight_pct": 40.80483217149903}]}])
        self.assertNotIn("{", nested_rows[0]["details"])
        self.assertIn("Technology", nested_rows[0]["details"])
        self.assertIn("40.80%", nested_rows[0]["details"])

    def test_dividend_seasonality_chart_explains_and_labels_income_dollars(self) -> None:
        options = dividend_seasonality_chart_options({"Jan": 214.94887198997495, "Feb": 0})
        self.assertEqual(options["title"]["text"], "Dividend Seasonality")
        self.assertEqual(options["xAxis"]["name"], "Month")
        self.assertEqual(options["yAxis"]["name"], "Estimated Dividend Income")
        self.assertEqual(options["series"][0]["data"], [214.94887198997495, 0.0])
        self.assertIn("minimumFractionDigits: 2", options["tooltip"][":valueFormatter"])
        self.assertEqual(options["yAxis"]["axisLabel"]["formatter"], "${value}")

        source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("Dividend Seasonality shows how your portfolio's estimated annual dividend income", source)
        self.assertIn("dividend_seasonality_chart_options(monthly)", source)

    def test_raw_json_presenters_are_removed_from_user_sections(self) -> None:
        source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("ui.json_editor", source)
        self.assertNotIn('{"content": {"json": package', source)
        concentration = source.split('ui.expansion("Concentration and ESPP context")', 1)[1].split('ui.expansion("Contribution allocation")', 1)[0]
        self.assertIn("detail_cards", concentration)
        self.assertNotIn("str(package", concentration)
        health = source.split("async def render_health", 1)[1].split("def render_history", 1)[0]
        self.assertNotIn("json_editor", health)

    def test_responsive_render_contracts_are_present(self) -> None:
        self.assertIn("grid-cols-1", RESPONSIVE_CARD_GRID)
        self.assertIn("sm:grid-cols-2", RESPONSIVE_CARD_GRID)
        self.assertIn("flex-col", RESPONSIVE_ACTIONS)
        self.assertIn("sm:flex-row", RESPONSIVE_ACTIONS)
        self.assertIn("overflow-x-auto", SCROLL_TABLE)
        self.assertIn("w-full", FULL_WIDTH_CONTROL)
        source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("RESPONSIVE_CARD_GRID", source)
        self.assertIn("SCROLL_TABLE", source)
        self.assertIn("primary_tabs = ui.tabs(", source)
        self.assertIn("ui.left_drawer(value=False", source)
        self.assertIn("align=justify", source)
        self.assertIn("w-full sm:w-auto min-h-11", source)

    def test_database_writes_remain_isolated_to_snapshot_persistence(self) -> None:
        service = ROOT / "nicegui_app" / "services" / "ai_snapshot.py"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "nicegui_app").rglob("*.py")
            if path != service
        ).upper()
        for statement in ("INSERT INTO", "UPDATE HOLDINGS", "DELETE FROM", "ALTER TABLE", "CREATE TABLE"):
            self.assertNotIn(statement, source)
        snapshot_source = service.read_text(encoding="utf-8").upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS", snapshot_source)
        self.assertIn("INSERT INTO", snapshot_source)
        self.assertIn("ON CONFLICT(SNAPSHOT_ID) DO UPDATE", snapshot_source)
        self.assertNotIn("UPDATE HOLDINGS", snapshot_source)


if __name__ == "__main__":
    unittest.main()

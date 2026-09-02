from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from hd_espp_integration import SAFE_UNAVAILABLE_REASON, build_hd_historical_integration_context, load_hd_historical_integration_context, unavailable_hd_historical_context
from portfolio_ai import compact_ai_context
from portfolio_analysis import build_analysis
from portfolio_export import build_export_markdown, build_export_payload
from portfolio_insights import build_portfolio_insights


def metrics() -> pd.DataFrame:
    frame = pd.DataFrame([
        {"ticker": "HD", "shares": 4.0, "current_price": 100.0, "market_value": 400.0, "cost_basis": 320.0, "unrealized_gain": 80.0, "annual_income": 16.0, "annual_div_per_share": 4.0, "asset_type": "Stock", "sector": "Consumer Cyclical", "regular_biweekly_contribution": 0.0, "espp_biweekly_contribution": 100.0, "special_contribution": 0.0, "employer_stock": True, "espp_enabled": True},
        {"ticker": "CORE", "shares": 10.0, "current_price": 50.0, "market_value": 500.0, "cost_basis": 400.0, "unrealized_gain": 100.0, "annual_income": 20.0, "annual_div_per_share": 2.0, "asset_type": "Stock", "sector": "Technology", "regular_biweekly_contribution": 280.0, "espp_biweekly_contribution": 0.0, "special_contribution": 0.0},
    ])
    frame["portfolio_weight_pct"] = frame["market_value"] / frame["market_value"].sum() * 100
    frame["dividend_yield_pct"] = frame["annual_income"] / frame["market_value"] * 100
    frame["portfolio_yield_pct"] = frame["dividend_yield_pct"]
    return frame


def tx(fingerprint: str, kind: str, tx_date: str, shares: str | None = None, **values):
    row = {
        "transaction_fingerprint": fingerprint,
        "transaction_type": kind,
        "transaction_date": tx_date,
        "shares": Decimal(shares) if shares is not None else None,
        "source": values.pop("source", "Merrill"),
        "source_location": values.pop("source_location", "Merrill"),
        "source_account_type": values.pop("source_account_type", "Brokerage"),
        "source_document": values.pop("source_document", "sanitized.pdf"),
    }
    row.update(values)
    return row


def hd_state() -> dict:
    transactions = [
        tx("espp", "ESPP_PURCHASE", "2024-01-01", "10", amount=Decimal("80"), purchase_price=Decimal("8"), fair_market_value=Decimal("10")),
        tx("drip", "DIVIDEND_REINVESTMENT", "2024-03-01", "0.5", amount=Decimal("10"), purchase_price=Decimal("20"), fees=Decimal("0"), taxes=Decimal("0"), notes="gross_dividend=10"),
        tx("cash-div", "DIVIDEND", "2025-01-01", None, amount=Decimal("12")),
        tx("sale-agg", "SALE", "2026-02-13", "8", amount=Decimal("1000")),
        tx("sale-known", "SALE", "2026-02-12", "5", amount=Decimal("700"), cost_basis=Decimal("500"), acquisition_date="2024-01-01", lot_sequence=1, source_account_type="Realized lot"),
        tx("sale-unknown", "SALE", "2026-02-12", "3", amount=Decimal("300"), cost_basis=None, acquisition_date=None, lot_sequence=2, source_account_type="Realized lot"),
    ]
    return {"transactions": transactions, "transfer_lots": [], "documents": [{"source_document": "sanitized.pdf", "document_family": "merrill_hd", "period_start": "2024-01-01", "period_end": "2026-02-28", "parsed_transaction_occurrence_count": len(transactions)}], "position_checkpoints": [], "summary": {"documents": 1}}


class HDESPPIntegrationTests(unittest.TestCase):
    def test_shared_payload_preserves_gating_and_unknown_values(self) -> None:
        context = build_hd_historical_integration_context(hd_state(), metrics())
        unresolved = next(row for row in context["realized"]["by_origin"] if row["origin"] == "UNRESOLVED")
        lifetime_return = next(row for row in context["performance_availability"] if row["metric"] == "Lifetime Total Return")

        self.assertTrue(context["available"])
        self.assertEqual(context["documented_economics"]["espp_employee_contributions"], Decimal("80"))
        self.assertEqual(context["documented_economics"]["espp_purchase_discount_benefit"], Decimal("20"))
        self.assertEqual(context["realized"]["documented_realized_gain"], Decimal("200"))
        self.assertEqual(context["coverage"]["basis_coverage_pct"], Decimal("0.625"))
        self.assertIsNone(unresolved["documented_cost_basis"])
        self.assertIsNone(unresolved["documented_realized_gain"])
        self.assertEqual(unresolved["basis_status"], "UNKNOWN")
        self.assertEqual(lifetime_return["status"], "WITHHELD")
        self.assertIsNone(lifetime_return["value"])

    def test_missing_and_empty_hd_history_are_unavailable_not_documented_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = load_hd_historical_integration_context(metrics(), Path(directory) / "missing.sqlite")
        empty = build_hd_historical_integration_context({"transactions": [], "summary": {"documents": 0}}, metrics())

        self.assertFalse(missing["available"])
        self.assertFalse(empty["available"])
        self.assertNotIn("espp_employee_contributions", empty["documented_economics"])

    def test_consumers_degrade_gracefully_without_hd_history(self) -> None:
        context = unavailable_hd_historical_context()
        frame = metrics()
        analysis = build_analysis(frame, hd_historical_context=context)
        insights = build_portfolio_insights(frame, hd_historical_context=context)
        payload = build_export_payload(frame.to_dict(orient="records"), frame, history=[], external_context={"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "yahoo_metadata": []}, hd_historical_context=context, generated_at="2026-09-01T12:00:00")
        compact = compact_ai_context(payload, "Comprehensive Portfolio Review", "")

        self.assertFalse(analysis["hd_historical_context"]["available"])
        self.assertFalse(insights["hd_historical_context"]["available"])
        self.assertTrue(all("HD historical ESPP context" not in row for row in insights["observations"]))
        self.assertFalse(payload["hd_historical_context"]["available"])
        self.assertFalse(compact["hd_historical_context"]["available"])

    def test_analysis_insights_export_and_ai_use_same_context_values(self) -> None:
        context = build_hd_historical_integration_context(hd_state(), metrics())
        frame = metrics()
        analysis = build_analysis(frame, hd_historical_context=context)
        insights = build_portfolio_insights(frame, hd_historical_context=context)
        payload = build_export_payload(frame.to_dict(orient="records"), frame, history=[], external_context={"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "yahoo_metadata": []}, hd_historical_context=context, generated_at="2026-09-01T12:00:00")
        compact = compact_ai_context(payload, "Comprehensive Portfolio Review", "")

        self.assertEqual(analysis["hd_historical_context"]["documented_economics"]["espp_employee_contributions"], context["documented_economics"]["espp_employee_contributions"])
        self.assertEqual(insights["hd_historical_context"]["realized"]["documented_realized_gain"], context["realized"]["documented_realized_gain"])
        self.assertEqual(payload["hd_historical_context"]["coverage"]["basis_coverage_pct"], str(context["coverage"]["basis_coverage_pct"]))
        self.assertEqual(compact["hd_historical_context"]["documented_realized_gain"], str(context["realized"]["documented_realized_gain"]))
        self.assertEqual(compact["hd_historical_context"]["lifetime_return_status"], "WITHHELD")
        self.assertTrue(any("HD historical ESPP context" in item for item in insights["observations"]))
        self.assertTrue(any("Complete lifetime HD return remains WITHHELD" in item for item in insights["observations"]))
        self.assertEqual(analysis["snowball"]["annual_contribution"], 280 * 26)
        self.assertEqual(analysis["contributions"]["summary"]["annual_espp_contribution"], 2600.0)

    def test_export_markdown_json_unknowns_and_gating_are_safe(self) -> None:
        context = build_hd_historical_integration_context(hd_state(), metrics())
        frame = metrics()
        payload = build_export_payload(frame.to_dict(orient="records"), frame, history=[], external_context={"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "yahoo_metadata": []}, hd_historical_context=context, generated_at="2026-09-01T12:00:00")
        markdown = build_export_markdown(payload)
        encoded = json.loads(json.dumps(payload, default=str))
        unresolved = next(row for row in payload["hd_historical_context"]["realized"]["by_origin"] if row["origin"] == "UNRESOLVED")

        self.assertIn("## HD / ESPP Historical Economics", markdown)
        self.assertIn("Lifetime total return: WITHHELD", markdown)
        self.assertIsNone(unresolved["documented_cost_basis"])
        self.assertIsNone(unresolved["documented_realized_gain"])
        self.assertIsNone(unresolved["documented_realized_gain_pct"])
        self.assertIsNone(encoded["hd_historical_context"]["realized"]["by_origin"][-1]["documented_cost_basis"])
        hd_section = markdown.split("## HD / ESPP Historical Economics", 1)[1]
        self.assertNotIn("sanitized.pdf", json.dumps(encoded))
        self.assertNotIn("sanitized.pdf", hd_section)
        self.assertNotIn("account number", hd_section.lower())

    def test_ai_context_is_compact_and_contains_no_raw_transactions_or_documents(self) -> None:
        context = build_hd_historical_integration_context(hd_state(), metrics())
        frame = metrics()
        payload = build_export_payload(frame.to_dict(orient="records"), frame, history=[], external_context={"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "yahoo_metadata": []}, hd_historical_context=context, generated_at="2026-09-01T12:00:00")
        compact = compact_ai_context(payload, "Comprehensive Portfolio Review", "")
        serialized = json.dumps(compact, default=str)

        self.assertIn("hd_historical_context", compact)
        self.assertEqual(compact["hd_historical_context"]["lifetime_return_status"], "WITHHELD")
        self.assertIn("Lifetime Total Return", compact["hd_historical_context"]["withheld_metrics"])
        self.assertIn("UNRESOLVED", serialized)
        self.assertNotIn("transaction_fingerprint", serialized)
        self.assertNotIn("sanitized.pdf", serialized)

    def test_load_failure_is_sanitized_across_payload_ai_and_exports_but_logged(self) -> None:
        sensitive = r"C:\Users\example\Documents\Portfolio Tracker\data\history.sqlite"
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite"
            database.touch()
            with patch("hd_espp_integration.HDESPPHistoryService.state", side_effect=RuntimeError(sensitive)), self.assertLogs("portfolio_tracker", level="ERROR") as captured:
                context = load_hd_historical_integration_context(metrics(), database)
        frame = metrics()
        payload = build_export_payload(frame.to_dict(orient="records"), frame, history=[], external_context={"supplemental_investment_intelligence": [], "historical_security_context": [], "dividend_seasonality": {}, "yahoo_metadata": []}, hd_historical_context=context, generated_at="2026-09-01T12:00:00")
        compact = compact_ai_context(payload, "Comprehensive Portfolio Review", "")
        markdown = build_export_markdown(payload)
        encoded = json.dumps(payload, default=str)

        self.assertFalse(context["available"])
        self.assertEqual(context["summary"]["reason"], SAFE_UNAVAILABLE_REASON)
        for rendered in (json.dumps(context, default=str), json.dumps(compact, default=str), markdown, encoded):
            self.assertNotIn(sensitive, rendered)
            self.assertNotIn("history.sqlite", rendered)
        self.assertTrue(any(sensitive in row for row in captured.output))

    def test_streamlit_analysis_and_insights_use_optional_shared_context_without_zero_fallback(self) -> None:
        import app

        context = build_hd_historical_integration_context(hd_state(), metrics())
        captured_frames: list[pd.DataFrame] = []
        with patch.object(app.st, "markdown"), patch.object(app.st, "caption"), patch.object(app.st, "dataframe", side_effect=lambda frame, **kwargs: captured_frames.append(frame)):
            app._render_hd_historical_analysis_context(context)
        rendered = {row["Metric"]: row["Value"] for row in captured_frames[0].to_dict(orient="records")}
        self.assertEqual(rendered["Documented ESPP Contributions"], app._format_currency(context["documented_economics"]["espp_employee_contributions"]))
        self.assertEqual(rendered["Purchase Discount Benefit"], app._format_currency(context["documented_economics"]["espp_purchase_discount_benefit"]))
        self.assertEqual(rendered["Documented Realized Gain"], app._format_currency(context["realized"]["documented_realized_gain"]))
        self.assertEqual(rendered["Lifetime Return Status"], context["summary"]["lifetime_return_status"])

        package = build_portfolio_insights(metrics(), hd_historical_context=context)
        written: list[str] = []
        with patch.object(app.st, "markdown"), patch.object(app.st, "write", side_effect=lambda value: written.append(str(value))):
            app._render_hd_historical_insights_context(package)
        self.assertTrue(any(str(context["documented_economics"]["espp_employee_contributions"]) in row for row in written))
        self.assertTrue(any(context["summary"]["lifetime_return_status"] in row for row in written))

        with patch.object(app.st, "markdown") as markdown_mock, patch.object(app.st, "dataframe") as dataframe_mock, patch.object(app.st, "write") as write_mock:
            app._render_hd_historical_analysis_context(unavailable_hd_historical_context())
            app._render_hd_historical_insights_context({"hd_historical_context": unavailable_hd_historical_context(), "observations": []})
        markdown_mock.assert_not_called()
        dataframe_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_streamlit_main_passes_one_loaded_context_to_analysis_and_insights(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
        main_tail = source.split("unresolved_prices = metrics[", 1)[1]
        self.assertIn("hd_historical_context = load_hd_historical_integration_context(metrics)", main_tail)
        self.assertIn("_render_analysis_tab(metrics, mobile_mode=mobile_mode, hd_historical_context=hd_historical_context)", main_tail)
        self.assertIn("_render_insights_tab(metrics, mobile_mode=mobile_mode, hd_historical_context=hd_historical_context)", main_tail)
        insights_block = source.split("def _render_insights_tab", 1)[1].split("def _render_health_tab", 1)[0]
        self.assertIn("build_shared_portfolio_insights(metrics, hd_historical_context=hd_historical_context)", insights_block)


if __name__ == "__main__":
    unittest.main()

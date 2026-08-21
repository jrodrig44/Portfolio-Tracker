import json
import math
import unittest
from unittest.mock import Mock

import pandas as pd

from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.exports import build_export_payload, export_package
from portfolio_export import build_export_markdown, build_export_package, build_export_payload as shared_payload


class NiceGuiPhase1FExportTests(unittest.TestCase):
    def holdings(self):
        return [
            {"ticker": "CORE", "shares": 10, "current_price": 100, "amount_invested": 800, "annual_div_per_share": 2, "asset_type": "Stock", "sector": "Technology", "regular_biweekly_contribution": 280, "special_contribution": 500},
            {"ticker": "HD", "shares": 5, "current_price": 100, "amount_invested": 400, "annual_div_per_share": 4, "asset_type": "Stock", "sector": "Consumer Cyclical", "regular_biweekly_contribution": 280, "special_contribution": 500, "espp_biweekly_contribution": 100},
            {"ticker": "FUND", "shares": 5, "current_price": 100, "amount_invested": 450, "annual_div_per_share": 3, "asset_type": "ETF", "sector": "", "regular_biweekly_contribution": 0},
        ]

    def inputs(self):
        holdings = self.holdings()
        metrics = metrics_frame(holdings)
        history = [{"history_date": "2025-08-20", "portfolio_value": 1500}, {"history_date": "2026-08-20", "portfolio_value": 2000}]
        weights = {"FUND": {"Technology": 0.6, "Financials": 0.3}}
        external = {"supplemental_investment_intelligence": [{"ticker": "CORE", "pe_ratio": 20}], "historical_security_context": [], "dividend_seasonality": {}, "notes": []}
        return holdings, metrics, history, weights, external

    def test_wrapper_matches_shared_payload_and_package(self):
        holdings, metrics, history, weights, external = self.inputs()
        kwargs = {"history": history, "fund_weights": weights, "external_context": external, "generated_at": "2026-08-20T12:00:00"}
        self.assertEqual(build_export_payload(holdings, metrics, **kwargs), shared_payload(holdings, metrics, **kwargs))
        self.assertEqual(export_package(holdings, metrics, **kwargs), build_export_package(holdings, metrics, **kwargs))

    def test_streamlit_and_nicegui_use_same_canonical_production_package(self):
        import app

        holdings, metrics, history, weights, external = self.inputs()
        generated_at = "2026-08-20T12:00:00"
        with unittest.mock.patch("app._use_export_json_data", return_value=False), unittest.mock.patch("app._strict_export_accuracy_mode", return_value=False):
            streamlit_package = app._build_shared_production_export_package(
                pd.DataFrame(holdings), metrics, history=history, fund_weights=weights,
                external_context=external, generated_at=generated_at,
            )
        nicegui_package = export_package(
            holdings, metrics, history=history, fund_weights=weights,
            external_context=external, generated_at=generated_at,
            use_export_json_data=False, strict_export_accuracy=False, current_live_export=True,
        )
        self.assertEqual(json.loads(streamlit_package["portfolio_export.json"]), json.loads(nicegui_package["portfolio_export.json"]))
        self.assertEqual(streamlit_package["portfolio_export.md"], nicegui_package["portfolio_export.md"])

    def test_streamlit_accuracy_controls_are_explicit_shared_contract_inputs(self):
        import app

        holdings, metrics, history, weights, external = self.inputs()
        with unittest.mock.patch("app._use_export_json_data", return_value=True), unittest.mock.patch("app._strict_export_accuracy_mode", return_value=True):
            package = app._build_shared_production_export_package(pd.DataFrame(holdings), metrics, fund_weights={}, external_context={}, generated_at="2026-08-20T12:00:00")
        accuracy = json.loads(package["portfolio_export.json"])["export_accuracy"]
        self.assertEqual(accuracy, {"use_export_json_data": True, "strict_export_accuracy": True, "current_live_export": False, "market_data_resolution": "already_resolved_metrics"})

    def test_streamlit_orchestration_forwards_cached_production_enrichment(self):
        import app

        holdings, metrics, history, _, _ = self.inputs()
        intelligence = {"CORE": {"fundamental_metrics": {"pe_ratio": 20}, "data_source": "Yahoo Finance"}}
        historical = {"CORE": {"ticker": "CORE", "start_price": 80, "end_price": 100, "return_pct": 25}}
        with unittest.mock.patch("app._latest_fund_sector_weights", return_value={"FUND": {"Technology": 0.6, "Financials": 0.3}}) as weights_loader, unittest.mock.patch("app._latest_investment_intelligence", return_value=intelligence) as intelligence_loader, unittest.mock.patch("app._resolved_historical_context", return_value=historical) as history_loader, unittest.mock.patch("app._use_export_json_data", return_value=False), unittest.mock.patch("app._strict_export_accuracy_mode", return_value=False):
            package = app._build_shared_production_export_package(pd.DataFrame(holdings), metrics, history=history, generated_at="2026-08-20T12:00:00")
        payload = json.loads(package["portfolio_export.json"])
        self.assertTrue(payload["exposure"]["look_through_exposure"])
        self.assertEqual(payload["supplemental_investment_intelligence"][0]["ticker"], "CORE")
        self.assertEqual(payload["historical_security_context"][0]["return_pct"], 25)
        self.assertEqual(payload["dividend_data_metadata"]["source"], "Yahoo Finance")
        self.assertEqual(payload["dividend_seasonality"], {})
        self.assertEqual(payload["investor_profile"]["risk_tolerance"], "Moderate")
        self.assertTrue(payload["recommendation_constraints"]["avoid_forced_selling"])
        self.assertNotIn("External Yahoo enrichment was not supplied", package["portfolio_export.md"])
        weights_loader.assert_called_once_with(("FUND",))
        intelligence_loader.assert_called_once()
        history_loader.assert_called_once()

    def test_streamlit_orchestration_falls_back_safely_without_enrichment(self):
        import app

        holdings, metrics, history, _, _ = self.inputs()
        with unittest.mock.patch("app._latest_fund_sector_weights", return_value={}), unittest.mock.patch("app._latest_investment_intelligence", return_value={}), unittest.mock.patch("app._resolved_historical_context", return_value={}), unittest.mock.patch("app._use_export_json_data", return_value=False), unittest.mock.patch("app._strict_export_accuracy_mode", return_value=False):
            package = app._build_shared_production_export_package(pd.DataFrame(holdings), metrics, history=history, generated_at="2026-08-20T12:00:00")
        payload = json.loads(package["portfolio_export.json"])
        self.assertEqual(payload["exposure"]["look_through_exposure"], [])
        self.assertEqual(payload["exposure"]["unresolved_fund_exposure"][0]["Market_Value"], 500.0)
        self.assertIn("External Yahoo enrichment was not supplied for this export.", payload["external_context"]["notes"])

    def test_json_contains_current_summary_holdings_and_analysis_sections(self):
        holdings, metrics, history, weights, external = self.inputs()
        package = export_package(holdings, metrics, history=history, fund_weights=weights, external_context=external)
        payload = json.loads(package["portfolio_export.json"])
        self.assertEqual(payload["totals"]["portfolio_value"], 2000.0)
        self.assertEqual(payload["totals"]["cost_basis"], 1650.0)
        self.assertEqual(payload["totals"]["unrealized_gain"], 350.0)
        self.assertEqual(payload["totals"]["annual_income"], 55.0)
        self.assertEqual(payload["totals"]["holdings_count"], 3)
        self.assertEqual(payload["totals"]["largest_holding"], "CORE")
        self.assertEqual(len(payload["holdings"]), 3)
        for key in ("allocation", "exposure", "income", "contributions", "projections", "health_score", "insights", "historical", "external_context"):
            self.assertIn(key, payload)
        self.assertTrue(payload["allocation"]["economic_exposure"])
        self.assertTrue(payload["exposure"]["direct_sector_exposure"])
        self.assertTrue(payload["exposure"]["look_through_exposure"])
        self.assertTrue(payload["income"]["by_asset_type"])
        self.assertTrue(payload["income"]["quality"])

    def test_contribution_and_hd_espp_semantics(self):
        holdings, metrics, history, weights, external = self.inputs()
        contributions = build_export_payload(holdings, metrics)["contributions"]
        self.assertEqual(contributions["regular_biweekly"], 280.0)
        self.assertEqual(contributions["espp_biweekly"], 100.0)
        self.assertEqual(contributions["combined_recurring_biweekly"], 380.0)
        self.assertEqual(contributions["combined_recurring_annual"], 9880.0)
        self.assertEqual(contributions["special_contribution"], 500.0)
        self.assertNotEqual(contributions["combined_recurring_annual"], (280 + 100 + 500) * 26)
        hd = next(row for row in contributions["distribution_by_holding"] if row["ticker"] == "HD")
        self.assertEqual(hd["regular_biweekly_contribution"], 0.0)
        self.assertEqual(hd["special_contribution"], 0.0)
        self.assertEqual(hd["espp_biweekly_contribution"], 100.0)
        self.assertEqual(hd["espp_discount_pct"], 15.0)

    def test_health_completeness_fund_and_history_use_shared_results(self):
        holdings, metrics, history, weights, external = self.inputs()
        payload = build_export_payload(holdings, metrics, history=history, fund_weights=weights)
        health = payload["health_score"]
        self.assertIn("subscores", health)
        self.assertIn("strongest_areas", health)
        self.assertIn("weakest_areas", health)
        self.assertIn("health_score_methodology", health)
        self.assertLessEqual(payload["exposure"]["data_completeness"]["overall_resolved_exposure_coverage_pct"], 100.0)
        self.assertEqual(payload["exposure"]["unresolved_fund_exposure"][0]["Market_Value"], 50.0)
        self.assertEqual(payload["historical"]["summary"]["absolute_change"], 500.0)
        self.assertEqual(payload["historical"]["series"]["portfolio_values"], [1500.0, 2000.0])

    def test_markdown_has_comprehensive_sections_and_nominal_wording(self):
        holdings, metrics, history, weights, external = self.inputs()
        markdown = export_package(holdings, metrics, history=history, fund_weights=weights)["portfolio_export.md"]
        for heading in ("## AI Context Summary", "## Portfolio Health", "## Employer Stock / ESPP Context", "## Asset Allocation", "## Economic Exposure Allocation", "## Direct Sector Exposure", "## Look-Through Sector Exposure", "## Unresolved Fund Look-Through Exposure", "## Income Allocation and Quality", "## Projections", "## History / Performance Context", "## Data Limitations"):
            self.assertIn(heading, markdown)
        self.assertIn("nominal portfolio value change, not investment return", markdown)
        self.assertIn("Special contribution (not annualized): $500.00", markdown)

    def test_json_is_strict_when_inputs_contain_nonfinite_values(self):
        holdings, metrics, history, weights, external = self.inputs()
        metrics["annual_income"] = metrics["annual_income"].astype(float)
        metrics.loc[0, "annual_income"] = math.inf
        external["bad"] = float("nan")
        text = export_package(holdings, metrics, external_context=external)["portfolio_export.json"]
        json.loads(text)
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)

    def test_empty_and_incomplete_external_data_exports_are_valid(self):
        empty = pd.DataFrame()
        package = export_package([], empty, history=[], fund_weights={}, external_context={})
        payload = json.loads(package["portfolio_export.json"])
        self.assertEqual(payload["totals"]["portfolio_value"], 0.0)
        self.assertEqual(payload["holdings"], [])
        self.assertEqual(payload["historical"]["records"], [])
        self.assertIn("# Dividend Portfolio Tracker Export", package["portfolio_export.md"])

    def test_export_generation_has_no_database_or_network_dependencies(self):
        holdings, metrics, history, weights, external = self.inputs()
        database_loader = Mock()
        yahoo_loader = Mock()
        export_package(holdings, metrics, history=history, fund_weights=weights, external_context=external)
        database_loader.assert_not_called()
        yahoo_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()

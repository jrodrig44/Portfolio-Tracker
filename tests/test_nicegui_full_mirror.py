import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from nicegui_app.services import mirror_logic


ROOT = Path(__file__).resolve().parents[1]


class NiceGuiFullMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = (ROOT / "nicegui_app" / "main.py").read_text(encoding="utf-8")
        cls.logic_source = (ROOT / "nicegui_app" / "services" / "mirror_logic.py").read_text(encoding="utf-8")
        cls.launcher_source = (ROOT / "run_nicegui.ps1").read_text(encoding="utf-8")

    def _holdings(self) -> list[dict[str, object]]:
        return [{
            "ticker": "ABC", "shares": 2.0, "current_price": 50.0,
            "market_value": 100.0, "amount_invested": 80.0, "cost_basis": 80.0,
            "annual_div_per_share": 2.5, "dividend_yield_pct": 5.0,
            "annual_income": 5.0, "annualized_income": 5.0,
            "asset_type": "Stock", "sector": "Technology",
            "regular_biweekly_contribution": 10.0, "espp_biweekly_contribution": 0.0,
            "special_contribution": 0.0, "employer_stock": False,
            "espp_enabled": False, "espp_acquired_shares": 0.0,
            "espp_discount_pct": 0.0, "espp_purchase_frequency": "",
        }]

    def test_all_required_navigation_areas_exist(self) -> None:
        for area in ("Holdings", "Portfolio Analysis", "AI Analysis", "Portfolio Insights", "Portfolio Health", "History"):
            self.assertIn(f'"{area}"', self.main_source)

    def test_analysis_uses_production_calculation_outputs(self) -> None:
        metrics = mirror_logic.metrics_frame(self._holdings())
        package = mirror_logic.analysis_package(metrics)
        self.assertEqual(package["totals"]["total_portfolio_value"], 100.0)
        self.assertEqual(package["totals"]["total_cost_basis"], 80.0)
        self.assertIn("concentration", package)
        self.assertIn("income_quality", package)

    def test_insights_use_tracker_intelligence_builders(self) -> None:
        package = mirror_logic.insights_package(mirror_logic.metrics_frame(self._holdings()))
        self.assertTrue(package["observations"])
        self.assertEqual(package["allocation"][0]["market_value"], 100.0)

    def test_health_uses_existing_health_output(self) -> None:
        expected = {"health_score": 77.0, "subscores": {"risk": 70.0}}
        fake_app = unittest.mock.Mock()
        fake_app._build_portfolio_health.return_value = expected
        with patch("nicegui_app.services.mirror_logic._production_logic", return_value=fake_app):
            self.assertEqual(mirror_logic.health_package(pd.DataFrame()), expected)

    def test_ai_is_explicit_and_not_automatic(self) -> None:
        self.assertIn('ui.button("Analyze", on_click=analyze', self.main_source)
        self.assertNotIn("await run_ai_analysis", self.main_source)
        self.assertIn("run_ai_analysis, holdings, metrics", self.main_source)
        self.assertNotIn("run_ai_analysis(", self.main_source)

    def test_read_only_service_has_no_persistent_write_entrypoints(self) -> None:
        for forbidden in ("sqlite3.connect", "INSERT INTO", "UPDATE ", "DELETE FROM", "upsert_live_portfolio_snapshot", "run_statement_scan", "import_validated_history", "SupplementalResearchService"):
            self.assertNotIn(forbidden, self.logic_source)
        self.assertIn("load_history()", self.logic_source)

    def test_exports_are_generated_in_memory(self) -> None:
        self.assertIn("metrics.to_csv(index=False)", self.logic_source)
        self.assertNotIn("write_text", self.logic_source)
        self.assertNotIn("write_bytes", self.logic_source)

    def test_network_and_lazy_loading_are_stable(self) -> None:
        self.assertIn('HOST = "0.0.0.0"', self.main_source)
        self.assertIn("PORT = 8502", self.main_source)
        self.assertIn("tabs.on_value_change(navigate)", self.main_source)
        self.assertIn("selector.on_value_change", self.main_source)
        self.assertIn('ui.button("Load Yahoo intelligence"', self.main_source)
        self.assertIn("yahoo_intelligence_package, metrics", self.main_source)

    def test_launcher_is_attached_checks_port_and_never_kills_unrelated_python(self) -> None:
        self.assertIn("NICEGUI_LAUNCHER_PID", self.launcher_source)
        self.assertIn("Get-NetTCPConnection -LocalPort 8502", self.launcher_source)
        self.assertIn("& $venvPython $niceGuiMain", self.launcher_source)
        self.assertNotIn("Start-Process", self.launcher_source)
        self.assertNotIn("Stop-Process", self.launcher_source)
        self.assertNotIn("taskkill", self.launcher_source.lower())


if __name__ == "__main__":
    unittest.main()

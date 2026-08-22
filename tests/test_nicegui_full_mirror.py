import sqlite3,subprocess,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from nicegui_app.services.ai_context import compact_context
from nicegui_app.services.analysis import analysis_package
from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.exports import build_export_payload
from nicegui_app.services.health import health_package
from nicegui_app.services.insights import insights_package
from nicegui_app.services.refresh import ReadOnlyRefreshCoordinator,database_fingerprint

ROOT=Path(__file__).resolve().parents[1]

class NiceGuiFullMirrorTests(unittest.TestCase):
    def holdings(self):
        return [{"ticker":"ABC","shares":2.0,"current_price":50.0,"amount_invested":80.0,"annual_div_per_share":2.5,"effective_annual_div_per_share":0.0,"dividend_yield_pct":5.0,"asset_type":"Stock","sector":"Technology","regular_biweekly_contribution":10.0,"espp_biweekly_contribution":0.0,"special_contribution":0.0,"employer_stock":False,"espp_enabled":False,"espp_acquired_shares":0.0}]
    def test_isolated_import_does_not_load_production_ui_modules(self):
        code="import sys,nicegui_app.services.calculations,nicegui_app.services.analysis,nicegui_app.services.health,nicegui_app.services.insights,nicegui_app.services.exports,nicegui_app.services.ai_context,nicegui_app.services.yahoo,nicegui_app.services.refresh;print(int('app' in sys.modules),int('streamlit' in sys.modules))"
        result=subprocess.run([sys.executable,"-B","-c",code],cwd=ROOT,text=True,capture_output=True,check=True);self.assertEqual(result.stdout.strip(),"0 0")
    def test_nicegui_source_has_no_app_or_streamlit_imports(self):
        source="\n".join(path.read_text(encoding="utf-8") for path in (ROOT/"nicegui_app").rglob("*.py"));self.assertNotIn("\nimport app\n",source);self.assertNotIn("\nfrom app import",source);self.assertNotIn("import streamlit",source);self.assertNotIn("from streamlit",source)
    def test_representative_formula_outputs(self):
        metrics=metrics_frame(self.holdings());self.assertEqual(float(metrics.iloc[0]["market_value"]),100.0);self.assertEqual(float(metrics.iloc[0]["unrealized_gain"]),20.0);self.assertEqual(float(metrics.iloc[0]["annual_income"]),5.0)
        analysis=analysis_package(metrics);self.assertEqual(analysis["totals"]["weighted_portfolio_yield"],5.0);self.assertEqual(analysis["summary"]["active_contributors"],1)
        insights=insights_package(metrics);self.assertEqual(insights["yield"]["highest_yield_pct"],5.0);self.assertEqual(insights["contribution"]["summary"]["regular_annual"],260.0)
        health=health_package(metrics);self.assertIn("risk",health["subscores"]);self.assertTrue(0<=health["health_score"]<=100)
        payload=build_export_payload(self.holdings(),metrics);self.assertEqual(payload["totals"]["portfolio_value"],100.0);self.assertEqual(payload["schema_version"],"2.0")
        compact=compact_context(payload,"Risk Analysis","Focus",[{"history_date":"2026-01-01","portfolio_value":100}]);self.assertEqual(compact["portfolio_snapshot"]["portfolio_value"],100.0);self.assertNotIn("portfolio_history_summary",compact)
    def _databases(self,root):
        portfolio=root/"portfolio.db";history=root/"history.sqlite"
        with closing(sqlite3.connect(portfolio)) as c:c.execute("CREATE TABLE holdings (ticker TEXT,shares REAL,amount_invested REAL,current_price REAL,dividend_yield_pct REAL,asset_type TEXT,sector TEXT)");c.execute("INSERT INTO holdings VALUES ('ABC',2,80,50,5,'Stock','Technology')");c.commit()
        with closing(sqlite3.connect(history)) as c:c.execute("CREATE TABLE statement_portfolio_history (history_date TEXT,portfolio_value REAL,source_type TEXT)");c.execute("CREATE TABLE live_portfolio_snapshots (snapshot_date TEXT,portfolio_value REAL,source_type TEXT)");c.execute("INSERT INTO statement_portfolio_history VALUES ('2026-01-01',100,'stash_statement')");c.commit()
        return portfolio,history
    def test_fingerprint_detects_each_database_and_skips_unchanged(self):
        from nicegui_app.services.portfolio_data import load_history,load_holdings
        with tempfile.TemporaryDirectory() as directory:
            portfolio,history=self._databases(Path(directory));p_loader=Mock(side_effect=load_holdings);h_loader=Mock(side_effect=load_history);coordinator=ReadOnlyRefreshCoordinator(portfolio,history,p_loader,h_loader)
            first=coordinator.refresh();self.assertTrue(first.portfolio_changed and first.history_changed);second=coordinator.refresh();self.assertFalse(second.portfolio_changed or second.history_changed);self.assertEqual((p_loader.call_count,h_loader.call_count),(1,1))
            before=database_fingerprint(portfolio)
            with closing(sqlite3.connect(portfolio)) as c:c.execute("UPDATE holdings SET shares=3");c.commit()
            self.assertNotEqual(before,database_fingerprint(portfolio));third=coordinator.refresh();self.assertTrue(third.portfolio_changed);self.assertFalse(third.history_changed)
            with closing(sqlite3.connect(history)) as c:c.execute("INSERT INTO live_portfolio_snapshots VALUES ('2026-01-02',110,'live_portfolio_snapshot')");c.commit()
            fourth=coordinator.refresh();self.assertFalse(fourth.portfolio_changed);self.assertTrue(fourth.history_changed)
    def test_manual_refresh_forces_both_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            portfolio,history=self._databases(Path(directory));coordinator=ReadOnlyRefreshCoordinator(portfolio,history);coordinator.refresh();forced=coordinator.refresh(force=True);self.assertTrue(forced.portfolio_changed and forced.history_changed);self.assertEqual((len(forced.holdings),len(forced.history)),(1,1))
    def test_main_preserves_areas_refresh_network_and_lifecycle(self):
        main=(ROOT/"nicegui_app"/"main.py").read_text(encoding="utf-8");launcher=(ROOT/"run_nicegui.ps1").read_text(encoding="utf-8")
        for area in ("Holdings","Portfolio Analysis","AI Analysis","Portfolio Insights","Portfolio Health","History"):self.assertIn(f'"{area}"',main)
        self.assertIn("Refresh Data",main);self.assertIn("DB_REFRESH_INTERVAL_SECONDS",main);self.assertIn('HOST = "0.0.0.0"',main);self.assertIn('PORT = int(os.environ.get("PORTFOLIO_TRACKER_PORT", "8501"))',main);self.assertIn("NICEGUI_LAUNCHER_PID",launcher);self.assertIn("Start-Process -FilePath $venvPython",launcher);self.assertIn("-PassThru",launcher)

if __name__=="__main__":unittest.main()

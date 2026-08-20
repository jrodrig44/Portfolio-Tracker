import unittest
import math

from income_quality_engine import OPTION_INCOME_ETFS
from nicegui_app.services.analysis import analysis_package, analysis_with_fund_weights
from nicegui_app.services.calculations import metrics_frame
from portfolio_analysis import future_value


class NiceGuiPhase1BAnalysisTests(unittest.TestCase):
    def metrics(self):
        rows=[
            {"ticker":"HD","shares":4,"current_price":100,"amount_invested":320,"annual_div_per_share":4,"asset_type":"Stock","sector":"Consumer Cyclical","regular_biweekly_contribution":50,"espp_biweekly_contribution":0},
            {"ticker":"JEPI","shares":2,"current_price":50,"amount_invested":90,"annual_div_per_share":5,"asset_type":"ETF","sector":"","regular_biweekly_contribution":20},
            {"ticker":"SDIV","shares":2,"current_price":50,"amount_invested":100,"annual_div_per_share":6,"asset_type":"ETF","sector":""},
            {"ticker":"O","shares":1,"current_price":100,"amount_invested":80,"annual_div_per_share":5,"asset_type":"REIT","sector":"realestate"},
            {"ticker":"SCHD","shares":2,"current_price":50,"amount_invested":90,"annual_div_per_share":2,"asset_type":"ETF","sector":""},
            {"ticker":"AGG","shares":1,"current_price":100,"amount_invested":95,"annual_div_per_share":3,"asset_type":"Bond Fund","sector":""},
            {"ticker":"IEFA","shares":1,"current_price":100,"amount_invested":90,"annual_div_per_share":2,"asset_type":"ETF","sector":""},
        ]
        return metrics_frame(rows)

    def test_allocation_concentration_income_and_projection_parity(self):
        package=analysis_package(self.metrics())
        totals=package["totals"]
        self.assertEqual(totals["total_portfolio_value"],1000.0)
        self.assertEqual(totals["largest_holding"],"HD")
        self.assertEqual(totals["largest_holding_weight"],40.0)
        self.assertAlmostEqual(totals["weighted_portfolio_yield"],5.2)
        self.assertAlmostEqual(sum(row["allocation_pct"] for row in package["asset_allocation"]),100.0)
        self.assertAlmostEqual(sum(row["income_allocation_pct"] for row in totals["income_allocation_by_asset_type"]),100.0)
        self.assertAlmostEqual(package["concentration"]["largest_income_weight_pct"],16/52*100)
        self.assertEqual(package["snapshot"]["projected_10y_value"],future_value(1000,.08,280,10))

    def test_hd_and_contribution_context(self):
        context=analysis_package(self.metrics())["contributions"]
        hd=next(row for row in context["holdings"] if row["ticker"]=="HD")
        self.assertTrue(hd["employer_stock"] and hd["espp_enabled"])
        self.assertEqual(hd["regular_biweekly_contribution"],0)
        self.assertEqual(hd["espp_biweekly_contribution"],100)
        self.assertEqual(hd["espp_discount_pct"],15)
        self.assertEqual(context["regular_biweekly"],20)
        self.assertEqual(context["espp_biweekly"],100)
        self.assertEqual(context["summary"]["annual_regular_contribution"],520)
        self.assertEqual(context["summary"]["annual_espp_contribution"],2600)

    def test_income_quality_uses_production_engine_and_sdiv_is_not_option_income(self):
        package=analysis_package(self.metrics())
        quality={row["ticker"]:row for row in package["income_quality"]}
        self.assertEqual(quality["JEPI"]["income_quality_classification"],"Option-Income ETF Distribution")
        self.assertNotEqual(quality["SDIV"]["income_quality_classification"],"Option-Income ETF Distribution")
        self.assertNotIn("SDIV",OPTION_INCOME_ETFS)
        self.assertIn("income_quality_score",package["income_quality_summary"])

    def test_sector_alias_direct_exposure_and_fund_lookthrough(self):
        weights={"JEPI":{"technology":25,"realestate":.10},"SCHD":{"technology":.20}}
        package=analysis_with_fund_weights(self.metrics(),weights)
        direct={row["sector"]:row for row in package["direct_sector"]}
        self.assertIn("Real Estate",direct)
        self.assertNotIn("Unresolved Direct Sector",direct)
        lookthrough=package["fund_lookthrough"]
        sectors={row["Sector"]:row for row in lookthrough["records"]}
        self.assertAlmostEqual(sectors["Technology"]["Market_Value"],45.0)
        self.assertAlmostEqual(sectors["Real Estate"]["Market_Value"],10.0)
        self.assertAlmostEqual(lookthrough["resolved_value"],55.0)
        self.assertAlmostEqual(lookthrough["unresolved_value"],445.0)
        self.assertAlmostEqual(lookthrough["fund_lookthrough_completeness_pct"],11.0)
        self.assertAlmostEqual(package["data_completeness"]["overall_resolved_exposure_coverage_pct"],55.5)

    def test_fund_lookthrough_bounds_partial_exact_and_overflow_weights(self):
        metrics=metrics_frame([{"ticker":"FUND","shares":10,"current_price":100,"amount_invested":1000,"asset_type":"ETF"}])
        cases=(
            ({"Technology":.50,"Financials":30},800.0,200.0,80.0),
            ({"Technology":.60,"Financials":40},1000.0,0.0,100.0),
            ({"Technology":60,"Financials":50},1000.0,0.0,100.0),
        )
        for weights,resolved,unresolved,coverage in cases:
            with self.subTest(weights=weights):
                package=analysis_with_fund_weights(metrics,{"FUND":weights})
                lookthrough=package["fund_lookthrough"]
                self.assertAlmostEqual(lookthrough["resolved_value"],resolved)
                self.assertAlmostEqual(lookthrough["unresolved_value"],unresolved)
                self.assertAlmostEqual(lookthrough["fund_lookthrough_completeness_pct"],coverage)
                self.assertLessEqual(lookthrough["resolved_value"],lookthrough["fund_total_value"])
                self.assertGreaterEqual(lookthrough["unresolved_value"],0)
                self.assertLessEqual(lookthrough["fund_lookthrough_completeness_pct"],100)
                self.assertLessEqual(package["data_completeness"]["overall_resolved_exposure_coverage_pct"],100)
        overflow=analysis_with_fund_weights(metrics,{"FUND":{"Technology":60,"Financials":50}})["fund_lookthrough"]
        sectors={row["Sector"]:row["Market_Value"] for row in overflow["records"]}
        self.assertAlmostEqual(sectors["Technology"],1000*60/110)
        self.assertAlmostEqual(sectors["Financials"],1000*50/110)

    def test_fund_lookthrough_ignores_invalid_nonpositive_weights(self):
        metrics=metrics_frame([{"ticker":"FUND","shares":10,"current_price":100,"amount_invested":1000,"asset_type":"ETF"}])
        lookthrough=analysis_with_fund_weights(metrics,{"FUND":{"Technology":.25,"BadNaN":math.nan,"BadInf":math.inf,"Zero":0,"Negative":-2}})["fund_lookthrough"]
        self.assertAlmostEqual(lookthrough["resolved_value"],250.0)
        self.assertAlmostEqual(lookthrough["unresolved_value"],750.0)
        self.assertEqual([row["Sector"] for row in lookthrough["records"]],["Technology"])


if __name__=="__main__":
    unittest.main()

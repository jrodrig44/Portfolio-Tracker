import unittest

from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.insights import insights_package
from portfolio_insights import build_portfolio_insights


class NiceGuiPhase1DInsightsTests(unittest.TestCase):
    def metrics(self):
        return metrics_frame([
            {"ticker":"HD","shares":4,"current_price":100,"amount_invested":320,"annual_div_per_share":4,"dividend_yield_pct":4,"asset_type":"Stock","sector":"Consumer Cyclical","regular_biweekly_contribution":50},
            {"ticker":"JEPI","shares":2,"current_price":50,"amount_invested":90,"annual_div_per_share":5,"dividend_yield_pct":10,"asset_type":"ETF","sector":""},
            {"ticker":"SDIV","shares":2,"current_price":50,"amount_invested":100,"annual_div_per_share":6,"dividend_yield_pct":12,"asset_type":"ETF","sector":""},
            {"ticker":"O","shares":1,"current_price":100,"amount_invested":80,"annual_div_per_share":5,"dividend_yield_pct":5,"asset_type":"REIT","sector":"realestate"},
            {"ticker":"SCHD","shares":2,"current_price":50,"amount_invested":90,"annual_div_per_share":2,"dividend_yield_pct":4,"asset_type":"ETF","sector":"","regular_biweekly_contribution":20},
            {"ticker":"AGG","shares":1,"current_price":100,"amount_invested":95,"annual_div_per_share":3,"dividend_yield_pct":3,"asset_type":"Bond Fund","sector":""},
            {"ticker":"IEFA","shares":1,"current_price":100,"amount_invested":90,"annual_div_per_share":2,"dividend_yield_pct":2,"asset_type":"ETF","sector":""},
        ])

    def weights(self):
        return {"JEPI":{"Technology":.5},"SCHD":{"Technology":50},"AGG":{"Financial Services":100}}

    def test_wrapper_matches_shared_builder(self):
        metrics=self.metrics();weights=self.weights()
        self.assertEqual(insights_package(metrics,fund_weights=weights),build_portfolio_insights(metrics,fund_weights=weights))

    def test_allocation_concentration_yield_and_income_parity(self):
        result=insights_package(self.metrics(),fund_weights=self.weights())
        self.assertAlmostEqual(sum(row["allocation_pct"] for row in result["allocation"]),100.0)
        self.assertEqual(result["concentration"]["largest_holding_by_value"],"HD")
        self.assertEqual(result["concentration"]["largest_holding_weight_pct"],40.0)
        self.assertAlmostEqual(result["concentration"]["top3_income_pct"],38/52*100)
        self.assertEqual(result["yield"]["highest_yield_ticker"],"SDIV")
        self.assertEqual(result["yield"]["highest_yield_pct"],12.0)
        self.assertEqual(result["yield"]["lowest_positive_yield_ticker"],"IEFA")
        self.assertEqual(result["yield"]["yield_spread_pct"],10.0)
        self.assertAlmostEqual(result["totals"]["weighted_yield_pct"],5.2)
        self.assertEqual(result["concentration"]["largest_income_contributor"],"HD")
        self.assertAlmostEqual(sum(row["income_allocation_pct"] for row in result["income_allocation"]),100.0)

    def test_employer_stock_and_contribution_observations(self):
        result=insights_package(self.metrics(),fund_weights=self.weights())
        contribution=result["contribution"]
        hd=next(row for row in contribution["rows"] if row["ticker"]=="HD")
        self.assertEqual(hd["regular_biweekly_contribution"],0.0)
        self.assertEqual(hd["espp_biweekly_contribution"],100.0)
        self.assertEqual(hd["espp_discount_pct"],15.0)
        self.assertEqual(contribution["summary"]["regular_biweekly"],20.0)
        self.assertEqual(contribution["summary"]["regular_annual"],520.0)
        self.assertEqual(contribution["summary"]["espp_biweekly"],100.0)
        self.assertEqual(contribution["summary"]["espp_annual"],2600.0)
        self.assertEqual(contribution["active_contributors"],2)
        self.assertTrue(any("Employer-stock context" in text for text in result["observations"]))
        self.assertTrue(any("Contribution profile: 2" in text for text in result["observations"]))

    def test_income_quality_sector_and_lookthrough_context(self):
        result=insights_package(self.metrics(),fund_weights=self.weights())
        quality={row["ticker"]:row["income_quality_classification"] for row in result["income_quality"]}
        self.assertEqual(quality["JEPI"],"Option-Income ETF Distribution")
        self.assertNotEqual(quality["SDIV"],"Option-Income ETF Distribution")
        self.assertIn("Real Estate",{row["sector"] for row in result["direct_sector"]})
        self.assertAlmostEqual(result["fund_lookthrough"]["resolved_value"],200.0)
        self.assertAlmostEqual(result["fund_lookthrough"]["unresolved_value"],300.0)
        self.assertAlmostEqual(result["fund_lookthrough"]["fund_lookthrough_completeness_pct"],40.0)
        self.assertTrue(any("remains unresolved" in text for text in result["observations"]))
        self.assertTrue(0<=result["data_completeness"]["overall_resolved_exposure_coverage_pct"]<=100)

    def test_empty_and_minimal_portfolios_are_safe(self):
        empty=insights_package(metrics_frame([]))
        self.assertEqual(empty["totals"]["portfolio_value"],0.0)
        self.assertEqual(empty["yield"]["highest_yield_ticker"],"N/A")
        self.assertEqual(empty["observations"],[])
        minimal=insights_package(metrics_frame([{"ticker":"ONE","shares":1,"current_price":10,"amount_invested":10,"annual_div_per_share":0,"asset_type":"Stock"}]))
        self.assertEqual(minimal["concentration"]["largest_holding_weight_pct"],100.0)
        self.assertIsNone(minimal["yield"]["yield_spread_pct"])
        self.assertTrue(all(0<=row["allocation_pct"]<=100 for row in minimal["allocation"]))


if __name__=="__main__":
    unittest.main()

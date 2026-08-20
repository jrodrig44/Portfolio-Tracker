import unittest

from health_engine import compute_portfolio_health
from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.health import health_package
from portfolio_health import DISCLAIMER


class NiceGuiPhase1CHealthTests(unittest.TestCase):
    def metrics(self):
        rows=[
            {"ticker":"HD","shares":4,"current_price":100,"amount_invested":320,"annual_div_per_share":4,"dividend_yield_pct":4,"asset_type":"Stock","sector":"Consumer Cyclical"},
            {"ticker":"JEPI","shares":2,"current_price":50,"amount_invested":90,"annual_div_per_share":5,"dividend_yield_pct":10,"asset_type":"ETF","sector":""},
            {"ticker":"SDIV","shares":2,"current_price":50,"amount_invested":100,"annual_div_per_share":6,"dividend_yield_pct":12,"asset_type":"ETF","sector":""},
            {"ticker":"O","shares":1,"current_price":100,"amount_invested":80,"annual_div_per_share":5,"dividend_yield_pct":5,"asset_type":"REIT","sector":"realestate"},
            {"ticker":"SCHD","shares":2,"current_price":50,"amount_invested":90,"annual_div_per_share":2,"dividend_yield_pct":4,"asset_type":"ETF","sector":""},
            {"ticker":"AGG","shares":1,"current_price":100,"amount_invested":95,"annual_div_per_share":3,"dividend_yield_pct":3,"asset_type":"Bond Fund","sector":""},
            {"ticker":"IEFA","shares":1,"current_price":100,"amount_invested":90,"annual_div_per_share":2,"dividend_yield_pct":2,"asset_type":"ETF","sector":""},
        ]
        return metrics_frame(rows)

    def fund_weights(self):
        return {ticker:{"Technology":.5,"Financial Services":.5} for ticker in ("JEPI","SDIV","SCHD","AGG","IEFA")}

    def test_wrapper_matches_production_health_engine(self):
        metrics=self.metrics();weights=self.fund_weights()
        result=health_package(metrics,fund_weights=weights)
        expected=compute_portfolio_health(
            df=metrics,
            exposure={"unresolved_exposure":{"unresolved_fund_exposure":0.0,"fund_total_value":500.0}},
            income={"monthly_dividend_weights":{},"asset_type_map":{}},
            contributions={},
            espp_context={"employer_stock":True,"employer_ticker":"HD"},
            data_quality={"pct_unresolved_sector":5/7,"pct_missing_dividends":0.0,"pct_unresolved_lookthrough":0.0},
        )
        self.assertEqual(result["health_score"],expected["overall_score"])
        self.assertEqual(result["subscores"],expected["subscores"])
        self.assertEqual(result["largest_holding"],expected["largest_holding"])
        self.assertEqual(result["largest_holding_weight"],expected["largest_holding_weight_pct"])
        self.assertEqual(result["strongest"],expected["strongest_areas"])
        self.assertEqual(result["weakest"],expected["weakest_areas"])

    def test_health_context_alerts_income_classification_and_bounds(self):
        result=health_package(self.metrics(),fund_weights=self.fund_weights())
        self.assertEqual(result["disclaimer"],DISCLAIMER)
        self.assertTrue(any("Employer stock" in alert for alert in result["alerts"]))
        self.assertTrue(any("High-yield" in alert for alert in result["alerts"]))
        classifications=result["income_quality"]["holding_classifications"]
        self.assertEqual(classifications["JEPI"]["income_type"],"Option-Income ETF Distribution")
        self.assertNotEqual(classifications["SDIV"]["income_type"],"Option-Income ETF Distribution")
        self.assertTrue(all(0<=score<=100 for score in result["subscores"].values()))
        self.assertTrue(0<=result["health_score"]<=100)
        self.assertEqual(len(result["strongest_areas"]),3)
        self.assertEqual(len(result["weakest_areas"]),3)

    def test_strongest_and_weakest_areas_follow_engine_order_and_scores(self):
        metrics=self.metrics()
        for weights,data_quality_expected in (({},False),(self.fund_weights(),True)):
            with self.subTest(data_quality_expected=data_quality_expected):
                result=health_package(metrics,fund_weights=weights)
                expected=compute_portfolio_health(
                    df=metrics,
                    exposure={"unresolved_exposure":{"unresolved_fund_exposure":0.0 if weights else 500.0,"fund_total_value":500.0}},
                    income={"monthly_dividend_weights":{},"asset_type_map":{}},
                    contributions={},
                    espp_context={"employer_stock":True,"employer_ticker":"HD"},
                    data_quality={"pct_unresolved_sector":5/7,"pct_missing_dividends":0.0,"pct_unresolved_lookthrough":0.0 if weights else 1.0},
                )
                strongest_labels=[row["area"] for row in result["strongest_areas"]]
                weakest_labels=[row["area"] for row in result["weakest_areas"]]
                self.assertEqual(strongest_labels,expected["strongest_areas"])
                self.assertEqual(weakest_labels,expected["weakest_areas"])
                self.assertEqual("Data Quality" in strongest_labels,data_quality_expected)
                for row in result["strongest_areas"]:
                    key=row["area"].lower().replace(" ","_")
                    self.assertEqual(row["score"],round(result["subscores"][key],1))

    def test_unresolved_sector_and_fund_exposure_reduce_data_quality(self):
        complete=health_package(self.metrics(),fund_weights=self.fund_weights())
        unresolved=health_package(self.metrics(),fund_weights={})
        self.assertLess(unresolved["subscores"]["data_quality"],complete["subscores"]["data_quality"])
        self.assertEqual(unresolved["data_quality"]["pct_unresolved_lookthrough"],1.0)
        self.assertTrue(any("Unresolved sector exposure" in alert for alert in unresolved["alerts"]))
        metrics=self.metrics();metrics.loc[metrics["ticker"].eq("HD"),"sector"]=""
        missing_direct=health_package(metrics,fund_weights=self.fund_weights())
        self.assertLess(missing_direct["subscores"]["data_quality"],complete["subscores"]["data_quality"])
        self.assertIn("unresolved sectors",missing_direct["data_quality_explanation"])

    def test_empty_and_minimal_portfolios_are_safe(self):
        empty=health_package(metrics_frame([]))
        self.assertEqual(empty["health_score"],0.0)
        self.assertEqual(empty["largest_holding"],"N/A")
        self.assertTrue(all(0<=score<=100 for score in empty["subscores"].values()))
        minimal=health_package(metrics_frame([{"ticker":"ONE","shares":1,"current_price":10,"amount_invested":10,"asset_type":"Stock"}]))
        self.assertTrue(0<=minimal["health_score"]<=100)
        self.assertEqual(minimal["largest_holding_weight"],100.0)


if __name__=="__main__":
    unittest.main()

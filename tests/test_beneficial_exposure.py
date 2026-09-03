from decimal import Decimal
import unittest

import pandas as pd
from pathlib import Path

from beneficial_exposure import beneficial_hd_exposure, beneficial_metrics_frame


def context(equate="10.318744", merrill="12"):
    return {"available": True, "reconciliation": {"current_custody": {"equate_shares": Decimal(equate), "merrill_shares": Decimal(merrill), "combined_beneficial_shares": Decimal(equate) + Decimal(merrill), "reconciliation_complete": True, "as_of_date": "2026-06-30"}}}


class BeneficialExposureTests(unittest.TestCase):
    def setUp(self):
        self.metrics = pd.DataFrame([
            {"ticker": "HD", "shares": 12.0, "current_price": 300.0, "market_value": 3600.0, "current_value": 3600.0, "cost_basis": 3000.0, "unrealized_gain": 600.0, "annual_income": 108.0, "asset_type": "Stock", "sector": "Consumer Cyclical"},
            {"ticker": "ABC", "shares": 10.0, "current_price": 50.0, "market_value": 500.0, "current_value": 500.0, "cost_basis": 400.0, "unrealized_gain": 100.0, "annual_income": 20.0, "asset_type": "Stock", "sector": "Technology"},
        ])

    def test_custody_is_unchanged_and_equate_is_added_once(self):
        result = beneficial_hd_exposure(self.metrics, context())
        self.assertEqual(result["combined_beneficial_shares"], Decimal("22.318744"))
        self.assertEqual(result["total_beneficial_portfolio_value"], Decimal("7195.6232000"))
        self.assertEqual(float(self.metrics.iloc[0]["shares"]), 12.0)
        frame = beneficial_metrics_frame(self.metrics, context())
        self.assertAlmostEqual(float(frame.iloc[0]["shares"]), 22.318744)

    def test_transfer_does_not_change_value_and_price_does_not_change_quantity(self):
        before = beneficial_hd_exposure(self.metrics, context("10.318744", "12"))
        moved_metrics = self.metrics.copy(); moved_metrics.loc[0, "shares"] = 16; moved_metrics.loc[0, "market_value"] = 4800
        after = beneficial_hd_exposure(moved_metrics, context("6.318744", "16"))
        self.assertEqual(before["total_beneficial_portfolio_value"], after["total_beneficial_portfolio_value"])
        repriced = self.metrics.copy(); repriced.loc[0, ["current_price", "market_value"]] = [310, 3720]
        changed = beneficial_hd_exposure(repriced, context())
        self.assertEqual(changed["combined_beneficial_shares"], before["combined_beneficial_shares"])
        self.assertGreater(changed["combined_hd_market_value"], before["combined_hd_market_value"])

    def test_purchase_drip_sale_and_failed_transfer_propagate_generically(self):
        baseline = beneficial_hd_exposure(self.metrics, context())
        purchase = beneficial_hd_exposure(self.metrics, context("11.318744", "12"))
        drip = beneficial_hd_exposure(self.metrics, context("10.328744", "12"))
        sale = beneficial_hd_exposure(self.metrics, context("10.318744", "11"))
        failed = beneficial_hd_exposure(self.metrics, context())
        self.assertGreater(purchase["total_beneficial_portfolio_value"], baseline["total_beneficial_portfolio_value"])
        self.assertGreater(drip["combined_hd_market_value"], baseline["combined_hd_market_value"])
        self.assertLess(sale["combined_beneficial_shares"], baseline["combined_beneficial_shares"])
        self.assertEqual(failed, baseline)

    def test_production_evidence_is_ignored_and_fixture_is_sanitized(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("/data/hd_espp_evidence.json", (root / ".gitignore").read_text(encoding="utf-8"))
        fixture = (root / "tests" / "fixtures" / "hd_espp_evidence.json").read_text(encoding="utf-8")
        self.assertIn("sanitized fixture", fixture)
        self.assertNotIn("TV1425", fixture)


if __name__ == "__main__": unittest.main()

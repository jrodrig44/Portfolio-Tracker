import json
import unittest

import pandas as pd

from app import (
    _build_ai_export_json,
    _build_export_markdown,
    ESPP_CONTRIB_BIWEEKLY,
    _build_contribution_context,
    _normalize_columns,
    _normalize_espp_acquired_shares,
)


class HDESPPNormalizationTests(unittest.TestCase):
    def test_normalize_columns_repairs_hd_espp_legacy_contribution(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "shares": 16.670123,
                    "amount_invested": 5629.0,
                    "special_contribution": 100.0,
                    "espp_contribution_biweekly": 0.0,
                    "espp_acquired_shares": 16.670123,
                    "is_espp": False,
                    "espp_discount": 0.0,
                    "sector": "",
                    "asset_type": "Stock",
                }
            ]
        )

        normalized = _normalize_columns(holdings, drop_blank_tickers=False)
        row = normalized.iloc[0]

        self.assertEqual(row["regular_biweekly_contribution"], 0.0)
        self.assertEqual(row["special_contribution"], 0.0)
        self.assertEqual(row["espp_biweekly_contribution"], ESPP_CONTRIB_BIWEEKLY)
        self.assertEqual(row["espp_contribution_biweekly"], ESPP_CONTRIB_BIWEEKLY)
        self.assertTrue(row["is_espp"])
        self.assertEqual(row["amount_invested"], 5629.0)
        self.assertEqual(row["espp_purchase_frequency_months"], 6.0)

    def test_build_contribution_context_uses_espp_path_for_hd_and_clamps_espp_shares(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "shares": 10.0,
                    "amount_invested": 1000.0,
                    "regular_biweekly_contribution": 50.0,
                    "special_contribution": 100.0,
                    "espp_biweekly_contribution": 0.0,
                    "espp_contribution_biweekly": 0.0,
                    "espp_acquired_shares": 12.0,
                }
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "regular_biweekly_contribution": 50.0,
                    "espp_biweekly_contribution": 0.0,
                    "special_contribution": 100.0,
                }
            ]
        )

        contribution_context = _build_contribution_context(
            holdings,
            metrics,
            espp_contrib_biweekly=100.0,
            espp_purchase_frequency="approximately_every_6_months",
        )
        row = contribution_context["holdings"][0]

        self.assertEqual(row["regular_biweekly_contribution"], 0.0)
        self.assertEqual(row["special_contribution"], 0.0)
        self.assertEqual(row["espp_biweekly_contribution"], 100.0)
        self.assertEqual(row["contribution_type"], "espp")

        cleaned, changed = _normalize_espp_acquired_shares(
            pd.DataFrame([{"ticker": "HD", "shares": 10.0, "espp_acquired_shares": 12.0}])
        )
        self.assertTrue(changed)
        self.assertEqual(cleaned.iloc[0]["espp_acquired_shares"], 10.0)

    def test_decimal_espp_discount_exports_as_15_percent(self) -> None:
        holdings = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "shares": 10.0,
                    "amount_invested": 1000.0,
                    "espp_enabled": True,
                    "employer_stock": True,
                    "espp_discount_pct": 0.15,
                    "asset_type": "Stock",
                    "sector": "Consumer Cyclical",
                }
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "ticker": "HD",
                    "current_price": 100.0,
                    "market_value": 1000.0,
                    "annual_income": 10.0,
                    "portfolio_yield_pct": 1.0,
                    "cost_basis": 1000.0,
                    "unrealized_gain": 0.0,
                    "sector": "Consumer Cyclical",
                    "asset_type": "Stock",
                }
            ]
        )

        contribution_context = _build_contribution_context(holdings, metrics)
        payload = json.loads(_build_ai_export_json(holdings, metrics))
        markdown = _build_export_markdown(holdings, metrics)

        self.assertEqual(contribution_context["summary"]["hd_espp_discount_pct"], 15.0)
        self.assertEqual(payload["holdings"][0]["espp_discount_pct"], 15.0)
        self.assertIn("HD ESPP Discount: 15.0%", markdown)
        self.assertNotIn("1500.0%", markdown)


if __name__ == "__main__":
    unittest.main()

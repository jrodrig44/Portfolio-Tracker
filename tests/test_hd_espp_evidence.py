from decimal import Decimal
import unittest
from hd_espp_evidence import authoritative_hd_reconciliation

class AuthoritativeHDEvidenceTests(unittest.TestCase):
    def setUp(self): self.result = authoritative_hd_reconciliation()
    def test_transfer_and_failed_transfer_rules(self):
        self.assertEqual(self.result["transfers"]["march_2025_shares"], Decimal("4.000000")); self.assertEqual(self.result["transfers"]["july_2025_shares"], Decimal("4.000000")); self.assertEqual(self.result["transfers"]["failed_shares_ignored"], Decimal("5"))
    def test_custody_chains_and_combined_ownership(self):
        self.assertEqual(sum(self.result["equate_chain"]), Decimal("10.318744")); self.assertEqual(sum(self.result["merrill_chain"]), Decimal("12")); self.assertEqual(self.result["custody"]["combined_beneficial_shares"], Decimal("22.318744")); self.assertEqual(self.result["unresolved_quantity"], Decimal("0"))
    def test_remainder_and_legacy_components(self):
        self.assertEqual(self.result["transfers"]["july_2025_purchase_remainder"], Decimal("0.748081")); self.assertEqual(self.result["transfers"]["july_2024_component_total"], Decimal("14.969390"))
    def test_reported_and_reconstructed_gain_are_distinct(self):
        r = self.result["realized"]; self.assertEqual(r["broker_reported_realized_gain"], Decimal("1822.48")); self.assertEqual(r["reconstructed_realized_gain"], Decimal("2384.1959971317")); self.assertEqual(r["basis_provenance"], "reconstructed_from_equate_allocations"); self.assertEqual(r["remaining_unknown_basis_shares"], Decimal("0"))
    def test_june_sale(self):
        self.assertEqual(self.result["realized"]["june_2026"], {"shares": Decimal("9"), "gross_proceeds": Decimal("2819.52"), "fees": Decimal("0.06"), "net_proceeds": Decimal("2819.46"), "basis": Decimal("3160.35"), "realized_gain_loss": Decimal("-340.89")})

if __name__ == "__main__": unittest.main()

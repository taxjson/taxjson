"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import unittest


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestCollapsePreservesFractionalDelivery(unittest.TestCase):
    """Audit 2026-08 #12: `_collapse_cross_listing_chains` rebuilt the
    collapsed event without `fractional_delivery` / `cash_in_lieu`, so
    an IB merger chain through a cross-listing journal reverted to
    snapping a genuinely delivered fraction to whole shares plus
    fictitious cash-in-lieu — regressing the phantom-short fix.

    Formerly: test_audit_2026_08_fixes.TestCollapsePreservesFractionalDelivery"""

    def _chain(self):
        from taxjson.lib.corp_actions import CorporateAction
        merger = CorporateAction(
            date='2025-06-01', time='09:30:00', action_type='merger',
            source_symbol='SSL.TO', source_isin='CA0000000001',
            target_symbol='RGLD.CAD', target_isin='US7771950000',
            ratio_new=1, ratio_old=16,
            qty_disposed=120.0, qty_received=7.5,
            fmv=1000.0, currency='CAD', target_currency='CAD',
            account='margin',
            fractional_delivery=True,
            cash_in_lieu=12.34, cash_in_lieu_currency='USD',
        )
        journal = CorporateAction(
            date='2025-06-01', time='09:31:00', action_type='merger',
            source_symbol='RGLD.CAD', source_isin='US7771950000',
            target_symbol='RGLD', target_isin='US7771950000',
            ratio_new=1, ratio_old=1,
            qty_disposed=7.5, qty_received=7.5,
            fmv=1000.0, currency='CAD', target_currency='USD',
            account='margin',
            fractional_delivery=True,
        )
        return [merger, journal]

    def test_flags_survive_collapse(self):
        from taxjson.lib.corp_actions import _collapse_cross_listing_chains
        out = _collapse_cross_listing_chains(self._chain())
        self.assertEqual(len(out), 1)
        collapsed = out[0]
        self.assertEqual(collapsed.target_symbol, 'RGLD')
        self.assertTrue(
            collapsed.fractional_delivery,
            "collapse dropped fractional_delivery: 7.5 delivered shares "
            "would snap to 7 + fictitious cash-in-lieu, going phantom-"
            "short 0.5 when the user sells their actual 7.5")
        self.assertAlmostEqual(collapsed.cash_in_lieu, 12.34, places=2)
        self.assertEqual(collapsed.cash_in_lieu_currency, 'USD')


if __name__ == '__main__':
    unittest.main()

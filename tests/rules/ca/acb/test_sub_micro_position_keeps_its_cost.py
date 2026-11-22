"""Regression tests for fix round 7 (wave B), group coreus: the engines'
income and US paths.

TJ-0647 a US stock dividend's entitled sale before a split was counted
        in pre-split shares: the dividend ratio came out wrong.
TJ-0648 an entitled sale under the ticker a rename replaced was not
        found: the held shares took the whole dividend.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7b_coreus
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains(book, sheltered=(), year=2025, country="usa", **req):
    """pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


# ---------------------------------------------------------------- TJ-0649
class TestSubMicroPositionWithARealCost(unittest.TestCase):
    """0.0005 shares bought for 500 and consolidated 1:1000 are 0.0000005
    shares that still cost 500: held (inventory, cost timeline, T1135),
    and their sale for 600 a gain of 100, as when bought that small.

    Formerly: test_fix_1029_r7b_coreus.TestSubMicroPositionWithARealCost"""

    def _book(self, sold=True, consolidate=True):
        if consolidate:
            book = [tx("BUYSELL", "2025-01-06", "ZQD.US", 0.0005, 500.0),
                    tx("SPLIT", "2025-06-02", "ZQD.US", 0.001)]
        else:
            book = [tx("BUYSELL", "2025-01-06", "ZQD.US", 0.0000005, 500.0)]
        if sold:
            book.append(tx("BUYSELL", "2025-06-03", "ZQD.US", -0.0000005,
                           600.0))
        return book

    @rule(R.CA_ACB_01)
    def test_consolidated_into_a_fraction_then_sold(self):
        for consolidate in (True, False):
            res = _gains(self._book(consolidate=consolidate),
                         country="canada")
            self.assertAlmostEqual(res["summary"]["total_gain"], 100.0,
                                   places=4)
            self.assertNotIn("stranded", res["_stderr"])

    @rule(R.CA_ACB_01)
    def test_held_in_inventory_timeline_and_t1135(self):
        from taxjson.bin.taxjson_t1135 import walk_costs
        from taxjson.lib.cost_timeline import inventory_on
        for consolidate in (True, False):
            book = self._book(sold=False, consolidate=consolidate)
            res = _gains(book, country="canada", cost_timeline=True)
            inv = {i["symbol"]: i for i in res["inventory"]}
            self.assertAlmostEqual(inv["ZQD.US"]["total_cost"], 500.0)
            self.assertNotIn("stranded", res["_stderr"])
            tl = inventory_on(res["cost_timeline"], "2025-12-31")
            self.assertEqual([r["total_cost"] for r in tl], [500.0])
            f = walk_costs([t.to_dict() for t in book], 2025,
                           {"ZQD.US": "USA"})
            self.assertEqual(f["max_total_cost"], 500.0)

    @rule(R.CA_ACB_01)
    def test_a_sale_residue_is_still_flat(self):
        """A sale that leaves 4e-7 of 100 shares (cost 0.000004) drains
        the pool as before."""
        book = [tx("BUYSELL", "2025-01-06", "ZQD.US", 100, 1000.0),
                tx("BUYSELL", "2025-06-03", "ZQD.US", -99.9999996, 1200.0)]
        res = _gains(book, country="canada")
        self.assertEqual(res["inventory"], [])


if __name__ == "__main__":
    unittest.main()

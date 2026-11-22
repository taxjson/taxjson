"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.

Formerly: test_fix_a2_spec_gaps
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _sales(res):
    return [t for t in res["transactions"] if t.get("qty")]


def _engine(country, rows):
    """compute_gains of one country's engine on TaxTransactions."""
    from taxjson.engine.by_country import get_tax_rules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = get_tax_rules(country).compute_gains(list(rows))
    res["_stderr"] = err.getvalue()
    return res


class TestPhantomLossNeverDenied(unittest.TestCase):
    """CA-ACB-11 (mutant ACB11-taintfeeds) and US-BASIS-04 (mutant
    USB04-phantomnowash): a loss drawn on phantom (missing-history)
    shares is listed for manual reporting and never feeds the
    superficial-loss / wash-sale matching, even with a replacement
    bought in the window and still held.

    Formerly: test_fix_a2_spec_gaps.TestPhantomLossNeverDenied"""

    @rule(R.CA_ACB_11)
    def test_canada_tainted_loss_with_held_rebuy(self):
        res = _engine("canada", [
            tx("OPENING_BALANCE", "2026-01-02", "TNT.TO", 100, 0.0,
               currency="CAD", time="00:00:00", price=0.0),
            tx("BUYSELL", "2026-01-05", "TNT.TO", 100, 5000.0,
               currency="CAD"),
            tx("BUYSELL", "2026-02-02", "TNT.TO", -200, 4000.0,
               currency="CAD"),
            tx("BUYSELL", "2026-02-10", "TNT.TO", 100, 2000.0,
               currency="CAD")])
        self.assertEqual(res["wash_sales"], [])
        loss = [t for t in _sales(res) if t["qty"] == 200][0]
        self.assertTrue(loss["tainted"])
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)
        inv = {i["symbol"]: i for i in res["inventory"]}
        self.assertAlmostEqual(inv["TNT.TO"]["total_cost"], 2000.0,
                               places=2)

    @rule(R.US_BASIS_04)
    def test_usa_tainted_lot_loss_with_held_rebuy(self):
        # A phantom lot (basis 0) sold for less than nothing (the
        # commission exceeded the gross) is a loss on a tainted lot.
        res = _engine("usa", [
            tx("OPENING_BALANCE", "2025-01-02", "TNT.US", 10, 0.0,
               time="00:00:00", price=0.0),
            tx("BUYSELL", "2025-03-03", "TNT.US", -10, -5.0, price=0.01),
            tx("BUYSELL", "2025-03-10", "TNT.US", 10, 100.0)])
        self.assertEqual(res["wash_sales"], [])
        loss = [t for t in _sales(res) if t["qty"] in (10, -10)
                and t["date"] == "2025-03-03"][0]
        self.assertTrue(loss["tainted"])
        self.assertAlmostEqual(loss["disallowed_amount"], 0.0)
        lots = [i for i in res["inventory"] if i["symbol"] == "TNT.US"]
        self.assertAlmostEqual(sum(i["total_cost"] for i in lots), 100.0,
                               places=2)


if __name__ == "__main__":
    unittest.main()

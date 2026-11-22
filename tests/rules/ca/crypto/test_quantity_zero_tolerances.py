"""Re-audit-2 test pins (tests-pins-03), the gains engines: each test
fails when the rule it names is reverted or mutated. Synthetic books
only (QZ* tickers, round numbers).

Formerly: test_fix_a2_pins1_03_engine"""
import unittest

import contextlib
import io

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as RID

from taxjson.engine.ca.taxrules import CanadaTaxRules


def _quiet(engine, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return engine.compute_gains(*a, **kw)


class TestCanadaQuantityTolerances(unittest.TestCase):
    """A2-1548: CA-CRYPTO-09's two stated numbers — under a millionth of
    a share is zero; a coin residue is zero only under a hundred-
    billionth of the position.

    Formerly: test_fix_a2_pins1_03_engine.TestCanadaQuantityTolerances"""

    def _left(self, sym, buy, sell):
        r = _quiet(CanadaTaxRules(), [
            tx("BUYSELL", "2025-01-02", sym, buy, 1000.0, currency="CAD"),
            tx("BUYSELL", "2025-02-03", sym, -sell, 1200.0, currency="CAD")])
        return sum(float(i.get('qty') or 0) for i in r.get('inventory', [])
                   if i.get('symbol') == sym)

    @rule(RID.CA_CRYPTO_09)
    def test_share_residue_around_a_millionth(self):
        self.assertAlmostEqual(self._left("QZA.TO", 10, 10 - 5e-6), 5e-6,
                               delta=1e-9)
        self.assertEqual(self._left("QZA.TO", 10, 10 - 5e-7), 0)

    @rule(RID.CA_CRYPTO_09)
    def test_coin_residue_around_a_hundred_billionth(self):
        self.assertAlmostEqual(self._left("BTC", 10, 10 - 5e-9), 5e-9,
                               delta=1e-12)
        self.assertEqual(self._left("BTC", 10, 10 - 5e-11), 0)


if __name__ == "__main__":
    unittest.main()

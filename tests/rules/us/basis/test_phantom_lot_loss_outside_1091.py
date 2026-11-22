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

from taxjson.engine.us.taxrules import USATaxRules


def _quiet(engine, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return engine.compute_gains(*a, **kw)


class TestUsTaintedLongLotNeverFeeds1091(unittest.TestCase):
    """A2-0548: a sale drawing on a phantom (OPENING_BALANCE) lot is
    manual-reporting only; its loss never feeds §1091 (US-BASIS-04),
    even when an ADJUST raised the lot's basis so it can lose.

    Formerly: test_fix_a2_pins1_03_engine.TestUsTaintedLongLotNeverFeeds1091"""

    def _book(self, sell_rebuy):
        b = [tx("OPENING_BALANCE", "2025-01-02", "PHL.US", 100, 0),
             tx("ADJUST", "2025-02-03", "PHL.US", 0, 1000),
             tx("BUYSELL", "2025-03-03", "PHL.US", -100, 500),
             tx("BUYSELL", "2025-03-10", "PHL.US", 100, 520)]
        if sell_rebuy:
            b.append(tx("BUYSELL", "2025-03-20", "PHL.US", -100, 530))
        return _quiet(USATaxRules(), b)

    @rule(RID.US_BASIS_04)
    def test_rebuy_held(self):
        r = self._book(False)
        self.assertEqual(r['summary']['total_disallowed'], 0)
        self.assertFalse(r.get('wash_sales'))

    @rule(RID.US_BASIS_04)
    def test_rebuy_sold_keeps_its_own_basis(self):
        r = self._book(True)
        self.assertEqual(r['summary']['total_disallowed'], 0)
        clean = [t for t in r['transactions']
                 if t.get('date') == '2025-03-20' and 'gain' in t]
        self.assertEqual(len(clean), 1)
        self.assertAlmostEqual(clean[0]['gain'], 10.0, places=2)


if __name__ == "__main__":
    unittest.main()

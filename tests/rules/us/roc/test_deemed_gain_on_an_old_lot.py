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


class TestUsRocExcessTerm(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_engine.TestUsRocExcessTerm"""
    @rule(RID.US_ROC_02)
    def test_excess_on_a_lot_held_over_a_year_is_long_term(self):
        """A2-0921: the §301(c)(3) gain takes the lot's holding period."""
        r = _quiet(USATaxRules(), [
            tx("BUYSELL", "2023-01-03", "QRX.US", 100, 1000.0),
            tx("ADJUST", "2025-06-02", "QRX.US", 0, -1500.0)])
        rows = [g for g in r['transactions'] if not g.get('action')
                and g.get('date') == '2025-06-02']
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['gain'], 500.0, places=2)
        self.assertEqual(rows[0]['term'], 'LONG_TERM')


if __name__ == "__main__":
    unittest.main()

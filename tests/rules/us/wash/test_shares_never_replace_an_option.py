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


class TestUsSharesNeverReplaceAnOption(unittest.TestCase):
    """A2-0549: share for share (US-WASH-02); only the identical option
    replaces an option loss (US-WASH-03).

    Formerly: test_fix_a2_pins1_03_engine.TestUsSharesNeverReplaceAnOption"""
    OPT = "XYZ251219C00050000.US"

    def _dis(self, book):
        return round(_quiet(USATaxRules(), book)['summary']
                     ['total_disallowed'], 2)

    @rule(RID.US_WASH_02)
    def test_share_buy_after_closed_option_loss(self):
        self.assertEqual(self._dis([
            tx("BUYSELL", "2025-03-03", self.OPT, 2, 1000),
            tx("BUYSELL", "2025-04-01", self.OPT, -2, 400),
            tx("BUYSELL", "2025-04-10", "XYZ.US", 100, 5000)]), 0)

    @rule(RID.US_WASH_02, RID.US_WASH_17)
    def test_share_buy_with_option_partly_held(self):
        """The contract kept from the same purchase (bought 29 days
        before the loss) replaces one of the two sold; the 100 shares
        replace nothing."""
        self.assertEqual(self._dis([
            tx("BUYSELL", "2025-03-03", self.OPT, 3, 1500),
            tx("BUYSELL", "2025-04-01", self.OPT, -2, 400),
            tx("BUYSELL", "2025-04-10", "XYZ.US", 100, 5000)]), 300.0)

    @rule(RID.US_WASH_03)
    def test_identical_option_rebought_is_a_replacement(self):
        self.assertEqual(self._dis([
            tx("BUYSELL", "2025-03-03", self.OPT, 2, 1000),
            tx("BUYSELL", "2025-04-01", self.OPT, -2, 400),
            tx("BUYSELL", "2025-04-10", self.OPT, 2, 500)]), 600.0)


if __name__ == "__main__":
    unittest.main()

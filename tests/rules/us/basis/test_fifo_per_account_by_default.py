"""Deferred round, engine-t1135 area: the T1135 cost walk carries every
s.53(1)(f) addition (S008-07, S009-01, S051-21).

Formerly: test_fix_x_engine_t1135"""
import unittest

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R


class TestUSPerAccountFifoDefault(unittest.TestCase):
    """SPEC-30: run_gains(country=usa) on a merged two-account book keeps
    FIFO per account by default (as the blended pass always did); the
    same book under Canada is one s.47 pool across the accounts.

    Formerly: test_fix_x_engine_t1135.TestUSPerAccountFifoDefault"""

    @rule(R.US_BASIS_01)
    @rule_absent(R.US_BASIS_01, country="canada")
    @rule(R.CA_ACB_01)
    @rule_absent(R.CA_ACB_01, country="usa")
    def test_merged_book_fifo_per_account(self):
        from tax_rules.dual import gains_both, tx
        book = [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 1000,
                   account="A"),
                tx("BUYSELL", "2025-02-03", "XYZ.US", 100, 2000,
                   account="B"),
                tx("BUYSELL", "2025-09-02", "XYZ.US", -100, 1500,
                   account="B")]
        r = gains_both(book)
        self.assertAlmostEqual(r["usa"]["summary"]["total_gain"], -500.0,
                               places=2)               # B's own lot at 20
        self.assertAlmostEqual(r["canada"]["summary"]["total_gain"], 0.0,
                               places=2)               # s.47 average 15


if __name__ == "__main__":
    unittest.main()

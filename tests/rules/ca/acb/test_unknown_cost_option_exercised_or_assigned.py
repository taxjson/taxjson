"""Regression tests for fix round 7 (wave C), group engine: the engines
(lib/core.py, lib/corp_actions.py, lib/futures.py).

TJ-0947 an option held at an unknown cost (an OPENING cost=unknown),
        exercised: its shares were booked at the strike alone, a
        definite gain; their cost is unknown too.
TJ-0948 a taxable spin-off's fraction paid in cash was left out of the
        dividend, and the broker's $0 fraction diluted the new shares'
        cost.
TJ-0949 two broker accounts' exports in one folder: a short future
        opened in one closed the long held in the other.
TJ-0951 a US §355 spin-off allocated basis from lots bought after its
        record date.
TJ-0952 buying back a warrant written short was named as a
        superficial-loss replacement.
TJ-0953 a US short sale covered at a loss in December and delivered in
        January was the earlier year's loss.
TJ-0954 a warrant exercise with no shares per warrant stated, delivered
        in several rows: the first row took all the warrants' cost.
TJ-0956 a US return of capital beyond many small lots' basis: each
        lot's sub-cent excess was left as negative basis.

Every fixture is synthetic: invented tickers (SYN*), account labels
and amounts.

Formerly: test_fix_1029_r7c_engine
"""
import unittest

from tax_rules import rule
from tax_rules.dual import gains_both, tx
from taxjson.rules import ids as R


# ---------------------------------------------------------------- TJ-0947
class TestUnknownCostOptionExercise(unittest.TestCase):
    """One long SYN call held before the data (cost unknown), exercised
    into 100 SYN at 10 (1000) and the shares sold for 1200. With the
    call's purchase in the book (100), the sale's cost is 1100 (gain
    100). Without it, the cost is unknown: the sale is reported by
    hand, never a definite gain of 200.

    Formerly: test_fix_1029_r7c_engine.TestUnknownCostOptionExercise"""

    OPT = 'SYN250620C00010000.US'

    def _book(self, known):
        first = (tx('BUYSELL', '2024-12-20', self.OPT, 1, 100) if known
                 else tx('OPENING_BALANCE', '2024-12-31', self.OPT, 1, 0,
                         time='00:00:00'))
        return [first,
                tx('ASSIGN', '2025-06-10', self.OPT, -1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', 100, 1000,
                   time='10:01:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -100, 1200)]

    @rule(R.CA_ACB_11)
    @rule(R.US_BASIS_04)
    def test_unknown_cost_call_makes_its_shares_unknown_cost(self):
        for c, res in gains_both(self._book(False), year=2025).items():
            with self.subTest(country=c):
                self.assertEqual(
                    [t for t in res['transactions']
                     if t['symbol'] == 'SYN.US'], [])
                manual = res.get('manual_reporting_required') or []
                self.assertEqual([(m['symbol'], m['qty']) for m in manual],
                                 [('SYN.US', 100.0)])

    def test_known_cost_call_still_carries_its_premium(self):
        for c, res in gains_both(self._book(True), year=2025).items():
            with self.subTest(country=c):
                sale = [t for t in res['transactions']
                        if t['symbol'] == 'SYN.US']
                self.assertEqual(len(sale), 1)
                self.assertAlmostEqual(sale[0]['cost'], 1100.0, places=4)
                self.assertAlmostEqual(sale[0]['gain'], 100.0, places=4)
                self.assertFalse(res.get('manual_reporting_required'))

    def test_unknown_cost_written_put_assigned(self):
        """A written put held before the data (premium unknown),
        assigned: the 100 shares bought at the strike carry it."""
        put = 'SYN250620P00010000.US'
        book = [tx('OPENING_BALANCE', '2024-12-31', put, -1, 0,
                   time='00:00:00'),
                tx('ASSIGN', '2025-06-10', put, 1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', 100, 1000,
                   time='10:01:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -100, 1200)]
        for c, res in gains_both(book, year=2025).items():
            with self.subTest(country=c):
                manual = res.get('manual_reporting_required') or []
                self.assertEqual([(m['symbol'], m['qty']) for m in manual],
                                 [('SYN.US', 100.0)])

    def test_unknown_cost_long_put_exercised(self):
        """A long put held before the data, exercised against 100
        known shares: the sale's proceeds carry its unknown cost."""
        put = 'SYN250620P00012000.US'
        book = [tx('BUYSELL', '2025-01-06', 'SYN.US', 100, 1000),
                tx('OPENING_BALANCE', '2024-12-31', put, 1, 0,
                   time='00:00:00'),
                tx('ASSIGN', '2025-06-10', put, -1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', -100, 1200,
                   time='10:01:00')]
        for c, res in gains_both(book, year=2025).items():
            with self.subTest(country=c):
                self.assertEqual(
                    [t for t in res['transactions']
                     if t['symbol'] == 'SYN.US'], [])
                manual = res.get('manual_reporting_required') or []
                self.assertEqual([(m['symbol'], m['qty']) for m in manual],
                                 [('SYN.US', 100.0)])


if __name__ == '__main__':
    unittest.main()

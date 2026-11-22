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
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


# ---------------------------------------------------------------- TJ-0954
class TestUnsizedWarrantExerciseInParts(unittest.TestCase):
    """100 SYN.WT bought for 100, exercised into 100 SYN at 10 delivered
    as two same-day legs of 50, with no shares per warrant stated; 50
    sold a month later for 600. Each leg carries 500 + 50: the sale's
    cost is 550 (gain 50), as with `x1` stated.

    Formerly: test_fix_1029_r7c_engine.TestUnsizedWarrantExerciseInParts"""

    def _sale(self, country, size):
        ex = tx('ASSIGN', '2025-06-02', 'SYN.WT.US', -100, 0,
                exercise_of='SYN.US', **({'multiplier': size}
                                         if size else {}))
        book = [tx('BUYSELL', '2024-01-08', 'SYN.WT.US', 100, 100), ex,
                tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 500,
                   time='10:01:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 500,
                   time='10:02:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -50, 600)]
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            res = run_gains(list(book), [], [], req=GainsRequest(
                country=country, taxable=True, year=2025,
                phantom_hint=False))
        sales = [t for t in res['transactions'] if t['symbol'] == 'SYN.US']
        return sales, err.getvalue()

    @rule(R.US_OPT_06)
    def test_us_cost_is_shared_by_the_deliveries(self):
        for size in (0, 1):
            with self.subTest(size=size):
                sales, err = self._sale('usa', size)
                self.assertEqual(len(sales), 1)
                self.assertAlmostEqual(sales[0]['cost'], 550.0, places=4)
                self.assertEqual('states no shares per warrant' in err,
                                 not size, err[-600:])

    @rule(R.CA_OPT_09)
    def test_canada_cost_is_shared_by_the_deliveries(self):
        sales, _err = self._sale('canada', 0)
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0]['cost'], 550.0, places=4)


if __name__ == '__main__':
    unittest.main()

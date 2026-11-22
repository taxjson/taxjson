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


def _gains(book, country, year=2025):
    """pipeline.run_gains under one country (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return run_gains(list(book), [], [], req=GainsRequest(
            country=country, taxable=True, year=year,
            phantom_hint=False))


# ---------------------------------------------------------------- TJ-0956
class TestUsRocExcessAcrossSmallLots(unittest.TestCase):
    """500 one-share SYN lots bought 2024-01-08 for 0.50 each (basis
    250), then a 252 return of capital on 2025-06-02: 2 of long-term
    gain in 2025 and zero basis, as for one 500-share lot.

    Formerly: test_fix_1029_r7c_engine.TestUsRocExcessAcrossSmallLots"""

    def _run(self, lots):
        each = 500 // lots
        book = [tx('BUYSELL', '2024-01-08', 'SYN.US', each, 0.5 * each,
                   time=f'10:{i // 60:02d}:{i % 60:02d}')
                for i in range(lots)]
        book.append(tx('ADJUST', '2025-06-02', 'SYN.US', 0, -252))
        return _gains(book, 'usa', year=2025)

    @rule(R.US_ROC_02)
    def test_fragmented_lots_book_the_excess(self):
        for lots in (1, 500):
            with self.subTest(lots=lots):
                res = self._run(lots)
                rows = [t for t in res['transactions'] if t.get('deemed')]
                self.assertEqual(len(rows), 1)
                self.assertAlmostEqual(rows[0]['gain'], 2.0, places=6)
                self.assertEqual(rows[0]['term'], 'LONG_TERM')
                self.assertEqual(rows[0]['acquired_date'], '2024-01-08')


if __name__ == '__main__':
    unittest.main()

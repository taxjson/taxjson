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


# ---------------------------------------------------------------- TJ-0953
class TestUsShortLossDeliveredInJanuary(unittest.TestCase):
    """10 SYN sold short 2025-01-06 for 100 and covered for 120 on
    2025-12-31, delivered 2026-01-02: the 20 loss is 2026's (Rev. Rul.
    2002-44, Situation 1). A cover on 2025-12-30 settling 2025-12-31 is
    2025's; a cover at a gain keeps its trade date (§1259).

    Formerly: test_fix_1029_r7c_engine.TestUsShortLossDeliveredInJanuary"""

    def _years(self, day, settle, cover=120):
        book = [tx('BUYSELL', '2025-01-06', 'SYN.US', -10, 100),
                tx('BUYSELL', day, 'SYN.US', 10, cover, settle=settle)]
        out = {}
        for y in (2025, 2026):
            res = _gains(book, 'usa', year=y)
            out[y] = [(t['date'], round(t['gain'], 4))
                      for t in res['transactions']]
        return out

    @rule(R.US_DATE_01)
    def test_loss_is_the_delivery_years(self):
        self.assertEqual(self._years('2025-12-31', '2026-01-02'),
                         {2025: [], 2026: [('2026-01-02', -20.0)]})

    def test_loss_settled_in_the_same_year(self):
        self.assertEqual(self._years('2025-12-30', '2025-12-31'),
                         {2025: [('2025-12-30', -20.0)], 2026: []})

    def test_gain_keeps_its_trade_date(self):
        self.assertEqual(self._years('2025-12-31', '2026-01-02', cover=80),
                         {2025: [('2025-12-31', 20.0)], 2026: []})


if __name__ == '__main__':
    unittest.main()

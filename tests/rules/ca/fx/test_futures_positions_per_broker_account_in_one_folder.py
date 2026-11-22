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
from taxjson.rules import ids as R


# ---------------------------------------------------------------- TJ-0949
def _fut(i, date, qty, net, src, account='a', sym='F:SYNZ6.US'):
    from taxjson.model.transaction import TaxTransaction
    return TaxTransaction(id=i, action='BUYSELL', date=date,
                          date_settle=date, time='10:00:00', symbol=sym,
                          quantity=qty, net_amount=net, currency='CAD',
                          account=account, source_account=src)


class TestFuturesBrokerAccountsInOneFolder(unittest.TestCase):
    """Two broker accounts' exports in one taxable folder: a long bought
    at 100 in one, a short opened at 120 in the other (2025); the long
    sold at 150 and the short covered at 110 in 2026. Nothing closes in
    2025; 2026 realizes 50 + 10 = 60, as with a folder per account.

    Formerly: test_fix_1029_r7c_engine.TestFuturesBrokerAccountsInOneFolder"""

    def _book(self, src_b='bbbbbbbbbb'):
        return [_fut('l1', '2025-01-06', 1, 100, 'aaaaaaaaaa'),
                _fut('s1', '2025-02-03', -1, 120, src_b),
                _fut('l2', '2026-01-05', -1, 150, 'aaaaaaaaaa'),
                _fut('s2', '2026-02-02', 1, 110, src_b)]

    def _parts(self, book):
        from taxjson.lib.futures import settle_futures
        rows, _ = settle_futures(book, 'average')
        return [(r.date, r.quantity, round(r.net_amount, 6),
                 r.settle_native.split()[0]) for r in rows]

    def test_settlement_keeps_each_broker_accounts_position(self):
        self.assertEqual(self._parts(self._book()), [
            ('2025-01-06', 1.0, 0.0, 'LO'),
            ('2025-02-03', -1.0, 0.0, 'SO'),
            ('2026-01-05', -1.0, 50.0, 'LC'),
            ('2026-02-02', 1.0, 10.0, 'SC')])

    def test_a_row_naming_no_broker_account_keeps_one_book(self):
        """A .tt line (no broker account) beside an export: one book,
        as before."""
        self.assertEqual(self._parts(self._book(src_b=''))[1],
                         ('2025-02-03', -1.0, 20.0, 'LC'))

    @rule(R.CA_FX_04)
    def test_canada_gains_by_year(self):
        from taxjson.lib.futures import settle_futures
        rows, _ = settle_futures(self._book(), 'average')
        for year, want in ((2025, 0.0), (2026, 60.0)):
            with self.subTest(year=year):
                from taxjson.lib.pipeline import GainsRequest, run_gains
                with contextlib.redirect_stderr(io.StringIO()):
                    r = run_gains(list(rows), [], [], GainsRequest(
                        country='canada', taxable=True, year=year,
                        phantom_hint=False))
                self.assertAlmostEqual(r['summary']['total_gain'], want,
                                       places=4)

    @rule(R.CA_FX_04)
    def test_canada_longs_of_two_broker_accounts_share_one_average(self):
        """s.47: longs at 100 and 140 in the two broker accounts of one
        folder are one average (120); a sale of one at 150 gains 30."""
        from taxjson.lib.futures import pool_across_accounts, settle_futures
        book = [_fut('l1', '2025-01-06', 1, 100, 'aaaaaaaaaa'),
                _fut('l2', '2025-01-07', 1, 140, 'bbbbbbbbbb'),
                _fut('x', '2025-03-03', -1, 150, 'aaaaaaaaaa')]
        rows, _ = settle_futures(book, 'average')
        pooled, _notes = pool_across_accounts(rows)
        close = [r for r in pooled if r.id == 'x']
        self.assertEqual(len(close), 1)
        self.assertAlmostEqual(close[0].net_amount, 30.0, places=6)


if __name__ == '__main__':
    unittest.main()

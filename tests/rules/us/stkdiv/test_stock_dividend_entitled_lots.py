"""The 2026-10 filing-amount audit: five defects that changed exported
filing figures (F1-F5), each with the neighbouring control that passed.

All data is synthetic (made-up tickers, whole-dollar amounts).

Formerly: test_fix_filing_audit_2026_10
"""
import contextlib
import io
import unittest

from taxjson.lib.tax_forms import build_8949
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def compute(book, country, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = run_gains(book, req=GainsRequest(country=country,
                                                  taxable=True, **kw))
    return result, err.getvalue()


def stock_dividend_book(**dates):
    """10 shares bought in 2023 for 100; a 10% stock dividend with
    record / ex date 2025-01-10, paid 2025-01-20; 10 more bought for 200
    on 2025-01-15, after the entitlement date; 11 sold on 2025-03-03."""
    return [tx('BUYSELL', '2023-01-02', 'QZSTK.US', 10, 100, id='old'),
            tx('BUYSELL', '2025-01-15', 'QZSTK.US', 10, 200, id='new'),
            tx('BUYSELL', '2025-01-20', 'QZSTK.US', 1, 0,
               type='stock_dividend', id='dividend', **dates),
            tx('BUYSELL', '2025-03-03', 'QZSTK.US', -11, 165, id='sale')]


def _inventory(res, symbol):
    return [r for r in res['inventory'] if r['symbol'] == symbol]


class TestF3USStockDividendEntitledLots(unittest.TestCase):
    """F3: the §307 basis spread reached every lot held on the pay
    date, a lot bought after the record date included: the old lot
    became 10.5 shares, the sale of 11 took half a share of the new
    lot, and Form 8949 said 57.50 long-term and -2.02 short-term for
    65 long-term.

    Formerly: test_fix_filing_audit_2026_10.TestF3USStockDividendEntitledLots"""

    def _check(self, book):
        r, err = compute(book, 'usa', year=2025)
        f = build_8949(r['transactions'], year=2025)
        self.assertEqual(f['part_II_totals']['gain'], 65.0)
        self.assertEqual(f['part_I_totals']['gain'], 0.0)
        inv = _inventory(r, 'QZSTK.US')
        self.assertAlmostEqual(sum(i['qty'] for i in inv), 10.0, places=6)
        self.assertAlmostEqual(sum(i['total_cost'] for i in inv), 200.0,
                               places=2)
        return err

    @rule(R.US_STKDIV_01)
    def test_record_and_ex_date(self):
        err = self._check(stock_dividend_book(record_date='2025-01-10',
                                              ex_date='2025-01-10'))
        self.assertIn('10 share(s) bought before 2025-01-10', err)
        self.assertIn('10 bought on or after 2025-01-10 keep their own '
                      'basis', err)
        self.assertNotIn('warning:', err)

    @rule(R.US_STKDIV_01)
    def test_record_date_alone(self):
        self._check(stock_dividend_book(record_date='2025-01-10'))

    @rule(R.US_STKDIV_01)
    def test_ex_date_alone(self):
        self._check(stock_dividend_book(ex_date='2025-01-10'))

    @rule(R.US_STKDIV_04)
    def test_no_date_spreads_over_all_lots_and_warns(self):
        # The broker's row (no record / ex date): every lot held on the
        # pay date shares the basis, as before, and the lot bought 5
        # days before the pay date is named as the open question.
        r, err = compute(stock_dividend_book(), 'usa', year=2025)
        f = build_8949(r['transactions'], year=2025)
        self.assertEqual(f['part_II_totals']['gain'], 57.5)
        # ATTENTION: `tjs run` echoes it on the console.
        self.assertIn('warning: ATTENTION: QZSTK.US: the stock dividend of '
                      '1 share(s) on 2025-01-20 carries no record or '
                      'ex-dividend date', err)
        self.assertIn('10 share(s) bought on 2025-01-15, within 90 days',
                      err)

    @rule(R.US_STKDIV_04)
    def test_no_date_one_old_lot_is_quiet(self):
        book = [t for t in stock_dividend_book() if t.id != 'new']
        r, err = compute(book, 'usa', year=2025)
        self.assertNotIn('warning:', err)
        self.assertEqual(build_8949(r['transactions'], 2025)
                         ['part_II_totals']['gain'], 65.0)

    @rule(R.US_STKDIV_01, R.US_STKDIV_03)
    def test_no_entitled_lot_is_a_zero_purchase(self):
        # Every lot held was bought after the ex date: the dividend
        # belongs to shares the books do not show.
        book = [t for t in stock_dividend_book(ex_date='2025-01-10')
                if t.id != 'old']
        r, err = compute(book, 'usa', year=2025)
        self.assertIn('none of the 10 share(s) held was bought before its '
                      'entitlement date 2025-01-10', err)
        inv = _inventory(r, 'QZSTK.US')
        self.assertAlmostEqual(sum(i['qty'] for i in inv), 0.0, places=6)
        # 11 sold for 165: 10 at 200 and the $0 share.
        f = build_8949(r['transactions'], year=2025)
        self.assertAlmostEqual(f['part_I_totals']['gain'], -35.0, places=2)

    def test_control_purchase_after_the_pay_date(self):
        b = stock_dividend_book()
        b[1].date = b[1].date_settle = '2025-01-21'
        r, _ = compute(b, 'usa', year=2025)
        f = build_8949(r['transactions'], 2025)
        self.assertEqual(f['part_II_totals']['gain'], 65)
        self.assertEqual(f['part_I_totals']['gain'], 0)


if __name__ == '__main__':
    unittest.main()

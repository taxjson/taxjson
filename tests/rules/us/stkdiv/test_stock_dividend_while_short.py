"""US: a stock dividend posted while the position is short raises the
short (the shares owed grow, the proceeds stay), never a $0 cover
(backlog TJ-5006: the row covered ten shares for nothing, a gain of 50,
and the 110-share purchase left 20 shares held that were never bought).

Synthetic rows only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from _support.engine_rules import assert_rule
from _support.us_corp_engine import _engine


def _t(i, date, qty, net, account='m', **kw):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          symbol='SYQ', quantity=qty, currency='USD',
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, account=account, id=i, **kw)


def _div(**kw):
    return _t('d', '2025-04-01', 10, 0, type='stock_dividend', **kw)


def _figures(r):
    return [(e['id'], e['qty'], round(e['gain'], 2),
             round(e.get('disallowed_amount') or 0.0, 2))
            for e in r['transactions']]


def _short(r):
    """The lots at the end of 2025: (units, proceeds, date), a short's
    units and proceeds negative."""
    return [(lot['qty'], lot['basis'], lot['date']) for lot in r['lots_at']]


def _rows(r):
    return [(e['id'], e['qty'], round(e['gain'], 2)) for e in r['transactions']]


class TestStockDividendWhileShort(unittest.TestCase):
    @rule(R.US_STKDIV_06)
    def test_the_short_grows_and_the_cover_closes_it(self):
        r = _engine([_t('s', '2025-03-03', -100, 500),
                     _t('d', '2025-04-01', 10, 0, type='stock_dividend'),
                     _t('c', '2025-05-05', 110, -440)])
        # 500 received, 440 paid for the 110 owed: 60, and nothing held.
        self.assertEqual(_rows(r), [('c', 110, 60.0)])
        self.assertEqual(r['inventory'], [])
        assert_rule(self, r, 'd', R.US_STKDIV_06, record='d')
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-04-01 while short 100: booked as the short's to make "
                      "good. The short owes the lender the new shares, so it "
                      "grows to 110 share(s) with its proceeds unchanged; "
                      "nothing is received, nothing is covered", r['_stderr'])
        self.assertNotIn('conservation', r['_stderr'])
        self.assertNotIn('no shares held', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_a_part_cover_takes_its_share_of_the_proceeds(self):
        r = _engine([_t('s', '2025-03-03', -100, 500),
                     _t('d', '2025-04-01', 10, 0, type='stock_dividend'),
                     _t('c', '2025-05-05', 55, -220)])
        self.assertEqual(_rows(r), [('c', 55, 30.0)])
        self.assertEqual(_short(r), [(-55.0, -250.0, '2025-03-03')])

    @rule(R.US_STKDIV_06)
    def test_two_short_lots_grow_pro_rata(self):
        r = _engine([_t('s1', '2025-03-03', -60, 300),
                     _t('s2', '2025-03-10', -40, 240),
                     _t('d', '2025-04-01', 10, 0, type='stock_dividend')])
        self.assertEqual(_short(r), [(-66.0, -300.0, '2025-03-03'),
                                     (-44.0, -240.0, '2025-03-10')])

    @rule(R.US_STKDIV_06)
    def test_only_the_shorts_open_at_the_ex_date_owe_it(self):
        r = _engine([_t('s1', '2025-03-03', -60, 300),
                     _t('s2', '2025-03-28', -40, 240),
                     _t('d', '2025-04-01', 6, 0, type='stock_dividend',
                        ex_date='2025-03-20')])
        self.assertEqual(_short(r), [(-66.0, -300.0, '2025-03-03'),
                                     (-40.0, -240.0, '2025-03-28')])
        self.assertNotIn('no short open that day', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_no_short_before_the_ex_date_is_said_and_never_covered(self):
        r = _engine([_t('s', '2025-03-28', -100, 500),
                     _t('d', '2025-04-01', 10, 0, type='stock_dividend',
                        ex_date='2025-03-20')])
        self.assertEqual(r['transactions'], [])
        self.assertEqual(_short(r), [(-110.0, -500.0, '2025-03-28')])
        self.assertIn('warning: ATTENTION: SYQ: the stock dividend of 10 '
                      'share(s) on 2025-04-01: no short open that day was '
                      'sold before its entitlement date 2025-03-20',
                      r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_the_cover_at_a_loss_is_the_shorts_own_loss(self):
        r = _engine([_t('s', '2025-03-03', -100, 500), _div(),
                     _t('c', '2025-05-05', 110, -600)])
        self.assertEqual(_figures(r), [('c', 110, -100.0, 0.0)])
        self.assertEqual(r['inventory'], [])

    @rule(R.US_STKDIV_06)
    def test_another_accounts_loss_after_the_cover_is_not_washed(self):
        # The wash-sale pre-pass reads the dividend as the walk does: the
        # cover of 110 buys nothing past the short, so no purchase stands
        # in the window of L's loss five days later. Read as shares
        # received, 20 of the cover were a purchase and denied 100 of it.
        def book(div):
            return ([_t('bl', '2025-01-06', 100, -1000, 'L'),
                     _t('s', '2025-03-03', -100, 500)] + ([_div()] if div else [])
                    + [_t('c', '2025-05-05', 110 if div else 100, -440),
                       _t('sl', '2025-05-10', -100, 500, 'L')])
        with_div, without = _engine(book(True)), _engine(book(False))
        self.assertEqual(_figures(with_div), [('c', 110, 60.0, 0.0),
                                              ('sl', 100, -500.0, 0.0)])
        self.assertEqual(_figures(without), [('c', 100, 60.0, 0.0),
                                             ('sl', 100, -500.0, 0.0)])

    @rule(R.US_STKDIV_06)
    def test_a_short_sold_again_after_a_losing_cover_as_without_the_dividend(self):
        # A cover's loss and a new short within 30 days (§1091(e)): the
        # whole 110 covered are loss shares, the 110 sold short again
        # replace them, as in the book whose short was 110 from the start.
        def book(div):
            return ([_t('s', '2025-03-03', -100 if div else -110, 500)]
                    + ([_div()] if div else [])
                    + [_t('c', '2025-05-05', 110, -600),
                       _t('s2', '2025-05-12', -110, 550),
                       _t('c2', '2025-08-04', 110, -500)])
        want = [('c', 110, 0.0, 100.0), ('c2', 110, -50.0, 0.0)]
        self.assertEqual(_figures(_engine(book(True))), want)
        self.assertEqual(_figures(_engine(book(False))), want)

    @rule(R.US_STKDIV_06)
    def test_a_value_on_the_row_is_no_proceeds(self):
        r = _engine([_t('s', '2025-03-03', -100, 500),
                     _t('d', '2025-04-01', 10, -30, type='stock_dividend'),
                     _t('c', '2025-05-05', 110, -440)])
        self.assertEqual(_figures(r), [('c', 110, 60.0, 0.0)])

    @rule(R.US_STKDIV_01)
    def test_control_a_long_holding_as_before(self):
        r = _engine([_t('b', '2025-03-03', 100, -500),
                     _t('d', '2025-04-01', 10, 0, type='stock_dividend'),
                     _t('x', '2025-05-05', -110, 440)])
        self.assertEqual(_rows(r), [('x', 110.00000000000001, -60.0)])
        self.assertEqual(r.get('rules', {}).get('d'), None)


if __name__ == "__main__":
    unittest.main()

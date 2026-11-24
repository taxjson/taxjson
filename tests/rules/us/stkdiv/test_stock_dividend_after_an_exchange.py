"""US: a stock dividend whose ex-date falls before a §356 exchange of part
of the lot: the exchanged shares kept their right to it, so they count
among the entitled shares and the new shares paid on them take their part
of the basis the exchange carried; where the engine cannot place that part
it books them at $0 and says how much to move (backlog TJ-4987, TJ-2706:
the shares still held took the whole dividend, silently).

Synthetic rows only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from _support.us_corp_engine import _boot_rows, _engine


def _t(i, date, sym, qty, net, **kw):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          symbol=sym, quantity=qty, currency='USD',
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, account='b', id=i, **kw)


def _dividend(**kw):
    """10 QZRX paid 2025-05-15 on 100 held on the ex-date, 2025-05-01."""
    return _t('div', '2025-05-15', 'QZRX', 10, 0, type='stock_dividend',
              **dict(dict(ex_date='2025-05-01'), **kw))


def _book(boot, *more, dividend=None):
    """100 QZRX bought for 400; on 2025-05-05 50 of them go in a §356
    exchange for 25 QZNW (worth 260) plus `boot` cash; then the dividend."""
    return ([_t('buy', '2024-01-02', 'QZRX', 100, -400)]
            + _boot_rows(50, 25, boot, 10.4, date='2025-05-05')
            + list(more) + [dividend or _dividend()])


def _held(r):
    return {i['symbol']: (round(i['qty'], 6), round(i['total_cost'], 4))
            for i in r['inventory']}


def _lots(r, sym):
    return sorted((round(lot['qty'], 6), round(lot['basis'], 4), lot['date'])
                  for lot in r['lots_at'] if lot['symbol'] == sym)


class TestStockDividendAfterAnExchange(unittest.TestCase):
    @rule(R.US_STKDIV_05)
    def test_the_exchanged_shares_keep_their_part(self):
        r = _engine(_book(30.0))
        # 100 entitled, 10 new: 5 on the 50 held (basis 200 over 55), 5
        # on the 50 exchanged, with 1/11 of the 200 that block carried.
        self.assertEqual(_held(r), {'QZRX': (60.0, 218.1818),
                                    'QZNW': (25.0, 181.8182)})
        self.assertEqual(_lots(r, 'QZRX'),
                         [(5.0, 18.1818, '2024-01-02'),
                          (55.0, 200.0, '2024-01-02')])
        # The boot's gain, recognized on the exchange, stays.
        self.assertEqual([(e['symbol'], e['gain']) for e in r['transactions']],
                         [('QZRX', 30.0)])
        self.assertIn('the basis of the 100 share(s) bought before '
                      '2025-05-01', r['_stderr'])
        self.assertIn('50 of them left in a §356 exchange on or after '
                      '2025-05-01, before the pay date (2025-05-05): the '
                      'shares received for them give up 18.18 of the basis '
                      'they carried, which the 5 new share(s) paid on them '
                      'carry', r['_stderr'])
        self.assertNotIn('ATTENTION', r['_stderr'])

    @rule(R.US_STKDIV_05)
    def test_an_exchange_with_no_boot_the_same(self):
        r = _engine(_book(0.0))
        self.assertEqual(_held(r), {'QZRX': (60.0, 218.1818),
                                    'QZNW': (25.0, 181.8182)})
        self.assertEqual(r['transactions'], [])

    @rule(R.US_STKDIV_05)
    def test_new_shares_sold_since_are_named_for_a_manual_move(self):
        # 10 of the 25 QZNW sold before the pay date: the lot the
        # exchange booked is no longer whole, so nothing is moved.
        r = _engine(_book(30.0, _t('s', '2025-05-08', 'QZNW', -10, 120)))
        self.assertEqual(_held(r), {'QZRX': (60.0, 200.0),
                                    'QZNW': (15.0, 120.0)})
        self.assertEqual(_lots(r, 'QZRX'), [(5.0, 0.0, '2024-01-02'),
                                            (55.0, 200.0, '2024-01-02')])
        self.assertIn('warning: ATTENTION: QZRX: the stock dividend on '
                      '2025-05-15: 50 of the shares entitled to it left in '
                      'the §356 exchange on 2025-05-05 (b) for QZNW',
                      r['_stderr'])
        self.assertIn('so their 5 new share(s) are booked at $0: move 18.18 '
                      'of the basis that exchange carried (10.91 off the 15 '
                      'QZNW share(s) still held, 7.27 off the cost of the 10 '
                      'sold since) to them by hand', r['_stderr'])
        # The note beside it says the same: nothing was moved.
        self.assertIn('(2025-05-05): 5 new share(s) paid on them are booked '
                      'at $0, their basis to move by hand (the ATTENTION '
                      'line)', r['_stderr'])
        self.assertNotIn('give up', r['_stderr'])

    @rule(R.US_STKDIV_05)
    def test_new_shares_all_sold_since_name_the_sales_cost(self):
        r = _engine(_book(0.0, _t('s', '2025-05-10', 'QZNW', -25, 300)))
        self.assertEqual(_held(r), {'QZRX': (60.0, 200.0)})
        self.assertIn('move 18.18 of the basis that exchange carried (off the '
                      'cost of the 25 QZNW share(s) sold since) to them by '
                      'hand', r['_stderr'])

    @rule(R.US_STKDIV_05)
    def test_control_an_exchange_before_the_ex_date_is_not_entitled(self):
        r = _engine(_book(30.0, dividend=_dividend(ex_date='2025-05-10')))
        # Only the 50 held on the ex-date: all 10 new shares are theirs.
        self.assertEqual(_held(r), {'QZRX': (60.0, 200.0),
                                    'QZNW': (25.0, 200.0)})
        self.assertNotIn('§356', r['_stderr'])

    @rule(R.US_STKDIV_05)
    def test_control_no_exchange_as_before(self):
        r = _engine([_t('buy', '2024-01-02', 'QZRX', 100, -400), _dividend()])
        self.assertEqual(_held(r), {'QZRX': (110.0, 400.0)})
        self.assertNotIn('§356', r['_stderr'])


if __name__ == "__main__":
    unittest.main()

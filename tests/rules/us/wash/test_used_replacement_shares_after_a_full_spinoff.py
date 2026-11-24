"""US: shares that already replaced a loss never replace a second one,
whatever a §355 spin-off later does to their basis (backlog TJ-4986,
TJ-2704: a spin-off that moved the lot's whole basis, the earlier loss's
add in it, left the lot looking never matched, and another account's later
loss was disallowed against the shares once they were sold).

Synthetic rows only.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.corp_actions import CorporateAction, resolve_event
from taxjson.model.transaction import TaxTransaction
from _support.us_corp_engine import _engine


def _t(i, acct, date, qty, net):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          symbol='QZRX', quantity=qty, currency='USD',
                          price=abs(net / qty), net_amount=net, account=acct,
                          id=i)


def _spin(alloc):
    ev = CorporateAction(
        date='2025-02-14', time='09:30:00', action_type='spinoff',
        source_symbol='QZRX', source_isin='', target_symbol='QZSP',
        target_isin='', ratio_new=1, ratio_old=2, qty_disposed=0.0,
        qty_received=5, fmv=0.0, currency='USD', target_currency='USD',
        account='b', event_id='ev-qzsp')
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': alloc})
    return [TaxTransaction(**r) for r in rows]


def _rows(alloc):
    """c holds 10 bought for 1000. b buys 10 for 500, sells them for 400
    (a loss of 100) and buys 10 back for 400: the replacement lot's basis
    is 500, the 100 in it. The spin-off moves `alloc` of that basis. b
    sells the 10 for 10; two days later c sells its own 10 at a loss of
    600."""
    book = ([_t('c1', 'c', '2024-12-01', 10, -1000),
             _t('b1', 'b', '2025-01-02', 10, -500),
             _t('b2', 'b', '2025-02-03', -10, 400),
             _t('b3', 'b', '2025-02-10', 10, -400)]
            + _spin(alloc)
            + [_t('b4', 'b', '2025-02-18', -10, 10),
               _t('c2', 'c', '2025-02-20', -10, 400)])
    r = _engine(book)
    return {e['id']: (round(e['gain'], 2),
                      round(e.get('disallowed_amount') or 0.0, 2))
            for e in r['transactions']}, r


class TestUsedReplacementSharesAfterAFullSpinOff(unittest.TestCase):
    @rule(R.US_WASH_22)
    def test_the_whole_basis_moved_leaves_the_shares_used(self):
        got, r = _rows(500)
        # b's first loss is disallowed and sits in the replacement lot.
        self.assertEqual(got['b2'], (0.0, 100.0))
        # The replacement shares, sold for 10 with no basis left: +10.
        self.assertEqual(got['b4'], (10.0, 0.0))
        # c's loss is its own: the shares b sold replaced b's loss.
        self.assertEqual(got['c2'], (-600.0, 0.0))
        self.assertEqual([(i['symbol'], i['qty'], i['total_cost'])
                          for i in r['inventory']], [('QZSP', 5.0, 500.0)])

    @rule(R.US_WASH_22)
    def test_control_a_cent_left_on_the_parent_as_before(self):
        got, _r = _rows(499.99)
        self.assertEqual(got, {'b2': (0.0, 100.0), 'b4': (9.99, 0.0),
                               'c2': (-600.0, 0.0)})

    @rule(R.US_WASH_22)
    def test_control_never_matched_shares_sold_still_replace(self):
        # No loss of b's own: its 10 shares, bought in c's window and
        # sold before c's loss, replace it (US-WASH-22), spin-off or not.
        book = ([_t('c1', 'c', '2024-12-01', 10, -1000),
                 _t('b3', 'b', '2025-02-10', 10, -400)]
                + _spin(400)
                + [_t('b4', 'b', '2025-02-18', -10, 10),
                   _t('c2', 'c', '2025-02-20', -10, 400)])
        r = _engine(book)
        got = {e['id']: (round(e['gain'], 2),
                         round(e.get('disallowed_amount') or 0.0, 2))
               for e in r['transactions']}
        self.assertEqual(got['c2'], (0.0, 600.0))
        self.assertEqual(got['b4'], (-590.0, 0.0))


if __name__ == "__main__":
    unittest.main()

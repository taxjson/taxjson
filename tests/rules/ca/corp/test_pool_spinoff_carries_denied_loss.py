"""A spin-off booked as a reallocation of cost (CA-CORP-06: the parent's
ACB reduced, the new shares bought for the amount, one corporate event)
when the parent's ACB holds a denied superficial loss: the new shares
take the share of it that the ACB given up is of the parent's ACB, and
the same share of what a return of capital had taken of it, so the views
that count each trade's P/L before any denial (CA-RPT-16) split the
recovered loss as the ACB is split. The filed figures do not read it.

PLAN step 13, commits 8 and 9: pins for the ADJUST row's and the opening
purchase's parts of it, which no test held (the survey before the moves).
Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.model.transaction import TaxTransaction
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    denied_loss, return_of_capital, run_canada, sale, trade)

PARENT, CHILD = 'ZZP.TO', 'ZZC.TO'


def _spinoff(amount, child_first=False):
    """The event's two rows on 2025-03-03: `amount` of the parent's ACB
    given up, 10 new shares bought for it; the new shares' row first when
    `child_first` (brokers list them either way)."""
    def row(action, sym, qty, net, time):
        return TaxTransaction(
            action=action, date='2025-03-03', date_settle='2025-03-03', time=time,
            symbol=sym, quantity=float(qty), price=abs(net / qty) if qty else 0.0,
            net_amount=float(net), currency='CAD', account='b', corp_event_id='E1')
    first, second = ('10:00:01', '10:00:00') if child_first else ('10:00:00', '10:00:01')
    rows = [row('ADJUST', PARENT, 0, -amount, first), row('BUYSELL', CHILD, 10, amount, second)]
    return rows[::-1] if child_first else rows


def _reported(rec):
    return tuple(round(rec.get(k) or 0.0, 2) for k in (
        'cost', 'deferred_loss_recovered', 'deferred_loss_cut'))


class TestTheNewSharesTakeTheirShareOfTheDeniedLoss(unittest.TestCase):
    """The parent: 100 at 1000, 500 of it a denied loss."""

    def _sales(self, before, amount, child_first):
        res, _ = run_canada(denied_loss(PARENT) + before + _spinoff(amount, child_first)
                            + [trade('2025-06-02', -100, 1200, sym=PARENT),
                               trade('2025-06-03', -10, 300, sym=CHILD)])
        return (_reported(sale(res, '2025-06-02', PARENT)),
                _reported(sale(res, '2025-06-03', CHILD)))

    @rule(R.CA_CORP_06, R.CA_RPT_16)
    def test_a_fifth_of_the_acb_carries_a_fifth_of_it(self):
        for child_first in (False, True):
            with self.subTest(child_first=child_first):
                # 200 of the 1000: 100 of the 500 goes to the new shares.
                self.assertEqual(self._sales([], 200, child_first),
                                 ((800.0, 400.0, 0.0), (200.0, 100.0, 0.0)))

    @rule(R.CA_CORP_06, R.CA_RPT_16, R.CA_ACB_06)
    def test_and_the_same_share_of_what_a_return_of_capital_took(self):
        # A return of capital of 800 first: ACB 200, all of it denied
        # loss, 300 taken. 50 of the 200 given up: a quarter of each.
        before = [return_of_capital('2025-02-20', 800, sym=PARENT)]
        for child_first in (False, True):
            with self.subTest(child_first=child_first):
                self.assertEqual(self._sales(before, 50, child_first),
                                 ((150.0, 150.0, 225.0), (50.0, 50.0, 75.0)))


if __name__ == '__main__':
    unittest.main()

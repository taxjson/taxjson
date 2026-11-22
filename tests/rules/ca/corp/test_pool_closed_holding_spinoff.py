"""An s.86.1 spin-off distributed after its parent was sold reduces the
cost of that sale (CA-CORP-14): the shares the distribution was made on
are the last holding's, the ones the pool kept the dates of when it was
closed. A holding bought and sold before that one began is another
position: its sale keeps its cost and its gain.

PLAN step 13: a pin for the closed holding's start, which the pool's
drain records and no test held (the review of commits 1 to 3).
Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.model.transaction import TaxTransaction
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import holding, run_canada, sale, trade

PARENT, CHILD = 'ZZS.TO', 'ZZD.TO'


def _spinoff(date, reduction, parent_qty, child_qty):
    """The two rows of an s.86.1 spin-off as the elections book them: the
    parent's cost reduced by `reduction`, the child bought for it."""
    def row(action, sym, qty, net, time, **kw):
        return TaxTransaction(
            action=action, date=date, date_settle=date, time=time, symbol=sym,
            quantity=float(qty), price=abs(net / qty) if qty else 0.0,
            net_amount=float(net), currency='CAD', account='b',
            corp_event_id='E1', **kw)
    return [row('ADJUST', PARENT, 0, -reduction, '10:00:00',
                corp_election='rollover_s_86_1', corp_parent_qty=parent_qty),
            row('BUYSELL', CHILD, child_qty, reduction, '10:00:01')]


class TestTheReductionGoesToTheLastHoldingsSale(unittest.TestCase):
    """100 bought for 1000 and sold for 1200 in January and February; 100
    bought again for 1000 in March and sold for 1300 in April. The
    spin-off of May was made on the last holding: its 150 comes off those
    shares' cost (850, a gain of 450); the February sale stays 1000 and
    200."""

    def _sales(self, parent_qty):
        res, err = run_canada([
            trade('2024-01-02', 100, 1000, sym=PARENT),
            trade('2024-02-01', -100, 1200, sym=PARENT),
            trade('2024-03-01', 100, 1000, sym=PARENT),
            trade('2024-04-01', -100, 1300, sym=PARENT)]
            + _spinoff('2024-05-01', 150, parent_qty, 30))
        self.assertNotIn('EMPTY pool', err)
        child = holding(res, CHILD)
        self.assertEqual((child['qty'], round(child['total_cost'], 2)), (30.0, 150.0))
        return [(round(r['cost'], 2), round(r['gain'], 2))
                for r in (sale(res, '2024-02-01', PARENT), sale(res, '2024-04-01', PARENT))]

    @rule(R.CA_CORP_14)
    def test_an_earlier_holdings_sale_keeps_its_cost(self):
        self.assertEqual(self._sales(100), [(1000.0, 200.0), (850.0, 450.0)])

    @rule(R.CA_CORP_14)
    def test_also_when_the_event_counts_more_shares_than_the_last_holding_sold(self):
        # The event was paid on 150 shares, 50 of them not among this
        # book's sales of the last holding: the walk back stops where that
        # holding began, never at the February sale of another position.
        self.assertEqual(self._sales(150), [(1000.0, 200.0), (850.0, 450.0)])

if __name__ == '__main__':
    unittest.main()

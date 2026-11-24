"""US: two §355 spin-offs of one parent on one day with the same allocated
amount each cut the parent's basis: no basis is created (backlog TJ-4985,
TJ-1348: the two parent ADJUST rows had one id and the dedup dropped one).

Synthetic rows only.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.bin.taxjson_sort import deduplicate
from taxjson.lib.corp_actions import CorporateAction, resolve_event
from taxjson.model.transaction import TaxTransaction
from _support.us_corp_engine import _engine


def _spin(event_id, qty, alloc):
    ev = CorporateAction(
        date='2025-04-01', time='09:30:00', action_type='spinoff',
        source_symbol='QZRX', source_isin='', target_symbol='QZSP',
        target_isin='', ratio_new=1, ratio_old=10, qty_disposed=0.0,
        qty_received=qty, fmv=0.0, currency='USD', target_currency='USD',
        account='b', event_id=event_id)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': alloc})
    return [TaxTransaction(**r) for r in rows]


def _basis(book):
    r = _engine(book)
    return {i['symbol']: (i['qty'], round(i['total_cost'], 6))
            for i in r['inventory']}


_BUY = dict(action='BUYSELL', date='2025-01-02', date_settle='2025-01-03',
            symbol='QZRX', quantity=150, currency='USD', net_amount=-250,
            account='b')


class TestTwoSpinOffsOfOneParentOnOneDay(unittest.TestCase):
    @rule(R.US_CORP_07)
    def test_the_same_amount_twice_cuts_the_parent_twice(self):
        rows = deduplicate(_spin('ev-a', 10, 40) + _spin('ev-b', 5, 40))
        got = _basis([TaxTransaction(**_BUY)] + rows)
        # 250 in, 250 held: 80 on the new shares, 170 left on the parent.
        self.assertEqual(got, {'QZRX': (150, 170.0), 'QZSP': (15.0, 80.0)})

    @rule(R.US_CORP_07)
    def test_control_two_different_amounts_as_before(self):
        rows = deduplicate(_spin('ev-a', 10, 40) + _spin('ev-b', 5, 20))
        got = _basis([TaxTransaction(**_BUY)] + rows)
        self.assertEqual(got, {'QZRX': (150, 190.0), 'QZSP': (15.0, 60.0)})

    @rule(R.US_CORP_07)
    def test_control_one_event_read_twice_cuts_once(self):
        rows = deduplicate(_spin('ev-a', 10, 40) + _spin('ev-a', 10, 40))
        got = _basis([TaxTransaction(**_BUY)] + rows)
        self.assertEqual(got, {'QZRX': (150, 210.0), 'QZSP': (10.0, 40.0)})


if __name__ == "__main__":
    unittest.main()

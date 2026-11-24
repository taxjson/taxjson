"""Canada: two s.86.1 spin-offs of one parent on one day with the same
allocated amount each cut the parent's ACB: no cost is created (backlog
TJ-4985, TJ-1348: the two parent ADJUST rows had one id and the dedup
dropped one).

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
from _support.ca_engine_runs import _run


def _spin(event_id, qty, alloc):
    ev = CorporateAction(
        date='2025-04-01', time='09:30:00', action_type='spinoff',
        source_symbol='QZRX', source_isin='', target_symbol='QZSP',
        target_isin='', ratio_new=1, ratio_old=10, qty_disposed=0.0,
        qty_received=qty, fmv=0.0, currency='CAD', target_currency='CAD',
        account='b', event_id=event_id)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'rollover_s_86_1', country='canada',
                             hints={'allocated_acb_cad': alloc})
    return [TaxTransaction(**r) for r in rows]


def _acb(rows):
    buy = TaxTransaction(action='BUYSELL', date='2025-01-02',
                         date_settle='2025-01-03', symbol='QZRX', quantity=150,
                         currency='CAD', net_amount=-250, account='b')
    res, _err = _run([buy] + rows)
    return {i['symbol']: (i['qty'], round(i['total_cost'], 6))
            for i in res['inventory']}


class TestTwoSpinOffsOfOneParentOnOneDay(unittest.TestCase):
    @rule(R.CA_CORP_06)
    def test_the_same_amount_twice_cuts_the_parent_twice(self):
        got = _acb(deduplicate(_spin('ev-a', 10, 20) + _spin('ev-b', 5, 20)))
        # 250 in, 250 held: 40 on the new shares, 210 left on the parent.
        self.assertEqual(got, {'QZRX': (150.0, 210.0), 'QZSP': (15.0, 40.0)})

    @rule(R.CA_CORP_06)
    def test_control_two_different_amounts_as_before(self):
        got = _acb(deduplicate(_spin('ev-a', 10, 20) + _spin('ev-b', 5, 10)))
        self.assertEqual(got, {'QZRX': (150.0, 220.0), 'QZSP': (15.0, 30.0)})


if __name__ == "__main__":
    unittest.main()

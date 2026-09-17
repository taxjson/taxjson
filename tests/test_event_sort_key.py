"""event_sort_key — the single event-ordering definition (profiles pin the
deliberate per-engine divergences; drift between hand-maintained sort keys
produced two historical bug classes)."""

import unittest

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import (
    CaPriority,
    Phase,
    UsPriority,
    event_sort_key,
)


def tx(action='BUYSELL', date='2025-06-10', time='09:30:00', qty=100,
       date_settle='', symbol='AAPL.US', **kw):
    return TaxTransaction(action=action, date=date, time=time,
                          quantity=qty, date_settle=date_settle,
                          symbol=symbol, currency='CAD', **kw)


def order(txs, profile, date_of=None):
    return [t.id for t in sorted(
        txs, key=lambda t: event_sort_key(t, profile=profile,
                                          date_of=date_of))]


class TestLadders(unittest.TestCase):
    def test_ca_priority_ladder_values(self):
        # Pinned: renumbering silently reorders same-timestamp events.
        self.assertEqual(CaPriority.OPENING_BALANCE, -1)
        self.assertEqual(CaPriority.DISALLOW, 0)
        self.assertEqual(CaPriority.ASSIGN_OPTION, 1)
        self.assertEqual(CaPriority.ASSIGN_STOCK_OR_SPLIT, 2)
        self.assertEqual(CaPriority.BUY, 3)
        self.assertEqual(CaPriority.SELL, 4)
        self.assertEqual(CaPriority.ADJUST, 5)
        self.assertEqual(CaPriority.OTHER, 6)

    def test_us_priority_ladder_values(self):
        self.assertEqual(UsPriority.OPENING_BALANCE, -1)
        self.assertEqual(UsPriority.ASSIGN_OPTION, 0)
        self.assertEqual(UsPriority.ASSIGN_STOCK, 1)
        self.assertEqual(UsPriority.SPLIT, 2)
        self.assertEqual(UsPriority.OTHER, 3)
        self.assertEqual(UsPriority.ADJUST, 4)

    def test_phase_ladder_values(self):
        self.assertEqual(Phase.PRE_EXISTING, 0)
        self.assertEqual(Phase.SPLIT, 1)
        self.assertEqual(Phase.EXECUTED_TODAY, 2)


class TestProfiles(unittest.TestCase):
    def test_ca_split_precedes_same_day_execution_regardless_of_clock(self):
        buy = tx(time='09:30:00', qty=100)
        split = tx(action='SPLIT', time='12:00:00', qty=2.0)
        self.assertEqual(order([buy, split], 'ca_main'),
                         [split.id, buy.id])

    def test_us_noon_split_follows_0930_buy(self):
        # The documented CA/US divergence: US sorts time before priority.
        buy = tx(time='09:30:00', qty=100)
        split = tx(action='SPLIT', time='12:00:00', qty=2.0)
        self.assertEqual(order([buy, split], 'us_main',
                               date_of=lambda t: t.date),
                         [buy.id, split.id])

    def test_ca_settle_lagged_row_sorts_before_split(self):
        # Executed the day before, settling on split day: its shares
        # pre-exist the split and must be scaled by it.
        lagged = tx(date='2025-06-09', date_settle='2025-06-10',
                    time='15:59:00', qty=100)
        split = tx(action='SPLIT', date='2025-06-10', time='00:00:00',
                   qty=2.0)
        self.assertEqual(order([lagged, split], 'ca_main'),
                         [lagged.id, split.id])

    def test_opening_balance_beats_same_timestamp_split_everywhere(self):
        ob = tx(action='OPENING_BALANCE', time='00:00:00', qty=50)
        split = tx(action='SPLIT', time='00:00:00', qty=2.0)
        for profile, date_of in (('ca_main', None), ('ca_balance', None),
                                 ('us_main', lambda t: t.date)):
            self.assertEqual(order([split, ob], profile, date_of=date_of),
                             [ob.id, split.id], profile)

    def test_us_assign_precedes_same_timestamp_buysell(self):
        assign = tx(action='ASSIGN', symbol='AAPL250117C00160000.US',
                    time='16:00:00', qty=1)
        stock = tx(time='16:00:00', qty=-100)
        self.assertEqual(order([stock, assign], 'us_main',
                               date_of=lambda t: t.date),
                         [assign.id, stock.id])

    def test_ca_balance_profile_has_no_priority_rung(self):
        # Two same-phase, same-time rows: key equality means input order
        # (stable sort) decides — pinning that the balance walk gained no
        # tie-break it never had.
        buy = tx(qty=100)
        sell = tx(qty=-100)
        self.assertEqual(event_sort_key(buy, profile='ca_balance'),
                         event_sort_key(sell, profile='ca_balance'))

    def test_plain_walk_is_date_time_only(self):
        split = tx(action='SPLIT', time='00:00:00', qty=2.0)
        self.assertEqual(event_sort_key(split, profile='plain_walk'),
                         ('2025-06-10', '00:00:00'))

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            event_sort_key(tx(), profile='nope')


if __name__ == '__main__':
    unittest.main()

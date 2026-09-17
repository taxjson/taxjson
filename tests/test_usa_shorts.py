"""Tests for USATaxRules short-position handling.

Covers the comprehensive short-side fixes:
  - Sell-to-open / buy-to-close FIFO matching
  - Gain on close = opening_proceeds - closing_cost
  - SHORT_TERM holding period (§1222) for stand-alone shorts
  - §1091 wash sale on short losses (Reg §1.1091-1): replacement short's
    effective opening proceeds reduced by disallowed amount
  - Position flips: SELL that exceeds long inventory, BUY that exceeds short
    inventory
  - Multiple short lots: FIFO close
  - Partial close of a short lot
"""

import unittest

from taxjson.lib.core import TaxTransaction, USATaxRules


class TestShortBasic(unittest.TestCase):
    """Bare sell-to-open / buy-to-close cycles."""

    def test_short_profit(self):
        """Sell-to-open at $100, buy-to-close at $80 → gain = $20."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-1.0, price=100.0, net_amount=100.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='AAPL',
                           quantity=1.0, price=80.0, net_amount=80.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        g = gains[0]
        self.assertEqual(g['direction'], 'SHORT')
        self.assertAlmostEqual(g['raw_gain'], 20.00, places=4)
        self.assertAlmostEqual(g['gain'], 20.00, places=4)
        # Stand-alone shorts are always short-term per §1222.
        self.assertEqual(g['term'], 'SHORT_TERM')
        # Signed cash flow: cost and proceeds negative so consumers can
        # compute proceeds-cost = signed gain regardless of direction.
        self.assertLess(g['cost'], 0)
        self.assertLess(g['proceeds'], 0)

    def test_short_loss(self):
        """Sell-to-open at $100, buy-to-close at $120 → loss = -$20."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-1.0, price=100.0, net_amount=100.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='AAPL',
                           quantity=1.0, price=120.0, net_amount=120.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        g = gains[0]
        self.assertAlmostEqual(g['raw_gain'], -20.00, places=4)
        # No wash sale (no replacement short in window).
        self.assertAlmostEqual(g['disallowed_amount'], 0.0)
        self.assertAlmostEqual(g['gain'], -20.00)


class TestShortWashSale(unittest.TestCase):
    """§1091 extended to shorts per Reg §1.1091-1."""

    def test_wash_sale_basic(self):
        """Short loss followed by another short-open within 30 days disallows
        the loss; the replacement short's effective opening proceeds are
        reduced by the disallowed amount."""
        rules = USATaxRules()
        txs = [
            # Short open 1: receive $100
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-1.0, price=100.0, net_amount=100.00,
                           currency='USD', account='M'),
            # Close at loss: pay $120 (loss = -$20)
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='AAPL',
                           quantity=1.0, price=120.0, net_amount=120.00,
                           currency='USD', account='M'),
            # Replacement short within 30 days: receive $110
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='AAPL',
                           quantity=-1.0, price=110.0, net_amount=110.00,
                           currency='USD', account='M'),
            # Final close: pay $90 (without wash: gain = $110 - $90 = $20)
            TaxTransaction(action='BUYSELL', date='2025-04-10', symbol='AAPL',
                           quantity=1.0, price=90.0, net_amount=90.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 2)

        # First close (Feb 15) — wash sale: $20 loss disallowed.
        first = gains[0]
        self.assertAlmostEqual(first['raw_gain'], -20.00, places=4)
        self.assertAlmostEqual(first['disallowed_amount'], 20.00, places=4)
        self.assertAlmostEqual(first['gain'], 0.00, places=4)
        self.assertTrue(first['is_wash_sale'])
        self.assertEqual(len(first.get('wash_replacements', [])), 1)
        self.assertAlmostEqual(
            first['wash_replacements'][0]['proceeds_reduction'], 20.00, places=4
        )

        # Second close (Apr 10) — replacement's proceeds were reduced from
        # $110 to $90, so the realized gain is $90 - $90 = $0 (not $20).
        # The $20 disallowed loss has been deferred into this gain.
        second = gains[1]
        self.assertAlmostEqual(second['raw_gain'], 0.00, places=4)

        # Aggregate invariant: total realized must equal what you would have
        # had without the wash sale at all.
        total = sum(g['gain'] for g in gains)
        self.assertAlmostEqual(total, 0.00, places=4)  # net zero across the round-trip

        # The wash_sales array should have the disallowance recorded.
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 20.00)
        self.assertEqual(result['wash_sales'][0]['direction'], 'SHORT')

    def test_wash_sale_outside_window(self):
        """Replacement short more than 30 days after the loss → no wash sale."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-1.0, price=100.0, net_amount=100.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='AAPL',
                           quantity=1.0, price=120.0, net_amount=120.00,
                           currency='USD', account='M'),
            # 32 days after the loss — outside ±30 day window
            TaxTransaction(action='BUYSELL', date='2025-03-19', symbol='AAPL',
                           quantity=-1.0, price=110.0, net_amount=110.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        # Only the first close is reported (the late short open is still open).
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['raw_gain'], -20.00, places=4)
        self.assertAlmostEqual(gains[0]['disallowed_amount'], 0.0, places=4)
        self.assertAlmostEqual(gains[0]['gain'], -20.00, places=4)


class TestShortFIFO(unittest.TestCase):
    """Multiple short lots, FIFO matching on close."""

    def test_multiple_shorts_fifo(self):
        """Two short opens at different prices, single buy-to-close matches
        the FIRST short first per FIFO."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-10.0, price=100.0, net_amount=1000.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='AAPL',
                           quantity=-10.0, price=110.0, net_amount=1100.00,
                           currency='USD', account='M'),
            # Close 15 @ $90: 10 FIFO from Jan, 5 from Feb
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='AAPL',
                           quantity=15.0, price=90.0, net_amount=1350.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = sorted(
            [g for g in result['transactions'] if g.get('action') != 'DIVIDEND'],
            key=lambda g: g['qty'],
        )
        # Two gain chunks. By qty: chunk of 5 and chunk of 10.
        self.assertEqual(len(gains), 2)

        chunk_5 = next(g for g in gains if abs(g['qty'] - 5) < 1e-6)
        chunk_10 = next(g for g in gains if abs(g['qty'] - 10) < 1e-6)

        # First chunk: 10 sh from Jan short @ open 1000 vs close 10/15*1350 = 900 → gain 100
        self.assertAlmostEqual(chunk_10['raw_gain'], 100.00, places=2)
        # Second chunk: 5 sh from Feb short @ open 5/10*1100 = 550 vs close 5/15*1350 = 450 → gain 100
        self.assertAlmostEqual(chunk_5['raw_gain'], 100.00, places=2)


class TestShortPartialClose(unittest.TestCase):
    """Closing less than the full short qty leaves the remainder open."""

    def test_partial_close(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-10.0, price=100.0, net_amount=1000.00,
                           currency='USD', account='M'),
            # Close only 4 of 10
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='AAPL',
                           quantity=4.0, price=80.0, net_amount=320.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        # Pro-rata: 4/10 of $1000 = $400 open; close cost $320; gain = $80
        self.assertAlmostEqual(gains[0]['raw_gain'], 80.00, places=2)
        self.assertAlmostEqual(gains[0]['qty'], 4.00, places=2)

        # Inventory should show 6 shares short remaining.
        inv = next(i for i in result['inventory'] if i['symbol'] == 'AAPL')
        self.assertAlmostEqual(inv['qty'], -6.00, places=2)


class TestPositionFlips(unittest.TestCase):
    """A close that crosses zero spawns an open on the other side."""

    def test_sell_long_then_flip_to_short(self):
        """Buy 10, then sell 15 → closes 10 long, opens 5 short."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=10.0, price=100.0, net_amount=1000.00,
                           currency='USD', account='M'),
            # Sell 15 @ $110: 10 closes long (gain 100), 5 opens short @ proc 550/3 = 366.67
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='AAPL',
                           quantity=-15.0, price=110.0, net_amount=1650.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        # One closed LONG gain entry; the SHORT portion is still open.
        self.assertEqual(len(gains), 1)
        long_close = gains[0]
        self.assertEqual(long_close['direction'], 'LONG')
        self.assertAlmostEqual(long_close['qty'], 10.00, places=2)
        # Proceeds: 10/15 * 1650 = 1100; cost 1000; gain 100
        self.assertAlmostEqual(long_close['raw_gain'], 100.00, places=2)

        # Inventory: 5 shares short
        inv = next(i for i in result['inventory'] if i['symbol'] == 'AAPL')
        self.assertAlmostEqual(inv['qty'], -5.00, places=2)

    def test_buy_short_then_flip_to_long(self):
        """Short 10, then buy 15 → closes 10 short, opens 5 long."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-10.0, price=100.0, net_amount=1000.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='AAPL',
                           quantity=15.0, price=80.0, net_amount=1200.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        short_close = gains[0]
        self.assertEqual(short_close['direction'], 'SHORT')
        self.assertAlmostEqual(short_close['qty'], 10.00, places=2)
        # Close cost: 10/15 * 1200 = 800; open proceeds 1000; gain 200
        self.assertAlmostEqual(short_close['raw_gain'], 200.00, places=2)

        # Inventory: 5 shares long (cost = 5/15 * 1200 = 400)
        inv = next(i for i in result['inventory'] if i['symbol'] == 'AAPL')
        self.assertAlmostEqual(inv['qty'], 5.00, places=2)
        self.assertAlmostEqual(inv['total_cost'], 400.00, places=2)


class TestShortHoldingPeriod(unittest.TestCase):
    """Stand-alone shorts are always SHORT_TERM (no §1233(b)(1) detection)."""

    def test_short_held_over_one_year_still_short_term(self):
        """A short held > 1 year still produces SHORT_TERM gain since there's
        no offsetting long for §1233(b)(1) to apply. (Detection of offsetting
        long+short of substantially identical property is out of scope v1.)"""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='AAPL',
                           quantity=-1.0, price=100.0, net_amount=100.00,
                           currency='USD', account='M'),
            # Closed 18 months later
            TaxTransaction(action='BUYSELL', date='2025-07-15', symbol='AAPL',
                           quantity=1.0, price=80.0, net_amount=80.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(gains[0]['term'], 'SHORT_TERM')


class TestNoWashFlagOnShorts(unittest.TestCase):
    """--no-wash also disables short-side wash detection."""

    def test_no_wash_leaves_short_loss_intact(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=-1.0, price=100.0, net_amount=100.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='AAPL',
                           quantity=1.0, price=120.0, net_amount=120.00,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='AAPL',
                           quantity=-1.0, price=110.0, net_amount=110.00,
                           currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs, detect_wash_sales=False)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        # First close at -$20 loss, but no disallowance.
        self.assertAlmostEqual(gains[0]['raw_gain'], -20.00, places=2)
        self.assertAlmostEqual(gains[0]['gain'], -20.00, places=2)
        self.assertAlmostEqual(gains[0]['disallowed_amount'], 0.0, places=4)
        self.assertEqual(len(result['wash_sales']), 0)


if __name__ == '__main__':
    unittest.main()

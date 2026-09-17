"""Tests for the US engine's option-premium roll-in across cross-zero
stock legs.

When an option ASSIGN closes, the would-be option gain is staged in
`pending_option_adjustments[underlying]` and the next stock-leg
BUY/SELL on that underlying consumes it (per Pub 550 — short-call
premium boosts the assigned stock's proceeds; short-put premium
reduces the assigned stock's cost basis).

Both directions previously applied the premium to only ONE side of a
cross-zero stock leg:
  - SELL closing long + opening short: only the close-long chunks got
    the premium (line 1828-1829); the leftover short open at line 1971
    was bare strike — premium share silently lost.
  - BUY closing short + opening long: only the leftover long open
    consumed the premium; the close-short chunks at line 1597 used
    bare strike — premium share silently lost.

These tests pin the symmetric fix that apportions premium across both
chunks.
"""
import unittest

from taxjson.lib.core import TaxTransaction, USATaxRules


class TestCallAssignmentLeftoverShort(unittest.TestCase):
    def test_premium_apportioned_across_close_long_and_leftover_short(self):
        """Short-call assignment exceeds the long pool: 70 shares close
        the long, 30 leftover open a short. Premium ($500 total, or $5
        per share) must boost BOTH the long-close gain AND the
        leftover short's opening proceeds."""
        rules = USATaxRules()
        txs = [
            # Long 70 stock at $90.
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL.US', quantity=70.0, price=90.0,
                           net_amount=6300.0, currency='USD', account='M'),
            # Sell-to-open 1 short call for $5 premium ($500 net).
            TaxTransaction(action='BUYSELL', date='2025-01-20',
                           symbol='AAPL250320C00100000.US',
                           quantity=-1.0, price=5.0, net_amount=500.0,
                           currency='USD', account='M'),
            # Call assigned: option closes at $0 (it vanishes).
            TaxTransaction(action='ASSIGN', date='2025-03-20',
                           symbol='AAPL250320C00100000.US',
                           quantity=1.0, price=0.0, net_amount=0.0,
                           currency='USD', account='M'),
            # Forced SELL 100 at strike $100. 70 close long, 30 leftover short.
            TaxTransaction(action='BUYSELL', date='2025-03-20',
                           symbol='AAPL.US', quantity=-100.0, price=100.0,
                           net_amount=10000.0, currency='USD', account='M'),
            # Cover the 30-share short later at $95 to realize the short gain.
            TaxTransaction(action='BUYSELL', date='2025-04-15',
                           symbol='AAPL.US', quantity=30.0, price=95.0,
                           net_amount=2850.0, currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs, detect_wash_sales=False)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        long_close = next(g for g in gains
                          if g['date'] == '2025-03-20' and g['direction'] == 'LONG')
        short_close = next(g for g in gains
                           if g['date'] == '2025-04-15' and g['direction'] == 'SHORT')
        # 70 × ($100 + $5 premium-share - $90) = 70 × $15 = $1050.
        self.assertAlmostEqual(long_close['raw_gain'], 1050.0, places=2)
        # Short opened at strike + premium-share = $100 + $5 = $105/share.
        # Covered at $95. Gain per share = $10. Total: 30 × $10 = $300.
        # Pre-fix this was $150 (short opened at bare strike $100).
        self.assertAlmostEqual(short_close['raw_gain'], 300.0, places=2)


class TestPutAssignmentCloseShort(unittest.TestCase):
    def test_premium_apportioned_across_close_short_and_leftover_long(self):
        """Short-put assignment lands on an existing short: 30 shares
        cover the short, 70 leftover open a long. Premium ($500 total,
        or $5 per share) must reduce BOTH the close-short cost AND the
        leftover long's basis."""
        rules = USATaxRules()
        txs = [
            # Short 30 shares at $100. Proceeds $3000.
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL.US', quantity=-30.0, price=100.0,
                           net_amount=3000.0, currency='USD', account='M'),
            # Sell-to-open 1 short put for $5 premium ($500 net).
            TaxTransaction(action='BUYSELL', date='2025-01-20',
                           symbol='AAPL250320P00090000.US',
                           quantity=-1.0, price=5.0, net_amount=500.0,
                           currency='USD', account='M'),
            # Put assigned: option vanishes.
            TaxTransaction(action='ASSIGN', date='2025-03-20',
                           symbol='AAPL250320P00090000.US',
                           quantity=1.0, price=0.0, net_amount=0.0,
                           currency='USD', account='M'),
            # Forced BUY 100 at strike $90. Close 30 short, open 70 long.
            TaxTransaction(action='BUYSELL', date='2025-03-20',
                           symbol='AAPL.US', quantity=100.0, price=90.0,
                           net_amount=9000.0, currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs, detect_wash_sales=False)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        short_close = next(g for g in gains
                           if g['date'] == '2025-03-20' and g['direction'] == 'SHORT')
        # Short opened at $100/share. Closed at strike - premium = $90 - $5 = $85.
        # Gain per share = $15. Total: 30 × $15 = $450.
        # Pre-fix this was $300 (close-short used bare strike $90).
        self.assertAlmostEqual(short_close['raw_gain'], 450.0, places=2)
        # Leftover 70 long opened at strike - premium-share = $85/share.
        # 70 × $85 = $5950.
        aapl_inv = next(h for h in result['inventory'] if h['symbol'] == 'AAPL.US')
        self.assertAlmostEqual(aapl_inv['qty'], 70.0, places=2)
        self.assertAlmostEqual(aapl_inv['total_cost'], 5950.0, places=2)


if __name__ == '__main__':
    unittest.main()

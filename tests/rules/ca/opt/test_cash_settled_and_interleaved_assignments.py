"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestAssignmentPremiumNotHijacked(unittest.TestCase):
    """Deep-audit 2026-08: the option-assignment premium staged by the
    ASSIGN option leg was consumed by the FIRST same-symbol trade, not
    the assignment's own stock leg — an unrelated trade sorted between
    the two legs absorbed the premium, misstating both dispositions.

    Formerly: test_audit_2026_08_fixes.TestAssignmentPremiumNotHijacked"""

    def test_canada_unrelated_sale_between_legs(self):
        txs = [
            # Hold 100 QRS @ 4000 (ACB 40/sh).
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='QRS.US', quantity=100.0, price=40.0,
                           net_amount=4000.0, currency='USD', account='M'),
            # Short put, premium +200; assigned.
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           time='10:00:00',
                           symbol='QRS250718P00050000.US', quantity=-2.0,
                           price=1.0, net_amount=200.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='09:00:00',
                           symbol='QRS250718P00050000.US', quantity=2.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            # UNRELATED sale of 40 held shares at ACB (gain must be 0).
            TaxTransaction(action='BUYSELL', date='2025-03-10',
                           time='09:10:00', symbol='QRS.US',
                           quantity=-40.0, price=40.0, net_amount=1600.0,
                           currency='USD', account='M'),
            # The assignment's own stock leg: buy 200 @ 50.
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='09:30:00', symbol='QRS.US',
                           quantity=200.0, price=50.0, net_amount=10000.0,
                           currency='USD', account='M'),
        ]
        result = CanadaTaxRules().compute_gains(txs)
        sale = next(g for g in result['transactions']
                    if g.get('qty') == 40.0 and g.get('action') != 'DIVIDEND')
        self.assertAlmostEqual(
            sale['gain'], 0.0, places=2,
            msg="the unrelated sale absorbed the staged put premium "
                "(buggy gain was +200)")

    def test_usa_unrelated_buy_between_legs(self):
        txs = [
            # Short put, premium +500; assigned.
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           time='10:00:00',
                           symbol='QRS250718P00100000.US', quantity=-1.0,
                           price=5.0, net_amount=500.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='09:00:00',
                           symbol='QRS250718P00100000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            # UNRELATED buy at market.
            TaxTransaction(action='BUYSELL', date='2025-03-10',
                           time='09:30:00', symbol='QRS.US',
                           quantity=100.0, price=100.0,
                           net_amount=10000.0, currency='USD',
                           account='M'),
            # Assignment stock leg: buy 100 @ strike.
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='16:00:00', symbol='QRS.US',
                           quantity=100.0, price=100.0,
                           net_amount=10000.0, currency='USD',
                           account='M'),
            # Sell the FIRST lot (FIFO) — must realize gain 1000, not
            # 1500 (basis 10000, not premium-reduced 9500).
            TaxTransaction(action='BUYSELL', date='2025-04-01',
                           symbol='QRS.US', quantity=-100.0, price=110.0,
                           net_amount=11000.0, currency='USD',
                           account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        sale = next(g for g in result['transactions']
                    if g['date'] == '2025-04-01')
        self.assertAlmostEqual(
            sale['gain'], 1000.0, places=2,
            msg="the unrelated 09:30 buy absorbed the premium and its "
                "lot's basis dropped to 9,500 (buggy gain +1,500)")


class TestCashSettledAssignmentRealized(unittest.TestCase):
    """Deep-audit 2026-08 #3: a cash-settled index option ASSIGN
    (XSP/SPX — the underlying never trades as stock) staged its premium
    for a stock leg that structurally cannot exist; the option's entire
    P&L vanished from the results with only a stderr warning.

    Formerly: test_audit_2026_08_fixes.TestCashSettledAssignmentRealized"""

    CANADA_TXS = [
        # Short 1 XSP put, premium +300.
        dict(action='BUYSELL', date='2025-01-10', time='10:00:00',
             symbol='XSP250620P00068500.US', quantity=-1.0, price=3.0,
             net_amount=300.0, currency='USD', account='M'),
        # Cash-settled assignment: pay out 450.
        dict(action='ASSIGN', date='2025-06-20', time='16:00:00',
             symbol='XSP250620P00068500.US', quantity=1.0, price=4.5,
             net_amount=450.0, currency='USD', account='M'),
    ]

    def test_canada_realizes_the_option_pl(self):
        txs = [TaxTransaction(**t) for t in self.CANADA_TXS]
        result = CanadaTaxRules().compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        self.assertEqual(
            len(gains), 1,
            "the cash-settled close vanished from the results entirely")
        self.assertAlmostEqual(gains[0]['gain'], -150.0, places=2)

    def test_usa_realizes_the_option_pl(self):
        txs = [TaxTransaction(**t) for t in self.CANADA_TXS]
        result = USATaxRules().compute_gains(txs)
        closes = [g for g in result['transactions']
                  if g.get('raw_gain') is not None]
        self.assertEqual(len(closes), 1)
        self.assertAlmostEqual(closes[0]['raw_gain'], -150.0, places=2)

    def test_real_stock_assignment_still_rolls_premium(self):
        """Guard the fix's boundary: an assignment whose underlying DOES
        trade keeps the premium-roll behavior."""
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-10',
                           time='10:00:00',
                           symbol='AAPL250117P00140000.US', quantity=-1.0,
                           price=2.0, net_amount=200.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-01-17',
                           time='16:00:00',
                           symbol='AAPL250117P00140000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-01-17',
                           time='16:00:01', symbol='AAPL.US',
                           quantity=100.0, price=140.0,
                           net_amount=14000.0, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-01',
                           symbol='AAPL.US', quantity=-100.0, price=140.0,
                           net_amount=14000.0, currency='USD',
                           account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        sale = next(g for g in result['transactions']
                    if g['date'] == '2025-03-01')
        # Basis 14,000 − 200 premium = 13,800 → gain +200.
        self.assertAlmostEqual(sale['raw_gain'], 200.0, places=2)


if __name__ == '__main__':
    unittest.main()

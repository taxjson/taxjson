import unittest
import sys
from pathlib import Path

from taxjson.lib.core import TaxTransaction, CanadaTaxRules, USATaxRules

class TestTaxRules(unittest.TestCase):
    def test_canada_acb_basic(self):
        """Scenario: Simple Buy and Sell in Canada (ACB)"""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.TO', quantity=100.0, net_amount=10000.0, currency='CAD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC.TO', quantity=-100.0, net_amount=8000.0, currency='CAD', id='s1'),
        ]
        result = rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], -2000.0)

    def test_canada_assignment(self):
        """Scenario: Option Assignment rolling into Stock ACB (CRA rules)"""
        rules = CanadaTaxRules()
        # AAPL Call Option sold for 500 (Open Short)
        # Then assigned: buying AAPL at 15000 (Close Option at gain, roll to stock)
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='AAPL260116C00150000', quantity=-1.0, net_amount=500.0, currency='CAD', id='opt1'),
            TaxTransaction(action='ASSIGN', date='2025-01-10', symbol='AAPL260116C00150000', quantity=1.0, net_amount=0.0, currency='CAD', id='asgn1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='AAPL', quantity=100.0, net_amount=15000.0, currency='CAD', id='stk1'),
        ]
        result = rules.compute_gains(txs)
        inventory = {item['symbol']: item for item in result['inventory']}
        # Option gain (500) rolls into stock basis: 15000 - 500 = 14500
        self.assertAlmostEqual(inventory['AAPL']['total_cost'], 14500.0)

    def test_canada_wash_sale_basic(self):
        """Scenario: Simple Superficial Loss detection (Canada)"""
        rules = CanadaTaxRules()
        # Buy 100 @ 100, Sell 100 @ 80 (Loss 2000), Buy 100 @ 85 (Replacement)
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC', quantity=100.0, net_amount=10000.0, currency='CAD', id='bw1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC', quantity=-100.0, net_amount=8000.0, currency='CAD', id='sw1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='ABC', quantity=100.0, net_amount=8500.0, currency='CAD', id='bw2'),
        ]
        result = rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 2000.0)

    def test_usa_fifo_basic(self):
        """Scenario: Simple Buy and Sell in USA (FIFO)"""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC', quantity=100.0, net_amount=10000.0, currency='USD', id='b1u'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC', quantity=-100.0, net_amount=12000.0, currency='USD', id='s1u'),
        ]
        result = rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertTrue(result['summary']['total_gain'] > 0)

if __name__ == '__main__':
    unittest.main()

import unittest
from taxjson.lib.core import CanadaTaxRules
from test_ported_tt_helper import parse_tt_lines

class TestOptionsLogic(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_call_sell_buy_loss(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 AAPL250117C00200000.US -1.0 USD 2.00 200.00 0.00
        BUYSELL 2025-01-10 10:00:00 AAPL250117C00200000.US 1.0 USD 3.00 300.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], -100.0)

    def test_call_sell_buy_profit(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 AAPL250117C00200000.US -1.0 USD 2.00 200.00 0.00
        BUYSELL 2025-01-10 10:00:00 AAPL250117C00200000.US 1.0 USD 0.50 50.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], 150.0)

    def test_call_sell_expire_worthless(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 AAPL250117C00200000.US -1.0 USD 2.00 200.00 0.00
        BUYSELL 2025-01-17 16:00:00 AAPL250117C00200000.US 1.0 USD 0.00 0.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], 200.0)

    def test_call_sell_assigned(self):
        content = """
        BUYSELL 2024-12-01 10:00:00 AAPL.US 100.0 USD 150.00 15000.00 0.00
        BUYSELL 2025-01-01 10:00:00 AAPL250117C00160000.US -1.0 USD 5.00 500.00 0.00
        ASSIGN 2025-01-17 16:00:00 AAPL250117C00160000.US 1.0 USD 0.00 0.00 0.00
        ASSIGN 2025-01-17 16:00:01 AAPL.US -100.0 USD 160.00 16000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        
        # Gain should be 1500 (Proceeds 16500 - Cost 15000)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], 1500.0)
        self.assertEqual(result['transactions'][0]['symbol'], 'AAPL.US')

    def test_put_sell_assigned(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 AAPL250117P00140000.US -1.0 USD 5.00 500.00 0.00
        ASSIGN 2025-01-17 16:00:00 AAPL250117P00140000.US 1.0 USD 0.00 0.00 0.00
        ASSIGN 2025-01-17 16:00:01 AAPL.US 100.0 USD 140.00 14000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        
        # Should reduce stock cost basis by 500, making it 13500.
        inventory = {i['symbol']: i for i in result['inventory']}
        self.assertAlmostEqual(inventory['AAPL.US']['total_cost'], 13500.0)

    def test_short_sale_gains(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-15 10:00:00 SHORT.US 100 USD 80.00 8000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], 2000.0)

    def test_multiple_buy_lots_partial_sale(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 MULTI.US 100 USD 10.00 1000.00 0.00
        BUYSELL 2025-01-02 10:00:00 MULTI.US 100 USD 20.00 2000.00 0.00
        BUYSELL 2025-01-10 10:00:00 MULTI.US -50 USD 30.00 1500.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['summary']['total_gain'], 750.0)

    def test_covered_call_partial_assignment(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 PARTIAL.US 200 USD 50.00 10000.00 0.00
        BUYSELL 2025-02-01 10:00:00 PARTIAL250220C00060000.US -2 USD 5.00 1000.00 0.00
        ASSIGN 2025-02-20 16:00:00 PARTIAL250220C00060000.US 1 USD 0.00 0.00 0.00
        BUYSELL 2025-02-20 16:01:00 PARTIAL.US -100 USD 60.00 6000.00 0.00
        BUYSELL 2025-03-01 10:00:00 PARTIAL.US -100 USD 65.00 6500.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 2)
        # First stock sale: 6000 + 500 premium = 6500. Cost = 5000. Gain = 1500
        self.assertAlmostEqual(result['transactions'][0]['gain'], 1500.0)
        # Second stock sale: 6500 proceeds. Cost = 5000. Gain = 1500
        self.assertAlmostEqual(result['transactions'][1]['gain'], 1500.0)

if __name__ == '__main__':
    unittest.main()

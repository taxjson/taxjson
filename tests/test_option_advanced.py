import unittest
from taxjson.lib.core import CanadaTaxRules
from test_ported_tt_helper import parse_tt_lines

class TestOptionAdvancedLogic(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_fee_handling(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 WITHFEES.US 100 USD 50.00 5000.00 5.00
        BUYSELL 2025-01-15 10:00:00 WITHFEES250117C00060000.US -1 USD 5.00 497.00 3.00
        ASSIGN 2025-01-17 16:00:00 WITHFEES250117C00060000.US 1 USD 0.00 0.00 0.00
        BUYSELL 2025-01-17 16:01:00 WITHFEES.US -100 USD 60.00 6000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        gain_info = result['transactions'][0]
        # Proceeds should be 6000 + 497 = 6497. Cost is 5000. Gain = 1497.
        self.assertAlmostEqual(gain_info['proceeds'], 6497.0)
        self.assertAlmostEqual(gain_info['gain'], 1497.0)

    def test_multiple_assignments_stack(self):
        content = """
        BUYSELL 2025-01-01 10:00:00 MULTI.US 200 USD 50.00 10000.00 0.00
        BUYSELL 2025-01-10 10:00:00 MULTI250117C00060000.US -1 USD 5.00 500.00 0.00
        BUYSELL 2025-01-15 10:00:00 MULTI250117C00065000.US -1 USD 4.00 400.00 0.00
        ASSIGN 2025-01-17 16:00:00 MULTI250117C00060000.US 1 USD 0.00 0.00 0.00
        ASSIGN 2025-01-17 16:00:00 MULTI250117C00065000.US 1 USD 0.00 0.00 0.00
        BUYSELL 2025-01-17 16:01:00 MULTI.US -100 USD 60.00 6000.00 0.00
        BUYSELL 2025-01-17 16:01:00 MULTI.US -100 USD 65.00 6500.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 2)
        # First stock sale should scoop all stacked premiums (900). Proceeds = 6000 + 900 = 6900
        self.assertAlmostEqual(result['transactions'][0]['proceeds'], 6900.0)
        self.assertAlmostEqual(result['transactions'][0]['gain'], 1900.0)
        # Second sale should just be 6500
        self.assertAlmostEqual(result['transactions'][1]['proceeds'], 6500.0)
        self.assertAlmostEqual(result['transactions'][1]['gain'], 1500.0)

if __name__ == '__main__':
    unittest.main()

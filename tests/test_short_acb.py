import unittest
from taxjson.lib.core import CanadaTaxRules
from test_ported_tt_helper import parse_tt_lines

class TestShortPositionLogic(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_short_wash_sale_basic(self):
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 120.00 12000.00 0.00
        BUYSELL 2025-01-20 09:30:00 SHORT.US -100 USD 130.00 13000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 2000.0)

    def test_short_position_partial_close_wash(self):
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -200 USD 100.00 20000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 120.00 12000.00 0.00
        BUYSELL 2025-01-20 09:30:00 SHORT.US -100 USD 130.00 13000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 1)

    def test_short_wash_deferred_loss_recognized_with_correct_sign(self):
        """A disallowed SHORT loss must DEFER onto the replacement short so
        it's recognized when the replacement is covered — not invert into a
        deferred gain.

        Regression: the wash ADJUST added the disallowed amount to the
        replacement pool's total_cost for BOTH directions. A short pool
        stores opening PROCEEDS and the close gain is (cost_basis -
        cover_cost), so adding raised the gain instead of lowering it.

          short 100@50 (proceeds 5000); cover 100@60 (loss 1000, disallowed);
          re-short 100@58 (proceeds 5800); cover 100@55 (cost 5500).

        Economic total = -1000 + 300 = -700. The disallowed 1000 defers onto
        the replacement, so the final cover realizes -700 (= 300 - 1000), not
        the buggy +1300 (= 300 + 1000)."""
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 50.00 5000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 60.00 6000.00 0.00
        BUYSELL 2025-01-15 09:30:00 SHORT.US -100 USD 58.00 5800.00 0.00
        BUYSELL 2025-03-20 09:30:00 SHORT.US 100 USD 55.00 5500.00 0.00
        """
        result = self.rules.compute_gains(parse_tt_lines(content))
        # Loss on the first cover is disallowed and deferred.
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 1000.0)
        by_date = {t['date']: t for t in result['transactions']}
        # First cover: full loss disallowed this period.
        self.assertAlmostEqual(by_date['2025-01-10']['gain'], 0.0)
        # Final cover: deferred loss recognized with the correct sign.
        self.assertAlmostEqual(by_date['2025-03-20']['gain'], -700.0)
        # Total recognized equals the true economic result.
        self.assertAlmostEqual(sum(t['gain'] for t in result['transactions']), -700.0)

    def test_mixed_long_and_short(self):
        content = """
        BUYSELL 2025-01-01 09:30:00 LONG.US 100 USD 50.00 5000.00 0.00
        BUYSELL 2025-01-02 09:30:00 SHORT.US -50 USD 80.00 4000.00 0.00
        BUYSELL 2025-01-10 09:30:00 LONG.US -100 USD 60.00 6000.00 0.00
        BUYSELL 2025-01-11 09:30:00 SHORT.US 50 USD 90.00 4500.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 2)

if __name__ == '__main__':
    unittest.main()

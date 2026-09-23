import unittest
from taxjson.lib.core import CanadaTaxRules
from test_ported_tt_helper import parse_tt_lines

class TestShortPositionLogic(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    # 2026-09 Canada audit: ITA s.54 needs an ACQUISITION of identical
    # property still OWNED at day 30. A new short sale acquires nothing, so
    # a re-short never denies a cover loss (the old behaviour was the US
    # §1091(e) rule); a LONG rebuy held at day 30 does, and the deferred
    # loss lands on that long position's ACB (s.53(1)(f)).

    def test_re_short_after_cover_loss_is_not_superficial(self):
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 120.00 12000.00 0.00
        BUYSELL 2025-01-20 09:30:00 SHORT.US -100 USD 130.00 13000.00 0.00
        """
        result = self.rules.compute_gains(parse_tt_lines(content))
        self.assertEqual(len(result['wash_sales']), 0)
        self.assertAlmostEqual(result['transactions'][0]['gain'], -2000.0)

    def test_short_position_partial_close_then_reshort_no_wash(self):
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -200 USD 100.00 20000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 120.00 12000.00 0.00
        BUYSELL 2025-01-20 09:30:00 SHORT.US -100 USD 130.00 13000.00 0.00
        """
        result = self.rules.compute_gains(parse_tt_lines(content))
        self.assertEqual(len(result['wash_sales']), 0)

    def test_long_rebuy_after_cover_loss_defers_onto_the_long(self):
        """short 100@50 (5000); cover 100@60 (loss 1000); BUY 100@58 long
        within the window and hold; sell it 100@55 after day 30.
        The cover loss is superficial (long rebuy held at day 30) and its
        1000 bumps the long's ACB: the later sale realizes -300 - 1000 =
        -1300, and the lifetime total (-1000 - 300) is conserved."""
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 50.00 5000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 60.00 6000.00 0.00
        BUYSELL 2025-01-15 09:30:00 SHORT.US 100 USD 58.00 5800.00 0.00
        BUYSELL 2025-03-20 09:30:00 SHORT.US -100 USD 55.00 5500.00 0.00
        """
        result = self.rules.compute_gains(parse_tt_lines(content))
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 1000.0)
        by_date = {t['date']: t for t in result['transactions']}
        self.assertAlmostEqual(by_date['2025-01-10']['gain'], 0.0)
        self.assertAlmostEqual(by_date['2025-03-20']['gain'], -1300.0)
        self.assertAlmostEqual(sum(t['gain'] for t in result['transactions']), -1300.0)

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

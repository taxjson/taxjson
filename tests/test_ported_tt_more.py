"""Tests ported from the legacy tt_gains.pl test corpus.

Sources:
- test_real_world.t           — put-snowball / covered-call regressions (synthetic tickers)
- test_wash_edge_cases.t      — different tickers, grace period, multi-replacement
- test_short_position_acb.t   — short-position ACB edge cases
- wash_rollover.tt            — rolling wash-sale ACB carry-forward
- wash_exhaustive_taxable.tt  — multi-lot replacement, pre-buy substitution
"""

import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from _support.tt_ported import parse_tt_lines


class TestRealWorldRegressions(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_put_snowball_no_double_adjustment(self):
        """test_real_world.t: puts snowball bug (synthetic SNWB).

        Two same-day put assignments rolling premium into stock acquisitions.
        The bug being regressed against is "snowballing" adjustments — the same
        premium getting applied twice. Final sale should reflect each premium
        applied exactly once.
        """
        content = """
        ASSIGN  2025-09-19 16:00:00 SNWB250919P00014000.US 1.0 USD 0.00 0.00 0.00
        BUYSELL 2025-09-19 16:00:01 SNWB.US               100.0 USD 14.00 1400.00 0.00
        ASSIGN  2025-09-19 16:00:02 SNWB250919P00010500.US 1.0 USD 0.00 0.00 0.00
        BUYSELL 2025-09-19 16:00:03 SNWB.US               100.0 USD 10.50 1050.00 0.00
        BUYSELL 2025-10-22 13:00:00 SNWB.US              -200.0 USD 9.37 1874.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions'] if g.get('symbol') == 'SNWB.US']
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(sells[0]['qty'], 200.0)
        # Without snowball: cost = 1400 + 1050 = 2450 (each premium applied once),
        # proceeds = 1874, gain = -576.
        self.assertAlmostEqual(sells[0]['cost'], 2450.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], -576.0, places=2)

    def test_covered_call_assignment(self):
        """test_real_world.t: covered call (synthetic CCX).

        Hold 400 shares, sell 4 calls (premium 635.00), get assigned, sell 400
        at strike 27. Effective proceeds = 10800 + 635 = 11435.
        Cost = 8600. Gain = 2835.
        """
        content = """
        BUYSELL 2025-09-08 10:00:00 CCX251017C00027000.TO -4.0 CAD 1.60 635.00 5.00
        BUYSELL 2025-09-08 10:00:00 CCX.TO                 400.0 CAD 21.50 8600.00 0.00
        ASSIGN  2025-10-20 16:00:00 CCX251017C00027000.TO 4.0 CAD 0.00 0.00 0.00
        BUYSELL 2025-10-20 16:00:01 CCX.TO                -400.0 CAD 27.00 10800.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions'] if g.get('symbol') == 'CCX.TO']
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(sells[0]['gain'], 2835.0, places=2)


class TestWashEdgeCases(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_different_ticker_extensions_do_not_trigger(self):
        """test_wash_edge_cases.t: ABC.US loss should NOT match ABC.TO buy.

        Different exchange listings are different securities for ACB purposes.
        """
        content = """
        BUYSELL 2025-01-01 09:30:00 ABC.US 100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 ABC.US -100 USD 80.00 8000.00 0.00
        BUYSELL 2025-01-15 09:30:00 ABC.TO 100 CAD 100.00 10000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 0,
                         "different .US/.TO suffixes are different ACB pools")

    def test_grace_period_default_no_match_at_day_32(self):
        """test_wash_edge_cases.t: Buy on day 32 (>30 days after loss) should not trigger."""
        content = """
        BUYSELL 2025-01-01 09:30:00 GRACE.US 100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 GRACE.US -100 USD 80.00 8000.00 0.00
        BUYSELL 2025-02-11 09:30:00 GRACE.US 100 USD 80.00 8000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 0)

    def test_multiple_replacement_lots(self):
        """wash_exhaustive_taxable.tt CASE 2: 30 + 70 within window after 100 loss.

        Loss is 200 (sold 100 at 8 vs 10 cost). Each replacement contributes its
        share — together they fully cover the loss qty, so 200 should be disallowed.
        """
        content = """
        BUYSELL 2025-04-01 10:00:00 MULTIBUY.US 100 USD 10.00 1000.00 0.00
        BUYSELL 2025-04-10 10:00:00 MULTIBUY.US -100 USD 8.00 800.00 0.00
        BUYSELL 2025-04-12 10:00:00 MULTIBUY.US 30 USD 9.00 270.00 0.00
        BUYSELL 2025-04-15 10:00:00 MULTIBUY.US 70 USD 9.00 630.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 1)
        # Both replacements together cover all 100 shares of the loss.
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_qty'], 100.0, places=4)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 200.0, places=2)

    def test_pre_buy_substitution(self):
        """wash_exhaustive_taxable.tt CASE 3: Buy A, Buy B (drop), Sell A at loss.

        Lot B held at the +30 deadline counts as substitute property.
        """
        content = """
        BUYSELL 2025-05-01 10:00:00 PREBUY.US 100 USD 10.00 1000.00 0.00
        BUYSELL 2025-05-15 10:00:00 PREBUY.US 100 USD 9.00 900.00 0.00
        BUYSELL 2025-05-20 10:00:00 PREBUY.US -100 USD 8.00 800.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 1)


class TestShortPositionACB(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_short_break_even_then_new_short(self):
        """test_short_position_acb.t: open short, close at break-even, re-open short."""
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-15 09:30:00 SHORT.US 100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-20 09:30:00 SHORT.US -100 USD 120.00 12000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        # First close: 0 gain. New short open isn't a realized event.
        sells = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(sells[0]['gain'], 0.0, places=2)
        # Final inventory: short 100 with negative ACB
        inv = {i['symbol']: i for i in result['inventory']}
        self.assertAlmostEqual(inv['SHORT.US']['qty'], -100.0)

    def test_short_replacement_partial_match(self):
        """test_short_position_acb.t, re-premised (2026-09 Canada audit):
        a NEW short is not an acquisition under ITA s.54, so a re-short
        never denies a cover loss; a 150-share LONG rebuy held at day 30
        denies the 100-share loss in full (min of sold/acquired/held)."""
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 120.00 12000.00 0.00
        BUYSELL 2025-01-15 09:30:00 SHORT.US -150 USD 130.00 19500.00 0.00
        """
        result = self.rules.compute_gains(parse_tt_lines(content))
        self.assertEqual(len(result['wash_sales']), 0)
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 100 USD 120.00 12000.00 0.00
        BUYSELL 2025-01-15 09:30:00 SHORT.US 150 USD 130.00 19500.00 0.00
        """
        result = self.rules.compute_gains(parse_tt_lines(content))
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_qty'], 100.0, places=4)

    def test_short_acb_resets_after_full_close(self):
        """test_short_position_acb.t: short opens, fully closed; pool should be clean."""
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-15 09:30:00 SHORT.US 100 USD 110.00 11000.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(sells), 1)
        # Short open at 100, cover at 110 → 1000 loss.
        self.assertAlmostEqual(sells[0]['gain'], -1000.0, places=2)
        # No residual inventory.
        inv = [i for i in result['inventory'] if i['symbol'] == 'SHORT.US']
        self.assertEqual(len(inv), 0)

    def test_negative_acb_multiple_short_closes(self):
        """test_short_position_acb.t: 60+40 short cover from one short open."""
        content = """
        BUYSELL 2025-01-01 09:30:00 SHORT.US -100 USD 100.00 10000.00 0.00
        BUYSELL 2025-01-10 09:30:00 SHORT.US 60 USD 120.00 7200.00 0.00
        BUYSELL 2025-01-20 09:30:00 SHORT.US 40 USD 130.00 5200.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(sells), 2)
        # First close: 60 shares cost basis = 6000, proceeds = 7200, loss = -1200.
        # Second close: 40 shares cost basis = 4000, proceeds = 5200, loss = -1200.
        gains = sorted([g['gain'] for g in sells])
        self.assertAlmostEqual(gains[0], -1200.0, places=2)
        self.assertAlmostEqual(gains[1], -1200.0, places=2)


class TestRollingWashSale(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_rolling_wash_carries_forward(self):
        """wash_rollover.tt: a chain of superficial losses carries the ACB
        to the shares held at day 30.

        Buy 100@10 (01-01), Sell 100@8 (01-15: a loss of 200). The rebuy
        of 01-18 (100@9) is sold again on 01-20, inside the window, so it
        is not the substituted property (s.54: acquired in the window and
        owned at its end); the 100@6 bought 01-22 and held are, for both
        losses (TJ-2711). The 01-15 loss: 200 denied, added to the 01-22
        shares. The 01-20 sale: proceeds 700, cost 900 (its own), a loss
        of 200, denied and added to the 01-22 shares too. Total disallowed
        = 200 + 200 = 400. Final hold: 100 shares, $1000 ACB (600 + 400).
        Before TJ-2711 the first 200 joined the 01-18 shares, the 01-20
        sale showed a loss of 400, and the total denied was 600 for the
        same final ACB.
        """
        content = """
        BUYSELL 2025-01-01 09:30:00 TEST.US 100 USD 10.00 1000.00 0.00
        BUYSELL 2025-01-15 09:30:00 TEST.US -100 USD 8.00 800.00 0.00
        BUYSELL 2025-01-18 09:30:00 TEST.US 100 USD 9.00 900.00 0.00
        BUYSELL 2025-01-20 09:30:00 TEST.US -100 USD 7.00 700.00 0.00
        BUYSELL 2025-01-22 09:30:00 TEST.US 100 USD 6.00 600.00 0.00
        """
        txs = parse_tt_lines(content)
        result = self.rules.compute_gains(txs)
        # Two superficial losses of 200, both added to the 01-22 shares.
        self.assertEqual(len(result['wash_sales']), 2)
        self.assertEqual([w['disallowed_amount'] for w in result['wash_sales']],
                         [200.0, 200.0])
        total_disallowed = sum(w['disallowed_amount'] for w in result['wash_sales'])
        self.assertAlmostEqual(total_disallowed, 400.0, places=2)
        # Final inventory: 100 shares with $1000 ACB ($10/sh).
        inv = {i['symbol']: i for i in result['inventory']}
        self.assertAlmostEqual(inv['TEST.US']['qty'], 100.0)
        self.assertAlmostEqual(inv['TEST.US']['total_cost'], 1000.0, places=2)


if __name__ == '__main__':
    unittest.main()

"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.

Formerly: test_review_fixes
"""

import io
import unittest
from contextlib import redirect_stderr

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction


class TestC6CurrencyMixGuard(unittest.TestCase):
    """C6: pooling the same symbol across two currencies must error loudly.

    Formerly: test_review_fixes.TestC6CurrencyMixGuard"""

    def test_mixed_currency_in_same_pool_raises(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='CAD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-02', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b2'),
        ]
        with self.assertRaises(ValueError) as cm:
            rules.compute_gains(txs)
        self.assertIn('Currency mismatch', str(cm.exception))

    def test_cash_flow_events_in_different_currencies_do_not_trigger_guard(self):
        """INTEREST/FEE/DIVIDEND on the synthetic CASH symbol can mix currencies."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='INTEREST', date='2025-01-01', symbol='CASH', quantity=0.0, net_amount=5.0, currency='CAD', type='interest', id='i1'),
            TaxTransaction(action='INTEREST', date='2025-01-02', symbol='CASH', quantity=0.0, net_amount=3.0, currency='USD', type='interest', id='i2'),
            TaxTransaction(action='FEE',      date='2025-01-03', symbol='CASH', quantity=0.0, net_amount=-1.0, currency='USD', type='fee',     id='f1'),
        ]
        # Should not raise; non-capital flow events bypass the pool entirely.
        result = rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 0)


class TestI4LastAcqDateResetOnCrossZero(unittest.TestCase):
    """I4: when a sell crosses zero, leftover is a fresh position; days_held resets.

    Formerly: test_review_fixes.TestI4LastAcqDateResetOnCrossZero"""

    def test_cross_zero_resets_acquisition_date(self):
        rules = CanadaTaxRules()
        # Buy 100 on day 1, oversell 150 on day 100 → leftover is a 50-share short.
        # Cover the short on day 110.
        # Without I4: days_held for the cover would be 100 - day 1 = 99 days.
        # With I4: days_held = 110 - 100 = 10 days.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='CAD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-04-10', symbol='X', quantity=-150.0, net_amount=15000.0, currency='CAD', id='oversell'),
            TaxTransaction(action='BUYSELL', date='2025-04-20', symbol='X', quantity=50.0, net_amount=5500.0, currency='CAD', id='cover'),
        ]
        result = rules.compute_gains(txs)
        # The cover sale produces a realized gain on the short.
        # We assert: days_held for the second realized event is small (10 days),
        # not large (~99 days as it would be with the bug).
        sells = [g for g in result['transactions'] if g.get('direction') in ('LONG', 'SHORT')]
        # The short cover is the SHORT direction realized gain
        short_cover = next((g for g in sells if g['direction'] == 'SHORT'), None)
        self.assertIsNotNone(short_cover, "expected a SHORT realized gain from cover")
        self.assertLessEqual(short_cover['days_held'], 15)


class TestShortPositionSignedCashFlow(unittest.TestCase):
    """SHORT direction gain entries must use signed cash-flow convention.

    A sell-to-open is a cash inflow (negative cost basis) and a buy-to-close
    is a cash outflow (negative proceeds). This matches tt_gains.pl:401-405
    so that downstream summaries compute `proceeds - cost = signed gain`
    correctly without direction-aware logic, and lets taxjson_ccd_gains.py
    filter shorts by `cost < 0` the same way tt_ccd_gains.pl does.

    Formerly: test_review_fixes.TestShortPositionSignedCashFlow
    """

    def test_short_put_cycle_emits_negative_cost_and_proceeds(self):
        rules = CanadaTaxRules()
        # Sell-to-open 4 puts → received 512.73 net.
        # Buy-to-close 4 puts → paid 98.40 net.
        # Profit (cost - proceeds for shorts) = 414.33.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-05-02',
                           symbol='QZA250815P00018500.US', quantity=-4.0,
                           currency='CAD', net_amount=512.734120, id='sto'),
            TaxTransaction(action='BUYSELL', date='2025-05-20',
                           symbol='QZA250815P00018500.US', quantity=4.0,
                           currency='CAD', net_amount=98.402216, id='btc'),
        ]
        result = rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        entry = result['transactions'][0]
        self.assertEqual(entry['direction'], 'SHORT')
        # Signed convention: cost reflects cash inflow (negative), proceeds
        # reflects cash outflow (negative).
        self.assertAlmostEqual(entry['cost'], -512.734120, places=4)
        self.assertAlmostEqual(entry['proceeds'], -98.402216, places=4)
        # Gain remains positive (winning short).
        self.assertAlmostEqual(entry['gain'], 414.331904, places=4)
        # And the invariant proceeds - cost == signed gain holds for shorts now.
        self.assertAlmostEqual(entry['proceeds'] - entry['cost'],
                               entry['gain'], places=4)

    def test_long_position_cost_proceeds_remain_positive(self):
        """Verify long-side gains are unaffected by the short-flip."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='CAD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-02-01', symbol='X',
                           quantity=-100.0, net_amount=11000.0, currency='CAD', id='s1'),
        ]
        result = rules.compute_gains(txs)
        entry = result['transactions'][0]
        self.assertEqual(entry['direction'], 'LONG')
        self.assertAlmostEqual(entry['cost'], 10000.0)
        self.assertAlmostEqual(entry['proceeds'], 11000.0)
        self.assertAlmostEqual(entry['gain'], 1000.0)


class TestInvariantWarning(unittest.TestCase):
    """The disallowance invariant should be silent on healthy data.

    Formerly: test_review_fixes.TestInvariantWarning"""

    def test_no_warning_on_clean_run(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='CAD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='X', quantity=-100.0, net_amount=8000.0, currency='CAD', id='s1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=100.0, net_amount=8500.0, currency='CAD', id='b2'),
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            rules.compute_gains(txs)
        self.assertNotIn('invariant broken', buf.getvalue())


if __name__ == '__main__':
    unittest.main()

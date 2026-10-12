"""Canada-engine regression tests for the `is_other_scope` gate.

Two recent bugs surfaced inside the Canada main loop:

  1. `pending_adjustments.pop(symbol, 0.0)` ran unconditionally —
     a sheltered/affiliated trade interleaved between a taxable option
     ASSIGN and a later taxable stock-leg silently consumed and
     discarded the pending option-premium adjustment.

  2. The currency-mismatch raise + currency-set both ran before the
     `is_other_scope` gate — an opening sheltered/affiliated trade
     could stamp the taxable pool's currency, and a later taxable
     trade in a different currency would hard-error on the mismatch
     even though the two scopes are separate books.

Both fixes gate the offending blocks on `not is_other_scope`. These
tests pin the fix.
"""
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction


class TestPendingAdjustmentScope(unittest.TestCase):
    def test_sheltered_buy_between_assign_and_stock_leg_preserves_premium(self):
        """ASSIGN stages a +$500 premium on the underlying. A
        sheltered BUY on the underlying lands between the ASSIGN and
        the taxable stock-leg SELL. The premium must survive — only
        the taxable stock-leg should pop pending_adjustments."""
        rules = CanadaTaxRules()
        taxable = [
            # Existing long 100 stock at $90 — pool qty=100, cost=$9000.
            TaxTransaction(action='BUYSELL', date='2025-01-01',
                           time='09:00:00', symbol='AAPL.US',
                           quantity=100, price=90.0, net_amount=9000,
                           currency='USD', account='Margin'),
            # Sell-to-open a short call: premium received $500.
            TaxTransaction(action='BUYSELL', date='2025-01-01',
                           time='09:01:00',
                           symbol='AAPL250320C00100000.US',
                           quantity=-1, net_amount=500, currency='USD',
                           account='Margin'),
            # Assignment closes the option at $0; engine stages
            # pending_adjustments['AAPL.US'] = -500 (Canada
            # convention: stored as -gain).
            TaxTransaction(action='ASSIGN', date='2025-03-20',
                           time='09:30:00',
                           symbol='AAPL250320C00100000.US',
                           quantity=1, price=0.0, net_amount=0.0,
                           currency='USD', account='Margin'),
            # Forced stock SELL at strike $100 — must pick up the $500
            # premium roll. effective_proceeds = 10000 + 500 = 10500.
            # Gain = 10500 − 9000 = 1500.
            TaxTransaction(action='BUYSELL', date='2025-03-20',
                           time='10:00:00', symbol='AAPL.US',
                           quantity=-100, price=100.0, net_amount=10000,
                           currency='USD', account='Margin'),
        ]
        sheltered = [
            # Sheltered BUY of the underlying between ASSIGN and the
            # taxable SELL. Pre-fix: silently popped+discarded the
            # premium via the is_sheltered no-op branch.
            TaxTransaction(action='BUYSELL', date='2025-03-20',
                           time='09:45:00', symbol='AAPL.US',
                           quantity=50, price=95.0, net_amount=4750,
                           currency='USD', account='RRSP'),
        ]
        result = rules.compute_gains(taxable,
                                     sheltered_transactions=sheltered)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        stock_sale = next(g for g in gains
                          if g['date'] == '2025-03-20'
                          and g['symbol'] == 'AAPL.US')
        # 1500 with fix; 1000 pre-fix (premium silently lost).
        self.assertAlmostEqual(stock_sale['gain'], 1500.0, places=2)


class TestCurrencyPoolNotStampedByOtherScope(unittest.TestCase):
    def test_sheltered_currency_does_not_lock_taxable_pool(self):
        """An opening sheltered trade in USD on the same symbol the
        user holds in CAD taxable used to stamp the taxable pool's
        currency = USD via the pre-gate `pool['currency'] = tx.currency`
        write. Then the taxable CAD trade hit the mismatch raise even
        though the two scopes are separate books. With the gate, the
        sheltered tx doesn't touch the taxable pool's currency at all."""
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-02-15',
                           time='09:00:00', symbol='X',
                           quantity=100, price=10.0, net_amount=1000,
                           currency='CAD', account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-06-15',
                           time='09:00:00', symbol='X',
                           quantity=-100, price=15.0, net_amount=1500,
                           currency='CAD', account='Margin'),
        ]
        # Sheltered trade in a DIFFERENT currency on the same symbol,
        # CHRONOLOGICALLY FIRST so it would stamp the pool currency
        # under the bug. With the fix the gate skips it.
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           time='09:00:00', symbol='X',
                           quantity=50, price=10.0, net_amount=500,
                           currency='USD', account='IRA'),
        ]
        # Must not raise the currency-mismatch ValueError.
        result = rules.compute_gains(taxable,
                                     sheltered_transactions=sheltered)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        self.assertEqual(len(gains), 1)
        # Gain on the taxable round-trip: 100 × ($15 − $10) = $500.
        # Confirms the taxable pool wasn't polluted by the sheltered
        # USD tx — the buy still establishes a $10/share CAD basis.
        self.assertAlmostEqual(gains[0]['gain'], 500.0, places=2)


if __name__ == '__main__':
    unittest.main()

"""Tests for the --affiliated flag (additive affiliated-persons input).

Mirrors --sheltered, but tagged as 'affiliated' in the wash-window output
and wash_trigger / wash_replacements records so the user can see whether
a wash sale was triggered by their own RRSP/TFSA (sheltered) or by a
spouse / related party (affiliated). The deferred-loss math is the same
from the taxable user's perspective — the loss is disallowed in both
cases — but the basis bump lands on different property.
"""

import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


class TestAffiliatedFlagCanada(unittest.TestCase):
    """A spouse's buy within ±30 days of your loss must trigger the
    superficial-loss disallowance even when your RRSP/TFSA didn't."""

    def test_affiliated_trades_do_not_pollute_acb_pool(self):
        """Regression: the Canada main loop only gated pool mutations on
        is_sheltered, not is_affiliated, so an affiliated BUY/SELL/SPLIT/
        ADJUST added to qty / total_cost / last_acq_date for the user's
        ACB pool. The fix gates on is_other_scope (sheltered OR
        affiliated). Affiliated property belongs on the other person's
        return — it must not move the user's average cost."""
        rules = CanadaTaxRules()
        taxable = [
            # Your BUY: pool qty=100, cost=$1000, avg=$10/share.
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, price=10.0, net_amount=1000.0,
                           currency='CAD', account='Margin'),
            # Your SELL: 100 × ($15 - $10) = $500 gain.
            TaxTransaction(action='BUYSELL', date='2025-06-20', symbol='X',
                           quantity=-100, price=15.0, net_amount=1500.0,
                           currency='CAD', account='Margin'),
        ]
        # Spouse buys 50 at $20 between your trades. With the bug the
        # pool became qty=150, cost=$2000 — pushing avg to $13.33 and
        # dropping your reported gain to ~$166.67.
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-03-01', symbol='X',
                           quantity=50, price=20.0, net_amount=1000.0,
                           currency='CAD', account='Spouse'),
        ]
        result = rules.compute_gains(taxable,
                                     affiliated_transactions=affiliated)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 500.0, places=2,
                               msg="Affiliated BUY must not affect the "
                                   "user's ACB — gain stays at qty × "
                                   "(price − own_avg_cost).")

    def test_spouse_buy_triggers_wash_sale(self):
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='SHOP.TO',
                           quantity=100, net_amount=10000, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='SHOP.TO',
                           quantity=-100, net_amount=8000, currency='CAD',
                           account='Margin'),
        ]
        # Spouse's account — feeds wash detection.
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='SHOP.TO',
                           quantity=100, net_amount=8500, currency='CAD',
                           account='Spouse-Margin'),
        ]
        result = rules.compute_gains(taxable, affiliated_transactions=affiliated)
        # Loss should be disallowed.
        loss = next(g for g in result['transactions'] if g.get('is_wash_sale'))
        self.assertAlmostEqual(loss['disallowed_amount'], 2000.0, places=2)

    def test_wash_trigger_marks_trigger_affiliated(self):
        """The wash_trigger record carries trigger_affiliated=True so the
        renderer can show [affiliated] vs [sheltered]."""
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=8000, currency='CAD',
                           account='Margin'),
        ]
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=100, net_amount=8500, currency='CAD',
                           account='Spouse'),
        ]
        result = rules.compute_gains(taxable, affiliated_transactions=affiliated)
        loss = next(g for g in result['transactions'] if g.get('is_wash_sale'))
        wt = loss['wash_trigger']
        self.assertTrue(wt['trigger_affiliated'])
        self.assertFalse(wt['trigger_sheltered'])
        self.assertEqual(wt['trigger_account'], 'Spouse')

    def test_wash_window_lists_affiliated_trade_with_tag(self):
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=8000, currency='CAD',
                           account='Margin'),
        ]
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=100, net_amount=8500, currency='CAD',
                           account='Spouse'),
        ]
        result = rules.compute_gains(taxable, affiliated_transactions=affiliated)
        loss = next(g for g in result['transactions'] if g.get('is_wash_sale'))
        ww = loss['wash_window']
        # Every same-symbol event in the window appears with its affiliated tag.
        spouse_row = next(t for t in ww['transactions'] if t['account'] == 'Spouse')
        self.assertTrue(spouse_row['affiliated'])
        self.assertFalse(spouse_row['sheltered'])

    def test_omitting_affiliated_is_no_op(self):
        """Confirm the flag is purely additive — omitting it changes
        nothing compared to baseline."""
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100, net_amount=11000, currency='CAD'),
        ]
        without = rules.compute_gains(taxable)
        with_empty = rules.compute_gains(taxable, affiliated_transactions=[])
        self.assertEqual(
            without['summary']['total_gain'],
            with_empty['summary']['total_gain'],
        )

    def test_affiliated_and_sheltered_together(self):
        """Both flags passed simultaneously: trades from each pool feed
        detection, tagged distinctly in the window output."""
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=8000, currency='CAD',
                           account='Margin'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-02-20', symbol='X',
                           quantity=50, net_amount=4250, currency='CAD',
                           account='RRSP'),
        ]
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-22', symbol='X',
                           quantity=50, net_amount=4300, currency='CAD',
                           account='Spouse'),
        ]
        result = rules.compute_gains(taxable,
                                      sheltered_transactions=sheltered,
                                      affiliated_transactions=affiliated)
        loss = next(g for g in result['transactions'] if g.get('is_wash_sale'))
        ww = loss['wash_window']
        # The RRSP row tags sheltered, the Spouse row tags affiliated.
        rrsp = next(t for t in ww['transactions'] if t['account'] == 'RRSP')
        spouse = next(t for t in ww['transactions'] if t['account'] == 'Spouse')
        self.assertTrue(rrsp['sheltered'])
        self.assertFalse(rrsp['affiliated'])
        self.assertTrue(spouse['affiliated'])
        self.assertFalse(spouse['sheltered'])


class TestAffiliatedFlagUSA(unittest.TestCase):
    """US engine accepts affiliated_transactions too. Their buys/sells
    appear in the wash replacements list with is_affiliated=True."""

    def test_us_long_wash_with_affiliated_replacement(self):
        rules = USATaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='AAPL',
                           quantity=100, net_amount=10000, currency='USD',
                           account='Brokerage'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='AAPL',
                           quantity=-100, net_amount=9000, currency='USD',
                           account='Brokerage'),
        ]
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='AAPL',
                           quantity=100, net_amount=9500, currency='USD',
                           account='Spouse'),
        ]
        result = rules.compute_gains(taxable, affiliated_transactions=affiliated)
        loss = next(g for g in result['transactions'] if g.get('is_wash_sale'))
        # The replacement is the spouse's buy — flagged is_affiliated.
        rep = loss['wash_replacements'][0]
        self.assertTrue(rep['is_affiliated'])
        self.assertFalse(rep['is_sheltered'])
        self.assertEqual(rep['account'], 'Spouse')


if __name__ == '__main__':
    unittest.main()

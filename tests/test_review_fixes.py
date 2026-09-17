"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.
"""

import io
import sys
import unittest
from contextlib import redirect_stderr

from taxjson.lib.core import TaxTransaction, CanadaTaxRules, USATaxRules


class TestC5ShelteredPoolSemantics(unittest.TestCase):
    """C5: sheltered shares must not enter the taxable ACB pool's qty.

    Before the fix, sheltered shares inflated pool['qty'] which diluted the
    average cost (total_cost / pool_qty) used to compute the cost basis on
    a taxable sell.
    """

    def test_sheltered_shares_do_not_dilute_taxable_acb(self):
        rules = CanadaTaxRules()
        # 50 taxable shares at $100 each.
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=50.0, net_amount=5000.0, currency='CAD', id='taxable_buy'),
            TaxTransaction(action='BUYSELL', date='2025-02-01', symbol='X', quantity=-30.0, net_amount=3300.0, currency='CAD', id='taxable_sell'),
        ]
        # 100 sheltered shares (RRSP) at $100 each. These should NOT contribute
        # to the taxable ACB pool's average-cost denominator.
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=100.0, net_amount=10000.0, currency='CAD', id='shelt_buy'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)

        sells = [g for g in result['transactions'] if g.get('id') == 'taxable_sell']
        self.assertEqual(len(sells), 1)
        # ACB per share is $100 (taxable cost basis) regardless of sheltered.
        # Selling 30 shares at $110 → cost 3000, proceeds 3300, gain $300.
        self.assertAlmostEqual(sells[0]['cost'], 3000.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], 300.0, places=2)


class TestC6CurrencyMixGuard(unittest.TestCase):
    """C6: pooling the same symbol across two currencies must error loudly."""

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


class TestC7USWashSaleBasisAdjustment(unittest.TestCase):
    """C7: disallowed loss must transfer to replacement-lot basis (IRC §1091)."""

    def test_full_basis_transfer_then_sell_replacement(self):
        rules = USATaxRules()
        # Buy 100 @ 100, Sell 100 @ 80 (loss 2000), Buy 100 @ 85 within 30 days,
        # later Sell 100 @ 110.
        # Without wash adjustment: sell #1 = -2000, sell #2 = +2500. Net = +500.
        # With wash adjustment: sell #1 disallowed (gain 0), basis on lot 2
        # bumped from 8500 to 10500. sell #2 = 11000-10500 = +500. Net = +500.
        # The TOTAL is unchanged but the DISTRIBUTION differs and the eventual
        # taxable amount must reflect the basis bump.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1_loss'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X', quantity=100.0, net_amount=8500.0, currency='USD', id='b2_replacement'),
            TaxTransaction(action='BUYSELL', date='2025-06-01', symbol='X', quantity=-100.0, net_amount=11000.0, currency='USD', id='s2'),
        ]
        result = rules.compute_gains(txs)
        sells = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND' and g.get('direction') == 'LONG']
        # Loss sale: loss disallowed, gain reported as 0
        loss_sale = next(g for g in sells if g['id'] == 's1_loss')
        self.assertTrue(loss_sale['is_wash_sale'])
        self.assertAlmostEqual(loss_sale['gain'], 0.0, places=2)
        self.assertAlmostEqual(loss_sale['disallowed_amount'], 2000.0, places=2)
        # Replacement sale: cost basis bumped from 8500 to 10500,
        # so gain = 11000 - 10500 = 500 (NOT 11000 - 8500 = 2500).
        replacement_sale = next(g for g in sells if g['id'] == 's2')
        self.assertAlmostEqual(replacement_sale['cost'], 10500.0, places=2)
        self.assertAlmostEqual(replacement_sale['gain'], 500.0, places=2)
        # And the total taxable across both = 0 + 500 = 500
        self.assertAlmostEqual(sum(g['gain'] for g in sells), 500.0, places=2)

    def test_partial_replacement_partial_disallowance(self):
        rules = USATaxRules()
        # Sell 100 at a loss, but only buy back 30 within 30 days.
        # 30 shares' worth of loss is disallowed; 70 shares' worth remains real.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1_loss'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X', quantity=30.0, net_amount=2400.0, currency='USD', id='b2_partial'),
        ]
        result = rules.compute_gains(txs)
        loss_sale = next(g for g in result['transactions'] if g.get('id') == 's1_loss')
        # Loss per share = 20. 30 shares disallowed = 600. 70 shares allowed = -1400.
        self.assertAlmostEqual(loss_sale['disallowed_amount'], 600.0, places=2)
        self.assertAlmostEqual(loss_sale['gain'], -1400.0, places=2)
        self.assertAlmostEqual(loss_sale['raw_gain'], -2000.0, places=2)

    def test_sheltered_replacement_permanent_disallowance(self):
        """Rev. Rul. 2008-5: replacement in IRA → loss is permanently denied."""
        rules = USATaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1_loss'),
        ]
        # Replacement is in the sheltered (IRA) account
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X', quantity=100.0, net_amount=8500.0, currency='USD', id='ira_buy'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)
        loss_sale = next(g for g in result['transactions'] if g.get('id') == 's1_loss')
        self.assertTrue(loss_sale['is_wash_sale'])
        self.assertAlmostEqual(loss_sale['disallowed_amount'], 2000.0, places=2)
        self.assertAlmostEqual(loss_sale['permanently_disallowed'], 2000.0, places=2)
        # Allowed gain on the loss sell is 0 (disallowed disappears entirely).
        self.assertAlmostEqual(loss_sale['gain'], 0.0, places=2)


class TestUSHoldingPeriod(unittest.TestCase):
    """ST/LT classification: 'more than one year' via anniversary, not 365 days."""

    def test_one_year_exact_is_short_term(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            # Sold exactly one year later → still ST per IRS (more than one year required)
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X', quantity=-100.0, net_amount=11000.0, currency='USD', id='s1'),
        ]
        result = rules.compute_gains(txs)
        sale = next(g for g in result['transactions'] if g.get('id') == 's1')
        self.assertEqual(sale['term'], 'SHORT_TERM')

    def test_one_year_plus_one_day_is_long_term(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-03-16', symbol='X', quantity=-100.0, net_amount=11000.0, currency='USD', id='s1'),
        ]
        result = rules.compute_gains(txs)
        sale = next(g for g in result['transactions'] if g.get('id') == 's1')
        self.assertEqual(sale['term'], 'LONG_TERM')


class TestI4LastAcqDateResetOnCrossZero(unittest.TestCase):
    """I4: when a sell crosses zero, leftover is a fresh position; days_held resets."""

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


class TestTaxDateBasis(unittest.TestCase):
    """The --tax-date flag selects trade vs settlement date for year filtering.

    Trades that close on Dec 30/31 and settle on Jan 1/2 are the canonical
    case where this matters. tt uses settle date; taxjson defaults to trade
    date but can be switched for cross-tool comparison.
    """

    def _run_gains(self, txs, year, tax_date):
        import json
        import subprocess
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'in.json'
            input_path.write_text(json.dumps({
                'transactions': [
                    {k: v for k, v in t.__dict__.items()} for t in txs
                ]
            }))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_gains',
                 '--country', 'canada',
                 '--year', str(year),
                 '--tax-date', tax_date,
                 str(input_path)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

    def test_dec_close_settling_in_january(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-12-20', date_settle='2025-12-22',
                           symbol='ABC.US', quantity=100.0, currency='USD',
                           net_amount=10000.0, id='b1'),
            # Closing trade: trade Dec 31, settle Jan 1, 2026.
            TaxTransaction(action='BUYSELL', date='2025-12-31', date_settle='2026-01-01',
                           symbol='ABC.US', quantity=-100.0, currency='USD',
                           net_amount=11500.0, id='s1'),
        ]
        # Default (trade-date): close falls in 2025.
        r_trade = self._run_gains(txs, 2025, 'trade')
        self.assertEqual(len(r_trade['transactions']), 1)
        self.assertAlmostEqual(r_trade['summary']['total_gain'], 1500.0)
        self.assertEqual(r_trade['summary']['tax_date_basis'], 'trade')
        # Settle-date: close falls in 2026, so 2025 is empty.
        r_settle = self._run_gains(txs, 2025, 'settle')
        self.assertEqual(len(r_settle['transactions']), 0)
        self.assertAlmostEqual(r_settle['summary']['total_gain'], 0.0)
        self.assertEqual(r_settle['summary']['tax_date_basis'], 'settle')
        # And settle-date 2026 picks up the same gain.
        r_settle_2026 = self._run_gains(txs, 2026, 'settle')
        self.assertEqual(len(r_settle_2026['transactions']), 1)
        self.assertAlmostEqual(r_settle_2026['summary']['total_gain'], 1500.0)


class TestAssignmentEdgeCases(unittest.TestCase):
    """ASSIGN-action handling edge cases discovered during tt comparison."""

    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_stock_leg_assign_emits_gain(self):
        """Stock leg of an option assignment recorded as ASSIGN must still
        produce a realized gain entry. tt_gains.pl previously rolled the gain
        into pending_adjustments and orphaned it; taxjson must not regress."""
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='AAPL.US',
                           quantity=100.0, currency='USD', net_amount=15000.0, id='b'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL250620C00200000.US',
                           quantity=-1.0, currency='USD', net_amount=500.0, id='sto'),
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           symbol='AAPL250620C00200000.US',
                           quantity=1.0, currency='USD', net_amount=0.0, id='opt_assign'),
            # Stock-leg recorded as ASSIGN (taxjson IB extractor convention).
            TaxTransaction(action='ASSIGN', date='2025-06-20', time='16:00:01',
                           symbol='AAPL.US',
                           quantity=-100.0, currency='USD', net_amount=20000.0, id='stk_assign'),
        ]
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND' and g.get('symbol') == 'AAPL.US']
        self.assertEqual(len(sells), 1, "stock-leg ASSIGN must emit a gain entry")
        # Cost = 15000, proceeds = 20000 + 500 (rolled premium) = 20500, gain = 5500.
        self.assertAlmostEqual(sells[0]['cost'], 15000.0, places=2)
        self.assertAlmostEqual(sells[0]['proceeds'], 20500.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], 5500.0, places=2)
        # No option-leg gain entry should appear.
        opt_entries = [g for g in result['transactions']
                       if g.get('symbol', '').startswith('AAPL250620C')]
        self.assertEqual(len(opt_entries), 0)

    def test_cross_zero_assign_apportions_premium_to_leftover(self):
        """If a stock ASSIGN crosses inventory zero (e.g. assigned on more
        shares than held), the rolled-in premium must be apportioned to the
        new opposite-direction position too — not just consumed by the close.

        Hold 50 stock @ $100, sell 1 call (premium $500), assigned on the
        full 100-share contract (over-sells by 50 shares).
        After close: realize 50 closed shares + premium share apportioned to
        them. After leftover open: 50-share short carrying the remaining
        premium share into its cost basis.
        """
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='AAPL.US',
                           quantity=50.0, currency='USD', net_amount=5000.0, id='b'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL250620C00120000.US',
                           quantity=-1.0, currency='USD', net_amount=500.0, id='sto'),
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           symbol='AAPL250620C00120000.US',
                           quantity=1.0, currency='USD', net_amount=0.0, id='opt_assign'),
            # 100-share assignment crosses zero (held 50, sells 100 → -50 leftover).
            TaxTransaction(action='ASSIGN', date='2025-06-20', time='16:00:01',
                           symbol='AAPL.US',
                           quantity=-100.0, currency='USD', net_amount=12000.0, id='stk_assign'),
        ]
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND' and g.get('symbol') == 'AAPL.US']
        self.assertEqual(len(sells), 1)
        # Closing 50 shares: cost=5000 (full lot), proceeds = 6000 + 250
        # (half of the $500 premium apportioned) = 6250, gain = 1250.
        self.assertAlmostEqual(sells[0]['cost'], 5000.0, places=2)
        self.assertAlmostEqual(sells[0]['proceeds'], 6250.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], 1250.0, places=2)
        # Leftover -50-share short carries the other half of the premium.
        # On a short open, the premium adds to the proceeds-equivalent cost
        # basis (raising the breakeven cover price): half stock proceeds +
        # half premium = 6000 + 250 = 6250. The remaining premium dollars
        # will be realized when the short is later covered.
        inv = {i['symbol']: i for i in result['inventory']}
        self.assertAlmostEqual(inv['AAPL.US']['qty'], -50.0)
        # NEGATIVE per the repo-wide short-inventory convention (proceeds
        # credited; FUZZ #21 aligned the Canada engine's emission with
        # the USA engine and the holdings export): the 6250 basis is a
        # credit, and cost_per_share = total_cost/qty = +125/share.
        self.assertAlmostEqual(inv['AAPL.US']['total_cost'], -6250.0, places=2)


class TestShortPositionSignedCashFlow(unittest.TestCase):
    """SHORT direction gain entries must use signed cash-flow convention.

    A sell-to-open is a cash inflow (negative cost basis) and a buy-to-close
    is a cash outflow (negative proceeds). This matches tt_gains.pl:401-405
    so that downstream summaries compute `proceeds - cost = signed gain`
    correctly without direction-aware logic, and lets taxjson_ccd_gains.py
    filter shorts by `cost < 0` the same way tt_ccd_gains.pl does.
    """

    def test_short_put_cycle_emits_negative_cost_and_proceeds(self):
        rules = CanadaTaxRules()
        # Sell-to-open 5 puts at premium 1.31651 → received 651.35 net.
        # Buy-to-close 5 puts at premium 0.30743 → paid 160.65 net.
        # Profit (cost - proceeds for shorts) = 490.71.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-04-25',
                           symbol='AA250620P00022500.US', quantity=-5.0,
                           currency='CAD', net_amount=651.353716, id='sto'),
            TaxTransaction(action='BUYSELL', date='2025-05-13',
                           symbol='AA250620P00022500.US', quantity=5.0,
                           currency='CAD', net_amount=160.645104, id='btc'),
        ]
        result = rules.compute_gains(txs)
        self.assertEqual(len(result['transactions']), 1)
        entry = result['transactions'][0]
        self.assertEqual(entry['direction'], 'SHORT')
        # Signed convention: cost reflects cash inflow (negative), proceeds
        # reflects cash outflow (negative).
        self.assertAlmostEqual(entry['cost'], -651.353716, places=4)
        self.assertAlmostEqual(entry['proceeds'], -160.645104, places=4)
        # Gain remains positive (winning short).
        self.assertAlmostEqual(entry['gain'], 490.708612, places=4)
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


class TestDividendGrossReporting(unittest.TestCase):
    """Dividends should be reported gross of foreign withholding tax.

    Brokerage adapters (e.g. RBC) populate gross_amount with the pre-withhold
    figure and emit a separate TAX record for the withholding. The gains
    engine should pass the gross figure through to the dividend total so it
    matches T5/T3/1099-DIV box 1a; the TAX record continues to feed the
    foreign tax credit independently.
    """

    def test_canada_dividend_uses_gross_when_present(self):
        rules = CanadaTaxRules()
        # 100 USD dividend, 15 USD withheld → net 85, gross 100.
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-31', symbol='AAPL.US',
                           currency='USD', net_amount=85.0, gross_amount=100.0,
                           type='dividend', id='div1'),
            TaxTransaction(action='TAX', date='2025-03-31', symbol='AAPL.US',
                           currency='USD', net_amount=15.0, type='tax', id='tax1'),
        ]
        result = rules.compute_gains(txs)
        div_entries = [t for t in result['transactions'] if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(div_entries), 1)
        self.assertAlmostEqual(div_entries[0]['dividend'], 100.0,
                               msg="dividend total must use gross_amount, not net_amount")
        self.assertAlmostEqual(result['by_ticker']['AAPL.US']['total_div'], 100.0)

    def test_canada_dividend_falls_back_to_net_when_gross_missing(self):
        """For older data lacking gross_amount, net_amount is used as-is."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-31', symbol='TD.TO',
                           currency='CAD', net_amount=42.0,  # gross_amount defaults to 0
                           type='dividend', id='div1'),
        ]
        result = rules.compute_gains(txs)
        div_entries = [t for t in result['transactions'] if t.get('action') == 'DIVIDEND']
        self.assertAlmostEqual(div_entries[0]['dividend'], 42.0)

    def test_usa_dividend_uses_gross_when_present(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-31', symbol='AAPL',
                           currency='USD', net_amount=85.0, gross_amount=100.0,
                           type='dividend', id='div1'),
        ]
        result = rules.compute_gains(txs)
        div_entries = [t for t in result['transactions'] if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(div_entries), 1)
        self.assertAlmostEqual(div_entries[0]['dividend'], 100.0)


class TestInvariantWarning(unittest.TestCase):
    """The disallowance invariant should be silent on healthy data."""

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

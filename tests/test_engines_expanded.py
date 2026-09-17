"""Expanded engine coverage for both USATaxRules and CanadaTaxRules.

Existing tests cover the happy paths (basic buy/sell, simple wash sale,
option assignment). These add:

USA:
  - Long-term vs short-term anniversary boundary (§1223 + holding period)
  - FIFO order across multiple lots
  - §1091 wash sale with partial coverage
  - §1091 wash sale with sheltered replacement (Rev. Rul. 2008-5 permanent)
  - §1223(3) holding-period inheritance across a wash sale
  - Mixed long + short on the same symbol (no §1233(b)(1) detection yet —
    so they're independent)
  - Trace strings are emitted when trace=True

Canada:
  - ACB pooling across multiple buys at different prices
  - ACB pool resets to zero on full close
  - SPLIT action multiplies pool qty and acb/share
  - ASSIGN action rolling option premium into underlying ACB
  - ADJUST action adding to pool cost
  - DIVIDEND aggregation
  - Wash sale across taxable + sheltered accounts
  - Short-position ACB with negative qty
  - --no-wash leaves the loss intact
"""

import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


# ============================================================================
# USA engine — expanded coverage
# ============================================================================
class TestUSAHoldingPeriod(unittest.TestCase):
    """Holding period boundaries under §1222 / §1223."""

    def _gain_of(self, acq_date, disp_date):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date=acq_date, symbol='AAPL',
                           quantity=100.0, net_amount=10000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date=disp_date, symbol='AAPL',
                           quantity=-100.0, net_amount=11000.0, currency='USD'),
        ]
        return rules.compute_gains(txs)['transactions'][0]

    def test_held_exactly_one_year_is_short_term(self):
        """Anniversary day — disp == acq + 1 year — is still SHORT_TERM."""
        g = self._gain_of('2024-03-15', '2025-03-15')
        self.assertEqual(g['term'], 'SHORT_TERM')

    def test_held_one_year_plus_one_day_is_long_term(self):
        g = self._gain_of('2024-03-15', '2025-03-16')
        self.assertEqual(g['term'], 'LONG_TERM')

    def test_end_of_month_acquisition_rev_rul_66_7(self):
        """Rev. Rul. 66-7: property acquired on the LAST day of a month
        starts its holding period on the 1st of the next month and is
        held more than one year on the 1st of that month a year later.
        Feb 29, 2024 -> LT from Mar 1, 2025 (not Mar 2); Feb 28, 2023
        (last day of a common-year February) -> LT from Mar 1, 2024, so
        a Feb 29, 2024 sale is still SHORT_TERM. The prior pin encoded
        the opposite (2026-09 US-engine audit)."""
        rules = USATaxRules()

        def term(acq, disp):
            return rules.compute_gains([
                TaxTransaction(action='BUYSELL', date=acq, symbol='AAPL',
                               quantity=1.0, net_amount=100.0,
                               currency='USD'),
                TaxTransaction(action='BUYSELL', date=disp, symbol='AAPL',
                               quantity=-1.0, net_amount=110.0,
                               currency='USD'),
            ])['transactions'][0]['term']

        self.assertEqual(term('2024-02-29', '2025-02-28'), 'SHORT_TERM')
        self.assertEqual(term('2024-02-29', '2025-03-01'), 'LONG_TERM')
        self.assertEqual(term('2023-02-28', '2024-02-29'), 'SHORT_TERM')
        self.assertEqual(term('2023-02-28', '2024-03-01'), 'LONG_TERM')
        # Non-month-end acquisitions keep the plain anniversary rule.
        self.assertEqual(term('2024-06-30', '2025-06-30'), 'SHORT_TERM')
        self.assertEqual(term('2024-06-30', '2025-07-01'), 'LONG_TERM')
        self.assertEqual(term('2024-06-15', '2025-06-15'), 'SHORT_TERM')
        self.assertEqual(term('2024-06-15', '2025-06-16'), 'LONG_TERM')
    def test_fifo_consumes_earliest_lot_first(self):
        """Buy 10 @ $100 (Jan), buy 10 @ $200 (Feb), sell 10 (Mar). The Jan
        lot is consumed → gain = proceeds - 1000, not - 2000."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=10.0, net_amount=1000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=10.0, net_amount=2000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-10.0, net_amount=1500.0, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['cost'], 1000.0, places=2)
        self.assertAlmostEqual(gains[0]['proceeds'], 1500.0, places=2)
        self.assertAlmostEqual(gains[0]['raw_gain'], 500.0, places=2)
        # Inventory still holds the Feb lot.
        inv = [i for i in result['inventory'] if i['symbol'] == 'X']
        self.assertEqual(len(inv), 1)
        self.assertAlmostEqual(inv[0]['qty'], 10.0)
        self.assertAlmostEqual(inv[0]['total_cost'], 2000.0)


class TestUSAWashSalePartialCoverage(unittest.TestCase):
    """Replacement BUY smaller than the loss qty → only partial disallowance."""

    def test_partial_coverage_leaves_unmatched_loss(self):
        """Sell 100 at a $500 loss; replacement buy of only 30 shares within
        30 days → $150 of the loss is disallowed (30/100 * 500), $350 remains
        a real loss."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100.0, net_amount=9500.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=30.0, net_amount=2800.0, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        g = gains[0]
        self.assertAlmostEqual(g['raw_gain'], -500.0, places=2)
        self.assertAlmostEqual(g['disallowed_amount'], 150.0, places=2)
        # Allowed loss = raw + disallowed_added_back = -500 + 150 = -350
        self.assertAlmostEqual(g['gain'], -350.0, places=2)


class TestUSAShelteredReplacement(unittest.TestCase):
    """Rev. Rul. 2008-5: replacement bought in an IRA = permanent disallowance."""

    def test_sheltered_replacement_is_permanent(self):
        rules = USATaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='USD',
                           account='Taxable'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100.0, net_amount=9000.0, currency='USD',
                           account='Taxable'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=100.0, net_amount=9500.0, currency='USD',
                           account='IRA'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        g = gains[0]
        self.assertAlmostEqual(g['raw_gain'], -1000.0, places=2)
        self.assertAlmostEqual(g['permanently_disallowed'], 1000.0, places=2)
        # Gain = raw + perm_disallowed = -1000 + 1000 = 0
        self.assertAlmostEqual(g['gain'], 0.0, places=2)
        # The replacement IS in the wash_replacements list, marked sheltered.
        self.assertTrue(any(r['is_sheltered'] for r in g.get('wash_replacements', [])))


class TestUSAHoldingPeriodInheritance(unittest.TestCase):
    """§1223(3): wash-sale replacement inherits the loss lot's holding period."""

    def test_replacement_inherits_acq_date_for_long_term_eligibility(self):
        """Buy in 2024-02 (held ~13 months), sell in 2025-03 at a loss,
        replacement bought 2025-03-25, sold 2025-04-15.
        Without §1223(3): the 2025-03-25 replacement is held only 21 days → ST.
        With §1223(3): replacement inherits 2024-02 acq → held >1yr → LT.
        """
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-02-10', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-03-10', symbol='X',
                           quantity=-100.0, net_amount=9000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-03-25', symbol='X',
                           quantity=100.0, net_amount=9500.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-04-15', symbol='X',
                           quantity=-100.0, net_amount=11000.0, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 2)
        # First sell: $1000 loss disallowed (wash with the Mar 25 buy).
        self.assertAlmostEqual(gains[0]['raw_gain'], -1000.0, places=2)
        self.assertAlmostEqual(gains[0]['disallowed_amount'], 1000.0, places=2)
        # Second sell: classification must be LONG_TERM via §1223(3) carry-back.
        self.assertEqual(gains[1]['term'], 'LONG_TERM')


class TestUSAMixedLongShortSameSymbol(unittest.TestCase):
    """Long position closed at gain, then short opened on the same symbol —
    they're independent (no §1233 cross-position detection in v1)."""

    def test_independent_long_then_short(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=10.0, net_amount=1000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-10.0, net_amount=1200.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-04-15', symbol='X',
                           quantity=-10.0, net_amount=1500.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='X',
                           quantity=10.0, net_amount=1300.0, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        # Two independent closes: long at +200 gain, short at +200 gain.
        self.assertEqual(len(gains), 2)
        long_close = next(g for g in gains if g['direction'] == 'LONG')
        short_close = next(g for g in gains if g['direction'] == 'SHORT')
        self.assertAlmostEqual(long_close['raw_gain'], 200.0, places=2)
        self.assertAlmostEqual(short_close['raw_gain'], 200.0, places=2)


class TestUSADividendsAlongsideTrades(unittest.TestCase):
    """Dividend records flow through with currency and pass invariant checks."""

    def test_dividend_in_results(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='USD'),
            TaxTransaction(action='DIVIDEND', date='2025-03-15', symbol='X',
                           quantity=0.0, net_amount=85.0, gross_amount=100.0,
                           currency='USD', type='dividend'),
        ]
        result = rules.compute_gains(txs)
        divs = [g for g in result['transactions'] if g.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        self.assertAlmostEqual(divs[0]['dividend'], 100.0)  # gross
        self.assertEqual(divs[0]['currency'], 'USD')


class TestUSAOptionAssignment(unittest.TestCase):
    """Option premium on ASSIGN rolls into the underlying's basis/proceeds
    rather than being recognized as a separate option gain (IRS Pub 550).

    This is the same convention the Canada engine uses (the option ASSIGN
    rolls into pending_adjustments on the underlying); we mirror it in
    the US engine so a short put assignment doesn't emit a phantom option
    gain.
    """

    def test_short_put_assignment_reduces_stock_basis(self):
        """Sell-to-open a $30 put for $1000 premium, get assigned 100 shares.
        Stock basis should be $3000 (strike) − $1000 (premium) = $2000."""
        rules = USATaxRules()
        txs = [
            # Sell-to-open the $30 put: receive $1000 premium
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL250620P00030000', quantity=-1.0,
                           net_amount=1000.00, currency='USD'),
            # Assignment: option closes at $0, stock bought at strike
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           symbol='AAPL250620P00030000', quantity=1.0,
                           net_amount=0.00, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-06-20',
                           symbol='AAPL', quantity=100.0,
                           net_amount=3000.00, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        # No gain emitted for the option leg.
        opt_gains = [g for g in result['transactions']
                     if g.get('symbol') == 'AAPL250620P00030000']
        self.assertEqual(len(opt_gains), 0,
                         "Option ASSIGN must not emit a separate gain entry — "
                         "premium rolls into underlying basis (Pub 550).")
        # The stock should be in inventory with basis = strike - premium.
        inv = next(i for i in result['inventory'] if i['symbol'] == 'AAPL')
        self.assertAlmostEqual(inv['qty'], 100.0)
        self.assertAlmostEqual(inv['total_cost'], 2000.0, places=2,
                               msg="Stock cost basis should be strike ($3000) "
                                   "− premium ($1000) = $2000.")

    def test_short_call_assignment_increases_stock_proceeds(self):
        """Own 100 AAPL @ $100 basis. Sell-to-open a $120 call for $500
        premium. Get assigned. Stock proceeds should be $12000 (strike)
        + $500 (premium) = $12500, gain = $12500 − $10000 = $2500."""
        rules = USATaxRules()
        txs = [
            # Acquire 100 AAPL at $100 basis (Feb 2024, > 1 year held by Mar 2025)
            TaxTransaction(action='BUYSELL', date='2024-02-10', symbol='AAPL',
                           quantity=100.0, net_amount=10000.00, currency='USD'),
            # Sell-to-open the $120 call: receive $500 premium
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL250620C00120000', quantity=-1.0,
                           net_amount=500.00, currency='USD'),
            # Assignment: option closes at $0, stock sold at strike
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           symbol='AAPL250620C00120000', quantity=1.0,
                           net_amount=0.00, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-06-20', symbol='AAPL',
                           quantity=-100.0, net_amount=12000.00, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        opt_gains = [g for g in result['transactions']
                     if g.get('symbol') == 'AAPL250620C00120000']
        self.assertEqual(len(opt_gains), 0)
        stock_gains = [g for g in result['transactions']
                       if g.get('symbol') == 'AAPL' and g.get('action') != 'DIVIDEND']
        self.assertEqual(len(stock_gains), 1)
        g = stock_gains[0]
        # Proceeds = strike + premium = 12000 + 500 = 12500
        self.assertAlmostEqual(g['proceeds'], 12500.0, places=2)
        # Gain = 12500 - 10000 = 2500
        self.assertAlmostEqual(g['gain'], 2500.0, places=2)
        # Long-term because the underlying was held > 1 year.
        self.assertEqual(g['term'], 'LONG_TERM')


class TestUSATraceEmission(unittest.TestCase):
    """When trace=True, each gain entry carries a populated trace list."""

    def test_trace_strings_present(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100.0, net_amount=11000.0, currency='USD'),
        ]
        result = rules.compute_gains(txs, trace=True)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertTrue(gains[0]['trace'])
        self.assertTrue(any('FIFO CALCULATION TRACE' in line for line in gains[0]['trace']))


# ============================================================================
# Canada engine — expanded coverage
# ============================================================================
class TestCanadaACBPooling(unittest.TestCase):
    """ACB pools multiple buys at the average cost."""

    def test_two_buys_average(self):
        """Buy 100 @ $50, buy 100 @ $60, sell 100 @ $70 → ACB/share = 55,
        gain = 100 * (70 - 55) = 1500."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=5000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=100.0, net_amount=6000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100.0, net_amount=7000.0, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        # cost basis = (5000+6000)/200 * 100 = 5500
        self.assertAlmostEqual(gains[0]['cost'], 5500.0, places=2)
        self.assertAlmostEqual(gains[0]['gain'], 1500.0, places=2)


class TestCanadaPoolResetAtZero(unittest.TestCase):
    """When position closes to zero, the pool's cost basis resets so a future
    buy doesn't carry stale ACB."""

    def test_full_close_then_new_buy_uses_new_cost(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=5000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100.0, net_amount=6000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-04-15', symbol='X',
                           quantity=50.0, net_amount=4000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-05-15', symbol='X',
                           quantity=-50.0, net_amount=4500.0, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 2)
        # First gain: $1000. Second: $500. NOT $500 + carryover from the first.
        gains_sorted = sorted(gains, key=lambda g: g['date'])
        self.assertAlmostEqual(gains_sorted[0]['raw_gain'], 1000.0)
        self.assertAlmostEqual(gains_sorted[1]['raw_gain'], 500.0)


class TestCanadaSplit(unittest.TestCase):
    """SPLIT multiplies pool qty (cost basis stays the same → acb/share falls)."""

    def test_two_for_one_split(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='CAD'),
            TaxTransaction(action='SPLIT', date='2025-03-15', symbol='X',
                           quantity=2.0, currency='CAD'),
            # Now you hold 200 shares; sell 100 @ $60. ACB/share = 100/2 = 50.
            TaxTransaction(action='BUYSELL', date='2025-04-15', symbol='X',
                           quantity=-100.0, net_amount=6000.0, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        # Post-split ACB/sh = 10000/200 = 50; cost of 100 closed = 5000;
        # proceeds 6000; gain 1000.
        self.assertAlmostEqual(gains[0]['cost'], 5000.0, places=2)
        self.assertAlmostEqual(gains[0]['gain'], 1000.0, places=2)


class TestCanadaACBAdjust(unittest.TestCase):
    """ADJUST action adds to pool cost basis (e.g. reinvested distributions)."""

    def test_adjust_increases_acb(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='CAD'),
            TaxTransaction(action='ADJUST', date='2025-02-15', symbol='X',
                           net_amount=200.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100.0, net_amount=11000.0, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        # cost = 10000 + 200 = 10200; gain = 800
        self.assertAlmostEqual(gains[0]['cost'], 10200.0, places=2)
        self.assertAlmostEqual(gains[0]['gain'], 800.0, places=2)


class TestCanadaDividendAggregation(unittest.TestCase):
    """DIVIDEND records flow into by_ticker['total_div']."""

    def test_dividend_aggregation(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=5000.0, currency='CAD'),
            TaxTransaction(action='DIVIDEND', date='2025-03-15', symbol='X',
                           quantity=0.0, net_amount=42.0, gross_amount=50.0,
                           currency='CAD', type='dividend'),
            TaxTransaction(action='DIVIDEND', date='2025-06-15', symbol='X',
                           quantity=0.0, net_amount=63.0, gross_amount=75.0,
                           currency='CAD', type='dividend'),
        ]
        result = rules.compute_gains(txs)
        self.assertAlmostEqual(result['by_ticker']['X']['total_div'], 125.0)


class TestCanadaWashAcrossSheltered(unittest.TestCase):
    """A loss in the taxable account + replacement buy in TFSA/RRSP →
    superficial loss disallowed (affiliated-persons rule under ITA 54)."""

    def test_taxable_loss_tfsa_replacement(self):
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100.0, net_amount=8000.0, currency='CAD',
                           account='Margin'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=100.0, net_amount=8500.0, currency='CAD',
                           account='TFSA'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        loss = next(g for g in gains if g['is_wash_sale'])
        self.assertAlmostEqual(loss['raw_gain'], -2000.0)
        self.assertAlmostEqual(loss['disallowed_amount'], 2000.0)
        # The replacement is in the TFSA → ITA 54 disallows the loss.
        self.assertEqual(len(result['wash_sales']), 1)


class TestCanadaShortPosition(unittest.TestCase):
    """Canada handles SHORT positions via signed cash flow in the ACB pool."""

    def test_short_sell_buy_cover(self):
        """Sell-to-open 100 @ $50, buy-to-cover 100 @ $40 → gain = $1000.
        Canada's signed-cash-flow convention: cost and proceeds reported
        negative for shorts."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=-100.0, net_amount=5000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=100.0, net_amount=4000.0, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        g = gains[0]
        self.assertEqual(g['direction'], 'SHORT')
        self.assertAlmostEqual(g['gain'], 1000.0, places=2)
        # Signed cash flow: cost and proceeds negative on shorts.
        self.assertLess(g['cost'], 0)
        self.assertLess(g['proceeds'], 0)


class TestCanadaNoWashFlag(unittest.TestCase):
    """--no-wash skips superficial-loss detection for an apples-to-apples
    diff against legacy tt_gains.pl baselines."""

    def test_no_wash_leaves_loss_intact(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100.0, net_amount=8000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=100.0, net_amount=8500.0, currency='CAD'),
        ]
        result_with = rules.compute_gains(txs)
        result_without = rules.compute_gains(txs, detect_wash_sales=False)
        # With wash: loss disallowed, gain = 0.
        # Without wash: raw loss flows through as the gain.
        loss_with = next(g for g in result_with['transactions']
                         if g.get('action') != 'DIVIDEND' and g['raw_gain'] < 0)
        loss_without = next(g for g in result_without['transactions']
                            if g.get('action') != 'DIVIDEND' and g['raw_gain'] < 0)
        self.assertAlmostEqual(loss_with['gain'], 0.0, places=2)
        self.assertAlmostEqual(loss_without['gain'], -2000.0, places=2)
        self.assertEqual(len(result_with['wash_sales']), 1)
        self.assertEqual(len(result_without['wash_sales']), 0)


class TestPaymentInLieuOfDividend(unittest.TestCase):
    """DIVIDEND_IN_LIEU (Payment-in-Lieu / PIL) is ordinary income, not
    an eligible/qualified dividend. The engines must skip it from ACB and
    from the per-ticker dividend total; taxjson-sum-income routes it to
    the interest bucket. Matches the legacy tt_sum_income.pl convention."""

    def _make_txs(self):
        return [
            # Regular dividend $100 — counts as dividend income
            TaxTransaction(action='DIVIDEND', date='2025-03-15', symbol='X',
                           quantity=0.0, net_amount=100.0, gross_amount=100.0,
                           currency='USD', type='dividend'),
            # PIL $40 — should count as interest income, NOT dividend
            TaxTransaction(action='DIVIDEND_IN_LIEU', date='2025-06-15', symbol='X',
                           quantity=0.0, net_amount=40.0, gross_amount=40.0,
                           currency='USD', type='dividend_in_lieu'),
        ]

    def test_canada_skips_pil_from_dividend_gain_entries(self):
        rules = CanadaTaxRules()
        result = rules.compute_gains(self._make_txs())
        divs = [g for g in result['transactions']
                if g.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1,
                         "Only the regular DIVIDEND should produce a DIVIDEND gain entry; "
                         "PIL gets its own DIVIDEND_IN_LIEU entry so sum-gains can column it.")
        self.assertAlmostEqual(divs[0]['dividend'], 100.0)
        # PIL has its own per-ticker entry with action='DIVIDEND_IN_LIEU' and
        # the amount stored under `pil` (not `dividend`) — sum-gains uses
        # this to render a separate PIL column without inflating the T5 total.
        pils = [g for g in result['transactions']
                if g.get('action') == 'DIVIDEND_IN_LIEU']
        self.assertEqual(len(pils), 1)
        self.assertAlmostEqual(pils[0]['pil'], 40.0)
        self.assertNotIn('dividend', pils[0])

    def test_usa_skips_pil_from_dividend_gain_entries(self):
        rules = USATaxRules()
        result = rules.compute_gains(self._make_txs())
        divs = [g for g in result['transactions']
                if g.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        self.assertAlmostEqual(divs[0]['dividend'], 100.0)
        pils = [g for g in result['transactions']
                if g.get('action') == 'DIVIDEND_IN_LIEU']
        self.assertEqual(len(pils), 1)
        self.assertAlmostEqual(pils[0]['pil'], 40.0)

    def test_sum_income_routes_pil_to_pil_column(self):
        """End-to-end: feed taxjson-sum-income a transaction list with a
        regular DIVIDEND and a PIL row; check that PIL lands in its own
        per-ticker `pil` column, not in `div` (which feeds the T5 dividend
        total). Cash interest bucket stays untouched."""
        from taxjson.bin.taxjson_sum_income import summarize_income
        tx_list = [
            {'action': 'DIVIDEND', 'date': '2025-03-15', 'symbol': 'X',
             'currency': 'USD', 'gross_amount': 100.0, 'net_amount': 100.0,
             'type': 'dividend'},
            {'action': 'DIVIDEND_IN_LIEU', 'date': '2025-06-15', 'symbol': 'X',
             'currency': 'USD', 'gross_amount': 40.0, 'net_amount': 40.0,
             'type': 'dividend_in_lieu'},
        ]
        result = summarize_income(tx_list, target_year=2025)
        # Dividend bucket has only the regular dividend ($100).
        self.assertAlmostEqual(result['ticker_stats']['X']['USD']['div'], 100.0)
        # PIL ($40) landed in its own column, ticker-associated.
        self.assertAlmostEqual(result['ticker_stats']['X']['USD']['pil'], 40.0)
        # PIL no longer pollutes the cash-interest bucket.
        self.assertEqual(result['interest_totals'].get('USD', 0), 0)


class TestCanadaTraceEmission(unittest.TestCase):
    """trace=True populates trace list on each gain entry."""

    def test_canada_trace_strings(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100.0, net_amount=10000.0, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100.0, net_amount=11000.0, currency='CAD'),
        ]
        result = rules.compute_gains(txs, trace=True)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertTrue(gains[0]['trace'])
        self.assertTrue(any('ACB CALCULATION TRACE' in line for line in gains[0]['trace']))


if __name__ == '__main__':
    unittest.main()

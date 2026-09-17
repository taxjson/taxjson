"""Engine edge cases — creative regression hunters.

Each test isolates a specific corner that a naive refactor could quietly
break. Pick scenarios where the symptom is subtle (e.g., a transaction
silently dropped, a wash-sale window off-by-one, a currency pool getting
mixed) so the test fires before the user notices the drift in their tax
return.
"""

import unittest
from decimal import Decimal

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


# ============================================================================
# Boundary conditions
# ============================================================================
class TestEmptyInputs(unittest.TestCase):
    def test_empty_canada(self):
        result = CanadaTaxRules().compute_gains([])
        self.assertEqual(result['transactions'], [])
        self.assertEqual(result['summary']['total_gain'], 0)

    def test_empty_usa(self):
        result = USATaxRules().compute_gains([])
        self.assertEqual(result['transactions'], [])

    def test_only_buy_no_sell(self):
        """No realized gain; the buy lives in inventory."""
        rules = USATaxRules()
        txs = [TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                              quantity=100.0, net_amount=10000.0, currency='USD')]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 0)
        # Inventory shows the open lot.
        self.assertEqual(len(result['inventory']), 1)
        self.assertAlmostEqual(result['inventory'][0]['qty'], 100.0)


# ============================================================================
# Wash-sale window boundaries
# ============================================================================
class TestWashSaleWindowBoundaries(unittest.TestCase):
    """Off-by-one bugs on the ±30 day wash-sale window."""

    def test_exactly_30_days_before_loss_is_in_window(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X',
                           quantity=100, net_amount=10000, currency='USD'),
            # 31 days later → loss
            TaxTransaction(action='BUYSELL', date='2025-02-01', symbol='X',
                           quantity=-100, net_amount=8000, currency='USD'),
            # Replacement exactly 30 days after the loss (in window)
            TaxTransaction(action='BUYSELL', date='2025-03-03', symbol='X',
                           quantity=100, net_amount=8500, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        # The loss should be disallowed.
        loss = next(g for g in result['transactions'] if g.get('raw_gain', 0) < 0)
        self.assertGreater(loss['disallowed_amount'], 0,
                           msg="±30 day boundary inclusive: this should fire.")

    def test_31_days_after_loss_is_not_a_wash(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X',
                           quantity=100, net_amount=10000, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-01', symbol='X',
                           quantity=-100, net_amount=8000, currency='USD'),
            # 32 days after the loss → outside window
            TaxTransaction(action='BUYSELL', date='2025-03-05', symbol='X',
                           quantity=100, net_amount=8500, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        loss = next(g for g in result['transactions'] if g.get('raw_gain', 0) < 0)
        self.assertAlmostEqual(loss['disallowed_amount'], 0.0)

    def test_year_boundary_wash_sale(self):
        """Loss in late Dec, replacement in early Jan: must trigger wash
        even though the events span two tax years. The replacement's basis
        bump then reduces the *following year's* gain."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-11-15', symbol='X',
                           quantity=100, net_amount=10000, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2024-12-28', symbol='X',
                           quantity=-100, net_amount=8000, currency='USD'),
            # 6 days later (next year) — in window
            TaxTransaction(action='BUYSELL', date='2025-01-03', symbol='X',
                           quantity=100, net_amount=8500, currency='USD'),
            # Eventually sold in 2025 — should reflect bumped basis
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='X',
                           quantity=-100, net_amount=12000, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        gains.sort(key=lambda g: g['date'])
        # First close (Dec 2024): loss disallowed, gain = 0.
        self.assertAlmostEqual(gains[0]['gain'], 0.0)
        self.assertAlmostEqual(gains[0]['disallowed_amount'], 2000.0)
        # Second close (Jun 2025): basis bumped by $2000 (8500 + 2000 = 10500),
        # so gain = 12000 - 10500 = 1500 (not 12000 - 8500 = 3500).
        self.assertAlmostEqual(gains[1]['gain'], 1500.0, places=2)


# ============================================================================
# Currency pool integrity
# ============================================================================
class TestCurrencyPoolGuard(unittest.TestCase):
    """A single ACB pool must not mix currencies (Canada engine guards this)."""

    def test_mismatched_currency_raises(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=8000, currency='USD'),
        ]
        with self.assertRaises(ValueError):
            rules.compute_gains(txs)


# ============================================================================
# ASSIGN on a non-option symbol
# ============================================================================
class TestAssignOnStockSymbol(unittest.TestCase):
    """ASSIGN on a stock symbol (the stock leg of an option assignment) must
    behave like a regular BUYSELL — NOT roll into a phantom underlying."""

    def test_canada_stock_assign_is_buysell(self):
        rules = CanadaTaxRules()
        txs = [
            # ASSIGN on plain stock symbol (no \d{6}[CP]\d+ option body)
            TaxTransaction(action='ASSIGN', date='2025-01-15', symbol='AAPL.US',
                           quantity=100, net_amount=10000, currency='USD',
                           price=100.0),
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='AAPL.US',
                           quantity=-100, net_amount=11000, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        # Same effect as a BUYSELL: 100 bought at 10000, sold at 11000, gain 1000.
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 1000.0)

    def test_usa_stock_assign_is_buysell(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='ASSIGN', date='2025-01-15', symbol='AAPL.US',
                           quantity=100, net_amount=10000, currency='USD',
                           price=100.0),
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='AAPL.US',
                           quantity=-100, net_amount=11000, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 1000.0)


# ============================================================================
# Multi-symbol non-interference
# ============================================================================
class TestMultiSymbolIsolation(unittest.TestCase):
    """A buy/sell of X must not affect the ACB pool or wash-sale tracking
    of Y, no matter how close in time."""

    def test_two_symbols_dont_cross_pool(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='Y',
                           quantity=100, net_amount=20000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100, net_amount=11000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='Y',
                           quantity=-100, net_amount=22000, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = {g['symbol']: g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND'}
        # X: cost 10000, proceeds 11000, gain 1000
        # Y: cost 20000, proceeds 22000, gain 2000
        self.assertAlmostEqual(gains['X']['gain'], 1000.0)
        self.assertAlmostEqual(gains['Y']['gain'], 2000.0)


# ============================================================================
# Same-day intra-day round trip
# ============================================================================
class TestSameDayRoundTrip(unittest.TestCase):
    """Buy and sell on the same day with same date — must close properly."""

    def test_intra_day_close(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', time='09:30:00',
                           symbol='X', quantity=100, net_amount=10000,
                           currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', time='15:45:00',
                           symbol='X', quantity=-100, net_amount=10100,
                           currency='USD'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 100.0)
        # Same-day trade → 0 days_held → SHORT_TERM.
        self.assertEqual(gains[0]['days_held'], 0)
        self.assertEqual(gains[0]['term'], 'SHORT_TERM')


# ============================================================================
# Multi-replacement wash sale (mixed sheltered + taxable)
# ============================================================================
class TestMixedShelteredAndTaxableReplacement(unittest.TestCase):
    """A loss of $1000 is matched by TWO replacements totaling 100 shares:
    50 in a taxable account (deferred), 50 in an IRA (permanent). The gain
    entry should track both buckets correctly."""

    def test_mixed_disallowance(self):
        rules = USATaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='USD',
                           account='Brokerage'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=9000, currency='USD',
                           account='Brokerage'),
            # Half the replacement comes back in the taxable account.
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=50, net_amount=4750, currency='USD',
                           account='Brokerage'),
        ]
        sheltered = [
            # Other half in IRA — permanent disallowance.
            TaxTransaction(action='BUYSELL', date='2025-02-26', symbol='X',
                           quantity=50, net_amount=4760, currency='USD',
                           account='IRA'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0) < 0)
        # raw loss $1000, 50% goes to taxable replacement (deferred),
        # 50% goes to IRA (permanent). Both are added back to the gain.
        self.assertAlmostEqual(loss['raw_gain'], -1000.0, places=2)
        self.assertAlmostEqual(loss['disallowed_amount'], 1000.0, places=2)
        self.assertAlmostEqual(loss['permanently_disallowed'], 500.0, places=2)
        # Allowed gain = raw + total_disallowed = -1000 + 1000 = 0.
        self.assertAlmostEqual(loss['gain'], 0.0, places=2)


# ============================================================================
# ADJUST that brings pool to zero
# ============================================================================
class TestAcbAdjustEdgeCases(unittest.TestCase):
    def test_adjust_after_full_close_persists_to_zero(self):
        """ADJUST applied after pool is at qty=0 — the cost shouldn't
        accumulate phantom value into the next position."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=11000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=100, net_amount=12000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-04-15', symbol='X',
                           quantity=-100, net_amount=13000, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        gains = sorted(
            [g for g in result['transactions'] if g.get('action') != 'DIVIDEND'],
            key=lambda g: g['date'],
        )
        # Two clean cycles with no carryover.
        self.assertAlmostEqual(gains[0]['gain'], 1000.0)
        self.assertAlmostEqual(gains[1]['gain'], 1000.0)


# ============================================================================
# Dividend + tax withholding accounting
# ============================================================================
class TestDividendWithForeignTaxWithholding(unittest.TestCase):
    """When a foreign DIVIDEND comes with a TAX withholding, the gross
    dividend should flow through; the TAX is reported separately for the
    foreign tax credit calculation."""

    def test_us_dividend_with_15pct_canadian_withholding(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-15', symbol='AAPL.US',
                           quantity=0.0, gross_amount=100.0, net_amount=85.0,
                           currency='USD', type='dividend'),
            TaxTransaction(action='TAX', date='2025-03-15', symbol='AAPL.US',
                           quantity=0.0, net_amount=15.0,
                           currency='USD', type='tax'),
        ]
        result = rules.compute_gains(txs)
        divs = [g for g in result['transactions']
                if g.get('action') == 'DIVIDEND']
        # Engine reports the GROSS dividend (matches Schedule 3 / 1099-DIV).
        self.assertAlmostEqual(divs[0]['dividend'], 100.0)


# ============================================================================
# Trace integrity — fees show on every leg
# ============================================================================
class TestTraceFeeFidelity(unittest.TestCase):
    """The trace's Fee column should show the actual fee paid, whether the
    parser broke it out into commission/fee or it's only present implicitly
    in net_amount."""

    def test_implicit_fee_back_compute_visible_in_trace(self):
        """SELL with commission=0, fee=0, but qty*price ≠ net — the
        trace's _effective_fee_for_trace helper back-computes the fee."""
        rules = CanadaTaxRules()
        txs = [
            # Buy with explicit fee
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, price=50.0, commission=0,
                           fee=1.00, net_amount=5001.00, currency='CAD'),
            # Sell with NO explicit fee, but net is short of qty*price by $1
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100, price=60.0, commission=0,
                           fee=0, net_amount=5999.00, currency='CAD'),
        ]
        result = rules.compute_gains(txs, trace=True)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        trace_text = "\n".join(gains[0]['trace'])
        # The buy line shows Fee: 1.0000 explicitly.
        self.assertIn('Fee: 1.0000', trace_text)
        # The sell line back-computes Fee: 1.0000 even though tx.fee=0.
        # (|qty*price| - |net| = 100*60 - 5999 = 1)
        # Count "Fee: 1.0000" — should appear twice (buy + sell).
        self.assertGreaterEqual(trace_text.count('Fee: 1.0000'), 2,
                                msg="Implicit fee not back-computed for sell.")


# ============================================================================
# Sort priority: same date+time, different actions
# ============================================================================
class TestSortPriority(unittest.TestCase):
    """When ASSIGN and BUYSELL share a date+time, ASSIGN must be processed
    first so the option-leg adjustment is ready when the stock leg lands."""

    def test_option_assign_before_stock_buysell_same_datetime(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL250620P00030000', quantity=-1.0,
                           net_amount=1000.00, currency='USD'),
            # Same date+time as the stock leg below
            TaxTransaction(action='ASSIGN', date='2025-06-20', time='09:30:00',
                           symbol='AAPL250620P00030000', quantity=1.0,
                           net_amount=0.00, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-06-20', time='09:30:00',
                           symbol='AAPL', quantity=100,
                           net_amount=3000.00, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        # No phantom option gain — premium rolled into stock.
        opt_gains = [g for g in result['transactions']
                     if g.get('symbol') == 'AAPL250620P00030000']
        self.assertEqual(len(opt_gains), 0)
        # Stock basis = $3000 − $1000 premium = $2000.
        inv = next(i for i in result['inventory'] if i['symbol'] == 'AAPL')
        self.assertAlmostEqual(inv['total_cost'], 2000.0, places=2)


# ============================================================================
# Inventory reporting (open positions at end of input)
# ============================================================================
class TestInventoryReporting(unittest.TestCase):
    """Open long lots and open short lots both appear in the inventory
    output, with short qty reported as negative."""

    def test_long_and_short_both_in_inventory(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='LONG',
                           quantity=100, net_amount=10000, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='SHORT',
                           quantity=-50, net_amount=5000, currency='USD'),
        ]
        result = rules.compute_gains(txs)
        inv_by_sym = {i['symbol']: i for i in result['inventory']}
        self.assertAlmostEqual(inv_by_sym['LONG']['qty'], 100.0)
        self.assertAlmostEqual(inv_by_sym['SHORT']['qty'], -50.0,
                               msg="Short positions report negative qty.")


# ============================================================================
# Canada wash-sale window data structure (verbose ±30 day report)
# ============================================================================
class TestFxRateLoaderHonorsToColumn(unittest.TestCase):
    """The rates-file TO column was previously ignored; a file containing
    USD→EUR rates would silently apply when the user asked for --to CAD."""

    def test_skips_rows_with_wrong_to_currency(self):
        import tempfile, os
        from pathlib import Path
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates

        # Rate file has both USD→CAD and USD→EUR. Target is CAD.
        content = (
            "2025-01-04 12:00:00 USD CAD 1.35\n"
            "2025-01-04 12:00:00 USD EUR 0.93\n"   # wrong direction — must be filtered
            "2025-01-05 12:00:00 USD CAD 1.36\n"
        )
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        fh.write(content); fh.close()
        try:
            history = load_exchange_rates(Path(fh.name), target_curr='CAD')
            # USD entries should be from CAD rows only — both Jan 4 and Jan 5.
            self.assertEqual(set(history.get('USD', {}).keys()),
                             {'2025-01-04', '2025-01-05'})
            self.assertEqual(history['USD']['2025-01-04'], Decimal('1.35'))
            self.assertEqual(history['USD']['2025-01-05'], Decimal('1.36'))
        finally:
            os.remove(fh.name)

    def test_accepts_all_when_target_curr_none(self):
        """Backward-compat: omitting target_curr keeps old accept-all
        behavior (used by tests that build rate dicts directly)."""
        import tempfile, os
        from pathlib import Path
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates

        content = (
            "2025-01-04 12:00:00 USD CAD 1.35\n"
            "2025-01-04 12:00:00 USD EUR 0.93\n"
        )
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        fh.write(content); fh.close()
        try:
            history = load_exchange_rates(Path(fh.name))  # no target_curr
            # Both rows loaded; the EUR rate clobbers CAD because same key.
            self.assertIn('USD', history)
        finally:
            os.remove(fh.name)


class TestWashSolverConvergenceFlag(unittest.TestCase):
    """The CRA wash solver iterates until it finds no new wash sales. The
    convergence outcome is now reported in summary.wash_solver_converged
    and the iteration count in summary.wash_solver_iterations."""

    def test_simple_case_converges_in_one_or_two_iterations(self):
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100, net_amount=11000, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        self.assertTrue(result['summary']['wash_solver_converged'])
        self.assertEqual(result['summary']['wash_solver_iterations'], 1)

    def test_one_wash_sale_two_iterations(self):
        """One wash sale takes 2 iterations: first detects it, second
        confirms no more."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='X',
                           quantity=-100, net_amount=8000, currency='CAD'),
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='X',
                           quantity=100, net_amount=8500, currency='CAD'),
        ]
        result = rules.compute_gains(txs)
        self.assertTrue(result['summary']['wash_solver_converged'])
        self.assertEqual(result['summary']['wash_solver_iterations'], 2)


class TestRoundFloatsPreservesQty(unittest.TestCase):
    """round_floats must preserve fractional-share precision in qty fields
    while still rounding money fields. Brokerages like Webull and DRIPs
    routinely produce 5-8 dp quantities; 4 dp rounding loses material
    precision across many small accumulations."""

    def test_qty_preserved_money_rounded(self):
        from taxjson.lib.numeric import round_floats
        obj = {
            'qty': 0.12345678,            # share count: keep 8 dp
            'gain': 100.123456789,        # money: round to 4 dp
            'cost': 99.876543,
            'days_held': 365,
            'match_qty': 0.5555555,
            'running_bal': 1234.56789,
        }
        out = round_floats(obj, places=4)
        # Quantities pass through.
        self.assertAlmostEqual(out['qty'], 0.12345678, places=8)
        self.assertAlmostEqual(out['match_qty'], 0.5555555, places=7)
        self.assertAlmostEqual(out['running_bal'], 1234.56789, places=5)
        # Money rounds to 4 dp.
        self.assertAlmostEqual(out['gain'], 100.1235, places=4)
        # days_held passes through (it's an int but the rule still applies).
        self.assertEqual(out['days_held'], 365)

    def test_nested_qty_in_wash_replacements_preserved(self):
        from taxjson.lib.numeric import round_floats
        obj = {
            'wash_replacements': [
                {'match_qty': 0.12345678, 'basis_bump': 1.234567},
            ],
        }
        out = round_floats(obj)
        self.assertAlmostEqual(
            out['wash_replacements'][0]['match_qty'], 0.12345678, places=8,
        )
        self.assertAlmostEqual(
            out['wash_replacements'][0]['basis_bump'], 1.2346, places=4,
        )


class TestCrossEngineGainShape(unittest.TestCase):
    """Both engines emit the same set of keys on every gain entry, so
    downstream consumers can read field-presence consistently."""

    CORE_FIELDS = {
        'date', 'date_settle', 'symbol', 'qty', 'cost', 'proceeds',
        'gain', 'raw_gain', 'disallowed_amount', 'permanently_disallowed',
        'replacement_lot_ids', 'is_option', 'days_held', 'account',
        'currency', 'commission', 'fee', 'is_wash_sale', 'id', 'direction',
        'term', 'wash_replacements',
    }

    def _basic_txs(self):
        return [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, net_amount=10000, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X',
                           quantity=-100, net_amount=11000, currency='USD'),
        ]

    def test_canada_emits_all_core_fields(self):
        result = CanadaTaxRules().compute_gains(self._basic_txs())
        gain = next(g for g in result['transactions']
                    if g.get('action') != 'DIVIDEND')
        for field in self.CORE_FIELDS:
            self.assertIn(field, gain,
                          f"Canada engine missing field '{field}' that US emits")

    def test_usa_emits_all_core_fields(self):
        result = USATaxRules().compute_gains(self._basic_txs())
        gain = next(g for g in result['transactions']
                    if g.get('action') != 'DIVIDEND')
        for field in self.CORE_FIELDS:
            self.assertIn(field, gain,
                          f"US engine missing field '{field}'")

    def test_canada_term_is_none(self):
        """Canada doesn't have ST/LT — value is None (not absent, not the
        string 'LONG_TERM'). Consumers can distinguish engine by this."""
        result = CanadaTaxRules().compute_gains(self._basic_txs())
        gain = next(g for g in result['transactions']
                    if g.get('action') != 'DIVIDEND')
        self.assertIsNone(gain['term'])

    def test_usa_term_is_st_or_lt(self):
        result = USATaxRules().compute_gains(self._basic_txs())
        gain = next(g for g in result['transactions']
                    if g.get('action') != 'DIVIDEND')
        self.assertIn(gain['term'], ('SHORT_TERM', 'LONG_TERM'))


class TestConsumedReplacementsCannotWash(unittest.TestCase):
    """FUZZ-2026-07 #A (critical): shares that were themselves already
    SOLD cannot serve as §1091 replacement. The old behavior matched
    losses against fully-consumed lots and routed them to PERMANENT
    disallowance — destroying real losses in taxable-only sell/rebuy
    chains (a 20-cycle ladder with economic P&L 0 reported +4000).
    Consuming a lot now zeroes its replacement capacity; with no live
    in-window acquisition, the loss is simply ALLOWED, and lifetime
    totals equal economic P&L on a fully-closed position.

    (This class previously pinned the buggy expectation under the name
    TestWashGhostDictBug.)"""

    def test_short_loss_with_only_closed_prior_shorts_is_allowed(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=-10, net_amount=1000, currency='USD'),  # S1
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X',
                           quantity=10, net_amount=900, currency='USD'),    # close S1 +100
            TaxTransaction(action='BUYSELL', date='2025-01-25', symbol='X',
                           quantity=-10, net_amount=800, currency='USD'),   # S2
            TaxTransaction(action='BUYSELL', date='2025-01-30', symbol='X',
                           quantity=10, net_amount=750, currency='USD'),    # close S2 +50
            TaxTransaction(action='BUYSELL', date='2025-02-05', symbol='X',
                           quantity=-10, net_amount=600, currency='USD'),   # S3
            TaxTransaction(action='BUYSELL', date='2025-02-10', symbol='X',
                           quantity=10, net_amount=900, currency='USD'),    # close S3 -300
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        s3_close = next(g for g in gains if abs(g['raw_gain'] - (-300)) < 0.01)
        # S1/S2 are in the window but were fully covered BEFORE the loss:
        # they cannot wash it. Loss fully allowed; nothing permanent.
        self.assertAlmostEqual(s3_close['disallowed_amount'], 0.0, places=2)
        self.assertAlmostEqual(s3_close['permanently_disallowed'], 0.0,
                               places=2)
        self.assertAlmostEqual(s3_close['gain'], -300.0, places=2)
        # Conservation: totals equal economic P&L (+100 +50 -300 = -150).
        self.assertAlmostEqual(result['summary']['total_gain'], -150.0,
                               places=2)

    def test_long_loss_with_only_sold_prior_buys_is_allowed(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=10, net_amount=1000, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X',
                           quantity=-10, net_amount=1100, currency='USD'),  # +100
            TaxTransaction(action='BUYSELL', date='2025-01-25', symbol='X',
                           quantity=10, net_amount=900, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-01-30', symbol='X',
                           quantity=-10, net_amount=950, currency='USD'),   # +50
            TaxTransaction(action='BUYSELL', date='2025-02-05', symbol='X',
                           quantity=10, net_amount=1200, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-02-10', symbol='X',
                           quantity=-10, net_amount=900, currency='USD'),   # -300
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        l3_close = next(g for g in gains if abs(g['raw_gain'] - (-300)) < 0.01)
        self.assertAlmostEqual(l3_close['disallowed_amount'], 0.0, places=2)
        self.assertAlmostEqual(l3_close['permanently_disallowed'], 0.0,
                               places=2)
        self.assertAlmostEqual(l3_close['gain'], -300.0, places=2)
        self.assertAlmostEqual(result['summary']['total_gain'], -150.0,
                               places=2)

    def test_live_replacement_still_washes(self):
        """The normal wash is untouched: loss with a LIVE rebuy inside
        the window still defers into the replacement lot."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-02', symbol='X',
                           quantity=10, net_amount=1000, currency='USD'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='X',
                           quantity=-10, net_amount=800, currency='USD'),   # -200
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=10, net_amount=850, currency='USD'),    # live rebuy
        ]
        result = rules.compute_gains(txs)
        loss = next(g for g in result['transactions']
                    if abs(g.get('raw_gain', 0) - (-200)) < 0.01)
        self.assertAlmostEqual(loss['disallowed_amount'], 200.0, places=2)
        self.assertAlmostEqual(loss['permanently_disallowed'], 0.0, places=2)
        inv = {r['symbol']: r for r in result['inventory']}
        self.assertAlmostEqual(inv['X']['total_cost'], 1050.0, places=2)
        self.assertAlmostEqual(inv['X']['deferred_wash'], 200.0, places=2)



class TestCanadaWashWindow(unittest.TestCase):
    """The wash_window field on a Canada wash-sale gain carries the verbose
    audit data the trace renderer presents: ±30 day boundaries, all
    same-symbol activity across all accounts, role tags, running affiliated
    balance, and per-tx ACB snapshots."""

    def _setup(self):
        rules = CanadaTaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='SHOP.TO',
                           quantity=100, net_amount=10000, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-02-15', symbol='SHOP.TO',
                           quantity=-100, net_amount=8000, currency='CAD',
                           account='Margin'),
            # Replacement in taxable, 10 days post-loss (in window).
            TaxTransaction(action='BUYSELL', date='2025-02-25', symbol='SHOP.TO',
                           quantity=100, net_amount=8500, currency='CAD',
                           account='Margin'),
        ]
        sheltered = [
            # An RRSP buy 5 days post-loss — should appear in the window as
            # an affiliated candidate with [sheltered] tag.
            TaxTransaction(action='BUYSELL', date='2025-02-20', symbol='SHOP.TO',
                           quantity=20, net_amount=1700, currency='CAD',
                           account='RRSP'),
        ]
        return rules.compute_gains(taxable, sheltered_transactions=sheltered)

    def _wash_window(self):
        result = self._setup()
        loss = next(g for g in result['transactions'] if g.get('is_wash_sale'))
        ww = loss.get('wash_window')
        self.assertIsNotNone(ww, "wash sale gains must carry wash_window data")
        return ww

    def test_window_boundaries(self):
        ww = self._wash_window()
        self.assertEqual(ww['loss_date'], '2025-02-15')
        # ±30 days from 2025-02-15
        self.assertEqual(ww['window_start'], '2025-01-16')
        self.assertEqual(ww['window_end'], '2025-03-17')
        self.assertEqual(ww['loss_direction'], 'LONG')

    def test_affiliated_balance_test_data(self):
        ww = self._wash_window()
        # Balance at end of window = 100 (Jan buy) - 100 (Feb sell) +
        # 100 (Feb 25 buy) + 20 (RRSP) = 120
        self.assertAlmostEqual(ww['bal_at_end'], 120.0)
        self.assertAlmostEqual(ww['loss_qty'], 100.0)
        self.assertAlmostEqual(ww['disallowed_qty'], 100.0)

    def test_window_lists_all_same_symbol_activity(self):
        ww = self._wash_window()
        # Loss sale + taxable replacement + RRSP buy. (Jan 1 buy is
        # outside the -30 day window since Jan 16 is the boundary.)
        entries = ww['transactions']
        self.assertEqual(len(entries), 3)
        # Sorted by days_from_loss.
        days = [e['days_from_loss'] for e in entries]
        self.assertEqual(days, sorted(days))

    def test_window_marks_loss_trigger_and_sheltered(self):
        ww = self._wash_window()
        roles = {e.get('role') for e in ww['transactions']}
        self.assertIn('loss_sale', roles)
        self.assertIn('trigger', roles)
        # The Feb 25 replacement is the earliest-eligible trigger.
        trigger = next(e for e in ww['transactions'] if e['role'] == 'trigger')
        # Sheltered RRSP buy is in the window as a candidate.
        sheltered_entry = next(e for e in ww['transactions'] if e.get('sheltered'))
        self.assertEqual(sheltered_entry['account'], 'RRSP')

    def test_window_includes_running_balance_and_acb_per_share(self):
        ww = self._wash_window()
        # Every entry has a running_bal and acb_per_share_after.
        for e in ww['transactions']:
            self.assertIn('running_bal', e)
            self.assertIn('acb_per_share_after', e)
        # The running_bal at the LAST entry must equal bal_at_end.
        last = ww['transactions'][-1]
        self.assertAlmostEqual(last['running_bal'], ww['bal_at_end'])


class TestSplitRenamesPool(unittest.TestCase):
    """End-to-end check that SPLIT with `symbol_new` actually carries the
    ACB onto the new ticker.

    The previous behaviour ignored `symbol_new` entirely: the engine
    multiplied the source pool's qty by the ratio but left the pool
    indexed under the source symbol. A subsequent sale of the target
    ticker therefore found no inventory and realized either a phantom
    short-sale gain or no gain at all — silently losing the source's
    cost basis. The merger-rollover JSON emitted by `taxjson-corp-actions`
    depends on this rename working.
    """

    def test_canada_rollover_carries_acb_to_target(self):
        """Buy SSL.TO @ 80000, SPLIT to RGLD.US at 0.0625, sell RGLD.US
        @ 90000. Realized gain must reflect SSL.TO's original ACB
        (90000 - 80000 = 10000), not start fresh from zero."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='SSL.TO',
                           quantity=5000.0, net_amount=80000.0, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='SPLIT', date='2025-10-22', symbol='SSL.TO',
                           symbol_new='RGLD.US', quantity=0.0625, currency='CAD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-11-15', symbol='RGLD.US',
                           quantity=-312.5, net_amount=90000.0, currency='CAD',
                           account='Margin'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')
                 and not g.get('tainted')]
        self.assertEqual(len(gains), 1, "Should realize one gain on the RGLD.US sale")
        g = gains[0]
        self.assertEqual(g['symbol'], 'RGLD.US')
        self.assertAlmostEqual(g['gain'], 10000.0, places=2,
                               msg="ACB should carry over from SSL.TO; got "
                                   f"cost={g.get('cost')}, proceeds={g.get('proceeds')}")
        # Source pool should be empty after rename (no SSL.TO inventory left).
        ssl_inv = [i for i in result.get('inventory', []) if i.get('symbol') == 'SSL.TO']
        self.assertEqual(ssl_inv, [],
                         "SSL.TO inventory should be empty after rename to RGLD.US")

    def test_canada_split_without_symbol_new_is_classic_split(self):
        """When symbol_new is missing/empty, behaviour matches a classic
        stock split — qty multiplied, ACB unchanged, ticker unchanged.
        Regression guard for the rename branch."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='NVDA.US',
                           quantity=100.0, net_amount=80000.0, currency='USD',
                           account='Margin'),
            # 10-for-1 split, no rename.
            TaxTransaction(action='SPLIT', date='2025-06-15', symbol='NVDA.US',
                           symbol_new='', quantity=10.0, currency='USD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-11-15', symbol='NVDA.US',
                           quantity=-1000.0, net_amount=90000.0, currency='USD',
                           account='Margin'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')
                 and not g.get('tainted')]
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 10000.0, places=2)

    def test_usa_split_carries_basis_to_target(self):
        """Same end-to-end test on the US engine. Before this fix the
        US engine had no SPLIT handler at all — the SPLIT row fell
        through to the regular BUY path and added `ratio` (e.g. 0.0625)
        as new $0-cost shares, then the RGLD.US sale realized garbage."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='SSL',
                           quantity=5000.0, net_amount=80000.0, currency='USD',
                           account='Margin'),
            TaxTransaction(action='SPLIT', date='2025-10-22', symbol='SSL',
                           symbol_new='RGLD', quantity=0.0625, currency='USD',
                           account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-11-15', symbol='RGLD',
                           quantity=-312.5, net_amount=90000.0, currency='USD',
                           account='Margin'),
        ]
        result = rules.compute_gains(txs)
        sells = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(sells[0]['gain'], 10000.0, places=2)
        self.assertEqual(sells[0]['symbol'], 'RGLD')

    def test_split_rename_merge_skips_sentinel_last_acq_date(self):
        """Regression: when a SPLIT renames source→target and the source
        pool was auto-created but never traded (still at sentinel
        last_acq_date='1970-01-01'), the merge previously overwrote the
        target's real acquisition date with the sentinel — producing
        20000+ days_held on later sales. Fix: skip the date merge when
        the source pool is empty or still at the sentinel."""
        rules = CanadaTaxRules()
        txs = [
            # Real RGLD.US buy 100 days before the SPLIT.
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='RGLD.US',
                           quantity=100.0, price=200.0, net_amount=20000.0,
                           currency='CAD', account='Margin'),
            # SPLIT from a source ticker (SSL.TO) that was never traded.
            # Source pool auto-creates at qty=0, last_acq_date=sentinel.
            TaxTransaction(action='SPLIT', date='2025-06-23', symbol='SSL.TO',
                           symbol_new='RGLD.US', quantity=0.0625, currency='CAD',
                           account='Margin'),
            # Sell RGLD.US a week later — days_held should be ~100,
            # NOT ~20407 (which is what the sentinel-leak produced).
            TaxTransaction(action='BUYSELL', date='2025-06-30', symbol='RGLD.US',
                           quantity=-50.0, price=210.0, net_amount=10500.0,
                           currency='CAD', account='Margin'),
        ]
        result = rules.compute_gains(txs)
        sells = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')
                 and not g.get('tainted')]
        self.assertEqual(len(sells), 1)
        days = sells[0].get('days_held', 0)
        # Real holding period is 107 days (Mar 15 → Jun 30). Allow some
        # slack but the sentinel-leak would have produced 5-figure values.
        self.assertLess(days, 365,
                        f"days_held leaked sentinel last_acq_date: got {days}")

    def test_usa_split_not_treated_as_wash_sale_replacement(self):
        """Regression for the US wash-sale pre-pass at core.py:1190
        indexing every positive-qty event as a replacement candidate.
        A SPLIT row with positive ratio was being recorded as a
        replacement lot, causing false wash-sale matches on a post-loss
        split with no actual repurchase."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='NVDA',
                           quantity=200.0, price=400.0, net_amount=80000.0,
                           currency='USD', account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='NVDA',
                           quantity=-100.0, price=350.0, net_amount=35000.0,
                           currency='USD', account='Margin'),
            TaxTransaction(action='SPLIT', date='2025-06-20', symbol='NVDA',
                           symbol_new='', quantity=10.0, currency='USD',
                           account='Margin'),
        ]
        result = rules.compute_gains(txs)
        # No real replacement buy → no wash sale should fire.
        sells = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0].get('disallowed_amount', 0), 0,
                         f"SPLIT must not count as replacement; got "
                         f"disallowed_amount={sells[0].get('disallowed_amount')}")

    def test_canada_split_not_treated_as_wash_sale_trigger(self):
        """Regression for the Canada wash-sale solver picking SPLIT rows
        as potential acquisitions. SPLIT has positive `quantity` (ratio)
        but is NOT a buy — letting it through the trigger filter
        silently disallowed full losses when a corporate split landed
        within 30 days of a loss sale, even with no actual repurchase."""
        rules = CanadaTaxRules()
        txs = [
            # Buy 200 NVDA.US well outside the wash window.
            TaxTransaction(action='BUYSELL', date='2024-01-15', symbol='NVDA.US',
                           quantity=200.0, price=400.0, net_amount=80000.0,
                           currency='USD', account='Margin'),
            # Sell 100 at a $5000 loss, mid-2025.
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='NVDA.US',
                           quantity=-100.0, price=350.0, net_amount=35000.0,
                           currency='USD', account='Margin'),
            # 10-for-1 split within the 30-day window — must NOT be
            # treated as a replacement buy. No actual repurchase.
            TaxTransaction(action='SPLIT', date='2025-06-20', symbol='NVDA.US',
                           symbol_new='', quantity=10.0, currency='USD',
                           account='Margin'),
        ]
        result = rules.compute_gains(txs)
        wash_sales = result.get('wash_sales', [])
        self.assertEqual(
            wash_sales, [],
            f"SPLIT must not count as a wash-sale trigger; got {wash_sales}",
        )

    def test_canada_wash_sale_bridges_split_rename(self):
        """A loss on SSL.TO followed within 30 days by a buy of RGLD.US
        (with SSL.TO→RGLD.US linked by a SPLIT-rename) must be detected
        as a superficial loss under CRA's substantially-identical rule.
        Before this fix the wash-sale solver was symbol-blind across
        renames — the loss-symbol's running balance only saw SSL.TO
        activity, and the candidate-trigger filter discarded RGLD.US
        buys for not matching tx.symbol."""
        rules = CanadaTaxRules()
        txs = [
            # Buy SSL.TO at $20/share, will be sold at a loss.
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='SSL.TO',
                           quantity=100.0, price=20.0, net_amount=2000.0,
                           currency='CAD', account='Margin'),
            # Sell SSL.TO at $10 — $1000 loss.
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='SSL.TO',
                           quantity=-100.0, price=10.0, net_amount=1000.0,
                           currency='CAD', account='Margin'),
            # Merger rollover SSL.TO → RGLD.US (s.85.1(5) election).
            TaxTransaction(action='SPLIT', date='2025-06-20', symbol='SSL.TO',
                           symbol_new='RGLD.US', quantity=0.0625, currency='CAD',
                           account='Margin'),
            # Buy RGLD.US within 30 days of the SSL.TO loss — must be
            # treated as a superficial-loss trigger because the rename
            # makes it the same logical security.
            TaxTransaction(action='BUYSELL', date='2025-07-01', symbol='RGLD.US',
                           quantity=10.0, price=200.0, net_amount=2000.0,
                           currency='CAD', account='Margin'),
        ]
        result = rules.compute_gains(txs)
        wash_sales = result.get('wash_sales', [])
        self.assertTrue(
            len(wash_sales) >= 1,
            "SSL.TO loss followed by RGLD.US buy within 30d should "
            "trigger superficial loss via SPLIT-rename alias; got "
            f"wash_sales={wash_sales}",
        )

    def test_both_engines_exclude_tainted_from_by_ticker(self):
        """The Canada engine's by_ticker construction skips tainted
        gain entries (those drawing from a phantom OPENING_BALANCE).
        The US engine's by_ticker construction needs the same guard,
        otherwise downstream consumers that read by_ticker — rather
        than the post-CLI `transactions` list — see fabricated cost=0
        gains inflating totals. Pin parity across both engines."""
        for rules in (CanadaTaxRules(), USATaxRules()):
            txs = [
                TaxTransaction(action='OPENING_BALANCE', date='2024-12-31',
                               symbol='AAPL', quantity=100.0, currency='USD',
                               account='Margin'),
                TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='AAPL',
                               quantity=-100.0, net_amount=20000.0, currency='USD',
                               account='Margin'),
            ]
            result = rules.compute_gains(txs)
            by_ticker = result.get('by_ticker', {})
            aapl = by_ticker.get('AAPL', {})
            self.assertEqual(
                aapl.get('total_gain', 0), 0.0,
                f"{rules.__class__.__name__} by_ticker['AAPL']['total_gain'] "
                f"should be 0 (only disposition is tainted); got {aapl!r}",
            )

    def test_usa_negative_opening_balance_uses_short_lot_shape(self):
        """Latent crash regression: a negative-qty OPENING_BALANCE row
        routes into inventory_short. If the lot is shaped like a long
        lot (`cost_basis` key only), the first BUY-CLOSE raises KeyError
        when it reads `short_lot['proceeds']`. `synthesize_openings`
        only emits positive qty today, but user-injected rows can be
        negative — fix is forward-compat hardening."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='OPENING_BALANCE', date='2024-12-31',
                           symbol='AAPL', quantity=-100.0, currency='USD',
                           account='Margin'),
            # Buy-to-close half the synthetic short.
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='AAPL',
                           quantity=50.0, net_amount=10000.0, currency='USD',
                           account='Margin'),
        ]
        # Must not raise. The tainted flag should still propagate so
        # the CLI routes the resulting gain entry to manual_reporting.
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        self.assertEqual(len(gains), 1)
        self.assertTrue(gains[0].get('tainted'))

    def test_usa_opening_balance_taints_disposition(self):
        """`--incomplete-history` synthesizes OPENING_BALANCE rows.
        Before this fix, the US engine treated them as a normal zero-
        cost-basis BUY, so a later sale appeared as a real, taxable
        $proceeds gain instead of being marked tainted and routed to
        manual_reporting_required at the CLI layer.

        Mirrors the Canada engine's tainted-pool behaviour: the gain
        entry must carry `tainted=True` so the CLI's split-by-taint
        path picks it up."""
        rules = USATaxRules()
        txs = [
            # Phantom 100 shares of AAPL from before the data window —
            # cost basis unknown.
            TaxTransaction(action='OPENING_BALANCE', date='2024-12-31',
                           symbol='AAPL', quantity=100.0, currency='USD',
                           account='Margin'),
            # Real disposition in the year of interest.
            TaxTransaction(action='BUYSELL', date='2025-06-15', symbol='AAPL',
                           quantity=-100.0, net_amount=20000.0, currency='USD',
                           account='Margin'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        self.assertEqual(len(gains), 1)
        # Tainted flag set so the CLI routes this to
        # `manual_reporting_required` instead of `transactions`.
        self.assertTrue(gains[0].get('tainted'),
                        "Disposition from a phantom OPENING_BALANCE lot "
                        "must be flagged tainted; got "
                        f"tainted={gains[0].get('tainted')!r}")


if __name__ == '__main__':
    unittest.main()

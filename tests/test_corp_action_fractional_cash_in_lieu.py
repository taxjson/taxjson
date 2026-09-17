"""Corp-action mergers/spinoffs snap fractional residue to whole shares
and reduce cost basis proportionally — mimicking real brokers' cash-in-
lieu treatment.

Without this, an SSL.TO 1-for-16 merger applied to a holding that
isn't a multiple of 16 would leave a phantom 0.00XX RGLD.US dust
position in the inventory forever. A `ticker.map` typically already
DELETEs the broker's CAD-side cash-in-lieu rows, but the target-side
residue persisted because the corp-actions extractor emitted the full
ratio multiplication.

The fix lives in `corp_actions.py:_snap_qty_to_whole_shares` and is
applied uniformly to:
  - `_canada_merger_taxable`
  - `_canada_spinoff_deemed_dividend`
  - `_canada_spinoff_rollover_s_86_1`
"""
import unittest

from taxjson.lib.corp_actions import (
    CorporateAction,
    _canada_merger_taxable,
    _canada_spinoff_deemed_dividend,
    _canada_spinoff_rollover_s_86_1,
    _emit_taxable_exchange,
    _snap_qty_to_whole_shares,
)


def _event(**kwargs):
    """Build a CorporateAction with sensible defaults for a synthetic
    SSL.TO → RGLD.US 1-for-16 case."""
    base = dict(
        date='2025-10-22', time='09:30:00',
        action_type='merger',
        source_symbol='SSL.TO', source_isin='CA0000000001',
        target_symbol='RGLD.US', target_isin='US0000000002',
        ratio_new=1, ratio_old=16,
        qty_disposed=1600.0416,
        qty_received=100.0026,    # the actual fractional residue
        fmv=25920.67, currency='CAD',
        target_fmv=18550.50, target_currency='USD',
        account='RRSP',
    )
    base.update(kwargs)
    return CorporateAction(**base)


class TestSnapHelper(unittest.TestCase):
    def test_clean_integer_qty_passes_through(self):
        whole, fmv, frac = _snap_qty_to_whole_shares(100.0, 5000.0)
        self.assertEqual(whole, 100.0)
        self.assertEqual(fmv, 5000.0)
        self.assertEqual(frac, 0.0)

    def test_small_fractional_residue_snaps_down(self):
        whole, fmv, frac = _snap_qty_to_whole_shares(100.0026, 18550.50)
        self.assertEqual(whole, 100.0)
        # Per-share basis preserved: fmv/whole == 18550.50/100.0026
        self.assertAlmostEqual(fmv / whole, 18550.50 / 100.0026, places=4)
        self.assertAlmostEqual(frac, 0.0026, places=6)

    def test_per_share_basis_preserved(self):
        """The whole point — adjusted_fmv/whole_qty must equal the
        original total_fmv/qty_received."""
        orig_qty, orig_total = 100.5, 5000.0
        whole, fmv, _ = _snap_qty_to_whole_shares(orig_qty, orig_total)
        self.assertAlmostEqual(fmv / whole, orig_total / orig_qty, places=10)

    def test_entirely_fractional_returns_zero_whole(self):
        """A merger ratio that produces only fractional shares (rare
        but possible: tiny source holding × small ratio) settles as
        cash-in-lieu only; caller skips the BUY row."""
        whole, fmv, frac = _snap_qty_to_whole_shares(0.5, 100.0)
        self.assertEqual(whole, 0.0)
        self.assertEqual(fmv, 0.0)
        self.assertAlmostEqual(frac, 0.5)

    def test_zero_qty_returns_zero(self):
        whole, fmv, frac = _snap_qty_to_whole_shares(0.0, 0.0)
        self.assertEqual(whole, 0.0)
        self.assertEqual(frac, 0.0)

    def test_float_noise_below_integer_treated_as_clean(self):
        """A quantity that is a whole number contaminated by float noise
        (ratio math rarely lands exactly on an integer) must NOT floor
        down and emit a spurious ~1.0 cash-in-lieu fraction."""
        whole, fmv, frac = _snap_qty_to_whole_shares(99.99999998, 5000.0)
        self.assertEqual(whole, 100.0)
        self.assertEqual(fmv, 5000.0)
        self.assertEqual(frac, 0.0)


class TestMergerTaxableSnap(unittest.TestCase):
    def test_fractional_residue_snapped(self):
        """The motivating case: SSL.TO 1-for-16 merger leaving
        100.0026 RGLD.US must snap the BUY to 100 with proportionally
        reduced cost basis. The phantom 0.0026 dust position is gone."""
        rows = _canada_merger_taxable(_event(), 'taxable_disposition', {})
        buys = [r for r in rows if r['action'] == 'BUYSELL' and r['quantity'] > 0]
        sells = [r for r in rows if r['action'] == 'BUYSELL' and r['quantity'] < 0]
        # Source SELL unchanged.
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(sells[0]['net_amount'], 25920.67, places=2)
        # Target BUY snapped to 100 whole shares.
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]['quantity'], 100.0)
        self.assertEqual(buys[0]['symbol'], 'RGLD.US')
        # Cost basis reduced proportionally (preserves per-share basis).
        expected_fmv = (100.0 / 100.0026) * 18550.50
        self.assertAlmostEqual(buys[0]['net_amount'], expected_fmv, places=2)
        # Description calls out the cash-in-lieu treatment.
        self.assertIn('cash-in-lieu', buys[0]['description'])

    def test_per_share_basis_preserved_after_snap(self):
        rows = _canada_merger_taxable(_event(), 'taxable_disposition', {})
        buy = next(r for r in rows if r['action'] == 'BUYSELL' and r['quantity'] > 0)
        per_share = buy['net_amount'] / buy['quantity']
        self.assertAlmostEqual(per_share, 18550.50 / 100.0026, places=4)

    def test_clean_integer_merger_unchanged(self):
        """If qty_received is already whole, the BUY emits unchanged
        (no spurious cash-in-lieu treatment)."""
        rows = _canada_merger_taxable(
            _event(qty_received=100.0), 'taxable_disposition', {},
        )
        buy = next(r for r in rows if r['action'] == 'BUYSELL' and r['quantity'] > 0)
        self.assertEqual(buy['quantity'], 100.0)
        self.assertAlmostEqual(buy['net_amount'], 18550.50, places=2)
        self.assertNotIn('cash-in-lieu', buy['description'])

    def test_pure_fractional_merger_skips_buy(self):
        """If qty_received < 1, no whole shares received — the user
        got only cash-in-lieu. The BUY row is suppressed; the source
        SELL alone captures the disposition."""
        rows = _canada_merger_taxable(
            _event(qty_received=0.5), 'taxable_disposition', {},
        )
        buys = [r for r in rows if r['action'] == 'BUYSELL' and r['quantity'] > 0]
        sells = [r for r in rows if r['action'] == 'BUYSELL' and r['quantity'] < 0]
        self.assertEqual(len(buys), 0)
        self.assertEqual(len(sells), 1)


class TestSpinoffDeemedDividendSnap(unittest.TestCase):
    def test_dividend_full_value_buy_snapped(self):
        """The DIVIDEND (income) row keeps the FULL pre-snap value —
        the user is taxed on what was actually distributed, including
        the fractional. Only the BUY snaps to whole shares."""
        ev = _event(
            action_type='spinoff', qty_received=10.5,
            source_symbol='ABBV.US', target_symbol='AB.US',
        )
        rows = _canada_spinoff_deemed_dividend(ev, '', {'fmv_per_share': 100.0})
        div = next(r for r in rows if r['action'] == 'DIVIDEND')
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        # DIVIDEND: full FMV (10.5 × $100).
        self.assertAlmostEqual(div['net_amount'], 1050.0, places=2)
        # BUY: snapped to 10 with proportional cost (10 × $100).
        self.assertEqual(buy['quantity'], 10.0)
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=2)
        self.assertIn('cash-in-lieu', buy['description'])


class TestSpinoffRolloverSnap(unittest.TestCase):
    def test_s86_1_rollover_snaps_acb_basis(self):
        """s. 86.1 rollover: ACB allocated from parent. Snap to whole
        shares; the parent's ACB reduction (separate row) stays at
        the full allocated_acb regardless."""
        ev = _event(
            action_type='spinoff', qty_received=10.5,
            source_symbol='ABBV.US', target_symbol='AB.US',
        )
        rows = _canada_spinoff_rollover_s_86_1(
            ev, '', {'allocated_acb': 525.0},
        )
        buys = [r for r in rows if r['action'] == 'BUYSELL']
        # The spinoff BUY is snapped to 10 with proportional ACB.
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]['quantity'], 10.0)
        self.assertAlmostEqual(buys[0]['net_amount'], 500.0, places=2)

    def test_s86_1_rollover_conserves_basis(self):
        """Basis must be conserved: the parent ACB reduction equals the
        basis that actually moved to the whole-share spinoff lot, NOT the
        full allocated_acb. Otherwise the fractional ACB (allocated_acb -
        adjusted_acb) silently vaporizes — present in neither pool."""
        ev = _event(
            action_type='spinoff', qty_received=10.5,
            source_symbol='ABBV.US', target_symbol='AB.US',
        )
        rows = _canada_spinoff_rollover_s_86_1(ev, '', {'allocated_acb': 525.0})
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        parent_adjust = next(r for r in rows if r['action'] == 'ADJUST')
        # The basis added to the spinoff lot exactly equals the basis
        # removed from the parent — no leak.
        self.assertAlmostEqual(buy['net_amount'], -parent_adjust['net_amount'], places=6)
        self.assertAlmostEqual(parent_adjust['net_amount'], -500.0, places=2)



class TestFractionalDeliveryBrokers(unittest.TestCase):
    """IB DELIVERS real fractional shares: qty_received is the exact
    delivered quantity. Snapping a real 0.5-share delivery to whole +
    cash-in-lieu manufactured a phantom -0.5 short the moment the user
    sold their actual fraction (Honeywell split-up, seen on a real IB
    export 2026-07). fractional_delivery=True limits snapping to dust."""

    def test_real_fraction_kept_when_fractional_delivery(self):
        ev = _event(qty_received=7.5, fractional_delivery=True)
        rows = _emit_taxable_exchange(ev, {}, description_base='x')
        buy = next(r for r in rows if r['action'] == 'BUYSELL'
                   and r['quantity'] > 0)
        self.assertEqual(buy['quantity'], 7.5)
        self.assertNotIn('cash-in-lieu', buy['description'])

    def test_dust_still_snapped_when_fractional_delivery(self):
        # IB's own dust (100.0026 later journaled as 100) must still
        # snap — that's the case the snap helper was built for.
        ev = _event(qty_received=100.0026, fractional_delivery=True)
        rows = _emit_taxable_exchange(ev, {}, description_base='x')
        buy = next(r for r in rows if r['action'] == 'BUYSELL'
                   and r['quantity'] > 0)
        self.assertEqual(buy['quantity'], 100.0)

    def test_default_brokers_still_snap(self):
        # No flag: pinned whole+CIL behavior unchanged.
        ev = _event(qty_received=10.5)
        rows = _emit_taxable_exchange(ev, {}, description_base='x')
        buy = next(r for r in rows if r['action'] == 'BUYSELL'
                   and r['quantity'] > 0)
        self.assertEqual(buy['quantity'], 10.0)

    def test_ib_extractor_sets_flag(self):
        import tempfile
        from pathlib import Path
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        csv_text = (
            'Corporate Actions,Header,Asset Category,Currency,Report Date,'
            'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00",'
            '"SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 '
            '(RGLD.CAD, ROYAL GOLD INC, US0000000002)",100.0026,0,25840.67,0,\n'
            'Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00",'
            '"SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 '
            '(SSL, SANDSTORM GOLD LTD, CA0000000001)",-1600.0416,0,-25920.67,0,\n')
        f = Path(tempfile.mkdtemp()) / 'ib.csv'
        f.write_text(csv_text)
        events = parse_ib_corporate_actions(f)
        self.assertTrue(all(e.fractional_delivery for e in events))


if __name__ == '__main__':
    unittest.main()

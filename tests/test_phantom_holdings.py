"""Tests for phantom-holdings reconciliation.

These cover the design contract:

- Detection finds (symbol, account) pairs whose running position goes negative.
- Tainted dispositions are excluded from the headline gains.
- Pool re-cleans on drain-to-zero so post-drain buys form a fresh ACB.
- Registered-account heuristic surfaces in suggestions.
- Real shorts (not on the phantoms list) are computed normally.
- Listing a ticker that doesn't go negative is a no-op (with a note).

Coverage maps to the scenarios discussed at design time. Each test names
the case for traceability.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from taxjson.lib.phantom_holdings import (
    PhantomCandidate,
    detect_phantoms,
    detect_superficial_loss_warnings,
    format_suggestions,
    is_registered_account,
    load_phantoms,
    synthesize_openings,
)


def _tx(action, date, symbol, qty, price=0.0, net=0.0, account='Margin', currency='USD'):
    return TaxTransaction(
        action=action,
        date=date,
        symbol=symbol,
        quantity=qty,
        price=price,
        net_amount=net or abs(qty * price),
        account=account,
        currency=currency,
    )


class TestDetection(unittest.TestCase):
    """Case #1: simple phantom — one sell with no preceding buy."""

    def test_simple_phantom_detected(self):
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 18000, account='LIRA'),
        ]
        candidates = detect_phantoms(txs)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.symbol, 'AAPL.US')
        self.assertEqual(c.account, 'LIRA')
        self.assertAlmostEqual(c.peak_short, -100.0)
        self.assertAlmostEqual(c.end_position, -100.0)
        self.assertTrue(c.registered)

    def test_clean_data_no_candidates(self):
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL.US', 100, 150.0, 15009),
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991),
        ]
        self.assertEqual(detect_phantoms(txs), [])

    def test_real_short_cycle_also_detected_as_candidate(self):
        """Case #6 detection-side: a real short open/close looks identical
        to a phantom from the detector's perspective. The user decides
        which entries are real shorts by *not* listing them in phantoms.json."""
        txs = [
            _tx('BUYSELL', '2024-06-15', 'NVDA.US', -50, 1000.0, 50000),   # short open
            _tx('BUYSELL', '2024-08-01', 'NVDA.US', 50, 900.0, 45000),    # buy to close
        ]
        candidates = detect_phantoms(txs)
        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0].peak_short, -50.0)
        self.assertAlmostEqual(candidates[0].end_position, 0.0)
        self.assertFalse(candidates[0].registered)

    def test_separate_accounts_independent(self):
        """Case #5: AAPL phantom in LIRA + real short in Margin — both surface."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 18000, account='LIRA'),
            _tx('BUYSELL', '2024-04-10', 'AAPL.US', -50, 175.0, 8750, account='Margin'),
            _tx('BUYSELL', '2024-05-15', 'AAPL.US', 50, 170.0, 8500, account='Margin'),
        ]
        candidates = detect_phantoms(txs)
        accounts = {c.account for c in candidates}
        self.assertEqual(accounts, {'LIRA', 'Margin'})

    def test_disposition_count(self):
        """Multi-disposition phantom: count both sells inside the negative span."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -60, 180.0, 10800, account='LIRA'),
            _tx('BUYSELL', '2024-04-10', 'AAPL.US', -40, 175.0, 7000, account='LIRA'),
        ]
        candidates = detect_phantoms(txs)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].disposition_count, 2)

    def test_options_skipped_by_default(self):
        """Sell-to-open covered call in registered account is normal, not phantom.
        OCC-format symbols should not appear in detection results by default."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL250620C00190000.US', -2, 5.0, 999, account='TFSA'),
            _tx('BUYSELL', '2024-05-15', 'AAPL250620C00190000.US', 2, 3.0, 601, account='TFSA'),
            # Stock phantom should still show up.
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='LIRA'),
        ]
        candidates = detect_phantoms(txs)
        symbols = {c.symbol for c in candidates}
        self.assertEqual(symbols, {'AAPL.US'})

    def test_options_included_with_flag(self):
        """include_options=True restores the old aggressive behavior."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL250620C00190000.US', -2, 5.0, 999, account='TFSA'),
        ]
        candidates = detect_phantoms(txs, include_options=True)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].symbol, 'AAPL250620C00190000.US')


class TestRegisteredAccount(unittest.TestCase):
    def test_canonical_registered_labels(self):
        for label in ('LIRA', 'RRSP', 'TFSA', 'RRIF', 'RESP', 'LIF', 'FHSA'):
            self.assertTrue(is_registered_account(label), label)

    def test_prefixed_or_suffixed_labels_match(self):
        self.assertTrue(is_registered_account('Spousal RRSP'))
        self.assertTrue(is_registered_account('TFSA-Self'))

    def test_non_registered(self):
        for label in ('Margin', 'Cash', 'Corporate', 'Joint'):
            self.assertFalse(is_registered_account(label), label)


class TestLoaderAndSuggestions(unittest.TestCase):
    def test_load_phantoms_strips_underscore_metadata(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([
                {
                    "symbol": "AAPL.US", "account": "LIRA",
                    "_note": "documentation only",
                    "_peak_short": -100,
                },
                {"symbol": "MSFT.US", "account": "TFSA"},
            ], f)
            fname = f.name
        try:
            result = load_phantoms(Path(fname))
            self.assertEqual(result, {('AAPL.US', 'LIRA'), ('MSFT.US', 'TFSA')})
        finally:
            os.remove(fname)

    def test_load_phantoms_requires_symbol_and_account(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{"symbol": "AAPL.US"}], f)
            fname = f.name
        try:
            with self.assertRaises(ValueError):
                load_phantoms(Path(fname))
        finally:
            os.remove(fname)

    def test_load_phantoms_rejects_non_array_root(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"phantoms": [{"symbol": "AAPL.US", "account": "LIRA"}]}, f)
            fname = f.name
        try:
            with self.assertRaises(ValueError):
                load_phantoms(Path(fname))
        finally:
            os.remove(fname)

    def test_load_phantoms_rejects_non_object_entries(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(["AAPL.US"], f)
            fname = f.name
        try:
            with self.assertRaises(ValueError):
                load_phantoms(Path(fname))
        finally:
            os.remove(fname)

    def test_load_phantoms_empty_array_ok(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([], f)
            fname = f.name
        try:
            self.assertEqual(load_phantoms(Path(fname)), set())
        finally:
            os.remove(fname)

    def test_load_phantoms_rejects_empty_symbol_or_account(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{"symbol": "", "account": "LIRA"}], f)
            fname = f.name
        try:
            with self.assertRaises(ValueError):
                load_phantoms(Path(fname))
        finally:
            os.remove(fname)

    def test_format_suggestions_empty_list(self):
        from taxjson.lib.phantom_holdings import format_suggestions
        out = format_suggestions([])
        # Valid JSON, empty array.
        self.assertEqual(out.strip(), '[]')


class TestSynthesisAndTaint(unittest.TestCase):
    """End-to-end through CanadaTaxRules.compute_gains to verify taint behavior."""

    def _compute(self, txs, phantoms=None):
        if phantoms:
            txs, _ = synthesize_openings(txs, phantoms)
        return CanadaTaxRules().compute_gains(txs, detect_wash_sales=False)

    def test_case1_simple_phantom_sell_tainted(self):
        """Case #1: phantom sell → gain record marked tainted."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='LIRA'),
        ]
        result = self._compute(txs, phantoms={('AAPL.US', 'LIRA')})
        self.assertEqual(len(result['transactions']), 1)
        self.assertTrue(result['transactions'][0].get('tainted', False))

    def test_case3_drain_to_zero_clean_pool(self):
        """Case #3: phantom drains to zero, fresh buys, later sell — last sell is clean."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='Margin'),  # phantom drain
            _tx('BUYSELL', '2024-04-10', 'AAPL.US', 200, 170.0, 34009, account='Margin'),   # fresh clean buys
            _tx('BUYSELL', '2024-05-15', 'AAPL.US', -100, 190.0, 18991, account='Margin'),  # clean sell
        ]
        result = self._compute(txs, phantoms={('AAPL.US', 'Margin')})
        gains = sorted(result['transactions'], key=lambda g: g['date'])
        self.assertEqual(len(gains), 2)
        # First sell consumes phantom shares → tainted.
        self.assertTrue(gains[0].get('tainted'))
        # Second sell is from the post-drain clean pool → not tainted, real gain.
        self.assertFalse(gains[1].get('tainted', False))
        # Clean gain: cost basis = 100 * (34009 / 200) = 17004.5, proceeds ~18991 → gain ~1986
        self.assertAlmostEqual(gains[1]['gain'], 1986.5, places=1)

    def test_case2_partial_cover_never_drains(self):
        """Case #2: phantom sell, partial cover that doesn't drain → still tainted."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='Margin'),
            _tx('BUYSELL', '2024-04-10', 'AAPL.US', 30, 175.0, 5251, account='Margin'),
            _tx('BUYSELL', '2024-05-15', 'AAPL.US', -50, 170.0, 8499, account='Margin'),
        ]
        result = self._compute(txs, phantoms={('AAPL.US', 'Margin')})
        # All dispositions while pool is tainted.
        self.assertTrue(all(g.get('tainted') for g in result['transactions']))

    def test_case6_real_short_not_listed_computed_normally(self):
        """Case #6: real short cycle, not in phantoms list → gain computed."""
        txs = [
            _tx('BUYSELL', '2024-06-15', 'NVDA.US', -50, 1000.0, 49998, account='Margin'),
            _tx('BUYSELL', '2024-08-01', 'NVDA.US', 50, 900.0, 45002, account='Margin'),
        ]
        result = self._compute(txs, phantoms=set())
        self.assertEqual(len(result['transactions']), 1)
        g = result['transactions'][0]
        self.assertFalse(g.get('tainted', False))
        # Short profit: proceeds 49998 - cover cost 45002 ≈ 4996
        self.assertAlmostEqual(g['gain'], 4996, delta=2)

    def test_case9_listed_but_data_complete_is_no_op(self):
        """Case #9: ticker in phantoms.json but data is complete → opening synthesized at 0, no taint propagates."""
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL.US', 100, 150.0, 15009),
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991),
        ]
        # User mis-classified — data is actually fine.
        new_txs, log = synthesize_openings(txs, phantoms={('AAPL.US', 'Margin')})
        # No OPENING_BALANCE should have been inserted.
        opening_count = sum(1 for t in new_txs if t.action == 'OPENING_BALANCE')
        self.assertEqual(opening_count, 0)
        # Log should reflect the no-op.
        self.assertEqual(len(log), 1)
        self.assertFalse(log[0]['inserted'])
        self.assertIn('no opening needed', log[0]['note'])

    def test_case10_suggest_phantoms_idempotency(self):
        """Case #10: regenerating suggestions twice produces the same output."""
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='LIRA'),
            _tx('BUYSELL', '2024-04-10', 'MSFT.US', -50, 400.0, 19998, account='TFSA'),
        ]
        from taxjson.lib.phantom_holdings import format_suggestions
        out1 = format_suggestions(detect_phantoms(txs))
        out2 = format_suggestions(detect_phantoms(txs))
        self.assertEqual(out1, out2)


class TestCase4RegisteredAccountFlagInSuggestions(unittest.TestCase):
    """Case #4: registered-account phantom shows up in --suggest-phantoms
    output with a strong-warning note."""

    def test_lira_phantom_flagged_in_suggestions(self):
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='LIRA'),
            _tx('BUYSELL', '2024-04-10', 'NVDA.US', -50, 1000.0, 49998, account='Margin'),
        ]
        candidates = detect_phantoms(txs)
        rendered = format_suggestions(candidates)
        # Both candidates appear; LIRA has the strong warning.
        self.assertIn('AAPL.US', rendered)
        self.assertIn('LIRA', rendered)
        self.assertIn('Registered account', rendered)
        # Margin account gets the softer note.
        self.assertIn('NVDA.US', rendered)
        self.assertIn('Margin/cash account', rendered)


class TestCase7MultipleRealShortCyclesSameTicker(unittest.TestCase):
    """Case #7: real short cycles each produce their own gain."""

    def test_two_short_cycles_each_gain(self):
        txs = [
            _tx('BUYSELL', '2024-02-01', 'NVDA.US', -10, 1000.0, 9998, account='Margin'),
            _tx('BUYSELL', '2024-03-15', 'NVDA.US', 10, 900.0, 9002, account='Margin'),
            _tx('BUYSELL', '2024-06-01', 'NVDA.US', -10, 1100.0, 10998, account='Margin'),
            _tx('BUYSELL', '2024-07-15', 'NVDA.US', 10, 1050.0, 10502, account='Margin'),
        ]
        # NOT marked as phantom — these are real short cycles.
        result = CanadaTaxRules().compute_gains(txs, detect_wash_sales=False)
        # Two gain records expected (one per close).
        self.assertEqual(len(result['transactions']), 2)
        for g in result['transactions']:
            self.assertFalse(g.get('tainted', False))
            self.assertEqual(g['direction'], 'SHORT')


class TestCase11MultiTaxYear(unittest.TestCase):
    """Case #11: phantom in prior year, clean by current year — current
    year's gains are clean once the pool drains."""

    def test_phantom_2023_clean_by_2024(self):
        txs = [
            # 2023: phantom sell that drains the synthetic opening
            _tx('BUYSELL', '2023-09-15', 'AAPL.US', -100, 180.0, 17991, account='Margin'),
            # 2024: fresh clean buy + sell
            _tx('BUYSELL', '2024-01-15', 'AAPL.US', 200, 170.0, 34009, account='Margin'),
            _tx('BUYSELL', '2024-09-10', 'AAPL.US', -100, 200.0, 19991, account='Margin'),
        ]
        new_txs, _ = synthesize_openings(txs, phantoms={('AAPL.US', 'Margin')})
        result = CanadaTaxRules().compute_gains(new_txs, detect_wash_sales=False)
        by_date = sorted(result['transactions'], key=lambda g: g['date'])
        # 2023 disposition: tainted (consumed the phantom opening).
        self.assertEqual(by_date[0]['date'][:4], '2023')
        self.assertTrue(by_date[0].get('tainted'))
        # 2024 disposition: clean (pool drained, fresh ACB).
        self.assertEqual(by_date[1]['date'][:4], '2024')
        self.assertFalse(by_date[1].get('tainted', False))


class TestCase12RoundingEdge(unittest.TestCase):
    """Case #12: tiny phantom contribution still taints. Defensive against
    code that 'rounds away' small phantom shares."""

    def test_tiny_phantom_still_taints(self):
        txs = [
            _tx('BUYSELL', '2024-01-15', 'BTC.US', 0.001, 60000.0, 60, account='Margin', currency='USD'),
            _tx('BUYSELL', '2024-03-20', 'BTC.US', -0.0001, 65000.0, 6.5, account='Margin', currency='USD'),
            _tx('BUYSELL', '2024-04-10', 'BTC.US', -0.0009, 70000.0, 63, account='Margin', currency='USD'),
        ]
        # Wait — these don't go negative on their own. Force a phantom by
        # listing the pair AND having the first sell precede an undisclosed
        # opening. Simulate by making the running position dip negative:
        txs2 = [
            _tx('BUYSELL', '2024-01-15', 'BTC.US', -0.0001, 60000.0, 6, account='Margin', currency='USD'),
        ]
        new_txs, _ = synthesize_openings(txs2, phantoms={('BTC.US', 'Margin')})
        result = CanadaTaxRules().compute_gains(new_txs, detect_wash_sales=False)
        self.assertTrue(result['transactions'][0].get('tainted'))


class TestSuperficialLossWarning(unittest.TestCase):
    """Cross-year ±30-day superficial-loss interaction warnings."""

    def test_warning_emitted_for_in_year_loss_near_tainted(self):
        clean_losses = [{
            'date': '2025-01-05', 'symbol': 'AAPL.US', 'account': 'Margin',
            'gain': -500.0,
        }]
        all_tainted = [{
            'date': '2024-12-28', 'symbol': 'AAPL.US', 'account': 'LIRA',
            'qty': 100, 'proceeds': 18000,
        }]
        warnings = detect_superficial_loss_warnings(clean_losses, all_tainted)
        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertEqual(w['loss_date'], '2025-01-05')
        self.assertEqual(w['symbol'], 'AAPL.US')
        self.assertEqual(len(w['tainted_dispositions']), 1)
        self.assertEqual(w['tainted_dispositions'][0]['days_offset'], -8)

    def test_no_warning_when_outside_window(self):
        clean_losses = [{
            'date': '2025-02-10', 'symbol': 'AAPL.US', 'account': 'Margin',
            'gain': -500.0,
        }]
        all_tainted = [{
            'date': '2024-12-28', 'symbol': 'AAPL.US', 'account': 'LIRA',
            'qty': 100, 'proceeds': 18000,
        }]
        # 44 days apart — outside the 30-day window.
        warnings = detect_superficial_loss_warnings(clean_losses, all_tainted)
        self.assertEqual(warnings, [])

    def test_no_warning_for_different_symbol(self):
        clean_losses = [{
            'date': '2025-01-05', 'symbol': 'AAPL.US', 'account': 'Margin',
            'gain': -500.0,
        }]
        all_tainted = [{
            'date': '2024-12-28', 'symbol': 'NVDA.US', 'account': 'LIRA',
            'qty': 100, 'proceeds': 18000,
        }]
        self.assertEqual(detect_superficial_loss_warnings(clean_losses, all_tainted), [])

    def test_multiple_tainted_within_window(self):
        clean_losses = [{
            'date': '2025-01-15', 'symbol': 'AAPL.US', 'account': 'Margin',
            'gain': -500.0,
        }]
        all_tainted = [
            {'date': '2024-12-28', 'symbol': 'AAPL.US', 'account': 'LIRA', 'qty': 50, 'proceeds': 9000},
            {'date': '2025-02-05', 'symbol': 'AAPL.US', 'account': 'LIRA', 'qty': 50, 'proceeds': 9100},
        ]
        warnings = detect_superficial_loss_warnings(clean_losses, all_tainted)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(warnings[0]['tainted_dispositions']), 2)


class TestSuggestPhantomsYearScope(unittest.TestCase):
    """Year scoping for --suggest-phantoms with --year (Phase 2)."""

    def test_year_scoped_filtering_in_caller_logic(self):
        # Detection produces a candidate. Year-scoping happens in the CLI
        # by filtering the candidate list against (symbol, account) pairs
        # with in-year dispositions. This unit-tests the filter shape.
        txs = [
            _tx('BUYSELL', '2023-09-15', 'AAPL.US', -100, 180.0, 17991, account='Margin'),
            _tx('BUYSELL', '2024-06-10', 'MSFT.US', -50, 400.0, 19998, account='Margin'),
        ]
        candidates = detect_phantoms(txs)
        self.assertEqual({c.symbol for c in candidates}, {'AAPL.US', 'MSFT.US'})
        # Caller scopes to year 2024:
        year_str = '2024'
        in_year_pairs = {
            (tx.symbol, tx.account) for tx in txs
            if tx.action in ('BUYSELL', 'ASSIGN') and tx.quantity < 0 and tx.date.startswith(year_str)
        }
        scoped = [c for c in candidates if (c.symbol, c.account) in in_year_pairs]
        self.assertEqual({c.symbol for c in scoped}, {'MSFT.US'})


class TestSynthesisOpeningPlacement(unittest.TestCase):
    def test_opening_inserted_before_earliest_transaction_for_symbol(self):
        txs = [
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='LIRA'),
        ]
        new_txs, log = synthesize_openings(txs, phantoms={('AAPL.US', 'LIRA')})
        openings = [t for t in new_txs if t.action == 'OPENING_BALANCE']
        self.assertEqual(len(openings), 1)
        self.assertEqual(openings[0].symbol, 'AAPL.US')
        self.assertEqual(openings[0].account, 'LIRA')
        self.assertAlmostEqual(openings[0].quantity, 100.0)
        # Anchored ON the first AAPL.US transaction's date: every sort
        # that sees an OPENING_BALANCE now has an explicit OB-first rung
        # (event_sort_key profiles), so the old fabricated day-early date
        # is gone.
        self.assertEqual(openings[0].date, '2024-03-20')
        self.assertEqual(openings[0].time, '00:00:00')

    def test_anchor_date_is_per_account_not_per_symbol(self):
        """A symbol held in two accounts — full history in Margin
        (earliest 2021), a phantom gap in LIRA (data starts 2024) — must
        anchor the LIRA opening to LIRA's own earliest date, not Margin's.
        Regression: earliest_date was keyed by symbol only, stamping the
        synthetic opening years before any real LIRA activity."""
        txs = [
            _tx('BUYSELL', '2021-01-05', 'AAPL.US', 50, 130.0, 6500, account='Margin'),
            _tx('BUYSELL', '2024-03-20', 'AAPL.US', -100, 180.0, 17991, account='LIRA'),
        ]
        new_txs, _ = synthesize_openings(txs, phantoms={('AAPL.US', 'LIRA')})
        opening = next(t for t in new_txs if t.action == 'OPENING_BALANCE')
        self.assertEqual(opening.account, 'LIRA')
        # Anchored to LIRA's own earliest row (2024), NOT Margin's 2021.
        self.assertEqual(opening.date, '2024-03-20')


class TestSplitRename(unittest.TestCase):
    """A SPLIT-RENAME (s.85.1(5) rollover merger) moves the pool onto the
    new ticker. The acquirer's shares arrive via the rename, not a BUY, so
    a naive per-symbol walk would read a later sale as a phantom short."""

    def _split(self, date, symbol, symbol_new, factor, account='Margin', currency='USD'):
        return TaxTransaction(
            action='SPLIT', date=date, symbol=symbol, symbol_new=symbol_new,
            quantity=factor, account=account, currency=currency,
        )

    def test_rename_target_sale_not_flagged_short(self):
        # 15 HES → (rollover) → 15 CVX, then CVX sold. Nets to zero; the
        # detector must NOT flag CVX as a phantom short.
        txs = [
            _tx('BUYSELL', '2025-01-02', 'HES.US', 15, 10.0, 150.0),
            self._split('2025-07-21', 'HES.US', 'CVX.US', 1.0),
            _tx('BUYSELL', '2025-12-23', 'CVX.US', -15, 150.0, 2250.0),
        ]
        self.assertEqual(detect_phantoms(txs), [])

    def test_genuine_short_after_rename_still_flagged(self):
        # Selling MORE than the rename delivered is a real short.
        txs = [
            _tx('BUYSELL', '2025-01-02', 'HES.US', 15, 10.0, 150.0),
            self._split('2025-07-21', 'HES.US', 'CVX.US', 1.0),
            _tx('BUYSELL', '2025-12-23', 'CVX.US', -20, 150.0, 3000.0),
        ]
        cands = detect_phantoms(txs)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].symbol, 'CVX.US')
        self.assertAlmostEqual(cands[0].peak_short, -5.0)


class TestWalkOrderingMatchesEngine(unittest.TestCase):
    """The phantom walks now order events like the engines do (splits
    effective at market open -> before same-date executions, regardless of
    clock stamp). IB stamps corp actions ~20:25; the old bare (date, time)
    walk put such a split AFTER the day's trades, sizing the opening in
    pre-split units while the engine replayed it post-split."""

    def test_evening_stamped_split_with_same_day_sale(self):
        from taxjson.lib.core import CanadaTaxRules
        from taxjson.lib.phantom_holdings import synthesize_openings
        txs = [
            _tx('BUYSELL', '2026-03-05', 'X.US', 10, 10.0, 100.0),
            TaxTransaction(action='SPLIT', date='2026-03-10',
                           time='20:25:00', symbol='X.US', quantity=2.0,
                           account='Margin', currency='USD'),
            _tx('BUYSELL', '2026-03-10', 'X.US', -120, 6.0, 720.0),
        ]
        out, applied = synthesize_openings(txs, {('X.US', 'Margin')})
        entry = next(a for a in applied if a['inserted'])
        # Post-split-aware walk: the sale of 120 is 60 opening-date units,
        # so the deficit is 50 — the old ordering computed 110 (2.2x).
        self.assertAlmostEqual(entry['opening_qty'], 50.0, places=6)
        # Engine replay agrees: (50 opening + 10 bought) * 2 - 120 = 0.
        res = CanadaTaxRules().compute_gains(out)
        qty = sum(h['qty'] for h in res['inventory']
                  if h['symbol'] == 'X.US')
        self.assertAlmostEqual(qty, 0.0, places=6)


if __name__ == '__main__':
    unittest.main()

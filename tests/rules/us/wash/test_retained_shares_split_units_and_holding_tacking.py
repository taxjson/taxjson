"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import unittest

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestWashUnitFactorSurvivesLaterRename(unittest.TestCase):
    """Audit 2026-08 #1: `_rep_units_factor` queried the split schedule
    under the loss transaction's raw symbol, but SplitTimeline migrates
    a symbol's schedule onto the rename target at BUILD time — so any
    rename anywhere in the input emptied the raw symbol's schedule and
    the factor collapsed to 1.0, mis-scaling §1091 matches for losses
    evaluated under the pre-rename ticker.

    Formerly: test_audit_2026_08_fixes.TestWashUnitFactorSurvivesLaterRename"""

    def _txs(self, with_later_rename: bool):
        txs = [
            # Lot A — fully consumed by the sale (its rep is excluded
            # by quantity accounting, not by this test's target path).
            TaxTransaction(action='BUYSELL', date='2025-01-10',
                           symbol='OLD.US', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            # Lot B — retained; the replacement (100 pre-split shares).
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='OLD.US', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            # 2:1 split between the replacement buy and the loss sale.
            TaxTransaction(action='SPLIT', date='2025-02-10',
                           symbol='OLD.US', quantity=2.0, currency='USD',
                           account='M'),
            # Sell 200 post-split shares (all of lot A) at a $600 loss.
            TaxTransaction(action='BUYSELL', date='2025-02-15',
                           symbol='OLD.US', quantity=-200.0, price=2.0,
                           net_amount=400.0, currency='USD', account='M'),
        ]
        if with_later_rename:
            # Rename months AFTER the wash sale — must not change it.
            txs.append(TaxTransaction(
                action='SPLIT', date='2025-06-01', symbol='OLD.US',
                quantity=1.0, symbol_new='NEW.US', currency='USD',
                account='M'))
        return txs

    def _disallowed(self, txs):
        result = USATaxRules().compute_gains(txs)
        return sum(g.get('disallowed_amount', 0.0)
                   for g in result['transactions']
                   if g.get('raw_gain', 0.0) < 0)

    def test_later_rename_does_not_change_disallowance(self):
        without = self._disallowed(self._txs(with_later_rename=False))
        with_rename = self._disallowed(self._txs(with_later_rename=True))
        # 100 retained pre-split shares × factor 2.0 cover the full
        # 200-share loss: $600 disallowed either way.
        self.assertAlmostEqual(without, 600.0, places=2)
        self.assertAlmostEqual(
            with_rename, 600.0, places=2,
            msg="a rename AFTER the wash sale emptied the raw symbol's "
                "split schedule and halved the disallowance")


class TestRetainedSharesAreReplacements(unittest.TestCase):
    """Audit 2026-08 #10: `find_replacements_in_window` excluded the
    sold lot's entire originating purchase by tx id, so the RETAINED
    shares of a partially-sold order never matched as §1091
    replacements — the disallowance depended purely on order
    granularity (one 200-share order vs two 100-share orders).

    Formerly: test_audit_2026_08_fixes.TestRetainedSharesAreReplacements"""

    def _disallowed(self, txs):
        result = USATaxRules().compute_gains(txs)
        return sum(g.get('disallowed_amount', 0.0)
                   for g in result['transactions']
                   if g.get('raw_gain', 0.0) < 0)

    def test_partial_sale_matches_retained_shares(self):
        one_order = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=200.0, price=100.0,
                           net_amount=20000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=-100.0, price=95.0,
                           net_amount=9500.0, currency='USD', account='M'),
        ]
        two_orders = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           time='09:30:00', symbol='AAPL', quantity=100.0,
                           price=100.0, net_amount=10000.0,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           time='09:31:00', symbol='AAPL', quantity=100.0,
                           price=100.0, net_amount=10000.0,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=-100.0, price=95.0,
                           net_amount=9500.0, currency='USD', account='M'),
        ]
        d1 = self._disallowed(one_order)
        d2 = self._disallowed(two_orders)
        # 100 shares retained, acquired 17 days before the loss sale —
        # a $500 wash disallowance either way.
        self.assertAlmostEqual(d2, 500.0, places=2)
        self.assertAlmostEqual(
            d1, 500.0, places=2,
            msg="identical economics must not diverge on order "
                "granularity: the buggy one-order result was $0")

    def test_stale_purchase_outside_window_still_allowed(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-06-01',
                           symbol='AAPL', quantity=200.0, price=100.0,
                           net_amount=20000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=-100.0, price=95.0,
                           net_amount=9500.0, currency='USD', account='M'),
        ]
        self.assertAlmostEqual(self._disallowed(txs), 0.0, places=2,
                               msg="retained shares acquired outside the "
                                   "±30-day window are not replacements")


class TestShelteredSellNotFullShortReplacement(unittest.TestCase):
    """Audit 2026-08 #11: a sheltered/affiliated SELL was registered as
    a short-side §1091 replacement at FULL quantity even when it merely
    closed a long in its own book — a routine RRSP sale permanently
    destroyed a taxable short-cover loss.

    Formerly: test_audit_2026_08_fixes.TestShelteredSellNotFullShortReplacement"""

    def test_sheltered_long_sale_does_not_kill_short_cover_loss(self):
        taxable = [
            # Short 100 @ 10, cover @ 15 → -$500 loss.
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
        ]
        sheltered = [
            # RRSP holds a long 100 and sells it ordinarily Feb 10 —
            # closing a long, not opening a short.
            TaxTransaction(action='BUYSELL', date='2024-06-01',
                           symbol='AAPL', quantity=100.0, price=8.0,
                           net_amount=800.0, currency='USD', account='R'),
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='R'),
        ]
        result = USATaxRules().compute_gains(
            taxable, sheltered_transactions=sheltered)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0.0) < 0)
        self.assertAlmostEqual(loss['raw_gain'], -500.0, places=2)
        self.assertAlmostEqual(
            loss.get('disallowed_amount', 0.0), 0.0, places=2,
            msg="an ordinary sheltered long-sale was booked as a short "
                "replacement and permanently destroyed the loss")

    def test_sheltered_overdrawn_sale_warns_not_denies(self):
        """Deep-audit 2026-08 refinement: a sheltered SELL exceeding its
        recorded balance can't be a real short-open (registered
        accounts can't short) — it's missing history. It must warn and
        NOT permanently deny the taxable loss."""
        import io
        from contextlib import redirect_stderr
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='R'),
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = USATaxRules().compute_gains(
                taxable, sheltered_transactions=sheltered)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0.0) < 0)
        self.assertAlmostEqual(
            loss.get('permanently_disallowed', 0.0), 0.0, places=2,
            msg="a routine cross-IRA in-kind sale permanently "
                "destroyed a real taxable loss")
        self.assertIn("missing acquisition history", buf.getvalue())

    def test_affiliated_short_open_still_triggers(self):
        """Affiliated (spouse) accounts CAN short — a genuine
        affiliated short-open keeps the disallowance."""
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
        ]
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='S'),
        ]
        result = USATaxRules().compute_gains(
            taxable, affiliated_transactions=affiliated)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0.0) < 0)
        self.assertAlmostEqual(
            loss.get('disallowed_amount', 0.0), 500.0, places=2)


class TestRepCapacityUnitsAcrossSplits(unittest.TestCase):
    """Deep-audit 2026-08 #4: the FIFO partial-pop decremented
    replacement capacity in TODAY's units while the capacity is stored
    in the rep's own trade-date units — with a split between the buy
    and the loss sale, wash quantities skewed both directions.

    Formerly: test_audit_2026_08_fixes.TestRepCapacityUnitsAcrossSplits"""

    def _disallowed(self, txs):
        result = USATaxRules().compute_gains(txs)
        return sum(g.get('disallowed_amount', 0.0)
                   for g in result['transactions']
                   if g.get('raw_gain', 0.0) < 0)

    def test_forward_split_keeps_retained_share_disallowance(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='SPLIT', date='2025-01-10',
                           symbol='AAPL', quantity=2.0, currency='USD',
                           account='M'),
            # Sell HALF the post-split position at a loss; the retained
            # 100 shares (50 rep-units × factor 2) are replacements.
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=-100.0, price=4.0,
                           net_amount=400.0, currency='USD', account='M'),
        ]
        control = [
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=200.0, price=5.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=-100.0, price=4.0,
                           net_amount=400.0, currency='USD', account='M'),
        ]
        d_split = self._disallowed(txs)
        d_control = self._disallowed(control)
        self.assertAlmostEqual(d_control, 100.0, places=2)
        self.assertAlmostEqual(
            d_split, 100.0, places=2,
            msg="raw-unit decrement zeroed the rep across a 2:1 split "
                "and the retained-share disallowance vanished")

    def test_reverse_split_does_not_overdisallow(self):
        txs = [
            # Lot A: 100 shares @ $1 (rep capacity 100 pre-split units).
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           time='09:30:00', symbol='ZZZ', quantity=100.0,
                           price=1.0, net_amount=100.0, currency='USD',
                           account='M'),
            # Lot B: 10 shares @ $10.
            TaxTransaction(action='BUYSELL', date='2025-01-03',
                           time='09:30:00', symbol='ZZZ', quantity=10.0,
                           price=10.0, net_amount=100.0, currency='USD',
                           account='M'),
            # 1:10 reverse split → lot A = 10 sh, lot B = 1 sh.
            TaxTransaction(action='SPLIT', date='2025-01-10',
                           symbol='ZZZ', quantity=0.1, currency='USD',
                           account='M'),
            # Sell 9 of lot A's 10 post-split shares at a $45 loss;
            # retained replacements = 1 sh (lot A) + 1 sh (lot B).
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='ZZZ', quantity=-9.0, price=5.0,
                           net_amount=45.0, currency='USD', account='M'),
        ]
        d = self._disallowed(txs)
        # Loss $45 over 9 shares = $5/sh; 2 replacement shares → $10.
        self.assertAlmostEqual(
            d, 10.0, places=2,
            msg="raw-unit decrement left phantom 9.1-share capacity on "
                "lot A and disallowed the full $45")


@rule(R.US_WASH_10)
class TestSection1223TackingExcludesGap(unittest.TestCase):
    """Deep-audit 2026-08 #9: §1223(3) tacking inherited the wash-sold
    lot's calendar acquisition date, wrongly counting the sale→rebuy
    gap toward the holding period — flipping SHORT_TERM to LONG_TERM
    (rate category) near the anniversary.

    Formerly: test_audit_2026_08_fixes.TestSection1223TackingExcludesGap"""

    def test_gap_days_do_not_count_toward_term(self):
        txs = [
            # Held 2025-01-02 → 2025-12-20 = 352 days, sold at a loss.
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=100.0, price=20.0,
                           net_amount=2000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-12-20',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
            # Rebuy after a 21-day gap (wash replacement), then sell.
            TaxTransaction(action='BUYSELL', date='2026-01-10',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2026-01-20',
                           symbol='AAPL', quantity=-100.0, price=16.0,
                           net_amount=1600.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        final = next(g for g in result['transactions']
                     if g['date'] == '2026-01-20')
        # Tacked holding = 352 (prior) + 10 (own) = 362 days.
        self.assertEqual(
            final['term'], 'SHORT_TERM',
            "the raw-date inheritance counted the 21-day gap and "
            "reported LONG_TERM at 383 days")
        self.assertEqual(final['days_held'], 362)


if __name__ == '__main__':
    unittest.main()

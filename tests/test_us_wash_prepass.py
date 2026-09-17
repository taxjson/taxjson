"""§1091 pre-pass regressions for USATaxRules.

The pre-pass walks `transactions + sheltered + affiliated` once before
the main FIFO pass to build the long_replacements / short_replacements
lists. Two earlier bugs caused the pre-pass to disagree with the main
FIFO pass about the running position:

  1. OPENING_BALANCE and SPLIT were skipped from `net_qty_state`, but
     the main pass DOES update inventory_long/short for both. A SELL
     drawing from a phantom OPENING_BALANCE long was therefore
     classified by the pre-pass as a short-opening (net_qty_state was
     0) and emitted a fake short-replacement that the main pass never
     produced — a later real short-loss could be disallowed against it.

  2. Sheltered and affiliated events were folded into `net_qty_state`,
     but the main pass walks taxable-only. A sheltered BUY shifted
     `net_qty_state` positive so a subsequent taxable SELL was
     mis-classified as a long-close — its real short-open (the main
     pass had no taxable long inventory to close against) never made
     it into short_replacements, and a real wash sale on a later
     short-loss got silently missed.

These tests assert that the pre-pass and main pass now agree.
"""
import unittest

from taxjson.lib.core import TaxTransaction, USATaxRules


class TestPrePassOpeningBalance(unittest.TestCase):
    def test_opening_balance_long_drained_then_real_short_loss_allowed(self):
        """OB long → SELL drains it → fresh short → short-close at loss.
        The only short-open in the loss's ±30-day window is the loss's
        own open (excluded by ID). With the OPENING_BALANCE skip bug,
        the SELL that drained the OB was registered as a phantom
        short-replacement (because pre-pass net_qty was 0), and the
        real short-loss was wrongly disallowed against it."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='OPENING_BALANCE', date='2025-01-01',
                           symbol='AAPL', quantity=100.0, currency='USD',
                           account='M'),
            # Drains the phantom 100-share lot — tainted long-close.
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=-100.0, price=100.0,
                           net_amount=10000.0, currency='USD', account='M'),
            # Real short-open after inventory is empty.
            TaxTransaction(action='BUYSELL', date='2025-01-20',
                           symbol='AAPL', quantity=-100.0, price=100.0,
                           net_amount=10000.0, currency='USD', account='M'),
            # Buy-to-cover at a loss within ±30d of both prior dates.
            TaxTransaction(action='BUYSELL', date='2025-02-05',
                           symbol='AAPL', quantity=100.0, price=120.0,
                           net_amount=12000.0, currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        loss = next(g for g in gains
                    if g['direction'] == 'SHORT' and not g.get('tainted'))
        self.assertAlmostEqual(loss['raw_gain'], -2000.0, places=2)
        # The loss must be ALLOWED — no replacement short exists other
        # than the loss's own open (which the matcher excludes by ID).
        self.assertAlmostEqual(loss.get('disallowed_amount', 0.0), 0.0,
                               places=2,
                               msg="OPENING_BALANCE skip caused a phantom "
                                   "short-replacement to disallow this loss")


class TestPrePassShelteredPollution(unittest.TestCase):
    def test_sheltered_buy_does_not_mask_taxable_short_replacement(self):
        """Sheltered BUY at t0; taxable SELL at t1 (real short-open
        since there's no taxable long inventory); taxable buy-to-cover
        at t2 at a loss; taxable SHORT-open at t3 inside the wash
        window. With the pollution bug, the sheltered BUY pushed
        pre-pass net_qty positive so the t1 SELL was mis-classified as
        long-close and never added to short_replacements — the t2 loss
        then missed its real wash trigger at t3."""
        rules = USATaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=100.0,
                           net_amount=10000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-20',
                           symbol='AAPL', quantity=100.0, price=120.0,
                           net_amount=12000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-25',
                           symbol='AAPL', quantity=-100.0, price=110.0,
                           net_amount=11000.0, currency='USD', account='M'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=100.0, price=100.0,
                           net_amount=10000.0, currency='USD',
                           account='IRA'),
        ]
        result = rules.compute_gains(taxable,
                                     sheltered_transactions=sheltered)
        loss = next(g for g in result['transactions']
                    if g['date'] == '2025-02-20'
                    and g.get('action') not in ('DIVIDEND',
                                                'DIVIDEND_IN_LIEU'))
        self.assertAlmostEqual(loss['raw_gain'], -2000.0, places=2)
        # The 2/25 short-open IS a §1091 replacement → loss disallowed.
        self.assertGreater(loss.get('disallowed_amount', 0.0), 1.0,
                           "Sheltered BUY polluted the taxable pre-pass "
                           "net_qty and hid this wash sale.")


class TestPrePassSplit(unittest.TestCase):
    def test_split_rescales_pre_pass_net_qty(self):
        """SPLIT must scale `net_qty_state` by the ratio in the §1091
        pre-pass so a post-split SELL is classified against post-split
        inventory. Pre-fix the SPLIT was skipped, leaving net_qty at
        pre-split units — the first post-split SELL got misregistered
        as (small close + large short-open) in `short_replacements`,
        and a real short-loss within the window matched against the
        phantom replacement and got wrongly disallowed.

        Repro: BUY 10 pre-split → SPLIT 10-for-1 → SELL 100 (drains
        post-split inventory at a loss) → SELL 50 more (real short
        opens) → BUY 50 to cover at a loss. The cover loss must be
        ALLOWED (the only other short-open in the window is its own,
        excluded by ID). Pre-fix the SELL-100 left a 90-share phantom
        in `short_replacements` that matched the cover loss."""
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01',
                           symbol='AAPL.US', quantity=10, price=100.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='SPLIT', date='2025-02-15',
                           symbol='AAPL.US', quantity=10.0,
                           currency='USD', account='M'),
            # Drains the post-split long pool at a loss.
            TaxTransaction(action='BUYSELL', date='2025-02-20',
                           symbol='AAPL.US', quantity=-100, price=5.0,
                           net_amount=500.0, currency='USD', account='M'),
            # Real short-open (inventory is empty after the SELL above).
            TaxTransaction(action='BUYSELL', date='2025-02-25',
                           symbol='AAPL.US', quantity=-50, price=5.0,
                           net_amount=250.0, currency='USD', account='M'),
            # Cover the short at a loss within ±30d of both prior dates.
            TaxTransaction(action='BUYSELL', date='2025-03-10',
                           symbol='AAPL.US', quantity=50, price=7.0,
                           net_amount=350.0, currency='USD', account='M'),
        ]
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        cover = next(g for g in gains
                     if g['date'] == '2025-03-10' and g['direction'] == 'SHORT')
        self.assertAlmostEqual(cover['raw_gain'], -100.0, places=2)
        self.assertAlmostEqual(cover.get('disallowed_amount', 0.0), 0.0,
                               places=2,
                               msg="SPLIT must rescale pre-pass net_qty — "
                                   "otherwise the first post-split SELL "
                                   "leaves a phantom short-replacement.")


if __name__ == '__main__':
    unittest.main()

"""Pins for surviving mutants from the 2026-09 mutation audit.

Each test targets ONE mutant that survived the existing suite — a
behavior the engines/pipeline get right but nothing asserted. Style
mirrors test_engine_invariants.py: hand-built TaxTransaction books,
engines run via get_tax_rules(...).compute_gains with stderr
redirected, EXACT denied/deferred/gain amounts asserted.

Engine pins (lib/core.py + lib/corporate_timeline.py):
  M1  cumulative_factor's equal-date early return (a condition-negation
      mutant makes the function return 1.0 for ALL date pairs,
      disabling split scaling in the class-factor conversion the US
      engine uses at wash match time).
  M2  a PLAIN split (empty symbol_new) must not union symbols into one
      wash alias class (the `if target and target != sym` guard).
  M3  SHORT-direction trigger sign: an in-window sheltered
      sell-to-open is the replacement for a short covered at a loss.
  M4  post-loss triggers absorb the denial BEFORE pre-loss triggers —
      the partition decides how much is deferred vs permanent.
  M5  `<= end_window_date` in the taxable-backing balance: backing
      bought EXACTLY on day +30 makes the denial a DEFERRAL.
  M6  a PRE-loss trigger's wash ADJUST is dated loss-sale + 1s, so
      sales between the trigger and the loss use the UNBUMPED basis.

Pipeline pins (lib/pipeline.py transfer netting):
  M7  pair-gap boundary: legs exactly 35 days apart share a segment
      (and net); 36 days apart split into two surviving segments.
  M8  a main-book SPLIT strictly between the legs blocks netting in
      both the per-account dropper and the cross-account netter.
  M9  segment-span cap: a zero-net gap-chain spanning exactly 45 days
      still nets; 46 days is refused outright.

Round-five transfer-region pins (lib/pipeline.py +
bin/taxjson_export.py — survivors of the 2026-09 round-five mutation
audit, adapted to the post-audit restatement/evidence fixes):
  M10 a declared pair plus a gap-chained far pair with NO nearby
      taxable trade nets as one quiet segment (the bless-pad
      narrowing only engages when a near trade exists).
  M11 TRANSFER rows listed out of date order segment identically —
      the dropper sorts before clustering.
  M12 _ATTEST_BLESS_PAD_DAYS = 7 boundary: a non-declared leg 7 days
      from the declared leg is inside the blessing (segment nets);
      8 days is outside, and the unbalanced blessed subset refuses
      the WHOLE segment.
  M13 _RESTATEMENT_CHAIN_PAD_DAYS = 3 boundary: per-symbol clusters
      4 days apart do not chain into a >=3-symbol restatement event
      (all rows refused near trades); 3 days apart they do (all net).
  M14 the SPLIT-in-span check is symbol-scoped, both directions: a
      same-symbol SPLIT between the legs blocks netting; an
      UNRELATED symbol's SPLIT does not.
  M15 the journal-candidate pairing tolerance is RELATIVE
      (1e-6 * qty): fractional broker dust (500 vs 500.0002) still
      pairs and warns; clearly-unequal quantities never pair.
  M16 _apply_transfer_evidence PARTIAL depot flip: cost moves
      pro-rata (frac = move/qty) on BOTH buckets and BOTH
      per-currency maps, and the applied move list is returned.
  M17 no-op guards: tmap=None, and an out-leg whose symbol has no
      holdings bucket, both no-op without crashing.
  M18 prune guard: a source bucket with shares LEFT (qty > 0,
      near-zero residual cost) is never pruned from holdings.
  M19 evidence symbols fold through tmap.journal BEFORE netting, so
      a JOURNAL pair's own legs land on one key and cancel — the
      already-folded bucket is never evidence-moved on top
      (post-audit behavior).
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.bin.taxjson_export import _apply_transfer_evidence
from taxjson.bin.taxjson_ticker_map import TickerMap
from taxjson.lib.core import TaxTransaction, get_tax_rules
from taxjson.lib.pipeline import (MANUAL_TRANSFER_DECLARATION,
                                  _drop_self_cancelling_transfers,
                                  _net_cross_account_transfers)


def run_engine(country, txs, shel=(), **kw):
    with redirect_stderr(io.StringIO()):
        return get_tax_rules(country).compute_gains(
            list(txs), sheltered_transactions=list(shel), **kw)


def T(**kw):
    d = dict(action="BUYSELL", time="09:30:00", symbol="Q.TO",
             currency="CAD", account="m")
    d.update(kw)
    return TaxTransaction(**d)


def gain_rec(result, date, symbol=None):
    """The single realized-gain record on `date` (optionally filtered
    by symbol)."""
    recs = [g for g in result["transactions"]
            if g.get("qty") and "gain" in g and g["date"] == date
            and (symbol is None or g.get("symbol") == symbol)]
    assert len(recs) == 1, recs
    return recs[0]


def denied_total(result):
    return round(sum(float(w.get("disallowed_amount") or 0)
                     for w in result.get("wash_sales") or []), 2)


def parked_deferral(result):
    return round(sum(float(r.get("deferred_wash") or 0)
                     for r in result.get("inventory") or []), 2)


def realized_total(result):
    return round(sum(float(g["gain"] or 0)
                     for g in result["transactions"]
                     if g.get("qty") and "gain" in g
                     and not g.get("tainted")), 2)


class TestCumulativeFactorEqualDateReturn(unittest.TestCase):
    """M1 — corporate_timeline.cumulative_factor's
    `if from_date == to_date: return 1.0`. Negating the condition makes
    the function return 1.0 unconditionally (for equal dates the loop
    body is empty anyway), which disables split scaling everywhere the
    class-factor conversion is used — notably the US engine's
    _rep_units_factor at §1091 match time."""

    def test_plain_split_inside_us_wash_window_scales_replacement(self):
        # Loss of $2,000 on 100 pre-split shares ($20/sh); 2:1 SPLIT
        # two days later; 50 POST-split shares (= 25 pre-split)
        # rebought in-window and held. Denied = 25 x $20 = $500.
        # With scaling disabled the 50 post-split shares match at face
        # value and the denial doubles to $1,000.
        txs = [T(date="2025-01-06", quantity=100, net_amount=10000.0,
                 symbol="Q.US", currency="USD"),
               T(date="2025-03-03", quantity=-100, net_amount=8000.0,
                 symbol="Q.US", currency="USD"),
               TaxTransaction(action="SPLIT", date="2025-03-05",
                              time="00:00:01", symbol="Q.US",
                              quantity=2.0, currency="USD",
                              account="m"),
               T(date="2025-03-10", quantity=50, net_amount=4100.0,
                 symbol="Q.US", currency="USD")]
        r = run_engine("usa", txs)
        loss = gain_rec(r, "2025-03-03")
        self.assertAlmostEqual(
            float(loss["disallowed_amount"]), 500.0, places=2)
        self.assertAlmostEqual(float(loss["gain"]), -1500.0, places=2)
        self.assertAlmostEqual(denied_total(r), 500.0, places=2)
        # The deferral parks on the still-held replacement lot.
        self.assertAlmostEqual(parked_deferral(r), 500.0, places=2)


class TestPlainSplitDoesNotMergeAliasClasses(unittest.TestCase):
    """M2 — SplitTimeline.from_transactions' rename-union guard
    `if target and target != sym:`. Dropping the `target` truthiness
    check unions every plain-splitting symbol with the '' pseudo-root,
    merging UNRELATED symbols into one wash alias class — an in-window
    buy of one then denies a loss on the other."""

    def test_unrelated_plain_splitters_stay_separate_classes(self):
        # B.TO: clean $2,000 loss, fully exited, nothing of B rebought.
        # A.TO: unrelated, bought inside B's window and held.
        # Both symbols have PLAIN splits (empty symbol_new) far from
        # the window. Identical property means the same symbol class:
        # no denial may fire.
        txs = [T(date="2025-01-06", quantity=100, net_amount=10000.0,
                 symbol="B.TO"),
               T(date="2025-03-03", quantity=-100, net_amount=8000.0,
                 symbol="B.TO"),
               T(date="2025-03-10", quantity=100, net_amount=8100.0,
                 symbol="A.TO"),
               TaxTransaction(action="SPLIT", date="2025-05-01",
                              time="00:00:01", symbol="A.TO",
                              quantity=2.0, currency="CAD",
                              account="m"),
               TaxTransaction(action="SPLIT", date="2025-06-01",
                              time="00:00:01", symbol="B.TO",
                              quantity=2.0, currency="CAD",
                              account="m")]
        r = run_engine("canada", txs)
        self.assertEqual(denied_total(r), 0.0)
        loss = gain_rec(r, "2025-03-03", symbol="B.TO")
        self.assertAlmostEqual(float(loss["gain"]), -2000.0, places=2)
        self.assertAlmostEqual(
            float(loss.get("disallowed_amount") or 0), 0.0, places=2)


class TestShortDirectionTriggerSign(unittest.TestCase):
    """M3 (re-premised 2026-09 Canada audit) — ITA s.54 needs an
    ACQUISITION of identical property still OWNED at day 30. A loss on
    covering a short is therefore superficial when a LONG purchase lands
    in the window and is held — and NOT when a new short is written
    (a sell-to-open acquires nothing; the old SHORT-direction branch was
    US §1091(e) logic). A sign-flip mutant on the acquisition test
    either denies on the re-short or misses the long rebuy."""

    MAIN = [dict(date="2025-01-06", quantity=-100, net_amount=10000.0),
            dict(date="2025-03-03", quantity=100, net_amount=12000.0)]

    def _run(self, replacement_qty):
        txs = [T(**kw) for kw in self.MAIN]
        shel = ([T(date="2025-03-10", quantity=replacement_qty,
                   net_amount=8000.0, account="rrsp")]
                if replacement_qty else [])
        return run_engine("canada", txs, shel)

    def test_sheltered_sell_to_open_does_not_deny_short_loss(self):
        r = self._run(-100)                       # rrsp WRITES/shorts: no acquisition
        loss = gain_rec(r, "2025-03-03")
        self.assertAlmostEqual(
            float(loss.get("disallowed_amount") or 0), 0.0, places=2)
        self.assertAlmostEqual(float(loss["gain"]), -2000.0, places=2)

    def test_sheltered_long_buy_denies_short_loss_permanently(self):
        r = self._run(100)                        # rrsp BUYS and holds
        loss = gain_rec(r, "2025-03-03")
        self.assertAlmostEqual(
            float(loss["disallowed_amount"]), 2000.0, places=2)
        self.assertAlmostEqual(
            float(loss["permanently_disallowed"]), 2000.0, places=2)
        self.assertAlmostEqual(float(loss["gain"]), 0.0, places=2)

    def test_no_replacement_no_denial(self):
        r = self._run(0)
        loss = gain_rec(r, "2025-03-03")
        self.assertAlmostEqual(
            float(loss.get("disallowed_amount") or 0), 0.0, places=2)
        self.assertAlmostEqual(float(loss["gain"]), -2000.0, places=2)


class TestPostLossTriggerAbsorbsFirst(unittest.TestCase):
    """M4 — the post-loss/pre-loss partition and allocation order in
    the Canada allocator. With BOTH a post-loss taxable rebuy and a
    pre-loss sheltered in-window buy, the post-loss (primary) trigger
    absorbs the denial first: 60 of the 100 denied shares defer on the
    taxable rebuy, only the 40-share remainder is permanent. A
    partition/order mutant hands the sheltered trigger 60 first,
    flipping the split to 800 deferred / 1200 permanent."""

    def test_deferred_and_permanent_split(self):
        txs = [T(date="2025-01-06", quantity=100, net_amount=10000.0),
               T(date="2025-03-03", quantity=-100, net_amount=8000.0),
               T(date="2025-03-10", quantity=60, net_amount=4860.0)]
        shel = [T(date="2025-02-20", quantity=60, net_amount=4900.0,
                  account="rrsp")]
        r = run_engine("canada", txs, shel)
        loss = gain_rec(r, "2025-03-03")
        self.assertAlmostEqual(
            float(loss["disallowed_amount"]), 2000.0, places=2)
        self.assertAlmostEqual(
            float(loss["permanently_disallowed"]), 800.0, places=2)
        self.assertAlmostEqual(parked_deferral(r), 1200.0, places=2)


class TestBackingOnDayThirtyDefers(unittest.TestCase):
    """M5 — the `<= end_window_date` bound in the taxable-backing
    balance (_bal_tax): a taxable backing purchase EXACTLY on day +30
    backs the deferral. A `<` mutant sees zero taxable backing and
    converts the whole (recoverable) deferral into a permanent
    denial."""

    def test_day_30_backing_purchase_defers_not_permanent(self):
        txs = [T(date="2025-01-06", quantity=100, net_amount=10000.0),
               T(date="2025-03-03", quantity=-100, net_amount=8000.0),
               # 2025-03-03 + 30d = 2025-04-02: last day of the window.
               T(date="2025-04-02", quantity=100, net_amount=8100.0)]
        wash = run_engine("canada", txs)
        nowash = run_engine("canada", txs, detect_wash_sales=False)
        loss = gain_rec(wash, "2025-03-03")
        self.assertAlmostEqual(
            float(loss["disallowed_amount"]), 2000.0, places=2)
        self.assertAlmostEqual(
            float(loss["permanently_disallowed"]), 0.0, places=2)
        # Recoverable: the full denial is parked on the still-held
        # inventory, and conservation holds with zero permanent loss:
        # realized(wash) - parked == realized(no-wash).
        self.assertAlmostEqual(parked_deferral(wash), 2000.0, places=2)
        self.assertAlmostEqual(
            realized_total(wash) - parked_deferral(wash),
        realized_total(nowash), places=2)


class TestPreLossAdjustDatedAfterLossSale(unittest.TestCase):
    """M6 — _mk_adjust dates a PRE-loss trigger's wash ADJUST at the
    loss sale + 1 second: the s.53(1)(f) basis bump takes effect AT
    the loss, strictly after the same-timestamp loss sale. Gains on
    sales BETWEEN the trigger and the loss must therefore be computed
    off the UNBUMPED pool. A mutant dating the ADJUST at the trigger
    bumps the pool early and corrupts the intermediate sale's gain
    (1666.67 -> 1355.56)."""

    def test_intermediate_sale_uses_unbumped_basis(self):
        txs = [T(date="2025-01-06", quantity=100, net_amount=10000.0),
               # Pre-loss in-window trigger (21 days before the loss).
               T(date="2025-02-10", quantity=50, net_amount=6000.0),
               # Intermediate sale at a gain: pool 150 sh @ 16,000 ->
               # ACB 106.67/sh -> gain 7000 - 5333.33 = 1666.67.
               T(date="2025-02-20", quantity=-50, net_amount=7000.0),
               # Loss sale: 60 of the remaining 100 @ 106.67 -> cost
               # 6400, proceeds 5000 -> loss 1400 ($23.33/sh).
               T(date="2025-03-03", quantity=-60, net_amount=5000.0)]
        r = run_engine("canada", txs)
        mid = gain_rec(r, "2025-02-20")
        self.assertAlmostEqual(float(mid["gain"]), 1666.67, places=2)
        self.assertAlmostEqual(
            float(mid.get("disallowed_amount") or 0), 0.0, places=2)
        # Still-held backing at +30 is the 40 retained shares:
        # denied = 40 x 23.33 = 933.33, all deferred.
        loss = gain_rec(r, "2025-03-03")
        self.assertAlmostEqual(
            float(loss["disallowed_amount"]), 933.33, places=2)
        self.assertAlmostEqual(
            float(loss["permanently_disallowed"]), 0.0, places=2)
        self.assertAlmostEqual(parked_deferral(r), 933.33, places=2)


# ============================================================================
# Pipeline pins — the transfer droppers, tested directly (mirroring
# tests/test_transfer_handling.py's _tx helper).
# ============================================================================
def _tx(action, date, symbol, qty, net=0.0, account='RRSP',
        currency='CAD', **extra):
    return TaxTransaction(action=action, date=date, symbol=symbol,
                          quantity=qty, net_amount=net, account=account,
                          currency=currency, **extra)


class TestTransferPairGapBoundary(unittest.TestCase):
    """M7 — _TRANSFER_PAIR_MAX_GAP_DAYS = 35: legs exactly 35 days
    apart share one segment and net to nothing; 36 days apart they are
    separate segments and BOTH survive. Pinned for both droppers."""

    def _legs(self, d2, a1='RRSP', a2='RRSP'):
        return [_tx('TRANSFER', '2026-03-01', 'Q.TO', +100, net=8000.0,
                    account=a1),
                _tx('TRANSFER', d2, 'Q.TO', -100, net=8000.0,
                    account=a2)]

    def test_per_account_35_days_nets(self):
        out, dropped = _drop_self_cancelling_transfers(
            self._legs('2026-04-05'))
        self.assertEqual(out, [])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 2)])

    def test_per_account_36_days_does_not_net(self):
        out, dropped = _drop_self_cancelling_transfers(
            self._legs('2026-04-06'))
        self.assertEqual(len(out), 2)
        self.assertEqual(dropped, [])

    def test_cross_account_35_days_nets(self):
        self.assertEqual(
            _net_cross_account_transfers(
                self._legs('2026-04-05', a2='RRSP2')), [])

    def test_cross_account_36_days_does_not_net(self):
        self.assertEqual(
            len(_net_cross_account_transfers(
                self._legs('2026-04-06', a2='RRSP2'))), 2)


class TestSplitInSpanBlocksNetting(unittest.TestCase):
    """M8 — a main-book SPLIT of the symbol dated strictly between the
    two legs leaves them in different share terms: netting must be
    refused by BOTH the per-account dropper (sheltered-side invocation
    with main_transactions) and the cross-account netter."""

    SPLIT = [_tx('SPLIT', '2026-03-05', 'Q.TO', 2.0, account='Margin')]

    def _legs(self, a1='RRSP', a2='RRSP'):
        return [_tx('TRANSFER', '2026-03-01', 'Q.TO', +100, net=8000.0,
                    account=a1),
                _tx('TRANSFER', '2026-03-10', 'Q.TO', -100, net=8000.0,
                    account=a2)]

    def test_dropper_main_book_split_blocks(self):
        out, dropped = _drop_self_cancelling_transfers(
            self._legs(), main_transactions=self.SPLIT)
        self.assertEqual(len(out), 2)
        self.assertEqual(dropped, [])

    def test_dropper_control_without_split_nets(self):
        out, dropped = _drop_self_cancelling_transfers(
            self._legs(), main_transactions=[])
        self.assertEqual(out, [])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 2)])

    def test_netter_main_book_split_blocks(self):
        out = _net_cross_account_transfers(
            self._legs(a2='RRSP2'), main_transactions=self.SPLIT)
        self.assertEqual(len(out), 2)

    def test_netter_control_without_split_nets(self):
        self.assertEqual(
            _net_cross_account_transfers(self._legs(a2='RRSP2')), [])


class TestSegmentSpanCapBoundary(unittest.TestCase):
    """M9 — _TRANSFER_SEGMENT_MAX_SPAN_DAYS = 45: a zero-net gap-chain
    (every hop <= 35 days) spanning exactly 45 days still nets; one
    day more and the whole segment is refused outright."""

    def _chain(self, last, accounts=('RRSP', 'RRSP', 'RRSP')):
        return [_tx('TRANSFER', '2026-03-01', 'Q.TO', -100, net=8000.0,
                    account=accounts[0]),
                _tx('TRANSFER', '2026-03-30', 'Q.TO', +50, net=4000.0,
                    account=accounts[1]),
                _tx('TRANSFER', last, 'Q.TO', +50, net=4000.0,
                    account=accounts[2])]

    def test_per_account_45_day_span_nets(self):
        out, dropped = _drop_self_cancelling_transfers(
            self._chain('2026-04-15'))
        self.assertEqual(out, [])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 3)])

    def test_per_account_46_day_span_refused(self):
        out, dropped = _drop_self_cancelling_transfers(
            self._chain('2026-04-16'))
        self.assertEqual(len(out), 3)
        self.assertEqual(dropped, [])

    def test_cross_account_45_day_span_nets(self):
        self.assertEqual(
            _net_cross_account_transfers(self._chain(
                '2026-04-15', accounts=('RRSP', 'RRSP2', 'RRSP'))), [])

    def test_cross_account_46_day_span_refused(self):
        self.assertEqual(
            len(_net_cross_account_transfers(self._chain(
                '2026-04-16', accounts=('RRSP', 'RRSP2', 'RRSP')))), 3)


# ============================================================================
# Round-five pins — _drop_self_cancelling_transfers' sheltered-context
# guards (declared attestations, restatement events, journal-candidate
# notes). Stderr is redirected: these paths print NOTEs by design.
# ============================================================================
def _drop(shel, main=None):
    """Run the dropper capturing stderr; returns (out, dropped, err)."""
    err = io.StringIO()
    with redirect_stderr(err):
        out, dropped = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
    return out, dropped, err.getvalue()


class TestDeclaredPlusFarPairNetsWhenQuiet(unittest.TestCase):
    """M10 — with NO taxable trade near the segment, `near` is False
    and the bless-pad narrowing must stay dormant: a declared June
    pair plus a gap-chained July pair (19 days later, same segment)
    nets as ONE quiet zero-net segment. A mutant that applies the
    declared-leg narrowing unconditionally strands the far pair."""

    def test_quiet_segment_with_declared_and_far_pair_fully_nets(self):
        shel = [_tx('TRANSFER', '2026-06-15', 'Q.TO', -100,
                    description=MANUAL_TRANSFER_DECLARATION),
                _tx('TRANSFER', '2026-06-16', 'Q.TO', +100,
                    description=MANUAL_TRANSFER_DECLARATION),
                _tx('TRANSFER', '2026-07-05', 'Q.TO', -50),
                _tx('TRANSFER', '2026-07-06', 'Q.TO', +50)]
        main = [_tx('BUYSELL', '2026-01-05', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        out, dropped, _ = _drop(shel, main)
        self.assertEqual(out, [])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 4)])


class TestUnorderedRowsSegmentByDate(unittest.TestCase):
    """M11 — the dropper sorts each (symbol, account) group by
    (date, time) before gap-clustering, so input order is irrelevant:
    an interleaved January pair and July pair land in two separate
    segments and BOTH net. A sort/segmentation mutant chains them in
    listing order and nothing nets."""

    def test_shuffled_input_order_both_pairs_net(self):
        rows = [_tx('TRANSFER', '2026-07-06', 'Q.TO', +100),
                _tx('TRANSFER', '2026-01-10', 'Q.TO', -30),
                _tx('TRANSFER', '2026-07-04', 'Q.TO', -100),
                _tx('TRANSFER', '2026-01-12', 'Q.TO', +30)]
        out, dropped, _ = _drop(rows)
        self.assertEqual(out, [])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 4)])


class TestAttestBlessPadBoundary(unittest.TestCase):
    """M12 — _ATTEST_BLESS_PAD_DAYS = 7: near a taxable trade, a
    declared leg blesses only rows within 7 days of itself. The
    day-7 companion leg is in reach (whole segment nets); the day-8
    leg is out of reach, the blessed subset is unbalanced (-100), and
    the WHOLE segment must be refused."""

    def _run(self, in_leg_date):
        shel = [_tx('TRANSFER', '2026-06-15', 'Q.TO', -100,
                    description=MANUAL_TRANSFER_DECLARATION),
                _tx('TRANSFER', in_leg_date, 'Q.TO', +100)]
        main = [_tx('BUYSELL', '2026-06-10', 'Q.TO', -100, net=800.0,
                    account='Margin')]
        return _drop(shel, main)

    def test_day_7_leg_in_reach_nets(self):
        out, dropped, _ = self._run('2026-06-22')
        self.assertEqual(out, [])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 2)])

    def test_day_8_leg_out_of_reach_refuses_whole_segment(self):
        out, dropped, err = self._run('2026-06-23')
        self.assertEqual(len(out), 2)
        self.assertEqual(dropped, [])
        self.assertIn("does not net to zero", err)


class TestRestatementChainPadBoundary(unittest.TestCase):
    """M13 — _RESTATEMENT_CHAIN_PAD_DAYS = 3: two same-day 2-symbol
    clusters plus a third symbol's cluster 4 days later must NOT
    chain into one >=3-symbol restatement event — with taxable
    trades nearby, every row is then refused. At a 3-day gap the
    chain forms (3 symbols, 3-day span, within the 7-day event cap)
    and the whole account's churn nets without attestation.
    Fixtures follow the post-audit discriminators: each cluster's
    first leg is an OUT-leg and event spans stay <= 7 days."""

    def _run(self, third_date):
        shel = []
        for sym in ('Q.TO', 'R.TO'):
            shel += [_tx('TRANSFER', '2026-06-15', sym, -100,
                         time='09:30:00'),
                     _tx('TRANSFER', '2026-06-15', sym, +100,
                         time='10:30:00')]
        shel += [_tx('TRANSFER', third_date, 'S.TO', -100,
                     time='09:30:00'),
                 _tx('TRANSFER', third_date, 'S.TO', +100,
                     time='10:30:00')]
        main = [_tx('BUYSELL', '2026-06-12', s, -100, net=800.0,
                    account='Margin')
                for s in ('Q.TO', 'R.TO', 'S.TO')]
        return _drop(shel, main)

    def test_4_day_gap_does_not_chain_all_rows_refused(self):
        out, dropped, err = self._run('2026-06-19')
        self.assertEqual(len(out), 6)
        self.assertEqual(dropped, [])
        self.assertNotIn("account-wide restatement detected", err)

    def test_3_day_gap_chains_into_event_and_nets(self):
        out, dropped, err = self._run('2026-06-18')
        self.assertEqual(out, [])
        self.assertEqual(sorted(dropped),
                         [('Q.TO', 'RRSP', 2), ('R.TO', 'RRSP', 2),
                          ('S.TO', 'RRSP', 2)])
        self.assertIn("account-wide restatement detected", err)


class TestSplitInSpanSymbolScoped(unittest.TestCase):
    """M14 — the in-book SPLIT block is symbol-scoped, and it must
    hold in BOTH directions: a SPLIT of the SAME symbol between the
    legs blocks netting (share terms differ), while an UNRELATED
    symbol's SPLIT must not. (M8 pins the main-book variant; this
    pins the same-book check's symbol equality.)"""

    def _rows(self, split_symbol):
        return [_tx('TRANSFER', '2026-06-15', 'Q.TO', -100),
                _tx('TRANSFER', '2026-06-18', 'Q.TO', +100),
                _tx('SPLIT', '2026-06-16', split_symbol, 2.0)]

    def test_same_symbol_split_blocks_netting(self):
        out, dropped, _ = _drop(self._rows('Q.TO'))
        self.assertEqual(len(out), 3)
        self.assertEqual(dropped, [])

    def test_unrelated_symbol_split_does_not_block(self):
        out, dropped, _ = _drop(self._rows('Z.TO'))
        self.assertEqual([t.symbol for t in out], ['Z.TO'])
        self.assertEqual(dropped, [('Q.TO', 'RRSP', 2)])


class TestJournalCandidateRelativeTolerance(unittest.TestCase):
    """M15 — the unmapped cross-listing journal fingerprint pairs an
    out-leg and in-leg of DIFFERENT symbols when the quantities match
    within max(1e-9, 1e-6 * qty): fractional broker dust (500 out vs
    500.0002 in) still pairs and prints the ticker.map suggestion;
    clearly-unequal quantities (500 vs 300) never do. An arithmetic
    mutant on the tolerance flips one of the two."""

    NOTE = "possible unmapped cross-listing journal"

    def _run(self, in_qty):
        shel = [_tx('TRANSFER', '2026-06-15', 'BTG.US', -500.0),
                _tx('TRANSFER', '2026-06-16', 'BTO.TO', in_qty)]
        return _drop(shel, [])

    def test_fractional_dust_still_pairs_and_warns(self):
        _, _, err = self._run(500.0002)
        self.assertIn(self.NOTE, err)

    def test_clearly_unequal_quantities_do_not_pair(self):
        _, _, err = self._run(300.0)
        self.assertNotIn(self.NOTE, err)


# ============================================================================
# Round-five pins — taxjson_export._apply_transfer_evidence (evidence-
# driven depot flips on the holdings aggregation).
# ============================================================================
def _tmap(journal=None):
    """A minimal ticker map declaring OR.US/OR.TO one identity class."""
    return TickerMap({}, {'OR.US': 'OR.TO'}, dict(journal or {}),
                     set(), set())


def _write_evidence(td, rows):
    """A transfer-sidecar JSON with the given (date, symbol, qty)."""
    p = Path(td) / 'm_ib_transfers.json'
    p.write_text(json.dumps(
        {'transactions': [
            {'action': 'TRANSFER', 'date': d, 'symbol': s,
             'quantity': q, 'currency': 'CAD', 'net_amount': 0.0,
             'account': 'margin', 'description': 'InterDepot'}
            for d, s, q in rows],
         'metadata': {'kind': 'transfer_sidecar', 'account': 'margin',
                      'brokerage': 'ib'}}), encoding='utf-8')
    return str(p)


def _bucket(qty, cost, currency, psd):
    return {'qty': qty, 'total_cost': cost, 'currency': currency,
            'cost_by_currency': {currency: cost},
            'position_start_date': psd}


def _apply(agg, rows, tmap):
    with tempfile.TemporaryDirectory() as td:
        p = _write_evidence(td, rows)
        with redirect_stderr(io.StringIO()):
            return _apply_transfer_evidence(agg, [p], tmap)


class TestPartialDepotFlipApportionsCost(unittest.TestCase):
    """M16 — flipping HALF a bucket must move cost PRO-RATA
    (frac = move / source qty = 0.5) on the flat totals AND both
    per-currency maps — not the whole bucket's cost, and not a
    divided amount. The applied move list is returned for the base-
    inventory replay (post-audit contract)."""

    def test_half_flip_moves_half_the_cost_and_returns_move(self):
        agg = {'OR.US': _bucket(600.0, 12000.0, 'USD', '2026-03-20'),
               'OR.TO': _bucket(100.0, 4000.0, 'CAD', '2026-07-17')}
        moves = _apply(agg, [('2026-07-02', 'OR.US', -300),
                             ('2026-07-02', 'OR.TO', 300)], _tmap())
        self.assertEqual(moves, [('OR.US', 'OR.TO', 300.0)])
        self.assertAlmostEqual(agg['OR.US']['qty'], 300.0, places=6)
        self.assertAlmostEqual(agg['OR.US']['total_cost'], 6000.0,
                               places=6)
        self.assertAlmostEqual(agg['OR.TO']['qty'], 400.0, places=6)
        self.assertAlmostEqual(agg['OR.TO']['total_cost'], 10000.0,
                               places=6)
        self.assertAlmostEqual(
            agg['OR.US']['cost_by_currency']['USD'], 6000.0, places=6)
        self.assertAlmostEqual(
            agg['OR.TO']['cost_by_currency']['USD'], 6000.0, places=6)
        self.assertAlmostEqual(
            agg['OR.TO']['cost_by_currency']['CAD'], 4000.0, places=6)


class TestDepotFlipNoOpGuards(unittest.TestCase):
    """M17 — two guards that must no-op WITHOUT crashing: tmap=None
    (no map file loaded), and evidence whose out-leg symbol has no
    holdings bucket (position already gone). Both return an empty
    applied-move list and leave the aggregation untouched."""

    def test_none_tmap_is_a_noop(self):
        agg = {'OR.TO': {'qty': 100.0, 'total_cost': 4000.0}}
        with tempfile.TemporaryDirectory() as td:
            p = _write_evidence(td, [('2026-07-02', 'OR.US', -300)])
            moves = _apply_transfer_evidence(agg, [p], None)
        self.assertEqual(moves, [])
        self.assertEqual(agg['OR.TO']['qty'], 100.0)
        self.assertEqual(agg['OR.TO']['total_cost'], 4000.0)

    def test_out_leg_without_source_bucket_is_a_noop(self):
        agg = {'OR.TO': _bucket(100.0, 4000.0, 'CAD', '2026-07-17')}
        moves = _apply(agg, [('2026-07-02', 'OR.US', -300),
                             ('2026-07-02', 'OR.TO', 300)], _tmap())
        self.assertEqual(moves, [])
        self.assertAlmostEqual(agg['OR.TO']['qty'], 100.0, places=6)
        self.assertAlmostEqual(agg['OR.TO']['total_cost'], 4000.0,
                               places=6)


class TestDepotFlipPruneGuard(unittest.TestCase):
    """M18 — after a flip, a source bucket is pruned only when BOTH
    its qty and residual cost are ~zero. A bucket with 300 shares
    LEFT (however tiny its residual cost) must survive; an and->or
    mutant on the prune condition deletes a live position."""

    def test_source_with_shares_left_is_never_pruned(self):
        agg = {'OR.US': _bucket(600.0, 0.004, 'USD', '2026-03-20'),
               'OR.TO': _bucket(100.0, 4000.0, 'CAD', '2026-07-17')}
        _apply(agg, [('2026-07-02', 'OR.US', -300),
                     ('2026-07-02', 'OR.TO', 300)], _tmap())
        self.assertIn('OR.US', agg)
        self.assertAlmostEqual(agg['OR.US']['qty'], 300.0, places=6)


class TestJournalFoldCancelsEvidence(unittest.TestCase):
    """M19 — post-audit behavior: evidence symbols are folded through
    tmap.journal BEFORE netting, so a JOURNAL pair's out/in legs land
    on one key and cancel. The holdings bucket (already folded by the
    aggregation) must NOT be evidence-moved on top of its fold."""

    def test_journal_pair_evidence_nets_to_zero_and_moves_nothing(self):
        agg = {'DLR.TO': _bucket(400.0, 5000.0, 'CAD', '2026-01-05')}
        tm = TickerMap({}, {}, {'DLR.US': 'DLR.TO'}, set(), set())
        moves = _apply(agg, [('2026-07-02', 'DLR.US', -300),
                             ('2026-07-02', 'DLR.TO', 300)], tm)
        self.assertEqual(moves, [])
        self.assertAlmostEqual(agg['DLR.TO']['qty'], 400.0, places=6)
        self.assertAlmostEqual(agg['DLR.TO']['total_cost'], 5000.0,
                               places=6)


if __name__ == "__main__":
    unittest.main()


class TestRoundSixPins(unittest.TestCase):
    """Round-six audit pins: the phase-aware balance walk, straddle
    boundaries and diagnostics, vintage selection, and evidence-row
    guards — each a proven mutation/fuzzer gap."""

    def _T(self, **kw):
        d = dict(action="BUYSELL", time="09:30:00", symbol="Q.TO",
                 currency="CAD", account="A0")
        d.update(kw)
        return TaxTransaction(**d)

    def test_bal_before_walk_is_phase_aware(self):
        """Round-six settle-straddle fuzzer (seeds 113/317/...): a
        sale settling ON a split date must be subtracted from the
        PRE-split balance. The naive sort applied it post-split —
        phantom shares made a later short-cover look like an opening
        buy and denied a loss with no trigger anywhere."""
        book = [
            self._T(date="2025-01-06", quantity=16,
                    net_amount=1600.0),
            self._T(date="2025-03-01", date_settle="2025-03-04",
                    quantity=-16, net_amount=3200.0),
            TaxTransaction(action="SPLIT", date="2025-03-04",
                           time="00:00:01", symbol="Q.TO",
                           quantity=2.0, currency="CAD",
                           account="A0"),
            self._T(date="2025-03-19", quantity=-8,
                    net_amount=400.0),
            self._T(date="2025-03-28", quantity=100, account="A1",
                    net_amount=5000.0),
            self._T(date="2025-04-06", quantity=4, net_amount=210.0),
            self._T(date="2025-05-03", quantity=-76, account="A1",
                    net_amount=1000.0),
        ]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules("canada").compute_gains(book)
        denied = round(sum(float(w.get("disallowed_amount") or 0)
                           for w in r.get("wash_sales") or []), 2)
        self.assertEqual(denied, 0.0,
                         "pure short-cover must not be a trigger")

    def test_trade_on_split_date_not_redenominated(self):
        """Mutation core-A1: a trade EXECUTED on the split date is
        already post-split-denominated (broker convention) even when
        it settles later — re-denominating it triples the loss and
        strands phantom shares."""
        book = [
            self._T(date="2026-01-05", quantity=100,
                    net_amount=10000.0),
            TaxTransaction(action="SPLIT", date="2026-06-10",
                           time="00:00:01", symbol="Q.TO",
                           quantity=2.0, currency="CAD",
                           account="A0"),
            self._T(date="2026-06-10", date_settle="2026-06-12",
                    quantity=-200, net_amount=8000.0),
        ]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules("canada").compute_gains(book)
        gains = [t for t in r["transactions"]
                 if t.get("qty") and "gain" in t]
        self.assertEqual(round(float(gains[0]["gain"]), 2), -2000.0)
        self.assertEqual([i for i in r.get("inventory") or []
                          if abs(i.get("qty") or 0) > 1e-9], [])

    def test_settle_desynced_split_uses_sort_date(self):
        """Round-six adversarial finding 2: the straddle window keys
        on the split's SORT date (settle when set). A split with
        date=06-11 but date_settle=06-16 applies to the pool at
        06-16 — the 06-12-settling sale is NOT straddled and must
        not be re-denominated."""
        book = [
            self._T(date="2026-01-05", quantity=100,
                    net_amount=10000.0),
            self._T(date="2026-06-10", date_settle="2026-06-12",
                    quantity=-100, net_amount=8000.0),
            TaxTransaction(action="SPLIT", date="2026-06-11",
                           date_settle="2026-06-16",
                           time="00:00:01", symbol="Q.TO",
                           quantity=2.0, currency="CAD",
                           account="A0"),
        ]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules("canada").compute_gains(book)
        gains = [t for t in r["transactions"]
                 if t.get("qty") and "gain" in t]
        self.assertEqual(round(float(gains[0]["gain"]), 2), -2000.0)
        self.assertEqual([i for i in r.get("inventory") or []
                          if abs(i.get("qty") or 0) > 1e-9], [])

    def test_evening_split_on_trade_date_redenominates_lagged_sale(self):
        """Real FFN 11-for-10 (2026-07-02): IB posts corporate actions
        in an evening batch (20:25) DATED the trade day, so the
        84-share sale executed that morning (settling T+1) was
        pre-split. A date-only 'strictly between' straddle test
        skipped it, the ladder split the pool first, and 84 x 0.1 =
        8.4 phantom shares (and $83.81 of stranded cost) stayed in
        inventory — caught by `taxjson sanity` against the broker's
        positions. The straddle test now compares the execution
        MOMENT (date, clock time) against the split's."""
        def _t(**kw):
            return TaxTransaction(**{"action": "BUYSELL",
                                     "symbol": "Q.TO",
                                     "currency": "CAD",
                                     "account": "A0", **kw})
        book = [
            _t(date="2026-06-16", time="11:03:12",
               date_settle="2026-06-17", quantity=220,
               net_amount=2296.8),
            _t(date="2026-06-30", time="10:59:06",
               date_settle="2026-07-02", quantity=150,
               net_amount=1764.0),
            _t(date="2026-07-02", time="10:54:31",
               date_settle="2026-07-03", quantity=-84,
               net_amount=983.48),
            TaxTransaction(action="SPLIT", date="2026-07-02",
                           time="20:25:00", symbol="Q.TO",
                           quantity=1.1, currency="CAD",
                           account="A0"),
            _t(date="2026-07-21", time="10:25:13",
               date_settle="2026-07-22", quantity=-314.6,
               net_amount=3400.83),
        ]
        err = io.StringIO()
        with redirect_stderr(err):
            r = get_tax_rules("canada").compute_gains(book)
        self.assertEqual([i for i in r.get("inventory") or []
                          if abs(i.get("qty") or 0) > 1e-9], [],
                         "no phantom shares after the full close")
        gains = [t for t in r["transactions"]
                 if t.get("qty") and "gain" in t]
        self.assertEqual([round(float(g["qty"]), 6) for g in gains],
                         [92.4, 314.6])
        self.assertIn("re-denominated a -84-share trade",
                      err.getvalue())
        # Total realized = total proceeds - total cost, nothing
        # stranded: (983.48 + 3400.83) - (2296.8 + 1764.0).
        self.assertAlmostEqual(sum(float(g["gain"]) for g in gains),
                               323.51, places=2)
        # A same-day split with a MORNING-default time (no meaningful
        # clock) keeps the ladder's convention: the sale is post-split.
        book2 = [TaxTransaction(**{**t.to_dict(), "time": "00:00:01"})
                 if t.action == "SPLIT" else t for t in book]
        with redirect_stderr(io.StringIO()):
            r2 = get_tax_rules("canada").compute_gains(book2)
        self.assertEqual([round(float(g["qty"]), 6)
                          for g in r2["transactions"]
                          if g.get("qty") and "gain" in g],
                         [84.0, 314.6])

    def test_straddled_trigger_diagnostics_use_redenominated_price(self):
        """Mutation core-A5: the re-denominated trigger's DIAGNOSTIC
        price (wash_window rows, trigger_price) must be the post-split
        per-share figure (41.0 / 2 = 20.5), not price*ratio."""
        book = [
            self._T(date="2026-01-05", quantity=100,
                    net_amount=10000.0, price=100.0),
            self._T(date="2026-06-10", quantity=-100,
                    net_amount=8000.0, price=80.0),
            self._T(date="2026-06-10", time="15:00:00",
                    date_settle="2026-06-12", quantity=50,
                    net_amount=2050.0, price=41.0),
            TaxTransaction(action="SPLIT", date="2026-06-11",
                           time="00:00:01", symbol="Q.TO",
                           quantity=2.0, currency="CAD",
                           account="A0"),
        ]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules("canada").compute_gains(book)
        trig_prices = [float((g.get("wash_trigger") or {})
                             .get("trigger_price") or 0)
                       for g in r["transactions"]
                       if g.get("wash_trigger")]
        self.assertIn(20.5, trig_prices)

    def test_restatement_prepass_respects_main_book_split(self):
        """Round-six adversarial finding 3: a MAIN-book SPLIT inside
        one symbol's span disqualifies that segment in the pre-pass —
        it must not raise the event's symbol count and launder its
        siblings past the near-trade refusal."""
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        shel, main = [], []
        for sym in ("Q.TO", "R.TO", "S.TO"):
            shel += [TaxTransaction(
                action="TRANSFER", date="2026-06-15", time="09:00:00",
                symbol=sym, quantity=-100.0, currency="CAD",
                net_amount=8000.0, account="RRSP"),
                TaxTransaction(
                action="TRANSFER", date="2026-06-16", time="09:00:00",
                symbol=sym, quantity=100.0, currency="CAD",
                net_amount=0.0, account="RRSP")]
            main.append(TaxTransaction(
                action="BUYSELL", date="2026-06-10", time="09:30:00",
                symbol=sym, quantity=-100.0, currency="CAD",
                net_amount=800.0, account="Margin"))
        main.append(TaxTransaction(
            action="SPLIT", date="2026-06-15", time="00:00:01",
            symbol="S.TO", quantity=2.0, currency="CAD",
            account="Margin"))
        err = io.StringIO()
        with redirect_stderr(err):
            out, _ = _drop_self_cancelling_transfers(
                shel, main_transactions=main)
        self.assertNotIn("restatement detected", err.getvalue())
        self.assertEqual(len(out), 6, "nothing nets: only 2 clean "
                                      "symbols remain, below threshold")

    def test_vintage_selection_boundaries(self):
        """Mutation est-B3/B6: pre-earliest year -> earliest vintage;
        the result's vintage label always matches the applied table."""
        from taxjson.lib.tax_estimate import (apply_vintage,
                                              estimate_canada,
                                              ca_amt_exemption)
        self.assertEqual(apply_vintage(2024), "2025")
        r26 = estimate_canada(realized=10000.0, year=2026,
                              eligible_div=0.0, foreign_div=0.0,
                              pil=0.0, other_income=100000.0,
                              other_losses=0.0, province="ON")
        self.assertEqual(r26["vintage"], "2026")
        self.assertEqual(ca_amt_exemption(), 181440.0)
        r25 = estimate_canada(realized=10000.0, year=2025,
                              eligible_div=0.0, foreign_div=0.0,
                              pil=0.0, other_income=100000.0,
                              other_losses=0.0, province="ON")
        self.assertEqual(r25["vintage"], "2025")
        self.assertEqual(ca_amt_exemption(), 177882.0)
        # The marginal figure can coincide across vintages on some
        # scenarios; the vintage label + table-derived exemption are
        # the discriminating pins here.

    def test_us_2026_ordinary_boundary(self):
        """Mutation est-B4: a 2026 US bracket edge that moved between
        vintages (12,400 vs 11,925 first-bracket top)."""
        from taxjson.lib.tax_estimate import (apply_vintage,
                                              US_ORD_BRACKETS)
        import taxjson.lib.tax_estimate as te
        apply_vintage(2026)
        self.assertEqual(te.US_ORD_BRACKETS[0][0], 12400)
        apply_vintage(2025)
        self.assertEqual(te.US_ORD_BRACKETS[0][0], 11925)

    def test_acquired_zero_quantity_raises(self):
        """Mutation ctt-C3: qty 0 must refuse (declares shares you
        HOLD), and comma quantities parse."""
        from taxjson.bin.taxjson_convert_tt import expand_acquired
        with self.assertRaises(ValueError):
            expand_acquired("ACQUIRED 2024-06-03 09:30:00 A.US 0 "
                            "CAD 1 0 ARRIVED 2025-05-09")
        lines = expand_acquired(
            "ACQUIRED 2024-06-03 09:30:00 A.US 1,000 CAD 10 "
            "10000 ARRIVED 2025-05-09")
        self.assertIn(" 1000 ", lines[0])
        self.assertIn("09:30:00", lines[1])

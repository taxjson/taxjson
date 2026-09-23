"""One definition of "a gains run".

Historically the semantics of a correct gains computation lived only in
taxjson_gains.py's main(): TRANSFER strip/rewrite, phantom opening
synthesis, the year filter with per-action date-basis rules, the
by_ticker rebuild, the tainted-split into manual_reporting_required,
superficial-loss / partial-taint warnings, and fee aggregation. Every
other consumer of compute_gains (taxjson-explain, the web what-if)
re-implemented a subset and drifted — the tier-5 "explain contradicts
the .sum" and "what-if ignores phantoms" bugs were symptoms.

This module is the single home:

    prepare_books(...)  — load-side preprocessing (transfers, phantoms)
                          shared by every consumer, usable standalone
                          (the web needs books without a full run).
    run_gains(...)      — the full CLI-equivalent gains run, returning
                          the exact dict taxjson-gains serializes.

taxjson_gains.py main() is now argparse + load + run_gains + json.dump;
explain and the web call the same functions, so they cannot drift.
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from taxjson.lib.core import TaxTransaction, get_tax_rules
from taxjson.lib.numeric import round_floats
from taxjson.lib.phantom_holdings import (
    detect_phantoms,
    detect_superficial_loss_warnings,
    load_phantoms,
    synthesize_openings,
)


def load_stdin_transactions(stream=None) -> List[TaxTransaction]:
    """The CLIs' stdin loader (naive line-based '#' comment stripping),
    shared so taxjson-gains and taxjson-explain can't drift. Kept
    byte-compatible with the historical inline copies rather than routed
    through core.strip_json_comments (which is string-aware and could
    accept inputs the old path rejected)."""
    from taxjson.lib.core import coerce_transaction_row
    stream = stream if stream is not None else sys.stdin
    content = "".join(line for line in stream
                      if not line.strip().startswith('#'))
    if not content.strip():
        return []
    raw = json.loads(content)
    tx_dicts = raw.get("transactions", [])
    # Row handling is shared with load_transactions so the stdin path
    # gets the same FUZZ #K hard guards and never silently drops a
    # malformed row (it used to `continue` past non-dicts — a corrupted
    # entry vanished from the ACB pool with no diagnostic).
    return [coerce_transaction_row(t, i, "stdin")
            for i, t in enumerate(tx_dicts)]


# A same-position custody move (broker transfer, cross-listing journal,
# cancel/rebook restatement) completes within days; ACATS worst cases run
# a couple of weeks. Rows further apart than this are separate EVENTS —
# an in-kind contribution out in February and an unrelated transfer in
# come December must never cancel each other just because the share
# counts happen to match.
_TRANSFER_PAIR_MAX_GAP_DAYS = 35

# The per-gap limit alone still lets a segment CHAIN: eleven rows each
# <= 35 days apart span February..December, so a real February
# disposition could be "netted" against unrelated rows ten months later.
# Beyond the per-gap limit, the whole segment's lo..hi SPAN is therefore
# capped for the zero-net drop decision. Oversized chains are REFUSED
# outright rather than split at the largest internal gap: refusal is
# simpler and strictly conservative — the surviving rows fall through to
# the loud paths (the --taxable hard error, or the transfer_rewrite +
# AmbiguousTransferDateError engine guard) instead of being silently
# guessed into clusters. A genuine custody move (ACATS worst case,
# cancel/rebook restatements included) completes well inside 45 days.
_TRANSFER_SEGMENT_MAX_SPAN_DAYS = 45

# How close a main-book trade of the symbol may be to a zero-net
# TRANSFER segment before netting is refused — mirrors the engine's
# ±30-day superficial-loss window.
_TRANSFER_NEAR_TRADE_PAD_DAYS = 30

# Stamped into `description` by taxjson-convert-tt on every hand-written
# .tt TRANSFER row. A zero-net segment containing a declared leg is the
# user ANSWERING the AmbiguousTransferDateError ("add a counter-TRANSFER
# to a .tt file so the pair nets out") — the near-trade refusal must not
# re-refuse the declared resolution it demanded (2026-09 audit).
MANUAL_TRANSFER_DECLARATION = 'manual declaration (.tt)'

# How far (days) a DECLARED .tt leg's authority reaches: only rows this
# close to a declared leg join its netted cluster. Wide enough for a
# multi-day broker restatement churn attested by one dated pair, narrow
# enough that a segment gap-chained weeks past the attestation keeps
# its own rows out of the blessing.
_ATTEST_BLESS_PAD_DAYS = 7

# Account-wide restatement detection: when at least this many DISTINCT
# symbols in ONE account have zero-net TRANSFER clusters overlapping a
# common date envelope (segments chained when within
# _RESTATEMENT_CHAIN_PAD_DAYS of each other), the event is a broker
# custody restatement — no tax event journals an account's whole
# inventory out-and-back to zero — and its clusters net without a
# per-symbol attestation. Below the threshold the per-symbol
# near-trade refusal (and the DECLARED resolution path) still applies:
# one or two symbols' churn is not evidence of an account-level event.
_RESTATEMENT_MIN_SYMBOLS = 3
_RESTATEMENT_CHAIN_PAD_DAYS = 3
# One broker restatement is a FEW DAYS of churn (the real 2026-08 event
# spanned 5). Without an event-level cap, pad-chaining could glue
# unrelated per-symbol pairs weeks apart into a >= 3-symbol "event"
# (round-five adversarial audit finding 2).
_RESTATEMENT_EVENT_MAX_SPAN_DAYS = 7

# Cross-listing journal candidates: an out-leg of X and an in-leg of a
# DIFFERENT symbol Y, same account, equal quantity, within this many
# days — the fingerprint of an UNMAPPED dual-listing journal (the
# mapped ones were normalized to one symbol before this code runs and
# net as same-symbol clusters). Surfaced as a ticker.map suggestion.
_JOURNAL_CANDIDATE_PAD_DAYS = 7


def _main_trade_dates(main_transactions):
    """Per-symbol datetimes of every main-book BUYSELL/ASSIGN — BOTH the
    trade date and the settle date (when present), regardless of the
    quantity's sign.

    Both dates (2026-09 audit finding 2): the Canada engine's ±30-day
    superficial-loss window runs on SETTLE dates, so a loss trading
    06-10 / settling 06-12 reaches two days further than the trade date
    alone; collecting only t.date let a transfer pair 31 days after the
    trade date (29 after settle) net silently while the engine, had it
    seen the rows, would have refused.

    Any sign (finding 3): a SHORT-cover loss is realized by a BUY, so a
    disposition can hide behind either sign. The refusal only needs to
    know "a main-book trade of this symbol is nearby", not its
    direction — being near ANY trade is reason enough not to guess.
    """
    from collections import defaultdict
    from datetime import datetime as _dt
    out: Dict[str, list] = defaultdict(list)
    for t in main_transactions:
        if t.action in ('BUYSELL', 'ASSIGN'):
            for raw in (t.date, getattr(t, 'date_settle', '') or ''):
                if not raw:
                    continue
                try:
                    out[t.symbol].append(_dt.strptime(raw, '%Y-%m-%d'))
                except (TypeError, ValueError):
                    continue
    return out


def _drop_self_cancelling_transfers(transactions, main_transactions=None):
    """Drop TRANSFER groups that net to zero WITHIN ONE TIME CLUSTER and
    have no intervening trade or split.

    Real cross-listing journals (AEM.TO out → AEM.US in, where the ticker
    map has normalized .US back to .TO) and broker-to-broker custody
    moves (out at the old broker, in at the new, often with cancel/
    rebook restatements between) appear as TRANSFER rows on the same
    ticker summing to zero qty within a few days. The position
    economically never changed; the gains engine should treat the
    intervening "missing" period as a no-op.

    Per (symbol, account), rows are first SEGMENTED in date order —
    consecutive rows more than _TRANSFER_PAIR_MAX_GAP_DAYS apart start a
    new segment — and each segment is judged alone. This is what keeps
    the drop from erasing two UNRELATED real events (e.g. a taxable
    in-kind contribution out — a CRA deemed disposition — cancelled by
    an unrelated transfer in months later): distant rows never share a
    segment, so neither nets to zero and both fall through to the
    taxable hard-error / sheltered rewrite.

    Safe-drop conditions (per segment):
      * segment qty sums to zero (within epsilon)
      * no BUYSELL/ASSIGN of the same symbol in the same account falls
        inside the segment's date span — the user traded while the
        position was "in transit"; surface that instead of papering over
      * no SPLIT of the symbol (ANY account — splits are corporate-wide)
        falls inside the span: the out and in legs would be denominated
        in different share terms, so a zero qty sum no longer means
        "same position".
      * the segment's lo..hi span does not exceed
        _TRANSFER_SEGMENT_MAX_SPAN_DAYS — see that constant for why
        chained segments are refused rather than split.

    When `main_transactions` is given (the SHELTERED-context invocation
    only — see _handle_transfers), a segment is additionally NOT dropped
    when any main-book trade of the symbol (trade or settle date, any
    sign) falls within _TRANSFER_NEAR_TRADE_PAD_DAYS of its span, and a
    main-book SPLIT inside the span blocks it too. Without this the
    per-account dropper — which runs BEFORE the cross-account netter —
    silently erased a same-account contribution+withdrawal pair inside a
    loss window, and let chained rrsp→rrsp2→out hops net per-account
    before the netter's near-sale refusal could ever see them (2026-09
    audit finding 4). The surviving legs are rewritten and the engine's
    AmbiguousTransferDateError forces a declaration if they matter.
    A segment containing a MANUAL_TRANSFER_DECLARATION leg (a
    hand-written .tt TRANSFER) bypasses that near-trade refusal only:
    the declaration is the answer the guard exists to demand, and
    re-refusing it would make the documented resolution path a dead
    end. The SPLIT blocks stay unconditional — mixed share terms are
    arithmetic, not judgment. The
    MAIN-book invocation deliberately does NOT get this guard: taxable
    custody moves near the account's own sales are normal (a broker move
    mid-trading), and the taxable book's transfers are guarded by the
    TransferValidationError hard-error path instead.

    Returns the filtered list plus a list of (symbol, account, count)
    tuples for the caller to log.
    """
    epsilon = 1e-9
    from collections import defaultdict
    from datetime import datetime as _dt, timedelta as _td
    main_trades = (_main_trade_dates(main_transactions)
                   if main_transactions is not None else {})
    groups: Dict[tuple, list] = defaultdict(list)
    for idx, t in enumerate(transactions):
        if t.action == 'TRANSFER':
            groups[(t.symbol, t.account)].append((idx, t))

    def _d(t):
        try:
            return _dt.strptime(t.date, '%Y-%m-%d')
        except (TypeError, ValueError):
            return None

    def _segment(rows):
        rows = sorted(rows, key=lambda r: (r[1].date or '',
                                           r[1].time or ''))
        segments: list = [[rows[0]]]
        for prev, cur in zip(rows, rows[1:]):
            pd, cd = _d(prev[1]), _d(cur[1])
            if pd is None or cd is None \
                    or (cd - pd).days > _TRANSFER_PAIR_MAX_GAP_DAYS:
                segments.append([cur])
            else:
                segments[-1].append(cur)
        return segments

    seg_by_key = {k: _segment(v) for k, v in groups.items()}

    # PRE-PASS (sheltered invocation only): account-wide restatement
    # detection. Per account, chain zero-net qualified segments whose
    # date spans sit within _RESTATEMENT_CHAIN_PAD_DAYS of each other;
    # a chain covering >= _RESTATEMENT_MIN_SYMBOLS distinct symbols is
    # one broker custody event (2026-09: a real restatement journaled
    # every position of one account out-and-back over a few days) —
    # its segments net without per-symbol attestation. The evidence is
    # IN the data: no tax event journals an account's whole inventory
    # out-and-back to zero, and the per-symbol near-trade refusal was
    # blind to the cross-symbol correlation, demanding one DECLARED
    # attestation per symbol for a single event.
    restatement_rows: set = set()
    if main_transactions is not None:
        _by_acct: Dict[str, list] = defaultdict(list)
        for (symbol, account), segs in seg_by_key.items():
            for seg in segs:
                if abs(sum(t.quantity for _, t in seg)) > epsilon:
                    continue
                _pd = [d for d in (_d(t) for _, t in seg)
                       if d is not None]
                if not _pd or (max(_pd) - min(_pd)).days \
                        > _TRANSFER_SEGMENT_MAX_SPAN_DAYS:
                    continue
                # Round-five adversarial audit, three discriminators
                # that keep genuine tax events out of the pool:
                # (a) LEG ORDER — a restatement journals OUT and back
                #     IN; an in-kind contribution+withdrawal pair is
                #     IN-first. The first non-declared leg must be an
                #     out-leg (declared legs are the user's attest
                #     rows, excluded from the order test).
                # Same-date/time ties resolve by input order (.tt
                # rows default to 09:30:00, so ties are common). A
                # misordered tie fails SAFE in the in-first direction
                # (falls back to the refusal/attestation path); the
                # out-first direction is what the discriminator
                # accepts anyway.
                _real = [t for _, t in sorted(
                    seg, key=lambda r: (r[1].date or '',
                                        r[1].time or ''))
                    if (t.description or '')
                    != MANUAL_TRANSFER_DECLARATION]
                if not _real or _real[0].quantity >= 0:
                    continue
                # (b) GUARDED SEGMENTS never qualify — a segment with
                #     an in-span trade/SPLIT is refused by the main
                #     loop and must not raise the symbol count here.
                _sd_lo = min(t.date for _, t in seg)
                _sd_hi = max(t.date for _, t in seg)
                if any((t.action in ('BUYSELL', 'ASSIGN')
                        and t.symbol == symbol
                        and t.account == account
                        and t.date
                        and _sd_lo <= t.date <= _sd_hi)
                       or (t.action == 'SPLIT' and t.symbol == symbol
                           and t.date
                           and _sd_lo <= t.date <= _sd_hi)
                       for t in transactions):
                    continue
                # MAIN-book SPLITs disqualify too (corporate-wide,
                # mirroring the drop loop's refusal): a guarded
                # symbol must not raise the event's symbol count and
                # launder its siblings past the near-trade refusal
                # (round-six adversarial audit finding 3).
                if main_transactions is not None and any(
                        t.action == 'SPLIT' and t.symbol == symbol
                        and t.date and _sd_lo <= t.date <= _sd_hi
                        for t in main_transactions):
                    continue
                _by_acct[account].append(
                    (min(_pd), max(_pd), symbol, seg))
        _cpad = _td(days=_RESTATEMENT_CHAIN_PAD_DAYS)
        _evcap = _td(days=_RESTATEMENT_EVENT_MAX_SPAN_DAYS)
        for account, entries in _by_acct.items():
            entries.sort(key=lambda e: e[0])
            event: list = []
            ev_lo = ev_hi = None

            def _flush(ev, acct):
                syms = {s for _, _, s, _ in ev}
                if len(syms) < _RESTATEMENT_MIN_SYMBOLS:
                    return
                lo = min(e[0] for e in ev)
                hi = max(e[1] for e in ev)
                for _, _, _, seg in ev:
                    for idx, _t in seg:
                        restatement_rows.add(idx)
                print(f"NOTE: account-wide restatement detected "
                      f"({acct}): {len(syms)} symbols share zero-net "
                      f"TRANSFER clusters over {lo:%Y-%m-%d}.."
                      f"{hi:%Y-%m-%d} — netted as one custody event "
                      f"(no attestation needed).", file=sys.stderr)

            for e in entries:
                # (c) EVENT SPAN CAP — one broker event is a few days
                #     of churn; pad-chaining must not glue unrelated
                #     pairs weeks apart into a symbol count. A segment
                #     that would stretch the event past the cap starts
                #     a new event instead.
                if event and (e[0] > ev_hi + _cpad
                              or (max(ev_hi, e[1]) - min(ev_lo, e[0]))
                              > _evcap):
                    _flush(event, account)
                    event, ev_lo, ev_hi = [], None, None
                event.append(e)
                ev_lo = e[0] if ev_lo is None else min(ev_lo, e[0])
                ev_hi = e[1] if ev_hi is None else max(ev_hi, e[1])
            if event:
                _flush(event, account)

    drop_idx = set()
    dropped = []
    for (symbol, account), rows in groups.items():
        segments = seg_by_key[(symbol, account)]
        n_dropped = 0
        for seg in segments:
            net_qty = sum(t.quantity for _, t in seg)
            if abs(net_qty) > epsilon:
                continue
            dates = [t.date for _, t in seg]
            date_lo, date_hi = min(dates), max(dates)
            pdates = [d for d in (_d(t) for _, t in seg) if d is not None]
            # Chained-segment residue: per-gap clustering alone lets
            # consecutive <=35d gaps chain a Feb..Dec span. Refuse the
            # zero-net drop for oversized spans (see the constant).
            if pdates and (max(pdates) - min(pdates)).days \
                    > _TRANSFER_SEGMENT_MAX_SPAN_DAYS:
                continue
            blocked = any(
                (t.action in ('BUYSELL', 'ASSIGN')
                 and t.symbol == symbol and t.account == account
                 and t.date and date_lo <= t.date <= date_hi)
                or (t.action == 'SPLIT' and t.symbol == symbol
                    and t.date and date_lo <= t.date <= date_hi)
                for t in transactions
            )
            if blocked:
                continue
            blessed_seg = seg
            if main_transactions is not None and pdates:
                lo, hi = min(pdates), max(pdates)
                _pad = _td(days=_TRANSFER_NEAR_TRADE_PAD_DAYS)
                near = any(lo - _pad <= td_ <= hi + _pad
                           for td_ in main_trades.get(symbol, ()))
                # Segments of a detected account-wide restatement net
                # regardless of nearby taxable trades — the event-level
                # evidence outranks the per-symbol ambiguity. The
                # SPLIT-in-span checks below still apply (share-term
                # arithmetic is unconditional).
                # Segments carrying DECLARED legs keep the
                # attestation path (bless-pad narrowing) even inside a
                # detected event — the event bypass must not widen a
                # declaration's reach (round-five audit finding 3).
                _has_decl = any((t.description or '')
                                == MANUAL_TRANSFER_DECLARATION
                                for _, t in seg)
                if near and not _has_decl                         and seg[0][0] in restatement_rows:
                    near = False
                # Near ANY main-book trade of the symbol (trade or
                # settle date, either sign): don't guess — keep the
                # legs so the rewrite + engine guard force a
                # declaration if they would act as wash triggers.
                # EXCEPT rows covered by a hand-written .tt DECLARED
                # leg: that IS the demanded declaration (the
                # counter-TRANSFER / attestation the error message
                # prescribes). The blessing reaches only rows within
                # _ATTEST_BLESS_PAD_DAYS of a declared leg — a
                # declared June pair must not silently net a genuine
                # July contribution that gap-chained into the same
                # segment (2026-09 round-four audit finding 1).
                decl_dates = [d for (_, t), d in
                              ((r, _d(r[1])) for r in seg)
                              if d is not None
                              and (t.description or '')
                              == MANUAL_TRANSFER_DECLARATION]
                if near and not decl_dates:
                    # The engine guard downstream will prescribe a
                    # counter-TRANSFER — wrong for a cluster that
                    # ALREADY nets to zero (it would unbalance it).
                    # Tell the user the attestation form instead.
                    _q = abs(seg[0][1].quantity)
                    print(
                        f"NOTE: {symbol} ({account}): a zero-net "
                        f"TRANSFER cluster ({len(seg)} rows, "
                        f"{date_lo}..{date_hi}) was NOT netted out "
                        f"because it sits within "
                        f"{_TRANSFER_NEAR_TRADE_PAD_DAYS} days of a "
                        f"taxable-book trade of the symbol. If this is "
                        f"broker churn (listing flip / custody "
                        f"restatement — ownership never changed), "
                        f"attest it by adding a hand-written zero-net "
                        f"pair to a .tt file in the same account "
                        f"(the trailing DECLARED token marks a "
                        f"deliberate declaration), e.g.:\n"
                        f"  TRANSFER  {date_lo}  09:30:00  {symbol}  "
                        f"{_q:g}  CAD  0.0  0.0  DECLARED\n"
                        f"  TRANSFER  {date_lo}  09:30:00  {symbol}  "
                        f"-{_q:g}  CAD  0.0  0.0  DECLARED\n"
                        f"— declared legs let the cluster net. If any "
                        f"leg was a genuine in-kind contribution or "
                        f"withdrawal, record THAT leg as a BUYSELL "
                        f"dated the true event day instead.",
                        file=sys.stderr)
                    continue
                if near and decl_dates:
                    _bpad = _td(days=_ATTEST_BLESS_PAD_DAYS)
                    blessed_seg = [
                        (idx, t) for idx, t in seg
                        if (_d(t) is not None
                            and any(abs(_d(t) - dd) <= _bpad
                                    for dd in decl_dates))]
                    if len(blessed_seg) < len(seg):
                        bal = sum(t.quantity for _, t in blessed_seg)
                        if abs(bal) > epsilon:
                            # The declared legs don't net within their
                            # own reach — refuse the whole segment; the
                            # survivors face the rewrite + guard.
                            print(
                                f"NOTE: {symbol} ({account}): the "
                                f"DECLARED attestation does not net to "
                                f"zero within {_ATTEST_BLESS_PAD_DAYS} "
                                f"days of its own legs — nothing was "
                                f"netted. Cover the whole cluster "
                                f"({date_lo}..{date_hi}) or move the "
                                f"declared pair next to the rows it "
                                f"attests.", file=sys.stderr)
                            continue
                # Main-book SPLIT inside the span: splits are
                # corporate-wide, so the legs are in different terms.
                if any(t.action == 'SPLIT' and t.symbol == symbol
                       and t.date and date_lo <= t.date <= date_hi
                       for t in main_transactions):
                    continue
            for idx, _ in blessed_seg:
                drop_idx.add(idx)
            n_dropped += len(blessed_seg)
        if n_dropped:
            dropped.append((symbol, account, n_dropped))

    # POST-PASS (sheltered invocation only): unmapped cross-listing
    # journal candidates. A mapped dual listing (ticker.map
    # TOBASE/JOURNAL) was normalized to ONE symbol before this code
    # ran and netted as a same-symbol cluster above. An UNMAPPED pair
    # (different roots — BTG.US/BTO.TO) survives as an out-leg of X
    # and an in-leg of Y that nothing can net: surface the fingerprint
    # (same account, equal qty, opposite signs, within
    # _JOURNAL_CANDIDATE_PAD_DAYS) as a ticker.map suggestion instead
    # of silently feeding the loss walk a disposal of X and an
    # acquisition of Y.
    if main_transactions is not None:
        _left = [t for i, t in enumerate(transactions)
                 if i not in drop_idx and t.action == 'TRANSFER']
        _warned = set()
        for a in _left:
            da = _d(a)
            if a.quantity >= 0 or da is None:
                continue
            for b in _left:
                db = _d(b)
                if (b.quantity <= 0 or db is None
                        or b.account != a.account
                        or b.symbol == a.symbol
                        or abs(b.quantity + a.quantity)
                        > max(epsilon, 1e-6 * abs(b.quantity))
                        or abs((db - da).days)
                        > _JOURNAL_CANDIDATE_PAD_DAYS):
                    continue
                key = (a.symbol, b.symbol, a.account)
                if key in _warned:
                    continue
                _warned.add(key)
                # Suggest the conventional direction: the non-base
                # listing (.US) maps to the base one.
                _frm, _to = ((b.symbol, a.symbol)
                             if b.symbol.endswith('.US')
                             else (a.symbol, b.symbol))
                print(f"NOTE: possible unmapped cross-listing journal "
                      f"in {a.account}: {a.symbol} out "
                      f"{a.quantity:g} ({a.date}) pairs with "
                      f"{b.symbol} in +{b.quantity:g} ({b.date}). If "
                      f"these are the SAME security's two listings, "
                      f"add `TOBASE {_frm} {_to}` (or JOURNAL) to "
                      f"ticker.map so the legs net as one symbol; if "
                      f"they are different securities, ignore this "
                      f"note.", file=sys.stderr)

    if not drop_idx:
        return transactions, dropped

    filtered = [t for i, t in enumerate(transactions) if i not in drop_idx]
    return filtered, dropped


def _net_cross_account_transfers(transactions, main_transactions=()):
    """Net out TRANSFER groups that cancel at the SYMBOL level across
    accounts within one time cluster — a registered-to-registered move
    of the user's own shares (rrsp → rrsp2). Ownership never changed, so
    the receiving leg is not an acquisition for wash purposes, and the
    symbol-level sheltered balance is unchanged. Same clustering and
    zero-net rules as the per-account drop; used only on the
    wash-context (--sheltered) book, where balances are consumed at the
    symbol level.

    Two refusals (2026-09 adversarial audit — a contribution INTO one
    registered account plus an in-kind withdrawal FROM another is
    byte-identical to a custody move, and netting it hid the canonical
    permanently-denied superficial-loss trigger):
      * a SPLIT of the symbol inside the segment's span blocks the
        drop (share terms changed — mirrors the per-account dropper);
      * a segment within _TRANSFER_NEAR_TRADE_PAD_DAYS of any MAIN-book
        (taxable) trade of the same symbol — trade OR settle date,
        either quantity sign (see _main_trade_dates: the engine's
        window runs on settle dates, and a short-cover loss is realized
        by a BUY) — is NOT netted: the surviving legs are rewritten
        and, if they would actually act as wash triggers, the engine's
        AmbiguousTransferDateError makes the user declare what they
        were. Custody moves away from loss windows still net silently.

    Segments whose lo..hi span exceeds _TRANSFER_SEGMENT_MAX_SPAN_DAYS
    are never netted (chained-gap residue — see that constant)."""
    epsilon = 1e-9
    from collections import defaultdict
    from datetime import datetime as _dt, timedelta as _td
    groups: Dict[str, list] = defaultdict(list)
    for idx, t in enumerate(transactions):
        if t.action == 'TRANSFER':
            groups[t.symbol].append((idx, t))

    def _d(t):
        try:
            return _dt.strptime(t.date, '%Y-%m-%d')
        except (TypeError, ValueError):
            return None

    main_trades = _main_trade_dates(main_transactions)

    drop_idx = set()
    for symbol, rows in groups.items():
        rows = sorted(rows, key=lambda r: (r[1].date or '',
                                           r[1].time or ''))
        segments: list = [[rows[0]]]
        for prev, cur in zip(rows, rows[1:]):
            pd, cd = _d(prev[1]), _d(cur[1])
            if pd is None or cd is None \
                    or (cd - pd).days > _TRANSFER_PAIR_MAX_GAP_DAYS:
                segments.append([cur])
            else:
                segments[-1].append(cur)
        for seg in segments:
            if abs(sum(t.quantity for _, t in seg)) > epsilon:
                continue
            # A one-sided segment (all same sign) can't be a move.
            if not (any(t.quantity > 0 for _, t in seg)
                    and any(t.quantity < 0 for _, t in seg)):
                continue
            dates = [_d(t) for _, t in seg if _d(t) is not None]
            if not dates:
                continue
            lo, hi = min(dates), max(dates)
            # Chained-gap residue: refuse oversized spans outright.
            if (hi - lo).days > _TRANSFER_SEGMENT_MAX_SPAN_DAYS:
                continue
            # SPLIT inside the span: legs are in different share terms.
            if any(t.action == 'SPLIT' and t.symbol == symbol
                   and _d(t) is not None and lo <= _d(t) <= hi
                   for t in list(transactions)
                   + list(main_transactions)):
                continue
            # Near a taxable trade (either sign, trade or settle
            # date): don't guess — let the rewrite + engine guard
            # force a declaration if it matters.
            _pad = _td(days=_TRANSFER_NEAR_TRADE_PAD_DAYS)
            if any(lo - _pad <= sd <= hi + _pad
                   for sd in main_trades.get(symbol, ())):
                continue
            for idx, _ in seg:
                drop_idx.add(idx)
    if not drop_idx:
        return transactions
    return [t for i, t in enumerate(transactions) if i not in drop_idx]


class TransferValidationError(ValueError):
    """A taxable input contains TRANSFER rows the engine must not book.

    Raised (never sys.exit) so library consumers — the web server, tests,
    future embedders — decide the failure mode. CLI entry points catch it
    and exit 1 with the message on stderr."""


def _handle_transfers(transactions, sheltered_transactions, *, taxable):
    """Pre-process TRANSFER rows before they reach the gains engine.

    The --sheltered file is for cross-account wash-sale context only. Its
    TRANSFER rows are informational for ownership tracking and are not
    buy/sell events — they're stripped unconditionally so they can't
    falsely trigger wash-sale detection on the main file's losses.

    For the main input we first auto-drop self-cancelling TRANSFER pairs
    (cross-listing journals etc. — see _drop_self_cancelling_transfers),
    then for whatever TRANSFERs remain:
      --taxable: hard-error. The user must replace transfers with the
                 actual buy/sell history.
      default (sheltered): rewrite each TRANSFER to BUYSELL using its
                 net_amount as approximate cost basis. Approximate is
                 acceptable because sheltered gains aren't reported on
                 the return — the rewrite exists so the sheltered run
                 still tracks the position in inventory_long/short for
                 the holdings.toml / inventory report.
    """
    # The --sheltered context file's TRANSFER rows need three-way
    # handling, not the old unconditional strip. Stripping everything
    # hid a GENUINE acquisition from superficial-loss detection: an
    # in-kind contribution into an RRSP/TFSA within the ±30-day window
    # is the canonical PERMANENTLY-denied superficial loss (s.40(2)(g),
    # affiliated-person acquisition), and parsers emit exactly a
    # TRANSFER row for it — the loss was silently claimed. So:
    #   1. custody noise (same-account broker moves, cancel/rebook
    #      restatements) cancels via the pair drop;
    #   2. registered-to-registered moves between the user's OWN
    #      sheltered accounts (rrsp→rrsp2) net at the symbol level —
    #      moving your own shares acquires nothing, and treating the
    #      receiving leg as a buy would fabricate wash triggers;
    #   3. what survives is a real acquisition or disposal by the
    #      sheltered side — rewritten to BUYSELL so the wash walk sees
    #      it (as trigger and in still-held balances).
    n_sh_before = sum(1 for t in sheltered_transactions
                      if t.action == 'TRANSFER')
    # The SHELTERED-side dropper gets main-book visibility so it applies
    # the same near-trade refusal as the cross-account netter — it runs
    # FIRST, so without the guard a same-account contribution+withdrawal
    # pair inside a loss window vanished before the netter could refuse.
    # The main-book invocation below stays unguarded on purpose (taxable
    # custody moves near own-sales are normal; the taxable book has the
    # TransferValidationError hard-error path instead).
    sheltered_transactions, _sh_pairs = _drop_self_cancelling_transfers(
        sheltered_transactions, main_transactions=transactions)
    sheltered_transactions = _net_cross_account_transfers(
        sheltered_transactions, main_transactions=transactions)
    n_sh_rewritten = 0
    _sh_out = []
    for t in sheltered_transactions:
        if t.action == 'TRANSFER':
            # type='transfer_rewrite': the engine refuses to use this
            # row as a wash TRIGGER (its date may be a custody-arrival
            # date, not an acquisition date) while still counting it
            # in still-held balances, which are date-insensitive.
            t = TaxTransaction(**{**t.to_dict(), 'action': 'BUYSELL',
                                  'type': 'transfer_rewrite'})
            n_sh_rewritten += 1
        _sh_out.append(t)
    sheltered_transactions = _sh_out
    n_sh_stripped = n_sh_before - n_sh_rewritten

    # Auto-drop self-cancelling pairs first — they're never tax events,
    # and dropping them avoids needlessly tripping the --taxable guard.
    transactions, dropped_pairs = _drop_self_cancelling_transfers(transactions)
    if dropped_pairs:
        # Emit one note line per ticker so the silence is audible —
        # a user reading the log can see which TRANSFER rows their
        # broker emitted but the engine deliberately ignored.
        summary = ", ".join(f"{sym} ({count} row{'s' if count != 1 else ''}, {acct})"
                            for sym, acct, count in dropped_pairs)
        print(
            f"NOTE: auto-dropped {sum(c for _, _, c in dropped_pairs)} self-cancelling "
            f"TRANSFER row(s) (net-zero, no intervening sell): {summary}.",
            file=sys.stderr,
        )

    n_main = sum(1 for t in transactions if t.action == 'TRANSFER')

    if n_sh_rewritten:
        print(
            f"NOTE: {n_sh_rewritten} unmatched TRANSFER row(s) in the "
            f"--sheltered context treated as sheltered "
            f"acquisitions/disposals for the superficial-loss walk "
            f"(in-kind contributions/withdrawals; custody moves were "
            f"netted out).",
            file=sys.stderr,
        )
    if n_main == 0:
        if n_sh_stripped:
            print(
                f"NOTE: netted out {n_sh_stripped} TRANSFER row(s) from "
                f"the --sheltered context (custody moves — not "
                f"acquisitions).",
                file=sys.stderr,
            )
        return transactions, sheltered_transactions

    if taxable:
        sample = []
        for t in transactions:
            if t.action == 'TRANSFER':
                sample.append(f"  {t.date}  {t.symbol:<16}  qty={t.quantity:>8}  account={t.account}")
                if len(sample) >= 5:
                    break
        more = "" if n_main <= 5 else f"\n  (+{n_main - 5} more)"
        raise TransferValidationError(
            f"--taxable was set but the input contains {n_main} TRANSFER row(s). "
            f"TRANSFER is not allowed in taxable accounts — replace each with "
            f"the actual buy/sell history that established the position.\n"
            + "\n".join(sample) + more
        )

    # Sheltered main file: rewrite TRANSFER → BUYSELL so the engine
    # treats net_amount as cost basis and the sheltered run still
    # tracks the position in inventory. Other fields preserved.
    rewritten = []
    for t in transactions:
        if t.action == 'TRANSFER':
            t = TaxTransaction(**{**t.to_dict(), 'action': 'BUYSELL',
                                  'type': 'transfer_rewrite'})
        rewritten.append(t)
    msg = (
        f"NOTE: rewrote {n_main} TRANSFER row(s) → BUYSELL for ACB pooling "
        f"(sheltered-account approximation). Pass --taxable to reject "
        f"TRANSFER rows instead."
    )
    if n_sh_stripped:
        msg += (f" Also netted out {n_sh_stripped} custody-move "
                f"TRANSFER row(s) from --sheltered.")
    print(msg, file=sys.stderr)
    return rewritten, sheltered_transactions


def prepare_books(transactions, sheltered_transactions=(),
                  affiliated_transactions=(), *, taxable: bool,
                  incomplete_history: Optional[Path] = None,
                  phantom_hint: bool = True):
    """The load-side preprocessing every gains consumer must share:
    TRANSFER handling (strip/drop/rewrite/reject) then phantom opening
    synthesis. Returns (transactions, sheltered, affiliated, phantom_log).

    `phantom_hint` controls the advisory stderr NOTE emitted when NO
    phantom file is supplied but positions go short (the gains CLI wants
    it; explain and the web keep their stderr quiet).
    """
    transactions = list(transactions)
    sheltered_transactions = list(sheltered_transactions)
    affiliated_transactions = list(affiliated_transactions)

    transactions, sheltered_transactions = _handle_transfers(
        transactions, sheltered_transactions, taxable=taxable,
    )

    phantom_application_log: list = []
    if incomplete_history:
        phantoms = load_phantoms(Path(incomplete_history))
        transactions, phantom_application_log = synthesize_openings(transactions, phantoms)
    elif phantom_hint:
        # No phantom file supplied — but if the data has positions that go
        # negative, the user may have truncated history they haven't told
        # us about. Emit a one-time hint so they notice. Options are
        # filtered out (sell-to-open is normal, not phantom).
        candidates = detect_phantoms(transactions + sheltered_transactions + affiliated_transactions)
        if candidates:
            n_reg = sum(1 for c in candidates if c.registered)
            preview = ', '.join(f"{c.symbol}/{c.account}" for c in candidates[:3])
            more = f" (+{len(candidates) - 3} more)" if len(candidates) > 3 else ""
            print(
                f"NOTE: {len(candidates)} (symbol, account) pair(s) go short in this data: "
                f"{preview}{more}. {n_reg} are in registered accounts. "
                f"If any of these are from truncated history rather than real short trades, "
                f"run with --suggest-phantoms FILE to generate a candidate list.",
                file=sys.stderr,
            )
    return (transactions, sheltered_transactions, affiliated_transactions,
            phantom_application_log)


@dataclass
class GainsRequest:
    """Everything that shapes a gains run. `tax_date` and `detect_wash`
    default country-aware / policy-aware here — ONE place — instead of
    being re-derived by each caller."""
    country: str = "canada"
    year: Optional[int] = None
    taxable: bool = False
    tax_date: Optional[str] = None            # None → country-aware default
    incomplete_history: Optional[Path] = None
    trace: bool = False
    no_wash: bool = False
    detect_wash: Optional[bool] = None        # None → taxable and not no_wash
    cross_asset: bool = False
    phantom_hint: bool = True
    # ITA s.49(1) premium timing for written options (Canada only):
    # 'grant' recognises the premium on the write date, 'close' at the
    # closing transaction (the US §1234 convention). Contracts written
    # before `option_grant_since` keep close timing (transition).
    option_premium_timing: str = 'close'
    option_grant_since: Optional[int] = None
    option_buyback_loss_superficial: bool = False
    # Blended multi-account mode: US FIFO pools keyed per
    # (account, symbol) while §1091 matching stays cross-account.
    # Canada needs no flag — its symbol-global pools already blend
    # (ITA s.47), so this is forwarded to the US engine only.
    per_account_basis: bool = False

    def effective_tax_date(self) -> str:
        # Country-aware default: CRA times dispositions on the SETTLEMENT
        # date, the IRS on the TRADE date. Explicit value wins.
        if self.tax_date is not None:
            return self.tax_date
        return ('trade'
                if (self.country or '').strip().lower() in ('us', 'usa')
                else 'settle')

    def effective_detect_wash(self) -> bool:
        # Wash-sale detection: only meaningful for taxable accounts. The
        # superficial-loss rule (CRA ITA 54 / IRC §1091) disallows real
        # losses, which sheltered accounts don't have. Default-off avoids
        # spurious "disallowed" entries on RRSP/TFSA/etc. reports.
        if self.detect_wash is not None:
            return self.detect_wash
        return self.taxable and not self.no_wash


def run_gains(transactions, sheltered_transactions=(),
              affiliated_transactions=(), req: GainsRequest = None, *,
              trace_sink: Optional[Callable[[dict], None]] = None,
              books_prepared: bool = False) -> dict:
    """The full gains run — the dict this returns is byte-identical to
    what `taxjson-gains` serializes (it IS what taxjson-gains serializes).

    `trace_sink`, when given with req.trace, is called with the results
    dict at the exact point the CLI historically wrote its traces file:
    after the year filter and fee aggregation, before the tainted split
    strips/removes trace-bearing entries.

    `books_prepared=True` skips prepare_books for callers that already
    ran it (the CLI's --suggest-phantoms path preprocesses first).
    """
    req = req or GainsRequest()
    tax_date = req.effective_tax_date()

    if books_prepared:
        phantom_application_log = []
    else:
        (transactions, sheltered_transactions, affiliated_transactions,
         phantom_application_log) = prepare_books(
            transactions, sheltered_transactions, affiliated_transactions,
            taxable=req.taxable, incomplete_history=req.incomplete_history,
            phantom_hint=req.phantom_hint)

    rules = get_tax_rules(req.country)
    _extra = {}
    if (req.per_account_basis
            and (req.country or '').strip().lower() in ('us', 'usa')):
        _extra['per_account_basis'] = True
    if (req.country or '').strip().lower() not in ('us', 'usa'):
        _extra['option_premium_timing'] = req.option_premium_timing or 'close'
        _extra['option_grant_since'] = req.option_grant_since
        _extra['option_buyback_loss_superficial'] = req.option_buyback_loss_superficial
    results = rules.compute_gains(
        transactions,
        sheltered_transactions=sheltered_transactions,
        affiliated_transactions=affiliated_transactions,
        cross_asset=req.cross_asset,
        trace=req.trace,
        detect_wash_sales=req.effective_detect_wash(),
        **_extra,
    )

    # Capture tainted dispositions across ALL years before the year filter
    # strips them. The superficial-loss warning below pairs in-year clean
    # losses with tainted dispositions that may be in adjacent years —
    # a Dec 28 tainted leg can disqualify a Jan 5 in-year loss next year.
    all_tainted_across_years = [
        t for t in results.get('transactions', []) if t.get('tainted', False)
    ]

    # Filter by year if specified. --tax-date picks which date drives the
    # filter: 'trade' (default) or 'settle'. Trades that close on Dec 30/31
    # but settle on Jan 1/2 of the following year fall in different tax
    # years depending on this choice.
    if req.year:
        year_str = str(req.year)
        date_key = 'date_settle' if tax_date == 'settle' else 'date'
        _INCOME_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST',
                           'FEE')

        def in_year(rec, key=date_key):
            # Income belongs to the year it was RECEIVED (pay date) — no
            # settlement concept applies. All parsers currently stamp income
            # date_settle == pay date, but keying income on 'date' removes
            # the latent divergence (and the sum_income tool already filters
            # on 'date', so the .sum sections agree by construction).
            if rec.get('action') in _INCOME_ACTIONS:
                key = 'date'
            return (rec.get(key) or rec.get('date', '')).startswith(year_str)

        results['transactions'] = [t for t in results['transactions'] if in_year(t)]
        # Option-replacement warnings follow their LOSS date (already on
        # the engine's window basis) — an out-of-year loss's warning is
        # noise in a year-scoped report.
        if results.get('option_replacement_warnings'):
            results['option_replacement_warnings'] = [
                w for w in results['option_replacement_warnings']
                if (w.get('loss_date') or '').startswith(year_str)]
        results['summary']['year'] = year_str
        results['summary']['tax_date_basis'] = tax_date
        if (req.country or '').strip().lower() not in ('us', 'usa'):
            results['summary']['option_premium_timing'] = req.option_premium_timing or 'close'
            results['summary']['option_grant_since'] = req.option_grant_since
            results['summary']['option_buyback_loss_superficial'] = req.option_buyback_loss_superficial
        results['summary']['total_gain'] = sum(t.get('gain', 0.0) for t in results['transactions'])
        if 'wash_sales' in results:
            # Wash record shape diverges by engine. Canada builds
            # `{'loss_tx': v.to_dict(), ...}` — the date lives nested.
            # US builds `{'loss_tx_id': tx.id, 'date': tx.date, ...}` —
            # the date lives flat. The fallback `w.get('loss_tx', w)`
            # handles both: for Canada it returns the nested dict; for
            # US it returns `w` itself (which has `'date'`). Without
            # this fallback the US case silently filtered EVERY wash
            # record out under `--year`, zeroing `total_disallowed`.
            results['wash_sales'] = [
                w for w in results['wash_sales']
                if in_year(w.get('loss_tx', w))
            ]
            results['summary']['total_disallowed'] = sum(w['disallowed_amount'] for w in results['wash_sales'])

        # Rebuild by_ticker from year-filtered transactions. Without this,
        # consumers reading by_ticker['AAPL.US']['total_gain'] get all-year
        # totals on what looks like a single-year report.
        #
        # Skip `tainted` rows — they carry fabricated gain numbers (cost
        # basis = 0 against a synthetic/phantom opening) which are about
        # to be routed to `manual_reporting_required` below. Letting them
        # into by_ticker totals would silently inflate any downstream
        # consumer that reads by_ticker instead of `transactions`.
        if 'by_ticker' in results:
            rebuilt: Dict[str, Dict] = {}
            for t in results['transactions']:
                if t.get('tainted'):
                    continue
                sym = t.get('symbol')
                if not sym:
                    continue
                stats = rebuilt.setdefault(sym, {
                    'total_cost': 0.0, 'total_proceeds': 0.0,
                    'total_gain': 0.0, 'total_div': 0.0,
                    'total_pil': 0.0,
                    'trade_count': 0, 'hold_days': [],
                })
                action = t.get('action')
                if action == 'DIVIDEND':
                    stats['total_div'] += float(t.get('dividend', 0.0) or 0.0)
                elif action == 'DIVIDEND_IN_LIEU':
                    # PIL is its own bucket — same per-ticker association
                    # as a dividend but bucketed separately so downstream
                    # T5 totals stay clean. Before this branch, PIL rows
                    # fell into the trade accumulator below: trade_count
                    # got incremented and the PIL amount was invisible.
                    stats['total_pil'] += float(t.get('pil', 0.0) or 0.0)
                else:
                    stats['total_cost'] += float(t.get('cost', 0.0) or 0.0)
                    stats['total_proceeds'] += float(t.get('proceeds', 0.0) or 0.0)
                    stats['total_gain'] += float(t.get('gain', 0.0) or 0.0)
                    stats['trade_count'] += 1
                    if t.get('days_held') is not None:
                        stats['hold_days'].append(t['days_held'])
            results['by_ticker'] = rebuilt

    # Aggregate trading fees across the raw transaction list (both buys AND
    # sells, both taxable and sheltered) for informational reporting. Gain
    # entries only carry sell-side fees (buy-side fees are folded into cost
    # basis), so summing from the raw input is the only way to get a true
    # "total cost of trading" figure that consumers like taxjson-sum-gains
    # can show as a percentage of portfolio value.
    year_str_for_fees = str(req.year) if req.year else None
    fee_date_attr = 'date_settle' if tax_date == 'settle' else 'date'

    def fee_tx_in_year(tx) -> bool:
        if not year_str_for_fees:
            return True
        d = getattr(tx, fee_date_attr, '') or tx.date
        return d.startswith(year_str_for_fees)

    fees_by_currency: Dict[str, Dict[str, float]] = {}
    # TAXABLE transactions only (FUZZ #I): the cross-account wash pass
    # feeds this account's book PLUS sheltered_base — summing fees over
    # all three lists booked every sheltered account's fees into EVERY
    # taxable account's wash file, so `taxjson sum` counted an RRSP's
    # 14.95 three times. The sheltered accounts' own gains files carry
    # their own fees.
    for tx in list(transactions):
        if tx.action not in ('BUYSELL', 'ASSIGN'):
            continue
        if not fee_tx_in_year(tx):
            continue
        fee = float(tx.commission or 0) + float(tx.fee or 0)
        # != 0, not > 0: a commission REBATE (negative fee) must net
        # against the total, or `.sum` FEES disagrees with `fees-sum`
        # (which nets) by the rebate amount. Zero rows carry nothing.
        if fee == 0:
            continue
        curr = tx.currency or '?'
        is_opt = bool(re.search(r'\d{6}[CP]\d+', tx.symbol or ''))
        asset = 'options' if is_opt else 'stocks'
        bucket = fees_by_currency.setdefault(
            curr, {'stocks': 0.0, 'options': 0.0, 'total': 0.0}
        )
        bucket[asset] += fee
        bucket['total'] += fee
    results['summary']['total_fees_by_currency'] = fees_by_currency

    if req.trace and trace_sink is not None:
        trace_sink(results)

    # Split tainted dispositions (those drawing from a phantom pool OR a
    # TRANSFER opening) into a separate "manual_reporting_required"
    # section. Their gain values are bogus by construction (cost basis =
    # 0 on the synthetic opening), so they must not feed totals.
    # Tax-year filter (above) already trimmed everything to the year of
    # interest; we just bucket it here.
    clean_txs = []
    tainted_txs = []
    for t in results.get('transactions', []):
        if t.pop('tainted', False):
            # Strip the bogus gain numbers before surfacing — leave the
            # facts the user needs for manual reporting (date, qty,
            # proceeds, account) and drop the fabricated gain/cost.
            tainted_txs.append({
                k: v for k, v in t.items()
                if k not in ('gain', 'cost', 'taxable_gain', 'disallowed')
            })
        else:
            clean_txs.append(t)
    results['transactions'] = clean_txs
    if tainted_txs:
        results['manual_reporting_required'] = tainted_txs
    if phantom_application_log:
        results['phantom_application_log'] = phantom_application_log
    # Recompute total_gain from clean transactions only.
    if 'summary' in results and (tainted_txs or req.incomplete_history):
        results['summary']['total_gain'] = sum(t.get('gain', 0.0) for t in clean_txs)

    # Cross-year superficial-loss warning: an in-year clean loss within
    # ±30 days of a tainted disposition on the same symbol may be
    # affected by the superficial-loss rule. We can't compute the
    # adjustment because the tainted leg has unknown ACB — surface it
    # for manual resolution.
    if req.incomplete_history and all_tainted_across_years:
        clean_losses = [t for t in clean_txs if t.get('gain', 0.0) < -0.001]
        sl_warnings = detect_superficial_loss_warnings(clean_losses, all_tainted_across_years)
        if sl_warnings:
            results['superficial_loss_warnings'] = sl_warnings

    # Partial-taint warning: a TAINTED loss never feeds the wash-sale solver
    # (its number is fabricated against a zero-cost pool by construction),
    # but when most of the shares were REAL and an acquisition sits inside
    # the ±30-day window, the user manually reporting that loss must apply
    # CRA's superficial-loss rule BY HAND — previously there was zero signal.
    if tainted_txs:
        from datetime import datetime as _dt
        _pt_warns = []
        for t in tainted_txs:
            raw = float(t.get('raw_gain') or 0.0)
            if raw >= -0.001:
                continue
            try:
                d0 = _dt.strptime(t.get('date') or '', '%Y-%m-%d')
            except ValueError:
                continue
            for a in transactions:
                if (a.symbol != t.get('symbol')
                        or a.action not in ('BUYSELL', 'ASSIGN')
                        or a.quantity <= 0):
                    continue
                try:
                    da = _dt.strptime(a.date, '%Y-%m-%d')
                except ValueError:
                    continue
                if abs((da - d0).days) <= 30:
                    _pt_warns.append({
                        'symbol': t.get('symbol'),
                        'loss_date': t.get('date'),
                        'raw_loss': raw,
                        'acquisition_date': a.date,
                        'note': ('tainted (phantom-pool) loss with an '
                                 'in-window acquisition — if you claim this '
                                 'loss manually, apply the superficial-loss '
                                 'rule to the rebuy'),
                    })
                    break
        if _pt_warns:
            results.setdefault('superficial_loss_warnings', []).extend(_pt_warns)

    # Traces are emitted in the sidecar text file (when requested) — drop
    # them from the JSON unconditionally so stdout stays clean and
    # grep-friendly. The engine emits 'trace': [] even when trace=False, so
    # stripping is worth doing in both modes.
    for t in results.get('transactions', []):
        t.pop('trace', None)
    for t in results.get('manual_reporting_required', []):
        t.pop('trace', None)
    for w in results.get('wash_sales', []):
        w.pop('trace', None)

    return round_floats(results)


def option_timing_from_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """The engine kwargs for `[settings] option_premium_timing` /
    `option_grant_timing_since` (Canada). Default: statutory grant timing
    from the project year on — earlier contracts keep close timing so a
    premium that was open at a prior year end is not taxed nowhere."""
    country = str(settings.get("country", "canada")).strip().lower()
    if country in ("us", "usa"):
        return {}
    timing = str(settings.get("option_premium_timing", "grant")).strip().lower()
    since = settings.get("option_grant_timing_since")
    if since in (None, ""):
        since = settings.get("year")
    try:
        since = int(since) if since is not None else None
    except (TypeError, ValueError):
        since = None
    bb = settings.get("option_buyback_loss_superficial", False)
    return {"option_premium_timing": timing, "option_grant_since": since,
            "option_buyback_loss_superficial": bool(bb) if bb is not None else False}


def option_timing_flags(settings: Dict[str, Any]) -> List[str]:
    """The same choice as CLI flags for the taxjson-gains / audit / explain
    subprocesses."""
    kw = option_timing_from_settings(settings)
    if not kw:
        return []
    fl = ["--option-premium-timing", kw["option_premium_timing"]]
    if kw.get("option_grant_since") is not None:
        fl += ["--option-grant-since", str(kw["option_grant_since"])]
    if kw.get("option_buyback_loss_superficial", False):
        fl.append("--option-buyback-wash")
    return fl

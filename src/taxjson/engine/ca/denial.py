"""The superficial-loss denial pass: a loss's window and triggers, the day-30
walk, the allocation and ADJUST rows.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from taxjson.lib.sale_groups import disposition_groups
import functools
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from datetime import datetime, timedelta

from taxjson.lib.corporate_timeline import event_sort_key

from taxjson.model import money as M
from taxjson.lib.option_symbols import is_option_symbol, parse_option_expiry
from taxjson.lib.contract_size import option_contract_size
from taxjson.lib.row_types import LOT_MOVE_TYPE, OWN_MOVE_TYPE
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.assign import class_root_aliases
from taxjson.rules import ids as R
from taxjson.engine.consts import _ZERO, _MILLI, _NANO
from taxjson.engine.rows import AmbiguousTransferDateError, in_kind_record, override_record
from taxjson.engine.ca.run_helpers import (
    _sl_holder, _sl_holder_rank, _sl_call_und, _sl_pkey, _sl_symbol_asof, _sl_opening_qty,
    _sl_wpos, _ca_ev_key, _ca_opt_root, _ca_row_pool_key,
)
from taxjson.engine.ca.indexes import (
    _SL_WASH_AFTER_ACTIONS, WashPlace, _own_move_held, _ClassBalances, _WashAdjustIndex,
)
from taxjson.engine.ca.holders import _SL_BOOKKEEPING_ACTIONS, _SLLoss, _SLHeld, _SLPass

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.engine.rows import RowId, RuleTable
    from taxjson.engine.ca.holders import _CanadaIteration, _CanadaRun
    from taxjson.engine.ca.run_helpers import _EvKey, _Holder, _Renames


def _sl_same_fn(run: _CanadaRun, tx: TaxTransaction) -> Callable[[str, Any], bool]:
    """Whether shares of a symbol booked on a trade date are identical
    property to loss row `tx`'s (`SplitTimeline.identical`), one answer
    per pair: the class joins a rename's target whole, and the target's
    own rows before the split window of the rename are another security
    (CA-ACB-RENAME, backlog TJ-2699)."""
    memo: Dict[Tuple[str, Any], bool] = {}

    def same(sym: str, date: Any) -> bool:
        k = (sym, date)
        v = memo.get(k)
        if v is None:
            v = memo[k] = run.split_timeline.identical(
                tx.symbol, tx.date, sym, date)
        return v
    return same


def _sl_trigger_scan(*, loss: Dict[str, Any], pas: _SLPass, run: _CanadaRun) -> _SLLoss:
    """One loss of the denial pass, first part: the loss's settlement date, class alias
    and window; the class's purchases dated inside the window, from the kept index
    or the book; the call triggers (ITA s.54, closing words); the loop tests for none
    and keeps the loss. Moved out of the loss loop as it was (backlog TJ-1839);
    keyword-only: what the loop hands it. t_date is a local: its only read before
    its store is short-circuited by the condition that stores it."""
    tx = loss['tx']
    _gid = pas._disp_of.get(id(tx), id(tx))
    _trg_used, _grp_claim, _grp_state = pas._ledgers.setdefault(
        _gid, ({}, {}, {}))
    # SETTLEMENT-date basis for the whole ±30-day window (CRA's
    # disposition timing; matches get_sort_date and the config's
    # tax_date="settle" default). Mixing trade-date detection
    # with the settle-date balance walk below let a disallowance
    # flip near T+1/T+2 window edges — a rebuy at trade-day +30
    # but settle-day +32 was admitted as a trigger the balance
    # walk then excluded.
    loss_sort = run.get_sort_date(tx)
    loss_date = datetime.strptime(loss_sort, '%Y-%m-%d')
    # Bridge SPLIT-renames: a post-rename ABH.US buy in the
    # 30-day window after an ABG.TO loss is a candidate
    # trigger under CRA's substantially-identical rule. Match
    # on equivalence-class representative instead of raw
    # symbol. `alias_of` is identity for symbols never
    # renamed, so this is a no-op for the common case.
    loss_alias = run.row_cls(tx)
    # Within the class, identical property row by row (TJ-2699).
    same = _sl_same_fn(run, tx)
    potential_triggers = []
    # The class's purchases dated inside the window, in book
    # order (the walk of the whole book kept exactly these;
    # every date is ISO, require_computable_rows: TJ-2702).
    _trg_rows = pas._sl_trg.get(loss_alias)
    for t in (_trg_rows.within(loss_date.toordinal(), 30)
              if _trg_rows is not None else ()):
        if not same(t.symbol, t.date):
            continue
        # The loss row itself is a candidate only when it is
        # a cover that also OPENS a long (buy 150 while short
        # 100): its 50 new shares are identical property
        # acquired in the window (audit R1-29 — booked as
        # one row the loss was allowed, as two rows denied).
        # _sl_opening_qty keeps just that opening portion.
        if t.id == tx.id and not (t is tx and tx.quantity > 0
                                  and loss.get('direction')
                                  == 'SHORT'):
            continue
        # Only real acquisitions trigger a superficial loss.
        # SPLIT carries positive `quantity` (the ratio) and
        # was being misclassified as a long-side buy — a
        # 16-for-1 reorg on the same ticker post-loss could
        # silently disallow the entire loss. Same exclusion
        # for TRANSFER (informational), OPENING_BALANCE
        # (synthetic), and the bookkeeping actions: the index
        # holds BUYSELL and ASSIGN rows only (_SLIndexes.build).
        # A transfer-in at its book value (an arrival) and an
        # opening balance (a positions report's snapshot) are
        # not purchases: never a replacement, never "acquired
        # in the window" — their shares still count as held
        # at day 30 through the balances (CA-OPEN-01): the
        # index leaves them out.
        if t.quantity > 0:
            # Only the OPENING portion is replacement
            # property; a pure cover/close is not a
            # trigger (FUZZ #B).
            if _sl_opening_qty(pas, t) > pool_qty_eps(
                    t.symbol, t.quantity):
                if getattr(t, 'type', '') \
                        == 'transfer_rewrite':
                    raise AmbiguousTransferDateError(
                        f"{t.symbol}: a TRANSFER-in "
                        f"dated {t.date} (account "
                        f"{t.account}, qty "
                        f"{t.quantity:g}) sits inside "
                        f"the +/-30-day window of the "
                        f"{tx.date} loss sale, but its "
                        f"date is a broker ARRIVAL "
                        f"date that may not be an "
                        f"acquisition. If this was a "
                        f"custody/account move, add a "
                        f"counter-TRANSFER to a .tt "
                        f"file in the same account "
                        f"(TRANSFER {t.date} 09:30:00 "
                        f"{t.symbol} {-t.quantity:g} "
                        f"<cur> <price> <total> "
                        f"DECLARED — the trailing "
                        f"DECLARED token marks a "
                        f"deliberate declaration) "
                        f"so the pair nets out, plus a "
                        f"BUYSELL at the TRUE original "
                        f"acquisition date to keep the "
                        f"balance"
                        + (f" — or the one-line form: "
                           f"ACQUIRED <true-date> "
                           f"09:30:00 {t.symbol} "
                           f"{t.quantity:.8g} <cur> "
                           f"<price> <total> ARRIVED "
                           f"{t.date}"
                           if t.quantity > 0 else "")
                        + f"; if it was a genuine "
                        f"in-kind contribution, record "
                        f"it as a BUYSELL dated the "
                        f"contribution day instead.")
                potential_triggers.append(t)
    # ITA s.54, closing words para (i): "a right to acquire
    # a property ... is deemed to be a property that is
    # identical to the property". A LONG CALL on the loss
    # shares, opened inside the window and still owned at
    # day 30, is substituted property for a loss on LONG
    # shares (its contract size per contract: 100, or the
    # declared size of a mini — A2-0049). A futures option
    # is never sized as units here: it is flagged for a
    # manual check (CA-SL-15; A2-0014/0056). A class-share
    # root counts for its class line. Deliberately one-way
    # (user policy, 2026-09-29): shares never replace an
    # option, and a different option series never replaces
    # an option — an option's own loss washes only against
    # the identical contract (same symbol, above).
    call_triggers = []
    if (loss.get('direction', 'LONG') == 'LONG'
            and not is_option_symbol(tx.symbol)):
        assert pas._sl_call is not None, "built by the first pass with a LONG share loss (this one)"
        _call_rows = pas._sl_call.get(loss_alias)
        for t in (_call_rows.within(loss_date.toordinal(), 30)
                  if _call_rows is not None else ()):
            if t.id == tx.id:
                continue
            if _sl_opening_qty(pas, t) <= 1e-6:
                continue          # a buy-to-close acquires nothing
            _und = _sl_call_und(pas._cls_root, t.symbol)
            assert _und is not None, "a call row's underlying parses (_SLIndexes.build_calls)"
            if not same(_und, t.date):
                continue          # a call on the target's own shares
            call_triggers.append(t)
    potential_triggers.extend(call_triggers)
    _call_ids = {t.id for t in call_triggers}
    _call_syms = {t.symbol for t in call_triggers}
    end_window_date = (loss_date + timedelta(days=30)).strftime('%Y-%m-%d')
    return _SLLoss(loss=loss, tx=tx, loss_date=loss_date, loss_alias=loss_alias,
                   same=same,
                   end_window_date=end_window_date, loss_sort=loss_sort,
                   loss_pre=_sl_pre_exists(tx, loss_sort, run=run),
                   potential_triggers=potential_triggers,
                   call_triggers=call_triggers, _call_ids=_call_ids,
                   _call_syms=_call_syms, _gid=_gid, _grp_claim=_grp_claim,
                   _grp_state=_grp_state, _trg_used=_trg_used)


def _sl_pre_exists(t: TaxTransaction, d: str, *, run: _CanadaRun) -> bool:
    """Whether row `t` pre-exists its sort date `d`, the denial pass's phase
    boundary (the main pass's ladder): a settle-lagged execution dated
    before `d` does; an OPENING_BALANCE does, a position the day opened
    with, unless it is stated in a symbol a SPLIT placed on `d` created (the
    restated post-event position: `SplitTimeline.created_on`, on the pass's
    own date basis, TJ-2314); a row executed that day does not. The one
    reading for the share and the call unit helpers and the loss row
    (TJ-1957, TJ-2315)."""
    if t.action == 'OPENING_BALANCE':
        return not run.split_timeline.created_on(t.symbol, d)
    return bool(t.date and t.date < d)


def _sl_row_loss_units(t: TaxTransaction, q: float, *, rec: _SLLoss, run: _CanadaRun) -> float:
    """`q`, denominated in `t`'s raw symbol at `t`'s sort date, in loss-date
    loss-symbol units, through the split lineage. A row that pre-exists its
    sort date (`_sl_pre_exists`) is scaled by a same-date SPLIT, one
    executed that day is not (the day-30 walk's unit helper, backlog
    TJ-1873)."""
    d = run.get_sort_date(t)
    pre = _sl_pre_exists(t, d, run=run)
    return q * run.split_timeline.lineage_factor(
        t.symbol, d, rec.tx.symbol, rec.loss_sort,
        from_inclusive=pre, ref_inclusive=rec.loss_pre)


def _sl_call_units(t: TaxTransaction, q: float, *, pas: _SLPass, rec: _SLLoss,
                   run: _CanadaRun) -> float:
    """`q` contracts of a call on the loss shares, in loss-date loss-symbol
    share units: the contract size per contract, through the underlying's
    split lineage (backlog TJ-1873); the share helper's boundary,
    `_sl_pre_exists` (TJ-1957)."""
    d = run.get_sort_date(t)
    pre = _sl_pre_exists(t, d, run=run)
    _und = _sl_call_und(pas._cls_root, t.symbol)
    assert _und is not None, "a call trigger's underlying parses (_SLIndexes.build_calls)"
    return q * option_contract_size(t) * run.split_timeline.lineage_factor(
        _und, d, rec.tx.symbol,
        rec.loss_sort, from_inclusive=pre, ref_inclusive=rec.loss_pre)


def _sl_avail_native(t: TaxTransaction, *, pas: _SLPass, rec: _SLLoss) -> float:
    """What `t` opened less what earlier fills of the sale already used."""
    return max(0.0, _sl_opening_qty(pas, t)
               - rec._trg_used.get(t.id, 0.0))


def _sl_gone_by_end(t: TaxTransaction, *, rec: _SLLoss) -> bool:
    """A contract that expires by day 30 is not owned at the end of day 30,
    with or without an expiry row in the export (audit S071-17: a missing
    EXP row parked a realized long-option loss on a contract that can never
    be sold); one expiring on day 30 is gone by its end too (TJ-0725); an
    exercise into shares is its own row."""
    return (is_option_symbol(t.symbol)
            and (parse_option_expiry(t.symbol) or '9999')
            <= rec.end_window_date)


def _sl_identical_units(cb: _ClassBalances, same: Callable[[str, Any], bool], pas: _SLPass,
                        run: _CanadaRun, units: Callable[[TaxTransaction, float], float]
                        ) -> List[Tuple[TaxTransaction, float]]:
    """The class's balance rows as (row, units) pairs in the loss's units,
    the identical-property view of one loss (TJ-2699; TJ-3289, TJ-3290):
    a row identical to the loss (`same`, the loss's `_sl_same_fn`) is kept.
    A purchase that is not (a rename target's own shares bought before the
    split window of the rename) is never an acquisition and is left out,
    but its units are the holder's older units of the pool that holds
    them, which that pool's later sales draw first (FIFO, the walk's
    convention): a target sale inside the window or after the rename
    reduces the identical holding only past them, so after the rename the
    merged pool's balance is the identical property held. The older units
    are keyed by the pool as of each row (`_sl_symbol_asof`: a source's go
    to the target's key once the rename applies, TJ-3299); a kept purchase
    first nets the pool's negative older units (a short in the target
    opened before the window, covered inside it: TJ-3300); an own move's
    out leg carries what it took from them to its paired in leg, which
    holds them as older units of its own account (TJ-3298). A one-symbol
    class is kept whole."""
    if cb.unit_symbol is not None:
        return [(t, t.quantity * 1.0) for t in cb.rows]
    renames = pas._renames
    own: Dict[Tuple[_Holder, str], float] = {}
    pend: Dict[Tuple[str, str], List[float]] = {}      # (symbol, move type): takes
    out = []
    for t in cb.rows:
        q = units(t, t.quantity)
        if renames and own:
            k = _ca_ev_key(run, t)
            for (oh, os), n in list(own.items()):
                ns = _sl_symbol_asof(renames, os, k)
                if ns != os:
                    own[(oh, ns)] = own.get((oh, ns), 0.0) + own.pop((oh, os))
        key = (_sl_holder(run, t), t.symbol)
        kind = (getattr(t, 'type', '') if t.action == 'TRANSFER' else '')
        leg = kind in (OWN_MOVE_TYPE, LOT_MOVE_TYPE)
        if not same(t.symbol, t.date):
            own[key] = own.get(key, 0.0) + q
            continue
        if q < 0.0 and own.get(key, 0.0) > 0.0:
            take = min(own[key], -q)
            own[key] -= take
            q += take
            if leg:
                pend.setdefault((t.symbol, kind), []).append(take)
        elif q > 0.0:
            todo = pend.get((t.symbol, kind), []) if leg else []
            while todo and q > 0.0:
                n = min(todo[0], q)
                todo[0] -= n
                q -= n
                own[key] = own.get(key, 0.0) + n
                if todo[0] <= 0.0:
                    todo.pop(0)
            if q > 0.0 and own.get(key, 0.0) < 0.0:
                n = min(-own[key], q)
                own[key] += n
                q -= n
        out.append((t, q))
    return out


def _sl_window_walk(*, pas: _SLPass, rec: _SLLoss, run: _CanadaRun) -> _SLHeld:
    """One loss of the denial pass, second part: what each holder still owns of the
    class at the end of day 30 (ITA s.54: the trigger must be an acquisition still
    owned then), from the class's balance rows, the own moves between the accounts
    and the per-contract call holdings; the substituted property held, per holder
    and in all. Moved out of the loss loop as it was (backlog TJ-1839); keyword-only:
    what the loop hands it."""
    # Never compare share quantities denominated at different
    # dates OR different raw symbols: a SPLIT inside the window
    # leaves balances in day-+30 units while loss['qty'] /
    # trigger quantities are in their own trade-date units —
    # min() across them corrupted disallowed_qty (a 1-for-10
    # reverse split shrank a $500 disallowance to $50). Every
    # quantity is therefore normalized to LOSS-DATE units of
    # the LOSS SYMBOL at the point it enters a sum, via a
    # PER-RAW-SYMBOL lineage factor (settle-basis dates; one
    # shared definition in lib/corporate_timeline.py). The old
    # class-wide alias_factor pooled every ratio of the rename
    # class, so a rename-split A->B into a symbol that ALREADY
    # TRADES divided native-B balances — which the event never
    # scaled — by the rename ratio too (2026-09 adversarial
    # audit, finding 1: $2,000 of superficial loss denied as
    # $1,800). Because each row converts independently, the
    # walks no longer need per-symbol fold dicts: SPLIT rows
    # contribute nothing and rename re-keying is implicit in
    # the lineage path.

    # The unit helpers over this loss (module functions, backlog TJ-1873),
    # bound to the loss's objects for the walk's own calls and for
    # `_own_move_held`.
    _row_loss_units = functools.partial(_sl_row_loss_units, rec=rec, run=run)
    _call_units = functools.partial(_sl_call_units, pas=pas, rec=rec, run=run)
    _gone_by_end = functools.partial(_sl_gone_by_end, rec=rec)

    # The class's balance rows (BUYSELL, ASSIGN, TRANSFER and
    # OPENING_BALANCE — missing-history shares are held) in processing order;
    # the first _k_end are dated by day 30 (None: not in date
    # order, every row is tested). _unit: no split or rename
    # acts on the class, so its quantities are loss units.
    _cb = _sl_class_balances(pas, run, rec.loss_alias)
    _k_end = _cb.prefix(rec.end_window_date)
    _unit = (_cb.unit_symbol == rec.tx.symbol
             and _k_end is not None)
    # The identical-property view: (row, units) pairs (`_sl_identical_units`).
    rows = _sl_identical_units(_cb, rec.same, pas, run, _row_loss_units)

    # ITA s.54: the trigger must be an ACQUISITION of identical
    # property still OWNED at the end of the window. A new
    # sell-to-open (short sale or written option) acquires
    # nothing, so the US §1091(e) "re-short" branch does not
    # apply here: every loss uses the LONG criteria — opening
    # long acquisitions, positive balance at day 30.
    #
    # "Owns the SUBSTITUTED property" — the property acquired
    # in the window — is measured PER HOLDER: the taxable
    # book (one s.47 pool across the taxpayer's taxable
    # accounts) and each registered / affiliated account on
    # its own. A holder backs a denial only with units it
    # ACQUIRED inside the window and still holds at its end:
    # min(acquired in window, balance at end) — FIFO, since
    # a later sale disposes of the oldest units first. Units
    # a registered account held BEFORE the window neither
    # create nor back a denial: a class-wide balance let an
    # RRSP's 2020 shares turn a taxable rebuy that was sold
    # again inside the window into a PERMANENT denial (2026-09
    # engine audit).
    # (_sl_holder: the pass's, over the run.)
    _bal_end_h: Dict[Any, float] = {}
    if _unit:
        assert _k_end is not None, "`_unit` holds only with `_k_end` set"
        if not _gone_by_end(rec.tx):
            _bal_end_h = _cb.holder_sums(_k_end)
    else:
        for t, q in rows:
            if (not _gone_by_end(t)
                    and run.get_sort_date(t) <= rec.end_window_date):
                _h = _sl_holder(run, t)
                _bal_end_h[_h] = _bal_end_h.get(_h, 0.0) + q
    # Each holder backs this sale with min(acquired in the
    # window, held at day 30), less what an earlier fill of
    # the same sale already claimed from it (CA-SL-08). A
    # unit another sale claimed still backs this one.
    _acq_h: Dict[Any, float] = {}
    for t in rec.potential_triggers:
        if t.id in rec._call_ids:
            continue
        _h = _sl_holder(run, t)
        _acq_h[_h] = (_acq_h.get(_h, 0.0)
                      + (_sl_opening_qty(pas, t) * 1.0 if _unit
                         else _row_loss_units(t,
                                              _sl_opening_qty(pas, t))))
    _held_h = {h: max(0.0, min(a, _bal_end_h.get(h, 0.0))
                      - rec._grp_claim.get(h, 0.0))
               for h, a in _acq_h.items()}
    # A move of your own between two of the accounts
    # (balance-only TRANSFER rows, row_types.OWN_MOVE_TYPE)
    # inside the window carries the units it moves: those
    # acquired in the window back a denial in the account
    # that holds them at day 30 (backlog TJ-0654: r1's
    # replacement moved to r2 and held there was lost, the
    # sender having no units and the receiver no purchase),
    # and older units moved in back nothing.
    _held_mv = _own_move_held(
        rec.loss_date, rec.end_window_date, rows,
        rec.potential_triggers, rec._call_ids, _gone_by_end,
        (lambda t, q: q) if _unit else _row_loss_units, pas=pas, run=run)
    if _held_mv is not None:
        _held_h = {h: max(0.0, n - rec._grp_claim.get(h, 0.0))
                   for h, n in _held_mv.items()}
    # Calls back a denial per (holder, contract): units of
    # THAT series opened in the window and still held at day
    # 30 — a contract bought, sold and bought again counts
    # once (A2-0057/0198). Share balances never back a call
    # and vice versa.
    if rec.call_triggers:
        # A series and the series a SPLIT replaced it with
        # are one contract here (TJ-0716): keyed by the
        # lineage's first symbol, every symbol of it walked.
        _call_acq: Dict[Any, float] = {}
        for t in rec.call_triggers:
            _k = ('call', _sl_holder(run, t), _ca_opt_root(run, t.symbol))
            _call_acq[_k] = (_call_acq.get(_k, 0.0)
                             + _call_units(t, _sl_opening_qty(pas, t)))
        _call_end: Dict[Any, float] = {}
        _call_roots = {_ca_opt_root(run, sym) for sym in rec._call_syms}
        _call_walk = sorted(
            {sym for _r in _call_roots
             for sym in run._opt_lineage.get(_r, (_r,))})
        # A call that expires by day 30 (on it too: TJ-0725)
        # is not held at the end of day 30, with or without
        # an expiry row (S071-15).
        _expired = {sym for sym in _call_walk
                    if (parse_option_expiry(sym) or '9999')
                    <= rec.end_window_date}
        for t in (t for _cs in _call_walk
                  for t in pas._sl_bal_sym.get(_cs, ())):
            if (t.symbol not in _expired
                    and run.get_sort_date(t) <= rec.end_window_date):
                _k = ('call', _sl_holder(run, t), _ca_opt_root(run, t.symbol))
                _call_end[_k] = (_call_end.get(_k, 0.0)
                                 + _call_units(t, t.quantity))
        for _k, a in _call_acq.items():
            _held_h[_k] = max(0.0, min(a, _call_end.get(_k, 0.0))
                              - rec._grp_claim.get(_k, 0.0))
    held_substituted = sum(_held_h.values())
    return _SLHeld(_cb=_cb, _k_end=_k_end, _unit=_unit, rows=rows,
                   _held_h=_held_h, held_substituted=held_substituted)


def _sl_window_trace(*, disallowed_qty: float, held: _SLHeld, pas: _SLPass, rec: _SLLoss,
                     run: _CanadaRun) -> None:
    """The denied loss's ±30-day window for the report (`wash_windows`): the symbol's
    rows and the call replacements dated inside it, in book order, each with its
    part in the denial, sorted as the main pass applied them. Reporting only: no
    figure depends on it. Moved out of the allocation as it was (backlog TJ-1875);
    keyword-only: what the allocation hands it."""
    window_start_dt = rec.loss_date - timedelta(days=30)
    window_end_dt = rec.loss_date + timedelta(days=30)
    win_txs: List[Dict[str, Any]] = []
    seen: Set[RowId] = set()
    # The loss symbol's rows dated inside the window and
    # the call replacements, in book order (the walk of
    # the whole book kept only these). Dated, as the
    # window and the DAY count are, on SETTLEMENT dates
    # like the detection in _sl_trigger_scan: a trade date counted
    # from the loss's settlement put the loss sale at
    # T-1 (backlog TJ-0258).
    _wrows = pas._sl_sym.get(rec.tx.symbol)
    _wscan = list({id(t): t for t in (
        (_wrows.within(rec.loss_date.toordinal(), 30)
         if _wrows is not None else [])
        + rec.call_triggers)}.values())
    _wscan.sort(key=lambda t: pas._sl_book_pos[id(t)])
    # The running tally of the identical property after each balance row,
    # in the loss's units: the day-30 holding's own view, in the walk's
    # order (TJ-3295); a row the view has not (a SPLIT) shows none.
    _tally: Dict[RowId, float] = {}
    _acc = 0.0
    for _t, _q in held.rows:
        _acc += _q
        _tally[_t.id] = _acc
    for t in _wscan:
        if t.id in seen:
            continue
        seen.add(t.id)
        if t.symbol != rec.tx.symbol and t.id not in rec._call_ids:
            continue
        if t.action in _SL_BOOKKEEPING_ACTIONS:
            continue
        try:
            t_dt = datetime.strptime(run.get_sort_date(t),
                                     '%Y-%m-%d')
        except (TypeError, ValueError):
            continue
        if not (window_start_dt <= t_dt <= window_end_dt):
            continue
        days_from = (t_dt - rec.loss_date).days
        if t.id == rec.tx.id:
            role = 'loss_sale'
        elif t.id in run.loss_to_triggers_multi.get(rec.tx.id, []):
            role = 'trigger'
        elif (t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER')
              and t.quantity > 0
              and rec.loss['direction'] == 'LONG'
              and _sl_opening_qty(pas, t) <= pool_qty_eps(
                  t.symbol, t.quantity)):
            # A buy that only closes a short (a written
            # call bought back) acquires nothing: never
            # a trigger (audit S069-24 — it read
            # "eligible").
            role = 'cover'
        elif t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER') and t.quantity > 0:
            role = 'candidate'
        elif t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER') and t.quantity < 0:
            role = 'other_sell' if rec.loss['direction'] == 'LONG' else 'other_buy'
        else:
            role = 'context'
        win_txs.append({
            'tx_id': t.id,
            'date': t.date,
            'date_settle': run.get_sort_date(t),
            'symbol': t.symbol,
            'days_from_loss': days_from,
            'account': t.account,
            'action': t.action,
            'qty': float(t.quantity),
            'price': M.of(t.price),
            'sheltered': t.id in run.sheltered_ids,
            'affiliated': t.id in run.affiliated_ids,
            'role': role,
            'running_bal': _tally.get(t.id),
        })
    # Processing order, so the running balance reads in
    # the order the main pass applied the rows (A2-0965).
    _wpos = _sl_wpos(pas, run)
    win_txs.sort(key=lambda r: (r['days_from_loss'],
                                r['date'],
                                _wpos.get(r['tx_id'], -1)))
    _bal_at_end: float
    if held._unit:
        assert held._k_end is not None, "`_unit` holds only with `_k_end` set (_sl_window_walk)"
        _bal_at_end = (0 if _sl_gone_by_end(rec.tx, rec=rec) else held._cb.total(held._k_end))
    else:
        _bal_at_end = sum(q for t, q in held.rows
                          if not _sl_gone_by_end(t, rec=rec)
                          and run.get_sort_date(t) <= rec.end_window_date)
    run.wash_windows[rec.tx.id] = {
        'window_start': window_start_dt.strftime('%Y-%m-%d'),
        'window_end': rec.end_window_date,
        'loss_date': rec.tx.date,
        # The date the window and DAY count from.
        'loss_settle': rec.loss_sort,
        'loss_direction': rec.loss['direction'],
        'bal_at_end': _bal_at_end,
        'loss_qty': rec.loss['qty'],
        'disallowed_qty': disallowed_qty,
        'transactions': win_txs,
    }


def _sl_trigger_holder(trg: TaxTransaction, *, pas: _SLPass, rec: _SLLoss, run: _CanadaRun
                       ) -> Tuple[Tuple[Any, ...], _Holder, float]:
    """A trigger's keys for the allocation: its holder key in the day-30 walk's
    terms (a call's is per contract, `_sl_window_walk`), its holder, and its
    loss-date units per native unit."""
    _hh = _sl_holder(run, trg)
    if trg.id in rec._call_ids:
        return (('call', _hh, _ca_opt_root(run, trg.symbol)), _hh,
                _sl_call_units(trg, 1.0, pas=pas, rec=rec, run=run))
    return _hh, _hh, _sl_row_loss_units(trg, 1.0, rec=rec, run=run)


def _sl_pool_rows(*, account: Optional[str], pas: _SLPass, rec: _SLLoss, run: _CanadaRun,
                  symbol: str) -> Tuple[List[TaxTransaction], Optional[TaxTransaction]]:
    """One pool's rows in the loss's window, in processing order, each a
    position change of the pool itself with the walk's pool units after it
    (`pool_snapshots_this_iter`): a purchase, a sale, an assignment's share
    leg, an opening balance; never a split or rename row (the pool at 0 after
    a rename's merge read as a sell-out and the loss was lost, TJ-2760), a
    transfer the walk skips, a row of another holder, or a marked or futures
    short's. The symbol's rows, then the symbols a rename moves the pool to
    by day 30 (`pas._renames`). `account` None: the taxable pool; else that
    registered or affiliated account's own. Returns (rows, the last row
    after which the pool holds no long position, None for none; the rows'
    processing positions are the pass's, read at use, since the virtual rows
    a pass adds shift them). Built once per window, pool and tolerance for the run
    (`run._sl_idx.pool_rows`: the rows and their units are the book's, the
    same for a sale's fills and on every solver pass): the sold-out test of
    every trigger is then a comparison of positions and the landing walk
    reads the same rows, so the cost is the window's rows per pool, not per
    trigger or per pass (TJ-2759)."""
    _eps = pool_qty_eps(symbol, rec.loss['qty'])
    assert run._sl_idx.pool_rows is not None, "built by the first pass (_SLIndexes.build)"
    _cache: Dict[Tuple[Any, ...], Tuple[List[TaxTransaction], Optional[TaxTransaction]]] = run._sl_idx.pool_rows
    _ck = (rec.loss_sort, rec.end_window_date, symbol, account, _eps)
    if _ck in _cache:
        return _cache[_ck]
    snaps = pas.pool_snapshots_this_iter
    _eow_key = (rec.end_window_date, 99, '99:99:99', 99)

    def _own(t: TaxTransaction, lo_key: Optional[_EvKey]) -> bool:
        if (t.action not in _SL_WASH_AFTER_ACTIONS or t.id not in snaps
                or t.id in run._short_routed
                or _ca_row_pool_key(run, t) != t.symbol
                or run.get_sort_date(t) > rec.end_window_date
                or (lo_key is not None and _ca_ev_key(run, t) <= lo_key)):
            return False
        other = t.id in run.sheltered_ids or t.id in run.affiliated_ids
        if account is None:
            return not other
        return other and t.account == account

    rows: List[TaxTransaction] = []
    sym: Optional[str]
    lo_key: Optional[_EvKey]
    sym, lo_key, seen = symbol, None, set()
    while sym and sym not in seen:
        seen.add(sym)
        _idx = pas._sl_sym.get(sym)
        rows += [t for t in (_idx.within(rec.loss_date.toordinal(), 30)
                             if _idx is not None else ())
                 if _own(t, lo_key)]
        _nxt = pas._renames.get(sym)
        sym, lo_key = ((_nxt[1], _nxt[0]) if _nxt and _nxt[0] <= _eow_key
                       else (None, None))
    rows.sort(key=lambda t: pas._pos.get(id(t), -1))
    # The pool's units after each row: the walk's snapshot for the taxable
    # pool; for a registered or affiliated account's own pool the holder's
    # running balance (`pas._bal_before`, keyed by account), since a
    # sheltered row's snapshot is the taxable pool's (TJ-2767: an RRSP
    # trigger read as sold out when the taxable pool was flat).
    _last_flat = None
    for t in rows:
        _after = (snaps[t.id]['pool_qty'] if account is None
                  else pas._bal_before.get(t.id, 0.0) + t.quantity)
        if _after <= _eps:
            _last_flat = t
    _cache[_ck] = (rows, _last_flat)
    return rows, _last_flat


def _sl_sold_out(trg: TaxTransaction, *, pas: _SLPass, rec: _SLLoss, run: _CanadaRun) -> bool:
    """Is the trigger's position sold out before the end of day 30: a row of
    its own pool after it, by day 30, after which the pool holds no long
    position? Such a rebuy is not a replacement (s.54: the substituted
    property is owned at the end of the window), whatever the pool holds
    again later (TJ-2761, TJ-2762: it took the denial first and the addition
    then moved whole onto the first later purchase, or displaced a registered
    holder's earlier one)."""
    assert run._sl_idx.sold_out is not None, "built by the first pass (_SLIndexes.build)"
    _memo: Dict[Tuple[Any, ...], bool] = run._sl_idx.sold_out
    _mk = (rec.loss_sort, rec.end_window_date, trg.id, rec.loss['qty'])
    _v = _memo.get(_mk)
    if _v is None:
        _acct = (trg.account if (trg.id in run.sheltered_ids
                                 or trg.id in run.affiliated_ids) else None)
        _rows, _last_flat = _sl_pool_rows(account=_acct, pas=pas, rec=rec,
                                          run=run, symbol=trg.symbol)
        _v = _memo[_mk] = (
            _last_flat is not None
            and pas._pos.get(id(trg), -1) < pas._pos.get(id(_last_flat), -1))
    return _v


def _sl_sale_alloc(*, held: _SLHeld, loss_pkey: Tuple[_EvKey, int], pas: _SLPass, rec: _SLLoss,
                   run: _CanadaRun) -> List[Tuple[TaxTransaction, float]]:
    """The sale's denied units over its triggers, once per sale; its fills
    share it, each taking its units' share of every trigger's take
    (TJ-2720, TJ-2756, TJ-2758: fill by fill, the fill listed first took
    the first holder whole, and a purchase between two fills was after
    the sale for one fill and before it for the other). The triggers in
    acquisition order (CA-SL-10) against the sale's own moment, its last
    losing fill (`loss_pkey`: a purchase between its fills is a purchase
    before the sale, as the ACB addition of CA-SL-08 is placed after the
    last fill), each capped at what it opened and at its holder's units
    held at day 30, given the whole sale's denied quantity (CA-SL-08's
    min(S, P, B), the fills' units together). A trigger whose position is
    sold out before day 30 is not a replacement (`_sl_sold_out`; TJ-2761,
    TJ-2762): the replacements held at day 30 draw first, as if the
    round trip were not in the book, and a sold-out one only when they
    cannot cover a holder's units, which is when no purchase opened the
    position held at day 30 (a rename or merger, an opening balance: its
    addition then waits, `_sl_landing_row`). Returns [(trigger, units)]
    in that order."""
    # Processing order, so a rebuy listed after a same-moment loss sale
    # is a purchase AFTER it (CA-DATE-14 / CA-SL-10; A2-0058). Triggers
    # at the SAME moment: the taxpayer's own (taxable) acquisition first,
    # then registered, then affiliated accounts; within one rank, the
    # main pass's processing order (export row order, accounts in name
    # order, CA-DATE-14), never the rows' content-hash id (audits
    # S018-06, A2-0192/0961/0965). The denial is permanent only for
    # property an affiliated person acquires (s.40(2)(g)(i)); with no
    # order between the two acquisitions the taxpayer's own substituted
    # property is the one s.53(1)(f) reaches first. Post-loss: earliest
    # first; pre-loss: latest first (the last-listed of a same-moment
    # group is the latest acquisition).
    def _order(triggers: List[TaxTransaction]) -> List[TaxTransaction]:
        post_loss = [t for t in triggers
                     if _sl_pkey(run, pas._pos, t) > loss_pkey]
        _post_ids = {id(t) for t in post_loss}
        pre_loss = [t for t in triggers if id(t) not in _post_ids]
        return (sorted(post_loss,
                       key=lambda x: (_ca_ev_key(run, x),
                                      _sl_holder_rank(run, x),
                                      pas._pos.get(id(x), -1)))
                + sorted(pre_loss,
                         key=lambda x: (_ca_ev_key(run, x),
                                        -_sl_holder_rank(run, x),
                                        pas._pos.get(id(x), -1)),
                         reverse=True))
    # The order once per sale and last losing fill for the run (the book
    # rows' relative order is the same on every pass); the sold-out
    # triggers after the others, in that order (TJ-2769).
    assert run._sl_idx.ordered is not None, "built by the first pass (_SLIndexes.build)"
    _memo: Dict[Tuple[Any, ...], List[TaxTransaction]] = run._sl_idx.ordered
    _mk = (rec._gid, _sl_pkey(run, pas._pos, rec.tx)[0], loss_pkey[0])
    ordered = _memo.get(_mk)
    if ordered is None:
        _all = _order(rec.potential_triggers)
        gone = {id(t) for t in _all if _sl_sold_out(t, pas=pas, rec=rec, run=run)}
        ordered = _memo[_mk] = ([t for t in _all if id(t) not in gone]
                                + [t for t in _all if id(t) in gone])
    # Each trigger capped at its OPENING quantity in loss-date units AND
    # at its holder's still-held substituted units (FUZZ #D: routing the
    # whole denial to one trigger made the answer depend on rebuy order).
    _S = pas._grp_units.get(rec._gid, rec.loss['qty'])
    _rem = min(_S, held.held_substituted)
    _cap_left = dict(held._held_h)
    out = []
    for trg in ordered:
        _h, _hh, _per = _sl_trigger_holder(trg, pas=pas, rec=rec, run=run)
        take = min(_rem, _per * _sl_avail_native(trg, pas=pas, rec=rec),
                   _cap_left.get(_h, 0.0))
        if take > 1e-9:
            out.append((trg, take))
            _cap_left[_h] -= take
            _rem -= take
        if _rem <= 1e-9:
            break
    return out


def _sl_landing_row(*, pas: _SLPass, rec: _SLLoss, run: _CanadaRun, start: TaxTransaction,
                    symbol: str) -> Optional[TaxTransaction]:
    """The row a denied loss's addition is placed after when the pool it is
    keyed to (`symbol`: the pool as of the placement, shares or a call)
    holds no long position at some point between the placement (`start`:
    the trigger purchase; the loss sale's last fill for a pre-loss trigger)
    and the end of day 30: the last row by day 30 that leaves the pool so.
    Flat there, the addition waits on the flat pool (CA-SL-19) for what
    opens the position held at day 30; sold short there (a sale past the
    holding), it joins the first later row that brings the pool long, since
    it is the long position's, never a short's (CA-SL-07). None when the
    pool holds units from the placement to day 30 (the placement stays: the
    addition is part of the replacement's cost from its acquisition,
    A2-0555), when it is flat at the placement and never sold out after (it
    waits there already), or when it holds no long position at day 30.
    Since a trigger sold out before day 30 is no replacement
    (`_sl_sold_out`), this is reached only for the fallback: no purchase
    opened the position held at day 30 (the shares come by a rename or
    merger, an opening balance: TJ-2754), or a loss sale's own last fill
    sold the pool out. The pool's units after each of its rows are the
    walk's own (`_sl_pool_rows`), read for the pool's rows after the
    placement only; its units at the placement are the placement row's own
    state, or the state before the pool's first later row (TJ-2759,
    TJ-2766)."""
    snaps = pas.pool_snapshots_this_iter
    _eps = pool_qty_eps(symbol, rec.loss['qty'])
    _pos0 = pas._pos.get(id(start), -1)
    if _pos0 < 0:
        return None
    _rows, _last_flat = _sl_pool_rows(account=None, pas=pas, rec=rec, run=run,
                                      symbol=symbol)
    rows = [t for t in _rows if pas._pos.get(id(t), -1) > _pos0]
    if not rows:
        return None
    if start.symbol == symbol and start.id in snaps:
        _q0 = snaps[start.id]['pool_qty']
    else:
        # The state before the pool's first later row: its units after
        # it less its own (a split between scales both: the sign holds).
        _q0 = snaps[rows[0].id]['pool_qty'] - rows[0].quantity
    landing, short = None, _q0 < -_eps
    for t in rows:
        q = snaps[t.id]['pool_qty']
        if q <= _eps:
            landing, short = t, q < -_eps
        elif short:
            landing, short = t, False
    return None if short else landing


def _sl_mk_adjust(trg: TaxTransaction, amt: Decimal, *, land: Dict[RowId, TaxTransaction],
                  loss_key: _EvKey, loss_pkey: Tuple[_EvKey, int], pas: _SLPass, rec: _SLLoss,
                  run: _CanadaRun) -> TaxTransaction:
    """The denied loss's addition to a replacement's ACB (s.53(1)(f); CA-SL-09):
    a virtual ADJUST placed right after the trigger purchase, or after the loss
    sale's last fill for a pre-loss trigger, keyed to the pool that holds the
    substituted property at day 30 (`land`: `_adjust_land`'s routing) — and
    when that pool holds no long position at some point between the placement
    and day 30, placed where the position held at day 30 begins
    (`_sl_landing_row`; TJ-2711, TJ-2753, TJ-2754). Moved out of
    `_sl_allocate` as it was."""
    a_id = f"WASH_{rec.tx.id}__{trg.id}"
    _pre_loss = _sl_pkey(run, pas._pos, trg) < loss_pkey
    # A pre-loss bump follows its sale's LAST fill (one sale, one
    # formula: CA-SL-08), so every later sale sees it (R1-31).
    _last_fill = pas._disp_last.get(pas._disp_of.get(id(rec.tx), id(rec.tx)), rec.tx)
    if _pre_loss:
        # Pre-loss trigger: the bump lands just
        # after the loss sale — same settle date and
        # phase (same trade date), one second later.
        a_date, a_settle = rec.tx.date, rec.tx.date_settle
        # Loader accepts time='' — don't crash the
        # engine on it (2026-09 audit).
        a_time = (datetime.strptime(
                      rec.tx.time or '09:30:00',
                      '%H:%M:%S')
                  + timedelta(seconds=1)
                  ).strftime('%H:%M:%S')
        if a_time == '00:00:00':
            a_time = '23:59:59'
    else:
        a_date, a_settle = trg.date, trg.date_settle
        a_time = trg.time
    # Sign follows the loss side (post-FUZZ-#B every
    # trigger OPENS on that side, so the proxy is
    # sound); symbol keyed to the trigger's pool AS
    # OF the adjust date — following renames so the
    # bump lands on the LIVE pool (FUZZ #F8).
    a_amt = amt                  # bump the LONG replacement's ACB (s.53(1)(f))
    # Backing-routed landing (see _adjust_land):
    # the pool that still holds the substituted
    # property, named as of the adjust date so the
    # bump enters it while live and rides any later
    # rename with the pool's own state.
    _land = land.get(trg.id, trg)
    v = TaxTransaction(action='ADJUST', date=a_date,
                       time=a_time, symbol=_land.symbol,
                       currency=rec.tx.currency,
                       net_amount=a_amt,  # type: ignore[arg-type]  # TaxTransaction.net_amount is float; a solver row's is Decimal (TJ-4113)
                       account=trg.account, id=a_id,
                       date_settle=a_settle)
    v.symbol = _sl_symbol_asof(pas._renames,
        _land.symbol,
        loss_key if _pre_loss else _ca_ev_key(run, v),
        _ca_ev_key(run, _land))
    # Applied right after its row in the main pass
    # (see _place_wash_adjusts): a pre-loss bump
    # after the loss sale's last fill; a post-loss
    # bump after the trigger purchase itself, so a
    # same-moment sale listed after that purchase
    # sees the bumped ACB (s.53(1)(f) — the bump is
    # part of the replacement's cost from its
    # acquisition; A2-0555). Sorted "cost adjustments
    # last" it landed after every trade at the
    # trigger's moment.
    _after, _landed = (_last_fill.id if _pre_loss else trg.id), False
    # No long position there at some point before day
    # 30: where the position held at day 30 begins
    # instead (the substituted property, s.54; TJ-2711).
    # A registered or affiliated trigger's row is scoped
    # out of the taxable pool (a denial for good): it
    # keeps the trigger's own date.
    _row = (None if (trg.id in run.sheltered_ids
                     or trg.id in run.affiliated_ids)
            else _sl_landing_row(pas=pas, rec=rec, run=run,
                                 start=_last_fill if _pre_loss else trg,
                                 symbol=v.symbol))
    if _row is not None:
        v.date, v.date_settle, v.time = _row.date, _row.date_settle, _row.time
        v.symbol = _sl_symbol_asof(pas._renames, _row.symbol,
                                   _ca_ev_key(run, v), _ca_ev_key(run, _row))
        _after, _landed = _row.id, True
    # (written whole: no mark of an earlier row of this id survives)
    run.wash_place[a_id] = WashPlace(_after, _landed)
    if trg.id in run.sheltered_ids:
        run.sheltered_ids.add(a_id)
    if trg.id in run.affiliated_ids:
        run.affiliated_ids.add(a_id)
    run.adjust_to_trigger[a_id] = trg.id
    # Its rule: an affiliated person's or a registered account's addition is
    # the acquirer's (s.53(1)(f)): CA-SL-09 (the affiliated person's trades
    # count only when given, CA-SL-04: a scope, no record of its own); an
    # addition that waits for the position held at day 30 (CA-SL-19: no
    # purchase opened it, so the trigger's position was sold out,
    # `_sl_sold_out`; moved to where that position begins, `_row`, or flat
    # at the placement already); else the addition to its ACB.
    run.rules.setdefault(rec.tx.id, {})[a_id] = (
        R.CA_SL_09 if (trg.id in run.affiliated_ids
                       or trg.id in run.sheltered_ids)
        else R.CA_SL_19 if (_row is not None
                            or _sl_sold_out(trg, pas=pas, rec=rec, run=run))
        else R.CA_SL_09)
    return v


def _sl_allocate(*, held: _SLHeld, pas: _SLPass, rec: _SLLoss, run: _CanadaRun) -> bool:
    """One loss of the denial pass, third part: the sale's min(S, P, B) / S formula
    shared pro rata by its fills, its denied units allocated over the triggers
    in order once per sale (`_sl_sale_alloc`), each fill taking its units' share
    of every trigger's take, the DISALLOW / ADJUST rows made (`_sl_mk_adjust`)
    and merged with an existing DISALLOW, the window traced for the report. Moved
    out of the loss loop as it was (backlog TJ-1839); keyword-only: what the loop
    hands it; it hands back whether a new denial was found (the override tally
    and the row lists it fills in place)."""
    found_new_wash_sale = False       # the result: a denial found for this loss
    # The sale's formula, min(S, P, B) / S, shared pro rata
    # by its fills: each fill's denied units are its units x
    # that fraction, so the result never depends on the
    # fills' order or prices (the first fill sees the whole
    # backing; later fills draw what is left of it).
    if 'frac' not in rec._grp_state:
        _S = pas._grp_units.get(rec._gid, rec.loss['qty'])
        rec._grp_state['frac'] = (min(1.0, held.held_substituted / _S)
                              if _S > 1e-12 else 1.0)
    _want = min(rec.loss['qty'] * rec._grp_state['frac'],
                held.held_substituted)
    # Zero is the pool's own tolerance: 1e-6 for shares, float
    # noise for a coin — a 0.0000009 BTC rebuy the pool keeps
    # as a holding backs a denial too (CA-CRYPTO-09/CA-SL-13;
    # A2-0552).
    if _want > pool_qty_eps(rec.tx.symbol, rec.loss['qty']):
        disallowed_qty = _want
        disallowed_amt = M.of(disallowed_qty) * (rec.loss['loss_amount'] / M.of(rec.loss['qty']))

        # --- Allocation across ALL in-window triggers ---
        # FUZZ #D: routing the ENTIRE disallowance to one
        # trigger made the answer depend on rebuy ORDER: a
        # taxable+sheltered window either recovered the
        # sheltered-attributed portion (tax understated) or
        # permanently lost the taxable-attributed portion
        # (overstated). Allocate pro-rata in acquisition
        # order — post-loss buys first (primary
        # replacements), then pre-loss buys latest-first —
        # each capped at its OPENING quantity in loss-date
        # units AND at its holder's still-held substituted
        # units. Sheltered/affiliated portions are
        # PERMANENT (their ADJUST is scoped out of the
        # taxable pool); taxable portions defer as ACB
        # (s.53(1)(f): the taxable holder's cap is its
        # still-held balance, so every deferral is backed).
        # Pre/post-loss is decided by the SAME key the
        # pool replays with (ca_main phase ladder), not by
        # (settle date, clock time): a trigger traded the
        # day before at a later clock time that settles on
        # the loss's settle date is PRE-EXISTING there and
        # sits in the pool the loss draws from. Classified
        # post-loss, its ADJUST landed before the loss sale
        # and inflated the very loss it deferred — a
        # feedback the solver amplified or never converged
        # on (2026-09 engine audit, r08 / r08b).
        # The sale's own moment is its last (losing) fill, where
        # CA-SL-08 places a pre-loss addition (`_disp_last`): the
        # triggers are classed before or after it for the sale as a
        # whole (TJ-2756: a purchase between two fills was after the
        # sale for the first and before it for the second).
        _last_fill = pas._disp_last.get(pas._disp_of.get(id(rec.tx), id(rec.tx)), rec.tx)
        _loss_key = _ca_ev_key(run, _last_fill)
        _loss_pkey = _sl_pkey(run, pas._pos, _last_fill)
        # One allocation per sale, on its first fill; each fill takes
        # its units' share of every trigger's take (TJ-2720, TJ-2758).
        if 'alloc' not in rec._grp_state:
            rec._grp_state['alloc'] = _sl_sale_alloc(
                held=held, loss_pkey=_loss_pkey, pas=pas, rec=rec, run=run)
            rec._grp_state['units'] = pas._grp_units.get(rec._gid,
                                                         rec.loss['qty'])
        per_share_loss = rec.loss['loss_amount'] / M.of(rec.loss['qty'])
        _share = rec.loss['qty'] / rec._grp_state['units']
        allocations = []      # (trigger, qty, amount)
        perm_amt = _ZERO
        for trg, _units in rec._grp_state['alloc']:
            _h, _hh, _per = _sl_trigger_holder(trg, pas=pas, rec=rec,
                                               run=run)
            take = _units * _share
            amt = M.of(take) * per_share_loss
            allocations.append((trg, take, amt))
            rec._trg_used[trg.id] = (rec._trg_used.get(trg.id, 0.0)
                                 + (take / _per if _per else 0.0))
            rec._grp_claim[_h] = rec._grp_claim.get(_h, 0.0) + take
            if _hh != ('taxable',):
                perm_amt += amt

        # Route each kept (deferred) bump onto a pool that
        # actually HOLDS substituted property at +30. The
        # class-level held_substituted test above can be satisfied
        # entirely by shares in a SIBLING pool of the alias
        # class (loss realized on S0 while the still-held
        # backing sits in S2, pre-merge): landing the
        # ADJUST on the trigger's own — possibly empty —
        # pool parked the deferral where nothing would ever
        # consume it, and the denied loss silently left
        # conservation (fuzz seed 2183, 2026-09 round-four
        # audit). Greedy: a trigger whose own pool holds
        # the deferred quantity keeps its pool; otherwise
        # the bump lands on the class pool with the most
        # remaining still-held backing.
        _pool_back: Dict[str, float] = {}
        _pool_repr: Dict[str, TaxTransaction] = {}
        # A key after every row of the window's last day.
        _eow_key = (rec.end_window_date, 99, '99:99:99', 99)
        if held._unit:
            # One pool (no rename acts on the symbol): the
            # taxable rows' running sum.
            assert held._k_end is not None, "`_unit` holds only with `_k_end` set (_sl_window_walk)"
            _n_tax, _s_tax = held._cb.taxable_sum(held._k_end)
            if _n_tax:
                _pool_back[rec.tx.symbol] = _s_tax
                _pool_repr[rec.tx.symbol] = held._cb.first_taxable()
        for t, q in (() if held._unit else held.rows):
            if (run.get_sort_date(t) <= rec.end_window_date
                    and t.id not in run.sheltered_ids
                    and t.id not in run.affiliated_ids):
                _k = _sl_symbol_asof(pas._renames, t.symbol, _eow_key,
                                  _ca_ev_key(run, t))
                _pool_back[_k] = _pool_back.get(_k, 0.0) + q
                _pool_repr.setdefault(_k, t)
        _lsign = 1.0                     # replacement is always a LONG holding (s.54)
        _back_left = {k: max(0.0, _lsign * v)
                      for k, v in _pool_back.items()}
        _adjust_land: Dict[RowId, TaxTransaction] = {}
        for trg, take, _amt in allocations:
            if (trg.id in run.sheltered_ids
                    or trg.id in run.affiliated_ids):
                continue      # moot pools; leave in place
            if trg.id in rec._call_ids:
                continue      # the bump lands on the call itself
            _own = _sl_symbol_asof(pas._renames, trg.symbol, _eow_key,
                                _ca_ev_key(run, trg))
            if _back_left.get(_own, 0.0) >= take - 1e-9:
                _back_left[_own] -= take
                continue      # own pool holds the backing
            _best = max(_back_left,
                        key=lambda k: _back_left[k],
                        default=None)
            if (_best is not None
                    and _back_left.get(_best, 0.0) > 1e-9):
                _back_left[_best] -= take
                _adjust_land[trg.id] = _pool_repr[_best]  # cov: a2-1596-wash-cross-pool
            # No positive pool anywhere: keep the default
            # landing — _defer_room already converted the
            # unbacked portion to a permanent denial.

        _mk_adjust = functools.partial(
            _sl_mk_adjust, land=_adjust_land, loss_key=_loss_key,
            loss_pkey=_loss_pkey, pas=pas, rec=rec, run=run)

        if rec.tx.id in run._ov_plan.row_of:
            # A filing position against this denial (CA-SL-18):
            # the units it would use are used (every other
            # sale's formula is unchanged), but no DISALLOW
            # and no ACB addition are booked — what it would
            # have been is recorded for the reports.
            _rec = pas._ov_would.setdefault(rec.tx.id,
                                        _LO.new_record())
            run.rules.setdefault(rec.tx.id, {})[override_record(rec.tx.id)] = R.CA_SL_18
            _rec['qty'] += rec.loss['qty']
            _rec['loss'] += rec.loss['loss_amount']
            _rec['disallowed'] += disallowed_amt
            _rec['permanent'] += perm_amt
            for trg, _q, _amt in allocations:
                _LO.add_replacement(
                    _rec, trg, _q, _amt,
                    'sheltered' if trg.id in run.sheltered_ids
                    else 'affiliated' if trg.id in run.affiliated_ids
                    else 'taxable')
            return found_new_wash_sale

        if allocations:
            run.loss_to_trigger[rec.tx.id] = allocations[0][0].id
            run.loss_to_triggers_multi[rec.tx.id] = [
                trg.id for trg, _q, _a in allocations]

        # Check if we already have a disallowance for this loss. If
        # so, update it if the amount has changed (due to basis
        # adjustments from a previous iteration). This fixes the
        # "solver stale-mate" where DISALLOW amounts would stay
        # stuck at their first-iteration values even as the
        # underlying realized loss drifted.
        existing_disallow = next(iter(pas._dis_by_id.get(rec.tx.id, ())),
                                 None)
        if existing_disallow:
            # A pre-loss bump follows its sale's last losing
            # fill as it moves (see _disp_last; A2-1596).
            _moved = False
            _wa = pas._disp_last.get(pas._disp_of.get(id(rec.tx), id(rec.tx)), rec.tx).id
            for trg, _q, _amt in allocations:
                if _sl_pkey(run, pas._pos, trg) >= _loss_pkey:
                    continue
                _v = next((v for v in pas._adj_idx.of(rec.tx.id)
                           if v.id == f"WASH_{rec.tx.id}__{trg.id}"),
                          None)
                _wp = run.wash_place.get(_v.id) if _v is not None else None
                if (_v is not None and _wp is not None
                        and not _wp.landed and _wp.after != _wa):
                    run.wash_place[_v.id] = _wp._replace(after=_wa)
                    _moved = True
            if _moved:
                found_new_wash_sale = True
            if abs(M.of(existing_disallow.net_amount) - M.of(disallowed_amt)) < _MILLI:
                return found_new_wash_sale
            existing_disallow.net_amount = disallowed_amt
            existing_disallow.quantity = disallowed_qty
            # Update the corresponding ADJUST vtx as well.
            # FULL loss-tx id: 8-hex prefixes collided across losses, so a
            # solver re-iteration could update the WRONG ADJUST and a
            # report row could attach another loss's adjustment.
            # (every ADJUST whose id starts WASH_<tx.id>__)
            _new_amts: Dict[RowId, Decimal] = {f"WASH_{rec.tx.id}__{trg.id}": amt
                         for trg, _q, amt in allocations}
            _seen_adj = set()
            for v in pas._adj_idx.of(rec.tx.id):
                v.net_amount = _new_amts.get(v.id, _ZERO)  # type: ignore[assignment]  # TaxTransaction.net_amount is float; a solver row's is Decimal (TJ-4113)
                _seen_adj.add(v.id)
            for trg, _q, amt in allocations:
                a_id = f"WASH_{rec.tx.id}__{trg.id}"
                if a_id not in _seen_adj:
                    run.final_virtual_txs.append(_mk_adjust(trg, amt))
                    pas._adj_idx.add(run.final_virtual_txs[-1])
            run.permanent_by_loss[rec.tx.id] = perm_amt
            found_new_wash_sale = True
            return found_new_wash_sale

        disallow_vtx = TaxTransaction(action='DISALLOW', date=rec.tx.date, time=rec.tx.time, symbol=rec.tx.symbol, currency=rec.tx.currency, net_amount=disallowed_amt, quantity=disallowed_qty, id=rec.tx.id, date_settle=rec.tx.date_settle)

        adjust_vtxs = [_mk_adjust(trg, amt)
                       for trg, _q, amt in allocations]
        run.permanent_by_loss[rec.tx.id] = perm_amt

        # Capture the full ±30 day window listing for the trace.
        # Includes every same-symbol transaction across all
        # accounts (taxable + sheltered) so the reader can see
        # which buys were eligible candidates and which were
        # actually selected as the ACB-bump anchor.
        _sl_window_trace(rec=rec, held=held, pas=pas, run=run, disallowed_qty=disallowed_qty)
        run.final_virtual_txs.extend([disallow_vtx] + adjust_vtxs)
        run.rules.setdefault(rec.tx.id, {})[rec.tx.id] = rec.loss['rule']
        pas._dis_by_id.setdefault(disallow_vtx.id,
                              []).append(disallow_vtx)
        for _a in adjust_vtxs:
            pas._adj_idx.add(_a)
        found_new_wash_sale = True
    else:
        # Nothing left to back this loss (an earlier fill of
        # the same sale took the replacement, or a later pass
        # moved the balance): withdraw a denial an earlier
        # pass attached.
        _stale = next((v for v in pas._dis_by_id.get(rec.tx.id, ())
                       if abs(M.of(v.net_amount)) > _MILLI),
                      None)
        if _stale is not None:
            _stale.net_amount = _ZERO  # type: ignore[assignment]  # TaxTransaction.net_amount is float; a solver row's is Decimal (TJ-4113)
            _stale.quantity = 0.0
            for v in pas._adj_idx.of(rec.tx.id):
                v.net_amount = _ZERO  # type: ignore[assignment]  # TaxTransaction.net_amount is float; a solver row's is Decimal (TJ-4113)
            run.permanent_by_loss[rec.tx.id] = _ZERO
            found_new_wash_sale = True
    return found_new_wash_sale


def _sl_class_balances(pas: _SLPass, run: _CanadaRun, c: str) -> _ClassBalances:
    """The class's balance view, built once per pass from the iteration's owned
    rows (`pas._sl_bal_cell` holds the rows by class once built)."""
    if not pas._sl_bal_cell:
        bal: Dict[str, List[TaxTransaction]] = {}
        for t in pas.current_tx_list:
            # Owned property only: a short futures leg (or
            # a broker-marked share short, TJ-0721) is an
            # obligation, never a holding, so it does not
            # net the long units held at day 30 down
            # (TJ-0651; any other short stays signed).
            if (t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                             'OPENING_BALANCE')
                    and _ca_row_pool_key(run, t) == t.symbol):
                bal.setdefault(run.row_cls(t), []).append(t)
                pas._sl_bal_sym.setdefault(t.symbol, []).append(t)
        pas._sl_bal_cell.append(bal)
    if c not in pas._sl_views:
        pas._sl_views[c] = _ClassBalances(pas._sl_bal_cell[0].get(c, []), run)
    return pas._sl_views[c]


def _ca_live_rules(rules: RuleTable, vtxs: Iterable[TaxTransaction],
                   would: Mapping[RowId, Any], gains: Iterable[Dict[str, Any]]) -> RuleTable:
    """The rules the denial passes noted (`run.rules`) for the records the
    converged books hold: a loss's DISALLOW while it denies (one withdrawn,
    zeroed or retracted, names nothing), each ADJUST while it adds, and an
    overridden loss's would-be denial (`would`: `_ov_would`, CA-SL-18,
    `override_record`); then each gain record that names its own (`gains`'
    `rule`: a deemed gain, keyed by its row, and an in-kind contribution's
    sale, by `in_kind_record`: the row's superficial loss keeps its own)."""
    live = {v.id for v in vtxs
            if abs(M.of(v.net_amount)) > (_MILLI if v.action == 'DISALLOW' else _NANO)
            and v.action in ('DISALLOW', 'ADJUST')}
    out = {loss: {k: r for k, r in recs.items()
                  if k in live or (loss in would and k == override_record(loss))}
           for loss, recs in rules.items() if loss in live or loss in would}
    for g in gains:
        if g.get('rule'):
            out.setdefault(g['tx_id'], {})[
                in_kind_record(g['tx_id']) if g.get('in_kind') else g['tx_id']] = g['rule']
    return out


def _superficial_loss_pass(*, it: _CanadaIteration, run: _CanadaRun) -> Tuple[bool, Dict[RowId, Dict[str, Any]]]:
    """The superficial-loss denial pass of one iteration of the Canadian
    engine's fixed-point loop (CA-SL-*): the replacement-property triggers
    of this iteration's losses, the DISALLOW/ADJUST rows they add to
    `final_virtual_txs`, and whether a new wash was found. Moved out of the
    loop by step 4a (backlog TJ-1832 to TJ-1837) and split at its seams by
    4d (TJ-1839): this body is the setup (the running balances, the book
    order, the loss indexes, the helpers) and the loop over the losses,
    each loss in four parts: `_sl_trigger_scan` returns the loss's record
    (`_SLLoss`), `_sl_window_walk` its holdings (`_SLHeld`), `_sl_allocate`
    (with `_sl_window_trace` for the report) whether it found a denial, which
    the loop ORs into the pass's result;
    the parts read the pass's state as `pas` (`_SLPass`) and the run's as
    `run`. The loop hands the pass the run's invariant state as `run`
    (`_CanadaRun`: the books, the indexes, the result containers) and this
    iteration's as `it` (`_CanadaIteration`: its rows, losses, denials and
    positions), and reads back whether a new wash was found and the override tally; the containers
    it fills, it fills in place."""
    found_new_wash_sale = False
    _ov_would: Dict[RowId, Dict[str, Any]] = {}
    if not run.detect_wash_sales or not it.iteration_losses:
        return found_new_wash_sale, _ov_would    # nothing to check this pass
    # Running balance per (account, alias) BEFORE each tx —
    # used to split a buy into its COVERING portion (closes a
    # short: not replacement property) and its OPENING portion
    # (acquires/extends: the only §54/§1091-relevant part).
    # FUZZ #B: a short-covering buy was accepted as a LONG
    # loss's trigger, so the +ADJUST landed on a SHORT pool and
    # inflated its opening proceeds — a deferred LOSS became a
    # phantom GAIN with no warning.
    _bal_before: Dict[RowId, float] = {}
    _running: Dict[Tuple[str, str], float] = {}
    # A class-share option root names its class line (SAMPLD for
    # SAMPLD.B.TO — CA-SL-05; A2-0015/0016).
    _cls_root = class_root_aliases(t.symbol for t in run.all_txs)

    # Rename chain for ADJUST placement (FUZZ #F8): a wash
    # ADJUST keyed to the trigger's TRADE-TIME symbol landed on
    # a pool the rename had already deleted — the deferred loss
    # stranded on an invisible empty pool and a later sale of
    # the new symbol used the un-bumped basis.
    _renames: _Renames = {}
    for _t in it.current_tx_list:
        if _t.action == 'SPLIT':
            _new_sym = (_t.symbol_new or '').strip()
            if _new_sym and _new_sym != _t.symbol:
                _renames[_t.symbol] = (_ca_ev_key(run, _t), _new_sym)

    # Phase-aware ordering (round-six settle-straddle fuzzer): the naive (date, time,
    # id) sort placed a settle-lagged row AFTER a same-date SPLIT, so its pre-split
    # quantity was applied to an already-scaled balance (phantom shares in the running
    # position; the cover-vs-opening gate misfired both ways). Every other balance
    # walk orders through the phase ladder; this one must too. No content-hash rung
    # (audit S069-16): current_tx_list is already in main-pass order, so the stable
    # sort keeps the main pass's tie order (its fixed rungs, then the export's row
    # order, CA-DATE-14); a one-cent price change used to flip two same-moment rows
    # here, and with them a superficial-loss denial.
    for _t in sorted(it.current_tx_list,
                     key=lambda x: event_sort_key(
                         x, profile='ca_balance',
                         date_of=run.get_sort_date)):
        # Keyed by (holder, RAW symbol): a rename-split
        # scales/moves only the named symbol's shares (in
        # every account — the event is corporate-wide), never
        # target-symbol shares acquired pre-rename. The holder
        # of every TAXABLE row is the taxpayer's one s.47 pool
        # (all taxable accounts of a blended pass together — a
        # sale in account B draws on A's shares there, so B's
        # rebuy is an acquisition, not a cover: audit S069-15);
        # sheltered and affiliated rows keep their own account.
        # A short futures leg is its own position (lib/futures
        # engine_pool_key): a short opened in one account never
        # covers, nor is covered by, a long held in another
        # (TJ-0651). Every other row: its symbol.
        _k = ((_t.account
               if (_t.id in run.sheltered_ids
                   or _t.id in run.affiliated_ids)
               else '\x00taxable'), _ca_row_pool_key(run, _t))
        _bal_before[_t.id] = _running.get(_k, 0.0)
        if _t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                         'OPENING_BALANCE'):
            _running[_k] = _running.get(_k, 0.0) + _t.quantity
        elif _t.action == 'SPLIT':
            _dst_sym = (getattr(_t, 'symbol_new', '')
                        or '').strip() or _t.symbol
            for _kk in list(_running):
                if _kk[1] != _t.symbol:
                    continue
                _moved = _running.pop(_kk) * _t.quantity
                _dk = (_kk[0], _dst_sym)
                _running[_dk] = _running.get(_dk, 0.0) + _moved

    _open_memo: Dict[int, float] = {}
    # Skip wash-sale detection entirely when disabled. The main loop
    # has already produced realized gains assuming no disallowance,
    # so converging on iteration 1 with no virtual txs is correct.
    # CRA's formula is applied PER SALE (CA-SL-08, owner decision
    # on A2-0167): denied units = least of (units sold, units
    # acquired in the window, units held at day 30), each sale on
    # its own, so the same held unit may back the denials of two
    # sales. Only the fills of ONE sale (disposition_groups: one
    # account's same-day sell-down) share the units: the sale's
    # ledger holds what its earlier fills claimed per holder (in
    # loss-date units: one day, one symbol) and per trigger (in
    # the trigger's own units, so no row is allocated twice),
    # taken in processing order, so every pass agrees.
    _disp_of = disposition_groups(it.current_tx_list)
    # The last LOSING fill of each sale: a pre-loss bump lands
    # after it, so the sale's losing fills are costed alike and
    # the formula's denial is not carried out by one of them (a
    # later sale at a gain, even the same day, still sees it).
    # The placement only ever moves LATER across passes
    # (_disp_last_seen): a fill priced just above the ACB is a
    # loss when costed after the bump and a gain before it, and
    # moving the bump back and forth with it never converged
    # (A2-1596).
    _disp_last: Dict[int, TaxTransaction] = {}
    for _l in sorted(it.iteration_losses,
                     key=lambda l: _sl_pkey(run, it._pos, l['tx'])):
        _disp_last[_disp_of.get(id(_l['tx']), id(_l['tx']))] = _l['tx']
    for _g, _t in run._disp_last_seen.items():
        if _g in _disp_last and _sl_pkey(run, it._pos, _t) > _sl_pkey(run, it._pos, _disp_last[_g]):
            _disp_last[_g] = _t
    run._disp_last_seen.update(_disp_last)
    # A sale's loss units (S of the formula): its losing fills.
    _grp_units: Dict[int, float] = {}
    for _l in it.iteration_losses:
        _g = _disp_of.get(id(_l['tx']), id(_l['tx']))
        _grp_units[_g] = _grp_units.get(_g, 0.0) + _l['qty']
    _ledgers: Dict[int, Tuple[Dict[RowId, float], Dict[Any, float], Dict[str, Any]]] = {}
    if run._sl_idx.trg is None:
        run._sl_idx.build(all_txs=run.all_txs, rows_of_cls=run._rows_of_cls,
                      get_sort_date=run.get_sort_date, short_routed=run._short_routed)
    if run._sl_idx.call is None and any(
            _l.get('direction', 'LONG') == 'LONG'
            and not is_option_symbol(_l['tx'].symbol)
            for _l in it.iteration_losses):
        run._sl_idx.build_calls(all_txs=run.all_txs, get_sort_date=run.get_sort_date,
                            call_und=lambda sym: _sl_call_und(_cls_root, sym),
                            split_timeline=run.split_timeline)
    _sl_trg, _sl_call = run._sl_idx.trg, run._sl_idx.call
    _sl_sym, _sl_book_pos = run._sl_idx.sym, run._sl_idx.book_pos
    assert (_sl_trg is not None and _sl_sym is not None
            and _sl_book_pos is not None), "built by the first pass (_SLIndexes.build)"
    # This pass's balance rows per class and per symbol, in
    # processing order (the day-30 walks), built on first use.
    _sl_bal_cell: List[Dict[str, List[TaxTransaction]]] = []        # the rows by class, once `_sl_class_balances` built them
    _sl_bal_sym: Dict[str, List[TaxTransaction]] = {}
    _sl_views: Dict[str, _ClassBalances] = {}
    # Each row's processing position in this pass, for the window's order
    # (A2-0965): built by the first denied loss that lists its window.
    _wpos_cell: List[Dict[RowId, int]] = []

    _adj_idx = _WashAdjustIndex(run.final_virtual_txs)

    pas = _SLPass(
        _adj_idx=_adj_idx,
        _bal_before=_bal_before,
        _cls_root=_cls_root,
        _dis_by_id=it._dis_by_id,
        _disp_last=_disp_last,
        _disp_of=_disp_of,
        _grp_units=_grp_units,
        _ledgers=_ledgers,
        _open_memo=_open_memo,
        _ov_would=_ov_would,
        _pos=it._pos,
        _renames=_renames,
        _sl_bal_cell=_sl_bal_cell,
        _sl_bal_sym=_sl_bal_sym,
        _sl_book_pos=_sl_book_pos,
        _sl_call=_sl_call,
        _sl_sym=_sl_sym,
        _sl_trg=_sl_trg,
        _sl_views=_sl_views,
        _wpos_cell=_wpos_cell, current_tx_list=it.current_tx_list,
        pool_snapshots_this_iter=it.pool_snapshots_this_iter)
    for loss in sorted(it.iteration_losses,
                       key=lambda l: _sl_pkey(run, it._pos, l['tx'])):
        rec = _sl_trigger_scan(pas=pas, run=run, loss=loss)
        if not rec.potential_triggers: continue

        held = _sl_window_walk(pas=pas, run=run, rec=rec)
        found_new_wash_sale |= _sl_allocate(held=held, pas=pas, run=run, rec=rec)
    return found_new_wash_sale, _ov_would

"""The fixed-point loop: the denials placed among the rows, the walk and the
denial pass until nothing changes.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import re
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Set, Tuple, TypeAlias
from datetime import datetime, timedelta

from taxjson.lib.corporate_timeline import event_sort_key

from taxjson.model import money as M
from taxjson.lib.option_symbols import exercise_target, is_option_symbol
from taxjson.lib.row_types import is_opening_row
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.engine.assign import _pair_assign_legs, _place_assign_options, _assign_trade_rows
from taxjson.engine.consts import _ZERO, _NANO, _MINUS_MILLI
from taxjson.engine.journal import _journal_legs
from taxjson.engine.premium_ledger import _AssignPremiumLedger
from taxjson.engine.ca.fold import _WalkFold
from taxjson.engine.ca.pool import PoolBook
from taxjson.engine.ca.pools import _ca_pool_state, _ca_short_state
from taxjson.engine.ca.indexes import _SL_WASH_AFTER_ACTIONS, _WashAdjustIndex
from taxjson.engine.ca.router import _MarkedShortRouter, _router_move_key
from taxjson.engine.ca.holders import _CanadaIteration, _CanadaBooks

from taxjson.engine.ca.denial import _superficial_loss_pass
from taxjson.engine.ca.walk import _per_share, _has_trace, _walk_rows

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.ca.indexes import WashPlace
    from taxjson.engine.assign import _AssignPairs
    from taxjson.engine.ca.holders import _CanadaRun

# An s.86.1 reallocation's two rows' join: (account, corp_event_id).
_CevKey: TypeAlias = Tuple[str, str]


def _assign_moved_legs(stream: List[TaxTransaction], pairs: Optional[_AssignPairs]
                       ) -> Dict[RowId, TaxTransaction]:
    """{option id: its first stock leg} for each paired option ASSIGN
    that _place_assign_options moves in front of that earlier leg. The
    moved row stands for the leg's moment: a year-end snapshot or a
    dated cost timeline must not end the earlier day at it (a December
    delivery whose option-close posting is dated January 2, backlog
    TJ-0839)."""
    if not pairs:
        return {}
    trade = _assign_trade_rows(stream)
    out: Dict[RowId, TaxTransaction] = {}
    for oid, lids in pairs.items():
        first = min(lids, key=lambda lid: trade.get(lid, (0,))[0])
        if oid in trade and first in trade and trade[first][0] < trade[oid][0]:
            out[oid] = trade[first][1]
    return out


def _place_wash_adjusts(stream: List[TaxTransaction],
                        wash_place: Mapping[RowId, WashPlace]) -> List[TaxTransaction]:
    """Move each superficial-loss ADJUST with a placement (`wash_place`,
    by the ADJUST's id: `after` = the loss row's id for a pre-loss
    trigger) to immediately after the row it names. s.53(1)(f)
    adds the denied loss to the substituted property when the loss is
    realized, so every later row sees it — including another fill of the
    same order at the same second. Dated loss+1 s and sorted after SELLs,
    the bump used to reach a second fill 2 s later but not one 0-1 s
    later (audit R1-31). Rows keep their sorted order otherwise."""
    moved = [t for t in stream if t.action == 'ADJUST'
             and t.id in wash_place]
    if not moved:
        return stream
    # The rows a placement names: a loss sale's fill, a trigger purchase
    # or a landing row (`_sl_pool_rows`: a position change of the pool, an
    # opening balance included), every one placed after by identity, never
    # left to the ADJUST's own sort key (TJ-2766).
    losses = {t.id for t in stream
              if t.action in _SL_WASH_AFTER_ACTIONS}
    after: Dict[RowId, List[TaxTransaction]] = {}
    for t in moved:
        _w = wash_place[t.id].after
        if _w in losses:
            after.setdefault(_w, []).append(t)
    placed = {id(t) for lst in after.values() for t in lst}
    out = []
    for t in stream:
        if id(t) in placed:
            continue
        out.append(t)
        if t.action in _SL_WASH_AFTER_ACTIONS and t.id in after:
            out.extend(after.pop(t.id))
    return out


def _trace_dated(lines: List[str]) -> List[Tuple[str, str]]:
    """(date, line) of each trace line that carries a date, in the trace's
    order (example line: `# margin | NOTE | 2024-12-19 ...`): read once for
    all the denied losses' trace windows (backlog TJ-4982)."""
    out = []
    for line in lines:
        match = re.search(r'\|\s+(\d{4}-\d{2}-\d{2})', line)
        if match:
            out.append((match.group(1), line))
    return out


def _wash_after(wash_place: Mapping[RowId, WashPlace], t: TaxTransaction) -> Optional[RowId]:
    """The row an addition (a virtual ADJUST) is placed after; None: it has
    no placement (any other row)."""
    wp = wash_place.get(t.id)
    return wp.after if wp is not None else None


def _drop_placements(wash_place: Dict[RowId, WashPlace], rows: List[TaxTransaction],
                     prefix: str) -> None:
    """A withdrawn denial's additions (the ADJUSTs whose id starts
    `prefix`) lose their placements with their rows: a map outlives the
    row a mark died with (TJ-4113)."""
    for x in rows:
        if x.action == 'ADJUST' and (x.id or '').startswith(prefix):
            wash_place.pop(x.id, None)


def _ca_solve(*, run: _CanadaRun) -> _CanadaBooks:
    """The Canadian engine's fixed-point loop: each iteration places the denials found so
    far among the rows, walks them (`_walk_rows`) and runs the denial pass
    (`_superficial_loss_pass`) over the iteration's losses; the first iteration that finds
    no new denial is the converged pass, whose results fill the books; after 1,000 passes
    the last iteration's stand in with a warning. Since recommendation 10 the walk is a
    re-fold (`_WalkFold`): an iteration restores the walk's state checkpointed before the
    first row the last pass changed and walks on from there, the same state a whole-book
    walk ends in. Returns the books (`_CanadaBooks`), the last iteration's row list and
    assignment-premium ledger on them for the finish."""
    books = _CanadaBooks()
    fold = _WalkFold(run=run) if run.detect_wash_sales else None   # one pass: no checkpoints
    # The cap on passes: a pathological book (dense same-symbol activity, daisy-chained
    # losses across accounts) could exhaust it; non-convergence is detected and warned.
    for iteration in range(1000):
        books.solver_iterations_used = iteration + 1
        current_tx_list = run.all_txs + run.final_virtual_txs
        current_tx_list.sort(
            key=lambda x: event_sort_key(x, profile='ca_main',
                                         date_of=run.get_sort_date))
        current_tx_list = _place_wash_adjusts(current_tx_list, run.wash_place)
        # This pass's denials by loss id, in list order (a lookup
        # per sale walked every denial: TJ-0105).
        _dis_by_id: Dict[RowId, List[TaxTransaction]] = {}
        for _dv in run.final_virtual_txs:
            if _dv.action == 'DISALLOW':
                _dis_by_id.setdefault(_dv.id, []).append(_dv)

        # Each option ASSIGN paired with its own stock leg; a leg dated
        # before its option row gets the option moved in front of it (A2-
        # 0051/0195); equal strikes of one day share one pro rata (TJ-2750).
        def _taxable_scope(_t: TaxTransaction) -> bool:
            return (_t.id not in run.sheltered_ids
                    and _t.id not in run.affiliated_ids)
        _assign_pairs = _pair_assign_legs(current_tx_list, _taxable_scope,
                                          run._assign_underlying, tie_pro_rata=True)
        _moved_legs = _assign_moved_legs(current_tx_list,
                                         _assign_pairs)
        current_tx_list = _place_assign_options(current_tx_list,
                                                _assign_pairs)

        # Staged option-assignment premiums, paired with their own
        # stock legs (see _AssignPremiumLedger).
        pending_adjustments = _AssignPremiumLedger(
            current_tx_list,
            lambda _t: (not is_option_symbol(_t.symbol)
                        and not exercise_target(_t)
                        and _t.action in ('BUYSELL', 'ASSIGN')
                        and not is_opening_row(_t)
                        and _taxable_scope(_t)),
            _assign_pairs)
        iteration_realized_gains: List[Dict[str, Any]] = []
        # Grant-timed closes that leave no record (_suppress_record).
        iteration_grant_nil_closes: List[Dict[str, Any]] = []
        iteration_losses: List[Dict[str, Any]] = []
        iteration_trace: List[str] = []
        symbol_acb_traces: Dict[str, List[str]] = {}
        pool_snapshots_this_iter: Dict[RowId, Dict[str, Any]] = {}
        # An ACB reallocation (an s.86.1 spin-off): the parent's
        # negative ADJUST and the new shares' BUYSELL, joined by
        # (account, corp_event_id). The parent's deferred_wash
        # tally moves with the share of the ACB it gives up, so the
        # economic P/L views split the recovered loss as the ACB is
        # split (backlog TJ-0062; the filed figures never read it).
        _cev_adj_sym = {(t.account, t.corp_event_id): t.symbol
                        for t in current_tx_list
                        if t.action == 'ADJUST' and t.corp_event_id
                        and M.of(t.net_amount or 0.0) < 0}
        _cev_moves = {(t.account, t.corp_event_id)
                      for t in current_tx_list
                      if t.action == 'BUYSELL' and t.corp_event_id
                      and float(t.quantity or 0.0) > 0
                      and _cev_adj_sym.get((t.account, t.corp_event_id))
                      not in (None, t.symbol)}
        # Pools by symbol (engine/ca/pool), with what a spin-off's parent gives up.
        global_pools = PoolBook(_cev_moves)
        # A native book's journals between listings (one leg books
        # the pair: _journal_units; backlog TJ-0361).
        _jlegs = _journal_legs(current_tx_list)
        _jdone: Set[str] = set()

        if run.trace:
            iteration_trace.append("# " + "-" * 215)
            iteration_trace.append("# ACCOUNT                    | NOTE         | DATE                | SYMBOL                     | ACTION   | QTY        | PRICE      | FEE/SH     | PRICE_FEE  | VALUE_NET  | ADJUSTMENT | GLOBAL_BAL | POOL_ACB   | ACB/SHARE  | REALIZED   | DISALLOWED | TRIGGER_INFO")
            iteration_trace.append("# " + "-" * 215)

        # The cost timeline (lib/cost_timeline; backlog TJ-0417):
        # each row's pools are read after it (at the next row), and
        # every pool still held at the end of each day.
        _tl = None
        _tl_shorts = None       # read only with a timeline (TJ-1942)
        _tl_dates: List[Tuple[str, str]] = []
        if run.cost_timeline:
            from taxjson.lib import cost_timeline as _CT
            _tl = _CT.Recorder(_ca_pool_state(global_pools))
            _tl_shorts = _CT.Recorder(_ca_short_state(
                global_pools, lambda: _msr.short))

        # The marked shorts' routing (_short_routed): each
        # account's units short and long (_MarkedShortRouter).
        run._short_routed.clear()
        _msr = _MarkedShortRouter()
        # The legs a blended book holds no row of: they still move
        # what each account holds (backlog TJ-1180).
        _msr_legs = sorted(
            ((_router_move_key(_m, run.get_sort_date), _i, _m)
             for _i, _m in enumerate(run._move_legs)),
            key=lambda _e: _e[:2])

        it = _CanadaIteration(
            _dis_by_id=_dis_by_id,
            _jdone=_jdone,
            _jlegs=_jlegs,
            _moved_legs=_moved_legs,
            _msr=_msr,
            _msr_legs=_msr_legs,
            _pos={id(_t): _i for _i, _t in enumerate(current_tx_list)},
            _tl=_tl,
            _tl_dates=_tl_dates,
            _tl_shorts=_tl_shorts,
            current_tx_list=current_tx_list,
            global_pools=global_pools,
            iteration=iteration,
            iteration_grant_nil_closes=iteration_grant_nil_closes,
            iteration_losses=iteration_losses,
            iteration_realized_gains=iteration_realized_gains,
            iteration_trace=iteration_trace,
            pending_adjustments=pending_adjustments,
            pool_snapshots_this_iter=pool_snapshots_this_iter,
            symbol_acb_traces=symbol_acb_traces)
        if fold is not None:
            fold.restore(it=it, pairs=_assign_pairs, moved_legs=_moved_legs, jlegs=_jlegs,
                         cev_moves=_cev_moves)
        _ye_pools, _ye_shorts = _walk_rows(
            run=run,
            it=it, fold=fold)

        # --- Detection ---
        found_new_wash_sale, books._ov_would = _superficial_loss_pass(
            run=run,
            it=it)

        if run.detect_wash_sales:
            # Retract STALE disallowances: a later iteration's basis adjustments can turn
            # a first-iteration loss into a raw gain while its DISALLOW stayed attached by
            # id (taxable = raw gain + a phantom disallowance; wash_sales listed a denial on
            # a disposition that is not a loss). Remove it and its ADJUSTs and re-iterate; an
            # oscillation ends at the cap with the loud non-convergence warning.
            _gain_by_id: Dict[RowId, Decimal] = {}
            for _rg in iteration_realized_gains:
                _gain_by_id[_rg['tx_id']] = (
                    _gain_by_id.get(_rg['tx_id'], _ZERO) + _rg['gain'])
            for _v in [v for v in run.final_virtual_txs
                       if v.action == 'DISALLOW'
                       and _gain_by_id.get(v.id, _ZERO) >= _MINUS_MILLI]:
                _pref = f"WASH_{_v.id}__"  # cov: a2-1596-stale-disallow-retract
                _drop_placements(run.wash_place, run.final_virtual_txs, _pref)
                run.final_virtual_txs[:] = [
                    x for x in run.final_virtual_txs
                    if x is not _v
                    and not (x.action == 'ADJUST'
                             and (x.id or '').startswith(_pref))]
                run.permanent_by_loss.pop(_v.id, None)
                run.loss_to_trigger.pop(_v.id, None)
                run.loss_to_triggers_multi.pop(_v.id, None)
                run.wash_windows.pop(_v.id, None)
                found_new_wash_sale = True

        if not found_new_wash_sale:
            books.keep(it=it, msr=_msr, tl=_tl, tl_shorts=_tl_shorts, tl_dates=_tl_dates,
                       ye_pools=_ye_pools, ye_shorts=_ye_shorts)

            # Backfill wash-window rows with the final, converged pool
            # state. Earlier iterations may have written different pool
            # values (before later ADJUSTs landed), so this overwrite is
            # important for accuracy.
            for _wl_id, ww in run.wash_windows.items():
                # A row's state "after the row" includes this
                # denial's taxable ACB bump placed right after it
                # (`WashPlace.after`: the trigger purchase, or a pre-loss
                # trigger's loss sale) — the last one processed, as
                # `list` shows the pool (backlog TJ-0111).
                _bump_after: Dict[str, Any] = {}
                for _a in run.final_virtual_txs:
                    _w = _wash_after(run.wash_place, _a)
                    if (_w and _a.action == 'ADJUST'
                            and (_a.id or '').startswith(f"WASH_{_wl_id}__")
                            and _a.id not in run.sheltered_ids
                            and _a.id not in run.affiliated_ids
                            and _a.id in it.pool_snapshots_this_iter
                            and (_w not in _bump_after
                                 or it._pos.get(id(_a), -1)
                                 > it._pos.get(id(_bump_after[_w]), -1))):
                        _bump_after[_w] = _a
                for entry in ww.get('transactions', []):
                    _eid = entry.get('tx_id', '')
                    snap: Optional[Dict[str, Any]] = it.pool_snapshots_this_iter.get(
                        _bump_after[_eid].id if _eid in _bump_after
                        else _eid)
                    if snap:
                        entry['pool_qty_after'] = snap['pool_qty']
                        entry['pool_acb_after'] = snap['pool_acb']
                        # The pool's own symbol (a rename row's is its target's, TJ-2349).
                        _ps = snap.get('pool_symbol') or entry.get('symbol') or ''
                        if abs(snap['pool_qty']) > pool_qty_eps(_ps, snap['pool_qty']):
                            entry['acb_per_share_after'] = _per_share(
                                snap['pool_acb'], snap['pool_qty'], _ps)
                        else:
                            entry['acb_per_share_after'] = _ZERO
            books.final_wash_sales = []
            _adj_idx = _WashAdjustIndex(run.final_virtual_txs)
            _dated: Optional[List[Tuple[str, str]]] = None
            for v in run.final_virtual_txs:
                if v.action == 'DISALLOW':
                    # ALL of this loss's ADJUST vtxs (a multi-trigger
                    # allocation emits several; solver updates can
                    # leave zeroed stubs). `next()` took the first —
                    # possibly a zeroed one — so adjust_cmd disagreed
                    # with disallow_cmd and replaying the pair
                    # stranded the difference. Present the poolable
                    # (taxable-deferral) total; the permanent/
                    # sheltered share has no ADJUST by design.
                    _adjs = [a for a in _adj_idx.of(v.id)
                             if abs(a.net_amount) > _NANO
                             # A sheltered/affiliated allocation's ADJUST (its id
                             # joins sheltered_ids/affiliated_ids at creation) is a
                             # permanent denial: presented as poolable it would bump
                             # next year's taxable ACB by money that never defers.
                             and a.id not in run.sheltered_ids
                             and a.id not in run.affiliated_ids]
                    adj = _adjs[0] if _adjs else None
                    adj_total = sum(a.net_amount for a in _adjs)

                    # Gather the trace for this specific wash sale
                    wash_trace = []
                    if run.trace and _has_trace(symbol_acb_traces, v.symbol):
                        # Add a bit of context: +/- 30 days around the wash sale
                        try:
                            loss_dt = datetime.strptime(v.date, '%Y-%m-%d')
                            window_start = (loss_dt - timedelta(days=30)).strftime('%Y-%m-%d')
                            window_end = (loss_dt + timedelta(days=30)).strftime('%Y-%m-%d')

                            wash_trace.append(f"# --- (D-30 Window Start: {window_start}) ---")
                            # Since symbol_acb_traces[v.symbol] gets reset to just the last segment when pool reaches 0,
                            # we need to trace over iteration_trace to get the full chronological context.
                            if _dated is None:
                                # (once: no line is added to the trace in this loop)
                                _dated = _trace_dated(iteration_trace)
                            for line_date, line in _dated:
                                # Also ensure the symbol matches
                                if f"| {v.symbol}" in line:
                                    if window_start <= line_date <= window_end:
                                        wash_trace.append(line)
                            wash_trace.append(f"# --- (D+30 Window End: {window_end}) ---")
                            wash_trace.append("# ")
                        except (ValueError, TypeError) as e:
                            # Narrowed from a bare `except Exception:
                            # pass`. The trace context is best-effort
                            # — if a virtual ADJUST row has a
                            # malformed date, we still want the
                            # disallow/adjust output to print. Log
                            # so an unexpected error doesn't
                            # disappear: a missing trace block is
                            # otherwise hard to diagnose.
                            emit_line(f"warning: skipped wash-trace "
                                  f"context for {v.symbol}@{v.date} "
                                  f"({e})")

                    disallow_cmd = f"DISALLOW {v.date} {v.time} {v.symbol} {v.currency} {v.net_amount:.4f}"
                    adjust_cmd = f"ADJUST {adj.date} {adj.time} {adj.symbol} {adj.currency} {adj_total:.4f}" if adj else ""

                    if run.trace:
                        wash_trace.append(disallow_cmd)
                        if adjust_cmd: wash_trace.append(adjust_cmd)

                    books.final_wash_sales.append({
                        'loss_tx': v.to_dict(),
                        'loss_tx_id': v.id,
                        'trigger_lot_id': run.loss_to_trigger.get(v.id, ''),
                        'amount': v.net_amount,
                        'disallowed_amount': v.net_amount,
                        'disallowed_qty': v.quantity,
                        'disallow_cmd': disallow_cmd,
                        'adjust_cmd': adjust_cmd,
                        # Where each s.53(1)(f) addition lands (one
                        # per taxable replacement; a multi-trigger
                        # allocation can name several symbols): the
                        # pool symbol as of the stamp, and `after` =
                        # the loss row when the bump is applied
                        # right after it (_place_wash_adjusts). The
                        # T1135 cost walk replays these (S008-07).
                        'adjusts': [
                            {'id': a.id, 'symbol': a.symbol,
                             'date': a.date,
                             'date_settle': a.date_settle or a.date,
                             'time': a.time,
                             'amount': a.net_amount,
                             'account': a.account,
                             'after': _wash_after(run.wash_place, a)}
                            for a in _adjs],
                        'trace': wash_trace if run.trace else []
                    })
            books.solver_converged = True
            break
        # Otherwise (a wash was found or an existing one was updated this
        # iteration) loop again: DISALLOW/ADJUST vtxs were already added to
        # final_virtual_txs inline, so the next pass recomputes against
        # them until the amounts stop changing.

    # If the solver ran out without converging, the last iteration's
    # state never got promoted to the `final_*` vars. Use it as a
    # best-effort fallback and emit a stderr warning so the caller
    # knows the result may be inconsistent.
    if not books.solver_converged:
        emit_line(f"warning: the superficial-loss solver did not converge "
            f"within {books.solver_iterations_used} iterations — the "
            f"superficial-loss list "
            f"may be incomplete and ACB pools may be inconsistent. "
            f"Check for unusual same-symbol activity in your input.")
        # Fall back to the last iteration's state.
        books.keep(it=it, msr=_msr, tl=_tl, tl_shorts=_tl_shorts, tl_dates=_tl_dates,
                   ye_pools=_ye_pools, ye_shorts=_ye_shorts)
    books.rows = current_tx_list
    books.premium_ledger = pending_adjustments
    return books

"""The walk over one iteration's rows: pools, gains, losses, traces, the cost
timeline.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import YEAR_END_INEXACT, emit_line
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from datetime import datetime

from taxjson.lib.corporate_timeline import event_sort_key
from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
# A sale's trace shares its pool's running trace (audit 2026-10-25
# robustness#5: a copy per sale grew with the square of the rows).
from taxjson.lib.trace_format import SharedTrace
from taxjson.lib.option_symbols import exercise_target, is_option_symbol, parse_option_underlying
from taxjson.lib.contract_size import OPTION_CONTRACT_SHARES
from taxjson.lib.row_types import (
    IN_KIND_CONTRIBUTION_TYPE, IN_KIND_DISPOSITION_TYPE, is_opening_row,
    is_stock_dividend,
)
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.rules import ids as R
from taxjson.engine.consts import (
    _ZERO, _ONE, _HALF, _FIFTY, _CENT, _HALF_CENT, _MICRO, _NANO, _MINUS_HALF_CENT, _MINUS_MILLI,
    _shown_if_float,
)
from taxjson.engine.journal import _opening_source, _is_listing_journal
from taxjson.engine.rows import _currency_mismatch_advice
from taxjson.engine.ca.pools import _pools_copy, pool_is_flat
from taxjson.engine.ca.run_helpers import (
    _futures_names, _ca_short_lot_open, _ca_open_short_option, _ca_walk_date, _ca_stock_short_key,
    _ca_tl_after, _ca_trade_money,
)
from taxjson.engine.ca.own_moves import (
    _is_lot_move, _unknown_sources, _own_move_only,
    _own_move_written, _own_move_units, _journal_units,
)
from taxjson.engine.ca.router import _drain_move_legs

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.ca.fold import _WalkFold
    from taxjson.engine.ca.holders import _CanadaIteration, _CanadaRun
    from taxjson.engine.ca.pool import AcbPool, PoolBook
    from taxjson.engine.ca.pools import _Pools


def _effective_fee_for_trace(tx: TaxTransaction) -> Decimal:
    """Fee actually paid on this transaction, for display in trace lines.

    When the parser broke out commission/fee explicitly, return their sum.
    When both are zero — some brokers fold fees into net_amount only (RBC
    being the canonical case) — back-compute from |qty * price * multiplier|
    vs |net_amount|. OCC option symbols get the 100x contract multiplier so
    a cheap option sale shows the commission RBC charged instead of a
    misleading 0.

    The math the engine uses for cost basis / proceeds is unaffected — it
    already keys off net_amount, which includes the fee. This is purely a
    display-side enrichment.
    """
    explicit = M.of(tx.commission) + M.of(tx.fee)
    if abs(explicit) > _MICRO:
        return explicit
    q = float(tx.quantity)
    qty = abs(q)
    price = M.of(tx.price)
    net = M.of(tx.net_amount)
    if qty < 1e-9 or price < _NANO:
        return _ZERO
    # The contract size the parser declared (a futures option: CL 1000,
    # ES 50 — audit S026-22), else 100 per equity option, 1 per share.
    multiplier = float(getattr(tx, 'multiplier', 0.0) or 0.0)
    if multiplier <= 0:
        multiplier = 100 if is_option_symbol(tx.symbol or '') else 1
    theoretical = M.of(qty) * price * M.of(multiplier)
    # The SIGNED residual (audit S070-24 / S071-00): a buy pays gross +
    # fee, a sale receives gross - fee (a cheap option's fee can exceed
    # its proceeds, so the net is negative). abs() turned a sub-cent
    # rounding residual into a positive "fee" on a zero-commission
    # broker and mis-sized a negative-net sale's fee.
    derived = (net - theoretical) if q > 0 else (theoretical - net)
    if abs(derived) < _CENT:
        return _ZERO               # price-rounding noise, not a fee
    # Units-mismatch guard (a per-contract quote against a per-share
    # net): only a residual both large and most of the trade's value.
    # The old 25%-of-net test hid real commissions on cheap options
    # (11.70 on a 30.00 buy showed as 0).
    if abs(derived) > max(_FIFTY, _HALF * theoretical):
        return _ZERO
    return derived


def _per_share(amount: Decimal, qty: float, symbol: str) -> Decimal:
    """`amount` per SHARE for a trace column labelled per share: an
    option's quantity is in contracts of OPTION_CONTRACT_SHARES shares,
    so its per-contract figure sat next to a per-share price (audit
    S069-08: 'ACB/Sh: 500.65' beside '@ 5.00'). 0.0 for an empty
    position."""
    q = float(qty)
    if abs(q) <= pool_qty_eps(symbol, q):
        return _ZERO
    mult = OPTION_CONTRACT_SHARES if is_option_symbol(symbol or '') else 1.0
    return M.of(amount) / M.of(q * mult)


def is_assign_premium_leg(tx: TaxTransaction) -> bool:
    """An ASSIGN whose cost or premium rolls into a stock leg: an option
    ASSIGN, or a marked warrant/right exercise leg."""
    return (getattr(tx, 'action', '') == 'ASSIGN'
            and (is_option_symbol(getattr(tx, 'symbol', '') or '')
                 or bool(exercise_target(tx))))


def _s861_prior_sales(pool: AcbPool, symbol: str,
                      records: List[Dict[str, Any]],
                      *, parent_qty: float = 0.0, account: str = ''
                      ) -> Optional[List[Tuple[Dict[str, Any], float]]]:
    """The taxable sales of an s.86.1 spin-off's original shares, as
    (record, shares of it): the shares the distribution was made on,
    which it reduces the cost of even when sold before it (TJ-0842).
    They are the last sold of `pool`'s last long holding (its
    `closed_holding` dates): walked back from the closing sale up to the
    `parent_qty` the event was paid on, so a trim from before the record
    date keeps its gain (TJ-1026). With no such count, the sales on the
    last sale's date. The event's own account's sales when it has any
    (one event per account, on one Canadian pool).

    None when there is none, or when one of them cannot simply take a
    lower cost: a loss (the superficial-loss solver has already read
    it), a denied amount, a missing purchase, a deemed or in-kind
    record."""
    held = pool.closed_holding
    if not held:
        return None
    start, end = held[:2]
    recs = [r for r in records
            if r.get('symbol') == symbol and r.get('direction') == 'LONG'
            and start <= str(r.get('date') or '') <= end
            and not r.get('grant') and not r.get('_void')
            and float(r.get('qty') or 0) > 1e-12]
    recs = [r for r in recs if r.get('account') == account] or recs
    if not recs:
        return None
    if parent_qty > 0:
        out = []
        left = parent_qty
        for r in reversed(recs):
            if left <= 1e-9:
                break
            part = min(float(r['qty']), left)
            out.append((r, part))
            left -= part
    else:
        last = max(str(r.get('date') or '') for r in recs)
        out = [(r, float(r['qty'])) for r in recs
               if str(r.get('date') or '') == last]
    for r, _part in out:
        if (r.get('deemed') or r.get('tainted') or r.get('in_kind')
                or abs(M.of(r.get('disallowed') or 0)) > _HALF_CENT
                or M.of(r.get('gain') or 0) < _MINUS_MILLI):
            return None
    return out


def _s861_holding_moved(rows: Sequence[Any], held: Any, upto: Any,
                        symbol: str, other_scope_ids: Any) -> bool:
    """A position row of `symbol` in the taxable book after the sale
    that recorded `closed_holding`, up to `upto`: the pool has held
    shares since (drained another way: a cross to short and its cover,
    an own-account move out, a listing journal), so those dates name an
    older holding, not the shares the distribution was made on
    (TJ-1026)."""
    closing = held[2] if held and len(held) > 2 else None
    after = False
    for t in rows:
        if t is upto:
            return False
        if after and t.symbol == symbol and t.id not in other_scope_ids \
                and t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                 'OPENING_BALANCE') \
                and abs(float(t.quantity or 0.0)) > 1e-12:
            return True
        if id(t) == closing:
            after = True
    return False


def _ca_deemed_gain(tx: TaxTransaction, symbol: str, pool: AcbPool, excess: Decimal, *,
                    note: str) -> Dict[str, Any]:
    """The qty-0 record of a return of capital's deemed gain (ITA s.40(3)),
    dated the ADJUST: no actual sale, so T4037 says enter 0 on line 13199
    and the gain on 13200 (audit R1-43), proceeds 0 and the nil-reset shown
    as -excess."""
    return {
        'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
        'date_settle': tx.date_settle or tx.date,
        'gain': excess, 'qty': 0.0,
        'cost': -excess, 'proceeds': _ZERO,
        'disallowed': _ZERO, 'taxable_gain': excess,
        'days_held': 0, 'account': tx.account,
        'currency': tx.currency,
        'commission': _ZERO, 'fee': _ZERO,
        'direction': 'LONG',
        'tainted': pool.tainted,
        'deemed': True,
        'note': note,
        'trace': [],
        'rule': R.CA_ACB_07,        # its rule (the run's `rules`)
    }


def _ca_acb_trace(it: _CanadaIteration, symbol: str) -> List[str]:
    """The symbol's ACB trace lines, started with their header when the
    symbol has none yet (one spelling of the header, TJ-1998). A pool's
    trace is named by _trace_name: a short pool's is its own (TJ-3228)."""
    return it.symbol_acb_traces.setdefault(
        symbol, [f"# --- ACB CALCULATION TRACE: {symbol} ---"])


def _has_trace(traces: Dict[str, Any], symbol: str) -> bool:
    """Whether `symbol` has an ACB trace: its own, or a short pool's (_trace_name)."""
    return symbol in traces or any(k.startswith(symbol + ' (') for k in traces)


def _trace_name(pkey: str, symbol: str) -> str:
    """The ACB trace a pool's rows go to: the symbol's own, or its short
    pool's ("SYM (written)" for an option series' written contracts, "SYM
    (short)" for a marked share or futures short), so a series bought in one
    account and written in another shows two traces, not one netted
    (backlog TJ-3228)."""
    if pkey == symbol:
        return symbol
    return f"{symbol} ({'written' if is_option_symbol(symbol) else 'short'})"


def _warn_adjust_on_empty_pool(symbol: str, tx: TaxTransaction, empty_roc: bool,
                               kept: Optional[AcbPool] = None) -> None:
    """The warning of an ADJUST row that finds no holding (`_adjust_holding`): a return of
    capital booked as a gain (`empty_roc`), else an amount the next position would take;
    `kept`, the pool of a holding under the unit tolerance whose cost the row would take."""
    if kept is not None:
        cut, cost = -M.of(tx.net_amount), M.of(kept.total_cost)
        emit_line(f"warning: ATTENTION: {symbol}: the return of capital "
            f"of {cut:.2f} on {tx.date} would leave no cost on a "
            f"holding of {abs(kept.qty):g} unit(s), too few to tell "
            f"from no position once they carry none: booked as a "
            f"capital gain in that year, and the holding keeps its "
            f"cost of {cost:.2f} for its sale. The total is right; "
            f"{min(cut, cost):.2f} of that gain belongs to the "
            f"sale (the ACB is reduced first, ITA s.53(2)(h)): if "
            f"the sale falls in another year, move that amount "
            f"between the two years by hand.")
    elif empty_roc:
        emit_line(f"warning: {symbol} return of capital "
            f"of {-M.of(tx.net_amount):.2f} on "
            f"{tx.date} hits an EMPTY pool — the "
            f"position was fully sold before it "
            f"posted, so there is no ACB to "
            f"reduce: booked as a capital gain in "
            f"that year (ITA s.40(3), ACB nil). "
            f"If it belongs to the sold position, "
            f"re-date the ADJUST before the final "
            f"sale instead.")
    else:
        emit_line(f"warning: {symbol} ADJUST of "
            f"{M.of(tx.net_amount):.2f} on {tx.date} "
            f"hits an EMPTY pool — the position was "
            f"fully sold before this ADJUST posted, so "
            f"the amount would leak into the NEXT "
            f"position's ACB instead of the one that "
            f"earned it. Re-date the ADJUST before "
            f"the final sale (adjusting that "
            f"disposition's gain) or apply it "
            f"manually.")


# The deemed gain's note: of a return of capital that found no holding, and
# of one on a holding kept under the unit tolerance (`_adjust_holding`).
_EMPTY_ROC_NOTES = (
    'DEEMED GAIN — return of capital received with no shares held (ITA s.40(3), ACB nil)',
    'DEEMED GAIN — return of capital on a holding under the unit tolerance, its cost kept (ITA s.40(3))')


def _adjust_holding(pool: AcbPool, symbol: str, tx: TaxTransaction) -> Tuple[bool, bool]:
    """What an ADJUST row finds in the pool, read before it changes the cost: (no holding,
    a holding kept out of the row's reach).

    No holding is the pools' own test (`pool_is_flat`, the one `AcbPool.drain` applies):
    fewer units than the tolerance at a material cost are a position. Read by the count
    alone, a return of capital on one share consolidated to 0.0000005 was a gain on an
    empty pool, the cost kept for the later sale's loss (TJ-5001).

    Kept out of reach: a long holding of no more units than the tolerance whose cost the
    row's reduction would take under MATERIAL_COST. Without a cost those units are no
    position to the pool or to the sale that closes them (`_walk_trade`: it opens a short,
    the gain lost), so the row is booked as it is on no holding, the amount a gain in its
    year and the cost left for the sale: the right total, said (`_warn_adjust_on_empty_pool`).
    Until the pool itself knows such units are held (backlog TJ-5140)."""
    flat = pool_is_flat(symbol, pool.qty, pool.total_cost)
    kept = (not flat and pool.qty > 0
            and abs(pool.qty) <= pool_qty_eps(symbol, pool.qty)
            and not str(tx.id or '').startswith('WASH_')
            and M.of(tx.net_amount) < _MINUS_HALF_CENT
            and M.of(pool.total_cost) + M.of(tx.net_amount) < _HALF_CENT)
    return flat or kept, kept


def _walk_adjust(*, is_other_scope: bool, it: _CanadaIteration, pool: AcbPool, run: _CanadaRun, symbol: str, trace_sym: str, tx: TaxTransaction) -> Tuple[Union[Decimal, float], str]:
    """An ADJUST row of the walk against the symbol's pool: a return of capital or a user
    adjustment folded into the pool's cost (a drained pool's return of capital a gain
    instead, an s.86.1 spin-off's reduction carried to the sales before it), a wash
    adjustment against the pool's deferred loss (the cut when it exceeds the cost left),
    the trace line; returns the adjustment shown and the row's note."""
    _applied_adj = M.of(tx.net_amount)
    # Flat is the pool's own tolerance (pool_qty_eps):
    # the 1e-6 share epsilon read a sub-micro coin
    # position as empty, parked its superficial-loss
    # deferral in pending_wash, and the closing sale
    # never recovered it (2026-10 filing audit F2).
    _flat, _kept = _adjust_holding(pool, symbol, tx)    # (TJ-5001, TJ-5140)
    _long = pool.qty > 0 and not _flat
    _short = pool.qty < 0 and not _flat
    if not is_other_scope:
        # A user ROC ADJUST landing on a DRAINED pool
        # (position fully sold before the distribution
        # posted) has no shares to adjust: the residual
        # silently folds into the NEXT position's ACB,
        # misstating BOTH dispositions. Retroactive
        # reallocation is out of scope — warn loudly so
        # the user re-dates or hand-applies it. A WASH_*
        # ADJUST can reach a flat pool on purpose (TJ-2698):
        # held in pending_wash below (CA-SL-19), unwarned.
        # A return of capital (negative, non-wash
        # ADJUST) on a DRAINED pool has no ACB left to
        # reduce: it is a capital gain in the year it
        # is received — the s.40(3) outcome with an
        # ACB of nil — and must not touch total_cost
        # (it used to leak into the NEXT purchase's
        # ACB, understating that position's later
        # gain).
        _empty_roc = (_flat
                      and not str(tx.id or '').startswith('WASH_')
                      and M.of(tx.net_amount) < _MINUS_HALF_CENT)
        _gone = _empty_roc and not _kept
        # An s.86.1 spin-off distributed after the parent
        # was sold: s.86.1 reduces the cost of the
        # original shares even when they were disposed of
        # before the distribution, so the reduction goes
        # to the sales that closed the holding — not a
        # return-of-capital gain dated the distribution
        # (TJ-0842).
        _s861_recs = (
            _s861_prior_sales(
                pool, symbol, it.iteration_realized_gains,
                parent_qty=tx.corp_parent_qty,
                account=tx.account)
            if (_gone and tx.id in run.taxable_ids
                and tx.corp_election == 'rollover_s_86_1'
                and not _s861_holding_moved(
                    it.current_tx_list,
                    pool.closed_holding, tx, symbol,
                    run.sheltered_ids | run.affiliated_ids))
            else None)
        if _s861_recs:
            _cut = -M.of(tx.net_amount)
            _sq = sum(_w for _r, _w in _s861_recs)
            for _r, _w in _s861_recs:
                _part = _cut * M.of(_w) / M.of(_sq)
                _r['cost'] -= _part
                _r['gain'] += _part
                _r['taxable_gain'] += _part
            if it.iteration == 0:
                _sold = sorted({r['date']
                                for r, _w in _s861_recs})
                emit_line(
                    f"note: {symbol}: the s.86.1 spin-off "
                    f"on {tx.date} reduces the cost of "
                    f"the shares sold before it (sold "
                    f"{', '.join(_sold)}) by "
                    f"{_cut:.2f}: their gain rises by "
                    f"that amount in the year of the "
                    f"sale (s.86.1). If that year's "
                    f"return is filed, it needs an "
                    f"adjustment.")
            _empty_roc = False
        elif (_gone and it.iteration == 0
                and tx.corp_election == 'rollover_s_86_1'):
            emit_line(
                f"warning: {symbol}: the s.86.1 spin-off "
                f"on {tx.date} reduces the cost of shares "
                f"sold before it, and taxjson cannot move "
                f"the reduction to that sale (a loss, a "
                f"superficial loss, a missing purchase or "
                f"no taxable sale on these books): booked "
                f"below as a gain on {tx.date}. Re-date "
                f"the parent's cost reduction before the "
                f"sale (a .tt ADJUST) to book it there.")
        # (an s.86.1 reduction's own line is said above)
        _s861_said = bool(_s861_recs) or (
            _gone
            and tx.corp_election == 'rollover_s_86_1')
        if (not _s861_said
                and _flat
                and not str(tx.id or '').startswith('WASH_')
                and abs(M.of(tx.net_amount)) > _HALF_CENT
                and it.iteration == 0):
            # iteration gate: the solver replays the
            # whole book each pass, so an unconditional
            # warning printed once PER ITERATION — one
            # data problem masqueraded as several.
            _warn_adjust_on_empty_pool(symbol, tx, _empty_roc, pool if _kept else None)
        if _s861_recs:
            # Moved to the sales: nothing left for the
            # empty pool (it would reach the next
            # purchase's cost).
            _applied_adj = _ZERO
        if (_short
                and not str(tx.id or '').startswith('WASH_')):
            # A SHORT pool's total_cost holds the short
            # sale's proceeds: a return of capital while
            # short is a compensation payment BY the
            # short seller (brokers debit it; parsers
            # book it as a positive ADJUST), which adds
            # to the cost of covering — it must LOWER the
            # short's gain. Applied as-is it raised the
            # gain by the amount (2x off, audit R1-157).
            _applied_adj = -_applied_adj
            if it.iteration == 0:
                emit_line(f"note: {symbol}: ADJUST of "
                      f"{M.of(tx.net_amount):+.2f} on "
                      f"{tx.date} lands on a SHORT "
                      f"position — booked as the short "
                      f"seller's compensation payment "
                      f"(it changes the cover's gain by "
                      f"{-M.of(tx.net_amount):+.2f}).")
        if _empty_roc:
            _applied_adj = _ZERO
            _excess = -M.of(tx.net_amount)
            if tx.id in run.taxable_ids:
                it.iteration_realized_gains.append(_ca_deemed_gain(
                    tx, symbol, pool, _excess,
                    note=_EMPTY_ROC_NOTES[_kept]))
        if str(tx.id or '').startswith('WASH_'):
            # Superficial-loss deferral: the invariant is
            # "reduce future gains by the denied loss".
            # For a LONG pool that means RAISING ACB
            # (+amt); for a SHORT pool, LOWERING the
            # entry proceeds held in total_cost (-amt).
            # The creation-time sign keyed off the LOSS
            # side, but s.47 pools are symbol-GLOBAL: in
            # a blended multi-account book the pool at
            # the landing site can be long while the
            # loss (and its trigger) were short — the
            # unconditioned -amt then LOWERED long ACB,
            # inflating every later gain. Decide by the
            # pool's actual direction here; a flat pool
            # keeps the creation-sign proxy (the trigger
            # opens on the loss side).
            _mag = abs(_applied_adj)
            if _long:
                _applied_adj = _mag
            elif _short:
                _applied_adj = -_mag  # cov: a2-1596-wash-short-pool-sign
            else:
                # FLAT pool: the sign cannot be decided
                # yet — it belongs to whichever
                # direction the pool NEXT opens
                # (creation-sign fallback leaked a
                # short-signed deferral into a
                # subsequent LONG opening with
                # inverted effect — conservation
                # fuzzer, seed 65). Park the magnitude;
                # the next opening applies it.
                pool.park(_mag)
                _applied_adj = _ZERO
        _tc_before = M.of(pool.total_cost)
        pool.add_cost(_applied_adj)
        # Superficial-loss deferrals arrive as virtual
        # ADJUSTs (id WASH_<loss>__...). Tally the
        # dollars parked in this pool so inventory can
        # report how much of the basis is deferred
        # loss vs. real purchase cost.
        if str(tx.id or '').startswith('WASH_'):
            pool.tally_denied(abs(M.of(tx.net_amount)))
        _ck = (tx.account, tx.corp_event_id)
        if (it.global_pools.is_spinoff(_ck) and _applied_adj < 0
                and _tc_before > _HALF_CENT
                and pool.deferred_wash > _NANO):
            # The ACB given up carries its share of the
            # tally to the new shares (TJ-0062).
            # What a return of capital took of the tally
            # goes along in the same share (TJ-1274).
            it.global_pools.give_up_to_child(
                _ck, pool, min(_ONE, -_applied_adj / _tc_before))
        if (_applied_adj < 0 and _long
                and not str(tx.id or '').startswith('WASH_')
                and pool.deferred_wash > _NANO):
            # The s.53(1)(f) additions are part of the
            # ACB: what a return of capital leaves of it
            # (nil past it, below) is all of them the
            # pool still holds, so a later sale does not
            # count them again (CA-RPT-16; backlog
            # TJ-1017). What it took is kept apart: a
            # reversal brings it back, and the sale
            # reports it (`deferred_loss_cut`), the ACB
            # having been short by any premium it held
            # as well (TJ-1217, TJ-1219).
            pool.cut_for_roc()
        if (_applied_adj > 0 and _long
                and tx.type == 'roc'
                and pool.wash_cut > _NANO):
            # A return of capital reversed: the ACB it
            # gives back holds the additions it took
            # (TJ-1219). New cost (type=dist) does not.
            pool.restore_roc()
        # ITA s.40(3): return-of-capital that drives ACB
        # below zero is a deemed capital gain in that
        # year. Rare enough to warrant human eyes rather
        # than silent computation — flag, don't book.
        if (_long and pool.total_cost < D('-0.005')
                and not str(tx.id or '').startswith('WASH_')):
            # ITA s.40(3): a return of capital that drives
            # the ACB below zero is a capital gain in the
            # year of the distribution, and the ACB resets
            # to nil. Booked as a qty-0 record dated the
            # ADJUST (the sale year got the whole amount
            # before — right total, wrong year).
            _excess = pool.reset_negative()
            if tx.id in run.taxable_ids:
                it.iteration_realized_gains.append(_ca_deemed_gain(
                    tx, symbol, pool, _excess,
                    note='DEEMED GAIN — return of capital exceeded ACB (ITA s.40(3)); ACB reset to nil'))
            emit_line(f"NOTE: {symbol}: return of capital on "
                f"{tx.date} exceeded the ACB by "
                f"{_excess:.2f} — booked as a deemed "
                f"capital gain in that year (ITA s.40(3)); "
                f"ACB reset to nil.")
    _shown_adj = (_applied_adj if not is_other_scope
                  else tx.net_amount)
    adjustment_shown = _shown_adj
    note = "ACB_ADJUST"
    if run.trace:
        _ca_acb_trace(it, trace_sym)
        pool_cost_f = M.of(pool.total_cost)
        acb_sh = _per_share(pool_cost_f, pool.qty, symbol)
        it.symbol_acb_traces[trace_sym].append(f"# {tx.date} ADJUST   {_shown_if_float(_shown_adj, 4):10.4f} | Fee: 0.0000 | Cost_Added: {_shown_if_float(_shown_adj, 4):10.4f} | Pool_Qty: {pool.qty:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
    return (adjustment_shown, note)


def _walk_split(*, is_other_scope: bool, it: _CanadaIteration, pool: AcbPool, qty: float,
                run: _CanadaRun, symbol: str, tx: TaxTransaction) -> Tuple[AcbPool, str]:
    """A SPLIT row of the walk: the pool's units (and its short-option grants and a marked
    short's own pool) re-denominated by the ratio, and a rename's pool carried to the new
    ticker (merged when one exists there). Returns the pool the walk's tail reads and the
    symbol it is keyed under: after a taxable-book rename the target's (the pool `pool` was
    merged into, or `pool` itself under its new key), else `pool` under `symbol` (a sheltered
    or affiliated row moves nothing in the taxable book; TJ-1993)."""
    _SHORT_SFX, _ = _futures_names()
    _carried = False
    if not is_other_scope:
        pool.split(qty)
        # A marked short's own pool (TJ-0721) splits
        # and follows a rename with the shares.
        _tgt = (tx.symbol_new or '').strip() or symbol
        it._msr.split(symbol, _tgt, qty)
        _carried = _split_short_pool(it.global_pools, symbol + _SHORT_SFX,
                                     _tgt + _SHORT_SFX, qty, symbol, _tgt)
    # Rename the pool when symbol_new is set and differs
    # from the source symbol — that's how mergers and
    # corporate reorganizations move ACB onto a new
    # ticker (e.g. ABG.TO → ABH.US 1-for-16 via the
    # s. 85.1(5) rollover election). Before this, the
    # SPLIT silently dropped symbol_new and any future
    # trade of the new ticker started from a zero-cost
    # pool with no relationship to the source's basis.
    target_symbol = (tx.symbol_new or '').strip()
    renamed = bool(target_symbol and target_symbol != symbol and not is_other_scope)
    if target_symbol and target_symbol != symbol:
        if renamed:
            existing = it.global_pools.get(target_symbol)
            if existing is None:
                it.global_pools.rename(symbol, target_symbol, pool)
            else:
                # Pool already exists for the new ticker: merge
                # (AcbPool.merge; the short pool's rename
                # takes the same merge, TJ-3193).
                existing.merge(pool, symbol, target_symbol)
                it.global_pools.pop(symbol)
        if run.trace:       # each pool's own trace: the short pool's too (TJ-3248)
            for _tn in ([target_symbol] + ([_trace_name(target_symbol + _SHORT_SFX, target_symbol)]
                                           if _carried else [])):
                _ca_acb_trace(it, _tn).append(
                    f"# {tx.date} SPLIT-RENAME {symbol}→{target_symbol} "
                    f"ratio={qty:.6g} | pool moved to new ticker")
    return (it.global_pools[target_symbol], target_symbol) if renamed else (pool, symbol)


def _split_short_pool(pools: PoolBook, key: str, target: str, ratio: float,
                      symbol: str, target_symbol: str) -> bool:
    """A SPLIT row's part for the symbol's short pool (`key`; TJ-0721): its
    units, and the written contracts' lots (TJ-2735), re-denominated by
    `ratio`, and the pool carried to a rename's `target` key (merged into
    the short pool there when one exists, as the long pool's rename merges:
    AcbPool.merge, TJ-3193)."""
    ssp = pools.get(key)
    if ssp is None:
        return False
    ssp.split(ratio)
    sx = pools.get(target) if target != key else None
    if sx is None:
        pools.rename(key, target, ssp)
        return True
    pools.pop(key)
    sx.merge(ssp, symbol, target_symbol)
    return True


def _ca_assign_close_units(run: _CanadaRun, it: _CanadaIteration, tx: TaxTransaction,
                           qty: float) -> float:
    """The units an option row that closes the account's own contracts only
    books, signed: an assignment or exercise (an ASSIGN row) or an expiry (a
    no-money BUYSELL on or after the series' expiry by an account holding the
    contracts) less its contracts
    past the account's own written (or bought) ones, which close nothing and
    open nothing (CA-OPT-06: never one still open in another account;
    CA-OPT-12; backlog TJ-3273, TJ-3302, TJ-3303); 0 when an assignment's or
    exercise's account owns none. The router found the excess
    (_MarkedShortRouter.excess); the conservation replay reads the units
    booked (_conserved_rows); a Warning once per row names the counts (the
    files hold more assignments than writes: a write or purchase is missing,
    or the row is another account's). Any other row: its own units."""
    over = it._msr.excess.get(tx.id)
    if not over:
        return qty
    own = abs(qty) - over
    units = own if qty > 0 else -own
    run._ev_memo.setdefault('_move_effect', {})[tx.id] = units
    seen: Set[RowId] = run._ev_memo.setdefault('_assign_over', set())
    if tx.id not in seen:
        seen.add(tx.id)
        assign = tx.action == 'ASSIGN'
        did, what = (('assigned', 'assignment') if qty > 0 else ('exercised', 'exercise')) \
            if assign else ('expired', 'expiry')
        kind, missing = ('written', 'write') if qty > 0 else ('bought', 'purchase')
        emit_line(f"warning: ATTENTION: more assignments than writes: {tx.symbol} {tx.date} "
                  f"{tx.account}: {abs(qty):g} {did}, {own:g} {kind}\n"
                  f"    The {over:g} contract(s) past the account's {kind} ones close nothing"
                  f"{': their shares go at the strike with no premium' if assign else ''}. "
                  f"A {missing} is missing from the files (tjs find-missing-history), or the "
                  f"{what} is another account's.")
    return units


# The result key (and the first walk's `_ev_memo` key) of the stock dividends
# booked on a short (CA-STKDIV-02): their row ids.
STOCK_DIVIDENDS_OWED = 'stock_dividends_owed'
# The result's key of the stock dividend rows booked as no shares, each the
# obligation of a short covered before it (CA-STKDIV-02; the decision is
# lib/stock_dividend_cover's, from the rows alone): the gains files carry it.
STOCK_DIVIDENDS_AFTER_COVER = 'stock_dividends_after_cover'
# The passes' `_ev_memo` keys for those rows' covers (the last purchase that
# bought the short back): the cover's row id -> [(the stock dividend row's
# id, what delivering its shares cost)], and the stock dividend rows whose
# cover closed a short in these books (and so booked the cost).
STKDIV_DELIVERY = 'stkdiv_delivery'
STKDIV_DELIVERED = 'stkdiv_delivered'
# How a pooled pass's line for a row whose cover closes no short in it
# starts: the run shows it on the console (pipeline/stages).
ATTENTION_POOLED_COVER = "ATTENTION: pooled short: "


def _ca_delivered_cost(run: _CanadaRun, tx: TaxTransaction) -> Decimal:
    """What delivering a stock dividend's shares cost, where the purchase `tx` closes a
    short and is the cover a later stock dividend row names (the row is the covered
    short's obligation; CA-STKDIV-02): a cost of closing that short, in the gain or loss
    the superficial-loss rule reads, never in the pool's cost or units. Asked where the
    purchase meets a short pool only; a row with no `delivered_cost` adds 0 and is noted
    as one whose cover these books closed."""
    out = _ZERO
    for row_id, cost in run._ev_memo.get(STKDIV_DELIVERY, {}).get(tx.id, ()):
        run._ev_memo.setdefault(STKDIV_DELIVERED, set()).add(row_id)
        out += cost
    return out


def stock_dividend_after_cover_line(f: Dict[str, Any], closed: bool, pooled: bool,
                                    cover_year: str) -> str:
    """The line of a stock dividend booked as no shares (`f`: the facts
    lib/stock_dividend_cover names; `closed`: its cover closed a short in these books,
    which then took the row's `delivered_cost`; `pooled`: the books hold several
    accounts; `cover_year`: the cover's tax year):

    * the cost booked: a note; an ATTENTION where the cover's tax year is not the row's
      (a figure of an earlier year moves: both years and `tjs check-filed` named);
    * no `delivered_cost` on the row: an ATTENTION naming the one line that carries it,
      which the account's own books book;
    * a cover that closes no short in pooled books (another account holds the shares:
      the short sale sold pool shares): the cost is not, or would not be, booked in
      them, said under ATTENTION_POOLED_COVER (TJ-5308, on TJ-5285)."""
    cost = M.of(f['cost'])
    head = (f"{f['symbol']}: stock dividend of {f['units']:g} share(s) "
            f"on {f['date']}: booked as no shares. The account {f['account']} was short "
            f"{f['short']:g} share(s) on the row's entitlement date {f['entitlement']} and "
            f"covered them on {', '.join(f['covered'])}, before the row was posted: the "
            f"new shares are what that short owed the lender, which its cover did not "
            f"include, not shares acquired at $0 (CA-STKDIV-02, an assumption: check the "
            f"broker's statement). It needs no declared amount.")
    line = (f"`BUYSELL {f['date']} {f['time']} {f['symbol']} {f['units']:g} "
            f"{f['currency']} 0 0 type=stock_dividend {f['dated_by']}={f['entitlement']} "
            f"delivered_cost=COST`")
    if not closed and pooled:
        return (f"warning: {ATTENTION_POOLED_COVER}{head} "
                + (f"Its delivered_cost of {cost:,.2f} is not booked in the books "
                   f"pooled across your accounts, the figures you file"
                   if cost else
                   "What delivering them cost, once on the row as delivered_cost=, is "
                   "booked in the account's own books and not in the books pooled "
                   "across your accounts, the figures you file")
                + f": the purchase on {f['cover_date']} closes no short in them "
                f"(another account holds the shares, so the short sale sold pool "
                f"shares). Enter that cost in the pooled figures by hand.")
    if cost and closed and cover_year and cover_year != f['date'][:4]:
        return (f"warning: ATTENTION: {head} Its delivered_cost of {cost:,.2f} is "
                f"booked as a cost of the cover on {f['cover_date']}, in tax year "
                f"{cover_year}: the row is dated {f['date'][:4]}, and {cover_year}'s "
                f"gain is lower by it than without the row. If {cover_year} is filed, "
                f"`tjs check-filed` shows the difference.")
    if cost and closed:
        return (f"note: {head} Its delivered_cost of {cost:,.2f} is booked as a "
                f"cost of the cover on {f['cover_date']}.")
    if cost:
        return (f"warning: ATTENTION: {head} Its delivered_cost of {cost:,.2f} is "
                f"not booked: the purchase on {f['cover_date']} closed no short in "
                f"these books. Enter it by hand.")
    return (f"warning: ATTENTION: {head} What delivering them cost is in no row: put it "
            f"on the row itself, as `delivered_cost=COST` on its .tt line ({line}, in "
            f"place of the broker's row): the account's own books then book it as a "
            f"cost of the cover on {f['cover_date']}. If the row is shares you "
            f"received, check its ex_date / record_date.")


def stock_dividend_cost_unbooked_line(t: TaxTransaction) -> str:
    """The ATTENTION line of a stock dividend row whose `delivered_cost` no rule books:
    the row is not read as a covered short's obligation, or the amount is below 0 (a
    JSON book's: the .tt reader refuses it)."""
    if (t.delivered_cost or 0.0) < 0:
        return (f"warning: ATTENTION: {t.symbol}: stock dividend of {t.quantity:g} "
                f"share(s) on {t.date}: its delivered_cost of "
                f"{M.of(t.delivered_cost):,.2f} is below 0 and is not booked: it is "
                f"what delivering the shares cost, an amount above 0. Correct the row.")
    return (f"warning: ATTENTION: {t.symbol}: stock dividend of {t.quantity:g} share(s) "
            f"on {t.date}: its delivered_cost of {M.of(t.delivered_cost):,.2f} is not "
            f"booked. The row is not read as the obligation of a short covered before it "
            f"(CA-STKDIV-02): that takes an ex-dividend or record date on the row, and "
            f"the account short on that date and bought back since. Check the row's "
            f"ex_date, or remove the delivered_cost.")


def stock_dividend_near_cover_line(f: Dict[str, Any]) -> str:
    """The ATTENTION line of a stock dividend with no entitlement date, booked as shares
    received at $0, that a short covered in the days before may owe (`f`: the facts
    lib/stock_dividend_cover names). `beside` in them: a dated row of an account that
    held shares on the entitlement date and was short beside them (a sale its broker
    marked short, bought back since): booked as shares received on the shares held, the
    covered short's own part in no row."""
    if f.get('beside'):
        return (f"warning: ATTENTION: {f['symbol']}: stock dividend of {f['units']:g} "
                f"share(s) on {f['date']}: {f['account']} held {f['held']:g} share(s) "
                f"on its entitlement date {f['entitlement']} and was also short "
                f"{f['short']:g} of the security (a sale the broker marked short), "
                f"covered on {', '.join(f['covered'])}. The row is booked as shares "
                f"received on the shares held; the part that short owed the lender is "
                f"not in it: enter that by hand (.tt), CA-STKDIV-02.")
    return (f"warning: ATTENTION: {f['symbol']}: the stock dividend of {f['units']:g} "
            f"share(s) on {f['date']} carries no ex-dividend or record date, and "
            f"{f['account']} covered a short of the security on "
            f"{', '.join(f['covered'])}, within the 90 days before it: a "
            f"short open on the ex-dividend date owes the new shares, and the row is then "
            f"that short's obligation, not shares acquired at $0 (CA-STKDIV-02). It is "
            f"booked as shares received. Check the issuer's ex-dividend date and give the "
            f"row its ex_date (`ex=DATE` on a .tt type=stock_dividend line).")


def _ca_stock_dividend_owed(run: _CanadaRun, it: _CanadaIteration, pool: AcbPool, tx: TaxTransaction,
                            qty: float, symbol: str, trace_sym: str) -> bool:
    """A stock dividend received by an account that is short the security (CA-STKDIV-02):
    the short seller owes the lender the new shares, so the short grows by them and its
    proceeds stay: nothing is acquired, nothing covered, no gain until the cover. Read as
    the $0 purchase the row looks like, it covered that many shares for nothing (backlog
    TJ-5006).

    The receiving account's own position decides (`CanadaTaxRules._stock_dividends_owed`):
    each account's rows are walked alone first, where a row of shares that meets a short
    pool is named (`_ev_memo`: a set to fill), and the passes get it with its quantity
    negative, the short's own units, which every one of them reads alike. Here, in the
    passes' walk (`_ev_memo`: those rows' ids), such a row takes its units off the pool at
    no proceeds, never as a sale: a short (or flat) pool grows short; a pool other accounts
    leave long gives the units up at the same cost, no gain or loss booked (one holding no
    more than the row owes gives up all it holds, its cost kept, and is short by the rest).
    Said once, on every branch. False: any other row, shares
    received among them (booked as before; a marked short open beside them is said).

    A row posted with no short open, after the short that owed it was covered, is no
    shares at all (lib/stock_dividend_cover, from the rows alone; backlog TJ-5282): it is
    left out of the book before any walk, this one included."""
    if not is_stock_dividend(tx) or not qty:
        return False
    named = run._ev_memo.get(STOCK_DIVIDENDS_OWED)
    eps = pool_qty_eps(symbol, pool.qty, qty)
    units = abs(qty)
    if isinstance(named, set):                  # an account's own first walk
        if not (qty > 0 and pool.qty < -eps):
            return False
        named.add(tx.id)
        # (its conservation replay reads the row at its effect, as a
        # written move's leg: `_conserved_rows`)
        run._ev_memo.setdefault('_move_effect', {})[tx.id] = -units
    elif qty > 0 or tx.id not in (named or ()):
        _short = it._msr.short.get((symbol, tx.account), 0.0) if qty > 0 else 0.0
        if _short > eps and it.iteration == 0:
            emit_line(f"warning: ATTENTION: {symbol}: stock dividend of {units:g} "
                      f"share(s) on {tx.date}: {tx.account} holds the security "
                      f"and is also short {_short:g} of it (a sale the broker "
                      f"marked short). The row is booked as shares received on "
                      f"the shares held; the part the short owes the lender is "
                      f"not in it: enter that by hand (.tt), CA-STKDIV-02.")
        return False
    _rest = units
    if pool.qty > units + eps:
        pool.split((pool.qty - units) / pool.qty)   # the units off, the cost as it was
        _rest = 0.0
    elif pool.qty > eps:
        # The pool holds no more than the row owes: its units go, never as
        # a sale at $0 (that booked their cost as a loss nothing caused).
        # Left flat, the pool keeps the cost for its next purchase; left
        # short by the rest, the short carries it as a cost of its cover
        # (proceeds below zero), where it comes back.
        _rest, _cost = units - pool.qty, pool.total_cost
        pool.split(0.0)
        if _rest > eps:
            pool.add_cost(-2 * _cost)
    if _rest > eps:
        # (`AcbPool.open` with the short's sign and no proceeds: the units
        # add up exactly, where a ratio's product does not; a value the row
        # carries is not proceeds.)
        pool.open(-_rest, _ZERO, date=tx.date, settle=run.get_sort_date(tx))
    if it.iteration == 0 and not isinstance(named, set):
        emit_line(f"warning: ATTENTION: {symbol}: stock dividend of {units:g} "
                  f"share(s) on {tx.date} while {tx.account} is short the "
                  f"security: booked as the short's to make good. The short owes "
                  f"the lender the new shares, so it grows by them with its "
                  f"proceeds unchanged; nothing is acquired, nothing is covered "
                  f"and no gain is booked until the cover (CA-STKDIV-02, an assumption: "
                  f"check the broker's statement). It needs no declared amount. "
                  f"If the row is shares you bought, enter it as a purchase (a "
                  f".tt BUYSELL line without type=stock_dividend).")
    if run.trace:
        _ca_acb_trace(it, trace_sym).append(
            f"# {tx.date} STOCK DIVIDEND while short {units:10.4f} | the short "
            f"grows, proceeds unchanged | Pool_Qty: {pool.qty:10.4f} | "
            f"Pool_ACB: {M.of(pool.total_cost):10.4f}")
    return True


def _past_position(tx: TaxTransaction, qty: float, closing_qty: float, internal_adj: Decimal,
                   qeps: float) -> Tuple[float, bool]:
    """A closing trade of `qty` units of which `closing_qty` close the position: (the share
    of the trade's money this disposition takes, whether a part past the position at or
    under the tolerance `qeps` is booked as a position of its own). The part past the
    position is no position by its units when it is at or under the tolerance, but its
    share of the trade's money is real (backlog TJ-4990, TJ-1347). Under a half cent
    (MATERIAL_COST: what `AcbPool.drain` and the dust close call no cost) it is rounding,
    and the whole trade's money goes to this disposition. Material, it is booked as any
    trade past zero is, a position the other way at its share of the money (a sub-tolerance
    position of material cost stands, as `AcbPool.drain` leaves one). No money of a trade
    is dropped either way."""
    leftover = abs(qty) - closing_qty
    if leftover > qeps:
        return closing_qty / abs(qty), False
    material = (leftover > 0 and (abs(_ca_trade_money(tx)) + abs(internal_adj))
                * M.of(leftover / abs(qty)) >= _HALF_CENT)
    return (closing_qty / abs(qty) if material else 1.0), material


def _walk_trade(*, account: str, internal_adj: Decimal, internal_dw: Decimal, internal_tainted: bool, is_option_assign: bool,
                   is_other_scope: bool, it: _CanadaIteration, pool: AcbPool, qty: float, run: _CanadaRun, symbol: str, trace_sym: str, tx: TaxTransaction, internal_sources: Sequence[Dict[str, Any]] = (), internal_outlay: Decimal = _ZERO) -> Tuple[Decimal, Optional[Decimal]]:
    """A trade row of the walk (a buy, a sale, a short's open or cover, an option leg's
    shares): a sheltered or affiliated account's row on its own pool, an opening on the
    taxable pool at its cost, else the closing's proceeds, cost and gain against the pool's
    average cost (the dust close, the grant mode, the deferred and tainted amounts, the
    timeline and the trace); returns the disallowed amount and the realized P&L."""
    disallowed_amt = _ZERO
    realized_pl = None
    qty = _ca_assign_close_units(run, it, tx, qty)      # a close row: the account's own only
    if not qty or (not is_other_scope and _ca_stock_dividend_owed(run, it, pool, tx, qty, symbol, trace_sym)):
        return (disallowed_amt, realized_pl)
    # A coin pool's residue is real property (S069-13):
    # per-asset tolerance (pool_qty_eps).
    _qeps = pool_qty_eps(symbol, pool.qty, qty)
    # A position under the tolerance that still carries
    # real cost, reversed by this trade, is a holding,
    # not rounding noise: closed, not read as flat —
    # the sale opened a short and stranded the cost
    # (2026-10 audit #101). A true zero still opens.
    _dust_close = (abs(pool.qty) <= _qeps
                   and pool.qty != 0
                   and abs(M.of(pool.total_cost))
                   >= _HALF_CENT
                   and (pool.qty > 0) == (qty < 0))
    is_opening = (pool.qty > _qeps and qty > 0) or \
                 (pool.qty < -_qeps and qty < 0) or \
                 (abs(pool.qty) <= _qeps
                  and not _dust_close)

    if is_other_scope:
        # Sheltered (RRSP/TFSA/LIRA/RESP) and affiliated
        # (spouse / controlled-corp / affiliated trust) shares
        # do NOT enter the taxable ACB pool — registered
        # accounts are separate property per CRA, and
        # affiliated-party trades belong on the other
        # person's return. The wash-sale walk still sees
        # them via current_tx_list, so the 30-day "still
        # held" balance and ITA 54 substitution-property
        # rule fire correctly.
        return (disallowed_amt, realized_pl)
    if is_opening:
        # BUY (or Short opening)
        effective_cost = _ca_trade_money(tx) + (internal_adj if qty > 0 else -internal_adj)
        # A reallocation's new shares: the parent's
        # tally share (and what a return of capital
        # took of it), if it gave its ACB up first
        # (TJ-0062).
        _ck = (tx.account, tx.corp_event_id)
        _mv: Decimal = _ZERO
        _cuts: Sequence[Tuple[Decimal, Decimal]] = ()
        if qty > 0 and it.global_pools.is_spinoff(_ck):
            _mv, _cuts = it.global_pools.child_opens(_ck, pool)
        _opening = is_opening_row(tx)
        # (`AcbPool.open`. `deferred`: the option's deferral
        # rides its premium into these shares, TJ-0061;
        # `unknown_from`: delivered by an option held at an
        # unknown cost, so is this, TJ-0947, the option's
        # OPENING rows its source.)
        pool.open(
            qty, effective_cost, date=tx.date,
            settle=None if _opening else run.get_sort_date(tx),
            lot_date=(tx.lot_date or tx.date) if _opening else None,
            deferred=internal_dw,
            unknown_from=internal_sources if internal_tainted else None,
            carried=_mv, cuts=_cuts)

        if qty < 0 and is_option_symbol(symbol):
            _ca_open_short_option(run, pool, tx, abs(qty),
                               effective_cost, 1.0,
                losses=it.iteration_losses, gains=it.iteration_realized_gains,
                traces=it.symbol_acb_traces, trace_sym=trace_sym)

        if run.trace:
            _ca_acb_trace(it, trace_sym)
            fee_amt = _effective_fee_for_trace(tx)
            pool_cost_f = M.of(pool.total_cost)
            acb_sh = _per_share(pool_cost_f, pool.qty, symbol)
            _lbl = ('OPENING' if is_opening_row(tx)
                    else tx.action)
            it.symbol_acb_traces[trace_sym].append(f"# {tx.date} {_lbl} {qty:10.4f} @ {M.shown(tx.price, 4):7.4f} | Fee: {fee_amt:6.4f} | Cost_Added: {effective_cost:10.4f} | Pool_Qty: {pool.qty:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
    else:
        # SELL (or Short covering) — divide in exact arithmetic.
        if abs(pool.qty) > _qeps or _dust_close:
            # (A sub-tolerance position with real
            # cost is closed at its cost: #101.)
            # SIGNED per-unit basis: total/|qty|, not
            # abs(total/qty) (FUZZ #F11): a short pool whose
            # opening proceeds went NEGATIVE under a large wash
            # deferral (a deferred loss can exceed the re-short's
            # premium) was flipped positive, the final cover
            # booking +3500 instead of recovering the -4500.
            avg_cost_unit_d = (pool.total_cost
                               / D(abs(pool.qty)))
        else:
            avg_cost_unit_d = Decimal(0)
        closing_qty = min(abs(qty), abs(pool.qty))
        # (the part past the position: booked below, or rounding kept here; TJ-4990)
        leftover = abs(qty) - closing_qty
        _close_share, _over_material = _past_position(tx, qty, closing_qty, internal_adj, _qeps)
        _recognized = _lot_out = _ZERO   # (the written lots' commission and fee: `write_outlay`)
        _grant_units_closed = 0.0
        _grant_closed = None
        _plain_close = M.of(D(closing_qty) * avg_cost_unit_d)
        _own_unknown = pool.written_taint(tx.account)      # (a written pool: the account's own, TJ-5377)
        if pool.qty < 0 and pool.grants:
            # Short option lots: FIFO by write, the
            # closing account's first; recognised lots
            # leave at their own premium (see
            # _short_lot_close).
            _lots = pool.grants
            # Close timing, one account's lots or lots
            # that do not hold the whole pool: the
            # pool's average.
            _avg_close = (
                not run._grant_mode
                and (len({l.get('account')
                          for l in _lots}) < 2
                     or abs(sum(l['units'] for l in _lots)
                            - abs(pool.qty)) > 1e-9))
            (cost_basis, _recognized,
             _grant_units_closed, _grant_closed, _lot_out) = \
                pool.close_lots(closing_qty, is_option_assign, tx.account)
            if _avg_close:
                cost_basis = _plain_close
        else:
            cost_basis = _plain_close

        # Apportion adjustment
        chunk_adj = internal_adj * M.of(_close_share)
        proceeds = _ca_trade_money(tx) * M.of(_close_share)
        effective_proceeds = proceeds + ((chunk_adj + _ca_delivered_cost(run, tx)) if pool.qty < 0 else -chunk_adj)

        gain = (effective_proceeds - cost_basis) if pool.qty > 0 else (cost_basis - effective_proceeds)

        # Grant-timing units closed here: the premium
        # already recognised on the write date comes
        # out of this record, so a buy-back is the
        # loss of the amount paid and an expiry is
        # zero. A stock-settled ASSIGN retracted the
        # grant record instead (_short_lot_close) and
        # folds the full premium into the share leg.
        _rec_gain = gain - _recognized
        _rec_cost = cost_basis - _recognized
        _suppress_record = (_grant_units_closed >= closing_qty - 1e-9
                            and closing_qty > 1e-9
                            and abs(_rec_gain) < _HALF_CENT
                            and abs(effective_proceeds) < _HALF_CENT)

        # Days held
        try:
            _acq_d = pool.last_acq_date
            if (pool.opening_acq
                    and _acq_d == '1970-01-01'):
                _acq_d = pool.opening_acq
            acq_dt = datetime.strptime(_acq_d, '%Y-%m-%d')
            disp_dt = datetime.strptime(tx.date, '%Y-%m-%d')
            raw_days = (disp_dt - acq_dt).days
            if raw_days < 0:
                emit_line(f"warning: negative days_held for {tx.symbol} "
                    f"on {tx.date} (last_acq={pool.last_acq_date}); "
                    f"this usually indicates out-of-order transactions")
            days_held = max(0, raw_days)
        except ValueError: days_held = 0

        # Check for taxable event (if tx is in the main transactions list)
        is_taxable = tx.id in run.taxable_ids

        if is_taxable and not is_option_assign and _suppress_record:
            assert _grant_closed is not None, "a suppressed record closed grant units (AcbPool.close_lots)"
            # No disposition, but the contract closed
            # this day: a broker's close-year slip
            # reports it, at its write's premium
            # (reconcile-slips, backlog TJ-1091).
            it.iteration_grant_nil_closes.append({
                'date': tx.date,
                'date_settle': tx.date_settle or tx.date,
                'symbol': symbol, 'account': tx.account,
                'grant_closed': {
                    y: {'units': M.units(v['units'], 9),
                        'premium': M.present(v['premium'], 6)}
                    for y, v in _grant_closed.items()}})
        if is_taxable and not is_option_assign and not _suppress_record:
            # Check for virtual disallowance for this specific ID
            disallowance_tx = next(iter(it._dis_by_id.get(tx.id, ())), None)
            if disallowance_tx:
                disallowed_amt = M.kept(disallowance_tx.net_amount)

            realized_pl = _rec_gain

            rg_trace: Union[List[str], SharedTrace] = []
            if run.trace:
                _ca_acb_trace(it, trace_sym)
                fee_amt = _effective_fee_for_trace(tx)
                # (shared, not copied: SharedTrace)
                rg_trace = SharedTrace(it.symbol_acb_traces[trace_sym])
                # The BOOKED figures (audit S069-10):
                # grant-timed units already recognised
                # their premium at the write, so the
                # record carries cost minus that
                # premium; the trace foots to it.
                gain_sh = _per_share(_rec_gain, abs(qty), symbol)
                _prem_note = (f" | Premium_Recognized_At_Write: {_recognized:10.4f}"
                              if abs(_recognized) > _NANO else "")
                rg_trace.append(f"# {tx.date} {tx.action} {qty:10.4f} @ {M.shown(tx.price, 4):7.4f} | Fee: {fee_amt:6.4f} | Proceeds: {effective_proceeds:10.4f} | Cost_Basis: {_rec_cost:10.4f} | Gain: {_rec_gain:10.4f} | Gain/Sh: {gain_sh:7.4f}{_prem_note}")

            # Apportion the sell tx's commission/fee to this
            # gain by the closing qty share. Matches the
            # convention sum-gains uses to roll up
            # per-trade fees.
            fee_share = _close_share if abs(qty) > 1e-9 else closing_qty
            # A tainted disposition draws on units of unknown cost
            # (an OPENING_BALANCE still in the pool): computed as any
            # other (the opening added no cost) and marked, so the
            # run routes it to manual_reporting_required, out of the
            # totals (lib/pipeline; CA-ACB-11), and names it with the
            # cost to add (lib/first_run.unknown_cost_sales, TJ-2770).
            is_tainted = ((pool.tainted if _own_unknown is None else bool(_own_unknown))
                          or internal_tainted)
            # (the OPENING cost=unknown rows behind it, TJ-3209)
            _ucf = _unknown_sources(pool, internal_sources, _own_unknown)
            # An in-kind contribution of LONG shares
            # to a registered plan (lib/in_kind): a
            # sale at fair market value whose loss
            # s.40(2)(g)(iv) denies for good — not a
            # superficial loss, no ACB addition
            # anywhere (CA-INKIND-03).
            _ik_type = getattr(tx, 'type', '') or ''
            _ik = (_ik_type if _ik_type in (
                IN_KIND_CONTRIBUTION_TYPE,
                IN_KIND_DISPOSITION_TYPE)
                and pool.qty > 0 else '')
            _ik_denied = (
                -_rec_gain
                if (_ik == IN_KIND_CONTRIBUTION_TYPE
                    and _rec_gain < _MINUS_MILLI
                    and not is_tainted) else _ZERO)
            # The earlier superficial losses this disposition's ACB carries (the s.53(1)(f)
            # additions the pool's deferred_wash tallies; a short's: taken off its proceeds;
            # never one parked on the pool, in no cost yet: TJ-4258), released in the same
            # share as the drain below releases them. The views that count economic P/L
            # before any denial take it back out (CA-RPT-16; audit 2026-10-25 planning-us#1).
            _dw_abs = abs(pool.qty)
            _dw_out = (
                pool.denied_in_cost
                * M.of(min(1.0, closing_qty / _dw_abs))
                if _dw_abs > _qeps else _ZERO)
            # ...and an exercised option's, in the
            # premium this sale absorbs (TJ-0061).
            _dw_out += internal_dw * M.of(_close_share)
            _dw_cut = (
                pool.wash_cut
                * M.of(min(1.0, closing_qty / _dw_abs))
                if _dw_abs > _qeps else _ZERO)
            _late_prem = (pool.cut_late
                          * _dw_cut
                          + max(_ZERO, -M.of(chunk_adj)))
            it.iteration_realized_gains.append({
                'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                'date_settle': tx.date_settle or tx.date,
                'gain': _rec_gain,
                'qty': closing_qty, 'cost': _rec_cost, 'proceeds': effective_proceeds,
                'disallowed': disallowed_amt, 'taxable_gain': _rec_gain + disallowed_amt + _ik_denied,
                **({'in_kind': 'contribution',
                    'denied_contribution': _ik_denied,
                    'note': (
                        'in-kind contribution to a '
                        'registered plan: a sale at '
                        'fair market value'
                        + ('; the loss is denied '
                           '(s.40(2)(g)(iv)), no ACB '
                           'addition'
                           if _ik_denied else '')
                        if _ik == IN_KIND_CONTRIBUTION_TYPE
                        else 'in-kind contribution to '
                        'a plan s.40(2)(g)(iv) does '
                        'not name: a sale at fair '
                        'market value')}
                   if _ik else {}),
                # Its rule (the run's `rules`): a sale at fair market value,
                # its loss denied for good when the plan is one s.40(2)(g)(iv) names.
                **({'rule': R.CA_INKIND_03 if _ik_denied else R.CA_INKIND_02} if _ik else {}),
                'days_held': days_held, 'account': account,
                'currency': tx.currency,
                'commission': M.of(tx.commission or 0) * M.of(fee_share),
                'fee': M.of(tx.fee or 0) * M.of(fee_share), **({'write_outlay': _w} if (_w := internal_outlay * M.of(fee_share) if pool.qty > 0 else _lot_out) > 0 else {}),   # (a written option's commission and fee: CA-OPT-06, CA-DISP-06)
                'direction': 'LONG' if pool.qty > 0 else 'SHORT',
                'tainted': is_tainted,
                **({'unknown_cost_from': _ucf}
                   if is_tainted and _ucf else {}),
                # Its unknown cost is an own move's
                # only: the accounts they came from
                # (backlog TJ-0279).
                **({'own_move_from': _own_move_only(pool)}
                   if is_tainted and not internal_tainted
                   and _own_move_only(pool)
                   else {}),
                'trace': rg_trace,
                **({'deferred_loss_recovered': _dw_out}
                   if _dw_out > _NANO else {}),
                **({'deferred_loss_cut': _dw_cut}
                   if _dw_cut > _NANO else {}),
                # (a written call's premium in these
                # proceeds was in no ACB either)
                **({'premium_after_cut':
                    _late_prem}
                   if _dw_cut > _NANO and _late_prem > _NANO
                   else {}),
                # {} when the close consumed only
                # close-timing lots; absent when the
                # pool kept no lots (a long close).
                **({'grant_closed': _grant_closed}
                   if _grant_closed is not None
                   else {}),
            })

            # Tainted losses never feed the superficial-
            # loss solver: they're computed against a
            # missing-history zero-cost pool and are bogus by
            # construction. Letting them through spawned
            # DISALLOW/ADJUST virtual rows whose ACB bump
            # could land on CLEAN lots after the taint
            # cleared (drain-to-zero), corrupting clean
            # gains — and fabricated wash_sales records
            # flowed into total_disallowed. The tainted
            # disposition itself is already excluded from
            # the gains report (manual_reporting_required).
            # A buy-back loss on a written option (any
            # timing: grant-timed units, a pre-since
            # transition lot, a close-timing project —
            # audit R1-270/R1-297) is fed
            # to the superficial-loss solver only when the
            # project opts in: s.54 needs "a loss from the
            # disposition of a property" and a closing
            # purchase disposes of nothing; CRA has no
            # published position applying the rule to it,
            # and the strict reading denies the loss for
            # good when a registered account holds the
            # same series (a same-minute order
            # correction could lose the whole loss).
            _written = pool.qty < 0 and is_option_symbol(symbol)
            _wash_eligible = run.option_buyback_loss_superficial or not _written
            # Every RAW loss re-enters the solver on every
            # pass (a fully denied one too), so each pass
            # shares the replacements out from scratch.
            if (_rec_gain < _MINUS_MILLI and not is_tainted
                    and _wash_eligible and not _ik_denied):
                it.iteration_losses.append({
                    'tx': tx, 'loss_amount': abs(_rec_gain),
                    'qty': closing_qty, 'direction': 'LONG' if pool.qty > 0 else 'SHORT',
                    # The rule its denial names (the run's `rules`).
                    'rule': R.CA_SL_12 if _written else R.CA_SL_01,
                })

        if is_option_assign:
            underlying = run._assign_underlying(tx)
            if underlying:
                # The contracts' share of the pool's
                # deferral tally goes with the
                # premium (TJ-0061).
                _dw_opt = (
                    pool.denied_in_cost
                    * M.of(min(1.0, closing_qty
                               / abs(pool.qty)))
                    if abs(pool.qty) > _qeps
                    else _ZERO)
                it.pending_adjustments.stage(
                    tx, underlying, -gain, _dw_opt,
                    bool(pool.tainted if _own_unknown is None else _own_unknown),
                    sources=pool.unknown_from if _own_unknown is None else _own_unknown,
                    outlay=_lot_out + ((M.of(tx.commission or 0) + M.of(tx.fee or 0)) * M.of(_close_share) if pool.qty < 0 else _ZERO))
        pool.close(closing_qty, cost_basis, _qeps)

        if run.trace:
            _ca_acb_trace(it, trace_sym)
            fee_amt = _effective_fee_for_trace(tx)
            pool_cost_f = M.of(pool.total_cost)
            pool_acb_sh = _per_share(pool_cost_f, pool.qty, symbol)
            it.symbol_acb_traces[trace_sym].append(f"# {tx.date} {tx.action} {qty:10.4f} @ {M.shown(tx.price, 4):7.4f} | Fee: {fee_amt:6.4f} | Cost_Rmvd: {cost_basis:10.4f} | Pool_Qty: {pool.qty:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {pool_acb_sh:7.4f}")

        # Handle leftover if it crosses zero — this is a fresh
        # position in the opposite direction, so last_acq_date resets.
        # Apportion any remaining internal_adj (e.g. an option-
        # premium roll-in) across the new opening, matching
        # tt_gains.pl:325-330 which apportions chunk_adj per
        # chunk's qty share.
        if leftover > _qeps or _over_material:
            leftover_ratio = leftover / abs(qty)
            leftover_adj = internal_adj * M.of(leftover_ratio)
            eff_cost_leftover = (
                _ca_trade_money(tx) * M.of(leftover_ratio)
                + (leftover_adj if qty > 0 else -leftover_adj)
            )
            # Position flipped direction (long→short
            # or vice versa via a cross-zero SELL).
            # New position begins at this trade.
            pool.leftover(
                leftover if qty > 0 else -leftover, D(eff_cost_leftover),
                deferred=internal_dw * M.of(leftover_ratio),
                date=tx.date, settle=run.get_sort_date(tx),
                unknown_from=internal_sources if internal_tainted else None)
            if qty < 0 and is_option_symbol(symbol):
                # The short leftover of a sell that
                # crosses zero is a write like any
                # other: a lot (and, under grant
                # timing, a grant record) of its own.
                _ca_open_short_option(run,
                    pool, tx, leftover,
                    eff_cost_leftover, leftover_ratio,
                    losses=it.iteration_losses, gains=it.iteration_realized_gains,
                    traces=it.symbol_acb_traces, trace_sym=trace_sym)


            if run.trace:
                it.symbol_acb_traces[trace_sym] = [f"# --- ACB CALCULATION TRACE: {trace_sym} ---"]
                fee_amt_leftover = _effective_fee_for_trace(tx) * M.of(leftover_ratio)
                pool_cost_f = M.of(pool.total_cost)
                acb_sh = _per_share(pool_cost_f, pool.qty, symbol)
                it.symbol_acb_traces[trace_sym].append(f"# {tx.date} {tx.action} {pool.qty:10.4f} @ {M.shown(tx.price, 4):7.4f} | Fee: {fee_amt_leftover:6.4f} | Cost_Added: {eff_cost_leftover:10.4f} | Pool_Qty: {pool.qty:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
    return (disallowed_amt, realized_pl)


def _conserved_rows(run: _CanadaRun, rows: List[TaxTransaction]) -> List[TaxTransaction]:
    """The taxable rows the conservation check replays (_finish): the trades,
    openings, splits, own moves and listing journals, a written move's leg at
    its effect on the pools' units (+ leaving, - arriving, whatever its sign:
    `_ev_memo['_move_effect']`, _own_move_written; TJ-3258) and an assignment,
    exercise or expiry past the account's own contracts at the units it closed
    (_ca_assign_close_units; TJ-3273, TJ-3302, TJ-3303)."""
    eff: Dict[RowId, float] = run._ev_memo.get('_move_effect', {})
    return [(replace(t, quantity=eff[t.id]) if t.id in eff else t)
            for t in rows
            if t.id in run.taxable_ids
            and (t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'SPLIT')
                 or _is_lot_move(t) or _is_listing_journal(t))]


def _walk_year_end_legs(run: _CanadaRun, it: _CanadaIteration) -> None:
    """The own-move legs dated up to the year end, walked before the year-end
    snapshot (TJ-3241, TJ-3257); the rest after it."""
    if it._msr_legs:
        _drain_move_legs(it._msr, it._msr_legs, (run.year_end, 100), pools=it.global_pools,
                         warned=run._move_notes, limit_date=run.year_end)


def _walk_move_legs(run: _CanadaRun, it: _CanadaIteration, tx: Optional[TaxTransaction]) -> None:
    """The own-move legs a blended book holds no row of (TJ-1180), walked into
    the router before row `tx` (every leg keyed below it; `tx` None: all of
    them, before a snapshot or at the walk's end, TJ-3226): each account's
    units, and a written move's lots re-tagged to the receiving account
    (_drain_move_legs; backlog TJ-3192, TJ-3224, TJ-3225)."""
    _limit = (None if tx is None
              else event_sort_key(tx, profile='ca_main', date_of=run.get_sort_date))
    _drain_move_legs(it._msr, it._msr_legs, _limit, pools=it.global_pools,
                     warned=run._move_notes, limit_date=None if tx is None else tx.date)


def _assign_rolls(*, is_other_scope: bool, it: _CanadaIteration, run: _CanadaRun,
                  symbol: str, tx: TaxTransaction) -> bool:
    """Does this option exercise or assignment (an `is_assign_premium_leg`
    row of `_walk_rows`) roll its premium into its shares, or is its own
    P&L realized here?

    It rolls when its underlying trades as stock in its account. A
    sheltered or affiliated pair keeps the premium-roll semantics
    regardless of the TAXABLE stock set: its pools are not computed
    here, and the cash-settled note would be spurious for a complete
    sheltered pair (re-audit). It rolls too (nothing staged, nothing
    realized) when its delivery, under the underlying's old name, is in
    an opening snapshot of the symbol it was renamed to, whose cost
    holds the premium (TJ-5005).

    Otherwise it is cash-settled (index options like XSP/SPX: the
    underlying never trades as stock in this book). No stock leg can
    ever consume a staged premium, so the row falls through to NORMAL
    disposition accounting: the option's own P&L is realized here
    instead of being staged forever and dropped. The note keeps the
    missing-data case diagnosable. (A row that carries an amount is
    routed like any other: its cash cannot tell a settlement from a
    commission or cash in lieu; where a snapshot holds its delivery the
    ledger says so, `_say_amount_on_delivered_row`.)"""
    if is_other_scope:
        return True
    ledger = it.pending_adjustments
    _und = run._assign_underlying(tx)
    if _und and (tx.account, _und) in run.taxable_stock_symbols:
        return True
    if ledger.renamed_delivery_underlying(tx):
        return True
    emit_line(f"note: {symbol}: assignment treated as "
              f"cash-settled ("
              f"{parse_option_underlying(symbol) or exercise_target(tx) or '?'}"
              f" never trades as stock in this book) — "
              f"option P&L realized directly. If a stock "
              f"leg is missing from your input, add it "
              f"and re-run.")
    return False


# The rows that change a pool (a dividend or a fee on its symbol does not).
_POOL_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'OPENING_BALANCE', 'SPLIT', 'ADJUST', 'DISALLOW')


def _pool_sig(p: Optional[AcbPool]) -> Any:
    """A year-end pool by what `_pools_copy` keeps of it, to tell whether a row changed it."""
    return None if p is None else (p.qty, p.total_cost, p.currency, p.deferred_wash,
                                   p.account_premiums, p.account_lots)


class _YearEndPools:
    """The pools (and each account's units short) at the end of `run.year_end`,
    read as the walk passes it: `at` before each row, `end` after the last; the
    walk hands back `pools` and `shorts`. The copy is taken before the first row
    past the year end, after the year's own-move legs (TJ-3226, TJ-3241, TJ-3257).
    On the walk's settlement dates that is the whole of it. A tax_date = "trade"
    project (`year_end_basis`) reads the year on trade dates (TJ-0343: a sale
    traded Dec 31 that settles in January has left the pool), and there a row
    traded after the year end can stand before rows traded in the year (a
    January 2 purchase settled that day, a December 29 sale settling January
    3): the one cut left every later in-year row out, silently (TJ-4984). So
    from that first row to the last row traded in the year (`open`: the fold
    takes no checkpoint there) the pools are read one by one: a pool stands as
    it was before the first row traded after the year end that changed it (or
    an own-move leg dated after it), a row traded in the year gives the pools
    it changed as they stand after it, and what a row changed is read off the
    pools themselves (a split, a rename's two keys, a spin-off's two pools, a
    denied loss's addition are rows like any other). A row touches the pools
    it changed and its own symbol's (and the one it renames to: a split of a
    pool the later-traded sale emptied changes nothing, and on trade dates it
    would have). A taxable row of the year that touches a pool a later-traded
    row touched cannot be read on trade dates (the books took the two in the
    other order): the pools it touches take none of it as it stands; one it
    changed among them takes the row's own change as far as that says
    (`_crossed`), and a warning names them: not exact."""

    __slots__ = ('pools', 'shorts', 'open', '_run', '_it', '_last', '_stay', '_prev')

    def __init__(self, run: _CanadaRun, it: _CanadaIteration, start: int,
                 pools: Optional[_Pools], shorts: Dict[Tuple[str, str], float]) -> None:
        self.pools, self.shorts, self.open = pools, shorts, False
        self._run, self._it = run, it
        self._last = -1             # the last row traded in the year (a trade-date year end)
        self._stay: Tuple[Set[Any], Set[Any]] = (set(), set())
        self._prev: Optional[Tuple[Optional[TaxTransaction], bool, _Pools,
                                   Dict[Tuple[str, str], float]]] = None
        if run.year_end and pools is None and run.year_end_basis == 'trade':
            for _i in range(start, len(it.current_tx_list)):
                if self._day(it.current_tx_list[_i]) <= run.year_end:
                    self._last = _i

    def _day(self, tx: TaxTransaction) -> str:
        return _ca_walk_date(self._run, self._it, tx, self._run.year_end_basis == 'trade')

    def _take(self) -> None:
        self.pools = _pools_copy(self._it.global_pools)
        self.shorts = dict(self._it._msr.short)

    def at(self, i: int, tx: TaxTransaction) -> None:
        """Before row `i` is walked."""
        run, it = self._run, self._it
        if self.open:
            self._settle()
            self.open = i <= self._last
        elif run.year_end and self.pools is None and self._day(tx) > run.year_end:
            _walk_year_end_legs(run, it)       # the year's moves only (TJ-3226, TJ-3241)
            self._take()
            self.open = i < self._last
        if self.open:
            now = (_pools_copy(it.global_pools), dict(it._msr.short))
            if it._msr_legs:
                _walk_move_legs(run, it, tx)    # dated after the year end: the pools they change stay
                self._prev = (None, False, *now)
                self._settle()
                now = (_pools_copy(it.global_pools), dict(it._msr.short))
            self._prev = (tx, self._day(tx) <= str(run.year_end), *now)

    def end(self) -> None:
        """After the last row."""
        if self.open:
            self._settle()
            self.open = False
        if self._run.year_end and self.pools is None:
            _walk_year_end_legs(self._run, self._it)   # the year's moves, the snapshot, then the rest (TJ-3257)
            self._take()

    @staticmethod
    def _crossed(mine: AcbPool, b: AcbPool, a: AcbPool) -> AcbPool:
        """The year-end pool `mine` (as it stood before a later-traded row
        changed it) after a row of the year that took the walk's pool from `b`
        to `a`: units leaving take their share of its cost and denied losses
        at its own average; any other change (units arriving, a cost
        adjustment, a split's units) is added as the walk booked it."""
        dq = a.qty - b.qty
        if mine.qty and dq and (dq < 0) != (mine.qty < 0) and abs(dq) <= abs(mine.qty):
            left = D(mine.qty + dq) / D(mine.qty)
            return mine.part(mine.qty + dq, mine.total_cost * left, M.of(mine.deferred_wash) * left)
        return mine.part(mine.qty + dq, mine.total_cost + (a.total_cost - b.total_cost),
                         M.of(mine.deferred_wash) + (M.of(a.deferred_wash) - M.of(b.deferred_wash)))

    def _own(self, tx: Optional[TaxTransaction]) -> Set[str]:
        """The pools a taxable row's own symbols name, changed or not."""
        if tx is None or tx.action not in _POOL_ACTIONS or tx.id in self._run.sheltered_ids \
                or tx.id in self._run.affiliated_ids:
            return set()
        _sfx, _ = _futures_names()
        return {k for _s in (tx.symbol, tx.symbol_new) if _s for k in (_s, _s + _sfx)}

    def _settle(self) -> None:
        """What was walked last (a row, or the move legs before one): the pools
        and the units short it touched, into the year end's or out of them."""
        assert self._prev is not None and self.pools is not None, "set when the window opens (at)"
        tx, in_year, b_pools, b_shorts = self._prev
        a_pools, a_shorts = _pools_copy(self._it.global_pools), dict(self._it._msr.short)
        p_changed = [k for k in dict.fromkeys([*b_pools, *a_pools])
                     if _pool_sig(b_pools.get(k)) != _pool_sig(a_pools.get(k))]
        s_changed = [k for k in dict.fromkeys([*b_shorts, *a_shorts])
                     if b_shorts.get(k, 0.0) != a_shorts.get(k, 0.0)]
        touched = set(p_changed) | self._own(tx)
        if not in_year:
            self._stay[0].update(touched)
            self._stay[1].update(s_changed)
            return
        pools, shorts = dict(self.pools), dict(self.shorts)
        crossing = bool(touched & self._stay[0])
        crossed: Dict[int, AcbPool] = {}
        for k in p_changed:
            b, a, mine = b_pools.get(k), a_pools.get(k), pools.get(k)
            if not crossing:
                if a is None:
                    pools.pop(k, None)
                else:
                    pools[k] = a
            elif k in self._stay[0] and b is not None and a is not None and mine is not None:
                if id(mine) not in crossed:
                    crossed[id(mine)] = self._crossed(mine, b, a)
                pools[k] = crossed[id(mine)]
        if crossing:
            self._stay[0].update(touched)
            assert tx is not None, "the move legs are never the year's (at)"
            self._say(sorted({_s for _s in (tx.symbol, tx.symbol_new) if _s}))
        for sk in s_changed:
            shorts[sk] = (a_shorts.get(sk, 0.0) if sk not in self._stay[1]
                          else shorts.get(sk, 0.0) + a_shorts.get(sk, 0.0) - b_shorts.get(sk, 0.0))
        self.pools, self.shorts = pools, shorts

    def _say(self, names: List[str]) -> None:
        """The warning for a pool read across both date orders, once a run."""
        _said = self._run._ev_memo.setdefault('_ye_crossed', set())
        if tuple(names) in _said:
            return
        _said.add(tuple(names))
        emit_line(f"{YEAR_END_INEXACT}{', '.join(names)}: the pool at the end of {self._run.year_end} (by "
                  f"trade date) is not exact\n"
                  f"  A row traded after that day settles before a row traded by it, and both touch this "
                  f"pool (a trade, a split or a ticker change), so the books took them in the other "
                  f"order. The year-end pool is the one before the later-traded row with each row of the "
                  f"year applied as far as it can be: units sold leave at that pool's average, which is "
                  f"not the average the sale's gain used, and where a split or a ticker change is among "
                  f"the crossing rows the units are not exact either.\n"
                  f"  Check this year-end pool (`tjs edge-cases`) against your records.")


def _walk_rows(*, it: _CanadaIteration, run: _CanadaRun, fold: Optional[_WalkFold] = None) -> Tuple[Optional[_Pools], Dict[Tuple[str, str], float]]:
    """The walk over one iteration's rows of the Canadian engine's
    fixed-point loop: pools, realized gains, losses, traces and the cost
    timeline, filled into the loop's containers in place. The loop's body,
    moved out unchanged so the golden ledgers prove the move (backlog
    TJ-1839 splits it); the loop hands it the run's invariant state as `run`
    (`_CanadaRun`) and this iteration's as `it` (`_CanadaIteration`), and reads
    back (_ye_pools, _ye_shorts), the year-end pools it may set. `fold`
    (`_WalkFold`): the re-fold's resume point and the rows it checkpoints
    before; None walks the whole book from a fresh state."""
    _, _fut_pool_key = _futures_names()
    _ye_pools: Optional[_Pools] = None
    _ye_shorts: Dict[Tuple[str, str], float] = {}
    _tl_prev: List[TaxTransaction] = []
    _start = 0
    if fold is not None:
        _start, _ye_pools, _ye_shorts = fold.start, fold.ye_pools, fold.ye_shorts
        if _start and it._tl is not None:
            _tl_prev = [it.current_tx_list[_start - 1]]
    _ye = _YearEndPools(run, it, _start, _ye_pools, _ye_shorts)
    for _i in range(_start, len(it.current_tx_list)):
        tx = it.current_tx_list[_i]
        if fold is not None and _i in fold.marks and not _ye.open:
            fold.save(_i, it, _ye.pools, _ye.shorts)
        if it._tl is not None:
            _tl_d = _ca_walk_date(run, it, tx)
            if _tl_prev:
                _ca_tl_after(run, it, len(it._tl_dates) - 1, _tl_prev[0])
                if _tl_d != it._tl_dates[-1][0]:
                    it._tl.sweep(len(it._tl_dates) - 1)
            it._tl_dates.append(
                (_tl_d, _tl_d if run.cost_timeline != 'trade'
                 else (_ca_walk_date(run, it, tx, True) or _tl_d)))
            # Which book row this step is (T1135 places a
            # custody account's rows between a day's steps:
            # backlog TJ-0646). A DISALLOW is the solver's row,
            # not a row of the books: it carries its sale's id,
            # and the id walked twice could not be placed
            # (backlog TJ-1099).
            it._tl.row_ids.append('' if tx.action == 'DISALLOW'
                               else str(tx.id or ''))
            _tl_prev[:] = [tx]
        # The year-end pools, on the walk's dates or, a tax_date =
        # "trade" project (`year_end_basis`), on trade dates: a sale
        # traded Dec 31 that settles in January (this year's
        # disposition on trade dates) has left the pool (backlog
        # TJ-0343, the mirror of the US settle case; TJ-4984).
        _ye.at(_i, tx)
        if it._msr_legs:
            _walk_move_legs(run, it, tx)        # the moves before this row (TJ-1180, TJ-3226)
        symbol = tx.symbol
        account = tx.account
        is_sheltered = tx.id in run.sheltered_ids
        is_affiliated = tx.id in run.affiliated_ids
        # Sheltered and affiliated trades both live OUTSIDE the
        # taxable ACB pool — sheltered because registered-account
        # property is treated as separate for cost-basis purposes,
        # affiliated because the spouse / controlled corp owns it,
        # not the user. The wash-sale walk still sees them via
        # current_tx_list (so the 30-day "still held" balance and
        # the substitution-property rule fire correctly), but the
        # pool stays clean.
        is_other_scope = is_sheltered or is_affiliated

        # Non-capital cash-flow events (dividends, interest, fees,
        # taxes, transfers) don't touch the ACB pool or account
        # balances. They often live on a synthetic "CASH" symbol
        # whose currency varies by row, so skip them before
        # currency/pool initialization. TRANSFER is handled at the
        # CLI layer — for sheltered files it's rewritten to BUYSELL
        # before reaching the engine, and for taxable files the CLI
        # errors out before invoking compute_gains. By the time
        # the engine runs, no TRANSFER rows should be present —
        # but one account's own view of a move between two of
        # your taxable accounts (_own_move_view): its units move;
        # and a native book's journal between two listings.
        if _is_listing_journal(tx):
            if not is_other_scope:
                it._msr.walk(tx)
            _jl = it._jlegs.get(tx.journal_pair)
            if (_jl and not is_other_scope
                    and tx.journal_pair not in it._jdone):
                it._jdone.add(tx.journal_pair)
                _jtxt = _journal_units(it.global_pools, *_jl)
                if run.trace:
                    for _js in (_jl[0].symbol, _jl[1].symbol):
                        it.symbol_acb_traces.setdefault(_js, [
                            f"# --- ACB CALCULATION TRACE: {_js} ---"
                        ]).append(f"# {tx.date} {_jtxt}")
            continue
        if _is_lot_move(tx):
            if not is_other_scope:
                _kind = it._msr.move_leg_kind(tx) if is_option_symbol(symbol) else 'bought'
                it._msr.walk(tx)
                _mv_txt = (_own_move_written(run, it, tx, abs(float(tx.quantity or 0.0)), _kind)
                           if _kind != 'bought' else _own_move_units(it.global_pools, _fut_pool_key(tx), tx))
                if run.trace:
                    _ca_acb_trace(it, symbol if _kind == 'bought' else _trace_name(
                        symbol + _futures_names()[0], symbol)).append(f"# {tx.date} {_mv_txt}")
            continue
        if tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
           or tx.type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee'):
            continue

        # A short futures position has its own pool beside the
        # long one (lib/futures.engine_pool_key): a short opened
        # in one account never closes a long held in another,
        # so each close is booked in the year its account
        # closed it (issue #76). Every other row: its symbol.
        _pkey = _fut_pool_key(tx)
        if _pkey == symbol and not is_other_scope:
            _pkey = _ca_stock_short_key(run, it, tx)
        elif _pkey != symbol and not is_other_scope:
            it._msr.walk_futures_short(tx)
        pool = it.global_pools.ensure(_pkey)
        _pre_qty, pool_sym, _tsym = pool.qty, symbol, _trace_name(_pkey, symbol)    # the drain's scale; the pool's key (a rename rebinds it); its trace (TJ-3228)

        # Currency-mix guard: one ACB pool, one currency. Taxable scope only
        # (a sheltered or affiliated trade must not stamp the taxable pool's
        # currency nor be checked against it). A SPLIT carries no money, so its
        # stamp says nothing (a .tt SPLIT is stamped CAD whatever the listing;
        # A2-0010, regression of R1-126): the real mix shows on the trades.
        if not is_other_scope and tx.action != 'SPLIT':
            if tx.currency and pool.currency and tx.currency != pool.currency:
                raise ValueError(
                    f"Currency mismatch for {symbol}: pool is in {pool.currency!r} "
                    f"but {tx.action} {tx.id} on {tx.date} is in {tx.currency!r}. "
                    + _currency_mismatch_advice(tx, pool.currency))
            if tx.currency:
                pool.stamp_currency(tx.currency)

        # Internal adjustment logic (Option Assignment).
        # Gated on the taxable scope: only a taxable BUYSELL
        # on the underlying may consume the pending premium.
        # Popping for a sheltered/affiliated trade would
        # silently discard the adjustment (the pass branch
        # downstream skips pool mutation for is_other_scope),
        # leaving a later taxable trade on the same underlying
        # with no premium roll.
        if (is_other_scope or is_option_symbol(symbol)
                or exercise_target(tx)
                or tx.action not in ('BUYSELL', 'ASSIGN')
                or is_opening_row(tx)):
            # Only a stock trade can be an assignment's leg (an
            # ADJUST / SPLIT / OB row on the underlying used to
            # pop — and drop — the staged premium).
            internal_adj = internal_dw = internal_outlay = _ZERO
            internal_tainted = False
            internal_sources: List[Dict[str, Any]] = []
        else:
            # The ledger keeps a premium whose own (paired) leg
            # is still to come away from this trade.
            _taken = it.pending_adjustments.take(tx)
            internal_adj = _taken.amount
            internal_outlay = _taken.outlay     # (a written call's commission: the sale's outlay, TJ-5381)
            # The earlier superficial loss in that premium (an
            # exercised replacement call: backlog TJ-0061).
            internal_dw = _taken.deferred
            # An unknown-cost option's premium: these shares'
            # cost (or this sale's proceeds) is unknown too
            # (TJ-0947).
            internal_tainted, internal_sources = _taken.tainted, list(_taken.sources)
            if (internal_adj < 0 and float(tx.quantity or 0.0) > 0
                    and pool.wash_cut > _NANO):
                # A put's premium arriving after the cut: see
                # AcbPool.add_wash_cut.
                pool.late_premium(internal_adj)
        
        action = tx.action
        qty = tx.quantity
        
        # Variables for trace
        realized_pl = None
        disallowed_amt = _ZERO
        note = ""
        trigger_info = ""
        adjustment_shown: Union[Decimal, float] = 0.0
        
        is_option_assign = (is_assign_premium_leg(tx)
                            and _assign_rolls(is_other_scope=is_other_scope, it=it,
                                              run=run, symbol=symbol, tx=tx))

        if action == 'DISALLOW':
            note = "DISALLOWANCE"
            adjustment_shown = tx.net_amount
            if run.trace:
                _ca_acb_trace(it, _tsym).append(f"# {tx.date} DISALLOW {_shown_if_float(tx.net_amount, 4):10.4f} | (Loss disallowed on this date)")
        elif action == 'ADJUST':
            (adjustment_shown, note) = _walk_adjust(is_other_scope=is_other_scope, it=it,
                                                    pool=pool, run=run, symbol=symbol, tx=tx, trace_sym=_tsym)
        elif action == 'SPLIT':
            pool, pool_sym = _walk_split(is_other_scope=is_other_scope, it=it, pool=pool,
                                         qty=qty, run=run, symbol=symbol, tx=tx)
            note = "RENAMED" if pool_sym != symbol else note   # the pool columns are the target's
        elif action == 'OPENING_BALANCE':
            # Missing-history opening (a .tt OPENING cost=unknown line): units with unknown
            # ACB, added at cost 0 and not trusted (the pool is tainted: a disposition while
            # they remain is computed at the pool's cost and marked for the run to route to
            # manual reporting, CA-ACB-11, TJ-2770). A drain to zero clears the taint.
            if not is_other_scope:
                # OB seeds the position-start when the pool
                # was empty. Position_start is approximate
                # for tainted pools (the real entry is
                # pre-data-window), but the OB date is the
                # best signal available.
                # (`move_in` takes what waits on the flat pool: TJ-2768)
                pool.move_in(qty, tx.date)
                # no cost added: the taint tracks the unknown; the
                # row's own fields name the line (TJ-3209)
                pool.taint(source=_opening_source(tx, symbol))
                if qty < 0 and is_option_symbol(symbol):
                    # Missing-history short contracts hold a FIFO place
                    # (never recognised — their write predates
                    # the data).
                    _ca_short_lot_open(run, pool, tx, abs(qty), 0.0, False)['unknown'] = True
            if run.trace:
                _ca_acb_trace(it, _tsym).append(f"# {tx.date} OPENING_BALANCE {qty:10.4f} | Missing history (bought before the data) — cost UNKNOWN until the pool drains to zero")
        else:
            # Only an exact zero is skipped (audit R1-24 /
            # R1-245): the 1e-6 share epsilon dropped every
            # sub-micro crypto row — staking rewards whose
            # income was booked lost their units and cost, and
            # the pool fell short of the wash walk's balance.
            if qty == 0: continue
            (disallowed_amt, realized_pl) = _walk_trade(
                account=account, internal_adj=internal_adj, internal_dw=internal_dw,
                internal_tainted=internal_tainted, is_option_assign=is_option_assign,
                is_other_scope=is_other_scope, it=it, pool=pool, qty=qty, run=run,
                symbol=symbol, tx=tx, trace_sym=_tsym, internal_sources=internal_sources, internal_outlay=internal_outlay)

        if run.trace:
            fee_sh = _per_share(M.of(tx.commission) + M.of(tx.fee), abs(tx.quantity), symbol)
            price_fee = M.of(tx.price) + (fee_sh if tx.quantity > 0 else -fee_sh)
            pool_cost_f = M.of(pool.total_cost)
            acb_sh = abs(_per_share(pool_cost_f, pool.qty, pool_sym))
            realized_pl_str = f"{realized_pl:10.2f}" if realized_pl is not None else " " * 10
            disallowed_amt_str = f"{disallowed_amt:10.2f}" if disallowed_amt != 0 else " " * 10
            trace_line = f"# {account:<26} | {note:<12} | {tx.date} {tx.time} | {symbol:<26} | {action:<8} | {qty:10.4f} | {M.shown(tx.price, 4):10.4f} | {fee_sh:10.4f} | {price_fee:10.4f} | {_shown_if_float(tx.net_amount, 2):10.2f} | {_shown_if_float(adjustment_shown, 2):10.2f} | {pool.qty:11.4f} | {pool_cost_f:10.2f} | {acb_sh:10.4f} | {realized_pl_str} | {disallowed_amt_str} | {trigger_info}"
            it.iteration_trace.append(trace_line)

        # A residue under the tolerance is drained to zero — but
        # not a position that carries real cost (MATERIAL_COST),
        # whichever row left it: zeroed, the next sale found an
        # empty pool, opened a short and lost the gain, its cost
        # stranded (2026-10 audit #101; the cash-conservation
        # fuzz; a consolidation, TJ-0649; the rest of a part
        # sale, TJ-1062, TJ-1141).
        # (`AcbPool.drain`; `closed`: the long holding this sale closed)
        _drained = pool.drain(
            pool_qty_eps(pool_sym, _pre_qty, qty),
            closed=(tx.date, id(tx)) if _pre_qty > 0 and not is_other_scope else None)
        if _drained is not None and run.trace:
            if _drained[0]:
                _ca_acb_trace(it, pool_sym).append(f"# {tx.date} pool drained to zero — TAINT CLEARED")
            if _drained[1] and pool_sym == symbol and _tsym in it.symbol_acb_traces:   # a rename's target keeps its SPLIT-RENAME line
                it.symbol_acb_traces[_tsym] = [f"# --- ACB CALCULATION TRACE: {_tsym} ---"]

        # Snapshot taxable-pool state after this tx (sheltered txs
        # don't move the pool, so they inherit the prior snapshot).
        # Used by the wash-window renderer for the pool_qty / acb/sh
        # columns. Per-iteration so the converged state wins.
        it.pool_snapshots_this_iter[tx.id] = {'pool_qty': pool.qty, 'pool_acb': M.of(pool.total_cost),
                                              'pool_symbol': pool_sym}    # the pool the figures belong to
    # The last row's timeline step and the year-end default, once the
    # rows are walked.
    _ye.end()
    _walk_move_legs(run, it, None)             # the moves after the last row (TJ-3226, TJ-3247)
    if it._tl is not None and _tl_prev:
        _ca_tl_after(run, it, len(it._tl_dates) - 1, _tl_prev[0])
        it._tl.sweep(len(it._tl_dates) - 1)
    # Grant records fully retracted by an assignment (s.49(3)/(3.1))
    # leave the record list; their losses leave the solver (the marks
    # are this walk's, so the filter is too; in place, the lists are
    # the iteration's).
    if run._grant_mode:
        it.iteration_realized_gains[:] = [
            g for g in it.iteration_realized_gains if not g.get('_void')]
        it.iteration_losses[:] = [
            l for l in it.iteration_losses if not l.get('_void')]
        for g in it.iteration_realized_gains:
            g.pop('_void', None)
    return _ye.pools, _ye.shorts

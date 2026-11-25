"""A buy or sell row against the lots, and one realized-gain entry.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, TypeAlias, cast
from datetime import datetime

from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.lib.option_symbols import is_option_symbol
from taxjson.lib.row_types import is_opening_row
from taxjson.engine.assign import _day_gap
from taxjson.engine.consts import _ZERO, _NANO
from taxjson.engine.us.lot_store import (
    _us_ov_take, _us_ov_share, _us_ov_loss, _us_take_option_adj, _us_insert_lots,
)
from taxjson.engine.us.terms import held_more_than_one_year, _US_MONEY_EPSILON
from taxjson.engine.us.wash import (
    _us_rkey_on, _us_rep_units_factor, _us_ix, _us_set_ref, _us_find_replacements_in_window,
    _us_record_sold_replacement, _us_record_left_shares, _us_apply_held_long, _us_apply_held_short,
    _us_with_sold_replacements, _us_match_sold_replacement, _us_name_delivered_lots,
    _us_run_retests, _us_outside_1091, _us_flag_futures_loss, _us_note_rules,
)
from taxjson.engine.us.lots import _us_plan_disposition, _us_delivered_long

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.us.lot_store import _IKey, _USLot
    from taxjson.engine.us.run import _USRun
    from taxjson.engine.us.wash import _RepEntry, _WashCand


# A realized-gain row of the US walk (`_us_gain_entry`; a dividend's, a
# deemed gain's, a §356 boot's too): date, date_settle, symbol, qty, cost,
# proceeds, gain, raw_gain (Decimal), the wash fields, the term, the row id.
_GainRow: TypeAlias = Dict[str, Any]


def _us_delivered_cost(run: _USRun, ikey: _IKey, tx: TaxTransaction, chunk_qty: float
                       ) -> Decimal:
    """A covering chunk's part of what delivering a stock dividend's shares cost, where the
    purchase `tx` is the cover a later stock dividend row names (the run's
    `stkdiv_after_cover`: the row is the covered short's obligation and carries a
    `delivered_cost`; US-STKDIV-06): a cost of closing that short, so in the gain or loss
    the §1091 test sees, never in a lot the purchase opens. Shared over the units the
    purchase covers (this chunk's and the shorts still open when its first chunk asks)."""
    out = _ZERO
    for f in run.stkdiv_after_cover.values():
        if f['cover'] != tx.id or not f['cost']:
            continue
        if f.get('units_for') != tx.id:
            f['units_for'], f['booked'] = tx.id, _ZERO
            f['units'] = min(tx.quantity, chunk_qty + sum(
                _s['qty'] for _s in run.inventory_short.get(ikey, [])))
        part = f['cost'] * M.of(chunk_qty / f['units']) if f['units'] > 0 else _ZERO
        f['booked'] += part
        f['chunk'] = f.get('chunk', _ZERO) + part
        out += part
    return out


def _us_with_delivery(run: _USRun, tx: TaxTransaction, entry: _GainRow) -> _GainRow:
    """A cover's gain row with the part of a stock dividend's delivery cost its chunk took
    (`stock_dividend_delivery`, as `_us_delivered_cost` left it); any other row as it is."""
    for f in run.stkdiv_after_cover.values():
        if f['cover'] == tx.id and f.get('chunk'):
            entry['stock_dividend_delivery'] = M.present(
                M.of(entry.get('stock_dividend_delivery') or 0.0) + f.pop('chunk'), 4)
    return entry


def _us_walk_buy(*, ikey: _IKey, is_option_assign: bool, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction, tx_net: Decimal, tx_qty_abs: float,
                    underlying_for_assign: Optional[str], delivered: Optional[Dict[str, Any]] = None) -> None:
    """A buy row: the pending option-assignment premium taken once; a buy-to-close pops the
    short lots FIFO, each chunk's gain, holding period and wash match booked; what remains
    opens a long lot (its replacement capacity, the timeline). `delivered`: a long
    lot delivered to a short (`_us_deliver_to_short`: its deferral, its taint, its term)."""
    qty_remaining = tx.quantity

    # Pop the pending option-assignment premium once, up front.
    # We apportion it across BOTH the buy-to-close chunks (below)
    # and any leftover buy-to-open (further down) so the full
    # premium is consumed even when an assignment crosses zero
    # (a short-put assignment that closes an existing short and
    # opens a new long). Earlier the pop happened only in the
    # leftover branch, silently discarding the close-share.
    # Stored as -gain (Canada convention). Only the marked
    # ASSIGN stock leg may consume it when one exists
    # (mirrors the Canada engine): an unrelated same-symbol
    # trade sorted between the option leg and the stock leg
    # used to hijack the premium.
    _taken = _us_take_option_adj(run, tx)
    option_adj = _taken.amount
    # An exercised replacement option's wash deferral in
    # that premium: these shares' lots tally it (TJ-0061).
    option_dw = _taken.deferred
    # An unknown-cost option's premium: the shares' basis
    # (a cover's cost) is unknown too (TJ-0947).
    option_tainted = _taken.tainted
    # (the OPENING row behind the option, for the lots it delivers)
    option_source = dict(_taken.sources[0]) if option_tainted and _taken.sources else None
    if delivered:
        option_dw = option_dw + delivered['deferred']
        option_tainted = option_tainted or delivered['tainted']
        option_source = option_source or delivered.get('source')
    if not is_option_assign:
        _us_plan_disposition(run, run.inventory_short, ikey, tx,
                          'short_lot_ref', 'proceeds')

    # --- buy-to-close: pop short lots FIFO ---
    while qty_remaining > run.epsilon and run.inventory_short[ikey]:
        short_lot = run.inventory_short[ikey][0]
        if short_lot['qty'] <= qty_remaining + run.epsilon:
            if (short_lot['qty'] - qty_remaining
                    > run._dust_noise(short_lot['qty'])):
                run._us_dust_absorbed.append(
                    (symbol, short_lot['qty'] - qty_remaining))
            chunk_qty = short_lot['qty']
            chunk_open_proceeds_d = short_lot['proceeds']
            chunk_wd_d = short_lot.get('wash_deferred',
                                       Decimal(0))
            run.inventory_short[ikey].pop(0)
            # A short_replacement record that pointed at this
            # lot is consumed: no lot reference (a later match
            # must not mutate the detached dict) and no
            # capacity, so find_replacements_in_window skips it
            # (tax-logic US-WASH-21, re-audit A2-0817).
            for r in _us_ix(run, run.short_replacements.get(
                    _us_rkey_on(run, symbol, tx.date), [])).holding(
                        'short_lot_ref', short_lot):
                _us_set_ref(run, r, 'short_lot_ref', None)
                # Covered shares can no longer serve as
                # §1091 replacement (FUZZ #A): leaving
                # capacity here matched later losses
                # against DEAD lots -> permanent denial
                # of real losses in taxable-only chains.
                r['remaining_qty'] = 0.0
        else:
            chunk_qty = qty_remaining
            chunk_open_proceeds_d = (D(chunk_qty) / D(short_lot['qty'])) * short_lot['proceeds']
            short_lot['qty'] -= chunk_qty
            short_lot['proceeds'] -= chunk_open_proceeds_d
            # Release embedded deferral proportionally on a
            # partial cover (mirrors the long side's
            # wash_deferred proration).
            _swd = short_lot.get('wash_deferred', Decimal(0))
            chunk_wd_d = ((D(chunk_qty)
                           / D(short_lot['qty'] + chunk_qty))
                          * _swd if _swd else Decimal(0))
            if _swd:
                short_lot['wash_deferred'] = _swd - chunk_wd_d
            for r in _us_ix(run, run.short_replacements.get(
                    _us_rkey_on(run, symbol, tx.date), [])).holding(
                        'short_lot_ref', short_lot):
                # remaining_qty is denominated in the
                # rep's own trade-date units; chunk_qty
                # is in TODAY's units. With a split in
                # between, the raw subtraction left
                # phantom capacity (or erased real
                # capacity) — and since this decrement
                # is the self-match guard, that skewed
                # §1091 quantities directly.
                _cuf = _us_rep_units_factor(run,
                    symbol, r['date'], tx.date)
                r['remaining_qty'] = max(
                    0.0, r['remaining_qty']
                    - chunk_qty / (_cuf or 1.0))

        chunk_open_proceeds = M.of(chunk_open_proceeds_d)
        # Close cost is apportioned from this BUY by qty share.
        chunk_close_cost = M.of(chunk_qty / tx_qty_abs) * tx_net
        # Apportion the option-assignment premium to this
        # chunk's close cost (short-put assignment: the
        # premium reduces the effective cost of acquiring
        # the stock used to cover the short).
        if option_adj != 0:
            chunk_close_cost += option_adj * M.of(chunk_qty / tx_qty_abs)
        # Dividend compensation paid on these shares, closed
        # within 45 days: part of the cost of covering them,
        # so in the gain or loss the §1091 test below sees
        # (§263(h)(1); US-INC-04, backlog TJ-1059).
        _cap_263h = (_ZERO if is_option_assign else
                     M.of(chunk_qty) * run._comp_per_unit.get(
                         (tx.id, short_lot.get('id')), _ZERO))
        chunk_close_cost += _cap_263h + _us_delivered_cost(run, ikey, tx, chunk_qty)
        # Gain on closing a short: opening_proceeds − closing_cost.
        raw_gain = chunk_open_proceeds - chunk_close_cost

        if is_option_assign:
            assert underlying_for_assign is not None, "an option assign has its underlying (_us_walk_rows)"
            # Premium rolls into the underlying — accumulate the
            # would-be gain (with sign flipped to match the
            # CanadaTaxRules / Pub 550 convention: BUY of stock
            # subtracts pending_adj from cost; SELL of stock adds
            # pending_adj to proceeds).
            run.pending_option_adjustments.stage(
                tx, underlying_for_assign, -raw_gain,
                M.of(chunk_wd_d),
                bool(short_lot.get('tainted')),
                sources=([short_lot['unknown_from']]
                         if short_lot.get('unknown_from') else None))
            if run.trace:
                symbol_traces[symbol].append(
                    f"# {tx.date} ASSIGN-CLOSE {chunk_qty:10.4f} | "
                    f"OpenProc: {chunk_open_proceeds:10.4f} → "
                    f"premium rolled into {underlying_for_assign} basis "
                    f"(would-be gain {raw_gain:.4f})"
                )
            qty_remaining -= chunk_qty
            continue

        disallowed_amt = _ZERO
        permanently_disallowed_amt = _ZERO
        replacement_ids: List[RowId] = []
        wash_reps: List[_RepEntry] = []
        ov_share = _us_ov_share(run, tx)

        # Tainted (missing-history OPENING_BALANCE) short lots have
        # proceeds=0, so covering them ALWAYS books a bogus loss
        # — it must never feed §1091 matching (it would fabricate
        # wash records and push proceeds-reductions onto clean
        # lots). Mirrors the Canada engine's tainted-loss gate.
        if (raw_gain < -_US_MONEY_EPSILON and run.detect_wash_sales
                and not short_lot.get('tainted')):
            # §1091 on shorts: a loss on closing a short is
            # disallowed if a substantially-identical short was
            # opened within ±30 days. The disallowed loss reduces
            # the replacement short's effective opening proceeds.
            remaining_loss_qty = chunk_qty
            chunk_loss = abs(raw_gain)
            loss_per_share_d = D(chunk_loss) / D(chunk_qty)
            _held = _us_find_replacements_in_window(run,
                run.short_replacements.get(_us_rkey_on(run, symbol, tx.date), []),
                tx.date,
            )
            candidates: Sequence[_WashCand] = _held
            if _us_outside_1091(run, symbol):
                if _held:
                    _us_flag_futures_loss(run, tx, raw_gain, _held)
                candidates = []
            else:       # (§1091(e); US-WASH-22, TJ-2708)
                candidates = _us_with_sold_replacements(
                    run, candidates, run.short_replacements.get(
                        _us_rkey_on(run, symbol, tx.date), []), tx, short=True)
            _ov_on = _us_ov_loss(run, tx, chunk_qty, chunk_loss, ov_share)
            for rep in candidates:
                if remaining_loss_qty <= run.epsilon:
                    break
                if isinstance(rep, tuple):
                    match_qty, _wr = _us_match_sold_replacement(
                        run, rep, remaining_loss_qty, loss_per_share_d, tx,
                        short_lot, _ov_on, ov_share, short=True)
                    remaining_loss_qty -= match_qty
                    if _wr is not None:
                        disallowed_amt += _wr['proceeds_reduction']
                        replacement_ids.append(_wr['tx_id'])
                        wash_reps.append(_wr)
                    continue
                # rep['remaining_qty'] is in the rep's own trade-
                # date units; convert to the loss-date's units
                # for the comparison, and consume in rep units.
                _uf = _us_rep_units_factor(run, symbol, rep['date'], tx.date)
                match_qty = min(remaining_loss_qty,
                                rep['remaining_qty'] * _uf)
                match_disallowed_d = D(match_qty) * loss_per_share_d
                match_disallowed = M.of(match_disallowed_d)
                rep['remaining_qty'] -= match_qty / _uf
                remaining_loss_qty -= match_qty
                if _ov_on:
                    assert ov_share is not None, "_us_ov_loss turns the overlap on only with a share"
                    _us_ov_take(run, tx, rep['tx'], rep['is_sheltered'],
                             rep.get('is_affiliated'),
                             match_qty, match_disallowed, ov_share)
                    continue

                _wr = _us_apply_held_short(run, rep, match_qty,
                                           match_disallowed_d)
                if _wr['is_sheltered'] or _wr['is_affiliated']:
                    permanently_disallowed_amt += match_disallowed
                else:
                    disallowed_amt += match_disallowed
                replacement_ids.append(rep['tx'].id)
                wash_reps.append(_wr)

        # Stand-alone shorts: holding period is conventionally
        # zero — gain on close is SHORT_TERM per §1222.
        # §1233(b)(1) (a long held ≤1yr at short-open makes a
        # gain short-term) and §1233(d) (a long held >1yr makes
        # a loss long-term) are not implemented in v1.
        is_long_term = _us_delivered_long(delivered, short_lot)
        acq_for_holding = cast("str", short_lot.get('effective_open_date', short_lot['date']))
        try:
            days_held = (datetime.strptime(tx.date, '%Y-%m-%d') -
                         datetime.strptime(acq_for_holding, '%Y-%m-%d')).days
        except ValueError:
            days_held = 0

        allowed_gain = raw_gain + disallowed_amt + permanently_disallowed_amt

        if run.trace:
            wash_tag = ''
            if disallowed_amt > 0 or permanently_disallowed_amt > 0:
                wash_tag = f" [WASH disallowed={disallowed_amt + permanently_disallowed_amt:.4f}]"
            symbol_traces[symbol].append(
                f"# {tx.date} BUY-CLOSE {chunk_qty:10.4f} | "
                f"Against Short: {short_lot['date']} | "
                f"OpenProc: {chunk_open_proceeds:10.4f} | "
                f"CloseCost: {chunk_close_cost:10.4f} | "
                f"RawGain: {raw_gain:10.4f} | "
                f"AllowedGain: {allowed_gain:10.4f}{wash_tag}"
            )
        rg_trace = []
        if run.trace:
            rg_trace = list(symbol_traces[symbol])
            symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} (CONT...) ---"]

        # Signed cash-flow convention for SHORT — the SAME
        # field order as the Canada engine (FUZZ #E: the
        # engines had premium/buyback in OPPOSITE fields,
        # and USA rows broke gain == proceeds - cost):
        # cost = -opening premium (cash IN at open),
        # proceeds = -close cost (cash OUT at close), so
        # proceeds - cost = signed gain, direction-free.
        # Old line: cost is the cash
        # outflow on the closing buy (positive number stored as
        # negative so downstream tools can compute
        # proceeds-cost=signed gain regardless of direction).
        # Matches the Canada engine's SHORT signing.
        entry = _us_gain_entry(
            chunk_qty=chunk_qty,
            chunk_cost=-chunk_open_proceeds,
            chunk_proceeds=-chunk_close_cost,
            raw_gain=raw_gain,
            allowed_gain=allowed_gain,
            disallowed_amt=disallowed_amt,
            permanently_disallowed_amt=permanently_disallowed_amt,
            replacement_ids=replacement_ids,
            wash_reps=wash_reps,
            days_held=days_held,
            is_long_term=is_long_term,
            direction='SHORT',
            rg_trace=rg_trace,
            tainted=(short_lot.get('tainted', False)
                     or option_tainted),
            unknown_from=short_lot.get('unknown_from'),
            raw_acq_date=short_lot['date'],
            deferred=chunk_wd_d + D(option_dw * M.of(chunk_qty
                                                 / tx_qty_abs)),
            is_option_sym=is_option_symbol(symbol), symbol=symbol, tx=tx,
            tx_qty_abs=tx_qty_abs, ov_share=ov_share)
        # A short sale closed at a LOSS is realized when the
        # shares are delivered to close it (Reg. §1.1233-1
        # (a)(1); Rev. Rul. 2002-44, Situation 1): a cover
        # traded in December and settled in January is a loss
        # of January's year. One closed at a gain stays on
        # its trade date (§1259, Situation 2). The trade date
        # is kept in `trade_date` (US-DATE-01; TJ-0953). A
        # written option delivers nothing to close: its loss
        # stays on the closing trade (§1234(b)(1), TJ-1160).
        _loss_day = tx.date
        _dlv = tx.date_settle or tx.date
        if (raw_gain < -_US_MONEY_EPSILON and not is_option_assign
                and not is_option_symbol(symbol)
                and _dlv[:4] > tx.date[:4]):
            entry['trade_date'] = tx.date
            entry['date'] = _dlv
            _loss_day = _dlv
        if _cap_263h:
            entry['capitalized_263h'] = M.present(_cap_263h, 4)
        run.realized_gains.append(_us_with_delivery(run, tx, entry))
        _us_record_left_shares(run, short_lot, chunk_qty, tx, entry=entry, short=True)
        if run._comp_building:
            run._short_cover_chunks.append(
                (entry, tx.id, short_lot.get('id')))

        total_disallowed = disallowed_amt + permanently_disallowed_amt
        if total_disallowed > _US_MONEY_EPSILON:
            run.wash_sale_records.append({
                'loss_tx_id': tx.id,
                'symbol': symbol,
                'date': _loss_day,
                # Settlement date too: the --year filter keys on
                # tax_date; without this the record fell back to
                # trade date while the gain entries used settle,
                # splitting total_disallowed and its rows across
                # two years.
                'date_settle': tx.date_settle or tx.date,
                'disallowed_amount': total_disallowed,
                'permanently_disallowed': permanently_disallowed_amt,
                'qty': chunk_qty,
                'replacement_lot_ids': replacement_ids,
                'direction': 'SHORT',
            })
            _us_note_rules(run, tx.id, wash_reps, short=True)
        qty_remaining -= chunk_qty

    _us_run_retests(run, hold=delivered)
    # --- buy-to-open: any leftover quantity opens a new long lot ---
    if (run._dust_noise(tx_qty_abs) < qty_remaining <= run.epsilon
            and not run.inventory_short[ikey]):
        run._us_dust_dropped.append((symbol, qty_remaining))
    if qty_remaining > run.epsilon:
        rep_record = next(
            (r for r in _us_ix(run, run.long_replacements.get(
                _us_rkey_on(run, symbol, tx.date), [])).of_tx(tx.id)
             if not r['is_sheltered']),
            None,
        )
        leftover_share = qty_remaining / tx_qty_abs
        # The plain cost of the shares opened (the option-
        # assignment premium share included; for a short put
        # assignment the premium received reduces the cost of
        # the stock acquired at strike, Pub 550 — stored as
        # -gain, Canada convention, so adding it yields cost -
        # premium). The buy-to-close branch already consumed
        # its share of the premium above.
        base_cost_d = D(leftover_share) * D(tx_net)
        if option_adj != 0:
            base_cost_d += D(option_adj * M.of(leftover_share))
        _odw = D(option_dw * M.of(leftover_share))
        # A replacement matched BEFORE its own buy (the loss
        # preceded it): only the matched shares carry the
        # tacked holding period and the deferred basis, one
        # block per matched loss in match order (audit
        # A2-0060); the rest of the buy is an ordinary lot
        # behind them in FIFO order and becomes the rep's
        # lot_ref for later matches.
        _pend = (list(rep_record.get('pending_matches') or [])
                 if rep_record is not None else [])
        _blocks: List[Tuple[float, Decimal, str]] = []          # (qty, bump, effective date)
        _left = qty_remaining
        for _pq, _pb, _pe in _pend:
            if _left <= run.epsilon:
                # Sub-epsilon overshoot: fold the bump into
                # the last block so no deferral is lost.
                if _blocks:
                    _q0, _b0, _e0 = _blocks[-1]
                    _blocks[-1] = (_q0, _b0 + _pb, _e0)
                continue
            _bq = min(_pq, _left)
            if _left - _bq <= run.epsilon:
                _bq = _left
            _blocks.append((_bq, _pb, min(_pe, tx.date)))
            _left -= _bq
        if not _blocks:
            _blocks.append((qty_remaining, Decimal(0), tx.date))
            _left = 0.0
        if is_opening_row(tx):
            # An opening lot (US-OPEN-01): its basis is the
            # report's cost and its holding period runs from
            # its own acquisition date (§1223), not the
            # snapshot day; FIFO places it by that date.
            if not tx.lot_date:
                raise ValueError(
                    f"{symbol}: the opening balance dated "
                    f"{tx.date} in {tx.account} has no lot "
                    f"date — a US lot's holding period "
                    f"needs its purchase date (one OPENING "
                    f"line per lot, US-OPEN-01).")
            _us_insert_lots(run, ikey, [{
                'qty': qty_remaining,
                'cost_basis': base_cost_d,
                'wash_deferred': Decimal(0),
                'date': tx.lot_date,
                'effective_acq_date': tx.lot_date,
                'id': tx.id,
                **({'tainted': True} if option_tainted
                   else {}),
                **({'unknown_from': option_source} if option_source else {}),
            }])
            if run.trace:
                symbol_traces[symbol].append(
                    f"# {tx.date} OPENING   {qty_remaining:10.4f} | "
                    f"Lot_Cost: {M.of(base_cost_d):10.4f} | "
                    f"Acquired: {tx.lot_date}")
            return
        _new_lots: List[_USLot] = []
        _alloc = Decimal(0)
        for _bq, _pb, _pe in _blocks:
            _c = base_cost_d * D(_bq) / D(qty_remaining)
            _alloc += _c
            _new_lots.append({
                'qty': _bq,
                'cost_basis': _c + _pb,
                'wash_deferred': (_pb + _odw * D(_bq)
                                  / D(qty_remaining)),
                'date': tx.date,
                # When the lot is the holder of record's
                # (a 'dist' ADJUST, US-DIST-01).
                'date_settle': tx.date_settle or tx.date,
                'effective_acq_date': _pe,
                'id': tx.id,
                **({'tainted': True} if option_tainted
                   else {}),
                **({'unknown_from': option_source} if option_source else {}),
            })
        if _left > run.epsilon:
            _new_lots.append({
                'qty': _left,
                'cost_basis': base_cost_d - _alloc,
                'wash_deferred': (_odw * D(_left)
                                  / D(qty_remaining)),
                'date': tx.date,
                'date_settle': tx.date_settle or tx.date,
                'effective_acq_date': tx.date,
                'id': tx.id,
                **({'tainted': True} if option_tainted
                   else {}),
                **({'unknown_from': option_source} if option_source else {}),
            })
        else:
            # The last block absorbs the division remainder.
            _new_lots[-1]['cost_basis'] += base_cost_d - _alloc
        run.inventory_long[ikey].extend(_new_lots)
        lot = _new_lots[0]
        if rep_record is not None:
            # The unmatched remainder (or, fully matched,
            # the last block) answers any later match.
            _us_set_ref(run, rep_record, 'lot_ref', _new_lots[-1])
            rep_record['pending_basis_add'] = Decimal(0)
            rep_record['pending_matches'] = []
        # (the exercised replacement units' shares: TJ-2737, TJ-3430)
        _us_name_delivered_lots(run, ikey, _new_lots, tx, _taken.chunks)
        if run.trace:
            symbol_traces[symbol].append(
                f"# {tx.date} BUY-OPEN  {qty_remaining:10.4f} @ {M.shown(tx.price, 4):10.4f} | "
                f"Lot_Cost: {M.of(lot['cost_basis']):10.4f} | "
                f"Effective_Acq: {lot['effective_acq_date']}"
            )


def _us_walk_sell(*, ikey: _IKey, is_option_assign: bool, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction, tx_net: Decimal, tx_qty_abs: float,
                     underlying_for_assign: Optional[str]) -> None:
    """A sale row, the buy's mirror: the pending option-assignment premium taken once; a
    sell-to-close pops the long lots FIFO, each chunk's gain or loss, holding period and
    wash match against the replacement index booked (the disallowed amount into the
    replacement lot's basis), the call-option note said once; what remains opens a
    short lot (its replacement capacity, the timeline)."""
    # ============================================================
    # SELL path: first close any existing longs FIFO; if leftover
    # quantity remains, open a new short lot.
    # ============================================================
    qty_remaining = tx_qty_abs

    # Pull any pending option-assignment premium for this stock symbol.
    # For a short call assignment, the premium increases the proceeds
    # of the stock sold at strike. Stored as -gain (Canada convention)
    # — subtracting it from per-chunk proceeds yields proceeds + gain.
    # Same marked-leg gate as the BUY path above.
    _taken = _us_take_option_adj(run, tx)
    option_adj_sell, option_dw_sell = _taken.amount, _taken.deferred
    option_tainted_sell = _taken.tainted
    _osrc = _taken.sources
    option_source_sell = (dict(_osrc[0]) if option_tainted_sell and _osrc
                          else None)
    _call_said = False
    if not is_option_assign:
        _us_plan_disposition(run, run.inventory_long, ikey, tx,
                          'lot_ref', 'cost_basis')

    # --- sell-to-close: pop long lots FIFO ---
    while qty_remaining > run.epsilon and run.inventory_long[ikey]:
        lot = run.inventory_long[ikey][0]
        if lot['qty'] <= qty_remaining + run.epsilon:
            if lot['qty'] - qty_remaining > run._dust_noise(lot['qty']):
                run._us_dust_absorbed.append(
                    (symbol, lot['qty'] - qty_remaining))
            chunk_qty = lot['qty']
            chunk_cost_d = lot['cost_basis']
            chunk_wd_d = lot.get('wash_deferred', Decimal(0))
            run.inventory_long[ikey].pop(0)
            # As on the short side: a long_replacement rep that
            # pointed at this sold lot loses its lot reference and
            # its capacity, so it never washes a later loss
            # (US-WASH-21, re-audit A2-0817).
            for r in _us_ix(run, run.long_replacements.get(
                    _us_rkey_on(run, symbol, tx.date), [])).holding(
                        'lot_ref', lot):
                _us_set_ref(run, r, 'lot_ref', None)
                # Sold shares can no longer serve as §1091
                # replacement (FUZZ #A): stale capacity made
                # later losses match DEAD lots and land in the
                # permanent-denial branch, destroying real
                # losses (economic 0 reported as +4000 on a
                # 20-cycle ladder).
                r['remaining_qty'] = 0.0
        else:
            chunk_qty = qty_remaining
            chunk_cost_d = (D(chunk_qty) / D(lot['qty'])) * lot['cost_basis']
            _wd = lot.get('wash_deferred', Decimal(0))
            chunk_wd_d = ((D(chunk_qty) / D(lot['qty'])) * _wd
                          if _wd else Decimal(0))
            if _wd:
                lot['wash_deferred'] = _wd - chunk_wd_d
            lot['qty'] -= chunk_qty
            lot['cost_basis'] -= chunk_cost_d
            for r in _us_ix(run, run.long_replacements.get(
                    _us_rkey_on(run, symbol, tx.date), [])).holding(
                        'lot_ref', lot):
                # Same unit conversion as the short side
                # above: consume the rep's capacity in ITS
                # trade-date units, not today's.
                _cuf = _us_rep_units_factor(run,
                    symbol, r['date'], tx.date)
                r['remaining_qty'] = max(
                    0.0, r['remaining_qty']
                    - chunk_qty / (_cuf or 1.0))
        # A return of capital took what the basis no longer
        # holds of the §1091(d) add (TJ-1017): reported apart, as
        # the basis was also short by any premium it held
        # (TJ-1217).
        chunk_wd_cut = max(Decimal(0), chunk_wd_d - max(
            Decimal(0), chunk_cost_d))
        if chunk_wd_cut > D('0.000001'):
            chunk_wd_d -= chunk_wd_cut
        else:
            chunk_wd_cut = Decimal(0)
        chunk_cost = M.of(chunk_cost_d)
        chunk_proceeds = M.of(chunk_qty / tx_qty_abs) * tx_net
        # Apportion any option-assignment premium to this chunk's
        # proceeds (short-call assignment case: premium boosts proceeds).
        if option_adj_sell != 0:
            chunk_proceeds -= option_adj_sell * M.of(chunk_qty / tx_qty_abs)
        raw_gain = chunk_proceeds - chunk_cost

        if is_option_assign:
            assert underlying_for_assign is not None, "an option assign has its underlying (_us_walk_rows)"
            # Long option exercised: roll premium into underlying.
            # (The exercised units stay acquired replacements: TJ-2737.)
            run.pending_option_adjustments.stage(
                tx, underlying_for_assign, -raw_gain,
                M.of(chunk_wd_d), bool(lot.get('tainted')),
                sources=[lot['unknown_from']] if lot.get('unknown_from') else None,
                chunk=_us_record_left_shares(run, lot, chunk_qty, tx))
            if run.trace:
                symbol_traces[symbol].append(
                    f"# {tx.date} ASSIGN-EXERCISE {chunk_qty:10.4f} | "
                    f"Cost: {chunk_cost:10.4f} Proc: {chunk_proceeds:10.4f} → "
                    f"premium rolled into {underlying_for_assign} basis "
                    f"(would-be gain {raw_gain:.4f})"
                )
            qty_remaining -= chunk_qty
            continue

        disallowed_amt = _ZERO
        permanently_disallowed_amt = _ZERO
        replacement_ids: List[RowId] = []
        wash_reps: List[_RepEntry] = []
        ov_share = _us_ov_share(run, tx)

        # Tainted long lots (basis 0) can't normally lose, but gate
        # symmetrically with the short side / Canada engine.
        if (raw_gain < -_US_MONEY_EPSILON and run.detect_wash_sales
                and not lot.get('tainted')):
            remaining_loss_qty = chunk_qty
            chunk_loss = abs(raw_gain)
            loss_per_share_d = D(chunk_loss) / D(chunk_qty)
            _reps_here = run.long_replacements.get(
                _us_rkey_on(run, symbol, tx.date), [])
            _held = _us_find_replacements_in_window(run,
                _reps_here, tx.date)
            candidates: Sequence[_WashCand] = _held
            if _us_outside_1091(run, symbol):
                if _held:
                    _us_flag_futures_loss(run, tx, raw_gain, _held)
                candidates = []
            else:
                # Replacements another taxable account bought
                # and sold before this loss (US-WASH-22), in the
                # order acquired; a purchase's still-held shares
                # match before its sold ones.
                candidates = _us_with_sold_replacements(
                    run, candidates, _reps_here, tx)
            _ov_on = _us_ov_loss(run, tx, chunk_qty, chunk_loss, ov_share)
            for rep in candidates:
                if remaining_loss_qty <= run.epsilon:
                    break
                if isinstance(rep, tuple):
                    match_qty, _wr = _us_match_sold_replacement(
                        run, rep, remaining_loss_qty, loss_per_share_d, tx,
                        lot, _ov_on, ov_share)
                    remaining_loss_qty -= match_qty
                    if _wr is not None:
                        disallowed_amt += _wr['basis_bump']
                        replacement_ids.append(_wr['tx_id'])
                        wash_reps.append(_wr)
                    continue
                # Convert the rep's own-units quantity into loss-date
                # units for matching; consume in rep units.
                _uf = _us_rep_units_factor(run, symbol, rep['date'], tx.date)
                match_qty = min(remaining_loss_qty,
                                rep['remaining_qty'] * _uf)
                match_disallowed_d = D(match_qty) * loss_per_share_d
                match_disallowed = M.of(match_disallowed_d)
                rep['remaining_qty'] -= match_qty / _uf
                remaining_loss_qty -= match_qty
                if _ov_on:
                    assert ov_share is not None, "_us_ov_loss turns the overlap on only with a share"
                    _us_ov_take(run, tx, rep['tx'], rep['is_sheltered'],
                             rep.get('is_affiliated'), match_qty,
                             match_disallowed, ov_share)
                    continue

                _wr = _us_apply_held_long(run, rep, match_qty, _uf,
                                          match_disallowed_d, tx.date, lot)
                if _wr['is_sheltered'] or _wr['is_affiliated']:
                    permanently_disallowed_amt += match_disallowed
                else:
                    disallowed_amt += match_disallowed
                replacement_ids.append(rep['tx'].id)
                wash_reps.append(_wr)

        acq_for_holding = cast("str", lot.get('effective_acq_date', lot['date']))
        is_long_term = held_more_than_one_year(acq_for_holding, tx.date)
        try:
            days_held = (datetime.strptime(tx.date, '%Y-%m-%d') -
                         datetime.strptime(acq_for_holding, '%Y-%m-%d')).days
        except ValueError:
            days_held = 0

        allowed_gain = raw_gain + disallowed_amt + permanently_disallowed_amt

        if run.trace:
            wash_tag = ''
            if disallowed_amt > 0 or permanently_disallowed_amt > 0:
                wash_tag = f" [WASH disallowed={disallowed_amt + permanently_disallowed_amt:.4f}]"
            symbol_traces[symbol].append(
                f"# {tx.date} SELL-CLOSE {chunk_qty:10.4f} | "
                f"Against Lot: {lot['date']} (eff {acq_for_holding}) | "
                f"Cost: {chunk_cost:10.4f} | Proceeds: {chunk_proceeds:10.4f} | "
                f"RawGain: {raw_gain:10.4f} | AllowedGain: {allowed_gain:10.4f}{wash_tag}"
            )
        rg_trace = []
        if run.trace:
            rg_trace = list(symbol_traces[symbol])
            symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} (CONT...) ---"]

        entry = _us_gain_entry(
            chunk_qty=chunk_qty,
            chunk_cost=chunk_cost,
            chunk_proceeds=chunk_proceeds,
            raw_gain=raw_gain,
            allowed_gain=allowed_gain,
            disallowed_amt=disallowed_amt,
            permanently_disallowed_amt=permanently_disallowed_amt,
            replacement_ids=replacement_ids,
            wash_reps=wash_reps,
            days_held=days_held,
            is_long_term=is_long_term,
            direction='LONG',
            rg_trace=rg_trace,
            raw_acq_date=lot['date'],
            tainted=(lot.get('tainted', False)
                     or option_tainted_sell),
            unknown_from=lot.get('unknown_from'),
            deferred=chunk_wd_d + D(option_dw_sell * M.of(chunk_qty
                                                      / tx_qty_abs)),
            is_option_sym=is_option_symbol(symbol), symbol=symbol, tx=tx,
            tx_qty_abs=tx_qty_abs, ov_share=ov_share)
        if lot.get('tainted') and lot.get('moved_from') is not None:
            # A lot an own move brought, its basis in the
            # blended pass (backlog TJ-0279).
            entry['own_move_from'] = [lot['moved_from']]
        if chunk_wd_cut:
            entry['deferred_loss_cut'] = M.of(chunk_wd_cut)
            if option_adj_sell < 0 and not _call_said:
                # A written call's premium in the proceeds was in
                # no basis: the cut does not cover it. The whole
                # sale's, on one row: the views add its rows up
                # (TJ-1272).
                entry['premium_after_cut'] = M.of(-option_adj_sell)
                _call_said = True
        run.realized_gains.append(entry)
        _us_record_sold_replacement(run, lot, entry, chunk_qty, tx)

        total_disallowed = disallowed_amt + permanently_disallowed_amt
        if total_disallowed > _US_MONEY_EPSILON:
            run.wash_sale_records.append({
                'loss_tx_id': tx.id,
                'symbol': symbol,
                'date': tx.date,
                'date_settle': tx.date_settle or tx.date,  # see short-side note
                'disallowed_amount': total_disallowed,
                'permanently_disallowed': permanently_disallowed_amt,
                'qty': chunk_qty,
                'replacement_lot_ids': replacement_ids,
                'direction': 'LONG',
            })
            _us_note_rules(run, tx.id, wash_reps, short=False)
        qty_remaining -= chunk_qty

    _us_run_retests(run)
    # --- sell-to-open: any leftover quantity opens a new short lot ---
    if (run._dust_noise(tx_qty_abs) < qty_remaining <= run.epsilon
            and not run.inventory_long[ikey]):
        run._us_dust_dropped.append((symbol, -qty_remaining))
    if qty_remaining > run.epsilon:
        rep_record = next(
            (r for r in _us_ix(run, run.short_replacements.get(
                _us_rkey_on(run, symbol, tx.date), [])).of_tx(tx.id)
             if not r['is_sheltered']),
            None,
        )
        leftover_share = qty_remaining / tx_qty_abs
        leftover_proceeds_d = D(leftover_share) * D(tx_net)
        # Apportion the remaining option-assignment premium to
        # the leftover short open (mirror of the close-long
        # application above so the full premium is consumed).
        # Short-call assignment: premium boosts the opening
        # proceeds of the resulting short. Without this the
        # leftover share was silently discarded.
        if option_adj_sell != 0:
            leftover_proceeds_d -= D(option_adj_sell * M.of(leftover_share))
        if rep_record is not None:
            # Wash-sale carryover: pending proceeds reduction baked in.
            leftover_proceeds_d -= rep_record['pending_proceeds_reduction']
        short_lot: _USLot = {
            'qty': qty_remaining,
            'proceeds': leftover_proceeds_d,
            'date': tx.date,
            'effective_open_date': tx.date,
            'id': tx.id,
            # Deferral already baked into leftover_proceeds_d —
            # surfaced so inventory reports show it.
            'wash_deferred': (rep_record['pending_proceeds_reduction']
                              if rep_record is not None
                              else Decimal(0))
            + D(option_dw_sell * M.of(leftover_share)),
            **({'tainted': True} if option_tainted_sell else {}),
            **({'unknown_from': option_source_sell}
               if option_source_sell else {}),
        }
        run.inventory_short[ikey].append(short_lot)
        if rep_record is not None:
            _us_set_ref(run, rep_record, 'short_lot_ref', short_lot)
            # Share for share (audit A2-0206): only the matched
            # shorts carry the reduction; the rest is an
            # ordinary short behind them in FIFO order.
            _smatched = (rep_record.get('open_qty', 0.0)
                         - rep_record['remaining_qty'])
            if (rep_record['pending_proceeds_reduction']
                    and run.epsilon < _smatched
                    < qty_remaining - run.epsilon):
                _sfr = D(_smatched) / D(qty_remaining)
                _plain_d = leftover_proceeds_d + \
                    rep_record['pending_proceeds_reduction']
                _srem = dict(short_lot)
                _srem['qty'] = qty_remaining - _smatched
                _srem['proceeds'] = _plain_d * (1 - _sfr)
                _srem['wash_deferred'] = Decimal(0)
                short_lot['qty'] = _smatched
                short_lot['proceeds'] = (leftover_proceeds_d
                                         - _srem['proceeds'])
                run.inventory_short[ikey].append(_srem)
                _us_set_ref(run, rep_record, 'short_lot_ref', (
                    _srem if rep_record['remaining_qty'] > run.epsilon
                    else short_lot))
            rep_record['pending_proceeds_reduction'] = Decimal(0)
        if run.trace:
            symbol_traces[symbol].append(
                f"# {tx.date} SELL-OPEN {qty_remaining:10.4f} @ {M.shown(tx.price, 4):10.4f} | "
                f"Short_Proc: {M.of(short_lot['proceeds']):10.4f} | "
                f"Effective_Open: {short_lot['effective_open_date']}"
            )


def _us_gain_entry(*, chunk_qty: float, chunk_cost: Decimal, chunk_proceeds: Decimal, raw_gain: Decimal,
                      allowed_gain: Decimal, disallowed_amt: Decimal, permanently_disallowed_amt: Decimal,
                      replacement_ids: List[RowId], wash_reps: List[_RepEntry], days_held: int,
                      is_long_term: bool, direction: str, rg_trace: List[str], is_option_sym: bool,
                      symbol: str, tx: TaxTransaction, tx_qty_abs: float, tainted: bool = False,
                      raw_acq_date: str = '', deferred: Decimal = Decimal(0),
                      ov_share: Optional[List[Decimal]] = None,
                      unknown_from: Optional[Dict[str, Any]] = None) -> _GainRow:
    """One realized-gain entry of the US walk, a chunk of a sale or a cover: the
    fees' share, the wash and holding-period fields, the per-row facts of its
    row, which the buy and the sale hand it (step 4n's reviews, TJ-2006,
    TJ-2012). `ov_share`: on a row a filing position names, the chunk's own
    [disallowed, permanent] of the denial the rule would have made, left on
    the row for lib/loss_overrides' stamp_entries, which takes it off again
    (TJ-3095)."""
    fee_share = chunk_qty / tx_qty_abs if tx_qty_abs > 0 else 1.0
    total_disallowed = disallowed_amt + permanently_disallowed_amt
    entry: _GainRow = {
        'date': tx.date,
        'date_settle': tx.date_settle or tx.date,
        'symbol': symbol,
        'qty': chunk_qty,
        'cost': chunk_cost,
        'proceeds': chunk_proceeds,
        'gain': allowed_gain,
        'raw_gain': raw_gain,
        'disallowed_amount': total_disallowed,
        'permanently_disallowed': permanently_disallowed_amt,
        'replacement_lot_ids': replacement_ids,
        'days_held': max(0, days_held),
        # The lot's ACTUAL purchase/open date. Form 8949
        # column (b) wants this (brokers report the real
        # date and adjust term separately); days_held stays
        # tacked for the term computation.
        'acquired_date': raw_acq_date,
        'account': tx.account,
        'currency': tx.currency,
        'commission': M.of(tx.commission or 0) * M.of(fee_share),
        'fee': M.of(tx.fee or 0) * M.of(fee_share),
        'is_wash_sale': total_disallowed > _US_MONEY_EPSILON,
        'is_option': is_option_sym,
        'id': tx.id,
        'trace': rg_trace,
        'direction': direction,
        'term': 'LONG_TERM' if is_long_term else 'SHORT_TERM',
        # Schema symmetry with CanadaTaxRules — these are
        # Canada-side concepts; US emits None so downstream
        # consumers can read either engine's output with
        # .get() and the same field set.
        'wash_trigger': None,
        'wash_window': None,
        'wash_replacements': wash_reps if wash_reps else None,
        # True when the consumed lot was a missing-history
        # OPENING_BALANCE (cost basis = 0 from synthesized
        # `--incomplete-history` rows). The CLI's tainted-
        # split path routes these to manual_reporting_required
        # — same end-state as Canada's tainted-pool model.
        'tainted': bool(tainted),
    }
    if tainted and unknown_from:
        # The OPENING cost=unknown row the lot came by (TJ-3209).
        entry['unknown_cost_from'] = [dict(unknown_from)]
    if deferred and M.of(deferred) > _NANO:
        # The earlier wash-sale losses (§1091(d) basis adds)
        # in this chunk's basis — a short's: off its opening
        # proceeds — the lot's wash_deferred, so `raw_gain`
        # + this is its P/L before any denial (US-RPT-12;
        # audit 2026-10-25 planning-us#1).
        entry['deferred_loss_recovered'] = M.of(deferred)
    if ov_share is not None:
        entry[_LO.ROW_DISALLOWED], entry[_LO.ROW_PERMANENT] = ov_share
    return entry


def _us_deliver_to_short(run: _USRun, row: TaxTransaction, kb: _IKey, drawn: List[_USLot],
                         f: Optional[Decimal], symbol_traces: Dict[str, List[str]],
                         tax_books: bool = False) -> Tuple[float, List[_GainRow]]:
    """Long lots delivered to the short that pool `kb` holds (`drawn`:
    lots taken off their own pool, FIFO; `row`: the in-leg that
    brings them, whose id and date the covers carry): each lot
    covers the short as a purchase would, at its own basis restated
    by `f` — the cover's cost, its wash-sale deferral with it; an
    unknown basis (no rate, an unknown-cost lot) makes the cover's
    unknown too. A listing journal's (the native books, TJ-2738), or,
    `tax_books`, an own-account move's (TJ-3424): then the cover's
    term follows the lot delivered (`_us_delivered_long`), and a lot's
    never-matched shares stay acquired replacements that left, the
    cover's row taking a later match's add (US-WASH-22; TJ-2700's
    ruling). Returns the units covered and the cover rows."""
    k = Decimal(1) if f is None else f
    got, rows = 0.0, []
    if run.trace and drawn:
        symbol_traces.setdefault(row.symbol, [
            f"# --- FIFO CALCULATION TRACE: {row.symbol} ---"])
    for c in drawn:
        if c['qty'] <= run.epsilon:
            continue
        tainted = f is None or bool(c.get('tainted'))
        cover = replace(row, quantity=c['qty'])
        before = {id(e) for e in run.realized_gains}
        _us_walk_buy(ikey=kb, is_option_assign=False, run=run,
                     symbol=row.symbol, symbol_traces=symbol_traces,
                     tx=cover, tx_qty_abs=c['qty'],
                     tx_net=_ZERO if tainted else M.of(c['cost_basis'] * k),
                     underlying_for_assign=None,
                     delivered={
                         'deferred': _ZERO if tainted else M.of(
                             c.get('wash_deferred', Decimal(0)) * k),
                         'tainted': tainted,
                         'source': (dict(c['unknown_from'])
                                    if c.get('unknown_from') else None),
                         'acq': (c.get('effective_acq_date', c['date'])
                                 if tax_books and not tainted else None)})
        got += c['qty']
        # This lot's cover rows, by identity: a wash match made while
        # they were booked can split an earlier row, whose head lands
        # before them in the list (backlog TJ-3459).
        new = [e for e in run.realized_gains if id(e) not in before
               and e.get('id') == cover.id and e.get('direction') == 'SHORT']
        rows += new
        for e in new if tax_books else ():
            e['closed_by_delivery'] = True
            if not tainted:
                # Form 8949's column (b) for a short sale closed by
                # delivery: the date the property delivered was
                # acquired; the days held read as the term does (a lot
                # held a year or less on the short-sale day starts
                # again at the close, §1233(b)(2)) (backlog TJ-3451).
                e['acquired_date'] = c['date']
                e['days_held'] = (_day_gap(cast("str", c.get('effective_acq_date',
                                                 c['date'])), cover.date) or 0
                                  ) if e.get('term') == 'LONG_TERM' else 0
            _us_record_left_shares(run, c, e['qty'], cover, entry=e)
    _us_run_retests(run)
    return got, rows

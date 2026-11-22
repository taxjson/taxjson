"""FIFO draws from a pool, and the lot timeline the trace shows.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, cast

from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.model.transaction import TaxTransaction

from taxjson.engine.us.lot_store import _lot_wash_held, _us_insert_lots
from taxjson.engine.us.terms import held_more_than_one_year
from taxjson.engine.us.wash import (
    _us_rkey_on, _us_rep_units_factor, _us_ix, _us_set_ref, _us_split_rep_refs,
    _us_record_left_shares,
)

if TYPE_CHECKING:
    from taxjson.engine.us.lot_store import _IKey, _USInventory, _USLot
    from taxjson.engine.us.run import _USRun


def _us_plan_draw(run: _USRun, inv: _USInventory, ikey_: _IKey, sym: str, q: float, on_date: str,
                  ref_key: str, amt_key: str) -> None:
    """Reserve the lots this disposition draws (FIFO, `q` units):
    the boundary lot is split so the KEPT units are their own lot
    (and the replacement record's ref follows them), and every
    replacement record whose lot is drawn loses that capacity
    now, before any of the disposition's losses is matched."""
    lots = inv.get(ikey_, [])
    reps = _us_ix(run, (run.long_replacements if ref_key == 'lot_ref'
                else run.short_replacements).get(_us_rkey_on(run, sym, on_date),
                                             []))
    left = q
    i = 0
    while left > run.epsilon and i < len(lots):
        lot = lots[i]
        take = min(lot['qty'], left)
        if lot['qty'] > take + run.epsilon:
            frac = D(take) / D(lot['qty'])
            keep = dict(lot)
            keep['qty'] = lot['qty'] - take
            keep[amt_key] = lot[amt_key] * (1 - frac)
            _wd = lot.get('wash_deferred', Decimal(0))
            keep['wash_deferred'] = _wd * (1 - frac)
            lot['qty'] = take
            lot[amt_key] = lot[amt_key] - keep[amt_key]
            lot['wash_deferred'] = _wd - keep['wash_deferred']
            lots.insert(i + 1, keep)
            for r in reps.holding(ref_key, lot):
                _us_set_ref(run, r, ref_key, keep)
            drawn = keep
        else:
            drawn = lot
        for r in reps.holding(ref_key, drawn):
            _cuf = _us_rep_units_factor(run, sym, r['date'], on_date)
            r['remaining_qty'] = max(
                0.0, r['remaining_qty'] - take / (_cuf or 1.0))
        left -= take
        i += 1

def _us_plan_disposition(run: _USRun, inv: _USInventory, ikey_: _IKey, tx_: TaxTransaction,
                         ref_key: str, amt_key: str) -> None:
    if not run.detect_wash_sales or tx_.id in run._disp_member:
        return
    _us_plan_draw(run, inv, ikey_, tx_.symbol,
               run._disp_total.get(tx_.id, abs(tx_.quantity)),
               tx_.date, ref_key, amt_key)

def _us_delivered_long(delivered: Optional[Dict[str, Any]], short_lot: _USLot) -> bool:
    """The term of a short sale closed by delivering a lot held
    (`delivered['acq']`, the lot's holding-period start; an own-account
    move, backlog TJ-3424): long-term only when the lot was held more
    than one year on the day of the short sale. Then §1233(b) does
    not reach a gain, which takes the delivered property's holding
    period (Reg. §1.1233-1(a)(3)), and a loss is long-term (§1233(d)).
    A lot held a year or less that day, or bought after it, makes a
    gain short-term (§1233(b)(1)) and starts its own holding period
    again at the close (§1233(b)(2)), so a loss is short-term too.
    Only the delivered lot is looked at, not other holdings of the
    security. Any other cover is short-term, as before."""
    acq = (delivered or {}).get('acq')
    if not acq:
        return False
    return held_more_than_one_year(acq, short_lot['date'])

def _us_move_long_lots(run: _USRun, src: _IKey, dst: Optional[_IKey], sym: str, q: float,
                       on_date: str) -> float:
    """Hand `q` units of `src`'s FIFO lots (the lot objects
    themselves: basis, purchase dates, wash-sale deferrals and
    replacement links travel with them; a lot split here gives
    its moved part its share of the links, TJ-0490) to `dst`.
    Returns the units moved."""
    lots = run.inventory_long.get(src, [])
    moved = []
    left = q
    while left > run.epsilon and lots:
        lot = lots[0]
        if lot['qty'] <= left + run.epsilon:
            moved.append(lots.pop(0))
            left -= lot['qty']
            continue
        frac = D(left) / D(lot['qty'])
        part = dict(lot)
        part['qty'] = left
        part['cost_basis'] = lot['cost_basis'] * frac
        _wd = lot.get('wash_deferred', Decimal(0))
        part['wash_deferred'] = _wd * frac
        lot['qty'] -= left
        lot['cost_basis'] -= part['cost_basis']
        lot['wash_deferred'] = _wd - part['wash_deferred']
        _us_split_rep_refs(run, sym, on_date, lot, part, 'lot_ref')
        moved.append(part)
        left = 0.0
    if moved and dst is not None:
        _us_insert_lots(run, dst, moved)
    return cast("float", sum(l['qty'] for l in moved))

def _us_draw_long_lots(run: _USRun, ikey_: _IKey, sym: str, q: float, on_date: str,
                       tx: Optional[TaxTransaction] = None) -> List[_USLot]:
    """Take `q` units off the front of a long pool (FIFO) with no
    disposition row: [(units, lot fields)] — the boundary lot is
    split. Replacement records lose the drawn shares' capacity,
    as on a sale; given the row `tx` that draws them (a §356
    exchange), the drawn shares stay acquired replacements that
    left (US-WASH-06, US-WASH-22; TJ-2700): each chunk's record
    rides on it (`_rep_chunk`) for the caller to name the lots
    they became."""
    out = []
    lots = run.inventory_long.get(ikey_, [])
    reps = _us_ix(run, run.long_replacements.get(_us_rkey_on(run, sym, on_date), []))
    left = q
    while left > run.epsilon and lots:
        lot = lots[0]
        if lot['qty'] <= left + run.epsilon:
            lots.pop(0)
            whole = dict(lot)
            out.append(whole)
            left -= lot['qty']
            for r in reps.holding('lot_ref', lot):
                _us_set_ref(run, r, 'lot_ref', None)
                r['remaining_qty'] = 0.0
            if tx is not None:
                whole['_rep_chunk'] = _us_record_left_shares(
                    run, lot, whole['qty'], tx, tack=True)
            continue
        frac = D(left) / D(lot['qty'])
        part = dict(lot)
        part['qty'] = left
        part['cost_basis'] = lot['cost_basis'] * frac
        _wd = lot.get('wash_deferred', Decimal(0))
        part['wash_deferred'] = _wd * frac
        lot['qty'] -= left
        lot['cost_basis'] -= part['cost_basis']
        lot['wash_deferred'] = _wd - part['wash_deferred']
        for r in reps.holding('lot_ref', lot):
            _cuf = _us_rep_units_factor(run, sym, r['date'], on_date)
            r['remaining_qty'] = max(
                0.0, r['remaining_qty'] - left / (_cuf or 1.0))
        if tx is not None:
            part['_rep_chunk'] = _us_record_left_shares(
                run, lot, left, tx, tack=True)
        out.append(part)
        left = 0.0
    return out

def _us_row_after(day: str, basis: Optional[str], *, get_sort_date: Callable[[TaxTransaction], str], taxable_sorted: Sequence[TaxTransaction]) -> Optional[TaxTransaction]:
    """The first row after the end of `day`: the lots are taken
    before it (None: no row after the day)."""
    _last = -1
    for _i, _t in enumerate(taxable_sorted):
        if str(get_sort_date(_t) or '')[:10] <= day:
            _last = _i
    # A tax_date = "settle" project (`year_end_basis`): its year
    # ends before the first row that SETTLES after year_end — a
    # sale traded Dec 31 that settles in January is a next-year
    # disposition, so the lots it sells (and the disallowed
    # losses in their basis) are still held at its year end
    # (backlog TJ-0343).
    if basis == 'settle':
        for _i, _t in enumerate(taxable_sorted[:_last + 1]):
            if str(_t.date_settle or _t.date or '')[:10] > day:
                _last = _i - 1
                break
    if _last + 1 < len(taxable_sorted):
        return taxable_sorted[_last + 1]
    return None

def _us_tl_label(run: _USRun, acct: str, sym: str) -> Tuple[str, str]:
    return (acct or '', sym) if run.per_account_basis else ('', sym)

def _us_tl_state(lab: Tuple[str, str], *, _ikey: Callable[[str, str], _IKey], inventory_long: _USInventory,
                 inventory_short: _USInventory, symbol_currency: Dict[str, str]
                 ) -> Optional[Tuple[float, Decimal, Decimal, bool, str, str, str]]:
    k = _ikey(lab[0], lab[1])
    lo = inventory_long.get(k) or []
    sh = inventory_short.get(k) or []
    if not lo and not sh:
        return None
    q = sum(l['qty'] for l in lo) - sum(l['qty'] for l in sh)
    cost = (M.of(sum(l['cost_basis'] for l in lo))
            - M.of(sum(l['proceeds'] for l in sh)))
    dw = M.of(sum(_lot_wash_held(l) for l in lo)
               + sum(l.get('wash_deferred', Decimal(0)) for l in sh))
    dates = [l['date'] for l in lo + sh]
    return (q, cost, dw, any(l.get('tainted') for l in lo + sh),
            symbol_currency.get(lab[1], ''), min(dates),
            max(dates))

def _us_tl_held(run: _USRun) -> Iterator[Tuple[str, str]]:
    for inv in (run.inventory_long, run.inventory_short):
        for k, lots in inv.items():
            if lots:
                yield ((k[0], k[1]) if run.per_account_basis
                       else ('', k))  # type: ignore[misc]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)

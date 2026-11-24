"""FIFO draws from a pool, and the lot timeline the trace shows.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, cast

from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.model.transaction import TaxTransaction

from taxjson.lib.stage_msg import YEAR_END_INEXACT, emit_line
from taxjson.engine.us.lot_store import _lot_wash_held, _us_insert_lots, _us_lots_copy
from taxjson.engine.us.terms import held_more_than_one_year
from taxjson.engine.us.wash import (
    _us_rkey_on, _us_rep_units_factor, _us_ix, _us_set_ref, _us_split_rep_refs,
    _us_record_left_shares,
)

if TYPE_CHECKING:
    from taxjson.engine.us.lot_store import _IKey, _USInventory, _USLot, _USLotsNow
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

def _us_lot_sig(lots: Sequence[_USLot]) -> List[Tuple[Tuple[str, Any], ...]]:
    """A pool's lots by their own plain fields (units, basis or proceeds,
    wash_deferred, dates), to tell whether a row changed them."""
    return [tuple(sorted((k, v) for k, v in lot.items()
                         if v is None or isinstance(v, (str, int, float, Decimal))))
            for lot in lots]


# The rows that change a position's lots (a dividend or a fee on it does not).
_LOT_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'OPENING_BALANCE', 'SPLIT', 'ADJUST')


class _USDayLots:
    """The lots at the end of a day (`year_end`, `lots_at`), read as the walk
    passes it: `at` before each row, `end` after the last. The copy is taken
    before `nxt` (_us_row_after: the first row after the day, on its date
    basis). On trade dates, the walk's own order, that is the whole of it. On
    settlement dates a row that settles after the day can stand before rows
    that settle by it (a December 29 sale settling in January, a December 30
    purchase settled that day): the one cut left every later in-year row out,
    silently (TJ-4984). So from `nxt` to the last row dated and settled by the
    day the lots are read pool by pool (an account's lots of one security,
    bought and written): a pool stands as it was before the first row
    settling after the day that touched it, a row settled by the day gives
    the pools it changed as they stand after it, and what a row changed is
    read off the lots themselves (a split, a rename, a spin-off's two pools
    and a wash sale's basis add are rows like any other). A row touches the
    pools it changed and its own (its account's lots of its symbol, and of
    the symbol it renames to: a split of a position the later-settling sale
    emptied changes no lot, and on settlement dates it would have). A row
    settled by the day that touches a pool a later-settling row touched
    cannot be read on settlement dates (the later row's lots are drawn
    already): none of the pools it touches takes it, they stay as they were,
    and one warning per account names them (once for a day: `said`, shared
    by the walk's two readers)."""

    __slots__ = ('lots', '_run', '_day', '_next', '_last', '_open', '_stay', '_prev', '_left', '_said')

    def __init__(self, run: _USRun, day: Optional[str], nxt: Optional[TaxTransaction],
                 said: set[Any]) -> None:
        self.lots: Optional[_USLotsNow] = None
        self._run, self._day, self._next, self._said = run, day, nxt, said
        self._last: Optional[TaxTransaction] = None      # the last row dated and settled by the day
        self._open = False
        self._stay: set[Any] = set()                     # the pools a later-settling row touched
        self._prev: Optional[Tuple[TaxTransaction, _USLotsNow]] = None
        self._left: Dict[Any, List[str]] = {}            # the crossed pools: their rows not taken
        if day and nxt is not None:
            _past = False
            for _t in run.taxable_sorted:
                _past = _past or _t is nxt
                if _past and self._in(_t):
                    self._last = _t

    def _in(self, tx: TaxTransaction) -> bool:
        return (str(self._run.get_sort_date(tx) or '')[:10] <= str(self._day)
                and str(tx.date_settle or tx.date or '')[:10] <= str(self._day))

    def at(self, tx: TaxTransaction) -> None:
        """Before `tx` is walked."""
        if self._open:
            self._settle()
        elif self.lots is None and self._day and tx is self._next:
            self.lots = _us_lots_copy(self._run)
            self._open = self._last is not None
        if self._open:
            self._prev = (tx, _us_lots_copy(self._run))

    def end(self) -> Optional[_USLotsNow]:
        """After the last row: the lots at the end of the day (None: no day)."""
        if self._open:
            self._settle()
        if self._day and self.lots is None:
            self.lots = _us_lots_copy(self._run)      # no row after the day
        return self.lots

    def _settle(self) -> None:
        """The row walked last: the pools it touched, into the day's lots or out of them."""
        assert self._prev is not None and self.lots is not None, "set when the window opens (at)"
        tx, before = self._prev
        run = self._run
        now = _us_lots_copy(run)
        touched = {k for b, a in zip(before, now) for k in set(b) | set(a)
                   if _us_lot_sig(b.get(k, [])) != _us_lot_sig(a.get(k, []))}
        if tx.action in _LOT_ACTIONS:
            touched |= {(tx.account, _s) if run.per_account_basis else _s
                        for _s in (tx.symbol, tx.symbol_new) if _s}
        if not self._in(tx):
            self._stay |= touched
        else:
            # The crossed accounts (per-account lots: only the account a
            # later-settling row touched; another account's lots of the
            # symbol are its own, exact) take none of the row.
            crossed = touched & self._stay
            whose = {k[0] for k in crossed} if run.per_account_basis else None
            for k in touched:
                if crossed and (whose is None or k[0] in whose):
                    self._left.setdefault(k, []).append(str(tx.date)[:10])
                    self._stay.add(k)
                    continue
                for side, a in zip(self.lots, now):
                    side[k] = a.get(k, [])
        if tx is self._last:
            self._open = False
            self._say()

    def _say(self) -> None:
        """One warning per account for the pools read across both date orders."""
        by_acct: Dict[str, Tuple[set[str], set[str]]] = {}
        for k, days in self._left.items():
            acct, sym = k if isinstance(k, tuple) else ('', k)
            syms, dates = by_acct.setdefault(acct, (set(), set()))
            syms.add(sym)
            dates.update(days)
        for acct, (syms, dates) in sorted(by_acct.items()):
            if (self._day, acct) in self._said:
                continue
            self._said.add((self._day, acct))
            _whose = "this account's position" if acct else "this position"
            emit_line(f"{YEAR_END_INEXACT}{acct + ': ' if acct else ''}{', '.join(sorted(syms))}: the lots "
                      f"at the end of {self._day} (by settlement date) are not exact\n"
                      f"  A row that settles after that day stands before {len(dates)} day(s) of rows settled "
                      f"by it ({', '.join(sorted(dates))}) in trade order, and both touch "
                      f"{_whose} (a trade, a split or a "
                      f"ticker change), so its lots are drawn in the other order. The year-end lots keep it "
                      f"as held before the first row settling after the day: the later rows are not in "
                      f"them.\n"
                      f"  Check these lots (`tjs edge-cases`, the closed year's record) against the "
                      f"broker's statement.")


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

"""The denial pass's row indexes: rows by date window, a class's balance rows,
the ADJUST rows by loss.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple
from datetime import datetime, timedelta

from taxjson.lib.row_types import LOT_MOVE_TYPE, OWN_MOVE_TYPE
from taxjson.lib.transfer_windows import _TRANSFER_SEGMENT_MAX_SPAN_DAYS
from taxjson.engine.rows import _iso_ordinal

from taxjson.engine.ca.run_helpers import _sl_holder, _sl_opening_qty

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.ca.holders import _CanadaRun, _SLPass
    from taxjson.engine.ca.run_helpers import _Holder


# The rows a wash ADJUST's placement may name (`WashPlace.after`;
# `_place_wash_adjusts` honours each by identity): the position changes of
# a pool.
_SL_WASH_AFTER_ACTIONS = ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE')


class WashPlace(NamedTuple):
    """Where a denied loss's addition (a virtual ADJUST) is applied, kept
    by the ADJUST's id in `run.wash_place` (TJ-4113): `after`, the row it
    is placed right after (`_place_wash_adjusts`: the trigger purchase, a
    pre-loss trigger's loss sale's last fill, or a landing row), and
    `landed`, the row is a landing row (`_sl_landing_row`), so the
    placement no longer follows the sale's last losing fill. An entry
    lives as long as its ADJUST: written whole when the row is made, and
    dropped with it when the denial is retracted (`_ca_solve`)."""
    after: RowId
    landed: bool


# The Canada solver's superficial-loss scans ask, for each loss sale,
# about the rows of ONE identity class inside ONE +/-30-day window.
# Walking every row of the book for every sale made a run cost loss
# sales x rows (TJ-0105: 50,000 rows took half an hour; TJ-0060: 8,000
# rows of one symbol took minutes). The indexes below hand each scan the
# same rows, in the same order, that the full walk kept, so every sum
# adds the same numbers in the same order and every result is unchanged.

class _DatedRows:
    """Rows of one key in their book order, found by date window.
    `date_of(row)` is the date the scan tests, an ISO date (the engines
    refuse any other up front: require_computable_rows, TJ-2702)."""

    __slots__ = ('rows', '_order', '_ords')

    def __init__(self, rows: List[TaxTransaction], date_of: Callable[[TaxTransaction], str]) -> None:
        self.rows = rows
        ords = [_iso_ordinal(date_of(t)) for t in rows]
        self._order = sorted(range(len(rows)), key=ords.__getitem__)
        self._ords = [ords[i] for i in self._order]

    def within(self, center: int, days: int) -> List[TaxTransaction]:
        """The rows dated `center` +/- `days` (inclusive), in book order."""
        import bisect
        lo = bisect.bisect_left(self._ords, center - days)
        hi = bisect.bisect_right(self._ords, center + days)
        return [self.rows[i] for i in sorted(self._order[lo:hi])]


def _own_move_held(loss_date: datetime, end_window_date: str, rows: List[Tuple[TaxTransaction, float]],
                   triggers: List[TaxTransaction], call_ids: Set[RowId],
                   gone_by_end: Callable[[TaxTransaction], bool],
                   units: Callable[[TaxTransaction, float], float], *, pas: _SLPass,
                   run: _CanadaRun) -> Optional[Dict[Any, float]]:
    """Per holder of the triggers (the account that ACQUIRED them), the
    units it acquired in a loss's window that one of the holders still
    holds at its end, when a move of your own between two of the
    accounts (balance-only TRANSFER rows of type `own_account_move`,
    lib/pipeline) lies in the window; else None (the caller's
    min(acquired, held) per holder stands — the same figure without
    such a move).

    The class's balance rows dated by day 30 are walked in date order,
    each holder's units in parts: older ones, and the ones acquired in
    the window (`triggers`, their opening units) by the holder that
    acquired them. A sale or a move out takes the older units first
    (FIFO); a move in takes the parts its out legs moved (the other
    account's leg of the same units, the closest date; else what is left
    of the other accounts' legs, pro rata over each out leg's parts) —
    so a replacement bought in one registered account and moved to
    another is still held (backlog TJ-0654, TJ-1102), and older units
    moved in back nothing. `rows`: (row, units) pairs in the loss's units
    (`_sl_identical_units`); `units(t, q)`: q of trigger t in those
    units; the holder and the opening quantity are the pass's
    (`_sl_holder`, `_sl_opening_qty`), the booking date the run's."""
    def _sd(t: TaxTransaction) -> str:
        return run.get_sort_date(t)

    def _own(t: TaxTransaction) -> bool:
        return (t.action == 'TRANSFER'
                and getattr(t, 'type', '') == OWN_MOVE_TYPE)

    def _leg(t: TaxTransaction) -> bool:
        # A leg of a move of your own: between two registered accounts,
        # or a taxable lot move (LOT_MOVE_TYPE, one holder: its parts
        # come back as they left).
        return _own(t) or (t.action == 'TRANSFER'
                           and getattr(t, 'type', '') == LOT_MOVE_TYPE)

    start = (loss_date - timedelta(days=30)).strftime('%Y-%m-%d')
    if not any(_own(t) and start <= _sd(t) <= end_window_date
               for t, _q in rows):
        return None
    trig = {t.id: units(t, _sl_opening_qty(pas, t)) for t in triggers
            if t.id not in call_ids}
    walk = sorted(((t, q) for t, q in rows
                   if _sd(t) <= end_window_date and not gone_by_end(t)),
                  key=lambda p: _sd(p[0]))
    old: Dict[_Holder, float] = {}
    # holder -> {the holder that acquired them: units acquired in the
    # window}
    new: Dict[_Holder, Dict[_Holder, float]] = {}
    # The legs paired before the walk: the same units first, the closest
    # date; then what is left, in parts (a move that posts in parts, or
    # gathers two senders' units in one leg: the pipeline nets a whole
    # segment that sums to zero, backlog TJ-1102), as close as one
    # segment's legs can be.
    legs = [t for t, _q in walk if _leg(t)]
    left = {id(t): abs(q) for t, q in walk if _leg(t)}
    links: Dict[int, List[Tuple[TaxTransaction, float]]] = {}

    def _gap(x: TaxTransaction, y: TaxTransaction) -> int:
        return abs((datetime.strptime(_sd(x), '%Y-%m-%d')
                    - datetime.strptime(_sd(y), '%Y-%m-%d')).days)

    def _pairs(same: bool) -> List[Tuple[TaxTransaction, TaxTransaction]]:
        found = []
        for n, o in enumerate(legs):
            for m, i in enumerate(legs):
                if (o.quantity < 0 < i.quantity and o.account != i.account
                        and getattr(o, 'type', '') == getattr(i, 'type', '')
                        and (not same or abs(left[id(o)] - left[id(i)])
                             <= 1e-9 * max(1.0, left[id(o)]))
                        and (same or _gap(o, i)
                             <= _TRANSFER_SEGMENT_MAX_SPAN_DAYS)):
                    found.append((_gap(o, i), n, m, o, i))
        return [(o, i) for *_rank, o, i in sorted(found, key=lambda e: e[:3])]

    o: Any      # a leg in the pairing, a holder in the walk below
    for same in (True, False):
        for o, i in _pairs(same):
            n = min(left[id(o)], left[id(i)])
            if n > 1e-9:
                left[id(o)] -= n
                left[id(i)] -= n
                links.setdefault(id(o), []).append((i, n))
                links.setdefault(id(i), []).append((o, n))
    landed: Set[int] = set()
    sent: Dict[Tuple[int, int], Dict[_Holder, float]] = {}

    def _add(h: _Holder, parts: Dict[_Holder, float]) -> None:
        mine = new.setdefault(h, {})
        for o, n in parts.items():
            mine[o] = mine.get(o, 0.0) + n
            old[h] = old.get(h, 0.0) - n

    for t, q in walk:
        h = _sl_holder(run, t)
        if t.id in trig:
            n = min(max(0.0, trig[t.id]), max(0.0, q))
            old[h] = old.get(h, 0.0) + q
            _add(h, {h: n})
            continue
        if q >= 0:
            old[h] = old.get(h, 0.0) + q
            if _leg(t):
                landed.add(id(t))
                for o, _n in links.get(id(t), ()):
                    _add(h, sent.pop((id(o), id(t)), {}))
            continue
        take = -q
        rest = take - min(take, max(0.0, old.get(h, 0.0)))
        moved: Dict[_Holder, float] = {}
        for o in sorted(new.get(h, {}), key=repr):
            if rest <= 0.0:
                break
            n = min(rest, max(0.0, new[h][o]))
            if n > 0.0:
                new[h][o] -= n
                moved[o] = n
                rest -= n
        old[h] = old.get(h, 0.0) - (take - sum(moved.values()))
        if _leg(t):
            for i, n in links.get(id(t), ()):
                share = {o: x * n / take for o, x in moved.items()}
                if id(i) in landed:
                    _add(_sl_holder(run, i), share)
                else:
                    sent[(id(t), id(i))] = share
    out: Dict[Any, float] = {}
    for h, parts in new.items():
        total = sum(max(0.0, n) for n in parts.values())
        # A holder short of its older units holds less than its parts.
        keep = max(0.0, min(total, old.get(h, 0.0) + total))
        for o, n in parts.items():
            if total > 0.0 and n > 0.0:
                out[o] = out.get(o, 0.0) + n * keep / total
    for t in triggers:
        out.setdefault(_sl_holder(run, t), 0.0)
    return out


class _ClassBalances:
    """One identity class's balance rows (BUYSELL, ASSIGN, TRANSFER,
    OPENING_BALANCE) in processing order, for the day-30 balance walks.

    `prefix(end)` is the number of leading rows whose sort date is on or
    before `end` — None when the rows are not in sort-date order (then
    the caller walks them all, as before). When the class is one raw
    symbol that no split or rename acts on, every row's quantity is
    already in the loss's units (the lineage factor is exactly 1.0), and
    the per-holder running sums answer a walk in one lookup: each is the
    walk's own sum, added in the walk's order from 0.0."""

    __slots__ = ('rows', 'dates', 'sorted', 'unit_symbol', '_hpos',
                 '_hsum', '_tpos', '_tsum', '_cum', '_int_exact')

    def __init__(self, rows: List[TaxTransaction], run: _CanadaRun) -> None:
        self.rows = rows
        self.dates = [run.get_sort_date(t) for t in rows]
        self.sorted = all(a <= b for a, b in zip(self.dates,
                                                 self.dates[1:]))
        syms = {t.symbol for t in rows}
        self.unit_symbol = None
        if (self.sorted and len(syms) == 1
                and not (syms & run._split_syms)):
            self.unit_symbol = next(iter(syms))
            self._hpos: Dict[_Holder, List[int]] = {}
            self._hsum: Dict[_Holder, List[float]] = {}
            self._tpos: List[int] = []
            self._tsum: List[float] = []
            self._cum: List[float] = []
            acc = 0.0
            exact = True
            mag = 0.0
            for i, t in enumerate(rows):
                u = t.quantity * 1.0
                h = _sl_holder(run, t)
                hs = self._hsum.setdefault(h, [])
                self._hpos.setdefault(h, []).append(i)
                hs.append((hs[-1] if hs else 0.0) + u)
                if t.id not in run.sheltered_ids and t.id not in run.affiliated_ids:
                    self._tpos.append(i)
                    self._tsum.append((self._tsum[-1] if self._tsum
                                       else 0.0) + u)
                acc += u
                self._cum.append(acc)
                mag += abs(u)
                if exact and not (float(u).is_integer() and mag < 2.0**52):
                    exact = False
            self._int_exact = exact

    def prefix(self, end: str) -> Optional[int]:
        if not self.sorted:
            return None
        import bisect
        return bisect.bisect_right(self.dates, end)

    def holder_sums(self, k: int) -> Dict[_Holder, float]:
        """{holder: the sum of its units among the first k rows}, for
        each holder with a row there (the walk's dict, key for key)."""
        import bisect
        out: Dict[_Holder, float] = {}
        for h, pos in self._hpos.items():
            j = bisect.bisect_left(pos, k)
            if j:
                out[h] = self._hsum[h][j - 1]
        return out

    def taxable_sum(self, k: int) -> Tuple[int, float]:
        """(how many taxable rows among the first k, their running sum)."""
        import bisect
        j = bisect.bisect_left(self._tpos, k)
        return j, (self._tsum[j - 1] if j else 0.0)

    def first_taxable(self) -> TaxTransaction:
        return self.rows[self._tpos[0]]

    def total(self, k: int) -> float:
        """`sum()` of the first k rows' units. Whole units add exactly
        whatever the order or the algorithm (Python 3.12's compensated
        sum() included), so the running sum is sum()'s own result;
        otherwise sum() runs over the same values in the same order."""
        if not k:
            return 0
        if self._int_exact:
            return self._cum[k - 1]
        import itertools
        return sum(itertools.islice((t.quantity * 1.0 for t in self.rows),
                                    k))


class _WashAdjustIndex:
    """The solver's ADJUST rows by each `WASH_<loss id>__` prefix their
    id starts with, in list order: `of(x)` is exactly the rows `v` of
    the list with v.action == 'ADJUST' and
    v.id.startswith(f"WASH_{x}__")."""

    def __init__(self, rows: Iterable[TaxTransaction] = ()) -> None:
        self._by: Dict[str, List[TaxTransaction]] = {}
        for v in rows:
            self.add(v)

    def add(self, v: TaxTransaction) -> None:
        s = v.id
        if (v.action != 'ADJUST' or not isinstance(s, str)
                or not s.startswith('WASH_')):
            return
        p = s.find('__', 5)
        while p != -1:
            self._by.setdefault(s[:p + 2], []).append(v)
            p = s.find('__', p + 1)

    def of(self, loss_id: RowId) -> List[TaxTransaction]:
        return self._by.get(f"WASH_{loss_id}__", [])

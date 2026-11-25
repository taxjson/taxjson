"""What the US engine binds before its walk, and its replacement index.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from taxjson.model.transaction import TaxTransaction
from taxjson.engine.rows import _iso_ordinal

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.lib.corporate_timeline import SplitTimeline
    from taxjson.lib.cost_timeline import Recorder
    from taxjson.lib.loss_overrides import Plan
    from taxjson.engine.premium_ledger import _AssignPremiumLedger
    from taxjson.engine.rows import RowId, RuleTable
    from taxjson.engine.us.lot_store import _USInventory, _USLot
    from taxjson.engine.us.terms import _AcctRatios
    from taxjson.engine.us.trades import _GainRow
    from taxjson.engine.us.wash import _RepRecord


class _RepIndex:
    """The US engine's replacement records of one class (one list, fixed
    once the pre-pass built it), indexed so the §1091 scans read only
    the records they keep (TJ-0238: every loss walked and date-parsed
    every record of its class, so a book cost losses x rows).

    `within(center, before, after)` is the records dated `center -
    before` to `center + after` (day numbers, inclusive) in list order;
    every record's date is ISO (require_computable_rows, TJ-2702).
    `of_tx(id)` is the records of one row id, in list order.
    `pos(rep)` is a record's place in the list order. `holding(ref_key,
    lot)` is the records whose `ref_key` is `lot`, in list order: the
    walk's own filter over the records `note_ref` was told point there
    (every assignment of a lot reference goes through `note_ref`).
    `add_after(parent, rep)` adds a record split off `parent` (part of
    its lot went on its own, TJ-0490): in list order it comes right
    behind `parent` and the records split off it before."""

    __slots__ = ('reps', '_ords', '_order', '_pos', '_keys', '_kids',
                 '_by_tx', '_refs')

    def __init__(self, reps: List[_RepRecord]) -> None:
        self.reps = reps
        keyed = sorted((_iso_ordinal(r['date']), i)
                       for i, r in enumerate(reps))
        self._ords = [o for o, _ in keyed]
        self._order = [i for _, i in keyed]
        self._pos = {id(r): i for i, r in enumerate(reps)}
        # Each record's place in the list order: (i,) for the records
        # the pre-pass made; a split-off record's is its parent's plus
        # its own number, so it sorts right behind the parent's family.
        self._keys: List[Tuple[int, ...]] = [(i,) for i in range(len(reps))]
        self._kids: Dict[int, int] = {}
        self._by_tx: Dict[RowId, List[_RepRecord]] = {}
        for r in reps:
            self._by_tx.setdefault(r['tx'].id, []).append(r)
        self._refs: Dict[Tuple[str, int], List[_RepRecord]] = {}

    def within(self, center: int, before: int, after: int) -> List[_RepRecord]:
        import bisect
        lo = bisect.bisect_left(self._ords, center - before)
        hi = bisect.bisect_right(self._ords, center + after)
        return [self.reps[i] for i in sorted(self._order[lo:hi],
                                             key=self._keys.__getitem__)]

    def of_tx(self, tx_id: RowId) -> List[_RepRecord]:
        return self._by_tx.get(tx_id, [])

    def pos(self, rep: _RepRecord) -> Tuple[int, ...]:
        i = self._pos.get(id(rep))
        return self._keys[i] if i is not None else (0,)

    def add_after(self, parent: _RepRecord, rep: _RepRecord) -> None:
        import bisect
        pi = self._pos.get(id(parent))
        if pi is None:
            return
        n = self._kids.get(pi, 0) + 1
        self._kids[pi] = n
        i = len(self.reps)
        self.reps.append(rep)
        self._pos[id(rep)] = i
        self._keys.append(self._keys[pi] + (n,))
        o = _iso_ordinal(rep['date'])
        k = bisect.bisect_right(self._ords, o)
        self._ords.insert(k, o)
        self._order.insert(k, i)
        self._by_tx.setdefault(rep['tx'].id, []).append(rep)

    def note_ref(self, rep: _RepRecord, ref_key: str, lot: Optional[_USLot]) -> None:
        if lot is None:
            return
        bucket = self._refs.setdefault((ref_key, id(lot)), [])
        if not any(r is rep for r in bucket):
            bucket.append(rep)

    def holding(self, ref_key: str, lot: _USLot) -> List[_RepRecord]:
        bucket = self._refs.get((ref_key, id(lot)))
        if not bucket:
            return []
        live = [r for r in bucket if r.get(ref_key) is lot]
        if len(live) != len(bucket):
            bucket[:] = live
        live.sort(key=self.pos)
        return live


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _USRun:
    """What the US engine binds before its walk over the taxable rows: the
    sorted rows, the two inventories and the replacement lists, the
    settings, the ledgers the walk fills in place, the inputs of the engine's
    helpers (`_us_*(run, ...)` module functions, step 4r, TJ-1983; three the
    pre-run scan and recorder share stay pure over one input the walk hands
    from here: `_us_ikey`, `_us_rep_key`, `_us_split_covers`),
    and what the finish reads of the preamble (the compensation planner's
    state, the dated notes, the futures flags, the override planner's
    would-be denials, the events, the taxable rows, the year-end basis).
    Built once as `_prepare` ends, with every field named; the walk, its
    parts and `_finish` read it through `run` (steps 4l and 4p);
    `tests/test_engine_run.py` pins it as it pins `_CanadaRun` (backlog
    TJ-1976). `rules`: each decision record's rule, noted as the walk
    makes the record."""
    _acct_ratios: _AcctRatios
    _assign_underlying: Callable[[TaxTransaction], Optional[str]]
    _comp_building: bool
    _comp_payments: Dict[RowId, Dict[str, Any]]
    _comp_per_unit: Dict[Tuple[RowId, RowId], Decimal]
    _dated_notes: List[Tuple[str, str]]
    _disp_member: Set[RowId]
    _disp_total: Dict[RowId, float]
    _dust_noise: Callable[..., float]
    _ev_pos: Dict[int, int]
    _fut_flags: Dict[RowId, Dict[str, Any]]
    _jlegs: Dict[str, Tuple[TaxTransaction, TaxTransaction]]
    _la_next: Optional[TaxTransaction]
    _long_rep_of: Dict[RowId, _RepRecord]
    _short_rep_of: Dict[RowId, _RepRecord]
    _lot_time: Dict[RowId, str]
    _move_pair: Dict[RowId, TaxTransaction]
    _other_stkdivs: List[Tuple[TaxTransaction, str, Optional[str], float]]
    _outside_seen: Dict[str, bool]
    _ov_plan: Plan
    _ov_would: Dict[RowId, Dict[str, Any]]
    _paid_comp: Dict[str, Any]
    _rep_home: Dict[int, "_RepIndex"]
    _rep_ix: Dict[int, "_RepIndex"]
    _short_cover_chunks: List[Tuple[_GainRow, RowId, RowId]]
    _spin_adj: Dict[Tuple[str, str], TaxTransaction]
    _tl: Optional[Recorder]
    _tl_dates: List[Tuple[str, str]]
    _us_dust: List[TaxTransaction]
    _us_dust_absorbed: List[Tuple[str, float]]
    _us_retests: List[Tuple[_GainRow, TaxTransaction, Decimal, Dict[str, Any]]]
    filed_rows: Sequence[Dict[str, Any]]
    _us_dust_dropped: List[Tuple[str, float]]
    _ye_next: Optional[TaxTransaction]
    all_events: List[TaxTransaction]
    cost_timeline: Optional[str]
    detect_wash_sales: bool
    emit_stderr: bool
    epsilon: float
    get_sort_date: Callable[[TaxTransaction], str]
    inventory_long: _USInventory
    inventory_short: _USInventory
    long_replacements: Dict[str, List[_RepRecord]]
    lots_at: Optional[str]
    non_capital: Callable[[str, str], bool]
    option_underlying: Callable[[str], Optional[str]]
    pending_option_adjustments: _AssignPremiumLedger
    per_account_basis: bool
    realized_gains: List[Dict[str, Any]]
    # The rule of each decision record (engine/rows `RuleTable`), noted as
    # the walk makes the record.
    rules: RuleTable
    short_replacements: Dict[str, List[_RepRecord]]
    split_timeline: SplitTimeline
    # The stock dividend rows that book no shares, each the obligation of
    # a short covered before it (lib/stock_dividend_cover, the one
    # decision, read before the pre-pass: engine/us/prepare
    # `_us_no_share_rows`): row id -> the short's units, the cover's row
    # and dates, the row's delivered cost and how much of it the cover
    # has booked (US-STKDIV-06).
    stkdiv_after_cover: Dict[RowId, Dict[str, Any]]
    symbol_currency: Dict[str, str]
    taxable_sorted: List[TaxTransaction]
    taxable_stock_symbols: Set[Tuple[str, str]]
    trace: bool
    transactions: List[TaxTransaction]
    wash_sale_records: List[Dict[str, Any]]
    window_days: int
    year_end: Optional[str]
    year_end_basis: Optional[str]

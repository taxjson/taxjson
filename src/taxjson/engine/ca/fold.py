"""The forward denial pass: the fixed point as a re-fold of the walk's affected
suffix.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

import copy
import math
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TypeAlias

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.assign import _AssignPairs
    from taxjson.engine.rows import RowId
    from taxjson.engine.ca.holders import _CanadaIteration, _CanadaRun
    from taxjson.engine.ca.indexes import WashPlace
    from taxjson.engine.ca.pools import _Pools

# What the walk reads of a solver's row (_WalkFold._fp): net_amount,
# quantity, the row its placement names (WashPlace.after), symbol, date,
# time, date_settle, account.
_Fingerprint: TypeAlias = Tuple[float, float, Any, str, str, str, str, str]
# A record's fields at a save: its keys and its values (_WalkFold._records).
_Fields: TypeAlias = Tuple[Tuple[str, ...], Tuple[Any, ...]]
# The iteration's pairing context (_WalkFold.restore): the assignment pairs,
# each moved leg's id, each journal's legs' ids, the s.86.1 moves.
_FoldCtx: TypeAlias = Tuple["_AssignPairs", Dict[Any, int], Dict[str, Tuple[int, int]],
                            Set[Tuple[str, str]]]


def _fold_same(a: Any, b: Any) -> bool:
    """Whether `a` and `b` are one value for a checkpoint's purposes: the same
    object, or equal and of one type, a float with the same sign (so 1, 1.0 and
    True differ, as do 0.0 and -0.0, which print apart); a NaN is never the same."""
    if a is b:
        return True
    if type(a) is not type(b) or a != b:
        return False
    return not isinstance(a, float) or math.copysign(1.0, a) == math.copysign(1.0, b)


class _WalkFold:
    """The forward denial pass (recommendation 10 of the 2026-11-07 re-extraction,
    step 4's open bullet): the Canadian solve's fixed point as a re-fold of the
    affected suffix. The walk of an iteration's rows is a fold: the state before
    row i is a function of rows [0, i) and the solver's rows among them (a denial
    is read at its own sale, `it._dis_by_id[tx.id]`). The one look ahead is the
    assignment ledger's (`_AssignPremiumLedger.take` and `stage`: the later legs,
    the kept days, the opening snapshots), over indexes a solver's row never
    enters (`_legs`, `_kept_days`, `_leg_row`, `_snap*`; in `_pos` / `_id_pos` a
    DISALLOW stands at its sale's moment with no trade between them), so those
    reads are the same in every iteration (`tests/test_engine_solver.py` pins
    the indexes). So a pass that adds, removes, moves or re-amounts a DISALLOW
    or ADJUST changes the walk from the first row that differs on, and the rows
    before it build the state they built before. The walk keeps checkpoints of
    that state (`save`) and the next iteration's `restore`
    installs the last one at or before the cut; the walk goes on from `start`,
    and ends in the state a whole-book walk of the iteration's rows ends in, so
    the pass after it sees the same inputs and the fixed point is the same, pass
    for pass. The cut is 0 (a whole-book walk from a fresh state) when the
    iteration's pairing context is not the last one's (an assignment pair, a
    journal, a spin-off set), when a solver's row is not in the walked list (the
    walk reads them by id too, so the cut fails closed), or when no checkpoint
    reaches it. A checkpoint (`save`)
    is the pools and the small scratch in one deep copy, with the book's rows and
    the walk's records atomic (a lot's `rec_ref` stays the list's record); the
    records as a tuple of their fields, shared with the last checkpoint while
    unchanged by type and value (`_fold_same`: 1, 1.0 and True differ, 0.0 and
    -0.0 too), and every kept record's fields put back on a restore (after its
    row a record changes field by field, never inside a
    field: the s.86.1 cut and an assignment's retraction write scalars and
    `_void`); each pool's trace as the list and its length (append-only, and
    `taxjson_gains` reads a pool's history by the list's identity, so a restore
    truncates it in place); the snapshots and the dated lists shallow. The
    run's `_lot_seq` is restored with the state, so a resumed walk numbers its
    new lots from the checkpoint's counter, not where the old loop's count
    (which ran on across passes) would be: only a lot's order among its pool's
    is ever read (the grants' merge at a rename), and that is the walk's order
    either way. Marks
    stand at start + 2**j from 8 up, thinned to distances past start / 4 on a
    resumed walk (a copy grows with the state): the copies held stay a small
    multiple of the walk. A message a row says is said when the row is walked:
    once, where the whole-book walk said it once per iteration."""

    __slots__ = ('_run', '_ck', '_rows', '_vtx', '_ctx', '_fields', 'start', 'marks',
                 'ye_pools', 'ye_shorts')

    def __init__(self, *, run: _CanadaRun) -> None:
        self._run = run
        self._ck: Dict[int, Any] = {}        # position -> the state before that row
        self._rows: Optional[List[TaxTransaction]] = None    # the last iteration's rows
        self._vtx: Tuple[Sequence[TaxTransaction], Dict[int, _Fingerprint]] = ((), {})          # the solver's rows then, and their fingerprint
        self._ctx: Optional[_FoldCtx] = None                # the last iteration's pairing context
        self._fields: Dict[int, _Fields] = {}  # id(record) -> its fields at the last save
        self.start = 0
        self.marks: Set[int] = set()
        self.ye_pools: Optional[_Pools] = None
        self.ye_shorts: Dict[Tuple[str, str], float] = {}

    @staticmethod
    def _fp(v: TaxTransaction, wash_place: Mapping[RowId, WashPlace]) -> _Fingerprint:
        """What the walk reads of a solver's row; `wash_place`: the run's
        placements, an ADJUST's by its id."""
        _wp = wash_place.get(v.id)
        return (v.net_amount, v.quantity, _wp.after if _wp is not None else None,
                v.symbol, v.date, v.time, v.date_settle, v.account)

    def _memo(self, it: _CanadaIteration, lists: Iterable[Iterable[Dict[str, Any]]]) -> Dict[int, Any]:
        """A deepcopy memo that keeps the book's rows (and the solver's) and the
        walk's records (`lists`: the iteration's when saving, the checkpoint's
        when restoring) as they are: the pools' lots point at the records."""
        memo: Dict[int, Any] = {id(t): t for t in it.current_tx_list}
        memo.update((id(t), t) for t in self._run._move_legs)
        memo.update((id(t), t) for t in self._vtx[0])
        for lst in lists:
            memo.update((id(r), r) for r in lst)
        return memo

    @staticmethod
    def _lists(it: _CanadaIteration
               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        return (it.iteration_realized_gains, it.iteration_losses, it.iteration_grant_nil_closes)

    def _records(self, lst: List[Dict[str, Any]], fields: Dict[int, _Fields]
                 ) -> List[Tuple[Dict[str, Any], Tuple[str, ...], Tuple[Any, ...]]]:
        """Each record with its fields as of now: (record, keys, values), the two
        tuples shared with the last save's when the same by type and value
        (`_fold_same`); `fields` collects this save's (it replaces the last save's,
        so a discarded record's tuple is let go, and an id a new record reuses can
        only share a tuple of the same keys and values)."""
        out = []
        for r in lst:
            ks, vs = tuple(r), tuple(r.values())
            was = self._fields.get(id(r))
            if (was is not None and len(was[0]) == len(ks) and len(was[1]) == len(vs)
                    and all(map(_fold_same, was[0], ks))
                    and all(map(_fold_same, was[1], vs))):
                ks, vs = was
            fields[id(r)] = (ks, vs)
            out.append((r, ks, vs))
        return out

    def _cut(self, it: _CanadaIteration, ctx: _FoldCtx) -> int:
        """The first row whose walk may differ from the last iteration's: the first
        row that is not the row there last time, or a solver's row re-amounted or
        moved in place; 0 on the first iteration or a changed context."""
        rows = it.current_tx_list
        if self._rows is None or ctx != self._ctx:
            return 0
        n = min(len(rows), len(self._rows))
        cut = next((i for i in range(n) if rows[i] is not self._rows[i]), n)
        was = self._vtx[1]
        for v in self._run.final_virtual_txs:
            p = it._pos.get(id(v))
            if p is None:
                return 0          # not in the walked list, yet read by id: fail closed
            if id(v) in was and not all(map(_fold_same, was[id(v)], self._fp(v, self._run.wash_place))):
                cut = min(cut, p)
        return cut

    def restore(self, *, it: _CanadaIteration, pairs: _AssignPairs,
                moved_legs: Dict[Any, TaxTransaction],
                jlegs: Dict[str, Tuple[TaxTransaction, TaxTransaction]],
                cev_moves: Set[Tuple[str, str]]) -> None:
        """Before an iteration's walk: install the last checkpoint at or before
        the cut into the iteration's containers (none: the walk starts fresh at
        0), and keep this iteration's rows, solver rows and context for the next
        cut. `marks` are the rows the walk checkpoints before."""
        ctx = (pairs, {k: id(v) for k, v in moved_legs.items()},
               {k: (id(a), id(b)) for k, (a, b) in jlegs.items()}, set(cev_moves))
        cut = self._cut(it, ctx)
        pos = max((p for p in self._ck if p <= cut), default=None)
        if pos is None:
            self._ck.clear()
            self.start, self.ye_pools, self.ye_shorts = 0, None, {}
        else:
            for p in [p for p in self._ck if p != pos]:
                del self._ck[p]
            self._install(it, self._ck[pos])
            self.start = pos
        n, lo = len(it.current_tx_list), max(8, self.start // 4)
        self.marks = {self.start + (1 << j) for j in range(3, n.bit_length() + 1)
                      if (1 << j) >= lo and self.start + (1 << j) < n}
        self._rows = list(it.current_tx_list)
        self._vtx = (list(self._run.final_virtual_txs),
                     {id(v): self._fp(v, self._run.wash_place) for v in self._run.final_virtual_txs})
        self._ctx = ctx

    def save(self, i: int, it: _CanadaIteration, ye_pools: Optional[_Pools],
             ye_shorts: Dict[Tuple[str, str], float]) -> None:
        """The state before row `i` (the class docstring says how each part is
        copied): every container the walk fills or consumes, the run's two
        walk-written fields and the year-end pools."""
        fields: Dict[int, _Fields] = {}
        tl = None
        if it._tl is not None:
            assert it._tl_shorts is not None, "set with _tl (_ca_solve)"
            tl = (it._tl.fold_state(), it._tl_shorts.fold_state())
        memo = self._memo(it, self._lists(it))
        self._ck[i] = (
            (it.global_pools.save(memo),
             *copy.deepcopy((it._msr.fold_state(), it.pending_adjustments.fold_state(),
                             ye_pools, ye_shorts), memo)),
            tuple(self._records(lst, fields) for lst in self._lists(it)),
            list(it.iteration_trace), {k: (v, len(v)) for k, v in it.symbol_acb_traces.items()},
            dict(it.pool_snapshots_this_iter), set(it._jdone), list(it._msr_legs),
            tl,
            list(it._tl_dates), set(self._run._short_routed), self._run._lot_seq[0])
        self._fields = fields

    def _install(self, it: _CanadaIteration, state: Any) -> None:
        """Checkpoint `state` into the iteration's containers, in place (the holder
        is frozen and the recorders' closures read these objects); the checkpoint
        itself is kept as it is, so it can be restored again."""
        (deep, records, trace, traces, snaps, jdone, msr_legs, tl, tl_dates, routed,
         lot_seq) = state
        memo = self._memo(it, [[r for r, _k, _v in src] for src in records])
        it.global_pools.restore(deep[0].save(memo))
        msr, ledger, ye_pools, ye_shorts = copy.deepcopy(deep[1:], memo)
        dst: Any        # the iteration's lists
        src: Any
        it.pool_snapshots_this_iter.clear()
        it.pool_snapshots_this_iter.update(snaps)
        for dst, src in zip(self._lists(it), records):
            dst[:] = [r for r, _ks, _vs in src]
            for r, ks, vs in src:          # every kept record's fields as they were
                r.clear()
                r.update(zip(ks, vs))
        it.symbol_acb_traces.clear()
        for k, (lst, n) in traces.items():
            del lst[n:]
            it.symbol_acb_traces[k] = lst
        for dst, src in ((it.iteration_trace, trace), (it._msr_legs, msr_legs),
                         (it._tl_dates, tl_dates)):
            dst[:] = src
        it._jdone.clear()
        it._jdone.update(jdone)
        it._msr.restore(msr)
        it.pending_adjustments.restore(ledger)
        if tl is not None:
            assert it._tl is not None and it._tl_shorts is not None, \
                "a checkpoint with a timeline is restored into an iteration with one (run.cost_timeline)"
            it._tl.restore(tl[0])
            it._tl_shorts.restore(tl[1])
        self._run._short_routed.clear()
        self._run._short_routed.update(routed)
        self._run._lot_seq[0] = lot_seq
        self.ye_pools, self.ye_shorts = ye_pools, ye_shorts

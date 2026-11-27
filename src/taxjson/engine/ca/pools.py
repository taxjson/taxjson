"""The ACB pools: their state and copies, flat pools, short and futures pools,
the lots per account.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Set, Tuple, TypeAlias, Union

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.lib.option_symbols import is_option_symbol
from taxjson.lib.row_types import MATERIAL_COST
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.engine.consts import _ZERO, _TWO_CENTS, _NANO, _HALF_CENT
from taxjson.engine.journal import _is_listing_journal

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.engine.ca.pool import AcbPool
    from taxjson.model.transaction import TaxTransaction

# Pools by key (a symbol, or its short pool: lib/futures.SHORT_POOL_SUFFIX),
# to read: the walk's PoolBook (engine/ca/pool, which owns a pool's writes:
# PLAN step 13), or a snapshot or a folded copy of it (AcbPool.snapshot,
# `part`, `netted`: the year-end's, a short's part for one account...).
_Pools: TypeAlias = "Mapping[str, AcbPool]"
# One written option lot (_ca_short_lot_open): 'tx_id', 'seq', 'units'
# (float), 'per_unit' and 'fee_per_unit' (Decimal: the net premium and the
# commission and fee netted in it, per unit), 'rec', 'rec_ref' and 'loss_ref' (the
# grant's gain and loss records), 'date', 'year', 'account'.
_Lot: TypeAlias = Dict[str, Any]
# {id(pool): (the pool's state key, its per-account lots)} (_account_lots).
_LotsCache: TypeAlias = Dict[int, Tuple[Tuple[Any, ...], Dict[str, Dict[str, Any]]]]
# A pool's state for lib/cost_timeline's Recorder: (qty, cost, deferred_wash,
# tainted, currency, position_start_date, last_acq_date), its money Decimal,
# and each account's written premium as an eighth field when it has one.
_PoolState: TypeAlias = Union[
    Tuple[float, "Decimal", "Decimal", bool, str, Optional[str], Optional[str]],
    Tuple[float, "Decimal", "Decimal", bool, str, Optional[str], Optional[str],
          Dict[str, Any]]]


_MATERIAL = M.of(MATERIAL_COST)        # as money (TJ-3089)


def pool_is_flat(symbol: str, qty: float, cost: Any = 0.0) -> bool:
    """A pool with `qty` units and `cost` is no holding: no units, or
    fewer than the tolerance (pool_qty_eps) and less than MATERIAL_COST
    of cost. A sub-micro position bought (or consolidated) at a real
    cost is held: it shows in the inventory and its cost amount counts
    (backlog TJ-0649). Exactly the tolerance with no material cost is no
    holding here, as the short router and the trade walk read it (a
    marked short of exactly the tolerance: tests/test_engine_check_pins_ca
    `TestADustMarkedShortIsNoPosition`), though `AcbPool.drain` leaves
    such units in the pool (backlog TJ-5140)."""
    q = float(qty or 0.0)
    if not q:
        return True
    if abs(q) > pool_qty_eps(symbol, q):
        return False
    return abs(M.of(cost or 0.0)) < _MATERIAL


def _warn_stranded_basis(pools: _Pools) -> None:
    """Basis-residue post-condition (Canada pools): a pool whose quantity
    has drained to zero must carry ~zero cost — the average-cost math
    consumes exactly the remaining basis on a full drain. Residue means
    leaked/duplicated basis (chunk-math regressions) or a real-world
    late ADJUST (e.g. an ETF's ROC posting after a full exit — which is
    taxable as a gain and needs manual handling, not a silent strand).

    The full basis identity (cost in == cost consumed + cost remaining)
    is deliberately NOT asserted: reproducing the engine's premium
    apportioning, taint gating, and crossing-zero proportional cost
    would be a second implementation of the pooling itself — the
    residue check is the honest subset."""
    seen_objs = set()
    for sym, pool in pools.items():
        if id(pool) in seen_objs:
            continue                    # rename aliases share one object
        seen_objs.add(id(pool))
        if (pool_is_flat(sym, pool.qty, pool.total_cost)
                and abs(M.of(pool.total_cost)) > _TWO_CENTS):
            emit_line(f"warning: conservation: {sym} pool is EMPTY but carries "
                f"{M.of(pool.total_cost):.2f} of stranded basis — "
                f"either an engine chunk-math bug or an ADJUST/ROC row "
                f"posted after the position was fully closed (the latter "
                f"is a taxable event needing manual review).")


def _warn_waiting_additions(pools: _Pools) -> None:
    """A denied loss's addition still parked on a pool when the walk ends
    (CA-SL-19; `AcbPool.take_pending`): no long position took it, so no
    ACB holds it and no sale in these books recovers it. A short never
    takes it (the owner's ruling on backlog TJ-4258), so a pool left
    short, or flat, with it is said, once per pool: the symbol and the
    amount."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX
    seen_objs: Set[int] = set()
    for key, pool in pools.items():
        if id(pool) in seen_objs:
            continue                    # rename aliases share one object
        seen_objs.add(id(pool))
        amount = M.of(pool.pending_wash or _ZERO)
        if amount < _HALF_CENT:
            continue
        sym = key[:-len(SHORT_POOL_SUFFIX)] if key.endswith(SHORT_POOL_SUFFIX) else key
        emit_line(f"warning: {sym}: a denied superficial loss of {amount:.2f} "
                  f"still waits for a long position at the end of the books: "
                  f"no ACB holds it")


def _account_premiums(p: AcbPool) -> Dict[str, Any]:
    """A written option open in several accounts under close timing:
    what each account's contracts hold, which a close takes
    (_short_lot_close, CA-OPT-06), for the per-account views
    (taxjson_split_gains._split_inventory, backlog TJ-1251). Not for a
    recognised lot, or lots that are not the whole position."""
    lots = p.grants or []
    if (p.qty >= 0 or any(l.get('rec') for l in lots)
            or abs(sum(l['units'] for l in lots) - abs(p.qty)) > 1e-6):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for l in lots:
        e = out.setdefault(str(l.get('account') or ''),
                           {'units': 0.0, 'premium': _ZERO})
        e['units'] = M.units(e['units'] + l['units'], 10)
        e['premium'] = M.present(e['premium'] + M.of(l['units']) * l['per_unit'], 6)
    return ({'account_premiums': out}
            if len(out) > 1 and '' not in out else {})


def _pools_copy(pools: _Pools) -> _Pools:
    """The Canadian engine's pools as they stand (units, cost, currency,
    deferred_wash, each account's written premium), rename aliases still
    sharing one entry: the year-end snapshot (backlog TJ-0282, TJ-1251)."""
    memo: Dict[int, AcbPool] = {}
    out: Dict[str, AcbPool] = {}
    for k, p in pools.items():
        c = memo.get(id(p))
        if c is None:
            # (`account_lots`: each account's written contracts, for its
            # own row beside a long: _short_account_part, TJ-3194)
            c = memo[id(p)] = p.snapshot(
                _account_premiums(p).get('account_premiums'),
                {a: {'units': e['units'], 'premium': e['premium']}
                 for a, e in _account_lots(p).items()}
                if p.grants and p.qty < 0 else None)
        out[k] = c
    return out


def _pool_waits(p: AcbPool) -> bool:
    """Whether a flat pool holds a denied loss's addition for the position
    it next opens (CA-SL-19; `_walk_adjust` parks it in `pending_wash` and
    tallies it in `deferred_wash`, which stays while the pool is flat).
    Such a pool is the symbol's own state, 0 units and the deferred
    amount, whatever short is open beside it: the short is shown in its
    own section (the owner's ruling on backlog TJ-2712). A wait is a
    material deferred amount (MATERIAL_COST, as pool_is_flat reads a
    cost) or a parked amount still to apply: the sub-cent residue a
    sale of the whole position but a sliver leaves in the tally is no
    wait (TJ-3285)."""
    return (M.of(p.deferred_wash or 0.0)
            >= _MATERIAL
            or M.of(p.pending_wash or 0.0) > _NANO)


def _ca_pool_state(pools: _Pools) -> Callable[[Tuple[str, str]], Optional[_PoolState]]:
    """state_of(label) for the Canadian engine's cost timeline
    (lib/cost_timeline.Recorder): the pool of the label's symbol now,
    its cost in the inventory's sign convention (a short's negative).
    A futures contract's short pool is the label's state only while no
    long contract is held: the timeline is the cost of property held
    (T1135, the year-end snapshot, the dated list), and a short opened
    in another account is an obligation that never nets a long
    contract's cost away (backlog TJ-0650; _ca_short_state records the
    short itself). A flat pool holding a denied loss's waiting addition
    (_pool_waits) keeps its own state too, 0 units and the deferred
    amount, the short beside it in the `shorts` part (backlog TJ-2712)."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX

    def state(lab: Tuple[str, str]) -> Optional[_PoolState]:
        sym = lab[1]
        p = pools.get(sym)
        sp = pools.get(sym + SHORT_POOL_SUFFIX)
        if sp is not None and (p is None or (abs(p.qty) <= 1e-12
                                             and not _pool_waits(p))):
            p = sp
        if p is None:
            return None
        q = float(p.qty)
        tc = M.of(p.total_cost)
        lad = p.last_acq_date
        st = (q, tc if q >= 0 else -tc,
              M.of(p.deferred_wash or _ZERO),
              bool(p.tainted), p.currency or '',
              p.position_start_date,
              None if lad in (None, '1970-01-01') else lad)
        # (each account's written premium: Recorder.finish, TJ-1251)
        prem = _account_premiums(p).get('account_premiums')
        return st + (prem,) if prem else st
    return state


def _ca_touched(tx: TaxTransaction, jlegs: Dict[str, Tuple[TaxTransaction, TaxTransaction]]
                ) -> List[Tuple[str, str]]:
    """The pools (cost-timeline labels) one row of the Canadian walk can
    change: its own, a rename's target, a listing journal's two."""
    labs = [('', tx.symbol)]
    if tx.action == 'SPLIT':
        new = (tx.symbol_new or '').strip()
        if new and new != tx.symbol:
            labs.append(('', new))
    if _is_listing_journal(tx):
        jl = jlegs.get(tx.journal_pair)
        if jl:
            labs += [('', jl[0].symbol), ('', jl[1].symbol)]
    return labs


def _fold_short_futures_pools(pools: _Pools) -> _Pools:
    """The Canadian engine's final pools with each short pool
    (lib/futures.SHORT_POOL_SUFFIX) under its symbol's entry when nothing
    else holds it. A short beside a long stays under its own key: an
    obligation and the property held are two positions, never their net
    (their costs have opposite signs; backlog TJ-1028, TJ-1058, TJ-1103).
    So does a short beside the symbol's flat pool while that pool holds a
    denied loss's waiting addition (_pool_waits): the pool keeps its row,
    0 units and the deferred amount (backlog TJ-2712). _pool_rows gives
    them to the reports."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX
    if not any(k.endswith(SHORT_POOL_SUFFIX) for k in pools):
        return pools

    def fold(sym: str, lp: Optional[AcbPool], sp: AcbPool) -> List[Tuple[str, AcbPool]]:
        # A flat long pool whose denied loss waits keeps its row, the
        # short beside it (TJ-2712).
        flat = lp is not None and abs(lp.qty) <= 1e-12
        if lp is None or (flat and not _pool_waits(lp)):
            return [(sym, sp)]
        if abs(sp.qty) > 1e-12 and (flat or lp.qty > 0):
            return [(sym, lp), (sym + SHORT_POOL_SUFFIX, sp)]
        if abs(sp.qty) > 1e-12:
            return [(sym, lp.netted(sp))]
        return [(sym, lp)]

    # The long pools in their order; an option series' written contracts
    # where the walk first opened a pool of the series (the long or the
    # short): the place its single pool had before TJ-2735 (TJ-3197).
    out: Dict[str, AcbPool] = {}
    done: Set[str] = set()
    for k, p in pools.items():
        sym = k[:-len(SHORT_POOL_SUFFIX)] if k.endswith(SHORT_POOL_SUFFIX) else k
        if is_option_symbol(sym) and sym + SHORT_POOL_SUFFIX in pools:
            if sym not in done:
                done.add(sym)
                out.update(fold(sym, pools.get(sym), pools[sym + SHORT_POOL_SUFFIX]))
        elif k == sym:
            out[k] = p
    # A futures or marked share short: under its symbol's entry when
    # nothing else holds it, else after the long pools, as before.
    for k, sp in pools.items():
        if not k.endswith(SHORT_POOL_SUFFIX):
            continue
        sym = k[:-len(SHORT_POOL_SUFFIX)]
        if sym in done:
            continue
        for kk, pp in fold(sym, out.get(sym), sp):
            out[kk] = pp
    return out


class _ShortUnits(Dict[Tuple[str, str], float]):
    """{(symbol, account): units short} (_MarkedShortRouter.short) that also
    keeps each symbol's accounts (`of`), so a symbol's shares cost the
    accounts short in it, not the whole table (backlog TJ-4978). A symbol's
    accounts stand in the order the table holds their keys: both take the
    same insertions and deletions (a new key last, a changed one where it
    was), so the floats are added in the order a scan of the table adds
    them. Written by item assignment, `del` and `clear` only: the other
    dict writes are refused (_refuse_other_writes), so none can pass the
    index by."""
    __slots__ = ('_by',)

    def __init__(self, units: Optional[Mapping[Tuple[str, str], float]] = None) -> None:
        super().__init__()
        self._by: Dict[str, Dict[str, float]] = {}
        for k, u in (units or {}).items():
            self[k] = u

    def __setitem__(self, k: Tuple[str, str], units: float) -> None:
        super().__setitem__(k, units)
        self._by.setdefault(k[0], {})[k[1]] = units

    def __delitem__(self, k: Tuple[str, str]) -> None:
        super().__delitem__(k)
        del self._by[k[0]][k[1]]

    def clear(self) -> None:
        super().clear()
        self._by.clear()

    def of(self, sym: str) -> Mapping[str, float]:
        """{account: units} of one symbol, in the table's order."""
        return self._by.get(sym, _NO_UNITS)

    def __deepcopy__(self, memo: Dict[int, Any]) -> _ShortUnits:
        """The re-fold's checkpoint copy (keys and units are immutable)."""
        out = memo[id(self)] = _ShortUnits(self)
        return out

    def __reduce__(self) -> Tuple[Any, ...]:
        return (_ShortUnits, (dict(self),))


_NO_UNITS: Mapping[str, float] = {}


def _refuse_other_writes() -> None:
    """dict's other writes on a _ShortUnits raise: they would not keep `of`."""
    def refused(self: _ShortUnits, *_a: Any, **_k: Any) -> None:
        raise TypeError("_ShortUnits: write by item, del or clear (its by-symbol index)")
    for name in ('pop', 'popitem', 'setdefault', 'update', '__ior__'):
        setattr(_ShortUnits, name, refused)


_refuse_other_writes()


def _short_shares(sym: str, short_units: Dict[Tuple[str, str], float]
                  ) -> Dict[str, float]:
    """{account: its share of the symbol's short pool}: each account's
    units short (_MarkedShortRouter.short), of all of them. The router's
    own table gives the symbol's accounts (_ShortUnits.of); a plain copy
    is scanned, in the same order."""
    own: Iterable[Tuple[str, float]] = (
        short_units.of(sym).items() if isinstance(short_units, _ShortUnits)
        else ((a, u) for (y, a), u in short_units.items() if y == sym))
    who = {a: u for a, u in own if u > pool_qty_eps(sym, u)}
    total = sum(who.values())
    return {a: u / total for a, u in who.items()}


def _account_lots(p: AcbPool, cache: Optional[_LotsCache] = None
                  ) -> Dict[str, Dict[str, Any]]:
    """{account: its written lots' 'units', 'premium' (every lot, under
    either timing) and 'lots'} of a short option pool; {} for a pool with
    no lots (a share or futures short). `cache` (by id(pool)) keeps the
    tally while the pool's state stands: its units, cost and the lots'
    count and accounts (the cost timeline reads every account's part of
    every short pool after each row: once per pool state, TJ-3198)."""
    lots = p.grants or []
    if not lots:
        return {}
    key = (p.qty, p.total_cost, len(lots),
           tuple(lot.get('account') for lot in lots))
    if cache is not None:
        hit = cache.get(id(p))
        if hit is not None and hit[0] == key:
            return hit[1]
    out: Dict[str, Dict[str, Any]] = {}
    for lot in lots:
        e = out.setdefault(str(lot.get('account') or ''),
                           {'units': 0.0, 'premium': _ZERO, 'lots': []})
        e['units'] = M.units(e['units'] + lot['units'], 10)
        e['premium'] += M.of(lot['units']) * lot['per_unit']
        e['lots'].append(lot)
    if cache is not None:
        cache[id(p)] = (key, out)
    return out


def _short_account_part(p: AcbPool, acct: str, f: float,
                        cache: Optional[_LotsCache] = None
                        ) -> Dict[str, Any]:
    """Account `acct`'s part of short pool `p`, whose share of the units is
    `f`, as the pool fields it changes ('qty', 'total_cost',
    'deferred_wash' and, with lots, 'grants'): its own written contracts
    (their premium, and the lots themselves, so their recognised premium
    is its own too: CA-OPT-06, TJ-2735, TJ-3194) when its lots hold the
    units it is short, else its share of the pool by units (a share or
    futures short, or lots that are not the whole position: TJ-1303)."""
    dw = M.of(p.deferred_wash) * M.of(f)
    e = (_account_lots(p, cache) or p.account_lots or {}).get(acct)
    if e is not None and abs(e['units'] + p.qty * f) <= 1e-6:
        part = {'qty': -e['units'], 'total_cost': D(e['premium']), 'deferred_wash': dw}
        if e.get('lots') is not None:
            part['grants'] = e['lots']
        return part
    return {'qty': p.qty * f, 'total_cost': p.total_cost * D(f),
            'deferred_wash': dw}


def _pool_rows(pools: _Pools,
               short_units: Dict[Tuple[str, str], float]
               ) -> Iterator[Tuple[str, AcbPool, str]]:
    """(symbol, pool, account) of each pool the reports show: a short
    beside a long (_fold_short_futures_pools) is one row per account that
    is short, each at its share of the short's own proceeds (an option
    series' written contracts: at their own premium, _short_account_part),
    named by its account because it is that account's alone (the long is
    pooled, and is apportioned without it:
    taxjson_split_gains._split_inventory). Every other pool: its symbol,
    account ''."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX
    units: Optional[_ShortUnits] = None
    for k, p in pools.items():
        if not k.endswith(SHORT_POOL_SUFFIX):
            yield k, p, ''
            continue
        sym = k[:-len(SHORT_POOL_SUFFIX)]
        if units is None:
            # Grouped by symbol once (the books' final units, which nothing
            # writes while these rows are read): TJ-4978.
            units = (short_units if isinstance(short_units, _ShortUnits)
                     else _ShortUnits(short_units))
        shares = _short_shares(sym, units)
        if not shares:
            yield sym, p, ''
        for acct, f in sorted(shares.items()):
            yield sym, p.part(**_short_account_part(p, acct, f)), acct


def _with_shorts(doc: Dict[str, Any], shorts: Dict[str, Any]
                 ) -> Dict[str, Any]:
    """The cost timeline with its `shorts` part, in books that have one
    (lib/cost_timeline.short_rows_on)."""
    return {**doc, 'shorts': shorts} if shorts.get('rows') else doc


def _ca_short_state(pools: _Pools, short_units: Callable[[], Dict[Tuple[str, str], float]]
                    ) -> Callable[[Tuple[str, str]], Optional[_PoolState]]:
    """state_of((account, symbol)) for the Canadian engine's second cost
    timeline: an account's part of a symbol's short pool (_pool_rows). It
    is what `tjs list --date` shows beside a long and what tells T1135
    who was short; the main one (_ca_pool_state) stays the cost of
    property held."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX
    cache: _LotsCache = {}        # each pool's per-account lots (TJ-3198)

    def state(lab: Tuple[str, str]) -> Optional[_PoolState]:
        acct, sym = lab
        p = pools.get(sym + SHORT_POOL_SUFFIX)
        if p is None:
            return None
        # The account's own units first: _short_shares gives a share only to
        # an account whose units pass this test, so one that fails it has
        # none, whatever the others hold (backlog TJ-4977).
        units = short_units()
        u = units.get((sym, acct))
        if u is None or not u > pool_qty_eps(sym, u):
            return None
        f = _short_shares(sym, units).get(acct)
        if not f:
            return None
        lad = p.last_acq_date
        part = _short_account_part(p, acct, f, cache)
        return (float(part['qty']), -M.of(part['total_cost']), part['deferred_wash'],
                bool(p.tainted), p.currency or '',
                p.position_start_date,
                None if lad in (None, '1970-01-01') else lad)
    return state

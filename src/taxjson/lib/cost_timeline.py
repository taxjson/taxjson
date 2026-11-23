"""The engines' cost timeline (backlog TJ-0417).

A gains engine run asked for it (`GainsRequest.cost_timeline`,
`taxjson-gains --cost-timeline FILE`) records, beside the gains, each
pool's quantity and cost after every row of the books that changed it,
dated, in the order the engine walked the rows: Canada's pools (one per
symbol across the book's taxable accounts, ITA s.47: account "") and
the US engine's lots per account (per-account FIFO basis: the account's
own). The readers take the pools from it instead of replaying the books
on their own, so they hold what the engine holds: `tjs t1135` (the
cost amounts, src/taxjson/bin/taxjson_t1135.py `walk_costs`), the
year-end snapshot `close-year` records and `handoff` compares
(lib/handoff `snapshot`) and `tjs list --date`
(src/taxjson/cli/positions.py `cmd_positions`).

A row's date is the day from which its state holds, on the engine's own
date basis (`basis`: the settlement date, or the trade date on a
trade-basis project); the pools at the end of a day are each pool's last
row dated on or before it (`pools_on`). Rows that share a `seq` came
from one row of the books (a rename empties one pool and fills
another). A pool's `total_cost` follows the inventory's convention: a
short's is negative (the opening proceeds credited).

A short pool beside a long (Canada: a future, or a share short the broker
marks) is no property held, so it has no row here while the long is held,
nor while the symbol's flat pool holds a denied loss's waiting addition
(CA-SL-19): that pool's row is the wait, 0 units and the deferred amount
(engine/ca/pools._pool_waits; backlog TJ-2712). Its part `shorts`, a
timeline of the same shape, has each account's part of it, per (account,
symbol): `short_rows_on`.

A written option open in several accounts under close timing (Canada):
`account_premiums` has, by row number, each account's premium in the
contracts then open, which `inventory_on` passes to the per-account views
(backlog TJ-1251).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TypeAlias, Union

from taxjson.model import money as M

VERSION = 1

# The US engine's flat tolerance (USATaxRules.compute_gains `epsilon`):
# a lot under it is not in the inventory.
US_QTY_EPS = 1e-8


def us_eps(_symbol: str) -> float:
    return US_QTY_EPS

# One row: where it came from, the pool, and its state after the row.
FIELDS = ("seq", "date", "account", "symbol", "qty", "total_cost",
          "deferred_wash", "unknown_cost", "currency",
          "position_start_date", "last_acq_date", "renamed_to")

# A pool's state: qty, total_cost, deferred_wash, unknown_cost,
# currency, position_start_date, last_acq_date (the Canadian engine may
# add each account's written premium: `account_premiums`).
State = Tuple[float, float, float, bool, str, Optional[str], Optional[str]]
Label = Tuple[str, str]                 # (account, symbol)
# A state as an engine's `state_of` hands it in: State's fields with the
# money (total_cost, deferred_wash) Decimal, the engines' own; the Canadian
# engine adds each account's written premium as an eighth field when it has
# one (engine/ca/pools._PoolState). The flat state, _FLAT, keeps float zeros.
EngineState: TypeAlias = Union[
    Tuple[float, "Decimal", "Decimal", bool, str, Optional[str], Optional[str]],
    Tuple[float, "Decimal", "Decimal", bool, str, Optional[str], Optional[str],
          Dict[str, Any]]]

_FLAT: State = (0.0, 0.0, 0.0, False, "", None, None)
# The types of a state's own seven fields (_unequal_to_itself).
_PLAIN = (float, int, bool, str, type(None), Decimal)


def _unequal_to_itself(st: Union[State, EngineState]) -> bool:
    """Whether the same state read again might not compare equal to this
    one: it holds a value that is not equal to itself (a float or Decimal
    NaN; a signalling one raises), or one that is no plain number, text,
    bool or None (an eighth field's dict could hold such a value)."""
    try:
        return any(x != x or type(x) not in _PLAIN for x in st)
    except ArithmeticError:
        return True


class Recorder:
    """Collects the timeline while an engine walks its rows.

    `state_of(label)` is the pool's state now (None: no pool). The
    engine calls `touch` after each row with the pools the row may have
    changed, and `sweep` at the end of each day (and of the books): it
    checks every pool still held, so a change the row's own pools do not
    name (a basis addition landing in another account's lot) is in the
    day's state. `finish` dates the rows."""

    def __init__(self, state_of: Callable[[Label], Optional[EngineState]]):
        self._state_of = state_of
        self._last: Dict[Label, Union[State, EngineState]] = {}
        self._held: Dict[Label, None] = {}      # ordered set
        # The labels whose last state is not the flat one and that are not
        # held (a state with no units and no cost that differs in another
        # field): with `_held`, every label a check with no state would
        # record a row for (`loose`).
        self._loose: Dict[Label, None] = {}
        # The labels whose last state holds a value unequal to itself (a
        # NaN): a check of the same state again records it again
        # (`sweep`'s `steady`).
        self._unequal: Dict[Label, None] = {}
        self._rows: List[Tuple[int, Label, Union[State, EngineState], str]] = []
        # The id of the book row each `seq` is (the engine appends one
        # per row it walks; the Canadian engine does): the document's
        # `row_ids`, so a reader can place a row of the books between
        # two steps of one day (T1135: a custody account's units at
        # each step, backlog TJ-0646).
        self.row_ids: List[str] = []

    def fold_state(self) -> tuple:
        """What a walk changes here, copied (the entries are tuples and strings):
        the Canadian re-fold's checkpoint (`engine/ca/fold._WalkFold`)."""
        return (dict(self._last), dict(self._held), list(self._rows), list(self.row_ids),
                dict(self._loose), dict(self._unequal))

    def restore(self, state: tuple) -> None:
        """Back to a `fold_state`, in place (the engine's closures keep this object)."""
        # (six names: a tuple of another shape is refused, not half restored)
        last, held, rows, row_ids, loose, unequal = state
        dst: Dict[Label, Any]
        for dst, src in ((self._last, last), (self._held, held),
                         (self._loose, loose), (self._unequal, unequal)):
            dst.clear()
            dst.update(src)
        self._rows[:] = rows
        self.row_ids[:] = row_ids

    def loose(self) -> Dict[Label, None]:
        """The labels not held whose last state is not the flat one (read,
        not changed): a sweep that leaves out a label with no state now
        must still name these, so their flat row is recorded."""
        return self._loose

    def touch(self, seq: int, labels: Iterable[Label],
              renamed: Optional[Dict[Label, str]] = None) -> None:
        for lab in labels:
            self._check(seq, lab, (renamed or {}).get(lab, ""))

    def sweep(self, seq: int, labels: Iterable[Label] = (), *,
              steady: bool = False) -> None:
        """Check every label held, then `labels`. `steady`: state_of gives
        a label the same state when asked twice in one sweep (it reads
        nothing a check writes), so a label of `labels` that was checked a
        moment ago as a held one is not asked again: its second check
        compares that same state with the one just kept and records
        nothing, unless the state holds a value unequal to itself
        (`_unequal`), which is still checked."""
        held, labels = list(self._held), list(labels)
        for lab in held:
            self._check(seq, lab, "")
        if steady and held and labels:
            done = set(held)
            labels = [lab for lab in labels
                      if lab not in done or lab in self._unequal]
        for lab in labels:
            self._check(seq, lab, "")

    def _check(self, seq: int, lab: Label, renamed: str) -> None:
        st = self._state_of(lab) or _FLAT
        if st == self._last.get(lab, _FLAT) and not renamed:
            return
        self._last[lab] = st
        if _unequal_to_itself(st):
            self._unequal[lab] = None
        elif self._unequal:
            self._unequal.pop(lab, None)
        # (the flat state's cost is a float zero: read through the switch)
        if abs(st[0]) > 1e-12 or abs(M.of(st[1])) > M.of(1e-9):
            self._held[lab] = None
            self._loose.pop(lab, None)
        else:
            self._held.pop(lab, None)
            if st == _FLAT:
                self._loose.pop(lab, None)
            else:
                self._loose[lab] = None
        self._rows.append((seq, lab, st, renamed))

    def finish(self, dates: List[Tuple[str, str]], *, stamp: str,
               basis: str = "") -> Dict[str, Any]:
        """The timeline document. `dates[seq]` is (the date the engine
        walked row `seq` on, its date on the timeline's `basis`).

        `stamp` dates the rows. When the basis is the one the engine
        walks on, a day's state is the engine's own year-end one, even
        where it walked a row out of date order (an assignment's option
        row moved in front of its earlier stock leg): "max" (Canada,
        final_year_end_pools) — the rows before the first one dated
        after the day; "min" (USA, _ye_lots) — every row up to the last
        one dated on or before it. "pool" (the other basis: a Canadian
        trade-date project, the engine walking settlement dates) — each
        row on its own date on the basis, never before an earlier row of
        its pool: what each pool held at the end of a day on that basis
        (two pools' rows interleave by those dates, not by the walk)."""
        n = len(dates)
        stamps: List[str] = [""] * n
        if stamp == "max":
            cur = ""
            for i, (d, _b) in enumerate(dates):
                cur = max(cur, d or "")
                stamps[i] = cur
        elif stamp == "min":
            cur = "9999-12-31"
            for i in range(n - 1, -1, -1):
                cur = min(cur, dates[i][0] or "9999-12-31")
                stamps[i] = cur
        from taxjson.lib.numeric import round_half_up
        rows = []
        premiums: Dict[str, Any] = {}
        pool_last: Dict[Label, str] = {}
        for seq, lab, st, renamed in self._rows:
            if not 0 <= seq < n:
                d = ""
            elif stamp == "pool":
                d = max(pool_last.get(lab, ""), dates[seq][1] or "")
                pool_last[lab] = d
            else:
                d = stamps[seq]
            # Money to 4 places, as the gains file's inventory; the
            # quantity as the engine holds it (a coin's dust stays).
            qty, cost, dw, *rest = st
            if len(rest) > 4:
                premiums[str(len(rows))] = rest.pop()
            rows.append([seq, d, lab[0], lab[1], float(qty),
                         round_half_up(cost, 4), round_half_up(dw, 4),
                         *rest, renamed])
        doc = {"version": VERSION, "basis": basis,
               "fields": list(FIELDS), "rows": rows}
        if self.row_ids and len(self.row_ids) == n:
            doc["row_ids"] = list(self.row_ids)
        if premiums:
            doc["account_premiums"] = premiums
        return doc


# ------------------------------------------------------------ readers

def rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The timeline's rows as dicts, by date, then in the engine's
    order."""
    fields = list(doc.get("fields") or FIELDS)
    out = [dict(zip(fields, r)) for r in doc.get("rows") or []]
    for i, prem in (doc.get("account_premiums") or {}).items():
        out[int(i)]["account_premiums"] = prem
    out.sort(key=lambda r: (str(r.get("date") or ""), int(r.get("seq")
                                                          or 0)))
    return out


def _is_num(v: Any) -> bool:
    import math
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_opt_str(v: Any) -> bool:
    return v is None or isinstance(v, str)


# Each field's test: a row the engine wrote (Recorder.finish) passes
# every one.
_FIELD_OK: Dict[str, Callable[[Any], bool]] = {
    "seq": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "date": _is_str,
    "account": _is_str,
    "symbol": lambda v: isinstance(v, str) and bool(v),
    "qty": _is_num,
    "total_cost": _is_num,
    "deferred_wash": _is_num,
    "unknown_cost": lambda v: isinstance(v, bool),
    "currency": _is_str,
    "position_start_date": _is_opt_str,
    "last_acq_date": _is_opt_str,
    "renamed_to": _is_str,
}

def row_seqs(doc: Dict[str, Any]) -> Dict[str, int]:
    """{book row id: the seq the engine walked it as} ({} when the
    timeline does not record its rows' ids). An id walked twice is left
    out: such a row cannot be placed."""
    out: Dict[str, int] = {}
    twice = set()
    for i, rid in enumerate(doc.get("row_ids") or []):
        rid = str(rid or "")
        if not rid:
            continue
        if rid in out:
            twice.add(rid)
        out[rid] = i
    for rid in twice:
        del out[rid]
    return out


def check(doc: Any) -> Optional[str]:
    """None for a timeline this version reads, else what is wrong: its
    version, its field names (every one of FIELDS), and each row (one
    value per field, each of its field's type, the numbers finite). A
    damaged sidecar read anyway turned a short row's cost into 0 and a
    row without its quantity into no holding (backlog TJ-0684)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return "not a cost timeline"
    if doc.get("version") != VERSION:
        return (f"cost timeline version {doc.get('version')!r}, this "
                f"taxjson reads {VERSION}")
    fields = doc.get("fields")
    if (not isinstance(fields, list)
            or not all(isinstance(f, str) for f in fields)):
        return "not a cost timeline (its field names are missing)"
    missing = [f for f in FIELDS if f not in fields]
    if missing:
        return ("not a cost timeline (no field "
                + ", ".join(repr(f) for f in missing) + ")")
    for i, r in enumerate(doc["rows"]):
        if not isinstance(r, list) or len(r) != len(fields):
            n = len(r) if isinstance(r, list) else "no"
            return (f"row {i} has {n} values, the timeline names "
                    f"{len(fields)} fields")
        for f, v in zip(fields, r):
            ok = _FIELD_OK.get(f)
            if ok is not None and not ok(v):
                return f"row {i}: {f} is {v!r}"
    prem = doc.get("account_premiums")
    if prem is not None and not (
            isinstance(prem, dict)
            and all(str(k).isdigit() and int(k) < len(doc["rows"])
                    and isinstance(v, dict) for k, v in prem.items())):
        return "its account_premiums are not by row number"
    if "shorts" in doc:
        problem = check(doc["shorts"])
        if problem:
            return f"its shorts: {problem}"
    return None


def pools_on(doc: Dict[str, Any], date: str,
             account: Optional[str] = None
             ) -> Dict[Label, Dict[str, Any]]:
    """{(account, symbol): the pool's last row dated on or before the
    end of `date`} — every pool the timeline has by then, flat ones
    included; `account` keeps that account's pools (and the pooled ones,
    account "", which are every account's)."""
    out: Dict[Label, Dict[str, Any]] = {}
    for r in rows(doc):
        if str(r.get("date") or "") > date:
            continue
        acct = str(r.get("account") or "")
        if account is not None and acct not in ("", account):
            continue
        out[(acct, str(r.get("symbol") or ""))] = r
    return out


def inventory_on(doc: Dict[str, Any], date: str,
                 account: Optional[str] = None,
                 eps: Optional[Callable[[str], float]] = None
                 ) -> List[Dict[str, Any]]:
    """The pools held at the end of `date` in the shape of a gains
    file's `inventory` (symbol, qty, total_cost, currency,
    position_start_date, last_acq_date, deferred_wash; `account` on a
    per-account pool), the engine's flat tolerance applied: `eps(symbol)`,
    the Canadian engine's (pool_eps.pool_qty_eps) when not given — the US
    engine's is US_QTY_EPS."""
    from taxjson.lib.pool_eps import pool_qty_eps
    from taxjson.lib.row_types import MATERIAL_COST
    eps = eps or pool_qty_eps
    out: List[Dict[str, Any]] = []
    for (acct, sym), r in pools_on(doc, date, account).items():
        q = float(r.get("qty") or 0.0)
        # A sub-micro position that carries a real cost is held
        # (CA-ACB-01; backlog TJ-0649).
        if not q or (abs(q) <= eps(sym)
                     and abs(float(r.get("total_cost") or 0.0))
                     < MATERIAL_COST):
            continue
        out.append({"symbol": sym, "qty": q,
                    **({"account": acct} if acct else {}),
                    "total_cost": float(r.get("total_cost") or 0.0),
                    "currency": r.get("currency") or "",
                    "position_start_date": r.get("position_start_date"),
                    "last_acq_date": r.get("last_acq_date"),
                    "deferred_wash": M.units(float(
                        r.get("deferred_wash") or 0.0), 4),
                    "unknown_cost": bool(r.get("unknown_cost")),
                    **({"account_premiums": r["account_premiums"]}
                       if r.get("account_premiums") else {})})
    return out


def short_rows_on(doc: Dict[str, Any], date: str,
                  eps: Optional[Callable[[str], float]] = None
                  ) -> List[Dict[str, Any]]:
    """The account-held shorts beside a long, or beside the symbol's flat
    pool while it holds a denied loss's waiting addition (0 units, the
    deferred amount: backlog TJ-2712), at the end of `date`, as
    `inventory_on` rows with their `account` and `short_pool`. A short
    with neither beside it is left out: it is the pool's own row in the
    timeline (engine/ca/pools), 0 units there once covered."""
    shorts = doc.get("shorts")
    if not isinstance(shorts, dict):
        return []
    pools = pools_on(doc, date)
    out = []
    for r in inventory_on(shorts, date, eps=eps):
        pooled = pools.get(("", r["symbol"]))
        if pooled is not None and float(pooled.get("qty") or 0.0) >= 0:
            out.append(dict(r, short_pool=True))
    return out

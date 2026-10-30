"""The engines' cost timeline (backlog TJ-0417).

A gains engine run asked for it (`GainsRequest.cost_timeline`,
`taxjson-gains --cost-timeline FILE`) records, beside the gains, each
pool's quantity and cost after every row of the books that changed it,
dated, in the order the engine walked the rows: Canada's pools (one per
symbol across the book's taxable accounts, ITA s.47: account "") and
the US engine's lots per account (per-account FIFO basis: the account's
own). The readers take the pools from it instead of replaying the books
on their own, so they hold what the engine holds: `taxjson t1135` (the
cost amounts, src/taxjson/bin/taxjson_t1135.py `walk_costs`), the
year-end snapshot `close-year` records and `handoff` compares
(lib/handoff `snapshot`) and `taxjson list --date`
(src/taxjson/bin/taxjson_run.py `cmd_positions`).

A row's date is the day from which its state holds, on the engine's own
date basis (`basis`: the settlement date, or the trade date on a
trade-basis project); the pools at the end of a day are each pool's last
row dated on or before it (`pools_on`). Rows that share a `seq` came
from one row of the books (a rename empties one pool and fills
another). A pool's `total_cost` follows the inventory's convention: a
short's is negative (the opening proceeds credited).

A short pool beside a long (Canada: a future, or a share short the broker
marks) is no property held, so it has no row here while the long is held.
Its part `shorts`, a timeline of the same shape, has each account's part
of it, per (account, symbol): `short_rows_on`.
"""

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

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
# currency, position_start_date, last_acq_date.
State = Tuple[float, float, float, bool, str, Optional[str], Optional[str]]
Label = Tuple[str, str]                 # (account, symbol)

_FLAT: State = (0.0, 0.0, 0.0, False, "", None, None)


class Recorder:
    """Collects the timeline while an engine walks its rows.

    `state_of(label)` is the pool's state now (None: no pool). The
    engine calls `touch` after each row with the pools the row may have
    changed, and `sweep` at the end of each day (and of the books): it
    checks every pool still held, so a change the row's own pools do not
    name (a basis addition landing in another account's lot) is in the
    day's state. `finish` dates the rows."""

    def __init__(self, state_of: Callable[[Label], Optional[State]]):
        self._state_of = state_of
        self._last: Dict[Label, State] = {}
        self._held: Dict[Label, None] = {}      # ordered set
        self._rows: List[Tuple[int, Label, State, str]] = []
        # The id of the book row each `seq` is (the engine appends one
        # per row it walks; the Canadian engine does): the document's
        # `row_ids`, so a reader can place a row of the books between
        # two steps of one day (T1135: a custody account's units at
        # each step, backlog TJ-0646).
        self.row_ids: List[str] = []

    def touch(self, seq: int, labels: Iterable[Label],
              renamed: Optional[Dict[Label, str]] = None) -> None:
        for lab in labels:
            self._check(seq, lab, (renamed or {}).get(lab, ""))

    def sweep(self, seq: int, labels: Iterable[Label] = ()) -> None:
        for lab in list(self._held) + list(labels):
            self._check(seq, lab, "")

    def _check(self, seq: int, lab: Label, renamed: str) -> None:
        st = self._state_of(lab) or _FLAT
        if st == self._last.get(lab, _FLAT) and not renamed:
            return
        self._last[lab] = st
        if abs(st[0]) > 1e-12 or abs(st[1]) > 1e-9:
            self._held[lab] = None
        else:
            self._held.pop(lab, None)
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
            rows.append([seq, d, lab[0], lab[1], float(qty),
                         round_half_up(cost, 4), round_half_up(dw, 4),
                         *rest, renamed])
        doc = {"version": VERSION, "basis": basis,
               "fields": list(FIELDS), "rows": rows}
        if self.row_ids and len(self.row_ids) == n:
            doc["row_ids"] = list(self.row_ids)
        return doc


# ------------------------------------------------------------ readers

def rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The timeline's rows as dicts, by date, then in the engine's
    order."""
    fields = list(doc.get("fields") or FIELDS)
    out = [dict(zip(fields, r)) for r in doc.get("rows") or []]
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
    the Canadian engine's (core.pool_qty_eps) when not given — the US
    engine's is US_QTY_EPS."""
    from taxjson.lib.core import MATERIAL_COST, pool_qty_eps
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
                    "deferred_wash": round(float(
                        r.get("deferred_wash") or 0.0), 4),
                    "unknown_cost": bool(r.get("unknown_cost"))})
    return out


def short_rows_on(doc: Dict[str, Any], date: str,
                  eps: Optional[Callable[[str], float]] = None
                  ) -> List[Dict[str, Any]]:
    """The account-held shorts beside a long at the end of `date`, as
    `inventory_on` rows with their `account` and `short_pool`. A short
    with no long beside it is left out: it is the pool's own row in the
    timeline (core._ca_pool_state)."""
    shorts = doc.get("shorts")
    if not isinstance(shorts, dict):
        return []
    pools = pools_on(doc, date)
    out = []
    for r in inventory_on(shorts, date, eps=eps):
        pooled = pools.get(("", r["symbol"]))
        if pooled is not None and float(pooled.get("qty") or 0.0) > 0:
            out.append(dict(r, short_pool=True))
    return out

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
        return {"version": VERSION, "basis": basis,
                "fields": list(FIELDS), "rows": rows}


# ------------------------------------------------------------ readers

def rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The timeline's rows as dicts, by date, then in the engine's
    order."""
    fields = list(doc.get("fields") or FIELDS)
    out = [dict(zip(fields, r)) for r in doc.get("rows") or []]
    out.sort(key=lambda r: (str(r.get("date") or ""), int(r.get("seq")
                                                          or 0)))
    return out


def check(doc: Any) -> Optional[str]:
    """None for a timeline this version reads, else what is wrong."""
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return "not a cost timeline"
    if doc.get("version") != VERSION:
        return (f"cost timeline version {doc.get('version')!r}, this "
                f"taxjson reads {VERSION}")
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
    from taxjson.lib.core import pool_qty_eps
    eps = eps or pool_qty_eps
    out: List[Dict[str, Any]] = []
    for (acct, sym), r in pools_on(doc, date, account).items():
        q = float(r.get("qty") or 0.0)
        if abs(q) <= eps(sym):
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

"""Ticker changes as DATED events (owner decision on audit A2-0197).

A rename is an event in the books, like a trade or a dividend: on its
date the position, the ACB / basis lots and the acquisition dates carry
from the old symbol to the new one, and the superficial-loss / wash-sale
rule treats OLD before the date and NEW after it as one security. It is
booked as a SPLIT row with a `symbol_new` (ratio 1 for a pure ticker
change) and comes from one of three places:

  - a broker row (IB's Corporate Actions, the corp-action stage's
    `rename` election, a parser's own SPLIT);
  - a `.tt` line `SPLIT <date> <time> OLD NEW 1`;
  - a ticker.map line `RENAME OLD NEW YYYY-MM-DD` — the pipeline books
    the SPLIT row in every account that held OLD before the date (none
    is added where the broker already booked the event).

After the date OLD is NOT automatically the same security. A trade in
OLD after the rename date is either the broker still booking the
renamed shares under the old ticker, or another company that now uses
the ticker — the export cannot tell. Such rows are listed by
`taxjson renames`, stop `taxjson run --strict`, and stay a separate
security until the user declares them in ticker.map on the dated line:

  RENAME OLD NEW YYYY-MM-DD late=fold      the late OLD rows ARE the
                                           renamed shares: booked as NEW
  RENAME OLD NEW YYYY-MM-DD late=separate  another security: kept as OLD

An UNDATED ticker.map rename (`GLOBAL OLD NEW`, or `RENAME OLD NEW`
without a date) keeps its old meaning — every row of OLD, at any date,
is NEW — and `taxjson renames` suggests the dated form when a broker
event gives the date.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LATE_FOLD = "fold"
LATE_SEPARATE = "separate"
LATE_CHOICES = (LATE_FOLD, LATE_SEPARATE)

# The `source` stamped on a SPLIT row the pipeline books from a dated
# ticker.map RENAME line.
TICKER_MAP_SOURCE = "ticker.map"

# Rows that move a position (a late one names the old ticker after its
# rename).
_POSITION_ACTIONS = ("BUYSELL", "ASSIGN", "TRANSFER", "OPENING_BALANCE")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Copies of one rename booked on two dates (two brokers) are one event.
WINDOW_DAYS = 7


@dataclass(frozen=True)
class DatedRename:
    """One dated ticker.map RENAME line."""
    old: str
    new: str
    date: str
    late: str = ""          # "", "fold" or "separate"
    where: str = ""         # "ticker.map:<lineno>"
    line: str = ""


class RenameConflict(ValueError):
    """A dated ticker.map RENAME disagrees with the broker's own event."""


def parse_rename_tail(tokens: List[str]) -> Tuple[str, str]:
    """(date, late) of a dated RENAME line's tokens after `OLD NEW`
    (`YYYY-MM-DD [late=fold|late=separate]`). Raises ValueError with a
    one-line reason."""
    if not tokens:
        raise ValueError("no date")
    date = tokens[0]
    if not _DATE_RE.match(date):
        raise ValueError(f"the date {date!r} is not YYYY-MM-DD")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"the date {date!r} is not a calendar date") \
            from None
    late = ""
    rest = tokens[1:]
    if rest:
        tok = rest[0].lower()
        if not tok.startswith("late=") or tok[5:] not in LATE_CHOICES:
            raise ValueError(f"{rest[0]!r} is not late=fold or "
                             f"late=separate")
        late = tok[5:]
        rest = rest[1:]
    if rest:
        raise ValueError(f"extra token(s) {rest} — notes go after `#`")
    return date, late


# ----------------------------------------------------------------- rows

def _g(r: Any, k: str, default: Any = "") -> Any:
    if isinstance(r, dict):
        v = r.get(k, default)
    else:
        v = getattr(r, k, default)
    return default if v is None else v


def _days(a: str, b: str) -> Optional[int]:
    try:
        return abs((datetime.strptime(str(a)[:10], "%Y-%m-%d")
                    - datetime.strptime(str(b)[:10], "%Y-%m-%d")).days)
    except ValueError:
        return None


def _option_root(sym: str) -> Optional[str]:
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    if sym and is_option_symbol(sym):
        return parse_option_underlying(sym)
    return None


def names_symbol(sym: str, old: str) -> bool:
    """The row's symbol is `old` or an option on it."""
    return sym == old or _option_root(sym) == old


def rename_target(r: Any) -> str:
    """The new symbol of a SPLIT row that renames ('' otherwise)."""
    if str(_g(r, "action")).upper() != "SPLIT":
        return ""
    new = str(_g(r, "symbol_new") or "").strip()
    return "" if not new or new == str(_g(r, "symbol")) else new


def row_source(r: Any) -> str:
    """Where a rename row came from, for the report."""
    src = str(_g(r, "source") or "")
    if src == TICKER_MAP_SOURCE:
        return "ticker.map line"
    if src.lower().endswith(".tt"):
        return f".tt line ({src})"
    if _g(r, "corp_event_id"):
        return "broker corporate action"
    return f"broker row ({src})" if src else "broker row"


def rename_events(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """The rename events in `rows` (dicts or TaxTransactions, any
    accounts): one per (old, new) with copies within WINDOW_DAYS merged
    onto the earliest date. Each: date, old, new, ratio, sources,
    accounts, rows."""
    evs: List[Dict[str, Any]] = []
    for r in sorted((r for r in rows if rename_target(r)),
                    key=lambda r: (str(_g(r, "date")), str(_g(r, "symbol")))):
        old, new = str(_g(r, "symbol")), rename_target(r)
        d = str(_g(r, "date"))[:10]
        for e in evs:
            gap = _days(e["date"], d)
            if (e["old"], e["new"]) == (old, new) and gap is not None \
                    and gap <= WINDOW_DAYS:
                break
        else:
            e = {"date": d, "old": old, "new": new,
                 "ratio": float(_g(r, "quantity", 0.0) or 0.0),
                 "sources": [], "accounts": [], "rows": []}
            evs.append(e)
        e["rows"].append(r)
        s = row_source(r)
        if s not in e["sources"]:
            e["sources"].append(s)
        a = str(_g(r, "_acct") or _g(r, "account") or "")
        if a and a not in e["accounts"]:
            e["accounts"].append(a)
    return evs


def late_rows(rows: Iterable[Any], events: List[Dict[str, Any]]
              ) -> List[Tuple[Any, Dict[str, Any]]]:
    """(row, event) for each position row that names a renamed ticker
    (or an option on it) AFTER that ticker's rename date (a row on the
    date itself is the renamed security, as the engines class it): the
    LATEST rename of the ticker on or before the row's trade date."""
    by_old: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        by_old.setdefault(e["old"], []).append(e)
    for v in by_old.values():
        v.sort(key=lambda e: e["date"])
    if not by_old:
        return []
    out = []
    for r in rows:
        if str(_g(r, "action")).upper() not in _POSITION_ACTIONS:
            continue
        sym = str(_g(r, "symbol"))
        cands = by_old.get(sym)
        if cands is None:
            root = _option_root(sym)
            cands = by_old.get(root) if root else None
        if not cands:
            continue
        d = str(_g(r, "date"))[:10]
        hit = None
        for e in cands:
            if d > e["date"]:
                hit = e
        if hit is not None:
            out.append((r, hit))
    return out


def matching_lines(dated: Iterable[DatedRename], event: Dict[str, Any],
                   mapping: Optional[Dict[str, str]] = None
                   ) -> List[DatedRename]:
    """The dated ticker.map lines that name `event`: the line's OLD
    (through the stage's undated renames, `mapping`) is the event's old
    symbol and its date is within WINDOW_DAYS of the event's."""
    from taxjson.bin.taxjson_ticker_map import map_symbol
    out = []
    for dr in dated:
        gap = _days(dr.date, event["date"])
        if (map_symbol(dr.old, mapping or {}) == event["old"]
                and gap is not None and gap <= WINDOW_DAYS):
            out.append(dr)
    return out


def declared_late(dated: Iterable[DatedRename], event: Dict[str, Any],
                  mapping: Optional[Dict[str, str]] = None) -> str:
    """The late= choice the ticker.map declares for `event` ('' when
    none)."""
    for dr in matching_lines(dated, event, mapping):
        if dr.late:
            return dr.late
    return ""


# ------------------------------------------------- the pipeline stage

def apply_dated_renames(txs: List[Any], dated: Iterable[DatedRename],
                        *, stream=None) -> List[Any]:
    """Book each dated ticker.map RENAME in `txs` (TaxTransactions on
    their RAW symbols, before the undated renames apply): a SPLIT row
    OLD -> NEW (ratio 1) on the date in every account that holds OLD
    before it, unless the account already books a rename of OLD within
    WINDOW_DAYS (to NEW: nothing to add; to another symbol: refused).
    `late=fold` re-books every OLD row (and option on OLD) on or after
    the date as NEW. Returns the new list."""
    from taxjson.bin.taxjson_ticker_map import map_symbol
    from taxjson.lib.core import TaxTransaction
    stream = stream or sys.stderr
    dated = list(dated)
    if not dated:
        return txs
    out = list(txs)
    for dr in dated:
        # Accounts with OLD position rows before the date.
        held: Dict[str, Any] = {}
        for t in out:
            if (t.action in _POSITION_ACTIONS + ("SPLIT",)
                    and t.symbol == dr.old and (t.date or "") < dr.date):
                held[t.account] = t
        booked: Dict[str, str] = {}
        for t in out:
            new = rename_target(t)
            if new and t.symbol == dr.old:
                gap = _days(t.date, dr.date)
                if gap is not None and gap <= WINDOW_DAYS:
                    booked[t.account] = new
                    if new != dr.new:
                        raise RenameConflict(
                            f"{dr.where}: RENAME {dr.old} {dr.new} "
                            f"{dr.date} disagrees with the rename "
                            f"{dr.old} -> {new} on {t.date} the books "
                            f"already carry (account {t.account}) — fix "
                            f"the ticker.map line ({dr.line!r})")
        added = []
        for acct in sorted(held):
            if acct in booked:
                continue
            last = held[acct]
            added.append(TaxTransaction(
                action="SPLIT", date=dr.date, time="00:00:00",
                date_settle=dr.date, symbol=dr.old, symbol_new=dr.new,
                quantity=1.0, price=0.0, net_amount=0.0,
                currency=last.currency or "", account=acct,
                description=(f"Ticker change {dr.old}→{dr.new} "
                             f"({dr.where} RENAME; no disposition: "
                             f"basis, acquisition dates and identity "
                             f"carried)"),
                source=TICKER_MAP_SOURCE))
        if added:
            print(f"note: {dr.where}: RENAME {dr.old} -> {dr.new} on "
                  f"{dr.date} booked in {len(added)} account(s) "
                  f"({', '.join(t.account for t in added)}).",
                  file=stream)
            # Placed before the first row dated on or after the rename
            # (the rows are already in the pipeline's order).
            at = next((i for i, t in enumerate(out)
                       if (t.date or "") >= dr.date), len(out))
            out[at:at] = added
        if dr.late == LATE_FOLD:
            moved = 0
            m = {dr.old: dr.new}
            for t in out:
                if (t.action != "SPLIT" and (t.date or "") >= dr.date
                        and names_symbol(t.symbol, dr.old)):
                    t.symbol = map_symbol(t.symbol, m)
                    moved += 1
            if moved:
                print(f"note: {dr.where}: {moved} {dr.old} row(s) on or "
                      f"after {dr.date} booked as {dr.new} (late=fold).",
                      file=stream)
    return out


# ------------------------------------------------- reading a project

def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read {path} ({e}) — re-run `taxjson "
                         f"run` to rebuild it") from None
    rows = doc.get("transactions", []) if isinstance(doc, dict) else doc
    return [r for r in rows if isinstance(r, dict)] \
        if isinstance(rows, list) else []


def _ticker_map(root: Path):
    """(TickerMap, base-stage undated renames) of the project, or
    (None, {}) without a map."""
    p = Path(root) / "ticker.map"
    if not p.exists():
        return None, {}
    from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                merge_renames)
    tmap = _parse_map_file(p)[0]
    try:
        ren = merge_renames(tmap, to_base=True)
    except ValueError:
        ren = {}
    return tmap, ren


def _book_rows(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    cache = Path(root) / "work"
    for acct in sorted(cfg.get("accounts") or {}):
        for r in _load_rows(cache / f"{acct}_base.json"):
            r = dict(r)
            r["_acct"] = acct
            out.append(r)
    return out


def _raw_rename_rows(root: Path, acct: str) -> List[Dict[str, Any]]:
    """The account's parsed rows BEFORE ticker.map (broker parses,
    corp-action files, .tt conversions), for the dated-form hint."""
    from taxjson.lib.pipeline import tt_json_path
    cache = Path(root) / "work"
    try:
        lines = (cache / f"{acct}_sources.list").read_text(
            encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):       # re-audit A2-0795
        return []
    files = []
    for ln in lines:
        kind, _, name = ln.partition("/")
        if kind == "tt":
            files.append(tt_json_path(cache, acct, name))
        elif kind:
            files += [cache / f"{acct}_{kind}.json",
                      cache / f"{acct}_{kind}_corp.json"]
    rows = []
    for f in dict.fromkeys(files):
        try:
            rows += [r for r in _load_rows(f) if rename_target(r)]
        except ValueError:
            continue
    return rows


def _walk_states(rows: List[Dict[str, Any]]):
    """Replay one account's rows: {event row id: (qty, cost)} of the
    renamed symbol just before each rename row (book cost: buys at their
    cost, sales at the average cost, ADJUST rows added; no superficial-
    loss adjustments)."""
    state: Dict[str, List[float]] = {}
    snap: Dict[int, Tuple[float, float]] = {}

    def key(r):
        a = str(r.get("action") or "")
        return (str(r.get("date") or ""),
                0 if a in ("OPENING_BALANCE",) else 1 if a == "SPLIT" else 2,
                str(r.get("time") or ""))
    for r in sorted(rows, key=key):
        a = str(r.get("action") or "").upper()
        sym = str(r.get("symbol") or "")
        st = state.setdefault(sym, [0.0, 0.0])
        q = float(r.get("quantity") or 0.0)
        if a == "SPLIT":
            new = rename_target(r)
            snap[id(r)] = (st[0], st[1])
            if q:
                st[0] *= q
            if new:
                dst = state.setdefault(new, [0.0, 0.0])
                dst[0] += st[0]
                dst[1] += st[1]
                state[sym] = [0.0, 0.0]
        elif a in _POSITION_ACTIONS:
            net = abs(float(r.get("net_amount") or 0.0))
            if q > 0:
                st[1] += net
            elif q < 0 and st[0] > 1e-9:
                st[1] -= st[1] * min(1.0, -q / st[0])
            st[0] += q
            if abs(st[0]) < 1e-9:
                st[1] = 0.0
        elif a == "ADJUST":
            st[1] += float(r.get("net_amount") or 0.0)
    return snap


def report(root: Path, cfg: Dict[str, Any],
           account: Optional[str] = None, *,
           undated: bool = True) -> Dict[str, Any]:
    """`taxjson renames`: every rename event with its date, source and
    the position / book cost it carried per account; every late trade
    in an old ticker with its resolution; the undated ticker.map renames
    (with the dated form when a broker event gives the date)."""
    root = Path(root)
    tmap, ren = _ticker_map(root)
    dated = list(getattr(tmap, "dated", ()) or ()) if tmap else []
    rows = _book_rows(root, cfg)
    base_cur = ((cfg.get("settings") or {}).get("base_currency") or "")
    events = rename_events(rows)
    by_acct: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_acct.setdefault(r["_acct"], []).append(r)
    snaps: Dict[int, Tuple[float, float]] = {}
    for acct, rs in by_acct.items():
        snaps.update(_walk_states(rs))
    accounts = cfg.get("accounts") or {}
    out_events = []
    for e in events:
        if account and account not in e["accounts"]:
            continue
        carried = []
        for r in e["rows"]:
            q, c = snaps.get(id(r), (0.0, 0.0))
            acct = r["_acct"]
            carried.append({
                "account": acct,
                "sheltered": (accounts.get(acct) or {}).get("type")
                != "taxable",
                "qty": round(q, 6),
                "qty_after": round(q * (e["ratio"] or 1.0), 6),
                "book_cost": round(c, 2)})
        lines = [dr.where for dr in matching_lines(dated, e, ren)]
        out_events.append({
            "date": e["date"], "old": e["old"], "new": e["new"],
            "ratio": e["ratio"], "sources": e["sources"],
            "ticker_map_lines": lines, "accounts": e["accounts"],
            "carried": carried, "currency": base_cur,
            "late": declared_late(dated, e, ren)})
    late = []
    for r, e in late_rows(rows, events):
        if account and r["_acct"] != account:
            continue
        choice = declared_late(dated, e, ren)
        late.append({
            "account": r["_acct"], "date": str(r.get("date") or "")[:10],
            "action": r.get("action"), "symbol": r.get("symbol"),
            "qty": float(r.get("quantity") or 0.0),
            "renamed_to": e["new"], "rename_date": e["date"],
            "resolution": choice or "unresolved"})
    want_undated, undated = undated, []
    if tmap is not None and want_undated:
        raw: List[Dict[str, Any]] = []
        for acct in sorted(accounts):
            if account and acct != account:
                continue
            for r in _raw_rename_rows(root, acct):
                r = dict(r)
                r["_acct"] = acct
                raw.append(r)
        raw_events = rename_events(raw)
        undated_rn = set(getattr(tmap, "undated_rename", ()) or ())
        # GLOBAL (and undated RENAME) only: TOBASE / JOURNAL join two
        # listings of one security, they are not ticker changes.
        for frm, to in sorted(tmap.glob.items()):
            hint = [ev["date"] for ev in raw_events
                    if ev["old"] == frm and ev["new"] == to]
            undated.append({"rule": ("RENAME" if frm in undated_rn
                                     else "GLOBAL"),
                            "old": frm, "new": to,
                            "broker_dates": sorted(set(hint))})
    unresolved = sum(1 for x in late if x["resolution"] == "unresolved")
    return {"renames": out_events, "late": late, "undated": undated,
            "unresolved": unresolved}


def unresolved_late(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The late trades no ticker.map line declares (`run --strict`)."""
    return [x for x in report(root, cfg, undated=False)["late"]
            if x["resolution"] == "unresolved"]


def _hang(d, text: str, indent: str = "", hang: str = "  ") -> None:
    """A paragraph of `d` (lib/out.Doc) with a hanging indent."""
    from taxjson.lib.out import wrap
    for ln in wrap(text, d.w, indent, hang):
        d.line(ln)


def render(doc: Dict[str, Any], width_: Optional[int] = None) -> List[str]:
    """The renames in the house layout (docs/output-style.md): each
    dated rename with the positions it carried and its late trades, the
    ticker.map lines to copy never wrapped; the last line says whether a
    declaration is needed."""
    from taxjson.lib.out import Doc
    evs, late, undated = doc["renames"], doc["late"], doc["undated"]
    d = Doc(f"RENAMES ({len(evs)})", width_=width_)
    d.blank()
    if not evs:
        d.para("No dated rename in the books.")
    for k, e in enumerate(evs):
        if k:
            d.blank()
        ratio = "" if abs((e["ratio"] or 1.0) - 1.0) < 1e-12 else \
            f" x{e['ratio']:g}"
        _hang(d, f"{e['date']}  {e['old']} -> {e['new']}{ratio}  "
               f"source: {', '.join(e['sources'])}"
               + (f" (also {', '.join(e['ticker_map_lines'])})"
                  if e["ticker_map_lines"]
                  and "ticker.map line" not in e["sources"] else ""),
               "", "  ")
        for c in e["carried"]:
            cost = ("" if c["sheltered"] else
                    f"; book cost {c['book_cost']:,.2f} {e['currency']} "
                    f"carried")
            d.item(f"{c['account']}: held {c['qty']:g} {e['old']} "
                   f"-> {c['qty_after']:g} {e['new']}{cost}", "  ")
        mine = [x for x in late if x["symbol"] and x["rename_date"]
                == e["date"] and x["renamed_to"] == e["new"]]
        if mine:
            res = mine[0]["resolution"]
            word = {"fold": "declared the renamed shares (late=fold)",
                    "separate": "declared another security "
                                "(late=separate)"}.get(res, "UNRESOLVED")
            _hang(d, f"{len(mine)} trade(s) in {e['old']} on or "
                   f"after {e['date']}: {word}", "  ", "    ")
            for x in mine:
                d.line(f"    {x['date']}  {x['account']:<8} "
                       f"{x['action']:<8} {x['symbol']} {x['qty']:+g}")
            if res == "unresolved":
                _hang(d, "Declare one in ticker.map — the broker still books "
                       f"the renamed shares as {e['old']} (late=fold), or "
                       f"another company now uses {e['old']} "
                       f"(late=separate):", "  ", "    ")
                d.line(f"    RENAME {e['old']} {e['new']} {e['date']} "
                       f"late=fold")
                d.line(f"    RENAME {e['old']} {e['new']} {e['date']} "
                       f"late=separate")
                _hang(d, "Until then they are a separate security and "
                       "`taxjson run --strict` stops.", "  ", "    ")
    if undated:
        d.section(f"UNDATED RENAMES IN ticker.map ({len(undated)})")
        d.para("Every row of the old symbol, at any date, is the new one.",
               "  ")
        for u in undated:
            d.line(f"  {u['rule']} {u['old']} {u['new']}")
            for dd in u["broker_dates"]:
                _hang(d, f"the broker books this change on {dd}; the dated "
                       f"form is:", "    ", "      ")
                d.line(f"      RENAME {u['old']} {u['new']} {dd}")
    d.blank()
    n = doc["unresolved"]
    d.para(f"{n} trade(s) in an old ticker after its rename need a "
           f"ticker.map declaration." if n else
           "No unresolved trade in an old ticker after its rename.")
    return d.lines()

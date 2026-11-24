"""Year-to-year hand-off: record what a closed year carried forward, and
check that the next year's project starts from exactly that.

Every disposition must be reported once, in the year it settles, and the
cost carried into the next year must match what the earlier return
claimed. Three things break that when each year is its own project:

- a trade made in the closed year that settles in the next one is left
  out of both years (or reported in both);
- the next year's opening positions or cost differ from the closed
  year's year-end books (a lot dropped from the opening file, a deferred
  superficial loss not carried, a return of capital not applied);
- a correction to the closed year is applied in one project and not the
  other, so a gain is claimed twice or not at all.

`close-year` stores the pieces a later project needs (`record_fields`):
every taxable disposition, the positions and cost at Dec 31, and the
trades that settle after Dec 31. When the return was prepared with
another tool, the dispositions it actually filed can be imported
alongside (`filed_dispositions`). `tjs handoff`, run in the next
year's project, compares that record with the new project's own books
(`check`).

Positions at Dec 31 are the engine's own pools on that day, read from
the cost timeline of one run over the FULL history (lib/cost_timeline):
a December loss whose window runs into January is judged with the
January trades, exactly as the return was.
"""
from __future__ import annotations

import csv
import json
import tempfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from taxjson.project.accounts import accounts_of
from taxjson.project.settings import settings_of
from taxjson.lib.tx_load import finite_num
from taxjson.lib.filed_locks import _d
from taxjson.lib.futures_prefixes import canonical_futures_symbol
from taxjson.model import money as M
from taxjson.model.result import GainRow, Result, ResultShapeError

RECORD_VERSION = 2
QTY_TOL = 1e-6
ACB_ABS_TOL = 1.00          # CAD/USD: rounding across two runs
ACB_REL_TOL = 1e-4

RunGains = Callable[[List[str], Path], None]


class BooksError(ValueError):
    """A work/ book the hand-off reads exists but cannot be read."""


def _year_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, str) and v.strip().isdigit()


def record_problem(record: Any) -> Optional[str]:
    """Why a prior-year record (filed/<year>.json, read by `tjs
    handoff`) cannot be checked, or None. Its fields are read bare by
    `check` — a list for the record, a text quantity, a sale with no
    date or no gain, a non-numeric year — and each was a traceback
    (re-audit A2-0162, A2-0473, A2-0475, A2-0476). A version-1 record
    (no year_end) passes: the caller refuses it with its own advice."""
    if not isinstance(record, dict):
        return (f"expected a JSON object (a close-year record), got "
                f"{type(record).__name__}")
    if not _year_like(record.get("year")):
        return f'"year" is {record.get("year")!r}, not a year'
    sv = record.get("schema_version")
    if sv is not None and not _year_like(sv):
        return f'"schema_version" is {sv!r}, not a number'
    ye = record.get("year_end")
    if ye is not None:
        if not isinstance(ye, dict):
            return '"year_end" must be an object of account groups'
        for g, pos in ye.items():
            if not isinstance(pos, dict):
                return f'"year_end" {g!r} must be an object of positions'
            for sym, p in pos.items():
                if not isinstance(p, dict):
                    return f'"year_end" {g}/{sym} must be an object'
                for k in ("qty", "acb"):
                    if not _num(p.get(k)):
                        return (f'"year_end" {g}/{sym}: {k} is '
                                f'{p.get(k)!r}, not a number')
                if p.get("deferred") is not None \
                        and not _num(p.get("deferred")):
                    return (f'"year_end" {g}/{sym}: deferred is '
                            f'{p.get("deferred")!r}, not a number')
    lp = _lots_problem(record.get("year_end_lots"))
    if lp:
        return lp
    sp = _shorts_problem(record.get("year_end_shorts"))
    if sp:
        return sp
    lists = {"settle_next_year": (("symbol", "date", "date_settle"),
                                  ("qty",)),
             "dispositions": (("symbol",), ("qty", "proceeds", "gain")),
             "filed_dispositions": (("symbol",),
                                    ("qty", "proceeds", "gain"))}
    for key, (texts, nums) in lists.items():
        rows = record.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list):
            return f'"{key}" must be a list'
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                return f'"{key}" entry {i} must be an object'
            for k in texts:
                if not isinstance(r.get(k), str) or not r.get(k):
                    return (f'"{key}" entry {i}: {k} is {r.get(k)!r}, not '
                            f'text')
            for k in nums:
                if not _num(r.get(k)):
                    return (f'"{key}" entry {i} ({r.get("symbol")}): {k} '
                            f'is {r.get(k)!r}, not a number')
    br = record.get("boundary_rows")
    if br is not None and not isinstance(br, list):
        return '"boundary_rows" must be a list'
    return None


def _label(path: Path) -> str:
    p = Path(path)
    return f"{p.parent.name}/{p.name}"


def _work_doc(path: Path) -> Dict[str, Any]:
    """A work/ book or gains file through json_input.read_work_doc: an
    unreadable, truncated or wrong-shape file (a bare list, a row that
    is not an object, a number field holding text) is a BooksError
    naming it, never an AttributeError (A2-0794 / A2-1396)."""
    from taxjson.lib.json_input import read_work_doc
    p = Path(path)
    try:
        return read_work_doc(p)
    except ValueError as e:
        raise BooksError(f"could not read {_label(p)} ({e}) — re-run "
                         f"`tjs run` to rebuild it") from None


def _gain_rows(path: Path) -> Tuple[GainRow, ...]:
    """A work/ gains file's `transactions` as typed rows (model/result
    `GainRow`; only that part is copied), after `_work_doc`'s checks:
    they refuse a `transactions` that is not a list of objects and a
    money field that is not a finite number (every money value a GainRow
    reads is in json_input `_NUM_FIELDS`), so its rows read."""
    return Result.from_json({"transactions": _work_doc(path).get("transactions")}).transactions


def _rows(path: Path) -> List[Dict[str, Any]]:
    """The rows of a work/<acct>_base.json; [] when the file does not
    exist (an account with no inputs). An unreadable or damaged file is
    a BooksError naming it: close-year wrote a lock with no year-end
    positions, and handoff reported every lot as missing from the
    opening file, at rc 0 and with nothing on stderr (A2-0346,
    A2-1137)."""
    p = Path(path)
    if not p.exists():
        return []
    return list(_work_doc(p).get("transactions") or [])


def _book(cache: Path, account: str) -> List[Dict[str, Any]]:
    """The account's rows less those the pass across accounts cut at its
    snapshot, so a walk through another account's rename holds the units
    the merged books do (backlog TJ-1168, TJ-1268)."""
    from taxjson.lib.positions_check import blended_pool_rows, without_pool_cut
    return without_pool_cut(_rows(cache / f"{account}_base.json"),
                            blended_pool_rows(cache, account), account)


def _check_books(path: Path) -> None:
    """Validate one source book with the engine's own loader BEFORE it is
    merged into a temporary file: a damaged row was reported against
    /tmp/taxjson-handoff-*/equity_full.json, a file the user never saw
    and that is gone when the message is read (A2-1143)."""
    p = Path(path)
    if not p.exists():
        return
    from taxjson.lib.tx_load import load_transactions
    try:
        load_transactions(p)
    except Exception as e:                          # noqa: BLE001
        raise BooksError(f"could not read {_label(p)} ({e}) — re-run "
                         f"`tjs run` to rebuild it")


def _bdate(r: Dict[str, Any], basis: str) -> Optional[date]:
    if basis == "settle":
        return _d(r.get("date_settle")) or _d(r.get("date"))
    return _d(r.get("date"))


def _lots_problem(lots: Any) -> Optional[str]:
    """Why a record's `year_end_lots` ({group: {symbol: [lot]}}, a US
    record's lots at Dec 31) is not the shape close-year writes, or
    None (absent: an older or a Canadian record)."""
    if lots is None:
        return None
    if not isinstance(lots, dict):
        return '"year_end_lots" must be an object of account groups'
    for g, by_sym in lots.items():
        if not isinstance(by_sym, dict):
            return f'"year_end_lots" {g!r} must be an object of securities'
        for sym, rows in by_sym.items():
            if not isinstance(rows, list):
                return f'"year_end_lots" {g}/{sym} must be a list of lots'
            for i, r in enumerate(rows):
                if not isinstance(r, dict):
                    return (f'"year_end_lots" {g}/{sym} lot {i} must be '
                            f'an object')
                for k in ("qty", "basis"):
                    if not _num(r.get(k)):
                        return (f'"year_end_lots" {g}/{sym} lot {i}: {k} '
                                f'is {r.get(k)!r}, not a number')
                for k in ("date", "account"):
                    if not isinstance(r.get(k), str):
                        return (f'"year_end_lots" {g}/{sym} lot {i}: {k} '
                                f'is {r.get(k)!r}, not text')
    return None


def _shorts_problem(shorts: Any) -> Optional[str]:
    """Why a record's `year_end_shorts` ({group: {symbol: {qty, acb}}}, a
    Canadian record's shorts beside a long) is not that shape, or None."""
    if shorts is None:
        return None
    if not isinstance(shorts, dict):
        return '"year_end_shorts" must be an object of account groups'
    for g, pos in shorts.items():
        if not isinstance(pos, dict):
            return f'"year_end_shorts" {g!r} must be an object of securities'
        for sym, p in pos.items():
            if not (isinstance(p, dict) and _num(p.get("qty"))
                    and _num(p.get("acb"))):
                return (f'"year_end_shorts" {g}/{sym} must have a number '
                        f'qty and acb')
    return None


def groups(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """Taxable accounts split into the pools the engine blends:
    equities together, crypto together — in taxjson.toml order, the
    order the run blends them in: rows of two accounts at one moment
    follow it (CA-DATE-14 / US-DATE-13). Sorted by name, the snapshot
    replayed a same-moment sale after another account's buy and
    recorded a deferral the return never had (A2-1556)."""
    out: Dict[str, List[str]] = {"equity": [], "crypto": []}
    for name, a in accounts_of(cfg).items():
        if not a.taxable:
            continue
        out["crypto" if a.crypto else "equity"].append(name)
    return {g: n for g, n in out.items() if n}


def _basis(settings: Dict[str, Any]) -> str:
    """The date basis in force (lib/country.settings_tax_date)."""
    from taxjson.lib.country import settings_tax_date
    return settings_tax_date(settings)


# ------------------------------------------------------------ snapshot

def snapshot(cache: Path, cfg: Dict[str, Any], as_of: str,
             run_gains: RunGains, common_flags: List[str],
             phantoms: Optional[Path] = None,
             lots_out: Optional[Dict[str, Any]] = None,
             shorts_out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{group: {symbol: {qty, acb, deferred}}} for the taxable pools as
    of the end of `as_of` (the engine's date basis). `phantoms`: the
    project's missing-history path (lib/missing_history.
    missing_history_arg: the project folder, for the accounts' .tt
    OPENING cost=unknown lines), None without either (the parameter
    keeps its pre-rename name). `lots_out`: a US project's lots at
    `as_of` are put in it, {group: {symbol: [lot]}} (the engine's
    `lots_at`: each lot's account, acquisition and holding-period
    dates, units, basis and deferral in FIFO order; a FIFO sale's
    basis and term are a lot's, not the position's: backlog TJ-0554).
    `shorts_out`: a Canadian project's shorts beside a long at `as_of`
    are put in it, {group: {symbol: {qty, acb}}}: the main rows are
    property held, so such a short has no row, while a short alone is
    the pool's own (cost_timeline.short_rows_on; backlog TJ-1134), unless
    the pool holds a denied loss's waiting addition: then the timeline's
    row of the symbol is the wait (0 units: no property held, left out
    here as for a book with no short) and the short is beside it
    (backlog TJ-2712).

    The pools are the engine's own: one run over each group's FULL
    history, as the return was computed, with its cost timeline
    (`--cost-timeline`, lib/cost_timeline; backlog TJ-0417), read at
    `as_of`. Every superficial-loss addition is where the engine
    applied it, in its order (a re-run of the rows to Dec 31 with the
    additions as ADJUST rows put one ahead of a same-moment sale:
    backlog TJ-0348, TJ-0371), a trust ROC on its record date, and a
    December loss is judged with the January trades."""
    from taxjson.lib import cost_timeline as CT
    settings = settings_of(cfg)
    from taxjson.lib.country import settings_country
    try:
        us = settings_country(settings) == "usa"
    except Exception:                               # noqa: BLE001
        us = False
    sheltered = cache / "sheltered_base.json"
    out: Dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="taxjson-handoff-") as td:
        tdp = Path(td)
        for g, names in groups(cfg).items():
            rows = []
            for n in names:
                _check_books(cache / f"{n}_base.json")
                rows += _book(cache, n)
            if not rows:
                continue
            full = tdp / f"{g}_full.json"
            full.write_text(json.dumps({"transactions": rows}))
            if us and g == "crypto":
                # §1091 does not reach digital assets: the pipeline runs
                # a US project's crypto books --no-wash (US-WASH-13), so
                # the record does too (backlog TJ-0297). The group keeps
                # its accounts together (moves between them carry their
                # lots) and FIFO per account (--per-account-basis in
                # common_flags).
                tail = ["--taxable", "--no-wash"] + common_flags
            else:
                # Canada: the s.53(1)(f) additions; US: the §1091(d)
                # adds to the replacement lots (A2-0353).
                tail = ["--taxable"] + common_flags
                if sheltered.exists() and g == "equity":
                    tail += ["--sheltered", str(sheltered)]
            if phantoms is not None:
                tail += ["--incomplete-history", str(phantoms)]
            tl_path = tdp / f"{g}_cost_timeline.json"
            want_lots = us and lots_out is not None
            if want_lots:
                tail += ["--lots-at", as_of]
            run_gains(tail + ["--cost-timeline", str(tl_path), str(full)],
                      tdp / f"{g}_full_gains.json")
            if want_lots:
                lots_out[g] = _lots_by_symbol(tdp / f"{g}_full_gains.json",
                                              names)
            try:
                tl = json.loads(tl_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise BooksError(f"the gains engine wrote no readable cost "
                                 f"timeline for {', '.join(names)} ({e})")
            problem = CT.check(tl)
            if problem:
                raise BooksError(f"{problem} — the taxjson-gains on the "
                                 f"PATH is not this taxjson's")
            pos: Dict[str, Dict[str, float]] = {}
            # The deferral still in each pool: the engine's own
            # deferred_wash tally (US: the §1091(d) adds; Canada: the
            # s.53(1)(f) additions, less what later sales released —
            # backlog TJ-0285).
            for h in CT.inventory_on(tl, as_of,
                                     eps=CT.us_eps if us else None):
                p = pos.setdefault(h["symbol"], {"qty": 0.0, "acb": 0.0,
                                                 "deferred": 0.0})
                p["qty"] += float(h["qty"])
                p["acb"] += float(h["total_cost"])
                p["deferred"] += float(h["deferred_wash"])
            for p in pos.values():
                p["qty"] = M.units(p["qty"], 8)
                p["acb"] = M.shown(p["acb"], 2)
                p["deferred"] = M.shown(p["deferred"], 2)
            out[g] = pos
            if shorts_out is not None and not us:
                beside: Dict[str, Dict[str, float]] = {}
                for h in CT.short_rows_on(tl, as_of):
                    s = beside.setdefault(h["symbol"],
                                          {"qty": 0.0, "acb": 0.0})
                    s["qty"] = M.units(s["qty"] + float(h["qty"]), 8)
                    s["acb"] = M.shown(s["acb"] + float(h["total_cost"]), 2)
                shorts_out[g] = beside
    return out


def _lots_by_symbol(gains: Path, names: List[str]
                    ) -> Dict[str, List[Dict[str, Any]]]:
    """{symbol: [lot]} of a gains run's `lots_at` (taxjson-gains
    --lots-at): each security's lots by account, in FIFO order within
    each account. A file with no list of lots is a BooksError saying
    the engine is not this taxjson's; a lot that is not an object, has
    no `symbol`, or has a `qty`, `basis` or `deferred` that does not read
    as a number, one naming the lot (the code before 6c stopped on both
    with a traceback)."""
    try:
        doc = json.loads(gains.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("lots_at"), list):
            raise KeyError("lots_at")
    except (OSError, ValueError, KeyError) as e:
        raise BooksError(f"the gains engine wrote no lots for "
                         f"{', '.join(names)} ({e}): the taxjson-gains "
                         f"on the PATH is not this taxjson's")

    def bad_lot(i: int, why: str) -> BooksError:
        return BooksError(f"the gains engine's lot {i} for "
                          f"{', '.join(names)} does not read ({why}): "
                          f"the taxjson-gains on the PATH is not "
                          f"this taxjson's")

    raw = doc["lots_at"]
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise bad_lot(i, f"it is {'null' if r is None else type(r).__name__}, not an object")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for i, lot in enumerate(Result.from_json({"lots_at": raw}).lots_at):
        try:
            # A null symbol is filed as "None", as it was.
            if "symbol" not in lot:
                raise ResultShapeError('"symbol" is missing')
            qty, basis = lot.quantity, lot.basis
            for key, value in (("qty", qty), ("basis", basis)):
                # The typed read is None for a null, "", [] or {} too.
                if value is None:
                    raise ResultShapeError(
                        f'"{key}" is {lot.get(key)!r}, not a number' if key in lot
                        else f'"{key}" is missing')
            row = {"account": str(lot.account or ""),
                   "date": str(lot.date or "")[:10],
                   "holding_start": str(lot.holding_start or lot.date or "")[:10],
                   "qty": M.units(float(qty), 8),
                   "basis": M.shown(float(basis), 2),
                   "deferred": M.shown(float(lot.deferred or 0.0), 2)}
        except ResultShapeError as e:
            raise bad_lot(i, str(e)) from None
        out.setdefault(str(lot.symbol), []).append(row)
    for sym in out:
        # A stable sort: FIFO order kept within each account.
        out[sym].sort(key=lambda x: x["account"])
    return out


# ---------------------------------------------------------- record

def straddlers(cache: Path, cfg: Dict[str, Any], year: int
               ) -> List[Dict[str, Any]]:
    """Taxable trades made in `year` that settle after Dec 31."""
    out = []
    for g, names in groups(cfg).items():
        for n in names:
            for r in _rows(cache / f"{n}_base.json"):
                if r.get("action") not in ("BUYSELL", "ASSIGN"):
                    continue
                t, s = _d(r.get("date")), _d(r.get("date_settle"))
                if t and s and t.year == year and s.year > year:
                    out.append({"group": g, "account": n,
                                "symbol": r.get("symbol"),
                                "date": str(t), "date_settle": str(s),
                                "qty": float(r.get("quantity") or 0.0),
                                "net": M.shown(float(r.get("net_amount")
                                                     or 0.0), 2)})
    out.sort(key=lambda x: (x["date"], x["symbol"] or ""))
    return out


def dispositions(gains_files: Dict[str, Path], year: int
                 ) -> List[Dict[str, Any]]:
    out = []
    for acct, p in sorted(gains_files.items()):
        if not Path(p).exists():
            continue
        # An unreadable gains file left its sales out of the lock in
        # silence (A2-1396).
        for t in _gain_rows(Path(p)):
            if t.gain is None or t.action:
                continue
            out.append({
                "account": t.account or acct,
                "symbol": t.symbol, "date": t.date,
                "date_settle": t.date_settle or t.date,
                "qty": float(t.quantity or 0.0),
                "proceeds": M.shown(float(t.proceeds or 0.0), 2),
                "cost": M.shown(float(t.cost or 0.0), 2),
                "gain": M.shown(float(t.gain or 0.0), 2),
                "denied": M.shown(float(t.disallowed_amount or 0.0), 2),
            })
    return out


_FILED_COLS = ("symbol", "date", "qty", "proceeds", "cost", "gain")


def load_filed_dispositions(path: Path, year: Optional[int] = None
                            ) -> List[Dict[str, Any]]:
    """The dispositions a return actually reported, when it was prepared
    with another tool. CSV with a header: symbol,date,qty,proceeds,cost,
    gain (account optional; date = the date the return used; amounts in
    the base currency; qty positive). `year`: the year being closed — a
    row dated in any other year is refused, naming each such line
    (TJ-0298)."""
    import io
    from taxjson.lib.brokerages.base import (BrokerageParseError,
                                             decode_broker_text)
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"{path}: not a file")
    # The decode funnel every broker export goes through: a UTF-16
    # (Excel "Unicode Text") save is read, any other encoding is one
    # line naming the file, not a codec message (A2-1138).
    try:
        text = decode_broker_text(p.read_bytes(), str(path))
    except BrokerageParseError as e:
        raise ValueError(str(e))
    except OSError as e:
        raise ValueError(f"{path}: cannot read ({e})")
    first = text.split("\n", 1)[0]
    delim = "\t" if ("\t" in first and "," not in first) else ","
    rows = []
    other: List[str] = []
    fh = io.StringIO(text, newline="")
    rd = csv.DictReader(fh, delimiter=delim)
    missing = [c for c in _FILED_COLS
               if c not in (rd.fieldnames or [])]
    if missing:
        raise ValueError(f"{path}: missing column(s) "
                         f"{', '.join(missing)} (need "
                         f"{', '.join(_FILED_COLS)})")
    for i, r in enumerate(rd, start=2):
        # A stray quote merges the following rows into one cell and
        # a filed disposition vanished from the record in silence
        # (A2-1136): a record wider than the header or a cell
        # holding a line break is refused, naming the line.
        if r.get(None) or any(
                isinstance(v, str) and ("\n" in v or "\r" in v)
                for k, v in r.items() if k is not None):
            raise ValueError(
                f"{path}: the record ending on line {rd.line_num} "
                f"holds a line break or more cells than the header — "
                f"an unescaped quote swallowed the next row(s). Fix "
                f"the quoting (double an inner quote: \"\") and "
                f"re-run.")
        short = [c for c in _FILED_COLS if r.get(c) is None]
        if short:
            # A short row ('SAMPLG.US,2024-05-14') was a TypeError
            # traceback from float(None) (A2-0769 / A2-1397).
            raise ValueError(f"{path}:{i}: bad row (no "
                             f"{', '.join(short)} cell)")
        if not r["symbol"].strip():
            # A blank symbol was written into the filed lock (A2-0769).
            raise ValueError(f"{path}:{i}: bad row (blank symbol)")
        try:
            rows.append({
                "account": (r.get("account") or "").strip(),
                # A future's `/` or `\` prefix is `F:`, as the books
                # spell it (lib/futures_prefixes, backlog TJ-5150).
                "symbol": canonical_futures_symbol(r["symbol"].strip()),
                "date": str(_d(r["date"]) or ""),
                "qty": abs(float(r["qty"])),
                "proceeds": M.shown(float(r["proceeds"]), 2),
                "cost": M.shown(float(r["cost"]), 2),
                "gain": M.shown(float(r["gain"]), 2)})
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"{path}:{i}: bad row ({e})")
        import math
        if not all(math.isfinite(rows[-1][k])
                   for k in ("qty", "proceeds", "cost", "gain")):
            raise ValueError(f"{path}:{i}: bad row (an amount is not a "
                             f"finite number)")
        if not rows[-1]["date"]:
            raise ValueError(f"{path}:{i}: bad date {r['date']!r}")
        if year is not None and not rows[-1]["date"].startswith(
                f"{int(year)}-"):
            other.append(f"line {i} ({rows[-1]['symbol']} "
                         f"{rows[-1]['date']})")
    if other:
        raise ValueError(
            f"{path}: {len(other)} row(s) dated outside {int(year)}, the "
            f"year being closed: {', '.join(other[:5])}"
            + (f" and {len(other) - 5} more" if len(other) > 5 else "")
            + f" — the file holds the sales the {int(year)} return "
            f"reported (each year's lock records its own return): remove "
            f"the other years' rows, or fix their dates")
    return rows


_TRADES = ("BUYSELL", "ASSIGN")
_INCOME = ("DIVIDEND", "DIVIDEND_IN_LIEU", "INTEREST", "ADJUST")


def _income_rules(settings: Dict[str, Any]):
    from taxjson.lib.income_dating import IncomeRules
    try:
        return IncomeRules.from_settings(settings)
    except ValueError:
        return IncomeRules(country="")


def _eff_date(r: Dict[str, Any], basis: str, ir) -> Optional[date]:
    """The date a row counts on in the books: a trade's trade or settle
    date (the date basis), an income row's income date (a Canadian
    trust's record date, a US RIC January dividend's Dec 31), a return
    of capital's record date (characterize/income_dating)."""
    a = str(r.get("action") or "").upper()
    if a in _TRADES:
        return _bdate(r, basis)
    try:
        return _d(ir.row_date(r)) or _d(r.get("date"))
    except Exception:                               # noqa: BLE001
        return _d(r.get("date"))


def _cents(v: Any) -> float:
    return M.shown(M.of(v or 0), 2)


def _income(r: Dict[str, Any]) -> float:
    """What the income summary counts for an income row (lib/
    income_summary `income_amount`: a dividend's gross), a trade's net:
    the amount a boundary row is compared at (backlog TJ-5066)."""
    from taxjson.lib.income_summary import income_amount
    if str(r.get("action") or "").upper() in _TRADES:
        return _cents(r.get("net_amount"))
    return _cents(income_amount(r))


_CENT, _HALF_CENT = Decimal("0.01"), Decimal("0.005")


def _c(v: Any) -> Decimal:
    """An amount the broker stated, in cents: half up from the decimal
    as written, past a computation's residue beyond the sixth place
    (57.37499999 is 57.375, so 57.38)."""
    return M.of(v or 0).quantize(Decimal("1e-6"), ROUND_HALF_UP).quantize(
        _CENT, ROUND_HALF_UP)


def _moved(a: Any, b: Any) -> bool:
    """Two amounts the broker stated differ in cents (`_c`: 57.375 and
    57.38 are both 57.38; backlog TJ-5180)."""
    return _c(a) != _c(b)


def _near(n: Any, xn: Any) -> bool:
    """One row's amount in two readings, within the identity tolerance:
    a cent, or 0.5% of the record's."""
    d, x = M.of(n or 0), M.of(xn or 0)
    return abs(d - x) <= max(_CENT, _HALF_CENT * abs(x))


class _Stated(NamedTuple):
    """A row's amounts as the broker stated them, each in the currency
    it was stated in (backlog TJ-5065, TJ-5180): `income` in `cur` (a
    dividend's gross, else the net: income_summary `income_amount`), the
    row's own `net` in `cur` (None: the merge netted its day's TAX rows
    into it, merge2 `reconcile_dividend_tax`), and `withheld`
    {currency: tax withheld}, each TAX row's in its own currency, else
    the row's gross less its net, in cents; `day` instead, where two or
    more dividends of the security share the day's TAX rows: the day's
    withholding, compared once (`_day_withheld`; backlog TJ-5185)."""
    cur: str
    income: Any
    net: Any
    withheld: Dict[str, Any]
    day: Optional[Dict[str, Any]] = None


def _stated(cur: str, income: Any, net: Any,
            taxes: Optional[Dict[str, Any]] = None,
            day: Optional[Dict[str, Any]] = None) -> _Stated:
    if taxes is None:
        w = _c(income) - _c(net) if net is not None else 0
        taxes = {cur: w} if w else {}
    return _Stated(cur, income, net, dict(taxes),
                   dict(day) if day is not None else None)


def _stated_of(x: Dict[str, Any]) -> Optional[_Stated]:
    """A record row's `_Stated`, or None when it has none (written
    before backlog TJ-5065, or its amounts were not the broker's)."""
    cur = str(x.get("native_currency") or "").strip().upper()
    if not cur or x.get("native_income") is None:
        return None
    w, day = x.get("native_withheld"), x.get("native_withheld_day")
    if isinstance(day, dict):
        return _stated(cur, x["native_income"], None, {}, day)
    return _stated(cur, x["native_income"], x.get("native_net"),
                   w if isinstance(w, dict) else None)


def _is_tax(t: Dict[str, Any]) -> bool:
    return (str(t.get("action") or "").upper() == "TAX"
            or str(t.get("type") or "").lower() == "tax")


class _Native:
    """A base row's amounts as the broker stated them (`of`, backlog
    TJ-5065, TJ-5180). An equity account's are its native books,
    work/<account>_raw.json: a row the conversion relabelled carries the
    currency it was in (`trade_currency`, bin/taxjson_convert_currency)
    and is found there by its id (one merge of the same sources); any
    other row is in its own currency already. A crypto account's are
    work/<account>_filled.json's rows the fill stage left as parsed
    (_sorted.json's): one it priced from the price cache is taxjson's
    figure, not the broker's. Each book is read once."""

    def __init__(self, cache: Path):
        self.cache = Path(cache)
        self.books: Dict[str, Any] = {}

    def _book(self, account: str, crypto: bool) -> Tuple[
            Path, Optional[Dict[str, List[Dict[str, Any]]]],
            Dict[Tuple[str, str, str], Dict[str, Any]],
            Optional[Dict[str, Dict[str, Any]]]]:
        """(path, {id: rows} or None when the file is missing, {(broker
        account, symbol, day): {"~currency": the TAX rows' sum, and ""
        the day's dividends' count}}, crypto: the parse {id: row})."""
        if account not in self.books:
            p = self.cache / f"{account}{'_filled' if crypto else '_raw'}.json"
            rows: Optional[Dict[str, List[Dict[str, Any]]]] = None
            taxes: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            if p.exists():
                rows = {}
                for t in _rows(p):
                    k = (str(t.get("account") or ""),
                         str(t.get("symbol") or ""),
                         str(t.get("date") or "")[:10])
                    if _is_tax(t):
                        c = "~" + str(t.get("currency") or "").strip().upper()
                        d = taxes.setdefault(k, {})
                        d[c] = d.get(c, 0) + M.of(t.get("net_amount") or 0)
                    elif str(t.get("action") or "").upper() == "DIVIDEND":
                        d = taxes.setdefault(k, {})
                        d[""] = d.get("", 0) + 1
                    if t.get("id") not in (None, ""):
                        rows.setdefault(str(t["id"]), []).append(t)
            sp = self.cache / f"{account}_sorted.json"
            parsed = ({str(t["id"]): t for t in _rows(sp)
                       if t.get("id") not in (None, "")} if crypto else None)
            self.books[account] = (p, rows, taxes, parsed)
        return self.books[account]

    @staticmethod
    def _as_parsed(n: Dict[str, Any], parsed: Dict[str, Dict[str, Any]]
                   ) -> bool:
        """A crypto row the fill stage did not price: its parse has the
        same currency and amounts (taxjson_audit `priced_by_fill`)."""
        p = parsed.get(str(n.get("id")))
        return p is not None and (
            str(p.get("currency") or "").upper()
            == str(n.get("currency") or "").upper()) and all(
            abs(M.of(p.get(k) or 0) - M.of(n.get(k) or 0)) <= Decimal("1e-9")
            for k in ("net_amount", "gross_amount"))

    def of(self, r: Dict[str, Any], account: str, crypto: bool = False
           ) -> Tuple[Optional[_Stated], Optional[str]]:
        """(base row `r`'s `_Stated`, None), or (None, why it has none:
        `missing:<book>`, `absent:<book>` or `computed`)."""
        from taxjson.lib.income_summary import income_amount
        cur = str(r.get("currency") or "").strip().upper()
        was = str(r.get("trade_currency") or "").strip().upper()
        p, rows, taxes, parsed = self._book(account, crypto)
        a = str(r.get("action") or "").upper()
        # The row of its id, action and day, then symbol (two: neither).
        alike = [t for t in (rows or {}).get(str(r.get("id")), [])
                 if str(t.get("action") or "").upper() == a
                 and str(t.get("date") or "")[:10]
                 == str(r.get("date") or "")[:10]] \
            if r.get("id") not in (None, "") else []
        if len(alike) > 1:
            alike = [t for t in alike if t.get("symbol") == r.get("symbol")]
        n = alike[0] if len(alike) == 1 else None
        if n is None:
            if crypto or (was and was != cur):
                return None, (f"missing:{_label(p)}" if rows is None
                              else f"absent:{_label(p)}")
            n = r                       # in its own currency already
        elif crypto and not self._as_parsed(n, parsed or {}):
            return None, "computed"
        ncur = str(n.get("currency") or "").strip().upper()
        if not ncur:
            return None, f"absent:{_label(p)}"
        day = (taxes.get((str(n.get("account") or ""),
                          str(n.get("symbol") or ""),
                          str(n.get("date") or "")[:10]))
               if a == "DIVIDEND" else None) or {}
        # The day's TAX rows as written (a TAX row with no currency is in
        # the dividend's): this dividend's, or, shared by two or more
        # dividends of the day, the day's (backlog TJ-5185).
        w: Dict[str, Any] = {}
        for c, v in day.items():
            if c:
                w[c[1:] or ncur] = w.get(c[1:] or ncur, 0) + v
        if a in _TRADES:
            income: Any = n.get("net_amount") or 0
        else:
            income = income_amount(n)
        if w and day.get("", 0) > 1:
            return _stated(ncur, income, None, {}, w), None
        return _stated(ncur, income, None if w else n.get("net_amount") or 0,
                       w or None), None


def boundary_rows(cache: Path, cfg: Dict[str, Any], year: int
                  ) -> List[Dict[str, Any]]:
    """Every taxable trade and income row of December `year` and January
    `year + 1`, by its own date or its tax date, for the next year's
    hand-off: the rows an export re-dated after the close, or the two
    projects put on different sides of Dec 31 — income a trust record
    date, a RIC January dividend or a local_timezone re-dating moves
    across the boundary, and a fill the overnight shift moved into
    January (A2-0120, A2-0343, A2-0344, A2-0670, A2-0675). Each row
    keeps its base amounts, `net` and `income` (what the income summary
    counts: a dividend's gross; backlog TJ-5066), and the amounts the
    broker stated (`_Native`), which the check compares to the cent
    (TJ-5065, TJ-5180): `native_currency`, `native_income` and
    `native_net` as written, or for a dividend whose day's TAX rows the
    merge netted into it, `native_withheld` {currency: amount} (shared
    by two or more dividends of the security: `native_withheld_day`,
    the day's; TJ-5185); none for a row whose amounts are taxjson's. A converted row whose native book
    is missing is a BooksError: re-run `tjs run`."""
    settings = settings_of(cfg)
    basis = _basis(settings)
    ir = _income_rules(settings)
    native = _Native(cache)
    lo, hi = date(year, 12, 1), date(year + 1, 1, 31)
    out = []
    for g, names in groups(cfg).items():
        for n in names:
            for r in _rows(cache / f"{n}_base.json"):
                a = str(r.get("action") or "").upper()
                if a not in _TRADES + _INCOME:
                    continue
                eff, raw = _eff_date(r, basis, ir), _d(r.get("date"))
                if not ((eff and lo <= eff <= hi)
                        or (raw and lo <= raw <= hi)):
                    continue
                row = {"group": g, "account": n, "action": a,
                       "symbol": r.get("symbol"), "date": str(raw),
                       "date_settle": str(_d(r.get("date_settle")) or raw),
                       # Full precision: a coin reward of
                       # 0.000123456789 rounded to 8 places no longer
                       # matched its own row.
                       "quantity": float(r.get("quantity") or 0.0),
                       "net": M.shown(float(r.get("net_amount") or 0.0), 2),
                       "income": _income(r),
                       "tax_date": str(eff) if eff else None}
                nat, why = native.of(r, n, g == "crypto")
                if nat is not None:
                    row.update(native_currency=nat.cur,
                               native_income=nat.income)
                    if nat.net is not None:
                        row["native_net"] = nat.net
                    else:
                        row["native_withheld" if nat.day is None
                            else "native_withheld_day"] = {
                            c: M.from_money(M.of(v)) for c, v in sorted(
                                (nat.withheld if nat.day is None
                                 else nat.day).items())}
                elif (str(why).startswith("missing:")
                      and r.get("trade_currency") not in (None, "",
                                                          r.get("currency"))):
                    raise BooksError(
                        f"{why[8:]} is missing: the books before "
                        f"conversion, which the Dec-31 rows are recorded "
                        f"from (backlog TJ-5181) — re-run `tjs run` to "
                        f"rebuild it")
                out.append(row)
    out.sort(key=lambda x: (x["date"], x["symbol"] or "", x["action"]))
    return out
def record_fields(root: Path, cfg: Dict[str, Any], year: int,
                  gains_files: Dict[str, Path], run_gains: RunGains,
                  common_flags: List[str],
                  filed_csv: Optional[Path] = None) -> Dict[str, Any]:
    cache = Path(root) / "work"
    from taxjson.lib.missing_history import missing_history_arg
    mh_file = missing_history_arg(root)
    lots: Dict[str, Any] = {}
    shorts: Dict[str, Any] = {}
    rec = {
        "record_version": RECORD_VERSION,
        "date_basis": _basis(settings_of(cfg)),
        "dispositions": dispositions(gains_files, year),
        "year_end": snapshot(cache, cfg, f"{year}-12-31", run_gains,
                             common_flags, mh_file, lots_out=lots,
                             shorts_out=shorts),
        "settle_next_year": straddlers(cache, cfg, year),
        "boundary_rows": boundary_rows(cache, cfg, year),
    }
    if lots:
        # US: each lot held at Dec 31 (backlog TJ-0554).
        rec["year_end_lots"] = lots
    if shorts:
        # Canada: each short beside a long at Dec 31 (backlog TJ-1134).
        rec["year_end_shorts"] = shorts
    if filed_csv is not None:
        fd = load_filed_dispositions(filed_csv, year)
        rec["filed_dispositions"] = fd
        rec["filed_totals"] = {
            "dispositions": len(fd),
            "proceeds": M.shown(sum(r["proceeds"] for r in fd), 2),
            "gain": M.shown(sum(r["gain"] for r in fd), 2),
            "source": str(filed_csv)}
    return rec


# ------------------------------------------------------- the record

class RecordError(ValueError):
    """A prior-year lock (filed/<year>.json) whose fields are not the
    shape close-year writes. One line naming the file and the field;
    each wrong type was a TypeError / KeyError traceback somewhere in
    check() (A2-0803)."""


def _num(v: Any, *, none_ok: bool = False) -> bool:
    return none_ok if v is None else finite_num(v)


def _txt(v: Any, *, none_ok: bool = True) -> bool:
    return isinstance(v, str) or (none_ok and v is None)


# Per list: {field: (check, required)}.
def _withheld_ok(v: Any) -> bool:
    """A boundary row's `native_withheld` / `native_withheld_day`:
    {currency: amount}."""
    return isinstance(v, dict) and all(
        _txt(k, none_ok=False) and _num(w) for k, w in v.items())


_REC_LISTS = {
    "dispositions": {"symbol": (lambda v: _txt(v, none_ok=False)
                                and bool(v.strip()), True),
                     "gain": (_num, True), "qty": (_num, False),
                     "proceeds": (_num, False), "cost": (_num, False),
                     "date": (_txt, False), "date_settle": (_txt, False)},
    "filed_dispositions": {"symbol": (lambda v: _txt(v, none_ok=False)
                                      and bool(v.strip()), True),
                           "gain": (_num, True), "qty": (_num, True),
                           "proceeds": (_num, True), "cost": (_num, False),
                           "date": (_txt, False)},
    "settle_next_year": {"symbol": (lambda v: _txt(v, none_ok=False)
                                    and bool(v.strip()), True),
                         "qty": (_num, True),
                         "net": (lambda v: _num(v, none_ok=True), False),
                         "date": (_txt, False),
                         "date_settle": (_txt, False)},
    "boundary_rows": {"symbol": (_txt, False), "action": (_txt, False),
                      "quantity": (lambda v: _num(v, none_ok=True), False),
                      "net": (lambda v: _num(v, none_ok=True), False),
                      "income": (lambda v: _num(v, none_ok=True), False),
                      "native_currency": (_txt, False),
                      "native_net": (lambda v: _num(v, none_ok=True), False),
                      "native_income": (lambda v: _num(v, none_ok=True),
                                        False),
                      "native_withheld": (_withheld_ok, False),
                      "native_withheld_day": (_withheld_ok, False),
                      "date": (_txt, False), "tax_date": (_txt, False)},
}


def validate_record(record: Any, path: Any = "the prior-year record"
                    ) -> Dict[str, Any]:
    """`record` when every field the hand-off reads has the shape
    close-year writes; else RecordError naming `path` and the field."""
    def bad(field: str, what: str) -> RecordError:
        return RecordError(f"{path}: {field} is not {what} — fix the "
                           f"lock or restore it from git (or re-close "
                           f"that year with `tjs close-year --force` "
                           f"in its project)")

    if not isinstance(record, dict):
        raise bad("the document", "a JSON object")
    for k in ("schema_version", "year", "record_version"):
        v = record.get(k)
        if v is None and (k != "year" or "year_end" not in record):
            continue
        if isinstance(v, bool) or not (
                isinstance(v, int) or (isinstance(v, str)
                                       and v.strip().isdigit())):
            raise bad(k, "an integer")
    for k in ("closed_at", "date_basis"):
        if not _txt(record.get(k)):
            raise bad(k, "text")
    for key, fields in _REC_LISTS.items():
        rows = record.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise bad(key, "a list")
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                raise bad(f"{key}[{i}]", "an object")
            for f, (ok, required) in fields.items():
                if f not in r:
                    if required:
                        raise bad(f"{key}[{i}].{f}", "present")
                    continue
                if not ok(r[f]):
                    raise bad(f"{key}[{i}].{f}", f"valid ({r[f]!r})")
    ye = record.get("year_end")
    if ye is not None:
        if not isinstance(ye, dict):
            raise bad("year_end", "an object")
        for g, pos in ye.items():
            if not isinstance(pos, dict):
                raise bad(f"year_end.{g}", "an object")
            for sym, p in pos.items():
                if not isinstance(p, dict):
                    raise bad(f"year_end.{g}.{sym}", "an object")
                for f in ("qty", "acb", "deferred"):
                    if f in p and not _num(p[f]):
                        raise bad(f"year_end.{g}.{sym}.{f}", "a number")
    lp = (_lots_problem(record.get("year_end_lots"))
          or _shorts_problem(record.get("year_end_shorts")))
    if lp:
        raise RecordError(f"{path}: {lp} (fix the lock or restore it "
                          f"from git, or re-close that year with "
                          f"`tjs close-year --force` in its project)")
    ot = record.get("option_timing")
    if ot is not None and not isinstance(ot, dict):
        raise bad("option_timing", "an object")
    # The one damage check every reader of a lock applies
    # (filed_locks.lock_damage): the carryforwards block, the locked
    # figures, and a year field naming another year than the file's —
    # the hand-off checked "2023 into 2024" from 2024.json and said
    # everything carried forward (2026-10-25 audit year-end#6).
    from taxjson.lib.filed_locks import file_year, filed_rows_problem, lock_damage
    prob = lock_damage(record, file_year(Path(str(path))), str(path))
    if prob:
        raise RecordError(f"{prob} — fix the lock or restore it from git "
                          f"(or re-close that year with `tjs "
                          f"close-year --force` in its project)")
    # A record whose filed dispositions hold another year's sales (an
    # older release wrote it): its filed totals and the loss it carried
    # are not that year's return, so the hand-off stops until the year
    # is re-closed with its own rows (backlog TJ-0347).
    prob = filed_rows_problem(record, file_year(Path(str(path))),
                              str(path))
    if prob:
        raise RecordError(prob)
    return record


def load_record(path: Any) -> Dict[str, Any]:
    """Read and validate a prior-year lock (utf-8, a BOM tolerated:
    A2-0776; a key written twice in one object refused, as every lock
    reader does: filed_locks.load_lock_json). Any problem is a
    RecordError naming the file."""
    from taxjson.lib.filed_locks import load_lock_json
    p = Path(path)
    try:
        doc = load_lock_json(p)
    except OSError as e:
        raise RecordError(f"{p}: cannot read ({e.strerror or e})") \
            from None
    except UnicodeDecodeError as e:
        raise RecordError(f"{p}: not UTF-8 text ({e.reason} at byte "
                          f"{e.start})") from None
    except ValueError as e:
        raise RecordError(f"{p}: not valid JSON ({e}) — restore it "
                          f"from git") from None
    return validate_record(doc, p)


# ------------------------------------------------------------ check

def _root_sym(sym: str) -> str:
    """Symbol with a leading exchange/currency difference ignored, for
    matching another tool's spelling (SAMPLG.US vs SAMPLG)."""
    from taxjson.lib.markets import strip_listing_suffix
    return strip_listing_suffix((sym or "").upper())


def _redescribed_options(prev: Dict[str, Any], now: Dict[str, Any]
                         ) -> List[Tuple[str, str]]:
    """(closed-year symbol, this project's symbol) pairs that are ONE
    option position under two roots: the record holds it only as one,
    the opening only as the other, with the same expiry, right, strike,
    listing, quantity and cost and roots naming the same company
    (option_symbols.root_matches_stock: ABC / ABC.B, ABC / ABC1). RBC
    re-describes a contract between yearly exports, and the opening
    must use this year's spelling (audit A2-0006) — reporting that as a
    missing lot pushed the user to the symbol that books the close as a
    new write."""
    from taxjson.lib.option_symbols import _OCC_OPTION_RE, root_matches_stock
    zero = {"qty": 0.0, "acb": 0.0}
    gone = [s for s in prev if abs(prev[s]["qty"]) > QTY_TOL
            and abs(now.get(s, zero)["qty"]) <= QTY_TOL]
    new = [s for s in now if abs(now[s]["qty"]) > QTY_TOL
           and abs(prev.get(s, zero)["qty"]) <= QTY_TOL]
    out: List[Tuple[str, str]] = []
    used: set = set()
    for a in sorted(gone):
        ma = _OCC_OPTION_RE.match(a)
        if not ma:
            continue
        hits = []
        for b in sorted(new):
            mb = _OCC_OPTION_RE.match(b)
            if (not mb or b in used or mb.group(2) != ma.group(2)
                    or (mb.group(3) or "") != (ma.group(3) or "")):
                continue
            ra, rb = ma.group(1), mb.group(1)
            if not (root_matches_stock(ra, rb)
                    or root_matches_stock(rb, ra)):
                continue
            if abs(now[b]["qty"] - prev[a]["qty"]) > \
                    QTY_TOL * max(1.0, abs(prev[a]["qty"])) \
                    or not _acb_close(now[b]["acb"], prev[a]["acb"]):
                continue
            hits.append(b)
        if len(hits) == 1:
            used.add(hits[0])
            out.append((a, hits[0]))
    return out


def _qty_eq(a: float, b: float) -> bool:
    """Quantities equal within QTY_TOL, relative for sub-unit amounts: the
    absolute floor called 9.4e-7 BTC and 5.6e-7 (or 0) equal, so a dust
    difference between the closed year and the opening was "no
    difference" (A2-1133; taxjson-diff's values_equal, S029-18)."""
    d = abs(float(a) - float(b))
    return (d <= QTY_TOL * max(1.0, abs(a), abs(b))
            and d <= QTY_TOL * max(abs(a), abs(b), 1e-12))


def _lot_seqs(lots: List[Dict[str, Any]]
              ) -> Dict[str, List[Dict[str, Any]]]:
    """{account: its lots in FIFO order}."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for x in lots:
        out.setdefault(str(x.get("account") or ""), []).append(x)
    return out


def _same_lots(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    """Whether two lists of a security's lots (the record's and the
    opening's) are the same lot by lot: each account's lots in FIFO
    order, each with the same acquisition and holding-period dates,
    units and basis. Accounts named differently in the two projects
    are paired by their lots (a renamed account holds the same lots)."""
    sa, sb = _lot_seqs(a), _lot_seqs(b)
    if len(sa) != len(sb):
        return False
    if set(sa) == set(sb):
        return all(_same_seq(sa[k], sb[k]) for k in sa)
    # Renamed accounts: each of the record's sequences takes an equal one
    # of the opening's (the same-named account first). Pairing them by a
    # sort on dates and units alone left two accounts tied on those in
    # name order, so a rename that swapped the order paired each with the
    # other's basis (backlog TJ-0919).
    left = dict(sb)
    for k, xa in sa.items():
        cand = ([k] if k in left else []) + [j for j in left if j != k]
        hit = next((j for j in cand if _same_seq(xa, left[j])), None)
        if hit is None:
            return False
        del left[hit]
    return True


def _same_seq(xa: List[Dict[str, Any]], xb: List[Dict[str, Any]]) -> bool:
    """Two accounts' lots of a security the same, lot by lot in FIFO
    order: dates, holding-period start, units and basis."""
    if len(xa) != len(xb):
        return False
    for x, y in zip(xa, xb):
        if (x.get("date") != y.get("date")
                or (x.get("holding_start") or x.get("date"))
                != (y.get("holding_start") or y.get("date"))
                or not _qty_eq(float(x["qty"]), float(y["qty"]))
                or not _acb_close(float(x["basis"]), float(y["basis"]))):
            return False
    return True


def _lots_text(lots: List[Dict[str, Any]], limit: int = 6) -> str:
    """'1 acquired 2024-03-01 at 10.00, ...' (the account named when
    there is more than one; at most `limit` lots)."""
    many = len({x.get("account") for x in lots}) > 1
    parts = []
    for x in lots[:limit]:
        t = (f"{float(x['qty']):g} acquired {x.get('date')} at "
             f"{M.shown(float(x['basis']), 2):,.2f}")
        hs = x.get("holding_start")
        if hs and hs != x.get("date"):
            t += f" (held from {hs})"
        if many:
            t = f"{x.get('account') or '(no account)'}: {t}"
        parts.append(t)
    if len(lots) > limit:
        parts.append(f"and {len(lots) - limit} more")
    return ", ".join(parts)


def _lots_why(ry: int, closed: List[Dict[str, Any]],
              opening: List[Dict[str, Any]]) -> str:
    """The why of a security whose units and total basis match the
    record but whose lots do not (backlog TJ-0554)."""
    return (f"the same units and total basis, but not lot by lot: the "
            f"{ry} books hold {_lots_text(closed)}; this project opens "
            f"with {_lots_text(opening)}. A FIFO sale here takes another "
            f"basis or holding period than the {ry} books would give it, "
            f"so its gain or term differs. Fix the opening's lots (their "
            f"dates and costs: the older export, or a .tt line per lot) "
            f"to the {ry} books', or re-close {ry} if its books are the "
            f"wrong side.")


def _acb_close(a: float, b: float) -> bool:
    return abs(a - b) <= max(ACB_ABS_TOL, ACB_REL_TOL * max(abs(a), abs(b)))


def _filed_by_symbol(rec: Dict[str, Any]) -> Tuple[Dict[str, float],
                                                   Dict[str, float]]:
    book: Dict[str, float] = {}
    for d in rec.get("dispositions") or []:
        k = _root_sym(d["symbol"])
        book[k] = book.get(k, 0.0) + d["gain"]
    filed: Dict[str, float] = {}
    for d in rec.get("filed_dispositions") or []:
        k = _root_sym(d["symbol"])
        filed[k] = filed.get(k, 0.0) + d["gain"]
    return book, filed


def _grant_in(timing: Dict[str, Any], wy: int) -> bool:
    """Whether a contract written in `wy` is on grant timing (premium
    taxed in the write year, ITA s.49(1)) under `timing`."""
    if str(timing.get("option_premium_timing") or "close").lower() \
            != "grant":
        return False
    since = timing.get("option_grant_since")
    try:
        return since is None or wy >= int(since)
    except (TypeError, ValueError):
        return True


def _timing_issues(cache: Path, cfg: Dict[str, Any],
                   record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Written options carried out of the closed year that the record
    and this project put on DIFFERENT premium timing (Canada): the
    closed return taxed the premium at the write (grant) and this
    project taxes it again at the close, or the closed return left it to
    the close (close timing) and this project puts it back in the closed
    year, where no return reports it. handoff said "Everything ... is
    here, once" over a premium taxed twice (A2-0037)."""
    rec_t = record.get("option_timing")
    if not isinstance(rec_t, dict) or "option_premium_timing" not in rec_t:
        return []
    settings = settings_of(cfg)
    from taxjson.lib.pipeline import option_timing_from_settings
    try:
        here_t = option_timing_from_settings(settings) or {}
    except ValueError:
        return []
    if not here_t:
        return []                       # no written-option timing (US)
    ry = int(record["year"])
    from taxjson.lib.tx_load import load_transactions
    from taxjson.lib.option_boundary import write_lots
    out: List[Dict[str, Any]] = []
    for g, names in groups(cfg).items():
        if g != "equity":
            continue
        txs = []
        for n in names:
            p = cache / f"{n}_base.json"
            if p.exists():
                txs += load_transactions(p)
        for lot in write_lots(txs, tax_date=_basis(settings)):
            wy = lot.write_year
            # (a write that is not in the books — a short opening
            # balance, a transfer in — has no known write year or
            # premium to place: option-boundary says so)
            if lot.broker_closing or lot.unknown_premium or wy > ry:
                continue
            carried = (lot.open_units > 1e-9
                       or any(int(c.date[:4]) > ry for c in lot.closes))
            if not carried:
                continue
            was, now = _grant_in(rec_t, wy), _grant_in(here_t, wy)
            if was == now:
                continue
            _since = rec_t.get("option_grant_since")
            if was:
                why = (f"written {lot.write_date} for {M.shown(lot.premium, 2):,.2f}: "
                       f"the {ry} record is on grant timing since "
                       f"{_since}, so the {wy} return taxed this premium; "
                       f"this project keeps the contract on close timing "
                       f"(option_grant_timing_since = "
                       f"{here_t.get('option_grant_since')}) and taxes it "
                       f"again when it closes. Set "
                       f"option_grant_timing_since = {_since} in this "
                       f"project, as the {ry} record has it.")
            else:
                why = (f"written {lot.write_date} for {M.shown(lot.premium, 2):,.2f}: "
                       f"the {ry} record is on "
                       + (f"grant timing since {_since}"
                          if str(rec_t.get("option_premium_timing"))
                          == "grant" else "close timing")
                       + f", so the {wy} return left this premium to the "
                       f"close; this project puts it back in {wy} "
                       f"(grant timing since "
                       f"{here_t.get('option_grant_since')}), where no "
                       f"return reports it. Set option_grant_timing_since "
                       f"= {ry + 1} (keep the closed year's contracts on "
                       f"close timing, as filed) or T1-ADJ {wy} to add the "
                       f"premium.")
            out.append({"group": g, "symbol": lot.symbol,
                        "account": lot.account, "written": lot.write_date,
                        "write_year": wy,
                        "premium": M.shown(lot.premium, 2),
                        "closed_year_timing": "grant" if was else "close",
                        "timing_here": "grant" if now else "close",
                        "why": why})
    return out


class _Here(NamedTuple):
    """A base row's amounts for the boundary check: `net` and `income`
    (lib/income_summary `income_amount`) in the base currency, `native`
    (`_Native.of`: as the broker stated them) and, without them, `why`."""
    net: float
    income: float
    native: Optional[_Stated]
    why: Optional[str]


def _here(r: Dict[str, Any], account: str,
          native: Optional[_Native] = None, crypto: bool = False) -> _Here:
    nat, why = (native.of(r, account, crypto) if native is not None
                else (None, None))
    return _Here(_cents(r.get("net_amount")), _income(r), nat, why)


def _space(h: _Here, x: Dict[str, Any]) -> Optional[_Stated]:
    """Record row `x`'s `_Stated` when both sides have one in one
    currency: the two are compared to the cent. None: in the base
    currency, within the tolerance (`_rough`)."""
    xs = _stated_of(x)
    if h.native is not None and xs is not None and xs.cur == h.native.cur:
        return xs
    return None


def _rough(h: _Here, x: Dict[str, Any]) -> Optional[str]:
    """Why a row and record row `x` are compared only within the
    tolerance (`_boundary_section` counts them; backlog TJ-5181), or None
    when they are compared to the cent."""
    if _space(h, x) is not None:
        return None
    if h.why:
        return h.why
    return "record" if _stated_of(x) is None else "currency"


def _w_moved(now: _Stated, was: _Stated) -> List[str]:
    """The currencies whose tax withheld differs to the cent; none where
    either side's is a day's shared by its dividends (`_day_withheld`
    compares that once)."""
    if now.day is not None or was.day is not None:
        return []
    return sorted(c for c in set(now.withheld) | set(was.withheld)
                  if _moved(now.withheld.get(c, 0), was.withheld.get(c, 0)))


def _changed(h: _Here, x: Dict[str, Any]) -> bool:
    """Whether the row `_same_row` matched to record row `x` was
    corrected since: its income (a dividend's gross) or a tax withheld
    differs to the cent as the broker stated them, each in its own
    currency, so a converted amount that moved only with the rate is no
    change (backlog TJ-5065, TJ-5066, TJ-5180); in the base currency, by
    more than the identity tolerance (an older record: its net matched,
    so no change, as before)."""
    xs = _space(h, x)
    if xs is not None and h.native is not None:
        return _moved(h.native.income, xs.income) or bool(
            _w_moved(h.native, xs))
    if x.get("income") is None:
        return False
    return not _near(h.income, x["income"])


def _same_row(r: Dict[str, Any], x: Dict[str, Any],
              account: Optional[str] = None, amount: bool = True,
              here: Optional[_Here] = None) -> bool:
    """A base row of this project (of taxjson account `account`) and a
    record's boundary row are the same row: account (when both name
    one: an older record's rows do not), action, symbol, own date,
    quantity and, within the identity tolerance (`_near`), the amount
    (`amount` False: any amount — the row as the broker corrected it):
    the income as the broker stated it where both sides have it
    (`_space`; `here`: this row's amounts), so a rate refreshed since
    moves no row out, else the base net. The tolerance identifies the
    row; whether it was corrected is `_changed`, to the cent (backlog
    TJ-5065)."""
    if account and x.get("account") and x.get("account") != account:
        return False
    if str(r.get("action") or "").upper() != x.get("action") \
            or r.get("symbol") != x.get("symbol"):
        return False
    if str(_d(r.get("date"))) != str(x.get("date")):
        return False
    if not _qty_eq(float(r.get("quantity") or 0.0),
                   float(x.get("quantity") or 0.0)):
        return False
    if not amount:
        return True
    h = here or _here(r, account or "")
    xs = _space(h, x)
    if xs is not None and h.native is not None:
        return _near(h.native.income, xs.income)
    return _near(h.net, x.get("net"))


def _j(v: Any) -> Any:
    return M.json_money(M.of(v or 0), 2)


def _f(v: Any) -> str:
    return f"{M.shown(M.of(v or 0), 2):,.2f}"


def _correction(r: Dict[str, Any], x: Dict[str, Any], h: _Here, ry: int,
                what: str, raw: Optional[date], eff: date,
                gone: Optional[Tuple[str, date]] = None) -> Dict[str, Any]:
    """The item of a row the closed books counted in the closed year
    that the export corrected since (`_changed`; backlog TJ-0224): both
    readings and what the return reported, as the broker stated them
    where both sides have them (`_space`: each in its currency when it
    is not the base one), a dividend's gross beside its tax withheld
    when either side has one, and each difference in the space that
    raised it (`difference` is the base net's: the rate's move with the
    correction; backlog TJ-5065, TJ-5066, TJ-5180). `gone` (account,
    the first day its exports hold): a row they no longer hold
    (`_removed`)."""
    base = str(r.get("currency") or "").strip().upper()
    xs = _space(h, x)
    if xs is not None and h.native is not None:
        now, was = h.native, xs
    else:                               # in the base currency
        xi = x.get("income")
        now = _stated(base, h.net if xi is None else h.income, h.net)
        was = _stated(base, x.get("net") if xi is None else xi,
                      x.get("net") or 0)
    cur = now.cur
    c = f" {cur}" if cur != base else ""

    def cs(wc: str) -> str:
        return c if wc == cur else f" {wc}"
    wcs = ([] if now.day is not None or was.day is not None
           else sorted(set(now.withheld) | set(was.withheld)))
    wm = _w_moved(now, was)
    di = M.of(now.income or 0) - M.of(was.income or 0)
    inc = _moved(now.income, was.income) or not wm

    def _at(s: _Stated) -> str:
        if not wcs:
            return f"{_f(s.income)}{c}"
        return f"{_f(s.income)}{c} gross, " + ", ".join(
            f"{_f(s.withheld.get(wc, 0))}{cs(wc)} withheld" for wc in wcs)
    wparts = []
    for wc in wm:
        dw = M.of(now.withheld.get(wc, 0)) - M.of(was.withheld.get(wc, 0))
        wparts.append(f"{_f(abs(dw))}{cs(wc)} {'less' if dw > 0 else 'more'}"
                      f" tax withheld")
    slip = [f"{_f(now.withheld.get(wc, 0))}{cs(wc)} withheld" for wc in wm]
    if inc:
        said = (f"{_f(abs(di))}{c} {'less' if di > 0 else 'more'}"
                + (", and " + " and ".join(wparts) if wparts else "")
                + " than the row now says. Compare it with the slip: if "
                + "the slip says " + " and ".join([f"{_f(now.income)}{c}"]
                                                  + slip))
    else:
        said = (f"the same {what} but " + " and ".join(wparts)
                + " than the row now says. Compare it with the slip: if "
                + "it says " + " and ".join(slip))
    xn = _cents(x.get("net"))
    item: Dict[str, Any] = {
        "symbol": r.get("symbol"), "action": str(r.get("action") or "").upper(),
        "date": str(raw), "tax_date": str(eff), "amount": h.net,
        "recorded_amount": xn, "difference": _j(M.of(h.net) - M.of(xn))}
    if x.get("income") is not None:
        item.update(income=h.income, recorded_income=_cents(x["income"]),
                    income_difference=_j(M.of(h.income)
                                         - M.of(x["income"])))
    if c:
        item.update(currency=cur, native_income=_j(now.income),
                    recorded_native_income=_j(was.income),
                    native_income_difference=_j(di))
        if now.net is not None and was.net is not None:
            item.update(native_amount=_j(now.net),
                        recorded_native_amount=_j(was.net),
                        native_difference=_j(M.of(now.net) - M.of(was.net)))
    if wcs:
        item.update(
            withheld={wc: _j(now.withheld.get(wc, 0)) for wc in wcs},
            recorded_withheld={wc: _j(was.withheld.get(wc, 0)) for wc in wcs},
            withheld_difference={wc: _j(M.of(now.withheld.get(wc, 0))
                                        - M.of(was.withheld.get(wc, 0)))
                                 for wc in wcs})
    item["why"] = (f"the {ry} books counted this {what} in {ry} at "
                   f"{_at(was)}; this project reads it at {_at(now)} (the "
                   f"export was corrected after the close), so the {ry} "
                   f"return reported {said}, amend {ry} for the "
                   f"difference.")
    if gone is not None:
        item["why"] = (
            f"the {ry} books counted this {what} (dated {raw}) in {ry} "
            f"at {_at(was)}, but this project's books of {gone[0]}, whose "
            f"broker exports reach that day (they start {gone[1]}), no "
            f"longer have it: the broker removed or reversed it after the "
            f"close, or the export that held it is not in this project. "
            f"Compare it with the slip: if the slip does not have it, "
            f"amend {ry} for it; if it does, add the export that holds it "
            f"here.")
    return item


def _in_window(a: str, q: Any, eff: date, raw: Optional[date], ry: int
               ) -> bool:
    """A row counted in closed year `ry` (tax date `eff`) that the
    boundary check compares: a sale from Dec 30, income from Dec 30 or
    dated in January; nothing earlier, no purchase (backlog TJ-5182)."""
    if a in _INCOME:
        return (raw is not None and raw > date(ry, 12, 31)) \
            or eff >= date(ry, 12, 30)
    return M.of(q or 0) < 0 and eff >= date(ry, 12, 30)


class _HereBooks:
    """This project's trade and income rows for the boundary check, by
    taxjson account (`acct_of`), their amounts (`h`), the base currency
    (`base`) and the first day each account's broker exports hold
    (`first`, `_exported`; backlog TJ-5183)."""

    def __init__(self, cache: Path, cfg: Dict[str, Any]):
        self.native = _Native(cache)
        self.rows: List[Dict[str, Any]] = []
        self.acct_of: Dict[int, str] = {}
        self.crypto: Dict[int, bool] = {}
        self.first: Dict[str, date] = {}
        self.base = str(settings_of(cfg).base_currency or "").upper()
        self._h: Dict[int, _Here] = {}
        for g, names in groups(cfg).items():
            for n in names:
                for r in _rows(cache / f"{n}_base.json"):
                    self.base = self.base or str(r.get("currency") or "")
                    d = _d(r.get("date"))
                    if (d is not None and _exported(r)
                            and d < self.first.get(n, date.max)):
                        self.first[n] = d
                    if str(r.get("action") or "").upper() in _TRADES + _INCOME:
                        self.rows.append(r)
                        self.acct_of[id(r)] = n
                        self.crypto[id(r)] = g == "crypto"

    def h(self, r: Dict[str, Any]) -> _Here:
        if id(r) not in self._h:
            self._h[id(r)] = _here(r, self.acct_of.get(id(r)) or "",
                                   self.native, self.crypto.get(id(r), False))
        return self._h[id(r)]

    def same(self, r: Dict[str, Any], x: Dict[str, Any],
             amount: bool = True) -> bool:
        a = self.acct_of.get(id(r))
        return _same_row(r, x, a, amount=False) and (
            not amount or _same_row(r, x, a, here=self.h(r)))

    def pairs(self, rows: List[Dict[str, Any]],
              rec_rows: List[Dict[str, Any]], used: set
              ) -> Dict[int, int]:
        """{id(row): its record row's index}: every pair unchanged to the
        cent first, then the rows alike within the tolerance, so of two
        record rows a day alike each row takes its own (`used`: the
        record rows taken, updated)."""
        out: Dict[int, int] = {}
        for exact in (True, False):
            for i, x in enumerate(rec_rows):
                if i in used:
                    continue
                for r in rows:
                    if id(r) not in out and self.same(r, x) and not (
                            exact and _changed(self.h(r), x)):
                        out[id(r)] = i
                        used.add(i)
                        break
        return out


def _exported(r: Dict[str, Any]) -> bool:
    """A base row from a broker's export of the account (a parsed file):
    evidence that the books reach its day. Not a .tt file's row (a hand
    file: what it covers cannot be told), an OPENING line, nor a row
    taxjson made (a lot move, a listing journal, a move between your own
    accounts, an in-kind or custody row, a corporate action's row with
    no file). A row with no file named (hand-built books) counts."""
    from taxjson.lib import row_types as RT
    t, a = str(r.get("type") or ""), str(r.get("action") or "").upper()
    src = str(r.get("source") or "")
    return not (src.lower().endswith(".tt") or RT.is_opening_row(r)
                or a == "OPENING_BALANCE"
                or t in ("opening_balance", RT.LOT_MOVE_TYPE,
                         RT.LISTING_JOURNAL_TYPE, RT.OWN_MOVE_TYPE,
                         RT.TRANSFER_CUSTODY_TYPE, *RT.IN_KIND_TYPES)
                or (r.get("corp_event_id") and not src))


def _comparable(x: Dict[str, Any], ry: int, doubled: set) -> bool:
    """A record row the closed year counted that `_boundary_issues`
    compares (`_in_window`), not one section 2 (a trade settling after
    Dec 31) or section 3 (`doubled`: (symbol, date) of each double)
    judges."""
    a = str(x.get("action") or "").upper()
    xd, raw = _d(x.get("tax_date")), _d(x.get("date"))
    return not (xd is None or raw is None or xd > date(ry, 12, 31)
                or not _in_window(a, x.get("quantity"), xd, raw, ry)
                or (a in _TRADES
                    and (_d(x.get("date_settle")) or raw) > date(ry, 12, 31))
                or bool({(x.get("symbol"), str(raw)),
                         (x.get("symbol"), str(xd))} & doubled))


def _what(a: str) -> str:
    return ("sale" if a in _TRADES else
            "return of capital" if a == "ADJUST" else "income")


def _removed(x: Dict[str, Any], ry: int, bk: _HereBooks, taken: set
             ) -> Optional[Dict[str, Any]]:
    """The item of a `_comparable` record row that this project's books
    of its account no longer hold anywhere, where the account's broker
    exports reach its day (`first`: they hold a row dated on or before
    it; none for an account of .tt files only, or books that start
    later): the broker removed or reversed it, or the export that held
    it is not here (backlog TJ-5183). `_correction`'s fields, this
    project's amounts nil."""
    acct = str(x.get("account") or "")
    raw = _d(x.get("date"))
    first = bk.first.get(acct)
    if first is None or raw is None or first > raw or any(
            id(r) not in taken and bk.same(r, x, amount=False)
            for r in bk.rows):
        return None
    a = str(x.get("action") or "").upper()
    xs = _stated_of(x)
    nil = _Here(0.0, 0.0, None if xs is None else _stated(
        xs.cur, 0, None if xs.net is None else 0,
        {c: 0 for c in xs.withheld}, None if xs.day is None else {}), None)
    return _correction({"symbol": x.get("symbol"), "action": a,
                        "currency": bk.base}, x, nil, ry, _what(a), raw,
                       _d(x.get("tax_date")) or raw, gone=(acct, first))


def _alike_sym(a: Any, b: Any) -> bool:
    """Two spellings of one security: the letters before the exchange
    suffix (ZZH.TO, ZZH)."""
    def root(s: Any) -> str:
        return str(s or "").strip().upper().replace(":", ".").split(".")[0]
    return bool(root(a)) and root(a) == root(b)


def _redated(r: Dict[str, Any], x: Dict[str, Any], h: _Here, ry: int,
             what: str, raw: Optional[date], eff: date,
             cross: bool = False) -> Dict[str, Any]:
    """The one item of a row here and a record row of one account, kind
    and amount that the export re-dated, re-counted or re-spelled after
    the close: what changed; both count it in `ry`, or (`cross`) it is
    re-dated past Dec 31, a tax date or a sale's settlement in `ry` + 1
    (`_moved_out`)."""
    q, xq = float(r.get("quantity") or 0.0), float(x.get("quantity") or 0.0)
    ch: List[str] = []
    item: Dict[str, Any] = {
        "symbol": r.get("symbol"), "action": str(r.get("action")).upper(),
        "date": str(raw), "tax_date": str(eff), "amount": h.net,
        "recorded_amount": _cents(x.get("net")),
        "difference": _j(M.of(h.net) - M.of(_cents(x.get("net"))))}
    if str(raw) != str(x.get("date")):
        ch.append(f"re-dated from {x.get('date')} to {raw}")
        item["recorded_date"] = x.get("date")
    if not _qty_eq(q, xq):
        ch.append(f"re-counted from {xq:g} to {q:g} units")
        item.update(quantity=q, recorded_quantity=xq)
    if r.get("symbol") != x.get("symbol"):
        ch.append(f"re-spelled from {x.get('symbol')} to {r.get('symbol')}")
        item["recorded_symbol"] = x.get("symbol")
    item["changed"] = [k for k, c in (("date", "re-dated"),
                                      ("quantity", "re-counted"),
                                      ("symbol", "re-spelled"))
                       if any(t.startswith(c) for t in ch)]
    said = x.get("income") if x.get("income") is not None else x.get("net")
    if cross:
        return _moved_out(item, r, x, ch, ry, what, said, eff)
    item["why"] = (f"the {ry} books counted this {what} in {ry} at "
                   f"{_f(said)}; this project has the same {what} at the "
                   f"same amount, "
                   + " and ".join(ch) + f" (the export was corrected after "
                   f"the close). Both count it in {ry}, so the {ry} return "
                   f"needs no change for it: check the slip shows it as "
                   f"this project now reads it.")
    return item


def _moved_out(item: Dict[str, Any], r: Dict[str, Any],
               x: Dict[str, Any], ch: List[str], ry: int, what: str,
               said: Any, eff: date) -> Dict[str, Any]:
    """`_redated`'s item for a row re-dated past Dec 31: the closed
    return reported it; this project counts it in `ry` + 1 (`eff`), or
    counts a sale settling then on its trade date. Never "needs no
    change" (backlog TJ-5186)."""
    s = _d(r.get("date_settle")) if item["action"] in _TRADES else None
    item.update(crossed_year_end=True, recorded_tax_date=x.get("tax_date"))
    if s is not None:
        item.update(date_settle=str(s),
                    recorded_date_settle=x.get("date_settle"))
    if s is not None and s > date(ry, 12, 31):
        ch = ch + [f"settling {s}"]
    nxt = eff.year if eff > date(ry, 12, 31) else None
    item["why"] = (
        f"the {ry} books counted this {what} (dated {x.get('date')}) in "
        f"{ry} at {_f(said)}; this project has the same {what} at the "
        f"same amount, " + " and ".join(ch) + " (the export was corrected "
        "after the close), " + (
            f"which counts in {nxt}: the {ry} return reported it and this "
            f"project counts it in {nxt} too. Report it in one year only: "
            f"if the slip (or the trade confirmation) puts it in {nxt}, "
            f"amend {ry} to take it out; if it puts it in {ry}, this "
            f"project's export misdates it." if nxt else
            f"which settles after Dec 31; this project still counts it in "
            f"{ry} on its trade date, as the {ry} return did. Compare it "
            f"with the trade confirmation: if the sale belongs to "
            f"{ry + 1}, amend {ry} to take it out."))
    return item


def _lone_and_gone(lone: List[Tuple[Any, ...]],
                   gone: List[Tuple[int, Dict[str, Any]]], ry: int,
                   bk: _HereBooks, taken: set, doubled: set,
                   eff_of: Callable[[Dict[str, Any]], Optional[date]]
                   ) -> List[Dict[str, Any]]:
    """Case (a)'s rows no record row matched (`lone`: (row, action, tax
    date, date, what, `_Here`)) and the record rows of the window none
    here matched (`gone`: (index, row)): a pair of one account, kind and
    amount (`_changed`, to the cent where both state it, else within the
    tolerance), the security spelled alike, is one row the export
    re-dated, re-counted or re-spelled (`_redated`, backlog TJ-5186): in
    `ry` both, or past Dec 31 (a row here counted, `eff_of`, or a sale
    settling by Jan 10, never paired as unchanged: `_moved_out`); then
    each lone row is in neither return (`_neither`) and each gone row
    removed where the exports reach its day (`_removed`)."""
    out: List[Dict[str, Any]] = []
    ye, near = date(ry, 12, 31), date(ry + 1, 1, 10)
    left = [x for _, x in gone if _comparable(x, ry, doubled)]

    def twin(r: Dict[str, Any], a: str, h: _Here) -> Optional[Dict[str, Any]]:
        acct = bk.acct_of.get(id(r))
        return next((x for x in left
                     if a == x.get("action")
                     and not (acct and x.get("account")
                              and x.get("account") != acct)
                     and _alike_sym(r.get("symbol"), x.get("symbol"))
                     and not _changed(h, x)
                     and (_space(h, x) is not None
                          or _near(h.net, x.get("net")))), None)

    def settles_out(r: Dict[str, Any], a: str) -> bool:
        return a in _TRADES and (_d(r.get("date_settle")) or ye) > ye

    for r, a, eff, raw, what, h in lone:
        x = twin(r, a, h)
        if x is None:
            out.append(_neither(r, a, eff, raw, what, ry, h))
            continue
        left.remove(x)
        out.append(_redated(r, x, h, ry, what, raw, eff, settles_out(r, a)))
    lone_ids = {id(t[0]) for t in lone}
    for r in bk.rows:
        a, e, raw = str(r.get("action") or "").upper(), eff_of(r), _d(
            r.get("date"))
        if (not left or id(r) in taken or id(r) in lone_ids or e is None
                or raw is None or not ye < e <= near or raw > near):
            continue
        x = twin(r, a, bk.h(r))
        if x is not None:
            left.remove(x)
            out.append(_redated(r, x, bk.h(r), ry, _what(a), raw, e, True))
    for x in left:
        g = _removed(x, ry, bk, taken)
        if g is not None:
            out.append(g)
    return out


def _day_withheld(bk: _HereBooks, rows: List[Dict[str, Any]],
                  rec_rows: List[Dict[str, Any]], ry: int, doubled: set
                  ) -> List[Dict[str, Any]]:
    """Where two or more dividends of a security share a day's TAX rows
    on either side (`_Stated.day`), the day's tax withheld for that
    account and security compared once, by currency, in cents: one item
    for a change (backlog TJ-5185)."""
    now: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    was: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    shared, td = set(), {}
    for r in rows:
        n = bk.h(r).native
        if str(r.get("action") or "").upper() == "DIVIDEND" and n:
            k = (bk.acct_of.get(id(r)), r.get("symbol"),
                 str(_d(r.get("date"))))
            now.setdefault(k, n.day if n.day is not None else n.withheld)
            shared |= {k} if n.day is not None else set()
    for x in rec_rows:
        xs = _stated_of(x)
        if x.get("action") == "DIVIDEND" and xs and _comparable(x, ry, doubled):
            k = (x.get("account"), x.get("symbol"), str(x.get("date")))
            was.setdefault(k, xs.day if xs.day is not None else xs.withheld)
            shared |= {k} if xs.day is not None else set()
            td.setdefault(k, x.get("tax_date"))
    out = []
    for k in sorted(shared, key=str):
        w1, w0 = now.get(k, {}), was.get(k, {})
        wm = [c for c in sorted(set(w1) | set(w0))
              if _moved(w1.get(c, 0), w0.get(c, 0))]
        if not wm:
            continue

        def at(w: Dict[str, Any]) -> str:
            return ", ".join(f"{_f(w.get(c, 0))} {c}" for c in wm)
        dws = {c: M.of(w1.get(c, 0)) - M.of(w0.get(c, 0)) for c in wm}
        out.append({
            "symbol": k[1], "action": "DIVIDEND", "date": k[2],
            "tax_date": td.get(k) or k[2],
            "withheld": {c: _j(w1.get(c, 0)) for c in wm},
            "recorded_withheld": {c: _j(w0.get(c, 0)) for c in wm},
            "withheld_difference": {c: _j(d) for c, d in dws.items()},
            "why": (f"the {ry} books counted the tax withheld on the "
                    f"{k[2]} dividends of {k[1]} in {k[0]} at {at(w0)}; "
                    f"this project reads {at(w1)} (the export was "
                    f"corrected after the close), so the {ry} return "
                    f"reported " + " and ".join(
                        f"{_f(abs(d))} {c} {'less' if d > 0 else 'more'}"
                        for c, d in dws.items())
                    + f" tax withheld than the rows now say. Compare it "
                    f"with the slip: if it says {at(w1)} withheld, amend "
                    f"{ry} for the difference.")})
    return out


def _neither(r: Dict[str, Any], a: str, eff: date, raw: Optional[date],
             what: str, ry: int, h: _Here) -> Dict[str, Any]:
    """Case (a)'s item: a row this project counts in the closed year
    that the closed books never had."""
    return {"symbol": r.get("symbol"), "action": a,
            "date": str(raw), "tax_date": str(eff), "amount": h.net,
            "why": (f"this project counts this {what} on {eff} (in {ry})"
                    + (f", though it is dated {raw}" if raw != eff else "")
                    + f", but the {ry} books never had it (their inputs "
                    f"did not hold it): it is in neither return. It "
                    f"belongs on the {ry} return — amend {ry} (or confirm "
                    f"the slip reported it).")}
def _boundary_issues(cache: Path, cfg: Dict[str, Any],
                     record: Dict[str, Any], basis: str,
                     section2: set,
                     notes: Optional[List[str]] = None,
                     rough: Optional[Dict[str, set]] = None,
                     doubled: Optional[set] = None
                     ) -> List[Dict[str, Any]]:
    """Rows reported in neither year or in both because this project and
    the closed one date them on different sides of Dec 31 (see
    boundary_rows), or corrected since the close. Four cases:
      (a) this project counts a row in the closed year that the closed
          books never had — income its trust record date (or a RIC entry)
          moves back, or a row local_timezone re-dates to Dec 31 — so it
          is in neither return; a row the closed books counted in the
          closed year, of the same date and quantity at another amount
          (the broker corrected the export), is that row: its
          difference is reported, not the whole row (backlog TJ-0224),
          to the cent (`_changed`; TJ-5065, TJ-5066, TJ-5180). Only the
          rows `_in_window`: a sale or income from Dec 30, income dated
          in January;
      (b) the closed books counted an income row in the closed year and
          this project counts it again in the next;
      (c) the closed project's inputs held an early-January row (an
          overnight fill moved past Dec 31) that this project lacks — a
          row here of the same date and quantity at another amount (the
          broker corrected the export) is that row: a note in `notes`,
          not "in neither return" (backlog TJ-0014);
      (d) a row of (a)'s window the closed books counted that this
          project no longer holds, or holds re-dated, re-counted or
          re-spelled (`_lone_and_gone`); shared TAX rows (`_day_withheld`).
    `rough` ({why: row keys}) collects the rows compared only within the
    tolerance (`_rough`); `doubled`, see `_removed`."""
    settings = settings_of(cfg)
    ir = _income_rules(settings)
    ry = int(record["year"])
    ye = date(ry, 12, 31)
    rec_rows = [x for x in record.get("boundary_rows") or []
                if isinstance(x, dict)]
    bk = _HereBooks(cache, cfg)
    out: List[Dict[str, Any]] = []
    used: set = set()
    lone: List[Tuple[Any, ...]] = []
    gone: List[Tuple[int, Dict[str, Any]]] = []

    def _corrected(r):
        # The record's row the closed books counted in the closed year,
        # at another amount: the broker corrected the export after the
        # close (backlog TJ-0224; case (c) does the same, TJ-0014).
        for i, x in enumerate(rec_rows):
            xd = _d(x.get("tax_date"))
            if (i not in used and xd is not None and xd <= ye
                    and bk.same(r, x, amount=False)):
                used.add(i)
                return x
        return None

    cands = []
    for r in bk.rows:
        if r.get("id") not in (None, "") and str(r["id"]) in section2:
            continue                    # section 2 already judges it
        a = str(r.get("action") or "").upper()
        eff, raw = _eff_date(r, basis, ir), _d(r.get("date"))
        if eff is not None and eff <= ye and _in_window(
                a, r.get("quantity"), eff, raw, ry):
            cands.append((r, a, eff, raw))
    pairs = bk.pairs([c[0] for c in cands], rec_rows, used)
    hits = {i: r for r, *_ in cands for i in [pairs.get(id(r))]
            if i is not None}
    for r, a, eff, raw in cands:
        h = bk.h(r)
        old = rec_rows[pairs[id(r)]] if id(r) in pairs else None
        if old is not None and not (_changed(h, old) and (
                _d(old.get("tax_date")) or date.max) <= ye):
            if rough is not None and _rough(h, old):
                rough.setdefault(str(_rough(h, old)), set()).add(id(r))
            continue
        what = _what(a)
        old = old or _corrected(r)
        if old is None:
            lone.append((r, a, eff, raw, what, h))
            continue
        if rough is not None and _rough(h, old):
            rough.setdefault(str(_rough(h, old)), set()).add(id(r))
        out.append(_correction(r, old, h, ry, what, raw, eff))
    # Each row of this project stands for one record row: two identical
    # record rows need two here.
    taken = {id(r) for r in hits.values()}
    rest = bk.pairs([r for r in bk.rows if id(r) not in taken], rec_rows,
                    used | set(hits))
    hits.update({i: r for r in bk.rows for i in [rest.get(id(r))]
                 if i is not None})
    taken = {id(r) for r in hits.values()}
    for i, x in enumerate(rec_rows):
        a = x.get("action")
        xd = _d(x.get("tax_date"))
        if xd is None:
            continue
        hit = hits.get(i)
        if hit is None and i not in used and xd <= ye:
            gone.append((i, x))
            continue
        if a in _INCOME and xd <= ye:
            if hit is None:
                continue
            eff = _eff_date(hit, basis, ir)
            if eff is not None and eff > ye:
                out.append({"symbol": x.get("symbol"), "action": a,
                            "date": x.get("date"), "tax_date": str(eff),
                            "amount": x.get("net"),
                            "why": (f"the {ry} books counted this row "
                                    f"(dated {x.get('date')}) in {ry} "
                                    f"(on {xd}); this project counts it "
                                    f"again on {eff}. Report it in one "
                                    f"year only (a RIC January dividend "
                                    f"or a trust's record date: keep the "
                                    f"same entry in this project).")})
        elif (xd > ye and xd <= date(ry + 1, 1, 10) and hit is None
              and not (a in _TRADES
                       and (_d(x.get("date")) or date.max) <= ye)):
            fixed = None
            for r in bk.rows:
                if id(r) not in taken and bk.same(r, x, amount=False):
                    eff = _eff_date(r, basis, ir)
                    if eff is not None and eff > ye:
                        fixed = r
                        taken.add(id(r))
                        break
            if fixed is not None:
                if notes is not None:
                    notes.append(
                        f"{x.get('symbol')} {a} dated {x.get('date')}: "
                        f"{bk.h(fixed).net:,.2f} here, "
                        f"{_cents(x.get('net')):,.2f} when "
                        f"the {ry} project read it (the export was "
                        f"corrected) — both count it in {ry + 1}, so it "
                        f"is in this return once.")
                continue
            out.append({"symbol": x.get("symbol"), "action": a,
                        "date": x.get("date"), "tax_date": str(xd),
                        "amount": x.get("net"),
                        "why": (f"the {ry} project's inputs hold this row "
                                f"dated {x.get('date')}, which counts in "
                                f"{ry + 1}, so the {ry} return left it "
                                f"out — but this project does not have it: "
                                f"it is in neither return. Add it here.")})
    return (out + _lone_and_gone(lone, gone, ry, bk, taken, doubled or set(),
                                 lambda r: _eff_date(r, basis, ir))
            + _day_withheld(bk, [c[0] for c in cands], rec_rows, ry,
                            doubled or set()))


def _rough_note(ry: int, rough: Dict[str, set]) -> Optional[str]:
    """The note on the rows compared only within the tolerance, by why
    (`_rough`; backlog TJ-5181)."""
    if not any(rough.values()):
        return None

    def _why(k: str) -> str:
        if k.startswith("missing:"):
            return f"{k[8:]} is missing (re-run `tjs run` to rebuild it)"
        if k.startswith("absent:"):
            return f"{k[7:]} does not hold it"
        return {"computed": "its amounts are taxjson's (a crypto row "
                            "priced from the price cache), not the "
                            "broker's",
                "record": f"the {ry} record has no amounts as the broker "
                          f"stated them for it",
                "currency": f"its currency is not the {ry} record's"}.get(
                    k, k)
    parts = [f"{len(v)} row{'s' if len(v) != 1 else ''}: {_why(k)}"
             for k, v in sorted(rough.items()) if v]
    return (f"Some of the {ry} Dec-31 rows were compared at their converted "
            f"amounts within 0.5%, not to the cent, so a smaller "
            f"correction since the close is not seen — "
            + "; ".join(parts) + ".")


def _boundary_section(cache: Path, cfg: Dict[str, Any],
                      record: Dict[str, Any], basis: str, section2: set,
                      notes: List[str],
                      doubles: Optional[List[Dict[str, Any]]] = None
                      ) -> List[Dict[str, Any]]:
    """Section 4 of `check`: `_boundary_issues`, with a note for a record
    that predates the Dec-31 row list (nothing compared) or the amounts
    its rows are compared at to the cent (backlog TJ-5065, TJ-5066), else
    one for the rows compared only within the tolerance (TJ-5181).
    `doubles`: section 3's items (`_removed` leaves their sales to it)."""
    ry = int(record["year"])
    rows = record.get("boundary_rows")
    if not isinstance(rows, list):
        notes.append(
            f"The {ry} record predates the Dec-31 row list: income or a "
            f"trade that this project dates in {ry} (a trust's record "
            f"date, a RIC January dividend, a local_timezone re-dating) "
            f"is not checked. Re-close {ry} with the current taxjson to "
            f"check it.")
        return []
    rough: Dict[str, set] = {}
    out = _boundary_issues(cache, cfg, record, basis, section2, notes, rough,
                           {(d.get("symbol"), str(d.get("closed_date")))
                            for d in doubles or []})
    if any(isinstance(x, dict) and "income" not in x for x in rows):
        notes.append(
            f"The {ry} record predates the native amounts in its Dec-31 "
            f"row list (closed by an older taxjson): its rows were "
            f"compared at their converted amounts within 0.5%, a dividend "
            f"at its net, so a smaller correction since the close, or a "
            f"dividend's gross split anew between the income and the tax "
            f"withheld, is not seen. Re-close {ry} with the current "
            f"taxjson (`close-year --force` in its project) to compare "
            f"them to the cent.")
    elif _rough_note(ry, rough):
        notes.append(str(_rough_note(ry, rough)))
    return out
def _short_rows_issues(record: Dict[str, Any],
                       opening_shorts: Optional[Dict[str, Any]],
                       moved_shorts: Dict[Tuple[str, str], Dict[str, Any]],
                       basis_dq: Dict[str, float], issues: Dict[str, List[Dict[str, Any]]],
                       ry: int) -> None:
    """Section 1a of `check`: a short beside a long, or beside a denied
    loss waiting for its replacement, is its own row (backlog TJ-1134,
    TJ-2712), as a short alone is a main row. An older record has none
    to compare, unless its main rows hold one the opening has beside
    (`moved_shorts`, TJ-3283): compared here, by symbol."""
    rec_shorts = record.get("year_end_shorts")
    if moved_shorts and rec_shorts is None:
        rec_shorts = {}
    if rec_shorts is not None and opening_shorts is not None:
        said = {(i["group"], i["symbol"]) for i in issues["positions"]}
        for g in sorted(set(rec_shorts) | set(opening_shorts)):
            prev = rec_shorts.get(g) or {}
            now = opening_shorts.get(g) or {}
            for sym in sorted(set(prev) | set(now)):
                p = (prev.get(sym) or moved_shorts.get((g, sym))
                     or {"qty": 0.0, "acb": 0.0})
                n = now.get(sym, {"qty": 0.0, "acb": 0.0})
                if ((g, sym) in said or abs(basis_dq.get(sym, 0.0)) > QTY_TOL
                        or (_qty_eq(n["qty"], p["qty"])
                            and _acb_close(n["acb"], p["acb"]))):
                    continue
                issues["positions"].append({
                    "group": g, "symbol": sym,
                    "closed_qty": p["qty"], "closed_acb": p["acb"],
                    "closed_deferred": 0.0,
                    "opening_qty": n["qty"], "opening_acb": n["acb"],
                    "qty_diff": M.units(n["qty"] - p["qty"], 8),
                    "acb_diff": M.shown(n["acb"] - p["acb"], 2),
                    "filed_gain_diff": None,
                    "why": (f"the short beside a long or a waiting denied "
                            f"loss: {ry} books hold {p['qty']:g} at "
                            f"{M.shown(p['acb'], 2):,.2f} on Dec 31; this project opens "
                            f"with {n['qty']:g} at {M.shown(n['acb'], 2):,.2f}. Fix "
                            f"whichever side is wrong; its cover's gain "
                            f"depends on it.")})


def check(root: Path, cfg: Dict[str, Any], record: Dict[str, Any],
          opening: Dict[str, Any],
          carry_back: Any = None,
          opening_lots: Optional[Dict[str, Any]] = None,
          opening_shorts: Optional[Dict[str, Any]] = None
          ) -> Dict[str, Any]:
    """Compare a closed year's record with this project's books.
    `opening` is `snapshot(...)` of THIS project at the record's Dec 31.
    `carry_back`: the carryover ledger on this project's T1A carry-back
    (lib/carryforward.LedgerCarryBack, Canada).
    `opening_lots`: a US project's lots then (snapshot's `lots_out`),
    compared with the record's `year_end_lots` (backlog TJ-0554).
    `opening_shorts`: a Canadian project's shorts beside a long, or
    beside a denied loss waiting for its replacement (backlog TJ-2712),
    then (snapshot's `shorts_out`), compared with the record's
    `year_end_shorts` where it has them (backlog TJ-1134). A record
    closed before TJ-2712 holds a short beside such a wait in
    `year_end`: it is matched against the opening's shorts, by symbol,
    and a changed quantity or cost is said in section 1a (TJ-3283)."""
    cache = Path(root) / "work"
    settings = settings_of(cfg)
    ry = int(record["year"])
    basis = _basis(settings)
    rbasis = record.get("date_basis") or basis
    issues: Dict[str, List[Dict[str, Any]]] = {
        "positions": [], "missed": [], "double": [], "timing": [],
        "boundary": [], "carry": [],
        "partial": [], "notes": []}
    issues["timing"] = _timing_issues(cache, cfg, record)
    from taxjson.lib.country import CountryError, settings_country
    try:
        _usa = settings_country(settings) == "usa"
    except CountryError:
        _usa = False
    _loss_words = ("wash-sale losses (§1091(d))" if _usa
                   else "superficial losses")
    # A record that is not of the year before this project's: this
    # project opens with the year-end in between, which the record does
    # not cover. A note on stderr only, the JSON said 0 problems
    # (backlog TJ-0555).
    _py = settings.year
    if _year_like(_py) and int(_py) != ry + 1:
        _gap = (f"{int(_py) - 1}" if int(_py) - 1 > ry
                else f"{int(_py) - 1} (not a later one)")
        issues["partial"].append({
            "record_year": ry, "project_year": int(_py),
            "what": (f"the record is for {ry}, this project's year is "
                     f"{int(_py)}"),
            "why": (f"this project opens with the {int(_py) - 1} "
                    f"year-end: what changed after {ry} (sales, buys, "
                    f"carry-forwards) is not checked against a return. "
                    f"Check it against the {_gap} record: `close-year` "
                    f"in that year's project, then `handoff --prior` "
                    f"its filed/{int(_py) - 1}.json, or set [settings] "
                    f"prior_year_record to it.")})
    # A record closed on or before Dec 31 of its year (close-year
    # --force during the year) is a partial-year snapshot, not the
    # year-end: its positions miss the rest of that year's trades, and
    # "Everything the closed year carried forward is here, once" was
    # printed over it (A2-0349).
    _ca = _d(record.get("closed_at"))
    if _ca is not None and _ca <= date(ry, 12, 31):
        issues["partial"].append({
            "closed_at": str(record.get("closed_at")),
            "why": (f"the {ry} record was closed on {_ca} (close-year "
                    f"--force before the year ended): a partial-year "
                    f"snapshot, not the {ry} year-end — its positions, "
                    f"sales and January settlements miss everything "
                    f"after {_ca}. Re-close {ry} in its project now that "
                    f"the year has ended (after filing).")})

    # A trade that straddles Dec 31 sits on different sides of the
    # year-end in two projects on different date bases: the quantity
    # difference it causes is that sale, reported once in section 2 as
    # a date-basis change, not "a lot or a sale is missing" here too
    # (A2-0356, A2-0673).
    basis_dq: Dict[str, float] = {}
    if rbasis != basis:
        for st in record.get("settle_next_year") or []:
            q = float(st.get("qty") or 0.0)
            basis_dq[st["symbol"]] = basis_dq.get(st["symbol"], 0.0) + (
                -q if rbasis == "trade" else q)

    # 1. Opening positions and cost.
    book_by, filed_by = _filed_by_symbol(record)
    # US: the lots too, where the record has them (TJ-0554).
    rec_lots = record.get("year_end_lots")
    lot_check = (_usa and isinstance(rec_lots, dict)
                 and opening_lots is not None)
    if _usa and rec_lots is None and any(
            (record.get("year_end") or {}).values()):
        issues["notes"].append(
            f"The {ry} record records no lots (closed by an older "
            f"taxjson): each security's units and total basis were "
            f"checked, not each lot's acquisition date and basis, which "
            f"decide a FIFO sale's gain and term here. Re-close {ry} "
            f"with the current taxjson (`close-year --force` in its "
            f"project) to check them.")
    # A record's main-row short the opening holds among its shorts
    # instead (closed before backlog TJ-2712, with Dec 31 inside a
    # denied loss's wait beside the short): compared there, section 1a.
    moved_shorts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for g in sorted(set(record.get("year_end") or {}) | set(opening)):
        prev = (record.get("year_end") or {}).get(g, {})
        now = opening.get(g, {})
        renamed = _redescribed_options(prev, now)
        for old, new in renamed:
            # The contract under its two roots is one position only if
            # its lots are the same too: the pair used to skip the US
            # lot check, so a changed acquisition date (a short-term sale
            # filed long-term, or the reverse) passed (backlog TJ-0920).
            if lot_check:
                pl = (rec_lots.get(g) or {}).get(old) or []
                nl = (opening_lots.get(g) or {}).get(new) or []
                if pl and nl and not _same_lots(pl, nl):
                    p, n = prev[old], now[new]
                    issues["positions"].append({
                        "group": g, "symbol": new,
                        "closed_symbol": old,
                        "closed_qty": p["qty"], "closed_acb": p["acb"],
                        "closed_deferred": p.get("deferred", 0.0),
                        "opening_qty": n["qty"], "opening_acb": n["acb"],
                        "qty_diff": M.units(n["qty"] - p["qty"], 8),
                        "acb_diff": M.shown(n["acb"] - p["acb"], 2),
                        "filed_gain_diff": None,
                        "lots": {"closed": pl, "opening": nl},
                        "why": (f"{old} in the {ry} books opens here as "
                                f"{new} (the broker re-described the "
                                f"contract), with "
                                + _lots_why(ry, pl, nl))})
                    continue
            issues["notes"].append(
                f"{old} in the {ry} books opens here as {new}: the same "
                f"expiry, right, strike, quantity and cost under another "
                f"root — the broker re-described the contract (RBC: ABC "
                f"→ ABC.B). Accepted as one position; this year's "
                f"closing rows must use {new}.")
        _skip = {s for pair in renamed for s in pair}
        for sym in sorted(set(prev) | set(now)):
            if sym in _skip:
                continue
            p = prev.get(sym, {"qty": 0.0, "acb": 0.0, "deferred": 0.0})
            n = now.get(sym, {"qty": 0.0, "acb": 0.0, "deferred": 0.0})
            if (p["qty"] < -QTY_TOL and sym not in now
                    and sym in ((opening_shorts or {}).get(g) or {})):
                moved_shorts[(g, sym)] = p
                continue
            dq = n["qty"] - p["qty"]
            da = n["acb"] - p["acb"]
            if _qty_eq(n["qty"], p["qty"]) \
                    and _acb_close(n["acb"], p["acb"]):
                if lot_check and sym in prev:
                    pl = (rec_lots.get(g) or {}).get(sym) or []
                    nl = (opening_lots.get(g) or {}).get(sym) or []
                    if pl and nl and not _same_lots(pl, nl):
                        issues["positions"].append({
                            "group": g, "symbol": sym,
                            "closed_qty": p["qty"], "closed_acb": p["acb"],
                            "closed_deferred": p.get("deferred", 0.0),
                            "opening_qty": n["qty"],
                            "opening_acb": n["acb"],
                            "qty_diff": M.units(dq, 8),
                            "acb_diff": M.shown(da, 2),
                            "filed_gain_diff": None,
                            "lots": {"closed": pl, "opening": nl},
                            "why": _lots_why(ry, pl, nl)})
                continue
            if sym in basis_dq and abs(basis_dq[sym]) > QTY_TOL \
                    and _qty_eq(dq, basis_dq[sym]):
                continue                # the straddling trade (section 2)
            k = _root_sym(sym)
            filed_diff = None
            if record.get("filed_dispositions") is not None:
                filed_diff = M.shown(filed_by.get(k, 0.0)
                                     - book_by.get(k, 0.0), 2)
            item = {"group": g, "symbol": sym,
                    "closed_qty": p["qty"], "closed_acb": p["acb"],
                    "closed_deferred": p.get("deferred", 0.0),
                    "opening_qty": n["qty"], "opening_acb": n["acb"],
                    "qty_diff": M.units(dq, 8), "acb_diff": M.shown(da, 2),
                    "filed_gain_diff": filed_diff}
            if not _qty_eq(n["qty"], p["qty"]):
                item["why"] = (
                    f"{ry} books hold {p['qty']:g} on Dec 31; this "
                    f"project opens with {n['qty']:g}. A lot or a sale is "
                    f"missing from the opening file (or from the {ry} "
                    f"books). Fix whichever side is wrong; quantities "
                    f"have no 'as filed' choice.")
            else:
                parts = [f"cost at Dec 31 {ry}: {M.shown(p['acb'], 2):,.2f} in the "
                         f"{ry} books, {M.shown(n['acb'], 2):,.2f} here "
                         f"({M.shown(da, 2):+,.2f})"]
                if p.get("deferred"):
                    # §1091(d) in a US project, never Canada's term
                    # (A2-1297).
                    parts.append(f"the {ry} books carry {M.shown(p['deferred'], 2):,.2f}"
                                 f" of deferred {_loss_words} in it")
                if filed_diff:
                    parts.append(f"the {ry} return reported {M.shown(filed_diff, 2):+,.2f}"
                                 f" of gain on this security differently "
                                 f"from the {ry} books")
                # The amend choice opens with the corrected cost (this
                # project's), the as-filed one with the record's: it
                # named the record's cost for both (backlog TJ-0144).
                parts.append(
                    f"choose one: keep {ry} as filed and open with the "
                    f"cost that return implied ({M.shown(p['acb'], 2):,.2f}), or "
                    f"amend {ry} and open with {M.shown(n['acb'], 2):,.2f}; never "
                    f"both")
                item["why"] = "; ".join(parts) + "."
            issues["positions"].append(item)

    # 1a. A short beside a long or a waiting denied loss: its own row.
    _short_rows_issues(record, opening_shorts, moved_shorts, basis_dq, issues, ry)

    # 1b. An option row this year's export marks CLOSING whose position
    # the opening holds only under another root (a .tt written with the
    # closed year's ABC while the export closes ABC.B): the opening
    # matches the record, yet the close is booked as a NEW written call
    # and the carried long never closes (audit A2-0006).
    from taxjson.lib.tx_load import load_transactions
    from taxjson.lib.option_close_check import unbacked_option_closes
    for g, names in groups(cfg).items():
        held = set((opening.get(g) or {}))
        for n in names:
            p = cache / f"{n}_base.json"
            if not p.exists():
                continue
            try:
                txs = load_transactions(p)
            except (OSError, ValueError, TypeError):
                continue
            for f in unbacked_option_closes(txs):
                carried = [s for s, _q in f["partners"] if s in held]
                if not carried or f["date"][:4] <= str(ry):
                    continue
                issues["positions"].append({
                    "group": g, "symbol": f["symbol"],
                    "closed_qty": 0.0, "closed_acb": 0.0,
                    "closed_deferred": 0.0,
                    "opening_qty": 0.0, "opening_acb": 0.0,
                    "qty_diff": 0.0, "acb_diff": 0.0,
                    "filed_gain_diff": None,
                    "why": (f"this year's {f['side']} of {f['symbol']} on "
                            f"{f['date']} is marked CLOSING, but the "
                            f"opening holds the contract as "
                            f"{carried[0]} (another root) — it is booked "
                            f"as a NEW position and {carried[0]} never "
                            f"closes. Write the opening (.tt) with "
                            f"{f['symbol']}, or add to ticker.map:  "
                            f"GLOBAL {carried[0]} {f['symbol']}.")})

    # 2. Trades that straddle Dec 31.
    here: List[Dict[str, Any]] = []
    for g, names in groups(cfg).items():
        for n in names:
            for r in _book(cache, n):
                if r.get("action") in ("BUYSELL", "ASSIGN"):
                    here.append(dict(r, _acct=n))

    used_here: set = set()
    here_accts = {n for ns in groups(cfg).values() for n in ns}

    def _found(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """This project's row for the record's straddling trade `s`: the
        same symbol and quantity, and the same trade date — or, within
        3 days (an opening .tt dates a row on its settlement day), the
        same net amount. Symbol + quantity + 3 days let a DISTINCT
        same-size January sale clear a sale missing from both years, or
        raise a false "double" (A2-0354). A row answers one item.
        The straddler's account, when this project has it, must match:
        another account's same-size sale is a distinct disposition and
        cleared the one this project lost (TJ-0807). An account this
        project does not name (renamed since) matches any."""
        sd = _d(s.get("date"))
        sa = str(s.get("account") or "")
        if sa not in here_accts:
            sa = ""
        for idx, r in enumerate(here):
            if idx in used_here or r.get("symbol") != s["symbol"]:
                continue
            if sa and r.get("_acct") != sa:
                continue
            if not _qty_eq(float(r.get("quantity") or 0), s["qty"]):
                continue
            rd = _d(r.get("date"))
            if rd is None or sd is None:
                continue
            if rd != sd:
                if abs((rd - sd).days) > 3:
                    continue
                if s.get("net") is not None:
                    rn = abs(float(r.get("net_amount") or 0.0))
                    if abs(rn - abs(float(s["net"]))) > max(
                            1.0, 0.005 * abs(float(s["net"]))):
                        continue
            used_here.add(idx)
            return r
        return None

    _bc = (f" (a date-basis change: the {ry} record is on "
           f"{rbasis} dates, this project on {basis} dates)"
           if rbasis != basis else "")
    reported_double: set = set()
    for s in record.get("settle_next_year") or []:
        hit = _found(s)
        if rbasis == "settle":
            if hit is None or ((_bdate(hit, basis) or date.min).year
                               <= ry):
                issues["missed"].append(dict(s, why=(
                    f"traded {s['date']}, settles {s['date_settle']}: "
                    f"the {ry} books leave it to {ry + 1} (settlement "
                    f"basis), but it is not in this project"
                    + (" as a " + str(ry + 1) + " trade" if hit else "")
                    + _bc
                    + ". Add it (dated its settlement day in a .tt file, "
                    "or keep the broker row) so it is reported once.")))
        else:
            if hit is not None and (_bdate(hit, basis) or date.min).year \
                    > ry:
                reported_double.add((_root_sym(s["symbol"]),
                                     M.units(abs(float(s["qty"])), 6),
                                     str(s.get("date"))))
                issues["double"].append(dict(s, why=(
                    f"the {ry} return used trade dates, so this "
                    f"{s['date']} trade was reported in {ry}; this "
                    f"project books it again in {ry + 1}" + _bc + ".")))

    # 3. Dispositions reported by the closed year and again here.
    closed = (record.get("filed_dispositions")
              if record.get("filed_dispositions") is not None
              else record.get("dispositions")) or []
    # The closed year's sales this project's own books ALSO hold as
    # closed-year rows: a same-size sale in early January is then a
    # distinct sale, not the closed one again (A2-0122).
    # Only a sale out of a LONG position is a disposition: the row that
    # opens a short is not the closed year's sale (its cover is, A2-0674).
    # The replay is per account and applies each account's splits (a
    # rename moves the units), transfers and opening balances: a sale
    # out of split-created units read as a sale out of a smaller long,
    # so the closed year's sale was not seen as this project's own row
    # and a same-size January sale became a false "double" (TJ-0808).
    # A split is the security's: the run applies one account's SPLIT row
    # to every account of the pool, so each account takes the ones other
    # accounts report as well (positions_check.pool_splits; TJ-1037).
    from taxjson.lib.corporate_timeline import split_copies
    from taxjson.lib.positions_check import blended_pool_rows, pool_splits
    replay = list(here)
    for names in groups(cfg).values():
        for n in names:
            for r in _book(cache, n):
                if r.get("action") in ("SPLIT", "TRANSFER",
                                       "OPENING_BALANCE"):
                    replay.append(dict(r, _acct=n))
            replay += [dict(r, _acct=n) for r in pool_splits(
                blended_pool_rows(cache, n), n)]
    split_dup = split_copies(replay, per_account=True,
                             account_of=lambda x: x.get("_acct") or "")
    own_closed: List[Tuple[str, str, float, Optional[date], Optional[date],
                           float]] = []
    _pos: Dict[Tuple[str, str], float] = {}
    for r in sorted(replay, key=lambda x: (_bdate(x, basis) or date.max,
                                           str(x.get("time") or ""))):
        acct, sym = str(r.get("_acct") or ""), r.get("symbol") or ""
        if r.get("action") == "SPLIT":
            if id(r) in split_dup:
                continue
            try:
                ratio = float(r.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            new = str(r.get("symbol_new") or "").strip() or sym
            moved = _pos.pop((acct, sym), 0.0) * ratio
            _pos[(acct, new)] = _pos.get((acct, new), 0.0) + moved
            continue
        q = float(r.get("quantity") or 0.0)
        before = _pos.get((acct, sym), 0.0)
        _pos[(acct, sym)] = before + q
        if r.get("action") not in ("BUYSELL", "ASSIGN"):
            continue
        if q >= 0 or before <= QTY_TOL:
            continue
        if (_bdate(r, basis) or date.max) > date(ry, 12, 31):
            continue
        own_closed.append((acct, _root_sym(sym), min(abs(q), before),
                           _d(r.get("date")), _d(r.get("date_settle")),
                           abs(float(r.get("net_amount") or 0.0))))

    def _is_own(c: Dict[str, Any]) -> bool:
        # The record's account, when this project has it, must be the
        # row's: another account's sale is its own (as `_found`, TJ-5067).
        ca = str(c.get("account") or "")
        k, cq = _root_sym(c["symbol"]), abs(float(c["qty"]))
        cds = {_d(c.get("date")), _d(c.get("date_settle"))} - {None}
        cp = abs(float(c.get("proceeds") or 0.0))
        for oa, ok, oq, otd, osd, onet in own_closed:
            if (ca in here_accts and oa != ca) or ok != k \
                    or not _qty_eq(oq, cq):
                continue
            if not ({otd, osd} & cds):
                continue
            if abs(onet - cp) <= max(1.0, 0.01 * cp):
                return True
        return False

    from taxjson.lib.report_model import resolve_gains_files
    taxable = {n for ns in groups(cfg).values() for n in ns}
    for acct, p in resolve_gains_files(cache).items():
        if acct not in taxable:
            continue
        # A skipped gains file hid every double in it (A2-1137); a
        # wrong-shape one was an AttributeError (A2-1396).
        for t in _gain_rows(Path(p)):
            if t.gain is None or t.action:
                continue
            td = _d(t.date)
            if not td or td > date(ry + 1, 1, 10):
                continue
            k = _root_sym(t.symbol)
            q = abs(float(t.quantity or 0.0))
            tp = float(t.proceeds or 0.0)
            # A short cover: the engine's proceeds are the negated cover
            # cost and its cost the negated short-sale proceeds; another
            # tool's CSV reports the short-sale proceeds (A2-0674).
            # Only against another tool's CSV: the record's own
            # dispositions use the engine's convention, and a grant-timed
            # premium row (proceeds 0) matched every buy-back otherwise.
            tc = float(t.cost or 0.0)
            tp_alt = (-tc if (record.get("filed_dispositions") is not None
                              and (t.direction == "SHORT" or tp < 0)
                              and abs(tc) > 0.005) else None)
            tol = max(1.0, 0.01 * abs(tp))
            # A sale another of this project's accounts recorded is a
            # distinct disposition, never this one again (the rule `_found`
            # applies; backlog TJ-0807, TJ-5067).
            others = taxable - {t.account or acct}
            for c in closed:
                if _root_sym(c["symbol"]) != k \
                        or str(c.get("account") or "") in others:
                    continue
                if abs(abs(c["qty"]) - q) > max(QTY_TOL, 0.005 * q):
                    continue
                cp = float(c["proceeds"])
                if abs(cp - tp) > tol and (
                        tp_alt is None
                        or abs(cp - tp_alt) > max(1.0, 0.01 * abs(tp_alt))
                        or abs(float(c.get("cost") or 0.0) + tp) > max(
                            1.0, 0.01 * abs(tp))):
                    continue            # a different sale of the same size
                cds = [x for x in (_d(c.get("date")),
                                   _d(c.get("date_settle"))) if x]
                if not cds or min(abs((x - td).days) for x in cds) > 5:
                    continue
                if (_root_sym(c["symbol"]), M.units(abs(float(c["qty"])), 6),
                        str(c.get("date"))) in reported_double:
                    break               # already one item in section 2
                if _is_own(c):
                    continue            # the closed sale is its own row here
                # The date the closed RETURN used (its record's basis).
                cd = (_d(c.get("date")) if rbasis == "trade"
                      else (_d(c.get("date_settle")) or _d(c.get("date"))))
                issues["double"].append({
                    "symbol": t.symbol, "date": str(td),
                    "qty": q, "gain": M.shown(float(t.gain or 0), 2),
                    "closed_date": str(cd),
                    "why": (f"a {q:g}-unit sale of "
                            f"{t.symbol} on {td} is in this "
                            f"year's totals, and the {ry} record "
                            f"has the same sale on {cd}. Report it "
                            f"in one year only.")})
                break
    # 4. Rows the two projects put on different sides of Dec 31.
    issues["boundary"] = _boundary_section(
        cache, cfg, record, basis, {str(here[i].get("id")) for i in used_here
                                    if here[i].get("id") not in (None, "")},
        issues["notes"], issues["double"])
    # 5. Carry-forwards: this project's inputs vs what the closed year
    # carried forward (tax-logic CA-CARRY-05, US-CARRY-03).
    from taxjson.lib.carryforward import handoff_issues
    _carry = handoff_issues(root, cfg, record, carry_back)
    issues["carry"] = _carry.issues
    issues["notes"] += [n for n in (_carry.unchecked, _carry.caveat,
                                    _carry.split_note, *_carry.order_notes) if n]
    if record.get("carryforwards") is None:
        issues["notes"].append(
            f"The {ry} record predates the carry-forward record (net "
            f"capital loss / capital loss carryover"
            + ("" if _usa else ", minimum tax carryover")
            + f"): this year's estimate cannot read them from it. "
            f"Re-close {ry} with the current taxjson, or enter them "
            f"yourself.")
    if record.get("filed_dispositions") is None:
        issues["notes"].append(
            f"The {ry} record holds taxjson's own dispositions. If that "
            f"return was prepared with another tool, re-close {ry} with "
            f"--filed-dispositions so doubles are checked against what "
            f"was actually filed.")
    return {"year": ry, "record_basis": rbasis, "basis": basis,
            **issues,
            **({"carry_unchecked": True} if _carry.unchecked else {}),
            "problems": sum(len(issues[k]) for k in
                            ("positions", "missed", "double", "timing",
                             "boundary", "carry", "partial"))}


def render(rep: Dict[str, Any], record_path: str,
           project_year: Optional[int] = None) -> List[str]:
    """The console report in the house style (docs/output-style.md): a
    section per check — its findings as `- ` items, each with its why
    indented under it — then the notes and a one-line verdict.
    `project_year` is this project's year: a record two years back
    (`--prior`) opened "into 2024 (this project)" in a 2025 project
    (backlog TJ-0115)."""
    import sys
    from taxjson.lib.out import label, wrap
    y = rep["year"]
    py = project_year or y + 1
    L = [f"HAND-OFF CHECK — {y} (closed) into {py} (this project)"]
    import os as _os
    L += wrap(f"Record: {_os.path.normpath(str(record_path))}")
    L.append("")

    def sec(title, items, line, skipped=None):
        # `skipped`: a check of this section that did not run, said here
        # and not only in a note further down (backlog TJ-1437).
        L.append(f"{title.upper()} ({len(items)})")
        if not items and not skipped:
            L.append("  OK.")
        for it in items:
            L.extend(wrap(line(it), None, "- ", "  "))
            L.extend(wrap(it["why"], None, "    ", "    "))
        if skipped:
            L.append(f"  {skipped}")
        L.append("")

    if rep.get("partial"):
        sec(f"The {y} record itself", rep["partial"],
            lambda i: i.get("what") or f"closed at {i['closed_at']}")
    sec(f"Opening positions vs the {y} year-end books",
        rep["positions"],
        lambda i: (f"{i['symbol']:<26} {y}: {i['closed_qty']:>12g} "
                   f"@ {M.shown(i['closed_acb'], 2):>13,.2f}   here: "
                   f"{i['opening_qty']:>12g} @ {M.shown(i['opening_acb'], 2):>13,.2f}"))
    sec(f"Trades made in {y} that settle in {y + 1}, missing here",
        rep["missed"],
        lambda i: (f"{i['symbol']:<26} {i['qty']:>+12g}  traded "
                   f"{i['date']}  settles {i['date_settle']}"))
    sec("Sales reported in both years", rep["double"],
        lambda i: f"{i['symbol']:<26} {i.get('qty', 0):>12g}  {i['date']}")
    sec(f"Rows on different sides of Dec 31 in the two projects",
        rep.get("boundary") or [],
        lambda i: (f"{i['symbol']:<26} {i['action']:<10} dated "
                   f"{i['date']}  counted {i['tax_date']}  "
                   f"{M.shown(float(i.get('amount') or 0), 2):>12,.2f}"))
    sec(f"Written options on another premium timing than the {y} record",
        rep.get("timing") or [],
        lambda i: (f"{i['symbol']:<26} written {i['written']}  "
                   f"{y}: {i['closed_year_timing']}  here: "
                   f"{i['timing_here']}"))
    sec(f"Carry-forward inputs vs the {y} record", rep.get("carry") or [],
        lambda i: (f"{i['what']:<34} here: "
                   + (f"{M.shown(i['here'], 2):,.2f}" if isinstance(i['here'],
                                                       (int, float))
                      else str(i['here']))
                   + (f"   {y} record: {M.shown(i['record'], 2):,.2f}"
                      if isinstance(i['record'], (int, float)) else "")),
        skipped=(f"[carryover] claimed {y}: not compared (see the note)."
                 if rep.get("carry_unchecked") else None))
    from taxjson.lib.out import width as _width
    _shown = _width(sys.stdout) > 0
    for i, n in enumerate(rep["notes"]):
        # A message: shown to a person its lines flush-left, one blank
        # line after it when it wraps (docs/output-style.md, Messages).
        block = wrap(label("note", stream=sys.stdout) + n, None, "",
                     "" if _shown else "  ")
        L.extend(block)
        if _shown and len(block) > 1 and i + 1 < len(rep["notes"]):
            L.append("")
    if rep["notes"]:
        L.append("")
    L.append(f"{rep['problems']} problem(s)." if rep["problems"] else
             "No problem found, but one check could not run (see the note)."
             if rep.get("carry_unchecked") else
             "Everything the closed year carried forward is here, once.")
    return L

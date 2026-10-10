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
alongside (`filed_dispositions`). `taxjson handoff`, run in the next
year's project, compares that record with the new project's own books
(`check`).

Positions at Dec 31 are the engine's pools rebuilt from the rows settled
by Dec 31, with the superficial-loss deferrals the FULL history decided
(a December loss whose window runs into January is judged with the
January trades, exactly as the return was).
"""
from __future__ import annotations

import csv
import json
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

RECORD_VERSION = 2
QTY_TOL = 1e-6
ACB_ABS_TOL = 1.00          # CAD/USD: rounding across two runs
ACB_REL_TOL = 1e-4

RunGains = Callable[[List[str], Path], None]


class BooksError(ValueError):
    """A work/ book the hand-off reads exists but cannot be read."""


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _year_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, str) and v.strip().isdigit()


def record_problem(record: Any) -> Optional[str]:
    """Why a prior-year record (filed/<year>.json, read by `taxjson
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
                         f"`taxjson run` to rebuild it") from None


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


def _check_books(path: Path) -> None:
    """Validate one source book with the engine's own loader BEFORE it is
    merged into a temporary file: a damaged row was reported against
    /tmp/taxjson-handoff-*/equity_full.json, a file the user never saw
    and that is gone when the message is read (A2-1143)."""
    p = Path(path)
    if not p.exists():
        return
    from taxjson.lib.core import load_transactions
    try:
        load_transactions(p)
    except Exception as e:                          # noqa: BLE001
        raise BooksError(f"could not read {_label(p)} ({e}) — re-run "
                         f"`taxjson run` to rebuild it")


def _d(s: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _bdate(r: Dict[str, Any], basis: str) -> Optional[date]:
    if basis == "settle":
        return _d(r.get("date_settle")) or _d(r.get("date"))
    return _d(r.get("date"))


def groups(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """Taxable accounts split into the pools the engine blends:
    equities together, crypto together — in taxjson.toml order, the
    order the run blends them in: rows of two accounts at one moment
    follow it (CA-DATE-14 / US-DATE-13). Sorted by name, the snapshot
    replayed a same-moment sale after another account's buy and
    recorded a deferral the return never had (A2-1556)."""
    out: Dict[str, List[str]] = {"equity": [], "crypto": []}
    for name, a in (cfg.get("accounts") or {}).items():
        if not isinstance(a, dict) or a.get("type") != "taxable":
            continue
        out["crypto" if a.get("crypto") else "equity"].append(name)
    return {g: n for g, n in out.items() if n}


def _basis(settings: Dict[str, Any]) -> str:
    """The date basis in force (lib/country.settings_tax_date)."""
    from taxjson.lib.country import settings_tax_date
    return settings_tax_date(settings)


# ------------------------------------------------------------ snapshot

def _parse_adjust(cmd: str) -> Optional[Dict[str, Any]]:
    # "ADJUST 2025-01-02 10:00:00 SAMPLA.US CAD 12.3456"
    f = (cmd or "").split()
    if len(f) != 6 or f[0] != "ADJUST":
        return None
    try:
        amt = float(f[5])
    except ValueError:
        return None
    return {"date": f[1], "time": f[2], "symbol": f[3], "currency": f[4],
            "amount": amt}


def _landings(w: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Where a superficial-loss record's s.53(1)(f) additions land: the
    engine's own `adjusts` (one per taxable replacement, each with its
    pool symbol, trade and settle stamps), else — a gains file written
    before `adjusts` existed — the single lump `adjust_cmd`. The lump
    put a multi-symbol allocation on the first symbol (A2-0352), a
    January landing at Dec 31 (A2-0669), and on settle basis a bump
    dated the trade day before the losing sale settled (A2-1140)."""
    out: List[Dict[str, Any]] = []
    if isinstance(w.get("adjusts"), list):
        for a in w["adjusts"]:
            try:
                amt = float(a.get("amount") or 0.0)
            except (TypeError, ValueError):
                continue
            if not a.get("symbol") or not a.get("date") or abs(amt) < 1e-9:
                continue
            out.append({"symbol": a["symbol"], "date": a["date"],
                        "date_settle": a.get("date_settle") or a["date"],
                        "time": a.get("time") or "00:00:00",
                        "currency": a.get("currency") or "",
                        "account": a.get("account") or "",
                        "amount": amt})
        if out:
            cur = _parse_adjust(w.get("adjust_cmd") or "")
            for a in out:
                a["currency"] = a["currency"] or (cur or {}).get(
                    "currency", "")
        return out
    a = _parse_adjust(w.get("adjust_cmd") or "")
    if a:
        out.append(dict(a, date_settle=a["date"], account=""))
    return out


def snapshot(cache: Path, cfg: Dict[str, Any], as_of: str,
             run_gains: RunGains, common_flags: List[str],
             phantoms: Optional[Path] = None) -> Dict[str, Any]:
    """{group: {symbol: {qty, acb, deferred}}} for the taxable pools as
    of the end of `as_of` (the engine's date basis). `phantoms`: the
    project's missing-history path (lib/missing_history.
    missing_history_arg: missing_history.json and the accounts' .tt
    OPENING cost=unknown lines), None without either (the parameter
    keeps its pre-rename name)."""
    settings = cfg.get("settings", {}) or {}
    basis = _basis(settings)
    cut = _d(as_of)
    from taxjson.lib.income_dating import IncomeRules
    try:
        _ir = IncomeRules.from_settings(settings)
    except ValueError:          # no country: no record-date rule to apply
        _ir = IncomeRules(country="")
    from taxjson.lib.country import settings_country
    try:
        us = settings_country(settings) == "usa"
    except Exception:                               # noqa: BLE001
        us = False
    sheltered = cache / "sheltered_base.json"
    sheltered_ids = {r.get("id") for r in _rows(sheltered)} \
        if sheltered.exists() else set()
    out: Dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="taxjson-handoff-") as td:
        tdp = Path(td)
        for g, names in groups(cfg).items():
            rows = []
            for n in names:
                _check_books(cache / f"{n}_base.json")
                rows += _rows(cache / f"{n}_base.json")
            if not rows:
                continue
            fdoc: Dict[str, Any] = {}
            if not us:          # §1091: the US engine re-runs below
                full = tdp / f"{g}_full.json"
                full.write_text(json.dumps({"transactions": rows}))
                tail = ["--taxable"] + common_flags
                if sheltered.exists() and g == "equity":
                    tail += ["--sheltered", str(sheltered)]
                if phantoms is not None:
                    tail += ["--incomplete-history", str(phantoms)]
                full_out = tdp / f"{g}_full_gains.json"
                run_gains(tail + [str(full)], full_out)
                fdoc = json.loads(full_out.read_text(encoding="utf-8"))
            deferred: Dict[str, float] = {}
            adjust_rows = []
            for i, w in enumerate(fdoc.get("wash_sales") or []):
                if w.get("trigger_lot_id") in sheltered_ids:
                    continue            # permanent: lands in no taxable pool
                for j, a in enumerate(_landings(w)):
                    d = (_d(a.get("date_settle")) or _d(a.get("date"))
                         if basis == "settle" else _d(a.get("date")))
                    if d is None or d > cut:
                        continue        # lands after Dec 31 (A2-0669)
                    deferred[a["symbol"]] = deferred.get(a["symbol"], 0.0) \
                        + a["amount"]
                    adjust_rows.append({
                        "action": "ADJUST", "date": a["date"],
                        "date_settle": a.get("date_settle") or a["date"],
                        "time": a.get("time") or "00:00:00",
                        "symbol": a["symbol"],
                        "currency": a.get("currency") or "",
                        "net_amount": a["amount"], "quantity": 0.0,
                        "account": a.get("account") or names[0],
                        "id": f"HANDOFF_WASH_{i}_{j}"})
            # A trust ROC counts on its record date, as the engine books
            # it (CA-INC-DATE-ROC-TRUST; A2-0202).
            kept = [r for r in rows
                    if (d := (_d(_ir.roc_record_date(r))
                              or _bdate(r, basis))) is not None
                    and d <= cut]
            trunc = tdp / f"{g}_asof.json"
            trunc.write_text(json.dumps({"transactions": kept + adjust_rows}))
            if us:
                # The US engine adds a disallowed loss to the
                # replacement lot's basis itself (§1091(d)) and records
                # no ADJUST landing: the Dec-31 rows are re-run WITH the
                # wash rule, so the year-end basis carries it, as `list`
                # shows (A2-0353). A replacement after Dec 31 is not in
                # a Dec-31 lot either way.
                tail = ["--taxable"] + common_flags
                if sheltered.exists() and g == "equity":
                    tail += ["--sheltered", str(sheltered)]
            else:
                tail = ["--taxable", "--no-wash"] + common_flags
            if phantoms is not None:
                tail += ["--incomplete-history", str(phantoms)]
            trunc_out = tdp / f"{g}_asof_gains.json"
            run_gains(tail + [str(trunc)], trunc_out)
            tdoc = json.loads(trunc_out.read_text(encoding="utf-8"))
            pos: Dict[str, Dict[str, float]] = {}
            for h in tdoc.get("inventory") or []:
                sym = h.get("symbol")
                q = float(h.get("qty") or 0.0)
                if not sym or abs(q) < 1e-12:
                    continue
                p = pos.setdefault(sym, {"qty": 0.0, "acb": 0.0,
                                         "deferred": 0.0})
                p["qty"] += q
                p["acb"] += float(h.get("total_cost") or 0.0)
                if us:
                    deferred[sym] = deferred.get(sym, 0.0) + float(
                        h.get("deferred_wash") or 0.0)
            for sym, amt in deferred.items():
                if sym in pos:
                    pos[sym]["deferred"] = round(amt, 2)
            for p in pos.values():
                p["qty"] = round(p["qty"], 8)
                p["acb"] = round(p["acb"], 2)
            out[g] = pos
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
                                "net": round(float(r.get("net_amount")
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
        doc = _work_doc(Path(p))
        for t in doc.get("transactions", []):
            if t.get("gain") is None or t.get("action"):
                continue
            out.append({
                "account": t.get("account") or acct,
                "symbol": t.get("symbol"), "date": t.get("date"),
                "date_settle": t.get("date_settle") or t.get("date"),
                "qty": float(t.get("qty") or 0.0),
                "proceeds": round(float(t.get("proceeds") or 0.0), 2),
                "cost": round(float(t.get("cost") or 0.0), 2),
                "gain": round(float(t.get("gain") or 0.0), 2),
                "denied": round(float(t.get("disallowed_amount") or 0.0), 2),
            })
    return out


_FILED_COLS = ("symbol", "date", "qty", "proceeds", "cost", "gain")


def load_filed_dispositions(path: Path) -> List[Dict[str, Any]]:
    """The dispositions a return actually reported, when it was prepared
    with another tool. CSV with a header: symbol,date,qty,proceeds,cost,
    gain (account optional; date = the date the return used; amounts in
    the base currency; qty positive)."""
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
                "symbol": r["symbol"].strip(),
                "date": str(_d(r["date"]) or ""),
                "qty": abs(float(r["qty"])),
                "proceeds": round(float(r["proceeds"]), 2),
                "cost": round(float(r["cost"]), 2),
                "gain": round(float(r["gain"]), 2)})
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"{path}:{i}: bad row ({e})")
        import math
        if not all(math.isfinite(rows[-1][k])
                   for k in ("qty", "proceeds", "cost", "gain")):
            raise ValueError(f"{path}:{i}: bad row (an amount is not a "
                             f"finite number)")
        if not rows[-1]["date"]:
            raise ValueError(f"{path}:{i}: bad date {r['date']!r}")
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
    of capital's record date (lib/income_dating)."""
    a = str(r.get("action") or "").upper()
    if a in _TRADES:
        return _bdate(r, basis)
    try:
        return _d(ir.row_date(r)) or _d(r.get("date"))
    except Exception:                               # noqa: BLE001
        return _d(r.get("date"))


def boundary_rows(cache: Path, cfg: Dict[str, Any], year: int
                  ) -> List[Dict[str, Any]]:
    """Every taxable trade and income row of December `year` and January
    `year + 1` (by its own date or the date the books count it on), with
    that tax date: the next project's `handoff` checks rows the two
    projects put on different sides of Dec 31 — income a trust record
    date, a RIC January dividend or a local_timezone re-dating moves
    across the boundary, and a fill the overnight shift moved into
    January (A2-0120, A2-0343, A2-0344, A2-0670, A2-0675)."""
    settings = cfg.get("settings", {}) or {}
    basis = _basis(settings)
    ir = _income_rules(settings)
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
                out.append({"group": g, "account": n, "action": a,
                            "symbol": r.get("symbol"), "date": str(raw),
                            "date_settle": str(_d(r.get("date_settle"))
                                               or raw),
                            # Full precision: a coin reward of
                            # 0.000123456789 rounded to 8 places no
                            # longer matched its own row.
                            "quantity": float(r.get("quantity") or 0.0),
                            "net": round(float(r.get("net_amount")
                                               or 0.0), 2),
                            "tax_date": str(eff) if eff else None})
    out.sort(key=lambda x: (x["date"], x["symbol"] or "", x["action"]))
    return out


def record_fields(root: Path, cfg: Dict[str, Any], year: int,
                  gains_files: Dict[str, Path], run_gains: RunGains,
                  common_flags: List[str],
                  filed_csv: Optional[Path] = None) -> Dict[str, Any]:
    cache = Path(root) / "work"
    from taxjson.lib.missing_history import missing_history_arg
    mh_file = missing_history_arg(root)
    rec = {
        "record_version": RECORD_VERSION,
        "date_basis": _basis(cfg.get("settings", {}) or {}),
        "dispositions": dispositions(gains_files, year),
        "year_end": snapshot(cache, cfg, f"{year}-12-31", run_gains,
                             common_flags, mh_file),
        "settle_next_year": straddlers(cache, cfg, year),
        "boundary_rows": boundary_rows(cache, cfg, year),
    }
    if filed_csv is not None:
        fd = load_filed_dispositions(filed_csv)
        rec["filed_dispositions"] = fd
        rec["filed_totals"] = {
            "dispositions": len(fd),
            "proceeds": round(sum(r["proceeds"] for r in fd), 2),
            "gain": round(sum(r["gain"] for r in fd), 2),
            "source": str(filed_csv)}
    return rec


# ------------------------------------------------------- the record

class RecordError(ValueError):
    """A prior-year lock (filed/<year>.json) whose fields are not the
    shape close-year writes. One line naming the file and the field;
    each wrong type was a TypeError / KeyError traceback somewhere in
    check() (A2-0803)."""


def _num(v: Any, *, none_ok: bool = False) -> bool:
    import math
    if v is None:
        return none_ok
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _txt(v: Any, *, none_ok: bool = True) -> bool:
    return isinstance(v, str) or (none_ok and v is None)


# Per list: {field: (check, required)}.
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
                      "date": (_txt, False), "tax_date": (_txt, False)},
}


def validate_record(record: Any, path: Any = "the prior-year record"
                    ) -> Dict[str, Any]:
    """`record` when every field the hand-off reads has the shape
    close-year writes; else RecordError naming `path` and the field."""
    def bad(field: str, what: str) -> RecordError:
        return RecordError(f"{path}: {field} is not {what} — fix the "
                           f"lock or restore it from git (or re-close "
                           f"that year with `taxjson close-year --force` "
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
    ot = record.get("option_timing")
    if ot is not None and not isinstance(ot, dict):
        raise bad("option_timing", "an object")
    if record.get("carryforwards") is not None:
        from taxjson.lib.carryforward import block_problem
        prob = block_problem(record["carryforwards"])
        if prob:
            raise RecordError(f"{path}: {prob} — fix the lock or restore "
                              f"it from git (or re-close that year with "
                              f"`taxjson close-year --force` in its "
                              f"project)")
    return record


def load_record(path: Any) -> Dict[str, Any]:
    """Read and validate a prior-year lock (utf-8, a BOM tolerated:
    A2-0776). Any problem is a RecordError naming the file."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
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
    (core._root_matches_stock: ABC / ABC.B, ABC / ABC1). RBC
    re-describes a contract between yearly exports, and the opening
    must use this year's spelling (audit A2-0006) — reporting that as a
    missing lot pushed the user to the symbol that books the close as a
    new write."""
    from taxjson.lib.core import _OCC_OPTION_RE, _root_matches_stock
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
            if not (_root_matches_stock(ra, rb)
                    or _root_matches_stock(rb, ra)):
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
    settings = cfg.get("settings", {}) or {}
    from taxjson.lib.pipeline import option_timing_from_settings
    try:
        here_t = option_timing_from_settings(settings) or {}
    except ValueError:
        return []
    if not here_t:
        return []                       # no written-option timing (US)
    ry = int(record["year"])
    from taxjson.lib.core import load_transactions
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
            if lot.broker_closing or wy > ry:
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
                why = (f"written {lot.write_date} for {lot.premium:,.2f}: "
                       f"the {ry} record is on grant timing since "
                       f"{_since}, so the {wy} return taxed this premium; "
                       f"this project keeps the contract on close timing "
                       f"(option_grant_timing_since = "
                       f"{here_t.get('option_grant_since')}) and taxes it "
                       f"again when it closes. Set "
                       f"option_grant_timing_since = {_since} in this "
                       f"project, as the {ry} record has it.")
            else:
                why = (f"written {lot.write_date} for {lot.premium:,.2f}: "
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
                        "premium": round(lot.premium, 2),
                        "closed_year_timing": "grant" if was else "close",
                        "timing_here": "grant" if now else "close",
                        "why": why})
    return out


def _same_row(r: Dict[str, Any], x: Dict[str, Any]) -> bool:
    """A base row of this project and a record's boundary row are the
    same row: action, symbol, own date, quantity and amount."""
    if str(r.get("action") or "").upper() != x.get("action") \
            or r.get("symbol") != x.get("symbol"):
        return False
    if str(_d(r.get("date"))) != str(x.get("date")):
        return False
    if not _qty_eq(float(r.get("quantity") or 0.0),
                   float(x.get("quantity") or 0.0)):
        return False
    n, xn = float(r.get("net_amount") or 0.0), float(x.get("net") or 0.0)
    return abs(n - xn) <= max(0.01, 0.005 * abs(xn))


def _boundary_issues(cache: Path, cfg: Dict[str, Any],
                     record: Dict[str, Any], basis: str,
                     section2: set) -> List[Dict[str, Any]]:
    """Rows reported in neither year or in both because this project and
    the closed one date them on different sides of Dec 31 (see
    boundary_rows). Three cases:
      (a) this project counts a row in the closed year that the closed
          books never had — income its trust record date (or a RIC entry)
          moves back, or a row local_timezone re-dates to Dec 31 — so it
          is in neither return;
      (b) the closed books counted an income row in the closed year and
          this project counts it again in the next;
      (c) the closed project's inputs held an early-January row (an
          overnight fill moved past Dec 31) that this project lacks."""
    settings = cfg.get("settings", {}) or {}
    ir = _income_rules(settings)
    ry = int(record["year"])
    ye = date(ry, 12, 31)
    rec_rows = [x for x in record.get("boundary_rows") or []
                if isinstance(x, dict)]
    here = []
    for _g, names in groups(cfg).items():
        for n in names:
            here += [r for r in _rows(cache / f"{n}_base.json")
                     if str(r.get("action") or "").upper()
                     in _TRADES + _INCOME]
    out: List[Dict[str, Any]] = []
    used: set = set()

    def _match(r):
        for i, x in enumerate(rec_rows):
            if i not in used and _same_row(r, x):
                used.add(i)
                return x
        return None

    for r in here:
        if r.get("id") not in (None, "") and str(r["id"]) in section2:
            continue                    # section 2 already judges it
        a = str(r.get("action") or "").upper()
        eff, raw = _eff_date(r, basis, ir), _d(r.get("date"))
        if eff is None or eff > ye:
            continue
        q = float(r.get("quantity") or 0.0)
        if a in _INCOME:
            if not ((raw and raw > ye) or eff >= date(ry, 12, 30)):
                continue
        elif not (q < 0 and eff >= date(ry, 12, 30)):
            continue
        if _match(r) is not None:
            continue
        what = ("sale" if a in _TRADES else
                "return of capital" if a == "ADJUST" else "income")
        out.append({"symbol": r.get("symbol"), "action": a,
                    "date": str(raw), "tax_date": str(eff),
                    "amount": round(float(r.get("net_amount") or 0.0), 2),
                    "why": (f"this project counts this {what} on {eff} "
                            f"(in {ry})"
                            + (f", though it is dated {raw}" if raw != eff
                               else "")
                            + f", but the {ry} books never had it (their "
                            f"inputs did not hold it): it is in neither "
                            f"return. It belongs on the {ry} return — "
                            f"amend {ry} (or confirm the slip reported "
                            f"it).")})
    for i, x in enumerate(rec_rows):
        a = x.get("action")
        xd = _d(x.get("tax_date"))
        if xd is None:
            continue
        hit = None
        for r in here:
            if _same_row(r, x):
                hit = r
                break
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
            out.append({"symbol": x.get("symbol"), "action": a,
                        "date": x.get("date"), "tax_date": str(xd),
                        "amount": x.get("net"),
                        "why": (f"the {ry} project's inputs hold this row "
                                f"dated {x.get('date')}, which counts in "
                                f"{ry + 1}, so the {ry} return left it "
                                f"out — but this project does not have it: "
                                f"it is in neither return. Add it here.")})
    return out


def check(root: Path, cfg: Dict[str, Any], record: Dict[str, Any],
          opening: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a closed year's record with this project's books.
    `opening` is `snapshot(...)` of THIS project at the record's Dec 31."""
    cache = Path(root) / "work"
    settings = cfg.get("settings", {}) or {}
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
    for g in sorted(set(record.get("year_end") or {}) | set(opening)):
        prev = (record.get("year_end") or {}).get(g, {})
        now = opening.get(g, {})
        renamed = _redescribed_options(prev, now)
        for old, new in renamed:
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
            dq = n["qty"] - p["qty"]
            da = n["acb"] - p["acb"]
            if _qty_eq(n["qty"], p["qty"]) \
                    and _acb_close(n["acb"], p["acb"]):
                continue
            if sym in basis_dq and abs(basis_dq[sym]) > QTY_TOL \
                    and _qty_eq(dq, basis_dq[sym]):
                continue                # the straddling trade (section 2)
            k = _root_sym(sym)
            filed_diff = None
            if record.get("filed_dispositions") is not None:
                filed_diff = round(filed_by.get(k, 0.0)
                                   - book_by.get(k, 0.0), 2)
            item = {"group": g, "symbol": sym,
                    "closed_qty": p["qty"], "closed_acb": p["acb"],
                    "closed_deferred": p.get("deferred", 0.0),
                    "opening_qty": n["qty"], "opening_acb": n["acb"],
                    "qty_diff": round(dq, 8), "acb_diff": round(da, 2),
                    "filed_gain_diff": filed_diff}
            if not _qty_eq(n["qty"], p["qty"]):
                item["why"] = (
                    f"{ry} books hold {p['qty']:g} on Dec 31; this "
                    f"project opens with {n['qty']:g}. A lot or a sale is "
                    f"missing from the opening file (or from the {ry} "
                    f"books). Fix whichever side is wrong; quantities "
                    f"have no 'as filed' choice.")
            else:
                parts = [f"cost at Dec 31 {ry}: {p['acb']:,.2f} in the "
                         f"{ry} books, {n['acb']:,.2f} here "
                         f"({da:+,.2f})"]
                if p.get("deferred"):
                    # §1091(d) in a US project, never Canada's term
                    # (A2-1297).
                    parts.append(f"the {ry} books carry {p['deferred']:,.2f}"
                                 f" of deferred {_loss_words} in it")
                if filed_diff:
                    parts.append(f"the {ry} return reported {filed_diff:+,.2f}"
                                 f" of gain on this security differently "
                                 f"from the {ry} books")
                parts.append(
                    f"choose one: keep {ry} as filed and open with the "
                    f"cost that return implied, or amend {ry} and open "
                    f"with {p['acb']:,.2f}; never both")
                item["why"] = "; ".join(parts) + "."
            issues["positions"].append(item)

    # 1b. An option row this year's export marks CLOSING whose position
    # the opening holds only under another root (a .tt written with the
    # closed year's ABC while the export closes ABC.B): the opening
    # matches the record, yet the close is booked as a NEW written call
    # and the carried long never closes (audit A2-0006).
    from taxjson.lib.core import load_transactions
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
            for r in _rows(cache / f"{n}_base.json"):
                if r.get("action") in ("BUYSELL", "ASSIGN"):
                    here.append(dict(r, _acct=n))

    used_here: set = set()

    def _found(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """This project's row for the record's straddling trade `s`: the
        same symbol and quantity, and the same trade date — or, within
        3 days (an opening .tt dates a row on its settlement day), the
        same net amount. Symbol + quantity + 3 days let a DISTINCT
        same-size January sale clear a sale missing from both years, or
        raise a false "double" (A2-0354). A row answers one item."""
        sd = _d(s.get("date"))
        for idx, r in enumerate(here):
            if idx in used_here or r.get("symbol") != s["symbol"]:
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
                                     round(abs(float(s["qty"])), 6),
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
    own_closed: List[Tuple[str, float, Optional[date], Optional[date],
                           float]] = []
    _pos: Dict[str, float] = {}
    for r in sorted(here, key=lambda x: (_bdate(x, basis) or date.max,
                                         str(x.get("time") or ""))):
        sym = r.get("symbol") or ""
        q = float(r.get("quantity") or 0.0)
        before = _pos.get(sym, 0.0)
        _pos[sym] = before + q
        if q >= 0 or before <= QTY_TOL:
            continue
        if (_bdate(r, basis) or date.max) > date(ry, 12, 31):
            continue
        own_closed.append((_root_sym(sym), min(abs(q), before),
                           _d(r.get("date")), _d(r.get("date_settle")),
                           abs(float(r.get("net_amount") or 0.0))))

    def _is_own(c: Dict[str, Any]) -> bool:
        k, cq = _root_sym(c["symbol"]), abs(float(c["qty"]))
        cds = {_d(c.get("date")), _d(c.get("date_settle"))} - {None}
        cp = abs(float(c.get("proceeds") or 0.0))
        for ok, oq, otd, osd, onet in own_closed:
            if ok != k or not _qty_eq(oq, cq):
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
        doc = _work_doc(Path(p))
        for t in doc.get("transactions", []):
            if t.get("gain") is None or t.get("action"):
                continue
            td = _d(t.get("date"))
            if not td or td > date(ry + 1, 1, 10):
                continue
            k = _root_sym(t.get("symbol"))
            q = abs(float(t.get("qty") or 0.0))
            tp = float(t.get("proceeds") or 0.0)
            # A short cover: the engine's proceeds are the negated cover
            # cost and its cost the negated short-sale proceeds; another
            # tool's CSV reports the short-sale proceeds (A2-0674).
            # Only against another tool's CSV: the record's own
            # dispositions use the engine's convention, and a grant-timed
            # premium row (proceeds 0) matched every buy-back otherwise.
            tc = float(t.get("cost") or 0.0)
            tp_alt = (-tc if (record.get("filed_dispositions") is not None
                              and (t.get("direction") == "SHORT" or tp < 0)
                              and abs(tc) > 0.005) else None)
            tol = max(1.0, 0.01 * abs(tp))
            for c in closed:
                if _root_sym(c["symbol"]) != k:
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
                if (_root_sym(c["symbol"]), round(abs(float(c["qty"])), 6),
                        str(c.get("date"))) in reported_double:
                    break               # already one item in section 2
                if _is_own(c):
                    continue            # the closed sale is its own row here
                # The date the closed RETURN used (its record's basis).
                cd = (_d(c.get("date")) if rbasis == "trade"
                      else (_d(c.get("date_settle")) or _d(c.get("date"))))
                issues["double"].append({
                    "symbol": t.get("symbol"), "date": str(td),
                    "qty": q, "gain": round(float(t.get("gain") or 0),
                                            2),
                    "closed_date": str(cd),
                    "why": (f"a {q:g}-unit sale of "
                            f"{t.get('symbol')} on {td} is in this "
                            f"year's totals, and the {ry} record "
                            f"has the same sale on {cd}. Report it "
                            f"in one year only.")})
                break
    # 4. Rows the two projects put on different sides of Dec 31.
    if isinstance(record.get("boundary_rows"), list):
        issues["boundary"] = _boundary_issues(
            cache, cfg, record, basis,
            {str(here[i].get("id")) for i in used_here
             if here[i].get("id") not in (None, "")})
    else:
        issues["notes"].append(
            f"The {ry} record predates the Dec-31 row list: income or a "
            f"trade that this project dates in {ry} (a trust's record "
            f"date, a RIC January dividend, a local_timezone re-dating) "
            f"is not checked. Re-close {ry} with the current taxjson to "
            f"check it.")
    # 5. Carry-forwards: this project's inputs vs what the closed year
    # carried forward (tax-logic CA-CARRY-05, US-CARRY-03).
    from taxjson.lib.carryforward import handoff_issues
    issues["carry"] = handoff_issues(root, cfg, record)
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
            "problems": sum(len(issues[k]) for k in
                            ("positions", "missed", "double", "timing",
                             "boundary", "carry", "partial"))}


def render(rep: Dict[str, Any], record_path: str) -> List[str]:
    """The console report in the house style (docs/output-style.md): a
    section per check — its findings as `- ` items, each with its why
    indented under it — then the notes and a one-line verdict."""
    import sys
    from taxjson.lib.out import label, wrap
    y = rep["year"]
    L = [f"HAND-OFF CHECK — {y} (closed) into {y + 1} (this project)"]
    L += wrap(f"Record: {record_path}")
    L.append("")

    def sec(title, items, line):
        L.append(f"{title.upper()} ({len(items)})")
        if not items:
            L.append("  OK.")
        for it in items:
            L.extend(wrap(line(it), None, "- ", "  "))
            L.extend(wrap(it["why"], None, "    ", "    "))
        L.append("")

    if rep.get("partial"):
        sec(f"The {y} record itself", rep["partial"],
            lambda i: f"closed at {i['closed_at']}")
    sec(f"Opening positions vs the {y} year-end books",
        rep["positions"],
        lambda i: (f"{i['symbol']:<26} {y}: {i['closed_qty']:>12g} "
                   f"@ {i['closed_acb']:>13,.2f}   here: "
                   f"{i['opening_qty']:>12g} @ {i['opening_acb']:>13,.2f}"))
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
                   f"{float(i.get('amount') or 0):>12,.2f}"))
    sec(f"Written options on another premium timing than the {y} record",
        rep.get("timing") or [],
        lambda i: (f"{i['symbol']:<26} written {i['written']}  "
                   f"{y}: {i['closed_year_timing']}  here: "
                   f"{i['timing_here']}"))
    sec(f"Carry-forward inputs vs the {y} record", rep.get("carry") or [],
        lambda i: (f"{i['what']:<34} here: "
                   + (f"{i['here']:,.2f}" if isinstance(i['here'],
                                                       (int, float))
                      else str(i['here']))
                   + (f"   {y} record: {i['record']:,.2f}"
                      if isinstance(i['record'], (int, float)) else "")))
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
             "Everything the closed year carried forward is here, once.")
    return L

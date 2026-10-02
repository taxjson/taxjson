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


def _rows(path: Path) -> List[Dict[str, Any]]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("transactions", []) if isinstance(doc, dict) else doc
    return [r for r in rows if isinstance(r, dict)]


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
    equities together, crypto together."""
    out: Dict[str, List[str]] = {"equity": [], "crypto": []}
    for name, a in sorted((cfg.get("accounts") or {}).items()):
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
    # "ADJUST 2025-02-03 09:30:04 AMD.US CAD 44.0834"
    f = (cmd or "").split()
    if len(f) != 6 or f[0] != "ADJUST":
        return None
    try:
        amt = float(f[5])
    except ValueError:
        return None
    return {"date": f[1], "time": f[2], "symbol": f[3], "currency": f[4],
            "amount": amt}


def snapshot(cache: Path, cfg: Dict[str, Any], as_of: str,
             run_gains: RunGains, common_flags: List[str],
             phantoms: Optional[Path] = None) -> Dict[str, Any]:
    """{group: {symbol: {qty, acb, deferred}}} for the taxable pools as
    of the end of `as_of` (the engine's date basis)."""
    settings = cfg.get("settings", {}) or {}
    basis = _basis(settings)
    cut = _d(as_of)
    from taxjson.lib.income_dating import IncomeRules
    try:
        _ir = IncomeRules.from_settings(settings)
    except ValueError:          # no country: no record-date rule to apply
        _ir = IncomeRules(country="")
    sheltered = cache / "sheltered_base.json"
    sheltered_ids = {r.get("id") for r in _rows(sheltered)} \
        if sheltered.exists() else set()
    out: Dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="taxjson-handoff-") as td:
        tdp = Path(td)
        for g, names in groups(cfg).items():
            rows = []
            for n in names:
                rows += _rows(cache / f"{n}_base.json")
            if not rows:
                continue
            full = tdp / f"{g}_full.json"
            full.write_text(json.dumps({"transactions": rows}))
            tail = ["--taxable"] + common_flags
            if sheltered.exists() and g == "equity":
                tail += ["--sheltered", str(sheltered)]
            if phantoms is not None and phantoms.exists():
                tail += ["--incomplete-history", str(phantoms)]
            full_out = tdp / f"{g}_full_gains.json"
            run_gains(tail + [str(full)], full_out)
            fdoc = json.loads(full_out.read_text(encoding="utf-8"))
            deferred: Dict[str, float] = {}
            adjust_rows = []
            for i, w in enumerate(fdoc.get("wash_sales") or []):
                if w.get("trigger_lot_id") in sheltered_ids:
                    continue            # permanent: lands in no taxable pool
                a = _parse_adjust(w.get("adjust_cmd") or "")
                if not a or not _d(a["date"]) or _d(a["date"]) > cut:
                    continue
                deferred[a["symbol"]] = deferred.get(a["symbol"], 0.0) \
                    + a["amount"]
                adjust_rows.append({
                    "action": "ADJUST", "date": a["date"],
                    "date_settle": a["date"], "time": a["time"],
                    "symbol": a["symbol"], "currency": a["currency"],
                    "net_amount": a["amount"], "quantity": 0.0,
                    "account": names[0], "id": f"HANDOFF_WASH_{i}"})
            # A trust ROC counts on its record date, as the engine books
            # it (CA-INC-DATE-ROC-TRUST; A2-0202).
            kept = [r for r in rows
                    if (d := (_d(_ir.roc_record_date(r))
                              or _bdate(r, basis))) is not None
                    and d <= cut]
            trunc = tdp / f"{g}_asof.json"
            trunc.write_text(json.dumps({"transactions": kept + adjust_rows}))
            tail = ["--taxable", "--no-wash"] + common_flags
            if phantoms is not None and phantoms.exists():
                tail += ["--incomplete-history", str(phantoms)]
            trunc_out = tdp / f"{g}_asof_gains.json"
            run_gains(tail + [str(trunc)], trunc_out)
            tdoc = json.loads(trunc_out.read_text(encoding="utf-8"))
            pos: Dict[str, Dict[str, float]] = {}
            for h in tdoc.get("inventory") or []:
                sym = h.get("symbol")
                q = float(h.get("qty") or 0.0)
                if not sym or abs(q) < QTY_TOL:
                    continue
                p = pos.setdefault(sym, {"qty": 0.0, "acb": 0.0,
                                         "deferred": 0.0})
                p["qty"] += q
                p["acb"] += float(h.get("total_cost") or 0.0)
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
        try:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
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
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in _FILED_COLS
                   if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s) "
                             f"{', '.join(missing)} (need "
                             f"{', '.join(_FILED_COLS)})")
        for i, r in enumerate(rd, start=2):
            try:
                rows.append({
                    "account": (r.get("account") or "").strip(),
                    "symbol": r["symbol"].strip(),
                    "date": str(_d(r["date"]) or ""),
                    "qty": abs(float(r["qty"])),
                    "proceeds": round(float(r["proceeds"]), 2),
                    "cost": round(float(r["cost"]), 2),
                    "gain": round(float(r["gain"]), 2)})
            except (KeyError, ValueError) as e:
                raise ValueError(f"{path}:{i}: bad row ({e})")
            if not rows[-1]["date"]:
                raise ValueError(f"{path}:{i}: bad date {r['date']!r}")
    return rows


def record_fields(root: Path, cfg: Dict[str, Any], year: int,
                  gains_files: Dict[str, Path], run_gains: RunGains,
                  common_flags: List[str],
                  filed_csv: Optional[Path] = None) -> Dict[str, Any]:
    cache = Path(root) / "work"
    phantoms = Path(root) / "phantoms.json"
    rec = {
        "record_version": RECORD_VERSION,
        "date_basis": _basis(cfg.get("settings", {}) or {}),
        "dispositions": dispositions(gains_files, year),
        "year_end": snapshot(cache, cfg, f"{year}-12-31", run_gains,
                             common_flags, phantoms),
        "settle_next_year": straddlers(cache, cfg, year),
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


# ------------------------------------------------------------ check

def _root_sym(sym: str) -> str:
    """Symbol with a leading exchange/currency difference ignored, for
    matching another tool's spelling (AAPL.US vs AAPL)."""
    return re.sub(r"\.(US|TO|V|CN|NE|L|AX)$", "", (sym or "").upper())


def _redescribed_options(prev: Dict[str, Any], now: Dict[str, Any]
                         ) -> List[Tuple[str, str]]:
    """(closed-year symbol, this project's symbol) pairs that are ONE
    option position under two roots: the record holds it only as one,
    the opening only as the other, with the same expiry, right, strike,
    listing, quantity and cost and roots naming the same company
    (core._root_matches_stock: RCI / RCI.B, TRX / TRX1). RBC
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
        "positions": [], "missed": [], "double": [], "notes": []}

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
                f"root — the broker re-described the contract (RBC: RCI "
                f"→ RCI.B). Accepted as one position; this year's "
                f"closing rows must use {new}.")
        _skip = {s for pair in renamed for s in pair}
        for sym in sorted(set(prev) | set(now)):
            if sym in _skip:
                continue
            p = prev.get(sym, {"qty": 0.0, "acb": 0.0, "deferred": 0.0})
            n = now.get(sym, {"qty": 0.0, "acb": 0.0, "deferred": 0.0})
            dq = n["qty"] - p["qty"]
            da = n["acb"] - p["acb"]
            if abs(dq) <= QTY_TOL * max(1.0, abs(p["qty"])) \
                    and _acb_close(n["acb"], p["acb"]):
                continue
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
            if abs(dq) > QTY_TOL * max(1.0, abs(p["qty"])):
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
                    parts.append(f"the {ry} books carry {p['deferred']:,.2f}"
                                 f" of deferred superficial losses in it")
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
    # closed year's RCI while the export closes RCI.B): the opening
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

    def _found(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for r in here:
            if r.get("symbol") != s["symbol"]:
                continue
            if abs(float(r.get("quantity") or 0) - s["qty"]) > \
                    QTY_TOL * max(1.0, abs(s["qty"])):
                continue
            rd = _d(r.get("date"))
            if rd and abs((rd - _d(s["date"])).days) <= 3:
                return r
        return None

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
                    + ". Add it (dated its settlement day in a .tt file, "
                    "or keep the broker row) so it is reported once.")))
        else:
            if hit is not None and (_bdate(hit, basis) or date.min).year \
                    > ry:
                issues["double"].append(dict(s, why=(
                    f"the {ry} return used trade dates, so this "
                    f"{s['date']} trade was reported in {ry}; this "
                    f"project books it again in {ry + 1}.")))

    # 3. Dispositions reported by the closed year and again here.
    closed = (record.get("filed_dispositions")
              if record.get("filed_dispositions") is not None
              else record.get("dispositions")) or []
    from taxjson.lib.report_model import resolve_gains_files
    taxable = {n for ns in groups(cfg).values() for n in ns}
    for acct, p in resolve_gains_files(cache).items():
        if acct not in taxable:
            continue
        try:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for t in doc.get("transactions", []):
            if t.get("gain") is None or t.get("action"):
                continue
            td = _d(t.get("date"))
            if not td or td > date(ry + 1, 1, 10):
                continue
            k = _root_sym(t.get("symbol"))
            q = abs(float(t.get("qty") or 0.0))
            for c in closed:
                if _root_sym(c["symbol"]) != k:
                    continue
                if abs(abs(c["qty"]) - q) > max(QTY_TOL, 0.005 * q):
                    continue
                cd = _d(c.get("date_settle") or c.get("date"))
                tp = float(t.get("proceeds") or 0.0)
                if abs(c["proceeds"] - tp) > max(5.0, 0.01 * abs(tp)):
                    continue            # a different sale of the same size
                if cd and abs((cd - td).days) <= 5:
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
    if record.get("filed_dispositions") is None:
        issues["notes"].append(
            f"The {ry} record holds taxjson's own dispositions. If that "
            f"return was prepared with another tool, re-close {ry} with "
            f"--filed-dispositions so doubles are checked against what "
            f"was actually filed.")
    return {"year": ry, "record_basis": rbasis, "basis": basis,
            **issues,
            "problems": sum(len(issues[k]) for k in
                            ("positions", "missed", "double"))}


def render(rep: Dict[str, Any], record_path: str) -> List[str]:
    y = rep["year"]
    L = [f"HAND-OFF CHECK — {y} (closed) into {y + 1} (this project)",
         f"record: {record_path}", ""]

    def sec(title, items, line):
        L.append(f"== {title} ({len(items)})")
        if not items:
            L.append("   OK.")
        for it in items:
            L.append("   " + line(it))
            L.append("      " + it["why"])
        L.append("")

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
    for n in rep["notes"]:
        L.append("note: " + n)
    L.append("")
    L.append(f"{rep['problems']} problem(s)." if rep["problems"] else
             "Everything the closed year carried forward is here, once.")
    return L

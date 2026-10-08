"""Filing positions against the loss rule: `.tt` ALLOWLOSS lines.

The superficial-loss rule (Canada, s.54) and the wash-sale rule (USA,
§1091) are applied by the engines as the law's mechanical test. A user
may take a filing position against one specific denial (a replacement
counted inside the 30 days from the settlement date and outside them
from the trade date, say). That position is declared, never inferred:

    ALLOWLOSS <sale date> <symbol> [<qty>] reason="<text>"

one line in a `.tt` file of the TAXABLE account that realised the loss,
date first like the other dated lines (JOURNAL, RENAME). The reason is
required. The line names ONE sale: its date (trade or settlement date),
its symbol as the books spell it, and, when two sales of that symbol on
that day would both be denied, its units.

What the engines do with it (tax-logic CA-SL-18 / US-WASH-25): the
denial is not applied to that sale — the loss stays allowed, the
replacement's ACB / basis is NOT raised by it (no double benefit) and
its holding period is not tacked (US). Everything else is computed as
without the line: the replacement units the denial would have used are
still used by it (every other sale's verdict is unchanged), and each
gains file records what the rule would have denied and why
(`loss_overrides`, and `loss_override` on the sale's rows).

What the run does: a line that matches no denied loss, or several,
stops it with a message naming the line (`problems`); every position
is said once per run as a Warning (`warning_message`), listed in
`taxjson sum` (FILING POSITIONS, `filing_positions` in --json), in
`taxjson wash-sales` and in the checklist's filing-positions step.

The run writes the project's lines to work/loss_overrides.json (STATE)
before any gains pass; every engine run of the project's books gets
them through `--loss-overrides` (`flags`).
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

KEYWORD = "ALLOWLOSS"
FORM = 'ALLOWLOSS <sale date> <symbol> [<qty>] reason="<text>"'
STATE = "loss_overrides.json"
SCHEMA = 1

STATUS_APPLIED = "applied"
STATUS_UNMATCHED = "unmatched"
STATUS_AMBIGUOUS = "ambiguous"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REASON_RE = re.compile(r'reason\s*=\s*"([^"]*)"')
# Units: the pool's own tolerance is far below this; a declared quantity
# is what the broker printed.
_QTY_TOL = 1e-6


class LossOverrideError(ValueError):
    """One or more ALLOWLOSS lines cannot be read (each named)."""


# ------------------------------------------------------------- the line

def parse_line(line: str, source: str = "") -> Optional[Dict[str, Any]]:
    """`ALLOWLOSS <date> <symbol> [<qty>] reason="<text>"` -> {date,
    symbol, qty (None when not given), reason, line}, or None when the
    line is not an ALLOWLOSS line. Raises ValueError naming the form on
    a malformed one. A `#` inside the quoted reason is text; after it, a
    comment."""
    where = f"{source}: " if source else ""
    m = _REASON_RE.search(line)
    reason = m.group(1).strip() if m else None
    rest = (line[:m.start()] + " " + line[m.end():]) if m else line
    rest = rest.split("#", 1)[0]
    parts = rest.split()
    if not parts or parts[0] != KEYWORD:
        return None
    shown = line.strip()
    if '"' in rest:
        raise ValueError(
            f"{where}malformed {KEYWORD} line — the reason is one quoted "
            f"text, reason=\"...\" (expected `{FORM}`): {shown!r}")
    if not reason:
        raise ValueError(
            f"{where}{KEYWORD} needs a reason: say why you take this "
            f"position, reason=\"...\" (expected `{FORM}`): {shown!r}")
    toks = parts[1:]
    if len(toks) >= 2 and not _is_date(toks[0]) and _is_date(toks[1]):
        raise ValueError(
            f"{where}malformed {KEYWORD} line — a .tt line is date first: "
            f"`{KEYWORD} {toks[1]} {toks[0].upper()}"
            f"{' ' + ' '.join(toks[2:]) if toks[2:] else ''} reason=...` "
            f"(expected `{FORM}`): {shown!r}")
    if len(toks) not in (2, 3):
        raise ValueError(
            f"{where}malformed {KEYWORD} line — expected `{FORM}`: "
            f"{shown!r}")
    date, symbol = toks[0], toks[1].upper()
    if not _is_date(date):
        raise ValueError(
            f"{where}{KEYWORD} date {date!r} is not a valid YYYY-MM-DD "
            f"date (expected `{FORM}`): {shown!r}")
    from datetime import date as _date
    if date > _date.today().isoformat():
        raise ValueError(
            f"{where}{KEYWORD} line is dated {date}, in the future — it "
            f"names a sale that has happened (check the year): {shown!r}")
    qty = None
    if len(toks) == 3:
        try:
            qty = abs(float(toks[2].replace(",", "")))
        except ValueError:
            qty = None
        if qty is None or not math.isfinite(qty) or qty == 0:
            raise ValueError(
                f"{where}{KEYWORD} quantity {toks[2]!r} is not a number of "
                f"units sold (expected `{FORM}`): {shown!r}")
    from taxjson.lib.brokerages.base import canonical_ca_listing
    from taxjson.lib.core import is_option_symbol
    if not is_option_symbol(symbol):
        symbol = canonical_ca_listing(symbol, "") or symbol
    return {"date": date, "symbol": symbol, "qty": qty, "reason": reason,
            "line": shown}


def _is_date(tok: str) -> bool:
    if not _DATE_RE.match(tok or ""):
        return False
    try:
        datetime.strptime(tok, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def read_project(root: Path, accounts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every ALLOWLOSS line of the accounts' .tt files, in account order
    (each with `account` and `where`, "inputs/<acct>/<file>:<line>").
    Raises LossOverrideError listing each line that cannot be used: a
    malformed one, one in a sheltered account (its losses are not on
    the return), two lines naming the same sale."""
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.dated_events import _where, tt_files
    out: List[Dict[str, Any]] = []
    problems: List[str] = []
    seen: Dict[Tuple, str] = {}
    for acct in sorted(accounts or {}):
        acfg = accounts.get(acct) or {}
        for tt in tt_files(Path(root) / "inputs" / acct):
            try:
                text = read_text_utf8(tt)
            except (OSError, ValueError):
                continue        # the .tt stage names an unreadable file
            for n, raw in enumerate(text.splitlines(), 1):
                if KEYWORD not in raw:
                    continue
                where = _where(acct, tt, n)
                try:
                    p = parse_line(raw, where)
                except ValueError as e:
                    problems.append(str(e))
                    continue
                if p is None:
                    continue
                if isinstance(acfg, dict) and acfg.get("type") != "taxable":
                    problems.append(
                        f"{where}: {KEYWORD} names a loss on your return; "
                        f"account {acct} is {acfg.get('type') or 'not taxable'}"
                        f" (a registered account's losses are not on the "
                        f"return) — put the line in the taxable account that "
                        f"sold: {p['line']!r}")
                    continue
                k = (acct, p["date"], p["symbol"], p["qty"])
                if k in seen:
                    problems.append(
                        f"{where}: the same sale is named twice ({seen[k]} "
                        f"too) — keep one line: {p['line']!r}")
                    continue
                seen[k] = where
                p.update(account=acct, where=where)
                out.append(p)
    if problems:
        raise LossOverrideError("\n".join(problems))
    return out


# ------------------------------------------------------------ the state

def state_path(cache: Path) -> Path:
    return Path(cache) / STATE


def write_state(cache: Path, items: Sequence[Dict[str, Any]]) -> None:
    """work/loss_overrides.json: the run's lines. Rewritten only when
    they change (so `run --fast` keeps its cache); a project that never
    had a line never gets the file, one whose lines were deleted keeps
    an empty list (its mtime tells the cached gains to rebuild)."""
    p = state_path(cache)
    if not items and not p.exists():
        return
    text = json.dumps({"schema_version": SCHEMA,
                       "overrides": list(items)}, indent=2) + "\n"
    try:
        if p.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(text, encoding="utf-8")


def read_state(cache: Path) -> List[Dict[str, Any]]:
    """The lines the last run recorded ([] when none or unreadable)."""
    try:
        return load_file(state_path(cache))
    except (OSError, ValueError):
        return []


def load_file(path) -> List[Dict[str, Any]]:
    """The overrides of a --loss-overrides file (the state's form, or a
    bare list). Raises ValueError on a file that is not one."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    items = doc.get("overrides") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        raise ValueError(f"{path}: not a loss-overrides file")
    out = []
    for i, it in enumerate(items):
        if not (isinstance(it, dict) and it.get("account")
                and it.get("date") and it.get("symbol")
                and str(it.get("reason") or "").strip()):
            raise ValueError(f"{path}: entry {i + 1} needs account, date, "
                             f"symbol and reason")
        out.append(dict(it, where=it.get("where") or f"{path}#{i + 1}",
                        qty=(abs(float(it["qty"]))
                             if it.get("qty") not in (None, "") else None)))
    return out


def flags(cache: Path) -> List[str]:
    """The engine flag for the project's lines ([] when none)."""
    return (["--loss-overrides", str(state_path(cache))]
            if read_state(cache) else [])


# ------------------------------------------------- engine-side matching

class Plan:
    """Which rows each override may name: `row_of` {row id: override
    index}; `groups` {index: [[rows of one sale], ...]} — the candidate
    sales; `day_rows` {index: [the account's trades that day]} for the
    message when none is denied."""

    def __init__(self, overrides: Sequence[Dict[str, Any]]):
        self.overrides = list(overrides)
        self.row_of: Dict[str, int] = {}
        self.groups: Dict[int, List[List[Any]]] = {}
        self.day_rows: Dict[int, List[Any]] = {}

    def __bool__(self) -> bool:
        return bool(self.overrides)


def plan(overrides: Sequence[Dict[str, Any]], rows_in_order) -> Plan:
    """Resolve each override to the sales it can name in `rows_in_order`
    (the engine's processing order): rows of its account and symbol
    traded or settled on its date, grouped into sales the way Canada's
    formula groups fills (core.disposition_groups: one account's
    uninterrupted same-day sell-down is one sale), kept when the sale's
    units, or one fill's, equal the declared quantity."""
    from taxjson.lib.core import disposition_groups
    pl = Plan(overrides)
    if not pl:
        return pl
    rows = list(rows_in_order)
    grp = disposition_groups(rows)
    for i, ov in enumerate(pl.overrides):
        d = ov["date"]
        mine = [t for t in rows
                if t.account == ov["account"]
                and t.action in ("BUYSELL", "ASSIGN")
                and abs(float(t.quantity or 0)) > 0
                and d in (t.date, t.date_settle or t.date)]
        pl.day_rows[i] = mine
        by_g: Dict[int, List[Any]] = {}
        for t in mine:
            if t.symbol != ov["symbol"]:
                continue
            by_g.setdefault(grp.get(id(t), id(t)), []).append(t)
        sales = list(by_g.values())
        q = ov.get("qty")
        if q:
            def _eq(a: float) -> bool:
                return abs(abs(a) - q) <= _QTY_TOL * max(1.0, q)
            sales = [s for s in sales
                     if _eq(sum(float(t.quantity) for t in s))
                     or any(_eq(float(t.quantity)) for t in s)]
        pl.groups[i] = sales
        for s in sales:
            for t in s:
                # A row two overrides name: the first keeps it (the run
                # refuses two lines of one sale before the engine).
                pl.row_of.setdefault(t.id, i)
    return pl


def new_record() -> Dict[str, Any]:
    return {"qty": 0.0, "loss": 0.0, "disallowed": 0.0, "permanent": 0.0,
            "replacements": []}


def add_replacement(rec: Dict[str, Any], trg, units: float, amount: float,
                    holder: str) -> None:
    """One replacement the denial would have used (merged per row)."""
    for r in rec["replacements"]:
        if r["id"] == trg.id:
            r["units"] += units
            r["amount"] += amount
            return
    rec["replacements"].append({
        "id": trg.id, "date": trg.date,
        "date_settle": trg.date_settle or trg.date,
        "account": trg.account, "symbol": trg.symbol,
        "units": units, "amount": amount, "holder": holder})


def _days(a: str, b: str) -> Optional[int]:
    try:
        return (datetime.strptime(str(b)[:10], "%Y-%m-%d")
                - datetime.strptime(str(a)[:10], "%Y-%m-%d")).days
    except ValueError:
        return None


def summarize(pl: Plan, would: Dict[str, Dict[str, Any]], *,
              country: str) -> List[Dict[str, Any]]:
    """The gains file's `loss_overrides`: per override its status
    (applied | unmatched | ambiguous) and the sale(s) its line names
    with the denial the rule would have made (`would` {row id: record}
    from the engine's last pass)."""
    out = []
    for i, ov in enumerate(pl.overrides):
        sales = []
        for s in pl.groups.get(i, []):
            recs = [(t, would[t.id]) for t in s if t.id in would]
            if not any(r["disallowed"] > 0.005 for _t, r in recs):
                continue
            first = s[0]
            reps: Dict[str, Dict[str, Any]] = {}
            for _t, r in recs:
                for rp in r["replacements"]:
                    cur = reps.setdefault(rp["id"], dict(rp, units=0.0,
                                                         amount=0.0))
                    cur["units"] += rp["units"]
                    cur["amount"] += rp["amount"]
            for rp in reps.values():
                rp["days_settle"] = _days(first.date_settle or first.date,
                                          rp["date_settle"])
                rp["days_trade"] = _days(first.date, rp["date"])
                rp["units"] = round(rp["units"], 9)
                rp["amount"] = round(rp["amount"], 6)
            dis = sum(r["disallowed"] for _t, r in recs)
            perm = sum(r["permanent"] for _t, r in recs)
            sales.append({
                "ids": [t.id for t in s],
                "account": first.account, "symbol": first.symbol,
                "date": first.date,
                "date_settle": first.date_settle or first.date,
                "qty": round(abs(sum(float(t.quantity) for t in s)), 9),
                "loss_units": round(sum(r["qty"] for _t, r in recs), 9),
                "loss": round(sum(r["loss"] for _t, r in recs), 6),
                "would_disallow": round(dis, 6),
                "would_permanent": round(perm, 6),
                "would_defer": round(dis - perm, 6),
                "replacements": sorted(reps.values(),
                                       key=lambda r: (r["date"], r["id"])),
            })
        status = (STATUS_APPLIED if len(sales) == 1 else
                  STATUS_UNMATCHED if not sales else STATUS_AMBIGUOUS)
        out.append({
            "where": ov.get("where") or "", "line": ov.get("line") or "",
            "account": ov["account"], "date": ov["date"],
            "symbol": ov["symbol"], "qty": ov.get("qty"),
            "reason": ov.get("reason") or "", "country": country,
            "status": status, "sales": sales,
            "day_trades": [{"symbol": t.symbol, "date": t.date,
                            "date_settle": t.date_settle or t.date,
                            "qty": float(t.quantity)}
                           for t in pl.day_rows.get(i, [])][:12],
        })
    return out


def entry_note(item: Dict[str, Any], sale: Dict[str, Any]) -> Dict[str, Any]:
    """The `loss_override` stamped on each gain row of the sale."""
    return {"where": item["where"], "reason": item["reason"],
            "would_disallow": sale["would_disallow"],
            "would_permanent": sale["would_permanent"]}


def stamp_entries(entries: Iterable[Dict[str, Any]],
                  items: Sequence[Dict[str, Any]]) -> None:
    """Mark the gain rows of every applied override's sale."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for it in items:
        if it["status"] != STATUS_APPLIED:
            continue
        for s in it["sales"]:
            for rid in s["ids"]:
                by_id[rid] = entry_note(it, s)
    if not by_id:
        return
    for e in entries:
        n = by_id.get(e.get("id"))
        if n is not None and "qty" in e and "gain" in e:
            e["loss_override"] = n


# ----------------------------------------------------- run-side views

def gather(files: Dict[str, Path]) -> List[Dict[str, Any]]:
    """The overrides recorded in the final gains files (one per line:
    its own account's file)."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for acct, p in files.items():
        try:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for it in (doc.get("loss_overrides") or []
                   if isinstance(doc, dict) else []):
            if not isinstance(it, dict) or it.get("account") != acct:
                continue
            k = it.get("where") or json.dumps(it, sort_keys=True)
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
    out.sort(key=lambda it: (str(it.get("date")), str(it.get("account")),
                             str(it.get("symbol")), str(it.get("where"))))
    return out


def _money(x) -> str:
    return f"{float(x or 0):,.2f}"


def _units(x) -> str:
    return f"{float(x or 0):,.8g}"


def problems(items: Sequence[Dict[str, Any]],
             expected: Sequence[Dict[str, Any]] = ()) -> List[str]:
    """One line per override that names no denied loss, or several
    (and per line of the project the final books never saw)."""
    out = []
    recorded = {it.get("where") for it in items}
    for ov in expected:
        if ov.get("where") not in recorded:
            out.append(f"{ov.get('where')}: {ov.get('line')!r} — no final "
                       f"gains file of account {ov.get('account')} "
                       f"recorded it (re-run `taxjson run`)")
    for it in items:
        st = it.get("status")
        if st == STATUS_APPLIED:
            continue
        what = (f"{it.get('symbol')} sold {it.get('date')}"
                + (f" ({_units(it.get('qty'))} units)" if it.get("qty")
                   else ""))
        if st == STATUS_AMBIGUOUS:
            sales = "; ".join(
                f"{s['date']} {_units(s['qty'])} units, "
                f"{_money(s['would_disallow'])} denied"
                for s in it.get("sales") or [])
            out.append(f"{it.get('where')}: {it.get('line')!r} — names "
                       f"{len(it.get('sales') or [])} denied sales of "
                       f"{what} ({sales}): add the units sold to the line "
                       f"to name one")
            continue
        days = it.get("day_trades") or []
        seen_txt = ("; that day's trades in account "
                    f"{it.get('account')}: "
                    + ", ".join(f"{d['symbol']} {d['qty']:+,.8g}"
                                for d in days)
                    if days else
                    f"; account {it.get('account')} has no trade traded or "
                    f"settled that day")
        out.append(f"{it.get('where')}: {it.get('line')!r} — no denied "
                   f"loss matches {what} in account {it.get('account')}"
                   + seen_txt + " (`taxjson wash-sales` lists the "
                   "denied ones; the symbol as the books spell it)")
    return out


def _why(sale: Dict[str, Any], country: str) -> str:
    us = country == "usa"
    parts = []
    for rp in sale.get("replacements") or []:
        holder = rp.get("holder")
        kind = ("" if holder == "taxable" else
                (" (an IRA: disallowed for good)" if us else
                 " (registered: denied for good)") if holder == "sheltered"
                else " (affiliated)")
        ds, dt = rp.get("days_settle"), rp.get("days_trade")
        if us:
            when = (f"{abs(dt)} day(s) {'after' if (dt or 0) >= 0 else 'before'}"
                    f" the sale on trade dates" if dt is not None else "")
        else:
            when = (f"{abs(ds)} day(s) {'after' if (ds or 0) >= 0 else 'before'}"
                    f" the sale on settle dates" if ds is not None else "")
            if dt is not None and ds is not None and dt != ds:
                when += f", {abs(dt)} on trade dates"
        parts.append(f"{rp.get('symbol')} bought {rp.get('date')} in "
                     f"{rp.get('account')}{kind}, {when}")
    return "; ".join(parts) or "a replacement in the window"


def describe(it: Dict[str, Any]) -> List[str]:
    """One line per sale of an applied override: the sale, the denial
    the rule would make and why, the user's reason and the line."""
    country = it.get("country") or "canada"
    us = country == "usa"
    out = []
    for s in it.get("sales") or []:
        perm, defer = s["would_permanent"], s["would_defer"]
        fate = ("for good" if defer < 0.005 else
                ("deferred to the replacement's "
                 + ("basis" if us else "ACB")) if perm < 0.005 else
                f"{_money(perm)} of it for good, {_money(defer)} deferred")
        out.append(f"{s['account']} {s['date']} {s['symbol']} "
                   f"{_units(s['qty'])} units: loss {_money(s['loss'])} "
                   f"claimed; the {'wash-sale' if us else 'superficial-loss'}"
                   f" rule would {'disallow' if us else 'deny'} "
                   f"{_money(s['would_disallow'])} {fate} — "
                   f"{_why(s, country)}. Reason: \"{it.get('reason')}\" "
                   f"({it.get('where')})")
    return out


def warning_message(items: Sequence[Dict[str, Any]],
                    country: str) -> Tuple[str, List[str]]:
    """The run's one Warning: (headline, detail lines)."""
    us = country == "usa"
    applied = [it for it in items if it.get("status") == STATUS_APPLIED]
    n = len(applied)
    head = (f"{n} filing position(s) taken against the "
            f"{'wash-sale rule (§1091)' if us else 'superficial-loss rule (s.54)'}"
            f" ({KEYWORD} lines): the loss is allowed although the rule "
            f"would {'disallow' if us else 'deny'} it")
    details = []
    for it in applied:
        details += [f"- {ln}" for ln in describe(it)]
    details.append(
        "This is your position, not the rule's test: the numbers include "
        "it and no " + ("basis" if us else "ACB") + " is raised for it. "
        "`taxjson sum` lists it under FILING POSITIONS; delete the line to "
        "apply the rule.")
    return head, details


def in_year(it: Dict[str, Any], year, basis: str) -> bool:
    """Whether the override's sale falls in `year` on the date basis."""
    y = str(year or "")
    if not y:
        return True
    for s in it.get("sales") or []:
        d = s.get("date_settle") if basis == "settle" else s.get("date")
        if str(d or s.get("date") or "").startswith(y):
            return True
    return not it.get("sales") and str(it.get("date") or "").startswith(y)


def positions(items: Sequence[Dict[str, Any]], year=None,
              basis: str = "settle") -> List[Dict[str, Any]]:
    """`sum --json` filing_positions: one record per applied override,
    flat, marked whether its sale is in the year."""
    out = []
    for it in items:
        if it.get("status") != STATUS_APPLIED:
            continue
        for s in it.get("sales") or []:
            out.append({
                "account": s["account"], "date": s["date"],
                "date_settle": s["date_settle"], "symbol": s["symbol"],
                "qty": s["qty"], "loss": round(s["loss"], 2),
                "would_disallow": round(s["would_disallow"], 2),
                "would_permanent": round(s["would_permanent"], 2),
                "would_defer": round(s["would_defer"], 2),
                "replacements": s["replacements"],
                "why": _why(s, it.get("country") or "canada"),
                "reason": it.get("reason"), "where": it.get("where"),
                "line": it.get("line"),
                "in_year": in_year(it, year, basis),
            })
    return out

"""What `taxjson sanity` checks beyond the quantity table: the books'
COST against a positions report's, the books' positions on a past date,
and income paid on a share count the books do not hold.

Costs (`compare_costs`): a broker's book value is not the books' ACB and
is not meant to be. The difference is reported with a REASON, never as
a failure (sanity's exit code stays the quantity check's):

  Canada (the books' ACB is the s.47 average over all your taxable
  accounts, CA-ACB-01, with denied superficial losses added, s.53(1)(f)):
    superficial-loss  the books' ACB carries denied superficial losses;
                      a broker's book value does not
    pooled            the same shares in another of your taxable
                      accounts: the books average them (s.47), the
                      broker shows this account's own cost
    return-of-capital the books lowered the ACB for a return of capital
                      the broker may not have applied (or the reverse)
    broker-fx         the broker's book value in Canadian dollars for a
                      foreign listing, converted at its own rates; the
                      books convert each purchase at the Bank of Canada
                      rate of its day
    lot-basis         the broker's cost is its LOT basis (sales matched
                      to lots); Canadian ACB is the average cost
  United States (the books' basis is FIFO lots per account with
  §1091(d) additions):
    wash-sale         wash-sale basis additions — a broker shows them
                      only for washes inside the same account
    lot-method        the broker may match sales to lots by another
                      method than FIFO (specific identification)
    return-of-capital as above
  Both: unexplained — none of those closes the gap: a missing or
  mis-costed purchase, an opening line's cost, or the broker's error.

A cost in the project's base currency is compared with the filing books
(work/<acct>_gains_wash.json, else _gains.json); a cost in the
position's own foreign currency with the account's native-currency
books (work/<acct>_raw_gains.json: no superficial-loss additions, no
pooling — said in the row).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Rows that move a position (quantity); SPLIT scales or renames it.
_QTY_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'OPENING_BALANCE')
# Rows that change a position or its cost after a date.
_COST_ACTIONS = _QTY_ACTIONS + ('SPLIT', 'ADJUST', 'DISALLOW')

# The share count a dividend row's description states ("... ON 500
# SHS", "ON 40 SHRS") — the parsers' own pattern (brokerages.base).
from taxjson.lib.brokerages.base import _DIV_QTY_ON_SHS_RE  # noqa: E402


def load_base_rows(cache: Path, account: str) -> List[Dict[str, Any]]:
    """The account's merged books (work/<acct>_base.json) rows, [] when
    missing or unreadable."""
    p = Path(cache) / f"{account}_base.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("transactions") if isinstance(doc, dict) else doc
    return [r for r in (rows or []) if isinstance(r, dict)]


def book_rows(cache: Path, account: str) -> List[Dict[str, Any]]:
    """The rows the account's books hold positions from: its merged rows
    (load_base_rows) plus the missing-history openings its gains run
    booked (the .tt OPENING cost=unknown
    lines: units bought before the data, which no row of the merged
    books carries — lib/missing_history.openings_from_log). Every view
    of the books' positions on a date reads these, so it holds the
    same units the books do."""
    from taxjson.lib.missing_history import openings_from_log
    rows = load_base_rows(cache, account)
    for name in (f"{account}_gains.json", f"{account}_gains_wash.json"):
        p = Path(cache) / name
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return rows + openings_from_log(doc, account)
    return rows


def load_inventory(path: Path) -> Dict[str, Dict[str, Any]]:
    """{symbol: inventory entry} of a gains file (summed per symbol)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for h in (doc.get("inventory") or []) if isinstance(doc, dict) else []:
        sym = str(h.get("symbol") or "")
        if not sym:
            continue
        e = out.setdefault(sym, {"qty": 0.0, "total_cost": 0.0,
                                 "deferred_wash": 0.0,
                                 "currency": h.get("currency") or ""})
        e["qty"] += float(h.get("qty") or 0.0)
        e["total_cost"] += float(h.get("total_cost") or 0.0)
        e["deferred_wash"] += float(h.get("deferred_wash") or 0.0)
    return out


def last_day(rows: Iterable[Dict[str, Any]]) -> str:
    return max((str(r.get("date") or "") for r in rows
                if r.get("action") in _COST_ACTIONS), default="")


def _ordered(rows):
    return sorted(rows, key=lambda r: (str(r.get("date") or ""),
                                       str(r.get("time") or "")))


def positions_on(rows: Iterable[Dict[str, Any]], day: str, *,
                 settled: bool = False,
                 before: bool = False) -> Dict[str, float]:
    """{symbol: quantity} after every row traded on or before `day`
    (`before`: strictly before it; `settled`: by settlement date — the
    holder of record). Splits scale the symbol's shares; a rename moves
    them to the new symbol."""
    pos: Dict[str, float] = {}
    for r in _ordered(rows):
        d = str((r.get("date_settle") if settled else None)
                or r.get("date") or "")
        if (d >= day) if before else (d > day):
            continue
        a = r.get("action")
        sym = str(r.get("symbol") or "")
        if a in _QTY_ACTIONS:
            pos[sym] = pos.get(sym, 0.0) + float(r.get("quantity") or 0.0)
        elif a == "SPLIT":
            new = str(r.get("symbol_new") or "").strip() or sym
            q = pos.pop(sym, 0.0) * float(r.get("quantity") or 0.0)
            pos[new] = pos.get(new, 0.0) + q
    return {s: q for s, q in pos.items() if abs(q) > 1e-9}


def touched_after(rows: Iterable[Dict[str, Any]], day: str) -> Set[str]:
    """Symbols with a position or cost row dated after `day`."""
    out: Set[str] = set()
    for r in rows:
        if r.get("action") in _COST_ACTIONS \
                and str(r.get("date") or "") > day:
            out.add(str(r.get("symbol") or ""))
            if r.get("symbol_new"):
                out.add(str(r["symbol_new"]))
    return out


def roc_symbols(rows: Iterable[Dict[str, Any]]) -> Set[str]:
    """Symbols the books lowered the cost of (an ADJUST that is a
    return of capital — negative amount or type roc)."""
    out: Set[str] = set()
    for r in rows:
        if r.get("action") != "ADJUST":
            continue
        if (str(r.get("type") or "").lower() == "roc"
                or float(r.get("net_amount") or 0.0) < 0):
            out.add(str(r.get("symbol") or ""))
    return out


# ------------------------------------------------------------------ income

# The window before a pay date in which the shares a dividend states must
# have been held when the row gives no record or ex-date: the record
# date of a monthly or quarterly payer sits in it (a quarterly payer's
# pay date can trail its record date by several weeks).
INCOME_WINDOW_DAYS = 45


def _record_date_of(r: Dict[str, Any]) -> str:
    """The row's record date: its `record_date` (a parser's, a .tt
    record=), else the one its description prints ("REC 09/26/25")."""
    rec = str(r.get("record_date") or "")
    if rec:
        return rec
    from taxjson.lib.brokerages.base import income_facts_from_description
    try:
        return str(income_facts_from_description(
            str(r.get("description") or "")).get("record_date") or "")
    except Exception:                                   # noqa: BLE001
        return ""


def held_as(rows: Iterable[Dict[str, Any]], sym: str, day: str, *,
            settled: bool = False, before: bool = False) -> float:
    """The units the books held on `day` of the security `sym` names
    now: its own position plus the units a later ticker change (a
    rename SPLIT row into `sym` — a broker's corporate action, a dated
    .tt RENAME, a ticker.map rename — dated after `day`) carried in from
    the old symbol, at the change's ratio. A dividend paid under the new
    ticker on a record date before the change was earned on the old
    one's shares."""
    rows = list(rows)
    pos = positions_on(rows, day, settled=settled, before=before)
    renames = []
    for r in rows:
        if r.get("action") != "SPLIT":
            continue
        new = str(r.get("symbol_new") or "").strip()
        old = str(r.get("symbol") or "")
        if not new or new == old:
            continue
        d = str((r.get("date_settle") if settled else None)
                or r.get("date") or "")
        later = (d >= day) if before else (d > day)
        if later:
            try:
                ratio = float(r.get("quantity") or 0.0) or 1.0
            except (TypeError, ValueError):
                ratio = 1.0
            renames.append((old, new, ratio))
    factor = {sym: 1.0}
    for _ in range(len(renames) + 1):
        grew = False
        for old, new, ratio in renames:
            if new in factor and old not in factor:
                factor[old] = factor[new] * ratio
                grew = True
        if not grew:
            break
    return sum(pos.get(s, 0.0) * f for s, f in factor.items())


# North American markets moved from T+2 to T+1 settlement in May 2024
# (Canada on the 27th, the US on the 28th): the last trade entitled to a
# dividend settles by its record date.
_T1_SINCE = {"US": "2024-05-28", "CA": "2024-05-27"}


def last_entitled_trade_day(record: str, currency: str) -> Optional[str]:
    """The last trading day whose trade settles by `record` on the
    market's own calendar (the currency's: USD the US, CAD Canada) —
    the eve of the ex-date. None for a bad date."""
    from taxjson.lib.market_calendar import market_for, sub_settlement_days
    mkt = market_for(currency) or "US"
    n = 1 if record >= _T1_SINCE.get(mkt, "2024-05-28") else 2
    try:
        return sub_settlement_days(record, n, currency or "USD").isoformat()
    except ValueError:
        return None


def _max_held(rows, sym: str, pay: str, days: int) -> Optional[float]:
    """The most shares of `sym` the rows held at the end of any day of
    the `days` before `pay` (pay day included); None for a bad date."""
    from datetime import datetime, timedelta
    try:
        d0 = datetime.strptime(pay, "%Y-%m-%d")
    except ValueError:
        return None
    best = 0.0
    for k in range(days + 1):
        day = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
        best = max(best, held_as(rows, sym, day))
    return best


def income_share_mismatches(rows: List[Dict[str, Any]], account: str,
                            tol: float = 1e-4) -> List[Dict[str, Any]]:
    """Dividend rows whose description STATES the share count ("ON 500
    SHS") for more shares than the books held when the payment was
    earned. Entitlement is fixed at the record date, not the pay date:
    a sale after it (the dividend paid days or weeks later, the position
    0 by then) is no finding. With the record date (the row's, a .tt
    record=, or the "REC mm/dd/yy" its description prints) the books'
    SETTLED position that day is compared (T+1 / T+2 settlement: the
    holder of record), or the shares traded by the eve of the ex-date
    on the market's calendar (last_entitled_trade_day), whichever is
    more; with only the ex-date, the position at the end of
    the day before it; with neither, the most the books held at the end
    of any day of the INCOME_WINDOW_DAYS before the pay date. Reported:
    a payment on more shares than that (on a symbol never held: 0) — a
    payment on fewer shares (another broker account's part, a partial
    entitlement) is not. Rows of one broker account are compared with
    that account's rows (and the hand-written ones): one taxjson account
    may hold two brokers' accounts (a count the whole account held is
    accepted too)."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("action") not in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            continue
        m = _DIV_QTY_ON_SHS_RE.search(str(r.get("description") or ""))
        if not m:
            continue
        try:
            stated = float(r.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        if stated <= 0:
            continue
        sym = str(r.get("symbol") or "")
        src = str(r.get("source_account") or "")
        mine = [t for t in rows
                if not src or not t.get("source_account")
                or t.get("source_account") == src]
        rec = _record_date_of(r)
        # (the listing's market decides the calendar: a US share paid
        # in CAD by a Canadian broker settles on US days)
        cur = ("USD" if sym.endswith(".US") else
               "CAD" if sym.rsplit(".", 1)[-1] in ("TO", "V", "CN", "NE")
               and "." in sym else str(r.get("currency") or "").upper())
        exd = str(r.get("ex_date") or "")
        pay = str(r.get("date") or "")

        def _held(book):
            # (through a ticker change after the date: the shares were
            # held under the old symbol then — held_as)
            if rec:
                # The settled position on the record date, or the shares
                # traded by the eve of the ex-date on the market's own
                # calendar (a broker's settle date can follow another
                # market's holiday), whichever is more.
                h = held_as(book, sym, rec, settled=True)
                eve = last_entitled_trade_day(rec, cur)
                if eve:
                    h = max(h, held_as(book, sym, eve))
                return h
            if exd:
                return held_as(book, sym, exd, before=True)
            return _max_held(book, sym, pay, INCOME_WINDOW_DAYS)
        held = _held(mine)
        if held is None:
            continue
        if rec:
            when, basis = rec, "record date"
        elif exd:
            when, basis = exd, "ex-date"
        else:
            when = pay
            basis = f"{INCOME_WINDOW_DAYS} days to the pay date"
        if held + tol >= stated:
            continue
        if mine is not rows and len(mine) != len(rows):
            # The whole account's position (a holding moved between the
            # account's brokers, rows stamped with another broker
            # account): enough there is no finding.
            alt = _held(rows)
            if alt is not None and alt + tol >= stated:
                continue
        out.append({"account": account, "symbol": sym, "date": pay,
                    "on": when, "basis": basis, "stated_shares": stated,
                    "books_shares": held,
                    "amount": float(r.get("net_amount") or 0.0),
                    "currency": r.get("currency") or ""})
    return out


# ------------------------------------------------------------------- costs

def _within(a: float, b: float, tol_abs: float, tol_rel: float) -> bool:
    return abs(a - b) <= max(tol_abs, tol_rel * max(abs(a), abs(b)))


def compare_cost(*, symbol: str, broker_cost: float, cost_currency: str,
                 cost_kind: str, position_currency: str, base: str,
                 country: str, filing: Optional[Dict[str, Any]],
                 own: Optional[float], native: Optional[Dict[str, Any]],
                 pooled: bool, roc: bool, tol_abs: float,
                 tol_rel: float, short: bool = False) -> Dict[str, Any]:
    """One symbol's cost row. `filing`: the summed filing-books
    inventory entry (base currency) of the group's accounts; `own`: the
    accounts' own pre-pooling cost (base currency); `native`: the
    native-currency books entry. Returns {status: match | differs |
    n/a, books_cost, basis, diff, reasons, explained, note}."""
    usa = country == "usa"
    cur = (cost_currency or "").upper()
    base = (base or "").upper()
    pos_cur = (position_currency or "").upper()
    row: Dict[str, Any] = {"symbol": symbol, "broker_cost": broker_cost,
                           "cost_currency": cur, "cost_kind": cost_kind,
                           "books_cost": None, "basis": "", "diff": None,
                           "reasons": [], "explained": False, "note": ""}
    if cur == base or not cur:
        if filing is None:
            row.update(status="n/a", note="no books cost for it")
            return row
        books = float(filing["total_cost"])
        row.update(books_cost=books, basis="filing books "
                   + ("(FIFO lots, wash-adjusted)" if usa else
                      "(s.47 ACB)"))
    else:
        if native is None or (native.get("currency") or "").upper() != cur:
            row.update(status="n/a",
                       note=f"no {cur} books for it (the books are in "
                            f"{base})")
            return row
        books = float(native["total_cost"])
        row.update(books_cost=books,
                   basis=f"native {cur} books (this account's own cost: "
                         f"no {'wash-sale additions' if usa else 'superficial-loss additions, no s.47 pooling'})")
    if short and books * broker_cost < 0:
        # A short position's cost is its opening proceeds: the books
        # carry it negative (the inventory convention), some reports
        # positive — compared as magnitudes.
        books, broker_cost = abs(books), abs(broker_cost)
        row.update(books_cost=books, broker_cost=broker_cost)
    diff = books - broker_cost
    row["diff"] = diff
    if _within(books, broker_cost, tol_abs, tol_rel):
        row["status"] = "match"
        return row
    row["status"] = "differs"
    reasons: List[str] = []
    explained = False
    in_filing = row["basis"].startswith("filing")
    deferred = float((filing or {}).get("deferred_wash") or 0.0)
    if in_filing and abs(deferred) > 0.005:
        reasons.append("wash-sale" if usa else "superficial-loss")
        if _within(books - deferred, broker_cost, tol_abs, tol_rel):
            explained = True
    if in_filing and not usa and pooled:
        reasons.append("pooled")
        if own is not None and _within(own, broker_cost, tol_abs,
                                       tol_rel):
            explained = True
    if roc:
        reasons.append("return-of-capital")
    if (in_filing and not usa and cur == base and pos_cur
            and pos_cur != base):
        reasons.append("broker-fx")
    if cost_kind == "lot basis" and not usa:
        reasons.append("lot-basis")
    if usa and cost_kind == "lot basis":
        reasons.append("lot-method")
    if not reasons:
        reasons.append("unexplained")
    row["reasons"] = reasons
    row["explained"] = explained
    return row


REASON_TEXT = {
    "superficial-loss": "the books' ACB carries denied superficial "
                        "losses (s.53(1)(f)); a broker's book value "
                        "does not",
    "pooled": "the books average the same shares in your other taxable "
              "accounts (s.47); the broker shows this account's own "
              "cost",
    "return-of-capital": "the books lowered the cost for a return of "
                         "capital the broker may not have applied",
    "broker-fx": "the broker converted a foreign listing's cost at its "
                 "own rates; the books use each purchase day's Bank of "
                 "Canada rate",
    "lot-basis": "the broker's cost is its lot basis; Canadian ACB is "
                 "the average cost of all identical shares",
    "wash-sale": "the books' basis carries wash-sale additions "
                 "(§1091(d)); a broker shows only washes inside the same "
                 "account",
    "lot-method": "the broker may match sales to lots by another method "
                  "than FIFO",
    "unexplained": "none of the known differences closes it: a missing "
                   "or mis-costed purchase, an opening line's cost, or "
                   "the broker's own error",
}

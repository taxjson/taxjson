"""The cash ledger's own lines and events, below the ledger: a `.tt`
cash line (FXCONV, CASHMOVE, CASHOPEN, CASHBAL, CASHBOOK) read and
checked (parse_line, CashLineError, FORMS), an opening's check, and the
event dicts every broker's parse builds its cash events with (conv,
move, balance, flow). lib/cash_events collects them per book. Out of
lib/cash_events, which the cash readers (until PLAN step 16) and the .tt
reader imported for these while it imports them (step 8 of the
architecture plan: lib's import cycle)."""

from __future__ import annotations

import math
import re
from datetime import date as _date, datetime
from typing import Any, Dict, List, Optional, Tuple


KEYWORDS = ("FXCONV", "CASHMOVE", "CASHOPEN", "CASHBAL", "CASHBOOK")
FORMS = {
    "FXCONV": "FXCONV <date> <from currency> <amount> <to currency> "
              "<amount> [value=<base amount>] [at=<book>]",
    "CASHMOVE": "CASHMOVE <date> <currency> <signed amount> "
                "own|kept|spot|cost=<base amount>|proceeds=<base amount> "
                "[at=<book>]",
    "CASHOPEN": "CASHOPEN <date> <currency> <signed units> <base cost> "
                "[at=<book>]",
    "CASHBAL": "CASHBAL <date> <currency> <balance> [at=<book>]",
    "CASHBOOK": "CASHBOOK <book> [complete]",
}


COMPLETE = "complete"
MOVE_HOW = ("own", "kept", "spot")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CUR_RE = re.compile(r"^[A-Z]{3,5}$")
_KEY_RE = re.compile(r"^([a-z]+)=(\S+)$")
_BOOK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?::[0-9a-f]{1,10})?$")


class CashLineError(ValueError):
    """A cash .tt line that cannot be read (named, with its form)."""


def _num(tok: str, what: str, where: str, line: str, kw: str) -> float:
    from taxjson.lib.brokerages.base import (BrokerageParseError,
                                             parse_strict_number)
    try:
        v = parse_strict_number(tok, field=what)
    except (BrokerageParseError, ValueError):
        v = None
    if v is None or not math.isfinite(v):
        raise CashLineError(f"{where}{kw} {what} {tok!r} is not a number "
                            f"(expected `{FORMS[kw]}`): {line!r}")
    return float(v)


def _check_date(tok: str, where: str, line: str, kw: str) -> str:
    ok = bool(_DATE_RE.match(tok or ""))
    if ok:
        try:
            datetime.strptime(tok, "%Y-%m-%d")
        except ValueError:
            ok = False
    if not ok:
        raise CashLineError(f"{where}{kw} date {tok!r} is not a valid "
                            f"YYYY-MM-DD date — a .tt line is date first "
                            f"(expected `{FORMS[kw]}`): {line!r}")
    return tok


def _check_cur(tok: str, where: str, line: str, kw: str) -> str:
    cur = (tok or "").upper()
    if not _CUR_RE.match(cur):
        raise CashLineError(f"{where}{kw} currency {tok!r} is not a "
                            f"currency code (expected `{FORMS[kw]}`): "
                            f"{line!r}")
    return cur


def parse_line(line: str, source: str = "") -> Optional[Dict[str, Any]]:
    """One .tt line -> a cash event (kind, date, ..., `at`, `line`), or
    None when it is not a cash line. Raises CashLineError naming the
    form on a malformed one."""
    where = f"{source}: " if source else ""
    text = line.split("#", 1)[0]
    parts = text.split()
    if not parts or parts[0] not in KEYWORDS:
        return None
    kw = parts[0]
    shown = line.strip()
    if kw == "CASHBOOK":
        # Which account's cash this file's rows move (a folder holding
        # several broker accounts): a standing fact of the file, no date.
        # `complete`: every conversion, deposit and withdrawal of the
        # book is a .tt line (a broker whose parse builds no cash events:
        # the parser registry's builds_cash).
        if (len(parts) not in (2, 3) or not _BOOK_RE.match(parts[1])
                or (len(parts) == 3 and parts[2] != COMPLETE)):
            raise CashLineError(f"{where}malformed CASHBOOK line — "
                                f"expected `{FORMS[kw]}` (a book as "
                                f"`tjs fx-cash --ledger v2` prints "
                                f"it: ib, rbc:3f2a, webull ...): {shown!r}")
        return {"kind": kw, "book_name": parts[1], "line": shown,
                "complete": len(parts) == 3,
                "where": source, "origin": "tt", "date": ""}
    keys: Dict[str, str] = {}
    toks: List[str] = []
    for t in parts[1:]:
        m = _KEY_RE.match(t)
        if m:
            if m.group(1) in keys:
                raise CashLineError(f"{where}{kw}: {m.group(1)}= given "
                                    f"twice: {shown!r}")
            keys[m.group(1)] = m.group(2)
        else:
            toks.append(t)
    if not toks:
        raise CashLineError(f"{where}malformed {kw} line — expected "
                            f"`{FORMS[kw]}`: {shown!r}")
    if not _DATE_RE.match(toks[0]) and len(toks) > 1 \
            and _DATE_RE.match(toks[1]):
        raise CashLineError(f"{where}malformed {kw} line — a .tt line is "
                            f"date first (expected `{FORMS[kw]}`): "
                            f"{shown!r}")
    d = _check_date(toks[0], where, shown, kw)
    if d > _date.today().isoformat():
        raise CashLineError(f"{where}{kw} line is dated {d}, in the "
                            f"future (check the year): {shown!r}")
    at = keys.pop("at", None)
    if at is not None and not _BOOK_RE.match(at):
        raise CashLineError(f"{where}{kw} at={at!r} is not a book name "
                            f"(a word, or <broker>:<id> as `tjs "
                            f"fx-cash --ledger v2` prints it): {shown!r}")
    ev: Dict[str, Any] = {"kind": kw, "date": d, "settle": d, "at": at,
                          "line": shown, "where": source, "origin": "tt"}
    rest = toks[1:]

    def _bad(why: str) -> CashLineError:
        return CashLineError(f"{where}malformed {kw} line — {why} "
                             f"(expected `{FORMS[kw]}`): {shown!r}")

    def _no_keys(allowed: Tuple[str, ...]) -> None:
        bad = [k for k in keys if k not in allowed]
        if bad:
            raise _bad(f"unknown key {bad[0]}=")

    if kw == "FXCONV":
        _no_keys(("value",))
        if len(rest) != 4:
            raise _bad("it needs two currencies and two amounts")
        a = _check_cur(rest[0], where, shown, kw)
        a_amt = _num(rest[1], "amount", where, shown, kw)
        b = _check_cur(rest[2], where, shown, kw)
        b_amt = _num(rest[3], "amount", where, shown, kw)
        if a == b:
            raise _bad("a conversion is between two currencies")
        if a_amt <= 0 or b_amt <= 0:
            raise _bad("both amounts are positive (what left, what "
                       "arrived)")
        ev.update(from_ccy=a, from_amt=a_amt, to_ccy=b, to_amt=b_amt,
                  value=(_num(keys["value"], "value", where, shown, kw)
                         if "value" in keys else None))
        if ev["value"] is not None and ev["value"] <= 0:
            raise _bad("value= is the positive base-currency amount")
        return ev
    if kw == "CASHMOVE":
        _no_keys(("cost", "proceeds"))
        how = [t for t in rest[2:]]
        if len(rest) < 2:
            raise _bad("it needs a currency and a signed amount")
        cur = _check_cur(rest[0], where, shown, kw)
        amt = _num(rest[1], "amount", where, shown, kw)
        if abs(amt) < 1e-9:
            raise _bad("the amount is 0")
        decl: Optional[Dict[str, Any]] = None
        if keys:
            if how or len(keys) != 1:
                raise _bad("say once how the cash moved")
            k, v = next(iter(keys.items()))
            val = _num(v, k, where, shown, kw)
            if val < 0:
                raise _bad(f"{k}= is a positive base-currency amount")
            if k == "cost" and amt < 0:
                raise _bad("cost= declares what cash coming IN cost; "
                           "money going out takes proceeds=, kept, "
                           "spot or own")
            if k == "proceeds" and amt > 0:
                raise _bad("proceeds= declares what cash going OUT "
                           "fetched; money coming in takes cost=, spot "
                           "or own")
            decl = {"how": k, "value": val}
        elif len(how) == 1 and how[0] in MOVE_HOW:
            if how[0] == "kept" and amt > 0:
                raise _bad("kept is for cash going OUT that you still "
                           "hold elsewhere; money coming in takes "
                           "cost=, spot or own")
            decl = {"how": how[0], "value": None}
        else:
            raise _bad("say how the cash moved: own, kept, spot, "
                       "cost=<base amount> or proceeds=<base amount>")
        ev.update(currency=cur, amount=amt, decl=decl)
        return ev
    if kw == "CASHOPEN":
        _no_keys(())
        if len(rest) != 3:
            raise _bad("it needs a currency, the units and their cost")
        cur = _check_cur(rest[0], where, shown, kw)
        units = _num(rest[1], "units", where, shown, kw)
        cost = _num(rest[2], "cost", where, shown, kw)
        why = opening_problem(units, cost)
        if why:
            raise _bad(why)
        ev.update(currency=cur, units=units, cost=cost)
        return ev
    # CASHBAL
    _no_keys(())
    if len(rest) != 2:
        raise _bad("it needs a currency and the balance")
    cur = _check_cur(rest[0], where, shown, kw)
    ev.update(currency=cur,
              balance=_num(rest[1], "balance", where, shown, kw),
              statement="tt")
    return ev


def opening_problem(units: float, cost: float) -> Optional[str]:
    """Why an opening balance of `units` (signed: negative is a debt)
    at a base-currency `cost` cannot open a year, or None. One rule for
    a CASHOPEN line (parse_line) and for a book carried from the prior
    year's close (lib/fx_cash_v2._carry_openings), which a damaged or
    hand-edited record could otherwise open with a cost CASHOPEN
    refuses (2026-10-25 audit year-end#2)."""
    if cost < 0:
        return ("the cost is a positive base-currency amount (for a "
                "debt, what the borrowed units were worth when borrowed)")
    if abs(units) < 1e-9 and abs(cost) > 0.005:
        return "0 units cannot have a cost"
    return None


def conv(date: str, from_ccy: str, from_amt: float, to_ccy: str,
         to_amt: float, *, value: Optional[float] = None,
         settle: Optional[str] = None, where: str = "",
         account: str = "", desc: str = "") -> Dict[str, Any]:
    return {"kind": "FXCONV", "date": date, "settle": settle or date,
            "from_ccy": from_ccy, "from_amt": abs(from_amt),
            "to_ccy": to_ccy, "to_amt": abs(to_amt), "value": value,
            "where": where, "account": account, "desc": desc,
            "origin": "broker"}


def move(date: str, ccy: str, amount: float, *,
         settle: Optional[str] = None, where: str = "", account: str = "",
         desc: str = "", internal: str = "") -> Dict[str, Any]:
    """A broker's cash move, undeclared (`decl` None) until a .tt
    CASHMOVE line says what it was. `internal`: a pairing class
    ("transfer", "advance") for moves inside one statement that cancel."""
    return {"kind": "CASHMOVE", "date": date, "settle": settle or date,
            "currency": ccy, "amount": amount, "decl": None,
            "where": where, "account": account, "desc": desc,
            "internal": internal, "origin": "broker"}


def balance(date: str, ccy: str, amount: float, *, where: str = "",
            account: str = "", statement: str = "") -> Dict[str, Any]:
    """A statement balance at the END of `date` (trade-date basis)."""
    return {"kind": "CASHBAL", "date": date, "settle": date,
            "currency": ccy, "balance": amount, "where": where,
            "account": account, "statement": statement or where,
            "origin": "broker"}


def flow(date: str, ccy: str, amount: float, *,
         settle: Optional[str] = None, where: str = "", account: str = "",
         desc: str = "") -> Dict[str, Any]:
    """A fee charged by a cash event (a conversion's commission, a
    withdrawal fee) in a currency: signed, - paid."""
    return {"kind": "FLOW", "date": date, "settle": settle or date,
            "currency": ccy, "amount": amount, "where": where,
            "account": account, "desc": desc, "origin": "broker"}

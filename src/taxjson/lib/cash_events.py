"""The cash-event book: what the FX-on-cash ledger v2 reads besides the
trades and income of the position books (tax-logic CA-FX-07 / US-FX-03).

A broker export says more about cash than its trades: currency
conversions, deposits and withdrawals, and the statement's own cash
balances. The parsers' position books leave all of it out (none of it
is a disposition of a security). This module gathers it into a separate
list of events, never rows of the books:

    FXCONV    a conversion: <from currency, amount> -> <to currency,
              amount>, at the base-currency amount actually paid or
              received when one side is the base currency
    CASHMOVE  cash in or out of an account: a deposit, a withdrawal, a
              transfer — signed (+ in, - out)
    CASHBAL   a statement's cash balance in one currency at the END of a
              day (trade-date basis, as the statement prints it)
    CASHOPEN  the opening balance of the ledger's year (a .tt line only)
    FLOW      a fee a cash event charged in a foreign currency

Sources (each broker module's `*_cash_events`): IB Trades/Forex rows,
Deposits & Withdrawals and the Cash Report's Starting/Ending Cash; RBC's
cash rows (WIR/DEP/TFI ...); Kraken's ledger (fiat funding, fiat-for-
fiat trades and its running balances); Coinbase fiat and stablecoin
moves and conversions; and `.tt` lines for whatever no export carries
(Webull, a bank account), written date first like the other dated lines:

    FXCONV <date> <from> <amount> <to> <amount> [value=<base>] [at=<book>]
    CASHMOVE <date> <cur> <signed amount> <how> [at=<book>]
        how: own | kept | spot | cost=<base> | proceeds=<base>
    CASHOPEN <date> <cur> <signed units> <base cost> [at=<book>]
    CASHBAL <date> <cur> <balance> [at=<book>]
    CASHBOOK <book> [complete]  (whose cash the file's rows move;
                        `complete`: the book's cash lines are all of its
                        conversions, deposits and withdrawals)

A CASHMOVE line that names a move a broker export carries (same account
folder, date, currency and amount) DECLARES that move — what it was —
instead of adding one.

Every event belongs to a BOOK: one broker account's cash, named
`<account label>/<broker>` (plus `:<first 4 of the hashed broker
account>` when a label holds several accounts of one broker), or a book
a `.tt` line names with `at=<name>` (a bank account kept by hand).
"""
from __future__ import annotations

import math
import re
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from taxjson.lib import project_layout as _PL

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
# The brokers whose exports' conversions, deposits and withdrawals the
# ledger reads (`_extract`). A book of any other broker (Webull, the
# generic importer) that moves foreign cash is refused until a
# `CASHBOOK <book> complete` line says its .tt cash lines are all of
# them (pre-release review M8).
READERS = ("ib", "rbc_direct", "kraken", "coinbase", "questrade")
COMPLETE = "complete"
MOVE_HOW = ("own", "kept", "spot")

# Parser ids (lib/brokerages/detect) -> the short name a book and an
# `at=` token use.
BROKER_SHORT = {"ib": "ib", "rbc_direct": "rbc", "questrade": "questrade",
                "webull": "webull", "coinbase": "coinbase",
                "kraken": "kraken", "generic": "generic"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CUR_RE = re.compile(r"^[A-Z]{3,5}$")
_KEY_RE = re.compile(r"^([a-z]+)=(\S+)$")
_BOOK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?::[0-9a-f]{1,10})?$")


class CashLineError(ValueError):
    """A cash .tt line that cannot be read (named, with its form)."""


# ------------------------------------------------------------ .tt lines

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
        # book is a .tt line (a broker whose export taxjson does not read
        # them, READERS).
        if (len(parts) not in (2, 3) or not _BOOK_RE.match(parts[1])
                or (len(parts) == 3 and parts[2] != COMPLETE)):
            raise CashLineError(f"{where}malformed CASHBOOK line — "
                                f"expected `{FORMS[kw]}` (a book as "
                                f"`taxjson fx-cash --ledger v2` prints "
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
                            f"(a word, or <broker>:<id> as `taxjson "
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
        if cost < 0:
            raise _bad("the cost is a positive base-currency amount (for "
                       "a debt, what the borrowed units were worth when "
                       "borrowed)")
        if abs(units) < 1e-9 and abs(cost) > 0.005:
            raise _bad("0 units cannot have a cost")
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


def read_project_lines(root: Path, accounts: Dict[str, Any]
                       ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every cash line of the TAXABLE accounts' .tt files (each with
    `label`, `file` and `where` "inputs/<acct>/<file>:<line>"), and the
    problems: a malformed line, a line in a sheltered account (its cash
    is outside s.39(1.1) / §988)."""
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.dated_events import _where, tt_files
    out: List[Dict[str, Any]] = []
    problems: List[str] = []
    for acct in sorted(accounts or {}):
        acfg = accounts.get(acct) or {}
        for tt in tt_files(_PL.inputs_dir(Path(root)) / acct):
            try:
                text = read_text_utf8(tt)
            except (OSError, ValueError):
                continue        # the .tt stage names an unreadable file
            for n, raw in enumerate(text.splitlines(), 1):
                if not any(k in raw for k in KEYWORDS):
                    continue
                where = _where(acct, tt, n)
                try:
                    ev = parse_line(raw, where)
                except CashLineError as e:
                    problems.append(str(e))
                    continue
                if ev is None:
                    continue
                if isinstance(acfg, dict) and acfg.get("type") != "taxable":
                    problems.append(
                        f"{where}: {ev['kind']} is a line of the FX-on-"
                        f"cash ledger; account {acct} is "
                        f"{acfg.get('type') or 'not taxable'} (its cash "
                        f"is not in it) — move the line to a taxable "
                        f"account's .tt: {ev['line']!r}")
                    continue
                from taxjson.lib.brokerages.base import shown_name
                ev.update(label=acct, file=shown_name(tt))
                out.append(ev)
    return out, problems


# ------------------------------------------------------------ events

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


def pair_internal(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop the moves of one statement that cancel inside it: an IB
    internal transfer between two accounts of a combined statement (the
    out and the in, same day, currency and amount), and a deposit
    advance with its cancellation. What is left unpaired stays, to be
    declared like any other move."""
    keep = [True] * len(events)
    by_class: Dict[Tuple, List[int]] = {}
    for i, e in enumerate(events):
        if e.get("kind") == "CASHMOVE" and e.get("internal"):
            by_class.setdefault((e["internal"], e.get("where", ""),
                                 e["currency"]), []).append(i)
    for (cls, _w, _c), idx in by_class.items():
        pos = [i for i in idx if events[i]["amount"] > 0]
        neg = [i for i in idx if events[i]["amount"] < 0]
        used: set = set()
        for i in pos:
            best = None
            for j in neg:
                if j in used:
                    continue
                if abs(events[i]["amount"] + events[j]["amount"]) > 0.005:
                    continue
                if cls == "transfer" and events[i]["date"] != \
                        events[j]["date"]:
                    continue
                gap = abs((_d(events[i]["date"])
                           - _d(events[j]["date"])).days)
                if best is None or gap < best[0]:
                    best = (gap, j)
            if best is not None:
                used.add(best[1])
                keep[i] = keep[best[1]] = False
    return [e for i, e in enumerate(events) if keep[i]]


def _identity(e: Dict[str, Any]) -> Tuple:
    k = e["kind"]
    if k == "FXCONV":
        return (k, e.get("book"), e["date"], e["from_ccy"],
                round(e["from_amt"], 2), e["to_ccy"], round(e["to_amt"], 2))
    if k == "CASHBAL":
        return (k, e.get("book"), e["date"], e["currency"],
                round(e["balance"], 2))
    if k in ("CASHMOVE", "FLOW"):
        return (k, e.get("book"), e["date"], e["currency"],
                round(e["amount"], 2), e.get("desc", ""))
    return (k, id(e))


def dedup_across_files(events: List[Dict[str, Any]]
                       ) -> List[Dict[str, Any]]:
    """Two exports of one account that overlap (a year's statement and a
    year-to-date one) carry the same cash event twice: an event repeated
    in ANOTHER file of the book is one event, the most any one file
    holds of it kept (two identical conversions in one file are two) —
    the position books' cross-file rule."""
    per: Dict[Tuple, Dict[str, int]] = {}
    for e in events:
        f = per.setdefault(_identity(e), {})
        f[e.get("file", "")] = f.get(e.get("file", ""), 0) + 1
    keep_n = {k: max(v.values()) for k, v in per.items()}
    keep_file = {k: max(v, key=lambda f: (v[f], f)) for k, v in per.items()}
    out: List[Dict[str, Any]] = []
    for e in events:
        k = _identity(e)
        if e.get("file", "") != keep_file[k]:
            continue
        if keep_n[k] <= 0:
            continue
        keep_n[k] -= 1
        out.append(e)
    return out


def _d(s: str) -> _date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


# ------------------------------------------------------------ books

def book_name(label: str, broker: str, account: str = "",
              several: bool = False) -> str:
    """`<label>/<broker>[:<first 4 of the hashed account>]`."""
    short = BROKER_SHORT.get(broker, broker)
    if account and several:
        return f"{label}/{short}:{account[:4]}"
    return f"{label}/{short}"


class Books:
    """The books of the taxable accounts: which (label, broker, hashed
    broker account) each file's cash belongs to, and how a `.tt` file or
    an `at=` token picks one."""

    def __init__(self) -> None:
        # label -> {broker: set(account hashes)}
        self.seen: Dict[str, Dict[str, set]] = {}
        # (label, file name) -> broker  (CSV files of the label)
        self.file_broker: Dict[Tuple[str, str], str] = {}
        # (label, file name) -> set(account hashes) the file's rows carry
        self.file_accounts: Dict[Tuple[str, str], set] = {}
        # (label, .tt file name) -> the book its CASHBOOK line names
        self.tt_book: Dict[Tuple[str, str], str] = {}
        # books a `CASHBOOK <book> complete` line declares complete
        self.complete: set = set()

    def add(self, label: str, broker: str, account: str = "") -> None:
        self.seen.setdefault(label, {}).setdefault(broker, set()).add(
            account or "")

    def _several(self, label: str, broker: str) -> bool:
        accts = {a for a in self.seen.get(label, {}).get(broker, set())
                 if a}
        return len(accts) > 1

    def name(self, label: str, broker: str, account: str = "") -> str:
        accts = {a for a in self.seen.get(label, {}).get(broker, set())
                 if a}
        if not account and len(accts) == 1:
            account = next(iter(accts))
        return book_name(label, broker, account,
                         self._several(label, broker))

    def all_names(self, label: str) -> List[str]:
        out = []
        for broker, accts in sorted(self.seen.get(label, {}).items()):
            real = sorted(a for a in accts if a) or [""]
            for a in real:
                out.append(self.name(label, broker, a))
        return sorted(set(out))

    def unread(self) -> List[Tuple[str, str]]:
        """[(book, broker)] of every book whose broker's export the
        ledger reads no cash event from (not in READERS)."""
        out = []
        for label in sorted(self.seen):
            for broker in sorted(self.seen[label]):
                if broker in READERS:
                    continue
                accts = sorted(a for a in self.seen[label][broker]
                               if a) or [""]
                for a in accts:
                    out.append((self.name(label, broker, a), broker))
        return sorted(set(out))

    def for_file(self, label: str, file: str, account: str = ""
                 ) -> Optional[str]:
        """The book of a native row from `file` (a CSV the label's
        detection routed, or a .tt file), or None when it cannot be told
        (a .tt file in a label with several books and no name to go by)."""
        broker = self.file_broker.get((label, file))
        if broker:
            if not account:
                accts = {a for a in self.file_accounts.get(
                    (label, file), set()) if a}
                if len(accts) == 1:
                    account = next(iter(accts))
            return self.name(label, broker, account)
        return self.for_tt(label, file)

    def for_tt(self, label: str, file: str) -> Optional[str]:
        """A .tt file's book: its CASHBOOK line, else the folder's only
        book (or `<label>/tt` in a folder of .tt files only), else None —
        never guessed from the file's name."""
        named = self.tt_book.get((label, file))
        if named:
            return named
        names = self.all_names(label)
        if not names:
            return f"{label}/tt"        # a folder of .tt files only
        if len(names) == 1:
            return names[0]
        return None

    def for_at(self, label: str, at: str) -> Tuple[Optional[str], str]:
        """(book, problem) for an `at=` token: a broker's book of the
        label (`ib`, `rbc:3f2a`), else a book kept by hand under that
        name."""
        short, _, pre = at.partition(":")
        long_ = {v: k for k, v in BROKER_SHORT.items()}.get(short.lower())
        if long_ and long_ in self.seen.get(label, {}):
            accts = sorted(a for a in self.seen[label][long_] if a)
            if pre:
                hit = [a for a in accts if a.startswith(pre.lower())]
                if len(hit) != 1:
                    return None, (f"at={at} names no single {short} "
                                  f"account of {label} (books: "
                                  f"{', '.join(self.all_names(label))})")
                return self.name(label, long_, hit[0]), ""
            if len(accts) > 1:
                return None, (f"at={at}: {label} holds {len(accts)} "
                              f"{short} accounts — name one (books: "
                              f"{', '.join(self.all_names(label))})")
            return self.name(label, long_, accts[0] if accts else ""), ""
        return f"{label}/{at}", ""


# ------------------------------------------------------------ collection

def collect(root: Path, cfg: Dict[str, Any], country: str,
            native_by_label: Dict[str, List[Dict[str, Any]]]
            ) -> Dict[str, Any]:
    """Every cash event of the taxable accounts: {"events": [...] (each
    with `label` and `book`), "lines": [...] (the .tt cash lines, `book`
    resolved where it can be), "books": Books, "problems": [...]}.
    `native_by_label`: the accounts' native rows (their `source` and
    `source_account` tell each CSV's accounts)."""
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.brokerages.detect import AmbiguousBroker, detect
    from taxjson.lib.country import is_usa
    stable_cash = not is_usa(country)
    accounts = cfg.get("accounts") or {}
    books = Books()
    events: List[Dict[str, Any]] = []
    problems: List[str] = []
    for label in sorted(accounts):
        acfg = accounts.get(label) or {}
        if acfg.get("type") != "taxable":
            continue
        folder = _PL.inputs_dir(Path(root)) / label
        for row in native_by_label.get(label, []):
            src = re.sub(r"#\d+$", "", str(row.get("source") or ""))
            sa = str(row.get("source_account") or "")
            books.file_accounts.setdefault((label, src), set()).add(sa)
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() != ".csv" or p.name.startswith((".", "~$")) \
                    or not p.is_file():
                continue
            try:
                det = detect(p)
            except (AmbiguousBroker, OSError, ValueError):
                continue        # `taxjson run` refuses it by name
            if det.positions or not det.broker:
                continue
            name = shown_name(p)
            books.file_broker[(label, name)] = det.broker
            for a in books.file_accounts.get((label, name), {""}):
                books.add(label, det.broker, a)
            try:
                found = _extract(det.broker, p, stable_cash,
                                 acfg.get("crypto"))
            except Exception as e:                  # noqa: BLE001
                problems.append(f"{label}/{name}: its cash events could "
                                f"not be read ({type(e).__name__}: {e})")
                continue
            for ev in found:
                ev["label"] = label
                ev["broker"] = det.broker
                ev["file"] = name
                acct = ev.get("account") or ""
                books.add(label, det.broker, acct)
            events.extend(found)
    for ev in events:
        acct = ev.get("account") or ""
        if not acct:
            accts = {a for a in books.file_accounts.get(
                (ev["label"], ev["file"]), set()) if a}
            if len(accts) == 1:
                acct = next(iter(accts))
        ev["book"] = books.name(ev["label"], ev["broker"], acct)
    events = dedup_across_files(pair_internal(events))
    lines, lp = read_project_lines(root, accounts)
    problems.extend(lp)
    for ev in [x for x in lines if x["kind"] == "CASHBOOK"]:
        b, why = books.for_at(ev["label"], ev["book_name"])
        k = (ev["label"], ev["file"])
        if why:
            problems.append(f"{ev['where']}: {why}")
        elif k in books.tt_book and books.tt_book[k] != b:
            problems.append(f"{ev['where']}: a second CASHBOOK in "
                            f"{ev['file']} names another book ("
                            f"{books.tt_book[k]}) — one file, one account's "
                            f"cash; split the file")
        else:
            books.tt_book[k] = b
        if b and ev.get("complete"):
            books.complete.add(b)
    lines = [x for x in lines if x["kind"] != "CASHBOOK"]
    for ev in lines:
        if ev.get("at"):
            b, why = books.for_at(ev["label"], ev["at"])
        else:
            b = books.for_tt(ev["label"], ev["file"])
            why = "" if b else (
                f"its account folder holds several books "
                f"({', '.join(books.all_names(ev['label'])) or 'none'}) "
                f"— add at=<book>, or a CASHBOOK <book> line to the file")
        ev["book"] = b
        ev["book_problem"] = why
    return {"events": events, "lines": lines, "books": books,
            "problems": problems, "stablecoins_as_cash": stable_cash}


def _extract(broker: str, path: Path, stable_cash: bool,
             crypto: Any) -> List[Dict[str, Any]]:
    if broker == "ib":
        from taxjson.lib.brokerages.ib_extractor import ib_cash_events
        return ib_cash_events(path)
    if broker == "rbc_direct":
        from taxjson.lib.brokerages.rbc_direct import rbc_cash_events
        return rbc_cash_events(path)
    if broker == "kraken":
        from taxjson.lib.brokerages.kraken import kraken_cash_events
        return kraken_cash_events(path, stable_cash)
    if broker == "coinbase":
        from taxjson.lib.brokerages.coinbase import coinbase_cash_events
        return coinbase_cash_events(path, stable_cash)
    if broker == "questrade":
        from taxjson.lib.brokerages.questrade import questrade_cash_events
        return questrade_cash_events(path)
    # Webull and the generic importer: no reader (READERS) — the ledger
    # refuses a book of theirs that moves foreign cash until a CASHBOOK
    # line declares its .tt cash lines complete.
    return []

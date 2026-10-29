"""FX-on-cash ledger v2 — OPT-IN, UNDER AUDIT (tax-logic CA-FX-07 /
US-FX-03).

The method (the same strict one as the default ledger): foreign currency
is property; every spend of it is a disposition; one pooled average cost
per currency across the taxable accounts; a day's rate is the Bank of
Canada's (the project's FX history). What v2 adds is its INPUT and its
refusals:

* conversions are dispositions/acquisitions at the base-currency amount
  actually paid or received (lib/cash_events FXCONV);
* a move between two of your own accounts in the books (`own`) is not a
  disposition: the units and their cost stay in the taxpayer-wide pool
  the whole time, in transit too (only where they sit changes; the
  units in transit are tracked apart, to match the arrival and to
  reconcile each account);
* money from outside the books needs a declared cost (`cost=`), or the
  day's rate when you say so (`spot` on the line, or [settings]
  fx_cash_inflow_cost = "spot"); money leaving them is declared too:
  `kept` (still yours elsewhere: it leaves at its cost, no gain),
  `proceeds=` or `spot` (converted or spent: a disposition);
* a negative balance in a broker account is a DEBT in that currency:
  borrowing values the borrowed units at the day's rate, and paying the
  debt back realises the FX move on it (what the borrowed units were
  worth when borrowed, less what repaying them cost);
* the year opens with the pool carried from the prior year's close
  (close-year records it) or a CASHOPEN line;
* each account's running balance is reconciled to the statement
  balances (IB Cash Report, Kraken ledger balances, CASHBAL lines).

It REFUSES instead of guessing: a missing opening pool, an undeclared
move, a reconciliation gap beyond TOL, an account with in-year activity
and no balance to reconcile it, an overdraft in an account that does not
reconcile, an FX rate missing — each one listed (date, book, amount);
the status is then `not_computed` and there is no reportable figure.
Even a clean result is labelled "v2 (opt-in, under audit)".

Dates: every event is walked on its SETTLEMENT date (the rate and the
tax year, as the default ledger); statement balances are trade-date
balances, so a check adds back what was traded but not yet settled.
CASHOPEN is the SETTLED balance at the start of Jan 1. The cash each
book holds day by day (`holdings`, for `taxjson t1135`) is given on the
project's tax_date basis: on trade dates (`holdings_basis="trade"`)
each leg traded and not yet settled has moved (_on_trade_dates), as the
securities it bought or sold have.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

LABEL = "v2 (opt-in, under audit)"
_COMPLETE = "complete"          # lib/cash_events.COMPLETE
# Units of the currency a statement balance may differ from the ledger's
# by (IB rounds each line to the cent; a few hundred rows of rounding).
TOL = 1.00
_EPS = 1e-6
# An own arrival that settles before its departure (the receiving
# broker credits the move first) is walked on that departure when it
# settles within this many days and its account holds the units until
# it leaves (backlog TJ-0067).
OWN_EARLY_DAYS = 7

RateOf = Callable[[str, str], Optional[float]]


def _p(kind: str, text: str, date: str = "", book: str = "",
       currency: str = "", amount: Optional[float] = None) -> Dict[str, Any]:
    return {"kind": kind, "date": date, "book": book or "",
            "currency": currency, "amount": (round(amount, 2)
                                              if amount is not None
                                              else None),
            "text": text}


def _finite(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)))


def close_problem(close: Any, year: Any) -> Optional[str]:
    """What is wrong with a lock's `fx_cash_v2` close (the next year's
    opening pool, CA-FX-07), else None: the shape close-year writes — a
    table naming its year (the lock's), books that are tables of
    currencies, each with finite units and cost. The one lock check
    (taxjson_filed.lock_damage) applies it, so check-filed no longer
    calls a lock OK whose close the next year's ledger refuses (backlog
    TJ-0068)."""
    if not isinstance(close, dict) or not isinstance(close.get("books"),
                                                     dict):
        return "fx_cash_v2 is not a table of books"
    if year is not None and close.get("year") != year:
        return (f"fx_cash_v2 is the close of {close.get('year')!r}, not "
                f"{year}")
    for book, curs in close["books"].items():
        if not isinstance(curs, dict):
            return f"fx_cash_v2.books.{book} is not a table of currencies"
        for cur, v in curs.items():
            if not isinstance(v, dict) or not _finite(v.get("units")) \
                    or not _finite(v.get("cost")):
                return (f"fx_cash_v2.books.{book}.{cur} has no finite "
                        f"units and cost")
    return None


def _book_parts(book: str) -> Tuple[str, str, str, str]:
    """(label, broker, short, prefix) of a book name `label/short[:prefix]`
    (lib/cash_events.book_name); the broker's long name is "" for a book
    kept by hand."""
    from taxjson.lib.cash_events import BROKER_SHORT
    label, _, rest = book.partition("/")
    short, _, pre = rest.partition(":")
    long_ = {v: k for k, v in BROKER_SHORT.items()}.get(short, "")
    return label, long_, short, pre


def _book_account(books: Any, book: str) -> Optional[str]:
    """The hashed broker account of `book` when the books tell it (one
    account of that broker in the folder, or the one the name's prefix
    picks), else None."""
    if books is None or not hasattr(books, "seen"):
        return None
    label, broker, _short, pre = _book_parts(book)
    accts = sorted(a for a in books.seen.get(label, {}).get(broker, set())
                   if a) if broker else []
    if pre:
        hit = [a for a in accts if a.startswith(pre)]
        return hit[0] if len(hit) == 1 else None
    return accts[0] if len(accts) == 1 else None


def _carried_book(book: str, books: Any, acct: Optional[str]
                  ) -> Tuple[Optional[str], str]:
    """This year's name of a book carried from the prior year's close,
    and why it differs ("" when it does not); (None, why) when it
    cannot be told. lib/cash_events.Books names a broker's book by its
    account only while the account folder holds several accounts of
    that broker, so a book is renamed when a second account appears or
    one goes: the carried `margin/questrade` matched none of this year's
    `margin/questrade:3f2a` / `:9c41`, and a CASHOPEN for the right one
    was added to the carried balance instead of replacing it (2026-10
    audit C05). The account the close recorded (`accounts`, written
    since then) names the book for certain; else the prefix in the
    carried name; a bare name among several accounts is ambiguous."""
    if books is None or not hasattr(books, "seen"):
        return book, ""
    label, broker, short, pre = _book_parts(book)
    accts = sorted(a for a in books.seen.get(label, {}).get(broker, set())
                   if a) if broker else []
    if not broker or not accts:
        return book, ""         # kept by hand, or no file of the broker
    names = sorted(books.name(label, broker, a) for a in accts)
    if acct:
        if acct in accts:
            name = books.name(label, broker, acct)
            return name, ("" if name == book else
                          f"{label} holds {len(accts)} {short} accounts "
                          f"now ({', '.join(names)})" if len(accts) > 1
                          else f"the only {short} account of {label} now")
        # Its account has no file this year while another of the broker
        # has: kept apart under its own name, never mixed into theirs.
        return f"{label}/{short}:{acct[:4]}", (
            f"its account has no file in {label} this year; "
            f"{', '.join(names)} is another {short} account")
    if book in names:
        return book, ""
    if pre:
        hit = [a for a in accts if a.startswith(pre)]
        if len(hit) == 1:
            return books.name(label, broker, hit[0]), (
                f"the only {short} account of {label} now"
                if len(accts) == 1 else
                f"{label} holds {len(accts)} {short} accounts now")
        return None, (f"{label} holds {len(accts)} {short} account(s) "
                      f"({', '.join(names)}), none or several starting "
                      f"with {pre}")
    return None, (f"{label} now holds {len(accts)} {short} accounts "
                  f"({', '.join(names)}) where it had one")


def _carry_openings(carry: Dict[str, Any],
                    opening: Dict[Tuple[str, str], Dict[str, Any]],
                    books: Any, base: str, year: int,
                    problems: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """The prior year's close into `opening` (a CASHOPEN for the same
    book and currency replaces its entry). Returns (books carried,
    notes). Refused, never guessed (2026-10 audit C02-C05): a record
    that could not be read (`carry["problem"]`, from
    bin/taxjson_run._fx_cash_carry), costs in another base currency, a
    units or cost value that is not a finite number, or one a CASHOPEN
    line could not give (lib/cash_events.opening_problem: a cost below
    zero, a cost on no units), and a carried book this year's books
    cannot place (_carried_book) unless a CASHOPEN for one of the
    broker's books takes its place."""
    from taxjson.lib.cash_events import opening_problem
    notes: List[str] = []
    y0 = f"{year}-01-01"
    cy = carry.get("year")
    src = f"close of {cy}"
    # The record the close came from: [settings] prior_year_record may
    # name another file than filed/<year>.json (backlog TJ-0068).
    rec = carry.get("record") or f"filed/{cy}.json"
    repair = (f"the prior-year record is damaged: restore {rec} "
              f"from git or re-close {cy} with this ledger")
    # A record of the other country, or costs in another base
    # currency, cannot open this year; its advice is to open the year
    # with CASHOPEN lines, and with them the record is noted, not a
    # problem (backlog TJ-0022: the refusal stayed with the lines in).
    if carry.get("problem"):
        if carry.get("foreign") and opening:
            notes.append(str(carry["foreign"]))
            return 0, notes
        problems.append(_p("carry", str(carry["problem"])))
    ccur = str(carry.get("currency") or "").upper()
    if ccur and ccur != base:
        if opening:
            notes.append(f"{src}: its costs are in {ccur}, not this "
                         f"project's {base}: not carried — the year opens "
                         f"from its CASHOPEN lines")
            return 0, notes
        problems.append(_p(
            "carry", f"{src}: its costs are in {ccur}, and this project's "
            f"base currency is {base} — a pool carried from a project of "
            f"another base currency cannot open this year; if {base} is "
            f"wrong set [settings] base_currency, else open the year with "
            f"CASHOPEN lines (docs/settings.md)", y0, "", ccur))
        return 0, notes
    accounts = carry.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
    cbooks = carry.get("books")
    if not isinstance(cbooks, dict):
        problems.append(_p("carry", f"{src}: its books are not a table — "
                           f"{repair}", y0))
        return 0, notes
    carried = 0
    for book, curs in cbooks.items():
        book = str(book)
        if not isinstance(curs, dict):
            problems.append(_p("carry", f"{src}: {book} is not a table of "
                               f"currencies — {repair}", y0, book))
            continue
        target, why = _carried_book(book, books, accounts.get(book))
        if target is not None and target != book:
            notes.append(f"{src}: {book} is {target} this year ({why}) — "
                         f"carried under that name")
        for cur, v in curs.items():
            cur = str(cur).upper()
            if not isinstance(v, dict) or not _finite(v.get("units")) \
                    or not _finite(v.get("cost")):
                shown = ({k: v.get(k) for k in ("units", "cost")}
                         if isinstance(v, dict) else v)
                problems.append(_p(
                    "carry", f"{src}: {book} {cur} carries {shown!r} — "
                    f"units and cost must be finite numbers; {repair}",
                    y0, book, cur))
                continue
            units, cost = float(v["units"]), float(v["cost"])
            # The rule a CASHOPEN line is held to (a cost below zero, a
            # cost on no units): a close never writes either, so the
            # record was damaged or edited (2026-10-25 audit year-end#2).
            unsound = opening_problem(units, cost)
            if unsound:
                problems.append(_p(
                    "carry", f"{src}: {book} {cur} carries {units:,.2f} "
                    f"{cur} at a cost of {cost:,.2f} {base} — as on a "
                    f"CASHOPEN line, {unsound}; {repair}",
                    y0, book, cur, units))
                continue
            if target is None and abs(units) <= _EPS \
                    and abs(cost) <= _EPS:
                continue        # a zero book: nothing to place
            if target is None:
                mine = sorted(k for k in opening
                              if k[1] == cur and k[0].startswith(f"{book}:"))
                if mine:
                    notes.append(
                        f"{', '.join(opening[k]['source'] for k in mine)}: "
                        f"CASHOPEN for {', '.join(k[0] for k in mine)} "
                        f"{cur} replaces the prior year's carried balance "
                        f"of {book} ({why})")
                    continue
                label = book.split("/", 1)[0]
                problems.append(_p(
                    "carry", f"{src} carries {units:,.2f} {cur} (cost "
                    f"{cost:,.2f} {base}) for {book}, and {why} — which "
                    f"of them held it the record does not say: add "
                    f"`CASHOPEN {y0} {cur} {units:.2f} {cost:.2f} "
                    f"at={book.split('/', 1)[1]}:<the first letters of its "
                    f"book>` to a .tt of {label} (it replaces the carried "
                    f"balance)", y0, book, cur, units))
                continue
            k = (target, cur)
            if k in opening:
                notes.append(f"{opening[k]['source']}: CASHOPEN for "
                             f"{target} {cur} replaces the prior year's "
                             f"carried balance")
                continue
            opening[k] = {"units": units, "cost": cost, "source": src}
            carried += 1
    return carried, notes


def _reward_cash(row: Dict[str, Any], stable_cash: bool
                 ) -> Optional[Tuple[str, float]]:
    """(currency, units) of a reward paid in cash that the export prices
    in ANOTHER currency: a Coinbase "Rewards Income" of 5 USDC valued at
    6.85 CAD is booked as income in CAD (its DIVIDEND row's currency),
    while the 5 US dollars (a USD stablecoin is US-dollar cash,
    CA-CRYPTO-02) came into the account (backlog TJ-0038). None for
    any other row, or when the row's currency is the reward's own."""
    if row.get("action") != "DIVIDEND" or not str(
            row.get("description") or "").lower().startswith(
                "staking reward"):
        return None
    from taxjson.lib.brokerages._crypto_common import (FIAT_CURRENCIES,
                                                       USD_STABLECOINS)
    sym = str(row.get("symbol") or "").upper().split(".")[0]
    cur = (sym if sym in FIAT_CURRENCIES else
           "USD" if stable_cash and sym in USD_STABLECOINS else "")
    units = abs(float(row.get("quantity") or 0.0))
    if not cur or cur == str(row.get("currency") or "").upper() \
            or units < _EPS:
        return None
    return cur, units


def _in_lag(legs: List[Tuple[Any, ...]], dates: List[str]):
    """For each date of `dates` (ascending): (date, the legs of `legs`
    — (trade date, settle date, ...) — traded and not yet settled at
    its end, or settled and not yet traded). A sweep: the lags are a
    few days, so few legs are open at once."""
    order = sorted((leg for leg in legs if leg[0] != leg[1]),
                   key=lambda leg: min(leg[0], leg[1]))
    i, act = 0, []
    for d in dates:
        while i < len(order) and min(order[i][0], order[i][1]) <= d:
            act.append(order[i])
            i += 1
        act = [leg for leg in act if d < max(leg[0], leg[1])]
        yield d, act


def _on_trade_dates(points: List[List[Any]], net: List[List[Any]],
                    avgs: List[List[Any]],
                    legs: List[Tuple[str, str, float, Optional[float]]],
                    prev_end: str, y1: str) -> List[List[Any]]:
    """A book's [[date, units, cost]] of a foreign currency (the walk's,
    at the end of each SETTLEMENT day) on the TRADE-date basis, for a
    project whose tax_date is trade (tax-logic CA-RPT-20; backlog
    TJ-0627): at the end of each day, what had settled plus each leg
    traded and not yet settled (less one settled before its trade
    date). The walk of `taxjson t1135` dates the securities on that
    basis: on settlement dates the cash that paid for shares was
    counted with them until it settled, and a sale's proceeds were in
    no row until then.

    A leg moves on its trade date: money coming in at its own cost (the
    cost per unit the walk gave it when it settled, else the pool's
    average), first repaying a debt (`net`: the settled units less a
    debt, the units held when positive); money going out at the average
    cost of what is then held. `avgs`: the pool's average cost per unit
    at the end of each day it changed; `legs`: (trade date, settle
    date, units signed, cost per unit or None)."""
    dates = {p[0] for p in points if prev_end <= p[0] <= y1}
    for td, sd, _a, _c in legs:
        if td == sd:
            continue
        dates.update(x for x in (td, sd) if prev_end < x <= y1)
        if min(td, sd) <= prev_end < max(td, sd):
            dates.add(prev_end)
    out: List[List[Any]] = []
    ip = in_ = ia = 0
    sp: Optional[List[Any]] = None
    sn: Optional[List[Any]] = None
    avg = 0.0
    for d, act in _in_lag(legs, sorted(dates)):
        while ip < len(points) and points[ip][0] <= d:
            sp = points[ip]
            ip += 1
        while in_ < len(net) and net[in_][0] <= d:
            sn = net[in_]
            in_ += 1
        while ia < len(avgs) and avgs[ia][0] <= d:
            avg = float(avgs[ia][1])
            ia += 1
        u = float(sn[1]) if sn else (float(sp[1]) if sp else 0.0)
        c = float(sp[2]) if sp and u > _EPS else 0.0
        outs: List[Tuple[float, Optional[float]]] = []
        for td, sd, amt, per in act:
            x = amt if td < sd else -amt
            own = per if amt > 0 else None
            if x > 0:
                rep = min(x, max(-u, 0.0))
                u += x
                c += (x - rep) * (own if own is not None else avg)
            else:
                outs.append((-x, own))
        for x, own in outs:
            take = min(x, max(u, 0.0))
            if own is None:
                own = c / u if u > _EPS else 0.0
            c -= take * own
            u -= x
        held = u if u > _EPS else 0.0
        pt = [d, round(held, 6), round(max(c, 0.0), 2) if held else 0.0]
        if not out or out[-1][1:] != pt[1:]:
            out.append(pt)
    return out


def _unwalked_cost(o: Dict[str, Any], cur: str, amt: float, base: str,
                   rate_of: RateOf) -> Optional[float]:
    """The cost per unit of `amt` units of `cur` coming in by `o`, an
    event the year's walk does not reach (traded in the year, settled
    after it): what it gives (a declared cost, the base amount a
    conversion paid, its value=), else the day's rate; None for a move
    between your own accounts (the pool's average) or no rate."""
    if amt <= _EPS:
        return None
    fee = float(o.get("fee") or 0.0)
    if o["kind"] == "flow" and o.get("cost") is not None:
        return float(o["cost"]) / amt
    if o["kind"] == "conv" and o.get("to_ccy") == cur:
        if o.get("from_ccy") == base:
            return (float(o["from_amt"]) + fee) / amt
        if o.get("value"):
            return (float(o["value"]) + fee) / amt
    if o["kind"] == "move":
        decl = o.get("decl") or {}
        if decl.get("how") == "own":
            return None
        if decl.get("how") == "cost" and decl.get("value") is not None:
            return float(decl["value"]) / amt
    for d in (o["sd"], o["td"]):
        r = rate_of(cur, d)
        if r is not None:
            return float(r)
    return None


def _base_holdings(ops: List[Dict[str, Any]], checks: List[Dict[str, Any]],
                   opening: Dict[str, Dict[str, Any]], lost: set,
                   year: int, basis: str = "settle"
                   ) -> List[Dict[str, Any]]:
    """Each book's balance of the base currency through `year`, for
    `taxjson t1135` (tax-logic CA-RPT-20): [{"book", "ok", "why",
    "points": [[date, balance], ...]}] — the balance at the end of each
    day it changed, the first point the year's opening (dated the day
    before the year). A balance is given (`ok`) only where the books
    give it: an opening (a `CASHOPEN <Jan 1> <base>` line, else the
    statement's balance on or before the day before the year, the
    flows after it added) and every statement balance of the year
    reconciled within TOL, one on or after the year's last flow.
    `ops`: the base-currency flows ({"sd", "td", "time", "book",
    "amount"}), `checks` the statement balances (CASHBAL), `opening`
    {book: CASHOPEN line}, `lost` the account labels with flows no book
    could be told for. Else `why` says what is missing. `basis` "trade"
    gives the points on trade dates (a flow traded and not yet settled
    has moved: the trade-date balance, backlog TJ-0627); the checks are
    the same."""
    y0, y1 = f"{year}-01-01", f"{year}-12-31"
    prev_end = f"{year - 1}-12-31"
    books = sorted({o["book"] for o in ops} | {c["book"] for c in checks}
                   | set(opening))
    out: List[Dict[str, Any]] = []
    for book in books:
        mine = sorted((o for o in ops if o["book"] == book),
                      key=lambda o: (o["sd"], o["td"], o.get("time") or ""))
        stmts = sorted((c for c in checks if c["book"] == book),
                       key=lambda c: c["date"])

        def adj(d: str) -> float:
            # What a trade-date statement shows at the end of `d` beyond
            # the settled balance (as the foreign ledger's adj).
            a = 0.0
            for o in mine:
                if o["td"] <= d < o["sd"]:
                    a += o["amount"]
                elif o["sd"] <= d < o["td"]:
                    a -= o["amount"]
            return a

        row: Dict[str, Any] = {"book": book, "ok": False, "why": "",
                               "points": []}
        out.append(row)
        if book.split("/", 1)[0] in lost:
            row["why"] = ("rows in this currency of a .tt file whose book "
                          "cannot be told (add a CASHBOOK line)")
            continue
        if book in opening:
            bal = float(opening[book]["units"])
        else:
            before = [c for c in stmts if c["date"] <= prev_end]
            if not before:
                row["why"] = (f"no balance at the start of {year}: no "
                              f"statement balance on or before "
                              f"{prev_end} and no `CASHOPEN {y0}` line")
                continue
            last = before[-1]
            bal = float(last["balance"]) - adj(last["date"]) + sum(
                o["amount"] for o in mine
                if last["date"] < o["sd"] <= prev_end)
        pts: List[List[Any]] = [[prev_end, round(bal, 2)]]
        for o in mine:
            if not y0 <= o["sd"] <= y1:
                continue
            bal += o["amount"]
            if pts[-1][0] == o["sd"]:
                pts[-1][1] = round(bal, 2)
            else:
                pts.append([o["sd"], round(bal, 2)])
        row["points"] = pts

        def bal_on(d: str) -> float:
            v = pts[0][1]
            for pd, pv in pts:
                if pd <= d:
                    v = pv
            return v
        bad = ""
        for c in stmts:
            if not prev_end <= c["date"] <= y1:
                continue
            led = bal_on(c["date"]) + adj(c["date"])
            if abs(float(c["balance"]) - led) > TOL:
                bad = (f"the statement balance at the end of {c['date']} "
                       f"is {float(c['balance']):,.2f}, the books' "
                       f"{led:,.2f} ({c.get('where') or 'CASHBAL'})")
                break
        active = [o["td"] for o in mine if y0 <= o["sd"] <= y1]
        if not bad and active:
            last_td = min(max(active), y1)
            if not any(last_td <= c["date"] <= y1 for c in stmts):
                bad = (f"no statement balance on or after its last {year} "
                       f"flow ({last_td}) to reconcile it against (a "
                       f"CASHBAL line)")
        row["ok"] = not bad
        row["why"] = bad
        if basis == "trade":
            row["points"] = _base_on_trade_dates(pts, mine, prev_end, y1)
    return out


def _base_on_trade_dates(pts: List[List[Any]], mine: List[Dict[str, Any]],
                         prev_end: str, y1: str) -> List[List[Any]]:
    """_base_holdings' settled [[date, balance]] on trade dates: the
    balance a trade-date statement shows at the end of each day (each
    flow traded and not yet settled added, one settled before its trade
    date taken back)."""
    legs = [(o["td"], o["sd"], float(o["amount"])) for o in mine]
    dates = {p[0] for p in pts}
    for td, sd, _a in legs:
        if td == sd:
            continue
        dates.update(x for x in (td, sd) if prev_end < x <= y1)
    out: List[List[Any]] = []
    ip, v = 0, 0.0
    for d, act in _in_lag(legs, sorted(dates)):
        while ip < len(pts) and pts[ip][0] <= d:
            v = float(pts[ip][1])
            ip += 1
        t = round(v + sum(a if td < sd else -a for td, sd, a in act), 2)
        if not out or out[-1][1] != t:
            out.append([d, t])
    return out


def build(native: List[Dict[str, Any]], cash: Dict[str, Any], base: str,
          year: int, rate_of: RateOf, *, country: str,
          carry: Optional[Dict[str, Any]] = None,
          inflow_spot: bool = False,
          holdings: bool = False,
          holdings_basis: str = "settle") -> Dict[str, Any]:
    """The v2 ledger of `year`. `native`: the taxable accounts' native
    rows, each with `_book` (lib/cash_events.Books.for_file; None when
    no book can be told). `cash`: lib/cash_events.collect's result.
    `carry`: the prior year's close ({"year", "currency", "books": {book:
    {cur: {"units", "cost"}}}, "accounts": {book: hashed account}}; a
    "problem" text when the record exists but could not be read:
    bin/taxjson_run._fx_cash_carry) — checked, never trusted
    (_carry_openings). `inflow_spot`: [settings] fx_cash_inflow_cost
    = "spot" (an undeclared inflow costs the day's rate).

    `holdings` (`taxjson t1135`, tax-logic CA-RPT-20) adds the doc's
    `holdings`: {"foreign": [{"book", "currency", "points": [[date,
    units, cost], ...]}] — each book's units of each foreign currency
    at the end of each day they or the pool's average cost changed,
    their cost the units times the pool's average cost per unit (one
    pool per currency, s.47), the first point the year's opening dated
    the day before it; "base": _base_holdings' balances of the base
    currency; "interest": the year's INTEREST rows naming no symbol,
    [{"id", "date", "account", "currency"}] — the currency it was paid
    in (a USD stablecoin's as USD where they are cash), which the base
    books do not keep: the income of that cash row (backlog TJ-0628)}.
    `holdings_basis` is the project's tax_date: "settle" (the
    walk's own dates) or "trade" (each leg traded and not yet settled
    has moved: _on_trade_dates; backlog TJ-0627) — the dates `taxjson
    t1135` walks the securities on, so the cash that paid for shares is
    never counted with them, nor a sale's proceeds left out."""
    from taxjson.bin.taxjson_fx_cash import _cash_legs
    if holdings_basis not in ("settle", "trade"):
        raise ValueError(f"holdings_basis {holdings_basis!r}: settle or "
                         f"trade")
    trade_basis = holdings and holdings_basis == "trade"
    base = base.upper()
    y0, y1 = f"{year}-01-01", f"{year}-12-31"
    prev_end = f"{year - 1}-12-31"
    problems: List[Dict[str, Any]] = [
        _p("input", t) for t in cash.get("problems") or []]
    notes: List[str] = []
    # The base currency's flows, statement balances and openings, for
    # `holdings` (_base_holdings); the labels with flows of it no book
    # could be told for.
    base_ops: List[Dict[str, Any]] = []
    base_checks: List[Dict[str, Any]] = []
    base_open: Dict[str, Dict[str, Any]] = {}
    base_lost: set = set()

    def in_year(d: str) -> bool:
        return y0 <= d <= y1

    # ------------------------------------------------ the unit-moving ops
    ops: List[Dict[str, Any]] = []
    seq = [0]

    def op(**kw) -> None:
        seq[0] += 1
        kw.setdefault("time", "")
        kw["seq"] = seq[0]
        ops.append(kw)

    from taxjson.lib.futures import (has_plain_futures, method_for,
                                     settle_futures_dicts)
    if has_plain_futures(native):
        native = settle_futures_dicts(list(native), method_for(country),
                                      fee_basis=True)
    unattributed: Dict[str, int] = {}
    # A statement's period: a row it carries dated before the period (a
    # correction IB posts with the original date) moved its cash inside
    # the period — the statement's own balances say so.
    import re as _re
    period_start: Dict[Tuple[str, str], str] = {}
    for ev in cash.get("events") or []:
        if ev.get("kind") == "PERIOD":
            period_start[(ev.get("label", ""), ev.get("file", ""))] = \
                ev["start"]
    redated = 0
    # The stablecoin policy of the project (lib/cash_events.collect): a
    # US project's stablecoin is property, so a reward in one priced in
    # a foreign currency moves none of it (backlog TJ-0493).
    stable_cash = cash.get("stablecoins_as_cash")
    if stable_cash is None:
        from taxjson.lib.country import is_usa
        stable_cash = not is_usa(country)
    stable_cash = bool(stable_cash)
    # `holdings`: the year's interest rows naming no symbol (interest an
    # account pays on its cash), each with the currency it was paid in:
    # the base books carry it in the base currency only, and `taxjson
    # t1135` makes it the income of that currency's cash row (tax-logic
    # CA-RPT-14; backlog TJ-0628).
    interest: List[Dict[str, Any]] = []
    for row in native:
        sd = str(row.get("date_settle") or row.get("date") or "")[:10]
        td = str(row.get("date") or sd)[:10]
        if holdings and str(row.get("action") or "").upper() == "INTEREST" \
                and str(row.get("symbol") or "").upper() in ("", "CASH") \
                and row.get("id") and td[:4] == str(year):
            from taxjson.lib.brokerages._crypto_common import \
                USD_STABLECOINS
            icur = str(row.get("currency") or "").upper()
            if stable_cash and icur in USD_STABLECOINS:
                icur = "USD"
            interest.append({"id": str(row["id"]), "date": td,
                             "account": str(row.get("account") or ""),
                             "currency": icur})
        ps = period_start.get((str(row.get("account") or ""), _re.sub(
            r"#\d+$", "", str(row.get("source") or ""))))
        if ps and td < ps:
            td, sd = ps, max(sd, ps)
            redated += 1
        legs = [(c, a, None) for c, a in _cash_legs(row, stable_cash)]
        rw = _reward_cash(row, stable_cash)
        if rw is not None:
            # The reward's own currency comes in, at the value the
            # income is booked at when that is in the base currency
            # (else at the day's rate, as a reward priced in it).
            v = float(row.get("gross_amount") or row.get("net_amount")
                      or 0.0)
            legs = [(rw[0], rw[1], abs(v) if str(row.get("currency")
                                                 or "").upper() == base
                     else None)]
        for cur, amt, cost in legs:
            cur = (cur or "").upper()
            if not cur or abs(amt) < _EPS:
                continue
            book = row.get("_book")
            if cur == base:
                if not holdings:
                    continue
                if book is None:
                    if in_year(sd) or (td < y0 <= sd):
                        base_lost.add(str(row.get("account") or ""))
                    continue
                base_ops.append({"sd": sd, "td": td, "book": book,
                                 "time": str(row.get("time") or ""),
                                 "amount": float(amt)})
                continue
            if book is None:
                if in_year(sd) or (td < y0 <= sd):
                    src = str(row.get("source") or "?")
                    unattributed[f"{row.get('account')}/{src}"] = \
                        unattributed.get(f"{row.get('account')}/{src}",
                                         0) + 1
                continue
            op(kind="flow", sd=sd, td=td, time=str(row.get("time") or ""),
               book=book, currency=cur, amount=float(amt), cost=cost,
               desc=f"{row.get('action')} {row.get('symbol') or ''}"
                    .strip(), where=str(row.get("source") or ""))
    if redated:
        notes.append(f"{redated} row(s) of a statement dated before its "
                     f"period (a correction posted with the original "
                     f"date) moved their cash inside the period: walked "
                     f"at the period's first day")
    for where, n in sorted(unattributed.items()):
        problems.append(_p(
            "book", f"{where}: {n} cash leg(s) in a foreign currency in or "
            f"across {year}, and the account folder holds several broker "
            f"accounts — add a `CASHBOOK <book>` line to the file (split "
            f"it when its rows are several accounts')"))

    events = list(cash.get("events") or [])
    lines = list(cash.get("lines") or [])
    checks: List[Dict[str, Any]] = []
    for ev in events:
        # (IB's Forex Balances cross-check: the base currency's line is
        # not a foreign-cash balance.)
        if ev["kind"] == "NOTE" and str(ev.get("currency") or "").upper() \
                != base:
            notes.append(ev["text"])
    # .tt CASHMOVE lines: a declaration of a broker's move when one
    # matches, else a move of their own (lib/cash_events; `taxjson
    # cash` lists the same).
    from taxjson.lib.cash_events import apply_declarations
    apply_declarations(events, lines)
    # A conversion's commission in the base currency (IB's "Comm in
    # CAD", a Kraken conversion fee in CAD): part of the base amount
    # actually paid or received (CA-FX-07; a purchase commission adds
    # to the cost, CA-ACB-02), never a cash flow of its own — dropped
    # with the base currency's other flows, it left the currency bought
    # that much too cheap (backlog TJ-0456). Keyed by the conversion's
    # book and row (`_fee_of`, the broker's cash reader).
    conv_fee: Dict[Tuple[str, str], float] = {}
    # A commission in the currency the conversion sells or buys (a
    # Kraken fee of 10 USD on USD sold for CAD): the units it took are
    # part of what was disposed of for the amount received, or come off
    # the units the amount paid bought (CA-FX-07). Walked as a spend of
    # its own at the day's rate, it made a gain of its own and left the
    # conversion's short by its units (backlog TJ-0621). Keyed as
    # conv_fee; {currency: units}.
    conv_ccys: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for ev in events:
        if ev["kind"] == "FXCONV" and ev.get("book") \
                and ev.get("origin") != "tt":
            conv_ccys[(ev["book"], str(ev.get("where") or ""))] = (
                str(ev["from_ccy"]).upper(), str(ev["to_ccy"]).upper())
    side_fee: Dict[Tuple[str, str], Dict[str, float]] = {}
    folded: set = set()
    for ev in events:
        if ev["kind"] == "FLOW" and ev.get("_fee_of") and ev.get("book"):
            k = (ev["book"], str(ev["_fee_of"]))
            cur = ev["currency"].upper()
            if cur == base:
                conv_fee[k] = conv_fee.get(k, 0.0) - float(ev["amount"])
            elif cur in conv_ccys.get(k, ()):
                fees = side_fee.setdefault(k, {})
                fees[cur] = fees.get(cur, 0.0) - float(ev["amount"])
                folded.add(id(ev))
    for ev in events + [x for x in lines if not x.get("matched")]:
        if id(ev) in folded:
            continue            # in its conversion (side_fee)
        kind = ev["kind"]
        book = ev.get("book")
        if kind in ("FXCONV", "CASHMOVE", "CASHBAL", "FLOW",
                    "CASHOPEN") and not book:
            # A CASHOPEN of a foreign currency with no book is reported
            # too, not dropped by the openings below (2026-10 audit #52).
            if ev.get("origin") == "tt" and (
                    kind == "CASHBAL" or in_year(ev["date"])) and not (
                    kind == "CASHOPEN"
                    and str(ev.get("currency", "")).upper() == base):
                problems.append(_p("book", f"{ev.get('where')}: "
                                   f"{ev.get('book_problem') or 'no book'}",
                                   ev["date"], "", ev.get("currency", "")))
            if holdings and kind != "CASHOPEN" and base in (
                    str(ev.get(k) or "").upper()
                    for k in ("currency", "from_ccy", "to_ccy")) \
                    and (kind == "CASHBAL" or in_year(ev["date"])):
                base_lost.add(str(ev.get("label") or ""))
            continue
        if kind == "FXCONV":
            ck = (book, str(ev.get("where") or ""))
            broker = ev.get("origin") != "tt"
            fee = conv_fee.pop(ck, 0.0) if broker else 0.0
            sides = side_fee.pop(ck, {}) if broker else {}
            fc_, tc_ = ev["from_ccy"].upper(), ev["to_ccy"].upper()
            op(kind="conv", sd=ev["settle"], td=ev["date"], book=book,
               from_ccy=fc_,
               from_amt=float(ev["from_amt"]) + sides.get(fc_, 0.0),
               to_ccy=tc_,
               to_amt=float(ev["to_amt"]) - sides.get(tc_, 0.0),
               value=ev.get("value"), desc=ev.get("desc") or "FXCONV",
               where=ev.get("line_where") or ev.get("where", ""),
               fee=fee)
            if holdings and base in (fc_, tc_):
                base_ops.append({
                    "sd": ev["settle"], "td": ev["date"], "book": book,
                    "amount": (float(ev["to_amt"]) if tc_ == base
                               else -float(ev["from_amt"]))})
        elif kind == "FLOW":
            if ev["currency"].upper() != base:
                op(kind="flow", sd=ev["settle"], td=ev["date"], book=book,
                   currency=ev["currency"].upper(), amount=ev["amount"],
                   desc=ev.get("desc", ""), where=ev.get("where", ""))
            elif holdings:
                base_ops.append({"sd": ev["settle"], "td": ev["date"],
                                 "book": book,
                                 "amount": float(ev["amount"])})
        elif kind == "CASHMOVE":
            if ev["currency"].upper() != base:
                op(kind="move", sd=ev["settle"], td=ev["date"], book=book,
                   currency=ev["currency"].upper(), amount=ev["amount"],
                   decl=ev.get("decl"), desc=ev.get("desc", ""),
                   where=(ev.get("line_where") or ev.get("where", "")),
                   decl_where=ev.get("decl_where") or (
                       ev.get("where") if ev.get("origin") == "tt"
                       else ""))
            elif holdings:
                base_ops.append({"sd": ev["settle"], "td": ev["date"],
                                 "book": book,
                                 "amount": float(ev["amount"])})
        elif kind == "CASHBAL":
            if ev["currency"].upper() != base:
                checks.append(dict(ev, currency=ev["currency"].upper()))
            elif holdings:
                base_checks.append(ev)

    # ------------------------------------------------ openings
    opening: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ln in [x for x in lines if x["kind"] == "CASHOPEN"]:
        cur = ln["currency"].upper()
        if cur == base:
            # The base currency's balance at the start of the year
            # (_base_holdings): no cost of its own to carry.
            if holdings and ln["date"] == y0 and ln.get("book") \
                    and ln["book"] not in base_open:
                base_open[ln["book"]] = ln
            continue
        if ln["date"] != y0:
            if in_year(ln["date"]):
                problems.append(_p(
                    "opening", f"{ln.get('where')}: CASHOPEN is the "
                    f"settled balance at the start of the year — date it "
                    f"{y0}", ln["date"], ln.get("book") or "", cur))
            continue
        if not ln.get("book"):
            continue            # already a book problem above
        k = (ln["book"], cur)
        if k in opening:
            problems.append(_p("opening", f"{ln.get('where')}: a second "
                               f"CASHOPEN for {ln['book']} {cur} — keep "
                               f"one", y0, ln["book"], cur))
            continue
        opening[k] = {"units": ln["units"], "cost": ln["cost"],
                      "source": ln.get("where") or "CASHOPEN"}
    carried = 0
    if carry:
        carried, _cn = _carry_openings(carry, opening, cash.get("books"),
                                       base, year, problems)
        notes.extend(_cn)

    # Trade-date vs settle-date: what a statement shows at the end of D
    # beyond the settled ledger.
    def _legs(o: Dict[str, Any]) -> List[Tuple[str, float]]:
        if o["kind"] == "conv":
            return [(o["from_ccy"], -o["from_amt"]),
                    (o["to_ccy"], o["to_amt"])]
        return [(o["currency"], o["amount"])]

    by_bc: Dict[Tuple[str, str], List[Tuple[str, str, float]]] = {}
    for o in ops:
        for cur, amt in _legs(o):
            if cur != base:
                by_bc.setdefault((o["book"], cur), []).append(
                    (o["td"], o["sd"], amt))

    def adj(book: str, cur: str, d: str) -> float:
        a = 0.0
        for td, sd, amt in by_bc.get((book, cur), ()):
            if td <= d < sd:
                a += amt
            elif sd <= d < td:
                a -= amt
        return a

    active: Dict[Tuple[str, str], str] = {}      # (book, cur) -> last td
    for o in ops:
        if in_year(o["sd"]):
            for cur, _a in _legs(o):
                if cur != base:
                    k = (o["book"], cur)
                    active[k] = max(active.get(k, ""), o["td"])
    # A book of a broker whose export the ledger reads no cash event from
    # (Webull, the generic importer): its trades move foreign cash, its
    # conversions, deposits and withdrawals are unseen — a figure built
    # on them was wrong (pre-release review M8). Refused until a
    # `CASHBOOK <book> complete` line says its .tt cash lines are all.
    _books = cash.get("books")
    unread: List[Dict[str, Any]] = []
    if _books is not None and hasattr(_books, "unread"):
        moving = {b for (b, _c) in active}
        for book, broker in _books.unread():
            done = book in _books.complete
            unread.append({"book": book, "broker": broker,
                           "complete": done, "moves": book in moving})
            if book in moving and not done:
                curs = sorted(c for (b, c) in active if b == book)
                short = book.split("/", 1)[1]
                problems.append(_p(
                    "unread", f"{book}: taxjson does not read {broker}'s "
                    f"conversions, deposits or withdrawals from its export, "
                    f"and this account moves {', '.join(curs)} in {year} — "
                    f"write each as a .tt line (FXCONV, CASHMOVE) in "
                    f"{book.split('/', 1)[0]}, then `CASHBOOK {short} "
                    f"{_COMPLETE}` to say they are all there",
                    "", book, curs[0]))
    # The remedy a missing opening names: re-closing the prior year
    # helps only when it was not closed with this ledger (backlog
    # TJ-0028: the message asked for the close that was already there).
    if carry and not carry.get("problem") and str(
            carry.get("currency") or base).upper() == base:
        reclose = (f" (the close of {year - 1} does not list it: it "
                   f"held none then, or is new)")
    else:
        reclose = f", or close {year - 1} with this ledger"
    stmt_open = {(c["book"], c["currency"]): c for c in checks
                 if c["date"] == prev_end}
    need = set(active) | {(c["book"], c["currency"]) for c in checks
                          if prev_end <= c["date"] <= y1}
    for k in sorted(need):
        if k in opening:
            continue
        book, cur = k
        if k in stmt_open:
            settled = stmt_open[k]["balance"] - adj(book, cur, prev_end)
            if abs(settled) <= TOL:
                opening[k] = {"units": 0.0, "cost": 0.0,
                              "source": f"{stmt_open[k]['where']} (zero)"}
                continue
            problems.append(_p(
                "opening", f"no opening pool for {book} {cur}: the "
                f"statement holds {settled:,.2f} {cur} (settled) at the "
                f"start of {year} and nothing gives their cost — add "
                f"`CASHOPEN {y0} {cur} {settled:.2f} <cost in {base}> "
                f"at={book.split('/', 1)[1]}` to a .tt of "
                f"{book.split('/', 1)[0]}{reclose}", y0, book, cur,
                settled))
        else:
            problems.append(_p(
                "opening", f"no opening pool for {book} {cur} — add "
                f"`CASHOPEN {y0} {cur} <settled units> <cost in {base}> "
                f"at={book.split('/', 1)[1]}` (0 0 when it held none) to a "
                f".tt of {book.split('/', 1)[0]}{reclose}", y0, book, cur))

    # ------------------------------------------------ the walk
    pool: Dict[str, List[float]] = {}            # cur -> [units, cost]
    held: Dict[Tuple[str, str], float] = {}
    owed: Dict[Tuple[str, str], List[float]] = {}   # [units, basis]
    # Units of a currency sent between your own accounts and not yet
    # recorded by the receiving one. They are still yours: their units
    # and cost stay in `pool` (issue #78) — this only tracks WHERE they
    # are, to match the arrival and to reconcile each account.
    transit: Dict[str, float] = {}
    # Units of an own departure (by its seq) an arrival walked before it
    # already took (_promise): the departure puts only the rest in
    # transit.
    promised: Dict[int, float] = {}
    for (book, cur), o in opening.items():
        u, c = float(o["units"]), float(o["cost"])
        if u > _EPS:
            held[(book, cur)] = u
            p = pool.setdefault(cur, [0.0, 0.0])
            p[0] += u
            p[1] += c
        elif u < -_EPS:
            owed[(book, cur)] = [-u, c]
    per: Dict[str, Dict[str, float]] = {}
    gain_events: List[Dict[str, Any]] = []
    borrowed: List[Dict[str, Any]] = []
    repaid: List[Dict[str, Any]] = []
    declared: List[Dict[str, Any]] = []
    recon: List[Dict[str, Any]] = []
    unrated: List[Tuple[str, str]] = []
    # `holdings`: (book, cur) -> [[date, units held, their cost]], the
    # state at the end of each day it changed (a debt holds none).
    fser: Dict[Tuple[str, str], List[List[Any]]] = {}
    # On trade dates (_on_trade_dates): each book's settled units less
    # its debt, the pool's average cost per unit, and the cost per unit
    # the walk gave each inflow ({(op seq, currency): cost per unit}).
    fnet: Dict[Tuple[str, str], List[List[Any]]] = {}
    favg: Dict[str, List[List[Any]]] = {}
    in_cost: Dict[Tuple[int, str], float] = {}

    def _put(series: List[List[Any]], pt: List[Any]) -> None:
        if series and series[-1][0] == pt[0]:
            series[-1] = pt
        elif not series or series[-1][1:] != pt[1:]:
            series.append(pt)

    def record(cur: str, d: str) -> None:
        """Every book's units of `cur` and their cost (the pool's
        average cost per unit, s.47) after an event of `d`."""
        p = pool.get(cur) or [0.0, 0.0]
        avg = p[1] / p[0] if p[0] > _EPS else 0.0
        for k in sorted({k for k in held if k[1] == cur}
                        | {k for k in fser if k[1] == cur}):
            u = max(held.get(k, 0.0), 0.0)
            _put(fser.setdefault(k, []), [d, round(u, 6), round(u * avg, 2)])
        if trade_basis:
            _put(favg.setdefault(cur, []), [d, avg])
            for k in sorted({k for k in held if k[1] == cur}
                            | {k for k in owed if k[1] == cur}
                            | {k for k in fnet if k[1] == cur}):
                _put(fnet.setdefault(k, []), [d, round(
                    held.get(k, 0.0) - (owed.get(k) or [0.0])[0], 6)])

    def record_op(o: Dict[str, Any]) -> None:
        if holdings and o["kind"] != "check":
            for cur in sorted({c for c, _a in _legs(o) if c != base}):
                record(cur, o["sd"])
    if holdings:
        for cur in sorted({c for (_b, c) in held}
                          | ({c for (_b, c) in owed} if trade_basis
                             else set())):
            record(cur, prev_end)

    def stat(cur: str) -> Dict[str, float]:
        return per.setdefault(cur, {"acquired": 0.0, "disposed": 0.0,
                                    "gain": 0.0, "debt_gain": 0.0})

    def rate(cur: str, d: str) -> Optional[float]:
        r = rate_of(cur, d)
        if r is None:
            unrated.append((cur, d))
        return r

    def outflow(o, cur: str, x: float, r: Optional[float], mode: str,
                proceeds: Optional[float] = None) -> None:
        k = (o["book"], cur)
        h = held.get(k, 0.0)
        cov = min(x, h)
        exc = x - cov
        if exc > _EPS and r is None:
            r = rate(cur, o["sd"])
            if r is None:
                return          # unrated: a problem, the event skipped
        rel = 0.0
        p = pool.setdefault(cur, [0.0, 0.0])
        if cov > _EPS:
            avg = p[1] / p[0] if p[0] > _EPS else 0.0
            rel = cov * avg
            p[0] -= cov
            p[1] -= rel
            held[k] = h - cov
            if p[0] < _EPS:
                p[0] = p[1] = 0.0
        if exc > _EPS:
            d = owed.setdefault(k, [0.0, 0.0])
            d[0] += exc
            d[1] += exc * r
            rel += exc * r
            borrowed.append({"date": o["sd"], "book": o["book"],
                             "currency": cur, "units": round(exc, 2),
                             "what": o.get("desc", "")})
        s = stat(cur)
        if mode == "spend":
            pr = proceeds if proceeds is not None else (
                x * r if r is not None else rel)
            g = pr - rel
            s["disposed"] += x
            s["gain"] += g
            gain_events.append({"date": o["sd"], "book": o["book"],
                                "currency": cur, "units": round(x, 2),
                                "rate": r, "gain": round(g, 2),
                                "what": o.get("desc", "")})
        elif mode == "own":
            # Not a disposition: the units leave this account, not the
            # pool. Put back what the covered part released; the
            # overdrawn part (borrowed above at the day's rate) is now
            # yours too and joins the pool at that cost.
            p[0] += x
            p[1] += rel
            transit[cur] = transit.get(cur, 0.0) + x - promised.pop(
                o.get("seq"), 0.0)
        elif mode == "kept":
            declared.append({"date": o["sd"], "book": o["book"],
                             "currency": cur, "amount": round(-x, 2),
                             "how": "kept",
                             "cost_left": round(rel, 2),
                             "where": o.get("decl_where") or ""})

    def inflow(o, cur: str, x: float, cost: float) -> None:
        k = (o["book"], cur)
        if trade_basis and x > _EPS:
            in_cost[(o.get("seq"), cur)] = cost / x
        s = stat(cur)
        s["acquired"] += x
        d = owed.get(k)
        rest = x
        if d and d[0] > _EPS:
            rep = min(x, d[0])
            avg_b = d[1] / d[0]
            k_r = cost * rep / x
            g = rep * avg_b - k_r
            d[0] -= rep
            d[1] -= rep * avg_b
            if d[0] < _EPS:
                owed.pop(k, None)
            s["debt_gain"] += g
            repaid.append({"date": o["sd"], "book": o["book"],
                           "currency": cur, "units": round(rep, 2),
                           "gain": round(g, 2)})
            rest = x - rep
        if rest > _EPS:
            held[k] = held.get(k, 0.0) + rest
            p = pool.setdefault(cur, [0.0, 0.0])
            p[0] += rest
            p[1] += cost * rest / x

    def arrive(o, cur: str, x: float) -> None:
        """Own-transfer units arriving: already in the pool (units and
        cost) — they only change place. A debt in the receiving account
        is repaid with them at the pool's average cost."""
        k = (o["book"], cur)
        p = pool.setdefault(cur, [0.0, 0.0])
        rest = x
        d = owed.get(k)
        if d and d[0] > _EPS:
            rep = min(x, d[0])
            avg = p[1] / p[0] if p[0] > _EPS else 0.0
            avg_b = d[1] / d[0]
            g = rep * (avg_b - avg)
            d[0] -= rep
            d[1] -= rep * avg_b
            if d[0] < _EPS:
                owed.pop(k, None)
            p[0] -= rep
            p[1] -= rep * avg
            if p[0] < _EPS:
                p[0] = p[1] = 0.0
            stat(cur)["debt_gain"] += g
            repaid.append({"date": o["sd"], "book": o["book"],
                           "currency": cur, "units": round(rep, 2),
                           "gain": round(g, 2)})
            rest = x - rep
        if rest > _EPS:
            held[k] = held.get(k, 0.0) + rest

    def bal(book: str, cur: str) -> float:
        return held.get((book, cur), 0.0) - (owed.get((book, cur))
                                             or [0.0])[0]

    def _stable_bal_note(c: Dict[str, Any]) -> str:
        """A CASHBAL line of US dollars in a crypto exchange's book, with
        stablecoins counted as cash: the statement shows the US dollars
        and the USDC apart, and the line must give both together — the
        gap was blamed on a conversion that does not exist (backlog
        TJ-0460)."""
        broker = c["book"].split("/", 1)[-1].split(":", 1)[0]
        if not (stable_cash and c["currency"] == "USD"
                and c.get("origin") == "tt"
                and broker in ("coinbase", "kraken")):
            return ""
        return ("; or the line gives the US dollars alone: USD "
                "stablecoins are US-dollar cash here (CA-CRYPTO-02), so a "
                "crypto account's CASHBAL USD is its US dollars and its "
                "USDC (and other USD stablecoins) together")

    walk = [o for o in ops if in_year(o["sd"])]
    for c in checks:
        if prev_end <= c["date"] <= y1:
            walk.append({"kind": "check", "sd": c["date"], "td": "~",
                         "time": "~", "seq": 0, "book": c["book"],
                         "check": c})
    def _own(o: Dict[str, Any]) -> bool:
        return o["kind"] == "move" and (o.get("decl") or {}).get(
            "how") == "own"

    # One settle day: by trade date and time, an own arrival after the
    # other events of its moment; checks last.
    def _order(o: Dict[str, Any]) -> Tuple:
        return (o["sd"], 9 if o["kind"] == "check" else 0, o["td"],
                o.get("time") or "",
                1 if _own(o) and o["amount"] > 0 else 0, o["seq"])
    walk.sort(key=_order)

    def _waits(i: int) -> bool:
        """walk[i] an own arrival that the units in transit do not cover
        while a departure of its currency comes later on its settlement
        day: the two brokers book their own trade dates (the receiving
        one the request day, the sending one the day it moved), and
        walked by trade date the arrival was refused as "only 0.00 had
        left", the departure as "never arrived" (2026-10 audit A20). The
        departure comes up to the earliest point from which its account
        holds the units until the departure's own place (no earlier than
        the arrival: the same units leave sooner, so no figure moves),
        and the arrival is walked right after it; the other events keep
        their order, so a conversion that funds the departure that day
        still comes first. Moving every own departure to the start of
        its day instead overdrew the account a same-day conversion
        funds: a debt at the day's rate, a gain on repaying it and the
        pool at spot, not at the amount paid (2026-10-25 audit
        diff-engine#2). True when walk changed."""
        o = walk[i]
        if not (_own(o) and o["amount"] > 0):
            return False
        cur, sd = o["currency"], o["sd"]
        if transit.get(cur, 0.0) + TOL >= o["amount"]:
            return False
        # The departure it waits for leaves ANOTHER account: one of its
        # own account's (a chain a -> b -> c on one day) needs this
        # arrival first, and walked before it made up a debt (backlog
        # TJ-0067). Of several, one its account holds the units for
        # (no overdraft), and of those one that holds them only by an
        # own arrival before it (the chain's next link: an account that
        # holds them from the start is left for that arrival).
        best = None
        for j in range(i + 1, len(walk)):
            d = walk[j]
            if d["sd"] != sd:
                break
            if not (_own(d) and d["amount"] < 0 and d["currency"] == cur) \
                    or d["book"] == o["book"]:
                continue
            # Its account's balance before each event from the arrival
            # to the departure's own place; the earliest point from
            # which it stays at or above the units that leave.
            bal_at = [held.get((d["book"], cur), 0.0)]
            for x in walk[i:j]:
                bal_at.append(bal_at[-1] + (
                    sum(a for c, a in _legs(x) if c == cur)
                    if x["kind"] != "check" and x["book"] == d["book"]
                    else 0.0))
            need, at = -d["amount"] - _EPS, j - i
            holds = bal_at[at] >= need  # else it overdraws in place
            if holds:
                while at > 0 and bal_at[at - 1] >= need:
                    at -= 1
            link = holds and any(
                _own(x) and x["amount"] > 0 and x["currency"] == cur
                and x["book"] == d["book"] for x in walk[i + 1:j])
            rank = (2 if link else 1 if holds else 0)
            if best is None or rank > best[0]:
                best = (rank, j, at)
        if best is None:
            return False
        _rank, j, at = best
        dep, arr = walk.pop(j), walk.pop(i)
        pos = i + max(at - 1, 0)
        walk.insert(pos, dep)
        walk.insert(pos + 1, arr)
        return True

    def _promise(k: int, short: float) -> str:
        """walk[k] an own arrival the units in transit fall `short` of,
        with no departure of another account later on its settlement
        day (_waits): it is walked on a later own departure, which then
        puts only the rest of its units in transit (`promised`) —
        either its own account's later that day (the units pass
        through it: a chain a -> b -> c whose middle account books the
        arrival after another one took the first units), or another
        account's that settles within OWN_EARLY_DAYS days after it
        while that account holds the units until they leave (the
        receiving broker credited the move first: the units are in the
        pool the whole time, so no figure moves; backlog TJ-0067). ""
        when promised, else why not (for the refusal)."""
        from datetime import date, timedelta
        o = walk[k]
        cur, sd = o["currency"], o["sd"]
        try:
            last = (date.fromisoformat(sd)
                    + timedelta(days=OWN_EARLY_DAYS)).isoformat()
        except ValueError:
            last = sd
        why = ""
        for j in range(k + 1, len(walk)):
            d = walk[j]
            if not (_own(d) and d["amount"] < 0 and d["currency"] == cur):
                continue
            room = -d["amount"] - promised.get(d["seq"], 0.0)
            if room + TOL < short:
                continue
            if d["sd"] == sd and d["book"] == o["book"]:
                pass                    # its own account sends them on
            elif d["sd"] == sd or d["book"] == o["book"]:
                continue
            elif d["sd"] > last:
                why = why or (f"the next move out of another of your "
                              f"accounts, {d['book']}'s on {d['sd']}, "
                              f"settles more than {OWN_EARLY_DAYS} days "
                              f"after it")
                break
            else:
                # The departure's account holds the units from now
                # until it leaves (its balance before the departure is
                # what that row adds back).
                low = min([held.get((d["book"], cur), 0.0)]
                          + [v for v in _running(k, j, d["book"], cur)])
                if low + _EPS < -d["amount"]:
                    why = why or (f"{d['book']}'s move out of "
                                  f"{-d['amount']:,.2f} {cur} settles "
                                  f"later, on {d['sd']}, and {d['book']} "
                                  f"does not hold them until then")
                    continue
            take = min(short, room)
            promised[d["seq"]] = promised.get(d["seq"], 0.0) + take
            transit[cur] = transit.get(cur, 0.0) + take
            return ""
        return why or (f"no move out of another of your accounts settles "
                       f"on its day or in the {OWN_EARLY_DAYS} days after")

    def _running(k: int, j: int, book: str, cur: str) -> List[float]:
        """`book`'s balance of `cur` after each event from walk[k+1] to
        the one before walk[j]."""
        v, out_ = held.get((book, cur), 0.0), []
        for x in walk[k + 1:j]:
            if x["kind"] != "check" and x["book"] == book:
                v += sum(a for c, a in _legs(x) if c == cur)
                out_.append(v)
        return out_

    i = 0
    walked: Optional[Dict[str, Any]] = None
    while i < len(walk):
        if _waits(i):
            continue
        if walked is not None:
            record_op(walked)       # its state, before the next event
        o = walked = walk[i]
        i += 1
        if o["kind"] == "check":
            c = o["check"]
            book, cur = c["book"], c["currency"]
            if (book, cur) not in opening and (book, cur) in need:
                continue        # its missing opening is the problem
            led = bal(book, cur) + adj(book, cur, c["date"])
            gap = c["balance"] - led
            row = {"book": book, "currency": cur, "date": c["date"],
                   "statement": round(c["balance"], 2),
                   "ledger": round(led, 2), "gap": round(gap, 2),
                   "ok": abs(gap) <= TOL,
                   "source": c.get("where") or ""}
            recon.append(row)
            if not row["ok"]:
                problems.append(_p(
                    "reconcile", f"{book} {cur}: the statement balance at "
                    f"the end of {c['date']} is {c['balance']:,.2f}, the "
                    f"ledger's {led:,.2f} (gap {gap:+,.2f}, over the "
                    f"{TOL:.2f} tolerance) — a conversion, deposit or "
                    f"withdrawal the ledger does not see ({row['source']})"
                    + _stable_bal_note(c), c["date"], book, cur, gap))
            continue
        if o["kind"] == "flow":
            cur = o["currency"]
            if o.get("cost") is not None and o["amount"] > 0:
                inflow(o, cur, o["amount"], o["cost"])
                continue
            r = rate(cur, o["sd"])
            if r is None:
                continue
            if o["amount"] < 0:
                outflow(o, cur, -o["amount"], r, "spend")
            else:
                inflow(o, cur, o["amount"], o["amount"] * r)
            continue
        if o["kind"] == "conv":
            fc, tc = o["from_ccy"], o["to_ccy"]
            # Its commission in the base currency (conv_fee): added to
            # what the currency bought cost, taken from what the
            # currency sold fetched.
            fee = float(o.get("fee") or 0.0)
            if fc == base:
                inflow(o, tc, o["to_amt"], o["from_amt"] + fee)
            elif tc == base:
                outflow(o, fc, o["from_amt"], None, "spend",
                        proceeds=o["to_amt"] - fee)
            else:
                # An explicit value= prices the conversion: a missing
                # rate is no problem (outflow asks for one only for an
                # overdrawn part, 2026-10 audit #53).
                v = o.get("value")
                if v:
                    rf = rate_of(fc, o["sd"])   # None is fine here
                else:
                    rf = rate(fc, o["sd"])
                    if rf is None:
                        continue
                    v = o["from_amt"] * rf
                outflow(o, fc, o["from_amt"], rf, "spend", proceeds=v)
                inflow(o, tc, o["to_amt"], v + fee)
            continue
        # a move
        cur, amt = o["currency"], o["amount"]
        decl = o.get("decl")
        how = (decl or {}).get("how")
        val = (decl or {}).get("value")
        if decl is None:
            if amt > 0 and inflow_spot:
                how = "spot"
                declared.append({"date": o["sd"], "book": o["book"],
                                 "currency": cur, "amount": round(amt, 2),
                                 "how": "spot (fx_cash_inflow_cost)",
                                 "where": o.get("where", "")})
            else:
                problems.append(_p(
                    "undeclared", f"{o['book']}: {amt:+,.2f} {cur} "
                    f"{'came in' if amt > 0 else 'went out'} on {o['sd']} "
                    f"({o.get('desc') or 'a cash move'}, "
                    f"{o.get('where')}) and nothing says what it was — "
                    f"add `CASHMOVE {o['td']} {cur} {amt:.2f} "
                    + ("cost=<what it cost in " + base + ">` (or own / "
                       "spot)" if amt > 0 else
                       "kept` (still yours elsewhere; or proceeds=<"
                       + base + "> / spot / own)")
                    + f" to a .tt of {o['book'].split('/', 1)[0]}",
                    o["sd"], o["book"], cur, amt))
                # Walked on (to find the other problems) as the neutral
                # reading: in at the day's rate, out at its cost.
                how = "spot" if amt > 0 else "kept"
        elif how in ("spot", "cost", "proceeds"):
            declared.append({"date": o["sd"], "book": o["book"],
                             "currency": cur, "amount": round(amt, 2),
                             "how": how + (f"={val:,.2f}" if val is not None
                                           else ""),
                             "where": o.get("decl_where") or ""})
        if how == "own" and amt > 0:
            t = transit.get(cur, 0.0)
            why = _promise(i - 1, amt - t) if t + TOL < amt else ""
            t = transit.get(cur, 0.0)
            if t + TOL < amt:
                problems.append(_p(
                    "own", f"{o['book']}: {amt:,.2f} {cur} came in on "
                    f"{o['sd']} as a move between your own accounts, but "
                    f"only {t:,.2f} {cur} had left one of them before "
                    f"it, and {why} — check the dates (the settlement "
                    f"dates of the two CASHMOVE lines), or declare it "
                    f"cost= / spot",
                    o["sd"], o["book"], cur, amt))
                r = rate(cur, o["sd"])
                if r is not None:
                    inflow(o, cur, amt, amt * r)
                continue
            take = min(amt, t)
            transit[cur] = t - take
            if amt - take > _EPS:
                # Within the tolerance more arrived than left: the
                # rounding joins the pool at no cost.
                p = pool.setdefault(cur, [0.0, 0.0])
                p[0] += amt - take
            arrive(o, cur, amt)
            continue
        if amt < 0:
            mode = {"own": "own", "kept": "kept"}.get(how, "spend")
            r = rate(cur, o["sd"]) if how == "spot" else None
            if how == "spot" and r is None:
                continue
            outflow(o, cur, -amt, r, mode,
                    proceeds=val if how == "proceeds" else None)
            continue
        if how == "cost":
            inflow(o, cur, amt, val)
            continue
        r = rate(cur, o["sd"])                       # spot
        if r is not None:
            inflow(o, cur, amt, amt * r)
    if walked is not None:
        record_op(walked)

    for cur, d in sorted({(c, d) for c, d in unrated}):
        problems.append(_p("rate", f"no {cur} rate on file for {d} (or "
                           f"the 5 days before) — run `taxjson run` to "
                           f"refresh the rates", d, "", cur))
    for cur, t in sorted(transit.items()):
        if t > TOL:
            problems.append(_p(
                "own", f"{t:,.2f} {cur} left an account as a move "
                f"between your own accounts and never arrived in another "
                f"by {y1}", y1, "", cur, t))
        elif t > _EPS:
            # Within the tolerance less arrived than left: the rounding
            # leaves the pool at its average cost (no gain), so the pool
            # is what the accounts hold.
            p = pool.get(cur) or [0.0, 0.0]
            if p[0] > _EPS:
                avg = p[1] / p[0]
                p[0] -= min(t, p[0])
                p[1] = max(p[1] - t * avg, 0.0)
                if p[0] < _EPS:
                    p[0] = p[1] = 0.0
    # Every account with in-year activity needs a statement balance on
    # or after its last event to reconcile against.
    checked: Dict[Tuple[str, str], str] = {}
    for row in recon:
        k = (row["book"], row["currency"])
        checked[k] = max(checked.get(k, ""), row["date"])
    bad_books = {(p["book"], p["currency"]) for p in problems
                 if p["kind"] == "reconcile"}
    for k, last in sorted(active.items()):
        if k in opening and checked.get(k, "") < min(last, y1):
            problems.append(_p(
                "reconcile", f"{k[0]} {k[1]}: no statement balance on or "
                f"after its last {year} event ({last}) to reconcile the "
                f"ledger against — add `CASHBAL <date> {k[1]} <balance as "
                f"the statement shows it> at={k[0].split('/', 1)[1]}` "
                f"(e.g. dated {y1}) to a .tt of {k[0].split('/', 1)[0]}",
                last, k[0], k[1]))
            bad_books.add(k)
    # Overdrafts are debt only in an account that reconciles.
    for b in borrowed:
        if (b["book"], b["currency"]) in bad_books:
            problems.append(_p(
                "overdraft", f"{b['book']}: the balance went "
                f"{b['units']:,.2f} {b['currency']} below zero on "
                f"{b['date']} ({b['what']}) and the account does not "
                f"reconcile, so it cannot be told from missing cash",
                b["date"], b["book"], b["currency"], -b["units"]))

    # ------------------------------------------------ the close
    close_books: Dict[str, Dict[str, Dict[str, float]]] = {}
    for (book, cur), h in held.items():
        if h > _EPS:
            p = pool.get(cur) or [0.0, 0.0]
            avg = p[1] / p[0] if p[0] > _EPS else 0.0
            close_books.setdefault(book, {})[cur] = {
                "units": round(h, 6), "cost": round(h * avg, 6)}
    for (book, cur), d in owed.items():
        if d[0] > _EPS:
            close_books.setdefault(book, {})[cur] = {
                "units": round(-d[0], 6), "cost": round(d[1], 6)}
    # A book the year walked that ends it at zero is recorded at zero:
    # left out, the next year asked for a `CASHOPEN ... 0 0` for it
    # (backlog TJ-0028).
    for book, cur in sorted(need):
        close_books.setdefault(book, {}).setdefault(
            cur, {"units": 0.0, "cost": 0.0})
    # The close names its base currency and each book's broker account
    # (hashed, as the books do): next year checks the first and places
    # each book by the second (_carry_openings).
    close_accounts: Dict[str, str] = {}
    for book in sorted(close_books):
        a = _book_account(cash.get("books"), book)
        if a:
            close_accounts[book] = a
    net = sum(s["gain"] + s["debt_gain"] for s in per.values())
    status = "not_computed" if problems else "computed"
    extra: Dict[str, Any] = {}
    if holdings:
        series = fser
        if trade_basis:
            series = {}
            legs: Dict[Tuple[str, str], List[Tuple[str, str, float,
                                                   Optional[float]]]] = {}
            for o in ops:
                for cur, amt in _legs(o):
                    if cur == base or o["td"] == o["sd"]:
                        continue
                    unit = in_cost.get((o.get("seq"), cur))
                    if unit is None and amt > 0 and not in_year(o["sd"]):
                        unit = _unwalked_cost(o, cur, amt, base, rate_of)
                    legs.setdefault((o["book"], cur), []).append(
                        (o["td"], o["sd"], float(amt), unit))
            for k in sorted(set(fser) | set(legs)):
                pts = _on_trade_dates(fser.get(k, []), fnet.get(k, []),
                                      favg.get(k[1], []), legs.get(k, []),
                                      prev_end, y1)
                if pts or k in fser:
                    series[k] = pts
        extra["holdings"] = {
            "basis": holdings_basis,
            "foreign": [{"book": b, "currency": c, "points": pts}
                        for (b, c), pts in sorted(series.items())],
            "base": _base_holdings(base_ops, base_checks, base_open,
                                   base_lost, year, holdings_basis),
            "interest": sorted(interest, key=lambda r: (r["date"],
                                                        r["id"]))}
    return {
        **extra,
        "ledger": "v2", "label": LABEL, "status": status,
        "reliable": status == "computed", "under_audit": True,
        "year": year, "currency": base,
        "problems": problems,
        "per_currency": {c: {k: round(v, 2) for k, v in s.items()}
                         for c, s in sorted(per.items())},
        "net_gain": round(net, 2),
        "events": gain_events, "borrowed": borrowed, "repaid": repaid,
        "declared": [d for d in declared if d],
        "reconciliation": recon,
        "opening": {f"{b} {c}": {"units": round(o["units"], 2),
                                 "cost": round(o["cost"], 2),
                                 "source": o["source"]}
                    for (b, c), o in sorted(opening.items())},
        "carried_in": carried,
        "close": {"year": year, "currency": base, "books": close_books,
                  "accounts": close_accounts},
        "pools_year_end": {c: {"units": round(p[0], 2),
                               "acb": round(p[1], 2)}
                           for c, p in sorted(pool.items())
                           if p[0] > 0.005},
        "notes": notes,
        # lib/cash_events.collect's warnings (one event two files date
        # differently): said, never a refusal.
        "warnings": list(cash.get("warnings") or []),
        # Books whose broker's export gives no cash event (READERS):
        # each with whether a CASHBOOK line declared its lines complete.
        "unread_books": unread,
        "stablecoins_as_cash": bool(cash.get("stablecoins_as_cash")),
    }


def headline(doc: Dict[str, Any], verdict: Optional[Dict[str, Any]]
             ) -> str:
    """The one line every output shows for the v2 result."""
    y = doc["year"]
    if doc["status"] != "computed":
        n = len(doc["problems"])
        first = doc["problems"][0]["text"] if n else ""
        return (f"FX on foreign cash: NOT COMPUTED for {y}, ledger "
                f"{LABEL} — {n} problem{'' if n == 1 else 's'}"
                + (f", first: {first}" if first else "")
                + "; no reportable figure")
    rep = (verdict or {}).get("reportable")
    return (f"FX on foreign cash, ledger {LABEL}: computed for {y} — net "
            f"{doc['net_gain']:,.2f} {doc['currency']}"
            + (f", reportable {rep:,.2f}" if rep is not None else "")
            + "; review it before using it")


def render(doc: Dict[str, Any], verdict: Optional[Dict[str, Any]],
           country: str, width_: Optional[int] = None,
           cash_events: Optional[List[Dict[str, Any]]] = None) -> str:
    """The v2 report in the house layout (docs/output-style.md)."""
    from taxjson.lib import out
    from taxjson.lib.country import is_usa
    from taxjson.lib.report_model import fmt_money
    w = out.width() if width_ is None else width_
    base, year = doc["currency"], doc["year"]
    lines = out.wrap(headline(doc, verdict) + ".", w, "", "  ")
    lines.append("")
    rule = (verdict or {}).get("rule") or (
        "§988" if is_usa(country) else "s.39(1.1)")
    lines += out.wrap(f"FX GAINS ON CASH — {base}, tax year {year}, "
                      f"{rule}, ledger {LABEL}", w)
    lines += out.wrap("Every spend of foreign currency is a disposition; "
                      "one pooled average cost per currency across the "
                      "taxable accounts; conversions at the amount "
                      "actually paid or received; moves between your own "
                      "accounts are not dispositions; a negative balance "
                      "is a debt in that currency, realised when repaid; "
                      "each account reconciled to its statement balances.",
                      w)
    if doc.get("stablecoins_as_cash") and not is_usa(country):
        lines += out.wrap("Policy: USD stablecoins (USDC and the others) "
                          "are counted as US-dollar cash (CA-CRYPTO-02), "
                          "not as crypto-assets.", w)
    lines.append("")
    if doc["status"] != "computed":
        lines.append(f"NOT COMPUTED — {len(doc['problems'])} problem(s); "
                     f"no reportable figure")
        body = [[p["date"] or "-", p["book"] or "-", p["currency"] or "-",
                 (fmt_money(p["amount"]) if p["amount"] is not None
                  else "-"), p["kind"]]
                for p in doc["problems"]]
        lines += out.fit_table(["DATE", "BOOK", "CUR", "AMOUNT", "WHAT"],
                               body, aligns=["<", "<", "<", ">", "<"],
                               width_=w)
        lines.append("")
        for p in doc["problems"]:
            lines += out.wrap("- " + p["text"], w, "", "  ")
    else:
        body = [[c, fmt_money(s["acquired"]), fmt_money(s["disposed"]),
                 fmt_money(s["gain"]), fmt_money(s["debt_gain"])]
                for c, s in sorted(doc["per_currency"].items())]
        foot = [["NET", "", "", "", fmt_money(doc["net_gain"])]]
        if verdict is not None:
            foot.append(["REPORTABLE", "", "", "",
                         fmt_money(verdict["reportable"])])
        # UNITS IN / OUT are the currency's own units, the gains the
        # base currency's: one "All amounts CAD" over both read 10,000
        # US dollars as 10,000 CAD (backlog TJ-0458).
        lines += out.fit_table(["CUR", "UNITS IN", "UNITS OUT",
                                f"GAIN(LOSS) {base}", f"ON DEBT {base}"],
                               body, aligns=["<", ">", ">", ">", ">"],
                               foot=foot, width_=w)
        if verdict is not None:
            lines.append("")
            lines += out.wrap(f"UNITS IN / OUT: units of each currency; "
                              f"the gains in {base}. {verdict['note']}", w)
    if doc.get("opening"):
        lines.append("")
        lines.append("OPENING")
        lines += out.fit_table(
            ["BOOK CUR", "UNITS", "COST", "FROM"],
            [[k, fmt_money(v["units"]), fmt_money(v["cost"]), v["source"]]
             for k, v in doc["opening"].items()],
            aligns=["<", ">", ">", "<"], width_=w)
    if doc.get("reconciliation"):
        lines.append("")
        lines.append("RECONCILIATION (statement vs ledger, trade-date "
                     f"balance; tolerance {TOL:.2f})")
        lines += out.fit_table(
            ["DATE", "BOOK", "CUR", "STATEMENT", "LEDGER", "GAP"],
            [[r["date"], r["book"], r["currency"],
              fmt_money(r["statement"]), fmt_money(r["ledger"]),
              fmt_money(r["gap"])] for r in doc["reconciliation"]],
            aligns=["<", "<", "<", ">", ">", ">"], width_=w, key=0)
    if doc.get("borrowed"):
        lines.append("")
        lines.append(f"BORROWED (a negative balance: a debt in that "
                     f"currency) — {len(doc['borrowed'])} time(s)")
        lines += out.fit_table(
            ["DATE", "BOOK", "CUR", "UNITS"],
            [[b["date"], b["book"], b["currency"], fmt_money(b["units"])]
             for b in doc["borrowed"]],
            aligns=["<", "<", "<", ">"], width_=w, key=0)
    if doc.get("declared"):
        lines.append("")
        lines.append("DECLARED MOVES")
        lines += out.fit_table(
            ["DATE", "BOOK", "CUR", "AMOUNT", "HOW"],
            [[d["date"], d["book"], d["currency"], fmt_money(d["amount"]),
              d["how"]] for d in doc["declared"]],
            aligns=["<", "<", "<", ">", "<"], width_=w, key=0)
    if doc.get("unread_books"):
        lines.append("")
        lines.append("NOT READ FROM THE EXPORT (conversions, deposits, "
                     "withdrawals)")
        lines += out.fit_table(
            ["BOOK", "BROKER", "CASH LINES"],
            [[u["book"], u["broker"],
              ("declared complete (CASHBOOK)" if u["complete"] else
               "not declared complete" + ("" if u["moves"] else
                                          " (no foreign cash moved)"))]
             for u in doc["unread_books"]],
            aligns=["<", "<", "<"], width_=w)
    for n in doc.get("warnings") or []:
        lines.append("")
        lines += out.wrap(out.label("warning", w) + n, w, "", "")
    for n in doc.get("notes") or []:
        lines.append("")
        lines += out.wrap(out.label("note", w) + n, w, "", "")
    if cash_events is not None:
        lines.append("")
        lines.append("CASH EVENTS READ")
        # A .tt CASHMOVE that declared a broker's move is shown once, on
        # that move (DECLARED BY names the line), not as a second move
        # (backlog TJ-0054); an empty list says so (TJ-0168).
        shown = [e for e in cash_events if not e.get("matched")]
        by = [str(e.get("decl_where") or "") if e.get("origin") != "tt"
              else "" for e in shown]
        if shown:
            head = ["DATE", "BOOK", "KIND", "DETAIL"] + (
                ["DECLARED BY"] if any(by) else [])
            lines += out.fit_table(
                head,
                [[e.get("date", ""), e.get("book") or "-", e["kind"],
                  _detail(e)] + ([b] if any(by) else [])
                 for e, b in zip(shown, by)],
                aligns=["<"] * len(head), drop=[4], width_=w, key=0)
        else:
            lines.append("None read from the exports or .tt lines.")
    return "\n".join(lines)


def _detail(e: Dict[str, Any]) -> str:
    k = e["kind"]
    if k == "FXCONV":
        return (f"{e['from_amt']:,.2f} {e['from_ccy']} -> "
                f"{e['to_amt']:,.2f} {e['to_ccy']}")
    if k == "CASHMOVE":
        d = e.get("decl") or {}
        how = d.get("how") or "undeclared"
        if d.get("value") is not None:
            how += f"={d['value']:,.2f}"
        return (f"{e['amount']:+,.2f} {e['currency']} {how} "
                f"{e.get('desc') or ''}").strip()
    if k == "CASHBAL":
        return f"{e['balance']:,.2f} {e['currency']} balance"
    if k == "CASHOPEN":
        return f"{e['units']:,.2f} {e['currency']} cost {e['cost']:,.2f}"
    if k == "FLOW":
        return f"{e['amount']:+,.2f} {e['currency']} {e.get('desc') or ''}"
    return e.get("text", "")

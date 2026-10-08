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
  disposition: the units and their cost travel;
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
CASHOPEN is the SETTLED balance at the start of Jan 1.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

LABEL = "v2 (opt-in, under audit)"
# Units of the currency a statement balance may differ from the ledger's
# by (IB rounds each line to the cent; a few hundred rows of rounding).
TOL = 1.00
_EPS = 1e-6

RateOf = Callable[[str, str], Optional[float]]


def _p(kind: str, text: str, date: str = "", book: str = "",
       currency: str = "", amount: Optional[float] = None) -> Dict[str, Any]:
    return {"kind": kind, "date": date, "book": book or "",
            "currency": currency, "amount": (round(amount, 2)
                                              if amount is not None
                                              else None),
            "text": text}


def build(native: List[Dict[str, Any]], cash: Dict[str, Any], base: str,
          year: int, rate_of: RateOf, *, country: str,
          carry: Optional[Dict[str, Any]] = None,
          inflow_spot: bool = False) -> Dict[str, Any]:
    """The v2 ledger of `year`. `native`: the taxable accounts' native
    rows, each with `_book` (lib/cash_events.Books.for_file; None when
    no book can be told). `cash`: lib/cash_events.collect's result.
    `carry`: the prior year's close ({"year", "books": {book: {cur:
    {"units", "cost"}}}}). `inflow_spot`: [settings] fx_cash_inflow_cost
    = "spot" (an undeclared inflow costs the day's rate)."""
    from taxjson.bin.taxjson_fx_cash import _cash_legs
    base = base.upper()
    y0, y1 = f"{year}-01-01", f"{year}-12-31"
    prev_end = f"{year - 1}-12-31"
    problems: List[Dict[str, Any]] = [
        _p("input", t) for t in cash.get("problems") or []]
    notes: List[str] = []

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
        native = settle_futures_dicts(list(native), method_for(country))
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
    for row in native:
        sd = str(row.get("date_settle") or row.get("date") or "")[:10]
        td = str(row.get("date") or sd)[:10]
        ps = period_start.get((str(row.get("account") or ""), _re.sub(
            r"#\d+$", "", str(row.get("source") or ""))))
        if ps and td < ps:
            td, sd = ps, max(sd, ps)
            redated += 1
        for cur, amt in _cash_legs(row):
            cur = (cur or "").upper()
            if not cur or cur == base or abs(amt) < _EPS:
                continue
            book = row.get("_book")
            if book is None:
                if in_year(sd) or (td < y0 <= sd):
                    src = str(row.get("source") or "?")
                    unattributed[f"{row.get('account')}/{src}"] = \
                        unattributed.get(f"{row.get('account')}/{src}",
                                         0) + 1
                continue
            op(kind="flow", sd=sd, td=td, time=str(row.get("time") or ""),
               book=book, currency=cur, amount=float(amt),
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
    # matches, else a move of their own.
    moves = [e for e in events if e["kind"] == "CASHMOVE"]
    for ln in [x for x in lines if x["kind"] == "CASHMOVE"]:
        cands = [m for m in moves
                 if m.get("label") == ln.get("label")
                 and m.get("decl") is None
                 and m["currency"] == ln["currency"]
                 and abs(m["amount"] - ln["amount"]) <= 0.01
                 and ln["date"] in (m["date"], m["settle"])
                 and (not ln.get("at") or m.get("book") == ln.get("book"))]
        if cands:
            m = min(cands, key=lambda e: (e["date"], e.get("line_where", "")))
            m["decl"] = ln["decl"]
            m["decl_where"] = ln.get("where")
            ln["matched"] = True
    for ev in events + [x for x in lines if not x.get("matched")]:
        kind = ev["kind"]
        book = ev.get("book")
        if kind in ("FXCONV", "CASHMOVE", "CASHBAL", "FLOW") and not book:
            if ev.get("origin") == "tt" and (
                    kind == "CASHBAL" or in_year(ev["date"])):
                problems.append(_p("book", f"{ev.get('where')}: "
                                   f"{ev.get('book_problem') or 'no book'}",
                                   ev["date"], "", ev.get("currency", "")))
            continue
        if kind == "FXCONV":
            op(kind="conv", sd=ev["settle"], td=ev["date"], book=book,
               from_ccy=ev["from_ccy"].upper(), from_amt=ev["from_amt"],
               to_ccy=ev["to_ccy"].upper(), to_amt=ev["to_amt"],
               value=ev.get("value"), desc=ev.get("desc") or "FXCONV",
               where=ev.get("line_where") or ev.get("where", ""))
        elif kind == "FLOW":
            if ev["currency"].upper() != base:
                op(kind="flow", sd=ev["settle"], td=ev["date"], book=book,
                   currency=ev["currency"].upper(), amount=ev["amount"],
                   desc=ev.get("desc", ""), where=ev.get("where", ""))
        elif kind == "CASHMOVE":
            if ev["currency"].upper() != base:
                op(kind="move", sd=ev["settle"], td=ev["date"], book=book,
                   currency=ev["currency"].upper(), amount=ev["amount"],
                   decl=ev.get("decl"), desc=ev.get("desc", ""),
                   where=(ev.get("line_where") or ev.get("where", "")),
                   decl_where=ev.get("decl_where") or (
                       ev.get("where") if ev.get("origin") == "tt"
                       else ""))
        elif kind == "CASHBAL":
            if ev["currency"].upper() != base:
                checks.append(dict(ev, currency=ev["currency"].upper()))

    # ------------------------------------------------ openings
    opening: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ln in [x for x in lines if x["kind"] == "CASHOPEN"]:
        cur = ln["currency"].upper()
        if cur == base:
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
        for book, curs in (carry.get("books") or {}).items():
            for cur, v in (curs or {}).items():
                k = (book, cur.upper())
                if k in opening:
                    notes.append(f"{opening[k]['source']}: CASHOPEN for "
                                 f"{book} {cur} replaces the prior "
                                 f"year's carried balance")
                    continue
                opening[k] = {"units": float(v.get("units") or 0.0),
                              "cost": float(v.get("cost") or 0.0),
                              "source": f"close of {carry.get('year')}"}
                carried += 1

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
                f"{book.split('/', 1)[0]}, or close {year - 1} with this "
                f"ledger", y0, book, cur, settled))
        else:
            problems.append(_p(
                "opening", f"no opening pool for {book} {cur} — add "
                f"`CASHOPEN {y0} {cur} <settled units> <cost in {base}> "
                f"at={book.split('/', 1)[1]}` (0 0 when it held none) to a "
                f".tt of {book.split('/', 1)[0]}, or close {year - 1} with "
                f"this ledger", y0, book, cur))

    # ------------------------------------------------ the walk
    pool: Dict[str, List[float]] = {}            # cur -> [units, cost]
    held: Dict[Tuple[str, str], float] = {}
    owed: Dict[Tuple[str, str], List[float]] = {}   # [units, basis]
    transit: Dict[str, List[float]] = {}
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
            t = transit.setdefault(cur, [0.0, 0.0])
            t[0] += x
            t[1] += rel
        elif mode == "kept":
            declared.append({"date": o["sd"], "book": o["book"],
                             "currency": cur, "amount": round(-x, 2),
                             "how": "kept",
                             "cost_left": round(rel, 2),
                             "where": o.get("decl_where") or ""})

    def inflow(o, cur: str, x: float, cost: float) -> None:
        k = (o["book"], cur)
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

    def bal(book: str, cur: str) -> float:
        return held.get((book, cur), 0.0) - (owed.get((book, cur))
                                             or [0.0])[0]

    walk = [o for o in ops if in_year(o["sd"])]
    for c in checks:
        if prev_end <= c["date"] <= y1:
            walk.append({"kind": "check", "sd": c["date"], "td": "~",
                         "time": "~", "seq": 0, "book": c["book"],
                         "check": c})
    # Same settle day: an own transfer out before one in; checks last.
    rank = {"check": 9}
    walk.sort(key=lambda o: (o["sd"], o["td"], o.get("time") or "",
                             rank.get(o["kind"], 1 if
                                      (o.get("decl") or {}).get("how")
                                      == "own" and o["amount"] > 0
                                      else 0),
                             o["seq"]))
    for o in walk:
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
                    f"withdrawal the ledger does not see ({row['source']})",
                    c["date"], book, cur, gap))
            continue
        if o["kind"] == "flow":
            cur = o["currency"]
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
            if fc == base:
                inflow(o, tc, o["to_amt"], o["from_amt"])
            elif tc == base:
                outflow(o, fc, o["from_amt"], None, "spend",
                        proceeds=o["to_amt"])
            else:
                rf = rate(fc, o["sd"])
                if rf is None:
                    continue
                v = o.get("value") or o["from_amt"] * rf
                outflow(o, fc, o["from_amt"], rf, "spend", proceeds=v)
                inflow(o, tc, o["to_amt"], v)
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
            t = transit.get(cur) or [0.0, 0.0]
            if t[0] + TOL < amt:
                problems.append(_p(
                    "own", f"{o['book']}: {amt:,.2f} {cur} came in on "
                    f"{o['sd']} as a move between your own accounts, but "
                    f"only {t[0]:,.2f} {cur} had left one of them before "
                    f"it — check the dates, or declare it cost= / spot",
                    o["sd"], o["book"], cur, amt))
                r = rate(cur, o["sd"])
                if r is not None:
                    inflow(o, cur, amt, amt * r)
                continue
            take = min(amt, t[0])
            cost = t[1] * take / t[0] if t[0] > _EPS else 0.0
            t[0] -= take
            t[1] -= cost
            inflow(o, cur, amt, cost * amt / take if take > _EPS else 0.0)
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

    for cur, d in sorted({(c, d) for c, d in unrated}):
        problems.append(_p("rate", f"no {cur} rate on file for {d} (or "
                           f"the 5 days before) — run `taxjson run` to "
                           f"refresh the rates", d, "", cur))
    for cur, t in sorted(transit.items()):
        if t[0] > TOL:
            problems.append(_p(
                "own", f"{t[0]:,.2f} {cur} left an account as a move "
                f"between your own accounts and never arrived in another "
                f"by {y1}", y1, "", cur, t[0]))
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
    net = sum(s["gain"] + s["debt_gain"] for s in per.values())
    status = "not_computed" if problems else "computed"
    return {
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
        "close": {"year": year, "books": close_books},
        "pools_year_end": {c: {"units": round(p[0], 2),
                               "acb": round(p[1], 2)}
                           for c, p in sorted(pool.items())
                           if p[0] > 0.005},
        "notes": notes,
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
        lines += out.fit_table(["CUR", "ACQUIRED", "DISPOSED", "GAIN(LOSS)",
                                "ON DEBT"], body,
                               aligns=["<", ">", ">", ">", ">"], foot=foot,
                               width_=w)
        if verdict is not None:
            lines.append("")
            lines += out.wrap(f"All amounts {base}. {verdict['note']}", w)
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
    for n in doc.get("notes") or []:
        lines.append("")
        lines += out.wrap(out.label("note", w) + n, w, "", "")
    if cash_events:
        lines.append("")
        lines.append("CASH EVENTS READ")
        lines += out.fit_table(
            ["DATE", "BOOK", "KIND", "DETAIL"],
            [[e.get("date", ""), e.get("book") or "-", e["kind"],
              _detail(e)] for e in cash_events],
            aligns=["<", "<", "<", "<"], width_=w, key=0)
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

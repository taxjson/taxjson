"""Journals between two listings of one security in the native books
(backlog TJ-0361).

The native-currency books (`taxjson run`'s work/<account>_raw*.json,
behind reports/<account>_holdings.toml) keep each listing apart: a TSX
line and its US line are two pools there. A journal between them (IB's
InterDepot, RBC's TFR "TRANSFER TO C$", Questrade's BRW journal, a .tt
JOURNAL line) is an out-leg of one listing and an in-leg of the other.
The holdings export used to move the units after the gains, so a trade
of the new listing between the journal and the year end was booked
against nothing (a short), and in an account that keeps its transfers
in the books (`transfers = true`) both legs were booked as a sale and a
purchase at the rows' market value. Here the legs are paired
(evidenced_journals) and written into the native books as two
`listing_journal` TRANSFER rows on the journal's date (book_journals):
the engines move the units with their cost (lib/core
LISTING_JOURNAL_TYPE), restated in the new listing's currency at the
day's rate (the in-leg's `price`). Nothing here touches the tax books:
they join the listings (a TOBASE line) and the legs cancel there.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any, Callable, Dict, List, Optional

# The description of a journal's two legs, numbered per book (row ids
# hash the description: two equal journals stay two).
DESC = "listing journal #{n}: {frm} -> {to}"
_EPS = 1e-9


def identity_find(tmap) -> Callable[[str], str]:
    """The identity class of a symbol (union-find over every GLOBAL,
    TOBASE and JOURNAL pair of the map): two listings of one security
    share a class."""
    parent: Dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in (tmap.glob, tmap.tobase, tmap.journal):
        for a, b in d.items():
            parent[find(a)] = find(b)
    return find


def _day(s) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def evidenced_journals(rows, same_class, note=None) -> List[Dict[str, Any]]:
    """The journals the TRANSFER rows evidence: an out-leg and an in-leg
    of two listings of one identity class (`same_class(a, b)`), netted
    per listing and day first (a flip flipped back the same day moves
    nothing), dated within PAIR_DAYS business days of each other
    (lib/cross_listings: the window one journal's legs post in), the
    closest pair first and an exact quantity before a part of one. A leg
    with no partner in that window is a custody move (a broker switch,
    ATON in or out) or a journal whose other leg the data lacks, and
    evidences nothing: a 2020 departure and a 2026 arrival used to pair
    as one journal (audit 2026-10-24 B12). Legs that would pair but for
    the window are said through `note` (one line each), so a journal the
    broker posted in two far-apart rows can be declared with a .tt
    JOURNAL line.

    Each journal: {date, time, out_date, in_date, out_symbol, in_symbol,
    quantity, out_currency, in_currency} — dated at its earlier leg (the
    units leave one listing and reach the other at once in the books),
    at that leg's earliest time that day."""
    from taxjson.lib.cross_listings import PAIR_DAYS, business_days

    net: Dict[tuple, float] = {}
    first: Dict[tuple, str] = {}
    cur: Dict[tuple, str] = {}
    for t in rows:
        try:
            q = float(t.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        k = (str(t.get("date") or ""), t["symbol"])
        net[k] = net.get(k, 0.0) + q
        tm = str(t.get("time") or "")
        if tm and (k not in first or tm < first[k]):
            first[k] = tm
        if t.get("currency") and k not in cur:
            cur[k] = str(t["currency"])
    legs = [[d, s, q] for (d, s), q in sorted(net.items())
            if abs(q) > _EPS]
    outs = [x for x in legs if x[2] < 0]
    ins = [x for x in legs if x[2] > 0]
    cands = []
    far = []
    for n, o in enumerate(outs):
        for m, i in enumerate(ins):
            if o[1] == i[1] or not same_class(o[1], i[1]):
                continue
            do, di = _day(o[0]), _day(i[0])
            if do is None or di is None:
                continue
            gap = business_days(do, di)
            if gap > PAIR_DAYS:
                far.append((o, i))
                continue
            exact = 0 if abs(-o[2] - i[2]) <= _EPS else 1
            cands.append((exact, gap, o[0], i[0], o[1], i[1], n, m))
    journals = []
    for _exact, _gap, _do, _di, _so, _si, n, m in sorted(cands):
        o, i = outs[n], ins[m]
        move = min(-o[2], i[2])
        if move <= _EPS:
            continue
        lead = min((o[0], o[1]), (i[0], i[1]))
        journals.append({
            "date": lead[0], "time": first.get(lead, ""),
            "out_date": o[0], "in_date": i[0],
            "out_symbol": o[1], "in_symbol": i[1], "quantity": move,
            "out_currency": cur.get((o[0], o[1]), ""),
            "in_currency": cur.get((i[0], i[1]), "")})
        o[2] += move
        i[2] -= move
    for o, i in far:
        if -o[2] <= _EPS or i[2] <= _EPS or note is None:
            continue
        note(f"note: transfer evidence: {o[1]} {o[2]:g} on {o[0]} and "
             f"{i[1]} +{i[2]:g} on {i[0]} are more than {PAIR_DAYS} "
             f"business days apart: not read as one journal between "
             f"the listings (a .tt JOURNAL line declares one).")
    return journals


def _is_leg(t: Dict[str, Any]) -> bool:
    """A TRANSFER row of the book that may be a journal's leg: not a
    move the run already booked (an own-account move, a journal)."""
    from taxjson.lib.core import LISTING_JOURNAL_TYPE, LOT_MOVE_TYPE
    return (t.get("action") == "TRANSFER" and bool(t.get("symbol"))
            and t.get("type") not in (LOT_MOVE_TYPE, LISTING_JOURNAL_TYPE))


def _take(rows: List[Dict[str, Any]], q: float) -> float:
    """Take `q` units (signed like the rows) off the book's rows of one
    listing and day: the rows of that sign, in order, each reduced (its
    amounts with it) or emptied. Returns the units taken."""
    left = abs(q)
    for t in rows:
        if left <= _EPS:
            break
        tq = float(t.get("quantity") or 0)
        if tq * q <= 0:
            continue
        cut = min(abs(tq), left)
        keep = (abs(tq) - cut) / abs(tq)
        t["quantity"] = tq * keep
        for k in ("net_amount", "gross_amount", "proceeds",
                  "market_value"):
            if isinstance(t.get(k), (int, float)):
                t[k] = t[k] * keep
        left -= cut
    return abs(q) - left


def book_journals(txs: List[Dict[str, Any]], evidence: List[Dict[str, Any]],
                  same_class, rate: Callable[[str, str], Optional[float]],
                  note=None, far_note=None) -> List[Dict[str, Any]]:
    """Write the journals between listings into one account's native
    book `txs` (rows as dicts, changed in place): the legs the book's
    own TRANSFER rows hold (an account with `transfers = true`) and the
    ones its transfer evidence holds (`evidence`: the sidecars' rows, a
    .tt JOURNAL line's legs) are paired (evidenced_journals); each
    journal's units are taken off the book's own rows of those legs
    (emptied rows dropped), and two `listing_journal` TRANSFER rows are
    appended: the out-leg of the listing the units left and the in-leg
    of the one they reached, on the journal's date, sharing a
    `journal_pair` id. The in-leg's `price` restates the moved cost in
    its own currency: `rate(currency, day)` (one unit in the base
    currency) of the out-leg's over the in-leg's; 0 when a rate is
    missing (the engines then hold the units at an unknown cost, said
    through `note`). `far_note`: evidenced_journals' note on legs too
    far apart to pair.

    A book that already holds `listing_journal` rows was written here
    before (the native merge is rebuilt only when its inputs change):
    nothing is done. Returns the journals booked."""
    from taxjson.lib.core import LISTING_JOURNAL_TYPE
    if any(t.get("type") == LISTING_JOURNAL_TYPE for t in txs):
        return []
    own = [t for t in txs if _is_leg(t)]
    journals = evidenced_journals(list(own) + list(evidence), same_class,
                                  note=far_note)
    if not journals:
        return []
    acct = next((t.get("account") for t in txs if t.get("account")), "")
    pool_cur: Dict[str, str] = {}
    for t in txs:
        if (t.get("action") in ("BUYSELL", "ASSIGN", "OPENING_BALANCE")
                and t.get("currency") and t.get("symbol")):
            pool_cur.setdefault(t["symbol"], t["currency"])
    by_leg: Dict[tuple, List[Dict[str, Any]]] = {}
    for t in own:
        by_leg.setdefault((str(t.get("date") or ""), t["symbol"]),
                          []).append(t)
    for n, j in enumerate(journals, 1):
        _take(by_leg.get((j["out_date"], j["out_symbol"]), []),
              -j["quantity"])
        _take(by_leg.get((j["in_date"], j["in_symbol"]), []),
              j["quantity"])
        # Each listing's pool is in its trades' currency (a .tt
        # JOURNAL line's legs only guess one); the legs' own else.
        c_out = pool_cur.get(j["out_symbol"]) or j["out_currency"]
        c_in = pool_cur.get(j["in_symbol"]) or j["in_currency"]
        f = 1.0
        if c_out and c_in and c_out != c_in:
            r_out, r_in = rate(c_out, j["date"]), rate(c_in, j["date"])
            f = float(r_out / r_in) if r_out and r_in else 0.0
            if not f and note is not None:
                note(f"{j['out_symbol']} -> {j['in_symbol']} on "
                     f"{j['date']}: no {c_out}/{c_in} rate on file: the "
                     f"journaled units' cost is unknown in the native "
                     f"holdings view")
        desc = DESC.format(n=n, frm=j["out_symbol"], to=j["in_symbol"])
        base = {"action": "TRANSFER", "type": LISTING_JOURNAL_TYPE,
                "date": j["date"], "time": j["time"] or "09:30:00",
                "account": acct, "journal_pair": f"listing-journal-{n}",
                "description": desc, "net_amount": 0.0}
        # In the book's order: after the rows of an earlier or equal
        # moment (the merge is sorted by date and time).
        when = (base["date"], base["time"])
        at = len(txs)
        while at and (str(txs[at - 1].get("date") or ""),
                      str(txs[at - 1].get("time") or "")) > when:
            at -= 1
        txs[at:at] = [
            dict(base, symbol=j["out_symbol"], quantity=-j["quantity"],
                 currency=c_out, price=0.0),
            dict(base, symbol=j["in_symbol"], quantity=j["quantity"],
                 currency=c_in, price=f)]
    txs[:] = [t for t in txs
              if not (_is_leg(t)
                      and abs(float(t.get("quantity") or 0)) <= _EPS)]
    return journals

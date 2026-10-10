"""Shares that arrive in a TAXABLE account by transfer from outside your
books (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).

A taxable account's TRANSFER rows are custody evidence kept out of the
books (the parse stage's transfer sidecar, `taxjson transfers`): the
cost of a position comes from its purchase. Most transfer rows are your
own shuffles — a broker's internal account moves, a move between two
of your accounts, a listing journal — whose out and in legs cancel. The
rest are shares that came from OUTSIDE the books (another broker whose
history you have not imported): those have no purchase in the books,
so a later sale reads as a short.

This module finds them and decides what the run does with each:

* the transfer rows of all your taxable (non-crypto, `transfers =
  false`) accounts are pooled per security (after ticker.map's undated
  renames); every out leg cancels in legs of the same security — the
  same quantity and the closest date first — and what is left of an in
  leg arrived from outside. A journal inside one account (a broker's
  journal pair, a .tt JOURNAL line) cancels within its own pair first
  and never against another account's leg (own_journal_legs);
* such an arrival is COVERED when the receiving account's .tt files
  already hold purchases of that security, dated on or before the
  arrival, for its quantity (the documented fix: the original purchase
  as a .tt line), or when a .tt OPENING cost=unknown line opens the security for
  that account (you declared its cost unknown, to be reported by hand)
  — nothing more is said or booked;
* otherwise, when the broker STATES the book value on the row
  (Questrade "TRANSFER BOOK VALUE", RBC "BOOK VALUE"), it becomes the
  cost of the incoming shares: one acquisition on the arrival date at
  that value (TRANSFER_BOOK_VALUE_TYPE), said as ATTENTION;
* otherwise (no stated book value — IB's VALUE is the market value on
  the transfer day, never a cost) the arrival has no cost: said as
  ATTENTION, and counted by the run's closing summary.

Pure functions over plain dicts: the run feeds the sidecar rows and the
converted .tt rows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple)

# The type of the acquisition booked for an arrival at the broker's stated
# book value. The engines do not treat it as a purchase in a loss window
# (its date is the arrival, not the acquisition) — core.py.
from taxjson.lib.core import TRANSFER_BOOK_VALUE_TYPE  # noqa: E402

_EPS = 1e-6


@dataclass
class Arrival:
    """The part of one transfer-in row that came from outside the books."""
    account: str
    symbol: str                 # the row's own symbol (the broker's)
    key: str                    # the security after ticker.map renames
    date: str
    time: str
    quantity: float             # arrived from outside (<= the row's qty)
    currency: str
    book_value: Optional[float]  # the broker's stated book value for
    #                              `quantity` (pro rata); None: not stated
    broker: str
    source: str                 # the input file the row came from
    description: str
    covered: bool = False       # the account's .tt lines cover it, or
    #                             a .tt OPENING cost=unknown line opens the pair
    covered_by: str = ""        # "tt" | "missing_history"
    row: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def status(self) -> str:
        if self.covered:
            return ("missing_history" if self.covered_by == "missing_history"
                    else "covered")
        return "book_value" if self.book_value else "no_cost"


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def stated_book_value(row: Dict[str, Any]) -> Optional[float]:
    """The book value the broker states on a transfer row (the parser's
    `book_value` evidence), else None. A VALUE / net_amount that is a
    market value (IB) is never one."""
    try:
        bv = float(row.get("book_value") or 0.0)
    except (TypeError, ValueError):
        return None
    return bv if bv > 0 else None


def sidecar_rows(cache: Path, accounts: Sequence[str], *,
                 declared: bool = False
                 ) -> List[Tuple[str, str, Dict[str, Any]]]:
    """(account, broker, TRANSFER row) of each account's transfer
    sidecars in work/ (work/<acct>_<broker>_transfers.json). The legs of
    the account's .tt JOURNAL lines (lib/dated_events.SIDECAR_BROKER: a
    move inside the account) are left out unless `declared`: a caller
    that reads them settles every journal inside its account first
    (own_journal_legs), so a journal's legs never pair with another
    account's transfer (pre-release review H1)."""
    from taxjson.lib.dated_events import SIDECAR_BROKER
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    names = sorted(accounts, key=len, reverse=True)
    for n in accounts:
        others = [o for o in names if o != n and o.startswith(f"{n}_")]
        for sc in sorted(cache.glob(f"{n}_*_transfers.json")):
            if any(sc.name.startswith(f"{o}_") for o in others):
                continue        # a longer-named sibling's sidecar
            try:
                doc = json.loads(sc.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            md = doc.get("metadata") if isinstance(doc, dict) else None
            if (not isinstance(md, dict)
                    or md.get("kind") != "transfer_sidecar"
                    or md.get("account") != n):
                continue
            broker = str(md.get("brokerage") or "")
            if broker == SIDECAR_BROKER and not declared:
                continue        # a .tt JOURNAL's legs: inside the account
            for t in doc.get("transactions") or []:
                if (isinstance(t, dict) and t.get("action") == "TRANSFER"
                        and t.get("symbol")):
                    try:
                        q = float(t.get("quantity") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if abs(q) > _EPS:
                        out.append((n, broker, t))
    return out


def own_journal_legs(rows: Sequence[Tuple[str, str, Dict[str, Any]]]
                     ) -> Tuple[set, set]:
    """(settled, orphans): the indices of `rows` ((account, broker, row))
    that are the legs of a journal INSIDE one account — never a move
    between two of your accounts, an arrival from outside the books, or
    an in-kind move (pre-release review H1):

    * the legs a journal's own pair id marks (missing_history.
      journal_leg_key: Questrade's BRW `journal_pair`, RBC's J~
      reference, a .tt JOURNAL line's pair id) cancel within their
      account and pair: a group that is one journal (missing_history.
      ref_group_journal: its out-legs on one listing, its in-legs on
      one, the same units, within PAIR_DAYS business days) is settled,
      every leg of it;
    * a leg left over from such a pair (a .tt JOURNAL line whose other
      leg the broker's rows hold: lib/dated_events.settle_journals)
      settles with the account's leg it stands beside: the opposite
      direction, the same quantity, within cross_listings.PAIR_DAYS
      business days, another symbol first, the closest date first;
    * a journal leg still left (its pair incomplete) is an orphan: it
      may cancel only legs of its own account."""
    from taxjson.lib.cross_listings import PAIR_DAYS, business_days
    from taxjson.lib.missing_history import journal_leg_key
    groups: Dict[Tuple[Any, ...], List[int]] = {}
    for n, (acct, broker, t) in enumerate(rows):
        k = journal_leg_key(t, broker=broker)
        if k is not None:
            groups.setdefault((acct,) + k, []).append(n)

    def _q(n: int) -> float:
        try:
            return float(rows[n][2].get("quantity") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    from taxjson.lib.missing_history import ref_group_journal
    settled: set = set()
    left: List[int] = []
    for _k, ns in sorted(groups.items()):
        # One rule for a reference group (missing_history.
        # ref_group_journal, as the cross-listing join and the
        # missing-history walk read it).
        if ref_group_journal(
                (str(rows[n][2].get("symbol") or ""), _q(n),
                 str(rows[n][2].get("date") or "")) for n in ns):
            settled.update(ns)
        else:
            left.extend(ns)
    orphans: set = set()
    for n in sorted(left, key=lambda x: str(rows[x][2].get("date") or "")):
        if n in settled:
            continue
        acct, _b, t = rows[n]
        q, dn = _q(n), _d(t.get("date"))
        best = None
        for m, (a2, _b2, u) in enumerate(rows):
            if m == n or m in settled or a2 != acct:
                continue
            qm, dm = _q(m), _d(u.get("date"))
            if (qm * q >= 0 or abs(abs(qm) - abs(q))
                    > max(_EPS, 1e-6 * abs(q)) or dn is None or dm is None):
                continue
            gap = business_days(dn, dm)
            if gap > PAIR_DAYS:
                continue
            rank = (str(u.get("symbol") or "").upper()
                    == str(t.get("symbol") or "").upper(), gap, m)
            if best is None or rank < best[0]:
                best = (rank, m)
        if best is not None:
            settled.update((n, best[1]))
        else:
            orphans.add(n)
    return settled, orphans - settled


def movable_rows(rows: Sequence[Tuple[str, str, Dict[str, Any]]]
                 ) -> List[Tuple[str, str, Dict[str, Any]]]:
    """`rows` less every leg of a journal inside one account (settled or
    orphan: own_journal_legs) — the transfer rows that may pair with
    another account's (a move of your own, an in-kind move)."""
    settled, orphans = own_journal_legs(rows)
    return [r for n, r in enumerate(rows)
            if n not in settled and n not in orphans]


def arrivals(rows: Iterable[Tuple[str, str, Dict[str, Any]]], *,
             key: Optional[Callable[[str], str]] = None) -> List[Arrival]:
    """The transfer-in quantities that came from outside the books.

    Per security (`key`: the symbol after ticker.map renames; default
    the upper-cased symbol), every out leg cancels in legs — same
    quantity first, then the closest date — across ALL the given
    accounts (a move between two of your accounts is not an arrival) —
    except the legs of a journal inside one account (own_journal_legs),
    which cancel within their own pair. What is left of each in leg is
    an Arrival."""
    keyf = key or (lambda s: str(s or "").upper())
    rows = list(rows)
    # A journal inside one account (its legs' pair id, a .tt JOURNAL
    # line) cancels within its pair: never against another account's leg
    # (pre-release review H1). An orphan journal leg (its pair
    # incomplete) cancels its own account's legs only.
    settled, orphans = own_journal_legs(rows)
    legs = []    # [account, broker, row, key, quantity left, journal leg]
    for n, (acct, broker, t) in enumerate(rows):
        if n in settled:
            continue
        legs.append([acct, broker, t, keyf(str(t.get("symbol") or "")),
                     float(t.get("quantity") or 0.0), n in orphans])
    ins = [g for g in legs if g[4] > 0]
    outs = [g for g in legs if g[4] < 0]
    for g in outs:
        g[4] = -g[4]

    def _cancel(pairs):
        for *_rank, i, j in sorted(pairs):
            take = min(ins[i][4], outs[j][4])
            if take <= _EPS:
                continue
            ins[i][4] -= take
            outs[j][4] -= take

    def _gap(a, b):
        da, db = _d(a[2].get("date")), _d(b[2].get("date"))
        return abs((da - db).days) if da and db else 10 ** 6
    # 1. The same security: same quantity first, then the closest date.
    _cancel([(abs(inn[4] - o[4]) > _EPS, _gap(inn, o),
              str(inn[2].get("date")), i, j)
             for i, inn in enumerate(ins) for j, o in enumerate(outs)
             if inn[3] == o[3]
             and (inn[0] == o[0] or not (inn[5] or o[5]))])
    # 2. A listing journal (the same units moved between two listings of
    #    one security, e.g. a US-dollar and a Canadian-dollar line, with
    #    no ticker.map rule joining them): an out leg of another symbol
    #    in the same account, the same quantity, within 3 days.
    _cancel([(_gap(inn, o), str(inn[2].get("date")), i, j)
             for i, inn in enumerate(ins) for j, o in enumerate(outs)
             if inn[3] != o[3] and inn[0] == o[0]
             and abs(inn[4] - o[4]) <= _EPS and _gap(inn, o) <= 3])
    out: List[Arrival] = []
    for a, b, t, k, left, _j in ins:
        if left <= _EPS:
            continue
        q = float(t.get("quantity") or 0.0)
        bv = stated_book_value(t)
        out.append(Arrival(
            account=a, symbol=str(t.get("symbol") or ""), key=k,
            date=str(t.get("date") or "")[:10],
            time=str(t.get("time") or "09:30:00"),
            quantity=round(left, 8),
            currency=str(t.get("currency") or ""),
            book_value=(round(bv * left / q, 2) if bv and q else None),
            broker=b, source=str(t.get("source") or ""),
            description=str(t.get("description") or ""), row=t))
    out.sort(key=lambda a: (a.account, a.key, a.date))
    return out


def mark_covered(found: List[Arrival],
                 tt_rows: Dict[str, List[Dict[str, Any]]], *,
                 key: Optional[Callable[[str], str]] = None) -> None:
    """Set `covered` on each arrival the receiving account's .tt rows
    already account for: the .tt acquisitions of the security dated on
    or before the arrival, less .tt sales before it, cover the
    arrivals in date order (`tt_rows`: account -> the converted .tt
    rows)."""
    from taxjson.lib.core import is_opening_row
    keyf = key or (lambda s: str(s or "").upper())
    for acct in sorted({a.account for a in found}):
        held: Dict[str, List[Tuple[str, float]]] = {}
        # An opening balance (CA-OPEN-03) dated on or after an arrival
        # already holds those shares: the snapshot replaces the history
        # before it, the arrival included.
        snap: Dict[str, str] = {}
        for t in tt_rows.get(acct) or []:
            if is_opening_row(t):
                k = keyf(str(t.get("symbol") or ""))
                snap[k] = max(snap.get(k, ""), str(t.get("date") or "")[:10])
        for a in found:
            if (a.account == acct and not a.covered
                    and a.date <= snap.get(a.key, "")):
                a.covered, a.covered_by = True, "tt"
        for t in tt_rows.get(acct) or []:
            if t.get("action") in ("SPLIT", "DIVIDEND", "DIVIDEND_IN_LIEU",
                                   "ADJUST", "INTEREST", "FEE"):
                continue
            if is_opening_row(t):
                continue        # the snapshot's own shares (above)
            try:
                q = float(t.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(q) > _EPS:
                held.setdefault(keyf(str(t.get("symbol") or "")), []).append(
                    (str(t.get("date") or "")[:10], q))
        used: Dict[str, float] = {}
        for a in sorted((x for x in found if x.account == acct
                         and not x.covered),
                        key=lambda x: (x.date, x.time)):
            avail = sum(q for d, q in held.get(a.key, []) if d <= a.date)
            if avail - used.get(a.key, 0.0) >= a.quantity - _EPS:
                a.covered, a.covered_by = True, "tt"
                used[a.key] = used.get(a.key, 0.0) + a.quantity


def mark_missing_history(found: List[Arrival],
                         pairs: Iterable[Tuple[str, str]]) -> None:
    """Cover each uncovered arrival whose (security, account)
    a .tt OPENING cost=unknown line opens: the user declared that position's cost
    unknown (its sales are reported by hand), and a booking at the
    broker's book value would silently replace that declaration."""
    listed = {(str(s).upper(), str(a)) for s, a in pairs}
    for a in found:
        if a.covered:
            continue
        if ((a.key.upper(), a.account) in listed
                or (a.symbol.upper(), a.account) in listed):
            a.covered, a.covered_by = True, "missing_history"


def booked_rows(found: Iterable[Arrival]) -> List[Dict[str, Any]]:
    """The acquisitions the books take for uncovered arrivals with a
    stated book value: one BUYSELL each, on the arrival date, at the
    book value (TRANSFER_BOOK_VALUE_TYPE)."""
    out = []
    for a in found:
        if a.covered or not a.book_value:
            continue
        t = a.row
        row = {
            "action": "BUYSELL",
            "type": TRANSFER_BOOK_VALUE_TYPE,
            "date": a.date,
            "time": a.time,
            "date_settle": str(t.get("date_settle") or a.date)[:10],
            "symbol": a.symbol,
            "quantity": a.quantity,
            "currency": a.currency,
            "price": round(a.book_value / a.quantity, 8),
            "net_amount": a.book_value,
            "gross_amount": a.book_value,
            "fee": 0.0,
            "account": a.account,
            # Never the broker's own text: an RBC transfer description
            # names the other account's number.
            "description": (f"TRANSFER-IN AT THE BROKER'S BOOK VALUE "
                            f"({a.broker or 'broker'})"),
        }
        if a.source:
            row["source"] = a.source
        for k in ("multiplier", "contract_size_basis", "source_account"):
            if t.get(k):
                row[k] = t[k]
        out.append(row)
    return out


def attention_lines(found: Iterable[Arrival], country: str) -> List[str]:
    """One ATTENTION line per account and kind: the arrivals booked at
    the broker's book value, and those with no cost."""
    from taxjson.lib.country import COST_TERM, is_canada
    cost = COST_TERM.get(country, "cost")
    lines: List[str] = []
    by: Dict[Tuple[str, str], List[Arrival]] = {}
    for a in found:
        if not a.covered:
            by.setdefault((a.account, a.status), []).append(a)

    def _list(xs):
        shown = "; ".join(f"{x.quantity:g} {x.symbol} ({x.date})"
                          for x in xs[:6])
        return shown + ("; ..." if len(xs) > 6 else "")
    override = ("To use your own figure instead, add the original "
                "purchase as a .tt BUYSELL line dated on or before the "
                "transfer: the book value is then no longer used and this "
                "line stops.")
    for (acct, status), xs in sorted(by.items()):
        if status == "book_value":
            from taxjson.lib.brokerages.detect import DISPLAY_NAMES
            srcs = sorted({DISPLAY_NAMES.get(x.broker, x.broker)
                           or "the broker" for x in xs})
            hp = ("" if is_canada(country) else
                  " The holding period starts on the arrival date, not "
                  "the original purchase: a sale within a year of it is "
                  "short-term unless you enter the original lot(s) with "
                  "their real purchase dates.")
            lines.append(
                f"transfer-in: {acct}: {len(xs)} transfer-in(s) from "
                f"outside your books booked at the {cost} the broker "
                f"states on the row ({', '.join(srcs)}: {_list(xs)}). "
                f"A broker's book value is its own record, not always "
                f"your {cost}: check it.{hp} {override}")
        else:
            lines.append(
                f"transfer-in: {acct}: {len(xs)} transfer-in(s) from "
                f"outside your books have NO cost in the books "
                f"({_list(xs)}): the row states no book value (a VALUE "
                f"column is the market value, never your {cost}), so the "
                f"shares are kept out and a later sale reads as a short. "
                f"Add the original purchase as a .tt BUYSELL line (date "
                f"and {cost} from the sending broker) dated on or before "
                f"the transfer; this line then stops "
                f"(docs/getting-started.md, step 5c).")
    return lines

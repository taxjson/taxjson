"""Shares that arrive in a TAXABLE account by transfer from outside your
books (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).

A taxable account's TRANSFER rows are custody evidence kept out of the
books (the parse stage's transfer sidecar, `tjs transfers`): the
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
  same quantity and the closest date first, within OWN_MOVE_DAYS days
  (the two legs of one move; a departure years after an arrival is not
  its source: audit 2026-10-24 B50); then a slower move of your own
  (slow_pairs): an out leg and an in leg of the same security and
  quantity in two of your accounts, the in leg more than OWN_MOVE_DAYS
  and at most SLOW_MOVE_DAYS days after, when neither leg has another
  candidate (an ATON move often takes one to three weeks: such legs were
  booked as an outside purchase while the sending account kept the
  shares, audit 2026-10-25 diff-engine#4; the run says each as Info) —
  and what is left of an in leg arrived from outside (an earlier
  transfer-out nothing paired is named on its ATTENTION line,
  Arrival.departures). A .tt NOTOWN line in the receiving account's
  folder undoes one such pairing, a slow one or an exact one (the same
  quantity within the window), and never makes one: its in leg is then
  an arrival and its out leg an ordinary transfer-out (NotOwn; backlog
  TJ-0075). A journal inside one account (a broker's
  journal pair, a .tt JOURNAL line) cancels within its own pair first
  and never against another account's leg (own_journal_legs); a
  listing journal with no pair id and no ticker.map rule cancels only
  two listings of one root (ZZQ.TO / ZZQ.US; cross_listings.
  listing_root), never two securities that happen to share a quantity
  and a day (B03);
* such an arrival is COVERED when the receiving account's .tt files
  already hold purchases of that security, dated on or before the
  arrival, for its whole quantity (the documented fix: the original
  purchase as a .tt line; the .tt SPLIT lines before the arrival are
  replayed, a .tt sale before it — an earlier day, or earlier on the
  day by its time — consumes; B04, B42), or when a .tt OPENING
  cost=unknown line opens the security for that account (you declared
  its cost unknown, to be reported by hand) — nothing more is said or
  booked. Purchases for PART of the quantity cover nothing: the
  arrival is said as ATTENTION and NOT booked (`partly_covered`) until
  the rest is declared — booking it whole would hold the covered units
  twice, booking a pro-rata rest would guess which units are which
  (B41);
* otherwise, when the broker STATES the book value on the row
  (Questrade "TRANSFER BOOK VALUE", RBC "BOOK VALUE"), it becomes the
  cost of the incoming shares: one acquisition on the arrival date at
  that value (TRANSFER_BOOK_VALUE_TYPE), said as ATTENTION;
* otherwise (no stated book value — IB's VALUE is the market value on
  the transfer day, never a cost) the arrival has no cost: said as
  ATTENTION, and counted by the run's closing summary. A stated book
  value of 0 is read as no cost too, and said as such (a broker's 0 is
  usually its placeholder for a cost it was not given; a real nil cost
  is declared as a .tt purchase at 0): never booked as a $0 lot (B40).

Pure functions over plain dicts: the run feeds the sidecar rows and the
converted .tt rows.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple)

# The type of the acquisition booked for an arrival at the broker's stated
# book value. The engines do not treat it as a purchase in a loss window
# (its date is the arrival, not the acquisition) — core.py.
from taxjson.lib.core import TRANSFER_BOOK_VALUE_TYPE  # noqa: E402
# The most days between the two legs of one move of your own: the window
# the in-kind pairing allows a transfer between two brokers
# (lib/in_kind.PAIR_DAYS; the US lot carry reads this pairing, backlog
# TJ-0269). An out leg further from an in leg
# than this is a departure (or an arrival) of its own, never the other's
# source (audit 2026-10-24 B50: a 2026 departure cancelled a 2020
# arrival).
from taxjson.lib.in_kind import PAIR_DAYS as OWN_MOVE_DAYS  # noqa: E402
# The most days a move of your own between two of your taxable accounts
# may take when nothing else could be either leg's partner (slow_pairs):
# an ATON / ACATS move between two brokers often posts its in leg one to
# three weeks after the out leg, and a delayed one later still. Legs
# further apart, or an in leg before its out leg, are an arrival and a
# departure of their own (audit 2026-10-25 diff-engine#4; B50).
SLOW_MOVE_DAYS = 45

_EPS = 1e-6
# A listing journal with no pair id and no ticker.map rule: its two legs
# are in one account, the same quantity, two listings of one root, this
# many days apart at most.
LISTING_JOURNAL_DAYS = 3


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
    #                              `quantity` (pro rata); None: not stated;
    #                              0.0: the row states 0 (zero_book_value)
    broker: str
    source: str                 # the input file the row came from
    description: str
    covered: bool = False       # the account's .tt lines cover it, or
    #                             a .tt OPENING cost=unknown line opens the pair
    covered_by: str = ""        # "tt" | "missing_history"
    covered_units: float = 0.0  # when not covered: the units the .tt
    #                             lines do account for (0 < u < quantity)
    # The transfer-outs of the security nothing paired, from any of the
    # accounts, dated on or before the arrival and at most
    # SLOW_MOVE_DAYS earlier: (account, date, units left). One of them
    # may be this arrival's source (its legs are ambiguous, or its
    # quantity differs), so attention_lines names them: the sending
    # account's books still hold those shares (audit 2026-10-25
    # diff-engine#4).
    departures: List[Tuple[str, str, float]] = field(default_factory=list)
    row: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def zero_book_value(self) -> bool:
        """The row states a book value of 0: read as no cost (never a $0
        lot), said apart from a row that states none."""
        return self.book_value is not None and abs(self.book_value) <= _EPS

    @property
    def status(self) -> str:
        """covered | missing_history | partly_covered | book_value |
        no_cost: what the books do with the arrival (booked_rows books
        `book_value` only)."""
        if self.covered:
            return ("missing_history" if self.covered_by == "missing_history"
                    else "covered")
        if self.covered_units > _EPS:
            return "partly_covered"
        return "book_value" if self.book_value else "no_cost"


@dataclass
class OwnMove:
    """Units of a security the pairing (arrivals) cancelled between two
    of your accounts: an out leg in one against an in leg in another,
    within the window or as a slower move. A Canadian project's books
    carry each as a lot move in both accounts' books, so every
    per-account view sees the receiving account hold the shares; the
    ACB stays one pool (CA-ACB-OWN-MOVE, backlog TJ-0093). A US
    project's books carry the sender's lots along the same pairs
    (US-BASIS-05): one pairing decides both (backlog TJ-0269). A leg
    the pairing leaves stays the transfer in or out it is."""
    key: str                    # the security after ticker.map renames
    quantity: float             # the units cancelled
    out_account: str
    out_date: str
    in_account: str
    in_date: str
    out_symbol: str = ""        # each row's own symbol and currency
    in_symbol: str = ""
    out_currency: str = ""
    in_currency: str = ""
    # The days between the legs when the pairing took them as a slower
    # move (slow_pairs); 0 within the window.
    slow: int = 0
    # The units of the out row and of the in row no pairing took, once
    # the pairing is done: a move whose rows' quantities differ (the
    # rest of the out row a transfer-out of its own, of the in row an
    # arrival from outside the books).
    out_left: float = 0.0
    in_left: float = 0.0


@dataclass
class SlowMove:
    """A move of your own whose two legs post more than OWN_MOVE_DAYS
    and at most SLOW_MOVE_DAYS days apart, paired because neither leg
    has another candidate (slow_pairs): nothing is booked for its in
    leg, and the run says it as Info (audit 2026-10-25
    diff-engine#4)."""
    key: str                    # the security after ticker.map renames
    symbol: str                 # the in row's own symbol
    quantity: float
    out_account: str
    out_date: str
    in_account: str
    in_date: str

    @property
    def days(self) -> int:
        do, di = _d(self.out_date), _d(self.in_date)
        return (di - do).days if do and di else 0

    def text(self) -> str:
        """`ZZQ.TO 100 a -> b (out 2024-03-01, in 2024-03-16: 15 days
        apart, nothing else pairs)`, as the run's own-account move line
        says a move."""
        when = slow_dates(self.out_date, self.in_date, self.days)
        return (f"{self.symbol} {self.quantity:g} {self.out_account} -> "
                f"{self.in_account} ({when})")

    def not_own_line(self) -> str:
        """The .tt line that undoes this pairing (not_own_text)."""
        return not_own_text(self.in_date, self.symbol, self.quantity,
                            self.out_account, self.out_date)


# A .tt NOTOWN line (backlog TJ-0075): in the folder of the taxable
# account the shares arrived in, it says that one transfer-in and the
# transfer-out the run paired it with are NOT one move of your own.
NOTOWN_KEYWORD = "NOTOWN"
NOTOWN_FORM = "NOTOWN <date> <symbol> <qty> <from-account> <from-date>"


@dataclass(eq=False)
class NotOwn:
    """One .tt NOTOWN line: the transfer-in of `quantity` `symbol` on
    `date` in `account` (the line's folder) and the transfer-out of the
    same units from `from_account` on `from_date` are not one move of
    your own. It undoes that pairing when arrivals made it — an exact
    one (the two legs of the same units within OWN_MOVE_DAYS) or a slow
    one (slow_pairs) — and never makes one: the in leg is then an
    arrival from outside the books, the out leg an ordinary
    transfer-out. `matched` ("exact" | "slow") and `legs` (the two
    legs' (account, symbol, date, units) as the transfer rows give them,
    units signed) are set by arrivals when the line undoes its
    pairing."""
    account: str
    date: str
    symbol: str
    quantity: float
    from_account: str
    from_date: str
    where: str = ""             # "inputs/<acct>/<file>:<line>"
    line: str = ""              # as written, stripped
    matched: str = ""
    legs: Tuple[Tuple[str, str, str, float], ...] = ()

    def names(self, out_leg: Sequence[Any], in_leg: Sequence[Any],
              keyf: Callable[[str], str]) -> bool:
        """The line names these two legs (arrivals' [account, broker,
        row, key, ...] lists): the in leg in this line's account on its
        date, the out leg in `from_account` on `from_date`, both of the
        line's security (after ticker.map renames, or as the row spells
        it) and its units."""
        def _is(leg, acct: str, day: str) -> bool:
            t = leg[2]
            try:
                q = abs(float(t.get("quantity") or 0.0))
            except (TypeError, ValueError):
                return False
            sym = self.symbol.upper()
            return (leg[0] == acct and str(t.get("date") or "")[:10] == day
                    and (leg[3] == keyf(sym)
                         or str(t.get("symbol") or "").upper() == sym)
                    and abs(q - self.quantity)
                    <= max(_EPS, 1e-6 * self.quantity))
        return (_is(in_leg, self.account, self.date)
                and _is(out_leg, self.from_account, self.from_date))


def _qty_text(q: float) -> str:
    """Units as a .tt line writes them: `100`, `12.5` (never `1e+06`)."""
    s = f"{abs(float(q)):.8f}".rstrip("0").rstrip(".")
    return s or "0"


def not_own_text(in_date: str, symbol: str, quantity: float,
                 out_account: str, out_date: str) -> str:
    """`NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01`: the line that
    undoes the pairing of that transfer-in with that transfer-out."""
    return (f"{NOTOWN_KEYWORD} {str(in_date)[:10]} {symbol} "
            f"{_qty_text(quantity)} {out_account} {str(out_date)[:10]}")


def parse_not_own_line(line: str, source: str = ""
                       ) -> Optional[Dict[str, Any]]:
    """`NOTOWN <date> <symbol> <qty> <from-account> <from-date>` ->
    {date, symbol, quantity, from_account, from_date, line}, or None when
    the line is not a NOTOWN line. Raises ValueError naming the form on
    a malformed one (`#` starts a comment)."""
    import re
    from taxjson.bin.taxjson_convert_tt import _is_date, strip_tt_comment
    where = f"{source}: " if source else ""
    parts = strip_tt_comment(line).split()
    if not parts or parts[0] != NOTOWN_KEYWORD:
        return None
    shown = line.strip()
    toks = parts[1:]
    if len(toks) >= 2 and not _is_date(toks[0]) and _is_date(toks[1]):
        raise ValueError(
            f"{where}malformed {NOTOWN_KEYWORD} line — a .tt line is date "
            f"first (expected `{NOTOWN_FORM}`): {shown!r}")
    if len(toks) != 5:
        raise ValueError(
            f"{where}malformed {NOTOWN_KEYWORD} line — expected "
            f"`{NOTOWN_FORM}` (the transfer-in's date, symbol and units, "
            f"then the account and date of the transfer-out it was paired "
            f"with): {shown!r}")
    date, symbol, qty, frm, fdate = toks
    for d, what in ((date, "date"), (fdate, "from-date")):
        if not _is_date(d):
            raise ValueError(
                f"{where}{NOTOWN_KEYWORD} {what} {d!r} is not a valid "
                f"YYYY-MM-DD date (expected `{NOTOWN_FORM}`): {shown!r}")
    from taxjson.lib.brokerages.base import parse_strict_number
    try:
        # Strict, like a .tt row's quantity: a decimal comma is refused.
        if not re.fullmatch(r"[0-9.,eE+]+", qty):
            raise ValueError(qty)       # no sign, `$` or parentheses
        q = parse_strict_number(qty, field="quantity")
    except ValueError:
        q = None
    if q is None or not math.isfinite(q) or q <= 0:
        raise ValueError(
            f"{where}{NOTOWN_KEYWORD} quantity {qty!r} is not the number of "
            f"units that arrived, positive (expected `{NOTOWN_FORM}`): "
            f"{shown!r}")
    return {"date": date, "symbol": symbol.upper(), "quantity": float(q),
            "from_account": frm, "from_date": fdate, "line": shown}


def not_own_account_problem(n: NotOwn, accounts: Dict[str, Any]) -> str:
    """Why NOTOWN line `n` can undo no pairing, by its accounts alone
    (`accounts`: taxjson.toml's [accounts]); "" when it may. The run
    pairs the transfer rows of taxable share accounts that keep them out
    of their books (`transfers = false`)."""
    acfg = (accounts or {}).get(n.account)
    if not isinstance(acfg, dict) or acfg.get("type") != "taxable":
        return (f"account {n.account} is not a taxable account: a move to "
                f"or from a registered account is an in-kind move "
                f"(INKIND), never a pairing this line undoes")
    if acfg.get("crypto"):
        return (f"account {n.account} is a crypto account: sends between "
                f"your crypto accounts are paired by `tjs "
                f"crypto-sends` (--unpair undoes one)")
    if acfg.get("transfers"):
        return (f"account {n.account} keeps its transfer rows in the "
                f"books (transfers = true): nothing pairs them")
    if n.from_account not in (accounts or {}):
        return (f"it names account {n.from_account!r}, which taxjson.toml "
                f"does not declare (the transfer-out's account, as "
                f"`tjs transfers` lists it)")
    return ""


def read_not_own(root: Path, accounts: Iterable[str]) -> List[NotOwn]:
    """Every NOTOWN line of the accounts' .tt files (inputs/<account>/,
    the shared folder with `inputs_dir`), in account and file order. A
    malformed line is skipped here: the .tt conversion refuses it, naming
    it (taxjson_convert_tt.tt_to_json)."""
    from taxjson.bin.taxjson_convert_tt import tt_lines
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.dated_events import _where, tt_files
    out: List[NotOwn] = []
    for acct in sorted(accounts or ()):
        for tt in tt_files(_PL.inputs_dir(Path(root)) / acct):
            try:
                text = read_text_utf8(tt)
            except (OSError, ValueError):
                continue
            for n, raw in enumerate(tt_lines(text), 1):
                if NOTOWN_KEYWORD not in raw:
                    continue
                try:
                    p = parse_not_own_line(raw)
                except ValueError:
                    continue
                if p is not None:
                    out.append(NotOwn(
                        account=acct, date=p["date"], symbol=p["symbol"],
                        quantity=p["quantity"],
                        from_account=p["from_account"],
                        from_date=p["from_date"], where=_where(acct, tt, n),
                        line=p["line"]))
    return out


def slow_dates(out_date: str, in_date: str, days: int) -> str:
    """How a move slower than the window names its legs:
    `out 2024-03-01, in 2024-03-16: 15 days apart, nothing else
    pairs`."""
    return (f"out {str(out_date)[:10]}, in {str(in_date)[:10]}: {days} "
            f"days apart, nothing else pairs")


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def leg_ident(account: str, t: Dict[str, Any]) -> Tuple[str, str, str, float]:
    """A transfer row's identity: (account, symbol as the row spells it,
    date, units signed) — as taxjson_run._leg_ident and lib/in_kind's
    Leg.ident name a leg."""
    try:
        q = round(float(t.get("quantity") or 0.0), 8)
    except (TypeError, ValueError):
        q = 0.0
    return (account, str(t.get("symbol") or ""),
            str(t.get("date") or "")[:10], q)


def slow_pairs(outs: Sequence[Tuple[str, str, Any, float, bool]],
               ins: Sequence[Tuple[str, str, Any, float, bool]]
               ) -> List[Tuple[int, int]]:
    """(index in `outs`, index in `ins`): the two legs of a move of your
    own between two of your accounts that post more than OWN_MOVE_DAYS
    and at most SLOW_MOVE_DAYS days apart (audit 2026-10-25
    diff-engine#4). `outs` and `ins` are (account, security, date,
    units, whole) of the legs the window pairing left, units positive;
    `whole` False marks a leg that may not pair here (partly paired
    already, a journal leg, one that looks like a move whose quantities
    do not pair) but still counts as a candidate.

    A candidate is an out leg and an in leg of the same security and
    the same units, the in leg OWN_MOVE_DAYS + 1 to SLOW_MOVE_DAYS days
    AFTER the out leg (an arrival never comes from a later departure:
    audit 2026-10-24 B50), in any accounts. A candidate pairs only when
    both legs are whole, in two accounts, and neither has another
    candidate (an in leg back in the out leg's own account counts as
    one): two departures that could be one arrival's source, or two
    arrivals one departure's, pair nothing — never a guess. The window
    pairing (OWN_MOVE_DAYS, either order) is the caller's, first."""
    def _q(x: float) -> float:
        return abs(float(x or 0.0))

    def _cand(o, g) -> bool:
        if o[1] != g[1]:
            return False
        qo, qi = _q(o[3]), _q(g[3])
        if abs(qo - qi) > max(_EPS, 1e-6 * max(qo, qi)):
            return False
        do, di = _d(o[2]), _d(g[2])
        if do is None or di is None:
            return False
        return OWN_MOVE_DAYS < (di - do).days <= SLOW_MOVE_DAYS
    by_out: Dict[int, List[int]] = {}
    by_in: Dict[int, List[int]] = {}
    for j, o in enumerate(outs):
        if _q(o[3]) <= _EPS:
            continue
        for i, g in enumerate(ins):
            if _q(g[3]) > _EPS and _cand(o, g):
                by_out.setdefault(j, []).append(i)
                by_in.setdefault(i, []).append(j)
    return [(j, c[0]) for j, c in sorted(by_out.items())
            if len(c) == 1 and len(by_in.get(c[0], ())) == 1
            and outs[j][4] and ins[c[0]][4]
            and outs[j][0] != ins[c[0]][0]]


def stated_book_value(row: Dict[str, Any]) -> Optional[float]:
    """The book value the broker states on a transfer row (the parser's
    `book_value` evidence), else None. A VALUE / net_amount that is a
    market value (IB) is never one. A stated 0 is 0.0 — known to be
    stated, so the arrival can say so — and never a cost (Arrival.status
    reads it as no_cost; audit 2026-10-24 B40). A negative or non-finite
    figure is no evidence (None)."""
    raw = row.get("book_value")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        bv = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(bv) or bv < 0:
        return None
    return bv


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
             key: Optional[Callable[[str], str]] = None,
             moves: Optional[List[OwnMove]] = None,
             slow: Optional[List[SlowMove]] = None,
             no_slow: Optional[Callable[[Tuple[str, str, Dict[str, Any]]],
                                        bool]] = None,
             no_move: Optional[Callable[[Tuple[str, str, Dict[str, Any]]],
                                        bool]] = None,
             not_own: Optional[Sequence[NotOwn]] = None,
             paired: Optional[List[Tuple[str, str, str, float]]] = None,
             apart: Optional[Callable[[str, str], bool]] = None
             ) -> List[Arrival]:
    """The transfer-in quantities that came from outside the books.

    Per security (`key`: the symbol after ticker.map renames; default
    the upper-cased symbol), every out leg cancels in legs — same
    quantity first, then the closest date, within OWN_MOVE_DAYS days —
    across ALL the given accounts (a move between two of your accounts
    is not an arrival; `moves`: given a list, each cancellation between
    two accounts is appended to it, OwnMove, backlog TJ-0093, the
    slower ones below too) — except the legs of a journal inside one
    account (own_journal_legs), which cancel within their own pair.
    Then, inside one account, an out leg of another listing of the same
    root (cross_listings.listing_root), the same quantity, within
    LISTING_JOURNAL_DAYS days: a listing journal with no pair id and no
    ticker.map rule. Then a slower move of your own between two
    accounts (slow_pairs: the same security and quantity, the in leg up
    to SLOW_MOVE_DAYS days after the out leg, neither with another
    candidate), each appended to `slow` when given; a row `no_slow`
    marks (the taxable leg of an in-kind move, booked or not) never
    pairs so, though it counts as a candidate. What is left of each in
    leg is an Arrival, carrying the earlier transfer-outs of its
    security nothing paired (Arrival.departures).

    `no_move`: a row it marks (the taxable leg of an in-kind move to or
    from a registered account, booked or not) went to or came from that
    account: it never pairs with another account's leg as a move of
    your own, in any step (backlog TJ-0230: within the window it took
    part of another quantity's transfer-in), nor is it a departure; it
    still cancels within its own account.

    `not_own`: the project's .tt NOTOWN lines (backlog TJ-0075). A line
    naming an exact pairing (step 1: its two legs of the same units,
    both whole) or a slow one (step 3) undoes it, and sets the line's
    `matched` and `legs`: its in leg is an Arrival, its out leg a
    departure (never named on that Arrival's line). Every other leg
    pairs as without the line — the two legs keep out of the later
    steps as the pairing had kept them — so a line never makes a
    pairing.

    `paired`: given a list, the identity (leg_ident) of every in leg
    the pairing cancelled, wholly or in part (a move of your own, a
    journal inside one account, shares out and back): the positive
    evidence `tjs transfers` needs to call a transfer-in your own
    move (backlog TJ-0308).

    `apart(out symbol, in symbol)`: true when the user's ticker.map
    keeps the two securities apart (a DISTINCT line): such legs are
    never a listing journal (step 2; backlog TJ-0821)."""
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
    # An in-kind move's taxable leg (`no_move`): never another
    # account's partner (backlog TJ-0230).
    stay = ({id(g) for g in legs if no_move((g[0], g[1], g[2]))}
            if no_move else set())
    # The NOTOWN lines (backlog TJ-0075), each matched afresh: the legs
    # a line took out of the pairing (`held`, by id) and each undone
    # pair's in leg -> its out leg.
    lines = list(not_own or ())
    for n in lines:
        n.matched, n.legs = "", ()
    held: set = set()
    undone: Dict[int, int] = {}

    def _undo(o, g, how: str) -> bool:
        n = next((x for x in lines if not x.matched
                  and x.names(o, g, keyf)), None)
        if n is None:
            return False
        n.matched = how
        n.legs = (leg_ident(o[0], o[2]), leg_ident(g[0], g[2]))
        undone[id(g)] = id(o)
        return True

    def _full(g) -> bool:
        # Untouched: the row's whole quantity is left.
        return abs(g[4] - abs(float(g[2].get("quantity") or 0.0))) <= _EPS

    def _cancel(pairs, exact: bool = False):
        for *_rank, i, j in sorted(pairs):
            if id(ins[i]) in held or id(outs[j]) in held:
                continue        # a NOTOWN line's: as if spent
            take = min(ins[i][4], outs[j][4])
            if take <= _EPS:
                continue
            if (exact and lines and _full(ins[i]) and _full(outs[j])
                    and abs(ins[i][4] - outs[j][4]) <= _EPS
                    and _undo(outs[j], ins[i], "exact")):
                # An exact pairing a NOTOWN line undoes: both legs keep
                # their units and stay out of every later step, as the
                # pairing had spent them (no other pairing changes).
                held.update((id(ins[i]), id(outs[j])))
                continue
            ins[i][4] -= take
            outs[j][4] -= take
            _moved(outs[j], ins[i], take)

    # Each move of your own with its two legs: what the pairing leaves
    # of them is set once it is done (OwnMove.out_left / in_left).
    made: List[Tuple[OwnMove, List[Any], List[Any]]] = []

    def _moved(o, g, take, slow_days: int = 0):
        # Units cancelled between two accounts: a move of your own.
        if moves is not None and o[0] != g[0]:
            m = OwnMove(
                key=g[3], quantity=round(take, 8), out_account=o[0],
                out_date=str(o[2].get("date") or "")[:10],
                in_account=g[0], in_date=str(g[2].get("date") or "")[:10],
                out_symbol=str(o[2].get("symbol") or ""),
                in_symbol=str(g[2].get("symbol") or ""),
                out_currency=str(o[2].get("currency") or ""),
                in_currency=str(g[2].get("currency") or ""),
                slow=slow_days)
            moves.append(m)
            made.append((m, o, g))

    def _gap(a, b):
        da, db = _d(a[2].get("date")), _d(b[2].get("date"))
        return abs((da - db).days) if da and db else 10 ** 6
    # 1. The same security: same quantity first, then the closest date
    #    — within OWN_MOVE_DAYS days, the two legs of one move. Further
    #    apart they are a departure and an arrival of their own (audit
    #    2026-10-24 B50: a 2026 departure cancelled a 2020 arrival whose
    #    booking the years between needed).
    _cancel([(abs(inn[4] - o[4]) > _EPS, _gap(inn, o),
              str(inn[2].get("date")), i, j)
             for i, inn in enumerate(ins) for j, o in enumerate(outs)
             if inn[3] == o[3]
             and (inn[0] == o[0] or not (inn[5] or o[5] or id(inn) in stay
                                         or id(o) in stay))
             and _gap(inn, o) <= OWN_MOVE_DAYS], exact=True)
    # 2. A listing journal (the same units moved between two listings of
    #    one security, e.g. a US-dollar and a Canadian-dollar line, with
    #    no ticker.map rule joining them): an out leg of another listing
    #    of the SAME ROOT (ZZQ.TO / ZZQ.US; a class or warrant suffix is
    #    the root's own) in the same account, the same quantity, within
    #    LISTING_JOURNAL_DAYS days. Two securities that share a quantity
    #    and a day are no journal (audit 2026-10-24 B03): the in leg
    #    stays an arrival, said as ATTENTION; a journal between two
    #    roots is declared by the ticker.map TOBASE line joining them.
    #    Nor are two listings a DISTINCT line keeps apart (backlog
    #    TJ-0821: the arrival of the other company's shares was cancelled
    #    and their sale read as a short).
    from taxjson.lib.cross_listings import listing_root

    def _apart(o, inn) -> bool:
        if apart is None:
            return False
        so = str(o[2].get("symbol") or "")
        si = str(inn[2].get("symbol") or "")
        return apart(so, si) or apart(o[3], inn[3])
    _cancel([(_gap(inn, o), str(inn[2].get("date")), i, j)
             for i, inn in enumerate(ins) for j, o in enumerate(outs)
             if inn[3] != o[3] and inn[0] == o[0]
             and abs(inn[4] - o[4]) <= _EPS
             and _gap(inn, o) <= LISTING_JOURNAL_DAYS
             and listing_root(str(inn[2].get("symbol") or ""))
             == listing_root(str(o[2].get("symbol") or ""))
             and not _apart(o, inn)])

    # 3. A move of your own slower than the window (audit 2026-10-25
    #    diff-engine#4): the in leg 11 to SLOW_MOVE_DAYS days after an
    #    out leg of the same security and quantity in another of your
    #    accounts, neither leg with another candidate. A taxable
    #    departure never leaves the books, so booking the arrival too
    #    (at the stated book value) held the shares twice and blended
    #    the pool's cost; an ATON move often takes one to three weeks.
    #    Ambiguous or partly paired legs stay an arrival and a departure
    #    of their own (the arrival's line names the departure).
    def _whole(g) -> bool:
        return (not g[5] and abs(g[4] - abs(float(g[2].get("quantity")
                                                  or 0.0))) <= _EPS
                and id(g) not in stay
                and not (no_slow and no_slow((g[0], g[1], g[2]))))
    lo = [j for j, o in enumerate(outs) if o[4] > _EPS and id(o) not in held]
    li = [i for i, g in enumerate(ins) if g[4] > _EPS and id(g) not in held]
    for pj, pi in slow_pairs(
            [(outs[j][0], outs[j][3], outs[j][2].get("date"), outs[j][4],
              _whole(outs[j])) for j in lo],
            [(ins[i][0], ins[i][3], ins[i][2].get("date"), ins[i][4],
              _whole(ins[i])) for i in li]):
        o, g = outs[lo[pj]], ins[li[pi]]
        if lines and _undo(o, g, "slow"):
            continue            # a NOTOWN line undoes it (TJ-0075)
        sm = SlowMove(
            key=g[3], symbol=str(g[2].get("symbol") or ""),
            quantity=round(g[4], 8), out_account=o[0],
            out_date=str(o[2].get("date") or "")[:10],
            in_account=g[0], in_date=str(g[2].get("date") or "")[:10])
        if slow is not None:
            slow.append(sm)
        _moved(o, g, g[4], sm.days)
        o[4] = g[4] = 0.0
    for m, o, g in made:
        m.out_left, m.in_left = round(o[4], 8), round(g[4], 8)

    def _departures(g) -> List[Tuple[str, str, float]]:
        # The out legs of the security nothing paired, in any of the
        # accounts (the arrival's own: shares that went out and came
        # back), up to SLOW_MOVE_DAYS before the arrival (a journal leg
        # is a move inside its account, a `no_slow` leg an in-kind
        # move's: never one; nor the out leg a NOTOWN line says is not
        # this arrival's source).
        dg = _d(g[2].get("date"))
        found = []
        for o in outs:
            do = _d(o[2].get("date"))
            if (o[4] > _EPS and not o[5] and o[3] == g[3]
                    and undone.get(id(g)) != id(o) and id(o) not in stay
                    and not (no_slow and no_slow((o[0], o[1], o[2])))
                    and dg is not None and do is not None
                    and 0 <= (dg - do).days <= SLOW_MOVE_DAYS):
                found.append((o[0], str(o[2].get("date") or "")[:10],
                              round(o[4], 8)))
        return sorted(found, key=lambda x: (x[1], x[0]))
    if paired is not None:
        paired.extend(leg_ident(acct, t) for n, (acct, _b, t)
                      in enumerate(rows) if n in settled
                      and float(t.get("quantity") or 0.0) > _EPS)
        paired.extend(leg_ident(g[0], g[2]) for g in ins
                      if g[4] < float(g[2].get("quantity") or 0.0) - _EPS)
    out: List[Arrival] = []
    for g in ins:
        a, b, t, k, left, _j = g
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
            book_value=(round(bv * left / q, 2) if bv is not None and q
                        else None),
            broker=b, source=str(t.get("source") or ""),
            description=str(t.get("description") or ""),
            departures=_departures(g), row=t))
    out.sort(key=lambda a: (a.account, a.key, a.date))
    return out


def _tt_events(rows: Iterable[Dict[str, Any]], keyf: Callable[[str], str]
               ) -> List[Tuple[str, str, int, str, str, float, str]]:
    """The position events of an account's converted .tt rows, in (date,
    time, file order): (date, time, n, kind, key, value, new_key) — a
    `trade` moves `value` units of `key` (a BUYSELL, an ASSIGN, any row
    with a quantity); a `split` scales the units of `key` by `value` and
    moves them to `new_key` when the SPLIT line renames (the engine's
    arithmetic: core._verify_share_conservation). Opening balances are
    not events (mark_covered reads them as snapshots); income and
    adjustments move no units."""
    from taxjson.lib.core import is_opening_row
    out: List[Tuple[str, str, int, str, str, float, str]] = []
    for n, t in enumerate(rows):
        if not isinstance(t, dict) or is_opening_row(t):
            continue
        act = t.get("action")
        if act in ("DIVIDEND", "DIVIDEND_IN_LIEU", "ADJUST", "INTEREST",
                   "FEE"):
            continue
        try:
            v = float(t.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        k = keyf(str(t.get("symbol") or ""))
        d, tm = str(t.get("date") or "")[:10], str(t.get("time") or "")
        if act == "SPLIT":
            if v <= 0:
                continue        # the converter refuses these (core.py)
            out.append((d, tm, n, "split", k, v,
                        keyf(str(t.get("symbol_new") or t.get("symbol")
                                 or ""))))
        elif abs(v) > _EPS:
            out.append((d, tm, n, "trade", k, v, k))
    out.sort(key=lambda e: (e[0], e[1], e[2]))
    return out


def _tt_units_at(events: Sequence[Tuple[str, str, int, str, str, float,
                                        str]],
                 consumed: Sequence[Tuple[str, str, str, float]],
                 a: Arrival,
                 landed: Sequence[Tuple[str, str, str, float]] = ()
                 ) -> float:
    """The units of `a.key` the account's .tt rows hold when `a`
    arrives, replayed: every .tt acquisition dated on or before the
    arrival day (the documented fix is a purchase "dated on or before
    the transfer", whatever its time); a .tt sale or SPLIT only when it
    is BEFORE the arrival — an earlier day, or the same day earlier by
    its time when the arrival's row carries one (a sale later on the
    arrival day sells the arrived shares and consumes nothing: audit
    2026-10-24 B42; a SPLIT before the arrival scales the earlier
    purchase into the units that arrived: B04); the units earlier
    arrivals of this account took (`consumed`: (date, time, key,
    units)), at their own place in the replay, so a split between two
    arrivals scales them too. The units an earlier arrival took are in
    the account from then on: a later .tt sale sells the .tt units on
    hand first, and what it sells beyond them is the arrived units, not
    a purchase dated after it (backlog TJ-0823: such a sale also spent
    the second arrival's own later purchase, whose book value was then
    booked as well — held twice; TJ-1054: a sale of the arrived units
    first left the account's own .tt units looking unspent, and they
    covered an arrival with no purchase, never booked). A broker sale
    takes the broker's units, then the arrived ones, then the units the
    account held by other means (`other`), then the .tt units on hand:
    nothing else was there for it to sell (backlog TJ-1179, TJ-1283).

    `landed`: the same (date, time, key, units) of the units of earlier
    arrivals no .tt purchase accounts for (booked at the broker's book
    value, or kept out): in the account from then on, as the arrived
    units are, and they spend no purchase (backlog TJ-1055).

    mark_covered's other events (backlog TJ-0822): a declared dated
    rename is a `split` of 1 that renames; `broker` an acquisition
    (sale) of the account's broker rows, its own pool; an in-kind
    move's taxable side `inkind`: a contribution takes its units out of
    the account — the arrived units first, then the broker's, only then
    the .tt purchases still unspent (a contributed .tt purchase covers
    no later arrival; it never spends a purchase dated after it) —, a
    withdrawal's units are in the account as an arrival's are. `other`
    (read by a broker sale alone; backlog TJ-1283): an opening balance's
    units and a move of your own in; `broker_split`, a broker row's SPLIT,
    adds what it makes of the units a broker sale can read."""
    arrival_time = str(a.row.get("time") or "") if a.row else ""

    def before(d: str, tm: str) -> bool:
        return d < a.date or (d == a.date and bool(tm) and bool(arrival_time)
                              and tm < arrival_time)

    merged = list(events) + [(d, tm, 10 ** 9 + n, "consume", k, -u, k)
                             for n, (d, tm, k, u) in enumerate(consumed)]
    merged += [(d, tm, 2 * 10 ** 9 + n, "landed", k, u, k)
               for n, (d, tm, k, u) in enumerate(landed)]
    merged.sort(key=lambda e: (e[0], e[1], e[2]))
    held: Dict[str, float] = {}         # .tt units no arrival took yet
    arrived: Dict[str, float] = {}      # units earlier arrivals took
    broker: Dict[str, float] = {}       # the broker rows' units
    other: Dict[str, float] = {}        # units held by other means
    redirect: Dict[str, str] = {}

    def _take(pool: Dict[str, float], k: str, want: float) -> float:
        got = min(want, max(0.0, pool.get(k, 0.0)))
        pool[k] = pool.get(k, 0.0) - got
        return want - got

    def rk(k: str) -> str:
        while k in redirect:
            k = redirect[k]
        return k

    for d, tm, _n, kind, k, v, nk in merged:
        if d > a.date:
            break
        k2 = rk(k)
        if kind == "trade":
            if v < 0 and not before(d, tm):
                continue
            if v < 0:
                # The .tt units on hand first; beyond them, the arrived
                # units; what is still left is a short the .tt lines
                # open (a later purchase closes it).
                v = -_take(arrived, k2, _take(held, k2, -v))
            held[k2] = held.get(k2, 0.0) + v
        elif kind == "consume":
            if not (d < a.date or (d == a.date and tm <= a.time)):
                continue
            held[k2] = held.get(k2, 0.0) + v
            arrived[k2] = arrived.get(k2, 0.0) - v
        elif kind == "landed":
            if d < a.date or (d == a.date and tm <= a.time):
                arrived[k2] = arrived.get(k2, 0.0) + v
        elif kind == "broker":
            if v < 0 and not before(d, tm):
                continue
            if v < 0:
                _take(held, k2, _take(other, k2, _take(
                    arrived, k2, _take(broker, k2, -v))))
            else:
                broker[k2] = broker.get(k2, 0.0) + v
        elif kind == "other":
            other[k2] = other.get(k2, 0.0) + v
        elif kind == "broker_split":
            if not before(d, tm):
                continue
            tk = rk(nk)
            units = sum(max(0.0, p.get(k2, 0.0))
                        for p in (broker, arrived, other))
            other[tk] = other.get(tk, 0.0) + max(
                0.0, units * v - (units if tk == k2 else 0.0))
        elif kind == "inkind":
            if not before(d, tm):
                continue
            if v > 0:
                arrived[k2] = arrived.get(k2, 0.0) + v
            else:
                _take(held, k2, _take(broker, k2, _take(arrived, k2, -v)))
        elif kind == "split":
            if not before(d, tm):
                continue
            tk = rk(nk)
            for pool in (held, arrived, broker, other):
                pool[k2] = pool.get(k2, 0.0) * v
                if tk != k2:
                    pool[tk] = pool.get(tk, 0.0) + pool.pop(k2, 0.0)
            if tk != k2:
                redirect[k2] = tk
    return max(0.0, held.get(rk(a.key), 0.0))


def mark_covered(found: List[Arrival],
                 tt_rows: Dict[str, List[Dict[str, Any]]], *,
                 key: Optional[Callable[[str], str]] = None,
                 renames: Iterable[Tuple[str, str, str]] = (),
                 broker_rows: Optional[Dict[str, List[Dict[str, Any]]]]
                 = None,
                 in_kind: Optional[Dict[str, List[Tuple[str, str, float]]]]
                 = None,
                 own_in: Optional[Dict[str, List[Tuple[str, str, float]]]]
                 = None) -> None:
    """Set `covered` on each arrival the receiving account's .tt rows
    already account for: the .tt acquisitions of the security dated on
    or before the arrival, its .tt SPLIT lines before the arrival
    replayed, less the .tt sales before it (_tt_units_at), cover the
    arrivals in date order (`tt_rows`: account -> the converted .tt
    rows). Rows that account for PART of an arrival's units set
    `covered_units` instead (status `partly_covered`): the arrival is
    not covered and booked_rows books nothing for it — booking it
    whole would hold the covered units twice (audit 2026-10-24 B41),
    booking a pro-rata rest would guess which units the .tt lines
    are.

    The declarations the converted rows leave out, replayed with them
    (backlog TJ-0822): `renames`, the project's dated renames (date,
    old symbol, new symbol: a purchase of the old symbol covers an
    arrival of the new one after the date, as a SPLIT line that renames
    does); `in_kind`, account -> its booked in-kind moves' taxable side
    (date, symbol, signed units: a contribution's shares left the
    account); `broker_rows`, account -> its parsed broker rows, whose
    shares a contribution takes before a .tt purchase's and whose sales
    sell the arrived units (backlog TJ-1054). An arrival the rows do not
    cover is in the account from then on too, booked or not (backlog
    TJ-1055). `own_in`, account -> the moves of your own into it (date,
    symbol, units), and the account's opening balances: a broker sale of
    those units spends no .tt purchase (backlog TJ-1283)."""
    from taxjson.lib.core import is_opening_row
    keyf = key or (lambda s: str(s or "").upper())
    extra: List[Tuple[str, str, int, str, str, float, str]] = []
    for n, (d, old, new) in enumerate(renames):
        ko, kn = keyf(old), keyf(new)
        if ko != kn:
            # At the start of its day: a row of that day is the new
            # symbol's.
            extra.append((str(d)[:10], "00:00:00", -1 - n, "split", ko,
                          1.0, kn))
    for acct in sorted({a.account for a in found}):
        rows = [t for t in tt_rows.get(acct) or [] if isinstance(t, dict)]
        # An opening balance (CA-OPEN-03) dated on or after an arrival
        # already holds those shares: the snapshot replaces the history
        # before it, the arrival included.
        snap: Dict[str, str] = {}
        for t in rows:
            if is_opening_row(t):
                k = keyf(str(t.get("symbol") or ""))
                snap[k] = max(snap.get(k, ""), str(t.get("date") or "")[:10])
        for a in found:
            if (a.account == acct and not a.covered
                    and a.date <= snap.get(a.key, "")):
                a.covered, a.covered_by = True, "tt"
        events = _tt_events(rows, keyf) + extra
        for d, tm, _n, kind, k, v, nk in _tt_events(
                (broker_rows or {}).get(acct) or [], keyf):
            if kind == "trade":
                events.append((d, tm, 10 ** 6 + _n, "broker", k, v, k))
            elif kind == "split":
                events.append((d, tm, 10 ** 6 + _n, "broker_split", k, v, nk))
        for n, (d, sym, q) in enumerate((in_kind or {}).get(acct) or []):
            k = keyf(sym)
            events.append((str(d)[:10], "09:30:00", 10 ** 8 + n,
                           "inkind", k, float(q), k))
        for n, t in enumerate(rows):
            if is_opening_row(t):
                k = keyf(str(t.get("symbol") or ""))
                try:
                    q = float(t.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    continue
                if q > 0:
                    events.append((str(t.get("date") or "")[:10], "",
                                   -10 ** 6 + n, "other", k, q, k))
        for n, (d, sym, q) in enumerate((own_in or {}).get(acct) or []):
            k = keyf(sym)
            events.append((str(d)[:10], "", -10 ** 5 + n, "other", k,
                           float(q), k))
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        consumed: List[Tuple[str, str, str, float]] = []
        landed: List[Tuple[str, str, str, float]] = []
        for a in sorted((x for x in found if x.account == acct
                         and not x.covered),
                        key=lambda x: (x.date, x.time)):
            a.covered_units = 0.0
            avail = _tt_units_at(events, consumed, a, landed)
            if avail >= a.quantity - _EPS:
                a.covered, a.covered_by = True, "tt"
                consumed.append((a.date, a.time, a.key, a.quantity))
                continue
            if avail > _EPS:
                a.covered_units = round(avail, 8)
                consumed.append((a.date, a.time, a.key, avail))
            landed.append((a.date, a.time, a.key,
                           a.quantity - max(0.0, avail)))


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
    stated book value (status `book_value`): one BUYSELL each, on the
    arrival date, at the book value (TRANSFER_BOOK_VALUE_TYPE). Nothing
    for a partly covered arrival (its .tt purchases account for some of
    its units: said as ATTENTION until the rest is declared) or a stated
    book value of 0 (no cost, never a $0 lot)."""
    out = []
    for a in found:
        if a.status != "book_value":
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
    the broker's book value, those only partly covered by the account's
    .tt purchases (not booked), and those with no cost (a row stating
    no book value, and a row stating 0, each said as what it is). An
    arrival booked or kept out that follows a transfer-out of yours
    nothing paired (Arrival.departures) names it: the sending account
    still holds those shares."""
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

    def _list_parts(xs):
        shown = "; ".join(f"{x.quantity:g} {x.symbol} ({x.date}, "
                          f"{x.covered_units:g} of {x.quantity:g} covered)"
                          for x in xs[:6])
        return shown + ("; ..." if len(xs) > 6 else "")
    override = ("To use your own figure instead, add the original "
                "purchase as a .tt BUYSELL line dated on or before the "
                "transfer: the book value is then no longer used and this "
                "line stops.")

    def _departed(xs, booked: bool) -> str:
        # Audit 2026-10-25 diff-engine#4: an earlier transfer-out of the
        # security nothing paired (ambiguous, another quantity) may be
        # the arrival's source. A taxable departure never leaves the
        # books, so the line names it and what follows.
        ds = [x for x in xs if x.departures]
        if not ds:
            return ""
        named = "; ".join(
            f"{x.quantity:g} {x.symbol} ({x.date}) after "
            + ", ".join(f"{u:g} out of {acct} ({d})"
                        for acct, d, u in x.departures[:3])
            + (", ..." if len(x.departures) > 3 else "")
            for x in ds[:3]) + ("; ..." if len(ds) > 3 else "")
        who = ("It may be" if len(xs) == 1
               else f"{len(ds)} of them may be")
        then = ("they are then held twice: `tjs sanity` compares each "
                "account with the broker's holdings" if booked else
                "a purchase added for them would then hold them twice")
        return (f" {who} the other end of a transfer-out of yours that "
                f"nothing paired ({named}; more than one could pair, or "
                f"the quantities differ): a transfer-out never leaves "
                f"the books, so if they are one move of your own the "
                f"sending account still holds those shares, and {then} "
                f"(docs/troubleshooting.md).")
    for (acct, status), xs in sorted(by.items()):
        if status == "partly_covered":
            # Audit 2026-10-24 B41: the .tt purchases account for some
            # of the units. Booking the arrival whole would hold those
            # units twice; booking the rest pro rata would guess which
            # units the .tt lines are. Nothing is booked, and the line
            # names both answers.
            lines.append(
                f"transfer-in: {acct}: {len(xs)} transfer-in(s) from "
                f"outside your books only PARTLY covered by your .tt "
                f"purchases ({_list_parts(xs)}): NOT booked. The units "
                f"your .tt lines do not account for have no {cost} in "
                f"the books, so a later sale reads as a short; a book "
                f"value the broker states on the row is not used for a "
                f"part. Add the purchase of those units as a .tt BUYSELL "
                f"line dated on or before the transfer (date and {cost} "
                f"from the sending broker) — or of all the transferred "
                f"units, when the .tt purchases are another holding of "
                f"the stock in this account; this line then stops.")
            continue
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
                f"your {cost}: check it.{hp} {override}"
                + _departed(xs, True))
        else:
            plain = [x for x in xs if not x.zero_book_value]
            zero = [x for x in xs if x.zero_book_value]
            if plain:
                lines.append(
                    f"transfer-in: {acct}: {len(plain)} transfer-in(s) "
                    f"from outside your books have NO cost in the books "
                    f"({_list(plain)}): the row states no book value (a "
                    f"VALUE column is the market value, never your "
                    f"{cost}), so the shares are kept out and a later "
                    f"sale reads as a short. Add the original purchase "
                    f"as a .tt BUYSELL line (date and {cost} from the "
                    f"sending broker) dated on or before the transfer; "
                    f"this line then stops (docs/getting-started.md, "
                    f"step 5c)." + _departed(plain, False))
            if zero:
                # Audit 2026-10-24 B40: a stated 0 is not "no book value"
                # — it is said as what it is, and not booked as a $0 lot
                # (a broker's 0 is usually its placeholder for a cost it
                # was not given; a real nil cost is a .tt purchase at 0).
                lines.append(
                    f"transfer-in: {acct}: {len(zero)} transfer-in(s) "
                    f"from outside your books have NO cost in the books "
                    f"({_list(zero)}): the row states a book value of 0, "
                    f"usually the broker's placeholder for the {cost} it "
                    f"was not given, never booked as a $0 lot, so the "
                    f"shares are kept out and a later sale reads as a "
                    f"short. Add the original purchase as a .tt BUYSELL "
                    f"line (date and {cost} from the sending broker; at "
                    f"0 if your {cost} really is nil) dated on or before "
                    f"the transfer; this line then stops "
                    f"(docs/getting-started.md, step 5c)."
                    + _departed(zero, False))
    return lines

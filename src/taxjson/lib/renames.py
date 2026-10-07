"""Ticker changes as DATED events (owner decision on audit A2-0197).

A rename is an event in the books, like a trade or a dividend: on its
date the position, the ACB / basis lots and the acquisition dates carry
from the old symbol to the new one, and the superficial-loss / wash-sale
rule treats OLD before the date and NEW after it as one security. It is
booked as a SPLIT row with a `symbol_new` (ratio 1 for a pure ticker
change) and comes from one of three places:

  - a broker row (IB's Corporate Actions, the corp-action stage's
    `rename` election, a parser's own SPLIT — IB's one contract id under
    two symbols is one, event_source "ib-conid");
  - a `.tt` line `RENAME <date> OLD NEW [late=...]` in any account's
    folder (lib/dated_events: the run writes it into its effective map)
    or, legacy, a ticker.map line `RENAME OLD NEW YYYY-MM-DD` — the
    pipeline books the SPLIT row in every account that held OLD before
    the date (none is added where the broker already booked the event);
  - a `.tt` line `SPLIT <date> <time> OLD NEW 1` (that account only).

After the date OLD is NOT automatically the same security. A trade in
OLD after the rename date is either the broker still booking the
renamed shares under the old ticker, or another company that now uses
the ticker — the export cannot tell. Such rows are listed by
`taxjson renames`, stop `taxjson run --strict`, and stay a separate
security until the user declares them on the dated line (.tt form):

  RENAME YYYY-MM-DD OLD NEW late=fold      the late OLD rows ARE the
                                           renamed shares: booked as NEW
  RENAME YYYY-MM-DD OLD NEW late=separate  another security: kept as OLD

An UNDATED ticker.map rename (`GLOBAL OLD NEW`, or `RENAME OLD NEW`
without a date) keeps its old meaning — every row of OLD, at any date,
is NEW — and `taxjson renames` suggests the dated form when a broker
event gives the date.
"""
from __future__ import annotations

from taxjson.lib.stage_msg import emit_line

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LATE_FOLD = "fold"
LATE_SEPARATE = "separate"
LATE_CHOICES = (LATE_FOLD, LATE_SEPARATE)

# The `source` stamped on a SPLIT row the pipeline books from a dated
# ticker.map RENAME line.
TICKER_MAP_SOURCE = "ticker.map"

# Rows that move a position (a late one names the old ticker after its
# rename).
_POSITION_ACTIONS = ("BUYSELL", "ASSIGN", "TRANSFER", "OPENING_BALANCE")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Copies of one rename booked on two dates (two brokers) are one event.
WINDOW_DAYS = 7


# Where a dated rename was declared (DatedRename.source, a booked SPLIT
# row's event_source): a .tt line `RENAME <date> OLD NEW` (the form of
# record, lib/dated_events), a legacy ticker.map line, or IB's contract
# id under two symbols (brokerages/ib_extractor).
SOURCE_TT = "tt"
SOURCE_MAP = "map"
SOURCE_IB_CONID = "ib-conid"


# The kind of account a .tt RENAME applies to (DatedRename.kind): a
# security's ticker change never touches a coin, nor a coin's a security
# (tax-logic CA-CRYPTO-RENAME / US-CRYPTO-RENAME). A legacy ticker.map
# line has no account: KIND_ANY.
KIND_ANY = ""
KIND_SECURITIES = "securities"
KIND_CRYPTO = "crypto"


@dataclass(frozen=True)
class DatedRename:
    """One declared dated rename: a .tt `RENAME <date> OLD NEW` line or a
    legacy ticker.map `RENAME OLD NEW YYYY-MM-DD` line — or, once
    lib/dated_events.resolve_renames has merged the declarations of one
    change, the EVENT: its date (the earliest declared), `late` the
    choice for an account without a line of its own, `lates` each
    declaring account's own choice, `kind` the accounts it applies to,
    `also` the other declarations it merged."""
    old: str
    new: str
    date: str
    late: str = ""          # "", "fold" or "separate"
    where: str = ""         # "ticker.map:<lineno>" | "inputs/<acct>/<f>.tt:<n>"
    line: str = ""
    source: str = SOURCE_MAP    # SOURCE_MAP | SOURCE_TT
    account: str = ""           # the .tt line's account ('' for the map)
    kind: str = KIND_ANY        # KIND_ANY | KIND_SECURITIES | KIND_CRYPTO
    lates: Tuple[Tuple[str, str], ...] = ()     # (account, late) overrides
    also: Tuple[str, ...] = ()                  # merged declarations' where

    def tt_line(self) -> str:
        """The declaration as a .tt line (date first)."""
        return (f"RENAME {self.date} {self.old} {self.new}"
                + (f" late={self.late}" if self.late else ""))

    def late_for(self, account: str, held: bool = True) -> str:
        """The late= choice for `account`'s late rows: its own line's
        when it declared one, else the event's (M4) — the event's only
        for an account that `held` OLD before the date (its books carry
        the rename row): the late rows of an account that never held OLD
        are another security or a broker's late booking only that
        account can say (second pre-release review, 8)."""
        own = dict(self.lates)
        if account in own:
            return own[account]
        return self.late if held else ""

    def applies_to(self, kind: str) -> bool:
        """The event books in an account of `kind` (KIND_ANY: unknown,
        every event)."""
        return not kind or not self.kind or self.kind == kind


class RenameConflict(ValueError):
    """A dated ticker.map RENAME disagrees with the broker's own event."""


def parse_rename_tail(tokens: List[str]) -> Tuple[str, str]:
    """(date, late) of a dated RENAME line's tokens after `OLD NEW`
    (`YYYY-MM-DD [late=fold|late=separate]`). Raises ValueError with a
    one-line reason."""
    if not tokens:
        raise ValueError("no date")
    date = tokens[0]
    if not _DATE_RE.match(date):
        raise ValueError(f"the date {date!r} is not YYYY-MM-DD")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"the date {date!r} is not a calendar date") \
            from None
    late = ""
    rest = tokens[1:]
    if rest:
        tok = rest[0].lower()
        if not tok.startswith("late=") or tok[5:] not in LATE_CHOICES:
            raise ValueError(f"{rest[0]!r} is not late=fold or "
                             f"late=separate")
        late = tok[5:]
        rest = rest[1:]
    if rest:
        raise ValueError(f"extra token(s) {rest} — notes go after `#`")
    return date, late


# ----------------------------------------------------------------- rows

def _g(r: Any, k: str, default: Any = "") -> Any:
    if isinstance(r, dict):
        v = r.get(k, default)
    else:
        v = getattr(r, k, default)
    return default if v is None else v


def _days(a: str, b: str) -> Optional[int]:
    try:
        return abs((datetime.strptime(str(a)[:10], "%Y-%m-%d")
                    - datetime.strptime(str(b)[:10], "%Y-%m-%d")).days)
    except ValueError:
        return None


def _option_root(sym: str) -> Optional[str]:
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    if sym and is_option_symbol(sym):
        return parse_option_underlying(sym)
    return None


def names_symbol(sym: str, old: str) -> bool:
    """The row's symbol is `old` or an option on it."""
    return sym == old or _option_root(sym) == old


def rename_target(r: Any) -> str:
    """The new symbol of a SPLIT row that renames ('' otherwise)."""
    if str(_g(r, "action")).upper() != "SPLIT":
        return ""
    new = str(_g(r, "symbol_new") or "").strip()
    return "" if not new or new == str(_g(r, "symbol")) else new


# The words of a rename's `source` (the SOURCE column of `taxjson
# renames`): the stamp a stage writes on the SPLIT row. A source not
# named here is shown as it is (a broker row's file name: `broker row
# (<file>)`).
SOURCE_LABELS: Dict[str, str] = {
    TICKER_MAP_SOURCE: "ticker.map line",
    "map": "ticker.map line",
    "tt": ".tt line",
    "ib-conid": "detected IB contract id",
    "map-undated": "legacy undated map",
    "legacy": "legacy undated map",
}
# An undated ticker.map rename (GLOBAL, RENAME without a date).
UNDATED_SOURCE = "legacy undated map"


def row_source(r: Any) -> str:
    """Where a rename row came from, for the report (SOURCE_LABELS): the
    row's `event_source` (the stage that booked it) when it names one,
    else its `source`."""
    ev = str(_g(r, "event_source") or "")
    if ev in SOURCE_LABELS:
        src = str(_g(r, "source") or "")
        if ev == "tt" and src.lower().endswith(".tt"):
            return f".tt line ({src})"
        return SOURCE_LABELS[ev]
    src = str(_g(r, "source") or "")
    if src in SOURCE_LABELS:
        return SOURCE_LABELS[src]
    if src.lower().endswith(".tt"):
        return f".tt line ({src})"
    if _g(r, "corp_event_id"):
        return "broker corporate action"
    return f"broker row ({src})" if src else "broker row"


def row_source_id(r: Any) -> str:
    """The machine source of a rename row: "tt", "map", "ib-conid" or
    "broker" (a broker's own row: a corporate action, a parser's SPLIT)."""
    ev = str(_g(r, "event_source") or "")
    if ev:
        return ev
    src = str(_g(r, "source") or "")
    if src == TICKER_MAP_SOURCE:
        return SOURCE_MAP
    if src.lower().endswith(".tt"):
        return SOURCE_TT
    return "broker"


def rename_events(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """The rename events in `rows` (dicts or TaxTransactions, any
    accounts): one per (old, new) with copies within WINDOW_DAYS merged
    onto the earliest date. Each: date, old, new, ratio, sources,
    accounts, rows."""
    evs: List[Dict[str, Any]] = []
    for r in sorted((r for r in rows if rename_target(r)),
                    key=lambda r: (str(_g(r, "date")), str(_g(r, "symbol")))):
        old, new = str(_g(r, "symbol")), rename_target(r)
        d = str(_g(r, "date"))[:10]
        for e in evs:
            gap = _days(e["date"], d)
            if (e["old"], e["new"]) == (old, new) and gap is not None \
                    and gap <= WINDOW_DAYS:
                break
        else:
            e = {"date": d, "old": old, "new": new,
                 "ratio": float(_g(r, "quantity", 0.0) or 0.0),
                 "sources": [], "source_ids": [], "accounts": [],
                 "rows": []}
            evs.append(e)
        e["rows"].append(r)
        s = row_source(r)
        if s not in e["sources"]:
            e["sources"].append(s)
        s = row_source_id(r)
        if s not in e["source_ids"]:
            e["source_ids"].append(s)
        a = str(_g(r, "_acct") or _g(r, "account") or "")
        if a and a not in e["accounts"]:
            e["accounts"].append(a)
    return evs


def row_stamp(r: Any) -> Tuple[str, str]:
    """(date, time) of a row (no time: the start of its day)."""
    return (str(_g(r, "date"))[:10], str(_g(r, "time") or "00:00:00"))


class RenameNeedsCountry(RenameConflict):
    """The order of a row and a rename row on one day differs between
    the Canada and the US engine, and no country was given."""


class _Ordered:
    """A row's fields for the engines' sort key (dicts or rows)."""
    __slots__ = ("action", "date", "date_settle", "time", "quantity",
                 "symbol", "exercise_of")

    def __init__(self, r: Any) -> None:
        self.action = str(_g(r, "action")).upper()
        self.date = str(_g(r, "date"))[:10]
        self.date_settle = str(_g(r, "date_settle") or "")[:10]
        self.time = str(_g(r, "time") or "00:00:00")
        try:
            self.quantity = float(_g(r, "quantity", 0.0) or 0.0)
        except (TypeError, ValueError):
            self.quantity = 0.0
        self.symbol = str(_g(r, "symbol"))
        self.exercise_of = str(_g(r, "exercise_of") or "")


# The engine's ordering ladder per country (lib/corporate_timeline): the
# Canada engine orders by settle date and takes a SPLIT ahead of every
# execution of its settle date (whatever its clock time); the US engine
# orders by trade date and clock time.
_ENGINE_PROFILE = {"canada": "ca_main", "usa": "us_main"}


def _engine_after(r: Any, rename_row: Any, country: str) -> bool:
    from taxjson.lib.corporate_timeline import event_sort_key
    prof = _ENGINE_PROFILE[country]
    return (event_sort_key(_Ordered(r), profile=prof)
            > event_sort_key(_Ordered(rename_row), profile=prof))


def after_rename_row(r: Any, rename_row: Any, country: str) -> bool:
    """Row `r` comes after the account's rename row `rename_row` in the
    books of `country`'s engine (lib/corporate_timeline.event_sort_key):
    in Canada a SPLIT is taken ahead of every execution of its settle
    date, so an OLD trade executed that day — before the rename row's
    clock time too — is after it (third pre-release review, 1); in the
    US a later date, or a later time that day (a rename row stamped
    00:00:00 precedes the day's trades). Without a `country` (a stage
    tool run on its own) the rows where the two engines agree are
    decided and any other is refused (RenameNeedsCountry)."""
    from taxjson.lib.country import canonical_country
    if country:
        return _engine_after(r, rename_row, canonical_country(country))
    ca = _engine_after(r, rename_row, "canada")
    us = _engine_after(r, rename_row, "usa")
    if ca == us:
        return ca
    raise RenameNeedsCountry(
        f"{_g(r, 'symbol')} on {str(_g(r, 'date'))[:10]} "
        f"{_g(r, 'time') or ''}: whether it comes after the rename row "
        f"{_g(rename_row, 'symbol')} -> {_g(rename_row, 'symbol_new')} "
        f"of that day depends on the country's engine — pass --country")


def _own_rename_row(rows: Iterable[Any]) -> Any:
    """The earliest (date, time) of an account's rename rows of one
    change."""
    return min(rows, key=row_stamp)


def late_rows(rows: Iterable[Any], events: List[Dict[str, Any]],
              country: str = "") -> List[Tuple[Any, Dict[str, Any]]]:
    """(row, event) for each position row that names a renamed ticker
    (or an option on it) AFTER the rename in its account's books: the
    LATEST rename of the ticker before the row. "After" is the order of
    `country`'s engine: past the account's own rename row
    (after_rename_row — in Canada every execution of the rename row's
    settle date; in the US a later date or clock time, and a booked
    SPLIT is dated at the start of its day, 00:00:00, so an OLD row that
    day is already late), or, in an account with no rename row of its
    own, on or after the event's date — the same rows `late=fold`
    re-books (apply_dated_renames)."""
    by_old: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        by_old.setdefault(e["old"], []).append(e)
    for v in by_old.values():
        v.sort(key=lambda e: e["date"])
    if not by_old:
        return []

    def acct_of(r: Any) -> str:
        return str(_g(r, "_acct") or _g(r, "account") or "")

    out = []
    for r in rows:
        if str(_g(r, "action")).upper() not in _POSITION_ACTIONS:
            continue
        sym = str(_g(r, "symbol"))
        cands = by_old.get(sym)
        if cands is None:
            root = _option_root(sym)
            cands = by_old.get(root) if root else None
        if not cands:
            continue
        acct = acct_of(r)
        hit = None
        for e in cands:
            own = [x for x in e["rows"] if acct_of(x) == acct]
            if (after_rename_row(r, _own_rename_row(own), country) if own
                    else row_stamp(r)[0] >= e["date"]):
                hit = e
        if hit is not None:
            out.append((r, hit))
    return out


def matching_lines(dated: Iterable[DatedRename], event: Dict[str, Any],
                   mapping: Optional[Dict[str, str]] = None
                   ) -> List[DatedRename]:
    """The dated declarations that name `event`: the line's OLD (through
    the stage's undated renames, `mapping`) is the event's old symbol and
    its date is within WINDOW_DAYS of the event's."""
    from taxjson.bin.taxjson_ticker_map import map_symbol
    out = []
    for dr in dated:
        gap = _days(dr.date, event["date"])
        if (map_symbol(dr.old, mapping or {}) == event["old"]
                and gap is not None and gap <= WINDOW_DAYS):
            out.append(dr)
    return out


def declared_late(dated: Iterable[DatedRename], event: Dict[str, Any],
                  mapping: Optional[Dict[str, str]] = None,
                  account: Optional[str] = None, held: bool = True) -> str:
    """The late= choice the declarations make for `event` ('' when none);
    with `account`, the choice for that account's late rows
    (DatedRename.late_for: its own line's, else the event's when the
    account `held` OLD before the date — its books carry the event)."""
    for dr in matching_lines(dated, event, mapping):
        late = (dr.late_for(account, held) if account is not None
                else dr.late)
        if late:
            return late
    return ""


# ------------------------------------------------- the pipeline stage

def _booked_source(dr: DatedRename) -> str:
    """The `source` of the SPLIT row a declaration books: the .tt file's
    name for a .tt line, else TICKER_MAP_SOURCE."""
    if dr.source == SOURCE_TT and dr.where:
        name = dr.where.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        if name.lower().endswith(".tt"):
            return name
    return TICKER_MAP_SOURCE


def apply_dated_renames(txs: List[Any], dated: Iterable[DatedRename],
                        *, stream=None, kind: str = KIND_ANY,
                        country: str = "",
                        mapping: Optional[Dict[str, str]] = None
                        ) -> List[Any]:
    """Book each dated RENAME (a .tt line through the run's effective
    map, or a legacy ticker.map line) in `txs` (TaxTransactions on their
    RAW symbols, before the undated renames apply): a SPLIT row OLD ->
    NEW (ratio 1) on the date in every account that holds OLD before it,
    unless the account already books a rename of OLD within WINDOW_DAYS
    (to NEW: nothing to add; to another symbol: refused). OLD is the
    declared symbol and every raw spelling the undated renames
    (`mapping`, e.g. `GLOBAL RAW OLD`) map onto it (third pre-release
    review, 4). The renames apply in DATE order whatever order they were
    declared in, so a chain A -> B -> C carries the position twice: an
    account holds B before the second date when an earlier rename row
    moved A into B (H3) — on the same date too: any rename row into B
    that day, booked here for A -> B or the broker's / a .tt SPLIT
    (second pre-release review, 11; third, 3); the B -> C row is then
    stamped no earlier than it. `late=fold` (DatedRename.late_for)
    re-books the account's OLD rows (and options on OLD) AFTER its own
    rename row in `country`'s engine order (after_rename_row: the rows
    late_rows calls late — an OLD row the account books before its
    broker's rename row is still OLD) as NEW; the event's late= applies
    to the accounts that held OLD before the date, an account that never
    did folds only on a line of its own (second pre-release review, 5
    and 8). `kind`: the accounts' kind (KIND_SECURITIES on the equity
    merge, KIND_CRYPTO on the crypto map stage) — a .tt RENAME applies
    only to accounts of its declaring account's kind
    (DatedRename.applies_to). Returns the new list."""
    from taxjson.bin.taxjson_ticker_map import map_symbol
    from taxjson.lib.core import TaxTransaction
    stream = stream or sys.stderr
    mapping = mapping or {}
    # Stable: two renames on one date keep their declared order.
    dated = sorted((dr for dr in dated if dr.applies_to(kind)),
                   key=lambda dr: dr.date)
    if not dated:
        return txs
    out = list(txs)
    # The rename rows this function booked (an earlier event's row on the
    # same date moves a position into the next link of a chain).
    ours: set = set()
    for dr in dated:
        # The raw spellings of OLD: the declared symbol and every symbol
        # an undated rename maps onto it.
        olds = {dr.old} | {k for k, v in mapping.items() if v == dr.old}
        # Accounts with an OLD position before the date: OLD rows, or a
        # rename row that moved another symbol into OLD (on the date:
        # any such row — `into` keeps the latest, which the booked row
        # must follow).
        held: Dict[str, Any] = {}
        into: Dict[str, Any] = {}
        for t in out:
            d = t.date or ""
            if d > dr.date:
                continue
            if d == dr.date:
                if rename_target(t) in olds:
                    held[t.account] = t
                    if t.account not in into \
                            or row_stamp(t) >= row_stamp(into[t.account]):
                        into[t.account] = t
                continue
            if ((t.action in _POSITION_ACTIONS + ("SPLIT",)
                 and t.symbol in olds)
                    or rename_target(t) in olds):
                held[t.account] = t
        booked: Dict[str, str] = {}
        for t in out:
            new = rename_target(t)
            if new and t.symbol in olds:
                gap = _days(t.date, dr.date)
                if gap is not None and gap <= WINDOW_DAYS:
                    booked[t.account] = new
                    if map_symbol(new, mapping) != map_symbol(dr.new,
                                                              mapping):
                        raise RenameConflict(
                            f"{dr.where}: RENAME {dr.old} {dr.new} "
                            f"{dr.date} disagrees with the rename "
                            f"{t.symbol} -> {new} on {t.date} the books "
                            f"already carry (account {t.account}) — fix "
                            f"the {'.tt' if dr.source == SOURCE_TT else 'ticker.map'}"
                            f" line ({dr.line!r})")
        added = []
        for acct in sorted(held):
            if acct in booked:
                continue
            last = held[acct]
            prev = into.get(acct)
            added.append(TaxTransaction(
                action="SPLIT", date=dr.date,
                # (after a same-day rename row into OLD: a chain's
                # second link never precedes its first)
                time=max("00:00:00", row_stamp(prev)[1] if prev
                         is not None else ""),
                date_settle=dr.date, symbol=dr.old, symbol_new=dr.new,
                quantity=1.0, price=0.0, net_amount=0.0,
                currency=last.currency or "", account=acct,
                # (where it was declared is the row's source, not its
                # description: the row id stays the same when a legacy
                # ticker.map line moves to a .tt file)
                description=(f"Ticker change {dr.old}→{dr.new} "
                             f"(a dated RENAME; no disposition: "
                             f"basis, acquisition dates and identity "
                             f"carried)"),
                source=_booked_source(dr),
                event_source=dr.source or SOURCE_MAP))
        if added:
            emit_line(f"note: {dr.where}: RENAME {dr.old} -> {dr.new} on "
                      f"{dr.date} booked in {len(added)} account(s) "
                      f"({', '.join(t.account for t in added)}).",
                      file=stream)
            # Placed before the first row dated on or after the rename
            # (the rows are already in the pipeline's order), after the
            # rows booked for an earlier rename that day and after the
            # day's rename rows into OLD.
            at = next((i for i, t in enumerate(out)
                       if (t.date or "") >= dr.date), len(out))
            while at < len(out) and id(out[at]) in ours \
                    and (out[at].date or "") == dr.date:
                at += 1
            prevs = {id(t) for t in into.values()}
            for i, t in enumerate(out):
                if id(t) in prevs:
                    at = max(at, i + 1)
            out[at:at] = added
            ours.update(id(t) for t in added)
        # Each account's own rename row of this change: its late rows
        # are the ones after it.
        own: Dict[str, Any] = {}
        new_m = map_symbol(dr.new, mapping)
        for t in out:
            tgt = rename_target(t)
            if t.symbol in olds and tgt \
                    and map_symbol(tgt, mapping) == new_m:
                gap = _days(t.date, dr.date)
                if gap is not None and gap <= WINDOW_DAYS:
                    if t.account not in own \
                            or row_stamp(t) < row_stamp(own[t.account]):
                        own[t.account] = t
        moved: Dict[str, int] = {}
        kept: Dict[str, int] = {}
        m = {o: dr.new for o in olds}
        for t in out:
            if t.action == "SPLIT" or not any(names_symbol(t.symbol, o)
                                              for o in olds):
                continue
            if t.account in own:
                # (a broker's row a few days BEFORE the declared date
                # included: the rows after it are late)
                if not after_rename_row(t, own[t.account], country):
                    continue
                choice = dr.late_for(t.account)
            elif (t.date or "") < dr.date:
                continue
            else:
                choice = dr.late_for(t.account, held=False)
                if not choice and dr.late == LATE_FOLD:
                    kept[t.account] = kept.get(t.account, 0) + 1
            if choice == LATE_FOLD:
                t.symbol = map_symbol(t.symbol, m)
                moved[t.account] = moved.get(t.account, 0) + 1
        if moved:
            emit_line(f"note: {dr.where}: {sum(moved.values())} {dr.old} "
                      f"row(s) after the rename ({dr.date}) booked as "
                      f"{dr.new} "
                      f"(late=fold"
                      + (f": {', '.join(sorted(moved))}"
                         if dr.lates else "") + ").", file=stream)
        for acct, n in sorted(kept.items()):
            emit_line(f"note: {dr.where}: {n} {dr.old} row(s) of account "
                      f"{acct} on or after {dr.date} are kept as {dr.old}: "
                      f"late=fold applies to the accounts that held "
                      f"{dr.old} before the date, and {acct} did not "
                      f"(another company may use the ticker now) — "
                      f"`taxjson renames` lists them; a .tt line of "
                      f"{acct}'s own (`RENAME {dr.date} {dr.old} {dr.new} "
                      f"late=fold` or `late=separate`) settles them.",
                      file=stream)
    return out


# ------------------------------------------------- reading a project

def _load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read {path} ({e}) — re-run `taxjson "
                         f"run` to rebuild it") from None
    rows = doc.get("transactions", []) if isinstance(doc, dict) else doc
    return [r for r in rows if isinstance(r, dict)] \
        if isinstance(rows, list) else []


def _ticker_map(root: Path):
    """(TickerMap, base-stage undated renames) of the project, or
    (None, {}) without a map."""
    p = Path(root) / "ticker.map"
    if not p.exists():
        return None, {}
    from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                merge_renames)
    tmap = _parse_map_file(p)[0]
    try:
        ren = merge_renames(tmap, to_base=True)
    except ValueError:
        ren = {}
    return tmap, ren


def _book_rows(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    cache = Path(root) / "work"
    for acct in sorted(cfg.get("accounts") or {}):
        for r in _load_rows(cache / f"{acct}_base.json"):
            r = dict(r)
            r["_acct"] = acct
            out.append(r)
    return out


def _raw_rename_rows(root: Path, acct: str) -> List[Dict[str, Any]]:
    """The account's parsed rows BEFORE ticker.map (broker parses,
    corp-action files, .tt conversions), for the dated-form hint."""
    from taxjson.lib.pipeline import tt_json_path
    cache = Path(root) / "work"
    try:
        lines = (cache / f"{acct}_sources.list").read_text(
            encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):       # re-audit A2-0795
        return []
    files = []
    for ln in lines:
        kind, _, name = ln.partition("/")
        if kind == "tt":
            files.append(tt_json_path(cache, acct, name))
        elif kind:
            files += [cache / f"{acct}_{kind}.json",
                      cache / f"{acct}_{kind}_corp.json"]
    rows = []
    for f in dict.fromkeys(files):
        try:
            rows += [r for r in _load_rows(f) if rename_target(r)]
        except ValueError:
            continue
    return rows


def _walk_states(rows: List[Dict[str, Any]]):
    """Replay one account's rows: {event row id: (qty, cost)} of the
    renamed symbol just before each rename row (book cost: buys at their
    cost, sales at the average cost, ADJUST rows added; no superficial-
    loss adjustments)."""
    state: Dict[str, List[float]] = {}
    snap: Dict[int, Tuple[float, float]] = {}

    def key(r):
        a = str(r.get("action") or "")
        return (str(r.get("date") or ""),
                0 if a in ("OPENING_BALANCE",) else 1 if a == "SPLIT" else 2,
                str(r.get("time") or ""))
    for r in sorted(rows, key=key):
        a = str(r.get("action") or "").upper()
        sym = str(r.get("symbol") or "")
        st = state.setdefault(sym, [0.0, 0.0])
        q = float(r.get("quantity") or 0.0)
        if a == "SPLIT":
            new = rename_target(r)
            snap[id(r)] = (st[0], st[1])
            if q:
                st[0] *= q
            if new:
                dst = state.setdefault(new, [0.0, 0.0])
                dst[0] += st[0]
                dst[1] += st[1]
                state[sym] = [0.0, 0.0]
        elif a in _POSITION_ACTIONS:
            net = abs(float(r.get("net_amount") or 0.0))
            if q > 0:
                st[1] += net
            elif q < 0 and st[0] > 1e-9:
                st[1] -= st[1] * min(1.0, -q / st[0])
            st[0] += q
            if abs(st[0]) < 1e-9:
                st[1] = 0.0
        elif a == "ADJUST":
            st[1] += float(r.get("net_amount") or 0.0)
    return snap


# The look-alike rename hints a broker parse writes to its .diag
# (captured bytes, one line per message): Questrade's and RBC's "symbol A
# looks renamed to B — ... GLOBAL A B ... B ... first appears on DATE",
# and a dated `RENAME OLD NEW YYYY-MM-DD` a message names (Webull's
# "likely a ticker change", IB's one stock under several symbols).
# (The gaps are bounded: an unbounded lazy `.*?` after each candidate
# start made a long adversarial .diag message quadratic.)
_LOOKS_RE = re.compile(
    r"\b(Questrade|RBC) symbol \S{1,64}(?: \(\w{1,32}\))? looks renamed "
    r"to \S{1,64} .{0,300}?\bGLOBAL (\S{1,64}) (\S{1,64})\s.{0,300}?"
    r"first appears on (\d{4}-\d{2}-\d{2})")
# The hint as written since the dated .tt events (lib/dated_events): the
# .tt line, date first.
_LOOKS_TT_RE = re.compile(
    r"\b(Questrade|RBC) symbol \S{1,64}(?: \(\w{1,32}\))? looks renamed "
    r"to \S{1,64} .{0,300}?\bRENAME (\d{4}-\d{2}-\d{2}) (\S{1,64}) "
    r"(\S{1,64})\s")
_DATED_HINT_RE = re.compile(
    r"`RENAME (\S+) (\S+) (\d{4}-\d{2}-\d{2})`")
_DATED_HINT_TT_RE = re.compile(
    r"`RENAME (\d{4}-\d{2}-\d{2}) (\S+) (\S+)`")
_BROKER_WORD_RE = re.compile(r"\b(Questrade|RBC|Webull|IB)\b")


def _diag_account(name: str, accounts: Iterable[str]) -> str:
    """The account whose stage wrote work/<name> (the longest account
    name the file name starts with), '' when none."""
    best = ""
    for a in accounts:
        if name.startswith(f"{a}_") and len(a) > len(best):
            best = a
    return best


def rename_hints(root: Path, cfg: Dict[str, Any],
                 events: Iterable[Dict[str, Any]] = (),
                 account: Optional[str] = None) -> List[Dict[str, Any]]:
    """The look-alike renames the brokers' exports show (_LOOKS_RE,
    _DATED_HINT_RE in the run's work/*.diag) that neither the books (a
    rename event of OLD to NEW) nor ticker.map (a line that joins or
    dates the two, a DISTINCT line) already answer: each with its
    account, date (the new symbol's first row: the latest date the
    change can have) and the `.tt` line `RENAME <date> OLD NEW` that
    books it."""
    from taxjson.lib import ticker_map_suggest as TS
    cache = Path(root) / "work"
    if not cache.is_dir():
        return []
    accounts = list(cfg.get("accounts") or {})
    st = TS.map_state(Path(root) / "ticker.map")
    booked = {(e["old"], e["new"]) for e in events}
    out: List[Dict[str, Any]] = []
    seen = set()
    for p in sorted(cache.glob("*.diag")):
        acct = _diag_account(p.name, accounts)
        if account and acct != account:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for head, cont in TS._messages(text):
            whole = TS._unquoted(" ".join([head] + [c.strip()
                                                     for c in cont]))
            found = []
            m = _LOOKS_RE.search(whole)
            mt = _LOOKS_TT_RE.search(whole)
            if m:
                found.append((m.group(1), m.group(2).upper(),
                              m.group(3).upper(), m.group(4)))
            elif mt:
                found.append((mt.group(1), mt.group(3).upper(),
                              mt.group(4).upper(), mt.group(2)))
            else:
                b = _BROKER_WORD_RE.search(whole)
                for m in _DATED_HINT_RE.finditer(whole):
                    found.append((b.group(1) if b else "broker",
                                  m.group(1).upper(), m.group(2).upper(),
                                  m.group(3)))
                for m in _DATED_HINT_TT_RE.finditer(whole):
                    found.append((b.group(1) if b else "broker",
                                  m.group(2).upper(), m.group(3).upper(),
                                  m.group(1)))
            for broker, old, new, day in found:
                if old == new or old in ("OLD", "A") \
                        or (acct, old, new) in seen:
                    continue
                seen.add((acct, old, new))
                if (old, new) in booked:
                    continue
                why = TS.already(TS.Suggestion(f"GLOBAL {old} {new}", "",
                                               ""), st)
                if why:
                    continue
                out.append({
                    "account": acct, "broker": broker, "old": old,
                    "new": new, "date": day,
                    "source": f"{broker} looks renamed",
                    "where": f"work/{p.name}",
                    "line": f"RENAME {day} {old} {new}",
                    "map_line": f"RENAME {old} {new} {day}"})
    out.sort(key=lambda h: (h["account"], h["date"], h["old"]))
    return out


def _project_country(cfg: Dict[str, Any]) -> str:
    """The project's canonical country ('' when missing or unknown: the
    late rows then decide only where both engines agree)."""
    from taxjson.lib.country import CountryError, settings_country
    try:
        return settings_country(cfg.get("settings"))
    except CountryError:
        return ""


def report(root: Path, cfg: Dict[str, Any],
           account: Optional[str] = None, *,
           undated: bool = True, hints: bool = True) -> Dict[str, Any]:
    """`taxjson renames`: every rename event with its date, source and
    the position / book cost it carried per account; every late trade
    in an old ticker with its resolution; the undated ticker.map renames
    (with the dated form when a broker event gives the date)."""
    root = Path(root)
    tmap, ren = _ticker_map(root)
    # The rename events the declarations make — .tt RENAME lines (the
    # form of record) and legacy dated ticker.map lines, merged as the
    # run merges them (lib/dated_events.resolve_renames).
    from taxjson.lib.dated_events import project_renames
    dated = project_renames(root, cfg.get("accounts") or {}, tmap)
    rows = _book_rows(root, cfg)
    base_cur = ((cfg.get("settings") or {}).get("base_currency") or "")
    events = rename_events(rows)
    by_acct: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_acct.setdefault(r["_acct"], []).append(r)
    snaps: Dict[int, Tuple[float, float]] = {}
    for acct, rs in by_acct.items():
        snaps.update(_walk_states(rs))
    accounts = cfg.get("accounts") or {}
    out_events = []
    for e in events:
        if account and account not in e["accounts"]:
            continue
        carried = []
        for r in e["rows"]:
            q, c = snaps.get(id(r), (0.0, 0.0))
            acct = r["_acct"]
            carried.append({
                "account": acct,
                "sheltered": (accounts.get(acct) or {}).get("type")
                != "taxable",
                "qty": round(q, 6),
                "qty_after": round(q * (e["ratio"] or 1.0), 6),
                "book_cost": round(c, 2)})
        lines = [w for dr in matching_lines(dated, e, ren)
                 for w in (dr.where,) + tuple(dr.also)]
        ids = e.get("source_ids") or []
        out_events.append({
            "date": e["date"], "old": e["old"], "new": e["new"],
            "ratio": e["ratio"], "sources": e["sources"],
            "source": next((x for x in (SOURCE_TT, SOURCE_MAP,
                                        SOURCE_IB_CONID, "broker")
                            if x in ids), "broker"),
            "ticker_map_lines": lines, "accounts": e["accounts"],
            "carried": carried, "currency": base_cur,
            "late": declared_late(dated, e, ren)})
    late = []
    for r, e in late_rows(rows, events, _project_country(cfg)):
        if account and r["_acct"] != account:
            continue
        choice = declared_late(dated, e, ren, account=r["_acct"],
                               held=r["_acct"] in e["accounts"])
        late.append({
            "account": r["_acct"], "date": str(r.get("date") or "")[:10],
            "action": r.get("action"), "symbol": r.get("symbol"),
            "qty": float(r.get("quantity") or 0.0),
            "renamed_to": e["new"], "rename_date": e["date"],
            "resolution": choice or "unresolved"})
    want_undated, undated = undated, []
    if tmap is not None and want_undated:
        raw: List[Dict[str, Any]] = []
        for acct in sorted(accounts):
            if account and acct != account:
                continue
            for r in _raw_rename_rows(root, acct):
                r = dict(r)
                r["_acct"] = acct
                raw.append(r)
        raw_events = rename_events(raw)
        undated_rn = set(getattr(tmap, "undated_rename", ()) or ())
        # GLOBAL (and undated RENAME) only: TOBASE / JOURNAL join two
        # listings of one security, they are not ticker changes.
        for frm, to in sorted(tmap.glob.items()):
            hint = [ev["date"] for ev in raw_events
                    if ev["old"] == frm and ev["new"] == to]
            undated.append({"rule": ("RENAME" if frm in undated_rn
                                     else "GLOBAL"),
                            "old": frm, "new": to,
                            "source": UNDATED_SOURCE,
                            "broker_dates": sorted(set(hint))})
    unresolved = sum(1 for x in late if x["resolution"] == "unresolved")
    suggested = (rename_hints(root, cfg, events, account) if hints
                 else [])
    unused = []
    for dr in unused_declarations(dated, events, ren):
        if account and dr.account and dr.account != account:
            continue
        unused.append({"date": dr.date, "old": dr.old, "new": dr.new,
                       "late": dr.late, "source": dr.source or SOURCE_MAP,
                       "where": [dr.where] + list(dr.also),
                       "line": dr.tt_line()})
    return {"renames": out_events, "late": late, "undated": undated,
            "suggested": suggested, "unused": unused,
            "unresolved": unresolved,
            "pending": unresolved + len(suggested) + len(unused)}


def unused_declarations(dated: Iterable[DatedRename],
                        events: List[Dict[str, Any]],
                        mapping: Optional[Dict[str, str]] = None
                        ) -> List[DatedRename]:
    """The declared renames (`dated`, merged events) no rename event of
    the books carries (matching_lines: OLD through the undated renames,
    the date within WINDOW_DAYS): a typo of the symbol, a date after the
    last OLD row, an account of the other kind — booked nowhere."""
    used = set()
    for e in events:
        for dr in matching_lines(dated, e, mapping):
            used.add(id(dr))
    return [dr for dr in dated if id(dr) not in used]


def unresolved_late(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The late trades no ticker.map line declares (`run --strict`)."""
    return [x for x in report(root, cfg, undated=False,
                              hints=False)["late"]
            if x["resolution"] == "unresolved"]


def _hang(d, text: str, indent: str = "", hang: str = "  ") -> None:
    """A paragraph of `d` (lib/out.Doc) with a hanging indent."""
    from taxjson.lib.out import wrap
    for ln in wrap(text, d.w, indent, hang):
        d.line(ln)


def render(doc: Dict[str, Any], width_: Optional[int] = None,
           pending: bool = False) -> List[str]:
    """The renames in the house layout (docs/output-style.md): a table of
    the dated renames with their source, then each one with the
    positions it carried and its late trades, the look-alike renames
    the exports show (suggested, with the `.tt` line that books each),
    the undated ticker.map renames; the lines to copy never wrapped;
    the last line says whether a declaration is needed. `pending`: only
    the unresolved late trades and the suggestions."""
    from taxjson.lib.out import Doc
    evs, late, undated = doc["renames"], doc["late"], doc["undated"]
    hints = doc.get("suggested") or []
    unused = doc.get("unused") or []
    if pending:
        bad = {(x["rename_date"], x["renamed_to"]) for x in late
               if x["resolution"] == "unresolved"}
        evs = [e for e in evs if (e["date"], e["new"]) in bad]
        undated = []
    d = Doc(f"RENAMES ({len(evs)})" if not pending else
            f"RENAMES — pending: {len(evs)} with an undeclared late trade, "
            f"{len(hints)} suggested"
            + (f", {len(unused)} declared but not booked" if unused else ""),
            width_=width_)
    d.blank()
    if not evs and not pending:
        d.para("No dated rename in the books.")
    if evs:
        body = []
        for e in evs:
            ratio = "1" if abs((e["ratio"] or 1.0) - 1.0) < 1e-12 else \
                f"{e['ratio']:g}"
            src = ", ".join(e["sources"])
            if e["ticker_map_lines"] and not any(
                    x == "ticker.map line" or x.startswith(".tt line")
                    for x in e["sources"]):
                src += f" (also {', '.join(e['ticker_map_lines'])})"
            body.append([e["date"], f"{e['old']} -> {e['new']}", ratio,
                         src, ", ".join(e["accounts"])])
        d.table(["DATE", "RENAME", "RATIO", "SOURCE", "ACCOUNTS"], body,
                aligns="<<><<", drop=(2, 4), key=(0, 1))
    for e in evs:
        d.blank()
        ratio = "" if abs((e["ratio"] or 1.0) - 1.0) < 1e-12 else \
            f" x{e['ratio']:g}"
        _hang(d, f"{e['date']}  {e['old']} -> {e['new']}{ratio}", "", "  ")
        for c in e["carried"]:
            cost = ("" if c["sheltered"] else
                    f"; book cost {c['book_cost']:,.2f} {e['currency']} "
                    f"carried")
            d.item(f"{c['account']}: held {c['qty']:g} {e['old']} "
                   f"-> {c['qty_after']:g} {e['new']}{cost}", "  ")
        mine = [x for x in late if x["symbol"] and x["rename_date"]
                == e["date"] and x["renamed_to"] == e["new"]]
        if mine:
            # (late= is per account: each row says its own when the
            # accounts chose differently)
            kinds = {x["resolution"] for x in mine}
            res = mine[0]["resolution"] if len(kinds) == 1 else (
                "unresolved" if "unresolved" in kinds else "mixed")
            word = {"fold": "declared the renamed shares (late=fold)",
                    "separate": "declared another security "
                                "(late=separate)",
                    "mixed": "declared per account"}.get(res, "UNRESOLVED")
            _hang(d, f"{len(mine)} trade(s) in {e['old']} on or "
                   f"after {e['date']}: {word}", "  ", "    ")
            for x in mine:
                d.line(f"    {x['date']}  {x['account']:<8} "
                       f"{x['action']:<8} {x['symbol']} {x['qty']:+g}"
                       + (f"  ({x['resolution']})" if len(kinds) > 1
                          else ""))
            if res == "unresolved":
                _hang(d, "Declare one in a .tt file of the account — the "
                       f"broker still books the renamed shares as "
                       f"{e['old']} (late=fold), or another company now "
                       f"uses {e['old']} (late=separate):", "  ", "    ")
                d.line(f"    RENAME {e['date']} {e['old']} {e['new']} "
                       f"late=fold")
                d.line(f"    RENAME {e['date']} {e['old']} {e['new']} "
                       f"late=separate")
                _hang(d, "Until then they are a separate security and "
                       "`taxjson run --strict` stops.", "  ", "    ")
    if unused:
        d.section(f"DECLARED, NOT BOOKED ({len(unused)})")
        d.para("No account's books hold the old symbol before the date "
               "(in an account of the line's kind: securities or crypto), "
               "so the line books nothing. Check the symbols (a typo?) and "
               "the date, or delete the line.", "  ")
        for u in unused:
            d.blank()
            d.item(f"{u['old']} -> {u['new']} on {u['date']} "
                   f"({', '.join(u['where'])})", "  ")
            d.line(f"      {u['line']}")
    if hints:
        d.section(f"SUGGESTED ({len(hints)}): look-alike renames the "
                  f"exports show")
        d.para("Not booked: the old symbol stops with shares still open "
               "and the new one starts with a sale, under one security "
               "name. If it is one security, add the .tt line to a .tt file "
               "in the account's inputs/ folder (a ticker change is a dated "
               "event) — the date is the new symbol's first row; use the "
               "broker's change date if you know it — then re-run "
               "`taxjson run`.", "  ")
        for h in hints:
            d.blank()
            d.item(f"{h['old']} -> {h['new']} ({h['account'] or '?'}, "
                   f"{h['source']}; {h['where']})", "  ")
            d.line(f"      {h['line']}")
    if undated:
        d.section(f"UNDATED RENAMES IN ticker.map ({len(undated)})")
        d.para(f"Source: {UNDATED_SOURCE}. Every row of the old symbol, at "
               f"any date, is the new one.", "  ")
        for u in undated:
            d.line(f"  {u['rule']} {u['old']} {u['new']}")
            for dd in u["broker_dates"]:
                _hang(d, f"the broker books this change on {dd}; the dated "
                       f"form (a .tt line, in place of the ticker.map "
                       f"line) is:", "    ", "      ")
                d.line(f"      RENAME {dd} {u['old']} {u['new']}")
    d.blank()
    n = doc["unresolved"]
    msg = (f"{n} trade(s) in an old ticker after its rename need a "
           f"declaration (a .tt RENAME line with late=)." if n else
           "No unresolved trade in an old ticker after its rename.")
    if hints:
        msg += f" {len(hints)} look-alike rename(s) to settle."
    if unused:
        msg += f" {len(unused)} declared rename(s) book nothing."
    d.para(msg)
    return d.lines()

"""What a row the broker marks CLOSE closes: the one rule every reader
shares (backlog TJ-5349; tax-logic CA-ACB-11, CA-OPT-12).

A row the broker marks CLOSE (IB code C; RBC "CLOSE CONTRACT", and its
share "COVER SHORT." words) never opens a position. The units the
account's own position backs close it. The rest closed a position
opened before the files: missing history, reported by hand, adding no
long and no written contract until the opening is supplied. On a day
with no clock (the row's `no_clock` fact: RBC prints no time), that
day's own OPEN rows of the other direction back the close first: the
parser puts them ahead of it (lib/brokerages/rbc_direct
`_order_by_open_close`), so every reader sees one order, and
`close_backing` says how many of the backed units rest on them.

`close_backing` is the decision; `CloseWalk` keeps what it needs between
rows (the position each broker order began from, a no-clock day's OPEN
rows). The engine (engine/ca/router), the option close check, `tjs
option-yield`, `tjs stats`, the option boundary and `tjs
find-missing-history` all call it: none decides alone. `unbacked_close`
is its yes / no. Out of lib/missing_history, which the option close
check (lib/option_close_check) read for these while missing_history's
callers reach that check (step 8 of the architecture plan: lib's import
cycle)."""

from __future__ import annotations

import re
from typing import Any, Dict, NamedTuple, Optional, Tuple


_EPS = 1e-9

# How a run says a marked CLOSE's unbacked units (lib/option_close_check,
# lib/pipeline.prepare_books): `tjs run` echoes the line, and `tjs run
# --strict` stops on it.
ATTENTION_UNBACKED = "warning: ATTENTION: unbacked close: "

# The row kind of the opening the Canadian books give the units of a marked
# CLOSE row that the account's position cannot back (engine/ca/router
# `_unbacked_opening`): an OPENING_BALANCE row of an unknown cost, as a .tt
# `OPENING ... cost=unknown` line's, that stands at its close row's own
# moment (corporate_timeline's Canadian ladder), never at the start of the
# day. The engine's own row: no book and no file holds one.
OPENING_KIND = 'unbacked_close'


def is_unbacked_opening(tx: Any) -> bool:
    """The engine's opening for a CLOSE row's unbacked units."""
    return (getattr(tx, 'action', '') == 'OPENING_BALANCE'
            and (getattr(tx, 'type', '') or '') == OPENING_KIND)


# The gains document's key for the openings a Canadian run gave the
# unbacked units of marked CLOSE rows (engine/ca/router
# `_unbacked_opening`): [{account, symbol, date, quantity (signed, as the
# opening), close (the close row's id)}], units the books counted that no
# row of the merged books carries. Every view of the books' positions on
# a date adds them (lib/missing_history.openings_from_log), as it adds a
# .tt OPENING cost=unknown line's. Left out when there is none.
OPENINGS_KEY = "unbacked_close_openings"

# The broker's short-sale words in a share row's description: RBC writes
# "<name> SHORT. UNSOLICITED ..." on a short sale and "COVER SHORT." on
# the purchase that closes it.
SHORT_WORDS_RE = re.compile(r'(?<![A-Z])SHORT\.(?=\s|$)')


def _field(tx: Any, name: str) -> Any:
    """A row's field, of a TaxTransaction (or a stand-in with attributes)
    or of a book's dict row."""
    return tx.get(name) if isinstance(tx, dict) else getattr(tx, name, None)


def close_marks(tx: Any) -> Tuple[str, ...]:
    """What the broker says the row does, as the open/close codes: its
    `open_close` evidence (IB's Trades code, RBC's OPEN / CLOSE CONTRACT),
    else its description's short words: a purchase marked "COVER SHORT."
    closes ('C'), a sale marked "SHORT." opens ('O'). () when the broker
    says nothing. A dict row reads the same."""
    codes = tuple(c for c in re.split(r'[;,\s]+', str(_field(tx, 'open_close') or '').upper())
                  if c in ('O', 'C'))
    if codes:
        return codes
    desc = str(_field(tx, 'description') or '').upper()
    if 'SHORT.' not in desc or not SHORT_WORDS_RE.search(desc):
        return ()
    q = float(_field(tx, 'quantity') or 0)
    if q > 0:
        return ('C',)
    return () if 'COVER SHORT.' in desc or not q else ('O',)


class Backing(NamedTuple):
    """A row's units, read as a close: `backed`, the units the position
    held before the row closes (the `day_open` units among them included);
    `day_open`, the backed units that rest on a no-clock day's own OPEN
    rows of the other direction (the day's order is the broker's words,
    not a clock); `unbacked`, the units that closed a position opened
    before the files. A row with no C code backs what the position holds
    and has no unbacked units: the rest of it opens."""
    backed: float
    day_open: float
    unbacked: float


NOT_A_CLOSE = Backing(0.0, 0.0, 0.0)


def no_clock(tx: Any) -> bool:
    """The row's parser says its source prints no time of day (the
    `no_clock` evidence fact: RBC), so its place in its day is not a
    clock's."""
    return bool(_field(tx, 'no_clock'))


def close_backing(tx: Any, prev: float, order_prev: Optional[float] = None,
                  day_open: float = 0.0) -> Backing:
    """The one decision. `prev`: the position before the row (signed: +
    held, - written or short); `order_prev`: the position its broker
    ORDER began from (IB stamps the order's code on every fill of it, so
    the second fill of a "C;O" order that closed 1 and opened 1 carries
    C;O too); `day_open`: the units the row's no-clock day opened in the
    other direction before it and no earlier close of the day used.

    A row coded C alone: the units past the position are unbacked. A row
    coded C;O: unbacked whole when its order began with nothing to close
    (the broker does not say how much of the fill closed), else what is
    past the position opens (no unbacked units). The mirror for a buy."""
    q = float(_field(tx, 'quantity') or 0)
    if not q:
        return NOT_A_CLOSE
    held = max(prev if q < 0 else -prev, 0.0)
    units = abs(q)
    backed = min(units, held)
    codes = close_marks(tx)
    if 'C' not in codes:
        return Backing(backed, 0.0, 0.0)
    if order_prev is None:
        order_prev = prev
    if 'O' in codes:
        began = order_prev if q < 0 else -order_prev
        if began <= _EPS:
            return Backing(0.0, 0.0, units)
        return Backing(backed, min(backed, max(day_open, 0.0)), 0.0)
    rest = units - backed
    if rest <= _EPS:
        rest = 0.0
    return Backing(backed, min(backed, max(day_open, 0.0)), rest)


def unbacked_close(tx: Any, prev: float, order_prev: Optional[float] = None
                   ) -> bool:
    """The broker codes this trade as (partly) CLOSING (IB code C) but
    the position the data holds cannot back the close (`close_backing`
    finds unbacked units): a sale coded C alone that sells more than is
    held, a sale coded C;O with no long held when its ORDER began, and
    the mirror cases for a buy. What it closed was opened before the
    data: missing history, never a new short or a new long (audit
    S013-00)."""
    return close_backing(tx, prev, order_prev).unbacked > 0.0


class OrderStarts:
    """The position each broker ORDER began from, for close_backing:
    consecutive rows of one (symbol, account) with the same clock stamp,
    direction and open/close code are fills of one order."""

    def __init__(self) -> None:
        self._last: Dict[Any, Tuple[Any, float]] = {}

    def prev(self, key: Any, tx: Any, prev: float) -> float:
        q = float(_field(tx, 'quantity') or 0)
        sig = (_field(tx, 'date'), _field(tx, 'time'), q > 0, close_marks(tx))
        last = self._last.get(key)
        if last is not None and last[0] == sig:
            return last[1]
        self._last[key] = (sig, prev)
        return prev


class CloseWalk:
    """`close_backing` over a walk's rows in order: per position key (a
    reader's own: an account and a symbol) the position each order began
    from, and a no-clock day's OPEN units still to back a close. A
    reader hands each trade row with the position before it and books
    what comes back."""

    def __init__(self) -> None:
        self._orders = OrderStarts()
        # {key: (date, units opened long that day, units opened short)}
        self._day: Dict[Any, Tuple[str, float, float]] = {}

    def read(self, key: Any, tx: Any, prev: float) -> Backing:
        """The row's backing, from the position `prev` before it."""
        q = float(_field(tx, 'quantity') or 0)
        order_prev = self._orders.prev(key, tx, prev)
        if not no_clock(tx):
            return close_backing(tx, prev, order_prev)
        date = str(_field(tx, 'date') or '')
        d, opened_long, opened_short = self._day.get(key, (date, 0.0, 0.0))
        if d != date:
            opened_long = opened_short = 0.0
        b = close_backing(tx, prev, order_prev,
                          opened_long if q < 0 else opened_short)
        if q < 0:
            opened_long -= b.day_open
        else:
            opened_short -= b.day_open
        if close_marks(tx) == ('O',):
            if q > 0:
                opened_long += q
            else:
                opened_short -= q
        self._day[key] = (date, opened_long, opened_short)
        return b

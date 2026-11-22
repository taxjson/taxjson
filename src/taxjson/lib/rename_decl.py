"""A dated rename's declaration: the DatedRename a .tt `RENAME <date>
OLD NEW` line or a legacy ticker.map `RENAME OLD NEW YYYY-MM-DD` line
declares, its tail's reader (parse_rename_tail), its source and kind
values, the window inside which two dates are one event (WINDOW_DAYS,
_days), and the effective map's notes that carry a merged event's
facts back (TT_ORIGIN_RE, EVENT_META, apply_meta). Out of lib/renames
and lib/dated_events, which the ticker.map parser (lib/ticker_map_rules)
read for these while both read the map (step 8 of the architecture
plan: lib's import cycle)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


LATE_FOLD = "fold"
LATE_SEPARATE = "separate"
LATE_CHOICES = (LATE_FOLD, LATE_SEPARATE)


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


def _days(a: str, b: str) -> Optional[int]:
    try:
        return abs((datetime.strptime(str(a)[:10], "%Y-%m-%d")
                    - datetime.strptime(str(b)[:10], "%Y-%m-%d")).days)
    except ValueError:
        return None


# The note the run writes after a .tt RENAME line in its effective map:
# `# .tt: <inputs/acct/file.tt:N> | <the line as written>`.
# The merged event's facts follow in a JSON tail: `... || {json}`.
TT_ORIGIN_RE = re.compile(r"^\s*\.tt: (.+?\.tt:\d+)(?: \| (.*?))?"
                          r"(?: \|\| (\{.*\}))?\s*$", re.IGNORECASE)
# The facts of a legacy ticker.map line's event that .tt lines of the
# same change merged into (its date, its late= choices, its other
# declarations): a comment line of the effective map, `# dated-event:
# {json}` (the line itself stays as the user wrote it, above).
EVENT_META = "# dated-event: "


def apply_meta(dated: List[Any], metas: List[Dict[str, Any]],
               origin: Dict[int, Dict[str, Any]]) -> List[Any]:
    """The effective map's dated lines with their merged facts: `origin`
    {index in dated: the JSON tail of its .tt origin note}, `metas` the
    EVENT_META lines (a legacy map line's event)."""
    from dataclasses import replace
    out = []
    for i, dr in enumerate(dated):
        m = origin.get(i)
        if m is None:
            m = next((x for x in metas if (x.get("old"), x.get("new"),
                                           x.get("written"))
                      == (dr.old, dr.new, dr.date)), None)
            if m is not None:
                dr = replace(dr, date=str(m.get("date") or dr.date),
                             late=str(m.get("late") or ""))
        if isinstance(m, dict):
            lates = m.get("lates") if isinstance(m.get("lates"), dict) \
                else {}
            dr = replace(
                dr, account=str(m.get("account") or ""),
                kind=str(m.get("kind") or ""),
                lates=tuple(sorted((str(a), str(v)) for a, v in
                                   lates.items())),
                also=tuple(str(w) for w in (m.get("also") or [])
                           if isinstance(m.get("also"), list)))
        out.append(dr)
    return out

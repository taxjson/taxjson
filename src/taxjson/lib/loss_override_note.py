"""The ALLOWLOSS line: its keyword and form, one line read
(parse_line), and the sentence a row carrying a `loss_override` shows
(the form-export rows, `tjs audit`, `wash-sales --explain`). Out of
lib/loss_overrides, which reads the project's ALLOWLOSS lines and
applies them, so the trace format the engine imports and the .tt
reader (lib/tt_format) do not import that reader (step 8 of the
architecture plan: lib's import cycle)."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Dict, Optional

from taxjson.model import money as M

KEYWORD = "ALLOWLOSS"


def note_text(note: Dict[str, Any], country: str) -> str:
    """One sentence for a row carrying `loss_override` (entry_note):
    the form-export rows, `tjs audit`, `wash-sales --explain`. The
    figure is the row's own share of the sale's denial."""
    us = country in ("us", "usa")
    dis = float(note.get("would_disallow") or 0.0)
    perm = float(note.get("would_permanent") or 0.0)
    fate = ("" if perm < 0.005 else " for good" if dis - perm < 0.005
            else f" ({_money(perm)} of it for good)")
    return (f"filing position: {KEYWORD} {note.get('where')}, the "
            f"{'wash-sale' if us else 'superficial-loss'} rule would "
            f"{'disallow' if us else 'deny'} {_money(dis)}{fate}; the "
            f"loss is claimed in full. Reason: \"{note.get('reason')}\"")


def _money(x) -> str:
    return f"{M.shown(float(x or 0), 2):,.2f}"


FORM = 'ALLOWLOSS <sale date> <symbol> [<qty>] reason="<text>"'


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REASON_RE = re.compile(r'reason\s*=\s*"([^"]*)"')


def parse_line(line: str, source: str = "") -> Optional[Dict[str, Any]]:
    """`ALLOWLOSS <date> <symbol> [<qty>] reason="<text>"` -> {date,
    symbol, qty (None when not given), reason, line}, or None when the
    line is not an ALLOWLOSS line. Raises ValueError naming the form on
    a malformed one. A `#` inside the quoted reason is text; after it, a
    comment."""
    where = f"{source}: " if source else ""
    # The comment starts at the first `#` outside a quoted text: a
    # reason="..." written after it is part of the comment, never the
    # line's reason (pre-release review: `ALLOWLOSS ... # reason="x"`
    # was accepted).
    body = line[:_comment_start(line)]
    m = _REASON_RE.search(body)
    reason = m.group(1).strip() if m else None
    rest = (body[:m.start()] + " " + body[m.end():]) if m else body
    parts = rest.split()
    if not parts or parts[0] != KEYWORD:
        return None
    shown = line.strip()
    if '"' in rest:
        raise ValueError(
            f"{where}malformed {KEYWORD} line — the reason is one quoted "
            f"text, reason=\"...\" (expected `{FORM}`): {shown!r}")
    if not reason:
        raise ValueError(
            f"{where}{KEYWORD} needs a reason: say why you take this "
            f"position, reason=\"...\" (expected `{FORM}`): {shown!r}")
    toks = parts[1:]
    if len(toks) >= 2 and not _is_date(toks[0]) and _is_date(toks[1]):
        raise ValueError(
            f"{where}malformed {KEYWORD} line — a .tt line is date first: "
            f"`{KEYWORD} {toks[1]} {toks[0].upper()}"
            f"{' ' + ' '.join(toks[2:]) if toks[2:] else ''} reason=...` "
            f"(expected `{FORM}`): {shown!r}")
    if len(toks) not in (2, 3):
        raise ValueError(
            f"{where}malformed {KEYWORD} line — expected `{FORM}`: "
            f"{shown!r}")
    date, symbol = toks[0], toks[1].upper()
    if not _is_date(date):
        raise ValueError(
            f"{where}{KEYWORD} date {date!r} is not a valid YYYY-MM-DD "
            f"date (expected `{FORM}`): {shown!r}")
    from datetime import date as _date
    if date > _date.today().isoformat():
        raise ValueError(
            f"{where}{KEYWORD} line is dated {date}, in the future — it "
            f"names a sale that has happened (check the year): {shown!r}")
    qty = None
    if len(toks) == 3:
        from taxjson.lib.brokerages.base import parse_strict_number
        try:
            # Strict, like a .tt row's quantity: `1,000` is a thousand,
            # a decimal comma (`1,5`) is refused, never read as 15
            # (2026-10 audit #87).
            if not re.fullmatch(r"[0-9.,eE+-]+", toks[2]):
                raise ValueError(toks[2])   # no `$`, no parentheses
            qty = abs(parse_strict_number(toks[2], field="quantity"))
        except ValueError:
            qty = None
        if qty is None or not math.isfinite(qty) or qty == 0:
            raise ValueError(
                f"{where}{KEYWORD} quantity {toks[2]!r} is not a number of "
                f"units sold (expected `{FORM}`): {shown!r}")
    from taxjson.lib.brokerages.base import canonical_ca_listing
    from taxjson.lib.option_symbols import is_option_symbol
    if not is_option_symbol(symbol):
        symbol = canonical_ca_listing(symbol, "") or symbol
    return {"date": date, "symbol": symbol, "qty": qty, "reason": reason,
            "line": shown}


def _comment_start(line: str) -> int:
    """Index of the first `#` outside double quotes (len(line) when
    none)."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == "#" and not quoted:
            return i
    return len(line)


def _is_date(tok: str) -> bool:
    if not _DATE_RE.match(tok or ""):
        return False
    try:
        datetime.strptime(tok, "%Y-%m-%d")
    except ValueError:
        return False
    return True

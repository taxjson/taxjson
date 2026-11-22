"""The project's base-currency rates (work/to_base.csv) as the crypto
readers price with them: load_rates reads the file into a RateTable,
Rates looks a currency up on a day (the latest rate on or before it,
within RATE_LOOKBACK_DAYS). Out of lib/crypto_sends, which the crypto parsers'
shared helpers imported for it (step 8 of the architecture plan: lib's
import cycle)."""

from __future__ import annotations

import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from taxjson.lib.stage_msg import emit_line


# ---------------------------------------------------------------- rates
class RateTable(dict):
    """load_rates' {currency: {date: (rate, source)}}, with `target`:
    the currency every rate is TO (the file's TO column; None for an
    empty file) — `Rates` refuses a table whose target is not its base —
    and `published`: {currency: {date: the date the rate was published}}
    for a date the file filled from an earlier day (its seventh column;
    backlog TJ-0462)."""
    target: Optional[str] = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.published: Dict[str, Dict[str, str]] = {}


def load_rates(path: Path, base: Optional[str] = None) -> RateTable:
    """{currency: {date: (rate, source)}} from work/to_base.csv
    (`DATE TIME FROM TO RATE [SOURCE]`), read like convert-currency
    reads it (re-audit A2-0999): a rate that parses but is not a
    positive finite number (NaN, 0, a negative) is refused — it used to
    be skipped and the send priced at an earlier day's rate without a
    word; an unreadable rate is skipped with a warning; the FIRST row of
    a currency and date wins (the noon row, not today's spot row).
    Every rate is to one currency: `base` when given, else the first
    row's; a row TO another is refused, naming it — read by its FROM
    currency alone, a USD rate to EUR priced a CAD project's send
    (backlog TJ-0502)."""
    out = RateTable()
    if base:
        out.target = base.upper()
    if not path.exists() and not path.is_symlink():
        return out
    # An unreadable rates file is one line naming it (re-audit A2-0790);
    # a BOM no longer hides the first rate (A2-1411).
    try:
        if not path.is_file():
            raise IsADirectoryError(0, "not a regular file")
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        why = (e.strerror if isinstance(e, OSError) else None) or e
        raise ValueError(f"cannot read the rates file {path} ({why}) — "
                         f"re-run `tjs run` to rebuild it.") from None
    bad: List[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if len(parts) < 5 or not re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
            continue
        try:
            rate = float(parts[4])
        except ValueError:
            bad.append(f"line {n}: {line.strip()!r}")
            continue
        if not (rate > 0 and math.isfinite(rate)):
            raise ValueError(
                f"rates file {path}, line {n}: rate {parts[4]!r} for "
                f"{parts[2]}->{parts[3]} on {parts[0]} is not a positive "
                f"finite number — refusing to value a send with it. Fix "
                f"or regenerate the rates file (`tjs run`).")
        to = parts[3].upper()
        if out.target is None:
            out.target = to
        elif to != out.target:
            raise ValueError(
                f"rates file {path}, line {n}: the {parts[2]} rate on "
                f"{parts[0]} is to {to}, and "
                + (f"this project's base currency is {out.target}"
                   if base else f"the file's rates are to {out.target}")
                + " — refusing to value a send with it. Regenerate the "
                  "rates file (`tjs run`).")
        day = out.setdefault(parts[2].upper(), {})
        if parts[0] not in day:
            day[parts[0]] = (rate, parts[5].lower() if len(parts) > 5
                             else "")
            if len(parts) > 6 and re.match(r"^\d{4}-\d{2}-\d{2}$",
                                           parts[6]) \
                    and parts[6] < parts[0]:
                out.published.setdefault(parts[2].upper(), {})[
                    parts[0]] = parts[6]
    if bad:
        emit_line(f"tjs crypto-sends: warning: {path.name}: skipped "
                  f"{len(bad)} line(s) with an unreadable rate (e.g. "
                  f"{bad[0]}).", file=sys.stderr)
    return out


# The days before a send's date a rate row may come from: the
# conversion stage's own lookback (taxjson_convert_currency, 5 days on
# top of the rates file's 7-day carry-forward).
RATE_LOOKBACK_DAYS = 5


class Rates:
    def __init__(self, table, base: str):
        self.table = table
        self.base = base.upper()
        self._sorted: Dict[str, List[str]] = {}
        # A table load_rates read names the currency its rates are to:
        # one to another currency than the base would price every send
        # in it (backlog TJ-0502).
        target = getattr(table, "target", None)
        if target and target != self.base:
            raise ValueError(
                f"the rates file's rates are to {target}, and this "
                f"project's base currency is {self.base} — refusing to "
                f"value a send with them. Regenerate the rates file "
                f"(`tjs run`).")

    def get(self, cur: str, on: str) -> Optional[Tuple[float, str, str]]:
        """(rate, rate date, label) of the latest rate on `on` or in the
        RATE_LOOKBACK_DAYS before it — convert-currency's lookback
        (CA-FX-02 / US-FX-02). None (the send stays unpriced, the pool
        row unrated) when there is none: an unbounded "latest before"
        priced a June send at a January rate (re-audit A2-0414)."""
        cur = cur.upper()
        if cur == self.base:
            return 1.0, on, ""
        days = self.table.get(cur) or {}
        if cur not in self._sorted:
            self._sorted[cur] = sorted(days)
        keys = self._sorted[cur]
        import bisect
        i = bisect.bisect_right(keys, on)
        if i == 0:
            return None
        d = keys[i - 1]
        try:
            oldest = (datetime.strptime(on[:10], "%Y-%m-%d")
                      - timedelta(days=RATE_LOOKBACK_DAYS)
                      ).strftime("%Y-%m-%d")
        except ValueError:
            return None
        if d < oldest:
            return None
        rate, src = days[d]
        label = {"boc": "Bank of Canada", "irs": "IRS"}.get(src, src or "")
        # The day the rate was published, when the file filled `d` from
        # it (backlog TJ-0462: a Sunday showed as the Bank's own date).
        pub = ((getattr(self.table, "published", None) or {}).get(cur)
               or {}).get(d)
        return rate, pub or d, label

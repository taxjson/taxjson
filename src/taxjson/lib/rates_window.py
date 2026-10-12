"""The first date a project's FX rates must cover (`taxjson run`'s rates
stage, stage_currency_rates).

The rates stage runs before any export is parsed, so it used to ask
taxjson-to-base-curr for every date from 2000-01-01: a 2024 project
fetched 24 years of Bank of Canada rates, and Yahoo Finance closes for
the years before the Bank's series (1,322 dates before 2007 for the
demo). The rows a project converts all come from its files, so the
window starts at the earliest date written in them:

  * every broker export and .tt file of the configured accounts
    (inputs/<account>/, or the shared inputs_dir), the year's slips
    (inputs/slips/) and its positions snapshots (holdings/);
  * the dates are found by their shape, not by parsing each export:
    2024-01-16 / 2024/01/16, 16-01-2024 / 01/16/2024 (either order),
    January 16, 2024 / 16 Jan 2024, and 20240116 in a file that holds
    no date of the other shapes (an IB Flex export). A number that only
    looks like a date makes the window start earlier, never later;
  * the start is never later than January 1 of the project year, and
    PAD_DAYS earlier still: a row on a Monday takes the Friday's rate
    (taxjson-to-base-curr fills a weekend or holiday from the business
    day before, up to 7 days, and the conversion looks back 5 days).

A file that cannot be read gives no date (the conversion stage still
names any row without a rate). The window's end stays today: a later
export brings rows up to the day it is downloaded.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

# Days before the earliest date: the weekend / holiday fill (7) and the
# conversion's look-back (5), with room for a long holiday.
PAD_DAYS = 14
# Years outside this range are not a date of the books (a price, an id).
_FIRST_YEAR = 1970

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
           "oct", "nov", "dec")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_YMD = re.compile(rb"(?<![0-9])((?:19|20)[0-9]{2})[-/.]([0-9]{1,2})[-/.]"
                  rb"([0-9]{1,2})(?![0-9])")
_DMY = re.compile(rb"(?<![0-9])([0-9]{1,2})[-/.]([0-9]{1,2})[-/.]"
                  rb"((?:19|20)[0-9]{2})(?![0-9])")
_MON_D_Y = re.compile(rb"(?i)(?<![a-z])" + _MON.encode()
                      + rb"\s+([0-9]{1,2}),?\s+((?:19|20)[0-9]{2})"
                        rb"(?![0-9])")
_D_MON_Y = re.compile(rb"(?i)(?<![0-9])([0-9]{1,2})[-\s]" + _MON.encode()
                      + rb"[-\s,]+((?:19|20)[0-9]{2})(?![0-9])")
_COMPACT = re.compile(rb"(?<![0-9A-Za-z])((?:19|20)[0-9]{2})(0[1-9]|1[0-2])"
                      rb"(0[1-9]|[12][0-9]|3[01])(?![0-9])")


def _date(y: int, m: int, d: int, last_year: int) -> Optional[date]:
    if not _FIRST_YEAR <= y <= last_year:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def earliest_in_text(data: bytes, last_year: int) -> Optional[date]:
    """The earliest date written in `data` (module docstring), or
    None."""
    found: List[date] = []

    def add(x: Optional[date]) -> None:
        if x is not None:
            found.append(x)
    for m in _YMD.finditer(data):
        add(_date(int(m[1]), int(m[2]), int(m[3]), last_year))
    for m in _DMY.finditer(data):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        # Day-first or month-first: whichever reads as a date (both:
        # the earlier, so the window never starts too late).
        for x in (_date(y, b, a, last_year), _date(y, a, b, last_year)):
            add(x)
    for m in _MON_D_Y.finditer(data):
        add(_date(int(m[3]), _MONTHS.index(m[1].decode().lower()) + 1,
                  int(m[2]), last_year))
    for m in _D_MON_Y.finditer(data):
        add(_date(int(m[3]), _MONTHS.index(m[2].decode().lower()) + 1,
                  int(m[1]), last_year))
    if not found:
        # A file with dates in no other shape (an IB Flex export writes
        # 20240116): only then read 8-digit runs as dates — elsewhere
        # they are ids and amounts.
        for m in _COMPACT.finditer(data):
            add(_date(int(m[1]), int(m[2]), int(m[3]), last_year))
    return min(found) if found else None


def earliest_in_files(paths: Iterable[Path],
                      today: Optional[date] = None) -> Optional[date]:
    """The earliest date written in any of `paths` (unreadable files
    skipped), or None."""
    last_year = (today or date.today()).year + 1
    best: Optional[date] = None
    for p in paths:
        try:
            data = Path(p).read_bytes()
        except OSError:
            continue
        d = earliest_in_text(data, last_year)
        if d is not None and (best is None or d < best):
            best = d
    return best


def window_start(year: Optional[int], paths: Iterable[Path],
                 today: Optional[date] = None) -> date:
    """The first date the rates must cover: the earliest date in
    `paths`, or January 1 of `year` when that is earlier, less
    PAD_DAYS."""
    today = today or date.today()
    first = earliest_in_files(paths, today)
    if isinstance(year, int) and not isinstance(year, bool) \
            and _FIRST_YEAR <= year <= today.year + 1:
        jan1 = date(year, 1, 1)
        first = jan1 if first is None or jan1 < first else first
    if first is None:
        first = date(today.year, 1, 1)
    return first - timedelta(days=PAD_DAYS)

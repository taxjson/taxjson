"""Strict helpers shared by the Kraken and Coinbase parsers.

Two jobs, both about refusing to produce a quietly-wrong number:

* `strict_money` — parse a CSV amount, accepting the currency-prefixed
  forms exchanges actually ship (`$1,234.50`, `CA$4.00`, `US$-3`,
  `(12.00)`), and RAISING on anything else. `BaseBrokerage.clean_number`
  returns 0.0 for anything it can't read, so `CA$4.00` became a $0
  basis/proceeds row without a word.

* `utc_to_local` — Kraken and Coinbase stamp every row in UTC. A
  Canadian taxpayer's trade/income DATE is the local calendar date, so
  a fill at 2026-01-01 03:00 UTC belongs to 2025-12-31 in Toronto (and
  to the 2025 return). Rows are converted to America/Toronto local
  time; set the `TAXJSON_LOCAL_TZ` environment variable to another
  IANA zone name (e.g. America/Vancouver) if you live elsewhere.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

DEFAULT_LOCAL_TZ = 'America/Toronto'

# Currency markers an exchange may glue to an amount. Longest first so
# `CA$` is not half-eaten by `A$`/`$`.
_MONEY_PREFIXES = ('CA$', 'US$', 'C$', 'A$', '$', '€', '£')
# A first group of 0 is a decimal comma ('0,125'), never thousands
# (audit S055-08).
_THOUSANDS_OK = re.compile(r'^[1-9]\d{0,2}(,\d{3})+(\.\d*)?$')
# What is left once sign, currency and thousands commas are stripped:
# ASCII digits with at most one decimal point, and an optional exponent
# (tiny crypto quantities: 1e-8). float() also took '1_000' and
# non-ASCII digits (R1-114).
_PLAIN_NUMBER = re.compile(r'^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$', re.ASCII)


def strict_money(raw, what: str = 'amount', context: str = '') -> float:
    """Parse one numeric CSV cell. Empty -> 0.0. `(x)` is negative.
    Commas must be thousands separators (a European `1.234,56` is
    refused, never read as 1.23456). Raises ValueError, naming the
    field and `context`, on anything unparseable."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s:
        return 0.0
    orig = s
    neg = False
    signs = 0          # at most ONE of (x), a leading -/+, a -/+ after $
    if s.startswith('(') and s.endswith(')'):
        neg = True
        signs += 1
        s = s[1:-1].strip()
    if s.startswith('-'):
        neg = not neg
        signs += 1
        s = s[1:].strip()
    elif s.startswith('+'):
        signs += 1
        s = s[1:].strip()
    for p in _MONEY_PREFIXES:
        if s.upper().startswith(p):
            s = s[len(p):].strip()
            break
    # A sign AFTER the currency marker (`CA$-4.00`, `$-4.00`).
    if s[:1] in ('-', '+'):
        neg = neg != (s[0] == '-')
        signs += 1
        s = s[1:].strip()
    if signs > 1:
        # '--5', '(-5)', '-$-5' used to cancel into +5 (R1-114).
        raise ValueError(
            f"unparseable {what} {orig!r}{_ctx(context)}: more than one "
            f"sign marker — refusing to guess the sign.")
    if ',' in s:
        if not _THOUSANDS_OK.match(s):
            raise ValueError(
                f"unparseable {what} {orig!r}{_ctx(context)}: commas are "
                f"only accepted as thousands separators (1,234.56).")
        s = s.replace(',', '')
    if not _PLAIN_NUMBER.match(s):
        # float() also takes '1_000', non-ASCII digits and 'nan'.
        raise ValueError(
            f"unparseable {what} {orig!r}{_ctx(context)} — refusing to "
            f"book it as 0. Fix the cell or report the new export "
            f"format.")
    try:
        v = float(s)
    except ValueError:
        raise ValueError(
            f"unparseable {what} {orig!r}{_ctx(context)} — refusing to "
            f"book it as 0. Fix the cell or report the new export "
            f"format.") from None
    if v != v or v in (float('inf'), float('-inf')):
        raise ValueError(f"non-finite {what} {orig!r}{_ctx(context)}")
    return -v if neg else v


def _ctx(context: str) -> str:
    return f" ({context})" if context else ''


def local_tz_name() -> str:
    return (os.environ.get('TAXJSON_LOCAL_TZ') or DEFAULT_LOCAL_TZ).strip()


def _eastern_offset_hours(dt_utc: datetime) -> int:
    """UTC offset for North-American Eastern time from the DST rules —
    the fallback when the platform has no tz database (Windows without
    the `tzdata` package). 2007+: second Sunday of March 07:00 UTC to
    first Sunday of November 06:00 UTC; before: first Sunday of April
    to last Sunday of October."""
    y = dt_utc.year

    def nth_sunday(month, n):
        d = datetime(y, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    if y >= 2007:
        start = nth_sunday(3, 2) + timedelta(hours=7)
        end = nth_sunday(11, 1) + timedelta(hours=6)
    else:
        start = nth_sunday(4, 1) + timedelta(hours=7)
        last_oct = datetime(y, 10, 31)
        last_oct -= timedelta(days=(last_oct.weekday() + 1) % 7)
        end = last_oct + timedelta(hours=6)
    return -4 if start <= dt_utc < end else -5


def utc_to_local(dt_utc: datetime, tz_name: Optional[str] = None) -> datetime:
    """Naive UTC datetime -> naive local wall-clock datetime."""
    name = tz_name or local_tz_name()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)
    except Exception:
        if name in ('America/Toronto', 'America/Montreal',
                    'America/New_York', 'US/Eastern', 'Canada/Eastern'):
            return dt_utc + timedelta(hours=_eastern_offset_hours(dt_utc))
        raise ValueError(
            f"local time zone {name!r} (TAXJSON_LOCAL_TZ) is not available "
            f"— install the `tzdata` package or use an IANA zone name "
            f"such as America/Toronto.")
    return (dt_utc.replace(tzinfo=timezone.utc).astimezone(tz)
            .replace(tzinfo=None))


# A USD stablecoin traded this far from 1.00 USD is said: the parsers
# book stablecoins as US-dollar cash (an approximation), so a de-peg
# gain or loss never reaches the books (partition INPUTS-12; tax-logic
# CA-CRYPTO-02 / US-CRYPTO-02).
DEPEG_TOLERANCE = 0.02


def warn_depeg(coin: str, usd_price: float, qty: float, date: str,
               where: str) -> bool:
    """Print a warning when a stablecoin fill's USD price is more than
    DEPEG_TOLERANCE from 1.00. Returns whether it warned."""
    import sys
    try:
        price = float(usd_price)
    except (TypeError, ValueError):
        return False
    if not price or abs(price - 1.0) <= DEPEG_TOLERANCE:
        return False
    print(f"warning: {where}: {coin} traded at {price:.4f} USD on {date} "
          f"— stablecoins are booked as US-dollar cash (an "
          f"approximation), so the {abs(price - 1.0) * qty:,.2f} USD "
          f"de-peg difference on {qty:g} {coin} is not in the gains; "
          f"report it by hand if it matters.", file=sys.stderr)
    return True

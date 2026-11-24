"""The exchange-rates file read: load_exchange_rates' {currency: {date:
rate}} (a RateHistory, which also names the dates a rate was published),
and the canonical currency code every comparison and lookup goes through
(norm_currency). bin/taxjson_convert_currency.py converts a book's rows
with them.
"""
from taxjson.lib.stage_msg import emit_line
import math
import re
import sys
from pathlib import Path
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, NamedTuple, Optional, Tuple

from taxjson.lib.cli_diag import InputContentError, InputReadError


class RatesFileError(InputContentError):
    """A row of a rates file (work/to_base.csv, a `--rates` file) the
    readers refuse: a date that is no day of the calendar, a rate that
    is no plain decimal or no positive finite number, a rate to another
    currency than a send's base. One line naming the file and the line.
    An InputContentError (a ValueError): every command and guarded tool
    says it as ``Error: ...`` with exit 2, never a traceback (`tjs
    div-estimate` printed one for a NaN rate, and for the refusals of
    TJ-5045 / TJ-5046)."""


_RATE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_calendar_date(text) -> bool:
    """`text` is YYYY-MM-DD and a day of the calendar. The shape alone
    took `2025-02-30`, which sorts after every real February day and so
    became the latest observation (backlog TJ-5046)."""
    if not isinstance(text, str) or not _RATE_DATE_RE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def norm_currency(code) -> str:
    """Canonical currency code: stripped, upper-cased ('' for None).
    Every comparison and rates-history lookup goes through this so a
    row labelled 'cad ' under --to CAD is recognised as already in the
    target instead of being multiplied by the USD default rate and
    relabelled CAD (stage-tools audit)."""
    return (code or "").strip().upper()


# A rate is a plain decimal: Decimal()/float() also take '1_35' (=135),
# 'nan' and 'inf', which turned a typo into a silent 100x or an
# unconverted row (audit S028-15 / S028-17).
_PLAIN_DECIMAL_RE = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')


class RateLine(NamedTuple):
    """One rates-file line as parse_rate_line read it. `rate` is None
    when `problem` is RATE_UNREADABLE; it is the parsed number, never to
    be used, when `problem` is RATE_NOT_POSITIVE."""
    date: str
    from_curr: str
    to_curr: str
    rate_text: str
    rate: Optional[Decimal]
    source: str
    published: Optional[str]


# parse_rate_line's verdicts. "" is a usable line.
RATE_BLANK = "blank"            # empty, or a `#` comment
RATE_NOT_A_ROW = "shape"        # fewer than 5 columns, or no YYYY-MM-DD
RATE_BAD_DATE = "date"          # date-shaped, not a day of the calendar
RATE_UNREADABLE = "rate"        # the rate is not a plain decimal
RATE_NOT_POSITIVE = "value"     # a number, not positive and finite


def parse_rate_line(line: str) -> Tuple[str, Optional[RateLine]]:
    """(problem, row) for one line of a rates file, `DATE TIME FROM TO
    RATE [SOURCE [PUBLISHED]]`: THE grammar of work/to_base.csv and a
    `--rates` file, shared by load_exchange_rates (the conversion) and
    lib/base_rates.load_rates (a crypto send's value), which each had
    their own: the send reader took `1_35` as 135 while the conversion
    refused the line (backlog TJ-5045), and both took the date-shaped
    `2025-02-30` (TJ-5046).

    The date, and the published date of a filled row when the seventh
    column is date-shaped, must be days of the calendar; the rate a
    plain decimal (_PLAIN_DECIMAL_RE), positive and finite both as a
    Decimal and as a float. The row is None for RATE_BLANK and
    RATE_NOT_A_ROW; for RATE_BAD_DATE its rate is unread (None) and
    `bad_date_text` says which date. Currency codes are canonical
    (norm_currency), the source lower-cased ('' when absent);
    `published` is the seventh column when it is an earlier day than
    the date, else None."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return RATE_BLANK, None
    parts = stripped.split()
    if len(parts) < 5 or not _RATE_DATE_RE.match(parts[0]):
        return RATE_NOT_A_ROW, None
    published = None
    if bad_date_text(stripped):
        return RATE_BAD_DATE, RateLine(
            parts[0], norm_currency(parts[2]), norm_currency(parts[3]),
            parts[4], None, parts[5].lower() if len(parts) > 5 else "", None)
    if len(parts) > 6 and _RATE_DATE_RE.match(parts[6]) \
            and parts[6] < parts[0]:
        published = parts[6]
    try:
        rate: Optional[Decimal] = Decimal(parts[4])
    except (InvalidOperation, ValueError):
        rate = None
    row = RateLine(parts[0], norm_currency(parts[2]),
                   norm_currency(parts[3]), parts[4], rate,
                   parts[5].lower() if len(parts) > 5 else "", published)
    if rate is None:
        return RATE_UNREADABLE, row
    # `1e400` is a finite Decimal that reads as inf once the amounts
    # are multiplied (float), `1e-400` as 0: both the Decimal and its
    # float must be positive and finite (audit 2026-10-24 B33 — a row
    # converted to net_amount=inf).
    if not rate.is_finite() or rate <= 0 \
            or not math.isfinite(float(rate)) or float(rate) <= 0:
        return RATE_NOT_POSITIVE, row
    if not _PLAIN_DECIMAL_RE.match(parts[4]):
        # '1_35' parses as 135 (S028-17): malformed.
        return RATE_UNREADABLE, row._replace(rate=None)
    return "", row


def bad_date_text(line: str) -> str:
    """The date of a rates-file line that has the shape YYYY-MM-DD and is
    no day of the calendar: its DATE, else the published date of a
    filled row (the seventh column); '' when both are days (or the line
    is no row)."""
    parts = line.split()
    if len(parts) < 5 or not _RATE_DATE_RE.match(parts[0]):
        return ""
    if not is_calendar_date(parts[0]):
        return parts[0]
    if len(parts) > 6 and _RATE_DATE_RE.match(parts[6]) \
            and not is_calendar_date(parts[6]):
        return parts[6]
    return ""


def bad_date_refusal(rates_file, lineno: int, line: str, what: str) -> str:
    """The refusal of a rates-file line dated on a day that does not
    exist: a hard stop, as a rate that is no positive number is. Skipped,
    the line's day would be priced at another day's rate without a word;
    kept (before TJ-5046), `2025-02-30` sorted after every real February
    day and was the latest rate for the days after it."""
    return (f"rates file {rates_file}, line {lineno}: "
            f"{bad_date_text(line)!r} is not a day of the calendar "
            f"({line.strip()!r}) — refusing to {what} with a rates file "
            f"that holds it. Fix or regenerate the rates file.")


class RateHistory(dict):
    """load_exchange_rates' {currency: {date: rate}}, with `published`:
    {currency: {date: the date the rate was published}} for a date the
    rates file filled from an earlier day (its seventh column; backlog
    TJ-0462) — the evidence names that day, not the filled one."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.published: Dict[str, Dict[str, str]] = {}


def load_exchange_rates(rates_file: Path, target_curr: str = None) -> Dict[str, Dict[str, Decimal]]:
    """
    Load historical exchange rates.
    Expected format (space separated): DATE TIME FROM TO RATE
    Example: 2025-01-04 12:00:00 USD CAD 1.35000
    Returns: { "USD": { "2025-01-04": Decimal("1.35") } }

    If `target_curr` is provided, rows whose TO column doesn't match are
    SKIPPED with a stderr warning. (Previously the TO column was silently
    ignored, so a rates file containing USD→EUR rates would mis-apply when
    the user asked for --to CAD.) Pass target_curr=None for the old
    accept-all behavior.

    Currency columns are upper-cased (a lowercase `usd` row used to be
    stored under a key no transaction ever matched). Malformed lines —
    fewer than 5 whitespace-separated tokens (a comma-separated export,
    a missing TIME column) or a non-YYYY-MM-DD date — are skipped and
    COUNTED with a stderr warning (parse_rate_line, the one grammar). A
    row whose rate is no plain decimal (`abc`, `1_35`, `1,35`), or
    whose date of that shape is no day of the calendar (`2025-02-30`; a
    filled row's published date too), raises ValueError naming the
    line, as a rate that is no positive number does (TJ-5045, TJ-5046):
    skipped, another day's rate would stand in. A rate that
    parses but is not a positive finite number (NaN, Infinity, 0, a
    negative) raises ValueError: multiplying money by it corrupts every
    downstream figure, so a corrupted rates file is a hard stop.
    """
    history = RateHistory()
    skipped_to_mismatches = 0
    skipped_malformed = 0
    malformed_samples = []
    target_norm = norm_currency(target_curr)
    if rates_file and (rates_file.exists() or rates_file.is_symlink()):
        # utf-8-sig: a BOM dropped line 1 as 'malformed' (re-audit
        # A2-1411). An unreadable file (not UTF-8, a directory, no
        # permission) is one line naming it, an InputReadError (exit 2
        # under guard_main), never a codec traceback in fx-cash,
        # harvest or crypto-sends (A2-0790 / A2-1434).
        try:
            if rates_file.is_dir():
                raise IsADirectoryError(0, "is a directory")
            text = rates_file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as e:
            raise InputReadError(
                f"cannot read the rates file {rates_file}: not UTF-8 text "
                f"(byte 0x{e.object[e.start]:02x} at offset {e.start}) — "
                f"fix it, or re-run `tjs run` to rebuild "
                f"work/to_base.csv") from None
        except OSError as e:
            raise InputReadError(
                f"cannot read the rates file {rates_file}: "
                f"{e.strerror or e} — fix it, or re-run `tjs run` to "
                f"rebuild work/to_base.csv") from None
        for lineno, line in enumerate(text.splitlines(), 1):
            problem, row = parse_rate_line(line)
            if problem == RATE_BLANK:
                continue
            if row is not None and target_norm \
                    and row.to_curr != target_norm:
                skipped_to_mismatches += 1
                continue
            if problem == RATE_BAD_DATE:
                raise RatesFileError(bad_date_refusal(
                    rates_file, lineno, line, "convert money"))
            if problem == RATE_NOT_POSITIVE:
                raise RatesFileError(
                    f"rates file {rates_file}, line {lineno}: rate "
                    f"{row.rate_text!r} for {row.from_curr}->"
                    f"{row.to_curr} on {row.date} is not a positive "
                    f"finite number — refusing to convert money with "
                    f"it. Fix or regenerate the rates file.")
            if problem == RATE_UNREADABLE:
                # One rule for every rates reader (owner, 2026-11-23):
                # refused naming the line, as the send reader does. It
                # was skipped and counted, and another day's rate stood
                # in for the line's day.
                raise RatesFileError(
                    f"rates file {rates_file}, line {lineno}: rate "
                    f"{row.rate_text!r} for {row.from_curr}->"
                    f"{row.to_curr} on {row.date} is not a plain decimal "
                    f"number — refusing to convert money with it. Fix or "
                    f"regenerate the rates file.")
            if problem:
                # (No row at all: fewer than 5 columns, no YYYY-MM-DD.)
                # Counted for a summary warning, so a damaged rates file
                # cannot quietly leave dates uncovered and force the
                # --default-rate fallback.
                skipped_malformed += 1
                if len(malformed_samples) < 3:
                    malformed_samples.append(
                        f"line {lineno}: {line.strip()!r}")
                continue
            # FIRST row per (currency, date) wins: to_base_curr
            # prints the forward-filled NOON row for every date,
            # then appends an intra-day spot row for TODAY.
            # Last-wins keying made today's conversions drift
            # with the market between runs; first-wins pins the
            # noon rate deterministically.
            days = history.setdefault(row.from_curr, {})
            if row.date not in days:
                days[row.date] = row.rate
                if row.published:
                    history.published.setdefault(
                        row.from_curr, {})[row.date] = row.published
    if skipped_to_mismatches > 0:
        emit_line(
            f"warning: skipped {skipped_to_mismatches} FX rate row(s) where the TO "
            f"column did not match --to {target_curr}. Pass --to that matches your "
            f"rate file's TO column, or split the rate file by direction.",
            file=sys.stderr,
        )
    if skipped_malformed > 0:
        emit_line(
            f"warning: skipped {skipped_malformed} malformed FX rate "
            f"line(s) in {rates_file} (expected `DATE TIME FROM TO RATE`, "
            f"YYYY-MM-DD date, numeric rate) — e.g. "
            f"{'; '.join(malformed_samples)}. Inspect the rates file — "
            f"silent drops here can leave a date uncovered and force the "
            f"--default-rate fallback later in the pipeline.",
            file=sys.stderr,
        )
    return history

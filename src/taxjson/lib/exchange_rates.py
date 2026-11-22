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
from decimal import Decimal, InvalidOperation
from typing import Dict

from taxjson.lib.cli_diag import InputReadError


_RATE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    a missing TIME column), a non-YYYY-MM-DD date, or an unparseable
    rate — are skipped and COUNTED with a stderr warning. A rate that
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
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 5 or not _RATE_DATE_RE.match(parts[0]):
                skipped_malformed += 1
                if len(malformed_samples) < 3:
                    malformed_samples.append(f"line {lineno}: {stripped!r}")
                continue
            date_str = parts[0]
            from_curr = norm_currency(parts[2])
            to_curr = norm_currency(parts[3])
            if target_norm and to_curr != target_norm:
                skipped_to_mismatches += 1
                continue
            try:
                rate = Decimal(parts[4])
            except (InvalidOperation, ValueError):
                # Narrowed from a broad `except Exception: pass`.
                # A malformed rate column was being silently
                # dropped; track for a summary warning so a
                # corrupted rates file can't quietly leave dates
                # uncovered and force --default-rate fallback.
                skipped_malformed += 1
                if len(malformed_samples) < 3:
                    malformed_samples.append(f"line {lineno}: {stripped!r}")
                continue
            # `1e400` is a finite Decimal that reads as inf once the
            # amounts are multiplied (float), `1e-400` as 0: both the
            # Decimal and its float must be positive and finite (audit
            # 2026-10-24 B33 — a row converted to net_amount=inf).
            if not rate.is_finite() or rate <= 0 \
                    or not math.isfinite(float(rate)) or float(rate) <= 0:
                raise ValueError(
                    f"rates file {rates_file}, line {lineno}: rate "
                    f"{parts[4]!r} for {from_curr}->{to_curr} on "
                    f"{date_str} is not a positive finite number — "
                    f"refusing to convert money with it. Fix or "
                    f"regenerate the rates file.")
            if not _PLAIN_DECIMAL_RE.match(parts[4]):
                # '1_35' parses as 135 (S028-17): malformed, counted.
                skipped_malformed += 1
                if len(malformed_samples) < 3:
                    malformed_samples.append(f"line {lineno}: {stripped!r}")
                continue
            if from_curr not in history:
                history[from_curr] = {}
            # FIRST row per (currency, date) wins: to_base_curr
            # prints the forward-filled NOON row for every date,
            # then appends an intra-day spot row for TODAY.
            # Last-wins keying made today's conversions drift
            # with the market between runs; first-wins pins the
            # noon rate deterministically.
            if date_str not in history[from_curr]:
                history[from_curr][date_str] = rate
                if len(parts) > 6 and _RATE_DATE_RE.match(parts[6]) \
                        and parts[6] < date_str:
                    history.published.setdefault(
                        from_curr, {})[date_str] = parts[6]
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

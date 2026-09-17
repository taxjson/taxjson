#!/usr/bin/env python3
"""
taxjson_convert_currency.py

Convert all amounts in a tax.json file to a target currency using exchange rates.

Usage:
    python -m taxjson.bin.taxjson_convert_currency input.json --to CAD [--rates rates.txt]
"""

import argparse
import json
import re
import sys
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Dict
from datetime import datetime, timedelta

from taxjson.lib.core import (
    TaxTransaction, convert_currency, load_transactions,
)

DEFAULT_RATE = 1.35

_RATE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def norm_currency(code) -> str:
    """Canonical currency code: stripped, upper-cased ('' for None).
    Every comparison and rates-history lookup goes through this so a
    row labelled 'cad ' under --to CAD is recognised as already in the
    target instead of being multiplied by the USD default rate and
    relabelled CAD (stage-tools audit)."""
    return (code or "").strip().upper()


def resolve_default_rate(value) -> float:
    """--default-rate is parsed with default=None so callers can tell
    an explicit `--default-rate 1.35` from the implicit fallback."""
    return DEFAULT_RATE if value is None else float(value)


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
    history = {}
    skipped_to_mismatches = 0
    skipped_malformed = 0
    malformed_samples = []
    target_norm = norm_currency(target_curr)
    if rates_file and rates_file.exists():
        with rates_file.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
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
                if not rate.is_finite() or rate <= 0:
                    raise ValueError(
                        f"rates file {rates_file}, line {lineno}: rate "
                        f"{parts[4]!r} for {from_curr}->{to_curr} on "
                        f"{date_str} is not a positive finite number — "
                        f"refusing to convert money with it. Fix or "
                        f"regenerate the rates file.")
                if from_curr not in history:
                    history[from_curr] = {}
                # FIRST row per (currency, date) wins: to_base_curr
                # prints the forward-filled NOON row for every date,
                # then appends an intra-day spot row for TODAY.
                # Last-wins keying made today's conversions drift
                # with the market between runs; first-wins pins the
                # noon rate deterministically.
                history[from_curr].setdefault(date_str, rate)
    if skipped_to_mismatches > 0:
        print(
            f"warning: skipped {skipped_to_mismatches} FX rate row(s) where the TO "
            f"column did not match --to {target_curr}. Pass --to that matches your "
            f"rate file's TO column, or split the rate file by direction.",
            file=sys.stderr,
        )
    if skipped_malformed > 0:
        print(
            f"warning: skipped {skipped_malformed} malformed FX rate "
            f"line(s) in {rates_file} (expected `DATE TIME FROM TO RATE`, "
            f"YYYY-MM-DD date, numeric rate) — e.g. "
            f"{'; '.join(malformed_samples)}. Inspect the rates file — "
            f"silent drops here can leave a date uncovered and force the "
            f"--default-rate fallback later in the pipeline.",
            file=sys.stderr,
        )
    return history

# Module-level tally of default-rate fallbacks so we can emit a single
# summary line at the end of a run rather than spamming once per row.
# Reset by main(); never read by anyone except the warn-summary path.
_DEFAULT_RATE_FALLBACKS: Dict[tuple, int] = {}
NO_RATES_REASON = "no rates for currency"


def get_rate_for_date(currency: str, date_str: str, history: Dict[str, Dict[str, Decimal]], default_rate: Decimal) -> Decimal:
    """Finds the rate for the exact date, or falls back to previous days, or default.

    Records every fallback into `_DEFAULT_RATE_FALLBACKS` so callers can
    surface a summary — silently applying a hardcoded 1.35 to a real
    USD→CAD transaction is one of the easier ways to ship wrong numbers
    if a rates file is truncated or missing for some date range.
    """
    currency = norm_currency(currency)

    def _record_fallback(reason: str) -> Decimal:
        key = (currency, reason)
        _DEFAULT_RATE_FALLBACKS[key] = _DEFAULT_RATE_FALLBACKS.get(key, 0) + 1
        return default_rate

    if currency not in history:
        return _record_fallback(NO_RATES_REASON)

    curr_history = history[currency]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return _record_fallback("unparseable date")

    # Look back up to 5 days for a rate (e.g. over long weekends)
    for i in range(6):
        test_date = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        if test_date in curr_history:
            return curr_history[test_date]

    # Distinguish "before the rates file even starts" from an interior gap:
    # the fetch window is ~5.5 years back from today, so a transaction older
    # than that (early trades, phantom openings) silently converts at the
    # default. Naming the file's start date makes the fix actionable
    # (extend the fetch window) instead of a generic gap message.
    if curr_history:
        first = min(curr_history)
        if date_str < first:
            return _record_fallback(f"date predates rates file start {first} "
                                    f"— extend the fetch window")
    return _record_fallback("no rate within 5-day lookback")

def convert_transaction(
    tx: TaxTransaction, target_curr: str, history: Dict[str, Dict[str, Decimal]], default_rate: Decimal
) -> TaxTransaction:
    converted = TaxTransaction(**tx.to_dict())
    target_curr = norm_currency(target_curr)
    src_curr = norm_currency(tx.currency)

    if src_curr and src_curr == target_curr:
        # Already in the target — only the LABEL may need canonicalising
        # ('cad ', 'Cad'). The raw compare used to treat those as a
        # foreign currency, apply the default rate and stamp them CAD.
        converted.currency = target_curr
    elif src_curr:
        # Resolve rate for this transaction's date
        tx_date = tx.date_settle if tx.date_settle else tx.date
        rate = get_rate_for_date(src_curr, tx_date, history, default_rate)
        rates_map = {(src_curr, target_curr): rate}

        # Stage all converted values before mutating `converted`. The
        # old loop wrote each field as it was converted; if a later
        # field-conversion failed, earlier fields were already in the
        # target currency while later ones (and `currency` itself)
        # stayed in the source currency, producing mixed-currency rows.
        # Now: collect every conversion's result first, only commit
        # if every field succeeded.
        staged: Dict[str, float] = {}
        any_failure = False
        for field in ["proceeds", "commission", "fee", "price", "net_amount", "gross_amount"]:
            value = getattr(tx, field, None)
            if value is None:
                continue
            try:
                staged[field] = convert_currency(
                    value, src_curr, target_curr, rates_map, float(default_rate)
                )
            except Exception as exc:
                # Stay loud — a silent skip here was the original bug.
                any_failure = True
                print(
                    f"warning: failed to convert {field}={value!r} on "
                    f"{tx.action} {tx.symbol} {tx.date} "
                    f"({tx.currency}→{target_curr}): {exc}",
                    file=sys.stderr,
                )

        if not any_failure:
            for field, converted_value in staged.items():
                setattr(converted, field, converted_value)
            converted.currency = target_curr

    return converted

def process_transactions(
    transactions: list, target_curr: str, history: Dict[str, Dict[str, Decimal]], default_rate: Decimal
) -> list:
    return [convert_transaction(tx, target_curr, history, default_rate) for tx in transactions]


def uncovered_currencies():
    """Currencies that fell back to --default-rate because the rates
    history had NO entry for them at all (as opposed to a date gap).
    Sorted list of (currency, row_count)."""
    return sorted((cur, n) for (cur, reason), n in _DEFAULT_RATE_FALLBACKS.items()
                  if reason == NO_RATES_REASON)


def abort_if_currency_uncovered(*, rates_given: bool,
                                default_rate_explicit: bool,
                                stream=sys.stderr) -> bool:
    """A currency entirely absent from a supplied --rates file is a
    hard error unless the user opted into the fallback with an explicit
    --default-rate: every one of its rows would otherwise be booked at
    the hardcoded 1.35 with only a warning to show for it (stage-tools
    audit). Without --rates at all the upfront "no --rates" warning
    already covers the run, so that path keeps its historical
    behaviour. Returns True when the caller must abort."""
    if not rates_given or default_rate_explicit:
        return False
    missing = uncovered_currencies()
    if not missing:
        return False
    detail = ", ".join(f"{cur} ({n} row(s))" for cur, n in missing)
    print(
        f"error: the rates file has no rates at all for {detail}; refusing "
        f"to convert those rows at the implicit default rate. Add the "
        f"currency to the rates file (in a `taxjson run` project: list it "
        f"in [settings] source_currencies in taxjson.toml), or pass "
        f"--default-rate explicitly to accept the fallback.",
        file=stream,
    )
    return True


def reset_fallback_tally() -> None:
    """Clear the module-level default-rate tally. Callers driving
    `process_transactions` directly (e.g. taxjson-merge2) should call
    this before starting a conversion so stale counts from an earlier
    invocation don't leak into the summary."""
    _DEFAULT_RATE_FALLBACKS.clear()


def emit_fallback_summary(default_rate, *, stream=sys.stderr) -> None:
    """Emit a one-line stderr summary of how many rows fell back to
    `default_rate`. Quiet when zero. Both the standalone CLI's main()
    and taxjson-merge2 call this after process_transactions so the
    warning fires regardless of which entry point ran the conversion —
    a missed summary in merge2 was the original silent-default-rate
    hazard the tally was added to prevent."""
    if not _DEFAULT_RATE_FALLBACKS:
        return
    total = sum(_DEFAULT_RATE_FALLBACKS.values())
    breakdown = ", ".join(
        f"{count}× {currency} ({reason})"
        for (currency, reason), count in sorted(_DEFAULT_RATE_FALLBACKS.items())
    )
    print(
        f"warning: applied --default-rate ({default_rate}) to {total} "
        f"row(s) that had no rate match: {breakdown}",
        file=stream,
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", help="Input tax.json file")
    parser.add_argument("--to", required=True, help="Target currency code (e.g. CAD, USD)")
    parser.add_argument("--rates", help="File with historical exchange rates")
    parser.add_argument(
        "--default-rate", type=float, default=None,
        help="Fallback rate when the rates file is missing a date "
             "(default: 1.35). Passing it explicitly also allows a "
             "currency that is entirely absent from --rates to convert "
             "at this rate; without it that is a fatal error.")
    args = parser.parse_args()

    # Shared loader funnel (core.load_transactions / the stdin loader):
    # `#` comments, qty→quantity alias, type guards. The bare
    # json.load + TaxTransaction(**item) here used to choke on a
    # commented file and silently DROP non-dict rows.
    try:
        if args.input:
            transactions = load_transactions(Path(args.input))
        else:
            from taxjson.lib.pipeline import load_stdin_transactions
            transactions = load_stdin_transactions()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    target_curr = norm_currency(args.to)
    if not args.rates:
        # The bare 1.35 default was historically silent. Make it loud:
        # someone running --to CAD on USD trades without --rates would
        # otherwise stamp every row at the same hardcoded rate without
        # noticing.
        print(
            f"warning: no --rates file given; every cross-currency row will "
            f"be converted with the hardcoded --default-rate "
            f"({resolve_default_rate(args.default_rate)}). Pass --rates "
            f"rates.csv to use real historical rates.",
            file=sys.stderr,
        )

    reset_fallback_tally()

    try:
        history = load_exchange_rates(
            Path(args.rates) if args.rates else None,
            target_curr=target_curr,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    default_rate = Decimal(str(resolve_default_rate(args.default_rate)))

    converted_transactions = process_transactions(transactions, target_curr, history, default_rate)

    emit_fallback_summary(default_rate)
    if abort_if_currency_uncovered(
            rates_given=bool(args.rates),
            default_rate_explicit=args.default_rate is not None):
        sys.exit(1)

    output_data = {
        "transactions": [tx.to_dict() for tx in converted_transactions],
        "metadata": {
            "converted_to": target_curr,
            "default_rate": str(default_rate),
        },
    }
    
    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

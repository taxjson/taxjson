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
from typing import Dict, Optional

from taxjson.lib.cli_diag import InputReadError, guard_main
from taxjson.lib.country import country_arg
from datetime import datetime, timedelta

from taxjson.lib.core import (
    TaxTransaction, convert_currency, load_transactions,
)

DEFAULT_RATE = 1.35
# The built-in fallback is a USD->CAD rate: applied to a CAD row in a
# USD book it multiplied CAD by 1.35 instead of ~0.74 (audit A2-0148).
# The implicit fallback is per direction; an explicit --default-rate is
# the user's own rate and is applied as given.
IMPLICIT_PAIR_RATES = {("USD", "CAD"): Decimal("1.35"),
                       ("CAD", "USD"): (Decimal(1) / Decimal("1.35")
                                        ).quantize(Decimal("0.000001"))}


def default_rate_for(src: str, tgt: str, explicit=None) -> Decimal:
    """The fallback rate for one src->tgt conversion: `explicit` (a
    --default-rate) when given, else the built-in rate of that
    direction (USD->CAD 1.35, CAD->USD its inverse), else DEFAULT_RATE."""
    if explicit is not None:
        return Decimal(str(explicit))
    return IMPLICIT_PAIR_RATES.get((norm_currency(src), norm_currency(tgt)),
                                   Decimal(str(DEFAULT_RATE)))


def describe_default_rate(default_rate) -> str:
    """How a fallback rate is named in messages (None: the built-in
    per-direction rates)."""
    if default_rate is not None:
        return str(default_rate)
    return (f"1.35 for USD->CAD, {IMPLICIT_PAIR_RATES[('CAD', 'USD')]} "
            f"for CAD->USD")

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


def positive_rate(value: str) -> float:
    """argparse type for --default-rate: a plain positive finite number —
    the rule load_exchange_rates applies to a rates file's rate column.
    A negative rate flipped the sign of every converted amount; 0, nan
    and inf zeroed, poisoned or skipped them, all with exit 0."""
    import argparse
    import math
    text = str(value).strip()
    try:
        rate = float(text) if _PLAIN_DECIMAL_RE.match(text) else None
    except ValueError:
        rate = None
    if rate is None or not math.isfinite(rate) or rate <= 0:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a positive number (a rate such as 1.35)")
    return rate


def resolve_default_rate(value):
    """--default-rate is parsed with default=None so callers can tell
    an explicit `--default-rate 1.35` from the implicit fallback: the
    explicit rate as a Decimal, or None — the built-in rate of each
    row's direction (default_rate_for)."""
    return None if value is None else Decimal(str(value))


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
                f"fix it, or re-run `taxjson run` to rebuild "
                f"work/to_base.csv") from None
        except OSError as e:
            raise InputReadError(
                f"cannot read the rates file {rates_file}: "
                f"{e.strerror or e} — fix it, or re-run `taxjson run` to "
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
            if not rate.is_finite() or rate <= 0:
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

def load_rate_sources(rates_file: Path, target_curr: str = None) -> Dict[str, Dict[str, str]]:
    """{currency: {date: source}} from the optional SIXTH column of a
    rates file (`boc` / `yahoo`, written by taxjson-to-base-curr). Same
    first-row-wins keying as load_exchange_rates; rows without the
    column (a hand-made or pre-0.17 file) are simply absent. Never
    raises on a malformed line — load_exchange_rates owns that."""
    out: Dict[str, Dict[str, str]] = {}
    target_norm = norm_currency(target_curr)
    if not rates_file or not Path(rates_file).exists():
        return out
    try:
        with Path(rates_file).open("r", encoding="utf-8-sig") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 6 or parts[0].startswith("#") \
                        or not _RATE_DATE_RE.match(parts[0]):
                    continue
                if target_norm and norm_currency(parts[3]) != target_norm:
                    continue
                out.setdefault(norm_currency(parts[2]), {}).setdefault(
                    parts[0], parts[5].lower())
    except (OSError, UnicodeDecodeError):
        return {}
    return out


# Module-level tally of default-rate fallbacks so we can emit a single
# summary line at the end of a run rather than spamming once per row.
# Reset by main(); never read by anyone except the warn-summary path.
_DEFAULT_RATE_FALLBACKS: Dict[tuple, int] = {}
NO_RATES_REASON = "no rates for currency"
# Per-ROW record of the same fallbacks (id, date, currency, symbol,
# action, reason) — the conversion stage turns each into a validation
# ERROR — and the (currency, rate-date) pairs actually applied, for the
# "which FX source" summary. Both reset with reset_fallback_tally().
_FALLBACK_ROWS: list = []
_RATES_USED: set = set()


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

    # Weekends/holidays: the most recent PRIOR business-day rate (the
    # usual CRA practice; to_base_curr forward-fills the same way).
    # Look back up to 5 days for a rate (e.g. over long weekends)
    for i in range(6):
        test_date = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        if test_date in curr_history:
            _RATES_USED.add((currency, test_date))
            return curr_history[test_date]

    # Distinguish "before the rates file even starts" from an interior gap:
    # a transaction older than the first rate (early trades, phantom
    # openings) would convert at the default. Naming the file's start
    # date makes the fix actionable instead of a generic gap message.
    if curr_history:
        first = min(curr_history)
        if date_str < first:
            return _record_fallback(f"date predates rates file start {first}")
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
    elif tx.action == 'SPLIT' and not any(
            float(getattr(tx, f, 0) or 0) for f in (
                "proceeds", "commission", "fee", "price", "net_amount",
                "gross_amount")):
        # A split / rename row carries no money (its quantity is a
        # ratio): only the label changes. A .tt SPLIT line is labelled
        # CAD, and a US project with no CAD rates refused the whole
        # account over it.
        converted.currency = target_curr
    elif src_curr:
        # Resolve rate for this transaction's date
        tx_date = tx.date_settle if tx.date_settle else tx.date
        before = dict(_DEFAULT_RATE_FALLBACKS)
        _fallback = default_rate_for(src_curr, target_curr, default_rate)
        rate = get_rate_for_date(src_curr, tx_date, history, _fallback)
        if _DEFAULT_RATE_FALLBACKS != before:
            reason = next(r for (c, r), n in _DEFAULT_RATE_FALLBACKS.items()
                          if n != before.get((c, r), 0))
            _FALLBACK_ROWS.append({
                "id": tx.id, "date": tx_date, "currency": src_curr,
                "symbol": tx.symbol, "action": tx.action,
                "reason": reason, "rate": str(_fallback)})
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
                    value, src_curr, target_curr, rates_map, float(rate)
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
    transactions: list, target_curr: str, history: Dict[str, Dict[str, Decimal]], default_rate: Decimal,
    country: Optional[str] = None,
) -> list:
    """Convert every row. Plain futures fills are first put on the
    SETTLEMENT basis in their native currency (lib/futures.settle_futures):
    an opening carries 0, a close carries the realized native P/L, so
    each close's P/L is converted at that closing leg's own rate and no
    notional is ever translated (R1-0/R1-52/R1-204). That holds in both
    countries; `country` (the project's) picks the lot rule a partial
    close uses — average cost in Canada, FIFO in the US — and is
    required when the book has plain futures (it used to be keyed on a
    CAD target: partition ENGINE-02). Raises ValueError on a futures row
    that cannot be put on that basis, or on futures with no country."""
    from taxjson.lib.futures import (has_plain_futures, method_for,
                                     settle_futures)
    if has_plain_futures(transactions):
        if not country:
            raise ValueError(
                "the book has futures contracts: pass --country (canada "
                "or usa) — the settled P/L of a partial close follows the "
                "country's lot rule (average cost / FIFO)")
        transactions, _stats = settle_futures(list(transactions),
                                              method_for(country))
    return [convert_transaction(tx, target_curr, history, default_rate) for tx in transactions]


def uncovered_currencies():
    """Currencies that fell back to --default-rate because the rates
    history had NO entry for them at all (as opposed to a date gap).
    Sorted list of (currency, row_count)."""
    return sorted((cur, n) for (cur, reason), n in _DEFAULT_RATE_FALLBACKS.items()
                  if reason == NO_RATES_REASON)


def abort_if_currency_uncovered(*, rates_given: bool,
                                default_rate_explicit: bool,
                                stream=None) -> bool:
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
        file=(stream or sys.stderr),
    )
    return True


def reset_fallback_tally() -> None:
    """Clear the module-level default-rate tally. Callers driving
    `process_transactions` directly (e.g. taxjson-merge2) should call
    this before starting a conversion so stale counts from an earlier
    invocation don't leak into the summary."""
    _DEFAULT_RATE_FALLBACKS.clear()
    _FALLBACK_ROWS.clear()
    _RATES_USED.clear()


def fallback_rows() -> list:
    """Rows converted at the default rate since the last reset."""
    return list(_FALLBACK_ROWS)


def fallback_validation_issues(target_curr: str,
                               default_rate) -> Dict[str, list]:
    """{context: [message]} — one validation ERROR per row that fell
    back to the default rate, shaped like taxjson-validate's issues so
    merge2 folds them into its `validation: N error(s)` count (the
    number the .sum DIAGNOSTICS, `taxjson checklist` and `run --strict`
    read). A default-rate conversion is a wrong number in the books,
    not a style warning: before, it was a stderr line and the run
    exited 0 with a clean checklist."""
    out: Dict[str, list] = {}
    tgt = norm_currency(target_curr)
    for r in _FALLBACK_ROWS:
        ctx = f"TX {r['id']} ({r['action']} {r['symbol']} {r['date']})"
        if str(r['reason']).startswith("date predates rates file start"):
            # No source publishes rates that early, so "refresh" cannot
            # help and `taxjson run` has no --default-rate (audit
            # S028-13): name the remedy that works in a project.
            fix = (f"No rate source reaches back that far, so refreshing "
                   f"cannot help: enter this row in {tgt} (the base "
                   f"currency) at its date's rate — e.g. a .tt row with "
                   f"currency {tgt} — and the conversion leaves it as is.")
        else:
            fix = (f"Refresh the rates (`taxjson run` online); if no "
                   f"rate exists for that date, enter the row in {tgt} "
                   f"at its date's rate. (The standalone "
                   f"taxjson-convert-currency takes --default-rate to "
                   f"accept the fallback.)")
        out.setdefault(ctx, []).append(
            f"FX: no {r['currency']}->{tgt} rate "
            f"for {r['date']} ({r['reason']}); converted at the default "
            f"rate {r.get('rate') or default_rate_for(r['currency'], tgt, default_rate)}. {fix}")
    return out


def emit_fallback_validation(target_curr: str, default_rate, *,
                             stream=None) -> int:
    """Print the default-rate rows as a `validation: N error(s)` block
    (the form every .diag reader counts). Returns N."""
    issues = fallback_validation_issues(target_curr, default_rate)
    n = sum(len(v) for v in issues.values())
    if n:
        print(f"validation: {n} error(s) across {len(issues)} "
              f"transaction(s):", file=(stream or sys.stderr))
        for ctx, errs in sorted(issues.items()):
            for err in errs:
                print(f"  {ctx}: {err}", file=(stream or sys.stderr))
    return n


def rate_source_summary(sources: Dict[str, Dict[str, str]]) -> str:
    """'FX: Bank of Canada Valet for N dates, Yahoo fallback for M' over
    the (currency, date) rates actually applied since the last reset.
    Empty when no conversion used a rate."""
    if not _RATES_USED:
        return ""
    counts: Dict[str, int] = {}
    for cur, d in _RATES_USED:
        src = (sources.get(cur) or {}).get(d) or "unlabelled"
        counts[src] = counts.get(src, 0) + 1
    text = (f"FX: Bank of Canada Valet for {counts.pop('boc', 0)} dates, "
            f"Yahoo fallback for {counts.pop('yahoo', 0)}")
    if counts.get("boc-noon"):
        text += (f", Bank of Canada noon rate (before 2017-03) for "
                 f"{counts.pop('boc-noon')}")
    for src, n in sorted(counts.items()):
        text += (f", {n} from a rates file without a source column"
                 if src == "unlabelled" else f", {src} for {n}")
    return text


def emit_source_summary(sources: Dict[str, Dict[str, str]], *,
                        stream=None) -> None:
    text = rate_source_summary(sources)
    if text:
        print(f"note: {text}", file=(stream or sys.stderr))


def emit_fallback_summary(default_rate, *, stream=None) -> None:
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
        f"warning: applied --default-rate ({describe_default_rate(default_rate)}) "
        f"to {total} row(s) that had no rate match: {breakdown}",
        file=(stream or sys.stderr),
    )

@guard_main("taxjson-convert-currency")
def main():
    parser = argparse.ArgumentParser(
        description="Convert every amount in a taxjson file to a target "
                    "currency at each row's exchange rate from a rates "
                    "file (taxjson-to-base-curr writes one).")
    parser.add_argument("input", nargs="?", help="Input tax.json file")
    parser.add_argument("--to", required=True, help="Target currency code (e.g. CAD, USD)")
    parser.add_argument("--rates", help="File with historical exchange rates")
    parser.add_argument(
        "--country", type=country_arg, default=None,
        metavar="{canada,ca,usa,us}",
        help="The project's country: required when the book has futures "
             "contracts (their settled P/L follows the country's lot "
             "rule: average cost in Canada, FIFO in the US)")
    parser.add_argument(
        "--default-rate", type=positive_rate, default=None,
        help="Fallback rate when the rates file is missing a date "
             "(default: 1.35 for USD->CAD, its inverse for CAD->USD — "
             "every such row is a validation error). Passing it "
             "explicitly also allows a "
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
            f"({describe_default_rate(resolve_default_rate(args.default_rate))}). Pass --rates "
            f"rates.csv to use real historical rates.",
            file=sys.stderr,
        )

    reset_fallback_tally()

    if args.rates and not Path(args.rates).exists():
        # A named --rates file that does not exist was ignored: every row
        # then took --default-rate, or the error blamed the file's
        # content (re-audit A2-1437).
        print(f"taxjson-convert-currency: error: no such file: --rates "
              f"{args.rates}",
              file=sys.stderr)
        sys.exit(2)
    try:
        history = load_exchange_rates(
            Path(args.rates) if args.rates else None,
            target_curr=target_curr,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    default_rate = resolve_default_rate(args.default_rate)

    try:
        converted_transactions = process_transactions(
            transactions, target_curr, history, default_rate,
            country=args.country)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    emit_fallback_summary(default_rate)
    if abort_if_currency_uncovered(
            rates_given=bool(args.rates),
            default_rate_explicit=args.default_rate is not None):
        sys.exit(1)
    emit_source_summary(load_rate_sources(
        Path(args.rates) if args.rates else None, target_curr))
    metadata = {
        "converted_to": target_curr,
        "default_rate": (str(default_rate) if default_rate is not None
                         else "implicit"),
    }
    if args.default_rate is None and fallback_rows():
        # Default-rate rows are validation ERRORS unless the fallback
        # was accepted with an explicit --default-rate: the stderr
        # block feeds the .diag counters, the metadata lets a later
        # taxjson-validate on this file (the crypto path) fail too.
        emit_fallback_validation(target_curr, default_rate)
        metadata["fx_default_rate_rows"] = fallback_rows()

    output_data = {
        "transactions": [tx.to_dict() for tx in converted_transactions],
        "metadata": metadata,
    }
    
    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

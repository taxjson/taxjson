#!/usr/bin/env python3
"""
taxjson_gains.py

Calculate capital gains according to a given country's tax rules.

Usage:
    python -m taxjson.bin.taxjson_gains --country canada [--year 2026] input.json

Thin CLI over taxjson.lib.pipeline.run_gains — argparse + load +
json.dump. ALL gains-run semantics (transfer handling, phantom openings,
year filter, tainted split, warnings, fee aggregation) live in the
pipeline module so taxjson-explain and the web what-if consume the exact
same definition of "a gains run" and cannot drift from this CLI.

Output is a JSON object with per-security and aggregate totals.
"""

import argparse
import json
import sys
from pathlib import Path

from taxjson.lib.cli_diag import note
from taxjson.lib.core import load_transactions
from taxjson.lib.phantom_holdings import detect_phantoms, format_suggestions
# Back-compat re-exports: tests and older callers import these from here.
from taxjson.lib.pipeline import (            # noqa: F401
    GainsRequest,
    TransferValidationError,
    _handle_transfers,
    load_stdin_transactions,
    run_gains,
)
from taxjson.lib.trace_format import (
    render_document_header,
    render_gain_block,
    render_summary_table,
)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Compute capital gains for a country."
    )
    parser.add_argument(
        "--country",
        choices=["canada", "ca", "usa", "us"],
        default=None,
        help="Country for tax rules (default: canada, with a stderr note "
             "when omitted)",
    )
    parser.add_argument("input", nargs="?", help="Path to merged transactions JSON (default: stdin)")
    parser.add_argument(
        "--sheltered",
        action="append",
        default=[],
        metavar="FILE",
        help="Path to tax-sheltered transactions JSON (for wash sale "
             "detection); repeatable",
    )
    parser.add_argument(
        "--affiliated",
        help=(
            "Path to AFFILIATED-PERSONS' transactions JSON (spouse, related "
            "person, controlled corp, etc.). Their trades feed wash-sale "
            "detection per ITA 54 / IRC §1091 affiliated-party rules; the "
            "deferred loss attaches to THEIR substituted property and is "
            "tracked on their own return (not yours). Pure additive — pass "
            "only when you have visibility into the affiliated person's data."
        ),
    )
    parser.add_argument("--year", type=int, help="Tax year to calculate (optional)")
    parser.add_argument(
        "--as-of", metavar="YYYY-MM-DD", default=None,
        help="Drop transactions TRADED after this date before computing "
             "— the books as they stood then (inventory = positions at "
             "that date, incl. ACB and deferred wash). Trade-date "
             "cutoff regardless of the settle/trade basis setting.")
    parser.add_argument(
        "--option-premium-timing", choices=["grant", "close"], default="close",
        help="Canada: recognise a written option's premium on the write "
             "date (grant — ITA s.49(1)) or at the closing transaction "
             "(close). Ignored for the US engine.")
    parser.add_argument(
        "--option-grant-since", type=int, default=None, metavar="YEAR",
        help="With grant timing: contracts written before YEAR keep close "
             "timing (transition from books filed under close timing).")
    parser.add_argument(
        "--option-buyback-wash", action="store_true",
        help="Canada, grant timing: treat the loss on buying back a written "
             "option as a superficial loss when identical options are acquired "
             "within 30 days and held (strict reading; default off — a "
             "closing purchase is not a disposition s.54 reaches).")
    parser.add_argument(
        "--cross-asset",
        action="store_true",
        help=(
            "WARN-ONLY: also scan for option-as-replacement wash triggers "
            "(a long CALL bought within +-30d of a share loss, or a long "
            "PUT within +-30d of a short-closing loss, on the same "
            "underlying). Emits option_replacement_warnings and stderr "
            "notes; computed numbers are never changed. Option losses "
            "themselves still wash only against the identical contract."
        ),
    )
    parser.add_argument(
        "--per-account-basis", action="store_true",
        help="Blended multi-account mode (combined taxable input): US "
             "FIFO basis pools are kept per account while wash-sale "
             "matching spans all accounts. No effect for Canada — its "
             "ACB pools already blend per ITA s.47.")
    parser.add_argument(
        "--no-wash",
        action="store_true",
        help=(
            "Skip superficial-loss / wash-sale detection even when "
            "--taxable is set. Useful for an apples-to-apples diff against "
            "the legacy tt_gains.pl baseline (which never applied wash-sale "
            "logic). Without this flag and with --taxable, taxjson-gains "
            "applies CRA's ITA 54 superficial-loss rule (Canada) or IRC "
            "§1091 (US)."
        ),
    )
    parser.add_argument(
        "--tax-date",
        choices=["trade", "settle"],
        default=None,
        help=(
            "Which date controls tax-year filtering. Default is COUNTRY-AWARE: "
            "'settle' for canada (CRA times dispositions on the settlement "
            "date) and 'trade' for usa (the IRS recognizes on the trade "
            "date). Pass explicitly to override."
        ),
    )
    parser.add_argument(
        "--full-traces",
        metavar="FILE",
        help=(
            "Write tt-style calculation traces (ACB for Canada, FIFO for USA) "
            "to FILE. JSON output still goes to stdout, and the per-gain 'trace' "
            "field is stripped from it. When omitted, no traces are produced. "
            "Use 'taxjson-explain' as an interactive companion."
        ),
    )
    parser.add_argument(
        "--incomplete-history",
        metavar="FILE",
        help=(
            "JSON file listing (symbol, account) pairs whose pre-data-window "
            "history is missing. The engine inserts a synthetic OPENING_BALANCE "
            "for each, taints the ACB pool, and excludes affected dispositions "
            "from the gains report (surfaced separately under "
            "'manual_reporting_required')."
        ),
    )
    parser.add_argument(
        "--suggest-phantoms",
        metavar="FILE",
        help=(
            "Detect (symbol, account) pairs whose running position goes "
            "negative and write a candidate phantoms file to FILE. Review, "
            "remove entries that are actually real shorts, then re-run with "
            "--incomplete-history FILE."
        ),
    )
    parser.add_argument(
        "--include-options-in-suggestions",
        action="store_true",
        help=(
            "By default --suggest-phantoms skips option (OCC-format) symbols "
            "because negative option positions are normal (sell-to-open for "
            "covered calls etc.). Use this flag to include them anyway."
        ),
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help=(
            "By default, when --year is specified, --suggest-phantoms only "
            "lists pairs whose dispositions fall within the tax year. Use "
            "--all-history to list every candidate regardless of year."
        ),
    )
    parser.add_argument(
        "--taxable",
        action="store_true",
        help=(
            "Mark this input file as a taxable account. Two effects: "
            "(1) TRANSFER rows are rejected with a hard error — the cost "
            "basis must come from actual buy/sell history. (2) Wash-sale "
            "/ superficial-loss detection is enabled (CRA ITA 54 or IRC "
            "§1091). Default (no flag): treat as sheltered — TRANSFER "
            "rows rewritten to BUYSELL using net_amount as approximate "
            "basis (so the sheltered inventory report still tracks the "
            "position), and wash-sale detection is OFF (the rule doesn't "
            "apply within registered accounts). Use --no-wash to disable "
            "wash-sale detection even when --taxable is set."
        ),
    )
    args = parser.parse_args()
    if args.country is None:
        # Silent default was audit finding A4; the `taxjson run` pipeline
        # always passes --country, so the note only reaches direct CLI users.
        note("taxjson-gains", "--country not given; assuming canada")
        args.country = "canada"
    return args


def _suggest_phantoms_and_exit(args, transactions, sheltered_transactions,
                               affiliated_transactions) -> None:
    """--suggest-phantoms mode: detect candidates, write the file, exit
    before computing gains. The user reviews the candidate file, prunes
    any real shorts, then re-runs with --incomplete-history."""
    all_for_detection = transactions + sheltered_transactions + affiliated_transactions
    candidates = detect_phantoms(
        all_for_detection,
        include_options=args.include_options_in_suggestions,
    )
    # Year-scope unless --all-history. A candidate "affects the tax year"
    # if the pair's running balance is NEGATIVE at any moment inside the
    # year (carried in from prior years or created in-year). This
    # deliberately includes a pair whose only in-year activity is the
    # BUY that covers a phantom short: filtering on in-year dispositions
    # alone dropped that pair, and the engine then booked the cover as a
    # clean short-close gain in the target year — silent corruption.
    if args.year and not args.all_history:
        year_str = str(args.year)
        year_start = f"{year_str}-01-01"
        next_year_start = f"{args.year + 1}-01-01"
        short_in_year: set = set()
        run: dict = {}
        checked_start = False
        for tx in sorted(all_for_detection,
                         key=lambda t: (t.date or '', t.time or '')):
            d = tx.date or ''
            if not checked_start and d >= year_start:
                # Balance ENTERING the year: anything short carried in
                # from prior years is in-year relevant.
                short_in_year.update(k for k, v in run.items()
                                     if v < -1e-6)
                checked_start = True
            if d >= next_year_start:
                break
            key = (tx.symbol, tx.account)
            if tx.action == 'SPLIT':
                if tx.quantity:
                    run[key] = run.get(key, 0.0) * tx.quantity
            elif tx.action in ('BUYSELL', 'ASSIGN', 'TRANSFER'):
                run[key] = run.get(key, 0.0) + tx.quantity
            else:
                continue
            if d >= year_start and run.get(key, 0.0) < -1e-6:
                short_in_year.add(key)
            # Original criterion, kept as a union: any in-year
            # disposition also marks the pair year-relevant (a pool that
            # dipped negative in a prior year can still distort in-year
            # ACB averages).
            if (tx.action in ('BUYSELL', 'ASSIGN') and tx.quantity < 0
                    and d.startswith(year_str)):
                short_in_year.add(key)
        if not checked_start:      # every tx predates the year
            short_in_year.update(k for k, v in run.items() if v < -1e-6)
        before = len(candidates)
        candidates = [c for c in candidates if (c.symbol, c.account) in short_in_year]
        if before > len(candidates):
            print(
                f"Filtered {before - len(candidates)} candidate(s) outside tax year "
                f"{year_str}. Use --all-history to include them.",
                file=sys.stderr,
            )
    Path(args.suggest_phantoms).write_text(format_suggestions(candidates), encoding='utf-8')
    n_reg = sum(1 for c in candidates if c.registered)
    print(
        f"Wrote {len(candidates)} candidate(s) to {args.suggest_phantoms} "
        f"({n_reg} in registered accounts — almost certainly phantom). "
        f"Review, remove any real shorts, then re-run with "
        f"--incomplete-history {args.suggest_phantoms}.",
        file=sys.stderr,
    )


def main():
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        _main()
    except (TransferValidationError, AmbiguousTransferDateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _main():
    args = _parse_args()

    if not args.country:
        sys.exit(2)                            # unreachable: argparse default

    # Argparse can't enforce flag dependencies; surface obvious mistakes early.
    if args.include_options_in_suggestions and not args.suggest_phantoms:
        print(
            "warning: --include-options-in-suggestions has no effect without "
            "--suggest-phantoms; ignoring.",
            file=sys.stderr,
        )
    if args.all_history and not args.suggest_phantoms:
        print(
            "warning: --all-history has no effect without --suggest-phantoms; ignoring.",
            file=sys.stderr,
        )

    # Load input. Bad data (non-finite numbers, impossible dates,
    # SPLIT ratio <= 0, corrupt rows) raises ValueError from the
    # loader guards — report it cleanly, never a stack trace
    # (FUZZ #K).
    try:
        if args.input:
            transactions = load_transactions(Path(args.input))
        else:
            transactions = load_stdin_transactions()
        if args.as_of:
            transactions = [t for t in transactions
                            if (t.date or "9999") <= args.as_of]

        sheltered_transactions = []
        for sheltered_path in args.sheltered:
            sheltered_transactions.extend(
                load_transactions(Path(sheltered_path)))

        affiliated_transactions = []
        if args.affiliated:
            affiliated_transactions = load_transactions(
                Path(args.affiliated))
    except ValueError as e:
        print(f"taxjson-gains: error: {e}", file=sys.stderr)
        raise SystemExit(2)

    req = GainsRequest(
        country=args.country,
        year=args.year,
        taxable=args.taxable,
        tax_date=args.tax_date,
        incomplete_history=args.incomplete_history,
        trace=bool(args.full_traces),
        no_wash=args.no_wash,
        cross_asset=args.cross_asset,
        per_account_basis=args.per_account_basis,
        option_premium_timing=args.option_premium_timing,
        option_grant_since=args.option_grant_since,
        option_buyback_loss_superficial=args.option_buyback_wash,
    )

    if args.suggest_phantoms:
        # Transfer handling runs first, exactly as the normal path would —
        # a taxable input with TRANSFER rows hard-errors here too.
        transactions, sheltered_transactions = _handle_transfers(
            transactions, sheltered_transactions, taxable=args.taxable,
        )
        _suggest_phantoms_and_exit(args, transactions, sheltered_transactions,
                                   affiliated_transactions)
        return

    def trace_sink(results):
        write_traces_file(
            results,
            args.full_traces,
            country=args.country,
            input_path=args.input,
            year=str(args.year) if args.year else None,
            tax_date_basis=req.effective_tax_date(),
        )

    # Engine-level data errors (e.g. an opposite-sign rename
    # merge) raise ValueError — report cleanly, never a
    # stack trace (FUZZ #K/#F10).
    try:
        results = run_gains(
            transactions, sheltered_transactions, affiliated_transactions, req,
            trace_sink=trace_sink if args.full_traces else None,
        )
    except ValueError as e:
        print(f"taxjson-gains: error: {e}", file=sys.stderr)
        raise SystemExit(2)

    # Output
    json.dump(results, sys.stdout, indent=2, sort_keys=True)


def write_traces_file(results, file_path, *, country, input_path, year, tax_date_basis):
    """Dump tt-style ACB/FIFO traces (and wash-sale window traces) to a text
    file. Layout: document header, per-symbol summary, per-gain blocks
    (chronological), then any wash-sale window traces."""
    all_disp = [g for g in results.get('transactions', []) if g.get('action') != 'DIVIDEND']
    gains = [g for g in all_disp if g.get('trace')]
    gains.sort(key=lambda g: (g.get('date', ''), g.get('symbol', '')))
    wash_count = sum(1 for g in all_disp if g.get('is_wash_sale'))

    summary = results.get('summary', {}) or {}
    total_gain = float(summary.get('total_gain', 0.0))
    total_disallowed = float(summary.get('total_disallowed', 0.0))

    # Use the basename rather than the full path — keeps the header readable
    # when the input lives behind a long absolute path or mounted directory.
    input_display = Path(input_path).name if input_path else None

    with open(file_path, 'w', encoding='utf-8') as f:
        for line in render_document_header(
            input_path=input_display,
            country=country,
            year=year,
            tax_date_basis=tax_date_basis,
            disposition_count=len(all_disp),
            wash_sale_count=wash_count,
            total_gain=total_gain,
            total_disallowed=total_disallowed,
        ):
            f.write(line + "\n")

        summary_lines = render_summary_table(all_disp)
        if summary_lines:
            f.write("\n")
            for line in summary_lines:
                f.write(line + "\n")

        for g in gains:
            f.write("\n")
            f.write("\n".join(render_gain_block(g)))
            f.write("\n")


if __name__ == "__main__":
    main()

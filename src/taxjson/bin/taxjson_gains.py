#!/usr/bin/env python3
"""
taxjson_gains.py

Calculate capital gains according to a given country's tax rules.

Usage:
    python -m taxjson.bin.taxjson_gains --country canada [--year 2026] input.json

Thin CLI over taxjson.lib.pipeline.run_gains — argparse + load +
json.dump. ALL gains-run semantics (transfer handling, missing-history
openings,
year filter, tainted split, warnings, fee aggregation) live in the
pipeline module so taxjson-explain and taxjson-audit consume the exact
same definition of "a gains run" and cannot drift from this CLI.

Output is a JSON object with per-security and aggregate totals.
"""

import argparse
import json
import sys
from pathlib import Path

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.country import add_country_argument, refuse_foreign_flags
from taxjson.lib.core import load_transactions
from taxjson.lib.missing_history import detect_missing_history, format_suggestions
# Back-compat re-exports: tests and older callers import these from here.
from taxjson.lib.pipeline import (            # noqa: F401
    GainsRequest,
    add_income_dating_args,
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
        description="Compute capital gains from a merged transaction "
                    "book: Canadian ACB with the superficial-loss rule, or "
                    "US FIFO lots with §1091 wash sales (--country)."
    )
    add_country_argument(parser)
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
            "Path to AFFILIATED-PERSONS' transactions JSON: your spouse or "
            "common-law partner, a corporation you control, or a trust / "
            "partnership affiliated with you (ITA s.251.1; IRC §1091 "
            "spouse / controlled entity). A parent, child or sibling is "
            "related but NOT affiliated — their buys never make your "
            "loss superficial. Their trades feed wash-sale "
            "detection; the "
            "deferred loss attaches to THEIR substituted property and is "
            "tracked on their own return (not yours). Pure additive — pass "
            "only when you have visibility into the affiliated person's data."
        ),
    )
    parser.add_argument("--year", type=tax_year, help="Tax year to calculate (optional)")
    parser.add_argument(
        "--as-of", metavar="YYYY-MM-DD", default=None,
        help="Drop transactions dated after this date before computing "
             "— the books as they stood then (inventory = positions at "
             "that date, incl. ACB and deferred wash). The cutoff reads "
             "the --tax-date basis: the SETTLEMENT date on a settle "
             "basis (a sale traded Dec 31 that settles in January is "
             "still held at Dec 31, as the gains year and t1135 see "
             "it), the trade date on a trade basis.")
    parser.add_argument(
        "--option-premium-timing", choices=["grant", "close"], default=None,
        help="Canada: recognise a written option's premium on the write "
             "date (grant — ITA s.49(1)) or at the closing transaction "
             "(close). Default: close, with a note — `taxjson run` "
             "defaults a Canada project to grant. Refused with "
             "--country usa.")
    parser.add_argument(
        "--option-grant-since", type=tax_year, default=None, metavar="YEAR",
        help="With grant timing: contracts written before YEAR keep close "
             "timing (transition from books filed under close timing).")
    parser.add_argument(
        "--option-buyback-wash", action="store_true",
        help="Canada, either premium timing: treat the loss on buying back a written "
             "option as a superficial loss when identical options are acquired "
             "within 30 days and held (strict reading; default off — a "
             "closing purchase is not a disposition s.54 reaches).")
    add_income_dating_args(parser)
    parser.add_argument(
        "--spot-crypto", action="store_true",
        help="The book is a crypto account's spot coins: a position going "
             "short is missing history (a deposit or transfer-in), said "
             "as an ATTENTION line (`taxjson run` passes it for crypto "
             "accounts).")
    # Retired (2026-09-29): long calls vs share losses are enforced by
    # the Canada engine and always warned by the US engine; nothing is
    # opt-in any more. Accepted so old scripts keep working.
    parser.add_argument("--cross-asset", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument(
        "--per-account-basis", action="store_true", default=None,
        help="Blended multi-account mode (combined taxable input): US "
             "FIFO basis pools are kept per account while wash-sale "
             "matching spans all accounts. The US default (FIFO is per "
             "account); kept for old scripts. Refused with --country "
             "canada — its ACB pools blend per ITA s.47.")
    parser.add_argument(
        "--locked-year", action="append", type=int, default=None,
        metavar="YEAR",
        help="USA: a filed (locked) tax year, repeatable (`taxjson run` "
             "passes every filed/<year>.json). A wash-sale loss whose "
             "replacement was sold in such an earlier year is not added "
             "to that sale's basis: the adjustment is booked in the "
             "loss's year with an ATTENTION line (US-WASH-22).")
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
            "JSON file (a project's missing_history.json) listing "
            "(symbol, account) pairs sold with no purchase in the files "
            "(bought before the data starts). The engine inserts a synthetic OPENING_BALANCE "
            "for each, taints the ACB pool, and excludes affected dispositions "
            "from the gains report (surfaced separately under "
            "'manual_reporting_required')."
        ),
    )
    parser.add_argument(
        "--suggest-missing-history",
        metavar="FILE",
        help=(
            "Detect (symbol, account) pairs whose running position goes "
            "negative (a sale with no purchase in the files) and write "
            "them to FILE as a candidate missing-history file. Review, "
            "remove entries that are actually real shorts, then re-run "
            "with --incomplete-history FILE."
        ),
    )
    # The flag's old name: hidden, still accepted with a note.
    parser.add_argument("--suggest-phantoms", metavar="FILE",
                        dest="suggest_phantoms_old", help=argparse.SUPPRESS)
    parser.add_argument(
        "--include-options-in-suggestions",
        action="store_true",
        help=(
            "By default --suggest-missing-history skips option (OCC-format) symbols "
            "because negative option positions are normal (sell-to-open for "
            "covered calls etc.). Use this flag to include them anyway."
        ),
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help=(
            "By default, when --year is specified, --suggest-missing-history only "
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
    if args.suggest_phantoms_old:
        print("taxjson-gains: note: --suggest-phantoms is now "
              "--suggest-missing-history (the old flag still works).",
              file=sys.stderr)
        if not args.suggest_missing_history:
            args.suggest_missing_history = args.suggest_phantoms_old
    # --country is required (partition audit R1: a missing country was
    # a noted Canada here and a silent one elsewhere).
    return args


def _suggest_missing_history_and_exit(args, transactions,
                                      sheltered_transactions,
                                      affiliated_transactions) -> None:
    """--suggest-missing-history mode: detect candidates, write the file, exit
    before computing gains. The user reviews the candidate file, prunes
    any real shorts, then re-runs with --incomplete-history."""
    all_for_detection = transactions + sheltered_transactions + affiliated_transactions
    # Registered status from the project's configured account types when
    # the book sits in a project (work/<acct>_base.json), else from the
    # books' roles — never from the label alone (audit S076-08).
    from taxjson.lib.missing_history import (account_types_near,
                                              assess_tax_year_relevance)
    types = {t.account: not args.taxable for t in transactions if t.account}
    types.update({t.account: True for t in sheltered_transactions
                  if t.account})
    if args.input:
        types.update(account_types_near(args.input))
    candidates = detect_missing_history(
        all_for_detection,
        include_options=args.include_options_in_suggestions,
        registered_accounts=types,
        country=args.country,
    )
    # Year-scope unless --all-history, with the SAME rule as the
    # find-missing-history report (missing_history.
    # assess_tax_year_relevance): a pair is year-relevant when an in-year
    # row draws on the short (missing-purchase) state — a sale while
    # short, or the BUY that covers a short carried in (dropping that pair let
    # the engine book the cover as a clean short-close gain in the target
    # year). The year of a row is its date on the tax_date basis, so a
    # Dec-31 cover settling in January counts for January's year (audit
    # S033-12, S076-07).
    if args.year and not args.all_history:
        year_str = str(args.year)
        basis = args.tax_date or (
            'trade' if args.country == 'usa' else 'settle')
        rows = assess_tax_year_relevance(all_for_detection, candidates,
                                         args.year, date_basis=basis)
        keep = {(r.candidate.symbol, r.candidate.account)
                for r in rows if r.affects_year}
        before = len(candidates)
        candidates = [c for c in candidates
                      if (c.symbol, c.account) in keep]
        if before > len(candidates):
            print(
                f"Filtered {before - len(candidates)} candidate(s) outside tax year "
                f"{year_str}. Use --all-history to include them.",
                file=sys.stderr,
            )
    _out = Path(args.suggest_missing_history)
    # A reviewed file (real shorts pruned, pairs added by hand) is a
    # user record: never rewritten (audit A2-0312). An empty file (the
    # `find-missing-history --write-missing-history` wrapper's temp file)
    # is ours.
    if _out.is_file() and _out.stat().st_size > 0:
        print(f"taxjson-gains: error: --suggest-missing-history {_out} "
              f"already exists — not overwritten (it may be a reviewed "
              f"missing_history.json). Write to a new file and merge by hand, or "
              f"delete it first.", file=sys.stderr)
        raise SystemExit(2)
    # 'cannot write <path>: ...' for a directory or a missing folder,
    # not 'cannot read' / 'no such file' (re-audit A2-1432).
    from taxjson.lib.cli_diag import write_text_atomic
    write_text_atomic(_out, format_suggestions(candidates))
    n_reg = sum(1 for c in candidates if c.registered)
    print(
        f"Wrote {len(candidates)} candidate(s) to {args.suggest_missing_history} "
        f"({n_reg} in registered accounts — almost certainly a purchase "
        f"missing from your files). "
        f"Review, remove any real shorts, then re-run with "
        f"--incomplete-history {args.suggest_missing_history}.",
        file=sys.stderr,
    )


def _timing_default_note(args, prog):
    """Standalone runs default to CLOSE timing, while a Canada project
    (`taxjson run`) defaults to s.49(1) grant timing from the project
    year: say so instead of silently disagreeing with the .sum (audit
    R1-177)."""
    if args.option_premium_timing is None and args.country == 'canada':
        print(f"{prog}: note: --option-premium-timing not given — using "
              f"close timing. `taxjson run` on a Canada project uses "
              f"grant timing from the project year; pass "
              f"--option-premium-timing grant --option-grant-since YEAR "
              f"to match it.", file=sys.stderr)
    if args.option_premium_timing is None:
        args.option_premium_timing = 'close'


def _request(args) -> GainsRequest:
    """The GainsRequest of the parsed flags (an invalid income-dating
    override raises ValueError)."""
    return GainsRequest(
        country=args.country,
        year=args.year,
        taxable=args.taxable,
        tax_date=args.tax_date,
        incomplete_history=args.incomplete_history,
        trace=bool(args.full_traces),
        no_wash=args.no_wash,
        per_account_basis=args.per_account_basis,
        option_premium_timing=args.option_premium_timing,
        option_grant_since=args.option_grant_since,
        option_buyback_loss_superficial=args.option_buyback_wash,
        corporate_distributions=tuple(args.corporate_distribution or ()),
        ric_january_dividends=tuple(args.ric_january_dividend or ()),
        spot_crypto=args.spot_crypto,
        locked_years=tuple(args.locked_year or ()),
    )


@guard_main("taxjson-gains")
def main():
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        _main()
    except (TransferValidationError, AmbiguousTransferDateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _main():
    args = _parse_args()
    refuse_foreign_flags(args, "taxjson-gains")
    _timing_default_note(args, "taxjson-gains")

    # Argparse can't enforce flag dependencies; surface obvious mistakes early.
    if args.include_options_in_suggestions and not args.suggest_missing_history:
        print(
            "warning: --include-options-in-suggestions has no effect without "
            "--suggest-missing-history; ignoring.",
            file=sys.stderr,
        )
    if args.all_history and not args.suggest_missing_history:
        print(
            "warning: --all-history has no effect without "
            "--suggest-missing-history; ignoring.",
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
            # The same date basis as the year filter (R1-10): a
            # trade-date cutoff on a settle project dropped a Dec-31
            # sale that is a next-year disposition everywhere else.
            _areq = GainsRequest(
                country=args.country, tax_date=args.tax_date,
                corporate_distributions=tuple(
                    args.corporate_distribution or ()),
                ric_january_dividends=tuple(
                    args.ric_january_dividend or ()))
            _basis = _areq.effective_tax_date()
            # A Canadian trust's return of capital lowers the ACB on its
            # record date (CA-INC-DATE-ROC-TRUST): the engine moves it
            # there, so the cutoff must judge it by that date too, not
            # drop a January-paid ROC whose record date is before the
            # cutoff (A2-0554/0960).
            _ir = _areq.income_rules()

            def _asof_date(t):
                _rec = _ir.roc_record_date(t)
                if _rec:
                    return _rec
                if _basis == "settle":
                    return t.date_settle or t.date or "9999"
                return t.date or "9999"
            transactions = [t for t in transactions
                            if _asof_date(t) <= args.as_of]

        sheltered_transactions = []
        for sheltered_path in args.sheltered:
            sheltered_transactions.extend(
                load_transactions(Path(sheltered_path)))

        affiliated_transactions = []
        if args.affiliated:
            affiliated_transactions = load_transactions(
                Path(args.affiliated))
        from taxjson.lib.core import require_trade_fields
        require_trade_fields(transactions + sheltered_transactions
                             + affiliated_transactions)
    except ValueError as e:
        print(f"taxjson-gains: error: {e}", file=sys.stderr)
        raise SystemExit(2)

    try:
        req = _request(args)
    except ValueError as e:
        print(f"taxjson-gains: error: {e}", file=sys.stderr)
        raise SystemExit(2)

    if args.suggest_missing_history:
        # Transfer handling runs first, exactly as the normal path would —
        # a taxable input with TRANSFER rows hard-errors here too.
        transactions, sheltered_transactions = _handle_transfers(
            transactions, sheltered_transactions, taxable=args.taxable,
        )
        _suggest_missing_history_and_exit(args, transactions,
                                          sheltered_transactions,
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

        # Unknown-cost dispositions (--incomplete-history): not in the
        # totals above; shown for the manual report (audit R1-165).
        manual = [g for g in results.get('manual_reporting_required') or []
                  if g.get('trace')]
        if manual:
            f.write("\n# " + "=" * 90 + "\n")
            f.write(f"# MANUAL REPORTING — {len(manual)} sale(s) with "
                    f"no purchase in your files, cost unknown, excluded "
                    f"from the "
                    f"totals above\n")
            for g in sorted(manual, key=lambda g: (g.get('date', ''),
                                                   g.get('symbol', ''))):
                f.write("\n")
                f.write("\n".join(render_gain_block(g, manual=True)))
                f.write("\n")


if __name__ == "__main__":
    main()

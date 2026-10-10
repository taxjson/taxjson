#!/usr/bin/env python3
"""
taxjson_explain.py

Print a tt-style calculation trace for one or more capital gains.

Reads the same transaction-JSON input as taxjson_gains.py, re-runs the engine
with trace=True, and pretty-prints the per-gain ACB (Canada) or FIFO (USA)
audit history. Useful for understanding *why* a particular gain came out the
way it did — every pool-affecting transaction that fed the disposition is
shown, then the disposition itself with cost basis / proceeds / gain.

Examples:
    # Every gain in the file
    taxjson-explain --country canada txs.json

    # One symbol
    taxjson-explain --country canada --symbol SAMPLG.USD txs.json

    # A specific disposition by tx id (16-char or any unique prefix)
    taxjson-explain --country canada --id 1a2b3c4d txs.json

    # Disposition on a specific date
    taxjson-explain --country canada --date 2024-03-10 txs.json

    # One-line summaries (use this first to find ids worth diving into)
    taxjson-explain --country canada --list txs.json

    # Only the wash-sale gains (each with its trigger lot)
    taxjson-explain --country canada --wash-sales txs.json
"""

from taxjson.lib.out import exit_text
from taxjson.lib.stage_msg import emit_line
import argparse
import os
import sys
from pathlib import Path

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.core import get_tax_rules
from taxjson.lib.json_input import load_transactions_or_exit
from taxjson.lib.country import (add_country_argument, default_tax_date,
                                 refuse_foreign_flags)
from taxjson.lib.pipeline import (GainsRequest, apply_roc_record_dates,
                                  engine_options, load_stdin_transactions,
                                  loss_overrides_from_args, prepare_books)
from taxjson.lib.trace_format import (render_gain_block,
                                      render_report_block)


COLORS = {
    'header': '\033[1;36m',  # bold cyan
    'rule':   '\033[0;36m',  # cyan
    'gain':   '\033[1;32m',  # bold green
    'loss':   '\033[1;31m',  # bold red
    'wash':   '\033[1;33m',  # bold yellow
    'dim':    '\033[2m',
    'reset':  '\033[0m',
}


def colorize(text: str, key: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{COLORS[key]}{text}{COLORS['reset']}"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print calculation traces for capital gains (tt-style ACB/FIFO audit).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    add_country_argument(parser)
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to transactions JSON (default: stdin)",
    )
    parser.add_argument(
        "--sheltered",
        action="append",
        default=[],
        metavar="FILE",
        help="Path to tax-sheltered transactions JSON (for wash-sale "
             "detection); repeatable, as in taxjson-gains",
    )
    parser.add_argument(
        "--affiliated",
        help="Path to affiliated-persons' transactions JSON (spouse or "
             "common-law partner, controlled corporation, affiliated trust "
             "or partnership — ITA s.251.1; a parent, child or sibling is "
             "related but not affiliated).",
    )
    parser.add_argument("--symbol", help="Filter to a symbol; case-insensitive prefix match (e.g. samplg matches SAMPLG.US).")
    parser.add_argument("--date", help="Filter to one disposition date (YYYY-MM-DD).")
    parser.add_argument("--id", dest="gain_id", help="Filter to one tx id (prefix match).")
    parser.add_argument("--year", type=tax_year, help="Filter to one tax year.")
    parser.add_argument(
        "--tax-date",
        choices=["trade", "settle"],
        default=None,
        help=(
            "Which date the --year and --date filters match against. Default "
            "is COUNTRY-AWARE like taxjson-gains: 'settle' for canada (CRA), "
            "'trade' for usa (IRS)."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print one-line summaries instead of full traces (good for finding ids).",
    )
    parser.add_argument(
        "--wash-sales",
        action="store_true",
        help="Filter to wash-sale gains only (each block now includes its trigger lot inline).",
    )
    parser.add_argument(
        "--incomplete-history", metavar="DIR",
        help="the project folder: its accounts' .tt OPENING cost=unknown "
             "lines (units bought before the data) — applied "
             "exactly like taxjson-gains, so traces match the pipeline's "
             "books.")
    parser.add_argument("--option-premium-timing", choices=["grant", "close"],
                        default=None, help="Canada: s.49(1) grant timing "
                        "or close timing for written options (match the "
                        "run). Default: close, with a note — `taxjson run` "
                        "defaults a Canada project to grant.")
    parser.add_argument("--option-grant-since", type=tax_year, default=None,
                        metavar="YEAR")
    parser.add_argument("--option-buyback-wash", action="store_true")
    parser.add_argument("--transfers-as-acquisitions", action="store_true",
                        help="Count a sheltered account's unmatched TRANSFER rows as "
                             "acquisitions/disposals for the superficial-loss / wash-sale "
                             "window (strict; [settings] transfers_as_acquisitions = true). "
                             "Default: a custody move, held but never a purchase.")
    from taxjson.lib.pipeline import add_loss_override_args
    add_loss_override_args(parser)
    parser.add_argument(
        "--corporate-distribution", action="append", default=None,
        metavar="SYMBOL",
        help="Canada: as in taxjson-gains ([settings] "
             "corporate_distributions) — the same income dating the "
             "books used.")
    parser.add_argument(
        "--ric-january-dividend", action="append", default=None,
        metavar="\"SYMBOL [YYYY-01-DD]\"",
        help="USA: as in taxjson-gains ([settings] "
             "ric_january_dividends).")
    parser.add_argument(
        "--per-account-basis", action="store_true", default=None,
        help="USA: FIFO basis pools per account on a merged book — the "
             "default for a US book, as in taxjson-gains.")
    parser.add_argument(
        "--no-wash", action="store_true",
        help="Skip superficial-loss / wash-sale detection. Use it for a "
             "REGISTERED (sheltered) account's book: `taxjson run` "
             "computes those with the rule off, and without this flag "
             "explain applies it as if the book were taxable.")
    parser.add_argument("--color", action="store_true",
                        help="Enable ANSI color output (off by default).")
    parser.add_argument(
        "--no-align",
        action="store_true",
        help="Don't re-pad pipe columns — preserve the engine's raw trace strings verbatim.",
    )
    parser.add_argument(
        "--layout", choices=["trace", "report"], default="trace",
        help="trace (default): the '#'-commented blocks trace files and "
             "`taxjson audit` use; report: the house report layout, "
             "wrapped to the terminal (`taxjson wash-sales --explain`).")
    return parser.parse_args()


def load_input(args):
    if args.input:
        return load_transactions_or_exit("taxjson-explain", args.input)
    try:
        return load_stdin_transactions()
    except (ValueError, TypeError, AttributeError) as e:
        sys.exit(exit_text(f"taxjson-explain: error: <stdin>: {e}"))


def gain_matches(g, args) -> bool:
    if g.get('action') == 'DIVIDEND':
        return False
    date_key = 'date_settle' if args.tax_date == 'settle' else 'date'
    effective_date = g.get(date_key) or g.get('date', '')
    # Case-insensitive, like `taxjson audit` and buy-check (S029-19).
    if args.symbol and not str(g.get('symbol') or '').upper().startswith(
            args.symbol.upper()):
        return False
    if args.date and effective_date != args.date:
        return False
    if args.gain_id and not (g.get('id') or '').startswith(args.gain_id):
        return False
    if args.year and not effective_date.startswith(str(args.year)):
        return False
    # A loss the rule would deny that a .tt ALLOWLOSS line claims is
    # listed too: its trace says what the rule would deny and why
    # ("no matching gains found" hid it, pre-release review M7).
    if (args.wash_sales and not g.get('is_wash_sale')
            and not g.get('loss_override')):
        return False
    return True


def fmt_summary(g) -> str:
    gid = (g.get('id') or '')[:10]
    date = g.get('date', '')
    sym = g.get('symbol', '')
    qty = g.get('qty', 0.0)
    cost = g.get('cost', 0.0)
    proc = g.get('proceeds', 0.0)
    gain = g.get('gain', 0.0)
    dis = g.get('disallowed_amount', 0.0)
    direction = g.get('direction', '')
    tag = ''
    if g.get('tainted'):
        # Unknown-cost disposition (--incomplete-history): the pipeline
        # routes it to manual reporting with no gain (audit S029-22).
        return (
            f"{gid}  {date}  {sym:<14}  qty={qty:>10.4f}  "
            f"proc={proc:>12.4f}  MANUAL REPORTING — no purchase in your "
            f"files (cost unknown); gain not computed, not in the gains "
            f"total"
            f"  ({direction})")
    if dis > 0.001:
        tag += f"  WASH+{dis:.2f}"
    if isinstance(g.get('loss_override'), dict):
        # A .tt ALLOWLOSS filing position (CA-SL-18 / US-WASH-25).
        tag += (f"  ALLOWLOSS(rule: "
                f"{float(g['loss_override'].get('would_disallow') or 0):.2f})")
    if g.get('term'):
        tag += f"  [{g['term']}]"
    return (
        f"{gid}  {date}  {sym:<14}  qty={qty:>10.4f}  "
        f"cost={cost:>12.4f}  proc={proc:>12.4f}  gain={gain:>+12.4f}"
        f"{tag}  ({direction})"
    )


def is_disposition_line(line: str) -> bool:
    return ('Gain:' in line) or ('AllowedGain' in line) or ('RawGain' in line)


def print_trace(g, args, use_color):
    block = render_gain_block(g, align=not args.no_align,
                              manual=bool(g.get('tainted')))
    if not block:
        print(f"# (no trace produced for {g.get('id','?')})", file=sys.stderr)
        return

    # render_gain_block returns: [rule, header, rule, *trace_lines, rule]
    rule_top, header_line, rule_mid = block[0], block[1], block[2]
    body = block[3:-1]
    rule_bot = block[-1]

    gain_amt = g.get('gain', 0.0)
    dis = g.get('disallowed_amount', 0.0)
    color_key = 'wash' if dis > 0.001 else ('gain' if gain_amt >= 0 else 'loss')

    print()
    print(colorize(rule_top, 'rule', use_color))
    print(colorize(header_line, color_key, use_color))
    print(colorize(rule_mid, 'rule', use_color))
    in_wash_block = False
    for line in body:
        if line.startswith('# --- WASH SALE'):
            in_wash_block = True
            print(colorize(line, 'wash', use_color))
        elif in_wash_block and (line == '#' or line.startswith('#   ') or line.startswith('#     ')):
            print(colorize(line, 'wash', use_color))
        elif 'CALCULATION TRACE' in line:
            in_wash_block = False
            print(colorize(line, 'header', use_color))
        elif is_disposition_line(line):
            in_wash_block = False
            print(colorize(line, color_key, use_color))
        else:
            in_wash_block = False
            print(line)
    print(colorize(rule_bot, 'rule', use_color))


def _timing_default_note(args, prog):
    """Standalone runs default to CLOSE timing, while a Canada project
    (`taxjson run`) defaults to s.49(1) grant timing from the project
    year: say so instead of silently disagreeing with the .sum (audit
    R1-177)."""
    if args.option_premium_timing is None and args.country == 'canada':
        emit_line(f"{prog}: note: --option-premium-timing not given — using "
                  f"close timing. `taxjson run` on a Canada project uses "
                  f"grant timing from the project year; pass "
                  f"--option-premium-timing grant --option-grant-since YEAR "
                  f"to match it.", file=sys.stderr)
    if args.option_premium_timing is None:
        args.option_premium_timing = 'close'


@guard_main("taxjson-explain", value_errors=True)
def main():
    args = parse_args()
    refuse_foreign_flags(args, "taxjson-explain")
    _timing_default_note(args, "taxjson-explain")
    # Country-aware --tax-date default (matches taxjson-gains): settle for
    # canada (CRA), trade for usa (IRS).
    if args.tax_date is None:
        args.tax_date = default_tax_date(args.country)
    transactions = load_input(args)
    # Repeatable (A2-0194): a second --sheltered used to replace the
    # first silently, and the superficial-loss denial it backed vanished.
    sheltered = [t for f in args.sheltered
                 for t in load_transactions_or_exit("taxjson-explain", f)]
    affiliated = (load_transactions_or_exit("taxjson-explain",
                                            args.affiliated)
                  if args.affiliated else [])

    # SHARED preprocessing (lib/pipeline.prepare_books) so a trace can't
    # contradict the .sum it explains: missing-history opening balances
    # (--incomplete-history, same file `taxjson run` auto-applies),
    # self-cancelling TRANSFER pairs dropped, remaining main-file
    # TRANSFERs rewritten to BUYSELL (sheltered approximation — explain
    # never hard-errors: taxable books coming out of the pipeline carry
    # no TRANSFERs, so the rewrite is a no-op for them), and
    # sheltered-context TRANSFERs stripped so they can't act as wash
    # triggers. Hint off: explain keeps its stderr to traces.
    transactions, sheltered, affiliated, _ = prepare_books(
        transactions, sheltered, affiliated, taxable=False,
        incomplete_history=(Path(args.incomplete_history)
                            if getattr(args, 'incomplete_history', None)
                            else None),
        phantom_hint=False, country=args.country,
        transfers_as_acquisitions=getattr(
            args, 'transfers_as_acquisitions', False))

    # The income re-dating and engine options run_gains applies (one
    # builder in lib/pipeline): the trust ROC record date
    # (CA-INC-DATE-ROC-TRUST), the grant-timing since-year on the
    # tax-date basis, US per-account FIFO — a trace on other inputs
    # contradicted the books it explains (re-audit A2-0033, A2-0314,
    # A2-0315).
    try:
        req = GainsRequest(
            country=args.country, tax_date=args.tax_date,
            per_account_basis=args.per_account_basis,
            option_premium_timing=args.option_premium_timing,
            option_grant_since=args.option_grant_since,
            option_buyback_loss_superficial=args.option_buyback_wash,
            corporate_distributions=tuple(
                args.corporate_distribution or ()),
            ric_january_dividends=tuple(args.ric_january_dividend or ()),
            loss_overrides=loss_overrides_from_args(args))
    except ValueError as e:
        sys.exit(exit_text(f"taxjson-explain: error: {e}"))
    apply_roc_record_dates(transactions, req)
    rules = get_tax_rules(args.country)
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        _kw = engine_options(req)
        results = rules.compute_gains(
            transactions,
            sheltered_transactions=sheltered,
            affiliated_transactions=affiliated,
            trace=True,
            detect_wash_sales=not args.no_wash,
            **_kw,
        )
    except AmbiguousTransferDateError as e:
        sys.exit(exit_text(f"taxjson-explain: {e}"))

    # Color is opt-in (--color); plain text everywhere else.
    use_color = (
        args.color
        and sys.stdout.isatty()
        and not os.environ.get('NO_COLOR')
    )

    matches = [g for g in results['transactions'] if gain_matches(g, args)]

    if not matches:
        # "No data" is a success (exit 0) — consistent with gains/fees/
        # list/wash-sales; the note keeps stdout clean for report content.
        emit_line("taxjson-explain: note: no matching gains found", file=sys.stderr)
        return

    if args.list:
        for g in matches:
            print(fmt_summary(g))
        return

    if args.layout == "report":
        # One blank line between blocks, none before the first.
        for i, g in enumerate(matches):
            block = render_report_block(g, manual=bool(g.get('tainted')),
                                        country=args.country)
            if not block:
                emit_line(f"taxjson-explain: note: no trace produced for "
                          f"{g.get('id', '?')}", file=sys.stderr)
                continue
            if i:
                print()
            for ln in block:
                print(ln)
        return

    for g in matches:
        print_trace(g, args, use_color)


if __name__ == "__main__":
    main()

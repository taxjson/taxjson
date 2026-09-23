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
    taxjson-explain --country canada --symbol AAPL.USD txs.json

    # A specific disposition by tx id (16-char or any unique prefix)
    taxjson-explain --country canada --id 1a2b3c4d txs.json

    # Disposition on a specific date
    taxjson-explain --country canada --date 2024-03-10 txs.json

    # One-line summaries (use this first to find ids worth diving into)
    taxjson-explain --country canada --list txs.json

    # Include the wash-sale window traces too
    taxjson-explain --country canada --wash-sales txs.json
"""

import argparse
import os
import sys
from pathlib import Path

from taxjson.lib.core import get_tax_rules, load_transactions
from taxjson.lib.pipeline import load_stdin_transactions, prepare_books
from taxjson.lib.trace_format import render_gain_block


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
    parser.add_argument(
        "--country",
        choices=["canada", "ca", "usa", "us"],
        default="canada",
        help="Country for tax rules (default: canada)",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to transactions JSON (default: stdin)",
    )
    parser.add_argument(
        "--sheltered",
        help="Path to tax-sheltered transactions JSON (for wash-sale detection)",
    )
    parser.add_argument(
        "--affiliated",
        help="Path to affiliated-persons' transactions JSON (spouse / related / corp).",
    )
    parser.add_argument("--symbol", help="Filter to a symbol; prefix match (e.g. AAPL matches AAPL.USD).")
    parser.add_argument("--date", help="Filter to one disposition date (YYYY-MM-DD).")
    parser.add_argument("--id", dest="gain_id", help="Filter to one tx id (prefix match).")
    parser.add_argument("--year", type=int, help="Filter to one tax year.")
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
        "--incomplete-history", metavar="FILE",
        help="phantoms.json of (symbol, account) pairs with missing pre-data "
             "history — applied exactly like taxjson-gains, so traces match "
             "the pipeline's books.")
    parser.add_argument("--option-premium-timing", choices=["grant", "close"],
                        default="close", help="Canada: s.49(1) grant timing "
                        "or close timing for written options (match the run).")
    parser.add_argument("--option-grant-since", type=int, default=None,
                        metavar="YEAR")
    parser.add_argument("--option-buyback-wash", action="store_true")
    parser.add_argument("--color", action="store_true",
                        help="Enable ANSI color output (off by default).")
    parser.add_argument(
        "--no-align",
        action="store_true",
        help="Don't re-pad pipe columns — preserve the engine's raw trace strings verbatim.",
    )
    return parser.parse_args()


def load_input(args):
    if args.input:
        return load_transactions(Path(args.input))
    return load_stdin_transactions()


def gain_matches(g, args) -> bool:
    if g.get('action') == 'DIVIDEND':
        return False
    date_key = 'date_settle' if args.tax_date == 'settle' else 'date'
    effective_date = g.get(date_key) or g.get('date', '')
    if args.symbol and not g.get('symbol', '').startswith(args.symbol):
        return False
    if args.date and effective_date != args.date:
        return False
    if args.gain_id and not (g.get('id') or '').startswith(args.gain_id):
        return False
    if args.year and not effective_date.startswith(str(args.year)):
        return False
    if args.wash_sales and not g.get('is_wash_sale'):
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
    if dis > 0.001:
        tag += f"  WASH+{dis:.2f}"
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
    block = render_gain_block(g, align=not args.no_align)
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


def main():
    args = parse_args()
    # Country-aware --tax-date default (matches taxjson-gains): settle for
    # canada (CRA), trade for usa (IRS).
    if args.tax_date is None:
        args.tax_date = ('trade'
                         if (args.country or '').strip().lower() in ('us', 'usa')
                         else 'settle')
    transactions = load_input(args)
    sheltered = load_transactions(Path(args.sheltered)) if args.sheltered else []
    affiliated = load_transactions(Path(args.affiliated)) if args.affiliated else []

    # SHARED preprocessing (lib/pipeline.prepare_books) so a trace can't
    # contradict the .sum it explains: phantom opening balances
    # (--incomplete-history, same file `taxjson run` auto-applies),
    # self-cancelling TRANSFER pairs dropped, remaining main-file
    # TRANSFERs rewritten to BUYSELL (sheltered approximation — explain
    # never hard-errors: taxable books coming out of the pipeline carry
    # no TRANSFERs, so the rewrite is a no-op for them), and
    # sheltered-context TRANSFERs stripped so they can't act as wash
    # triggers. phantom_hint off: explain keeps its stderr to traces.
    transactions, sheltered, affiliated, _ = prepare_books(
        transactions, sheltered, affiliated, taxable=False,
        incomplete_history=(Path(args.incomplete_history)
                            if getattr(args, 'incomplete_history', None)
                            else None),
        phantom_hint=False)

    rules = get_tax_rules(args.country)
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        _kw = {}
        if str(args.country).strip().lower() not in ("us", "usa"):
            _kw = {"option_premium_timing": args.option_premium_timing,
                   "option_grant_since": args.option_grant_since,
                   "option_buyback_loss_superficial": args.option_buyback_wash}
        results = rules.compute_gains(
            transactions,
            sheltered_transactions=sheltered,
            affiliated_transactions=affiliated,
            trace=True,
            **_kw,
        )
    except AmbiguousTransferDateError as e:
        sys.exit(f"taxjson-explain: {e}")

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
        print("taxjson-explain: note: no matching gains found", file=sys.stderr)
        return

    if args.list:
        for g in matches:
            print(fmt_summary(g))
        return

    for g in matches:
        print_trace(g, args, use_color)


if __name__ == "__main__":
    main()

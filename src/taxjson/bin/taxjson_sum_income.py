#!/usr/bin/env python3
"""
taxjson_sum_income.py

Summarize income from taxjson_income.py output.

Usage:
    python -m taxjson.bin.taxjson_sum_income [--sort-by FIELD] [file1.json file2.json ...]

Sort options:
    --sort-by net|dividend|tax|ticker

If no files provided, reads from stdin.
"""

import argparse
import json
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.report_model import load_report_json
from taxjson.lib.ticker_map import get_underlying, is_option_ticker


def load_income_data(file_path: Path = None) -> List[Dict[str, Any]]:
    """Load income data from a file or stdin."""
    raw = load_report_json(file_path)

    # taxjson_income outputs a dict with transactions list
    if isinstance(raw, dict) and "transactions" in raw:
        return raw["transactions"]
    elif isinstance(raw, list):
        return raw
    return []


def get_base_ticker(symbol: str) -> str:
    """Group an income symbol under its underlying, exactly like the gains
    half of the .sum does. The old local implementation split on the last
    dot and rejoined — an identity function apart from a leading-dot strip
    — so a PIL/dividend row on an OCC option symbol bucketed under the raw
    contract while sum_gains bucketed the same event's gains under the
    underlying, and the two halves of one .sum disagreed."""
    symbol = (symbol or '').lstrip('.')
    if is_option_ticker(symbol):
        underlying = get_underlying(symbol)
        if underlying:
            return underlying
    return symbol


def summarize_income(transactions: List[Dict[str, Any]], target_year: int = None) -> Dict[str, Any]:
    """Process income data and create summary."""
    ticker_stats: Dict[str, Dict] = {}
    interest_totals: Dict[str, float] = {}
    
    for tx in transactions:
        # `tx['date']` is sometimes explicitly None (came in as JSON
        # null) — `tx.get('date', '')` returns None in that case and
        # the [:4] slice would have crashed. Use `or ''` to fold both
        # missing-and-None into the empty-string path which the year
        # filter below already tolerates.
        date = str((tx.get('date') or '')[:4])
        
        # Filter by year if specified
        if target_year and not date.startswith(str(target_year)):
            continue
        
        action = tx.get('action', '').upper()
        type_ = tx.get('type', '').lower()
        symbol = tx.get('symbol', 'UNKNOWN')
        # '?' for missing currency, matching sum_gains — a USD
        # default let the two halves of one .sum bucket the same
        # account's rows differently.
        currency = tx.get('currency') or '?'
        
        # Get amount - gross_amount is primary for dividends, net_amount for others
        if action in ('DIVIDEND', 'DIVIDEND_IN_LIEU') or type_ in ('dividend', 'dividend_in_lieu'):
            amount_raw = tx.get('gross_amount') or tx.get('net_amount') or tx.get('amount')
        else:
            amount_raw = tx.get('net_amount') or tx.get('gross_amount') or tx.get('amount')

        amount = float(amount_raw or 0)

        base_ticker = get_base_ticker(symbol)

        # DIVIDEND_IN_LIEU (Payment-in-Lieu): the broker had your shares
        # loaned out, so the issuer's dividend was paid to someone else and
        # you got a substitute payment. CRA treats PIL as ordinary income
        # (no dividend tax credit); IRS treats it as a non-qualified
        # dividend (ordinary rate). Bucket it per-ticker in its own column
        # so reconciliation against broker statements stays easy without
        # inflating the eligible-dividend total used for T5 / Schedule B.
        if action == 'DIVIDEND_IN_LIEU' or type_ == 'dividend_in_lieu':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            ticker_stats[base_ticker][currency].setdefault('pil', 0.0)
            ticker_stats[base_ticker][currency]['pil'] += amount
            continue

        if action == 'DIVIDEND' or type_ == 'dividend':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            ticker_stats[base_ticker][currency]['div'] += amount

        elif action == 'TAX' or type_ == 'tax':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            # Sign-respecting: parsers emit positive = tax withheld (RBC is
            # always positive; IB flips its negative cash amounts at parse).
            # A withholding refund/reversal arrives NEGATIVE and must net
            # against the charge — abs() double-counted refunds as tax paid.
            ticker_stats[base_ticker][currency]['tax'] += amount

        elif action == 'INTEREST' or type_ == 'interest':
            if currency not in interest_totals:
                interest_totals[currency] = 0.0
            interest_totals[currency] += amount

        # taxjson_income.py puts some income under "other"
        elif type_ == 'other':
            if base_ticker not in ticker_stats:
                ticker_stats[base_ticker] = {}
            if currency not in ticker_stats[base_ticker]:
                ticker_stats[base_ticker][currency] = {'div': 0.0, 'tax': 0.0, 'pil': 0.0}
            ticker_stats[base_ticker][currency]['div'] += amount
    
    return {
        'ticker_stats': ticker_stats,
        'interest_totals': interest_totals,
        'total_year': target_year or 'all'
    }


def format_report(data: Dict[str, Any], sort_by: str = 'ticker') -> str:
    """Format the summary as a text report."""
    lines = []
    ticker_stats = data['ticker_stats']
    interest_totals = data.get('interest_totals', {})
    total_year = data.get('total_year', 'all')
    
    all_currencies = set()
    for base in ticker_stats:
        for curr in ticker_stats[base]:
            all_currencies.add(curr)
    # Interest-only currencies (no dividend rows) need to render too —
    # otherwise CASH INTEREST silently disappears from the report.
    for curr in interest_totals:
        all_currencies.add(curr)
    # Zero-income input still produces an empty table so downstream
    # consumers and visual inspection see a consistent layout.
    if not all_currencies:
        all_currencies = {''}

    def get_sort_value(ticker: str, currency: str, key: str) -> float:
        stats = ticker_stats[ticker].get(currency, {})
        div = stats.get('div', 0)
        tax = stats.get('tax', 0)
        pil = stats.get('pil', 0)

        if key in ('net', 'total_gain'):
            return div + pil - tax
        elif key in ('dividend', 'div'):
            return div
        elif key == 'pil':
            return pil
        elif key == 'tax':
            return tax
        return 0

    for currency in sorted(all_currencies):
        lines.append("")
        cur_tag = f"{currency}, " if currency else ""
        lines.append(f"INCOME SUMMARY — {cur_tag}year {total_year}, sorted by {sort_by}")
        lines.append("")
        lines.append(
            "SYMBOL".ljust(26) +
            "GROSS DIV".rjust(17) +
            "PIL".rjust(17) +
            "TAX W/H".rjust(17) +
            "NET INCOME".rjust(17)
        )
        lines.append("-" * 94)

        tickers_in_curr = [t for t in ticker_stats if currency in ticker_stats[t]]

        if sort_by == 'ticker':
            sorted_tickers = sorted(tickers_in_curr)
        else:
            sorted_tickers = sorted(
                tickers_in_curr,
                key=lambda t: (-get_sort_value(t, currency, sort_by), t)
            )

        totals = {'div': 0.0, 'pil': 0.0, 'tax': 0.0, 'net': 0.0}

        for ticker in sorted_tickers:
            stats = ticker_stats[ticker][currency]
            div = stats.get('div', 0)
            tax = stats.get('tax', 0)
            pil = stats.get('pil', 0)
            net = div + pil - tax

            if abs(div) < 1e-4 and abs(tax) < 1e-4 and abs(pil) < 1e-4:
                continue

            totals['div'] += div
            totals['pil'] += pil
            totals['tax'] += tax
            totals['net'] += net

            lines.append(
                ticker.ljust(26) +
                f"{div:17,.2f}" +
                f"{pil:17,.2f}" +
                f"{tax:17,.2f}" +
                f"{net:17,.2f}"
            )

        interest = interest_totals.get(currency, 0)
        grand_total = totals['net'] + interest

        lines.append("-" * 94)
        lines.append(
            "SUBTOTAL".ljust(26) +
            f"{totals['div']:17,.2f}" +
            f"{totals['pil']:17,.2f}" +
            f"{totals['tax']:17,.2f}" +
            f"{totals['net']:17,.2f}"
        )
        lines.append(
            "CASH INTEREST".ljust(26) +
            "".rjust(17) +
            "".rjust(17) +
            "".rjust(17) +
            f"{interest:17,.2f}"
        )
        lines.append("-" * 94)
        lines.append(
            "TOTAL NET INCOME".ljust(26) +
            "".rjust(17) +
            "".rjust(17) +
            "".rjust(17) +
            f"{grand_total:17,.2f}"
        )
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Summarize income from taxjson_income.py output."
    )
    parser.add_argument(
        "--year", "-y",
        type=int,
        metavar="YYYY",
        help="Tax year to summarize (default: all years)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the report as JSON instead of text"
    )
    parser.add_argument(
        "--sort-by", "-s",
        choices=['net', 'dividend', 'pil', 'tax', 'ticker'],
        default='ticker',
        help="Sort field (default: ticker)"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Input JSON files from taxjson_income.py"
    )
    
    args = parser.parse_args()
    
    file_paths = [Path(f) for f in args.files]
    
    if not file_paths:
        transactions = load_income_data(None)
        report_data = summarize_income(transactions, args.year)
    else:
        all_txs = []
        for fp in file_paths:
            all_txs.extend(load_income_data(fp))
        report_data = summarize_income(all_txs, args.year)

    if args.json:
        print(json.dumps(report_data, indent=2, sort_keys=True))
        return
    print(format_report(report_data, args.sort_by))


if __name__ == "__main__":
    main()

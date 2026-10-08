#!/usr/bin/env python3
"""
taxjson_sum_income.py

Summarize income (dividends, payments in lieu, withholding tax, interest)
from a transaction book: the merged / base JSON (`work/<acct>_base.json`)
that taxjson-merge2 and `taxjson run` write.

Usage:
    python -m taxjson.bin.taxjson_sum_income [--sort-by FIELD] [file1.json file2.json ...]

Sort options:
    --sort-by net|dividend|tax|ticker

If no files provided, reads from stdin.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.country import country_arg, refuse_foreign_flags
from taxjson.lib.income_dating import IncomeRules, parse_ric_entries
from taxjson.lib import cli_diag
from taxjson.lib.json_input import InputFileError, read_json_doc
from taxjson.lib.report_model import load_report_json

PROG = "taxjson-sum-income"
_AMOUNT_KEYS = ('gross_amount', 'net_amount', 'amount')
from taxjson.lib.ticker_map import get_underlying, is_option_ticker


def load_income_data(file_path: Path = None) -> List[Dict[str, Any]]:
    """Load income data from a file or stdin. Raises InputFileError
    (a ValueError) naming the file when it cannot be used."""
    if file_path is not None:
        raw = read_json_doc(file_path)
    else:
        try:
            raw = load_report_json(None)
        except ValueError as e:
            raise InputFileError(f"<stdin>: not valid JSON ({e})") from None

    # taxjson_income outputs a dict with transactions list
    if isinstance(raw, dict) and "transactions" in raw:
        rows = raw["transactions"]
    elif isinstance(raw, list):
        rows = raw
    else:
        # {"Transactions": [...]} summed to no income at exit 0 (#6).
        where = str(file_path) if file_path is not None else "<stdin>"
        keys = (", ".join(sorted(map(str, raw))) or "none"
                if isinstance(raw, dict) else type(raw).__name__)
        raise InputFileError(f'{where}: no "transactions" list (keys: '
                             f"{keys})")
    # The shared row funnel taxjson-gains reads the same book through:
    # an impossible date, a NaN / inf amount or a text amount was summed
    # at rc 0 (or a float() traceback) here while gains refused it in
    # one line (A2-1448).
    from taxjson.lib.core import coerce_transaction_row
    where = str(file_path) if file_path is not None else "<stdin>"
    if not isinstance(rows, list):
        raise InputFileError(f'{where}: "transactions" must be a list')
    for i, t in enumerate(rows):
        if isinstance(t, dict):
            # An absent (null) amount has its own refusal below
            # ('no amount', S051-14); the funnel checks the rest.
            t = {k: v for k, v in t.items()
                 if not (k in _AMOUNT_KEYS and v is None)}
        try:
            coerce_transaction_row(t, i, where)
        except (TypeError, ValueError) as e:
            msg = str(e)
            raise InputFileError(msg if where in msg
                                 else f"{where}: {msg}") from None
    return rows


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


def summarize_income(transactions: List[Dict[str, Any]], target_year: int = None,
                     rules=None) -> Dict[str, Any]:
    """Process income data and create summary. `rules` (a
    lib/income_dating.IncomeRules) dates each income row by its
    country's rule and (Canada) counts a s.260 payment in lieu as a
    dividend; without it every row is dated by its pay date and every
    payment in lieu is kept in its own column."""
    ticker_stats: Dict[str, Dict] = {}
    interest_totals: Dict[str, float] = {}
    # Income rows with no amount at all (every amount key missing or
    # null) — booked as $0 without a word before (audit S051-14). The
    # CLI refuses them; library callers see the count.
    missing_amount: List[Dict[str, Any]] = []

    # The tax withheld on a payment follows its dividend's date (A2-0396).
    wh_dates = (rules.withholding_dates(transactions)
                if rules is not None else {})
    # A Canadian trust's units (the books call their payouts
    # distributions): a payment in lieu on one stays ordinary (A2-1465).
    trusts = (rules.trust_units(transactions)
              if rules is not None else frozenset())
    for tx in transactions:
        # `tx['date']` is sometimes explicitly None (came in as JSON
        # null) — `tx.get('date', '')` returns None in that case and
        # the [:4] slice would have crashed. Use `or ''` to fold both
        # missing-and-None into the empty-string path which the year
        # filter below already tolerates.
        date = str((wh_dates.get(id(tx))
                    or (rules.income_date(tx) if rules is not None
                        else tx.get('date')) or '')[:4])
        
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
        if (action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST')
                and all(tx.get(k) is None for k in _AMOUNT_KEYS)):
            missing_amount.append({k: tx.get(k) for k in
                                   ('date', 'symbol', 'action', 'account')})

        base_ticker = get_base_ticker(symbol)

        # DIVIDEND_IN_LIEU (Payment-in-Lieu): the broker had your shares
        # loaned out, so the issuer's dividend was paid to someone else and
        # you got a substitute payment. US: ordinary (non-qualified)
        # income. Canada: ordinary income, EXCEPT a Canadian dealer's
        # payment on a Canadian corporation's share, which s.260 deems a
        # taxable dividend (moved to the dividend column above). The
        # rest stays per-ticker in its own column.
        if rules is not None and rules.pil_is_dividend(tx, trusts):
            # Canada, ITA s.260: a Canadian dealer's payment in lieu on
            # a Canadian issuer's share is a taxable dividend (T5 box
            # 24), so it is in the dividend column.
            action, type_ = 'DIVIDEND', 'dividend'
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
        'total_year': target_year or 'all',
        'missing_amount': missing_amount,
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


@guard_main("taxjson-sum-income")
def main():
    parser = argparse.ArgumentParser(
        description="Summarize income from a transaction book (the "
                    "merged / base JSON taxjson-merge2 and `taxjson run` "
                    "write)."
    )
    parser.add_argument(
        "--year", "-y",
        type=tax_year,
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
        help="Transaction JSON files (merged / base books; "
             "default: stdin)"
    )
    
    parser.add_argument(
        "--country", type=country_arg, default=None,
        metavar="{canada,ca,usa,us}",
        help="Apply that country's income dating and payment-in-lieu "
             "rules (lib/income_dating); without it every row is dated "
             "by its pay date and every payment in lieu is its own "
             "column")
    parser.add_argument("--corporate-distribution", action="append",
                        default=None, metavar="SYMBOL",
                        help="Canada: see taxjson-gains")
    parser.add_argument("--ric-january-dividend", action="append",
                        default=None, metavar="\"SYMBOL [YYYY-01-DD]\"",
                        help="USA: see taxjson-gains")

    args = parser.parse_args()
    rules = None
    if args.country:
        refuse_foreign_flags(args, "taxjson-sum-income")
        try:
            rules = IncomeRules(
                country=args.country,
                corporate_distributions=tuple(
                    s.upper() for s in args.corporate_distribution or ()),
                ric_january_dividends=parse_ric_entries(
                    args.ric_january_dividend or [],
                    key="--ric-january-dividend"))
        except ValueError as e:
            parser.error(str(e))
    elif args.corporate_distribution or args.ric_january_dividend:
        parser.error("--corporate-distribution / --ric-january-dividend "
                     "need --country")
    
    file_paths = [Path(f) for f in args.files]
    
    all_txs = []
    try:
        for fp in (file_paths or [None]):
            all_txs.extend(load_income_data(fp))
    except InputFileError as e:
        cli_diag.error(PROG, str(e))
        sys.exit(2)
    for i, tx in enumerate(all_txs):
        # Wrong-shape rows were a TypeError traceback (S051-11).
        if not isinstance(tx, dict) or not isinstance(
                tx.get('date') if isinstance(tx, dict) else None,
                (str, type(None))):
            cli_diag.error(PROG, f"row {i}: expected an object with a "
                                 f"string 'date', got {tx!r:.80}")
            sys.exit(2)
    report_data = summarize_income(all_txs, args.year, rules)
    if report_data['missing_amount']:
        bad = report_data['missing_amount']
        first = bad[0]
        cli_diag.error(PROG, f"{len(bad)} income row(s) carry no amount "
                             f"(gross_amount/net_amount/amount all missing "
                             f"or null), e.g. {first.get('action')} "
                             f"{first.get('symbol')} {first.get('date')} — "
                             f"they would be booked as $0; fix the book")
        sys.exit(2)

    if args.json:
        from taxjson.lib.json_input import filing_json_text
        print(filing_json_text(report_data, indent=2, sort_keys=True))
        return
    print(format_report(report_data, args.sort_by))


if __name__ == "__main__":
    main()

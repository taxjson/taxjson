#!/usr/bin/env python3
"""
taxjson_sum_income.py

Summarize income (dividends, payments in lieu, withholding tax, interest)
from a transaction book: the merged / base JSON (`work/<acct>_base.json`)
that taxjson-merge2 and `tjs run` write.

Usage:
    python -m taxjson.bin.taxjson_sum_income [--sort-by FIELD] [file1.json file2.json ...]

Sort options:
    --sort-by net|dividend|tax|ticker

If no files provided, reads from stdin.
"""
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.country import country_arg, refuse_foreign_flags
from taxjson.characterize.income_dating import IncomeRules, parse_ric_entries
from taxjson.lib import cli_diag
from taxjson.lib import out as _out
from taxjson.lib.json_input import InputFileError, read_json_doc
from taxjson.lib.report_model import load_report_json
from taxjson.model import money as M
from taxjson.lib.income_summary import _AMOUNT_KEYS, capitalized_pil_shares, summarize_income


PROG = "taxjson-sum-income"


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
    from dataclasses import asdict
    from taxjson.lib.tx_load import coerce_transaction_row
    where = str(file_path) if file_path is not None else "<stdin>"
    if not isinstance(rows, list):
        raise InputFileError(f'{where}: "transactions" must be a list')
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(rows):
        probe = t
        if isinstance(t, dict):
            # An absent (null) amount has its own refusal below
            # ('no amount', S051-14); the funnel checks the rest.
            probe = {k: v for k, v in t.items()
                     if not (k in _AMOUNT_KEYS and v is None)}
        try:
            obj = coerce_transaction_row(probe, i, where)
            amount = _checked_amount(t, i, where)
        except (TypeError, ValueError) as e:
            msg = str(e)
            raise InputFileError(msg if where in msg
                                 else f"{where}: {msg}") from None
        if not isinstance(t, dict):
            out.append(t)
            continue
        # The row the summary reads is the funnel's NORMALIZED one for
        # every field it checked: a null `type` becomes the default
        # (summarize_income called .lower() on None — audit 2026-10-24
        # B46), a numeric string its number, a date its YYYY-MM-DD. A
        # key the funnel does not know (`amount`, the alias) keeps the
        # row's own value, and so does a null amount: the probe left it
        # out, so the funnel's value is TaxTransaction's 0.0 default,
        # and copying it booked the row as $0 past the 'no amount'
        # refusal (2026-10-25 audit diff-tooling#3).
        norm = asdict(obj)
        row = dict(t)
        for k in t:
            if k in norm and not (k in _AMOUNT_KEYS and t[k] is None):
                row[k] = norm[k]
        if amount is not None:
            row["amount"] = amount
        out.append(row)
    return out


def _checked_amount(t, i: int, where: str):
    """The row's `amount` (the alias summarize_income reads beside
    gross_amount / net_amount) as a finite float, or None when absent
    or null. It is not a TaxTransaction field, so the shared funnel
    never saw it: an amount of "NaN" reached the totals as nan (audit
    2026-10-24 B49). The funnel's own refusals, word for word."""
    import math
    if not isinstance(t, dict) or t.get("amount") is None:
        return None
    v = t["amount"]
    ctx = (f"{where}: transaction at index {i} (id={t.get('id', '?')!r}, "
           f"symbol={t.get('symbol', '?')!r})")
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"{ctx}: amount={v!r} is not a number (got "
                         f"{type(v).__name__}) — fix the input data.")
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            raise ValueError(f"{ctx}: non-numeric amount={t['amount']!r} "
                             f"— fix the input data.") from None
    if not math.isfinite(v):
        raise ValueError(f"{ctx}: non-finite amount={v!r} — fix the "
                         f"input data.")
    return float(v)


def format_report(data: Dict[str, Any], sort_by: str = 'ticker') -> str:
    """Format the summary as a text report."""
    lines = []
    ticker_stats = data['ticker_stats']
    interest_totals = data.get('interest_totals', {})
    interest_paid = data.get('interest_paid_totals', {}) or {}
    total_year = data.get('total_year', 'all')
    
    all_currencies = set()
    for base in ticker_stats:
        for curr in ticker_stats[base]:
            all_currencies.add(curr)
    # Interest-only currencies (no dividend rows) need to render too —
    # otherwise CASH INTEREST silently disappears from the report.
    for curr in interest_totals:
        all_currencies.add(curr)
    for curr in interest_paid:
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
                f"{M.shown(div, 2):17,.2f}" +
                f"{M.shown(pil, 2):17,.2f}" +
                f"{M.shown(tax, 2):17,.2f}" +
                f"{M.shown(net, 2):17,.2f}"
            )

        interest = interest_totals.get(currency, 0)
        grand_total = totals['net'] + interest

        lines.append("-" * 94)
        lines.append(
            "SUBTOTAL".ljust(26) +
            f"{M.shown(totals['div'], 2):17,.2f}" +
            f"{M.shown(totals['pil'], 2):17,.2f}" +
            f"{M.shown(totals['tax'], 2):17,.2f}" +
            f"{M.shown(totals['net'], 2):17,.2f}"
        )
        lines.append(
            "CASH INTEREST".ljust(26) +
            "".rjust(17) +
            "".rjust(17) +
            "".rjust(17) +
            f"{M.shown(interest, 2):17,.2f}"
        )
        paid = interest_paid.get(currency, 0)
        if paid:
            # Interest charged: a deduction (a carrying charge), shown
            # beside the income and kept out of the total.
            lines.append(
                "INTEREST PAID (not in total)".ljust(26) +
                "".rjust(17) +
                "".rjust(17) +
                "".rjust(17) +
                f"{M.shown(paid, 2):17,.2f}"
            )
        cap = (data.get('capitalized_263h') or {}).get(currency, 0)
        if cap:
            # In a cover's cost (§263(h)), not income: kept out of the
            # total, as `tjs dil-sum` does (backlog TJ-1139).
            lines.append(
                "CAPITALIZED, §263(h) (not in total)".ljust(26) +
                f"{M.shown(cap, 2):17,.2f}"
            )
        lines.append("-" * 94)
        lines.append(
            "TOTAL NET INCOME".ljust(26) +
            "".rjust(17) +
            "".rjust(17) +
            "".rjust(17) +
            f"{M.shown(grand_total, 2):17,.2f}"
        )
    
    return "\n".join(lines)


@guard_main("taxjson-sum-income")
def main():
    parser = argparse.ArgumentParser(
        description="Summarize income from a transaction book (the "
                    "merged / base JSON taxjson-merge2 and `tjs run` "
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
        "--gains", metavar="FILE", default=None,
        help="The book's gains file: dividend compensation it put into a "
             "cover's cost (§263(h)) is left out of the PIL column and "
             "shown on its own line")
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
        capitalized = (capitalized_pil_shares(read_json_doc(
            Path(args.gains), require_key="transactions")["transactions"], args.gains)
            if args.gains else None)
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
    report_data = summarize_income(all_txs, args.year, rules, capitalized)
    if report_data['missing_amount']:
        bad = report_data['missing_amount']
        first = bad[0]
        cli_diag.error(PROG, f"{len(bad)} income row(s) carry no amount "
                             f"(gross_amount/net_amount/amount all missing "
                             f"or null), e.g. {first.get('action')} "
                             f"{first.get('symbol')} {first.get('date')} — "
                             f"they would be booked as $0; fix the book")
        sys.exit(2)

    # A live exact Doc: the report written where the print() was.
    d = _out.Doc(exact=True, width_=0, live=True)
    if args.json:
        from taxjson.lib.json_contract import versioned
        from taxjson.lib.json_input import filing_json_text
        d.line(filing_json_text(versioned(report_data, "taxjson-sum-income"), indent=2,
                                sort_keys=True))
        return
    d.line(format_report(report_data, args.sort_by))


if __name__ == "__main__":
    main()

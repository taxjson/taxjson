#!/usr/bin/env python3
"""
taxjson_sum_gains.py

Summarize gains from taxjson_gains.py output, formatted identically to tt_sum_gains.pl.
"""
import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.country import country_arg
from taxjson.lib.report_model import load_report_json
from taxjson.lib import cli_diag
from taxjson.lib.json_input import load_json_doc_or_exit
from taxjson.model.result import Result, ResultShapeError
from taxjson.model import money as M
from taxjson.lib.gain_summary import summarize_gains
from taxjson.lib import out as _out


PROG = "taxjson-sum-gains"


def _places_of(spec: str) -> int:
    """A fixed-point spec's places (`17,.2f`: 2), so a cut matches the
    format it feeds (TJ-3362)."""
    places = spec.rpartition(".")[2]
    if not (places.endswith("f") and places[:-1].isdigit()):
        raise ValueError(f"not a fixed-point format spec: {spec!r}")
    return int(places[:-1])

def load_gains_data(file_path: Path = None) -> Dict[str, Any]:
    # '#' comment lines (e.g. from --full-traces) are stripped by the
    # shared report-layer loader.
    return load_report_json(file_path)


def get_sort_value(ticker: str, currency: str, key: str, ticker_stats: Dict[str, Any]) -> float:
    stats = ticker_stats[ticker].get(currency, {})
    if key == 'total':
        return stats.get('cap', 0) + stats.get('opt', 0) + stats.get('div', 0) + stats.get('pil', 0)
    elif key == 'total_gain':
        return stats.get('cap', 0) + stats.get('opt', 0)
    elif key == 'capital_gain':
        return stats.get('cap', 0)
    elif key == 'option_gain':
        return stats.get('opt', 0)
    elif key == 'dividend':
        return stats.get('div', 0)
    elif key == 'pil':
        return stats.get('pil', 0)
    elif key == 'holding_days':
        count = stats.get('trade_count', 0)
        days = stats.get('hold_days', [])
        return sum(days) / count if count > 0 else 0
    return 0

def format_report(data: Dict[str, Any], sort_by: str = 'ticker', no_color: bool = False,
                  staking: bool = False, country: str = None) -> str:
    """`country` (canada | usa | None) words the form and cost
    pointers: Schedule 3 / ACB, Form 8949 / basis, or neutral words
    for a standalone run without --country (re-audit A2-0432)."""
    from taxjson.lib.country import COST_TERM, GAINS_FORM, LOSS_RULE
    form, cost_word = GAINS_FORM[country], COST_TERM[country]
    lines = []
    ticker_stats = data['ticker_stats']
    total_year = data['total_year']
    
    # ANSI color: on iff stdout is a tty AND neither --no-color nor the
    # NO_COLOR env var (https://no-color.org) is set — same gate as
    # taxjson_diff.
    use_color = (sys.stdout.isatty() and not no_color
                 and not os.environ.get("NO_COLOR"))

    RESET = "\033[0m" if use_color else ""
    BOLD = "\033[1m" if use_color else ""
    GREEN = "\033[32m" if use_color else ""
    RED = "\033[31m" if use_color else ""
    CYAN = "\033[36m" if use_color else ""

    all_currencies = set()
    for base in ticker_stats:
        for curr in ticker_stats[base]:
            all_currencies.add(curr)
    
    def color_val(val: Any, fmt: str = "17,.2f", is_cost: bool = False) -> str:
        if val is None: val = 0.0
        s = f"{M.shown(float(val), _places_of(fmt)):{fmt}}"     # TJ-3346, TJ-3362
        if abs(val) < 1e-6: return s
        if is_cost: return s # Costs usually don't need colors
        if val > 0: return f"{GREEN}{s}{RESET}"
        if val < 0: return f"{RED}{s}{RESET}"
        return s

    # Surface a non-converged wash-sale solver prominently: the engine
    # warns on stderr at compute time, but a user reading only this report
    # would otherwise have no signal that the gains/disallowed figures may
    # be inconsistent. Only fires when the flag is explicitly False.
    if data.get('wash_solver_converged', True) is False:
        iters = data.get('wash_solver_iterations')
        iter_note = f" after {iters} iterations" if iters else ""
        lines.append(f"{BOLD}{RED}{'!' * 112}{RESET}")
        lines.append(
            f"{BOLD}{RED}WARNING: the {LOSS_RULE[country]} solver did NOT "
            f"converge{iter_note}. "
            f"Gains and disallowed-loss totals below may be incomplete or "
            f"inconsistent — verify before filing.{RESET}"
        )
        lines.append(f"{BOLD}{RED}{'!' * 112}{RESET}")

    for currency in sorted(all_currencies):
        lines.append("")
        lines.append(f"{BOLD}TICKER SUMMARY — {currency}, year {total_year}, sorted by {sort_by}{RESET}")
        lines.append("")
        lines.append(
            f"{BOLD}{'SYMBOL':<26} {'TOTAL':>17} {'TOTAL_COST':>17} {'TOTAL_PROCEED':>17} {'TOTAL_GAIN':>17} {'CAP_GAIN':>17} {'OPT_GAIN':>17} {'DIVIDEND':>17} {'PIL':>17} {'AVG_DAYS':>10}{RESET}"
        )
        lines.append(f"{CYAN}{'-' * 199}{RESET}")
        
        tickers_in_curr = [t for t in ticker_stats if currency in ticker_stats[t]]
        
        if sort_by == 'ticker':
            sorted_tickers = sorted(tickers_in_curr)
        else:
            sorted_tickers = sorted(
                tickers_in_curr,
                key=lambda t: (-get_sort_value(t, currency, sort_by, ticker_stats), t)
            )
        
        totals = {
            'all': 0.0, 'cost': 0.0, 'proceeds': 0.0, 'total': 0.0,
            'cap': 0.0, 'opt': 0.0, 'div': 0.0, 'pil': 0.0, 'trades': 0, 'hold_days': 0
        }

        _ticker_rows(sorted_tickers, ticker_stats, currency, totals, lines, color_val)

        grand_avg = totals['hold_days'] / totals['trades'] if totals['trades'] > 0 else 0
        lines.append(f"{CYAN}{'-' * 199}{RESET}")
        grand_term_str = ""
        if abs(totals.get('st', 0)) > 1e-3 or abs(totals.get('lt', 0)) > 1e-3:
            grand_term_str = f" [ST: {M.shown(totals['st'], 2):8.2f} LT: {M.shown(totals['lt'], 2):8.2f}]"

        lines.append(
            f"{BOLD}{'TOTAL':<26} {color_val(totals['all'])} {M.shown(totals['cost'], 2):17,.2f} {M.shown(totals['proceeds'], 2):17,.2f} {color_val(totals['total'])} {color_val(totals['cap'])} {color_val(totals['opt'])} {color_val(totals['div'])} {color_val(totals['pil'])} {grand_avg:10.1f}{grand_term_str}{RESET}"
        )
        
        # Add Statistical sections
        for asset_type in ['Stocks', 'Options', 'Futures']:
            if asset_type in data['returns_by_asset'] and currency in data['returns_by_asset'][asset_type]:
                lines.append(output_statistics(currency, asset_type, data['returns_by_asset'][asset_type][currency], total_year, use_color=use_color))

        _consolidated_totals(lines, BOLD, currency, total_year, RESET, totals, form, cost_word,
                             color_val, staking, data)

    if data.get('manual_rows'):
        from taxjson.bin.taxjson_form_export import manual_section
        lines.append("")
        lines += manual_section(data['manual_rows'],
                                ", ".join(sorted(all_currencies)) or "",
                                country=country)
    return "\n".join(lines)


def _ticker_rows(sorted_tickers: List[str], ticker_stats: Dict[str, Any], currency: str,
                 totals: Dict[str, Any], lines: List[str], color_val: Any) -> None:
    """sum-gains: one currency's ticker rows, each added to `lines` and to
    the currency's `totals` (a ticker whose figures are all zero is
    skipped)."""
    for ticker in sorted_tickers:
        stats = ticker_stats[ticker][currency]
        cap = float(stats.get('cap', 0) or 0)
        opt = float(stats.get('opt', 0) or 0)
        div = float(stats.get('div', 0) or 0)
        pil = float(stats.get('pil', 0) or 0)
        cost = float(stats.get('cost', 0) or 0)
        proceeds = float(stats.get('proceeds', 0) or 0)

        # Skip empty entries where everything is effectively zero
        if abs(cap) < 1e-4 and abs(opt) < 1e-4 and abs(div) < 1e-4 and abs(pil) < 1e-4 and abs(cost) < 1e-4 and abs(proceeds) < 1e-4:
            continue

        # Align with tt_sum_gains.pl: Adjust cost for wash sales (where proceeds - cost != gain)
        raw_trade_gain = proceeds - cost
        taxable_trade_gain = cap + opt
        cost_adjustment = taxable_trade_gain - raw_trade_gain
        adjusted_cost = cost - cost_adjustment

        trade_total = cap + opt
        all_total = trade_total + div + pil

        avg_hold = 0.0
        hold_days_list = stats.get('hold_days', [])
        if stats.get('trade_count', 0) > 0 and hold_days_list:
            avg_hold = float(sum(hold_days_list)) / len(hold_days_list)

        totals['all'] += all_total
        totals['cost'] += adjusted_cost
        totals['proceeds'] += proceeds
        totals['total'] += trade_total
        totals['cap'] += cap
        totals['opt'] += opt
        totals['div'] += div
        totals['pil'] += pil
        totals['trades'] += stats.get('trade_count', 0)
        totals['hold_days'] += sum(hold_days_list)

        # Build the line. If US terms exist, add them to the ticker line
        st_gain = float(stats.get('st_gain', 0) or 0)
        lt_gain = float(stats.get('lt_gain', 0) or 0)
        totals['st'] = totals.get('st', 0.0) + st_gain
        totals['lt'] = totals.get('lt', 0.0) + lt_gain

        term_str = f" [ST: {M.shown(st_gain, 2):8.2f} LT: {M.shown(lt_gain, 2):8.2f}]" if (abs(st_gain) > 1e-3 or abs(lt_gain) > 1e-3) else ""

        lines.append(
            f"{ticker:<26} {color_val(all_total)} {M.shown(adjusted_cost, 2):17,.2f} {M.shown(proceeds, 2):17,.2f} {color_val(trade_total)} {color_val(cap)} {color_val(opt)} {color_val(div)} {color_val(pil)} {avg_hold:10.1f}{term_str}"
        )


def _consolidated_totals(lines: List[str], BOLD: str, currency: str, total_year: Any, RESET: str,
                         totals: Dict[str, Any], form: str, cost_word: str, color_val: Any,
                         staking: bool, data: Dict[str, Any]) -> None:
    """sum-gains: one currency's CONSOLIDATED TOTALS block, added to
    `lines`."""
    # Print the consolidated summary block at the bottom
    lines.append("")
    lines.append(f"{BOLD}CONSOLIDATED TOTALS — {currency}, year {total_year}{RESET}")
    lines.append("")
    lines.append(f"TOTAL COST:                 {M.shown(totals['cost'], 2):17,.2f} {currency}")
    lines.append(f"TOTAL PROCEEDS:             {M.shown(totals['proceeds'], 2):17,.2f} {currency}")
    # These two are the ENGINE's signed figures (a short's cover
    # cost is negative proceeds, its opening proceeds negative
    # cost; sell-side outlays netted) — not Schedule 3 lines. The
    # gains below are the same under both conventions (2026-09
    # audit R1-208).
    lines.append("  (engine sign convention: shorts/written options "
                 f"negated — for {form} proceeds and {cost_word} use "
                 "`tjs form-export`)")
    lines.append("-" * 54)
    # 'cap' is every NON-option disposition: shares and units, but
    # also plain futures and crypto (Schedule 3 lines 6/7) — the
    # old "STOCK" label read as line 4 and disagreed with it by the
    # futures/crypto gains (audit R1-211, S051-01, S031-04).
    lines.append(f"TOTAL REALIZED NON-OPTION GAIN:{color_val(totals['cap'], '14,.2f')} {currency}")
    lines.append(f"  (shares, units, futures and crypto — {form} "
                 "lines: `tjs form-export`)")
    lines.append(f"TOTAL REALIZED OPTION GAIN: {color_val(totals['opt'], is_cost=False)} {currency}")
    lines.append(f"TOTAL REALIZED GAIN:        {color_val(totals['total'], is_cost=False)} {currency}")
    lines.append("-" * 54)
    if staking:
        # `--staking` (a crypto account's books, passed by `tjs
        # run`): its DIVIDEND rows are staking rewards — ordinary
        # income, not dividends (S023-11).
        lines.append(f"TOTAL STAKING REWARDS:      {color_val(totals['div'], is_cost=False)} {currency}  (ordinary income, not dividends)")
    else:
        # DIVIDEND rows: for a crypto account they are staking rewards
        # (other income, not dividends) — the label says so (S031-04).
        lines.append(f"TOTAL DIVIDENDS / STAKING:  {color_val(totals['div'], is_cost=False)} {currency}")
    if abs(totals.get('pil', 0)) > 1e-3:
        lines.append(f"TOTAL PIL (PAY-IN-LIEU):    {color_val(totals['pil'], is_cost=False)} {currency}")
    lines.append("-" * 54)
    # Gain + dividends + PIL: an investment return, not a realized
    # gain (it overstated the capital gain by all income, S051-00).
    # `tjs sum`'s TOTAL leaves PIL out; this line keeps it.
    lines.append(f"GRAND TOTAL (GAIN+DIV+PIL): {color_val(totals['all'], is_cost=False)} {currency}")
        
    total_fee = data['total_fees'].get(currency, 0.0)
    opt_fee = data['option_fees'].get(currency, 0.0)
    lines.append("-" * 54)
    lines.append(f"TOTAL TRADING FEES PAID:    {M.shown(total_fee, 2):17,.2f} {currency}")
    lines.append(f"  (OPTION TRADING FEES):    {M.shown(opt_fee, 2):17,.2f} {currency}")

def output_statistics(currency: str, asset_type: str, stats: Dict[str, Any], year: str, use_color: bool = True) -> str:
    returns = stats.get('dollars', [])
    days = stats.get('days', [])
    fees = stats.get('fees', [])
    fee_shares = stats.get('fee_shares', [])
    
    # ANSI Color codes
    RESET = "\033[0m" if use_color else ""
    BOLD = "\033[1m" if use_color else ""
    CYAN = "\033[36m" if use_color else ""
    GREEN = "\033[32m" if use_color else ""
    RED = "\033[31m" if use_color else ""

    count = len(returns)
    fee_count = len(fees)
    
    total_gain = sum(returns)
    total_fees = sum(fees)
    total_fee_shares = sum(fee_shares)
    avg_hold = sum(days) / count if count > 0 else 0
    
    pos_ret = [r for r in returns if r > 0]
    neg_ret = [r for r in returns if r < 0]
    
    avg_pos = sum(pos_ret) / len(pos_ret) if pos_ret else 0
    avg_neg = sum(neg_ret) / len(neg_ret) if neg_ret else 0
    # wins-per-loss (W/L), labeled accordingly — this was labeled 'Win
    # Ratio', which reads as wins/total. Win Rate (%) is shown alongside.
    win_loss = len(pos_ret) / len(neg_ret) if neg_ret else float('inf')
    total_traded = len(pos_ret) + len(neg_ret)
    win_rate = 100.0 * len(pos_ret) / total_traded if total_traded else 0.0
    
    color = GREEN if total_gain > 0 else (RED if total_gain < 0 else "")

    lines = []
    lines.append("")
    shown_type = 'Non-option' if asset_type == 'Stocks' else asset_type
    lines.append(f"{BOLD}ASSET: {shown_type:<10} | CURRENCY: {currency:<5} | TRADES: {count} | YEAR: {year}{RESET}")
    lines.append("")
    lines.append(f"Total Realized Gain:     {color}{M.shown(total_gain, 2):17,.2f}{RESET} {currency}")
    lines.append(f"Average Holding Period:   {avg_hold:17,.1f} Days")

    avg_fee_per_trade = total_fees / fee_count if fee_count > 0 else 0
    if asset_type == 'Stocks':
        avg_fee_per_share = total_fees / total_fee_shares if total_fee_shares > 0 else 0
        lines.append(f"Avg Fee Per Trade:       {M.shown(avg_fee_per_trade, 2):17,.2f} {currency}")
        lines.append(f"Avg Fee Per Share:       {M.shown(avg_fee_per_share, 4):17,.4f} {currency}")
    elif asset_type == 'Futures':
        # `fee_shares` for futures is the contract count.
        avg_fee_per_contract = total_fees / total_fee_shares if total_fee_shares > 0 else 0
        lines.append(f"Avg Fee Per Trade:       {M.shown(avg_fee_per_trade, 2):17,.2f} {currency}")
        lines.append(f"Avg Fee Per Contract:    {M.shown(avg_fee_per_contract, 4):17,.4f} {currency}")
    else:
        # `fee_shares` for options is contract count, not underlying shares.
        # One contract already represents 100 underlying shares, so
        # total_fees / contracts is the per-100-shares figure directly —
        # no extra ×100 (that bug inflated the line by exactly 100×).
        avg_fee_per_100 = total_fees / total_fee_shares if total_fee_shares > 0 else 0
        lines.append(f"Avg Fee Per Trade:       {M.shown(avg_fee_per_trade, 2):17,.2f} {currency}")
        lines.append(f"Avg Fee Per 100 Opt:     {M.shown(avg_fee_per_100, 4):17,.4f} {currency}")

    lines.append(f"{CYAN}{'-' * 112}{RESET}")
    wl = "inf" if win_loss == float('inf') else f"{win_loss:.2f}"
    lines.append(f"Positive Trades: {len(pos_ret)} | Negative Trades: {len(neg_ret)} | Win/Loss: {wl} | Win Rate: {win_rate:.1f}%")
    lines.append(f"Average Positive: {GREEN}{M.shown(avg_pos, 2):17,.2f}{RESET} {currency} | Average Negative: {RED}{M.shown(avg_neg, 2):17,.2f}{RESET} {currency}")
    lines.append(f"{CYAN}{'-' * 112}{RESET}")
    return "\n".join(lines)

def _require_gains(doc, where) -> None:
    """A non-gains document (no 'transactions', a stage file) printed
    GRAND TOTAL 0.00 at exit 0 (S033-01)."""
    from taxjson.lib.json_input import (InputFileError, require_gains_doc,
                                        require_row_lists)
    try:
        require_row_lists(require_gains_doc(doc, where), where)
    except InputFileError as e:
        cli_diag.error(PROG, str(e))
        sys.exit(2)
    # The parts summarize_gains reads, of the shape it reads (a row that
    # is no object is past the checks above): one line naming the file,
    # not a traceback (TJ-2859, TJ-2872).
    try:
        result = Result.from_json(doc)
        for part in ("transactions", "manual_reporting_required", "inventory", "summary"):
            getattr(result, part)
    except ResultShapeError as e:
        cli_diag.error(PROG, f"{where}: {e} — the file is damaged or hand-edited: fix it "
                             f"or re-run `tjs run`")
        sys.exit(2)


# The money summarize_gains adds up per ticker (and per currency).
_TICKER_MONEY = ("cap", "opt", "div", "pil", "pil_paid", "cost", "proceeds",
                 "st_gain", "lt_gain")


def json_figures(report: Dict[str, Any]) -> Dict[str, Any]:
    """The summary as --json prints it (TJ-4930): what it adds up from
    the gains files' figures at their four places (M.GAINS_PLACES: the
    exact sum of their figures, where the float sum printed its binary
    error), and a trade's share of a grant write's gain, a quotient, at
    M.DERIVED_PLACES. The text keeps the floats."""
    def cut(v: Any, places: int) -> Any:
        return M.json_money(v, places) if isinstance(v, float) else v
    g = M.GAINS_PLACES
    out = dict(report)
    out["ticker_stats"] = {
        t: {c: {k: (cut(v, g) if k in _TICKER_MONEY else v)
                for k, v in st.items()}
            for c, st in by_cur.items()}
        for t, by_cur in (report.get("ticker_stats") or {}).items()}
    out["returns_by_asset"] = {
        a: {c: dict(b, dollars=[cut(v, M.DERIVED_PLACES)
                                for v in b.get("dollars") or []])
            for c, b in by_cur.items()}
        for a, by_cur in (report.get("returns_by_asset") or {}).items()}
    for k in ("total_fees", "option_fees"):
        if isinstance(report.get(k), dict):
            out[k] = {c: cut(v, g) for c, v in report[k].items()}
    return out


@guard_main("taxjson-sum-gains")
def main():
    parser = argparse.ArgumentParser(
        description="Summarize taxjson-gains output per ticker: capital "
                    "and option gains, dividends, payments in lieu.")
    parser.add_argument("--sort-by", "-s", choices=['total', 'total_gain', 'capital_gain', 'option_gain', 'dividend', 'pil', 'holding_days', 'ticker'], default='ticker')
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    parser.add_argument("--staking", action="store_true",
                        help="The books are a crypto account's: label its "
                             "DIVIDEND total as staking rewards")
    parser.add_argument("--country", type=country_arg, default=None,
                        metavar="{canada,ca,usa,us}",
                        help="Words the form and cost pointers (Schedule "
                             "3 / ACB or Form 8949 / basis; neutral "
                             "without it). `tjs run` passes it.")
    parser.add_argument("files", nargs="*", metavar="FILE")
    args = parser.parse_args()
    
    file_paths = [Path(f) for f in args.files]
    if not file_paths:
        try:
            raw = load_report_json(None)
        except (UnicodeDecodeError, ValueError) as e:
            cli_diag.error(PROG, f"<stdin>: not valid JSON ({e})")
            sys.exit(2)
        if isinstance(raw, list):
            raw = {'transactions': raw}
        if not isinstance(raw, dict):
            cli_diag.error(PROG, "<stdin>: expected a JSON object")
            sys.exit(2)
        _require_gains(raw, "<stdin>")
        report_data = summarize_gains(raw)
    else:
        all_by_ticker = {}
        all_txs = []
        all_manual = []
        # The held positions too: summarize_gains resolves an option's
        # class-share underlying from them (SYN...TO calls on SYN.B.TO
        # shares held, no share row in the year); dropped here, a file
        # named on the command line reported a phantom SYN.TO that stdin
        # did not (backlog TJ-1011).
        all_inventory = []
        total_year = 'all'
        # Merge total_fees_by_currency across files (summing per currency
        # and asset type). taxjson-gains writes this block from the raw
        # transaction list so it captures both buy and sell-side fees,
        # unlike the per-gain aggregation in summarize_gains which only
        # has sell-side. Forgetting to carry it through drops half the fees.
        merged_fees: Dict[str, Dict[str, float]] = {}
        # Convergence is AND-ed across files: if ANY input's solver failed
        # to converge, the merged report must warn.
        wash_converged = True
        wash_iters = None
        # The year each file says it is (summary.year): a summary is one
        # year's figures, so files of different years are refused — the
        # loop took the LAST file's year and labelled the sum of every
        # file's gains as that year's (audit 2026-10-24 D04).
        years: Dict[str, Any] = {}
        for fp in file_paths:
            data = load_json_doc_or_exit(PROG, fp)
            _require_gains(data, fp)
            if 'by_ticker' in data:
                all_by_ticker.update(data['by_ticker'])
            if 'transactions' in data:
                all_txs.extend(data['transactions'])
            all_manual.extend(data.get('manual_reporting_required') or [])
            all_inventory.extend(data.get('inventory') or [])
            summary = data.get('summary') or {}
            if summary.get('year'):
                total_year = summary['year']
                years[str(fp)] = summary['year']
                if len({str(y) for y in years.values()}) > 1:
                    cli_diag.error(
                        PROG,
                        "the gains files are of different tax years: "
                        + ", ".join(f"{f} ({y})" for f, y in years.items())
                        + " — a summary is one year's figures: run it on "
                        "each year's files on their own")
                    sys.exit(2)
            wash_converged = wash_converged and summary.get('wash_solver_converged', True)
            # Carried like stdin carries it (the file path dropped it,
            # audit S051-02): the most iterations any input needed.
            if summary.get('wash_solver_iterations') is not None:
                wash_iters = max(wash_iters or 0,
                                 summary['wash_solver_iterations'])
            for curr, bucket in (summary.get('total_fees_by_currency') or {}).items():
                m = merged_fees.setdefault(curr, {'stocks': 0.0, 'options': 0.0, 'total': 0.0})
                for k in ('stocks', 'options', 'total') + (
                        ('futures',) if 'futures' in bucket else ()):
                    m[k] = m.get(k, 0.0) + float(bucket.get(k, 0.0) or 0.0)
        merged_data = {
            'by_ticker': all_by_ticker,
            'transactions': all_txs,
            'manual_reporting_required': all_manual,
            'inventory': all_inventory,
            'summary': {'year': total_year, 'total_fees_by_currency': merged_fees,
                        'wash_solver_converged': wash_converged,
                        'wash_solver_iterations': wash_iters}
        }
        report_data = summarize_gains(merged_data)

    # A live exact Doc: each line printed when it is added, as the
    # print() it replaces printed it.
    d = _out.Doc(exact=True, width_=0, live=True)
    if args.json:
        from taxjson.lib.json_contract import versioned
        from taxjson.lib.json_input import filing_json_text
        d.line(filing_json_text(versioned(json_figures(report_data),
                                          "taxjson-sum-gains"), indent=2,
                                sort_keys=True))
        return
    d.line(format_report(report_data, args.sort_by, args.no_color,
                         staking=args.staking, country=args.country))

if __name__ == "__main__":
    main()

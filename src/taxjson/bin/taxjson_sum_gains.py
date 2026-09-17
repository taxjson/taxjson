#!/usr/bin/env python3
"""
taxjson_sum_gains.py

Summarize gains from taxjson_gains.py output, formatted identically to tt_sum_gains.pl.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any

from taxjson.lib.report_model import load_report_json
from taxjson.lib.ticker_map import get_underlying as get_base_ticker, is_option_ticker

def load_gains_data(file_path: Path = None) -> Dict[str, Any]:
    # '#' comment lines (e.g. from --full-traces) are stripped by the
    # shared report-layer loader.
    return load_report_json(file_path)


def summarize_gains(data: Dict[str, Any]) -> Dict[str, Any]:
    ticker_stats: Dict[str, Dict] = {}
    returns_by_asset: Dict[str, Dict] = {} # asset_type -> currency -> {dollars: [], days: [], fees: [], fee_shares: []}
    total_fees: Dict[str, float] = {}
    option_fees: Dict[str, float] = {}
    
    # We rebuild the summary from the transaction list to ensure year-filtering and currency are correct.
    transactions = data.get('transactions', [])
    target_year = data.get('summary', {}).get('year') or 'all'

    for tx in transactions:
        symbol = tx.get('symbol')
        if not symbol: continue
        
        is_opt = is_option_ticker(symbol)
        base_ticker = get_base_ticker(symbol) if is_opt else symbol
        if base_ticker is None: base_ticker = symbol
        
        # If the gain entry is missing currency, bucket it under '?' rather
        # than silently mis-attributing to CAD. The engine populates this
        # field for every Canada/USA gain, so missing means upstream parser
        # didn't set it on the source transaction.
        currency = tx.get('currency') or '?'
        
        if base_ticker not in ticker_stats:
            ticker_stats[base_ticker] = {}
        if currency not in ticker_stats[base_ticker]:
            ticker_stats[base_ticker][currency] = {
                'cap': 0.0, 'opt': 0.0, 'div': 0.0, 'pil': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                'hold_days': [], 'trade_count': 0
            }
        
        tick_stats = ticker_stats[base_ticker][currency]
        
        if tx.get('action') == 'DIVIDEND':
            div = float(tx.get('dividend', 0) or 0)
            tick_stats['div'] += div
        elif tx.get('action') == 'DIVIDEND_IN_LIEU':
            # PIL: ticker-associated but tax-bucketed as ordinary income, not
            # eligible dividend. Separate column so it contributes to total
            # profit without inflating the T5/Schedule B dividend total.
            pil = float(tx.get('pil', 0) or 0)
            tick_stats['pil'] = tick_stats.get('pil', 0.0) + pil
        else:
            cost = float(tx.get('cost', 0) or 0)
            proceeds = float(tx.get('proceeds', 0) or 0)
            gain = float(tx.get('gain', 0) or 0)
            days = int(tx.get('days_held', 0) or 0)

            tick_stats['cost'] += cost
            tick_stats['proceeds'] += proceeds
            if is_opt:
                tick_stats['opt'] += gain
            else:
                tick_stats['cap'] += gain

            tick_stats['hold_days'].append(days)
            tick_stats['trade_count'] += 1

            # Aggregate for statistics
            asset_type = 'Options' if is_opt else 'Stocks'
            if asset_type not in returns_by_asset: returns_by_asset[asset_type] = {}
            if currency not in returns_by_asset[asset_type]:
                returns_by_asset[asset_type][currency] = {'dollars': [], 'days': [], 'fees': [], 'fee_shares': []}

            returns_by_asset[asset_type][currency]['dollars'].append(gain)
            returns_by_asset[asset_type][currency]['days'].append(days)

            term = tx.get('term')
            if term == 'SHORT_TERM':
                tick_stats['st_gain'] = tick_stats.get('st_gain', 0.0) + gain
            elif term == 'LONG_TERM':
                tick_stats['lt_gain'] = tick_stats.get('lt_gain', 0.0) + gain

        # Track fees if present in the gain entry
        fee = float(tx.get('fee', 0) or 0) + float(tx.get('commission', 0) or 0)
        # != 0, not > 0: fee rebates/corrections arrive negative and
        # must net out of TOTAL TRADING FEES.
        if fee != 0:
            total_fees[currency] = total_fees.get(currency, 0.0) + fee
            is_opt = is_option_ticker(symbol)
            asset_type = 'Options' if is_opt else 'Stocks'
            
            if asset_type not in returns_by_asset: returns_by_asset[asset_type] = {}
            if currency not in returns_by_asset[asset_type]: 
                returns_by_asset[asset_type][currency] = {'dollars': [], 'days': [], 'fees': [], 'fee_shares': []}
            
            returns_by_asset[asset_type][currency]['fees'].append(fee)
            returns_by_asset[asset_type][currency]['fee_shares'].append(abs(float(tx.get('qty', 0))))
            
            if is_opt:
                option_fees[currency] = option_fees.get(currency, 0.0) + fee

    # If the engine already aggregated total fees across the RAW transaction
    # list (both buy and sell sides), prefer that — gain entries only carry
    # sell-side fees because buy fees are baked into cost basis. The summary
    # total is the right number for trading-efficiency analysis.
    summary_fees = (data.get('summary') or {}).get('total_fees_by_currency') or {}
    if summary_fees:
        total_fees = {curr: bucket.get('total', 0.0) for curr, bucket in summary_fees.items()}
        option_fees = {curr: bucket.get('options', 0.0) for curr, bucket in summary_fees.items()}

    return {
        'ticker_stats': ticker_stats,
        'returns_by_asset': returns_by_asset,
        # Dispositions carrying a phantom OPENING_BALANCE basis.
        # PIPELINE-written gains files carry them in a separate
        # manual_reporting_required section (run_gains strips the rows
        # and pops the flag), so the aggregates above EXCLUDE them;
        # only raw engine output still carries in-line tainted rows.
        # Count both homes so consumers can warn either way
        # (round-five audit: the in-line count was always 0 for
        # pipeline files, silencing `taxjson sum`'s warning).
        'tainted_count': sum(1 for t in transactions
                             if t.get('tainted') and 'gain' in t),
        # Pipeline files carry tainted rows OUT-of-line: excluded from
        # every aggregate above, so they get their own count (and the
        # consumer a different warning).
        'tainted_routed': len(
            data.get('manual_reporting_required') or []),
        'total_fees': total_fees,
        'option_fees': option_fees,
        'total_year': target_year,
        # Carry the wash-sale solver's convergence flag through so the
        # report can warn when the numbers may be unreliable. Default True
        # so engines/old files that don't emit it never spuriously warn.
        'wash_solver_converged': (data.get('summary') or {}).get('wash_solver_converged', True),
        'wash_solver_iterations': (data.get('summary') or {}).get('wash_solver_iterations'),
    }

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

def format_report(data: Dict[str, Any], sort_by: str = 'ticker', no_color: bool = False) -> str:
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
        s = f"{float(val):{fmt}}"
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
            f"{BOLD}{RED}WARNING: the CRA wash-sale solver did NOT converge{iter_note}. "
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

            term_str = f" [ST: {st_gain:8.2f} LT: {lt_gain:8.2f}]" if (abs(st_gain) > 1e-3 or abs(lt_gain) > 1e-3) else ""

            lines.append(
                f"{ticker:<26} {color_val(all_total)} {adjusted_cost:17,.2f} {proceeds:17,.2f} {color_val(trade_total)} {color_val(cap)} {color_val(opt)} {color_val(div)} {color_val(pil)} {avg_hold:10.1f}{term_str}"
            )

        grand_avg = totals['hold_days'] / totals['trades'] if totals['trades'] > 0 else 0
        lines.append(f"{CYAN}{'-' * 199}{RESET}")
        grand_term_str = ""
        if abs(totals.get('st', 0)) > 1e-3 or abs(totals.get('lt', 0)) > 1e-3:
            grand_term_str = f" [ST: {totals['st']:8.2f} LT: {totals['lt']:8.2f}]"

        lines.append(
            f"{BOLD}{'TOTAL':<26} {color_val(totals['all'])} {totals['cost']:17,.2f} {totals['proceeds']:17,.2f} {color_val(totals['total'])} {color_val(totals['cap'])} {color_val(totals['opt'])} {color_val(totals['div'])} {color_val(totals['pil'])} {grand_avg:10.1f}{grand_term_str}{RESET}"
        )
        
        # Add Statistical sections
        for asset_type in ['Stocks', 'Options']:
            if asset_type in data['returns_by_asset'] and currency in data['returns_by_asset'][asset_type]:
                lines.append(output_statistics(currency, asset_type, data['returns_by_asset'][asset_type][currency], total_year, use_color=use_color))

        # Print the consolidated summary block at the bottom
        lines.append("")
        lines.append(f"{BOLD}CONSOLIDATED TOTALS — {currency}, year {total_year}{RESET}")
        lines.append("")
        lines.append(f"TOTAL COST:                 {totals['cost']:17,.2f} {currency}")
        lines.append(f"TOTAL PROCEEDS:             {totals['proceeds']:17,.2f} {currency}")
        lines.append("-" * 54)
        lines.append(f"TOTAL REALIZED STOCK GAIN:  {color_val(totals['cap'], is_cost=False)} {currency}")
        lines.append(f"TOTAL REALIZED OPTION GAIN: {color_val(totals['opt'], is_cost=False)} {currency}")
        lines.append(f"TOTAL REALIZED GAIN:        {color_val(totals['total'], is_cost=False)} {currency}")
        lines.append("-" * 54)
        lines.append(f"TOTAL REALIZED DIVIDENDS:   {color_val(totals['div'], is_cost=False)} {currency}")
        if abs(totals.get('pil', 0)) > 1e-3:
            lines.append(f"TOTAL PIL (PAY-IN-LIEU):    {color_val(totals['pil'], is_cost=False)} {currency}")
        lines.append("-" * 54)
        lines.append(f"GRAND TOTAL REALIZED GAIN:  {color_val(totals['all'], is_cost=False)} {currency}")
        
        total_fee = data['total_fees'].get(currency, 0.0)
        opt_fee = data['option_fees'].get(currency, 0.0)
        lines.append("-" * 54)
        lines.append(f"TOTAL TRADING FEES PAID:    {total_fee:17,.2f} {currency}")
        lines.append(f"  (OPTION TRADING FEES):    {opt_fee:17,.2f} {currency}")

    return "\n".join(lines)

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
    lines.append(f"{BOLD}ASSET: {asset_type:<8} | CURRENCY: {currency:<5} | TRADES: {count} | YEAR: {year}{RESET}")
    lines.append("")
    lines.append(f"Total Realized Gain:     {color}{total_gain:17,.2f}{RESET} {currency}")
    lines.append(f"Average Holding Period:   {avg_hold:17,.1f} Days")

    avg_fee_per_trade = total_fees / fee_count if fee_count > 0 else 0
    if asset_type == 'Stocks':
        avg_fee_per_share = total_fees / total_fee_shares if total_fee_shares > 0 else 0
        lines.append(f"Avg Fee Per Trade:       {avg_fee_per_trade:17,.2f} {currency}")
        lines.append(f"Avg Fee Per Share:       {avg_fee_per_share:17,.4f} {currency}")
    else:
        # `fee_shares` for options is contract count, not underlying shares.
        # One contract already represents 100 underlying shares, so
        # total_fees / contracts is the per-100-shares figure directly —
        # no extra ×100 (that bug inflated the line by exactly 100×).
        avg_fee_per_100 = total_fees / total_fee_shares if total_fee_shares > 0 else 0
        lines.append(f"Avg Fee Per Trade:       {avg_fee_per_trade:17,.2f} {currency}")
        lines.append(f"Avg Fee Per 100 Opt:     {avg_fee_per_100:17,.4f} {currency}")

    lines.append(f"{CYAN}{'-' * 112}{RESET}")
    wl = "inf" if win_loss == float('inf') else f"{win_loss:.2f}"
    lines.append(f"Positive Trades: {len(pos_ret)} | Negative Trades: {len(neg_ret)} | Win/Loss: {wl} | Win Rate: {win_rate:.1f}%")
    lines.append(f"Average Positive: {GREEN}{avg_pos:17,.2f}{RESET} {currency} | Average Negative: {RED}{avg_neg:17,.2f}{RESET} {currency}")
    lines.append(f"{CYAN}{'-' * 112}{RESET}")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Summarize gains from taxjson_gains.py output.")
    parser.add_argument("--sort-by", "-s", choices=['total', 'total_gain', 'capital_gain', 'option_gain', 'dividend', 'pil', 'holding_days', 'ticker'], default='ticker')
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    parser.add_argument("files", nargs="*", metavar="FILE")
    args = parser.parse_args()
    
    file_paths = [Path(f) for f in args.files]
    if not file_paths:
        raw = load_report_json(None)
        report_data = summarize_gains(raw)
    else:
        all_by_ticker = {}
        all_txs = []
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
        for fp in file_paths:
            data = load_gains_data(fp)
            if 'by_ticker' in data:
                all_by_ticker.update(data['by_ticker'])
            if 'transactions' in data:
                all_txs.extend(data['transactions'])
            summary = data.get('summary') or {}
            if summary.get('year'):
                total_year = summary['year']
            wash_converged = wash_converged and summary.get('wash_solver_converged', True)
            for curr, bucket in (summary.get('total_fees_by_currency') or {}).items():
                m = merged_fees.setdefault(curr, {'stocks': 0.0, 'options': 0.0, 'total': 0.0})
                for k in ('stocks', 'options', 'total'):
                    m[k] += float(bucket.get(k, 0.0) or 0.0)
        merged_data = {
            'by_ticker': all_by_ticker,
            'transactions': all_txs,
            'summary': {'year': total_year, 'total_fees_by_currency': merged_fees,
                        'wash_solver_converged': wash_converged}
        }
        report_data = summarize_gains(merged_data)

    if args.json:
        print(json.dumps(report_data, indent=2, sort_keys=True))
        return
    print(format_report(report_data, args.sort_by, args.no_color))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
taxjson_ccd_gains.py

Summarize Covered Call (Short Option) gains by underlying from taxjson_gains.py output.
Ported from tt_ccd_gains.pl.
"""

import argparse
import json
import sys

from taxjson.lib.report_model import load_report_json
from taxjson.lib.ticker_map import get_underlying, is_option_ticker, get_option_type
from taxjson.lib import cli_diag

PROG = "taxjson-ccd-gains"

def process_data(data, ccd_by_underlying):
    transactions = data.get('transactions', [])
    # RCI...TO calls on RCI.B.TO shares group under the class share,
    # not a phantom RCI.TO (S040-11).
    from taxjson.lib.ticker_map import class_share_aliases
    _aliases = class_share_aliases(t.get('symbol') for t in transactions)
    for tx in transactions:
        symbol = tx.get('symbol', '')
        if not is_option_ticker(symbol):
            continue
            
        # Port logic: Calls only (C)
        if get_option_type(symbol) != 'C':
            continue
        
        # Port logic: Short positions only (Sold to Open)
        direction = tx.get('direction')
        if direction is None:
            # Fallback for older JSON files: infer from gain/proceeds/cost
            cost = float(tx.get('cost', 0.0))
            proceeds = float(tx.get('proceeds', 0.0))
            gain = float(tx.get('gain', 0.0))
            if abs(gain - (cost - proceeds)) < 0.01:
                direction = 'SHORT'
            else:
                direction = 'LONG'

        if direction != 'SHORT':
            continue
        # Real-world orientation, as ccd-sum and Schedule 3 show it:
        # PREMIUM = proceeds of the sold call, BUYBACK = cost to close.
        # Rows with an explicit direction carry the engines' signed legs
        # (cost = -premium, proceeds = -buyback) and printed as a
        # NEGATIVE cost / proceeds; legacy inferred rows carry them
        # swapped and positive (S028-04).
        _c = float(tx.get('cost', 0.0) or 0.0)
        _p = float(tx.get('proceeds', 0.0) or 0.0)
        tx = dict(tx)
        tx['_premium'], tx['_buyback'] = ((_c, _p)
                                          if tx.get('direction') is None
                                          else (-_c, -_p))
            
        if tx.get('tainted'):
            # Phantom-basis rows carry a fabricated cost: excluded (and
            # counted) exactly as ccd-sum / leaps-sum and every
            # filing-facing consumer do (audit R1-173).
            ccd_by_underlying.setdefault('_tainted', 0)
            ccd_by_underlying['_tainted'] += 1
            continue
        underlying = get_underlying(symbol)
        if not underlying:
            continue
        underlying = _aliases.get(underlying.upper(), underlying)
            
        if underlying not in ccd_by_underlying:
            ccd_by_underlying[underlying] = {
                'lines': [],
                'total_gain': 0.0
            }
            
        ccd_by_underlying[underlying]['lines'].append(tx)
        gain = float(tx.get('gain', 0.0))
        ccd_by_underlying[underlying]['total_gain'] += gain

def main():
    parser = argparse.ArgumentParser(description="Summarize Covered Call (Short Option) gains.")
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="Input JSON files from taxjson_gains.py "
                             "(default: stdin)")
    args = parser.parse_args()

    ccd_by_underlying = {}
    grand_total = 0.0

    # Process all inputs
    if not args.files:
        from taxjson.lib.report_model import strip_report_comments
        content = strip_report_comments(sys.stdin)
        if content.strip():
            data = json.loads(content)
            process_data(data, ccd_by_underlying)
        else:
            cli_diag.error(PROG, "no input provided")
            sys.exit(1)
    else:
        for input_path in args.files:
            try:
                data = load_report_json(input_path)
            except (OSError, ValueError) as e:
                # A report missing an input is not a report: the old
                # code printed TOTAL 0.00 and exited 0 (audit R1-173).
                cli_diag.error(PROG, f"cannot load {input_path}: {e}")
                sys.exit(1)
            process_data(data, ccd_by_underlying)

    tainted = ccd_by_underlying.pop('_tainted', 0)
    # Re-calculate grand total from aggregated data
    grand_total = sum(und_data['total_gain'] for und_data in ccd_by_underlying.values())

    # Sort underlyings
    sorted_underlyings = sorted(ccd_by_underlying.keys())

    for und in sorted_underlyings:
        print(f"COVERED CALLS — {und}")
        print()

        headers = ["DATE", "SYMBOL", "QTY", "CUR", "COST/SH", "PROC/SH", "GAIN/SH", "PREMIUM", "BUYBACK", "GAIN", "DAYS"]
        print(f"{headers[0]:<12} {headers[1]:<26} {headers[2]:>10} {headers[3]:<5} {headers[4]:>15} {headers[5]:>15} {headers[6]:>15} {headers[7]:>15} {headers[8]:>15} {headers[9]:>15} {headers[10]:>6}")
        print("-" * 159)   # header rule matches the table width

        # Per-share columns keep 4-decimal precision; the money columns
        # (COST/PROCEEDS/GAIN) are 2dp with thousands separators.
        for tx in ccd_by_underlying[und]['lines']:
            print(f"{tx.get('date'):<12} "
                  f"{tx.get('symbol'):<26} "
                  f"{float(tx.get('qty', 0)):10.4f} "
                  f"{(tx.get('currency') or '?'):<5} "
                  f"{float(tx.get('cost_per_share', 0)):15.4f} "
                  f"{float(tx.get('proceeds_per_share', 0)):15.4f} "
                  f"{float(tx.get('gain_per_share', 0)):15.4f} "
                  f"{tx['_premium'] + 0.0:15,.2f} "
                  f"{tx['_buyback'] + 0.0:15,.2f} "
                  f"{float(tx.get('gain', 0)):15,.2f} "
                  f"{int(tx.get('days_held', 0)):6}")

        print(f"\nTOTAL {und} COVERED CALL GAIN: {ccd_by_underlying[und]['total_gain']:,.2f}\n")

    # Summary
    print("COVERED CALLS SUMMARY")
    print()
    print(f"{'SYMBOL':<15} {'CCD_GAIN':>20}")
    print("-" * 36)

    # Sort summary descending by gain
    summary_items = sorted(ccd_by_underlying.items(), key=lambda x: x[1]['total_gain'], reverse=True)
    for und, stats in summary_items:
        print(f"{und:<15} {stats['total_gain']:20,.2f}")

    print("-" * 36)
    print(f"{'TOTAL':<15} {grand_total:20,.2f}")
    if tainted:
        print(f"\nNOTE: {tainted} tainted disposition(s) with phantom cost "
              f"basis excluded (report them by hand; form-export lists "
              f"them).")

if __name__ == "__main__":
    main()

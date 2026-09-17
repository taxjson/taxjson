#!/usr/bin/env python3
"""
taxjson_safe_to_sell.py

Proactive superficial loss prevention.
Focuses ONLY on shares held in TAXABLE accounts.
Ported from tt_safe_to_sell.pl.

Usage:
    python -m taxjson.bin.taxjson_safe_to_sell --taxable tax1.json --sheltered sh1.json [--date YYYY-MM-DD]
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

# UTC-noon epoch helper: shared home in lib/dates (DST rationale there).
from taxjson.lib.dates import date_to_epoch, noon_utc  # noqa: E402,F401

def main():
    parser = argparse.ArgumentParser(description="Taxable-Only Safe-to-Sell Audit")
    # nargs='+' + extend: both `--taxable a b` (historical) and repeated
    # `--taxable a --taxable b` (A2 composability) work.
    parser.add_argument("--taxable", nargs='+', action='extend', default=[],
                        required=True, metavar="FILE",
                        help="Taxable transaction JSON files (repeatable)")
    parser.add_argument("--sheltered", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="Sheltered transaction JSON files (repeatable)")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), defaults to today")
    
    args = parser.parse_args()

    if args.date:
        today_dt = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        today_dt = datetime.now()
    
    today_dt = noon_utc(today_dt)
    today_epoch = today_dt.timestamp()

    taxable_inventory = {} # ticker -> [ {date, qty, epoch} ]
    last_acq_global = {}   # ticker -> epoch (Any account)

    def process_files(files, is_taxable):
        for f in files:
            path = Path(f)
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                txs = data.get("transactions", [])
                # Transactions are likely sorted already, but let's be safe
                sorted_txs = sorted(txs, key=lambda x: (x.get('date'), x.get('time', '09:30:00')))
                
                for tx_data in sorted_txs:
                    # Settle-basis, matching the engine's CRA window.
                    date = tx_data.get('date_settle') or tx_data.get('date')
                    ticker = tx_data.get('symbol')
                    # `qty` alias: some producers emit 'qty' (matches the
                    # radar's alias handling).
                    qty = tx_data.get('quantity', tx_data.get('qty', 0.0))
                    action = tx_data.get('action', '').upper()

                    epoch = date_to_epoch(date)
                    if epoch > today_epoch:
                        continue

                    if action == 'SPLIT':
                        # Scale held lots by the ratio (mirrors the radar /
                        # engine walks). Ignoring splits showed phantom
                        # quantities: a 1:10 reverse split left the pre-split
                        # count, and post-split sells over-drained — hiding a
                        # genuinely held (possibly LOCKED) position.
                        ratio = qty
                        if is_taxable and ratio and abs(ratio) > 1e-12 \
                                and ticker in taxable_inventory:
                            for lot in taxable_inventory[ticker]:
                                lot['qty'] *= ratio
                        continue

                    # ASSIGN (option assignment/exercise) acquires and
                    # disposes shares exactly like BUYSELL — the engine
                    # and the radar both count it as a trigger; skipping
                    # it hid a LOCK opened by an assigned put.
                    if action not in ('BUYSELL', 'ASSIGN'):
                        continue

                    if qty > 0:
                        # ANY buy is a window trigger
                        if ticker not in last_acq_global or epoch > last_acq_global[ticker]:
                            last_acq_global[ticker] = epoch
                        
                        # Only track inventory for TAXABLE
                        if is_taxable:
                            if ticker not in taxable_inventory:
                                taxable_inventory[ticker] = []
                            taxable_inventory[ticker].append({'epoch': epoch, 'date': date, 'qty': qty})
                    else:
                        # Drain taxable inventory ONLY
                        if is_taxable:
                            to_rem = abs(qty)
                            if ticker in taxable_inventory:
                                while to_rem > 1e-6 and taxable_inventory[ticker]:
                                    lot = taxable_inventory[ticker][0]
                                    if lot['qty'] <= to_rem + 1e-6:
                                        to_rem -= lot['qty']
                                        taxable_inventory[ticker].pop(0)
                                    else:
                                        lot['qty'] -= to_rem
                                        to_rem = 0.0

    process_files(args.taxable, True)
    process_files(args.sheltered, False)

    print("TAXABLE-ONLY SAFE-TO-SELL AUDIT — CRA 30-day window")
    print()
    headers = ["TICKER", "TAXABLE QTY", "STATUS", "REASON"]
    output_rows = []

    for t in sorted(taxable_inventory.keys()):
        lots = taxable_inventory[t]
        total = sum(lot['qty'] for lot in lots)
        if total < 0.001:
            continue

        lacq = last_acq_global.get(t, 0.0)
        safe_epoch = lacq + (31 * 86400)
        # UTC to round-trip the UTC-noon epochs back to the same calendar day.
        last_date = datetime.fromtimestamp(lacq, tz=timezone.utc).strftime("%Y-%m-%d")
        safe_date = datetime.fromtimestamp(safe_epoch, tz=timezone.utc).strftime("%Y-%m-%d")
        
        status = "SAFE"
        reason = "NO RECENT BUYS"
        
        if today_epoch < safe_epoch:
            status = "LOCKED"
            reason = f"Last Buy {last_date} (Safe {safe_date})"
        
        output_rows.append([t, f"{total:.4f}", status, reason])

    if not output_rows:
        print("No taxable holdings found.")
        return

    widths = [len(h) for h in headers]
    for row in output_rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("-" * (sum(widths) + 2 * (len(widths)-1)))

    for row in output_rows:
        print(fmt.format(*row))

    print("\nNOTE: 'SAFE' means no acquisitions occurred in the last 30 days.")
    print("To fully avoid superficial loss, you must also NOT REPURCHASE for 30 days AFTER selling.")

if __name__ == "__main__":
    main()

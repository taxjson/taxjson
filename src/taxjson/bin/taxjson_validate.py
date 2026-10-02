#!/usr/bin/env python3
"""
taxjson_validate.py

Validates a taxjson file for obvious mistakes, missing fields,
malformed tickers, date issues, etc.

Usage:
    taxjson-validate input1.json [input2.json ...]
"""

import argparse
import json
import re
import sys
from pathlib import Path

from taxjson.lib.report_model import load_report_json
from datetime import datetime
from collections import defaultdict

from taxjson.lib import cli_diag

PROG = "taxjson-validate"

def validate_transactions(transactions, filename="input",
                          require_prices=False):
    """`require_prices`: a BUYSELL/DIVIDEND that moves a nonzero
    quantity at price 0 AND net 0 is an ERROR — the crypto path, where
    such a row is one taxjson-fill-crypto could not price (a failed
    Yahoo lookup): $0 income, $0 cost or $0 proceeds, never a real
    value. Equity books legitimately carry $0 rows (spin-off receipts,
    gifted units), so the check is opt-in."""
    issues = defaultdict(list)
    warnings = defaultdict(list)
    
    valid_actions = {
        "BUYSELL", "DIVIDEND", "DIVIDEND_IN_LIEU", "FEE", "INTEREST",
        "SPLIT", "TAX", "ASSIGN", "TRANSFER", "INCOME",
        "OPENING_BALANCE",
    }
    
    import math

    for i, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            issues[f"TX index_{i} (row {i+1})"].append(
                f"Row is not a JSON object (got {type(tx).__name__})")
            continue
        tx_id = tx.get("id", f"index_{i}")
        context = f"TX {tx_id} (row {i+1})"

        # 0. Type checks (stage-tools audit). The validator used to
        # trust the JSON types: a null net_amount passed as OK (then
        # crashed every loader), an int date / list action / float
        # symbol crashed the validator itself on regex/len/hash.
        # Every later check runs on a str()-coerced view so a wrong
        # type is reported as an ERROR instead of a traceback.
        for _fld in ("action", "date", "date_settle", "time", "symbol",
                     "currency", "account", "type", "description",
                     "symbol_new", "id"):
            _v = tx.get(_fld)
            if _v is not None and not isinstance(_v, str):
                issues[context].append(
                    f"Field '{_fld}' must be a string, got "
                    f"{type(_v).__name__}: {_v!r}")
        # Numeric view: value or None when the field is unusable.
        nums = {}
        for _fld in ("quantity", "price", "net_amount", "gross_amount",
                     "fee", "commission", "proceeds"):
            if _fld not in tx:
                nums[_fld] = 0.0
                continue
            _v = tx[_fld]
            if _v is None:
                issues[context].append(
                    f"Null {_fld} (must be a number; loaders refuse null)")
                nums[_fld] = None
            elif isinstance(_v, bool) or not isinstance(_v, (int, float, str)):
                issues[context].append(
                    f"Non-numeric {_fld}: {_v!r} ({type(_v).__name__})")
                nums[_fld] = None
            elif isinstance(_v, str):
                try:
                    _f = float(_v)
                except ValueError:
                    issues[context].append(f"Non-numeric {_fld}: {_v!r}")
                    nums[_fld] = None
                    continue
                if not math.isfinite(_f):
                    issues[context].append(f"Non-finite {_fld}: {_v!r}")
                    nums[_fld] = None
                else:
                    issues[context].append(
                        f"{_fld} is a string {_v!r} — must be a JSON "
                        f"number (loaders coerce it, emitters must not "
                        f"rely on that)")
                    nums[_fld] = _f
            elif isinstance(_v, float) and not math.isfinite(_v):
                # Non-finite numerics corrupt every downstream computation.
                issues[context].append(f"Non-finite {_fld}: {_v!r}")
                nums[_fld] = None
            else:
                nums[_fld] = float(_v)

        def _s(field):
            v = tx.get(field)
            return "" if v is None else str(v)

        # 1. Action validation
        action = _s("action")
        if not action:
            issues[context].append("Missing 'action'")
        elif action not in valid_actions:
            warnings[context].append(f"Unknown action: '{action}'")

        # 2. Date validation
        date = _s("date")
        if not date:
            issues[context].append("Missing 'date'")
        elif not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            issues[context].append(f"Malformed date: '{date}' (expected YYYY-MM-DD)")
        else:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                issues[context].append(
                    f"Impossible date: '{date}' (not a real calendar day)")

        date_settle = _s("date_settle")
        if date_settle and not re.match(r"^\d{4}-\d{2}-\d{2}$", date_settle):
            issues[context].append(f"Malformed date_settle: '{date_settle}'")
        elif date_settle:
            try:
                datetime.strptime(date_settle, "%Y-%m-%d")
            except ValueError:
                issues[context].append(
                    f"Impossible date_settle: '{date_settle}'")
            else:
                # CA-DATE-03 / US-DATE-04 (audit A2-0959).
                if (action in ("BUYSELL", "ASSIGN") and date
                        and re.match(r"^\d{4}-\d{2}-\d{2}$", date)):
                    if date_settle < date:
                        issues[context].append(
                            f"date_settle {date_settle} is before the "
                            f"trade date {date} (a settlement never "
                            f"precedes its trade)")
                    elif (datetime.strptime(date_settle, "%Y-%m-%d")
                          - datetime.strptime(date, "%Y-%m-%d")).days > 31:
                        warnings[context].append(
                            f"date_settle {date_settle} is more than a "
                            f"month after the trade date {date} — "
                            f"implausible for a settlement cycle")

        if action == "SPLIT" and nums["quantity"] is not None \
                and nums["quantity"] <= 0:
            issues[context].append(
                "SPLIT ratio must be > 0 "
                f"(got {tx.get('quantity')!r})")

        # 3. Time validation
        time_str = _s("time")
        if time_str and not re.match(r"^\d{2}:\d{2}:\d{2}$", time_str):
            warnings[context].append(f"Malformed time: '{time_str}' (expected HH:MM:SS)")

        # 4. Symbol validation
        symbol = _s("symbol")
        if action in {"BUYSELL", "ASSIGN", "SPLIT"}:
            if not symbol:
                issues[context].append(f"Missing 'symbol' for action '{action}'")

        if symbol:
            if re.search(r"\s", symbol):
                issues[context].append(f"Symbol contains spaces: '{symbol}'")

            # Check for synthetic unmapped options (e.g. 8ABCDE1.TO)
            if re.match(r"^\d+[A-Z0-9]{4,8}\.(TO|US)$", symbol):
                warnings[context].append(f"Possible unmapped synthetic option ticker: '{symbol}'")

        # 5. Quantity / Price validation
        if action in {"BUYSELL", "ASSIGN"}:
            # A MISSING key books as 0 in the engines (audit R1-162).
            for _fld in ("quantity", "net_amount"):
                if _fld not in tx and not (_fld == "quantity"
                                           and "qty" in tx):
                    issues[context].append(
                        f"Missing '{_fld}' for action '{action}'")
            # None = unusable value, already reported by the type check.
            qty_val = nums["quantity"]
            if qty_val is not None and qty_val == 0:
                issues[context].append(f"Quantity is 0 for action '{action}'")

            price_val = nums["price"]
            # A futures price can be negative (WTI, April 2020; a
            # spread) and is booked correctly (audit S053-13), whatever
            # futures prefix the symbol carries (A2-1087/A2-1089).
            if (price_val is not None and price_val < 0
                    and not (symbol or "").startswith(("F:", "/", "\\"))):
                issues[context].append(f"Price is negative: {price_val}")

        if (require_prices and action in {"BUYSELL", "DIVIDEND"}
                and symbol not in ("USD", "CAD")
                and nums["quantity"] is not None
                and abs(nums["quantity"]) > 1e-12
                and nums["price"] is not None and abs(nums["price"]) < 1e-8
                and nums["net_amount"] is not None
                and abs(nums["net_amount"]) < 1e-8):
            issues[context].append(
                f"Unpriced {action} {symbol} {date}: quantity "
                f"{nums['quantity']:g} at price 0 and net 0 — the crypto "
                f"price lookup failed, so this books $0 "
                f"{'income' if action == 'DIVIDEND' else 'cost/proceeds'}"
                f". Re-run online, add the price, or fix the symbol in "
                f"crypto_ticker.map.")

        # 6. Currency validation
        currency = _s("currency")
        if not currency:
            issues[context].append("Missing 'currency'")
        elif len(currency) != 3:
            warnings[context].append(f"Suspicious currency code: '{currency}'")

    return issues, warnings

def main():
    parser = argparse.ArgumentParser(description="Validate taxjson for obvious mistakes")
    parser.add_argument("files", nargs="+", help="Input JSON file(s)")
    parser.add_argument("--warnings", action="store_true", help="Show warnings as well as errors")
    parser.add_argument("--require-prices", action="store_true",
                        help="ERROR on a BUYSELL/DIVIDEND with a nonzero "
                             "quantity but price 0 and net 0 (an unpriced "
                             "crypto row; `taxjson run` sets it for crypto "
                             "accounts)")
    args = parser.parse_args()
    
    global_total_issues = 0
    global_total_warnings = 0
    
    for filename in args.files:
        try:
            data = load_report_json(Path(filename))
        except Exception as e:
            cli_diag.error(PROG, f"failed to load JSON {filename}: {e}")
            global_total_issues += 1
            continue
            
        fx_fallback = []
        if isinstance(data, dict) and "transactions" in data:
            # taxjson-convert-currency records rows it converted at the
            # default FX rate; each is an ERROR here (the crypto path
            # validates the converted file, so --strict sees them).
            meta = data.get("metadata")
            if isinstance(meta, dict) and isinstance(
                    meta.get("fx_default_rate_rows"), list):
                fx_fallback = meta["fx_default_rate_rows"]
            data = data["transactions"]
            
        if not isinstance(data, list):
            cli_diag.error(PROG, f"{filename}: expected a JSON array of transactions, or an object with a 'transactions' array")
            global_total_issues += 1
            continue
            
        issues, warnings = validate_transactions(
            data, filename=filename, require_prices=args.require_prices)
        for r in fx_fallback:
            if isinstance(r, dict):
                issues[f"TX {r.get('id')} ({r.get('action')} "
                       f"{r.get('symbol')} {r.get('date')})"].append(
                    f"FX: no {r.get('currency')} rate for {r.get('date')} "
                    f"({r.get('reason')}); converted at the default rate")
        
        num_tx = len(data)
        total_issues = sum(len(v) for v in issues.values())
        total_warnings = sum(len(v) for v in warnings.values())
        
        global_total_issues += total_issues
        global_total_warnings += total_warnings
        
        if total_issues == 0 and (total_warnings == 0 or not args.warnings):
            print(f"OK: {filename}: {num_tx} transactions validated.")
        
        if total_issues > 0 or (total_warnings > 0 and args.warnings):
            print(f"Validation found {total_issues} errors and {total_warnings} warnings in {filename} ({num_tx} transactions):\n")
            
            all_contexts = set(issues.keys()) | (set(warnings.keys()) if args.warnings else set())
            
            for ctx in sorted(all_contexts):
                if ctx in issues or (args.warnings and ctx in warnings):
                    print(f"- {ctx}")
                    # Report-content row labels (stdout), not diagnostics:
                    # per AUDIT-2026-07-ui §1C3 the per-record detail IS the
                    # tool's report.
                    for err in issues.get(ctx, []):
                        print(f"  ERROR: {err}")
                    if args.warnings:
                        for warn in warnings.get(ctx, []):
                            print(f"  WARNING: {warn}")
                    print()

    if global_total_issues == 0:
        if global_total_warnings == 0 or not args.warnings:
            print(f"OK: Validation passed. No obvious issues found across {len(args.files)} files.")
            if global_total_warnings > 0:
                print(f"   (There are {global_total_warnings} warnings hidden. Run with --warnings to see them)")
            sys.exit(0)
        else:
            # We had warnings and user wanted them, but no issues.
            # The individual file warnings were already printed.
            sys.exit(0)
    else:
        cli_diag.error(PROG, f"validation failed with {global_total_issues} total errors")
        sys.exit(1)

if __name__ == "__main__":
    main()

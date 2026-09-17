#!/usr/bin/env python3
"""
taxjson_sort.py

Sort transactions by date/time and optionally deduplicate.

Usage:
    python -m taxjson.bin.taxjson_sort input.json [--dedup]

The script reads transactions from JSON, sorts them chronologically, and writes
the result to stdout. Optionally deduplicates by transaction ID or synthetic UID.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

from taxjson.lib.core import TaxTransaction, load_transactions

PROG = "taxjson-sort"


def generate_uid(tx: TaxTransaction) -> str:
    """Generate a unique ID for a transaction."""
    if getattr(tx, "id", None):
        return tx.id
    
    parts = [
        getattr(tx, "brokerage", "unknown"),
        getattr(tx, "account", "default"),
        getattr(tx, "symbol", "unknown"),
        getattr(tx, "date", "unknown"),
        getattr(tx, "time", "00:00:00"),
    ]
    return "_".join(str(p) for p in parts)


def validate_transactions(transactions: List[TaxTransaction]) -> Tuple[List[TaxTransaction], List[str]]:
    """Validate with the SHARED schema validator
    (lib/brokerages/schema.validate_transactions) and return
    `(transactions, error_messages)` — every row, always.

    The private validator this replaced required a symbol on EVERY
    action and a currency of exactly 3 characters, then the non-strict
    CLI path silently DROPPED each failing row: a Coinbase row priced
    in 'USDC', or a TAX/INTEREST row without a symbol, vanished from
    the crypto pipeline (which runs `taxjson-sort --dedup` with no
    --strict) — and the "Validation error:" stderr line did not match
    the pipeline's diag-marker format, so the .sum never showed the
    drop (stage-tools audit). Rows are never dropped now; the caller
    decides between warning (default) and abort (--strict)."""
    from taxjson.lib.brokerages.schema import (
        validate_transactions as _schema_validate,
    )
    errors, _warnings = _schema_validate([tx.to_dict() for tx in transactions])
    return list(transactions), errors


def sort_transactions(transactions: List[TaxTransaction]) -> List[TaxTransaction]:
    """Sort transactions by date and time."""
    def sort_key(tx):
        date_str = getattr(tx, "date", "9999-99-99")
        time_str = getattr(tx, "time", "00:00:00")
        return (date_str, time_str)
    
    return sorted(transactions, key=sort_key)


def deduplicate(transactions: List[TaxTransaction], return_dropped: bool = False):
    """Remove duplicate transactions by UID.

    With `return_dropped=True` returns `(kept, dropped)` so callers can
    report which rows collapsed (useful when --dedup removes more rows
    than expected and the user needs to audit which broker/account/date
    combinations are colliding). The default-False signature preserves
    backwards compatibility with the bare `deduplicate(txs)` callers.
    """
    seen_uids: dict = {}
    kept: List[TaxTransaction] = []
    dropped: List[TaxTransaction] = []

    for tx in transactions:
        uid = generate_uid(tx)
        if uid not in seen_uids:
            seen_uids[uid] = tx
            kept.append(tx)
        else:
            dropped.append(tx)

    if return_dropped:
        return kept, dropped
    return kept


def main():
    parser = argparse.ArgumentParser(description="Sort and optionally deduplicate transactions")
    parser.add_argument("input", nargs="?", help="Input JSON file (default: stdin)")
    parser.add_argument("--dedup", action="store_true", help="Remove duplicate transactions")
    parser.add_argument("--strict", action="store_true", help="Fail on validation errors")
    parser.add_argument("--no-validation", action="store_true", help="Skip validation")
    args = parser.parse_args()

    # Load input through the shared loader funnel (file: core.load_
    # transactions; stdin: the pipeline's stdin loader) — `#` comments,
    # qty alias, type guards; a malformed row is a clean error.
    try:
        if args.input:
            transactions = load_transactions(Path(args.input))
        else:
            from taxjson.lib.pipeline import load_stdin_transactions
            transactions = load_stdin_transactions()
    except ValueError as e:
        print(f"{PROG}: error: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate transactions (unless disabled). NEVER drops a row:
    # without --strict each schema error is a `<prog>: warning:` line
    # (the pipeline's diag-marker format, so it lands in the .sum) and
    # the row flows on; with --strict they are errors and we abort.
    if not args.no_validation:
        transactions, errors = validate_transactions(transactions)
        if errors:
            level = 'error' if args.strict else 'warning'
            for err in errors:
                print(f"{PROG}: {level}: validation: {err}", file=sys.stderr)
            if args.strict:
                print(f"{PROG}: error: aborting due to {len(errors)} "
                      f"validation error(s) (--strict)", file=sys.stderr)
                sys.exit(1)
            print(f"{PROG}: warning: {len(errors)} validation error(s) "
                  f"above; every row was KEPT (no row is dropped by "
                  f"validation) — fix the input or run with --strict "
                  f"to abort.", file=sys.stderr)
    
    # Sort first, then dedup. Dedup keeps the *first* occurrence of each
    # UID — if it ran before sort, the survivor depended on input-file
    # ordering rather than chronology. Re-running over a re-ordered
    # concatenation of the same data could then keep a different row.
    # `taxjson-merge2` has always done sort→dedup; this brings the bare
    # `taxjson-sort --dedup` into the same order.
    transactions = sort_transactions(transactions)

    if args.dedup:
        transactions = deduplicate(transactions)

    # Sort is a passthrough — preserve full input precision so downstream
    # calculations don't see truncated values.
    output_data = {"transactions": [tx.to_dict() for tx in transactions]}

    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()  # Add final newline


if __name__ == "__main__":
    main()

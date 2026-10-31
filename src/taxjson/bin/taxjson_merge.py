#!/usr/bin/env python3
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

from taxjson.lib import cli_diag

PROG = "taxjson-merge"

def main():
    parser = argparse.ArgumentParser(description="Merge transaction JSON files")
    parser.add_argument("files", nargs="+", help="Input JSON files")
    parser.add_argument(
        "--opening-cutoff", action="store_true",
        help="Apply the opening snapshots' cut-off (lib/opening) to the "
             "merged books: a rename one account's books carry reaches "
             "another account's rows of the old symbol, which its own "
             "merge could not tell (backlog TJ-0964).")
    parser.add_argument("--year", type=int, default=None,
                        help="With --opening-cutoff: the tax year whose "
                             "left-out sale stops the merge.")
    parser.add_argument("--country", default=None,
                        help="With --opening-cutoff: the project's "
                             "country.")
    parser.add_argument("--tax-date", choices=("settle", "trade"),
                        default=None,
                        help="With --opening-cutoff: the project's date "
                             "basis for --year.")
    args = parser.parse_args()

    merged_transactions = []
    opening_cut = []
    metadata = {
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "sources": []
    }

    # Every input is required: `tjs run` feeds this tool only books
    # it just built (crypto merge, blended base, sheltered_base, audit
    # tie-out). A missing or unreadable one used to be skipped at exit 0
    # — a truncated cached Coinbase book dropped half the crypto gains
    # under `run --fast` with a clean console (R1-295) and a damaged
    # sheltered book vanished from the superficial-loss context
    # (R1-260). Same policy as taxjson-merge2: no partial merge.
    failed = False
    for file_path in args.files:
        path = Path(file_path)
        if not path.exists():
            cli_diag.error(PROG, f"{file_path} not found")
            failed = True
            continue

        try:
            # utf-8-sig: a BOM is dropped (re-audit A2-1412).
            with open(path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            if not isinstance(data, dict) or "transactions" not in data:
                # Read as an empty book at exit 0 (audit S033-01).
                raise ValueError("no 'transactions' list")
            txs = data.get("transactions", [])
            if not isinstance(txs, list):
                raise ValueError("'transactions' is not a list")
            merged_transactions.extend(txs)
            _meta = data.get("metadata")
            if isinstance(_meta, dict):
                opening_cut.extend(_meta.get("opening_cut") or [])
            metadata["sources"].append({
                "file": str(path),
                "count": len(txs),
                "original_metadata": data.get("metadata", {})
            })
        except Exception as e:
            cli_diag.error(PROG, f"cannot read {file_path}: {e}")
            failed = True
    if failed:
        cli_diag.error(PROG, "one or more inputs are missing or "
                             "unreadable; refusing to emit a partial "
                             "merge.")
        # exit 2: an input that cannot be read, not a finding (A2-0164)
        sys.exit(2)

    if args.opening_cutoff:
        # Each account's merge (taxjson-merge2) cut its own rows at its
        # snapshot with the renames ITS books carry; a rename only
        # another account reports reaches this account's old symbol
        # here, where every account's rows meet (backlog TJ-0964).
        from taxjson.lib.opening import OpeningError, apply_opening_cutoff
        try:
            merged_transactions = apply_opening_cutoff(
                merged_transactions, year=args.year, country=args.country,
                tax_date=args.tax_date, earlier_cut=opening_cut)
        except OpeningError as e:
            cli_diag.error(PROG, str(e))
            sys.exit(1)

    output = {
        "transactions": merged_transactions,
        "metadata": metadata
    }

    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

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
    args = parser.parse_args()

    merged_transactions = []
    metadata = {
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "sources": []
    }

    # Every input is required: `taxjson run` feeds this tool only books
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

    output = {
        "transactions": merged_transactions,
        "metadata": metadata
    }

    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

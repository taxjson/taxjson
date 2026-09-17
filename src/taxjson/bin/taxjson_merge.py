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

    for file_path in args.files:
        path = Path(file_path)
        if not path.exists():
            cli_diag.warn(PROG, f"{file_path} not found, skipping")
            continue
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                txs = data.get("transactions", [])
                merged_transactions.extend(txs)
                metadata["sources"].append({
                    "file": str(path),
                    "count": len(txs),
                    "original_metadata": data.get("metadata", {})
                })
        except Exception as e:
            cli_diag.error(PROG, f"cannot read {file_path}: {e}")

    output = {
        "transactions": merged_transactions,
        "metadata": metadata
    }

    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Auto-detect brokerage from CSV file header.

Returns brokerage name that can be passed to taxjson_brokerage.py
"""

import csv
import sys
from pathlib import Path
from typing import Optional

from taxjson.lib import cli_diag

PROG = "taxjson-detect-brokerage"


def detect_brokerage(file_path: Path) -> Optional[str]:
    """
    Detect the brokerage from a CSV file header.
    
    Returns:
        Brokerage name (ib, rbc_direct, questrade, webull) or None if not detected
    """
    try:
        with file_path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            first_row = next(reader, None)
            
            if not first_row:
                return None
            
            # Check for Questrade format (Transaction Date, Settlement Date, Action, Symbol, ...)
            # Headers: Transaction Date, Settlement Date, Action, Symbol, Description, Quantity, Price, Gross Amount, Commission, Net Amount, Currency, Account #, Activity Type, Account Type
            if len(first_row) >= 14:
                headers = [h.strip().lower() for h in first_row]
                # Check for Questrade-specific column names
                if any("transaction date" in h for h in headers) and \
                   any("settlement date" in h for h in headers) and \
                   any("action" in h for h in headers):
                    return "questrade"
            
            # Check for IB format (has "Statement" header rows with BrokerName)
            # First row: Statement, Header, Field Name, Field Value
            # Second row: Statement, Data, BrokerName, Interactive Brokers Canada Inc.
            if first_row[0] == "Statement" and first_row[1] == "Header":
                try:
                    second_row = next(reader)
                    if second_row and len(second_row) >= 4:
                        # "Interactive Brokers" is the canonical broker
                        # name string in every IB statement variant.
                        # Bare `"IB" in val` previously matched any
                        # uppercase IB substring (e.g. holdings named
                        # "...LIBOR..." or "...IBM..." in a row value).
                        for val in second_row:
                            if "Interactive Brokers" in val:
                                return "ib"
                except (StopIteration, IndexError):
                    pass
            
            fh.seek(0)
            content = fh.read()

            # Check for Webull format BEFORE the RBC test — but on
            # Webull's STRUCTURAL marker only. "Account Number" alone
            # is generic broker-preamble boilerplate: RBC Direct
            # exports carry `"Account Number: ..."` too, and matching
            # it here routed real RBC files to the Webull parser,
            # which emitted 0 transactions with only a warning
            # (2026-09 audit — the repo's own rbc_direct_demo.csv
            # reproduced it). A Webull file holding a ticker like RBC
            # BEARINGS is still safe: "Action Code" is checked first.
            if "Action Code" in content:
                return "webull"

            # Check for RBC format: require RBC's own export markers rather
            # than the bare substring "RBC" (which any file merely mentioning
            # an RBC-named security contains). Case-insensitive — some
            # exports carry the name in caps.
            content_lower = content.lower()
            if ("activity export" in content_lower and "account:" in content_lower) \
                    or "rbc direct investing" in content_lower \
                    or "rbc dominion" in content_lower \
                    or "account activity detail report" in content_lower:
                return "rbc_direct"

            # Structural RBC fallback: exports round-tripped through
            # Excel/Sheets lose the "Activity Export"/"Account:" preamble
            # entirely — the file starts at the column header. The RBC
            # header shape (bare 'Date' + 'Activity' + 'Symbol' +
            # 'Settlement Date') is distinctive: Questrade uses
            # 'Transaction Date' (matched above), Webull uses 'Action
            # Code' (matched above). The rbc_direct parser already
            # handles this variant's ISO datetimes.
            header_cols = {h.strip().lower() for h in first_row}
            if {"date", "activity", "symbol",
                    "settlement date"} <= header_cols:
                return "rbc_direct"

            return None
            
    except Exception as e:
        cli_diag.error(PROG, f"detecting brokerage: {e}")
        return None


def main():
    import argparse
    ap = argparse.ArgumentParser(
        prog="taxjson-detect-brokerage",
        description="Print which brokerage parser a CSV resolves "
                    "to (content markers; the pipeline additionally "
                    "applies filename hints for crypto exports).")
    ap.add_argument("csv", type=Path, help="CSV file to identify")
    args = ap.parse_args()

    file_path = args.csv
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    brokerage = detect_brokerage(file_path)
    
    if brokerage:
        print(brokerage)
    else:
        print("unknown", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

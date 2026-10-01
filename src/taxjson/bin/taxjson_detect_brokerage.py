#!/usr/bin/env python3
"""
Auto-detect brokerage from CSV file header.

Returns brokerage name that can be passed to taxjson_brokerage.py
"""

import csv
import io
import sys
from pathlib import Path
from typing import Optional

from taxjson.lib import cli_diag
from taxjson.lib.brokerages.base import BrokerageParseError, decode_broker_text

PROG = "taxjson-detect-brokerage"


# Sections an IB Activity Statement (or a Flex query with section codes)
# can start with. A file whose first row is `<one of these>,Header,...`
# has the section,Header/Data shape the IB parser reads — whether or not
# a BrokerName row follows (audit R1-57: a Trades-first Flex download, a
# statement without the BrokerName row, or a Title row before it all
# parsed, but `taxjson run` stopped with "cannot detect broker").
IB_SECTIONS = frozenset({
    'Statement', 'Account Information', 'Trades', 'Dividends',
    'Withholding Tax', 'Interest', 'Fees', 'Transfers', 'Corporate Actions',
    'Cash Report', 'Open Positions', 'Financial Instrument Information',
    'Change in Dividend Accruals', 'Commission Adjustments',
    'Options Expirations', 'Net Asset Value', 'Change in NAV',
    'Mark-to-Market Performance Summary',
    'Realized & Unrealized Performance Summary', 'Deposits & Withdrawals',
    'Transaction Fees'})
# How far into the file the header row may sit (an RBC preamble, blank
# spacer lines).
_SCAN_ROWS = 40


def looks_like_ib_rows(rows) -> bool:
    """The IB section,Header/Data shape: the first non-empty row is the
    Header row of a known IB section, and the file is IB's — it names
    Interactive Brokers, or has Header rows of two IB sections, or is a
    Trades-only Flex download with IB's Trades columns. (A lone
    `Statement,Header` table of some other export is not IB.)"""
    first = next((r for r in rows if r and any(c.strip() for c in r)), None)
    if not (first and len(first) >= 2 and first[1] == "Header"
            and first[0].strip() in IB_SECTIONS):
        return False
    sections = set()
    for row in rows:
        if len(row) >= 2 and row[1] == "Header" \
                and row[0].strip() in IB_SECTIONS:
            sections.add(row[0].strip())
        if any("Interactive Brokers" in c for c in row):
            return True
    if len(sections) >= 2:
        return True
    return (first[0].strip() == "Trades"
            and {"Asset Category", "Symbol"} <= {c.strip() for c in first})


def _csv_rows(text: str, limit: Optional[int] = None):
    rows = []
    try:
        for row in csv.reader(io.StringIO(text, newline="")):
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    except csv.Error:
        pass
    return rows


def looks_like_ib_text(text: str) -> bool:
    """looks_like_ib_rows over CSV text (`taxjson fetch` checks a Flex
    download with it, so fetch accepts exactly what detection routes)."""
    return looks_like_ib_rows(_csv_rows(text))


def _rbc_header_row(rows) -> bool:
    """An RBC activity header (bare Date + Activity + Symbol +
    Settlement Date) in any of the first rows — after a preamble or a
    blank spacer line, as the RBC parser finds it (audit S029-14)."""
    for row in rows[:_SCAN_ROWS]:
        cols = {h.strip().lower() for h in row}
        if {"date", "activity", "symbol", "settlement date"} <= cols:
            return True
    return False


def detect_brokerage(file_path: Path) -> Optional[str]:
    """
    Detect the brokerage from a CSV file header.

    Returns:
        Brokerage name (ib, rbc_direct, questrade, webull) or None if not detected
    """
    try:
        # Decoded the way the parsers do (decode_broker_text): a UTF-16
        # export the IB/Questrade/RBC parsers read is routed too (audit
        # R1-70); another encoding is said in one line.
        text = decode_broker_text(file_path.read_bytes(), file_path.name)
        rows = _csv_rows(text)
        first_row = next((r for r in rows if r and any(c.strip() for c in r)),
                         None)
        if not first_row:
            return None

        # Questrade format (Transaction Date, Settlement Date, Action,
        # Symbol, Description, Quantity, Price, Gross Amount, Commission,
        # Net Amount, Currency, Account #, Activity Type, Account Type)
        if len(first_row) >= 14:
            headers = [h.strip().lower() for h in first_row]
            if any("transaction date" in h for h in headers) and \
               any("settlement date" in h for h in headers) and \
               any("action" in h for h in headers):
                return "questrade"

        # IB: the section,Header/Data shape (Statement first in an
        # Activity Statement, Trades first in a Flex query). A BrokerName
        # row is not required.
        if looks_like_ib_rows(rows):
            return "ib"

        content = text

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
        # entirely, or keep only part of it. The RBC header shape (bare
        # 'Date' + 'Activity' + 'Symbol' + 'Settlement Date') is
        # distinctive: Questrade uses 'Transaction Date' (matched above),
        # Webull uses 'Action Code' (matched above).
        if _rbc_header_row(rows):
            return "rbc_direct"

        return None

    except BrokerageParseError as e:
        cli_diag.error(PROG, str(e))
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

#!/usr/bin/env python3
"""
xlsx_to_csv.py — convert an XLSX brokerage export to CSV.

Used as a preprocessor for brokerages that ship statements only as
.xlsx (no CSV download option). Reads one sheet, strips thousands-
separator commas from numeric-looking cells so the resulting CSV
parses cleanly downstream, and writes the result to a file or stdout.

Typical flow:
    taxjson-xlsx-to-csv broker_statement.xlsx -o broker_statement.csv
    taxjson-brokerage --brokerage <id> --account <name> broker_statement.csv > broker.json
"""
import argparse
import os
import re
import sys

from taxjson.lib import cli_diag
from taxjson.lib.install_hint import extra_hint

PROG = "taxjson-xlsx-to-csv"

# A first group of 0 is a decimal comma ('0,125'), never thousands
# (audit S055-08).
_GROUPED_NUMBER_RE = re.compile(
    r'^[+-]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?$')


def _clean_numeric_commas(val):
    """Return `val` as a clean numeric string when it parses as a
    number, otherwise as its original string.

    Pandas serializes already-numeric cells correctly on its own; this
    helper only normalizes strings like ``"1,234.56"`` → ``"1234.56"``
    so a downstream CSV parser doesn't have to handle them. Non-numeric
    strings (descriptions, dates, etc.) are passed through unchanged.
    """
    import pandas as pd
    if val is None or (not isinstance(val, str) and pd.isna(val)):
        return ""
    if isinstance(val, (int, float)):
        return val
    s_val = str(val).strip()
    # Only a VALID thousands grouping loses its commas ("1,234.56",
    # "-2,000,000"). A decimal comma ("12,34") or any other comma shape
    # passes through untouched, so the downstream strict parser refuses
    # it instead of it silently becoming 1234.
    if _GROUPED_NUMBER_RE.match(s_val):
        return s_val.replace(',', '')
    return s_val


def _apply_cells(df, fn):
    """Run `fn` over every cell. `DataFrame.map` is the modern API
    (pandas 2.1+); fall back to the older `applymap` on earlier
    versions so this tool installs cleanly on Python 3.9/3.10 environs
    that haven't bumped pandas yet."""
    if hasattr(df, 'map') and callable(getattr(df, 'map')):
        try:
            return df.map(fn)
        except TypeError:
            pass
    return df.applymap(fn)


def read_sheet(pd, input_file, sheet_name=0):
    """Read one sheet with EVERY cell as text, exactly as shown:

      * dtype=str — no type inference: an integer-looking id keeps its
        leading zeros, and a date cell comes through as ISO text
        ("2025-12-31 00:00:00"), which the parsers accept (Questrade's
        own CSV spells it "2025-12-31 12:00:00 AM").
      * keep_default_na=False — pandas' NA sentinels ("NA", "N/A",
        "null", "nan", ...) stay literal strings: the ticker NA
        (National Bank) used to become an empty cell. Only a truly
        empty cell is empty.
    """
    return pd.read_excel(input_file, sheet_name=sheet_name,
                         engine='openpyxl', dtype=str,
                         keep_default_na=False)


def convert_xlsx_to_csv(input_file: str, output_file=None, sheet_name=0) -> None:
    """Read `input_file` (one sheet), strip numeric-comma formatting,
    and write CSV to `output_file` or stdout."""
    # Check the input before importing pandas so a missing file reports
    # cleanly (exit 2, environment error) even without the xlsx extras.
    if not os.path.exists(input_file):
        cli_diag.error(PROG, f"no such file: {input_file}")
        sys.exit(2)

    import pandas as pd

    print(f"Reading {input_file!r}...", file=sys.stderr)
    df = read_sheet(pd, input_file, sheet_name)
    df = _apply_cells(df, _clean_numeric_commas)

    if output_file:
        df.to_csv(output_file, index=False, encoding='utf-8')
        print(f"Successfully converted to {output_file!r}.", file=sys.stderr)
    else:
        df.to_csv(sys.stdout, index=False, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert an XLSX brokerage export to CSV. Strips "
            "thousands-separator commas from numeric-looking cells so "
            "the resulting file parses cleanly downstream (typically "
            "as input to taxjson-brokerage)."
        ),
    )
    parser.add_argument("input", help="Input .xlsx file path.")
    parser.add_argument("-o", "--output",
                        help="Output .csv file path (default: stdout).")
    parser.add_argument("-s", "--sheet", default=0,
                        help="Sheet name or zero-based index "
                             "(default: 0, the first sheet).")
    args = parser.parse_args()

    # Allow `-s 0` / `-s 1` numeric arguments to pass through as int;
    # leave string names ("Trades", "Activity") alone.
    sheet = args.sheet
    try:
        sheet = int(sheet)
    except (TypeError, ValueError):
        pass

    try:
        convert_xlsx_to_csv(args.input, args.output, sheet)
    except ImportError as e:
        # Missing optional dependency = environment error → exit 2.
        cli_diag.error(
            PROG,
            f"{e}. taxjson-xlsx-to-csv requires the optional "
            f"`xlsx` extras: {extra_hint('xlsx')} (or pip install "
            f"pandas openpyxl into it).",
        )
        sys.exit(2)
    except Exception as e:
        cli_diag.error(PROG, str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

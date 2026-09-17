#!/usr/bin/env python3
"""
xlsx_to_csv.py — convert an XLSX brokerage export to CSV.

Used as a preprocessor for brokerages that ship statements only as
.xlsx (no CSV download option). Reads one sheet, strips thousands-
separator commas from numeric-looking cells so the resulting CSV
parses cleanly downstream, and writes the result to a file or stdout.

Typical flow:
    taxjson-xlsx-to-csv broker_statement.xlsx -o broker_statement.csv
    taxjson-brokerage --brokerage <id> --account-name <name> broker_statement.csv > broker.json
"""
import argparse
import os
import sys

from taxjson.lib import cli_diag

PROG = "taxjson-xlsx-to-csv"


def _clean_numeric_commas(val):
    """Return `val` as a clean numeric string when it parses as a
    number, otherwise as its original string.

    Pandas serializes already-numeric cells correctly on its own; this
    helper only normalizes strings like ``"1,234.56"`` → ``"1234.56"``
    so a downstream CSV parser doesn't have to handle them. Non-numeric
    strings (descriptions, dates, etc.) are passed through unchanged.
    """
    import pandas as pd
    if pd.isna(val):
        return ""
    if isinstance(val, (int, float)):
        return val
    s_val = str(val).strip()
    clean_s = s_val.replace(',', '')
    try:
        float(clean_s)
        return clean_s
    except ValueError:
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
    df = pd.read_excel(input_file, sheet_name=sheet_name, engine='openpyxl')
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
            f"`xlsx` extras: pip install 'taxjson[xlsx]' "
            f"(or pip install pandas openpyxl).",
        )
        sys.exit(2)
    except Exception as e:
        cli_diag.error(PROG, str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

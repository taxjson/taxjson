#!/usr/bin/env python3
"""
xlsx_to_csv.py — convert an XLSX brokerage export to CSV.

Used as a preprocessor for brokerages that ship statements only as
.xlsx (no CSV download option). Reads one sheet, every cell as the
text it shows (openpyxl, no header inference: a label given twice
stays twice, an id cell formatted 00000000 keeps its leading zeros),
strips thousands-separator commas from numeric-looking cells so the
resulting CSV parses cleanly downstream, and writes the result to a
file or stdout.

Typical flow:
    taxjson-xlsx-to-csv broker_statement.xlsx -o broker_statement.csv
    taxjson-brokerage --brokerage <id> --account <name> broker_statement.csv > broker.json
"""
import argparse
import csv
import datetime as _dt
import io
import math
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
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    if not isinstance(val, (str, int, float)):
        # A pandas frame's missing value (NaT, pd.NA).
        try:
            import pandas as pd
            if pd.isna(val):
                return ""
        except (ImportError, TypeError, ValueError):
            pass
    if isinstance(val, (int, float)):
        return val
    s_val = str(val).strip()
    # Only a VALID thousands grouping loses its commas ("1,234.56",  # pii-ok
    # "-2,000,000"). A decimal comma ("12,34") or any other comma shape
    # passes through untouched, so the downstream strict parser refuses
    # it instead of it silently becoming 1234.
    if _GROUPED_NUMBER_RE.match(s_val):
        return s_val.replace(',', '')
    return s_val


class Sheet:
    """One worksheet's cells as text, row by row (read_sheet)."""

    def __init__(self, rows):
        self.rows = rows

    def to_csv(self, path_or_buf=None, index=False, encoding=None):
        """The rows as CSV text (minimal quoting, "\n" line ends, as
        pandas wrote them); written to `path_or_buf` when given."""
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(self.rows)
        if path_or_buf is None:
            return buf.getvalue()
        path_or_buf.write(buf.getvalue())
        return None


def _apply_cells(df, fn):
    """Run `fn` over every cell. `DataFrame.map` is the modern API
    (pandas 2.1+); fall back to the older `applymap` on earlier
    versions so this tool installs cleanly on Python 3.10 environs
    that haven't bumped pandas yet."""
    if isinstance(df, Sheet):
        return Sheet([[fn(c) for c in row] for row in df.rows])
    if hasattr(df, 'map') and callable(getattr(df, 'map')):
        try:
            return df.map(fn)
        except TypeError:
            pass
    return df.applymap(fn)


# A number format of zeros alone ("00000000"): an id shown padded to
# that many digits (backlog TJ-0535).
_ZERO_PAD_FMT = re.compile(r'"?(0+)"?')


def _cell_text(cell) -> str:
    """A cell as the text the sheet shows, as pandas' text read gave it
    (a whole number without ".0", a date as "2025-12-31 00:00:00", a
    text cell as is — "NA" stays NA), and an id whose number format
    pads it with zeros ("00000000") keeps them: the number alone lost
    them (999001 for the 00999001 shown; backlog TJ-0535)."""
    v = cell.value
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return str(v)
        whole = int(v)
        if whole != v:
            return str(float(v))
        text = str(whole)
        m = _ZERO_PAD_FMT.fullmatch(str(cell.number_format or ""))
        if m and whole >= 0 and len(m.group(1)) > len(text):
            text = text.zfill(len(m.group(1)))
        return text
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return str(v)
    return str(v)


def pick_sheet(wb, sheet_name=0):
    """The worksheet `sheet_name` names: a sheet of that exact name
    first (a year tab "2025" is a name, not the index 2025: backlog
    TJ-0536), else a zero-based index; anything else is refused,
    naming the sheets the workbook has."""
    names = list(wb.sheetnames)
    if isinstance(sheet_name, str) and sheet_name in names:
        return wb[sheet_name]
    try:
        idx = int(str(sheet_name).strip())
    except ValueError:
        idx = None
    if idx is not None and 0 <= idx < len(names):
        return wb.worksheets[idx]
    shown = ", ".join(repr(n) for n in names)
    raise ValueError(
        f"no worksheet {sheet_name!r}: the workbook has {len(names)} "
        f"sheet(s), {shown} — give --sheet one of those names, or its "
        f"index from 0 to {len(names) - 1}")


def read_sheet(input_file, sheet_name=0):
    """Read one sheet with EVERY cell as the text it shows (_cell_text):

      * no header inference: the first row is written as it is, so a
        label given twice stays twice for the parser's own layout
        check (pandas renamed the second Quantity to Quantity.1, and
        the converted file read 10 units where the raw one refused the
        mismatch; backlog TJ-0534), and an empty label stays empty;
      * no type inference: an id keeps its leading zeros, a date cell
        comes through as ISO text ("2025-12-31 00:00:00"), which the
        parsers accept (Questrade's own CSV spells it "2025-12-31
        12:00:00 AM"), and "NA", "N/A", "null" stay literal strings
        (the ticker NA, National Bank). Only a truly empty cell is
        empty.

    Trailing empty cells and rows are dropped and every row padded to
    the widest, as pandas wrote them."""
    import openpyxl
    wb = openpyxl.load_workbook(input_file, data_only=True)
    try:
        ws = pick_sheet(wb, sheet_name)
        rows = []
        for row in ws.iter_rows():
            cells = [_cell_text(c) for c in row]
            while cells and cells[-1] == "":
                cells.pop()
            rows.append(cells)
    finally:
        wb.close()
    while rows and not rows[-1]:
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    return Sheet([r + [""] * (width - len(r)) for r in rows])


def convert_xlsx_to_csv(input_file: str, output_file=None, sheet_name=0) -> None:
    """Read `input_file` (one sheet), strip numeric-comma formatting,
    and write CSV to `output_file` or stdout."""
    # Check the input before importing pandas so a missing file reports
    # cleanly (exit 2, environment error) even without the xlsx extras.
    if not os.path.exists(input_file):
        cli_diag.error(PROG, f"no such file: {input_file}")
        sys.exit(2)

    print(f"Reading {input_file!r}...", file=sys.stderr)
    df = read_sheet(input_file, sheet_name)
    df = _apply_cells(df, _clean_numeric_commas)

    if output_file:
        # A generated file: staged beside `-o` and renamed over it, so a
        # symlink planted at the output name is replaced by the CSV and
        # its target is never opened (lib/safe_write; audit 2026-10-24
        # B06 — pandas' own writer followed the link and overwrote what
        # it pointed at). newline="": the text keeps the line endings
        # pandas wrote, as its path writer does.
        from taxjson.lib.safe_write import write_atomic
        try:
            write_atomic(output_file, df.to_csv(index=False), newline="")
        except OSError as e:
            # The file asked for, not the temp staged beside it, and
            # what to do (backlog TJ-0056).
            head, fix = cli_diag.cannot_write(output_file, e,
                                              alt=", or choose another -o")
            cli_diag.error(PROG, head, [fix] if fix else ())
            sys.exit(2)
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
                        help="Sheet name, else zero-based index "
                             "(default: 0, the first sheet). A sheet "
                             "named 2025 is that sheet, not index 2025.")
    args = parser.parse_args()

    # A sheet's own name wins over an index (pick_sheet): `-s 2025`
    # selects the year tab "2025"; `-s 1` the second sheet unless one
    # is named "1".
    try:
        convert_xlsx_to_csv(args.input, args.output, args.sheet)
    except ImportError as e:
        # Missing optional dependency = environment error → exit 2.
        # taxjson's own fix first: the library's own text said "Use pip
        # or conda", which installs outside taxjson's environment
        # (backlog TJ-0425).
        missing = getattr(e, "name", None) or "openpyxl"
        cli_diag.error(
            PROG,
            f"the optional `xlsx` extra this tool needs is not "
            f"installed (no {missing} in taxjson's environment)",
            [f"To add it: {extra_hint('xlsx')}."],
        )
        sys.exit(2)
    except Exception as e:
        cli_diag.error(PROG, str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

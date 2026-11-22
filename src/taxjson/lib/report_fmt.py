"""The report's numbers and tables: the formatters every report and
message shares (`fmt_money` is THE money formatter for report output;
4-decimal precision is reserved for per-share / per-unit / quantity
columns) and the two table renderers, which differ by input shape:

  * `render_table`: structured cells — headers / per-column aligns /
    body / optional foot rows; supports stacked ('\n') headers and drops
    all-empty columns. Prefer this for new code with structured rows
    (lib/out.fit_table fits one to the width).
  * `align_columns` (and report_model.format_report_table over it):
    whitespace-tokenized row strings, the first row the header; à la
    tt/bin/align.pl, for rows that are already space-joined token lines.

Below lib/out, which renders with them, and lib/report_model, which reads
the books' report files: it imports nothing from taxjson but the money
model (step 8 of the architecture plan: lib's import cycle)."""

import re

from taxjson.model import money as M


def fmt_money(x) -> str:
    """Money for report display: thousands separators, exactly 2 decimals,
    '-' for negatives (1234.5 -> '1,234.50'). None-safe (None -> '0.00'),
    like the ten inline `money()` copies it replaced."""
    return f"{M.shown(float(x or 0), 2):,.2f}"


def fmt_qty(x) -> str:
    """Quantity for report columns: thousands separators, up to 4
    decimals, noise zeros trimmed (100.0 -> '100', 0.257 -> '0.257',
    0 -> '0'). The single home for the five inline copies (list,
    wash-sales, harvest, form-export)."""
    return f"{float(x or 0):,.4f}".rstrip("0").rstrip(".") or "0"


def qty_text(q) -> str:
    """Units as a .tt line writes them, whole: `100`, `12.5`,
    `0.123456789`, `1500000` (never `1e+06`, never cut at a decimal
    place: the shortest decimal that reads back as the same number).
    The one formatter for a quantity a person copies into a line: the
    NOTOWN lines (lib/transfer_in), the OPENING cost=unknown line the
    run's closing list and `tjs checklist` suggest (backlog TJ-3212)."""
    from decimal import Decimal
    v = abs(float(q or 0))
    s = format(Decimal(repr(v)), "f")
    if "." in s and len(s.split(".")[1]) > 10:
        # (a sum's binary noise, 0.1 + 0.2: ten places are every
        # quantity a line carries)
        s = f"{v:.10f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_qty6(x) -> str:
    """Holdings-precision quantity: integer counts without a trailing
    `.0`, fractional (crypto) quantities to 6 decimals trimmed of noise
    zeros. Shared by the holdings export and diff."""
    q = float(x or 0)
    if abs(q - round(q)) < 1e-9:
        return f"{int(round(q))}"
    return f"{q:.6f}".rstrip('0').rstrip('.')


# A table cell that IS a number: money ('1,234.56', '-9,805.50'),  # pii-ok
# bare ints, percentages, or the '-' placeholder. Signs and thousands
# separators included; anything else (tickers, dates, marks) is text.
_NUMERIC_CELL_RE = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?%?$|^-$")
# The cells auto_aligns() right-aligns: the above, a `$` amount and the
# `—` placeholder too (the house tables, docs/output-style.md), and an
# amount marked `+?`, the known part of a cost (backlog TJ-0774, TJ-1262).
_NUMERIC_AUTO_RE = re.compile(r"^[+-]?\$?\d[\d,]*(\.\d+)?(%|\+\?)?$|^[-—]$")


def auto_aligns(headers, body) -> "list[str]":
    """render_table aligns for `body`: '>' for a column whose every
    non-empty cell is a number (money, count, percentage, a '-' or '—'
    placeholder), '<' otherwise."""
    aligns = []
    for i in range(len(headers)):
        cells = [str(r[i]) for r in body if i < len(r) and str(r[i])]
        numeric = bool(cells) and all(_NUMERIC_AUTO_RE.match(c)
                                      for c in cells)
        aligns.append(">" if numeric else "<")
    return aligns


def _tokenize(lines: "list[str]", header: bool = True
              ) -> "list[list[str]]":
    """Whitespace-split row strings; with a header row, a row with MORE
    tokens than it gets its leading extras merged into the first cell
    (see align_columns)."""
    rows = [ln.split() for ln in lines]
    if header and rows and rows[0]:
        ncols = len(rows[0])
        for r in rows:
            if len(r) > ncols:
                extra = len(r) - ncols + 1
                r[:extra] = [" ".join(r[:extra])]
    return rows


def align_columns(lines: "list[str]", padding: str = " ",
                  numeric_right: bool = False, header: bool = True
                  ) -> "list[str]":
    """Align whitespace-separated columns (à la tt/bin/align.pl): each
    positional token is left-justified to the widest value in its column and
    joined with a single-space gap (trailing pad on the last column is
    trimmed). With `numeric_right`, all-numeric columns right-align
    instead, lining up the decimal places (the report tables' mode).

    Rows with MORE tokens than the header get their leading extras merged
    into the first cell: builders join cells with spaces, so an account
    or ticker containing a space ([accounts."rrsp x"] is legal TOML)
    used to shift every subsequent column of that row under the wrong
    header (REVIEW #15). Merging left matches where free-text cells
    live (first column) in every table this renders.

    `header=False`: the lines are positional records with no header
    (the row views' taxtext, where a dividend row with withholding
    carries one more field, its net amount): nothing is merged — the
    first line being shorter than a later one merged that line's
    account and action into one cell and shifted its every column —
    and the numeric columns are read over every line."""
    rows = _tokenize(lines, header)
    widths: "list[int]" = []
    for r in rows:
        for i, f in enumerate(r):
            if i == len(widths):
                widths.append(len(f))
            else:
                widths[i] = max(widths[i], len(f))
    right: "set[int]" = set()
    if numeric_right and len(rows) > 1:
        # A column whose every BODY cell is a number (money, count,
        # percentage — '-' placeholders tolerated) right-aligns, so
        # decimal points and cents line up down the column. The header
        # right-aligns with it. Detection is per-table: a column with
        # any free-text cell stays left.
        body = rows[1:] if header else rows
        for i in range(len(widths)):
            cells = [r[i] for r in body if i < len(r)]
            if cells and all(_NUMERIC_CELL_RE.match(c) for c in cells):
                right.add(i)
    return [padding.join(
        f"{f:>{widths[i]}}" if i in right else f"{f:<{widths[i]}}"
        for i, f in enumerate(r)).rstrip()
            for r in rows]


def render_table(headers, aligns, body, foot=(), gap="  "):
    """Render a column table sized to the data. A header may contain '\\n' to
    stack lines; an all-empty column is dropped. `aligns` None: numeric
    columns right, the rest left (auto_aligns). Returns the list of lines.
    (Not width-aware: lib/out.fit_table fits one to the width.)"""
    ncol = len(headers)
    if aligns is None:
        aligns = auto_aligns(headers, [list(r) for r in body] + list(foot))
    aligns = list(aligns) + [">"] * (ncol - len(aligns))

    def pad(row):
        row = list(row)
        if len(row) < ncol:
            row += [""] * (ncol - len(row))
        return row[:ncol]

    body = [pad(r) for r in body]
    foot = [pad(r) for r in foot]
    allrows = body + foot
    keep = [i for i in range(ncol) if any(r[i] for r in allrows)]
    hlines = [str(h).split("\n") for h in headers]
    nhdr = max((len(h) for h in hlines), default=1)
    hlines = [h + [""] * (nhdr - len(h)) for h in hlines]
    width = {i: max([len(line) for line in hlines[i]]
                    + [len(r[i]) for r in allrows]) for i in keep}

    def fmt(row):
        return gap.join(f"{row[i]:{aligns[i]}{width[i]}}" for i in keep)

    lines = [fmt([hlines[i][k] for i in range(ncol)]) for k in range(nhdr)]
    rule = "-" * len(lines[0])
    lines.append(rule)
    lines += [fmt(r) for r in body]
    if foot:
        lines.append(rule)
        lines += [fmt(r) for r in foot]
    return lines

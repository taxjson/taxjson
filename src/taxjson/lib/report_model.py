"""Shared report-layer primitives.

Every report tool used to carry its own copy of the same two things:

  * a comment-tolerant JSON loader (gains/base JSON files may contain
    '#'-prefixed comment lines, e.g. from --full-traces) — ~10 copies;
  * a fixed-width table renderer — 7 independent implementations.

Each copy was a drift point: a fix landing in one tool and not the others
is exactly the bug class the 2026-07 audits kept finding. This module is
the single home.

Two table renderers live here, with different semantics — pick by input
shape:

  * `render_table`: structured cells
    — headers / per-column aligns / body / optional foot rows; supports
    stacked ('\n') headers and drops all-empty columns. Prefer this for
    NEW code that has structured row data.
  * `format_report_table` + `align_columns` (promoted from
    taxjson_run.py): whitespace-tokenized row STRINGS, first row is the
    header; à la tt/bin/align.pl. Use when the rows are already
    space-joined token lines (the `taxjson` report views).

`fmt_money` is THE money formatter for report output; 4-decimal precision
is reserved for per-share / per-unit / quantity columns.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional


def fmt_money(x) -> str:
    """Money for report display: thousands separators, exactly 2 decimals,
    '-' for negatives (1234.5 -> '1,234.50'). None-safe (None -> '0.00'),
    like the ten inline `money()` copies it replaced."""
    return f"{float(x or 0):,.2f}"


def fmt_qty(x) -> str:
    """Quantity for report columns: thousands separators, up to 4
    decimals, noise zeros trimmed (100.0 -> '100', 0.257 -> '0.257',
    0 -> '0'). The single home for the five inline copies (list,
    wash-sales, harvest, form-export)."""
    return f"{float(x or 0):,.4f}".rstrip("0").rstrip(".") or "0"


def fmt_qty6(x) -> str:
    """Holdings-precision quantity: integer counts without a trailing
    `.0`, fractional (crypto) quantities to 6 decimals trimmed of noise
    zeros. Shared by the holdings export and diff."""
    q = float(x or 0)
    if abs(q - round(q)) < 1e-9:
        return f"{int(round(q))}"
    return f"{q:.6f}".rstrip('0').rstrip('.')


# A table cell that IS a number: money ('1,234.56', '-9,805.50'),
# bare ints, percentages, or the '-' placeholder. Signs and thousands
# separators included; anything else (tickers, dates, marks) is text.
_NUMERIC_CELL_RE = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?%?$|^-$")


def align_columns(lines: "list[str]", padding: str = " ",
                  numeric_right: bool = False) -> "list[str]":
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
    live (first column) in every table this renders."""
    rows = [ln.split() for ln in lines]
    if rows and rows[0]:
        ncols = len(rows[0])
        for r in rows:
            if len(r) > ncols:
                extra = len(r) - ncols + 1
                r[:extra] = [" ".join(r[:extra])]
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
        for i in range(len(widths)):
            cells = [r[i] for r in rows[1:] if i < len(r)]
            if cells and all(_NUMERIC_CELL_RE.match(c) for c in cells):
                right.add(i)
    return [padding.join(
        f"{f:>{widths[i]}}" if i in right else f"{f:<{widths[i]}}"
        for i, f in enumerate(r)).rstrip()
            for r in rows]


def format_report_table(rows: "list[str]", padding: str = "   ",
                        rule_before_last: bool = False) -> "list[str]":
    """Aligned table lines (first row is the header) with a wider column
    gap and a `---` underline beneath the header — for the readable report
    views, as opposed to the compact taxtext logs. `rule_before_last` also
    rules off the final row (e.g. a TOTAL). Returns the list of lines."""
    aligned = align_columns(rows, padding=padding, numeric_right=True)
    if not aligned:
        return []
    width = max(len(ln) for ln in aligned)
    out = [aligned[0], "-" * width]
    body = aligned[1:]
    for i, line in enumerate(body):
        if rule_before_last and i == len(body) - 1:
            out.append("-" * width)
        out.append(line)
    return out


def strip_report_comments(lines: Iterable[str]) -> str:
    """Drop '#'-prefixed lines (leading whitespace tolerated) and rejoin.
    `lines` is any iterable of newline-terminated strings (an open file,
    sys.stdin, text.splitlines(keepends=True))."""
    return "".join(line for line in lines
                   if not line.lstrip().startswith('#'))


def parse_report_json(text: str) -> Any:
    """json.loads over comment-stripped text."""
    return json.loads(strip_report_comments(text.splitlines(keepends=True)))


def load_report_json(path: Optional[Path] = None) -> Any:
    """THE report-layer JSON loader: read `path` (or sys.stdin when None),
    strip '#' comment lines, parse. Raises OSError / json.JSONDecodeError
    exactly like the inline copies it replaces — callers keep their own
    error policy."""
    if path is not None:
        with open(path, 'r', encoding='utf-8') as f:
            return json.loads(strip_report_comments(f))
    return json.loads(strip_report_comments(sys.stdin))


def render_table(headers, aligns, body, foot=(), gap="  "):
    """Render a column table sized to the data. A header may contain '\\n' to
    stack lines; an all-empty column is dropped. Returns the list of lines."""
    ncol = len(headers)
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


def resolve_gains_files(cache, account: Optional[str] = None, *,
                        prefer_wash: bool = True) -> "dict[str, Path]":
    """THE canonical discovery of per-account gains files under work/.

    The 2026-07b audit found six hand-copied variants of this walk in
    taxjson_run.py running TWO different policies (`taxjson sum`/`list`
    read pre-wash files while carryover/t1135/wash-sales preferred the
    wash-adjusted ones), so totals disagreed across commands whenever a
    cross-account wash disallowance fired. Every consumer goes through
    here now.

    Returns {account: path}, sorted by account. With prefer_wash (the
    default — the wash-adjusted numbers are what a return reports), an
    account's cross-account `<acct>_gains_wash.json` wins over its
    `<acct>_gains.json` when both exist. With prefer_wash=False the
    plain file is required — pre-wash means pre-wash, no fallback.
    Raw/native derivatives (_raw_gains, _raw_base_gains) are never
    returned. Accounts with no qualifying file are omitted; the caller
    owns the empty-result policy.
    """
    cache = Path(cache)
    if account is not None:
        names = [account]
    else:
        found = set()
        for pat, suf in (("*_gains.json", "_gains.json"),
                         ("*_gains_wash.json", "_gains_wash.json")):
            for f in cache.glob(pat):
                if f.name.endswith(("_raw_gains.json",
                                    "_raw_base_gains.json")):
                    continue
                if f.name.startswith("."):
                    # Hidden pipeline intermediates (the blended pass's
                    # .blend_* files) — pathlib.glob's `*` DOES match a
                    # leading dot, so without this they'd surface as a
                    # phantom account and double every aggregate.
                    continue
                found.add(f.name[: -len(suf)])
        names = sorted(found)
    out: "dict[str, Path]" = {}
    import sys as _sys
    for name in names:
        wash = cache / f"{name}_gains_wash.json"
        main = cache / f"{name}_gains.json"
        if prefer_wash and wash.exists():
            try:
                if main.exists() and (main.stat().st_mtime
                                      > wash.stat().st_mtime + 1.0):
                    # `run --account X` rebuilds the plain gains but
                    # skips the blended wash pass — the preferred wash
                    # file is then STALE, and form-export/t1135/
                    # reconcile-slips served it silently (2026-09
                    # audit). Loud, not fatal: partial runs are a
                    # legitimate iteration loop.
                    print(f"warning: {wash.name} is OLDER than "
                          f"{main.name} — wash-adjusted numbers are "
                          f"stale (run a full `taxjson run` before "
                          f"filing from this output).",
                          file=_sys.stderr)
            except OSError:
                pass
            out[name] = wash
        elif main.exists():
            out[name] = main
    return out


def gains_basis_label(files) -> str:
    """Human label for which basis a report was computed on, from the
    paths resolve_gains_files returned. Printed by the query commands so
    a user comparing two reports can see WHY their totals differ."""
    paths = files.values() if hasattr(files, "values") else files
    if any(Path(p).name.endswith("_gains_wash.json") for p in paths):
        return "wash-adjusted"
    return "pre-wash"


def build_account_report(gains_data, account: str,
                         basis: Optional[str] = None,
                         base_transactions=None) -> dict:
    """The structured dict work/<account>_report.json carries — the machine
    twin of the text reports, written by `taxjson run` after the .sum stage.

    Schema (schema_version 1):
      account         str — account name
      generated_at    ISO timestamp
      year            the gains run's tax year (or 'all')
      gains           taxjson_sum_gains.summarize_gains(gains_data) output:
                      {ticker_stats, returns_by_asset, total_fees,
                       option_fees, target_year, ...}
      income          taxjson_sum_income.summarize_income(transactions, year)
      wash            {count, total_disallowed} from the gains run's
                      wash_sales records

    ADDITIVE artifact: the .sum text pipeline is unchanged; consumers
    (taxjson summary today; future exports/web) read this instead of
    re-deriving the same aggregates from the raw gains JSON."""
    from datetime import datetime
    # Deferred imports: bin tools import report_model, so a module-level
    # import here would be circular.
    from taxjson.bin.taxjson_sum_gains import summarize_gains
    from taxjson.bin.taxjson_sum_income import summarize_income

    year = (gains_data.get("summary") or {}).get("year")
    washes = gains_data.get("wash_sales") or []

    def _amt(w):
        return float(w.get("amount") or (w.get("loss_tx") or {}).get("amount", 0) or 0)

    return {
        "schema_version": 1,
        "account": account,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # Which gains basis the aggregates were computed from
        # ("pre-wash" | "wash-adjusted") — `taxjson sum`'s fast path
        # serves this file only when it matches the basis it resolved,
        # since after `run --account X` the report is freshly pre-wash
        # while the stale wash file still wins resolve_gains_files.
        "basis": basis,
        "year": year,
        "gains": summarize_gains(gains_data),
        # Income comes from the BASE book: the gains file's income
        # entries carry `dividend`/`pil` fields (not gross/net_amount)
        # and no TAX/INTEREST rows at all, so summarizing them here
        # produced an all-zero income section — the machine twin
        # disagreed with its own text twin (2026-09 audit).
        "income": summarize_income(
            (base_transactions if base_transactions is not None
             else gains_data.get("transactions", [])),
            target_year=year),
        "wash": {"count": len(washes),
                 "total_disallowed": round(sum(_amt(w) for w in washes), 2)},
    }

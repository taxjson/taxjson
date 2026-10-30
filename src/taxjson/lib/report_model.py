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


def format_report_table(rows: "list[str]", padding: str = "   ",
                        rule_before_last: bool = False, *,
                        fit: bool = False, drop=(), key=0,
                        width_: "Optional[int]" = None) -> "list[str]":
    """Aligned table lines (first row is the header) with a wider column
    gap and a `---` underline beneath the header — for the readable report
    views, as opposed to the compact taxtext logs. `rule_before_last` also
    rules off the final row (e.g. a TOTAL). Returns the list of lines.

    `fit=True`: the house table (docs/output-style.md) — lib/out.fit_table
    over the same cells: two-space gaps, numeric columns right-aligned,
    and when wider than the width the `drop` columns go first (indexes,
    least important first), then one record per row headed by the `key`
    column(s). Opt in per table, after checking no program reads its rows
    (the doc lists the ones that are read)."""
    if fit:
        from taxjson.lib.out import fit_table
        cells = _tokenize(rows)
        if not cells:
            return []
        body = cells[1:]
        foot = []
        if rule_before_last and len(body) > 1:
            body, foot = body[:-1], body[-1:]
        return fit_table(cells[0], body, foot=foot, drop=drop, key=key,
                         width_=width_)
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


def _refuse_non_finite(token: str):
    # json.loads reads the non-standard NaN / Infinity tokens as floats;
    # a report built on one printed NaN figures at exit 0 (issue #8).
    raise ValueError(f"holds a non-finite number ({token})")


def parse_report_json(text: str) -> Any:
    """json.loads over comment-stripped text (NaN / Infinity refused)."""
    return json.loads(strip_report_comments(text.splitlines(keepends=True)),
                      parse_constant=_refuse_non_finite)


def load_report_json(path: Optional[Path] = None) -> Any:
    """THE report-layer JSON loader: read `path` (or sys.stdin when None),
    strip '#' comment lines, parse. Raises OSError / json.JSONDecodeError
    exactly like the inline copies it replaces — callers keep their own
    error policy — and a ValueError for a NaN / Infinity token."""
    if path is not None:
        # utf-8-sig: a BOM is dropped (re-audit A2-1412).
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.loads(strip_report_comments(f),
                              parse_constant=_refuse_non_finite)
    # UTF-8 whatever the locale, as files are read (A2-1219).
    import io
    from taxjson.lib.cli_diag import read_stdin_utf8
    return json.loads(strip_report_comments(io.StringIO(read_stdin_utf8())),
                      parse_constant=_refuse_non_finite)


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


def grant_write_closes(transactions, inventory, year=None,
                       splits=()) -> "dict[int, float]":
    """{index in `transactions`: contracts closed} for the grant-timing
    WRITE records (``grant``) that are the only record of their close
    (grant_write_expiries, without the expiry dates)."""
    return {i: q for i, (q, _e) in grant_write_expiries(
        transactions, inventory, year, splits).items()}


def grant_write_expiries(transactions, inventory, year=None,
                         splits=()) -> "dict[int, tuple]":
    """{index in `transactions`: (contracts closed, the date they
    expired)} for the grant-timing WRITE records (``grant``) that are
    the only record of their close.

    Under grant timing (ITA s.49(1)) the premium is recognised at the
    WRITE; a buy-back adds its own closing record, but an expiry adds
    none. The views count closing records only, so an expired write
    showed 0 closes / 0 contracts while close timing showed 1 / 5
    (audit A2-0693, regression of S024-01 / S028-07).

    Per series, in date order: each WRITE queues its contracts, each
    non-grant SHORT record of the same symbol consumes from the queue
    (FIFO; one with nothing queued closes a write from an earlier year).
    `splits`, the book's SPLIT rows of the option series (date, symbol,
    symbol_new, quantity = the ratio; one event once): a split or
    rename carries the series' queued contracts into the new series,
    re-denominated (a write renamed, then bought back in the new
    series, was left queued under the old one and read as expired:
    backlog TJ-0903). What is left, less the contracts still written at
    the end of the data (``inventory``, a negative quantity), expired
    when the series' expiry date has passed by the end of ``year`` (or
    today, if earlier): it is attributed to the earliest remaining
    writes, in the series' units at the expiry, with that expiry date
    (a view's window takes the close on it, not on the write's date:
    backlog TJ-0778). A series expiring later is open at the year end,
    or closed in a later year's books, and is not counted."""
    from datetime import date as _date
    from taxjson.lib.core import parse_option_expiry
    bound = _date.today().isoformat()
    if year:
        bound = min(bound, f"{year}-12-31")
    txs = list(transactions or ())
    # (date, rank, order, kind, payload): on a split's day the writes
    # come first, then the closes of the series it renames away, the
    # split, the other closes.
    split_rows = []
    for s in splits or ():
        if not isinstance(s, dict) or not s.get("symbol"):
            continue
        try:
            r = float(s.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        old = str(s["symbol"])
        new = str(s.get("symbol_new") or "").strip() or old
        split_rows.append((str(s.get("date") or "")[:10], old, new,
                           r if r > 0 else 1.0))
    renamed_away = {(d, old) for d, old, _n, _r in split_rows}
    events = []
    has_grant = False
    for i, t in enumerate(txs):
        if not isinstance(t, dict) or not t.get("symbol"):
            continue
        if not (t.get("grant") or t.get("direction") == "SHORT"):
            continue
        d = str(t.get("date") or "")
        sym = str(t["symbol"])
        if t.get("grant"):
            has_grant = True
            rank = 0
        else:
            rank = 1 if (d[:10], sym) in renamed_away else 3
        events.append((d, rank, i, "row", i))
    if not has_grant:
        return {}
    for k, s in enumerate(split_rows):
        events.append((s[0], 2, k, "split", s))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    queues: "dict[str, list]" = {}           # symbol: [[index, remaining]]
    eps = 1e-9
    for _d, _rank, _o, kind, payload in events:
        if kind == "split":
            _sd, old, new, ratio = payload
            moved = queues.pop(old, [])
            if not moved:
                continue
            for ent in moved:
                ent[1] *= ratio
            dest = queues.setdefault(new, [])
            dest.extend(moved)
            dest.sort(key=lambda ent: (str(txs[ent[0]].get("date") or ""),
                                       ent[0]))
            continue
        i = payload
        t = txs[i]
        try:
            q = abs(float(t.get("qty") or 0.0))
        except (TypeError, ValueError):
            continue
        sym = str(t["symbol"])
        if t.get("grant"):
            queues.setdefault(sym, []).append([i, q])
            continue
        queue = queues.get(sym) or []
        while q > eps and queue:
            take = min(q, queue[0][1])
            queue[0][1] -= take
            q -= take
            if queue[0][1] <= eps:
                queue.pop(0)
    open_short: "dict[str, float]" = {}
    for r in inventory or ():
        try:
            q = float(r.get("qty") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        if q < 0:
            sym = str(r.get("symbol"))
            open_short[sym] = open_short.get(sym, 0.0) - q
    out: "dict[int, tuple]" = {}
    for sym, queue in queues.items():
        exp = parse_option_expiry(sym)
        if exp is None or exp > bound:
            continue
        # Contracts still written at the end are the LATEST writes.
        still = open_short.get(sym, 0.0)
        for ent in reversed(queue):
            take = min(still, ent[1])
            ent[1] -= take
            still -= take
        for i, left in queue:
            if left > eps:
                q0, _e = out.get(i, (0.0, exp))
                out[i] = (q0 + left, exp)
    return out


def resolve_gains_files(cache, account: Optional[str] = None, *,
                        prefer_wash: bool = True,
                        warn_stale: bool = True) -> "dict[str, Path]":
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
    owns the empty-result policy. `warn_stale=False`: no word about a
    stale wash file (a full run snapshotting the books it is about to
    rebuild, backlog TJ-0154).
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
            # `run --account X` rebuilds X's books but skips the
            # cross-account wash pass — the preferred wash file is then
            # STALE, and sum/form-export/t1135/reconcile-slips served it
            # silently (2026-09 audit). Loud, not fatal: partial runs
            # are a legitimate iteration loop.
            newer = stale_wash_inputs(wash) if warn_stale else []
            if newer and (wash.name, tuple(newer)) in _STALE_SAID:
                newer = []          # said once per process (harvest and
                                    # checklist read the books twice)
            if newer:
                _STALE_SAID.add((wash.name, tuple(newer)))
                from taxjson.lib.stage_msg import emit_line
                emit_line(f"warning: {wash.name} was built before "
                          f"{', '.join(newer)} changed — wash-adjusted "
                          f"numbers are "
                          f"stale (run a full `taxjson run` before "
                          f"filing from this output).",
                          file=_sys.stderr)
            out[name] = wash
        elif main.exists():
            out[name] = main
    return out


# The blended (cross-account) passes' record of what each wash file was
# built from: {"wash": {<acct>_gains_wash.json: {"blend", "wash": fp,
# "inputs": {name: fp | None}}}} with fp = {size, mtime_ns, sha256}.
# Dot-prefixed: no account discovery glob may see it.
WASH_INPUTS_FILE = ".wash_inputs.json"


def _sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# The stale-wash warnings already given in this process.
_STALE_SAID: set = set()


def _fingerprint(p: Path) -> "Optional[dict]":
    """size + mtime_ns + sha256 of a file; None when it does not exist."""
    try:
        st = p.stat()
        return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                "sha256": _sha256(p)}
    except OSError:
        return None


def _matches(fp: Any, p: Path) -> bool:
    """Is `p` still the file `fp` fingerprinted (None: still absent)?
    Size and mtime equal is a match; a file rewritten with the same
    bytes (a --fast no-op) matches by its hash."""
    try:
        st = p.stat()
    except OSError:
        return fp is None
    if not isinstance(fp, dict):
        return False
    if st.st_size != fp.get("size"):
        return False
    if st.st_mtime_ns == fp.get("mtime_ns"):
        return True
    try:
        return _sha256(p) == fp.get("sha256")
    except OSError:
        return False


def _read_wash_inputs(cache: Path) -> dict:
    try:
        doc = json.loads((Path(cache) / WASH_INPUTS_FILE)
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    w = doc.get("wash") if isinstance(doc, dict) else None
    return w if isinstance(w, dict) else {}


def wash_blend(cache: Path, account: str
               ) -> "Optional[tuple[str, list[str]]]":
    """(tag, members) of the run's cross-account pass that wrote
    `account`'s `<acct>_gains_wash.json` (stage_blended_wash_pass: tag
    "blend" for the taxable equity accounts, "cryptoblend" for the
    crypto ones), from the record that pass kept (record_wash_inputs):
    every account whose base and plain gains it read. None when no
    record names such a pass of two or more accounts that includes
    `account` (a single account, a wash file of an older release)."""
    rec = _read_wash_inputs(Path(cache)).get(f"{account}_gains_wash.json")
    if not isinstance(rec, dict) or rec.get("blend") not in (
            "blend", "cryptoblend"):
        return None
    inputs = rec.get("inputs")
    if not isinstance(inputs, dict):
        return None
    # Each member's base AND plain gains: the combined sheltered book
    # (sheltered_base.json) has no gains beside it.
    members = sorted(k[: -len("_base.json")] for k in inputs
                     if k.endswith("_base.json")
                     and f"{k[: -len('_base.json')]}_gains.json" in inputs)
    if account not in members or len(members) < 2:
        return None
    return str(rec["blend"]), members


def _book_accounts(cache: Path) -> "list[str]":
    """The accounts with a plain gains book in `cache`, as
    resolve_gains_files discovers them: `<acct>_gains.json`, never a
    dot-prefixed intermediate or a raw derivative."""
    return sorted(
        f.name[: -len("_gains.json")]
        for f in Path(cache).glob("*_gains.json")
        if not f.name.startswith(".")
        and not f.name.endswith(("_raw_gains.json",
                                 "_raw_base_gains.json")))


def record_wash_inputs(cache: Path, washes: Iterable[Path],
                       inputs: Iterable[Path], *, blend: str) -> None:
    """After a wash pass wrote `washes` from `inputs` (every member's
    base and plain gains, the sheltered book, the loss-override state —
    a missing one recorded as absent), remember what each was built
    from, for stale_wash_inputs: a blended result depends on EVERY
    account in the blend, and `run --account B` rebuilds B without the
    blended pass (GitHub issue #15). `books` is the roster of accounts
    whose books were there when the pass ran (members and not): one
    that appears later (a new account built by `run --account <new>`)
    was not in the blend and makes it stale (2026-10-24 audit D01).
    Written by the run (under its run lock), owner-only, never through
    a link (safe_write)."""
    from taxjson.lib.safe_write import write_atomic
    cache = Path(cache)
    fps = {Path(p).name: _fingerprint(Path(p)) for p in inputs}
    books = _book_accounts(cache)
    entries = _read_wash_inputs(cache)
    for w in washes:
        w = Path(w)
        entries[w.name] = {"blend": blend, "wash": _fingerprint(w),
                           "inputs": fps, "books": books}
    write_atomic(cache / WASH_INPUTS_FILE,
                 json.dumps({"schema_version": 1, "wash": entries},
                            indent=2, sort_keys=True) + "\n")


def stale_wash_inputs(wash: Path) -> "list[str]":
    """The inputs of a `<acct>_gains_wash.json` rebuilt AFTER it — its
    account's plain gains and base book, and the combined sheltered book
    (`run --account <sheltered>` rebuilds sheltered_base.json but skips
    the wash pass, so a registered-account buy that makes a taxable loss
    superficial never reached the served numbers; 2026-09 audit R1-251)
    — and every input of the blend it came from that changed since
    (another taxable account's books after `run --account <other>`:
    GitHub issue #15), from the record the wash pass kept
    (WASH_INPUTS_FILE) — by that record's fingerprints, the account's
    own inputs included, so a rebuild with the same bytes is current.
    A blended result should have had EVERY account in it: an account
    whose books were not there when the pass ran (not in the record's
    `books` roster — a new account, built by `run --account <new>`)
    makes it stale when its base or plain gains are newer (2026-10-24
    audit D01; the roster tells it from a sheltered account rebuilt
    unchanged, which a record without a roster — an older release's —
    reports too, until the next full run). A single-account pass
    (`blend` is the account's own name) depends on no other account. A
    wash file with no record (an older taxjson wrote it) counts its own
    inputs and any other account's base or plain gains newer than
    it (accounts as resolve_gains_files finds them). Empty when the wash file is current. One second of slack
    absorbs filesystem timestamp granularity within one run."""
    wash = Path(wash)
    name = wash.name[: -len("_gains_wash.json")]
    try:
        w = wash.stat().st_mtime
    except OSError:
        return []
    rec = _read_wash_inputs(wash.parent).get(wash.name)
    recorded = (rec["inputs"] if isinstance(rec, dict)
                and isinstance(rec.get("inputs"), dict)
                and _matches(rec.get("wash"), wash) else None)
    out = []
    for p in (wash.with_name(f"{name}_gains.json"),
              wash.with_name(f"{name}_base.json"),
              wash.with_name("sheltered_base.json")):
        if recorded is not None and p.name in recorded:
            # The record's fingerprint judges the account's OWN inputs
            # too: a no-op `run --account <this>` rewrites them with the
            # same bytes (newer, not changed) — no warning for that.
            if not _matches(recorded[p.name], p):
                out.append(p.name)
            continue
        try:
            if p.stat().st_mtime > w + 1.0:
                out.append(p.name)
        except OSError:
            continue
    if recorded is not None:
        for inp, fp in sorted(recorded.items()):
            if inp not in out and not _matches(fp, wash.with_name(inp)):
                out.append(inp)
        if rec.get("blend") != name:
            # A blended pass: an account the roster lacks is new to the
            # project since the pass, and its books newer than the wash
            # file never went through it (audit D01).
            roster = rec.get("books")
            if isinstance(roster, list) and roster:
                known = {str(b) for b in roster}
            else:
                known = {inp[: -len(suf)] for inp in recorded
                         for suf in ("_gains.json", "_base.json")
                         if inp.endswith(suf)}
            for other in _book_accounts(wash.parent):
                if other == name or other in known:
                    continue
                for p in (wash.with_name(f"{other}_base.json"),
                          wash.with_name(f"{other}_gains.json")):
                    try:
                        if p.name not in out and p.stat().st_mtime > w + 1.0:
                            out.append(p.name)
                    except OSError:
                        continue
        return sorted(out)
    others = sorted(set(_book_accounts(wash.parent)) - {name})
    for other in others:
        for p in (wash.with_name(f"{other}_base.json"),
                  wash.with_name(f"{other}_gains.json")):
            try:
                if p.name not in out and p.stat().st_mtime > w + 1.0:
                    out.append(p.name)
            except OSError:
                continue
    return out


def gains_basis_label(files) -> str:
    """Human label for which basis a report was computed on, from the
    paths resolve_gains_files returned. Printed by the query commands so
    a user comparing two reports can see WHY their totals differ."""
    paths = list(files.values() if hasattr(files, "values") else files)
    if any(Path(p).name.endswith("_gains_all.json") for p in paths):
        # The full-history file a windowed view reads for a window
        # outside the tax year (taxjson_run _gains_all_file): the
        # engine's denials are in it, across the taxable accounts the
        # run blends (TJ-0677); a registered account's purchase is
        # checked for the tax year only. "pre-wash" read as no
        # superficial-loss rule over a DENIED column that showed one
        # (backlog TJ-0452).
        return "denials among taxable accounts, full history"
    if any(Path(p).name.endswith("_gains_wash.json") for p in paths):
        return "wash-adjusted"
    return "pre-wash"


def build_account_report(gains_data, account: str,
                         basis: Optional[str] = None,
                         base_transactions=None, rules=None) -> dict:
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
    (`taxjson sum` today; future exports) read this instead of
    re-deriving the same aggregates from the raw gains JSON."""
    from datetime import datetime
    # Deferred imports: bin tools import report_model, so a module-level
    # import here would be circular.
    from taxjson.bin.taxjson_sum_gains import summarize_gains
    from taxjson.bin.taxjson_sum_income import summarize_income

    year = (gains_data.get("summary") or {}).get("year")
    washes = gains_data.get("wash_sales") or []

    def _amt(w):
        # 'disallowed_amount' is the field BOTH engines emit on a wash
        # record; the US engine emits nothing else, so reading only
        # 'amount' reported total_disallowed 0 for every US project
        # (audit S077-14). The old keys stay as a fallback.
        v = w.get("disallowed_amount")
        if v is None:
            v = (w.get("amount")
                 or (w.get("loss_tx") or {}).get("amount", 0))
        return float(v or 0)

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
            target_year=year, rules=rules),
        "wash": {"count": len(washes),
                 "total_disallowed": round(sum(_amt(w) for w in washes), 2)},
    }

"""Shared report-layer primitives: the books' report files and the
report views' tables.

Every report tool used to carry its own copy of a comment-tolerant JSON
loader (gains/base JSON files may contain '#'-prefixed comment lines,
e.g. from --full-traces) — ~10 copies — and of a fixed-width table
renderer — 7 independent implementations. Each copy was a drift point:
a fix landing in one tool and not the others is exactly the bug class
the 2026-07 audits kept finding. This module is the single home of the
loader, of which gains file a view reads (`resolve_gains_files`, the
wash inputs) and of `format_report_table`, the report views' table over
whitespace-tokenized row strings.

The numbers and the structured-cell table (`fmt_money`, `fmt_qty`,
`render_table`, `align_columns`) are lib/report_fmt's, below lib/out;
the grant-timing writes' closes lib/grant_writes'; the account report's
JSON lib/account_report's (step 8 of the architecture plan: lib's import
cycle).
"""

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from taxjson.lib.once import said_set
from taxjson.lib.out import fit_table
from taxjson.lib.report_fmt import _tokenize, align_columns


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


def resolve_gains_files(cache, account: Optional[str] = None, *,
                        prefer_wash: bool = True,
                        warn_stale: bool = True,
                        accounts: Optional[Iterable[str]] = None) -> "dict[str, Path]":
    """THE canonical discovery of per-account gains files under work/.

    The 2026-07b audit found six hand-copied variants of this walk in
    the commands running TWO different policies (`tjs sum`/`list`
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
    rebuild, backlog TJ-0154). `accounts`: these accounts' only (the
    configured ones: work/ may still hold the books of an account
    taxjson.toml no longer names, which a full run sweeps after its
    checks across accounts, backlog TJ-4301).
    """
    cache = Path(cache)
    if account is not None:
        names = [account]
    elif accounts is not None:
        names = sorted(set(accounts))
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
            # Said once in a chain (harvest and checklist read the books
            # twice), for each project's file (`tjs -C a sum -- -C b sum`).
            said = (str(wash.resolve()), tuple(newer))
            if newer and said in _STALE_SAID:
                newer = []
            if newer:
                _STALE_SAID.add(said)
                from taxjson.lib.stage_msg import emit_line
                emit_line(f"warning: {wash.name} was built before "
                          f"{', '.join(newer)} changed — wash-adjusted "
                          f"numbers are "
                          f"stale (run a full `tjs run` before "
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
# A wash pass's own record (what it read) is the `wash` field of its wash
# file's record (lib/records; work/.wash_inputs.json until TJ-1804).
WASH_FIELD = "wash"


def _sha256(p: Path) -> str:
    from taxjson.lib.records import sha256_file
    digest = sha256_file(p)
    if digest is None:
        raise OSError(f"{p}: cannot be read")
    return digest


# The stale-wash warnings already given (lib/once).
_STALE_SAID = said_set()


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
    bytes (a run that changed nothing) matches by its hash."""
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


def _wash_record(wash: Path) -> "Optional[dict]":
    """What the pass that wrote `wash` read (record_wash_inputs), from the
    wash file's record; None when there is none (an older taxjson's)."""
    from taxjson.lib.records import read_record
    w = (read_record(Path(wash)) or {}).get(WASH_FIELD)
    return w if isinstance(w, dict) else None


def wash_blend(cache: Path, account: str
               ) -> "Optional[tuple[str, list[str]]]":
    """(tag, members) of the run's cross-account pass that wrote
    `account`'s `<acct>_gains_wash.json` (stage_blended_wash_pass: tag
    "blend" for the taxable equity accounts, "cryptoblend" for the
    crypto ones), from the record that pass kept (record_wash_inputs):
    every account whose base and plain gains it read. None when no
    record names such a pass of two or more accounts that includes
    `account` (a single account, a wash file of an older release)."""
    rec = _wash_record(Path(cache) / f"{account}_gains_wash.json")
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
                       inputs: Iterable[Path], *, blend: str,
                       configured: Optional[Iterable[str]] = None) -> None:
    """After a wash pass wrote `washes` from `inputs` (every member's
    base and plain gains, the sheltered book, the loss-override state —
    a missing one recorded as absent), remember what each was built
    from, for stale_wash_inputs: a blended result depends on EVERY
    account in the blend, and `run --account B` rebuilds B without the
    blended pass (GitHub issue #15). `books` is the roster of accounts
    whose books were there when the pass ran (members and not): one
    that appears later (a new account built by `run --account <new>`)
    was not in the blend and makes it stale (2026-10-24 audit D01).
    `configured`: the project's accounts, the only ones the roster
    names: the books of an account taxjson.toml no longer names are
    still there when the pass runs (the run sweeps them later), and
    one named in the roster never made the pass stale once re-added
    and built alone (backlog TJ-4302).
    Kept in each wash file's record (`WASH_FIELD`, lib/records: TJ-1804),
    by the run (under its run lock), owner-only, never through a link,
    when it changed; the record's own fields are the run's to vouch for
    when it completes."""
    from taxjson.lib.records import set_record_field
    cache = Path(cache)
    fps = {Path(p).name: _fingerprint(Path(p)) for p in inputs}
    books = _book_accounts(cache)
    if configured is not None:
        names = set(configured)
        books = [b for b in books if b in names]
    for w in washes:
        w = Path(w)
        set_record_field(w, WASH_FIELD, {"blend": blend, "wash": _fingerprint(w),
                                         "inputs": fps, "books": books})


def stale_wash_inputs(wash: Path) -> "list[str]":
    """The inputs of a `<acct>_gains_wash.json` rebuilt AFTER it — its
    account's plain gains and base book, and the combined sheltered book
    (`run --account <sheltered>` rebuilds sheltered_base.json but skips
    the wash pass, so a registered-account buy that makes a taxable loss
    superficial never reached the served numbers; 2026-09 audit R1-251)
    — and every input of the blend it came from that changed since
    (another taxable account's books after `run --account <other>`:
    GitHub issue #15), from the record the wash pass kept
    (in the wash file's record) — by that record's fingerprints, the account's
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
    rec = _wash_record(wash)
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
        # outside the tax year (cli/common._gains_all_file): the
        # engine's denials are in it, across the taxable accounts the
        # run blends (TJ-0677); a registered account's purchase is
        # checked for the tax year only. "pre-wash" read as no
        # superficial-loss rule over a DENIED column that showed one
        # (backlog TJ-0452).
        return "denials among taxable accounts, full history"
    if any(Path(p).name.endswith("_gains_wash.json") for p in paths):
        return "wash-adjusted"
    return "pre-wash"

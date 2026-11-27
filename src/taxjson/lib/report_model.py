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
from typing import Any, Dict, Iterable, Mapping, Optional

from taxjson.lib.once import said_set
from taxjson.lib.out import fit_table
from taxjson.lib.report_fmt import _tokenize, align_columns


def format_report_table(rows: "list[str]", padding: str = "   ",
                        rule_before_last: bool = False, *,
                        fit: bool = False, drop=(), key=0,
                        width_: "Optional[int]" = None,
                        left_out: "Optional[list[str]]" = None) -> "list[str]":
    """Aligned table lines (first row is the header) with a wider column
    gap and a `---` underline beneath the header — for the readable report
    views, as opposed to the compact taxtext logs. `rule_before_last` also
    rules off the final row (e.g. a TOTAL). Returns the list of lines.

    `fit=True`: the house table (docs/output-style.md) — lib/out.fit_table
    over the same cells: two-space gaps, numeric columns right-aligned,
    and when wider than the width the `drop` columns go first (indexes,
    least important first), then one record per row headed by the `key`
    column(s). Opt in per table, after checking no program reads its rows
    (the doc lists the ones that are read). `left_out`: fit_table's, the
    headings of the columns dropped."""
    if fit:
        cells = _tokenize(rows)
        if not cells:
            return []
        body = cells[1:]
        foot = []
        if rule_before_last and len(body) > 1:
            body, foot = body[:-1], body[-1:]
        return fit_table(cells[0], body, foot=foot, drop=drop, key=key,
                         width_=width_, left_out=left_out)
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


_FULL_HISTORY = "_gains_all.json"
_WASHED = "_gains_wash.json"
_PLAIN = "_gains.json"

# What checked an account's gains file for the superficial-loss /
# wash-sale rule, as the run recorded it (record_loss_checks): the run's
# pass across accounts (the registered accounts' book included), the
# account's own engine pass where that is the whole check, no rule (a
# registered account's own sales, a US crypto account's), or not yet (built
# by `tjs run --account` without the pass, or waiting on elections).
LOSS_RULE_PASS = "pass"
LOSS_RULE_OWN = "own"
LOSS_RULE_NONE = "none"
LOSS_RULE_NOT_YET = "not_yet"
_LOSS_RULES = (LOSS_RULE_PASS, LOSS_RULE_OWN, LOSS_RULE_NONE, LOSS_RULE_NOT_YET)

# The run's record of them, for the tax year's files: a field of its record
# of the last run (work/.deps/run.json, lib/records RUN_FILE;
# pipeline/stage_table `commit_run` keeps it):
#   {"config": sha256 of taxjson.toml as the run read it,
#    "kinds": {<account>: {"taxable": bool, "crypto": bool, "country": str}}
#             (account_kinds: the accounts configured then)
#    "accounts": {<account>: {
#        "tax_year": {"file": "<account>_gains_wash.json", "check": "pass"},
#        "no_inputs_in": [<input folder>, ...]}}}   (an "own" check's only)
LOSS_CHECKS_FIELD = "loss_checks"

# A full-history file (`<account>_gains_all.json`) is no run's: a view
# builds it each time it reads it (cli/common `_gains_all_file`), from the
# books in work/ as they stand, so its check is what THAT build read
# (pipeline/basis_label `build_check`), kept in the file's own record
# (lib/records, as a wash pass's `wash` field is) with the fingerprint of
# the bytes it is true of:
#   {"check": "pass" | "own" | "none" | "not_yet", "of": {size, mtime_ns, sha256}}
# pass: the build blended every taxable account the rule reaches with this
# one, or read the registered accounts' whole book, or both; own: the
# account is alone among them and there is no registered book to read;
# not_yet: the build read only part of what the rule needs.
FULL_HISTORY_CHECK_FIELD = "loss_check"


def record_full_history_check(path: Path, check: str) -> None:
    """Keep what the build of the full-history file `path` checked
    (FULL_HISTORY_CHECK_FIELD), once it is written and vouched for."""
    from taxjson.lib.records import set_record_field
    path = Path(path)
    set_record_field(path, FULL_HISTORY_CHECK_FIELD, {"check": check, "of": _fingerprint(path)})


def forget_full_history_check(path: Path) -> None:
    """The record of the full-history file `path` removed, as far as it can
    be: its check could not be written (record_full_history_check raised),
    and an earlier build's must not stand for bytes this build wrote alike
    (the fingerprint binds the bytes; the check depends on the accounts and
    books of its own build). A record that cannot be removed either is
    left: the command that built the file reads its own word
    (gains_basis_label `built`)."""
    from taxjson.lib.records import record_path
    try:
        record_path(Path(path)).unlink(missing_ok=True)
    except OSError:
        pass


def full_history_check(path: Path) -> str:
    """What the build of the full-history file `path` checked
    (record_full_history_check): not yet when its record says nothing, or
    speaks of other bytes than the file's (never the run's record of the
    tax year's files, never the name: the name is every such file's)."""
    from taxjson.lib.records import read_record
    path = Path(path)
    rec = (read_record(path) or {}).get(FULL_HISTORY_CHECK_FIELD)
    if (isinstance(rec, dict) and rec.get("check") in _LOSS_RULES
            and isinstance(rec.get("of"), dict) and _matches(rec["of"], path)):
        return str(rec["check"])
    return LOSS_RULE_NOT_YET


def no_rule_reaches(kind: Any) -> bool:
    """Whether no superficial-loss / wash-sale rule reaches the sales of an
    account of `kind` (account_kinds' entry): a registered account's own, a
    US crypto account's (pipeline/stages `_wash_flags`). A country that
    cannot be read is no US one: a rule is taken to reach the account."""
    from taxjson.lib.country import CountryError, is_usa
    if not isinstance(kind, Mapping):
        return False
    if not kind.get("taxable"):
        return True
    try:
        return bool(kind.get("crypto")) and is_usa(kind.get("country"))
    except CountryError:
        return False


def account_kinds(accounts: Mapping[str, Any], country: str) -> Dict[str, Dict[str, Any]]:
    """{account: {"taxable", "crypto", "country"}}: what of each configured
    account's type a recorded check depends on (its gains run's flags:
    pipeline/stages `_wash_flags`), from the project's accounts
    (project/accounts `accounts_of`) and country; the record keeps the
    run's, a reader gives today's (pipeline/sanity `loss_check_kinds`)."""
    return {str(n): {"taxable": bool(a.taxable), "crypto": bool(a.crypto),
                     "country": str(country)} for n, a in accounts.items()}


def _run_record(work: Path) -> Path:
    from taxjson.lib.records import DEPS_DIR, RUN_FILE
    return Path(work) / DEPS_DIR / RUN_FILE


def _run_doc(work: Path) -> Dict[str, Any]:
    from taxjson.lib.records import RECORD_FORMAT
    try:
        doc = json.loads(_run_record(work).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return doc if isinstance(doc, dict) and doc.get("format") == RECORD_FORMAT else {}


def _config_digest(root: Path) -> "Optional[str]":
    from taxjson.lib.project_layout import CONFIG
    from taxjson.lib.records import sha256_file
    return sha256_file(Path(root) / CONFIG)


def _entry_check(entry: Dict[str, Any]) -> Any:
    tax_year = entry.get("tax_year")
    return tax_year.get("check") if isinstance(tax_year, dict) else None


def _recorded_checks(root: Path, kinds: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The record's accounts (None when there is none), as they stand
    with the accounts configured now (`kinds`: account_kinds). A taxjson.toml
    changed since keeps what the record says of a file that is still the
    one served: a pass's check, whatever the account's type is now or
    if it is no longer configured (that file is the pass's, as a stale one
    after `tjs run --account` is; a view of every account reads a removed
    one's books until a run sweeps them), and no rule while the account
    keeps the type and country its file was built under (a registered
    account made taxable has sales a rule reaches that nothing checked);
    an own check while the registered and the crypto accounts are the ones
    the run saw, of the same types (it held for them only); an account no
    longer configured nothing else, one newly configured none yet."""
    rec = _run_doc(Path(root) / "work").get(LOSS_CHECKS_FIELD)
    if not isinstance(rec, dict) or not isinstance(rec.get("accounts"), dict):
        return None
    entries = {a: e for a, e in rec["accounts"].items() if isinstance(e, dict)}
    if rec.get("config") == _config_digest(root):
        return entries
    then: Dict[str, Any] = rec["kinds"] if isinstance(rec.get("kinds"), dict) else {}

    def roster(ks: Mapping[str, Any]) -> Dict[str, Any]:
        return {n: k for n, k in ks.items()
                if isinstance(k, Mapping) and (not k.get("taxable") or k.get("crypto"))}

    own_holds = roster(then) == roster(kinds)
    return {a: e for a, e in entries.items()
            if _entry_check(e) == LOSS_RULE_PASS
            or (a in kinds and ((_entry_check(e) == LOSS_RULE_NONE and then.get(a) == kinds[a])
                                or (_entry_check(e) == LOSS_RULE_OWN and own_holds)))}


def _holds_inputs(folder: Path) -> bool:
    """Whether `folder` holds an export or a .tt file, as the run's input
    listing reads one (pipeline/config `input_files`: any case, never a
    hidden or an Office lock file)."""
    try:
        return any(p.suffix.lower() in (".csv", ".tt")
                   and not p.name.startswith((".", "~$")) for p in folder.iterdir())
    except OSError:
        return False


def _named_checks(root: Path, kinds: Mapping[str, Any]) -> Dict[str, Any]:
    """The tax-year checks the files served in work/ name, where no run
    recorded any (books of an older taxjson, a first run that failed,
    work/.deps/run.json damaged or deleted): an account's
    `<account>_gains_wash.json` is the file a pass across accounts wrote
    (as the label read it before there was a record); no rule for an account
    none reaches (no_rule_reaches, by `kinds`: the accounts configured
    now); any other file, no check yet. An older run's lone Canadian crypto
    account therefore reads "pre-wash" until a run records its own check."""
    out: Dict[str, Any] = {}
    for name, served in resolve_gains_files(Path(root) / "work", None, warn_stale=False).items():
        if no_rule_reaches(kinds.get(name)):
            check = LOSS_RULE_NONE
        elif served.name == f"{name}{_WASHED}":
            check = LOSS_RULE_PASS
        else:
            continue
        out[name] = {"tax_year": {"file": served.name, "check": check}}
    return out


def loss_checks(root: Path, kinds: Mapping[str, Any]) -> Dict[str, Any]:
    """What checked each account's tax-year gains file, as the project's
    last runs recorded it (record_loss_checks), for gains_basis_label, with
    the accounts configured now (`kinds`: account_kinds; with taxjson.toml
    edited since, what still holds: _recorded_checks); with no record, what
    the files served name (_named_checks). An account whose own engine pass
    was the whole check is left out once a registered account's folder it
    read empty holds an input."""
    root = Path(root)
    accounts = _recorded_checks(root, kinds)
    if accounts is None:
        return _named_checks(root, kinds)
    return {a: e for a, e in accounts.items()
            if not any(_holds_inputs(root / str(d)) for d in e.get("no_inputs_in") or ())}


def _check_of(entry: Any, path: Path) -> str:
    """The check `entry` (an account's in loss_checks) records for the
    tax-year file `path`, by name; not yet for a file it does not name
    (another file is served now)."""
    e = entry.get("tax_year") if isinstance(entry, dict) else None
    if isinstance(e, dict) and e.get("file") == path.name and e.get("check") in _LOSS_RULES:
        return str(e["check"])
    return LOSS_RULE_NOT_YET


def record_loss_checks(root: Path, decided: "Mapping[str, Optional[str]]", *,
                       kinds: Mapping[str, Any], full: bool,
                       no_inputs_in: Iterable[str] = ()) -> None:
    """Record what checked each account's tax-year gains file
    (LOSS_CHECKS_FIELD), once a run's passes are done (or the run stopped
    after them): `decided`, the run's word for each account it built
    (LOSS_RULE_PASS, _OWN, _NONE; None: no check this run), `kinds` the
    configured accounts (account_kinds). A check names the file it checked:
    the wash-adjusted file the pass wrote, the plain one an own pass is the
    whole check of, the file served for no rule (a full-history file's
    check is its own build's: record_full_history_check). An account the
    run did not check (None, an account a full run deferred) keeps the
    earlier check while that one's file is still the one served (a stale
    wash-adjusted file, warned: resolve_gains_files), else is not yet
    checked. A full run records its configured accounts; a partial one
    (`tjs run --account`) its own, the others kept as they stand
    (_recorded_checks). The earlier checks, with no record: what the files
    served name (_named_checks), so an older taxjson's full run followed by
    one `tjs run --account` keeps the pass's word for the files that pass
    wrote. An own check holds while the registered accounts' input folders
    (`no_inputs_in`, relative to the project) hold no input. Through
    lib/safe_write, when it changed."""
    from taxjson.lib.records import RECORD_FORMAT
    from taxjson.lib.safe_write import write_changed
    root = Path(root)
    work = root / "work"
    earlier = _recorded_checks(root, kinds)
    if earlier is None:
        earlier = _named_checks(root, kinds)
    accounts = {} if full else dict(earlier)
    for name in (list(kinds) if full else list(decided)):
        served = resolve_gains_files(work, name, warn_stale=False).get(name)
        if served is None:
            accounts.pop(name, None)
            continue
        check = decided.get(name)
        meant = {LOSS_RULE_PASS: f"{name}{_WASHED}", LOSS_RULE_OWN: f"{name}{_PLAIN}",
                 LOSS_RULE_NONE: served.name}.get(check or "")
        if meant != served.name:
            if _check_of(earlier.get(name), served) != LOSS_RULE_NOT_YET:
                accounts[name] = earlier[name]
                continue
            check = LOSS_RULE_NOT_YET
        entry: Dict[str, Any] = {"tax_year": {"file": served.name, "check": check}}
        if check == LOSS_RULE_OWN:
            entry["no_inputs_in"] = sorted(no_inputs_in)
        accounts[name] = entry
    doc = _run_doc(work) or {"format": RECORD_FORMAT}
    doc[LOSS_CHECKS_FIELD] = {"config": _config_digest(root), "kinds": dict(kinds),
                              "accounts": accounts}
    _run_record(work).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_changed(_run_record(work), json.dumps(doc, indent=1, sort_keys=True) + "\n")


def filed_basis_label(files) -> str:
    """The `basis` of a filed-year lock (cli/file `_close_year`): which
    files the filing read, for check-filed's recompute (bin/taxjson_filed
    `recompute_year`: the registered book for a wash-adjusted one): the
    file names alone, not the views' label of what the run's record says
    is checked (TJ-5473)."""
    return gains_basis_label(files, checks=None)


def _gains_account(name: str) -> str:
    """The account a gains file's name is of (`<account>_gains_wash.json`,
    `_gains_all.json`, `_gains.json`)."""
    for suffix in (_WASHED, _FULL_HISTORY, _PLAIN):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def gains_basis_label(files, *, checks: Optional[Mapping[str, Any]] = None,
                      built: Optional[Mapping[str, str]] = None) -> str:
    """Human label for which basis a report was computed on, from the
    paths resolve_gains_files returned ({account: path}, or the paths):
    what the superficial-loss / wash-sale rule checked of their sales.
    Printed by the query commands so a user comparing two reports can
    see WHY their totals differ.

    Each file is judged by the check on record for it. A tax-year file:
    `checks` (loss_checks: the run's record, or with none what the files
    served name), by the file the check names (another file: not yet
    checked). A full-history file (`<account>_gains_all.json`, read for a
    window outside the tax year: cli/common._gains_all_file): what its own
    build read, never the tax year's record nor its name, which says
    nothing: a view builds one from whatever books work/ holds. `built`:
    what the builds of this command checked, by resolved path
    (pipeline/run_state `view_checks`): the word for a file the command
    built; the file's record (full_history_check) only for one it did not.
    "wash-adjusted" when the set holds an account a rule reaches
    and every such account's file was checked completely (by the pass
    across accounts, by a full-history build that read all the rule needs,
    or by the account's own engine pass where that is the whole check);
    "pre-wash" otherwise: an account not yet checked (built by `tjs run
    --account` alone, waiting on elections, configured or retyped in
    taxjson.toml since; a full-history build that read part of what the
    rule needs: its rows may still carry the denials that part found), or
    none a rule reaches (a registered account's own sales, a US crypto
    account's). ", full history" is added for full-history files.

    `checks` None (the filed-year lock, which names its files:
    filed_basis_label; `taxjson-harvest` alone): the tax-year file names, as before the
    record: one wash-adjusted file makes the set "wash-adjusted" (TJ-5441,
    TJ-5471, TJ-5473: "denials among taxable accounts" left the
    registered purchase out, and "pre-wash" over a DENIED column that
    showed one read as no rule, backlog TJ-0452)."""
    items = ([(str(a), Path(p)) for a, p in files.items()] if hasattr(files, "items")
             else [(_gains_account(Path(p).name), Path(p)) for p in files])
    full = any(p.name.endswith(_FULL_HISTORY) for _a, p in items)
    if checks is None and not full:
        checked = any(p.name.endswith(_WASHED) for _a, p in items)
    else:
        mine = built or {}
        found = [mine.get(str(p.resolve())) or full_history_check(p)
                 if p.name.endswith(_FULL_HISTORY)
                 else _check_of(checks.get(a), p) if checks is not None
                 else LOSS_RULE_PASS if p.name.endswith(_WASHED) else LOSS_RULE_NOT_YET
                 for a, p in items]
        reached = [c for c in found if c != LOSS_RULE_NONE]
        checked = bool(reached) and all(c in (LOSS_RULE_PASS, LOSS_RULE_OWN) for c in reached)
    label = "wash-adjusted" if checked else "pre-wash"
    return f"{label}, full history" if full else label

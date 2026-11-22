"""`tjs run` itself: _cmd_run walks the stages, then the checks and the closing summary."""
import argparse
import atexit
import contextlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from taxjson.project.accounts import AccountSettings, account_of, accounts_of, typed_accounts
from taxjson.project.toml_io import read_toml
from taxjson.lib.pipeline import premium_timing
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib.cli_diag import write_text_atomic
from taxjson.lib.out import exit_text
from taxjson.lib.stage_msg import emit_line

from taxjson.pipeline import run_state
from taxjson.pipeline import stage_table as ST
from taxjson.pipeline.config import (
    load_settings,
    _config_shown, _country, _fetch_plugin_note, _income_rules,
    _refuse_unreadable_project_inputs, input_files, load_config,
    validate_config,
)
from taxjson.pipeline.diagnostics import (
    _count_noun, _diag_lines, _split_msg,
)
from taxjson.pipeline.freshness import _read_work_stamp, package_fingerprint, timeline_beside
from taxjson.pipeline.inputs import (
    _base,
    _read_dated_events, _read_loss_overrides,
    _say_loss_overrides, _tax_date, _US_EXPERIMENTAL_NOTE,
)
from taxjson.pipeline.messages import (
    _cmd_prog, _die, _die_input, _out_relpath, _out_wrap,
    _PROG, _run_concise, _say, _say_once, _step,
)
from taxjson.pipeline.sanity import (
    _has_inputs, _sanity_items_from_config, cmd_sanity,
)
from taxjson.pipeline.stages import (
    _accounts_with_symbol_codes, _duplicate_crypto_exports,
    _duplicate_input_files, _listing_suffix_stale,
    _migrate_edit, _missing_history_arg,
    _rates_inputs, _read_work_doc, _record_skipped_accounts,
    _resolve_manifest, _say_export_coverage, _say_in_kind,
    _say_listing_spellings, _say_option_transition, _say_similar_losses,
    _say_tobase_map, _say_tobase_until, _say_transfer_windows,
    _say_xlist_losses,
    stage_sweep, _tax_date_basis, _warn_cross_taxable_overlap,
    _warn_shared_broker_accounts, _wash_preferred_gains, OWN_MOVES_SUFFIX,
    MISSING_HISTORY_STATE, PendingElectionsError, stage_account, stage_blended_wash_pass,
    stage_cross_reports, stage_currency_rates, stage_fees,
    stage_in_kind_context, stage_own_account_moves, stage_moved_holdings,
    stage_reports, stage_wash_pass,
    transfer_arrivals,
)
from taxjson.pipeline.tools import (
    _cmd, _drop_work_file, _echo_captured,
    _echo_stage_stderr, _SHORT_KEY_RE, _write_work_stamp, run_to_file,
)
from taxjson.model import money as M


def _refuse_unusable_missing_history(project: Path) -> None:
    """Stop (exit 2) on a .tt OPENING cost=unknown line that cannot be
    used (malformed, a symbol opened twice in one account), naming it,
    before any stage reads the lines."""
    from taxjson.lib.missing_history import (TtOpeningError,
                                              load_missing_history)
    try:
        load_missing_history(project)
    except TtOpeningError as e:
        _die_input("an OPENING cost=unknown line cannot be used — fix it; "
                   "nothing was run:", *str(e).splitlines())
    except (OSError, ValueError) as e:
        _die_input(str(e))


def _sheltered_build(i: ST.Inst, _r: ST.Ready) -> None:
    # The row's books (BOOK) in the run's order (ST.member_books), not
    # as its inputs list them: the equity books first, then a sheltered
    # crypto account's. `own`: the run's `--account`, said by the caller.
    if not i.ctx.own:
        _step("Combining sheltered accounts")
    run_to_file(_cmd("taxjson-merge") + [str(p) for p in ST.member_books(i)], ST.output(i))


# The stage table with the sheltered row's build bound.
_RUN_STAGES = ST.bound({"sheltered": ST.Steps(_sheltered_build)})


def _merge_sheltered(cache: Path, args: argparse.Namespace, names: List[str],
                     accounts: Dict[str, Any]) -> Path:
    """work/sheltered_base.json, the books of the sheltered accounts
    `names` (in the run's order) merged: gated like a stage (the stage
    table's sheltered row over `names`, its books its inputs). Merged
    on every run, it carried a fresh time stamp each time (merged_at,
    dropped since: TJ-4267), so an incremental run (retired since) ran
    the blend's engine, which declares it, again (backlog TJ-4130)."""
    ctx = ST.StageCtx(work=cache, members=names, crypto_members=frozenset(
        n for n in names if accounts[n].crypto),
        own=args.account)
    ST.execute(_RUN_STAGES, ctx, "sheltered")
    return ST.output(ST.Inst("sheltered", ctx))


def _warn_year_without_activity(year: Any, bases: List[Path]) -> None:
    """Warn when no row of this run's books is dated in [settings]
    year. A typo'd in-range year (2015 for 2025) built all-zero filing
    totals with exit 0 and no word (R1-256)."""
    import json as _json
    if not year or not bases:
        return
    ys = str(year)
    lo = hi = None
    for b in bases:
        try:
            txs = _json.loads(Path(b).read_text(encoding="utf-8")).get(
                "transactions", [])
        except (OSError, ValueError, AttributeError):
            return                   # unreadable: other checks say so
        for t in txs:
            for d in (str(t.get("date") or "")[:10],
                      str(t.get("date_settle") or "")[:10]):
                if d.startswith(ys):
                    return
                if len(d) == 10:
                    lo = d if lo is None or d < lo else lo
                    hi = d if hi is None or d > hi else hi
    if lo is None:
        return                       # empty books: said elsewhere
    _say("warning", f"no transaction in any account's books is dated {ys} "
         f"(the books run {lo} to {hi})",
         f"Every {ys} filing total will be 0. Is [settings] year in "
         f"taxjson.toml right?", prog=_PROG)


def _loose_project_dirs(root: Path) -> List[str]:
    """The project folder, inputs/ and reports/ when another user may
    read or write them (group / other bits): taxjson creates them 0700,
    an older release or a copy did not (2026-10 security review LOW f).
    A folder under one of the project's own folders that no other user
    can pass through (yours, with no group or other search bit: the
    0700 `init` makes) is not: `mkdir inputs/slips` under it made
    inputs/ 0755 and the warning was false (backlog TJ-0426). Folders
    above the project are not counted (a home folder's mode is not the
    project's). Empty on Windows."""
    import os
    if os.name == "nt":
        return []
    try:
        top = (_PL.multi_root(root) or root).resolve()
        uid = os.getuid()
    except (OSError, AttributeError):
        top, uid = None, None

    def shut(d: Path) -> bool:
        # Nobody but its owner (and root) passes through `d`.
        try:
            st = d.stat()
        except OSError:
            return False
        return st.st_uid == uid and not st.st_mode & 0o011

    def reachable(p: Path) -> bool:
        if top is None:
            return True
        try:
            rp = p.resolve()
            rp.relative_to(top)
        except (OSError, ValueError):
            return True
        return not any(shut(d) for d in rp.parents
                       if d == top or top in d.parents)

    out = []
    for p, shown in ((root, "the project folder"), (root / "inputs",
                                                    "inputs/"),
                     (root / "reports", "reports/")):
        try:
            if p.is_dir() and p.stat().st_mode & 0o066 and reachable(p):
                out.append(shown)
        except OSError:
            continue
    return out


def _shell_quote(p: Path) -> str:
    import shlex
    return shlex.quote(str(p))


def _acquire_run_lock(cache: Path, *, closing: bool = False) -> None:
    """Hold an exclusive lock on work/.run.lock for the life of this
    process: released at exit by an atexit handler (one registered before
    this runs after the release, without the lock), by the OS when the process
    ends without running them (a signal, os._exit). A second `tjs run` in the
    same project refuses in one line.
    `closing`: close-year takes it too, so a run started while the year
    is closed refuses, and close-year refuses while a run is building
    the books it locks (backlog TJ-0924: the lock's totals came from
    one run and its frozen inputs and RETURN row from another).
    Where advisory locks are unavailable (no fcntl) runs are not
    serialized, as before."""
    if run_state.run_lock is not None:
        return                          # `tjs run run` chains
    # safe_write.file_lock: created owner-only and never through a
    # symlink (a planted work/.run.lock link was opened "a+", creating
    # or appending to its target; 2026-10 security review LOW b).
    import contextlib
    from taxjson.lib.safe_write import LockLinkError, file_lock
    _nothing = "Nothing was written." if closing else "Nothing was run."

    def busy() -> None:
        _die("another `tjs run` (or `tjs close-year`) is in "
             "progress in this project",
             f"Wait for it to finish (or stop it), then re-run. "
             f"{_nothing}")
    stack = contextlib.ExitStack()
    try:
        stack.enter_context(file_lock(cache / ".run.lock", on_wait=busy))
    except LockLinkError as e:
        _die(str(e), _nothing)
    run_state.run_lock = stack
    # Held to the end of the process, and let go before the interpreter
    # takes the modules apart: left to teardown, safe_write's names can be
    # gone by the time the lock's `finally` needs them.
    atexit.register(stack.close)


def _filing_reminder(cfg, settings, inputs_dir, cache, root) -> None:
    """A rollover elected in March is forgotten by filing season, and the
    deferral is INVALID without the paperwork. The option text said so at
    choose time; say it again where it matters: at the end of every run."""
    from taxjson.lib.corp_actions import (FILING_REQUIRED_ELECTIONS,
                                          Manifest)
    _year = str(settings.year or "")
    _obligations = []
    for _name, _acfg in accounts_of(cfg).items():
        if not _acfg.taxable:
            continue          # no gain to defer inside a registered
                              # plan, so there is nothing to file —
                              # the election only kept the books
                              # consistent
        _mp = _manifest_path_for(inputs_dir / _name, cache, _name)
        if not _mp.exists() and not _mp.is_symlink():
            continue
        try:
            _recs = list(Manifest.load(_mp).records.values())
        except (OSError, ValueError, AttributeError) as _e:
            # One unreadable manifest used to discard every
            # account's reminder in silence (S038-23): name it and
            # keep going.
            _say("warning", f"could not read {_out_relpath(_mp, root)} "
                 f"({_e})",
                 "Its elections are not checked for a FILING REQUIRED "
                 f"reminder; fix the file (`tjs elect {_name}` "
                 "reads it too).", prog=_PROG)
            continue
        for _rec in _recs:
            _todo = FILING_REQUIRED_ELECTIONS.get(_rec.election)
            if not _todo:
                continue
            _sum = _rec.summary or ""
            if _year and _sum[:4].isdigit() and _sum[:4] != _year:
                continue          # election from another tax year
            _obligations.append((f"{_name}: {_sum or _rec.event_id}",
                                 _todo))
    if _obligations:
        # Only s.86.1 needs one, and only for a foreign parent
        # (backlog TJ-0441): the headline says so.
        _head = (f"FILING REQUIRED for {len(_obligations)} "
                 f"election(s) when the parent is a foreign "
                 f"corporation — the s. 86.1 deferral is only valid "
                 f"with the paperwork")
        from taxjson.lib.out import show as _show, width as _ow
        if _ow(sys.stderr) > 0:
            # Shown to a person: one message, its lines flush-left.
            _say("warning", _head, *[x for _pair in _obligations
                                     for x in _pair], prog=_PROG, whole=True)
        else:
            # Captured (width 0): these bytes, which owe nothing.
            _say("warning", _head, prog=_PROG)
            for _what, _todo in _obligations:
                _show([*_out_wrap(_what, indent="  - ", hang="    ",
                                  file=sys.stderr),
                       *_out_wrap(_todo, indent="    ",
                                  file=sys.stderr)], sys.stderr)


def _say_filing_reminder(cfg, settings, inputs_dir, cache, root) -> None:
    """The reminder never breaks a run, and a bug in it doesn't lose a filing
    obligation in silence (backlog TJ-1525)."""
    try:
        _filing_reminder(cfg, settings, inputs_dir, cache, root)
    except Exception as _e:              # advisory guard, never a crash
        with contextlib.suppress(OSError):   # stderr itself may be what failed
            _say("warning", "could not check the elections for a FILING "
                 f"REQUIRED reminder: {type(_e).__name__}: {_e}",
                 "A s. 86.1 election needs its paperwork when the parent is a "
                 "foreign corporation: `tjs elect` lists yours.", prog=_PROG,
                 whole=True)


def _drop_run_records(cache: Path) -> None:
    """Drop the last full run's record of what the books were built from
    (work/.inputs_fingerprint.json) before any stage writes into work/: only
    the run's lock, and `tjs compare`'s copy of the reports it replaces (which
    reads the record), come first. Only a full run that finishes writes it
    back (lib/checklist `record_input_fingerprint`). A run scoped by
    --account, stopped by --strict or at pending elections (--no-input), or
    failed, leaves none, so every command that reads the books or the parse's
    cash events refuses them until a full run finishes: the inputs put back
    as the record named them no longer vouch for books such a run rewrote
    (backlog TJ-4356; the owner's rule of 2026-11-17). It is set aside in one
    step (SET_ASIDE_FILE: run-clean names what changed since that run, and
    says the last run did not finish one); a record that cannot be moved
    stops the run.

    The records of each stage say what it was built from (pipeline/freshness;
    a run that stops half-way records nothing); the code stamp of older
    releases is read by nothing now: dropped too."""
    from taxjson.lib.fingerprint_record import FINGERPRINT_FILE, SET_ASIDE_FILE
    rec = cache / FINGERPRINT_FILE
    if rec.is_file() and not rec.is_symlink():
        try:
            rec.replace(cache / SET_ASIDE_FILE)
        except OSError as e:
            _die(f"cannot set aside work/{FINGERPRINT_FILE}: {e.strerror or e}",
                 "It would vouch for books this run rewrites. Nothing was run.")
    _drop_work_file(rec)
    _drop_work_file(cache / ".code_fingerprint")


def _run_config(args: argparse.Namespace) -> Tuple[Path, Dict[str, Any],
                                                   Dict[str, AccountSettings], Settings]:
    """`tjs run`'s project, taxjson.toml, accounts and settings, and what is
    said before anything is read (_cmd_run): the US engine's note, folders
    others can read, orphaned work/ files (an exit with --strict),
    taxjson.toml's warnings; an exit with no account, year or country."""
    # Every stage is built every run: stale cached artifacts must never
    # feed a filing decision (`--fast` is gone, 2026-11-17).
    run_state.begin_run(full=not args.account)
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    accounts = accounts_of(cfg)
    # Register the config path globally so every `needs_rebuild` call
    # picks it up as an implicit dependency. Touching taxjson.toml
    # (e.g. bumping `year` for a new tax season, switching country,
    # adding a source_currency) now invalidates cached outputs.
    run_state.config_path = root / "taxjson.toml"
    settings = settings_of(cfg)
    if _country(settings) == "usa":
        _say("note", *_US_EXPERIMENTAL_NOTE,
             short="the US engine is EXPERIMENTAL: treat the output as a "
                   "draft")
    _loose = _loose_project_dirs(root)
    if _loose:
        _say_once("loose-permissions", "warning",
                  f"{', '.join(_loose)} can be read by other users of "
                  f"this computer (made by an older taxjson or another "
                  f"program; new files are owner-only)",
                  f"Tighten it once: chmod -R go-rwx {_shell_quote(root)}",
                  prog=_PROG,
                  short=f"{', '.join(_loose)} can be read by other users: "
                        f"`chmod -R go-rwx` it")
    _late_since = _grant_since_after_year(settings)
    if _late_since:
        _say("warning", _late_since, prog=_PROG)

    # Orphaned artifacts from RENAMED/REMOVED accounts: work/ files
    # keep matching the discovery globs (resolve_gains_files, fees
    # --cache, the audit's --source scan), so a renamed account was
    # counted TWICE in every aggregate (2026-09 audit). Loud, with the
    # exact fix. What the last run's row instances built, a full run
    # sweeps before any report reads it (stage_sweep, TJ-4265): only an
    # older taxjson's leftovers, or a partial run's, are warned about.
    from taxjson.lib.checklist import orphan_work_accounts
    _orphans = [a for a in orphan_work_accounts(root, accounts) if args.account
                or a not in ST.swept_accounts(root / "work", [a])]   # TJ-4265
    if _orphans:
        _say("warning", f"work/ carries artifacts for account(s) not in "
             f"taxjson.toml: {', '.join(_orphans)}",
             "These are STILL COUNTED by sum/fees/wash tools (a renamed "
             "account is counted twice). Delete work/<name>_* and "
             "reports/<name>* for each, or restore the account in the "
             "config.", prog=_PROG)
        if getattr(args, "strict", False):
            # Every aggregate would count them twice (A2-1165).
            _die("--strict: work/ carries artifacts for account(s) not in "
                 f"taxjson.toml: {', '.join(_orphans)} — aborting")
    if not accounts:
        _die("no [accounts.*] sections in taxjson.toml")
    # Warnings FIRST: a typo'd `yeer = 2025` must show its did-you-mean
    # before the "missing year" death it causes.
    _shared = _PL.shared_inputs(root)
    _inputs_at = _PL.inputs_dir(root)
    for msg in validate_config(
            cfg, _inputs_at, shared=_shared,
            inputs_shown=(_PL.shown(_inputs_at, root) if _shared
                          else "inputs")):
        _say("warning", *_split_msg(f"taxjson.toml: {msg}"), prog=_PROG)
    if _shared:
        # The exports every year shares: a folder of an account this
        # year does not have (one split later, one closed earlier) is
        # another year's, not a mistake (lib/project_layout).
        _local = _PL.local_input_folders(root)
        if _local:
            _say("warning", f"inputs/{', inputs/'.join(_local)} in this "
                 f"year's folder: not read — this project reads its "
                 f"exports from {_PL.shown(_PL.inputs_dir(root), root)}/ "
                 f"(its own inputs/ holds only slips/)", prog=_PROG)
        _other = _PL.unconfigured_inputs(root, accounts)
        if _other:
            _say("note", f"{_PL.shown(_PL.inputs_dir(root), root)}/: "
                 f"{', '.join(_other)} — not an account of {settings.year} "
                 f"(no [accounts.NAME] here): not read")
    _fnote = _fetch_plugin_note(cfg)
    if _fnote:
        _say("note", *_split_msg(f"taxjson.toml: {_fnote}"), prog=_PROG)
    for required in ("year", "country"):
        if not settings.has(required):
            _die(f"missing [settings] {required} in taxjson.toml")
    if not settings.base_currency:
        # Unset means the country's currency, as every other command
        # reads it (INPUTS-07; re-audit A2-0712: only `run` refused it).
        cfg["settings"] = settings = settings.replace(base_currency=_base(settings))
    return root, cfg, accounts, settings


def _run_folders(root: Path, args: argparse.Namespace,
                 cfg: Dict[str, Any]) -> Tuple[Path, Path, Path, Optional[Dict[str, str]]]:
    """`tjs run`'s inputs/, work/ and reports/ (an exit when work/ or
    reports/ cannot be used), the run's lock, the inputs' fingerprint as
    the run starts, the reports kept for `tjs compare`, the last full run's
    record set aside, the project's files that cannot be read refused, and
    the note naming a retired file (tv_exchange.map) (_cmd_run)."""
    inputs_dir = _PL.inputs_dir(root)
    cache = root / "work"
    reports_dir = root / "reports"
    # work/ or reports/ that is a FILE, or a project directory that
    # cannot be written, tracebacked — reports/ only after every stage
    # had rewritten work/, leaving the two out of step (S038-17).
    for _d in (cache, reports_dir):
        try:
            _d.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            _die(f"cannot use {_d.name}/: "
                 + ("it exists and is not a directory"
                    if isinstance(e, FileExistsError) or _d.is_file()
                    else (e.strerror or str(e))),
                 "Nothing was run.")
        import os as _os_mod
        if not _os_mod.access(_d, _os_mod.W_OK | _os_mod.X_OK):
            # The reason and the fix, the folder named from where the
            # command was run (backlog TJ-0056).
            _ds = _config_shown(_d)
            _die(f"cannot write to {_ds}/: Permission denied",
                 f"`chmod u+w {_ds}` makes it writable. Nothing was run.")
    # One run per project at a time: the stages write fixed work/ names,
    # so a cron `watch` racing an interactive run crashed one of them
    # with a FileNotFoundError on a shared .part file (A2-0778).
    _acquire_run_lock(cache)
    # What a full run is about to read, before any stage reads it: an
    # input edited while the run reads is told apart from what the
    # books were built from (record_input_fingerprint, backlog
    # TJ-0929).
    _fp_started: Optional[Dict[str, str]] = None
    if not args.account:
        try:
            from taxjson.lib.checklist import input_fingerprint
            _fp_started = input_fingerprint(root, cfg)
        except Exception:                # advisory, never a crash
            _fp_started = None
    # The reports a full run replaces become `tjs compare`'s
    # `prev` snapshot (lib/compare.rotate_previous); a single-
    # account run keeps the full run's there before it replaces part
    # of them, and records that reports/ are partial (backlog
    # TJ-0254).
    try:
        from taxjson.lib import compare as _cmp
        _cmp.rotate_previous(root, cfg, account=args.account or None)
    except Exception as _e:          # advisory, never a crash
        from taxjson.lib.cli_diag import is_no_space, no_space_text
        _say("warning", f"the previous reports were not kept for "
             f"`tjs compare`: "
             + (no_space_text(_e, "work/compare/")
                if is_no_space(_e) else str(_e)), prog=_PROG)
    # The last full run's record goes before any stage writes into work/
    # (TJ-4356), and the code stamp of older releases: _drop_run_records.
    _drop_run_records(cache)
    # A project map that exists as a NAME but cannot be opened (a
    # dangling or looping symlink) read as "absent": the run exited 0
    # with other gains (audit A2-0313). Absent and unreadable differ.
    _refuse_unreadable_project_inputs(root)
    # A leftover tv_exchange.map (the removed TradingView export) stops
    # nothing — nothing reads it — but is named once per run.
    from taxjson.lib.migrate import retired_file_note, retired_files
    _retired = retired_files(root)
    if _retired:
        _say("note", *_split_msg(retired_file_note(_retired)),
             prog=_PROG)
    return inputs_dir, cache, reports_dir, _fp_started


def _run_maps(root: Path, accounts: Dict[str, AccountSettings],
              cache: Path) -> Tuple[Optional[Path], Any, Optional[Path], Optional[Path]]:
    """ticker.map (refused with a line it cannot read), the dated events and
    loss overrides, the EXTRACT lines, and the missing-history lines, each
    read before any stage (_cmd_run)."""
    # ticker.map — one keyword-prefixed symbol-rule file. GLOBAL renames
    # apply everywhere; TOBASE consolidations apply only in the main
    # (to-base) merge; JOURNAL pairs also net in the holdings export;
    # DELETE nukes a ticker. Each merge/export stage requests its subset.
    ticker_map = _PL.ticker_map_path(root)
    ticker_map_arg = ticker_map if ticker_map.exists() else None
    if ticker_map_arg:
        # A line the loader cannot parse DROPS its rule, and a dropped
        # TOBASE/GLOBAL/JOURNAL rule moves ACB pools and the Schedule 3
        # gain; its warning used to reach only reports/*.sum while the
        # run (even --strict) exited 0 (S009-03). Refuse up front.
        from taxjson.lib.map_file import map_file_problems
        try:
            _tm_problems = map_file_problems(ticker_map)
        except OSError as e:        # not UTF-8 (S053-06)
            # An input that cannot be read: exit 2, one line naming it
            # (A2-0163, A2-1438 — the EXTRACT lines live here now).
            from taxjson.lib.cli_diag import describe_input_error
            _die_input(describe_input_error(e))
        if _tm_problems:
            _refuse_map_problems(
                ticker_map, _tm_problems,
                "either changes ACB pools and gains")
        # Its no-op lines (a symbol renamed to itself): said here, under
        # the project's checks, once for the run — each pass that reads
        # the map said them again (backlog TJ-0126).
        from taxjson.lib.map_file import load_map_file
        load_map_file(ticker_map)
    _say_tobase_map(root, ticker_map)
    # Dated events: the .tt JOURNAL / RENAME lines of every account
    # (lib/dated_events), checked up front; ticker.map's legacy JOURNAL
    # and dated RENAME lines still work, said once.
    _tmap_parsed = None
    if ticker_map_arg:
        from taxjson.lib.map_file import _parse_map_file
        try:
            _tmap_parsed = _parse_map_file(ticker_map)[0]
        except (OSError, ValueError):
            _tmap_parsed = None
        from taxjson.lib.dated_events import legacy_note
        _legacy = legacy_note(_tmap_parsed)
        if _legacy:
            _say("warning", _legacy[0], *_legacy[1], prog=_PROG)
    _read_dated_events(root, accounts, _tmap_parsed)
    _read_loss_overrides(root, accounts)
    # ticker.map EXTRACT lines — description-keyed ticker corrections
    # for securities the currency->exchange suffix mislabels. The parse
    # stage gets the map as taxjson-brokerage --security-overrides when
    # it has any (a malformed one was refused above with the rest of
    # the map's problems).
    sec_overrides_arg = None
    if ticker_map_arg:
        from taxjson.lib.ticker_map import read_side_rules
        _side = read_side_rules(ticker_map)
        if _side.extract:
            sec_overrides_arg = ticker_map
        if _side.retired:
            # TRADINGVIEW lines: the export they fed was removed. Ignored
            # (never an error — an older project may still carry them);
            # said once per run.
            _say("note", f"{ticker_map.name} has {len(_side.retired)} "
                 f"TRADINGVIEW line(s) ({', '.join(_side.retired)})",
                 "TradingView export removed; delete these lines (they "
                 "are ignored).", prog=_PROG)
    # Missing history: the accounts' .tt `OPENING <date> <SYMBOL> <qty>
    # cost=unknown` lines (units bought before the data; from
    # `find-missing-history --write-missing-history`). When any exists the
    # project folder feeds every account's gains run via
    # --incomplete-history, and the stages read the lines through it.
    mh_arg = _missing_history_arg(root)
    if mh_arg:
        _refuse_unusable_missing_history(mh_arg)
    # The lines withdrawn: each stage given them declared their stamp
    # (stages.MISSING_HISTORY_STATE), so the books built with them rebuild
    # (TJ-1807); the stamp, or an older taxjson's marker, says so.
    if mh_arg:
        from taxjson.lib.missing_history import read_tt_openings as _rtt
        try:
            _n_tt = len(_rtt(root, accounts))
        except ValueError:
            _n_tt = 0
        _step(f"Reading {_n_tt} .tt OPENING cost=unknown line"
              f"{'' if _n_tt == 1 else 's'} (openings for sales with no "
              f"purchase in the files)")
    elif any((cache / _n).exists() for _n in (
            MISSING_HISTORY_STATE, ".missing_history_applied", ".phantoms_applied")):
        _step("missing-history lines removed: rebuilding the gains built "
              "with them")
        (cache / MISSING_HISTORY_STATE).unlink(missing_ok=True)

    # The map removed: each stage given it declared it (`@ticker_map`), so
    # the books built with it rebuild (TJ-1807); a report's record that
    # names it says it was applied.
    if not ticker_map.exists() and ST.recorded_input(cache, ticker_map):
        _step(f"{ticker_map.name} removed: rebuilding the books built with it")
    return ticker_map_arg, _tmap_parsed, sec_overrides_arg, mh_arg


def _run_duplicate_inputs(inputs_dir: Path, accounts: Dict[str, AccountSettings],
                          args: argparse.Namespace) -> List[str]:
    """One export file in two accounts' folders: said (an exit with
    --strict), and what it books twice, for the run's input record
    (_cmd_run)."""
    _dup_inputs = _duplicate_input_files(inputs_dir, accounts)
    # What books one export in two accounts: recorded with the run's
    # input fingerprint, so the checklist's run-clean step, every report
    # banner and the closing list say it (backlog TJ-0478).
    _booked_twice: List[str] = [
        f"one file in "
        f"{' and '.join(sorted({x.split('/', 1)[0] for x in _paths}))}: "
        f"{', '.join(_paths)}"
        for _paths in _dup_inputs]
    for _paths in _dup_inputs:
        _say("attention", f"the same export file sits in two accounts: "
             f"{', '.join(_paths)} (identical content)",
             "Every row of it is booked twice, once in each account. Keep "
             "each broker export under ONE inputs/<account>/ folder.",
             indent="  ")
    if _dup_inputs and getattr(args, "strict", False):
        _die("--strict: the same export file is in two accounts "
             "(warning above)", "Its rows would be booked twice — "
             "nothing was built.")
    return _booked_twice


def _run_first_pass(settings: Settings, cache: Path, root: Path, inputs_dir: Path,
                    accounts: Dict[str, AccountSettings], args: argparse.Namespace,
                    reports_dir: Path, ticker_map_arg: Optional[Path],
                    sec_overrides_arg: Optional[Path], mh_arg: Optional[Path],
                    cfg: Dict[str, Any]) -> Tuple[Path, List[Tuple[str, AccountSettings]],
                                                  List[Tuple[str, AccountSettings]], Any,
                                                  List[PendingElectionsError]]:
    """`tjs run`'s currency rates, the accounts it builds (an exit when
    --account names none), and the parse before any book: every crypto
    account's exports (an exit with --strict when the same exchange rows
    are in two of them), then every equity account's transfer evidence
    (an account whose listing evidence moved read again), then the moves
    between your own accounts (_cmd_run)."""
    _step("Loading currency rates")
    rates = stage_currency_rates(settings, cache,
                                 _rates_inputs(root, inputs_dir, accounts))

    sheltered_items = [(n, c) for n, c in accounts.items()
                       if not c.taxable]
    taxable_items = [(n, c) for n, c in accounts.items()
                     if c.taxable]

    if args.account:
        sheltered_items = [(n, c) for n, c in sheltered_items if n == args.account]
        taxable_items = [(n, c) for n, c in taxable_items if n == args.account]
        if not sheltered_items and not taxable_items:
            _die(f"no [accounts.{args.account}] in taxjson.toml")

    no_input = getattr(args, "no_input", False)
    pending_accounts: List[PendingElectionsError] = []

    # Crypto sends pair a send in one crypto account with its arrival
    # in another: parse EVERY crypto account's exports before any
    # account's sends stage reads the transfer evidence, so the first
    # run after a new export pairs against it (re-audit A2-0077) and a
    # removed export's evidence is gone (A2-1026). The parse is cached,
    # so the account's own stage below does not parse again.
    _crypto_first = [(n, c) for n, c in accounts.items()
                     if c.crypto and not c.transfers]
    if len(_crypto_first) >= 2:
        for name, acfg in _crypto_first:
            stage_account(name, acfg, settings, inputs_dir, cache,
                          reports_dir, rates, ticker_map_arg,
                          sec_overrides_arg,
                          incomplete_history=mh_arg,
                          no_input=no_input,
                          strict=getattr(args, "strict", False),
                          parse_only=True)
        _dups = _duplicate_crypto_exports(
            cache, [n for n, _c in _crypto_first])
        for _d in _dups:
            _say("warning", *_split_msg(_d), prog=_PROG)
        if _dups and getattr(args, "strict", False):
            _die("--strict: the same exchange rows are in two crypto "
                 "accounts (above)", "Every one would be booked twice. "
                 "Remove the copy.")

    # US: a move between two of your own taxable accounts carries its
    # lots (US-BASIS-05). Every taxable equity account's transfer
    # evidence is parsed first (cached), so the moves are known before
    # any account's books are merged; each account's legs file is then
    # one of its sources.
    # Both countries: a transfer-in that pairs with a transfer-out of
    # another of your taxable accounts is not an arrival from outside
    # the books (lib/transfer_in) — every account's evidence must be
    # current before any account's books are merged.
    # Every equity account, sheltered ones too: two listings joined by
    # a transfer journal (lib/cross_listings) are one security in EVERY
    # account's books, so all transfer evidence is read before any
    # account is merged.
    _equity_first = [(n, c) for n, c in accounts.items()
                     if not c.crypto]
    if len(_equity_first) < 2:
        _equity_first = []
    # Questrade internal symbol codes are resolved from the OTHER
    # accounts' transfers and names (stage_symbol_codes): those accounts
    # are parsed first, the accounts holding codes last.
    _code_accts = (_accounts_with_symbol_codes(inputs_dir, accounts)
                   if len(accounts) >= 2 else [])
    if _code_accts:
        _equity_first += [(n, c) for n, c in accounts.items()
                          if not c.crypto
                          and n not in {x for x, _c in _equity_first}]
        _equity_first.sort(key=lambda nc: nc[0] in _code_accts)
    if _equity_first:
        for name, acfg in _equity_first:
            try:
                stage_account(name, acfg, settings, inputs_dir, cache,
                              reports_dir, rates, ticker_map_arg,
                              sec_overrides_arg,
                              incomplete_history=mh_arg,
                              no_input=no_input,
                              strict=getattr(args, "strict", False),
                              parse_only=True)
            except PendingElectionsError:
                pass
        # A listing read from another account's books (lib/
        # listing_suffix) whose evidence that account parsed only after
        # this one: read the account again, so every account's books
        # are final before any is merged (the order never matters).
        _stale = set(_listing_suffix_stale(root, cache))
        for name, acfg in _equity_first:
            if name not in _stale:
                continue
            try:
                stage_account(name, acfg, settings, inputs_dir, cache,
                              reports_dir, rates, ticker_map_arg,
                              sec_overrides_arg,
                              incomplete_history=mh_arg,
                              no_input=no_input,
                              strict=getattr(args, "strict", False),
                              parse_only=True)
            except PendingElectionsError:
                pass
    stage_own_account_moves(root, cfg, settings, cache,
                            strict=getattr(args, "strict", False))
    return rates, sheltered_items, taxable_items, no_input, pending_accounts


def _run_sheltered(sheltered_items: List[Tuple[str, AccountSettings]], settings: Settings,
                   inputs_dir: Path, cache: Path, reports_dir: Path, rates: Path,
                   ticker_map_arg: Optional[Path], sec_overrides_arg: Optional[Path],
                   mh_arg: Optional[Path], no_input: Any, args: argparse.Namespace,
                   pending_accounts: List[PendingElectionsError],
                   accounts: Dict[str, AccountSettings]
                   ) -> Tuple[List[Tuple[str, Dict[str, Path]]], List[str],
                              Optional[Path], List[Path]]:
    """`tjs run`'s sheltered accounts: each one's books (deferred on
    elections, skipped with no inputs), then their combined book, merged
    and validated (an exit when it fails, or when --account would leave
    out a sibling's book) or removed when no account has books
    (_cmd_run)."""
    sheltered_outputs: List[Tuple[str, Dict[str, Path]]] = []
    _skipped_no_input: List[str] = []       # no CSV/.tt — see B7 helper
    for name, acfg in sheltered_items:
        try:
            out = stage_account(name, acfg, settings, inputs_dir, cache,
                                reports_dir, rates, ticker_map_arg,
                                sec_overrides_arg,
                                incomplete_history=mh_arg,
                                no_input=no_input,
                                strict=getattr(args, "strict", False))
        except PendingElectionsError as pe:
            _say("warning", f"{name}: corp-action elections required — "
                 f"account deferred", indent="  ")
            pending_accounts.append(pe)
            continue
        if out is None:                     # unpopulated account — skipped
            _skipped_no_input.append(name)
            continue
        sheltered_outputs.append((name, out))

    sheltered_base: Optional[Path] = None
    _sheltered_merge_inputs: List[Path] = []
    _sheltered_merge_names = [n for n, _o in sheltered_outputs]
    if sheltered_outputs and not args.account:
        _sheltered_merge_inputs = [o["base"] for _, o in sheltered_outputs]
    elif sheltered_outputs and args.account:
        # `--account <sheltered>`: the named book is fresh, the other
        # sheltered books are whatever the last run left in work/. Fold
        # them together anyway — leaving sheltered_base.json untouched
        # meant a later `wash-radar --account margin` read a sheltered
        # book that predated this run's buys,
        # exactly the rows a 30-day radar exists to see (2026-09 audit).
        _other_names = [n for n, c in accounts.items()
                        if not c.taxable
                        and n != args.account]
        _others = [cache / f"{n}_base.json" for n in _other_names]
        # A sibling WITH inputs but no book (deleted, or its last stage
        # failed) would silently drop its registered buys from the
        # combined book — the radar and the what-if then said "safe to
        # sell at a loss" (audit A2-0128). Refuse and keep the old
        # combined book; an account with no inputs never has a book.
        _lost = [f"{n}_base.json" for n in _other_names
                 if not (cache / f"{n}_base.json").exists()
                 and not (cache / f"{n}_pending_elections.json").exists()
                 and (input_files(inputs_dir / n, ".csv")
                      or input_files(inputs_dir / n, ".tt"))]
        if _lost:
            _die(f"--account {args.account}: the sheltered book(s) "
                 f"{', '.join(_lost)} are missing from work/ although "
                 f"their accounts have inputs",
                 "sheltered_base.json was left unchanged (rebuilding it "
                 "without them would hide their purchases from the "
                 "superficial-loss checks). Run `tjs run` without "
                 "--account to rebuild every book.")
        _missing = [p.name for p in _others if not p.is_file()]
        _sheltered_merge_inputs = (
            [o["base"] for _, o in sheltered_outputs]
            + [p for p in _others if p.is_file()])
        _sheltered_merge_names += [n for n, p in zip(_other_names, _others) if p.is_file()]
        _step("Combining sheltered accounts")
        _say("note", f"from this run's {args.account} book plus the other "
             f"sheltered accounts' last-built books"
             + (f" (no book for {', '.join(_missing)}: no inputs, or "
                f"deferred on elections)" if _missing else ""),
             indent="  ", file=sys.stdout)
    _pending_sheltered = {pe.account for pe in pending_accounts}
    if (not _sheltered_merge_inputs and not args.account
            and not _pending_sheltered):
        # No sheltered account produced books in this FULL run (the last
        # one was removed, or its inputs emptied): the previous run's
        # combined book must go, or the filed-year lock, check-filed and
        # the radar keep reading registered holdings that no longer
        # exist — a false OK on a moved filed number (S004-05).
        for _ghost in (cache / "sheltered_base.json",
                       cache / "sheltered_base.json.diag"):
            if _ghost.exists():
                _ghost.unlink()
                _say("note", f"removed stale work/{_ghost.name} (no "
                     f"sheltered account has books)", indent="  ",
                     file=sys.stdout)
    if _sheltered_merge_inputs:
        sheltered_base = _merge_sheltered(cache, args, _sheltered_merge_names, accounts)
    if sheltered_base is not None:
        # Sanity-check the combined sheltered file before the wash-radar
        # pass consumes it. Quiet on success; surface and halt on failure.
        from taxjson.pipeline.dispatch import run_cmd as _run_cmd
        vres = _run_cmd(_cmd("taxjson-validate") + [str(sheltered_base)],
                        capture_output=True)
        if vres.returncode != 0:
            _echo_stage_stderr((vres.stdout or "") + (vres.stderr or ""))
            # A clean exit, not a raw CalledProcessError traceback —
            # the validator's output above already names the offending
            # transaction(s); tell the user where to look next.
            _die("the combined sheltered book failed validation (details "
                 "above) — the wash pass cannot trust it",
                 "The usual cause is a malformed row from a corp-action "
                 "election; check the named account's DIAGNOSTICS in its "
                 ".sum, fix the election (`tjs elect <account> "
                 "--redo`) or the input row, and re-run.")
    return sheltered_outputs, _skipped_no_input, sheltered_base, _sheltered_merge_inputs


def _run_taxable(settings: Settings, accounts: Dict[str, AccountSettings], cache: Path,
                 args: argparse.Namespace, taxable_items: List[Tuple[str, AccountSettings]],
                 inputs_dir: Path, reports_dir: Path, rates: Path,
                 ticker_map_arg: Optional[Path], sec_overrides_arg: Optional[Path],
                 mh_arg: Optional[Path], no_input: Any,
                 pending_accounts: List[PendingElectionsError], _skipped_no_input: List[str],
                 sheltered_base: Optional[Path]
                 ) -> Tuple[List[Tuple[str, Dict[str, Path], bool]], List[str], List[str],
                            bool]:
    """`tjs run`'s taxable accounts: which blend (crypto with crypto,
    decided on the config; a stale blend mirror removed), then each one's
    books (deferred on elections, skipped with no inputs) and its wash
    pass, or its place in a blended pass after the loop; a crypto
    account a full run gives no wash pass has its stale wash files
    removed (_cmd_run)."""
    taxable_outputs: List[Tuple[str, Dict[str, Path], bool]] = []
    _blend_names: List[str] = []
    # Canada: crypto accounts blend with each other when the project
    # CONFIGURES two or more (decided on the config, not on this run's
    # --account subset, so a single-account rerun never rewrites a
    # blended wash file with a one-exchange one).
    _crypto_blend_names: List[str] = []
    _crypto_blend = (
        _country(settings)
        not in ("us", "usa")
        and sum(1 for _c in accounts.values()
                if _c.taxable and _c.crypto) >= 2)
    # USA: taxable crypto accounts with a move of coins between them
    # (stage_own_account_moves wrote their legs) run one blended crypto
    # pass — FIFO stays per account and no wash-sale rule applies, but
    # the move carries its lots (US-CRYPTO-05). Without a move they stay
    # per account.
    _us_crypto_blend = (
        _country(settings) in ("us", "usa")
        and any(_c.taxable and _c.crypto
                and not _c.transfers
                and (cache / f"{_n}{OWN_MOVES_SUFFIX}").is_file()
                for _n, _c in accounts.items()))
    if not args.account:
        # A per-account blend mirror is rewritten only for accounts IN
        # this run's blend: one that left it (re-typed, the other
        # crypto account removed) kept its old mirror, and its fixed
        # problem was re-reported in every later banner (S038-08).
        for _n, _c in accounts.items():
            _taxable = _c.taxable
            for _tag, _in_blend in (
                    ("blend", _taxable and not _c.crypto),
                    ("cryptoblend", _taxable and bool(_c.crypto)
                     and (_crypto_blend or (_us_crypto_blend
                                            and not _c.transfers)))):
                _m = cache / f"{_n}_{_tag}.diag"
                if not _in_blend and _m.exists():
                    _m.unlink()
    for name, acfg in taxable_items:
        try:
            out = stage_account(name, acfg, settings, inputs_dir, cache,
                                reports_dir, rates, ticker_map_arg,
                                sec_overrides_arg,
                                incomplete_history=mh_arg,
                                no_input=no_input,
                                strict=getattr(args, "strict", False))
        except PendingElectionsError as pe:
            _say("warning", f"{name}: corp-action elections required — "
                 f"account deferred", indent="  ")
            pending_accounts.append(pe)
            continue
        if out is None:                     # unpopulated account — skipped
            _skipped_no_input.append(name)
            continue
        is_crypto = bool(acfg.crypto)
        taxable_outputs.append((name, out, is_crypto))
        # Wash second pass: crypto is skipped only for US projects
        # (§1091 does not reach digital assets — matching _wash_flags).
        # Canada's superficial-loss rule covers ANY identical property,
        # crypto included: the engine already wash-checks Canadian
        # crypto in the main pass, so the advisory tooling must not go
        # silent on exactly the account type where 30-day rebuys are
        # most common.
        _crypto_wash_covered = _country(settings) not in ("us", "usa")
        if not is_crypto:
            # Equity taxable accounts are handled by ONE blended pass
            # after this loop (Canada ACB blending / US cross-account
            # §1091 — the multi-account fix). Collected here.
            _blend_names.append(name)
        elif _crypto_wash_covered and _crypto_blend:
            # Two or more Canadian crypto accounts: ONE blended crypto
            # pass after this loop (s.47 averaging and superficial loss
            # across exchanges — each exchange's book alone missed both).
            _crypto_blend_names.append(name)
        elif _crypto_wash_covered and sheltered_base is not None:
            stage_wash_pass(name, settings, cache, reports_dir, sheltered_base,
                            incomplete_history=mh_arg)
        elif _us_crypto_blend and not acfg.transfers:
            # USA, two or more crypto accounts: ONE blended crypto pass
            # (no wash-sale rule) so own moves carry their lots.
            _crypto_blend_names.append(name)
        elif not args.account and not pending_accounts:
            # A FULL run decided this crypto account gets no wash pass
            # (US policy excludes it, or the sheltered context vanished
            # by data). A stale _gains_wash.json would otherwise keep
            # winning resolve_gains_files forever. (Kept under
            # --account and pending-election deferrals — transient.)
            for _stale in (cache / f"{name}_gains_wash.json",
                           cache / f"{name}_gains_wash.traces",
                           cache / f"{name}_gains_wash.json.diag",
                           timeline_beside(
                               cache / f"{name}_gains_wash.json"),
                           reports_dir / f"{name}_wash.sum"):
                _stale.unlink(missing_ok=True)
    return taxable_outputs, _blend_names, _crypto_blend_names, _crypto_blend


def _run_reports(root: Path, settings: Settings, cache: Path, ticker_map_arg: Optional[Path],
                 mh_arg: Optional[Path], cfg: Dict[str, Any], _tmap_parsed: Any,
                 pending_accounts: List[PendingElectionsError], args: argparse.Namespace,
                 _blend_names: List[str], reports_dir: Path, sheltered_base: Optional[Path],
                 _crypto_blend_names: List[str], _crypto_blend: bool,
                 taxable_outputs: List[Tuple[str, Dict[str, Path], bool]],
                 sheltered_outputs: List[Tuple[str, Dict[str, Path]]],
                 accounts: Dict[str, AccountSettings]) -> None:
    """`tjs run`'s passes once every account's books are built: short
    positions, the dated events' state and renames, the blended wash
    passes, the moved holdings, each account's reports, and the notes
    said across accounts; a sheltered account's stale wash files removed
    (_cmd_run)."""
    _report_short_positions(root, settings, cache, ticker_map_arg, mh_arg)
    _rename_records = _write_dated_events_state(root, cfg, cache,
                                                _tmap_parsed)
    if not pending_accounts:
        _warn_unused_renames(_rename_records,
                             strict=getattr(args, "strict", False))
        _check_renamed_late(root, strict=getattr(args, "strict", False))
    if _blend_names and not args.account and not pending_accounts:
        stage_blended_wash_pass(_blend_names, settings, cache,
                                reports_dir, sheltered_base,
                                incomplete_history=mh_arg,
                                strict=getattr(args, "strict", False))
    if _crypto_blend_names and not args.account and not pending_accounts:
        stage_blended_wash_pass(_crypto_blend_names, settings, cache,
                                reports_dir, sheltered_base,
                                incomplete_history=mh_arg,
                                tag="cryptoblend",
                                no_wash=not _crypto_blend,
                                spot_crypto=not _crypto_blend,
                                strict=getattr(args, "strict", False))
    if not args.account and not pending_accounts:
        # Units moved in from another of your accounts: their cost from
        # the passes just run, in the holdings files (TJ-0598).
        stage_moved_holdings(_blend_names + _crypto_blend_names, taxable_outputs)
    stage_reports(sheltered_outputs, taxable_outputs)
    _say_transfer_windows(settings)
    if not pending_accounts:
        _say_loss_overrides(root, settings, cache,
                            full=not args.account)
    _say_in_kind(root, cache, settings,
                 strict=getattr(args, "strict", False))
    if not args.account and not pending_accounts:
        # A ticker.map line naming a bare ticker whose US listing the
        # books hold (v0.24.1 leftovers, 1), said before the radar.
        _say_listing_spellings(root)
        # A trade in an ended interlisting after its tobase.map `until`
        # date: the ticker may now be another security's (CA-XLIST-06).
        _say_tobase_until(root)
        # Every account's gains are final: a loss on one listing, the
        # other listing bought in the window (QA F3).
        _say_xlist_losses(root, cfg, cache,
                          strict=getattr(args, "strict", False))
        # A loss on a fund with a SIMILAR fund bought in the window.
        _say_similar_losses(root, cfg, cache)
        # Written options carried in from an earlier year and closed in
        # this one, on transition close timing (CA-OPT-11).
        _say_option_transition(root, cfg)
        # A broker whose exports stop while it holds positions.
        _say_export_coverage(root, cfg)
    if not args.account and not pending_accounts:
        # A sheltered account never gets a wash pass. One re-typed from
        # taxable kept its old <name>_gains_wash.json / _wash.sum, which
        # resolve_gains_files prefers forever — the "run a full `tjs
        # run`" remedy never cleared them (S038-19).
        for _n, _c in accounts.items():
            if not _c.sheltered:
                continue
            for _stale in (cache / f"{_n}_gains_wash.json",
                           cache / f"{_n}_gains_wash.traces",
                           cache / f"{_n}_gains_wash.json.diag",
                           reports_dir / f"{_n}_wash.sum"):
                if _stale.exists():
                    _stale.unlink()
                    _say("note", f"removed stale {_stale.name} ({_n} is "
                         f"sheltered: no wash pass)", indent="  ",
                         file=sys.stdout)


def _run_pending_elections(pending_accounts: List[PendingElectionsError], cache: Path,
                           args: argparse.Namespace, root: Path) -> None:
    """`tjs run`'s corp-action elections: with an account deferred on
    them, work/pending_elections.json written and exit 3 with the `tjs
    elect` lines; otherwise the file kept for the other accounts under
    --account, or removed (_cmd_run)."""
    if pending_accounts:
        # Some account(s) stopped at unresolved corp-action elections.
        # Aggregate the per-account pending JSONs into ONE document a
        # GUI (or the user) can act on, explain the resolution path,
        # and exit 3 — the cross-account passes below would be built
        # from incomplete books.
        import json as _json
        agg: Dict[str, Any] = {"schema_version": 1, "accounts": {}}
        agg_path = cache / "pending_elections.json"
        # Under --account only a SUBSET was re-examined: other accounts'
        # pending entries in the existing aggregate are still live —
        # overwriting (or, below, unlinking) the file lost their
        # ready-to-copy --set lines (REVIEW #33).
        if args.account and agg_path.exists():
            try:
                prior = _load_pending_agg(agg_path)
                agg["accounts"].update(
                    {a: d for a, d in (prior.get("accounts")
                                       or {}).items()
                     if a != args.account})
            except ValueError:
                pass                    # damaged: rebuilt below
        for pe in pending_accounts:
            try:
                agg["accounts"][pe.account] = _json.loads(
                    pe.pending_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                agg["accounts"][pe.account] = {"pending": []}
        # tmp + replace: a reader never sees half a file (A2-0779)
        write_text_atomic(agg_path,
                          _json.dumps(agg, indent=2, sort_keys=True))
        from taxjson.lib import out as _out
        _n = len(pending_accounts)
        _sets = []
        for pe in pending_accounts:
            doc = agg["accounts"].get(pe.account) or {}
            for ev in doc.get("pending", []):
                opts = "|".join(o["election"] for o in ev.get("options",
                                                             []))
                _sets.append(f"  tjs elect {pe.account} --set "
                             f"{ev['event_id']}=<{opts}>")
        _shown = _out.width(sys.stderr) > 0
        if not _shown:
            _out.put("", file=sys.stderr)
        _lines = _out.message(
            "error", f"{_n} account{'s' if _n != 1 else ''} "
            f"need{'' if _n != 1 else 's'} corp-action elections "
            f"before {'their' if _n != 1 else 'its'} books can build",
            prog=f"{_PROG} run")
        # The ready lines are commands to copy: never wrapped (shown to
        # a person, flush-left like every continuation).
        _lines += [ln.strip() if _shown else ln for ln in _sets]
        _lines += _out.wrap(
            "`tjs elect --pending` shows what each option books "
            "and the hints it needs (also in "
            f"{_out.relpath(agg_path, root)}). Set each with `tjs "
            "elect ACCOUNT --set` (add --hint KEY=VALUE where "
            "required), or run `tjs run` at a terminal to be "
            "asked; then run again.", indent="" if _shown else "  ",
            stream=sys.stderr)
        _out.show(_lines, sys.stderr)
        raise SystemExit(3)
    _agg_path = cache / "pending_elections.json"
    if args.account and _agg_path.exists():
        # This run only proved args.account resolved — drop just its
        # entry; other accounts' pending elections are untouched
        # (REVIEW #33: the unconditional unlink made `elect --pending`
        # claim everything was resolved).
        import json as _json
        try:
            _doc = _load_pending_agg(_agg_path)
        except ValueError:
            # A damaged or wrong-shape aggregate is a rebuildable work
            # file: dropped, never a traceback after a good run
            # (re-audit A2-1439, A2-1457, A2-1419).
            _doc = None
            _agg_path.unlink(missing_ok=True)
        if _doc is not None:
            _accts = dict(_doc.get("accounts") or {})
            _accts.pop(args.account, None)
            _accts = {a: d for a, d in _accts.items() if d.get("pending")}
            if _accts:
                _doc["accounts"] = _accts
                write_text_atomic(
                    _agg_path, _json.dumps(_doc, indent=2, sort_keys=True))
            else:
                _agg_path.unlink()
    else:
        _agg_path.unlink(missing_ok=True)


def _run_cross_reports(args: argparse.Namespace, cache: Path, reports_dir: Path,
                       accounts: Dict[str, AccountSettings],
                       sheltered_outputs: List[Tuple[str, Dict[str, Path]]],
                       taxable_outputs: List[Tuple[str, Dict[str, Path], bool]],
                       settings: Settings, sheltered_base: Optional[Path],
                       ticker_map_arg: Optional[Path], mh_arg: Optional[Path],
                       _booked_twice: List[str], _crypto_blend: bool) -> List[str]:
    """A full `tjs run`'s sweep and reports across accounts, and its notes
    on symbols in two accounts and on one broker account in two (an exit
    with --strict); what that books twice is added to the run's input
    record's list (_cmd_run)."""
    if not args.account:
        stage_sweep(cache, reports_dir, accounts=list(accounts))

        all_gains = ([o["gains"] for _, o in sheltered_outputs] +
                     [_wash_preferred_gains(o["gains"])
                      for _, o, _ in taxable_outputs])
        # Radar/lint bases: equity always; crypto too when the
        # jurisdiction's wash rule covers it (Canada s.54 identical
        # property — same condition as the second pass above).
        _crypto_wash_covered = _country(settings) not in ("us", "usa")
        taxable_equity_base = [
            o["base"] for _, o, is_crypto in taxable_outputs
            if not is_crypto or _crypto_wash_covered]
        stage_cross_reports(all_gains, taxable_equity_base, sheltered_base, reports_dir,
                            ticker_map_arg, missing_history=mh_arg,
                            country=_country(settings))
        # Overlap notes per blended group — the note says the blended
        # pass covers the symbol, so it must only name accounts a blend
        # actually spans (crypto blends only in Canada; equity and
        # crypto never blend with each other).
        _warn_cross_taxable_overlap(
            [(n, o["base"]) for n, o, c in taxable_outputs if not c],
            settings)
        _shared_brokers = _warn_shared_broker_accounts(
            [(n, o["base"]) for n, o in sheltered_outputs]
            + [(n, o["base"]) for n, o, _c in taxable_outputs])
        _booked_twice += _shared_brokers
        if _shared_brokers and getattr(args, "strict", False):
            # Every row of that broker account is booked twice (A2-0366).
            _die("--strict: one broker account feeds two taxjson "
                 "accounts (warning above) — aborting.")
        if _crypto_blend:
            _warn_cross_taxable_overlap(
                [(n, o["base"]) for n, o, c in taxable_outputs if c],
                settings)
    return _booked_twice


def _run_finish(cache: Path, settings: Settings, rates: Path, reports_dir: Path,
                sheltered_outputs: List[Tuple[str, Dict[str, Path]]],
                taxable_outputs: List[Tuple[str, Dict[str, Path], bool]], root: Path,
                accounts: Dict[str, AccountSettings], args: argparse.Namespace,
                cfg: Dict[str, Any], _booked_twice: List[str],
                _fp_started: Optional[Dict[str, str]], inputs_dir: Path) -> None:
    """`tjs run`'s close: the fees report, the year and option-grant
    warnings, the filed years checked, the inputs' fingerprint recorded,
    the fx-cash report and the exports, the filing reminder, the late
    sweep and the rebuild records, then "Done" (_cmd_run)."""
    stage_fees(cache, settings, rates, reports_dir)
    _warn_year_without_activity(
        settings.year,
        [o["base"] for _, o in sheltered_outputs]
        + [o["base"] for _, o, _c in taxable_outputs])
    # After the books: said only when they hold a written option (a
    # crypto-only project writes none — nothing to warn about).
    _since_warn = _grant_since_warning(settings, root, accounts)
    if _since_warn:
        _say("warning", *_split_msg(_since_warn), prog=_PROG)

    # Filed-year lock: recompute every closed year from the fresh books
    # and shout if a filed number moved (warn-only; --strict aborts).
    try:
        from taxjson.lib import filed_locks as _tf
        _snaps = _tf.list_snapshots(root)
        if _snaps and args.account:
            # A single-account run skipped the cross-account wash pass,
            # so the books the recompute would read are NOT the books
            # a full run produces: printing "OK" here right after
            # declaring the wash pass skipped was a false all-clear
            # that the next full run contradicted with DRIFTED
            # (2026-09 audit). Say so instead of checking.
            _step("Checking the filed years")
            for _year, _ in _snaps:
                _say("note", f"filed {_year}: not checked (a single-account "
                     f"run skips the wash pass; run without --account)",
                     indent="  ", file=sys.stdout)
        elif _snaps or _lock_missing_year(root, settings) is not None:
            # (A frozen year whose lock was deleted: said as a damaged
            # one is; backlog TJ-0263.)
            _step("Checking the filed years")
            _check_filed_years(root, cache, settings,
                               strict=getattr(args, "strict", False),
                               console=True)
    except SystemExit:
        raise
    except Exception as _e:              # advisory guard, never a crash
        _say("warning", f"filed-year check failed: {_e}", prog=_PROG)

    if not args.account:
        # What these reports were built from (content hashes), so the
        # checklist's run-clean step sees a deleted input or a
        # corrected export copied with an old mtime (S067-07).
        try:
            from taxjson.lib.checklist import record_input_fingerprint
            _edited = record_input_fingerprint(
                root, cfg, booked_twice=_booked_twice,
                started=_fp_started, code=package_fingerprint())
            if _edited:
                _say("warning", f"{len(_edited)} input(s) changed while "
                     f"this run read them: {', '.join(_edited[:3])}"
                     + (" ..." if len(_edited) > 3 else ""),
                     "The books may hold the earlier version, so they "
                     "are recorded as stale: run `tjs run` again.",
                     prog=_PROG, cmd="tjs run")
        except Exception as _e:          # advisory, never a crash
            _say("warning", f"could not record the input fingerprint: "
                 f"{_e}", prog=_PROG)

    try:
        _fx_cash_after_run(root, cache, reports_dir)
    except Exception as _e:              # advisory guard, never a crash
        _say("warning", f"fx-cash report failed: {_e}", prog=_PROG)

    if not args.account:
        try:
            _write_exports(root, settings, reports_dir)
        except Exception as _e:          # advisory guard, never a crash
            _say("warning", f"exports not written: {_e}", prog=_PROG)

    _say_filing_reminder(cfg, settings, inputs_dir, cache, root)

    # The stages' outputs are vouched for (what the sweep and the views read).
    if not args.account:
        stage_sweep(cache, reports_dir, late=True, accounts=list(accounts))
    if ST.commit_run(cache, full=not args.account):
        _say("warning", "the rebuild records could not be written (is work/ "
             "full?): the next `tjs run` writes them again", prog=_PROG)
    if not args.account:
        # reports/ are one full run's again (lib/compare, TJ-0254).
        from taxjson.lib import compare as _cmp_done
        _cmp_done.full_run_done(root)
    _step(f"Done. Reports are in {_out_relpath(reports_dir, root)}/")


def _run_holdings_check(args: argparse.Namespace, accounts: Dict[str, AccountSettings],
                        root: Path, cfg: Dict[str, Any]) -> None:
    """A full `tjs run`'s positions checked against the broker's holdings
    files, when the project declares any: a warning, never a failing exit
    (_cmd_run)."""
    # Broker-positions cross-check, when taxjson.toml declares any
    # account's `holdings` files. A WARNING, never a failing exit:
    # same-day trades that haven't reached the CSVs yet differ
    # routinely, and a hard failure there would teach the user to
    # ignore it. Only a self-vs-external compare catches a stranded
    # position that every internal report agrees on (shares stranded
    # by a split, an option class split).
    if not args.account and (
            any(_a.holdings for _a in accounts.values())
            or _holdings_folder_in_use(root, cfg)):
        from taxjson.lib import out as _out
        from taxjson.lib.out import width as _ow
        if _ow(sys.stdout) <= 0:
            _out.put("")        # print(), PLAN step 15
        _step("Checking positions against the broker's holdings files")
        _san_kinds: Dict[str, List[str]] = {}
        _san_notes = _sanity_items_from_config(
            accounts, root,
            settings_of(cfg).year, kinds=_san_kinds)[1]
        for _n in _san_notes:
            if _n in (_san_kinds.get("info") or ()):
                # (a holdings/ snapshot of an account checked against
                # its own `holdings` files: nothing unchecked, TJ-0020)
                _say("note", *_split_msg(_n), indent="  ")
                continue
            # One unreadable holdings file drops that account from the
            # compare while sanity itself still exits 0 — say so here,
            # or the run reads as fully checked (2026-09 audit R1-324).
            _say("warning", *_split_msg(f"holdings check incomplete: {_n}"),
                 indent="  ")
        try:
            if _ow(sys.stdout) > 0:
                # Shown to a person: what differs, in a few lines
                # (`tjs sanity` has the tables).
                _sanity_console(root)
            else:
                cmd_sanity(argparse.Namespace(dir=str(root), items=[],
                                              tolerance=None, json=False,
                                              brief_extras=True))
        except SystemExit as _e:
            if isinstance(_e.code, str) and _e.code != _SANITY_REFUSED:
                # A message exit is a CONFIG problem (missing holdings
                # file, unknown account, malformed .toml) — not a
                # position difference (2026-09 CLI audit B13). Its
                # reason and the command stay on the one line the
                # default view shows: "could not run" alone named no
                # file and no fix (backlog TJ-0485).
                _why = str(_e.code).splitlines()
                _say("warning", "holdings check could not run",
                     *_why, _SANITY_FIX, indent="  ",
                     short=_sanity_failed_line(_why))
            elif _e.code == _SANITY_REFUSED or (isinstance(_e.code, int)
                                                and _e.code >= 2):
                # An input refusal (a holdings file that is not a whole
                # snapshot, a date that is no day) or a usage error (an
                # account taxjson.toml pairs with a file has no books,
                # backlog TJ-0630): its Error is above; never "positions
                # differ … missing history". On a terminal
                # _sanity_console says so: one line naming the command,
                # not that text as the reason.
                _say("warning", "holdings check could not run",
                     _SANITY_REFUSED, indent="  ")
            elif _e.code and _ow(sys.stdout) <= 0:
                # (shown to a person, _sanity_console said it)
                _say("warning", "positions differ from the broker "
                     "holdings files",
                     "On a first project the likely cause is missing "
                     "history (purchases before your download starts: "
                     "`tjs find-missing-history`, "
                     "docs/getting-started.md step 5); later, same-day "
                     "trades not yet in the CSVs are the usual one; "
                     "anything else is a booking problem (see `tjs "
                     "sanity`).", indent="  ")
        except Exception as _e:  # noqa: BLE001 — never break a run
            _say("warning", f"holdings sanity skipped: {_e}", indent="  ")


def _cmd_run(args: argparse.Namespace) -> None:
    root, cfg, accounts, settings = _run_config(args)

    inputs_dir, cache, reports_dir, _fp_started = _run_folders(root, args, cfg)
    ticker_map_arg, _tmap_parsed, sec_overrides_arg, mh_arg = _run_maps(root, accounts, cache)

    _booked_twice = _run_duplicate_inputs(inputs_dir, accounts, args)

    rates, sheltered_items, taxable_items, no_input, pending_accounts = _run_first_pass(
        settings, cache, root, inputs_dir, accounts, args, reports_dir, ticker_map_arg,
        sec_overrides_arg, mh_arg, cfg)

    sheltered_outputs, _skipped_no_input, sheltered_base, _sheltered_merge_inputs = (
        _run_sheltered(sheltered_items, settings, inputs_dir, cache, reports_dir, rates,
                       ticker_map_arg, sec_overrides_arg, mh_arg, no_input, args,
                       pending_accounts, accounts))

    taxable_outputs, _blend_names, _crypto_blend_names, _crypto_blend = _run_taxable(
        settings, accounts, cache, args, taxable_items, inputs_dir, reports_dir, rates,
        ticker_map_arg, sec_overrides_arg, mh_arg, no_input, pending_accounts,
        _skipped_no_input, sheltered_base)

    # An in-kind contribution's plan acquisition is a purchase for the
    # loss rule (CA-INKIND-04): marked in the combined book (or the book
    # written for it) once every taxable account's transfer evidence is
    # read, before the cross-account passes use it.
    if not args.account or _sheltered_merge_inputs:
        sheltered_base = stage_in_kind_context(root, cache, settings,
                                               sheltered_base)
    _record_skipped_accounts(cache, _skipped_no_input,
                             only=args.account or None)
    if (not args.account and not pending_accounts
            and not sheltered_outputs and not taxable_outputs):
        # Every account was skipped for having no inputs: "Done" with
        # exit 0 read as success on an empty project (2026-09 CLI audit).
        # The exports folder as configured (../inputs, backlog TJ-0133).
        _in = _PL.shown(_PL.inputs_dir(root), root)
        _say("warning", "no account had any input — nothing was computed",
             f"Drop broker CSV exports (or .tt files) into "
             f"{_in}/<account>/ and re-run.", prog=f"{_PROG} run",
             short=f"no account had any input — nothing was computed: put "
                   f"each broker's export in {_in}/<account>/ "
                   f"(docs/brokers.md) and re-run")
        if getattr(args, "strict", False):
            raise SystemExit(1)
        return
    _run_reports(root, settings, cache, ticker_map_arg, mh_arg, cfg, _tmap_parsed,
                 pending_accounts, args, _blend_names, reports_dir, sheltered_base,
                 _crypto_blend_names, _crypto_blend, taxable_outputs, sheltered_outputs,
                 accounts)

    _run_pending_elections(pending_accounts, cache, args, root)

    _booked_twice = _run_cross_reports(args, cache, reports_dir, accounts, sheltered_outputs,
                                       taxable_outputs, settings, sheltered_base,
                                       ticker_map_arg, mh_arg, _booked_twice, _crypto_blend)
    _run_finish(cache, settings, rates, reports_dir, sheltered_outputs, taxable_outputs, root,
                accounts, args, cfg, _booked_twice, _fp_started, inputs_dir)

    _run_holdings_check(args, accounts, root, cfg)
    if not args.account:
        _first_run_summary(root, cfg, cache, mh_arg)
    if args.account:
        # A single-account run can't do cross-account wash detection or the
        # combined cross-account reports — those are SKIPPED (previously they
        # ran on just this account's data and silently OVERWROTE the combined
        # ccd/leaps/crosslistings with one-account truncations).
        # `<account>_wash.sum` and the combined reports keep whatever the
        # last FULL run wrote.
        # Canada's rule is the superficial-loss rule (audit A2-1360).
        _rule_n = ("wash-sale" if _country(settings) == "usa"
                   else "superficial-loss")
        _say("warning", f"single-account run ({args.account}): cross-account "
             f"{_rule_n} detection and the combined cross-account reports "
             f"were skipped",
             "They keep the last full run's contents. Run `tjs run` "
             "with no --account before filing.", prog=f"{_PROG} run",
             short=f"single-account run ({args.account}) skipped the "
                   f"cross-account {_rule_n} pass, so `tjs run` fully "
                   f"before filing")


# Discrepancies the run's console names before `+N more`.
_SANITY_SHOWN = 8
# What the run says under "holdings check could not run" when sanity
# refused an input (exit 2) and printed its Error: never "no result",
# never "positions differ" (audit 2026-10-25 brokers-inputs#1).
_SANITY_REFUSED = ("The Error above names the file to fix; then "
                   "`tjs sanity`.")
# ... and under it when sanity stopped with a message (exit 1): the
# reason is the line above.
_SANITY_FIX = "Fix what it names; then `tjs sanity`."


def _sanity_failed_line(why: List[str]) -> str:
    """The default view's one line for a holdings check that stopped
    with a message: the reason (its label and a closing parenthesis
    dropped) and the command (backlog TJ-0485)."""
    from taxjson.lib.out import _CAPTURED_RE
    head = re.sub(r"\x1b\[[0-9;]*m", "", why[0] if why else "").strip()
    m = _CAPTURED_RE.match(head)
    if m:
        head = head[m.end():]
    head = re.sub(r"\s*\([^()]*\)\s*$", "", head).rstrip(" .:")
    return ("holdings check could not run"
            + (f": {head}" if head else "") + " — `tjs sanity`")


def _holdings_folder_in_use(root: Path, cfg: Dict[str, Any]) -> bool:
    """The year's holdings/ folder holds a snapshot `tjs sanity`
    compares (lib/holdings_dir)."""
    from taxjson.lib import holdings_dir as _HD
    folder, note = _HD.folder_for(root, settings_of(cfg).year)
    return folder is not None or bool(note)


def _sanity_console(root: Path) -> None:
    """The run's holdings check as a person reads it on the console:
    one message per finding with its few lines (`tjs sanity` lists
    every row). Exits as cmd_sanity does (1 on a quantity difference,
    a message on a config problem)."""
    import contextlib
    import io
    import json as _json
    buf = io.StringIO()
    code: Any = 0
    try:
        with contextlib.redirect_stdout(buf):
            cmd_sanity(argparse.Namespace(dir=str(root), items=[],
                                          tolerance=None, json=True,
                                          brief_extras=True))
    except SystemExit as e:
        code = e.code
    if isinstance(code, str):
        raise SystemExit(code)
    if isinstance(code, int) and code >= 2:
        # An input refusal (exit 2): its Error is on stderr already.
        raise SystemExit(_SANITY_REFUSED)
    try:
        doc = _json.loads(buf.getvalue())
    except ValueError:
        raise SystemExit(f"holdings check returned no result "
                         f"({buf.getvalue()[:200]!r})")
    rows = doc.get("discrepancies") or []

    def _n(k: int, one: str, many: str) -> str:
        return f"{k} {one if k == 1 else many}"
    if rows:
        first = rows if len(rows) <= _SANITY_SHOWN else rows[:5]
        shown = [f"{'+'.join(r.get('accounts') or [])} {r['symbol']}: "
                 f"taxjson {r['taxjson_qty']:g}, broker "
                 f"{r['holdings_qty']:g}" for r in first]
        if len(first) < len(rows):
            per: Dict[str, int] = {}
            for r in rows:
                a = "+".join(r.get("accounts") or [])
                per[a] = per.get(a, 0) + 1
            shown.append(f"+{len(rows) - len(first)} more"
                         + (" (by account: " + ", ".join(
                             f"{a} {k}" for a, k in sorted(per.items()))
                            + ")" if len(per) > 1 else ""))
        _say("warning", f"{_n(len(rows), 'position differs', 'positions differ')}"
             f" from the broker's holdings files", *shown,
             "`tjs sanity` lists them. Fewer shares than the broker: "
             "usually missing history (`tjs find-missing-history`); "
             "more: a trade after the last export.",
             file=sys.stdout)
    else:
        # An account with open positions and no holdings file was never
        # compared: not an all-clear for it (pre-release review L4) —
        # nor one whose `holdings` file is missing, positions or not
        # ("every checked account" read as all of them: TJ-0064).
        _unchk = list(doc.get("uncovered_accounts") or [])
        _unchk += [a for a in doc.get("accounts_not_checked") or []
                   if a not in _unchk]
        _say("note", "positions match the broker's holdings files"
             + (f" (checked accounts only; {len(_unchk)} unchecked: "
                f"{', '.join(_unchk)})" if _unchk
                else " (every checked account)"
                if not doc.get("complete", True)
                else ""),
             file=sys.stdout)
    diffs = doc.get("cost_differences") or []
    if diffs:
        unexpl = sum(1 for d in diffs if d.get("reasons") == ["unexplained"])
        _say("note", f"{len(diffs)} of "
             f"{_n((doc.get('cost') or {}).get('compared', 0), 'position', 'positions')}"
             f" differ from the reports' cost ({unexpl} unexplained)",
             "`tjs sanity` lists them with their reasons (a broker's "
             "book value is not always your ACB).", file=sys.stdout)
    inc = doc.get("income_share_mismatches") or []
    if inc:
        _say("note", f"{_n(len(inc), 'dividend row states', 'dividend rows state')}"
             f" a share count the books did not hold",
             "`tjs sanity` lists them.", file=sys.stdout)
    if rows:
        raise SystemExit(1)


def _short_positions_cands(base_json: Path, account: str,
                           ticker_map: Optional[Path],
                           missing_history: Optional[Path]) -> List[Any]:
    """A taxable account's positions that go short in its books — a sale
    with no purchase in the files, unless the broker marks it a short
    sale or codes it closing (those are said as ATTENTION lines) — read
    like find-missing-history reads them (a journal's same-day legs —
    lib/missing_history.walk_journal_symbols — and the .tt OPENING
    cost=unknown lines are not missing history). [] when the
    books cannot be read (advisory: never breaks a run)."""
    try:
        from taxjson.lib.tx_load import load_transactions
        from taxjson.lib.first_run import read_missing_history_pairs
        from taxjson.lib.missing_history import (account_types_near,
                                                  detect_missing_history,
                                                  walk_journal_symbols)
        txs = load_transactions(base_json)
        journal = walk_journal_symbols(
            Path(base_json).parent,
            ticker_map if ticker_map and Path(ticker_map).is_file()
            else None)
        covered = {(sym, a.lower()) for sym, a in
                   read_missing_history_pairs(missing_history)}

        def _cands(rows, registered):
            return [c for c in detect_missing_history(
                        rows, include_broker_shorts=True,
                        registered_accounts=registered,
                        journal_symbols=journal)
                    if c.account == account and not c.broker_marked_short
                    and not c.broker_says_closing
                    and (str(c.symbol).upper(), account.lower())
                    not in covered]
        found = _cands(txs, {account: False})
        if not found:
            return found
        # A split another taxable account reports re-denominates this
        # account's units too, as in the engine and find-missing-history
        # (backlog TJ-0832): a sale of those units is no short (TJ-1258).
        types = dict(account_types_near(base_json), **{account: False})
        splits = []
        for f in _project_base_files(Path(base_json).parent):
            if f == Path(base_json):
                continue
            try:
                splits += [t for t in load_transactions(f)
                           if t.action == "SPLIT" and t.account != account
                           and types.get(t.account) is False]
            except (OSError, ValueError):
                continue
        return _cands(txs + splits, types) if splits else found
    except Exception:                               # noqa: BLE001
        return []


def _short_positions_note(account: str, symbols: List[str],
                          booked: str = "open") -> Tuple[str, ...]:
    """The note (headline, detail) naming a taxable account's positions
    that go short (_short_positions_cands) and bear on the tax year.
    `booked`: what the gains engine did with their sales
    (lib/first_run.engine_booking, the split the run's closing summary
    and `tjs sum` say too): "open" — not in any total —,
    "short_cover" — a short sale a later purchase closed, in the totals
    — or "matched" — sold from a purchase in the files."""
    shown = ", ".join(symbols[:5]) \
        + (f" +{len(symbols) - 5} more" if len(symbols) > 5 else "")
    if booked == "short_cover":
        return (f"{len(symbols)} position(s) go short in {account}'s data "
                f"({shown}): booked as short sales closed by a later "
                f"purchase",
                "Sales with no purchase in your files, unless they were "
                "real short sales. Their gain is in the totals at the "
                "covering purchase's cost — not yours if you held the "
                "shares before your files start; `tjs "
                "find-missing-history` lists them with the fixes.")
    if booked == "matched":
        return (f"{len(symbols)} position(s) read short in {account}'s "
                f"data by the missing-history check ({shown})",
                "The gains engine sold them from purchases in your files, "
                "so they are in the totals; the check reads that day's "
                "rows in another order: compare it with the broker's "
                "trades.")
    return (f"{len(symbols)} position(s) go short in {account}'s data "
            f"({shown})",
            "Sales with no purchase in your files, unless they were real "
            "short sales. Until the purchase is supplied their gain is in "
            "no total — `tjs find-missing-history` lists them with "
            "the fixes.")


def _short_blocks(gains_json: Path) -> List[List[str]]:
    """The `ATTENTION: short:` messages of a gains stage's .diag, each
    with its continuation lines."""
    from taxjson.lib.pipeline import ATTENTION_SHORT
    out: List[List[str]] = []
    cur: Optional[List[str]] = None
    for line in _diag_lines(gains_json):
        if cur is not None and line[:1] in (" ", "\t") and line.strip():
            cur.append(line)
            continue
        cur = None
        if line.startswith(ATTENTION_SHORT):
            cur = [line]
            out.append(cur)
    return out


def _report_short_positions(root: Path, settings: Settings,
                            cache: Path, ticker_map: Optional[Path],
                            missing_history: Optional[Path]) -> None:
    """The positions that go short in this run's books (sales with no
    purchase in the files), once, after every account's books: the ones
    that bear on the tax year one by one — a row of the year draws on
    the missing purchase or touches the position at all, or (Canada) an
    other taxable account of its ACB pool trades it that year; the test
    find-missing-history sorts by (lib/missing_history.
    classify_year_shorts) — and the rest in ONE line that names the
    command recording them. Display only: each gains stage's .diag and
    the .sum DIAGNOSTICS keep every line (owner request: a position
    that went short years ago and never comes up again is noise)."""
    sources = list(run_state.current.short_sources)
    run_state.current.short_sources.clear()
    year = settings.year
    blocks: List[Tuple[Tuple[str, str], List[str]]] = []
    unmarked: List[Tuple[str, List[Any]]] = []
    for name, base_json, gains_json, taxable_equity in sources:
        for b in _short_blocks(gains_json):
            m = _SHORT_KEY_RE.match(b[0])
            blocks.append(((m["sym"], m["acct"]) if m else ("", ""), b))
        if taxable_equity:
            unmarked.append((name, _short_positions_cands(
                base_json, name, ticker_map, missing_history)))
    if not blocks and not any(c for _n, c in unmarked):
        return
    rows = (_year_short_rows(root, _project_base_files(cache), year)
            if year else {})

    def _listed(key) -> bool:
        r = rows.get(key)
        return r is None or r.year_listed

    shown_blocks, outside = [], {}
    for key, b in blocks:
        if _listed(key):
            shown_blocks.append(b)
        else:
            outside[key] = rows[key]
            # (a later pass's echo of it — the blended pass, a failed
            # stage — is not shown either)
            run_state.current.shown.add(("short",) + key)
    # What the gains engine booked for each listed pair (the split the
    # closing summary and `tjs sum` make, lib/first_run.
    # engine_booking): a short a later purchase of the year closed is IN
    # the totals — "in no total" said for it contradicted both. This
    # run's plain gains files: the cross-account wash pass has not yet
    # rebuilt <acct>_gains_wash.json, the last run's (third pre-release
    # review, finding 5).
    booked: Dict[Tuple[str, str], str] = {}
    if rows:
        try:
            from taxjson.lib.first_run import engine_booking
            booked = engine_booking(
                cache, [rows[(c.symbol, c.account)]
                        for _n, cands in unmarked for c in cands
                        if (c.symbol, c.account) in rows],
                year, date_basis=_tax_date_basis(settings),
                prefer_wash=False)
        except Exception:                           # noqa: BLE001
            booked = {}
    notes: List[Tuple[str, List[str], str]] = []
    for name, cands in unmarked:
        keep: Dict[str, List[str]] = {}
        for c in cands:
            key = (c.symbol, c.account)
            if _listed(key):
                keep.setdefault(booked.get(key, "open"), []).append(
                    c.symbol)
            else:
                outside[key] = rows[key]
        for how in ("open", "short_cover", "matched"):
            if keep.get(how):
                notes.append((name, keep[how], how))
    _step("Checking for missing purchase history")
    for b in shown_blocks:
        for line in b:
            _echo_captured(line, once=True)
    for name, syms, how in notes:
        _say("note", *_short_positions_note(name, syms, how), indent="  ",
             file=sys.stdout)
    if notes:
        # A day a TOBASE line's two listings trade opposite ways with no
        # journal on it reads in clock order: the line to add when the
        # units were journaled (lib/missing_history.
        # undeclared_journal_days).
        try:
            from taxjson.lib.missing_history import undeclared_journal_days
            _named = {n[0] for n in notes}
            _undecl = [u for u in undeclared_journal_days(cache, ticker_map)
                       if u[0] in _named]
        except Exception:                           # noqa: BLE001
            _undecl = []
        if _undecl:
            _say("note", f"{len(_undecl)} day(s) with a buy of one listing "
                 f"and a sale of the other that a ticker.map TOBASE line "
                 f"joins, the same quantity, and no journal",
                 "A TOBASE line says two listings are one security, not "
                 "that units moved between them: such a day reads in "
                 "clock order, and a sale stamped before the buy is a "
                 "sale with no purchase. If the units were journaled (a "
                 "Norbert's gambit), declare each journal in a .tt file "
                 "of its account: "
                 + "; ".join(f"`JOURNAL {d} {b} {s_} {q:g}` ({a})"
                             for a, d, b, s_, q in _undecl[:5])
                 + (" ..." if len(_undecl) > 5 else "") + ".",
                 indent="  ", file=sys.stdout)
    # A cover traded the year before and delivered in this one: its loss
    # is this year's (US-DATE-01), though the walk dates it by its trade
    # (backlog TJ-1266).
    in_year = _with_a_gain_in_year(cache, outside, year) if outside else []
    if in_year:
        n = len(in_year)
        _say("note", f"{n} position{'' if n == 1 else 's'} went short "
             f"before {year} with missing history; the cover's loss is in "
             f"{year}'s numbers",
             f"No purchase in your files. The cover was traded before {year} "
             f"and delivered in it, which dates a short's loss. `tjs gains` "
             f"has the row; `tjs find-missing-history` lists the position.",
             indent="  ", file=sys.stdout)
        outside = {k: v for k, v in outside.items() if k not in in_year}
    if outside:
        n = len(outside)
        when = ("before" if all(
            (r.candidate.first_negative_date or "") < f"{year}-01-01"
            for r in outside.values()) else "outside")
        _say("note", f"{n} position{'' if n == 1 else 's'} went short "
             f"{when} {year} with missing history; no effect on {year}'s "
             f"numbers",
             f"No purchase in your files and no {year} activity. `tjs "
             f"find-missing-history` lists {'it' if n == 1 else 'them'}; "
             f"`tjs find-missing-history --write-missing-history "
             f"--outside-year` records {'it' if n == 1 else 'them'} in "
             f"inputs/<account>/missing_history.tt so the run stops listing "
             f"{'it' if n == 1 else 'them'}.", indent="  ",
             file=sys.stdout)


def _with_a_gain_in_year(cache: Path, keys: Iterable[Tuple[str, str]],
                         year: Any) -> List[Tuple[str, str]]:
    """Of the (symbol, account) `keys`, those whose plain gains file has a
    disposition dated in `year`."""
    import json
    from taxjson.lib.report_model import resolve_gains_files
    try:
        files = resolve_gains_files(cache, prefer_wash=False)
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for sym, acct in keys:
        try:
            doc = json.loads(Path(files[acct]).read_text(encoding="utf-8"))
        except (KeyError, OSError, TypeError, ValueError):
            continue
        rows = doc.get("transactions") if isinstance(doc, dict) else None
        if any(isinstance(t, dict) and t.get("qty") and t.get("symbol") == sym
               and str(t.get("date") or "").startswith(str(year))
               for t in rows or []):
            out.append((sym, acct))
    return out


def _first_run_summary(root: Path, cfg: Dict[str, Any], cache: Path,
                       mh_file: Optional[Path]) -> None:
    """What the books show is still incomplete, each with the command
    that lists it (lib/first_run): printed after a full run when any
    count is nonzero, silent when clean; the counts also go to
    reports/run_summary.json. Advisory — never breaks a run."""
    from taxjson.lib import first_run as FR
    try:
        doc = FR.collect(root, cfg, missing_history=mh_file,
                         arrivals=transfer_arrivals(root, cache,
                                                    accounts_of(cfg)))
    except Exception as e:                          # noqa: BLE001
        emit_line(f"tjs: warning: the closing summary could not be built "
                  f"({type(e).__name__}: {e}).", file=sys.stderr)
        return
    import json as _json
    try:
        # In reports/, not work/: every work/*.json is read as a stage
        # file by the fees report (and a planted symlink there must not
        # break the next run's fees stage). Written when it changed.
        _summary = cache.parent / "reports" / FR.SUMMARY_FILE
        if not (_summary.is_dir() and not _summary.is_symlink()):
            from taxjson.lib.safe_write import write_changed
            write_changed(_summary, _json.dumps(doc, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass
    from taxjson.lib.out import show_blocks
    # (a project with only .tt OPENING cost=unknown lines: named so)
    show_blocks(FR.render_blocks(doc, mh_name="missing_history.tt",
                                 concise=_run_concise(sys.stdout),
                                 details_hint=run_state.current.details_hidden),
                sys.stdout)


def _manifest_path_for(acct_dir: Path, cache: Path, name: str) -> Path:
    """The corp-action elections manifest the pipeline uses for an account.
    Delegates to _resolve_manifest (read-only mode) so `tjs elect` and
    the run pipeline can never disagree about which file is live — a
    legacy work/ manifest is migrated to inputs/ on first touch."""
    return _resolve_manifest(acct_dir, cache, name, create=False)


def _load_pending_agg(path: Path) -> Dict[str, Any]:
    """work/pending_elections.json, shape-checked: an object whose
    `accounts` maps each account to {"pending": [event, ...]}, each
    event an object with a text `event_id` and `options` a list of
    objects with a text `election`. Anything else (truncated, not
    UTF-8, a directory, a list, a pending row that is a number) is an
    InputFileError naming the file — elect --pending, elect --set and
    run --account read it bare and tracebacked (re-audit A2-0471,
    A2-0779, A2-1419, A2-1439, A2-1457, A2-1460)."""
    from taxjson.lib.json_input import InputFileError, read_json_doc
    doc = read_json_doc(path, list_key=None)
    accts = doc.get("accounts")
    if accts is None:
        doc["accounts"] = accts = {}
    if not isinstance(accts, dict):
        raise InputFileError(f'{path}: "accounts" must be an object')
    for a, ad in accts.items():
        if not isinstance(ad, dict):
            raise InputFileError(f'{path}: account {a!r} must be an '
                                 f'object')
        pend = ad.get("pending")
        if pend is None:
            ad["pending"] = pend = []
        if not isinstance(pend, list):
            raise InputFileError(f'{path}: {a}: "pending" must be a list')
        for i, ev in enumerate(pend):
            if not isinstance(ev, dict) \
                    or not isinstance(ev.get("event_id"), str):
                raise InputFileError(f'{path}: {a}: pending entry {i} '
                                     f'must be an object with an '
                                     f'"event_id"')
            opts = ev.get("options")
            if opts is None:
                ev["options"] = opts = []
            if not isinstance(opts, list) or any(
                    not isinstance(o, dict)
                    or not isinstance(o.get("election"), str)
                    or not isinstance(o.get("hints") or [], list)
                    or any(not isinstance(h, dict)
                           or not isinstance(h.get("key"), str)
                           for h in (o.get("hints") or []))
                    for o in opts):
                raise InputFileError(f'{path}: {a}: {ev["event_id"]}: '
                                     f'"options" must be a list of '
                                     f'{{"election": ...}} objects')
    return doc


# Native (pre-base) per-account transaction file, in preference order: the raw
# equity merge, then the crypto native (post fill-crypto-prices), then sorted.
# These carry NATIVE currency with NO TOBASE / currency-to-base mapping —
# exactly what `tjs events` shows.
_NATIVE_TX_SUFFIXES = ("_raw.json", "_filled.json", "_sorted.json")


def _native_tx_file(cache: Path, account: str) -> Optional[Path]:
    for suf in _NATIVE_TX_SUFFIXES:
        p = cache / f"{account}{suf}"
        if p.exists():
            return p
    return None


def _locked_grant_since(root: Path, settings: Settings
                        ) -> Optional[Tuple[int, str]]:
    """(since, lock label) from the most recent filed-year lock — the
    project's own or the one [settings] prior_year_record names — that
    records grant timing with an option_grant_since; None otherwise."""
    from taxjson.lib import filed_locks as _tfl
    try:
        locks = _tfl.project_locks(root, settings)
    except _tfl.PriorRecordError:
        return None
    for _y, p, _w in sorted(locks, key=lambda t: t[0], reverse=True):
        try:
            # The one lock check (read_lock): a damaged or other-country
            # lock is not the year to copy (backlog TJ-0069).
            ot = _tfl.read_lock(p, settings,
                                prior=_w != "local").get("option_timing")
        except ValueError:              # LockProblem; a bad country
            continue
        if not isinstance(ot, dict) or str(
                ot.get("option_premium_timing") or "").lower() != "grant":
            continue
        try:
            return int(ot.get("option_grant_since")), \
                _tfl.lock_label(root, p)
        except (TypeError, ValueError):
            continue
    return None


def _books_write_options(root: Path, accounts: Mapping[str, AccountSettings]
                         ) -> Optional[bool]:
    """Whether a taxable equity account's books (work/<acct>_base.json)
    hold a written option (lib/option_boundary.write_lots): True /
    False, or None when no such book can be read."""
    import json as _json
    from taxjson.model.transaction import TaxTransaction
    from taxjson.lib.option_boundary import write_lots
    fields = TaxTransaction.__dataclass_fields__
    seen = False
    for name, acfg in sorted((accounts or {}).items()):
        if not acfg.taxable or acfg.crypto:
            continue
        try:
            doc = _json.loads((Path(root) / "work" / f"{name}_base.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        raw = doc.get("transactions") if isinstance(doc, dict) else doc
        txs = []
        for r in raw if isinstance(raw, list) else []:
            if isinstance(r, dict):
                try:
                    txs.append(TaxTransaction(
                        **{k: v for k, v in r.items() if k in fields}))
                except TypeError:
                    continue
        seen = True
        if write_lots(txs):
            return True
    return False if seen else None


def _grant_since_after_year(settings: Settings) -> Optional[str]:
    """One line when a Canadian project's option_grant_timing_since is
    later than its year: its written options are then taxed at the
    close, not when written — usually `year` lowered (filing an earlier
    year) with the key left as an earlier project wrote it."""
    if _country(settings) in ("us", "usa"):
        return None
    since, yr = settings.option_grant_timing_since, \
        settings.year
    if not (isinstance(since, int) and isinstance(yr, int)) \
            or isinstance(since, bool) or isinstance(yr, bool) \
            or since <= yr:
        return None
    if premium_timing(settings) != "grant":
        return None
    return (f"option_grant_timing_since = {since} is after year = {yr}: "
            f"{yr}'s written options are taxed at the close (right only "
            f"if {yr} was filed that way)")


def _grant_since_warning(settings: Settings,
                         root: Optional[Path] = None,
                         accounts: Optional[Dict[str, Any]] = None
                         ) -> Optional[str]:
    """The warning for a Canada project on grant timing with no explicit
    `option_grant_timing_since`: the default is the PROJECT year, which
    moves every year — consecutive default projects tax a year-straddling
    premium twice (2026-09 audit: +399 in 2025, +298 in 2026, for a 298
    economic gain). None when the key is set or does not apply — also
    when `accounts` (the [accounts] table) has no non-crypto taxable
    account: a crypto-only project writes no options (one gate for run,
    carryover and option-boundary, A2-1144)."""
    if _country(settings) in (
            "us", "usa"):
        return None
    accounts = typed_accounts(accounts) if accounts is not None else None
    if accounts is not None and not any(
            _c.taxable and not _c.crypto for _c in accounts.values()):
        return None
    if premium_timing(settings) != "grant":
        return None
    if settings.option_grant_timing_since not in (None, ""):
        return None
    if root is not None and accounts is not None \
            and _books_write_options(root, accounts) is not True:
        # No written option in the books, or no taxable equity books at
        # all (the scaffold's empty margin account, skipped: a crypto-
        # only use, backlog TJ-0162): the default (the project year)
        # decides nothing (init leaves the key commented).
        return None
    yr = settings.year
    if not isinstance(yr, int) or isinstance(yr, bool):
        # No year: the missing year is the real error (run and
        # option-boundary say so); never suggest "since = None"
        # (S044-06).
        return None
    # A filed-year lock that records an earlier `since` is the year to
    # copy: "e.g. <project year>" put last year's year-straddling
    # contracts back on close timing and taxed their premium twice
    # (A2-1142).
    locked = _locked_grant_since(root, settings) if root else None
    example = (f"{locked[0]}, the year {locked[1]} records"
               if locked else f"{yr}")
    return (f"[settings] option_grant_timing_since is not set, so grant "
            f"timing (ITA s.49(1)) starts at the project year ({yr}) — a "
            f"default that MOVES when you bump `year`: next year's project "
            f"would put this year's year-straddling written options back "
            f"on close timing and tax their premium a second time. Add "
            f"`option_grant_timing_since = <first year you file under "
            f"grant timing>` (e.g. {example}) to [settings] once and keep "
            f"it unchanged in every later year's project.")


def _write_dated_events_state(root: Path, cfg: Dict[str, Any], cache: Path,
                              tmap: Any) -> List[Dict[str, Any]]:
    """work/dated_events.state (lib/dated_events): every declared journal
    with its status, and every rename event the books carry (each
    account's base rows) with its source and declarations, plus each
    declaration no account's books carried. Returns the rename records."""
    from taxjson.lib import dated_events as DE
    from taxjson.lib.renames import _book_rows
    decl = run_state.current.dated_events.get(cache.resolve())
    if decl is not None and decl.events:
        # Every event, the map's included, merged (resolve_renames).
        declared = list(decl.events)
    else:
        declared = list(getattr(tmap, "dated", ()) or ()) if tmap else []
        declared += list(decl.renames) if decl is not None else []
    try:
        rows = _book_rows(root, cfg)
    except ValueError:
        rows = []
    mapping: Dict[str, str] = {}
    if tmap is not None:
        from taxjson.lib.ticker_map_rules import merge_renames
        try:
            mapping = merge_renames(tmap, to_base=True)
        except ValueError:
            mapping = {}
    records = DE.rename_records(rows, declared, mapping)
    doc = DE.state_doc(decl.journals if decl is not None else (), records)
    import json
    text = json.dumps(doc, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"
    path = cache / DE.STATE
    if _read_work_stamp(path) != text:
        _write_work_stamp(path, text)
    return records


def _warn_unused_renames(records: List[Dict[str, Any]], *,
                         strict: bool = False) -> None:
    """A declared rename no account's books carry (a typo of the symbol,
    a date after the last OLD row was sold, an account of the other kind):
    one Warning per declaration, naming its line (second pre-release
    review, 11); --strict stops, like the other unresolved rename items
    (third pre-release review, 7)."""
    unused = [r for r in records if r.get("status") == "unused"]
    for r in unused:
        where = ", ".join(r.get("where") or []) or "a declaration"
        _say_once(("rename-unused", where, r["old"], r["new"]), "warning",
                  f"{where}: RENAME {r['date']} {r['old']} {r['new']} books "
                  f"nothing",
                  f"No account's books hold {r['old']} before "
                  f"{r['date']} (in an account of the line's kind: "
                  f"securities or crypto) — check the symbols (a typo?) "
                  f"and the date, or delete the line. `tjs renames` "
                  f"lists it.", prog=_PROG)
    if unused and strict:
        _die(f"--strict: {len(unused)} declared rename(s) book nothing "
             f"— aborting",
             "See the warnings above and `tjs renames --pending`.")


def _check_renamed_late(root: Path, *, strict: bool) -> None:
    """A trade in a renamed ticker after its rename date that no
    declaration resolves (a .tt `RENAME <date> OLD NEW late=...` line, or
    a legacy ticker.map one: lib/renames, A2-0197): ATTENTION, and
    --strict stops."""
    from taxjson.lib.renames import unresolved_late
    try:
        rows = unresolved_late(root, load_config(root))
    except ValueError as e:
        _say("warning", *_split_msg(str(e)), indent="  ")
        return
    seen: Dict[Tuple[str, str], int] = {}
    for x in rows:
        k = (x["symbol"], x["rename_date"])
        seen[k] = seen.get(k, 0) + 1
    for (sym, d), n in sorted(seen.items()):
        new = next(x["renamed_to"] for x in rows
                   if (x["symbol"], x["rename_date"]) == (sym, d))
        _say("attention", f"{n} trade(s) in {sym} after its rename to "
             f"{new} on {d}",
             "Booked as a separate security until a .tt RENAME line says "
             "which it is (`late=fold` or `late=separate`; `tjs "
             "renames` prints the lines).", indent="  ")
    if rows and strict:
        _die(f"--strict: {len(rows)} trade(s) in a renamed ticker after "
             f"its rename are not declared — aborting",
             "See the warnings above and `tjs renames`.")


def _filed_run_gains(cmd_tail, out_path):
    """taxjson-gains invocation for the filed-year recompute."""
    run_to_file(_cmd("taxjson-gains") + cmd_tail, Path(out_path),
                capture_diag=False)


def _child_error_line(e: BaseException) -> str:
    """One line for a failed sub-tool: the child's own last error line,
    never the CalledProcessError repr with the full argv (A2-0676)."""
    if isinstance(e, subprocess.CalledProcessError):
        lines = [ln.strip() for ln in str(e.stderr or "").splitlines()
                 if ln.strip()]
        if lines:
            return lines[-1]
        return f"the gains engine exited {e.returncode}"
    return f"{type(e).__name__}: {e}"


def _recompute_hint(said: str) -> str:
    """What to do about a filed year's recompute that failed, said as
    `said`: fix the input it names, or, for exact money's stop, report
    a bug in taxjson (TJ-3316)."""
    from taxjson.model import money as M
    if M.STOPPED in said:
        return ("The recompute stopped on a bug in taxjson: report it with "
                "that line (the lock itself was not judged).")
    return "Fix that input and re-run `tjs run` (the lock itself was not judged)."


def _frozen_inputs_changed(root: Path, frozen: Dict[str, str]
                           ) -> List[str]:
    """The frozen inputs (a lock's `frozen.inputs` hashes) that differ
    from the project's files now: changed, added or missing, by name.
    [] when the project cannot be fingerprinted."""
    from taxjson.lib.checklist import (fingerprint_changes,
                                       unrecorded_generated)
    try:
        now = _soft_fingerprint(root)
    except Exception:                                   # noqa: BLE001
        return []
    if now is None:
        return []
    return fingerprint_changes(frozen,
                               unrecorded_generated(root, frozen, now))


# A date on an export line, in each form a supported export writes
# (the parsers' date formats): the year is the group named `y`; `m`
# and `d`, when there, are checked as a month and a day (YYYYMMDD and
# YYYY/MM/DD are also a run of digits); a 2-digit year reads as
# strptime's %y does.
_LINE_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
_LINE_DATE_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    # ISO (Questrade, Kraken, Coinbase, IB, .tt lines)
    r"(?<!\d)(?P<y>(?:19|20)\d\d)-\d\d-\d\d(?!\d)",
    # YYYYMMDD (IB Flex)
    r"(?<!\d)(?P<y>(?:19|20)\d\d)(?P<m>\d\d)(?P<d>\d\d)(?!\d)",
    # YYYY/MM/DD (RBC Direct)
    r"(?<![\d/])(?P<y>(?:19|20)\d\d)/(?P<m>\d\d?)/(?P<d>\d\d?)(?!\d)",
    # M/D/YYYY or D/M/YYYY (RBC Direct, a generic mapping)
    r"(?<![\d/])\d{1,2}/\d{1,2}/(?P<y>(?:19|20)\d\d)(?!\d)",
    # DD-MM-YYYY (Webull; backlog TJ-0574) or MM-DD-YYYY
    r"(?<![\d-])\d{1,2}-\d{1,2}-(?P<y>(?:19|20)\d\d)(?!\d)",
    # D.M.YYYY (a generic mapping)
    r"(?<![\d.])\d{1,2}\.\d{1,2}\.(?P<y>(?:19|20)\d\d)(?!\d)",
    # January 30, 2025 / Jan 30, 2025 (RBC Direct)
    rf"(?<![a-z]){_LINE_MONTH}[a-z]*\.? \d{{1,2}}, "
    rf"(?P<y>(?:19|20)\d\d)(?!\d)",
    # 10-Jan-22 / 10-Jan-2022 (RBC Direct)
    rf"(?<!\d)\d{{1,2}}-{_LINE_MONTH}-(?P<y>(?:19|20)\d\d|\d\d)(?!\d)",
))


def _line_year(line: str) -> Optional[int]:
    """The earliest year an export line is dated in (a row's trade
    date, before its settlement date), None for a line with no date (a
    header, a total). Every date form a supported export writes counts
    (_LINE_DATE_RES): a Webull row's DD-MM-YYYY was no date, so a
    correction of it showed nowhere (backlog TJ-0574)."""
    years = []
    for rx in _LINE_DATE_RES:
        for m in rx.finditer(line):
            g = m.groupdict()
            if g.get("m") is not None and not (
                    1 <= int(g["m"]) <= 12 and 1 <= int(g["d"]) <= 31):
                continue
            y = int(g["y"])
            if len(g["y"]) == 2:
                y += 2000 if y < 69 else 1900
            years.append(y)
    return min(years) if years else None


def _frozen_year_accounts(root: Path, fz: Dict[str, Any]) -> List[str]:
    """The accounts of a year closed with its inputs frozen: those the
    freeze copied a file of (the lock's inputs/<account>/<file> keys)
    and those its taxjson.toml names (an account with no export at the
    close). Only the frozen ones when taxjson.toml cannot be read."""
    pre = f"{_PL.INPUTS}/"
    out = {k[len(pre):].split("/")[0] for k in (fz.get("inputs") or {})
           if k.startswith(pre) and k.count("/") == 2}
    try:
        doc = read_toml(root / "taxjson.toml")
    except Exception:                                   # noqa: BLE001
        doc = {}
    accts = doc.get("accounts") if isinstance(doc, dict) else None
    if isinstance(accts, dict):
        out |= {str(a) for a in accts if str(a) and "/" not in str(a)}
    return sorted(out)


def _frozen_shared_src(root: Path, fz: Dict[str, Any]) -> Optional[Path]:
    """The shared folder a filed year's frozen inputs were copied from:
    the lock's `inputs_dir`; for a year frozen as a single folder and
    then moved by `migrate --to-years` (its lock names none), the
    inputs/ beside the year folders the later years read, as new-year
    and check-dates take it (lib/project_layout.migrated_shared_inputs;
    backlog TJ-0610). None for a partial lock or no shared folder."""
    if fz.get("partial"):
        return None
    v = fz.get("inputs_dir")
    if isinstance(v, str) and v.strip():
        src = Path(v.strip()).expanduser()
        return src if src.is_absolute() else root / src
    return _PL.migrated_shared_inputs(root, fz)


def _shared_exports_moved(root: Path, fz: Dict[str, Any], year: int
                          ) -> List[str]:
    """The shared exports whose rows of `year` or earlier differ from
    what the filed year was closed with (_shared_inputs_moved's
    `exports`)."""
    return _shared_inputs_moved(root, fz, year)["exports"]


def _mapping_views(root: Path, mine: Path, theirs: Path, then: Optional[Path]
                   ) -> Tuple[bool, Optional[Tuple[str, BaseException]]]:
    """Whether the year's copy of a CSV (`mine`, its mapping file `then`
    when one exists) and the shared one (`theirs`) are read with the same
    mapping: the mapping as the importer reads it, the layout a file
    names included, this project's layouts on both sides (generic.
    mapping_documents; a layout edit is a mapping edit, TJ-2138). Equal
    bytes, the layout's included, are read alike and judged no further.
    (same, error): the error names which copy ('theirs' or 'mine') could
    not be judged."""
    from taxjson.lib.brokerages.generic import mapping_bytes, mapping_documents
    has_mine = then is not None and then.is_file()
    try:
        if has_mine and mapping_bytes(mine, project=root) == mapping_bytes(
                theirs, project=root):
            return True, None
        theirs_doc = mapping_documents(theirs, project=root)
    except (OSError, ValueError) as e:
        return True, ("theirs", e)
    try:
        mine_doc = mapping_documents(mine, project=root) if has_mine else None
    except (OSError, ValueError) as e:
        return True, ("mine", e)
    return mine_doc == theirs_doc, None


def _shared_inputs_moved(root: Path, fz: Dict[str, Any], year: int
                         ) -> Dict[str, List[Any]]:
    """What changed for `year` in the shared folder a filed year's
    inputs were frozen from, each file shown from `root`:

    `exports` — the shared exports whose rows of `year` or earlier
    differ from what the filed year was closed with: each .csv / .tt
    of the lock's frozen inputs (inputs/<account>/<file>) against its
    namesake in the shared folder the freeze copied it from
    (`inputs_dir`), row by row and counting repeats — a row of the year
    changed, removed (one of two identical rows too, backlog TJ-0587)
    or added (the late-December trades a re-download has, backlog
    TJ-0421) — and each export put in an account's shared folder since
    the close that holds a row of the year no frozen copy of the
    account has (TJ-0421). A correction saved there (a sale's proceeds,
    the cost of a lot sold that year, a missing sale) reaches neither
    the filed year, which reads its copy, nor the next year's handoff,
    which compares Dec 31 positions and cost (backlog TJ-0091). Rows of
    later years are no difference, nor is a row added to one file that
    another frozen copy of the account has (a moved row); a namesake no
    longer there is not judged. Each file is read as the parsers read
    it (brokerages/base.decode_broker_text): a UTF-16 export was read
    as UTF-8, no row of it had a date, and its correction showed
    nowhere (backlog TJ-0707).

    `mappings` — the shared generic mappings (<csv>.toml, generic.toml)
    that now read a frozen CSV holding a row of `year` or earlier
    otherwise than the mapping the year was closed with (another
    column, action or format; a mapping new since the close): the CSV's
    bytes are the same, its figures are not (backlog TJ-0708). Which
    mapping reads a CSV: brokerages/detect.generic_mapping, on both
    sides; the mappings are compared as parsed TOML (a comment or the
    layout is no difference).

    `unread` — [(file, why)]: a file of the comparison that could not
    be read (not UTF-8 or UTF-16 text, not TOML, no permission): not
    judged, so named, never passed as unchanged (TJ-0707).

    The shared folder: _frozen_shared_src (a year frozen as a single
    folder, then migrated: ../inputs, backlog TJ-0610)."""
    from collections import Counter
    from taxjson.lib import crypto_sends as _CS
    from taxjson.lib.brokerages.base import (BrokerageParseError,
                                             decode_broker_text)
    from taxjson.lib.brokerages.detect import generic_mapping
    from taxjson.lib.checklist import _skipped_input_name
    res: Dict[str, List[Any]] = {"exports": [], "mappings": [],
                                 "unread": []}
    src = _frozen_shared_src(root, fz)
    if src is None:
        return res
    pre = f"{_PL.INPUTS}/"

    def _export(name: str) -> bool:
        return (Path(name).suffix.lower() in (".csv", ".tt")
                and name.lower() != _CS.TT_NAME
                and not _skipped_input_name(name))

    def _why(e: BaseException) -> str:
        if isinstance(e, BrokerageParseError):
            return "not UTF-8 or UTF-16 text"
        if isinstance(e, OSError):
            return str(e.strerror or e)
        return " ".join(str(e).split())

    def _rows(p: Path) -> "Counter[str]":
        """The rows of `year` or earlier, each with its count.
        OSError / BrokerageParseError when it cannot be read."""
        out: "Counter[str]" = Counter()
        for ln in decode_broker_text(p.read_bytes(), p.name).splitlines():
            ln = ln.rstrip()
            if not ln.strip() or (p.suffix.lower() == ".tt"
                                  and ln.lstrip().startswith("#")):
                continue
            if (_line_year(ln) or year + 1) <= year:
                out[ln] += 1
        return out

    def _unread(p: Path, e: BaseException) -> None:
        item = (_PL.shown(p, root), _why(e))
        if item not in res["unread"]:
            res["unread"].append(item)

    frozen: Dict[str, List[str]] = {}
    for key in fz.get("inputs") or {}:
        if key.startswith(pre) and key.count("/") == 2:
            acct, name = key[len(pre):].split("/")
            frozen.setdefault(acct, []).append(name)
    for acct in _frozen_year_accounts(root, fz):
        names = sorted(n for n in frozen.get(acct, []) if _export(n))
        mine: Dict[str, "Counter[str]"] = {}
        for n in names:
            p = root / _PL.INPUTS / acct / n
            try:
                mine[n] = _rows(p)
            except FileNotFoundError:
                continue            # the frozen-input check names it
            except (OSError, BrokerageParseError) as e:
                _unread(p, e)
        folder = src / acct
        try:
            added_files = sorted(p.name for p in folder.iterdir()
                                 if p.is_file() and _export(p.name)
                                 and p.name not in frozen.get(acct, []))
        except (FileNotFoundError, NotADirectoryError):
            added_files = []
        except OSError as e:
            _unread(folder, e)
            added_files = []
        for n in [x for x in names if x in mine] + added_files:
            theirs = folder / n
            try:
                if not theirs.is_file():
                    continue
                if n in mine and (root / _PL.INPUTS / acct / n
                                  ).read_bytes() == theirs.read_bytes():
                    continue
                have = _rows(theirs)
            except (OSError, BrokerageParseError) as e:
                _unread(theirs, e)
                continue
            had = mine.get(n, Counter())
            # A row another frozen copy of the account has is not new
            # to the year (moved between files, an overlapping export).
            others = {ln for m, c in mine.items() if m != n for ln in c}
            if (had - have) or any(ln not in others
                                   for ln in (have - had)):
                res["exports"].append(_PL.shown(theirs, root))
        # The generic mapping each frozen CSV of the year is read with,
        # then and now (a CSV that could not be read counts as one of
        # the year: it is not known to hold none).
        for n in names:
            if Path(n).suffix.lower() != ".csv" or (
                    n in mine and not mine[n]):
                continue
            then = generic_mapping(root / _PL.INPUTS / acct / n, root)
            now = (generic_mapping(folder / n, root)
                   if (folder / n).is_file() else None)
            if now is None:
                continue            # no longer there: not judged
            shown = _PL.shown(now, root)
            if shown in res["mappings"]:
                continue
            if not now.is_file():
                continue            # a dangling link: the run says so
            same, err = _mapping_views(root, root / _PL.INPUTS / acct / n,
                                       folder / n, then)
            if err is not None:
                _unread(now if err[0] == "theirs" else then, err[1])
                continue
            if not same:
                res["mappings"].append(shown)
    return res


# The decisions files a frozen year copied from the shared folder, and
# the table of entries each keeps (lib/corp_actions.Manifest,
# lib/crypto_sends.load_decisions).
_DECISION_FILES = {"manifest.json": "elections", "sends.json": "sends"}
_ENTRY_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})-[01]\d-[0-3]\d(?!\d)")


def _decisions_moved(mine: Optional[Path], theirs: Path, table: str,
                     year: int) -> List[str]:
    """The entries (ids) of a shared decisions file that differ from
    the filed year's frozen copy `mine` (None: it had none) and concern
    `year` or earlier: a decision changed, removed or added since the
    close. Later years rewrite these files whole, so the comparison is
    per entry, never of the bytes (backlog TJ-0211): an entry dated
    after `year` (the date in its summary — an election's event, a
    send's — else in its id) is no difference, nor is an added one with
    no date. ValueError / OSError when a file cannot be read."""
    import json as _json

    def _entries(p: Optional[Path]) -> Dict[str, Any]:
        if p is None:
            return {}
        doc = _json.loads(p.read_text(encoding="utf-8-sig"))
        t = doc.get(table) if isinstance(doc, dict) else None
        return t if isinstance(t, dict) else {}
    a, b = _entries(mine), _entries(theirs)
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) == b.get(k):
            continue
        years = [int(m.group(1)) for v in (a.get(k), b.get(k))
                 if isinstance(v, dict)
                 for m in [_ENTRY_DATE_RE.search(str(v.get("summary")
                                                     or ""))] if m]
        if not years:
            m = _ENTRY_DATE_RE.search(str(k))
            years = [int(m.group(1))] if m else []
        if years and min(years) > year:
            continue
        if not years and k not in a:
            continue
        out.append(str(k))
    return out


def _shared_tobase_moved(root: Path, fz: Dict[str, Any], year: int
                         ) -> Tuple[str, List[str], str]:
    """(the shared tobase.map shown from `root`, its pairs that differ
    from the copy the filed year was closed with and name a symbol with a
    row of `year`, why they could not be compared) (TJ-0712). A pair added
    or gone can change pooling, so the year's cost and gains, but the
    year reads its copy. Each file is read as a run reads it, over the
    year's ticker.map (lib/tobase_map.compute_overlay), so a pair
    ticker.map decides is no difference. Bytes and lines say nothing:
    `update-tobase-map` rewrites the shared file with every master."""
    from taxjson.lib import tobase_map as _TB
    from taxjson.lib.cli_diag import read_text_utf8
    v = fz.get("tobase_map")
    if (fz.get("partial") or not isinstance(v, str) or not v.strip()
            or _TB.project_country(root) != "canada"):
        return "", [], ""
    src = Path(v.strip()).expanduser()
    src = src if src.is_absolute() else root / src
    own = root / _PL.TOBASE_MAP
    if not src.is_file() or not own.is_file():
        return "", [], ""       # (no file to compare: nothing to say)
    shown = _PL.shown(src, root)
    try:
        tm = _PL.ticker_map_path(root)
        ticker = read_text_utf8(tm) if tm.is_file() else ""

        def _pairs(p: Path) -> Set[str]:
            return {ln for ln in _TB.compute_overlay(
                        ticker, _TB.parse_tobase(read_text_utf8(p), p.name),
                        True).lines if ln.startswith("TOBASE ")}
        moved = _pairs(src) ^ _pairs(own)
    except (OSError, ValueError) as e:
        return shown, [], str(e)
    if not moved:
        return shown, [], ""
    rows = _TB.books_symbol_dates(root)
    if not rows:
        return shown, [], "no books in work/: `tjs run` there"
    ys = str(year)
    held = {s for s, days in rows.items() if any(d[:4] == ys for d in days)}
    return shown, sorted(ln for ln in moved
                         if held & set(ln.split()[1:])), ""


def _shared_decisions_moved(root: Path, fz: Dict[str, Any], year: int
                            ) -> List[Tuple[str, int]]:
    """[(the shared file shown from `root`, how many entries)] — each
    decisions file of the lock's frozen inputs (inputs/<account>/
    manifest.json, sends.json) whose namesake in the shared folder the
    freeze copied it from holds a decision on `year` or earlier that
    differs from the copy (_decisions_moved). A late election or send
    decision saved there reaches neither the filed year, which reads
    its copy, nor its return; `elect` and `crypto-sends --set` name the
    frozen years when they write (_note_shared_decision), a hand edit
    showed nowhere (backlog TJ-0211). A file that cannot be read is not
    judged (the run that reads it says why). The shared folder:
    _frozen_shared_src (backlog TJ-0610)."""
    src = _frozen_shared_src(root, fz)
    if src is None:
        return []
    pre = f"{_PL.INPUTS}/"
    # Each account the freeze copied (a decisions file it had none of
    # is compared with an empty one: a first election saved later).
    accounts = sorted({k[len(pre):].split("/")[0]
                       for k in (fz.get("inputs") or {})
                       if k.startswith(pre) and k.count("/") == 2})
    out = []
    for acct in accounts:
        for name, table in sorted(_DECISION_FILES.items()):
            mine = root / _PL.INPUTS / acct / name
            theirs = src / acct / name
            try:
                if not theirs.is_file() or (
                        mine.is_file()
                        and mine.read_bytes() == theirs.read_bytes()):
                    continue
                moved = _decisions_moved(mine if mine.is_file() else None,
                                         theirs, table, year)
            except (OSError, ValueError):
                continue
            if moved:
                out.append((_PL.shown(theirs, root), len(moved)))
    return out


def _carry_in_moved(later: Any, record: Any, ry: int
                    ) -> Optional[Tuple[float, float, float]]:
    """(taken in, applied, carried now) when a later year's lock
    `later` took its net capital loss in from the `ry` close-year
    record (its opening_source names that record) and that record, as
    it is now (`record`), carries another balance into the next year:
    `ry` was closed again since (an amendment), so the later return
    applied, and its lock carried forward, a balance the record no
    longer has (backlog TJ-0430). None otherwise: another source ([estimate]
    other_losses), a lock without the block, the same balance."""
    import math as _math

    def _ncl(doc: Any) -> Optional[Dict[str, Any]]:
        cf = doc.get("carryforwards") if isinstance(doc, dict) else None
        b = cf.get("net_capital_loss") if isinstance(cf, dict) else None
        return b if isinstance(b, dict) else None
    a, b = _ncl(later), _ncl(record)
    if a is None or b is None or (f"(the {ry} close-year record)"
                                  not in str(a.get("opening_source") or "")):
        return None
    try:
        got = (float(a.get("opening") or 0.0), float(a.get("applied") or 0.0),
               float(b.get("closing") or 0.0))
    except (TypeError, ValueError):
        return None
    if not all(_math.isfinite(x) for x in got) \
            or M.cents_apart(got[0], got[2]) <= 1:   # whole cents (TJ-3381)
        return None
    return got


def _carry_in_detail(year: int, ry: int, got: Tuple[float, float, float],
                     record: str, then: str) -> str:
    """The detail of a _carry_in_moved message: what `year`'s return
    did with the balance `ry`'s record (`record`, shown) carried
    before, and `then` (what to run)."""
    taken, applied, now = got
    over = applied > now + 0.005
    return (f"{ry}'s close-year record ({record}) was closed again since "
            f"(an amendment). The {year} "
            f"return applied {M.shown(applied, 2):,.2f} of the {M.shown(taken, 2):,.2f}"
            + (f", more than the {M.shown(now, 2):,.2f} now carried in: no more "
               f"than the balance carried in can be applied (ITA "
               f"s.111(1)(b)), so {year} needs a T1-ADJ too"
               if over else
               f"; the balance its lock carried into {year + 1} is "
               f"built on the old {M.shown(taken, 2):,.2f}")
            + f" (its minimum tax carryover can move with it). {then}")


def _frozen_settings_back(root: Path) -> List[str]:
    """The shared folders a frozen filed year names again in its
    [settings] (`inputs_dir = "../inputs"`, `tobase_map = ...`): the
    freeze commented them out, so a live one means the year reads the
    shared files, not its frozen copy. [] when it names none."""
    out = []
    if _PL.shared_inputs(root):
        out.append(f"{_PL.INPUTS_KEY} ({_PL.shown(_PL.inputs_dir(root), root)})")
    if _PL.shared_tobase(root):
        out.append(f"{_PL.TOBASE_KEY} "
                   f"({_PL.shown(_PL.tobase_map_path(root), root)})")
    return out


def _soft_fingerprint(root: Path) -> Optional[Dict[str, str]]:
    from taxjson.lib.checklist import input_fingerprint
    try:
        cfg = load_config(root, missing_ok=True)
    except Exception:                                   # noqa: BLE001
        return None
    return input_fingerprint(root, cfg)


def _lock_missing_year(root: Path, settings: Settings
                       ) -> Optional[int]:
    """The project's year when its filed/<year>.json was deleted while
    taxjson.toml still carries close-year's freeze note
    (lib/project_layout.lock_missing_frozen; backlog TJ-0263), else
    None."""
    year = settings.year
    if not isinstance(year, int) or isinstance(year, bool):
        return None
    return year if _PL.lock_missing_frozen(root, year) else None


def _closed_with(lock: Dict[str, Any]) -> List[str]:
    """A drifted lock's detail line naming the taxjson that closed it
    and this one (backlog TJ-0341), or [] for a lock that records none
    (closed before the record was kept)."""
    from taxjson.lib import channels as _ch
    then = _ch.build_label(lock.get("taxjson"))
    if then is None:
        return []
    now_build = _ch.running_build()
    now = _ch.build_label(now_build) or "an unknown taxjson"
    same = _ch.same_build(lock.get("taxjson"), now_build)
    if same:
        return [f"It was closed with this taxjson ({then}): the change is "
                f"in the inputs or the settings, not the code."]
    return [f"It was closed with {then}; this is {now}: the releases "
            f"between them say what each moved (docs/upgrading.md)."]


def _check_filed_years(root: Path, cache: Path,
                       settings: Settings, *,
                       strict: bool,
                       counts: Optional[Dict[str, int]] = None,
                       console: bool = False) -> int:
    """Drift check for every filed/<year>.json. Returns the number of
    years that drifted or could not be checked; prints per-year
    OK/DRIFT lines. `counts` (when given) receives them split:
    drifted, lock (unreadable, damaged or other-country locks) and
    input (the recompute itself failed on the books or an input)."""
    from taxjson.bin import taxjson_filed
    from taxjson.lib import filed_locks
    from taxjson.lib import out as _out
    _prog = _cmd_prog()

    def _line(text: str) -> None:
        # A per-year line: wrapped, its continuation indented under it
        # (a `note: ` line shown with its label, lib/out.labelled).
        # On the run's console (`console`), shown to a person: a message
        # of its own, the label at column 0 (`Info: Filed 2024: OK ...`),
        # continuations flush-left, a blank line after it when it wraps.
        if console and _out.width(sys.stdout) > 0:
            _t = _out.labelled(text)
            if not _t.startswith(("Info: ", "Warning: ", "Error: ")):
                _t = "Info: " + _t[:1].upper() + _t[1:]
            _out.show(_out.wrap(_t, None, "", ""), sys.stdout)
            return
        # Flush-left like the OK / DRIFT lines (docs/output-style.md).
        _out.show(_out.wrap(_out.labelled(text), None, "", ""), sys.stdout)

    snaps = filed_locks.list_snapshots(root)
    # A bad [settings] value is the CONFIG's problem: refuse it in the
    # plain settings words before any lock is touched, not as "lock
    # could not be checked — fix or restore the lock" (A2-1134).
    if snaps:
        try:
            from taxjson.lib.pipeline import option_timing_from_settings
            _income_rules(settings)
            option_timing_from_settings(settings)
            taxjson_filed._tax_date(
                settings, filed_locks._canonical_country(settings))
        except ValueError as e:
            _die(f"{e}", "Fix taxjson.toml; the filed-year locks were not "
                 "checked.")
    drifting = 0
    # Which snapshot accounts are crypto (they blend only with each
    # other, and only in a Canadian project with two or more);
    # equity accounts recompute via ONE blended combined run — exactly
    # how close-year's numbers were produced — or the check would
    # falsely drift every multi-account book.
    try:
        _acct_cfg = accounts_of(load_config(root))
    except SystemExit:
        _acct_cfg = {}
        if snaps:
            _out.warn("taxjson.toml unreadable — the filed-year check "
                      "cannot tell crypto from equity accounts", prog=_prog,
                      details=["Crypto books would be blended into the "
                               "combined equity recompute and may report "
                               "false drift."])
    _taxable_cfg = {a for a, c in _acct_cfg.items() if c.taxable}
    unreadable = 0
    input_failed = 0
    mismatched = 0
    settings_back = 0               # frozen years naming shared folders
    carry_moved = 0                 # built on an earlier record replaced
    # Each judged year's verdict, kept for `tjs years` (backlog
    # TJ-0159: a filed year whose run DRIFTED looked clean there).
    _verdicts: Dict[int, str] = {}
    _lost = _lock_missing_year(root, settings)
    if _lost is not None:
        # A frozen year whose lock was deleted: said and counted as a
        # damaged lock is (backlog TJ-0263).
        unreadable += 1
        _h, _d = _PL.lock_missing_parts(_lost)
        _out.warn(f"filed {_lost} could not be checked: {_h}", prog=_prog,
                  details=[f"{_d} The lock is the record of the filed "
                           f"return; the next year's estimate, amt, "
                           f"carryover and handoff need it too."])
    for year, path in snaps:
        _stage = "lock"
        # One lock at a time: an unreadable or hand-edited lock is
        # reported BY NAME and counted as a failure, and the other
        # locks are still checked. A KeyError/JSONDecodeError here used
        # to escape the loop, skip --strict's exit and every later lock
        # (R1-189).
        try:
            try:
                # The one check every reader of a lock applies: a lock
                # the next year's estimate, amt, carryover or handoff
                # refuses as damaged (its carryforwards block, a year
                # field naming another year) was called OK here
                # (2026-10-25 audit year-end#6).
                snap = filed_locks.read_lock(path,
                                               label=f"filed/{path.name}")
            except filed_locks.LockProblem as e:
                unreadable += 1
                _out.warn(f"filed {year} could not be checked: {e}",
                          prog=_prog,
                          details=["The lock is the record of the filed "
                                   "return; the next year's estimate, "
                                   "amt, carryover and handoff refuse it "
                                   "too."])
                continue
            # A lock closed under the other country is never recomputed
            # under this one's law (partition COMMANDS-08).
            _cp = filed_locks.lock_country_problem(
                snap, settings, f"filed/{path.name}")
            if _cp:
                mismatched += 1
                _out.warn(f"filed {year}: {_cp}", prog=_prog)
                continue
            _snap_accts = list(snap.get("accounts", {}))
            # A locked account that is no longer a configured taxable
            # account (renamed/removed) is NOT recomputed from its
            # orphan work/<label>_base.json — that blended stale books
            # into the check and said OK (S002-06).
            _gone = ({a for a in _snap_accts if a not in _taxable_cfg}
                     if _acct_cfg else set())
            _snap_accts = [a for a in _snap_accts if a not in _gone]
            # Taxable accounts the BOOKS have but the lock does not
            # (added or renamed after close-year) are recomputed too —
            # in the same blend — so diff_snapshot can report them
            # instead of saying OK.
            _snap_accts += sorted(
                a for a in _taxable_cfg
                if a not in _snap_accts
                and (cache / f"{a}_base.json").exists())
            # Merge in taxjson.toml order, as the run's blended pass
            # does (CA-DATE-14 / US-DATE-13: rows of different accounts
            # at one moment follow the accounts' order). The lock's
            # keys are alphabetical (sort_keys), so its order gave a
            # false DRIFT right after close-year (A2-0512).
            if _acct_cfg:
                _toml_order = {a: i for i, a in enumerate(_acct_cfg)}
                _snap_accts.sort(key=lambda a: _toml_order.get(
                    a, len(_toml_order)))
            _crypto = [a for a in _snap_accts
                       if account_of(_acct_cfg, a).crypto]
            _equity = [a for a in _snap_accts if a not in _crypto]
            _lock_timing = snap.get("option_timing")
            _stage = "recompute"
            recomputed = taxjson_filed.recompute_accounts(
                cache, _equity, _crypto, year,
                taxjson_filed.lock_settings(snap, settings),
                snap.get("basis", ""), _filed_run_gains,
                option_timing=_lock_timing)
            _stage = "lock"
            _diff_notes: List[str] = []
            lines = taxjson_filed.diff_snapshot(snap, recomputed,
                                                unconfigured=_gone,
                                                notes=_diff_notes)
            for _dn in _diff_notes:
                _line(f"note: filed {year}: {_dn}")
        except SystemExit:
            raise
        except Exception as e:          # this lock only
            if _stage == "recompute":
                # The books or an input failed, not the lock: say what
                # failed in the child's words and do not send the user
                # to edit the filed record (A2-0676).
                input_failed += 1
                _out.warn(f"filed {year}: {path.name} could not be "
                          f"checked: {_child_error_line(e)}", prog=_prog,
                          details=[_recompute_hint(_child_error_line(e))])
                continue
            unreadable += 1
            _out.warn(f"filed {year}: {path.name} could not be checked: "
                      f"{type(e).__name__}: {e}", prog=_prog,
                      details=["Fix or restore the lock (it is the record "
                               "of the filed return)."])
            continue
        if isinstance(_lock_timing, dict):
            from taxjson.lib.pipeline import option_timing_from_settings
            _cur = option_timing_from_settings(settings) or {}
            if _cur and (
                    _cur.get("option_premium_timing")
                    != _lock_timing.get("option_premium_timing")
                    or _cur.get("option_grant_since")
                    != _lock_timing.get("option_grant_since")):
                _line(f"note: filed {year} recomputed with the option "
                      f"timing its lock records "
                      f"({_lock_timing.get('option_premium_timing')}, "
                      f"since {_lock_timing.get('option_grant_since')}); "
                      f"this project uses "
                      f"{_cur.get('option_premium_timing')}, since "
                      f"{_cur.get('option_grant_since')} — set "
                      f"option_grant_timing_since to match or contracts "
                      f"written around {year} are taxed in the wrong "
                      f"year or twice")
            # The buy-back flag is recomputed from the lock too: an OK
            # line said nothing while the project's own .sum for that
            # year moved (A2-0348).
            if _cur and "option_buyback_loss_superficial" in _lock_timing \
                    and bool(_lock_timing.get(
                        "option_buyback_loss_superficial")) \
                    != bool(_cur.get("option_buyback_loss_superficial")):
                _line(f"note: filed {year} recomputed with "
                      f"option_buyback_loss_superficial = "
                      f"{str(bool(_lock_timing['option_buyback_loss_superficial'])).lower()}"
                      f" as its lock records; this project now says "
                      f"{str(bool(_cur.get('option_buyback_loss_superficial'))).lower()}"
                      f" — this project's reports for {year} no longer "
                      f"match what was filed")
        # Likewise the date basis (lock_settings): the lock is checked
        # on the basis it was filed on, so OK says nothing about this
        # project's own report for that year (A2-0672).
        _fz = snap.get("frozen")
        # A lock taken before the year ended (close-year --force): its
        # inputs were hashed, not frozen — the year reads the shared
        # exports, so its later trades are expected to move it
        # (workflow audit 2026-10-23).
        _partial = filed_locks.partial_year_note(snap, year)
        if isinstance(_fz, dict) and isinstance(_fz.get("inputs"), dict):
            # The lock hashed the inputs the filed books came from: one
            # edited or replaced since is named, so a DRIFT (or an OK
            # with other books) is not blamed on the code.
            _moved = _frozen_inputs_changed(root, _fz["inputs"])
            # taxjson.toml as `tjs migrate` left it, the edit its
            # lock records (frozen.migrated): migrate's own, explained
            # by check-filed, never a changed input; any later edit is
            # one (backlog TJ-0231).
            _mig = (_migrate_edit(root, _fz) if _PL.CONFIG in _moved
                    else None)
            if _mig is not None:
                _moved = [m for m in _moved if m != _PL.CONFIG]
                if not console:
                    _keys = ", ".join(str(k) for k in
                                      (_mig.get("keys") or [])) or "layout"
                    _line(f"note: filed {year}: taxjson.toml is as "
                          f"`{_mig.get('by') or 'tjs migrate'}` "
                          f"rewrote it on {_mig.get('on') or '?'} "
                          f"({_keys}), the edit its lock records — not "
                          f"a changed input")
            # The freeze is the project's own year's: a lock of an
            # earlier year kept in this year's filed/ (a single-folder
            # project gone on to the next year, then `migrate
            # --to-years`) is checked from the inputs this year reads,
            # rightly shared — never "delete inputs_dir" (backlog
            # TJ-0087).
            _cur_year = settings.year
            _own = year == _cur_year
            _back = ([] if _partial or not _own
                     else _frozen_settings_back(root))
            if _partial and _moved:
                _line(f"note: filed {year}: {len(_moved)} input(s) "
                      f"changed since the partial lock was taken: "
                      f"{', '.join(_moved[:4])}"
                      + (" ..." if len(_moved) > 4 else "")
                      + " — not frozen while the year was open: "
                      "they reach the books")
            elif not _own:
                if _moved:
                    _line(f"note: filed {year} is checked from the inputs "
                          f"this {_cur_year} project reads "
                          f"({len(_moved)} changed since {year} was "
                          f"closed: {', '.join(_moved[:4])}"
                          + (" ..." if len(_moved) > 4 else "") + ")")
            elif _back:
                # taxjson.toml names the shared folders again (an older
                # `align`, or by hand): the books read them, not the
                # frozen copy — restoring files would change nothing
                # (workflow audit 2026-10-22). Something to act on: a
                # Warning, and `run --strict` stops (backlog TJ-0108).
                settings_back += 1
                _out.warn(f"filed {year} was closed with its inputs "
                          f"frozen, but taxjson.toml names the shared "
                          f"{' and '.join(_back)} again", prog=_prog,
                          details=[
                              f"The books no longer read the frozen "
                              f"copy: delete "
                              f"{'those lines' if len(_back) > 1 else 'that line'}"
                              f" from [settings], then `tjs run`."])
            elif _moved:
                _line(f"note: filed {year}: {len(_moved)} frozen input(s) "
                      f"changed since the year was closed: "
                      f"{', '.join(_moved[:4])}"
                      + (" ..." if len(_moved) > 4 else "")
                      + " — the books no longer come from the exports "
                      "as filed (restore them from your records; for "
                      "an amendment, re-close once it is filed)")
            _shared = ({"exports": [], "mappings": [], "unread": []}
                       if _partial or _back
                       else _shared_inputs_moved(root, _fz, year))
            _shared_moved = _shared["exports"]
            if _shared_moved:
                # A correction saved in the shared folder: the one place
                # it shows (backlog TJ-0091).
                _line(f"note: filed {year}: {len(_shared_moved)} shared "
                      f"export(s) gained, changed or lost a row of "
                      f"{year} or earlier since the year was closed: "
                      f"{', '.join(_shared_moved[:4])}"
                      + (" ..." if len(_shared_moved) > 4 else "")
                      + f" — the filed year reads its frozen copy and "
                      f"{year + 1}'s handoff checks only Dec 31 positions "
                      f"and cost, so a correction there shows nowhere "
                      f"else: compare each with its copy in inputs/ (a "
                      f"file new since the close has none); to "
                      f"amend, copy it in, `tjs run`, and after "
                      f"filing `tjs close-year --force`")
            _map_moved = _shared["mappings"]
            if _map_moved:
                # A generic mapping corrected in the shared folder: the
                # CSV's bytes are the same, its figures not (backlog
                # TJ-0708).
                _line(f"note: filed {year}: {len(_map_moved)} shared "
                      f"generic mapping(s) changed since the year was "
                      f"closed: {', '.join(_map_moved[:4])}"
                      + (" ..." if len(_map_moved) > 4 else "")
                      + f" — {'it reads' if len(_map_moved) == 1 else 'they read'}"
                      f" a CSV with rows of {year} or earlier otherwise "
                      f"than the copy {year} was closed with (a column, an "
                      f"action, a format), and the filed year reads its "
                      f"copy, so a correction there shows nowhere else: "
                      f"compare each with its copy in inputs/ (a mapping "
                      f"new since the close has none); to amend, copy it "
                      f"in, `tjs run`, and after filing `tjs "
                      f"close-year --force`")
            _tob_file, _tob_moved, _tob_why = (
                ("", [], "") if _partial or _back
                else _shared_tobase_moved(root, _fz, year))
            if _tob_moved:
                # A pair added or gone in the shared tobase.map: pooling
                # moves, and the filed year reads its copy (TJ-0712).
                _line(f"note: filed {year}: {_tob_file} differs from the "
                      f"copy {year} was closed with by "
                      f"{_count_noun(len(_tob_moved), 'pair')} naming a "
                      f"symbol with rows of {year}: "
                      f"{', '.join(_tob_moved[:4])}"
                      + (" ..." if len(_tob_moved) > 4 else "")
                      + f" — pooling can change {year}'s cost and gains, "
                      f"and the filed year reads its copy, so this shows "
                      f"nowhere else: to amend, make the same change in "
                      f"{year}'s tobase.map, `tjs run`, and after filing "
                      f"`tjs close-year --force`")
            if _tob_why:
                _line(f"note: filed {year}: {_tob_file} could not be "
                      f"compared with the copy {year} was closed with: "
                      f"{_tob_why}")
            _dec_moved = ([] if _partial or _back
                          else _shared_decisions_moved(root, _fz, year))
            if _dec_moved:
                # A late election or send decision saved by hand in the
                # shared folder (backlog TJ-0211).
                _line(f"note: filed {year}: "
                      + ", ".join(f"{p} ({_count_noun(n, 'entry', 'entries')})"
                                  for p, n in _dec_moved[:4])
                      + (" ..." if len(_dec_moved) > 4 else "")
                      + f" in the shared folder "
                      f"{'holds' if len(_dec_moved) == 1 else 'hold'} "
                      f"a decision on {year} "
                      f"or earlier that differs from the copy {year} was "
                      f"closed with — the filed year reads its copy, so "
                      f"it reaches neither its books nor its return: to "
                      f"amend, make the same decision in {year}'s folder "
                      f"(`tjs elect` / `tjs crypto-sends --set` "
                      f"there), `tjs run`, and after filing `tjs "
                      f"close-year --force`; else undo it in the shared "
                      f"file")
            _unread = _shared["unread"]
            if _unread:
                # Not judged, so never passed as unchanged (backlog
                # TJ-0707).
                _line(f"note: filed {year}: "
                      f"{_count_noun(len(_unread), 'file')} could not be "
                      f"compared with the copy {year} was closed with: "
                      + ", ".join(f"{p} ({why})" for p, why in _unread[:4])
                      + (" ..." if len(_unread) > 4 else "")
                      + " — a correction there would show nowhere: "
                      "compare it with its copy in inputs/ yourself, or "
                      "save it readable (CSV UTF-8, a TOML mapping) and "
                      "check again")
        _rb = snap.get("date_basis")
        _now_basis = _tax_date(settings)
        if _rb in ("settle", "trade") and _rb != _now_basis:
            _line(f"note: filed {year} recomputed on the {_rb} date "
                  f"basis its lock records; this project uses "
                  f"tax_date = \"{_now_basis}\" — sales near Dec 31 "
                  f"fall in another year in this project's reports than "
                  f"on the filed return")
        _verdicts[year] = "drifted" if lines else "ok"
        if lines:
            drifting += 1
            _out.warn(f"filed {year} DRIFTED vs {path.name}", prog=_prog,
                      details=[f"- {ln}" for ln in lines]
                      + _closed_with(snap)
                      + ([f"The lock is a snapshot taken before {year} "
                          f"ended: trades and corrections since then "
                          f"move the year. Close it again after filing "
                          f"(`tjs close-year --force` with "
                          f"[settings].year = {year})."] if _partial else
                         [f"A code/data change moved an already-filed "
                          f"year. Review; then either amend the return or "
                          f"refresh the lock (`tjs close-year --force` "
                          f"with [settings].year = {year})."]))
        else:
            _line(f"filed {year}: OK (matches {path.name}; "
                  f"{taxjson_filed.NOT_LOCKED})")
        if _partial:
            _line(f"note: filed {year}: {_partial}")
        elif year == settings.year:
            # The year's own lock took its net capital loss in from the
            # earlier year's record: that year amended and closed again
            # since leaves this return built on the old balance (backlog
            # TJ-0430).
            from taxjson.lib import carryforward as _CFw
            _pl = _CFw.prior_lock(root, settings)
            try:
                _rec = (filed_locks.load_lock_json(_pl[1])
                        if _pl is not None else None)
            except (OSError, ValueError):
                _rec = None                 # its readers name it
            _cm = (_carry_in_moved(snap, _rec, _pl[0])
                   if _pl is not None else None)
            if _cm is not None:
                carry_moved += 1
                _out.warn(f"filed {year} took in a {M.shown(_cm[0], 2):,.2f} net "
                          f"capital loss from {_pl[0]}, whose record now "
                          f"carries {M.shown(_cm[2], 2):,.2f} into {year}", prog=_prog,
                          details=[_carry_in_detail(
                              year, _pl[0], _cm, _pl[2],
                              f"`tjs run` here (the estimate reads "
                              f"the {_pl[0]} record as it is now), then "
                              f"after filing `tjs close-year "
                              f"--force`.")])
    _PL.record_filed_check(root, _verdicts)
    if counts is not None:
        counts.update(drifted=drifting, lock=unreadable + mismatched,
                      input=input_failed)
    unreadable += input_failed
    if (unreadable or mismatched) and strict:
        sys.exit(exit_text(f"tjs run --strict: {unreadable + mismatched} "
                           f"filed-year lock(s) could not be checked"
                           + (f" ({mismatched} closed under another country)"
                    if mismatched else "")
                           + (f" and {drifting} drifted" if drifting else "")
                           + " — aborting."))
    if drifting and strict:
        sys.exit(exit_text(f"tjs run --strict: {drifting} filed year(s) "
                           f"drifted — aborting."))
    if settings_back and strict:
        sys.exit(exit_text(f"tjs run --strict: {settings_back} filed "
                           f"year(s) no longer read their frozen inputs "
                           f"— aborting."))
    if carry_moved and strict:
        sys.exit(exit_text(f"tjs run --strict: the filed year took in "
                           f"a net capital loss its earlier year's record "
                           f"no longer carries — aborting."))
    return drifting + unreadable + mismatched


# What the newest year's run copies into [settings] exports_dir for other
# tools (lib/project_layout): reports/ files by pattern.
_EXPORT_PATTERNS = ("*_holdings.toml", "wash_radar_*.json",
                    "wash_radar_*.rpt")
_EXPORTS_README = """\
Written by `tjs run` of the {year} project ({folder}/), the newest
year folder here; every run of the newest year replaces these files.
Read them; do not edit them.

  <account>_holdings.toml   the account's positions at the end of the
                            books, at the account's own cost, before the
                            passes across accounts: not the filing cost
                            (as reports/<account>_holdings.toml)
  wash_radar_<account>.json the superficial-loss / wash-sale radar: the
  wash_radar_<account>.rpt  sales and purchases whose window is open
                            (JSON for programs, the text report)
  wash_radar_COMBINED.*     the same across the taxable accounts
"""


_EXPORTS_README_NAME = "README.txt"
# The files the last export wrote: the next one removes only those it no
# longer writes (`_write_exports`).
_EXPORTS_MANIFEST = ".taxjson-exports.json"


def _exports_manifest(dst: Path) -> List[str]:
    """The plain file names the previous export recorded writing ([]
    without a readable manifest; a name with a path in it is never
    one)."""
    import json
    try:
        doc = json.loads((dst / _EXPORTS_MANIFEST).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return []
    files = doc.get("files") if isinstance(doc, dict) else None
    return [f for f in (files if isinstance(files, list) else [])
            if isinstance(f, str) and f and f == Path(f).name
            and f not in (".", "..") and not f.startswith(".")]


def _write_exports(root: Path, settings: Settings,
                   reports_dir: Path) -> None:
    """The newest year's run: copy its positions and wash radar from
    reports/ into exports_dir with a README naming the year; an older
    year's run leaves them alone."""
    dst = _PL.exports_folder(root)
    if dst is None:
        return
    year = settings.year
    newer = [y for y, _d in _PL.year_dirs(root.parent)
             if isinstance(year, int) and y > year]
    if newer:
        return
    # (each copy written when it changed: a run that changed nothing
    # writes nothing)
    from taxjson.lib.safe_write import write_changed
    if dst.is_symlink():
        raise OSError(f"{dst} is a symlink")
    # (load_config refused an exports_dir overlapping another folder:
    # lib/project_layout.exports_overlap; said again here, never a
    # write into inputs, holdings or a year folder.)
    _ov = _PL.exports_overlap(root, settings)
    if _ov:
        raise OSError(_ov)
    dst.mkdir(parents=True, exist_ok=True, mode=0o700)
    srcs = [f for pat in _EXPORT_PATTERNS
            for f in sorted(reports_dir.glob(pat))
            if f.is_file() and not f.name.startswith(".")]
    names = sorted({f.name for f in srcs} | {_EXPORTS_README_NAME})
    # Only the files the previous export wrote (its manifest) and this
    # one no longer writes are removed: a file someone else put in the
    # folder is never touched.
    for old in sorted(set(_exports_manifest(dst)) - set(names)):
        p = dst / old
        if p.is_file() or p.is_symlink():
            p.unlink()
    for f in srcs:
        write_changed(dst / f.name, f.read_bytes())
    n = len(srcs)
    write_changed(dst / _EXPORTS_README_NAME, _EXPORTS_README.format(
        year=year, folder=root.name))
    import json
    write_changed(dst / _EXPORTS_MANIFEST, json.dumps(
        {"schema_version": 1, "year": year, "files": names},
        indent=2) + "\n")
    _say("note", f"{n} file(s) for other tools -> "
         f"{_PL.shown(dst, root)}/ (the newest year's)", indent="  ",
         file=sys.stdout)


def _fx_cash_after_run(root: Path, cache: Path,
                       reports_dir: Path) -> None:
    """End-of-run FX-cash report, ONLY when [settings] fx_cash_gains
    is true — the ledger is advisory and many filers have never
    reported s.39(1.1), so it stays opt-in and touches no other
    output."""
    settings = load_settings(root, missing_ok=True)
    from taxjson.bin import taxjson_fx_cash as FX
    if not settings.fx_cash_gains:
        return
    _step("Calculating FX gains on cash (fx_cash_gains = true)")
    from taxjson.lib.safe_write import write_atomic
    rpt = reports_dir / "fx_cash.rpt"
    try:
        if _fx_cash_ledger_choice(settings) == "v2":
            from taxjson.lib import fx_cash_v2 as V2
            doc, verdict, _c, base, year, country = _fx_cash_v2_doc(root,
                                                                    cache)
            write_atomic(rpt, V2.render(doc, verdict, country, width_=0)
                         + "\n")
            _say("note", f"{V2.headline(doc, verdict)} -> "
                 f"{_out_relpath(rpt, root)}", indent="  ",
                 file=sys.stdout)
            return
        ledger, verdict, base, year, country = _fx_cash_doc(root, cache)
    except SystemExit as e:
        _say("warning", f"FX gains on cash skipped: {e}", indent="  ")
        return
    # A file, like the captured stage reports: never wrapped.
    text = FX.render_report(ledger, base, year, country, verdict,
                            width_=0)
    write_atomic(rpt, text + "\n")
    # Never a reportable figure from the default ledger (CA-FX-07).
    st = FX.unreliable_status(ledger, year, verdict)
    _say("note", f"{st['headline']} -> {_out_relpath(rpt, root)}",
         indent="  ", file=sys.stdout)


FX_CASH_LEDGERS = ("v1", "v2")


def _fx_cash_ledger_choice(settings: Settings,
                           flag: Optional[str] = None) -> str:
    """Which FX-on-cash ledger: `--ledger` when given, else [settings]
    fx_cash_ledger, else "v1" — the default, flagged NOT RELIABLE
    (CA-FX-07 / US-FX-03). v2 stays opt-in until it has been audited."""
    v = flag or settings.fx_cash_ledger or "v1"
    v = str(v).strip().lower()
    if v not in FX_CASH_LEDGERS:
        sys.exit(exit_text(f"tjs fx-cash: fx_cash_ledger must be "
                           f"\"v1\" or \"v2\" (got {v!r})"))
    return v


def _fx_cash_native(root: Path, cache: Path, prog: str = "tjs fx-cash"):
    """(rows, base, year, country, fx history) for the FX-on-cash
    ledgers (and `tjs cash`, `prog` naming it in the errors): every
    TAXABLE account's native rows (each with `account`) — s.39(1.1) /
    §988 reach the person's taxable holdings; registered accounts are
    exempt."""
    from taxjson.lib.price_chain import load_fx_history
    cfg = load_config(root, missing_ok=True)
    settings = settings_of(cfg)
    base = str(_base(settings)).upper()
    year = settings.year
    if not year:
        sys.exit(exit_text(f"{prog}: needs [settings] year in "
                           "taxjson.toml."))
    country = _country(settings)
    txs: List[Dict[str, Any]] = []
    found = False
    for name, acfg in sorted(accounts_of(cfg).items()):
        if not acfg.taxable:
            continue
        f = _native_tx_file(cache, name)
        if f is None:
            if _has_inputs(root, name):
                # A partial ledger flipped the line-15300 figure's sign
                # with rc 0 (S005-04, S046-19).
                sys.exit(exit_text(f"{prog}: no native transaction file "
                                   f"for taxable account {name!r} — run `tjs "
                                   f"run` first; a ledger without it would be "
                                   f"partial."))
            continue
        try:
            doc = _read_work_doc(f)
        except (OSError, ValueError) as e:
            _m = str(e)                 # (named once: A2-1413)
            sys.exit(exit_text(f"{prog}: "
                               f"{_m if str(f) in _m else f'could not read {f}: {_m}'}"
                               + ("" if "tjs run" in _m else
                        " — re-run `tjs run` to rebuild it")
                               + f"; a ledger without account {name!r} would be "
                       f"partial."))
        found = True
        for t in doc.get("transactions", []):
            t.setdefault("account", name)
            txs.append(t)
    if not found:
        sys.exit(exit_text(f"{prog}: no native transaction files in "
                           f"{cache} (run `tjs run` first)."))
    try:
        fx = load_fx_history(cache / "to_base.csv", base)
    except (OSError, ValueError) as e:
        sys.exit(exit_text(f"{prog}: error: {e}"))
    return txs, base, int(year), country, fx


def _fx_cash_doc(root: Path, cache: Path):
    """(doc, verdict, base, year, country) for the default (v1) FX-cash
    report — shared by the fx-cash command and the end-of-run hook."""
    from taxjson.bin import taxjson_fx_cash as FX
    txs, base, year, country, fx = _fx_cash_native(root, cache)
    try:      # a futures refusal (A2-1424/1434)
        ledger = FX.build_ledger(txs, base, fx, int(year), country=country)
    except (OSError, ValueError) as e:
        sys.exit(exit_text(f"tjs fx-cash: error: {e}"))
    verdict = FX.apply_jurisdiction(ledger["net_gain"], country)
    return ledger, verdict, base, int(year), country


def _fx_cash_carry(root: Path, settings: Settings, year: int,
                   country: Optional[str] = None
                   ) -> Optional[Dict[str, Any]]:
    """The prior year's v2 close (close-year's `fx_cash_v2` in the
    prior-year record: filed/<year-1>.json, or the record [settings]
    prior_year_record names). None when there is no record, or it
    carries no close (closed under the default ledger). A record that
    EXISTS but cannot be read, is not a close-year record, carries
    another year's close, or was closed under the other country
    (`country`: this project's) is {"year", "books": {}, "problem":
    text}: lib/fx_cash_v2.build lists the problem and computes nothing.
    It used to read as no record, and an empty year was certified on it
    (2026-10 audit C02, C04)."""
    import json as _json
    from taxjson.lib.filed_locks import PriorRecordError, prior_record_setting
    from taxjson.lib.country import (CountryError, canonical_country,
                                     display_name, home_currency)

    def bad(text: str) -> Dict[str, Any]:
        return {"year": year - 1, "books": {}, "problem": text}
    try:
        p = prior_record_setting(root, settings)
    except PriorRecordError as e:
        return bad(str(e))
    label = (f"[settings] prior_year_record ({p})" if p is not None
             else f"filed/{year - 1}.json")
    if p is None:
        p = root / "filed" / f"{year - 1}.json"
    p = Path(p)
    if not p.exists():
        # A filed year's deleted lock is a problem, not "no record"
        # (backlog TJ-0272); a year never closed has none to miss.
        from taxjson.lib.filed_locks import missing_lock_problem
        _gone = missing_lock_problem(p, label)
        return bad(_gone) if _gone else None
    try:
        rec = _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return bad(f"{label}: the prior-year record cannot be read ({e}) "
                   f"— restore it from git or re-close {year - 1}; until "
                   f"then this year's opening pool is unknown")
    if not isinstance(rec, dict):
        return bad(f"{label}: not a close-year record (not a JSON "
                   f"object) — restore it from git or re-close {year - 1}")
    # A record closed under the default ledger carries no pool, whatever
    # its country: no carry (backlog TJ-0022: the country check came
    # first and refused a year that had nothing to carry).
    close = rec.get("fx_cash_v2")
    if close is None:
        return None
    rc = rec.get("country")
    if country is not None and rc not in (None, ""):
        here = canonical_country(country)
        try:
            rcc = canonical_country(rc, what=f"{label} country")
        except CountryError as e:
            return bad(f"{e} — the record cannot be checked against this "
                       f"project; restore it from git or re-close "
                       f"{year - 1}")
        if rcc != here:
            # `foreign`: CASHOPEN lines open the year instead, and the
            # record is then only noted (lib/fx_cash_v2._carry_openings).
            return dict(bad(
                f"{label} was closed under country = \"{rcc}\" "
                f"({display_name(rcc)} law) but this project is "
                f"country = \"{here}\" ({display_name(here)}): its "
                f"foreign-cash pool (costs in {home_currency(rcc)}) "
                f"cannot open a {display_name(here)} year — set "
                f"[settings] country = \"{rcc}\" if that is wrong, "
                f"else open the year with CASHOPEN lines "
                f"(docs/settings.md)"), foreign=(
                f"{label} was closed under country = \"{rcc}\" "
                f"({display_name(rcc)} law): its foreign-cash pool is not "
                f"carried into this {display_name(here)} year, which opens "
                f"from its CASHOPEN lines"))
    if not isinstance(close, dict) or not isinstance(close.get("books"),
                                                     dict):
        return bad(f"{label}: its fx_cash_v2 close is not a table of books "
                   f"— the record is damaged; restore it from git or "
                   f"re-close {year - 1} with this ledger")
    if close.get("year") != year - 1:
        return bad(f"{label}: its fx_cash_v2 close is {close.get('year')!r}'s, "
                   f"not {year - 1}'s — the record is damaged or names the "
                   f"wrong year; restore it from git or re-close {year - 1}")
    return close


def _fx_cash_carry_record(settings: Settings, year: int) -> str:
    """The prior-year record a carry's repair names
    (lib/fx_cash_v2._carry_openings): [settings] prior_year_record as
    written, else filed/<year-1>.json. A repair used to name
    filed/<year-1>.json whatever file was read (backlog TJ-0068)."""
    rec = str(settings.prior_year_record or "").strip()
    return rec or f"filed/{year - 1}.json"


def _fx_cash_v2_doc(root: Path, cache: Path, holdings: bool = False,
                    holdings_basis: str = "settle"):
    """(doc, verdict or None, cash, base, year, country) of the opt-in
    ledger v2 (lib/fx_cash_v2): the native rows attributed to their
    broker account's book, the cash events (lib/cash_events), the prior
    year's close. `holdings`: the doc carries each book's cash day by
    day (lib/fx_cash_v2.build; `tjs t1135`), on the date basis
    `holdings_basis` (the project's tax_date: backlog TJ-0627)."""
    import re as _re
    from taxjson.lib import cash_events as CE
    from taxjson.lib import fx_cash_v2 as V2
    from taxjson.bin import taxjson_fx_cash as FX
    from taxjson.lib.price_chain import latest_rate
    txs, base, year, country, fx = _fx_cash_native(root, cache)
    cfg = load_config(root, missing_ok=True)
    settings = settings_of(cfg)
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for t in txs:
        by_label.setdefault(str(t.get("account")), []).append(t)
    try:
        cash = CE.collect(root, cfg, country, by_label, work=cache)
    except (OSError, ValueError) as e:
        sys.exit(exit_text(f"tjs fx-cash: error: {e}"))
    books = cash["books"]
    for t in txs:
        src = _re.sub(r"#\d+$", "", str(t.get("source") or ""))
        t["_book"] = books.for_file(str(t.get("account")), src,
                                    str(t.get("source_account") or ""))

    def rate_of(cur: str, d: str):
        r, _d = latest_rate(fx, cur, d,
                            max_age_days=FX.FX_MAX_RATE_AGE_DAYS)
        return r
    carry = _fx_cash_carry(root, settings, year, country=country)
    if carry:
        carry = dict(carry, record=_fx_cash_carry_record(settings, year))
    try:
        doc = V2.build(txs, cash, base, year, rate_of, country=country,
                       carry=carry,
                       inflow_spot=str(settings.fx_cash_inflow_cost
                                       or "").strip().lower() == "spot",
                       holdings=holdings, holdings_basis=holdings_basis)
    except ValueError as e:
        sys.exit(exit_text(f"tjs fx-cash: error: {e}"))
    verdict = FX.v2_verdict(doc, country)
    return doc, verdict, cash, base, year, country


def _refuse_map_problems(ticker_map: Path, problems: Sequence[str],
                         effect: str, code: int = 1) -> None:
    """Stop on ticker.map / tobase.map lines map_file_problems found
    (`effect`: what a dropped or contradictory rule changes), naming
    each line, exit `code`. `tjs run` and the wash checks refuse
    the same map with the same words."""
    # The files the problems are in (tobase.map is read with
    # ticker.map): a tobase.map line was headed "ticker.map
    # problem(s)" (backlog TJ-0125).
    files: List[str] = []
    for p in problems:
        m = re.match(r"(.+?):\d+: ", p)
        n = Path(m.group(1)).name if m else ticker_map.name
        if n not in files:
            files.append(n)
    files.sort(key=lambda n: n != ticker_map.name)
    (_die_input if code == 2 else _die)(
        f"{len(problems)} {' and '.join(files)} problem(s)",
        *[f"- {p}" for p in problems],
        f"A malformed line's rule would be silently dropped, and "
        f"contradictory rules (a cycle, two targets for one symbol, a "
        f"DISTINCT pair the renames pool) have no single meaning; "
        f"{effect}. Fix the line (its keyword's fields separated by "
        f"spaces, as the ticker.map header and docs/settings.md give "
        f"them; notes after `#`) or delete it.")


def _project_base_files(cache: Path) -> List[Path]:
    """work/<account>_base.json of every account (not the raw/native
    books, not the combined sheltered book, not a dot-prefixed pass)."""
    return sorted(p for p in cache.glob("*_base.json")
                  if not p.name.endswith("_raw_base.json")
                  and p.name != "sheltered_base.json"
                  and not p.name.startswith("."))


def _year_short_rows(root: Path, files: List[Path], year: Any):
    """{(symbol, account): MissingHistoryRow} of every pair that goes
    short in the project's books, judged against `year`
    (lib/missing_history.classify_year_shorts) — the one test the run's
    console and `find-missing-history --outside-year` share."""
    from taxjson.lib.tx_load import load_transactions
    from taxjson.lib.missing_history import (account_types_near,
                                              classify_year_shorts,
                                              walk_journal_symbols)
    txs = []
    for f in files:
        try:
            txs.extend(load_transactions(f))
        except (OSError, ValueError):
            continue
    settings = load_settings(root, missing_ok=True)
    tm = _PL.ticker_map_path(root)
    journal = walk_journal_symbols(root / "work",
                                   tm if tm.is_file() else None)
    types = (account_types_near(files[0]) if files else {})
    return classify_year_shorts(
        txs, year, country=_country(settings), registered=types,
        date_basis=_tax_date_basis(settings), journal_symbols=journal)

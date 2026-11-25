"""The dispatcher: the checks before every command, chaining, and a failure's one line."""
import argparse
import contextlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.project.settings import settings_of
from taxjson.project.toml_io import read_toml
from taxjson.lib import once
from taxjson.lib import project_layout as _PL
from taxjson.lib.out import exit_text, show as _show
from taxjson.lib.stage_msg import emit_line

from taxjson.cli.books import (
    _is_configured_account, _refuse_missing_history_file,
)
from taxjson.cli.common import (
    _accounts_skipped_for_no_inputs, _frozen_years_apart, _is_period,
    _os_path_lexists, _refuse_cash_older_than_inputs, _warn_run_state,
)
from taxjson.cli.explain import _tt_path_item
from taxjson.cli.file import _filed_year_folders
from taxjson.cli.parser import _build_parser, _DETAILS_CMDS
from taxjson.cli.setup import _no_fetcher_exit, _stale_fetcher_exit
from taxjson.pipeline import run_state
from taxjson.pipeline.config import (
    load_config, load_settings,
    _country, _no_project_message,
    _refuse_legacy_project_files, _refuse_newer_config,
    _refuse_outside_dir_links, _unknown_setting_warnings,
    _use_project_ticker_map,
)
from taxjson.pipeline.messages import (
    _cmd_prog, _details, _die, _die_input, _PROG, _say, _step,
)
from taxjson.pipeline.sanity import _mask_ids_in_path
from taxjson.pipeline.stages import (
    _SKIPPED_ACCOUNTS_FILE,
)
from taxjson.pipeline.tools import _stage_text_shown


def _console_line_buffered() -> None:
    """stdout and stderr flushed at every line end. Read through a pipe
    (`tjs run 2>&1 | tee log`) a block-buffered stdout came out in
    chunks while stderr came out at once: a warning landed in the middle
    of an earlier stdout line, or before the step it belongs to."""
    import io as _io
    for st in (sys.stdout, sys.stderr):
        try:
            st.flush()
            st.reconfigure(line_buffering=True)
        except (AttributeError, ValueError, OSError,
                _io.UnsupportedOperation):
            pass


def _refuse_newer_projects(args: argparse.Namespace) -> None:
    """Every command, before anything else: the project in -C (and, for
    the commands that work on the folder holding the years, or from a
    folder with no taxjson.toml, every year folder beside it) must not
    need a newer taxjson (lib/requires)."""
    if args.cmd in ("help", "tax-logic") + _RELEASE_CMDS:
        return
    root = Path((getattr(args, "path", None) if args.cmd == "init"
                 else None) or args.dir)
    if not root.is_dir():
        return
    own = root / _PL.CONFIG
    cfg = _refuse_newer_config(own) if own.is_file() else None
    if cfg is not None and args.cmd not in ("run", "init"):
        # A key this taxjson does not read is ignored: said, one line
        # each (`run` says it with its other config warnings).
        for _msg in _unknown_setting_warnings(cfg):
            _warn_config_once(_msg)
    if own.is_file() and args.cmd not in _YEARS_ROOT_CMDS | {"align"}:
        return
    top = _PL.multi_root(root)
    if top is None:
        return
    for y, d in _PL.year_dirs(top):
        if d.resolve() != root.resolve():
            _refuse_newer_config(d / _PL.CONFIG, f"{y}/")


def _warn_config_once(msg: str) -> None:
    """`tjs <cmd>: warning: taxjson.toml: <msg>` on stderr, once for
    each project in a chain."""
    if msg in run_state.current.config_warned:
        return
    run_state.current.config_warned.add(msg)
    emit_line(f"{_cmd_prog()}: warning: taxjson.toml: {msg}",
              file=sys.stderr)

# The release verbs (and the parser check, which reads a checkout's
# fixtures) read a git checkout, never a tax project: -C/--dir and the
# project guards do not apply to them.
_RELEASE_CMDS: Tuple[str, ...] = ("channels", "deploy", "promote", "parser-check")


def _help_country(argv: List[str],
                  default_dir: Optional[str] = None) -> Optional[str]:
    """The country of the project the help page is shown for (-C/--dir,
    else `default_dir`: the folder the global options name in another
    spelling argparse takes, `--di DIR`; else the current directory):
    None outside a project or when its country is not set (lib/country's
    resolver, never a guess)."""
    from taxjson.lib.country import settings_country
    d = default_dir or "."
    for i, tok in enumerate(argv):
        if tok in ("-C", "--dir") and i + 1 < len(argv):
            d = argv[i + 1]
        elif tok.startswith("--dir="):
            d = tok[len("--dir="):]
        elif tok.startswith("-C") and len(tok) > 2:
            d = tok[2:]
    root = Path(d)
    try:
        if not _PL.has_config(root):
            return None
        if not _legacy_free(root):
            # The command itself says why the project cannot run; the
            # help page needs only the country, read without the guards.
            cfg = read_toml(root / "taxjson.toml")
            return settings_country(settings_of(cfg))
        # Quiet: a refusal here (a ticker.map that is a folder) is the
        # command's own to say, once — it was printed twice, around
        # `==> Checking the project` (backlog TJ-0127).
        import io as _io
        with contextlib.redirect_stderr(_io.StringIO()), \
                contextlib.redirect_stdout(_io.StringIO()):
            return settings_country(load_settings(root.resolve(), missing_ok=True))
    except (Exception, SystemExit):                     # noqa: BLE001
        # (SystemExit: a config the command itself refuses — e.g. a
        # crypto project with no local_timezone — still gets help.)
        return None


def _legacy_free(root: Path) -> bool:
    from taxjson.lib.migrate import legacy_files
    return not legacy_files(root)


def _no_command(argv: List[str], commands,
                parser: Optional[argparse.ArgumentParser] = None) -> bool:
    """True when argv names no command and holds nothing but -C/--dir
    DIR (in any spelling `parser` takes: `--di DIR` too): `taxjson` or
    `tjs -C DIR` alone prints the help page. Any other token (-h,
    --version, a mistyped command) keeps argparse's own handling."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        act, val = (_global_option(parser, tok) if parser is not None
                    else (None, None))
        is_dir = act is not None and act.dest == "dir"
        if tok in ("-C", "--dir") or (is_dir and val is None):
            if i + 1 >= len(argv):
                return False            # argparse names the missing DIR
            i += 2
            continue
        if tok.startswith("--dir=") or (tok.startswith("-C")
                                         and len(tok) > 2) or is_dir:
            i += 1
            continue
        return False
    # Only -C/--dir options: their values are folders, even one named
    # like a command (`tjs -C run`, #51).
    return True


def _check_tt_files_only(args: argparse.Namespace) -> bool:
    """`check-tt FILE.tt ...` needs no project: the files are named."""
    items = getattr(args, "items", None) or []
    return (args.cmd == "check-tt" and bool(items)
            and all(_tt_path_item(i) for i in items))


def _reads_no_project(args: argparse.Namespace) -> bool:
    """A command that reads no project, run in any folder: `check-tt
    FILE.tt ...` and `fetch --list` (the installed fetcher plugins; the
    installer runs it from the user's own folder — backlog TJ-0081)."""
    return _check_tt_files_only(args) or (
        args.cmd == "fetch" and bool(getattr(args, "list", False)))


def _interrupt_note() -> str:
    if run_state.current_cmd == "run":
        return ("work/ is incomplete — re-run `tjs run` before "
                "trusting any report")
    return ""


def main() -> None:
    """The `taxjson` entry point: Ctrl-C is one `interrupted` line with
    exit 130 and a closed stdout pipe a quiet exit 141, never a
    traceback (re-audit A2-0782, A2-0785); report text the terminal
    cannot encode degrades to '?' (A2-0786)."""
    from taxjson.lib.cli_diag import run_top_level
    # A command that runs another in this process (`_self_cmd`: amt and
    # instalments the estimate) gets its own name and --details back when
    # it returns, and a Ctrl-C in the other stops it too.
    nested = run_state.in_main
    outer = run_state.current_cmd, run_state.current_details
    if not nested:
        # A command run from the top says its once-only warnings as a new
        # process would: a second `main` in one process (a test's) printed
        # less than the first (lib/once).
        once.forget_all()
    run_state.in_main = True
    try:
        with (run_state.nested_command() if nested
              else contextlib.nullcontext()):
            run_top_level(lambda: (f"{_PROG} {run_state.current_cmd}"
                                   if run_state.current_cmd else _PROG),
                          _main, interrupt_note=_interrupt_note, nested=nested)
    finally:
        run_state.current_cmd, run_state.current_details = outer
        run_state.in_main = nested


# Commands whose messages are their report and keep their lines: the
# run (it sets the switch itself), redact's name map (shown only once,
# never written), check-filed's DRIFTED figures.
_OWN_MESSAGES = frozenset({"run", "redact", "check-filed"})


@contextlib.contextmanager
def _brief_messages(args: argparse.Namespace):
    """Essentials first for the messages a command's engine and stages
    print while it runs (an income-year note of a recomputed year, a
    split-share note ...): shown to a person without --details or
    --json, each is one line and a message said more than twice is
    folded into one count line at the end (lib/stage_msg.CONCISE_ENV,
    fold_summary). `run` sets the switch itself (cmd_run); captured
    output (width 0) keeps every byte."""
    import os
    from taxjson.lib import stage_msg
    from taxjson.lib.out import width as _w
    on = (args.cmd in _DETAILS_CMDS and args.cmd not in _OWN_MESSAGES
          and not _details(args) and not getattr(args, "json", False)
          and _w(sys.stderr) > 0)
    old = os.environ.get(stage_msg.CONCISE_ENV)
    if on:
        os.environ[stage_msg.CONCISE_ENV] = "1"
    try:
        yield
    finally:
        _mk = sys.modules.get("taxjson.lib.markets")
        if on and _mk is not None:
            # Its rolled-up notes (said at exit) as one line too.
            _mk.flush_notes()
        stage_msg.fold_summary(sys.stderr)
        if on:
            if old is None:
                os.environ.pop(stage_msg.CONCISE_ENV, None)
            else:
                os.environ[stage_msg.CONCISE_ENV] = old


def _main() -> None:
    # Tax data is private: everything this process and its pipeline
    # stages create is owner-only (files 0600, dirs 0700) whatever the
    # shell umask — SECURITY.md promises it.
    from taxjson.bin._entry import private_umask
    private_umask()
    p, sub = _build_parser(_PROG)
    argv = sys.argv[1:]
    p.help_country = _help_country(argv, _prefix_dir(
        p, argv[:_command_index(p, argv, set(sub.choices))]))
    if _no_command(argv, set(sub.choices), p):
        # `taxjson` / `tjs -C DIR` alone: the help page, exit 0
        # (it was an argparse "COMMAND is required" error, exit 2).
        p.print_help()
        return
    commands = set(sub.choices)
    segments = _split_command_segments(p, argv, commands)
    if "fetch" in argv:
        # `tjs fetch` with no fetcher plugin installed: one line
        # naming the install, exit 2 — whatever options were typed
        # (they belong to the absent plugin, so argparse would only
        # call them unrecognized). Before any segment runs.
        # With none loaded but a taxjson-fetch of another version than
        # this taxjson's: its one line instead (it is not loaded, so its
        # options would read as unrecognized too: backlog TJ-2554).
        for seg in segments:
            if (_command_of(p, seg, commands) == "fetch"
                    and not {"-h", "--help", "--list"} & set(seg)):
                from taxjson.lib.fetchers import installed as _fetchers
                if not _fetchers():
                    _stale_fetcher_exit()
                    _no_fetcher_exit()
    if len(segments) > 1:
        # Validate the WHOLE chain before executing ANY of it — a
        # bad later segment (`tjs events -- 30d`: '30d' is not a
        # command) previously half-ran the chain, executing `events`
        # and then dying rc 2.
        for seg in segments:
            first = _command_of(p, seg, commands)
            if first is None or not _parses_ok(p, seg):
                bad = " ".join(seg)
                _die(f"{bad!r} is not a valid command in "
                         f"this chain — nothing was executed. (A "
                         f"literal `--` separates chained commands; "
                         f"the token after it must start a command.)")
    from taxjson.lib.corp_actions import ManifestError
    for seg in segments:
        args, _extra = p.parse_known_args(seg)
        if _extra:
            # The command's own usage line, not the top-level one: an
            # unknown flag after `option-yield` was answered with
            # `usage: tjs [-h] [-C DIR] ...` (workflow audit 2026-10).
            _subparser(p, args.cmd).error(
                f"unrecognized arguments: {' '.join(_extra)}")
        run_state.current_cmd = args.cmd or ""
        run_state.current_details = _details(args)
        run_state.begin_command()
        run_state.config_warnings_for(Path(args.dir))
        if args.cmd == "run":
            # The run's console: whole lines in order whatever reads
            # them (a pipe, `| tee`), and every line under a step —
            # the checks before the first stage (config warnings, a
            # $0 election, a leftover file) under this one
            # (docs/output-style.md, The run's console).
            _console_line_buffered()
            _step("Checking the project")
        if (args.cmd not in ("init", "help", "redact") + _RELEASE_CMDS
                and not Path(args.dir).is_dir()):
            # `-C typo sum` said "no gains files in typo/work (run
            # `tjs run` first)" — send the user to the real problem.
            _die_input(f"no such directory: {args.dir} (-C/--dir names the "
                 f"project root — the folder holding taxjson.toml)")
        # A project that needs a newer taxjson: before any other check.
        _refuse_newer_projects(args)
        _refuse_years_root(args)
        _refuse_no_project(args)
        if args.cmd not in ("init", "help", "migrate", "checklist") \
                + _RELEASE_CMDS and not _reads_no_project(args):
            # An old per-purpose file (yf_ticker.map, distributions.map
            # ...) stops every command that reads the project (lib/
            # migrate), naming `tjs migrate` — the checklist shows it
            # as its configure item instead (_checklist_config). One
            # that reads none (`fetch --list`, `check-tt FILE.tt`) runs,
            # as the other project guards let it (backlog TJ-0217).
            _refuse_legacy_project_files(Path(args.dir).resolve())
        # A written folder linked outside the project: every command,
        # whatever reads its config (audit 2026-10-25 robustness#3).
        _refuse_outside_links(args)
        _enforce_command_country(args)
        _refuse_artifact_account(args)
        _refuse_path_account(args)
        _refuse_unknown_account(args)
        _refuse_no_input_books(args)
        # Every command of a project reads its ticker.map, the soft-reading
        # ones too (backlog TJ-1029, TJ-1140).
        _root = Path(args.dir).resolve()
        if (_root / "taxjson.toml").is_file():
            _use_project_ticker_map(_root)
        if args.cmd in _RUN_STATE_BANNER_CMDS:
            _banner_run_state(args)
        try:
            # An input a command reads itself (not through a guarded
            # tool) that is missing, a directory, not UTF-8 or not JSON:
            # one `tjs <cmd>: error:` line with exit 2, never a
            # traceback — the S070-23 / S079-10 contract (A2-1420).
            with _brief_messages(args):
                _guarded_func(args)
            _note_shared_decision(args)
        except SystemExit as e:
            # A failing command stops the chain and propagates its
            # code; an explicit success (sys.exit(0) / None) lets the
            # next command run.
            if e.code not in (None, 0):
                raise
        except ManifestError as e:
            # A hand-edited elections manifest that does not load: one
            # line naming the file, never a traceback (audit S072-05);
            # exit 2, an input that cannot be read (docs/commands.md;
            # backlog TJ-0406: `elect --json` said 0, the text 1).
            _show([exit_text(f"tjs {args.cmd}: error: {e}")], sys.stderr)
            sys.exit(2)
        except subprocess.CalledProcessError as e:
            # A pipeline stage failed. The child's own stderr already
            # explained WHY (run_to_file echoes it) — re-raising the
            # CalledProcessError just buried that explanation under a
            # second traceback (2026-09 audit).
            # Its own error headline is repeated here: the explanation
            # may be hundreds of lines up (owner request).
            _why = _failure_headline(
                _stage_text_shown(e.stderr, e.cmd,
                                  getattr(e, "out_path", None))
                if isinstance(e.stderr, str) else e.stderr)
            sys.exit(exit_text(
                f"taxjson: stopped at {_stage_description(e.cmd)}"
                + (f": {_why} (exit {e.returncode}; details above)"
                   if _why else
                   f" (exit {e.returncode}) — see the error above.")))
    return


# A failed stage's error line: `[<prog>: ]error: <what>` (or the last
# line of a traceback, `ValueError: <what>`).
_FAIL_LINE_RE = re.compile(r"^(?:[\w./-]+:\s+)?(?:error|fatal):\s*(?P<m>.+)$",
                           re.IGNORECASE)
_TRACE_LAST_RE = re.compile(r"^(?:[\w.]+(?:Error|Exception)):\s*(?P<m>.+)$")
# The longest headline the run's last line repeats.
_FAIL_HEAD_MAX = 160


def _failure_headline(stderr: Any) -> str:
    """The headline of a failed stage's own error (its last `error:`
    line, else a traceback's last line), for the run's final line — ''
    when its stderr says none."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    if not stderr:
        return ""
    found = ""
    for line in str(stderr).splitlines():
        line = line.strip()
        m = _FAIL_LINE_RE.match(line) or _TRACE_LAST_RE.match(line)
        if m:
            found = m["m"].strip()
    if not found:
        return ""
    from taxjson.lib.out import split_message
    head = split_message(found)[0]
    if len(head) > _FAIL_HEAD_MAX:
        head = head[:_FAIL_HEAD_MAX - 1].rstrip() + "…"
    return head


# What each sub-tool does, for the one-line stage failure (new-user
# study: the raw argv tail — two file paths and a flag — said nothing).
_STAGE_WHAT = {
    "taxjson_brokerage": "reading the broker files",
    "taxjson_corp_actions": "the corporate actions",
    "taxjson_convert_tt": "converting the .tt file",
    "taxjson_merge": "merging the account's files",
    "taxjson_merge2": "merging and converting the account's books",
    "taxjson_sort": "sorting the books",
    "taxjson_ticker_map": "applying ticker.map",
    "fill_crypto_prices": "filling in crypto prices",
    "taxjson_convert_currency": "converting to the base currency",
    "taxjson_validate": "checking the books",
    "taxjson_gains": "computing the gains",
    "to_base_curr": "fetching currency rates",
    "taxjson_apply_distributions": "applying [[distributions]]",
}


# The sub-tool options `tjs run` passes that take no value.
_STAGE_FLAG_OPTS = frozenset({
    "--strict", "--dedup", "--sort", "--validate", "--transfers",
    "--require-inputs", "--no-input", "--global-only", "--spot-crypto",
    "--no-wash", "--require-prices", "--combined-broker-accounts",
    "--taxable", "--lint", "--decisions-only"})


def _stage_description(cmd) -> str:
    """A human description of a failed sub-tool invocation: what it was
    doing, for which account, on which input file(s) — never the raw
    argv (paths, flags)."""
    from taxjson.pipeline.dispatch import tool_module
    argv = [str(c) for c in (cmd or [])]
    spec = tool_module(argv)
    if spec is not None:
        # The module and its arguments: never the interpreter's options
        # (python_module_argv's `-P` read as an input file).
        argv = [spec[0], *spec[1]]
    mod = next((a.rsplit(".", 1)[-1] for a in argv
                if a.startswith("taxjson.bin.")), "")
    what = _STAGE_WHAT.get(mod) or (f"the {mod.replace('_', '-')} step"
                                    if mod else "a pipeline step")

    def _opt(*names):
        for n in names:
            if n in argv[:-1]:
                return argv[argv.index(n) + 1]
        return ""
    acct = _opt("--account", "--account-name")
    # The inputs are the trailing positionals (every stage appends them
    # last); an argument right after a value-taking option is its value.
    files: List[str] = []
    i = len(argv) - 1
    while i > 0 and not argv[i].startswith("-"):
        prev = argv[i - 1]
        if prev.startswith("-") and prev not in _STAGE_FLAG_OPTS:
            break
        files.insert(0, Path(argv[i]).name)
        i -= 1
    out = what
    if acct:
        out += f" for account {acct}"
    if files:
        shown = ", ".join(_mask_ids_in_path(f) for f in files[:3])
        out += (f" ({shown}" + (f" and {len(files) - 3} more" if
                                len(files) > 3 else "") + ")")
    return out


def _guarded_func(args: argparse.Namespace) -> None:
    from taxjson.lib.cli_diag import error, guard_main
    from taxjson.lib.json_input import InputFileError
    prog = f"{_PROG} {args.cmd}"
    try:
        guard_main(prog)(args.func)(args)
    except InputFileError as e:
        error(prog, str(e))
        raise SystemExit(2)


_ACCOUNT_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def _refuse_path_account(args: argparse.Namespace) -> None:
    """An ACCOUNT argument must be an account NAME (the spelling
    load_config allows in [accounts]): './margin' or
    '../../other/work/margin' was joined into work/ paths — another
    project's books were read and printed, `sum ./margin` was a
    traceback — and fees-sum used the name as a glob ('*' summed every
    account, '/' tracebacked) (re-audit A2-1398, A2-0798)."""
    _acct = getattr(args, "account", None)
    if (getattr(args, "write_missing_history", None)
            and isinstance(_acct, str) and _acct
            and (Path(_acct).suffix or "/" in _acct)
            and not _is_configured_account(
                Path(getattr(args, "dir", None) or ".").resolve(), _acct)):
        # (A configured account named with a period, `margin.old`, is
        # the account: backlog TJ-0816.)
        _refuse_missing_history_file(_acct)
    vals = []
    for attr in ("account", "period"):
        v = getattr(args, attr, None)
        vals += v if isinstance(v, list) else [v]
    for v in vals:
        if not isinstance(v, str) or not v:
            continue
        if v is getattr(args, "period", None) and (
                _is_period(v) or v.strip()[:1].isdigit()):
            continue                # a PERIOD, judged by its command
        if not _ACCOUNT_NAME_RE.fullmatch(v):
            from taxjson.lib.config_check import ACCOUNT_NAME_RULE
            _die(f"{v!r} is not an account name — give the name of an "
                 f"[accounts.<name>] section in taxjson.toml "
                 f"({ACCOUNT_NAME_RULE}), not a path or a pattern.")


def _refuse_artifact_account(args: argparse.Namespace) -> None:
    """An ACCOUNT argument that is a pipeline artifact's name (`margin_raw`,
    `margin_raw_base`, `x_gains` ...) is never an account — the config
    refuses those names — yet sum, list, winners, wash-sales and their
    twins read work/<name>_gains.json and printed the native-currency
    books under a base-currency header (re-audit A2-0394). Refused once,
    for every command."""
    from taxjson.lib.config_check import RESERVED_NAME_SUFFIXES
    # `period` too: `winners [PERIOD] [ACCOUNT]` takes a lone name in
    # the first slot as the account.
    vals = []
    for attr in ("account", "period"):
        v = getattr(args, attr, None)
        vals += v if isinstance(v, list) else [v]
    for v in vals:
        if not isinstance(v, str):
            continue
        low = v.lower()
        suf = next((x for x in RESERVED_NAME_SUFFIXES if low.endswith(x)),
                   None)
        if suf:
            _die(f"{v!r} is not an account: names ending in {suf!r} are "
                 f"pipeline work files (an account's native or "
                 f"intermediate books), never accounts — use an account "
                 f"name from [accounts] in taxjson.toml.")


# Commands that run in the folder holding a multi-year project's year
# folders (lib/project_layout.multi_root): they read no year's project.
# Every other command is refused there, naming the year folders — a
# year is never guessed.
_YEARS_ROOT_CMDS = frozenset({
    "init", "help", "tax-logic", "redact", "years", "new-year",
    # What the years share: the per-year tobase.map copies made one file
    # (migrate), that file brought in step with the master.
    "migrate", "update-tobase-map",
    # Every year's generated files removed (lib/clean; it refuses a folder
    # that is no project itself).
    "clean"}) | frozenset(_RELEASE_CMDS)


# The commands (and the flags that make them write) that save a decision
# into an account's folder: with exports shared by every year, said.
_DECISION_WRITERS: Dict[str, Tuple[str, ...]] = {
    "elect": ("set", "redo", "reset"),
    "crypto-sends": ("set", "unset", "write"),
}


def _note_shared_decision(args: argparse.Namespace) -> None:
    """After `elect --set` / `crypto-sends --set` in a project whose
    exports are shared (lib/project_layout): the decision is saved in
    the shared folder, so every year reading it applies it."""
    flags = _DECISION_WRITERS.get(args.cmd)
    if not flags or not any(getattr(args, f, None) not in (None, False, [])
                            for f in flags):
        return
    root = Path(args.dir).resolve()
    if not _PL.shared_inputs(root):
        return
    _say("note", f"saved in {_PL.shown(_PL.inputs_dir(root), root)}/"
         f"<account>/ (manifest.json, sends.json): every year reading "
         f"these exports applies it on its next run",
         *_frozen_years_apart(root))


# Commands that run with no project, or say themselves what is missing:
# the years-root commands, the checklist (every step, outside a
# project) and format-map (a ticker.map of its own).
_NO_PROJECT_CMDS = _YEARS_ROOT_CMDS | frozenset({"checklist",
                                                  "format-map"})


# The commands the project-level link check leaves alone: no project
# (help, tax-logic, the release tools), init (its own check of the
# folders it creates: _init_refuse_outside_links) and redact (it writes
# only its own inputs_redact/, refusing a link there, and follows no
# folder link: docs/commands.md).
_LINK_CHECK_FREE_CMDS = frozenset({"help", "tax-logic", "init", "redact"}
                                  ) | frozenset(_RELEASE_CMDS)


def _refuse_outside_links(args: argparse.Namespace) -> None:
    """Every command run in a project, before it reads or writes
    anything: a folder taxjson writes into (_WRITTEN_DIRS, each account's
    inputs/ folder) that is a symlink leaving the project stops it, exit
    2 (_refuse_outside_dir_links). load_config refused it only for the
    commands that read the project through it: watch, the radar family,
    the listings, audit and ~30 more read taxjson.toml their own way,
    and watch wrote work/.watch_state.json through such a work/ link
    (audit 2026-10-25 robustness#3). The account names come from a
    lenient read; a taxjson.toml that does not parse is left to the
    command's own reader, which says why."""
    if (args.cmd in _LINK_CHECK_FREE_CMDS or not Path(args.dir).is_dir()
            or _reads_no_project(args)):
        return
    root = Path(args.dir).resolve()
    cfg_path = root / _PL.CONFIG
    if not cfg_path.is_file():
        return
    try:
        cfg = read_toml(cfg_path)
    except Exception:                                   # noqa: BLE001
        return
    accounts = cfg.get("accounts")
    names = {str(n): a for n, a in accounts.items()
             if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", str(n))
             } if isinstance(accounts, dict) else {}
    _refuse_outside_dir_links(root, names)


def _refuse_no_project(args: argparse.Namespace) -> None:
    """Stop a project command in a folder that is no project (no
    taxjson.toml, no year folders): each said "no gains files
    in work/ — run `tjs run` first", or blamed a key missing from a
    taxjson.toml that does not exist (workflow audit 2026-10-22, item
    15). Every one of them reads the country from taxjson.toml, so a
    folder with a work/ but no taxjson.toml is no project either."""
    if args.cmd in _NO_PROJECT_CMDS or not Path(args.dir).is_dir() \
            or _reads_no_project(args):
        return
    root = Path(args.dir)
    # (a taxjson.toml that is a folder or unreadable: load_config says so)
    if _os_path_lexists(root / _PL.CONFIG) or _PL.year_dirs(root):
        return
    _die(*_no_project_message(root.resolve(), args.cmd))


def _refuse_years_root(args: argparse.Namespace) -> None:
    """Stop a command that needs a project in the folder that holds the
    year folders (it has no taxjson.toml of its own)."""
    if args.cmd in _YEARS_ROOT_CMDS or not Path(args.dir).is_dir() \
            or _reads_no_project(args):
        return
    if _PL.has_config(args.dir):
        return
    years = [y for y, _p in _PL.year_dirs(args.dir)]
    if not years:
        return
    import os as _os
    y = years[-1]
    here = args.dir in (".", "./", "")
    where = str(y) if here else _os.path.join(args.dir, str(y))
    hint = (f"Run it in a year folder: `cd {where}`, or `{_PROG} -C "
            f"{where} {args.cmd}`.")
    if args.cmd == "check-filed":
        # The newest year is usually open (no lock to check): name the
        # folders that hold one (backlog TJ-0092).
        filed = [p.name if here else _os.path.join(args.dir, p.name)
                 for p in _filed_year_folders(Path(args.dir))]
        hint = (("Run it in each filed year's folder: "
                 + ", ".join(f"`{_PROG} -C {w} check-filed`"
                             for w in filed) + ".") if filed else
                "No year folder holds a filed/<year>.json lock yet — "
                "`tjs close-year` in a year's folder creates one "
                "after you file.")
    _die_input(f"`{args.cmd}` works on one year's project, and this "
               f"folder holds the year folders "
               f"{', '.join(str(x) for x in years)} (with the exports "
               f"they share)",
               f"{hint} `{_PROG} years` lists the years.")


# Commands that read the work/ books and print figures or verdicts from
# them: each prints the run-state banner (inputs changed since the last
# full run, validation ERRORs, a deferred account, an account with
# inputs but no books) before it runs, on stderr so --json stays valid.
# Only sum/t1135/carryover/form-export/check-filed had it; every other
# view served a failed run's books with rc 0 and no word (audit A2-0380,
# A2-0381). Those five print it themselves and are not listed here, nor
# are close-year, plot-tax, estimate and amt (they call
# _warn_run_state) or slip-audit (after its --template and --import-cra
# branches, which read no books).
_RUN_STATE_BANNER_CMDS = frozenset({
    "gains", "roc-sum", "divs-sum", "dil-sum", "wash-sales", "winners",
    "losers", "trades-sum", "fx-cash", "leaps", "leaps-sum", "fees-sum",
    "fees", "shares", "list", "ccd-sum", "trades", "divs", "dil", "roc",
    "staking", "staking-sum",
    "events", "wash-radar", "sell-check", "buy-check", "rebuy",
    "div-estimate", "harvest", "watch", "edge-cases", "option-boundary", "spinoffs", "splits",
    "check-dates", "stats", "option-yield", "win-rates", "compare",
    "technicals",
    # The step-5 detectors read the books too (backlog TJ-0077).
    "find-missing-history", "sanity", "transfers",
    # The review commands over the saved books: audit's tie-out passed
    # and journals, renames and cash showed figures from books older
    # than an edited input, with no word (backlog TJ-0557); the slips
    # and the year's handoff are checked against the books too.
    "audit", "journals", "renames", "cash", "reconcile-slips", "handoff",
})


def _banner_run_state(args: argparse.Namespace) -> None:
    root = Path(args.dir).resolve()
    if not _PL.has_config(root) or not (root / "work").is_dir():
        return
    try:
        cfg = load_config(root, missing_ok=True)
    except Exception:                                   # noqa: BLE001
        return
    if _reads_cash_events(args, cfg):
        # Refused before the banner, whose "The figures below may..." would
        # head no figures (review of commit 6).
        _refuse_cash_older_than_inputs(root, cfg)
    _warn_run_state(root, cfg)


def _reads_cash_events(args: argparse.Namespace, cfg: Dict[str, Any]) -> bool:
    """Whether the command reads the parse's cash events (lib/cash_events
    `collect`): `tjs cash`, and `tjs fx-cash` on the ledger v2."""
    if args.cmd == "cash":
        return True
    if args.cmd != "fx-cash":
        return False
    from taxjson.pipeline.run import _fx_cash_ledger_choice
    try:
        return _fx_cash_ledger_choice(settings_of(cfg),
                                      getattr(args, "ledger", None)) == "v2"
    except (SystemExit, ValueError, TypeError):
        return False        # (the command says what is wrong with the choice)


# The report commands that print the run-state banner themselves: like
# _RUN_STATE_BANNER_CMDS, they read the books.
_NO_INPUT_REPORT_CMDS = _RUN_STATE_BANNER_CMDS | frozenset({
    "sum", "t1135", "carryover", "form-export", "gains-base", "gains-cra"})


def _refuse_no_input_books(args: argparse.Namespace) -> None:
    """Stop a report command when the last `tjs run` had no input at
    all (every configured account skipped for an empty folder, which
    still is, and no books in work/), naming the empty folders as the
    project reads them (../inputs/margin with shared exports): `sum`
    said "Run `tjs run` first" right after that run exited 0 with
    "nothing was computed" (backlog TJ-0155). The fix is the export, not
    another run, so the run is not named (option-boundary's contract,
    workflow audit 2026-10-22 item 12)."""
    if args.cmd not in _NO_INPUT_REPORT_CMDS:
        return
    root = Path(args.dir).resolve()
    cache = root / "work"
    if not _PL.has_config(root) \
            or not (cache / _SKIPPED_ACCOUNTS_FILE).is_file():
        return
    try:
        accounts = [str(n) for n, a in (load_config(root, missing_ok=True).get("accounts")
                                        or {}).items()
                    if isinstance(a, dict)]
        books = any(not p.name.startswith(".")
                    for pat in ("*_base.json", "*_gains.json",
                                "*_gains_wash.json")
                    for p in cache.glob(pat))
    except Exception:                                   # noqa: BLE001
        return
    if not accounts or books \
            or not set(accounts) <= _accounts_skipped_for_no_inputs(root):
        return
    _empty = [_PL.shown(_PL.inputs_dir(root) / n, root) for n in accounts]
    _die(f"the last run had no input: {', '.join(_empty[:3])}"
         + (f" +{len(_empty) - 3} more" if len(_empty) > 3 else "")
         + f" {'is' if len(_empty) == 1 else 'are'} empty, so it made no "
           f"books",
         "Save each account's exports there (docs/brokers.md), or delete "
         "the [accounts.NAME] table of an account you do not have.")


# Commands whose optional `account` positional names a configured
# account (with a leading optional PERIOD for the period-taking ones):
# a mistyped name gave an empty report with rc 0, or blamed a missing
# run (audit A2-0684, A2-1167; wash-radar: backlog TJ-0157). Refused in
# one line here.
_ACCOUNT_ARG_CMDS = frozenset({
    "edge-cases", "spinoffs", "splits", "check-dates", "winners", "losers",
    "leaps", "leaps-sum", "stats", "wash-radar",
})


def _refuse_unknown_account(args: argparse.Namespace) -> None:
    if args.cmd not in _ACCOUNT_ARG_CMDS:
        return
    account = getattr(args, "account", None)
    period = getattr(args, "period", None)
    if account is None and period and not _is_period(period):
        if period.strip()[:1].isdigit():
            return                     # the command names the bad window
        account = period
    if not account:
        return
    root = Path(args.dir).resolve()
    if not _PL.has_config(root):
        return
    accounts = load_config(root, missing_ok=True).get("accounts") or {}
    if not isinstance(accounts, dict) or not accounts or account in accounts:
        return
    _die_input(f"no [accounts.{account}] in taxjson.toml — check the name "
               f"(configured: {', '.join(sorted(accounts))}).")


def _enforce_command_country(args: argparse.Namespace) -> None:
    """Refuse a command (or command:variant) the project's country does
    not own — lib/country.COMMAND_COUNTRY — before it runs, once, for
    every entry point (partition audit R3: t1135, option-boundary and
    form-export --form schedule3 ran in US projects and gave Canadian
    advice; 8949/txf in a Canada project failed only by accident)."""
    from taxjson.lib.country import (command_country, command_country_problem,
                                     flag_country_problems, given_flags)
    cmd = getattr(args, "cmd", "") or ""
    variant = getattr(args, "form", None) if cmd == "form-export" else None
    flags = {f: v for f, v in given_flags(args).items()
             if v not in (None, False, "")}
    if command_country(cmd, variant) is None and not flags:
        return
    settings = load_settings(Path(args.dir).resolve(), missing_ok=True)
    if settings.empty and cmd in _YEARS_ROOT_CMDS:
        # The folder holding the year folders: the newest year's
        # country (a command that works on what the years share).
        _yrs = _PL.year_dirs(Path(args.dir).resolve())
        if _yrs:
            settings = load_settings(_yrs[-1][1], missing_ok=True)
    if settings.empty:
        if not _PL.has_config(Path(args.dir)) \
                and not _PL.year_dirs(Path(args.dir)):
            _die(*_no_project_message(Path(args.dir).resolve(), cmd))
        _die("no taxjson.toml here — this command needs a project "
             "(its country decides whether it applies).")
    country = _country(settings)
    msg = command_country_problem(cmd, country, variant)
    if msg:
        _die(msg)
    # `estimate --province XX` in a US project was silently ignored
    # (partition COMMANDS-10 / SPEC-09): FLAG_COUNTRY, like the config.
    problems = flag_country_problems(country, flags)
    if problems:
        _die("; ".join(problems))


def _parses_ok(parser: argparse.ArgumentParser, seg: List[str]) -> bool:
    """Would argparse accept `seg` as one complete command? Trial parse
    with stderr suppressed — argparse raises SystemExit on rejection."""
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            # stdout too: argparse prints --help THERE, and a trial
            # parse hitting -h leaked the full help block into the
            # real output stream.
            parser.parse_args(seg)
        return True
    except SystemExit:
        return False


def _global_option(parser: argparse.ArgumentParser, tok: str
                   ) -> Tuple[Optional[argparse.Action], Optional[str]]:
    """The option of `parser`'s own that `tok` names, as argparse reads
    it — the exact spelling, an unambiguous abbreviation of a long one
    (`--di`), with its value in the token (`--dir=DIR`, `--di=DIR`,
    `-CDIR`) — and that value (None: none in the token). (None, None)
    for any other token."""
    acts = parser._option_string_actions                # noqa: SLF001
    act = acts.get(tok)
    if act is not None:
        return act, None
    if tok.startswith("--"):
        name, eq, val = tok.partition("=")
        act = acts.get(name)
        if act is None:
            hits = {id(a): a for s, a in acts.items()
                    if s.startswith("--") and s.startswith(name)}
            act = next(iter(hits.values())) if len(hits) == 1 else None
        return (act, val if eq else None) if act is not None \
            else (None, None)
    if tok.startswith("-") and len(tok) > 2:
        act = acts.get(tok[:2])
        if act is not None and act.nargs != 0:
            return act, tok[2:]
    return None, None


def _takes_separate_value(parser: argparse.ArgumentParser,
                          tok: str) -> bool:
    """Is `tok` one of `parser`'s own options whose value is the NEXT
    token (`-C DIR`, `--dir DIR`, an unambiguous `--di DIR`)? Not for
    `--dir=DIR` / `-CDIR` (one token) or a flag (`--version`)."""
    act, val = _global_option(parser, tok)
    return act is not None and act.nargs != 0 and val is None


def _prefix_dir(parser: argparse.ArgumentParser, prefix: List[str]
                ) -> Optional[str]:
    """The project folder the global options before the command name
    (-C/--dir in any spelling argparse takes: `--di DIR`, `--di=DIR`,
    `-CDIR`; the last one wins, as in argparse), or None."""
    d = None
    i = 0
    while i < len(prefix):
        act, val = _global_option(parser, prefix[i])
        if act is not None and act.dest == "dir":
            if val is not None:
                d = val
            elif i + 1 < len(prefix):
                d = prefix[i + 1]
        i += 2 if _takes_separate_value(parser, prefix[i]) else 1
    return d


def _command_index(parser: argparse.ArgumentParser, argv: List[str],
                   commands: set) -> int:
    """Where the command starts in `argv` (len(argv): no command). The
    tokens before it are the global options, a value of theirs skipped
    even when it is named like a command: `-C run run sum` is the
    project folder run, then `run` (#51)."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in commands:
            return i
        i += 2 if _takes_separate_value(parser, tok) else 1
    return len(argv)


def _command_of(parser: argparse.ArgumentParser, seg: List[str],
                commands: set) -> Optional[str]:
    i = _command_index(parser, seg, commands)
    return seg[i] if i < len(seg) else None


def _subparser(parser: argparse.ArgumentParser, cmd: Optional[str]
               ) -> argparse.ArgumentParser:
    """The parser of command `cmd` (its usage line in an error), or the
    top-level one when there is none."""
    for a in parser._actions:                       # noqa: SLF001
        if isinstance(a, argparse._SubParsersAction):  # noqa: SLF001
            sp = a.choices.get(cmd or "")
            if sp is not None:
                return sp
    return parser


def _trial_parse(parser: argparse.ArgumentParser, seg: List[str]
                 ) -> Optional[argparse.Namespace]:
    """`seg` parsed as one complete command, or None (argparse would
    refuse it). Output suppressed, as in _parses_ok."""
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            return parser.parse_args(seg)
    except SystemExit:
        return None


# Positionals naming one of the project's accounts (PERIOD too: a lone
# word that is no period is read as the account — `events margin`). A
# command name there is the account only when the project has one of
# that name; otherwise it starts the next command (`fetch run`).
_CHAIN_ACCOUNT_DESTS = frozenset({"account", "period", "items"})
# Positionals a command name can never be (a date).
_CHAIN_NEVER_DESTS = frozenset({"when"})


def _chain_accounts(parser: argparse.ArgumentParser,
                    prefix: List[str]) -> set:
    """The account names of the project the global prefix names (-C or
    --dir in any spelling argparse takes, `--di DIR` too: backlog
    TJ-0553; else the current folder), soft-read: an empty set when
    there is no readable taxjson.toml (the command itself says why)."""
    d = _prefix_dir(parser, prefix) or "."
    try:
        doc = read_toml((Path(d) / "taxjson.toml"))
    except Exception:                                   # noqa: BLE001
        return set()
    accounts = doc.get("accounts") if isinstance(doc, dict) else None
    return set(accounts) if isinstance(accounts, dict) else set()


def _argument_taking(before: argparse.Namespace,
                     after: argparse.Namespace) -> Optional[str]:
    """The destination whose value changed between two trial parses of
    one command (the one a further token was given to)."""
    for k, v in vars(after).items():
        if getattr(before, k, None) != v:
            return k
    return None


def _split_command_segments(parser: argparse.ArgumentParser,
                            argv: List[str],
                            commands: set) -> List[List[str]]:
    """Split one argv into per-command segments so `tjs run sum`
    (or `tjs run --strict sum --json`) executes the commands in
    order.

    Everything before the FIRST command token is a global prefix
    (`-C DIR`, a value named like a command included), re-applied to
    every segment. After that, a literal `--` ends a complete command
    and the next one starts (the explicit separator). A bare command
    name starts the next command only when the segment built so far is
    a COMPLETE valid command that cannot take the word as an argument:
    `run --strict sum` splits (run takes no positional), `run --account
    sum` does not (the option's value), and neither does `events sum`
    when the project has an account named sum, `init sum` (the folder)
    or `audit sum` (a symbol) — a valid single command is never split
    (#52). `events sum` with no such account is `events`, then `sum`.
    """
    start = _command_index(parser, argv, commands)
    if start == len(argv):              # no command at all — let
        return [argv]                   # argparse print its usual error
    prefix: List[str] = argv[:start]
    accounts: Optional[set] = None
    segments: List[List[str]] = []
    cur: List[str] = [argv[start]]
    i = start + 1
    while i < len(argv):
        tok = argv[i]
        if cur and cur[0] == "redact":
            # Every later token is redact's: its FILEs are optional (no
            # FILE = the project's inputs/), so `redact` alone parses and
            # `tjs redact -- --check` (a file named --check) or a
            # file named like a command would otherwise start a chain.
            cur.append(tok)
        elif tok == "--" and cur and _parses_ok(parser, prefix + cur):
            segments.append(cur)
            cur = []
        elif (tok in commands
                and cur                 # empty right after a `--`
                and cur[0] != "help"):  # `tjs help divs` — help's
                                        # argument IS a command name
            now = _trial_parse(parser, prefix + cur)
            more = (_trial_parse(parser, prefix + cur + [tok])
                    if now is not None else None)
            dest = _argument_taking(now, more) if more is not None else None
            if now is None:
                split = False           # incomplete: tok is its argument
            elif more is None or dest in _CHAIN_NEVER_DESTS:
                split = True            # it cannot take tok
            elif dest in _CHAIN_ACCOUNT_DESTS:
                if accounts is None:
                    accounts = _chain_accounts(parser, prefix)
                split = tok not in accounts
            else:
                split = False           # a folder, a symbol, a value
            if split:
                segments.append(cur)
                cur = [tok]
            else:
                cur.append(tok)
        else:
            cur.append(tok)
        i += 1
    if cur:
        segments.append(cur)
    return [prefix + s for s in segments if s]

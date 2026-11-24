"""What one process's commands share and rebind.

A module's `from x import y` copies a binding, so a value more than one module
reads and any of them rebinds has to be reached through this module:
`run_state.current_cmd`, never `from ...run_state import current_cmd`.

`RunState` is what one `tjs run` keeps in memory between its stages, and what a
command has already warned about. A command another one runs in this process
gets a copy (`nested_command`), so what it says does not count as said for its
caller, whose user may never see it (its stderr can be dropped), unless the caller
shows it (`relayed_nested`).
"""
import contextlib
import copy
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from taxjson.lib import once

# The command currently executing (set by main's dispatch loop) so
# shared helpers' errors can say WHICH command in a chain failed.
current_cmd = ""
# --details on the command currently executing (docs/output-style.md,
# Essentials first): shared helpers print a warning's detail lines only
# then; the default view shows its headline.
current_details = False
# True while `main` is on the stack: a `main` called then is a command another
# one runs in this process (dispatch.run_cmd).
in_main = False
# The project's taxjson.toml, set by the run: an implicit input of every
# needs_rebuild call.
config_path: Optional[Path] = None
# The newest file time in the package, read once per process.
package_fingerprint: Optional[str] = None
# The held work/.run.lock (a contextlib.ExitStack): `tjs run run` chains.
run_lock: Any = None


@dataclass
class RunState:
    """What the stages of one `tjs run` leave for each other in memory, and
    the warnings a command says once."""
    # Accounts whose per-file detection lines this run printed already (a
    # crypto account is staged twice: parse first, then books).
    detection_shown: Set[Any] = field(default_factory=set)
    # The broker parses this run made, by output: (what the parse read: its
    # command and the bytes of every file it reads; what it wrote: its outputs
    # as left on disk). An account read in the first pass (transfers / sends
    # between your accounts) is staged again for its books: the same command
    # over the same bytes is the same parse, so the books' pass takes it
    # instead of parsing (and printing its messages) a second time
    # (_reused_parse).
    parsed: Dict[Path, Tuple[Any, Any]] = field(default_factory=dict)
    # Run-console messages a stage shows once per run although the account is
    # staged twice (first pass, then books): their keys.
    shown: Set[Any] = field(default_factory=set)
    # The gains files of this run's passes WITH the sheltered context (the
    # blended passes, a crypto account's wash pass): their
    # `transfers_in_loss_windows` make the run's one transfer warning.
    loss_context_gains: List[Path] = field(default_factory=list)
    # The cross-listing joins this run computed: {work dir: (map path or None,
    # analysis)}, once per run (lib/cross_listings).
    cross_listings: Dict[Path, Tuple[Optional[Path], Dict[str, Any]]] = field(default_factory=dict)
    # The dated events the project's .tt files declare (lib/dated_events):
    # {work dir: Declarations}, read once per run, up front (cmd_run), and
    # settled against the broker's legs once (stage_dated_events).
    dated_events: Dict[Path, Any] = field(default_factory=dict)
    dated_settled: Set[Any] = field(default_factory=set)
    # {work dir: stages._InKindState}, computed once per run.
    in_kind: Dict[Path, Any] = field(default_factory=dict)
    # The accounts whose gains this run built or echoed, in run order: (name,
    # base book, gains book, taxable equity), for _report_short_positions.
    short_sources: List[Tuple[str, Path, Path, bool]] = field(default_factory=list)
    # The cost rows this run's blended pass flagged on a listing no dated
    # row shows held (stages._flag_undated_listings), as --strict names them.
    undated_rows: List[str] = field(default_factory=list)
    # The currency-suffix groups staged this run: {(work dir, account):
    # {broker: csvs}} (stage_listing_suffix; the first pass re-reads a group
    # whose listings changed once every account is parsed).
    listing_groups: Dict[Tuple[Path, str], Dict[str, List[Path]]] = field(default_factory=dict)
    # The run's console left a message's detail out (_run_concise): the closing
    # lines then name `tjs run --details`.
    details_hidden: bool = False
    # The continuation lines of a message _echo_captured left out are next.
    echo_skip: bool = False
    # The last message _echo_captured showed was an error (its continuation
    # lines are shown whole).
    echo_last_error: bool = False
    # taxjson.toml warnings said for `config_project`: a new run does not repeat
    # them, another project's file gets its own.
    config_warned: Set[str] = field(default_factory=set)
    # What needs_rebuild said was stale and from what: vouched for by
    # commit_records when the run completes (pipeline/freshness).
    pending_records: Dict[Path, Dict[str, Any]] = field(default_factory=dict)
    # The outputs the stage table's executor ran (built or found fresh), each
    # with what names its instance (its row, account, broker group, .tt file
    # and blend tag: pipeline/stage_table `_ID`): kept by output, as two rows
    # share one (base and crypto_base, gains_all and gains_all_split), so the
    # sweep of the outputs no instance built takes none another row builds
    # now (pipeline/stage_table `sweep_vanished`, `commit_run`).
    stage_outputs: Dict[Path, Tuple[str, Optional[str], Optional[str], Optional[str],
                                    Optional[str]]] = field(default_factory=dict)
    # A full `tjs run` (no --account): what the last run built and this one
    # does not, it sweeps (pipeline/stage_table `sweep_vanished`, TJ-4265).
    full_run: bool = False
    # The accounts whose wash-adjusted gains this run built or kept as
    # current (stages._wash_preferred_gains reads it).
    wash_current: Set[str] = field(default_factory=set)
    # One content hash per (path, size, mtime_ns, inode) this run read
    # (pipeline/freshness._file_hash).
    hash_cache: Dict[Tuple[str, int, int, int], str] = field(default_factory=dict)
    config_project: Optional[Path] = None
    # A lone positional that names a period and an account (_warn_period_or_account).
    ambiguous_warned: Set[str] = field(default_factory=set)
    # Base books already named as missing (warned_missing_base_once).
    missing_base_warned: Set[Any] = field(default_factory=set)
    # The full-history gains this command built (cli/common `_gains_all_file`,
    # `_gains_all_blend`), by absolute path: a view builds them every time it
    # runs, and a later read in the same command (another account of the
    # blend, the grant expiries, a pool's covers) takes the one it built. Its
    # own build, not a record's word: emptied as each command starts
    # (`begin_command`), and by `begin_run`.
    views_built: Set[str] = field(default_factory=set)
    # The broker parses and corporate-action reads this run staged, by
    # (work dir, account): their outputs, which `spellings_checked`
    # reads together (lib/symbol_case, backlog TJ-4606).
    broker_sources: Dict[Tuple[Path, str], List[Path]] = field(default_factory=dict)


# The run's state. Read it as `run_state.current.x` every time: `begin_run`
# replaces the object.
current = RunState()


# What the last nested command's state came to, for `relayed_nested`.
_last_nested: Optional[RunState] = None


def begin_run(*, full: bool = False) -> None:
    """The state of a new `tjs run` (`full`: no --account): empty, and `lib`'s
    warnings said again, but for what taxjson.toml's warnings have said (that
    file has not changed)."""
    global current
    once.forget_all()
    current = RunState(config_warned=current.config_warned,
                       config_project=current.config_project, full_run=full)


def begin_command() -> None:
    """A command starts (each of a chain, `tjs stats all -- winners all`):
    what an earlier one built for its own reads serves it no more
    (`views_built`)."""
    current.views_built.clear()


def config_warnings_for(root: Path) -> None:
    """The command about to run reads this project: `tjs -C a sum -- -C b sum`
    says b's taxjson.toml warnings even when a's have the same words."""
    root = Path(root).resolve()
    if current.config_project not in (None, root):
        current.config_warned = set()
    current.config_project = root


@contextlib.contextmanager
def nested_command() -> Iterator[None]:
    """For a command another one runs in this process (`main`, nested): it
    starts knowing what its caller said, so it doesn't repeat that into a stderr
    its caller may show, and what it says is forgotten when it returns."""
    global current, _last_nested
    outer = current
    current = RunState(**{f.name: copy.copy(getattr(outer, f.name))
                          for f in fields(RunState)})
    try:
        with once.nested():
            yield
    finally:
        _last_nested = current
        current = outer


def relayed_nested() -> None:
    """The caller showed the last nested command's stderr to the person: what
    it said is said, so a later command in the chain doesn't say it again."""
    global _last_nested
    if _last_nested is not None:
        for f in fields(RunState):
            mine = getattr(current, f.name)
            if isinstance(mine, set):
                mine.update(getattr(_last_nested, f.name))
        _last_nested = None
    once.adopt_nested()

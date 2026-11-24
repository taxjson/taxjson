"""The stages a run builds, as a table (PLAN step 12; the design's section 2).

One row per output a `needs_rebuild` ask decides (pipeline/freshness): the
stage's name, what it is built per (PER: the project, an account, a broker
group of an account, a .tt file, a blend of accounts), its output, its inputs
as typed references, when it runs and the grounds of its own it rebuilds on.

`execute` runs every row, of a table its steps are bound to (`bound`: the
module whose code they are binds them, as this one cannot import it; the run's
stages, run.py's sheltered merge and cli/common.py's on-demand builders each
bind their own): for each instance `when` admits, `prepare` (the stamps the
ask reads, written first), the ask over the inputs the row declares, `build`,
and `every_run` (the .diag echoes and --strict gates a stage holds on every
run). Every instance is built every time (`tjs run` always rebuilds
everything, and a view its full-history gains: the owner's decisions of
2026-11-17, "i don't want to take any chance of stale data", PLAN step 12);
the ask records what each is built from, which the sweep reads (`_vouched`).
One kind of row may `reuse` a build its record says is fresh: one whose
output is data `fetched` from outside, not derived from the books (the
rates, refetched when stale or on the row's own `grounds`, vouched for as the
ask would, `mark_built`). `execute` refuses `reuse` for any other row, and
tests/test_stage_table.py's `test_only_a_fetched_row_reuses_its_record`
holds every call. It is the only `needs_rebuild` ask: the inputs a record
holds are the row's. A command that builds a view of its own vouches for
each build at once (`at_once`); a run's are vouched for when it completes. A
path the row declares that is a folder stops the command in one line
(TJ-4180).

Two rows may share an output (base and crypto_base: an account is one kind or
the other; gains_all and gains_all_split: an account the run blended with
others is split from their full history, `_gains_all_file`). What a run built
is kept by output (run_state's `stage_outputs`), so a sweep of the outputs no
row built (step 7) never takes one another row now builds. A row whose `when`
reads what another row's every_run found (the native books: the raw row's
`raw_mixed`) names that row in `after`; `execute` refuses it unless that row
ran first on the same context.

The references (`resolve`):

- File(path): one file, a template over the instance ({work} the work
  folder, {root} the project, {a} the account, {b} the broker group, {tag}
  the blend, {mh} the folder --incomplete-history names) or a fact of the
  run (`@ticker_map`: the map the stage is given); declared present or absent
  (an absent file records None), unless `keep` says otherwise.
- Out(row): the output of another row, or of a stamp the run writes before
  the stage asks (STAMPS: a work/ file holding what a stage takes from files
  it does not name whole, rewritten only when that changes). An Out of a
  narrower row is every one of its instances: each broker group's parse for
  an account, each .tt file's, each member account's book for a blend.
- Tree(select): a set of files the run selects, each its own input: a glob
  (`glob:` and a template, sorted), or one of the run's selections (TREES:
  a broker group's exports, the account's .tt files, the filed-year locks).

A ref's `when` is asked of each instance it expands to; `keep` is what
declares a file: ANY (present or absent), FILE (only a file), NONEMPTY (only
a file with bytes in it), NOT_DIR (anything but a folder). A folder declared
is refused, as an ask refuses one (freshness.FolderInputError).

A row with no steps bound is built at its own call site.
"""
import json
import os
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import (
    Any, Callable, Dict, FrozenSet, List, Mapping, NamedTuple, Optional,
    Sequence, Tuple, Union,
)

from taxjson.lib.pipeline import tt_json_path
from taxjson.lib.safe_write import write_changed
from taxjson.lib.project_layout import _GAINS_ALL_SUFFIX
from taxjson.pipeline.config import CORP_ACTION_BROKERS, RATE_READING_BROKERS
from taxjson.lib import project_layout as _PL
from taxjson.pipeline import run_state
from taxjson.lib import records as _R
from taxjson.pipeline.freshness import (
    FolderInputError, commit_records, mark_built, needs_rebuild, record_now,
    timeline_beside, unmark,
)
from taxjson.pipeline.inputs import (
    _account_sidecars, _country_has_corp_rules, _is_generic_group,
    _LOOKALIKE_RENAME_BROKERS, _lock_files,
)
from taxjson.pipeline.messages import _die_input
from taxjson.parsers.registry import SPECS as _PARSERS

# A folder each process notes the rows it executed in (a file per row and
# process, <row>.<pid>.log, holding the row's name): the test that the books
# reach every row (tests/test_freshness_edges).
REACHED_LOG_ENV = "TAXJSON_STAGE_REACHED_LOG"

# What a row is built per.
PROJECT, ACCOUNT, BROKER, TT, BLEND = "project", "account", "broker", "tt", "blend"
PER = (PROJECT, ACCOUNT, BROKER, TT, BLEND)
# What declares a file (a ref's `keep`).
ANY, FILE, NONEMPTY, NOT_DIR = "any", "file", "nonempty", "not-dir"


# ---------------------------------------------------------------- the instance

@dataclass
class StageCtx:
    """What a row's inputs resolve against: the run's facts when the stage
    asks. An account's stages share one (stage_account), filled in as they
    run: the effective map from the cross listings on, the .tt files from the
    crypto sends on, the elections manifest from the corporate actions on.
    `own`: what the steps bound to the rows read beyond these (their
    module's record of the account's run)."""
    work: Path
    settings: Any = None
    account: str = ""
    crypto: bool = False
    taxable: bool = True
    transfers: bool = False
    # broker group -> its exports, in the folder's order
    grouped: Mapping[str, Sequence[Path]] = field(default_factory=dict)
    # a generic export -> its mapping choice (inputs._mapping_choice)
    choices: Mapping[Path, Any] = field(default_factory=dict)
    tt_files: Sequence[Path] = ()
    ticker_map: Optional[Path] = None
    overrides: Optional[Path] = None        # securities.map
    manifest: Optional[Path] = None         # the elections manifest
    mh: Optional[Path] = None               # what --incomplete-history names
    dists: bool = False                     # [[distributions]] apply
    siblings: Sequence[str] = ()            # longer account names' prefixes
    # a blend's (or the sheltered merge's) accounts, and which are crypto
    members: Sequence[str] = ()
    crypto_members: FrozenSet[str] = frozenset()
    tag: str = ""
    sheltered: Optional[Path] = None        # the registered books a blend reads
    root_dir: Optional[Path] = None         # the project, when not work/'s parent
    # the rates: their source currencies, the window the inputs need and
    # the one the file was built from (stages.RATES_START_STAMP)
    currencies: Sequence[str] = ()
    window: Optional[str] = None
    built_from: Optional[str] = None
    # the native books pool mixed currencies (the raw row's every_run): their
    # gains and base are not built
    raw_mixed: bool = False
    own: Any = None
    # the rows `execute` ran on this context, in order (a row's `after`)
    ran: List[str] = field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.root_dir if self.root_dir is not None else self.work.parent


@dataclass(frozen=True)
class Inst:
    """One instance of a row: the row, the run's facts, and which broker
    group, .tt file or member account it is."""
    row: str
    ctx: StageCtx
    broker: Optional[str] = None
    tt: Optional[Path] = None
    member: Optional[str] = None

    @property
    def account(self) -> str:
        return self.member if self.member is not None else self.ctx.account

    @property
    def crypto(self) -> bool:
        if self.member is not None:
            return self.member in self.ctx.crypto_members
        return self.ctx.crypto

    def fields(self) -> Dict[str, str]:
        c = self.ctx
        return {"work": str(c.work), "root": str(c.root), "a": self.account,
                "b": self.broker or "", "tag": c.tag,
                "mh": str(c.mh) if c.mh is not None else ""}



# ---------------------------------------------------------------- references

Pred = Callable[[Inst], bool]
PathSpec = Union[str, Callable[[Inst], Optional[Path]]]


def always(_i: Inst) -> bool:
    return True


def never(_i: Inst) -> bool:
    return False


def both(a: Pred, b: Pred) -> Pred:
    if b is always:
        return a
    return lambda i: a(i) and b(i)


@dataclass(frozen=True)
class File:
    path: PathSpec
    when: Pred = always
    keep: str = ANY


@dataclass(frozen=True)
class Out:
    row: str
    when: Pred = always
    keep: str = ANY


@dataclass(frozen=True)
class Tree:
    select: str
    when: Pred = always
    keep: str = ANY


Ref = Union[File, Out, Tree]


class Ready(NamedTuple):
    """What a row's build and every_run are given beside the instance: the
    inputs its ask declared, and what its prepare returned."""
    inputs: List[Path]
    prep: Any


Step = Callable[[Inst, Ready], Any]


@dataclass(frozen=True)
class Stage:
    """A row of the table (the module docstring)."""
    name: str
    per: str
    out: PathSpec
    inputs: Tuple[Ref, ...]
    build: Optional[Step] = None
    when: Pred = always
    # the output is data fetched from outside the project (the rates), not
    # derived from the books: the one kind of row `execute` may `reuse`
    fetched: bool = False
    # a ground of the stage's own, asked when a reused record says fresh
    # (rates that end early)
    grounds: Pred = never
    every_run: Optional[Step] = None
    # a BROKER row's order over the account's groups (a sort key)
    order: Optional[Callable[[str], Any]] = None
    # the stamps the ask reads, written before it (returns the build's prep)
    prepare: Optional[Callable[[Inst], Any]] = None
    # the rows whose every_run `when` reads, run first on the same context
    after: Tuple[str, ...] = ()


def _path(spec: PathSpec, i: Inst) -> Optional[Path]:
    if callable(spec):
        return spec(i)
    if spec.startswith("@"):
        v = i.tt if spec == "@tt" else getattr(i.ctx, spec[1:])
        return Path(v) if v else None
    return Path(spec.format(**i.fields()))


def _kept(p: Path, keep: str) -> bool:
    if keep == FILE:
        return p.is_file()
    if keep == NONEMPTY:
        return p.is_file() and p.stat().st_size > 0
    if keep == NOT_DIR:
        return not p.is_dir()
    return True


def output(i: Inst) -> Path:
    """The output of instance `i` (a row's, or a stamp's)."""
    spec = TABLE[i.row].out if i.row in TABLE else STAMPS[i.row][1]
    p = _path(spec, i)
    assert p is not None, i.row
    return p


def instances(row: str, i: Inst) -> List[Inst]:
    """The instances of `row` that instance `i` reads: each broker group of
    its account for a BROKER row, each .tt file for a TT row, each member
    account for an ACCOUNT row read by a blend (one that is not a member's
    own), else the one `i` names."""
    st = TABLE.get(row)
    per = st.per if st is not None else STAMPS[row][0]
    c = i.ctx
    if per == BROKER and i.broker is None:
        brokers = list(c.grouped)
        if st is not None and st.order is not None:
            brokers.sort(key=st.order)
        return [Inst(row, c, broker=b) for b in brokers]
    if per == TT and i.tt is None:
        return [Inst(row, c, tt=t) for t in c.tt_files]
    if per == ACCOUNT and i.member is None and c.members:
        return [Inst(row, c, member=m) for m in c.members]
    return [Inst(row, c, broker=i.broker, tt=i.tt, member=i.member)]


def _tree(select: str, i: Inst) -> List[Path]:
    if select.startswith("glob:"):
        p = Path(select[len("glob:"):].format(**i.fields()))
        return sorted(p.parent.glob(p.name))
    return list(TREES[select](i))


def resolve(refs: Sequence[Ref], i: Inst) -> List[Path]:
    """The paths `refs` declare for instance `i`, in order."""
    out: List[Path] = []
    for r in refs:
        if isinstance(r, Out):
            paths = [output(j) for j in instances(r.row, i) if r.when(j)]
        elif isinstance(r, Tree):
            paths = _tree(r.select, i) if r.when(i) else []
        else:
            p = _path(r.path, i) if r.when(i) else None
            paths = [p] if p is not None else []
        out += [p for p in paths if _kept(p, r.keep)]
    return out


def member_books(i: Inst) -> List[Path]:
    """The books BOOK declares for `i`'s members, in the members' order:
    what a merge of them reads (taxjson-merge keeps its books' order).
    `resolve(BOOK, i)` holds the same paths, but each ref's in turn, the
    equity books before the crypto ones: a sheltered merge of both kinds
    would reorder (a blend is of one kind: the same list)."""
    return [p for m in i.ctx.members for p in resolve(BOOK, replace(i, member=m))]


# ---------------------------------------------------------------- the executor

def _reached(row: str) -> None:
    """Note that this process executed `row` (REACHED_LOG_ENV)."""
    folder = os.environ.get(REACHED_LOG_ENV)
    if folder:
        mark = Path(folder) / f"{row}.{os.getpid()}.log"
        if not mark.exists():
            from taxjson.lib.safe_write import write_atomic
            write_atomic(mark, row + "\n")


def _refuse_folder(e: FolderInputError, root: Path) -> None:
    """A path a row declares is a folder (freshness.FolderInputError): one
    `tjs <command>: error:` line naming it, exit 2, not a traceback, for every
    command that reaches the executor (TJ-4133, TJ-4180). Nothing was vouched
    for: a run's records are written when it completes, a view's as each
    build ends."""
    _die_input(f"{_PL.shown(e.path, root)} is a folder where a file was "
               f"expected — rename or remove it, then run again")


class Steps(NamedTuple):
    """What builds a row: its build, the stamps it writes before the ask
    (`prepare`, whose return is the build's `Ready.prep`), and its every-run
    hook."""
    build: Step
    prepare: Optional[Callable[[Inst], Any]] = None
    every_run: Optional[Step] = None


def bound(steps: Mapping[str, Steps]) -> Dict[str, Stage]:
    """The table with `steps` bound to their rows: the module whose code
    they are keeps it (this one cannot import that module)."""
    unknown = sorted(set(steps) - set(TABLE))
    if unknown:
        raise KeyError(f"no row of the stage table: {', '.join(unknown)}")
    return {name: (replace(st, build=steps[name].build, prepare=steps[name].prepare,
                           every_run=steps[name].every_run)
                   if name in steps else st)
            for name, st in TABLE.items()}


def execute(table: Mapping[str, Stage], ctx: StageCtx, *rows: str,
            reuse: bool = False, at_once: bool = False) -> List[Inst]:
    """Run `rows` of `table` (`bound`), in order, for `ctx` (the design's
    section 4): each instance `when` admits is prepared, asked over the
    inputs its row declares, built, and given its every_run. `reuse`, for a
    `fetched` row only (the rates; a derived row is refused, LookupError):
    built only when the record says stale or on its row's grounds; a build
    on a ground is vouched for before it runs, as a stale ask is, and one
    that returns False kept nothing to vouch for. Every other build is made
    every time: no derived figure comes from a record's word.
    `at_once`: each build is vouched for as it ends (`record_now`: a command
    that builds a view of its own, outside a run); else the run's
    `commit_records` vouches for it when the run completes. Each output run
    is kept in run_state's `stage_outputs`, with what names its instance
    (what work/.deps/run.json lists: `commit_run`). Returns the
    instances run, in order."""
    ran: List[Inst] = []
    for row in rows:
        st = table[row]
        if st.build is None:
            raise LookupError(f"the stage table's {row} row has no build bound")
        if reuse and not st.fetched:
            raise LookupError(f"the stage table's {row} row is derived: it is "
                              f"built every time, never reused on its record")
        before = [r for r in st.after if r not in ctx.ran]
        if before:
            raise LookupError(f"the stage table's {row} row reads what "
                              f"{', '.join(before)} found: run it first")
        for i in instances(row, Inst(row, ctx)):
            if not st.when(i):
                continue
            prep = st.prepare(i) if st.prepare is not None else None
            out = output(i)
            try:
                ready = Ready(resolve(st.inputs, i), prep)
                stale = needs_rebuild(out, *ready.inputs, force=not reuse)
            except FolderInputError as e:
                _refuse_folder(e, ctx.root)
                raise                       # not reached: _die_input exits
            built = stale
            if stale:
                st.build(i, ready)
            elif st.grounds(i):
                mark_built(out, *ready.inputs)
                built = st.build(i, ready) is not False
                if not built:
                    unmark(out)
            if built and at_once:
                record_now(out)
            run_state.current.stage_outputs[out] = (
                row, i.account or None, i.broker,
                i.tt.name if i.tt is not None else None, i.ctx.tag or None)
            if st.every_run is not None:
                st.every_run(i, ready)
            _reached(row)
            ran.append(i)
        ctx.ran.append(row)
    return ran


# ---------------------------------------------------------------- the last run

# The rows a full run asks after its sweep of what vanished (`sweep_vanished`
# before the cross-account reports): the project's reports and the radars.
LATE_ROWS = ("ccd", "leaps", "radar", "radar_combined", "crosslistings", "fees")
# The rows a view builds on demand (cli/common: no run lists them): a removed
# account's go with its books.
VIEW_ROWS = ("gains_all", "gains_all_split")
# What names an instance in work/.deps/run.json: its row, and the account,
# broker group, .tt file and blend tag it is for. The sweep takes the files
# from the table by these (`derived`), never a name the list holds.
_ID = ("row", "account", "broker", "tt", "tag")
# The export groups an account's BROKER rows run per: a parser's id, or a
# generic mapping's own (`generic-<name>`: pipeline/inputs).
_GROUPS = frozenset(s.id for s in _PARSERS)
# What the sweep never removes, whatever a listed name derives: the elections
# (pipeline/stages: a shared-inputs project still reads the legacy manifest in
# work/, and neither can be built again).
_NEVER_SWEPT = ("_manifest.json", "_pending_elections.json")
# Where the sweep may remove a file, relative to the project: work/,
# reports/ and the records' folders (lib/records).
_SWEPT_DIRS = (("work",), (_R.REPORTS_DIR,), ("work", _R.DEPS_DIR),
               ("work", _R.DEPS_DIR, _R.REPORTS_DIR))


def companions(row: str, out: Path) -> List[Path]:
    """The files a row's build writes beside its output, which go with it
    when its instance is gone: the stage's captured stderr (`.diag`), a
    gains run's traces and cost timeline, a radar's sidecar, a parse's
    transfer evidence and cash events, a holdings handoff's last copy (the
    diff it shows)."""
    out = Path(out)
    also = [out.with_name(out.name + ".diag")]
    if out.suffix == ".json":
        also += [timeline_beside(out), out.with_suffix(".traces")]
    if row in ("radar", "radar_combined"):
        also.append(out.with_suffix(".json"))
    if row == "parse":
        from taxjson.lib.cash_events import sidecar_path
        also += [out.with_name(out.stem + "_transfers.json"), sidecar_path(out)]
    if row in ("holdings", "crypto_holdings"):
        also.append(out.parent.parent / "work" / out.name.replace(
            "_holdings.toml", "_holdings_prev.toml"))
    return also


def _name_part(v: object) -> bool:
    """`v` (an account, a broker group, a .tt file, a tag) can be one part
    of a file name: text with no folder separator, not `.` or `..`."""
    return (isinstance(v, str) and v not in ("", ".", "..")
            and not any(c in v for c in "/\\\0"))


def _group(b: str) -> bool:
    """`b` an export group a BROKER row runs per (TJ-4273)."""
    return b in _GROUPS or (b.startswith("generic-") and _name_part(b[len("generic-"):]))


def derived(work: Path, ident: Mapping[str, Any]) -> Optional[Path]:
    """The output the table's row writes today for the account, broker
    group, .tt file or blend tag `ident` names (a run.json instance, or a
    stamp's): None for a row the table does not have, a name that is not one
    part of a file name, a broker that is no export group, one the row's
    kind needs and `ident` lacks, or an elections file (`_NEVER_SWEPT`)."""
    row = ident.get("row")
    if not isinstance(row, str) or (row not in TABLE and row not in STAMPS):
        return None
    v = {k: ident.get(k) for k in _ID[1:]}
    if any(x is not None and not _name_part(x) for x in v.values()):
        return None
    if v["broker"] is not None and not _group(v["broker"]):
        return None
    per = TABLE[row].per if row in TABLE else STAMPS[row][0]
    need = {ACCOUNT: ("account",), BROKER: ("account", "broker"),
            TT: ("account", "tt"), BLEND: ("tag",)}.get(per, ())
    if any(not v[k] for k in need):
        return None
    work = Path(os.path.abspath(work))
    ctx = StageCtx(work=work, account=v["account"] or "", tag=v["tag"] or "",
                   root_dir=work.parent)
    try:
        p = _path(TABLE[row].out if row in TABLE else STAMPS[row][1],
                  Inst(row, ctx, broker=v["broker"],
                       tt=Path(v["tt"]) if v["tt"] else None))
    except (AssertionError, KeyError, IndexError, TypeError, ValueError):
        return None
    if p is None or str(p).endswith(_NEVER_SWEPT):
        return None
    return Path(os.path.abspath(p))


def _sweepable(root: Path, p: Path) -> bool:
    """`p` a file the sweep may remove: in the project's work/ or reports/
    (or the records' folders), no link on the way from the project folder
    (each part's lstat: a folder link inside work/ leads out of it) but
    work/ itself, when it is a link to a folder in the project, the run's
    work/, which it writes through (`_linked_work`: TJ-5071), and a file
    or a link itself (the link alone goes)."""
    try:
        rel = Path(os.path.abspath(p)).relative_to(root)
    except ValueError:
        return False
    if rel.parts[:-1] not in _SWEPT_DIRS:
        return False
    try:
        cur = root
        for i, part in enumerate(rel.parts[:-1]):
            cur = cur / part
            if not stat.S_ISDIR(os.lstat(cur).st_mode) and not (
                    i == 0 and part == "work" and _linked_work(root)):
                return False
        mode = os.lstat(p).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) or stat.S_ISLNK(mode)


def _linked_work(root: Path) -> bool:
    """Whether the project's work/ is a link to a folder in the project
    (`.w`, say: every command refuses one leading out of it), resolved
    once: the sweeps treat that folder as work/."""
    work = Path(root) / "work"
    try:
        if not os.path.islink(work):
            return False
        real, top = Path(os.path.realpath(work)), Path(os.path.realpath(root))
        return real != top and real.is_relative_to(top) and real.is_dir()
    except (OSError, ValueError):
        return False


def _vouched(p: Path) -> Optional[bool]:
    """Whether taxjson wrote `p` as it is now (TJ-4274): True when its record
    (lib/records, format 2: written for an output a run built) holds the hash
    of its bytes, False when it holds another (edited since: a link's target
    is read, if a file), None when there is no such record or no hash in it
    (not taxjson's)."""
    rec = _R.read_record(p)
    if rec is None or not isinstance(rec.get("out"), str):
        return None
    try:
        if not stat.S_ISREG(os.stat(p).st_mode):
            return False
    except OSError:
        return False
    return rec["out"] == _R.sha256_file(p)


# Where the stale-parse cleanup moves what it cannot vouch for (work/kept/:
# no reader of work/<account>_*.json sees it there, and no sweep goes in).
KEPT_DIR = "kept"
# Why a file is moved there (`stale_parse_fates`), and what is left alone.
OTHER_BYTES = "its bytes are not the ones taxjson's record of it holds"
NO_RECORD = "taxjson has no record of writing it"
LEFT = "left"


def _has_records(work: Path) -> bool:
    """Whether work/ holds records (work/.deps/: an output's, or the last
    run's list, run.json)."""
    try:
        return any((Path(work) / _R.DEPS_DIR).glob("*.json"))
    except OSError:
        return False


def stale_parse_fates(work: Path, account: str,
                      stale: Sequence[Path]) -> Dict[Path, Optional[str]]:
    """What an account's stale-parse cleanup (pipeline/stages
    `_sweep_stale_parses`) does with each of `stale`, the
    work/<account>_<x>.json no export group, .tt file or corp file of the
    account names now (by absolute path). taxjson never deletes or reads a
    file it did not write (TJ-5071, TJ-4275): None, it goes, only when it
    can vouch for it; otherwise why it is moved aside (`move_aside`):
    OTHER_BYTES (its record holds other bytes: edited since, or written by
    a run that stopped before it recorded them) or NO_RECORD; LEFT for one
    the sweep may not touch (`_sweepable`: a folder, a path through a
    link other than work/ itself), which stays where it is.

    With records in work/ (`_has_records`), what goes is what its record
    vouches for (`_vouched`), and the _transfers.json beside a parse that
    goes (the parse's companion, which has no record of its own:
    `companions`; a file, not a link). With none at all (a work/ from
    before the records), a file, not a link, named as an export group's
    parse (<account>_<group>.json, and the _corp.json and _transfers.json
    beside it: `_group`)."""
    work = Path(os.path.abspath(work))
    records = _has_records(work)
    out: Dict[Path, Optional[str]] = {}
    for p in [Path(os.path.abspath(q)) for q in stale]:
        if not _sweepable(work.parent, p):
            out[p] = LEFT
        elif records:
            ok = _vouched(p)
            out[p] = None if ok is True else OTHER_BYTES if ok is False else NO_RECORD
        else:
            stem = p.name[len(account) + 1:-len(".json")]
            ours = not os.path.islink(p) and p.name.startswith(f"{account}_") \
                and p.name.endswith(".json") and (_group(stem) or any(
                    stem.endswith(s) and _group(stem[:-len(s)])
                    for s in ("_corp", "_transfers")))
            out[p] = None if ours else NO_RECORD
    for p, fate in list(out.items()):
        parse = p.with_name(p.name[:-len("_transfers.json")] + ".json")
        if records and fate == NO_RECORD and p.name.endswith("_transfers.json") \
                and not os.path.islink(p) and parse in out and out[parse] is None:
            out[p] = None
    return out


def move_aside(work: Path, p: Path) -> Path:
    """`p` (a file of work/, or a link: the link itself, still leading
    where it led) moved to work/kept/ (KEPT_DIR), with the .diag beside
    it, under its own name or, when that is taken, <stem>-<n><suffix>;
    returns where it went. OSError when work/kept/ is not a folder of its
    own (a link, a file) or the move fails."""
    kept = Path(os.path.abspath(work)) / KEPT_DIR
    if not os.path.lexists(kept):
        kept.mkdir(mode=0o700)
    if not stat.S_ISDIR(os.lstat(kept).st_mode):
        raise OSError(f"{KEPT_DIR}/ in work/ is not a folder")
    dest, n = kept / p.name, 1
    while os.path.lexists(dest) or os.path.lexists(f"{dest}.diag"):
        n += 1
        dest = kept / f"{p.stem}-{n}{p.suffix}"
    to = os.readlink(p) if os.path.islink(p) else None
    if to is not None and not os.path.isabs(to):
        # Read from the folder the link is in, work/ resolved (`_linked_work`).
        os.symlink(os.path.relpath(os.path.join(os.path.realpath(p.parent), to),
                                   os.path.realpath(kept)), dest)
        os.unlink(p)
    else:
        os.rename(p, dest)
    diag = p.with_name(p.name + ".diag")
    if _sweepable(Path(os.path.abspath(work)).parent, diag):
        os.rename(diag, f"{dest}.diag")
    return dest


def _run_file(work: Path) -> Path:
    return Path(work) / _R.DEPS_DIR / _R.RUN_FILE


def last_run(work: Path) -> Dict[str, Dict[str, Optional[str]]]:
    """The row instances work/.deps/run.json lists, by output (relative to
    the project), each its `_ID` names. Empty when there is none or it is
    an older format's; an entry of another shape is left out."""
    try:
        doc = json.loads(_run_file(work).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("format") != _R.RECORD_FORMAT:
        return {}
    got = doc.get("instances")
    if not isinstance(got, dict):
        return {}
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for k, v in got.items():
        if not _ours(k) or not isinstance(v, dict):
            continue
        ident = {f: v.get(f) for f in _ID}
        if isinstance(ident["row"], str) and all(
                x is None or isinstance(x, str) for x in ident.values()):
            out[k] = ident
    return out


def _ours(rel: object) -> bool:
    """Whether `rel` (read from run.json) names a file of the run's own: in
    work/ or reports/, relative, with no `..` (a damaged list never names an
    input or taxjson.toml)."""
    if not isinstance(rel, str):
        return False
    parts = rel.split("/")
    return parts[0] in ("work", "reports") and len(parts) > 1 and all(
        p not in ("", ".", "..") for p in parts)


def _ran_now() -> Dict[Path, Dict[str, Optional[str]]]:
    """The row instances this run executed, by output, each its `_ID`."""
    return {Path(os.path.abspath(p)): dict(zip(_ID, ident))
            for p, ident in run_state.current.stage_outputs.items()}


def _this_run(root: Path) -> Dict[str, Dict[str, Optional[str]]]:
    """The row instances this run executed, as run.json lists them."""
    return {rel: ident for p, ident in _ran_now().items()
            for rel in [_inside(p, root)] if rel is not None}


def _inside(p: Path, root: Path) -> Optional[str]:
    """`p` relative to the project, or None when it is not in it."""
    try:
        return Path(os.path.abspath(p)).relative_to(root).as_posix()
    except ValueError:
        return None


def recorded_input(work: Path, path: Path) -> bool:
    """Whether a report's record (work/.deps/reports/) holds `path` as there
    when the report was built: what the run reads for a file the stages
    declared and that is gone now (ticker.map removed: TJ-1807)."""
    p = Path(os.path.abspath(path))
    name = _inside(p, Path(os.path.abspath(work)).parent) or str(p)
    try:
        recs = sorted((Path(work) / _R.DEPS_DIR / _R.REPORTS_DIR).glob("*.json"))
    except OSError:
        return False
    for rp in recs:
        try:
            rec = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("format") != _R.RECORD_FORMAT:
            continue
        got = rec.get("inputs")
        if isinstance(got, dict) and got.get(name) is not None:
            return True
    return False


def swept_accounts(work: Path, accounts: Sequence[str]) -> List[str]:
    """The `accounts` (ones taxjson.toml no longer names) whose books the
    last run's instances built: a full run's sweep takes them, before any
    report reads the books (`sweep_vanished`)."""
    return sorted({str(v.get("account")) for v in last_run(work).values()}
                  & set(accounts))


def account_files(work: Path, account: str, brokers: Sequence[str] = ()) -> List[Path]:
    """An account's own files beyond its rows' outputs, from the table: its
    stamps (STAMPS', per account and per broker group of `brokers`), its
    detection notes, the blend's mirror of what it said, its transfer
    costs and own moves, and what the views build for it on demand
    (VIEW_ROWS) with the files beside them."""
    work = Path(os.path.abspath(work))
    if not _name_part(account):
        return []
    files = [p for s, (per, _spec) in STAMPS.items() if per == ACCOUNT
             for p in [derived(work, {"row": s, "account": account})] if p is not None]
    files += [p for s, (per, _spec) in STAMPS.items() if per == BROKER
              for b in brokers
              for p in [derived(work, {"row": s, "account": account, "broker": b})]
              if p is not None]
    files += [Path(spec.format(work=work, a=account)) for spec in (
        "{work}/{a}_detect.diag", "{work}/{a}_blend.diag",
        "{work}/{a}_cryptoblend.diag", TRANSFER_COSTS, OWN_MOVES)]
    for row in VIEW_ROWS:
        out = derived(work, {"row": row, "account": account})
        if out is not None:
            files += [out, *companions(row, out), _R.record_path(out)]
    return files


class Swept(NamedTuple):
    """What `sweep_vanished` did: the files it removed, and the outputs it
    kept, each with why (`edited` since taxjson wrote it, or not taxjson's:
    TJ-4274), relative to the project."""
    gone: List[str]
    kept: List[Tuple[str, str]]


EDITED, NOT_OURS = "edited", "not taxjson's"


def sweep_vanished(work: Path, *, spare: Sequence[str] = (),
                   configured: Optional[Sequence[str]] = None) -> Swept:
    """A full run, once the instances of every row but `spare`'s ran: what
    the last run's instances built and this run's did not goes, so an
    account, a broker export or a kind gone from the project leaves no
    book a view or a report could read (backlog TJ-4265). Each output is
    the table's (`derived` from the instance run.json lists, which must
    name that output too), only one `_sweepable` (backlog TJ-4269), and
    only one its record vouches for as taxjson wrote it (`_vouched`: one
    edited since, or not taxjson's, is kept and said: TJ-4274). The files
    beside a removed output go with it, and its record: the row's
    `companions`, each one with a record of its own only when that record
    vouches for it. A spared row's instance goes too when its account ran
    nothing now (a removed account's radar, with its books). An account
    `configured` no longer names, that ran nothing and whose outputs went,
    leaves none of its own files either (`account_files`: TJ-4264). What a
    current instance writes or keeps beside its output stays, and so do the
    stamps of an account that ran."""
    work = Path(os.path.abspath(work))
    root = work.parent
    now = _ran_now()
    ran = {v["account"] for v in now.values() if v["account"]}
    brokers_now: Dict[str, List[str]] = {}
    for v in now.values():
        if v["account"] and v["broker"]:
            brokers_now.setdefault(v["account"], []).append(v["broker"])
    keep = {Path(os.path.abspath(q)) for p, v in now.items()
            for q in [p, *companions(v["row"] or "", p)]}
    keep.update(Path(os.path.abspath(q)) for a in ran
                for q in account_files(work, a, brokers_now.get(a, ())))
    swept = Swept([], [])

    def remove(p: Path) -> bool:
        try:
            p.unlink()
        except OSError:          # (one that cannot go is left, and not said)
            return False
        rel = _inside(p, root)
        if rel is not None and not rel.startswith(f"work/{_R.DEPS_DIR}/") \
                and rel not in swept.gone:
            swept.gone.append(rel)
        return True

    def remove_beside(files: Sequence[Path]) -> None:
        for q in files:
            q = Path(os.path.abspath(q))
            if q in keep or not _sweepable(root, q):
                continue
            if _R.read_record(q) is not None and _vouched(q) is not True:
                continue
            remove(q)

    listed = last_run(work)
    owners: Dict[str, List[str]] = {}     # an account whose outputs went: their brokers
    for rel, ident in sorted(listed.items()):
        account = ident["account"]
        if ident["row"] in spare and (account is None or account in ran):
            continue
        out = derived(work, ident)
        if out is None or _inside(out, root) != rel or out in keep:
            continue
        if not os.path.lexists(out):
            # Gone already (a stage removed it): its record vouches for nothing.
            remove_beside([_R.record_path(out)])
            continue
        if not _sweepable(root, out):
            continue
        why = _vouched(out)
        if why is not True:
            swept.kept.append((rel, EDITED if why is False else NOT_OURS))
            continue
        if not remove(out):
            continue
        remove_beside([*companions(ident["row"] or "", out), _R.record_path(out)])
        if account:
            owners.setdefault(account, []).extend([ident["broker"]] if ident["broker"] else [])
    if configured is not None:
        gone_accounts = {v["account"] for v in listed.values()
                         if v["account"]} - ran - set(configured)
        for a in sorted(gone_accounts & set(owners)):
            remove_beside(account_files(work, a, owners[a]))
    return swept


# What an older taxjson kept beside the records, gone with the first run that
# writes run.json: the wash passes' own record (in the records now: TJ-1804),
# the markers and `invalidate`'s bumps (the inputs are declared: TJ-1807).
_LEGACY = (".wash_inputs.json", ".missing_history_applied", ".phantoms_applied",
           ".project_maps_applied")


def commit_run(work: Path, *, full: bool) -> List[Path]:
    """A run completed: its records vouched for (freshness.commit_records)
    and work/.deps/run.json written, the row instances it executed: a full
    run's in place of the last run's (what vanished since swept first:
    `sweep_vanished`), a partial run's added to them. Returns the files
    that could not be written (the next run writes them again)."""
    work = Path(os.path.abspath(work))
    unwritten = commit_records()
    last = last_run(work)
    if full:
        sweep_vanished(work)
    if not last:
        deps = work / _R.DEPS_DIR
        try:
            bumps = sorted(deps.glob("*.bump")) if not deps.is_symlink() else []
        except OSError:
            bumps = []
        for p in [*(work / name for name in _LEGACY), *bumps]:
            if _sweepable(work.parent, p):
                p.unlink()
    now = _this_run(work.parent)
    listed = now if full else {**last, **now}
    if not listed and not last:
        return unwritten
    try:
        _run_file(work).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_changed(_run_file(work), json.dumps(
            {"format": _R.RECORD_FORMAT, "instances": listed}, indent=1,
            sort_keys=True) + "\n")
    except OSError:
        unwritten.append(_run_file(work))
    return unwritten


# ---------------------------------------------------------------- predicates

def _equity(i: Inst) -> bool:
    return not i.crypto


def _crypto(i: Inst) -> bool:
    return i.crypto


def _questrade(i: Inst) -> bool:
    return i.broker == "questrade"


def _qt(i: Inst) -> bool:
    return i.broker == "questrade" and not i.crypto


def _ib(i: Inst) -> bool:
    return i.broker == "ib" and not i.crypto


def _lookalike(i: Inst) -> bool:
    return i.broker in _LOOKALIKE_RENAME_BROKERS and not i.crypto


def _suffix(i: Inst) -> bool:
    from taxjson.lib.listing_suffix import CURRENCY_SUFFIX_BROKERS
    return i.broker in CURRENCY_SUFFIX_BROKERS and not i.crypto


def _generic(i: Inst) -> bool:
    return _is_generic_group(i.broker or "")


def _reads_rates(i: Inst) -> bool:
    return i.broker in RATE_READING_BROKERS


def _corp(i: Inst) -> bool:
    return (not i.crypto and i.broker in CORP_ACTION_BROKERS
            and _country_has_corp_rules(i.ctx.settings.country))


def _has_map(i: Inst) -> bool:
    return bool(i.ctx.ticker_map)


def _no_map(i: Inst) -> bool:
    return not i.ctx.ticker_map


def _crypto_map(i: Inst) -> bool:
    return i.crypto and bool(i.ctx.ticker_map)


def _arrivals(i: Inst) -> bool:
    # stage_transfer_arrivals: a taxable equity account's arrivals at a
    # stated book value (present only when there are some)
    return i.ctx.taxable and not i.crypto and not i.ctx.transfers


def _own_moves(i: Inst) -> bool:
    return i.ctx.taxable and not i.ctx.transfers


def _has_mh(i: Inst) -> bool:
    return i.ctx.mh is not None


def _native(i: Inst) -> bool:
    return not i.crypto and not i.ctx.raw_mixed


def washed(i: Inst) -> bool:
    """This run's pass across accounts wrote the account's wash file (a
    blend's split, or its own pass): its report model is wash-adjusted."""
    return i.account in run_state.current.wash_current


def _not_washed(i: Inst) -> bool:
    return not washed(i)


def _diags(i: Inst, keep: Callable[[str], bool]) -> List[Path]:
    """The account's persisted stage diagnostics a banner folds
    (diagnostics.collect_diagnostics: every `<account>_*.diag`)."""
    return [p for p in sorted(i.ctx.work.glob(f"{i.account}_*.diag")) if keep(p.name)]


def _sum_diags(i: Inst) -> List[Path]:
    """The pre-blend summary's: the passes across accounts' sidecars left out."""
    from taxjson.pipeline.diagnostics import _POST_PASS_DIAG_SUFFIXES
    return _diags(i, lambda n: not n.endswith(_POST_PASS_DIAG_SUFFIXES))


def _wash_sum_diags(i: Inst) -> List[Path]:
    """The wash twin's: a blended account's without its own gains run's."""
    blended = bool(getattr(i.ctx.own, "blended", False))
    return _diags(i, lambda n: not (blended and n == f"{i.account}_gains.json.diag"))


def _taxable(i: Inst) -> bool:
    return i.ctx.taxable


def _venue_books(i: Inst) -> List[Path]:
    """A crypto account's per-venue books its holdings read (`--venue-book`)."""
    return sorted(i.ctx.work.glob(f"{i.account}_*_transfers.json"))


def _cross_gains(i: Inst) -> List[Path]:
    """Every account's gains the cross-account reports read (a taxable
    account's wash file when its pass wrote one)."""
    return list(i.ctx.own.gains)


def _radar_bases(i: Inst) -> List[Path]:
    """The taxable books the radar and the cross-listing lint read."""
    return list(i.ctx.own.bases)


def _radar_gains(i: Inst) -> List[Path]:
    """The gains the radar's engine pools: each taxable book's, as the
    queries resolve them (the wash file when there is one)."""
    from taxjson.lib.report_model import resolve_gains_files
    out: List[Path] = []
    for b in i.ctx.own.bases:
        b = Path(b)
        if b.name.endswith("_base.json"):
            g = resolve_gains_files(b.parent, b.name[:-len("_base.json")],
                                    warn_stale=False).get(b.name[:-len("_base.json")])
            if g is not None:
                out.append(g)
    return out


def _fees_books(i: Inst) -> List[Path]:
    """The books the fees report reads, as its prepare found them
    (stages._fees_prepare): the broker-tagged ones, else every work/*.json."""
    return list(i.ctx.own.books)


def radar_json(i: Inst) -> Path:
    """The radar's structured sidecar beside its .rpt (harvest --radar)."""
    return output(i).with_suffix(".json")


def wash_traces(i: Inst) -> Path:
    """The full traces a wash pass writes beside its file (`tjs wash-sales`)."""
    return i.ctx.work / f"{i.account}_gains_wash.traces"


def _parse_order(broker: str) -> Tuple[bool, bool]:
    """Questrade last (its internal codes are read from the other exports),
    and a broker that names a listing from the row currency alone after
    the ones that name it (lib/listing_suffix)."""
    from taxjson.lib.listing_suffix import CURRENCY_SUFFIX_BROKERS
    return (broker in CURRENCY_SUFFIX_BROKERS, broker == "questrade")


# ---------------------------------------------------------------- the files

def _codes(i: Inst) -> Path:
    from taxjson.lib.symbol_codes import SUFFIX
    return i.ctx.work / f"{i.account}{SUFFIX}"


def _listing_suffix(i: Inst) -> Path:
    from taxjson.lib.listing_suffix import state_path
    return state_path(i.ctx.work, i.account, i.broker or "")


def _markets_map(_i: Inst) -> Optional[Path]:
    # The ticker.map whose MULT lines size an option line's total check
    # (lib/markets; backlog TJ-0864).
    from taxjson.lib.markets import ticker_map_path
    p = ticker_map_path()
    return Path(p) if p else None


def _loss_state(i: Inst) -> Path:
    from taxjson.lib.loss_overrides import state_path
    return state_path(i.ctx.work)


def _tt_out(i: Inst) -> Path:
    assert i.tt is not None
    return tt_json_path(i.ctx.work, i.account, i.tt.name)


# The stamps a stage reads, by what writes them (pipeline/stages): each a
# work/ file rewritten only when what it holds changes, as (per, path).
STAMPS: Dict[str, Tuple[str, PathSpec]] = {
    # _write_source_stamps: the account's inputs, membership and content
    "sources": (ACCOUNT, "{work}/{a}_sources.list"),
    "fingerprint": (ACCOUNT, "{work}/{a}_inputs.fingerprint"),
    # stage_symbol_codes: the Questrade codes the other exports resolve
    "codes": (ACCOUNT, _codes),
    # stage_ib_project: the project's other IB statements
    "ib_project": (ACCOUNT, "{work}/{a}_ib_project.state"),
    # _ib_tt_state: the .tt lines the IB parse reads (TJ-4129)
    "ib_tt": (ACCOUNT, "{work}/{a}_ib_tt.state"),
    # stage_declared_renames: the .tt RENAME lines
    "renames": (PROJECT, "{work}/declared_renames.list"),
    # stage_listing_suffix: the listings read from the evidence
    "suffix": (BROKER, _listing_suffix),
    # stage_account: the map's CRYPTO lines fill-crypto reads
    "crypto_map": (ACCOUNT, "{work}/{a}_crypto_ticker_map.state"),
    # _missing_history_deps: the OPENING cost=unknown lines (TJ-4128)
    "missing_history": (PROJECT, "{mh}/work/missing_history.state"),
    # stages._write_today: the day the wash radar counts its windows from
    "today": (PROJECT, "{work}/radar_today.state"),
}

TRANSFER_COSTS = "{work}/{a}_transfer_costs.json"     # stages.TRANSFER_COSTS_SUFFIX
OWN_MOVES = "{work}/{a}_own_moves.json"               # stages.OWN_MOVES_SUFFIX


def _ib_statements(i: Inst) -> List[Path]:
    """The project's other IB statements the account's IB project state
    lists (stage_ib_project writes it just before the parse asks)."""
    try:
        doc = json.loads(output(Inst("ib_project", i.ctx, broker=i.broker))
                         .read_text(encoding="utf-8"))
        accounts = doc.get("accounts") or {}
    except (OSError, ValueError, AttributeError):
        return []
    return [Path(p) for label in sorted(accounts) for p in accounts[label]]


def _mappings(i: Inst) -> List[Path]:
    return [p for c in TREES["csvs"](i) for p in i.ctx.choices[c].deps]


def _mh_maps(i: Inst) -> List[Path]:
    from taxjson.lib.missing_history import missing_history_maps
    return missing_history_maps(i.ctx.mh) if i.ctx.mh is not None else []


# The run's selections a Tree names.
TREES: Dict[str, Callable[[Inst], Sequence[Path]]] = {
    "csvs": lambda i: i.ctx.grouped[i.broker or ""],
    "mappings": _mappings,
    "tt_files": lambda i: i.ctx.tt_files,
    "ib_statements": _ib_statements,
    "sidecars": lambda i: _account_sidecars(i.ctx.work, i.account, i.ctx.siblings),
    "locks": lambda i: _lock_files(i.ctx.root, i.ctx.settings),
    "mh_maps": _mh_maps,
    "sum_diags": _sum_diags,
    "wash_sum_diags": _wash_sum_diags,
    "venue_books": _venue_books,
    "cross_gains": _cross_gains,
    "radar_bases": _radar_bases,
    "radar_gains": _radar_gains,
    "fees_books": _fees_books,
}


def _mh(when: Pred = always) -> Tuple[Ref, ...]:
    """What a stage given --incomplete-history declares: the lines' stamp
    and the maps they are read through (`_missing_history_deps`)."""
    w = both(_has_mh, when)
    return (Out("missing_history", when=w), Tree("mh_maps", when=w))


# What a taxable account's holdings handoff reads after the passes: their
# figures for units moved in from another of your accounts.
MOVED_COST: Tuple[Ref, ...] = (File("{work}/{a}_gains_wash.json", when=_taxable),)


# What the wash radar reads: the taxable books and the gains it pools, the
# registered books, the map, the losses you allowed, the missing history and
# the day it counts from.
RADAR_READS: Tuple[Ref, ...] = (
    Tree("radar_bases"), Tree("radar_gains"), File("@sheltered"),
    File("@ticker_map"), File(_loss_state), Out("today")) + _mh()


# The account's books' sources: its .tt files', its parses', its corporate
# actions', its arrivals at a stated book value and its moves between your
# own accounts.
SOURCES: Tuple[Ref, ...] = (
    Out("tt"), Out("parse"), Out("corp", when=_corp),
    File(TRANSFER_COSTS, when=_arrivals, keep=FILE),
    File(OWN_MOVES, when=_own_moves, keep=FILE),
)
# An account's base book: the equity merge's, or the crypto chain's.
BOOK: Tuple[Ref, ...] = (Out("base", when=_equity), Out("crypto_base", when=_crypto))
# What a gains run reads beside its book: the ALLOWLOSS state, the filed-year
# locks, the missing history. One list for the per-account run, the blended
# one and the full-history ones, so a new engine input is declared in each
# (TJ-1821).
GAINS_READS: Tuple[Ref, ...] = (File(_loss_state), Tree("locks")) + _mh()


def _rates_grounds(i: Inst) -> bool:
    """The rates' own grounds: a source currency's rates end before the last
    few days (a refresh that failed for one currency, S046-06), or the file
    was built from a later start than the window the inputs need
    (lib/rates_window)."""
    from taxjson.pipeline.inputs import _rates_coverage_stale
    c = i.ctx
    return (_rates_coverage_stale(output(i), currencies=list(c.currencies))
            or (c.window is not None and c.built_from is not None
                and c.built_from > c.window))


ROWS: Tuple[Stage, ...] = (
    # stages.stage_currency_rates: its coverage and window are grounds
    Stage("rates", PROJECT, "{work}/to_base.csv", (), fetched=True,
          grounds=_rates_grounds),
    # stage_account, 1: the broker parse per group. The map: a parser's
    # identity hint the map now answers is dropped (taxjson-brokerage
    # --ticker-map, A2-1056). A crypto parse reads no .tt and no other line
    # of the sources list (its settings are taxjson.toml's): the list
    # changes again after the sends stage (TJ-4131). A mapping (or layout)
    # edit rebuilds the parse like a CSV edit (files only: a folder is no
    # mapping, TJ-4133).
    Stage("parse", BROKER, "{work}/{a}_{b}.json", (
        Tree("csvs"), Out("sources", when=_equity), File("@overrides"),
        File("@ticker_map"), Out("codes", when=_qt),
        Out("ib_project", when=_ib), Tree("ib_statements", when=_ib),
        Out("ib_tt", when=_ib), Out("renames", when=_lookalike),
        Out("suffix", when=_suffix), Tree("mappings", when=_generic, keep=FILE),
        Out("rates", when=_reads_rates, keep=NONEMPTY)),
        order=_parse_order),
    # 2: corporate actions per equity group. The sources list: deleting a
    # CSV must dirty this stage too, or its rows from the deleted file live
    # on (2026-09 audit). The rates: a cross-currency exchange's legs are
    # valued at the event-date rate. The map: a temporary code it now
    # renames is no longer warned about (S072-03). The Questrade codes: a
    # chain under an internal code books under the listing they resolve it
    # to (GitHub issue #4). The .tt files: a spin-off's fraction sold there
    # by hand books its cash in lieu (workflow audit 2026-10-22 #14).
    Stage("corp", BROKER, "{work}/{a}_{b}_corp.json", (
        Tree("csvs"), File("@manifest", keep=NOT_DIR), Out("sources"),
        Out("rates"), File("@ticker_map"), Out("codes", when=_questrade, keep=FILE),
        Tree("tt_files")), when=_corp),
    # 3: each .tt file
    Stage("tt", TT, _tt_out, (File("@tt"), Out("sources"), File(_markets_map))),
    # 4: the crypto chain. The sources list: deleting a whole broker group
    # (the only kr_*.csv) removes its parse from the sources. A record sees
    # an input gone too now, so no edit tells the list apart; it stays as the
    # merge's ask named it, its record checked (tests/test_record_edges,
    # TJ-4184).
    Stage("crypto_merged", ACCOUNT, "{work}/{a}_merged.json",
          SOURCES + (Out("sources"), Out("fingerprint")), when=_crypto),
    Stage("crypto_sorted", ACCOUNT, "{work}/{a}_sorted.json",
          (Out("crypto_merged"),), when=_crypto),
    Stage("crypto_mapped", ACCOUNT, "{work}/{a}_mapped.json",
          (Out("crypto_sorted"), File("@ticker_map")) + _mh(), when=_crypto_map),
    Stage("crypto_filled", ACCOUNT, "{work}/{a}_filled.json",
          (Out("crypto_mapped", when=_has_map), Out("crypto_sorted", when=_no_map),
           Out("crypto_map")), when=_crypto),
    Stage("crypto_base", ACCOUNT, "{work}/{a}_base.json",
          (Out("crypto_filled"), Out("rates")), when=_crypto),
    # 4: the equity merge (and [[distributions]]): crypto_base's output, for
    # an account that is not crypto. The fingerprint, and the
    # arrivals file declared present or absent whatever the account: every
    # change they carry moves a parse, a .tt, the corporate actions, the map
    # or taxjson.toml too (the review of step 12's table, 65 edits). They
    # stay as the merge's ask named them (FUZZ #J, R1-253), their record
    # checked (tests/test_record_edges, TJ-4184).
    Stage("base", ACCOUNT, "{work}/{a}_base.json",
          SOURCES + (Out("rates"), Out("sources"), Out("fingerprint"),
                     File(TRANSFER_COSTS), File("@ticker_map")) + _mh(),
          when=_equity),
    # 5: gains
    Stage("gains", ACCOUNT, "{work}/{a}_gains.json", BOOK + GAINS_READS),
    # 5b: the native books. The sources list: deleting a .tt from a
    # .tt-only account kept its positions under --fast (S037-22). The
    # transfer evidence and the rates: the journals between listings are
    # written into this merge (_raw_book_journals).
    Stage("raw", ACCOUNT, "{work}/{a}_raw.json",
          SOURCES + (Out("sources"), File("@ticker_map"), Tree("sidecars"),
                     Out("rates", keep=FILE)) + _mh(), when=_equity),
    # Not built when the raw row's every_run finds the native books pool
    # mixed currencies (`raw_mixed`): after it.
    Stage("raw_gains", ACCOUNT, "{work}/{a}_raw_gains.json",
          (Out("raw"),) + _mh(), when=_native, after=("raw",)),
    Stage("raw_base", ACCOUNT, "{work}/{a}_raw_base.json",
          (Out("raw"), Out("rates")), when=_native, after=("raw",)),
    Stage("raw_base_gains", ACCOUNT, "{work}/{a}_raw_base_gains.json",
          (Out("raw_base"),) + _mh(), when=_native, after=("raw",)),
    # run._merge_sheltered: the registered books merged (bound in run._RUN_STAGES)
    Stage("sheltered", PROJECT, "{work}/sheltered_base.json", BOOK),
    # stages.stage_blended_wash_pass: the blend's merge, its gains and each
    # member's split of them. The split's books beside the blend's add no
    # edit (a member's book moves the blend's merge), as the old asks named
    # them; kept (tests/test_record_edges checks the record).
    Stage("blend_base", BLEND, "{work}/.{tag}_base.json", BOOK),
    Stage("blend_gains", BLEND, "{work}/.{tag}_gains_wash.json",
          (Out("blend_base"),) + GAINS_READS + (File("@sheltered"),)),
    Stage("split", ACCOUNT, "{work}/{a}_gains_wash.json",
          (Out("blend_gains"), Out("blend_base")) + BOOK),
    # stages.stage_wash_pass: a crypto account's own pass with the
    # sheltered books (a Canadian project's one crypto account). It shares
    # the split's output, never for one account: an account is blended or
    # passed alone.
    Stage("wash", ACCOUNT, "{work}/{a}_gains_wash.json",
          BOOK + GAINS_READS + (File("@sheltered"),)),
    # The renders (TJ-1828), each built every run, its step line said.
    # stage_account: a crypto account's validation, its holdings
    # handoff (an equity account's from the native books, a crypto
    # account's from its own: the two share the output) and its summary,
    # with the diagnostics its banner folds.
    Stage("crypto_validate", ACCOUNT, "{work}/{a}_validate.diag", BOOK,
          when=_crypto),
    # The holdings rows are built again once the passes across accounts
    # ran (stages.stage_moved_holdings), a taxable account's wash file
    # then what they wrote: units moved in from another of your accounts
    # take their cost from it (TJ-0598).
    Stage("holdings", ACCOUNT, "{root}/reports/{a}_holdings.toml",
          (Out("raw"), Out("raw_gains"), Out("raw_base_gains"), File("@ticker_map"))
          + MOVED_COST, when=_native, after=("raw",)),
    Stage("crypto_holdings", ACCOUNT, "{root}/reports/{a}_holdings.toml",
          (Out("gains"), Out("parse"), Tree("venue_books")) + BOOK + MOVED_COST,
          when=_crypto),
    Stage("sum", ACCOUNT, "{root}/reports/{a}.sum",
          (Out("gains"),) + BOOK + (Tree("sum_diags"),)),
    # stages._render_wash_outputs: the wash twin, after its pass (split's
    # or wash's every_run); stages.stage_report: the report model once the
    # passes ran, on the basis this run's pass gave it (`washed`).
    Stage("wash_sum", ACCOUNT, "{root}/reports/{a}_wash.sum",
          (File("{work}/{a}_gains_wash.json"),) + BOOK + (Tree("wash_sum_diags"),)),
    Stage("report", ACCOUNT, "{work}/{a}_report.json",
          (Out("gains", when=_not_washed), File("{work}/{a}_gains_wash.json", when=washed))
          + BOOK),
    # stages.stage_cross_reports: the reports across accounts. The radar
    # pools every taxable book's gains and counts from today (its sidecar
    # JSON beside it); the COMBINED pair is every row.
    Stage("ccd", PROJECT, "{root}/reports/ccd.rpt", (Tree("cross_gains"),)),
    Stage("leaps", PROJECT, "{root}/reports/leaps.rpt", (Tree("cross_gains"),)),
    Stage("radar", ACCOUNT, "{root}/reports/wash_radar_{a}.rpt", RADAR_READS),
    Stage("radar_combined", PROJECT, "{root}/reports/wash_radar_COMBINED.rpt",
          RADAR_READS),
    Stage("crosslistings", PROJECT, "{root}/reports/crosslistings.rpt",
          (Tree("radar_bases"), File("@sheltered"), File("@ticker_map"))),
    # stages.stage_fees: every parsed book's fees, at the rates
    Stage("fees", PROJECT, "{root}/reports/fees.rpt",
          (Tree("fees_books"), Out("rates"), File("@ticker_map"))),
    # cli/common.py: the full-history gains, built every time a view reads
    # them (once per command). gains_all and gains_all_split share an
    # output: an account the run blended with others is split from their
    # full history, any other is its own (`_gains_all_file`). Bound in
    # cli/common._ON_DEMAND; vouched for as each is built (`at_once`: a view
    # command completes no run), for the sweep's `_vouched`.
    Stage("gains_all", ACCOUNT, "{work}/{a}" + _GAINS_ALL_SUFFIX,
          BOOK + (File("{root}/taxjson.toml"),) + GAINS_READS),
    Stage("gains_all_blend", BLEND, "{work}/.{tag}" + _GAINS_ALL_SUFFIX,
          BOOK + (File("{root}/taxjson.toml"),) + GAINS_READS),
    Stage("gains_all_split", ACCOUNT, "{work}/{a}" + _GAINS_ALL_SUFFIX,
          (Out("gains_all_blend"),) + BOOK),
)
TABLE: Dict[str, Stage] = {st.name: st for st in ROWS}

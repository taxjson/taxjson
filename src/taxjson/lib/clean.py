"""`tjs clean`: what `tjs run` generated in a project, removed, so the next
run builds every file again from the inputs (cli/books `cmd_clean`).

Only what it recognises as taxjson's goes (`plan`), each by its full name
(`Known`), never by how a name begins; anything else stays and is named,
never called run-written:
- work/: the records' folder (.deps/); a file (never a folder) a record
  there or the last run's list (.deps/run.json) names, and what a stage
  writes beside it; the fixed names the writers use (`WORK_FILES`,
  `LEGACY_WORK`); the names they give an account's files (`ACCOUNT_WORK`,
  per export group `GROUP_WORK`, per .tt file `TT_WORK`) and a blend's
  (`BLEND_WORK`), for the accounts taxjson.toml names and the ones the
  records name (an account since removed: its books are the stale result),
  the parsers' export groups (`PARSE_GROUPS`) and blends (`BLEND_TAGS`) and
  the groups, .tt files and blends the last run's list names; a safe_write
  temp of one of these. Kept, though taxjson's (`kept_why`): the lock
  files, the slip-key salt, `tjs watch`'s baseline, the fetched prices, the
  generated crypto-sends `.tt`, an older layout's elections and the
  snapshots `tjs compare --save` keeps in work/compare/.
- reports/: a file its record (work/.deps/reports/) or the last run's list
  names, the fixed names a run writes (`REPORT_FILES`), an account's
  (`ACCOUNT_REPORTS`), a safe_write temp of one, and the watchlists an
  older run left in reports/exports/ (the patterns the run removes them by).
- exports_dir: the files its record (.taxjson-exports.json) says the run
  of a year being cleaned wrote, and the record. Not removed from: a folder
  outside the project's boundary (for a year folder: the folder holding the
  year folders), reached through a link, or overlapping another folder.
- No .tt file goes, whatever names it (`never_removed`), nor a folder
  holding one.

Refused, nothing removed (`projects`): a work/ or reports/ that is a link or
no folder, and a place the user's files live (a folder a [settings] key
names, an account's holdings files, inputs/, holdings/, filed/, ...) of any
project cleaned that is, is inside or holds the work/ or reports/ of any
project cleaned (`_overlap_problem`). From a year folder, only that year's
settings are read.

The order (`remove`), so that whatever a failure or an interrupt leaves is
refused by the filing commands and warned of by the views: in every project
first the reports' .sum files (with none, cli/common `_run_state_problems`
takes what work/ holds as a run's partial books: `PARTIAL_BOOKS`), then the
last full run's record of what it read (.inputs_fingerprint.json: without
it the filing commands refuse the books) and the verdicts; then the rest,
the books that banner looks for after every other file, the records'
folder last; the run locks after every folder of every year and the export.

`holds_books`: whether a work/ holds a run's book for an account (by the
same names), for the commands that refuse without books (lib/checklist
`has_books`, cli/maps, cli/slips, cli/summaries): before any run, and after
a clean that kept something, it does not.
"""
from __future__ import annotations

import contextlib
import errno
import fnmatch
import itertools
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import (Any, Dict, FrozenSet, Iterable, Iterator, List, Optional,
                    Sequence, Set, Tuple)

from taxjson.lib import project_layout as _PL
from taxjson.lib import records as _R

WORK = "work"
REPORTS = "reports"
RUN_LOCK = ".run.lock"
# What work/ keeps (`kept_why`). The names are their owners' (lib/clean
# imports none of them, so lib/checklist can import it); the feature test
# pins each one to its owner's.
SLIP_KEY_SALT = ".slip_key_salt"            # lib/slip_audit KEY_SALT_FILE
WATCH_STATE = ".watch_state.json"           # cli/plan, bin/taxjson_watch
CLOSE_CACHE = ".close_cache.json"           # pipeline/stages (in-kind closes)
PRICE_CACHE = ".price_cache.json"           # cli/plan (harvest's quotes)
HISTORY_CACHE = ".history_cache.json"       # cli/technicals (daily closes)
CRYPTO_SENDS = "crypto_sends"               # lib/crypto_sends GENERATED_DIR
LEGACY_MANIFEST = "_manifest.json"          # pipeline/stage_table _NEVER_SWEPT
COMPARE = "compare"                         # lib/compare SNAP_ROOT
# The run's own entries in work/compare/ (lib/compare PREV, PARTIAL_MARK;
# `.prev.*`: a rotation's staging folder).
COMPARE_RUN = ("prev", ".partial-run")
_COMPARE_RUN_STAGING = ".prev."
DEPS = _R.DEPS_DIR
CHECKLIST_RESULTS = "checklist_results.json"     # lib/checklist RESULTS_FILE
FINGERPRINT = ".inputs_fingerprint.json"         # lib/fingerprint_record FINGERPRINT_FILE
FILED_CHECK = "filed_check.state"                # lib/project_layout FILED_CHECK_STATE
# Removed before anything else but the .sum files, in every project
# (`remove`): the record the filing commands read the books' freshness
# from, and the verdicts that would outlive the books (the checklist's,
# check-filed's last drift).
FIRST = (FINGERPRINT, CHECKLIST_RESULTS, FILED_CHECK)
# What cli/common `_run_state_problems` takes for a run's partial books
# when reports/ holds no .sum (its "did not finish" banner on every view):
# removed after every other file of work/ but the records.
PARTIAL_BOOKS = ("*_gains.json", "*_gains_wash.json", "*_base.json")


def _row(out: str) -> Tuple[str, ...]:
    """A stage's output with what its build writes beside it
    (pipeline/stage_table `companions`, freshness `timeline_beside`): its
    captured stderr and, for a JSON output, its traces and cost timeline."""
    also: Tuple[str, ...] = (out, out + ".diag")
    if out.endswith(".json"):
        stem = out[:-len(".json")]
        also += (stem + ".traces", "." + stem.lstrip(".") + ".timeline.json")
    return also


# The names the writers give the files of work/ that are no account's or
# blend's (each with its owner; the feature test pins them).
WORK_FILES = frozenset((
    *_row("to_base.csv"),                       # pipeline/stage_table "rates"
    *_row("sheltered_base.json"),               # pipeline/stage_table "sheltered"
    FINGERPRINT, CHECKLIST_RESULTS, FILED_CHECK,
    ".inputs_fingerprint.set_aside.json",       # lib/fingerprint_record SET_ASIDE_FILE
    ".to_base.start",                           # pipeline/stages RATES_START_STAMP
    "declared_renames.list",                    # pipeline/stage_table STAMPS "renames"
    "radar_today.state",                        # pipeline/stage_table STAMPS "today"
    "missing_history.state",                    # pipeline/stages MISSING_HISTORY_STATE
    "in_kind.json",                             # lib/in_kind IN_KIND_FILE
    "skipped_accounts.json",                    # pipeline/stages _SKIPPED_ACCOUNTS_FILE
    "pending_elections.json",                   # pipeline/run (the accounts' together)
    "ticker.map.effective",                     # lib/project_layout EFFECTIVE_MAP
    "dated_events.state",                       # lib/dated_events STATE
    "similar.state",                            # lib/similar STATE
    "xlist_loss_radar.state",                   # lib/xlist_state STATE
    "cross_listings.state",                     # lib/cross_listings STATE
    "loss_overrides.json",                      # lib/loss_overrides STATE
    ".harvest_radar.json",                      # cli/plan (`tjs harvest`)
))
# What an older taxjson wrote in work/ that a run still removes
# (pipeline/stage_table _LEGACY; pipeline/run `.code_fingerprint`).
LEGACY_WORK = frozenset((".wash_inputs.json", ".missing_history_applied",
                         ".phantoms_applied", ".project_maps_applied",
                         ".code_fingerprint"))
# An account's books in work/ ({a} the account, {b} an export group of it,
# {t} a .tt file's stem): the rows' outputs (pipeline/stage_table ROWS: the
# crypto chain, the equity merge, gains, the native books, the wash pass or
# the blend's split, the report model, the full-history gains; per group
# the parse and the corporate actions; per .tt file its conversion).
ACCOUNT_BOOKS = ("{a}_merged.json", "{a}_sorted.json", "{a}_mapped.json",
                 "{a}_filled.json", "{a}_base.json", "{a}_gains.json",
                 "{a}_raw.json", "{a}_raw_gains.json", "{a}_raw_base.json",
                 "{a}_raw_base_gains.json", "{a}_gains_wash.json",
                 "{a}_report.json", "{a}_gains_all.json")
GROUP_BOOKS = ("{a}_{b}.json", "{a}_{b}_corp.json")
TT_BOOKS = ("{a}_tt_{t}.json",)
# Every name the writers give an account's files in work/: the books with
# what their builds write beside them (`_row`), and the others, each with
# what writes it. The feature test derives the stage table's own (ROWS,
# STAMPS, companions, account_files) and fails on one missing here.
ACCOUNT_WORK = (
    *(n for b in ACCOUNT_BOOKS for n in _row(b)),
    *_row("{a}_validate.diag"),                 # stage_table "crypto_validate"
    "{a}_holdings_prev.toml",                   # stage_table companions (holdings)
    # stage_table STAMPS
    "{a}_sources.list", "{a}_inputs.fingerprint", "{a}_symbol_codes.state",
    "{a}_ib_project.state", "{a}_ib_tt.state", "{a}_crypto_ticker_map.state",
    # stage_table account_files: the detection notes, the blends' mirrors,
    # the arrivals' costs, the moves between your own accounts
    "{a}_detect.diag", "{a}_blend.diag", "{a}_cryptoblend.diag",
    "{a}_transfer_costs.json", "{a}_own_moves.json",
    # pipeline/inputs: a spin-off's value, a partial carryover, expired options
    "{a}_corp_spinoff_value.diag", "{a}_partial_carryover.diag",
    "{a}_expired_options.diag",
    # pipeline/stages: the crypto sends' notes, the elections a run left pending
    "{a}_crypto_sends.diag", "{a}_pending_elections.json",
)
GROUP_WORK = (
    *(n for b in GROUP_BOOKS for n in _row(b)),
    # the parse's transfer evidence, overrides log and cash events
    # (pipeline/inputs `_parse_outputs`)
    "{a}_{b}_transfers.json", "{a}_{b}.overrides", "{a}_{b}.cash",
    "{a}_{b}_listing_suffix.state",             # stage_table STAMPS "suffix"
)
TT_WORK = tuple(n for b in TT_BOOKS for n in _row(b))
# A blend's ({tag}): the stage table's BLEND rows, and the full history's
# merge (cli/common).
BLEND_WORK = (*_row(".{tag}_base.json"), *_row(".{tag}_gains_wash.json"),
              *_row(".{tag}_gains_all.json"), ".{tag}_all_base.json")
# The export groups a parse runs per: the parsers' ids (parsers/registry
# SPECS; a `generic-<name>` group is known from the last run's list or a
# record), and the blends (pipeline/run).
PARSE_GROUPS = frozenset(("ib", "questrade", "webull", "rbc_direct", "coinbase",
                          "kraken", "generic"))
BLEND_TAGS = frozenset(("blend", "cryptoblend"))


def _rep(out: str) -> Tuple[str, str]:
    """A report and its stage's captured stderr (stage_table `companions`)."""
    return (out, out + ".diag")


# What a run writes into reports/ beside the accounts' files (pipeline/
# stage_table's report rows, the combined radar's JSON beside its .rpt,
# lib/first_run SUMMARY_FILE, `tjs fx-cash`'s report).
REPORT_FILES = frozenset((*_rep("ccd.rpt"), *_rep("leaps.rpt"),
                          *_rep("crosslistings.rpt"), *_rep("fees.rpt"),
                          *_rep("wash_radar_COMBINED.rpt"),
                          "wash_radar_COMBINED.json", "run_summary.json",
                          "fx_cash.rpt"))
# An account's reports (stage_table "sum", "wash_sum", "holdings", "radar"
# and the radar's JSON).
ACCOUNT_REPORTS = (*_rep("{a}.sum"), *_rep("{a}_wash.sum"),
                   *_rep("{a}_holdings.toml"), *_rep("wash_radar_{a}.rpt"),
                   "wash_radar_{a}.json")
# A safe_write temp a stopped write left (`NAME.<8 hex>[.kind].part`).
_TEMP_RE = re.compile(r"(?P<base>.+)\.[0-9a-f]{8}(?:\.[a-z]+)*\.part\Z")
# The watchlist files a run before v0.20 wrote in reports/exports/
# (pipeline/stages _RETIRED_EXPORTS: every run removes them by these too).
_RETIRED_EXPORTS_DIR = "exports"
RETIRED_EXPORTS = ("*_SA.csv", "*_FG.csv", "*_TV.txt")
# The folder an older taxjson wrote (`.gitignore`'s /export/): no command
# writes it now, so what it holds is not known to be taxjson's.
LEGACY_EXPORT = "export"
# Where the user's own files live in a project, by default (each with its
# owner): `projects` refuses one that is, is inside or holds work/ or
# reports/ (a link followed: the run follows it).
_USER_PLACES = (_PL.CONFIG, _PL.TICKER_MAP, _PL.TOBASE_MAP, _PL.INPUTS,
                _PL.SNAPSHOTS,
                "filed",                        # cli/file (close-year's lock)
                "layouts",                      # lib/brokerages/generic LAYOUTS_DIR
                "inputs_redact",                # bin/taxjson_redact TREE_DEFAULT
                "checklist.json")               # lib/checklist STATE_FILE
# The [settings] keys naming a place the user's files live (or a lock).
_PLACE_KEYS = (_PL.INPUTS_KEY, _PL.HOLDINGS_KEY, _PL.EXPORTS_KEY,
               _PL.TOBASE_KEY, "prior_year_record")
_TT_WHY = ("it holds a .tt file, which `tjs clean` never removes: move the "
           ".tt out, then run `tjs clean` again")


class CleanError(Exception):
    """Why nothing is removed: a link, a folder that is not one, a place
    of the user's in work/ or reports/, a busy run lock (`Busy`)."""


class Busy(CleanError):
    """A `tjs run` (or `tjs close-year`) holds a project's run lock."""


@dataclass
class Folder:
    """One folder's part of the plan: the entries removed from it (each
    with the files it holds), by the phase they go in (`remove`): `sums`
    (the .sum files), `first`, `remove` (the rest), `books`
    (`PARTIAL_BOOKS`), `last` (the records); whether the folder goes too
    once empty."""
    path: Path
    shown: str
    remove: List[Tuple[Path, int, bool]] = field(default_factory=list)   # (entry, files, a folder)
    first: List[Tuple[Path, int, bool]] = field(default_factory=list)
    sums: List[Tuple[Path, int, bool]] = field(default_factory=list)
    books: List[Tuple[Path, int, bool]] = field(default_factory=list)
    last: List[Tuple[Path, int, bool]] = field(default_factory=list)
    later: List[Path] = field(default_factory=list)     # emptied folders, deepest first
    rmdir: bool = True
    what: str = ""
    lock: Optional[Path] = None     # work/'s run lock: removed last, with work/
    lock_was_there: bool = False    # (not one `held` made: counted)

    @property
    def entries(self) -> List[Tuple[Path, int, bool]]:
        return self.sums + self.first + self.remove + self.books + self.last

    @property
    def files(self) -> int:
        """The files removed: each entry's, and with work/ the run lock
        when it was there before the command."""
        return (sum(n for _p, n, _d in self.entries)
                + (1 if self.rmdir and self.lock_was_there else 0))

    @property
    def goes(self) -> bool:
        """Anything of it removed: a file, or the folder itself."""
        return bool(self.entries or self.later or self.rmdir)


@dataclass
class Plan:
    """What `tjs clean` in `base` removes, what it keeps (taxjson's, with
    why), what it leaves because taxjson did not write it, and why it does
    not clean an exports folder."""
    base: Path
    projects: List[Path]
    folders: List[Folder] = field(default_factory=list)
    kept: List[Tuple[str, str]] = field(default_factory=list)
    foreign: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    filed: List[Tuple[str, int]] = field(default_factory=list)
    stray: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def files(self) -> int:
        return sum(f.files for f in self.folders)

    @property
    def removed(self) -> List[Folder]:
        """The folders anything is removed from (or that go)."""
        return [f for f in self.folders if f.goes]

    def to_json(self, dry_run: bool) -> Dict[str, Any]:
        return {"dry_run": dry_run, "files": self.files,
                "folders": [{"folder": f.shown, "files": f.files,
                             "folder_removed": f.rmdir,
                             **({"what": f.what} if f.what else {})}
                            for f in self.removed],
                "kept": [{"path": p, "why": w} for p, w in self.kept],
                "not_written": list(self.foreign),
                "skipped": [{"path": p, "why": w} for p, w in self.skipped],
                "filed": [{"project": p, "year": y} for p, y in self.filed],
                "stray": [{"path": p, "why": w} for p, w in self.stray]}


# ----------------------------------------------------------- what stays

def never_removed(name: str) -> bool:
    """A .tt file: `tjs clean` never removes one, whatever names it (your
    own lines; the generated crypto_sends.tt is kept: `kept_why`)."""
    return name.lower().endswith(".tt")


def kept_why(name: str, is_dir: bool) -> Optional[str]:
    """Why an entry of work/ is kept (None: it goes). The run lock is the
    command's own (`held`)."""
    if name == RUN_LOCK:
        return None
    if name.endswith(".lock"):
        return "a lock file another command may hold or wait on"
    if name == SLIP_KEY_SALT:
        return ("the key slips.toml's broker entries are salted with: "
                "without it `tjs slip-audit` no longer matches them")
    if name == WATCH_STATE:
        return "`tjs watch`'s baseline: a new one would hide what changed"
    if name in (CLOSE_CACHE, PRICE_CACHE, HISTORY_CACHE):
        return ("prices fetched from outside, a cache: an offline run reads "
                "only it, an online one could get other prices")
    if name == CRYPTO_SENDS:
        return ("the generated crypto_sends.tt of the shared-exports layout: "
                "the run keeps each line at the price it showed")
    if name.endswith(LEGACY_MANIFEST) and not is_dir:
        return "corporate-action elections an older layout kept in work/: your decisions"
    return None


def _compare_kept(name: str) -> bool:
    return name not in COMPARE_RUN and not name.startswith(_COMPARE_RUN_STAGING)


# ----------------------------------------------------------- recognition

def _names(specs: Iterable[str], **values: Iterable[str]) -> Set[str]:
    """Each of `specs` with every value of the fields it names ({a}, {b},
    {t}, {tag})."""
    out: Set[str] = set()
    for spec in specs:
        keys = [k for k in ("a", "b", "t", "tag") if "{" + k + "}" in spec]
        for vals in itertools.product(*(sorted(values[k]) for k in keys)):
            out.add(spec.format(**dict(zip(keys, vals))))
    return out


@dataclass(frozen=True)
class Known:
    """What says a name in a project's work/ or reports/ is taxjson's: the
    accounts (taxjson.toml's, and the ones the records name), their export
    groups, .tt files and blends, and the names the records name in each
    folder; from them every full name the writers give a file there
    (`names`, `report_names`) and the accounts' books (`books`)."""
    accounts: FrozenSet[str]
    work: FrozenSet[str] = frozenset()
    reports: FrozenSet[str] = frozenset()
    groups: FrozenSet[str] = PARSE_GROUPS
    tts: FrozenSet[str] = frozenset()
    tags: FrozenSet[str] = BLEND_TAGS
    books: FrozenSet[str] = field(init=False, default=frozenset())
    names: FrozenSet[str] = field(init=False, default=frozenset())
    report_names: FrozenSet[str] = field(init=False, default=frozenset())

    def __post_init__(self) -> None:
        a, b, t = self.accounts, self.groups, self.tts
        books = (_names(ACCOUNT_BOOKS, a=a) | _names(GROUP_BOOKS, a=a, b=b)
                 | _names(TT_BOOKS, a=a, t=t))
        names = (books | _names(ACCOUNT_WORK, a=a) | _names(GROUP_WORK, a=a, b=b)
                 | _names(TT_WORK, a=a, t=t) | _names(BLEND_WORK, tag=self.tags))
        object.__setattr__(self, "books", frozenset(books))
        object.__setattr__(self, "names", frozenset(names))
        object.__setattr__(self, "report_names",
                           frozenset(_names(ACCOUNT_REPORTS, a=a)))


def _name_part(v: object) -> bool:
    return (isinstance(v, str) and v not in ("", ".", "..")
            and not any(c in v for c in "/\\\0"))


def _regular(p: Path) -> bool:
    st = _lstat(p)
    return st is not None and stat.S_ISREG(st.st_mode)


def _listed(folder: Path) -> List[str]:
    """The names in `folder`, a real folder (not a link): [] otherwise."""
    if not _is_dir(_lstat(folder)):
        return []
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def _last_run(deps: Path) -> Dict[Any, Any]:
    """work/.deps/run.json's instances ({} when there is none)."""
    run = deps / _R.RUN_FILE
    doc: Any = None
    if _regular(run):
        try:
            doc = json.loads(run.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            doc = None
    got = doc.get("instances") if isinstance(doc, dict) else None
    return got if isinstance(got, dict) else {}


def known(root: Path) -> Known:
    """The project's `Known`: taxjson.toml's accounts; the accounts, export
    groups, .tt files and blends, and the outputs, the last run's list
    (work/.deps/run.json) names; every output a record in work/.deps/ (and
    its reports/) is for, the account a book's record names (`<a>_base.json`,
    `<a>_gains.json`) and a generic mapping's group a parse's record names."""
    accounts: Set[str] = set()
    tab = _PL.read_config_soft(root).get("accounts")
    if isinstance(tab, dict):
        accounts |= {a for a in tab if _name_part(a)}
    groups, tts, tags = set(PARSE_GROUPS), set(), set(BLEND_TAGS)
    work_n: Set[str] = set()
    reports_n: Set[str] = set()
    deps = root / WORK / DEPS
    for rel, ident in _last_run(deps).items():
        parts = rel.split("/") if isinstance(rel, str) else []
        if len(parts) == 2 and _name_part(parts[1]):
            {WORK: work_n, REPORTS: reports_n}.get(parts[0], set()).add(parts[1])
        ident = ident if isinstance(ident, dict) else {}
        for key, into in (("account", accounts), ("broker", groups), ("tag", tags)):
            if _name_part(ident.get(key)):
                into.add(ident[key])
        if _name_part(ident.get("tt")) and _name_part(Path(ident["tt"]).stem):
            tts.add(Path(ident["tt"]).stem)
    for n in _listed(deps):
        if n.endswith(".json") and n != _R.RUN_FILE and _name_part(n[:-5]):
            work_n.add(n[:-5])
            for suf in ("_base.json", "_gains.json"):
                a = n[:-5][:-len(suf)] if n[:-5].endswith(suf) else ""
                if _name_part(a) and not a.startswith(".") and a != "sheltered":
                    accounts.add(a)
    for out in work_n:
        for a in accounts:
            g = out[len(a) + 1:-len(".json")] if (
                out.startswith(f"{a}_generic-") and out.endswith(".json")) else ""
            g = g[:-len("_corp")] if g.endswith("_corp") else g
            if _name_part(g):
                groups.add(g)
    for n in _listed(deps / _R.REPORTS_DIR):
        if n.endswith(".json") and _name_part(n[:-5]):
            reports_n.add(n[:-5])
    return Known(frozenset(accounts), frozenset(work_n), frozenset(reports_n),
                 frozenset(groups), frozenset(tts), frozenset(tags))


def _beside(name: str, names: Iterable[str]) -> bool:
    """`name` a file a stage writes beside one of `names` (a record's
    output): its captured stderr (`.diag`), a JSON output's traces and cost
    timeline (`_row`)."""
    return any(name in _row(f)[1:] for f in names)


def work_ours(name: str, is_dir: bool, k: Known) -> bool:
    """`name`, an entry of work/, one taxjson writes, by its full name: the
    records' folder, a file a record or the last run's list names (or one
    its stage writes beside it), a fixed name, one the writers give an
    account's or a blend's files (`Known.names`), a temp of one of these.
    Never a .tt file, nor any other folder."""
    if never_removed(name):
        return False
    if is_dir:
        return name == DEPS
    if (name in k.work or name in k.names or name in WORK_FILES
            or name in LEGACY_WORK or _beside(name, k.work)):
        return True
    m = _TEMP_RE.match(name)
    return bool(m) and work_ours(m.group("base"), False, k)


def report_ours(name: str, k: Known) -> bool:
    """`name`, a file of reports/, one a run writes there: one a record or
    the last run's list names (or its stage's stderr, a radar's JSON beside
    its .rpt), a fixed name, an account's (`Known.report_names`), a temp of
    one of these. Never a .tt file."""
    if never_removed(name):
        return False
    if name in k.reports or name in REPORT_FILES or name in k.report_names:
        return True
    if any(name == f + ".diag" or (f.startswith("wash_radar_") and f.endswith(".rpt")
                                   and name == f[:-len(".rpt")] + ".json")
           for f in k.reports):
        return True
    m = _TEMP_RE.match(name)
    return bool(m) and report_ours(m.group("base"), k)


def holds_books(work: Path) -> bool:
    """work/ holds a run's book: a file named as the stages name an
    account's books (`Known.books`, for the accounts taxjson.toml and the
    records name). False when work/ is not there, and when it holds only
    other files: the records, the verdicts, what `tjs clean` keeps, a file
    of the user's (notes.txt, my_notes.json, a .tt draft)."""
    try:
        with os.scandir(work) as it:
            entries = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
    except OSError:
        return False
    k = known(Path(work).parent)
    return any(not d and n in k.books for n, d in entries)


def no_books(work: Path) -> Optional[str]:
    """None when work/ holds books (`holds_books`), else what is missing:
    "no work/" (no folder), "no books in work/" (one with none)."""
    if holds_books(work):
        return None
    return "no books in work/" if _is_dir(_lstat(work)) else "no work/"


# ----------------------------------------------------------- the walk

def _lstat(p: Path) -> Optional[os.stat_result]:
    try:
        return os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _is_dir(st: Optional[os.stat_result]) -> bool:
    return st is not None and stat.S_ISDIR(st.st_mode)


def _count(p: Path) -> int:
    """The files (links and other entries too) under the folder `p`, no
    link followed."""
    n = 0
    with os.scandir(p) as it:
        for e in it:
            if e.is_dir(follow_symlinks=False):
                n += _count(Path(e.path))
            else:
                n += 1
    return n


def _holds_tt(p: Path) -> bool:
    """A .tt file anywhere under the folder `p` (no link followed)."""
    with os.scandir(p) as it:
        for e in it:
            if e.is_dir(follow_symlinks=False):
                if _holds_tt(Path(e.path)):
                    return True
            elif never_removed(e.name):
                return True
    return False


def _entries(p: Path) -> List[Tuple[str, bool]]:
    with os.scandir(p) as it:
        return sorted((e.name, e.is_dir(follow_symlinks=False)) for e in it)


def _shown(p: Path, base: Path, folder: bool = False) -> str:
    s = _PL.shown(p, base)
    return s + "/" if folder else s


def _refuse_link(p: Path, base: Path) -> None:
    """A work/ or reports/ that is a link, or no folder: nothing removed."""
    st = _lstat(p)
    if st is None or _is_dir(st):
        return
    where = _shown(p, base)
    if stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(p)
        except OSError:
            target = "?"
        raise CleanError(f"{where}/ is a symlink (-> {target}): `tjs clean` "
                         f"never follows a link, so nothing was removed — "
                         f"replace the link with a real folder, or remove "
                         f"it yourself")
    raise CleanError(f"{where} is not a folder: `tjs run` writes a folder "
                     f"there, so nothing was removed — move the file away")


def _places(root: Path) -> List[Tuple[str, str, bool]]:
    """(what names it, the path, from a setting) of each place the
    project's own files live: the [settings] keys naming one
    (`_PLACE_KEYS`), each account's holdings files (read as `tjs sanity`
    reads them: project/accounts `holdings_paths`), the default folders
    and files (`_USER_PLACES`, holdings/ without `holdings_dir`)."""
    doc = _PL.read_config_soft(root)
    st = doc.get("settings")
    st = st if isinstance(st, dict) else {}
    out = []
    for key in _PLACE_KEYS:
        v = st.get(key)
        if isinstance(v, str) and v.strip():
            out.append((f"[settings] {key} = {json.dumps(v)}", v.strip(), True))
    from taxjson.project.accounts import accounts_of
    for name, acct in sorted(accounts_of(doc).items()):
        raw = acct.holdings
        for h in acct.holdings_paths or []:
            if isinstance(h, str) and h.strip():
                out.append((f"[accounts.{name}] holdings = {json.dumps(raw)}",
                            h.strip(), True))
    for n in _USER_PLACES + (() if st.get(_PL.HOLDINGS_KEY) else (_PL.HOLDINGS,)):
        out.append((n, n, False))
    return out


def _forms(p: str, root: Path) -> List[Path]:
    """`p` (a setting's path, from the project) as written and with every
    link followed."""
    q = Path(os.path.expanduser(p))
    q = q if q.is_absolute() else Path(os.path.abspath(root)) / q
    return [Path(os.path.normpath(str(q))), Path(os.path.realpath(q))]


def _overlap_problem(roots: Sequence[Path], base: Path) -> Optional[str]:
    """Why `tjs clean` may not clean the projects `roots`: a place the
    files of one of them live (`_places`) is, is inside or holds the work/
    or reports/ of one of them (as written, or a link followed; another
    year's too: 2024's holdings file in 2023/reports/), so removing the
    run's files there could remove the user's. None when none does."""
    folders = [(r, name, [Path(os.path.abspath(r)) / name,
                          Path(os.path.realpath(r)) / name])
               for r in roots for name in (WORK, REPORTS)]
    for root in roots:
        for what, path, setting in _places(root):
            forms = _forms(path, root)
            for r, name, ws in folders:
                for a in forms:
                    for w in ws:
                        how = ("is" if a == w else "is inside" if w in a.parents
                               else "holds" if a in w.parents else None)
                        if how is None:
                            continue
                        where = _shown(r / name, base, folder=True)
                        named = (f"{what} in {_shown(root / _PL.CONFIG, base)}"
                                 if setting else _shown(root / path, base))
                        if a != forms[0]:
                            named += f" (a link to {_shown(a, base)})"
                        fix = ("move those files out of work/ and reports/ and "
                               "change the setting" if setting else
                               "move it out of work/ and reports/")
                        return (f"{named} {how} {where}, where `tjs clean` "
                                f"removes what `tjs run` wrote: nothing was "
                                f"removed — {fix}")
    return None


def projects(base: Path) -> List[Path]:
    """The projects `tjs clean` in `base` cleans: `base`, or in the folder
    holding the year folders each year folder (project_layout.year_dirs),
    oldest first; all of them checked before anything is taken or
    removed: a work/ or reports/ that is a link or no folder, a
    taxjson.toml that cannot be read, and a place the user's files live of
    any of them inside the work/ or reports/ of any of them
    (`_overlap_problem`), refuse (CleanError). A year folder's own settings
    only: another year's are not read."""
    roots = ([base] if _PL.has_config(base)
             else [d for _y, d in _PL.year_dirs(base)])
    for r in roots:
        for name in (WORK, REPORTS):
            _refuse_link(r / name, base)
    for r in roots:
        try:
            _PL.read_config(r)
        except _PL.LayoutError as e:
            # (where its settings put the user's files is not known)
            raise CleanError(f"{e}: `tjs clean` reads it to know where your "
                             f"files are, so nothing was removed — fix it "
                             f"first") from None
    why = _overlap_problem(roots, base)
    if why:
        raise CleanError(why)
    return roots


@contextlib.contextmanager
def held(roots: List[Path], *, dry_run: bool) -> Iterator[List[Path]]:
    """Hold each project's run lock (work/.run.lock: pipeline/run
    `_acquire_run_lock`) for the block: Busy, before anything is removed,
    when a run or close-year holds one. The lock files already there are
    taken first, so a busy one refuses before a missing one is made (one
    made is removed again when another refuses). A project with no work/
    has no lock (none is made); a dry run takes only a lock file already
    there."""
    from taxjson.lib.safe_write import LockLinkError, file_lock
    there, missing = [], []
    for r in roots:
        work = r / WORK
        if not _is_dir(_lstat(work)):
            continue
        st = _lstat(work / RUN_LOCK)
        if st is not None and stat.S_ISREG(st.st_mode):
            there.append(r)
        elif not dry_run:
            missing.append(r)
    made: List[Path] = []           # yielded: the lock files it made
    with contextlib.ExitStack() as stack:
        try:
            for r in there + missing:
                lock = r / WORK / RUN_LOCK

                def busy(r=r) -> None:
                    raise Busy(r)
                fresh = r in missing and _lstat(lock) is None
                try:
                    stack.enter_context(file_lock(lock, on_wait=busy))
                except LockLinkError as e:
                    raise CleanError(f"{e}; nothing was removed") from None
                if fresh:
                    made.append(lock)
        except CleanError:
            for lock in made:
                with contextlib.suppress(OSError):
                    os.unlink(lock)
            raise
        yield made


# ----------------------------------------------------------- the plan

def _year(root: Path) -> Optional[int]:
    st = _PL.read_config_soft(root).get("settings")
    y = st.get("year") if isinstance(st, dict) else None
    return y if isinstance(y, int) and not isinstance(y, bool) else None


def _entry(p: Path, is_dir: bool) -> Tuple[Path, int, bool]:
    return (p, _count(p) if is_dir else 1, is_dir)


def _plan_compare(p: Path, base: Path, f: Folder, plan: Plan) -> bool:
    """work/compare/: the run's own entries go (a folder of them holding a
    .tt stays), the snapshots stay; True when one stays."""
    stays = False
    for n, d in _entries(p):
        if _compare_kept(n):
            plan.kept.append((_shown(p / n, base, d), "a snapshot `tjs compare --save` kept "
                              "(`tjs compare --delete NAME` removes one)"))
            stays = True
        elif never_removed(n) or (d and _holds_tt(p / n)):
            plan.kept.append((_shown(p / n, base, d), _TT_WHY))
            stays = True
        else:
            f.remove.append(_entry(p / n, d))
    if not stays:
        f.later.append(p)
    return stays


def _plan_work(root: Path, base: Path, plan: Plan, k: Known,
               made: Sequence[Path]) -> Optional[Folder]:
    work = root / WORK
    if not _is_dir(_lstat(work)):
        return None
    lock = work / RUN_LOCK
    f = Folder(work, _shown(work, base, folder=True), lock=lock,
               lock_was_there=_lstat(lock) is not None and lock not in made)
    stays = False
    for name, is_dir in _entries(work):
        p = work / name
        if name == RUN_LOCK:
            continue
        if name == COMPARE and is_dir:
            stays = _plan_compare(p, base, f, plan) or stays
            continue
        why = kept_why(name, is_dir)
        if why is not None:
            plan.kept.append((_shown(p, base, is_dir), why))
            stays = True
        elif not work_ours(name, is_dir, k):
            plan.foreign.append(_shown(p, base, is_dir))
            stays = True
        elif name == DEPS:
            if _holds_tt(p):
                plan.kept.append((_shown(p, base, True), _TT_WHY))
                stays = True
            else:
                f.last.append(_entry(p, is_dir))
        elif name in FIRST:
            f.first.append(_entry(p, is_dir))
        elif not name.startswith(".") and any(fnmatch.fnmatchcase(name, pat)
                                              for pat in PARTIAL_BOOKS):
            f.books.append(_entry(p, is_dir))
        else:
            f.remove.append(_entry(p, is_dir))
    f.rmdir = not stays
    plan.folders.append(f)
    return f


def _plan_reports(root: Path, base: Path, plan: Plan, k: Known) -> None:
    reports = root / REPORTS
    if not _is_dir(_lstat(reports)):
        return
    f = Folder(reports, _shown(reports, base, folder=True))
    stays = False
    for name, is_dir in _entries(reports):
        p = reports / name
        if is_dir and name == _RETIRED_EXPORTS_DIR:
            rest = []
            for n, d in _entries(p):
                if not d and not never_removed(n) and any(
                        fnmatch.fnmatchcase(n, pat) for pat in RETIRED_EXPORTS):
                    f.remove.append((p / n, 1, False))
                else:
                    rest.append((n, d))
            plan.foreign += [_shown(p / n, base, d) for n, d in rest]
            if rest:
                stays = True
            else:
                f.later.append(p)
            continue
        if not is_dir and report_ours(name, k):
            (f.sums if name.endswith(".sum") else f.remove).append((p, 1, False))
            continue
        plan.foreign.append(_shown(p, base, is_dir))
        stays = True
    f.rmdir = not stays
    plan.folders.append(f)


def _exports_target(root: Path) -> Tuple[Optional[Path], Optional[str]]:
    """(the exports folder `root`'s run writes, why clean may not remove a
    file there or None); (None, None) when `exports_dir` is unset or names
    nothing there. Not removed from: a folder reached through a link,
    outside the project's boundary (a year folder's: the folder holding
    the year folders, which a filed year's run writes into too; else
    project_layout.write_boundary), overlapping another folder
    (project_layout.exports_overlap), not a folder. LayoutError when the
    setting cannot be used."""
    st = _PL.read_config_soft(root).get("settings")
    raw = st.get(_PL.EXPORTS_KEY) if isinstance(st, dict) else None
    if raw is None:
        return None, None
    real = _PL.folder_setting(root, _PL.EXPORTS_KEY)
    if real is None or _lstat(real) is None:
        return None, None
    rr = Path(os.path.realpath(root))
    written = Path(os.path.normpath(os.path.join(
        str(rr), os.path.expanduser(str(raw).strip()))))
    if written != real:
        return real, ("it is reached through a symlink, which `tjs clean` "
                      "never follows")
    years = _PL.multi_root(rr) or (rr.parent if _PL.years_layout(rr) else None)
    bound = Path(os.path.realpath(years if years is not None
                                  else _PL.write_boundary(rr)))
    if bound not in real.parents:
        return real, ("it is outside the project, where `tjs clean` never "
                      "removes a file (remove the export yourself if you "
                      "mean to)")
    ov = _PL.exports_overlap(root, st)
    if ov:
        return real, ov
    if not _is_dir(_lstat(real)):
        return real, "it is not a folder"
    return real, None


def _plan_exports(roots: List[Path], base: Path, plan: Plan) -> None:
    """Each exports folder a project of `roots` writes: the files its
    record says the run of one of these years wrote (never a .tt), and the
    record, when that year's project may remove them (else a Warning:
    `skipped`); another year's export, or one with no readable record, left
    alone. A record naming no year is any year's: cleaned only where every
    year is (a single-folder project, the folder holding the year folders)."""
    every_year = (not _PL.has_config(base)) or _PL.multi_root(base) is None
    targets: Dict[Path, List[Tuple[Optional[int], Optional[str]]]] = {}
    for r in roots:
        try:
            dst, why = _exports_target(r)
        except _PL.LayoutError as e:
            plan.skipped.append((f"the exports_dir of "
                                 f"{_shown(r / _PL.CONFIG, base)}", str(e)))
            continue
        if dst is not None:
            targets.setdefault(dst, []).append((_year(r), why))
    for dst, mine in targets.items():
        shown = _shown(dst, base, folder=True)
        rec = _PL.exports_record(dst) if _is_dir(_lstat(dst)) else None
        if rec is None:
            if any(w is None for _y, w in mine) and _entries(dst):
                plan.kept.append((shown, f"no readable {_PL.EXPORTS_MANIFEST} "
                                  f"names what `tjs run` wrote there: left alone"))
            continue
        year, names = rec
        if year is None and not every_year:
            plan.kept.append((shown, "an export whose record names no year: "
                              "`tjs clean` in the folder holding the year "
                              "folders removes it"))
            continue
        whys = [w for y, w in mine if year is None or y == year]
        if not whys:
            plan.kept.append((shown, f"the {year} run's export, a year not "
                              f"cleaned here: left alone"))
            continue
        if None not in whys:
            plan.skipped.append((shown, str(whys[0])))
            continue
        f = Folder(dst, shown, what=(f"the {year} run's export" if year is not None
                                     else "the export (its record names no year)"))
        ours = set()
        for n in names:
            st = _lstat(dst / n)
            if st is not None and not _is_dir(st) and not never_removed(n):
                f.remove.append((dst / n, 1, False))
                ours.add(n)
        f.remove.append((dst / _PL.EXPORTS_MANIFEST, 1, False))
        others = sorted({n for n, _d in _entries(dst)} - ours
                        - {_PL.EXPORTS_MANIFEST})
        plan.foreign += [_shown(dst / n, base) for n in others]
        f.rmdir = not others
        plan.folders.append(f)


_LEGACY_WHY = "an older taxjson's folder, which no command writes now"
_STRAY_WHY = "no year's: each year folder has its own"


def plan(base: Path, roots: List[Path], made: Sequence[Path] = ()) -> Plan:
    """What `tjs clean` in `base` removes from the projects `roots`
    (`projects`), and what it keeps or leaves alone, each with why. Read
    with each run lock held (`held`; `made`: the lock files it made, not
    counted as removed). OSError when a folder cannot be read."""
    pl = Plan(base, roots)
    for r in roots:
        k = known(r)
        work = _plan_work(r, base, pl, k, made)
        _plan_reports(r, base, pl, k)
        y = _year(r)
        if (work is not None and work.goes and y is not None
                and (r / "filed" / f"{y}.json").is_file()):
            pl.filed.append((_shown(r, base, folder=True), y))
        if _lstat(r / LEGACY_EXPORT) is not None:
            pl.stray.append((_shown(r / LEGACY_EXPORT, base, folder=True), _LEGACY_WHY))
    if not _PL.has_config(base):
        for n, why in ((LEGACY_EXPORT, _LEGACY_WHY), (WORK, _STRAY_WHY),
                       (REPORTS, _STRAY_WHY)):
            if _lstat(base / n) is not None:
                pl.stray.append((_shown(base / n, base, folder=True), why))
    _plan_exports(roots, base, pl)
    return pl


# ----------------------------------------------------------- removing

def _full_path(_func: Any, path: str, exc: BaseException) -> None:
    """shutil.rmtree's onexc: the error names the whole path (its own names
    only the entry, relative to the folder it was in)."""
    if isinstance(exc, OSError):
        exc.filename = path
    raise exc


def _remove(p: Path) -> None:
    st = _lstat(p)
    if st is None:
        return
    if _is_dir(st):
        shutil.rmtree(p, onexc=_full_path)      # fd-based: a link inside is removed, never followed
    else:
        os.unlink(p)


def _rmdir(p: Path) -> None:
    """The folder `p` removed when empty; one that is not (a file made
    since the plan) stays."""
    try:
        os.rmdir(p)
    except FileNotFoundError:
        pass
    except OSError as e:
        if e.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise


def _unlock(lock: Path) -> None:
    """A run lock's file removed (its lock still held, `held`)."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(lock)


def remove(pl: Plan) -> None:
    """Remove what `pl` lists (each run lock still held), phase by phase
    over every folder of every project, so that a stop part-way leaves no
    books a command takes as whole: the reports' .sum files, then the
    run's record of what it read and the verdicts (`first`), then every
    other entry, then the books a view's banner looks for
    (`PARTIAL_BOOKS`), then the records' folder, the emptied sub-folders
    and the folders that go, and only then the run locks and work/ itself.
    OSError when an entry cannot be removed (what went before it is
    gone)."""
    for phase in ("sums", "first", "remove", "books"):
        for f in pl.folders:
            for p, _n, _d in getattr(f, phase):
                _remove(p)
    for f in pl.folders:
        for p, _n, _d in f.last:
            _remove(p)
        for p in f.later:
            _rmdir(p)
        if f.rmdir and f.lock is None:
            _rmdir(f.path)
    for f in pl.folders:
        if f.rmdir and f.lock is not None:
            _unlock(f.lock)
            _rmdir(f.path)

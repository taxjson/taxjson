"""`tjs unclose-year`: a `tjs close-year` taken back (backlog TJ-4974).

The close wrote three things as one step (cli/file `cmd_close_year`): the
freeze (the year's own copy of the shared exports and tobase.map, and
taxjson.toml reading them), the reports as filed (filed/<year>/reports/)
and the lock (filed/<year>.json). This takes the three back as one step,
all or nothing (lib/write_step `WriteStep`), and keeps the record of what
was filed in a backup folder of its own, filed/<year>.unclosed-<stamp>/,
which no reader of a lock looks into (they read filed/<year>.json and
filed/<year>/reports/ only):

- the lock and the reports as filed are moved into the backup;
- taxjson.toml names `inputs_dir` / `tobase_map` again and loses the
  freeze note: only the close's own lines are edited (`_thawed`), so an
  edit made since stays; the file as closed is copied into the backup;
- a frozen copy the same as the shared file it was copied from is removed;
  one that differs, or that the shared folder does not hold, is moved into
  the backup, never deleted, and the report says the year is now computed
  from the shared inputs and may differ from what was filed;
- the positions snapshots close-year copied from a holdings folder every
  year shares (`holdings_dir`, backlog TJ-5367) go the same way (SNAPSHOT:
  the figures never read them).

Every write is a rename or an atomic write whose undo step is added before
it; the record backup/unclosed.json is written last, the commit point.
Whatever stops the command before it puts everything back.
"""
import argparse
import contextlib
import dataclasses
import datetime
import json
import os
import shlex
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from taxjson.cli.common import _FROZEN_NOTE_PREFIX, _raise_requirements
from taxjson.cli.file import (
    _config_rewrite_target, _FROZEN_NOTE_2, _gitignore_hides_filed_reports,
)
from taxjson.lib import project_layout as _PL
from taxjson.lib.cli_diag import tax_year as _tax_year_arg
from taxjson.lib.out import Doc
from taxjson.lib.safe_write import write_atomic as _write_atomic
from taxjson.lib.write_step import WriteStep
from taxjson.pipeline.config import _read_config_text, load_config
from taxjson.pipeline.messages import _details, _die, _die_input, _PROG, _say
from taxjson.pipeline.run import _acquire_run_lock

_NOTHING = "Nothing was changed."
# The backup's record of the reopening, written last: the commit point.
RECORD = "unclosed.json"
# Inside the backup until the record is written: the copies being removed.
_REMOVED = ".removed"
# Why a frozen copy is kept (moved into the backup), in the order shown.
DIFFERS, ONLY_COPY, UNREADABLE, GENERATED = ("differs", "only-copy",
                                             "unreadable", "generated")
# A positions snapshot of the year's frozen holdings/ that is not the
# shared folder's (another file, or none there): kept in the backup.
SNAPSHOT = "snapshot"
_KEYS = (_PL.INPUTS_KEY, _PL.TOBASE_KEY, _PL.HOLDINGS_KEY)
# How a setting came back: its own line under the note un-commented, or
# written from the lock's record (the line gone, or not the close's).
NOTE, RECORD_GONE, RECORD_OTHER = "note", "record", "record-other-line"
# The year's own copy of what each setting names.
_OWN_COPY = {_PL.INPUTS_KEY: f"{_PL.INPUTS}/", _PL.HOLDINGS_KEY: f"{_PL.HOLDINGS}/"}


@dataclasses.dataclass
class Plan:
    """What reopening `year` in `root` changes (`_plan`): nothing is
    written to make it. `lock`: None when it is already gone;
    `text`: taxjson.toml's new text, None when it stays as it is;
    `restored`: (key, value, NOTE | RECORD_GONE | RECORD_OTHER) of each
    setting named again; `by_hand`: the ones the file names again
    already; `unsure`: (key, line) of a commented line under a note that
    was not restored and whose key stays unset (nothing says the close
    wrote it: left as it is); `remove`: the frozen copies the same as the
    shared files; `keep`: (file, why) of those moved into the backup;
    `unread`: shared files the filed year never read; `started`: (year,
    folder) of the later years started from this one; `left`: copies left
    where they are (links); `layouts`: the project's layouts/ files the
    lock's record names (the close copies a layout of the user's folder
    there: they stay)."""
    root: Path
    year: int
    backup: Path
    lock: Optional[Path] = None
    lock_unread: bool = False
    reports: Optional[Path] = None
    report_count: int = 0
    cfg_real: Optional[Path] = None
    text: Optional[str] = None
    restored: List[Tuple[str, str, str]] = dataclasses.field(default_factory=list)
    by_hand: List[str] = dataclasses.field(default_factory=list)
    unsure: List[Tuple[str, str]] = dataclasses.field(default_factory=list)
    remove: List[Path] = dataclasses.field(default_factory=list)
    keep: List[Tuple[Path, str]] = dataclasses.field(default_factory=list)
    unread: int = 0
    shared: str = ""
    started: List[Tuple[int, Path]] = dataclasses.field(default_factory=list)
    left: List[Path] = dataclasses.field(default_factory=list)
    layouts: List[Path] = dataclasses.field(default_factory=list)

    def rel(self, p: Path) -> str:
        return _PL.shown(p, self.root)

    def kept(self, *whys: str) -> List[Path]:
        return [p for p, w in self.keep if w in whys]

    @property
    def diverged(self) -> bool:
        """The reopened year reads other inputs than it was filed from."""
        return bool(self.kept(DIFFERS, ONLY_COPY, UNREADABLE) or self.unread)

    def shown_backup(self, done: bool) -> str:
        """The backup folder in the report: its name once made; before
        (--dry-run) the pattern, since the run takes the time it starts."""
        if done:
            return self.rel(self.backup)
        return f"filed/{self.year}.unclosed-<date>-<time>"


def cmd_unclose_year(args: argparse.Namespace) -> None:
    """`tjs unclose-year`: take back the year's `tjs close-year`, all or
    nothing as the close is (lib/write_step `WriteStep`; its commit file
    the backup's record, written last): whatever stops it before the
    record puts back all it moved and says nothing was changed
    (`_not_reopened`); once the record is written the year is reopened
    (`_reopened_anyway`). A Ctrl-C pressed again meanwhile waits, and one
    held or swallowed is raised before the record and at the end."""
    state = WriteStep()
    try:
        state.hold_interrupts()
        _unclose_year(args, state)
        state.raise_held()
        state.release()
    except BaseException as e:
        try:
            while True:
                try:
                    # (an interrupt raised as it is called: the call again)
                    state.hold_interrupts()
                    written = state.settle()
                    break
                except KeyboardInterrupt:
                    pass
            if not written:
                _not_reopened(e)
            elif not (isinstance(e, SystemExit) and e.code in (None, 0)):
                _reopened_anyway(Path(args.dir).resolve(), state)
        finally:
            state.release()
        raise


def _unclose_year(args: argparse.Namespace, state: WriteStep) -> None:
    """cmd_unclose_year's steps: the plan (every refusal, nothing
    written), then with --dry-run the plan shown, else the moves and the
    record as one step and the report. The year reopened reads the
    shared exports again: an elections.json there raises its
    requires_taxjson to the release that reads it (cli/common
    `_raise_requirements`, as migrate does: an older taxjson would read
    no elections)."""
    root = Path(args.dir).resolve()
    # (two tobase.map files of the year are this command's to take back)
    cfg = load_config(root, reopening=True)
    settings = cfg["settings"]
    year = args.year or settings.year
    if year != settings.year:
        _die(f"this project's tax year is {settings.year}, not {year}",
             f"Run it in {year}'s project (a single-folder project: set "
             f"[settings].year to {year} first). {_NOTHING}")
    dry = bool(getattr(args, "dry_run", False))
    if not dry and (root / "work").is_dir():
        # No `tjs run` (or close) meanwhile: it reads what this moves.
        _acquire_run_lock(root / "work", closing=True)
    plan = _plan(root, cfg, int(year))
    if dry:
        _report(plan, args, done=False)
        return
    _apply(plan, state)
    _report(plan, args, done=True)
    _raise_requirements(root, ("elections_json",))


# ------------------------------------------------------------------ plan

def _plan(root: Path, cfg: Dict[str, Any], year: int) -> Plan:
    """The reopening of `year`, or the refusal (exit 1; 2 for a file
    that cannot be read or written): a year not closed, a later year
    closed on it, a frozen copy that is the only one of a shared file
    this could not keep. Nothing is written. What was frozen is what the
    lock's `frozen` record says (`fz`; None: no record, a lock gone,
    unreadable, of an older release or taken before the year ended), and
    only without one what the note in taxjson.toml shows."""
    lock = root / "filed" / f"{year}.json"
    reports = root / "filed" / str(year) / "reports"
    has_lock = os.path.lexists(lock)
    if not has_lock and not _PL.lock_missing_frozen(root, year):
        _die(f"{year} is not closed (no filed/{year}.json): there is "
             f"nothing to reopen")
    if has_lock and (lock.is_symlink() or not lock.is_file()):
        _die_input(f"filed/{year}.json is not a regular file",
                   f"A lock is a file `tjs close-year` wrote: move this "
                   f"one away by hand. {_NOTHING}")
    plan = Plan(root=root, year=year, backup=_backup_dir(root, year),
                lock=lock if has_lock else None)
    _later_years(plan)
    fz: Any = None
    if has_lock:
        try:
            doc = json.loads(lock.read_text(encoding="utf-8-sig"))
            fz = doc.get("frozen") if isinstance(doc, dict) else None
        except (OSError, ValueError):
            plan.lock_unread = True
    fz = fz if isinstance(fz, dict) and not fz.get("partial") else None
    if os.path.lexists(reports):
        plan.reports = reports
        with contextlib.suppress(OSError):
            plan.report_count = sum(1 for p in reports.iterdir())
    why = _backup_problem(root)
    if why:
        _die_input(f"the backup of what was filed cannot be written: {why}",
                   _NOTHING)
    st, frozen = _settings_back(plan, fz)
    hashes = (fz or {}).get("inputs")
    hashes = hashes if isinstance(hashes, dict) else None
    plan.layouts = [root / k for k in sorted(hashes or {})
                    if str(k).startswith("layouts/") and ".." not in str(k)
                    and (root / k).is_file()]
    try:
        if _PL.INPUTS_KEY in frozen:
            _input_copies(plan, cfg, st, hashes)
        if _PL.TOBASE_KEY in frozen:
            _tobase_copy(plan, st)
        if _PL.HOLDINGS_KEY in frozen:
            _holdings_copies(plan, st)
        # The year's own tobase.map beside the setting is no problem when
        # it is the frozen copy this takes away (or a link to the shared
        # file); any other problem of the folder settings is a refusal.
        own = root / _PL.TOBASE_MAP
        goes = own in plan.remove or own in plan.kept(DIFFERS, UNREADABLE)
        both = _PL.tobase_both_problem(root, st)
        bad = [b for b in _PL.setting_problems(root, st)
               if not (goes and b == both)]
    except _PL.LayoutError as e:
        bad, both = [str(e)], None
    if bad and bad[0] == both:
        # tobase_map named by hand beside a tobase.map nothing records as
        # the close's copy: which of the two the year reads is the
        # person's to say.
        _die(both, f"Then run this again. {_NOTHING}")
    if bad:
        _die(f"taxjson.toml cannot name the shared folders again: {bad[0]}",
             _NOTHING)
    return plan


def _backup_dir(root: Path, year: int) -> Path:
    """filed/<year>.unclosed-<date>-<time>/, a name not taken: beside the
    locks, where the records of filed years are kept and committed, and
    read by nothing (a lock is filed/<year>.json, the reports as filed
    filed/<year>/reports/)."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = root / "filed" / f"{year}.unclosed-{stamp}"
    d, n = base, 1
    while os.path.lexists(d):
        n += 1
        d = base.with_name(f"{base.name}-{n}")
    return d


def _backup_problem(root: Path) -> Optional[str]:
    """Why filed/ cannot take the backup folder, else None."""
    d = root / "filed"
    if d.is_symlink() or d.exists():
        if not d.is_dir():
            return "filed exists and is not a folder"
        if not os.access(d, os.W_OK | os.X_OK):
            return "filed/ cannot be written (Permission denied)"
        return None
    if not os.access(root, os.W_OK | os.X_OK):
        return "the project folder cannot be written (Permission denied)"
    return None


def _dir_arg(d: Path) -> str:
    """The `-C` argument naming the folder `d` from where the command
    was run (`../2025`), its full path when it is further away; quoted."""
    try:
        rel = os.path.relpath(d)
    except ValueError:                      # another drive (Windows)
        rel = str(d)
    if rel.startswith(os.pardir + os.sep + os.pardir):
        rel = str(d)
    return shlex.quote(rel)


def _later_projects(plan: Plan) -> List[Tuple[int, Path, Optional[str], bool]]:
    """(year, folder, why, locked) of the later years' projects beside this
    one: the year folders (lib/project_layout `year_dirs`), and any other
    folder beside it holding a taxjson.toml, a link to a folder included
    (`2025 -> y2025`: year_dirs leaves links out), whose year is later
    (beside year folders only: two unrelated projects may share a parent)
    or whose `prior_year_record` names this year's lock. Each folder once,
    by where it leads; only the folders directly beside this one are read,
    none walked into. `why` is None, or why a folder beside this one cannot
    say whether it is a later year closed (`_cannot_tell`, TJ-5088): listed
    all the same, `locked` when its filed/ holds a later year's lock (the
    latest is the year), else at its name's year or the next one."""
    root, year = plan.root, plan.year
    lock = root / "filed" / f"{year}.json"
    multi = _PL.multi_root(root)
    out: Dict[Path, Tuple[int, Path, Optional[str], bool]] = {}
    for y, d in (_PL.year_dirs(multi) if multi is not None else []):
        if y > year and d.resolve() != root:
            out[d.resolve()] = (y, d, None, False)
    try:
        beside = sorted(root.parent.iterdir())
    except OSError:
        beside = []
    for d in beside:
        try:
            real = d.resolve()
            if real == root or real in out or not d.is_dir() \
                    or real in root.parents:
                continue
            broken = _cannot_tell(plan, d, year)
            if broken is not None:
                out[real] = (broken[0], d, broken[1], broken[2])
                continue
            if not (d / _PL.CONFIG).is_file():
                continue
            st = _PL.read_config_soft(d).get("settings")
        except (OSError, RuntimeError):
            continue
        st = st if isinstance(st, dict) else {}
        y = st.get("year")
        y = y if isinstance(y, int) and not isinstance(y, bool) else None
        prior = st.get("prior_year_record")
        names = False
        if isinstance(prior, str) and prior.strip():
            q = Path(prior.strip()).expanduser()
            q = q if q.is_absolute() else d / q
            with contextlib.suppress(OSError, RuntimeError):
                names = q.resolve() == lock.resolve()
        if names and (y is None or y > year) \
                or (multi is not None and y is not None and y > year):
            out[real] = (y if y is not None else year + 1, d, None, False)
    return sorted(out.values(), key=lambda t: (t[0], str(t[1])))


def _cannot_tell(plan: Plan, d: Path, year: int
                 ) -> Optional[Tuple[int, str, bool]]:
    """(year, why, locked) when the folder `d` beside this project may be a
    later year closed and cannot say (TJ-5088): its taxjson.toml cannot say
    which year it is (`_unread`) and its filed/ holds a later year's lock
    (`locked`), or cannot be listed. Only a folder holding a taxjson.toml
    (a link to nothing too) or named as a later year (`2025`, a link so
    named too) is one: a folder of another name with no taxjson.toml is no
    project. None when it can say, or is none."""
    named = int(d.name) if _PL.YEAR_DIR_RE.match(d.name) else None
    if named is not None and named <= year:
        named = None
    if named is None and not os.path.lexists(d / _PL.CONFIG):
        return None
    why = _unread(plan, d)
    if why is None:
        return None
    later, unlisted = _later_locks(d, year)
    if later:
        return later[-1], why, True
    if unlisted:
        return (named or year + 1,
                f"{plan.rel(d / 'filed')} cannot be listed ({unlisted}) and "
                f"{why}", False)
    return None


def _unread(plan: Plan, d: Path) -> Optional[str]:
    """Why the project folder `d`'s taxjson.toml cannot say which year it
    is: it is missing or a link to nothing, it cannot be read or parsed, or
    its [settings] year is not one; None when it can."""
    cfg = d / _PL.CONFIG
    rel = plan.rel(cfg)
    if not os.path.lexists(cfg):
        return f"{rel} is missing"
    if os.path.islink(cfg) and not os.path.exists(cfg):
        return f"{rel} is a link to nothing ({os.readlink(cfg)})"
    try:
        doc = _PL.read_config(d)
    except _PL.LayoutError as e:
        why = str(e).replace(str(cfg), rel)
        cannot = f"cannot read {rel}: "
        return (f"{rel} cannot be read: {why[len(cannot):]}"
                if why.startswith(cannot) else why)
    st = doc.get("settings")
    y = st.get("year") if isinstance(st, dict) else None
    if isinstance(y, int) and not isinstance(y, bool):
        return None
    if y is None:
        return f"{rel} sets no [settings] year"
    return (f"{rel} sets [settings] year = {_PL.toml_value(y)}, "
            f"{'a string' if isinstance(y, str) else 'not a whole number'}, "
            f"not a year")


def _later_locks(d: Path, year: int) -> Tuple[List[int], Optional[str]]:
    """(the years after `year` whose lock (filed/<year>.json, a link or
    not) the project folder `d` holds, oldest first; why its filed/ cannot
    be listed, None when it can or there is none)."""
    try:
        names = [p.name for p in (d / "filed").iterdir()]
    except (FileNotFoundError, NotADirectoryError):
        return [], None
    except OSError as e:
        return [], e.strerror or str(e)
    return sorted(int(n[:-5]) for n in names
                  if n.endswith(".json") and n[:-5].isdigit()
                  and int(n[:-5]) > year), None


def _later_years(plan: Plan) -> None:
    """A later year closed (a lock in this project's filed/ or in a later
    year's project beside it, `_later_projects`; a missing lock of a
    closed year too) is a refusal: its return took its carry-in from this
    year's record, so the newest year is reopened first. So is a folder
    beside this one that may be a later year closed and cannot say (its
    taxjson.toml cannot say which year it is, or its filed/ cannot be
    listed: TJ-5088): it is named, with why; a year's readable folder
    first. A later year only started is recorded (`started`): the report
    names it."""
    root, year = plan.root, plan.year
    closed: List[Tuple[int, str, Optional[Path], Optional[str], bool]] = []
    with contextlib.suppress(OSError):
        for p in sorted((root / "filed").glob("*.json")):
            if p.stem.isdigit() and int(p.stem) > year:
                closed.append((int(p.stem), plan.rel(p), None, None, True))
    for y, d, why, locked in _later_projects(plan):
        p = d / "filed" / f"{y}.json"
        if why is None and not os.path.lexists(p):
            unlisted = _later_locks(d, year)[1]
            why = (f"{plan.rel(d / 'filed')} cannot be listed ({unlisted})"
                   if unlisted else None)
        if why is not None or os.path.lexists(p) \
                or _PL.lock_missing_frozen(d, y):
            closed.append((y, plan.rel(p), d, why or _unread(plan, d),
                           locked or os.path.lexists(p)))
        else:
            plan.started.append((y, d))
    if not closed:
        return
    y, shown, d, why, locked = max(closed, key=lambda c: (c[0], c[3] is None))
    how = (f"`tjs -C {_dir_arg(d)} unclose-year`"
           if d is not None else
           f"`tjs unclose-year` with [settings].year = {y}")
    if d is not None and why is not None:
        fix = [plan.rel(q) for q, bad in (
            (d / _PL.CONFIG, _unread(plan, d)),
            (d / "filed", _later_locks(d, year)[1])) if bad]
        _die((f"{plan.rel(d)} holds the lock of {y} ({shown}), a later "
              f"year than {year}, but {why}" if locked else
              f"{plan.rel(d)} may hold the lock of a later year than "
              f"{year}: {why}"),
             f"It may be a later year closed on what {year} carried into "
             f"it, which is reopened first. Repair {' and '.join(fix)}, "
             f"then reopen the newest year first ({how}), then {year}; if "
             f"{plan.rel(d)} is not a later year of this project, move it "
             f"out of this folder's parent. {_NOTHING}")
    _die(f"{y} is closed too ({shown}), on what {year} carried into it",
         f"Reopen the newest year first ({how}), then {year}. To amend "
         f"{year} and keep both filed, see docs/filing.md (After "
         f"assessment): `tjs close-year --force` once the amended return "
         f"is filed. {_NOTHING}")


def _commented_setting(line: str) -> Optional[Tuple[str, Any]]:
    """(key, value) of a line the freeze commented out: `# ` (as the
    freeze writes it, lib/project_layout `_commented`) before a one-line
    `inputs_dir = ...`, `tobase_map = ...` or `holdings_dir = ...`, the key bare, quoted or
    dotted from its table (`settings.inputs_dir`). None for any other
    line (`#tobase_map`, prose, another key)."""
    if not line.startswith("# "):
        return None
    try:
        doc = _PL.parse_config_text(line[2:])
    except _PL.LayoutError:
        return None
    if set(doc) == {"settings"} and isinstance(doc["settings"], dict):
        doc = doc["settings"]
    if len(doc) == 1:
        (k, v), = doc.items()
        if k in _KEYS:
            return k, v
    return None


def _thawed(text: str, live: Sequence[str], record: Optional[Dict[str, Any]]
            ) -> Tuple[str, Dict[str, Any], List[Tuple[str, str]]]:
    """taxjson.toml's `text` without the close's own lines: (the text,
    {key: value} of the settings un-commented, [(key, line)] of the
    commented lines looked at and left).

    Each freeze note is dropped. Under it at most what the close wrote is
    restored, the `# ` the freeze put before it taken off: the one
    commented `inputs_dir` / `tobase_map` line directly under the note,
    and under the first note a second one directly after it when it is
    the other key and the file has no second note (the freeze writes one
    note over two adjacent keys, else a note each). With the lock's
    `frozen` record (`record`) a line is restored only when the record
    names its key with that very value: a commented line of the person's
    own under the note is not the close's. Without a record only the one
    line directly under each note is. A key in `live` (set again by hand)
    is not restored. A line that is not restored stays as it is: nothing
    but a note is ever removed."""
    lines = text.split("\n")
    second_note = any(ln.strip() == _FROZEN_NOTE_2 for ln in lines)
    out: List[str] = []
    back: Dict[str, Any] = {}
    left: List[Tuple[str, str]] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith(_FROZEN_NOTE_PREFIX):
            out.append(lines[i])
            i += 1
            continue
        most = 1 if (record is None or second_note
                     or lines[i].strip() == _FROZEN_NOTE_2) else 2
        i += 1
        first: Optional[str] = None
        for _ in range(most):
            got = _commented_setting(lines[i]) if i < len(lines) else None
            if got is None or got[0] == first:
                break
            k, v = got
            first = first or k
            ours = record is None or (isinstance(record.get(k), str)
                                      and v == record.get(k))
            if ours and isinstance(v, str) and k not in live and k not in back:
                out.append(lines[i][2:])
                back[k] = v
            else:
                out.append(lines[i])
                left.append((k, lines[i]))
            i += 1
        # With no record, a commented line of the other key right after
        # the one restored may be the close's too (one note over two
        # adjacent keys): it stays, and is named for the person to judge.
        after = _commented_setting(lines[i]) if i < len(lines) else None
        if record is None and first and after and after[0] != first:
            left.append((after[0], lines[i]))
    return "\n".join(out), back, left


def _settings_back(plan: Plan, fz: Optional[Dict[str, Any]]
                   ) -> Tuple[Dict[str, Any], List[str]]:
    """taxjson.toml's new text (`plan.text`, None when nothing of the
    close is in it): ([settings] as it gives them, the keys whose copies
    the close froze and the year will read shared again).

    A setting the close commented out comes back from its own line
    (`_thawed`); when the lock's record names it and the line is gone, or
    is not the one the close wrote, from the record, and the report says
    which; one the file names again already stays (`by_hand`). What was
    frozen is what the record names; without a record, what a note's own
    line restored. Refused when the result does not read as asked."""
    root = plan.root
    text = _read_config_text(root / _PL.CONFIG)
    named = {k: v for k, v in (fz or {}).items()
             if k in _KEYS and isinstance(v, str) and v.strip()}
    try:
        st = _PL.parse_config_text(text).get("settings") or {}
        live = [k for k in _KEYS if st.get(k) is not None]
        new, back, left = _thawed(text, live, fz)
        plan.restored = [(k, v, NOTE) for k, v in back.items()]
        seen = {k for k, _ln in left}
        for k in _KEYS:
            if k in live or k in back or k not in named:
                continue
            new = _PL.set_key_text(new, ("settings", k), named[k],
                                   after=_PL.EXPORTS_KEY)
            plan.restored.append((k, named[k], RECORD_OTHER if k in seen
                                  else RECORD_GONE))
        st = _PL.parse_config_text(new).get("settings") or {}
        bad = [f"{k} would not read as {v!r}" for k, v, _h in plan.restored
               if st.get(k) != v]
    except _PL.LayoutError as e:
        bad = [str(e)]
    if bad:
        want = "; ".join(f'{k} = "{v}"' for k, v, _h in plan.restored)
        _die(f"taxjson.toml cannot name the shared folders again: {bad[0]}",
             f"Under [settings] set {want or 'inputs_dir / tobase_map'} by "
             f"hand and delete close-year's note, then run this again. "
             f"{_NOTHING}")
    was = set(named) if fz is not None else set(back) | seen
    plan.by_hand = [k for k in live if k in was]
    # (with a record, a commented line it does not name is the person's)
    plan.unsure = ([(k, ln) for k, ln in left if st.get(k) is None]
                   if fz is None else [])
    frozen = [k for k in _KEYS if st.get(k) is not None
              and (k in named if fz is not None else k in back)]
    if new != text:
        plan.text = new
        plan.cfg_real = _config_rewrite_target(root, _NOTHING, then="reopen")
    return st, frozen


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _against_shared(own: Path, shared: Path) -> Optional[str]:
    """How the copy `own` (read already) stands to the shared file: None
    the same bytes, else why it is kept: UNREADABLE (the shared file
    cannot be read: nothing says they are the same), DIFFERS."""
    try:
        theirs = shared.read_bytes()
    except OSError:
        return UNREADABLE
    return None if theirs == own.read_bytes() else DIFFERS


def _input_copies(plan: Plan, cfg: Dict[str, Any], st: Dict[str, Any],
                  hashes: Optional[Dict[str, Any]]) -> None:
    """The year's own inputs/ sorted into `remove` (the same bytes as the
    shared file of that name), `keep` (another file than the shared one,
    one that cannot be read, or none there: DIFFERS, UNREADABLE,
    ONLY_COPY; a crypto_sends.tt taxjson generated that is not the one in
    work/: GENERATED) and `left` (a link, or a file reached through one:
    not touched. The close copies files, so a link is the person's; moved,
    a relative one would lead nowhere, and removed, one to the shared file
    would take that file's only name). The copies are the files in the account folders (the close
    refuses an account folder that holds any) and those the lock's
    `frozen` record (`hashes`) names. `unread` counts the shared exports
    the filed year did not read (the files a run reads there,
    lib/checklist `input_files`, with no copy and not in the record)."""
    from taxjson.lib import crypto_sends as _CS
    from taxjson.lib.checklist import _ACCOUNT_SIDECARS, _skipped_input
    root = plan.root
    shared = _PL.folder_setting(root, _PL.INPUTS_KEY, st)
    if shared is None:
        return
    plan.shared = plan.rel(shared)
    own = root / _PL.INPUTS
    accounts = sorted(str(a) for a in (cfg.get("accounts") or {}))
    cands: Dict[str, Path] = {}
    for a in accounts:
        with contextlib.suppress(OSError):
            for p in sorted((own / a).rglob("*")):
                if p.is_symlink() or p.is_file():
                    cands[p.relative_to(own).as_posix()] = p
    for k in sorted(hashes or {}):
        parts = str(k).split("/")
        if parts[0] == _PL.INPUTS and len(parts) > 1 and ".." not in parts:
            p = root.joinpath(*parts)
            if p.is_symlink() or p.is_file():
                cands.setdefault("/".join(parts[1:]), p)
    if not shared.is_dir():
        _die(f"{plan.shared}/, the folder {plan.year} read its exports "
             f"from, is gone",
             ("The year's own inputs/ is the only copy: put the shared "
              "folder back (or copy inputs/ to it)" if cands else
              "Put the shared folder back") + f", then run this again. "
             f"{_NOTHING}")
    inside = root.resolve()
    for rel, p in sorted(cands.items()):
        s = shared / rel
        parts = rel.split("/")
        if parts[-1] == _PL.OLD_ELECTIONS and not os.path.lexists(s):
            # A copy frozen as manifest.json, the shared one renamed by
            # `tjs migrate`: one file.
            s = s.with_name(_PL.ELECTIONS)
        try:
            if p.is_symlink() or _through_link(own, p) \
                    or inside not in p.parent.resolve().parents \
                    or (s.exists() and os.path.samefile(p, s)):
                plan.left.append(p)
                continue
            generated = (len(parts) == 2 and parts[1].lower() == _CS.TT_NAME
                         and (_CS.read_tt(p) or "").startswith(
                             _CS.GENERATED_MARK))
            p.read_bytes()
        except (OSError, ValueError) as e:
            _die_input(f"cannot read {plan.rel(p)}: "
                       f"{getattr(e, 'strerror', None) or e}", _NOTHING)
        if generated:
            # With shared exports the run writes it in work/ again.
            if _same_bytes(p, root / "work" / _CS.GENERATED_DIR
                           / parts[0] / _CS.TT_NAME):
                plan.remove.append(p)
            else:
                plan.keep.append((p, GENERATED))
        elif not (s.is_file() or s.is_symlink()):
            plan.keep.append((p, ONLY_COPY))
        else:
            why = _against_shared(p, s)
            if why is None:
                plan.remove.append(p)
            else:
                plan.keep.append((p, why))
    for a in accounts:
        with contextlib.suppress(OSError):
            for s in sorted((shared / a).iterdir()):
                rel = f"{a}/{s.name}"
                if (s.is_file() and not _skipped_input(s)
                        and (s.suffix.lower() in (".csv", ".tt", ".toml")
                             or s.name in _ACCOUNT_SIDECARS)
                        and rel not in cands
                        and not (s.name == _PL.ELECTIONS
                                 and f"{a}/{_PL.OLD_ELECTIONS}" in cands)
                        and f"{_PL.INPUTS}/{rel}" not in (hashes or {})
                        and s.name.lower() != _CS.TT_NAME):
                    plan.unread += 1


def _through_link(own: Path, p: Path) -> bool:
    """`p`, a file below the year's inputs/ (`own`), is reached through a
    link: inputs/ itself or a folder between it and `p` is one (an
    account folder linked to a folder of the person's). The file at the
    link's end is the person's: removed, it was gone while the link
    stayed (backlog TJ-5080)."""
    d = own
    for part in p.relative_to(own).parts[:-1]:
        if d.is_symlink():
            return True
        d = d / part
    return d.is_symlink()


def _tobase_copy(plan: Plan, st: Dict[str, Any]) -> None:
    """The year's own tobase.map: removed when it is the shared file's
    bytes, else kept (DIFFERS, UNREADABLE). A link to the shared file
    itself is left. Refused when the shared file is gone (the copy is
    then the only one), and when the year's tobase.map is a link to
    another file: left beside the setting the year would have two
    tobase.map files, and moved, a relative link would lead nowhere."""
    root = plan.root
    own = root / _PL.TOBASE_MAP
    if not os.path.lexists(own):
        return
    shared = _PL.tobase_setting(root, st)
    if shared is None:
        return
    if not shared.is_file():
        _die(f"{plan.rel(shared)}, the tobase.map {plan.year} read "
             f"before it was closed, is gone",
             f"The year's own tobase.map is the only copy: `cp tobase.map "
             f"{plan.rel(shared)}`, then run this again. {_NOTHING}")
    if own.exists() and os.path.samefile(own, shared):
        plan.left.append(own)           # a link to the shared file itself
        return
    if own.is_symlink():
        _die(f"{_PL.TOBASE_MAP} is a link to {os.readlink(own)}, not the "
             f"copy close-year froze",
             f"Replace the link with the file it leads to (or remove it), "
             f"then run this again. {_NOTHING}")
    try:
        why = _against_shared(own, shared)
    except OSError as e:
        _die_input(f"cannot read {_PL.TOBASE_MAP}: {e.strerror or e}",
                   _NOTHING)
    if why is None:
        plan.remove.append(own)
    else:
        plan.keep.append((own, why))


def _holdings_copies(plan: Plan, st: Dict[str, Any]) -> None:
    """The year's own holdings/, where close-year copied the year's
    snapshots of a folder every year shares (backlog TJ-5367): a snapshot
    the same as the shared one of its name is removed, any other moved
    into the backup (SNAPSHOT: never deleted; the reopened year compares
    the shared folder's), a link or folder left; a file that is no
    snapshot (a README, a note of yours) is not the close's and stays
    (TJ-5405). The figures never read them, so a kept one is no
    divergence."""
    root = plan.root
    own = root / _PL.HOLDINGS
    try:
        shared = _PL.folder_setting(root, _PL.HOLDINGS_KEY, st)
    except _PL.LayoutError:
        return                          # (the settings' check says why)
    if shared is None or own.is_symlink() or not own.is_dir():
        return
    from taxjson.lib.holdings_dir import snapshot_files
    snaps = set(snapshot_files(own))
    for p in sorted(own.iterdir()):
        if p.is_symlink() or not p.is_file():
            plan.left.append(p)
            continue
        if p not in snaps:
            continue        # (no snapshot: never a copy of the close's)
        try:
            why = (_against_shared(p, shared / p.name)
                   if (shared / p.name).is_file() else ONLY_COPY)
        except OSError as e:
            _die_input(f"cannot read {plan.rel(p)}: {e.strerror or e}",
                       _NOTHING)
        if why is None:
            plan.remove.append(p)
        else:
            plan.keep.append((p, SNAPSHOT))


# ----------------------------------------------------------------- apply

def _apply(plan: Plan, state: WriteStep) -> None:
    """The plan carried out as one step. Every change before the record
    is a rename into the backup (the copies being removed too: they go
    once the record is written), a copy made in it or taxjson.toml's
    atomic rewrite, each known to `undo` before it is made: the renames
    back, taxjson.toml's bytes again, the files and folders made removed
    when empty (never a tree: what could not be moved back stays where
    the warning says). The record, written last, is the commit."""
    from taxjson.lib.checklist import forget_results
    root, b = plan.root, plan.backup
    record = b / RECORD
    made: List[Path] = []
    moved: List[Tuple[Path, Path]] = []
    wrote: List[Path] = []
    cfg_before: List[bytes] = []

    def undo() -> None:
        if cfg_before and plan.cfg_real is not None:
            try:
                if plan.cfg_real.read_bytes() != cfg_before[0]:
                    _write_atomic(plan.cfg_real, cfg_before[0], keep_mode=True)
            except OSError as e:
                _say("warning", f"taxjson.toml could not be put back as it "
                     f"was: {e.strerror or e}",
                     f"The file as it was is {plan.rel(b / _PL.CONFIG)}: "
                     f"copy it back by hand.", prog=f"{_PROG} unclose-year")
                wrote.clear()           # (the copy stays)
        for src, dst in reversed(moved):
            try:
                if os.path.lexists(dst) and not os.path.lexists(src):
                    os.rename(dst, src)
            except OSError as e:
                _say("warning", f"{plan.rel(src)} could not be put back: "
                     f"{e.strerror or e}",
                     f"It is {plan.rel(dst)}: move it back by hand.",
                     prog=f"{_PROG} unclose-year")
        for p in wrote:
            with contextlib.suppress(OSError):
                p.unlink()
        for d in reversed(made):
            with contextlib.suppress(OSError):
                d.rmdir()

    def finish() -> None:
        # The record is written: the copies being removed go, then the
        # folders this emptied (inputs/<account>/, inputs/, filed/<year>/).
        shutil.rmtree(b / _REMOVED, ignore_errors=True)
        seen: List[Path] = []
        for src, _dst in moved:
            for d in src.parents:
                if d == root or root not in d.parents:
                    break
                if d not in seen:
                    seen.append(d)
        for d in sorted(seen, key=lambda q: len(q.parts), reverse=True):
            with contextlib.suppress(OSError):
                d.rmdir()
        # The checklist verdicts kept while the lock was there (this
        # year's "locked; no drift", a later year's hand-off) are dropped.
        for d in [root] + [q for _y, q in plan.started]:
            if not forget_results(d):
                _say("warning", f"the checklist verdicts kept in "
                     f"{plan.rel(d / 'work')}/ could not be dropped",
                     "They were made while the year was closed: `tjs "
                     "checklist --run` there checks again.",
                     prog=f"{_PROG} unclose-year")

    def folder(d: Path) -> None:
        for q in reversed([d, *d.parents]):
            if root in q.parents and not q.exists():
                made.append(q)
                q.mkdir()

    def move(src: Path, dst: Path) -> None:
        folder(dst.parent)
        moved.append((src, dst))
        os.rename(src, dst)

    state.begin(record)
    state.undo.append(undo)             # before the first write
    state.done.append(finish)
    doing, src = "make", b
    try:
        folder(b)
        doing = "move"
        if plan.reports is not None:
            move(src := plan.reports, b / "reports")
        if plan.text is not None and plan.cfg_real is not None:
            doing, src = "rewrite", plan.cfg_real
            wrote.append(b / _PL.CONFIG)
            shutil.copy2(src, b / _PL.CONFIG)
            cfg_before.append(src.read_bytes())
            _write_atomic(src, plan.text, keep_mode=True)
            doing = "move"
        for src, _why in plan.keep:
            move(src, b / src.relative_to(root))
        for src in plan.remove:
            move(src, b / _REMOVED / src.relative_to(root))
        if plan.lock is not None:
            move(src := plan.lock, b / plan.lock.name)
        state.raise_held()              # a Ctrl-C this went on after
        doing, src = "write", record
        _write_atomic(record, json.dumps(_record(plan), indent=2,
                                         sort_keys=True) + "\n")
    except OSError as e:
        _die_input(f"cannot {doing} {plan.rel(src)}: {e.strerror or e}",
                   _NOTHING)
    state.committed = True
    state.settle(stopping=False)


def _record(plan: Plan) -> Dict[str, Any]:
    """backup/unclosed.json: what the reopening did, for the person (and
    for putting the year back by hand). Not a lock: no reader takes it
    for one."""
    from taxjson.lib import channels
    return {
        "schema_version": 1, "kind": "unclosed", "year": plan.year,
        "unclosed_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "taxjson": channels.running_build(),
        "lock": plan.lock.name if plan.lock is not None else None,
        "reports": "reports" if plan.reports is not None else None,
        "config_as_closed": _PL.CONFIG if plan.text is not None else None,
        "settings_restored": {k: v for k, v, _h in plan.restored},
        "settings_restored_from": {k: h for k, _v, h in plan.restored},
        "settings_named_by_hand": list(plan.by_hand),
        "commented_lines_left": [ln for _k, ln in plan.unsure],
        "links_left": [plan.rel(p) for p in plan.left],
        "copies_removed": [plan.rel(p) for p in plan.remove],
        "copies_kept": {w: [plan.rel(p) for p in plan.kept(w)]
                        for w in (DIFFERS, ONLY_COPY, UNREADABLE,
                                  GENERATED, SNAPSHOT)
                        if w != SNAPSHOT or plan.kept(w)},
        "shared_files_not_read_when_filed": plan.unread,
        "later_years_started": [y for y, _d in plan.started],
        "later_year_folders": [plan.rel(d) for _y, d in plan.started],
    }


def _reopened_anyway(root: Path, state: WriteStep) -> None:
    """Said when the command stops once the record is written, before the
    exception goes on: the year is reopened."""
    from taxjson.lib import out as _out
    rec = state.commit or root
    _out.warn(f"the year is reopened: {_out.relpath(rec.parent, root)}/ "
              f"holds what was filed, but the report stopped",
              prog=f"{_PROG} unclose-year",
              details=["`tjs years` shows the year open: do not run "
                       "this again for it."])


def _not_reopened(e: BaseException) -> None:
    """The command stopped before the record was written, all it moved
    put back (`WriteStep.settle`). A refusal says so itself, and an
    interrupt or a closed pipe goes on as it is; an input or output that
    cannot be read or written keeps its line and exit 2; any other error
    is one Error saying nothing was changed, then goes on as it did."""
    from taxjson.lib.cli_diag import input_error_text
    if isinstance(e, (SystemExit, KeyboardInterrupt, BrokenPipeError)):
        return
    text = input_error_text(e)
    if text is not None:
        _die_input(text, "Nothing was changed: fix that, then run this "
                         "again.")
    _say("error", "stopped before the year was reopened: nothing was "
         "changed", prog=f"{_PROG} unclose-year")


# ---------------------------------------------------------------- report

def _names(paths: Sequence[Path], plan: Plan, every: bool) -> str:
    shown = [plan.rel(p) for p in paths]
    if every or len(shown) <= 3:
        return ", ".join(shown)
    return ", ".join(shown[:2]) + f" +{len(shown) - 2} more"


def _copies_row(plan: Plan, det: bool) -> Optional[str]:
    """The `Copies:` row: the frozen copies removed and kept."""
    parts = []
    if plan.remove:
        parts.append(f"{len(plan.remove)} the same as the shared file(s): "
                     f"removed")
    for why, text in ((DIFFERS, "not the same as the shared file"),
                      (ONLY_COPY, f"not in {plan.shared or 'the shared folder'}"),
                      (UNREADABLE, "whose shared file cannot be read"),
                      (GENERATED, "generated by taxjson"),
                      (SNAPSHOT, "positions snapshot(s) not the same as "
                                 "the shared folder's, or not in it")):
        got = plan.kept(why)
        if got:
            parts.append(f"{len(got)} {text} ({_names(got, plan, det)}): "
                         f"kept in the backup")
    if plan.left:
        parts.append(f"{len(plan.left)} left where they are: a link, or "
                     f"reached through one "
                     f"({_names(plan.left, plan, det)})")
    return "; ".join(parts) or None


def _report(plan: Plan, args: argparse.Namespace, *, done: bool) -> None:
    """What was done (`done`), or with --dry-run what would be: the
    title, a row per thing changed (`from -> to`), then one `! ` line
    for each thing not to miss: the filed figures are no longer recorded,
    the year now reads other inputs than it was filed from, a later year
    was started from it. --details says each in full."""
    from taxjson.lib import out as _out
    det = _details(args)
    year, b = plan.year, plan.shown_backup(done)
    page = Doc(exact=True, width_=0, live=True)
    page.blank()
    page.line(f"REOPENED {year} — it is not filed any more" if done else
              f"DRY RUN — reopening {year} would change (nothing was "
              f"changed):")
    kv: List[Tuple[str, str]] = []
    if plan.lock is not None:
        kv.append(("Lock", f"{plan.rel(plan.lock)} -> {b}/{plan.lock.name}"
                   + (" (it cannot be read: kept as it is)"
                      if plan.lock_unread else "")))
    else:
        kv.append(("Lock", f"filed/{year}.json was gone already: the "
                           f"filed figures are not in the backup"))
    if plan.reports is not None:
        kv.append(("Reports as filed",
                   f"{plan.rel(plan.reports)}/ ({plan.report_count} "
                   f"file(s)) -> {b}/reports/"))
    how = {NOTE: "", RECORD_GONE: " (from the lock's record: its line under "
                                  "the note was gone)",
           RECORD_OTHER: " (from the lock's record: the commented line "
                         "under the note is not the one close-year wrote, "
                         "and stays)"}
    sets = [f"{k} = \"{v}\"{how[h]}" for k, v, h in plan.restored]
    if plan.text is not None:
        kv.append(("Settings", "taxjson.toml: "
                   + (f"{', '.join(sets)} set again, " if sets else "")
                   + f"close-year's note removed (the file as closed -> "
                     f"{b}/{_PL.CONFIG})"))
    if plan.by_hand:
        kv.append(("Set by hand", f"{', '.join(plan.by_hand)} already "
                                  f"named in taxjson.toml: left as it is"))
    if plan.unsure:
        kv.append(("Not restored", "; ".join(
            f"`{ln.strip()}` stays commented (nothing records that "
            f"close-year wrote it): if it did, un-comment it by hand and "
            f"move the year's own {_OWN_COPY.get(k, _PL.TOBASE_MAP)} "
            f"away" for k, ln in plan.unsure)))
    row = _copies_row(plan, det)
    if row:
        kv.append(("Frozen copies", row))
    if plan.layouts:
        kv.append(("Layouts", f"{_names(plan.layouts, plan, det)} stay(s): "
                              f"the project's own, or the copy close-year "
                              f"made of your layout (the same layout)"))
    kv.append(("Backup", f"{b}/ — what was filed; commit it with your "
                         f"records" + (f" ({RECORD} lists every file)"
                                       if det else "")))
    gi = _gitignore_hides_filed_reports(plan.root)
    if gi and plan.reports is not None:
        # An older scaffold's unanchored rule hides the backup's reports.
        kv.append(("Note", f".gitignore's `{gi}` also ignores {b}/reports/ "
                           f"— change the line to `/reports/` so the "
                           f"backup is committed whole"))
    for ln in _out.kv_lines(kv, indent="  "):
        page.line(ln)
    for text, cmd, more in _must_not_miss(plan, done):
        if det:
            page.line(_out.fill(f"! {text}. {more}"))
        else:
            page.act(text, cmd)


def _must_not_miss(plan: Plan, done: bool) -> List[Tuple[str, str, str]]:
    """(the `! ` line, its command, the rest said with --details) of what
    the reader must not miss."""
    year, b = plan.year, plan.shown_backup(done)
    if plan.lock is None:
        out = [(f"{year}'s lock was gone already: its filed figures are "
                f"recorded nowhere here", "tjs years",
                f"The backup holds the reports as filed, not the lock: "
                f"restore filed/{year}.json from git or a backup of your "
                f"own if you want the figures kept. `tjs close-year` locks "
                f"the books as they are then, which are the filed figures "
                f"only while the inputs are the same.")]
    else:
        out = [(f"{year}'s filed figures are "
                + ("no longer" if done else "then no longer")
                + " recorded outside the backup", "tjs years",
                f"`tjs check-filed`, `tjs years` and next year's carry-in "
                f"read no lock for {year} now. `tjs close-year` locks the "
                f"books as they are then, which are the filed figures only "
                f"while the inputs are the same; the lock and the reports "
                f"as filed are in {b}/.")]
    if plan.diverged:
        only = len(plan.kept(ONLY_COPY))
        other = len(plan.kept(DIFFERS, UNREADABLE))
        n = only + other
        what = ", ".join(([f"{n} kept"] if n else [])
                         + ([f"{plan.unread} new"] if plan.unread else []))
        where = plan.shared or "the shared folder"
        out.append((f"{year} now reads other inputs than it was filed from "
                    f"({what}): figures may differ", "tjs run",
                    f"It is computed from {where}/ now, which holds "
                    f"{plan.unread} file(s) the filed year never read; "
                    f"{n} file(s) of the frozen copy are kept in {b}/: "
                    f"`tjs run`, then compare reports/ with {b}/reports/."
                    + (f" {other} kept file(s) differ from the file of "
                       f"that name in {where}/ (or it cannot be read): "
                       f"compare the two; the one in {where}/ is what "
                       f"every year reads, so do not copy the kept one "
                       f"over it unless it is the right one for all of "
                       f"them." if other else "")
                    + (f" {only} kept file(s) are not in {where}/: one "
                       f"that belongs to the books goes into "
                       f"{where}/<account>/." if only else "")))
    for y, d in plan.started:
        out.append((f"{y} carries in from {year}, which has no lock now: "
                    f"its carry-in reads none",
                    f"tjs -C {_dir_arg(d)} run",
                    f"Its net capital loss, minimum tax carryover and "
                    f"foreign cash brought in from {year} were read from "
                    f"the lock: its next `tjs run`, `estimate` and "
                    f"`carryover` read none, and `tjs handoff` there has "
                    f"no record to check against."))
    return out


def register(sub: argparse._SubParsersAction) -> None:
    """This command, for the `tjs` parser."""
    p = sub.add_parser(
        "unclose-year",
        help="Reopen a closed year (its lock moved to a backup)",
        description="Take back the year's `tjs close-year`, all or "
             "nothing: the lock (filed/<year>.json) and the reports as "
             "filed move into a backup folder, "
             "filed/<year>.unclosed-<date>-<time>/, and taxjson.toml reads "
             "the shared exports, tobase.map and holdings folder again. "
             "A frozen copy the "
             "same as the shared file is removed; one that differs is "
             "kept in the backup, and the year is then computed from the "
             "shared inputs. Refused while a later year is closed.")
    p.add_argument("--year", type=_tax_year_arg, default=None,
                   help="Must match [settings].year (guard)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change; change nothing")
    p.set_defaults(func=cmd_unclose_year)

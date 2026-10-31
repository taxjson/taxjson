"""`tjs compare`: the current reports/ against a snapshot of an
earlier run.

A snapshot is a copy of reports/ with a record of what the books were
built from: the inputs fingerprint (lib/checklist.input_fingerprint,
name and SHA-256 of every file `tjs run` reads) and each taxable
account's filing aggregates (taxjson_filed.aggregates_from_gains).
Four kinds:

- `prev` (work/compare/prev/): the run before. Every full `tjs run`
  rotates the reports it is about to replace there (rotate_previous). A
  `tjs run --account` rebuilds one account's reports only: the first
  one after a full run keeps that run's reports as `prev`, and the full
  run after it leaves them there, so `prev` is always a full run's
  (backlog TJ-0254).
- a saved one (work/compare/<name>/, `tjs compare --save NAME`): a
  baseline kept until `--delete NAME`, say before a batch of exports is
  replaced.
- `filed` (filed/<year>/reports/ and the lock's `frozen` record, kept by
  `tjs close-year`): the reports as filed.
- a full one (`snapshot/<year>.<timestamp>/`, `tjs snapshot`,
  take_full): the whole project copied with its layout, so a person can
  `diff -r` it file by file or run taxjson in it, and a comparison reads
  its reports like any other.

The comparison names the account figures that moved (before, after,
change), the report files that differ (lines that only say when a file
was written are ignored) and the inputs that changed, were added or
vanished. Exit 1 when any report differs or any account figure moved.
Synthetic data only in tests.
"""
from __future__ import annotations

import difflib
import errno
import json
import math
import re
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SNAP_ROOT = "compare"           # under work/
PREV = "prev"
FILED = "filed"
VERSION = 1
SNAPSHOT_FILE = "snapshot.json"
FULL = "snapshot"               # a full snapshot's `kind`
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RESERVED = (PREV, FILED)
# reports/ mix a `tjs run --account` into the last full run's: the
# accounts it rebuilt, one per line, under work/compare/ (a leading dot
# is no snapshot name). Written by rotate_previous for a single-account
# run; removed when a full run completes (full_run_done).
PARTIAL_MARK = ".partial-run"

# A report line that says when the file was written, not what it holds
# (`Generated:  2026-...` in a .rpt, `generated_at = ...` in a holdings
# TOML, `"generated_at": ...` in a radar JSON): never a difference.
_STAMP_RE = re.compile(r'^\s*("?generated(_at)?"?\s*[:=]|Generated:)', re.I)
# A report written before the command's name became `tjs` everywhere has
# the project's name before the command: the same text, or every older snapshot and filed report
# would read as changed by the rename alone. Only the word, never a figure.
_OLD_NAME_RE = re.compile(r"(?<![\w./-])taxjson(?=[ :])")

# The per-account figures compared, in this order: (key, kind).
FIGURES: Tuple[Tuple[str, str], ...] = (
    ("realized", "money"), ("disallowed", "money"), ("income", "money"),
    ("dividend", "money"), ("st_gain", "money"), ("lt_gain", "money"),
    ("dispositions", "count"))


class CompareError(ValueError):
    """What stops a comparison (no snapshot, a bad name), in one line."""


# ------------------------------------------------------------- snapshots

def snapshots_dir(root: Path) -> Path:
    return Path(root) / "work" / SNAP_ROOT


def snap_dir(root: Path, name: str) -> Path:
    return snapshots_dir(root) / name


def _full_dir(root: Path, name: str) -> Optional[Path]:
    """The full snapshot `name` (take_full), when there is one and no
    snapshot of that name under work/, which a name looks up first."""
    from taxjson.lib.project_layout import snapshots_folder
    if (snap_dir(root, name) / SNAPSHOT_FILE).is_file():
        return None
    store = snapshots_folder(root)
    d = store / name
    if store.is_symlink() or not (d / SNAPSHOT_FILE).is_file():
        return None
    return d


def _is_path(name: str) -> bool:
    """A snapshot given by its folder (`../snapshot/2025.20261030-101000/`,
    as a shell completes it), not by its name: no name holds a slash."""
    return "/" in name or os.sep in name


def _folder_given(root: Path, given: str) -> Path:
    """The snapshot folder `given` leads to, a relative path from the
    shell's folder, else the project's. CompareError when it is not one."""
    p = Path(given).expanduser()
    tried = [p] if p.is_absolute() else [Path.cwd() / p, Path(root) / p]
    for d in tried:
        if (d / SNAPSHOT_FILE).is_file():
            return d.resolve()
    if any(d.is_dir() for d in tried):
        raise CompareError(f"{given} is not a snapshot: it has no "
                           f"{SNAPSHOT_FILE}")
    raise CompareError(f"no snapshot at {given} (`tjs compare --list` "
                       f"names them)")


def _shown_dir(root: Path, d: Path) -> str:
    """A snapshot's folder as the user knows it, from the project."""
    return Path(os.path.relpath(d, Path(root).resolve())).as_posix() + "/"


def _snapshot_label(root: Path, name: str) -> str:
    d = _full_dir(root, name)
    return (_shown_dir(root, d) if d is not None
            else f"work/{SNAP_ROOT}/{name}/")


def check_name(name: str, *, saving: bool = False) -> str:
    """A snapshot name as typed; CompareError when it cannot be one
    (a path, a reserved word when saving)."""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise CompareError(f"{name!r} is not a snapshot name (letters, "
                           f"digits, '.', '_' and '-', up to 64)")
    if saving and name in RESERVED:
        raise CompareError(f"{name!r} is reserved (`prev` is the run "
                           f"before, `filed` the reports as filed) — pick "
                           f"another name")
    return name


def _why(e: OSError) -> str:
    return e.strerror or str(e)


def report_files(reports: Path, label: str = "reports/"
                 ) -> Dict[str, bytes]:
    """{name: bytes} of the files directly in reports/ (no folders);
    {} when the folder does not exist. Any other OSError — the folder
    or one file cannot be read — is a CompareError naming it (`label`:
    the folder as the user knows it): a file that cannot be read is
    never left out as if it were not there (audit finding 84)."""
    out: Dict[str, bytes] = {}
    try:
        entries = sorted(Path(reports).iterdir())
    except OSError as e:
        if e.errno == errno.ENOENT:
            return out
        raise CompareError(f"{label} cannot be read: {_why(e)}") from None
    for p in entries:
        if p.name.startswith("."):
            continue
        try:
            if not p.is_file():
                continue
            out[p.name] = p.read_bytes()
        except OSError as e:
            raise CompareError(f"{label}{p.name} cannot be read: "
                               f"{_why(e)}") from None
    return out


def normalized(data: bytes, *, display: bool = False) -> str:
    """A report's text without the lines that only date it, the
    command's old name read as `tjs` (_OLD_NAME_RE). A byte that
    is not UTF-8 stays a character of its own (surrogateescape), so two
    damaged or binary reports that differ only there never compare as
    the same — `errors="replace"` made every such byte one U+FFFD (audit
    2026-10-24 A34). `display`: the same text with those bytes as `\\xff`
    escapes, for a diff that can be printed."""
    text = data.decode("utf-8", errors="backslashreplace" if display
                       else "surrogateescape")
    return _OLD_NAME_RE.sub("tjs", "".join(
        ln for ln in text.splitlines(keepends=True)
        if not _STAMP_RE.match(ln)))


def aggregates(root: Path, cfg: Dict[str, Any], *, warn_stale: bool = True
               ) -> Dict[str, Dict[str, Any]]:
    """Each taxable account's filing aggregates from the books in
    work/ (the figures close-year locks); {} when they cannot be read
    (the files that differ still tell). `warn_stale`: as
    report_model.resolve_gains_files."""
    from taxjson.bin.taxjson_filed import aggregates_from_gains
    from taxjson.lib.report_model import load_report_json, resolve_gains_files
    accounts = cfg.get("accounts") or {}
    year = (cfg.get("settings") or {}).get("year")
    out: Dict[str, Dict[str, Any]] = {}
    try:
        files = resolve_gains_files(Path(root) / "work",
                                    warn_stale=warn_stale)
    except Exception:                                   # noqa: BLE001
        return out
    for acct, path in sorted(files.items()):
        acfg = accounts.get(acct) if isinstance(accounts.get(acct), dict) \
            else {}
        if acfg.get("type") != "taxable":
            continue
        try:
            doc = load_report_json(Path(path))
            agg = aggregates_from_gains(
                doc, crypto=bool(acfg.get("crypto")),
                year=int(year) if isinstance(year, int) else None)
        except Exception:                               # noqa: BLE001
            continue
        out[acct] = {k: agg.get(k) for k, _kind in FIGURES
                     if agg.get(k) is not None}
    return out


def _snapshot_doc(root: Path, cfg: Dict[str, Any], files: Dict[str, bytes],
                  kind: str, warn_stale: bool = True) -> Dict[str, Any]:
    """A snapshot's record of the reports in `files`."""
    from taxjson.lib.checklist import input_fingerprint, recorded_fingerprint
    # What the reports were built from is what the last full run
    # recorded, not the inputs as they are now (an export dropped in
    # since belongs to the NEXT run's snapshot); its time is the run's.
    rec = recorded_fingerprint(root)
    if rec is not None:
        fp, taken = rec
    else:
        fp, taken = input_fingerprint(root, cfg), datetime.now()
    doc = {"version": VERSION, "kind": kind,
           "taken": taken.isoformat(timespec="seconds"),
           "year": (cfg.get("settings") or {}).get("year"),
           "files": fp,
           "accounts": aggregates(root, cfg, warn_stale=warn_stale),
           "reports": sorted(files)}
    partial = partial_accounts(root)
    if partial:
        # Reports a `run --account` partly rebuilt (`compare --save`
        # then): said, never passed off as one run's (backlog TJ-0254).
        doc["partial_accounts"] = partial
    return doc


def take(root: Path, cfg: Dict[str, Any], dest: Path, kind: str,
         warn_stale: bool = True) -> Optional[Dict[str, Any]]:
    """Snapshot reports/ into `dest` (replaced): dest/reports/<files>
    and dest/snapshot.json. None (nothing written, an old snapshot
    there kept) when reports/ holds no file; CompareError (nothing
    written) when reports/ or a file in it cannot be read."""
    root = Path(root)
    files = report_files(root / "reports")
    if not files:
        return None
    doc = _snapshot_doc(root, cfg, files, kind, warn_stale)
    from taxjson.lib.safe_write import write_atomic
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Staged in a folder of this call's own, named outside the snapshot
    # namespace (a leading dot is no snapshot name, NAME_RE): the fixed
    # `<name>.part` was the user's own snapshot of that name and was
    # removed to make room (audit 2026-10-24 A08).
    stage = Path(tempfile.mkdtemp(prefix=_HIDDEN + dest.name + ".",
                                  suffix=".part", dir=str(dest.parent)))
    try:
        (stage / "reports").mkdir()
        for name, data in files.items():
            write_atomic(stage / "reports" / name, data)
        write_atomic(stage / SNAPSHOT_FILE,
                     json.dumps(doc, indent=1, sort_keys=True) + "\n")
        _publish(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return doc


_HIDDEN = "."       # a staging or holding folder's name starts with it


def _publish(stage: Path, dest: Path) -> None:
    """Rename the finished `stage` to `dest`. A snapshot already at
    `dest` is moved aside first, into a holding folder of its own
    beside it, and removed once the new one is in place; when the
    rename fails it is moved back, so the name never goes missing — it
    used to be deleted before the rename (audit 2026-10-24 A41).
    CompareError, naming where the earlier snapshot is, when the
    rename fails."""
    where = f"work/{SNAP_ROOT}/{dest.name}/"
    aside = None
    if dest.exists() or dest.is_symlink():
        hold = Path(tempfile.mkdtemp(prefix=_HIDDEN + dest.name + ".",
                                     suffix=".old", dir=str(dest.parent)))
        aside = hold / dest.name
        try:
            dest.rename(aside)
        except OSError as e:
            shutil.rmtree(hold, ignore_errors=True)
            raise CompareError(f"{where} cannot be replaced ({_why(e)}); "
                               f"the earlier snapshot is kept") from None
    try:
        stage.rename(dest)
    except OSError as e:
        if aside is None:
            raise CompareError(f"{where} cannot be written: "
                               f"{_why(e)}") from None
        try:
            aside.rename(dest)
        except OSError as e2:
            raise CompareError(
                f"{where} cannot be replaced ({_why(e)}), and the earlier "
                f"snapshot could not be moved back ({_why(e2)}): it is at "
                f"work/{SNAP_ROOT}/{aside.parent.name}/{aside.name}/ — move "
                f"it to {where}") from None
        shutil.rmtree(aside.parent, ignore_errors=True)
        raise CompareError(f"{where} cannot be replaced ({_why(e)}); "
                           f"the earlier snapshot is kept") from None
    if aside is not None:
        shutil.rmtree(aside.parent, ignore_errors=True)


# Never copied by take_full: a repository, what a command or Python keeps
# for itself, the copy `tjs redact` makes to share.
_FULL_LEFT_OUT = (".git", "__pycache__", "inputs_redact")


def _copy_tree(src: Path, dst: Path, store: Path) -> None:
    """`src` copied to `dst`, its files' times and modes kept and its links
    kept as links, so the copy is what was there and nothing outside the
    project is read through a planted one. work/compare/ (the smaller
    snapshots) and the snapshots folder itself stay out."""
    def left_out(folder: str, names: List[str]) -> List[str]:
        here = Path(folder)
        return [n for n in names
                if n in _FULL_LEFT_OUT
                or (n.startswith(".") and n.endswith(".lock"))
                or (n == SNAP_ROOT and here.name == "work")
                or (here / n).absolute() == store]
    shutil.copytree(src, dst, symlinks=True, ignore=left_out,
                    dirs_exist_ok=True)


def _full_sources(root: Path) -> List[Tuple[Path, Path]]:
    """(what, where in the snapshot) for a full snapshot, every path
    relative to the snapshot's top. A single project is its own folder.
    A year folder also takes what it reads beside it, as far as its
    settings name it (the shared folders, the shared tobase.map) and the
    other years' locks; never the whole folder holding it, which may
    hold anything (`~/2024`)."""
    from taxjson.lib import project_layout as PL
    if not PL.years_layout(root):
        return [(root, Path("."))]
    out = [(root, Path(root.name))]
    top = root.parent
    named = [PL.inputs_dir(root), PL.holdings_folder(root),
             PL.exports_folder(root), PL.tobase_map_path(root)]
    for p in named:
        if p is None or not p.exists():
            continue
        real = p.resolve()
        try:
            real.relative_to(root)      # already in the year's own copy
            continue
        except ValueError:
            pass
        try:
            out.append((real, real.relative_to(top)))
        except ValueError:
            continue
    for y in sorted(PL._year_folders_beside(root)):     # noqa: SLF001
        if y != root and (y / "filed").is_dir():
            out.append((y / "filed", Path(y.name) / "filed"))
    return out


def _links_leaving(stage: Path) -> List[str]:
    """The links in the finished copy that lead out of it."""
    out = []
    real = stage.resolve()
    for folder, dirs, names in os.walk(stage):
        for n in dirs + names:
            p = Path(folder) / n
            if p.is_symlink():
                try:
                    p.resolve().relative_to(real)
                except (OSError, ValueError):
                    out.append(p.relative_to(stage).as_posix())
    return sorted(out)


def take_full(root: Path, cfg: Dict[str, Any],
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """`tjs snapshot`: the project copied to
    snapshot/<year>.<timestamp>/, laid out as the project is (a year
    folder's copy is under its own name, beside the shared folders it
    reads, so `../inputs` still leads to them), with snapshot.json at the
    top; `project` in it names the folder of the copy holding reports/
    ("" for a single project). Never replaces a snapshot. CompareError
    (nothing left behind) when it cannot be made."""
    from taxjson.lib.channels import running_build
    from taxjson.lib.project_layout import LayoutError, snapshots_folder, years_layout
    from taxjson.lib.safe_write import write_atomic
    root = Path(root).resolve()
    year = (cfg.get("settings") or {}).get("year")
    if not isinstance(year, int):
        raise CompareError("[settings] year is required in taxjson.toml: a "
                           "snapshot is named for its year")
    files = report_files(root / "reports")
    if not files:
        raise CompareError("reports/ holds no file to snapshot — run "
                           "`tjs run` first")
    store = snapshots_folder(root)
    shown = _shown_dir(root, store)
    if store.is_symlink():
        raise CompareError(f"{shown.rstrip('/')} is a link: a snapshot is "
                           f"not written through one — make it a folder")
    for src, _rel in _full_sources(root):
        if src.resolve() == store.resolve():
            raise CompareError(f"{shown} is a folder this project reads: "
                               f"a snapshot needs that name for itself — "
                               f"rename the folder and its setting")
    doc = _snapshot_doc(root, cfg, files, FULL)
    doc["project"] = root.name if years_layout(root) else ""
    doc["taxjson"] = running_build()
    stage: Optional[Path] = None
    try:
        store.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=_HIDDEN + str(year) + ".",
                                      suffix=".part", dir=str(store)))
        for src, rel in _full_sources(root):
            dst = stage / rel
            if src.is_dir():
                _copy_tree(src, dst, store)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        write_atomic(stage / SNAPSHOT_FILE,
                     json.dumps(doc, indent=1, sort_keys=True) + "\n")
        leaving = _links_leaving(stage)
        size = count = 0
        for folder, _dirs, names in os.walk(stage):
            for n in names:
                p = Path(folder) / n
                if not p.is_symlink():
                    count += 1
                    size += p.stat().st_size
        base = f"{year}.{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
        name, n = base, 1
        while True:
            # A rename onto a folder holding files fails: two snapshots
            # in one second never share a name.
            if not (store / name).exists():
                try:
                    stage.rename(store / name)
                    break
                except OSError as e:
                    if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                        raise
            n += 1
            name = f"{base}-{n}"
    except (OSError, shutil.Error, LayoutError) as e:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        why = (e.args[0][0][2] if isinstance(e, shutil.Error) and e.args
               and isinstance(e.args[0], list) and e.args[0]
               else _why(e) if isinstance(e, OSError) else str(e))
        raise CompareError(f"the snapshot cannot be made in {shown}: "
                           f"{why}") from None
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"name": name, "folder": _shown_dir(root, store / name),
            "reports_folder": _shown_dir(
                root, store / name / doc["project"] / "reports"),
            "reports": len(files), "files": count, "bytes": size,
            "taken": doc["taken"], "links_leaving": leaving,
            "partial_accounts": doc.get("partial_accounts") or []}


def partial_accounts(root: Path) -> List[str]:
    """The accounts a `tjs run --account` rebuilt since the last
    full run (PARTIAL_MARK); [] when reports/ are one full run's."""
    try:
        text = (snapshots_dir(root) / PARTIAL_MARK).read_text(
            encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    return sorted({ln.strip() for ln in text.splitlines() if ln.strip()})


def rotate_previous(root: Path, cfg: Dict[str, Any],
                    account: Optional[str] = None) -> bool:
    """Called by `tjs run` before it writes: the reports a full run
    is about to replace become the `prev` snapshot. A single-account
    run (`account`) replaces part of them: the first one after a full
    run keeps that run's reports as `prev` and records the account
    (PARTIAL_MARK); a full run after it leaves `prev` alone, the reports
    it replaces being partial. `prev` recorded the mixed reports as
    "the run before" (backlog TJ-0254). True when `prev` holds the run
    before; CompareError naming the path when reports/ cannot be read
    (the run warns that the previous reports were not kept). A wash
    file a `run --account` left stale is not warned about: this run
    rebuilds it, and the warnings said `tjs run` to the run doing
    it (backlog TJ-0154)."""
    root = Path(root)
    partial = partial_accounts(root)
    kept = True
    if not partial:
        kept = take(root, cfg, snap_dir(root, PREV), "previous run",
                    warn_stale=False) is not None
    if account:
        from taxjson.lib.safe_write import write_atomic
        snapshots_dir(root).mkdir(parents=True, exist_ok=True)
        write_atomic(snapshots_dir(root) / PARTIAL_MARK,
                     "\n".join(sorted(set(partial) | {str(account)}))
                     + "\n")
    return kept


def full_run_done(root: Path) -> None:
    """A full `tjs run` completed: reports/ are one run's again."""
    try:
        (snapshots_dir(root) / PARTIAL_MARK).unlink()
    except OSError:
        pass


def save(root: Path, cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    name = check_name(name, saving=True)
    doc = take(Path(root), cfg, snap_dir(root, name), "saved")
    if doc is None:
        raise CompareError("reports/ holds no file to save — run "
                           "`tjs run` first")
    return doc


def delete(root: Path, name: str) -> None:
    name = check_name(name)
    if name == FILED:
        raise CompareError("the reports as filed are kept by `tjs "
                           "close-year` with the lock; they are not a "
                           "saved snapshot")
    d = snap_dir(root, name)
    if not d.is_dir():
        d = _full_dir(root, name)
    if d is None or not d.is_dir():
        raise CompareError(f"no snapshot named {name!r} (`tjs compare "
                           f"--list` names them)")
    shutil.rmtree(d)


def list_snapshots(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every snapshot the project has: name, kind, taken, how many
    report files; `prev` first, `filed` last."""
    out: List[Dict[str, Any]] = []
    d = snapshots_dir(root)
    names: List[str] = []
    if d.is_dir():
        # Only snapshot names: a staging or holding folder `take` is
        # using (or a crash left) starts with a dot.
        names = sorted(p.name for p in d.iterdir()
                       if NAME_RE.match(p.name) and p.is_dir()
                       and (p / SNAPSHOT_FILE).is_file())
    names.sort(key=lambda n: (n != PREV, n))
    # The full snapshots of this year: a folder every year shares.
    from taxjson.lib.project_layout import snapshots_folder
    store = snapshots_folder(root)
    if store.is_dir() and not store.is_symlink():
        year = (cfg.get("settings") or {}).get("year")
        names += sorted(p.name for p in store.iterdir()
                        if NAME_RE.match(p.name)
                        and p.name.startswith(f"{year}.")
                        and p.name not in names
                        and (p / SNAPSHOT_FILE).is_file())
    for n in names:
        try:
            doc, _rep = load(root, cfg, n)
        except CompareError:
            continue
        out.append({"name": n, "kind": doc.get("kind") or "saved",
                    "taken": doc.get("taken") or "",
                    "reports": len(doc.get("reports") or [])})
    try:
        doc, _rep = load(root, cfg, FILED)
    except CompareError:
        pass
    else:
        out.append({"name": FILED, "kind": "filed",
                    "taken": doc.get("taken") or "",
                    "reports": len(doc.get("reports") or [])})
    return out


def filed_reports_dir(root: Path, year: Any) -> Path:
    """Where close-year keeps the reports as filed."""
    return Path(root) / "filed" / str(year) / "reports"


def load(root: Path, cfg: Dict[str, Any], name: str
         ) -> Tuple[Dict[str, Any], Path]:
    """(snapshot document, its reports folder). CompareError when
    there is none of that name."""
    root = Path(root)
    if _is_path(name):
        d = _folder_given(root, name)
        return _read_snapshot(root, cfg, d, d.name, _shown_dir(root, d))
    name = check_name(name)
    if name == FILED:
        year = (cfg.get("settings") or {}).get("year")
        lock = root / "filed" / f"{year}.json"
        rep = filed_reports_dir(root, year)
        from taxjson.lib.project_layout import (lock_missing_frozen,
                                                lock_missing_text)
        if lock_missing_frozen(root, year):
            # A frozen year whose lock was deleted: never "not closed"
            # (backlog TJ-0263).
            raise CompareError(lock_missing_text(year))
        if not lock.is_file():
            raise CompareError(f"{year} is not closed (no filed/{year}.json)"
                               f" — `tjs close-year` keeps the reports "
                               f"as filed")
        from taxjson.bin.taxjson_filed import LockProblem, read_lock
        try:
            # The one check every reader of a lock applies: a damaged
            # lock, or one closed under the other country's law, is not
            # the reports as filed (backlog TJ-0069).
            snap = read_lock(lock, cfg.get("settings") or {},
                             label=f"filed/{year}.json")
        except LockProblem as e:
            raise CompareError(str(e))
        except ValueError as e:         # the project's own country
            raise CompareError(f"filed/{year}.json cannot be checked: {e}")
        frozen = snap.get("frozen") if isinstance(snap, dict) else None
        if not isinstance(frozen, dict) or not rep.is_dir():
            raise CompareError(f"filed/{year}.json was closed before "
                               f"close-year kept the reports as filed — "
                               f"`tjs close-year --force` records them")
        doc = {"version": VERSION, "kind": "filed",
               "taken": snap.get("closed_at") or "", "year": year,
               "files": frozen.get("inputs") or {},
               "accounts": {a: {k: v.get(k) for k, _kind in FIGURES
                                if v.get(k) is not None}
                            for a, v in (snap.get("accounts") or {}).items()
                            if isinstance(v, dict)},
               "reports": sorted(report_files(
                   rep, f"filed/{year}/reports/"))}
        return doc, rep
    d = _full_dir(root, name) or snap_dir(root, name)
    p = d / SNAPSHOT_FILE
    if not p.is_file():
        if name == PREV:
            raise CompareError("no previous run kept yet — the next full "
                               "`tjs run` keeps the reports it "
                               "replaces (work/compare/prev/)")
        raise CompareError(f"no snapshot named {name!r} (`tjs compare "
                           f"--list` names them)")
    return _read_snapshot(root, cfg, d, name, _snapshot_label(root, name))


def _read_snapshot(root: Path, cfg: Dict[str, Any], d: Path, name: str,
                   label: str) -> Tuple[Dict[str, Any], Path]:
    """(the record in folder `d`, its reports folder), checked; `label`:
    the folder as shown."""
    try:
        doc = json.loads((d / SNAPSHOT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CompareError(f"{label}{SNAPSHOT_FILE} cannot be read: {e}")
    _check_snapshot(doc, name, label)
    if d.resolve().parent != snapshots_dir(root).resolve():
        year = (cfg.get("settings") or {}).get("year")
        if doc.get("year") != year:
            # Year folders share snapshot/: another year's reports are
            # not a run before this year's.
            raise CompareError(f"{label} is a snapshot of {doc.get('year')}, "
                               f"this project is {year} — compare it from "
                               f"that year's folder")
        return doc, d / str(doc.get("project") or "") / "reports"
    return doc, d / "reports"


def _finite(v: Any) -> bool:
    """A real, finite number (not a boolean, not text)."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _what(v: Any) -> str:
    return ("a table" if isinstance(v, dict) else
            "NaN" if isinstance(v, float) and math.isnan(v) else
            "an infinite number" if isinstance(v, float)
            and math.isinf(v) else
            "a list" if isinstance(v, list) else
            "text" if isinstance(v, str) else
            "null" if v is None else
            "a boolean" if isinstance(v, bool) else
            "a number" if isinstance(v, (int, float)) else
            type(v).__name__)


def _check_snapshot(doc: Any, name: str, label: Optional[str] = None) -> None:
    """CompareError naming the field when `doc` is not the shape
    `compare` and `list_snapshots` read: `files` a table, `accounts`
    (when present) a table of tables whose figures (FIGURES) are
    finite numbers, `reports` a list, `kind` and `taken` text. A
    snapshot whose `accounts` was a list passed and the comparison died
    on it (audit 2026-10-24 A48); a NaN figure compared as unchanged
    with any amount (backlog TJ-0567). `label`: its folder as shown."""
    label = label or f"work/{SNAP_ROOT}/{name}/"

    def bad(field: str, found: Any, want: str) -> CompareError:
        return CompareError(
            f"{label}{SNAPSHOT_FILE} is not a snapshot "
            f"({field} is {_what(found)}, not {want}) — `tjs compare "
            f"--delete {name}` removes it")
    if not isinstance(doc, dict):
        raise bad("the document", doc, "a table")
    if not isinstance(doc.get("files"), dict):
        raise bad("files", doc.get("files"), "a table of inputs")
    acc = doc.get("accounts")
    if acc is not None:
        if not isinstance(acc, dict):
            raise bad("accounts", acc, "a table of accounts")
        for a, v in acc.items():
            if not isinstance(v, dict):
                raise bad(f"accounts.{a}", v, "a table of figures")
            for k, _kind in FIGURES:
                f = v.get(k)
                if f is not None and not _finite(f):
                    raise bad(f"accounts.{a}.{k}", f, "a finite number")
    rep = doc.get("reports")
    if rep is not None and not isinstance(rep, list):
        raise bad("reports", rep, "a list of file names")
    for k in ("kind", "taken"):
        if doc.get(k) is not None and not isinstance(doc.get(k), str):
            raise bad(k, doc.get(k), "text")
    # A full snapshot's folder of reports, one name inside it (never a
    # path a damaged record could lead elsewhere with).
    project = doc.get("project")
    if project is not None and (not isinstance(project, str)
                                or project in (".", "..")
                                or "/" in project or "\\" in project):
        raise bad("project", project, "the name of a folder in it")


# ------------------------------------------------------------ comparison

def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _differs(a: Any, b: Any, kind: str) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is None or fb is None:
        return a != b
    if not (math.isfinite(fa) and math.isfinite(fb)):
        # abs(NaN - x) > tolerance is False: a NaN was "unchanged"
        # (backlog TJ-0567).
        return not (fa == fb)
    return abs(fa - fb) > (0.005 if kind == "money" else 0.5)


def compare(root: Path, cfg: Dict[str, Any], name: str = PREV, *,
            details: bool = False) -> Dict[str, Any]:
    """The comparison document (CompareError when the snapshot is
    missing): what moved per account, which report files differ, which
    inputs changed, and with `details` a unified diff per report."""
    from taxjson.lib.checklist import (fingerprint_delta, frozen_files,
                                       input_fingerprint,
                                       unrecorded_generated)
    root = Path(root)
    doc, rep = load(root, cfg, name)
    if _is_path(name):
        # Shown by its name, however the folder was reached.
        name = Path(name.rstrip("/" + os.sep)).name or name
    before = report_files(rep, f"filed/{doc.get('year')}/reports/"
                          if name == FILED else
                          _shown_dir(root, rep))
    after = report_files(root / "reports")
    changed, same = [], []
    for n in sorted(set(before) & set(after)):
        if normalized(before[n]) != normalized(after[n]):
            changed.append(n)
        else:
            same.append(n)
    reports = {"changed": changed,
               "added": sorted(set(after) - set(before)),
               "removed": sorted(set(before) - set(after)),
               "same": len(same)}
    old_fp = doc.get("files") or {}
    new_fp = input_fingerprint(root, cfg)
    if name == FILED:
        # The frozen record holds the files only (the year's own lock
        # is no input it was filed from).
        old_fp, new_fp = frozen_files(old_fp), frozen_files(new_fp)
        # A generated crypto_sends.tt an older freeze did not record
        # (backlog TJ-0089).
        new_fp = unrecorded_generated(root, old_fp, new_fp)
    _ch, _ad, _rm = fingerprint_delta(old_fp, new_fp)
    inputs = {"changed": _ch, "added": _ad, "removed": _rm,
              "same": len(old_fp) - len(_ch) - len(_rm)}
    old_acc = doc.get("accounts") or {}
    new_acc = aggregates(root, cfg)
    rows: List[Dict[str, Any]] = []
    unchanged: List[str] = []
    for a in sorted(set(old_acc) | set(new_acc)):
        o, n = old_acc.get(a), new_acc.get(a)
        if o is None or n is None:
            rows.append({"account": a, "figure": "(books)",
                         "before": "none" if o is None else "present",
                         "after": "none" if n is None else "present",
                         "kind": "text"})
            continue
        moved = False
        for k, kind in FIGURES:
            if k not in o and k not in n:
                continue
            if _differs(o.get(k), n.get(k), kind):
                moved = True
                rows.append({"account": a, "figure": k,
                             "before": o.get(k), "after": n.get(k),
                             "kind": kind})
        if not moved:
            unchanged.append(a)
    # An account figure that moved is a difference even when every
    # report file matches (audit finding 66: the table was hidden and
    # the exit was 0).
    _pb = doc.get("partial_accounts")
    out = {"against": name, "kind": doc.get("kind") or "saved",
           "taken": doc.get("taken") or "",
           "different": bool(changed or reports["added"]
                             or reports["removed"] or rows),
           "accounts": rows, "unchanged_accounts": unchanged,
           "reports": reports, "inputs": inputs,
           # A `run --account` since the last full run: in reports/ now,
           # or in the snapshot (backlog TJ-0254).
           "partial_now": partial_accounts(root),
           "partial_before": ([str(a) for a in _pb]
                              if isinstance(_pb, list) else [])}
    # The taxjson a full snapshot was made with, when another one is
    # running: an upgrade moves figures without any input changing.
    from taxjson.lib.channels import build_label, running_build
    _then = build_label(doc.get("taxjson"))
    if _then and _then != build_label(running_build()):
        out["taxjson_before"] = _then
    if details:
        diffs = {}
        for n in changed:
            diffs[n] = "".join(difflib.unified_diff(
                normalized(before[n], display=True).splitlines(keepends=True),
                normalized(after[n], display=True).splitlines(keepends=True),
                fromfile=f"{name}/{n}", tofile=f"reports/{n}", n=1))
        out["diffs"] = diffs
    return out


# ---------------------------------------------------------------- text

def _against(doc: Dict[str, Any]) -> str:
    kind, name = doc.get("kind"), doc.get("against")
    what = ("the previous run" if name == PREV else
            "the reports as filed" if name == FILED else
            f"snapshot {name}")
    when = str(doc.get("taken") or "").replace("T", " ")[:16]
    return what + (f" ({when})" if when else "")


def _money(v: Any) -> str:
    f = _num(v)
    return f"{f:,.2f}" if f is not None else str(v)


def _count(v: Any) -> str:
    f = _num(v)
    return f"{int(round(f))}" if f is not None else str(v)


def _cell(v: Any, kind: str) -> str:
    return (_money(v) if kind == "money" else _count(v) if kind == "count"
            else str(v))


def _change(r: Dict[str, Any]) -> str:
    a, b = _num(r.get("before")), _num(r.get("after"))
    if a is None or b is None:
        return "-"
    d = b - a
    return (f"{d:+,.2f}" if r.get("kind") == "money"
            else f"{round(d):+d}")


def _names(items: List[str], limit: int = 6) -> str:
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" +{len(items) - limit} more"


def render(doc: Dict[str, Any], *, details: bool = False) -> List[str]:
    """The default view: one title line, the figures that moved as a
    table, then the aligned `label:  value` lines; --details adds a
    unified diff per changed report."""
    from taxjson.lib import out as _out
    rep, inp = doc["reports"], doc["inputs"]
    lines: List[str] = []
    n_now = rep["same"] + len(rep["changed"]) + len(rep["added"])
    files_match = not (rep["changed"] or rep["added"] or rep["removed"])
    if not doc["different"]:
        lines.append(f"No difference: {n_now} report file(s) match "
                     f"{_against(doc)}.")
    else:
        if files_match:
            lines.append(f"COMPARED reports/ with {_against(doc)}: the "
                         f"{n_now} report file(s) match, but "
                         f"{len(doc['accounts'])} account figure(s) moved")
        else:
            lines.append(f"COMPARED reports/ with {_against(doc)}")
        lines.append("")
        if doc["accounts"]:
            body = [[r["account"], r["figure"],
                     _cell(r["before"], r["kind"]),
                     _cell(r["after"], r["kind"]), _change(r)]
                    for r in doc["accounts"]]
            lines += _out.fit_table(
                ["ACCOUNT", "FIGURE", "BEFORE", "AFTER", "CHANGE"], body,
                aligns=["<", "<", ">", ">", ">"], per_record=False)
            lines.append("")
    kv: List[Tuple[str, str]] = []
    if doc["different"] and doc["unchanged_accounts"]:
        kv.append(("Unchanged", ", ".join(doc["unchanged_accounts"])))
    parts = []
    if rep["changed"]:
        parts.append(f"{len(rep['changed'])} of {n_now} changed: "
                     f"{_names(rep['changed'])}")
    if rep["added"]:
        parts.append(f"{len(rep['added'])} added: {_names(rep['added'])}")
    if rep["removed"]:
        parts.append(f"{len(rep['removed'])} removed: "
                     f"{_names(rep['removed'])}")
    if parts:
        kv.append(("Reports", "; ".join(parts)))
    parts = []
    if inp["changed"]:
        parts.append(f"{len(inp['changed'])} changed: "
                     f"{_names(inp['changed'])}")
    if inp["added"]:
        parts.append(f"{len(inp['added'])} added: {_names(inp['added'])}")
    if inp["removed"]:
        parts.append(f"{len(inp['removed'])} removed: "
                     f"{_names(inp['removed'])}")
    kv.append(("Inputs", "; ".join(parts) if parts else
               f"unchanged ({inp['same']} files)"))
    if doc.get("taxjson_before"):
        kv.append(("Built with", f"{doc['taxjson_before']} then; another "
                   f"taxjson now"))
    for _who, _accts in (("reports/", doc.get("partial_now") or []),
                         (_against(doc), doc.get("partial_before") or [])):
        if _accts:
            kv.append(("Partial", f"{_who} hold a `tjs run --account "
                       f"{', '.join(_accts)}` over the last full run's: "
                       f"the other accounts' and the combined reports are "
                       f"that full run's"))
    lines += _out.kv_lines(kv)
    if details and doc.get("diffs"):
        for text in doc["diffs"].values():
            lines.append("")
            lines += text.rstrip("\n").split("\n")
    elif doc["different"] and rep["changed"] and not details:
        lines.append("")
        lines.append("More: the line-by-line diff of each report: "
                     "tjs compare --details")
    return lines


def render_list(items: List[Dict[str, Any]]) -> List[str]:
    from taxjson.lib import out as _out
    if not items:
        return ["No snapshot yet: the next full `tjs run` keeps the "
                "run before as `prev`; `tjs compare --save NAME` keeps "
                "one by name; `tjs snapshot` copies the whole project."]
    body = [[i["name"], i["kind"],
             str(i["taken"]).replace("T", " ")[:16], str(i["reports"])]
            for i in items]
    return _out.fit_table(["NAME", "KIND", "TAKEN", "REPORTS"], body,
                          aligns=["<", "<", "<", ">"], per_record=False)

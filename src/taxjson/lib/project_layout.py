"""Where a project's files are: one folder of exports for every year.

A project is a folder holding taxjson.toml (the tax year, the accounts,
the settings), ticker.map, work/, reports/,
filed/ and checklist.json. Its broker exports are in inputs/<account>/
inside it — or, with `[settings] inputs_dir`, in a folder shared by the
projects of every year:

    taxes/
      inputs/<account>/        every year's exports, .tt and manifests
      holdings/                every year's broker positions snapshots
      exports/                 the newest year's positions and radar
      2024/                    a complete project for 2024
        taxjson.toml           year = 2024, inputs_dir = "../inputs",
                               holdings_dir = "../holdings"
        ticker.map             its own map (always wins over tobase.map)
        inputs/slips/          2024's slips (its own inputs/ holds only them)
        filed/ work/ reports/ checklist.json
      2025/ ...
      tobase.map               Canada: the interlisted pairs, one file every
                               year reads (`tobase_map = "../tobase.map"`)

Each year folder is a complete project: its own taxjson.toml and
ticker.map, read exactly as a single-folder project's; only the exports
are shared (`inputs_dir`, and in Canada `tobase_map`: the interlisted
master's pairs, the same for every year), and `exports_dir` names where
the newest year's run writes the files other tools read. A folder or
file a setting names must be inside the folder that holds the project
(its parent): a path that leaves it, by `..` or a symlink, is refused.
A year folder that names a shared tobase.map and still holds a
tobase.map of its own is refused, naming both (`tjs migrate` at the
top makes the per-year copies one shared file). `holdings_dir`
(default holdings/) holds the year's positions snapshots
(lib/holdings_dir); `tjs init`'s year folders share one beside them
(`"../holdings"`: each year compares the snapshots of its own dates,
and close-year copies those into the year's holdings/, as it copies
the exports). A year folder (named YYYY) of a shared-exports project
must hold that year's project.

Every path to an input goes through here (`inputs_dir`,
`ticker_map_path`, `tobase_map_path`, `data_file`, `slips_dir`,
`holdings_folder`, `exports_folder`); a project without these settings reads exactly as
before.
"""
from __future__ import annotations

import collections.abc as _cabc

import datetime as _dt
import os
import re
import stat
from pathlib import Path
from typing import (Any, Dict, List, NamedTuple, Optional, Sequence,
                    Tuple, Union)

from taxjson.project.settings import Settings, as_settings
from taxjson.project.toml_io import parse_toml as _parse_toml, read_toml

CONFIG = "taxjson.toml"
TICKER_MAP = "ticker.map"
TOBASE_MAP = "tobase.map"
INPUTS = "inputs"
SLIPS = "slips"
HOLDINGS = "holdings"
EXPORTS = "exports"
SNAPSHOTS = "snapshot"
# [settings] keys naming folders (relative to the project).
INPUTS_KEY = "inputs_dir"
HOLDINGS_KEY = "holdings_dir"
EXPORTS_KEY = "exports_dir"
FOLDER_KEYS = (INPUTS_KEY, HOLDINGS_KEY, EXPORTS_KEY)
# [settings] key naming the tobase.map every year shares (Canada): a
# file at the folder holding the year folders.
TOBASE_KEY = "tobase_map"
SHARED_TOBASE = f"../{TOBASE_MAP}"
# The positions snapshots every year shares, beside the year folders
# (`tjs init`'s shared layout; lib/holdings_dir).
SHARED_HOLDINGS = f"../{HOLDINGS}"
_KEY_EXAMPLE = {INPUTS_KEY: INPUTS, HOLDINGS_KEY: HOLDINGS,
                EXPORTS_KEY: EXPORTS}
YEAR_DIR_RE = re.compile(r"^\d{4}$")


class LayoutError(ValueError):
    """A folder setting that cannot be used (not a path, or leaving the
    folder that holds the project), or a taxjson.toml that cannot be
    read."""


def _stamp(p: Path) -> Optional[Tuple[int, int]]:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _resolved(root) -> Path:
    p = Path(root)
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return p.absolute()


def _read_doc(path: Path) -> Dict[str, Any]:
    """The parsed TOML at `path` (a UTF-8 BOM dropped); LayoutError
    naming the file when it cannot be read or parsed."""
    try:
        doc = read_toml(path)
    except OSError as e:
        raise LayoutError(f"cannot read {path}: {e.strerror or e}") from None
    except Exception as e:                              # noqa: BLE001
        raise LayoutError(f"{path} is not valid TOML: {e}") from None
    return doc if isinstance(doc, dict) else {}


_SETTINGS_CACHE: Dict[Tuple[str, Optional[Tuple[int, int]]],
                      Settings] = {}


def _settings(root: Path) -> Settings:
    """The project's [settings] ({} without a readable taxjson.toml)."""
    cfg = Path(root) / CONFIG
    key = (str(cfg), _stamp(cfg))
    if key not in _SETTINGS_CACHE:
        st = None
        if key[1] is not None and cfg.is_file():
            try:
                st = _read_doc(cfg).get("settings")
            except LayoutError:
                st = None
        if len(_SETTINGS_CACHE) > 256:
            _SETTINGS_CACHE.clear()
        _SETTINGS_CACHE[key] = as_settings(st if isinstance(st, dict) else {})
    return _SETTINGS_CACHE[key]


def folder_setting(root, key: str, settings: Any
                   = None) -> Optional[Path]:
    """The folder `[settings] key` names, resolved against the project
    (None when unset). LayoutError when it is not a path string, or
    resolves outside the folder holding the project (its parent) or to
    that folder or the project itself: the shared folders of a
    multi-year project sit beside the year folders, never further out."""
    root = _resolved(root)
    st = _settings(root) if settings is None else as_settings(settings)
    v = getattr(st, key, None)
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise LayoutError(f"[settings] {key} must be a folder path such "
                          f"as \"../{_KEY_EXAMPLE.get(key, INPUTS)}\" "
                          f"(got {v!r})")
    p = Path(v.strip()).expanduser()
    p = p if p.is_absolute() else root / p
    real = _resolved(p)
    bound = root.parent
    try:
        real.relative_to(bound)
    except ValueError:
        raise LayoutError(
            f"[settings] {key} = {v!r} leads to {real}, outside "
            f"{bound} (the folder that holds this project): a shared "
            f"folder must sit beside the year folders, not further out "
            f"(a symlink that leaves it counts too)") from None
    if real in (bound, root):
        raise LayoutError(f"[settings] {key} = {v!r} names the project "
                          f"folder itself or the folder holding it — name "
                          f"a folder inside it, such as "
                          f"\"../{_KEY_EXAMPLE.get(key, INPUTS)}\"")
    if key == INPUTS_KEY and _overlaps(real, root):
        # A folder inside this project is not a shared one: the
        # project's own exports are read from inputs/ with no setting.
        raise LayoutError(f"[settings] {key} = {v!r} names a folder "
                          f"inside this project — {key} names the folder "
                          f"of exports every year shares, beside the year "
                          f"folders (\"../{INPUTS}\"); for this project's "
                          f"own inputs/ remove the setting")
    # (exports_dir: exports_overlap names any year folder it overlaps)
    for y in (_year_folders_beside(root) if key != EXPORTS_KEY else ()):
        if y != root and _overlaps(real, y):
            raise LayoutError(f"[settings] {key} = {v!r} names a folder "
                              f"inside {y.name}/, another year's project "
                              f"— name a folder beside the year folders, "
                              f"such as \"../{_KEY_EXAMPLE.get(key, INPUTS)}"
                              f"\"")
    return real


def own_folder_value(root, value: Any) -> Any:
    """A folder setting's value (`holdings_dir`) as another year's copy
    of the project would write it: a folder inside the project itself,
    named by way of its year folder or by its full path
    (`"../2025/snapshots"` in 2025/), is that year's own — the copy's
    own is the same folder inside it (`"snapshots"`); `new-year` and
    `align` carried the path into a year that refuses a folder inside
    another year's (backlog TJ-0798). Any other value as it is: one
    inside the project only as written, by a link leading out of it, is
    a folder the years can share, as folder_setting reads it (backlog
    TJ-1081)."""
    if not isinstance(value, str) or not value.strip():
        return value
    v = value.strip()
    p = Path(v).expanduser()
    r = _resolved(root)
    cand = p if p.is_absolute() else r / p
    real = _resolved(cand)
    try:
        real.relative_to(r)
    except ValueError:
        return value
    # As written first (a link to another folder of the year keeps the
    # name it was given), then with links followed (a full path through
    # a link to the project's folder).
    for form in (Path(os.path.normpath(str(cand))), real):
        try:
            rel = form.relative_to(r)
        except ValueError:
            continue
        if not rel.parts:
            return value
        own = rel.as_posix()
        return value if Path(own) == Path(os.path.normpath(v)) else own
    return value


def _year_folders_beside(root: Path) -> List[Path]:
    """The folders named YYYY beside `root` (resolved)."""
    try:
        return [_resolved(p) for p in Path(root).parent.iterdir()
                if YEAR_DIR_RE.match(p.name) and p.is_dir()]
    except OSError:
        return []


def _folder(root, key: str, default: Optional[str]) -> Optional[Path]:
    try:
        p = folder_setting(root, key)
    except LayoutError:
        # load_config refuses the setting, naming it; a reader that runs
        # anyway sees the project's own folder, never an outside one.
        p = None
    if p is not None:
        return p
    return Path(root) / default if default else None


# --------------------------------------------------------------- paths

def inputs_dir(root) -> Path:
    """inputs/ of the project, or the shared folder `inputs_dir` names."""
    return _folder(root, INPUTS_KEY, INPUTS)


def shared_inputs(root) -> bool:
    """The project reads its exports from a folder outside it."""
    try:
        return folder_setting(root, INPUTS_KEY) is not None
    except LayoutError:
        return False


# The project a command runs for, named by `tjs run` (every
# command's load_config) to its stages: with exports shared by every
# year (`inputs_dir`) an input file's folders say nothing about which
# project reads it (inputs/../ is the folder holding the year folders).
ENV_PROJECT_ROOT = "TAXJSON_PROJECT_ROOT"


def project_of_input(path) -> Optional[Path]:
    """The project folder an input file (inputs/<account>/<file>)
    belongs to: the project TAXJSON_PROJECT_ROOT names when the file is
    in its inputs folder (its own inputs/ or the shared one); else the
    folder holding inputs/ (a single-folder project, or a file read
    outside a run). None when the file is in no inputs/<account>/."""
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return None
    env = (os.environ.get(ENV_PROJECT_ROOT) or "").strip()
    if env:
        root = _resolved(env)
        if has_config(root):
            for base in (inputs_dir(root), root / INPUTS):
                try:
                    p.relative_to(_resolved(base))
                    return root
                except ValueError:
                    continue
    pp = p.parents
    if len(pp) > 2 and pp[1].name == INPUTS:
        return pp[2]
    return None


# The stem ending of the copy `tjs redact FILE` writes (NAME.redacted.EXT).
REDACTED_STEM = ".redacted"
_INPUT_SUFFIXES = (".csv", ".tt")


def redacted_copy_beside_exports(path) -> bool:
    """`path` is a NAME.redacted.EXT copy `tjs redact FILE` wrote
    beside the exports it was made from: its folder also holds an
    activity file (.csv / .tt) that is no redacted copy. Never an
    input: its rows are the export's again under placeholder ids, so
    the run booked them twice (backlog TJ-0476). A folder holding only
    redacted copies (a sample project) is read as it is."""
    p = Path(path)
    if not p.stem.lower().endswith(REDACTED_STEM):
        return False
    try:
        kids = list(p.parent.iterdir())
    except OSError:
        return False
    return any(q.suffix.lower() in _INPUT_SUFFIXES
               and not q.stem.lower().endswith(REDACTED_STEM)
               and not q.name.startswith((".", "~$")) and q.is_file()
               for q in kids)


def is_account_folder(folder) -> bool:
    """`folder` is an inputs/<account>/ folder: of a folder named
    inputs, or of the shared inputs folder of the project
    TAXJSON_PROJECT_ROOT names."""
    d = _resolved(folder)
    if d.parent.name == INPUTS:
        return True
    env = (os.environ.get(ENV_PROJECT_ROOT) or "").strip()
    return bool(env) and has_config(_resolved(env)) \
        and d.parent == _resolved(inputs_dir(_resolved(env)))


def project_path(root, rel: str) -> Path:
    """A project-relative path as written in messages and plans
    ("inputs/<account>/x.tt", "ticker.map"): one under inputs/ in the
    shared inputs folder when `inputs_dir` names one."""
    parts = Path(rel).parts
    if parts and parts[0] == INPUTS:
        return inputs_dir(root).joinpath(*parts[1:])
    return Path(root) / rel


# An account's corporate-action elections (lib/corp_actions.Manifest):
# inputs/<account>/elections.json. OLD_ELECTIONS is its name before
# v3.0.0: read only from a filed year's frozen copy, which keeps the name
# close-year copied it under (a filed year is never touched); in an open
# year it stops every command until `tjs migrate` renames it.
ELECTIONS = "elections.json"
OLD_ELECTIONS = "manifest.json"
# work/<account>_manifest.json: the elections of a layout older still,
# read by no command now (`tjs migrate` moves it into inputs/<account>/).
WORK = "work"
LEGACY_WORK_ELECTIONS = "_manifest.json"


def elections_file(acct_dir, root=None) -> Path:
    """The elections file of the account folder `acct_dir`: its
    elections.json, or its manifest.json when there is no elections.json
    and the project `root` is a filed year with frozen inputs
    (frozen_project): the copy close-year froze under the old name."""
    new = Path(acct_dir) / ELECTIONS
    old = new.with_name(OLD_ELECTIONS)
    if (root is not None and not os.path.lexists(new)
            and os.path.lexists(old) and frozen_project(root)):
        return old
    return new


def holds_elections(root, settings: Any = None) -> bool:
    """The project `root` reads an elections.json: an account folder of
    its inputs folder (inputs_dir: the shared one too) holds one (what
    lib/requires' `elections_json` asks for) — with `settings`, the
    [settings] of a taxjson.toml not written yet (new-year's copy).
    False for a year frozen with its elections under the old name, and
    when the folder cannot be read."""
    try:
        folder = (inputs_dir(root) if settings is None else
                  folder_setting(root, INPUTS_KEY, settings)
                  or Path(root) / INPUTS)
        return any(os.path.lexists(d / ELECTIONS) for d in folder.iterdir())
    except (OSError, LayoutError):
        return False


def old_elections_problem(root, accounts: Sequence[str]
                          ) -> Optional[Tuple[str, str]]:
    """(headline, detail) of the refusal while the project reads an
    account's elections under an old name: inputs/<account>/manifest.json
    of an open year's inputs folder (inputs_dir: the shared one too), an
    account folder holding both names (a filed year too: it never picks
    one without a word), or work/<account>_manifest.json (a layout older
    still). A folder at the old name is no elections file: what it holds
    goes out, the folder goes (`tjs migrate` refuses it). None when there
    is none, or the project is a filed year with frozen inputs reading its
    copy under the name it was frozen with."""
    folder = inputs_dir(root)
    old = [folder / str(a) / OLD_ELECTIONS for a in accounts
           if os.path.lexists(folder / str(a) / OLD_ELECTIONS)]
    work = [Path(root) / WORK / f"{a}{LEGACY_WORK_ELECTIONS}"
            for a in accounts
            if os.path.lexists(Path(root) / WORK
                               / f"{a}{LEGACY_WORK_ELECTIONS}")]
    if not old and not work:
        return None             # (every command's read: no lock opened)
    frozen = frozen_project(root)
    dirs = [p for p in old if p.is_dir()]
    old = [p for p in old if p not in dirs]
    both = [p for p in old if os.path.lexists(p.with_name(ELECTIONS))]
    names = ", ".join(shown(p, root) for p in both)
    folders = ", ".join(shown(p, root) for p in dirs)
    move = (f"move what {'it' if len(dirs) == 1 else 'each'} holds out "
            f"(elections go in {ELECTIONS}), then remove "
            f"{'it' if len(dirs) == 1 else 'them'}")
    gone = (f" {folders}: {'a folder' if len(dirs) == 1 else 'folders'} "
            f"at the old name, no elections file: {move}." if dirs else "")
    if dirs and not both and not work and (frozen or not old):
        return (f"{folders}: {'a folder' if len(dirs) == 1 else 'folders'} "
                f"at the elections file's old name",
                f"{ELECTIONS} is the file taxjson reads ({OLD_ELECTIONS} is "
                f"its name before v3.0.0): {move}. Nothing was run.")
    if both and frozen:
        return (f"{names} and {ELECTIONS} beside "
                f"{'it' if len(both) == 1 else 'each'}: a filed year's "
                f"elections under two names",
                "This year reads the copy close-year froze, under the "
                "name it was frozen with: keep the one that holds this "
                "year's elections (a correction you put in goes in under "
                f"that name) and delete the other.{gone} Nothing was run.")
    if both:
        rest = [shown(p, root) for p in old + work if p not in both]
        return (f"{names} and {ELECTIONS} beside "
                f"{'it' if len(both) == 1 else 'each'}: elections under "
                f"two names",
                f"{ELECTIONS} is the file taxjson reads ({OLD_ELECTIONS} is "
                f"its name before v3.0.0): keep the one that holds your "
                f"elections and delete the other (`tjs migrate` renames "
                f"{OLD_ELECTIONS} {OLD_ELECTIONS}.migrated when the two are "
                f"the same"
                + (f", and moves {', '.join(rest)} too" if rest else "")
                + f").{gone} Nothing was run.")
    old = [] if frozen else old
    if not old and not work:
        return None
    return (f"{', '.join(shown(p, root) for p in old + work)}: the "
            f"elections file is now named {ELECTIONS}",
            "Run `tjs migrate` to rename it ("
            + ("a work/ copy moves into inputs/<account>/; " if work
               else "")
            + "`tjs migrate --dry-run` shows the plan; a filed year's "
            f"frozen copy is left as it is).{gone} Nothing was run.")


def years_layout(root) -> bool:
    """The project is a year folder of a multi-year project: it reads
    shared exports, or (a closed year has its own copy and names no
    shared folder) it is a folder named YYYY beside `inputs/` or
    `snapshot/`."""
    root = Path(root)
    if shared_inputs(root):
        return True
    parent = root.resolve().parent
    return bool(YEAR_DIR_RE.match(root.resolve().name)) and (
        (parent / INPUTS).is_dir() or (parent / SNAPSHOTS).is_dir())


def snapshots_folder(root) -> Path:
    """Where `tjs snapshot` keeps its copies: `snapshot/` beside the
    year folders, else in the project."""
    root = Path(root)
    return (root.resolve().parent if years_layout(root) else root) / SNAPSHOTS


def write_boundary(root) -> Path:
    """The folder a file the project rewrites must stay inside: the
    project, or with shared inputs the folder holding the year folders
    (the inputs folder is inside it — folder_setting)."""
    return Path(root).parent if shared_inputs(root) else Path(root)


def ticker_map_path(root) -> Path:
    return Path(root) / TICKER_MAP


def data_file(root, name: str) -> Path:
    """A project-root file (a legacy map)."""
    return Path(root) / name


def project_map_path(root, name: str) -> Path:
    """Where the project's map `name` is read from: tobase.map through
    `[settings] tobase_map` (tobase_map_path), any other beside
    taxjson.toml."""
    if name == TOBASE_MAP:
        return tobase_map_path(root)
    return Path(root) / name


def data_root(root) -> Path:
    """The folder of the project's own files (ticker.map ...)."""
    return Path(root)


def slips_dir(root) -> Path:
    """The year's slips: always the project's own inputs/slips/ — with
    exports shared by every year (`inputs_dir`) the project keeps an
    inputs/ folder of its own for them alone (slips belong to one tax
    year)."""
    return Path(root) / INPUTS / SLIPS


def local_input_folders(root) -> List[str]:
    """With shared exports: the folders of the project's own inputs/
    besides slips/ (exports put there are not read). [] otherwise."""
    if not shared_inputs(root):
        return []
    d = Path(root) / INPUTS
    try:
        return sorted(p.name for p in d.iterdir() if p.is_dir()
                      and p.name != SLIPS and not p.name.startswith("."))
    except OSError:
        return []


def holdings_folder(root) -> Path:
    """The year's broker positions snapshots: holdings/, or the folder
    `holdings_dir` names (lib/holdings_dir)."""
    return _folder(root, HOLDINGS_KEY, HOLDINGS)


def shared_holdings(root) -> bool:
    """The project is a year folder (years_layout) reading the holdings/
    beside the year folders (`holdings_dir = "../holdings"`, as `tjs
    init` and `tjs migrate` write it): one every year shares, each year
    comparing the snapshots of its own dates (lib/holdings_dir
    `year_files`). Any other `holdings_dir` (inside the project, or
    another folder beside it) and a single-folder project read every
    snapshot there, as before (backlog TJ-5367, TJ-5404)."""
    try:
        p = folder_setting(root, HOLDINGS_KEY)
    except LayoutError:
        return False
    if p is None or not years_layout(root):
        return False
    return _resolved(p) == _resolved(_resolved(root).parent / HOLDINGS)


def exports_folder(root) -> Optional[Path]:
    """Where the newest year's run writes files for other tools
    (`exports_dir`), or None."""
    return _folder(root, EXPORTS_KEY, None)


# The record of the files the last export into exports_dir wrote
# (pipeline/run `_write_exports`; `tjs clean` removes only those).
EXPORTS_MANIFEST = ".taxjson-exports.json"


def exports_record(dst) -> Optional[Tuple[Optional[int], List[str]]]:
    """(the year whose run wrote it, the plain file names it recorded
    writing) of the export in the folder `dst`; None without a readable
    record (none there, not a regular file, not JSON). A name with a path
    in it, or a dot name, is never one."""
    import json
    rec = Path(dst) / EXPORTS_MANIFEST
    try:
        if not stat.S_ISREG(os.lstat(rec).st_mode):
            return None
        doc = json.loads(rec.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    files = doc.get("files")
    year = doc.get("year")
    return (year if isinstance(year, int) and not isinstance(year, bool)
            else None,
            [f for f in (files if isinstance(files, list) else [])
             if isinstance(f, str) and f and f == Path(f).name
             and f not in (".", "..") and not f.startswith(".")])


def tobase_setting(root, settings: Any = None
                   ) -> Optional[Path]:
    """The shared tobase.map `[settings] tobase_map` names, resolved
    against the project (None when unset). LayoutError when it is not a
    path string, or resolves (a symlink followed) outside the folder
    holding the project (its parent), to a folder, inside this project
    (its own tobase.map needs no setting) or inside another year's
    folder: the file every year shares sits beside the year folders."""
    root = _resolved(root)
    st = _settings(root) if settings is None else as_settings(settings)
    v = st.tobase_map
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise LayoutError(f"[settings] {TOBASE_KEY} must be a file path "
                          f"such as \"{SHARED_TOBASE}\" (got {v!r})")
    p = Path(v.strip()).expanduser()
    p = p if p.is_absolute() else root / p
    real = _resolved(p)
    bound = root.parent
    try:
        real.relative_to(bound)
    except ValueError:
        raise LayoutError(
            f"[settings] {TOBASE_KEY} = {v!r} leads to {real}, outside "
            f"{bound} (the folder that holds this project): the "
            f"tobase.map every year shares sits beside the year folders, "
            f"not further out (a symlink that leaves it counts too)"
        ) from None
    if real.is_dir() or real in (bound, root):
        raise LayoutError(f"[settings] {TOBASE_KEY} = {v!r} names a "
                          f"folder — name the file, such as "
                          f"\"{SHARED_TOBASE}\"")
    if _overlaps(real, root):
        raise LayoutError(f"[settings] {TOBASE_KEY} = {v!r} names a file "
                          f"inside this project — {TOBASE_KEY} names the "
                          f"tobase.map every year shares, beside the year "
                          f"folders (\"{SHARED_TOBASE}\"); for this "
                          f"project's own tobase.map remove the setting")
    for y in _year_folders_beside(root):
        if y != root and _overlaps(real, y):
            raise LayoutError(f"[settings] {TOBASE_KEY} = {v!r} names a "
                              f"file inside {y.name}/, another year's "
                              f"project — the shared tobase.map sits "
                              f"beside the year folders "
                              f"(\"{SHARED_TOBASE}\")")
    return real


def tobase_map_path(root) -> Path:
    """The tobase.map the project reads: the shared file `[settings]
    tobase_map` names, else tobase.map beside ticker.map. (A setting
    that cannot be used is refused by load_config; a reader that runs
    anyway sees the project's own file, never an outside one.)"""
    try:
        p = tobase_setting(root)
    except LayoutError:
        p = None
    return p if p is not None else Path(root) / TOBASE_MAP


def shared_tobase(root) -> bool:
    """The project reads a tobase.map every year shares (`tobase_map`)."""
    try:
        return tobase_setting(root) is not None
    except LayoutError:
        return False


def tobase_shown(root) -> str:
    """The project's tobase.map as written in messages: `tobase.map`, or
    for a shared one its path from the project and what it is
    ("../tobase.map, shared by every year")."""
    if not shared_tobase(root):
        return TOBASE_MAP
    return (f"{shown(tobase_map_path(root), _resolved(root))}, shared by "
            f"every year")


def tobase_both_problem(root, table: Any) -> Optional[str]:
    """Why a year folder's tobase.map files are ambiguous, or None: the
    setting names a shared file and the folder still holds a tobase.map
    of its own (one of the two would be silently ignored)."""
    try:
        shared = tobase_setting(root, table)
    except LayoutError:
        return None                 # said by setting_problems already
    own = Path(root) / TOBASE_MAP
    if shared is None or not os.path.lexists(own):
        return None
    if own.exists() and _resolved(own) == shared:
        return None                 # a link to the shared file itself
    v = as_settings(table).tobase_map
    if frozen_project(root, table):
        # A filed year's tobase.map is the copy close-year froze: the
        # setting is what was written back, never a copy to migrate.
        return (f"two tobase.map files for this year: [settings] "
                f"{TOBASE_KEY} = {v!r} names "
                f"{shown(shared, _resolved(root))} (shared by every year), "
                f"but this year is filed and its tobase.map is the copy "
                f"close-year froze — delete {TOBASE_KEY} (and "
                f"{INPUTS_KEY}, if set) from [settings]: the year then "
                f"reads its own frozen copy again")
    return (f"two tobase.map files for this year: [settings] {TOBASE_KEY} "
            f"= {v!r} names {shown(shared, _resolved(root))} (shared by "
            f"every year), and this folder holds a tobase.map of its own "
            f"— `tjs migrate` in the folder holding the year folders "
            f"makes the per-year copies one shared file (each kept as "
            f"tobase.map.bak); or delete this folder's tobase.map, or "
            f"remove the setting")


def config_files(root) -> List[Path]:
    p = Path(root) / CONFIG
    return [p] if (p.exists() or p.is_symlink()) else []


def has_config(root) -> bool:
    return (Path(root) / CONFIG).is_file()


def user_config_dir() -> Path:
    """The user's taxjson folder, ~/.config/taxjson: the installer's
    channel, the redaction denylist, the generic importer's layouts."""
    return Path.home() / ".config" / "taxjson"


def read_config(root) -> Dict[str, Any]:
    """The parsed taxjson.toml (LayoutError when it cannot be read)."""
    return _read_doc(Path(root) / CONFIG)


def read_config_soft(root) -> Dict[str, Any]:
    """The configuration, or {} when there is none or it cannot be read
    (the lenient readers: views that work from work/ files)."""
    try:
        return read_config(root) if has_config(root) else {}
    except (LayoutError, OSError):
        return {}


def shown(path: Path, base: Path) -> str:
    """`path` relative to `base` (../inputs), else as is."""
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:
        return str(path)


# ------------------------------------------------------- the year folders

def year_dirs(folder) -> List[Tuple[int, Path]]:
    """[(year, folder)] of the year projects in `folder` (sub-folders
    named YYYY holding a taxjson.toml), oldest first. A symlinked
    folder is not one."""
    out = []
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return []
    for p in entries:
        if YEAR_DIR_RE.match(p.name) and p.is_dir() \
                and not p.is_symlink() and (p / CONFIG).is_file():
            out.append((int(p.name), p))
    return sorted(out)


def multi_root(root) -> Optional[Path]:
    """The folder holding a multi-year project's year folders: `root`
    itself when it has no taxjson.toml of its own but year projects;
    its parent for a year project (named YYYY) beside other year
    folders or reading its exports from the parent; None otherwise."""
    d = _resolved(root)
    if not has_config(d):
        return d if year_dirs(d) else None
    if YEAR_DIR_RE.match(d.name) and (
            shared_inputs(d) or len(year_dirs(d.parent)) > 1):
        return d.parent
    return None


def setting_problems(root, table: Any) -> List[str]:
    """What load_config refuses about the folder settings: a path that
    is not one or leaves the folder holding the project (the shared
    tobase.map too, and a year folder holding a tobase.map of its own
    beside that setting: tobase_both_problem); an
    `exports_dir` that overlaps another folder (exports_overlap); a
    year folder
    (named YYYY) of a shared-exports project whose `year` is another."""
    st = as_settings(table)              # once: the helpers hand it back as is
    out = []
    for key in FOLDER_KEYS:
        try:
            folder_setting(root, key, st)
        except LayoutError as e:
            out.append(str(e))
    try:
        tobase_setting(root, st)
    except LayoutError as e:
        out.append(str(e))
    both = tobase_both_problem(root, st)
    if both:
        out.append(both)
    ov = exports_overlap(root, st)
    if ov:
        out.append(ov)
    d = _resolved(root)
    year = st.year
    if (st.inputs_dir is not None and YEAR_DIR_RE.match(d.name)
            and isinstance(year, int) and not isinstance(year, bool)
            and int(d.name) != year):
        out.append(f"[settings] year = {year} but this folder is {d.name}: "
                   f"a year folder holds that year's project — fix `year`, "
                   f"or move the project to a folder named {year}")
    return out


def _overlaps(a: Path, b: Path) -> bool:
    """`a` and `b` are the same folder, or one holds the other."""
    return a == b or a in b.parents or b in a.parents


def exports_overlap(root, table: Any) -> Optional[str]:
    """Why `exports_dir` cannot be used, or None: the newest year's run
    replaces files there (pipeline/run `_write_exports`), so it must be a
    folder of its own — not the same as, inside or holding the inputs
    (the shared folder or the project's own inputs/), the holdings
    folder, a year folder (YYYY beside the project, this one included),
    the project's work/, reports/ or filed/, or the project folder."""
    _st = as_settings(table)
    if _st.exports_dir is None:
        return None
    try:
        exp = folder_setting(root, EXPORTS_KEY, _st)
    except LayoutError:
        return None                 # said by setting_problems already
    if exp is None:
        return None
    d = _resolved(root)
    bound = d.parent
    places: List[Tuple[str, Path]] = [
        (f"the project's {n}/ folder", d / n)
        for n in ("work", "reports", "filed")]
    for key, default, what in ((INPUTS_KEY, INPUTS, "inputs folder"),
                               (HOLDINGS_KEY, HOLDINGS, "holdings folder")):
        try:
            p = folder_setting(root, key, _st)
        except LayoutError:
            p = None
        if p is not None:
            places.append((f"the {what} ({key})", p))
        places.append((f"the project's {default}/ folder", d / default))
    places.append(("this project's folder", d))
    years: List[Path] = []
    try:
        years += [p for p in bound.iterdir()
                  if YEAR_DIR_RE.match(p.name) and p.is_dir()]
    except OSError:
        pass
    if YEAR_DIR_RE.match(exp.name) and exp.parent == bound:
        years.append(exp)           # a year folder still to be made
    for y in sorted({_resolved(p) for p in years}):
        places.append((f"the year folder {y.name}/", y))
    v = _st.exports_dir
    for what, p in places:
        if _overlaps(exp, _resolved(p)):
            return (f"[settings] {EXPORTS_KEY} = {v!r} overlaps {what} "
                    f"({p}): the newest year's run replaces files in "
                    f"{EXPORTS_KEY} — name a folder of its own beside the "
                    f"year folders, such as \"../{EXPORTS}\"")
    return None


def unconfigured_inputs(root, accounts: Dict[str, Any]) -> List[str]:
    """The folders of the shared inputs/ this year's taxjson.toml has no
    [accounts.NAME] for (an account another year has: one split later,
    one closed earlier). [] when the exports are not shared."""
    if not shared_inputs(root):
        return []
    d = inputs_dir(root)
    try:
        names = sorted(p.name for p in d.iterdir() if p.is_dir()
                       and not p.name.startswith(".")
                       and p.name != SLIPS)
    except OSError:
        return []
    return [n for n in names if n not in (accounts or {})]


# ------------------------------------------------------------- new year

# Tables that hold one year's own figures: commented out in the copy
# `tjs new-year` makes of last year's taxjson.toml.
YEAR_ONLY_TABLES = ("estimate", "instalments")


def new_year_text(text: str, old_year: int, new_year: int) -> str:
    """Last year's taxjson.toml as next year's: `year` set, the
    prior_year_record pointed at last year's lock, and the year's own
    tables ([estimate], [instalments]) and each account's `holdings`
    (last year's positions snapshots) commented out under a note —
    everything else (accounts, settings, comments) kept as written.
    Every change goes through set_key_text, which reads the keys as
    tomllib does (a quoted or hyphenated account table, GitHub #27; a
    value over several lines; `year` spelt `+2024` or `2_024`, GitHub
    #37; [settings] as an inline table, #38, or dotted keys, #39) and
    checks that the result reads as intended. LayoutError when the
    text cannot be read or changed that way."""
    doc = parse_config_text(text)
    for t in YEAR_ONLY_TABLES:
        if t in doc:
            text = set_key_text(
                text, (t,), None,
                note=f"## {old_year}'s figures, kept for reference by "
                     f"`tjs new-year`: put {new_year}'s in and "
                     f"uncomment them.")
    accounts = doc.get("accounts")
    for name, a in (accounts.items() if isinstance(accounts, dict)
                    else ()):
        if isinstance(a, _cabc.Mapping) and "holdings" in a:
            # Last year's positions snapshots are not this year's: the
            # new year's go in its holdings/ (found by account), or are
            # listed here again (lib/holdings_dir).
            text = set_key_text(
                text, ("accounts", name, "holdings"), None,
                note=f"## {old_year}'s positions snapshots, commented out "
                     f"by `tjs new-year`: save {new_year}'s in "
                     f"holdings/, or list them here.")
    text = set_key_text(text, ("settings", "year"), new_year)
    return set_key_text(text, ("settings", "prior_year_record"),
                        f"../{old_year}/filed/{old_year}.json",
                        after="year")


# ----------------------------------------------------------- comparing

def map_rules(path: Path) -> List[str]:
    """ticker.map's rule lines, comments and spacing dropped ([] when
    there is no file). LayoutError when the file is there but cannot be
    read (a folder, a link to a missing file, not UTF-8): an unreadable
    map is never compared as an empty one."""
    p = Path(path)
    if not os.path.lexists(p):
        return []
    try:
        text = p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        why = e.strerror if isinstance(e, OSError) else "not UTF-8 text"
        raise LayoutError(f"cannot read {p}: {why or e}") from None
    out = []
    for ln in text.splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.append(" ".join(ln.split()))
    return out


KeyPath = Tuple[str, ...]


def flat_paths(doc: Dict[str, Any]) -> Dict[KeyPath, Any]:
    """{("settings", "year"): 2025, ("accounts", "margin", "type"):
    "taxable", ...}: each key of a parsed taxjson.toml by its path (an
    array, an array of tables or an inline table as one value). A name
    holding a dot (`[accounts."margin.one"]`) stays one part (GitHub
    #36)."""
    out: Dict[KeyPath, Any] = {}
    for t, v in doc.items():
        if t == "accounts" and isinstance(v, _cabc.Mapping):
            for name, a in v.items():
                if isinstance(a, _cabc.Mapping):
                    for k, x in a.items():
                        out[("accounts", name, k)] = x
                else:
                    out[("accounts", name)] = a
        elif isinstance(v, dict):
            for k, x in v.items():
                out[(t, k)] = x
        else:
            out[(t,)] = v
    return out


def flat_keys(doc: Dict[str, Any]) -> Dict[str, Any]:
    """flat_paths with each path written as its TOML dotted key
    (`accounts."margin.one".type`)."""
    return {key_text(p): v for p, v in flat_paths(doc).items()}


def key_text(path: Sequence[str]) -> str:
    """A key path as a TOML dotted key: `settings.year`,
    `accounts."margin.one".type` (a part that is not a bare key quoted)."""
    return _dotted(path)


# Keys that differ from year to year by design.
_PER_YEAR_KEYS = (("settings", "year"), ("settings", "prior_year_record"))
# The shared folders a filed year stops naming when close-year freezes
# its inputs (frozen_project): its own, never compared with another
# year's or brought over (`align` re-pointed a filed year at the shared
# exports; workflow audit 2026-10-22), the shared holdings folder among
# them (backlog TJ-5367).
_FROZEN_KEYS = (("settings", INPUTS_KEY), ("settings", TOBASE_KEY),
                ("settings", HOLDINGS_KEY))
# Account keys never compared or brought over: the year's own positions
# snapshots (`holdings`), and the broker's account ids and query id
# (an id is the user's, never shown in a list of differences).
_ACCOUNT_KEYS_APART = ("holdings", "account", "broker_accounts",
                       "query_id")


def _compared(path: KeyPath) -> bool:
    if path in _PER_YEAR_KEYS or path[0] in YEAR_ONLY_TABLES:
        return False
    return not (path[0] == "accounts" and len(path) == 3
                and path[2] in _ACCOUNT_KEYS_APART)


# compare()'s key for the accounts' order (GitHub #42).
ACCOUNT_ORDER_KEY = "accounts (order)"


def account_order(here: Dict[str, Any], there: Dict[str, Any]
                  ) -> Optional[Dict[str, List[str]]]:
    """The accounts both configurations have, in each one's order, when
    the orders differ (None when they agree): the [accounts.NAME] tables
    are processed in the order they are written, so trades at the same
    moment in two accounts can book differently (GitHub #42)."""
    def _names(doc):
        a = doc.get("accounts")
        return [n for n, v in a.items() if isinstance(v, _cabc.Mapping)] \
            if isinstance(a, _cabc.Mapping) else []
    a, b = _names(here), _names(there)
    common = set(a) & set(b)
    a = [n for n in a if n in common]
    b = [n for n in b if n in common]
    return None if a == b else {"here": a, "there": b}


def _account_names(doc: Dict[str, Any]) -> List[str]:
    """The [accounts.NAME] names of a parsed taxjson.toml, in order."""
    a = doc.get("accounts")
    return [str(n) for n in a] if isinstance(a, _cabc.Mapping) else []


def compare(here: Path, other: Path) -> Dict[str, Any]:
    """What differs between two projects' ticker.map rules and
    taxjson.toml keys (`tjs align`, `tjs years --diff`): rules
    only in one, the accounts only one has (`accounts_only_here`,
    `accounts_only_there`: listed whole, their keys never compared),
    keys set differently (`year`, prior_year_record, the
    year's own tables and the accounts' holdings and broker ids left
    out: _compared; a frozen filed year's inputs_dir, tobase_map and
    holdings_dir too: _FROZEN_KEYS; arrays of tables compared whole), and the order of
    the accounts both have when it differs (one more key,
    ACCOUNT_ORDER_KEY, with `"order": true` and the names in each
    order). Each key is given by its path (`path`, a name with a dot
    one part) and its TOML spelling (`key`). LayoutError naming the file when either
    taxjson.toml or ticker.map cannot be read: an unreadable file never
    compares as the same (GitHub #56)."""
    da, db = read_config(here), read_config(other)
    a, b = map_rules(here / TICKER_MAP), map_rules(other / TICKER_MAP)
    # tobase.map (lib/tobase_map): the interlisted pairs. With one file
    # every year shares (`tobase_map`) the setting is compared (a key
    # below), never the file; per-year copies (the layout before
    # v0.27.1, `tjs migrate` makes them one) are compared line by
    # line, shown apart (align never copies them).
    ta: List[str] = []
    tb: List[str] = []
    if not (shared_tobase(here) or shared_tobase(other)):
        ta, tb = map_rules(here / TOBASE_MAP), map_rules(other / TOBASE_MAP)
    fa, fb = flat_paths(da), flat_paths(db)
    # A holdings folder each year names inside itself is compared (and
    # brought over) as its own: `"../2025/snapshots"` in 2025/ is
    # `"snapshots"`, never a path into another year (backlog TJ-0798).
    for f, d in ((fa, here), (fb, other)):
        if ("settings", HOLDINGS_KEY) in f:
            f[("settings", HOLDINGS_KEY)] = own_folder_value(
                d, f[("settings", HOLDINGS_KEY)])
    order = account_order(da, db)
    # A year closed with its inputs frozen reads its own copy: the
    # shared folders' settings are left out both ways (align never
    # re-points a filed year, nor un-points the open one).
    apart = _FROZEN_KEYS if (frozen_project(here, da.get("settings"))
                             or frozen_project(other, db.get("settings"))
                             ) else ()
    # An account only one year has is listed whole, never its keys: a
    # key brought over to "(not set)" commented out its required `type`
    # and the next run stopped (backlog TJ-0349).
    na, nb = _account_names(da), _account_names(db)
    lone = set(na) ^ set(nb)
    keys = sorted((k for k in set(fa) | set(fb)
                   if _compared(k) and k not in apart
                   and not (k[0] == "accounts" and len(k) > 1
                            and k[1] in lone)
                   and fa.get(k) != fb.get(k)),
                  key=lambda p: (key_text(p), p))
    return {
        "map_only_here": [r for r in a if r not in b],
        "map_only_there": [r for r in b if r not in a],
        "tobase_only_here": [r for r in ta if r not in set(tb)],
        "tobase_only_there": [r for r in tb if r not in set(ta)],
        "accounts_only_here": [n for n in na if n not in nb],
        "accounts_only_there": [n for n in nb if n not in na],
        "keys": [{"key": key_text(k), "path": list(k),
                  "here": fa.get(k), "there": fb.get(k)} for k in keys]
        + ([{"key": ACCOUNT_ORDER_KEY, "path": ["accounts"], "order": True,
             "here": order["here"], "there": order["there"]}]
           if order else []),
    }


def toml_value(v: Any) -> str:
    """A value as a TOML literal (the types taxjson.toml holds)."""
    import json
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return v.isoformat()
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {toml_value(x)}"
                                for k, x in v.items()) + " }"
    raise LayoutError(f"cannot write {v!r} as TOML")


def _toml_key(k: Any) -> str:
    import json
    k = str(k)
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def _lines(text: str) -> List[str]:
    """`text`'s lines, split on "\\n" only (toml_statements' lines)."""
    ls = text.split("\n")
    if ls and ls[-1] == "":
        ls.pop()
    return ls


class TomlStatement(NamedTuple):
    """One statement of a TOML text by its lines (0-based, inclusive): a
    table header ('table': `table` its dotted name as parts, `array`
    for `[[...]]`), a key = value ('kv': `table` the table it is in,
    `key` its dotted key as parts, `comment` the comment after the
    value on its last line), or 'other' (a blank or comment line, or
    one that does not parse)."""
    kind: str
    first: int
    last: int
    table: Tuple[str, ...]
    key: Tuple[str, ...] = ()
    array: bool = False
    comment: str = ""


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_BASIC_STR = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_TRIPLE = ('"' * 3, "'" * 3)


def _toml_key_at(text: str, k: int) -> Tuple[Optional[List[str]], int]:
    """The dotted key at `text[k:]` (bare, "basic" or 'literal' parts,
    spaces around the dots) as its parts, and the position after it;
    (None, k) when there is none."""
    n = len(text)
    segs: List[str] = []
    while True:
        while k < n and text[k] in " \t":
            k += 1
        if k >= n:
            return None, k
        c = text[k]
        if c == '"':
            m = _BASIC_STR.match(text, k)
            if not m:
                return None, k
            try:
                segs.append(_parse_toml(f"k = {m.group(0)}")["k"])
            except Exception:                           # noqa: BLE001
                return None, k
            k = m.end()
        elif c == "'":
            e = text.find("'", k + 1)
            if e < 0 or "\n" in text[k + 1:e]:
                return None, k
            segs.append(text[k + 1:e])
            k = e + 1
        else:
            m = _BARE_KEY.match(text, k)
            if not m:
                return None, k
            segs.append(m.group(0))
            k = m.end()
        j = k
        while j < n and text[j] in " \t":
            j += 1
        if j < n and text[j] == ".":
            k = j + 1
            continue
        return segs, k


def _value_end(text: str, k: int, line: int) -> Tuple[int, int, str]:
    """Where the value starting at `text[k:]` (on line `line`) ends:
    (the position of the newline after it, or len(text); the line it
    ends on; the comment after it on that line, '' when none). An array
    or inline table runs to its closing bracket and a multi-line string
    to its closing quotes, over any number of lines; a bracket, quote or
    `#` inside a string does not count."""
    n = len(text)
    depth = 0
    comment = ""
    while k < n:
        c = text[k]
        if c == "\n":
            if depth <= 0:
                return k, line, comment
            line += 1
            comment = ""
            k += 1
        elif text[k:k + 3] in _TRIPLE:
            q = text[k:k + 3]
            k += 3
            while k < n and not text.startswith(q, k):
                if text[k] == "\\" and q[0] == '"':
                    k += 1
                if k < n and text[k] == "\n":
                    line += 1
                k += 1
            k += 3
            # Up to two quotes of the content may touch the closing ones.
            for _ in range(2):
                if k < n and text[k] == q[0]:
                    k += 1
        elif c in "\"'":
            e = k + 1
            while e < n and text[e] not in (c, "\n"):
                e += 2 if c == '"' and text[e] == "\\" else 1
            k = e + 1 if e < n and text[e] == c else e
        elif c == "#":
            e = text.find("\n", k)
            e = n if e < 0 else e
            comment = text[k:e].rstrip("\r")
            k = e
        else:
            if c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
            k += 1
    return n, line, comment


def toml_statements(text: str) -> List[TomlStatement]:
    """`text` (a TOML file) as its statements, in order, every line in
    one: what a line-by-line edit of a taxjson.toml needs — the table a
    line is in (a quoted or hyphenated name included: a header matched
    as `[A-Za-z0-9_.]+` left the previous table in effect, GitHub #27)
    and every line a key's value spans (an array, inline table or
    string over several lines: GitHub #26)."""
    out: List[TomlStatement] = []
    n = len(text)
    i = line = 0
    table: Tuple[str, ...] = ()
    while i < n:
        j = i
        while j < n and text[j] in " \t\r":
            j += 1
        first = line
        end = text.find("\n", j)
        end = n if end < 0 else end
        st = TomlStatement("other", first, first, table)
        if j < n and text[j] == "[":
            arr = text.startswith("[[", j)
            segs, k = _toml_key_at(text, j + (2 if arr else 1))
            while k < n and text[k] in " \t":
                k += 1
            if segs is not None and text.startswith("]]" if arr else "]",
                                                     k):
                table = tuple(segs)
                st = TomlStatement("table", first, first, table, array=arr)
        elif j < n and text[j] not in "\n#":
            segs, k = _toml_key_at(text, j)
            while k < n and text[k] in " \t":
                k += 1
            if segs is not None and k < n and text[k] == "=":
                end, line, comment = _value_end(text, k + 1, line)
                st = TomlStatement("kv", first, line, table, tuple(segs),
                                   comment=comment)
        out.append(st)
        i = end + 1
        line += 1
    return out


def _dotted(parts: Sequence[str]) -> str:
    return ".".join(_toml_key(p) for p in parts)


def parse_config_text(text: str) -> Dict[str, Any]:
    """taxjson.toml's `text` parsed; LayoutError when it does not read as
    TOML (toml_io.parse_toml's reading, the BOM dropped there). Named for
    what it is: toml_io's `parse_toml` is the parser (TJ-1655)."""
    try:
        doc = _parse_toml(text)
    except Exception as e:                              # noqa: BLE001
        raise LayoutError(f"taxjson.toml is not valid TOML: {e}") from None
    return doc if isinstance(doc, dict) else {}


def _with_key(doc: Dict[str, Any], path: KeyPath, value: Any
              ) -> Dict[str, Any]:
    """A copy of the parsed `doc` with `path` set to `value` (removed
    when None): what set_key_text's result must read as. LayoutError
    when the path runs through a value that is not a table (an array
    of tables included)."""
    import copy
    out = copy.deepcopy(doc)
    cur = out
    for i, p in enumerate(path[:-1]):
        nxt = cur.get(p)
        if nxt is None:
            if value is None:
                return out
            nxt = cur[p] = {}
        if not isinstance(nxt, dict):
            raise LayoutError(f"cannot {'remove' if value is None else 'set'}"
                              f" {_dotted(path)}: {_dotted(path[:i + 1])} "
                              f"is not a table")
        cur = nxt
    if value is None:
        cur.pop(path[-1], None)
    else:
        cur[path[-1]] = value
    return out


def _value_at(doc: Dict[str, Any], path: KeyPath) -> Any:
    cur: Any = doc
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _commented(ln: str, whole: bool) -> str:
    """A line commented out: every line of a key's value (`whole`), else
    only one that is not blank or a comment already."""
    if whole or (ln.strip() and not ln.lstrip().startswith("#")):
        return "# " + ln
    return ln


def set_key_text(text: str, dotted: Union[str, Sequence[str]],
                 value: Any, *, after: Optional[str] = None,
                 note: Optional[str] = None) -> str:
    """`text` (a taxjson.toml) with the key `dotted` ("settings.x", or
    its parts as a tuple when a name holds a dot) set to `value`, or
    commented out when `value` is None — comments and the rest of the
    text kept as written.

    Where the key is set, every line of its value is replaced (a value
    over several lines included, GitHub #26; a comment after a one-line
    value kept), whatever the spelling of the old value (`+2024`,
    `2_024`, GitHub #37). A key inside an inline table (`settings =
    {year = 2024}`) rewrites that inline table (#34, #38); a key of a
    table written as dotted keys (`settings.year = 2024`) is added
    beside them, in the same table (#39); an array of tables
    (`[[distributions]]`) is written or commented out as [[...]]
    sections (#41). A key not set yet goes after the key `after` of the
    same table when that is set, else at the end of its table (the
    table added at the end of the file when absent). Removing comments
    out every line that sets it (each section of an array of tables),
    under `note` when given.

    The result is read back and must equal the original with exactly
    this key changed: LayoutError otherwise (and when `text` does not
    read as TOML) — never a file that reads differently from what was
    asked."""
    path: KeyPath = tuple(dotted.split(".")) if isinstance(dotted, str) \
        else tuple(dotted)
    if not path:
        raise LayoutError("no key to set")
    doc = parse_config_text(text)
    want = _with_key(doc, path, value)
    if value is None and want == doc:
        return text                 # not set: nothing to remove
    out = _edit_text(text, doc, path, value, after, note)
    try:
        got = parse_config_text(out)
    except LayoutError as e:
        got, why = None, str(e)
    else:
        why = "it would read differently"
    if got != want:
        raise LayoutError(
            f"cannot {'remove' if value is None else 'set'} "
            f"{_dotted(path)} in taxjson.toml safely ({why}): the table "
            f"is written in a form this edit does not handle — write it "
            f"out as a [table] with one key per line, then try again")
    return out


def _edit_text(text: str, doc: Dict[str, Any], path: KeyPath, value: Any,
               after: Optional[str], note: Optional[str]) -> str:
    """set_key_text's edit of the lines (checked by the caller)."""
    lines = _lines(text)
    stmts = toml_statements(text)
    n = len(path)

    def full(s: TomlStatement) -> KeyPath:
        return s.table + s.key

    def one(s: TomlStatement, v: Any) -> List[str]:
        # The key as written (its spacing and alignment kept).
        first = lines[s.first]
        _segs, k = _toml_key_at(first, 0)
        while k < len(first) and first[k] in " \t":
            k += 1
        k += 1                                          # the "="
        while k < len(first) and first[k] in " \t":
            k += 1
        return [first[:k] + toml_value(v)
                + (f"  {s.comment}" if s.comment else "")]

    kvs = [s for s in stmts if s.kind == "kv"]
    # A key of an inline table: the inline table is rewritten whole.
    anc = [s for s in kvs if len(full(s)) < n and path[:len(full(s))]
           == full(s)]
    if anc:
        s = anc[0]
        sub = _value_at(doc, full(s))
        if not isinstance(sub, dict):
            raise LayoutError(f"cannot set {_dotted(path)}: "
                              f"{_dotted(full(s))} is not a table")
        sub = _with_key(sub, path[len(full(s)):], value)
        lines[s.first:s.last + 1] = one(s, sub)
        return "\n".join(lines) + "\n"
    exact = [s for s in kvs if full(s) == path]
    below = [s for s in kvs if len(full(s)) > n and full(s)[:n] == path]
    heads = [i for i, s in enumerate(stmts) if s.kind == "table"]
    sections = [i for i in heads if stmts[i].table[:n] == path]
    if exact and not below and not sections and value is not None:
        s = exact[0]
        lines[s.first:s.last + 1] = one(s, value)
        return "\n".join(lines) + "\n"
    # Comment out every statement that sets the key: its own line(s),
    # dotted keys below it, and each [path...] / [[path]] section.
    gone = set(id(s) for s in exact + below)
    for i in sections:
        nxt = next((h for h in heads if h > i), len(stmts))
        gone.update(id(s) for s in stmts[i:nxt])
    out: Dict[int, List[str]] = {}
    noted = False
    for s in stmts:
        if id(s) not in gone:
            continue
        seg = [_commented(ln, s.kind != "other")
               for ln in lines[s.first:s.last + 1]]
        if note and not noted:
            seg = [note] + seg
            noted = True
        out[s.first] = seg
        for k in range(s.first + 1, s.last + 1):
            out[k] = []
    end: List[str] = []
    if value is not None:
        key = path[-1]
        parent = path[:-1]
        line = f"{_toml_key(key)} = {toml_value(value)}"
        live = [s for s in stmts if id(s) not in gone]
        if isinstance(value, list) and value \
                and all(isinstance(x, dict) for x in value):
            # An array of tables: one [[path]] section per element.
            for x in value:
                end += ["", f"[[{_dotted(path)}]]"] + [
                    f"{_toml_key(k)} = {toml_value(v)}"
                    for k, v in x.items()]
        elif isinstance(value, dict) and sections:
            end += ["", f"[{_dotted(path)}]"] + [
                f"{_toml_key(k)} = {toml_value(v)}"
                for k, v in value.items()]
        else:
            at = None
            sib = [s for s in live if s.kind == "kv" and after is not None
                   and full(s) == parent + (after,)]
            hdr = [i for i in heads if id(stmts[i]) not in gone
                   and stmts[i].table == parent and not stmts[i].array]
            dk = [s for s in live if s.kind == "kv"
                  and len(full(s)) > len(parent)
                  and full(s)[:len(parent)] == parent
                  and len(s.table) <= len(parent)]
            if sib:
                s = sib[0]
                at = s.last
                line = (f"{_dotted(s.key[:-1] + (key,))} = "
                        f"{toml_value(value)}")
            elif hdr:
                # After the table's last key (a comment or blank line
                # after it stays below).
                i = hdr[0]
                nxt = next((h for h in heads if h > i), len(stmts))
                body = [s for s in stmts[i + 1:nxt] if s.kind == "kv"]
                at = body[-1].last if body else stmts[i].last
            elif dk and parent:
                s = dk[-1]
                at = s.last
                line = (f"{_dotted(parent[len(s.table):] + (key,))} = "
                        f"{toml_value(value)}")
            elif not parent:
                # A key of the root table: before the first table.
                first = stmts[heads[0]].first if heads else len(lines)
                at = first - 1
                while at >= 0 and not lines[at].strip():
                    at -= 1
                if at < 0:
                    if lines:
                        out[0] = [line] + out.get(0, [lines[0]])
                    else:
                        end.append(line)
                    line = ""
            else:
                end += ["", f"[{_dotted(parent)}]", line]
                line = ""
            if at is not None and line:
                cur = out.get(at, [lines[at]])
                out[at] = cur + [line]
    res: List[str] = []
    for k, ln in enumerate(lines):
        res += out.get(k, [ln])
    if end:
        while res and not res[-1].strip():
            res.pop()
        res += end
    return "\n".join(res) + "\n"


# The note close-year's freeze writes over the shared folders' settings
# it comments out (cli/common._FROZEN_NOTE_PREFIX).
FROZEN_NOTE_PREFIX = "## frozen by `tjs close-year`"


def _freeze_noted(d) -> bool:
    """The year project `d`'s taxjson.toml carries close-year's freeze
    note (FROZEN_NOTE_PREFIX)."""
    try:
        text = (Path(d) / CONFIG).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    return any(ln.strip().startswith(FROZEN_NOTE_PREFIX)
               for ln in text.splitlines())


def frozen_year(d, year) -> bool:
    """The year project `d` was closed with its inputs frozen (its
    filed/<year>.json lock carries a `frozen` record: `tjs
    close-year` copied the shared exports and tobase.map into the
    year's own folders, which the year reads from then on). A lock
    taken before the year ended (close-year --force) records the
    inputs' hashes with `partial` set and copies nothing: that year
    still reads the shared folders, so it is not frozen. A lock that
    is there but cannot be read counts as frozen when the year's
    taxjson.toml still carries the freeze note: the year reads its own
    copy whatever the lock says, so `years` and `migrate` never offer
    to merge its tobase.map while the lock is damaged (backlog
    TJ-0158); so does a lock that was deleted (lock_missing_frozen,
    backlog TJ-0263). A year never closed has no note."""
    import json
    lock = Path(d) / "filed" / f"{year}.json"
    try:
        doc = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _freeze_noted(d)
    if not isinstance(doc, dict):
        return _freeze_noted(d)
    fz = doc.get("frozen")
    return isinstance(fz, dict) and not fz.get("partial")


def filed_reports_kept(d, year) -> bool:
    """`d` (a filed/<year>/reports/ folder) holds a report close-year
    kept (lib/compare.filed_reports_dir) after the year ended: the year
    was closed. Reports all written on or before Dec 31 of `year` are a
    lock taken during the year, a snapshot (filed_locks.
    partial_year_note; backlog TJ-1247)."""
    import datetime as _dt
    d = Path(d)
    try:
        return d.is_dir() and not d.is_symlink() and any(
            f.is_file() and _dt.date.fromtimestamp(f.stat().st_mtime).year
            > int(year) for f in d.iterdir())
    except (OSError, TypeError, ValueError, OverflowError):
        return False


def lock_missing_frozen(d, year) -> bool:
    """The year project `d`'s filed/<year>.json is gone while the
    project shows the year was closed: a filed year whose lock was
    deleted. The sign is close-year's freeze note in its taxjson.toml,
    or the reports close-year kept in filed/<year>/reports/ once the
    year has ended (a year closed as a single folder, then moved by
    `migrate --to-years`, has no note: backlog TJ-1119; a lock taken
    during the year keeps them too, and that year is not closed, its
    reports all dated within it: backlog TJ-1247). It is
    treated as a damaged lock is (backlog TJ-0158, TJ-0263): `years`,
    check-filed and the checklist name the lock to restore; a noted year
    stays frozen as well (frozen_year). A year never closed has neither
    sign; a lock taken before the year ended (`close-year --force`)
    froze nothing and wrote no note."""
    if os.path.lexists(Path(d) / "filed" / f"{year}.json"):
        return False
    if _freeze_noted(d):
        return True
    import datetime as _dt
    try:
        ended = _dt.date.today().year > int(year)
    except (TypeError, ValueError):
        return False
    return ended and filed_reports_kept(
        Path(d) / "filed" / str(year) / "reports", year)


def lock_missing_parts(year) -> Tuple[str, str]:
    """(headline, detail) of what `years`, check-filed, new-year and the
    checklist say about a lock_missing_frozen year."""
    return (f"filed/{year}.json is missing: restore it or run close-year "
            f"again",
            "The year's folder shows it was closed (close-year's freeze "
            "note in taxjson.toml, or the reports it kept as filed): "
            "restore the lock from git or a backup, or `tjs "
            "close-year` locks the year again.")


def lock_missing_text(year) -> str:
    """lock_missing_parts as one text (its headline is the `years`
    line)."""
    head, detail = lock_missing_parts(year)
    return f"{head} — {detail[:1].lower()}{detail[1:].rstrip('.')}"


def frozen_from_shared(d, year) -> bool:
    """frozen_year, with the exports copied from a folder shared by
    every year (the lock's `frozen` record names the `inputs_dir` it
    stopped reading): the year's own inputs/ is that folder's copy,
    later years' rows and all (backlog TJ-0013). So is a year frozen
    as a single-folder project and then moved into its year folder by
    `migrate --to-years` (its lock names no shared folder): its inputs/
    is a copy of the inputs/ beside the year folders, which the years
    after it read (new-year reads it so; backlog TJ-0435)."""
    import json
    try:
        doc = json.loads((Path(d) / "filed" / f"{year}.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return False
    fz = doc.get("frozen") if isinstance(doc, dict) else None
    if not isinstance(fz, dict) or fz.get("partial"):
        return False
    v = fz.get("inputs_dir")
    if isinstance(v, str) and v.strip():
        return True
    return migrated_shared_inputs(d, fz) is not None


def migrated_shared_inputs(d, fz: Any) -> Optional[Path]:
    """The inputs/ beside the year folders, for the year folder `d`
    whose lock's `frozen` record `fz` froze its inputs as a single-
    folder project and names no shared folder (`inputs_dir` null): the
    year was then moved into its year folder by `migrate --to-years`,
    and its inputs/ is a copy of that folder, which the years after it
    read (new-year reads it so; backlog TJ-0435). check-filed and
    `years` compare the year's frozen copy with it (backlog TJ-0610).
    None otherwise (a lock that names its folder, a partial one, a
    folder that is not a year folder beside a shared inputs/)."""
    if not isinstance(fz, dict) or fz.get("partial") \
            or fz.get("inputs_dir") is not None \
            or not isinstance(fz.get("inputs"), dict):
        return None
    here = _resolved(d)
    shared = here.parent / INPUTS
    if (YEAR_DIR_RE.match(here.name) and not has_config(here.parent)
            and shared.is_dir() and not shared.is_symlink()):
        # (named from `d` as given: shown relative to it, ../inputs)
        return Path(os.path.normpath(Path(d).absolute() / os.pardir
                                     / INPUTS))
    return None


def frozen_project(d, settings: Any = None) -> bool:
    """frozen_year for the project `d`, its year read from `settings`
    (its [settings]; read from its taxjson.toml when not given). False
    when the year cannot be read."""
    st = _settings(Path(d)) if settings is None else as_settings(settings)
    try:
        year = int(st.year)
    except (TypeError, ValueError):
        return False
    return frozen_year(d, year)


def tobase_layout(folder) -> Dict[str, Any]:
    """The tobase.map files of a multi-year folder's year projects:
    {"shared": {resolved path: [years naming it]}, "own": {year: its
    own tobase.map}, "frozen": {year: the copy close-year froze — a
    filed year's own file by design, never a copy to merge},
    "both": [years holding one beside the setting], "problems": {year:
    why its setting cannot be used}}."""
    out: Dict[str, Any] = {"shared": {}, "own": {}, "frozen": {},
                           "both": [], "problems": {}}
    for y, d in year_dirs(folder):
        st = _settings(d)
        try:
            p = tobase_setting(d, st)
        except LayoutError as e:
            out["problems"][y] = str(e)
            p = None
        if p is not None:
            out["shared"].setdefault(p, []).append(y)
        own = d / TOBASE_MAP
        if own.is_file() and not own.is_symlink():
            if frozen_year(d, y):
                # The freeze's copy, even beside a setting written back
                # (an older `align`): never a copy to merge — migrate
                # would remove the filed year's own file.
                out["frozen"][y] = own
                if p is not None and _resolved(own) != p:
                    out["both"].append(y)
                continue
            out["own"][y] = own
            if p is not None and _resolved(own) != p:
                out["both"].append(y)
    return out


def set_key_after(text: str, dotted: str, value: Any,
                  after: str) -> str:
    """set_key_text, a key not set yet going on the line after the key
    `after` of the same table when that is set (`tobase_map` beside
    `inputs_dir`)."""
    return set_key_text(text, dotted, value, after=after)


# ------------------------------------------------------------ years view

# The last drift check of the project's filed years, in work/ (written
# by pipeline/run._check_filed_years: `tjs run`, `check-filed`).
FILED_CHECK_STATE = "filed_check.state"
_FILED_CHECK_FORMAT = "filed_check/1"


def _lock_digest(lock: Path) -> Optional[str]:
    import hashlib
    try:
        return hashlib.sha256(lock.read_bytes()).hexdigest()
    except OSError:
        return None


def record_filed_check(root, verdicts: Dict[Any, str]) -> None:
    """Keep the drift check's verdict on each filed year it judged
    (`verdicts`: {year: "drifted" | "ok"}) in work/FILED_CHECK_STATE,
    with the digest of the lock it judged, so `tjs years` shows a
    filed year whose last run DRIFTED (filed_check, backlog TJ-0159).
    Advisory: nothing is written without a work/ folder, and a write
    that fails is let go."""
    import json
    work = Path(root) / "work"
    if not work.is_dir():
        return
    years: Dict[str, Any] = {}
    for y, res in sorted(verdicts.items()):
        digest = _lock_digest(Path(root) / "filed" / f"{y}.json")
        if digest is not None:
            years[str(y)] = {"result": res, "lock": digest}
    from taxjson.lib.safe_write import write_atomic
    try:
        write_atomic(work / FILED_CHECK_STATE, json.dumps(
            {"format": _FILED_CHECK_FORMAT, "years": years}, indent=2,
            sort_keys=True) + "\n")
    except OSError:
        pass


def filed_check(root, year) -> Optional[bool]:
    """True when the last drift check of the project's filed `year`
    (record_filed_check) found it DRIFTED from its lock, False when it
    matched; None when there is no verdict on the lock as it is now (no
    check yet, a lock closed again since, a record that cannot be
    read)."""
    import json
    try:
        doc = json.loads((Path(root) / "work" / FILED_CHECK_STATE)
                         .read_text(encoding="utf-8"))
        rec = doc["years"][str(year)]
        res, digest = rec["result"], rec["lock"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if res not in ("drifted", "ok") or digest != _lock_digest(
            Path(root) / "filed" / f"{year}.json"):
        return None
    return res == "drifted"


_GAINS_ALL_SUFFIX = "_gains_all.json"


# Pipeline artifacts in work/ that are NOT parsed-source files — the
# audit's source join must never read a derived book as provenance.
_AUDIT_DERIVED_SUFFIXES = (
    "_base.json", "_gains.json", "_gains_wash.json", _GAINS_ALL_SUFFIX,
    "_raw.json",
    "_raw_base.json", "_raw_gains.json", "_raw_base_gains.json",
    "_merged.json", "_sorted.json", "_filled.json", "_report.json",
    # The crypto ticker.map stage's output: a renamed copy of the
    # parse, read as a second source it claimed a false 2-file dedup
    # and hid the rename (S047-12).
    "_mapped.json", "_manifest.json",
    "_pending_elections.json", "_validate.diag",
    # The engine's cost timelines (timeline_beside): pools, not rows.
    ".timeline.json",
)


def _audit_source_files(cache: Path, name: str,
                        accounts=None) -> List[Path]:
    """The parsed-source JSONs for one account: everything the pipeline
    wrote as `{name}_*.json` that is not a derived book. These are the
    per-brokerage parses, converted .tt files, and corp-action rows —
    the nominal-currency provenance the audit joins dispositions to.
    `accounts` (the configured names) keeps a longer-named sibling's
    files out: `margin_*.json` also matches margin_us's parses, which
    the audit then listed twice (S047-13)."""
    siblings = [a for a in (accounts or [])
                if a != name and str(a).startswith(f"{name}_")]
    out = []
    for p in sorted(cache.glob(f"{name}_*.json")):
        if any(p.name.endswith(sfx) for sfx in _AUDIT_DERIVED_SUFFIXES):
            continue
        if any(p.name.startswith(f"{sib}_") for sib in siblings):
            continue
        out.append(p)
    return out


def book_files(cache: Path) -> List[Path]:
    """The per-account full-history books (work/<acct>_base.json), as
    `tjs find-missing-history` reads them."""
    return sorted(p for p in cache.glob("*_base.json")
                  if not p.name.endswith("_raw_base.json")
                  and p.name != "sheltered_base.json"
                  and not p.name.startswith("."))


# The map a run applied, in work/ (pipeline/stages.stage_cross_listings
# writes it, lib/cross_listings.effective_map_text: ticker.map with its
# tobase.map pairs, the run's TOBASE joins and the .tt RENAME lines).
EFFECTIVE_MAP = "ticker.map.effective"

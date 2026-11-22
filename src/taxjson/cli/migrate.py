"""`tjs migrate`: a flat project into year folders, shared files, the project's layout."""
import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.project.accounts import accounts_of
from taxjson.lib import out as _out
from taxjson.lib import project_layout as _PL

from taxjson.cli.common import _raise_requirements
from taxjson.cli.setup import _frozen_record, _templated
from taxjson.pipeline.config import (
    _config_shown, _read_config_text, _refuse_legacy_project_files,
    load_config,
)
from taxjson.pipeline.messages import (
    _cmd_prog, _details, _die, _die_input, _out_wrap, _PROG, _say,
)
from taxjson.pipeline.stages import _MIGRATED


# What `migrate --to-years` moves from a single-folder project into its
# year's folder: everything but the exports (inputs/ stays, shared) and
# tobase.map (stays, the one file every year reads: `tobase_map`).
_TO_YEARS_MOVED = ("taxjson.toml", "ticker.map",
                   "checklist.json", "work", "reports",
                   "filed", "export", "holdings")


def _to_years_preflight(root: Path) -> Optional[bytes]:
    """The user files `migrate --to-years` rewrites, checked before
    anything moves (backlog TJ-0799, TJ-0800): taxjson.toml (written in
    the year folder) and .gitignore (gains an `exports/` line) — each
    refused (exit 2, nothing moved) when it is read-only or a link
    leaving the project, as every editor of a user file refuses it
    (lib/safe_write.write_user_file). .gitignore is read as bytes: git
    does not need UTF-8, and its bytes are kept. Returns .gitignore's
    new bytes, or None when it is not to be written."""
    from taxjson.lib.cli_diag import cannot_write
    from taxjson.lib.safe_write import (ReadOnlyFileError, link_outside,
                                        refuse_read_only)

    def _check(p: Path) -> None:
        # (named by the link's own place, never the file it points at)
        shown = str(Path(_config_shown(p.parent)) / p.name)
        tgt = link_outside(p, root)
        if tgt is not None:
            _die_input(f"{shown} is a symlink to {tgt}, outside the "
                       f"project — never written through: replace the "
                       f"link with a copy", "Nothing was moved.")
        try:
            refuse_read_only(p.resolve() if p.is_symlink() else p)
        except ReadOnlyFileError as e:
            _h, _fix = cannot_write(shown, e, p)
            _die_input(_h, *([_fix] if _fix else []), "Nothing was moved.")
    _check(root / "taxjson.toml")
    gi = root / ".gitignore"
    if not gi.is_file():
        return None
    try:
        data = gi.read_bytes()
    except OSError as e:
        _die_input(f"cannot read {_config_shown(gi)}: {e.strerror or e}",
                   "Nothing was moved.")
    if b"exports/" in data:
        return None
    _check(gi)
    data = data.rstrip(b"\n")
    return (data + b"\n" if data else b"") + b"exports/\n"


def _migrate_to_years(root: Path, dry_run: bool) -> None:
    """`tjs migrate --to-years`: a single-folder project becomes one
    folder of exports for every year — inputs/ stays where it is, shared,
    and the project's own files move into <root>/<year>/ (inputs/slips/
    to <year>/inputs/slips/), its taxjson.toml naming the shared folders
    (lib/project_layout). Relative `holdings` and prior_year_record
    paths are rewritten for the folder one level down."""
    import os as _os
    from taxjson.lib.safe_write import write_user_file
    cfg = load_config(root)
    settings = cfg["settings"]
    year = settings.year
    if settings.inputs_dir is not None:
        _die_input("this project already reads a shared inputs folder "
                   f"([settings] {_PL.INPUTS_KEY})")
    # An old per-purpose map (yf_ticker.map ...) would stay behind at the
    # top, read by no year: `tjs migrate` folds it in first.
    _refuse_legacy_project_files(root)
    if not isinstance(year, int):
        _die_input("[settings] year is required")
    dest = root / str(year)
    if dest.exists() or dest.is_symlink():
        _die_input(f"{year}/ already exists here — move it away first")
    # inputs/slips/ is the year's slips folder: it moves into the year
    # folder, so an account of that name would lose its exports there
    # (the shared inputs/ reads no slips/ folder) — refused before
    # anything moves (backlog TJ-0797).
    def _is_slips(n: str) -> bool:
        # (`Slips` too where the disk ignores case: the same folder)
        if n == _PL.SLIPS:
            return True
        a, s = root / _PL.INPUTS / n, root / _PL.INPUTS / _PL.SLIPS
        try:
            return (n.lower() == _PL.SLIPS and a.is_dir() and s.is_dir()
                    and _os.path.samefile(a, s))
        except OSError:
            return False
    _slips_accts = [str(n) for n in (cfg.get("accounts") or {})
                    if _is_slips(str(n))]
    if _slips_accts:
        _n = _slips_accts[0]
        _die_input(f"[accounts.{_n}]: inputs/{_PL.SLIPS}/ is the year's "
                   f"slips folder, which moves into {year}/ — the "
                   f"account's exports there would no longer be read",
                   f"Rename the account (its [accounts.{_n}] table and "
                   f"its inputs/{_n}/ folder) first, then run `tjs "
                   f"migrate --to-years` again. Nothing was moved.")
    moves = [(root / n, dest / n) for n in _TO_YEARS_MOVED
             if _os.path.lexists(root / n)]
    slips = root / "inputs" / "slips"
    if slips.is_dir():
        moves.append((slips, dest / "inputs" / "slips"))
    # A holdings folder of another name (`holdings_dir = "snapshots"`)
    # holds the year's positions snapshots like holdings/: it moves too,
    # and the setting still names it (GitHub #43). One further away is
    # named from the year folder below (_down).
    hd = settings.holdings_dir
    hd_own = False
    if isinstance(hd, str) and hd.strip():
        hn = _os.path.normpath(hd.strip())
        hd_own = (not _os.path.isabs(hn) and len(Path(hn).parts) == 1
                  and hn not in (_PL.INPUTS, _PL.EXPORTS, ".", "..")
                  and not hn.startswith("~"))
        if hd_own and hn not in _TO_YEARS_MOVED and (root / hn).is_dir() \
                and not (root / hn).is_symlink():
            moves.append((root / hn, dest / hn))
    text = _read_config_text(root / "taxjson.toml")
    # A file in the template layout stays in it: the keys set below go
    # in their places, as new-year writes them (`tjs format --check`
    # passes after the move — backlog TJ-0049).
    _was_templated = _templated(text) == text
    _tob = (root / _PL.TOBASE_MAP).is_file()
    # A filed year whose lock froze its inputs keeps a copy of its
    # own: the account folders (and tobase.map) are copied into the
    # year folder, which goes on reading them; the shared ones at the
    # top are for the years to come.
    _frozen = _frozen_record(root, year) is not None
    _frozen_copies = []
    if _frozen:
        for a in sorted(cfg.get("accounts") or {}):
            if (root / _PL.INPUTS / a).is_dir():
                _frozen_copies.append((root / _PL.INPUTS / a,
                                       dest / _PL.INPUTS / a))
        if _tob:
            _frozen_copies.append((root / _PL.TOBASE_MAP,
                                   dest / _PL.TOBASE_MAP))
    try:
        if not _frozen:
            text = _PL.set_key_text(text, f"settings.{_PL.INPUTS_KEY}",
                                    f"../{_PL.INPUTS}")
        text = _PL.set_key_text(text, f"settings.{_PL.EXPORTS_KEY}",
                                f"../{_PL.EXPORTS}")
        if _tob and not _frozen:
            # tobase.map stays at the top: the one file every year reads.
            text = _PL.set_key_after(text, f"settings.{_PL.TOBASE_KEY}",
                                     _PL.SHARED_TOBASE, _PL.EXPORTS_KEY)
    except _PL.LayoutError as e:
        _die_input(f"taxjson.toml: {e}", "Nothing was moved.")
    fixed = []
    moved_rel = {_PL.shown(a, root) for a, _b in moves}

    def _moved_to(path: str) -> Optional[str]:
        """An absolute path's new place when it is in an item that
        moves (holdings/snap.toml -> <year>/holdings/snap.toml), else
        None. Read as written, then with its folder's links followed
        (a path through a link to the project's folder)."""
        p = _os.path.normpath(path)
        forms = [p]
        try:
            forms.append(_os.path.join(
                _os.path.realpath(_os.path.dirname(p)),
                _os.path.basename(p)))
        except (OSError, ValueError):
            pass
        for f in forms:
            for a, b in moves:
                sa = str(a)
                if f == sa or f.startswith(sa + _os.sep):
                    return str(b) + f[len(sa):]
        return None

    def _down(v):
        """A path relative to the project, as written from the year
        folder: one into an item that moves with it (holdings/x.toml,
        filed/2023.json, inputs/slips/...) stays as it is; anything
        else (inputs/<account>/..., ../other) gains a `../`. An
        absolute one (or `~/...`) into an item that moves names its
        new place (backlog TJ-0698); any other stays as written."""
        if not isinstance(v, str) or not v:
            return v
        if v.startswith("~") or _os.path.isabs(v):
            ex = _os.path.expanduser(v)
            new = _moved_to(ex) if _os.path.isabs(ex) else None
            return v if new is None else new
        norm = _os.path.normpath(v)
        if any(norm == m or norm.startswith(m + _os.sep)
               for m in moved_rel):
            return v
        return _os.path.join("..", v)
    try:
        for key in ("prior_year_record",) + (
                () if hd_own else (_PL.HOLDINGS_KEY,)):
            v = getattr(settings, key)
            if isinstance(v, str) and _down(v) != v:
                text = _PL.set_key_text(text, ("settings", key), _down(v))
                fixed.append(f"{key} = \"{_down(v)}\"")
        for n, a in accounts_of(cfg).items():
            h = a.holdings
            if h is None:
                continue
            hv = [_down(x) for x in h] if isinstance(h, list) else _down(h)
            if hv != h:
                text = _PL.set_key_text(text, ("accounts", n, "holdings"),
                                        hv)
                fixed.append(f"[accounts.{n}] holdings")
        new_settings = _PL.parse_config_text(text).get("settings") or {}
        # The oldest taxjson the moved file runs on, raised here (as
        # cmd_migrate would after the move): laid out with the rest.
        from taxjson.lib.requires import raised_text as _req_raised
        text = _req_raised(text, ("missing_history_tt",),
                           own_tobase=_frozen and _tob)
    except (_PL.LayoutError, ValueError) as e:
        _die_input(f"taxjson.toml: {e}", "Nothing was moved.")
    if _was_templated:
        text = _templated(text)
    # The folders the moved taxjson.toml names must be usable from the
    # year folder (a path that would leave the folder holding it ...).
    bad = _PL.setting_problems(dest, new_settings)
    if bad:
        _die_input(f"{year}/taxjson.toml would name a folder it cannot "
                   f"use:", *bad, "Nothing was moved.")
    # A moved link (ticker.map -> maps/current.map, GitHub #44), and
    # one inside a moved folder (holdings/current.toml ->
    # ../inputs/margin/pos.toml), is made again at its new place, still
    # naming the same file: a relative one from there, an absolute one
    # into an item that moves at the item's new place (backlog
    # TJ-0699). A link that still names the same file is left alone.
    links: List[Tuple[Path, Path]] = []
    for a, b in moves:
        if a.is_symlink():
            links.append((a, b))
        elif a.is_dir():
            for _dp, _dns, _fns in _os.walk(a):
                for _n in _dns + _fns:
                    _q = Path(_dp) / _n
                    if _q.is_symlink():
                        links.append((_q, b / _q.relative_to(a)))
    relinks: Dict[Path, str] = {}
    for a, b in links:
        try:
            tgt = _os.readlink(a)
        except OSError:
            continue
        if _os.path.isabs(tgt):
            new_tgt = _moved_to(tgt) or tgt
        else:
            old_t = _os.path.normpath(_os.path.join(str(a.parent), tgt))
            new_tgt = _os.path.relpath(_moved_to(old_t) or old_t,
                                       str(b.parent))
            if new_tgt == _os.path.normpath(tgt):
                new_tgt = tgt
        if new_tgt != tgt:
            relinks[b] = new_tgt
    # A link that stays (in the shared inputs/, at the top) and names
    # something that moves names it at its new place (backlog TJ-1082):
    # the next run refused it as dangling. stay_moved: the new place.
    _moved_items = {a for a, _b in moves}
    stay_moved: Dict[Path, str] = {}
    for _dp, _dns, _fns in _os.walk(root):
        _here = Path(_dp)
        _dns[:] = [n for n in _dns if _here / n not in _moved_items
                   and not (_here == root and n == ".git")]
        for _n in _dns + _fns:
            _q = _here / _n
            if _q in _moved_items or not _q.is_symlink():
                continue
            try:
                tgt = _os.readlink(_q)
            except OSError:
                continue
            _new = _moved_to(tgt if _os.path.isabs(tgt) else _os.path.normpath(
                _os.path.join(_dp, tgt)))
            if _new is None:
                continue
            stay_moved[_q] = _new
            relinks[_q] = (_new if _os.path.isabs(tgt)
                           else _os.path.relpath(_new, _dp))
            if not _os.access(_dp, _os.W_OK | _os.X_OK):
                _die_input(f"{_PL.shown(_q, root)} links to {tgt}, which "
                           f"moves into {year}/, and its folder is read-only "
                           f"— the link cannot be made again",
                           f"Make {_PL.shown(_here, root)}/ writable first. "
                           f"Nothing was moved.")
    gi_new = _to_years_preflight(root)

    def _say_wrapped(line: str) -> None:
        # Wrapped at the width, later lines flush-left (backlog TJ-0190).
        for _ln in _out_wrap(line, indent="", hang=""):
            print(_ln)
    _say_wrapped(f"{'Would move' if dry_run else 'Moving'} into {year}/: "
                 + ", ".join(_PL.shown(a, root) for a, _b in moves))
    if _frozen:
        _nf = len([a for a, _b in _frozen_copies if a.is_dir()])
        _say_wrapped(f"{year} is filed with its inputs frozen: "
                     f"{'would get' if dry_run else 'gets'} its own copy "
                     f"({_nf} account folder(s)"
                     + (", tobase.map" if _tob else "") + ") and keeps "
                     f"reading it; the shared ones here serve the years "
                     f"to come")
        _say_wrapped(f"{year}/taxjson.toml: [settings] {_PL.EXPORTS_KEY} = "
                     f"\"../{_PL.EXPORTS}\""
                     + (f"; {', '.join(fixed)} (one level down)" if fixed
                        else ""))
    else:
        _say_wrapped(f"{year}/taxjson.toml: [settings] {_PL.INPUTS_KEY} = "
                     f"\"../{_PL.INPUTS}\", {_PL.EXPORTS_KEY} = "
                     f"\"../{_PL.EXPORTS}\""
                     + (f", {_PL.TOBASE_KEY} = \"{_PL.SHARED_TOBASE}\" "
                        f"(tobase.map stays here, read by every year)"
                        if _tob else "")
                     + (f"; {', '.join(fixed)} (one level down)" if fixed
                        else ""))
    if dry_run:
        for b in stay_moved:
            _say_wrapped(f"{_PL.shown(b, root)} would link to {relinks[b]} "
                         f"(the same file)")
        _out.put("Dry run: nothing moved.")
        return

    def _relink(b: Path, tgt: str) -> None:
        # (a new link beside it, renamed over the moved one)
        _tmp = b.with_name(f".{b.name}.relink-{_os.getpid()}")
        if _os.path.lexists(_tmp):
            _os.unlink(_tmp)
        _os.symlink(tgt, _tmp)
        _os.replace(_tmp, b)
    dest.mkdir(mode=0o700)
    for a, b in moves:
        b.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _os.replace(a, b)
    for b, tgt in relinks.items():
        _relink(b, tgt)
        _say_wrapped(f"{_PL.shown(b, root)} links to {tgt} (the same "
                     f"file, from {_PL.shown(b.parent, root)}/)")
    for a, b in _frozen_copies:
        if a.is_dir():
            shutil.copytree(a, b, symlinks=True)
            # (a copy sits elsewhere: a relative link just re-pointed
            # names the moved file from there)
            for _q, _new in stay_moved.items():
                if a in _q.parents:
                    _c = b / _q.relative_to(a)
                    _relink(_c, _new if _os.path.isabs(relinks[_q])
                            else _os.path.relpath(_new, _c.parent))
            # (a relative link to what neither moves nor is copied names
            # it one level down too: backlog TJ-1248. A link into a
            # copied folder reads the year's own copy, as written.)
            for _dp, _dns, _fns in _os.walk(a):
                for _n in _dns + _fns:
                    _q = Path(_dp) / _n
                    if not _q.is_symlink() or _q in stay_moved:
                        continue
                    _t = _os.readlink(_q)
                    _at = _os.path.normpath(_os.path.join(_dp, _t))
                    if _os.path.isabs(_t) or any(
                            _at == str(_c) or _at.startswith(f"{_c}{_os.sep}")
                            for _c, _b in _frozen_copies):
                        continue
                    _c = b / _q.relative_to(a)
                    _mode = _c.parent.stat().st_mode
                    _c.parent.chmod(_mode | 0o700)
                    _relink(_c, _os.path.relpath(_at, _c.parent))
                    _c.parent.chmod(_mode)
                    _say_wrapped(f"{_PL.shown(_c, root)} links to "
                                 f"{_os.readlink(_c)} (the same file, from "
                                 f"{_PL.shown(_c.parent, root)}/)")
        else:
            b.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(a, b)
    # The user's files, written the way user files are (a link inside
    # the project stays, its target written; checked above before
    # anything moved: _to_years_preflight, backlog TJ-0799).
    write_user_file(dest / "taxjson.toml", text, root, backup=False)
    if gi_new is not None:
        write_user_file(root / ".gitignore", gi_new, root, backup=False)
    # What stays at the top besides the shared folders: no year reads
    # it (a note of the user's, a script, a stray export).
    _kept = {_PL.INPUTS, _PL.EXPORTS, _PL.SNAPSHOTS, str(year), ".git",
             ".gitignore", ".gitattributes", _PL.TOBASE_MAP}
    for a, b in moves:
        if b.is_symlink():
            # (the folder a moved link names stays: a year reads it)
            try:
                _kept.add(b.resolve().relative_to(root).parts[0])
            except (OSError, ValueError, IndexError):
                pass
    # (and the folder a link kept in a shared folder or in the year reads:
    # TJ-1264)
    for _d in (_PL.INPUTS, _PL.EXPORTS, _PL.SNAPSHOTS, str(year)):
        for _dp, _dn, _fn in _os.walk(root / _d):
            for _n in (*_dn, *_fn):
                _q = Path(_dp) / _n
                if _q.is_symlink():
                    try:
                        _kept.add(_q.resolve().relative_to(root).parts[0])
                    except (OSError, ValueError, IndexError):
                        pass
    try:
        left = sorted(e.name for e in root.iterdir()
                      if e.name not in _kept)
    except OSError:
        left = []
    if left:
        _say_wrapped(f"Left at the top (no year reads them; move what "
                     f"belongs to {year} into {year}/): " + ", ".join(left))
    _say_wrapped(f"Done. `tjs -C {year} run` rebuilds the books there; "
                 f"`tjs new-year {year + 1}` here starts the next year. "
                 f"The files are moved: commit the move (`git add -A` "
                 f"records them as renames, keeping their history).")


def cmd_migrate(args: argparse.Namespace) -> None:
    """`tjs migrate`: _migrate_steps, then each project it covers
    records the oldest taxjson its layout now needs (`[settings]
    requires_taxjson`, lib/requires; never lowered). A frozen filed
    year's taxjson.toml migrate edited has the edit recorded in its
    lock (_record_migrate_edits), so check-filed explains it instead of
    calling it a changed input (backlog TJ-0231)."""
    root = Path(args.dir).resolve()
    dry = bool(getattr(args, "dry_run", False))
    before = [] if dry else _frozen_configs(root)
    _migrate_steps(args)
    if not dry:
        _raise_requirements(root, ("missing_history_tt",), all_years=True)
        _record_migrate_edits(
            root, before, "tjs migrate"
            + (" --to-years" if getattr(args, "to_years", False) else ""))


def _frozen_configs(root: Path) -> List[Tuple[int, Path, str, Any]]:
    """(year, project, digest, run part) of the taxjson.toml of each
    filed year with frozen inputs `tjs migrate` in `root` may
    edit: the project itself, or the year folders of the folder
    holding them (lib/checklist.run_config_state)."""
    from taxjson.lib.checklist import run_config_state
    dirs: List[Path] = []
    if _PL.has_config(root):
        dirs.append(root)
    top = _PL.multi_root(root)
    if top is not None:
        dirs += [d for _y, d in _PL.year_dirs(top) if d != root]
    out: List[Tuple[int, Path, str, Any]] = []
    for d in dirs:
        st = run_config_state(d / _PL.CONFIG)
        if st is None or not isinstance(st[1], dict):
            continue
        try:
            y = int((st[1].get("settings") or {}).get("year"))
        except (TypeError, ValueError):
            continue
        if _frozen_record(d, y) is not None:
            out.append((y, d, st[0], st[1]))
    return out


def _record_migrate_edits(root: Path,
                          before: List[Tuple[int, Path, str, Any]],
                          how: str) -> None:
    """Record in each frozen year's lock the edit `how` (`tjs
    migrate`) made to its taxjson.toml — `before`, _frozen_configs
    taken before it ran: the digest the file has now and the keys it
    changed, under frozen.migrated. Only a file that still was as the
    year was closed (or as an earlier migrate left it): an edit of
    anyone else stays a changed input (backlog TJ-0231)."""
    import json as _json
    from datetime import date as _date
    from taxjson.lib.checklist import (run_config_keys_changed,
                                       run_config_state)
    from taxjson.lib.safe_write import write_atomic
    out_doc = _out.Doc(exact=True, width_=0, live=True)
    for y, d, digest, part in before:
        # `migrate --to-years` moved the project into its year folder.
        here = next((c for c in (d, d / str(y))
                     if (c / _PL.CONFIG).is_file()
                     and (c / "filed" / f"{y}.json").is_file()), None)
        if here is None:
            continue
        now = run_config_state(here / _PL.CONFIG)
        if now is None or now[0] == digest:
            continue
        lock = here / "filed" / f"{y}.json"
        try:
            doc = _json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fz = doc.get("frozen") if isinstance(doc, dict) else None
        if not isinstance(fz, dict) or not isinstance(fz.get("inputs"),
                                                      dict):
            continue
        prev = fz.get(_MIGRATED) if isinstance(fz.get(_MIGRATED),
                                               dict) else {}
        if digest not in (fz["inputs"].get(_PL.CONFIG),
                          prev.get(_PL.CONFIG)):
            continue                    # already changed: still drift
        keys = sorted(set(prev.get("keys") or [])
                      | set(run_config_keys_changed(part, now[1])))
        fz[_MIGRATED] = {"by": how, "on": _date.today().isoformat(),
                         _PL.CONFIG: now[0], "keys": keys}
        try:
            write_atomic(lock, _json.dumps(doc, indent=2, sort_keys=True)
                         + "\n")
        except OSError as e:
            _say("warning", f"cannot record migrate's edit of {y}'s "
                 f"frozen taxjson.toml in filed/{y}.json: "
                 f"{e.strerror or e}",
                 "`tjs check-filed` will call the edit a changed "
                 "frozen input.", prog=_cmd_prog())
            continue
        for _ln in _out_wrap(
                f"filed/{y}.json: records this edit of {y}'s frozen "
                f"taxjson.toml ({', '.join(keys)}) as migrate's own — "
                f"`tjs check-filed` explains it, not a changed "
                f"input", indent="", hang=""):
            out_doc.line(_ln)


def _migrate_steps(args: argparse.Namespace) -> None:
    """`tjs migrate [--dry-run]`: move an old project's per-purpose
    files into ticker.map (QUOTE / CRYPTO / EXTRACT / T1135 lines) and
    taxjson.toml ([estimate] amt_carryover, [carryover] claimed,
    [[capital_gains_dividends]], [[distributions]]) — appended, never
    rewritten — and rename each old file to <name>.migrated. A leftover
    tv_exchange.map (the removed TradingView export) is only renamed.
    Refuses (exit 2, nothing written) when a file cannot be converted or
    the target already holds a conflicting entry: every phase is planned,
    and so checked, before the first one writes (backlog TJ-0300). A
    phase refused after an earlier one wrote names what was done."""
    from taxjson.lib import migrate as M
    root = Path(args.dir).resolve()
    if getattr(args, "to_years", False):
        _migrate_to_years(root, args.dry_run)
        return
    dry = bool(getattr(args, "dry_run", False))
    has_cfg = _PL.has_config(root)
    # This project's own phases first: an old file that cannot be
    # converted (amt_carryover.txt with a line that is no `YEAR AMOUNT`)
    # stops migrate before the shared tobase.map below rewrites every
    # year's taxjson.toml — it said "Nothing was changed." after it had
    # (backlog TJ-0300).
    pl, mhm = _migrate_plan(root) if has_cfg else (None, None)
    # A multi-year project's per-year tobase.map copies become one file
    # every year reads (in the folder holding the years, or any year).
    _top = _PL.multi_root(root)
    _tob_done = (_migrate_shared_tobase(_top, args) if _top is not None
                 else False)
    # What a later refusal leaves done (not dry: the phase above either
    # wrote or stopped).
    done: List[str] = ([f"the shared {_PL.TOBASE_MAP} (above)"]
                       if _tob_done and not dry else [])
    if not has_cfg:
        if not _tob_done:
            _say("note", "nothing to migrate here (no year folder holds "
                 "a tobase.map of its own)", prog=f"{_PROG} migrate",
                 file=sys.stdout)
        return
    if done and pl.toml_text is not None:
        # The shared tobase.map just set [settings] tobase_map in this
        # taxjson.toml: plan again on the file as it now is, or the
        # migrated taxjson.toml would drop that setting.
        try:
            pl = M.plan(root)
        except M.MigrateError as e:
            _die_input(str(e), *_migrate_done_note(done))
    if not pl.names and mhm is None and _tob_done:
        return
    if not pl.names and mhm is None:
        _say("note", "nothing to migrate in this project",
             *([f"None of {', '.join(M.LEGACY_FILES)} is here."]
               if _details(args) else []), prog=f"{_PROG} migrate",
             file=sys.stdout)
        return
    if not pl.names:
        _migrate_missing_history(root, mhm, args, done)
        return
    doc = _out.Doc(exact=True, width_=0, live=True)
    doc.line(f"tjs migrate{' --dry-run' if dry else ''}: {root}")
    from taxjson.lib.out import show_listed
    for ln in pl.summary(dry):
        show_listed(ln, sys.stdout)
    if dry:
        if pl.map_append:
            doc.line("\n  would append to ticker.map:")
            for ln in pl.map_append.strip("\n").splitlines():
                doc.line(f"    {ln}")
        if pl.toml_text is not None:
            import difflib
            doc.line("\n  would change taxjson.toml:")
            for ln in difflib.unified_diff(
                    pl.toml_before.splitlines(), pl.toml_text.splitlines(),
                    "taxjson.toml", "taxjson.toml (migrated)", lineterm="",
                    n=1):
                doc.line(f"    {ln}")
        doc.line("\n  dry run: nothing was written")
        if mhm is not None:
            _migrate_missing_history(root, mhm, args)
        return
    try:
        M.apply(pl)
    except OSError as e:
        if not e.filename:
            _die_input(f"could not write the migration: {e}",
                       *_migrate_done_note(done))
        # The file and the fix (a ticker.map or taxjson.toml made
        # read-only is refused before anything is written: TJ-0107).
        from taxjson.lib.cli_diag import cannot_write
        _h, _fix = cannot_write(_config_shown(Path(e.filename)), e,
                                e.filename)
        _die_input(f"could not write the migration: {_h}",
                   *([_fix] if _fix else []), *_migrate_done_note(done))
    done.append("the old files moved into ticker.map / taxjson.toml "
                "(above)")
    doc.line("\nDone.")
    if _details(args):
        for _ln in _out_wrap(
                "Review ticker.map and taxjson.toml and commit them (in a "
                "git repository, `git rm` the old files too); delete the "
                "*.migrated files once you are satisfied; run `tjs run` "
                "to rebuild.", indent="  "):
            doc.line(_ln)
    else:
        doc.line("  Review and commit ticker.map and taxjson.toml, then "
                 "`tjs run` (more: --details).")
    if mhm is not None:
        _migrate_missing_history(root, mhm, args, done)


def _migrate_plan(root: Path) -> Tuple[Any, Any]:
    """`tjs migrate`: this project's two plans — the old per-purpose
    files (lib/migrate.plan) and missing_history.json (lib/history_
    migration.plan_missing_history_migration, None when there is none) —
    made before any phase writes, so a refusal here is true to its
    "Nothing was changed." (backlog TJ-0300)."""
    from taxjson.lib import migrate as M
    from taxjson.lib import missing_history as MH
    from taxjson.lib import history_migration as HMG
    try:
        pl = M.plan(root)
    except M.MigrateError as e:
        _die_input(str(e), "Nothing was changed.")
    try:
        mhm = HMG.plan_missing_history_migration(root)
    except MH.MissingHistoryFileConflict as e:
        _die_input(str(e), "Nothing was changed.")
    except MH.TtOpeningError as e:
        _die_input("an OPENING cost=unknown line cannot be read — fix it "
                   "first:", *str(e).splitlines(), "Nothing was changed.")
    except (OSError, ValueError) as e:
        _die_input(str(e), "Nothing was changed.")
    return pl, mhm


def _migrate_done_note(done: List[str]) -> List[str]:
    """The line a migrate refusal ends with once an earlier phase wrote:
    what that phase did, kept (backlog TJ-0300); none before any did."""
    if not done:
        return []
    return [f"Done before it, and kept: {'; '.join(done)}. Nothing else "
            f"was changed."]


def _migrate_shared_tobase(top: Path, args: argparse.Namespace) -> bool:
    """`tjs migrate` in a multi-year project: the year folders'
    tobase.map copies (the layout before v0.27.1) become one tobase.map
    in `top` that every year reads (`[settings] tobase_map =
    "../tobase.map"`, lib/tobase_map.plan_shared). Identical copies:
    written at once, each copy kept as tobase.map.bak. Copies that
    differ: listed (the lines of yours only another year has), and
    nothing is written without --write (the newest year's file wins).
    False when no year holds a copy."""
    from taxjson.lib import tobase_map as TB
    dry = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "write", False))
    try:
        plan = TB.plan_shared(top)
    except (OSError, ValueError) as e:
        _die_input(f"cannot read the year folders' tobase.map: {e}",
                   "Nothing was changed.")
    if not plan.work:
        return False
    if plan.problems:
        # Refused before the plan is shown: its lines would name the
        # file just refused as "the one kept" (backlog TJ-0050).
        _die_input("the year folders' tobase.map cannot be made one file:",
                   *plan.problems, "Nothing was changed.")
    years = ", ".join(str(y) for y in sorted(plan.copies))
    # Every line below says what --write does when it will not write:
    # a dry run, or copies that differ without --write (backlog
    # TJ-0086).
    would = dry or (not plan.identical and not force)
    doc = _out.Doc(exact=True, width_=0, live=True)
    # Wrapped like the plan's lines: a long folder path passed the
    # width (backlog TJ-0233).
    for _ln in _out_wrap(
            f"tobase.map: one file every year reads, in {top} "
            f"([settings] {_PL.TOBASE_KEY} = \"{_PL.SHARED_TOBASE}\")",
            hang="  "):
        doc.line(_ln)
    for _ln in _out_wrap(
            f"The copies in {years}"
            + (f" and the shared {TB.TOBASE_MAP}" if plan.target.is_file()
               else "")
            + (" are identical: one file, the copies removed (each kept "
               "as tobase.map.bak)." if plan.identical else
               f" differ: {plan.source} (the newest year's) is the one "
               f"kept."), indent="  "):
        doc.line(_ln)
    for where, rules in sorted(plan.user_lines.items()):
        for _ln in _out_wrap(
                f"Lines of yours only {where} has (not carried over — "
                f"add each you still need to the shared {TB.TOBASE_MAP}, "
                f"or that year's ticker.map):", indent="  ", hang="    "):
            doc.line(_ln)
        for r in rules:
            doc.line(f"    {r}")
    for where, n in sorted(plan.other_lines.items()):
        for _ln in _out_wrap(
                f"{where}: {n} more line(s) of another master version "
                f"(`tjs update-tobase-map` brings the shared file in "
                f"step with this taxjson's master).", indent="  ",
                hang="    "):
            doc.line(_ln)
    if plan.set_years:
        for _ln in _out_wrap(
                f"{'would set' if would else 'set'} [settings] "
                f"{_PL.TOBASE_KEY} = \"{_PL.SHARED_TOBASE}\" in "
                + ", ".join(f"{y}/taxjson.toml" for y in plan.set_years),
                indent="  ", hang="    "):
            doc.line(_ln)
    if plan.frozen:
        # A filed year's frozen copy is its own by design (as `tjs
        # years` says it): never pointed at the shared file (TJ-0086).
        _one = len(plan.frozen) == 1
        for _ln in _out_wrap(
                f"Not changed: {', '.join(str(y) for y in plan.frozen)} "
                f"read{'s its' if _one else ' their'} own frozen copy "
                f"(filed: close-year froze it).", indent="  ",
                hang="    "):
            doc.line(_ln)
    if plan.without:
        for _ln in _out_wrap(
                f"Not changed: {', '.join(str(y) for y in plan.without)} "
                f"read no tobase.map (add the setting there to read the "
                f"shared one).", indent="  ", hang="    "):
            doc.line(_ln)
    if dry:
        doc.line("  dry run: nothing was written")
        return True
    if not plan.identical and not force:
        _die("the year folders' tobase.map copies differ (listed above) — "
             "nothing was written for them",
             f"Pass --write to keep {plan.source} as the one file every "
             f"year reads (the lines listed above are not carried over).")
    from taxjson.lib.safe_write import OutsideLinkError
    try:
        done, baks = TB.apply_shared(plan)
    except _PL.LayoutError as e:
        _die_input(str(e), "Nothing was changed.")
    except (OSError, OutsideLinkError) as e:
        _die_input(f"could not write the migration: {e}")
    for b in baks:
        doc.line(f"  removed {_PL.shown(b.with_name(TB.TOBASE_MAP), top)} "
                 f"(kept as {_PL.shown(b, top)})")
    for _ln in _out_wrap(
            f"Done: {TB.TOBASE_MAP} here is the one file "
            + ", ".join(str(y) for y in sorted(set(done) | set(plan.sharing)))
            + " read. `tjs update-tobase-map` in any year folder "
            "updates it for every year; commit the change (in git, `git "
            "rm` the copies).", indent="  ", hang="  "):
        doc.line(_ln)
    return True


def _migrate_missing_history(root: Path, mhm: Any,
                             args: argparse.Namespace,
                             done: Optional[List[str]] = None) -> None:
    """`tjs migrate`, missing_history.json: each entry becomes a
    dated line `OPENING <date> <SYMBOL> <qty> cost=unknown` in
    inputs/<account>/missing_history.tt, its quantity what the project's
    run opens for it (lib/history_migration.plan_missing_history_
    migration); with exports shared by every year the year folders'
    files are merged into one record. Projects that disagree (another
    quantity, an entry only some list) are listed and nothing is written
    unless --write (each entry as the newest project listing it sizes
    it); entries that open nothing anywhere are dropped, said. Then each
    missing_history.json is renamed <name>.migrated. `done`: what the
    phases before this one wrote, named by a refusal here (TJ-0300) —
    an entry not converted keeps them (it needs `tjs run`, which an
    old file not yet migrated would stop)."""
    from taxjson.lib import missing_history as MH
    from taxjson.lib import history_migration as HMG
    dry = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "write", False))
    plan = mhm.plan
    names = ", ".join(_PL.shown(v.file, root) for v in mhm.views)
    doc = _out.Doc(exact=True, width_=0, live=True)
    doc.line(f"\n{MH.MISSING_HISTORY_FILE} -> dated .tt lines "
             f"(OPENING <date> <SYMBOL> <qty> cost=unknown): {names}")
    shared = _PL.shared_inputs(root)
    for acct in sorted(plan.add):
        doc.line(f"  {'would write' if dry else 'write'} "
                 f"{_PL.shown(plan.files[acct], root)}"
                 + (" (shared by every year)" if shared else "") + ":")
        for ln in plan.add[acct]:
            doc.line(f"    {ln.symbol} {ln.quantity:g}  (dated {ln.date}, the "
                     f"day before the account's first row)")
    if mhm.agreed and len(mhm.views) > 1:
        doc.line(f"  {len(mhm.agreed)} entr"
                 f"{'y' if len(mhm.agreed) == 1 else 'ies'} every year lists "
                 f"the same: one line each")
    for ln, old in plan.same:
        doc.line(f"  {ln.account} {ln.symbol}: already opened by {old.where} "
                 f"with the same quantity — not written again")
    _diff_tt = MH.tt_differ_lines(plan)
    if mhm.differ or _diff_tt:
        doc.line("  The projects disagree on (for you to decide):")
        for d in mhm.differ:
            doc.line(f"    - {d}")
        for d in _diff_tt:
            doc.line(f"    - {d}")
    if mhm.stale:
        doc.line("  Dropped (opens nothing: no rows, or the rows never go "
                 "short):")
        for d in mhm.stale:
            doc.line(f"    - {d}")
    if mhm.nodate:
        doc.line(f"  Cannot convert (no books for the account in a year "
                 f"folder listing it — run `tjs run` there): "
                 f"{', '.join(mhm.nodate)}")
    if dry:
        doc.line("\n  dry run: nothing was written")
        return
    if mhm.nodate:
        # Never archive a file whose entries were not all converted
        # (issue #25): the user would lose the declaration.
        _die(f"{len(mhm.nodate)} entr"
             f"{'y' if len(mhm.nodate) == 1 else 'ies'} cannot be "
             f"converted (listed above) — nothing was written or renamed"
             + (" for them" if done else ""),
             "Kept: " + ", ".join(_PL.shown(p, root)
                                  for p in sorted(mhm.unconverted))
             + ". Run `tjs run` in the year folder whose accounts "
             "include the account, then `tjs migrate` again.",
             *_migrate_done_note(done or []))
    if mhm.differ and not force:
        _die("the year folders' missing_history.json files disagree "
             "(listed above) — nothing was written for them",
             "Decide each, or pass --write to take, for each entry, the "
             "newest year's view (the list above says which).",
             *_migrate_done_note(done or []))
    try:
        written, renamed = HMG.apply_missing_history_migration(mhm)
    except OSError as e:
        _die_input(f"could not write the migration: {e}",
                   *_migrate_done_note(done or []))
    for p in renamed:
        doc.line(f"  renamed {_PL.shown(p.with_name(p.name.split('.migrated')[0]), root)}"
                 f" -> {p.name}")
    doc.line("\nDone.")
    for _ln in _out_wrap(
            "Review the .tt lines (the date and quantity of each are "
            "yours to correct) and commit them; delete the *.migrated "
            "files once you are satisfied; run `tjs run`"
            + (" in each year folder" if shared else "") + ".",
            indent="  "):
        doc.line(_ln)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's command, for the `tjs` parser."""
    p_mig = sub.add_parser(
        "migrate",
        help="Move an older project's files into the new layout",
        description="Move an old project's yf_ticker.map, "
             "crypto_ticker.map, ticker_extraction_overrides.txt, "
             "t1135.map, amt_carryover.txt, claimed_losses.txt, "
             "capital_gains_dividends.map and distributions.map into "
             "ticker.map / taxjson.toml (appended; each old file is "
             "renamed <name>.migrated). Every other command stops while "
             "one of those files is present. A leftover tv_exchange.map "
             "(the removed TradingView export) is only renamed. In a "
             "multi-year project (the folder holding the years, or a "
             "year folder) the years' tobase.map copies become one "
             "tobase.map beside the year folders that every year reads "
             "([settings] tobase_map = \"../tobase.map\"; each copy kept "
             "as tobase.map.bak).")
    p_mig.add_argument("--dry-run", action="store_true",
                       help="Show what would be appended and moved; "
                            "write nothing")
    p_mig.add_argument("--write", action="store_true",
                       help="missing_history.json of year folders that "
                            "disagree: write anyway, each entry as the "
                            "newest year listing it sizes it; tobase.map "
                            "copies that differ: keep the newest year's "
                            "as the one file")
    p_mig.add_argument("--to-years", action="store_true",
                       help="Turn this single-folder project into one "
                            "folder of exports for every year: inputs/ "
                            "stays here, shared, and the project moves "
                            "into the YYYY/ folder of its year "
                            "(docs/getting-started.md)")
    p_mig.set_defaults(func=cmd_migrate)

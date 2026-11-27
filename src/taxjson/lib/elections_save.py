"""The one writer of an account's elections file (backlog TJ-5411).

`update` is lib/corp_actions `Manifest.update` (load, change and save as
one locked step, TJ-0544) with, before the save, the requires_taxjson of
each project that reads a saved elections.json raised to the release that
reads it (lib/requires `raise_for_elections`): an older taxjson then
refuses the project instead of reading no elections, asking them again or
booking a sheltered account's event by the zero default. Every command
saves through it: `tjs elections --set` / `--redo` / `--reset`
(cli/elections `_update_manifest`) and taxjson-corp-actions, the run's
prompt and auto-elections (bin/taxjson_corp_actions `_update_or_die`).
A year frozen with its elections as manifest.json is saved under that
name and raises nothing. A filed year's taxjson.toml the save raised is
recorded in its lock as the save's own edit (`_record_raises`), as `tjs
migrate` records its own (TJ-0231): `tjs check-filed` explains it, never
a changed input.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from taxjson.lib.corp_actions import Manifest, ManifestError

Raised = List[Tuple[Path, str]]
# The lock's record of a frozen taxjson.toml's edit by taxjson itself:
# pipeline/stages `_MIGRATED`, which check-filed reads (lib never
# imports pipeline).
LOCK_EDIT = "migrated"


def update(path: Any, change: Callable[[Manifest], Any],
           by: str = "tjs elections") -> Tuple[Manifest, Any, Raised]:
    """Manifest.update of the elections file `path` with `change`, each
    reading project's requires_taxjson raised under the file's lock
    before the save (a filed year's raise recorded in its lock as `by`'s
    edit): (the manifest as saved, the change's value, the [(project
    folder, value)] written). A taxjson.toml that cannot be read or
    raised, or a lock that cannot record the raise, is a ManifestError
    naming the file, and nothing is saved."""
    from taxjson.lib.requires import raise_for_elections
    path = Path(path)
    raised: Raised = []

    def _change(man: Manifest) -> Any:
        result = change(man)
        if result:
            folder = path.parent.parent.parent
            before = _locked(folder)
            try:
                raised[:] = raise_for_elections(path)
            except ValueError as e:
                raise ManifestError(str(e)) from e
            _record_raises(raised, before, by, folder)
        return result

    man, result = Manifest.update(path, _change)
    return man, result, raised


def _locked(folder: Path) -> Dict[Path, Tuple[Path, bytes, str, Any]]:
    """{project: (its lock, its taxjson.toml's bytes, digest and run
    part)} of each filed year among the projects of `folder` (it, its
    year folders): the state a raise starts from."""
    from taxjson.lib import project_layout as PL
    from taxjson.lib.checklist import run_config_state
    out: Dict[Path, Tuple[Path, bytes, str, Any]] = {}
    for d in [folder] + [d for _y, d in PL.year_dirs(folder)]:
        st = run_config_state(d / PL.CONFIG)
        if st is None or not isinstance(st[1], dict):
            continue
        try:
            lock = d / "filed" / f"{int(st[1]['settings']['year'])}.json"
            if lock.is_file():
                out[d] = (lock, (d / PL.CONFIG).read_bytes(), st[0], st[1])
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return out


def _record_raises(raised: Raised,
                   before: Dict[Path, Tuple[Path, bytes, str, Any]],
                   by: str, folder: Path) -> None:
    """Record in each filed year's lock the raise of its taxjson.toml
    (frozen.migrated: the digest the file has now, the keys changed, `by`
    and the date) when the file was as the year was closed or as
    taxjson's last recorded edit left it; an edit of anyone else stays a
    changed input (backlog TJ-0231). One record: after an earlier one
    (migrate's), the keys are both edits' and `by` / `on` this one's. A lock that cannot be written puts
    taxjson.toml back: ManifestError naming it (from `folder`), nothing
    saved."""
    from datetime import date
    from taxjson.lib import project_layout as PL
    from taxjson.lib.checklist import run_config_keys_changed, run_config_state
    from taxjson.lib.safe_write import write_atomic, write_user_file
    for d, _new in raised:
        if d not in before:
            continue
        lock, raw, digest, part = before[d]
        now = run_config_state(d / PL.CONFIG)
        try:
            doc = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fz = doc.get("frozen") if isinstance(doc, dict) else None
        if now is None or not isinstance(fz, dict) \
                or not isinstance(fz.get("inputs"), dict):
            continue
        prev = fz.get(LOCK_EDIT) if isinstance(fz.get(LOCK_EDIT),
                                               dict) else {}
        if digest not in (fz["inputs"].get(PL.CONFIG), prev.get(PL.CONFIG)):
            continue                    # already changed: still drift
        fz[LOCK_EDIT] = {"by": by, "on": date.today().isoformat(),
                         PL.CONFIG: now[0], "keys": sorted(
                             set(prev.get("keys") or [])
                             | set(run_config_keys_changed(part, now[1])))}
        try:
            write_atomic(lock, json.dumps(doc, indent=2, sort_keys=True)
                         + "\n")
        except OSError as e:
            write_user_file(d / PL.CONFIG, raw, d, backup=False)
            raise ManifestError(
                f"cannot record in {PL.shown(lock, folder)} the taxjson "
                f"version {PL.shown(d / PL.CONFIG, folder)} needs: "
                f"{e.strerror or e}") from e

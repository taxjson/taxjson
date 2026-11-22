"""`tjs years`: each year project of a multi-year folder with its state
(filed, its lock, its last run, what changed since). Above
lib/project_layout, whose layout it reads, lib/checklist and
lib/filed_locks; out of project_layout, which the parsers and the
core's modules import (step 8 of the architecture plan: lib's import
cycle)."""

import datetime as _dt
import os
from pathlib import Path
from typing import Any, Dict

from taxjson.lib.project_layout import (
    INPUTS_KEY, TOBASE_KEY, TOBASE_MAP, LayoutError, _resolved, compare, filed_check,
    frozen_year, lock_missing_frozen, lock_missing_text, read_config, shared_inputs, shown,
    tobase_setting, year_dirs)
from taxjson.lib.fingerprint_record import FINGERPRINT_FILE
from taxjson.project.settings import settings_of


def years_report(folder: Path) -> Dict[str, Any]:
    """`tjs years --json`: each year project of the multi-year folder
    with its state (docs/settings.md, `tjs years --json`)."""
    from taxjson.lib import checklist as CL
    yrs = year_dirs(folder)
    newest = yrs[-1][1] if yrs else None
    out_years = []
    for y, d in yrs:
        rec: Dict[str, Any] = {"year": y, "folder": d.name,
                               "filed": False, "closed_at": None,
                               "partial_lock": False, "totals": None,
                               "last_run": None, "stale": None,
                               "changed": None, "problem": None,
                               "differs_from_newest": None,
                               "tobase_map": None, "tobase_shared": False,
                               "tobase_frozen": False, "drifted": None,
                               "marks": {"done": 0, "forced": 0}}
        lock = d / "filed" / f"{y}.json"
        doc = None
        if lock.is_file():
            rec["filed"] = True
            try:
                from taxjson.lib.filed_locks import lock_damage, load_lock_json
                doc = load_lock_json(lock)
                # The one damage check every reader of a lock applies
                # (its carryforwards block too: a lock the next year's
                # estimate refuses was shown as a clean filed year,
                # 2026-10-25 audit year-end#6).
                bad = lock_damage(doc, y, f"filed/{y}.json")
                if bad:
                    # A damaged lock is named, never shown as a
                    # clean filed year (issue #49).
                    rec["problem"] = bad
                elif isinstance(doc, dict):
                    rec["closed_at"] = str(doc.get("closed_at") or "") or None
                    t = doc.get("totals")
                    if isinstance(t, dict):
                        rec["totals"] = {k: t.get(k) for k in (
                            "realized", "disallowed", "income") if k in t}
                        # Realized and disallowed as filed: the RETURN
                        # row close-year recorded, not the engine total
                        # a cent or two apart (backlog TJ-0192).
                        rr = doc.get("return_row")
                        if isinstance(rr, dict):
                            rec["totals"].update(
                                (k, rr[k]) for k in ("realized",
                                                     "disallowed")
                                if isinstance(rr.get(k), (int, float))
                                and not isinstance(rr.get(k), bool))
                        # ...and income as `tjs sum`'s taxable
                        # SUBTOTAL adds it (backlog TJ-0212).
                        from taxjson.lib.filed_locks import income_as_summed
                        _inc = income_as_summed(doc)
                        if _inc is not None and "income" in rec["totals"]:
                            rec["totals"]["income"] = _inc
                    from taxjson.lib.filed_locks import partial_year_note
                    rec["partial_lock"] = bool(partial_year_note(doc, y))
                    # The last run's drift check, never a recompute here
                    # (backlog TJ-0159).
                    rec["drifted"] = filed_check(d, y)
            except (OSError, ValueError):
                rec["problem"] = f"filed/{y}.json cannot be read"
        elif os.path.lexists(lock):
            # Something other than a file at the lock's path (a folder,
            # a link to nothing): a damaged lock, never an open year
            # (backlog TJ-0271), as the checklist's _lock_state says.
            rec["filed"] = True
            rec["problem"] = (f"filed/{y}.json cannot be read ("
                              + ("a directory" if lock.is_dir() else
                                 "a link to nothing" if lock.is_symlink()
                                 and not lock.exists() else "not a file")
                              + ") — restore the lock from git, or "
                              "remove it and `tjs close-year` after "
                              "filing")
        elif lock_missing_frozen(d, y):
            # A frozen year whose lock was deleted: filed, its lock to
            # restore, as a damaged one (backlog TJ-0263).
            rec["filed"] = True
            rec["problem"] = lock_missing_text(y)
        fp = d / "work" / FINGERPRINT_FILE
        try:
            rec["last_run"] = _dt.datetime.fromtimestamp(
                fp.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            pass
        try:
            cfg = read_config(d)
            if isinstance(doc, dict) and not rec["problem"]:
                # The country check every reader of a lock applies
                # (filed_locks.read_lock): a lock closed under the
                # other country's law was shown as a clean filed year
                # (backlog TJ-0069).
                from taxjson.lib.filed_locks import lock_country_problem
                from taxjson.lib.country import CountryError
                try:
                    rec["problem"] = lock_country_problem(
                        doc, settings_of(cfg), f"filed/{y}.json")
                except CountryError:
                    pass                # no country here to judge it by
                if rec["problem"]:
                    rec["totals"] = None    # the other country's figures
            if rec["last_run"] is not None:
                why = CL.inputs_changed(d, cfg)
                rec["stale"] = bool(why) if why is not None else None
                rec["changed"] = why or None
        except (LayoutError, OSError) as e:
            rec["problem"] = "; ".join(
                x for x in (rec["problem"], str(e).splitlines()[0]) if x)
        _year_tobase(d, y, folder, rec)
        if newest is not None and d != newest:
            try:
                c = compare(d, newest)
            except LayoutError as e:
                # Never "the same" when a file cannot be read (#56).
                rec["problem"] = rec["problem"] or str(e).splitlines()[0]
            else:
                rec["differs_from_newest"] = {
                    "map_rules": len(c["map_only_here"])
                    + len(c["map_only_there"]),
                    "tobase_rules": len(c["tobase_only_here"])
                    + len(c["tobase_only_there"]),
                    "accounts": len(c["accounts_only_here"])
                    + len(c["accounts_only_there"]),
                    "keys": len(c["keys"])}
        try:
            st = CL.load_state(d)
            for ov in (st.get("overrides") or {}).values():
                s = CL.mark_kind(ov)
                if s:
                    rec["marks"][s] += 1
        except Exception:                               # noqa: BLE001
            rec["problem"] = rec["problem"] or "checklist.json cannot be read"
        out_years.append(rec)
    # 2: `marks` counts "forced", not "skipped" (v0.35 renamed it under
    # 1; backlog TJ-0030).
    return {"schema_version": 2, "root": str(folder),
            "newest": yrs[-1][0] if yrs else None, "years": out_years}


def _year_tobase(d: Path, y: int, folder: Path, rec: Dict[str, Any]) -> None:
    """years_report, a year's tobase.map: the file it reads (its own
    frozen copy, the shared one its setting names, or its own), whether
    shared or frozen, and a frozen year naming the shared folders again
    (its problem), in `rec`."""
    try:
        p = tobase_setting(d)
    except LayoutError:
        p = None
    # The copy close-year froze: the filed year's own by design —
    # its file even beside a setting written back, which is the
    # year's problem (its run refuses the two; backlog TJ-0108).
    fz = frozen_year(d, y)
    own_frozen = fz and (d / TOBASE_MAP).is_file()
    rec["tobase_map"] = (f"{d.name}/{TOBASE_MAP}" if own_frozen
                         else shown(p, _resolved(folder))
                         if p is not None
                         else f"{d.name}/{TOBASE_MAP}"
                         if (d / TOBASE_MAP).is_file() else None)
    rec["tobase_shared"] = p is not None and not own_frozen
    rec["tobase_frozen"] = own_frozen
    back = [k for k, on in ((INPUTS_KEY, shared_inputs(d)),
                            (TOBASE_KEY, p is not None)) if on]
    if fz and back:
        # A frozen filed year naming the shared folders again reads
        # them, not its frozen copy (the run warns).
        rec["problem"] = "; ".join(x for x in (
            rec["problem"], f"frozen, but [settings] names "
            f"{', '.join(back)} again: delete "
            f"{'them' if len(back) > 1 else 'it'}") if x)

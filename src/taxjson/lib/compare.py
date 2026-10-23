"""`taxjson compare`: the current reports/ against a snapshot of an
earlier run.

A snapshot is a copy of reports/ with a record of what the books were
built from: the inputs fingerprint (lib/checklist.input_fingerprint,
name and SHA-256 of every file `taxjson run` reads) and each taxable
account's filing aggregates (taxjson_filed.aggregates_from_gains).
Three kinds:

- `prev` (work/compare/prev/): the run before. Every full `taxjson run`
  rotates the reports it is about to replace there (rotate_previous).
- a saved one (work/compare/<name>/, `taxjson compare --save NAME`): a
  baseline kept until `--delete NAME`, say before a batch of exports is
  replaced.
- `filed` (filed/<year>/reports/ and the lock's `frozen` record, kept by
  `taxjson close-year`): the reports as filed.

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
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SNAP_ROOT = "compare"           # under work/
PREV = "prev"
FILED = "filed"
VERSION = 1
SNAPSHOT_FILE = "snapshot.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RESERVED = (PREV, FILED)

# A report line that says when the file was written, not what it holds
# (`Generated:  2026-...` in a .rpt, `generated_at = ...` in a holdings
# TOML, `"generated_at": ...` in a radar JSON): never a difference.
_STAMP_RE = re.compile(r'^\s*("?generated(_at)?"?\s*[:=]|Generated:)', re.I)

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


def normalized(data: bytes) -> str:
    """A report's text without the lines that only date it."""
    text = data.decode("utf-8", errors="replace")
    return "".join(ln for ln in text.splitlines(keepends=True)
                   if not _STAMP_RE.match(ln))


def aggregates(root: Path, cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Each taxable account's filing aggregates from the books in
    work/ (the figures close-year locks); {} when they cannot be read
    (the files that differ still tell)."""
    from taxjson.bin.taxjson_filed import aggregates_from_gains
    from taxjson.lib.report_model import load_report_json, resolve_gains_files
    accounts = cfg.get("accounts") or {}
    year = (cfg.get("settings") or {}).get("year")
    out: Dict[str, Dict[str, Any]] = {}
    try:
        files = resolve_gains_files(Path(root) / "work")
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


def take(root: Path, cfg: Dict[str, Any], dest: Path, kind: str
         ) -> Optional[Dict[str, Any]]:
    """Snapshot reports/ into `dest` (replaced): dest/reports/<files>
    and dest/snapshot.json. None (nothing written, an old snapshot
    there kept) when reports/ holds no file; CompareError (nothing
    written) when reports/ or a file in it cannot be read."""
    from taxjson.lib.checklist import input_fingerprint, recorded_fingerprint
    root = Path(root)
    files = report_files(root / "reports")
    if not files:
        return None
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
           "accounts": aggregates(root, cfg),
           "reports": sorted(files)}
    from taxjson.lib.safe_write import write_atomic
    tmp = dest.with_name(dest.name + ".part")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "reports").mkdir(parents=True)
    for name, data in files.items():
        write_atomic(tmp / "reports" / name, data)
    write_atomic(tmp / SNAPSHOT_FILE,
                 json.dumps(doc, indent=1, sort_keys=True) + "\n")
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)
    return doc


def rotate_previous(root: Path, cfg: Dict[str, Any]) -> bool:
    """Called by a full `taxjson run` before it writes: the reports it
    is about to replace become the `prev` snapshot. True when kept;
    CompareError naming the path when reports/ cannot be read (the run
    warns that the previous reports were not kept)."""
    return take(Path(root), cfg, snap_dir(root, PREV), "previous run") \
        is not None


def save(root: Path, cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    name = check_name(name, saving=True)
    doc = take(Path(root), cfg, snap_dir(root, name), "saved")
    if doc is None:
        raise CompareError("reports/ holds no file to save — run "
                           "`taxjson run` first")
    return doc


def delete(root: Path, name: str) -> None:
    name = check_name(name)
    if name == FILED:
        raise CompareError("the reports as filed are kept by `taxjson "
                           "close-year` with the lock; they are not a "
                           "saved snapshot")
    d = snap_dir(root, name)
    if not d.is_dir():
        raise CompareError(f"no snapshot named {name!r} (`taxjson compare "
                           f"--list` names them)")
    shutil.rmtree(d)


def list_snapshots(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every snapshot the project has: name, kind, taken, how many
    report files; `prev` first, `filed` last."""
    out: List[Dict[str, Any]] = []
    d = snapshots_dir(root)
    names: List[str] = []
    if d.is_dir():
        names = sorted(p.name for p in d.iterdir()
                       if p.is_dir() and (p / SNAPSHOT_FILE).is_file())
    names.sort(key=lambda n: (n != PREV, n))
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
    name = check_name(name)
    if name == FILED:
        year = (cfg.get("settings") or {}).get("year")
        lock = root / "filed" / f"{year}.json"
        rep = filed_reports_dir(root, year)
        if not lock.is_file():
            raise CompareError(f"{year} is not closed (no filed/{year}.json)"
                               f" — `taxjson close-year` keeps the reports "
                               f"as filed")
        try:
            snap = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CompareError(f"filed/{year}.json cannot be read: {e}")
        frozen = snap.get("frozen") if isinstance(snap, dict) else None
        if not isinstance(frozen, dict) or not rep.is_dir():
            raise CompareError(f"filed/{year}.json was closed before "
                               f"close-year kept the reports as filed — "
                               f"`taxjson close-year --force` records them")
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
    d = snap_dir(root, name)
    p = d / SNAPSHOT_FILE
    if not p.is_file():
        if name == PREV:
            raise CompareError("no previous run kept yet — the next full "
                               "`taxjson run` keeps the reports it "
                               "replaces (work/compare/prev/)")
        raise CompareError(f"no snapshot named {name!r} (`taxjson compare "
                           f"--list` names them)")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CompareError(f"work/compare/{name}/{SNAPSHOT_FILE} cannot be "
                           f"read: {e}")
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        raise CompareError(f"work/compare/{name}/{SNAPSHOT_FILE} is not a "
                           f"snapshot")
    return doc, d / "reports"


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
    return abs(fa - fb) > (0.005 if kind == "money" else 0.5)


def compare(root: Path, cfg: Dict[str, Any], name: str = PREV, *,
            details: bool = False) -> Dict[str, Any]:
    """The comparison document (CompareError when the snapshot is
    missing): what moved per account, which report files differ, which
    inputs changed, and with `details` a unified diff per report."""
    from taxjson.lib.checklist import (fingerprint_delta, frozen_files,
                                       input_fingerprint)
    root = Path(root)
    doc, rep = load(root, cfg, name)
    before = report_files(rep, f"filed/{doc.get('year')}/reports/"
                          if name == FILED else
                          f"work/{SNAP_ROOT}/{name}/reports/")
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
    out = {"against": name, "kind": doc.get("kind") or "saved",
           "taken": doc.get("taken") or "",
           "different": bool(changed or reports["added"]
                             or reports["removed"] or rows),
           "accounts": rows, "unchanged_accounts": unchanged,
           "reports": reports, "inputs": inputs}
    if details:
        diffs = {}
        for n in changed:
            diffs[n] = "".join(difflib.unified_diff(
                normalized(before[n]).splitlines(keepends=True),
                normalized(after[n]).splitlines(keepends=True),
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
        return ["No snapshot yet: the next full `taxjson run` keeps the "
                "run before as `prev`; `taxjson compare --save NAME` keeps "
                "one by name."]
    body = [[i["name"], i["kind"],
             str(i["taken"]).replace("T", " ")[:16], str(i["reports"])]
            for i in items]
    return _out.fit_table(["NAME", "KIND", "TAKEN", "REPORTS"], body,
                          aligns=["<", "<", "<", ">"], per_record=False)

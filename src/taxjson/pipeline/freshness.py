"""When a work/ file is stale: a content record per output, the package
fingerprint, and the stamps the run keeps.

`needs_rebuild(out, *inputs)`, the stage table's executor's ask
(pipeline/stage_table), decides on content, not mtimes (PLAN step 3a, F2).
A run builds every stage, and a view its full-history gains, every time
(`force`: no derived figure comes from a record's word) and the ask records
what each was built from, for the sweep; only the fetched rates are built
when stale (pipeline/stage_table: a `fetched` row, the one `execute` may
`reuse`): each output has a record beside it (`<folder>/.deps/<name>.json`) holding
what it was built from, the hash of each declared input (or its absence), the
hash of taxjson.toml, the package's content fingerprint and the output's own
hash. The output is stale when any
of those differ from what is on disk now, or when no complete run has
recorded it. A deleted input, a file restored with an old mtime (`cp -p`,
`tar x`, `touch -r`) and a touched-but-unchanged file all read right, which
the mtime rule got wrong three ways (the FUZZ #J deletion blindness, S039-03,
issue #10).

The inputs are read when the stage asks (what it is about to build from),
and the record is written when the output is vouched for: a `tjs run` records
what it built when it completes (`commit_records`: a run that stops half-way
vouches for nothing), and a command that builds a view of its own records it
as soon as it is built (`record_now`). A build decided on another ground (a
rates file whose coverage ended) says so with `mark_built`. An input the stage no longer declares (a map removed,
the missing history withdrawn) or one now absent is a change by itself: no
marker says so (backlog TJ-1807). Input names in a record are relative to
the project, so a moved project keeps its records. The record's format, its
place and its hasher are lib/records' (one for the run and the views:
TJ-1804).
"""
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from taxjson.lib.records import read_record, record_path, sha256_file, write_record
from taxjson.pipeline import run_state

# Files in the package that are not code or data the code reads: byte
# caches, hidden files (.gitignore, .DS_Store, editor swap files),
# documentation and editor / merge leftovers.
_PKG_SKIP_SUFFIXES = (".pyc", ".pyo", ".md", ".rst", "~", ".swp", ".swo",
                      ".orig", ".rej", ".bak")

__all__ = ["record_path", "needs_rebuild", "commit_records", "record_now",
           "mark_built", "unmark", "package_fingerprint", "FolderInputError",
           "timeline_beside"]


def _package_files(pkg_root: Path) -> List[Path]:
    """Every file of the installed taxjson package that decides what a
    run computes: the `.py` sources AND the shipped data they read
    (data/markets.toml — venues, index-option roots, stablecoins — and
    any other toml / json / csv / txt shipped beside the code), sorted.
    A market-data-only update left an incremental run (retired since)
    serving output built from the old data (GitHub issue #10)."""
    out = []
    for p in pkg_root.rglob("*"):
        rel = p.relative_to(pkg_root).parts
        if any(part.startswith(".") or part == "__pycache__"
               for part in rel):
            continue
        if p.name.endswith(_PKG_SKIP_SUFFIXES) or not p.is_file():
            continue
        out.append(p)
    return sorted(out)


def _package_fingerprint() -> str:
    """A CONTENT fingerprint of the installed taxjson package's .py
    sources and shipped data (`_package_files`; relative path + bytes).
    An mtime key misses a code change whose files keep an older mtime
    (cp -p, rsync -a, tar x, touch -r) and a deleted module, so an
    incremental run (retired since) served output from the old code
    (S039-03); every record names it, so a view built by other code is
    rebuilt."""
    import taxjson
    pkg_root = Path(taxjson.__file__).parent
    h = hashlib.sha256()
    for p in _package_files(pkg_root):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(p.relative_to(pkg_root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def package_fingerprint() -> str:
    """`_package_fingerprint`, once per process: the package's files do not
    change under a running command (a test that edits them resets
    `run_state.package_fingerprint`)."""
    if run_state.package_fingerprint is None:
        run_state.package_fingerprint = _package_fingerprint()
    return run_state.package_fingerprint


def _read_work_stamp(path: Path) -> Optional[str]:
    """A work/ bookkeeping file's text (sources list, input fingerprint, a
    stamp), or None when it is missing or cannot be read: an
    unreadable stamp means 'changed' and is rewritten. A non-UTF-8 byte in
    one was a UnicodeDecodeError traceback that even a full `tjs run` — the
    documented recovery — could not get past (re-audit A2-0795, A2-1429)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def timeline_beside(gains_json: Path) -> Path:
    """The engine's cost timeline the run writes with a gains file
    (taxjson-gains --cost-timeline; lib/cost_timeline, backlog TJ-0417),
    read by `tjs list --date`: `.<gains file's stem>.timeline.json`
    beside it — dot-prefixed, so no account glob (`<account>_*.json`) or
    gains glob (`*_gains*.json`) finds it."""
    stem = gains_json.name.lstrip(".")
    if stem.endswith(".json"):
        stem = stem[:-len(".json")]
    return gains_json.with_name(f".{stem}.timeline.json")


# ---- the content records

# One hash per (path, size, mtime_ns, inode) per process (run_state.current.hash_cache):
# a base book is asked about by four stages and recorded once; the stat is
# the cheap check that the bytes are the ones hashed (lib/report_model's
# rule). A file replaced within one run by one of the same size and mtime
# is read once; the next command (another process) reads it again. The
# pipeline's writers replace files atomically (a new inode), which the key
# sees; an in-place rewrite of the same size within one mtime tick would not be.


def _file_hash(path: Path) -> Optional[str]:
    """The sha256 of a file's bytes, or None when it is absent or cannot
    be read (absent and unreadable both read as "not this content")."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, st.st_mtime_ns, st.st_ino)
    hit = run_state.current.hash_cache.get(key)
    if hit is not None:
        return hit
    digest = sha256_file(path)
    if digest is not None:
        run_state.current.hash_cache[key] = digest
    return digest


class FolderInputError(TypeError):
    """A stage declared a folder as an input (`path`). A folder has no
    content hash (it read as absent on every run), so no edit of the files
    in it ever reached a record (TJ-4128: the project folder that
    --incomplete-history names): declare the files the stage reads, or a
    stamp of what it takes from them. A stage leaves out a path a person
    may have made a folder by mistake (it reads a file there, or nothing),
    and `tjs run` refuses one it did not, naming it (TJ-4133)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__(
            f"{path}: a folder declared as an input has no content hash, so "
            f"an edit of the files in it would never rebuild: declare the "
            f"files the stage reads, or a stamp of what it takes from them")


def _input_key(path: Path) -> Optional[str]:
    """What a record holds for an input: its content hash, or None for an
    absent input. A folder is refused (FolderInputError)."""
    if path.is_dir():
        raise FolderInputError(path)
    return _file_hash(path)


def _project_root(out: Path) -> Path:
    """The folder a record's input names are relative to: the project
    (taxjson.toml's folder when the run named it, else the work folder's
    parent), so a moved or restored project keeps its records."""
    cp = run_state.config_path
    return Path(cp).parent.resolve() if cp is not None else out.parent.parent.resolve()


def _input_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())           # outside the project: as is


def _inputs_now(out: Path, inputs: Tuple[Path, ...]) -> Dict[str, Optional[str]]:
    root = _project_root(out)
    return {_input_name(p, root): _input_key(p) for p in inputs}


def _config_key() -> Optional[str]:
    cp = run_state.config_path
    return None if cp is None else _file_hash(Path(cp))


# What a record holds that freshness writes; the rest is another module's
# (a wash pass's `wash`: lib/report_model), kept.
_OWN = frozenset({"format", "out", "inputs", "config", "code"})


def _write_record(out: Path, pending: Dict[str, object]) -> None:
    """The record of `out`: what the stage asked with (`_pending`: the
    inputs, taxjson.toml and the code as they were then) and the output's
    hash, taken now (lib/records: written when it changed)."""
    out = Path(out)
    kept = {k: v for k, v in (read_record(out) or {}).items() if k not in _OWN}
    write_record(out, {**kept, "out": _file_hash(out), **pending})


def needs_rebuild(out: Path, *inputs: Path, force: bool = False) -> bool:
    """Whether `out` must be built (again) from `inputs`: `force` (every
    derived row, a run's stages and the views' full-history gains: always),
    no output, no record from a complete run, an input whose content (or
    absence) changed since, a changed taxjson.toml, changed code, or an
    output that is not the one recorded. The stage table's executor is its
    one caller, over the inputs a row declares (pipeline/stage_table
    `execute`), and asks without `force` only for a row of fetched data (the
    rates: `reuse`); a stage's own ground is not asked here: the executor
    asks it when this says fresh and `mark_built`s what it builds on it.
    Every True remembers what `out` is built from, read now, for
    `commit_records` / `record_now`."""
    out = Path(out)
    inputs_now = _inputs_now(out, tuple(Path(p) for p in inputs))
    stale = force or _is_stale(out, inputs_now)
    if stale:
        run_state.current.pending_records[out] = _pending(inputs_now)
    return stale


def _pending(inputs_now: Dict[str, Optional[str]]) -> Dict[str, object]:
    """What a record holds as of the ask: the inputs, taxjson.toml and the
    code as they are now (a taxjson.toml saved during a later stage is a
    change the next view must see)."""
    return {"inputs": inputs_now, "config": _config_key(),
            "code": package_fingerprint()}


def _is_stale(out: Path, inputs_now: Dict[str, Optional[str]]) -> bool:
    if not out.exists():
        return True
    rec = read_record(out)
    if rec is None:
        return True
    if rec.get("code") != package_fingerprint():        # the cheap tests first
        return True
    if rec.get("config") != _config_key():
        return True
    if rec.get("inputs") != inputs_now:
        return True
    return rec.get("out") != _file_hash(out)


def mark_built(out: Path, *inputs: Path) -> None:
    """A stage built `out` from `inputs` on a ground of its own (a rates
    file whose coverage ended): remember it as `needs_rebuild` would
    have."""
    out = Path(out)
    run_state.current.pending_records[out] = _pending(
        _inputs_now(out, tuple(Path(p) for p in inputs)))


def unmark(out: Path) -> None:
    """The stage asked and then kept the old `out` after all (a refresh that
    failed): nothing to vouch for."""
    run_state.current.pending_records.pop(Path(out), None)


def record_now(out: Path) -> List[Path]:
    """Vouch for `out` now: the record of what `needs_rebuild` saw it was to
    be built from (a command that builds a view of its own, outside a run).
    Nothing when `out` is missing or was never asked about. Returns the
    outputs whose record could not be written (a full disk: safe, the next
    view rebuilds them; the caller says so)."""
    out = Path(out)
    inputs = run_state.current.pending_records.pop(out, None)
    if inputs is None or not out.exists():
        return []
    try:
        _write_record(out, inputs)
    except OSError:
        return [out]
    return []


def commit_records() -> List[Path]:
    """A run completed: vouch for every output it was asked about and that
    exists now (one that it did not write, or that a stage removed, stays
    unvouched: the sweep leaves it). Returns the outputs whose record could
    not be written."""
    pending = run_state.current.pending_records
    unwritten: List[Path] = []
    for out, inputs in list(pending.items()):
        if out.exists():
            try:
                _write_record(out, inputs)
            except OSError:
                unwritten.append(out)
    pending.clear()
    return unwritten

"""The records of what each work/ file was built from: one format, one hasher.

`tjs run` keeps a record per output (`<folder>/.deps/<name>.json`; a
report's in work/.deps/reports/): the hash of each declared input (or its
absence), of taxjson.toml, of the package and of the output itself
(pipeline/freshness decides on them). A cross-account wash pass's record
also holds what that pass read (`wash`: lib/report_model), so the views can
say a wash-adjusted file is stale after a `run --account`. The last run's
outputs are listed in work/.deps/run.json (pipeline/stage_table
`commit_run`), with what checked each account's gains for the loss rule
(lib/report_model `record_loss_checks`). A record of another format (an
older taxjson's) reads as no record: its output is built again (backlog
TJ-1804, TJ-4266).
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from taxjson.lib.safe_write import write_changed

DEPS_DIR = ".deps"
REPORTS_DIR = "reports"         # the project's reports/ (pipeline/run reports_dir)
RECORD_FORMAT = 2
RUN_FILE = "run.json"           # work/.deps/run.json: the last run's outputs


def sha256_file(path: Path) -> Optional[str]:
    """The sha256 of a file's bytes, or None when it is absent or cannot be
    read (absent and unreadable both read as "not this content")."""
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256()
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def record_path(out: Path) -> Path:
    """Where `out`'s record lives: `.deps/<name>.json` beside it; a
    report's in work/.deps/reports/ (reports/ holds only what a person
    reads: the comparison copies it, a golden book compares all of it)."""
    out = Path(out)
    if out.parent.name == REPORTS_DIR:
        return out.parent.parent / "work" / DEPS_DIR / REPORTS_DIR / (out.name + ".json")
    return out.parent / DEPS_DIR / (out.name + ".json")


def read_record(out: Path) -> Optional[Dict[str, Any]]:
    """`out`'s record, or None: none there, one that cannot be read, or one
    of another format."""
    try:
        rec = json.loads(record_path(out).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("format") == RECORD_FORMAT else None


def write_record(out: Path, rec: Dict[str, Any]) -> None:
    """`out`'s record, when it changed (owner-only, never through a link)."""
    rp = record_path(out)
    rp.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_changed(rp, json.dumps({**rec, "format": RECORD_FORMAT}, sort_keys=True) + "\n")


def set_record_field(out: Path, key: str, value: Any) -> None:
    """One field of `out`'s record set, the rest kept (a record that is not
    there is begun with it alone: no `code`, so it vouches for nothing)."""
    rec = read_record(out) or {}
    if rec.get(key) == value and rec:
        return
    rec[key] = value
    write_record(out, rec)

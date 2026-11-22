"""What the last full run recorded the books were built from
(work/.inputs_fingerprint.json): the reader of that record, for the
checklist, the first-run walk, the run's closing summary, `tjs years`,
the views' banner (the books' country), find-missing-history's refusal
of stale books and lib/compare. lib/checklist writes it
(record_input_fingerprint) and compares it with the inputs; this module
reads it alone, so the modules under the checklist read it without
importing the checklist (step 8 of the architecture plan: lib's import
cycle)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


FINGERPRINT_FILE = ".inputs_fingerprint.json"     # in work/
# The last full run's record, set aside by every `tjs run` before it writes
# into work/ (pipeline/run `_drop_run_records`), and removed when a full run
# that finishes records anew (checklist `record_input_fingerprint`). Nothing
# reads it as a record: the books a run rewrote are refused until a full run
# finishes; run-clean names what changed since that run (backlog TJ-4356).
SET_ASIDE_FILE = ".inputs_fingerprint.set_aside.json"     # in work/


def recorded_booked_twice(root: Path) -> List[str]:
    """What the last full run found booked in two accounts
    (record_input_fingerprint's `booked_twice`): [] when nothing, or
    when there is no readable record."""
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable:
        return []
    got = (doc or {}).get("booked_twice")
    return [str(x) for x in got if str(x).strip()] \
        if isinstance(got, list) else []


def books_country(root: Path) -> Optional[str]:
    """The country the last full run built work/ under (its fingerprint
    record), or None when the record does not say."""
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable:
        return None
    c = (doc or {}).get("country")
    return c if isinstance(c, str) and c else None


def books_code(root: Path) -> Optional[str]:
    """The package fingerprint of the taxjson whose last full run built
    work/ (its record's `code`), or None when the record does not say (an
    older taxjson wrote it) or cannot be read."""
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable:
        return None
    c = (doc or {}).get("code")
    return c if isinstance(c, str) and c else None


class FingerprintUnreadable(ValueError):
    """work/.inputs_fingerprint.json exists but is not a fingerprint."""


def _load_fingerprint_doc(root: Path, name: str = FINGERPRINT_FILE
                          ) -> Optional[Dict[str, Any]]:
    """The recorded fingerprint document, None when there is none.
    Raises FingerprintUnreadable for a file that exists but cannot be
    read (truncated, files not a table): never "no record", which fell
    back to mtimes and called stale books clean (A2-1155). `name`: the
    record set aside (SET_ASIDE_FILE) instead."""
    p = root / "work" / name
    if not p.exists() and not p.is_symlink():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FingerprintUnreadable(str(e)) from e
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in files.items()):
        raise FingerprintUnreadable("no files table")
    return doc


def recorded_fingerprint(root: Path
                         ) -> Optional[Tuple[Dict[str, str], "datetime"]]:
    """What the last full run recorded the books were built from
    (`work/.inputs_fingerprint.json`: {key: sha256}) and when it was
    written, or None when there is no readable record."""
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable:
        return None
    if not doc:
        return None
    try:
        when = datetime.fromtimestamp(
            (root / "work" / FINGERPRINT_FILE).stat().st_mtime)
    except OSError:
        when = datetime.now()
    return dict(doc["files"]), when

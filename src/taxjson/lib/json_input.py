"""One input contract for the stand-alone bin tools that read JSON.

Every tool that is handed a JSON file on its command line reads it
through :func:`read_json_doc` (or :func:`load_json_doc_or_exit`), so an
input that was named but cannot be used gives the same answer
everywhere: a one-line ``<prog>: error: <file>: ...`` on stderr and a
non-zero exit — never a traceback, and never a warning followed by a
report built from the files that happened to load (audit S028-09,
S049-22, S051-11, S079-11, S035-13).

The document shape follows ``core.load_transactions``: a JSON object,
or — for a transaction book — a bare JSON array of rows, which is
wrapped as ``{list_key: [...]}``. ``#`` comments are accepted, as the
canonical loader accepts them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from taxjson.lib import cli_diag


class InputFileError(ValueError):
    """A named input file cannot be read or does not have the expected
    shape. ``str(err)`` is a one-line message that names the file."""


def read_json_doc(path, *, list_key: Optional[str] = "transactions",
                  require_key: Optional[str] = None) -> Dict[str, Any]:
    """Return the JSON object in ``path``.

    * unreadable / non-UTF-8 / invalid JSON -> InputFileError
    * a bare array -> ``{list_key: array}`` when ``list_key`` is set
      (the transaction-book form ``core.load_transactions`` accepts),
      otherwise InputFileError
    * any other non-object -> InputFileError
    * ``require_key`` set and missing -> InputFileError
    """
    from taxjson.lib.core import strip_json_comments
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise InputFileError(f"{p}: cannot read ({e.strerror or e})") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise InputFileError(f"{p}: not UTF-8 text ({e.reason} at byte "
                             f"{e.start})") from None
    try:
        doc = json.loads(strip_json_comments(text))
    except ValueError as e:
        raise InputFileError(f"{p}: not valid JSON ({e})") from None
    if isinstance(doc, list) and list_key:
        doc = {list_key: doc}
    if not isinstance(doc, dict):
        want = (f'a JSON object (or an array of {list_key} rows)'
                if list_key else "a JSON object")
        raise InputFileError(f"{p}: expected {want}, got "
                             f"{type(doc).__name__}")
    if list_key and list_key in doc and not isinstance(doc[list_key], list):
        raise InputFileError(f'{p}: "{list_key}" must be a list, got '
                             f"{type(doc[list_key]).__name__}")
    if require_key and require_key not in doc:
        raise InputFileError(f'{p}: no "{require_key}" key (keys: '
                             f"{', '.join(sorted(map(str, doc))) or 'none'})")
    return doc


def require_gains_doc(doc: Dict[str, Any], path) -> Dict[str, Any]:
    """``doc`` when it is a gains-stage document: a ``transactions``
    list whose rows carry ``gain`` (any income-only or empty list is
    fine). A document with no ``transactions`` key ('Transactions', a
    report JSON) or a pre-gains stage file (rows with ``quantity`` and
    no ``gain`` — work/<acct>_base.json) is refused: the report tools
    read either as zero dispositions and printed a $0 Schedule 3 /
    GRAND TOTAL 0.00 at exit 0 (audit S033-01)."""
    if not isinstance(doc, dict) or "transactions" not in doc:
        keys = ", ".join(sorted(map(str, doc))) if isinstance(doc, dict) \
            else type(doc).__name__
        raise InputFileError(f'{path}: no "transactions" list (keys: '
                             f"{keys or 'none'}) — not a taxjson gains "
                             f"file")
    rows = doc.get("transactions") or []
    if not isinstance(rows, list):
        raise InputFileError(f'{path}: "transactions" must be a list')
    dict_rows = [r for r in rows if isinstance(r, dict)]
    if dict_rows and not any("gain" in r for r in dict_rows) \
            and any("quantity" in r for r in dict_rows):
        raise InputFileError(
            f"{path}: a pipeline stage file (its rows have no 'gain') — "
            f"pass the <account>_gains.json (or _gains_wash.json) the "
            f"gains stage writes")
    return doc


def load_json_doc_or_exit(prog: str, path, *, exit_code: int = 2,
                          **kw) -> Dict[str, Any]:
    """:func:`read_json_doc`, or print ``<prog>: error: ...`` and exit."""
    try:
        return read_json_doc(path, **kw)
    except InputFileError as e:
        cli_diag.error(prog, str(e))
        sys.exit(exit_code)


def rows_or_exit(prog: str, doc: Dict[str, Any], path, key: str,
                 *, exit_code: int = 2) -> list:
    """``doc[key]`` as a list of objects, or a one-line refusal."""
    rows = doc.get(key, []) or []
    if not isinstance(rows, list) or any(not isinstance(r, dict)
                                         for r in rows):
        cli_diag.error(prog, f'{path}: "{key}" must be a list of JSON '
                             "objects")
        sys.exit(exit_code)
    return rows


def load_transactions_or_exit(prog: str, path, *, exit_code: int = 2):
    """``core.load_transactions(path)``, or a one-line
    ``<prog>: error: <file>: ...`` and exit — its refusals (a missing
    file, bad JSON, a row the coercion funnel rejects) were tracebacks
    in the tools that called it bare (audit S051-11, S049-22)."""
    from taxjson.lib.core import load_transactions
    p = Path(path)
    try:
        return load_transactions(p)
    except OSError as e:
        msg = f"{p}: cannot read ({e.strerror or e})"
    except UnicodeDecodeError as e:
        msg = f"{p}: not UTF-8 text ({e.reason} at byte {e.start})"
    except (ValueError, TypeError, AttributeError) as e:
        text = str(e)
        msg = text if str(p) in text else f"{p}: {text}"
    cli_diag.error(prog, msg)
    sys.exit(exit_code)

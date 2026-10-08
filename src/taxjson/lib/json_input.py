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
import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from taxjson.lib import cli_diag


class InputFileError(cli_diag.InputContentError):
    """A named input file cannot be read or does not have the expected
    shape. ``str(err)`` is a one-line message that names the file. A
    ValueError (via InputContentError), so every guard_main-wrapped tool
    reports it in one line with exit 2."""


class _NonFiniteToken(ValueError):
    pass


def _refuse_constant(token: str):
    # json.loads reads the non-standard NaN / Infinity / -Infinity
    # tokens as floats; no taxjson file holds one legitimately.
    raise _NonFiniteToken(token)


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
        # A BOM is dropped: a hand-edited JSON file saved by Notepad is
        # read like the TOML and text inputs (re-audit A2-0776).
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise InputFileError(f"{p}: not UTF-8 text ({e.reason} at byte "
                             f"{e.start})") from None
    try:
        doc = json.loads(strip_json_comments(text),
                         parse_constant=_refuse_constant)
    except _NonFiniteToken as e:
        # A NaN gain went through form-export into a filing JSON at exit
        # 0 (issue #8): a non-finite number is refused wherever it is.
        raise InputFileError(
            f"{p}: holds a non-finite number ({e}) — the file is damaged "
            f"or hand-edited: fix it or re-run `taxjson run`") from None
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
    if list_key and any(not isinstance(r, dict)
                        for r in doc.get(list_key) or []):
        # [1, 2, 3] passed as a transaction list and died later on
        # row.get (audit S042-18).
        raise InputFileError(f'{p}: "{list_key}" must be a list of JSON '
                             f"objects")
    if require_key and require_key not in doc:
        raise InputFileError(f'{p}: no "{require_key}" key (keys: '
                             f"{', '.join(sorted(map(str, doc))) or 'none'})")
    return doc


# The row funnel for report readers (A2-0330): a work/ row whose date is
# not a string, or whose money / quantity field is not a number, died
# later as a TypeError/ValueError traceback in whichever view read it
# first (events, trades-sum, form-export's _acquired_date, ...). Checked
# once, here, where every reader loads the file. None is allowed (an
# absent value); bool is not a number.
_STR_FIELDS = ("date", "date_settle")
_NUM_FIELDS = ("qty", "quantity", "proceeds", "cost", "gain", "net_amount",
               "price", "commission", "fee", "total_cost",
               "disallowed_amount")


def check_row_types(rows, path, key: str = "transactions") -> None:
    """InputFileError naming the file, list, row and field when a row's
    date is not a string or a numeric field is not a finite number."""
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        for f in _STR_FIELDS:
            v = r.get(f)
            if v is not None and not isinstance(v, str):
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is {v!r}, not a YYYY-MM-DD string — the file "
                    f"is damaged or hand-edited: fix it or re-run "
                    f"`taxjson run`")
        for f in _NUM_FIELDS:
            v = r.get(f)
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float))):
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is {v!r}, not a number — the file is damaged "
                    f"or hand-edited: fix it or re-run `taxjson run`")
            if v is not None and not math.isfinite(v):
                # issue #8: a NaN gain reached a filing export.
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is {v!r}, a non-finite number — the file is "
                    f"damaged or hand-edited: fix it or re-run "
                    f"`taxjson run`")


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
    for key in _WORK_ROW_LISTS:
        check_row_types(doc.get(key) if isinstance(doc.get(key), list)
                        else [], path, key)
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
    """``doc[key]`` as a list of objects, or a one-line refusal. A
    document without ``key`` is refused too: read as an empty list, a
    {"Transactions": [...]} book gave an empty report at exit 0 (issue
    #6). An explicit empty list is fine."""
    if key not in doc:
        cli_diag.error(prog, f'{path}: no "{key}" list (keys: '
                             f"{', '.join(sorted(map(str, doc))) or 'none'})")
        sys.exit(exit_code)
    rows = doc.get(key) or []
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


# The row lists and the summary a pipeline artifact in work/ may carry.
_WORK_ROW_LISTS = ("transactions", "inventory", "manual_reporting_required")


def read_work_doc(path) -> Dict[str, Any]:
    """A pipeline artifact from work/ (<acct>_base/_raw/_gains*.json,
    _report.json): :func:`read_json_doc`, plus its row lists must be
    lists of objects and `summary` an object. A truncated, non-UTF-8 or
    wrong-shape file is an InputFileError naming the file — the read
    views printed a traceback, or an AttributeError on `.get`, for
    each of those (audit S042-18)."""
    doc = read_json_doc(path, list_key="transactions")
    p = Path(path)
    for key in _WORK_ROW_LISTS:
        rows = doc.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list) or any(not isinstance(r, dict)
                                             for r in rows):
            raise InputFileError(f'{p}: "{key}" must be a list of JSON '
                                 f"objects")
    if doc.get("summary") is not None and not isinstance(doc["summary"],
                                                         dict):
        raise InputFileError(f'{p}: "summary" must be a JSON object')
    for key in _WORK_ROW_LISTS:
        check_row_types(doc.get(key), p, key)
    return doc


class NonFiniteOutputError(cli_diag.InputContentError):
    """A filing result holds NaN or an infinity. ``str(err)`` names where.
    A ValueError (via InputContentError), so a guard_main-wrapped tool
    reports it in one line with exit 2."""


def _non_finite_at(obj: Any, where: str = "") -> Optional[str]:
    """The path (``lines[0].gain``) of the first non-finite float in
    ``obj``, or None."""
    if isinstance(obj, float):
        return None if math.isfinite(obj) else (where or "(the value)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            hit = _non_finite_at(v, f"{where}.{k}" if where else str(k))
            if hit:
                return hit
    elif isinstance(obj, (list, tuple)):
        for n, v in enumerate(obj):
            hit = _non_finite_at(v, f"{where}[{n}]")
            if hit:
                return hit
    return None


def filing_json_text(obj: Any, **kw: Any) -> str:
    """``json.dumps(obj, allow_nan=False, **kw)``, refusing a NaN or an
    infinity with a NonFiniteOutputError naming its path: json.dumps'
    default wrote a bare NaN token into a filing export at exit 0
    (issue #8), which is not JSON and not a figure anyone can file."""
    hit = _non_finite_at(obj)
    if hit:
        raise NonFiniteOutputError(
            f"refusing to write a non-finite number (NaN or infinity) at "
            f"{hit}: a filing figure must be a real number — the input it "
            f"was computed from is damaged")
    return json.dumps(obj, allow_nan=False, **kw)


def dump_filing_json(obj: Any, fp, **kw: Any) -> None:
    """:func:`filing_json_text` written to ``fp`` (nothing is written
    when it refuses). Defaults: indent=2, sort_keys=True."""
    kw.setdefault("indent", 2)
    kw.setdefault("sort_keys", True)
    fp.write(filing_json_text(obj, **kw))

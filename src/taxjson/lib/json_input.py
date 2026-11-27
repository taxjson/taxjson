"""One input contract for the stand-alone bin tools that read JSON.

Every tool that is handed a JSON file on its command line reads it
through :func:`read_json_doc` (or :func:`load_json_doc_or_exit`), so an
input that was named but cannot be used gives the same answer
everywhere: a one-line ``<prog>: error: <file>: ...`` on stderr and a
non-zero exit — never a traceback, and never a warning followed by a
report built from the files that happened to load (audit S028-09,
S049-22, S051-11, S079-11, S035-13).

The document shape follows ``tx_load.load_transactions``: a JSON object,
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
      (the transaction-book form ``tx_load.load_transactions`` accepts),
      otherwise InputFileError
    * any other non-object -> InputFileError
    * ``require_key`` set and missing -> InputFileError
    """
    from taxjson.lib.tx_load import strip_json_comments
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
            f"or hand-edited: fix it or re-run `tjs run`") from None
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
                             f"objects{_bad_row(doc.get(list_key))}")
    if require_key and require_key not in doc:
        raise InputFileError(f'{p}: no "{require_key}" key (keys: '
                             f"{', '.join(sorted(map(str, doc))) or 'none'})")
    return doc


_JSON_TYPE = {type(None): "null", bool: "true/false", int: "a number",
              float: "a number", str: "a string", list: "a list"}


def _bad_row(rows: Any) -> str:
    """` (row 3 is null)`: the first row of a list that is not a JSON
    object, named (backlog TJ-0320); '' when every row is one or `rows`
    is not a list."""
    if not isinstance(rows, list):
        return ""
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            return f" (row {i} is {_JSON_TYPE.get(type(r), 'not an object')})"
    return ""


# The row funnel for report readers (A2-0330): a work/ row whose date is
# not a string, or whose money / quantity field is not a number, died
# later as a TypeError/ValueError traceback in whichever view read it
# first (events, trades-sum, form-export's _acquired_date, ...). Checked
# once, here, where every reader loads the file. None is allowed (an
# absent value); bool is not a number.
_STR_FIELDS = ("date", "date_settle")
# `dividend` and `pil`: the income rows a gains file carries beside its
# dispositions — a dividend of 1e400 (valid JSON, inf as a float) summed
# to "TOTAL DIVIDENDS / STAKING: inf" at exit 0 (audit 2026-10-24 D03).
# `raw_gain`, `permanently_disallowed`, `deferred_loss_recovered`: the
# rest of the money a gains row carries (model/result `GainRow`); a text
# one stopped the report that took float() of it (TJ-2879).
_NUM_FIELDS = ("qty", "quantity", "proceeds", "cost", "gain", "net_amount",
               "price", "commission", "fee", "total_cost",
               "disallowed_amount", "dividend", "pil", "raw_gain",
               "permanently_disallowed", "deferred_loss_recovered",
               "deferred_wash", "capitalized_263h", "recognised_premium",
               "denied_contribution", "write_outlay")


def check_row_types(rows, path, key: str = "transactions", only=None,
                    fields=None) -> None:
    """InputFileError naming the file, list, row and field when a row's
    date is not a string or a numeric field is not a finite number;
    with `only`, the rows it is true of (a reader's own, at their index
    in the list: backlog TJ-2992), and with `fields` those fields alone
    (the ones it reads: TJ-2998)."""
    str_fields = _STR_FIELDS if fields is None else [f for f in _STR_FIELDS if f in fields]
    num_fields = _NUM_FIELDS if fields is None else [f for f in _NUM_FIELDS if f in fields]
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict) or (only is not None and not only(r)):
            continue
        for f in str_fields:
            v = r.get(f)
            if v is not None and not isinstance(v, str):
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is {v!r}, not a YYYY-MM-DD string — the file "
                    f"is damaged or hand-edited: fix it or re-run "
                    f"`tjs run`")
        for f in num_fields:
            v = r.get(f)
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float))):
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is {v!r}, not a number — the file is damaged "
                    f"or hand-edited: fix it or re-run `tjs run`")
            try:
                finite = v is None or math.isfinite(v)
            except OverflowError:
                # Valid JSON, read as an int a float cannot hold
                # (TJ-2886: an OverflowError traceback).
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is an integer of {len(str(abs(v)))} digits, too "
                    f"large to read — the file is damaged or hand-edited: "
                    f"fix it or re-run `tjs run`") from None
            if not finite:
                # issue #8: a NaN gain reached a filing export.
                raise InputFileError(
                    f'{path}: "{key}" row {i} ({r.get("symbol") or "?"}): '
                    f"{f} is {v!r}, a non-finite number — the file is "
                    f"damaged or hand-edited: fix it or re-run "
                    f"`tjs run`")


# The income rows a gains document carries beside its dispositions: the
# engines book them by their own `dividend` / `pil` amounts.
INCOME_ROW_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU")
# What every computed disposition carries and every reader keys on: its
# units and gain (a row without them was skipped) and a date (a row
# without one fell out of every year filter). An unknown-cost row still
# flagged `tainted` (a hand-run file — the pipeline moves them to
# manual_reporting_required) is reported by hand from its date and units.
DISPOSITION_FIELDS = ("qty", "gain")
MANUAL_ROW_FIELDS = ("qty",)
# The amount an income row is booked by (any of them; an explicit 0
# counts): a DIVIDEND's `dividend`, a payment in lieu's `pil` (or its
# `dividend`, a Canadian dealer's s.260 deemed dividend). A row without
# one was read as income 0 (backlog TJ-0495).
INCOME_AMOUNT_FIELDS = {"DIVIDEND": ("dividend",),
                        "DIVIDEND_IN_LIEU": ("pil", "dividend")}


def is_disposition(row: Dict[str, Any]) -> bool:
    """Whether a row of a gains document's `transactions` is a disposition
    (a manual, unknown-cost one included), not an income row: what the
    total, a trace file's counts and taxjson-explain take (audit R1-313;
    TJ-2784: a payment in lieu counted as a sale)."""
    return row.get("action") not in INCOME_ROW_ACTIONS


def gains_row_kind(row: Dict[str, Any]) -> str:
    """'income', 'manual' (an unknown-cost row flagged `tainted`) or
    'disposition' — every other row of a gains document's
    `transactions` is a computed disposition."""
    if row.get("action") in INCOME_ROW_ACTIONS:
        return "income"
    if row.get("tainted"):
        return "manual"
    return "disposition"


def read_rows_doc(path, key: str = "transactions", fields=(), only=None,
                  shown=None) -> Optional[Dict[str, Any]]:
    """A work/ JSON document a reader takes beside its own (another
    account's, an earlier year's, a pass's), or None when it cannot be
    read or is not an object (the reader leaves it out, as it always
    did). Its `key` rows (those `only` is true of) are held to the types
    of `fields` alone, the ones the reader's views read: a damaged field
    it never reads leaves the document in (backlog TJ-3043, TJ-3044,
    TJ-3057). A refused one is an InputFileError naming the file
    (`shown`, else `path`): the caller says so, or stops."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    rows = doc.get(key)
    check_row_types(rows if isinstance(rows, list) else None,
                    path if shown is None else shown, key,
                    only=only, fields=tuple(fields))
    return doc


def check_gains_rows(rows, path, key: str = "transactions", only=None) -> None:
    """InputFileError naming the file and row when a computed
    disposition lacks its units, its gain or a date (DISPOSITION_FIELDS
    and date / date_settle: absent, null or empty), a manual row its
    units or date (issue #17), or an income row its amount
    (INCOME_AMOUNT_FIELDS; an explicit 0 is an amount) or date (backlog
    TJ-0495: T1135 read a DIVIDEND without its amount as income 0 and
    dropped an undated one).
    The readers skipped such a row in silence — a valid neighbour made
    the document look like a gains file — and the export or total left
    the disposition out at exit 0. One contract for every filing and
    summary reader: form-export, reconcile-slips, sum-gains, the option
    reports, t1135, close-year and check-filed (taxjson_filed) and
    carryover. With `only`, the rows it is true of (as check_row_types:
    one account's, backlog TJ-3032)."""
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict) or (only is not None and not only(r)):
            continue
        kind = gains_row_kind(r)
        if kind == "income":
            amt = INCOME_AMOUNT_FIELDS[r.get("action")]
            missing = ([" or ".join(amt)]
                       if all(r.get(f) is None for f in amt) else [])
        else:
            need = DISPOSITION_FIELDS if kind == "disposition" \
                else MANUAL_ROW_FIELDS
            missing = [f for f in need
                       if r.get(f) is None
                       or (isinstance(r.get(f), str) and not r[f].strip())]
        if not any(isinstance(r.get(f), str) and r[f].strip()
                   for f in ("date", "date_settle")):
            missing.insert(0, "date")
        if missing:
            what = ("a disposition" if kind == "disposition"
                    else f"an income row ({r.get('action')})"
                    if kind == "income"
                    else "an unknown-cost (tainted) row")
            raise InputFileError(
                f'{path}: "{key}" row {i} ({r.get("symbol") or "?"} '
                f'{r.get("date") or r.get("date_settle") or "?"}): {what} without '
                f"{', '.join(missing)} — it would be left out of the "
                f"totals and forms. The file is damaged or hand-edited: "
                f"fix it or re-run `tjs run`")


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
    # Its other row lists are lists of objects too (TJ-2939: form-export
    # and reconcile-slips stopped with an AttributeError or a TypeError
    # on one that was not); an empty one of any type reads as none, as
    # its transactions and model/result's parts do (TJ-2859).
    require_object_lists(doc, path, [k for k in _WORK_ROW_LISTS
                                     if k != "transactions"], empty_ok=True)
    for key in _WORK_ROW_LISTS:
        check_row_types(doc.get(key) if isinstance(doc.get(key), list)
                        else [], path, key)
    # Every row on its own (issue #17): the stage-file test above is
    # about the document; a valid neighbour must not carry a broken row.
    check_gains_rows(rows, path)
    return doc


def require_row_lists(doc: Dict[str, Any], path) -> Dict[str, Any]:
    """``doc`` when its ``inventory`` and ``manual_reporting_required``,
    if present and not empty, are lists of objects: for a tool that
    iterates them (a wrong-shape file was a traceback, backlog TJ-1196)."""
    for key in ("inventory", "manual_reporting_required"):
        rows = doc.get(key)
        if rows and (not isinstance(rows, list)
                     or any(not isinstance(r, dict) for r in rows)):
            raise InputFileError(f'{path}: "{key}" must be a list of JSON '
                                 f'objects{_bad_row(rows)}')
    return doc


def require_object_lists(doc: Dict[str, Any], path, keys=None, *,
                         empty_ok: bool = False) -> None:
    """InputFileError naming the file and the first row that is not an
    object, when one of a work/ document's row lists (`keys`, default
    _WORK_ROW_LISTS) is present and not null but not a list of JSON
    objects; with `empty_ok`, an empty value of any type ({}, "", 0)
    reads as none too."""
    for key in _WORK_ROW_LISTS if keys is None else keys:
        rows = doc.get(key)
        if rows is None or (empty_ok and not rows):
            continue
        if not isinstance(rows, list) or any(not isinstance(r, dict)
                                             for r in rows):
            raise InputFileError(f'{path}: "{key}" must be a list of JSON '
                                 f"objects{_bad_row(rows)}")


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
                             f"objects{_bad_row(rows)}")
        sys.exit(exit_code)
    return rows


def load_transactions_or_exit(prog: str, path, *, exit_code: int = 2):
    """``tx_load.load_transactions(path)``, or a one-line
    ``<prog>: error: <file>: ...`` and exit — its refusals (a missing
    file, bad JSON, a row the coercion funnel rejects) were tracebacks
    in the tools that called it bare (audit S051-11, S049-22)."""
    from taxjson.lib.tx_load import load_transactions
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
# A gains file's covers of short sales realized next year, its pools or
# lots at the project year's end and its grant-timed closes of no value
# are rows too (TJ-2909, TJ-2919, TJ-2938): an older file has no year end
# at all (None), never one of another type.
_WORK_ROW_LISTS = ("transactions", "inventory", "manual_reporting_required",
                   "covers_realized_next_year", "inventory_year_end",
                   "grant_nil_closes")
# What every gains file the engine writes carries (each may be empty).
_GAINS_PARTS = ("transactions", "inventory", "summary")


def is_gains_name(name: str) -> bool:
    """A gains artifact's file name: `<acct>_gains.json`,
    `<acct>_gains_wash.json` (and the raw books' `_raw_gains.json`,
    `_raw_base_gains.json`)."""
    return name.endswith(("_gains.json", "_gains_wash.json"))


def check_gains_parts(doc: Dict[str, Any], path) -> None:
    """InputFileError when `doc`, read from a gains file (is_gains_name),
    has none of its transactions, inventory and summary. The engine
    writes all three (each may be empty): a gains file without any is
    damaged, never an empty book — shares, list, sanity and wash-sales
    read `{}` as no positions at exit 0 (backlog TJ-0412)."""
    p = Path(path)
    if is_gains_name(p.name) and not any(k in doc for k in _GAINS_PARTS):
        raise InputFileError(
            f'{p}: a gains file with none of "transactions", '
            f'"inventory", "summary" (keys: '
            f"{', '.join(sorted(map(str, doc))) or 'none'}): damaged")


def advises_rerun(msg: str) -> bool:
    """Whether `msg` already says to re-run `tjs run`, as the row checks'
    messages do ("fix it or re-run `tjs run`"); a message naming the
    command for another reason does not (backlog TJ-2970, TJ-2980)."""
    return "re-run `tjs run`" in msg.lower()


def rerun_advice(msg: str, tail: str = " — re-run `tjs run`") -> str:
    """`msg` with the advice to re-run (`tail`) unless it already gives
    it: a reader wrapping a row check's refusal says it once (backlog
    TJ-2912, TJ-2968, TJ-2976 to TJ-2978)."""
    return msg if advises_rerun(msg) else msg + tail


def read_work_doc(path) -> Dict[str, Any]:
    """A pipeline artifact from work/ (<acct>_base/_raw/_gains*.json,
    _report.json): :func:`read_json_doc`, plus its row lists must be
    lists of objects and `summary` an object. A truncated, non-UTF-8 or
    wrong-shape file is an InputFileError naming the file — the read
    views printed a traceback, or an AttributeError on `.get`, for
    each of those (audit S042-18)."""
    doc = read_json_doc(path, list_key="transactions")
    p = Path(path)
    check_gains_parts(doc, p)
    require_object_lists(doc, p)
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

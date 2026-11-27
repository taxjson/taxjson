"""A book read into TaxTransaction rows: the JSON loader (its comments
stripped), each row's coercion and checks, the date fields, and the
small number checks the readers share. Below engine/, which computes on
the rows (step 8 of the architecture plan: lib's import cycle)."""

import functools
import json
import math
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, List

from taxjson.lib.futures_prefixes import canonical_futures_symbol
from taxjson.model import money as M
from taxjson.model.transaction import TaxTransaction


def finite_num(v: Any) -> bool:
    """A figure read from JSON: a number (not a bool) that is finite.
    json.loads reads `NaN` and `Infinity` into floats, and every comparison
    with a NaN is False, so a locked NaN total passed the shape check and
    matched any recomputed figure in the drift check (2026-10 audit B38). An
    integer too large for a float raised OverflowError (backlog TJ-1458)."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def validate_staking_reward(value, context='staking_reward'):
    """Validate optional versioned reward evidence without inventing facts.

    Null monetary/quantity values mean unknown, not zero. Evidence does not
    participate in tax arithmetic; malformed types must not make a reporting
    command silently display a known commission or a made-up valuation.
    """
    import math
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{context}: staking_reward must be an object or null")
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError(f"{context}: staking_reward schema_version must be 1")
    if type(value.get('commission_known')) is not bool:
        raise ValueError(f"{context}: staking_reward commission_known must be boolean")
    if value.get('kind') not in ('staking', 'other_reward'):
        raise ValueError(f"{context}: staking_reward kind must be staking or other_reward")
    if value.get('commission_method') not in (
            'withheld_from_reward', 'paid_in_other_asset', 'unknown'):
        raise ValueError(f"{context}: staking_reward has an unknown commission_method")
    for field in ('broker', 'source_file', 'source_type', 'source_timestamp',
                  'source_id', 'asset', 'fee_currency', 'valuation_currency'):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"{context}: staking_reward {field} must be a string")
    line = value.get('source_line')
    if line is not None and (type(line) is not int or line < 1):
        raise ValueError(f"{context}: staking_reward source_line must be a positive integer or null")
    for field in ('gross_quantity', 'fee_quantity', 'net_quantity',
                  'gross_value', 'fee_value', 'net_value', 'price'):
        v = value.get(field)
        if v is not None and (isinstance(v, bool)
                              or not isinstance(v, (int, float))
                              or not math.isfinite(v)):
            raise ValueError(f"{context}: staking_reward {field} must be a finite number or null")
        if v is not None and field != 'net_value' and v < 0:
            raise ValueError(f"{context}: staking_reward {field} must not be negative")
    valuation = value.get('valuation')
    if valuation is not None:
        if not isinstance(valuation, dict):
            raise ValueError(f"{context}: staking_reward valuation must be an object")
        for field in ('currency', 'date', 'source', 'target_currency',
                      'fx_date', 'fx_source', 'market_symbol'):
            if field in valuation and not isinstance(valuation[field], str):
                raise ValueError(f"{context}: staking_reward valuation {field} must be a string")
        for field in ('price', 'fx_rate'):
            v = valuation.get(field)
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float))
                                  or not math.isfinite(v) or v <= 0):
                raise ValueError(f"{context}: staking_reward valuation {field} must be positive or null")


_JSON_COMMENT_RE = re.compile(r'"(?:[^"\\]|\\.)*"|(#[^\n]*)')


def strip_json_comments(content: str) -> str:
    """Strip `#`-prefixed comments from a JSON-ish document while
    preserving any `#` that appears inside a string literal. JSON has
    no native comment syntax, but our intermediate files sometimes
    carry user-added comments for legibility. A naive line-strip would
    also drop a (perversely-formatted) JSON value beginning at column 0
    with `#`; this string-aware pass walks string literals first so the
    `#`-as-data case is safe.

    A text with no `#`, or one that is valid JSON as it stands (every
    `#` is then inside a string literal), has nothing to strip and is
    returned as is: the pass cost about ten times the parse of a large
    work/ file (TJ-0240)."""
    if '#' not in content:
        return content
    try:
        json.loads(content)
    except (ValueError, RecursionError):
        pass
    else:
        return content
    return _JSON_COMMENT_RE.sub(
        lambda m: '' if m.group(1) is not None else m.group(0),
        content,
    )


# Every TaxTransaction field that holds a calendar date (YYYY-MM-DD).
DATE_FIELDS = ('date', 'date_settle', 'lot_date', 'record_date', 'ex_date')

_LOOSE_DATE_RE = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})')


def canonical_date(value: Any, ctx: str, field: str = 'date') -> str:
    """`value` written as a canonical ISO date YYYY-MM-DD. An unpadded
    month or day ("2025-2-1") is padded; a value that is not a real
    calendar date in year-month-day order is a ValueError naming `ctx`
    and `field` (issue #5: the engines compare date strings)."""
    m = _LOOSE_DATE_RE.fullmatch(str(value).strip())
    try:
        if not m:
            raise ValueError
        return datetime(int(m.group(1)), int(m.group(2)),
                        int(m.group(3))).strftime('%Y-%m-%d')
    except ValueError:
        raise ValueError(
            f"{ctx}: impossible {field}={value!r} (not a real calendar "
            f"date written YYYY-MM-DD) — fix the input data.") from None


@functools.lru_cache(maxsize=1)
def _tx_field_names():
    """TaxTransaction's constructor keywords, read once (reading the
    signature for every row was a third of a large run: TJ-0105)."""
    import inspect
    return inspect.signature(TaxTransaction).parameters.keys()


def _row_number(d: Decimal):
    """A Decimal in a row's number field as the row keeps it: the float
    it was (an int where it has no places), a signed zero -0.0; nan or
    an infinity (one past the float's range too) where it is no finite
    float, refused next (TJ-3317, TJ-3318, TJ-3319). A row's numbers
    stay float: its id hashes their repr, and the engines read them
    exactly (model/money `of`); a Decimal price stopped a split's
    re-denomination (TJ-3312)."""
    if d.is_nan():
        return math.nan
    f = float(d)
    if not math.isfinite(f) or (d.is_zero() and d.is_signed()):
        return f
    return M.from_money(d)


# The fields of a row that name a security (an option on a future carries
# its underlying's prefix at the head of its own symbol).
_SYMBOL_FIELDS = ('symbol', 'symbol_new', 'exercise_of')


def fold_row_symbols(row: dict, ctx: str = "") -> dict:
    """`row` with each symbol as the books spell a future: a `/` or `\\`
    prefix `F:` (lib/futures_prefixes canonical_futures_symbol), as every
    parser and the .tt reader write it, so a hand-made JSON book's
    `/QZM26.US` is one security with an export's `F:QZM26.US` (backlog
    TJ-5160). An id the row states is kept; one computed from the content
    (TaxTransaction.compute_id) hashes the `F:` spelling, as a .tt row's.
    A futures symbol that names no contract (`//QZM26.US`) is refused, a
    ValueError naming the row (`ctx`)."""
    for k in _SYMBOL_FIELDS:
        if isinstance(row.get(k), str):
            try:
                row[k] = canonical_futures_symbol(row[k])
            except ValueError as e:
                raise ValueError(f"{ctx or 'a row'}: {k}: {e}") from None
    return row


def _check_row_evidence(clean_t: dict, _ctx: str) -> None:
    """A row's reward evidence (on its DIVIDEND leg only) and its
    `cash_leg` (one of row_types CASH_LEG_KINDS: an unknown kind read as
    none at all would move the row's cash with no word)."""
    if clean_t.get('staking_reward') is not None:
        validate_staking_reward(clean_t['staking_reward'], _ctx)
        if clean_t.get('action') != 'DIVIDEND':
            raise ValueError(f"{_ctx}: staking_reward belongs on the DIVIDEND income leg only")
    if clean_t.get('cash_leg') not in (None, ''):
        from taxjson.lib.row_types import CASH_LEG_KINDS
        if clean_t['cash_leg'] not in CASH_LEG_KINDS:
            raise ValueError(
                f"{_ctx}: cash_leg={clean_t['cash_leg']!r} is not one of "
                f"{', '.join(CASH_LEG_KINDS)} — fix the input data.")


def coerce_transaction_row(t, i: int, ctx_prefix: str) -> TaxTransaction:
    """One row's coercion + validation — the shared body of
    load_transactions and pipeline.load_stdin_transactions, so the
    stdin path cannot drift back to silently dropping rows or skipping
    the FUZZ #K hard guards. Handles TaxTransaction passthrough,
    mapping coercion, `qty` aliasing, known-field filtering, and the
    non-finite / impossible-date / SPLIT-ratio refusals."""
    valid_keys = _tx_field_names()
    if isinstance(t, TaxTransaction):
        return t
    if not isinstance(t, dict):
        # Try last-resort coercion via mapping protocol, mirroring
        # the historical sort.py behavior. A row that can't be
        # dict()'d is data corruption — refuse to silently drop
        # it. One missing buy/sell can invalidate an entire ACB
        # pool downstream, and the silent skip would make the
        # resulting wrong gain numbers very hard to trace back.
        try:
            t = dict(t)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"{ctx_prefix}: transaction at index "
                f"{i} is not a TaxTransaction, dict, or mapping-"
                f"coercible value — got {type(t).__name__}={t!r} "
                f"({e}). Refusing to drop the row silently because "
                f"a missing buy/sell would corrupt the ACB pool "
                f"downstream. Fix the upstream emitter or hand-edit "
                f"the JSON to remove the malformed entry."
            )
    if 'qty' in t and 'quantity' not in t:
        t = {**t, 'quantity': t['qty']}
    clean_t = {k: v for k, v in t.items() if k in valid_keys}
    # Hard input guards (FUZZ #K) — refuse cleanly instead of
    # letting garbage reach the engines (NaN drove the Canada
    # engine into a decimal.InvalidOperation traceback and the USA
    # engine into NaN outputs; SPLIT ratio <= 0 silently corrupted
    # pools; impossible calendar dates passed straight through).
    import math
    _ctx = (f"{ctx_prefix}: transaction at index {i} "
            f"(id={t.get('id', '?')!r}, symbol="
            f"{t.get('symbol', '?')!r})")
    _check_row_evidence(clean_t, _ctx)
    # Type funnel (stage-tools audit): every numeric field must be a
    # real number (a numeric STRING is coerced with float(); null,
    # bools, lists, non-numeric strings are refused) and every string
    # field must be a str (null falls back to the field's default).
    # Before this, `"net_amount": null` reached compute_id's float()
    # as a bare TypeError traceback, `"quantity": "10"` crashed the
    # phantom walk's arithmetic, and `"symbol": 0` crashed the engines
    # — none of them caught by the tools' ValueError handlers.
    for _fld in ('quantity', 'price', 'proceeds', 'commission', 'fee',
                 'net_amount', 'gross_amount', 'multiplier',
                 'market_value', 'delivered_cost', 'corp_parent_qty'):
        if _fld not in clean_t:
            continue
        _v = clean_t[_fld]
        if _v is None or isinstance(_v, bool) \
                or not isinstance(_v, (int, float, Decimal, str)):
            raise ValueError(
                f"{_ctx}: {_fld}={_v!r} is not a number (got "
                f"{type(_v).__name__}) — fix the input data.")
        if isinstance(_v, (str, Decimal)):
            try:
                _v = float(_v) if isinstance(_v, str) else _row_number(_v)
            except ValueError:
                raise ValueError(
                    f"{_ctx}: non-numeric {_fld}={clean_t[_fld]!r} — "
                    f"fix the input data.")
            clean_t[_fld] = _v
        if isinstance(_v, float) and not math.isfinite(_v):
            raise ValueError(f"{_ctx}: non-finite {_fld}={_v!r} — "
                             f"fix the input data.")
    for _fld in ('action', 'date', 'symbol', 'currency', 'time',
                 'date_settle', 'account', 'type', 'description',
                 'symbol_new', 'corp_event_id', 'corp_election', 'id',
                 'record_date', 'ex_date', 'income_label',
                 'dealer_country', 'issuer_country', 'broker_time',
                 'security_name', 'open_close', 'short_marks', 'no_clock', 'broker_basis',
                 'broker_lots', 'exercise_of', 'corp_cash', 'no_cash',
                 'cash_leg',
                 'no_share_count', 'contract_size_basis',
                 'lot_date', 'opening_cut_legs', 'journal_pair', 'isin',
                 'event_source', 'listing_currency', 'settle_native',
                 'commission_refunds', 'trade_currency', 'swap_fee', 'security_type'):
        if _fld not in clean_t:
            continue
        _v = clean_t[_fld]
        if _v is None:
            if _fld in ('action', 'date'):
                raise ValueError(
                    f"{_ctx}: required field {_fld} is null — fix the "
                    f"input data.")
            if _fld != 'id':
                del clean_t[_fld]      # dataclass default applies
        elif not isinstance(_v, str):
            raise ValueError(
                f"{_ctx}: {_fld}={_v!r} must be a string (got "
                f"{type(_v).__name__}) — fix the input data.")
    for _fld in ('action', 'date'):
        if _fld not in clean_t:
            raise ValueError(
                f"{_ctx}: required field {_fld} is missing — fix the "
                f"input data.")
    # The engines order same-day rows by the time STRING: '9:30:00'
    # sorted after its own '09:30:01' superficial-loss bump, and '09:30'
    # crashed the bump's clock arithmetic (audit S071-13). An unpadded
    # or seconds-less clock time is written HH:MM:SS; anything else is
    # refused.
    _tm = clean_t.get('time')
    if _tm:
        _m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', _tm.strip())
        if not _m or int(_m.group(1)) > 23 or int(_m.group(2)) > 59 \
                or int(_m.group(3) or 0) > 59:
            raise ValueError(
                f"{_ctx}: time={_tm!r} is not a clock time HH:MM:SS — "
                f"fix the input data.")
        clean_t['time'] = (f"{int(_m.group(1)):02d}:{_m.group(2)}:"
                           f"{_m.group(3) or '00'}")
    # A trade row without its quantity or money gets the dataclass
    # default 0.0 — a buy at $0 cost (the whole sale becomes gain) or a
    # sale at $0 proceeds (audit R1-162). The loader serves many tools
    # (merge, ticker-map, ...), so it only MARKS the row;
    # require_trade_fields() refuses it where money is computed.
    _missing = ()
    if clean_t.get('action') in ('BUYSELL', 'ASSIGN'):
        _missing = tuple(f for f in ('quantity', 'net_amount')
                         if f not in clean_t)
    # The engines order rows by comparing date STRINGS, so every date is
    # written YYYY-MM-DD here: an accepted "2025-2-01" sorted after
    # "2025-10-01", which moved FIFO/ACB lots and turned a long sale
    # into a short cover (issue #5). An unpadded month or day is padded;
    # anything else that is not a real calendar date is refused.
    for _fld in DATE_FIELDS:
        _d = clean_t.get(_fld)
        if _d:
            clean_t[_fld] = canonical_date(_d, _ctx, _fld)
    # CA-DATE-03 / US-DATE-04: a settlement never precedes its trade;
    # the parsers refuse one, and the JSON path (taxjson-gains on a
    # hand-written file, taxjson-validate) now does too — the tax year
    # follows the settle date, so the row moved into the prior year
    # silently (audit A2-0959).
    _td, _sd = clean_t.get('date'), clean_t.get('date_settle')
    if (clean_t.get('action') in ('BUYSELL', 'ASSIGN') and _td and _sd
            and str(_sd) < str(_td)):
        raise ValueError(
            f"{_ctx}: date_settle {_sd} is before the trade date {_td} — "
            f"a settlement never precedes its trade, and the tax year "
            f"follows the settle date; fix the row (or drop date_settle "
            f"for the standard cycle).")
    if clean_t.get('action') == 'SPLIT' \
            and float(clean_t.get('quantity') or 0) <= 0:
        raise ValueError(
            f"{_ctx}: SPLIT ratio must be > 0 (got "
            f"{clean_t.get('quantity')!r}) — a zero/negative ratio "
            f"is never a real corporate action.")
    tx = TaxTransaction(**fold_row_symbols(clean_t, _ctx))
    # Where the row came from, for require_computable_rows' messages.
    tx._row_ctx = _ctx
    if _missing:
        tx._missing_trade_fields = (_ctx, _missing)
    return tx


def load_transactions(path: Path) -> List[TaxTransaction]:
    """Canonical taxjson loader — the single implementation that every
    bin tool should use. Handles:

    * `#`-prefixed comments (full-line and trailing). String-aware so
      a `#` inside a JSON string value is preserved.
    * `qty` → `quantity` aliasing (older parsers/tests emit `qty`).
    * Filtering to TaxTransaction's known fields so extra keys from
      enrichment passes don't blow up the dataclass init.
    * Already-TaxTransaction items in the list (passthrough — useful
      when an in-memory pipeline composes loaders).
    * The FUZZ #K hard input guards (via coerce_transaction_row).
    """
    return [coerce_transaction_row(t, i, f"load_transactions({path})")
            for i, t in enumerate(read_book_rows(path))]


def read_book_rows(path: Path) -> list:
    """A book's rows as the file holds them (load_transactions' reader,
    before each row's coercion): UTF-8 with or without a BOM, `#`
    comments stripped, an object with a "transactions" list or a bare
    list. Raises InputReadError (not UTF-8), JSONDecodeError naming the
    file, or ValueError (no list of rows)."""
    from taxjson.lib.cli_diag import InputReadError
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        # A BOM (a hand-written book saved by a Windows editor) is
        # dropped, as json_input and the .tt reader do (A2-1412).
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        # An OSError-class error, not the ValueError the data guards
        # raise: an unreadable file is an environment error (exit 2),
        # and it names the file (audit S070-23).
        raise InputReadError(
            f"cannot read {path}: not UTF-8 text (byte "
            f"0x{raw[e.start]:02x} at offset {e.start})") from None
    try:
        data = json.loads(strip_json_comments(text))
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"{path}: {e.msg}", e.doc,
                                   e.pos) from None

    if isinstance(data, dict) and "transactions" not in data:
        # {"Transactions": [...]} (or any object without the key) was
        # read as an empty book: empty gains and inventory at exit 0
        # (issue #6). The stdin loader already refused it.
        keys = ", ".join(sorted(map(str, data))) or "none"
        raise ValueError(f"{path}: no \"transactions\" list (keys: "
                         f"{keys}) — expected a JSON object with a "
                         f"\"transactions\" list, or a bare list of rows")
    txs = data["transactions"] if isinstance(data, dict) else data
    if not isinstance(txs, list):
        # {"transactions": 5} or a bare scalar was a TypeError traceback
        # in every tool that loads a book (audit S042-18).
        raise ValueError(f"{path}: expected a JSON object with a "
                         f"\"transactions\" list (or a bare list of "
                         f"rows), got {type(txs).__name__}")
    return txs

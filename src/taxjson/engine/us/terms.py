"""US terms both the scan and the walk read: the holding period, section 1256
contracts, share precision, per-account rename ratios, spin-off entitlement,
the section 356 reorganization type, the lot event types and the epsilons.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Tuple, TypeAlias, cast
from datetime import datetime

from taxjson.model import money as M
from taxjson.lib.option_symbols import (
    _FUTURES_PREFIX_RE, is_option_symbol, parse_option_underlying,
)
from taxjson.lib.row_types import SPINOFF_355_TYPE
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.splits import _multi_ratio_rename_groups

if TYPE_CHECKING:
    from taxjson.engine.rows import RowId
    from taxjson.engine.splits import _RenameKey


# A multi-ratio merger's rows on a per-account basis: row id -> (the
# event's key, {account: that account's delivered ratio}).
_AcctRatios: TypeAlias = Dict["RowId", Tuple["_RenameKey", Dict[str, float]]]


def held_more_than_one_year(acq_date_str: str, disp_date_str: str) -> bool:
    try:
        acq = datetime.strptime(acq_date_str, '%Y-%m-%d')
        disp = datetime.strptime(disp_date_str, '%Y-%m-%d')
    except ValueError:
        return False
    # Pub 550: the holding period starts the day AFTER
    # acquisition, so "more than one year" is disp > the
    # anniversary. Rev. Rul. 66-7: property acquired on the
    # LAST day of a month starts its period on the 1st of the
    # next month and is held more than one year on the 1st of
    # that month a year later — so Feb 29 -> LT from Mar 1 of
    # the next year (not Mar 2), and Feb 28 of a common year
    # -> LT from Mar 1 as well (2026-09 US-engine audit; the
    # old code treated only the leap day specially and got
    # both directions wrong).
    import calendar as _cal
    last_day = _cal.monthrange(acq.year, acq.month)[1]
    if acq.day == last_day:
        nm_year = acq.year + (1 if acq.month == 12 else 0)
        nm_month = 1 if acq.month == 12 else acq.month + 1
        return disp >= datetime(nm_year + 1, nm_month, 1)
    anniversary = acq.replace(year=acq.year + 1)
    return disp > anniversary


def outside_1091(sym: str) -> bool:
    """A §1256 contract: a futures contract, an option on one, or a
    broad-based index option (lib/futures.section_1256_kind, the list
    Form 6781's rows come from). It is marked to market and outside the
    wash-sale rule: its loss is never disallowed (tax-logic US-WASH-18;
    TJ-0506: an index option's loss was washed, though Form 6781's list
    named it). US only: Canada's s.54 covers any property."""
    s = str(sym or '')
    if is_option_symbol(s):
        if _FUTURES_PREFIX_RE.match(parse_option_underlying(s) or ''):
            return True
    # A plain future is one unless the ticker.map declares it a
    # security future (SECFUT; section_1256_kind, TJ-0730).
    from taxjson.lib.futures import section_1256_kind
    return bool(section_1256_kind(s))


# A share count at the share pools' precision (pool_eps._SHARE_QTY_EPS,
# six decimals).
_SHARE_QTY_PLACES = 6


def share_qty(q: float) -> float:
    """`q` shares at the engine's share precision (_SHARE_QTY_PLACES): a
    count derived from a money ratio (a loss's part no purchase replaced,
    100 x (1 - 90/100) = 9.999999999999998) is saved and shown as 10
    (backlog TJ-0634)."""
    return cast("float", M.units(float(q), _SHARE_QTY_PLACES) + 0.0)


def _per_account_rename_ratios(taxable: List[TaxTransaction],
                               sheltered: List[TaxTransaction],
                               affiliated: List[TaxTransaction]
                               ) -> _AcctRatios:
    """A US blended book (FIFO lots per account, US-BASIS-01): each row
    of a multi-ratio merger event scales and migrates ITS account's
    lots at its own delivered ratio (US-CORP-14). Returns, for every
    such row, tx.id -> (event key, {account: that account's ratio}).
    The 2026-10 filing audit (F1): the first row scaled and migrated
    every account's lots at its ratio; the second found the source
    symbol gone, so an account that received 42 shares for 40 sold
    42 against 40 and opened a two-share phantom short."""
    out: _AcctRatios = {}
    for key, rows in _multi_ratio_rename_groups(
            (taxable, sheltered, affiliated)).items():
        ratio_of: Dict[str, float] = {}
        for _li, _i, t in rows:
            ratio_of.setdefault(t.account, float(t.quantity or 0))
        for _li, _i, t in rows:
            out[t.id] = (key, ratio_of)
    return out


def spin_entitlement(ex_date: str, parent_qty: float, before: float, held: float) -> str:
    """How a §355 row's stated dates pick the parent's entitled lots:
    'dated' (only the lots before the date), 'due_bill' (every lot held
    on the pay date: the broker paid on all of them, a purchase after
    the record date came with the right) or 'unknown' (every lot, and
    said). `parent_qty`: the shares the broker paid on (0: not stated);
    `before`, `held`: the shares held from before the date, and on the
    pay date. An ex-date settles it; a record date alone does not when
    the shares traded with a due bill (ex after the pay date, the usual
    case for a large spin-off; TJ-1161). A count equal to one of the two
    at the share precision is that one; only then does a count printed
    in whole shares (within one share) pick a holding, and one within a
    share of both picks neither. Paid on 1, with 0.4 held before and 1
    that day, is not the 0.4 (TJ-4988); nor is it surely the whole
    holding: a whole count equal to the shares held that day and within
    a share of those held before (100, with 99.6 before and 100 that
    day) may be either, rounded, and picks neither; a count with a
    fraction is the holding it equals."""
    if ex_date:
        return 'dated'
    if parent_qty <= 0:
        return 'unknown'
    exact = 0.5 * 10 ** -_SHARE_QTY_PLACES
    if abs(parent_qty - before) <= exact:
        return 'dated'
    near_before = abs(parent_qty - before) < 1.0
    if abs(parent_qty - held) <= exact:
        part = parent_qty % 1.0
        whole = min(part, 1.0 - part) <= exact
        return 'unknown' if whole and near_before else 'due_bill'
    near_held = abs(parent_qty - held) < 1.0
    if near_before and near_held:
        return 'unknown'
    if near_before:
        return 'dated'
    if near_held:
        return 'due_bill'
    return 'unknown'


REORG_356_TYPE = 'reorg_356'
_LOT_EVENT_TYPES = (SPINOFF_355_TYPE, REORG_356_TYPE)


# The US engine's quantity tolerance (`run.epsilon`), and the same figure
# as money, read once (TJ-3089).
_US_EPSILON = 1e-8
_US_MONEY_EPSILON = M.of(_US_EPSILON)

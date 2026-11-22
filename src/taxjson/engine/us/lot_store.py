"""The US engine's lots: a lot's wash-sale add, the denial's per-chunk record,
the inventories' keys and copies.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, TypeAlias, Union, cast

from decimal import Decimal

from taxjson.lib.option_symbols import exercise_target, is_option_symbol
from taxjson.rules import ids as R
from taxjson.engine.consts import _ZERO
from taxjson.engine.premium_ledger import Taken, nothing_taken
from taxjson.engine.rows import override_record

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.us.run import _USRun


# A US lot (step 13 makes it a class): `qty` (units), a long's `cost_basis`
# or a short's `proceeds` and its `wash_deferred` (Decimal), `date`, its
# `effective_acq_date` (a short's `effective_open_date`), the row `id`;
# `tainted`, `unknown_from`, `moved_from`, `date_settle`, `_rep_chunk`
# when set.
_USLot: TypeAlias = Dict[str, Any]
# An inventory's key: the symbol, or (account, symbol) on a per-account
# basis (`_us_ikey`).
_IKey: TypeAlias = Union[str, Tuple[str, str]]
# The long or the short lots, each pool in FIFO order.
_USInventory: TypeAlias = Dict[_IKey, List[_USLot]]
# The long and the short lots at one moment (`_us_lots_copy`).
_USLotsNow: TypeAlias = Tuple[_USInventory, _USInventory]


def _lot_wash_held(lot: Dict[str, Any]) -> Decimal:
    """What a long US lot's basis still holds of its §1091(d) add: a
    return of capital can have left it less (backlog TJ-1017)."""
    return cast("Decimal", min(lot.get('wash_deferred', Decimal(0)),
                               max(Decimal(0), lot['cost_basis'])))

def _us_ov_take(run: _USRun, tx_: TaxTransaction, rep_tx: TaxTransaction, is_sh: bool,
                is_aff: Optional[bool], q: float, amt: Decimal, share: List[Decimal]) -> None:
    """A replacement the denial would have used: on the row's record
    and on `share`, the chunk's own [disallowed, permanent] (TJ-3095).
    The would-be denial's rule is the filing position's (the run's
    `rules`, under `override_record`: a retest's denial of the same row
    keeps its own)."""
    rec = run._ov_would.setdefault(tx_.id, _LO.new_record())
    run.rules.setdefault(tx_.id, {})[override_record(tx_.id)] = R.US_WASH_25
    rec['disallowed'] += amt
    share[0] += amt
    if is_sh or is_aff:
        rec['permanent'] += amt
        share[1] += amt
    _LO.add_replacement(rec, rep_tx, q, amt,
                        'sheltered' if is_sh
                        else 'affiliated' if is_aff else 'taxable')

def _us_ov_share(run: _USRun, tx_: TaxTransaction) -> Optional[List[Decimal]]:
    """A chunk's own share of the denial a filing position keeps from
    being applied, [disallowed, permanent], for its gain row (a sale
    over several lots is several rows with one id, each lot matched on
    its own: lib/loss_overrides row_share, TJ-3095); None when no line
    names the row."""
    return ([_ZERO, _ZERO]
            if tx_.id in run._ov_plan.row_of else None)

def _us_ov_loss(run: _USRun, tx_: TaxTransaction, q: float, amt: Decimal,
                share: Optional[List[Decimal]]) -> bool:
    """The chunk's loss on the named row's record; False when no line
    names the row (`share` is None: _us_ov_share)."""
    if share is None:
        return False
    rec = run._ov_would.setdefault(tx_.id, _LO.new_record())
    rec['qty'] += q
    rec['loss'] += amt
    return True

def _us_take_option_adj(run: _USRun, tx: TaxTransaction) -> Taken:
    # What the row takes of the staged premiums (`Taken`: the amount,
    # its deferral part, backlog TJ-0061, its taint and its chunks);
    # nothing for a row that is no assignment's stock leg.
    if (is_option_symbol(tx.symbol) or exercise_target(tx)
            or tx.action not in ('BUYSELL', 'ASSIGN')):
        return nothing_taken()
    return run.pending_option_adjustments.take(tx)

def _us_real_stamp(tm: str) -> bool:
    return bool(tm) and tm not in ('00:00:00', '00:00')

# Blended (combined multi-account) mode: FIFO basis pools are
# per-(account, symbol) — the IRS keys basis per account — while
# everything symbol-keyed (replacement lists, wash matching,
# currency, traces) stays cross-account, which is exactly the
# §1091 scope. Single-book callers (per_account_basis=False)
# keep plain symbol keys — bit-identical behavior.
def _us_ikey_on(run: _USRun, acct: str, sym: str) -> _IKey:
    """`_us_ikey` over this run's basis (the walk's form)."""
    return _us_ikey(acct, sym, per_account_basis=run.per_account_basis)


def _us_ikey(acct: str, sym: str, *, per_account_basis: bool) -> _IKey:
    return (acct, sym) if per_account_basis else sym

def _us_insert_lots(run: _USRun, ikey_: _IKey, new_lots: List[_USLot]) -> None:
    """Carried lots join a pool in acquisition order (their
    actual dates; same-date lots by time, then arrival)."""
    lots = run.inventory_long.setdefault(ikey_, [])
    lots.extend(new_lots)
    lots.sort(key=lambda l: (l['date'],
                             run._lot_time.get(l.get('id'), '')))

def _us_inv_keys_for(run: _USRun, inv: _USInventory, sym: str) -> List[_IKey]:
    if run.per_account_basis:
        return [k for k in inv if k[1] == sym]
    return [sym] if sym in inv else []

def _us_lots_copy(run: _USRun) -> _USLotsNow:
    """The long and short lots now (each lot's own fields: units,
    basis or proceeds, wash_deferred, dates)."""
    return ({k: [dict(l) for l in v]
             for k, v in run.inventory_long.items()},
            {k: [dict(l) for l in v]
             for k, v in run.inventory_short.items()})

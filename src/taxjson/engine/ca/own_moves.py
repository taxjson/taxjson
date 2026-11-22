"""Own-account moves: a move's sign and units, the sources and taint its legs
carry.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import re
from typing import TYPE_CHECKING, Any, Dict, Iterable, List

from decimal import Decimal

from taxjson.model import money as M
from taxjson.lib.row_types import LOT_MOVE_TYPE
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.journal import _MOVE_DESC_RE, _journal_factor

from taxjson.engine.ca.run_helpers import _futures_names, _ca_short_lot_open

if TYPE_CHECKING:
    from taxjson.engine.ca.holders import _CanadaIteration, _CanadaRun
    from taxjson.engine.ca.pool import AcbPool, PoolBook


# How the move's rows were signed, when the pairing knew the brokers
# (pipeline/stages._move_legs; transfer_in.transfer_sign_convention).
_MOVE_SIGN_RE = re.compile(r'; rows signed by (direction|change)\)$')


def _move_sign(t: TaxTransaction) -> str:
    """"direction", "change" or "" for an own-move leg (backlog TJ-3243)."""
    m = _MOVE_SIGN_RE.search(t.description or '')
    return m.group(1) if m else ''


def _is_lot_move(t: TaxTransaction) -> bool:
    return t.action == 'TRANSFER' and t.type == LOT_MOVE_TYPE


def _own_move_view(transactions: List['TaxTransaction']
                   ) -> List['TaxTransaction']:
    """Canada (CA-ACB-OWN-MOVE): the rows the engine books of the legs
    of a move between two of your own taxable accounts. The ACB is one
    pool across the accounts (s.47), so a book of more than one account
    (the blended pass) books no leg — the pool, its gains and its
    superficial losses are what they are without the move. A book of
    one account is that account's own view: its legs stay, and the
    engine moves the units (_own_move_units) — never a disposition,
    never an acquisition — each said once as a note."""
    legs = [t for t in transactions if _is_lot_move(t)]
    if not legs:
        return transactions
    if len({t.account for t in transactions if t.account}) > 1:
        return [t for t in transactions if not _is_lot_move(t)]
    for t in legs:
        _m = _MOVE_DESC_RE.match(t.description or '')
        _peer = (_m.group(2) if t.quantity < 0 else _m.group(1)) \
            if _m else 'another of your accounts'
        q = abs(t.quantity or 0.0)
        emit_line(
            f"note: {t.symbol}: {q:g} moved "
            f"{'to' if t.quantity < 0 else 'from'} {_peer} on {t.date} "
            f"(your own account): this book has only {t.account}, so "
            + ("they leave it at its average cost with no sale"
               if t.quantity < 0 else
               "their cost is unknown here and its sales of them are "
               "set apart in this account's own view")
            + " — the blended pass of `tjs run`, the figures you "
              "file, keeps one ACB across your taxable accounts "
              "(CA-ACB-OWN-MOVE).")
    return transactions


def _unknown_sources(pool: AcbPool, more: Iterable[Dict[str, Any]]
                     ) -> List[Dict[str, Any]]:
    """The OPENING cost=unknown rows a tainted disposition's record names
    (`unknown_cost_from`): the pool's and `more` (an option's that
    delivered the shares), each once."""
    out = [dict(_s) for _s in pool.unknown_from]
    out += [dict(_s) for _s in more if _s not in out]
    return out



def _own_move_only(pool: AcbPool) -> List[str]:
    """The accounts the pool's unknown-cost units were moved in from,
    when an own move is their only cause (AcbPool.taint); else []."""
    if (pool.tainted and pool.moved_from
            and not pool.taint_other):
        return list(pool.moved_from)
    return []


def _own_move_written(run: _CanadaRun, it: _CanadaIteration, tx: TaxTransaction, units: float,
                      kind: str) -> str:
    """One account's own view of a leg of a move of its WRITTEN option
    contracts between two of the person's taxable accounts (TJ-3192;
    _own_move_units moves bought units; `kind` is the router's reading,
    move_leg_kind: 'written_out' when the account is short the series,
    'written_in' for a negative leg in an account holding none, TJ-3224):
    contracts out leave the symbol's short pool with their own lots'
    premium, no close and no gain; contracts in open a lot at a premium
    unknown in this book (the pool tainted, as an opening's: its closes
    here are left to report by hand). The leg is the short pool's row
    (_short_routed). Returns the trace text."""
    _SHORT_SFX, _ = _futures_names()
    key = tx.symbol + _SHORT_SFX
    pool = it.global_pools.ensure(key)
    run._short_routed.add(tx.id)
    # The leg's effect on the pools' units, for the conservation check
    # (_finish): a written contract leaving is +units, arriving -units,
    # whatever the leg's sign (TJ-3258's one-account view).
    run._ev_memo.setdefault('_move_effect', {})[tx.id] = (
        -units if kind == 'written_in' else units)
    if kind == 'written_in':
        pool.move_in(-units, tx.date)
        _ca_short_lot_open(run, pool, tx, units, 0.0, False)
        _mm = _MOVE_DESC_RE.match(tx.description or '')
        pool.taint(_mm.group(1) if _mm else '')
        return (f"OWN MOVE IN {units:10.4f} written | premium unknown in "
                f"this book (its closes here: unknown cost)")
    held = abs(pool.qty)
    taken, premium = pool.move_out_lots(units, tx.account)
    pool.drain(pool_qty_eps(tx.symbol, held, units))
    return (f"OWN MOVE OUT {units:10.4f} written | Premium_Moved: "
            f"{premium:10.4f} | no close")


def _own_move_units(pools: PoolBook, key: str, tx: TaxTransaction) -> str:
    """One account's own view of a leg of a move between two of your
    taxable accounts (_own_move_view; CA-ACB-OWN-MOVE): units in join
    the pool at a cost unknown in this book (the pool is tainted, as an
    opening balance's: its sales are left to report by hand here);
    units out leave it at its average cost — no disposition, no gain,
    never a superficial-loss replacement (only BUYSELL / ASSIGN rows
    are). Units out beyond the pool's long position have no purchase in
    this book (missing history in the sender): the pool goes short by
    them, tainted, as the history checks read the account. Returns the
    trace text."""
    pool = pools.ensure(key)
    q = float(tx.quantity or 0.0)
    if q > 0:
        pool.move_in(q, tx.date)
        _mm = _MOVE_DESC_RE.match(tx.description or '')
        pool.taint(_mm.group(1) if _mm else '')
        return (f"OWN MOVE IN {q:10.4f} | cost unknown in this book "
                f"(its sales here: unknown cost)")
    held = pool.qty
    take = min(-q, held) if held > 0 else 0.0
    out_cost = Decimal(0)
    if take > 0:
        out_cost, _dw, _cut = pool.move_out(take, 'average')
    rest = -q - take
    eps = pool_qty_eps(tx.symbol, held, q)
    if rest > eps:
        pool.move_beyond(-rest, tx.date, restart=pool.qty >= 0)
    else:
        pool.drain(eps)
    return (f"OWN MOVE OUT {-q:10.4f} | Cost_Moved: {M.of(out_cost):10.4f}"
            f" | Pool_Qty: {pool.qty:10.4f} | no sale"
            + (f" | {rest:g} with no purchase in this book"
               if rest > eps else ""))


def _journal_units(pools: PoolBook, out_leg: TaxTransaction, in_leg: TaxTransaction,
                   eps: float = 1e-9) -> str:
    """One listing journal (LISTING_JOURNAL_TYPE) in a Canadian book: a
    long's units leave the out-leg's pool at its average cost and join
    the in-leg's with that cost restated (_journal_factor), the
    position's start date and holding dates with them. When the out-leg's
    listing holds no long and the in-leg's holds a short, the short
    moves instead (a short journaled shows the opposite legs, audit
    2026-10-24 B13). Units beyond what the source holds have no purchase
    in this book: the source goes short by them and the receiver holds
    them at an unknown cost, both tainted, as an own move's are
    (_own_move_units). Returns the trace text."""
    def pool(sym: str, cur: str) -> AcbPool:
        p = pools.ensure(sym, cur)
        p.stamp_currency(cur)
        return p

    q = abs(float(out_leg.quantity or 0.0))
    a = pool(out_leg.symbol, out_leg.currency)
    b = pool(in_leg.symbol, in_leg.currency)
    if a.qty <= eps and b.qty < -eps:
        src, dst, s_leg, d_leg, sign = b, a, in_leg, out_leg, -1.0
    else:
        src, dst, s_leg, d_leg, sign = a, b, out_leg, in_leg, 1.0
    f = _journal_factor(src.currency, s_leg, d_leg)
    held = src.qty * sign
    take = min(q, held) if held > eps else 0.0
    rest = q - take
    if take > 0:
        cost, dw, cut = src.move_out(take, 'fraction', sign)
        dst.move_in_from(src, take, sign, cost, dw, cut, f, eps)
    if rest > eps:
        for p, d in ((src, -sign), (dst, sign)):
            p.move_beyond(rest * d, out_leg.date, restart=abs(p.qty) <= eps)
    src.drain(pool_qty_eps(s_leg.symbol, held, q))
    return (f"LISTING JOURNAL {s_leg.symbol} -> {d_leg.symbol} "
            f"{take:10.4f}{' (a short)' if sign < 0 else ''} | cost "
            + ("unknown here (no rate)" if f is None else
               f"carried x{float(f):.6g}")
            + (f" | {rest:g} with no purchase in this book"
               if rest > eps else ""))

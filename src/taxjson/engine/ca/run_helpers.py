"""The run's module helpers: a row's holder, pool key, sort date and year, the
split targets.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, TypeAlias, Union

from taxjson.lib.corporate_timeline import event_sort_key

from taxjson.model import money as M
from taxjson.lib.trace_format import SharedTrace
from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.rules import ids as R
from taxjson.engine.consts import _ZERO, _MINUS_MILLI
from taxjson.engine.replacement import receives_on_buy

from taxjson.engine.ca.pools import _ca_touched

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.ca.holders import _CanadaIteration, _CanadaRun, _SLPass
    from taxjson.engine.ca.pools import _Lot
    from taxjson.engine.ca.pool import AcbPool

# Who owns a row's property (_sl_holder): ('taxable',), the taxpayer's one
# s.47 pool, or ('other', account) for a registered or affiliated account.
_Holder: TypeAlias = Tuple[str, ...]
# A row's processing-order key (lib/corporate_timeline.event_sort_key).
_EvKey: TypeAlias = Tuple[Any, ...]
# A rename's (the main pass's key of its SPLIT row, the new symbol), by the
# old symbol (the denial pass's `pas._renames`).
_Renames: TypeAlias = Dict[str, Tuple[_EvKey, str]]


def _sl_holder(run: _CanadaRun, t: TaxTransaction) -> _Holder:
    """Who owns row `t`'s property: the taxable book, or one registered or
    affiliated account ("owns the substituted property" is measured per holder)."""
    if t.id in run.sheltered_ids or t.id in run.affiliated_ids:
        return ('other', t.account)
    return ('taxable',)

def _sl_holder_rank(run: _CanadaRun, t: TaxTransaction) -> int:
    return (2 if t.id in run.affiliated_ids
            else 1 if t.id in run.sheltered_ids else 0)

def _sl_call_und(cls_root: Dict[str, str], sym: str) -> Optional[str]:
    """The underlying stock symbol of an option series, through the class's
    root aliases (`pas._cls_root`)."""
    u = parse_option_underlying(sym)
    return cls_root.get(u.upper(), u) if u else u

# The main pass's own processing order: its fixed rungs, then
# the export's row order at one moment (CA-DATE-14; accounts
# in name order, issue #31). Every same-moment question of ca/denial.py —
# which loss claims a shared replacement first, whether a rebuy
# listed after the loss sale is acquired after it, which of two
# same-moment triggers takes the bump — is answered by this
# order, never by the rows' content-hash id
# (audit A2-0059/0551/0058/0193/0192/0961/0965: a one-cent
# price change used to move a denial).
def _sl_pkey(run: _CanadaRun, pos: Dict[int, int], t: TaxTransaction) -> Tuple[_EvKey, int]:
    """A row's processing key in this pass: its event key, then its position in
    the iteration's rows (`it._pos`, `pas._pos`)."""
    return (_ca_ev_key(run, t), pos.get(id(t), -1))

def _sl_symbol_asof(renames: _Renames, sym: str, hi_key: _EvKey,
                    lo_key: Optional[_EvKey] = None) -> str:
    """The pool that holds shares booked under `sym` by the
    row at main-pass key `lo_key`, as of key `hi_key`. A
    rename moves them only when the main pass applies it
    AFTER that row and at or before `hi_key` — compared on
    the main pass's own ordering (audit S069-14: the clock
    comparison ignored that a SPLIT runs before every
    same-day row, so a trigger booked under the old ticker
    on the rename date — never migrated — had its bump
    sent to the empty new pool and lost)."""
    seen_syms = set()
    while sym in renames and sym not in seen_syms:
        r_key, r_new = renames[sym]
        if (lo_key is None or lo_key < r_key) and r_key <= hi_key:
            seen_syms.add(sym)
            sym = r_new
        else:
            break
    return sym


def _sl_opening_qty(pas: _SLPass, t: TaxTransaction) -> float:
    """The portion of buy `t` that OPENS/extends a long
    position (vs covering a short), from the running balance
    of its holder. Only a long acquisition replaces (s.54;
    CA-SL-07), so there is no short-side variant (A2-1596:
    its branch was never reached)."""
    if id(t) in pas._open_memo:      # (this pass's, TJ-0105)
        return pas._open_memo[id(t)]
    bal = pas._bal_before.get(t.id, 0.0)
    q = t.quantity
    covering = min(q, max(0.0, -bal))
    pas._open_memo[id(t)] = r = max(0.0, q - covering)
    return r

def _sl_wpos(pas: _SLPass, run: _CanadaRun) -> Dict[RowId, int]:
    """Each row's processing position in this pass, for the window's order
    (A2-0965): built by the first denied loss that lists its window."""
    if not pas._wpos_cell:
        pas._wpos_cell.append({t.id: pas._pos.get(id(t), -1) for t in run.all_txs})
    return pas._wpos_cell[0]


@functools.lru_cache(maxsize=None)
def _futures_names() -> Tuple[str, Callable[[Any], str]]:
    """`lib/futures`' short-pool suffix and pool key, imported once (the
    core/futures import cycle keeps them off the module top) and kept: the
    per-row helpers and the SPLIT part read them (TJ-1995)."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX, engine_pool_key
    return SHORT_POOL_SUFFIX, engine_pool_key


# The Canadian engine's helpers that read its frame, as module functions
# (backlog TJ-1848, TJ-1942; step 4r, TJ-1964): each takes `run` (and `it`)
# first and reads its inputs from them, so the holders carry data alone and
# none of these keeps the engine's frame alive (three small closures remain
# in the engine: `_taxable_scope`, the ledger's lambda and the shorts
# recorder's).
def _ca_ev_key(run: _CanadaRun, t: TaxTransaction) -> _EvKey:
    """A row's processing-order key, memoized for the book's rows (the solver's
    own rows are keyed afresh); the pass and the solve read it through `run`."""
    k = run._ev_memo.get(id(t))
    if k is None:
        k = event_sort_key(t, profile='ca_main',
                           date_of=run.get_sort_date)
        if id(t) in run._book_ids:
            run._ev_memo[id(t)] = k
    return k

def _ca_write_year(run: _CanadaRun, tx: TaxTransaction) -> str:
    """The write's year on the project's tax_date basis, the same date the
    return's year filter uses (audit S068-21: with tax_date = "trade", a
    2024-12-31 write settling in 2025 was grant-booked in 2024 although
    since=2025 put it on close timing, and no year's return taxed it)."""
    _d = tx.date if run.grant_on_trade else run.get_sort_date(tx)
    return str(_d or '')[:4]

def _ca_grant_applies(run: _CanadaRun, tx: TaxTransaction) -> bool:
    if not run._grant_mode or not is_option_symbol(tx.symbol or ''):
        return False
    if run.option_grant_since is None:
        return True
    try:
        return int(_ca_write_year(run, tx)) >= int(run.option_grant_since)
    except (TypeError, ValueError):
        return True

def _ca_short_lot_open(run: _CanadaRun, pool: AcbPool, tx: TaxTransaction, units: float,
                       premium: Union[Decimal, float], recognise: bool) -> _Lot:
    """Append a short-option lot of `units` whose (signed, net
    of commission) premium is `premium`; returns the lot."""
    run._lot_seq[0] += 1
    return pool.open_lot(tx_id=tx.id, seq=run._lot_seq[0], units=units, premium=premium,
                         recognise=recognise, date=tx.date,
                         year=_ca_write_year(run, tx), account=tx.account)

def _ca_open_short_option(run: _CanadaRun, pool: AcbPool, tx: TaxTransaction, units: float,
                          premium: Union[Decimal, float], fee_share: float, *,
                          losses: List[Dict[str, Any]], gains: List[Dict[str, Any]],
                          traces: Dict[str, List[str]], trace_sym: str) -> None:
    """A taxable short opening of `units` option contracts for a
    (signed, net) `premium`: record the lot and, under grant
    timing, emit the s.49(1) grant record on the write date, into
    this iteration's `losses`, `gains` and `traces` (the walk hands
    its own; the helper reads nothing the loop rebinds)."""
    rec = _ca_grant_applies(run, tx)
    lot = _ca_short_lot_open(run, pool, tx, units, premium, rec)
    if not rec or units <= 1e-9 or tx.id not in run.taxable_ids:
        return
    symbol = tx.symbol
    _g = M.of(premium)
    _gt: Union[List[str], SharedTrace] = []
    if run.trace:
        if trace_sym not in traces:
            traces[trace_sym] = [f"# --- ACB CALCULATION TRACE: {trace_sym} ---"]
        traces[trace_sym].append(
            f"# {tx.date} WRITE {units:10.4f} @ {M.shown(tx.price, 4):7.4f} | premium {_g:10.4f} recognised now (ITA s.49(1))")
        _gt = SharedTrace(traces[trace_sym])
    rec_d = {
        'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
        'date_settle': tx.date_settle or tx.date,
        'gain': _g, 'qty': units,
        'cost': _g, 'proceeds': _ZERO,
        'disallowed': _ZERO, 'taxable_gain': _g,
        'days_held': 0, 'account': tx.account,
        'currency': tx.currency,
        'commission': M.of(tx.commission or 0) * M.of(fee_share),
        'fee': M.of(tx.fee or 0) * M.of(fee_share),
        'direction': 'SHORT',
        'tainted': pool.tainted,
        'grant': True,
        'note': 'WRITE — premium recognised on grant (ITA s.49(1))',
        'trace': _gt,
    }
    gains.append(rec_d)
    pool.record_grant(lot, rec_d)
    # A write whose commission exceeds its premium is a loss on
    # the grant. It is the same written-option loss close timing
    # books at the buy-back or expiry, so it takes the same rule
    # (audit S069-01): fed to the superficial-loss solver only
    # when the project opts in (option_buyback_loss_superficial,
    # CA-SL-11/12), and then the solver's denial is APPLIED to
    # the grant record (it used to reach wash_sales and the
    # summary but not the record). A tainted (missing-history) pool's
    # loss never feeds the solver (audit S069-00; the close
    # path's gate).
    if (_g < _MINUS_MILLI and run.option_buyback_loss_superficial
            and not pool.tainted):
        _dis = next((v for v in run.final_virtual_txs
                     if v.action == 'DISALLOW' and v.id == tx.id),
                    None)
        if _dis is not None:
            rec_d['disallowed'] = M.kept(_dis.net_amount)
            rec_d['taxable_gain'] = _g + M.kept(_dis.net_amount)
        loss_d = {'tx': tx, 'loss_amount': abs(_g),
                  'qty': units, 'direction': 'SHORT', 'rule': R.CA_SL_12}
        losses.append(loss_d)
        pool.record_grant_loss(lot, loss_d)

def _ca_split_targets(all_txs: Iterable[TaxTransaction]
                      ) -> Tuple[Dict[str, str], Dict[str, Set[str]], Set[str]]:
    """One pass over the SPLIT rows. `opt_prev`: an option series mapped to
    the one it replaced (2:1: one C10 contract becomes two C5 contracts,
    the same right under a new symbol; the day-30 call walk sums each
    lineage whole, keyed by its first symbol, TJ-0716), and `opt_lineage`, each
    root's series. `split_syms`: the
    symbols with a SPLIT row (which symbol a split created on which day is
    the split timeline's, `created_on`)."""
    opt_prev: Dict[str, str] = {}
    split_syms: Set[str] = set()
    for t in all_txs:
        if t.action != 'SPLIT':
            continue
        split_syms.add(t.symbol)
        new = (getattr(t, 'symbol_new', '') or '').strip()
        if new and new != t.symbol and is_option_symbol(t.symbol) and is_option_symbol(new):
            opt_prev.setdefault(new, t.symbol)
    opt_lineage: Dict[str, Set[str]] = {}
    for sym in set(opt_prev) | set(opt_prev.values()):
        opt_lineage.setdefault(_opt_root_in(opt_prev, sym), set()).add(sym)
    return opt_prev, opt_lineage, split_syms


def _ca_opt_root(run: _CanadaRun, sym: str) -> str:
    """An option series' lineage root (the symbol the splits started from)."""
    return _opt_root_in(run._opt_prev, sym)


def _opt_root_in(opt_prev: Dict[str, str], sym: str) -> str:
    seen = {sym}
    while sym in opt_prev and opt_prev[sym] not in seen:
        sym = opt_prev[sym]
        seen.add(sym)
    return sym

def _ca_row_pool_key(run: _CanadaRun, t: TaxTransaction) -> str:
    _SHORT_SFX, _fut_pool_key = _futures_names()
    if t.id in run._short_routed:
        return t.symbol + _SHORT_SFX
    return _fut_pool_key(t)

def _ca_walk_date(run: _CanadaRun, it: _CanadaIteration, t: TaxTransaction,
                  trade: bool = False) -> str:
    """The date the walk stands at on row `t`: its own (sort,
    or trade) date, but a moved option row's is its stock
    leg's when that is earlier (_assign_moved_legs)."""
    d = str((t.date if trade else run.get_sort_date(t)) or '')[:10]
    leg = it._moved_legs.get(t.id)
    if leg is not None:
        ld = str((leg.date if trade else run.get_sort_date(leg))
                 or '')[:10]
        if ld and ld < d:
            return ld
    return d

def _ca_stock_short_key(run: _CanadaRun, it: _CanadaIteration, t: TaxTransaction) -> str:
    """The pool a share or option row of the walk is keyed under: the symbol's short
    pool for a marked short, or for an option series' written contracts (CA-OPT-12,
    TJ-2735) (recorded on the run for the denial walks), else the symbol.
    A superficial-loss addition (a WASH_ ADJUST) is always the symbol's: it is the
    replacement's, a long holding (CA-SL-07, CA-SL-09), never a marked short's."""
    _SHORT_SFX, _ = _futures_names()
    sym = t.symbol
    if it._msr.walk(t):
        run._short_routed.add(t.id)
        return sym + _SHORT_SFX
    if t.action == 'DISALLOW' or (
            t.action == 'ADJUST'
            and not str(t.id or '').startswith('WASH_')):
        # Only the short is open: a row on the symbol is
        # the short's, as it was before shorts had a pool.
        # An ADJUST, when its own account holds the short
        # (a compensation payment while short, CA-ACB-14):
        # one entered in an account that holds no short is
        # that account's cost adjustment, and waits on the
        # symbol's own pool like any ADJUST on a flat pool,
        # warned about (TJ-2710). A DISALLOW is walked
        # before its sale, so it meets a flat main pool only
        # when the loss is a cover's (the short's row then).
        # Not a denied loss's addition: the symbol's own
        # pool, flat here, keeps it for the replacement it
        # next opens or merges in (CA-SL-19); routed to the
        # short it lowered the short's proceeds, moving the
        # loss to the cover's year (TJ-2698).
        sk = sym + _SHORT_SFX
        sp, mp = it.global_pools.get(sk), it.global_pools.get(sym)
        if (sp is not None
                and abs(sp.qty) > pool_qty_eps(sym, sp.qty)
                and (mp is None or abs(mp.qty)
                     <= pool_qty_eps(sym, mp.qty))
                and (t.action == 'DISALLOW'
                     or it._msr.short.get((sym, t.account), 0.0)
                     > pool_qty_eps(sym, sp.qty))):
            return sk
    return sym

def _ca_tl_after(run: _CanadaRun, it: _CanadaIteration, seq: int, prev: TaxTransaction) -> None:
    """The timeline's step after row `prev` (a taxable rename carried; the
    marked shorts swept)."""
    _ren: Dict[Tuple[str, str], str] = {}
    if (prev.action == 'SPLIT' and prev.id not in run.sheltered_ids
            and prev.id not in run.affiliated_ids):
        _new = (prev.symbol_new or '').strip()
        if _new and _new != prev.symbol:
            _ren[('', prev.symbol)] = _new
    assert it._tl is not None, "walked with a timeline only (_walk_rows)"
    it._tl.touch(seq, _ca_touched(prev, it._jlegs), _ren)
    if it._msr.short:
        assert it._tl_shorts is not None, "set with _tl (_ca_solve)"
        # The shorts that are open, in the table's order. A key at 0 units
        # (a covered short stays in the table) has no state, so its check
        # records nothing unless the recorder's last state of it is not the
        # flat one: one it still holds is in the sweep's own labels, and
        # one it does not is in `loose` (backlog TJ-4977).
        # `steady`: a short's state (_ca_short_state) reads the pools, the
        # router's table and its lots, none of which a check writes.
        _loose = it._tl_shorts.loose()
        it._tl_shorts.sweep(seq, [(_a, _s) for (_s, _a), _u in it._msr.short.items()
                                  if _u > 0 or (_loose and (_a, _s) in _loose)],
                            steady=True)


# The Canadian engine's helpers that read nothing of its frame, as module
# functions (step 4k of the engine refactor, backlog TJ-1848), called directly
# since step 4r.
def _ca_sort_date(tx: TaxTransaction) -> str:
    return tx.date_settle if tx.date_settle else tx.date

def _ca_trade_money(tx: TaxTransaction) -> Decimal:
    """The trade's money in pool terms: a BUY's cost (magnitude —
    parsers and .tt books spell it either sign), a SELL's
    proceeds SIGNED. Schema: trade net_amount is positive and
    direction lives in the quantity, so a negative SELL amount
    only arises when the commission exceeds the gross (closing a
    worthless option for $0.01, writing one for less than the
    fee): the proceeds really are negative. abs() booked them
    as positive proceeds (a -220.90 loss reported as -201.00;
    2026-09 engine audit)."""
    _n = M.of(tx.net_amount or 0.0)
    if (tx.type or '') == 'futures_settlement':
        # A futures fill on the settlement basis (lib/futures.py):
        # net_amount is the realized P/L, SIGNED (+ received,
        # - paid), 0 on an opening. A sell's proceeds are it; a
        # buy (a short's cover) costs its negation, so a short
        # closed at a profit realizes exactly the P/L.
        return _n if float(tx.quantity or 0.0) < 0 else -_n
    if receives_on_buy(tx):
        return _n
    return _n if float(tx.quantity or 0.0) < 0 else abs(_n)


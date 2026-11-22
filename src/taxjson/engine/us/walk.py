"""The US engine's row loop and its per-action blocks: lot moves, splits,
adjusts, stock dividends, spin-offs, reorganizations, journals.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, TypeAlias, cast
from datetime import datetime

from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.lib.option_symbols import exercise_target, is_option_symbol
from taxjson.lib.row_types import LOT_MOVE_TYPE, SPINOFF_355_TYPE, is_stock_dividend
from taxjson.engine.assign import _day_gap
from taxjson.rules import ids as R
from taxjson.engine.consts import _ZERO, _HALF_CENT
from taxjson.engine.journal import (
    _MOVE_DESC_RE, _opening_source, _is_listing_journal, _journal_factor,
)
from taxjson.engine.replacement import receives_on_buy
from taxjson.engine.us.lot_store import (
    _us_ikey_on, _us_insert_lots, _us_inv_keys_for, _us_lots_copy,
)
from taxjson.engine.us.terms import (
    held_more_than_one_year, spin_entitlement, REORG_356_TYPE, _US_MONEY_EPSILON,
)
from taxjson.engine.us.wash import (
    _us_split_covers, _us_note, _us_rep_units_factor, _us_grow_rep_capacity, _us_allot_sold_shares,
    _us_apply_other_stkdivs, _us_split_rep_refs,
)
from taxjson.engine.us.lots import (
    _us_move_long_lots, _us_draw_long_lots, _us_tl_label, _us_tl_held,
)

from taxjson.engine.us.trades import _us_walk_buy, _us_walk_sell, _us_deliver_to_short

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.splits import _RenameKey
    from taxjson.engine.us.lot_store import _IKey, _USInventory, _USLotsNow
    from taxjson.engine.us.run import _USRun
    from taxjson.engine.us.trades import _GainRow
    from taxjson.engine.us.wash import _LeftChunk


# One old-share block of a §356 exchange (`_us_walk_reorg`): its fraction of
# the shares given up, the new shares' basis, its purchase date, its holding
# period's start, tainted, its wash-sale deferral, the OPENING row behind an
# unknown cost, and the chunk of its never-matched units (TJ-2700).
_ReorgBlock: TypeAlias = Tuple[Decimal, Decimal, str, str, bool, Decimal,
                               Optional[Dict[str, Any]], Optional["_LeftChunk"]]


def _us_walk_lot_move(*, _book_accounts: Set[str], _moves_done: Set[RowId], ikey: _IKey, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction) -> None:
    """A lot move between two of the user's own taxable accounts (a custody transfer, not a
    sale): the lots leave the source account's inventory with their basis and dates and
    land in the target's, the move's two rows paired, a shortfall against what the source
    holds noted; the trace line."""
    if tx.id in _moves_done:
        return
    _other = run._move_pair.get(tx.id)
    if _other is None:
        _mm = _MOVE_DESC_RE.match(tx.description or '')
        _peer = (_mm.group(2) if _mm and tx.quantity < 0
                 else _mm.group(1) if _mm else None)
        if _peer is None or _peer in _book_accounts:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: own-account move: "
                  f"{symbol} {tx.quantity:+g} in {tx.account} "
                  f"on {tx.date} has no matching leg — not "
                  f"booked.")
            return
        # A book without the other account (one account's
        # own view): the shares leave the sender with no
        # disposition; the receiver holds them with their
        # basis unknown HERE — the blended pass, which has
        # both accounts, carries the basis (US-BASIS-05).
        _shv = (sum(l_['qty'] for l_ in run.inventory_short.get(ikey, []))
                if tx.quantity > 0 else 0.0)
        if tx.quantity < 0:
            _us_move_long_lots(run, ikey, None, symbol,
                            -tx.quantity, tx.date)
        else:
            # `moved_from`: a sale of them says their basis
            # is in the blended figures (backlog TJ-0279).
            _us_insert_lots(run, ikey, [{
                'qty': tx.quantity,
                'cost_basis': Decimal(0),
                'wash_deferred': Decimal(0),
                'date': tx.date,
                'effective_acq_date': tx.date,
                'id': tx.id, 'tainted': True,
                'moved_from': _peer}])
        _us_note(run, tx.date,
              f"note: {symbol}: {abs(tx.quantity):g} moved "
              f"{'to' if tx.quantity < 0 else 'from'} {_peer} "
              f"on {tx.date} (your own account): this book has "
              f"only {tx.account}, so "
              + ("the lots leave it with no sale"
                 if tx.quantity < 0 else
                 "their basis is unknown here")
              + " — the blended pass of `tjs run` carries "
                "the basis and purchase dates (US-BASIS-05)."
              + (f" {tx.account} is short {_shv:g} of it: this view "
                 f"leaves that short open beside them, where the "
                 f"blended pass (the tax books) closes it with the "
                 f"moved shares at their basis, which this book does "
                 f"not have." if _shv > run.epsilon else ""))
        return
    _moves_done.update((tx.id, _other.id))
    _out, _in = ((tx, _other) if tx.quantity < 0
                 else (_other, tx))
    if not run.per_account_basis:
        return          # one pool: nothing moves
    _q = -_out.quantity
    _ks = _us_ikey_on(run, _out.account, symbol)
    _kd = _us_ikey_on(run, _in.account, symbol)
    # Shares moved into an account short the security close the
    # short first, on the move's date at their own basis (the
    # owner's ruling on backlog TJ-3424, 2026-11-13: net them):
    # a cover row in the tax books, said on every run. Only the
    # rest move as lots. §1259 is not modelled.
    _sh = sum(l_['qty'] for l_ in run.inventory_short.get(_kd, []))
    _rows: List[_GainRow]
    _cov, _rows = 0.0, []
    if _sh > run.epsilon:
        _held = sum(l_['qty'] for l_ in run.inventory_long.get(_ks, []))
        _cov, _rows = _us_deliver_to_short(
            run, _in, _kd, _us_draw_long_lots(
                run, _ks, symbol, min(_q, _held, _sh), tx.date),
            Decimal(1), symbol_traces, tax_books=True)
    _moved = _us_move_long_lots(run, _ks, _kd, symbol, _q - _cov, tx.date)
    _got = _cov + _moved
    _miss = _q - _got
    if _miss <= max(run.epsilon, run._dust_noise(_q)):
        _miss = 0.0
    _more = 0.0
    if _miss and _sh - _cov > run.epsilon:
        # Units the sender did not hold cover what is left of the
        # short first, at an unknown cost, as a journal's do
        # (TJ-3439; backlog TJ-3456).
        _more, _r2 = _us_deliver_to_short(run, _in, _kd, [{
            'qty': min(_miss, _sh - _cov), 'cost_basis': Decimal(0),
            'date': tx.date, 'tainted': True}], Decimal(1), symbol_traces,
            tax_books=True)
        _rows += _r2
        # ... and the sender is short by them, as a journal's source
        # is (the units left it: the share count holds; TJ-3465).
        run.inventory_short.setdefault(_ks, []).append({
            'qty': _more, 'proceeds': Decimal(0),
            'wash_deferred': Decimal(0), 'date': tx.date,
            'effective_open_date': tx.date, 'id': _out.id,
            'tainted': True})
    if _rows:
        _us_note(run, tx.date, _us_move_cover_line(
            symbol, tx.date, _out.account, _in.account, _q, _sh, _cov,
            _more, _moved, _rows))
    if _miss:
        _us_note(run, tx.date,
              f"warning: ATTENTION: own-account move: "
              f"{symbol}: {_q:g} moved from {_out.account} to "
              f"{_in.account} on {tx.date}, but {_out.account} "
              f"held {_got:g} — the other {_miss:g} have "
              f"no purchase in the books (missing history in "
              f"{_out.account}); "
              + (f"{_more:g} of them closed {_in.account}'s short at an "
                 f"unknown cost, and {_out.account} is booked short by "
                 f"them." if _more else
                 f"{_in.account}'s sales of them read as a short.")
              + (f" The other {_miss - _more:g} reach no account: "
                 f"{_in.account}'s sales of them read as a short."
                 if _more and _miss - _more > run.epsilon else ""))
    if run.trace:
        symbol_traces[symbol].append(
            f"# {tx.date} OWN MOVE {_got:10.4f} | "
            f"{_out.account} -> {_in.account}, lots carried"
            + (f" ({_cov:g} cover the short there)" if _cov else ""))


def _us_move_cover_line(symbol: str, date: str, src: str, dst: str, q: float, short: float,
                        covered: float, more: float, moved: float, rows: List[_GainRow]) -> str:
    """The every-run ATTENTION line of an own-account move that closed
    a short (TJ-3424): what moved and what closed the short, the
    cover's term and its figure as booked at the move (a later
    wash-sale add can still change the row: TJ-3463), and the two
    limits (the term reads the delivered lot only, TJ-3449; §1259)."""
    # The figure: the rows of known cost only (a cover at an unknown
    # cost has no gain to say: TJ-3465), the gains and the losses each
    # said, never netted (TJ-3469).
    known = [e for e in rows if not e.get('tainted')]
    terms = sorted({'long-term' if e.get('term') == 'LONG_TERM'
                    else 'short-term' for e in known or rows})
    gain = sum((e['raw_gain'] for e in known if e['raw_gain'] >= 0), _ZERO)
    loss = -sum((e['raw_gain'] for e in known if e['raw_gain'] < 0), _ZERO)
    dis = sum((e['disallowed_amount'] for e in known), _ZERO)
    parts = ([f"a gain of {gain:,.2f}"]
             if gain > _US_MONEY_EPSILON or loss <= _US_MONEY_EPSILON else [])
    if loss > _US_MONEY_EPSILON:
        parts.append(f"a loss of {loss:,.2f}"
                     + (f", {dis:,.2f} of it disallowed as a wash sale"
                        if dis > _US_MONEY_EPSILON else ""))
    figure = (" and ".join(parts) if known
              else "its cost unknown, so no gain or loss is computed")
    return (
        f"warning: ATTENTION: own-account move: {symbol}: "
        f"{q:g} moved from {src} to {dst} on {date}, where {dst} was "
        f"short {short:g}: {covered + more:g} of them close the short "
        f"that day at their own basis"
        + (f" ({more:g} of them at an unknown cost, for manual reporting)"
           if more else "")
        + f" (a cover on Form 8949, {' and '.join(terms)}: {figure}, "
        f"as booked at the move, before any later wash-sale add), and "
        + (f"the other {moved:g} move as lots. " if moved > 1e-9
           else "none move as lots. ")
        + "The term reads the lot delivered only (§1233): your other "
        "holdings of the security were not read for it. A constructive "
        "sale while the account held both (§1259) is not modelled.")


def _us_walk_split(*, _main_split_groups: Set[_RenameKey], run: _USRun, symbol: str, tx: TaxTransaction) -> None:
    """A SPLIT row: the lots re-denominated by the ratio (a multi-ratio merger's row on its own
    account's lots alone, US-CORP-14), and a rename's lots carried to the new ticker."""
    ratio = tx.quantity
    # A multi-ratio merger's row reaches its own account's
    # lots only (US-CORP-14; _split_covers): each account
    # lands on exactly the shares its broker delivered.
    _covers = _us_split_covers(tx, _main_split_groups, _acct_ratios=run._acct_ratios)

    def _split_keys(inv: _USInventory, _sym: str = symbol, _c: Optional[Callable[[str], bool]] = _covers) -> List[_IKey]:
        return [k for k in _us_inv_keys_for(run, inv, _sym)
                if _c is None or not run.per_account_basis
                or _c(k[0])]
    # A zero/falsy ratio skips only the SCALING — the
    # rename migration below must still run. The old
    # `continue` dropped the whole event while the pre-pass
    # and SplitTimeline both honored it, forking the book
    # into a stranded long under the old symbol plus a
    # phantom short under the new one.
    inv: _USInventory       # the long lots, then the short ones
    if ratio:
        # Scale every existing lot's qty by ratio and its
        # cost-per-share by 1/ratio (so total cost stays put).
        # Blended mode: a split is a property of the
        # SECURITY — scale every account's key.
        for inv in (run.inventory_long, run.inventory_short):
            for _k in _split_keys(inv):
                for lot in inv[_k]:
                    lot['qty'] = lot['qty'] * ratio
                # Cost basis is held in Decimal; multiplying
                # qty by ratio implicitly scales cost-per-
                # share by 1/ratio because the cost field
                # stays unchanged.
    # Replacement records are NOT rescaled here: they stay
    # denominated in their own trade-date units, and the match
    # sites convert with _rep_units_factor. The old blanket
    # rescale multiplied every rep for the symbol — including
    # buys dated AFTER the split that were already in post-
    # split units — doubling their match quantity (2x wash
    # disallowance and inflated deferred basis).
    # If symbol_new is set, move all lots to the new symbol.
    target_symbol = (tx.symbol_new or '').strip()
    if target_symbol and target_symbol != symbol:
        for inv in (run.inventory_long, run.inventory_short):
            for _k in _split_keys(inv):
                _tk = ((_k[0], target_symbol)
                       if run.per_account_basis else target_symbol)
                inv.setdefault(_tk, []).extend(inv[_k])
                del inv[_k]
                # FIFO is DATE order, not append order
                # (FUZZ #G): merging migrated lots after
                # existing target lots made the newest lot
                # sell first, flipping LONG_TERM to
                # SHORT_TERM and misstating per-lot gains.
                # Sort by ACTUAL acquisition date only —
                # never effective_acq_date: §1223(3)
                # tacking rewrites effective_acq_date to a
                # fictitious earlier date (holding-period
                # only; Reg. 1.1012-1(c) FIFO goes by the
                # order shares were actually acquired), and
                # it only does so when wash detection is
                # on. Leading the key with the tacked date
                # made a rename-split's merge order — and
                # hence WHICH shares a later sale consumed
                # — depend on wash processing, silently
                # shifting plain basis between realized
                # gains and held inventory (conservation
                # broke by exactly that basis difference).
                # Same-date lots go by their acquisition
                # TIME (audit S070-14: the target's own lots
                # always came first), then append order (the
                # export's row order for same-stamp rows).
                inv[_tk].sort(key=lambda l: (
                    l['date'], run._lot_time.get(l.get('id'), '')))
        # Wash-sale replacement records need no move: they
        # are keyed by the dated identity class (_rep_key),
        # which already joins OLD before the rename to NEW
        # and is never the renamed-away ticker itself
        # (A2-1596: the per-symbol move never ran).
        # Carry the symbol→currency map too — without this,
        # the currency guard above flags the post-rename
        # ticker as "unseen" and the per-ticker stats lose
        # their currency association.
        if symbol in run.symbol_currency:
            run.symbol_currency.setdefault(target_symbol, run.symbol_currency[symbol])
            del run.symbol_currency[symbol]


def _us_walk_adjust(*, ikey: _IKey, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction) -> None:
    """An ADJUST row against the lots: a distribution to the holders of record over the lots
    settled by the record date (US-DIST-01), else a basis adjustment spread over the open
    lots in proportion, what exceeds a lot's basis a gain; the trace line."""
    lots = run.inventory_long.get(ikey, [])
    if tx.type == 'dist' and tx.record_date and lots:
        # A distribution to the holders of record (the
        # [[distributions]] row, tax-logic US-DIST-01): the
        # lots settled by the record date only — a purchase
        # that straddles it is not the holder's. One stamp
        # could not pick them (audit 2026-10-24 B14: backdated
        # before a straddling buy, the whole amount landed on
        # the lots open before it).
        _rd = tx.record_date[:10]
        _settled = [l for l in lots
                    if str(l.get('date_settle')
                           or l['date'])[:10] <= _rd]
        if not _settled:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: unapplied basis "
                  f"adjustment: {symbol} ADJUST of "
                  f"{M.of(tx.net_amount):+.2f} on {tx.date} "
                  f"(record date {_rd}) found no open long lot "
                  f"settled by the record date ({len(lots)} "
                  f"open, all settling after it) — the row was "
                  f"NOT applied; check the record date.")
            return
        lots = _settled
    open_qty = sum(l['qty'] for l in lots)
    if open_qty <= run.epsilon:
        # Not applied (tax-logic US-ROC-03 / US-ROC-04): a
        # return of capital with no basis left is a §301(c)(3)
        # gain to report by hand; a basis INCREASE (a notional
        # distribution) has no lot to raise. Console-visible
        # ATTENTION (audit A2-0199), worded by sign (A2-0964:
        # an increase was called a return of capital).
        _amt = M.of(tx.net_amount)
        _where = ('the position is short'
                  if run.inventory_short.get(ikey)
                  else 'the position was closed')
        if _amt < 0:
            _what = ("a return of capital with no basis to "
                     "reduce is a taxable gain (§301(c)(3)) "
                     "to report by hand")
        else:
            _what = ("a basis increase (e.g. a notional "
                     "distribution) has no lot to raise — "
                     "re-date it before the sale or adjust "
                     "that sale by hand")
        _us_note(run, tx.date,
              f"warning: ATTENTION: unapplied basis adjustment: "
              f"{symbol} ADJUST of {_amt:+.2f} on {tx.date} "
              f"found no open long lots ({_where}) — {_what}; "
              f"the row was NOT applied.")
        return
    delta = D(tx.net_amount)
    applied = Decimal(0)
    excess_total = Decimal(0)
    # §301(c)(3) excess by (acquired, holding-period start,
    # unknown cost): every lot driven below zero is floored
    # at zero, however small its part — a per-lot half-cent
    # threshold left hundreds of execution lots' sub-cent
    # excesses as negative basis and their sum out of the
    # gain (TJ-0956).
    _xs: Dict[Tuple[str, str, bool], Decimal] = {}
    # ...and by lot, for a row of two lots of one date: the
    # views' premium is the lot's own (backlog TJ-1218).
    _xs_lots: Dict[Tuple[str, str, bool], Dict[str, Decimal]] = {}
    _date_ids: Dict[str, Set[str]] = {}
    for i, lot in enumerate(lots):
        _date_ids.setdefault(str(lot['date']), set()).add(
            str(lot.get('id') or ''))
        if i == len(lots) - 1:
            # Last lot absorbs the division remainder so the
            # applied total equals net_amount exactly.
            share = delta - applied
        else:
            share = (delta * D(lot['qty'])) / D(open_qty)
        lot['cost_basis'] += share
        applied += share
        _lwd = lot.get('wash_deferred', Decimal(0))
        if (share > 0 and _lwd > 0
                and (tx.type or '') != 'roc'
                and not str(tx.id or '').startswith('WASH_')):
            # The §1091(d) add is part of the basis: what a
            # return of capital left of the basis (none past
            # it) is all the add a sale reports of it
            # (`_lot_wash_held`; US-RPT-12; backlog TJ-1017,
            # TJ-1219). New cost does not bring the rest
            # back, as a reversal (type=roc) does. The lot
            # stays one that took a loss's add (US-WASH-22).
            _cap = max(Decimal(0), lot['cost_basis'] - share)
            if _lwd > _cap:
                lot['wash_deferred'] = _cap
                lot['wash_basis_spent'] = True
        if lot['cost_basis'] < 0:
            # §301(c)(3): the part beyond this lot's basis is
            # gain now; the basis stays at zero.
            excess = -lot['cost_basis']
            lot['cost_basis'] = Decimal(0)
            excess_total += excess
            _k = (str(lot['date']),
                  str(lot.get('effective_acq_date',
                              lot['date'])),
                  bool(lot.get('tainted')))
            _xs[_k] = _xs.get(_k, Decimal(0)) + excess
            _xl = _xs_lots.setdefault(_k, {})
            _xl[str(lot.get('id') or '')] = (
                _xl.get(str(lot.get('id') or ''), _ZERO)
                + M.of(excess))
    # One deemed row per acquisition; the ones under half a
    # cent are pooled by term (dated their earliest lot).
    _rows = []
    _small: Dict[Tuple[bool, bool], List[Any]] = {}
    for (_lot_date, _acq, _tnt), _amt in _xs.items():
        _lt = held_more_than_one_year(_acq, tx.date)
        if _amt >= D('0.005'):
            _rows.append((_lot_date, _acq, _tnt, _amt, _lt))
        else:
            _pool = _small.setdefault((_lt, _tnt), [
                _lot_date, _acq, Decimal(0)])
            _pool[2] += _amt
            if _acq < _pool[1]:
                _pool[0], _pool[1] = _lot_date, _acq
    for (_lt, _tnt), (_lot_date, _acq, _amt) in _small.items():
        if _amt >= D('0.005'):
            _rows.append((_lot_date, _acq, _tnt, _amt, _lt))
    for _lot_date, _acq, _tnt, _amt, _lt in _rows:
        try:
            _held = (datetime.strptime(tx.date, '%Y-%m-%d')
                     - datetime.strptime(_acq, '%Y-%m-%d')
                     ).days
        except ValueError:
            _held = 0
        _x = M.of(_amt)
        _by_lot = _xs_lots.get((_lot_date, _acq, _tnt))
        if (len(_date_ids.get(str(_lot_date), ())) < 2
                or '' in _date_ids.get(str(_lot_date), ())
                or not _by_lot
                or abs(sum(_by_lot.values()) - _x) > _HALF_CENT):
            _by_lot = None
        run.realized_gains.append({
            **({'deemed_lots': _by_lot} if _by_lot else {}),
            'date': tx.date,
            'date_settle': tx.date_settle or tx.date,
            'symbol': symbol, 'qty': 0.0,
            'cost': _ZERO, 'proceeds': _x,
            'gain': _x, 'raw_gain': _x,
            'disallowed_amount': _ZERO,
            'permanently_disallowed': _ZERO,
            'replacement_lot_ids': [],
            'days_held': max(0, _held),
            'acquired_date': _lot_date,
            'account': tx.account,
            'currency': tx.currency,
            'commission': _ZERO, 'fee': _ZERO,
            'is_wash_sale': False,
            'is_option': is_option_symbol(symbol),
            'id': tx.id, 'trace': [],
            'direction': 'LONG',
            'term': 'LONG_TERM' if _lt else 'SHORT_TERM',
            'wash_trigger': None, 'wash_window': None,
            'wash_replacements': None,
            'tainted': _tnt,
            'deemed': True,
            'note': ('DEEMED GAIN — nondividend '
                     'distribution in excess of basis '
                     '(§301(c)(3)); basis reset to zero'),
        })
        run.rules.setdefault(tx.id, {})[tx.id] = R.US_ROC_02     # its rule (RuleTable)
    if excess_total > D('0.005'):
        emit_line(f"NOTE: {symbol}: nondividend distribution on "
            f"{tx.date} exceeded the basis by "
            f"{M.of(excess_total):.2f} — booked as capital "
            f"gain in that year (§301(c)(3)); the basis is "
            f"zero.")
    if run.trace:
        symbol_traces[symbol].append(
            f"# {tx.date} ADJUST   {M.shown(tx.net_amount, 4):10.4f} | "
            f"spread pro-rata over {len(lots)} lot(s), "
            f"{open_qty:.4f} sh")


def _us_walk_stock_dividend(*, ikey: _IKey, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction) -> bool:
    """A stock dividend's shares: the lots entitled to it (bought before the ex-date, else the
    record date; the shares sold since counted) share their basis with the new shares
    (Pub. 550), whose lot carries its share; returns True when the row is spent."""
    _lots = run.inventory_long.get(ikey, [])
    _held = sum(l['qty'] for l in _lots)
    # The shares ENTITLED to the dividend share their basis
    # with the new ones (Pub. 550, "Stock bought at various
    # times"): the lots bought before the ex-dividend date
    # (with only a record date, before it). A lot bought
    # after the entitlement date and before the pay date
    # keeps its own basis (2026-10 filing audit F3: every
    # lot held on the pay date was scaled, moving basis from
    # the old lot to the new one and the gain the other
    # way). With neither date the lots held on the pay date
    # are taken as entitled, and a lot bought within the
    # 90 days before it is named as the open question.
    _ent_date = (tx.ex_date or tx.record_date or '').strip()
    if _ent_date:
        _ent = [l for l in _lots
                if (l.get('date') or '') < _ent_date]
    else:
        _ent = list(_lots)
    _ent_qty = sum(l['qty'] for l in _ent)
    # Entitled shares sold on or after the entitlement date,
    # before the pay date: the sale keeps the right to the
    # new shares, so they share the basis too (TJ-0498: the
    # held lots took the whole dividend and the sale kept
    # its whole basis). A sale under the security's name
    # before a rename is the same security (TJ-0648: only
    # the literal symbol was searched), and its quantity is
    # counted in the pay date's units, through any split in
    # between (TJ-0647: 50 pre-split shares counted as 50,
    # not 100).
    _cls_now = run.split_timeline.class_at(symbol, tx.date)
    _sold_ent = ([e for e in run.realized_gains
                  if e.get('direction') == 'LONG'
                  and e.get('qty')
                  and not (e.get('deemed')
                           or e.get('reorg_356'))
                  and (e.get('symbol') == symbol
                       or run.split_timeline.class_at(
                           e.get('symbol') or '',
                           e.get('date') or None)
                       == _cls_now)
                  and _us_ikey_on(run, e['account'], symbol) == ikey
                  and _ent_date <= (e.get('date') or '')
                  <= tx.date
                  and (e.get('acquired_date') or '')
                  < _ent_date]
                 if _ent_date else [])
    _sold_units = {
        id(e): e['qty'] * _us_rep_units_factor(run,
            e.get('symbol') or symbol, e.get('date') or '',
            tx.date)
        for e in _sold_ent}
    _sold_qty = sum(_sold_units.values())
    if (_ent_qty + _sold_qty > run.epsilon
            and not run.inventory_short.get(ikey)):
        _ratio = ((_ent_qty + _sold_qty + tx.quantity)
                  / (_ent_qty + _sold_qty))
        # Named before the spread: the lots' own, 0 to 90 days
        _recent = [l for l in _lots      # before it (TJ-2705)
                   if (l.get('date') or '') <= tx.date
                   and _day_gap(l.get('date') or '',
                                tx.date) in range(91)]
        _recent_qty = sum(l['qty'] for l in _recent)
        for _lot in _ent:
            _lot['qty'] = _lot['qty'] * _ratio
        _us_grow_rep_capacity(run, symbol, tx.date, _ent, _ratio)
        _later = [l for l in _lots if l not in _ent]
        _moved, _manual = _us_allot_sold_shares(run,
            _sold_ent, _ratio, ikey, tx, _sold_units)
        _us_note(run, tx.date,
              f"note: {symbol}: stock dividend of "
              f"{tx.quantity:g} share(s) on {tx.date} — "
              f"nontaxable (§305(a)): the basis of the "
              f"{_ent_qty + _sold_qty:g} share(s) "
              + (f"bought before {_ent_date} "
                 f"({len(_ent)} lot(s) held)"
                 if _ent_date else "held")
              + f" is spread over old and new (§307) and "
              f"their purchase dates carry over"
              + (f"; {_sold_qty:g} of them were sold on or "
                 f"after {_ent_date}, before the pay date ("
                 + ", ".join(sorted({e['date']
                                     for e in _sold_ent}))
                 + f"): those sales' cost falls by "
                 f"{_moved:,.2f}, which the "
                 f"{_sold_qty * (_ratio - 1):g} new share(s) "
                 f"paid on them carry"
                 if _sold_ent else "")
              + (f"; the {sum(l['qty'] for l in _later):g} "
                 f"bought on or after {_ent_date} keep their "
                 f"own basis" if _later else "")
              + f"; if it was taxable (§305(b), e.g. a cash "
              f"option), enter it by hand.")
        for _e, _amt in _manual:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: {symbol}: the stock "
                  f"dividend on {tx.date}: {_e['qty']:g} of "
                  f"the shares entitled to it were sold on "
                  f"{_e['date']} ({_e.get('account')}), "
                  + ("a wash sale" if _e['date'][:4]
                     == tx.date[:4] else
                     f"in {_e['date'][:4]}")
                  + f", so their "
                  f"{_sold_units[id(_e)] * (_ratio - 1):g} "
                  f"new share(s) are booked at $0: move "
                  f"{_amt:,.2f} of that sale's cost to them "
                  f"by hand (§307; .tt ADJUST rows).")
        if not _ent_date and len(_lots) > 1:
            if _recent:
                _us_note(run, tx.date,
                      f"warning: ATTENTION: {symbol}: the "
                      f"stock dividend of {tx.quantity:g} "
                      f"share(s) on {tx.date} carries no record or "
                      f"ex-dividend date, so its basis was "
                      f"spread over every lot held on the "
                      f"pay date — including "
                      f"{_recent_qty:g} share(s) bought on "
                      f"{', '.join(sorted({l['date'] for l in _recent}))}"
                      f", within 90 days before it. A lot "
                      f"bought after the record date is not "
                      f"entitled to the dividend and keeps "
                      f"its own basis (§307): check the "
                      f"issuer's record date; if one of "
                      f"those lots came after it, give the "
                      f"row its ex_date / record_date in a "
                      f"JSON book, or correct the lots' "
                      f"basis by hand.")
        if run.trace:
            symbol_traces[symbol].append(
                f"# {tx.date} STOCK DIVIDEND +{tx.quantity:g} "
                f"sh | spread over {len(_ent)} lot(s), "
                f"{_ent_qty:.4f} sh entitled"
                + (f" (before {_ent_date})" if _ent_date
                   else ""))
        return True
    if _held > run.epsilon and not run.inventory_short.get(ikey):
        # Shares held on the pay date, none bought before
        # the entitlement date: the dividend belongs to
        # shares the books do not show (history missing, or
        # sold between the record and pay dates).
        _us_note(run, tx.date,
              f"warning: {symbol}: stock dividend of "
              f"{tx.quantity:g} share(s) on {tx.date}: none "
              f"of the {_held:g} share(s) held was bought "
              f"before its entitlement date {_ent_date} — "
              f"booked as a $0 purchase (not a wash-sale "
              f"replacement); add the purchase history of "
              f"the shares entitled to it (§307), or check "
              f"the row's ex_date / record_date.")
        # Falls through to the $0 purchase below.
    else:
        # Held before and sold out by the pay date (sold between
        # the record and pay dates): the history is complete, the
        # §307 share belongs to the sold lots (audit A2-0562). Never
        # a wash-sale replacement (US-STKDIV-01, A2-0205).
        _sold_out = any(
            _p is not tx and _p.symbol == tx.symbol
            and _p.account == tx.account
            and _p.action in ('BUYSELL', 'ASSIGN')
            and _p.quantity < 0
            and (_p.date, _p.time or '') <= (tx.date,
                                             tx.time or '')
            for _p in run.taxable_sorted)
        _us_note(run, tx.date,
              f"warning: {symbol}: stock dividend of "
              f"{tx.quantity:g} share(s) on {tx.date} with no "
              f"shares held — booked as a $0 purchase (not a "
              f"wash-sale replacement); "
              + ("the shares were disposed of before the pay "
                 "date, so the §307 basis allocation reaches the "
                 "SOLD lots: adjust their basis and this lot's by "
                 "hand (.tt ADJUST rows)."
                 if _sold_out else
                 "add the missing purchase history so it can "
                 "share their basis (§307)."))
    return False


def _us_walk_spinoff(*, _spin_done: Set[RowId], ikey: _IKey, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction) -> bool:
    """An s.355 spin-off's shares: the parent's entitled lots (the late buys apart) allocate a
    share of their basis to the new shares' lot by the fraction the event's ADJUST row
    set; returns True when the row is spent."""
    _adj = run._spin_adj.get((tx.account, tx.corp_event_id))
    _par = (_adj.symbol if _adj is not None else '')
    _pkey = _us_ikey_on(run, tx.account, _par)
    _plots = [l for l in run.inventory_long.get(_pkey, [])
              if l['qty'] > run.epsilon]
    # Only the lots bought before the entitlement date
    # (ex-date, else the record date) are entitled: a later
    # purchase keeps its own basis and date (TJ-0951).
    _ent_d = (tx.ex_date or tx.record_date or '').strip()
    _late = ([l for l in _plots
              if (l.get('date') or '') >= _ent_d]
             if _ent_d else [])
    if _late and len(_late) < len(_plots) and _adj is not None:
        _lq = sum(l['qty'] for l in _late)
        _hq = sum(l['qty'] for l in _plots)
        _n = float(_adj.corp_parent_qty or 0.0)
        _how = spin_entitlement((tx.ex_date or '').strip(), _n,
                                _hq - _lq, _hq)
        if _how == 'dated':
            _plots = [l for l in _plots if l not in _late]
            _us_note(run, tx.date,
                  f"note: {_par}: the §355 spin-off on "
                  f"{tx.date} (entitlement {_ent_d}): the "
                  f"{_lq:g} share(s) "
                  f"bought on or after {_ent_d} are not entitled "
                  f"and keep their own basis.")
        elif _how == 'due_bill':
            # Older shares sold on or after the date: they may
            # be the ones paid on, and the books hold the same
            # count either way (TJ-1267, TJ-1337). Said, with
            # the figures of the due-bill reading.
            _sold = sum(
                e['qty'] for e in run.realized_gains
                if e.get('direction') == 'LONG' and e.get('qty')
                and e.get('symbol') == _par
                and _us_ikey_on(run, e['account'], _par) == _pkey
                and _ent_d <= (e.get('date') or '') <= tx.date
                and (e.get('acquired_date') or '') < _ent_d)
            if _sold > run.epsilon:
                _us_note(run, tx.date,
                      f"warning: ATTENTION: {_par}: the §355 "
                      f"spin-off on {tx.date} was paid on "
                      f"{_n:g} shares, as many as were held "
                      f"that day, but {_sold:g} held before its "
                      f"record date {_ent_d} were sold before "
                      f"it. Booked as a due bill: the {_lq:g} "
                      f"bought on or after the date share the "
                      f"basis. If the sold shares were the ones "
                      f"paid on, the later lot keeps its own and "
                      f"the basis belongs to those sales: check "
                      f"the company's Form 8937 and the broker's "
                      f"confirmation, and correct them by hand "
                      f"(.tt ADJUST rows).")
            else:
                _us_note(run, tx.date,
                      f"note: {_par}: the §355 spin-off on "
                      f"{tx.date} was paid on {_n:g} shares, all "
                      f"those held that day: the {_lq:g} bought "
                      f"on or after its record date {_ent_d} came "
                      f"with a due bill and share the basis.")
        else:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: {_par}: the §355 "
                  f"spin-off on {tx.date} was paid on "
                  + (f"{_n:g} shares" if _n > 0
                     else "an unstated number of shares")
                  + f", but {_hq - _lq:g} were held before its "
                  f"record date {_ent_d} and {_hq:g} that day: "
                  f"every lot held that day shares the basis. "
                  f"A lot not entitled to it keeps its own "
                  f"(§1.358-2): check the lots against the "
                  f"company's Form 8937 and correct them by "
                  f"hand (.tt ADJUST rows).")
    elif _late and _adj is not None:
        _us_note(run, tx.date,
              f"warning: ATTENTION: {_par}: the §355 spin-off "
              f"on {tx.date}: every share held that day was "
              f"bought on or after its record date {_ent_d}, so "
              f"the basis and the new shares' dates come from "
              f"those lots. If the shares entitled to it were "
              f"sold before the pay date, they belong to those "
              f"sales: correct them by hand (.tt ADJUST rows).")
    _pbasis =sum((l['cost_basis'] for l in _plots),
                  Decimal(0))
    _alloc = (-D(_adj.net_amount) if _adj is not None
              else Decimal(0))
    if (_adj is not None and _plots
            and not run.inventory_short.get(_pkey)):
        # Reg. §1.358-2: every parent share gives up the same
        # fraction of its OWN basis; each parent block gets a
        # block of spun-off shares with its basis share and
        # its acquisition date (§1223(1)). Never below zero:
        # a §355 distribution recognizes no gain. With no
        # basis to move (a $0 allocation, or a parent whose
        # basis is zero) the blocks still follow the parent's
        # blocks share for share, at $0, with their dates
        # (TJ-0723: booked as a new lot on the spin date).
        _frac = (_alloc / _pbasis
                 if _pbasis > 0 and _alloc > 0
                 else Decimal(0))
        _new_total = D(abs(tx.net_amount))
        if _pbasis <= 0 and _alloc > 0:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: {_par}: the §355 "
                  f"spin-off on {tx.date} allocates "
                  f"{_alloc:,.2f} but the parent's "
                  f"basis held is {_pbasis:,.2f} — "
                  f"capped at the basis (a tax-free spin-off "
                  f"books no gain); check the allocated "
                  f"amount (Form 8937 %).")
            _new_total = Decimal(0)
        if _frac > 1:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: {_par}: the §355 "
                  f"spin-off on {tx.date} allocates "
                  f"{_alloc:,.2f} but the parent's "
                  f"basis held is {_pbasis:,.2f} — "
                  f"capped at the basis (a tax-free spin-off "
                  f"books no gain); check the allocated "
                  f"amount (Form 8937 %).")
            _new_total = _new_total / _frac
            _frac = Decimal(1)
        _pq = sum(l['qty'] for l in _plots)
        _new = []
        _taken = Decimal(0)
        _cost_left = _new_total
        for _i, _l in enumerate(_plots):
            _take = _l['cost_basis'] * _frac
            _wd = _l.get('wash_deferred', Decimal(0))
            _l['cost_basis'] -= _take
            if _wd:
                _l['wash_deferred'] = _wd * (1 - _frac)
            _taken += _take
            _c = (_cost_left if _i == len(_plots) - 1
                  else _new_total * _take / (_pbasis * _frac)
                  if _pbasis * _frac > 0
                  else _new_total * D(_l['qty']) / D(_pq))
            _cost_left -= _c
            _new.append({
                'qty': tx.quantity * _l['qty'] / _pq,
                'cost_basis': _c,
                'wash_deferred': _wd * _frac if _wd
                else Decimal(0),
                'date': _l['date'],
                'effective_acq_date': _l.get(
                    'effective_acq_date', _l['date']),
                'id': tx.id,
                **({'tainted': True} if _l.get('tainted')
                   else {}),
                **({'unknown_from': dict(_l['unknown_from'])}
                   if _l.get('unknown_from') else {}),
            })
        _us_insert_lots(run, ikey, _new)
        _spin_done.add(_adj.id)
        if run.trace:
            symbol_traces[symbol].append(
                f"# {tx.date} SPIN-OFF  {tx.quantity:10.4f} "
                f"from {_par} | {len(_new)} block(s), "
                f"{float(_frac):.6f} of each parent lot's basis")
        return True
    if _adj is not None:
        _us_note(run, tx.date,
              f"warning: {symbol}: the §355 spin-off on "
              f"{tx.date} finds no long {_par} lots with "
              f"basis in {tx.account} — the spun-off shares "
              f"are booked as one lot on the spin date and "
              f"the parent's basis reduction as a plain "
              f"adjustment (check the parent's history).")
    return False


def _us_walk_reorg(*, _boot_blocks: Dict[Tuple[str, str], Tuple[List[_ReorgBlock], Decimal]], ikey: _IKey, run: _USRun, symbol: str, symbol_traces: Dict[str, List[str]], tx: TaxTransaction) -> bool:
    """An s.356 reorganization's two legs: the old shares' lots each realize their share of
    the new shares' value plus boot less their basis and recognize up to their boot share
    (Reg. 1.356-1(b)), kept per block; the new shares' row takes the blocks' carried basis;
    returns True when the row is spent."""
    if tx.action == 'BUYSELL' and tx.quantity < 0:
        # §356 per block (Reg. §1.356-1(b), Rev. Rul. 68-23): each
        # lot realizes its share of (new shares' value + boot)
        # less its basis and recognizes min(realized, its boot
        # share) — never a loss (§356(c)); new basis = basis −
        # boot share + recognized (§358(a)), dates carried
        # (§1223(1)). One row per block: proceeds = its boot,
        # cost = boot − recognized, gain = recognized.
        _q = -tx.quantity
        _ar = D(tx.net_amount)
        _boot = D(tx.gross_amount or 0)
        _drawn = _us_draw_long_lots(run, ikey, symbol, _q, tx.date, tx)
        _got = sum(c['qty'] for c in _drawn)
        if _q - _got > run.epsilon:
            _us_note(run, tx.date,
                  f"warning: ATTENTION: {symbol}: the §356 "
                  f"exchange on {tx.date} gives up {_q:g} shares "
                  f"but {tx.account} holds {_got:g} — the other "
                  f"{_q - _got:g} have no basis in the books "
                  f"(missing history): booked at zero basis for "
                  f"manual reporting.")
            _drawn.append({'qty': _q - _got,
                           'cost_basis': Decimal(0),
                           'date': tx.date,
                           'effective_acq_date': tx.date,
                           'tainted': True})
        _blocks: List[_ReorgBlock] = []
        for _c in _drawn:
            _f = D(_c['qty']) / D(_q)
            _arb, _bb = _ar * _f, _boot * _f
            _rec = max(Decimal(0), min(_arb - _c['cost_basis'],
                                       _bb))
            _acq = cast("str", _c.get('effective_acq_date', _c['date']))
            _blocks.append((_f, _c['cost_basis'] - _bb + _rec,
                            _c['date'], _acq,
                            bool(_c.get('tainted')),
                            _c.get('wash_deferred', Decimal(0)),
                            _c.get('unknown_from'),
                            _c.get('_rep_chunk')))
            if _bb <= 0:
                continue
            try:
                _held = (datetime.strptime(tx.date, '%Y-%m-%d')
                         - datetime.strptime(_acq, '%Y-%m-%d')
                         ).days
            except ValueError:
                _held = 0
            run.realized_gains.append({
                'date': tx.date,
                'date_settle': tx.date_settle or tx.date,
                'symbol': symbol, 'qty': _c['qty'],
                'cost': M.of(_bb - _rec),
                'proceeds': M.of(_bb),
                'gain': M.of(_rec), 'raw_gain': M.of(_rec),
                'disallowed_amount': _ZERO,
                'permanently_disallowed': _ZERO,
                'replacement_lot_ids': [],
                'days_held': max(0, _held),
                'acquired_date': _c['date'],
                'account': tx.account,
                'currency': tx.currency,
                'commission': _ZERO, 'fee': _ZERO,
                'is_wash_sale': False,
                'is_option': is_option_symbol(symbol),
                'id': tx.id, 'trace': [],
                'direction': 'LONG',
                'term': ('LONG_TERM'
                         if held_more_than_one_year(_acq, tx.date)
                         else 'SHORT_TERM'),
                'wash_trigger': None, 'wash_window': None,
                'wash_replacements': None,
                'tainted': bool(_c.get('tainted')),
                **({'unknown_cost_from': [dict(_c['unknown_from'])]}
                   if _c.get('tainted') and _c.get('unknown_from') else {}),
                'reorg_356': True,
                'note': (f"§356 boot: realized "
                         f"{M.of(_arb - _c['cost_basis']):.2f}, "
                         f"recognized {M.of(_rec):.2f} "
                         f"(never a loss)"),
            })
            run.rules.setdefault(tx.id, {})[tx.id] = R.US_CORP_05    # its rule (RuleTable)
            if _c.get('_rep_chunk') and _c['qty'] > run.epsilon:
                # (per old share: a later match restates it, TJ-3427)
                _c['_rep_chunk']['boot'] = (
                    run.realized_gains[-1],
                    (_arb - _c['cost_basis']) / D(_c['qty']),
                    _bb / D(_c['qty']))
        _boot_blocks[(tx.account, tx.corp_event_id)] = (
            _blocks, _ar - _boot)
        if run.trace:
            symbol_traces[symbol].append(
                f"# {tx.date} §356 EXCHANGE {_q:10.4f} | "
                f"{len(_blocks)} block(s), boot {_boot:.4f}")
        return True
    if tx.action == 'BUYSELL' and tx.quantity > 0:
        _st = _boot_blocks.pop((tx.account, tx.corp_event_id),
                               None)
        if _st is not None:
            _blocks, _src_value = _st
            _val = D(abs(tx.net_amount))
            # The new shares' value in this leg's currency over
            # the same value in the old leg's: the currency
            # rate (and the whole-share fraction) between them.
            _r = (_val / _src_value
                  if _src_value > 0 and _val > 0 else Decimal(1))
            _new = [{
                'qty': tx.quantity * float(_f),
                'cost_basis': _nb * _r,
                'wash_deferred': _wd * _r if _wd else Decimal(0),
                'date': _d, 'effective_acq_date': _e,
                'id': tx.id,
                **({'tainted': True} if _t else {}),
                **({'unknown_from': dict(_uf)} if _t and _uf else {}),
            } for _f, _nb, _d, _e, _t, _wd, _uf, _ch in _blocks]
            _us_insert_lots(run, ikey, _new)
            # The old shares still replace another account's later
            # loss (US-WASH-22, TJ-2700): its §1091(d) add goes to
            # the new shares they became (§358).
            for _b, _l in zip(_blocks, _new):
                if _b[7] is not None:
                    _b[7]['lots'].append(_l)
            if run.trace:
                symbol_traces[symbol].append(
                    f"# {tx.date} §356 NEW SHARES "
                    f"{tx.quantity:10.4f} | {len(_blocks)} "
                    f"block(s), basis carried")
            return True
        _us_note(run, tx.date,
              f"warning: ATTENTION: {symbol}: the new shares of "
              f"the §356 exchange on {tx.date} have no old-share "
              f"leg in {tx.account} — booked at their value, not "
              f"the carried basis; check the merger's rows.")
    return False


def _us_walk_rows(*, run: _USRun) -> Tuple[Optional[_USLotsNow], Optional[_USLotsNow]]:
    """The row loop of the US engine (step 4c), over `run`, the engine's state, and
    the two lots it returns. Its per-action blocks are functions (step 4n): the
    lot move, the split, the ADJUST, the stock dividend, the s.355 spin-off and
    the s.356 reorg against the lots, then the buy (a cover and its long lot) and
    the sale (a close and its short lot). A part whose `continue` the driver
    keeps returns plainly; a part the walk falls through from returns True when
    the row is spent and the driver continues on it."""
    # The lots as they stand at the end of `year_end` (YYYY-12-31,
    # the project year's last day) on this engine's trade-date walk
    # (tax-logic US-RPT-13): `tjs edge-cases` reads the
    # disallowed losses still in the basis of the lots held at the
    # year end from them, not from the lots after every later row
    # (backlog TJ-0284). The walk is not cut there: a January
    # purchase still decides a December loss, and its lot, which
    # takes the basis add, was not held on Dec 31. Taken before the
    # row after the last one dated on or before year_end (an option
    # row an assignment moved in front of its December stock leg
    # stays in). None without a year_end.
    _ye_lots = None
    # The lots at the end of `lots_at` (backlog TJ-0554).
    _la_lots = None
    symbol_traces: Dict[str, List[str]] = {}
    _spin_done: Set[RowId] = set()
    _boot_blocks: Dict[Tuple[str, str], Tuple[List[_ReorgBlock], Decimal]] = {}
    _moves_done: Set[RowId] = set()
    _book_accounts = {t.account for t in run.taxable_sorted}
    _jdone: Set[str] = set()
    _tl_prev: List[TaxTransaction] = []
    _main_split_groups: Set[_RenameKey] = set()     # _split_covers, the main pass
    for tx in run.taxable_sorted:
        if run._tl is not None:
            _tl_d = str(run.get_sort_date(tx) or '')[:10]
            if _tl_prev:
                run._tl.touch(len(run._tl_dates) - 1,
                          [_us_tl_label(run, _tl_prev[0].account,
                                     _tl_prev[0].symbol)])
                if _tl_d != run._tl_dates[-1][0]:
                    run._tl.sweep(len(run._tl_dates) - 1, _us_tl_held(run))
            run._tl_dates.append(
                (_tl_d, _tl_d if run.cost_timeline != 'settle'
                 else str(tx.date_settle or _tl_d)[:10]))
            _tl_prev[:] = [tx]
        if tx is run._ye_next and _ye_lots is None:
            _ye_lots = _us_lots_copy(run)
        if tx is run._la_next and _la_lots is None:
            _la_lots = _us_lots_copy(run)
        if run._other_stkdivs:
            _us_apply_other_stkdivs(run, tx)
        symbol = tx.symbol
        ikey = _us_ikey_on(run, tx.account, symbol)
        run.inventory_long.setdefault(ikey, [])
        run.inventory_short.setdefault(ikey, [])
        if run.trace and symbol not in symbol_traces:
            symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} ---"]

        if _is_listing_journal(tx):
            _jl = run._jlegs.get(tx.journal_pair)
            if _jl and tx.journal_pair not in _jdone:
                _jdone.add(tx.journal_pair)
                _jtxt = _us_book_journal(run, *_jl, symbol_traces=symbol_traces)
                if run.trace:
                    for _js in (_jl[0].symbol, _jl[1].symbol):
                        symbol_traces.setdefault(_js, [
                            f"# --- FIFO CALCULATION TRACE: {_js} ---"
                        ]).append(f"# {tx.date} {_jtxt}")
            continue
        if tx.action == 'TRANSFER' and tx.type == LOT_MOVE_TYPE:
            # A custody move between two of your own taxable
            # accounts: not a sale — the lots keep their basis and
            # purchase dates and go to the receiving account
            # (US-BASIS-05). One leg books the pair.
            _us_walk_lot_move(_book_accounts=_book_accounts, _moves_done=_moves_done, ikey=ikey,
                              run=run, symbol=symbol, symbol_traces=symbol_traces, tx=tx)
            continue

        if run.non_capital(tx.action, tx.type):
            continue

        # ----- SPLIT (handles both classic splits and merger rollovers) -----
        # Mirror of the Canada engine's SPLIT branch (engine/ca/walk.py).
        # Without this, SPLIT falls through to the default BUY path
        # and the engine would add `ratio` (e.g. 0.0625 for a 1-for-16
        # merger) as new shares at $0 cost — completely wrong.
        if tx.action == 'SPLIT':
            _us_walk_split(_main_split_groups=_main_split_groups, run=run, symbol=symbol, tx=tx)
            continue

        # ----- OPENING_BALANCE (missing-history pre-data-window shares) -------
        # Synthesized by --incomplete-history. The Canada engine
        # tracks a `tainted` flag per pool so dispositions from a
        # missing-history pool can be split out into `manual_reporting_required`.
        # The US engine doesn't have a pool concept (FIFO lot list
        # instead), so we tag each missing-history lot directly and the
        # disposition path below propagates the flag to its gain
        # entry — same end-state as Canada's tainted-pool model.
        if tx.action == 'OPENING_BALANCE':
            qty = tx.quantity
            if abs(qty) < run.epsilon:
                continue
            if qty > 0:
                run.inventory_long.setdefault(ikey, []).append({
                    'qty': abs(qty),
                    'cost_basis': Decimal(0),
                    'date': tx.date,
                    'effective_acq_date': tx.date,
                    'id': tx.id,
                    'tainted': True,
                    # (the line's own fields, for the record: TJ-3209)
                    'unknown_from': _opening_source(tx, symbol),
                })
            else:
                # Short-side lots use a different shape than long
                # lots: the disposition path reads `proceeds`, not
                # `cost_basis` (engine/us/trades.py). A naive long-lot
                # shape here would KeyError on the first BUY-CLOSE.
                # `effective_open_date` mirrors the live short-open
                # path's field name.
                run.inventory_short.setdefault(ikey, []).append({
                    'qty': abs(qty),
                    'proceeds': Decimal(0),
                    'date': tx.date,
                    'effective_open_date': tx.date,
                    'id': tx.id,
                    'tainted': True,
                    'unknown_from': _opening_source(tx, symbol),
                })
            continue

        # ----- ADJUST (return of capital / manual basis adjustment) ---
        # Mirror of the Canada engine's ADJUST branch: a signed basis
        # delta in net_amount, no share movement. US treatment (IRC
        # §301(c)(2), Pub 550): a nondividend distribution reduces
        # stock basis — apportioned per share across the open long
        # lots. The excess of a lot's share over its basis is capital
        # gain in the distribution year (§301(c)(3)): booked as a
        # deemed row per lot (term from that lot's holding period)
        # and the lot's basis stays at zero (tax-logic US-ROC-02;
        # it used to be a warning only, and the excess came back as
        # extra gain at the sale — right total, wrong year).
        # Before this branch, ADJUST rows fell through to the
        # qty-epsilon skip below and were silently dropped, leaving
        # lot basis unreduced and understating gains at sale.
        if tx.action == 'ADJUST' and (
                tx.id in _spin_done
                or (tx.type == SPINOFF_355_TYPE
                    and abs(M.of(tx.net_amount or 0.0)) < _HALF_CENT)):
            # Booked with its spin-off (US-CORP-07); a $0 one only
            # names the parent (TJ-0723) and moves nothing.
            continue
        if tx.action == 'ADJUST':
            _us_walk_adjust(ikey=ikey, run=run, symbol=symbol, symbol_traces=symbol_traces,
                            tx=tx)
            continue

        if abs(tx.quantity) < run.epsilon:
            if tx.quantity and tx.action in ('BUYSELL', 'ASSIGN'):
                run._us_dust.append(tx)
            continue

        # ----- Stock dividend (§305(a), §307, §1223(5)) -------------
        # The new shares join the lots held: each lot's quantity
        # grows pro rata, its basis and purchase date stay — the
        # basis is spread over old and new shares and the holding
        # period tacks. No purchase, no new lot (partition
        # INPUTS-01; tax-logic US-STKDIV-01). With no long lots the
        # row is a data gap: booked as the $0 purchase it looks
        # like, with a warning.
        if is_stock_dividend(tx) and tx.quantity > 0:
            if _us_walk_stock_dividend(ikey=ikey, run=run, symbol=symbol,
                                       symbol_traces=symbol_traces, tx=tx):
                continue

        if (tx.action == 'BUYSELL' and tx.quantity > 0
                and tx.type == SPINOFF_355_TYPE):
            if _us_walk_spinoff(_spin_done=_spin_done, ikey=ikey, run=run, symbol=symbol,
                                symbol_traces=symbol_traces, tx=tx):
                continue

        if tx.type == REORG_356_TYPE:
            if _us_walk_reorg(_boot_blocks=_boot_blocks, ikey=ikey, run=run, symbol=symbol,
                              symbol_traces=symbol_traces, tx=tx):
                continue

        tx_qty_abs = abs(tx.quantity)
        # A BUY's cost is a magnitude (parsers spell it either sign);
        # a SELL's proceeds stay SIGNED — negative only when the
        # commission exceeds the gross (a $0.01 close), which abs()
        # turned into a credit (audit R1-164; mirrors the Canada
        # engine's _ca_trade_money).
        tx_net = (abs(M.of(tx.net_amount))
                  if tx.quantity > 0 and not receives_on_buy(tx)
                  else M.of(tx.net_amount))
        if (tx.type or '') == 'futures_settlement':
            # A futures fill on the settlement basis (lib/futures.py):
            # net_amount is the realized P/L, SIGNED (+ received,
            # - paid), 0 on an opening. A sell's proceeds are it; a
            # buy (a short's cover) costs its negation, so a short
            # closed at a profit realizes exactly the P/L — abs()
            # booked a +5,000 cover as a 5,000 cost (partition
            # ENGINE-02; the Canada engine's _ca_trade_money twin).
            tx_net = (M.of(tx.net_amount) if tx.quantity < 0
                      else -M.of(tx.net_amount))
        # Commission and fee are split separately on each gain entry
        # (see _us_gain_entry) — apportioned by chunk_qty share.

        # Option-leg ASSIGN: the premium rolls into the underlying's
        # basis/proceeds instead of being recognized here. We still pop
        # the position from inventory, but emit no gain entry — the
        # would-be gain is staged in pending_option_adjustments[underlying]
        # and consumed by the stock-leg BUYSELL processed next.
        is_option_assign = (tx.action == 'ASSIGN')
        # A marked warrant/right exercise leg rolls its cost into the
        # shares like a long call's premium (US-OPT-06).
        _assign_und_named = (run.option_underlying(symbol)
                             or exercise_target(tx))
        underlying_for_assign = (run._assign_underlying(tx)
                                 if is_option_assign
                                 and _assign_und_named
                                 else None)
        if not is_option_assign or not _assign_und_named:
            is_option_assign = False
            underlying_for_assign = None
        elif (underlying_for_assign is None
              or (tx.account, underlying_for_assign)
              not in run.taxable_stock_symbols):
            # Cash-settled assignment/exercise (index options): the
            # underlying never trades as stock in this book, so no
            # stock leg can consume a staged premium — realize the
            # option's own P&L via normal disposition accounting
            # (mirrors the Canada engine, note and all).
            emit_line(f"note: {symbol}: assignment treated as "
                  f"cash-settled ({_assign_und_named} never "
                  f"trades as stock in this book) — option P&L "
                  f"realized directly. If a stock leg is missing "
                  f"from your input, add it and re-run.")
            is_option_assign = False
            underlying_for_assign = None

        # ============================================================
        # BUY path: first close any existing shorts FIFO; if leftover
        # quantity remains, open a new long lot with the remainder.
        # ============================================================
        if tx.quantity > 0:
            _us_walk_buy(ikey=ikey, is_option_assign=is_option_assign, run=run, symbol=symbol,
                         symbol_traces=symbol_traces, tx=tx, tx_net=tx_net,
                         tx_qty_abs=tx_qty_abs, underlying_for_assign=underlying_for_assign)
            continue

        _us_walk_sell(ikey=ikey, is_option_assign=is_option_assign, run=run, symbol=symbol,
                      symbol_traces=symbol_traces, tx=tx, tx_net=tx_net, tx_qty_abs=tx_qty_abs,
                      underlying_for_assign=underlying_for_assign)
    # The fallbacks and the last timeline step, once the rows are walked.
    if run.year_end and _ye_lots is None:
        _ye_lots = _us_lots_copy(run)     # no row after the year end
    if run.lots_at and _la_lots is None:
        _la_lots = _us_lots_copy(run)     # no row after the day
    if run._tl is not None and _tl_prev:
        run._tl.touch(len(run._tl_dates) - 1,
                  [_us_tl_label(run, _tl_prev[0].account, _tl_prev[0].symbol)])
        run._tl.sweep(len(run._tl_dates) - 1, _us_tl_held(run))
    return (_la_lots, _ye_lots)


def _us_carry_lots(run: _USRun, inv: _USInventory, src: _IKey, dst: _IKey, q: float,
                   f: Optional[Decimal], field: str, sym: str, on_date: str) -> float:
    """Move `q` units of `inv[src]`'s FIFO lots to `inv[dst]` (a
    listing journal): the lots keep their dates and wash-sale
    deferrals; their `field` (a long's cost_basis, a short's
    proceeds) and deferral restated by `f` (None: no rate — held
    at an unknown cost, tainted). A lot split by the journal
    gives its moved part its share of the replacement records'
    capacity (`sym` on `on_date` is the source's class,
    TJ-0490). Returns the units moved."""
    lots = inv.get(src, [])
    moved = []
    left = q
    while left > run.epsilon and lots:
        lot = lots[0]
        if lot['qty'] <= left + run.epsilon:
            moved.append(lots.pop(0))
            left -= lot['qty']
            continue
        frac = D(left) / D(lot['qty'])
        part = dict(lot)
        part['qty'] = left
        part[field] = lot[field] * frac
        _wd = lot.get('wash_deferred', Decimal(0))
        part['wash_deferred'] = _wd * frac
        lot['qty'] -= left
        lot[field] -= part[field]
        lot['wash_deferred'] = _wd - part['wash_deferred']
        _us_split_rep_refs(run, sym, on_date, lot, part,
                        'lot_ref' if field == 'cost_basis'
                        else 'short_lot_ref')
        moved.append(part)
        left = 0.0
    for lot in moved:
        if f is None:
            lot[field] = Decimal(0)
            lot['wash_deferred'] = Decimal(0)
            lot['tainted'] = True
        elif f != 1:
            lot[field] = lot[field] * f
            lot['wash_deferred'] = (lot.get('wash_deferred',
                                            Decimal(0)) * f)
    if moved:
        lots_ = inv.setdefault(dst, [])
        lots_.extend(moved)
        lots_.sort(key=lambda l: (l['date'],
                                  run._lot_time.get(l.get('id'), '')))
    return cast("float", sum(l['qty'] for l in moved))

def _us_book_journal(run: _USRun, out_leg: TaxTransaction, in_leg: TaxTransaction, *,
                     symbol_traces: Dict[str, List[str]]) -> str:
    """One listing journal: a long's lots move from the out-leg's
    listing to the in-leg's, covering first a short the in-leg's
    listing holds, as a purchase there would (`_us_deliver_to_short`;
    backlog TJ-2738); when the out-leg's holds no long and
    the in-leg's holds a short, the short's lots move the other
    way (audit 2026-10-24 B13). Units beyond what the source
    holds have no purchase in this book: the receiver holds them
    at an unknown cost and the source is short by them, both
    tainted. Returns the trace text."""
    q = abs(out_leg.quantity or 0.0)
    ka = _us_ikey_on(run, out_leg.account, out_leg.symbol)
    kb = _us_ikey_on(run, in_leg.account, in_leg.symbol)
    a_long = sum(l['qty'] for l in run.inventory_long.get(ka, []))
    b_short = sum(l['qty'] for l in run.inventory_short.get(kb, []))
    short = a_long <= run.epsilon and b_short > run.epsilon
    if short:
        src_leg, dst_leg, inv, src, dst = (in_leg, out_leg,
                                           run.inventory_short, kb, ka)
    else:
        src_leg, dst_leg, inv, src, dst = (out_leg, in_leg,
                                           run.inventory_long, ka, kb)
    f = _journal_factor(run.symbol_currency.get(src_leg.symbol, ''),
                        src_leg, dst_leg)
    covered = 0.0
    if not short and b_short > run.epsilon:
        covered = _us_deliver_to_short(
            run, in_leg, kb, _us_draw_long_lots(
                run, ka, out_leg.symbol, min(q, a_long, b_short),
                out_leg.date), f, symbol_traces)[0]
    got = covered + _us_carry_lots(run, inv, src, dst, q - covered, f,
                      'proceeds' if short else 'cost_basis',
                      src_leg.symbol, out_leg.date)
    rest = q - got
    if rest > max(run.epsilon, run._dust_noise(q)):
        # Units the source did not hold cover what is left of the
        # short first, as a purchase would, at an unknown cost
        # (backlog TJ-3439): only the others are a long beside it.
        more = 0.0
        if not short and b_short - covered > run.epsilon:
            more = _us_deliver_to_short(run, in_leg, kb, [{
                'qty': min(rest, b_short - covered),
                'cost_basis': Decimal(0), 'date': out_leg.date,
                'tainted': True}], f, symbol_traces)[0]
        for k, side in ((dst, run.inventory_short if short
                         else run.inventory_long),
                        (src, run.inventory_long if short
                         else run.inventory_short)):
            if k == dst and rest - more <= run.epsilon:
                continue
            side.setdefault(k, []).append({
                'qty': rest - more if k == dst else rest,
                ('proceeds' if side is run.inventory_short
                 else 'cost_basis'): Decimal(0),
                'wash_deferred': Decimal(0),
                'date': out_leg.date,
                ('effective_open_date' if side is run.inventory_short
                 else 'effective_acq_date'): out_leg.date,
                'id': out_leg.id, 'tainted': True})
    return (f"LISTING JOURNAL {src_leg.symbol} -> "
            f"{dst_leg.symbol} {got:10.4f}"
            f"{' (a short)' if short else ''}"
            + (f" ({covered:g} cover the short there)" if covered else "")
            + " | basis "
            + ("unknown here (no rate)" if f is None else
               f"carried x{float(f):.6g}")
            + (f" | {rest:g} with no purchase in this book"
               if rest > max(run.epsilon, run._dust_noise(q)) else ""))

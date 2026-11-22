"""The wash-sale rule: a loss's replacements in the window, the add to their
basis, the retests a booked sale queues.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from taxjson.lib.stage_msg import emit_line
from typing import (
    TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, TypeAlias,
    Union, cast,
)
from datetime import datetime, timedelta

from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.rules import ids as R
from taxjson.engine.consts import _ZERO, _CENT
from taxjson.engine.rows import (
    AmbiguousTransferDateError, _iso_ordinal, closed_record, retest_record)

from taxjson.engine.us.lot_store import _us_ov_take, _us_insert_lots
from taxjson.engine.us.run import _RepIndex
from taxjson.engine.us.terms import held_more_than_one_year, outside_1091, _US_MONEY_EPSILON

if TYPE_CHECKING:
    from taxjson.lib.corporate_timeline import SplitTimeline
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.splits import _RenameKey
    from taxjson.engine.us.lot_store import _IKey, _USLot
    from taxjson.engine.us.run import _USRun
    from taxjson.engine.us.terms import _AcctRatios
    from taxjson.engine.us.trades import _GainRow


# A replacement record (the pre-pass's, prepare._us_scan_events): `tx`,
# `date`, `open_qty` and `remaining_qty` (units of its own trade date),
# `is_sheltered`, `is_affiliated`, its lot (`lot_ref`, a short's
# `short_lot_ref`; None until booked), the add a match left before the lot
# was booked (`pending_basis_add` and `pending_matches`, a short's
# `pending_proceeds_reduction`), `effective_acq_date`, and `sold`: its units
# that left (_LeftChunk).
_RepRecord: TypeAlias = Dict[str, Any]
# A record's units that left (`_us_record_left_shares`): `entry` (the gain
# row, or None), `avail`, `sale_tx`; with no entry `lots`, `tack`, `units`
# and `of`; `refused`, `gone` and `boot` once a match read them.
_LeftChunk: TypeAlias = Dict[str, Any]
# A loss's candidate replacement: a record still held, or (record, chunk)
# of units that left (`_us_with_sold_replacements`).
_WashCand: TypeAlias = Union[_RepRecord, Tuple[_RepRecord, _LeftChunk]]
# A loss row's entry for one replacement it matched (`wash_replacements`):
# tx_id, date, qty_total, price, account, match_qty, the basis bump or the
# proceeds cut, is_sheltered, is_affiliated.
_RepEntry: TypeAlias = Dict[str, Any]


# The US engine's helpers as module functions (step 4l): each takes what it
# read of compute_gains's frame as keyword-only parameters, and the engine
# binds a `functools.partial` over its same-named locals where the def
# stood (after the last of those locals is bound, for the six that read a
# later one), so every call site and the run holder are as before while no
# helper keeps the engine's frame alive.
def _us_split_covers(tx: TaxTransaction, seen_groups: Set[_RenameKey], *,
                     _acct_ratios: _AcctRatios) -> Optional[Callable[[str], bool]]:
    """Which accounts a SPLIT row applies to: None for every
    account (one event, one ratio); else a predicate over the
    account name — the accounts whose delivered ratio is this
    row's, plus, for the event's first row met, the accounts
    holding the symbol with no row of their own (they keep the
    first ratio, as the Canada fold does)."""
    ent = _acct_ratios.get(tx.id)
    if ent is None:
        return None
    key, ratio_of = ent
    r = M.units(float(tx.quantity or 0), 9)
    accts = {a for a, ra in ratio_of.items() if M.units(ra, 9) == r}
    first = key not in seen_groups
    seen_groups.add(key)
    return lambda a: a in accts or (first and a not in ratio_of)

def _us_get_sort_date(tx: TaxTransaction) -> str:
    # TRADE date, deliberately NOT settlement: the IRS recognizes
    # capital transactions on the trade date — acquisition order for
    # FIFO, the §1091 61-day window, and the holding period all run
    # on trade dates. Sorting by settlement consumed the WRONG lot
    # whenever settlement lags differed between two lots of one
    # symbol (verified ±$500 on a two-lot scenario), and it also
    # created a settle-date collision class with same-day SPLITs
    # that trade-date ordering never has. (The Canada engine keeps
    # its settle basis — CRA disposition timing.)
    return tx.date

def _us_non_capital(action: str, tx_type: str) -> bool:
    # TRANSFER is handled at the CLI layer (rewritten to BUYSELL
    # for sheltered files, hard-errors out for --taxable).
    return action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
           or tx_type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee')

def _us_dust_noise(*q: float) -> float:
    return 1e-11 * max([1.0] + [abs(x) for x in q])

def _us_note(run: _USRun, date_: str, text: str) -> None:
    if run.emit_stderr:
        emit_line(text)
    else:
        run._dated_notes.append((date_, text))

def _us_rkey_on(run: _USRun, sym: str, on_date: str) -> str:
    """`_us_rep_key` over this run's split timeline (the walk's form)."""
    return _us_rep_key(sym, on_date, split_timeline=run.split_timeline)


def _us_rep_key(sym: str, on_date: str, *, split_timeline: SplitTimeline) -> str:
    """Replacement records are keyed by the rename-chain
    CANONICAL symbol (FUZZ #C): keying by raw symbol made a
    pre-rename loss look up 'OLD.TO' while the post-rename
    rebuy sat under 'NEW.TO' — §1091 silently missed across
    mergers (the SPLIT-time migration ran too late for losses
    processed before the SPLIT row). Canada already
    canonicalizes via alias_of; this is the USA twin. Unit
    conversion stays with _rep_units_factor. The class is
    DATED (A2-0197): an OLD row after its rename date is
    its own security (SplitTimeline.class_at)."""
    return split_timeline.class_at(sym, on_date)

def _us_nkey(acct: str, s: str, *, per_account_basis: bool) -> _IKey:
    # Blended mode: the taxable open/cover partition is per
    # ACCOUNT (an account's sell closes ITS position), matching
    # the per-(account, symbol) FIFO pools in the main pass.
    return (acct, s) if per_account_basis else s

def _us_rep_units_factor(run: _USRun, sym: str, from_date: str, to_date: str) -> float:
    """Cumulative split factor converting a share quantity denominated
    at `from_date` into `to_date` units — the (from, to] boundary and
    reciprocal-backward semantics live in lib/corporate_timeline.py.
    This is what lets a replacement record stay in its own trade-date
    units and still match correctly against a loss in different
    units — including a split BETWEEN the loss and a later
    replacement, which a scale-at-split approach can never see (the
    loss is processed first).

    Queries go through the rename-chain canonical symbol: the
    timeline migrates a symbol's schedule onto the rename target at
    build time, so a raw pre-rename symbol would see an empty
    schedule (factor 1.0) even for splits genuinely inside
    (from, to] — reps are keyed canonically (_rep_key); the unit
    conversion must be too."""
    return run.split_timeline.factor(
        run.split_timeline.canonical(sym), from_date, to_date)

def _us_ix(run: _USRun, rep_list: List[_RepRecord]) -> _RepIndex:
    ix_ = run._rep_ix.get(id(rep_list))
    if ix_ is None or ix_.reps is not rep_list:
        return _RepIndex(rep_list)
    return ix_

def _us_set_ref(run: _USRun, rep: _RepRecord, ref_key: str, lot: Optional[_USLot]) -> None:
    """Point a replacement record at its lot (or None)."""
    rep[ref_key] = lot
    run._rep_home[id(rep)].note_ref(rep, ref_key, lot)

def _us_grow_rep_capacity(run: _USRun, sym: str, on_date: str, lots: List[_USLot],
                          ratio: float) -> None:
    """A stock dividend grew `lots` by `ratio`: the purchases they
    stand for hold that many more shares, so their replacement
    records' capacity grows with them, as after a split, and a
    later loss matches share for share in the new units
    (TJ-0492; the dividend itself is never a purchase,
    US-STKDIV-01)."""
    ix_ = _us_ix(run, run.long_replacements.get(_us_rkey_on(run, sym, on_date), []))
    for lot in lots:
        for r in ix_.holding('lot_ref', lot):
            r['remaining_qty'] = r['remaining_qty'] * ratio

def _us_allot_sold_shares(run: _USRun, sold: List[_GainRow], ratio: float, ikey_: _IKey,
                          div_tx: TaxTransaction, units: Optional[Dict[int, float]] = None
                          ) -> Tuple[Decimal, List[Tuple[_GainRow, Decimal]]]:
    """The new shares a stock dividend pays on entitled shares
    sold before the pay date (`sold`: their gain rows; `units`:
    id(row) -> its quantity in the pay date's units, through any
    split since the sale, TJ-0647): each
    sale gives up 1 - 1/ratio of its cost to its new shares,
    booked as a lot with the sold lot's dates (§307, §1223(5);
    TJ-0498). A sale in another year or one the wash-sale rule
    touched is left as booked: its new shares are a $0 lot and
    the amount is returned for a manual check. Returns (the
    amount moved, [(gain row, amount to move by hand)])."""
    moved = Decimal(0)
    manual = []
    new_lots = []
    keep = D(1) / D(ratio)
    for e in sold:
        cost_d = D(e.get('cost') or 0.0)
        amt = cost_d * (1 - keep)
        wd = D(e.get('deferred_loss_recovered') or 0.0) * (1 - keep)
        if (e['date'][:4] != div_tx.date[:4]
                or M.of(e.get('disallowed_amount') or 0.0) > _US_MONEY_EPSILON
                or e.get('wash_basis_added')):
            manual.append((e, M.of(amt)))
            amt = wd = Decimal(0)
        elif amt or wd:
            e['cost'] = M.of(cost_d - amt)
            e['gain'] = e['gain'] + M.of(amt)
            e['raw_gain'] = e['raw_gain'] + M.of(amt)
            if wd:
                e['deferred_loss_recovered'] = M.of(
                    D(e['deferred_loss_recovered']) - wd)
            moved += amt
        eff = (datetime.strptime(e['date'], '%Y-%m-%d')
               - timedelta(days=int(e.get('days_held') or 0))
               ).strftime('%Y-%m-%d')
        new_lots.append({
            'qty': cast("float", (units or {}).get(id(e), e['qty'])) * (ratio - 1),
            'cost_basis': amt,
            'wash_deferred': wd,
            'date': e.get('acquired_date') or div_tx.date,
            'effective_acq_date': eff,
            'id': div_tx.id,
            **({'tainted': True} if e.get('tainted') else {}),
            **({'unknown_from': dict(e['unknown_from'])}
               if e.get('unknown_from') else {}),
        })
    if new_lots:
        _us_insert_lots(run, ikey_, new_lots)
    return moved, manual

def _us_apply_other_stkdivs(run: _USRun, upto: TaxTransaction) -> None:
    """The context books' stock dividends sorted before `upto`
    (a main-pass row): each grows the capacity of the purchases
    that account made before its entitlement date (TJ-0492)."""
    p = run._ev_pos.get(id(upto))
    if p is None:
        return
    while (run._other_stkdivs
           and run._ev_pos.get(id(run._other_stkdivs[0][0]), -1) < p):
        ev, cls, ent_d, ratio = run._other_stkdivs.pop(0)
        for r in run.long_replacements.get(cls, []):
            if (r['tx'].account == ev.account
                    and (r['is_sheltered'] or r['is_affiliated'])
                    and r['date'] <= ev.date
                    and (ent_d is None or r['date'] < ent_d)):
                r['remaining_qty'] = r['remaining_qty'] * ratio

def _us_split_rep_refs(run: _USRun, sym: str, on_date: str, lot: _USLot, part: _USLot,
                       ref_key: str) -> None:
    """`part` was split off `lot` and goes on its own (a move
    between your own accounts, a listing journal): each
    replacement record holding `lot` gives `part` its share of
    the capacity, as a record of its own pointing at `part`. A
    later wash match then adds its basis (a short's: its
    proceeds cut) and holding period to the units it matches,
    wherever they went, and a sale of either part uses up only
    that part's capacity (TJ-0490: the sender's half kept the
    whole capacity and took the whole basis add)."""
    whole = lot['qty'] + part['qty']
    if whole <= run.epsilon:
        return
    ix_ = _us_ix(run, (run.long_replacements if ref_key == 'lot_ref'
               else run.short_replacements).get(_us_rkey_on(run, sym, on_date),
                                            []))
    for r in ix_.holding(ref_key, lot):
        share = part['qty'] / whole
        child = dict(r)
        child.pop('sold', None)
        child['pending_matches'] = []
        child['pending_basis_add'] = Decimal(0)
        child['pending_proceeds_reduction'] = Decimal(0)
        child['open_qty'] = r.get('open_qty', 0.0) * share
        child['remaining_qty'] = r['remaining_qty'] * share
        r['remaining_qty'] -= child['remaining_qty']
        child[ref_key] = None
        ix_.add_after(r, child)
        run._rep_home[id(child)] = ix_
        _us_set_ref(run, child, ref_key, part)

def _us_find_replacements_in_window(run: _USRun, rep_list: List[_RepRecord],
                                    loss_date_str: str) -> List[_RepRecord]:
    # No ID-based self-exclusion: the FIFO pop/partial branches
    # zero or decrement the consumed shares' rep capacity BEFORE
    # the wash match runs, so a loss can never match the very
    # shares it just sold — but the RETAINED shares of a
    # partially-sold purchase are genuine §1091 replacements
    # (brokers report code W on them), and excluding the whole
    # originating purchase by tx id made the disallowance depend
    # on order granularity: one 200-share order → loss allowed,
    # two 100-share orders same day → loss disallowed.
    try:
        loss_ord = _iso_ordinal(loss_date_str)
    except ValueError:
        return []
    out = []
    # The records dated within the window, in list order (a
    # record whose date does not parse is never one).
    for rep in _us_ix(run, rep_list).within(loss_ord, run.window_days,
                                    run.window_days):
        if rep['remaining_qty'] <= run.epsilon:
            continue
        if getattr(rep['tx'], 'type', '') \
                == 'transfer_rewrite':
            raise AmbiguousTransferDateError(
                f"{rep['tx'].symbol}: a TRANSFER-in dated "
                f"{rep['tx'].date} (account "
                f"{rep['tx'].account}) sits inside the "
                f"wash-sale window of the {loss_date_str} "
                f"loss, but its date is a broker ARRIVAL "
                f"date that may not be an acquisition. "
                f"Import the prior broker's history (the "
                f"pair then nets out), add a DECLARED "
                f"counter-TRANSFER .tt row (custody move; "
                f"the one-line ACQUIRED ... ARRIVED ... "
                f"form covers move + true cost), or "
                f"record the true acquisition as a "
                f"BUYSELL row.")
        out.append(rep)
    # Reg. 1.1091-1(c): replacement shares match in the ORDER
    # ACQUIRED. Date alone left same-date ties to pre-pass insertion
    # order (which follows SETTLE date), so which lot inherited the
    # deferred loss + holding period was effectively random — and a
    # sheltered same-date lot could beat an earlier-acquired taxable
    # one, flipping a deferral into a permanent denial. Tie-break by
    # intra-day time; at the SAME moment the taxpayer's own
    # (taxable) lot first, then sheltered, then affiliated —
    # never by the content-hash id, which let a one-cent change
    # on an IRA row flip a deferral into a permanent denial
    # (audit S018-06), and never by the account LABEL: renaming
    # an account moved the deferral (audit A2-0200/A2-0208).
    out.sort(key=lambda r: (r['date'], r['tx'].time or '',
                            2 if r['is_affiliated']
                            else 1 if r['is_sheltered'] else 0))
    # (Stable sort: rows still tied keep the pre-pass order,
    # which is the merged book's row order — the export's row
    # order within an account and the accounts' taxjson.toml
    # order across accounts (US-DATE-13), the only evidence of
    # acquisition order for same-moment lots. A content-hash id
    # rung let a one-cent price change move the deferral to the
    # other lot, audit S070-12.)
    return out

def _us_taxable_rep_of(replacements: Dict[str, List[_RepRecord]]) -> Dict[RowId, _RepRecord]:
    """{row id: its first replacement record} of the taxable accounts'
    records in `replacements` (by class)."""
    out: Dict[RowId, _RepRecord] = {}
    for rl in replacements.values():
        for r in rl:
            if not (r['is_sheltered'] or r['is_affiliated']):
                out.setdefault(r['tx'].id, r)
    return out

def _us_record_sold_replacement(run: _USRun, lot: _USLot, entry: _GainRow, chunk_qty: float,
                                sale_tx: TaxTransaction) -> None:
    """The sale of a purchase's never-matched shares: kept on the
    purchase's replacement record (US-WASH-22)."""
    _us_record_left_shares(run, lot, chunk_qty, sale_tx, entry=entry)

def _us_record_left_shares(run: _USRun, lot: _USLot, qty: float, tx: TaxTransaction, *,
                           entry: Optional[_GainRow] = None, tack: bool = False,
                           short: bool = False) -> Optional[_LeftChunk]:
    """`qty` never-matched units of a taxable purchase (`lot`, of
    `run._long_rep_of`; `short`: of a short sale, `run._short_rep_of`,
    covered by `tx`, §1091(e), TJ-2708) that leave by row `tx`: kept on the
    purchase's replacement record (`sold`), so another account's
    later loss in the window still matches them — §1091 has no
    still-held test (US-WASH-06, US-WASH-22). With `entry`, the
    gain row of the sale that closed them, where a match adds its
    §1091(d) basis. Without one (a §356 exchange's old shares, the
    warrants or contracts an exercise gave up: TJ-2700, TJ-2737),
    the chunk's `lots` are the lots they became (the new shares,
    the shares delivered), named by the caller, which take the
    add instead, their holding period tacked when `tack` (the new
    shares carry the old shares', §1223(1); an exercise's shares
    start their own). Returns the chunk (None: not recorded)."""
    rep = (run._short_rep_of if short
           else run._long_rep_of).get(lot.get('id'))
    if (rep is None or lot.get('tainted')
            or lot.get('wash_deferred', Decimal(0))
            or lot.get('wash_basis_spent')
            or not run.detect_wash_sales):
        return None
    _uf = _us_rep_units_factor(run, tx.symbol, rep['date'],
                            tx.date) or 1.0
    ch: _LeftChunk = {'entry': entry, 'avail': qty / _uf, 'sale_tx': tx}
    if entry is None:
        ch['lots'] = []
        ch['tack'] = bool(tack)
        # (an exercise's: its units of the row's, TJ-3430)
        ch['units'], ch['of'] = qty, abs(tx.quantity or 0.0)
    rep.setdefault('sold', []).append(ch)
    return ch

def _us_sold_replacements_in_window(run: _USRun, rep_list: List[_RepRecord],
                                    loss_tx: TaxTransaction, short: bool = False,
                                    retest: Union[bool, RowId] = False
                                    ) -> List[_WashCand]:
    """[(rep, chunk)] a long loss of `loss_tx` may still match:
    shares of a taxable purchase in ANOTHER account, bought in
    the window and sold before the loss, never matched and not
    sold at a disallowed loss of their own (US-WASH-22). `short`:
    a cover's loss and another account's short sales, opened in
    the window and covered before it (§1091(e), TJ-2708).
    `retest` (the id of the loss whose add made this one): the
    loss is an earlier row's, tested after the walk passed it
    (TJ-3428): the purchases of its whole window that were sold
    since, in another account at any time, in its own only after
    the loss (shares still unsold at the loss: US-WASH-21), never
    the shares that later loss itself sold."""
    try:
        loss_ord = _iso_ordinal(loss_tx.date)
    except ValueError:
        return []
    out: List[_WashCand] = []      # (record, chunk) pairs
    # The records bought on the loss date or the 30 days before.
    for rep in _us_ix(run, rep_list).within(
            loss_ord, run.window_days, run.window_days if retest else 0):
        if (rep['is_sheltered'] or rep['is_affiliated']
                or (rep['tx'].account == loss_tx.account and not retest)
                or not rep.get('sold')):
            continue
        for ch in rep['sold']:
            e = ch['entry']
            own = loss_tx.account in (rep['tx'].account,
                                      ch['sale_tx'].account)
            if (ch['avail'] <= run.epsilon
                    or ch['sale_tx'].id in (loss_tx.id, retest)
                    or (own and not (retest and ch['sale_tx'].date
                                     > loss_tx.date))):
                continue
            if e is None:
                # Shares that left with no sale (an exchange, an
                # exercise: TJ-2700, TJ-2737): matched once the lots
                # they became are in the books, with a basis.
                # A put's exercise delivers none (`lots` empty):
                # not matched.
                if not ch.get('lots') or any(
                        lot_.get('tainted') for lot_ in ch['lots']):
                    continue
                # (Lots sold, in whole or in part, since: no lot is
                # left to take the add, and the match names the
                # shares for a manual check: `gone`, TJ-3433.)
                ch['gone'] = not _us_left_lots_held(run, ch)
                out.append((rep, ch))
                continue
            if e.get('tainted'):
                continue
            # That sale's own loss was disallowed: a basis add
            # there would need its wash redone — named for a manual
            # check instead (US-WASH-22), when its turn comes with
            # loss shares still unmatched (`refused`, at the match:
            # backlog TJ-3437).
            ch['refused'] = (e.get('disallowed_amount', _ZERO)
                             > _US_MONEY_EPSILON)
            out.append((rep, ch))
    return out

def _us_apply_held_long(run: _USRun, rep: _RepRecord, match_qty: float, uf: float,
                        amt_d: Decimal, loss_date: str, loss_lot: _USLot) -> _RepEntry:
    """A loss's match to a replacement purchase still in the books
    (`rep`, `match_qty` shares in the loss date's units, `amt_d` the
    loss disallowed on them): the walk's own block until TJ-3428,
    which tests a row's loss after the walk passed it too. Returns
    the loss row's replacement entry.

    §1223(3) tacking: the replacement's holding period gains the
    PERIOD the wash-sold shares were held, not their calendar
    acquisition date. Inheriting the raw date wrongly included the
    sale→repurchase gap (up to 30 days) in the holding period —
    flipping SHORT/LONG_TERM near the anniversary — and collapsed
    the overlap for replacements bought before the sale. Broker
    convention: adjusted acquisition date = the replacement's own
    buy date minus the prior holding period.

    Tacking and the §1091(d) bump apply to the MATCHED shares only.
    A replacement lot bigger than the match is split at the match
    point: the matched sub-lot (kept as the original object, so lot
    identity elsewhere holds) takes the tack + bump; the untouched
    remainder becomes a new lot right behind it in FIFO order and
    the rep's lot_ref for any later match. Whole-lot tacking
    mis-termed the remainder (2026-09 US-engine audit: a 200-share
    rep for a 100-share loss reported all 200 as LONG_TERM).
    Inventory lots are rescaled by every SPLIT row, so the lot is
    already in the loss date's units, like match_qty — dividing by
    the rep's units factor put the bump on the pre-split share count
    (audit A2-0054).

    An IRA's (Rev. Rul. 2008-5: no basis transfer) or a spouse's /
    controlled corporation's (§1091(d) adds the loss to THEIR
    replacement's basis): no lot in these books carries it, so it
    is not a deferral here — reporting it as one left a deferred
    amount nothing recovers (partition ENGINE-I1, tax-logic
    US-WASH-16). A purchase not yet booked takes the add when it is
    (`pending_basis_add`), one block per matched loss, each with its
    own bump and tacked holding period (§1223(3), audit A2-0060 —
    one merged sub-lot averaged the bumps and gave every share the
    earliest tacked date)."""
    loss_acq_eff = cast("str", loss_lot.get('effective_acq_date', loss_lot['date']))
    try:
        _prior_held = (datetime.strptime(loss_date, '%Y-%m-%d')
                       - datetime.strptime(loss_acq_eff, '%Y-%m-%d'))
        _tacked_eff = (datetime.strptime(rep['date'], '%Y-%m-%d')
                       - _prior_held).strftime('%Y-%m-%d')
    except ValueError:
        _tacked_eff = loss_acq_eff
    _tack_lot = rep.get('lot_ref')
    if _tack_lot is not None:
        _mq = match_qty
        if _tack_lot['qty'] > _mq + run.epsilon:
            _frac = D(_mq) / D(_tack_lot['qty'])
            _rem = dict(_tack_lot)
            _rem['qty'] = _tack_lot['qty'] - _mq
            _rem['cost_basis'] = _tack_lot['cost_basis'] * (1 - _frac)
            _rem['wash_deferred'] = (
                _tack_lot.get('wash_deferred', Decimal(0)) * (1 - _frac))
            _tack_lot['qty'] = _mq
            _tack_lot['cost_basis'] = _tack_lot['cost_basis'] * _frac
            _tack_lot['wash_deferred'] = (
                _tack_lot.get('wash_deferred', Decimal(0)) * _frac)
            for _lots in run.inventory_long.values():
                for _li, _l in enumerate(_lots):
                    if _l is _tack_lot:
                        _lots.insert(_li + 1, _rem)
                        break
            _us_set_ref(run, rep, 'lot_ref', _rem)
    if _tacked_eff < rep['effective_acq_date']:
        rep['effective_acq_date'] = _tacked_eff
    if (_tack_lot is not None
            and _tacked_eff < _tack_lot['effective_acq_date']):
        _tack_lot['effective_acq_date'] = _tacked_eff
    if not (rep['is_sheltered'] or rep.get('is_affiliated')):
        # (A taxable replacement sold before the loss has no
        # capacity left and is never a candidate: US-WASH-21.)
        if _tack_lot is not None:
            _tack_lot['cost_basis'] += amt_d
            _tack_lot['wash_deferred'] = (
                _tack_lot.get('wash_deferred', Decimal(0)) + amt_d)
        else:
            rep['pending_basis_add'] += amt_d
            rep.setdefault('pending_matches', []).append(
                (match_qty / (uf or 1.0), amt_d, _tacked_eff))
    return {
        'tx_id': rep['tx'].id,
        'date': rep['date'],
        'qty_total': float(rep['tx'].quantity),
        'price': M.of(rep['tx'].price),
        'account': rep['tx'].account,
        'match_qty': match_qty,
        'basis_bump': M.of(amt_d),
        'is_sheltered': rep['is_sheltered'],
        'is_affiliated': rep.get('is_affiliated', False),
    }

def _us_apply_held_short(run: _USRun, rep: _RepRecord, match_qty: float,
                         amt_d: Decimal) -> _RepEntry:
    """A cover's loss matched to a replacement short sale not yet
    covered (`rep`): the walk's own block until TJ-3428. Returns the
    loss row's replacement entry.

    An IRA's (Rev. Rul. 2008-5) or a spouse's / controlled
    corporation's: the §1091(d) adjustment belongs to THEIR
    position, never to a lot in these books (ENGINE-I1). (A
    replacement short covered before the loss has no capacity left
    and is never a candidate: US-WASH-21.) A replacement short
    already open has its remaining proceeds reduced now, the
    embedded deferral tracked so inventory reports show it (the long
    side already did; short-side deferrals were invisible). Share
    for share, as on the long side: a replacement short bigger than
    the match is split, the matched shares first in FIFO carry the
    whole reduction (audit A2-0206 — it was spread over every share,
    moving loss into a later year). One not yet sold takes it when
    it is (`pending_proceeds_reduction`)."""
    if not (rep['is_sheltered'] or rep.get('is_affiliated')):
        if rep.get('short_lot_ref') is not None:
            _sl = _us_split_matched_short(run, rep, match_qty)
            _sl['proceeds'] -= amt_d
            _sl['wash_deferred'] = (
                _sl.get('wash_deferred', Decimal(0)) + amt_d)
        else:
            rep['pending_proceeds_reduction'] += amt_d
    return {
        'tx_id': rep['tx'].id,
        'date': rep['date'],
        'qty_total': float(rep['tx'].quantity),
        'price': M.of(rep['tx'].price),
        'account': rep['tx'].account,
        'match_qty': match_qty,
        'proceeds_reduction': M.of(amt_d),
        'is_sheltered': rep['is_sheltered'],
        'is_affiliated': rep.get('is_affiliated', False),
    }

def _us_split_matched_short(run: _USRun, rep: _RepRecord, match_qty: float) -> _USLot:
    """The open short lot of replacement record `rep`, cut to the
    `match_qty` shares a loss matched: a bigger lot is split, the
    matched shares first in FIFO (returned), the rest right behind
    them as the record's lot for a later match (audit A2-0206; the
    walk's own block until TJ-2708)."""
    _sl: _USLot = rep['short_lot_ref']
    if _sl['qty'] > match_qty + run.epsilon:
        _sfr = D(match_qty) / D(_sl['qty'])
        _srem = dict(_sl)
        _srem['qty'] = _sl['qty'] - match_qty
        _srem['proceeds'] = _sl['proceeds'] * (1 - _sfr)
        _swd0 = _sl.get('wash_deferred', Decimal(0))
        _srem['wash_deferred'] = _swd0 * (1 - _sfr)
        _sl['qty'] = match_qty
        _sl['proceeds'] = _sl['proceeds'] - _srem['proceeds']
        _sl['wash_deferred'] = _swd0 - _srem['wash_deferred']
        for _sls in run.inventory_short.values():
            for _si, _s in enumerate(_sls):
                if _s is _sl:
                    _sls.insert(_si + 1, _srem)
                    break
        _us_set_ref(run, rep, 'short_lot_ref', _srem)
    return _sl

def _us_with_sold_replacements(run: _USRun, candidates: Sequence[_WashCand],
                               rep_list: List[_RepRecord], loss_tx: TaxTransaction,
                               short: bool = False, retest: Union[bool, RowId] = False
                               ) -> Sequence[_WashCand]:
    """`candidates` (a loss's replacements still held) with the ones
    another taxable account closed before the loss
    (`_us_sold_replacements_in_window`), in the order acquired; a
    purchase's still-held shares match before its sold ones."""
    sold = _us_sold_replacements_in_window(run, rep_list, loss_tx, short,
                                           retest)
    if not sold:
        return candidates
    rank = _us_ix(run, rep_list).pos

    def _ck(c: _WashCand) -> Tuple[Any, str, int, Tuple[int, ...], int]:
        r = c[0] if isinstance(c, tuple) else c
        return (r['date'], r['tx'].time or '',
                2 if r['is_affiliated'] else 1 if r['is_sheltered'] else 0,
                rank(r), 1 if isinstance(c, tuple) else 0)
    return sorted(list(candidates) + sold, key=_ck)

def _us_match_sold_replacement(run: _USRun, cand: Tuple[_RepRecord, _LeftChunk],
                               loss_qty: float, loss_per_share_d: Decimal,
                               loss_tx: TaxTransaction, loss_lot: _USLot, ov_on: bool,
                               ov_share: Optional[List[Decimal]], short: bool = False,
                               root: Optional[Dict[str, Any]] = None
                               ) -> Tuple[float, Optional[_RepEntry]]:
    """Match up to `loss_qty` loss shares to `cand`, a (record, chunk)
    of `_us_sold_replacements_in_window`: (units matched, the loss
    row's replacement entry — None when a filing position keeps the
    loss allowed, `ov_on`). The disallowed amount goes to the earlier
    row or the lots the shares became (`_us_apply_sold_replacement`)."""
    srep, sch = cand
    uf = _us_rep_units_factor(run, loss_tx.symbol, srep['date'],
                              loss_tx.date) or 1.0
    match_qty = min(loss_qty, sch['avail'] * uf)
    if sch.get('entry') is not None:
        # A row two records share (a moved lot's cover: the short's
        # and the lot's) may have been split for the other since:
        # no more than its units are left (backlog TJ-3464).
        _cap = float(sch['entry']['qty']) * uf / (_us_rep_units_factor(
            run, sch['sale_tx'].symbol, srep['date'], sch['sale_tx'].date)
            or 1.0)
        if _cap < match_qty - 1e-9:
            match_qty = _cap
            sch['avail'] = _cap / uf
    amt_d = D(match_qty) * loss_per_share_d
    if sch.get('refused'):
        _us_note(run, loss_tx.date,
              f"warning: {loss_tx.symbol}: the {loss_tx.date}"
              f" loss ({loss_tx.account}) has a "
              f"replacement "
              f"{'sold short' if short else 'bought'} "
              f"{srep['date']} in "
              f"{srep['tx'].account} and "
              f"{'covered' if short else 'sold'} "
              f"{sch['entry'].get('date')} at a loss that was itself "
              f"disallowed — not matched; check this "
              f"wash sale by hand (§1091).")
        sch['avail'] = 0.0
        return 0.0, None
    if sch.get('gone'):
        _us_note(run, loss_tx.date,
              f"warning: {loss_tx.symbol}: the {loss_tx.date} loss "
              f"({loss_tx.account}) has a replacement bought "
              f"{srep['date']} in {srep['tx'].account} that left on "
              f"{sch['sale_tx'].date} by an exchange or an exercise, and "
              f"shares it became were sold before the loss: up to "
              f"{match_qty:g} of the loss's shares ({M.of(amt_d):,.2f} "
              f"of it) are not matched to them and stay allowed here "
              f"— check this wash sale by hand (§1091).")
        sch['avail'] = 0.0
        return 0.0, None
    sch['avail'] -= match_qty / uf
    if ov_on:
        assert ov_share is not None, "_us_ov_loss turns the overlap on only with a share"
        _us_ov_take(run, loss_tx, srep['tx'], False, False, match_qty,
                    M.of(amt_d), ov_share)
        return match_qty, None
    se = _us_apply_sold_replacement(run, srep, sch, match_qty, uf, amt_d,
                                    loss_tx, loss_lot, short=short, root=root)
    return match_qty, {
        'tx_id': srep['tx'].id,
        'date': srep['date'],
        'qty_total': float(srep['tx'].quantity),
        'price': M.of(srep['tx'].price),
        'account': srep['tx'].account,
        'match_qty': match_qty,
        ('proceeds_reduction' if short else 'basis_bump'): M.of(amt_d),
        'is_sheltered': False,
        'is_affiliated': False,
        **({'covered_before_loss': se.get('trade_date') or se.get('date')}
           if short else {'sold_before_loss': se.get('date')}),
    }

def _us_split_gain_entry(run: _USRun, entry: _GainRow, q: float) -> _GainRow:
    """Split a gain row so its first `q` units are their own row
    (returned); the rest stays right behind it in the list. Only the
    keys listed are scaled: a row split here is a sold replacement's
    sale, which never carries a denial or a deferral cut
    (`_us_sold_replacements_in_window` refuses a sale with its own
    disallowance, `_us_record_sold_replacement` records only lots with
    no wash deferral), so the other money keys are zero (TJ-3086). A
    filing position's row carries no denial but its share of what the
    rule would have made (`ov_share`): that is scaled too (TJ-3095)."""
    if entry['qty'] <= q + run.epsilon:
        return entry
    frac = q / entry['qty']
    head = dict(entry)
    for k in ('qty', 'cost', 'proceeds', 'gain', 'raw_gain',
              'commission', 'fee', 'deferred_loss_recovered',
              'capitalized_263h',
              _LO.ROW_DISALLOWED, _LO.ROW_PERMANENT):
        if k not in entry:
            continue
        head[k] = entry[k] * (frac if k == 'qty' else M.of(frac))
        entry[k] = entry[k] - head[k]
    entry['trace'] = []
    for i_, e_ in enumerate(run.realized_gains):
        if e_ is entry:
            run.realized_gains.insert(i_, head)
            break
    # A cover's row in the compensation plan's building run: the
    # head is a chunk of the same cover and short lot (TJ-2708).
    for e_, cover_id, lot_id in list(run._short_cover_chunks):
        if e_ is entry:
            run._short_cover_chunks.append((head, cover_id, lot_id))
            break
    # The rest keeps the record of unmatched shares; the head
    # takes their place there.
    return head

def _us_tacked_date(rep_date: str, loss_tx: TaxTransaction, loss_lot: _USLot) -> Optional[str]:
    """The replacement's acquisition date less the period the loss
    shares were held (§1223(3)), or None when a date is unreadable."""
    _leff = cast("str", loss_lot.get('effective_acq_date', loss_lot['date']))
    try:
        _prior = (datetime.strptime(loss_tx.date, '%Y-%m-%d')
                  - datetime.strptime(_leff, '%Y-%m-%d'))
        return (datetime.strptime(rep_date, '%Y-%m-%d')
                - _prior).strftime('%Y-%m-%d')
    except ValueError:
        return None

def _us_left_lots_held(run: _USRun, ch: _LeftChunk) -> List[Tuple[_IKey, List[_USLot], _USLot]]:
    """[(pool key, pool, lot)]: the lots a left chunk's shares became
    (`ch['lots']`) that are still in the books."""
    out: List[Tuple[_IKey, List[_USLot], _USLot]] = []
    for lot_ in ch.get('lots') or ():
        for key, pool in run.inventory_long.items():
            if any(x is lot_ for x in pool):
                out.append((key, pool, lot_))
                break
    return out

def _us_restate_boot(run: _USRun, rep: _RepRecord, ch: _LeftChunk, match_ru: float,
                     amt_d: Decimal, loss_tx: TaxTransaction, loss_lot: _USLot) -> Decimal:
    """A §356 exchange's old shares that a later loss matched
    (`match_ru` of chunk `ch`, in the purchase's units): their basis
    was `amt_d` higher at the exchange, so the gain the exchange
    recognized on them is recomputed on it — the boot received, at
    most the gain realized (§356(a)(1)) — and the part that falls
    comes off the exchange's row (`ch['boot']`: that row, the gain
    realized and the boot per old share), split at the matched
    shares, as an add to a sale's basis does (`_us_add_to_row`: a
    filed year keeps its row, a surviving gain's term follows the
    tacked period). Returns that part: the new shares take the rest
    of the add (§358: the old basis, less the boot, plus the gain
    recognized) (backlog TJ-3427)."""
    boot = ch.get('boot')
    if not boot:
        return Decimal(0)
    entry, unit_gain, unit_boot = boot
    sale = ch['sale_tx']
    q = match_ru * (_us_rep_units_factor(run, sale.symbol, rep['date'],
                                         sale.date) or 1.0)
    was = max(Decimal(0), min(unit_gain, unit_boot) * D(q))
    now = max(Decimal(0), min(unit_gain * D(q) - amt_d, unit_boot * D(q)))
    cut: Decimal = was - now
    if cut <= 0:
        return Decimal(0)
    head = _us_split_gain_entry(run, entry, q)
    _us_add_to_row(head, M.of(cut), q, rep, loss_tx, loss_lot)
    head['note'] = (f"{head.get('note') or ''}; recognized "
                    f"{M.of(now):.2f} on the basis a later wash sale "
                    f"added (§1091(d))")
    return cut

def _us_name_delivered_lots(run: _USRun, ikey: _IKey, new_lots: List[_USLot],
                            tx: TaxTransaction, chunks: List[Dict[str, Any]]) -> None:
    """The shares an exercise delivered (`new_lots`, just booked by
    row `tx`) named on the exercised replacement units' chunks (`chunks`:
    what the row took from the assignment ledger, `Taken.chunks`), which take a later match's
    §1091(d) add (TJ-2737) — share for share (backlog TJ-3430): a
    chunk's units of the exercise (`units` of `of`) get that part of
    the delivery as a lot of their own, cut from the one lot
    delivered with none of the deferral the other exercised units
    brought (an earlier wash of their own stays on their shares).
    A delivery booked as several blocks is named whole, as before."""
    if not chunks:
        return
    pool = run.inventory_long[ikey]
    total = sum(lot_['qty'] for lot_ in new_lots)
    last = new_lots[-1]
    for ch in chunks:
        want = total * ch['units'] / ch['of'] if ch.get('of') else 0.0
        if (len(new_lots) != 1 or want <= run.epsilon
                or want >= last['qty'] - run.epsilon):
            ch['lots'].extend(new_lots)
            continue
        _wd = last.get('wash_deferred', Decimal(0))
        part = dict(last)
        part['qty'] = want
        part['cost_basis'] = ((last['cost_basis'] - _wd)
                              * D(want) / D(last['qty']))
        part['wash_deferred'] = Decimal(0)
        last['qty'] -= want
        last['cost_basis'] -= part['cost_basis']
        for _i, _l in enumerate(pool):
            if _l is last:
                pool.insert(_i + 1, part)
                break
        _us_split_rep_refs(run, tx.symbol, tx.date, last, part, 'lot_ref')
        ch['lots'].append(part)

def _us_apply_left_replacement(run: _USRun, rep: _RepRecord, ch: _LeftChunk, match_ru: float,
                               amt_d: Decimal, loss_tx: TaxTransaction,
                               loss_lot: _USLot) -> Dict[str, Any]:
    """Add `amt_d` to the basis of the lots that the matched shares of
    `rep` (`match_ru` of them, in the purchase's units) became when
    they left with no sale (`ch['lots']`: a §356 exchange's new
    shares, the shares an exercise delivered), by their units, the
    add shown as the lots' deferral. A match of part of the chunk
    splits each lot at the match, as a held replacement lot is: the
    matched part takes the add, and the loss shares' holding period
    when the lots carry the old shares' (`ch['tack']`, §1223(1) over
    §1223(3)); the rest answers a later match (TJ-2700, TJ-2737).
    An exchange's boot gain on the matched shares is restated first
    and takes its part of the add (`_us_restate_boot`).
    Returns the record of the add."""
    held = _us_left_lots_held(run, ch)
    amt_d -= _us_restate_boot(run, rep, ch, match_ru, amt_d, loss_tx, loss_lot)
    before = ch['avail'] + match_ru
    frac = (D(match_ru) / D(before)
            if ch['avail'] > run.epsilon and before > 0 else Decimal(1))
    _tack = (_us_tacked_date(rep['date'], loss_tx, loss_lot)
             if ch.get('tack') else None)
    total = sum(D(lot_['qty']) for _k, _p, lot_ in held)
    left = amt_d
    for i, (key, pool, lot_) in enumerate(held):
        share = (left if i == len(held) - 1 or total <= 0
                 else amt_d * D(lot_['qty']) / total)
        left -= share
        if frac < 1:
            rest = dict(lot_)
            _wd = lot_.get('wash_deferred', Decimal(0))
            rest['qty'] = lot_['qty'] * float(1 - frac)
            rest['cost_basis'] = lot_['cost_basis'] * (1 - frac)
            rest['wash_deferred'] = _wd * (1 - frac)
            lot_['qty'] -= rest['qty']
            lot_['cost_basis'] -= rest['cost_basis']
            lot_['wash_deferred'] = _wd - rest['wash_deferred']
            for _i, _l in enumerate(pool):
                if _l is lot_:
                    pool.insert(_i + 1, rest)
                    break
            _us_split_rep_refs(
                run, key[1] if run.per_account_basis else key,  # type: ignore[arg-type]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)
                loss_tx.date, lot_, rest, 'lot_ref')
            ch['lots'] = [rest if x is lot_ else x for x in ch['lots']]
        lot_['cost_basis'] += share
        lot_['wash_deferred'] = lot_.get('wash_deferred', Decimal(0)) + share
        if _tack and _tack < cast("str", lot_.get('effective_acq_date', lot_['date'])):
            lot_['effective_acq_date'] = _tack
    return {'date': ch['sale_tx'].date, 'amount': M.of(amt_d),
            'left_by': ch['sale_tx'].id}

def _us_apply_sold_replacement(run: _USRun, rep: _RepRecord, ch: _LeftChunk,
                               match_qty: float, uf: float, amt_d: Decimal,
                               loss_tx: TaxTransaction, loss_lot: _USLot,
                               short: bool = False, root: Optional[Dict[str, Any]] = None
                               ) -> Dict[str, Any]:
    """Add `amt_d` (the disallowed loss of `match_qty` units, in
    loss-date units) to the basis of the matched shares of the
    earlier sale, with the loss shares' holding period tacked on
    (§1091(d), §1223(3)); shares that left with no sale: the lots
    they became (`_us_apply_left_replacement`). `short`: the
    earlier row is another account's cover and the amount comes
    off the proceeds of the short sale it closed (the row's cost
    is their negative, so the same sum); a short sale's term stays
    short (§1091(e), TJ-2708)."""
    if ch.get('entry') is None:
        return _us_apply_left_replacement(run, rep, ch, match_qty / uf,
                                          amt_d, loss_tx, loss_lot)
    sale = ch['sale_tx']
    _ufs = _us_rep_units_factor(run, sale.symbol, rep['date'],
                             sale.date) or 1.0
    q_sale = match_qty / uf * _ufs
    entry = _us_split_gain_entry(run, ch['entry'], q_sale)
    _us_add_to_row(entry, M.of(amt_d), q_sale, rep, loss_tx, loss_lot,
                   short=short)
    if root:        # (a re-test's add: the loss that began the chain)
        entry['wash_basis_added'][-1].update(
            {f'root_{k}': v for k, v in root.items()})
    _us_queue_retest(run, entry, sale, M.of(amt_d))
    return entry

def _us_add_to_row(entry: _GainRow, amt: Decimal, q: float, rep: _RepRecord,
                   loss_tx: TaxTransaction, loss_lot: _USLot, short: bool = False) -> None:
    """Add `amt` to the cost of the gain row `entry` (`q` units of it,
    the whole row), the row of an earlier sale or cover of
    replacement `rep` that the loss of `loss_tx` matched: its gain
    falls by it, the loss shares' holding period is tacked on (never
    on a short sale's row), and the row lists the add
    (`wash_basis_added`; `pre_retro`: the row before any add, which a
    filed year keeps). A row the add leaves at a loss is tested for
    a wash sale of its own by the caller (`_us_queue_retest`)."""
    if 'pre_retro' not in entry:
        entry['pre_retro'] = {
            k: entry.get(k) for k in ('cost', 'gain', 'raw_gain',
                                      'term', 'days_held')}
    entry['cost'] += amt
    entry['raw_gain'] -= amt
    entry['gain'] -= amt
    _tack = _eff0 = None
    # (a cover a moved lot closed is a short's row too: TJ-3424)
    if not short and entry.get('direction') != 'SHORT':
        _tack = _us_tacked_date(rep['date'], loss_tx, loss_lot)
        try:
            _eff0 = (datetime.strptime(entry['date'], '%Y-%m-%d')
                     - timedelta(days=int(entry.get('days_held') or 0))
                     ).strftime('%Y-%m-%d')
        except ValueError:
            _tack = None
    tacked_term = entry.get('term')
    if _tack and _eff0 and _tack < _eff0:
        entry['days_held'] = (
            datetime.strptime(entry['date'], '%Y-%m-%d')
            - datetime.strptime(_tack, '%Y-%m-%d')).days
        tacked_term = ('LONG_TERM'
                       if held_more_than_one_year(_tack,
                                                  entry['date'])
                       else 'SHORT_TERM')
        entry['term'] = tacked_term
        if entry.get('reorg_356'):
            # (the row's dates read as its term does: TJ-3458)
            entry['acquired_date'] = _tack
    entry.setdefault('wash_basis_added', []).append({
        'amount': amt, 'qty': q,
        'loss_id': loss_tx.id, 'loss_date': loss_tx.date,
        'loss_date_settle': loss_tx.date_settle or loss_tx.date,
        'loss_account': loss_tx.account,
        'term': tacked_term})

def _us_lock_row(run: _USRun, entry: _GainRow, cost: Decimal) -> Optional[Dict[str, Any]]:
    """The row a filed year's lock holds for the sale or cover
    `entry` (`run.filed_rows`: the locks' dispositions; the account,
    symbol, date and units) at `cost`, to the cent; None when the
    lock has no such row at that cost."""
    for d in run.filed_rows:
        if (d.get('account') == entry.get('account')
                and d.get('symbol') == entry.get('symbol')
                and d.get('date') == entry.get('date')
                # (a row whose units or cost are no number is no row
                # of the lock: `engine_options` names it, TJ-3478)
                and isinstance(d.get('qty'), (int, float))
                and isinstance(d.get('cost'), (int, float))
                and abs(d['qty'] - entry['qty']) <= 1e-6
                and abs(M.of(d['cost']) - cost) <= _CENT):
            return d
    return None

def _us_would_wash(run: _USRun, cands: Sequence[_WashCand], qty: float, per_share: Decimal,
                   sym: str, loss_date: str) -> List[Dict[str, Any]]:
    """What a row's own wash test would match of `cands`, in order,
    and how much (nothing booked, no units taken): [{date, account,
    match_qty, amount, row}], `row` the sale or cover a replacement
    since sold became (None: one still held or open)."""
    out, left = [], qty
    for cand in cands:
        if left <= run.epsilon:
            break
        rep_, ch = cand if isinstance(cand, tuple) else (cand, None)
        if ch is not None and (ch.get('refused') or ch.get('gone')
                               or ch.get('entry') is None):
            continue
        uf = _us_rep_units_factor(run, sym, rep_['date'], loss_date) or 1.0
        mq = min(left, (ch['avail'] if ch is not None
                        else rep_['remaining_qty']) * uf)
        if mq <= run.epsilon:
            continue
        left -= mq
        out.append({'date': rep_['date'], 'account': rep_['tx'].account,
                    'match_qty': mq, 'amount': M.of(D(mq) * per_share),
                    # (sold or covered on that day; None: still held)
                    'sold': ch['entry'].get('date') if ch is not None else None,
                    'row': ch['entry'] if ch is not None else None})
    return out

def _us_queue_retest(run: _USRun, entry: _GainRow, row_tx: TaxTransaction, amt: Decimal) -> None:
    """`entry`, the row of `row_tx`, took a wash add of `amt`
    (`_us_add_to_row`, its last `wash_basis_added` record). The loss
    the add created, or the growth of a loss the row already had
    (tested then, and kept), is a loss of that row's own sale or
    cover: queued for its own §1091 test (`_us_run_retests`; backlog
    TJ-3428: it was never tested, so an add could turn a deferred
    loss into an allowed one)."""
    grown = min(amt, -entry['gain'])
    if grown > _US_MONEY_EPSILON and run.detect_wash_sales:
        run._us_retests.append((entry, row_tx, grown,
                                entry['wash_basis_added'][-1]))

def _us_say_retests(run: _USRun) -> None:
    """One line a run: how many rows a wash add turned into a loss
    were tested themselves, and the order they were tested in, an
    interim while backlog TJ-3444 is open (TJ-3453)."""
    by_year: Dict[str, List[Tuple[str, _GainRow, Dict[str, Any]]]] = {}
    for g in run.realized_gains:
        for a in g.get('wash_basis_added') or ():
            if 'retested' in a:     # (tested: not a row skipped or filed)
                d = a.get('root_loss_date') or a['loss_date']
                by_year.setdefault(d[:4], []).append((d, g, a))
    # One line for each year's losses (the pipeline prints a year's
    # own: TJ-3471), naming the securities and accounts tested.
    for _y, items in sorted(by_year.items()):
        adds = [a for _d, _g, a in items]
        where = ", ".join(sorted({f"{g['symbol']} in {g['account']}"
                                  for _d, g, _a in items}))
        hits = sorted({f"{g['symbol']} in {g['account']}"
                       for _d, g, a in items
                       if a['retested'] > _US_MONEY_EPSILON})
        hit = sum(1 for a in adds if a['retested'] > _US_MONEY_EPSILON)
        _us_note(run, max(d for d, _g, _a in items),
              f"note: {len(adds)} earlier sale(s) or cover(s) ({where}) "
              f"that a wash sale's disallowed loss turned into a loss were "
              f"tested for a wash sale of their own, {hit} with a part "
              f"disallowed"
              + (f" ({', '.join(hits)})" if 0 < hit < len(adds) else "")
              + ". "
              "Each was tested after the losses already matched, not in "
              "the order of the sales (Reg. §1.1091-1), so a replacement "
              "a later loss took first stayed with it (US-WASH-22).")

def _us_run_retests(run: _USRun, hold: Optional[Dict[str, Any]] = None) -> None:
    """Test each queued row's new loss (`_us_queue_retest`), in the
    order queued, once the whole sale or cover whose matches queued
    them is booked (every lot of it: backlog TJ-3462 — drained lot by
    lot, the figures followed how the sale's lots split). A test's own
    adds can queue more rows (a cascade): every one stems from a
    match that used up a sold replacement's units, which are used
    once, so the queue ends; a bound far above that stops a walk
    that does not, as an error naming the row. `hold`: one lot of
    a delivery to a short (`_us_deliver_to_short`, which drains once
    every lot is booked): not yet."""
    if hold:
        return
    done = 0
    while run._us_retests:
        done += 1
        entry, row_tx, grown, add = run._us_retests.pop(0)
        if done > 1000 + 10 * len(run.realized_gains):
            raise ValueError(
                f"{row_tx.symbol}: the wash-sale test of rows a basis add "
                f"turned into losses did not end, at the {row_tx.date} "
                f"row of {row_tx.account} (id {row_tx.id}; an engine bug: "
                f"please report it)")
        try:
            _us_retest_row(run, entry, row_tx, grown, add)
        except AmbiguousTransferDateError as ex:
            raise AmbiguousTransferDateError(
                f"{ex} (Met testing the {row_tx.date} "
                f"{'cover' if entry.get('direction') == 'SHORT' else 'sale'}"
                f" in {row_tx.account}, which a later wash sale's "
                f"disallowed loss turned into a loss.)") from ex

def _us_retest_row(run: _USRun, entry: _GainRow, row_tx: TaxTransaction, grown: Decimal,
                   add: Dict[str, Any]) -> None:
    """The §1091 test of `grown`, the loss a wash add made on `entry`
    (the earlier sale or cover of `row_tx`; `add`: that add's
    record): as a loss of that row's date and account, on the row's
    own side (a cover's against short sales, §1091(e), whichever
    side the add came from: a moved lot's cover takes a long-side
    add, backlog TJ-3460), share for share over the row's units,
    against what is left, now, of the replacements in its own 61
    days — still held or open (a later purchase or short sale not
    yet booked takes it when it is), or sold or covered since
    (US-WASH-22; never the shares of the loss that made this one) —
    in the order acquired (US-WASH-20). The order of the losses:
    this one is tested when the add is made, after every loss the
    walk has matched, the later loss whose disallowance made it
    included, so replacement units a later-dated loss already took
    stay with that loss (Reg. §1.1091-1(b), (c) would give the
    earlier loss the first claim; undoing matches already booked is
    not done: backlog TJ-3444). A filing position (ALLOWLOSS) is not
    read here. The disallowed part goes where any goes; the row
    shows it (`disallowed_amount`, its replacements marked
    `retest_of`), the add's record says how much (`retested`, 0 when
    none: a filed year's route moves it with the add,
    `place_retro_wash_adjustments`), and a wash record carries it.
    A row a filed year's lock already holds with the add and its
    loss allowed is not tested: left as filed (backlog TJ-3468).
    An add this test makes on another row names the loss that began
    the chain (`root_loss_*`), so a filed year is left as filed all
    the way down (backlog TJ-3461)."""
    short = entry.get('direction') == 'SHORT'
    root = {k: add.get(f'root_{k}') or add[k] for k in (
        'loss_date', 'loss_date_settle', 'loss_account')}
    sym = row_tx.symbol
    qty = float(entry.get('qty') or 0.0)
    if qty <= run.epsilon or entry.get('tainted') or _us_outside_1091(run, sym):
        return
    reps = (run.short_replacements if short else run.long_replacements).get(
        _us_rkey_on(run, sym, row_tx.date), [])
    cands = _us_with_sold_replacements(
        run, _us_find_replacements_in_window(run, reps, row_tx.date), reps,
        row_tx, short, retest=add['loss_id'])
    # A row filed with this add in its cost and its loss allowed (a
    # year closed by a version that did not test such a loss): left
    # exactly as filed, nothing booked anywhere; what the test would
    # disallow is kept on the add's record for the run's line
    # (`retest_as_filed`: `place_retro_wash_adjustments`; TJ-3468).
    lock = _us_lock_row(run, entry, entry['cost'])
    if lock is not None:
        would = _us_would_wash(run, cands, qty, D(grown) / D(qty), sym,
                               row_tx.date)
        # (`denied` read when it is a number: a damaged row's is not,
        # and the rows decide instead: TJ-3478)
        _den = lock.get('denied')
        if not (_den > 0.005 if isinstance(_den, (int, float)) else
                bool(would) and all(
                    w['row'] is not None and _us_lock_row(
                        run, w['row'], w['row']['cost'] + w['amount'])
                    for w in would)):
            add['retest_as_filed'] = [
                {k: v for k, v in w.items() if k != 'row'} for w in would]
            return
    add['retested'] = _ZERO     # (tested: counted, TJ-3471)
    try:
        _eff = (datetime.strptime(row_tx.date, '%Y-%m-%d') - timedelta(
            days=int(entry.get('days_held') or 0))).strftime('%Y-%m-%d')
    except ValueError:
        _eff = entry.get('acquired_date') or row_tx.date
    loss_lot = {'date': entry.get('acquired_date') or _eff,
                'effective_acq_date': _eff}
    per_share = D(grown) / D(qty)
    left = qty
    deferred = permanent = _ZERO
    wrs = []
    for cand in cands:
        if left <= run.epsilon:
            break
        if isinstance(cand, tuple):
            mq, wr = _us_match_sold_replacement(
                run, cand, left, per_share, row_tx, loss_lot, False, None,
                short=short, root=root)
            left -= mq
            if wr is not None:
                deferred += wr['proceeds_reduction' if short else 'basis_bump']
                wrs.append(wr)
            continue
        uf = _us_rep_units_factor(run, sym, cand['date'], row_tx.date)
        mq = min(left, cand['remaining_qty'] * uf)
        if mq <= run.epsilon:
            continue
        amt_d = D(mq) * per_share
        cand['remaining_qty'] -= mq / uf
        left -= mq
        wr = (_us_apply_held_short(run, cand, mq, amt_d) if short else
              _us_apply_held_long(run, cand, mq, uf, amt_d, row_tx.date,
                                  loss_lot))
        if wr['is_sheltered'] or wr['is_affiliated']:
            permanent += M.of(amt_d)
        else:
            deferred += M.of(amt_d)
        wrs.append(wr)
    total = deferred + permanent
    if total <= _US_MONEY_EPSILON:
        return
    for wr in wrs:
        wr['retest_of'] = add['loss_id']
    add['retested'] = total
    entry['disallowed_amount'] = entry['disallowed_amount'] + total
    entry['permanently_disallowed'] = entry['permanently_disallowed'] + permanent
    entry['gain'] = entry['gain'] + total
    entry['is_wash_sale'] = True
    entry['replacement_lot_ids'] = (list(entry.get('replacement_lot_ids') or [])
                                    + [wr['tx_id'] for wr in wrs])
    entry['wash_replacements'] = list(entry.get('wash_replacements') or []) + wrs
    run.wash_sale_records.append({
        'loss_tx_id': row_tx.id,
        'symbol': sym,
        'date': entry['date'],
        'date_settle': entry['date_settle'],
        'disallowed_amount': total,
        'permanently_disallowed': permanent,
        'qty': qty,
        'replacement_lot_ids': [wr['tx_id'] for wr in wrs],
        'direction': 'SHORT' if short else 'LONG',
        'retest_of': add['loss_id'],
    })
    _us_note_rules(run, row_tx.id, wrs, short=short, retest=True)


def _us_note_rules(run: _USRun, loss_id: RowId, reps: Sequence[_RepEntry], *,
                   short: bool, retest: bool = False) -> None:
    """The rule each record of a wash record names, noted on the run's
    `rules` (engine/rows RuleTable) as the record is made: the loss's
    disallowance (a sale's, §1091(a); a cover's, §1091(e); a retested
    row's, `retest`, US-WASH-22, a sale's and a cover's alike: the
    retest's order and exclusions are stated there alone) and each
    replacement's entry: an IRA's (permanent, Rev. Rul. 2008-5), a
    spouse's or controlled corporation's, one another account sold or
    covered before the loss, else the add to the lot's basis (§1091(d))
    or, for a short, the cut to its proceeds (US-WASH-27, §1091(d) by
    analogy). One closed before the loss is keyed apart
    (`closed_record`): a loss can take a purchase's units sold before it
    and the units still held. So is a retest of a row that holds its own
    disallowance (`retest_record`): one lot of a sale can be replaced by
    §1091(a) and another's loss be one a later add made (backlog
    TJ-4412); a row whose only disallowance is a retest keeps it under
    its id."""
    recs = run.rules.setdefault(loss_id, {})
    if retest:
        own = recs.get(loss_id) not in (None, R.US_WASH_22)
        recs[retest_record(loss_id) if own else loss_id] = R.US_WASH_22
    else:
        if recs.get(loss_id) == R.US_WASH_22:    # a retest noted first
            recs[retest_record(loss_id)] = R.US_WASH_22
        recs[loss_id] = R.US_WASH_05 if short else R.US_WASH_01
    for wr in reps:
        closed = 'sold_before_loss' in wr or 'covered_before_loss' in wr
        recs[closed_record(wr['tx_id']) if closed else wr['tx_id']] = (
            R.US_WASH_11 if wr['is_sheltered']
            else R.US_WASH_16 if wr['is_affiliated']
            else R.US_WASH_22 if closed
            else R.US_WASH_27 if short else R.US_WASH_09)

def _us_outside_1091(run: _USRun, sym: str) -> bool:
    hit = run._outside_seen.get(sym)
    if hit is None:
        hit = run._outside_seen[sym] = outside_1091(sym)
    return hit

def _us_flag_futures_loss(run: _USRun, loss_tx: TaxTransaction, loss_amt: Decimal,
                          cands: Sequence[_RepRecord]) -> None:
    rec = run._fut_flags.get(loss_tx.id)
    if rec is None:
        rec = run._fut_flags[loss_tx.id] = {
            'rule': 'futures_vs_loss',
            'loss_symbol': loss_tx.symbol,
            'loss_date': loss_tx.date,
            'loss_amount': _ZERO,
            'loss_id': loss_tx.id,
            'option_symbol': cands[0]['tx'].symbol,
            'option_acquired': min(c['date'] for c in cands),
            'option_qty': sum(c['remaining_qty'] for c in cands),
            'held_at_window_end': None,
            'statute': "IRS §1091 ('stock or securities')",
        }
    rec['loss_amount'] = M.present(rec['loss_amount'] + loss_amt, 2)

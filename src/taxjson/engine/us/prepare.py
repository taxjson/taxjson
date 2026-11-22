"""The US engine's preamble: the replacement scan over the book's events, the
payment-in-lieu plan, the dividend entries.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import functools
import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta

from decimal import Decimal

from taxjson.model import money as M
from taxjson.lib.readiness import UNSETTLED_COMPENSATION_MARK
from taxjson.lib.row_types import LOT_MOVE_TYPE, POOL_FREE_ACTIONS, is_stock_dividend
from taxjson.engine.assign import _day_gap
from taxjson.engine.consts import _ZERO, _HALF_CENT, _MINUS_HALF_CENT
from taxjson.engine.journal import _is_listing_journal
from taxjson.engine.rows import _currency_mismatch_advice, not_a_purchase
from taxjson.engine.us.lot_store import _lot_wash_held, _us_real_stamp
from taxjson.engine.us.terms import _LOT_EVENT_TYPES
from taxjson.engine.us.wash import _us_note, _us_rkey_on, _us_rep_units_factor

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId
    from taxjson.engine.splits import _RenameKey
    from taxjson.engine.us.lot_store import _IKey, _USInventory, _USLot
    from taxjson.engine.us.run import _USRun
    from taxjson.engine.us.trades import _GainRow
    from taxjson.engine.us.wash import _RepRecord


def _us_plan_paid_pil(ptx: TaxTransaction, paid: Decimal, *, _acct_of_row: Dict[RowId, str], _books_end: str, _paid_comp: Dict[str, Any], _rep_key: Callable[[str, str], str], _rep_units_factor: Callable[[str, str, str], float], _short_cover_chunks: List[Tuple[_GainRow, RowId, RowId]], epsilon: float, inventory_short: _USInventory, per_account_basis: bool) -> None:
    """Dividend compensation `paid` on a short (a negative
    payment in lieu, `ptx`), in the planning walk: it falls on
    every share of that account's short open over the ex-dividend
    date the row states (sold short before it, not covered before
    it: a short sold on or after the ex-date owes none; backlog
    TJ-2709), else on every share short on the pay date (`keyed`:
    'ex' or 'pay'), those covered later and those still open at
    the end of the books (backlog TJ-1024), by quantity in the pay
    date's units.
    The part on shares of a short sale closed on or before the
    45th day after it was made is no deduction but the cover's
    cost (§263(h)(1); US-INC-04, backlog TJ-0728): per_unit, for
    the booking walk. A still-open short whose 45th day is after
    the last row leaves its part unsettled (`open`; backlog
    TJ-1065). The rest stays paid compensation."""
    pd_ = ptx.date or ''
    cls = _rep_key(ptx.symbol, pd_)
    ex = stated = (getattr(ptx, 'ex_date', '') or '').strip()[:10]
    # An unreadable date, or one after the pay date, is no ex-date:
    # ignored, and said (`ex_ignored`: the date and why; TJ-3431).
    ex_ignored = None
    if stated and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', stated):
        ex, ex_ignored = '', (stated, 'not a YYYY-MM-DD date')
    elif stated > pd_:
        ex, ex_ignored = '', (stated, 'after the pay date')
    # The days an ex-date could change the split in: a short sold or
    # covered in about the 60 days before the pay date (`near`: the
    # no-ex-date note is said for these payments only, TJ-3432).
    try:
        near_lo = (datetime.strptime(pd_, '%Y-%m-%d')
                   - timedelta(days=60)).strftime('%Y-%m-%d')
    except ValueError:
        near_lo = pd_
    near = False

    def _on_pay(opened: str, closed: Optional[str] = None) -> bool:
        return opened <= pd_ and (closed is None or pd_ <= closed)

    def _owes(opened: str, closed: Optional[str] = None) -> bool:
        # Short over the ex-date; with none stated, on the pay date.
        if ex:
            return opened < ex and (closed is None or ex <= closed)
        return _on_pay(opened, closed)
    parts: List[Tuple[Optional[Tuple[_GainRow, RowId, RowId, float]], float, Optional[_USLot]]] = []          # (cover or None, units on pay date, lot)
    # A short on the pay date that owes none (sold on or after the
    # ex-date): the payment is still one paid while short, no income
    # reduction (`on_short`), though no cover's cost.
    short_on_pay = False
    for e, cover_id, lot_id in _short_cover_chunks:
        closed = e.get('trade_date') or e.get('date') or ''
        if (e.get('account') != ptx.account
                or _rep_key(e.get('symbol') or '', closed) != cls):
            continue
        short_on_pay = short_on_pay or _on_pay(
            e.get('acquired_date') or '', closed)
        near = (near or near_lo <= (e.get('acquired_date') or '') <= pd_
                or near_lo <= closed <= pd_)
        if not _owes(e.get('acquired_date') or '', closed):
            continue
        f = _rep_units_factor(e['symbol'], closed, pd_) or 1.0
        parts.append(((e, cover_id, lot_id, f),
                      float(e['qty']) * f, None))
    for k, lots in inventory_short.items():
        sym = k[1] if per_account_basis else k
        if _rep_key(sym, _books_end) != cls:  # type: ignore[arg-type]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)
            continue
        f = _rep_units_factor(sym, _books_end, pd_) or 1.0  # type: ignore[arg-type]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)
        for lot in lots:
            acct = (k[0] if per_account_basis
                    else _acct_of_row.get(lot.get('id')))
            if acct != ptx.account or lot['qty'] <= epsilon:
                continue
            short_on_pay = short_on_pay or _on_pay(lot.get('date') or '')
            near = near or near_lo <= (lot.get('date') or '') <= pd_
            if _owes(lot.get('date') or ''):
                parts.append((None, float(lot['qty']) * f, lot))
    total = sum(q for _c, q, _l in parts)
    rec: Dict[str, Any] = {'capitalized': _ZERO,
                           'on_short': total > epsilon or short_on_pay,
                           'open': [],
                           'keyed': 'ex' if ex else 'pay',
                           'near': near, 'ex_ignored': ex_ignored,
                           # a stated ex-date no short was open over
                           'ex_unmet': ex if (ex and total <= epsilon
                                              and short_on_pay) else None}
    _paid_comp['payments'][ptx.id] = rec
    if total <= epsilon:
        return
    units: Dict[Tuple[RowId, RowId], Decimal] = {}
    for cover, q, part_lot in parts:
        if cover is None:
            assert part_lot is not None, "a held short lot's part carries its lot (parts)"
            gap = _day_gap(part_lot.get('date') or '', _books_end)
            if gap is not None and gap < 45:
                rec['open'].append({
                    'date': part_lot['date'],
                    'covered_by': (
                        datetime.strptime(part_lot['date'], '%Y-%m-%d')
                        + timedelta(days=45)).strftime('%Y-%m-%d'),
                    'amount': paid * M.of(q) / M.of(total)})
            continue
        e, cover_id, lot_id, f = cover
        closed = e.get('trade_date') or e.get('date') or ''
        gap = _day_gap(e.get('acquired_date') or '', closed)
        if gap is None or gap > 45:
            continue
        units[(cover_id, lot_id)] = paid * M.of(f) / M.of(total)
        rec['capitalized'] += paid * M.of(q) / M.of(total)
    per_unit = _paid_comp['per_unit']
    for key, unit in units.items():
        per_unit[key] = per_unit.get(key, _ZERO) + unit

def _us_paid_pil_treatment(ptx: TaxTransaction, paid: Decimal, *, _books_end: str, _comp_payments: Dict[RowId, Dict[str, Any]], _note: Callable[[str, str], None]) -> Dict[str, Any]:
    """The plan's record of the payment `ptx`, said once."""
    rec = _comp_payments.get(ptx.id) or {}
    if rec.get('on_short') and rec.get('ex_ignored'):
        _note(ptx.date,
              f"warning: {ptx.symbol}: the dividend compensation paid on "
              f"{ptx.date} states the ex-dividend date "
              f"{rec['ex_ignored'][0]}, {rec['ex_ignored'][1]}: ignored, "
              f"and the payment is shared among the shares short on the "
              f"pay date; check the row (US-INC-04).")
    elif rec.get('on_short') and rec.get('keyed') == 'pay' and rec.get('near'):
        _note(ptx.date,
              f"note: {ptx.symbol}: the dividend compensation paid on "
              f"{ptx.date} states no ex-dividend date: it is shared "
              f"among the shares short on the pay date. A short sold "
              f"on or after the ex-date owes none (US-INC-04).")
    if rec.get('ex_unmet'):
        _note(ptx.date,
              f"warning: {ptx.symbol}: the dividend compensation paid on "
              f"{ptx.date} states the ex-dividend date {rec['ex_unmet']}, "
              f"but no short of {ptx.account} was open over it: kept as "
              f"compensation paid on a short, no cover's cost; check the "
              f"row (US-INC-04).")
    if rec.get('capitalized'):
        _note(ptx.date,
              f"note: {ptx.symbol}: {rec['capitalized']:,.2f} of the "
              f"dividend compensation paid on {ptx.date} is on a "
              f"short sale covered within 45 days: added to the "
              f"cover's cost (§263(h)), not deducted.")
    for o in rec.get('open') or ():
        _note(ptx.date,
              f"warning: {UNSETTLED_COMPENSATION_MARK}: {ptx.symbol}: "
              f"{o['amount']:,.2f} of the dividend compensation "
              f"paid on {ptx.date} is on a short sale of "
              f"{o['date']} still open at the last row "
              f"({_books_end}). Covered by {o['covered_by']} it is "
              f"the cover's cost (§263(h)), and this year's income "
              f"changes; held longer it stays paid compensation. "
              f"Add the later rows before closing the year.")
    return rec

def _us_open_qty(g: _GainRow) -> float:
    # The loss shares no share purchase replaced: a loss share
    # is matched once (US-WASH-20), so a call can replace only
    # the part still allowed (backlog TJ-0467).
    raw = M.of(g.get('raw_gain', g['gain']) or 0.0)
    den = M.of(g.get('disallowed_amount') or 0.0)
    q = abs(float(g.get('qty') or 0.0))
    if den <= _HALF_CENT:
        return q
    # A ratio of two money figures scales a quantity: quantities stay float.
    return q * max(0.0, 1.0 - min(1.0, float(den / -raw)))

# Inventory report includes both unsold long lots and unclosed short
# lots (short qty reported as a negative number for clarity).
# Currency comes from the symbol_currency map built at pass start;
# falls back to '' if a symbol somehow has no currency on any tx
# (defensive — taxjson-export tolerates the empty case). One
# builder for the final lots and the year-end ones (US-RPT-13).
def _us_lot_rows(inv_long: _USInventory, inv_short: _USInventory, *, epsilon: float,
                 per_account_basis: bool) -> List[Dict[str, Any]]:
    """Each lot held, in FIFO order per account and symbol:
    {account, symbol, date (acquired), holding_start (§1223
    tacking moved it back), qty (negative: short), basis (a
    short's: its proceeds, negative), deferred (§1091 adds in
    it)}."""
    rows = []
    for inv, sign in ((inv_long, 1), (inv_short, -1)):
        for s, lots in inv.items():
            _acct = s[0] if per_account_basis else ''
            s = s[1] if per_account_basis else s
            for l in lots:
                if l['qty'] <= epsilon:
                    continue
                money = (l['cost_basis'] if sign > 0
                         else -l['proceeds'])
                rows.append({
                    'account': _acct, 'symbol': s,
                    'date': l['date'],
                    'holding_start': (
                        l.get('effective_acq_date') or l['date']
                        if sign > 0 else
                        l.get('effective_open_date') or l['date']),
                    'qty': float(sign * l['qty']),
                    'basis': M.of(money),
                    'deferred': M.present(M.of(
                        _lot_wash_held(l) if sign > 0 else l.get(
                            'wash_deferred', Decimal(0))), 4)})
    return rows

def _us_inventory_rows(inv_long: _USInventory, inv_short: _USInventory, *, epsilon: float, per_account_basis: bool, symbol_currency: Dict[str, str]) -> List[Dict[str, Any]]:
    rows = []
    for s, lots in inv_long.items():
        _acct = s[0] if per_account_basis else ''
        s = s[1] if per_account_basis else s
        tot_qty = sum(l['qty'] for l in lots)
        if tot_qty > epsilon:
            rows.append({
                'symbol': s, 'qty': tot_qty,
                **({'account': _acct} if _acct else {}),
                'total_cost': M.of(sum(l['cost_basis']
                                        for l in lots)),
                'currency': symbol_currency.get(s, ''),  # type: ignore[arg-type]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)
                # Earliest acquisition date among the REMAINING
                # lots. After a FIFO close, the consumed lots
                # are gone, so `min` correctly returns the new
                # position-start when the original lots have
                # all been closed and replaced by later buys.
                'position_start_date': min(l['date']
                                           for l in lots),
                # The earliest remaining lot's holding period
                # start: a wash-sale replacement's is moved back
                # by the loss shares' period (§1223(3),
                # US-WASH-10), the date the sale's term is
                # decided from (harvest's LT_IN read the buy
                # date: backlog TJ-0138).
                'holding_start_date': min(
                    l.get('effective_acq_date') or l['date']
                    for l in lots),
                # Most recent surviving lot — the wash-window
                # signal.
                'last_acq_date': max(l['date'] for l in lots),
                # §1091 basis bumps still embedded in surviving
                # lots.
                'deferred_wash': M.present(M.of(sum(
                    _lot_wash_held(l) for l in lots)), 4),
                # A lot whose basis these books do not have
                # (backlog TJ-0598): total_cost leaves it out.
                **({'unknown_cost': True}
                   if any(l.get('tainted') for l in lots
                          if l['qty'] > epsilon) else {}),
            })
    for s, lots in inv_short.items():
        _acct = s[0] if per_account_basis else ''
        s = s[1] if per_account_basis else s
        tot_qty = sum(l['qty'] for l in lots)
        if tot_qty > epsilon:
            rows.append({
                'symbol': s,
                'qty': -tot_qty,    # signed: negative = short
                **({'account': _acct} if _acct else {}),
                # NEGATIVE: opening proceeds credited — the
                # repo-wide short-inventory convention
                # (cost_per_share = total_cost/qty comes out
                # positive; see test_holdings_toml /
                # test_taxjson_export_report).
                'total_cost': M.of(-sum(l['proceeds']
                                         for l in lots)),
                'currency': symbol_currency.get(s, ''),  # type: ignore[arg-type]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)
                'position_start_date': min(l['date']
                                           for l in lots),
                'last_acq_date': max(l['date'] for l in lots),
                'deferred_wash': M.present(M.of(sum(
                    l.get('wash_deferred', Decimal(0))
                    for l in lots)), 4),
                # A lot whose basis these books do not have
                # (backlog TJ-0598): total_cost leaves it out.
                **({'unknown_cost': True}
                   if any(l.get('tainted') for l in lots
                          if l['qty'] > epsilon) else {}),
            })
    return rows


def _us_scan_events(*, _nkey: Callable[[str, str], _IKey], _rep_key: Callable[[str, str], str], _split_covers: Callable[[TaxTransaction, Set[_RenameKey]], Optional[Callable[[str], bool]]], affiliated_ids: Set[RowId], all_events: List[TaxTransaction], epsilon: float,
                       non_capital: Callable[[str, str], bool], per_account_basis: bool, sheltered_ids: Set[RowId]) -> Tuple[Dict[str, List[_RepRecord]], Dict[str, List[_RepRecord]], List[Tuple[TaxTransaction, str, Optional[str], float]]]:
    """The replacement scan over the book's events in order (the pre-pass of the US engine's
    preamble): every buy, cover, split, option and corporate row of the taxable, sheltered
    and affiliated books; returns the long and short replacement candidates and the
    context books' stock dividends to apply, keeping the net and the other books' running
    quantities and the groups a split covers as it goes."""
    # === PRE-PASS: classify each event and build replacement indexes. ===
    # The "opening portion" of each transaction is what's eligible to be
    # a wash-sale replacement. A flip transaction (closes one side, opens
    # the other) contributes only its opening portion.
    long_replacements: Dict[str, List[_RepRecord]] = {}
    short_replacements: Dict[str, List[_RepRecord]] = {}
    # A context (sheltered/affiliated) book's stock dividends: (row,
    # class, entitlement date, ratio) — the main pass grows the
    # capacity of that account's replacement records by it (TJ-0492).
    _other_stkdivs: List[Tuple[TaxTransaction, str, Optional[str], float]] = []
    net_qty_state: Dict[_IKey, float] = {}

    # Context (sheltered/affiliated) books' running balances, keyed
    # (account, symbol) — partitions an other-scope SELL into its
    # long-close vs short-open portions and an other-scope BUY into
    # its short-cover vs long-open portions, mirroring the taxable
    # partition (Rev. Rul. 2008-5: a sheltered ACQUISITION is
    # replacement-eligible; a buy-to-close acquires nothing).
    other_qty_state: Dict[Tuple[str, str], float] = {}

    _pre_split_groups: Set[_RenameKey] = set()      # _split_covers, the pre-pass
    for ev in all_events:
        if (ev.action == 'TRANSFER'
                and (ev.id in sheltered_ids or ev.id in affiliated_ids)
                and abs(ev.quantity or 0.0) >= epsilon):
            # A netted own-account move (rrsp -> rrsp2) kept as a
            # balance-only row: never an acquisition, but it moves
            # the per-account balance the oversell check reads
            # (audit S070-11 — the receiving IRA's sale was called
            # "beyond its recorded balance" after the tool's own
            # netting removed the move).
            _ok = (ev.account, ev.symbol)
            other_qty_state[_ok] = (other_qty_state.get(_ok, 0.0)
                                    + ev.quantity)
            continue
        if ev.action == 'TRANSFER' and ev.type == LOT_MOVE_TYPE:
            # An own-account custody move: never an acquisition, but
            # the per-account position moves with it (US-BASIS-05).
            if per_account_basis and abs(ev.quantity or 0.0) >= epsilon:
                _nk = _nkey(ev.account, ev.symbol)
                net_qty_state[_nk] = (net_qty_state.get(_nk, 0.0)
                                      + ev.quantity)
            continue
        if _is_listing_journal(ev):
            # A native book's journal between two listings: each
            # leg is its listing's position change (TJ-0361).
            if (ev.id not in sheltered_ids
                    and ev.id not in affiliated_ids
                    and abs(ev.quantity or 0.0) >= epsilon):
                _nk = _nkey(ev.account, ev.symbol)
                net_qty_state[_nk] = (net_qty_state.get(_nk, 0.0)
                                      + ev.quantity)
            continue
        if non_capital(ev.action, ev.type):
            continue
        sym = ev.symbol
        is_sheltered_ev = ev.id in sheltered_ids
        is_affiliated_ev = ev.id in affiliated_ids
        is_other_scope = is_sheltered_ev or is_affiliated_ev

        # SPLIT and OPENING_BALANCE aren't real acquisitions and
        # never contribute to the §1091 replacement lists, but
        # they DO change the running taxable position the main
        # pass tracks in inventory_long/short. Mirror those
        # position changes in net_qty_state so a later BUYSELL is
        # classified against the correct running balance — without
        # this, a SELL drawing from a missing-history OPENING_BALANCE long
        # registers as a fake short-replacement (because the
        # pre-pass thought net_qty was 0), and a post-split SELL
        # mis-counts open/close against pre-split units.
        if ev.action == 'SPLIT':
            # (Schedule recording moved to split_timeline above.)
            # A split is a property of the SECURITY, and the entry
            # dedupe keeps ONE row per corporate event in an
            # arbitrary book/account — so scale the taxable running
            # position AND every context book's balance regardless
            # of which copy survived (previously a sheltered-book
            # survivor left the taxable balance unscaled and vice
            # versa).
            ratio = ev.quantity
            # A multi-ratio merger's row reaches its own account's
            # balances only (US-CORP-14; _split_covers).
            _covers = _split_covers(ev, _pre_split_groups)
            if ratio:
                if per_account_basis:
                    for _nk in list(net_qty_state):
                        if _nk[1] == sym and (_covers is None
                                              or _covers(_nk[0])):
                            net_qty_state[_nk] *= ratio
                elif sym in net_qty_state:
                    net_qty_state[sym] *= ratio
                for _ok in list(other_qty_state):
                    if _ok[1] == sym and (_covers is None
                                          or _covers(_ok[0])):
                        other_qty_state[_ok] *= ratio
            # Handle ticker rename (e.g. merger). Migration ensures
            # future BUYSELLs on the new ticker correctly partition
            # into open/close portions against the migrated balance.
            target_symbol = (ev.symbol_new or '').strip()
            if target_symbol and target_symbol != sym:
                if per_account_basis:
                    for _nk in list(net_qty_state):
                        if _nk[1] == sym and (_covers is None
                                              or _covers(_nk[0])):
                            _tk = (_nk[0], target_symbol)
                            net_qty_state[_tk] = (
                                net_qty_state.get(_tk, 0.0)
                                + net_qty_state.pop(_nk))
                else:
                    net_qty_state[target_symbol] = net_qty_state.get(target_symbol, 0.0) + net_qty_state.get(sym, 0.0)
                    # pop (not `del`): a rename-SPLIT with a falsy
                    # ratio skips the scaling above, so `sym` may
                    # never have been added to net_qty_state — `del`
                    # would KeyError.
                    net_qty_state.pop(sym, None)
                for _ok in list(other_qty_state):
                    if _ok[1] == sym and (_covers is None
                                          or _covers(_ok[0])):
                        _nk = (_ok[0], target_symbol)
                        other_qty_state[_nk] = (
                            other_qty_state.get(_nk, 0.0)
                            + other_qty_state.pop(_ok))
            continue
        if ev.action == 'OPENING_BALANCE':
            if abs(ev.quantity) >= epsilon:
                if is_other_scope:
                    _ok = (ev.account, sym)
                    other_qty_state[_ok] = (  # cov: a2-1596-us-other-opening
                        other_qty_state.get(_ok, 0.0) + ev.quantity)
                else:
                    _nk = _nkey(ev.account, sym)
                    net_qty_state[_nk] = net_qty_state.get(_nk, 0.0) + ev.quantity
            continue
        if ev.action not in ('BUYSELL', 'ASSIGN'):
            continue
        if abs(ev.quantity) < epsilon:
            continue

        prev = net_qty_state.get(_nkey(ev.account, sym), 0.0)

        if is_stock_dividend(ev) and ev.quantity > 0:
            # A nontaxable stock dividend (§305(a)) is not an
            # acquisition "by purchase": it never replaces a loss
            # (§1091), it only grows the position (partition
            # INPUTS-01). With nothing held the main pass books it as
            # a $0 purchase (and warns) — shares sold before the pay
            # date, or missing history — but it is still not a
            # purchase for §1091 (US-STKDIV-01, audit A2-0205: a
            # dividend posted after a loss sale washed 5% of it).
            if not is_other_scope:
                net_qty_state[_nkey(ev.account, sym)] = \
                    prev + ev.quantity
            else:
                _ok = (ev.account, sym)
                _oprev = other_qty_state.get(_ok, 0.0)
                other_qty_state[_ok] = (_oprev  # cov: a2-1596-us-other-stock-div
                                        + ev.quantity)
                # The shares it is paid on: the balance less this
                # account's purchases on or after the entitlement
                # date (TJ-0492; a context book has no lots).
                _ent_d = (ev.ex_date or ev.record_date
                          or '').strip() or None
                _late = sum(
                    r['open_qty'] for r in long_replacements.get(
                        _rep_key(sym, ev.date), [])
                    if r['tx'].account == ev.account
                    and _ent_d is not None and r['date'] >= _ent_d)
                _base = _oprev - _late
                if _base > epsilon:
                    _other_stkdivs.append(
                        (ev, _rep_key(sym, ev.date), _ent_d,
                         (_base + ev.quantity) / _base))
            continue

        # An opening balance (a positions report's lots) is not a
        # purchase in any window either (US-OPEN-01).
        if (ev.quantity > 0 and ev.action == 'BUYSELL'
                and ((ev.type or '') in _LOT_EVENT_TYPES
                     or not_a_purchase(ev))):
            # Shares received in a §355 spin-off or a §356 exchange
            # are not acquired "by purchase or by an exchange on which
            # the entire amount of gain or loss was recognized"
            # (§1091(a)): never a replacement (US-CORP-05/-07).
            if not is_other_scope:
                net_qty_state[_nkey(ev.account, sym)] = \
                    prev + ev.quantity
            else:
                _ok = (ev.account, sym)
                other_qty_state[_ok] = (other_qty_state.get(_ok, 0.0)
                                        + ev.quantity)
            continue

        if ev.quantity > 0:
            # Taxable buys close any taxable shorts first; leftover
            # opens long. Sheltered/affiliated buys live in a
            # separate book — their full qty is §1091 replacement-
            # eligible (Rev. Rul. 2008-5 for sheltered; §1091(a)
            # spouse/controlled-entity for affiliated), and they
            # don't move the taxable running position.
            if is_other_scope:
                # ... but only the portion that OPENS or extends a
                # long in its own book: an IRA buy-to-close of a
                # written covered call, or a spouse's buy-to-cover,
                # acquires nothing (audit S070-10 — the full
                # quantity permanently denied taxable losses).
                _oprev = other_qty_state.get((ev.account, sym), 0.0)
                open_qty = ev.quantity - min(ev.quantity,
                                             max(0.0, -_oprev))
            else:
                open_qty = ev.quantity - min(ev.quantity, max(0.0, -prev))
            if open_qty > epsilon:
                long_replacements.setdefault(_rep_key(sym, ev.date), []).append({
                    'tx': ev,
                    'date': ev.date,
                    'open_qty': open_qty,
                    'remaining_qty': open_qty,
                    'is_sheltered': is_sheltered_ev,
                    'is_affiliated': is_affiliated_ev,
                    'lot_ref': None,
                    'pending_basis_add': Decimal(0),
                    'effective_acq_date': ev.date,
                })
        else:
            if is_other_scope:
                # A sheltered/affiliated SELL is a short-side §1091
                # replacement only for the portion that actually
                # OPENS a short in its own book. Registering the
                # full quantity meant a routine registered-account
                # sale of a long (registered accounts cannot even
                # open shorts) permanently destroyed a taxable
                # short-cover loss — inconsistent with both the
                # taxable partition below and the Canada engine.
                _oprev = other_qty_state.get((ev.account, sym), 0.0)
                open_qty = abs(ev.quantity) - min(abs(ev.quantity),
                                                  max(0.0, _oprev))
                if open_qty > epsilon and is_sheltered_ev:
                    # A registered account cannot open a short: a
                    # sheltered sale exceeding its recorded balance
                    # is missing acquisition history (an in-kind
                    # move between two IRAs, an incomplete export)
                    # — registering it as a §1091 short replacement
                    # PERMANENTLY denied real taxable losses. Warn
                    # and skip; affiliated (spouse) accounts keep
                    # the partition result, since they genuinely
                    # can short.
                    emit_line(f"warning: sheltered account "
                          f"{ev.account!r} sells {open_qty:g} "
                          f"{sym} beyond its recorded balance on "
                          f"{ev.date} — missing acquisition "
                          f"history; NOT treated as a §1091 short "
                          f"replacement. Add the missing sheltered "
                          f"buy/transfer rows for exact wash "
                          f"matching.")
                    open_qty = 0.0
            else:
                open_qty = abs(ev.quantity) - min(abs(ev.quantity), max(0.0, prev))
            if open_qty > epsilon:
                short_replacements.setdefault(_rep_key(sym, ev.date), []).append({
                    'tx': ev,
                    'date': ev.date,
                    'open_qty': open_qty,
                    'remaining_qty': open_qty,
                    'is_sheltered': is_sheltered_ev,
                    'is_affiliated': is_affiliated_ev,
                    'short_lot_ref': None,
                    'pending_proceeds_reduction': Decimal(0),
                })
        if not is_other_scope:
            net_qty_state[_nkey(ev.account, sym)] = prev + ev.quantity
        else:
            _ok = (ev.account, sym)
            other_qty_state[_ok] = (other_qty_state.get(_ok, 0.0)
                                    + ev.quantity)
    return (long_replacements, short_replacements, _other_stkdivs)


def _us_symbol_currencies(*, affiliated_transactions: Optional[List[TaxTransaction]], sheltered_transactions: Optional[List[TaxTransaction]], transactions: List[TaxTransaction]) -> Dict[str, str]:
    """Each symbol's currency across the full input set (taxable, sheltered and affiliated):
    the inventory reports only the symbols whose taxable lots are still open, and a
    symbol-keyed lookup attaches a currency at emission time without re-walking lots
    (the Canadian engine's per-pool currency tracking and mismatch guard, mirrored). A
    mismatch between the books is warned once; a SPLIT carries no money (A2-0010)."""
    symbol_currency: Dict[str, str] = {}
    _main_ids = {id(t) for t in transactions}
    for _tx in (transactions + (sheltered_transactions or []) + (affiliated_transactions or [])):
        # SPLIT carries no money (a .tt SPLIT is stamped CAD whatever
        # the listing — A2-0010); its currency says nothing.
        if not _tx.currency or _tx.action in POOL_FREE_ACTIONS:
            continue
        existing = symbol_currency.get(_tx.symbol)
        if existing and existing != _tx.currency:
            if id(_tx) not in _main_ids:
                # CONTEXT books (sheltered/affiliated) exist for
                # wash matching only — a native-currency sheltered
                # file must not kill the whole run (the Canada
                # engine gates its check to the taxable scope the
                # same way). Warn and keep the main book's label.
                emit_line(f"warning: {_tx.symbol}: context book "
                      f"transaction {_tx.id} on {_tx.date} is in "
                      f"{_tx.currency!r} but the main book uses "
                      f"{existing!r} — context amounts assumed "
                      f"comparable; convert the context file for "
                      f"exact wash amounts.")
                continue
            raise ValueError(
                f"Currency mismatch for {_tx.symbol}: previously seen as {existing!r} "
                f"but {_tx.action} {_tx.id} on {_tx.date} is in {_tx.currency!r}. "
                + _currency_mismatch_advice(_tx, existing))
        if not existing:
            symbol_currency[_tx.symbol] = _tx.currency
    return symbol_currency


def _us_disposition_groups(*, epsilon: float, non_capital: Callable[[str, str], bool], taxable_sorted: List[TaxTransaction]) -> Tuple[Set[RowId], Dict[RowId, float]]:
    """One disposition per sale (or cover) row, or per the same-second fills of one order:
    consecutive BUYSELL rows of one account and symbol, same direction, same date and a
    real clock time (a date-only or midnight stamp is no evidence of one order). Lots
    drawn by one disposition are never replacements for each other's losses (tax-logic
    US-WASH-17; audit A2-0001/A2-0017/A2-0553: a 1-share old lot sold with 100 recent
    shares washed its loss into the very shares being sold, split them, and cascaded one
    share at a time, 100,001 Form 8949 W rows). Returns the lead rows' group quantities
    and the non-lead fill ids, over the taxable rows in order."""
    _disp_total: Dict[RowId, float] = {}   # lead row id -> group qty
    _disp_member: Set[RowId] = set()            # non-lead fill ids
    _disp_prev: Dict[Tuple[str, str], Tuple[TaxTransaction, RowId]] = {}

    for _t in taxable_sorted:
        if non_capital(_t.action, _t.type):
            continue
        _k = (_t.account, _t.symbol)
        if _t.action != 'BUYSELL' or abs(_t.quantity or 0) < epsilon:
            _disp_prev.pop(_k, None)
            continue
        _p = _disp_prev.get(_k)
        if (_p is not None and _us_real_stamp(_t.time)
                and _p[0].date == _t.date and _p[0].time == _t.time
                and (_p[0].quantity > 0) == (_t.quantity > 0)):
            _disp_total[_p[1]] += abs(_t.quantity)
            _disp_member.add(_t.id)
            _disp_prev[_k] = (_t, _p[1])
        else:
            _disp_total[_t.id] = abs(_t.quantity)
            _disp_prev[_k] = (_t, _t.id)
    return (_disp_member, _disp_total)


def _us_pil_row(tx: TaxTransaction) -> bool:
    """A payment-in-lieu row (US-INC-04): the action, or the type a parser stamped."""
    return tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu'


def _us_paid_in_lieu(tx: TaxTransaction) -> Optional[Decimal]:
    """What a payment-in-lieu row paid on a short (its gross amount, else its net; a
    payment is negative, under a half-cent tolerance), or None when the row is not one
    or paid nothing."""
    if not _us_pil_row(tx):
        return None
    amount = M.of((tx.gross_amount or tx.net_amount) or 0.0)
    return amount if amount < _MINUS_HALF_CENT else None


def _us_plan_payments(*, run: _USRun) -> None:
    """The compensation plan's building run (step 4q): for each payment in lieu paid on a
    short (a negative amount), where it fell among the short-cover chunks the walk kept
    (`_us_plan_paid_pil` writes the plan, `run._paid_comp`). The report's dividend and
    payment-in-lieu entries are `_us_dividend_entries`', on the booking run, which reads
    the plan; the planner's silent run calls this and nothing of the report."""
    _books_end = max((t.date or '' for t in run.transactions), default='')
    _acct_of_row = {t.id: t.account for t in run.transactions}
    _rep_key = functools.partial(_us_rkey_on, run)
    _rep_units_factor = functools.partial(_us_rep_units_factor, run)
    _plan_paid_pil = functools.partial(_us_plan_paid_pil, _acct_of_row=_acct_of_row, _books_end=_books_end, _paid_comp=run._paid_comp, _rep_key=_rep_key, _rep_units_factor=_rep_units_factor, _short_cover_chunks=run._short_cover_chunks, epsilon=run.epsilon, inventory_short=run.inventory_short, per_account_basis=run.per_account_basis)
    for tx in run.transactions:
        paid = _us_paid_in_lieu(tx)
        if paid is not None:
            _plan_paid_pil(tx, -paid)


def _us_dividend_entries(*, run: _USRun) -> None:
    """The report's dividend and payment-in-lieu entries (the gross dividend, 1099-DIV
    box 1a; a payment in lieu its own entry, ordinary income kept out of the dividend
    total), appended to the run's realized gains in row order; the compensation paid on
    a short is capitalised into the cover's cost where the plan says so (US-INC-04,
    s.263(h)) and the plan's notes said once; the plan is `_us_plan_payments`' alone."""
    _books_end = max((t.date or '' for t in run.transactions), default='')
    _note = functools.partial(_us_note, run)       # the plan's notes, on this run
    _paid_pil_treatment = functools.partial(_us_paid_pil_treatment, _books_end=_books_end,
                                             _comp_payments=run._comp_payments, _note=_note)
    # Payments on a short with no ex-date, and no short sold or covered
    # near them: one line a run (TJ-3432), said after the rows.
    _no_ex: List[str] = []

    # Dividends — report the gross dividend (1099-DIV box 1a). Withheld
    # foreign tax flows via separate TAX records into the foreign tax
    # credit, not the dividend income line.
    for tx in run.transactions:
        # PIL gets its own entry so sum-gains can show it in a dedicated
        # column. Kept out of the eligible-dividend total — IRS treats
        # PIL as a non-qualified substitute payment (ordinary income).
        if _us_pil_row(tx):
            pil_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
            paid = _us_paid_in_lieu(tx)
            _ppil = _paid_pil_treatment(tx, -paid) if paid is not None else {}
            _cap = _ppil.get('capitalized') or _ZERO
            if (_ppil.get('on_short') and _ppil.get('keyed') == 'pay'
                    and not _ppil.get('near') and not _ppil.get('ex_ignored')):
                _no_ex.append(tx.date)
            run.realized_gains.append({
                'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                'symbol': tx.symbol, 'qty': tx.quantity,
                'currency': tx.currency, 'gain': _ZERO, 'cost': _ZERO, 'proceeds': _ZERO,
                'pil': (M.of(pil_amount) + _cap if _cap
                        else M.kept(pil_amount)),
                'account': tx.account,
                'id': tx.id, 'action': 'DIVIDEND_IN_LIEU',
                **({'capitalized_263h': M.present(_cap, 4)} if _cap
                   else {}),
                **({'paid_on_short': True} if _ppil.get('on_short')
                   else {}),
            })
            continue

        if tx.action == 'DIVIDEND':
            # Convention: parsers set gross_amount as the pre-withholding
            # dividend (e.g. RBC computes net/0.85 when description shows
            # NON-RES TAX WITHHELD); they leave it at the dataclass
            # default 0.0 when only net is known. We use gross if it's
            # truthy (positive — actual dividends are always > 0), else
            # net. Treating 0 and unset identically is intentional here:
            # a 0-gross "dividend" is contradictory data — no real
            # parser emits gross=0 + net>0 for a real dividend, and a
            # return-of-capital event should use action='ADJUST', not
            # DIVIDEND. If you ever need to distinguish "explicit zero"
            # from "missing", introduce a sentinel field on
            # TaxTransaction rather than changing this check.
            div_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
            run.realized_gains.append({
                'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                'symbol': tx.symbol, 'qty': tx.quantity,
                'currency': tx.currency, 'gain': _ZERO, 'cost': _ZERO, 'proceeds': _ZERO,
                'dividend': M.kept(div_amount), 'account': tx.account,
                'id': tx.id, 'action': 'DIVIDEND',
            })
    if _no_ex:
        _note(max(_no_ex),
              f"note: {len(_no_ex)} payment(s) of dividend compensation on a "
              f"short state no ex-dividend date: each is shared among the "
              f"shares short on its pay date (US-INC-04).")

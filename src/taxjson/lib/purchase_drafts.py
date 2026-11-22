"""Purchase drafts for missing history (`tjs find-missing-history
--write-purchases`): the `.tt` purchase lines the broker's own cost
evidence supports (draft_purchases: an IB sale coded C that sells
units the data never bought, with IB's basis; a transfer-in that
states the delivering broker's book value), and the sales and
transfer-ins it leaves undrafted, with the reason. Out of
lib/missing_history, which lib/transfer_in reads, while the drafts
read transfer_in's arrivals (step 8 of the architecture plan: lib's
import cycle)."""

from __future__ import annotations

import re
from typing import (Any, Dict, Iterable, List, Optional, Sequence, Set,
                    Tuple)

from taxjson.model import evidence
from taxjson.model import money as M
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corporate_timeline import normalize_symbol_new
from taxjson.lib.futures_prefixes import FUTURES_PREFIXES
from taxjson.lib.option_symbols import is_option_symbol
# The `type` of a books row that acquires transferred-in shares at the
# broker's stated book value on their arrival date: such a transfer is
# already costed by the run, so it is not drafted again.
from taxjson.lib.row_types import TRANSFER_BOOK_VALUE_TYPE
from taxjson.lib.unbacked_close import OrderStarts
from taxjson.lib.missing_history import (DraftGap, FutureBrokerBooks,
                                         PurchaseDraft, _basis_date,
                                         _book_journals, _close_unbacked,
                                         _derivative_symbol,
                                         _drop_duplicate_splits, _mask_id,
                                         _spread_global_splits, _walk_key,
                                         broker_short_marker,
                                         is_registered_account)


# A transfer-in row that states the delivering dealer's book value of
# what was delivered: Questrade's "... TRANSFER BOOK VALUE <amount>"
# (lib/brokerages/questrade.py reads it as the row's net amount), RBC's
# "... ACCOUNT TRANSFER BOOK VALUE <amount> FROM ACCOUNT ...".
_TRANSFER_BOOK_VALUE_RE = re.compile(
    r'TRANSFER\s+BOOK\s+VALUE\s+([\d,]+(?:\.\d+)?)', re.IGNORECASE)


def parse_broker_basis(text: str) -> Optional[Tuple[float, str]]:
    """'1,234.56 USD' -> (1234.56, 'USD'); None when it does not read."""
    m = re.match(r'^\s*([\d,]+(?:\.\d+)?)\s+([A-Za-z]{3})\s*$',
                 str(text or ''))
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '')), m.group(2).upper()
    except ValueError:
        return None


def broker_lot_floats(text: str) -> Optional[List[Tuple[str, float, float]]]:
    """broker_lots evidence -> [(open date, qty, cost)] as floats; None
    when there is none or a lot did not read (the export's lots are then
    not used). model/evidence `parse_broker_lots` reads it, as Decimals."""
    lots = evidence.parse_broker_lots(text)
    if lots is None:
        return None
    return [(lot.open_date, float(lot.quantity), float(lot.cost)) for lot in lots]


def _drafting_caveats(country: str, currency: str, *, lots: bool,
                      is_transfer: bool = False,
                      date_basis: str = 'settle') -> List[str]:
    """The per-line notes that differ by country (CA-ACB-15 /
    US-BASIS-08)."""
    from taxjson.lib.country import home_currency, is_canada
    out: List[str] = []
    if is_canada(country):
        if is_transfer:
            out.append("check: the delivering broker's book value is its "
                       "average cost, which is your ACB only if this was "
                       "your only position in it (s.47 averages every "
                       "identical share in all your taxable accounts).")
        else:
            out.append("check: IB's Basis is the cost of the lots IB "
                       "closed (FIFO), not your ACB: the ACB averages "
                       "every identical share in all your taxable "
                       "accounts (s.47). If you held more of it bought "
                       "before the data (still held, or at another "
                       "broker), add those purchases too.")
        if currency and currency != home_currency(country):
            # A .tt line's one date is its settlement date under
            # tax_date settle (settings.md, .tt files; backlog TJ-0419).
            out.append(f"check: the run converts this {currency} cost "
                       f"to CAD at the Bank of Canada rate of the line's "
                       f"date, so the date must be the real purchase "
                       f"date" + (", its settlement date (tax_date "
                                  "settle: a .tt line's one date)."
                                  if date_basis == 'settle' else "."))
        out.append("check: a purchase within 30 days (settlement dates) "
                   "of a loss sale of the same stock is a replacement "
                   "for the superficial-loss rule.")
    else:
        if lots:
            out.append("check: IB's lot date and cost are this lot's basis "
                       "and the start of its holding period; if IB "
                       "adjusted the lot for a wash sale, enter the "
                       "original cost (taxjson applies §1091 itself).")
        elif is_transfer:
            out.append("check: the book value may sum several lots: write "
                       "one line per lot with its own purchase date and "
                       "cost — the date decides short- or long-term.")
        else:
            out.append("check: IB's Basis may sum several lots (FIFO): "
                       "write one line per lot with its own purchase date "
                       "and cost — the date decides short- or long-term.")
    return out


def _lot_line_date(trade_day: str, symbol: str, currency: str,
                   date_basis: str) -> str:
    """The .tt date of a lot IB lists: its Closed Lots date is the lot's
    TRADE date, and a .tt line has one date, its settlement date under
    tax_date settle (settings.md, .tt files) — the day the run converts
    the cost on and dates the superficial-loss window from, as it does
    for IB's own trade row (the listing market's cycle and calendar,
    lib/dates.settlement_date; backlog TJ-0419). Under trade: the lot's
    date as IB gives it."""
    if date_basis != 'settle':
        return trade_day
    from taxjson.lib.dates import settlement_calendar, settlement_date
    return settlement_date(trade_day,
                           settlement_calendar(symbol, currency)
                           or currency or 'USD',
                           is_option_symbol(symbol))


def _splits_after(events: List[Tuple[str, float, str]],
                  when: Optional[str]) -> Tuple[float, str]:
    """(unit factor, symbol in force) at `when` (None: before the data)
    from a pair's split/rename history [(date, ratio, old symbol or '')],
    walking back from the sale: every split or rename dated after
    `when` re-denominates the units and renames back."""
    factor = 1.0
    symbol = ''
    for d, ratio, old in reversed(events):
        if when is not None and d <= when:
            break
        if ratio and abs(ratio) > 1e-12:
            factor *= ratio
        if old:
            symbol = old
    return factor, symbol


def draft_purchases(
    transactions: Iterable[TaxTransaction],
    *,
    country: str,
    year: Any = None,
    date_basis: Optional[str] = None,
    transfer_rows: Iterable[Dict[str, Any]] = (),
    registered_accounts=None,
    journal_symbols: Optional[Set[str]] = None,
    listed_pairs: Optional[Set[Tuple[str, str]]] = None,
    rename_sources: Optional[Dict[str, List[str]]] = None,
    accounts: Optional[Set[str]] = None,
    symbol_key=None,
    in_kind_legs: Optional[Set[Tuple[str, str, str, float]]] = None,
    not_own: Optional[Sequence[Any]] = None,
    in_kind_moves: Optional[Set[Tuple[str, str, str, float]]] = None,
    own_moves: Optional[Sequence[Any]] = None,
) -> Tuple[List[PurchaseDraft], List[DraftGap]]:
    """Draft `.tt` purchase lines from the broker's own cost evidence.

    1. An IB sale coded C (closing) that sells units the data never
       bought, with IB's Basis: one line per lot when IB lists the lots
       it closed (ClosedLot rows: open date, quantity, cost); else one
       line with the purchase date left as DATE_PLACEHOLDER. When the
       data held some of the units sold, IB's Basis covers those too:
       the missing units get IB's lots dated before the account's first
       row (when they add up), else a COST_PLACEHOLDER with the
       arithmetic in the comment.
    2. A transfer-in that states the delivering broker's book value
       (Questrade's or RBC's "TRANSFER BOOK VALUE"; `transfer_rows` are the
       transfer sidecars' rows, the run leaves them out of a taxable
       account's books): one line with the date placeholder — the
       transfer date is not the purchase date.

    A pair is drafted whole (every one of its uncovered sales) when any
    of its sales falls in `year` (on `date_basis`), or always when
    `year` is None: a draft for one sale of a pair would otherwise be
    consumed by an earlier one. Sheltered accounts are not drafted (no
    gain is computed there). Quantities are in the units in force on
    the purchase date (a split in the data after it is undone). Returns
    (drafts, gaps); gaps are the sales and transfer-ins left undrafted,
    with the reason. `accounts`: draft only these (every account's rows
    still count for the Canadian cross-account check). `symbol_key`: a
    transfer row's symbol as the books spell it (ticker.map's renames —
    the sidecars keep the broker's spelling, which the draft line
    uses); identity by default. `in_kind_legs`: the (account, symbol,
    date, quantity) of the transfer rows an in-kind move to or from a
    registered account names, booked or not (pipeline/stages.
    in_kind_taxable_legs every=True): never a slower move of your own,
    as the run's arrivals read them; `in_kind_moves`: the moves' own
    legs (every=True, legs_only=True): never a move of your own at all
    (backlog TJ-0230). `not_own`: the project's .tt NOTOWN
    lines (lib/transfer_in.read_not_own): a transfer-in whose pairing one
    undoes is drafted as one from outside the books (backlog TJ-0075).
    `own_moves`: the run's own pairing of the project's transfer rows
    (pipeline/stages.own_move_plan, [transfer_in.OwnMove]); given, a
    transfer-in it pairs is a move of your own and `transfer_rows` are
    not paired again — pairing them here took another set of rows than
    the run (backlog TJ-0599). Without it (no project around the
    books) every row of `transfer_rows` is paired, whatever `accounts`
    drafts."""
    from taxjson.lib.country import (canonical_country, default_tax_date,
                                     is_canada)
    country = canonical_country(country)
    # The project's tax_date (the caller's); else the country's default.
    date_basis = date_basis or default_tax_date(country)
    year_str = str(year) if year is not None else None
    listed = {(str(s).upper(), str(a)) for s, a in (listed_pairs or ())}
    txs = list(transactions)
    first_day: Dict[str, str] = {}
    for t in txs:
        d = str(t.date or '')[:10]
        if d and (t.account not in first_day or d < first_day[t.account]):
            first_day[t.account] = d
    journal_symbols = _book_journals(txs, journal_symbols)
    # A split another account of the book reports re-denominates this
    # account's units too, as in the engines (backlog TJ-0832, TJ-1022).
    sorted_txs = _drop_duplicate_splits(sorted(
        _spread_global_splits(txs, registered_accounts, country),
        key=lambda t: (_walk_key(t, journal_symbols),
                       0 if float(t.quantity or 0) > 0 else 1)))

    # 'virt': the units the drafts so far add before the data (the walk
    # reads the pair as if they were in: _draft_sale's lines). 'low':
    # the deepest the position goes WITHOUT them.
    def _new():
        return {'qty': 0.0, 'cost': 0.0, 'events': [], 'sales': [],
                'in_year': False, 'tt_qty': 0.0, 'low': 0.0,
                'bought': False, 'virt': 0.0}
    state: Dict[Tuple[str, str], Dict[str, Any]] = {}
    orders = OrderStarts()
    books = FutureBrokerBooks(txs)
    for tx in sorted_txs:
        key = (tx.symbol, tx.account)
        if tx.action == 'SPLIT':
            ratio = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, 'symbol_new', ''))
            s = state.setdefault(key, _new())
            if new_sym:
                t = state.setdefault((new_sym, tx.account), _new())
                t['qty'] += s['qty'] * ratio
                t['virt'] += s['virt'] * ratio
                t['cost'] += s['cost']
                t['events'] = (s['events'] + [(str(tx.date)[:10], ratio,
                                               tx.symbol)])
                t['low'] = min(t['low'], s['low'] * ratio)
                t['bought'] = t['bought'] or s['bought']
                s['qty'] = s['cost'] = s['virt'] = 0.0
            else:
                s['qty'] *= ratio
                s['virt'] *= ratio
                s['events'].append((str(tx.date)[:10], ratio, ''))
            continue
        if tx.action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                             'TRANSFER'):
            continue
        s = state.setdefault(key, _new())
        prev = s['qty']
        q = float(tx.quantity or 0.0)
        order_prev = orders.prev(key, tx, prev)
        own = books.step(tx)
        if q > 0:
            if str(getattr(tx, 'type', '') or '') == \
                    TRANSFER_BOOK_VALUE_TYPE:
                s['booked_bv'] = s.get('booked_bv', 0.0) + q
            s['qty'] = prev + q
            s['bought'] = True
            if prev >= 0:
                s['cost'] += abs(float(tx.net_amount or 0.0))
            else:
                s['cost'] = (abs(float(tx.net_amount or 0.0))
                             * max(0.0, prev + q) / q)
            if (tx.action == 'BUYSELL'
                    and str(getattr(tx, 'source', '') or '')
                    .lower().endswith('.tt')):
                s['tt_qty'] += q
            continue
        if q >= 0:
            continue
        held = max(prev, 0.0)
        held_cost = s['cost'] if held > 1e-12 else 0.0
        s['qty'] = prev + q
        s['low'] = min(s['low'], s['qty'] - s['virt'])
        if prev > 1e-12:
            s['cost'] = (s['cost'] * max(0.0, prev + q) / prev
                         if prev + q > 1e-12 else 0.0)
        unbacked = abs(q) - held
        if unbacked <= 1e-9:
            continue
        closing = _close_unbacked(tx, prev, order_prev, own)
        if not closing and (broker_short_marker(tx)
                            or _derivative_symbol(tx.symbol or '')):
            # A short the broker declares (RBC SHORT., IB code O) or an
            # option / future sold to open: a real position, not a
            # missing purchase (R1-8, S013-00).
            continue
        if year_str is None or _basis_date(tx, date_basis).startswith(
                year_str):
            s['in_year'] = True
        s['sales'].append({
            'tx': tx, 'unbacked': unbacked, 'held': held,
            'held_cost': held_cost, 'events': list(s['events']),
        })
        # The line drafted for these units is a purchase before the
        # data: with it in, the position after this sale is not short,
        # so a later sale of a purchase in the files sells that
        # purchase, not another missing one. One line per sale that
        # went short drafted more than the deepest short, and filling
        # them in held shares never owned (backlog TJ-0018).
        s['qty'] += unbacked
        s['virt'] += unbacked

    drafts: List[PurchaseDraft] = []
    gaps: List[DraftGap] = []
    # Canada pools identical shares across taxable accounts (s.47): the
    # other taxable accounts that trade each symbol.
    traded_in: Dict[str, Set[str]] = {}
    for t in txs:
        if (t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER')
                and not is_registered_account(t.account,
                                              registered_accounts,
                                              country)):
            traded_in.setdefault(t.symbol, set()).add(t.account)
    for (symbol, account), s in sorted(state.items()):
        if not s['sales'] or not s['in_year']:
            continue
        if accounts is not None and account not in accounts:
            continue
        if is_registered_account(account, registered_accounts, country):
            for sale in s['sales']:
                gaps.append(DraftGap(account, symbol,
                                     str(sale['tx'].date), sale['unbacked'],
                                     'sheltered account (no gain is '
                                     'computed there)'))
            continue
        pair_drafts: List[PurchaseDraft] = []
        pair_gaps: List[DraftGap] = []
        for sale in s['sales']:
            d, g = _draft_sale(sale, symbol, account, country,
                               first_day.get(account, ''),
                               rename_sources or {}, date_basis)
            pair_drafts += d
            pair_gaps += g
        if pair_drafts and pair_gaps:
            # The pair stays short until every gap is filled: say so on
            # its drafts.
            short = sum(g.quantity for g in pair_gaps)
            for dr in pair_drafts:
                dr.comments += (f"CHECK: {short:g} more unit(s) of "
                                f"{symbol} were sold with no purchase in "
                                f"your files and no broker cost — their "
                                f"purchase is not drafted; without it the "
                                f"sales still draw on missing history.",)
                dr.warn = True
        others = sorted(traded_in.get(symbol, set()) - {account})
        if pair_drafts and others and is_canada(country):
            for dr in pair_drafts:
                dr.comments += (f"CHECK: you also hold or trade {symbol} "
                                f"in {', '.join(others)}: the ACB pools "
                                f"those shares with these (s.47), so "
                                f"IB's lot cost is not this sale's ACB — "
                                f"those accounts' history must be "
                                f"complete too.",)
                dr.warn = True
        if pair_drafts and (str(symbol).upper(), account) in listed:
            for dr in pair_drafts:
                dr.comments += ("once these lines are in a .tt file, "
                                "remove the OPENING cost=unknown line of "
                                + f"{symbol} / {account}.",)
        drafts += pair_drafts
        gaps += pair_gaps

    t_drafts, t_gaps = _draft_transfers(
        list(transfer_rows), state, country, registered_accounts,
        symbol_key or (lambda x: x), in_kind_legs or set(), not_own,
        in_kind_moves or set(), moves=own_moves, accounts=accounts)
    # A sale whose units a drafted transfer-in delivered is fixed by
    # that line: not listed as undrafted.
    delivered: Dict[Tuple[str, str], float] = {}
    for dr in t_drafts:
        k = ((symbol_key or (lambda x: x))(dr.symbol), dr.account)
        delivered[k] = delivered.get(k, 0.0) + dr.quantity
        if (str(k[0]).upper(), dr.account) in listed:
            dr.comments += ("once this line is in a .tt file, remove "
                            f"the OPENING cost=unknown line of {k[0]} / "
                            f"{dr.account}.",)
    kept: List[DraftGap] = []
    for g in gaps:
        left = delivered.get((g.symbol, g.account), 0.0)
        if g.reason.startswith('no broker cost') and left > 1e-9:
            used = min(left, g.quantity)
            delivered[(g.symbol, g.account)] = left - used
            if used >= g.quantity - 1e-9:
                continue
            g.quantity -= used
            g.reason += (" (the rest of this sale is the drafted "
                         "transfer-in's)")
        kept.append(g)
    return drafts + t_drafts, kept + t_gaps


def _draft_sale(sale: Dict[str, Any], symbol: str, account: str,
                country: str, first_day: str,
                rename_sources: Dict[str, List[str]],
                date_basis: str = 'settle'
                ) -> Tuple[List[PurchaseDraft], List[DraftGap]]:
    """The draft lines (or the gap) for one sale with unbacked units."""
    tx = sale['tx']
    u = sale['unbacked']
    sale_date = str(tx.date or '')[:10]
    gap = DraftGap(account, symbol, sale_date, u, '')
    if (symbol or '').startswith(FUTURES_PREFIXES):
        gap.reason = ("a future: IB's Basis on a futures close is not a "
                      "purchase cost")
        return [], [gap]
    basis = parse_broker_basis(getattr(tx, 'broker_basis', '') or '')
    if basis is None:
        gap.reason = ("no broker cost on the sale (only an IB sale coded "
                      "C carries one, in a statement with the Basis "
                      "column)")
        return [], [gap]
    amount, cur = basis
    mult = float(getattr(tx, 'multiplier', 0.0) or 0.0) or (
        100.0 if is_option_symbol(symbol) else 1.0)
    sold = abs(float(tx.quantity or 0.0))
    src = (f"IB sale {sale_date} of {sold:g} {symbol} (code C, row "
           f"{_mask_id(tx.id)}"
           + (f", {tx.source}" if getattr(tx, 'source', '') else '')
           + ")")
    ev = f"IB Basis {M.shown(amount, 2):,.2f} {cur} for the {sold:g} sold"
    lots = broker_lot_floats(getattr(tx, 'broker_lots', '') or '')
    lot_note = ''
    if lots is not None and abs(sum(q for _, q, _ in lots) - sold) > 1e-6:
        lot_note = ("IB's Closed Lots do not add up to the sale's "
                    "quantity — not used")
        lots = None
    if lots is not None and sale['held'] > 1e-12:
        # Only the lots bought before the data are missing.
        pre = [lt for lt in lots if first_day and lt[0] < first_day]
        if abs(sum(q for _, q, _ in pre) - u) <= 1e-6:
            lots = pre
        else:
            lot_note = ("IB's Closed Lots dated before your data do not "
                        "add up to the units missing — not used")
            lots = None
    # The line goes under the broker's own spelling when ticker.map
    # renames it into the books' symbol (the run maps the .tt line the
    # same way; the holdings hand-off keys on the broker's listing —
    # S049-01): the rename source whose root is IB's raw symbol.
    rename_note = ''
    line_symbol = symbol
    srcs = rename_sources.get(symbol) or []
    raw = str(getattr(tx, 'description', '') or '').strip().upper() \
        .replace(' ', '.')
    mine = [x for x in srcs if x.upper().rsplit('.', 1)[0] == raw]
    if len(mine) == 1:
        line_symbol = mine[0]
        rename_note = (f"{line_symbol} is the broker's symbol: ticker.map "
                       f"maps it to {symbol}, as it maps the sale.")
    elif srcs:
        rename_note = (f"check: ticker.map renames {', '.join(srcs)} to "
                       f"{symbol} — enter the purchase under the "
                       f"broker's symbol and currency, as the account "
                       f"labels it.")
    out: List[PurchaseDraft] = []
    if lots is not None:
        for lot_date, lq, lc in lots:
            line_date = _lot_line_date(lot_date, symbol, cur, date_basis)
            factor, old = _splits_after(sale['events'], line_date)
            comments = [src, f"{ev}; this lot: opened {lot_date}, "
                             f"{lq:g} units, cost {M.shown(lc, 2):,.2f} {cur} "
                             f"(IB Closed Lots)"]
            if line_date != lot_date:
                comments.append(
                    f"dated {line_date}, the lot's settlement date: IB's "
                    f"lot date is the trade date, and under tax_date "
                    f"settle a .tt line's one date is its settlement "
                    f"date.")
            if abs(factor - 1.0) > 1e-12 or old:
                comments.append(
                    f"quantity in the units of {line_date}: a split or "
                    f"rename in your data after it turns {lq / factor:g} "
                    f"{old or symbol} into {lq:g} {symbol}.")
            comments += _drafting_caveats(country, cur, lots=True,
                                          date_basis=date_basis)
            if rename_note:
                comments.append(rename_note)
            out.append(PurchaseDraft(
                account=account, symbol=old or line_symbol,
                quantity=lq / factor, currency=cur, cost=lc,
                date=line_date, multiplier=mult, source='ib-lot',
                sale_date=sale_date, comments=tuple(comments)))
        return out, []
    factor, old = _splits_after(sale['events'], None)
    comments = [src, ev + (" (no lot detail in the export)"
                           if not lot_note else f" ({lot_note})")]
    warn = False
    if sale['held'] > 1e-12:
        rest = amount - sale['held_cost']
        comments.append(
            f"CHECK: your data held {sale['held']:g} of the units sold "
            f"(cost {M.shown(sale['held_cost'], 2):,.2f} in the books' currency) and "
            f"IB's Basis covers all {sold:g}: the {u:g} missing units' "
            f"cost is not known. If this sale closed your whole position "
            f"and the books' currency is {cur}, it is {M.shown(amount, 2):,.2f} - "
            f"{M.shown(sale['held_cost'], 2):,.2f} = {M.shown(rest, 2):,.2f}; else use the "
            f"purchase confirmation.")
        cost = None
        warn = True
    else:
        cost = amount
    comments.append("fill in: the purchase date (the broker does not say "
                    "when these units were bought)"
                    + ("; its settlement date (tax_date settle)."
                       if date_basis == 'settle' else "."))
    if abs(factor - 1.0) > 1e-12 or old:
        comments.append(
            f"quantity in the units before your data: a split or rename "
            f"in your data turns {u / factor:g} {old or symbol} into "
            f"{u:g} {symbol}.")
    comments += _drafting_caveats(country, cur, lots=False,
                                  date_basis=date_basis)
    if rename_note:
        comments.append(rename_note)
    out.append(PurchaseDraft(
        account=account, symbol=old or line_symbol, quantity=u / factor,
        currency=cur, cost=cost, date=None, multiplier=mult,
        source='ib-basis', sale_date=sale_date, comments=tuple(comments),
        warn=warn))
    return out, []


def _draft_transfers(rows: List[Dict[str, Any]],
                     state: Dict[Tuple[str, str], Dict[str, Any]],
                     country: str, registered_accounts, key,
                     in_kind: Set[Tuple[str, str, str, float]] = frozenset(),
                     not_own: Optional[Sequence[Any]] = None,
                     in_kind_moves: Set[Tuple[str, str, str, float]]
                     = frozenset(), *,
                     moves: Optional[Sequence[Any]] = None,
                     accounts: Optional[Set[str]] = None
                     ) -> Tuple[List[PurchaseDraft], List[DraftGap]]:
    """Drafts from transfer-ins that state the delivering broker's book
    value (rows of the transfer sidecars), for the transfer-ins of
    `accounts` (None: every account). A transfer-in the run's pairing
    (`moves`: pipeline/stages.own_move_plan, the arrival stage's call)
    takes as a move of your own is not drafted, and names the account
    it came from as the run paired it. Without `moves`, every row of
    `rows` is paired here (lib/transfer_in.arrivals, as the run calls
    it), whichever accounts are drafted (backlog TJ-0599)."""
    drafts: List[PurchaseDraft] = []
    gaps: List[DraftGap] = []
    tt_left: Dict[Tuple[str, str], float] = {
        k: s.get('tt_qty', 0.0) for k, s in state.items()}
    bv_left: Dict[Tuple[str, str], float] = {
        k: s.get('booked_bv', 0.0) for k, s in state.items()}
    # The moves of your own the run's arrivals pair (lib/transfer_in.
    # arrivals: within OWN_MOVE_DAYS the same quantity first, then the
    # closest date; a slower one when neither leg has another candidate,
    # audit 2026-10-25 diff-engine#4): not booked, so not drafted either.
    # The drafts read that pairing rather than one of their own, which
    # named another source than the run's (backlog TJ-0230). A leg an
    # in-kind move names (to or from a registered account, booked or
    # not) never pairs slowly (`in_kind`, backlog TJ-0074), the move's
    # own leg never at all (`in_kind_moves`), as the run reads them.
    from taxjson.lib import transfer_in as TI

    def _in(idents):
        if not idents:
            return None

        def _f(leg) -> bool:
            acct, _b, t = leg
            return TI.leg_ident(acct, t) in idents
        return _f
    moved: List[Any] = list(moves or ())
    if moves is None:
        TI.arrivals([(str(r.get('account') or ''), '', r) for r in rows
                     if r.get('action', 'TRANSFER') == 'TRANSFER'
                     and not is_registered_account(
                         str(r.get('account') or ''), registered_accounts,
                         country)],
                    key=lambda s: str(key(s) or '').upper(), moves=moved,
                    no_slow=_in(in_kind), no_move=_in(in_kind_moves),
                    not_own=list(not_own or ()))
    # Each transfer-in's moves of your own: (account, symbol, date) ->
    # [OwnMove] (the pairing names the in leg so).
    own_in: Dict[Tuple[str, str, str], List[Any]] = {}
    for m in moved:
        own_in.setdefault((m.in_account, m.in_symbol, m.in_date),
                          []).append(m)
    # The transfer-ins whose pairing a .tt NOTOWN line undoes (backlog
    # TJ-0075): from outside the books, never "a move from your account".
    not_own_in = {n.legs[1] for n in not_own or () if n.matched}
    for r in sorted(rows, key=lambda r: (str(r.get('date') or ''),
                                         str(r.get('symbol') or ''))):
        q = float(r.get('quantity') or 0)
        if q <= 1e-12 or r.get('action', 'TRANSFER') != 'TRANSFER':
            continue
        if accounts is not None and r.get('account') not in accounts:
            continue        # paired above, drafted for its own account
        m = _TRANSFER_BOOK_VALUE_RE.search(str(r.get('description') or ''))
        if not m:
            continue
        symbol = str(r.get('symbol') or '')
        account = str(r.get('account') or '')
        d = str(r.get('date') or '')[:10]
        gap = DraftGap(account, symbol, d, q, '')
        if is_registered_account(account, registered_accounts, country):
            gap.reason = 'sheltered account (no gain is computed there)'
            gaps.append(gap)
            continue
        own = own_in.get((account, symbol, d)) or []
        if (account, symbol, d, M.units(q, 8)) in not_own_in:
            own = []        # a NOTOWN line undoes the pairing (TJ-0075)
        took = sum(m.quantity for m in own)
        if own and not any(m.slow for m in own):
            src = ", ".join(sorted({m.out_account for m in own}))
            part = (f" for {took:g} of the {q:g} units (the other "
                    f"{q - took:g} are a transfer-in from outside, as the "
                    f"run books it)" if took < q - 1e-9 else "")
            gap.reason = (f"a move from your account {src}{part}"
                          f" — its cost carries over, it is not a "
                          f"purchase")
            gaps.append(gap)
            continue
        book_key = (key(symbol), account)
        if tt_left.get(book_key, 0.0) >= q - 1e-9:
            tt_left[book_key] -= q
            gap.reason = "your .tt purchase lines already cover it"
            gaps.append(gap)
            continue
        if bv_left.get(book_key, 0.0) >= q - 1e-9:
            bv_left[book_key] -= q
            gap.reason = ("the run already books it at the stated book "
                          "value")
            gaps.append(gap)
            continue
        sm = next((m for m in own if m.slow), None)
        if sm is not None:
            gap.reason = (f"a move from your account {sm.out_account} "
                          f"({sm.out_date}, {sm.slow} days before) — its "
                          f"cost carries over, it is not a purchase")
            gaps.append(gap)
            continue
        st = state.get(book_key) or {}
        try:
            amount = float(m.group(1).replace(',', ''))
        except ValueError:
            amount = None
        cur = str(r.get('currency') or '').upper()
        mult = float(r.get('multiplier') or 0.0) or (
            100.0 if is_option_symbol(symbol) else 1.0)
        # The row's id when the sidecar keeps one (a transfer row
        # often has none: "(row ?)" said nothing — backlog TJ-0008).
        where = ", ".join(x for x in (
            f"row {_mask_id(r.get('id'))}" if r.get('id') else '',
            str(r.get('source') or '')) if x)
        head = (f"transfer-in {d} of {q:g} {symbol}"
                + (f" ({where})" if where else ''))
        stated = (f"the delivering broker's book value {M.shown(amount or 0, 2):,.2f} "
                  f"{cur}, as printed on the row")
        part = tt_left.get(book_key, 0.0)
        if part > 1e-9 and amount is not None:
            # Your .tt purchases account for PART of the units: the run
            # books nothing for it (lib/transfer_in.mark_covered,
            # partly_covered, B41) and asks for the other units'
            # purchase. Drafted for those units, the cost left to fill
            # in: the book value is for all of them, and a pro-rata
            # share would guess which units the .tt lines are (backlog
            # TJ-0009).
            tt_left[book_key] = 0.0
            rest = q - part
            comments = [head,
                        stated + f" — for all {q:g} units",
                        f"CHECK: your .tt purchase lines already hold "
                        f"{part:g} of these {q:g} units, so the run books "
                        f"none of them: this line is for the other "
                        f"{rest:g}. If your .tt lines are part of the "
                        f"transferred units, these {rest:g} cost the book "
                        f"value less what those lines paid (else use the "
                        f"purchase confirmation); if they are another "
                        f"holding of {symbol} in this account, make this "
                        f"line all {q:g} units at the book value.",
                        "fill in: the ORIGINAL purchase date at the other "
                        "broker (the transfer date is not a purchase "
                        "date) and the cost."]
            comments += _drafting_caveats(country, cur, lots=False,
                                          is_transfer=True)
            drafts.append(PurchaseDraft(
                account=account, symbol=symbol, quantity=rest,
                currency=cur, cost=None, date=None, multiplier=mult,
                source='transfer', comments=tuple(comments), warn=True))
            continue
        if st.get('bought') and st.get('low', 0.0) >= -1e-9:
            # Your files acquire it and no sale of it goes short: the
            # shares are in the books already (another export, a .tt
            # line under another quantity) — a draft would count them
            # twice. Shares still held but missing show in `sanity`.
            gap.reason = ("your files already acquire it and no sale "
                          "goes short — a line would count it twice "
                          "(`tjs sanity` shows a position still "
                          "short of the broker's)")
            gaps.append(gap)
            continue
        if amount is None:
            continue
        # A stated 0 is the broker's placeholder for a cost it was not
        # given, never a $0 lot (lib/transfer_in, B40): the line leaves
        # the cost to fill in, so a draft renamed with only its date
        # filled in is refused, not booked at 0 (backlog TJ-0008).
        zero = abs(amount) <= 1e-6
        comments = [head, stated]
        if zero:
            comments.append(
                "the row states a book value of 0: usually the delivering "
                "broker's placeholder for a cost it was not given, never "
                "your cost, so the cost is left to fill in (0 only if "
                "your cost really is nil).")
        comments.append("fill in: the ORIGINAL purchase date at the other "
                        "broker (the transfer date is not a purchase date)"
                        + (" and the cost." if zero else "."))
        warn = False
        if st.get('bought') and -st.get('low', 0.0) < q - 1e-6:
            comments.append(
                f"CHECK: your files already acquire some {symbol} and "
                f"its sales go at most {-st.get('low', 0.0):g} units "
                f"short: part of these {q:g} may be in your files "
                f"already — keep only the units that are not.")
            warn = True
        # (a stated 0 is no average cost: its note is left out)
        comments += [c for c in _drafting_caveats(country, cur, lots=False,
                                                  is_transfer=True)
                     if not (zero and 'average cost' in c)]
        drafts.append(PurchaseDraft(
            account=account, symbol=symbol, quantity=q, currency=cur,
            cost=None if zero else amount, date=None, multiplier=mult,
            source='transfer', comments=tuple(comments), warn=warn))
    return drafts, gaps

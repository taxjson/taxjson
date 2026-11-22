"""The warn-only replacement checks beside a loss: rights, unresolved options,
opening lots.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import re
from typing import (
    TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple,
)
from datetime import datetime

from taxjson.model import money as M
from taxjson.lib.futures_prefixes import FUTURES_PREFIXES
from taxjson.lib.option_symbols import (
    _FUTURES_PREFIX_RE, is_option_symbol, parse_option_right, parse_option_underlying,
    root_matches_stock,
)
from taxjson.lib.contract_size import OPTION_CONTRACT_SHARES
from taxjson.lib.row_types import is_opening_row

from taxjson.engine.assign import _split_underlying
from taxjson.engine.consts import _shown_if_float
from taxjson.engine.rows import not_a_purchase

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction


def receives_on_buy(tx: TaxTransaction) -> bool:
    """A plain-futures BUY at a negative price: it RECEIVES cash, so
    its cost is negative (the schema's exemption, audit A2-0302). The
    engines' abs() of a buy's amount made it a positive cost in the
    native view, while the settled (base) books were right."""
    sym = tx.symbol or ''
    return (float(tx.quantity or 0.0) > 0
            and M.of(tx.net_amount or 0.0) < 0
            and M.of(tx.price or 0.0) < 0
            and sym.startswith(FUTURES_PREFIXES)
            and not is_option_symbol(sym))


def _opening_option_buys(events: Iterable[TaxTransaction],
                         date_of: Callable[[TaxTransaction], str],
                         contract: Optional[Callable[[str], bool]] = None
                         ) -> List[Tuple[TaxTransaction, float]]:
    """[(buy row, opening contracts)] for every option BUY that opens
    or adds to a long position. Only the OPENING part of a buy acquires
    a right (audit R1-181): a buy that closes a written option acquires
    nothing. The position before each buy is walked per (account,
    contract) in event order (stable: same-stamp rows keep the export's
    order). `contract(symbol)`: which symbols are walked (default: an
    option's; a warrant or right's for detect_right_replacement_matches,
    TJ-0952)."""
    def _when(ev: TaxTransaction) -> Tuple[str, str]:
        return (str(date_of(ev) or ''), str(getattr(ev, 'time', '') or ''))

    if contract is None:
        def contract(sym: str) -> bool:
            return parse_option_right(sym) is not None
    bal: Dict[Tuple[str, str], float] = {}
    out = []
    for ev in sorted(events, key=_when):
        if not contract(ev.symbol) or ev.action not in (
                'BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        k = (getattr(ev, 'account', ''), ev.symbol)
        before = bal.get(k, 0.0)
        bal[k] = before + ev.quantity
        # An opening balance holds the contract but did not buy it in
        # any window (CA-OPEN-01 / US-OPEN-01).
        if (ev.action != 'BUYSELL' or ev.quantity <= 0
                or not_a_purchase(ev)):
            continue
        opening = ev.quantity - min(ev.quantity, max(0.0, -before))
        if opening > 1e-9:
            out.append((ev, opening))
    return out


_RIGHT_RE = re.compile(r'^(.+?)[.\-](WTS|WT|WS|WR|RT|W|R)([.\-][A-Z])?$')


def right_underlying(symbol: str) -> Optional[str]:
    """The share line a WARRANT or RIGHT names, from the dotted/dashed
    listing spelling ('SLH.WT.TO' -> 'SLH.TO', 'ABC.RT.TO' -> 'ABC.TO',
    'SAMPLE.WS.US' -> 'SAMPLE.US'), else None. Undotted US forms ('QZDW')
    are ambiguous with ordinary tickers and are not recognised."""
    if not symbol or is_option_symbol(symbol):
        return None
    base, ext = symbol, ''
    if '.' in symbol:
        b, _, e = symbol.rpartition('.')
        if e.isalpha() and e.isupper() and len(e) <= 3 and e not in (
                'WT', 'WS', 'RT', 'W', 'R', 'WTS', 'WR'):
            base, ext = b, e
    m = _RIGHT_RE.match(base)
    if not m:
        return None
    return f"{m.group(1)}.{ext}" if ext else m.group(1)


def detect_right_replacement_matches(loss_entries: Iterable[Dict[str, Any]],
                                     events: Iterable[TaxTransaction], *,
                                     date_of: Callable[[TaxTransaction], str],
                                     canonical: Optional[Callable[[str], str]] = None,
                                     statute_label: str = '',
                                     window_days: int = 30) -> List[Dict[str, Any]]:
    """WARN-ONLY: a warrant or subscription right on the loss shares
    acquired inside the ±window (audit S071-14). s.54's closing words
    deem "a right to acquire a property" identical to it (IRC §1091:
    "contract or option to acquire"), but the shares one warrant buys
    are not in the books, so the engine cannot size a denial; it names
    the case for review instead. Same record shape as
    detect_option_replacement_matches, rule 'right_vs_share_loss'."""
    canon = canonical or (lambda s: s)
    acqs = []
    # Only the OPENING part of a buy acquires a right: a buy that covers
    # a warrant written short acquires nothing (TJ-0952), as for an
    # option (_opening_option_buys).
    for ev, opening in _opening_option_buys(
            events, date_of, lambda s: bool(right_underlying(s))):
        und = right_underlying(ev.symbol)
        if und:
            acqs.append((canon(und), ev, opening))
    if not acqs:
        return []
    out = []
    for loss in loss_entries:
        symbol = loss['symbol']
        if is_option_symbol(symbol) or right_underlying(symbol):
            continue
        if loss.get('direction') == 'SHORT':
            continue
        try:
            loss_dt = datetime.strptime(loss['date'], '%Y-%m-%d')
        except (KeyError, TypeError, ValueError):
            continue
        by_sym: Dict[str, Dict[str, Any]] = {}
        for und_c, ev, opening in acqs:
            if und_c != canon(symbol):
                continue
            try:
                ev_dt = datetime.strptime(date_of(ev), '%Y-%m-%d')
            except (TypeError, ValueError):
                continue
            if abs((ev_dt - loss_dt).days) > window_days:
                continue
            rec = by_sym.setdefault(ev.symbol, {'qty': 0.0, 'first': None})
            rec['qty'] += opening
            if rec['first'] is None or date_of(ev) < rec['first']:
                rec['first'] = date_of(ev)
        for wsym, rec in sorted(by_sym.items()):
            out.append({
                'rule': 'right_vs_share_loss',
                'loss_symbol': symbol,
                'loss_date': loss['date'],
                'loss_amount': M.present(M.of(loss['amount']), 2),
                'loss_id': loss.get('id', ''),
                'option_symbol': wsym,
                'option_acquired': rec['first'],
                'option_qty': rec['qty'],
                'held_at_window_end': None,
                'statute': statute_label,
            })
    return out


def detect_unresolved_option_replacement_matches(
        loss_entries: Iterable[Dict[str, Any]], events: Iterable[TaxTransaction], *,
        date_of: Callable[[TaxTransaction], str],
        canonical: Optional[Callable[[str], str]] = None,
        statute_label: str = '', window_days: int = 30) -> List[Dict[str, Any]]:
    """WARN-ONLY (audit S069-23; owner decision 2026-10-01): a long CALL
    acquired inside the ±window whose underlying the engines cannot size
    is named for a manual check, the way a warrant is:

      'adjusted_option_vs_loss': a call on an ADJUSTED series of the loss
          shares (root + digit, 'ZZS1' after a corporate action on ZZS).
          Its deliverable is not 100 shares and is not in the books.
      'futures_option_vs_loss': a call on the futures contract of the
          loss named by its family root or another prefix spelling (a
          loss on F:CLG6 and a call on F:CL or /CLG6).

    The root matching is the assignment resolver's (root_matches_stock);
    the market suffix must agree. A futures option is flagged here even
    when it names the loss's own contract (F:CLG6 call vs an F:CLG6
    loss): the engines' call rule never sizes a futures option (A2-0014/
    0056). Any other call whose underlying IS the loss symbol is left to
    the engines' own rule (Canada enforces it, the US warns:
    call_vs_share_loss), so nothing is reported twice. Puts never
    count (a right to sell). Same record shape as
    detect_right_replacement_matches."""
    canon = canonical or (lambda s: s)

    def _base(sym: str) -> Tuple[str, str]:
        # (base, suffix) with any futures prefix spelled 'F:'.
        b, ext = _split_underlying(sym)
        return _FUTURES_PREFIX_RE.sub('F:', b), ext

    acqs = []
    for ev, opening in _opening_option_buys(events, date_of):
        if parse_option_right(ev.symbol) != 'C':
            continue
        und = parse_option_underlying(ev.symbol)
        if not und:
            continue
        r_base, r_ext = _base(canon(und))
        if r_base.startswith('F:'):
            kind = 'futures_option_vs_loss'
        elif r_base[-1:].isdigit():
            kind = 'adjusted_option_vs_loss'
        else:
            continue
        acqs.append((kind, canon(und), r_base, r_ext, ev, opening))
    if not acqs:
        return []
    out = []
    for loss in loss_entries:
        symbol = loss['symbol']
        if is_option_symbol(symbol) or right_underlying(symbol):
            continue
        if loss.get('direction') == 'SHORT':
            continue
        try:
            loss_dt = datetime.strptime(loss['date'], '%Y-%m-%d')
        except (KeyError, TypeError, ValueError):
            continue
        loss_c = canon(symbol)
        s_base, s_ext = _base(loss_c)
        by_sym: Dict[str, Dict[str, Any]] = {}
        for kind, und_c, r_base, r_ext, ev, opening in acqs:
            if und_c == loss_c and kind != 'futures_option_vs_loss':
                continue            # the engines' own call rule
            if r_ext != s_ext or not root_matches_stock(r_base, s_base):
                continue
            try:
                ev_dt = datetime.strptime(date_of(ev), '%Y-%m-%d')
            except (TypeError, ValueError):
                continue
            if abs((ev_dt - loss_dt).days) > window_days:
                continue
            rec = by_sym.setdefault(ev.symbol, {'qty': 0.0, 'first': None,
                                                'rule': kind})
            rec['qty'] += opening
            if rec['first'] is None or date_of(ev) < rec['first']:
                rec['first'] = date_of(ev)
        for osym, rec in sorted(by_sym.items()):
            out.append({
                'rule': rec['rule'],
                'loss_symbol': symbol,
                'loss_date': loss['date'],
                'loss_amount': M.present(M.of(loss['amount']), 2),
                'loss_id': loss.get('id', ''),
                'option_symbol': osym,
                'option_acquired': rec['first'],
                'option_qty': rec['qty'],
                'held_at_window_end': None,
                'statute': statute_label,
            })
    return out


@M.engine
def replacement_flags(loss_entries: Sequence[Dict[str, Any]],
                      events: Sequence[TaxTransaction], *,
                      date_of: Callable[[TaxTransaction], str],
                      canonical: Optional[Callable[[str], str]] = None
                      ) -> List[Dict[str, Any]]:
    """The warn-only flags the wash radar shows beside a loss: the
    unresolved option replacements, then the rights. Called outside an
    engine run, so run as one (`M.engine`): its money left as floats
    (TJ-3101)."""
    return (detect_unresolved_option_replacement_matches(
                loss_entries, events, date_of=date_of, canonical=canonical)
            + detect_right_replacement_matches(
                loss_entries, events, date_of=date_of, canonical=canonical))


def detect_opening_lot_matches(loss_entries: Iterable[Dict[str, Any]],
                               events: Iterable[TaxTransaction], *,
                               date_of: Callable[[TaxTransaction], str],
                               canonical: Optional[Callable[[str], str]] = None,
                               statute_label: str = '',
                               window_days: int = 30) -> List[Dict[str, Any]]:
    """WARN-ONLY: an opening balance whose own lot date (its real
    acquisition, `lot_date`) falls within the ±window of a loss on the
    same security. The opening row is never a replacement (CA-OPEN-01 /
    US-OPEN-01: the snapshot day is not a purchase), but a lot the
    report dates inside the window WAS bought then — and the history
    that would show it is before the snapshot. Named for a manual check,
    like a warrant (CA-SL-14 / US-WASH-14); nothing is denied. Same
    record shape as detect_right_replacement_matches, rule
    'opening_lot_vs_loss'. `date_of` is unused for the lot (its date is
    the lot date as the report states it)."""
    canon = canonical or (lambda s: s)
    lots = [ev for ev in events
            if is_opening_row(ev) and getattr(ev, 'lot_date', None)
            and ev.quantity > 0]
    if not lots:
        return []
    out = []
    for loss in loss_entries:
        if loss.get('direction') == 'SHORT':
            continue
        try:
            loss_dt = datetime.strptime(loss['date'], '%Y-%m-%d')
        except (KeyError, TypeError, ValueError):
            continue
        loss_c = canon(loss['symbol'])
        by_sym: Dict[str, Dict[str, Any]] = {}
        for ev in lots:
            if canon(ev.symbol) != loss_c:
                continue
            try:
                lot_dt = datetime.strptime(ev.lot_date, '%Y-%m-%d')
            except ValueError:
                continue
            if abs((lot_dt - loss_dt).days) > window_days:
                continue
            rec = by_sym.setdefault(ev.symbol, {'qty': 0.0,
                                                'first': ev.lot_date})
            rec['qty'] += ev.quantity
            rec['first'] = min(rec['first'], ev.lot_date)
        for osym, rec in sorted(by_sym.items()):
            out.append({
                'rule': 'opening_lot_vs_loss',
                'loss_symbol': loss['symbol'],
                'loss_date': loss['date'],
                'loss_amount': M.present(M.of(loss['amount']), 2),
                'loss_id': loss.get('id', ''),
                'option_symbol': osym,
                'option_acquired': rec['first'],
                'option_qty': rec['qty'],
                'held_at_window_end': None,
                'statute': statute_label,
            })
    return out


# What a denied loss is called, by the engine that prints the warning
# (partition ENGINE-09: the shared helper said "superficial" in US runs).
_LOSS_TERM = {'canada': 'the loss may be superficial',
              'usa': 'the loss may be a wash sale'}


def _emit_option_replacement_stderr(warnings: Iterable[Dict[str, Any]], *,
                                    country: str) -> None:
    for w in warnings:
        emit_line(f"warning: {format_option_replacement_warning(w, country=country)}")


def format_option_replacement_warning(w: Dict[str, Any], *, country: str) -> str:
    """One option/right-replacement flag as the run prints it (after
    "warning: ")."""
    if w['rule'] == 'opening_lot_vs_loss':
        return (
            f"opening-balance lot (warn-only, numbers unchanged): "
            f"{w['loss_symbol']} loss {_shown_if_float(w['loss_amount'], 2):+,.2f} on "
            f"{w['loss_date']}: the opening balance dates "
            f"{w['option_qty']:g} {w['option_symbol']} as bought "
            f"{w['option_acquired']}, inside the ±30d window; an opening "
            f"balance is never a replacement in the books, but that "
            f"purchase was real, so under {w['statute']} "
            f"{_LOSS_TERM[country]} — review it by hand "
            f"[{w['rule']}]")
    held = ''
    if w['held_at_window_end'] is not None:
        held = (' — still held at window end'
                if w['held_at_window_end'] else
                ' — NOT held at window end (s.54 would likely not '
                'apply)')
    if w['rule'] == 'right_vs_share_loss':
        verdict = (f"a warrant/right is a right to acquire the "
                   f"shares, so {_LOSS_TERM[country]} — review it "
                   f"by hand")
    elif w['rule'] == 'adjusted_option_vs_loss':
        verdict = (f"a call on an adjusted series of the shares is a "
                   f"right to acquire them (its deliverable is not in "
                   f"the books), so {_LOSS_TERM[country]} — review "
                   f"it by hand")
    elif w['rule'] == 'futures_vs_loss':
        verdict = ("a §1256 contract (a futures contract, an option on "
                   "one, a broad-based index option) is marked to "
                   "market, outside §1091: the loss is NOT denied — "
                   "review it by hand")
    elif w['rule'] == 'futures_option_vs_loss':
        verdict = (f"a call on the same futures contract is a right "
                   f"to acquire it, so {_LOSS_TERM[country]}"
                   + (" (a commodity future is a §1256 contract, not "
                      "stock or securities, and usually outside "
                      "§1091)" if country == 'usa' else '')
                   + " — review it by hand")
    elif w.get('loss_qty'):
        _rep = w.get('replaced_qty')
        _size = w.get('contract_size') or OPTION_CONTRACT_SHARES
        _cov = M.units(float(w['covered_shares']), 6)
        # The contracts bought, whole, then the shares of them that
        # count against this loss when that is not all of them (backlog
        # TJ-0619: "0.1 contract(s) x 100 shares").
        _part = ("" if abs(float(w['option_qty']) * _size - _cov) < 1e-6
                 else f": {_cov:g} against this loss")
        verdict = (f"up to {_cov:g} of the "
                   f"{w['loss_qty']:g} shares' loss "
                   f"({_shown_if_float(w['at_risk_amount'], 2):+,.2f}) would be denied "
                   f"({w['option_qty']:g} contract(s) bought x "
                   f"{_size:g} shares, each share used once{_part}"
                   + (f"; {M.units(float(_rep), 6):g} of the loss shares "
                      f"are already replaced by shares bought in the "
                      f"window, and denied"
                      if _rep else "")
                   + ")")
    else:
        verdict = "this loss would be denied"
    return (
        f"option-replacement (warn-only, numbers unchanged): "
        f"{w['loss_symbol']} loss {_shown_if_float(w['loss_amount'], 2):+,.2f} on "
        f"{w['loss_date']} has {w['option_symbol']} acquired "
        f"{w['option_acquired']} in the ±30d window{held}; under "
        f"{w['statute']} {verdict} [{w['rule']}]")

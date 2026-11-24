from taxjson.lib.stage_msg import emit_line, undated_listing_note
from taxjson.lib.out import END_OF_RUN_MARK
import csv
import io
import re
from pathlib import Path
from dataclasses import dataclass, field
from functools import partial
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

# IB's listing suffixes (the currency -> suffix table, the currency/venue
# tags, the listing rule): lib/ib_listing.
from taxjson.lib.ib_listing import (_IB_CURRENCY_EXT, _IB_CURRENCY_TAGS,
                                    _IB_EXT_RE, _ib_currency_ext,
                                    _ib_listing_ext)
from taxjson.model.transaction import NO_SHARE_COUNT_ACCRUAL
from taxjson.parsers.toolbox.descriptions import (FILL_MARK_RE, ISSUER_ROC_KEY, PIL_DESC_RE,
                                                  PIL_LABEL_RETURN_OF_CAPITAL,
                                                  _parse_div_qty_rate)
from taxjson.parsers.toolbox.money import money_close
from taxjson.parsers.toolbox.masking import mask_id
from taxjson.parsers.protocol import Outcome, Row, builds
from taxjson.model.evidence import (add_broker_lot, add_commission_refund, broker_lot,
                                    commission_refund)
from taxjson.model import money as M
from taxjson.lib.option_symbols import is_option_symbol


def _strip_ib_ext(symbol: str, currency: str = '') -> str:
    """`symbol` without the listing suffix a currency gives (.TO, .US,
    .L ...). A dotted preferred series keeps its letter: FTN.PR.L is
    series L, not a London listing, and FTN.PR.V series V (backlog
    TJ-0970), unless the row's `currency` gives that very suffix
    (SYN.PF.L in GBP is that listing: backlog TJ-1202)."""
    from taxjson.lib.brokerages.base import is_ca_pref_series
    ext = _IB_CURRENCY_EXT.get(currency)
    if (is_ca_pref_series(symbol)
            and not (ext and symbol.upper().endswith(f'.{ext}'.upper()))):
        return symbol
    return _IB_EXT_RE.sub('', symbol)


# Trades take their exchange suffix from the trade currency; DIVIDEND/TAX rows
# take theirs from the security's ISIN country. Those disagree for a dual-listed
# name held on a non-domicile exchange — a Canadian-domiciled issuer held on the
# NYSE (SAMPLQ.US) has its dividend stamped SAMPLQ.TO (ISIN 'CA'); an Irish-domiciled
# issuer held as SAMPMB.US gets SAMPMB.L (ISIN 'IE') — orphaning the income onto a
# phantom symbol with no shares. Neither currency nor ISIN is reliable on its
# own (the issuer's TSX listing SAMPLP.TO pays some dividends in USD, so currency would
# wrongly say .US there). The dividend's own ticker (SAMPLP vs SAMPLQ) already names
# the listing, so bind each income row to the suffix of the position actually
# held for that ticker in this statement.
_POSITION_ACTIONS = frozenset({'BUYSELL', 'TRANSFER'})
_INCOME_ACTIONS = frozenset({'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'})
_KNOWN_EXT_RE = re.compile(r'^(.*)' + _IB_EXT_RE.pattern)

# A Corporate Actions row settling the fractional share a split/merger
# ratio leaves over for cash: negative Quantity (the fraction removed),
# the cash in Proceeds. Matched on the phrase alone, wherever it sits
# after the leading `TICKER(ISIN)` token — IB's exact wording for this
# row is unverified against a real statement (KNOWN_ISSUES.md).
_IB_CIL_RE = re.compile(
    r'^\s*([A-Z0-9\s\.]+?)\s*\([^)]*\).*?\bcash\s+in\s+lieu\b', re.IGNORECASE)


# A Corporate Actions row of a split: `TICKER(ISIN) Split 3 for 2`. Ratio
# terms may be comma-grouped ('Split 1 for 1,000'): the old [\d\.]+ stopped
# at the comma and read it as 1 for 1 (audit S058-19).
_IB_SPLIT_RE = re.compile(
    r'^([A-Z0-9\s\.]+)\s*\(([^)]+)\)\s+Split\s+([\d\.,]+)\s+for\s+'
    r'([\d\.,]+)', re.IGNORECASE)


def _ib_refine_split_ratio(info: Dict[str, Any]) -> None:
    """Snap an emitted SPLIT to the broker's own leg quantities once both
    legs are known. IB books fractional results to 4 dp, so the text
    ratio leaves ±1e-4 phantom dust; and a whole-share floor with cash
    in lieu (10 sh 1-for-3 → 3 sh + 0.3333 CIL) diverges from the text
    ratio by the settled fraction, so that fraction counts as part of
    the new leg: old × ratio − fraction then lands on whole shares
    exactly. Bounded to near the text ratio — a larger divergence means
    the legs describe something else; keep the text ratio."""
    if not (info['new'] and info['old']):
        return
    if _ib_split_mixed(info):
        # A long book's legs and a short book's (two accounts of a
        # combined statement): their sums are no ratio at all (155 new
        # for 152.5 old in a 21-for-20) — the declared ratio stands
        # (backlog TJ-0967).
        info['tx']['quantity'] = info['text_ratio']
        return
    # `new` sums the positive legs and `old` the negative ones. On a
    # LONG pool the old shares leave (negative) and the new arrive; on
    # a SHORT pool the signs flip — the positive leg covers the old
    # short and the negative one opens the new — so the long reading
    # gave the reciprocal, which a ratio near 1 (102-for-100) let
    # through the 5% bound (audit S058-20). The reading closer to the
    # text ratio wins; cash in lieu exists only on a long pool.
    # Each new leg can fall short of old x ratio by less than one share
    # (the fraction a whole-share split pays in cash, or IB's 4-dp
    # rounding): a reading whose new shares miss the text ratio's by
    # more than that is no split of one book — the text ratio stands
    # (backlog TJ-0967: a long and a short book's legs summed came
    # within the 5% bound).
    text = info['text_ratio']
    legs = info.get('legs') or ()
    n_pos = sum(1 for q in legs if q > 0) or 1
    n_neg = sum(1 for q in legs if q < 0) or 1
    new_long = info['new'] + info['cil']
    cands = [(new_long / info['old'],
              abs(new_long - info['old'] * text), n_pos)]
    if not info['cil']:
        cands.append((info['old'] / info['new'],
                      abs(info['old'] - info['new'] * text), n_neg))
    derived, miss, n_new = min(cands, key=lambda c: abs(c[0] / text - 1.0))
    if abs(derived / text - 1.0) < 0.05 and (
            miss < n_new + 1e-6 or _ib_legs_one_way(legs, derived, text)):
        info['tx']['quantity'] = derived
    else:
        info['tx']['quantity'] = text


def _ib_legs_one_way(legs, derived: float, text: float) -> bool:
    """True when a split's legs read as one book, or as books of one
    direction that each deliver about the `derived` ratio: a description
    that rounds its ratio (Split 1.05 for 1, 10526 delivered per 10000)
    misses the text by more than a share on a large position, and the
    broker's legs stand (backlog TJ-1166). Two books' pairs of opposite
    directions do not fit: their sums are no ratio."""
    pos = sorted(q for q in legs if q > 0)
    neg = sorted(-q for q in legs if q < 0)
    if len(pos) != len(neg) or not pos:
        return False
    if len(pos) == 1:
        return True
    if abs(derived - text) >= abs(derived - 1.0):
        return False                     # equal long and short books sum to 1
    return (all(abs(n - o * derived) < 1 + 1e-6 for o, n in zip(neg, pos))
            or all(abs(n - o * derived) < 1 + 1e-6 for o, n in zip(pos, neg)))


def _ib_split_mixed(info: Dict[str, Any]) -> bool:
    """True when a split's legs (`info['legs']`, signed quantities) hold
    both a long position's pair (old shares out, about old x ratio in)
    and a SHORT position's (the short covered, about cover x ratio
    opened), on four different legs: a combined statement's long and
    short accounts (backlog TJ-0967). A pair's new leg is old x ratio
    within IB's rounding; floored legs are left to the share bound of
    _ib_refine_split_ratio."""
    legs = list(info.get('legs') or ())
    text = info['text_ratio']
    pos = [(i, q) for i, q in enumerate(legs) if q > 0]
    neg = [(i, -q) for i, q in enumerate(legs) if q < 0]
    if len(pos) < 2 or len(neg) < 2:
        return False

    def near(old_q, new_q):
        return abs(new_q - old_q * text) <= 1e-3 + 1e-9 * new_q
    longs = [(n, p) for n, nq in neg for p, pq in pos if near(nq, pq)]
    shorts = [(p, n) for p, pq in pos for n, nq in neg if near(pq, nq)]
    return any(n1 != n2 and p1 != p2
               for n1, p1 in longs for p2, n2 in shorts)


# Days between a cash-in-lieu row and the split whose fraction it settles.
_IB_CIL_WINDOW = 7


def _days_between(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d")
            - datetime.strptime(a, "%Y-%m-%d")).days


def _split_known_ext(symbol: str):
    """(root, ext) if `symbol` ends in a known exchange suffix, else (symbol,
    None). Options (OCC) and futures (F:/CASH) have no such suffix → skipped."""
    m = _KNOWN_EXT_RE.match(symbol or '')
    return (m.group(1), m.group(2)) if m else (symbol or '', None)


def _listing_replay(transactions: List[Dict[str, Any]], open_qty=None):
    """held_on(root, ext, day) -> bool for _reattribute_income_to_holdings:
    whether the statement shows a position in listing ROOT.EXT on `day`.

    A chronological replay of each listing: its trades and transfers,
    and the corporate actions that change a count without a trade — a
    split (a SPLIT row of one symbol; its quantity is the ratio, applied
    before the day's trades) and a ticker change (a SPLIT row whose
    symbol_new is another listing: the units leave the old listing and
    arrive on the new one, times the ratio). Summing the trades alone,
    a February buy of 10, split 2-for-1 and sold as 20 implied 10 units
    held before the statement, and a January ROC moved onto that
    phantom holding (backlog TJ-0521).

    Events of one day replay in clock order, then the input's order
    (a split first): sorting a day's trades by quantity put a day
    trade's sale before its buy and invented a holding (backlog
    TJ-0659).

    The opening position: `open_qty` (the Open Positions quantities)
    less what the statement adds, when the listing is not renamed away.
    Else the least opening its replay needs never to go below zero, in
    the units before the statement's splits. A sale the broker codes an
    opening (IB `O`, not `C`) opens a short, and a buy coded a closing
    (`C`, not `O`) covers it: neither asks for units held before the
    statement (backlog TJ-0660). A listing with no trade, transfer or
    arrival is no dated evidence: held on every day (it cannot be
    excluded)."""
    ev: Dict[tuple, list] = {}
    for n, t in enumerate(transactions):
        root, ext = _split_known_ext(t.get('symbol'))
        if not ext:
            continue
        day = t.get('date') or ''
        seq = (t.get('time') or '', n)
        act = t.get('action')
        if act in _POSITION_ACTIONS:
            q = float(t.get('quantity') or 0.0)
            codes = {c for c in re.split(r'[;,\s]+', str(
                t.get('open_close') or '').upper()) if c}
            short = ''
            if q < 0 and 'O' in codes and 'C' not in codes:
                short = 'open'
            elif q > 0 and 'C' in codes and 'O' not in codes:
                short = 'cover'
            ev.setdefault((root, ext), []).append(
                (day, 1, seq, 'q', q, short))
        elif act == 'SPLIT':
            try:
                ratio = float(t.get('quantity') or 0.0)
            except (TypeError, ValueError):
                continue
            if not 0 < ratio < float('inf'):
                continue
            if t.get('symbol_new'):
                nroot, next_ = _split_known_ext(t.get('symbol_new'))
                if not next_:
                    continue             # a listing it cannot place
                new = (nroot, next_)
            else:
                new = (root, ext)
            if new == (root, ext):
                ev.setdefault(new, []).append(
                    (day, 0, seq, 'x', ratio, ''))
            else:
                ev.setdefault((root, ext), []).append(
                    (day, 0, seq, 'out', None, ''))
                ev.setdefault(new, []).append(
                    (day, 0, seq, 'in', ((root, ext), ratio), ''))
    for evs in ev.values():
        evs.sort(key=lambda e: e[:3])
    opening: Dict[tuple, float] = {}
    busy: set = set()

    def _evidence(lst, seen=()) -> bool:
        for _d, _o, _s, kind, val, _sh in ev.get(lst, ()):
            if kind == 'q':
                return True
            if kind == 'in' and val[0] not in seen \
                    and _evidence(val[0], seen + (lst,)):
                return True
        return False

    def _inflow(val, day) -> float:
        src, ratio = val
        if src in busy or not _evidence(src):
            return 0.0                   # a cycle, or nothing dated
        return _qty(src, day, strict=True) * ratio

    def _opening(lst) -> float:
        if lst in opening:
            return opening[lst]
        busy.add(lst)
        # u: the long units the replay adds (pre-split units); short:
        # the units a coded short opening sold and no coded cover has
        # bought back. Only the long balance must stay >= 0.
        p, u, low, out, short = 1.0, 0.0, 0.0, False, 0.0
        for day, _o, _s, kind, val, sh in ev.get(lst, ()):
            if kind == 'x':
                p *= val
            elif kind == 'q' and sh == 'open':
                short += -val / p
                continue
            elif kind == 'q' and sh == 'cover' and short > 1e-12:
                covered = min(short, val / p)
                short -= covered
                u += val / p - covered
            elif kind == 'q':
                u += val / p
            elif kind == 'in':
                u += _inflow(val, day) / p
            else:
                out = True
                break
            low = min(low, u)
        if open_qty is not None and not out:
            # The Open Positions hold a short still open (IB prints it
            # negative): what the statement added is the long units less
            # that short, or a short opened in the statement reads as a
            # holding before it (backlog TJ-1044).
            o = (float(open_qty.get(f"{lst[0]}.{lst[1]}", 0.0)) / p
                 - (u - short))
        else:
            o = -low
        busy.discard(lst)
        opening[lst] = o
        return o

    def _qty(lst, day, strict=False) -> float:
        q = _opening(lst)
        for d, _o, _s, kind, val, _sh in ev.get(lst, ()):
            if d > day or (strict and d == day):
                break
            if kind == 'x':
                q *= val
            elif kind == 'q':
                q += val
            elif kind == 'in':
                q += _inflow(val, d)
            else:
                q = 0.0
        return q

    def held_on(root, ext, day) -> bool:
        if not _evidence((root, ext)):
            return True              # no dated evidence: cannot exclude
        return abs(_qty((root, ext), day)) > 1e-9
    return held_on


# An ISIN's shape: two letters, nine letters or digits, a check digit. A
# `Security ID` of another shape (a nine-character CUSIP) is not one: a
# CUSIP alone does not say whether the issuer is American or Canadian.
_ISIN_RE = re.compile(r'[A-Z]{2}[A-Z0-9]{9}[0-9]')
# The instrument `Type` values that name a depositary receipt.
_IB_RECEIPT_TYPES = frozenset({'ADR', 'GDR', 'NVDR', 'CDR', 'DR'})


def _ib_stamp_security_ids(transactions: List[Dict[str, Any]],
                           pre: Dict[str, Any]) -> None:
    """Each share row's `isin`, and `security_type` where the statement
    calls the instrument a depositary receipt: what its Financial
    Instrument Information section gives the row's symbol (`Security
    ID`, `Type`), for every stock and warrant, not only one that paid a
    dividend (backlog TJ-4957); an income row keeps the ISIN its own
    description prints (`_isin`, popped here). An instrument whose
    Listing Exch is another market's than the row's (the section lists
    the TSX instrument and the row is the ticker's US listing) is not
    the row's: no stamp. `isin` is exactly one ISIN: where the section
    gives the ticker two on the row's own market (an ISIN that changed
    inside the statement) `isin` is left out and both are the row's
    `isins`, a list: the report reads each.
    `tjs t1135` reads the issuer's country from it."""
    from taxjson.lib.markets import canadian_suffixes, ib_venue_suffix
    from taxjson.lib.markets_data import suffix_of
    ca = canadian_suffixes()
    infos = [i for (cat, _text), lst in (pre.get('fii_all') or {}).items()
             if cat in ('Stocks', 'Warrants') for i in lst]

    def _market(sfx: str) -> str:
        return 'TO' if sfx in ca else sfx

    def _venue(i: Dict[str, Any]) -> str:
        v = ib_venue_suffix((i.get('exch') or '').upper()) \
            if i.get('exch') else None
        return _market(v) if v else ''

    def _of(i: Dict[str, Any]) -> str:
        return _venue(i) or {'CA': 'TO', 'US': 'US'}.get(
            (i.get('isin') or '').upper()[:2], '')
    for t in transactions:
        own = (t.pop('_isin', None) or '').upper()
        sym = str(t.get('symbol') or '')
        if '.' not in sym or sym.startswith('F:') or is_option_symbol(sym):
            continue
        ids = {x.upper() for x in (pre.get('stock_isins') or {}).get(
            sym.rsplit('.', 1)[0], ())}
        mine = [i for i in infos if (i.get('isin') or '').upper() in ids]
        want = _market(suffix_of(sym))
        # Its own market's instrument: by the venue where the statement
        # names one, by the ISIN's country too where it lists several.
        mine = [i for i in mine
                if not want or (_of(i) if len(ids) > 1 else _venue(i))
                in ('', want)]
        ids = sorted({(i.get('isin') or '').upper() for i in mine
                      if _ISIN_RE.fullmatch((i.get('isin') or '').upper())})
        if _ISIN_RE.fullmatch(own):
            ids = [own]
        if len(ids) == 1:
            t['isin'] = ids[0]
        elif ids:
            t['isins'] = list(ids)
        kinds = {(i.get('type') or '').upper() for i in mine
                 if (i.get('isin') or '').upper() in ids}
        if kinds & _IB_RECEIPT_TYPES:
            t['security_type'] = sorted(kinds & _IB_RECEIPT_TYPES)[0]


def _ib_listings_named(transactions: List[Dict[str, Any]], extra_held,
                       fallback_held) -> tuple:
    """The listings `_reattribute_income_to_holdings` places a row by, each
    {ticker: its listings' suffixes}: `own`, this statement's (its Open
    Positions `extra_held`, its trades and transfers); `fb`, the other
    statements' (`fallback_held`) for a ticker this one has none of;
    `held`, the two together; `named`, every listing any of them names."""
    held: Dict[str, set] = {}
    for full in (extra_held or ()):
        root, ext = _split_known_ext(full)
        if ext:
            held.setdefault(root, set()).add(ext)
    for t in transactions:
        if t.get('action') in _POSITION_ACTIONS:
            root, ext = _split_known_ext(t.get('symbol'))
            if ext:
                held.setdefault(root, set()).add(ext)
    own = {root: set(exts) for root, exts in held.items()}
    named: Dict[str, set] = {root: set(exts) for root, exts in held.items()}
    fb: Dict[str, set] = {}
    for full in (fallback_held or ()):
        root, ext = _split_known_ext(full)
        if not ext:
            continue
        named.setdefault(root, set()).add(ext)
        if root not in held:
            fb.setdefault(root, set()).add(ext)
    held.update(fb)
    return held, own, named, fb


def _reattribute_income_to_holdings(transactions: List[Dict[str, Any]],
                                    extra_held=None, fallback_held=None,
                                    isins_by_root=None,
                                    mismatches=None, open_qty=None,
                                    ambiguous=None, undated=None,
                                    elsewhere=None) -> None:
    """Rewrite each DIVIDEND/TAX suffix to match the position held for the same
    ticker in this file, correcting ISIN-vs-listing mismatches. Leaves the
    ISIN-derived suffix untouched when the account holds no position for that
    ticker, or holds it under more than one suffix (ambiguous — don't guess).

    `extra_held` seeds the holdings map with full symbols from the statement's
    Open Positions section. That closes the two gaps of trade-only inference:
    (a) a buy-and-hold name generates dividends in every LATER statement year
    with no trade rows, so trade-derived holdings were empty and the ISIN
    fallback (SAMPMB.L, SAMPLQ.TO) came back every hold-year; (b) an interlisted
    same-ticker name (SAMPLW-class) whose OTHER listing is still held makes the
    root ambiguous, correctly suppressing the rewrite instead of misbinding a
    .TO dividend onto the .US listing traded in this file.

    `fallback_held` (the listings held or traded in the account's OTHER
    statements) is used only for a root this statement has no position
    evidence for: a statement with only a dividend or ROC row kept the ISIN
    suffix and booked the ROC on a phantom listing (audit S060-00).

    `isins_by_root` (the Financial Instrument Information Security IDs per
    ticker) makes the rebind require the SAME security: a row whose ISIN
    names a different issuer than the held listing of that root (a US
    issuer's SAMPMC vs a Canadian issuer's SAMPMC.TO) keeps its ISIN-derived
    suffix (audit S059-24 / S060-19); the (symbol, ISIN) pairs skipped
    are added to `mismatches`. A row whose ISIN, or whose root's listing
    ISIN, is unknown is rebound as before.

    A root held under several listings is narrowed to the listings held
    ON THE PAYMENT DATE: a TSX buy months after an NYSE-line ROC moved
    the ROC onto the TSX line (an EMPTY pool, a false s.40(3) gain, a
    currency clash) and made the row differ between overlapping
    downloads (audit A2-0089). The position on a date is this
    statement's trades and transfers up to it on top of the opening
    position — `open_qty` (the Open Positions quantities, when the
    section is present) less the statement's net, else the least
    opening the trades need. Rows still ambiguous are added to
    `ambiguous` as (symbol, date); a row of a `fallback_held` root, whose
    listings carry no dates here (which was held that day is not known,
    backlog TJ-5011), to `undated` instead.

    A cost row (a return of capital: an ADJUST, or a foreign issuer's
    marked row) is never moved off its ISIN listing onto a listing this
    statement's own dated rows show was not held on its date (a CA-ISIN
    row went onto a US line first bought weeks later), nor onto a listing
    only the account's other statements name, unless one of those
    statements' rows of the listing leaves room for a holding on the
    row's date (`elsewhere`: {listing: [`_ib_note_held` facts of each
    other statement of the broker account]}; `_ib_could_predate`): a
    listing bought a year back and added to here, with no Open Positions
    section, still takes the row; one another statement first buys, coded
    an opening, after the payment does not. Without `elsewhere` (no
    account context) every other statement's listing takes it. A row on a
    listing other than its ISIN's that neither shows held counts the
    ISIN's listing as another. And wherever a
    cost row ends, on a listing no dated row of the statement shows held
    that day while the account's exports (this statement, `fallback_held`)
    name another listing of the ticker, it is added to `undated` too:
    each entry {'tx', 'why' ('split': the fallback case above; 'moved':
    a row moved off its ISIN's listing, no other listing named, onto one
    no dated row of this statement shows held, for the run to flag where
    the books find nothing there to reduce; else 'unshown'), 'listings'
    (every listing named, the row's included),
    'flat' (the others this statement's rows show not held that day,
    as above)}."""
    held, own, named, fb = _ib_listings_named(
        transactions, extra_held, fallback_held)
    if not held:
        return
    _held_on = _listing_replay(transactions, open_qty)
    def _room(root, e, day) -> bool:
        """Another statement's rows of ROOT.E leave room for a holding of
        it on `day` (any other statement's listing, with no context)."""
        if elsewhere is None:
            return e in (named.get(root) or ()) and e not in (own.get(root) or ())
        return any(_ib_could_predate(i, day)
                   for i in elsewhere.get(f"{root}.{e}") or ())

    def _shown_flat(root, e, day) -> bool:
        """This statement's rows show listing ROOT.E not held on `day`,
        and no other statement's rows leave room for it."""
        return (e in (own.get(root) or ()) and not _held_on(root, e, day)
                and not _room(root, e, day))

    for t in transactions:
        # ROC ADJUSTs come out of the same ISIN-suffixed Dividends section
        # as income rows, so they need the same held-listing rebind — an
        # ADJUST on a phantom listing (SAMPMB.L) would reduce nothing.
        is_roc_adjust = (t.get('action') == 'ADJUST'
                         and t.get('type') == 'roc')
        if t.get('action') not in _INCOME_ACTIONS and not is_roc_adjust:
            continue
        root, ext = _split_known_ext(t.get('symbol'))
        if ext is None:
            continue
        day = t.get('date') or ''
        mine = own.get(root) or set()
        cost = is_roc_adjust or bool(t.get(ISSUER_ROC_KEY))
        split = False
        suffixes = held.get(root)
        if suffixes and len(suffixes) > 1:
            on_day = {e for e in suffixes if _held_on(root, e, day)}
            if len(on_day) == 1:
                suffixes = on_day
            elif root in fb and undated is not None:
                split = True
            elif ambiguous is not None:
                ambiguous.add((t.get('symbol'), t.get('date')))
        if suffixes and len(suffixes) == 1:
            want = next(iter(suffixes))
            if want != ext:
                row_isin = (t.get('_isin') or '').upper()
                known = (isins_by_root or {}).get(root) or set()
                if row_isin and known and row_isin not in known:
                    if mismatches is not None:
                        mismatches.add((t.get('symbol'), row_isin,
                                        f"{root}.{want}"))
                    continue
                if (not cost or (want in mine and _held_on(root, want, day))
                        or _room(root, want, day)):
                    # (a cost row stays off a listing neither this
                    # statement's rows show held that day nor another
                    # statement's rows leave room for)
                    t['symbol'] = f"{root}.{want}"
        if undated is None or not (cost or split):
            continue
        at = _split_known_ext(t.get('symbol'))[1]
        others = (named.get(root) or set()) - {at}
        shown = at in mine and _held_on(root, at, day)
        if at != ext and not shown and not _room(root, at, day):
            others = others | {ext}      # (its ISIN's listing is another)
        moved = cost and at != ext and not shown and not others
        if moved:
            others = {ext}               # (where it may belong)
        if split or (others and not shown):
            undated.append({
                'tx': t, 'why': ('split' if split else
                                 'moved' if moved else 'unshown'),
                'listings': sorted(f"{root}.{e}" for e in others | {at}),
                'flat': sorted(f"{root}.{e}" for e in others
                               if _shown_flat(root, e, day))})


def _ib_level_key(cat: str, sym: str, cur: str, acct: str,
                  when: str) -> tuple:
    """The fill a Trades row's detail levels describe: (category, raw
    symbol, currency, broker Account, trade day). An Order row and the
    execution-level Trade rows under it share it; a row of another
    account or currency of the same ticker on the same day is another
    trade (backlog TJ-0965)."""
    return ((cat or '').strip(), (sym or '').strip(), _norm_ccy(cur),
            (acct or '').strip(),
            ((when or '').replace(',', ' ').split() or [''])[0])


FUTURES_CATEGORIES = ('Futures', 'Options On Futures')
# Row kinds (column 2) IB writes besides Header and Data: roll-ups,
# footnotes, and the blank kind of a short trailing row.
_IB_STRUCTURE_ROW_TYPES = frozenset({'', 'Total', 'SubTotal', 'Notes'})

# Parser warnings about statement COVERAGE or identity that the numbers
# may silently depend on (a statement ending before year end, a missing
# Cash Report, one security under two symbols). `tjs run` echoes
# lines with this prefix to the console (they used to live only in the
# .diag / .sum DIAGNOSTICS banner).
ATTENTION_PREFIX = "warning: ATTENTION:"
# Known tax events the parser could not book (same prefix Kraken uses):
# echoed to the console by `tjs run`, fatal under `run --strict`.
UNBOOKED_PREFIX = "warning: UNBOOKED:"
# The security an IB tender allocation DELIVERS: the first token of the
# row's trailing `(TICKER, NAME, ISIN)` parenthetical.
_IB_DELIVERED_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .]*?)\s*,[^()]*\)\s*$')
# Sections whose rows are not events of a named stock for the
# one-contract-id ticker-change dates (_ib_prescan `conid_rows`): the
# position rows (read by their Symbol), the instrument list and the
# statement's metadata.
_IB_NO_EVENT_SECTIONS = frozenset((
    'Trades', 'Transfers', 'Open Positions',
    'Financial Instrument Information', 'Statement', 'Account Information',
    'Cash Report', 'Codes', 'Notes/Legal Notes'))
# The stocks a row's Description names: its head (`QZX(US...) Cash
# Dividend`, `QZX(US...) Split 2 for 1`) and the first token of every
# `(TICKER, NAME, ...)` parenthetical (a corporate action's delivered
# line). IB's format, not security data.
_IB_DESC_HEAD_RE = re.compile(r'^\s*([A-Z0-9][A-Z0-9 .]*?)\s*\(')
_IB_DESC_PAREN_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .]*?)\s*,')


def _ib_description_tickers(desc: str) -> List[str]:
    """The stock symbols (as the Trades section roots them: spaces as
    dots) an IB row Description names, in order, once each."""
    out: List[str] = []
    m = _IB_DESC_HEAD_RE.match(desc or '')
    found = ([m.group(1)] if m else []) + _IB_DESC_PAREN_RE.findall(
        desc or '')
    for t in found:
        t = re.sub(r'\s+', '.', t.strip())
        if t and t not in out:
            out.append(t)
    return out


def _ib_placeholder_ticker(sym: str) -> Optional[str]:
    """The ticker inside an IB placeholder spelling of a contract —
    a temporary symbol (`20260101093000QZX`) or the `.OLD` line a
    corporate action retires (`QZX.OLD`) — else None. Never a ticker
    change's old or new symbol (audit review LOW)."""
    s = (sym or '').strip().upper()
    if s.endswith('.OLD') and len(s) > 4:
        return s[:-4]
    return ib_temp_symbol_ticker(s)


_IB_STOCK_DIV_RE = re.compile(
    r'^\s*([A-Z0-9][A-Z0-9 .]*?)\s*\([^)]*\)\s+Stock\s+Dividend\b',
    re.IGNORECASE)


def get_ib_settlement(date_str: str, asset_cat: str,
                      currency: str = 'USD',
                      futures_settle: str = 'trade') -> str:
    """Settlement date for an IB trade (the activity CSV has no settle
    column):
    - Futures and futures options: the TRADE date by default. Their
      profit and loss is settled daily through variation margin, so the
      disposition happens when the position is closed.
      futures_settle='next_day' uses the next settlement day instead
      (the clearing house's option-premium date).
    - Stocks/Warrants: T+1 since the cutover (US 2024-05-28; Canada
      2024-05-27, a TSX trading day the US spent closed for Memorial
      Day), T+2 before, T+3 before 2017-09-05, T+5 before 1995-06-07
      in USD and CAD (tax-logic CA-DATE-04 / US-DATE-04). Other markets by
      currency (lib/dates._T1_CUTOVER): LSE/EU T+2 until 2027-10-11,
      the ASX and the rest T+2.
    - Everything else (equity and index options, bonds): T+1.
    `currency` is the listing's settlement calendar (lib/dates.
    settlement_calendar), not the quote currency: days are counted in
    that market's settlement calendar (US: NYSE + Federal Reserve
    holidays; Canada: TSX + bank holidays; a US-dollar trade on a
    Canadian market, 'CAD+USD': both), see lib/market_calendar.
    """
    from taxjson.lib.dates import settlement_lag_days
    from taxjson.lib.market_calendar import add_settlement_days
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    if asset_cat in FUTURES_CATEGORIES:
        if futures_settle == 'next_day':
            return add_settlement_days(date_str, 1, currency).isoformat()
        return date_str
    is_equity = asset_cat in ('Stocks', 'Warrants')
    days = settlement_lag_days(date_str, currency, is_option=not is_equity)
    return add_settlement_days(date_str, days, currency).isoformat()

from taxjson.lib.row_types import STOCK_DIVIDEND
from taxjson.lib.dates import settlement_calendar
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         warrant_exercise_tt_hint,
                                         pair_warrant_legs,
                                         combined_accounts_note,
                                         combined_accounts_refusal,
                                         OPTION_STRIKE_RE,
                                         encode_occ_strike,
                                         option_strike_text,
                                         is_roc_description,
                                         parse_strict_number, shown_name,
                                         ticker_map_joins,
                                         ticker_map_loaded,
                                         ticker_map_renames,
                                         ticker_map_rules,
                                         ticker_map_mentioned)
from taxjson.lib.corp_actions import (ib_cash_merger, ib_merger_owned,
                                      ib_spinoff_parts, ib_tender_root)
from taxjson.lib.trade_cancel import (TRADE_CANCEL_TYPE, pair_cancellations,
                                      retained_fee, trade_cancel_what)

# Statement sections that are statement METADATA or roll-ups of rows the
# parser reads elsewhere — never tax events of their own. Any section
# NOT in this list and without a parser branch is reported loudly (and
# is a parse ERROR when its header carries money-like columns): a
# renamed or localized money section ("Dividendes", "Transactions")
# used to be counted as a quiet non-event while its rows vanished.
_IB_METADATA_SECTIONS = frozenset({
    'Statement', 'Account Information', 'Account Summary',
    'Net Asset Value', 'Change in NAV', 'Mark-to-Market Performance Summary',
    'Realized & Unrealized Performance Summary',
    'Month & Year to Date Performance Summary',
    'Total P/L for Statement Period', 'Cash Report', 'Interest Accruals',
    'Codes', 'Notes/Legal Notes', 'Financial Instrument Information',
    'Deposits & Withdrawals', 'Forex Balances', 'Forex P/L Details',
    'Net Stock Position Summary', 'Base Currency Exchange Rate',
    'Location of Customer Assets, Positions and Money',
    'Stock Yield Enhancement Program Securities Lent',
    'Stock Yield Enhancement Program Securities Lent Activity',
    'Stock Yield Enhancement Program Securities Lent Interest Details',
    # A per-fill BREAKDOWN of levies IB already includes in the Trades
    # Comm/Fee column (see `_ib_row_transaction_fees`).
    'Transaction Fees',
})
# Money / position sections whose Data rows IB writes at the header's
# full width (a shorter row is a truncated export).
_IB_FULL_WIDTH_SECTIONS = frozenset({
    'Trades', 'Dividends', 'Withholding Tax', 'Interest', 'Fees',
    'Commission Adjustments', 'Corporate Actions', 'Transfers',
    'Options Expirations'})
# Header columns that mark a section as carrying money.
_IB_MONEY_COLUMNS = frozenset({
    'Amount', 'Proceeds', 'Comm/Fee', 'Net Amount', 'Gross Amount',
    'Market Value', 'Cash Amount', 'Notional Value', 'Value', 'Basis',
})

# Cash Report lines the parsed rows must reproduce, per currency, to the
# cent (0.02). Each is compared to the sum of what the parser booked for
# it (`_IbRows.cash_booked`, filled by `_ib_book`).
_IB_CASH_TOL = 0.02
_IB_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_IB_TIME_RE = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
# A Trades row's own settlement date (Flex `Settle Date Target`, an
# optional field): the row's settle date when given (tax-logic
# CA-DATE-06 / US-DATE-06, backlog TJ-0971).
_IB_SETTLE_COLS = ('Settle Date Target', 'SettleDateTarget', 'Settle Date',
                   'SettleDate')
# A stated settle date this many days after the trade is refused, as in
# the generic importer: no listed security settles a month late, and
# the tax year follows the settle date.
_IB_SETTLE_REFUSE_DAYS = 31
_IB_SETTLE_LATE_DAYS = 7
# FII option symbol, OCC-style with IB's padding: "QZD   261218P00012500".
# An OCC series in an instrument's name: the root, padded with spaces or not (OCC
# pads a root under six characters; a six-character one has none, TJ-2376), then
# the date, the right and the strike, a fixed fifteen characters (TJ-2408).
_IB_FII_OCC_RE = re.compile(r'^([A-Z0-9.]+)\s*(\d{6})([CP])(\d{8})$')
_MON_MAP = {
    'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05',
    'JUN': '06', 'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10',
    'NOV': '11', 'DEC': '12'}
# Income rows take their exchange suffix from the security's ISIN
# country until _reattribute_income_to_holdings rebinds them to the
# listing actually held. The table is market data
# (lib/markets.isin_country_suffix: a country's home market). An ISIN
# country outside it names where the issuer is domiciled, not a market
# (an Irish fund lists in London, Amsterdam or Frankfurt, an Irish or
# Luxembourg company in New York): its suffix (the first place looked,
# lib/markets.isin_domicile_suffix, else .US) is only where the row
# waits; `fallback` keeps (the symbol, the country, the ISIN), and the
# end of the parse books the row there only on evidence of the listing
# (`_ib_settle_assumed_listings`, TJ-5016).
def _isin_ext(isin: str, ticker: str, fallback: set) -> str:
    from taxjson.lib.markets import isin_country_suffix, isin_domicile_suffix
    if not isin or len(isin) < 2:
        return 'US'
    cc = isin[:2].upper()
    ext = isin_country_suffix(cc)
    if ext:
        return ext
    ext = isin_domicile_suffix(cc) or 'US'
    fallback.add((f"{ticker}.{ext}", cc, isin.upper()))
    return ext


def _ib_isin_venues(isin: str, fii_all) -> Tuple[set, set]:
    """What the statement's Financial Instrument Information says of the
    listing of the stock of `isin`: (the listing suffixes of the Listing
    Exch codes the market data knows, lib/markets.ib_venue_suffix, a
    Canadian venue being `TO` as the books spell it; the codes it does
    not know)."""
    from taxjson.lib.markets import canadian_suffixes, ib_venue_suffix
    known, unknown = set(), set()
    for (cat, _text), infos in (fii_all or {}).items():
        for i in infos if cat in ('Stocks', 'Warrants') else ():
            exch = (i.get('exch') or '').strip().upper()
            if (i.get('isin') or '').upper() != isin or not exch:
                continue
            v = ib_venue_suffix(exch)
            if v:
                known.add('TO' if v in canadian_suffixes() else v)
            else:
                unknown.add(exch)
    return known, unknown


def _ib_income_kinds(rows) -> str:
    """What `rows` (income and cost rows of one security) are, counted:
    '1 dividend, 1 withholding tax'."""
    def kind(t) -> str:
        if t.get('action') == 'ADJUST' or t.get(ISSUER_ROC_KEY):
            return 'return of capital'
        return {'TAX': 'withholding tax',
                'DIVIDEND_IN_LIEU': 'payment in lieu'}.get(t.get('action'), 'dividend')
    many = {'dividend': 'dividends', 'payment in lieu': 'payments in lieu',
            'return of capital': 'returns of capital'}
    kinds = [kind(t) for t in rows]
    return ', '.join(f"{kinds.count(k)} {many.get(k, k) if kinds.count(k) > 1 else k}"
                     for k in dict.fromkeys(kinds))


def _ib_held_as(h: str, transactions, open_syms) -> str:
    """How the statement shows the listing `h` held, for a message: its
    first dated row, its Open Positions row, or another statement's."""
    days = sorted(str(t.get('date')) for t in transactions
                  if t.get('symbol') == h and t.get('action') in _POSITION_ACTIONS
                  and t.get('date'))
    how = ([f"first row {days[0]}"] if days else []) + (
        ["Open Positions"] if h in open_syms else [])
    return f"{h} ({', '.join(how) or 'another statement of the account'})"


def _ib_assumed_listing_refusal(rows, sym: str, cc: str, isin: str, name: str,
                                held=(), venues=(), other_isins=(), cost=False) -> str:
    """The refusal of income rows whose ISIN names no market and whose
    listing nothing settles (`_ib_settle_assumed_listings`): the rows, why
    `sym` is no more than a guess, the true cause, and the ticker.map line
    that names the listing. The cause: the account holds listings of the
    ticker (`held`, each as `_ib_held_as` words it) and the statement does
    not show which the rows belong to (`other_isins`: the instrument list
    gives the ticker these ISINs, not the rows'; `cost`: a return of
    capital, which the one listing held under a currency's code does not
    take unasked); or the instrument list gives the ISIN more than one
    listing exchange (`venues`, worded); or nothing names a listing."""
    from taxjson.lib.markets import isin_domicile_suffix, yahoo_suffix
    root, ext = sym.rsplit('.', 1)
    lines = sorted({o['_line'] for o in rows if o.get('_line')})
    y = yahoo_suffix(ext)
    keep = f"`QUOTE {sym} {f'{root}.{y}' if y else root}`"
    why = ("is where the issuer is domiciled, not a market" if isin_domicile_suffix(cc)
           else "has no home market in taxjson's table (CA, US, GB, AU), so the ISIN "
                "does not say where it is listed")
    head = (f"{name} line{'s' if len(lines) != 1 else ''} "
            f"{', '.join(str(n) for n in lines)}: {root} (ISIN {isin}): the ISIN's "
            f"country ({cc}) {why}")
    tail = (f"refusing to book {len(rows)} row{'s' if len(rows) != 1 else ''} "
            f"({_ib_income_kinds(rows)}) on {sym}, a guess")
    if held:
        names = [h.split(' (', 1)[0] for h in held]
        moves = ' or '.join(f"`GLOBAL {sym} {h}`" for h in names)
        if cost:
            cause = (f"the account holds {root} as {held[0]}, a listing whose rows "
                     f"carry no ISIN: its dividends and the tax withheld are booked "
                     f"there, but a return of capital lowers a cost, and no dated row "
                     f"shows {names[0]} is the holding it was paid on")
        elif other_isins:
            cause = (f"the account holds {root} as {' and '.join(held)}, which the "
                     f"statement's instrument list gives another ISIN "
                     f"({', '.join(sorted(other_isins))}): the statement does not "
                     f"show a holding of this security")
        elif len(held) == 1:
            cause = (f"the account holds {root} as {held[0]}, which no dated row "
                     f"shows held on the payment's date: a return of capital is not "
                     f"moved onto such a listing")
        else:
            cause = (f"the account holds {root} as {' and '.join(held)}, and the "
                     f"statement does not show which holding the rows belong to")
        return (f"{head}, and {cause} — {tail}. Say which in the project's "
                f"ticker.map: {moves} for the listing held; if it is {sym}, any line "
                f"naming that symbol ({keep}).")
    if venues:
        return (f"{head}, and the statement's Financial Instrument Information gives "
                f"the ISIN more than one listing exchange ({'; '.join(venues)}), which "
                f"does not say which line paid — {tail}. Say which in the project's "
                f"ticker.map: if it is {sym}, any line naming that symbol ({keep}); "
                f"else `GLOBAL {sym} {root}.SUFFIX` with the symbol the account's "
                f"trades of it carry (a line IB trades in euros is {root}.EUR).")
    return (
        f"{head}, and nothing "
        f"says where {root} is listed: no trade, transfer or Open Positions row of the "
        f"account's statements holds it, and no Financial Instrument Information row "
        f"gives the ISIN a listing exchange taxjson knows — {tail}. "
        f"Name its listing in the project's ticker.map: if it is {sym}, any "
        f"line naming that symbol ({keep}); if it is listed elsewhere, "
        f"`GLOBAL {sym} {root}.SUFFIX` with the symbol the account's trades of it "
        f"carry (a line IB trades in euros is {root}.EUR). Or add the statement that "
        f"holds its trades or its Open Positions row.")


def _ib_settle_assumed_listings(transactions, assumed, held, open_syms, fii_all,
                                isins_by_root, named, name: str) -> None:
    """Book the income and cost rows still waiting on a listing their
    ISIN's country alone gave (`assumed`, `_isin_ext`) where there is
    evidence of the listing, and refuse the rest (TJ-5016: an Irish ISIN
    put every such row on a London line). A row the rebind moved to a
    held listing is not waiting. In order:

    1. the account holds that very listing (`held`: this statement's
       positions and trades, the account's other statements'): kept;
    2. the project's ticker.map names that symbol (`named`): the user's
       declaration (a GLOBAL line then moves it in the run). A line that
       names another listing of the ticker declares nothing of this row
       (a DELETE, a DISTINCT, a QUOTE or T1135 line of another security's
       listing): it is no evidence;
    3. the account holds other listings of the ticker (`_ib_held_as`):
       exactly one, under a suffix the rebind does not read (a currency's
       code: a line IB trades in euros is ROOT.EUR), no other ISIN's by
       the instrument list (`isins_by_root`), takes the dividends, the
       payments in lieu and the tax, said in an ATTENTION that names the
       holding, the line that confirms it (and is then the user's
       declaration, rule 2) and the line that places the rows elsewhere,
       unless the instrument list gives the ticker the rows' ISIN; a return of
       capital is not moved there (that holding carries no dates here:
       the rebind's rule for a cost row, TJ-5011). Any other holding of
       the ticker is what the rebind left the row off: refused, naming
       the listings held;
    4. the statement's Financial Instrument Information gives the ISIN
       one listing exchange, one the market data knows: that venue's
       listing. Two, or one known and one not: refused, naming them.

    Else refused: nothing names a listing."""
    for sym, cc, isin in sorted(assumed):
        rows = [t for t in transactions if t.get('symbol') == sym
                and (t.get('action') in _INCOME_ACTIONS
                     or (t.get('action') == 'ADJUST' and t.get('type') == 'roc'))
                and (t.get('_isin') or isin).upper() == isin]
        if not rows or sym in held or sym.upper() in named:
            continue
        root = sym.rsplit('.', 1)[0]
        known = (isins_by_root or {}).get(root) or set()
        others = sorted(h for h in held if h and h != sym and '.' in h
                        and h.rsplit('.', 1)[0] == root)
        as_held = [_ib_held_as(h, transactions, open_syms) for h in others]
        venues, strange = _ib_isin_venues(isin, fii_all)
        if others:
            cost = [t for t in rows if t.get('action') == 'ADJUST'
                    or t.get(ISSUER_ROC_KEY)]
            if (len(others) != 1 or _split_known_ext(others[0])[1] is not None
                    or (known and isin not in known)):
                raise BrokerageParseError(_ib_assumed_listing_refusal(
                    rows, sym, cc, isin, name, held=as_held,
                    other_isins=known - {isin} if known and isin not in known else ()))
            if cost:
                raise BrokerageParseError(_ib_assumed_listing_refusal(
                    cost, sym, cc, isin, name, held=as_held, cost=True))
            target = others[0]
            lines = sorted({o['_line'] for o in rows if o.get('_line')})
            if isin not in known:
                # (said once a security; the instrument list giving the
                # ticker this ISIN is the statement's own word: nothing)
                # (the first sentence is the run's one console line)
                emit_line(
                    f"{ATTENTION_PREFIX} {name} line{'s' if len(lines) != 1 else ''} "
                    f"{', '.join(str(n) for n in lines)}: {root} income booked on "
                    f"{target}, the one listing of {root} the account holds, by its "
                    f"name alone. The {len(rows)} row{'s' if len(rows) != 1 else ''} "
                    f"({_ib_income_kinds(rows)}) name ISIN {isin}, whose country "
                    f"names no market, and the rows of {as_held[0]} carry no ISIN. "
                    f"If it is this security, add "
                    f"`GLOBAL {sym} {target}` to the project's ticker.map: the line "
                    f"says so, books the rows the same way and this is not said "
                    f"again. If the rows belong elsewhere, `GLOBAL {sym} "
                    f"{root}.SUFFIX` moves them, and any other line naming {sym} "
                    f"keeps them on {sym}.")
        elif len(venues) == 1 and not strange:
            target = f"{root}.{next(iter(venues))}"
        else:
            raise BrokerageParseError(_ib_assumed_listing_refusal(
                rows, sym, cc, isin, name, venues=(
                    [f"{root}.{v}" for v in sorted(venues)]
                    + [f"{x}, a venue not in taxjson's market data"
                       for x in sorted(strange)] if len(venues) + len(strange) > 1
                    else ())))
        for t in rows:
            t['symbol'] = target


# Statement titles that are NOT an Activity Statement but share its CSV
# shape (a Realized Summary lists closed lots, not the trades — parsing
# it as activity invents or drops events).
_IB_REFUSED_TITLE_RE = re.compile(
    r'summary|confirmation|performance|mtm|projected|tax', re.IGNORECASE)


def _mask_account(acct: str) -> str:
    """Account numbers never reach logs: masked, `?` when missing."""
    return mask_id(acct, empty='?')


def _ib_period(raw: str):
    """(start, end) ISO dates of a Statement 'Period' value ('January 1,
    2024 - December 27, 2024'; a one-day period names one date), or
    None when it does not parse."""
    parts = [x.strip() for x in (raw or '').split(' - ')]
    try:
        days = [datetime.strptime(x, "%B %d, %Y").date() for x in parts]
    except ValueError:
        return None
    if len(days) == 1:
        return days[0], days[0]
    if len(days) == 2 and days[0] <= days[1]:
        return days[0], days[1]
    return None


def _ib_no_trading_gap(g0, g1) -> bool:
    """True when every day of the gap [g0, g1] is a weekend day or Jan 1
    / Dec 25 — no exchange trades and IB books nothing then, so a
    Friday-to-Monday split between two statements is not a hole (audit
    A2-0609)."""
    d = g0
    while d <= g1:
        if d.weekday() < 5 and (d.month, d.day) not in ((1, 1), (12, 25)):
            return False
        d += timedelta(days=1)
    return True


def _coverage_groups(periods):
    """{broker account: (merged spans [[start, end, name]], gaps)} of a
    label's IB statement periods — (name, start, end) or (name, start,
    end, accounts) — merged across weekend-only gaps. A statement naming
    no account covers every account of the label."""
    named = sorted(set().union(*(per[3] for per in periods
                                 if len(per) > 3 and per[3])))
    groups: Dict[str, list] = {}
    for per in periods:
        n, a, b = per[0], per[1], per[2]
        accts = (sorted(per[3]) if len(per) > 3 and per[3]
                 else named or [''])
        for acct in accts:
            groups.setdefault(acct, []).append((a, b, n))
    out = {}
    for acct, spans in groups.items():
        spans.sort()
        merged = [[spans[0][0], spans[0][1], spans[0][2]]]
        gaps = []
        for a, b, n in spans[1:]:
            cur = merged[-1]
            if (a <= cur[1] + timedelta(days=1)
                    or _ib_no_trading_gap(cur[1] + timedelta(days=1),
                                          a - timedelta(days=1))):
                if b > cur[1]:
                    cur[1], cur[2] = b, n
            else:
                gaps.append((cur[1] + timedelta(days=1),
                             a - timedelta(days=1), cur[2], n))
                merged.append([a, b, n])
        out[acct] = (merged, gaps)
    return out


def _year_shortfall(merged, tax_year):
    """(end, statement name) when an account's merged statement spans
    stop before Dec 31 of `tax_year` — end None when none covers the
    year but an earlier one exists — else None."""
    from datetime import date as _date
    y0, y1 = _date(tax_year, 1, 1), _date(tax_year, 12, 31)
    covering = [m for m in merged if m[0] <= y1 and m[1] >= y0]
    if not covering:
        last = max(merged, key=lambda m: m[1])
        return (None, last[2]) if last[1] < y0 else None
    end = max(m[1] for m in covering)
    if end >= y1:
        return None
    return end, next(m[2] for m in covering if m[1] == end)


def ib_year_coverage(paths, tax_year: int) -> List[tuple]:
    """[(masked account or '', end or None)] for each IB account whose
    statements among `paths` stop before Dec 31 of `tax_year` (end None:
    no statement covers the year) — the checklist's per-statement test
    (audit A2-0262). Unreadable files are skipped."""
    periods = []
    for path in paths:
        try:
            rows = IbBrokerage._read_rows(Path(path))
            pre = _ib_prescan(rows, shown_name(path))
        except (OSError, UnicodeError, BrokerageParseError, csv.Error):
            continue
        for row in rows:
            if (len(row) >= 4 and row[0] == 'Statement'
                    and row[1] == 'Data' and row[2] == 'Period'):
                span = _ib_period(row[3])
                if span:
                    periods.append((shown_name(path), *span,
                                    frozenset(pre['accounts'])))
                break
    out = []
    for acct, (merged, _gaps) in sorted(_coverage_groups(periods).items()):
        short = _year_shortfall(merged, tax_year)
        if short is not None:
            out.append((_mask_account(acct) if acct else '', short[0]))
    return out


def _warn_coverage_gaps(periods, today=None, tax_year=None) -> None:
    """Warn when an IB account's statements leave days uncovered: a gap
    between two statements (weekend-only gaps excepted), or statements
    that stop before Dec 31 of the tax year (the 2024 statement that
    ended Dec 27 dropped a Dec 30 sale silently — audit R1-2 / R1-195).

    Per broker ACCOUNT (Account Information): one label may hold the
    statements of two IB accounts, and one account's full year hid the
    other's missing half-year (audit A2-0091 / A2-0261).

    `tax_year` (tjs run passes the project year): an account whose
    statements stop before Dec 31 of that FINISHED year — or hold
    nothing of it — is reported; a statement running into the next year
    covers it (A2-0262 / A2-0609). Without it, the year of the last
    statement is checked when that year is over (the first statement
    may start mid-year: an account opened in May)."""
    from datetime import date as _date
    if not periods:
        return
    today = today or _date.today()
    groups = _coverage_groups(periods)
    many = len(groups) > 1
    for acct, (merged, gaps) in sorted(groups.items()):
        who = (f" (IB account {_mask_account(acct)})"
               if many and acct else '')
        for g0, g1, before, after in gaps:
            emit_line(f"{ATTENTION_PREFIX} IB statements{who} leave "
                  f"{g0.isoformat()} .. {g1.isoformat()} uncovered "
                  f"(between {before} and {after}) — any trade or income "
                  f"in those days is missing from the books. Download "
                  f"the statement for that period.")
        last_end, last_name = merged[-1][1], merged[-1][2]
        if tax_year is not None:
            if today <= _date(tax_year, 12, 31):
                continue                 # the year is still open
            short = _year_shortfall(merged, tax_year)
            if short is None:
                continue
            end, end_name = short
            if end is None:
                emit_line(f"{ATTENTION_PREFIX} {end_name}: the account's IB "
                      f"statements{who} end {last_end.isoformat()} — none "
                      f"covers {tax_year}, so any {tax_year} trade or "
                      f"income is missing from the books. Download the "
                      f"{tax_year} statement.")
            else:
                emit_line(f"{ATTENTION_PREFIX} {end_name}: the account's IB "
                      f"statements{who} end {end.isoformat()}, before the "
                      f"end of {tax_year} — any trade or income from "
                      f"{(end + timedelta(days=1)).isoformat()} to "
                      f"{tax_year}-12-31 is missing from the books. "
                      f"Download the statement that covers the rest of "
                      f"the year.")
            continue
        if (last_end.year < today.year
                and (last_end.month, last_end.day) != (12, 31)):
            emit_line(f"{ATTENTION_PREFIX} {last_name}: the account's IB "
                  f"statements{who} end {last_end.isoformat()}, before the "
                  f"end of {last_end.year} — any trade or income from "
                  f"{(last_end + timedelta(days=1)).isoformat()} to "
                  f"{last_end.year}-12-31 is missing from the books. "
                  f"Download the statement that covers the rest of the "
                  f"year.")


def _ib_cil_unmatched_note(c: Dict[str, Any]) -> str:
    return (f"note: {c['where']}: {c['symbol']}: cash in lieu of "
            f"{c['frac']:g} share(s) on {c['date']} matched no split "
            f"of the symbol within {_IB_CIL_WINDOW} days — booked as a "
            f"sale of the fraction only.")


def unmatched_ca_warning(ca: Dict[str, Any]) -> str:
    return (f"warning: {ca['where']}: IB cancelled (Ca) the corporate "
            f"action {ca['desc']!r} ({ca['qty']:g} on {ca['date']}) but "
            f"its original row is not in this account's statements — "
            f"nothing undone; if the original was booked another way, "
            f"reverse it by hand in a .tt file.")


def _ib_split_datetime(raw: str, where: str):
    """(date, time) from IB's `YYYY-MM-DD, HH:MM:SS`; a date in any
    other shape (Flex `20250328;093000`, `03/28/2025`) is refused —
    it used to pass through verbatim into `date` and misfile the year."""
    parts = (raw or '').replace(',', ' ').split()
    date = parts[0] if parts else ''
    time = parts[1] if len(parts) > 1 else '09:30:00'
    if not _IB_DATE_RE.match(date) or not _IB_TIME_RE.match(time):
        raise BrokerageParseError(
            f"{where}: Date/Time {raw!r} is not IB's 'YYYY-MM-DD, "
            f"HH:MM:SS' — refusing to guess the date")
    hms = [int(x) for x in time.split(':')] + [0]
    h, m, s = hms[0], hms[1], hms[2]
    if h > 23 or m > 59 or s > 59:
        raise BrokerageParseError(
            f"{where}: Date/Time {raw!r} holds an impossible clock time "
            f"(hour 00-23, minute and second 00-59) — refusing to guess "
            f"the date")
    # Zero-padded: the session rules and every (date, time) sort compare
    # the time as TEXT, and '9:45:00' sorts after '20:00:00' (audit
    # A2-0082 / A2-0607).
    return date, f"{h:02d}:{m:02d}:{s:02d}"


# IB stamps Trades rows with the US Eastern CLOCK time. These fills have
# an official trade date other than that clock date (tax-logic
# CA-DATE-SESSION / US-DATE-SESSION):
#   * a US-listed stock or ETF filled in the overnight session (20:00 ET
#     onward, Sunday to Thursday nights — and its after-midnight half on
#     a day the NYSE is closed) trades on the NEXT trading day;
#   * a futures or futures-option fill in its venue's evening session
#     (lib/markets.ib_futures_session, by the contract's Listing Exch;
#     a US-dollar contract elsewhere: CME Globex, 18:00 ET onward,
#     Sunday to Thursday), on a weekday the exchange is closed, or in a
#     24/7 venue's weekend session trades on the next trading day;
#   * an option on a root with a Cboe Global Trading Hours session
#     (lib/markets.is_evening_session_root: shipped market data plus
#     ticker.map EVENING lines) filled from 20:15 ET, or on a weekday
#     the exchange is closed (the holiday GTH session), trades on the
#     next trading day;
#   * a fill on the ASX, HKEX, Tokyo, Singapore or NZX exchanges (every
#     asset class; read from the currency, except a future whose venue
#     has session data, which that data dates) is dated AND timed in the
#     exchange's local time.
_IB_OVERNIGHT_OPEN = '20:00:00'
_IB_OVERNIGHT_CLOSE = '04:00:00'        # the session ends 03:50 ET
_IB_CME_EVENING_OPEN = '18:00:00'
_IB_GTH_OPEN = '20:15:00'
_IB_CLOCK_TZ = 'America/New_York'
_IB_LOCAL_TZ_BY_CURRENCY = {'AUD': 'Australia/Sydney',
                            'HKD': 'Asia/Hong_Kong', 'JPY': 'Asia/Tokyo',
                            'SGD': 'Asia/Singapore',
                            # Shanghai/Shenzhen Stock Connect lines
                            # trade in CNH (re-audit A2-1302).
                            'CNH': 'Asia/Shanghai',
                            'NZD': 'Pacific/Auckland'}

def _ib_next_trading_day(d, include_today: bool = False,
                         calendar: str = 'USD'):
    from taxjson.lib.market_calendar import is_trading_day
    nxt = d if include_today else d + timedelta(days=1)
    while not is_trading_day(nxt, calendar):
        nxt += timedelta(days=1)
    return nxt


def _ib_futures_trade_date(d, date: str, time: str, cur: str, exch: str,
                           stamp: str):
    """The futures branch of _ib_market_trade_date: the venue's session
    data (lib/markets.ib_futures_session, by the contract's Listing
    Exch), else CME Globex's for a US-dollar contract; a fill of any
    other venue keeps its clock date."""
    from taxjson.lib.markets import ib_futures_session
    from taxjson.lib.market_calendar import is_trading_day
    venue = ib_futures_session(exch)
    if venue is None and cur != 'USD':
        return date, time, ''
    venue = venue or {}
    cal = str(venue.get('calendar') or 'USD')
    nxt = (_ib_next_trading_day(d, calendar=cal).isoformat(), '00:00:00',
           stamp)
    if d.weekday() < 5 and not is_trading_day(d, cal):
        # A holiday session (MLK day): its trade date is the next
        # trading day.
        return nxt
    cont = str(venue.get('continuous_from') or '')
    if cont and f"{date} {time}" >= cont:
        # 24/7 contracts (CME crypto from May 29, 2026): the trade date
        # rolls at the daily close, and Friday evening to Sunday evening
        # is the next business day's. No other contract trades then.
        close = str(venue.get('continuous_close') or '17:00:00')
        if d.weekday() >= 5 or time > close:
            return nxt
        return date, time, ''
    opens = str(venue.get('evening_open') or _IB_CME_EVENING_OPEN)
    every = bool(venue.get('every_evening'))
    if time >= opens and (every or d.weekday() in (6, 0, 1, 2, 3)):
        return nxt
    return date, time, ''


def _ib_market_trade_date(date: str, time: str, asset_cat: str,
                          currency: str, ext: str, symbol: str = '',
                          exch: str = ''):
    """(trade date, time, broker stamp) of a Trades row whose exchange
    trade date is not the clock date IB printed; the broker stamp is ''
    when nothing changes. A fill moved to the next trading day is placed
    at 00:00:00 of that day — before its regular session, the fills of
    one night keeping their export (clock) order. An Asia-Pacific fill
    takes its exchange's local date AND time, so one day's fills share
    one clock. A Friday- or Saturday-night US fill is left as stamped:
    there is no session then, and `tjs check-dates` flags it.
    `exch`: the contract's Listing Exch (futures: its venue's session,
    lib/markets.ib_futures_session)."""
    stamp = f"{date} {time} ET"
    cur = (currency or '').upper()
    zone = _IB_LOCAL_TZ_BY_CURRENCY.get(cur)
    if zone and asset_cat in FUTURES_CATEGORIES:
        # A contract's venue decides, not its currency: a yen future on
        # CME follows CME's session, not Tokyo's clock (backlog TJ-1120).
        from taxjson.lib.markets import ib_futures_session
        if ib_futures_session(exch) is not None:
            zone = None
    if zone:
        from zoneinfo import ZoneInfo
        clock = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S")
        try:
            local = clock.replace(tzinfo=ZoneInfo(_IB_CLOCK_TZ)).astimezone(
                ZoneInfo(zone))
        except Exception as e:      # ZoneInfoNotFoundError, no tz database
            # An uncaught traceback where the platform has no tz database
            # (Windows without `tzdata`, audit A2-1447). No DST-rule
            # fallback for Sydney/Auckland: the exchange trade date would
            # be guessed.
            raise BrokerageParseError(
                f"{symbol or cur} {date} {time}: the {cur} fill's exchange "
                f"trade date needs the time zone {zone!r}, which this "
                f"system's time-zone database does not have ({e}) — "
                f"install it: pip install tzdata") from None
        ld, lt = local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S")
        # The local time even when the date is the same: an Eastern
        # 23:30 buy (12:30 local) and a 00:30 sale (13:30 local) of one
        # Tokyo afternoon sorted the sale first (backlog TJ-0872).
        if (ld, lt) != (date, time):
            return ld, lt, stamp
        return date, time, ''
    d = datetime.strptime(date, "%Y-%m-%d").date()
    sun_thu = d.weekday() in (6, 0, 1, 2, 3)
    from taxjson.lib.market_calendar import is_trading_day
    if asset_cat in FUTURES_CATEGORIES:
        return _ib_futures_trade_date(d, date, time, cur, exch, stamp)
    if asset_cat == 'Equity and Index Options':
        root = (symbol or '').strip().split(' ')[0].upper()
        if cur != 'USD':
            return date, time, ''
        holiday = d.weekday() < 5 and not is_trading_day(d, 'USD')
        if (time >= _IB_GTH_OPEN and sun_thu) or holiday:
            # A holiday GTH session (Sunday 20:15 ET through MLK Monday)
            # trades on the next trading day, its after-midnight part
            # too (backlog TJ-0871).
            from taxjson.lib.markets import is_evening_session_root
            if is_evening_session_root(root):
                return (_ib_next_trading_day(d).isoformat(), '00:00:00',
                        stamp)
        return date, time, ''
    if asset_cat != 'Stocks' or ext != 'US':
        return date, time, ''
    if time >= _IB_OVERNIGHT_OPEN:
        if sun_thu:
            return _ib_next_trading_day(d).isoformat(), '00:00:00', stamp
        return date, time, ''
    if (time < _IB_OVERNIGHT_CLOSE and d.weekday() < 5
            and not is_trading_day(d, 'USD')):
        # The after-midnight half of an overnight session on an NYSE
        # holiday (audit A2-0597): the same trade date as its
        # pre-midnight half.
        return _ib_next_trading_day(d).isoformat(), '00:00:00', stamp
    return date, time, ''


_IB_INCOME_TICKER_RE = re.compile(
    r'^([A-Z.\d\-]+(?:\s+[A-Z.\d\-]+)*)\s*\(([^)]+)\)')


def _ib_income_ticker(description: str):
    """(ticker, ISIN) of a Dividends / Withholding Tax row: the leading
    `TICKER(ISIN)` token (space-form class tickers 'SAMPLC B' dotted), else
    the first ticker-like word and no ISIN."""
    ticker, isin = 'UNKNOWN', ''
    m = _IB_INCOME_TICKER_RE.search(description or '')
    if m:
        ticker, isin = m.groups()
    else:
        m = re.search(r'([A-Z.\d\-]+)', description or '')
        if m:
            ticker = m.group(1)
    return ticker.replace(' ', '.'), isin


_IB_INCOME_PHRASE_WORDS = frozenset((
    'CASH', 'DIVIDEND', 'DIVIDENDS', 'PAYMENT', 'RETURN', 'WITHHOLDING',
    'INTEREST', 'CHOICE', 'STOCK', 'TAX', 'PER', 'SHARE', 'USD', 'CAD',
    'ORDINARY', 'SPECIAL', 'BONUS', 'CREDIT', 'DEBIT'))


def _ib_income_ticker_strict(description: str, where: str,
                             section: str):
    """`_ib_income_ticker` for a row that is BOOKED: a Dividends or
    Withholding Tax description with no leading `TICKER (ISIN)` token
    is refused naming the file line. The fallback invented UNKNOWN.US or
    a word of the text ('CASH.DIVIDEND...US') and a CAD-paid Canadian
    eligible dividend was estimated as a foreign one (A2-0780). A
    withholding row on credit interest (IB: 'Withholding @ 20% on Credit
    Interest for MAY-2024') names no security: it is booked on CASH, the
    symbol the Interest section books the interest itself on."""
    if _IB_INCOME_TICKER_RE.search(description or ''):
        return _ib_income_ticker(description)
    # An older statement's 'QZNO Return of Capital USD 0.20 per Share':
    # a leading upper-case ticker, then IB's mixed-case wording — kept
    # as before (no ISIN: the issuer country stays unknown). A leading
    # word of the income phrase itself ('CASH DIVIDEND ...') is not a
    # ticker.
    m = re.match(r'([A-Z][A-Z.\d\-]*)\s+(\S.*)$', description or '')
    if (m and m.group(1) not in _IB_INCOME_PHRASE_WORDS
            and re.search(r'[a-z]', m.group(2))):
        return m.group(1), ''
    if section == 'Withholding Tax' and 'interest' in (description
                                                       or '').lower():
        return 'CASH', ''
    raise BrokerageParseError(
        f"{where}: the Description {description!r} has no leading "
        f"'TICKER (ISIN)' token, so the security this {section} row "
        f"belongs to is unknown — restore the row from the original IB "
        f"statement (or download it again)")


def _ib_posted_dividends(rows) -> List[tuple]:
    """(ticker, pay date) of every posted Dividends row of a statement
    (subtotals skipped) — the account-wide evidence that an accrual in
    another statement was paid (audit R1-327)."""
    out: List[tuple] = []
    hm: Dict[str, int] = {}
    for row in rows:
        if len(row) < 2 or row[0] != 'Dividends':
            continue
        if row[1] == 'Header':
            hm = {c: i for i, c in enumerate(row)}
            continue
        if row[1] != 'Data' or not hm:
            continue

        def g(col, _row=row):
            i = hm.get(col)
            v = _row[i].strip() if i is not None and i < len(_row) else ''
            return _norm_ccy(v) if col == 'Currency' else v
        if not g('Currency') or 'Total' in g('Currency'):
            continue
        if _IB_DATE_RE.match(g('Date')):
            out.append((_ib_income_ticker(g('Description'))[0], g('Date')))
    return out


def _ib_accrual_facts(rows) -> List[Dict[str, Any]]:
    """The dividend-accrual facts of a statement's Change in Dividend
    Accruals section (symbol, pay dates, ex date, currency, the Po row's
    rate and share count), one per dividend — the account-wide evidence
    for a posting in ANOTHER statement (audit A2-0601). Read leniently:
    a malformed row is the statement parse's to report."""
    out: Dict[tuple, Dict[str, Any]] = {}
    hm: Dict[str, int] = {}
    for row in rows:
        if len(row) < 2 or row[0] != 'Change in Dividend Accruals':
            continue
        if row[1] == 'Header':
            hm = {c: i for i, c in enumerate(row)}
            continue
        if row[1] != 'Data' or not hm:
            continue

        def g(col, _row=row):
            i = hm.get(col)
            return _row[i].strip() if i is not None and i < len(_row) else ''
        toks = set(re.split(r'[;,\s]+', g('Code')))
        code = 'Po' if 'Po' in toks else 'Re' if 'Re' in toks else ''
        sym = g('Symbol').replace(' ', '.')
        if not code or not sym:
            continue
        ex, pay = g('Ex Date'), g('Pay Date')
        key = ((g('Account'), sym, 'ex', ex) if ex
               else (g('Account'), sym, 'pay', pay))
        m = out.setdefault(key, {'symbol': sym, 'pay_date': pay,
                                 'pay_dates': set(),
                                 'currency': _norm_ccy(g('Currency')),
                                 'account': g('Account')})
        if pay:
            m['pay_date'] = pay
            m['pay_dates'].add(pay)
        if _IB_DATE_RE.match(ex or ''):
            m['ex_date'] = ex
        if code == 'Po':
            for col, fld in (('Gross Rate', 'po_rate'),
                             ('Quantity', 'po_qty')):
                try:
                    v = abs(parse_strict_number(g(col), field=col))
                except (BrokerageParseError, ValueError):
                    v = 0.0
                if v:
                    m[fld] = v
        _ib_note_po_row(m, code)
    return list(out.values())


def _ib_note_po_row(meta, code: str) -> None:
    """Keep an accrual's Po row (its rate and shares, just read into
    `meta`) beside the ones before it: IB may accrue a distribution's
    characters as a row each (`_ib_gross_rates`). A Re row closes the
    run: a Po row after it is the accrual posted again (a revised rate or
    share count), no part beside the reversed one."""
    if code == 'Re':
        meta['po_closed'] = True
    elif meta.get('po_rate'):
        if meta.pop('po_closed', False):
            meta['po_rows'] = []
        meta.setdefault('po_rows', []).append(
            (meta['po_rate'], meta.get('po_qty') or 0.0))


def _norm_ccy(v: str) -> str:
    """A Currency cell as IB writes it: a 3-letter code upper-cased (a
    hand-edited 'usd' became the suffix .usd and split the pool, audit
    S055-17); anything else ('Total', 'Total in CAD') unchanged."""
    v = (v or '').strip()
    return v.upper() if len(v) == 3 and v.isalpha() else v


def _ib_tender_is_placeholder(description: str, qty: float) -> bool:
    """Whether a tender/voluntary-offer leg moves shares onto (a
    `Tendered to` row's positive leg) or off (an allocation's negative
    leg) IB's `.TEN` placeholder line."""
    tender_in = 'tendered to' in (description or '').lower()
    return (tender_in and qty > 0) or (not tender_in and qty < 0)


def _ib_tender_parked(rows, fii=None) -> Dict[str, float]:
    """Shares a statement leaves on the tender placeholder line, per
    suffixed root symbol — the parse's own tally, for the account-wide
    check (a tender in one statement resolved in the next, audit
    S059-06). A `Ca` row takes back what its original parked."""
    out: Dict[str, float] = {}
    hm: Dict[str, int] = {}
    for row in rows:
        if len(row) < 2 or row[0] != 'Corporate Actions':
            continue
        if row[1] == 'Header':
            hm = {c: i for i, c in enumerate(row)}
            continue
        if row[1] != 'Data' or not hm:
            continue

        def g(col, _row=row):
            i = hm.get(col)
            v = _row[i].strip() if i is not None and i < len(_row) else ''
            return _norm_ccy(v) if col == 'Currency' else v
        cur, cat, desc = g('Currency'), g('Asset Category'), g('Description')
        if (not cur or 'Total' in cur or cat.startswith('Total')
                or (cat and cat not in ('Stocks', 'Warrants'))):
            continue
        root = ib_tender_root(desc)
        if root is None:
            continue
        try:
            qty = parse_strict_number(g('Quantity'), field='Quantity')
            proceeds = parse_strict_number(g('Proceeds'), field='Proceeds',
                                           allow_blank=True, blank=0.0)
        except BrokerageParseError:
            continue
        _base = _IB_CURRENCY_EXT.get(cur, cur)
        _e = _base
        if fii:
            for _raw in (root, root.replace('.', ' ')):
                _e = _ib_listing_ext(cat or 'Stocks', _raw, cur, fii)
                if _e != _base:
                    break
        sym = f"{root}.{_e}"
        if 'Ca' in re.split(r'[;,\s]+', g('Code')):
            if _ib_tender_is_placeholder(desc, -qty):
                out[sym] = out.get(sym, 0.0) + qty
            continue
        if abs(proceeds) >= 0.005 and qty > 0:
            continue                         # not a shape the parse books
        if _ib_tender_is_placeholder(desc, qty):
            out[sym] = out.get(sym, 0.0) + qty
    return out


def _ib_stated_settle(raw: str, date: str, where: str,
                      field: str) -> str:
    """The settle date a Trades row states (`raw`, YYYY-MM-DD or
    YYYYMMDD, a time after it ignored), or '' when the cell is blank.
    Refused when it does not read, is before the trade `date` or more
    than _IB_SETTLE_REFUSE_DAYS after it (backlog TJ-0971)."""
    tok = ((raw or '').replace(',', ' ').replace(';', ' ').split()
           or [''])[0]
    if not tok:
        return ''
    settle = ''
    for fmt in ('%Y-%m-%d', '%Y%m%d'):
        try:
            settle = datetime.strptime(tok, fmt).strftime('%Y-%m-%d')
            break
        except ValueError:
            continue
    if not settle:
        raise BrokerageParseError(
            f"{where}: {field} {raw!r} is not a date (YYYY-MM-DD or "
            f"YYYYMMDD) — refusing to guess the settle date")
    try:
        lag = (datetime.strptime(settle, '%Y-%m-%d')
               - datetime.strptime(date, '%Y-%m-%d')).days
    except ValueError:
        return settle
    if lag < 0:
        raise BrokerageParseError(
            f"{where}: {field} {settle} is before the trade date {date} — "
            f"refusing it (are the columns swapped?)")
    if lag > _IB_SETTLE_REFUSE_DAYS:
        raise BrokerageParseError(
            f"{where}: {field} {settle} is more than "
            f"{_IB_SETTLE_REFUSE_DAYS} days after the trade date {date} — "
            f"no listed security settles that late, and the tax year "
            f"follows the settle date. Fix the cell (or blank it for the "
            f"standard cycle).")
    if lag > _IB_SETTLE_LATE_DAYS:
        emit_line(f"{ATTENTION_PREFIX} {where}: {field} {settle} is {lag} "
                  f"days after the trade date {date} — the tax year "
                  f"follows the settle date: check the cell.")
    return settle


def _ib_require_date(raw: str, where: str, field: str = 'Date') -> str:
    d = (raw or '').strip()
    if not _IB_DATE_RE.match(d):
        raise BrokerageParseError(
            f"{where}: {field} {raw!r} is not YYYY-MM-DD — refusing to "
            f"guess the date")
    return d


def _canonical_root(roots) -> str:
    """The canonical option root among aliases IB lists for ONE conid.
    OCC renames an adjusted contract by appending a digit (QZD ->
    QZD1), so the root every other alias extends is the original;
    otherwise the shortest (then alphabetical) wins."""
    roots = sorted(set(roots), key=lambda r: (len(r), r))
    for r in roots:
        if all(o == r or o.startswith(r) for o in roots):
            return r
    return roots[0]


def _root_aliases(occ_by_conid, underlying_by_conid):
    """(root_alias, alias_conids) for conids listed under several option
    roots (QZD 251121P..., QZD1 251121P... after a corporate action
    renamed the adjusted contract): every alias root maps to the
    canonical one. The canonical root is the contract's UNDERLYING when
    it is one of the roots — after a ticker rename (OLDTKR -> SAMPLE) the
    shorter old root won and the assigned option never met the
    delivered shares (audit S059-11); otherwise _canonical_root."""
    root_alias: Dict[str, str] = {}
    alias_conids: Dict[str, set] = {}
    for conid, occs in occ_by_conid.items():
        roots = {o[0] for o in occs}
        if len(roots) < 2:
            continue
        und = underlying_by_conid.get(conid) or ''
        # Every Underlying any of the account's statements names for the
        # conid: one statement said QZD and a re-download QZD1, and
        # the file being parsed won — one put series split across two
        # symbols (audit A2-0087). Among several, the prefix rule.
        unds = ({u.strip() for u in und} if isinstance(und, (set, frozenset))
                else {und.strip()}) & roots
        canon = (next(iter(unds)) if len(unds) == 1
                 else _canonical_root(unds) if unds
                 else _canonical_root(roots))
        for r in roots:
            if r != canon:
                root_alias[r] = canon
                alias_conids.setdefault(r, set()).add(conid)
    return root_alias, alias_conids


def _ib_stock_symbol(asset_cat: str, raw_symbol: str, currency: str,
                     fii: Dict[tuple, Any]) -> str:
    sym = _strip_ib_ext((raw_symbol or '').strip().replace(' ', '.'), currency)
    return f"{sym}.{_ib_listing_ext(asset_cat, raw_symbol, currency, fii)}"


# IB's TEMPORARY symbol: around a corporate action IB renames the old
# contract to a time stamp (YYYYMMDDHHMMSS) followed by the ticker
# ("20260101093000QZX"), and its Financial Instrument Information then
# lists one contract id under the ticker AND the stamped spelling. The
# stamp is IB's format, not security data.
_IB_TEMP_SYMBOL_RE = re.compile(
    r'^((?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])'
    r'(?:[01]\d|2[0-3])[0-5]\d[0-5]\d)([A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)*)$')


def ib_temp_symbol_ticker(root: str) -> Optional[str]:
    """The ticker inside an IB temporary symbol ('20260101093000QZX' ->
    'QZX'), None when `root` is not one."""
    m = _IB_TEMP_SYMBOL_RE.match((root or '').strip().upper())
    return m.group(2) if m else None


def _ib_temp_folds(conid_syms: Dict[str, set], named=frozenset(),
                   kept: Optional[Dict[str, str]] = None
                   ) -> Dict[str, str]:
    """{temporary root: plain root} for every IB temporary symbol listed
    under the SAME contract id as the plain ticker it stamps: one
    security under IB's working name, folded onto the ticker with no
    ticker.map line. A stamped symbol whose ticker the contract does not
    also list stays as it is (no evidence it is that ticker). Nor one a
    line of the project's ticker.map names (`named`: the map's symbols,
    a lookup line's included — _map_names_temp): the user's line
    decides what it is (a GLOBAL / RENAME / TOBASE target or source,
    DISTINCT, DELETE, an EXTRACT target ...); such a symbol goes to
    `kept` instead."""
    out: Dict[str, str] = {}
    for _conid, syms in conid_syms.items():
        for s in syms:
            tk = ib_temp_symbol_ticker(s)
            if tk and tk in syms:
                if named and _map_names_temp(s, named):
                    if kept is not None:
                        kept[s] = tk
                    continue
                out[s] = tk
    return out


def _map_names_temp(temp: str, named) -> bool:
    """True when a symbol of `named` (ticker.map's) is IB temporary
    symbol `temp` as IB writes it or as the parser books it (with its
    listing suffix: `<stamp>QZX.US`)."""
    from taxjson.lib.markets_data import known_suffixes
    t = re.sub(r'\s+', '.', (temp or '').strip().upper())
    if not t:
        return False
    sfx = {x.upper() for x in known_suffixes()}
    for n in named:
        n = str(n).upper()
        if n == t or (n.startswith(t + '.') and n[len(t) + 1:] in sfx):
            return True
    return False


def _project_map_names(paths) -> frozenset:
    """Every symbol a line of the project's ticker.map names, a lookup
    line's included: the map `taxjson-brokerage --ticker-map` loaded,
    else the one next to inputs/<account>/<statement> (empty without
    one, or when it does not parse — the run refuses such a map)."""
    if ticker_map_loaded():
        return ticker_map_mentioned()
    tmf = _project_map_file(paths)
    if tmf is None:
        return frozenset()
    try:
        from taxjson.lib.ticker_map_rules import named_symbols
        from taxjson.lib.map_file import _parse_map_file
        return named_symbols(_parse_map_file(tmf)[0], lookups=True)
    except Exception:                   # the run reports a bad map
        return frozenset()


def _ib_fold_rows(rows: List[List[str]], folds: Dict[str, str]
                  ) -> List[List[str]]:
    """`rows` with every IB temporary symbol of `folds` written as its
    ticker (the stamp dropped) in every section but the Financial
    Instrument Information, whose symbol lists are the evidence: a Trades
    or Transfers Symbol, a Corporate Actions or Dividends description
    ('(20260101093000QZX, QZX CORP, ...)')."""
    if not folds:
        return rows
    alts = []
    for temp in sorted(folds, key=len, reverse=True):
        m = _IB_TEMP_SYMBOL_RE.match(temp)
        tk = re.escape(m.group(2)).replace(r'\.', r'[.\s]')
        alts.append(rf'{m.group(1)}(?={tk}(?![A-Za-z0-9]))')
    rx = re.compile(r'(?<![A-Za-z0-9])(?:' + '|'.join(alts) + r')')
    out = []
    for row in rows:
        if (len(row) > 1 and row[0] != 'Financial Instrument Information'
                and any(rx.search(c) for c in row)):
            row = [rx.sub('', c) for c in row]
        out.append(row)
    return out


def _note_temp_folds(folds: Dict[str, str], where: str,
                     kept: Optional[Dict[str, str]] = None) -> None:
    """One Info line per fold (the console's `note:` channel), and per
    temporary symbol ticker.map names (`kept`: not folded)."""
    for temp, tk in sorted(folds.items()):
        emit_line(f"note: {where}: {temp} is IB's temporary symbol for "
                  f"{tk} (the same contract id, renamed around a corporate "
                  f"action) — its rows are booked as {tk}; no ticker.map "
                  f"line is needed.")
    for temp, tk in sorted((kept or {}).items()):
        emit_line(f"note: {where}: {temp} is IB's temporary symbol for "
                  f"{tk} (the same contract id, renamed around a corporate "
                  f"action), but a ticker.map line names {temp}: its rows "
                  f"keep that symbol and the map line decides (not folded "
                  f"onto {tk}).")


# The conid_rows kind of a return-of-capital row (a Dividends row whose
# description says so): it moves the position's cost.
_CONID_ROC = 'roc'
# The rows that move a position or its cost (trades, transfers,
# corporate actions, returns of capital): only these date OLD's last row
# (review 2 #10: a dividend, withholding or payment in lieu of OLD paid
# after the change moves nothing and never blocks it — booked under OLD,
# it is income like any other).
_CONID_MOVES = ('pos', 'Corporate Actions', _CONID_ROC)


def _conid_kind_text(kind: str) -> str:
    """A conid_rows kind as the messages say it."""
    if kind == 'pos':
        return 'trade or transfer'
    if kind == _CONID_ROC:
        return 'return of capital row'
    return f"{kind} row"


def _conid_seen(statements, conid_syms: Dict[str, set], account: str = ''
                ) -> Dict[tuple, Dict[str, Any]]:
    """The times (`YYYY-MM-DD[ HH:MM:SS]`) of each stock symbol PER
    CONTRACT ID, from the statements' rows (`statements`: (name,
    _ib_prescan) pairs; `conid_syms`: the account's contract id ->
    symbols): {(conid, symbol): {'first', 'last': its first and last
    position row (a Trades, Transfers or Open Positions row), 'any' /
    'last_any': its first / last row of ANY section (a corporate action,
    a dividend, withholding, a payment in lieu, a fee ...), 'any_what' /
    'last_what': that row's section ('pos' for a position row),
    'last_move' / 'last_move_what': its last row that moves a position or
    its cost (_CONID_MOVES), 'amb': {statement: [contract ids]} where the
    symbol is listed under several contract ids, so its rows there cannot
    be told apart, and '<field>_acct': the IB account label (`account`)
    whose row set that field (_merge_seen: several accounts)}}. A row
    belongs to the contract ids its own statement's instrument list
    gives the symbol (another company that reused the ticker has its own
    id there), else the account's (audit review M1: symbol-keyed dates
    booked a reused ticker's change reversed and backdated)."""
    acct: Dict[str, set] = {}
    for c, syms in conid_syms.items():
        for x in syms:
            acct.setdefault(x, set()).add(c)
    out: Dict[tuple, Dict[str, Any]] = {}
    for name, pre in statements:
        own: Dict[str, set] = {}
        for c, syms in (pre.get('stock_conid_syms') or {}).items():
            for x in syms:
                own.setdefault(x, set()).add(c)
        for root, day, kind in pre.get('conid_rows') or ():
            cands = own.get(root) or acct.get(root)
            if not cands:
                continue
            for c in cands:
                st = out.setdefault((c, root), {
                    'first': '', 'last': '', 'any': '', 'any_what': '',
                    'last_any': '', 'last_what': '', 'last_move': '',
                    'last_move_what': '', 'first_acct': account,
                    'any_acct': account, 'last_move_acct': account,
                    'amb': {}})
                if len(cands) > 1:
                    st['amb'].setdefault(name, sorted(cands))
                if kind == 'pos':
                    if not st['first'] or day < st['first']:
                        st['first'] = day
                    if day > st['last']:
                        st['last'] = day
                if (not st['any'] or day < st['any']
                        or (day == st['any'] and kind == 'pos')):
                    st['any'], st['any_what'] = day, kind
                if (day > st['last_any']
                        or (day == st['last_any'] and kind == 'pos')):
                    st['last_any'], st['last_what'] = day, kind
                if kind in _CONID_MOVES and (
                        day > st['last_move']
                        or (day == st['last_move'] and kind == 'pos')):
                    st['last_move'], st['last_move_what'] = day, kind
    return out


def _merge_seen(seens) -> Dict[tuple, Dict[str, Any]]:
    """One _conid_seen view of several IB accounts' (each account's own
    view, its rows attributed by its own instrument lists): a contract
    id's ticker change is the security's, decided from every account's
    rows (review 2 #4). Each field keeps the account that set it."""
    out: Dict[tuple, Dict[str, Any]] = {}
    for seen in seens:
        for key, st in seen.items():
            m = out.get(key)
            if m is None:
                out[key] = dict(st, amb=dict(st['amb']))
                continue
            if st['first'] and (not m['first'] or st['first'] < m['first']):
                m['first'], m['first_acct'] = st['first'], st['first_acct']
            m['last'] = max(m['last'], st['last'])
            if st['any'] and (not m['any'] or st['any'] < m['any']
                              or (st['any'] == m['any']
                                  and st['any_what'] == 'pos')):
                m['any'], m['any_what'], m['any_acct'] = (
                    st['any'], st['any_what'], st['any_acct'])
            if (st['last_any'] > m['last_any']
                    or (st['last_any'] == m['last_any']
                        and st['last_what'] == 'pos')):
                m['last_any'], m['last_what'] = (st['last_any'],
                                                 st['last_what'])
            if st['last_move'] and (
                    st['last_move'] > m['last_move']
                    or (st['last_move'] == m['last_move']
                        and st['last_move_what'] == 'pos')):
                m['last_move'], m['last_move_what'], m['last_move_acct'] = (
                    st['last_move'], st['last_move_what'],
                    st['last_move_acct'])
            m['amb'].update(st['amb'])
    return out


def _conid_link(order: List[str], full: List[str], links, tt_links
                ) -> Optional[Tuple[str, bool]]:
    """How the account's own rows join two of one contract id's symbols
    (`order`: its symbols as IB roots them, `full`: as booked): (what,
    decided). `decided`: the join books the change for sure — a merger
    row naming both (taxjson-corp-actions books it after the election,
    or stops the run on a shape it cannot book), a `.tt` SPLIT row from
    one to the other. A corporate action of another kind naming both
    (its head and its delivered line, IB's `.OLD` placeholder read as
    its ticker) is undecided: it books the change only when the parse
    books it as a symbol change (a renaming split's SPLIT row —
    _book_conid_renames reads the parsed rows; a CUSIP/ISIN or name
    change row is unhandled and books nothing, review 2 #3). None when
    nothing joins them."""
    roots = set(order)
    found = []
    for link in sorted(links or (), key=lambda x: x[1]):
        named, day = link[0], link[1]
        owned = link[2] if len(link) > 2 else True
        both = sorted(roots & set(named))
        if len(both) >= 2:
            found.append((f"a corporate action of {day} names "
                          f"{' and '.join(both)} together", bool(owned)))
    fulls = {f.upper() for f in full}
    for (a, b), where in sorted((tt_links or {}).items()):
        if a in fulls and b in fulls:
            found.append((f"{where} books a SPLIT row from {a} to {b}",
                          True))
    decided = [f for f in found if f[1]]
    return (decided or found or [None])[0]


def _declared_clash(full: List[str], declared, mapping) -> Optional[str]:
    """A declaration that renames one of the change's old symbols to a
    symbol outside the group (a `.tt` RENAME OLD C, a ticker.map rule
    renaming OLD elsewhere): the user's line decides, the contract id
    books nothing (audit review LOW). None when there is none."""
    from taxjson.lib.ticker_map_rules import map_symbol
    group = {f.upper() for f in full}
    for old in full[:-1]:
        o = old.upper()
        for d in declared or ():
            d_old, d_new = (getattr(d, 'old', None), getattr(d, 'new', None))
            if d_old is None:
                d_old, d_new = d
            if d_old.upper() == o and d_new.upper() not in group:
                where = getattr(d, 'where', '') or 'a .tt line'
                return (f"{where} renames {old} to {d_new.upper()}")
        if ticker_map_loaded():
            if ticker_map_renames(o) and not any(
                    ticker_map_joins(o, f) for f in group - {o}):
                return f"a ticker.map line renames {old}"
        elif mapping is not None:
            ren, dated = mapping
            m = map_symbol(o, ren)
            if m != o and m not in {map_symbol(f, ren) for f in group}:
                return f"a ticker.map line renames {old} to {m}"
            for d_old, d_new in dated:
                if d_old.upper() == o and d_new.upper() not in group:
                    return (f"a ticker.map line renames {old} to "
                            f"{d_new.upper()}")
    return None


def _conid_verdict(conid: str, o: str, n: str,
                   seen: Dict[tuple, Dict[str, Any]]):
    """(date, why, hint) for the change of one contract id from IB
    symbol `o` to `n`: the date to book it on — NEW's earliest row of
    ANY section (a trade, a transfer, a corporate action, a dividend, a
    return of capital, withholding ...), when OLD's last row that moves a
    position or its cost (a trade, a transfer, a corporate action, a
    return of capital — not a dividend, withholding or payment in lieu,
    review 2 #10) is on an earlier day: the change happened in between —
    else why the rows cannot date it and the `.tt` line hint (None: a
    date the rows cannot give). `seen` may span several IB accounts
    (_merge_seen): the hint is NEW's earliest row anywhere, and the
    reason names the accounts."""
    so = seen.get((conid, o)) or {}
    sn = seen.get((conid, n)) or {}
    amb = {**(so.get('amb') or {}), **(sn.get('amb') or {})}
    if amb:
        bad = [x for x in (o, n) if (seen.get((conid, x)) or {}).get('amb')]
        st, ids = sorted(amb.items())[0]
        return None, (f"{st} lists {' and '.join(bad)} under several "
                      f"contract ids ({', '.join(ids)}): the rows there "
                      f"cannot be told apart"), None
    # Days: the order of two rows on one day is no date of a change.
    lo = (so.get('last_move') or '')[:10]
    any_n = (sn.get('any') or '')[:10]
    if not so.get('last_any') or not any_n:
        return None, (f"{o if not so.get('last_any') else n} has no dated "
                      f"row"), None
    if lo and any_n <= lo:
        def _a(st, k):
            a = st.get(k) or ''
            return f" in account '{a}'" if a else ''
        return None, (f"{n}'s rows begin on {any_n} (a "
                      f"{_conid_kind_text(sn.get('any_what'))}"
                      f"{_a(sn, 'any_acct')}), on or before {o}'s last "
                      f"row that moves its position or cost ({lo}, a "
                      f"{_conid_kind_text(so.get('last_move_what'))}"
                      f"{_a(so, 'last_move_acct')})"), any_n
    return any_n, '', any_n


def _declared_join(full: List[str], rules, pairs):
    """How the declarations join `full` (the change's listings, OLD
    first as the contract id's rows order them): 'joined' when every
    step is joined — an undated rename / GLOBAL / TOBASE making them one
    symbol, or a dated RENAME (`rules`: (undated map, dated (old, new)
    pairs) of the ticker.map; `pairs`: the .tt RENAME (old, new) pairs)
    from each symbol to the next; (a, b) for the first step a dated line
    declares the OTHER way (b -> a: review 2 #11 — it must not silence
    the contract id's evidence); '' when nothing joins them."""
    from taxjson.lib.ticker_map_rules import map_symbol
    ren, dated = rules if rules else ({}, ())
    ends = [map_symbol(f.upper(), ren) for f in full]
    edges: Dict[str, set] = {}
    for old, new in list(dated) + list(pairs):
        a, b = map_symbol(old.upper(), ren), map_symbol(new.upper(), ren)
        if a != b:
            edges.setdefault(a, set()).add(b)

    def _reach(a, b):
        seen, todo = {a}, [a]
        while todo:
            x = todo.pop()
            if x == b:
                return True
            for y in edges.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        return False
    steps = []
    for k in range(len(ends) - 1):
        a, b = ends[k], ends[k + 1]
        if a == b or _reach(a, b):
            steps.append('fwd')
        elif _reach(b, a):
            return (full[k], full[k + 1])
        else:
            steps.append('')
    return 'joined' if all(steps) else ''


def _declared_dates(full: List[str], dated_decl, rules) -> Dict[int, tuple]:
    """{step k: (date, where)} of the earliest dated declaration (.tt
    RENAME line, ticker.map dated RENAME) of the change full[k] ->
    full[k + 1]."""
    from taxjson.lib.ticker_map_rules import map_symbol
    ren = (rules or ({}, ()))[0]
    ends = [map_symbol(f.upper(), ren) for f in full]
    out: Dict[int, tuple] = {}
    for d in dated_decl or ():
        old, new = getattr(d, 'old', ''), getattr(d, 'new', '')
        date = str(getattr(d, 'date', '') or '')[:10]
        if not (old and new and date):
            continue
        a, b = map_symbol(old.upper(), ren), map_symbol(new.upper(), ren)
        for k in range(len(ends) - 1):
            if (a, b) == (ends[k], ends[k + 1]) and (
                    k not in out or date < out[k][0]):
                out[k] = (date, getattr(d, 'where', '') or 'a declaration')
    return out


def _check_declared_dates(conid: str, order: List[str], full: List[str],
                          dated_decl, rules, accounts, seen) -> None:
    """A declared date of a contract id's change (a .tt RENAME line, a
    ticker.map dated RENAME) that falls AFTER an IB account's first
    trade or transfer of the new symbol is refused, naming that account
    and day: booked there, the change moved the position after that
    account's NEW rows (one account's sale went short — a hard error in
    Canada — or sold nothing in the USA, review 2 #4). `accounts`:
    {label: that account's _conid_seen}; `seen`: every account's
    (_merge_seen)."""
    dates = _declared_dates(full, dated_decl, rules)
    for k, (date, where) in sorted(dates.items()):
        n = order[k + 1]
        late = []
        for label, aseen in sorted((accounts or {}).items()):
            day = ((aseen.get((conid, n)) or {}).get('first') or '')[:10]
            if day and day < date:
                late.append((day, label))
        if not late:
            continue
        day, label = min(late)
        sn = seen.get((conid, n)) or {}
        hint = (sn.get('any') or day)[:10]
        acct = (f"the IB statements of account '{label}'" if label
                else "the account's IB statements")
        raise BrokerageParseError(
            f"{where}: the ticker change {full[k]} -> {full[k + 1]} is "
            f"declared on {date}, but {acct} already trade or transfer "
            f"{n} on {day} (IB lists both symbols under one contract id, "
            f"{conid}): the change cannot be later than that row. Date it "
            f"on or before {day} — the new symbol's earliest row in any "
            f"IB account is {hint}: `RENAME {hint} {full[k]} "
            f"{full[k + 1]}`.")


def _warn_stock_aliases(conid_syms: Dict[str, set], where: str,
                        seen=None, listing=None,
                        mapping=None, folds=None, book: bool = False,
                        declared=(), distinct=frozenset(), links=(),
                        tt_links=None, project_syms=None, accounts=None,
                        dated_decl=()) -> List[Dict[str, Any]]:
    """One stock conid listed under several symbols (a ticker change
    with no corporate-action row): the parser would book each symbol as
    its own security, so the position splits into two pools (audit
    S059-13 / S060-17). The change is a DATED event (renames are events,
    lib/renames): OLD the symbol whose rows end first, NEW the
    one that continues; each with the listing suffix it is booked under
    (an alphabetical pair with a hard-coded .US joined nothing, or
    renamed new to old — audit A2-0611). The dates are the contract
    id's own (`seen`, _conid_seen: another company that reused a ticker
    has its own id) — with `accounts` ({label: _conid_seen} of every IB
    account of the project), `seen` is all of theirs (_merge_seen) and
    `project_syms` the contract ids' symbols in any account: the change
    is the security's, one decision and one date in every account
    (review 2 #4).

    With `book` (the account-level pass, prepare_files) the change is
    strong evidence — one contract id — and is BOOKED at 00:00 on NEW's
    earliest row of ANY section (a trade, a transfer, a corporate
    action, a dividend, a return of capital ...) when OLD's last row
    that moves a position or its cost is on an earlier day (audit review
    H4: NEW's split before its first trade was lost when the event was
    dated at that trade; review 2 #10: OLD's dividends, withholding and
    payments in lieu never block it). The
    events are returned (old, new, date, conid; reconcile_files adds
    their SPLIT rows, event_source "ib-conid"), each said as a Warning
    naming the way out (`DISTINCT OLD NEW` in ticker.map: two
    securities; a .tt `RENAME <date> OLD NEW late=separate`: OLD's later
    rows are another company's). It is NOT booked — an ATTENTION line
    gives the .tt line `RENAME <date> OLD NEW` and why — when the rows
    cannot date it (NEW's rows start on or before OLD's last position
    row, a symbol listed under several contract ids in one statement),
    and without `book`. Nothing is booked, with a note, when the
    account's own rows already book the change (`links`: a merger row
    naming both, the corporate-action stage's; `tt_links`: a .tt SPLIT
    row from one to the other — audit review H6). A corporate action of
    another kind naming both books it only if the parse does: the
    decision waits for the parsed rows (a `pending` entry,
    _book_conid_renames — review 2 #3). An ATTENTION line names a
    declaration that renames OLD to another symbol (the user's line
    decides), and one that declares the change the other way round
    (NEW -> OLD) while the rows date it OLD -> NEW (review 2 #11). IB's
    placeholder spellings — temporary symbols (`folds`, _ib_temp_folds:
    folded onto the ticker, or named by a ticker.map line, which
    decides) and the `.OLD` line a corporate action retires — are no
    alias here and never a rename target; a stamped symbol of another
    ticker is named but never suggested.
    Quiet, and not booked, once the project's ticker.map (`mapping`:
    (renames, dated pairs)) or a .tt RENAME line (`declared`: the
    DatedRename declarations, or (old, new) pairs) joins them in the
    rows' direction — a dated declaration (`dated_decl`) whose date
    falls after an account's first NEW trade is refused
    (_check_declared_dates) — or a DISTINCT line (`distinct`: pairs)
    keeps them apart."""
    seen = seen or {}
    listing = listing or {}
    folds = folds or {}
    booked: List[Dict[str, Any]] = []
    pairs = []
    for d in declared or ():
        pairs.append((d.old, d.new) if hasattr(d, 'old') else tuple(d))
    rules = ticker_map_rules() if ticker_map_loaded() else mapping
    for conid, own_syms in sorted(conid_syms.items()):
        syms = {x for x in own_syms if x not in folds}
        if len(syms) < 2:
            continue
        # Every IB account's symbols of the contract id (a third symbol
        # another account lists continues the chain).
        syms |= {x for x in (project_syms or {}).get(conid, ())
                 if x not in folds}
        real = [x for x in syms if not _ib_placeholder_ticker(x)]
        temps = sorted(x for x in syms if ib_temp_symbol_ticker(x))
        olds = sorted(x for x in syms if _ib_placeholder_ticker(x)
                      and not ib_temp_symbol_ticker(x))

        def _d(x, k, _c=conid):
            return (seen.get((_c, x)) or {}).get(k) or ''
        # OLD: the symbol whose trades and transfers end first (its
        # other rows, a late dividend, may run past the change).
        order = sorted(real, key=lambda x: (
            _d(x, 'last') or _d(x, 'last_any') or '9999',
            _d(x, 'first') or _d(x, 'any') or '9999', x))
        full = [listing.get(x) or f"{x}.US" for x in order]
        named = ', '.join(order + temps + olds)
        _is = ("is an IB temporary symbol" if len(temps) == 1
               else "are IB temporary symbols")
        tmp_note = (f" {', '.join(temps)} {_is} (a time stamp before the "
                    f"ticker, given around a "
                    f"corporate action) for another ticker — never a "
                    f"rename target; check that corporate action."
                    if temps else "")
        if olds:
            tmp_note += (f" {', '.join(olds)}: IB's placeholder for the "
                         f"line a corporate action retires — never a "
                         f"rename target.")
        head = (f"{where}: IB lists one stock (contract id {conid}) under "
                f"several symbols: {named}")
        if len(full) < 2:
            if temps:           # (IB's `.OLD` placeholder alone: quiet)
                emit_line(f"{ATTENTION_PREFIX} {head}.{tmp_note}")
            continue
        verdicts = [_conid_verdict(str(conid), order[k], order[k + 1],
                                   seen)
                    for k in range(len(full) - 1)]
        joined = _declared_join(full, rules, pairs)
        if joined == 'joined':
            # A declaration books it — on a date no account's NEW rows
            # precede (else refused, naming the account).
            _check_declared_dates(str(conid), order, full, dated_decl,
                                  rules, accounts, seen)
            continue
        if joined:
            a, b = joined
            k = full.index(a)
            if verdicts[k][0]:
                so = seen.get((str(conid), order[k])) or {}
                lo = (so.get('last_move') or so.get('last_any') or '')[:10]
                decl = next((getattr(d, 'where', '') for d in declared or ()
                             if hasattr(d, 'old')
                             and (d.old.upper(), d.new.upper())
                             == (b.upper(), a.upper())), '') \
                    or 'a ticker.map line'
                emit_line(f"{ATTENTION_PREFIX} {head} — {decl} declares "
                          f"the ticker change the other way round ({b} -> "
                          f"{a}), but the contract id's rows run {a} -> "
                          f"{b}: {a}'s rows end on {lo}, {b}'s begin on "
                          f"{verdicts[k][0]}. Not booked from the contract "
                          f"id. If {a} became {b}, write the line as "
                          f"`RENAME {verdicts[k][0]} {a} {b}`; if they are "
                          f"two securities, add `DISTINCT {a} {b}` to "
                          f"ticker.map.{tmp_note}")
                continue
            # The rows cannot date it either way: the user's line
            # decides (quiet, as a declaration in the rows' order).
            continue
        if any(frozenset((full[k], full[k + 1])) in distinct
               for k in range(len(full) - 1)):
            continue        # DISTINCT: the user's call, two securities
        link = _conid_link(order, full, links, tt_links)
        note = (f"note: {head} — {link[0]}: the change is that row's "
                f"(booked from it), not booked again from the "
                f"contract id.{tmp_note}") if link else ''
        if link and (link[1] or not book):
            emit_line(note)
            continue
        clash = _declared_clash(full, declared, mapping)
        if clash:
            emit_line(f"{ATTENTION_PREFIX} {head} — not booked as a ticker "
                      f"change: {clash}, and that line decides. If "
                      f"{full[0]} became {full[1]}, fix the declaration "
                      f"(one ticker change, one line); if they are two "
                      f"securities, add `DISTINCT {full[0]} {full[1]}` to "
                      f"ticker.map.{tmp_note}")
            continue
        events: List[Dict[str, Any]] = []
        if book and all(v[0] for v in verdicts):
            events = [{"old": full[k], "new": full[k + 1],
                       "date": verdicts[k][0], "conid": str(conid)}
                      for k in range(len(full) - 1)]
            what = "; ".join(f"{e['old']} -> {e['new']} on {e['date']}"
                             for e in events)
            outs = " / ".join(f"`DISTINCT {e['old']} {e['new']}`"
                              for e in events)
            lates = " / ".join(f"`RENAME {e['date']} {e['old']} "
                               f"{e['new']} late=separate`"
                               for e in events)
            msg = (f"{ATTENTION_PREFIX} {head} — "
                   f"booked as a ticker change, a dated event ({what}): "
                   f"the position, its cost and acquisition dates carry "
                   f"to the new symbol. If they are two securities, add "
                   f"{outs} to ticker.map; if the old symbol's rows "
                   f"after the "
                   f"date are another company's, add {lates} to a .tt "
                   f"file of this account.{tmp_note}")
        else:
            whys = [v[1] for v in verdicts if v[1]]
            lines = []
            for k, (_date, _why, hint) in enumerate(verdicts):
                if hint:
                    lines.append(f"`RENAME {hint} {full[k]} {full[k + 1]}`")
                else:
                    lines.append(f"`RENAME YYYY-MM-DD {full[k]} "
                                 f"{full[k + 1]}` with the date of the "
                                 f"change")
            if whys:
                why = (f" — a ticker change taxjson "
                       f"does not book from the contract id: "
                       f"{'; '.join(whys)}. Each symbol is "
                       f"booked as its own security until a .tt line of "
                       f"this account records the change as a dated "
                       f"event, e.g. "
                       f"{' and '.join(lines)} (`late=fold` if the old "
                       f"symbol's rows after the date are the renamed "
                       f"shares, `late=separate` if another company's), "
                       f"or, if they are two securities, `DISTINCT "
                       f"{full[0]} {full[1]}` in ticker.map.")
            else:
                why = (f" — a ticker change. Each symbol is booked as its "
                       f"own security until a .tt line of this account "
                       f"records the change as a dated event, e.g. "
                       f"{' and '.join(lines)} (OLD is the symbol whose "
                       f"rows end first, the date the first row of the "
                       f"one that continues).")
            msg = f"{ATTENTION_PREFIX} {head}{why}{tmp_note}"
        if link:
            # A corporate action of another kind names both: the parse
            # decides (its SPLIT row moving OLD to NEW books the change;
            # else this is the change's only booking — review 2 #3).
            why_unbooked = (f" ({link[0]}, but the parse books no symbol "
                            f"change from it)")
            booked.append({'pending': {
                'pairs': [frozenset((full[k].upper(), full[k + 1].upper()))
                          for k in range(len(full) - 1)],
                'events': events, 'note': note,
                'msg': msg.replace(f"{head} —", f"{head}{why_unbooked} —",
                                   1)}})
            continue
        booked.extend(events)
        emit_line(msg)
    return booked


_IB_UNMATCHED_CA_SKIP = ("Corporate Actions Ca row whose original is not "
                         "in this statement (see warning)")


def _flush_ca_side_effects(st: "_IbParse") -> None:
    """Print the lines one statement's translated Corporate Actions rows
    leave (a late cash in lieu, a cash takeover, a stock dividend, an
    unbooked or unhandled row), from its parse state. `_ib_ca_apply_undo`
    takes an undone event's line out of these lists, so under an account
    context they print after every cancellation that can reach the
    statement has run: the parse defers the flush, bound to the
    statement's state (`ca_side_effects`, TJ-2438)."""
    for _w in st.late_warnings:
        emit_line(_w)
    for _ct in st.cash_takeovers:
        emit_line(f"NOTE: cash takeover booked as a sale: {_ct} "
              f"({shown_name(st.path)}).")
    if st.corp_owned_rows:
        emit_line(f"note: {len(st.corp_owned_rows)} merger/spin-off "
              f"Corporate Action row(s) in {shown_name(st.path)} are booked by "
              f"taxjson-corp-actions after the tax election (`tjs "
              f"run` runs it), not by this parser.")
    for _m in st.stock_dividends:
        emit_line(f"{ATTENTION_PREFIX} {_m}")
    for _m in st.unbooked_ca:
        emit_line(f"{UNBOOKED_PREFIX} {_m}")
    if st.unhandled_ca_tickers:
        total = sum(st.unhandled_ca_tickers.values())
        tickers = ', '.join(sorted(st.unhandled_ca_tickers.keys()))
        # UNBOOKED (console; fatal under run --strict): a row that
        # moved shares and nothing booked. The old advice — a
        # manual TRANSFER row — is dropped in a taxable account and
        # double-booked what corp-actions already books (R1-140).
        emit_line(f"{UNBOOKED_PREFIX} {total} unhandled Corporate Action "
            f"row(s) in {shown_name(st.path)} for: {tickers}. Only splits, cash "
            f"in lieu, stock dividends, tenders and cash takeovers are "
            f"booked here (mergers and spin-offs by "
            f"taxjson-corp-actions). If the event changed your "
            f"position or basis, book it by hand in a .tt file "
            f"(BUYSELL / SPLIT rows).")


def _project_map_file(paths) -> Optional[Path]:
    """The project's ticker.map: the one the parser loaded (--ticker-map;
    `tjs run` passes it), else the map of the project of the first of
    `paths` that has one (lib/project_layout.project_of_input: the
    project the run names, whose exports may sit in a folder shared by
    every year), else None."""
    from taxjson.lib.brokerages.base import ticker_map_file
    loaded = ticker_map_file()
    if loaded is not None:
        return loaded
    from taxjson.lib import project_layout as _PL
    for path in paths:
        root = _PL.project_of_input(path)
        if root is not None and _PL.ticker_map_path(root).is_file():
            return _PL.ticker_map_path(root)
    return None


def _project_root(paths) -> Optional[Path]:
    """The project folder of inputs/<account>/<statement> for the first
    of `paths` (the project the run names when the inputs folder is
    shared by every year: lib/project_layout.project_of_input), else
    None."""
    from taxjson.lib import project_layout as _PL
    for path in paths:
        root = _PL.project_of_input(path)
        if root is not None and _PL.has_config(root):
            return root
    return None


def _project_tt_renames(paths) -> List[Any]:
    """The project's .tt RENAME declarations (lib/dated_events
    DatedRename: old, new, date, where): a change a line declares is
    booked from it; one that renames OLD elsewhere decides."""
    root = _project_root(paths)
    if root is None:
        return []
    from taxjson.lib.dated_events import tt_renames
    from taxjson.lib.rename_decl import KIND_CRYPTO
    # The resolved events (one per change, lib/dated_events.
    # resolve_renames); a crypto account's coin ticker change never
    # names a stock.
    return [d for d in tt_renames(root)
            if getattr(d, 'kind', '') != KIND_CRYPTO]


def _account_tt_links(paths) -> Dict[tuple, str]:
    """{(OLD, NEW): where} of the `.tt` SPLIT rows of the account folder
    of `paths` (inputs/<account>/) that move a position to another
    symbol (`SPLIT <date> <time> OLD NEW <ratio>`): the account's own
    rows already book that change (audit review H6)."""
    from taxjson.lib.tt_format import strip_tt_comment, tt_lines
    from taxjson.lib.dated_events import tt_files
    from taxjson.lib.futures_prefixes import canonical_futures_symbol
    out: Dict[tuple, str] = {}
    dirs = []
    for path in paths:
        try:
            d = Path(path).resolve().parent
        except OSError:
            continue
        from taxjson.lib import project_layout as _PL
        if _PL.is_account_folder(d) and d not in dirs:
            dirs.append(d)
    for d in dirs:
        for tt in tt_files(d):
            try:
                text = tt.read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError):
                continue        # the .tt stage names an unreadable file
            for n, raw in enumerate(tt_lines(text), 1):
                parts = strip_tt_comment(raw).split()
                if len(parts) < 5 or parts[0] != 'SPLIT':
                    continue
                # As the .tt reader spells them (a future's `/` or `\`
                # `F:`: lib/futures_prefixes, backlog TJ-5150).
                try:
                    old, new = (canonical_futures_symbol(p.upper())
                                for p in parts[3:5])
                except ValueError:
                    continue    # the .tt stage refuses the line, naming it
                if old != new:
                    out.setdefault((old, new), f"{shown_name(tt)}:{n}")
    return out


def tt_reads_text(paths) -> str:
    """What the parse of `paths` reads from the project's .tt files, one
    line each: every RENAME declaration (_project_tt_renames) and the
    account's SPLIT rows that move a position to another symbol
    (_account_tt_links). The run keeps it as the parse's freshness stamp
    (work/<account>_ib_tt.state): the parse declared neither, so a line
    added under `--fast` kept the old ticker-change decision (backlog
    TJ-4129). Each line keeps its file and line number (`where`), so an
    edit that moves one (a line inserted above it) re-parses too; an
    edit that leaves these lines where they are does not."""
    lines = [f"rename\t{d!r}" for d in _project_tt_renames(paths)]
    lines += [f"link\t{old}\t{new}\t{where}" for (old, new), where
              in sorted(_account_tt_links(paths).items())]
    return "".join(f"{ln}\n" for ln in lines)


def _project_distinct(paths) -> frozenset:
    """The DISTINCT pairs of the loaded ticker.map, else the project's."""
    from taxjson.lib.brokerages.base import ticker_map_distinct_pairs
    if ticker_map_loaded():
        return ticker_map_distinct_pairs()
    tmf = _project_map_file(paths)
    if tmf is None:
        return frozenset()
    try:
        from taxjson.lib.map_file import _parse_map_file
        return frozenset(_parse_map_file(tmf)[0].distinct)
    except Exception:                   # the run reports a bad map
        return frozenset()


def _book_conid_renames(parsed) -> None:
    """The SPLIT rows (ratio 1, `symbol_new`, event_source "ib-conid") of
    the ticker changes prepare_files booked, appended once to the
    account's first IB statement — in its broker account's currency of
    the old listing."""
    files = [(p, ex, txs) for p, ex, txs in parsed
             if isinstance(ex, IbBrokerage)]
    if not files:
        return
    ctx = files[0][1].account_context or {}
    events = ctx.get('conid_renames') or []
    if not events:
        return
    from taxjson.lib.price_chain import quote_currency
    _p, ex, txs = files[0]
    acct = next((t.get('account') for _f in files for t in _f[2]
                 if t.get('account')), getattr(ex, 'DEFAULT_ACCOUNT', ''))
    # The account's rows that already move one symbol to another (a
    # corporate action's split or merger with a symbol change): an
    # event they join is theirs, never booked twice (audit review H6;
    # prepare_files already stands down for a corporate action naming
    # both — this is the rows' own word).
    moved = {frozenset((str(t.get('symbol') or '').upper(),
                        str(t.get('symbol_new') or '').upper()))
             for _f in parsed for t in _f[2]
             if t.get('action') == 'SPLIT' and t.get('symbol_new')
             and t.get('symbol_new') != t.get('symbol')}
    todo: List[Dict[str, Any]] = []
    for e in events:
        p = e.get('pending')
        if p is None:
            todo.append(e)
        elif all(pair in moved for pair in p['pairs']):
            # IB's corporate action naming both symbols books the change
            # (its SPLIT row moves OLD to NEW).
            emit_line(p['note'])
        else:
            # It names both but books no symbol change (a CUSIP/ISIN or
            # name change row, unhandled): the contract id's verdict
            # stands — booked when the rows date it, else the ATTENTION
            # line with the .tt line (review 2 #3).
            emit_line(p['msg'])
            todo.extend(p['events'])
    for e in todo:
        if frozenset((e['old'].upper(), e['new'].upper())) in moved:
            emit_line(f"note: the account's IB statements: {e['old']} -> "
                      f"{e['new']} is booked from IB's own corporate-action "
                      f"row, not again from the contract id.")
            continue
        txs.append({
            'action': 'SPLIT', 'date': e['date'], 'time': '00:00:00',
            'date_settle': e['date'], 'symbol': e['old'],
            'symbol_new': e['new'], 'quantity': 1.0, 'price': 0.0,
            'net_amount': 0.0,
            'currency': quote_currency(e['old']) or '',
            'account': acct,
            'description': (f"Ticker change {e['old']}→{e['new']} (IB "
                            f"lists one contract id under both symbols; "
                            f"no disposition: cost, acquisition dates "
                            f"and identity carried)"),
            'event_source': 'ib-conid'})


def _project_map_dated(paths) -> List[Any]:
    """The dated RENAME lines (lib/rename_decl.DatedRename: old, new, date,
    where) of the project's ticker.map (_project_map_file); empty
    without one (or a map that does not parse: the run reports it)."""
    tmf = _project_map_file(paths)
    if tmf is None:
        return []
    try:
        from taxjson.lib.map_file import _parse_map_file
        return list(_parse_map_file(tmf)[0].dated)
    except Exception:                   # the run reports a bad map
        return []


def _ib_account_prescans(paths, named=frozenset()):
    """([(path, rows, prescan)], folds, map_kept) of one IB account's
    statements, its temporary symbols folded onto their tickers
    (_ib_temp_folds over all its statements' instrument lists; `named`:
    the project map's symbols, which decide). A statement that cannot be
    read or prescanned is left out: its account's parse reports it."""
    read: List[tuple] = []
    for path in paths:
        try:
            rows = IbBrokerage._read_rows(Path(path))
            pre = _ib_prescan(rows, shown_name(path))
        except (OSError, UnicodeError, BrokerageParseError, csv.Error):
            continue
        read.append((path, rows, pre))
    syms: Dict[str, set] = {}
    for _p, _r, _pre in read:
        for conid, ss in _pre['stock_conid_syms'].items():
            syms.setdefault(conid, set()).update(ss)
    kept: Dict[str, str] = {}
    folds = _ib_temp_folds(syms, named, kept)
    if not folds:
        return read, folds, kept
    out: List[tuple] = []
    for path, rows, pre in read:
        folded = _ib_fold_rows(rows, folds)
        if folded != rows:
            try:
                rows, pre = folded, _ib_prescan(folded, shown_name(path))
            except BrokerageParseError:
                continue
        out.append((path, rows, pre))
    return out, folds, kept


def _project_ticker_map(paths):
    """(the rename map, the dated RENAME (old, new) pairs) of the
    project's ticker.map (_project_map_file), or None (a statement
    outside a project, no map, or a map that does not parse)."""
    tmf = _project_map_file(paths)
    if tmf is None:
        return None
    try:
        from taxjson.lib.ticker_map_rules import merge_renames
        from taxjson.lib.map_file import _parse_map_file
        tm = _parse_map_file(tmf)[0]
        return (merge_renames(tm, True),
                [(dr.old, dr.new) for dr in tm.dated])
    except Exception:                   # the run reports a bad map
        return None


def _ib_header_map(row: List[str], where: str) -> Dict[str, int]:
    """A section's Header row as a map of its column names. A Header row
    naming no column (two cells: a hand-edited or cut statement) is
    refused: the section's rows were read as blank with no word, and the
    instrument list was lost (backlog TJ-4565 (2); the owner's ruling of
    2026-11-19, TJ-4569 (4))."""
    if not any(c.strip() for c in row[2:]):
        raise BrokerageParseError(
            f"{where}: the header row of section {row[0].strip()!r} names no "
            f"columns; the section cannot be read. Download the statement "
            f"again.")
    return {c: i for i, c in enumerate(row)}


def _ib_prescan(rows, where: str) -> Dict[str, Any]:
    """Read the statement-level context every row branch needs BEFORE
    the row walk (these sections sit after Trades in IB's layout):
    the title, the Financial Instrument Information multipliers /
    option root aliases / real expiries, the account ids, and the Cash
    Report totals."""
    hm: Dict[str, Dict[str, int]] = {}
    out: Dict[str, Any] = {
        'title': '', 'fii': {}, 'fii_all': {}, 'root_alias': {},
        'alias_conids': {},
        'accounts': set(), 'accounts_included': '', 'cash': {},
        'cash_currencies': set(), 'has_cash_report': False,
        'has_order_level': False, 'order_levels': {},
        'stock_isins': {}, 'stock_conid_syms': {}, 'opt_underlying': {},
        'held_rows': [], 'broker_name': '', 'cash_bad': {},
        'conid_rows': [], 'ca_links': [], 'stock_listing': {},
    }
    occ_by_conid: Dict[str, set] = {}
    contract_conids: Dict[tuple, set] = {}
    for lineno, row in enumerate(rows, 1):
        if len(row) < 2:
            continue
        sec, kind = row[0], row[1]
        if kind == 'Header':
            hm[sec] = _ib_header_map(row, f"{where} line {lineno}")
            continue
        if kind != 'Data' or sec not in hm:
            continue
        h = hm[sec]

        def g(col, _row=row, _h=h):
            i = _h.get(col)
            v = _row[i].strip() if i is not None and i < len(_row) else ''
            return _norm_ccy(v) if col == 'Currency' else v

        if sec == 'Trades' and g('DataDiscriminator') == 'Order':
            out['has_order_level'] = True
        if (sec in ('Trades', 'Transfers', 'Open Positions')
                and g('Asset Category') in ('Stocks', 'Warrants')
                and g('Symbol') and g('Currency')):
            # Listings this statement trades or holds (raw symbol,
            # category, currency) — the account-wide income rebind uses
            # them (prepare_files).
            out['held_rows'].append((g('Symbol'), g('Asset Category'), g('Currency'),
                                     sec, g('Date/Time') or g('Date'), g('Quantity') or g('Qty'), g('Direction') or g('Code')))
            _sroot = re.sub(r'\s+', '.', g('Symbol'))
            _sday = (g('Date/Time') or g('Date')).replace(',', ' ').split()
            if _sday and _IB_DATE_RE.match(_sday[0]):
                # A position row of the symbol at that time: the dates a
                # one-contract-id ticker change is read from
                # (_conid_seen, per contract id).
                out['conid_rows'].append((_sroot, ' '.join(_sday[:2]),
                                          'pos'))
        elif (sec not in _IB_NO_EVENT_SECTIONS and 'Description' in h
              and g('Description')):
            # Any other dated row that names a stock (a corporate
            # action, a dividend, withholding, a payment in lieu, a
            # fee): the symbol already existed on that day (audit
            # review H4: NEW's split before its first trade was lost).
            _eday = ''
            for _dc in ('Date/Time', 'Date', 'Report Date'):
                _d = g(_dc).replace(',', ' ').split()
                if _d and _IB_DATE_RE.match(_d[0]):
                    _eday = ' '.join(_d[:2])
                    break
            if _eday:
                _named = _ib_description_tickers(g('Description'))
                # A return of capital moves the position's cost: it
                # dates OLD's last row like a trade (_conid_seen).
                _kind = (_CONID_ROC if sec != 'Corporate Actions'
                         and is_roc_description(g('Description')) else sec)
                for _t in _named:
                    out['conid_rows'].append((_t, _eday, _kind))
                if sec == 'Corporate Actions' and _named:
                    # The symbols one corporate action names together
                    # (its head and its delivered line, IB's `.OLD`
                    # placeholder read as its ticker), and whether
                    # taxjson-corp-actions owns the row (a merger: it
                    # books the change after the election). Any other
                    # such row books the change only if the parse does
                    # (a renaming split's SPLIT row — _book_conid_
                    # renames checks the parsed rows; a CUSIP/ISIN or
                    # name change row is unhandled, review 2 #3).
                    out['ca_links'].append((frozenset(
                        _ib_placeholder_ticker(t) or t for t in _named),
                        _eday[:10], ib_merger_owned(g('Description'))))
        if (sec == 'Trades'
                and g('DataDiscriminator') in ('Order', 'Trade')):
            # Quantity per (category, symbol, currency, account, trade
            # day) and detail level: an execution-level 'Trade' row is a
            # duplicate only when an 'Order' row covers the same listing
            # of the same account on that day (backlog TJ-0965: another
            # account's or currency's Trade row was dropped as one).
            _okey = _ib_level_key(g('Asset Category'), g('Symbol'),
                                  g('Currency'), g('Account'),
                                  g('Date/Time'))
            try:
                _oq = parse_strict_number(g('Quantity'), field='Quantity',
                                          where=where)
            except BrokerageParseError:
                _oq = 0.0
            _lv = out['order_levels'].setdefault(_okey, {})
            _lv[g('DataDiscriminator')] = _lv.get(
                g('DataDiscriminator'), 0.0) + _oq
        acct = g('Account')
        if acct and 'Total' not in acct:
            out['accounts'].add(acct)
        if sec == 'Statement' and g('Field Name') == 'Title':
            out['title'] = g('Field Value')
        elif sec == 'Statement' and g('Field Name') == 'BrokerName':
            # The IB entity that carries the account ("Interactive
            # Brokers Canada Inc.", "Interactive Brokers LLC"): the
            # payer of a payment in lieu (a neutral fact; s.260 is a
            # Canada-project rule in characterize/income_dating).
            out['broker_name'] = g('Field Value')
        elif sec == 'Account Information':
            fn = g('Field Name')
            if fn == 'Account':
                v = g('Field Value').split()[0] if g('Field Value') else ''
                if v:
                    out.setdefault('account_field', set()).add(v)
            elif fn == 'Accounts Included':
                out['accounts_included'] = g('Field Value')
                for a in g('Field Value').split(','):
                    if a.strip():
                        out['accounts'].add(a.strip())
        elif sec == 'Financial Instrument Information':
            cat = g('Asset Category')
            mult_raw = g('Multiplier')
            mult_bad = ''
            try:
                mult = (parse_strict_number(mult_raw, field='Multiplier',
                                            where=where)
                        if mult_raw else None)
            except BrokerageParseError:
                mult, mult_bad = None, mult_raw
            info = {'mult': mult, 'mult_bad': mult_bad,
                    'name': g('Description'), 'expiry': g('Expiry'),
                    'conid': g('Conid'), 'exch': g('Listing Exch'), 'type': g('Type'),
                    'isin': g('Security ID'), 'underlying': g('Underlying')}
            texts = [s.strip() for s in g('Symbol').split(',')
                     if s.strip()]
            if cat in ('Stocks', 'Warrants'):
                for t in texts:
                    _root = re.sub(r'\s+', '.', t)
                    if info['isin']:
                        out['stock_isins'].setdefault(_root, set()).add(
                            info['isin'].upper())
                    if info['conid']:
                        out['stock_conid_syms'].setdefault(
                            info['conid'], set()).add(_root)
            elif info['conid'] and info['underlying']:
                out['opt_underlying'].setdefault(info['conid'], set()).add(
                    info['underlying'])
            if g('Description'):
                texts.append(g('Description'))
            for t in texts:
                out['fii'].setdefault((cat, t), info)
                out['fii'].setdefault((cat, re.sub(r'\s+', ' ', t)), info)
                # Every instrument a symbol names (two companies may
                # share a bare ticker on two markets: a TSX one and an
                # NYSE one): _security_name picks the row's own.
                if cat in ('Stocks', 'Warrants'):
                    for _k in {(cat, t), (cat, re.sub(r'\s+', ' ', t))}:
                        _all = out['fii_all'].setdefault(_k, [])
                        if not any(i['conid'] == info['conid']
                                   and i['name'] == info['name']
                                   for i in _all):
                            _all.append(info)
            if cat == 'Equity and Index Options':
                for t in texts:
                    m = _IB_FII_OCC_RE.match(t)
                    if m:
                        occ_by_conid.setdefault(info['conid'], set()).add(
                            m.groups())
                        contract_conids.setdefault(
                            m.groups()[1:], {}).setdefault(
                            m.group(1), set()).add(info['conid'])
        elif sec == 'Cash Report':
            out['has_cash_report'] = True
            cur = g('Currency')
            line = g('Currency Summary')
            if not cur or cur == 'Base Currency Summary' or not line:
                continue
            out['cash_currencies'].add(cur)
            try:
                tot = parse_strict_number(g('Total'), field='Total',
                                          where=f"{where}: Cash Report "
                                                f"{line} {cur}",
                                          allow_blank=True, blank=0.0)
            except BrokerageParseError:
                # Kept, not skipped: a reconciled line whose broker
                # total cannot be read fails the reconciliation by NAME
                # (it used to compare against an implicit 0.00 — a
                # misleading error, or a silent pass; audit S059-14).
                out['cash_bad'][(line, cur)] = g('Total')
                continue
            out['cash'][(line, cur)] = out['cash'].get((line, cur),
                                                       0.0) + tot
    for _sym, _cat, _cur, *_more in out['held_rows']:
        out['stock_listing'].setdefault(
            re.sub(r'\s+', '.', _sym), _ib_stock_symbol(_cat, _sym, _cur,
                                                        out['fii']))
    out['occ_by_conid'] = occ_by_conid
    out['root_alias'], out['alias_conids'] = _root_aliases(
        occ_by_conid, out['opt_underlying'])
    out['contract_conids'] = contract_conids
    for (_cat, _sym, _cur, _acct, _day), _lv in out['order_levels'].items():
        if ('Order' in _lv and 'Trade' in _lv
                and abs(_lv['Order'] - _lv['Trade']) > 1e-6):
            raise BrokerageParseError(
                f"{where}: Trades {_cat} {_sym} ({_cur}"
                f"{', account ' + _mask_account(_acct) if _acct else ''}) "
                f"on {_day}: the Order rows "
                f"total {_lv['Order']:g} but the execution-level Trade "
                f"rows total {_lv['Trade']:g} — the two detail levels "
                f"disagree; refusing to pick one. Export one detail "
                f"level (Order) only.")
    # A consolidated statement names its members in "Accounts
    # Included"; a single-account one only in "Account".
    if not out['accounts_included']:
        out['accounts'] |= out.get('account_field', set())
    return out

# `Commission Adjustments` description: `Refund (QZA, -150 2024-02-07)`
# — the ticker sits first inside the parenthetical.
_IB_COMM_ADJ_TICKER_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .\-]*?)\s*,')
# ... and the trade it adjusts: `Refund (QZA, 250, 2024-05-14)`.
_IB_COMM_ADJ_TRADE_RE = re.compile(
    r'\(\s*([A-Z0-9][A-Z0-9 .\-]*?)\s*,\s*([-+]?[\d,]*\.?\d+)\s*,?\s*'
    r'(\d{4}-\d{2}-\d{2})\s*\)')


def _ib_trade_key(t: Dict[str, Any], negate: bool = False) -> tuple:
    """A Trades row's identity across overlapping statements of one
    account: symbol, currency, trade date and clock time, quantity and
    price (the money columns may differ — a refund folded in one copy).
    `negate` gives the key of the original a `Ca` row reverses."""
    q = float(t.get('quantity') or 0.0)
    return (t.get('symbol'), t.get('currency'), t.get('date'),
            t.get('time') or '', M.units(-q if negate else q, 9),
            M.units(float(t.get('price') or 0.0), 8))


def _ib_xfer_cancels(t: Dict[str, Any], leg: Dict[str, Any]) -> bool:
    """True when `leg` (a Transfers `Ca` kept as a reversing leg, its
    description suffixed ' (Ca)') reverses the TRANSFER row `t`: same
    symbol and description, the negated quantity, dated on or before
    the cancellation. A description is read without the `[fill #N]` mark
    the parse gives the second of rows alike (TJ-2528)."""
    desc = FILL_MARK_RE.sub('', leg.get('description') or '')
    return (t is not leg and t.get('action') == 'TRANSFER'
            and t.get('symbol') == leg.get('symbol')
            and desc.endswith(' (Ca)')
            and FILL_MARK_RE.sub('', t.get('description') or '')
            == desc[:-len(' (Ca)')]
            and (t.get('date') or '') <= (leg.get('date') or '')
            and abs(float(t.get('quantity') or 0)
                    + float(leg.get('quantity') or 0)) < 1e-9)


def _ib_in_period(ex, t: Dict[str, Any]) -> bool:
    """True unless `t` is dated before its statement's period (a
    rebook of an earlier statement's row)."""
    ps = ex._ib.period_start
    return not ps or (t.get('date') or '') >= ps


def _ib_trade_meta(ex, t: Dict[str, Any]) -> Dict[str, Any]:
    """What a statement's parse knows of its trade row `t` beyond the
    row (`ex._ib.trade_meta`, by the row's id, the row itself kept so the
    id stays its own): `acct`, the broker Account the row names (a
    combined statement's); `fee0`, its commission before any refund
    was folded in; `applied`, the refunds folded in (their keys). Never
    written out: the rows stay the schema's."""
    meta = ex._ib.trade_meta
    e = meta.get(id(t))
    if e is None or e['t'] is not t:
        e = meta[id(t)] = {'t': t, 'acct': '',
                           'fee0': float(t.get('fee') or 0.0),
                           'applied': set()}
    return e


# The columns that carry IB's id of one execution (optional Flex fields),
# the most specific first.
_IB_EXEC_ID_COLS = ('IB Execution ID', 'IBExecID', 'Execution ID',
                    'Trade ID', 'TradeID')
# Each column's namespace: an id says nothing of an id from another one
# (backlog TJ-1165).
_IB_EXEC_NS = {'IB Execution ID': 'exec', 'IBExecID': 'exec',
               'Execution ID': 'exec', 'Trade ID': 'trade', 'TradeID': 'trade'}


def _ib_share_stated_settle(parsed) -> None:
    """A Flex row's stated settle date (backlog TJ-0971) is also the
    date of that execution's copy in a statement with no such column: the
    id hashes the date, so the two copies stayed two trades and one sale
    was booked twice (backlog TJ-1164). Copies are paired by the row's
    identity across statements and broker account, one stated row per
    copy; two statements stating different dates keep their own."""
    def _rows(ex, txs):
        for t in txs:
            if (t.get('action') in ('BUYSELL', 'ASSIGN')
                    and t.get('type') != TRADE_CANCEL_TYPE):
                e = ex._ib.trade_meta.get(id(t))
                yield t, bool(e is not None and e['t'] is t
                              and e.get('settle_stated'))

    files = [(ex, txs) for _p, ex, txs in parsed
             if isinstance(ex, IbBrokerage)]
    stated: Dict[tuple, list] = {}
    for n, (ex, txs) in enumerate(files):
        for t, said in _rows(ex, txs):
            if said:
                stated.setdefault(_ib_trade_key(t), []).append(
                    (n, _ib_row_acct(ex, t), t['date_settle']))
    if not stated:
        return
    for n, (ex, txs) in enumerate(files):
        left = {k: [s for s in v if s[0] != n] for k, v in stated.items()}
        for t, said in _rows(ex, txs):
            if said:
                continue
            cands = left.get(_ib_trade_key(t)) or []
            hit = next((s for s in cands
                        if _ib_accts_agree(s[1], _ib_row_acct(ex, t))), None)
            if hit is not None:
                cands.remove(hit)
                t['date_settle'] = hit[2]


def _ib_number_executions(parsed) -> None:
    """Number the fills of one account's statements that look alike in
    every booked field by IB's execution id (backlog TJ-0966). A parse
    marks the second and later of identical rows of ONE statement
    "[fill #N]", so the books keep each; the same two fills split over
    two statements had no mark, and dedup kept one as an overlapping
    copy. When such rows carry two or more different execution ids,
    each id gets one number across the statements (the ids in sorted
    order: the first unmarked, the next "[fill #2]" ...): one execution
    exported twice stays one row, two executions stay two. Rows without
    an id keep their statement's own marks, unless numbered rows share
    their statement (backlog TJ-1269). Ids of two columns are two
    namespaces (backlog TJ-1165), and two broker accounts are numbered
    apart unless one statement lists both (backlog TJ-1198)
    (_ib_number_group)."""
    groups: Dict[tuple, list] = {}
    for n, (_p, ex, txs) in enumerate(parsed):
        if not isinstance(ex, IbBrokerage):
            continue
        meta = ex._ib.trade_meta
        for t in txs:
            if (t.get('action') not in ('BUYSELL', 'ASSIGN')
                    or t.get('type') == TRADE_CANCEL_TYPE):
                continue
            e = meta.get(id(t))
            xids = (e.get('execs') or {}) if (e is not None
                                              and e['t'] is t) else {}
            base = FILL_MARK_RE.sub('', t.get('description') or '')
            key = tuple(repr(t.get(f)) for f in (
                'action', 'date', 'time', 'symbol', 'quantity',
                'currency', 'price', 'net_amount', 'gross_amount',
                'account', 'type', 'date_settle')) + (base,)
            groups.setdefault(key, []).append(
                (t, xids, base, _ib_row_acct(ex, t), n))
    for rows in groups.values():
        # Accounts one statement lists together share a numbering: the
        # statement's alike rows must stay apart. A row naming no account
        # may be any of them.
        parts: List[set] = []
        for n in {r[4] for r in rows}:
            part = {r[3] for r in rows if r[4] == n}
            for other in [o for o in parts if o & part]:
                part |= other
                parts.remove(other)
            parts.append(part)
        if any('' in part for part in parts):
            parts = [set().union(*parts)]
        for part in parts:
            _ib_number_group([r for r in rows if r[3] in part])


def _ib_number_group(rows: List[tuple]) -> None:
    """Number alike rows (t, ids by namespace, description without a mark,
    ...) by the first namespace in which they carry two or more ids: a
    row with only another namespace's id takes the number of the row
    that lists both ids, else keeps its statement's own mark."""
    for ns in ('exec', 'trade'):
        ids = sorted({r[1][ns] for r in rows if ns in r[1]})
        if len(ids) >= 2:
            break
    else:
        return
    rank = {x: n for n, x in enumerate(ids, 1)}
    alias = {(o, v): r[1][ns] for r in rows if ns in r[1]
             for o, v in r[1].items() if o != ns}
    rest: List[tuple] = []
    numbered = set()
    for t, xids, base, _acct, n in rows:
        x = xids.get(ns) or next((alias[kv] for kv in xids.items()
                                  if kv in alias), '')
        if x:
            numbered.add(n)
            t['description'] = (f"{base} [fill #{rank[x]}]".strip()
                                if rank[x] > 1 else base)
        else:
            rest.append((t, base, n))
    # A row with no id, in a statement whose other rows were just numbered,
    # could share a number with one of them, and alike rows of one statement
    # are separate fills: it takes the next number past the ids (backlog
    # TJ-1269). Elsewhere it keeps its statement's own mark.
    spare: Dict[int, int] = {}
    for t, base, n in rest:
        if n in numbered:
            spare[n] = spare.get(n, len(ids)) + 1
            t['description'] = f"{base} [fill #{spare[n]}]".strip()


def _ib_trade_acct(ex, t: Dict[str, Any]) -> str:
    """The broker Account `ex`'s parse read on trade row `t` ('' when
    the statement has no Account column or `t` is not its row)."""
    e = ex._ib.trade_meta.get(id(t))
    return e['acct'] if e is not None and e['t'] is t else ''


def _ib_row_acct(ex, t: Dict[str, Any]) -> str:
    """The broker Account of `ex`'s row `t`: the one the row names, else
    the statement's only account ('' when neither says)."""
    a = _ib_trade_acct(ex, t)
    if a:
        return a
    accts = ex.statement_accounts()
    return next(iter(accts)) if len(accts) == 1 else ''


def _ib_accts_agree(a: str, b: str) -> bool:
    """False only when two broker accounts are both known and differ."""
    return not a or not b or a == b


def _ib_fold_refund_into(ex, t: Dict[str, Any], amount: float,
                         rkey) -> None:
    """_ib_fold_refund, with the fold recorded in `ex._ib.trade_meta` (the
    commission before the first refund, the refunds applied) and on the
    row's `commission_refunds` (the refund's date, `rkey`'s second
    item, and cash: the cash ledger walks it on that date, backlog
    TJ-0748), kept in date order so overlapping copies read alike."""
    e = _ib_trade_meta(ex, t)
    _ib_fold_refund(t, amount)
    e['applied'].add(rkey)
    t['commission_refunds'] = add_commission_refund(
        t.get('commission_refunds'), commission_refund(rkey[1], amount))


def _ib_refund_hits(adj: Dict[str, Any], txs,
                    acct_of=None) -> List[Dict[str, Any]]:
    """The trade(s) of `txs` a Commission Adjustments refund names
    (ticker, signed quantity, trade date). An exact quantity first; else
    the ONE same-symbol, same-date, same-sign trade whose quantity
    covers the named one — IB names an execution of a statement that
    lists only the Order row (a 'Refund (QZW, -40, ...)' for a
    -50 order, audit A2-0604). `acct_of(t)`: the broker Account of a
    trade row; when the refund and the trade both name one they must be
    the same — two accounts of a combined statement with the same fill
    matched the refund twice and it was folded into neither (backlog
    TJ-0740)."""
    def _base(t):
        return (t.get('action') == 'BUYSELL'
                and t.get('type') != TRADE_CANCEL_TYPE
                and t.get('currency') == adj['currency']
                and _split_known_ext(t.get('symbol'))[0] == adj['ticker']
                and adj['trade_date'] in (
                    t.get('date'), (t.get('broker_time') or '')[:10])
                and not (adj.get('account') and acct_of is not None
                         and acct_of(t)
                         and acct_of(t) != adj['account']))
    cands = [t for t in txs if _base(t)]
    exact = [t for t in cands
             if abs(float(t.get('quantity') or 0) - adj['qty']) < 1e-9]
    if exact:
        return exact
    q = adj['qty']
    return [t for t in cands
            if (float(t.get('quantity') or 0) > 0) == (q > 0)
            and abs(float(t.get('quantity') or 0)) >= abs(q) - 1e-9]


def _ib_option_ref(ex, raw: str, fii, root_alias, aliased_roots,
                   where: str) -> Optional[str]:
    """The OCC symbol (no exchange suffix) of an equity option named in
    a Commission Adjustments description ('QZK 21MAR25 10 C'), or None
    for a stock ticker: the refund of an option trade never matched the
    OCC-coded trade (audit A2-1032)."""
    from taxjson.lib.option_symbols import is_option_symbol
    if not re.search(r'\s', raw or ''):
        return None
    occ = ex._option_symbol(raw, 'Equity and Index Options', fii,
                            root_alias, aliased_roots, where, strict=False)
    return occ if is_option_symbol(occ) else None


def _ib_fold_refund(t: Dict[str, Any], amount: float) -> None:
    """Net a commission refund (cash, positive) into its trade: the fee
    falls, a purchase costs less and a sale brings in more
    (tax-logic CA-ACB-COMMREFUND / US-BASIS-COMMREFUND)."""
    t['fee'] = M.units(float(t.get('fee') or 0.0) - amount, 10)
    if float(t.get('quantity') or 0) > 0:
        t['net_amount'] = M.units(t['net_amount'] - amount, 10)
    else:
        t['net_amount'] = M.units(t['net_amount'] + amount, 10)


@dataclass(eq=False)                    # compared and hashed by identity (TJ-2445)
class _IbParse:
    """One statement's parse state (set up by IbBrokerage.read): the file and the
    containers the parse's methods read and the rows and the finish fill (the
    Corporate Actions effects and the lines they leave, the splits and their
    cash in lieu, the accruals). Kept on
    the parser after the parse: reconcile_files reaches it through `ca_undo`,
    `cil_join` and the deferred side-effect lines (5h (8a)), and reads what
    crosses statements on it (`ex._ib.<field>`: the Ca rows seen, paired,
    unmatched and held, the cash in lieu, the commission refunds, the trades'
    metadata and the period; TJ-2429, TJ-2464). What stays on the parser: the
    prescan `_ib_pre` (the symbol helpers and `statement_accounts` read it, and
    positions_reports sets it without a parse) and the deferred-output hooks
    `ca_side_effects` / `deferred_skip_summary` (reconcile_files' `finally`
    runs them for every parser).
    `read` binds no field's name but `path`, its parameter: a local of a
    field's name would split from the state (tests/test_ib_state.py,
    TJ-2428)."""
    path: Path
    # What the methods reach after the parse too (ca_undo, cil_join, the
    # deferred side-effect lines), and the records the parse pairs them by.
    transactions: List[Dict[str, Any]] = field(default_factory=list)
    # Corporate Actions rows that aren't SPLIT or Spinoff (e.g.
    # Mergers/Acquisitions, name changes) aren't auto-translated to
    # transactions — the row formats are too varied to handle safely
    # without per-event judgement. Track them per ticker so we can
    # surface a warning at end of parse, prompting the user to add a
    # manual TRANSFER entry if the event affects taxable basis.
    unhandled_ca_tickers: Dict[str, int] = field(default_factory=dict)
    # Cash takeovers booked as sales, and merger rows left to
    # taxjson-corp-actions — one note each at end of parse.
    cash_takeovers: List[str] = field(default_factory=list)
    # Warnings about a translated Corporate Actions row, printed at
    # the end of the parse so a later `Ca` of the row withdraws them.
    late_warnings: List[str] = field(default_factory=list)
    corp_owned_rows: List[str] = field(default_factory=list)
    # Corporate Actions rows that are real events nothing books (an
    # option adjustment, a spin-off debit on a short parent, a
    # share-for-share tender): one UNBOOKED line each at the end.
    unbooked_ca: List[str] = field(default_factory=list)
    # Stock dividends booked at $0 cost: one ATTENTION line each.
    stock_dividends: List[str] = field(default_factory=list)
    # (symbol, date, ratio) of SPLIT rows already emitted from the
    # Corporate Actions section — a reverse split's paired negative+
    # positive legs must yield ONE SPLIT row. Each entry keeps the
    # emitted transaction plus the legs' share quantities so the
    # ratio can be refined to the broker's own rounding once both
    # legs have been seen (IB books fractional results to 4 dp:
    # 7,000 shares 1-for-3 becomes 2333.3333, not 7000/3 — using
    # the text ratio leaves ±1e-4 phantom dust in the pool).
    emitted_splits: Dict[tuple, Dict[str, Any]] = field(default_factory=dict)
    # Cash-in-lieu fractions seen BEFORE a split of their symbol
    # within a week — folded into that split's ratio when it is
    # emitted (see _ib_refine_split_ratio); one never claimed is
    # said at the end.
    pending_cil: List[Dict[str, Any]] = field(default_factory=list)
    # Corporate Actions rows already translated, so a later `Ca`
    # (cancellation) row can find and undo its original — a
    # cancelled split/merger/cash-in-lieu used to stay booked.
    ca_effects: List[Dict[str, Any]] = field(default_factory=list)
    pending_ca: List[Dict[str, Any]] = field(default_factory=list)
    # What crosses statements (TJ-2429): the account's cross-statement
    # pass (`_reconcile_files`) reads these on each statement's state;
    # the rows and the end of the parse fill them.
    # Every Corporate Actions `Ca` row of this statement, and the
    # ones paired here with their original's date: an OVERLAPPING
    # statement of the account that holds the original but not the
    # cancellation (a download taken before IB posted it) must undo
    # its copy too, or dedup keeps the cancelled row (audit A2-0023
    # / A2-0024; IbBrokerage.reconcile_files).
    ca_seen: List[Dict[str, Any]] = field(default_factory=list)
    ca_pairs: List[tuple] = field(default_factory=list)
    # Each income and cost row with its line, its broker row's identity
    # and whether its listing came from undated evidence: an overlapping
    # statement's copy of the row may have put it on another listing
    # (`_ib_cross_income_listings`, backlog TJ-5011).
    listed_income: List[Dict[str, Any]] = field(default_factory=list)
    # `Ca` rows whose original never appeared: offered to the account's
    # other statements (`resolve_unmatched_ca`; audit S059-04).
    unmatched_ca: List[Dict[str, Any]] = field(default_factory=list)
    # (symbol, date, fraction) of every cash-in-lieu row booked: an
    # overlapping statement that holds the same row joins it to its
    # own split (reconcile_files).
    cil_seen: set = field(default_factory=set)
    # The cash in lieu no split of this statement claimed, for a split
    # in another statement.
    unmatched_cil: List[Dict[str, Any]] = field(default_factory=list)
    # Trades `Ca` rows (keys of the originals they reverse, with the
    # account), the (original, Ca) pairs this statement consumed, and
    # the Ca rows dated before the period, held for the account's
    # earlier statements (audit A2-0259 / A2-0088 / A2-0886).
    trade_ca_keys: set = field(default_factory=set)
    trade_pairs: List[tuple] = field(default_factory=list)
    held_trade_cas: List[Dict[str, Any]] = field(default_factory=list)
    # Transfers `Ca` rows and the (original, Ca) pairs consumed here (an
    # overlapping statement's copy of the cancelled transfer, audit
    # A2-1038), and the Ca rows held for the account's other statements.
    xfer_ca_keys: set = field(default_factory=set)
    xfer_pairs: List[tuple] = field(default_factory=list)
    held_xfer_cas: List[Dict[str, Any]] = field(default_factory=list)
    # Commission refunds folded into their trade here (repeated on an
    # overlapping statement's copy that lacks the refund row), the
    # refunds this statement holds, and those no trade of it matched
    # (A2-0258 / A2-0088 / A2-0605).
    refund_folds: List[Dict[str, Any]] = field(default_factory=list)
    refund_seen: set = field(default_factory=set)
    unmatched_refunds: List[Dict[str, Any]] = field(default_factory=list)
    # What the parse knows of each trade row beyond the row (`_ib_trade_meta`:
    # its broker Account, its commission before a refund, the refunds folded
    # in), and the statement's period start (a row dated before it is a
    # rebook of an earlier statement's, `_ib_before_period`); the pass reads
    # both (TJ-2464).
    trade_meta: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    period_start: str = ''


@dataclass(eq=False)
class _IbTrade:
    """One Trades (or Options Expirations) row as its handling reads it
    (IbBrokerage._ib_row_trades; 5h (8d), TJ-2448): the row and its section,
    then what each step adds: the cells and the money read, the dates
    (`_ib_trade_dates`), the kind (`_ib_trade_kind`) and the symbol booked
    (`_ib_trade_symbol`), all of which `_ib_trade_book` books."""
    row: List[str]
    header_map: Dict[str, int]
    section: str
    where: str
    asset_cat: str
    pre_disc: str
    symbol: str = ''
    description: str = ''
    currency: str = ''
    date: str = ''
    time: str = ''
    broker_time: str = ''
    date_settle: str = ''
    stated: str = ''                    # the settlement the row states, if any
    qty: float = 0.0
    price: float = 0.0
    proceeds_signed: float = 0.0
    comm_signed: float = 0.0
    gross_proceeds: float = 0.0
    mult: float = 1.0
    comm_fee: float = 0.0
    net_amount: float = 0.0
    action: str = ''
    code: str = ''
    code_tokens: List[str] = field(default_factory=list)
    is_expiry: bool = False
    full_symbol: str = ''


@dataclass(eq=False)
class _IbCa:
    """One Corporate Actions row as its handling reads it
    (IbBrokerage._ib_row_corporate_actions; 5h (8d), TJ-2448): the row, its
    section and its cells' values, the security it delivers (`deliv`) and the
    effect a later `Ca` undoes (`eff`, made with the row: `_ib_ca_record`)."""
    row: List[str]
    header_map: Dict[str, int]
    section: str
    where: str
    currency: str
    description: str
    cat: str                            # the Asset Category
    date: str
    time: str
    qty: float
    val: float
    proceeds: float                     # cash actually paid (cash in lieu, a tender)
    base_ext: str                       # the currency's listing suffix
    fii: Dict[str, Any]                 # the statement's instruments (the prescan's)
    deliv: str                          # the security the row delivers
    eff: Dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.eff = {'desc': self.description, 'date': self.date, 'qty': self.qty,
                    'kind': 'none', 'txs': [], 'consumed': False,
                    'currency': self.currency, 'cat': self.cat}

    def listing_ext(self, ticker: str) -> str:
        """The listing suffix of a corporate action's stock: the S010-06 /
        A2-0081 listing rule of the trades (a TSX USD unit stays SAMPMD.U.TO,
        an LSE USD line .L) — the currency suffix alone put a split or cash
        takeover of ZSP.U on ZSP.U.US while its trades sat on ZSP.U.TO (audit
        A2-0086)."""
        for _raw in (ticker, ticker.replace('.', ' ')):
            _e = _ib_listing_ext(self.cat or 'Stocks', _raw, self.currency, self.fii)
            if _e != self.base_ext:
                return _e
        return self.base_ext


class _IbRow(Row):
    """One row of the statement as read (IbBrokerage.read; 5h (8d)): its cells
    and export line (the protocol's row), its section and row type, and the
    header map in force (the section's latest Header row before it) with that
    header's length."""
    __slots__ = ("section", "kind", "header_map", "header_len")

    def __init__(self, cells: List[str], line: int, section: str, kind: str,
                 header_map: Optional[Dict[str, int]], header_len: int):
        super().__init__(cells, line)
        self.section, self.kind = section, kind
        self.header_map, self.header_len = header_map, header_len


@dataclass(eq=False)                    # compared and hashed by identity
class _IbRows:
    """What the section methods (`_ib_row_*`) share across one statement's
    rows: the prescan's results (`pre`, the statement's parse of its
    instruments and aliases: `pre['fii']`, `pre['root_alias']`), the dealer's
    country, the containers the rows fill and the finish reads (each by its one
    name, `rs.<field>`; TJ-2449), and the values a row leaves for later rows or
    the finish (`_ib_finish`). The parse's alone, unlike `_IbParse`: on
    `self._ib_rows` from read to the template's release (5h (8b), (8d))."""
    pre: Dict[str, Any]
    dealer_country: str
    # Fee rows that lack both a Date column and a "for Mmm YYYY" hint
    # in the description. Collected and reported as one summary line
    # at the end of the parse so the user sees a single audible
    # signal instead of one warning per skipped fee.
    skipped_dateless_fees: List[float] = field(default_factory=list)
    # Full symbols from the Open Positions section, used to seed the
    # income-reattribution holdings map (covers buy-and-hold years with
    # no trade rows, and disambiguates interlisted same-ticker names).
    open_position_syms: set = field(default_factory=set)
    open_position_qty: Dict[str, float] = field(default_factory=dict)
    # Year-end dividend accruals. IB accrues a declared dividend
    # ('Po' code) and reverses the accrual ('Re') once the cash
    # actually posts into the Dividends section. We do NOT treat an
    # accrual as income — it's an estimate (often a stale per-share
    # rate, and denominated in the account base currency rather than
    # the dividend's native currency), and booking it would also risk
    # double-counting once the real Dividends row lands in a later
    # statement. Instead we net Po/Re per dividend and, at end of
    # parse, warn about any accrual that's still open and has no
    # matching posted Dividends row in this file — that's income IB
    # has declared but not yet booked, so the user should re-download
    # a more recent statement before filing.
    accrual_net: Dict[tuple, float] = field(default_factory=dict)
    accrual_meta: Dict[tuple, Dict[str, str]] = field(default_factory=dict)
    # (symbol, pay_date) -> ex date, from the same section.
    accrual_ex: Dict[tuple, str] = field(default_factory=dict)
    # (ticker, date) of every posted Dividends row in this file, used
    # to suppress an accrual warning when the cash dividend is already
    # present. The Dividends row's Date is the dividend's pay date.
    posted_dividend_keys: List[tuple] = field(default_factory=list)
    # The Cash Report tally `_ib_book` adds to while the statement is
    # parsed (`_reconcile_cash_report`); released with the rows (TJ-2435).
    cash_booked: Dict[tuple, float] = field(default_factory=dict)
    # The last Header row's column map per section (read), for the
    # finish's checks; the sections and row types the rows met that
    # the parser does not read, said once (classify, finish).
    header_maps: Dict[str, Dict[str, int]] = field(default_factory=dict)
    unknown_sections: set = field(default_factory=set)
    unknown_row_types: Dict[str, int] = field(default_factory=dict)
    # Assignment / exercise legs: the option side (ASSIGN) and the
    # stock side (a BUYSELL coded A or Ex), paired after the loop.
    assign_option_legs: List[Dict[str, Any]] = field(default_factory=list)
    assign_stock_legs: List[Dict[str, Any]] = field(default_factory=list)
    # Warrant exercises: (warrant leg, where, code) and the share
    # legs coded Ex, paired after the loop (A2-0090).
    warrant_ex_legs: List[Tuple[Dict[str, Any], str, str]] = field(default_factory=list)
    share_ex_legs: List[Dict[str, Any]] = field(default_factory=list)
    # (symbol, ISIN country) of income rows whose country has no
    # suffix mapping (fell back to .US).
    isin_fallback: set = field(default_factory=set)
    # Transfers rows coded `Ca` listed before their original (or
    # whose original is in an earlier statement): consumed by a
    # later original, else kept as a reversing leg at the end.
    pending_xfer_ca: List[Dict[str, Any]] = field(default_factory=list)
    # Commission Adjustments rows that name their trade (ticker,
    # signed quantity, trade date): folded into that trade after the
    # walk (tax-logic CA-ACB-COMMREFUND / US-BASIS-COMMREFUND).
    comm_adjustments: List[Dict[str, Any]] = field(default_factory=list)
    # Trades rows coded `Ca` (cancelled): paired with their original
    # after the loop (lib/trade_cancel).
    trade_cancels: List[Dict[str, Any]] = field(default_factory=list)
    # Tender / voluntary-offer journals (Corporate Actions rows
    # matched by corp_actions.ib_tender_root), keyed by the
    # suffixed ROOT symbol. IB parks tendered shares on a `.TEN`
    # placeholder line with zero-proceeds legs and journals them
    # back (or settles them) on allocation. Zero-proceeds legs are
    # recognized non-events (the position never economically
    # changed); a leg carrying cash is a DISPOSITION and is booked
    # as a sale — never dropped into the unhandled tally. Per root:
    # rows seen, shares still parked on the `.TEN` line, cash
    # dispositions booked, dates — for the end-of-parse notes.
    tender_legs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Option roots booked as their canonical root (IB's post-corporate-
    # action alias, `_alias_root`): one note each per statement.
    aliased_roots: Dict[str, str] = field(default_factory=dict)
    # Statement period end (ISO). An accrual whose pay date is after
    # the period end is just a normal pending dividend, not a missed
    # one — only accruals payable *within* the period are surprising.
    statement_period_end: str = ''
    # The last closing trade that carries IB's Basis: the ClosedLot
    # rows that follow it are its lots (open date, quantity, cost),
    # kept as evidence for `find-missing-history --write-purchases`.
    last_closing: Optional[Dict[str, Any]] = None


def _ib_ca_matches(ca: Dict[str, Any], eff: Dict[str, Any]) -> bool:
    """Whether the `Ca` row `ca` cancels the translated row `eff`."""
    # The currency and asset category are part of the row's
    # identity: a CAD leg's Ca undid a USD-leg split with the
    # same description (audit A2-0603).
    return (not eff['consumed'] and eff['desc'] == ca['desc']
            and abs(eff['qty'] + ca['qty']) < 1e-9
            and (eff.get('currency') or '') == (ca.get('currency')
                                                or '')
            and (eff.get('cat') or '') == (ca.get('cat') or ''))


def _ib_days_apart(a: str, b: str) -> Optional[int]:
    """The days between two ISO dates (`_days_between`, unsigned), None
    when one is not a date."""
    try:
        return abs(_days_between(a, b))
    except (TypeError, ValueError):
        return None


def _ib_pay_distance(meta, date: str) -> Optional[int]:
    """The days from `date` to the nearest pay date of the accrual `meta`,
    None when none reads as a date: how a posting is matched to the
    accrual it pays (`_ib_accrual_for`, `_ib_claim`)."""
    dists = [d for d in (_ib_days_apart(pd, date)
                         for pd in (meta['pay_dates'] or {meta['pay_date']}))
             if d is not None]
    return min(dists) if dists else None


def _ib_claim(meta, window: int, postings, used) -> bool:
    """Claim the nearest unclaimed posting (`postings`, `used`) of the
    accrual `meta` within `window` days; whether one was."""
    best = None
    for _i, (_s, _d) in enumerate(postings):
        if used[_i] or _s != meta['symbol']:
            continue
        dist = _ib_pay_distance(meta, _d)
        if dist is not None and dist <= window and (best is None or dist < best[0]):
            best = (dist, _i)
    if best is None:
        return False
    used[best[1]] = True
    return True


def _ib_cil_key(symbol: str, date: str, frac: float) -> tuple:
    """A cash in lieu's key across the account's statements: the symbol, the
    date and the fraction to six places (TJ-2476)."""
    return (symbol, date, M.units(frac, 6))


def _ib_cil_cancelled(st: "_IbParse") -> set:
    """The cash-in-lieu keys of a statement whose every booked row a `Ca`
    cancelled, in the statement or by the account's pass: derived from its
    effects when asked (TJ-2474), so no path keeps a set in step."""
    done, live = set(), set()
    for e in st.ca_effects:
        if e['kind'] == 'cil':
            (done if e['consumed'] else live).add(
                _ib_cil_key(e['symbol'], e['date'], e['frac']))
    return done - live


def _ib_same_account(mine: set, accts) -> bool:
    """Whether two sets of account ids name one account (a statement's
    `statement_accounts()`, a fact another statement of the account left):
    either side unknown counts as the same (TJ-2466)."""
    return not accts or not mine or bool(set(accts) & mine)


def _ib_note_held(ctx: Dict[str, Any], name: str, pre: Dict[str, Any]) -> None:
    """The listings one statement holds or trades, in the account's
    context (`prepare_files`): `held`, every statement's together (the
    income rebind's names, S060-00), and `held_by_file`, each statement's
    own, for a cost row's listing (backlog TJ-5011): per listing, its
    first dated row in the statement (`first`, the day; `opening`: it is
    a purchase the broker codes an opening, or a transfer in: no units
    before it), what its rows add (`net`) and what the Open Positions
    hold (`open`, None without a row there)."""
    mine = ctx.setdefault('held_by_file', {}).setdefault(name, {})
    for sym, cat, cur, sec, when, qty, code in pre['held_rows']:
        full = _ib_stock_symbol(cat, sym, cur, pre['fii'])
        ctx['held'].add(full)
        info = mine.setdefault(full, {'first': '', 'opening': False,
                                      'net': 0.0, 'open': None})
        try:
            units = float((qty or '0').replace(',', ''))
        except ValueError:
            units = 0.0
        if sec == 'Open Positions':
            info['open'] = (info['open'] or 0.0) + units
            continue
        day = ((when or '').replace(',', ' ').split() or [''])[0]
        if not _IB_DATE_RE.match(day):
            continue
        codes = {c for c in re.split(r'[;,\s]+', (code or '').upper()) if c}
        if sec == 'Transfers':
            # (its 7th cell is the Direction: an arrival adds its units)
            units = -abs(units) if 'OUT' in codes else abs(units)
            arrival = 'OUT' not in codes
        else:
            arrival = units > 0 and 'O' in codes and 'C' not in codes
        info['net'] += units
        if not info['first'] or day < info['first']:
            info.update(first=day, opening=arrival)


def _ib_held_elsewhere(ctx: Dict[str, Any], name: str, accounts: set
                       ) -> Dict[str, List[Dict[str, Any]]]:
    """{listing: its `_ib_note_held` facts in each OTHER statement of the
    broker account} for the statement `name` of `accounts`: another broker
    account's statement in the same folder says nothing of this one's
    holdings."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for other, listings in (ctx.get('held_by_file') or {}).items():
        if other == name or not _ib_same_account(
                accounts, (ctx.get('file_accounts') or {}).get(other)):
            continue
        for full, info in listings.items():
            out.setdefault(full, []).append(info)
    return out


def _ib_could_predate(info: Dict[str, Any], day: str) -> bool:
    """Whether a statement's rows of a listing (`_ib_note_held`) leave room
    for a holding of it on `day`: its first dated row is on or before the
    day, or is after it and neither a coded opening purchase nor a
    transfer in (a sale, an uncoded purchase: units may be older than the
    row); or the Open Positions hold more than its rows add (older units,
    proven: IB codes an addition `O` too); with no dated row, the Open
    Positions hold it, bought no export says when."""
    if info.get('open') is not None and info['open'] > (info.get('net') or 0.0) + 1e-9:
        return True
    return (not info.get('first') or info['first'] <= day
            or not info.get('opening'))


def _ib_income_key(tx: Dict[str, Any]) -> tuple:
    """The broker's row an income or cost row was read from, whatever
    listing it was put on: its kind, date, ticker, ISIN, currency, money
    and wording (an Activity Statement's Dividends row carries no
    transaction id)."""
    return (tx.get('action'), tx.get('type') or '', tx.get('date'),
            _split_known_ext(tx.get('symbol'))[0],
            tx.get('isin') or tuple(tx.get('isins') or ()),
            _norm_ccy(tx.get('currency')), str(tx.get('net_amount')),
            str(tx.get('gross_amount')), tx.get('description') or '')


def _ib_cross_income_listings(files) -> None:
    """One broker income or cost row that two statements of one broker
    account both hold is one row on one listing (pass 6). Each statement
    places an ISIN-named row by its own evidence, so a statement holding
    the ticker's trades and one holding the income alone put the same row
    on two listings, and the books, which keep identical rows once,
    booked both (backlog TJ-5011, the review's overlap case). The copy
    placed from undated evidence takes the listing of the copy the dated
    rows placed, said in an ATTENTION naming both; two copies placed with
    equal standing on different listings are refused: nothing says which
    is right, and the row is never booked twice."""
    def _where(i, e) -> str:
        return (f"{_ib_nm(files, i)} line {e['line']}" if e['line']
                else _ib_nm(files, i))

    for i, (_p, ex, _t) in enumerate(files):
        for j in range(i + 1, len(files)):
            other = files[j][1]
            if not _ib_same_acct(ex, other):
                continue
            theirs: Dict[tuple, list] = {}
            for e in other._ib.listed_income:
                theirs.setdefault(e['key'], []).append(e)
            used: set = set()
            for a in ex._ib.listed_income:
                cands = [b for b in theirs.get(a['key'], ())
                         if id(b) not in used]
                if not cands:
                    continue
                # (an identical copy first: two payments alike on two
                # listings pair each with its own)
                b = next((c for c in cands if c['tx'].get('symbol')
                          == a['tx'].get('symbol')), cands[0])
                used.add(id(b))
                sa, sb = a['tx'].get('symbol'), b['tx'].get('symbol')
                if sa == sb:
                    continue
                what = ('return of capital' if a['tx'].get('action') == 'ADJUST'
                        or a['tx'].get(ISSUER_ROC_KEY) else 'income')
                if a['marked'] == b['marked']:
                    raise BrokerageParseError(
                        f"{_where(i, a)} and {_where(j, b)} are one {what} "
                        f"row of {a['tx'].get('date')} that the two "
                        f"statements put on two listings ({sa}, {sb}), each "
                        f"by its own "
                        f"{'undated' if a['marked'] else 'dated'} evidence: "
                        f"it would be booked twice. Replace both exports "
                        f"with one statement of the account that holds the "
                        f"trades behind both placings: its Open Positions "
                        f"section, or every trade of the ticker from before "
                        f"the holdings' purchases.")
                keep, drop = (b, a) if a['marked'] else (a, b)
                ki, di = (j, i) if a['marked'] else (i, j)
                was = drop['tx'].get('symbol')
                drop['tx']['symbol'] = keep['tx'].get('symbol')
                drop['marked'] = False
                emit_line(
                    f"{ATTENTION_PREFIX} one payment in two exports: "
                    f"{_where(ki, keep)} and {_where(di, drop)} are one "
                    f"{what} row of {keep['tx'].get('date')}. It is booked "
                    f"once, on {keep['tx'].get('symbol')}, where the dated "
                    f"rows of {_ib_nm(files, ki)} place it; "
                    f"{_ib_nm(files, di)} alone had left it on {was}, a "
                    f"listing none of its rows shows held that day. One "
                    f"statement of the account that holds the payment and "
                    f"the ticker's trades, in place of the two, books it "
                    f"the same way.")


def _ib_same_acct(a, b) -> bool:
    """Whether two IB statements are of the same broker account (Account
    Information): statements of different accounts never touch each other."""
    return _ib_same_account(a.statement_accounts(), b.statement_accounts())


def _ib_nm(files, i) -> str:
    """The shown name of the i-th statement of `_reconcile_files`' list."""
    return shown_name(files[i][0])


def _ib_split_cash_refusal(ca: "_IbCa", ticker: str) -> str:
    """The refusal of a split row carrying cash (TJ-4569 (1)): its fix.
    Cash received: the split at no cash, and the cash as a .tt line of
    the kind the statement names, written out (IB's wording of a cash in
    lieu is unverified, _IB_CIL_RE: a real row may stop here). A charge
    (Proceeds below 0) is no sale and no dividend, and no plain .tt line
    fits one the statement does not explain: none is offered (review
    round 2)."""
    sym = f"{ticker}.{ca.listing_ext(ticker)}"
    cash, cur = abs(ca.proceeds), ca.currency
    head = (f"{ca.where}: the split carries "
            f"{'a charge' if ca.proceeds < 0 else 'cash'} (Proceeds "
            f"{M.shown(ca.proceeds, 2):,.2f} {cur}; "
            f"{ca.description[:60]!r}) — refusing to guess what it is. ")
    if ca.proceeds < 0:
        return (head + f"A split moves shares, not cash: the statement must "
                f"explain what the {cash:.2f} {cur} paid for (no sale, no "
                f"dividend). Set this row's Proceeds to 0 (the split at no "
                f"cash), and enter the charge in a .tt file in the account's "
                f"folder as what the statement names it.")
    return (head + f"Set this row's Proceeds to 0 (the split at no cash), "
            f"and write the cash as a line of its own in a .tt file in the "
            f"account's folder, of the kind the statement names: cash in "
            f"lieu of a fractional share as the fraction's sale, `BUYSELL "
            f"{ca.date} {ca.time} {sym} -FRACTION {cur} PRICE {cash:.2f}` "
            f"(PRICE is the cash, {cash:.2f}, divided by the FRACTION); a "
            f"dividend as `DIVIDEND {ca.date} {ca.time} {sym} SHARES {cur} "
            f"0 {cash:.2f}` (SHARES: the shares it was paid on).")


def _ib_ca_key(ca):
    """A Corporate Actions `Ca` row's key across the statements."""
    return (ca['desc'], ca['date'], M.units(ca['qty'], 9),
            ca.get('currency') or '', ca.get('cat') or '')


def _ib_cross_corp_cas(files) -> None:
    """Corporate Actions `Ca` rows across the account's statements: a
    cancellation's original undone wherever a statement holds it (pass 1)."""
    # --- 1. Corporate Actions `Ca` rows.
    cas: Dict[tuple, Dict[str, Any]] = {}
    for i, (_p, ex, _t) in enumerate(files):
        for ca in ex._ib.ca_seen:
            e = cas.setdefault(_ib_ca_key(ca), {
                'ca': ca, 'odate': None, 'seen': set()})
            e['seen'].add(i)
        for ca, odate in ex._ib.ca_pairs:
            cas[_ib_ca_key(ca)]['odate'] = odate
    for e in cas.values():
        ca, seen = e['ca'], e['seen']
        others = [j for j in range(len(files) - 1, -1, -1)
                  if j not in seen and any(
                      _ib_same_acct(files[j][1], files[k][1])
                      for k in seen)]
        odate = e['odate']
        undone = []
        if odate is None:
            # Paired nowhere: the original sits in another
            # statement — the latest one first (audit S059-04).
            for j in others:
                eff = files[j][1].ca_undo(ca)
                if eff:
                    odate = eff['date']
                    undone.append(j)
                    break
        for j in others:
            if j in undone or odate is None:
                continue
            if files[j][1].ca_undo(ca, exact_date=odate):
                undone.append(j)
        src = _ib_nm(files, min(seen))
        for j in undone:
            emit_line(f"note: {src}: IB cancelled (Ca) {ca['desc']!r} "
                  f"({ca['qty']:g} on {ca['date']}); its original in "
                  f"{_ib_nm(files, j)} is undone.")
        if odate is None:
            # No statement holds an original in its period: the Ca
            # undoes the row dated before its own statement's period
            # (held by its parse, audit A2-1559) — on every copy.
            for k in sorted(seen):
                eff = files[k][1].ca_undo(ca, rebook_ok=True)
                if eff:
                    odate = eff['date']
                    emit_line(f"note: {_ib_nm(files, k)}: IB cancelled (Ca) "
                          f"{ca['desc']!r} ({ca['qty']:g} on "
                          f"{ca['date']}); no other statement holds "
                          f"its original — the row of {eff['date']} "
                          f"in this statement is undone.")
        if odate is None:
            emit_line(unmatched_ca_warning(ca))
        else:
            for k in seen:
                files[k][1].resolve_unmatched_ca(_ib_ca_key, _ib_ca_key(ca))


def _ib_cross_trade_cas(files) -> None:
    """Trades `Ca` cancellations: an overlapping statement's copy of a
    cancelled trade dropped too (pass 3)."""
    # --- 3. Trades `Ca` cancellations. One cancellation is the
    # original's key and its broker Account: in a combined statement
    # of two accounts with the same fill, the key alone dropped the
    # other account's copy (backlog TJ-1130).
    pairs: Dict[tuple, int] = {}
    for _p, ex, _t in files:
        per: Dict[tuple, int] = {}
        for o, _c in ex._ib.trade_pairs:
            k = (_ib_trade_key(o), o.get('_acct') or '')
            per[k] = per.get(k, 0) + 1
        for k, n in per.items():
            pairs[k] = max(pairs.get(k, 0), n)
    # A pair naming no Account is the named one's copy: once.
    for k, a in list(pairs):
        if not a and any(k2 == k and a2 for k2, a2 in pairs):
            del pairs[(k, a)]
    for (k, acct), n in pairs.items():
        owners = [i for i, (_p, ex, _t) in enumerate(files)
                  if any(_ib_trade_key(o) == k
                         and _ib_accts_agree(o.get('_acct') or '', acct)
                         for o, _c in ex._ib.trade_pairs)]
        for j, (_p, ex, txs) in enumerate(files):
            if (j in owners
                    or any(ck == k and _ib_accts_agree(ca, acct)
                           for ck, ca in ex._ib.trade_ca_keys)
                    or not any(_ib_same_acct(ex, files[i][1])
                               for i in owners)):
                continue
            hits = [t for t in txs
                    if t.get('action') in ('BUYSELL', 'ASSIGN')
                    and t.get('type') != TRADE_CANCEL_TYPE
                    and _ib_trade_key(t) == k
                    and _ib_accts_agree(_ib_row_acct(ex, t), acct)][-n:]
            if not hits:
                continue
            gone = {id(t) for t in hits}
            txs[:] = [t for t in txs if id(t) not in gone]
            emit_line(f"note: {_ib_nm(files, j)}: the {k[0]} trade of {k[4]:g} @ "
                  f"{k[5]:g} on {k[2]} was cancelled (Ca) by IB in "
                  f"{_ib_nm(files, owners[0])} — dropped from this overlapping "
                  f"statement too.")


def _ib_cross_held_cas(files) -> None:
    """A Trades or Transfers `Ca` row its parse held: the original in the
    account's other statements cancelled, the rebook kept (passes 3b and 4b)."""
    # --- 3b / 4b. A Trades or Transfers `Ca` row that its parse
    # held: IB cancelled a row of an EARLIER statement and rebooked
    # it in this one, both dated before this statement's period. The
    # Ca cancels the original in the account's other statements —
    # on every one that holds it in its period (overlapping copies)
    # — and the rebook stays. Paired in its own statement (as a
    # lone statement is) only when no statement holds the original
    # (audit A2-0886 / A2-1560). It used to pair with the rebook in
    # its own statement, and step 3 then dropped the earlier
    # original as an overlapping copy: neither was booked.
    def _held_cross(match, what):
        done: set = set()
        # A combined statement's Ca names its broker account: never
        # another account's row (backlog TJ-0982), and two accounts' Ca
        # rows alike but for it are two (TJ-2518). One naming no Account
        # (a transfer's, a combined statement's) is a named one's copy,
        # so the named ones go first: whatever the order of the files,
        # each named one cancels its own (TJ-2529).
        order = []
        for i, (_p, ex, txs) in enumerate(files):
            held = ex._ib.held_trade_cas if what == 'trade' else ex._ib.held_xfer_cas
            order += [(i, ex, txs, ca, _ib_row_acct(ex, ca)) for ca in held]
        order.sort(key=lambda e: not e[4])
        for i, ex, txs, ca, _ca_acct in order:
            if not any(t is ca for t in txs):
                continue
            key = (ca.get('symbol'), ca.get('currency'),
                   ca.get('date'), ca.get('time') or '',
                   M.units(float(ca.get('quantity') or 0), 9),
                   ca.get('description'))
            hit = any(k == key and _ib_accts_agree(a, _ca_acct)
                      for k, a in done)
            kept_fee = None

            def _other(ex_t, t, _a=_ca_acct):
                _ta = _ib_row_acct(ex_t, t)
                return bool(_a and _ta and _a != _ta)
            for j, (_p2, ex2, txs2) in enumerate(files):
                if hit or j == i or not _ib_same_acct(ex, ex2):
                    continue
                cands = [t for t in txs2 if match(t, ca)
                         and _ib_in_period(ex2, t)
                         and not _other(ex2, t)]
                if not cands:
                    continue
                same = [t for t in cands
                        if t.get('date') == ca.get('date')
                        and (t.get('time') or '')
                        == (ca.get('time') or '')]
                orig = (same or cands)[-1]
                if what == 'trade':
                    # The commission IB kept stays (backlog
                    # TJ-0742), once.
                    kept_fee = retained_fee(orig, ca)
                for k, (_p3, ex3, txs3) in enumerate(files):
                    if k == i or not _ib_same_acct(ex, ex3):
                        continue
                    dup = [t for t in txs3 if match(t, ca)
                           and _ib_in_period(ex3, t)
                           and not _other(ex3, t)
                           and t.get('date') == orig.get('date')
                           and (t.get('time') or '')
                           == (orig.get('time') or '')]
                    if dup:
                        txs3[:] = [t for t in txs3
                                   if t is not dup[-1]]
                        emit_line(f"note: {_ib_nm(files, i)}: IB cancelled (Ca) "
                              f"the {ca['symbol']} {what} of "
                              f"{-float(ca['quantity']):g} on "
                              f"{orig.get('date')} — its original "
                              f"in {_ib_nm(files, k)} is dropped; the "
                              f"rebooked row in {_ib_nm(files, i)} is kept.")
                hit = True
            if hit:
                done.add((key, _ca_acct))
                txs[:] = [kept_fee if t is ca else t for t in txs
                          if t is not ca or kept_fee is not None]

    from taxjson.lib.trade_cancel import cancels as _cancels
    _held_cross(_cancels, 'trade')
    _held_cross(_ib_xfer_cancels, 'transfer')
    for _p, ex, txs in files:
        nm_ = shown_name(_p)
        held_t = [c for c in ex._ib.held_trade_cas
                  if any(t is c for t in txs)]
        if held_t:
            kept, tpairs, unpaired = pair_cancellations(
                txs, acct_of=lambda t, _ex=ex: _ib_trade_acct(_ex, t))
            txs[:] = kept
            for _o, _c in tpairs:
                emit_line(f"note: {nm_}: IB cancelled (Ca) "
                      f"{trade_cancel_what(_o['quantity'], _c['quantity'])} "
                      f"{_o['symbol']} trade of {_o['quantity']:g} @ "
                      f"{_o['price']:g} on {_o['date']} — no other "
                      f"statement holds its original; the trade and "
                      f"its cancellation are both dropped.")
            for _c in unpaired:
                if any(_c is h for h in held_t):
                    emit_line(f"note: {nm_}: IB cancelled (Ca) a "
                          f"{_c['symbol']} trade of "
                          f"{-_c['quantity']:g} @ {_c['price']:g} on "
                          f"{_c['date']} whose original row is in "
                          f"none of the account's statements — kept "
                          f"as a cancellation leg.")
        for leg in [c for c in ex._ib.held_xfer_cas
                    if any(t is c for t in txs)]:
            cands = [t for t in txs if _ib_xfer_cancels(t, leg)]
            same = [t for t in cands if t.get('date') == leg['date']]
            if cands:
                orig = (same or cands)[-1]
                txs[:] = [t for t in txs
                          if t is not orig and t is not leg]
                emit_line(f"note: {nm_}: IB cancelled (Ca) the "
                      f"{leg['symbol']} transfer of "
                      f"{-leg['quantity']:g} on {orig.get('date')} — "
                      f"no other statement holds its original; the "
                      f"transfer and its cancellation are both "
                      f"dropped.")


def _ib_cross_xfer_cas(files) -> None:
    """Transfers `Ca` cancellations: an overlapping statement's copy of a
    cancelled transfer dropped too (pass 4)."""
    # --- 4. Transfers `Ca` cancellations. Counted as the Trades pass
    # counts its pairs: two transfers alike, both cancelled, are two
    # copies to drop from an overlapping statement (TJ-2519; one was),
    # the second of them marked "[fill #2]" by its statement's parse
    # after the pairs were taken (`FILL_MARK_RE`). The owner is the
    # statement with the most pairs of a key; another drops the owner's
    # cancellations it holds no copy of itself (TJ-2530: one holding the
    # first was skipped, whatever the owner's count).
    owners: Dict[tuple, tuple] = {}
    for i, (_p, ex, _t) in enumerate(files):
        per: Dict[tuple, list] = {}
        for ok, ck in ex._ib.xfer_pairs:
            per.setdefault(ok, []).append(ck)
        for ok, cks in per.items():
            if len(cks) > len(owners.get(ok, (None, ()))[1]):
                owners[ok] = (i, cks)
    if not owners:
        return
    # Each statement's transfers by their key, read once.
    keyed: List[Dict[tuple, list]] = []
    for _p, ex, txs in files:
        by: Dict[tuple, list] = {}
        for t in txs:
            if t.get('action') == 'TRANSFER':
                by.setdefault((t.get('symbol'),
                               FILL_MARK_RE.sub('', t.get('description') or ''),
                               t.get('date'), float(t.get('quantity') or 0)),
                              []).append(t)
        keyed.append(by)
    for ok, (owner, cks) in owners.items():
        for j, (_p, ex, txs) in enumerate(files):
            n = sum(1 for ck in cks if ck not in ex._ib.xfer_ca_keys)
            if (j == owner or not n or ok not in keyed[j]
                    or not _ib_same_acct(ex, files[owner][1])):
                continue
            hits = keyed[j][ok][-n:]
            gone = {id(t) for t in hits}
            txs[:] = [t for t in txs if id(t) not in gone]
            what = ("transfer of" if len(hits) == 1
                    else f"{len(hits)} transfers of")
            emit_line(f"note: {_ib_nm(files, j)}: the {ok[0]} {what} {ok[3]:g} "
                  f"on {ok[2]} {'was' if len(hits) == 1 else 'were'} cancelled "
                  f"(Ca) by IB in {_ib_nm(files, owner)} — dropped from this "
                  f"overlapping statement too.")


def _ib_cross_refunds(files) -> None:
    """Commission refunds: a refund folded into every overlapping copy of
    its trade, an unmatched one offered to the other statements (pass 5)."""
    # --- 5. Commission refunds. One refund is its key and its broker
    # Account: two accounts of a combined statement refunded alike
    # (same currency, date, amount and wording) are two refunds, one
    # kept and the other lost when the key alone named them (backlog
    # TJ-1042, TJ-1043). A row with no Account is the same refund as
    # a named one of that key (one account's own statement and a
    # combined one list it differently).
    folds: Dict[tuple, tuple] = {}
    for i, (_p, ex, _t) in enumerate(files):
        for f in ex._ib.refund_folds:
            folds.setdefault((f['refund'], f['refund_acct']), (f, i))

    def _refund_seen(ex, rkey, acct) -> bool:
        return any(k == rkey and _ib_accts_agree(a, acct)
                   for k, a in ex._ib.refund_seen)

    def _drop_fee_rows(rkey, acct):
        for _p, ex, txs in files:
            for adj in ex._ib.unmatched_refunds:
                if adj['key'] == rkey and _ib_accts_agree(adj['account'],
                                                          acct):
                    txs[:] = [t for t in txs if t is not adj['fee_tx']]

    # The copy of the trade: the same key and broker Account, the
    # same commission BEFORE any refund, this refund not yet in it.
    # Its commission as it stands said nothing: a copy holding
    # another subset of the trade's refunds (its rows in another
    # order) never matched, and the purchase was booked twice
    # (backlog TJ-0760).
    for (rkey, racct), (f, owner) in folds.items():
        for j, (_p, ex, txs) in enumerate(files):
            if (j == owner or _refund_seen(ex, rkey, racct)
                    or not _ib_same_acct(ex, files[owner][1])):
                continue
            hit = next((t for t in txs
                        if t.get('action') == 'BUYSELL'
                        and _ib_trade_key(t) == f['key']
                        and _ib_accts_agree(_ib_row_acct(ex, t),
                                            f.get('acct') or racct)
                        and rkey not in _ib_trade_meta(ex, t)['applied']
                        and abs(_ib_trade_meta(ex, t)['fee0']
                                - f.get('fee0', f['fee'])) < 1e-9),
                       None)
            if hit is not None:
                _ib_fold_refund_into(ex, hit, f['amount'], rkey)
                emit_line(f"note: {_ib_nm(files, j)}: the commission refund of "
                      f"{M.shown(f['amount'], 2):+.2f} on the {f['key'][0]} trade "
                      f"of {f['key'][2]} (in {_ib_nm(files, owner)}) is folded "
                      f"into this overlapping statement's copy too.")
        # An overlapping copy of the REFUND (its trade not in that
        # statement) must not stay as a FEE row next to the fold.
        _drop_fee_rows(rkey, racct)
    open_refunds: Dict[tuple, tuple] = {}
    for i, (_p, ex, _t) in enumerate(files):
        for adj in ex._ib.unmatched_refunds:
            if not any(k == adj['key']
                       and _ib_accts_agree(a, adj['account'])
                       for k, a in folds):
                open_refunds.setdefault((adj['key'], adj['account']),
                                        (adj, i))
    # A copy naming no Account is the named refund's copy: once.
    for k, a in list(open_refunds):
        if not a and any(k2 == k and a2 for k2, a2 in open_refunds):
            del open_refunds[(k, a)]
    for (rkey, racct), (adj, owner) in open_refunds.items():
        hits = []
        for j, (_p, ex, txs) in enumerate(files):
            if j != owner and _ib_same_acct(ex, files[owner][1]):
                hits += [(ex, t) for t in _ib_refund_hits(
                    adj, txs,
                    acct_of=lambda t, _ex=ex: _ib_row_acct(_ex, t))]
        keys = {_ib_trade_key(t) for _ex, t in hits}
        if len(keys) == 1:
            for _ex, t in hits:
                _ib_fold_refund_into(_ex, t, adj['amount'], rkey)
            _drop_fee_rows(rkey, racct)
            emit_line(f"note: {_ib_nm(files, owner)}: commission adjustment of "
                  f"{M.shown(adj['amount'], 2):+.2f} {adj['currency']} on "
                  f"{adj['date']} ({adj['ticker']} {adj['qty']:g} on "
                  f"{adj['trade_date']}) folded into its trade in the "
                  f"account's other statement.")
        else:
            emit_line(f"note: {_ib_nm(files, owner)}: commission adjustment of "
                  f"{M.shown(adj['amount'], 2):+.2f} {adj['currency']} on "
                  f"{adj['date']} ({adj['ticker']} {adj['qty']:g} on "
                  f"{adj['trade_date']}) matches "
                  f"{'no' if not keys else len(keys)} trade(s) in "
                  f"the account's statements — kept as a FEE row, "
                  f"outside the trade's cost; adjust the trade by "
                  f"hand if it is in a taxable account.")


def _ib_join_cash_in_lieu(files) -> None:
    """The account's cross-statement pass, its cash-in-lieu step
    (IbBrokerage._reconcile_files, step 2; `files` its [(path, extractor,
    txs)]): a cash in lieu whose split is in another statement of the account
    joins that split (ex.cil_join), and one no statement's split takes is
    said. A row's entry comes from the statements that hold a LIVE copy of it
    (`seen`: none joins its own copy; `joined`: one joined it to its own
    split). A copy a Ca cancelled, in its statement or in the pass's Ca step,
    is no entry: a fraction no statement holds live is joined to no split and
    said by none (TJ-2463, TJ-2473), while a live copy (IB's rebook in the
    next statement) is offered to the others, the statement that cancelled
    its own included."""
    cils: Dict[tuple, Dict[str, Any]] = {}
    for i, (_p, ex, _t) in enumerate(files):
        gone = _ib_cil_cancelled(ex._ib)
        open_keys = set()
        for c in ex._ib.unmatched_cil:
            key = _ib_cil_key(c['symbol'], c['date'], c['frac'])
            if key not in gone:
                open_keys.add(key)
                cils.setdefault(key, {'c': c, 'joined': False, 'seen': set()})
        for key in ex._ib.cil_seen - gone:
            e = cils.setdefault(key, {
                'c': {'symbol': key[0], 'date': key[1],
                      'frac': key[2], 'where': _ib_nm(files, i)},
                'joined': False, 'seen': set()})
            e['seen'].add(i)
            if key not in open_keys:
                e['joined'] = True
    for key, e in cils.items():
        hit = False
        for j, (_p, ex, _t) in enumerate(files):
            if j in e['seen'] or not any(
                    _ib_same_acct(ex, files[k][1]) for k in e['seen']):
                continue
            if ex.cil_join(e['c']):
                hit = True
                emit_line(f"note: {_ib_nm(files, j)}: {key[0]}: cash in lieu of "
                      f"{key[2]:g} share(s) on {key[1]} (in "
                      f"{e['c']['where']}) joined the split it "
                      f"settles.")
        if not hit and not e['joined']:
            emit_line(_ib_cil_unmatched_note(e['c']))


def _ib_accrual_index(accrual_meta, ext_metas) -> Dict[str, List[Tuple[bool, Any]]]:
    """The accruals a posting may pay, by symbol, each with whether it is
    another statement's: this statement's (`accrual_meta`) first, then the
    account's other statements' (`ext_metas`). Built once per parse, for
    `_ib_accrual_for` (TJ-2432)."""
    index: Dict[str, List[Tuple[bool, Any]]] = {}
    for other, metas in ((False, accrual_meta.values()), (True, ext_metas)):
        for meta in metas:
            index.setdefault(meta['symbol'], []).append((other, meta))
    return index


def _ib_accrual_key(meta) -> tuple:
    """What makes an accrual one dividend across the account's
    statements: its ex date (its pay date where IB gives none), its
    currency and its account."""
    return (meta.get('ex_date') or ('pay', meta.get('pay_date')),
            meta['currency'], meta.get('account') or '')


def _ib_accrual_for(ticker: str, date: str, currency: str, index, account: str = ''):
    """The accruals of the dividends a posting on `date` may pay: same
    ticker, a pay date within a week (the exact date first), the
    posting's currency first — two listings of one ticker can pay
    on the same day (audit S057-22 / S058-05), and IB posts some
    dividends a day or more after the accrued pay date
    (S058-12). This statement's copy of an accrual first, then the
    account's other statements' (`index`, `_ib_accrual_index`). In a
    statement of several accounts, the posting's own account's alone
    (`account`, the row's Account cell; TJ-2598). One accrual, or none,
    is the rule; several are the distributions of one security that
    share the pay date (another ex date each), which nothing on a
    Dividends row tells apart: `_ib_tied` matches the rows to them by
    the statement's evidence (TJ-5013), never the first taken."""
    best: Optional[tuple] = None
    found: Dict[tuple, Any] = {}
    for _other, meta in index.get(ticker, ()):
        dist = _ib_pay_distance(meta, date)
        if not _ib_same_holder(account, meta.get('account')):
            continue
        if dist is None or dist > 7:
            continue
        rank = (dist, meta['currency'] != currency)
        if best is None or rank < best:
            best, found = rank, {}
        if rank == best:
            found.setdefault(_ib_accrual_key(meta), meta)
    # (an accrual a statement gives no ex date is the dated one of its
    # currency, account and rate, not a second distribution)
    dated = [m for m in found.values() if m.get('ex_date')]
    return [m for m in found.values() if m.get('ex_date') or not any(
        d['currency'] == m['currency']
        and _ib_same_holder(d.get('account'), m.get('account'))
        and (not m.get('po_rate') or m.get('po_rate') == d.get('po_rate'))
        for d in dated)]


def _ib_rate_facts(meta) -> tuple:
    """What an accrual says of the shares and the rate: two accruals
    alike in it give a row the same share count and rate."""
    return (meta['currency'], tuple(meta.get('po_rows') or ()),
            meta.get('po_rate') or 0.0, meta.get('po_qty') or 0.0)


def _ib_states_rate(meta, rate) -> bool:
    """Whether `rate`, which a row's description states, is one the
    accrual `meta` states: a Po row's, or its rows' added."""
    own = {M.units(r, 8) for r, _ in meta.get('po_rows') or ()}
    own |= {M.units(g, 8) for g, _ in _ib_gross_rates(meta, {}) if g}
    return M.units(rate or 0.0, 8) in own


def _ib_explains(meta, rows, index, taken) -> Optional[tuple]:
    """Whether the accrual `meta` explains `rows` as the rows it pays,
    as (the keys (`_ib_accrual_key`) of the pay date's other accruals that
    are no part of it, whether it took another accrual's rows to explain
    them, the readings of its cash, `_ib_readings`: None where the cash
    was not what explained them); None: it does not. Every row states a rate of
    its own; or the rows' cash is the accrual's (`_ib_readings`, the
    shares lent counted); or, the rows being of different character and
    `meta` the first of the accruals no row was given to, the cash is
    that of those accruals together (IB accrued a row for each character
    under two ex dates). `taken`: the keys of the accruals given rows.
    An accrual in another currency than the rows says nothing of their
    cash: any may be the one."""
    others = taken - {_ib_accrual_key(meta)}
    apart = frozenset(_ib_accrual_key(m) for _o, m in index.get(meta['symbol'], ())
                      if m is not meta) - {_ib_accrual_key(meta)}
    if any(o.get('currency') != meta['currency'] for o in rows):
        return apart, False, None
    if all(o.get('_rated') and _ib_states_rate(meta, o.get('price')) for o in rows):
        return apart, False, None
    own = _ib_readings(rows, _ib_gross_rates(meta, {}))
    if own:
        return apart, False, own
    free = [m for _o, m in index.get(meta['symbol'], ())
            if _ib_accrual_key(m) not in others
            and m['pay_dates'] & meta['pay_dates'] and m['currency'] == meta['currency']
            and _ib_same_holder(m.get('account'), meta.get('account'))]
    both = (_ib_readings(rows, _ib_gross_rates(meta, index, {id(meta): others}))
            if _ib_split_sides(rows) and free and free[0] is meta else None)
    return (frozenset(others), True, both) if both else None


# The ways of matching a pay date's rows to its accruals that are tried
# (`_ib_tied`): past it the rows' evidence is not weighed.
_IB_TIE_WAYS = 4096


def _ib_tied_ways(rows, found, index) -> Optional[list]:
    """The ways the rows of one pay date (`rows`) can be given to the
    accruals that share it (`found`) that the statement's evidence bears
    out, each (the accrual of each row, {accrual id: the keys of the
    accruals no part of it}): a row that states a rate is given to an
    accrual stating it (where one does), and every accrual given rows
    explains them (`_ib_explains`), so one accrual pays one
    distribution.

    Amounts can fit more than one way by coincidence (a payment in lieu
    whose cash happens to be another accrual's; two distributions on 60
    shares held and 40 lent, which add up as one of both rates as well),
    so the ways are weighed by what else they say, in order, and the
    best are returned (`_ib_tied` refuses where those still differ):
    whole share counts, where the accruals' are whole, before a way that
    needs a fraction of a share; the same shares lent under every accrual
    (or the accruals read as one distribution accrued in parts, a Po row
    for each character) before all the shares lent under one accrual and
    none under the next; then the most accruals given rows of their own.
    Each way is (the accrual of each row, {accrual id: the keys of the
    accruals no part of it}, its reading for a message: `_ib_way_said`).
    Returned: (the best ways, the other ways that need no fraction of a
    share: readings the ranking set aside, which `_ib_tied` says).
    None: more ways than are tried (`_IB_TIE_WAYS`)."""
    from itertools import product
    options = []
    for o in rows:
        hits = [m for m in found if o.get('_rated') and m['currency'] == o.get('currency')
                and _ib_states_rate(m, o.get('price'))]
        options.append(hits or list(found))
    size = 1
    for opt in options:
        size *= len(opt)
    if size > _IB_TIE_WAYS:
        return None
    counted = all(float(q).is_integer() for m in found
                  for q in [m.get('po_qty') or 0.0] + [q for _r, q in m.get('po_rows') or ()])
    ways: list = []
    seen: list = []
    for way in product(*options):
        taken = frozenset(_ib_accrual_key(m) for m in way)
        apart: Dict[int, frozenset] = {}
        whole, lent = True, []
        parts: list = []
        for m in found:
            mine = [o for o, x in zip(rows, way) if x is m]
            if not mine:
                continue
            ok = _ib_explains(m, mine, index, taken)
            if ok is None:
                break
            apart[id(m)] = ok[0]
            parts.append((m, mine, ok[2], ok[1]))
            if ok[2] is not None:
                whole = whole and (not counted or any(
                    _ib_whole_reading(mine, k, g) for k, g in ok[2].items()))
                if not ok[1]:
                    lent.append({M.units(k[1], 4) for k in ok[2]})
        else:
            # (two rows alike, their accruals swapped: one way)
            same = sorted((_ib_income_key(o), id(x)) for o, x in zip(rows, way))
            if same not in seen:
                seen.append(same)
                even = not lent or bool(set.intersection(*lent))
                ways.append(((whole, even, len(apart)), way, apart, parts))
    best = max((w[0] for w in ways), default=None)
    return ([w[1:] for w in ways if w[0] == best],
            [w[1:] for w in ways if w[0] != best and w[0][0]])


def _ib_whole_reading(rows, shares, gross) -> bool:
    """Whether a reading of an accrual's rows (`_ib_readings`: `shares`
    held and lent, at `gross` a share) needs no fraction of a share: the
    two counts, and each row of a side that is of one character (it is
    booked at its own cash over the rate, `_ib_split_row_shares`)."""
    if not all(float(M.units(v, 6)).is_integer() for v in shares):
        return False
    for lieu in (False, True):
        side = [o for o in rows if (o.get('action') == 'DIVIDEND_IN_LIEU') == lieu]
        if not _ib_split_by_character(side) and not all(
                _ib_shares_paid(_ib_paid(o), gross) % 1 == 0 for o in side):
            return False
    return True


def _ib_way_shares(parts) -> Dict[int, tuple]:
    """The (shares, rate) a way of matching gives each rate-less dividend
    row (by the row's id), as the end of the parse books them
    (`_ib_split_row_shares`, `_ib_lone_shares`): what two ways are
    compared by before one set aside is said (`_ib_tied`)."""
    out: Dict[int, tuple] = {}
    for m, mine, reads, _joint in parts:
        if not reads:
            continue
        (held, lent), gross = sorted(reads.items())[0]
        split = _ib_split_sides(mine)
        for o in mine:
            if o.get('_rated') or o.get('action') == 'ADJUST':
                continue
            lieu = o.get('action') == 'DIVIDEND_IN_LIEU'
            side = [x for x in mine if (x.get('action') == 'DIVIDEND_IN_LIEU') == lieu]
            cash, n = abs(_ib_paid(o)), (lent if lieu else held)
            if split and _ib_split_by_character(side) and n:
                got = (n, cash / n)
            else:
                rate = gross if split else (_ib_lone_rate(m, mine) or gross)
                got = (cash / rate, rate)
            out[id(o)] = (M.units(got[0], 4), M.units(got[1], 6))
    return out


def _ib_way_said(parts) -> str:
    """One way of matching a pay date's rows to its accruals, for a
    message: under each accrual, its rows' lines and the shares and rate
    they are then paid on (`parts`: `_ib_tied_ways`)."""
    out = []
    for m, mine, reads, joint in parts:
        lines = ', '.join(str(n) for n in sorted({o['_line'] for o in mine
                                                  if o.get('_line')}))
        cur = m['currency']
        who = (f"the accrual of {m.get('po_qty') or 0:g} shares at "
               f"{m.get('po_rate') or 0:g} {cur}"
               f"{', ex date ' + m['ex_date'] if m.get('ex_date') else ''}")
        if not reads:
            out.append(f"line{'s' if ',' in lines else ''} {lines} under {who}, at the "
                       f"rates the rows state")
            continue
        (held, lent), gross = sorted(reads.items())[0]
        if joint:
            who = (f"the pay date's accruals read as one distribution of "
                   f"{M.units(gross, 8):g} {cur} a share")
        out.append(f"line{'s' if ',' in lines else ''} {lines} under {who}: "
                   f"{held:g} shares held"
                   + (f" and {lent:g} lent" if lent else ""))
    return '; '.join(out)


def _ib_tied_head(rows, found) -> str:
    """How the accruals `found` stand to the rows' date, for a message:
    they share a pay date, or their pay dates are as near it."""
    shared = set.intersection(*(set(m['pay_dates'] or {m['pay_date']}) for m in found))
    if shared:
        day = rows[0].get('date') if rows[0].get('date') in shared else min(shared, key=str)
        return f"{len(found)} distributions share the pay date {day}"
    days = sorted({str(d) for m in found for d in (m['pay_dates'] or {m['pay_date']})})
    return (f"{len(found)} distributions are paid as near the date of "
            f"{rows[0].get('date')} (pay dates {', '.join(days)})")


def _ib_tied_refusal(rows, found, ways, name: str) -> str:
    """The refusal of a pay date's rows that the statement's evidence
    does not match to the accruals sharing it (`_ib_tied`): every row
    named, each accrual, and the fix (the rate in the row's own
    currency)."""
    cur = rows[0].get('currency') or found[0]['currency']
    lines = sorted({o['_line'] for o in rows if o.get('_line')})
    accrued = '; '.join(
        f"{m.get('po_qty') or 0:g} shares at {m.get('po_rate') or 0:g} {m['currency']}"
        f"{', ex date ' + m['ex_date'] if m.get('ex_date') else ''}" for m in found)
    why = ("match them more than one way" if ways
           else "match them no way" if ways is not None
           else "were not weighed (too many ways of matching them)")
    return (
        f"{name} line{'s' if len(lines) != 1 else ''} "
        f"{', '.join(str(n) for n in lines)} (Dividends): {rows[0].get('symbol')}: "
        f"{_ib_tied_head(rows, found)} (Change in Dividend "
        f"Accruals: {accrued}), nothing on a Dividends row names the one it pays, and "
        f"the rows' rates, amounts and shares {why} — refusing to guess the "
        f"distribution each row pays, and with it the row's shares, rate and ex date. "
        f"Write each row's per-share rate into its Description as IB prints it "
        f"(`{cur} 0.10 per Share`), from IB's dividend report or the issuer's notice. "
        f"A row that states its rate is read from it.")


def _ib_tied(rows, found, index, name: str):
    """The accrual each of `rows` pays, where the accruals `found` (two
    or more distributions of one security, `_ib_accrual_for`) share the
    rows' pay date (TJ-5013; before it every row took the first),
    {accrual id: the accruals no part of it} for `_ib_gross_rates`, and
    what there is to say of the match: [(kind, the lines, the words)],
    for `_ib_rows_paid` to say once a security.

    The evidence is the statement's: a rate a row states, the cash
    against each accrual's shares and rate (the shares lent with it), and
    the rows' characters (`_ib_tied_ways`, which weighs the ways the cash
    fits). The best ways give every rate-less row the same accrual facts
    and the same rows beside it: the first is taken, and a row whose ex
    date they do not agree on is left without one, said (`_ex_open`).
    Where the weighing set aside another reading in whole shares that
    gives a row another share count or rate (`_ib_way_shares`), the reading taken and the
    one set aside are both said, with the fix that settles it (`ranked`).
    Where no way is borne out but the choice moves no share count or
    rate (every row states its rate, or the accruals state the same
    shares and rate) the rows are booked the same, without an ex date.
    Anything else is refused, naming the rows and the fix: the first
    accrual is never taken for the match."""
    got = _ib_tied_ways(rows, found, index)
    ways, aside = got if got is not None else (None, [])

    def _shape(way) -> list:
        # (a row in another currency than its accrual takes the share
        # count alone, whatever rows are beside it)
        return [(_ib_rate_facts(m), frozenset(j for j, x in enumerate(way) if x is m))
                if o.get('currency') == m['currency'] else (m.get('po_qty') or 0.0,)
                for o, m in zip(rows, way) if not o.get('_rated')]
    if ways and any(_shape(w[0]) != _shape(ways[0][0]) for w in ways[1:]):
        raise BrokerageParseError(_ib_tied_refusal(rows, found, ways, name))
    open_ex = set()
    said: list = []
    if ways:
        open_ex = {i for i in range(len(rows))
                   if len({w[0][i].get('ex_date') for w in ways}) > 1}
        other = [w for w in aside if _ib_way_shares(w[2]) != _ib_way_shares(ways[0][2])]
        if other:
            cur = rows[0].get('currency') or found[0]['currency']
            lines = sorted({o['_line'] for o in rows if o.get('_line')})
            said.append(('ranked', lines, (
                f"the shares and rates booked are one of {len(other[:2]) + 1} "
                f"readings the dividend rows' amounts fit — "
                f"{_ib_tied_head(rows, found)}, nothing on a Dividends row names "
                f"the one it pays, and the rows' amounts fit them more than one way "
                f"in whole shares. Booked: {_ib_way_said(ways[0][2])}. Set aside: "
                + ' Or: '.join(_ib_way_said(w[2]) + '.' for w in other[:2])
                + f" The reading booked is the one with the same shares lent under "
                f"each accrual, then with the most accruals paid; no amount depends "
                f"on it, the rows' share counts and rates do. To settle "
                f"it, write each row's per-share rate into its Description as IB "
                f"prints it (`{cur} 0.40 per Share`): a row that states its rate is "
                f"read from it, and this is not said again.")))
    elif (all(o.get('_rated') for o in rows)
          or len({_ib_rate_facts(m) for m in found}) == 1):
        ways = [(tuple(found[0] for _o in rows), {}, [])]
        open_ex = (set(range(len(rows)))
                   if len({m.get('ex_date') for m in found}) > 1 else set())
    else:
        raise BrokerageParseError(_ib_tied_refusal(rows, found, ways, name))
    for i in open_ex:
        rows[i]['_ex_open'] = True
    if open_ex:
        lines = sorted({rows[i]['_line'] for i in open_ex if rows[i].get('_line')})
        said.append(('no ex date', lines, (
            f"no ex date recorded for {len(lines)} dividend "
            f"row{'s' if len(lines) != 1 else ''} — "
            f"{_ib_tied_head(rows, found)} (ex dates "
            f"{', '.join(sorted(str(m.get('ex_date') or '?') for m in found))}) and "
            f"nothing in the statement says which of them "
            f"{'these rows pay' if len(lines) != 1 else 'this row pays'}: the shares "
            f"and rate are the same either way; no ex date is recorded for "
            f"{'them' if len(lines) != 1 else 'it'}, so what reads an ex date (a US "
            f"project's short-sale and holding-period rules, the slip audit's T3 "
            f"year) goes by the pay date. Nothing in a statement can say more; a "
            f"statement in which the two are not paid on one day does.")))
    return ways[0][0], ways[0][1], said


def _ib_paid(tx) -> float:
    """The cash a Dividends row paid, signed (a cost reduction holds it negated)."""
    amt = tx.get('net_amount') or 0.0
    return -amt if tx.get('action') == 'ADJUST' else amt


def _ib_shares_paid(cash: float, rate: float, rows: int = 1) -> float:
    """The shares `cash` (signed) at `rate` a share was paid on. The same
    cent-rounding snap as _parse_div_qty_rate: each of the `rows` the
    cash is the sum of is rounded to cents, so the division lands near,
    not on, the true share count."""
    q = M.units(cash / rate, 8)
    nearest = round(q)
    if nearest != 0 and money_close(cash, nearest * rate, abs_tol=0.005 * rows + 1e-9,
                                    rel_tol=0.0):
        q = float(nearest)
    return q


_IB_DESIGNATION_RE = re.compile(r'\(([^()]+)\)')


def _ib_character(tx) -> str:
    """The character IB gives a Dividends row: 'return of capital' (a
    Canadian issuer's, the ADJUST `roc`; a foreign issuer's,
    ISSUER_ROC_KEY; a payment in lieu of one), else the designation its
    description ends in ("(Long-Term Capital Gain)"), an ordinary
    dividend when it names none."""
    if (tx.get('action') == 'ADJUST' or tx.get(ISSUER_ROC_KEY)
            or tx.get('income_label') == PIL_LABEL_RETURN_OF_CAPITAL):
        return 'return of capital'
    # The first parenthesis is the ISIN's.
    named = _IB_DESIGNATION_RE.findall(str(tx.get('description') or ''))[1:]
    name = named[-1].strip().lower() if named else ''
    return 'ordinary dividend' if name in ('', 'ordinary dividend') else name


def _ib_split_by_character(rows) -> bool:
    """Whether `rows` (one side of a distribution: the issuer's rows, or
    the payments in lieu) are of more than one character."""
    return len({_ib_character(o) for o in rows}) > 1


def _ib_rows_paid(transactions, accruals, name: str = ''):
    """The accrual each Dividends row pays (dividends, payments in lieu
    and a Canadian issuer's return of capital; by the row's id, None when
    none), the rows each accrual in the rows' own currency pays (by
    the accrual's id): a distribution's rows, whatever day IB posted each
    (TJ-2598), and, for the accruals that share a pay date with another,
    the accruals no part of each (`_ib_tied`, which matches such a pay
    date's rows to them; TJ-5013). A row of new cash posted on a later day than the pay date
    (`_ib_late_rows`) that the accrual does not account for, where the
    other rows do add up to it, pays an accrual the statements do not
    hold (the next week's distribution): it is left with none, and a
    rate-less one says it has no share count (`no_share_count`)."""
    metas: Dict[int, Any] = {}
    paid: Dict[int, List[Dict[str, Any]]] = {}
    by_id: Dict[int, Any] = {}
    apart: Dict[int, frozenset] = {}
    income: List[Dict[str, Any]] = []
    ties: Dict[tuple, tuple] = {}
    for tx in transactions:
        if not (tx.get('action') in ('DIVIDEND', 'DIVIDEND_IN_LIEU')
                or (tx.get('action') == 'ADJUST' and tx.get('type') == 'roc')):
            continue
        income.append(tx)
        ticker = (tx.get('symbol') or '').rsplit('.', 1)[0]
        found = _ib_accrual_for(
            ticker, tx.get('date') or '', tx.get('currency') or '', accruals,
            tx.get('_acct') or '')
        metas[id(tx)] = found[0] if found else None
        if tx.get('price'):
            tx['_rated'] = True     # its description states it; popped
        if len(found) > 1:
            ties.setdefault(tuple(id(m) for m in found), (found, []))[1].append(tx)
    notes: Dict[tuple, list] = {}
    for found, rows in ties.values():
        way, parts, said = _ib_tied(rows, found, accruals, name)
        apart.update(parts)
        metas.update((id(o), m) for o, m in zip(rows, way))
        for kind, lines, words in said:
            notes.setdefault((kind, rows[0].get('symbol')), []).append((lines, words))
    # Each said once a security (the run echoes an ATTENTION line): the
    # first pay date's words, the other pay dates' rows named after them.
    for (_kind, sym), each in notes.items():
        lines, words = each[0]
        more = sorted(n for ls, _w in each[1:] for n in ls)
        # (the words' first clause is the run's one console line)
        emit_line(
            f"{ATTENTION_PREFIX} {name} line{'s' if len(lines) != 1 else ''} "
            f"{', '.join(str(n) for n in lines)}: {sym}: {words}"
            + (f" The same holds for line{'s' if len(more) != 1 else ''} "
               f"{', '.join(str(n) for n in more)}, of "
               f"{len(each) - 1} more pay date{'s' if len(each) != 2 else ''}."
               if more else ""))
    for tx in income:
        meta = metas[id(tx)]
        if meta is not None and meta['currency'] == tx.get('currency'):
            paid.setdefault(id(meta), []).append(tx)
            by_id[id(meta)] = meta
    for key, rows in paid.items():
        late = _ib_late_rows(rows, by_id[key])
        rest = [o for o in rows if not any(o is x for x in late)]
        rates = _ib_gross_rates(by_id[key], accruals, apart)
        if (late
                and not _ib_readings(rows, rates) and _ib_readings(rest, rates)):
            paid[key] = rest
            for o in late:
                metas[id(o)] = None
                if not o.get('price') and o.get('action') != 'ADJUST':
                    # No share count, said: quantity 0 alone reads as
                    # "the description names none" (`tjs div-estimate`
                    # then borrows the nearest payment's).
                    o['no_share_count'] = NO_SHARE_COUNT_ACCRUAL
    return metas, paid, apart


def _ib_late_rows(rows, meta) -> List[Dict[str, Any]]:
    """The rows of `rows` (those matched to the accrual `meta`) posted on
    a day that is no pay date of it and brought new cash that day: no
    reversal, and no reclassification, whose rows net to nothing."""
    pays = meta['pay_dates'] or {meta['pay_date']}
    late: List[Dict[str, Any]] = []
    for day in sorted({o.get('date') for o in rows} - pays, key=str):
        of_day = [o for o in rows if o.get('date') == day]
        if sum(_ib_paid(o) for o in of_day) > 0.005 * len(of_day) + 1e-9:
            late += of_day
    return late


def _ib_split_sides(rows) -> bool:
    """Whether a distribution's issuer rows, or its payments in lieu, are
    of more than one character."""
    lieu = [o for o in rows if o.get('action') == 'DIVIDEND_IN_LIEU']
    issuer = [o for o in rows if o.get('action') != 'DIVIDEND_IN_LIEU']
    return _ib_split_by_character(issuer) or _ib_split_by_character(lieu)


def _ib_lone_rate(meta, rows) -> float:
    """The accrual's rate for a row that is the whole payment, or a
    share-split part of it (`rows`: the rows the accrual pays): its Po
    row's. Of several Po rows with no Re between them: their rates added
    (IB accrued a row for each character) where the rows bear that out,
    their net cash being the rows' one share count at the sum, or the
    rates they state being the Po rows'; else the one rate the Po rows
    share (a row repeated, or the shares in two rows); else the last
    row's where the cash is its amount (posted again with no Re); else
    0: no rate is known (`_ib_lone_refusal`)."""
    parts = meta.get('po_rows') or ()
    last = meta.get('po_rate') or 0.0
    if len(parts) <= 1:
        return last
    each = {M.units(r, 8) for r, _ in parts}
    added = sum(r for r, _ in parts)
    net = abs(sum(_ib_paid(o) for o in rows))
    # Each row's cash is rounded to the cent.
    cents = 0.005 * sum(1 for o in rows if _ib_paid(o)) + 1e-9
    stated = {M.units(o.get('price') or 0.0, 8) for o in rows if o.get('_rated')}
    if ((len({q for _, q in parts}) == 1 and abs(net - parts[0][1] * added) <= cents)
            or (len(each) > 1 and stated == each)):
        return added
    if len(each) == 1 or abs(net - parts[-1][1] * last) <= cents:
        return last
    return 0.0


def _ib_lone_refusal(tx, meta, rows, name: str) -> str:
    """The refusal of a rate-less row whose accrual states no one rate
    (`_ib_lone_rate`)."""
    cur = meta['currency']
    parts = '; '.join(f"{q:g} shares at {r:g} {cur}" for r, q in meta.get('po_rows') or ())
    net = sum(_ib_paid(o) for o in rows)
    return (
        f"{name} line {tx.get('_line')} (Dividends): {tx.get('symbol')}: the row states "
        f"no per-share rate, and the Change in Dividend Accruals for the distribution "
        f"of {meta.get('pay_date') or tx.get('date')} has Po rows of different rates "
        f"({parts}) that its rows' {M.shown(net, 2):.2f} {cur} bears out neither added "
        f"(the parts of one rate) nor as the last (the accrual posted again) — "
        f"refusing to guess the rate the row was paid at. Write the row's per-share "
        f"rate into its Description as IB prints it (`{cur} 0.10 per Share`), from "
        f"IB's dividend report or the issuer's notice. A row that states its rate is "
        f"read from it.")


def _ib_readings(rows, rates) -> Dict[Tuple[float, float], float]:
    """The readings of a distribution's rows an accrual bears out, as
    {(the issuer's rows' shares, the payments in lieu's): the gross
    rate}, for each (gross rate, shares) it may state (`rates`): the two
    sides' net cash adds up to the accrual, or the issuer's rows' alone
    does (IB accrued the shares held)."""
    lieu = [o for o in rows if o.get('action') == 'DIVIDEND_IN_LIEU']
    issuer = [o for o in rows if o.get('action') != 'DIVIDEND_IN_LIEU']
    net_i, net_l = sum(_ib_paid(o) for o in issuer), sum(_ib_paid(o) for o in lieu)
    # Each row's cash is rounded to the cent.
    cents = 0.005 * sum(1 for o in rows if _ib_paid(o)) + 1e-9
    found: Dict[Tuple[float, float], float] = {}
    for gross, qty in rates:
        if not (gross > 0 and qty > 0):
            continue
        lent = _ib_shares_paid(abs(net_l), gross, len(lieu))
        if abs(abs(net_i + net_l) - qty * gross) <= cents:
            held = _ib_shares_paid(abs(net_i), gross, len(issuer))
            found[(held, lent)] = gross
        if abs(abs(net_i) - qty * gross) <= cents:
            found[(qty, lent)] = gross
    return found


def _ib_gross_rates(meta, index, apart=None) -> List[Tuple[float, float]]:
    """The (gross rate, shares) a distribution's accrual may state: its
    last Po row's (the rule for a lone row), and, where IB accrued the
    distribution in parts (a Po row for each character, under one ex date
    or two, the pay date the same), the parts' rates added, when they are
    of one share count; where its Po rows share one rate and not one
    share count (the shares in two rows, or the count posted again with
    no Re), that rate on the counts added. The cash decides between them
    (`_ib_readings`). `apart`: {accrual id: the keys (`_ib_accrual_key`)
    of the pay date's accruals that are distributions of their own, with
    rows of their own, and so no part of it} (`_ib_tied`)."""
    own = list(meta.get('po_rows') or ())
    parts, seen = list(own), {(meta.get('ex_date'), tuple(own))}
    skip = (apart or {}).get(id(meta), ())
    for _other, m in index.get(meta['symbol'], ()):
        key = (m.get('ex_date'), tuple(m.get('po_rows') or ()))
        if (m is meta or key in seen or m['currency'] != meta['currency']
                or _ib_accrual_key(m) in skip
                or not _ib_same_holder(m.get('account'), meta.get('account'))
                or not (m['pay_dates'] & meta['pay_dates'])):
            continue
        seen.add(key)
        parts += list(m.get('po_rows') or ())
    out = [(meta.get('po_rate') or 0.0, meta.get('po_qty') or 0.0)]
    for rows in (own, parts):
        if len(rows) > 1 and len({q for _, q in rows}) == 1:
            out.append((sum(r for r, _ in rows), rows[0][1]))
    if len({r for r, _ in own}) == 1 and len({q for _, q in own}) > 1:
        out.append((own[0][0], sum(q for _, q in own)))
    return list(dict.fromkeys(out))


def _ib_same_holder(a, b) -> bool:
    """Whether two Account cells may name one account: an unknown one
    (a statement of one account has no such column) is any."""
    return not a or not b or a == b


def _ib_distribution_shares(rows, meta, index, name: str, apart=None):
    """The shares the rows one accrual pays (`_ib_rows_paid`) were paid
    on, when IB paid the distribution as rows of different character (a
    return of capital or a capital gain beside an ordinary dividend;
    TJ-2598): (the issuer's rows' shares, the payments in lieu's, the
    distribution's gross rate, the refusal of a row they do not give);
    0 where not needed, or not known. None when no side is
    split by character: each row is the whole payment or a share-split
    part of it (a payment in lieu beside a dividend, shares lent), and
    the accrual's rate is its own.

    A distribution split by character is paid on the same shares in each
    row, so the accrual's gross rate (the rows' rates added) is no row's
    rate: each row is at its side's share count and its own rate. With no
    payment in lieu, the issuer's rows are at the shares accrued (where
    the accrual states one count). With
    shares lent, a side's shares are its net cash (a reversed row and its
    repost net out) over the gross rate, which the statement must bear
    out: the two sides add up to the accrual, or the issuer's rows alone
    do (IB accrued the shares held; `_ib_readings`); so must it where a row
    of new cash was posted after the pay date. Where no reading explains the
    distribution, or two do, a rate-less row of a side split by character
    is refused (`_ib_split_row_shares`): its count would be a guess."""
    if not _ib_split_sides(rows):
        return None
    rates = _ib_gross_rates(meta, index, apart)
    refusal = _ib_distribution_refusal(rows, meta, rates, name)
    lieu = [o for o in rows if o.get('action') == 'DIVIDEND_IN_LIEU']
    if (not lieu and rates[0][1] and len({q for _, q in rates}) == 1
            and not _ib_late_rows(rows, meta)):
        return rates[0][1], 0.0, 0.0, refusal   # no shares lent: the shares accrued
    found = _ib_readings(rows, rates)
    if len(found) == 1:
        (held, lent), gross = next(iter(found.items()))
        return held, lent, gross, refusal
    return 0.0, 0.0, 0.0, refusal


def _ib_split_row_shares(tx, rows, held, lent, gross, refusal, meta, name) -> None:
    """Give a rate-less row of a distribution split by character
    (`_ib_distribution_shares`: `held`, `lent`, `gross`, `refusal`) its
    shares, signed as its cash, and its rate: on a side of more than one
    character, the side's shares and the row's own rate, refused where
    the shares are not known (or the side's rows net to nothing); on a
    side of one, its cash over the distribution's gross rate (not known:
    the accrual's, the rule of a lone row, `_ib_lone_shares`)."""
    amt = tx['net_amount']
    in_lieu = tx.get('action') == 'DIVIDEND_IN_LIEU'
    side = [o for o in rows if (o.get('action') == 'DIVIDEND_IN_LIEU') == in_lieu]
    shares = lent if in_lieu else held
    if not _ib_split_by_character(side) and gross:
        tx['price'] = gross
        tx['quantity'] = _ib_shares_paid(amt, gross)
    elif not _ib_split_by_character(side):
        _ib_lone_shares(tx, meta, rows, name)
    elif shares > 0:
        tx['quantity'] = shares if amt > 0 else -shares
        tx['price'] = M.units(abs(amt) / shares, 8)
    else:
        raise BrokerageParseError(refusal)


def _ib_lone_shares(tx, meta, rows, name: str) -> None:
    """Give a rate-less row that is the whole payment, or a share-split
    part of it, its shares and rate: its cash over the accrual's rate
    (`_ib_lone_rate`; `rows`: the rows the accrual pays), refused where
    the accrual states no one rate."""
    rate = _ib_lone_rate(meta, rows)
    if not rate:
        raise BrokerageParseError(_ib_lone_refusal(tx, meta, rows, name))
    tx['price'] = rate
    tx['quantity'] = _ib_shares_paid(tx['net_amount'], rate)


def _ib_distribution_refusal(rows, meta, rates, name: str) -> str:
    """The refusal of a distribution paid as rows of different character
    whose accrual does not give the shares (`_ib_distribution_shares`):
    every row of it named, and the fix, which asks for the rates alone."""
    cur = meta['currency']
    lines = sorted({o['_line'] for o in rows if o.get('_line')})
    kinds = sorted({_ib_character(o) for o in rows})
    accrued = '; '.join(
        f"{q:g} shares at {g:g} {cur}, {M.shown(q * g, 2):.2f} {cur}" if q
        else f"{g:g} {cur} a share, no share count"
        for g, q in dict.fromkeys(rates))
    net = sum(_ib_paid(o) for o in rows)
    lieu = sum(1 for o in rows if o.get('action') == 'DIVIDEND_IN_LIEU')
    return (
        f"{name} line{'s' if len(lines) != 1 else ''} "
        f"{', '.join(str(n) for n in lines)} (Dividends): {rows[0].get('symbol')}: IB "
        f"paid the distribution of {meta.get('pay_date') or rows[0].get('date')} as "
        f"{len(rows)} rows of different character ({', '.join(kinds)}"
        f"{f'; {lieu} of them in lieu, shares lent' if lieu else ''}), not every one "
        f"stating a per-share rate, and the Change in Dividend Accruals for it "
        f"({accrued}) does not account for their {M.shown(net, 2):.2f} {cur} — "
        f"refusing to guess the shares each row was paid on. Write each row's "
        f"per-share rate into its Description as IB prints it (`{cur} 0.10 per "
        f"Share`): the rate of the row's own character, from IB's dividend report "
        f"or the issuer's notice (a payment in lieu's: the rate of what it "
        f"replaces). A row that states its rate is read from it.")


def _ib_root_forms(r):
    # The option root as the stock leg may spell it: itself, the
    # adjusted contract's root without OCC's digit (QZX1 ->
    # QZX), a class share without its dot (SAMPLCB vs SAMPLC.B) —
    # the exact-root match left those legs on two settle dates
    # (audit A2-1028).
    base = re.sub(r'\d+$', '', r) or r
    return [r, base]


# ------------------------------------------------------------ the cash events
# The FX-on-cash ledger v2 (lib/fx_cash_v2, tax-logic CA-FX-07 /
# US-FX-03) reads what a statement says about CASH that the position book
# leaves out: Trades/Forex conversions, Deposits & Withdrawals, and the
# Cash Report's Starting/Ending Cash per currency. IbBrokerage.read notes
# the rows they come from (`_ib_cash_rows`) and the finish builds them
# (`_ib_cash_events_of`): lib/cash_readers/ib.py read the export a second
# time for them until PLAN step 16 commit 10.
_IB_INTERNAL_RE = re.compile(r'^\s*Internal Transfer\b', re.IGNORECASE)
_IB_ADVANCE_RE = re.compile(r'^\s*(?:Adjustment:\s*Deposit Advance|'
                            r'Cancellation\b)', re.IGNORECASE)
_IB_CUR_RE = re.compile(r'^[A-Z]{3}$')
# The sections every Data row of which is a cash row, and what it is to
# the finish.
_IB_CASH_SECTIONS = {'Deposits & Withdrawals': 'deposit', 'Cash Report': 'cash',
                     'Forex Balances': 'fxbal'}


@dataclass(frozen=True, eq=False)
class _IbCashRow:
    """A row the statement's cash events are read from (`_ib_cash_rows`,
    which IbBrokerage.read notes): what it is to the finish (`what`: an
    `account` the statement names, a Statement `period` row, a `forex`
    fill, a `deposit` (Deposits & Withdrawals), `cash` (Cash Report) or
    `fxbal` (Forex Balances) row), its export line, its cells by its
    section's header (`d`) and that header (a Forex fill's commission
    column is the header's first `Comm in XXX`)."""
    what: str
    line: int
    d: Dict[str, str]
    header: Tuple[str, ...] = ()


def _ib_cash_order_level(rows: List[List[str]]) -> bool:
    """True when the statement has a Trades Order row. Its Forex fills are
    read at one detail level, the security parser's rule (`_ib_trade_level`,
    prescan `has_order_level`): the Order rows when the statement has any,
    else the Trade (execution) rows — read only at the Order level, a
    statement of Trade rows moved no cash here while the parser reconciled
    it (backlog TJ-0858). Read as the cash rows are (`_ib_cash_rows`)."""
    hdr: Dict[str, List[str]] = {}
    for row in rows:
        if len(row) < 3:
            continue
        if row[1].strip() == 'Header':
            hdr[row[0].strip()] = row[2:]
        elif row[0].strip() == 'Trades' and row[1].strip() == 'Data' \
                and (dict(zip(hdr.get('Trades') or [], row[2:])).get(
                    'DataDiscriminator') or '').strip() == 'Order':
            return True
    return False


def _ib_cash_what(sec: str, d: Dict[str, str], has_order_level: bool) -> str:
    """What a Data row of section `sec` (its cells `d`) is to the cash
    events (`_IbCashRow.what`), '' for none: a Statement Period row, a
    Trades Forex fill of the statement's detail level, any row of
    `_IB_CASH_SECTIONS`."""
    if sec == 'Statement' and d.get('Field Name') == 'Period':
        return 'period'
    if sec == 'Trades' and d.get('Asset Category') == 'Forex':
        disc = (d.get('DataDiscriminator') or '').strip()
        return ('forex' if disc == 'Order'
                or (disc in ('', 'Trade') and not has_order_level) else '')
    return _IB_CASH_SECTIONS.get(sec, '')


def _ib_cash_rows(rows: List[List[str]]) -> List[_IbCashRow]:
    """The statement's rows its cash events are read from, in the file's
    order (IbBrokerage.read notes them, `note_cash`; the finish builds the
    events, `_ib_cash_events_of`). The rows as the export holds them, read
    as lib/cash_readers/ib.py read them until PLAN step 16 commit 10: IB's
    temporary symbols not folded (`_ib_fold_rows`), a quoted cell's line
    ends as text mode reads them, the section and row type stripped, a Data
    row's cells by its section's latest Header row from the third cell on.
    Each account the statement names, once (Accounts Included, a row's
    Account column but a total's); the Statement Period rows; the Forex
    fills of the statement's detail level (`_ib_cash_order_level`); every
    Deposits & Withdrawals, Cash Report and Forex Balances row. What their
    cells hold is the finish's to read: one it cannot read is the file's
    cash problem, never a refused parse."""
    rows = [[c.replace('\r\n', '\n').replace('\r', '\n') for c in r]
            if any('\r' in c for c in r) else r for r in rows]
    has_order_level = _ib_cash_order_level(rows)
    headers: Dict[str, List[str]] = {}
    seen: set = set()
    out: List[_IbCashRow] = []
    for lineno, row in enumerate(rows, 1):
        if len(row) < 3:
            continue
        sec, kind = row[0].strip(), row[1].strip()
        if kind == 'Header':
            headers[sec] = row[2:]
            continue
        if kind != 'Data':
            continue
        h = headers.get(sec) or []
        d = dict(zip(h, row[2:]))
        # The broker accounts the statement covers (Accounts Included,
        # the rows' Account column): a consolidated statement is one book.
        names = set()
        acct = (d.get('Account') or '').strip()
        if acct and 'total' not in acct.lower():
            names.add(acct)
        if sec == 'Account Information' \
                and d.get('Field Name') == 'Accounts Included':
            names |= {a.strip() for a in (d.get('Field Value') or '').split(',')
                      if a.strip()}
        out += [_IbCashRow('account', lineno, {'Account': a})
                for a in sorted(names - seen)]
        seen |= names
        what = _ib_cash_what(sec, d, has_order_level)
        if what:
            out.append(_IbCashRow(what, lineno, d, tuple(h)))
    return out


def _ib_cash_number(v: str, what: str, where: str) -> float:
    """A cash row's number (a blank one 0), refused as the parser refuses one."""
    return parse_strict_number(v, field=what, where=where, allow_blank=True,
                               blank=0.0)


def _ib_forex_fill(c: _IbCashRow, where: str) -> Dict[str, Any]:
    """A Forex fill noted (`_IbCashRow` 'forex') as `_ib_forex_events`
    reads it: its pair, date and clock, quantity, proceeds, commission and
    its currency, price and whether it is a `Ca` row. BrokerageParseError
    for a symbol not BASE.QUOTE, a date or a number it cannot read."""
    d = c.d
    sym = (d.get('Symbol') or '').strip()
    base, _, quote = sym.partition('.')
    if not base or not quote:
        raise BrokerageParseError(
            f"{where}: Forex symbol {sym!r} is not BASE.QUOTE")
    # The clock too: one day's conversions are walked in the order IB
    # executed them, not the order of the rows (backlog TJ-0859).
    day, clock = _ib_split_datetime(d.get('Date/Time', ''), where)
    qty = _ib_cash_number(d.get('Quantity', ''), 'Quantity', where)
    proceeds = _ib_cash_number(d.get('Proceeds', ''), 'Proceeds', where)
    comm_col = next((col for col in c.header
                     if re.match(r'^Comm in [A-Z]{3}$', col or '')), None)
    if comm_col:
        comm, ccur = _ib_cash_number(d.get(comm_col, ''), comm_col, where), \
            comm_col[-3:]
    else:
        comm = _ib_cash_number(d.get('Comm/Fee', ''), 'Comm/Fee', where)
        ccur = (d.get('Currency') or quote).strip()
    return {
        'where': where, 'sym': sym, 'base': base, 'quote': quote,
        'day': day, 'time': clock, 'qty': qty,
        'proceeds': proceeds, 'comm': comm, 'ccur': ccur,
        'price': (_ib_cash_number(d.get('T. Price', ''), 'T. Price', where)
                  if 'T. Price' in d else None),
        'ca': 'Ca' in re.split(r'[;,\s]+', d.get('Code') or '')}


def _ib_deposit_move(d: Dict[str, str], where: str, name: str
                     ) -> Optional[Dict[str, Any]]:
    """The CASHMOVE of a Deposits & Withdrawals row (internal transfers and
    deposit advances marked for pairing), None for a Total row or a zero
    amount. BrokerageParseError for a date or an amount it cannot read."""
    from taxjson.lib import cash_lines as CL
    cur = (d.get('Currency') or '').strip()
    if not _IB_CUR_RE.match(cur):
        return None                             # Total rows
    desc = (d.get('Description') or '').strip()
    day = (d.get('Settle Date') or d.get('Date') or '').strip()
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', day):
        raise BrokerageParseError(
            f"{where}: Deposits & Withdrawals date {day!r} is not "
            f"YYYY-MM-DD")
    amt = _ib_cash_number(d.get('Amount', ''), 'Amount', where)
    if abs(amt) < 1e-9:
        return None
    internal = ('transfer' if _IB_INTERNAL_RE.match(desc) else
                'advance' if _IB_ADVANCE_RE.match(desc) else '')
    # The description can name a person or an account: keep only its
    # leading words, digits masked.
    short = re.sub(r'\d', '#', ' '.join(desc.split()[:3]))
    ev = CL.move(day, cur, amt, where=name, desc=short, internal=internal)
    ev['line_where'] = where
    return ev


def _ib_forex_cancels(orig: Dict[str, Any], ca: Dict[str, Any]) -> bool:
    """True when the Forex `Ca` row `ca` reverses the fill `orig`: the
    same pair and trade date, the negated quantity, the same price
    (when the statement gives one) — the rule of lib/trade_cancel."""
    if orig['ca'] or orig['sym'] != ca['sym'] or orig['day'] != ca['day']:
        return False
    if abs(ca['qty']) < 1e-12 or abs(orig['qty'] + ca['qty']) > \
            1e-9 * max(1.0, abs(ca['qty'])):
        return False
    if orig['price'] is None or ca['price'] is None:
        return True
    return abs(orig['price'] - ca['price']) <= 1e-6 * max(1.0,
                                                          abs(ca['price']))


def _ib_forex_events(forex: List[Dict[str, Any]], name: str
                     ) -> List[Dict[str, Any]]:
    """The cash events of a statement's Forex fills (`_ib_cash_events_of`): an
    FXCONV each, its commission a FLOW. A fill IB cancelled (a `Ca` row
    reversing it: the same pair, date and price, the negated quantity)
    and its `Ca` row are no conversion — read as two, the round trip
    realized a gain on currency that never changed hands (backlog
    TJ-0743). What the two rows' commissions leave (a commission IB
    kept) stays a fee of its own. Among several originals the one at
    the cancellation's own clock wins, else the latest before it, else
    the first after it (lib/trade_cancel's rule). A `Ca` row whose
    original is not in this statement stays a reversing conversion,
    said in a note."""
    from taxjson.lib import cash_lines as CL
    used: set = set()
    paired: Dict[int, int] = {}
    for ci, ca in enumerate(forex):
        if not ca['ca']:
            continue
        cands = [i for i, f in enumerate(forex)
                 if i not in used and i != ci and _ib_forex_cancels(f, ca)]
        if not cands:
            continue
        pool = [i for i in cands if forex[i]['time'] == ca['time']] \
            or cands
        before = [i for i in pool if i < ci]
        oi = before[-1] if before else pool[0]
        used.update((oi, ci))
        paired[ci] = oi
    out: List[Dict[str, Any]] = []
    for i, f in enumerate(forex):
        sym, where, day = f['sym'], f['where'], f['day']
        if i in used:
            if i not in paired:
                continue            # the original: said with its Ca
            o = forex[paired[i]]
            fees = ([(o['ccur'], o['comm'] + f['comm'])]
                    if o['ccur'] == f['ccur']
                    else [(o['ccur'], o['comm']), (f['ccur'], f['comm'])])
            for ccur, comm in fees:
                if abs(comm) > 1e-9:
                    ev = CL.flow(day, ccur, comm, where=where,
                                 desc=f"Forex {sym} commission kept on a "
                                      f"cancelled order")
                    ev['time'] = f['time']
                    out.append(ev)
            continue
        qty, proceeds = f['qty'], f['proceeds']
        if abs(qty) < 1e-12 or abs(proceeds) < 1e-12:
            continue
        if qty > 0:          # bought BASE, paid QUOTE
            ev = CL.conv(day, f['quote'], proceeds, f['base'], qty,
                         where=where, desc=f"Forex {sym}")
        else:                # sold BASE, received QUOTE
            ev = CL.conv(day, f['base'], qty, f['quote'], proceeds,
                         where=where, desc=f"Forex {sym}")
        ev['time'] = f['time']
        out.append(ev)
        if f['ca']:
            out.append({"kind": "NOTE", "date": day, "settle": day,
                        "currency": "", "where": name, "origin": "broker",
                        "text": f"{where}: IB cancelled (Ca) a {sym} "
                                f"conversion of {-qty:g} on {day} whose "
                                f"original is not in this statement — "
                                f"kept as a reversing conversion"})
        if abs(f['comm']) > 1e-9:
            # `_fee_of`: the conversion it was charged on — in the
            # base currency it is part of the amount paid or
            # received (lib/fx_cash_v2, backlog TJ-0456).
            ev = CL.flow(day, f['ccur'], f['comm'], where=where,
                         desc=f"Forex {sym} commission")
            ev['_fee_of'] = where
            ev['time'] = f['time']
            out.append(ev)
    return out


def _ib_cash_close(name: str, members: set, period, cash: Dict[Tuple[str, str], float],
                   fxbal: Dict[str, float]) -> List[Dict[str, Any]]:
    """The statement's events after its rows' (`_ib_cash_events_of`): the
    note that a statement of several accounts is one cash book, the period
    (PERIOD), the Cash Report's balances (CASHBAL: Starting Cash at the
    end of the day before the period, Ending Cash at its last day) and the
    Forex Balances that differ from them (a `note`). BrokerageParseError
    for a Cash Report with no Statement Period."""
    from taxjson.lib import cash_lines as CL
    out: List[Dict[str, Any]] = []
    if len(members) > 1:
        # Its Cash Report has no Account column: one combined balance,
        # so the ledger keeps the statement's accounts in one book — a
        # debt in one and cash in another net out. Said, not silent
        # (pre-release review; a per-account split needs the accounts'
        # own statements).
        from taxjson.lib.privacy import hash_broker_account
        ids = ", ".join(sorted("#" + hash_broker_account(a)[:4]
                               for a in members))
        _nd = period[1].isoformat() if period is not None else ""
        out.append({"kind": "NOTE", "date": _nd, "settle": _nd,
                    "currency": "", "where": name, "origin": "broker",
                    "text": f"{name}: one IB statement of "
                            f"{len(members)} accounts ({ids}) is one cash "
                            f"book — its Cash Report is their combined "
                            f"balance, so a debt in one account and cash "
                            f"in another net out (each account's own "
                            f"statement, exported apart, keeps them "
                            f"apart)"})
    if cash and period is None:
        raise BrokerageParseError(
            f"{name}: a Cash Report but no Statement Period — the "
            f"balances cannot be dated")
    if period is None:
        return out
    start, end = period
    # The period itself: a row of this statement dated before it (a
    # correction IB posts with the original date) moved its cash inside
    # it (lib/fx_cash_v2 dates the cash there).
    out.append({"kind": "PERIOD", "date": start.isoformat(),
                "settle": start.isoformat(), "start": start.isoformat(),
                "end": end.isoformat(), "where": name,
                "origin": "broker"})
    before = (start - timedelta(days=1)).isoformat()
    for (line, cur), v in sorted(cash.items()):
        day = before if line == 'Starting Cash' else end.isoformat()
        out.append(CL.balance(day, cur, v, where=f"{name}: Cash Report "
                              f"{line}", statement=name))
    for cur, q in sorted(fxbal.items()):
        ending = cash.get(('Ending Cash', cur))
        if ending is not None and abs(ending - q) > 1.0:
            out.append({"kind": "NOTE", "date": end.isoformat(),
                        "settle": end.isoformat(), "currency": cur,
                        "where": name,
                        "text": f"{name}: Forex Balances {cur} "
                                f"{M.shown(q, 2):,.2f} differs from the Cash "
                                f"Report's Ending Cash {M.shown(ending, 2):,.2f}",
                        "origin": "broker"})
    return out


def _ib_cash_events_of(noted: List[_IbCashRow], name: str) -> List[Dict[str, Any]]:
    """The cash events of one IB activity statement (lib/cash_events, the
    ledger v2's), from the rows its parse noted (`_ib_cash_rows`), in their
    order: a CASHMOVE per Deposits & Withdrawals row (`_ib_deposit_move`),
    an FXCONV per Forex order, its commission a FLOW when charged in a
    currency (`_ib_forex_events`), then the closing ones (`_ib_cash_close`):
    the one-book note, PERIOD, CASHBAL from the Cash Report and the Forex
    Balances cross-check. Rows carry no account: a combined statement's
    Cash Report is one balance (no Account column), so its accounts are
    one book — said in a `note` event naming them by hashed id. The first
    cell it cannot read, in the rows' order, raises: the file's cash
    problem (IbBrokerage `_ib_finish_cash`)."""
    out: List[Dict[str, Any]] = []
    period = None
    cash: Dict[Tuple[str, str], float] = {}
    fxbal: Dict[str, float] = {}
    members: set = set()
    # The Forex fills, turned into events once all are read: a `Ca` row
    # and the order it cancels are no conversion (backlog TJ-0743).
    forex: List[Dict[str, Any]] = []
    for c in noted:
        d, where = c.d, f"{name}:{c.line}"
        if c.what == 'account':
            members.add(d['Account'])
        elif c.what == 'period':
            period = _ib_period(d.get('Field Value', ''))
        elif c.what == 'forex':
            forex.append(_ib_forex_fill(c, where))
        elif c.what == 'deposit':
            ev = _ib_deposit_move(d, where, name)
            if ev is not None:
                out.append(ev)
        elif c.what == 'cash':
            line = (d.get('Currency Summary') or '').strip()
            cur = (d.get('Currency') or '').strip()
            if line in ('Starting Cash', 'Ending Cash') \
                    and _IB_CUR_RE.match(cur):
                cash[(line, cur)] = cash.get((line, cur), 0.0) + _ib_cash_number(
                    d.get('Total', ''), 'Total', where)
        elif c.what == 'fxbal':
            cur = (d.get('Currency') or d.get('Description') or '').strip()
            if _IB_CUR_RE.match(cur):
                fxbal[cur] = fxbal.get(cur, 0.0) + _ib_cash_number(
                    d.get('Quantity', ''), 'Quantity', where)
    out.extend(_ib_forex_events(forex, name))
    out.extend(_ib_cash_close(name, members, period, cash, fxbal))
    return out


class IbBrokerage(BaseBrokerage):
    # 'trade' (default) | 'next_day': settle date of futures and futures
    # options ([settings] futures_settle, passed by taxjson-brokerage).
    futures_settle = 'trade'

    # Account-wide context (set by taxjson-brokerage from prepare_files).
    account_context = None
    # Set by taxjson-brokerage when it will run reconcile_files: the
    # Corporate Actions side-effect lines (stock-dividend ATTENTION,
    # cash-takeover NOTE, ...) wait in `ca_side_effects` until the
    # cross-statement Ca pass, so an event another statement cancels
    # prints nothing (re-audit A2-1091).
    defer_ca_messages = False
    ca_side_effects = None
    deferred_skip_summary = None
    # The rows' shared state while a statement is parsed (read to release).
    _ib_rows = None

    def configure(self, settings, *, account_context=None) -> None:
        """The base class's, then the deferral: with the account's context
        (every statement prepared together) the corporate-action messages
        wait for the cross-statement pass (re-audit A2-1091)."""
        super().configure(settings, account_context=account_context)
        self.defer_ca_messages = account_context is not None

    @classmethod
    def prepare_files(cls, paths, tax_year=None, combined=False,
                      taxable=None, project=None, *, symbol_codes=None) -> Dict[str, Any]:
        """Read ALL of one account's IB statements once, before any is
        parsed: statement periods (coverage check below, per broker
        account and against `tax_year` when taxjson-brokerage passes it)
        and the facts a per-file parse cannot see alone. Account-level
        warnings print here, once. `project` ({'account': this label,
        'accounts': {label: [statement paths]}} of the project's OTHER IB
        accounts, taxjson-brokerage --project-statements): a contract
        id's ticker change is decided from every account's rows
        (review 2 #4, _warn_stock_aliases)."""
        ctx: Dict[str, Any] = {
            'periods': [], 'occ_by_conid': {}, 'contract_conids': {},
            'opt_underlying': {}, 'stock_conid_syms': {},
            'stock_isins': {}, 'held': set(), 'posted_dividends': [],
            'tender_parked': {}, 'accrual_facts': [],
            'file_accounts': {}, 'ca_links': [],
            'stock_listing': {}, 'stock_folds': {}}
        read: List[tuple] = []
        # (statement, prescan) of each statement, for the per-contract-id
        # dates of a one-contract-id ticker change (_conid_seen).
        _conid_stmts: List[tuple] = []
        for path in paths:
            try:
                rows = cls._read_rows(Path(path))
                pre = _ib_prescan(rows, shown_name(path))
            except (OSError, UnicodeError, BrokerageParseError):
                continue                 # its parse reports it
            read.append((path, rows, pre))
        # IB's temporary symbols listed under the ticker's own contract
        # id in ANY statement are the ticker in every statement.
        _all_syms: Dict[str, set] = {}
        for _p, _r, _pre in read:
            for conid, syms in _pre['stock_conid_syms'].items():
                _all_syms.setdefault(conid, set()).update(syms)
        # A temporary symbol a ticker.map line names is the line's to
        # decide: not folded (`map_kept`).
        map_kept: Dict[str, str] = {}
        folds = ctx['stock_folds'] = _ib_temp_folds(
            _all_syms, _project_map_names(paths), map_kept)
        if folds or map_kept:
            _note_temp_folds(folds, "the account's IB statements", map_kept)
        for path, rows, pre in read:
            name = Path(path).name
            if folds:
                _folded = _ib_fold_rows(rows, folds)
                if _folded != rows:
                    try:
                        rows, pre = _folded, _ib_prescan(_folded,
                                                         shown_name(path))
                    except BrokerageParseError:
                        continue         # its parse reports it
            accts = frozenset(pre['accounts'])
            ctx['file_accounts'][name] = accts
            span = None
            for row in rows:
                if (len(row) >= 4 and row[0] == 'Statement'
                        and row[1] == 'Data' and row[2] == 'Period'):
                    span = _ib_period(row[3])
                    if span:
                        ctx['periods'].append((shown_name(path), *span,
                                               accts))
                    break
            # Identity facts learned from ANY of the account's
            # statements: an option root alias listed only in last
            # year's instrument list (audit S059-15), a stock renamed
            # between statements (S060-17), the listings held for the
            # income rebind (S060-00).
            for conid, occs in pre['occ_by_conid'].items():
                ctx['occ_by_conid'].setdefault(conid, set()).update(occs)
            for key, by_root in pre['contract_conids'].items():
                tgt = ctx['contract_conids'].setdefault(key, {})
                for root, ids in by_root.items():
                    tgt.setdefault(root, set()).update(ids)
            for conid, unds in pre['opt_underlying'].items():
                ctx['opt_underlying'].setdefault(conid, set()).update(unds)
            for conid, syms in pre['stock_conid_syms'].items():
                ctx['stock_conid_syms'].setdefault(conid, set()).update(syms)
            for root, ids in pre['stock_isins'].items():
                ctx['stock_isins'].setdefault(root, set()).update(ids)
            _conid_stmts.append((shown_name(path), pre))
            ctx['ca_links'].extend(pre['ca_links'])
            for root, full in pre['stock_listing'].items():
                ctx['stock_listing'].setdefault(root, full)
            # (the listings held: all together, and this statement's own)
            _ib_note_held(ctx, name, pre)
            # Postings and accrual facts carry their statement's broker
            # accounts: another IB account's dividend never pays this
            # account's accrual (audit A2-1035 / A2-1039).
            ctx['posted_dividends'].extend(
                (name, t, d, accts) for t, d in _ib_posted_dividends(rows))
            ctx['accrual_facts'].extend(
                dict(m, file=name, accounts=accts)
                for m in _ib_accrual_facts(rows))
            for sym, n in _ib_tender_parked(rows, pre['fii']).items():
                ctx['tender_parked'][sym] = (
                    ctx['tender_parked'].get(sym, 0.0) + n)
        _warn_coverage_gaps(ctx['periods'], tax_year=tax_year)
        _accts = set().union(*ctx['file_accounts'].values()) \
            if ctx['file_accounts'] else set()
        _spread = len(_accts) > 1 and not any(
            a >= _accts for a in ctx['file_accounts'].values())
        if _spread and combined:
            # combined_broker_accounts = true: a NOTE (refused on a
            # sheltered label — no plan per account in IB statements).
            _masked = sorted(_mask_account(a) for a in _accts)
            if taxable is False:
                raise combined_accounts_refusal(
                    "this account's IB statements", 'IB', _masked,
                    "an IB statement does not say which plan each "
                    "account is")
            emit_line(combined_accounts_note("this account's IB statements",
                                         'IB', _masked))
        elif _spread:
            # Separate statements of several IB accounts in one label:
            # booked as ONE account (right only for one tax entity).
            emit_line(f"{ATTENTION_PREFIX} this account's IB statements "
                  f"belong to {len(_accts)} IB accounts ("
                  f"{', '.join(sorted(_mask_account(a) for a in _accts))}"
                  f") — every row is booked to ONE account label. That is "
                  f"right only when they are one tax entity (e.g. two "
                  f"taxable margin accounts); give a sheltered (tax-"
                  f"advantaged) account its own folder.")
        # A contract id's ticker change is the security's: every IB
        # account of the project dates it (review 2 #4) — this one's
        # rows and the others' (each attributed by its own instrument
        # lists, its own temporary symbols folded).
        here = str((project or {}).get('account') or '')
        others = (project or {}).get('accounts') or {}
        own_seen = _conid_seen(_conid_stmts, ctx['stock_conid_syms'],
                               here if others else '')
        per_acct = {here: own_seen}
        proj_syms: Dict[str, set] = {}
        listing = dict(ctx['stock_listing'])
        all_folds = {**folds, **map_kept}
        _named = _project_map_names(paths) if others else frozenset()
        for label, opaths in sorted(others.items()):
            if str(label) == here:
                continue
            o_read, o_folds, o_kept = _ib_account_prescans(opaths, _named)
            all_folds.update(o_folds)
            all_folds.update(o_kept)
            o_syms: Dict[str, set] = {}
            for _p, _r, _pre in o_read:
                for conid, syms in _pre['stock_conid_syms'].items():
                    o_syms.setdefault(conid, set()).update(syms)
                for root, full in _pre['stock_listing'].items():
                    listing.setdefault(root, full)
            per_acct[str(label)] = _conid_seen(
                [(f"inputs/{label}/{shown_name(_p)}", _pre)
                 for _p, _r, _pre in o_read], o_syms, str(label))
            for conid, syms in o_syms.items():
                if conid in ctx['stock_conid_syms']:
                    proj_syms.setdefault(conid, set()).update(syms)
        tt_decl = _project_tt_renames(paths)
        ctx['conid_renames'] = _warn_stock_aliases(
            ctx['stock_conid_syms'], 'the account\'s IB statements',
            seen=_merge_seen(per_acct.values()),
            listing=listing,
            mapping=_project_ticker_map(paths),
            folds=all_folds, book=True,
            declared=tt_decl,
            distinct=_project_distinct(paths),
            links=ctx['ca_links'], tt_links=_account_tt_links(paths),
            project_syms=proj_syms, accounts=per_acct,
            dated_decl=list(tt_decl) + _project_map_dated(paths))
        return ctx

    @classmethod
    @builds("rename")
    def reconcile_files(cls, parsed) -> None:
        """See `_reconcile_files`; then every statement's deferred
        Corporate Actions side-effect lines print, minus the events the
        cross-statement pass undid (re-audit A2-1091). The ticker changes
        prepare_files booked (one contract id under two symbols) get
        their SPLIT rows here, once for the account (_book_conid_renames)."""
        try:
            cls._reconcile_files(parsed)
            _book_conid_renames(parsed)
            _ib_share_stated_settle(parsed)
            _ib_number_executions(parsed)
        finally:
            for _p, ex, _t in parsed:
                flush = getattr(ex, 'ca_side_effects', None)
                if flush is not None:
                    ex.ca_side_effects = None
                    flush()
                flush_summary = getattr(ex, '_ib_flush_skip_summary', None)
                if flush_summary is not None:
                    flush_summary()

    def resolve_unmatched_ca(self, key_fn, key) -> None:
        """A `Ca` row this statement could not pair was paired by the
        cross-statement pass: it is consumed, not a skipped row (its
        'original is not in this statement (see warning)' skip line
        named a warning that never comes)."""
        for i, ca in enumerate(self._ib.unmatched_ca):
            if key_fn(ca) != key:
                continue
            self._ib.unmatched_ca.pop(i)
            was = Outcome.skip(_IB_UNMATCHED_CA_SKIP)
            if self._skip_counts.get(self._counter_key(was)):
                self.recount(Outcome.consumed(), was=was)
            return

    @classmethod
    def _reconcile_files(cls, parsed) -> None:
        """Cross-statement pass over one account's parsed statements
        (taxjson-brokerage calls it with [(path, extractor, txs)], every
        txs list mutated in place), before the books de-duplicate the
        overlapping rows.

        Each statement pairs a `Ca` cancellation with its original,
        folds a commission refund into its trade and joins a cash in
        lieu to its split ON ITS OWN. An overlapping download of the
        same account taken earlier (before IB posted the cancellation or
        the refund) still holds the unadjusted row, which no longer
        matches the adjusted copy, so dedup kept both: a cancelled split
        or sale stayed booked, a refunded buy was booked twice (audit
        A2-0023 / A2-0024 / A2-0088 / A2-0258 / A2-0259 / A2-1038). And
        an adjustment whose row is in ANOTHER statement (a December
        trade refunded in January, a Dec 30 split's cash in lieu paid
        Jan 5) was never applied (A2-0605 / A2-1032 / A2-1036 /
        A2-1037). Here every adjustment one statement made is repeated
        on the account's other statements that hold the same original
        row but not the adjustment, and the unmatched ones are offered
        to the other statements. Statements of DIFFERENT broker accounts
        (Account Information) never touch each other."""
        files = [(p, ex, txs) for p, ex, txs in parsed
                 if isinstance(ex, IbBrokerage)]
        if not files:
            return

        _ib_cross_corp_cas(files)                # 1. Corporate Actions `Ca` rows
        _ib_join_cash_in_lieu(files)             # 2. cash in lieu and its split
        _ib_cross_trade_cas(files)               # 3. Trades `Ca` cancellations
        _ib_cross_held_cas(files)                # 3b / 4b. the Ca rows a parse held
        _ib_cross_xfer_cas(files)                # 4. Transfers `Ca` cancellations
        _ib_cross_refunds(files)                 # 5. commission refunds
        _ib_cross_income_listings(files)         # 6. one income row, one listing

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _read_rows(path: Path) -> List[List[str]]:
        """All CSV rows. utf-8-sig handles a BOM gracefully — IB's web
        export occasionally ships one and plain utf-8 reads it as a data
        byte, breaking the very first section-tag match; a UTF-16 BOM
        (a spreadsheet's "Unicode text" save) is decoded instead of
        failing on byte 0xFF."""
        from taxjson.lib.brokerages.base import decode_broker_text
        text = decode_broker_text(Path(path).read_bytes(), shown_name(path))
        return list(csv.reader(io.StringIO(text, newline='')))

    @staticmethod
    def _cell(row: List[str], header_map: Dict[str, int], col: str) -> str:
        i = header_map.get(col)
        v = row[i].strip() if i is not None and i < len(row) else ''
        return _norm_ccy(v) if col == 'Currency' else v

    @staticmethod
    def _check_statement_kind(pre: Dict[str, Any], path: Path) -> None:
        """Refuse an IB report that shares the Activity Statement's CSV
        shape but not its meaning: a Realized Summary lists realized
        LOTS, so reading it as activity books phantom trades and none of
        the income."""
        title = (pre.get('title') or '').strip()
        if not title or 'activity' in title.lower():
            return
        if _IB_REFUSED_TITLE_RE.search(title):
            raise BrokerageParseError(
                f"{shown_name(path)}: this is an IB {title!r} report, not an "
                f"Activity Statement — refusing to read it as trades and "
                f"income. Download Reports > Statements > Activity (CSV) "
                f"instead.")
        emit_line(f"warning: {shown_name(path)}: IB statement title {title!r} is not "
              f"'Activity Statement' — parsing it as one; check the "
              f"result.")

    @staticmethod
    def _multiplier(asset_cat: str, raw_symbol: str, fii: Dict[tuple, Any],
                    where: str) -> float:
        """Contract size for a row: IB's own Financial Instrument
        Information Multiplier when the statement lists the instrument
        (1000 for a crude-oil future, 50 for an index e-mini, 0.1 for a
        micro future, equity option 100); otherwise 1 per share/warrant and 100 per equity option.
        A futures / futures-option contract size cannot be guessed, so
        its absence fails the parse."""
        s = (raw_symbol or '').strip()
        info = (fii.get((asset_cat, s))
                or fii.get((asset_cat, re.sub(r'\s+', ' ', s))))
        if info and info.get('mult'):
            return float(info['mult'])
        if (info and info.get('mult_bad')
                and asset_cat not in ('Stocks', 'Warrants')):
            # The statement names a derivative's contract size in a
            # form the parser cannot read: never fall back to the 100
            # guess (S059-14). A share is 1 whatever the cell says.
            raise BrokerageParseError(
                f"{where}: {asset_cat} {s!r}: the Financial Instrument "
                f"Information Multiplier {info['mult_bad']!r} is not a "
                f"number this parser reads — refusing to guess the "
                f"contract size.")
        if asset_cat in ('Stocks', 'Warrants'):
            return 1.0
        if asset_cat == 'Equity and Index Options':
            return 100.0
        raise BrokerageParseError(
            f"{where}: {asset_cat} {s!r} has no multiplier in the "
            f"statement's Financial Instrument Information section — a "
            f"futures contract size (1000 for crude oil, 50 for an "
            f"index e-mini, ...) cannot be guessed. Export the full Activity Statement "
            f"(it lists every instrument).")

    def _closed_lot(self, closing: Optional[Dict[str, Any]], row,
                    header_map, asset_cat: str, where: str) -> None:
        """One IB ClosedLot row (a lot the closing trade above it
        closed: open date, quantity, cost) appended to that trade's
        `broker_lots` evidence as "<date> <qty> <basis>", lots joined
        by ";" — read only by `find-missing-history --write-purchases`,
        never booked. A lot whose cells do not read marks the trade's
        lots "invalid" (the drafter then uses IB's total Basis); a
        ClosedLot row of another security than the closing trade above
        it is ignored."""
        if closing is None or closing.get('broker_lots') == 'invalid':
            return
        if (self._cell(row, header_map, 'Symbol')
                != closing.get('description')
                or self._cell(row, header_map, 'Currency')
                != closing.get('currency')):
            return
        try:
            lot_date, _ = _ib_split_datetime(
                self._cell(row, header_map, 'Date/Time'), where)
            qty = abs(parse_strict_number(
                self._cell(row, header_map, 'Quantity'),
                field='Quantity', where=where))
            basis = (abs(parse_strict_number(
                self._cell(row, header_map, 'Basis'), field='Basis',
                where=where)) if 'Basis' in header_map else None)
        except (BrokerageParseError, ValueError):
            closing['broker_lots'] = 'invalid'
            return
        if basis is None or qty <= 1e-12:
            closing['broker_lots'] = 'invalid'
            return
        closing['broker_lots'] = add_broker_lot(closing.get('broker_lots'),
                                                broker_lot(lot_date, qty, basis))

    @staticmethod
    def _security_name(asset_cat: str, raw_symbol: str,
                       fii: Dict[tuple, Any], booked: str = '',
                       fii_all: Optional[Dict[tuple, Any]] = None) -> str:
        """The Financial Instrument Information name of a stock row
        ('SAMPLE PLATFORMS INC-CDR'), or '' — the row's description stays
        the raw symbol (the security overrides key on it). When the
        statement lists several instruments under the symbol (QZE on the
        TSX and a different company's QZE on the NYSE: `fii_all`), the
        name is the one whose market is the row's `booked` listing (its
        Listing Exch, else its ISIN country), never the first listed;
        '' when none or several match."""
        if asset_cat not in ('Stocks', 'Warrants'):
            return ''
        s = (raw_symbol or '').strip()
        keys = ((asset_cat, s), (asset_cat, re.sub(r'\s+', ' ', s)))
        infos = next((fii_all[k] for k in keys
                      if fii_all and fii_all.get(k)), [])
        if len({(i.get('name') or '').strip() for i in infos}) > 1:
            from taxjson.lib.markets import canadian_suffixes, ib_venue_suffix
            from taxjson.lib.markets_data import suffix_of
            ca = canadian_suffixes()

            def _market(sfx: str) -> str:
                return 'TO' if sfx in ca else sfx
            want = _market(suffix_of(booked))

            def _of(i: Dict[str, Any]) -> str:
                exch = (i.get('exch') or '').upper()
                v = ib_venue_suffix(exch) if exch else None
                if v:
                    return _market(v)
                return {'CA': 'TO', 'US': 'US'}.get(
                    (i.get('isin') or '').upper()[:2], '')
            hits = {(i.get('name') or '').strip() for i in infos
                    if want and _of(i) == want}
            name = hits.pop() if len(hits) == 1 else ''
            return name if name and name != s else ''
        info = next((fii[k] for k in keys if fii.get(k)), None) or {}
        name = (info.get('name') or '').strip()
        return name if name and name != s else ''

    def _check_symbol_tag(self, root: str, where: str) -> None:
        """Say once per symbol when an IB stock symbol carries a
        currency/venue tag (ABH.CAD): it is booked as a security of
        its own, apart from the plain listing."""
        root = (root or '').strip().replace(' ', '.')
        if '.' not in root:
            return
        base, tag = root.rsplit('.', 1)
        if tag.upper() not in _IB_CURRENCY_TAGS:
            return
        seen = getattr(self, '_tag_warned', None)
        if seen is None:
            seen = self._tag_warned = set()
        if root in seen:
            return
        seen.add(root)
        emit_line(f"warning: {where}: IB symbol {root!r} ends in the "
              f"currency/venue tag .{tag} — booked as a security of its "
              f"own, apart from {base}. If it is the same security (IB's "
              f"temporary line for a merger fraction, say), join or drop "
              f"it in ticker.map.")

    def _check_trade_money(self, where: str, symbol: str, qty: float,
                           price: float, proceeds: float, comm: float,
                           mult: float, check_notional: bool,
                           exact: bool = False) -> None:
        """Per-row money check that FAILS CLOSED: a header-only column
        swap (Proceeds <-> Comm/Fee) or a wrong multiplier is refused
        instead of booked. A Proceeds sign that contradicts the quantity
        only warns (the magnitude is what is booked; the Cash Report
        reconciliation catches a real sign error)."""
        gross = abs(proceeds)
        if check_notional:
            expected = abs(qty) * abs(price) * mult
            # Futures are outside the Cash Report's Trades reconciliation
            # (they settle through "Cash Settling MTM"), so this check is
            # their only guard: cent-level, as IB's own futures rows are
            # exact. The 0.1% band let a CL leg move ~57 silently (audit
            # G7-3); securities keep it — the Cash Report backs them.
            if not money_close(gross, expected, abs_tol=0.02,
                               rel_tol=1e-6 if exact else 0.001):
                raise BrokerageParseError(
                    f"{where}: {symbol}: |Proceeds| {M.shown(gross, 2):,.2f} is not "
                    f"|Quantity| {abs(qty):g} x T. Price {price:g} x "
                    f"multiplier {mult:g} = {M.shown(expected, 2):,.2f} — a swapped "
                    f"or mislabelled column, or a wrong contract "
                    f"multiplier; refusing to book it.")
            if ((qty > 0 and proceeds > 0.005)
                    or (qty < 0 and proceeds < -0.005)) and not (
                        price < 0 and exact) and not getattr(
                        self, '_sign_warned', False):
                self._sign_warned = True
                emit_line(f"warning: {where}: {symbol}: Proceeds "
                      f"{M.shown(proceeds, 2):,.2f} has the opposite sign to IB's "
                      f"convention for Quantity {qty:g} (a buy's proceeds "
                      f"are negative); the magnitude is booked — check "
                      f"the export (first such row only).")
        # Commissions are small next to the trade: a percentage of the
        # gross plus a per-contract/per-share allowance and a floor.
        allowed = 0.25 * gross + abs(qty) + 10.0
        if abs(comm) > allowed:
            raise BrokerageParseError(
                f"{where}: {symbol}: Comm/Fee {M.shown(comm, 2):,.2f} is implausible "
                f"for a trade of gross {M.shown(gross, 2):,.2f} (qty {qty:g}) — a "
                f"swapped or mislabelled column; refusing to book it.")

    def _book_forex(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                    where: str) -> None:
        """A Trades/Forex conversion is not a tax event here, but its
        cash IS in the Cash Report's Trades and Commissions lines — book
        it for the reconciliation only."""
        self.require_columns(header_map, ('Symbol', 'Quantity',
                                          'Proceeds', 'Currency'),
                             section='Trades (Forex)', where=where)
        sym = self._cell(row, header_map, 'Symbol')
        base, _, quote = sym.partition('.')
        if not base or not quote:
            raise BrokerageParseError(
                f"{where}: Forex symbol {sym!r} is not BASE.QUOTE")
        qty = parse_strict_number(self._cell(row, header_map, 'Quantity'),
                                  field='Quantity', where=where)
        proceeds = parse_strict_number(
            self._cell(row, header_map, 'Proceeds'), field='Proceeds',
            where=where)
        self._ib_book(rs, 'Trades (Sales + Purchase)', quote, proceeds)
        self._ib_book(rs, 'Trades (Sales + Purchase)', base, qty)
        comm_col = next((c for c in header_map
                         if re.match(r'^Comm in [A-Z]{3}$', c or '')), None)
        if comm_col:
            self._ib_book(rs, 'Commissions', comm_col[-3:], parse_strict_number(
                self._cell(row, header_map, comm_col), field=comm_col,
                where=where, allow_blank=True, blank=0.0))
        elif 'Comm/Fee' in header_map:
            self._ib_book(rs, 'Commissions',
                          self._cell(row, header_map, 'Currency') or quote,
                          parse_strict_number(
                              self._cell(row, header_map, 'Comm/Fee'),
                              field='Comm/Fee', where=where, allow_blank=True,
                              blank=0.0))

    def _option_symbol(self, raw: str, asset_cat: str, fii: Dict[tuple, Any],
                       root_alias: Dict[str, str],
                       aliased_roots: Dict[str, str], where: str,
                       strict: bool = True) -> str:
        """OCC symbol for an IB option row ("ABC 15JAN27 12.5 P",
        futures-option monthly "CL MAR27 40 P", legacy "SPX 20241220 P
        4000"). The root is canonicalized through the statement's
        option-root aliases (one conid listed as QZD 251121P... and
        QZD1 251121P... after a corporate action), so the opening and
        the assigned leg share ONE symbol and the premium folds."""
        symbol = raw

        def _strike(text: str) -> str:
            # The shared strike reader refuses a decimal comma; name
            # the row so the user can find it (audit A2-1041). A strike
            # the OCC field cannot hold is refused here too, with the
            # row (backlog TJ-0520).
            try:
                s = option_strike_text(text)
                encode_occ_strike(s)
                return s
            except BrokerageParseError as exc:
                raise BrokerageParseError(
                    f"{where}: {asset_cat} symbol {raw!r}: {exc}") from exc

        # Strikes may carry a thousands separator (5,000): OPTION_STRIKE_RE
        # tries the grouped form first; the commas are dropped below.
        opt_match = re.search(r'^(.+?)\s+(\d{2})([A-Z]{3})(\d{2})\s+' + OPTION_STRIKE_RE + r'\s+([PC])$', raw)
        opt_match_monthly = (re.search(r'^(.+?)\s+([A-Z]{3})(\d{2})\s+' + OPTION_STRIKE_RE + r'\s+([PC])$', raw)
                             if not opt_match else None)
        # Anchored at the end: a decimal-comma strike ('4,00') was read
        # as its integer part (audit A2-1041).
        opt_match_legacy = (re.search(r'^(.+?)\s+(\d{8})\s+([PC])\s+' + OPTION_STRIKE_RE + r'\s*$', raw)
                            if not (opt_match or opt_match_monthly) else None)
        if opt_match:
            base, day, mon, yr, strike, right = opt_match.groups()
            strike = _strike(strike)
            month = _MON_MAP.get(mon.upper())
            if month:
                if asset_cat == 'Equity and Index Options':
                    base = self._alias_root(base.strip(), f"{yr}{month}{day}",
                                            right, strike, root_alias,
                                            aliased_roots, where)
                base = base.replace(' ', '.')
                symbol = f"{base}{yr}{month}{day}{right}{encode_occ_strike(strike)}"
        elif opt_match_monthly:
            base, mon, yr, strike, right = opt_match_monthly.groups()
            strike = _strike(strike)
            month = _MON_MAP.get(mon.upper())
            if month:
                base = base.replace(' ', '.')
                # A monthly futures option names only the DELIVERY
                # month; the contract's real last trading day is in
                # the Financial Instrument Information (CL JAN26 ->
                # 2025-12-16). The old placeholder day "20" invented
                # an expiry — in the wrong month, possibly the wrong
                # tax year.
                s = raw.strip()
                info = (fii.get((asset_cat, s))
                        or fii.get((asset_cat, re.sub(r'\s+', ' ', s))))
                exp = (info or {}).get('expiry') or ''
                if _IB_DATE_RE.match(exp):
                    ymd = f"{exp[2:4]}{exp[5:7]}{exp[8:10]}"
                else:
                    ymd = f"{yr}{month}20"
                    if raw not in self._placeholder_warned:
                        self._placeholder_warned.add(raw)
                        emit_line(f"warning: {where}: {raw!r}: no expiry in "
                              f"the Financial Instrument Information — "
                              f"placeholder day 20 used in the option "
                              f"symbol (it will not match a statement "
                              f"that has the real expiry).")
                symbol = f"{base}{ymd}{right}{encode_occ_strike(strike)}"
        elif opt_match_legacy:
            base, exp, right, strike = opt_match_legacy.groups()
            strike = _strike(strike)
            base = base.replace(' ', '.')
            symbol = f"{base}{exp[2:]}{right}{encode_occ_strike(strike)}"
        # Strip all spaces as a fallback / cleanup (an OCC-padded
        # symbol 'QZY   250321C00050000' is already the contract).
        symbol = symbol.replace(' ', '')
        if strict and not is_option_symbol(symbol):
            # An option description no form reads ('XSP 16JAN26 6,85 P',
            # a decimal-comma strike) became a raw non-option symbol —
            # a phantom security, silently (audit A2-1041).
            raise BrokerageParseError(
                f"{where}: {asset_cat} symbol {raw!r} is not an option "
                f"description this parser reads (UNDERLYING DDMMMYY "
                f"STRIKE P|C, a decimal point in the strike) — refusing "
                f"to guess the contract. Re-export the statement in "
                f"English.")
        return symbol

    def _alias_root(self, base: str, yymmdd: str, right: str, strike: str,
                    root_alias: Dict[str, str],
                    aliased_roots: Dict[str, str], where: str) -> str:
        canon = root_alias.get(base)
        if not canon:
            return base
        key = (yymmdd, right, encode_occ_strike(strike))
        conids = self._ib_pre.get('contract_conids', {}).get(key, {})
        a, c = conids.get(base, set()), conids.get(canon, set())
        if a and c and not (a & c):
            # Two DIFFERENT listed contracts share the key under both
            # roots (a new standard series next to the adjusted one):
            # they are not the same security — keep them apart.
            emit_line(f"warning: {where}: option root {base} is an alias of "
                  f"{canon} elsewhere in this statement, but "
                  f"{base} {yymmdd}{right} is a different contract than "
                  f"{canon} {yymmdd}{right} — not merged.")
            return base
        if base not in aliased_roots:
            aliased_roots[base] = canon
            emit_line(f"note: option root {base} is IB's post-corporate-"
                  f"action alias of {canon} (same contract id in the "
                  f"Financial Instrument Information) — booked as "
                  f"{canon} so its opening and closing/assigned legs "
                  f"share one symbol.")
        return canon

    def _reconcile_cash_report(self, pre: Dict[str, Any],
                               booked: Dict[tuple, float],
                               path: Path) -> None:
        """Every money row the parser booked must reproduce IB's own
        Cash Report, per currency, within 0.02: Dividends, Payment In
        Lieu, Withholding Tax, Broker Interest, Other Fees, Commissions
        (+ Transaction Fees, a breakdown of the same charges) and Trades
        (Sales + Purchase). A mismatch means a row was dropped, doubled
        or mis-signed — the parse FAILS rather than emit a book that
        disagrees with the broker."""
        if not pre['has_cash_report']:
            # Flex queries and customized statements can leave the
            # section out; every guard that rides on the reconciliation
            # (a dropped, doubled or word-matched money row) is then
            # off — said on the console, not silently (audit R1-53).
            if any(booked.values()):
                emit_line(f"{ATTENTION_PREFIX} {shown_name(path)}: the statement has "
                      f"no Cash Report — parsed money is NOT reconciled "
                      f"against IB's own totals. Include the Cash Report "
                      f"section in the export (Flex: add it to the query).")
            return
        if not pre['cash_currencies']:
            emit_line(f"note: {shown_name(path)}: the Cash Report has no per-currency "
                  f"rows — parsed money not reconciled against it.")
            return
        lines = (
            ('Dividends', ('Dividends',)),
            ('Payment In Lieu of Dividends',
             ('Payment In Lieu of Dividends',)),
            ('Withholding Tax', ('Withholding Tax',)),
            ('Broker Interest Paid and Received',
             ('Broker Interest Paid and Received',)),
            ('Other Fees', ('Other Fees',)),
            ('Commissions', ('Commissions', 'Transaction Fees')),
            ('Trades (Sales + Purchase)',
             ('Trades (Sales)', 'Trades (Purchase)')),
        )
        currencies = set(pre['cash_currencies']) | {c for _, c in booked}
        bad = []
        unread = sorted(f"{ln} {cur} {raw!r}"
                        for (ln, cur), raw in pre.get('cash_bad', {}).items()
                        if any(ln in cr for _, cr in lines))
        if unread:
            raise BrokerageParseError(
                f"{shown_name(path)}: the Cash Report total of "
                f"{'; '.join(unread)} is not a number this parser reads — "
                f"the parsed rows cannot be reconciled against it. "
                f"Re-export the statement (English, decimal point).")
        for cur in sorted(currencies):
            for label, cr_lines in lines:
                broker = sum(pre['cash'].get((ln, cur), 0.0)
                             for ln in cr_lines)
                mine = booked.get((label, cur), 0.0)
                if abs(broker - mine) > _IB_CASH_TOL:
                    bad.append(f"{cur} {' + '.join(cr_lines)}: parsed "
                               f"{M.shown(mine, 2):,.2f} vs Cash Report {M.shown(broker, 2):,.2f} "
                               f"(diff {M.shown(mine - broker, 2):+,.2f})")
        if bad:
            raise BrokerageParseError(
                f"{shown_name(path)}: parsed rows do not reconcile with IB's own "
                f"Cash Report — {'; '.join(bad)}. A money row was "
                f"dropped, doubled or mis-signed; refusing to emit a book "
                f"that disagrees with the broker.")

    def statement_accounts(self) -> set:
        """Account ids named by the last parsed statement (Account
        Information / Accounts Included / the per-row Account column)."""
        pre = getattr(self, '_ib_pre', None) or {}
        return set(pre.get('accounts') or ())

    def _pair_warrant_exercises(self, warrant_legs, share_legs) -> None:
        """Mark each warrant exercise leg with the share listing it
        delivers, a share leg coded Ex on its date and in its currency
        (base.py `pair_warrant_legs`: by root, the closest first; a leg no
        root names only when the two are the day's one exercise). An
        unpaired leg keeps the old booking with an ATTENTION line."""
        how = {id(w): (where, code) for w, where, code in warrant_legs}
        for w, st, cands in pair_warrant_legs(
                [w for w, _where, _code in warrant_legs], share_legs):
            where, code = how[id(w)]
            if st is not None:
                emit_line(f"note: {where}: warrant {w['symbol']} exercised into "
                      f"{st['symbol']} ({-float(w['quantity']):g} warrants "
                      f"-> {float(st['quantity']):g} shares) — no "
                      f"disposition; the warrant's cost goes into the "
                      f"shares' cost.")
                continue
            emit_line(f"{ATTENTION_PREFIX} {where}: warrant {w['symbol']} "
                  f"exercised ({-float(w['quantity']):g}, code {code}) but "
                  f"no share leg "
                  f"coded Ex on {w['date']} names the shares — booked as a "
                  f"disposal at 0, so the warrant's cost becomes a capital "
                  f"loss instead of part of the shares' cost"
                  f"{self.law(' (ITA s.49(3))', ' (US basis carryover)')}. "
                  f"Book the exercise in a .tt file of this account and "
                  f"remove the row from the statement: "
                  f"{warrant_exercise_tt_hint(w, [t['symbol'] for t in cands])}"
                  f"; the warrant's cost then goes into the shares acquired.")

    # The parse's methods over its state (`self._ib`, an _IbParse): the
    # closures IB's parse_file defined over its locals until 5h (8a).

    def _ib_book(self, rs: "_IbRows", line: str, cur: str, amt: float) -> None:
        """`amt` booked on the Cash Report `line` in `cur`, for the end-of-parse
        reconciliation against IB's own Cash Report (`_IbRows.cash_booked`)."""
        rs.cash_booked[(line, cur)] = rs.cash_booked.get((line, cur), 0.0) + amt

    def _ib_ca_apply_undo(self, eff: Dict[str, Any]) -> None:
        """Undo one translated Corporate Actions row (its `Ca`
        cancellation arrived)."""
        st = self._ib
        eff['consumed'] = True
        drop = {id(t) for t in eff.get('txs', ())}
        kind = eff['kind']
        if kind == 'split':
            info = eff['split']
            if eff.get('renames'):
                info['tx']['symbol_new'] = info['tx']['symbol']
            if eff['qty'] > 0:
                info['new'] -= eff['qty']
            elif eff['qty'] < 0:
                info['old'] += eff['qty']
            _legs = info.get('legs')
            if _legs and eff['qty'] in _legs:
                _legs.remove(eff['qty'])
            if abs(info['new']) < 1e-9 and abs(info['old']) < 1e-9:
                # No leg left: the split never happened.
                drop.add(id(info['tx']))
                st.emitted_splits.pop(info['key'], None)
            else:
                info['tx']['quantity'] = info['text_ratio']
                _ib_refine_split_ratio(info)
        elif kind == 'cil':
            if eff.get('msg') in st.late_warnings:
                st.late_warnings.remove(eff['msg'])
            info = eff.get('split')
            if info is not None:
                info['cil'] -= eff['frac']
                info['tx']['quantity'] = info['text_ratio']
                _ib_refine_split_ratio(info)
            else:
                st.pending_cil[:] = [c for c in st.pending_cil
                                     if c['eff'] is not eff]
        elif kind == 'tender':
            tl = eff['tender']
            tl['parked'] -= eff.get('parked', 0.0)
            if eff.get('cash'):
                tl['cash_qty'] -= -eff['qty']
                tl['cash'] -= eff['cash']
        elif kind == 'cash_takeover':
            if eff.get('msg') in st.cash_takeovers:
                st.cash_takeovers.remove(eff['msg'])
        elif kind in ('corp_owned', 'spinoff'):
            if eff['desc'] in st.corp_owned_rows:
                st.corp_owned_rows.remove(eff['desc'])
        elif kind == 'unbooked':
            if eff['msg'] in st.unbooked_ca:
                st.unbooked_ca.remove(eff['msg'])
        elif kind == 'stockdiv':
            if eff['msg'] in st.stock_dividends:
                st.stock_dividends.remove(eff['msg'])
        elif kind == 'unhandled':
            t = eff['ticker']
            st.unhandled_ca_tickers[t] = st.unhandled_ca_tickers.get(t, 0) - 1
            if st.unhandled_ca_tickers[t] <= 0:
                del st.unhandled_ca_tickers[t]
        tl = eff.get('tender')
        if tl is not None:
            # The cancelled journal row leaves the tender note's row
            # tally and dates (audit A2-1031 / A2-1034).
            tl['rows'] -= 1
            if eff['date'] in tl['dates']:
                tl['dates'].remove(eff['date'])
        _was = eff.get('skip_cat')      # the row's skip, an Outcome
        if _was is not None and self._skip_counts.get(self._counter_key(_was)):
            # The cancelled original is resolved, not skipped: the
            # skip line and the UNBOOKED tally agree (audit R1-329).
            self.recount(Outcome.consumed(), was=_was)
        if drop:
            st.transactions[:] = [t for t in st.transactions
                                  if id(t) not in drop]

    def ca_undo(self, ca: Dict[str, Any], same_date_only: bool = False,
                exact_date: Optional[str] = None, rebook_ok: bool = False):
        """A `Ca` row: undo the latest matching original (same
        description, currency and category, negated quantity, dated
        on or before the cancellation; same date preferred).
        `same_date_only` (the row walk) leaves a Ca with no
        same-date original waiting, so a rebooked row listed before
        the original is never the one undone (audit R1-300); the
        end of the walk and the account's other statements take the
        latest earlier original. `exact_date` (an overlapping copy
        of the statement that already paired the Ca) undoes only
        the original dated that day. A row dated before the
        statement's period is a rebook of an earlier statement's
        row, undone only with `rebook_ok` (reconcile_files, when no
        statement holds the original). Returns the undone
        original's record, or None."""
        st = self._ib
        cands = [e for e in st.ca_effects if _ib_ca_matches(ca, e)
                 and e['date'] <= ca['date']
                 and (exact_date is None or e['date'] == exact_date)
                 and (rebook_ok or not self._ib_before_period(e['date']))]
        if not cands:
            return None
        same = [e for e in cands if e['date'] == ca['date']]
        if same_date_only and not same:
            return None
        eff = (same or cands)[-1]
        self._ib_ca_apply_undo(eff)
        return eff

    def _ib_ca_settle(self, eff: Dict[str, Any], outcome: Outcome) -> Outcome:
        """A translated Corporate Actions row's answer (5h (8d)): its effect
        recorded (`_ib_ca_record`), then consumed when a `Ca` row waiting for it
        undid it there, else `outcome`, kept on the effect (`skip_cat`) so a later
        `Ca` moves the row's count. Recorded first: the undo moves only a count
        the loop has made."""
        self._ib_ca_record(eff)
        if eff['consumed']:
            return Outcome.consumed()
        if outcome.kind != 'consumed':
            eff['skip_cat'] = outcome
        return outcome

    def _ib_ca_record(self, eff: Dict[str, Any]) -> None:
        """Remember a translated row; a same-date `Ca` row that
        arrived BEFORE it (same description, negated quantity)
        undoes it now."""
        st = self._ib
        st.ca_effects.append(eff)
        if self._ib_before_period(eff['date']):
            return                    # a rebook: reconcile_files
        for i, ca in enumerate(st.pending_ca):
            if _ib_ca_matches(ca, eff) and eff['date'] == ca['date']:
                st.pending_ca.pop(i)
                self._ib_ca_apply_undo(eff)
                st.ca_pairs.append((ca, eff['date']))
                return

    def _ib_before_period(self, d) -> bool:
        """Whether `d` is before the statement's period under an account
        context (`_IbParse.period_start`): a rebook of an earlier statement's row."""
        return bool(self.account_context is not None and self._ib.period_start
                    and (d or '') < self._ib.period_start)

    # A fraction whose split is in ANOTHER statement of the account
    # (a Dec 30 reverse split, its cash in lieu paid Jan 5) joins it
    # there (reconcile_files, audit A2-1037): the split's new leg
    # counts the fraction, and the parse's cash-in-lieu sale removes it once.
    def cil_join(self, c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Join the cash in lieu `c` (its symbol, date and fraction) to the
        split of this statement NEAREST its date within `_IB_CIL_WINDOW`
        days, in either order (`_IbParse.emitted_splits`): the split it
        joined, None when none. The loop's own rows and reconcile_files'
        (another statement's) join by this one rule (TJ-2439)."""
        st = self._ib
        near = []
        for (s_, d, _r), info in st.emitted_splits.items():
            if c['symbol'] not in (s_, info['tx'].get('symbol_new')):
                continue
            dist = abs(_days_between(d, c['date']))
            if dist <= _IB_CIL_WINDOW:
                near.append((dist, d, info))
        if not near:
            return None
        info = min(near, key=lambda x: (x[0], x[1]))[2]
        info['cil'] += c['frac']
        _ib_refine_split_ratio(info)
        return info

    def _ib_row_open_positions(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                                lineno: int, section: str) -> Outcome:
        """A Data row of the Open Positions section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Not emitted as transactions — read purely to seed the
        # income-reattribution holdings map (see
        # _reattribute_income_to_holdings). Summary rows only
        # (Lot rows repeat the same symbol per acquisition).
        try:
            discr = row[header_map['DataDiscriminator']]
            asset_cat = row[header_map['Asset Category']]
            currency = _norm_ccy(row[header_map['Currency']])
            symbol = row[header_map['Symbol']]
            qty_raw = row[header_map['Quantity']]
        except (KeyError, IndexError):
            return Outcome.skip(f"malformed {section} row")
        if discr != 'Summary' or asset_cat != 'Stocks':
            # Lot rows, option/futures summaries, subtotals:
            # nothing the holdings seed needs.
            return Outcome.nonevent(f"{section} row (not a stock "
                                    f"summary)")
        try:
            # Strict: a decimal comma is refused (a skip), not
            # read 100x too large.
            if abs(parse_strict_number(qty_raw,
                                       field='Quantity')) < 1e-9:
                return Outcome.nonevent(f"{section} zero-quantity "
                                        f"row")
        except ValueError:
            return Outcome.skip(f"malformed {section} row")
        self._check_symbol_tag(_strip_ib_ext(symbol.strip()),
                               shown_name(st.path))
        _op_full = _ib_stock_symbol(asset_cat, symbol, currency,
                                    rs.pre['fii'])
        rs.open_position_syms.add(_op_full)
        try:
            rs.open_position_qty[_op_full] = (
                rs.open_position_qty.get(_op_full, 0.0)
                + parse_strict_number(qty_raw, field='Quantity'))
        except ValueError:
            pass
        return Outcome.consumed()      # read into parser state

    def _ib_row_statement(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                           lineno: int, section: str) -> Outcome:
        """A Data row of the Statement section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        # Capture the statement's period-end date so the
        # accrual diagnostic can tell a payable-now dividend
        # from a normal future-dated pending accrual.
        fn_idx = header_map.get('Field Name')
        fv_idx = header_map.get('Field Value')
        if (fn_idx is not None and fv_idx is not None
                and fv_idx < len(row)
                and row[fn_idx] == 'Period'):
            raw = row[fv_idx]
            end_part = raw.split(' - ')[-1].strip()
            try:
                rs.statement_period_end = datetime.strptime(
                    end_part, "%B %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                rs.statement_period_end = ''
            return Outcome.consumed()  # read into parser state
        else:
            return Outcome.nonevent("Statement metadata row")

    def _ib_row_accruals(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                          lineno: int, section: str) -> Outcome:
        """A Data row of the Change in Dividend Accruals section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        # Net 'Po' (accrual posted) against 'Re' (reversed) per
        # dividend. Keyed on (account, symbol, ex date, pay
        # date) — a Po/Re pair for the same dividend shares all
        # four and nets to zero; an un-reversed Po stays
        # positive. Not emitted as a transaction; only feeds
        # the end-of-parse diagnostic.
        # IB packs several codes into the cell ('ADR;Po' on an
        # ADR's accrual): match the Po/Re TOKEN, not the cell.
        _acc_tokens = set(re.split(r'[;,\s]+', self._cell(
            row, header_map, 'Code')))
        code = ('Po' if 'Po' in _acc_tokens
                else 'Re' if 'Re' in _acc_tokens else '')
        if code not in ('Po', 'Re'):
            return Outcome.nonevent(f"{section} row without a "
                                    f"Po/Re code (subtotal)")
        symbol = (row[header_map['Symbol']]
                  if 'Symbol' in header_map
                  and header_map['Symbol'] < len(row) else '')
        if not symbol:
            return Outcome.nonevent(f"{section} row without a "
                                    f"symbol (subtotal)")
        symbol = symbol.replace(' ', '.')
        account = (row[header_map['Account']]
                   if 'Account' in header_map
                   and header_map['Account'] < len(row) else '')
        ex_date = (row[header_map['Ex Date']]
                   if 'Ex Date' in header_map
                   and header_map['Ex Date'] < len(row) else '')
        pay_date = (row[header_map['Pay Date']]
                    if 'Pay Date' in header_map
                    and header_map['Pay Date'] < len(row) else '')
        currency = _norm_ccy(row[header_map['Currency']]
                             if 'Currency' in header_map
                             and header_map['Currency'] < len(row)
                             else '')
        gross_idx = header_map.get('Gross Amount')
        if gross_idx is None or gross_idx >= len(row):
            return Outcome.skip(f"malformed {section} row")
        try:
            gross = parse_strict_number(row[gross_idx],
                                        field='Gross Amount')
        except (ValueError, IndexError) as e:
            # Name the row by its symbol and date only: the full
            # row carries the IB account id.
            _sym_i = header_map.get('Symbol')
            _dt_i = header_map.get('Ex Date', header_map.get('Date'))
            _sym = (row[_sym_i] if _sym_i is not None
                    and _sym_i < len(row) else '?')
            _dt = (row[_dt_i] if _dt_i is not None
                   and _dt_i < len(row) else '?')
            emit_line(f"warning: skipping malformed IB {section} row "
                  f"({e}): symbol {_sym}, date {_dt}")
            return Outcome.skip(f"malformed {section} row")
        # Keyed on the EX date, not the pay date: IB revises
        # a dividend's pay date between the Po and the Re row
        # (an accrual can post with one pay date and reverse
        # with the next day's), and a pay-date key split the pair into two
        # half-open accruals — a false "accrued but not
        # booked" warning for a dividend already paid. The
        # pay date is only a fallback key when IB omits the
        # ex date. Latest row's pay date wins for display.
        key = ((account, symbol, 'ex', ex_date) if ex_date
               else (account, symbol, 'pay', pay_date))
        rs.accrual_net[key] = rs.accrual_net.get(key, 0.0) + gross
        _meta = rs.accrual_meta.setdefault(key, {
            'symbol': symbol, 'pay_date': pay_date,
            'currency': currency, 'pay_dates': set(), 'account': account,
        })
        if pay_date:
            _meta['pay_date'] = pay_date
            _meta['pay_dates'].add(pay_date)
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', ex_date or ''):
            _meta['ex_date'] = ex_date
        # The per-share rate and share count of the dividend, so
        # a Payment-in-Lieu row — which has neither in its own
        # description — can be reconciled to shares at the end of the
        # parse (`_ib_finish`). Taken
        # from the Po (posting) row, in the accrual's currency:
        # IB's Re row may carry a rate in the PAYMENT currency
        # (a USD accrual reversed at the CAD rate), so the first
        # row seen made the result depend on row order (audit
        # S060-13 / G3-0).
        if code == 'Po':
            for _col, _fld in (('Gross Rate', 'po_rate'),
                               ('Quantity', 'po_qty')):
                _ci = header_map.get(_col)
                if _ci is None or _ci >= len(row):
                    continue
                try:
                    _v = abs(parse_strict_number(row[_ci],
                                                 field=_col))
                except (ValueError, IndexError):
                    _v = 0.0
                if _v:
                    _meta[_fld] = _v
        _ib_note_po_row(_meta, code)
        # The ex-dividend date of the dividend paid on pay_date
        # (a neutral fact on the posted Dividends row, at the end of
        # the parse; a
        # US project reads it for §852(b)(7), characterize/income_dating).
        if (pay_date and re.fullmatch(r'\d{4}-\d{2}-\d{2}',
                                      ex_date or '')):
            rs.accrual_ex.setdefault((symbol, pay_date), ex_date)
        return Outcome.consumed()      # read into parser state

    @builds("tax")
    def _ib_row_withholding(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                             lineno: int, section: str) -> Outcome:
        """A Data row of the Withholding Tax section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Required columns — see the Dividends section.
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Currency', 'Date', 'Description',
                         'Amount'), section=section, where=where)
        currency = self._cell(row, header_map, 'Currency')
        description = self._cell(row, header_map, 'Description')
        if 'Total' in currency:
            return Outcome.nonevent(f"{section} subtotal row")
        date = _ib_require_date(
            self._cell(row, header_map, 'Date'), where)
        # IB books withholding as a NEGATIVE amount (cash out)
        # and a refund/correction as POSITIVE. The repo-wide
        # TAX convention is positive = tax withheld (RBC emits
        # it that way), so flip the sign rather than abs() it:
        # a charge stays positive and a refund nets NEGATIVE
        # instead of double-counting as more tax paid.
        _wht_cash = parse_strict_number(
            self._cell(row, header_map, 'Amount'), field='Amount',
            where=where)
        amount = -_wht_cash
        self._ib_book(rs, 'Withholding Tax', currency, _wht_cash)

        # Description shape:  "SAMPLG (US0000000001) Cash Dividend..."
        # — pull the ticker AND the ISIN so the market suffix
        # comes from the security's country (the same `_isin_ext`
        # the Dividends rows use, `_ib_row_dividends`) rather than hardcoding
        # `.US`. Hardcoded `.US` fragmented the TAX symbol
        # from non-US dividends on the same security, so the
        # foreign-tax-credit pairing broke for any non-US
        # holding.
        ticker, isin = _ib_income_ticker_strict(description,
                                                where, section)
        self._check_symbol_tag(ticker, where)       # A2-1493

        ext = (None if ticker == 'CASH'
               else _isin_ext(isin, ticker, rs.isin_fallback))

        st.transactions.append({
            'action': 'TAX',
            'date': date,
            'time': '09:30:00',
            'date_settle': date,
            'symbol': f"{ticker}.{ext}" if ext else ticker,
            'quantity': 0.0,
            'currency': currency,
            'net_amount': amount,
            'type': 'tax',
            'account': 'IB',
            'description': description,
            '_isin': isin,      # income rebind check; popped
            '_line': lineno,    # for a refusal naming the row; popped
        })
        return Outcome.consumed()

    @builds("interest")
    def _ib_row_interest(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                          lineno: int, section: str) -> Outcome:
        """A Data row of the Interest section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Required columns — see the Dividends section.
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Currency', 'Date', 'Description',
                         'Amount'), section=section, where=where)
        currency = self._cell(row, header_map, 'Currency')
        description = self._cell(row, header_map, 'Description')
        if 'Total' in currency:
            return Outcome.nonevent(f"{section} subtotal row")
        date = _ib_require_date(
            self._cell(row, header_map, 'Date'), where)
        amount = parse_strict_number(
            self._cell(row, header_map, 'Amount'), field='Amount',
            where=where)
        self._ib_book(rs, 'Broker Interest Paid and Received', currency,
                      amount)

        st.transactions.append({
            'action': 'INTEREST',
            'date': date,
            'time': '09:30:00',
            'date_settle': date,
            'symbol': 'CASH',
            'quantity': 0.0,
            'currency': currency,
            'net_amount': amount, # Preserve sign for interest paid vs charged
            'type': 'interest',
            'account': 'IB',
            'description': description
        })
        return Outcome.consumed()

    @builds("fee")
    def _ib_row_fees(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                      lineno: int, section: str) -> Outcome:
        """A Data row of the Fees section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Required columns — see the Dividends section. (Date
        # may be BLANK on a row: the "for Mmm YYYY" fallback
        # below; the COLUMN must exist.)
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Currency', 'Date', 'Description',
                         'Amount'), section=section, where=where)
        currency = self._cell(row, header_map, 'Currency')
        description = self._cell(row, header_map, 'Description')
        date = self._cell(row, header_map, 'Date')
        # Subtotal rows: 'Total' / 'Total in CAD' in the
        # Currency cell, OR a blank Currency with 'Total' in
        # the Subtitle/Description. The blank-currency form
        # used to fall through (defaulted to CAD, no date,
        # no 'for Mmm YYYY' hint) into the dateless-fees
        # warning, which then reported the subtotal ON TOP
        # of the rows it summed — a doubled total.
        _subtitle = (row[header_map['Subtitle']]
                     if 'Subtitle' in header_map
                     and header_map['Subtitle'] < len(row)
                     else '')
        # (Not by a word in the description: a "Nasdaq
        # TotalView" market-data charge is a real fee, audit
        # S058-03.)
        if ('Total' in currency or not currency
                or 'Total' in _subtitle):
            return Outcome.nonevent(f"{section} subtotal row")

        # IB books a charge NEGATIVE (cash out) and a
        # refund/reversal POSITIVE. Repo FEE convention
        # (taxjson_fx_cash, .tt round-trip): positive =
        # charged — flip, like the Withholding Tax
        # branch does for TAX, so a market-data charge
        # is an outflow and a reversal nets against it.
        _fee_cash = parse_strict_number(
            self._cell(row, header_map, 'Amount'), field='Amount',
            where=where)
        amount = -_fee_cash
        if date:
            date = _ib_require_date(date, where)
        else:
            # Extract date from description if possible (e.g., "for Feb 2025")
            date_match = re.search(r'for\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', description, re.IGNORECASE)
            if date_match:
                mon, yr = date_match.groups()
                mon_map = {
                    'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                    'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
                }
                date = f"{yr}-{mon_map.get(mon.upper())}-01"
            else:
                # No date column AND no "for Mmm YYYY" hint. The
                # old fallback stamped a literal "2025-01-01"
                # which silently mis-files the row in any other
                # tax year. Skip + accumulate; a single summary
                # line fires at the end of the parse.
                rs.skipped_dateless_fees.append(amount)
                return Outcome.skip(f"{section} row with no date "
                                    f"(see warning)")

        self._ib_book(rs, 'Other Fees', currency, _fee_cash)
        st.transactions.append({
            'action': 'FEE',
            'date': date,
            'time': '09:30:00',
            'date_settle': date,
            'symbol': 'CASH',
            'quantity': 0.0,
            'currency': currency,
            'net_amount': amount,
            'type': 'fee',
            'account': 'IB',
            'description': description
        })
        return Outcome.consumed()

    def _ib_row_transaction_fees(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                                  lineno: int, section: str) -> Outcome:
        """A Data row of the Transaction Fees section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        # `Transaction Fees` rows (UK Stamp Tax, SEC/FINRA-style
        # levies) are a per-fill BREAKDOWN of charges IB ALREADY
        # includes in the trade's Comm/Fee: an LSE buy's Comm/Fee
        # already holds the stamp levy (its Basis is the gross plus
        # that Comm/Fee), the stamp-tax rows sum to part of it, and the
        # Cash Report's Commissions + Transaction Fees lines add up to
        # the sum of the Comm/Fee column. Folding the levy into the
        # trade AGAIN (the earlier behaviour) charged it twice.
        # The section is therefore a recognized non-event: the Cash
        # Report reconciliation (the end of the parse) checks the identity
        #   sum(Comm/Fee) == Commissions + Transaction Fees
        # per currency, so a statement layout that ever EXCLUDED the
        # levy from Comm/Fee would fail loudly instead of under-booking.
        # A per-fill BREAKDOWN of levies already inside the
        # trade's Comm/Fee (see the note on `_IB_METADATA_SECTIONS`'s
        # 'Transaction Fees') — a recognized non-event.
        currency = self._cell(row, header_map, 'Currency')
        _tf_cat = self._cell(row, header_map, 'Asset Category')
        if (not currency or 'Total' in currency
                or _tf_cat.startswith('Total')):
            return Outcome.nonevent(f"{section} subtotal row")
        return Outcome.nonevent(
                f"{section} row (breakdown of a levy already in the "
                f"trade's Comm/Fee)")

    @builds("fee")
    def _ib_row_commission_adjustments(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                                        lineno: int, section: str) -> Outcome:
        """A Data row of the Commission Adjustments section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Post-trade commission corrections, e.g.
        #   USD,2025-02-10,"Refund (QZW, -20 2025-01-07)",1.10
        # IB's Amount is signed cash (a refund POSITIVE).
        # Emitted as a FEE with the repo sign (positive =
        # charged) so a refund is a NEGATIVE fee that nets
        # against the original commission in fee totals.
        # Bound to the ticker named in the description
        # (the fee report groups by symbol); CASH when the
        # description names none.
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Currency', 'Date', 'Description',
                         'Amount'), section=section, where=where)
        currency = self._cell(row, header_map, 'Currency')
        description = self._cell(row, header_map, 'Description')
        if not currency or 'Total' in currency:
            return Outcome.nonevent(f"{section} subtotal row")
        date = _ib_require_date(
            self._cell(row, header_map, 'Date'), where)
        amount = parse_strict_number(
            self._cell(row, header_map, 'Amount'), field='Amount',
            where=where)
        self._ib_book(rs, 'Commissions', currency, amount)
        _m = _IB_COMM_ADJ_TICKER_RE.search(description or '')
        if _m:
            _raw_tk = _m.group(1).strip()
            _occ = _ib_option_ref(self, _raw_tk, rs.pre['fii'], rs.pre['root_alias'],
                                  rs.aliased_roots, where)
            if _occ:
                symbol = f"{_occ}.{_ib_currency_ext(currency)}"
            else:
                _tk = _strip_ib_ext(_raw_tk.replace(' ', '.'), currency)
                # The listing rule of the trades (S010-06): a
                # TSX USD unit's refund sits on SAMPMD.U.TO with its
                # trades, not SAMPMD.U.US (audit A2-0086).
                symbol = (f"{_tk}."
                          f"{_ib_listing_ext('Stocks', _raw_tk, currency, rs.pre['fii'])}")
        else:
            symbol = 'CASH'
        st.transactions.append({
            'action': 'FEE',
            'date': date,
            'time': '09:30:00',
            'date_settle': date,
            'symbol': symbol,
            'quantity': 0.0,
            'currency': currency,
            'net_amount': -amount,
            'type': 'fee',
            'account': 'IB',
            'description': f"{description} (Commission "
                           f"Adjustments)",
        })
        _tm = _IB_COMM_ADJ_TRADE_RE.search(description or '')
        if _tm:
            try:
                _aq = parse_strict_number(_tm.group(2),
                                          field='quantity')
            except BrokerageParseError:
                _aq = None
            if _aq:
                _adj_tk = _tm.group(1).strip()
                _adj_occ = _ib_option_ref(self, _adj_tk, rs.pre['fii'],
                                          rs.pre['root_alias'], rs.aliased_roots,
                                          where)
                # The row's Account, else the statement's only
                # account: one account's own statement has no
                # such column (backlog TJ-1245).
                _adj_accts = self.statement_accounts()
                rs.comm_adjustments.append({
                    'account': ((self._cell(row, header_map,
                                            'Account')
                                 if 'Account' in header_map
                                 else '')
                                or (next(iter(_adj_accts))
                                    if len(_adj_accts) == 1
                                    else '')),
                    'fee_tx': st.transactions[-1], 'amount': amount,
                    'ticker': (_adj_occ or _strip_ib_ext(
                        _adj_tk.replace(' ', '.'), currency)),
                    'qty': _aq, 'trade_date': _tm.group(3),
                    'currency': currency, 'date': date,
                    'key': (currency, date, M.units(amount, 6),
                            description)})
        return Outcome.consumed()

    @builds("dividend", "dividend_in_lieu", "roc")
    def _ib_row_dividends(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                           lineno: int, section: str) -> Outcome:
        """A Data row of the Dividends section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Required columns resolved by header name; a missing
        # one FAILS the parse (it used to fall back to row[0] —
        # the literal section name — and later to a loud skip
        # that still lost the income).
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Currency', 'Date', 'Description',
                         'Amount'), section=section, where=where)
        currency = self._cell(row, header_map, 'Currency')
        description = self._cell(row, header_map, 'Description')
        # IB emits per-currency subtotal rows ('Total' / 'Total
        # in CAD' in the CURRENCY cell) — skip before parsing.
        # Not by a word in the description: a fund named "Total
        # Return" paid a dividend that vanished (audit S058-03).
        if 'Total' in currency or (not currency
                                   and 'Total' in description):
            return Outcome.nonevent(f"{section} subtotal row")
        date = _ib_require_date(
            self._cell(row, header_map, 'Date'), where)
        # SIGN-PRESERVING: IB posts re-characterizations as a
        # negative reversal row plus a corrected positive row.
        # abs() here used to book all three legs as income
        # (+250 / −250 / +240 → 740 instead of 240); keeping
        # the sign lets the reversal net out downstream.
        amount = parse_strict_number(
            self._cell(row, header_map, 'Amount'), field='Amount',
            where=where)

        ticker, isin = _ib_income_ticker_strict(description,
                                                where, section)
        # A currency-tagged line (SAMPLE.CAD) is its own security
        # here as in Trades: say so once (re-audit A2-1493).
        self._check_symbol_tag(ticker, where)
        # Record (ticker, pay date) so the accrual diagnostic at
        # the end of the parse can tell whether this dividend's cash has
        # already been booked in this file.
        rs.posted_dividend_keys.append((ticker, date))
        ext = _isin_ext(isin, ticker, rs.isin_fallback)
        # The row's line and Account cell, for the dividend facts at the
        # end of the parse (popped there).
        marks = {'_line': lineno, '_acct': (self._cell(row, header_map, 'Account')
                                            if 'Account' in header_map else '')}

        # IB's PIL marker is "Payment in Lieu of Dividend" (note
        # the lowercase 'in'); the previous match string had a
        # capital 'I' and silently missed every PIL row. The
        # phrase is the toolbox's, read as RBC and Questrade do.
        is_pil = bool(PIL_DESC_RE.search(description))
        self._ib_book(rs, 'Payment In Lieu of Dividends' if is_pil
                      else 'Dividends', currency, amount)
        issuer_roc = False
        if is_roc_description(description):
            consumed, description, issuer_roc = self._ib_dividend_roc(
                amount=amount, currency=currency, date=date, description=description,
                ext=ext, is_pil=is_pil, isin=isin, ticker=ticker)
            if consumed:
                st.transactions[-1].update(marks)
                return Outcome.consumed()
        action = 'DIVIDEND_IN_LIEU' if is_pil else 'DIVIDEND'
        # Type 'dividend' makes downstream tools skip ACB; PIL
        # uses a different type so dividend gain-entry emission
        # can exclude it cleanly (PIL is interest income, not
        # an eligible/qualified dividend).
        tx_type = 'dividend_in_lieu' if is_pil else 'dividend'

        # IB dividend descriptions include "USD 0.24 per
        # Share" — extract the per-share rate so the record
        # reconciles against the user's pool size and T5/
        # 1099-DIV. Qty is back-computed from amount/rate
        # (IB ships gross on the Dividend row; withholding
        # is in a separate section). Falls back to qty=
        # price=0 when the pattern doesn't match.
        qty, price = _parse_div_qty_rate(description, amount)
        st.transactions.append({
            'action': action,
            'date': date,
            'time': '09:30:00',
            'date_settle': date,
            'symbol': f"{ticker}.{ext}",
            'quantity': qty,
            'price': price,
            'currency': currency,
            'net_amount': amount,
            'gross_amount': amount,
            'type': tx_type,
            'account': 'IB',
            'description': description,
            '_isin': isin,      # income rebind check; popped
            **marks,
        })
        if len(isin) >= 2:
            st.transactions[-1]['issuer_country'] = isin[:2].upper()
        if rs.dealer_country:
            st.transactions[-1]['dealer_country'] = rs.dealer_country
        if issuer_roc:
            # A foreign issuer's return of capital: the neutral fact, a
            # dividend row marked as such; taxjson.characterize.foreign_roc
            # books it as a dividend (s.90(1)) or a cost reduction.
            st.transactions[-1][ISSUER_ROC_KEY] = True
        if is_pil and is_roc_description(description):
            # The distribution it replaces was a return of
            # capital, no taxable dividend: a neutral fact the
            # income rules read (s.260(5.1)(c); TJ-0729).
            st.transactions[-1]['income_label'] = (
                PIL_LABEL_RETURN_OF_CAPITAL)
        return Outcome.consumed()

    def _ib_dividend_roc(self, *, amount, currency, date, description, ext, is_pil, isin,
                         ticker) -> Tuple[bool, str, bool]:
        """A Dividends row IB labels a return of capital (is_roc_description):
        whether it booked the cost reduction (the row consumed), the
        description the row is otherwise booked with, and whether that row
        is a foreign issuer's return of capital (ISSUER_ROC_KEY: the
        characterize step's to book as a dividend or a cost reduction,
        never the parse's; TJ-2594)."""
        st = self._ib
        # IB's "(Return of Capital)" label is the ISSUER's
        # designation, which is only an ACB reduction for
        # Canadian purposes in the Canadian-issuer case.
        #  * A PAYMENT IN LIEU is paid by the share
        #    borrower, not the issuer: it can never reduce
        #    ACB, whatever the underlying distribution was
        #    — income (falls through to the PIL branch).
        #  * A non-resident corporation's distribution on
        #    a portfolio share is a foreign DIVIDEND (ITA
        #    s.90(1)); a US "return of capital" (no E&P)
        #    is a cost reduction only when the issuer
        #    really reduced its paid-up capital
        #    (s.53(2)(b)(ii)) — s.90(2)/(3) are the
        #    foreign-affiliate rules (audit S058-07). The
        #    parse marks the row (a dividend row with
        #    ISSUER_ROC_KEY) whatever the country: the law is
        #    taxjson.characterize.foreign_roc's (CA-ACB-08,
        #    CA-ACB-09, US-ROC-01), applied after the parse.
        #  * Canadian issuer (T3 box 42 style): ACB
        #    reduction, as before.
        # No ISIN → issuer unknown → kept as ADJUST.
        _issuer_cc = isin[:2].upper() if len(isin) >= 2 else ''
        _foreign = bool(_issuer_cc) and _issuer_cc != 'CA'
        if is_pil:
            description = (
                f"{description} [payment in lieu of an "
                f"IB-designated return of capital: paid by "
                f"the share borrower — income, never an ACB "
                f"reduction]")
        elif _foreign:
            return False, description, True
        else:
            # `amount` stays signed so IB's negative
            # reversal rows net out (they become positive
            # ADJUSTs).
            st.transactions.append(self.tx_roc_adjust(
                symbol=f"{ticker}.{ext}", currency=currency,
                date=date, desc=description, amount=amount,
                account='IB'))
            st.transactions[-1]['_isin'] = isin
            if len(isin) >= 2:
                st.transactions[-1]['issuer_country'] = \
                    isin[:2].upper()
            return True, description, False
        return False, description, False

    @builds("transfer")
    def _ib_row_transfers(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                           lineno: int, section: str) -> Outcome:
        """A Data row of the Transfers section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Required columns resolved by header name; a missing
        # one fails the parse.
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Asset Category', 'Type', 'Symbol',
                         'Currency', 'Date', 'Qty',
                         'Market Value', 'Code'),
            section=section, where=where)
        asset_cat = self._cell(row, header_map, 'Asset Category')
        transfer_type = self._cell(row, header_map, 'Type')
        symbol = self._cell(row, header_map, 'Symbol')
        # The description names the SECURITY (the raw IB symbol,
        # as on Trades rows) next to the transfer kind: the
        # security overrides key on it, and a kind-only
        # description ('ACATS') left the transfer on the
        # un-overridden listing — the pool split (audit S059-03).
        xfer_desc = f"{transfer_type} ({symbol})"
        currency = self._cell(row, header_map, 'Currency')
        qty_str = self._cell(row, header_map, 'Qty')
        if not asset_cat or asset_cat.startswith('Total'):
            return Outcome.nonevent(f"{section} subtotal row")
        if asset_cat not in ('Stocks', 'Equity and Index Options',
                             'Warrants', 'Futures',
                             'Options On Futures'):
            # Cash movements (no security) are custody of
            # money, not property; any other asset class is a
            # security the parser has no branch for — loud.
            if asset_cat in ('Cash', 'Forex') or not symbol:
                return Outcome.nonevent(f"{section} cash row")
            return Outcome.skip(f"{section}/{asset_cat}")
        # A security transfer without a quantity moves shares
        # we cannot count: refused (it was a calm non-event,
        # and the position change was lost — audit R1-55).
        qty = parse_strict_number(qty_str, field='Qty',
                                  where=where)
        date = _ib_require_date(
            self._cell(row, header_map, 'Date'), where)

        # Skip cash transfers
        if symbol.startswith('CASH.'):
            return Outcome.nonevent(f"{section} cash transfer")

        total_cost = parse_strict_number(
            self._cell(row, header_map, 'Market Value'),
            field='Market Value', where=where)

        multiplier = self._multiplier(asset_cat, symbol, rs.pre['fii'],
                                      where)
        if asset_cat in ('Equity and Index Options',
                         'Options On Futures'):
            symbol = self._option_symbol(
                symbol, asset_cat, rs.pre['fii'], rs.pre['root_alias'],
                rs.aliased_roots, where)
            if asset_cat == 'Options On Futures':
                symbol = f"F:{symbol}"
        elif asset_cat == 'Futures':
            symbol = f"F:{symbol.replace(' ', '.')}"
        else:
            symbol = symbol.replace(' ', '.')
            self._check_symbol_tag(_strip_ib_ext(symbol),
                where)

        symbol = _strip_ib_ext(symbol, currency)
        ext = (_ib_listing_ext(asset_cat, self._cell(
                   row, header_map, 'Symbol'), currency, rs.pre['fii'])
               if asset_cat in ('Stocks', 'Warrants')
               else _ib_currency_ext(currency))
        symbol = f"{symbol}.{ext}"

        abs_qty = abs(qty)
        price = (M.units(abs(total_cost) / (abs_qty * multiplier), 8)
                 if abs_qty > 1e-6 and multiplier else 0.0)

        if self._ib_transfer_cancel_pair(rs, currency=currency, date=date,
                                         header_map=header_map, price=price, qty=qty, row=row,
                                         symbol=symbol, total_cost=total_cost,
                                         transfer_type=transfer_type, xfer_desc=xfer_desc):
            return Outcome.consumed()

        st.transactions.append({
            'action': 'TRANSFER',
            'date': date,
            'time': '09:30:00',
            'date_settle': date,
            'symbol': symbol,
            'quantity': qty,
            'currency': currency,
            'price': price,
            'net_amount': abs(total_cost),
            # The contract size the price above is per (IB's
            # Financial Instrument Information; 1 per share),
            # as on a trade row: the loss rule reads a moved
            # option's size from it (backlog TJ-0830: a mini's
            # stated 10 was dropped and 100 assumed).
            'multiplier': multiplier,
            'account': 'IB',
            'description': xfer_desc,
            # IB's Market Value: the fair market value of the
            # moved shares on the transfer day — the value of
            # an in-kind move to or from a registered account
            # (lib/in_kind), never a cost (lib/transfer_in).
            'market_value': abs(total_cost),
        })
        _name = self._security_name(
            asset_cat, self._cell(row, header_map, 'Symbol'), rs.pre['fii'],
            symbol, rs.pre.get('fii_all'))
        if _name:
            st.transactions[-1]['security_name'] = _name
        return Outcome.consumed()

    def _ib_transfer_cancel_pair(self, rs: "_IbRows", *, currency, date, header_map, price, qty,
                                 row, symbol, total_cost, transfer_type, xfer_desc) -> bool:
        """A Transfers row's cancellation pairing: a Ca row consumes its original (or
        waits for it in rs.pending_xfer_ca), an original consumes a Ca row waiting for
        it. Whether the row was consumed (a Ca row waiting for its original is consumed
        into the state at its row; one left at the end is a reversing leg)."""
        st = self._ib
        # Cancel/rebook: IB lists a reversed ACATS/ATON leg
        # as original + `Ca` cancellation (opposite qty) +
        # rebooked rows — a transfer reversed and rebooked
        # twice reads Out, Ca, Out, Ca, Out: arithmetically -N,
        # but each Ca leg reads as a +N ACQUISITION to the
        # superficial-loss walk. The Ca row consumes its
        # original wherever it sits in the statement: the
        # latest earlier-or-same-dated one before it (same
        # date preferred — a Ca posted days later used to miss
        # it, audit R1-61), else the next one after it (a Ca
        # listed first, R1-300). Only a Ca whose original is in
        # an earlier statement stays as a netting leg (end of
        # the walk).
        _xcode = self._cell(row, header_map, 'Code')
        _xleg = {'symbol': symbol, 'desc': xfer_desc,
                 'qty': qty, 'date': date}
        if 'Ca' in re.split(r'[;,\s]+', _xcode or ''):
            st.xfer_ca_keys.add((symbol, xfer_desc, date, qty))
            _hits = [
                _i for _i, _t in enumerate(st.transactions)
                if (_t.get('action') == 'TRANSFER'
                    and _t.get('symbol') == symbol
                    and (_t.get('date') or '') <= date
                    and abs(float(_t.get('quantity') or 0)
                            + qty) < 1e-9
                    and _t.get('description') == xfer_desc
                    and not self._ib_before_period(_t.get('date')))]
            _same = [_i for _i in _hits
                     if st.transactions[_i].get('date') == date]
            if _hits:
                _xo = st.transactions[(_same or _hits)[-1]]
                st.xfer_pairs.append((
                    (symbol, xfer_desc, _xo.get('date'),
                     float(_xo.get('quantity') or 0)),
                    (symbol, xfer_desc, date, qty)))
                del st.transactions[(_same or _hits)[-1]]
            else:
                rs.pending_xfer_ca.append(dict(
                    _xleg, currency=currency, price=price,
                    net=abs(total_cost), kind=transfer_type))
            return True

        _waiting = [_c for _c in rs.pending_xfer_ca
                    if _c['symbol'] == symbol
                    and _c['desc'] == xfer_desc
                    and date <= _c['date']
                    and abs(_c['qty'] + qty) < 1e-9]
        if _waiting and self._ib_before_period(date):
            _waiting = []               # a rebook: reconcile_files
        if _waiting:
            _c = next((_c for _c in _waiting if _c['date'] == date),
                      _waiting[0])
            rs.pending_xfer_ca.remove(_c)
            st.xfer_pairs.append((
                (symbol, xfer_desc, date, qty),
                (symbol, xfer_desc, _c['date'], _c['qty'])))
            return True
        return False

    @builds("trade", "expiry", "assign", "trade_cancel")
    def _ib_row_trades(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                        lineno: int, section: str) -> Outcome:
        """A Data row of the Trades section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        if section == 'Trades':
            self.require_columns(header_map, ('Asset Category',),
                                 section=section, where=where)
            asset_cat = self._cell(row, header_map,
                                   'Asset Category')
        else:
            # The row's own Asset Category when the section has
            # one: a futures-option expiry read as an equity
            # option became a phantom non-F: contract while the
            # real position never closed (audit R1-56).
            asset_cat = (self._cell(row, header_map,
                                    'Asset Category')
                         if 'Asset Category' in header_map
                         else 'Equity and Index Options')

        # Roll-up rows carry the asset category of what
        # they sum (`Total,Forex,...`), so they must be
        # recognized BEFORE the per-category dispatch or a
        # Forex subtotal counts as one more conversion.
        _pre_disc = self._cell(row, header_map, 'DataDiscriminator')
        if _pre_disc in ('SubTotal', 'Total'):
            return Outcome.nonevent("Trades roll-up row "
                                    f"({_pre_disc})")
        tr = _IbTrade(row=row, header_map=header_map, section=section, where=where,
                      asset_cat=asset_cat, pre_disc=_pre_disc)

        done = self._ib_trade_other_category(rs, tr)
        if done is None:
            done = self._ib_trade_level(rs, tr)
        if done is not None:
            return done

        # Every money/quantity/price/date/currency column is
        # REQUIRED and resolved by header name — a renamed or
        # missing column used to read as 0 (Comm/Fee renamed:
        # every fee vanished; Proceeds renamed: every gross 0)
        # while the parse "succeeded".
        proceeds_key = ('Proceeds' if 'Proceeds' in header_map
                        else 'Notional Value')
        if section == 'Trades':
            self.require_columns(
                header_map,
                ('Currency', 'Symbol', 'Date/Time', 'Quantity',
                 'T. Price', ('Proceeds', 'Notional Value'),
                 'Comm/Fee', 'Code'),
                section=section, where=where)
        else:
            # An expiry carries no money; its money columns
            # are optional here, but parsed strictly when
            # present.
            self.require_columns(
                header_map,
                ('Currency', 'Symbol', 'Date/Time', 'Quantity'),
                section=section, where=where)

        def _num(col, _where=where, _row=row, _hm=header_map,
                 optional=False):
            if col not in _hm:
                if optional:
                    return 0.0
                raise BrokerageParseError(
                    f"{_where}: missing column {col!r}")
            return parse_strict_number(
                self._cell(_row, _hm, col), field=col,
                where=_where)

        tr.symbol = self._cell(row, header_map, 'Symbol')
        # Preserve the raw symbol string as `description`
        # so taxjson-brokerage's security-overrides can
        # rewrite mislabeled IB tickers (every other
        # broker emits a `description` field; IB Trades
        # was the lone gap — without it the override
        # silently no-op'd on IB rows). For options the
        # raw symbol is the verbose `SAMPMN 16JAN26 100 P`
        # form, which is exactly what the user keys on
        # in ticker.map `EXTRACT` lines.
        tr.description = tr.symbol
        tr.currency = self._cell(row, header_map, 'Currency')
        if not tr.symbol or not tr.currency:
            raise BrokerageParseError(
                f"{where}: blank Symbol/Currency on a "
                f"{asset_cat} trade row")
        tr.date, tr.time = _ib_split_datetime(
            self._cell(row, header_map, 'Date/Time'), where)
        self._ib_trade_dates(rs, tr)
        tr.qty = _num('Quantity')
        opt_exp = section == 'Options Expirations'
        tr.price = _num('T. Price', optional=opt_exp)
        tr.proceeds_signed = _num(proceeds_key, optional=opt_exp)
        tr.comm_signed = _num('Comm/Fee', optional=opt_exp)
        tr.gross_proceeds = abs(tr.proceeds_signed)
        tr.mult = self._multiplier(asset_cat, tr.symbol, rs.pre['fii'], where)
        self._check_trade_money(
            where, tr.symbol, tr.qty, tr.price, tr.proceeds_signed,
            tr.comm_signed, tr.mult,
            check_notional=(proceeds_key in header_map),
            exact=asset_cat in FUTURES_CATEGORIES)

        # SIGNED commission. IB reports a charge NEGATIVE and a
        # rebate (option exchange/ORF rebates, a cancelled
        # trade's refunded commission) POSITIVE. The engine's
        # `fee` is positive = charged, so fee = -Comm/Fee, and
        # a rebate is a NEGATIVE fee. abs() turned every rebate
        # into a charge — a net error of 2x the rebate on each
        # rebated fill.
        # Cash-true net: a buy costs -(Proceeds + Comm/Fee), a
        # sell brings in Proceeds + Comm/Fee.
        tr.comm_fee = 0.0 - tr.comm_signed   # (no -0.0 for a zero charge)
        tr.net_amount = ((tr.gross_proceeds + tr.comm_fee) if tr.qty > 0
                         else (tr.gross_proceeds - tr.comm_fee))
        if asset_cat == 'Futures':
            # A futures price can be negative (WTI, April 2020):
            # the notional's SIGN is kept — a sale at -37.63
            # pays 37,630, a buy at -37.63 receives it. The
            # magnitude booked a loss as a gain (audit A2-0092;
            # S053-13 only silenced the schema error). Same
            # numbers as before for a positive price.
            tr.net_amount = (-(tr.proceeds_signed + tr.comm_signed)
                          if tr.qty > 0
                          else tr.proceeds_signed + tr.comm_signed)
        self._ib_trade_kind(tr)

        tr.full_symbol = self._ib_trade_symbol(rs, tr)

        return self._ib_trade_book(rs, tr)

    def _ib_trade_other_category(self, rs: "_IbRows", tr: "_IbTrade") -> Optional[Outcome]:
        """A Trades row of an asset category with no security branch: a Forex conversion
        (noted, booked to the cash tally), a subtotal, or a category this parser cannot
        book (refused when it moves money). The row's Outcome, or None for a security row."""
        if tr.asset_cat not in ('Stocks', 'Equity and Index Options', 'Futures',
                                'Options On Futures', 'Warrants'):
            if tr.asset_cat == 'Forex':
                # Explicit currency conversions (Trades / Forex rows, e.g.
                # USD.CAD). Counted, never a row of the position book: the
                # FX-on-cash ledger v2 reads them from the rows read noted
                # (`_ib_cash_events_of`; KNOWN_ISSUES "IB `Trades / Forex`
                # conversions are not in the position books").
                # Emitting them as a BUYSELL of a phantom `USD`/`CASH.USD`
                # asset would corrupt the position book, so they get one
                # explicit note instead of vanishing into the asset filter.
                # One detail level only (as for securities):
                # Order rows when the file has them.
                if (tr.pre_disc == 'Order'
                        or (tr.pre_disc in ('', 'Trade')
                            and not rs.pre['has_order_level'])):
                    self._book_forex(rs, tr.row, tr.header_map, tr.where)
                return Outcome.nonevent(
                    "Trades/Forex (currency conversion, "
                    "not modeled — docs/limits.md)")
            elif tr.asset_cat in ('Total', '') or 'Total' in tr.asset_cat:
                return Outcome.nonevent("Trades subtotal row")
            else:
                # Bonds, CFDs, Mutual Funds, a renamed category
                # — a real asset class the parser has no branch
                # for. A row that moves money is a disposition
                # or acquisition this parser would DROP: an
                # error, like an unknown money section (audit
                # S060-08 — it was a counted skip, caught only
                # when a Cash Report happened to be present).
                _raw_money = (self._cell(tr.row, tr.header_map,
                                         'Proceeds')
                              or self._cell(tr.row, tr.header_map,
                                            'Notional Value'))
                try:
                    _moves = abs(parse_strict_number(
                        _raw_money, field='Proceeds', where=tr.where,
                        allow_blank=True, blank=0.0)) > 0.005
                except BrokerageParseError:
                    _moves = True
                if _moves:
                    raise BrokerageParseError(
                        f"{tr.where}: Trades row in asset category "
                        f"{tr.asset_cat!r} "
                        f"({self._cell(tr.row, tr.header_map, 'Symbol')}"
                        f") moves money, and this parser has no "
                        f"branch for that category — refusing to "
                        f"drop it. Book it by hand in a .tt file "
                        f"and remove the rows from the export, or "
                        f"report the category.")
                return Outcome.skip(f"Trades/{tr.asset_cat}")
        return None

    def _ib_trade_level(self, rs: "_IbRows", tr: "_IbTrade") -> Optional[Outcome]:
        """Which detail level of a Trades row books (one per execution: Order rows when
        the file has them, else Trade rows); the row's Outcome when it is not that level
        (another level, a lot or a subtotal), else None."""
        # Flex queries configured with several detail levels
        # list each fill more than once (Order + Trade +
        # ClosedLot rows for the SAME execution); without
        # this filter each level emitted a BUYSELL and
        # split-fill disambiguation then PROTECTED the
        # duplicate from dedup — every trade double-counted.
        # Accept exactly one level, preferring 'Order' when
        # both appear; roll-up rows are skipped silently
        # (they are structure, not data).
        if 'DataDiscriminator' in tr.header_map:
            _disc = tr.pre_disc
            if _disc in ('ClosedLot', 'SubTotal', 'Total'):
                if _disc == 'ClosedLot':
                    self._closed_lot(rs.last_closing, tr.row,
                                     tr.header_map, tr.asset_cat, tr.where)
                return Outcome.nonevent("Trades roll-up row "
                                        f"({_disc})")
            # A Trade row is skipped only when an Order row
            # covers the SAME symbol and day (audit S060-10: a
            # file-global flag set by the first Order row
            # dropped every later Trade-only fill of any symbol,
            # and a Trade row listed before its Order row was
            # booked twice). The prescan refuses a file whose
            # two levels disagree on the quantity.
            if _disc == 'Trade' and 'Order' in rs.pre[
                    'order_levels'].get(
                    _ib_level_key(
                        tr.asset_cat,
                        self._cell(tr.row, tr.header_map, 'Symbol'),
                        self._cell(tr.row, tr.header_map, 'Currency'),
                        self._cell(tr.row, tr.header_map, 'Account'),
                        self._cell(tr.row, tr.header_map, 'Date/Time')),
                    {}):
                return Outcome.nonevent("Trades execution-level "
                                        "duplicate of an Order row")
            if _disc and _disc not in ('Order', 'Trade'):
                return Outcome.skip(f"Trades DataDiscriminator "
                                    f"{_disc}")
        return None

    def _ib_trade_dates(self, rs: "_IbRows", tr: "_IbTrade") -> None:
        """A Trades row's dates: the trade date on its market's calendar (a future by
        its venue's session), the settlement (computed, or the one the row states)."""
        date, time = tr.date, tr.time
        # The exchange's trade date when it is not the ET clock
        # date (an overnight-session US fill, an ASX fill):
        # tax-logic CA-DATE-SESSION / US-DATE-SESSION.
        _listing = (_ib_listing_ext(tr.asset_cat, tr.symbol, tr.currency,
                                    rs.pre['fii'])
                    if tr.asset_cat in ('Stocks', 'Warrants') else '')
        # A futures contract's venue (its Listing Exch) decides
        # its sessions: Montreal's 20:00 ET opening, CME's 24/7
        # crypto contracts (backlog TJ-0878, TJ-0879).
        _fexch = ''
        if tr.asset_cat in FUTURES_CATEGORIES:
            _finfo = (rs.pre['fii'].get((tr.asset_cat, tr.symbol.strip()))
                      or rs.pre['fii'].get((tr.asset_cat, re.sub(
                          r'\s+', ' ', tr.symbol.strip()))) or {})
            _fexch = _finfo.get('exch') or ''
        date, time, broker_time = _ib_market_trade_date(
            date, time, tr.asset_cat, tr.currency, _listing,
            symbol=tr.symbol, exch=_fexch)
        # Settled in the listing's market (lib/dates.
        # settlement_calendar, the rule every parser shares): a
        # USD unit on the TSX through CDS on the days both Canada
        # and the US settle, a USD line on the LSE on the UK
        # cycle.
        date_settle = get_ib_settlement(
            date, tr.asset_cat,
            settlement_calendar(
                f".{_listing}" if _listing else "", tr.currency),
            futures_settle=self.futures_settle)
        # The settle date the row states (a Flex query's Settle
        # Date Target) is the row's: the standard cycle is the
        # fallback when the statement gives none (backlog
        # TJ-0971). Futures keep the futures_settle rule
        # (CA-DATE-09 / US-DATE-09); an expiry its own day.
        _stated = ''
        if (tr.section == 'Trades'
                and tr.asset_cat not in FUTURES_CATEGORIES):
            for _scol in _IB_SETTLE_COLS:
                if _scol in tr.header_map:
                    _stated = _ib_stated_settle(
                        self._cell(tr.row, tr.header_map, _scol), date,
                        tr.where, _scol)
                    if _stated:
                        date_settle = _stated
                    break
        tr.date, tr.time, tr.broker_time = date, time, broker_time
        tr.date_settle, tr.stated = date_settle, _stated

    def _ib_trade_kind(self, tr: "_IbTrade") -> None:
        """A Trades row's kind from its Code: an assignment or exercise (A, Ex at no
        price), an expiry (settled on its date), the codes the booking reads."""
        date_settle = tr.date_settle
        action = 'BUYSELL'
        # IB packs multiple per-trade codes into one cell
        # (separators are `;`, `,`, or whitespace) — e.g.
        # `O;P`, `A`, `C;Ep`. Split into tokens and match `A`
        # exactly so codes that merely *contain* the letter
        # A (e.g. `Au`, `AEx`, `ADR`) don't get reclassified
        # as an option assignment. The zero-price guard
        # already limits damage, but the token match is the
        # principled check.
        code = self._cell(tr.row, tr.header_map, 'Code')
        code_tokens = re.split(r'[;,\s]+', code)
        # `Ex` (exercise) is the holder-side twin of `A`
        # (assignment): IB closes the option leg at T. Price
        # 0 with `C;Ex` and books the stock leg (`Ex;O` for
        # a call, `C;Ex` for a put) at the strike. Booked as
        # a plain BUYSELL, the option's premium was realized
        # as a 100% loss on the option instead of rolling
        # into the stock leg's cost (call) / proceeds (put)
        # the way an assignment's does. The zero-price
        # guard keeps the stock leg (price = strike) a
        # BUYSELL, which is the leg that pops the premium.
        if (('A' in code_tokens or 'Ex' in code_tokens)
                and abs(tr.price) < 1e-5):
            action = 'ASSIGN'

        # Expiry: the `Ep` code, a row of the Options
        # Expirations section, or a zero-price zero-proceeds
        # CLOSE of an option that isn't an assignment/
        # exercise. An expiry has no settlement cycle — the
        # contract ceases to exist on its expiry date — so
        # date_settle == date. The T+1 applied to every
        # Trades row pushed a Dec-31 expiry into the NEXT
        # tax year on the (Canadian) settle-date basis.
        # Assignment/exercise option legs keep T+1: their
        # premium rolls into the stock leg, which really
        # settles T+1, and the pair must share a settle
        # date so no unrelated same-underlying trade can
        # consume the staged premium in between.
        is_expiry = (
            tr.section == 'Options Expirations'
            or 'Ep' in code_tokens
            or (tr.asset_cat in ('Equity and Index Options',
                              'Options On Futures')
                and action != 'ASSIGN'
                and 'C' in code_tokens
                and abs(tr.price) < 1e-9
                and tr.gross_proceeds < 1e-9))
        if is_expiry and tr.date:
            date_settle = tr.date
        tr.action, tr.code, tr.code_tokens = action, code, code_tokens
        tr.date_settle, tr.is_expiry = date_settle, is_expiry

    def _ib_trade_symbol(self, rs: "_IbRows", tr: "_IbTrade"):
        """The symbol a Trades row books: an option's or a future's contract form (F:
        for a future), a stock's on its listing."""
        symbol = tr.symbol
        if tr.asset_cat in ('Equity and Index Options', 'Options On Futures'):
            symbol = self._option_symbol(
                symbol, tr.asset_cat, rs.pre['fii'], rs.pre['root_alias'],
                rs.aliased_roots, tr.where)
            if tr.asset_cat == 'Options On Futures':
                symbol = f"F:{symbol}"
        elif tr.asset_cat == 'Futures':
            symbol = symbol.replace(' ', '.')
            symbol = f"F:{symbol}"
        else:
            symbol = symbol.replace(' ', '.')
            self._check_symbol_tag(_strip_ib_ext(symbol),
                tr.where)

        # Remove common known exchange extensions to avoid doubling
        symbol = _strip_ib_ext(symbol, tr.currency)

        ext = (_ib_listing_ext(tr.asset_cat, tr.description, tr.currency, rs.pre['fii'])
               if tr.asset_cat in ('Stocks', 'Warrants')
               else _ib_currency_ext(tr.currency))
        full_symbol = f"{symbol}.{ext}"
        return full_symbol

    def _ib_trade_book(self, rs: "_IbRows", tr: "_IbTrade") -> Outcome:
        """Book a Trades row: the trade (its open/close code, the broker's basis, the
        security name, the account, the stated settlement, the execution ids), a
        cancellation, an expiry, an assignment or exercise leg, and the cash tally."""
        st = self._ib
        _trade_tx = {
            'action': tr.action,
            'date': tr.date,
            'time': tr.time,
            'date_settle': tr.date_settle,
            'symbol': tr.full_symbol,
            'quantity': tr.qty,
            'currency': tr.currency,
            'price': tr.price,
            'fee': tr.comm_fee,
            'net_amount': tr.net_amount,
            'gross_amount': tr.gross_proceeds,
            # Contract size (IB's Financial Instrument
            # Information Multiplier; 100 per equity option,
            # 1000 per crude-oil future, 0.1 per micro, 1 per
            # share). The schema notional check uses it —
            # futures used to trip it on every row.
            'multiplier': tr.mult,
            'account': 'IB',
            'description': tr.description,
        }
        if tr.broker_time:
            _trade_tx['broker_time'] = tr.broker_time
        # IB's open/close marker (O = opening, C = closing; a
        # sale coded `C;O` closed a long and opened a short in
        # one fill), in IB's order. The engine books by sign;
        # the missing-history checks read this to tell a real
        # short (O) from a sale of a position bought before the
        # data (C) — audit S013-00 / S058-02 / S060-12. A `Ca`
        # cancellation row reverses another fill: its marker
        # says nothing about this account's position.
        _oc = [c for c in tr.code_tokens if c in ('O', 'C')]
        if _oc and 'Ca' not in tr.code_tokens:
            _trade_tx['open_close'] = ';'.join(_oc)
            if _oc == ['C'] and 'Basis' in tr.header_map:
                # IB's cost of what the closing trade closed —
                # evidence for the missing-history report, in
                # the trade's own currency (never booked).
                _braw = self._cell(tr.row, tr.header_map, 'Basis')
                try:
                    _bv = parse_strict_number(
                        _braw, field='Basis', where=tr.where) \
                        if _braw.strip() else None
                except (BrokerageParseError, ValueError):
                    _bv = None
                if _bv is not None and abs(_bv) > 1e-9:
                    _trade_tx['broker_basis'] = (
                        f"{M.units(abs(_bv), 2):,.2f} {tr.currency}")
        _name = self._security_name(
            tr.asset_cat, tr.description, rs.pre['fii'], _trade_tx['symbol'],
            rs.pre.get('fii_all'))
        if _name:
            _trade_tx['security_name'] = _name
        # `Ca` = IB CANCELLED an earlier fill: this row reverses
        # it (opposite quantity, same date/time and price). It
        # used to book as an ordinary trade — a phantom round
        # trip whose rebooking turned an allowed loss into two
        # superficial-loss denials (audit R1-51). Marked here,
        # dropped with its original after the loop; an original
        # in an earlier statement is paired by taxjson-merge2.
        # The Cash Report booking below keeps both rows (IB's
        # own totals include both).
        if (tr.asset_cat == 'Stocks' and 'A' in tr.code_tokens
                and 'Ca' not in tr.code_tokens and abs(tr.price) < 1e-9
                and abs(tr.gross_proceeds) < 0.005):
            # Booked as an ASSIGN at $0 with no word (TJ-4569 (2)).
            raise BrokerageParseError(
                f"{tr.where}: {tr.full_symbol}: a share leg of {tr.qty:+g} "
                f"coded {tr.code} (an assignment) at T. Price 0 and Proceeds "
                f"0 — an assignment's share leg is at the strike; refusing "
                f"to book the shares at $0. Fill the T. Price and Proceeds "
                f"from the statement, or delete the row and book the "
                f"assignment in a .tt file.")
        if (tr.asset_cat in ('Stocks', 'Warrants') and tr.qty > 0
                and tr.section == 'Trades'
                and abs(tr.price) < 1e-9 and tr.gross_proceeds < 0.005
                and 'Ca' not in tr.code_tokens
                and not ('A' in tr.code_tokens or 'Ex' in tr.code_tokens)):
            # A stock BUY at ZERO cost books a $0 ACB, and the
            # whole sale later becomes gain: almost always a
            # transfer or journal row (the generic importer
            # refuses it, R1-120; audit A2-0263).
            emit_line(f"{ATTENTION_PREFIX} {tr.where}: {tr.full_symbol}: a "
                  f"buy of {tr.qty:g} at ZERO cost (T. Price and "
                  f"Proceeds 0) — booked at $0 cost. A zero-cost "
                  f"buy is almost always a transfer or journal "
                  f"row: give the real cost in a .tt file and "
                  f"remove the row, if so.")
        if 'Account' in tr.header_map:
            _ib_trade_meta(self, _trade_tx)['acct'] = self._cell(
                tr.row, tr.header_map, 'Account')
        if tr.stated and tr.date_settle == tr.stated:
            _ib_trade_meta(self, _trade_tx)['settle_stated'] = True
        # The broker's id of the execution (optional Flex
        # columns): two fills alike in every booked field are
        # two trades when their ids differ, in one statement or
        # in two (reconcile_files, backlog TJ-0966).
        for _xcol in _IB_EXEC_ID_COLS:
            _xid = (self._cell(tr.row, tr.header_map, _xcol)
                    if _xcol in tr.header_map else '')
            if _xid:
                _ib_trade_meta(self, _trade_tx).setdefault(
                    'execs', {}).setdefault(_IB_EXEC_NS[_xcol], _xid)
        if 'Ca' in tr.code_tokens:
            # (description stays the raw symbol: the security
            # overrides key on it, and the original must get the
            # same rewrite for the pair to match.)
            _trade_tx['type'] = TRADE_CANCEL_TYPE
            rs.trade_cancels.append(_trade_tx)
        st.transactions.append(_trade_tx)
        rs.last_closing = (_trade_tx if _trade_tx.get('broker_basis')
                           else None)
        if tr.is_expiry:
            self._expiries.append(_trade_tx)
        if tr.action == 'ASSIGN' and tr.asset_cat == 'Equity and Index Options':
            rs.assign_option_legs.append(_trade_tx)
        elif (tr.asset_cat in ('Stocks', 'Warrants')
              and ('A' in tr.code_tokens or 'Ex' in tr.code_tokens)):
            rs.assign_stock_legs.append(_trade_tx)
        if 'Ex' in tr.code_tokens and 'Ca' not in tr.code_tokens:
            if (tr.asset_cat == 'Warrants' and tr.action == 'ASSIGN'
                    and tr.qty < 0):
                rs.warrant_ex_legs.append((_trade_tx, tr.where, tr.code))
            elif tr.asset_cat == 'Stocks' and tr.qty > 0:
                rs.share_ex_legs.append(_trade_tx)
        # Cash Report: futures settle daily through "Cash
        # Settling MTM", never through Trades (Sales/Purchase).
        if tr.asset_cat != 'Futures':
            self._ib_book(rs, 'Trades (Sales + Purchase)', tr.currency,
                          tr.proceeds_signed)
        self._ib_book(rs, 'Commissions', tr.currency, tr.comm_signed)
        return Outcome.consumed()

    @builds("trade", "split", "rename", "stock_dividend")
    def _ib_row_corporate_actions(self, rs: "_IbRows", row: List[str], header_map: Dict[str, int],
                                   lineno: int, section: str) -> Outcome:
        """A Data row of the Corporate Actions section (the template's build through `_ROW_METHODS`, 5h (8b))."""
        st = self._ib
        # Required columns resolved by header name; a missing
        # one fails the parse (Quantity/Value/Proceeds are the
        # share and cash legs, Code carries the `Ca` marker a
        # restatement depends on).
        where = f"{shown_name(st.path)} line {lineno} ({section})"
        self.require_columns(
            header_map, ('Currency', 'Date/Time', 'Description',
                         'Quantity', 'Proceeds', 'Value', 'Code'),
            section=section, where=where)
        currency = self._cell(row, header_map, 'Currency')
        description = self._cell(row, header_map, 'Description')
        # Subtotal rows (Asset Category 'Total', blank
        # Currency) must be recognized BEFORE the currency
        # → suffix lookup: the blank currency used to reach
        # _ib_currency_ext and fire the "IB currency '' has
        # no exchange-suffix mapping" warning on every
        # statement with a Corporate Actions total.
        _ca_cat = self._cell(row, header_map, 'Asset Category')
        if (not currency or 'Total' in currency
                or _ca_cat.startswith('Total')):
            return Outcome.nonevent(f"{section} subtotal row")

        date, time = _ib_split_datetime(
            self._cell(row, header_map, 'Date/Time'), where)
        qty = parse_strict_number(
            self._cell(row, header_map, 'Quantity'),
            field='Quantity', where=where)
        val = parse_strict_number(
            self._cell(row, header_map, 'Value'), field='Value',
            where=where)
        # Cash actually paid (cash in lieu, a cash tender).
        proceeds = parse_strict_number(
            self._cell(row, header_map, 'Proceeds'),
            field='Proceeds', where=where)

        # The security the row DELIVERS: the first token of its
        # trailing `(TICKER, NAME, ISIN)` parenthetical (the
        # tender branch's S013-06 reading, now shared by the
        # split, stock-dividend and cash-in-lieu branches —
        # audit A2-0085 / A2-0093 / A2-0257 / A2-1033).
        _dm_all = _IB_DELIVERED_RE.search(description)
        ca = _IbCa(row=row, header_map=header_map, section=section, where=where,
                   currency=currency, description=description, cat=_ca_cat, date=date,
                   time=time, qty=qty, val=val, proceeds=proceeds,
                   base_ext=_ib_currency_ext(currency), fii=rs.pre['fii'],
                   deliv=(_dm_all.group(1).strip().replace(' ', '.') if _dm_all else ''))

        if self._ib_ca_cancel(rs, ca):
            return Outcome.consumed()   # a Ca row, into the state (pending or paired)

        # Every branch below builds STOCK symbols (ticker + the
        # currency suffix, multiplier 1). An option or futures
        # row (a contract adjustment on a split) booked there
        # became an equity SPLIT on an invented symbol while the
        # real contract never rolled (audit S058-16).
        if _ca_cat and _ca_cat not in ('Stocks', 'Warrants'):
            if qty != 0 or abs(proceeds) > 0.005:
                return self._ib_ca_unbooked(
                    ca, f"{shown_name(st.path)} {date}: {_ca_cat} corporate "
                    f"action {description[:90]!r} (quantity "
                    f"{qty:g}) — contract adjustments are not "
                    f"translated; book the old and the adjusted "
                    f"contract by hand in a .tt file.")
            return self._ib_ca_nonevent(ca, f"{section} zero-quantity row")

        return self._ib_ca_event(rs, ca)

    def _ib_ca_nonevent(self, ca: "_IbCa", label: str) -> Outcome:
        """A recognized non-event Corporate Actions row: the label is kept on
        its effect, so a later `Ca` of the row takes it back out of the tally
        (audit A2-0600 / A2-1030 / A2-1034)."""
        return self._ib_ca_settle(ca.eff, Outcome.nonevent(label))

    def _ib_ca_unbooked(self, ca: "_IbCa", msg: str) -> Outcome:
        """A real Corporate Actions event nothing books (an option adjustment,
        a spin-off debit on a short parent, a share-for-share tender): one
        UNBOOKED line at the end (`unbooked_ca`), the row a skip."""
        self._ib.unbooked_ca.append(msg)
        ca.eff.update(kind='unbooked', msg=msg)
        return self._ib_ca_settle(ca.eff, Outcome.skip(
            f"{ca.section} row not booked (see UNBOOKED warning)"))

    def _ib_ca_cancel(self, rs: "_IbRows", ca: "_IbCa") -> bool:
        """A Corporate Actions row coded Ca (a cancellation): it undoes its original's
        effect when the original is in this statement on its date (ca_undo), else it
        waits for one (st.pending_ca: later in the statement, or another statement's);
        whether it was one."""
        st = self._ib
        # Cancel/rebook restatements: IB re-lists a corrected
        # corporate action as original + `Ca` cancellation
        # (same description, negated quantity) + rebooked
        # rows. Every translated row records its effect
        # (`_ib_ca_record`, in `_IbParse.ca_effects`); a Ca row
        # UNDOES its original's effect
        # — a cancelled split's legs come off the split (the
        # SPLIT row goes when no leg is left), a cancelled
        # cash-in-lieu / tender sale / spinoff removes the
        # rows it emitted, a cancelled untranslated merger
        # leaves the unhandled tally. Only the split branch
        # used to be spared the doubling; a restated split
        # 3:1 -> 2:1 emitted BOTH ratios. A Ca row whose
        # original is not (yet) seen waits for it; unmatched
        # at end of file it is a LOUD skip.
        _cacode = self._cell(ca.row, ca.header_map, 'Code')
        if 'Ca' in re.split(r'[;,\s]+', _cacode or ''):
            _ca = {'desc': ca.description, 'date': ca.date,
                   'qty': ca.qty, 'where': ca.where,
                   'currency': ca.currency, 'cat': ca.cat}
            st.ca_seen.append(_ca)
            _orig = self.ca_undo(_ca, same_date_only=True)
            if _orig:
                st.ca_pairs.append((_ca, _orig['date']))
            else:
                # Consumed into the state at its row: one no original
                # claims moves to its skip at the end (_ib_finish_unmatched_ca).
                st.pending_ca.append(_ca)
            return True
        return False

    def _ib_ca_event(self, rs: "_IbRows", ca: "_IbCa") -> Outcome:
        """Book a Corporate Actions row's event, by kind in turn: a tender leg, cash in
        lieu, a split, a spin-off, a cash takeover, a stock dividend, a merger the
        election books; else a skip (not translated). The row's Outcome, its effect
        recorded for a cancellation to undo (`_ib_ca_settle`)."""
        st = self._ib
        done = self._ib_ca_tender(rs, ca)
        if done is not None:
            return done

        handled = (self._ib_ca_cil(rs, ca)
                   or self._ib_ca_split(ca))

        if not handled:
            # Spinoff 1 for 10. Gated on `not handled` so a
            # description that happens to contain both "Split"
            # and "Spinoff" keywords is not read twice (the split
            # above, `_ib_ca_split`, already consumed it). A spin-off is a
            # TAX ELECTION (a dividend in kind at FMV, or s.86.1 /
            # a Canadian butterfly's ACB allocation), so its rows
            # are taxjson-corp-actions' — which carries IB's Value
            # as the broker FMV. This branch used to book every
            # one as a dividend at |Value| with no election
            # (2026-09 audit). Recognized, not booked, here.
            # A spin-off DEBIT (negative quantity) is a short
            # parent's delivery obligation for the spun-off shares.
            # taxjson-corp-actions books only the long side, so it
            # used to fall into the unhandled tally as a
            # "non-spin-off" row (audit S058-22).
            if ca.qty < 0 and ib_spinoff_parts(ca.description) is not None:
                return self._ib_ca_unbooked(
                    ca, f"{shown_name(st.path)} {ca.date}: spin-off debit of {ca.qty:g} on "
                    f"a SHORT parent position ({ca.description[:90]!r}, "
                    f"Value {M.shown(ca.val, 2):,.2f}) — the short spun-off position "
                    f"is not booked; add it by hand in a .tt file.")
            if ca.qty > 0 and ib_spinoff_parts(ca.description) is not None:
                ca.eff.update(kind='spinoff')
                st.corp_owned_rows.append(ca.description)
                return self._ib_ca_nonevent(
                    ca, f"{ca.section} spin-off row (booked by "
                    f"taxjson-corp-actions after the election)")

            done = self._ib_ca_cash_merger(ca)
            if done is None:
                done = self._ib_ca_stock_dividend(ca)
            if done is not None:
                return done

            # A share-for-share merger (or a merger shape nothing
            # can book, which becomes a blocking `unsupported`
            # event): taxjson-corp-actions owns it, after the
            # election. Not an unhandled row.
            if ib_merger_owned(ca.description):
                st.corp_owned_rows.append(ca.description)
                ca.eff.update(kind='corp_owned')
                return self._ib_ca_nonevent(
                    ca, f"{ca.section} merger row (booked by "
                    f"taxjson-corp-actions after the election)")

        # Anything else (Merger/Acquisition, name change, etc.)
        # — IB's row format varies by event type and matching
        # paired rows safely is tricky. Track per ticker so we
        # can warn the user once at end of parse. We pull a
        # rough ticker out of the description's leading token.
        if handled:
            outcome = Outcome.consumed()
        elif ca.qty != 0:
            m = re.match(r'\s*([A-Z0-9\.]{1,12})', ca.description)
            ticker = m.group(1) if m else '<unknown>'
            st.unhandled_ca_tickers[ticker] = st.unhandled_ca_tickers.get(ticker, 0) + 1
            _cat = (f"{ca.section} row not translated (see UNBOOKED "
                    f"warning)")
            ca.eff.update(kind='unhandled', ticker=ticker)
            outcome = Outcome.skip(_cat)
        else:
            outcome = Outcome.nonevent(f"{ca.section} zero-quantity row")
        return self._ib_ca_settle(ca.eff, outcome)

    def _ib_ca_tender(self, rs: "_IbRows", ca: "_IbCa") -> Optional[Outcome]:
        """A Corporate Actions row of a tender offer's leg (the shares tendered, the
        cash or the shares delivered): gathered by tender (rs.tender_legs) for the end
        of the parse; the row's Outcome, or None for another kind."""
        st = self._ib
        # Tender / voluntary-offer journals (`_IbRows.tender_legs`). A zero-proceeds leg
        # only moves shares between the ticker and its `.TEN` placeholder — a recognized
        # non-event, netted and noted at end of parse. A leg carrying cash on a negative
        # quantity is the ACCEPTED cash tender: the shares are gone and the cash is
        # proceeds — booked as a sale of the ROOT symbol (the placeholder never entered
        # the book) with a loud NOTE, so the disposition can't vanish into the unhandled
        # tally the way every other non-split/spinoff corporate action does.
        _troot = ib_tender_root(ca.description)
        if _troot is not None:
            _tsym = f"{_troot}.{ca.listing_ext(_troot)}"
            _tl = rs.tender_legs.setdefault(_tsym, {
                'rows': 0, 'parked': 0.0, 'cash_qty': 0.0,
                'cash': 0.0, 'dates': [], 'currency': ca.currency})
            _tl['rows'] += 1
            _tl['dates'].append(ca.date)
            ca.eff['tender'] = _tl
            # Which leg touches the placeholder line is
            # fixed by the row kind, not by the leading
            # token (IB may reuse the root's description
            # on both legs): a `Tendered to` row's POSITIVE
            # leg moves shares INTO the placeholder, a
            # `Voluntary Offer Allocation` row's NEGATIVE
            # leg moves them OUT.
            _tender_in = 'tendered to' in ca.description.lower()
            _is_placeholder = _ib_tender_is_placeholder(
                ca.description, ca.qty)
            ca.eff.update(kind='tender', tender=_tl,
                        parked=ca.qty if _is_placeholder else 0.0)
            # An allocation that DELIVERS another security (a
            # share-for-share exchange offer) is a merger, not a
            # journal: it used to be netted as a no-op, the old
            # shares staying in the book and the acquirer's
            # never arriving (audit S013-06).
            _dm = _IB_DELIVERED_RE.search(ca.description)
            _delivered = (_dm.group(1).strip().replace(' ', '.')
                          if _dm else '')
            if (not _tender_in and ca.qty > 0 and _delivered
                    and _delivered.upper() != _troot.upper()):
                _tl['foreign'] = True
                return self._ib_ca_unbooked(
                    ca, f"{shown_name(st.path)} {ca.date}: tender/exchange offer "
                    f"for {_tsym} delivered {ca.qty:g} {_delivered} "
                    f"(another security) — a share-for-share "
                    f"exchange is a disposition of {_tsym}"
                    f"{self.law(' (or a s.85.1 rollover)', ' (or a §354/§368 reorganization)', ' unless a tax-deferred rollover applies')}"
                    f": book it by hand in a .tt file.")
            if abs(ca.proceeds) < 0.005:
                if _is_placeholder:
                    _tl['parked'] += ca.qty
                return self._ib_ca_nonevent(
                    ca, f"{ca.section} tender/voluntary-offer share "
                    f"journal (zero proceeds)")
            if ca.qty < 0:
                _cash = abs(ca.proceeds)
                _ttx = {
                    'action': 'BUYSELL',
                    'date': ca.date,
                    'time': ca.time,
                    'date_settle': ca.date,
                    'symbol': _tsym,
                    'quantity': ca.qty,
                    'currency': ca.currency,
                    'price': M.units(_cash / -ca.qty, 8),
                    'fee': 0.0,
                    'net_amount': _cash,
                    'gross_amount': _cash,
                    'multiplier': 1.0,
                    'account': 'IB',
                    'description': ca.description,
                }
                st.transactions.append(_ttx)
                _tl['cash_qty'] += -ca.qty
                _tl['cash'] += _cash
                if _is_placeholder:
                    _tl['parked'] += ca.qty
                ca.eff.update(txs=[_ttx], cash=_cash)
                return self._ib_ca_settle(ca.eff, Outcome.consumed())
            # Cash on a POSITIVE leg is a shape we have not
            # seen — loud bucket, never a silent drop.
            _cat = (f"{ca.section} tender row with proceeds on a "
                    f"positive quantity")
            return self._ib_ca_settle(ca.eff, Outcome.skip(_cat))
        return None

    def _ib_ca_cil(self, rs: "_IbRows", ca: "_IbCa") -> bool:
        """A Corporate Actions row of cash in lieu of a fraction: the fraction's sale,
        joined to its split (cil_join) or waiting for it; whether it handled the row."""
        st = self._ib
        # Cash in lieu of the fractional share a ratio left
        # over: a disposition of that fraction for the cash
        # — the same shape the RBC parser's CIL rows take.
        # Left unbooked, the fraction sat in the pool
        # forever and the cash was never proceeds. Checked
        # before the split branch so a CIL description
        # that also names the split is not read as a leg.
        cil_match = _IB_CIL_RE.search(ca.description)
        if cil_match and ca.qty < 0:
            ticker = cil_match.group(1).strip().replace(' ', '.')
            if (ca.deliv and ca.deliv.upper() != ticker.upper()
                    and not ca.deliv.upper().endswith('.OLD')):
                # The fraction is of the DELIVERED security (a
                # merger's or a renaming split's new line): the
                # parent may no longer exist (audit A2-1033).
                ticker = ca.deliv
            symbol = f"{ticker}.{ca.listing_ext(ticker)}"
            self._check_symbol_tag(ticker, ca.where)
            # Proceeds is the cash paid (a required column; a
            # blank one is refused). Value is IB's MARKET value
            # of the fraction, never cash: an explicit 0 used
            # to be replaced by it, booking a gain nobody was
            # paid (audit S058-17).
            cash = abs(ca.proceeds)
            _cil_msg = None
            if cash < 0.005:
                _cil_msg = (
                    f"warning: {ca.where}: {symbol}: cash in lieu "
                    f"of {-ca.qty:g} share(s) with Proceeds 0 — "
                    f"booked as a disposal for no cash (IB's "
                    f"Value {M.shown(ca.val, 2):,.2f} is a market value, not "
                    f"cash paid). Check the statement.")
                st.late_warnings.append(_cil_msg)
            _ctx = {
                'action': 'BUYSELL',
                'date': ca.date,
                'time': ca.time,
                'date_settle': ca.date,
                'symbol': symbol,
                'quantity': ca.qty,
                'currency': ca.currency,
                'price': M.units(cash / -ca.qty, 8),
                'net_amount': cash,
                'multiplier': 1.0,
                'account': 'IB',
                'description': ca.description
            }
            st.transactions.append(_ctx)
            st.cil_seen.add(_ib_cil_key(symbol, ca.date, -ca.qty))
            ca.eff.update(kind='cil', txs=[_ctx], symbol=symbol,
                        frac=-ca.qty, split=None, msg=_cil_msg)
            # The fraction belongs to the split of this symbol
            # NEAREST its date, within a week, in either row
            # order: "the latest split on or before" folded an
            # unrelated event's fraction into a split months
            # later, and a CIL dated before its legs joined a
            # split or not depending on row order (audit
            # S058-18 / S060-14).
            _info = self.cil_join({'symbol': symbol, 'date': ca.date,
                                   'frac': -ca.qty})
            if _info is not None:
                ca.eff['split'] = _info
            else:
                st.pending_cil.append({'symbol': symbol, 'date': ca.date,
                                    'frac': -ca.qty, 'eff': ca.eff})
            return True
        return False

    def _ib_ca_split(self, ca: "_IbCa") -> bool:
        """A Corporate Actions row of a split (IB's 'Split N for M'): the split event,
        its legs joined by (symbol, date, ratio) in _IbParse.emitted_splits; whether a
        split handled the row."""
        st = self._ib
        # Split 3 for 2. NOT gated on qty > 0: a reverse split's
        # share-reduction leg arrives with NEGATIVE quantity (and
        # some events ship ONLY that leg) — the old `qty > 0`
        # gate silently dropped it, leaving the pool 10x too big.
        # The ratio math (new/old from the description) is
        # sign-independent; paired negative+positive legs are
        # collapsed by the per-file (symbol, date, ratio) key.
        # Ratio terms parsed strictly.
        split_match = _IB_SPLIT_RE.search(ca.description)
        if split_match and abs(ca.proceeds) > 0.005:
            # Its cash was dropped with no word (TJ-4569 (1)). IB's
            # wording of a cash in lieu is unverified (_IB_CIL_RE): a
            # real row may stop here, so the fix names the lines to write.
            raise BrokerageParseError(_ib_split_cash_refusal(
                ca, split_match.group(1).strip().replace(' ', '.')))
        if split_match:
            ticker = split_match.group(1).strip().replace(' ', '.')
            self._check_symbol_tag(ticker, ca.where)
            new_sh = parse_strict_number(split_match.group(3),
                                         field='split ratio',
                                         where=ca.where)
            old_sh = parse_strict_number(split_match.group(4),
                                         field='split ratio',
                                         where=ca.where)
            ratio = new_sh / old_sh if old_sh != 0 else 1.0
            symbol = f"{ticker}.{ca.listing_ext(ticker)}"
            split_key = (symbol, ca.date, M.units(ratio, 9))
            info = st.emitted_splits.get(split_key)
            if info is None:
                tx = {
                    'action': 'SPLIT',
                    'date': ca.date,
                    'time': ca.time,
                    'date_settle': ca.date,
                    'symbol': symbol,
                    'symbol_new': symbol,
                    'quantity': ratio,
                    'currency': ca.currency,
                    'account': 'IB',
                    'description': ca.description
                }
                st.transactions.append(tx)
                info = {'tx': tx, 'new': 0.0, 'old': 0.0,
                        'text_ratio': ratio, 'key': split_key,
                        'cil': 0.0, 'legs': []}
                st.emitted_splits[split_key] = info
                for _c in [c for c in st.pending_cil
                           if c['symbol'] == symbol
                           and abs(_days_between(c['date'], ca.date))
                           <= _IB_CIL_WINDOW]:
                    info['cil'] += _c['frac']
                    _c['eff']['split'] = info
                    st.pending_cil.remove(_c)
            if ca.qty > 0:
                info['new'] += ca.qty
            elif ca.qty < 0:
                info['old'] += -ca.qty
            if ca.qty:
                info['legs'].append(ca.qty)
            _ib_refine_split_ratio(info)
            ca.eff.update(kind='split', split=info)
            if (ca.qty > 0 and ca.deliv
                    and ca.deliv.upper() != ticker.upper()):
                # The new leg names ANOTHER line: a split that
                # also renames the security (OLDT 1-for-10 into
                # NEWT). The pool moves to it — it stayed on the
                # old ticker and the NEWT sale went short (audit
                # A2-0085 / A2-0257). Tax-logic CA-ACB-04.
                info['tx']['symbol_new'] = f"{ca.deliv}.{ca.listing_ext(ca.deliv)}"
                ca.eff['renames'] = True
                for _c in [c for c in st.pending_cil
                           if c['symbol'] == info['tx']['symbol_new']
                           and abs(_days_between(c['date'], ca.date))
                           <= _IB_CIL_WINDOW]:
                    info['cil'] += _c['frac']
                    _c['eff']['split'] = info
                    st.pending_cil.remove(_c)
                _ib_refine_split_ratio(info)
            return True
        return False

    def _ib_ca_cash_merger(self, ca: "_IbCa") -> Optional[Outcome]:
        """A Corporate Actions row of a cash takeover (a merger for cash per share): the
        shares' sale at the cash paid; the row's Outcome, or None for another kind."""
        st = self._ib
        # A cash takeover ('Merged(Acquisition) FOR USD 30.00
        # PER SHARE'): the shares are bought out for cash — a
        # disposition at the cash amount, booked as a sale. It
        # used to fall into the unhandled NOTE and the position
        # stayed in inventory forever (2026-09 audit).
        _cash_m = ib_cash_merger(ca.description)
        if _cash_m is not None:
            _ticker, _ccur, _per_sh = _cash_m
            if ca.qty < 0:
                _cash = abs(ca.proceeds) or M.units(_per_sh * -ca.qty, 2)
                if not abs(ca.proceeds):
                    # Said: the amount is the description's, not IB's
                    # (TJ-4569 (3); cash in lieu warns alike, S058-17).
                    # In the end-of-run list too (END_OF_RUN_MARK).
                    emit_line(f"{ATTENTION_PREFIX} {END_OF_RUN_MARK}"
                              f"{ca.where}: {_ticker} cash "
                              f"takeover: Proceeds is 0; booked at the "
                              f"description's {_per_sh:.2f} x {-ca.qty:g} = "
                              f"{M.shown(_cash, 2):.2f} {_ccur}; check it "
                              f"against the cash received.")
                _ttx = {
                    'action': 'BUYSELL',
                    'date': ca.date,
                    'time': ca.time,
                    'date_settle': ca.date,
                    'symbol': f"{_ticker}.{ca.listing_ext(_ticker)}",
                    'quantity': ca.qty,
                    'currency': ca.currency,
                    'price': M.units(_cash / -ca.qty, 8),
                    'fee': 0.0,
                    'net_amount': _cash,
                    'gross_amount': _cash,
                    'multiplier': 1.0,
                    'account': 'IB',
                    'description': ca.description,
                }
                st.transactions.append(_ttx)
                _ct_msg = (f"{_ticker}.{ca.listing_ext(_ticker)} {-ca.qty:g} sh for "
                           f"{M.shown(_cash, 2):.2f} {ca.currency} on {ca.date}")
                st.cash_takeovers.append(_ct_msg)
                ca.eff.update(kind='cash_takeover', txs=[_ttx],
                            msg=_ct_msg)
                outcome = Outcome.consumed()
            else:
                outcome = Outcome.skip(f"{ca.section} cash-takeover row with a "
                                       f"positive quantity")
            return self._ib_ca_settle(ca.eff, outcome)
        return None

    def _ib_ca_stock_dividend(self, ca: "_IbCa") -> Optional[Outcome]:
        """A Corporate Actions row of a stock dividend (IB's 'Stock Dividend ... N for
        M'): the shares received, at the value IB states; the row's Outcome, or None for
        another kind."""
        st = self._ib
        # A stock dividend: new shares delivered in kind. They
        # used to be dropped (a phantom short at the next full
        # sale — audit S058-24). Emitted as a NEUTRAL stock-
        # dividend event (a $0 BUYSELL typed stock_dividend, IB's
        # Value in the note): the gains engine applies the
        # country's rule (partition INPUTS-01).
        _sd = _IB_STOCK_DIV_RE.match(ca.description)
        if _sd:
            _sd_tk = _sd.group(1).strip().replace(' ', '.')
            if (ca.qty > 0 and ca.deliv
                    and ca.deliv.upper() != _sd_tk.upper()):
                # Shares of ANOTHER security (another class:
                # class A paying class C) are not new shares of the
                # parent: booked into its pool they inflated it
                # and the delivered line's sale went short. The
                # cost split between two securities is each
                # country's rule (CA-STKDIV-01, US-STKDIV-02):
                # not guessed here (audit A2-0093).
                return self._ib_ca_unbooked(
                    ca, f"{shown_name(st.path)} {ca.date}: stock dividend "
                    f"on {_sd_tk} paid {ca.qty:g} share(s) of "
                    f"ANOTHER security, {ca.deliv} "
                    f"({ca.description[:90]!r}) — not booked: enter "
                    f"the new shares and their cost by hand in a "
                    f".tt file (BUYSELL; a US filer also moves "
                    f"part of {_sd_tk}'s basis, §307).")
            if ca.qty > 0:
                _sym = f"{_sd_tk}.{ca.listing_ext(_sd_tk)}"
                _stx = {
                    'action': 'BUYSELL', 'date': ca.date, 'time': ca.time,
                    'date_settle': ca.date, 'symbol': _sym,
                    'quantity': ca.qty, 'currency': ca.currency,
                    'price': 0.0, 'fee': 0.0, 'net_amount': 0.0,
                    'gross_amount': 0.0, 'multiplier': 1.0,
                    'account': 'IB', 'description': ca.description,
                    'type': STOCK_DIVIDEND,
                }
                st.transactions.append(_stx)
                _msg = (f"{_sym}: stock dividend of {ca.qty:g} "
                        f"share(s) on {ca.date} (IB Value {M.shown(ca.val, 2):,.2f} "
                        f"{ca.currency}) booked as a stock-dividend "
                        f"event — the gains run applies your "
                        f"country's rule to its cost (`tjs "
                        f"tax-logic`).")
                st.stock_dividends.append(_msg)
                ca.eff.update(kind='stockdiv', txs=[_stx], msg=_msg)
                return self._ib_ca_settle(ca.eff, Outcome.consumed())
            return self._ib_ca_unbooked(
                ca, f"{shown_name(st.path)} {ca.date}: stock dividend row "
                f"with quantity {ca.qty:g} "
                f"({ca.description[:90]!r}) — not booked.")
        return None

    # The row loop's dispatch: a section's Data rows go to the method named
    # here, looked up on the parser (so an override or a test's patch is the
    # one called) with (rs, row, header_map, lineno, section) (TJ-2454,
    # TJ-2457).
    _ROW_METHODS = {
        'Trades': '_ib_row_trades',
        'Options Expirations': '_ib_row_trades',
        'Dividends': '_ib_row_dividends',
        'Open Positions': '_ib_row_open_positions',
        'Statement': '_ib_row_statement',
        'Change in Dividend Accruals': '_ib_row_accruals',
        'Withholding Tax': '_ib_row_withholding',
        'Interest': '_ib_row_interest',
        'Fees': '_ib_row_fees',
        'Transaction Fees': '_ib_row_transaction_fees',
        'Commission Adjustments': '_ib_row_commission_adjustments',
        'Corporate Actions': '_ib_row_corporate_actions',
        'Transfers': '_ib_row_transfers',
    }

    def read(self, path: Path) -> List["_IbRow"]:
        """The statement's rows (the template's read; 5h (8d)). The parse state first:
        this statement's (`_IbParse` on `self._ib`, kept after the parse for the
        account's cross-statement pass: ca_undo, cil_join, the deferred side-effect
        lines; the methods read it as `st = self._ib`) and the rows' (`_IbRows` on
        `self._ib_rows`, read as `rs`, released with the parse); then the rows the
        cash events are read from noted (`_ib_cash_rows`), the prescan, IB's
        temporary symbols folded, the period, the account's aliases and notes.
        One `_IbRow` per Data row and per row of a type IB does not write, with the
        header map in force (its section's latest Header row); Header rows and IB's
        structure rows are read's own, never counted (tests/test_ib_state.py)."""
        st = self._ib = _IbParse(path)
        self._placeholder_warned: set = set()
        self._sign_warned = False
        self._tag_warned: set = set()

        rows = self._read_rows(path)
        # The rows the statement's cash events are read from, as the file
        # holds them (before the temporary symbols are folded below), for
        # the finish (`_ib_finish_cash`; PLAN step 16 commit 10).
        for _cash_row in _ib_cash_rows(rows):
            self.note_cash(_cash_row)
        pre = _ib_prescan(rows, shown_name(path))
        ctx = self.account_context
        # IB's temporary symbols (a time stamp before the ticker, listed
        # under the ticker's contract id): the ticker's rows — the
        # account's statements decide (prepare_files), a lone statement
        # its own instrument list.
        _map_kept: Dict[str, str] = {}
        _folds = (ctx.get('stock_folds', {}) if ctx
                  else _ib_temp_folds(pre['stock_conid_syms'],
                                      _project_map_names([path]),
                                      _map_kept))
        if _folds:
            _folded = _ib_fold_rows(rows, _folds)
            if _folded != rows:
                rows = _folded
                pre = _ib_prescan(rows, shown_name(path))
        if not ctx and (_folds or _map_kept):
            _note_temp_folds(_folds, shown_name(path), _map_kept)
        # The statement's first day. A row dated before it (a Trades,
        # Transfers or Corporate Actions row) is IB's cancel-and-rebook
        # of a row of an EARLIER statement: under the account context
        # its `Ca` looks for that statement's original first
        # (reconcile_files) and pairs with the rebook in this statement
        # only when no statement holds one (audit A2-0886 / A2-1559 /
        # A2-1560). A lone parse pairs in this statement, as before.
        for _r in rows:
            if (len(_r) >= 4 and _r[0] == 'Statement' and _r[1] == 'Data'
                    and _r[2] == 'Period'):
                _span = _ib_period(_r[3])
                if _span:
                    st.period_start = _span[0].isoformat()
                break

        if ctx:
            # Option-root aliases from ALL of the account's statements
            # (audit S059-15: next year's instrument list may name only
            # the adjusted root).
            occ = {c: set(v) for c, v in pre['occ_by_conid'].items()}
            for c, v in ctx.get('occ_by_conid', {}).items():
                occ.setdefault(c, set()).update(v)
            und = {c: set(v) for c, v in
                   ctx.get('opt_underlying', {}).items()}
            for c, v in pre['opt_underlying'].items():
                und.setdefault(c, set()).update(v)
            pre['root_alias'], pre['alias_conids'] = _root_aliases(occ, und)
            for key, by_root in ctx.get('contract_conids', {}).items():
                tgt = pre['contract_conids'].setdefault(key, {})
                for root, ids in by_root.items():
                    tgt.setdefault(root, set()).update(ids)
        else:
            _warn_stock_aliases(pre['stock_conid_syms'], shown_name(path),
                                seen=_conid_seen([(shown_name(path), pre)],
                                                 pre['stock_conid_syms']),
                                listing=pre['stock_listing'],
                                folds={**_folds, **_map_kept},
                                links=pre['ca_links'])
        self._ib_pre = pre
        self._check_statement_kind(pre, path)
        # Neutral payer fact for income rows (characterize/income_dating decides
        # what it means): the IB entity named in the statement header.
        _bn = (pre.get('broker_name') or '').lower()
        dealer_country = ('CA' if 'canada' in _bn
                          else 'US' if _bn.rstrip('. ').endswith('llc')
                          else '')
        if len(pre['accounts']) > 1 and self.combined_broker_accounts:
            # The label declares every broker account in its statements
            # is the user's and taxable together (owner decision): a
            # NOTE. IB's statement names no plan per account, so on a
            # sheltered label that cannot be checked — refused.
            _masked = sorted(_mask_account(a) for a in pre['accounts'])
            if self.account_taxable is False:
                raise combined_accounts_refusal(
                    shown_name(path), 'IB', _masked,
                    "an IB statement does not say which plan each "
                    "account is")
            emit_line(combined_accounts_note(shown_name(path), 'IB', _masked))
        elif len(pre['accounts']) > 1:
            # ATTENTION (the run's console): a TFSA/RRSP inside a
            # consolidated statement lands in this book (audit A2-0610).
            emit_line(f"{ATTENTION_PREFIX} {shown_name(path)}: IB statement spans "
                  f"{len(pre['accounts'])} accounts ("
                  f"{', '.join(sorted(_mask_account(a) for a in pre['accounts']))}"
                  f") — every row is booked to ONE account label. That "
                  f"is right only when they are one tax entity (e.g. two "
                  f"taxable margin accounts); export a registered "
                  f"account (TFSA/RRSP) separately.")

        # The rows' shared state (`_IbRows`): what the section methods fill
        # and the finish reads, each by one name (`rs.<field>`; TJ-2449).
        rs = _IbRows(pre=pre, dealer_country=dealer_country)
        self._ib_rows = rs
        out: List[_IbRow] = []
        header_lens: Dict[str, int] = {}
        for lineno, row in enumerate(rows, 1):
            if not row:
                continue
            section = row[0]
            type_ = row[1] if len(row) > 1 else ''
            if type_ == 'Header':
                rs.header_maps[section] = {col: i for i, col in enumerate(row)}
                header_lens[section] = len(row)
            elif type_ == 'Data' or type_ not in _IB_STRUCTURE_ROW_TYPES:
                out.append(_IbRow(row, lineno, section, type_, rs.header_maps.get(section),
                                  header_lens.get(section, 0)))
        return out

    def new_transactions(self) -> List[Dict[str, Any]]:
        """The statement's own list (`_IbParse.transactions`; the template's hook): a
        `Ca` row undoes rows booked before it, and the account's pass after the parse
        (`ca_undo`) works on the list the parse returned."""
        return self._ib.transactions

    def classify(self, r: "_IbRow") -> Outcome:
        """What the template does with a row (5h (8d)): one of a type IB does not write
        ('data' after a spreadsheet re-save, say) is skipped and said at the end, never
        unseen (audit S060-07); a Data row with no Header row skipped; a row cut short
        refused; a section's row built by its method (`_ROW_METHODS`); a metadata
        section's (Account Information, Net Asset Value, Cash Report, Mark-to-Market,
        Financial Instrument Information, ...) a non-event; an unknown section's
        refused when it carries money, else skipped and said once."""
        rs, path = self._ib_rows, self._ib.path
        section, lineno, row, header_map = r.section, r.line, r.cells, r.header_map
        if r.kind != 'Data':
            rs.unknown_row_types[r.kind] = rs.unknown_row_types.get(r.kind, 0) + 1
            return Outcome.skip(f"row of unknown type {r.kind!r}")
        if not header_map:
            return Outcome.skip(f"section {section}: Data row with "
                                f"no Header row")
        if section in _IB_FULL_WIDTH_SECTIONS and len(row) < r.header_len:
            # A row cut short (a truncated or hand-edited export)
            # read its missing cells as blank: a Trades row without
            # its Code booked an assignment leg as a plain trade,
            # silently (audit A2-1042 — the Webull/RBC/Kraken
            # refusal). IB writes every money row at full width.
            raise BrokerageParseError(
                f"{shown_name(path)} line {lineno}: {section} row has "
                f"{len(row)} cells but its header has "
                f"{r.header_len} — the row is cut short; "
                f"refusing to read its missing cells as blank. "
                f"Re-download the statement.")
        if section in self._ROW_METHODS:
            return Outcome.build()
        if section in _IB_METADATA_SECTIONS:
            return Outcome.nonevent(f"section {section} (not "
                                    f"translated)")
        # A section with no branch and not on the metadata
        # allowlist: a renamed/localized money section
        # ("Dividendes", "Transactions") or one IB added.
        # With money-like columns it is an ERROR (its cash
        # would silently vanish); otherwise a loud skip.
        _money = sorted(_IB_MONEY_COLUMNS & set(header_map))
        if _money:
            raise BrokerageParseError(
                f"{shown_name(path)} line {lineno}: unknown IB section "
                f"{section!r} carries money columns "
                f"({', '.join(_money)}) and has no parser branch "
                f"— refusing to drop its rows. Re-export the "
                f"statement in English, or report the section.")
        if section not in rs.unknown_sections:
            rs.unknown_sections.add(section)
            emit_line(f"warning: {shown_name(path)}: unknown IB section "
                  f"{section!r} (no parser branch, not known "
                  f"metadata) — its rows are skipped; check "
                  f"they carry no trades or income.")
        return Outcome.skip(f"section {section} (unknown)")

    def build(self, r: "_IbRow") -> Outcome:
        """The row's section method (`_ROW_METHODS`): it books into the statement's
        list (`new_transactions`) and answers what the row is (a parsers.protocol
        Outcome, the template's to count)."""
        return getattr(self, self._ROW_METHODS[r.section])(
            self._ib_rows, r.cells, r.header_map, r.line, r.section)

    @builds("transfer")
    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """After the rows (the template's finish): rows of a type IB does not write
        said, or the statement refused when it has no others; then `_ib_finish`. The
        settlement clamp on the expiries the rows kept (`_expiries`), the fill marks
        and the skip summary are the template's."""
        rs = self._ib_rows
        if rs.unknown_row_types:
            _kinds = ', '.join(f"{k!r} x{n}"
                               for k, n in sorted(rs.unknown_row_types.items()))
            if self._rows_seen == sum(rs.unknown_row_types.values()):
                raise BrokerageParseError(
                    f"{shown_name(path)}: the statement has Header rows but no "
                    f"'Data' rows (row types {_kinds}) — not IB's CSV "
                    f"layout; refusing to read it as an empty statement. "
                    f"Re-download the Activity Statement CSV.")
            emit_line(f"warning: {shown_name(path)}: rows of a type IB does not "
                  f"write were skipped ({_kinds}) — check they carry no "
                  f"trades or income.")

        # The end of the parse (`_ib_finish`): what the rows left, paired,
        # checked and said.
        self._ib_finish(rs)
        return transactions

    def release(self) -> None:
        """The rows' shared state goes with the parse (`_ib_rows`: the rows'
        tallies, the header maps, the cash booked per Cash Report line, TJ-2435);
        the statement's state (`_ib`) stays for the account's pass, and the
        prescan (`_ib_pre`) for `statement_accounts` after it (TJ-2485)."""
        self._ib_rows = None

    def emit_skip_summary(self, source_name: str) -> None:
        """The template's skip summary; under an account context it waits for the
        account's cross-statement pass, whose `finally` prints it
        (`_ib_flush_skip_summary`): a `Ca` row another statement pairs is no skip
        (`resolve_unmatched_ca`). One statement per parser, as taxjson-brokerage
        makes them (the counts are the parser's, as the base's are; TJ-2484)."""
        if self.defer_ca_messages:
            self.deferred_skip_summary = source_name
            return
        super().emit_skip_summary(source_name)

    def _ib_flush_skip_summary(self) -> None:
        """The skip summary `emit_skip_summary` held for the account's pass."""
        name, self.deferred_skip_summary = self.deferred_skip_summary, None
        if name is not None:
            super().emit_skip_summary(name)

    def _ib_finish(self, rs: "_IbRows") -> None:
        """The end of one statement's parse, after its rows (the template's finish; 5h (8c)): the
        cancellations, refunds and cash in lieu no row of the statement claimed, kept
        for the account's other statements; the Cash Report's check; the tenders, the
        accruals and the income's listing; the assignment and warrant legs paired; the
        notes; the statement's cash events (`_ib_finish_cash`). Its parts run in
        this order: a later one reads what an earlier one left."""
        st = self._ib

        self._ib_finish_left_cancels(rs)

        self._ib_finish_trade_cancels(rs)

        self._ib_finish_refunds(rs)

        self._ib_finish_unmatched_ca()

        # Transaction Fees are inside Comm/Fee while the Cash Report
        # books them on their own line, so the commission identity is
        #   sum(Comm/Fee) + Commission Adjustments
        #     == Commissions + Transaction Fees.
        self._reconcile_cash_report(rs.pre, rs.cash_booked, st.path)

        self._ib_finish_unmatched_cil()

        self._ib_finish_tenders(rs)

        self._ib_finish_dividend_facts(rs)

        if self.defer_ca_messages:
            # taxjson-brokerage runs reconcile_files, whose Ca pass can
            # undo an event of this statement from another one (re-audit
            # A2-1091): it prints the lines after that pass.
            self.ca_side_effects = partial(_flush_ca_side_effects, st)
        else:
            _flush_ca_side_effects(st)

        if rs.skipped_dateless_fees:
            total = sum(rs.skipped_dateless_fees)
            emit_line(f"warning: skipped {len(rs.skipped_dateless_fees)} IB Fees row(s) "
                f"with no date and no 'for Mmm YYYY' hint (total: {M.shown(total, 2):.2f}). "
                f"Edit the source CSV to add dates if these should appear in "
                f"FEE totals.")

        self._ib_finish_open_accruals(rs)

        self._ib_finish_income(rs)

        self._ib_finish_assignments(rs)

        # A warrant exercise (warrant leg `C;Ex` at 0, shares `Ex;O` at
        # the exercise price) is not a disposition: the warrant leg names
        # the shares it delivers (`exercise_of`) and the engines roll its
        # cost into them like a long call's premium (CA s.49(3); US basis
        # carryover — tax-logic CA-OPT-09 / US-OPT-06, A2-0090). A leg
        # whose shares cannot be told apart stays a disposal at 0, said.
        self._pair_warrant_exercises(rs.warrant_ex_legs, rs.share_ex_legs)

        self._ib_finish_cash()

    def _ib_finish_cash(self) -> None:
        """The statement's cash events (`cash_events`, which taxjson-brokerage
        --cash-out writes) from the rows read noted (`_ib_cash_rows`,
        `note_cash`), in their order (`_ib_cash_events_of`): a cell they
        cannot read leaves the file none and its `cash_problem`
        (`_cash_events_from`), never a refused parse (PLAN step 16 commit
        10; lib/cash_readers/ib.py read the export again until then)."""
        self._cash_events_from(partial(_ib_cash_events_of, list(self._cash_rows),
                                       shown_name(self._ib.path)))

    def _ib_other_statement(self):
        """A test for a fact the account context carries (its file, its
        accounts): from another statement of this account, not this file;
        either side's accounts unknown counts as this account (TJ-2477)."""
        mine, name = self.statement_accounts(), self._ib.path.name
        return lambda file, accounts: file != name and _ib_same_account(mine, accounts)

    def _ib_finish_left_cancels(self, rs: "_IbRows") -> None:
        """The Transfers and Corporate Actions `Ca` rows no original of this statement
        claimed in its rows (`_ib_finish`): a transfer's becomes a reversing TRANSFER leg
        (held for the account's cross-statement pass when a row here matches it, said
        otherwise); a corporate action's is paired with the latest earlier event of this
        statement it undoes (`ca_undo`)."""
        st = self._ib
        # Transfers `Ca` rows no original in this statement claimed: the
        # original is in an earlier statement — kept as a reversing leg.
        for _c in rs.pending_xfer_ca:
            _leg = {
                'action': 'TRANSFER', 'date': _c['date'],
                'time': '09:30:00', 'date_settle': _c['date'],
                'symbol': _c['symbol'], 'quantity': _c['qty'],
                'currency': _c['currency'], 'price': _c['price'],
                'net_amount': _c['net'], 'account': 'IB',
                'description': f"{_c['desc']} (Ca)",
            }
            if any(_ib_xfer_cancels(t, _leg) for t in st.transactions):
                # Only a rebook dated before this statement's period is
                # here: the original is looked for in the account's
                # other statements first (reconcile_files).
                st.held_xfer_cas.append(_leg)
            else:
                emit_line(f"note: {_c['symbol']}: IB cancelled a "
                      f"{_c['kind']} transfer of {-_c['qty']:g} on "
                      f"{_c['date']} whose original row is not in this "
                      f"statement — kept as a reversing TRANSFER leg.")
            st.transactions.append(_leg)

        # Corporate Actions `Ca` rows with no same-date original: the
        # latest earlier original in this statement, if any.
        for _ca in list(st.pending_ca):
            _orig = self.ca_undo(_ca)
            if _orig:
                st.pending_ca.remove(_ca)
                st.ca_pairs.append((_ca, _orig['date']))

    def _ib_finish_trade_cancels(self, rs: "_IbRows") -> None:
        """The Trades `Ca` rows (`_ib_finish`): each paired with the original it
        reverses and both dropped (lib/trade_cancel), the pairs and the keys kept for
        the account's overlapping statements; a Ca dated before the period held for its
        earlier statements."""
        st = self._ib
        # Trades `Ca` rows (keys of the originals they reverse) and the
        # pairs dropped here — offered to the account's overlapping
        # statements (reconcile_files, audit A2-0259 / A2-0088).
        # (`_ib_row_acct`: the statement's only account names the rows of
        # a statement with no Account column, backlog TJ-1244.)
        st.trade_ca_keys.update((_ib_trade_key(c, negate=True),
                                 _ib_row_acct(self, c))
                                for c in rs.trade_cancels)
        # A Ca dated before the statement's period (its candidates are
        # too: a Ca shares its original's date) waits for
        # reconcile_files, which looks in the account's earlier
        # statements first (audit A2-0886).
        st.held_trade_cas.extend(c for c in rs.trade_cancels
                                 if self._ib_before_period(c.get('date')))
        _held = {id(c) for c in st.held_trade_cas}
        if rs.trade_cancels and len(_held) < len(rs.trade_cancels):
            _partials: list = []
            _overlaps: list = []
            # A combined statement's rows name their broker account:
            # A's cancellation never removes B's same fill (backlog
            # TJ-0982).
            _kept, _pairs, _unpaired = pair_cancellations(
                [t for t in st.transactions if id(t) not in _held],
                partials=_partials, overlaps=_overlaps,
                acct_of=lambda t: _ib_trade_acct(self, t))
            _kept += [t for t in st.transactions if id(t) in _held]
            # `_acct`: the row's broker Account (the copies lose its
            # trade_meta entry); never written out.
            st.trade_pairs.extend((dict(_o, _acct=_ib_row_acct(self, _o)),
                                   dict(_c)) for _o, _c in _pairs)
            # A Ca of one execution of a multi-fill order (A2-0298).
            for _o, _c, _r in _partials:
                emit_line(f"note: {shown_name(st.path)}: IB cancelled (Ca) "
                      f"{-_c['quantity']:g} of the {_o['symbol']} order of "
                      f"{_o['quantity']:g} @ {_o['price']:g} on "
                      f"{_o['date']} (one execution) — the order is "
                      f"booked as {_r['quantity']:g}.")
            _gone = {id(t) for pr in _pairs for t in pr}
            st.transactions[:] = _kept
            self._expiries[:] = [t for t in self._expiries if id(t) not in _gone]
            # A Ca of the whole order after one of its executions'.
            _whole = {id(_c) for _o, _c, _r in _overlaps}
            for _o, _c, _r in _overlaps:
                emit_line(f"note: {shown_name(st.path)}: IB then cancelled "
                      f"(Ca) the whole {_o['symbol']} order of "
                      f"{_o['quantity']:g} @ {_o['price']:g} on "
                      f"{_o['date']}, which covers the execution cancelled "
                      f"before — the order is removed in full (not "
                      f"{_r['quantity']:g} left, and not cancelled twice).")
            for _o, _c in _pairs:
                if id(_c) in _whole:
                    continue
                _kf = retained_fee(_o, _c)
                emit_line(f"note: {shown_name(st.path)}: IB cancelled (Ca) "
                      f"{trade_cancel_what(_o['quantity'], _c['quantity'])} "
                      f"{_o['symbol']} trade of {_o['quantity']:g} @ "
                      f"{_o['price']:g} on {_o['date']} — the trade and "
                      f"its cancellation are both dropped"
                      + (f"; the commission IB kept "
                         f"({_kf['net_amount']:g} {_kf['currency']}) "
                         f"stays, a FEE row." if _kf else "."))
            for _c in _unpaired:
                emit_line(f"note: {shown_name(st.path)}: IB cancelled (Ca) a "
                      f"{_c['symbol']} trade of {-_c['quantity']:g} @ "
                      f"{_c['price']:g} on {_c['date']} whose original "
                      f"row is not in this statement — kept as a "
                      f"cancellation leg; taxjson-merge2 (tjs run) "
                      f"drops it with the original from the account's "
                      f"other statement.")

    def _ib_finish_refunds(self, rs: "_IbRows") -> None:
        """The commission refunds and corrections (`_ib_finish`): each folded into the
        one trade of this statement it names, or kept as its FEE row and said; the
        folds, the refunds seen and the unmatched ones kept for the account's other
        statements."""
        ctx = self.account_context
        st = self._ib
        # A commission refund (or correction) naming its trade changes
        # what that trade cost: the outlay is the commission net of the
        # refund (tax-logic CA-ACB-COMMREFUND / US-BASIS-COMMREFUND). It
        # was a stand-alone FEE row the gains never saw — a refunded
        # purchase commission stayed in the ACB (audit R1-58). Folded
        # into the ONE matching trade of this statement; otherwise kept
        # as the FEE row, and said.
        # A refund whose trade is in ANOTHER statement of the account
        # (a December trade refunded in January) is folded there by
        # reconcile_files (audit A2-0605 / A2-1032 / A2-1036); every
        # fold made here is repeated on an overlapping statement's copy
        # of the trade that lacks the refund row, so dedup still sees one
        # trade (A2-0258 / A2-0088).
        _folded = 0
        # Two rows of one statement alike in every field are two refunds:
        # the row's place among them joins its key, so a copy in another
        # statement is the same refund and neither is lost when the
        # account's statements are reconciled (backlog TJ-1246).
        _adj_nth: Dict[tuple, int] = {}
        for _adj in rs.comm_adjustments:
            _k = (_adj['key'], _adj['account'])
            _adj_nth[_k] = _adj_nth.get(_k, 0) + 1
            _adj['key'] = _adj['key'] + (_adj_nth[_k],)
        st.refund_seen.update((_adj['key'], _adj['account'])
                              for _adj in rs.comm_adjustments)
        for _adj in rs.comm_adjustments:
            _hits = _ib_refund_hits(
                _adj, st.transactions,
                acct_of=lambda t: _ib_trade_acct(self, t))
            if len(_hits) != 1:
                if not _hits and ctx is not None:
                    # The account's other statements are searched once
                    # every one is parsed; the note is said there.
                    st.unmatched_refunds.append(_adj)
                    continue
                emit_line(f"note: {shown_name(st.path)}: commission adjustment of "
                      f"{M.shown(_adj['amount'], 2):+.2f} {_adj['currency']} on "
                      f"{_adj['date']} ({_adj['ticker']} {_adj['qty']:g} on "
                      f"{_adj['trade_date']}) matches "
                      f"{'no' if not _hits else len(_hits)} trade(s) in "
                      f"this statement — kept as a FEE row, outside the "
                      f"trade's cost; adjust the trade by hand if it is "
                      f"in a taxable account.")
                continue
            _t = _hits[0]
            st.refund_folds.append({
                'key': _ib_trade_key(_t), 'fee': float(_t.get('fee') or 0.0),
                'fee0': _ib_trade_meta(self, _t)['fee0'],
                'acct': _ib_row_acct(self, _t),
                'amount': _adj['amount'], 'refund': _adj['key'],
                'refund_acct': _adj['account']})
            # cash: a refund is +
            _ib_fold_refund_into(self, _t, _adj['amount'], _adj['key'])
            st.transactions[:] = [t for t in st.transactions
                               if t is not _adj['fee_tx']]
            _folded += 1
        if _folded:
            emit_line(f"note: {shown_name(st.path)}: {_folded} commission "
                  f"adjustment(s) folded into the trade(s) they name "
                  f"(a refund lowers a purchase's cost / raises a sale's "
                  f"proceeds).")

    def _ib_finish_unmatched_ca(self) -> None:
        """The Corporate Actions `Ca` rows whose original never appeared
        (`_ib_finish`): kept for the account's other statements
        (`st.unmatched_ca`), a loud skip each."""
        st = self._ib
        # A cancellation of something booked in an EARLIER statement (or a
        # restatement this parser cannot pair). Nothing is guessed — loud
        # skip. Kept for taxjson-brokerage, which offers each unmatched Ca
        # row to the account's OTHER statements (a split booked in the 2025
        # statement and cancelled in the 2026 one — audit S059-04). Under an
        # account context it also prints the warning for the rows no
        # statement matched; a lone parse warns here.
        st.unmatched_ca.extend(st.pending_ca)
        for _ca in st.pending_ca:
            if self.account_context is None:
                emit_line(unmatched_ca_warning(_ca))
            # Consumed at its row (waiting in the state): its skip now.
            self.recount(Outcome.skip(_IB_UNMATCHED_CA_SKIP))

    def _ib_finish_unmatched_cil(self) -> None:
        """The cash in lieu no split of this statement claimed (`_ib_finish`):
        kept for a split in the account's other statements
        (`st.unmatched_cil`); a lone parse says each."""
        st = self._ib
        st.unmatched_cil.extend(
            {'symbol': _c['symbol'], 'date': _c['date'], 'frac': _c['frac'],
             'where': shown_name(st.path)} for _c in st.pending_cil)
        if self.account_context is None:
            for _c in st.unmatched_cil:
                emit_line(_ib_cil_unmatched_note(_c))

    def _ib_finish_tenders(self, rs: "_IbRows") -> None:
        """The tender offers' legs gathered by the rows (`_ib_finish`,
        `rs.tender_legs`): a note per tender for the shares still parked on its `.TEN`
        line and the cash dispositions booked."""
        ctx = self.account_context
        for _tsym, _tl in sorted(rs.tender_legs.items()):
            if _tl['rows'] <= 0:
                continue                # every row was cancelled (Ca)
            _dates = ', '.join(sorted(set(_tl['dates'])))
            if _tl['cash_qty'] > 0:
                # taxjson-corp-actions skips tender rows, so no election
                # can replace this sale (audit S059-05): the advice is a
                # hand booking.
                emit_line(f"NOTE: {_tsym}: tender/voluntary offer settled for "
                    f"cash — {_tl['cash_qty']:g} share(s) disposed for "
                    f"{M.shown(_tl['cash'], 2):.2f} {_tl['currency']} ({_dates}); "
                    f"booked as a sale. If the offer also delivered new "
                    f"shares (a share-for-share or mixed exchange), this "
                    f"sale is wrong: remove the tender rows from the export "
                    f"and book the exchange by hand in a .tt file.")
            _acct_parked = ((ctx or {}).get('tender_parked') or {}).get(
                _tsym)
            if (abs(_tl['parked']) > 1e-9 and _acct_parked is not None
                    and abs(_acct_parked) < 1e-9):
                # Parked in one statement, resolved in another of the
                # account's (audit S059-06): no outcome is missing.
                emit_line(f"note: {_tsym}: {_tl['rows']} tender/voluntary-offer "
                    f"journal row(s) ({_dates}) moved {_tl['parked']:g} "
                    f"share(s) to or from the tender placeholder; the "
                    f"account's other statement completes the round trip "
                    f"— recognized no-op, nothing booked.")
            elif abs(_tl['parked']) > 1e-9:
                emit_line(f"warning: {_tsym}: {_tl['parked']:g} share(s) still "
                    f"sit on the tender placeholder line at period end "
                    f"({_dates}) — the offer's outcome (shares returned "
                    f"or cash paid) is in a later statement; nothing was "
                    f"booked for them here.")
            elif _tl['cash_qty'] == 0 and not _tl.get('foreign'):
                emit_line(f"note: {_tsym}: {_tl['rows']} tender/voluntary-offer "
                    f"journal row(s) ({_dates}) moved shares to and from "
                    f"the tender placeholder with no cash — recognized "
                    f"no-op, nothing booked.")

    def _ib_finish_dividend_facts(self, rs: "_IbRows") -> None:
        """The dividends' share counts and rates (`_ib_finish`): the accrual facts of
        this statement and the account's other statements fill a dividend row that had
        no per-share rate (a payment in lieu), and give each the ex date an accrual names."""
        ctx = self.account_context
        other = self._ib_other_statement()
        st = self._ib
        # The accrual facts of the account's OTHER statements (same IB
        # account): a dividend whose Po/Re rows sit in the previous
        # statement kept no ex date or share count (audit A2-0601).
        _ext_metas = [m for m in ((ctx or {}).get('accrual_facts') or ())
                      if other(m.get('file'), m.get('accounts'))]
        _accruals = _ib_accrual_index(rs.accrual_meta, _ext_metas)
        _metas, _paid, _apart = _ib_rows_paid(st.transactions, _accruals,
                                              shown_name(st.path))
        _shares: Dict[int, Any] = {}

        # Back-fill quantity/price for dividend rows that had no per-share rate
        # in their own description — chiefly Payment-in-Lieu rows, which IB
        # ships as just "…Payment in Lieu of Dividend" with no rate or share
        # count. The rate lives in the Change in Dividend Accruals section
        # (same dividend, keyed by symbol + pay date), so shares = amount /
        # rate — reconciling a PIL exactly like a normal dividend. Only qty and
        # price are set; net_amount (the income) is untouched.
        for tx in st.transactions:
            if tx.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU'):
                continue
            ticker = (tx.get('symbol') or '').rsplit('.', 1)[0]
            meta = _metas[id(tx)]
            # The ex date of the accrual this posting pays — matched the
            # way the share count is (a posting a day after the accrued
            # pay date kept no ex date, so the US §852(b)(7) advisory
            # never fired: audit A2-0602).
            _ex = (meta or {}).get('ex_date') or rs.accrual_ex.get(
                (ticker, tx.get('date')))
            # (a row no evidence gives one of a pay date's ex dates:
            # none, said by `_ib_tied`)
            if _ex and _ex <= (tx.get('date') or '') and not tx.get('_ex_open'):
                tx['ex_date'] = _ex
            if tx.get('price'):          # description already gave a rate
                continue
            # SIGNED amount: a reversal row back-computes a NEGATIVE share
            # count, matching _parse_div_qty_rate's convention (qty carries
            # the row's sign; the positive per-share rate stays positive).
            amt = float(tx.get('net_amount') or 0.0)
            if meta is None or not amt:
                continue
            rate = meta.get('po_rate') or 0.0
            _own = meta['currency'] == tx.get('currency') and rate > 0
            if _own and id(meta) not in _shares:
                _shares[id(meta)] = _ib_distribution_shares(
                    _paid[id(meta)], meta, _accruals, shown_name(st.path), _apart)
            if _own and _shares[id(meta)]:
                # A distribution paid as rows of different character (a
                # return of capital beside an ordinary dividend): one
                # share count a side, each row's own rate (TJ-2598).
                _ib_split_row_shares(tx, _paid[id(meta)], *_shares[id(meta)],
                                     meta, shown_name(st.path))
            elif _own:
                _ib_lone_shares(tx, meta, _paid[id(meta)], shown_name(st.path))
            elif meta.get('po_qty') and not any(
                    o is not tx and o.get('action') == 'DIVIDEND'
                    # A foreign issuer's return of capital beside an
                    # ordinary dividend does not keep the dividend's
                    # share count from it (it lowered the cost before
                    # the characterize step; TJ-2594); beside another
                    # return of capital it counts, as it did.
                    and (tx.get(ISSUER_ROC_KEY) or not o.get(ISSUER_ROC_KEY))
                    and o.get('symbol') == tx.get('symbol')
                    and o.get('date') == tx.get('date')
                    for o in st.transactions):
                # The accrual is in another currency than the cash (IB
                # accrues a CAD-paid dividend in USD): its rate does not
                # apply, but its share count does when this row is the
                # whole payment (no ordinary dividend beside it).
                q = meta['po_qty'] if amt > 0 else -meta['po_qty']
                tx['quantity'] = q
                tx['price'] = M.units(abs(amt) / meta['po_qty'], 8)
        for tx in st.transactions:
            # (`_line` stays for `_ib_finish_income`, which pops it)
            tx.pop('_acct', None)
            tx.pop('_rated', None)
            tx.pop('_ex_open', None)

    def _ib_finish_open_accruals(self, rs: "_IbRows") -> None:
        """The accruals no posted dividend paid (`_ib_finish`): each posted dividend of
        this statement or the account's others pays one accrual; an accrual payable
        within the period and left unpaid is said."""
        ctx = self.account_context
        other = self._ib_other_statement()
        st = self._ib
        # Open accruals: a positive net (Po not yet reversed) whose cash
        # dividend isn't already posted in this file's Dividends section,
        # and whose pay date falls on or before the statement period end
        # (a future-dated accrual is a normal pending dividend, not a
        # missed one). When the period end is unknown, don't filter on it.
        # Each posted dividend pays ONE accrual: exact pay-date matches
        # first (every accrual, reversed or not), then a posting within a
        # week for the accruals left — so another week's dividend of a
        # weekly payer no longer hides an unpaid one (audit S059-08).
        # Postings in the account's OTHER statements count too: a
        # December accrual is often paid in the next year's statement
        # (R1-327).
        postings = list(rs.posted_dividend_keys) + [
            (_s, _d) for _n, _s, _d, _a in
            ((ctx or {}).get('posted_dividends') or ())
            if other(_n, _a)]
        used = [False] * len(postings)

        paid = set()
        for key, meta in rs.accrual_meta.items():
            if _ib_claim(meta, 0, postings, used):
                paid.add(key)
        for want_open in (False, True):
            for key, meta in rs.accrual_meta.items():
                if key in paid or (rs.accrual_net[key] > 0.01) != want_open:
                    continue
                if _ib_claim(meta, 7, postings, used):
                    paid.add(key)

        open_accruals = []
        for key, net in rs.accrual_net.items():
            if net <= 0.01 or key in paid:
                continue
            meta = rs.accrual_meta[key]
            if (rs.statement_period_end and meta['pay_date']
                    and meta['pay_date'] > rs.statement_period_end):
                continue
            open_accruals.append(
                (meta['symbol'], meta['pay_date'], net, meta['currency']))
        if open_accruals:
            open_accruals.sort()
            details = '; '.join(
                f"{sym} pay:{pay or '?'} ~{M.shown(amt, 2):.2f} {cur}".rstrip()
                for sym, pay, amt, cur in open_accruals
            )
            # ATTENTION: income the books may be missing (the run's
            # console, not only .sum/.diag — audit A2-0264).
            emit_line(f"{ATTENTION_PREFIX} {len(open_accruals)} dividend(s) in {shown_name(st.path)} are "
                f"accrued but not yet booked as posted dividends ({details}). "
                f"IB books the cash with a lag — accruals are estimates and "
                f"are NOT counted as income. If a more recent statement "
                f"doesn't already show these as posted dividends, re-download "
                f"it before filing so the income is captured.")

    def _ib_finish_income(self, rs: "_IbRows") -> None:
        """The dividend and withholding-tax rows bound to the listing held for their
        ticker (`_ib_finish`; `_reattribute_income_to_holdings`): the ambiguous and
        mismatched ones said."""
        ctx = self.account_context
        st = self._ib
        # Bind each dividend/withholding-tax row to the listing actually held
        # for its ticker (fixes ISIN-country vs listing-exchange mismatches on
        # dual-listed names). Runs after the full file is parsed so it sees
        # every position regardless of section order.
        _isin_mismatch: set = set()
        _ambiguous: set = set()
        _undated: list = []
        _reattribute_income_to_holdings(
            st.transactions, extra_held=rs.open_position_syms,
            fallback_held=(ctx or {}).get('held'),
            elsewhere=(None if ctx is None else
                       _ib_held_elsewhere(ctx, Path(st.path).name,
                                          self.statement_accounts())),
            isins_by_root={**(ctx or {}).get('stock_isins', {}),
                           **rs.pre['stock_isins']},
            mismatches=_isin_mismatch,
            open_qty=(rs.open_position_qty if 'Open Positions' in rs.header_maps
                      else None),
            ambiguous=_ambiguous, undated=_undated)
        # A row whose listing no dated row of the statement shows held,
        # the account's exports naming another: what is known is that the
        # account may have held either; the run flags a cost row placed so
        # (pipeline/stages._flag_undated_listings).
        _marked = set()
        for _u in sorted(_undated, key=lambda u: (
                u['tx'].get('_line') or 0, str(u['tx'].get('symbol')))):
            _t = _u['tx']
            _marked.add(id(_t))
            emit_line(undated_listing_note(
                shown_name(st.path), _t.get('_line'),
                'return of capital' if (_t.get('action') == 'ADJUST'
                                        or _t.get(ISSUER_ROC_KEY))
                else 'income', str(_t.get('symbol')), str(_t.get('date')),
                _u['listings'], why=_u['why'], flat=_u['flat']))
        for _sym, _day in sorted(_ambiguous):
            emit_line(f"note: {shown_name(st.path)}: income on {_sym} of {_day} "
                  f"kept its ISIN listing: the account held more than one "
                  f"listing of the ticker that day (add a ticker.map rule "
                  f"if it belongs to the other).")
        # A row still on the listing its ISIN's country alone gave, a
        # domicile and no market: booked on evidence of the listing, else
        # refused (TJ-5016).
        _ib_settle_assumed_listings(
            st.transactions, rs.isin_fallback,
            set(rs.open_position_syms) | set((ctx or {}).get('held') or ()) | {
                t.get('symbol') for t in st.transactions
                if t.get('action') in _POSITION_ACTIONS},
            rs.open_position_syms, rs.pre.get('fii_all'),
            {**(ctx or {}).get('stock_isins', {}), **rs.pre['stock_isins']},
            _project_map_names([st.path]), shown_name(st.path))
        _ib_stamp_security_ids(st.transactions, rs.pre)
        # Each income and cost row as the account's other statements may
        # hold it too (`_ib_cross_income_listings`), and the rows' lines
        # dropped.
        for _t in st.transactions:
            _n = _t.pop('_line', None)
            if (_t.get('action') in _INCOME_ACTIONS
                    or (_t.get('action') == 'ADJUST'
                        and _t.get('type') == 'roc')):
                st.listed_income.append({
                    'tx': _t, 'line': _n, 'marked': id(_t) in _marked,
                    'key': _ib_income_key(_t)})
        for _sym, _isin, _held in sorted(_isin_mismatch):
            emit_line(f"warning: {shown_name(st.path)}: income on {_sym} (ISIN {_isin}) "
                  f"was NOT moved to the held listing {_held}: that "
                  f"listing is a different security (another ISIN) with "
                  f"the same ticker.")

    def _ib_finish_assignments(self, rs: "_IbRows") -> None:
        """An assignment's option leg settled on its stock leg's date (`_ib_finish`), so
        the premium rolls into the delivered shares before another trade of the
        underlying sorts between them."""
        # An assignment's option leg settles with its STOCK leg: the
        # premium rolls into the delivered shares, so the pair must share
        # a settle date or an unrelated same-underlying trade sorting
        # between them consumes the staged premium. Before the T+1
        # cutover the option class settled T+1 and the stock T+2 (audit
        # S058-01). Cash-settled contracts have no stock leg: unchanged.
        _stock_by_key = {}
        for _st in rs.assign_stock_legs:
            _root, _ext = _split_known_ext(_st['symbol'])
            _stock_by_key.setdefault((_root, _ext, _st['date']), _st)

        _stock_flat = {}
        for (_r, _e, _d), _st in _stock_by_key.items():
            _stock_flat.setdefault((_r.replace('.', ''), _e, _d), _st)
        for _ol in rs.assign_option_legs:
            _om = re.match(r'^(.+?)\d{6}[CP]\d{8}\.(\w+)$', _ol['symbol'])
            _st = None
            if _om:
                for _r in _ib_root_forms(_om.group(1)):
                    _st = (_stock_by_key.get((_r, _om.group(2), _ol['date']))
                           or _stock_flat.get((_r.replace('.', ''),
                                               _om.group(2), _ol['date'])))
                    if _st is not None:
                        break
            if _st is not None:
                _ol['date_settle'] = _st['date_settle']

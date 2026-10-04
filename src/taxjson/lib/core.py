import functools
import json
import re
import hashlib
import sys
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime, timedelta

from taxjson.lib.country import check_engine_allowed as _check_engine_allowed
from taxjson.lib.corporate_timeline import (SplitTimeline, event_sort_key,
                                            normalize_symbol_new, split_seen,
                                            SPLIT_DATE_WINDOW_DAYS)
from decimal import Decimal

from taxjson.lib.numeric import D

class SplitStraddlesSettlementError(ValueError):
    """A RENAME-split dated strictly between a trade's execution and
    its settlement: re-denominating the in-flight quantity would also
    need re-symboling mid-trade, so the engine refuses and the user
    re-dates the row's settlement (or the split). PLAIN splits inside
    a settle lag no longer refuse — the executed quantity/price are
    re-denominated through the ratio (money untouched) and booked
    correctly against the post-split pool."""


class AmbiguousTransferDateError(ValueError):
    """A row rewritten from a TRANSFER sits inside a superficial-loss /
    wash-sale trigger window. Its date is the broker ARRIVAL date —
    for a custody move that is not an acquisition date, and using it
    as one could wrongly deny (or wrongly allow) a return-relevant
    loss. The engine refuses to guess; the user declares what the row
    is. (Balance/still-held usage is unaffected: shares held are held
    regardless of how they arrived.)"""


@dataclass
class TaxTransaction:
    """Normalized transaction record. Monetary fields are typed `float`
    on purpose — the parse/IO boundary is float (every brokerage CSV
    arrives as text → float, and JSON has no Decimal). Gains engines
    wrap each value in `D()` before any sum or pool mutation, so the
    precision-sensitive arithmetic happens in Decimal. Don't migrate
    these fields to `Decimal`: it cascades through every parser, every
    test fixture, and the JSON serializer for no observable bug, since
    intermediate float ops have IEEE-754 error well below cent
    precision."""
    action: str
    date: str
    symbol: str = ''
    quantity: float = 0.0
    currency: str = ''
    time: str = '09:30:00'
    date_settle: str = ''
    price: float = 0.0
    proceeds: float = 0.0
    commission: float = 0.0
    fee: float = 0.0
    net_amount: float = 0.0
    account: str = 'PORTFOLIO'
    type: str = ''
    gross_amount: float = 0.0
    description: str = ''
    symbol_new: str = ''
    # Traceability back to the corp-action event/election that emitted
    # this row (empty for ordinary broker rows). NOT part of
    # compute_id — the identifying content is unchanged.
    corp_event_id: str = ''
    corp_election: str = ''
    id: Optional[str] = None
    # Neutral income facts a parser read from the export (empty when the
    # export does not say). NOT part of compute_id, and left out of
    # to_dict() when empty. What they mean for the tax year or the
    # character of the income is decided per country in
    # lib/income_dating — never by the parser.
    #   record_date     the record date the broker prints ("REC 12/30/24")
    #   ex_date         the ex-dividend date (IB's dividend accruals)
    #   income_label    "distribution" when the broker calls the payment
    #                   a distribution ("DIST ON ...", RBC "Distribution")
    #   dealer_country  the country of the dealer that paid the row
    #                   ("CA" for Interactive Brokers Canada Inc.)
    #   issuer_country  the issuer's country from its ISIN ("CA", "US")
    record_date: str = ''
    ex_date: str = ''
    income_label: str = ''
    dealer_country: str = ''
    issuer_country: str = ''
    # The broker's own clock stamp ("2025-12-25 22:07:41 ET") on a trade
    # whose exchange trade date differs from it — an overnight-session US
    # fill, an ASX fill stamped in the US Eastern evening (tax-logic
    # CA-DATE-SESSION / US-DATE-SESSION). Evidence only: NOT part of compute_id,
    # left out of to_dict() when empty.
    broker_time: str = ''
    # The security's name from the broker's instrument list, when the
    # row's description is only the ticker (IB: "META PLATFORMS INC-CDR"
    # beside description "META") — read by the cross-listing lint to tell
    # a CDR or another company from an interlisting (audit S057-24).
    # Evidence only: NOT part of compute_id, omitted when empty.
    security_name: str = ''
    # The broker's own open/close marker on a trade (IB Trades `Code`:
    # "O" opening, "C" closing, "C;O" a sale that closed a long and
    # opened a short in one fill). Evidence for the missing-history
    # checks (find-missing-history, option-boundary): a sale coded "O"
    # is a broker-declared short, a sale coded only "C" closed a
    # position bought before the data. NOT part of compute_id, omitted
    # from to_dict() when empty (audit S013-00, S058-02, S060-12).
    open_close: str = ''
    # The broker's own cost basis of the position a closing trade
    # closed, as "<amount> <currency>" (IB Trades `Basis` on a row coded
    # "C") — shown next to a closing sale that has no position in the
    # data. Evidence only, never booked.
    broker_basis: str = ''
    # Contract size the parser read from the export (IB Financial
    # Instrument Information Multiplier: 100 per equity option, 1000 per
    # CL future or futures option, 0.1 per micro-crypto future) — kept
    # on option and futures rows only; 0 = not declared (audit S026-22).
    # NOT part of compute_id, omitted from to_dict() when 0.
    multiplier: float = 0.0
    # The input file the row was read from ("questrade_2025.csv",
    # "history.tt"), stamped by taxjson-brokerage / convert-tt. Cross-
    # file dedup tells an overlapping re-export (one row, two files)
    # from separate records that happen to look alike (bin/taxjson_sort
    # .plan_dedup, audit R1-296). NOT part of compute_id, omitted when
    # empty.
    source: str = ''
    # When `source` is a MASKED name (an account-number token shown as
    # 55***), a short hash of the real file name keeps two files whose
    # names differ only in that token apart for dedup (sha256, first 10
    # hex — never the name itself; audit A2-0159). Empty otherwise.
    # NOT part of compute_id, omitted when empty.
    source_key: str = ''
    # The broker account the row came from, HASHED (sha256 of the id the
    # export prints, first 10 hex — never the id itself), stamped by
    # taxjson-brokerage from the parser's per-row `broker_account` or
    # the statement's single account. Cross-file dedup never collapses
    # two rows of different broker accounts (audit A2-0008, R1-296).
    # NOT part of compute_id, omitted when empty.
    source_account: str = ''
    # The share listing a WARRANT or RIGHT exercise delivers, set by the
    # parser on the warrant leg (an ASSIGN of the warrant): exercising is
    # not a disposition — the warrant's cost goes into the shares' cost
    # like an option's premium (CA s.49(3) / US basis carryover; owner
    # decision on audit A2-0090 / A2-0274). NOT part of compute_id,
    # omitted from to_dict() when empty.
    exercise_of: str = ''
    # The cash a corporate-action row's event paid (cash in lieu of a
    # fraction, boot), as "<amount> <CUR>[; <amount> <CUR>]" — set by
    # the corp-action emitters and read by fx-cash, which used to tell
    # cash from the description text (re-audit A2-1014). Evidence only:
    # NOT part of compute_id, omitted from to_dict() when empty.
    corp_cash: str = ''

    def __post_init__(self):
        if self.id is None:
            self.id = self.compute_id()

    def compute_id(self) -> str:
        """Deterministic content-hash ID used for dedup, wash-sale
        linkage, and trace cross-references. Floats go in via `repr()`
        rather than fixed-decimal formatting so distinct values cannot
        collide on rounding: Python's float `repr` (≥3.1) is the
        shortest decimal that uniquely round-trips back to the same
        IEEE-754 bit pattern — deterministic across platforms and full
        precision. The earlier `:.8f` formatting truncated below the
        satoshi level, so two distinct sub-satoshi crypto quantities
        could hash to the same ID and dedup as duplicates."""
        q = repr(float(self.quantity))
        p = repr(float(self.price))
        n = repr(float(self.net_amount))
        g = repr(float(self.gross_amount))
        
        # Combine fields into a unique string
        components = [
            self.action,
            self.date,
            self.time,
            self.symbol,
            q,
            self.currency,
            p,
            n,
            g,
            self.account,
            self.type,
            self.date_settle,
            self.description
        ]
        raw_id = "|".join(str(c) for c in components)
        return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]

    def to_dict(self):
        d = asdict(self)
        for k in INCOME_FACT_FIELDS + EVIDENCE_FIELDS:
            if not d.get(k):
                d.pop(k, None)
        return d


# The optional income facts on TaxTransaction (see the class): omitted
# from to_dict() when empty so every other row keeps its shape.
INCOME_FACT_FIELDS = ('record_date', 'ex_date', 'income_label',
                      'dealer_country', 'issuer_country')
# The other optional evidence fields, omitted from to_dict() when empty.
EVIDENCE_FIELDS = ('broker_time', 'security_name', 'open_close',
                   'broker_basis', 'multiplier', 'source', 'source_key',
                   'source_account', 'exercise_of', 'corp_cash')

# OCC option-symbol pattern: [F:|/|\]<base><yymmdd><C|P><strike-8d>[.<ext>]
# e.g. "AAPL250120C00150000.US", "MDA251219P00029000.TO", or
# "F:CL251220P00053000.US" for futures options.
#
# Single regex covering every option-detection / underlying-extraction
# need across the codebase:
#   - optional futures prefix (F: or / or \)
#   - non-greedy base ticker so the OCC date/strike block consumes greedily
#   - 6-digit yymmdd, C or P, 8-digit strike-×1000
#   - optional market suffix (.TO/.US/.AX/.L)
#   - end-anchored — rejects symbols with trailing junk
#
# Centralized so the Canada engine, US engine, and ticker-map helpers
# can't drift. Sites that previously used the bare `re.search(r'\d{6}[CP]\d+', ...)`
# detector now go through `is_option_symbol` for consistency — the
# anchoring change is safe because real OCC symbols never have trailing
# junk.
_OCC_OPTION_RE = re.compile(
    r'^((?:F:|[\\\/])?'             # optional futures prefix (captured)
    r'[A-Z0-9\.]{1,10}?)'           # base ticker (non-greedy)
    r'(\d{6}[CP]\d+)'               # OCC contract block
    r'(?:\.([A-Z]+))?'              # optional market suffix
    r'$'
)


def parse_option_underlying(symbol: str):
    """Return the underlying ticker (with market suffix) for an OCC
    option symbol, or None when `symbol` doesn't look like one. The
    futures prefix (`F:`/`/`/`\\`) IS preserved — so e.g.
    `F:CL250120P00053000.US` → `F:CL.US`, keeping futures positions
    distinct from same-base equity positions in by-ticker buckets."""
    if not symbol:
        return None
    m = _OCC_OPTION_RE.match(symbol)
    if not m:
        return None
    ext = m.group(3)
    return f"{m.group(1)}.{ext}" if ext else m.group(1)


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


def is_option_symbol(symbol: str) -> bool:
    """True if `symbol` is an OCC-format option ticker. Accepts the
    `F:`/`/`/`\\` futures prefix and optional market suffix."""
    return bool(symbol) and bool(_OCC_OPTION_RE.match(symbol))


def parse_option_expiry(symbol: str) -> Optional[str]:
    """ISO expiry date ('YYYY-MM-DD') from an OCC option symbol, or None
    when `symbol` isn't one (or carries an impossible date). The OCC
    block's first six digits are yymmdd; yy pivots into 20yy — valid
    until 2099, same horizon as every other OCC consumer here."""
    if not symbol:
        return None
    m = _OCC_OPTION_RE.match(symbol)
    if not m:
        return None
    yymmdd = m.group(2)[:6]
    try:
        return datetime.strptime(yymmdd, '%y%m%d').strftime('%Y-%m-%d')
    except ValueError:
        return None


OPTION_CONTRACT_SHARES = 100.0      # shares per standard equity option

# Actions that never touch a symbol's ACB / basis pool, so their currency
# is never checked against it (both engines' currency guards skip them:
# income rows, a TRANSFER kept as evidence, a SPLIT that carries no
# money). Every other action with a currency must match the pool's — the
# raw (native-currency) pass in `taxjson run` tests the same set (audit
# A2-0440).
POOL_FREE_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST',
                     'FEE', 'TRANSFER', 'SPLIT')


def parse_option_right(symbol: str) -> Optional[str]:
    """'C' or 'P' for an OCC option symbol, else None."""
    m = _OCC_OPTION_RE.match(symbol or '')
    return m.group(2)[6] if m else None


def parse_option_strike(symbol: str) -> Optional[float]:
    """Strike price from an OCC option symbol (the 8-digit block is the
    strike x 1000), else None."""
    m = _OCC_OPTION_RE.match(symbol or '')
    return int(m.group(2)[7:]) / 1000.0 if m else None


def _opening_option_buys(events, date_of):
    """[(buy row, opening contracts)] for every option BUY that opens
    or adds to a long position. Only the OPENING part of a buy acquires
    a right (audit R1-181): a buy that closes a written option acquires
    nothing. The position before each buy is walked per (account,
    contract) in event order (stable: same-stamp rows keep the export's
    order)."""
    def _when(ev):
        return (str(date_of(ev) or ''), str(getattr(ev, 'time', '') or ''))

    bal: Dict[Tuple[str, str], float] = {}
    out = []
    for ev in sorted(events, key=_when):
        if parse_option_right(ev.symbol) is None or ev.action not in (
                'BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        k = (getattr(ev, 'account', ''), ev.symbol)
        before = bal.get(k, 0.0)
        bal[k] = before + ev.quantity
        if ev.action != 'BUYSELL' or ev.quantity <= 0:
            continue
        opening = ev.quantity - min(ev.quantity, max(0.0, -before))
        if opening > 1e-9:
            out.append((ev, opening))
    return out


def detect_option_replacement_matches(loss_entries, events, *, date_of,
                                      canonical=None, statute_label='',
                                      window_days=30,
                                      check_held_at_end=False):
    """WARN-ONLY call-as-replacement scan, used by the (experimental) US
    engine; the Canada engine enforces the same rule in its solver.

      'call_vs_share_loss': loss on LONG shares + LONG CALL acquired on
          the same underlying inside the ±window (IRS §1091 "option to
          acquire"; CRA s.54 "a right to acquire").

    One-way, per the owner's policy (2026-09-29): options replace shares,
    never the reverse, and an option is replaced only by the identical
    contract (the engines' own symbol matching). A put is a right to
    SELL, never replacement property. Detection only — numbers unchanged.

    `loss_entries`: dicts with symbol, date (already on the calling
    engine's window basis), amount (negative), id, direction.
    `events`: the full multi-scope stream (taxable + sheltered +
    affiliated — an affiliated or registered-account acquisition is still
    a replacement under both regimes).
    `canonical`: symbol canonicalizer (SplitTimeline.canonical) so a
    rename between the loss and the option acquisition still matches.
    Underlying matching is suffix-exact after canonicalization: an option
    suffixed .US never matches shares held under a .TO listing.
    `check_held_at_end`: CRA s.54 additionally requires the replacement
    still be owned at the end of the +window; when True each warning
    carries held_at_window_end for the OPTION's own position (across all
    scopes)."""
    canon = canonical or (lambda s: s)
    # A class-share root (RCI for RCI.B.TO, BRKB for BRK.B.US) names the
    # class line it delivers (A2-0016/0207).
    _cls = class_root_aliases(
        [ev.symbol for ev in events]
        + [str(l.get('symbol') or '') for l in loss_entries])
    acqs = []
    for ev, opening in _opening_option_buys(events, date_of):
        und = parse_option_underlying(ev.symbol)
        # A futures option is never sized as shares: its own rule flags
        # it for a manual check (futures_option_vs_loss, US-WASH-15 /
        # CA-SL-15 — A2-0014/0056).
        if not und or _FUTURES_PREFIX_RE.match(und):
            continue
        acqs.append([canon(_cls.get(und.upper(), und)),
                     parse_option_right(ev.symbol), ev, opening])
    if not acqs:
        return []

    def _d(s):
        return datetime.strptime(s, '%Y-%m-%d')

    # Each contract is a right to its contract size in shares (100, or
    # the declared size of a mini / adjusted contract — A2-0957) and
    # backs one denial: it is used up across the losses in date order
    # (audit S069-17 / S070-00 / S070-01 — one contract used to be cited
    # as denying every loss in its window in full).
    left = {id(a[2]): a[3] * option_contract_size(a[2]) for a in acqs}
    out = []
    order = sorted(range(len(loss_entries)),
                   key=lambda n: str(loss_entries[n].get('date') or ''))
    for n in order:
        loss = loss_entries[n]
        symbol = loss['symbol']
        if is_option_symbol(symbol):
            continue
        if loss.get('direction') == 'SHORT':
            continue            # a put is not a right to acquire
        want = 'C'
        try:
            loss_dt = _d(loss['date'])
        except (KeyError, TypeError, ValueError):
            continue
        loss_c = canon(symbol)
        window_end = loss_dt + timedelta(days=window_days)
        loss_qty = abs(float(loss.get('qty') or 0.0))
        need = loss_qty if loss_qty > 1e-9 else float('inf')
        by_contract: Dict[str, Dict[str, Any]] = {}
        for und_c, right, ev, _opening in acqs:
            if right != want or und_c != loss_c or need <= 1e-9:
                continue
            try:
                ev_dt = _d(date_of(ev))
            except (TypeError, ValueError):
                continue
            if abs((ev_dt - loss_dt).days) > window_days:
                continue
            take = min(need, left[id(ev)])
            if take <= 1e-9:
                continue
            left[id(ev)] -= take
            need -= take
            rec = by_contract.setdefault(
                ev.symbol, {'shares': 0.0, 'first': None,
                            'size': option_contract_size(ev)})
            rec['shares'] += take
            if rec['first'] is None or date_of(ev) < rec['first']:
                rec['first'] = date_of(ev)
        for occ, rec in sorted(by_contract.items()):
            held = None
            if check_held_at_end:
                hb = 0.0
                for ev in events:
                    if ev.symbol != occ or \
                            ev.action not in ('BUYSELL', 'ASSIGN'):
                        continue
                    try:
                        if _d(date_of(ev)) <= window_end:
                            hb += ev.quantity
                    except (TypeError, ValueError):
                        continue
                held = hb > 1e-6
            frac = (min(1.0, rec['shares'] / loss_qty)
                    if loss_qty > 1e-9 else 1.0)
            out.append({
                'rule': 'call_vs_share_loss',
                'loss_symbol': symbol,
                'loss_date': loss['date'],
                'loss_amount': round(float(loss['amount']), 2),
                'loss_id': loss.get('id', ''),
                'loss_qty': loss_qty or None,
                'option_symbol': occ,
                'option_acquired': rec['first'],
                'option_qty': rec['shares'] / rec['size'],
                'contract_size': rec['size'],
                'covered_shares': rec['shares'],
                'at_risk_amount': round(float(loss['amount']) * frac, 2),
                'held_at_window_end': held,
                'statute': statute_label,
            })
    return out


_RIGHT_RE = re.compile(r'^(.+?)[.\-](WTS|WT|WS|WR|RT|W|R)([.\-][A-Z])?$')


def right_underlying(symbol: str) -> Optional[str]:
    """The share line a WARRANT or RIGHT names, from the dotted/dashed
    listing spelling ('SLH.WT.TO' -> 'SLH.TO', 'ABC.RT.TO' -> 'ABC.TO',
    'XYZ.WS.US' -> 'XYZ.US'), else None. Undotted US forms ('DFDVW')
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


def detect_right_replacement_matches(loss_entries, events, *, date_of,
                                     canonical=None, statute_label='',
                                     window_days=30):
    """WARN-ONLY: a warrant or subscription right on the loss shares
    acquired inside the ±window (audit S071-14). s.54's closing words
    deem "a right to acquire a property" identical to it (IRC §1091:
    "contract or option to acquire"), but the shares one warrant buys
    are not in the books, so the engine cannot size a denial; it names
    the case for review instead. Same record shape as
    detect_option_replacement_matches, rule 'right_vs_share_loss'."""
    canon = canonical or (lambda s: s)
    acqs = []
    for ev in events:
        if ev.action != 'BUYSELL' or ev.quantity <= 0:
            continue
        und = right_underlying(ev.symbol)
        if und:
            acqs.append((canon(und), ev))
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
        for und_c, ev in acqs:
            if und_c != canon(symbol):
                continue
            try:
                ev_dt = datetime.strptime(date_of(ev), '%Y-%m-%d')
            except (TypeError, ValueError):
                continue
            if abs((ev_dt - loss_dt).days) > window_days:
                continue
            rec = by_sym.setdefault(ev.symbol, {'qty': 0.0, 'first': None})
            rec['qty'] += ev.quantity
            if rec['first'] is None or date_of(ev) < rec['first']:
                rec['first'] = date_of(ev)
        for wsym, rec in sorted(by_sym.items()):
            out.append({
                'rule': 'right_vs_share_loss',
                'loss_symbol': symbol,
                'loss_date': loss['date'],
                'loss_amount': round(float(loss['amount']), 2),
                'loss_id': loss.get('id', ''),
                'option_symbol': wsym,
                'option_acquired': rec['first'],
                'option_qty': rec['qty'],
                'held_at_window_end': None,
                'statute': statute_label,
            })
    return out


def detect_unresolved_option_replacement_matches(
        loss_entries, events, *, date_of, canonical=None,
        statute_label='', window_days=30):
    """WARN-ONLY (audit S069-23; owner decision 2026-10-01): a long CALL
    acquired inside the ±window whose underlying the engines cannot size
    is named for a manual check, the way a warrant is:

      'adjusted_option_vs_loss': a call on an ADJUSTED series of the loss
          shares (root + digit, 'ZZS1' after a corporate action on ZZS).
          Its deliverable is not 100 shares and is not in the books.
      'futures_option_vs_loss': a call on the futures contract of the
          loss named by its family root or another prefix spelling (a
          loss on F:CLG6 and a call on F:CL or /CLG6).

    The root matching is the assignment resolver's (_root_matches_stock);
    the market suffix must agree. A futures option is flagged here even
    when it names the loss's own contract (F:CLG6 call vs an F:CLG6
    loss): the engines' call rule never sizes a futures option (A2-0014/
    0056). Any other call whose underlying IS the loss symbol is left to
    the engines' own rule (Canada enforces it, the US warns:
    call_vs_share_loss), so nothing is reported twice. Puts never
    count (a right to sell). Same record shape as
    detect_right_replacement_matches."""
    canon = canonical or (lambda s: s)

    def _base(sym):
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
            if r_ext != s_ext or not _root_matches_stock(r_base, s_base):
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
                'loss_amount': round(float(loss['amount']), 2),
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


def _emit_option_replacement_stderr(warnings, *, country: str) -> None:
    for w in warnings:
        print(f"warning: {format_option_replacement_warning(w, country=country)}",
              file=sys.stderr)


def format_option_replacement_warning(w, *, country: str) -> str:
    """One option/right-replacement flag as the run prints it (after
    "warning: ")."""
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
        verdict = ("a futures contract (or an option on one) is a "
                   "§1256 contract, not stock or securities, and "
                   "usually outside §1091: the loss is NOT denied — "
                   "review it by hand")
    elif w['rule'] == 'futures_option_vs_loss':
        verdict = (f"a call on the same futures contract is a right "
                   f"to acquire it, so {_LOSS_TERM[country]}"
                   + (" (a commodity future is a §1256 contract, not "
                      "stock or securities, and usually outside "
                      "§1091)" if country == 'usa' else '')
                   + " — review it by hand")
    elif w.get('loss_qty'):
        verdict = (f"up to {w['covered_shares']:g} of the "
                   f"{w['loss_qty']:g} shares' loss "
                   f"({w['at_risk_amount']:+,.2f}) would be denied "
                   f"({w['option_qty']:g} contract(s) x "
                   f"{w.get('contract_size') or OPTION_CONTRACT_SHARES:g}"
                   f" shares, each used once)")
    else:
        verdict = "this loss would be denied"
    return (
        f"option-replacement (warn-only, numbers unchanged): "
        f"{w['loss_symbol']} loss {w['loss_amount']:+,.2f} on "
        f"{w['loss_date']} has {w['option_symbol']} acquired "
        f"{w['option_acquired']} in the ±30d window{held}; under "
        f"{w['statute']} {verdict} [{w['rule']}]")


_JSON_COMMENT_RE = re.compile(r'"(?:[^"\\]|\\.)*"|(#[^\n]*)')


def strip_json_comments(content: str) -> str:
    """Strip `#`-prefixed comments from a JSON-ish document while
    preserving any `#` that appears inside a string literal. JSON has
    no native comment syntax, but our intermediate files sometimes
    carry user-added comments for legibility. A naive line-strip would
    also drop a (perversely-formatted) JSON value beginning at column 0
    with `#`; this string-aware pass walks string literals first so the
    `#`-as-data case is safe."""
    return _JSON_COMMENT_RE.sub(
        lambda m: '' if m.group(1) is not None else m.group(0),
        content,
    )


def coerce_transaction_row(t, i: int, ctx_prefix: str) -> TaxTransaction:
    """One row's coercion + validation — the shared body of
    load_transactions and pipeline.load_stdin_transactions, so the
    stdin path cannot drift back to silently dropping rows or skipping
    the FUZZ #K hard guards. Handles TaxTransaction passthrough,
    mapping coercion, `qty` aliasing, known-field filtering, and the
    non-finite / impossible-date / SPLIT-ratio refusals."""
    import inspect
    valid_keys = inspect.signature(TaxTransaction).parameters.keys()
    if isinstance(t, TaxTransaction):
        return t
    if not isinstance(t, dict):
        # Try last-resort coercion via mapping protocol, mirroring
        # the historical sort.py behavior. A row that can't be
        # dict()'d is data corruption — refuse to silently drop
        # it. One missing buy/sell can invalidate an entire ACB
        # pool downstream, and the silent skip would make the
        # resulting wrong gain numbers very hard to trace back.
        try:
            t = dict(t)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"{ctx_prefix}: transaction at index "
                f"{i} is not a TaxTransaction, dict, or mapping-"
                f"coercible value — got {type(t).__name__}={t!r} "
                f"({e}). Refusing to drop the row silently because "
                f"a missing buy/sell would corrupt the ACB pool "
                f"downstream. Fix the upstream emitter or hand-edit "
                f"the JSON to remove the malformed entry."
            )
    if 'qty' in t and 'quantity' not in t:
        t = {**t, 'quantity': t['qty']}
    clean_t = {k: v for k, v in t.items() if k in valid_keys}
    # Hard input guards (FUZZ #K) — refuse cleanly instead of
    # letting garbage reach the engines (NaN drove the Canada
    # engine into a decimal.InvalidOperation traceback and the USA
    # engine into NaN outputs; SPLIT ratio <= 0 silently corrupted
    # pools; impossible calendar dates passed straight through).
    import math
    _ctx = (f"{ctx_prefix}: transaction at index {i} "
            f"(id={t.get('id', '?')!r}, symbol="
            f"{t.get('symbol', '?')!r})")
    # Type funnel (stage-tools audit): every numeric field must be a
    # real number (a numeric STRING is coerced with float(); null,
    # bools, lists, non-numeric strings are refused) and every string
    # field must be a str (null falls back to the field's default).
    # Before this, `"net_amount": null` reached compute_id's float()
    # as a bare TypeError traceback, `"quantity": "10"` crashed the
    # phantom walk's arithmetic, and `"symbol": 0` crashed the engines
    # — none of them caught by the tools' ValueError handlers.
    for _fld in ('quantity', 'price', 'proceeds', 'commission', 'fee',
                 'net_amount', 'gross_amount', 'multiplier'):
        if _fld not in clean_t:
            continue
        _v = clean_t[_fld]
        if _v is None or isinstance(_v, bool) \
                or not isinstance(_v, (int, float, Decimal, str)):
            raise ValueError(
                f"{_ctx}: {_fld}={_v!r} is not a number (got "
                f"{type(_v).__name__}) — fix the input data.")
        if isinstance(_v, str):
            try:
                _v = float(_v)
            except ValueError:
                raise ValueError(
                    f"{_ctx}: non-numeric {_fld}={clean_t[_fld]!r} — "
                    f"fix the input data.")
            clean_t[_fld] = _v
        if isinstance(_v, (float, Decimal)) and not math.isfinite(_v):
            raise ValueError(f"{_ctx}: non-finite {_fld}={_v!r} — "
                             f"fix the input data.")
    for _fld in ('action', 'date', 'symbol', 'currency', 'time',
                 'date_settle', 'account', 'type', 'description',
                 'symbol_new', 'corp_event_id', 'corp_election', 'id',
                 'record_date', 'ex_date', 'income_label',
                 'dealer_country', 'issuer_country', 'broker_time',
                 'security_name', 'open_close', 'broker_basis',
                 'exercise_of', 'corp_cash'):
        if _fld not in clean_t:
            continue
        _v = clean_t[_fld]
        if _v is None:
            if _fld in ('action', 'date'):
                raise ValueError(
                    f"{_ctx}: required field {_fld} is null — fix the "
                    f"input data.")
            if _fld != 'id':
                del clean_t[_fld]      # dataclass default applies
        elif not isinstance(_v, str):
            raise ValueError(
                f"{_ctx}: {_fld}={_v!r} must be a string (got "
                f"{type(_v).__name__}) — fix the input data.")
    for _fld in ('action', 'date'):
        if _fld not in clean_t:
            raise ValueError(
                f"{_ctx}: required field {_fld} is missing — fix the "
                f"input data.")
    # The engines order same-day rows by the time STRING: '9:30:00'
    # sorted after its own '09:30:01' superficial-loss bump, and '09:30'
    # crashed the bump's clock arithmetic (audit S071-13). An unpadded
    # or seconds-less clock time is written HH:MM:SS; anything else is
    # refused.
    _tm = clean_t.get('time')
    if _tm:
        _m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', _tm.strip())
        if not _m or int(_m.group(1)) > 23 or int(_m.group(2)) > 59 \
                or int(_m.group(3) or 0) > 59:
            raise ValueError(
                f"{_ctx}: time={_tm!r} is not a clock time HH:MM:SS — "
                f"fix the input data.")
        clean_t['time'] = (f"{int(_m.group(1)):02d}:{_m.group(2)}:"
                           f"{_m.group(3) or '00'}")
    # A trade row without its quantity or money gets the dataclass
    # default 0.0 — a buy at $0 cost (the whole sale becomes gain) or a
    # sale at $0 proceeds (audit R1-162). The loader serves many tools
    # (merge, ticker-map, ...), so it only MARKS the row;
    # require_trade_fields() refuses it where money is computed.
    _missing = ()
    if clean_t.get('action') in ('BUYSELL', 'ASSIGN'):
        _missing = tuple(f for f in ('quantity', 'net_amount')
                         if f not in clean_t)
    for _fld in ('date', 'date_settle'):
        _d = clean_t.get(_fld)
        if _d:
            try:
                datetime.strptime(str(_d), '%Y-%m-%d')
            except ValueError:
                raise ValueError(
                    f"{_ctx}: impossible {_fld}={_d!r} (not a real "
                    f"calendar date) — fix the input data.")
    # CA-DATE-03 / US-DATE-04: a settlement never precedes its trade;
    # the parsers refuse one, and the JSON path (taxjson-gains on a
    # hand-written file, taxjson-validate) now does too — the tax year
    # follows the settle date, so the row moved into the prior year
    # silently (audit A2-0959).
    _td, _sd = clean_t.get('date'), clean_t.get('date_settle')
    if (clean_t.get('action') in ('BUYSELL', 'ASSIGN') and _td and _sd
            and str(_sd) < str(_td)):
        raise ValueError(
            f"{_ctx}: date_settle {_sd} is before the trade date {_td} — "
            f"a settlement never precedes its trade, and the tax year "
            f"follows the settle date; fix the row (or drop date_settle "
            f"for the standard cycle).")
    if clean_t.get('action') == 'SPLIT' \
            and float(clean_t.get('quantity') or 0) <= 0:
        raise ValueError(
            f"{_ctx}: SPLIT ratio must be > 0 (got "
            f"{clean_t.get('quantity')!r}) — a zero/negative ratio "
            f"is never a real corporate action.")
    tx = TaxTransaction(**clean_t)
    if _missing:
        tx._missing_trade_fields = (_ctx, _missing)
    return tx


def require_trade_fields(transactions) -> None:
    """Refuse a BUYSELL/ASSIGN row that came in WITHOUT its quantity or
    net_amount key (see coerce_transaction_row) — the same way a null
    value is refused. Raises ValueError naming the row."""
    for t in transactions:
        miss = getattr(t, '_missing_trade_fields', None)
        if miss:
            ctx, fields = miss
            raise ValueError(
                f"{ctx}: required field(s) {', '.join(fields)} missing on "
                f"a {t.action} row — the engine would book it at 0. Fix "
                f"the input data.")


def load_transactions(path: Path) -> List[TaxTransaction]:
    """Canonical taxjson loader — the single implementation that every
    bin tool should use. Handles:

    * `#`-prefixed comments (full-line and trailing). String-aware so
      a `#` inside a JSON string value is preserved.
    * `qty` → `quantity` aliasing (older parsers/tests emit `qty`).
    * Filtering to TaxTransaction's known fields so extra keys from
      enrichment passes don't blow up the dataclass init.
    * Already-TaxTransaction items in the list (passthrough — useful
      when an in-memory pipeline composes loaders).
    * The FUZZ #K hard input guards (via coerce_transaction_row).
    """
    from taxjson.lib.cli_diag import InputReadError
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        # A BOM (a hand-written book saved by a Windows editor) is
        # dropped, as json_input and the .tt reader do (A2-1412).
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        # An OSError-class error, not the ValueError the data guards
        # raise: an unreadable file is an environment error (exit 2),
        # and it names the file (audit S070-23).
        raise InputReadError(
            f"cannot read {path}: not UTF-8 text (byte "
            f"0x{raw[e.start]:02x} at offset {e.start})") from None
    try:
        data = json.loads(strip_json_comments(text))
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"{path}: {e.msg}", e.doc,
                                   e.pos) from None

    txs = data.get("transactions", []) if isinstance(data, dict) else data
    if not isinstance(txs, list):
        # {"transactions": 5} or a bare scalar was a TypeError traceback
        # in every tool that loads a book (audit S042-18).
        raise ValueError(f"{path}: expected a JSON object with a "
                         f"\"transactions\" list (or a bare list of "
                         f"rows), got {type(txs).__name__}")
    return [coerce_transaction_row(t, i, f"load_transactions({path})")
            for i, t in enumerate(txs)]

def convert_currency(amount: float, from_curr: str, to_curr: str, rates: Dict[Any, Decimal], default_rate: float) -> float:
    """Convert amount from from_curr to to_curr.

    rates may be keyed by (from_curr, to_curr) tuples (preferred), or by
    from_curr strings — in the string form the dict's implicit target must be
    to_curr or the result is nonsense, so callers should prefer tuple keys.
    """
    if from_curr == to_curr:
        return amount
    rate = rates.get((from_curr, to_curr))
    if rate is None:
        rate = rates.get(from_curr)
    if rate is None:
        rate = Decimal(str(default_rate))
    return float(Decimal(str(amount)) * rate)

# Registry for brokerages: {id: parser_class}.
_BROKERAGES = {}


def register_brokerage(id, cls):
    _BROKERAGES[id] = cls


def load_brokerage(id):
    if id not in _BROKERAGES:
        raise ValueError(f"Unknown brokerage: {id}")
    return _BROKERAGES[id]

def _effective_fee_for_trace(tx) -> float:
    """Fee actually paid on this transaction, for display in trace lines.

    When the parser broke out commission/fee explicitly, return their sum.
    When both are zero — some brokers fold fees into net_amount only (RBC
    being the canonical case) — back-compute from |qty * price * multiplier|
    vs |net_amount|. OCC option symbols get the 100x contract multiplier so
    a -75 @ 0.4193 option sale shows the ~$100 commission RBC charged instead
    of a misleading 0.

    The math the engine uses for cost basis / proceeds is unaffected — it
    already keys off net_amount, which includes the fee. This is purely a
    display-side enrichment.
    """
    explicit = float(tx.commission) + float(tx.fee)
    if abs(explicit) > 1e-6:
        return explicit
    q = float(tx.quantity)
    qty = abs(q)
    price = float(tx.price)
    net = float(tx.net_amount)
    if qty < 1e-9 or price < 1e-9:
        return 0.0
    # The contract size the parser declared (a futures option: CL 1000,
    # ES 50 — audit S026-22), else 100 per equity option, 1 per share.
    multiplier = float(getattr(tx, 'multiplier', 0.0) or 0.0)
    if multiplier <= 0:
        multiplier = 100 if is_option_symbol(tx.symbol or '') else 1
    theoretical = qty * price * multiplier
    # The SIGNED residual (audit S070-24 / S071-00): a buy pays gross +
    # fee, a sale receives gross - fee (a cheap option's fee can exceed
    # its proceeds, so the net is negative). abs() turned a sub-cent
    # rounding residual into a positive "fee" on a zero-commission
    # broker and mis-sized a negative-net sale's fee.
    derived = (net - theoretical) if q > 0 else (theoretical - net)
    if abs(derived) < 0.01:
        return 0.0                     # price-rounding noise, not a fee
    # Units-mismatch guard (a per-contract quote against a per-share
    # net): only a residual both large and most of the trade's value.
    # The old 25%-of-net test hid real commissions on cheap options
    # (11.95 on a 32.00 buy showed as 0).
    if abs(derived) > max(50.0, 0.5 * theoretical):
        return 0.0
    return derived


def _per_share(amount, qty, symbol) -> float:
    """`amount` per SHARE for a trace column labelled per share: an
    option's quantity is in contracts of OPTION_CONTRACT_SHARES shares,
    so its per-contract figure sat next to a per-share price (audit
    S069-08: 'ACB/Sh: 500.65' beside '@ 5.00'). 0.0 for an empty
    position."""
    q = float(qty)
    if abs(q) <= 1e-6:
        return 0.0
    mult = OPTION_CONTRACT_SHARES if is_option_symbol(symbol or '') else 1.0
    return float(amount) / (q * mult)


def _disambiguate_duplicate_ids(*books) -> None:
    """Give a row whose content (and so whose id) repeats an earlier
    row's a distinct id, in place, with one NOTE. The engines link a
    loss to its denial, and a replacement lot to its basis bump, by
    tx.id: two byte-identical rows shared one id, so only the first
    took its denial or bump and the rest vanished (audit R1-169, US;
    S069-21, Canada). `taxjson run` never gets here with duplicates
    (parsers tag repeated fills "[fill #N]" and merge2 --dedup drops
    same-id rows); hand-made JSON passed to taxjson-gains can."""
    seen: Dict[str, int] = {}
    renamed = 0
    for book in books:
        for t in book or []:
            n = seen.get(t.id, 0)
            seen[t.id] = n + 1
            if n:
                new_id = f"{t.id}~{n + 1}"
                while new_id in seen:
                    n += 1
                    new_id = f"{t.id}~{n + 1}"
                seen[new_id] = 1
                t.id = new_id
                renamed += 1
    if renamed:
        print(f"NOTE: {renamed} row(s) repeat an earlier row exactly "
              f"(same date, time, symbol, quantity, price and amount); "
              f"each is booked as a separate trade. If they are "
              f"duplicates, drop them (taxjson-merge2 --dedup).",
              file=sys.stderr)


def _warn_undrained_adjustments(pending: Dict[str, float], engine: str) -> None:
    """End-of-run drainage check for staged option-assignment premium.

    ASSIGN option legs park their premium in a pending-adjustments dict to
    be consumed by the matching stock leg's BUYSELL. When the stock leg is
    absent or its symbol doesn't match (missing statement rows, symbol
    drift), the premium used to be dropped SILENTLY — the option's economics
    simply vanished from the books. Say so, per symbol, with the amount."""
    undrained = {s: amt for s, amt in pending.items() if abs(amt) > 0.005}
    if not undrained:
        return
    def _kname(k):
        return f"{k[1]} ({k[0]})" if isinstance(k, tuple) else str(k)
    detail = ", ".join(f"{_kname(s)}: {amt:+.2f}"
                       for s, amt in sorted(undrained.items(),
                                            key=lambda kv: str(kv[0])))
    print(
        f"warning: {len(undrained)} unconsumed option-assignment "
        f"adjustment(s) at end of {engine} gains run — {detail}. An ASSIGN "
        f"option leg staged this premium, but no stock trade in its "
        f"account could be paired with it: none on that symbol in the "
        f"assignment's direction from 3 days before to "
        f"{_MARKED_LEG_MAX_LAG_DAYS} days after the option row (missing "
        f"rows, a symbol mismatch, or a leg dated outside that window); "
        f"that premium is NOT reflected in any gain. Check the "
        f"underlying's buy/sell rows around the assignment date.",
        file=sys.stderr,
    )


# A marked assignment stock leg (the Webull two-row convention) is
# stamped with its option leg's trade date by the parser; allow a short
# posting lag, never more (a marked leg a year later belongs to a
# different assignment).
_MARKED_LEG_MAX_LAG_DAYS = 7


# An assignment's stock leg dated BEFORE its option row (a broker that
# books the shares on the assignment notice and the option on the next
# business day, or the reverse posting order) — up to a weekend apart.
_ASSIGN_LEG_LEAD_DAYS = 3


def exercise_target(tx) -> str:
    """The share listing a warrant/right exercise leg delivers: an ASSIGN
    row the parser marked with `exercise_of` ('' for anything else). The
    engines treat it exactly like a long call's exercise — no
    disposition; its cost rolls into the shares (tax-logic CA-OPT-09 /
    US-OPT-06)."""
    if getattr(tx, 'action', '') != 'ASSIGN':
        return ''
    tgt = (getattr(tx, 'exercise_of', '') or '').strip()
    if not tgt or is_option_symbol(getattr(tx, 'symbol', '') or ''):
        return ''
    return tgt


def is_assign_premium_leg(tx) -> bool:
    """An ASSIGN whose cost or premium rolls into a stock leg: an option
    ASSIGN, or a marked warrant/right exercise leg."""
    return (getattr(tx, 'action', '') == 'ASSIGN'
            and (is_option_symbol(getattr(tx, 'symbol', '') or '')
                 or bool(exercise_target(tx))))


def _assign_delivery_shares(opt_tx) -> Optional[float]:
    """Units an option ASSIGN delivers: contracts x the declared contract
    size (a mini x10, an adjusted deliverable — audit A2-0196), else 100
    per equity option; one futures contract per futures option (its
    declared multiplier is the futures' dollar size, not a unit count).
    A warrant exercise delivers warrants x the declared shares per
    warrant (`multiplier`); unknown (None) when none is declared."""
    q = abs(float(opt_tx.quantity or 0.0))
    if q < 1e-12:
        return None
    if exercise_target(opt_tx):
        m = float(getattr(opt_tx, 'multiplier', 0.0) or 0.0)
        return q * m if m > 0 else None
    if _FUTURES_PREFIX_RE.match(opt_tx.symbol or ''):
        return q
    m = float(getattr(opt_tx, 'multiplier', 0.0) or 0.0)
    return q * (m if m > 0 else OPTION_CONTRACT_SHARES)


def _assign_direction(opt_tx) -> Optional[int]:
    """+1 when the assignment makes the account BUY the underlying (short
    put assigned / long call exercised), -1 when it SELLS."""
    q = float(opt_tx.quantity or 0.0)
    if exercise_target(opt_tx):
        # A warrant is a long call: exercising it (the leg closes the
        # holding, q < 0) buys the shares.
        return 1 if q < 0 else None
    right = parse_option_right(opt_tx.symbol)
    if right not in ('C', 'P') or abs(q) < 1e-12:
        return None
    return 1 if (right == 'P') == (q > 0) else -1


def _signed_day_gap(a: str, b: str) -> Optional[int]:
    """b - a in days, or None when either date is unparsable."""
    try:
        return (datetime.strptime(b, '%Y-%m-%d')
                - datetime.strptime(a, '%Y-%m-%d')).days
    except (TypeError, ValueError):
        return None


def _pair_assign_legs(stream, in_scope, underlying_of=None):
    """Pair each option ASSIGN in `stream` with ITS OWN stock leg(s), by
    identity rather than by position (audit A2-0050/0051/0052/0195/0203,
    the R1-33 and S019-00 intents).

    A candidate leg is a stock BUYSELL or marked ASSIGN row on the
    option's own (account, underlying), in the assignment's share
    direction, dated within _ASSIGN_LEG_LEAD_DAYS before to
    _MARKED_LEG_MAX_LAG_DAYS after the option row. A leg that sorts
    BEFORE its option row is accepted only on strong identity (a marked
    ASSIGN leg, or the delivered quantity at the strike); a leg after it
    needs one identity sign (marked, at the strike, or the delivered
    quantity) — a bare unrelated trade is left to the ledger's proximity
    rule. Pairs are chosen best-first: the strike as the leg's price,
    then a marked leg, then the delivered quantity, then the nearest
    date (after before before), so a plain-convention assignment keeps
    its own BUYSELL leg and never claims another option's marked leg,
    and two same-moment assignments are told apart by strike — never by
    staging order. An assignment filled in several legs takes extra legs
    at its strike (or marked) until its delivered quantity is covered.

    Returns {option id: [leg id, ...]} (ids are unique after
    _disambiguate_duplicate_ids)."""
    pos = {}
    legs: Dict[Any, list] = {}
    opts = []
    for i, t in enumerate(stream):
        if not in_scope(t) or t.action not in ('BUYSELL', 'ASSIGN'):
            continue
        if is_option_symbol(t.symbol) or exercise_target(t):
            if t.action == 'ASSIGN':
                opts.append(t)
                pos[t.id] = i
        elif abs(float(t.quantity or 0.0)) > 1e-12:
            legs.setdefault((t.account, t.symbol), []).append(t)
            pos[t.id] = i
    if not opts or not legs:
        return {}
    cands = []
    for o in opts:
        und = (underlying_of(o) if underlying_of
               else parse_option_underlying(o.symbol))
        lst = legs.get((o.account, und)) if und else None
        if not lst:
            continue
        d = _assign_direction(o)
        size = _assign_delivery_shares(o)
        strike = (None if exercise_target(o)
                  else parse_option_strike(o.symbol))
        for l in lst:
            q = float(l.quantity)
            if d is not None and (1 if q > 0 else -1) != d:
                continue
            gap = _signed_day_gap(o.date or '', l.date or '')
            if gap is None or not (-_ASSIGN_LEG_LEAD_DAYS <= gap
                                   <= _MARKED_LEG_MAX_LAG_DAYS):
                continue
            after = pos[l.id] > pos[o.id]
            marked = l.action == 'ASSIGN'
            at_strike = bool(strike and strike > 0 and abs(
                float(l.price or 0.0) - strike) <= max(0.005, 1e-6 * strike))
            sized = size is not None and abs(abs(q) - size) < 1e-6
            if after:
                if not (marked or at_strike or sized):
                    continue
            elif not (marked or (at_strike and sized)):
                continue
            score = (0 if at_strike else 1, 0 if marked else 1,
                     0 if sized else 1, abs(gap), 0 if after else 1,
                     abs(pos[l.id] - pos[o.id]))
            cands.append((score, pos[o.id], o, l, abs(q), size,
                          marked or at_strike))
    cands.sort(key=lambda c: (c[0], c[1]))
    pairs: Dict[str, list] = {}
    covered: Dict[str, float] = {}
    used = set()
    for _sc, _p, o, l, q, _size, _strong in cands:
        if o.id in pairs or l.id in used:
            continue
        pairs[o.id] = [l.id]
        covered[o.id] = q
        used.add(l.id)
    for _sc, _p, o, l, q, size, strong in cands:
        if (not strong or l.id in used or o.id not in pairs
                or size is None or covered[o.id] >= size - 1e-6):
            continue
        pairs[o.id].append(l.id)
        covered[o.id] += q
        used.add(l.id)
    return pairs


def _place_assign_options(stream, pairs):
    """Move each paired option ASSIGN row to just before its first stock
    leg when that leg sorts earlier (a leg dated a day or two before the
    option row, A2-0051/0195): the premium is only known once the option
    leg closes, so the option must be processed first. Only the option's
    own row moves; every stock row keeps its place."""
    if not pairs:
        return stream
    pos = {t.id: i for i, t in enumerate(stream)}
    before: Dict[str, list] = {}
    moved = set()
    for oid, lids in pairs.items():
        first = min(lids, key=lambda lid: pos.get(lid, 0))
        if oid in pos and first in pos and pos[first] < pos[oid]:
            before.setdefault(first, []).append(oid)
            moved.add(oid)
    if not moved:
        return stream
    by_id = {t.id: t for t in stream if t.id in moved}
    out = []
    for t in stream:
        if t.id in moved:
            continue
        for oid in sorted(before.get(t.id, ()), key=lambda x: pos[x]):
            out.append(by_id[oid])
        out.append(t)
    return out


def _day_gap(a: str, b: str) -> Optional[int]:
    """|a - b| in days for two ISO dates, or None when either is unparsable."""
    try:
        return abs((datetime.strptime(a, '%Y-%m-%d')
                    - datetime.strptime(b, '%Y-%m-%d')).days)
    except (TypeError, ValueError):
        return None


_FUTURES_MONTH_RE = r'[FGHJKMNQUVXZ]\d{1,2}'
_FUTURES_PREFIX_RE = re.compile(r'^(F:|[\\/])')


def _split_underlying(sym: str):
    """('RCI', 'TO') for 'RCI.TO'; ('F:CL', 'US') for 'F:CL.US'."""
    if '.' in sym:
        base, _, ext = sym.rpartition('.')
        if ext.isalpha() and ext.isupper() and len(ext) <= 3:
            return base, ext
    return sym, ''


def _root_matches_stock(root_base: str, stock_base: str) -> bool:
    """Does an option ROOT name this stock line? Montreal / OCC roots
    drop the share class ('RCI' for RCI.B, 'BRKB' or 'BRK' for BRK.B)
    and OCC-adjusted roots carry a digit ('XYZ1' after a corporate
    action). Futures roots name the contract family ('F:CL' for the
    dated 'F:CLG6')."""
    if root_base == stock_base:
        return True
    if _FUTURES_PREFIX_RE.match(root_base):
        return bool(re.fullmatch(re.escape(root_base) + _FUTURES_MONTH_RE,
                                 stock_base))
    if _FUTURES_PREFIX_RE.match(stock_base):
        return False
    if (root_base.replace('.', '').replace('-', '')
            == stock_base.replace('.', '').replace('-', '')):
        return True
    head = re.split(r'[.\-]', stock_base)[0]
    if head != stock_base and root_base == head:
        return True
    r_nodigit = root_base.rstrip('0123456789')
    return r_nodigit != root_base and r_nodigit in (stock_base, head)


def _class_root_matches(root_base: str, stock_base: str) -> bool:
    """The share-class subset of _root_matches_stock: an option root that
    drops the class of a class-share line ('RCI' for RCI.B, 'BRKB' or
    'BRK' for BRK.B, 'ABC' for ABC.UN). Never a futures root and never
    an OCC-adjusted (digit) root — those are flagged, not resolved."""
    if (_FUTURES_PREFIX_RE.match(root_base)
            or _FUTURES_PREFIX_RE.match(stock_base)
            or root_base[-1:].isdigit()):
        return False
    head = re.split(r'[.\-]', stock_base)[0]
    if head == stock_base:
        return False
    return (root_base == head
            or root_base == stock_base.replace('.', '').replace('-', ''))


def class_root_aliases(symbols) -> Dict[str, str]:
    """{option underlying: share line} for an option root that names no
    share line among `symbols` but exactly ONE class share of that root
    on the same market: RBC books Rogers' Montreal calls under the root
    RCI (RCI251219C00045000.TO) while the shares are RCI.B.TO; OCC spells
    Berkshire B calls BRKB. Used by both engines' call-replacement rules
    (CA-SL-05 / US-WASH-12; audit A2-0015/0016/0207), the way the
    assignment resolver and the reports already map the root (S030-02,
    S040-11, S047-01). A root matching two class lines stays unresolved."""
    syms = {str(x).strip().upper() for x in symbols if x}
    shares = {x for x in syms if not is_option_symbol(x)}
    out: Dict[str, str] = {}
    for x in syms:
        und = parse_option_underlying(x) if is_option_symbol(x) else None
        if not und or und in shares or und in out:
            continue
        r_base, r_ext = _split_underlying(und)
        cands = [sh for sh in shares
                 if _split_underlying(sh)[1] == r_ext
                 and _class_root_matches(r_base, _split_underlying(sh)[0])]
        if len(cands) == 1:
            out[und] = cands[0]
    return out


def option_contract_size(opt_tx) -> float:
    """Units of the underlying one contract of `opt_tx` is a right to:
    the declared contract size (` x10` mini, an adjusted deliverable),
    else 100 for an equity option; one contract for a futures option
    (its declared multiplier is the futures' dollar size, not a unit
    count). Audit A2-0049/0957."""
    if _FUTURES_PREFIX_RE.match(getattr(opt_tx, 'symbol', '') or ''):
        return 1.0
    m = float(getattr(opt_tx, 'multiplier', 0.0) or 0.0)
    return m if m > 0 else float(OPTION_CONTRACT_SHARES)


def _make_assign_underlying_resolver(transactions, date_of, quiet=False):
    """Return resolve(option_tx) -> underlying stock symbol for an option
    ASSIGN, or None when no stock line in the option's own account
    matches (a cash-settled index option, or a missing stock leg).

    The option root is used as-is when that exact symbol trades as stock
    in the account. Otherwise the root is matched to the account's stock
    lines by class / futures-month / OCC-adjustment spelling
    (_root_matches_stock) and must name exactly ONE line that trades
    within _MARKED_LEG_MAX_LAG_DAYS of the assignment (audit R1-35,
    S019-01, S070-17, R1-176: RCI for RCI.B.TO, BRKB for BRK.B.US,
    F:CL for F:CLG6.US used to be treated as cash-settled, realizing the
    premium in the wrong year). An ambiguous match is left unresolved
    with a warning naming the candidates. `quiet` drops the per-match
    "resolved" note (a report that re-derives the engine's pairing,
    e.g. option-boundary, A2-0114; the engine run already printed it)."""
    dates: Dict[Any, list] = {}
    for t in transactions:
        if is_option_symbol(t.symbol) or t.action not in ('BUYSELL', 'ASSIGN'):
            continue
        dates.setdefault((t.account, t.symbol), []).append(t.date or '')
    by_acct: Dict[str, list] = {}
    for (acct, sym) in dates:
        by_acct.setdefault(acct, []).append(sym)
    cache: Dict[Any, Optional[str]] = {}

    def resolve(tx):
        _ex = exercise_target(tx)
        if _ex:
            # A warrant/right exercise names its shares (the parser
            # paired the legs): no root matching.
            return _ex if (tx.account, _ex) in dates else None
        und = parse_option_underlying(tx.symbol)
        if not und:
            return None
        if (tx.account, und) in dates:
            return und
        ck = (tx.account, und, tx.date)
        if ck in cache:
            return cache[ck]
        root_base, ext = _split_underlying(und)
        near = []
        for sym in sorted(by_acct.get(tx.account, ())):
            s_base, s_ext = _split_underlying(sym)
            if s_ext != ext or not _root_matches_stock(root_base, s_base):
                continue
            gaps = [_day_gap(d, tx.date or '') for d in dates[(tx.account, sym)]]
            if any(g is not None and g <= _MARKED_LEG_MAX_LAG_DAYS
                   for g in gaps):
                near.append(sym)
        out = None
        if len(near) == 1:
            out = near[0]
            if not quiet:
                print(f"note: {tx.symbol}: option root {und} resolved to "
                      f"{out}, the stock line this account trades at the "
                      f"assignment — the premium rolls into its "
                      f"cost/proceeds.", file=sys.stderr)
        elif len(near) > 1:
            print(f"warning: {tx.symbol}: option root {und} matches "
                  f"several stock lines traded at the assignment "
                  f"({', '.join(near)}) — treated as cash-settled. Map "
                  f"the option to its stock in ticker.map and re-run.",
                  file=sys.stderr)
        cache[ck] = out
        return out

    return resolve


# Canada pool quantity tolerance (S069-13). A share pool treats less
# than a millionth of a share as zero: broker exports round share
# counts, and that absorbs the rounding and float noise. A coin is
# divisible far below that — a real residue of 9e-7 BTC is property
# with its own cost — so a crypto pool (a symbol with no market suffix)
# only absorbs float-arithmetic noise: a residue under 1e-11 of the
# larger of the position and the trade.
_SHARE_QTY_EPS = 1e-6
_COIN_QTY_REL_EPS = 1e-11
_COIN_QTY_ABS_EPS = 1e-15


@functools.lru_cache(maxsize=None)
def _is_coin_symbol(symbol: str) -> bool:
    from taxjson.lib.price_chain import is_crypto_symbol
    return is_crypto_symbol(symbol or '')


def pool_qty_eps(symbol: str, *scales: float) -> float:
    """The quantity under which a Canada pool position (or a leftover)
    is zero: 1e-6 for shares and options; for a coin, float noise
    relative to `scales` (the position and the trade quantities)."""
    if not _is_coin_symbol(symbol):
        return _SHARE_QTY_EPS
    scale = max((abs(float(x)) for x in scales), default=0.0)
    return max(_COIN_QTY_ABS_EPS, _COIN_QTY_REL_EPS * scale)


def _currency_mismatch_advice(tx, pool_cur: str) -> str:
    """The fix a currency-mismatch error names (audit A2-0055/0191/0204:
    it named an underscore tool a project user never runs)."""
    if tx.action in ('ADJUST', 'DISALLOW'):
        return (f"Enter the amount in the pool's currency ({pool_cur}) — a "
                f"slip's CAD figure on a {pool_cur} listing is converted at "
                f"the row's date — or convert the whole book first "
                f"(taxjson-convert-currency --to <base>). `taxjson run` "
                f"does this itself.")
    return ("One pool holds one currency: convert the book first "
            "(taxjson-convert-currency --to <base>), or keep the listings "
            "apart (.TO vs .US). `taxjson run` converts before the "
            "filing books.")


def _warn_ticker_reused_after_rename(txs, date_of, *, rule_text: str
                                     ) -> None:
    """A symbol that TRADES after the date a SPLIT renamed it away
    (audit A2-0197). Renames are dated (owner decision): such a row is
    NOT the renamed security — it is its own identical-property class
    for the superficial-loss / wash-sale rule (SplitTimeline.class_at).
    Right when another company reuses the ticker; wrong when the broker
    still books the renamed shares under the old ticker — ticker.map's
    `RENAME OLD NEW <date> late=fold` folds those rows into NEW (and
    `late=separate` records the other case). `taxjson renames` lists
    the rows and `run --strict` stops until each is declared. One line
    per (symbol, rename)."""
    renamed: Dict[str, Tuple[str, str]] = {}
    for t in txs:
        if t.action != 'SPLIT':
            continue
        new = (getattr(t, 'symbol_new', '') or '').strip()
        if new and new != t.symbol:
            d = str(date_of(t) or '')
            if t.symbol not in renamed or d < renamed[t.symbol][0]:
                renamed[t.symbol] = (d, new)
    if not renamed:
        return
    seen = set()
    for t in txs:
        if t.action not in ('BUYSELL', 'ASSIGN') or t.symbol not in renamed:
            continue
        d_r, new = renamed[t.symbol]
        d = str(date_of(t) or '')
        if d > d_r and t.symbol not in seen:
            seen.add(t.symbol)
            print(f"warning: {t.symbol} trades on {d}, after its "
                  f"rename to {new} on {d_r}: renames are dated, so "
                  f"taxjson treats these rows as a DIFFERENT security "
                  f"from {new} for {rule_text}. If the broker still books "
                  f"the renamed shares under {t.symbol}, declare it in "
                  f"ticker.map (RENAME {t.symbol} {new} {d_r} late=fold); "
                  f"if another company now uses the ticker, `late=separate`"
                  f" (`taxjson renames`).", file=sys.stderr)


def disposition_groups(rows) -> Dict[int, int]:
    """Group the rows of one sale for Canada's superficial-loss formula
    (CA-SL-08): consecutive BUYSELL rows of one account and symbol, the
    same direction and the same date, with no row between them that moves
    that position — a sale split into fills. The clock is no help here:
    a broker stamps the partial fills of one order seconds or minutes
    apart, or all at midnight, so a day's uninterrupted sell-down in one
    account is one sale. `rows` are in processing order. Returns id(row)
    -> id(the group's first row) for every BUYSELL row; income and
    cost-adjustment rows never break a group."""
    out: Dict[int, int] = {}
    prev: Dict[tuple, tuple] = {}
    for t in rows:
        k = (t.account, t.symbol)
        if t.action == 'SPLIT':            # corporate-wide: every account
            for kk in [kk for kk in prev if kk[1] == t.symbol]:
                prev.pop(kk)
            continue
        if t.action != 'BUYSELL' or abs(t.quantity or 0) < 1e-12:
            if t.action in ('ASSIGN', 'TRANSFER', 'OPENING_BALANCE',
                            'BUYSELL'):
                prev.pop(k, None)
            continue
        p = prev.get(k)
        if (p is not None and p[0].date == t.date
                and (p[0].quantity > 0) == (t.quantity > 0)):
            out[id(t)] = p[1]
        else:
            out[id(t)] = id(t)
        prev[k] = (t, out[id(t)])
    return out


def _place_wash_adjusts(stream):
    """Move each pre-loss superficial-loss ADJUST (marked `_wash_after`
    = the loss row's id) to immediately after its loss row. s.53(1)(f)
    adds the denied loss to the substituted property when the loss is
    realized, so every later row sees it — including another fill of the
    same order at the same second. Dated loss+1 s and sorted after SELLs,
    the bump used to reach a second fill 2 s later but not one 0-1 s
    later (audit R1-31). Rows keep their sorted order otherwise."""
    moved = [t for t in stream if t.action == 'ADJUST'
             and getattr(t, '_wash_after', None)]
    if not moved:
        return stream
    losses = {t.id for t in stream
              if t.action in ('BUYSELL', 'ASSIGN')}
    after: Dict[str, list] = {}
    for t in moved:
        if t._wash_after in losses:
            after.setdefault(t._wash_after, []).append(t)
    placed = {id(t) for lst in after.values() for t in lst}
    out = []
    for t in stream:
        if id(t) in placed:
            continue
        out.append(t)
        if t.action in ('BUYSELL', 'ASSIGN') and t.id in after:
            out.extend(after.pop(t.id))
    return out


class _AssignPremiumLedger:
    """Staged option-assignment premiums, each paired with ITS OWN stock
    leg(s) (audit R1-28/32/34/178, S070-18/20, S071-11, A2-0050/0052/
    0196/0203).

    An option ASSIGN stages the amount its stock leg must absorb (the
    negative of the option's would-be gain: a BUY subtracts it from cost,
    a SELL adds it to proceeds) with the share direction the assignment
    implies (short put assigned / long call exercised -> the account
    BUYS; short call assigned / long put exercised -> it SELLS) and the
    share count it delivers (_assign_delivery_shares: the declared
    contract size, else 100; one per futures option).

    `pairs` ({option id: [leg id, ...]}, from _pair_assign_legs) names
    each assignment's own stock leg(s). A paired leg takes its own
    option's entry first; an entry whose paired leg is still to come is
    RESERVED — no other trade may take it (an unrelated trade sorted in
    between, a same-moment leg of another strike, or a plain-convention
    leg next to another option's marked leg).

    Unpaired entries keep the proximity rule: a stock trade takes only
    entries on its own (account, symbol) whose option leg traded within
    _MARKED_LEG_MAX_LAG_DAYS, in its own direction, per share: a
    spread's put premium goes to the put's shares and the call premium
    to the call's; two legs of one assignment split it; an exact-size
    leg later in the window keeps its entry from a smaller unrelated
    trade sorted in between. The last matching leg in the window takes
    any residual (mini / adjusted deliverables). An entry no leg claims
    in its window is never folded into an unrelated trade months later —
    it stays undrained and the end-of-run warning names it."""

    def __init__(self, stream, is_leg, pairs=None):
        self._pos: Dict[int, int] = {}
        self._id_pos: Dict[str, int] = {}
        self._legs: Dict[Any, list] = {}
        for i, t in enumerate(stream):
            self._pos[id(t)] = i
            if t.id is not None:
                self._id_pos.setdefault(t.id, i)
            if is_leg(t):
                self._legs.setdefault((t.account, t.symbol), []).append(
                    (i, t.date or '', float(t.quantity or 0.0)))
        self._pairs = {o: list(ls) for o, ls in (pairs or {}).items()}
        self._leg_owner = {l: o for o, ls in self._pairs.items()
                           for l in ls}
        self._e: Dict[Any, list] = {}

    _direction = staticmethod(_assign_direction)

    def stage(self, opt_tx, underlying: str, amount: float) -> None:
        key = (opt_tx.account, underlying)
        lst = self._e.setdefault(key, [])
        for e in lst:
            if e['src'] == opt_tx.id:
                e['amt'] += amount
                return
        shares = _assign_delivery_shares(opt_tx)
        lst.append({'src': opt_tx.id, 'amt': amount,
                    'dir': self._direction(opt_tx),
                    'shares': shares if shares and shares > 1e-9 else None,
                    'date': opt_tx.date or '',
                    'legs': self._pairs.get(opt_tx.id, [])})

    @staticmethod
    def _in_window(e, date: str) -> bool:
        g = _day_gap(e['date'], date)
        return g is None or g <= _MARKED_LEG_MAX_LAG_DAYS

    def _reserved(self, e, idx) -> bool:
        """Is this entry held for a paired leg that has not traded yet?"""
        return any(self._id_pos.get(l, -1) > idx for l in e['legs'])

    def _later_legs(self, e, acct, sym, idx):
        out = []
        for (i, d, q) in self._legs.get((acct, sym), ()):
            if i <= idx or abs(q) < 1e-12:
                continue
            if e['dir'] is not None and (1 if q > 0 else -1) != e['dir']:
                continue
            if self._in_window(e, d):
                out.append(abs(q))
        return out

    def _take_entry(self, lst, e, need, later) -> Tuple[float, float]:
        """Take up to `need` shares of entry `e`: (amount, shares)."""
        if e['shares'] is None:
            lst.remove(e)
            return e['amt'], 0.0
        take_sh = min(need, e['shares'])
        if take_sh >= e['shares'] - 1e-9 or not later:
            lst.remove(e)
            return e['amt'], take_sh
        part = e['amt'] * take_sh / e['shares']
        e['amt'] -= part
        e['shares'] -= take_sh
        return part, take_sh

    def take(self, tx) -> float:
        key = (tx.account, tx.symbol)
        lst = self._e.get(key)
        if not lst:
            return 0.0
        idx = self._pos.get(id(tx), -1)
        q = float(tx.quantity or 0.0)
        need = abs(q)
        sign = 1 if q > 0 else -1
        date = tx.date or ''
        total = 0.0
        # 1. This leg's own assignment (paired by identity).
        owner = self._leg_owner.get(tx.id)
        if owner is not None:
            for e in [e for e in lst if e['src'] == owner]:
                later = [l for l in e['legs']
                         if self._id_pos.get(l, -1) > idx]
                amt, took = self._take_entry(lst, e, need, later)
                total += amt
                need -= took
        # 2. Unpaired (or no-longer-reserved) entries by proximity.
        cands = [e for e in lst if self._in_window(e, date)
                 and not self._reserved(e, idx)]
        same = [e for e in cands if e['dir'] in (None, sign)]
        # An opposite-direction entry is taken only when no leg of its
        # own direction is still coming in its window (a parser whose
        # option-leg sign disagrees must not strand the premium).
        other = [e for e in cands if e['dir'] not in (None, sign)
                 and not self._later_legs(e, tx.account, tx.symbol, idx)]
        chosen = same or other
        chosen.sort(key=lambda e: 0 if (e['shares'] is not None and abs(
            e['shares'] - need) < 1e-6) else 1)
        for e in chosen:
            if need <= 1e-9:
                break
            later = self._later_legs(e, tx.account, tx.symbol, idx)
            if (e['shares'] is not None
                    and abs(e['shares'] - need) > 1e-6
                    and any(abs(l - e['shares']) < 1e-6 for l in later)):
                continue    # reserved for its exact-size leg
            amt, took = self._take_entry(lst, e, need, later)
            total += amt
            need -= took
        if not lst:
            self._e.pop(key, None)
        return total

    def undrained(self) -> Dict[Any, float]:
        out: Dict[Any, float] = {}
        for k, lst in self._e.items():
            for e in lst:
                out[k] = out.get(k, 0.0) + e['amt']
        return out


def _verify_share_conservation(position_rows, actual_qty_by_symbol,
                                engine_label, *, zero_ratio_skips):
    """Conservation post-condition: replay signed position quantities with
    plain arithmetic (+= qty; SPLIT scales; rename migrates) and compare
    against the engine's end inventory per symbol. The replay is
    deliberately SIMPLE — no chunking, no wash logic, no basis — so
    regressions in the complex pool/lot machinery (ordering, crossing-zero,
    OB scaling, rename merges, double-applied splits) surface as one
    mismatch warning here. It shares the upstream split dedup and sort
    order with the engine (a second independent copy of those would
    recreate the exact multi-copy drift this guards against); everything
    downstream of them is independent.

    `position_rows`: engine-ordered rows already filtered to what the
    engine lets mutate inventory (taxable scope; income/transfer rows
    removed). `zero_ratio_skips`: the US engine skips a ratio-0 SPLIT's
    SCALING but still applies its rename migration (2026-08 fix — the
    old full-skip forked the book); the Canada engine applies the
    ratio blindly — mirror each.

    Emits `warning:`-prefixed stderr lines (picked up by the .diag /
    DIAGNOSTICS banner); never raises."""
    expected: Dict[str, float] = {}
    redirect: Dict[str, str] = {}

    def rkey(sym: str) -> str:
        while sym in redirect:
            sym = redirect[sym]
        return sym

    for t in position_rows:
        if t.action == 'SPLIT':
            ratio = float(t.quantity or 0.0)
            k = rkey(t.symbol)
            if not (ratio == 0.0 and zero_ratio_skips):
                expected[k] = expected.get(k, 0.0) * ratio
            target = (t.symbol_new or '').strip()
            if target and target != t.symbol:
                tk = rkey(target)
                if tk != k:
                    expected[tk] = expected.get(tk, 0.0) + expected.pop(k, 0.0)
                    redirect[k] = tk
        elif t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                          'TRANSFER'):
            # (TRANSFER: the US engine's own-account move legs only —
            # the callers pass no other.)
            k = rkey(t.symbol)
            expected[k] = expected.get(k, 0.0) + t.quantity

    actual: Dict[str, float] = {}
    for sym, qty in actual_qty_by_symbol.items():
        k = rkey(sym)
        actual[k] = actual.get(k, 0.0) + qty

    for k in sorted(set(expected) | set(actual)):
        e, a = expected.get(k, 0.0), actual.get(k, 0.0)
        if abs(e - a) > 1e-3:
            print(
                f"warning: conservation: {engine_label} share-count "
                f"mismatch for {k}: replaying the transactions gives "
                f"{e:.4f} but the engine's inventory holds {a:.4f} "
                f"(delta {a - e:+.4f}). The gains for this symbol are "
                f"NOT trustworthy — this indicates an engine ordering/"
                f"split/opening-balance bug, not bad input data.",
                file=sys.stderr,
            )


def _warn_stranded_basis(pools) -> None:
    """Basis-residue post-condition (Canada pools): a pool whose quantity
    has drained to zero must carry ~zero cost — the average-cost math
    consumes exactly the remaining basis on a full drain. Residue means
    leaked/duplicated basis (chunk-math regressions) or a real-world
    late ADJUST (e.g. an ETF's ROC posting after a full exit — which is
    taxable as a gain and needs manual handling, not a silent strand).

    The full basis identity (cost in == cost consumed + cost remaining)
    is deliberately NOT asserted: reproducing the engine's premium
    apportioning, taint gating, and crossing-zero proportional cost
    would be a second implementation of the pooling itself — the
    residue check is the honest subset."""
    seen_objs = set()
    for sym, pool in pools.items():
        if id(pool) in seen_objs:
            continue                    # rename aliases share one object
        seen_objs.add(id(pool))
        if (abs(pool['qty']) <= pool_qty_eps(sym)
                and abs(float(pool['total_cost'])) > 0.02):
            print(
                f"warning: conservation: {sym} pool is EMPTY but carries "
                f"{float(pool['total_cost']):.2f} of stranded basis — "
                f"either an engine chunk-math bug or an ADJUST/ROC row "
                f"posted after the position was fully closed (the latter "
                f"is a taxable event needing manual review).",
                file=sys.stderr,
            )


def _dedupe_corporate_splits(txs: List[TaxTransaction], seen: set) -> List[TaxTransaction]:
    """Drop repeated SPLIT rows for the same corporate event.

    A stock split is a property of the SECURITY, not of an account: every
    brokerage parser emits its own SPLIT row per account, and their ids differ
    (account/description feed compute_id), so `--dedup` can't collapse them.
    On merged multi-account input — one account fed by several brokers, or the
    combined sheltered context file — the same event then arrives N times and
    the engines' symbol-global pools/walks get scaled by ratio**N (e.g. two
    accounts holding CRWD through its split doubled the pool).

    Keyed on (symbol, date, ratio, symbol_new); `seen` is shared across the
    taxable/sheltered/affiliated lists so a split present in more than one
    list is still applied exactly once. Limitation: two brokers reporting
    slightly different ratios for the same event (rounding) won't collapse.

    Delegates to SplitTimeline.dedupe — the one definition of split-event
    identity (see lib/corporate_timeline.py).
    """
    return SplitTimeline.dedupe(txs, seen)


def _fold_per_account_rename_ratios(taxable: List[TaxTransaction],
                                    sheltered: List[TaxTransaction],
                                    affiliated: List[TaxTransaction]):
    """One merger, per-account EMPIRICAL ratios -> one corporate event.

    corp_actions emits a rename-SPLIT per account with the ratio the
    broker actually delivered (qty_received / qty_disposed, so a snapped
    fractional entitlement leaves no dust). Two accounts of one merger
    can therefore carry different ratios (15 HES -> 15 CVX, 40 -> 41).
    Canada pools are symbol-global (s.47), so those rows neither dedupe
    (the ratio is part of the event key) nor scale their own account:
    the first renamed the WHOLE pool at its account's ratio and the
    second found no pool — shares went missing and the gain moved
    (2026-09 engine audit, r10: 1,100 booked for 1,220).

    Rows of one event (same symbol, date and rename target) with more
    than one ratio are folded into ONE row whose ratio is the
    balance-weighted mean sum(q_a * r_a) / sum(q_a) over the TAXABLE
    accounts' holdings just before the event (all scopes when the
    taxable book holds none) — the pool lands on exactly the shares the
    broker delivered in total, cost preserved. Accounts holding the
    symbol without a row of their own keep the first row's ratio, as
    before. Per-account balances in the wash walks become the weighted
    share of that total (a fraction of a share off per account)."""
    lists = (taxable, sheltered, affiliated)
    by_pair: Dict[tuple, list] = {}
    for li, lst in enumerate(lists):
        for i, t in enumerate(lst):
            if t.action != 'SPLIT' or not t.date:
                continue
            new = normalize_symbol_new(t.symbol, getattr(t, 'symbol_new', ''))
            if not new:
                continue
            by_pair.setdefault((t.symbol, new), []).append((li, i, t))
    # One merger booked by two brokers on different dates (IB 06-11,
    # RBC 06-15) is ONE event: rows of a (symbol, target) pair within
    # SPLIT_DATE_WINDOW_DAYS of each other form one group, keyed by its
    # earliest date (audit S071-01 — an exact-date key left the second
    # broker's row to find an empty pool and drop its extra share).
    groups: Dict[tuple, list] = {}
    for (sym, new), rows in by_pair.items():
        rows.sort(key=lambda r: r[2].date)
        cluster: list = []
        for r in rows:
            if cluster:
                gap = _day_gap(cluster[-1][2].date, r[2].date)
                if gap is None or gap > SPLIT_DATE_WINDOW_DAYS:
                    groups[(sym, cluster[0][2].date, new)] = cluster
                    cluster = []
            cluster.append(r)
        if cluster:
            groups[(sym, cluster[0][2].date, new)] = cluster
    todo = {k: rows for k, rows in groups.items()
            if len({round(float(r[2].quantity or 0), 9) for r in rows}) > 1}
    if not todo:
        return taxable, sheltered, affiliated

    def _sd(t):
        return t.date_settle or t.date

    drop = set()
    replace: Dict[tuple, TaxTransaction] = {}
    for (sym, d, new), rows in todo.items():
        first = rows[0][2]
        split_key = event_sort_key(first, profile='ca_balance', date_of=_sd)
        ratio_of = {}
        for _li, _i, t in rows:
            ratio_of.setdefault(t.account, float(t.quantity or 0))
        r0 = float(first.quantity or 0)

        def _weights(scopes):
            bal: Dict[str, float] = {}
            seen_plain = set()
            src = [t for li in scopes for t in lists[li]
                   if t.symbol == sym
                   and event_sort_key(t, profile='ca_balance',
                                      date_of=_sd) < split_key]
            src.sort(key=lambda t: event_sort_key(
                t, profile='ca_balance', date_of=_sd))
            for t in src:
                if t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE'):
                    bal[t.account] = bal.get(t.account, 0.0) + float(t.quantity or 0)
                elif (t.action == 'SPLIT'
                      and not normalize_symbol_new(t.symbol, t.symbol_new)):
                    if split_seen(seen_plain, t.symbol, t.date,
                                  t.quantity, '') is not None:
                        continue
                    for a in bal:
                        bal[a] *= float(t.quantity or 0)
            return {a: q for a, q in bal.items() if q > 1e-9}

        w = _weights((0,)) or _weights((0, 1, 2))
        tot = sum(w.values())
        if tot <= 1e-9:
            continue
        r_eff = sum(q * ratio_of.get(a, r0) for a, q in w.items()) / tot
        replace[(rows[0][0], rows[0][1])] = TaxTransaction(
            **{**first.to_dict(), 'quantity': r_eff, 'id': first.id})
        for li, i, _t in rows[1:]:
            drop.add((li, i))
        print(f"NOTE: {sym} -> {new} on {d}: per-account merger ratios "
              f"{sorted(set(round(v, 6) for v in ratio_of.values()))} "
              f"applied to the symbol-wide pool as one event at the "
              f"holdings-weighted ratio {r_eff:.6g}.", file=sys.stderr)
    out = []
    for li, lst in enumerate(lists):
        out.append([replace.get((li, i), t) for i, t in enumerate(lst)
                    if (li, i) not in drop])
    return out[0], out[1], out[2]


# A stock dividend (new shares delivered in kind), as the parsers emit
# it: a BUYSELL of the new shares at $0 with this `type`. A NEUTRAL fact
# — the parsers do not decide its tax treatment; each engine does
# (partition INPUTS-01). Canada: an acquisition at $0 cost (the declared
# amount is income and cost, added by the user; tax-logic CA-STKDIV-01),
# which counts for s.54. US: not a purchase — the new shares join the
# lots held, spreading their basis (§307) with the purchase dates
# carried over (§1223(5)); not a §1091 replacement (US-STKDIV-01).
STOCK_DIVIDEND = 'stock_dividend'


# Corporate-action rows the US engine books per parent lot (types set by
# lib/corp_actions): a §355 spin-off's two rows (the spun-off shares'
# BUYSELL and the parent's ADJUST, joined by corp_event_id) and a §356
# boot exchange's two rows (the old shares' SELL and the new shares'
# BUYSELL). Neither acquisition is "by purchase" for §1091(a).
SPINOFF_355_TYPE = 'spinoff_355'
# A custody move between two of your own TAXABLE accounts, as a pair of
# TRANSFER rows (out of one account, into the other; same symbol,
# moment, quantity and description) that `taxjson run` adds to a US
# blended book: the US engine moves the sender's FIFO lots — basis and
# purchase dates — to the receiver, with no disposition (US-BASIS-05).
# Canada pools the ACB across the accounts (s.47): nothing to move.
LOT_MOVE_TYPE = 'lot_move'
# "own-account move #N: FROM -> TO (...)" — the legs' description.
_MOVE_DESC_RE = re.compile(r'own-account move #\d+: (\S+) -> (\S+)')
REORG_356_TYPE = 'reorg_356'
_LOT_EVENT_TYPES = (SPINOFF_355_TYPE, REORG_356_TYPE)


def is_stock_dividend(tx) -> bool:
    """A parser's stock-dividend row (a $0 BUYSELL of new shares)."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return (t or '') == STOCK_DIVIDEND and a == 'BUYSELL'


class TaxRules:
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True) -> Dict[str, Any]:
        raise NotImplementedError()

class CanadaTaxRules(TaxRules):
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, option_premium_timing: str = 'close', option_grant_since: Optional[int] = None, option_buyback_loss_superficial: bool = False, option_grant_basis: str = 'settle') -> Dict[str, Any]:
        """
        Detects Superficial Losses (Wash Sales) based on CRA rules and calculates gains.
        Handles multi-account pooling and iterative adjustments.

        ITA 54 disallows a loss as "superficial" when an affiliated person
        (you, your spouse, a corporation you control, your RRSP/TFSA, etc.)
        acquired identical property in the ±30 day window and still holds
        some at the end of day 30. We split the inputs:

        - `transactions`: YOUR taxable trades (the gains/losses we compute).
        - `sheltered_transactions`: YOUR sheltered-account trades (RRSP,
          TFSA, etc.). Same taxpayer, but the basis adjustment goes onto
          property whose ACB doesn't matter for tax purposes.
        - `affiliated_transactions`: OTHER affiliated persons' trades (e.g.
          your spouse). Trigger the same superficial-loss treatment from
          your perspective; the deferred loss attaches to the affiliated
          person's substituted property per ITA 53(1)(f) — tracked by
          your spouse on THEIR return, not yours.

        Both `sheltered` and `affiliated` are pure additive context for
        wash detection; passing them only ever increases the set of
        candidate-replacement trades.
        """
        _check_engine_allowed("canada")  # test-only guard (lib/country)
        # A move between two of your own taxable accounts changes nothing
        # in Canada: the ACB is one pool across them (s.47).
        transactions = [t for t in transactions
                        if not (t.action == 'TRANSFER'
                                and t.type == LOT_MOVE_TYPE)]
        _disambiguate_duplicate_ids(transactions, sheltered_transactions,
                                    affiliated_transactions)
        # One corporate split = one application: collapse per-account SPLIT
        # duplicates across all three lists (shared `seen`) before any
        # symbol-global pool or window walk sees them.
        _seen_splits: set = set()
        transactions, sheltered_transactions, affiliated_transactions = \
            _fold_per_account_rename_ratios(
                list(transactions), list(sheltered_transactions or []),
                list(affiliated_transactions or []))
        transactions = _dedupe_corporate_splits(transactions, _seen_splits)
        sheltered_transactions = _dedupe_corporate_splits(
            sheltered_transactions or [], _seen_splits)
        affiliated_transactions = _dedupe_corporate_splits(
            affiliated_transactions or [], _seen_splits)

        all_txs = transactions + (sheltered_transactions or []) + (affiliated_transactions or [])
        sheltered_ids = {t.id for t in (sheltered_transactions or [])}
        affiliated_ids = {t.id for t in (affiliated_transactions or [])}
        taxable_ids = {t.id for t in transactions}

        def get_sort_date(tx):
            return tx.date_settle if tx.date_settle else tx.date

        # A SPLIT inside a trade's settle lag — after the execution
        # MOMENT (date, clock time) and before the settle date: the
        # engine orders by settle date, so the pool splits before the
        # executed (pre-split-denominated) quantity is consumed. The
        # executed quantity/price are re-denominated into post-split
        # terms (qty x ratio, price / ratio; the MONEY — net_amount —
        # is untouched), which books the trade correctly against the
        # post-split pool. Splits ON the settle date are already
        # handled by the pre-existing phase ladder and excluded here.
        # The execution side compares clock time because IB posts
        # corporate actions in an evening batch (20:25) dated the
        # trade day: a sale executed that morning was pre-split, and
        # a date-only "strictly between" test left phantom
        # fractional shares after an 11-for-10 split. A split with no
        # meaningful time (00:00:01, parser default) still sorts
        # before every same-day execution — the ladder's convention.
        # A RENAME-split (symbol changes) straddling the lag would
        # also need re-symboling mid-flight — refuse loudly, as
        # before (rare squared).
        _lag_splits = [t for t in all_txs
                       if t.action == 'SPLIT' and t.date]
        if _lag_splits:
            _redenoms: Dict[int, float] = {}
            for _i, _t in enumerate(all_txs):
                if not (_t.action in ('BUYSELL', 'ASSIGN') and _t.date
                        and _t.date_settle
                        and _t.date < _t.date_settle):
                    continue
                _f = 1.0
                for _sp in _lag_splits:
                    # The ladder places a SPLIT at its SORT date
                    # (settle when set): the straddle test must use
                    # the same date, or a settle-desynced SPLIT row
                    # re-denominates the trade while the pool splits
                    # later — silent phantom shares (round-six
                    # adversarial audit finding 2).
                    _spd = _sp.date_settle or _sp.date
                    if not ((_t.date, _t.time or '00:00:00')
                            < (_spd, _sp.time or '00:00:00')
                            and _spd < _t.date_settle
                            and _sp.symbol == _t.symbol):
                        continue
                    _new = getattr(_sp, 'symbol_new', '') or ''
                    if _new and _new != _sp.symbol:
                        raise SplitStraddlesSettlementError(
                            f"{_t.symbol}: the RENAME-split dated "
                            f"{_sp.date} {_sp.time or ''} ({_sp.symbol} "
                            f"-> {_new}) falls between the {_t.date} "
                            f"{_t.time or ''} execution and "
                            f"{_t.date_settle} "
                            f"settlement of a {_t.quantity:g}-share "
                            f"trade (account {_t.account}) — the "
                            f"trade would need re-symboling "
                            f"mid-flight. Set that row's date_settle "
                            f"equal to its trade date, or re-date "
                            f"the SPLIT off the lag.")
                    if float(_sp.quantity or 0):
                        _f *= float(_sp.quantity)
                if abs(_f - 1.0) > 1e-12:
                    _redenoms[_i] = _f
            if _redenoms:
                all_txs = list(all_txs)
                for _i, _f in _redenoms.items():
                    _t = all_txs[_i]
                    _d = _t.to_dict()
                    _d['quantity'] = float(_t.quantity) * _f
                    if float(_t.price or 0):
                        _d['price'] = float(_t.price) / _f
                    _r = TaxTransaction(**{**_d, 'id': _t.id})
                    all_txs[_i] = _r
                    print(f"NOTE: {_t.symbol}: re-denominated a "
                          f"{_t.quantity:g}-share trade executed "
                          f"{_t.date} through the x{_f:g} split "
                          f"inside its settle lag (booked as "
                          f"{_d['quantity']:g} post-split shares; "
                          f"money unchanged).", file=sys.stderr)
                # Rebuild the per-book lists from the same slices
                # (all_txs is transactions + sheltered + affiliated,
                # in order) so every downstream walk sees the
                # re-denominated rows.
                _n1 = len(transactions)
                transactions = all_txs[:_n1]
                _n2 = _n1 + len(sheltered_transactions or [])
                sheltered_transactions = all_txs[_n1:_n2]
                affiliated_transactions = all_txs[_n2:]

        # Each option ASSIGN's own stock leg is paired by identity in
        # the premium ledger (_pair_assign_legs: same account and
        # underlying, delivered quantity, strike, date window), TAXABLE
        # book only (re-audit: a sheltered/affiliated marked leg must
        # neither gate nor absorb the taxable premium). Keyed per
        # (ACCOUNT, symbol): in a combined multi-account book (the
        # blended pass) one account's leg never absorbs another
        # account's premium. A marked leg reserves only its own
        # option's premium (audit R1-33, A2-0052); an unrelated trade
        # sorted between the option and its leg cannot take it.
        # Option root -> the stock line its assignment delivers (the
        # root can differ from the ticker: RCI for RCI.B.TO, F:CL for
        # F:CLG6.US); see _make_assign_underlying_resolver.
        _assign_underlying = _make_assign_underlying_resolver(
            transactions, get_sort_date)

        # (ACCOUNT, underlying) pairs that actually trade as STOCK in
        # the taxable book. An option ASSIGN whose underlying is absent
        # for ITS OWN account is cash-settled (or missing its leg) —
        # its P&L must be realized on the option itself, not staged for
        # a stock leg that cannot exist. OPENING_BALANCE deliberately
        # does NOT count (re-audit: an OB-only underlying means the
        # assignment's stock leg is missing from the input — realizing
        # with the diagnosable note beats staging a premium nothing can
        # ever consume).
        taxable_stock_symbols = {
            (t.account, t.symbol) for t in transactions
            if not is_option_symbol(t.symbol)
            and t.action in ('BUYSELL', 'ASSIGN')}

        # One split/rename timeline per compute (SETTLE-basis dates, matching
        # this engine's window measurements). Virtual solver txs are never
        # SPLITs, so the timeline is stable across solver iterations — the
        # per-loss unit conversions below all query it.
        split_timeline = SplitTimeline.from_transactions(
            all_txs, date_of=get_sort_date)
        _warn_ticker_reused_after_rename(
            all_txs, lambda t: t.date,      # trade dates: a settle-lagged
            #                                 pre-rename sale is no reuse
            rule_text="the superficial-loss rule (CA-ACB-04)")

        # Ordering (phase ladder + tie-break priorities) is centralized in
        # lib/corporate_timeline.event_sort_key — profile 'ca_main' for the
        # solver pass, 'ca_balance' (no priority rung) for the running-
        # balance walks. The settle-lagged phase rationale and the
        # OB/SPLIT 00:00:00 tie rationale are documented there.

        # Iterative solver state
        final_virtual_txs = []
        final_realized_gains = []
        final_wash_sales = []
        final_global_pools = {}
        adjust_to_trigger: Dict[str, str] = {}  # adj_id -> trigger_lot.id (full)
        loss_to_trigger: Dict[str, str] = {}  # loss tx.id -> PRIMARY trigger id
        loss_to_triggers_multi: Dict[str, list] = {}  # loss tx.id -> all allocated trigger ids
        permanent_by_loss: Dict[str, float] = {}  # loss tx.id -> sheltered-allocated denial
        wash_windows: Dict[str, Dict[str, Any]] = {}  # loss tx.id -> ±30d window listing
        final_pool_snapshots: Dict[str, Dict[str, float]] = {}  # tx.id -> pool state after that tx

        # Symbol-alias map for SPLIT-renames. CRA s. 85.1(5) and
        # similar reorgs treat the pre- and post-rename security as
        # substantially identical for wash-sale purposes — so SSL.TO
        # and RGLD.US must share a single time-series when computing
        # the 30-day affiliated balance, and a loss on SSL.TO must
        # consider RGLD.US buys as potential wash triggers.
        #
        # Build equivalence classes via simple union-find on every
        # SPLIT with a non-empty symbol_new. The map sends every
        # symbol to its canonical class representative.
        # The union-find lives on the SplitTimeline (same union condition:
        # every SPLIT with a non-empty, non-self symbol_new; rename applies
        # regardless of the ratio's truthiness). This engine used to carry
        # its own copy — one more hand-maintained duplicate of the rename
        # chain, now deleted.
        alias_of = split_timeline.canonical
        # Identical-property matching is DATED (A2-0197, owner decision):
        # OLD before its rename date and NEW after it are one security;
        # an OLD row after the rename date is its own (another
        # company reusing the ticker, or a broker still booking the
        # renamed shares — ticker.map's `RENAME ... late=fold` folds
        # those into NEW before the engine runs). Same classes as
        # alias_of for every book with no such row. alias_of stays for
        # the warn-only replacement detectors.
        row_cls = split_timeline.class_of_row

        # Running affiliated balance per tx — same definition the wash test
        # uses (BUYSELL/ASSIGN/TRANSFER summed across all_txs, multiplicative
        # for SPLIT). Independent of the iteration loop because virtual
        # DISALLOW/ADJUST txs don't move this balance.
        #
        # Group trades by equivalence class (not raw symbol) so SSL.TO
        # → RGLD.US shares one chronological running balance. Without
        # this, post-rename RGLD.US buys wouldn't show up in the 30-day
        # affiliated check for an SSL.TO loss.
        running_bal_by_tx: Dict[str, float] = {}
        for rep in set(row_cls(t) for t in all_txs):
            txs_sym = sorted(
                [t for t in all_txs if row_cls(t) == rep],
                key=lambda t: event_sort_key(t, profile='ca_balance',
                                              date_of=get_sort_date),
            )
            # Per-RAW-SYMBOL sub-balances: a rename-SPLIT's ratio
            # scales only the shares of the symbol the event names —
            # scaling the whole class multiplied target-symbol shares
            # acquired BEFORE the rename, fabricating still-held
            # balance (2026-09 adversarial audit; the US engine's
            # per-symbol walks were the reference model).
            sub: Dict[str, float] = {}
            for t in txs_sym:
                # OPENING_BALANCE counts: missing-history shares ARE held, and
                # excluding them made every walk that spans the opening
                # under-count the position (a clean loss after a missing-history
                # drain could net to zero and dodge a real wash sale).
                if t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                'OPENING_BALANCE'):
                    sub[t.symbol] = sub.get(t.symbol, 0.0) + t.quantity
                elif t.action == 'SPLIT':
                    _dst = (t.symbol_new or '').strip() or t.symbol
                    sub[_dst] = (sub.get(_dst, 0.0)
                                 if _dst != t.symbol else 0.0) \
                        + sub.pop(t.symbol, 0.0) * t.quantity
                running_bal_by_tx[t.id] = sum(sub.values())
        
        # Limit iterations to prevent infinite loops. Pathological inputs
        # (very dense same-symbol activity producing daisy-chained losses
        # across many accounts) can in principle exhaust this. We detect
        # non-convergence below and warn — silently truncating would
        # silently mis-state gains.
        # --- ITA s.49(1) option premium timing (see docs/design) -------
        # 'grant': a written option's premium is a capital gain on the
        # write date; a buy-back is a loss on its own date; expiry adds
        # nothing; a stock-settled assignment folds the premium into the
        # share leg and its grant record is not emitted (s.49(3)/(3.1)
        # deem the grant and exercise not to be dispositions; s.49(4)
        # only lets the grant year be reassessed to match). Contracts written before
        # `option_grant_since` keep close timing.
        #
        # Every short opening of a taxable option pool — pre-since
        # writes, the short leftover of a sell that crosses zero, and a
        # short OPENING_BALANCE included — is a LOT in the pool's
        # `grants` list, in write order. A close consumes the lots
        # strictly FIFO (the same order the pool walk sees the rows),
        # so an ASSIGN consumes exactly the units the old pre-scan
        # predicted. A recognised lot is carved out of the pool at its
        # own per-unit premium (so a buy-back of it is a loss of the
        # amount paid, whatever the other lots were written at); the
        # rest of the pool (close-timing lots, wash residue) keeps
        # average cost among itself. When an ASSIGN consumes a
        # recognised lot its grant record is retracted for those units
        # (s.49(3)/(3.1); s.49(4) reassesses the grant year) — the grant record is mutated in place within the
        # same pass, which is what the old pre-scan approximated from
        # outside the walk (it missed lots with no grant record: a
        # pre-since write or a cross-zero leftover consumed by the
        # ASSIGN double-counted the premium — 2026-09 engine audit).
        _grant_mode = (str(option_premium_timing or 'close').lower() == 'grant')
        _lot_seq = [0]

        def _ev_key(t):
            return event_sort_key(t, profile='ca_main',
                                  date_of=get_sort_date)

        def _trade_money(tx) -> float:
            """The trade's money in pool terms: a BUY's cost (magnitude —
            parsers and .tt books spell it either sign), a SELL's
            proceeds SIGNED. Schema: trade net_amount is positive and
            direction lives in the quantity, so a negative SELL amount
            only arises when the commission exceeds the gross (closing a
            worthless option for $0.01, writing one for less than the
            fee): the proceeds really are negative. abs() booked them
            as positive proceeds (a -220.90 loss reported as -201.00;
            2026-09 engine audit)."""
            _n = float(tx.net_amount or 0.0)
            if (tx.type or '') == 'futures_settlement':
                # A futures fill on the settlement basis (lib/futures.py):
                # net_amount is the realized P/L, SIGNED (+ received,
                # - paid), 0 on an opening. A sell's proceeds are it; a
                # buy (a short's cover) costs its negation, so a short
                # closed at a profit realizes exactly the P/L.
                return _n if float(tx.quantity or 0.0) < 0 else -_n
            return _n if float(tx.quantity or 0.0) < 0 else abs(_n)

        def _write_year(tx) -> str:
            # The write's YEAR on the project's tax_date basis — the
            # same date the return's year filter uses (audit S068-21:
            # with tax_date = "trade", a 2024-12-31 write settling in
            # 2025 was grant-booked in 2024 although since=2025 put it
            # on close timing, and no year's return taxed it).
            _d = (tx.date if str(option_grant_basis).lower() == 'trade'
                  else get_sort_date(tx))
            return str(_d or '')[:4]

        def _grant_applies(tx) -> bool:
            if not _grant_mode or not is_option_symbol(tx.symbol or ''):
                return False
            if option_grant_since is None:
                return True
            try:
                return int(_write_year(tx)) >= int(option_grant_since)
            except (TypeError, ValueError):
                return True

        def _short_lot_open(pool, tx, units, premium, recognise):
            """Append a short-option lot of `units` whose (signed, net
            of commission) premium is `premium`; returns the lot."""
            _lot_seq[0] += 1
            lot = {'tx_id': tx.id, 'seq': _lot_seq[0], 'units': units,
                   'per_unit': (premium / units) if units > 1e-12 else 0.0,
                   'rec': bool(recognise), 'rec_ref': None,
                   'loss_ref': None, 'date': tx.date,
                   'year': _write_year(tx)}
            pool.setdefault('grants', []).append(lot)
            return lot

        def _short_lot_close(pool, closing_qty, is_assign):
            """Consume `closing_qty` units of a SHORT option pool's lots
            FIFO. Returns (cost_removed, recognised, grant_units_closed,
            by_year) where cost_removed is the premium leaving the pool
            (positive = premium), recognised the part already booked at
            grant, and by_year {write year: {'units', 'premium'}} the
            recognised lots this close consumed — Schedule 3 and
            reconcile-slips count a buy-back of a SAME-year write once
            with its write, but a buy-back of an earlier year's write
            is a disposition of its own (A2-0320/0650/0651), and a
            broker's close-year slip carries that earlier premium
            (A2-0657)."""
            lots = pool.get('grants') or []
            qabs = abs(pool['qty'])
            total = float(pool['total_cost'])
            rq = sum(l['units'] for l in lots if l['rec'])
            r_amt = sum(l['units'] * l['per_unit'] for l in lots if l['rec'])
            n_other = qabs - rq
            if n_other > 1e-9:
                other_avg = (total - r_amt) / n_other
                rec_extra = 0.0
            else:
                # Only recognised lots remain: any residue in the pool
                # (a parked wash deferral) rides them pro rata so the
                # pool still drains to exactly zero.
                other_avg = 0.0
                rec_extra = (total - r_amt) / rq if rq > 1e-9 else 0.0
            cost = 0.0
            recognised = 0.0
            g_units = 0.0
            by_year: Dict[str, Dict[str, float]] = {}
            rem = closing_qty
            for lot in lots:
                if rem <= 1e-9:
                    break
                take = min(rem, lot['units'])
                if take <= 1e-12:
                    continue
                lot['units'] -= take
                rem -= take
                if not lot['rec']:
                    cost += take * other_avg
                    continue
                cost += take * (lot['per_unit'] + rec_extra)
                if is_assign:
                    # s.49(3) (call) / s.49(3.1) (put): the granting is
                    # deemed not to be a disposition (s.49(4) reopens
                    # the grant year) — retract these units from the grant
                    # record; the full premium folds into the share leg.
                    _amt = take * lot['per_unit']
                    ref = lot.get('rec_ref')
                    if ref is not None:
                        _q0 = ref['qty']
                        _f = (take / _q0) if _q0 > 1e-12 else 1.0
                        ref['qty'] = _q0 - take
                        ref['gain'] -= _amt
                        ref['taxable_gain'] -= _amt
                        ref['cost'] -= _amt
                        ref['commission'] *= max(0.0, 1.0 - _f)
                        ref['fee'] *= max(0.0, 1.0 - _f)
                        if ref['qty'] <= 1e-9:
                            ref['_void'] = True
                    lref = lot.get('loss_ref')
                    if lref is not None:
                        lref['qty'] -= take  # cov: a2-1596-grant-loss-ref
                        lref['loss_amount'] = max(
                            0.0, lref['loss_amount'] + _amt)
                        if lref['qty'] <= 1e-9 or lref['loss_amount'] <= 0.001:
                            lref['_void'] = True
                else:
                    recognised += take * lot['per_unit']
                    g_units += take
                    _by = by_year.setdefault(
                        lot.get('year') or '', {'units': 0.0,
                                                'premium': 0.0})
                    _by['units'] += take
                    _by['premium'] += take * lot['per_unit']
            # The lots always cover the close (A2-1596): every short
            # opening of an option pool opens a lot (write, crossing
            # sell, missing-history opening), a split scales lots and
            # pool alike, a rename merges both, a drain clears both, and
            # a close takes the same quantity from each — so `rem` ends
            # at zero and no closed unit is left uncosted.
            pool['grants'] = [l for l in lots if l['units'] > 1e-9]
            return cost, recognised, g_units, by_year

        def _open_short_option(pool, tx, units, premium, fee_share):
            """A taxable short opening of `units` option contracts for a
            (signed, net) `premium`: record the lot and, under grant
            timing, emit the s.49(1) grant record on the write date."""
            rec = _grant_applies(tx)
            lot = _short_lot_open(pool, tx, units, premium, rec)
            if not rec or units <= 1e-9 or tx.id not in taxable_ids:
                return
            symbol = tx.symbol
            _g = float(premium)
            _gt = []
            if trace:
                if symbol not in symbol_acb_traces:
                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                symbol_acb_traces[symbol].append(
                    f"# {tx.date} WRITE {units:10.4f} @ {tx.price:7.4f} | premium {_g:10.4f} recognised now (ITA s.49(1))")
                _gt = list(symbol_acb_traces[symbol])
            rec_d = {
                'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                'date_settle': tx.date_settle or tx.date,
                'gain': _g, 'qty': units,
                'cost': _g, 'proceeds': 0.0,
                'disallowed': 0.0, 'taxable_gain': _g,
                'days_held': 0, 'account': tx.account,
                'currency': tx.currency,
                'commission': float(tx.commission or 0) * fee_share,
                'fee': float(tx.fee or 0) * fee_share,
                'direction': 'SHORT',
                'tainted': pool.get('tainted', False),
                'grant': True,
                'note': 'WRITE — premium recognised on grant (ITA s.49(1))',
                'trace': _gt,
            }
            iteration_realized_gains.append(rec_d)
            lot['rec_ref'] = rec_d
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
            if (_g < -0.001 and option_buyback_loss_superficial
                    and not pool.get('tainted', False)):
                _dis = next((v for v in final_virtual_txs
                             if v.action == 'DISALLOW' and v.id == tx.id),
                            None)
                if _dis is not None:
                    rec_d['disallowed'] = _dis.net_amount
                    rec_d['taxable_gain'] = _g + _dis.net_amount
                loss_d = {'tx': tx, 'loss_amount': abs(_g),
                          'qty': units, 'direction': 'SHORT'}
                iteration_losses.append(loss_d)
                lot['loss_ref'] = loss_d

        # Each sale's pre-loss bump placement, kept across passes (see
        # _disp_last).
        _disp_last_seen: Dict[int, Any] = {}
        solver_converged = False
        solver_iterations_used = 0
        for iteration in range(1000):
            solver_iterations_used = iteration + 1
            current_tx_list = all_txs + final_virtual_txs
            current_tx_list.sort(
                key=lambda x: event_sort_key(x, profile='ca_main',
                                             date_of=get_sort_date))
            current_tx_list = _place_wash_adjusts(current_tx_list)

            # Each option ASSIGN paired with its own stock leg; a leg
            # dated before its option row gets the option moved in front
            # of it (A2-0051/0195).
            def _taxable_scope(_t):
                return (_t.id not in sheltered_ids
                        and _t.id not in affiliated_ids)
            _assign_pairs = _pair_assign_legs(
                current_tx_list, _taxable_scope, _assign_underlying)
            current_tx_list = _place_assign_options(current_tx_list,
                                                    _assign_pairs)
            
            # Pools indexed by symbol
            global_pools = {}  # symbol -> {'qty', 'total_cost', 'last_acq_date', 'currency', 'tainted'}

            # Staged option-assignment premiums, paired with their own
            # stock legs (see _AssignPremiumLedger).
            pending_adjustments = _AssignPremiumLedger(
                current_tx_list,
                lambda _t: (not is_option_symbol(_t.symbol)
                            and not exercise_target(_t)
                            and _t.action in ('BUYSELL', 'ASSIGN')
                            and _taxable_scope(_t)),
                _assign_pairs)
            iteration_realized_gains = []
            iteration_losses = []
            iteration_trace = []
            symbol_acb_traces = {}
            pool_snapshots_this_iter: Dict[str, Dict[str, float]] = {}
            
            if trace:
                iteration_trace.append("# " + "-" * 215)
                iteration_trace.append("# ACCOUNT                    | NOTE         | DATE                | SYMBOL                     | ACTION   | QTY        | PRICE      | FEE/SH     | PRICE_FEE  | VALUE_NET  | ADJUSTMENT | GLOBAL_BAL | POOL_ACB   | ACB/SHARE  | REALIZED   | DISALLOWED | TRIGGER_INFO")
                iteration_trace.append("# " + "-" * 215)

            for tx in current_tx_list:
                symbol = tx.symbol
                account = tx.account
                is_sheltered = tx.id in sheltered_ids
                is_affiliated = tx.id in affiliated_ids
                # Sheltered and affiliated trades both live OUTSIDE the
                # taxable ACB pool — sheltered because registered-account
                # property is treated as separate for cost-basis purposes,
                # affiliated because the spouse / controlled corp owns it,
                # not the user. The wash-sale walk still sees them via
                # current_tx_list (so the 30-day "still held" balance and
                # the substitution-property rule fire correctly), but the
                # pool stays clean.
                is_other_scope = is_sheltered or is_affiliated

                # Non-capital cash-flow events (dividends, interest, fees,
                # taxes, transfers) don't touch the ACB pool or account
                # balances. They often live on a synthetic "CASH" symbol
                # whose currency varies by row, so skip them before
                # currency/pool initialization. TRANSFER is handled at the
                # CLI layer — for sheltered files it's rewritten to BUYSELL
                # before reaching the engine, and for taxable files the CLI
                # errors out before invoking compute_gains. By the time
                # the engine runs, no TRANSFER rows should be present.
                if tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
                   or tx.type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee'):
                    continue

                if symbol not in global_pools:
                    # total_cost is held as Decimal to avoid drift across many
                    # accumulating transactions; qty stays float (share counts
                    # don't suffer the same way at sub-cent scales).
                    # 'tainted' goes True when a missing-history OPENING_BALANCE enters
                    # the pool (pre-data-window shares with unknown ACB) and
                    # back False when the pool drains to zero. Dispositions
                    # from a tainted pool are excluded from gain computation.
                    # `position_start_date` tracks the date of the trade
                    # that re-opened the current run of holding this
                    # symbol (cleared on each drain-to-zero, set again
                    # on the next open). Used by the inventory section
                    # so a downstream tool can look up the price as of
                    # the position entry date.
                    global_pools[symbol] = {'qty': 0.0, 'total_cost': Decimal(0), 'last_acq_date': '1970-01-01', 'currency': '', 'tainted': False, 'position_start_date': None, 'deferred_wash': 0.0, 'grants': []}
                pool = global_pools[symbol]
                _pre_qty = pool['qty']      # the drain tolerance's scale

                # Currency-mix guard: a single ACB pool must be
                # denominated in one currency. Gated on the taxable
                # scope — sheltered/affiliated trades live in their
                # own books and must not stamp (or be enforced
                # against) the taxable pool's currency. Without this
                # gate, an opening sheltered trade in USD would set
                # the pool's currency, then a later taxable CAD trade
                # on the same symbol would hard-error on the mismatch.
                # A SPLIT carries no money, so its currency stamp says
                # nothing about the pool: a .tt SPLIT is stamped CAD
                # whatever the listing (kept so row ids stay stable),
                # and a USD stock's split or rename stopped the native
                # gains pass (A2-0010, regression of R1-126). A rename's
                # real currency mix still shows on the trades either side.
                if not is_other_scope and tx.action != 'SPLIT':
                    if tx.currency and pool['currency'] and tx.currency != pool['currency']:
                        raise ValueError(
                            f"Currency mismatch for {symbol}: pool is in {pool['currency']!r} "
                            f"but {tx.action} {tx.id} on {tx.date} is in {tx.currency!r}. "
                            + _currency_mismatch_advice(tx, pool['currency']))
                    if tx.currency and not pool['currency']:
                        pool['currency'] = tx.currency

                # Internal adjustment logic (Option Assignment).
                # Gated on the taxable scope: only a taxable BUYSELL
                # on the underlying may consume the pending premium.
                # Popping for a sheltered/affiliated trade would
                # silently discard the adjustment (the pass branch
                # downstream skips pool mutation for is_other_scope),
                # leaving a later taxable trade on the same underlying
                # with no premium roll.
                if (is_other_scope or is_option_symbol(symbol)
                        or exercise_target(tx)
                        or tx.action not in ('BUYSELL', 'ASSIGN')):
                    # Only a stock trade can be an assignment's leg (an
                    # ADJUST / SPLIT / OB row on the underlying used to
                    # pop — and drop — the staged premium).
                    internal_adj = 0.0
                else:
                    # The ledger keeps a premium whose own (paired) leg
                    # is still to come away from this trade.
                    internal_adj = pending_adjustments.take(tx)
                
                action = tx.action
                qty = tx.quantity
                
                # Variables for trace
                realized_pl = None
                disallowed_amt = 0.0
                note = ""
                trigger_info = ""
                adjustment_shown = 0.0
                
                is_option_assign = False
                if is_assign_premium_leg(tx):
                    _und = (None if is_other_scope
                            else _assign_underlying(tx))
                    if (_und and (tx.account, _und)
                            in taxable_stock_symbols) \
                            or is_other_scope:
                        # Sheltered/affiliated assignment pairs keep the
                        # premium-roll semantics regardless of the
                        # TAXABLE stock set — their pools aren't
                        # computed here, and the cash-settled note would
                        # be spurious for a complete sheltered pair
                        # (re-audit).
                        is_option_assign = True
                    else:
                        # Cash-settled assignment/exercise (index
                        # options like XSP/SPX — the underlying never
                        # trades as stock in this book). No stock leg
                        # can ever consume a staged premium, so fall
                        # through to NORMAL disposition accounting: the
                        # option's own P&L is realized here instead of
                        # being staged forever and dropped. The note
                        # keeps the missing-data case diagnosable.
                        print(f"note: {symbol}: assignment treated as "
                              f"cash-settled ("
                              f"{parse_option_underlying(symbol) or exercise_target(tx) or '?'}"
                              f" never trades as stock in this book) — "
                              f"option P&L realized directly. If a stock "
                              f"leg is missing from your input, add it "
                              f"and re-run.", file=sys.stderr)
                
                if action == 'DISALLOW':
                    note = "DISALLOWANCE"
                    adjustment_shown = tx.net_amount
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        symbol_acb_traces[symbol].append(f"# {tx.date} DISALLOW {tx.net_amount:10.4f} | (Loss disallowed on this date)")
                elif action == 'ADJUST':
                    _applied_adj = float(tx.net_amount)
                    if not is_other_scope:
                        # A user ROC ADJUST landing on a DRAINED pool
                        # (position fully sold before the distribution
                        # posted) has no shares to adjust: the residual
                        # silently folds into the NEXT position's ACB,
                        # misstating BOTH dispositions. Retroactive
                        # reallocation is out of scope — warn loudly so
                        # the user re-dates or hand-applies it. Wash
                        # virtual ADJUSTs (WASH_*) always target held
                        # pools by construction.
                        # A return of capital (negative, non-wash
                        # ADJUST) on a DRAINED pool has no ACB left to
                        # reduce: it is a capital gain in the year it
                        # is received — the s.40(3) outcome with an
                        # ACB of nil — and must not touch total_cost
                        # (it used to leak into the NEXT purchase's
                        # ACB, understating that position's later
                        # gain; seen on real 2026 data).
                        _empty_roc = (abs(pool['qty']) <= 1e-6
                                      and not str(tx.id or '').startswith('WASH_')
                                      and float(tx.net_amount) < -0.005)
                        if (abs(pool['qty']) <= 1e-6
                                and not str(tx.id or '').startswith('WASH_')
                                and abs(float(tx.net_amount)) > 0.005
                                and iteration == 0):
                            # iteration gate: the solver replays the
                            # whole book each pass, so an unconditional
                            # warning printed once PER ITERATION — one
                            # data problem masqueraded as several.
                            if _empty_roc:
                                print(
                                    f"warning: {symbol} return of capital "
                                    f"of {-float(tx.net_amount):.2f} on "
                                    f"{tx.date} hits an EMPTY pool — the "
                                    f"position was fully sold before it "
                                    f"posted, so there is no ACB to "
                                    f"reduce: booked as a capital gain in "
                                    f"that year (ITA s.40(3), ACB nil). "
                                    f"If it belongs to the sold position, "
                                    f"re-date the ADJUST before the final "
                                    f"sale instead.", file=sys.stderr)
                            else:
                                print(
                                    f"warning: {symbol} ADJUST of "
                                    f"{float(tx.net_amount):.2f} on {tx.date} "
                                    f"hits an EMPTY pool — the position was "
                                    f"fully sold before this ADJUST posted, so "
                                    f"the amount would leak into the NEXT "
                                    f"position's ACB instead of the one that "
                                    f"earned it. Re-date the ADJUST before "
                                    f"the final sale (adjusting that "
                                    f"disposition's gain) or apply it "
                                    f"manually.", file=sys.stderr)
                        _applied_adj = float(tx.net_amount)
                        if (pool['qty'] < -1e-6
                                and not str(tx.id or '').startswith('WASH_')):
                            # A SHORT pool's total_cost holds the short
                            # sale's proceeds: a return of capital while
                            # short is a compensation payment BY the
                            # short seller (brokers debit it; parsers
                            # book it as a positive ADJUST), which adds
                            # to the cost of covering — it must LOWER the
                            # short's gain. Applied as-is it raised the
                            # gain by the amount (2x off, audit R1-157).
                            _applied_adj = -_applied_adj
                            if iteration == 0:
                                print(f"note: {symbol}: ADJUST of "
                                      f"{float(tx.net_amount):+.2f} on "
                                      f"{tx.date} lands on a SHORT "
                                      f"position — booked as the short "
                                      f"seller's compensation payment "
                                      f"(it changes the cover's gain by "
                                      f"{-float(tx.net_amount):+.2f}).",
                                      file=sys.stderr)
                        if _empty_roc:
                            _applied_adj = 0.0
                            _excess = -float(tx.net_amount)
                            if tx.id in taxable_ids:
                                iteration_realized_gains.append({
                                    'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                                    'date_settle': tx.date_settle or tx.date,
                                    # No actual sale: T4037 says enter
                                    # 0 on line 13199 and the gain on
                                    # 13200 (audit R1-43), so proceeds 0
                                    # and the nil-reset shows as -excess.
                                    'gain': _excess, 'qty': 0.0,
                                    'cost': -_excess, 'proceeds': 0.0,
                                    'disallowed': 0.0, 'taxable_gain': _excess,
                                    'days_held': 0, 'account': tx.account,
                                    'currency': tx.currency,
                                    'commission': 0.0, 'fee': 0.0,
                                    'direction': 'LONG',
                                    'tainted': pool.get('tainted', False),
                                    'deemed': True,
                                    'note': 'DEEMED GAIN — return of capital received with no shares held (ITA s.40(3), ACB nil)',
                                    'trace': [],
                                })
                        if str(tx.id or '').startswith('WASH_'):
                            # Superficial-loss deferral: the invariant is
                            # "reduce future gains by the denied loss".
                            # For a LONG pool that means RAISING ACB
                            # (+amt); for a SHORT pool, LOWERING the
                            # entry proceeds held in total_cost (-amt).
                            # The creation-time sign keyed off the LOSS
                            # side, but s.47 pools are symbol-GLOBAL: in
                            # a blended multi-account book the pool at
                            # the landing site can be long while the
                            # loss (and its trigger) were short — the
                            # unconditioned -amt then LOWERED long ACB,
                            # inflating every later gain. Decide by the
                            # pool's actual direction here; a flat pool
                            # keeps the creation-sign proxy (the trigger
                            # opens on the loss side).
                            _mag = abs(_applied_adj)
                            if pool['qty'] > 1e-6:
                                _applied_adj = _mag
                            elif pool['qty'] < -1e-6:
                                _applied_adj = -_mag  # cov: a2-1596-wash-short-pool-sign
                            else:
                                # FLAT pool: the sign cannot be decided
                                # yet — it belongs to whichever
                                # direction the pool NEXT opens
                                # (creation-sign fallback leaked a
                                # short-signed deferral into a
                                # subsequent LONG opening with
                                # inverted effect — conservation
                                # fuzzer, seed 65). Park the magnitude;
                                # the next opening applies it.
                                pool['pending_wash'] = (
                                    pool.get('pending_wash', 0.0)
                                    + _mag)
                                _applied_adj = 0.0
                        pool['total_cost'] += D(_applied_adj)
                        # Superficial-loss deferrals arrive as virtual
                        # ADJUSTs (id WASH_<loss>__...). Tally the
                        # dollars parked in this pool so inventory can
                        # report how much of the basis is deferred
                        # loss vs. real purchase cost.
                        if str(tx.id or '').startswith('WASH_'):
                            pool['deferred_wash'] = (
                                pool.get('deferred_wash', 0.0)
                                + abs(float(tx.net_amount)))
                        # ITA s.40(3): return-of-capital that drives ACB
                        # below zero is a deemed capital gain in that
                        # year. Rare enough to warrant human eyes rather
                        # than silent computation — flag, don't book.
                        if (pool['qty'] > 1e-6 and pool['total_cost'] < D('-0.005')
                                and not str(tx.id or '').startswith('WASH_')):
                            # ITA s.40(3): a return of capital that drives
                            # the ACB below zero is a capital gain in the
                            # year of the distribution, and the ACB resets
                            # to nil. Booked as a qty-0 record dated the
                            # ADJUST (the sale year got the whole amount
                            # before — right total, wrong year).
                            _excess = float(-pool['total_cost'])
                            pool['total_cost'] = Decimal(0)
                            if tx.id in taxable_ids:
                                iteration_realized_gains.append({
                                    'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                                    'date_settle': tx.date_settle or tx.date,
                                    # No actual sale: T4037 says enter
                                    # 0 on line 13199 and the gain on
                                    # 13200 (audit R1-43), so proceeds 0
                                    # and the nil-reset shows as -excess.
                                    'gain': _excess, 'qty': 0.0,
                                    'cost': -_excess, 'proceeds': 0.0,
                                    'disallowed': 0.0, 'taxable_gain': _excess,
                                    'days_held': 0, 'account': tx.account,
                                    'currency': tx.currency,
                                    'commission': 0.0, 'fee': 0.0,
                                    'direction': 'LONG',
                                    'tainted': pool.get('tainted', False),
                                    'deemed': True,
                                    'note': 'DEEMED GAIN — return of capital exceeded ACB (ITA s.40(3)); ACB reset to nil',
                                    'trace': [],
                                })
                            print(
                                f"NOTE: {symbol}: return of capital on "
                                f"{tx.date} exceeded the ACB by "
                                f"{_excess:.2f} — booked as a deemed "
                                f"capital gain in that year (ITA s.40(3)); "
                                f"ACB reset to nil.",
                                file=sys.stderr,
                            )
                    _shown_adj = (_applied_adj if not is_other_scope
                                  else tx.net_amount)
                    adjustment_shown = _shown_adj
                    note = "ACB_ADJUST"
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        pool_cost_f = float(pool['total_cost'])
                        acb_sh = _per_share(pool_cost_f, pool['qty'], symbol)
                        symbol_acb_traces[symbol].append(f"# {tx.date} ADJUST   {_shown_adj:10.4f} | Fee: 0.0000 | Cost_Added: {_shown_adj:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
                elif action == 'SPLIT':
                    if not is_other_scope:
                        pool['qty'] *= qty
                        if qty and pool.get('grants'):
                            # Short-option lots follow the contract
                            # re-denomination (same premium per lot).
                            for _l in pool['grants']:
                                _l['units'] *= qty
                                _l['per_unit'] /= qty
                    # Rename the pool when symbol_new is set and differs
                    # from the source symbol — that's how mergers and
                    # corporate reorganizations move ACB onto a new
                    # ticker (e.g. SSL.TO → RGLD.US 1-for-16 via the
                    # s. 85.1(5) rollover election). Before this, the
                    # SPLIT silently dropped symbol_new and any future
                    # trade of the new ticker started from a zero-cost
                    # pool with no relationship to the source's basis.
                    target_symbol = (tx.symbol_new or '').strip()
                    if target_symbol and target_symbol != symbol:
                        if not is_other_scope:
                            existing = global_pools.get(target_symbol)
                            if existing is None:
                                global_pools[target_symbol] = pool
                            else:
                                # Pool already exists for the new ticker
                                # — merge (cost + qty add, taint sticks
                                # if either side was tainted). Currency
                                # mix would be an upstream bug; guard it.
                                if existing['currency'] and pool['currency'] \
                                        and existing['currency'] != pool['currency']:
                                    raise ValueError(
                                        f"SPLIT {symbol}→{target_symbol}: "
                                        f"currency mismatch "
                                        f"{pool['currency']!r} vs "
                                        f"{existing['currency']!r}. Run "
                                        f"taxjson-convert-currency first."
                                    )
                                if (existing['qty'] * pool['qty']
                                        < -1e-9):
                                    # Merging a LONG pool into a SHORT
                                    # one (or vice versa) is a close-out
                                    # event, not an addition — blind
                                    # summing turned short-sale proceeds
                                    # into phantom COST (FUZZ #F10).
                                    # Refuse loudly rather than corrupt.
                                    raise ValueError(
                                        f"SPLIT {symbol}→{target_symbol}"
                                        f": rename would merge a "
                                        f"{'LONG' if pool['qty'] > 0 else 'SHORT'}"
                                        f" position into an existing "
                                        f"{'LONG' if existing['qty'] > 0 else 'SHORT'}"
                                        f" pool — that is a close-out, "
                                        f"which this engine does not "
                                        f"net automatically. Record the "
                                        f"close explicitly (e.g. a .tt "
                                        f"BUYSELL) before the rename.")
                                existing['qty'] += pool['qty']
                                existing['total_cost'] += pool['total_cost']
                                existing['deferred_wash'] = (
                                    existing.get('deferred_wash', 0.0)
                                    + pool.get('deferred_wash', 0.0))
                                existing['grants'] = sorted(
                                    existing.get('grants', [])
                                    + pool.get('grants', []),
                                    key=lambda _l: _l.get('seq', 0))
                                # Parked flat-pool deferral dollars ride
                                # the rename too — dropping them here
                                # silently erased the denied loss's
                                # future recovery (2026-09 adversarial
                                # audit). Apply signed by the TARGET
                                # pool's direction when it has one;
                                # else keep parking.
                                _src_pw = pool.pop('pending_wash', 0.0)
                                if _src_pw > 1e-9:
                                    if existing['qty'] > 1e-6:  # cov: a2-1596-rename-pending-wash
                                        existing['total_cost'] += D(
                                            _src_pw)
                                    elif existing['qty'] < -1e-6:
                                        existing['total_cost'] += D(
                                            -_src_pw)
                                    else:
                                        existing['pending_wash'] = (
                                            existing.get(
                                                'pending_wash', 0.0)
                                            + _src_pw)
                                existing['tainted'] = (
                                    existing.get('tainted', False)
                                    or pool.get('tainted', False)
                                )
                                # Keep the EARLIER acquisition date when
                                # merging — CRA s. 85.1(5) rollover
                                # inherits the source's holding period
                                # onto the target. Picking max would
                                # pessimize the days-held column and
                                # could push a long-term holding into
                                # short-term territory under the wash-
                                # sale 30-day window.
                                #
                                # Skip the source's date if the source
                                # pool was auto-created with no prior
                                # trades — its `last_acq_date` is still
                                # the dataclass sentinel ('1970-01-01')
                                # and would overwrite the target's real
                                # acquisition date, producing wildly
                                # wrong days-held on later sales.
                                SENTINEL = '1970-01-01'
                                if (pool['qty'] != 0 and pool['last_acq_date'] != SENTINEL
                                        and pool['last_acq_date'] < existing['last_acq_date']):
                                    existing['last_acq_date'] = pool['last_acq_date']
                                    existing['last_acq_settle'] = pool.get(
                                        'last_acq_settle')
                                if not existing['currency']:
                                    existing['currency'] = pool['currency']
                                # Preserve the EARLIEST position_start
                                # across the merged pools — the combined
                                # position's continuous-holding date is
                                # whichever side was entered first.
                                src_psd = pool.get('position_start_date')
                                ex_psd = existing.get('position_start_date')
                                if src_psd and (not ex_psd or src_psd < ex_psd):
                                    existing['position_start_date'] = src_psd
                            del global_pools[symbol]
                        if trace:
                            if target_symbol not in symbol_acb_traces:
                                symbol_acb_traces[target_symbol] = [
                                    f"# --- ACB CALCULATION TRACE: {target_symbol} ---"
                                ]
                            symbol_acb_traces[target_symbol].append(
                                f"# {tx.date} SPLIT-RENAME {symbol}→{target_symbol} "
                                f"ratio={qty:.6g} | pool moved to new ticker"
                            )
                elif action == 'OPENING_BALANCE':
                    # Missing-history opening: pre-data-window shares with unknown
                    # ACB. Quantity is added at cost=0 (won't be trusted —
                    # the pool is marked tainted, so any disposition while
                    # missing-history shares remain gets suppressed from gains).
                    # When the pool later drains to zero, taint clears and
                    # subsequent buys form a fresh, fully-known ACB pool.
                    if not is_other_scope:
                        if pool['position_start_date'] is None:
                            # OB seeds the position-start when the pool
                            # was empty. Position_start is approximate
                            # for tainted pools (the real entry is
                            # pre-data-window), but the OB date is the
                            # best signal available.
                            pool['position_start_date'] = tx.date
                        pool['qty'] += qty
                        # No cost added — taint flag tracks the unknown.
                        pool['tainted'] = True
                        if (qty < 0 and _grant_mode
                                and is_option_symbol(symbol)):
                            # Missing-history short contracts hold a FIFO place
                            # (never recognised — their write predates
                            # the data).
                            _short_lot_open(pool, tx, abs(qty), 0.0, False)
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        symbol_acb_traces[symbol].append(f"# {tx.date} OPENING_BALANCE {qty:10.4f} | Missing history (bought before the data) — pool TAINTED until drain to zero")
                else:
                    # Only an exact zero is skipped (audit R1-24 /
                    # R1-245): the 1e-6 share epsilon dropped every
                    # sub-micro crypto row — staking rewards whose
                    # income was booked lost their units and cost, and
                    # the pool fell short of the wash walk's balance.
                    if qty == 0: continue
                    # A coin pool's residue is real property (S069-13):
                    # per-asset tolerance (pool_qty_eps).
                    _qeps = pool_qty_eps(symbol, pool['qty'], qty)
                    is_opening = (pool['qty'] > _qeps and qty > 0) or \
                                 (pool['qty'] < -_qeps and qty < 0) or \
                                 (abs(pool['qty']) <= _qeps)

                    if is_other_scope:
                        # Sheltered (RRSP/TFSA/LIRA/RESP) and affiliated
                        # (spouse / controlled-corp / affiliated trust) shares
                        # do NOT enter the taxable ACB pool — registered
                        # accounts are separate property per CRA, and
                        # affiliated-party trades belong on the other
                        # person's return. The wash-sale walk still sees
                        # them via current_tx_list, so the 30-day "still
                        # held" balance and ITA 54 substitution-property
                        # rule fire correctly.
                        pass
                    else:
                        if is_opening:
                            # BUY (or Short opening)
                            effective_cost = _trade_money(tx) + (internal_adj if qty > 0 else -internal_adj)
                            pool['total_cost'] += D(effective_cost)
                            _pw = pool.pop('pending_wash', 0.0)
                            if _pw > 1e-9:
                                # Deferred superficial-loss dollars
                                # parked while the pool was flat attach
                                # to this fresh position: raise a long
                                # position's ACB / lower a short's
                                # entry proceeds, so the next
                                # disposition recovers the loss.
                                pool['total_cost'] += D(
                                    _pw if qty > 0 else -_pw)
                            # Seed the position-start on the trade that
                            # re-opens the pool (cleared at the last
                            # drain-to-zero). Subsequent same-direction
                            # buys don't reset it — the position is a
                            # continuous run.
                            if pool['position_start_date'] is None:
                                pool['position_start_date'] = tx.date
                            pool['qty'] += qty
                            pool['last_acq_date'] = tx.date
                            # The s.54 window runs on settle dates
                            # (CA-SL-01): the planning views measure
                            # from this one (A2-0958). last_acq_date
                            # stays the TRADE date (days held).
                            pool['last_acq_settle'] = get_sort_date(tx)

                            if qty < 0 and _grant_mode and is_option_symbol(symbol):
                                _open_short_option(pool, tx, abs(qty),
                                                   effective_cost, 1.0)

                            if trace:
                                if symbol not in symbol_acb_traces:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                fee_amt = _effective_fee_for_trace(tx)
                                pool_cost_f = float(pool['total_cost'])
                                acb_sh = _per_share(pool_cost_f, pool['qty'], symbol)
                                symbol_acb_traces[symbol].append(f"# {tx.date} {tx.action} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Cost_Added: {effective_cost:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
                        else:
                            # SELL (or Short covering) — divide in exact arithmetic.
                            if abs(pool['qty']) > _qeps:
                                # SIGNED per-unit basis: total/|qty|,
                                # not abs(total/qty) (FUZZ #F11). A
                                # short pool whose opening proceeds
                                # went NEGATIVE under a large wash
                                # deferral (legitimate: deferred loss
                                # can exceed the re-short's premium)
                                # was silently flipped positive — the
                                # final cover booked +3500 instead of
                                # recovering the -4500.
                                avg_cost_unit_d = (pool['total_cost']
                                                   / D(abs(pool['qty'])))
                            else:
                                avg_cost_unit_d = Decimal(0)
                            closing_qty = min(abs(qty), abs(pool['qty']))
                            _recognized = 0.0
                            _grant_units_closed = 0.0
                            _grant_closed = None
                            if pool['qty'] < 0 and pool.get('grants'):
                                # Short option lots (grant timing): FIFO
                                # by write; recognised lots leave at
                                # their own premium (see _short_lot_close).
                                (cost_basis, _recognized,
                                 _grant_units_closed, _grant_closed) = \
                                    _short_lot_close(pool, closing_qty,
                                                     is_option_assign)
                            else:
                                cost_basis = float(D(closing_qty) * avg_cost_unit_d)
                            
                            # Apportion adjustment
                            chunk_adj = internal_adj * (closing_qty / abs(qty))
                            proceeds = _trade_money(tx) * (closing_qty / abs(qty))
                            effective_proceeds = proceeds + (chunk_adj if pool['qty'] < 0 else -chunk_adj)
                            
                            gain = (effective_proceeds - cost_basis) if pool['qty'] > 0 else (cost_basis - effective_proceeds)

                            # Grant-timing units closed here: the premium
                            # already recognised on the write date comes
                            # out of this record, so a buy-back is the
                            # loss of the amount paid and an expiry is
                            # zero. A stock-settled ASSIGN retracted the
                            # grant record instead (_short_lot_close) and
                            # folds the full premium into the share leg.
                            _rec_gain = gain - _recognized
                            _rec_cost = cost_basis - _recognized
                            _suppress_record = (_grant_units_closed >= closing_qty - 1e-9
                                                and closing_qty > 1e-9
                                                and abs(_rec_gain) < 0.005
                                                and abs(effective_proceeds) < 0.005)

                            # Days held
                            try:
                                acq_dt = datetime.strptime(pool['last_acq_date'], '%Y-%m-%d')
                                disp_dt = datetime.strptime(tx.date, '%Y-%m-%d')
                                raw_days = (disp_dt - acq_dt).days
                                if raw_days < 0:
                                    print(
                                        f"warning: negative days_held for {tx.symbol} "
                                        f"on {tx.date} (last_acq={pool['last_acq_date']}); "
                                        f"this usually indicates out-of-order transactions",
                                        file=sys.stderr,
                                    )
                                days_held = max(0, raw_days)
                            except ValueError: days_held = 0
                            
                            # Check for taxable event (if tx is in the main transactions list)
                            is_taxable = tx.id in taxable_ids
                            
                            if is_taxable and not is_option_assign and not _suppress_record:
                                # Check for virtual disallowance for this specific ID
                                disallowance_tx = next((v for v in final_virtual_txs if v.action == 'DISALLOW' and v.id == tx.id), None)
                                if disallowance_tx:
                                    disallowed_amt = disallowance_tx.net_amount
                                
                                realized_pl = _rec_gain
                                
                                rg_trace = []
                                if trace:
                                    if symbol not in symbol_acb_traces:
                                        symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                    fee_amt = _effective_fee_for_trace(tx)
                                    rg_trace = list(symbol_acb_traces[symbol])
                                    # The BOOKED figures (audit S069-10):
                                    # grant-timed units already recognised
                                    # their premium at the write, so the
                                    # record carries cost minus that
                                    # premium; the trace foots to it.
                                    gain_sh = _per_share(_rec_gain, abs(qty), symbol)
                                    _prem_note = (f" | Premium_Recognized_At_Write: {_recognized:10.4f}"
                                                  if abs(_recognized) > 1e-9 else "")
                                    rg_trace.append(f"# {tx.date} {tx.action} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Proceeds: {effective_proceeds:10.4f} | Cost_Basis: {_rec_cost:10.4f} | Gain: {_rec_gain:10.4f} | Gain/Sh: {gain_sh:7.4f}{_prem_note}")
                                
                                # Apportion the sell tx's commission/fee to this
                                # gain by the closing qty share. Matches the
                                # convention sum-gains uses to roll up
                                # per-trade fees.
                                tx_qty_abs = abs(qty) if abs(qty) > 1e-9 else 1.0
                                fee_share = closing_qty / tx_qty_abs
                                # Tainted dispositions consume missing-history shares (pool
                                # has an OPENING_BALANCE that hasn't drained yet).
                                # The 'gain' value is computed against cost=0 and
                                # is bogus by construction. Surface tainted=True
                                # so the report can split them into the
                                # "manual reporting required" section.
                                is_tainted = pool.get('tainted', False)
                                iteration_realized_gains.append({
                                    'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                                    'date_settle': tx.date_settle or tx.date,
                                    'gain': _rec_gain,
                                    'qty': closing_qty, 'cost': _rec_cost, 'proceeds': effective_proceeds,
                                    'disallowed': disallowed_amt, 'taxable_gain': _rec_gain + disallowed_amt,
                                    'days_held': days_held, 'account': account,
                                    'currency': tx.currency,
                                    'commission': float(tx.commission or 0) * fee_share,
                                    'fee': float(tx.fee or 0) * fee_share,
                                    'direction': 'LONG' if pool['qty'] > 0 else 'SHORT',
                                    'tainted': is_tainted,
                                    'trace': rg_trace,
                                    # {} when the close consumed only
                                    # close-timing lots; absent when the
                                    # pool kept no lots (a long close).
                                    **({'grant_closed': _grant_closed}
                                       if _grant_closed is not None
                                       else {}),
                                })
                                
                                # Tainted losses never feed the superficial-
                                # loss solver: they're computed against a
                                # missing-history zero-cost pool and are bogus by
                                # construction. Letting them through spawned
                                # DISALLOW/ADJUST virtual rows whose ACB bump
                                # could land on CLEAN lots after the taint
                                # cleared (drain-to-zero), corrupting clean
                                # gains — and fabricated wash_sales records
                                # flowed into total_disallowed. The tainted
                                # disposition itself is already excluded from
                                # the gains report (manual_reporting_required).
                                # A buy-back loss on a written option (any
                                # timing: grant-timed units, a pre-since
                                # transition lot, a close-timing project —
                                # audit R1-270/R1-297) is fed
                                # to the superficial-loss solver only when the
                                # project opts in: s.54 needs "a loss from the
                                # disposition of a property" and a closing
                                # purchase disposes of nothing; CRA has no
                                # published position applying the rule to it,
                                # and the strict reading denies the loss for
                                # good when a registered account holds the
                                # same series (a same-minute order
                                # correction could lose the whole loss).
                                _wash_eligible = (option_buyback_loss_superficial
                                                  or not (pool['qty'] < 0
                                                          and is_option_symbol(symbol)))
                                # Every RAW loss re-enters the solver on every
                                # pass (a fully denied one too), so each pass
                                # shares the replacements out from scratch.
                                if _rec_gain < -0.001 and not is_tainted and _wash_eligible:
                                    iteration_losses.append({
                                        'tx': tx, 'loss_amount': abs(_rec_gain),
                                        'qty': closing_qty, 'direction': 'LONG' if pool['qty'] > 0 else 'SHORT'
                                    })
                            
                            if is_option_assign:
                                underlying = _assign_underlying(tx)
                                if underlying:
                                    pending_adjustments.stage(
                                        tx, underlying, -gain)

                            pool['total_cost'] -= D(cost_basis)
                            # Deferred-wash dollars are part of the ACB,
                            # so a partial close releases them in the
                            # same proportion (a full drain releases
                            # all — they were recovered in this gain).
                            _pre_abs = abs(pool['qty'])
                            if _pre_abs > _qeps:
                                pool['deferred_wash'] = (
                                    pool.get('deferred_wash', 0.0)
                                    * max(0.0, 1.0 - closing_qty
                                          / _pre_abs))
                            pool['qty'] += (closing_qty if pool['qty'] < 0 else -closing_qty)

                            if trace:
                                if symbol not in symbol_acb_traces:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                fee_amt = _effective_fee_for_trace(tx)
                                pool_cost_f = float(pool['total_cost'])
                                pool_acb_sh = _per_share(pool_cost_f, pool['qty'], symbol)
                                symbol_acb_traces[symbol].append(f"# {tx.date} {tx.action} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Cost_Rmvd: {cost_basis:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {pool_acb_sh:7.4f}")

                            # Handle leftover if it crosses zero — this is a fresh
                            # position in the opposite direction, so last_acq_date resets.
                            # Apportion any remaining internal_adj (e.g. an option-
                            # premium roll-in) across the new opening, matching
                            # tt_gains.pl:325-330 which apportions chunk_adj per
                            # chunk's qty share.
                            leftover = abs(qty) - closing_qty
                            if leftover > _qeps:
                                leftover_ratio = leftover / abs(qty)
                                leftover_adj = internal_adj * leftover_ratio
                                eff_cost_leftover = (
                                    _trade_money(tx) * leftover_ratio
                                    + (leftover_adj if qty > 0 else -leftover_adj)
                                )
                                pool['qty'] = (leftover if qty > 0 else -leftover)
                                pool['total_cost'] = D(eff_cost_leftover)
                                pool['deferred_wash'] = 0.0
                                pool['last_acq_date'] = tx.date
                                pool['last_acq_settle'] = get_sort_date(tx)
                                # Position flipped direction (long→short
                                # or vice versa via a cross-zero SELL).
                                # New position begins at this trade.
                                pool['position_start_date'] = tx.date
                                pool['grants'] = []
                                if (qty < 0 and _grant_mode
                                        and is_option_symbol(symbol)):
                                    # The short leftover of a sell that
                                    # crosses zero is a write like any
                                    # other: a lot (and, under grant
                                    # timing, a grant record) of its own.
                                    _open_short_option(
                                        pool, tx, leftover,
                                        eff_cost_leftover, leftover_ratio)


                                if trace:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                    fee_amt_leftover = _effective_fee_for_trace(tx) * leftover_ratio
                                    pool_cost_f = float(pool['total_cost'])
                                    acb_sh = _per_share(pool_cost_f, pool['qty'], symbol)
                                    symbol_acb_traces[symbol].append(f"# {tx.date} {tx.action} {pool['qty']:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt_leftover:6.4f} | Cost_Added: {eff_cost_leftover:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")

                if trace:
                    fee_sh = _per_share(tx.commission + tx.fee, abs(tx.quantity), symbol)
                    price_fee = tx.price + (fee_sh if tx.quantity > 0 else -fee_sh)
                    pool_cost_f = float(pool['total_cost'])
                    acb_sh = abs(_per_share(pool_cost_f, pool['qty'], symbol))
                    realized_pl_str = f"{realized_pl:10.2f}" if realized_pl is not None else " " * 10
                    disallowed_amt_str = f"{disallowed_amt:10.2f}" if disallowed_amt != 0 else " " * 10
                    trace_line = f"# {account:<26} | {note:<12} | {tx.date} {tx.time} | {symbol:<26} | {action:<8} | {qty:10.4f} | {tx.price:10.4f} | {fee_sh:10.4f} | {price_fee:10.4f} | {tx.net_amount:10.2f} | {adjustment_shown:10.2f} | {pool['qty']:11.4f} | {pool_cost_f:10.2f} | {acb_sh:10.4f} | {realized_pl_str} | {disallowed_amt_str} | {trigger_info}"
                    iteration_trace.append(trace_line)

                if abs(pool['qty']) < pool_qty_eps(symbol, _pre_qty, qty):
                    pool['qty'] = 0.0
                    # Pool drained — clear position_start_date so the
                    # next open seeds a fresh start. Critical for the
                    # "opened, closed, reopened" pattern: the user
                    # wants the date of the SECOND open, not the first.
                    pool['position_start_date'] = None
                    pool['grants'] = []
                    # Missing-history shares are fully drained — the pool re-cleans.
                    # Subsequent buys form a fresh, fully-known ACB.
                    if pool.get('tainted', False):
                        pool['tainted'] = False
                        if trace:
                            if symbol not in symbol_acb_traces:
                                symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                            symbol_acb_traces[symbol].append(f"# {tx.date} pool drained to zero — TAINT CLEARED")
                    # Only wipe cost if it's effectively zero (to allow adjustments to persist)
                    if abs(pool['total_cost']) < Decimal('0.001'):
                        pool['total_cost'] = Decimal(0)
                        if trace and symbol in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]

                # Snapshot taxable-pool state after this tx (sheltered txs
                # don't move the pool, so they inherit the prior snapshot).
                # Used by the wash-window renderer for the pool_qty / acb/sh
                # columns. Per-iteration so the converged state wins.
                pool_snapshots_this_iter[tx.id] = {
                    'pool_qty': pool['qty'],
                    'pool_acb': float(pool['total_cost']),
                }

            # Grant records fully retracted by an assignment (s.49(3)/(3.1))
            # leave the record list; their losses leave the solver.
            if _grant_mode:
                iteration_realized_gains = [
                    g for g in iteration_realized_gains
                    if not g.get('_void')]
                iteration_losses = [
                    l for l in iteration_losses if not l.get('_void')]
                for g in iteration_realized_gains:
                    g.pop('_void', None)

            # --- Detection ---
            found_new_wash_sale = False
            # Running balance per (account, alias) BEFORE each tx —
            # used to split a buy into its COVERING portion (closes a
            # short: not replacement property) and its OPENING portion
            # (acquires/extends: the only §54/§1091-relevant part).
            # FUZZ #B: a short-covering buy was accepted as a LONG
            # loss's trigger, so the +ADJUST landed on a SHORT pool and
            # inflated its opening proceeds — a deferred LOSS became a
            # phantom GAIN with no warning.
            _bal_before: Dict[str, float] = {}
            _running: Dict[tuple, float] = {}
            # The main pass's own processing order: its fixed rungs, then
            # the export's row order at one moment (CA-DATE-14; accounts
            # in taxjson.toml order). Every same-moment question below —
            # which loss claims a shared replacement first, whether a rebuy
            # listed after the loss sale is acquired after it, which of two
            # same-moment triggers takes the bump — is answered by this
            # order, never by the rows' content-hash id or account label
            # (audit A2-0059/0551/0058/0193/0192/0961/0965: a one-cent
            # price change used to move a denial).
            _pos = {id(_t): _i for _i, _t in enumerate(current_tx_list)}
            # A class-share option root names its class line (RCI for
            # RCI.B.TO — CA-SL-05; A2-0015/0016).
            _cls_root = class_root_aliases(t.symbol for t in all_txs)

            def _call_und(sym):
                u = parse_option_underlying(sym)
                return _cls_root.get(u.upper(), u) if u else u

            def _pkey(t):
                return (_ev_key(t), _pos.get(id(t), -1))
            # Rename chain for ADJUST placement (FUZZ #F8): a wash
            # ADJUST keyed to the trigger's TRADE-TIME symbol landed on
            # a pool the rename had already deleted — the deferred loss
            # stranded on an invisible empty pool and a later sale of
            # the new symbol used the un-bumped basis.
            _renames: Dict[str, tuple] = {}
            for _t in current_tx_list:
                if _t.action == 'SPLIT':
                    _new_sym = (_t.symbol_new or '').strip()
                    if _new_sym and _new_sym != _t.symbol:
                        _renames[_t.symbol] = (_ev_key(_t), _new_sym)

            def _symbol_asof(sym: str, hi_key, lo_key=None) -> str:
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
                while sym in _renames and sym not in seen_syms:
                    r_key, r_new = _renames[sym]
                    if (lo_key is None or lo_key < r_key) and r_key <= hi_key:
                        seen_syms.add(sym)
                        sym = r_new
                    else:
                        break
                return sym
            # Phase-aware ordering (round-six settle-straddle fuzzer):
            # the naive (date, time, id) sort placed a settle-lagged
            # row AFTER a same-date SPLIT, so its pre-split-denominated
            # quantity was applied to an already-scaled balance —
            # phantom shares in the per-account running position, and
            # the cover-vs-opening trigger gate misfired in BOTH
            # directions (spurious denials and masked real triggers).
            # Every other balance walk already orders through the
            # phase ladder; this one must too.
            # No content-hash rung (audit S069-16): current_tx_list is
            # already in main-pass order, so the stable sort keeps the
            # main pass's tie order (its fixed rungs, then the export's
            # row order — CA-DATE-14) — a one-cent price change used to flip
            # which of two same-moment rows came first here, and with it
            # a superficial-loss denial.
            for _t in sorted(current_tx_list,
                             key=lambda x: event_sort_key(
                                 x, profile='ca_balance',
                                 date_of=get_sort_date)):
                # Keyed by (holder, RAW symbol): a rename-split
                # scales/moves only the named symbol's shares (in
                # every account — the event is corporate-wide), never
                # target-symbol shares acquired pre-rename. The holder
                # of every TAXABLE row is the taxpayer's one s.47 pool
                # (all taxable accounts of a blended pass together — a
                # sale in account B draws on A's shares there, so B's
                # rebuy is an acquisition, not a cover: audit S069-15);
                # sheltered and affiliated rows keep their own account.
                _k = ((_t.account
                       if (_t.id in sheltered_ids
                           or _t.id in affiliated_ids)
                       else '\x00taxable'), _t.symbol)
                _bal_before[_t.id] = _running.get(_k, 0.0)
                if _t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                 'OPENING_BALANCE'):
                    _running[_k] = _running.get(_k, 0.0) + _t.quantity
                elif _t.action == 'SPLIT':
                    _dst_sym = (getattr(_t, 'symbol_new', '')
                                or '').strip() or _t.symbol
                    for _kk in list(_running):
                        if _kk[1] != _t.symbol:
                            continue
                        _moved = _running.pop(_kk) * _t.quantity
                        _dk = (_kk[0], _dst_sym)
                        _running[_dk] = _running.get(_dk, 0.0) + _moved

            def _opening_qty(t) -> float:
                """The portion of buy `t` that OPENS/extends a long
                position (vs covering a short), from the running balance
                of its holder. Only a long acquisition replaces (s.54;
                CA-SL-07), so there is no short-side variant (A2-1596:
                its branch was never reached)."""
                bal = _bal_before.get(t.id, 0.0)
                q = t.quantity
                covering = min(q, max(0.0, -bal))
                return max(0.0, q - covering)
            # Skip wash-sale detection entirely when disabled. The main loop
            # has already produced realized gains assuming no disallowance,
            # so converging on iteration 1 with no virtual txs is correct.
            iteration_losses_to_check = [] if not detect_wash_sales else iteration_losses
            # CRA's formula is applied PER SALE (CA-SL-08, owner decision
            # on A2-0167): denied units = least of (units sold, units
            # acquired in the window, units held at day 30), each sale on
            # its own, so the same held unit may back the denials of two
            # sales. Only the fills of ONE sale (disposition_groups: one
            # account's same-day sell-down) share the units: the sale's
            # ledger holds what its earlier fills claimed per holder (in
            # loss-date units: one day, one symbol) and per trigger (in
            # the trigger's own units, so no row is allocated twice),
            # taken in processing order, so every pass agrees.
            _disp_of = disposition_groups(current_tx_list)
            # The last LOSING fill of each sale: a pre-loss bump lands
            # after it, so the sale's losing fills are costed alike and
            # the formula's denial is not carried out by one of them (a
            # later sale at a gain, even the same day, still sees it).
            # The placement only ever moves LATER across passes
            # (_disp_last_seen): a fill priced just above the ACB is a
            # loss when costed after the bump and a gain before it, and
            # moving the bump back and forth with it never converged
            # (A2-1596).
            _disp_last: Dict[int, Any] = {}
            for _l in sorted(iteration_losses_to_check,
                             key=lambda l: _pkey(l['tx'])):
                _disp_last[_disp_of.get(id(_l['tx']), id(_l['tx']))] = _l['tx']
            for _g, _t in _disp_last_seen.items():
                if _g in _disp_last and _pkey(_t) > _pkey(_disp_last[_g]):
                    _disp_last[_g] = _t
            _disp_last_seen.update(_disp_last)
            # A sale's loss units (S of the formula): its losing fills.
            _grp_units: Dict[int, float] = {}
            for _l in iteration_losses_to_check:
                _g = _disp_of.get(id(_l['tx']), id(_l['tx']))
                _grp_units[_g] = _grp_units.get(_g, 0.0) + _l['qty']
            _ledgers: Dict[int, tuple] = {}
            _trg_used: Dict[str, float] = {}
            _grp_claim: Dict[Any, float] = {}
            _grp_state: Dict[str, float] = {}
            for loss in sorted(iteration_losses_to_check,
                               key=lambda l: _pkey(l['tx'])):
                tx = loss['tx']
                _gid = _disp_of.get(id(tx), id(tx))
                _trg_used, _grp_claim, _grp_state = _ledgers.setdefault(
                    _gid, ({}, {}, {}))
                # SETTLEMENT-date basis for the whole ±30-day window (CRA's
                # disposition timing; matches get_sort_date and the config's
                # tax_date="settle" default). Mixing trade-date detection
                # with the settle-date balance walk below let a disallowance
                # flip near T+1/T+2 window edges — a rebuy at trade-day +30
                # but settle-day +32 was admitted as a trigger the balance
                # walk then excluded.
                loss_date = datetime.strptime(get_sort_date(tx), '%Y-%m-%d')
                # Bridge SPLIT-renames: a post-rename RGLD.US buy in the
                # 30-day window after an SSL.TO loss is a candidate
                # trigger under CRA's substantially-identical rule. Match
                # on equivalence-class representative instead of raw
                # symbol. `alias_of` is identity for symbols never
                # renamed, so this is a no-op for the common case.
                loss_alias = row_cls(tx)
                potential_triggers = []
                for t in all_txs:
                    if row_cls(t) != loss_alias:
                        continue
                    # The loss row itself is a candidate only when it is
                    # a cover that also OPENS a long (buy 150 while short
                    # 100): its 50 new shares are identical property
                    # acquired in the window (audit R1-29 — booked as
                    # one row the loss was allowed, as two rows denied).
                    # _opening_qty keeps just that opening portion.
                    if t.id == tx.id and not (t is tx and tx.quantity > 0
                                              and loss.get('direction')
                                              == 'SHORT'):
                        continue
                    # Only real acquisitions trigger a superficial loss.
                    # SPLIT carries positive `quantity` (the ratio) and
                    # was being misclassified as a long-side buy — a
                    # 16-for-1 reorg on the same ticker post-loss could
                    # silently disallow the entire loss. Same exclusion
                    # for TRANSFER (informational), OPENING_BALANCE
                    # (synthetic), and the bookkeeping actions.
                    if t.action not in ('BUYSELL', 'ASSIGN'):
                        continue
                    t_date = datetime.strptime(get_sort_date(t), '%Y-%m-%d')
                    if abs((t_date - loss_date).days) <= 30:
                        if t.quantity > 0:
                            # Only the OPENING portion is replacement
                            # property; a pure cover/close is not a
                            # trigger (FUZZ #B).
                            if _opening_qty(t) > pool_qty_eps(
                                    t.symbol, t.quantity):
                                if getattr(t, 'type', '') \
                                        == 'transfer_rewrite':
                                    raise AmbiguousTransferDateError(
                                        f"{t.symbol}: a TRANSFER-in "
                                        f"dated {t.date} (account "
                                        f"{t.account}, qty "
                                        f"{t.quantity:g}) sits inside "
                                        f"the +/-30-day window of the "
                                        f"{tx.date} loss sale, but its "
                                        f"date is a broker ARRIVAL "
                                        f"date that may not be an "
                                        f"acquisition. If this was a "
                                        f"custody/account move, add a "
                                        f"counter-TRANSFER to a .tt "
                                        f"file in the same account "
                                        f"(TRANSFER {t.date} 09:30:00 "
                                        f"{t.symbol} {-t.quantity:g} "
                                        f"<cur> <price> <total> "
                                        f"DECLARED — the trailing "
                                        f"DECLARED token marks a "
                                        f"deliberate declaration) "
                                        f"so the pair nets out, plus a "
                                        f"BUYSELL at the TRUE original "
                                        f"acquisition date to keep the "
                                        f"balance"
                                        + (f" — or the one-line form: "
                                           f"ACQUIRED <true-date> "
                                           f"09:30:00 {t.symbol} "
                                           f"{t.quantity:.8g} <cur> "
                                           f"<price> <total> ARRIVED "
                                           f"{t.date}"
                                           if t.quantity > 0 else "")
                                        + f"; if it was a genuine "
                                        f"in-kind contribution, record "
                                        f"it as a BUYSELL dated the "
                                        f"contribution day instead.")
                                potential_triggers.append(t)
                # ITA s.54, closing words para (i): "a right to acquire
                # a property ... is deemed to be a property that is
                # identical to the property". A LONG CALL on the loss
                # shares, opened inside the window and still owned at
                # day 30, is substituted property for a loss on LONG
                # shares (its contract size per contract: 100, or the
                # declared size of a mini — A2-0049). A futures option
                # is never sized as units here: it is flagged for a
                # manual check (CA-SL-15; A2-0014/0056). A class-share
                # root counts for its class line. Deliberately one-way
                # (user policy, 2026-09-29): shares never replace an
                # option, and a different option series never replaces
                # an option — an option's own loss washes only against
                # the identical contract (same symbol, above).
                call_triggers = []
                if (loss.get('direction', 'LONG') == 'LONG'
                        and not is_option_symbol(tx.symbol)):
                    for t in all_txs:
                        if (t.id == tx.id or t.action != 'BUYSELL'
                                or t.quantity <= 0
                                or parse_option_right(t.symbol) != 'C'):
                            continue
                        _und = _call_und(t.symbol)
                        if (not _und or _FUTURES_PREFIX_RE.match(_und)
                                or split_timeline.class_at(_und, t.date) != loss_alias):
                            continue
                        t_date = datetime.strptime(get_sort_date(t), '%Y-%m-%d')
                        if abs((t_date - loss_date).days) > 30:
                            continue
                        if _opening_qty(t) <= 1e-6:
                            continue          # a buy-to-close acquires nothing
                        call_triggers.append(t)
                potential_triggers.extend(call_triggers)
                _call_ids = {t.id for t in call_triggers}
                _call_syms = {t.symbol for t in call_triggers}
                if not potential_triggers: continue

                end_window_date = (loss_date + timedelta(days=30)).strftime('%Y-%m-%d')

                # Never compare share quantities denominated at different
                # dates OR different raw symbols: a SPLIT inside the window
                # leaves balances in day-+30 units while loss['qty'] /
                # trigger quantities are in their own trade-date units —
                # min() across them corrupted disallowed_qty (a 1-for-10
                # reverse split shrank a $500 disallowance to $50). Every
                # quantity is therefore normalized to LOSS-DATE units of
                # the LOSS SYMBOL at the point it enters a sum, via a
                # PER-RAW-SYMBOL lineage factor (settle-basis dates; one
                # shared definition in lib/corporate_timeline.py). The old
                # class-wide alias_factor pooled every ratio of the rename
                # class, so a rename-split A->B into a symbol that ALREADY
                # TRADES divided native-B balances — which the event never
                # scaled — by the rename ratio too (2026-09 adversarial
                # audit, finding 1: $2,000 of superficial loss denied as
                # $1,800). Because each row converts independently, the
                # walks no longer need per-symbol fold dicts: SPLIT rows
                # contribute nothing and rename re-keying is implicit in
                # the lineage path.
                loss_sort = get_sort_date(tx)
                # The loss row itself pre-exists loss_sort when it
                # settles later than it trades: the ladder processed it
                # in the pre-existing phase, BEFORE a same-date SPLIT,
                # so loss qty / per-share loss are in pre-split units
                # and the ref side of every conversion must apply a
                # split dated exactly loss_sort (round-four finding 3).
                loss_pre = bool(tx.date and tx.date < loss_sort)

                def _row_loss_units(t, q: float) -> float:
                    # Convert q — denominated in t's RAW symbol at t's
                    # sort date — into loss-date loss-symbol units.
                    # Boundary matches the ca_main phase ladder: rows
                    # that PRE-EXIST their sort date (OPENING_BALANCE,
                    # settle-lagged executions) are scaled by a
                    # same-date SPLIT; rows executed that day are not.
                    d = get_sort_date(t)
                    pre = (t.action == 'OPENING_BALANCE'
                           or bool(t.date and t.date < d))
                    return q * split_timeline.lineage_factor(
                        t.symbol, d, tx.symbol, loss_sort,
                        from_inclusive=pre, ref_inclusive=loss_pre)

                def _call_units(t, q: float) -> float:
                    # q contracts of a call on the loss shares, in
                    # loss-date loss-symbol SHARE units (the contract
                    # size per contract, through the underlying's split
                    # lineage).
                    d = get_sort_date(t)
                    pre = bool(t.date and t.date < d)
                    return q * option_contract_size(t) * split_timeline.lineage_factor(
                        _call_und(t.symbol), d, tx.symbol,
                        loss_sort, from_inclusive=pre, ref_inclusive=loss_pre)

                def _avail_native(t) -> float:
                    return max(0.0, _opening_qty(t)
                               - _trg_used.get(t.id, 0.0))

                # A contract that expires before day 30 is not owned at
                # day 30, with or without an expiry row in the export
                # (audit S071-17: a missing EXP row parked a realized
                # long-option loss on a contract that can never be sold).
                def _gone_by_end(t) -> bool:
                    return (is_option_symbol(t.symbol)
                            and (parse_option_expiry(t.symbol) or '9999')
                            < end_window_date)

                bal_at_end = sum(
                    _row_loss_units(t, t.quantity)
                    for t in current_tx_list
                    if row_cls(t) == loss_alias
                    and not _gone_by_end(t)
                    and get_sort_date(t) <= end_window_date
                    # OPENING_BALANCE counts — missing-history shares are held
                    # (see running_bal_by_tx walk above).
                    and t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                     'OPENING_BALANCE'))

                # ITA s.54: the trigger must be an ACQUISITION of identical
                # property still OWNED at the end of the window. A new
                # sell-to-open (short sale or written option) acquires
                # nothing, so the US §1091(e) "re-short" branch does not
                # apply here: every loss uses the LONG criteria — opening
                # long acquisitions, positive balance at day 30.
                #
                # "Owns the SUBSTITUTED property" — the property acquired
                # in the window — is measured PER HOLDER: the taxable
                # book (one s.47 pool across the taxpayer's taxable
                # accounts) and each registered / affiliated account on
                # its own. A holder backs a denial only with units it
                # ACQUIRED inside the window and still holds at its end:
                # min(acquired in window, balance at end) — FIFO, since
                # a later sale disposes of the oldest units first. Units
                # a registered account held BEFORE the window neither
                # create nor back a denial: a class-wide balance let an
                # RRSP's 2020 shares turn a taxable rebuy that was sold
                # again inside the window into a PERMANENT denial (2026-09
                # engine and real-data audits: AMD, ENPH, XTD shapes).
                def _holder(t):
                    if t.id in sheltered_ids or t.id in affiliated_ids:
                        return ('other', t.account)
                    return ('taxable',)

                def _holder_rank(t):
                    return (2 if t.id in affiliated_ids
                            else 1 if t.id in sheltered_ids else 0)
                _bal_end_h: Dict[Any, float] = {}
                for t in current_tx_list:
                    if (row_cls(t) == loss_alias
                            and not _gone_by_end(t)
                            and get_sort_date(t) <= end_window_date
                            and t.action in ('BUYSELL', 'ASSIGN',
                                             'TRANSFER',
                                             'OPENING_BALANCE')):
                        _h = _holder(t)
                        _bal_end_h[_h] = (_bal_end_h.get(_h, 0.0)
                                          + _row_loss_units(t, t.quantity))
                # Each holder backs this sale with min(acquired in the
                # window, held at day 30), less what an earlier fill of
                # the same sale already claimed from it (CA-SL-08). A
                # unit another sale claimed still backs this one.
                _acq_h: Dict[Any, float] = {}
                for t in potential_triggers:
                    if t.id in _call_ids:
                        continue
                    _h = _holder(t)
                    _acq_h[_h] = (_acq_h.get(_h, 0.0)
                                  + _row_loss_units(t, _opening_qty(t)))
                _held_h = {h: max(0.0, min(a, _bal_end_h.get(h, 0.0))
                                  - _grp_claim.get(h, 0.0))
                           for h, a in _acq_h.items()}
                # Calls back a denial per (holder, contract): units of
                # THAT series opened in the window and still held at day
                # 30 — a contract bought, sold and bought again counts
                # once (A2-0057/0198). Share balances never back a call
                # and vice versa.
                if call_triggers:
                    _call_acq: Dict[Any, float] = {}
                    for t in call_triggers:
                        _k = ('call', _holder(t), t.symbol)
                        _call_acq[_k] = (_call_acq.get(_k, 0.0)
                                         + _call_units(t, _opening_qty(t)))
                    _call_end: Dict[Any, float] = {}
                    # A call that expires before day 30 is not held at
                    # day 30, with or without an expiry row (S071-15).
                    _expired = {sym for sym in _call_syms
                                if (parse_option_expiry(sym) or '9999')
                                < end_window_date}
                    for t in current_tx_list:
                        if (t.symbol in _call_syms
                                and t.symbol not in _expired
                                and get_sort_date(t) <= end_window_date
                                and t.action in ('BUYSELL', 'ASSIGN',
                                                 'TRANSFER',
                                                 'OPENING_BALANCE')):
                            _k = ('call', _holder(t), t.symbol)
                            _call_end[_k] = (_call_end.get(_k, 0.0)
                                             + _call_units(t, t.quantity))
                    for _k, a in _call_acq.items():
                        _held_h[_k] = max(0.0, min(a, _call_end.get(_k, 0.0))
                                          - _grp_claim.get(_k, 0.0))
                held_substituted = sum(_held_h.values())
                # The sale's formula, min(S, P, B) / S, shared pro rata
                # by its fills: each fill's denied units are its units x
                # that fraction, so the result never depends on the
                # fills' order or prices (the first fill sees the whole
                # backing; later fills draw what is left of it).
                if 'frac' not in _grp_state:
                    _S = _grp_units.get(_gid, loss['qty'])
                    _grp_state['frac'] = (min(1.0, held_substituted / _S)
                                          if _S > 1e-12 else 1.0)
                _want = min(loss['qty'] * _grp_state['frac'],
                            held_substituted)
                # Zero is the pool's own tolerance: 1e-6 for shares, float
                # noise for a coin — a 0.0000009 BTC rebuy the pool keeps
                # as a holding backs a denial too (CA-CRYPTO-09/CA-SL-13;
                # A2-0552).
                if _want > pool_qty_eps(tx.symbol, loss['qty']):
                    disallowed_qty = _want
                    disallowed_amt = disallowed_qty * (loss['loss_amount'] / loss['qty'])

                    # --- Allocation across ALL in-window triggers ---
                    # FUZZ #D: routing the ENTIRE disallowance to one
                    # trigger made the answer depend on rebuy ORDER: a
                    # taxable+sheltered window either recovered the
                    # sheltered-attributed portion (tax understated) or
                    # permanently lost the taxable-attributed portion
                    # (overstated). Allocate pro-rata in acquisition
                    # order — post-loss buys first (primary
                    # replacements), then pre-loss buys latest-first —
                    # each capped at its OPENING quantity in loss-date
                    # units AND at its holder's still-held substituted
                    # units. Sheltered/affiliated portions are
                    # PERMANENT (their ADJUST is scoped out of the
                    # taxable pool); taxable portions defer as ACB
                    # (s.53(1)(f): the taxable holder's cap is its
                    # still-held balance, so every deferral is backed).
                    # Pre/post-loss is decided by the SAME key the
                    # pool replays with (ca_main phase ladder), not by
                    # (settle date, clock time): a trigger traded the
                    # day before at a later clock time that settles on
                    # the loss's settle date is PRE-EXISTING there and
                    # sits in the pool the loss draws from. Classified
                    # post-loss, its ADJUST landed before the loss sale
                    # and inflated the very loss it deferred — a
                    # feedback the solver amplified or never converged
                    # on (2026-09 engine audit, r08 / r08b).
                    _loss_key = _ev_key(tx)
                    # Processing order, so a rebuy listed after a
                    # same-moment loss sale is a purchase AFTER it
                    # (CA-DATE-14 / CA-SL-10; A2-0058).
                    _loss_pkey = _pkey(tx)
                    post_loss = [t for t in potential_triggers
                                 if _pkey(t) > _loss_pkey]
                    pre_loss = [t for t in potential_triggers
                                if t not in post_loss]
                    # Triggers at the SAME moment: the taxpayer's own
                    # (taxable) acquisition first, then registered, then
                    # affiliated accounts; within one rank, the main
                    # pass's processing order (export row order, accounts
                    # in taxjson.toml order — CA-DATE-14) — never the
                    # rows' content-hash id or the account label, which
                    # let a one-cent change flip a deferral into a
                    # permanent denial or move the bump between a call
                    # and the shares (audits S018-06, A2-0192/0961/0965).
                    # Rationale for the rank: the denial is permanent
                    # only for property an affiliated person acquires
                    # (s.40(2)(g)(i)); with no order between the two
                    # acquisitions the taxpayer's own substituted
                    # property is the one s.53(1)(f) reaches first.
                    # Post-loss: earliest first; pre-loss: latest first
                    # (the last-listed of a same-moment group is the
                    # latest acquisition).
                    ordered = (sorted(post_loss,
                                      key=lambda x: (_ev_key(x),
                                                     _holder_rank(x),
                                                     _pos.get(id(x), -1)))
                               + sorted(pre_loss,
                                        key=lambda x: (_ev_key(x),
                                                       -_holder_rank(x),
                                                       _pos.get(id(x), -1)),
                                        reverse=True))
                    per_share_loss = loss['loss_amount'] / loss['qty']
                    allocations = []      # (trigger, qty, amount)
                    perm_amt = 0.0
                    _rem = disallowed_qty
                    _cap_left = dict(_held_h)
                    for trg in ordered:
                        _hh = _holder(trg)
                        if trg.id in _call_ids:
                            _h = ('call', _hh, trg.symbol)
                            _per = _call_units(trg, 1.0)
                        else:
                            _h = _hh
                            _per = _row_loss_units(trg, 1.0)
                        cap = min(_per * _avail_native(trg),
                                  _cap_left.get(_h, 0.0))
                        take = min(_rem, cap)
                        if take > 1e-9:
                            amt = take * per_share_loss
                            allocations.append((trg, take, amt))
                            _trg_used[trg.id] = (_trg_used.get(trg.id, 0.0)
                                                 + (take / _per if _per
                                                    else 0.0))
                            _grp_claim[_h] = _grp_claim.get(_h, 0.0) + take
                            if _hh != ('taxable',):
                                perm_amt += amt
                            _rem -= take
                            _cap_left[_h] = _cap_left.get(_h, 0.0) - take
                        if _rem <= 1e-9:
                            break

                    # Route each kept (deferred) bump onto a pool that
                    # actually HOLDS substituted property at +30. The
                    # class-level _backed test above can be satisfied
                    # entirely by shares in a SIBLING pool of the alias
                    # class (loss realized on S0 while the still-held
                    # backing sits in S2, pre-merge): landing the
                    # ADJUST on the trigger's own — possibly empty —
                    # pool parked the deferral where nothing would ever
                    # consume it, and the denied loss silently left
                    # conservation (fuzz seed 2183, 2026-09 round-four
                    # audit). Greedy: a trigger whose own pool holds
                    # the deferred quantity keeps its pool; otherwise
                    # the bump lands on the class pool with the most
                    # remaining still-held backing.
                    _pool_back: Dict[str, float] = {}
                    _pool_repr: Dict[str, Any] = {}
                    # A key after every row of the window's last day.
                    _eow_key = (end_window_date, 99, '99:99:99', 99)
                    for t in current_tx_list:
                        if (row_cls(t) == loss_alias
                                and get_sort_date(t) <= end_window_date
                                and t.id not in sheltered_ids
                                and t.id not in affiliated_ids
                                and t.action in ('BUYSELL', 'ASSIGN',
                                                 'TRANSFER',
                                                 'OPENING_BALANCE')):
                            _k = _symbol_asof(t.symbol, _eow_key,
                                              _ev_key(t))
                            _pool_back[_k] = (_pool_back.get(_k, 0.0)
                                              + _row_loss_units(
                                                  t, t.quantity))
                            _pool_repr.setdefault(_k, t)
                    _lsign = 1.0                     # replacement is always a LONG holding (s.54)
                    _back_left = {k: max(0.0, _lsign * v)
                                  for k, v in _pool_back.items()}
                    _adjust_land: Dict[str, str] = {}
                    for trg, take, _amt in allocations:
                        if (trg.id in sheltered_ids
                                or trg.id in affiliated_ids):
                            continue      # moot pools; leave in place
                        if trg.id in _call_ids:
                            continue      # the bump lands on the call itself
                        _own = _symbol_asof(trg.symbol, _eow_key,
                                            _ev_key(trg))
                        if _back_left.get(_own, 0.0) >= take - 1e-9:
                            _back_left[_own] -= take
                            continue      # own pool holds the backing
                        _best = max(_back_left,
                                    key=lambda k: _back_left[k],
                                    default=None)
                        if (_best is not None
                                and _back_left.get(_best, 0.0) > 1e-9):
                            _back_left[_best] -= take
                            _adjust_land[trg.id] = _pool_repr[_best]  # a row of that pool
                        # No positive pool anywhere: keep the default
                        # landing — _defer_room already converted the
                        # unbacked portion to a permanent denial.

                    def _mk_adjust(trg, amt):
                        a_id = f"WASH_{tx.id}__{trg.id}"
                        _pre_loss = _pkey(trg) < _loss_pkey
                        if _pre_loss:
                            # Pre-loss trigger: the bump lands just
                            # after the loss sale — same settle date and
                            # phase (same trade date), one second later.
                            a_date, a_settle = tx.date, tx.date_settle
                            # Loader accepts time='' — don't crash the
                            # engine on it (2026-09 audit).
                            a_time = (datetime.strptime(
                                          tx.time or '09:30:00',
                                          '%H:%M:%S')
                                      + timedelta(seconds=1)
                                      ).strftime('%H:%M:%S')
                            if a_time == '00:00:00':
                                a_time = '23:59:59'
                        else:
                            a_date, a_settle = trg.date, trg.date_settle
                            a_time = trg.time
                        # Sign follows the loss side (post-FUZZ-#B every
                        # trigger OPENS on that side, so the proxy is
                        # sound); symbol keyed to the trigger's pool AS
                        # OF the adjust date — following renames so the
                        # bump lands on the LIVE pool (FUZZ #F8).
                        a_amt = amt                  # bump the LONG replacement's ACB (s.53(1)(f))
                        # Backing-routed landing (see _adjust_land):
                        # the pool that still holds the substituted
                        # property, named as of the adjust date so the
                        # bump enters it while live and rides any later
                        # rename with the pool's own state.
                        _land = _adjust_land.get(trg.id, trg)
                        v = TaxTransaction(action='ADJUST', date=a_date,
                                           time=a_time, symbol=_land.symbol,
                                           currency=tx.currency,
                                           net_amount=a_amt,
                                           account=trg.account, id=a_id,
                                           date_settle=a_settle)
                        v.symbol = _symbol_asof(
                            _land.symbol,
                            _loss_key if _pre_loss else _ev_key(v),
                            _ev_key(_land))
                        # Applied right after its row in the main pass
                        # (see _place_wash_adjusts): a pre-loss bump
                        # after the loss sale — its LAST fill (one sale,
                        # one formula: CA-SL-08), so every later sale
                        # sees it (R1-31); a post-loss bump after the
                        # trigger purchase itself, so a same-moment sale
                        # listed after that purchase sees the bumped ACB
                        # (s.53(1)(f) — the bump is part of the
                        # replacement's cost from its acquisition;
                        # A2-0555). Sorted "cost adjustments last" it
                        # landed after every trade at the trigger's
                        # moment.
                        v._wash_after = (
                            _disp_last.get(_disp_of.get(id(tx)), tx).id
                            if _pre_loss else trg.id)
                        if trg.id in sheltered_ids:
                            sheltered_ids.add(a_id)
                        if trg.id in affiliated_ids:
                            affiliated_ids.add(a_id)
                        adjust_to_trigger[a_id] = trg.id
                        return v

                    if allocations:
                        loss_to_trigger[tx.id] = allocations[0][0].id
                        loss_to_triggers_multi[tx.id] = [
                            trg.id for trg, _q, _a in allocations]

                    # Check if we already have a disallowance for this loss. If
                    # so, update it if the amount has changed (due to basis
                    # adjustments from a previous iteration). This fixes the
                    # "solver stale-mate" where DISALLOW amounts would stay
                    # stuck at their first-iteration values even as the
                    # underlying realized loss drifted.
                    existing_disallow = next((v for v in final_virtual_txs if v.id == tx.id and v.action == 'DISALLOW'), None)
                    if existing_disallow:
                        # A pre-loss bump follows its sale's last losing
                        # fill as it moves (see _disp_last; A2-1596).
                        _moved = False
                        _wa = _disp_last.get(_disp_of.get(id(tx)), tx).id
                        for trg, _q, _amt in allocations:
                            if _pkey(trg) >= _loss_pkey:
                                continue
                            _v = next((v for v in final_virtual_txs
                                       if v.action == 'ADJUST'
                                       and v.id == f"WASH_{tx.id}__{trg.id}"),
                                      None)
                            if (_v is not None
                                    and getattr(_v, '_wash_after', _wa) != _wa):
                                _v._wash_after = _wa
                                _moved = True
                        if _moved:
                            found_new_wash_sale = True
                        if abs(float(existing_disallow.net_amount) - float(disallowed_amt)) < 0.001:
                            continue
                        existing_disallow.net_amount = disallowed_amt
                        existing_disallow.quantity = disallowed_qty
                        # Update the corresponding ADJUST vtx as well.
                        # FULL loss-tx id: 8-hex prefixes collided across losses, so a
                        # solver re-iteration could update the WRONG ADJUST and a
                        # report row could attach another loss's adjustment.
                        adj_id_prefix = f"WASH_{tx.id}__"
                        _new_amts = {f"WASH_{tx.id}__{trg.id}": amt
                                     for trg, _q, amt in allocations}
                        _seen_adj = set()
                        for v in final_virtual_txs:
                            if v.action == 'ADJUST' and v.id.startswith(adj_id_prefix):
                                v.net_amount = _new_amts.get(v.id, 0.0)
                                _seen_adj.add(v.id)
                        for trg, _q, amt in allocations:
                            a_id = f"WASH_{tx.id}__{trg.id}"
                            if a_id not in _seen_adj:
                                final_virtual_txs.append(_mk_adjust(trg, amt))
                        permanent_by_loss[tx.id] = perm_amt
                        found_new_wash_sale = True
                        continue

                    disallow_vtx = TaxTransaction(action='DISALLOW', date=tx.date, time=tx.time, symbol=tx.symbol, currency=tx.currency, net_amount=disallowed_amt, quantity=disallowed_qty, id=tx.id, date_settle=tx.date_settle)

                    adjust_vtxs = [_mk_adjust(trg, amt)
                                   for trg, _q, amt in allocations]
                    permanent_by_loss[tx.id] = perm_amt

                    # Capture the full ±30 day window listing for the trace.
                    # Includes every same-symbol transaction across all
                    # accounts (taxable + sheltered) so the reader can see
                    # which buys were eligible candidates and which were
                    # actually selected as the ACB-bump anchor.
                    window_start_dt = loss_date - timedelta(days=30)
                    window_end_dt = loss_date + timedelta(days=30)
                    win_txs = []
                    seen = set()
                    for t in all_txs:
                        if t.id in seen:
                            continue
                        seen.add(t.id)
                        if t.symbol != tx.symbol and t.id not in _call_ids:
                            continue
                        if t.action in ('DISALLOW', 'ADJUST', 'DIVIDEND', 'TAX', 'INTEREST', 'FEE'):
                            continue
                        try:
                            t_dt = datetime.strptime(t.date, '%Y-%m-%d')
                        except ValueError:
                            continue
                        if not (window_start_dt <= t_dt <= window_end_dt):
                            continue
                        days_from = (t_dt - loss_date).days
                        if t.id == tx.id:
                            role = 'loss_sale'
                        elif t.id in loss_to_triggers_multi.get(tx.id, []):
                            role = 'trigger'
                        elif (t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER')
                              and t.quantity > 0
                              and loss['direction'] == 'LONG'
                              and _opening_qty(t) <= pool_qty_eps(
                                  t.symbol, t.quantity)):
                            # A buy that only closes a short (a written
                            # call bought back) acquires nothing: never
                            # a trigger (audit S069-24 — it read
                            # "eligible").
                            role = 'cover'
                        elif t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER') and t.quantity > 0:
                            role = 'candidate'
                        elif t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER') and t.quantity < 0:
                            role = 'other_sell' if loss['direction'] == 'LONG' else 'other_buy'
                        else:
                            role = 'context'
                        win_txs.append({
                            'tx_id': t.id,
                            'date': t.date,
                            'symbol': t.symbol,
                            'days_from_loss': days_from,
                            'account': t.account,
                            'action': t.action,
                            'qty': float(t.quantity),
                            'price': float(t.price),
                            'sheltered': t.id in sheltered_ids,
                            'affiliated': t.id in affiliated_ids,
                            'role': role,
                            'running_bal': running_bal_by_tx.get(t.id),
                        })
                    # Processing order, so the running balance reads in
                    # the order the main pass applied the rows (A2-0965).
                    _wpos = {t.id: _pos.get(id(t), -1) for t in all_txs}
                    win_txs.sort(key=lambda r: (r['days_from_loss'],
                                                r['date'],
                                                _wpos.get(r['tx_id'], -1)))
                    wash_windows[tx.id] = {
                        'window_start': window_start_dt.strftime('%Y-%m-%d'),
                        'window_end': end_window_date,
                        'loss_date': tx.date,
                        'loss_direction': loss['direction'],
                        'bal_at_end': bal_at_end,
                        'loss_qty': loss['qty'],
                        'disallowed_qty': disallowed_qty,
                        'transactions': win_txs,
                    }
                    final_virtual_txs.extend([disallow_vtx] + adjust_vtxs)
                    found_new_wash_sale = True
                else:
                    # Nothing left to back this loss (an earlier fill of
                    # the same sale took the replacement, or a later pass
                    # moved the balance): withdraw a denial an earlier
                    # pass attached.
                    _stale = next((v for v in final_virtual_txs
                                   if v.id == tx.id and v.action == 'DISALLOW'
                                   and abs(float(v.net_amount)) > 0.001),
                                  None)
                    if _stale is not None:
                        _stale.net_amount = 0.0
                        _stale.quantity = 0.0
                        for v in final_virtual_txs:
                            if (v.action == 'ADJUST'
                                    and v.id.startswith(f"WASH_{tx.id}__")):
                                v.net_amount = 0.0
                        permanent_by_loss[tx.id] = 0.0
                        found_new_wash_sale = True

            if detect_wash_sales:
                # Retract STALE disallowances: basis adjustments from a
                # later iteration can turn a first-iteration loss into a
                # raw gain, but its DISALLOW was still attached
                # unconditionally by id — the final entry then reported
                # taxable = raw gain + phantom disallowance, and
                # wash_sales listed a "superficial loss" on a
                # disposition that is not a loss. Remove the DISALLOW
                # and its ADJUSTs and re-iterate; a pathological
                # oscillation ends at the iteration cap with the loud
                # non-convergence warning rather than a silent wrong
                # number.
                _gain_by_id: Dict[str, float] = {}
                for _rg in iteration_realized_gains:
                    _gain_by_id[_rg['tx_id']] = (
                        _gain_by_id.get(_rg['tx_id'], 0.0) + _rg['gain'])
                for _v in [v for v in final_virtual_txs
                           if v.action == 'DISALLOW'
                           and _gain_by_id.get(v.id, 0.0) >= -0.001]:
                    _pref = f"WASH_{_v.id}__"  # cov: a2-1596-stale-disallow-retract
                    final_virtual_txs[:] = [
                        x for x in final_virtual_txs
                        if x is not _v
                        and not (x.action == 'ADJUST'
                                 and x.id.startswith(_pref))]
                    permanent_by_loss.pop(_v.id, None)
                    loss_to_trigger.pop(_v.id, None)
                    loss_to_triggers_multi.pop(_v.id, None)
                    wash_windows.pop(_v.id, None)
                    found_new_wash_sale = True

            if not found_new_wash_sale:
                final_realized_gains = iteration_realized_gains
                final_global_pools = global_pools
                final_pool_snapshots = pool_snapshots_this_iter

                # Backfill wash-window rows with the final, converged pool
                # state. Earlier iterations may have written different pool
                # values (before later ADJUSTs landed), so this overwrite is
                # important for accuracy.
                for ww in wash_windows.values():
                    for entry in ww.get('transactions', []):
                        snap = final_pool_snapshots.get(entry.get('tx_id', ''))
                        if snap:
                            entry['pool_qty_after'] = snap['pool_qty']
                            entry['pool_acb_after'] = snap['pool_acb']
                            if abs(snap['pool_qty']) > 1e-6:
                                entry['acb_per_share_after'] = _per_share(
                                    snap['pool_acb'], snap['pool_qty'],
                                    entry.get('symbol') or '')
                            else:
                                entry['acb_per_share_after'] = 0.0
                final_wash_sales = []
                for v in final_virtual_txs:
                    if v.action == 'DISALLOW':
                        # ALL of this loss's ADJUST vtxs (a multi-trigger
                        # allocation emits several; solver updates can
                        # leave zeroed stubs). `next()` took the first —
                        # possibly a zeroed one — so adjust_cmd disagreed
                        # with disallow_cmd and replaying the pair
                        # stranded the difference. Present the poolable
                        # (taxable-deferral) total; the permanent/
                        # sheltered share has no ADJUST by design.
                        _adjs = [a for a in final_virtual_txs
                                 if a.action == 'ADJUST'
                                 and a.id.startswith(f"WASH_{v.id}__")
                                 and abs(a.net_amount) > 1e-9
                                 # sheltered/affiliated allocations DO
                                 # get ADJUST vtxs (their ids join
                                 # sheltered_ids/affiliated_ids at
                                 # creation) but that share is a
                                 # permanent denial — presenting it as
                                 # poolable would bump next-year
                                 # taxable ACB by money that never
                                 # defers (re-audit).
                                 and a.id not in sheltered_ids
                                 and a.id not in affiliated_ids]
                        adj = _adjs[0] if _adjs else None
                        adj_total = sum(a.net_amount for a in _adjs)
                        
                        # Gather the trace for this specific wash sale
                        wash_trace = []
                        if trace and v.symbol in symbol_acb_traces:
                            # Add a bit of context: +/- 30 days around the wash sale
                            try:
                                loss_dt = datetime.strptime(v.date, '%Y-%m-%d')
                                window_start = (loss_dt - timedelta(days=30)).strftime('%Y-%m-%d')
                                window_end = (loss_dt + timedelta(days=30)).strftime('%Y-%m-%d')
                                
                                wash_trace.append(f"# --- (D-30 Window Start: {window_start}) ---")
                                # Since symbol_acb_traces[v.symbol] gets reset to just the last segment when pool reaches 0,
                                # we need to trace over iteration_trace to get the full chronological context.
                                for line in iteration_trace:
                                    # Example line: # margin | NOTE | 2024-12-19 ...
                                    match = re.search(r'\|\s+(\d{4}-\d{2}-\d{2})', line)
                                    if match:
                                        line_date = match.group(1)
                                        # Also ensure the symbol matches
                                        if f"| {v.symbol}" in line:
                                            if window_start <= line_date <= window_end:
                                                wash_trace.append(line)
                                wash_trace.append(f"# --- (D+30 Window End: {window_end}) ---")
                                wash_trace.append("# ")
                            except (ValueError, TypeError) as e:
                                # Narrowed from a bare `except Exception:
                                # pass`. The trace context is best-effort
                                # — if a virtual ADJUST row has a
                                # malformed date, we still want the
                                # disallow/adjust output to print. Log
                                # so an unexpected error doesn't
                                # disappear: a missing trace block is
                                # otherwise hard to diagnose.
                                print(f"warning: skipped wash-trace "
                                      f"context for {v.symbol}@{v.date} "
                                      f"({e})", file=sys.stderr)
                        
                        disallow_cmd = f"DISALLOW {v.date} {v.time} {v.symbol} {v.currency} {v.net_amount:.4f}"
                        adjust_cmd = f"ADJUST {adj.date} {adj.time} {adj.symbol} {adj.currency} {adj_total:.4f}" if adj else ""
                        
                        if trace:
                            wash_trace.append(disallow_cmd)
                            if adjust_cmd: wash_trace.append(adjust_cmd)
                        
                        final_wash_sales.append({
                            'loss_tx': v.to_dict(),
                            'loss_tx_id': v.id,
                            'trigger_lot_id': loss_to_trigger.get(v.id, ''),
                            'amount': v.net_amount,
                            'disallowed_amount': v.net_amount,
                            'disallowed_qty': v.quantity,
                            'disallow_cmd': disallow_cmd,
                            'adjust_cmd': adjust_cmd,
                            # Where each s.53(1)(f) addition lands (one
                            # per taxable replacement; a multi-trigger
                            # allocation can name several symbols): the
                            # pool symbol as of the stamp, and `after` =
                            # the loss row when the bump is applied
                            # right after it (_place_wash_adjusts). The
                            # T1135 cost walk replays these (S008-07).
                            'adjusts': [
                                {'id': a.id, 'symbol': a.symbol,
                                 'date': a.date,
                                 'date_settle': a.date_settle or a.date,
                                 'time': a.time,
                                 'amount': a.net_amount,
                                 'account': a.account,
                                 'after': getattr(a, '_wash_after', None)}
                                for a in _adjs],
                            'trace': wash_trace if trace else []
                        })
                solver_converged = True
                break
            # Otherwise (a wash was found or an existing one was updated this
            # iteration) loop again: DISALLOW/ADJUST vtxs were already added to
            # final_virtual_txs inline, so the next pass recomputes against
            # them until the amounts stop changing.

        # If the solver ran out without converging, the last iteration's
        # state never got promoted to the `final_*` vars. Use it as a
        # best-effort fallback and emit a stderr warning so the caller
        # knows the result may be inconsistent.
        if not solver_converged:
            print(
                f"warning: the superficial-loss solver did not converge "
                f"within {solver_iterations_used} iterations — the "
                f"superficial-loss list "
                f"may be incomplete and ACB pools may be inconsistent. "
                f"Check for unusual same-symbol activity in your input.",
                file=sys.stderr,
            )
            # Fall back to the last iteration's state.
            if not final_realized_gains:
                final_realized_gains = iteration_realized_gains
                final_global_pools = global_pools
        
        # Format results
        processed_gains = []
        by_ticker = {}
        for g in final_realized_gains:
            # Signed cash-flow convention for SHORT positions: a sell-to-open
            # is a cash inflow (negative cost) and a buy-to-close is a cash
            # outflow (negative proceeds). This matches tt_gains.pl:401-405
            # so that consumers like taxjson_sum_gains.py can compute
            # `proceeds - cost = signed gain` without direction-aware logic,
            # and so taxjson_ccd_gains.py can filter shorts by `cost < 0`.
            entry_cost = g['cost']
            entry_proceeds = g['proceeds']
            if g['direction'] == 'SHORT':
                entry_cost = -entry_cost
                entry_proceeds = -entry_proceeds
            # Cross-engine schema: emit the same keys the US engine emits so
            # downstream consumers (sum-gains, explain, diff) can rely on
            # field presence without per-engine branching. Canada-specific
            # fields use None / 0.0 / [] for "concept doesn't apply here":
            #   - term: Canadian tax has no ST/LT distinction.
            #   - permanently_disallowed: the share of the denial whose
            #     replacement sits in a sheltered account (lost for good,
            #     CA-SL-09); the rest is deferred via the ACB bump.
            #   - wash_replacements: US-specific shape (basis_bump per rep);
            #     Canada uses wash_trigger + wash_window instead.
            tx_id_full = g['id' if 'id' in g else 'tx_id']
            trigger_id = loss_to_trigger.get(tx_id_full, '')
            gain_entry = {
                'date': g['date'],
                'date_settle': g.get('date_settle', g['date']),
                'symbol': g['symbol'], 'qty': g['qty'],
                'gain': g['taxable_gain'], 'raw_gain': g['gain'],
                'cost': entry_cost, 'proceeds': entry_proceeds,
                'currency': g.get('currency') or '',
                'commission': g.get('commission', 0.0),
                'fee': g.get('fee', 0.0),
                'disallowed_amount': g['disallowed'], 'is_wash_sale': g['disallowed'] > 0.001,
                # Sheltered/affiliated-allocated portion of the denial —
                # lost for good (was hardcoded 0.0: FUZZ #D/#16 audit
                # hole; wash-sales/form-export described a deferral that
                # never existed).
                'permanently_disallowed': round(
                    permanent_by_loss.get(tx_id_full, 0.0), 6)
                if g['disallowed'] > 0.001 else 0.0,
                'replacement_lot_ids': loss_to_triggers_multi.get(
                    tx_id_full, [trigger_id] if trigger_id else []),
                'is_option': is_option_symbol(g['symbol'] or ''),
                'days_held': g['days_held'], 'account': g['account'], 'id': tx_id_full,
                'direction': g['direction'],
                'term': None,
                'wash_replacements': None,
                'tainted': g.get('tainted', False),
                # Grant-timing write records and s.40(3) deemed gains carry
                # a note naming the provision; consumers show it verbatim.
                'note': g.get('note', ''),
                'grant': bool(g.get('grant', False)),
                'deemed': bool(g.get('deemed', False)),
            }
            if g.get('grant_closed') is not None:
                # A buy-back of grant-timed lots: {write year: {units,
                # premium}} of the lots it closed (see _short_lot_close).
                gain_entry['grant_closed'] = {
                    y: {'units': round(v['units'], 9),
                        'premium': round(v['premium'], 6)}
                    for y, v in g['grant_closed'].items()}
            if 'trace' in g:
                gain_entry['trace'] = g['trace']

            # Attach the wash-sale trigger info inline so the trace can
            # explain *why* the loss was disallowed alongside the disposition
            # itself, rather than punting to a separate section. Includes the
            # trigger buy and the ACB-adjust virtual transaction.
            if g['disallowed'] > 0.001:
                trigger_id = loss_to_trigger.get(g['tx_id'])
                trigger_tx = None
                if trigger_id:
                    trigger_tx = next((t for t in all_txs if t.id == trigger_id), None)
                adj_vtx = next(
                    (v for v in final_virtual_txs
                     if v.action == 'ADJUST' and v.id.startswith(f"WASH_{g['tx_id']}__")),
                    None,
                )
                wash_trigger: Dict[str, Any] = {
                    'is_full_disallowance': abs(g['taxable_gain']) < 0.01,
                    'loss_raw_gain': g['gain'],
                    'disallowed_amount': g['disallowed'],
                }
                if trigger_tx is not None:
                    wash_trigger['trigger_tx_id'] = trigger_id
                    wash_trigger['trigger_date'] = trigger_tx.date
                    wash_trigger['trigger_qty'] = trigger_tx.quantity
                    wash_trigger['trigger_price'] = trigger_tx.price
                    wash_trigger['trigger_account'] = trigger_tx.account
                    wash_trigger['trigger_sheltered'] = trigger_id in sheltered_ids
                    wash_trigger['trigger_affiliated'] = trigger_id in affiliated_ids
                if adj_vtx is not None:
                    wash_trigger['adjust_amount'] = adj_vtx.net_amount
                    wash_trigger['adjust_date'] = adj_vtx.date
                gain_entry['wash_trigger'] = wash_trigger
                if g['tx_id'] in wash_windows:
                    gain_entry['wash_window'] = wash_windows[g['tx_id']]

            processed_gains.append(gain_entry)
            # Skip tainted entries from by_ticker totals. They carry
            # fabricated numbers (cost basis = 0 against a missing-history
            # OPENING_BALANCE) and the CLI will route them out of
            # `transactions` into `manual_reporting_required` later.
            # Without this skip, any downstream consumer reading
            # by_ticker (rather than transactions) sees missing-history gains
            # mixed with real ones. The --year-rebuild path in
            # taxjson_gains.py has the same guard; this is the
            # primary-construction parallel.
            if gain_entry.get('tainted'):
                continue
            s = gain_entry['symbol']
            if s not in by_ticker:
                by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
            stats = by_ticker[s]
            stats['total_cost'] += gain_entry['cost']
            stats['total_proceeds'] += gain_entry['proceeds']
            stats['total_gain'] += gain_entry['gain']
            stats['trade_count'] += 1
            stats['hold_days'].append(gain_entry['days_held'])

        for tx in transactions:
            # PIL goes to a dedicated entry below so sum-gains can show it
            # in its own column. Keep it out of the eligible-dividend total
            # so the T5 / T3 dividend lines stay accurate (a payment in
            # lieu is other income, CA-INC-03).
            if tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu':
                pil_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                pil_entry = {
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'time': tx.time, 'symbol': tx.symbol, 'qty': tx.quantity, 'currency': tx.currency,
                    'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0, 'pil': pil_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND_IN_LIEU'
                }
                processed_gains.append(pil_entry)
                s = pil_entry['symbol']
                if s not in by_ticker:
                    by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
                by_ticker[s]['total_pil'] += pil_entry['pil']
                continue

            if (tx.action == 'DIVIDEND' or tx.type == 'dividend') \
               and tx.action != 'DIVIDEND_IN_LIEU':
                # Report the gross dividend (pre-withholding) — that's what
                # T5/T3/1099-DIV expect on the income line. The withheld
                # foreign tax flows separately as TAX records and feeds the
                # foreign tax credit, not the dividend total.
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
                div_entry = {
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'time': tx.time, 'symbol': tx.symbol, 'qty': tx.quantity, 'currency': tx.currency,
                    'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0, 'dividend': div_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND'
                }
                processed_gains.append(div_entry)
                s = div_entry['symbol']
                if s not in by_ticker:
                    by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
                by_ticker[s]['total_div'] += div_entry['dividend']

        # Invariant: every dollar of disallowed loss reported in wash_sales should
        # also appear as the (taxable_gain - raw_gain) bump on the corresponding
        # gain entry. If they diverge, the engine has lost track of a wash sale.
        sum_taxable = sum(g['taxable_gain'] for g in final_realized_gains)
        sum_raw = sum(g['gain'] for g in final_realized_gains)
        sum_disallowed_on_gains = sum(g['disallowed'] for g in final_realized_gains)
        sum_disallowed_on_washes = sum(w['amount'] for w in final_wash_sales)
        if abs((sum_taxable - sum_raw) - sum_disallowed_on_gains) > 0.01:
            print(
                f"warning: gain disallowance invariant broken — "
                f"taxable-raw={sum_taxable - sum_raw:.4f} vs gains-disallowed={sum_disallowed_on_gains:.4f}",
                file=sys.stderr,
            )
        if abs(sum_disallowed_on_gains - sum_disallowed_on_washes) > 0.01:
            print(
                f"warning: wash-sale disallowance invariant broken — "
                f"gains-disallowed={sum_disallowed_on_gains:.4f} vs wash-sales={sum_disallowed_on_washes:.4f}",
                file=sys.stderr,
            )
        _warn_undrained_adjustments(pending_adjustments.undrained(), "canada")

        # Options as replacement property are ENFORCED in the solver
        # above (a long call vs a share loss, s.54 para (i)). A warrant
        # or right, a call on an adjusted series or an option on the
        # same futures contract bought in the window is only NAMED
        # (warn-only, CA-SL-14/-15): what it converts into is not in
        # the books.
        _ca_losses = [{'symbol': g['symbol'], 'date': g.get('date_settle')
                       or g['date'], 'amount': g.get('raw_gain', g['gain']),
                       'id': g.get('id', ''),
                       'direction': g.get('direction', 'LONG')}
                      for g in processed_gains
                      if g.get('raw_gain', g['gain']) < -0.005
                      and not g.get('tainted')]
        option_replacement_warnings: List[Dict[str, Any]] = \
            detect_right_replacement_matches(
                _ca_losses, all_txs, date_of=get_sort_date,
                canonical=alias_of,
                statute_label="ITA s.54 ('a right to acquire')")
        option_replacement_warnings += \
            detect_unresolved_option_replacement_matches(
                _ca_losses, all_txs, date_of=get_sort_date,
                canonical=alias_of,
                statute_label="ITA s.54 ('a right to acquire')")
        if getattr(self, 'emit_replacement_stderr', True):
            # run_gains turns this off and prints the warnings after its
            # year filter (audit S070-04).
            _emit_option_replacement_stderr(option_replacement_warnings,
                                            country='canada')

        # Conservation post-conditions (see the helpers' docstrings).
        _pool_qty: Dict[str, float] = {}
        _seen_pool_objs: set = set()
        for _s, _p in final_global_pools.items():
            if id(_p) in _seen_pool_objs:
                continue                # rename aliases share one object
            _seen_pool_objs.add(id(_p))
            _pool_qty[_s] = _p['qty']
        _verify_share_conservation(
            [t for t in current_tx_list
             if t.id in taxable_ids
             and t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                              'SPLIT')],
            _pool_qty, 'canada',
            zero_ratio_skips=False)     # CA applies a ratio-0 SPLIT blindly
        _warn_stranded_basis(final_global_pools)

        def _recognised_premium(p) -> Dict[str, float]:
            if p['qty'] >= 0:
                return {}
            rp = round(sum(float(l['units']) * float(l['per_unit'])
                           for l in (p.get('grants') or [])
                           if l.get('rec')), 4)
            return {'recognised_premium': rp} if rp > 1e-9 else {}

        return {
            'transactions': processed_gains,
            'by_ticker': by_ticker,
            'wash_sales': final_wash_sales,
            'option_replacement_warnings': option_replacement_warnings,
            'inventory': [
                {
                    'symbol': s,
                    'qty': p['qty'],
                    # Short pools hold the opening proceeds positive
                    # internally, but the repo-wide inventory convention
                    # (holdings export, `list`, USA engine) is NEGATIVE
                    # total_cost for shorts — proceeds credited, so
                    # cost_per_share = total_cost/qty is positive.
                    # FUZZ #21: this used to leak the internal positive
                    # sign, disagreeing with the USA engine and flipping
                    # COST vs COST/SH signs in `list`.
                    'total_cost': (float(p['total_cost'])
                                   if p['qty'] >= 0
                                   else -float(p['total_cost'])),
                    'currency': p.get('currency', ''),
                    # Earliest open of the current run of holding this
                    # symbol. None for pools that drained and didn't
                    # reopen (those don't survive the qty filter
                    # anyway). Pre-data-window pools use the synthetic
                    # OPENING_BALANCE date — approximate but the best
                    # signal available.
                    'position_start_date': p.get('position_start_date'),
                    # Most recent acquisition (any buy, incl. adds to an
                    # old position) — the date the superficial-loss /
                    # wash 30-day window measures from. None when the
                    # pool never saw an in-window buy (sentinel).
                    'last_acq_date': (None
                                      if p.get('last_acq_date')
                                      in (None, '1970-01-01')
                                      else p.get('last_acq_date')),
                    # Canada: the same acquisition on the s.54 window's
                    # own basis (settle dates, CA-SL-01) — what the
                    # harvest TX_ADD / SH_ADD columns measure from
                    # (A2-0958). last_acq_date above is the trade date.
                    'last_acq_settle': (None
                                        if p.get('last_acq_date')
                                        in (None, '1970-01-01')
                                        else p.get('last_acq_settle')
                                        or p.get('last_acq_date')),
                    # Denied superficial losses still parked in this
                    # pool's ACB (deferred; recovered on a clean sale).
                    'deferred_wash': round(
                        float(p.get('deferred_wash', 0.0)), 4),
                    # Written-option lots whose premium was already
                    # recognised at the write (grant timing, s.49(1)):
                    # their buy-back is a loss of the whole amount paid,
                    # so a harvest view must not net this premium
                    # against the buy-back value (2026-09 audit R1-230).
                    **_recognised_premium(p),
                }
                for s, p in final_global_pools.items()
                if abs(p['qty']) > pool_qty_eps(s)
            ],
            'summary': {
                'total_gain': sum_taxable,
                'total_disallowed': sum_disallowed_on_washes,
                'wash_solver_converged': solver_converged,
                'wash_solver_iterations': solver_iterations_used,
            }
        }

class USATaxRules(TaxRules):
    """US capital-gains engine: FIFO matching for long AND short positions
    with IRC §1091 wash-sale handling on both sides.

    Long positions:
    - FIFO sell-to-close matching, gain = proceeds - basis.
    - §1091: disallowed loss adds to basis of replacement BUY (deferred).
    - §1223(3): replacement inherits loss lot's holding-period start.
    - Rev. Rul. 2008-5: replacement in a sheltered account → *permanent*
      disallowance with no basis transfer.

    Short positions:
    - Sell-to-open creates a short lot (qty owed, proceeds received).
    - Buy-to-close pops short lots FIFO. Gain = opening_proceeds - closing_cost.
    - Holding period: stand-alone shorts are SHORT_TERM (the holding
      period of borrowed property is conventionally zero — see §1222).
    - §1091 extends to short sales (Reg §1.1091-1): a loss on closing a
      short followed by a substantially-identical sell-to-open within
      ±30 days disallows the loss and REDUCES the replacement short's
      effective opening proceeds (the short-side analog of basis bump).
    - Sheltered replacement short → permanent disallowance.

    Position flips:
    - A BUY exceeding short inventory closes shorts FIFO; leftover qty
      opens a new long lot.
    - A SELL exceeding long inventory closes longs FIFO; leftover qty
      opens a new short lot.

    Out of scope (v1) — to be added when a use case exists:
    - §1233(b)(1) anti-conversion: substantially identical property
      held NOT more than 1 year at the short sale (or acquired while the
      short is open) → gain on closing the short = SHORT_TERM.
    - §1233(b)(2) that long's holding period restarts when the short
      closes (or the long is sold).
    - §1233(d): substantially identical property held MORE than 1 year
      at the short sale → a loss on closing the short = LONG_TERM.
      (Each needs "substantially identical" reasoning beyond a simple
      symbol match.)
    - §1259 constructive sale of appreciated long when hedged by short.
    - §1091(e)(1): a SALE of substantially identical stock within ±30
      days of a short-cover loss does not disallow it (only a re-short
      registers as a short-side replacement) — tax-logic US-WASH-19.
    - Section 1256 60/40 mark-to-market for futures and broad-based
      index options. (Affects symbols like SPX, NDX, futures.)
    """

    WASH_WINDOW_DAYS = 30

    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, per_account_basis: bool = False) -> Dict[str, Any]:
        _check_engine_allowed("usa")  # test-only guard (lib/country)
        _disambiguate_duplicate_ids(transactions, sheltered_transactions,
                                    affiliated_transactions)
        # §1091 contemplates a narrower "related party" rule than CRA's
        # affiliated-persons test, but the mechanics are the same: an
        # affiliated person's BUY/SELL is treated as a replacement for
        # wash-sale purposes. We additively merge both pools — passing
        # neither flag yields the baseline single-taxpayer behavior.
        #
        # One corporate split = one application: collapse per-account SPLIT
        # duplicates across all three lists (shared `seen`) before the
        # symbol-keyed inventory/replacement walks see them.
        _seen_splits: set = set()
        transactions = _dedupe_corporate_splits(transactions, _seen_splits)
        sheltered_transactions = _dedupe_corporate_splits(
            sheltered_transactions or [], _seen_splits)
        affiliated_transactions = _dedupe_corporate_splits(
            affiliated_transactions or [], _seen_splits)

        sheltered_ids = {t.id for t in (sheltered_transactions or [])}
        affiliated_ids = {t.id for t in (affiliated_transactions or [])}
        epsilon = 1e-8

        def get_sort_date(tx):
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

        def non_capital(action: str, tx_type: str) -> bool:
            # TRANSFER is handled at the CLI layer (rewritten to BUYSELL
            # for sheltered files, hard-errors out for --taxable).
            return action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
                   or tx_type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee')

        # ASSIGN must be processed before BUYSELL when they share a settle
        # date+time — the option-leg ASSIGN sets up a pending adjustment
        # that the stock-leg BUYSELL then consumes.
        # Ordering centralized in lib/corporate_timeline.event_sort_key,
        # profile 'us_main': (trade date, time, priority) — priority sorts
        # AFTER time here, a deliberate divergence from Canada's phase
        # ladder, pinned in test_event_sort_key.py.

        # Use the module-level OCC parser. Local binding kept for
        # backward compatibility with this scope's existing callers.
        option_underlying = parse_option_underlying

        # Combined timeline so future sheltered openings can match against
        # earlier taxable losses — sheltered matches result in permanent
        # disallowance (Rev. Rul. 2008-5).
        all_events = sorted(
            transactions + (sheltered_transactions or []) + (affiliated_transactions or []),
            key=lambda x: event_sort_key(x, profile='us_main',
                                         date_of=get_sort_date),
        )
        # A lot's acquisition time, by its source row's id (lots carry
        # the id, not the time): the rename merge orders same-date lots
        # by it.
        _lot_time = {t.id: (t.time or '') for t in all_events}
        # Rows below the lot epsilon (1e-8 units) are not booked; they
        # are named once instead of vanishing silently (audit S070-09).
        _us_dust: List[TaxTransaction] = []
        # A lot residue of at most epsilon units folded into the sale
        # that closes the lot, and a sale's excess of at most epsilon
        # over the lots (no position opened): named too (US-CRYPTO-08,
        # re-audit A2-0808 / A2-1485). (symbol, units) per case; float
        # noise (under 1e-11 of the quantities) is not named.
        _us_dust_absorbed: List[Tuple[str, float]] = []
        _us_dust_dropped: List[Tuple[str, float]] = []

        def _dust_noise(*q: float) -> float:
            return 1e-11 * max([1.0] + [abs(x) for x in q])
        # Notes tied to one dated row (stock dividends, an unapplied
        # basis adjustment): printed now by a direct caller, or by
        # run_gains after its year filter (audit A2-0956: a 2023 stock
        # dividend's note landed in every later year's .sum).
        _dated_notes: List[tuple] = []

        def _note(date_: str, text: str) -> None:
            if getattr(self, 'emit_replacement_stderr', True):
                print(text, file=sys.stderr)
            else:
                _dated_notes.append((date_, text))

        # === PRE-PASS: classify each event and build replacement indexes. ===
        # The "opening portion" of each transaction is what's eligible to be
        # a wash-sale replacement. A flip transaction (closes one side, opens
        # the other) contributes only its opening portion.
        long_replacements: Dict[str, List[Dict[str, Any]]] = {}
        short_replacements: Dict[str, List[Dict[str, Any]]] = {}

        def _rep_key(sym: str, on_date: str) -> str:
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
        net_qty_state: Dict[Any, float] = {}

        def _nkey(acct, s):
            # Blended mode: the taxable open/cover partition is per
            # ACCOUNT (an account's sell closes ITS position), matching
            # the per-(account, symbol) FIFO pools in the main pass.
            return (acct, s) if per_account_basis else s
        # Context (sheltered/affiliated) books' running balances, keyed
        # (account, symbol) — partitions an other-scope SELL into its
        # long-close vs short-open portions and an other-scope BUY into
        # its short-cover vs long-open portions, mirroring the taxable
        # partition (Rev. Rul. 2008-5: a sheltered ACQUISITION is
        # replacement-eligible; a buy-to-close acquires nothing).
        other_qty_state: Dict[tuple, float] = {}
        # Per-symbol split timeline (TRADE-basis dates, IRS semantics), ALL
        # scopes — a split is a property of the security, and
        # _dedupe_corporate_splits at entry guarantees one row per event.
        # Replacement records stay denominated in their own trade-date units
        # forever; _rep_units_factor converts between a rep's units and the
        # loss-date's units at match time. Built in all_events order (rename
        # migration is positional); the non_capital filter mirrors this
        # pre-pass loop's own skip.
        split_timeline = SplitTimeline.from_transactions(
            ev for ev in all_events if not non_capital(ev.action, ev.type))
        _warn_ticker_reused_after_rename(
            all_events, lambda ev: ev.date,
            rule_text="the wash-sale rule (US-BASIS-06)")

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
                if ratio:
                    if per_account_basis:
                        for _nk in list(net_qty_state):
                            if _nk[1] == sym:
                                net_qty_state[_nk] *= ratio
                    elif sym in net_qty_state:
                        net_qty_state[sym] *= ratio
                    for _ok in list(other_qty_state):
                        if _ok[1] == sym:
                            other_qty_state[_ok] *= ratio
                # Handle ticker rename (e.g. merger). Migration ensures
                # future BUYSELLs on the new ticker correctly partition
                # into open/close portions against the migrated balance.
                target_symbol = (ev.symbol_new or '').strip()
                if target_symbol and target_symbol != sym:
                    if per_account_basis:
                        for _nk in list(net_qty_state):
                            if _nk[1] == sym:
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
                        if _ok[1] == sym:
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
                    other_qty_state[_ok] = (other_qty_state.get(_ok, 0.0)  # cov: a2-1596-us-other-stock-div
                                            + ev.quantity)
                continue

            if (ev.quantity > 0 and ev.action == 'BUYSELL'
                    and (ev.type or '') in _LOT_EVENT_TYPES):
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
                        print(f"warning: sheltered account "
                              f"{ev.account!r} sells {open_qty:g} "
                              f"{sym} beyond its recorded balance on "
                              f"{ev.date} — missing acquisition "
                              f"history; NOT treated as a §1091 short "
                              f"replacement. Add the missing sheltered "
                              f"buy/transfer rows for exact wash "
                              f"matching.", file=sys.stderr)
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

        # === MAIN PASS STATE ===
        inventory_long: Dict[str, List[Dict[str, Any]]] = {}
        inventory_short: Dict[str, List[Dict[str, Any]]] = {}
        realized_gains: List[Dict[str, Any]] = []
        wash_sale_records: List[Dict[str, Any]] = []
        symbol_traces: Dict[str, List[str]] = {}
        # Track currency per symbol across the full input set (taxable +
        # sheltered + affiliated). The inventory only reports symbols whose
        # taxable lots are still open, but a symbol-keyed lookup means we
        # can attach currency at emission time without re-walking lots.
        # Mirrors the Canada engine's per-pool currency tracking + mismatch
        # guard at line ~286.
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
                    print(f"warning: {_tx.symbol}: context book "
                          f"transaction {_tx.id} on {_tx.date} is in "
                          f"{_tx.currency!r} but the main book uses "
                          f"{existing!r} — context amounts assumed "
                          f"comparable; convert the context file for "
                          f"exact wash amounts.", file=sys.stderr)
                    continue
                raise ValueError(
                    f"Currency mismatch for {_tx.symbol}: previously seen as {existing!r} "
                    f"but {_tx.action} {_tx.id} on {_tx.date} is in {_tx.currency!r}. "
                    + _currency_mismatch_advice(_tx, existing))
            if not existing:
                symbol_currency[_tx.symbol] = _tx.currency
        # When an option closes via ASSIGN/exercise, the premium is rolled
        # into the underlying stock's basis/proceeds rather than recognized
        # as a separate gain (IRS Pub 550). Key: underlying symbol, value:
        # negative of the would-be gain on the option close, matching the
        # Canada engine's sign convention (so SELL adds, BUY subtracts).
        # (The staging ledger itself is built below, once the main
        # pass's sorted stream exists — see _AssignPremiumLedger.)

        def _rep_units_factor(sym: str, from_date: str, to_date: str) -> float:
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
            return split_timeline.factor(
                split_timeline.canonical(sym), from_date, to_date)

        def find_replacements_in_window(rep_list: List[Dict[str, Any]], loss_date_str: str):
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
                loss_dt = datetime.strptime(loss_date_str, '%Y-%m-%d')
            except ValueError:
                return []
            out = []
            for rep in rep_list:
                if rep['remaining_qty'] <= epsilon:
                    continue
                try:
                    rep_dt = datetime.strptime(rep['date'], '%Y-%m-%d')
                except ValueError:
                    continue
                if abs((rep_dt - loss_dt).days) <= self.WASH_WINDOW_DAYS:
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

        # §1091 has no still-held test: a purchase in another of your
        # TAXABLE accounts inside the window replaces the loss even when
        # that account sold the shares before the loss sale (tax-logic
        # US-WASH-22, owner decision on audit A2-0544). Its sold,
        # never-matched shares are kept here per purchase, each with
        # the gain row of the sale that closed them, so a later loss
        # can add its disallowed amount to that sale's basis (the
        # §1091(d) basis of shares no longer held changes their sale).
        _long_rep_of: Dict[str, Dict[str, Any]] = {}
        for _rl in long_replacements.values():
            for _r in _rl:
                if not (_r['is_sheltered'] or _r['is_affiliated']):
                    _long_rep_of.setdefault(_r['tx'].id, _r)

        def _record_sold_replacement(lot, entry, chunk_qty, sale_tx):
            """The sale of a purchase's never-matched shares: kept on the
            purchase's replacement record (US-WASH-22)."""
            rep = _long_rep_of.get(lot.get('id'))
            if (rep is None or lot.get('tainted')
                    or lot.get('wash_deferred', Decimal(0))
                    or not detect_wash_sales):
                return
            _uf = _rep_units_factor(sale_tx.symbol, rep['date'],
                                    sale_tx.date) or 1.0
            rep.setdefault('sold', []).append({
                'entry': entry, 'avail': chunk_qty / _uf,
                'sale_tx': sale_tx})

        def _sold_replacements_in_window(rep_list, loss_tx):
            """[(rep, chunk)] a long loss of `loss_tx` may still match:
            shares of a taxable purchase in ANOTHER account, bought in
            the window and sold before the loss, never matched and not
            sold at a disallowed loss of their own (US-WASH-22)."""
            try:
                loss_dt = datetime.strptime(loss_tx.date, '%Y-%m-%d')
            except ValueError:
                return []
            out = []
            for rep in rep_list:
                if (rep['is_sheltered'] or rep['is_affiliated']
                        or rep['tx'].account == loss_tx.account
                        or not rep.get('sold')):
                    continue
                try:
                    rep_dt = datetime.strptime(rep['date'], '%Y-%m-%d')
                except ValueError:
                    continue
                if not (0 <= (loss_dt - rep_dt).days
                        <= self.WASH_WINDOW_DAYS):
                    continue
                for ch in rep['sold']:
                    e = ch['entry']
                    if (ch['avail'] <= epsilon
                            or ch['sale_tx'].id == loss_tx.id
                            or ch['sale_tx'].account == loss_tx.account
                            or e.get('tainted')):
                        continue
                    if e.get('disallowed_amount', 0.0) > epsilon:
                        # That sale's own loss was disallowed: a basis
                        # add there would need its wash redone — named
                        # for a manual check instead (US-WASH-22).
                        _note(loss_tx.date,
                              f"warning: {loss_tx.symbol}: the {loss_tx.date}"
                              f" loss ({loss_tx.account}) has a "
                              f"replacement bought {rep['date']} in "
                              f"{rep['tx'].account} and sold "
                              f"{e.get('date')} at a loss that was itself "
                              f"disallowed — not matched; check this "
                              f"wash sale by hand (§1091).")
                        ch['avail'] = 0.0
                        continue
                    out.append((rep, ch))
            return out

        def _split_gain_entry(entry, q):
            """Split a gain row so its first `q` units are their own row
            (returned); the rest stays right behind it in the list."""
            if entry['qty'] <= q + epsilon:
                return entry
            frac = q / entry['qty']
            head = dict(entry)
            for k in ('qty', 'cost', 'proceeds', 'gain', 'raw_gain',
                      'commission', 'fee'):
                head[k] = entry[k] * frac
                entry[k] = entry[k] - head[k]
            entry['trace'] = []
            for i_, e_ in enumerate(realized_gains):
                if e_ is entry:
                    realized_gains.insert(i_, head)
                    break
            # The rest keeps the record of unmatched shares; the head
            # takes their place there.
            return head

        def _apply_sold_replacement(rep, ch, match_qty, uf, amt_d,
                                    loss_tx, loss_lot):
            """Add `amt_d` (the disallowed loss of `match_qty` units, in
            loss-date units) to the basis of the matched shares of the
            earlier sale, with the loss shares' holding period tacked on
            (§1091(d), §1223(3))."""
            sale = ch['sale_tx']
            _ufs = _rep_units_factor(sale.symbol, rep['date'],
                                     sale.date) or 1.0
            q_sale = match_qty / uf * _ufs
            entry = _split_gain_entry(ch['entry'], q_sale)
            amt = float(amt_d)
            if 'pre_retro' not in entry:
                entry['pre_retro'] = {
                    k: entry.get(k) for k in ('cost', 'gain', 'raw_gain',
                                              'term', 'days_held')}
            entry['cost'] += amt
            entry['raw_gain'] -= amt
            entry['gain'] -= amt
            _leff = loss_lot.get('effective_acq_date', loss_lot['date'])
            try:
                _prior = (datetime.strptime(loss_tx.date, '%Y-%m-%d')
                          - datetime.strptime(_leff, '%Y-%m-%d'))
                _tack = (datetime.strptime(rep['date'], '%Y-%m-%d')
                         - _prior).strftime('%Y-%m-%d')
                _eff0 = (datetime.strptime(entry['date'], '%Y-%m-%d')
                         - timedelta(days=int(entry.get('days_held') or 0))
                         ).strftime('%Y-%m-%d')
            except ValueError:
                _tack = _eff0 = None
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
            entry.setdefault('wash_basis_added', []).append({
                'amount': amt, 'qty': q_sale,
                'loss_id': loss_tx.id, 'loss_date': loss_tx.date,
                'loss_date_settle': loss_tx.date_settle or loss_tx.date,
                'loss_account': loss_tx.account,
                'term': tacked_term})
            return entry

        # §1091 covers "stock or securities": a commodity or broad-index
        # futures contract (or an option on one) is a §1256 contract,
        # marked to market, usually outside it. Its loss is never denied;
        # a re-purchase in the window is flagged for a manual check
        # (tax-logic US-WASH-18; audit A2-0053 — a re-bought F:CLG7 had
        # its whole 10,000 loss disallowed with no flag). Canada's s.54
        # covers any property and keeps denying.
        _fut_flags: Dict[str, Dict[str, Any]] = {}

        def _outside_1091(sym: str) -> bool:
            if is_option_symbol(sym):
                return bool(_FUTURES_PREFIX_RE.match(
                    parse_option_underlying(sym) or ''))
            return bool(_FUTURES_PREFIX_RE.match(sym or ''))

        def _flag_futures_loss(loss_tx, loss_amt, cands):
            rec = _fut_flags.get(loss_tx.id)
            if rec is None:
                rec = _fut_flags[loss_tx.id] = {
                    'rule': 'futures_vs_loss',
                    'loss_symbol': loss_tx.symbol,
                    'loss_date': loss_tx.date,
                    'loss_amount': 0.0,
                    'loss_id': loss_tx.id,
                    'option_symbol': cands[0]['tx'].symbol,
                    'option_acquired': min(c['date'] for c in cands),
                    'option_qty': sum(c['remaining_qty'] for c in cands),
                    'held_at_window_end': None,
                    'statute': "IRS §1091 ('stock or securities')",
                }
            rec['loss_amount'] = round(rec['loss_amount'] + loss_amt, 2)

        # === MAIN PASS ===
        # Each option ASSIGN's own stock leg is paired by identity in
        # the premium ledger (_pair_assign_legs; see the Canada twin),
        # keyed per (ACCOUNT, symbol) so in a blended combined book one
        # account's leg never absorbs another account's premium.
        _assign_underlying = _make_assign_underlying_resolver(
            transactions, lambda _t: _t.date)
        # (ACCOUNT, underlying) pairs that trade as STOCK in the
        # taxable book — an ASSIGN whose underlying is absent for ITS
        # OWN account is cash-settled; see the Canada twin (OB
        # excluded: an OB-only underlying means the stock leg is
        # missing).
        taxable_stock_symbols = {
            (t.account, t.symbol) for t in transactions
            if not is_option_symbol(t.symbol)
            and t.action in ('BUYSELL', 'ASSIGN')}
        taxable_sorted = sorted(
            transactions,
            key=lambda x: event_sort_key(x, profile='us_main',
                                         date_of=get_sort_date))
        # A leg dated before its option row gets the option moved in
        # front of it (A2-0051/0195).
        _assign_pairs = _pair_assign_legs(
            taxable_sorted, lambda _t: True, _assign_underlying)
        taxable_sorted = _place_assign_options(taxable_sorted,
                                               _assign_pairs)
        pending_option_adjustments = _AssignPremiumLedger(
            taxable_sorted,
            lambda _t: (not is_option_symbol(_t.symbol)
                        and not exercise_target(_t)
                        and _t.action in ('BUYSELL', 'ASSIGN')),
            _assign_pairs)

        def _take_option_adj(tx) -> float:
            if (is_option_symbol(tx.symbol) or exercise_target(tx)
                    or tx.action not in ('BUYSELL', 'ASSIGN')):
                return 0.0
            return pending_option_adjustments.take(tx)

        # One disposition = one sale (or cover) row, or the same-second
        # fills of one order: consecutive BUYSELL rows of one account and
        # symbol, same direction, same date and a real clock time (a
        # date-only or midnight stamp is no evidence of one order). Lots
        # drawn by one disposition are never replacements for each
        # other's losses (tax-logic US-WASH-17; audit A2-0001/A2-0017/
        # A2-0553: a 1-share old lot sold with 100 recent shares washed
        # its loss into the very shares being sold, split them, and
        # cascaded one share at a time — 100,001 Form 8949 W rows).
        _disp_total: Dict[str, float] = {}   # lead row id -> group qty
        _disp_member: set = set()            # non-lead fill ids
        _disp_prev: Dict[Any, tuple] = {}

        def _real_stamp(tm) -> bool:
            return bool(tm) and tm not in ('00:00:00', '00:00')
        for _t in taxable_sorted:
            if non_capital(_t.action, _t.type):
                continue
            _k = (_t.account, _t.symbol)
            if _t.action != 'BUYSELL' or abs(_t.quantity or 0) < epsilon:
                _disp_prev.pop(_k, None)
                continue
            _p = _disp_prev.get(_k)
            if (_p is not None and _real_stamp(_t.time)
                    and _p[0].date == _t.date and _p[0].time == _t.time
                    and (_p[0].quantity > 0) == (_t.quantity > 0)):
                _disp_total[_p[1]] += abs(_t.quantity)
                _disp_member.add(_t.id)
                _disp_prev[_k] = (_t, _p[1])
            else:
                _disp_total[_t.id] = abs(_t.quantity)
                _disp_prev[_k] = (_t, _t.id)

        def _plan_draw(inv, ikey_, sym, q, on_date, ref_key, amt_key):
            """Reserve the lots this disposition draws (FIFO, `q` units):
            the boundary lot is split so the KEPT units are their own lot
            (and the replacement record's ref follows them), and every
            replacement record whose lot is drawn loses that capacity
            now, before any of the disposition's losses is matched."""
            lots = inv.get(ikey_, [])
            reps = (long_replacements if ref_key == 'lot_ref'
                    else short_replacements).get(_rep_key(sym, on_date), [])
            left = q
            i = 0
            while left > epsilon and i < len(lots):
                lot = lots[i]
                take = min(lot['qty'], left)
                if lot['qty'] > take + epsilon:
                    frac = D(take) / D(lot['qty'])
                    keep = dict(lot)
                    keep['qty'] = lot['qty'] - take
                    keep[amt_key] = lot[amt_key] * (1 - frac)
                    _wd = lot.get('wash_deferred', Decimal(0))
                    keep['wash_deferred'] = _wd * (1 - frac)
                    lot['qty'] = take
                    lot[amt_key] = lot[amt_key] - keep[amt_key]
                    lot['wash_deferred'] = _wd - keep['wash_deferred']
                    lots.insert(i + 1, keep)
                    for r in reps:
                        if r.get(ref_key) is lot:
                            r[ref_key] = keep
                    drawn = keep
                else:
                    drawn = lot
                for r in reps:
                    if r.get(ref_key) is drawn:
                        _cuf = _rep_units_factor(sym, r['date'], on_date)
                        r['remaining_qty'] = max(
                            0.0, r['remaining_qty'] - take / (_cuf or 1.0))
                left -= take
                i += 1

        def _plan_disposition(inv, ikey_, tx_, ref_key, amt_key):
            if not detect_wash_sales or tx_.id in _disp_member:
                return
            _plan_draw(inv, ikey_, tx_.symbol,
                       _disp_total.get(tx_.id, abs(tx_.quantity)),
                       tx_.date, ref_key, amt_key)

        # Blended (combined multi-account) mode: FIFO basis pools are
        # per-(account, symbol) — the IRS keys basis per account — while
        # everything symbol-keyed (replacement lists, wash matching,
        # currency, traces) stays cross-account, which is exactly the
        # §1091 scope. Single-book callers (per_account_basis=False)
        # keep plain symbol keys — bit-identical behavior.
        def _ikey(acct, sym):
            return (acct, sym) if per_account_basis else sym

        # §355 spin-offs booked per parent lot (US-CORP-07): the spun-off
        # shares' BUYSELL and the parent's ADJUST of one event and
        # account, joined by corp_event_id. The BUYSELL sorts first at
        # their shared moment and books both; its ADJUST is then skipped.
        _spin_adj: Dict[tuple, TaxTransaction] = {}
        for _t in taxable_sorted:
            if (_t.action == 'ADJUST' and _t.type == SPINOFF_355_TYPE
                    and _t.corp_event_id):
                _spin_adj.setdefault((_t.account, _t.corp_event_id), _t)
        _spin_done: set = set()
        # §356 boot exchanges booked per block (US-CORP-05): the SELL of
        # the old shares (sorted first) leaves its blocks here for the
        # BUY of the new shares, keyed (account, corp_event_id).
        _boot_blocks: Dict[tuple, tuple] = {}
        # Own-account custody moves (US-BASIS-05): each TRANSFER lot_move
        # leg -> its other leg (out of one account, into another: same
        # symbol, moment, quantity and description).
        _move_pair: Dict[str, TaxTransaction] = {}
        _moves_done: set = set()
        _book_accounts = {t.account for t in taxable_sorted}
        _move_ins = [t for t in taxable_sorted
                     if t.action == 'TRANSFER' and t.type == LOT_MOVE_TYPE
                     and (t.quantity or 0.0) > 0]
        for _o in taxable_sorted:
            if not (_o.action == 'TRANSFER' and _o.type == LOT_MOVE_TYPE
                    and (_o.quantity or 0.0) < 0):
                continue
            for _i in _move_ins:
                if (_i.id not in _move_pair and _i.account != _o.account
                        and _i.symbol == _o.symbol and _i.date == _o.date
                        and (_i.time or '') == (_o.time or '')
                        and _i.description == _o.description
                        and abs(_i.quantity + _o.quantity)
                        <= 1e-9 * max(1.0, abs(_i.quantity))):
                    _move_pair[_o.id] = _i
                    _move_pair[_i.id] = _o
                    break

        def _move_long_lots(src, dst, sym, q, on_date):
            """Hand `q` units of `src`'s FIFO lots (the lot objects
            themselves: basis, purchase dates, wash-sale deferrals and
            replacement links travel with them) to `dst`. Returns the
            units moved."""
            lots = inventory_long.get(src, [])
            moved = []
            left = q
            while left > epsilon and lots:
                lot = lots[0]
                if lot['qty'] <= left + epsilon:
                    moved.append(lots.pop(0))
                    left -= lot['qty']
                    continue
                frac = D(left) / D(lot['qty'])
                part = dict(lot)
                part['qty'] = left
                part['cost_basis'] = lot['cost_basis'] * frac
                _wd = lot.get('wash_deferred', Decimal(0))
                part['wash_deferred'] = _wd * frac
                lot['qty'] -= left
                lot['cost_basis'] -= part['cost_basis']
                lot['wash_deferred'] = _wd - part['wash_deferred']
                moved.append(part)
                left = 0.0
            if moved and dst is not None:
                _insert_lots(dst, moved)
            return sum(l['qty'] for l in moved)

        def _draw_long_lots(ikey_, sym, q, on_date):
            """Take `q` units off the front of a long pool (FIFO) with no
            disposition row: [(units, lot fields)] — the boundary lot is
            split. Replacement records lose the drawn shares' capacity,
            as on a sale."""
            out = []
            lots = inventory_long.get(ikey_, [])
            reps = long_replacements.get(_rep_key(sym, on_date), [])
            left = q
            while left > epsilon and lots:
                lot = lots[0]
                if lot['qty'] <= left + epsilon:
                    lots.pop(0)
                    out.append(dict(lot))
                    left -= lot['qty']
                    for r in reps:
                        if r.get('lot_ref') is lot:
                            r['lot_ref'] = None
                            r['remaining_qty'] = 0.0
                    continue
                frac = D(left) / D(lot['qty'])
                part = dict(lot)
                part['qty'] = left
                part['cost_basis'] = lot['cost_basis'] * frac
                _wd = lot.get('wash_deferred', Decimal(0))
                part['wash_deferred'] = _wd * frac
                lot['qty'] -= left
                lot['cost_basis'] -= part['cost_basis']
                lot['wash_deferred'] = _wd - part['wash_deferred']
                for r in reps:
                    if r.get('lot_ref') is lot:
                        _cuf = _rep_units_factor(sym, r['date'], on_date)
                        r['remaining_qty'] = max(
                            0.0, r['remaining_qty'] - left / (_cuf or 1.0))
                out.append(part)
                left = 0.0
            return out

        def _insert_lots(ikey_, new_lots):
            """Carried lots join a pool in acquisition order (their
            actual dates; same-date lots by time, then arrival)."""
            lots = inventory_long.setdefault(ikey_, [])
            lots.extend(new_lots)
            lots.sort(key=lambda l: (l['date'],
                                     _lot_time.get(l.get('id'), '')))

        def _inv_keys_for(inv, sym):
            if per_account_basis:
                return [k for k in inv if k[1] == sym]
            return [sym] if sym in inv else []

        for tx in taxable_sorted:
            symbol = tx.symbol
            ikey = _ikey(tx.account, symbol)
            inventory_long.setdefault(ikey, [])
            inventory_short.setdefault(ikey, [])
            if trace and symbol not in symbol_traces:
                symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} ---"]

            if tx.action == 'TRANSFER' and tx.type == LOT_MOVE_TYPE:
                # A custody move between two of your own taxable
                # accounts: not a sale — the lots keep their basis and
                # purchase dates and go to the receiving account
                # (US-BASIS-05). One leg books the pair.
                if tx.id in _moves_done:
                    continue
                _other = _move_pair.get(tx.id)
                if _other is None:
                    _mm = _MOVE_DESC_RE.match(tx.description or '')
                    _peer = (_mm.group(2) if _mm and tx.quantity < 0
                             else _mm.group(1) if _mm else None)
                    if _peer is None or _peer in _book_accounts:
                        _note(tx.date,
                              f"warning: ATTENTION: own-account move: "
                              f"{symbol} {tx.quantity:+g} in {tx.account} "
                              f"on {tx.date} has no matching leg — not "
                              f"booked.")
                        continue
                    # A book without the other account (one account's
                    # own view): the shares leave the sender with no
                    # disposition; the receiver holds them with their
                    # basis unknown HERE — the blended pass, which has
                    # both accounts, carries the basis (US-BASIS-05).
                    if tx.quantity < 0:
                        _move_long_lots(ikey, None, symbol,
                                        -tx.quantity, tx.date)
                    else:
                        _insert_lots(ikey, [{
                            'qty': tx.quantity,
                            'cost_basis': Decimal(0),
                            'wash_deferred': Decimal(0),
                            'date': tx.date,
                            'effective_acq_date': tx.date,
                            'id': tx.id, 'tainted': True}])
                    _note(tx.date,
                          f"note: {symbol}: {abs(tx.quantity):g} moved "
                          f"{'to' if tx.quantity < 0 else 'from'} {_peer} "
                          f"on {tx.date} (your own account): this book has "
                          f"only {tx.account}, so "
                          + ("the lots leave it with no sale"
                             if tx.quantity < 0 else
                             "their basis is unknown here")
                          + " — the blended pass of `taxjson run` carries "
                            "the basis and purchase dates (US-BASIS-05).")
                    continue
                _moves_done.update((tx.id, _other.id))
                _out, _in = ((tx, _other) if tx.quantity < 0
                             else (_other, tx))
                if not per_account_basis:
                    continue          # one pool: nothing moves
                _q = -_out.quantity
                _got = _move_long_lots(_ikey(_out.account, symbol),
                                       _ikey(_in.account, symbol),
                                       symbol, _q, tx.date)
                if _q - _got > max(epsilon, _dust_noise(_q)):
                    _note(tx.date,
                          f"warning: ATTENTION: own-account move: "
                          f"{symbol}: {_q:g} moved from {_out.account} to "
                          f"{_in.account} on {tx.date}, but {_out.account} "
                          f"held {_got:g} — the other {_q - _got:g} have "
                          f"no purchase in the books (missing history in "
                          f"{_out.account}); {_in.account}'s sales of them "
                          f"read as a short.")
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} OWN MOVE {_got:10.4f} | "
                        f"{_out.account} -> {_in.account}, lots carried")
                continue

            if non_capital(tx.action, tx.type):
                continue

            # ----- SPLIT (handles both classic splits and merger rollovers) -----
            # Mirror of the Canada engine's SPLIT branch (core.py:341).
            # Without this, SPLIT falls through to the default BUY path
            # and the engine would add `ratio` (e.g. 0.0625 for a 1-for-16
            # merger) as new shares at $0 cost — completely wrong.
            if tx.action == 'SPLIT':
                ratio = tx.quantity
                # A zero/falsy ratio skips only the SCALING — the
                # rename migration below must still run. The old
                # `continue` dropped the whole event while the pre-pass
                # and SplitTimeline both honored it, forking the book
                # into a stranded long under the old symbol plus a
                # phantom short under the new one.
                if ratio:
                    # Scale every existing lot's qty by ratio and its
                    # cost-per-share by 1/ratio (so total cost stays put).
                    # Blended mode: a split is a property of the
                    # SECURITY — scale every account's key.
                    for inv in (inventory_long, inventory_short):
                        for _k in _inv_keys_for(inv, symbol):
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
                    for inv in (inventory_long, inventory_short):
                        for _k in _inv_keys_for(inv, symbol):
                            _tk = ((_k[0], target_symbol)
                                   if per_account_basis else target_symbol)
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
                                l['date'], _lot_time.get(l.get('id'), '')))
                    # Wash-sale replacement records need no move: they
                    # are keyed by the dated identity class (_rep_key),
                    # which already joins OLD before the rename to NEW
                    # and is never the renamed-away ticker itself
                    # (A2-1596: the per-symbol move never ran).
                    # Carry the symbol→currency map too — without this,
                    # the currency guard above flags the post-rename
                    # ticker as "unseen" and the per-ticker stats lose
                    # their currency association.
                    if symbol in symbol_currency:
                        symbol_currency.setdefault(target_symbol, symbol_currency[symbol])
                        del symbol_currency[symbol]
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
                if abs(qty) < epsilon:
                    continue
                if qty > 0:
                    inventory_long.setdefault(ikey, []).append({
                        'qty': abs(qty),
                        'cost_basis': Decimal(0),
                        'date': tx.date,
                        'effective_acq_date': tx.date,
                        'id': tx.id,
                        'tainted': True,
                    })
                else:
                    # Short-side lots use a different shape than long
                    # lots: the disposition path reads `proceeds`, not
                    # `cost_basis` (core.py:1378). A naive long-lot
                    # shape here would KeyError on the first BUY-CLOSE.
                    # `effective_open_date` mirrors the live short-open
                    # path's field name.
                    inventory_short.setdefault(ikey, []).append({
                        'qty': abs(qty),
                        'proceeds': Decimal(0),
                        'date': tx.date,
                        'effective_open_date': tx.date,
                        'id': tx.id,
                        'tainted': True,
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
            if tx.action == 'ADJUST' and tx.id in _spin_done:
                continue            # booked with its spin-off (US-CORP-07)
            if tx.action == 'ADJUST':
                lots = inventory_long.get(ikey, [])
                open_qty = sum(l['qty'] for l in lots)
                if open_qty <= epsilon:
                    # Not applied (tax-logic US-ROC-03 / US-ROC-04): a
                    # return of capital with no basis left is a §301(c)(3)
                    # gain to report by hand; a basis INCREASE (a notional
                    # distribution) has no lot to raise. Console-visible
                    # ATTENTION (audit A2-0199), worded by sign (A2-0964:
                    # an increase was called a return of capital).
                    _amt = float(tx.net_amount)
                    _where = ('the position is short'
                              if inventory_short.get(ikey)
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
                    _note(tx.date,
                          f"warning: ATTENTION: unapplied basis adjustment: "
                          f"{symbol} ADJUST of {_amt:+.2f} on {tx.date} "
                          f"found no open long lots ({_where}) — {_what}; "
                          f"the row was NOT applied.")
                    continue
                delta = D(tx.net_amount)
                applied = Decimal(0)
                excess_total = Decimal(0)
                for i, lot in enumerate(lots):
                    if i == len(lots) - 1:
                        # Last lot absorbs the division remainder so the
                        # applied total equals net_amount exactly.
                        share = delta - applied
                    else:
                        share = (delta * D(lot['qty'])) / D(open_qty)
                    lot['cost_basis'] += share
                    applied += share
                    if lot['cost_basis'] < D('-0.005'):
                        # §301(c)(3): the part beyond this lot's basis is
                        # gain now; the basis stays at zero.
                        excess = -lot['cost_basis']
                        lot['cost_basis'] = Decimal(0)
                        excess_total += excess
                        _acq = lot.get('effective_acq_date', lot['date'])
                        try:
                            _held = (datetime.strptime(tx.date, '%Y-%m-%d')
                                     - datetime.strptime(_acq, '%Y-%m-%d')
                                     ).days
                        except ValueError:
                            _held = 0
                        _x = float(excess)
                        realized_gains.append({
                            'date': tx.date,
                            'date_settle': tx.date_settle or tx.date,
                            'symbol': symbol, 'qty': 0.0,
                            'cost': 0.0, 'proceeds': _x,
                            'gain': _x, 'raw_gain': _x,
                            'disallowed_amount': 0.0,
                            'permanently_disallowed': 0.0,
                            'replacement_lot_ids': [],
                            'days_held': max(0, _held),
                            'acquired_date': lot['date'],
                            'account': tx.account,
                            'currency': tx.currency,
                            'commission': 0.0, 'fee': 0.0,
                            'is_wash_sale': False,
                            'is_option': is_option_symbol(symbol),
                            'id': tx.id, 'trace': [],
                            'direction': 'LONG',
                            'term': ('LONG_TERM'
                                     if held_more_than_one_year(_acq,
                                                                tx.date)
                                     else 'SHORT_TERM'),
                            'wash_trigger': None, 'wash_window': None,
                            'wash_replacements': None,
                            'tainted': bool(lot.get('tainted')),
                            'deemed': True,
                            'note': ('DEEMED GAIN — nondividend '
                                     'distribution in excess of basis '
                                     '(§301(c)(3)); basis reset to zero'),
                        })
                if excess_total > D('0.005'):
                    print(
                        f"NOTE: {symbol}: nondividend distribution on "
                        f"{tx.date} exceeded the basis by "
                        f"{float(excess_total):.2f} — booked as capital "
                        f"gain in that year (§301(c)(3)); the basis is "
                        f"zero.", file=sys.stderr)
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} ADJUST   {tx.net_amount:10.4f} | "
                        f"spread pro-rata over {len(lots)} lot(s), "
                        f"{open_qty:.4f} sh")
                continue

            if abs(tx.quantity) < epsilon:
                if tx.quantity and tx.action in ('BUYSELL', 'ASSIGN'):
                    _us_dust.append(tx)
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
                _lots = inventory_long.get(ikey, [])
                _held = sum(l['qty'] for l in _lots)
                if _held > epsilon and not inventory_short.get(ikey):
                    _ratio = (_held + tx.quantity) / _held
                    for _lot in _lots:
                        _lot['qty'] = _lot['qty'] * _ratio
                    _note(tx.date,
                          f"note: {symbol}: stock dividend of "
                          f"{tx.quantity:g} share(s) on {tx.date} — "
                          f"nontaxable (§305(a)): the basis of the "
                          f"{_held:g} share(s) held is spread over old "
                          f"and new (§307) and their purchase dates carry "
                          f"over; if it was taxable (§305(b), e.g. a cash "
                          f"option), enter it by hand.")
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} STOCK DIVIDEND +{tx.quantity:g} "
                            f"sh | spread over {len(_lots)} lot(s), "
                            f"{_held:.4f} sh held")
                    continue
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
                    for _p in taxable_sorted)
                _note(tx.date,
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

            if (tx.action == 'BUYSELL' and tx.quantity > 0
                    and tx.type == SPINOFF_355_TYPE):
                _adj = _spin_adj.get((tx.account, tx.corp_event_id))
                _par = (_adj.symbol if _adj is not None else '')
                _pkey = _ikey(tx.account, _par)
                _plots = [l for l in inventory_long.get(_pkey, [])
                          if l['qty'] > epsilon]
                _pbasis = sum((l['cost_basis'] for l in _plots),
                              Decimal(0))
                _alloc = (-D(_adj.net_amount) if _adj is not None
                          else Decimal(0))
                if (_adj is not None and _plots and _pbasis > 0
                        and _alloc > 0
                        and not inventory_short.get(_pkey)):
                    # Reg. §1.358-2: every parent share gives up the same
                    # fraction of its OWN basis; each parent block gets a
                    # block of spun-off shares with its basis share and
                    # its acquisition date (§1223(1)). Never below zero:
                    # a §355 distribution recognizes no gain.
                    _frac = _alloc / _pbasis
                    _new_total = D(abs(tx.net_amount))
                    if _frac > 1:
                        _note(tx.date,
                              f"warning: ATTENTION: {_par}: the §355 "
                              f"spin-off on {tx.date} allocates "
                              f"{float(_alloc):,.2f} but the parent's "
                              f"basis held is {float(_pbasis):,.2f} — "
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
                              else _new_total * _take / (_pbasis * _frac))
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
                        })
                    _insert_lots(ikey, _new)
                    _spin_done.add(_adj.id)
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} SPIN-OFF  {tx.quantity:10.4f} "
                            f"from {_par} | {len(_new)} block(s), "
                            f"{float(_frac):.6f} of each parent lot's basis")
                    continue
                if _adj is not None:
                    _note(tx.date,
                          f"warning: {symbol}: the §355 spin-off on "
                          f"{tx.date} finds no long {_par} lots with "
                          f"basis in {tx.account} — the spun-off shares "
                          f"are booked as one lot on the spin date and "
                          f"the parent's basis reduction as a plain "
                          f"adjustment (check the parent's history).")

            if (tx.action == 'BUYSELL' and tx.type == REORG_356_TYPE
                    and tx.quantity < 0):
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
                _drawn = _draw_long_lots(ikey, symbol, _q, tx.date)
                _got = sum(c['qty'] for c in _drawn)
                if _q - _got > epsilon:
                    _note(tx.date,
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
                _blocks = []
                for _c in _drawn:
                    _f = D(_c['qty']) / D(_q)
                    _arb, _bb = _ar * _f, _boot * _f
                    _rec = max(Decimal(0), min(_arb - _c['cost_basis'],
                                               _bb))
                    _acq = _c.get('effective_acq_date', _c['date'])
                    _blocks.append((_f, _c['cost_basis'] - _bb + _rec,
                                    _c['date'], _acq,
                                    bool(_c.get('tainted')),
                                    _c.get('wash_deferred', Decimal(0))))
                    if _bb <= 0:
                        continue
                    try:
                        _held = (datetime.strptime(tx.date, '%Y-%m-%d')
                                 - datetime.strptime(_acq, '%Y-%m-%d')
                                 ).days
                    except ValueError:
                        _held = 0
                    realized_gains.append({
                        'date': tx.date,
                        'date_settle': tx.date_settle or tx.date,
                        'symbol': symbol, 'qty': _c['qty'],
                        'cost': float(_bb - _rec),
                        'proceeds': float(_bb),
                        'gain': float(_rec), 'raw_gain': float(_rec),
                        'disallowed_amount': 0.0,
                        'permanently_disallowed': 0.0,
                        'replacement_lot_ids': [],
                        'days_held': max(0, _held),
                        'acquired_date': _c['date'],
                        'account': tx.account,
                        'currency': tx.currency,
                        'commission': 0.0, 'fee': 0.0,
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
                        'reorg_356': True,
                        'note': (f"§356 boot: realized "
                                 f"{float(_arb - _c['cost_basis']):.2f}, "
                                 f"recognized {float(_rec):.2f} "
                                 f"(never a loss)"),
                    })
                _boot_blocks[(tx.account, tx.corp_event_id)] = (
                    _blocks, _ar - _boot)
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} §356 EXCHANGE {_q:10.4f} | "
                        f"{len(_blocks)} block(s), boot {float(_boot):.4f}")
                continue
            if (tx.action == 'BUYSELL' and tx.type == REORG_356_TYPE
                    and tx.quantity > 0):
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
                    _insert_lots(ikey, [{
                        'qty': tx.quantity * float(_f),
                        'cost_basis': _nb * _r,
                        'wash_deferred': _wd * _r if _wd else Decimal(0),
                        'date': _d, 'effective_acq_date': _e,
                        'id': tx.id,
                        **({'tainted': True} if _t else {}),
                    } for _f, _nb, _d, _e, _t, _wd in _blocks])
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} §356 NEW SHARES "
                            f"{tx.quantity:10.4f} | {len(_blocks)} "
                            f"block(s), basis carried")
                    continue
                _note(tx.date,
                      f"warning: ATTENTION: {symbol}: the new shares of "
                      f"the §356 exchange on {tx.date} have no old-share "
                      f"leg in {tx.account} — booked at their value, not "
                      f"the carried basis; check the merger's rows.")

            tx_qty_abs = abs(tx.quantity)
            # A BUY's cost is a magnitude (parsers spell it either sign);
            # a SELL's proceeds stay SIGNED — negative only when the
            # commission exceeds the gross (a $0.01 close), which abs()
            # turned into a credit (audit R1-164; mirrors the Canada
            # engine's _trade_money).
            tx_net = (abs(tx.net_amount) if tx.quantity > 0
                      else float(tx.net_amount))
            if (tx.type or '') == 'futures_settlement':
                # A futures fill on the settlement basis (lib/futures.py):
                # net_amount is the realized P/L, SIGNED (+ received,
                # - paid), 0 on an opening. A sell's proceeds are it; a
                # buy (a short's cover) costs its negation, so a short
                # closed at a profit realizes exactly the P/L — abs()
                # booked a +5,000 cover as a 5,000 cost (partition
                # ENGINE-02; the Canada engine's _trade_money twin).
                tx_net = (float(tx.net_amount) if tx.quantity < 0
                          else -float(tx.net_amount))
            # Commission and fee are split separately on each gain entry
            # (see make_gain_entry below) — apportioned by chunk_qty share.


            is_option_sym = is_option_symbol(symbol)

            def make_gain_entry(*, chunk_qty, chunk_cost, chunk_proceeds, raw_gain, allowed_gain,
                                disallowed_amt, permanently_disallowed_amt, replacement_ids, wash_reps,
                                acq_for_holding, days_held, is_long_term, direction, rg_trace,
                                tainted=False, raw_acq_date=''):
                fee_share = chunk_qty / tx_qty_abs if tx_qty_abs > 0 else 1.0
                total_disallowed = disallowed_amt + permanently_disallowed_amt
                entry = {
                    'date': tx.date,
                    'date_settle': tx.date_settle or tx.date,
                    'symbol': symbol,
                    'qty': chunk_qty,
                    'cost': chunk_cost,
                    'proceeds': chunk_proceeds,
                    'gain': allowed_gain,
                    'raw_gain': raw_gain,
                    'disallowed_amount': total_disallowed,
                    'permanently_disallowed': permanently_disallowed_amt,
                    'replacement_lot_ids': replacement_ids,
                    'days_held': max(0, days_held),
                    # The lot's ACTUAL purchase/open date. Form 8949
                    # column (b) wants this (brokers report the real
                    # date and adjust term separately); days_held stays
                    # tacked for the term computation.
                    'acquired_date': raw_acq_date,
                    'account': tx.account,
                    'currency': tx.currency,
                    'commission': float(tx.commission or 0) * fee_share,
                    'fee': float(tx.fee or 0) * fee_share,
                    'is_wash_sale': total_disallowed > epsilon,
                    'is_option': is_option_sym,
                    'id': tx.id,
                    'trace': rg_trace,
                    'direction': direction,
                    'term': 'LONG_TERM' if is_long_term else 'SHORT_TERM',
                    # Schema symmetry with CanadaTaxRules — these are
                    # Canada-side concepts; US emits None so downstream
                    # consumers can read either engine's output with
                    # .get() and the same field set.
                    'wash_trigger': None,
                    'wash_window': None,
                    'wash_replacements': wash_reps if wash_reps else None,
                    # True when the consumed lot was a missing-history
                    # OPENING_BALANCE (cost basis = 0 from synthesized
                    # `--incomplete-history` rows). The CLI's tainted-
                    # split path routes these to manual_reporting_required
                    # — same end-state as Canada's tainted-pool model.
                    'tainted': bool(tainted),
                }
                return entry

            # Option-leg ASSIGN: the premium rolls into the underlying's
            # basis/proceeds instead of being recognized here. We still pop
            # the position from inventory, but emit no gain entry — the
            # would-be gain is staged in pending_option_adjustments[underlying]
            # and consumed by the stock-leg BUYSELL processed next.
            is_option_assign = (tx.action == 'ASSIGN')
            # A marked warrant/right exercise leg rolls its cost into the
            # shares like a long call's premium (US-OPT-06).
            _assign_und_named = (option_underlying(symbol)
                                 or exercise_target(tx))
            underlying_for_assign = (_assign_underlying(tx)
                                     if is_option_assign
                                     and _assign_und_named
                                     else None)
            if not is_option_assign or not _assign_und_named:
                is_option_assign = False
                underlying_for_assign = None
            elif (underlying_for_assign is None
                  or (tx.account, underlying_for_assign)
                  not in taxable_stock_symbols):
                # Cash-settled assignment/exercise (index options): the
                # underlying never trades as stock in this book, so no
                # stock leg can consume a staged premium — realize the
                # option's own P&L via normal disposition accounting
                # (mirrors the Canada engine, note and all).
                print(f"note: {symbol}: assignment treated as "
                      f"cash-settled ({_assign_und_named} never "
                      f"trades as stock in this book) — option P&L "
                      f"realized directly. If a stock leg is missing "
                      f"from your input, add it and re-run.",
                      file=sys.stderr)
                is_option_assign = False
                underlying_for_assign = None

            # ============================================================
            # BUY path: first close any existing shorts FIFO; if leftover
            # quantity remains, open a new long lot with the remainder.
            # ============================================================
            if tx.quantity > 0:
                qty_remaining = tx.quantity

                # Pop the pending option-assignment premium once, up front.
                # We apportion it across BOTH the buy-to-close chunks (below)
                # and any leftover buy-to-open (further down) so the full
                # premium is consumed even when an assignment crosses zero
                # (a short-put assignment that closes an existing short and
                # opens a new long). Earlier the pop happened only in the
                # leftover branch, silently discarding the close-share.
                # Stored as -gain (Canada convention). Only the marked
                # ASSIGN stock leg may consume it when one exists
                # (mirrors the Canada engine): an unrelated same-symbol
                # trade sorted between the option leg and the stock leg
                # used to hijack the premium.
                option_adj = _take_option_adj(tx)
                if not is_option_assign:
                    _plan_disposition(inventory_short, ikey, tx,
                                      'short_lot_ref', 'proceeds')

                # --- buy-to-close: pop short lots FIFO ---
                while qty_remaining > epsilon and inventory_short[ikey]:
                    short_lot = inventory_short[ikey][0]
                    if short_lot['qty'] <= qty_remaining + epsilon:
                        if (short_lot['qty'] - qty_remaining
                                > _dust_noise(short_lot['qty'])):
                            _us_dust_absorbed.append(
                                (symbol, short_lot['qty'] - qty_remaining))
                        chunk_qty = short_lot['qty']
                        chunk_open_proceeds_d = short_lot['proceeds']
                        inventory_short[ikey].pop(0)
                        # A short_replacement record that pointed at this
                        # lot is consumed: no lot reference (a later match
                        # must not mutate the detached dict) and no
                        # capacity, so find_replacements_in_window skips it
                        # (tax-logic US-WASH-21, re-audit A2-0817).
                        for r in short_replacements.get(_rep_key(symbol, tx.date), []):
                            if r.get('short_lot_ref') is short_lot:
                                r['short_lot_ref'] = None
                                # Covered shares can no longer serve as
                                # §1091 replacement (FUZZ #A): leaving
                                # capacity here matched later losses
                                # against DEAD lots -> permanent denial
                                # of real losses in taxable-only chains.
                                r['remaining_qty'] = 0.0
                    else:
                        chunk_qty = qty_remaining
                        chunk_open_proceeds_d = (D(chunk_qty) / D(short_lot['qty'])) * short_lot['proceeds']
                        short_lot['qty'] -= chunk_qty
                        short_lot['proceeds'] -= chunk_open_proceeds_d
                        # Release embedded deferral proportionally on a
                        # partial cover (mirrors the long side's
                        # wash_deferred proration).
                        _swd = short_lot.get('wash_deferred', Decimal(0))
                        if _swd:
                            short_lot['wash_deferred'] = _swd - (
                                D(chunk_qty)
                                / D(short_lot['qty'] + chunk_qty)) * _swd
                        for r in short_replacements.get(_rep_key(symbol, tx.date), []):
                            if r.get('short_lot_ref') is short_lot:
                                # remaining_qty is denominated in the
                                # rep's own trade-date units; chunk_qty
                                # is in TODAY's units. With a split in
                                # between, the raw subtraction left
                                # phantom capacity (or erased real
                                # capacity) — and since this decrement
                                # is the self-match guard, that skewed
                                # §1091 quantities directly.
                                _cuf = _rep_units_factor(
                                    symbol, r['date'], tx.date)
                                r['remaining_qty'] = max(
                                    0.0, r['remaining_qty']
                                    - chunk_qty / (_cuf or 1.0))

                    chunk_open_proceeds = float(chunk_open_proceeds_d)
                    # Close cost is apportioned from this BUY by qty share.
                    chunk_close_cost = (chunk_qty / tx_qty_abs) * tx_net
                    # Apportion the option-assignment premium to this
                    # chunk's close cost (short-put assignment: the
                    # premium reduces the effective cost of acquiring
                    # the stock used to cover the short).
                    if option_adj != 0:
                        chunk_close_cost += option_adj * (chunk_qty / tx_qty_abs)
                    # Gain on closing a short: opening_proceeds − closing_cost.
                    raw_gain = chunk_open_proceeds - chunk_close_cost

                    if is_option_assign:
                        # Premium rolls into the underlying — accumulate the
                        # would-be gain (with sign flipped to match the
                        # CanadaTaxRules / Pub 550 convention: BUY of stock
                        # subtracts pending_adj from cost; SELL of stock adds
                        # pending_adj to proceeds).
                        pending_option_adjustments.stage(
                            tx, underlying_for_assign, -raw_gain)
                        if trace:
                            symbol_traces[symbol].append(
                                f"# {tx.date} ASSIGN-CLOSE {chunk_qty:10.4f} | "
                                f"OpenProc: {chunk_open_proceeds:10.4f} → "
                                f"premium rolled into {underlying_for_assign} basis "
                                f"(would-be gain {raw_gain:.4f})"
                            )
                        qty_remaining -= chunk_qty
                        continue

                    disallowed_amt = 0.0
                    permanently_disallowed_amt = 0.0
                    replacement_ids: List[str] = []
                    wash_reps: List[Dict[str, Any]] = []

                    # Tainted (missing-history OPENING_BALANCE) short lots have
                    # proceeds=0, so covering them ALWAYS books a bogus loss
                    # — it must never feed §1091 matching (it would fabricate
                    # wash records and push proceeds-reductions onto clean
                    # lots). Mirrors the Canada engine's tainted-loss gate.
                    if (raw_gain < -epsilon and detect_wash_sales
                            and not short_lot.get('tainted')):
                        # §1091 on shorts: a loss on closing a short is
                        # disallowed if a substantially-identical short was
                        # opened within ±30 days. The disallowed loss reduces
                        # the replacement short's effective opening proceeds.
                        remaining_loss_qty = chunk_qty
                        chunk_loss = abs(raw_gain)
                        loss_per_share_d = D(chunk_loss) / D(chunk_qty)
                        candidates = find_replacements_in_window(
                            short_replacements.get(_rep_key(symbol, tx.date), []),
                            tx.date,
                        )
                        if _outside_1091(symbol):
                            if candidates:
                                _flag_futures_loss(tx, raw_gain, candidates)
                            candidates = []
                        for rep in candidates:
                            if remaining_loss_qty <= epsilon:
                                break
                            # rep['remaining_qty'] is in the rep's own trade-
                            # date units; convert to the loss-date's units
                            # for the comparison, and consume in rep units.
                            _uf = _rep_units_factor(symbol, rep['date'], tx.date)
                            match_qty = min(remaining_loss_qty,
                                            rep['remaining_qty'] * _uf)
                            match_disallowed_d = D(match_qty) * loss_per_share_d
                            match_disallowed = float(match_disallowed_d)
                            rep['remaining_qty'] -= match_qty / _uf
                            remaining_loss_qty -= match_qty

                            if rep['is_sheltered'] or rep.get('is_affiliated'):
                                # IRA (Rev. Rul. 2008-5) or a spouse /
                                # controlled corporation: the §1091(d)
                                # adjustment belongs to THEIR position,
                                # never to a lot in these books (ENGINE-I1).
                                permanently_disallowed_amt += match_disallowed
                            else:
                                # (A replacement short covered before the
                                # loss has no capacity left and is never a
                                # candidate: US-WASH-21.)
                                disallowed_amt += match_disallowed
                                if rep.get('short_lot_ref') is not None:
                                    # Replacement short is already open —
                                    # reduce its remaining proceeds now,
                                    # and track the embedded deferral so
                                    # inventory reports show it (the
                                    # long side already did; short-side
                                    # deferrals were invisible). Share
                                    # for share, as on the long side: a
                                    # replacement short bigger than the
                                    # match is split, the matched shares
                                    # first in FIFO carry the whole
                                    # reduction (audit A2-0206 — it was
                                    # spread over every share, moving
                                    # loss into a later year).
                                    _sl = rep['short_lot_ref']
                                    if _sl['qty'] > match_qty + epsilon:
                                        _sfr = D(match_qty) / D(_sl['qty'])
                                        _srem = dict(_sl)
                                        _srem['qty'] = _sl['qty'] - match_qty
                                        _srem['proceeds'] = (
                                            _sl['proceeds'] * (1 - _sfr))
                                        _swd0 = _sl.get('wash_deferred',
                                                        Decimal(0))
                                        _srem['wash_deferred'] = (
                                            _swd0 * (1 - _sfr))
                                        _sl['qty'] = match_qty
                                        _sl['proceeds'] = (
                                            _sl['proceeds']
                                            - _srem['proceeds'])
                                        _sl['wash_deferred'] = (
                                            _swd0 - _srem['wash_deferred'])
                                        for _sls in inventory_short.values():
                                            for _si, _s in enumerate(_sls):
                                                if _s is _sl:
                                                    _sls.insert(_si + 1,
                                                                _srem)
                                                    break
                                        rep['short_lot_ref'] = _srem
                                    _sl['proceeds'] -= match_disallowed_d
                                    _sl['wash_deferred'] = (
                                        _sl.get('wash_deferred', Decimal(0))
                                        + match_disallowed_d)
                                else:
                                    rep['pending_proceeds_reduction'] += match_disallowed_d
                            replacement_ids.append(rep['tx'].id)
                            wash_reps.append({
                                'tx_id': rep['tx'].id,
                                'date': rep['date'],
                                'qty_total': float(rep['tx'].quantity),
                                'price': float(rep['tx'].price),
                                'account': rep['tx'].account,
                                'match_qty': match_qty,
                                'proceeds_reduction': match_disallowed,
                                'is_sheltered': rep['is_sheltered'],
                                'is_affiliated': rep.get('is_affiliated', False),
                            })

                    # Stand-alone shorts: holding period is conventionally
                    # zero — gain on close is SHORT_TERM per §1222.
                    # §1233(b)(1) (a long held ≤1yr at short-open makes a
                    # gain short-term) and §1233(d) (a long held >1yr makes
                    # a loss long-term) are not implemented in v1.
                    is_long_term = False
                    acq_for_holding = short_lot.get('effective_open_date', short_lot['date'])
                    try:
                        days_held = (datetime.strptime(tx.date, '%Y-%m-%d') -
                                     datetime.strptime(acq_for_holding, '%Y-%m-%d')).days
                    except ValueError:
                        days_held = 0

                    allowed_gain = raw_gain + disallowed_amt + permanently_disallowed_amt

                    if trace:
                        wash_tag = ''
                        if disallowed_amt > 0 or permanently_disallowed_amt > 0:
                            wash_tag = f" [WASH disallowed={disallowed_amt + permanently_disallowed_amt:.4f}]"
                        symbol_traces[symbol].append(
                            f"# {tx.date} BUY-CLOSE {chunk_qty:10.4f} | "
                            f"Against Short: {short_lot['date']} | "
                            f"OpenProc: {chunk_open_proceeds:10.4f} | "
                            f"CloseCost: {chunk_close_cost:10.4f} | "
                            f"RawGain: {raw_gain:10.4f} | "
                            f"AllowedGain: {allowed_gain:10.4f}{wash_tag}"
                        )
                    rg_trace = []
                    if trace:
                        rg_trace = list(symbol_traces[symbol])
                        symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} (CONT...) ---"]

                    # Signed cash-flow convention for SHORT — the SAME
                    # field order as the Canada engine (FUZZ #E: the
                    # engines had premium/buyback in OPPOSITE fields,
                    # and USA rows broke gain == proceeds - cost):
                    # cost = -opening premium (cash IN at open),
                    # proceeds = -close cost (cash OUT at close), so
                    # proceeds - cost = signed gain, direction-free.
                    # Old line: cost is the cash
                    # outflow on the closing buy (positive number stored as
                    # negative so downstream tools can compute
                    # proceeds-cost=signed gain regardless of direction).
                    # Matches the Canada engine's SHORT signing.
                    entry = make_gain_entry(
                        chunk_qty=chunk_qty,
                        chunk_cost=-chunk_open_proceeds,
                        chunk_proceeds=-chunk_close_cost,
                        raw_gain=raw_gain,
                        allowed_gain=allowed_gain,
                        disallowed_amt=disallowed_amt,
                        permanently_disallowed_amt=permanently_disallowed_amt,
                        replacement_ids=replacement_ids,
                        wash_reps=wash_reps,
                        acq_for_holding=acq_for_holding,
                        days_held=days_held,
                        is_long_term=is_long_term,
                        direction='SHORT',
                        rg_trace=rg_trace,
                        tainted=short_lot.get('tainted', False),
                        raw_acq_date=short_lot['date'],
                    )
                    realized_gains.append(entry)

                    total_disallowed = disallowed_amt + permanently_disallowed_amt
                    if total_disallowed > epsilon:
                        wash_sale_records.append({
                            'loss_tx_id': tx.id,
                            'symbol': symbol,
                            'date': tx.date,
                            # Settlement date too: the --year filter keys on
                            # tax_date; without this the record fell back to
                            # trade date while the gain entries used settle,
                            # splitting total_disallowed and its rows across
                            # two years.
                            'date_settle': tx.date_settle or tx.date,
                            'disallowed_amount': total_disallowed,
                            'permanently_disallowed': permanently_disallowed_amt,
                            'qty': chunk_qty,
                            'replacement_lot_ids': replacement_ids,
                            'direction': 'SHORT',
                        })
                    qty_remaining -= chunk_qty

                # --- buy-to-open: any leftover quantity opens a new long lot ---
                if (_dust_noise(tx_qty_abs) < qty_remaining <= epsilon
                        and not inventory_short[ikey]):
                    _us_dust_dropped.append((symbol, qty_remaining))
                if qty_remaining > epsilon:
                    rep_record = next(
                        (r for r in long_replacements.get(_rep_key(symbol, tx.date), [])
                         if r['tx'].id == tx.id and not r['is_sheltered']),
                        None,
                    )
                    leftover_share = qty_remaining / tx_qty_abs
                    # The plain cost of the shares opened (the option-
                    # assignment premium share included; for a short put
                    # assignment the premium received reduces the cost of
                    # the stock acquired at strike, Pub 550 — stored as
                    # -gain, Canada convention, so adding it yields cost -
                    # premium). The buy-to-close branch already consumed
                    # its share of the premium above.
                    base_cost_d = D(leftover_share) * D(tx_net)
                    if option_adj != 0:
                        base_cost_d += D(option_adj * leftover_share)
                    # A replacement matched BEFORE its own buy (the loss
                    # preceded it): only the matched shares carry the
                    # tacked holding period and the deferred basis, one
                    # block per matched loss in match order (audit
                    # A2-0060); the rest of the buy is an ordinary lot
                    # behind them in FIFO order and becomes the rep's
                    # lot_ref for later matches.
                    _pend = (list(rep_record.get('pending_matches') or [])
                             if rep_record is not None else [])
                    _blocks = []          # (qty, bump, effective date)
                    _left = qty_remaining
                    for _pq, _pb, _pe in _pend:
                        if _left <= epsilon:
                            # Sub-epsilon overshoot: fold the bump into
                            # the last block so no deferral is lost.
                            if _blocks:
                                _q0, _b0, _e0 = _blocks[-1]
                                _blocks[-1] = (_q0, _b0 + _pb, _e0)
                            continue
                        _bq = min(_pq, _left)
                        if _left - _bq <= epsilon:
                            _bq = _left
                        _blocks.append((_bq, _pb, min(_pe, tx.date)))
                        _left -= _bq
                    if not _blocks:
                        _blocks.append((qty_remaining, Decimal(0), tx.date))
                        _left = 0.0
                    _new_lots = []
                    _alloc = Decimal(0)
                    for _bq, _pb, _pe in _blocks:
                        _c = base_cost_d * D(_bq) / D(qty_remaining)
                        _alloc += _c
                        _new_lots.append({
                            'qty': _bq,
                            'cost_basis': _c + _pb,
                            'wash_deferred': _pb,
                            'date': tx.date,
                            'effective_acq_date': _pe,
                            'id': tx.id,
                        })
                    if _left > epsilon:
                        _new_lots.append({
                            'qty': _left,
                            'cost_basis': base_cost_d - _alloc,
                            'wash_deferred': Decimal(0),
                            'date': tx.date,
                            'effective_acq_date': tx.date,
                            'id': tx.id,
                        })
                    else:
                        # The last block absorbs the division remainder.
                        _new_lots[-1]['cost_basis'] += base_cost_d - _alloc
                    inventory_long[ikey].extend(_new_lots)
                    lot = _new_lots[0]
                    if rep_record is not None:
                        # The unmatched remainder (or, fully matched,
                        # the last block) answers any later match.
                        rep_record['lot_ref'] = _new_lots[-1]
                        rep_record['pending_basis_add'] = Decimal(0)
                        rep_record['pending_matches'] = []
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} BUY-OPEN  {qty_remaining:10.4f} @ {tx.price:10.4f} | "
                            f"Lot_Cost: {float(lot['cost_basis']):10.4f} | "
                            f"Effective_Acq: {lot['effective_acq_date']}"
                        )
                continue

            # ============================================================
            # SELL path: first close any existing longs FIFO; if leftover
            # quantity remains, open a new short lot.
            # ============================================================
            qty_remaining = tx_qty_abs

            # Pull any pending option-assignment premium for this stock symbol.
            # For a short call assignment, the premium increases the proceeds
            # of the stock sold at strike. Stored as -gain (Canada convention)
            # — subtracting it from per-chunk proceeds yields proceeds + gain.
            # Same marked-leg gate as the BUY path above.
            option_adj_sell = _take_option_adj(tx)
            if not is_option_assign:
                _plan_disposition(inventory_long, ikey, tx,
                                  'lot_ref', 'cost_basis')

            # --- sell-to-close: pop long lots FIFO ---
            while qty_remaining > epsilon and inventory_long[ikey]:
                lot = inventory_long[ikey][0]
                if lot['qty'] <= qty_remaining + epsilon:
                    if lot['qty'] - qty_remaining > _dust_noise(lot['qty']):
                        _us_dust_absorbed.append(
                            (symbol, lot['qty'] - qty_remaining))
                    chunk_qty = lot['qty']
                    chunk_cost_d = lot['cost_basis']
                    inventory_long[ikey].pop(0)
                    # As on the short side: a long_replacement rep that
                    # pointed at this sold lot loses its lot reference and
                    # its capacity, so it never washes a later loss
                    # (US-WASH-21, re-audit A2-0817).
                    for r in long_replacements.get(_rep_key(symbol, tx.date), []):
                        if r.get('lot_ref') is lot:
                            r['lot_ref'] = None
                            # Sold shares can no longer serve as §1091
                            # replacement (FUZZ #A): stale capacity made
                            # later losses match DEAD lots and land in the
                            # permanent-denial branch, destroying real
                            # losses (economic 0 reported as +4000 on a
                            # 20-cycle ladder).
                            r['remaining_qty'] = 0.0
                else:
                    chunk_qty = qty_remaining
                    chunk_cost_d = (D(chunk_qty) / D(lot['qty'])) * lot['cost_basis']
                    _wd = lot.get('wash_deferred', Decimal(0))
                    if _wd:
                        lot['wash_deferred'] = _wd - (
                            D(chunk_qty) / D(lot['qty'])) * _wd
                    lot['qty'] -= chunk_qty
                    lot['cost_basis'] -= chunk_cost_d
                    for r in long_replacements.get(_rep_key(symbol, tx.date), []):
                        if r.get('lot_ref') is lot:
                            # Same unit conversion as the short side
                            # above: consume the rep's capacity in ITS
                            # trade-date units, not today's.
                            _cuf = _rep_units_factor(
                                symbol, r['date'], tx.date)
                            r['remaining_qty'] = max(
                                0.0, r['remaining_qty']
                                - chunk_qty / (_cuf or 1.0))
                chunk_cost = float(chunk_cost_d)
                chunk_proceeds = (chunk_qty / tx_qty_abs) * tx_net
                # Apportion any option-assignment premium to this chunk's
                # proceeds (short-call assignment case: premium boosts proceeds).
                if option_adj_sell != 0:
                    chunk_proceeds -= option_adj_sell * (chunk_qty / tx_qty_abs)
                raw_gain = chunk_proceeds - chunk_cost

                if is_option_assign:
                    # Long option exercised: roll premium into underlying.
                    pending_option_adjustments.stage(
                        tx, underlying_for_assign, -raw_gain)
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} ASSIGN-EXERCISE {chunk_qty:10.4f} | "
                            f"Cost: {chunk_cost:10.4f} Proc: {chunk_proceeds:10.4f} → "
                            f"premium rolled into {underlying_for_assign} basis "
                            f"(would-be gain {raw_gain:.4f})"
                        )
                    qty_remaining -= chunk_qty
                    continue

                disallowed_amt = 0.0
                permanently_disallowed_amt = 0.0
                replacement_ids: List[str] = []
                wash_reps: List[Dict[str, Any]] = []

                # Tainted long lots (basis 0) can't normally lose, but gate
                # symmetrically with the short side / Canada engine.
                if (raw_gain < -epsilon and detect_wash_sales
                        and not lot.get('tainted')):
                    remaining_loss_qty = chunk_qty
                    chunk_loss = abs(raw_gain)
                    loss_per_share_d = D(chunk_loss) / D(chunk_qty)
                    _reps_here = long_replacements.get(
                        _rep_key(symbol, tx.date), [])
                    candidates = find_replacements_in_window(
                        _reps_here, tx.date)
                    if _outside_1091(symbol):
                        if candidates:
                            _flag_futures_loss(tx, raw_gain, candidates)
                        candidates = []
                    else:
                        # Replacements another taxable account bought
                        # and sold before this loss (US-WASH-22), in the
                        # order acquired; a purchase's still-held shares
                        # match before its sold ones.
                        _sold = _sold_replacements_in_window(_reps_here, tx)
                        if _sold:
                            _rank = {id(r): i for i, r in
                                     enumerate(_reps_here)}

                            def _ck(c):
                                r = c[0] if isinstance(c, tuple) else c
                                return (r['date'], r['tx'].time or '',
                                        2 if r['is_affiliated'] else 1
                                        if r['is_sheltered'] else 0,
                                        _rank.get(id(r), 0),
                                        1 if isinstance(c, tuple) else 0)
                            candidates = sorted(list(candidates) + _sold,
                                                key=_ck)
                    for rep in candidates:
                        if remaining_loss_qty <= epsilon:
                            break
                        if isinstance(rep, tuple):
                            _srep, _sch = rep
                            _uf = _rep_units_factor(symbol, _srep['date'],
                                                    tx.date) or 1.0
                            match_qty = min(remaining_loss_qty,
                                            _sch['avail'] * _uf)
                            match_disallowed_d = (D(match_qty)
                                                  * loss_per_share_d)
                            _sch['avail'] -= match_qty / _uf
                            remaining_loss_qty -= match_qty
                            disallowed_amt += float(match_disallowed_d)
                            _se = _apply_sold_replacement(
                                _srep, _sch, match_qty, _uf,
                                match_disallowed_d, tx, lot)
                            replacement_ids.append(_srep['tx'].id)
                            wash_reps.append({
                                'tx_id': _srep['tx'].id,
                                'date': _srep['date'],
                                'qty_total': float(_srep['tx'].quantity),
                                'price': float(_srep['tx'].price),
                                'account': _srep['tx'].account,
                                'match_qty': match_qty,
                                'basis_bump': float(match_disallowed_d),
                                'is_sheltered': False,
                                'is_affiliated': False,
                                'sold_before_loss': _se.get('date'),
                            })
                            continue
                        # Convert the rep's own-units quantity into loss-date
                        # units for matching; consume in rep units.
                        _uf = _rep_units_factor(symbol, rep['date'], tx.date)
                        match_qty = min(remaining_loss_qty,
                                        rep['remaining_qty'] * _uf)
                        match_disallowed_d = D(match_qty) * loss_per_share_d
                        match_disallowed = float(match_disallowed_d)
                        rep['remaining_qty'] -= match_qty / _uf
                        remaining_loss_qty -= match_qty

                        # §1223(3) tacking: the replacement's holding
                        # period gains the PERIOD the wash-sold shares
                        # were held, not their calendar acquisition
                        # date. Inheriting the raw date wrongly
                        # included the sale→repurchase gap (up to 30
                        # days) in the holding period — flipping
                        # SHORT/LONG_TERM near the anniversary — and
                        # collapsed the overlap for replacements bought
                        # before the sale. Broker convention: adjusted
                        # acquisition date = the replacement's own buy
                        # date minus the prior holding period.
                        loss_acq_eff = lot.get('effective_acq_date', lot['date'])
                        try:
                            _prior_held = (
                                datetime.strptime(tx.date, '%Y-%m-%d')
                                - datetime.strptime(loss_acq_eff,
                                                    '%Y-%m-%d'))
                            _tacked_eff = (
                                datetime.strptime(rep['date'], '%Y-%m-%d')
                                - _prior_held).strftime('%Y-%m-%d')
                        except ValueError:
                            _tacked_eff = loss_acq_eff
                        # Tacking and the §1091(d) bump apply to the
                        # MATCHED shares only. A replacement lot bigger
                        # than the match is split at the match point:
                        # the matched sub-lot (kept as the original
                        # object, so lot identity elsewhere holds)
                        # takes the tack + bump; the untouched
                        # remainder becomes a new lot right behind it
                        # in FIFO order and the rep's lot_ref for any
                        # later match. Whole-lot tacking mis-termed the
                        # remainder (2026-09 US-engine audit: a 200-share
                        # rep for a 100-share loss reported all 200 as
                        # LONG_TERM).
                        _tack_lot = rep.get('lot_ref')
                        if _tack_lot is not None:
                            # Inventory lots are rescaled by every SPLIT
                            # row, so the lot is already in the loss
                            # date's units, like match_qty — dividing by
                            # the rep's units factor put the bump on the
                            # pre-split share count (audit A2-0054).
                            _mq = match_qty
                            if _tack_lot['qty'] > _mq + epsilon:
                                _frac = D(_mq) / D(_tack_lot['qty'])
                                _rem = dict(_tack_lot)
                                _rem['qty'] = _tack_lot['qty'] - _mq
                                _rem['cost_basis'] = (
                                    _tack_lot['cost_basis'] * (1 - _frac))
                                _rem['wash_deferred'] = (
                                    _tack_lot.get('wash_deferred',
                                                  Decimal(0))
                                    * (1 - _frac))
                                _tack_lot['qty'] = _mq
                                _tack_lot['cost_basis'] = (
                                    _tack_lot['cost_basis'] * _frac)
                                _tack_lot['wash_deferred'] = (
                                    _tack_lot.get('wash_deferred',
                                                  Decimal(0)) * _frac)
                                for _lots in inventory_long.values():
                                    for _li, _l in enumerate(_lots):
                                        if _l is _tack_lot:
                                            _lots.insert(_li + 1, _rem)
                                            break
                                rep['lot_ref'] = _rem
                        if _tacked_eff < rep['effective_acq_date']:
                            rep['effective_acq_date'] = _tacked_eff
                        if (_tack_lot is not None
                                and _tacked_eff
                                < _tack_lot['effective_acq_date']):
                            _tack_lot['effective_acq_date'] = _tacked_eff

                        if rep['is_sheltered'] or rep.get('is_affiliated'):
                            # IRA (Rev. Rul. 2008-5: no basis transfer)
                            # or a spouse / controlled corporation
                            # (§1091(d) adds the loss to THEIR
                            # replacement's basis): no lot in these
                            # books carries it, so it is not a deferral
                            # here — reporting it as one left a
                            # deferred amount nothing recovers
                            # (partition ENGINE-I1, tax-logic US-WASH-16).
                            permanently_disallowed_amt += match_disallowed
                        else:
                            # (A taxable replacement sold before the loss
                            # has no capacity left and is never a
                            # candidate: US-WASH-21.)
                            disallowed_amt += match_disallowed
                            if _tack_lot is not None:
                                _tack_lot['cost_basis'] += match_disallowed_d
                                _tack_lot['wash_deferred'] = (
                                    _tack_lot.get('wash_deferred',
                                                  Decimal(0))
                                    + match_disallowed_d)
                            else:
                                rep['pending_basis_add'] += match_disallowed_d
                                # One block per matched loss: each keeps
                                # its own bump and tacked holding period
                                # (§1223(3), audit A2-0060 — one merged
                                # sub-lot averaged the bumps and gave every
                                # share the earliest tacked date).
                                rep.setdefault('pending_matches', []).append(
                                    (match_qty / (_uf or 1.0),
                                     match_disallowed_d, _tacked_eff))
                        replacement_ids.append(rep['tx'].id)
                        wash_reps.append({
                            'tx_id': rep['tx'].id,
                            'date': rep['date'],
                            'qty_total': float(rep['tx'].quantity),
                            'price': float(rep['tx'].price),
                            'account': rep['tx'].account,
                            'match_qty': match_qty,
                            'basis_bump': match_disallowed,
                            'is_sheltered': rep['is_sheltered'],
                            'is_affiliated': rep.get('is_affiliated', False),
                        })

                acq_for_holding = lot.get('effective_acq_date', lot['date'])
                is_long_term = held_more_than_one_year(acq_for_holding, tx.date)
                try:
                    days_held = (datetime.strptime(tx.date, '%Y-%m-%d') -
                                 datetime.strptime(acq_for_holding, '%Y-%m-%d')).days
                except ValueError:
                    days_held = 0

                allowed_gain = raw_gain + disallowed_amt + permanently_disallowed_amt

                if trace:
                    wash_tag = ''
                    if disallowed_amt > 0 or permanently_disallowed_amt > 0:
                        wash_tag = f" [WASH disallowed={disallowed_amt + permanently_disallowed_amt:.4f}]"
                    symbol_traces[symbol].append(
                        f"# {tx.date} SELL-CLOSE {chunk_qty:10.4f} | "
                        f"Against Lot: {lot['date']} (eff {acq_for_holding}) | "
                        f"Cost: {chunk_cost:10.4f} | Proceeds: {chunk_proceeds:10.4f} | "
                        f"RawGain: {raw_gain:10.4f} | AllowedGain: {allowed_gain:10.4f}{wash_tag}"
                    )
                rg_trace = []
                if trace:
                    rg_trace = list(symbol_traces[symbol])
                    symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} (CONT...) ---"]

                entry = make_gain_entry(
                    chunk_qty=chunk_qty,
                    chunk_cost=chunk_cost,
                    chunk_proceeds=chunk_proceeds,
                    raw_gain=raw_gain,
                    allowed_gain=allowed_gain,
                    disallowed_amt=disallowed_amt,
                    permanently_disallowed_amt=permanently_disallowed_amt,
                    replacement_ids=replacement_ids,
                    wash_reps=wash_reps,
                    acq_for_holding=acq_for_holding,
                    days_held=days_held,
                    is_long_term=is_long_term,
                    direction='LONG',
                    rg_trace=rg_trace,
                    raw_acq_date=lot['date'],
                    tainted=lot.get('tainted', False),
                )
                realized_gains.append(entry)
                _record_sold_replacement(lot, entry, chunk_qty, tx)

                total_disallowed = disallowed_amt + permanently_disallowed_amt
                if total_disallowed > epsilon:
                    wash_sale_records.append({
                        'loss_tx_id': tx.id,
                        'symbol': symbol,
                        'date': tx.date,
                        'date_settle': tx.date_settle or tx.date,  # see short-side note
                        'disallowed_amount': total_disallowed,
                        'permanently_disallowed': permanently_disallowed_amt,
                        'qty': chunk_qty,
                        'replacement_lot_ids': replacement_ids,
                        'direction': 'LONG',
                    })
                qty_remaining -= chunk_qty

            # --- sell-to-open: any leftover quantity opens a new short lot ---
            if (_dust_noise(tx_qty_abs) < qty_remaining <= epsilon
                    and not inventory_long[ikey]):
                _us_dust_dropped.append((symbol, -qty_remaining))
            if qty_remaining > epsilon:
                rep_record = next(
                    (r for r in short_replacements.get(_rep_key(symbol, tx.date), [])
                     if r['tx'].id == tx.id and not r['is_sheltered']),
                    None,
                )
                leftover_share = qty_remaining / tx_qty_abs
                leftover_proceeds_d = D(leftover_share) * D(tx_net)
                # Apportion the remaining option-assignment premium to
                # the leftover short open (mirror of the close-long
                # application above so the full premium is consumed).
                # Short-call assignment: premium boosts the opening
                # proceeds of the resulting short. Without this the
                # leftover share was silently discarded.
                if option_adj_sell != 0:
                    leftover_proceeds_d -= D(option_adj_sell * leftover_share)
                if rep_record is not None:
                    # Wash-sale carryover: pending proceeds reduction baked in.
                    leftover_proceeds_d -= rep_record['pending_proceeds_reduction']
                short_lot = {
                    'qty': qty_remaining,
                    'proceeds': leftover_proceeds_d,
                    'date': tx.date,
                    'effective_open_date': tx.date,
                    'id': tx.id,
                    # Deferral already baked into leftover_proceeds_d —
                    # surfaced so inventory reports show it.
                    'wash_deferred': (rep_record['pending_proceeds_reduction']
                                      if rep_record is not None
                                      else Decimal(0)),
                }
                inventory_short[ikey].append(short_lot)
                if rep_record is not None:
                    rep_record['short_lot_ref'] = short_lot
                    # Share for share (audit A2-0206): only the matched
                    # shorts carry the reduction; the rest is an
                    # ordinary short behind them in FIFO order.
                    _smatched = (rep_record.get('open_qty', 0.0)
                                 - rep_record['remaining_qty'])
                    if (rep_record['pending_proceeds_reduction']
                            and epsilon < _smatched
                            < qty_remaining - epsilon):
                        _sfr = D(_smatched) / D(qty_remaining)
                        _plain_d = leftover_proceeds_d + \
                            rep_record['pending_proceeds_reduction']
                        _srem = dict(short_lot)
                        _srem['qty'] = qty_remaining - _smatched
                        _srem['proceeds'] = _plain_d * (1 - _sfr)
                        _srem['wash_deferred'] = Decimal(0)
                        short_lot['qty'] = _smatched
                        short_lot['proceeds'] = (leftover_proceeds_d
                                                 - _srem['proceeds'])
                        inventory_short[ikey].append(_srem)
                        rep_record['short_lot_ref'] = (
                            _srem if rep_record['remaining_qty'] > epsilon
                            else short_lot)
                    rep_record['pending_proceeds_reduction'] = Decimal(0)
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} SELL-OPEN {qty_remaining:10.4f} @ {tx.price:10.4f} | "
                        f"Short_Proc: {float(short_lot['proceeds']):10.4f} | "
                        f"Effective_Open: {short_lot['effective_open_date']}"
                    )

        # Dividends — report the gross dividend (1099-DIV box 1a). Withheld
        # foreign tax flows via separate TAX records into the foreign tax
        # credit, not the dividend income line.
        for tx in transactions:
            # PIL gets its own entry so sum-gains can show it in a dedicated
            # column. Kept out of the eligible-dividend total — IRS treats
            # PIL as a non-qualified substitute payment (ordinary income).
            if tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu':
                pil_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                realized_gains.append({
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'symbol': tx.symbol, 'qty': tx.quantity,
                    'currency': tx.currency, 'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                    'pil': pil_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND_IN_LIEU',
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
                realized_gains.append({
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'symbol': tx.symbol, 'qty': tx.quantity,
                    'currency': tx.currency, 'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                    'dividend': div_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND',
                })

        # Invariant: across all close events (long and short), the
        # difference between allowed and raw gain must equal the total
        # disallowed amount. Permanently disallowed losses also add back.
        sell_entries = [g for g in realized_gains if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        sum_allowed = sum(g['gain'] for g in sell_entries)
        sum_raw = sum(g.get('raw_gain', g['gain']) for g in sell_entries)
        sum_disallowed = sum(g.get('disallowed_amount', 0.0) for g in sell_entries)
        if abs((sum_allowed - sum_raw) - sum_disallowed) > 0.01:
            print(
                f"warning: US gain disallowance invariant broken — "
                f"allowed-raw={sum_allowed - sum_raw:.4f} vs disallowed={sum_disallowed:.4f}",
                file=sys.stderr,
            )
        _warn_undrained_adjustments(pending_option_adjustments.undrained(), "usa")
        if _us_dust:
            _by: Dict[str, List[float]] = {}
            for _t in _us_dust:
                _by.setdefault(_t.symbol, []).append(_t.quantity)
            print("warning: rows smaller than 1e-08 units are not booked "
                  "by the US engine: " + ", ".join(
                      f"{s_} ({len(q)} row(s), net {sum(q):+.3g})"
                      for s_, q in sorted(_by.items()))
                  + " — their units and money are left out of the lots "
                  "and Form 8949.", file=sys.stderr)
        for _what, _rows in (
                ("a lot residue of at most 1e-08 units was folded into "
                 "the sale that closed the lot (its cost is in that "
                 "sale's basis)", _us_dust_absorbed),
                ("a sale exceeded the units held by at most 1e-08 (no "
                 "position was opened for the excess; the whole proceeds "
                 "are on the units held)", _us_dust_dropped)):
            if _rows:
                _agg: Dict[str, List[float]] = {}
                for _s, _q in _rows:
                    _agg.setdefault(_s, []).append(_q)
                print(f"warning: {_what}: " + ", ".join(
                    f"{s_} ({len(q)} time(s), {sum(q):+.3g} units)"
                    for s_, q in sorted(_agg.items()))
                    + " (US-CRYPTO-08).", file=sys.stderr)

        # Warn-only call-as-replacement scan (the experimental US engine
        # does not enforce it; always on — cross_asset is retired).
        # Losses on the TRADE basis, matching the §1091 window
        # convention. No held-at-end requirement in §1091 (that is a CRA
        # s.54 condition).
        _orw_losses = [
            {'symbol': g['symbol'],
             'date': g['date'],
             'qty': abs(float(g.get('qty') or 0.0)),
             'amount': g.get('raw_gain', g['gain']),
             'id': g.get('id', ''),
             'direction': g.get('direction', 'LONG')}
            for g in sell_entries
            if g.get('raw_gain', g['gain']) < -0.005
            and not g.get('tainted')]
        option_replacement_warnings = detect_option_replacement_matches(
            _orw_losses, all_events,
            date_of=lambda t: t.date,
            canonical=split_timeline.canonical,
            statute_label="IRS §1091 ('option to acquire')",
            check_held_at_end=False)
        option_replacement_warnings += detect_right_replacement_matches(
            _orw_losses, all_events,
            date_of=lambda t: t.date,
            canonical=split_timeline.canonical,
            statute_label="IRS §1091 ('contract or option to acquire')")
        option_replacement_warnings += \
            detect_unresolved_option_replacement_matches(
                _orw_losses, all_events,
                date_of=lambda t: t.date,
                canonical=split_timeline.canonical,
                statute_label="IRS §1091 ('option to acquire')")
        option_replacement_warnings += list(_fut_flags.values())
        if getattr(self, 'emit_replacement_stderr', True):
            # run_gains turns this off and prints the warnings after its
            # year filter (audit S070-04).
            _emit_option_replacement_stderr(option_replacement_warnings,
                                            country='usa')

        # Conservation post-condition (see _verify_share_conservation).
        _inv_qty: Dict[str, float] = {}
        for _k, _lots in inventory_long.items():
            _sym = _k[1] if per_account_basis else _k
            _inv_qty[_sym] = _inv_qty.get(_sym, 0.0) \
                + sum(l['qty'] for l in _lots)
        for _k, _lots in inventory_short.items():
            _sym = _k[1] if per_account_basis else _k
            _inv_qty[_sym] = _inv_qty.get(_sym, 0.0) \
                - sum(l['qty'] for l in _lots)
        _verify_share_conservation(
            [t for t in taxable_sorted
             if (not non_capital(t.action, t.type)
                 and t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                                  'SPLIT'))
             or (t.action == 'TRANSFER' and t.type == LOT_MOVE_TYPE)],
            _inv_qty, 'usa',
            zero_ratio_skips=True)      # US: ratio-0 skips the scale,
                                        # rename still migrates

        by_ticker: Dict[str, Dict[str, Any]] = {}
        for g in realized_gains:
            # Mirror the Canada engine: tainted dispositions (missing-history
            # OPENING_BALANCE consumption) carry fabricated cost=0
            # numbers and the CLI splits them out into
            # `manual_reporting_required` after this point. Letting them
            # into by_ticker totals would silently inflate any
            # downstream consumer reading by_ticker rather than
            # transactions.
            if g.get('tainted'):
                continue
            s = g['symbol']
            if s not in by_ticker:
                by_ticker[s] = {'total_cost': 0.0, 'total_proceeds': 0.0, 'total_gain': 0.0, 'total_div': 0.0, 'total_pil': 0.0, 'trade_count': 0, 'hold_days': []}
            stats = by_ticker[s]
            if g.get('action') == 'DIVIDEND':
                stats['total_div'] += g.get('dividend', 0.0)
            elif g.get('action') == 'DIVIDEND_IN_LIEU':
                stats['total_pil'] += g.get('pil', 0.0)
            else:
                stats['total_cost'] += g['cost']
                stats['total_proceeds'] += g['proceeds']
                stats['total_gain'] += g['gain']
                stats['trade_count'] += 1
                stats['hold_days'].append(g.get('days_held', 0))

        # Inventory report includes both unsold long lots and unclosed short
        # lots (short qty reported as a negative number for clarity).
        # Currency comes from the symbol_currency map built at pass start;
        # falls back to '' if a symbol somehow has no currency on any tx
        # (defensive — taxjson-export tolerates the empty case).
        inventory_report = []
        for s, lots in inventory_long.items():
            _acct = s[0] if per_account_basis else ''
            s = s[1] if per_account_basis else s
            tot_qty = sum(l['qty'] for l in lots)
            if tot_qty > epsilon:
                inventory_report.append({
                    'symbol': s, 'qty': tot_qty,
                    **({'account': _acct} if _acct else {}),
                    'total_cost': float(sum(l['cost_basis'] for l in lots)),
                    'currency': symbol_currency.get(s, ''),
                    # Earliest acquisition date among the REMAINING
                    # lots. After a FIFO close, the consumed lots are
                    # gone, so `min` correctly returns the new
                    # position-start when the original lots have all
                    # been closed and replaced by later buys.
                    'position_start_date': min(l['date'] for l in lots),
                    # Most recent surviving lot — the wash-window signal.
                    'last_acq_date': max(l['date'] for l in lots),
                    # §1091 basis bumps still embedded in surviving lots.
                    'deferred_wash': round(float(sum(
                        l.get('wash_deferred', Decimal(0))
                        for l in lots)), 4),
                })
        for s, lots in inventory_short.items():
            _acct = s[0] if per_account_basis else ''
            s = s[1] if per_account_basis else s
            tot_qty = sum(l['qty'] for l in lots)
            if tot_qty > epsilon:
                inventory_report.append({
                    'symbol': s, 'qty': -tot_qty,  # signed: negative = short
                    **({'account': _acct} if _acct else {}),
                    # NEGATIVE: opening proceeds credited — the repo-wide
                    # short-inventory convention (cost_per_share =
                    # total_cost/qty comes out positive; see
                    # test_holdings_toml / test_taxjson_export_report).
                    'total_cost': float(-sum(l['proceeds'] for l in lots)),
                    'currency': symbol_currency.get(s, ''),
                    'position_start_date': min(l['date'] for l in lots),
                    'last_acq_date': max(l['date'] for l in lots),
                    'deferred_wash': round(float(sum(
                        l.get('wash_deferred', Decimal(0))
                        for l in lots)), 4),
                })

        return {
            'transactions': realized_gains,
            'by_ticker': by_ticker,
            'wash_sales': wash_sale_records,
            'option_replacement_warnings': option_replacement_warnings,
            'dated_notes': [list(n) for n in _dated_notes],
            'inventory': inventory_report,
            'summary': {
                'total_gain': sum(g['gain'] for g in realized_gains),
                'total_disallowed': sum(w['disallowed_amount'] for w in wash_sale_records),
                'count': len(realized_gains),
            },
        }

def get_tax_rules(country: str) -> TaxRules:
    """The engine for a country (lib/country.canonical_country: an
    unknown value raises CountryError, a ValueError)."""
    from taxjson.lib.country import canonical_country
    c = canonical_country(country)
    return CanadaTaxRules() if c == "canada" else USATaxRules()

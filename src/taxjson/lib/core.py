from taxjson.lib.stage_msg import emit_line
import contextlib
import copy
import functools
import io
import json
import re
import hashlib
from dataclasses import dataclass, asdict, replace
from typing import List, Dict, Any, Optional, Sequence, Tuple
from pathlib import Path
from datetime import datetime, timedelta

from taxjson.lib.country import check_engine_allowed as _check_engine_allowed
from taxjson.lib.corporate_timeline import (SplitTimeline, account_tie,
                                            event_sort_key,
                                            normalize_symbol_new, split_seen,
                                            split_in_settle_lag,
                                            SPLIT_DATE_WINDOW_DAYS)
from decimal import Decimal

from taxjson.lib.numeric import D
# A sale's trace shares its pool's running trace (audit 2026-10-25
# robustness#5: a copy per sale grew with the square of the rows).
from taxjson.lib.readiness import UNSETTLED_COMPENSATION_MARK
from taxjson.lib.trace_format import SharedTrace

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
    # An s.86.1 spin-off's parent cost reduction: how many parent shares
    # the distribution was made on (0: not known), which sales it reduces
    # when the parent was sold before it (TJ-1026). NOT part of
    # compute_id, omitted from to_dict() when 0.
    corp_parent_qty: float = 0.0
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
    # Original crypto reward/commission evidence, on the income leg only.
    # Source amounts retain their declared currencies through conversion;
    # nested `valuation` records the price and FX actually used by the books.
    # Evidence only: never part of compute_id or a second accounting event.
    staking_reward: Optional[Dict[str, Any]] = None
    # The broker's own clock stamp ("2025-01-02 22:00:00 ET") on a trade
    # whose exchange trade date differs from it — an overnight-session US
    # fill, an ASX fill stamped in the US Eastern evening (tax-logic
    # CA-DATE-SESSION / US-DATE-SESSION). Evidence only: NOT part of compute_id,
    # left out of to_dict() when empty.
    broker_time: str = ''
    # The security's name from the broker's instrument list, when the
    # row's description is only the ticker (IB: "SAMPLE PLATFORMS INC-CDR"
    # beside description "SAMPLE") — read by the cross-listing lint to tell
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
    # The lots that closing trade closed, when the export lists them
    # (IB ClosedLot rows): "<open date> <qty> <cost>" per lot, joined by
    # ";", in broker_basis's currency ("invalid" when a lot row did not
    # read). Evidence for `find-missing-history --write-purchases`,
    # never booked; NOT part of compute_id, omitted when empty.
    broker_lots: str = ''
    # The commission refunds folded into this trade's cost (IB Commission
    # Adjustments, tax-logic CA-ACB-COMMREFUND): "<date> <amount>" per
    # refund, the cash it brought in the trade's own currency, joined by
    # "; " in date order. The cost is net of them, but the cash moved on
    # their own dates: the FX-on-cash ledger v2 reads them to walk the
    # trade's full commission on its date and each refund on its own
    # (backlog TJ-0748). NOT part of compute_id, omitted when empty.
    commission_refunds: str = ''
    # Contract size the parser read from the export (IB Financial
    # Instrument Information Multiplier: 100 per equity option, 1000 per
    # CL future or futures option, 0.1 per micro-crypto future) — kept
    # on option and futures rows only; 0 = not declared (audit S026-22).
    # NOT part of compute_id, omitted from to_dict() when 0.
    multiplier: float = 0.0
    # How a non-IB parser got `multiplier` on an option row: "assumed"
    # (the export does not state a contract's size; the standard 100
    # was used) or "derived" (the row's own money showed a mini's 10).
    # The engine says once per option root when it relied on an
    # assumed size (B10). NOT part of compute_id, omitted when empty.
    contract_size_basis: str = ''
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
    # fraction, boot), as "<amount> <CUR>[ @<paid>][; <amount> <CUR>...]"
    # — set by the corp-action emitters and read by fx-cash, which used to
    # tell cash from the description text (re-audit A2-1014); @<paid> is
    # cash paid after the row's date. Evidence only:
    # NOT part of compute_id, omitted from to_dict() when empty.
    corp_cash: str = ''
    # Why an ADJUST the parser booked as a cash return of capital carries
    # no cash of its own ('book-cost': RBC's year-end row, Value 0, whose
    # cash came with the distribution rows). Read by fx-cash (backlog
    # TJ-1033). Evidence only: NOT part of compute_id, omitted from
    # to_dict() when empty.
    no_cash: str = ''
    # The acquisition date of an OPENING lot (an opening balance from a
    # positions report, `type == OPENING_TYPE`): the US engine dates the
    # lot's holding period from it (US-OPEN-01); Canada pools the ACB
    # and only shows it (CA-OPEN-01). Its own text is also in the row's
    # description, so two lots that differ only by it keep two ids.
    # NOT part of compute_id, omitted from to_dict() when empty.
    lot_date: str = ''
    # The market value the broker states for a TRANSFER row (IB's
    # Transfers `Market Value`), in the row's currency: the fair market
    # value of an in-kind move between a taxable and a registered
    # account (lib/in_kind, CA-INKIND-06 / US-INKIND-03) — never a cost
    # of its own. Evidence only: NOT part of compute_id, omitted from
    # to_dict() when 0.
    market_value: float = 0.0
    # The id a parser gives the two legs of one broker CURRENCY journal
    # (Questrade's BRW "JOURNAL POSITION TO USD" / "FROM CAD" pair,
    # brokerages/questrade._plan_qt_journals): kept on the TRANSFER row
    # whether the account keeps its transfers in the books or aside, so
    # the run joins the two lines as one security (lib/cross_listings,
    # CA-XLIST-03) and the missing-history walks read the pair as a
    # journal. Evidence only: NOT part of compute_id, omitted from
    # to_dict() when empty.
    journal_pair: str = ''
    # Where a DATED EVENT row came from when no broker row states it
    # (lib/dated_events): "tt" (a .tt JOURNAL leg or RENAME line), "map"
    # (a legacy ticker.map dated RENAME line) or "ib-conid" (a ticker
    # change IB shows as one contract id under two symbols). Evidence
    # only: NOT part of compute_id, omitted from to_dict() when empty.
    event_source: str = ''
    # The currency of the listing a row is booked under when the row's
    # cash is in another (a Questrade reinvestment paid on the USD side
    # buying the account's TSX listing: brokerages/questrade._rei_listing).
    # The converted books convert the cash as usual; the native-currency
    # books restate the row in this currency at the day's rate
    # (taxjson_run._raw_align_adjust_currency), one currency per pool.
    # Evidence only: NOT part of compute_id, omitted when empty.
    listing_currency: str = ''
    # A plain-futures fill on the settlement basis (lib/futures.py,
    # type FUTURES_SETTLEMENT): what that account's own settlement
    # booked, as "<leg> <money> <pl> <CUR>[ <rate>]" — leg LO/LC/SO/SC
    # (a Long or Short position Opened or Closed in its account), the
    # part's native fill money (a buy's cost, a sell's proceeds), the
    # account's own native P/L (0 on an opening), the native currency,
    # and the rate the conversion applied. The Canadian engine re-averages
    # the closes across your taxable accounts from it (s.47, tax-logic
    # CA-FX-04; issue #76). NOT part of compute_id, omitted when empty.
    settle_native: str = ''
    # The currency the trade was made in, on a row the base-currency
    # books relabel (taxjson_convert_currency): its settlement calendar
    # (lib/dates.settlement_calendar — a TSX US-dollar unit settles only
    # on days both Canada and the US settle, 'CAD+USD') for the wash
    # radar's rescue and re-entry dates (backlog TJ-0713). Evidence
    # only: NOT part of compute_id, omitted when empty.
    trade_currency: str = ''
    # The fee of a crypto-to-crypto swap whose legs the export left
    # unpriced, as "<units> received" or "<units> spent" (in the coin
    # of the leg that received or spent it), set by the
    # parser on both legs when its priced rows count the fee in the
    # exchange's one value (a Coinbase Advanced Trade buy on a crypto-
    # quoted pair: the quote coin's fee; a Kraken ledger swap: the
    # received coin's fee). taxjson-fill-crypto adds it when it values
    # the swap from the received coin (_value_swaps_once, backlog
    # TJ-0981 / TJ-0991). Evidence only: NOT part of compute_id,
    # omitted when empty.
    swap_fee: str = ''

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
        # A trade read WITHOUT its quantity or net_amount key stays
        # without it when a pass-through tool (sort, merge, convert)
        # writes it out, so the gains computation still refuses it
        # (issue #14) instead of reading the written default 0.
        miss = getattr(self, '_missing_trade_fields', None)
        if miss:
            for k in miss[1]:
                if not d.get(k):
                    d.pop(k, None)
        return d


# The optional income facts on TaxTransaction (see the class): omitted
# from to_dict() when empty so every other row keeps its shape.
INCOME_FACT_FIELDS = ('record_date', 'ex_date', 'income_label',
                      'dealer_country', 'issuer_country')
# The other optional evidence fields, omitted from to_dict() when empty.
EVIDENCE_FIELDS = ('broker_time', 'security_name', 'open_close',
                   'broker_basis', 'broker_lots', 'multiplier',
                   'commission_refunds', 'contract_size_basis',
                   'source', 'source_key',
                   'source_account', 'exercise_of', 'corp_cash', 'no_cash',
                   'corp_parent_qty', 'lot_date', 'market_value', 'journal_pair',
                   'event_source', 'listing_currency', 'settle_native',
                   'staking_reward', 'trade_currency', 'swap_fee')


def validate_staking_reward(value, context='staking_reward'):
    """Validate optional versioned reward evidence without inventing facts.

    Null monetary/quantity values mean unknown, not zero. Evidence does not
    participate in tax arithmetic; malformed types must not make a reporting
    command silently display a known commission or a made-up valuation.
    """
    import math
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{context}: staking_reward must be an object or null")
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError(f"{context}: staking_reward schema_version must be 1")
    if type(value.get('commission_known')) is not bool:
        raise ValueError(f"{context}: staking_reward commission_known must be boolean")
    if value.get('kind') not in ('staking', 'other_reward'):
        raise ValueError(f"{context}: staking_reward kind must be staking or other_reward")
    if value.get('commission_method') not in (
            'withheld_from_reward', 'paid_in_other_asset', 'unknown'):
        raise ValueError(f"{context}: staking_reward has an unknown commission_method")
    for field in ('broker', 'source_file', 'source_type', 'source_timestamp',
                  'source_id', 'asset', 'fee_currency', 'valuation_currency'):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"{context}: staking_reward {field} must be a string")
    line = value.get('source_line')
    if line is not None and (type(line) is not int or line < 1):
        raise ValueError(f"{context}: staking_reward source_line must be a positive integer or null")
    for field in ('gross_quantity', 'fee_quantity', 'net_quantity',
                  'gross_value', 'fee_value', 'net_value', 'price'):
        v = value.get(field)
        if v is not None and (isinstance(v, bool)
                              or not isinstance(v, (int, float))
                              or not math.isfinite(v)):
            raise ValueError(f"{context}: staking_reward {field} must be a finite number or null")
        if v is not None and field != 'net_value' and v < 0:
            raise ValueError(f"{context}: staking_reward {field} must not be negative")
    valuation = value.get('valuation')
    if valuation is not None:
        if not isinstance(valuation, dict):
            raise ValueError(f"{context}: staking_reward valuation must be an object")
        for field in ('currency', 'date', 'source', 'target_currency',
                      'fx_date', 'fx_source', 'market_symbol'):
            if field in valuation and not isinstance(valuation[field], str):
                raise ValueError(f"{context}: staking_reward valuation {field} must be a string")
        for field in ('price', 'fx_rate'):
            v = valuation.get(field)
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float))
                                  or not math.isfinite(v) or v <= 0):
                raise ValueError(f"{context}: staking_reward valuation {field} must be positive or null")

# OCC option-symbol pattern: [F:|/|\]<base><yymmdd><C|P><strike-8d>[.<ext>]
# e.g. "SAMPLG250120C00150000.US", "ABC271217P00029000.TO", or
# "F:SAMPLX271116P00040000.US" for futures options.
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
    `F:SAMPLX250120P00053000.US` → `F:SAMPLX.US`, keeping futures positions
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


def receives_on_buy(tx) -> bool:
    """A plain-futures BUY at a negative price: it RECEIVES cash, so
    its cost is negative (the schema's exemption, audit A2-0302). The
    engines' abs() of a buy's amount made it a positive cost in the
    native view, while the settled (base) books were right."""
    sym = tx.symbol or ''
    return (float(tx.quantity or 0.0) > 0
            and float(tx.net_amount or 0.0) < 0
            and float(tx.price or 0.0) < 0
            and sym.startswith(('F:', '/', '\\'))
            and not is_option_symbol(sym))


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


def equity_option_size(opt_tx) -> float:
    """Units of the underlying one contract of the equity option
    `opt_tx` delivers: a ticker.map `MULT` line for the option (or its
    root) first; else the size the row declares, unless its parser
    only assumed it; else the standard 100 — ASSUMED, said once per
    option root (tax-logic: the 100-share contract is assumed when the
    export does not state the size; B10)."""
    from taxjson.lib import markets
    sym = str(getattr(opt_tx, 'symbol', '') or '')
    und = parse_option_underlying(sym) or ''
    root = und.split('.', 1)[0]
    ovr = markets.option_contract_size(sym, und)
    if ovr:
        return float(ovr)
    m = float(getattr(opt_tx, 'multiplier', 0.0) or 0.0)
    if m > 0 and getattr(opt_tx, 'contract_size_basis', '') != 'assumed':
        return m
    size = m if m > 0 else OPTION_CONTRACT_SHARES
    if root:
        markets.note_builtin(
            "mult", root,
            f"{root} options: the export does not state the contract size, "
            f"so {size:g} shares per contract is ASSUMED where it matters "
            f"(an exercise or assignment, replacement shares); for a mini "
            f"or an adjusted series add `MULT {root} N` to ticker.map.",
            rollup=(root if size == OPTION_CONTRACT_SHARES
                    else f"{root} ({size:g} shares)",))
    return size

# Actions that never touch a symbol's ACB / basis pool, so their currency
# is never checked against it (both engines' currency guards skip them:
# income rows, a TRANSFER kept as evidence, a SPLIT that carries no
# money). Every other action with a currency must match the pool's — the
# raw (native-currency) pass in `tjs run` tests the same set (audit
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


def _opening_option_buys(events, date_of, contract=None):
    """[(buy row, opening contracts)] for every option BUY that opens
    or adds to a long position. Only the OPENING part of a buy acquires
    a right (audit R1-181): a buy that closes a written option acquires
    nothing. The position before each buy is walked per (account,
    contract) in event order (stable: same-stamp rows keep the export's
    order). `contract(symbol)`: which symbols are walked (default: an
    option's; a warrant or right's for detect_right_replacement_matches,
    TJ-0952)."""
    def _when(ev):
        return (str(date_of(ev) or ''), str(getattr(ev, 'time', '') or ''))

    if contract is None:
        def contract(sym):
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
    engine's window basis), amount (negative), id, direction, and
    optionally `open_qty`: the loss shares no share purchase replaced
    (the engine's matches; each loss share is matched once, US-WASH-20),
    the most a call can still replace — a loss replaced in full is not
    flagged (backlog TJ-0467: the flag put the whole loss at risk).
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
    # A class-share root (SAMPLD for SAMPLD.B.TO, SAMPLCB for SAMPLC.B.US) names the
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
        if loss_qty > 1e-9 and loss.get('open_qty') is not None:
            # A share count at the engine's precision: the open part is
            # a money ratio's (backlog TJ-0634).
            need = min(loss_qty, max(0.0, share_qty(loss['open_qty'])))
            if need <= 1e-9:
                continue
        denied_qty = (loss_qty - need) if loss_qty > 1e-9 else 0.0
        by_contract: Dict[str, Dict[str, Any]] = {}
        for und_c, right, ev, opening in acqs:
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
                ev.symbol, {'shares': 0.0, 'first': None, 'contracts': 0.0,
                            'size': option_contract_size(ev)})
            rec['shares'] += take
            # The contracts the buy opened, whole: a call that covers
            # only part of its shares against this loss is still one
            # contract bought (backlog TJ-0619: "0.1 contract(s)").
            rec['contracts'] += opening
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
                # The contracts bought; covered_shares, how many of
                # their shares count against this loss (TJ-0619).
                'option_qty': rec['contracts'],
                'contract_size': rec['size'],
                # Share counts at the engine's precision (TJ-0634).
                'covered_shares': share_qty(rec['shares']),
                'at_risk_amount': round(float(loss['amount']) * frac, 2),
                # The loss shares share purchases already replaced.
                'replaced_qty': (share_qty(denied_qty)
                                 if denied_qty > 1e-9 else None),
                'held_at_window_end': held,
                'statute': statute_label,
            })
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


def detect_opening_lot_matches(loss_entries, events, *, date_of,
                               canonical=None, statute_label='',
                               window_days=30):
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
            if is_opening_row(ev) and getattr(ev, 'lot_date', '')
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
        emit_line(f"warning: {format_option_replacement_warning(w, country=country)}")


def format_option_replacement_warning(w, *, country: str) -> str:
    """One option/right-replacement flag as the run prints it (after
    "warning: ")."""
    if w['rule'] == 'opening_lot_vs_loss':
        return (
            f"opening-balance lot (warn-only, numbers unchanged): "
            f"{w['loss_symbol']} loss {w['loss_amount']:+,.2f} on "
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
        _cov = round(float(w['covered_shares']), 6)
        # The contracts bought, whole, then the shares of them that
        # count against this loss when that is not all of them (backlog
        # TJ-0619: "0.1 contract(s) x 100 shares").
        _part = ("" if abs(float(w['option_qty']) * _size - _cov) < 1e-6
                 else f": {_cov:g} against this loss")
        verdict = (f"up to {_cov:g} of the "
                   f"{w['loss_qty']:g} shares' loss "
                   f"({w['at_risk_amount']:+,.2f}) would be denied "
                   f"({w['option_qty']:g} contract(s) bought x "
                   f"{_size:g} shares, each share used once{_part}"
                   + (f"; {round(float(_rep), 6):g} of the loss shares "
                      f"are already replaced by shares bought in the "
                      f"window, and denied"
                      if _rep else "")
                   + ")")
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
    `#`-as-data case is safe.

    A text with no `#`, or one that is valid JSON as it stands (every
    `#` is then inside a string literal), has nothing to strip and is
    returned as is: the pass cost about ten times the parse of a large
    work/ file (TJ-0240)."""
    if '#' not in content:
        return content
    try:
        json.loads(content)
    except (ValueError, RecursionError):
        pass
    else:
        return content
    return _JSON_COMMENT_RE.sub(
        lambda m: '' if m.group(1) is not None else m.group(0),
        content,
    )


# Every TaxTransaction field that holds a calendar date (YYYY-MM-DD).
DATE_FIELDS = ('date', 'date_settle', 'lot_date', 'record_date', 'ex_date')

_LOOSE_DATE_RE = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})')


def canonical_date(value: Any, ctx: str, field: str = 'date') -> str:
    """`value` written as a canonical ISO date YYYY-MM-DD. An unpadded
    month or day ("2025-2-1") is padded; a value that is not a real
    calendar date in year-month-day order is a ValueError naming `ctx`
    and `field` (issue #5: the engines compare date strings)."""
    m = _LOOSE_DATE_RE.fullmatch(str(value).strip())
    try:
        if not m:
            raise ValueError
        return datetime(int(m.group(1)), int(m.group(2)),
                        int(m.group(3))).strftime('%Y-%m-%d')
    except ValueError:
        raise ValueError(
            f"{ctx}: impossible {field}={value!r} (not a real calendar "
            f"date written YYYY-MM-DD) — fix the input data.") from None


@functools.lru_cache(maxsize=1)
def _tx_field_names():
    """TaxTransaction's constructor keywords, read once (reading the
    signature for every row was a third of a large run: TJ-0105)."""
    import inspect
    return inspect.signature(TaxTransaction).parameters.keys()


def coerce_transaction_row(t, i: int, ctx_prefix: str) -> TaxTransaction:
    """One row's coercion + validation — the shared body of
    load_transactions and pipeline.load_stdin_transactions, so the
    stdin path cannot drift back to silently dropping rows or skipping
    the FUZZ #K hard guards. Handles TaxTransaction passthrough,
    mapping coercion, `qty` aliasing, known-field filtering, and the
    non-finite / impossible-date / SPLIT-ratio refusals."""
    valid_keys = _tx_field_names()
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
    if clean_t.get('staking_reward') is not None:
        validate_staking_reward(clean_t['staking_reward'], _ctx)
        if clean_t.get('action') != 'DIVIDEND':
            raise ValueError(f"{_ctx}: staking_reward belongs on the DIVIDEND income leg only")
    # Type funnel (stage-tools audit): every numeric field must be a
    # real number (a numeric STRING is coerced with float(); null,
    # bools, lists, non-numeric strings are refused) and every string
    # field must be a str (null falls back to the field's default).
    # Before this, `"net_amount": null` reached compute_id's float()
    # as a bare TypeError traceback, `"quantity": "10"` crashed the
    # phantom walk's arithmetic, and `"symbol": 0` crashed the engines
    # — none of them caught by the tools' ValueError handlers.
    for _fld in ('quantity', 'price', 'proceeds', 'commission', 'fee',
                 'net_amount', 'gross_amount', 'multiplier',
                 'market_value', 'corp_parent_qty'):
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
                 'broker_lots', 'exercise_of', 'corp_cash', 'no_cash',
                 'contract_size_basis',
                 'lot_date', 'journal_pair', 'event_source',
                 'listing_currency', 'settle_native',
                 'commission_refunds', 'trade_currency', 'swap_fee'):
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
    # The engines order rows by comparing date STRINGS, so every date is
    # written YYYY-MM-DD here: an accepted "2025-2-01" sorted after
    # "2025-10-01", which moved FIFO/ACB lots and turned a long sale
    # into a short cover (issue #5). An unpadded month or day is padded;
    # anything else that is not a real calendar date is refused.
    for _fld in DATE_FIELDS:
        _d = clean_t.get(_fld)
        if _d:
            clean_t[_fld] = canonical_date(_d, _ctx, _fld)
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
    # Where the row came from, for require_computable_rows' messages.
    tx._row_ctx = _ctx
    if _missing:
        tx._missing_trade_fields = (_ctx, _missing)
    return tx


# The actions that move a position or its cost: each names its security,
# so an empty symbol is refused where money is computed (issue #16). The
# cash rows (FEE, INTEREST, TAX) may stand alone, and so may an income row
# the engines book by its own amount.
SYMBOL_REQUIRED_ACTIONS = ('BUYSELL', 'ASSIGN', 'SPLIT', 'TRANSFER',
                           'ADJUST', 'OPENING_BALANCE', 'DISALLOW')


def _describe_row(t) -> str:
    """The row as the user can find it: the loader's file and index when
    it came through coerce_transaction_row, else its own fields."""
    ctx = getattr(t, '_row_ctx', None)
    if ctx:
        return ctx
    src = f"{t.source}: " if getattr(t, 'source', '') else ""
    return (f"{src}{t.action or '?'} row (id={t.id!r}, symbol="
            f"{t.symbol!r}, date={t.date!r})")


def require_computable_rows(*books) -> None:
    """The one input contract of the gains computation (issues #14, #16).
    Every engine entry (CanadaTaxRules/USATaxRules.compute_gains) and the
    preparation in front of it (pipeline.prepare_books) refuse, in the
    main book and the context books alike:

    * a BUYSELL/ASSIGN row that came in WITHOUT its quantity or
      net_amount key (coerce_transaction_row marks it; an explicit 0 is
      a real amount and stays legal) — the engine would book it at 0;
    * an action the engines do not book (it was skipped in silence);
    * a row whose date is empty (missing and null are refused when the
      row is read);
    * an empty symbol on a row that moves a position or its cost
      (SYMBOL_REQUIRED_ACTIONS).

    Raises InputContentError (a ValueError) naming the row."""
    from taxjson.lib.brokerages.schema import KNOWN_ACTIONS
    from taxjson.lib.cli_diag import InputContentError
    for book in books:
        for t in book or ():
            miss = getattr(t, '_missing_trade_fields', None)
            if miss:
                ctx, fields = miss
                raise InputContentError(
                    f"{ctx}: required field(s) {', '.join(fields)} missing "
                    f"on a {t.action} row — the engine would book it at 0. "
                    f"Fix the input data.")
            if t.action not in KNOWN_ACTIONS:
                raise InputContentError(
                    f"{_describe_row(t)}: unsupported action "
                    f"{t.action!r} — the engines book only "
                    f"{', '.join(sorted(KNOWN_ACTIONS))}. Fix the input "
                    f"data.")
            if not str(t.date or '').strip():
                raise InputContentError(
                    f"{_describe_row(t)}: required field date is empty — "
                    f"fix the input data.")
            if t.action in SYMBOL_REQUIRED_ACTIONS \
                    and not str(t.symbol or '').strip():
                raise InputContentError(
                    f"{_describe_row(t)}: required field symbol is empty "
                    f"on a {t.action} row — fix the input data.")


def carry_row_marks(src, dst) -> None:
    """Copy the loader's marks (the missing-trade-field marker and the
    row's file/index) from `src` to its rebuilt copy `dst`, so a tool
    that rebuilds a row cannot launder a missing amount into a 0."""
    for k in ('_missing_trade_fields', '_row_ctx'):
        v = getattr(src, k, None)
        if v:
            setattr(dst, k, v)


def require_trade_fields(transactions) -> None:
    """require_computable_rows on one list of rows (the name the CLIs
    called before issue #14 moved the check to the engine boundary)."""
    require_computable_rows(transactions)


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

    if isinstance(data, dict) and "transactions" not in data:
        # {"Transactions": [...]} (or any object without the key) was
        # read as an empty book: empty gains and inventory at exit 0
        # (issue #6). The stdin loader already refused it.
        keys = ", ".join(sorted(map(str, data))) or "none"
        raise ValueError(f"{path}: no \"transactions\" list (keys: "
                         f"{keys}) — expected a JSON object with a "
                         f"\"transactions\" list, or a bare list of rows")
    txs = data["transactions"] if isinstance(data, dict) else data
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
    a cheap option sale shows the commission RBC charged instead of a
    misleading 0.

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
    # (11.70 on a 30.00 buy showed as 0).
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
    if abs(q) <= pool_qty_eps(symbol, q):
        return 0.0
    mult = OPTION_CONTRACT_SHARES if is_option_symbol(symbol or '') else 1.0
    return float(amount) / (q * mult)


def _by_account_name(*books):
    """Each book with its accounts' rows in account-name order, one
    account's rows in their own order (a stable sort; None stays None).
    The engines' same-moment tie-break, and every first-wins step
    before it (one copy of a split two brokers dated apart, duplicate
    ids), then never depend on the order the accounts' books were
    merged in — taxjson.toml's (issue #31; CA-DATE-14 / US-DATE-13)."""
    return tuple(None if b is None else sorted(b, key=account_tie)
                 for b in books)


def _disambiguate_duplicate_ids(*books) -> None:
    """Give a row whose content (and so whose id) repeats an earlier
    row's a distinct id, in place, with one NOTE. The engines link a
    loss to its denial, and a replacement lot to its basis bump, by
    tx.id: two byte-identical rows shared one id, so only the first
    took its denial or bump and the rest vanished (audit R1-169, US;
    S069-21, Canada). `tjs run` never gets here with duplicates
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
        emit_line(f"NOTE: {renamed} row(s) repeat an earlier row exactly "
              f"(same date, time, symbol, quantity, price and amount); "
              f"each is booked as a separate trade. If they are "
              f"duplicates, drop them (taxjson-merge2 --dedup).")


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
    emit_line(f"warning: {len(undrained)} unconsumed option-assignment "
        f"adjustment(s) at end of {engine} gains run — {detail}. An ASSIGN "
        f"option leg staged this premium, but no stock trade in its "
        f"account could be paired with it: none on that symbol in the "
        f"assignment's direction from 3 days before to "
        f"{_MARKED_LEG_MAX_LAG_DAYS} days after the option row (missing "
        f"rows, a symbol mismatch, or a leg dated outside that window); "
        f"that premium is NOT reflected in any gain. Check the "
        f"underlying's buy/sell rows around the assignment date.")


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
    return q * equity_option_size(opt_tx)


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
    # Each leg's shares not yet claimed: one delivery of several
    # assignments' shares (two 100-share puts delivered as one marked
    # 200-share row) is the leg of each, up to its quantity (backlog
    # TJ-0838). A leg is shared only on strong identity (marked, or at
    # the option's strike), and only while its unclaimed shares cover
    # what the option still needs of its delivered quantity.
    left: Dict[str, float] = {}

    def _claim(o, l, q, size, strong) -> bool:
        rem = left.get(l.id, q)
        if l.id in left and not (
                strong and size is not None
                and rem >= size - covered.get(o.id, 0.0) - 1e-6):
            return False
        if rem <= 1e-6:
            return False
        got = rem if size is None else min(rem, size - covered.get(o.id, 0.0))
        left[l.id] = rem - got
        covered[o.id] = covered.get(o.id, 0.0) + got
        pairs.setdefault(o.id, []).append(l.id)
        return True
    for _sc, _p, o, l, q, size, strong in cands:
        if o.id in pairs:
            continue
        _claim(o, l, q, size, strong)
    for _sc, _p, o, l, q, size, strong in cands:
        if (not strong or o.id not in pairs or l.id in pairs[o.id]
                or size is None or covered[o.id] >= size - 1e-6):
            continue
        _claim(o, l, q, size, strong)
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


def _assign_moved_legs(stream, pairs) -> Dict[str, Any]:
    """{option id: its first stock leg} for each paired option ASSIGN
    that _place_assign_options moves in front of that earlier leg. The
    moved row stands for the leg's moment: a year-end snapshot or a
    dated cost timeline must not end the earlier day at it (a December
    delivery whose option-close posting is dated January 2, backlog
    TJ-0839)."""
    if not pairs:
        return {}
    pos = {t.id: i for i, t in enumerate(stream)}
    by_id = {t.id: t for t in stream}
    out: Dict[str, Any] = {}
    for oid, lids in pairs.items():
        first = min(lids, key=lambda lid: pos.get(lid, 0))
        if oid in pos and first in pos and pos[first] < pos[oid]:
            out[oid] = by_id[first]
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


def outside_1091(sym: str) -> bool:
    """A §1256 contract: a futures contract, an option on one, or a
    broad-based index option (lib/futures.section_1256_kind, the list
    Form 6781's rows come from). It is marked to market and outside the
    wash-sale rule: its loss is never disallowed (tax-logic US-WASH-18;
    TJ-0506: an index option's loss was washed, though Form 6781's list
    named it). US only: Canada's s.54 covers any property."""
    s = str(sym or '')
    if is_option_symbol(s):
        if _FUTURES_PREFIX_RE.match(parse_option_underlying(s) or ''):
            return True
    # A plain future is one unless the ticker.map declares it a
    # security future (SECFUT; section_1256_kind, TJ-0730).
    from taxjson.lib.futures import section_1256_kind
    return bool(section_1256_kind(s))


def _split_underlying(sym: str):
    """('SAMPLD', 'TO') for 'SAMPLD.TO'; ('F:SAMPLX', 'US') for 'F:SAMPLX.US'."""
    if '.' in sym:
        base, _, ext = sym.rpartition('.')
        if ext.isalpha() and ext.isupper() and len(ext) <= 3:
            return base, ext
    return sym, ''


def _root_matches_stock(root_base: str, stock_base: str) -> bool:
    """Does an option ROOT name this stock line? Montreal / OCC roots
    drop the share class ('SAMPLD' for SAMPLD.B, 'SAMPLCB' or 'SAMPLC' for SAMPLC.B)
    and OCC-adjusted roots carry a digit ('SAMPLE1' after a corporate
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
    drops the class of a class-share line ('SAMPLD' for SAMPLD.B, 'SAMPLCB' or
    'SAMPLC' for SAMPLC.B, 'ABC' for ABC.UN). Never a futures root and never
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
    on the same market: RBC books a class share's Montreal calls under the root
    SAMPLD (SAMPLD271217C00030000.TO) while the shares are SAMPLD.B.TO; OCC spells
    class B calls SAMPLCB. Used by both engines' call-replacement rules
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
    return equity_option_size(opt_tx)


def _make_assign_underlying_resolver(transactions, date_of, quiet=False):
    """Return resolve(option_tx) -> underlying stock symbol for an option
    ASSIGN, or None when no stock line in the option's own account
    matches (a cash-settled index option, or a missing stock leg).

    The option root is used as-is when that exact symbol trades as stock
    in the account. Otherwise the root is matched to the account's stock
    lines by class / futures-month / OCC-adjustment spelling
    (_root_matches_stock) and must name exactly ONE line that trades
    within _MARKED_LEG_MAX_LAG_DAYS of the assignment (audit R1-35,
    S019-01, S070-17, R1-176: SAMPLD for SAMPLD.B.TO, SAMPLCB for SAMPLC.B.US,
    F:SAMPLX for F:SAMPLXG6.US used to be treated as cash-settled, realizing the
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
                emit_line(f"note: {tx.symbol}: option root {und} resolved to "
                      f"{out}, the stock line this account trades at the "
                      f"assignment — the premium rolls into its "
                      f"cost/proceeds.")
        elif len(near) > 1:
            emit_line(f"warning: {tx.symbol}: option root {und} matches "
                  f"several stock lines traded at the assignment "
                  f"({', '.join(near)}) — treated as cash-settled. Map "
                  f"the option to its stock in ticker.map and re-run.")
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


def _s861_prior_sales(pool: Dict[str, Any], symbol: str,
                      records: List[Dict[str, Any]],
                      *, parent_qty: float = 0.0, account: str = ''
                      ) -> Optional[List[Tuple[Dict[str, Any], float]]]:
    """The taxable sales of an s.86.1 spin-off's original shares, as
    (record, shares of it): the shares the distribution was made on,
    which it reduces the cost of even when sold before it (TJ-0842).
    They are the last sold of `pool`'s last long holding (its
    `closed_holding` dates): walked back from the closing sale up to the
    `parent_qty` the event was paid on, so a trim from before the record
    date keeps its gain (TJ-1026). With no such count, the sales on the
    last sale's date. The event's own account's sales when it has any
    (one event per account, on one Canadian pool).

    None when there is none, or when one of them cannot simply take a
    lower cost: a loss (the superficial-loss solver has already read
    it), a denied amount, a missing purchase, a deemed or in-kind
    record."""
    held = pool.get('closed_holding')
    if not held:
        return None
    start, end = held[:2]
    recs = [r for r in records
            if r.get('symbol') == symbol and r.get('direction') == 'LONG'
            and start <= str(r.get('date') or '') <= end
            and not r.get('grant') and not r.get('_void')
            and float(r.get('qty') or 0) > 1e-12]
    recs = [r for r in recs if r.get('account') == account] or recs
    if not recs:
        return None
    if parent_qty > 0:
        out = []
        left = parent_qty
        for r in reversed(recs):
            if left <= 1e-9:
                break
            part = min(float(r['qty']), left)
            out.append((r, part))
            left -= part
    else:
        last = max(str(r.get('date') or '') for r in recs)
        out = [(r, float(r['qty'])) for r in recs
               if str(r.get('date') or '') == last]
    for r, _part in out:
        if (r.get('deemed') or r.get('tainted') or r.get('in_kind')
                or abs(float(r.get('disallowed') or 0)) > 0.005
                or float(r.get('gain') or 0) < -0.001):
            return None
    return out


def _s861_holding_moved(rows: Sequence[Any], held: Any, upto: Any,
                        symbol: str, other_scope_ids: Any) -> bool:
    """A position row of `symbol` in the taxable book after the sale
    that recorded `closed_holding`, up to `upto`: the pool has held
    shares since (drained another way: a cross to short and its cover,
    an own-account move out, a listing journal), so those dates name an
    older holding, not the shares the distribution was made on
    (TJ-1026)."""
    closing = held[2] if held and len(held) > 2 else None
    after = False
    for t in rows:
        if t is upto:
            return False
        if after and t.symbol == symbol and t.id not in other_scope_ids \
                and t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                 'OPENING_BALANCE') \
                and abs(float(t.quantity or 0.0)) > 1e-12:
            return True
        if id(t) == closing:
            after = True
    return False


def pool_qty_eps(symbol: str, *scales: float) -> float:
    """The quantity under which a Canada pool position (or a leftover)
    is zero: 1e-6 for shares and options; for a coin, float noise
    relative to `scales` (the position and the trade quantities)."""
    if not _is_coin_symbol(symbol):
        return _SHARE_QTY_EPS
    scale = max((abs(float(x)) for x in scales), default=0.0)
    return max(_COIN_QTY_ABS_EPS, _COIN_QTY_REL_EPS * scale)


# A position under the quantity tolerance that still carries this much
# cost (half a cent, a cent once rounded) is a holding, not rounding
# residue (tax-logic CA-ACB-01; docs/limits.md).
MATERIAL_COST = 0.005


def pool_is_flat(symbol: str, qty: float, cost: Any = 0.0) -> bool:
    """A pool with `qty` units and `cost` is no holding: no units, or
    fewer than the tolerance (pool_qty_eps) and less than MATERIAL_COST
    of cost. A sub-micro position bought (or consolidated) at a real
    cost is held: it shows in the inventory and its cost amount counts
    (backlog TJ-0649)."""
    q = float(qty or 0.0)
    if not q:
        return True
    if abs(q) > pool_qty_eps(symbol, q):
        return False
    return abs(float(cost or 0.0)) < MATERIAL_COST


# A share count at the share pools' precision (_SHARE_QTY_EPS, six
# decimals).
_SHARE_QTY_PLACES = 6


def share_qty(q: float) -> float:
    """`q` shares at the engine's share precision (_SHARE_QTY_PLACES): a
    count derived from a money ratio (a loss's part no purchase replaced,
    100 x (1 - 90/100) = 9.999999999999998) is saved and shown as 10
    (backlog TJ-0634)."""
    return round(float(q), _SHARE_QTY_PLACES) + 0.0


def _currency_mismatch_advice(tx, pool_cur: str) -> str:
    """The fix a currency-mismatch error names (audit A2-0055/0191/0204:
    it named an underscore tool a project user never runs)."""
    if tx.action in ('ADJUST', 'DISALLOW'):
        return (f"Enter the amount in the pool's currency ({pool_cur}) — a "
                f"slip's CAD figure on a {pool_cur} listing is converted at "
                f"the row's date — or convert the whole book first "
                f"(taxjson-convert-currency --to <base>). `tjs run` "
                f"does this itself.")
    return ("One pool holds one currency: convert the book first "
            "(taxjson-convert-currency --to <base>), or keep the listings "
            "apart (.TO vs .US). `tjs run` converts before the "
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
    `late=separate` records the other case). `tjs renames` lists
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
            emit_line(f"warning: {t.symbol} trades on {d}, after its "
                  f"rename to {new} on {d_r}: renames are dated, so "
                  f"taxjson treats these rows as a DIFFERENT security "
                  f"from {new} for {rule_text}. If the broker still books "
                  f"the renamed shares under {t.symbol}, declare it in "
                  f"ticker.map (RENAME {t.symbol} {new} {d_r} late=fold); "
                  f"if another company now uses the ticker, `late=separate`"
                  f" (`tjs renames`).")


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


# The Canada solver's superficial-loss scans ask, for each loss sale,
# about the rows of ONE identity class inside ONE +/-30-day window.
# Walking every row of the book for every sale made a run cost loss
# sales x rows (TJ-0105: 50,000 rows took half an hour; TJ-0060: 8,000
# rows of one symbol took minutes). The indexes below hand each scan the
# same rows, in the same order, that the full walk kept, so every sum
# adds the same numbers in the same order and every result is unchanged.

@functools.lru_cache(maxsize=8192)
def _iso_ordinal(s: str) -> int:
    """The day number of `datetime.strptime(s, '%Y-%m-%d')`, cached (the
    scans ask for the same few hundred dates again and again)."""
    return datetime.strptime(s, '%Y-%m-%d').toordinal()


class _DatedRows:
    """Rows of one key in their book order, found by date window.
    `date_of(row)` is the date the scan tests, parsed as the scan parses
    it. `ok` is False when a date does not parse: the caller then walks
    the book as before, so it fails (or skips the row) where it did."""

    __slots__ = ('rows', 'ok', '_order', '_ords')

    def __init__(self, rows, date_of):
        self.rows = rows
        self.ok = True
        try:
            ords = [_iso_ordinal(date_of(t)) for t in rows]
        except (TypeError, ValueError):
            self.ok = False
            return
        self._order = sorted(range(len(rows)), key=ords.__getitem__)
        self._ords = [ords[i] for i in self._order]

    def within(self, center: int, days: int) -> list:
        """The rows dated `center` +/- `days` (inclusive), in book order."""
        import bisect
        lo = bisect.bisect_left(self._ords, center - days)
        hi = bisect.bisect_right(self._ords, center + days)
        return [self.rows[i] for i in sorted(self._order[lo:hi])]


class _RepIndex:
    """The US engine's replacement records of one class (one list, fixed
    once the pre-pass built it), indexed so the §1091 scans read only
    the records they keep (TJ-0238: every loss walked and date-parsed
    every record of its class, so a book cost losses x rows).

    `within(center, before, after)` is the records dated `center -
    before` to `center + after` (day numbers, inclusive) in list order;
    a record whose date does not parse is left out, as the walk skipped
    it. `of_tx(id)` is the records of one row id, in list order.
    `pos(rep)` is a record's place in the list order. `holding(ref_key,
    lot)` is the records whose `ref_key` is `lot`, in list order: the
    walk's own filter over the records `note_ref` was told point there
    (every assignment of a lot reference goes through `note_ref`).
    `add_after(parent, rep)` adds a record split off `parent` (part of
    its lot went on its own, TJ-0490): in list order it comes right
    behind `parent` and the records split off it before."""

    __slots__ = ('reps', '_ords', '_order', '_pos', '_keys', '_kids',
                 '_by_tx', '_refs')

    def __init__(self, reps):
        self.reps = reps
        keyed = []
        for i, r in enumerate(reps):
            try:
                keyed.append((_iso_ordinal(r['date']), i))
            except (TypeError, ValueError):
                continue
        keyed.sort()
        self._ords = [o for o, _ in keyed]
        self._order = [i for _, i in keyed]
        self._pos = {id(r): i for i, r in enumerate(reps)}
        # Each record's place in the list order: (i,) for the records
        # the pre-pass made; a split-off record's is its parent's plus
        # its own number, so it sorts right behind the parent's family.
        self._keys: List[tuple] = [(i,) for i in range(len(reps))]
        self._kids: Dict[int, int] = {}
        self._by_tx: Dict[Any, list] = {}
        for r in reps:
            self._by_tx.setdefault(r['tx'].id, []).append(r)
        self._refs: Dict[tuple, list] = {}

    def within(self, center: int, before: int, after: int) -> list:
        import bisect
        lo = bisect.bisect_left(self._ords, center - before)
        hi = bisect.bisect_right(self._ords, center + after)
        return [self.reps[i] for i in sorted(self._order[lo:hi],
                                             key=self._keys.__getitem__)]

    def of_tx(self, tx_id) -> list:
        return self._by_tx.get(tx_id, [])

    def pos(self, rep) -> tuple:
        i = self._pos.get(id(rep))
        return self._keys[i] if i is not None else (0,)

    def add_after(self, parent, rep) -> None:
        import bisect
        pi = self._pos.get(id(parent))
        if pi is None:
            return
        n = self._kids.get(pi, 0) + 1
        self._kids[pi] = n
        i = len(self.reps)
        self.reps.append(rep)
        self._pos[id(rep)] = i
        self._keys.append(self._keys[pi] + (n,))
        try:
            o = _iso_ordinal(rep['date'])
        except (TypeError, ValueError):
            o = None
        if o is not None:
            k = bisect.bisect_right(self._ords, o)
            self._ords.insert(k, o)
            self._order.insert(k, i)
        self._by_tx.setdefault(rep['tx'].id, []).append(rep)

    def note_ref(self, rep, ref_key: str, lot) -> None:
        if lot is None:
            return
        bucket = self._refs.setdefault((ref_key, id(lot)), [])
        if not any(r is rep for r in bucket):
            bucket.append(rep)

    def holding(self, ref_key: str, lot) -> list:
        bucket = self._refs.get((ref_key, id(lot)))
        if not bucket:
            return []
        live = [r for r in bucket if r.get(ref_key) is lot]
        if len(live) != len(bucket):
            bucket[:] = live
        live.sort(key=self.pos)
        return live


def _own_move_held(loss_date, end_window_date: str, rows, triggers,
                   call_ids, holder, opening_qty, gone_by_end, units,
                   sort_date) -> Optional[Dict[Any, float]]:
    """Per holder of the triggers (the account that ACQUIRED them), the
    units it acquired in a loss's window that one of the holders still
    holds at its end, when a move of your own between two of the
    accounts (balance-only TRANSFER rows of type `own_account_move`,
    lib/pipeline) lies in the window; else None (the caller's
    min(acquired, held) per holder stands — the same figure without
    such a move).

    The class's balance rows dated by day 30 are walked in date order,
    each holder's units in parts: older ones, and the ones acquired in
    the window (`triggers`, their opening units) by the holder that
    acquired them. A sale or a move out takes the older units first
    (FIFO); a move in takes the parts its out legs moved (the other
    account's leg of the same units, the closest date; else what is left
    of the other accounts' legs, pro rata over each out leg's parts) —
    so a replacement bought in one registered account and moved to
    another is still held (backlog TJ-0654, TJ-1102), and older units
    moved in back nothing. `units(t, q)`: q of row t in the loss's units;
    `sort_date(t)`: the date the engine books row t on."""
    from taxjson.lib.pipeline import (OWN_MOVE_TYPE,
                                      _TRANSFER_SEGMENT_MAX_SPAN_DAYS)

    def _sd(t) -> str:
        return sort_date(t)

    def _own(t) -> bool:
        return (t.action == 'TRANSFER'
                and getattr(t, 'type', '') == OWN_MOVE_TYPE)

    def _leg(t) -> bool:
        # A leg of a move of your own: between two registered accounts,
        # or a taxable lot move (LOT_MOVE_TYPE, one holder: its parts
        # come back as they left).
        return _own(t) or (t.action == 'TRANSFER'
                           and getattr(t, 'type', '') == LOT_MOVE_TYPE)

    start = (loss_date - timedelta(days=30)).strftime('%Y-%m-%d')
    if not any(_own(t) and start <= _sd(t) <= end_window_date
               for t in rows):
        return None
    trig = {t.id: units(t, opening_qty(t)) for t in triggers
            if t.id not in call_ids}
    walk = sorted((t for t in rows
                   if _sd(t) <= end_window_date and not gone_by_end(t)),
                  key=_sd)
    old: Dict[Any, float] = {}
    # holder -> {the holder that acquired them: units acquired in the
    # window}
    new: Dict[Any, Dict[Any, float]] = {}
    # The legs paired before the walk: the same units first, the closest
    # date; then what is left, in parts (a move that posts in parts, or
    # gathers two senders' units in one leg: the pipeline nets a whole
    # segment that sums to zero, backlog TJ-1102), as close as one
    # segment's legs can be.
    legs = [t for t in walk if _leg(t)]
    left = {id(t): abs(units(t, t.quantity)) for t in legs}
    links: Dict[int, List[Tuple[Any, float]]] = {}

    def _gap(x, y) -> int:
        return abs((datetime.strptime(_sd(x), '%Y-%m-%d')
                    - datetime.strptime(_sd(y), '%Y-%m-%d')).days)

    def _pairs(same: bool) -> List[Tuple[Any, Any]]:
        found = []
        for n, o in enumerate(legs):
            for m, i in enumerate(legs):
                if (o.quantity < 0 < i.quantity and o.account != i.account
                        and getattr(o, 'type', '') == getattr(i, 'type', '')
                        and (not same or abs(left[id(o)] - left[id(i)])
                             <= 1e-9 * max(1.0, left[id(o)]))
                        and (same or _gap(o, i)
                             <= _TRANSFER_SEGMENT_MAX_SPAN_DAYS)):
                    found.append((_gap(o, i), n, m, o, i))
        return [(o, i) for *_rank, o, i in sorted(found, key=lambda e: e[:3])]

    for same in (True, False):
        for o, i in _pairs(same):
            n = min(left[id(o)], left[id(i)])
            if n > 1e-9:
                left[id(o)] -= n
                left[id(i)] -= n
                links.setdefault(id(o), []).append((i, n))
                links.setdefault(id(i), []).append((o, n))
    landed: set = set()
    sent: Dict[Tuple[int, int], Dict[Any, float]] = {}

    def _add(h, parts: Dict[Any, float]) -> None:
        mine = new.setdefault(h, {})
        for o, n in parts.items():
            mine[o] = mine.get(o, 0.0) + n
            old[h] = old.get(h, 0.0) - n

    for t in walk:
        h = holder(t)
        q = units(t, t.quantity)
        if t.id in trig:
            n = min(max(0.0, trig[t.id]), max(0.0, q))
            old[h] = old.get(h, 0.0) + q
            _add(h, {h: n})
            continue
        if q >= 0:
            old[h] = old.get(h, 0.0) + q
            if _leg(t):
                landed.add(id(t))
                for o, _n in links.get(id(t), ()):
                    _add(h, sent.pop((id(o), id(t)), {}))
            continue
        take = -q
        rest = take - min(take, max(0.0, old.get(h, 0.0)))
        moved: Dict[Any, float] = {}
        for o in sorted(new.get(h, {}), key=repr):
            if rest <= 0.0:
                break
            n = min(rest, max(0.0, new[h][o]))
            if n > 0.0:
                new[h][o] -= n
                moved[o] = n
                rest -= n
        old[h] = old.get(h, 0.0) - (take - sum(moved.values()))
        if _leg(t):
            for i, n in links.get(id(t), ()):
                share = {o: x * n / take for o, x in moved.items()}
                if id(i) in landed:
                    _add(holder(i), share)
                else:
                    sent[(id(t), id(i))] = share
    out: Dict[Any, float] = {}
    for h, parts in new.items():
        total = sum(max(0.0, n) for n in parts.values())
        # A holder short of its older units holds less than its parts.
        keep = max(0.0, min(total, old.get(h, 0.0) + total))
        for o, n in parts.items():
            if total > 0.0 and n > 0.0:
                out[o] = out.get(o, 0.0) + n * keep / total
    for t in triggers:
        out.setdefault(holder(t), 0.0)
    return out


class _ClassBalances:
    """One identity class's balance rows (BUYSELL, ASSIGN, TRANSFER,
    OPENING_BALANCE) in processing order, for the day-30 balance walks.

    `prefix(end)` is the number of leading rows whose sort date is on or
    before `end` — None when the rows are not in sort-date order (then
    the caller walks them all, as before). When the class is one raw
    symbol that no split or rename acts on, every row's quantity is
    already in the loss's units (the lineage factor is exactly 1.0), and
    the per-holder running sums answer a walk in one lookup: each is the
    walk's own sum, added in the walk's order from 0.0."""

    __slots__ = ('rows', 'dates', 'sorted', 'unit_symbol', '_hpos',
                 '_hsum', '_tpos', '_tsum', '_cum', '_int_exact')

    def __init__(self, rows, sort_date, holder_of, is_taxable,
                 split_symbols):
        self.rows = rows
        self.dates = [sort_date(t) for t in rows]
        self.sorted = all(a <= b for a, b in zip(self.dates,
                                                 self.dates[1:]))
        syms = {t.symbol for t in rows}
        self.unit_symbol = None
        if (self.sorted and len(syms) == 1
                and not (syms & split_symbols)):
            self.unit_symbol = next(iter(syms))
            self._hpos: Dict[Any, list] = {}
            self._hsum: Dict[Any, list] = {}
            self._tpos: list = []
            self._tsum: list = []
            self._cum: list = []
            acc = 0.0
            exact = True
            mag = 0.0
            for i, t in enumerate(rows):
                u = t.quantity * 1.0
                h = holder_of(t)
                hs = self._hsum.setdefault(h, [])
                self._hpos.setdefault(h, []).append(i)
                hs.append((hs[-1] if hs else 0.0) + u)
                if is_taxable(t):
                    self._tpos.append(i)
                    self._tsum.append((self._tsum[-1] if self._tsum
                                       else 0.0) + u)
                acc += u
                self._cum.append(acc)
                mag += abs(u)
                if exact and not (float(u).is_integer() and mag < 2.0**52):
                    exact = False
            self._int_exact = exact

    def prefix(self, end: str) -> Optional[int]:
        if not self.sorted:
            return None
        import bisect
        return bisect.bisect_right(self.dates, end)

    def holder_sums(self, k: int) -> Dict[Any, float]:
        """{holder: the sum of its units among the first k rows}, for
        each holder with a row there (the walk's dict, key for key)."""
        import bisect
        out: Dict[Any, float] = {}
        for h, pos in self._hpos.items():
            j = bisect.bisect_left(pos, k)
            if j:
                out[h] = self._hsum[h][j - 1]
        return out

    def taxable_sum(self, k: int) -> Tuple[int, float]:
        """(how many taxable rows among the first k, their running sum)."""
        import bisect
        j = bisect.bisect_left(self._tpos, k)
        return j, (self._tsum[j - 1] if j else 0.0)

    def first_taxable(self):
        return self.rows[self._tpos[0]]

    def total(self, k: int):
        """`sum()` of the first k rows' units. Whole units add exactly
        whatever the order or the algorithm (Python 3.12's compensated
        sum() included), so the running sum is sum()'s own result;
        otherwise sum() runs over the same values in the same order."""
        if not k:
            return 0
        if self._int_exact:
            return self._cum[k - 1]
        import itertools
        return sum(itertools.islice((t.quantity * 1.0 for t in self.rows),
                                    k))


class _WashAdjustIndex:
    """The solver's ADJUST rows by each `WASH_<loss id>__` prefix their
    id starts with, in list order: `of(x)` is exactly the rows `v` of
    the list with v.action == 'ADJUST' and
    v.id.startswith(f"WASH_{x}__")."""

    def __init__(self, rows=()):
        self._by: Dict[str, list] = {}
        for v in rows:
            self.add(v)

    def add(self, v) -> None:
        s = v.id
        if (v.action != 'ADJUST' or not isinstance(s, str)
                or not s.startswith('WASH_')):
            return
        p = s.find('__', 5)
        while p != -1:
            self._by.setdefault(s[:p + 2], []).append(v)
            p = s.find('__', p + 1)

    def of(self, loss_id) -> list:
        return self._by.get(f"WASH_{loss_id}__", [])


def _add_wash_cut(pool, cut: float, late: float) -> None:
    """Add `cut` to the pool's `wash_cut` (what a return of capital took
    of the denied-loss tally), `late` being its `cut_late`: the premium
    delivered into the pool since the cut, per unit of the cut, which
    scales with it. That premium was not in the ACB the cut read
    (backlog TJ-1309)."""
    had = pool.get('wash_cut', 0.0)
    pool['cut_late'] = ((pool.get('cut_late', 0.0) * had + late * cut)
                        / (had + cut))
    pool['wash_cut'] = had + cut


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
    it stays undrained and the end-of-run warning names it.

    An assignment dated on or before an OPENING snapshot of its
    underlying in its account, with no stock leg of that symbol left
    up to the snapshot day (lib/opening cut them), stages nothing: the
    snapshot's cost already holds its premium, which neither the
    snapshot row nor a later purchase may take again (backlog
    TJ-0962) — unless its own leg dated after the snapshot is marked, or
    is a purchase at the strike the snapshot is too small to hold, which
    then does not hold it (TJ-1167, TJ-1297)."""

    def __init__(self, stream, is_leg, pairs=None):
        self._pos: Dict[int, int] = {}
        self._id_pos: Dict[str, int] = {}
        self._legs: Dict[Any, list] = {}
        # A leg's id by its stream position (an unsized warrant's
        # same-day deliveries, _same_day_later; TJ-0954).
        self._leg_id: Dict[int, Any] = {}
        self._leg_px: Dict[int, float] = {}
        self._snap: Dict[Any, str] = {}
        self._snap_qty: Dict[Any, float] = {}
        self._kept_days: Dict[Any, list] = {}
        # A stock row by its id (a paired leg dated after a snapshot,
        # _delivered_after_snapshot; TJ-1167).
        self._leg_row: Dict[Any, Any] = {}
        for i, t in enumerate(stream):
            self._pos[id(t)] = i
            if t.id is not None:
                self._id_pos.setdefault(t.id, i)
            if is_opening_row(t):
                k = (t.account, t.symbol)
                self._snap[k] = max(self._snap.get(k, ''),
                                    (t.date or '')[:10])
                self._snap_qty[k] = (self._snap_qty.get(k, 0.0)
                                     + float(t.quantity or 0.0))
            elif (t.action in ('BUYSELL', 'ASSIGN')
                    and not is_option_symbol(t.symbol)):
                self._kept_days.setdefault((t.account, t.symbol), []).append(
                    (t.date or '')[:10])
                if t.id is not None:
                    self._leg_row.setdefault(t.id, t)
            if is_leg(t):
                self._legs.setdefault((t.account, t.symbol), []).append(
                    (i, t.date or '', float(t.quantity or 0.0)))
                self._leg_id[i] = t.id
                self._leg_px[i] = (float(t.price or 0.0)
                                   or abs(float(t.net_amount or 0.0)
                                          / float(t.quantity or 1.0)))
        self._pairs = {o: list(ls) for o, ls in (pairs or {}).items()}
        # A leg's own assignments, in staging order (one delivery may
        # carry several: _pair_assign_legs, TJ-0838).
        self._leg_owners: Dict[str, list] = {}
        for o, ls in self._pairs.items():
            for l in ls:
                self._leg_owners.setdefault(l, []).append(o)
        self._e: Dict[Any, list] = {}
        self.last_deferred = 0.0
        self.last_tainted = False

    _direction = staticmethod(_assign_direction)

    def stage(self, opt_tx, underlying: str, amount: float,
              deferred: float = 0.0, tainted: bool = False) -> None:
        """`deferred`: the part of `amount` that is an earlier
        superficial loss added to the option's cost (its pool's
        deferred_wash share): the stock leg's pool tallies it, so the
        economic P/L views count that loss once (backlog TJ-0061).
        `tainted`: the contracts were held at an unknown cost (an
        OPENING cost=unknown): `amount` is not their premium, and the
        stock leg that takes it carries the unknown cost on
        (`last_tainted`; backlog TJ-0947)."""
        key = (opt_tx.account, underlying)
        if (self._in_snapshot(key, (opt_tx.date or '')[:10])
                and not self._delivered_after_snapshot(opt_tx, key)):
            return
        lst = self._e.setdefault(key, [])
        for e in lst:
            if e['src'] == opt_tx.id:
                e['amt'] += amount
                e['dw'] += deferred
                e['tainted'] = e['tainted'] or bool(tainted)
                return
        shares = _assign_delivery_shares(opt_tx)
        lst.append({'src': opt_tx.id, 'amt': amount, 'dw': deferred,
                    'tainted': bool(tainted),
                    'sym': opt_tx.symbol or '', 'und': underlying,
                    'dir': self._direction(opt_tx),
                    'shares': shares if shares and shares > 1e-9 else None,
                    'contracts': abs(float(opt_tx.quantity or 0.0)),
                    'date': opt_tx.date or '',
                    'legs': self._pairs.get(opt_tx.id, [])})

    def _in_snapshot(self, key, day: str) -> bool:
        """Does an OPENING snapshot of `key` (account, underlying), on or
        after `day`, hold this assignment's delivery (no stock leg of
        it kept from `day` to the snapshot day)? TJ-0962."""
        snap = self._snap.get(key)
        if not snap or not day or day > snap:
            return False
        return not any(day <= d <= snap
                       for d in self._kept_days.get(key, ()))

    def _delivered_after_snapshot(self, opt_tx, key) -> bool:
        """Is one of this assignment's own legs dated after the snapshot
        of `key`, by strong identity? The snapshot does not hold those
        shares, so the premium is theirs (TJ-1167). A marked ASSIGN leg
        is. A plain row at the option's strike is only when it is a
        purchase and the snapshot holds fewer shares than the assignment
        delivers: a snapshot that can hold the delivery may have replaced
        its row, and then an ordinary trade at the strike takes nothing
        (TJ-0962, TJ-1297). Nor does a plain purchase just after it,
        merely of the delivered size."""
        snap = self._snap.get(key, '')
        size = _assign_delivery_shares(opt_tx)
        strike = (None if (exercise_target(opt_tx) or size is None
                           or self._direction(opt_tx) != 1
                           or self._snap_qty.get(key, 0.0) >= size - 1e-6)
                  else parse_option_strike(opt_tx.symbol))
        for l in self._pairs.get(opt_tx.id, ()):
            leg = self._leg_row.get(l)
            if leg is None or (leg.date or '')[:10] <= snap:
                continue
            if leg.action == 'ASSIGN' or (
                    strike and strike > 0
                    and abs(float(leg.price or 0.0) - strike)
                    <= max(0.005, 1e-6 * strike)):
                return True
        return False

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

    def _same_day_later(self, e, acct, sym, idx, date, need) -> float:
        """Shares of the later legs on `date` in the entry's direction
        that no other assignment owns: the rest of a delivery in parts
        when the entry's size is unknown (a warrant exercised with no
        shares per warrant stated; TJ-0954). The parts of one delivery
        are at one price, with no sale of the shares between them, and
        come to no more shares than the warrants (a larger size is
        stated, `x<n>`): the first row that cannot be one ends them, so
        a market trade of the day keeps its own cost (TJ-1190)."""
        tot = 0.0
        room = e['contracts'] - e.get('took', 0.0) - need
        px = self._leg_px.get(idx, 0.0)
        for (i, d, q) in self._legs.get((acct, sym), ()):
            if i <= idx or d != date or abs(q) < 1e-12:
                continue
            if e['dir'] is not None and (1 if q > 0 else -1) != e['dir']:
                break
            owners = self._leg_owners.get(self._leg_id.get(i), ())
            if owners and e['src'] not in owners:
                continue
            leg_px = self._leg_px.get(i, 0.0)
            if (tot + abs(q) > room + 1e-9
                    or (px > 0 and leg_px > 0
                        and abs(leg_px - px) > 0.02 * px)):
                break
            tot += abs(q)
        return tot

    def _take_entry(self, lst, e, need, later,
                    rest_today: float = 0.0) -> Tuple[float, float]:
        """Take up to `need` shares of entry `e`: (amount, shares); its
        deferral share adds to `last_deferred`, its unknown cost sets
        `last_tainted`. `rest_today`: for an entry of unknown size, the
        shares of the same-day deliveries still to come (_same_day_later):
        the amount is shared by quantity with them, never all taken by
        the first part (TJ-0954)."""
        if e.get('tainted'):
            self.last_tainted = True
        if e['shares'] is None and rest_today > 1e-9 and need > 1e-9:
            frac = need / (need + rest_today)
            part = e['amt'] * frac
            dw = e['dw'] * frac
            e['amt'] -= part
            e['dw'] -= dw
            e['took'] = e.get('took', 0.0) + need
            self.last_deferred += dw
            if not e.get('split_said'):
                e['split_said'] = True
                emit_line(
                    f"warning: ATTENTION: {e['sym']}: the exercise on "
                    f"{e['date']} states no shares per warrant, so its "
                    f"cost is shared by quantity among the "
                    f"{need + rest_today:g} {e['und']} shares delivered "
                    f"that day; if those are not all its shares, give "
                    f"the exercise row its size (`x<shares per "
                    f"warrant>`).")
            return part, need
        if e['shares'] is None:
            lst.remove(e)
            self.last_deferred += e['dw']
            return e['amt'], 0.0
        take_sh = min(need, e['shares'])
        if take_sh >= e['shares'] - 1e-9 or not later:
            lst.remove(e)
            self.last_deferred += e['dw']
            return e['amt'], take_sh
        part = e['amt'] * take_sh / e['shares']
        dw = e['dw'] * take_sh / e['shares']
        e['amt'] -= part
        e['dw'] -= dw
        e['shares'] -= take_sh
        self.last_deferred += dw
        return part, take_sh

    def take(self, tx) -> float:
        """The staged amount this stock trade absorbs; the deferral part
        of it in `last_deferred`; `last_tainted` when any of it is an
        unknown-cost option's (TJ-0947)."""
        self.last_deferred = 0.0
        self.last_tainted = False
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
        owners = self._leg_owners.get(tx.id, ())
        if owners:
            for e in [e for e in lst if e['src'] in owners]:
                later = [l for l in e['legs']
                         if self._id_pos.get(l, -1) > idx]
                amt, took = self._take_entry(
                    lst, e, need, later,
                    self._same_day_later(e, tx.account, tx.symbol, idx,
                                         date, need)
                    if e['shares'] is None else 0.0)
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
            amt, took = self._take_entry(
                lst, e, need, later,
                self._same_day_later(e, tx.account, tx.symbol, idx, date,
                                     need)
                if e['shares'] is None else 0.0)
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
            # (TRANSFER: own-account move legs and a native book's
            # listing journal legs only — the callers pass no other.)
            k = rkey(t.symbol)
            expected[k] = expected.get(k, 0.0) + t.quantity

    actual: Dict[str, float] = {}
    for sym, qty in actual_qty_by_symbol.items():
        k = rkey(sym)
        actual[k] = actual.get(k, 0.0) + qty

    for k in sorted(set(expected) | set(actual)):
        e, a = expected.get(k, 0.0), actual.get(k, 0.0)
        if abs(e - a) > 1e-3:
            emit_line(f"warning: conservation: {engine_label} share-count "
                f"mismatch for {k}: replaying the transactions gives "
                f"{e:.4f} but the engine's inventory holds {a:.4f} "
                f"(delta {a - e:+.4f}). The gains for this symbol are "
                f"NOT trustworthy — this indicates an engine ordering/"
                f"split/opening-balance bug, not bad input data.")


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
        if (pool_is_flat(sym, pool['qty'], pool['total_cost'])
                and abs(float(pool['total_cost'])) > 0.02):
            emit_line(f"warning: conservation: {sym} pool is EMPTY but carries "
                f"{float(pool['total_cost']):.2f} of stranded basis — "
                f"either an engine chunk-math bug or an ADJUST/ROC row "
                f"posted after the position was fully closed (the latter "
                f"is a taxable event needing manual review).")


def _dedupe_corporate_splits(txs: List[TaxTransaction], seen: set) -> List[TaxTransaction]:
    """Drop repeated SPLIT rows for the same corporate event.

    A stock split is a property of the SECURITY, not of an account: every
    brokerage parser emits its own SPLIT row per account, and their ids differ
    (account/description feed compute_id), so `--dedup` can't collapse them.
    On merged multi-account input — one account fed by several brokers, or the
    combined sheltered context file — the same event then arrives N times and
    the engines' symbol-global pools/walks get scaled by ratio**N (e.g. two
    accounts holding one stock through its split doubled the pool).

    Keyed on (symbol, date, ratio, symbol_new); `seen` is shared across the
    taxable/sheltered/affiliated lists so a split present in more than one
    list is still applied exactly once. Limitation: two brokers reporting
    slightly different ratios for the same event (rounding) won't collapse.

    Delegates to SplitTimeline.dedupe — the one definition of split-event
    identity (see lib/corporate_timeline.py).
    """
    return SplitTimeline.dedupe(txs, seen)


def _rename_ratio_groups(lists) -> Dict[tuple, list]:
    """The rename-SPLIT rows of each corporate event, keyed (symbol,
    earliest date, target) -> [(list index, row index, row)]. One merger
    booked by two brokers on different dates (IB 06-11, RBC 06-15) is
    ONE event: rows of a (symbol, target) pair within
    SPLIT_DATE_WINDOW_DAYS of each other form one group (audit S071-01
    — an exact-date key left the second broker's row to find an empty
    pool and drop its extra share)."""
    by_pair: Dict[tuple, list] = {}
    for li, lst in enumerate(lists):
        for i, t in enumerate(lst):
            if t.action != 'SPLIT' or not t.date:
                continue
            new = normalize_symbol_new(t.symbol, getattr(t, 'symbol_new', ''))
            if not new:
                continue
            by_pair.setdefault((t.symbol, new), []).append((li, i, t))
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
    return groups


def _multi_ratio_rename_groups(lists) -> Dict[tuple, list]:
    """The events of _rename_ratio_groups whose rows carry more than one
    ratio: a merger's per-account EMPIRICAL deliveries (12 -> 12 and
    40 -> 42; corp_actions._emit_basis_carryover_rename)."""
    return {k: rows for k, rows in _rename_ratio_groups(lists).items()
            if len({round(float(r[2].quantity or 0), 9) for r in rows}) > 1}


def _per_account_rename_ratios(taxable: List[TaxTransaction],
                               sheltered: List[TaxTransaction],
                               affiliated: List[TaxTransaction]
                               ) -> Dict[str, tuple]:
    """A US blended book (FIFO lots per account, US-BASIS-01): each row
    of a multi-ratio merger event scales and migrates ITS account's
    lots at its own delivered ratio (US-CORP-14). Returns, for every
    such row, tx.id -> (event key, {account: that account's ratio}).
    The 2026-10 filing audit (F1): the first row scaled and migrated
    every account's lots at its ratio; the second found the source
    symbol gone, so an account that received 42 shares for 40 sold
    42 against 40 and opened a two-share phantom short."""
    out: Dict[str, tuple] = {}
    for key, rows in _multi_ratio_rename_groups(
            (taxable, sheltered, affiliated)).items():
        ratio_of: Dict[str, float] = {}
        for _li, _i, t in rows:
            ratio_of.setdefault(t.account, float(t.quantity or 0))
        for _li, _i, t in rows:
            out[t.id] = (key, ratio_of)
    return out


def _fold_per_account_rename_ratios(taxable: List[TaxTransaction],
                                    sheltered: List[TaxTransaction],
                                    affiliated: List[TaxTransaction],
                                    *, profile: str = 'ca_balance',
                                    date_of=None,
                                    moves: Sequence[TaxTransaction] = ()):
    """One merger, per-account EMPIRICAL ratios -> one corporate event.

    corp_actions emits a rename-SPLIT per account with the ratio the
    broker actually delivered (qty_received / qty_disposed, so a snapped
    fractional entitlement leaves no dust). Two accounts of one merger
    can therefore carry different ratios (12 ABC -> 12 ABD, 40 -> 42).
    Canada pools are symbol-global (s.47), so those rows neither dedupe
    (the ratio is part of the event key) nor scale their own account:
    the first renamed the WHOLE pool at its account's ratio and the
    second found no pool — shares went missing and the gain moved
    (2026-09 engine audit: fewer shares booked than were held).

    Rows of one event (same symbol, date and rename target) with more
    than one ratio are folded into ONE row whose ratio is the
    balance-weighted mean sum(q_a * r_a) / sum(q_a) over the TAXABLE
    accounts' holdings just before the event (all scopes when the
    taxable book holds none) — the pool lands on exactly the shares the
    broker delivered in total, cost preserved. Accounts holding the
    symbol without a row of their own keep the first row's ratio, as
    before. Per-account balances in the wash walks become the weighted
    share of that total (a fraction of a share off per account).

    `profile` / `date_of` order the balance walk: the Canada engine's
    settle-first ladder (the default); a US book whose lots are
    symbol-wide (no per-account basis) and the T1135 cost walk pass
    their own. `moves`: the legs of the moves between two of your
    taxable accounts that the caller's book no longer holds (the Canada
    engine books none: one ACB pool, _own_move_view): they move the
    TAXABLE balances the weights read, and are never returned (backlog
    TJ-0280: a merger after such a move weighed the holdings without
    it)."""
    lists = (taxable, sheltered, affiliated)
    todo = _multi_ratio_rename_groups(lists)
    if not todo:
        return taxable, sheltered, affiliated

    def _sd(t):
        return t.date_settle or t.date

    if date_of is None:
        date_of = _sd
    drop = set()
    replace: Dict[tuple, TaxTransaction] = {}
    for (sym, d, new), rows in todo.items():
        first = rows[0][2]
        split_key = event_sort_key(first, profile=profile, date_of=date_of)
        ratio_of = {}
        for _li, _i, t in rows:
            ratio_of.setdefault(t.account, float(t.quantity or 0))
        r0 = float(first.quantity or 0)

        def _weights(scopes):
            # Every symbol's balance per account up to the event, the
            # renames replayed: shares bought under an earlier ticker
            # and renamed INTO `sym` weigh too (reading only `sym`'s
            # own rows found no weights after a prior rename, and the
            # first row then migrated the whole pool: 2026-10 audit
            # R01). An own-account move (TRANSFER) moves them.
            bal: Dict[str, Dict[str, float]] = {}
            seen_plain = set()
            seen_rename = set()
            src = [t for li in scopes
                   for t in list(lists[li]) + (list(moves) if li == 0
                                               else [])
                   if event_sort_key(t, profile=profile,
                                     date_of=date_of) < split_key]
            src.sort(key=lambda t: event_sort_key(
                t, profile=profile, date_of=date_of))
            for t in src:
                if t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                                'TRANSFER'):
                    b = bal.setdefault(t.symbol, {})
                    b[t.account] = (b.get(t.account, 0.0)
                                    + float(t.quantity or 0))
                elif t.action == 'SPLIT':
                    tgt = normalize_symbol_new(t.symbol, t.symbol_new)
                    if not tgt:
                        if split_seen(seen_plain, t.symbol, t.date,
                                      t.quantity, '') is not None:
                            continue
                        for a in bal.get(t.symbol, {}):
                            bal[t.symbol][a] *= float(t.quantity or 0)
                        continue
                    ev_key = (t.symbol, t.date, tgt)
                    if ev_key in seen_rename:
                        continue
                    seen_rename.add(ev_key)
                    # One rename event: each account's own ratio where
                    # it has a row, else the first row's.
                    ev_ratio = {}
                    for u in src:
                        if (u.action == 'SPLIT' and u.symbol == t.symbol
                                and u.date == t.date
                                and normalize_symbol_new(
                                    u.symbol, u.symbol_new) == tgt):
                            ev_ratio.setdefault(u.account,
                                                float(u.quantity or 0))
                    r_first = float(t.quantity or 0)
                    moved = bal.pop(t.symbol, {})
                    dst = bal.setdefault(tgt, {})
                    for a, q in moved.items():
                        dst[a] = dst.get(a, 0.0) + q * ev_ratio.get(a, r_first)
            return {a: q for a, q in bal.get(sym, {}).items() if q > 1e-9}

        w = _weights((0,)) or _weights((0, 1, 2))
        tot = sum(w.values())
        if tot <= 1e-9:
            continue
        r_eff = sum(q * ratio_of.get(a, r0) for a, q in w.items()) / tot
        replace[(rows[0][0], rows[0][1])] = TaxTransaction(
            **{**first.to_dict(), 'quantity': r_eff, 'id': first.id})
        for li, i, _t in rows[1:]:
            drop.add((li, i))
        emit_line(f"NOTE: {sym} -> {new} on {d}: per-account merger ratios "
              f"{sorted(set(round(v, 6) for v in ratio_of.values()))} "
              f"applied to the symbol-wide pool as one event at the "
              f"holdings-weighted ratio {r_eff:.6g}.")
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


def spin_entitlement(ex_date, parent_qty, before, held) -> str:
    """How a §355 row's stated dates pick the parent's entitled lots:
    'dated' (only the lots before the date), 'due_bill' (every lot held
    on the pay date: the broker paid on all of them, a purchase after
    the record date came with the right) or 'unknown' (every lot, and
    said). `parent_qty`: the shares the broker paid on (0: not stated);
    `before`, `held`: the shares held from before the date, and on the
    pay date. An ex-date settles it; a record date alone does not when
    the shares traded with a due bill (ex after the pay date, the usual
    case for a large spin-off; TJ-1161)."""
    if ex_date:
        return 'dated'
    if parent_qty > 0 and abs(parent_qty - before) < 1.0:
        return 'dated'
    if parent_qty > 0 and abs(parent_qty - held) < 1.0:
        return 'due_bill'
    return 'unknown'

# A custody move between two of your own TAXABLE accounts, as a pair of
# TRANSFER rows (out of one account, into the other; same symbol,
# moment, quantity and description) that `tjs run` adds to a US
# blended book: the US engine moves the sender's FIFO lots — basis and
# purchase dates — to the receiver, with no disposition (US-BASIS-05).
# Canada pools the ACB across the accounts (s.47): a book of several
# accounts moves nothing; a book of one account moves its units only
# (CA-ACB-OWN-MOVE, _own_move_view).
LOT_MOVE_TYPE = 'lot_move'
# "own-account move #N: FROM -> TO (...)" — the legs' description.
_MOVE_DESC_RE = re.compile(r'own-account move #\d+: (\S+) -> (\S+)')


def _is_lot_move(t) -> bool:
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


def _taint(pool: Dict[str, Any], moved_from: Optional[str] = None) -> None:
    """Mark a Canada pool as holding units of unknown cost. `moved_from`
    names the account an own move brought them from (_own_move_units);
    any other cause (an opening at an unknown cost, units out beyond
    the pool) is `taint_other`. A sale from a pool whose unknown units
    all came by own moves says so (`own_move_from`): their cost is in
    the blended figures, never a purchase missing from the files
    (backlog TJ-0279)."""
    pool['tainted'] = True
    if moved_from is None:
        pool['taint_other'] = True
        return
    _mf = pool.setdefault('moved_from', [])
    if moved_from not in _mf:
        _mf.append(moved_from)


def _taint_from(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """`dst` takes `src`'s unknown-cost units (a journal, a rename into
    an existing pool): their taint and its cause."""
    if not src.get('tainted'):
        return
    if src.get('taint_other') or not src.get('moved_from'):
        _taint(dst)
    for _a in src.get('moved_from') or []:
        _taint(dst, _a)


def _untaint(pool: Dict[str, Any]) -> None:
    """A pool drained flat: its unknown-cost units are gone."""
    pool['tainted'] = False
    pool.pop('moved_from', None)
    pool.pop('taint_other', None)


def _own_move_only(pool: Dict[str, Any]) -> List[str]:
    """The accounts the pool's unknown-cost units were moved in from,
    when an own move is their only cause (_taint); else []."""
    if (pool.get('tainted') and pool.get('moved_from')
            and not pool.get('taint_other')):
        return list(pool['moved_from'])
    return []


def _own_move_units(pools: Dict[Any, Dict[str, Any]], key: Any, tx) -> str:
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
    pool = pools.get(key)
    if pool is None:
        pool = pools[key] = {
            'qty': 0.0, 'total_cost': Decimal(0),
            'last_acq_date': '1970-01-01', 'currency': '', 'tainted': False,
            'position_start_date': None, 'deferred_wash': 0.0, 'grants': []}
    q = float(tx.quantity or 0.0)
    if q > 0:
        if pool['position_start_date'] is None:
            pool['position_start_date'] = tx.date
        pool['qty'] += q
        _mm = _MOVE_DESC_RE.match(tx.description or '')
        _taint(pool, _mm.group(1) if _mm else '')
        return (f"OWN MOVE IN {q:10.4f} | cost unknown in this book "
                f"(its sales here: unknown cost)")
    held = pool['qty']
    take = min(-q, held) if held > 0 else 0.0
    out_cost = Decimal(0)
    if take > 0:
        out_cost = pool['total_cost'] * D(take) / D(held)
        pool['total_cost'] -= out_cost
        pool['deferred_wash'] = (pool.get('deferred_wash', 0.0)
                                 * max(0.0, 1.0 - take / held))
        if pool.get('wash_cut'):
            pool['wash_cut'] *= max(0.0, 1.0 - take / held)
        pool['qty'] = held - take
    rest = -q - take
    eps = pool_qty_eps(tx.symbol, held, q)
    if rest > eps:
        if pool['qty'] >= 0:
            pool['position_start_date'] = tx.date
        pool['qty'] -= rest
        _taint(pool)
    elif abs(pool['qty']) < eps:
        pool['qty'] = 0.0
        pool['position_start_date'] = None
        pool['grants'] = []
        _untaint(pool)
        if abs(pool['total_cost']) < Decimal('0.001'):
            pool['total_cost'] = Decimal(0)
    return (f"OWN MOVE OUT {-q:10.4f} | Cost_Moved: {float(out_cost):10.4f}"
            f" | Pool_Qty: {pool['qty']:10.4f} | no sale"
            + (f" | {rest:g} with no purchase in this book"
               if rest > eps else ""))


# A journal between two listings of one security inside one account (a
# TSX line moved to its US line: lib/listing_journals, backlog TJ-0361):
# two TRANSFER rows `tjs run` writes into the NATIVE-currency books
# only (work/<account>_raw*.json, behind the holdings export), the
# out-leg of the listing the units left and the in-leg of the one they
# reached, sharing a `journal_pair` id. The engines move the units with
# their cost from one listing's pool to the other's on the journal's
# date: never a disposition, never an acquisition. The in-leg's `price`
# restates the cost in its listing's currency when the source pool is
# in another (_journal_factor). The tax books never hold these rows:
# they join the listings, and the broker's legs cancel there.
LISTING_JOURNAL_TYPE = 'listing_journal'


def _is_listing_journal(t) -> bool:
    return t.action == 'TRANSFER' and t.type == LISTING_JOURNAL_TYPE


def _journal_legs(transactions) -> Dict[str, Tuple[Any, Any]]:
    """journal_pair -> (out-leg, in-leg) of each listing journal
    (LISTING_JOURNAL_TYPE) whose two legs are both in the book."""
    legs: Dict[str, list] = {}
    for t in transactions:
        if _is_listing_journal(t) and t.journal_pair:
            legs.setdefault(t.journal_pair, []).append(t)
    out: Dict[str, Tuple[Any, Any]] = {}
    for k, ts in legs.items():
        neg = [t for t in ts if (t.quantity or 0.0) < 0]
        pos = [t for t in ts if (t.quantity or 0.0) > 0]
        if (len(neg) == 1 and len(pos) == 1
                and neg[0].symbol != pos[0].symbol):
            out[k] = (neg[0], pos[0])
    return out


def _journal_factor(src_currency: str, src_leg, dst_leg) -> Optional[Decimal]:
    """The factor that restates a journaled cost in the receiving
    listing's currency: 1 when the source pool is already in it (every
    pool of a base-currency book); else the in-leg's `price` (the
    source's currency over the receiver's), inverted when a short moves
    the other way (from the in-leg's listing to the out-leg's). None:
    no rate on file — the cost is unknown in this book."""
    cur = dst_leg.currency
    if not src_currency or not cur or src_currency == cur:
        return Decimal(1)
    f = float((dst_leg if dst_leg.quantity > 0 else src_leg).price or 0.0)
    if f <= 0:
        return None
    return D(f) if dst_leg.quantity > 0 else Decimal(1) / D(f)


def _journal_units(pools: Dict[Any, Dict[str, Any]], out_leg, in_leg,
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
    def pool(sym, cur):
        p = pools.get(sym)
        if p is None:
            p = pools[sym] = {
                'qty': 0.0, 'total_cost': Decimal(0),
                'last_acq_date': '1970-01-01', 'currency': cur,
                'tainted': False, 'position_start_date': None,
                'deferred_wash': 0.0, 'grants': []}
        if not p['currency']:
            p['currency'] = cur
        return p

    q = abs(float(out_leg.quantity or 0.0))
    a = pool(out_leg.symbol, out_leg.currency)
    b = pool(in_leg.symbol, in_leg.currency)
    if a['qty'] <= eps and b['qty'] < -eps:
        src, dst, s_leg, d_leg, sign = b, a, in_leg, out_leg, -1.0
    else:
        src, dst, s_leg, d_leg, sign = a, b, out_leg, in_leg, 1.0
    f = _journal_factor(src['currency'], s_leg, d_leg)
    held = src['qty'] * sign
    take = min(q, held) if held > eps else 0.0
    rest = q - take
    if take > 0:
        frac = D(take) / D(held)
        cost = src['total_cost'] * frac
        dw = src.get('deferred_wash', 0.0) * float(frac)
        src['total_cost'] -= cost
        src['deferred_wash'] = src.get('deferred_wash', 0.0) - dw
        cut = src.get('wash_cut', 0.0) * float(frac)
        if cut:
            src['wash_cut'] -= cut
        src['qty'] -= take * sign
        if dst['qty'] * sign < -eps:
            _taint(dst)                  # into an opposite position
        elif abs(dst['qty']) <= eps or (
                src['position_start_date'] and (
                    dst['position_start_date'] is None
                    or src['position_start_date']
                    < dst['position_start_date'])):
            dst['position_start_date'] = src['position_start_date']
        dst['qty'] += take * sign
        if f is None:
            _taint(dst)
        else:
            dst['total_cost'] += cost * f
            dst['deferred_wash'] = (dst.get('deferred_wash', 0.0)
                                    + dw * float(f))
            if cut:
                _add_wash_cut(dst, cut * float(f), src.get('cut_late', 0.0))
        _sl = src.get('last_acq_date') or '1970-01-01'
        if _sl != '1970-01-01' and (
                dst['last_acq_date'] == '1970-01-01'
                or _sl < dst['last_acq_date']):
            dst['last_acq_date'] = _sl
            dst['last_acq_settle'] = src.get('last_acq_settle')
        if src.get('opening_acq'):
            dst['opening_acq'] = min(dst.get('opening_acq')
                                     or src['opening_acq'],
                                     src['opening_acq'])
        _taint_from(dst, src)
    if rest > eps:
        for p, d in ((src, -sign), (dst, sign)):
            if abs(p['qty']) <= eps:
                p['position_start_date'] = out_leg.date
            p['qty'] += rest * d
            _taint(p)
    if abs(src['qty']) <= pool_qty_eps(s_leg.symbol, held, q):
        src['qty'] = 0.0
        src['position_start_date'] = None
        src['grants'] = []
        _untaint(src)
        if abs(src['total_cost']) < Decimal('0.001'):
            src['total_cost'] = Decimal(0)
    return (f"LISTING JOURNAL {s_leg.symbol} -> {d_leg.symbol} "
            f"{take:10.4f}{' (a short)' if sign < 0 else ''} | cost "
            + ("unknown here (no rate)" if f is None else
               f"carried x{float(f):.6g}")
            + (f" | {rest:g} with no purchase in this book"
               if rest > eps else ""))


REORG_356_TYPE = 'reorg_356'
_LOT_EVENT_TYPES = (SPINOFF_355_TYPE, REORG_356_TYPE)
# An opening balance (`tjs opening`, a .tt OPENING line): a BUYSELL
# that sets a position and its cost on a positions report's date but is
# NOT a purchase — never a superficial-loss / wash-sale replacement and
# never "acquired in the window" (CA-OPEN-01, US-OPEN-01). Its date is
# the snapshot day; `lot_date` the real acquisition (US holding period).
OPENING_TYPE = 'opening'


def is_opening_row(tx) -> bool:
    """An opening-balance row (BUYSELL of type OPENING_TYPE), dict or
    TaxTransaction."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return a == 'BUYSELL' and (t or '') == OPENING_TYPE

# Shares that arrived by transfer from outside the books, booked at the
# broker's stated book value on their ARRIVAL date (lib/transfer_in,
# CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV). Held like any lot, but
# never a purchase for the superficial-loss / wash-sale window: the
# arrival is not when they were acquired.
TRANSFER_BOOK_VALUE_TYPE = 'transfer_book_value'
# A sheltered account's unmatched TRANSFER row kept in the loss-rule
# context as a custody move (lib/pipeline._handle_transfers, the default
# policy — CA-SL-16 / US-WASH-23): its shares count as held, but the row
# is never an acquisition (nor a disposition) for the superficial-loss /
# wash-sale window. `[settings] transfers_as_acquisitions = true` books
# such rows as 'transfer_rewrite' instead (CA-SL-17 / US-WASH-24).
TRANSFER_CUSTODY_TYPE = 'transfer_custody'


# An in-kind move between a TAXABLE account and a REGISTERED account of
# the same taxpayer (lib/in_kind, booked by `tjs run` from the pair
# of transfer rows or a .tt INKIND line):
# * IN_KIND_CONTRIBUTION_TYPE: the taxable account's SALE at fair market
#   value when its shares go into an RRSP/RRIF/TFSA/FHSA/RDSP/LIRA... —
#   a gain is taxed, a loss is denied for good (s.40(2)(g)(iv), CA-
#   INKIND-03): never a superficial loss, never added to any ACB. The
#   same type marks the PLAN's acquisition in the loss-rule context: a
#   purchase for s.54 whatever the transfer policy (CA-INKIND-04).
# * IN_KIND_DISPOSITION_TYPE: the same sale into a plan s.40(2)(g)(iv)
#   does not name (an RESP, a PRPP): a loss is an ordinary loss.
# * IN_KIND_WITHDRAWAL_TYPE: the taxable account's PURCHASE at fair
#   market value of shares withdrawn in kind from a plan (CA-INKIND-05 /
#   US-INKIND-02) — an acquisition like any other.
IN_KIND_CONTRIBUTION_TYPE = 'in_kind_contribution'
IN_KIND_DISPOSITION_TYPE = 'in_kind_disposition'
IN_KIND_WITHDRAWAL_TYPE = 'in_kind_withdrawal'
IN_KIND_TYPES = (IN_KIND_CONTRIBUTION_TYPE, IN_KIND_DISPOSITION_TYPE,
                 IN_KIND_WITHDRAWAL_TYPE)


def _pools_copy(pools: Dict[str, Any]) -> Dict[str, Any]:
    """The Canadian engine's pools as they stand (units, cost, currency,
    deferred_wash), rename aliases still sharing one entry: the year-end
    snapshot (backlog TJ-0282)."""
    memo: Dict[int, Dict[str, Any]] = {}
    out: Dict[str, Any] = {}
    for k, p in pools.items():
        c = memo.get(id(p))
        if c is None:
            c = memo[id(p)] = {f: p.get(f) for f in (
                'qty', 'total_cost', 'currency', 'deferred_wash')}
        out[k] = c
    return out


def _ca_pool_state(pools: Dict[str, Any]):
    """state_of(label) for the Canadian engine's cost timeline
    (lib/cost_timeline.Recorder): the pool of the label's symbol now,
    its cost in the inventory's sign convention (a short's negative).
    A futures contract's short pool is the label's state only while no
    long contract is held: the timeline is the cost of property held
    (T1135, the year-end snapshot, the dated list), and a short opened
    in another account is an obligation that never nets a long
    contract's cost away (backlog TJ-0650; _ca_short_state records the
    short itself)."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX

    def state(lab):
        sym = lab[1]
        p = pools.get(sym)
        sp = pools.get(sym + SHORT_POOL_SUFFIX)
        if sp is not None and (p is None or abs(p['qty']) <= 1e-12):
            p = sp
        if p is None:
            return None
        q = float(p['qty'])
        tc = float(p['total_cost'])
        lad = p.get('last_acq_date')
        return (q, tc if q >= 0 else -tc,
                float(p.get('deferred_wash', 0.0) or 0.0),
                bool(p.get('tainted')), p.get('currency') or '',
                p.get('position_start_date'),
                None if lad in (None, '1970-01-01') else lad)
    return state


def _ca_touched(tx, jlegs) -> List[Tuple[str, str]]:
    """The pools (cost-timeline labels) one row of the Canadian walk can
    change: its own, a rename's target, a listing journal's two."""
    labs = [('', tx.symbol)]
    if tx.action == 'SPLIT':
        new = (tx.symbol_new or '').strip()
        if new and new != tx.symbol:
            labs.append(('', new))
    if _is_listing_journal(tx):
        jl = jlegs.get(tx.journal_pair)
        if jl:
            labs += [('', jl[0].symbol), ('', jl[1].symbol)]
    return labs


def _fold_short_futures_pools(pools: Dict[str, Any]) -> Dict[str, Any]:
    """The Canadian engine's final pools with each short pool
    (lib/futures.SHORT_POOL_SUFFIX) under its symbol's entry when nothing
    else holds it. A short beside a long stays under its own key: an
    obligation and the property held are two positions, never their net
    (their costs have opposite signs; backlog TJ-1028, TJ-1058, TJ-1103).
    _pool_rows gives them to the reports."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX
    if not any(k.endswith(SHORT_POOL_SUFFIX) for k in pools):
        return pools
    out = {k: v for k, v in pools.items()
           if not k.endswith(SHORT_POOL_SUFFIX)}
    for k, sp in pools.items():
        if not k.endswith(SHORT_POOL_SUFFIX):
            continue
        sym = k[:-len(SHORT_POOL_SUFFIX)]
        lp = out.get(sym)
        if lp is None or abs(lp['qty']) <= 1e-12:
            out[sym] = sp
        elif abs(sp['qty']) > 1e-12 and lp['qty'] > 0:
            out[k] = sp
        elif abs(sp['qty']) > 1e-12:
            merged = dict(lp)
            merged['qty'] = lp['qty'] + sp['qty']
            merged['total_cost'] = lp['total_cost'] + sp['total_cost']
            merged['deferred_wash'] = (float(lp.get('deferred_wash', 0.0))
                                       + float(sp.get('deferred_wash', 0.0)))
            out[sym] = merged
    return out


def _short_shares(sym: str, short_units: Dict[Tuple[str, str], float]
                  ) -> Dict[str, float]:
    """{account: its share of the symbol's short pool}: each account's
    units short (_MarkedShortRouter.short), of all of them."""
    who = {a: u for (y, a), u in short_units.items()
           if y == sym and u > pool_qty_eps(sym, u)}
    total = sum(who.values())
    return {a: u / total for a, u in who.items()}


def _pool_rows(pools: Dict[str, Any],
               short_units: Dict[Tuple[str, str], float]):
    """(symbol, pool, account) of each pool the reports show: a short
    beside a long (_fold_short_futures_pools) is one row per account that
    is short, each at its share of the short's own proceeds, named by
    its account because it is that account's alone (the long is pooled,
    and is apportioned without it: taxjson_split_gains._split_inventory).
    Every other pool: its symbol, account ''."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX
    for k, p in pools.items():
        if not k.endswith(SHORT_POOL_SUFFIX):
            yield k, p, ''
            continue
        sym = k[:-len(SHORT_POOL_SUFFIX)]
        shares = _short_shares(sym, short_units)
        if not shares:
            yield sym, p, ''
        for acct, f in sorted(shares.items()):
            yield sym, dict(
                p, qty=p['qty'] * f, total_cost=p['total_cost'] * D(f),
                deferred_wash=float(p.get('deferred_wash', 0.0)) * f), acct


def _with_shorts(doc: Dict[str, Any], shorts: Dict[str, Any]
                 ) -> Dict[str, Any]:
    """The cost timeline with its `shorts` part, in books that have one
    (lib/cost_timeline.short_rows_on)."""
    return {**doc, 'shorts': shorts} if shorts.get('rows') else doc


def _ca_short_state(pools: Dict[str, Any], short_units):
    """state_of((account, symbol)) for the Canadian engine's second cost
    timeline: an account's part of a symbol's short pool (_pool_rows). It
    is what `tjs list --date` shows beside a long and what tells T1135
    who was short; the main one (_ca_pool_state) stays the cost of
    property held."""
    from taxjson.lib.futures import SHORT_POOL_SUFFIX

    def state(lab):
        acct, sym = lab
        p = pools.get(sym + SHORT_POOL_SUFFIX)
        f = _short_shares(sym, short_units()).get(acct)
        if p is None or not f:
            return None
        lad = p.get('last_acq_date')
        return (float(p['qty']) * f, -float(p['total_cost']) * f,
                float(p.get('deferred_wash', 0.0) or 0.0) * f,
                bool(p.get('tainted')), p.get('currency') or '',
                p.get('position_start_date'),
                None if lad in (None, '1970-01-01') else lad)
    return state


def _lot_wash_held(lot: Dict[str, Any]) -> Decimal:
    """What a long US lot's basis still holds of its §1091(d) add: a
    return of capital can have left it less (backlog TJ-1017)."""
    return min(lot.get('wash_deferred', Decimal(0)),
               max(Decimal(0), lot['cost_basis']))


def not_a_purchase(tx) -> bool:
    """Shares that are held but were not ACQUIRED on the row's date: an
    opening balance (CA-OPEN-01 / US-OPEN-01), a transfer-in booked at
    the broker's book value on its arrival (CA-ACB-TRANSFER-BV /
    US-BASIS-TRANSFER-BV) or a sheltered account's transfer kept as a
    custody move (CA-SL-16 / US-WASH-23). Never a superficial-loss /
    wash-sale replacement nor a recent buy; they count as held. Dict or
    TaxTransaction."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return a == 'BUYSELL' and (t or '') in (OPENING_TYPE,
                                             TRANSFER_BOOK_VALUE_TYPE,
                                             TRANSFER_CUSTODY_TYPE)


def is_stock_dividend(tx) -> bool:
    """A parser's stock-dividend row (a $0 BUYSELL of new shares)."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return (t or '') == STOCK_DIVIDEND and a == 'BUYSELL'


class TaxRules:
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True) -> Dict[str, Any]:
        raise NotImplementedError()


class _MarkedShortRouter:
    """Which rows of the Canadian walk belong to a symbol's marked-short
    pool (lib/futures.SHORT_POOL_SUFFIX; CA-DISP-04, backlog TJ-0721):
    each account's units short there and its units in the pooled long,
    so an unmarked purchase covers the account's own short only while it
    holds no long itself, and a sale coded C;O closes the account's own
    long and opens a short only past it. A row that does both is cut in
    two first (_split_crossing_marked_rows), so a row belongs wholly to
    one pool."""

    def __init__(self):
        from taxjson.lib.missing_history import (
            _BROKER_SHORT_RE, open_close_codes)
        self._short_re = _BROKER_SHORT_RE
        self._codes = open_close_codes
        self.short: Dict[Tuple[str, str], float] = {}
        self.long: Dict[Tuple[str, str], float] = {}

    def _opens_short(self, t) -> bool:
        d = (t.description or '').upper()
        if 'COVER SHORT.' in d:
            return False
        return (bool(self._short_re.search(d))
                or self._codes(t) == ('O',))

    def _covers(self, t) -> bool:
        return (bool(self._short_re.search((t.description or '').upper()))
                or 'C' in self._codes(t))

    def _both_coded(self, t) -> bool:
        return set(self._codes(t)) == {'C', 'O'}

    @staticmethod
    def _is_stock(t) -> bool:
        return not (is_option_symbol(t.symbol)
                    or (t.type or '') == 'futures_settlement')

    @staticmethod
    def _is_trade(t) -> bool:
        return t.action == 'BUYSELL' and not not_a_purchase(t)

    def _wants_short(self, t, q: float, k, eps: float) -> bool:
        if q < 0:
            if self._opens_short(t):
                return True
            return (self._both_coded(t)
                    and self.long.get(k, 0.0) <= eps)
        return (q > 0 and self.short.get(k, 0.0) > eps
                and (self._covers(t)
                     or (self._codes(t) != ('O',)
                         and self.long.get(k, 0.0) <= eps)))

    def crossing_units(self, t) -> Optional[float]:
        """The units of a trade's first part when it crosses from one
        pool to the other (a cover past the account's short, a C;O sale
        past its long); None for a row that stays in one pool."""
        if not (self._is_stock(t) and self._is_trade(t)):
            return None
        q = float(t.quantity or 0.0)
        k = (t.symbol, t.account)
        eps = pool_qty_eps(t.symbol, q)
        if q > 0 and self._wants_short(t, q, k, eps):
            units = self.short.get(k, 0.0)
            return units if q > units + eps else None
        if q < 0 and self._both_coded(t) and not self._opens_short(t):
            units = self.long.get(k, 0.0)
            return units if eps < units and -q > units + eps else None
        return None

    def walk(self, t) -> bool:
        """Book a row; True when it belongs to the short pool."""
        if not self._is_stock(t):
            return False
        q = float(t.quantity or 0.0)
        k = (t.symbol, t.account)
        if self._is_trade(t):
            if self._wants_short(t, q, k, pool_qty_eps(t.symbol, q)):
                self.short[k] = self.short.get(k, 0.0) - q
                return True
            self.long[k] = self.long.get(k, 0.0) + q
        elif _is_lot_move(t) and q < 0:
            # Units moved out never leave less than none: a later buy
            # is a long of the account's own (backlog TJ-1180).
            self.long[k] = max(self.long.get(k, 0.0) + q, 0.0)
        elif (t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE')
                or _is_lot_move(t) or _is_listing_journal(t)):
            # Held units that are no trade of this rule: they are the
            # account's long all the same.
            self.long[k] = self.long.get(k, 0.0) + q
        return False

    def walk_futures_short(self, t) -> None:
        """A short futures leg (lib/futures.engine_pool_key): its pool is
        by the row's own direction, only whose units they are is kept."""
        k = (t.symbol, t.account)
        self.short[k] = self.short.get(k, 0.0) - float(t.quantity or 0.0)

    def split(self, symbol: str, target: str, ratio: float) -> None:
        """A split or rename applies to both positions alike."""
        for d in (self.short, self.long):
            for ka in [k for k in d if k[0] == symbol]:
                units = d.pop(ka) * ratio
                kt = (target, ka[1])
                d[kt] = d.get(kt, 0.0) + units


def _row_part(t, units: float, code: str, part_id: str, *,
              rest_of=None) -> 'TaxTransaction':
    """`units` of trade `t`, its money pro rata (`rest_of`: the part
    already cut, this one takes what it left, so the two add up to the
    row exactly). `code`: what this part does, the broker's own letter."""
    q = float(t.quantity)
    sign = 1.0 if q > 0 else -1.0
    frac = units / abs(q)

    def share(field: str) -> float:
        v = float(getattr(t, field) or 0.0)
        if rest_of is not None:
            return v - float(getattr(rest_of, field) or 0.0)
        return v * frac
    return replace(t, quantity=sign * units, proceeds=share('proceeds'),
                   commission=share('commission'), fee=share('fee'),
                   net_amount=share('net_amount'),
                   gross_amount=share('gross_amount'),
                   open_close=code, id=part_id)


def _router_move_key(t, date_of) -> Tuple:
    """Where a blended book's move leg (dropped from its rows,
    _own_move_view) falls for _MarkedShortRouter, against a row's
    event_sort_key 'ca_main': the in leg from the start of its day, the
    out leg after the day's rows, as the missing-history walks read them
    (TJ-0093), so a sale of the moved units that day is no short."""
    return (date_of(t), -1 if float(t.quantity or 0.0) > 0 else 99)


def _split_crossing_marked_rows(transactions: List['TaxTransaction'],
                                sort_key, moves: Sequence[Any] = (),
                                move_key=None) -> List['TaxTransaction']:
    """The taxable rows with each trade that crosses between the short
    pool and the long one cut in two, in the walk's order
    (_MarkedShortRouter.crossing_units): a buy past the account's own
    short covers the short and buys long with the rest (CA-DISP-04; all
    of it in the short pool, the rest was a long no later sale could
    find, backlog TJ-1021), and a sale coded C;O past the account's own
    long sells that long and opens a short with the rest, never another
    account's shares (backlog TJ-1023). The part that realizes keeps the
    row's id, so its gain is the row's. `moves`: the legs of own moves
    the book no longer holds, for what each account holds (TJ-1180)."""
    router = _MarkedShortRouter()
    if not any(t.action == 'BUYSELL'
               and (router._opens_short(t) or 'O' in router._codes(t))
               for t in transactions):
        return transactions             # no marked short in these books
    parts: Dict[int, Tuple[Any, Any]] = {}
    _leg_ids = {id(m) for m in moves}
    for t in sorted([*transactions, *moves],
                    key=lambda x: (move_key(x) if id(x) in _leg_ids
                                   else sort_key(x))):
        if t.action == 'SPLIT':
            router.split(t.symbol, (t.symbol_new or '').strip() or t.symbol,
                         float(t.quantity or 0.0))
            continue
        units = router.crossing_units(t)
        if units is None:
            router.walk(t)
            continue
        first = _row_part(t, units, 'C', t.id)
        rest = _row_part(t, abs(float(t.quantity)) - units, 'O',
                         f"{t.id}#2", rest_of=first)
        router.walk(first)
        router.walk(rest)
        parts[id(t)] = (first, rest)
    if not parts:
        return transactions
    out: List['TaxTransaction'] = []
    for t in transactions:
        out.extend(parts.get(id(t), (t,)))
    return out


class CanadaTaxRules(TaxRules):
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, option_premium_timing: str = 'close', option_grant_since: Optional[int] = None, option_buyback_loss_superficial: bool = False, option_grant_basis: str = 'settle', loss_overrides: Sequence[Dict[str, Any]] = (), year_end: Optional[str] = None, year_end_basis: Optional[str] = None, cost_timeline: Optional[str] = None) -> Dict[str, Any]:
        """
        Detects Superficial Losses (Wash Sales) based on CRA rules and calculates gains.
        Handles multi-account pooling and iterative adjustments.

        `cost_timeline` ("settle" or "trade", the date basis its rows
        are dated on): also return each pool's quantity and cost after
        every row that changed it (`cost_timeline`, lib/cost_timeline;
        backlog TJ-0417), the converged pass's.

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
        # The computation's input contract, main and context books
        # (issues #14, #16): a trade without its amount, an empty date or
        # symbol, an action the engine does not book.
        require_computable_rows(transactions, sheltered_transactions,
                                affiliated_transactions)
        transactions, sheltered_transactions, affiliated_transactions = \
            _by_account_name(transactions, sheltered_transactions,
                             affiliated_transactions)
        # A move between two of your own taxable accounts changes nothing
        # in Canada: the ACB is one pool across them (s.47). A book of
        # one account moves its units (CA-ACB-OWN-MOVE). Its legs still
        # move the holdings a merger's per-account ratios are weighted
        # by (backlog TJ-0280).
        _booked = _own_move_view(transactions)
        _move_legs = ([t for t in transactions if _is_lot_move(t)]
                      if len(_booked) < len(transactions) else [])
        transactions = _booked
        # The same holds for futures (issue #76): each account's books
        # were settled on their own, so a close of a contract held in
        # several taxable accounts is re-averaged over all of them here
        # (lib/futures.pool_across_accounts; tax-logic CA-FX-04).
        from taxjson.lib.futures import (engine_pool_key as _fut_pool_key,
                                         pool_across_accounts)
        transactions, _fut_notes = pool_across_accounts(list(transactions))
        for _n in _fut_notes:
            emit_line(_n)
        _disambiguate_duplicate_ids(transactions, sheltered_transactions,
                                    affiliated_transactions)
        # One corporate split = one application: collapse per-account SPLIT
        # duplicates across all three lists (shared `seen`) before any
        # symbol-global pool or window walk sees them.
        _seen_splits: set = set()
        transactions, sheltered_transactions, affiliated_transactions = \
            _fold_per_account_rename_ratios(
                list(transactions), list(sheltered_transactions or []),
                list(affiliated_transactions or []), moves=_move_legs)
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

        # The user's filing positions against the rule (.tt ALLOWLOSS,
        # lib/loss_overrides; CA-SL-18): each names one sale whose denial
        # is not applied. `_ov_would` holds, per named row, what the
        # solver's last pass would have denied (and to which
        # replacements) — recorded, never booked.
        from taxjson.lib import loss_overrides as _LO
        _ov_plan = _LO.plan(
            loss_overrides,
            sorted((t for t in transactions),
                   key=lambda x: event_sort_key(x, profile='ca_main',
                                                date_of=get_sort_date)))
        _ov_would: Dict[str, Dict[str, Any]] = {}

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
                    if not (split_in_settle_lag(
                                _t.date, _t.time, _t.date_settle,
                                _spd, _sp.time)
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
                    emit_line(f"NOTE: {_t.symbol}: re-denominated a "
                          f"{_t.quantity:g}-share trade executed "
                          f"{_t.date} through the x{_f:g} split "
                          f"inside its settle lag (booked as "
                          f"{_d['quantity']:g} post-split shares; "
                          f"money unchanged).")
                # Rebuild the per-book lists from the same slices
                # (all_txs is transactions + sheltered + affiliated,
                # in order) so every downstream walk sees the
                # re-denominated rows.
                _n1 = len(transactions)
                transactions = all_txs[:_n1]
                _n2 = _n1 + len(sheltered_transactions or [])
                sheltered_transactions = all_txs[_n1:_n2]
                affiliated_transactions = all_txs[_n2:]

        # A trade that crosses between a marked short and the long pool
        # is two rows from here on (CA-DISP-04).
        _uncut = transactions
        transactions = _split_crossing_marked_rows(
            transactions,
            lambda x: event_sort_key(x, profile='ca_main',
                                     date_of=get_sort_date),
            moves=_move_legs,
            move_key=lambda x: _router_move_key(x, get_sort_date))
        if transactions is not _uncut:
            all_txs = (transactions + (sheltered_transactions or [])
                       + (affiliated_transactions or []))
            taxable_ids = {t.id for t in transactions}

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
        # root can differ from the ticker: SAMPLD for SAMPLD.B.TO, F:SAMPLX for
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
        final_grant_nil_closes = []
        final_wash_sales = []
        final_global_pools = {}
        adjust_to_trigger: Dict[str, str] = {}  # adj_id -> trigger_lot.id (full)
        loss_to_trigger: Dict[str, str] = {}  # loss tx.id -> PRIMARY trigger id
        loss_to_triggers_multi: Dict[str, list] = {}  # loss tx.id -> all allocated trigger ids
        permanent_by_loss: Dict[str, float] = {}  # loss tx.id -> sheltered-allocated denial
        wash_windows: Dict[str, Dict[str, Any]] = {}  # loss tx.id -> ±30d window listing
        final_pool_snapshots: Dict[str, Dict[str, float]] = {}  # tx.id -> pool state after that tx
        # The pools at the end of `year_end` (YYYY-12-31, the project
        # year's last day, on this engine's settlement-date order), the
        # converged pass's: `tjs edge-cases` reads the denied losses
        # still held at the year end from them, not from the pools after
        # every later row (backlog TJ-0282). None without a year_end.
        final_year_end_pools: Optional[Dict[str, Any]] = None
        # The converged pass's cost timeline (lib/cost_timeline; backlog
        # TJ-0417): (recorder, each row's (walk date, basis date)). None
        # unless asked for.
        final_timeline: Optional[Tuple[Any, Any, List[Tuple[str, str]]]] = None
        # Each account's units short at the end and at year_end (_pool_rows).
        final_short_units: Dict[Tuple[str, str], float] = {}
        final_ye_short_units: Dict[Tuple[str, str], float] = {}

        # Symbol-alias map for SPLIT-renames. CRA s. 85.1(5) and
        # similar reorgs treat the pre- and post-rename security as
        # substantially identical for wash-sale purposes — so ABG.TO
        # and ABH.US must share a single time-series when computing
        # the 30-day affiliated balance, and a loss on ABG.TO must
        # consider ABH.US buys as potential wash triggers.
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
        # Group trades by equivalence class (not raw symbol) so ABG.TO
        # → ABH.US shares one chronological running balance. Without
        # this, post-rename ABH.US buys wouldn't show up in the 30-day
        # affiliated check for an ABG.TO loss.
        running_bal_by_tx: Dict[str, float] = {}
        # Each class's rows in book order, grouped in one pass (one
        # walk of the book per class cost classes x rows: TJ-0105).
        _rows_of_cls: Dict[str, list] = {}
        for t in all_txs:
            _rows_of_cls.setdefault(row_cls(t), []).append(t)
        for _cls_rows in _rows_of_cls.values():
            txs_sym = sorted(
                _cls_rows,
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

        # A book row's key, kept: the superficial-loss scans ask for it
        # once per loss and pass (TJ-0105). Book rows are never changed
        # here and all_txs keeps them alive (so no id is reused); the
        # solver's own rows (DISALLOW, ADJUST) are keyed afresh.
        _ev_memo: Dict[int, Tuple] = {}
        _book_ids = frozenset(id(t) for t in all_txs)

        def _ev_key(t):
            k = _ev_memo.get(id(t))
            if k is None:
                k = event_sort_key(t, profile='ca_main',
                                   date_of=get_sort_date)
                if id(t) in _book_ids:
                    _ev_memo[id(t)] = k
            return k

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
            if receives_on_buy(tx):
                return _n
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
                   'year': _write_year(tx), 'account': tx.account}
            pool.setdefault('grants', []).append(lot)
            return lot

        def _short_lot_close(pool, closing_qty, is_assign, account=None):
            """Consume `closing_qty` units of a SHORT option pool's lots
            FIFO, the closing row's own `account`'s lots first: an
            assignment or buy-back closes the contract written in that
            brokerage account, never one still open in another (backlog
            TJ-0718); another account's lots only when its own run out
            (a contract moved between accounts). Returns (cost_removed, recognised, grant_units_closed,
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
            # What a lot with no recognised premium leaves at: the average
            # of its own account's such lots, so a close never takes
            # another account's premium, with its share of what the lots
            # do not hold (a parked wash deferral) (backlog TJ-1105). One
            # account's lots: the pool's average, the same figure. Each
            # account's lots keep that average for its next close.
            own_avg: Dict[Any, float] = {}
            residue = 0.0
            if n_other > 1e-9:
                other_avg = (total - r_amt) / n_other
                rec_extra = 0.0
                plain = [l for l in lots if not l['rec']]
                _sums: Dict[Any, List[float]] = {}
                for l in plain:
                    e = _sums.setdefault(l.get('account'), [0.0, 0.0])
                    e[0] += l['units']
                    e[1] += l['units'] * l['per_unit']
                for a, (u, m) in _sums.items():
                    own_avg[a] = m / u if u > 1e-12 else 0.0
                for l in plain:
                    l['per_unit'] = own_avg[l.get('account')]
                if len(own_avg) > 1:
                    residue = ((total - r_amt
                                - sum(m for _u, m in _sums.values()))
                               / n_other)
                else:
                    own_avg = {}
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
            _order = lots
            if account is not None:
                _order = ([l for l in lots if l.get('account') == account]
                          + [l for l in lots
                             if l.get('account') != account])
            for lot in _order:
                if rem <= 1e-9:
                    break
                take = min(rem, lot['units'])
                if take <= 1e-12:
                    continue
                lot['units'] -= take
                rem -= take
                if not lot['rec']:
                    cost += take * (own_avg[lot.get('account')] + residue
                                    if own_avg else other_avg)
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
                _gt = SharedTrace(symbol_acb_traces[symbol])
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
        # The superficial-loss scans' indexes of the book (see
        # _DatedRows; built on the first pass, the book never changes):
        # each class's candidate replacements, the calls on each class,
        # each symbol's rows for the window listing.
        _sl_trg: Optional[Dict[str, _DatedRows]] = None
        _sl_call: Optional[Dict[str, _DatedRows]] = None
        _sl_sym: Optional[Dict[str, _DatedRows]] = None
        _split_syms = {t.symbol for t in all_txs if t.action == 'SPLIT'}
        # An option series a SPLIT replaces (2:1: one C10 contract
        # becomes two C5 contracts) is the same right under a new
        # symbol: the day-30 call walk sums each series' whole lineage,
        # keyed by its first symbol (TJ-0716). Rows are converted by
        # the underlying's split lineage, so the SPLIT row adds nothing.
        _opt_prev: Dict[str, str] = {}
        for _t in all_txs:
            _new = (getattr(_t, 'symbol_new', '') or '').strip()
            if (_t.action == 'SPLIT' and _new and _new != _t.symbol
                    and is_option_symbol(_t.symbol)
                    and is_option_symbol(_new)):
                _opt_prev.setdefault(_new, _t.symbol)

        def _opt_root(sym: str) -> str:
            seen = {sym}
            while sym in _opt_prev and _opt_prev[sym] not in seen:
                sym = _opt_prev[sym]
                seen.add(sym)
            return sym
        _opt_lineage: Dict[str, set] = {}
        for _s in set(_opt_prev) | set(_opt_prev.values()):
            _opt_lineage.setdefault(_opt_root(_s), set()).add(_s)
        # A share short the broker marks (RBC "SHORT." / "COVER SHORT.",
        # IB code O / C) is a position of its own beside the taxpayer's
        # pooled long shares, as a short future is (CA-DISP-04, backlog
        # TJ-0721): opening it never sells the shares another account
        # holds, and a purchase in another account never covers it. The
        # main pass routes such rows to the symbol's short pool
        # (SHORT_POOL_SUFFIX) and records them here, so the
        # superficial-loss walks key them alike (_row_pool_key).
        from taxjson.lib.futures import SHORT_POOL_SUFFIX as _SHORT_SFX
        _short_routed: set = set()

        def _row_pool_key(t) -> str:
            if t.id in _short_routed:
                return t.symbol + _SHORT_SFX
            return _fut_pool_key(t)

        solver_converged = False
        solver_iterations_used = 0
        for iteration in range(1000):
            solver_iterations_used = iteration + 1
            current_tx_list = all_txs + final_virtual_txs
            current_tx_list.sort(
                key=lambda x: event_sort_key(x, profile='ca_main',
                                             date_of=get_sort_date))
            current_tx_list = _place_wash_adjusts(current_tx_list)
            # This pass's denials by loss id, in list order (a lookup
            # per sale walked every denial: TJ-0105).
            _dis_by_id: Dict[Any, list] = {}
            for _dv in final_virtual_txs:
                if _dv.action == 'DISALLOW':
                    _dis_by_id.setdefault(_dv.id, []).append(_dv)

            # Each option ASSIGN paired with its own stock leg; a leg
            # dated before its option row gets the option moved in front
            # of it (A2-0051/0195).
            def _taxable_scope(_t):
                return (_t.id not in sheltered_ids
                        and _t.id not in affiliated_ids)
            _assign_pairs = _pair_assign_legs(
                current_tx_list, _taxable_scope, _assign_underlying)
            _moved_legs = _assign_moved_legs(current_tx_list,
                                             _assign_pairs)
            current_tx_list = _place_assign_options(current_tx_list,
                                                    _assign_pairs)

            def _walk_date(t, trade: bool = False) -> str:
                """The date the walk stands at on row `t`: its own (sort,
                or trade) date, but a moved option row's is its stock
                leg's when that is earlier (_assign_moved_legs)."""
                d = str((t.date if trade else get_sort_date(t)) or '')[:10]
                leg = _moved_legs.get(t.id)
                if leg is not None:
                    ld = str((leg.date if trade else get_sort_date(leg))
                             or '')[:10]
                    if ld and ld < d:
                        return ld
                return d

            # Pools indexed by symbol
            global_pools = {}  # symbol -> {'qty', 'total_cost', 'last_acq_date', 'currency', 'tainted'}

            # Staged option-assignment premiums, paired with their own
            # stock legs (see _AssignPremiumLedger).
            pending_adjustments = _AssignPremiumLedger(
                current_tx_list,
                lambda _t: (not is_option_symbol(_t.symbol)
                            and not exercise_target(_t)
                            and _t.action in ('BUYSELL', 'ASSIGN')
                            and not is_opening_row(_t)
                            and _taxable_scope(_t)),
                _assign_pairs)
            iteration_realized_gains = []
            # Grant-timed closes that leave no record (_suppress_record).
            iteration_grant_nil_closes = []
            iteration_losses = []
            iteration_trace = []
            symbol_acb_traces = {}
            pool_snapshots_this_iter: Dict[str, Dict[str, float]] = {}
            # An ACB reallocation (an s.86.1 spin-off): the parent's
            # negative ADJUST and the new shares' BUYSELL, joined by
            # (account, corp_event_id). The parent's deferred_wash
            # tally moves with the share of the ACB it gives up, so the
            # economic P/L views split the recovered loss as the ACB is
            # split (backlog TJ-0062; the filed figures never read it).
            _cev_adj_sym = {(t.account, t.corp_event_id): t.symbol
                            for t in current_tx_list
                            if t.action == 'ADJUST' and t.corp_event_id
                            and float(t.net_amount or 0.0) < 0}
            _cev_moves = {(t.account, t.corp_event_id)
                          for t in current_tx_list
                          if t.action == 'BUYSELL' and t.corp_event_id
                          and float(t.quantity or 0.0) > 0
                          and _cev_adj_sym.get((t.account, t.corp_event_id))
                          not in (None, t.symbol)}
            _cev_stash: Dict[tuple, float] = {}
            _cev_child: Dict[tuple, Dict[str, Any]] = {}
            # A native book's journals between listings (one leg books
            # the pair: _journal_units; backlog TJ-0361).
            _jlegs = _journal_legs(current_tx_list)
            _jdone: set = set()
            
            if trace:
                iteration_trace.append("# " + "-" * 215)
                iteration_trace.append("# ACCOUNT                    | NOTE         | DATE                | SYMBOL                     | ACTION   | QTY        | PRICE      | FEE/SH     | PRICE_FEE  | VALUE_NET  | ADJUSTMENT | GLOBAL_BAL | POOL_ACB   | ACB/SHARE  | REALIZED   | DISALLOWED | TRIGGER_INFO")
                iteration_trace.append("# " + "-" * 215)

            _ye_pools: Optional[Dict[str, Any]] = None
            _ye_shorts: Dict[Tuple[str, str], float] = {}
            # The cost timeline (lib/cost_timeline; backlog TJ-0417):
            # each row's pools are read after it (at the next row), and
            # every pool still held at the end of each day.
            _tl = None
            _tl_dates: List[Tuple[str, str]] = []
            _tl_prev: List[Any] = []
            if cost_timeline:
                from taxjson.lib import cost_timeline as _CT
                _tl = _CT.Recorder(_ca_pool_state(global_pools))
                _tl_shorts = _CT.Recorder(_ca_short_state(
                    global_pools, lambda: _msr.short))

            # The marked shorts' routing (_short_routed): each
            # account's units short and long (_MarkedShortRouter).
            _short_routed.clear()
            _msr = _MarkedShortRouter()
            # The legs a blended book holds no row of: they still move
            # what each account holds (backlog TJ-1180).
            _msr_legs = sorted(
                ((_router_move_key(_m, get_sort_date), _i, _m)
                 for _i, _m in enumerate(_move_legs)),
                key=lambda _e: _e[:2])

            def _stock_short_key(t) -> str:
                sym = t.symbol
                if _msr.walk(t):
                    _short_routed.add(t.id)
                    return sym + _SHORT_SFX
                if t.action in ('ADJUST', 'DISALLOW'):
                    # Only the short is open: a row on the symbol is
                    # the short's, as it was before shorts had a pool.
                    sk = sym + _SHORT_SFX
                    sp, mp = global_pools.get(sk), global_pools.get(sym)
                    if (sp is not None
                            and abs(sp['qty']) > pool_qty_eps(sym, sp['qty'])
                            and (mp is None or abs(mp['qty'])
                                 <= pool_qty_eps(sym, mp['qty']))):
                        return sk
                return sym

            def _tl_after(seq, prev):
                _ren = {}
                if (prev.action == 'SPLIT' and prev.id not in sheltered_ids
                        and prev.id not in affiliated_ids):
                    _new = (prev.symbol_new or '').strip()
                    if _new and _new != prev.symbol:
                        _ren[('', prev.symbol)] = _new
                _tl.touch(seq, _ca_touched(prev, _jlegs), _ren)
                if _msr.short:
                    _tl_shorts.sweep(seq, [(_a, _s) for (_s, _a) in _msr.short])

            for tx in current_tx_list:
                if _tl is not None:
                    _tl_d = _walk_date(tx)
                    if _tl_prev:
                        _tl_after(len(_tl_dates) - 1, _tl_prev[0])
                        if _tl_d != _tl_dates[-1][0]:
                            _tl.sweep(len(_tl_dates) - 1)
                    _tl_dates.append(
                        (_tl_d, _tl_d if cost_timeline != 'trade'
                         else (_walk_date(tx, True) or _tl_d)))
                    # Which book row this step is (T1135 places a
                    # custody account's rows between a day's steps:
                    # backlog TJ-0646). A DISALLOW is the solver's row,
                    # not a row of the books: it carries its sale's id,
                    # and the id walked twice could not be placed
                    # (backlog TJ-1099).
                    _tl.row_ids.append('' if tx.action == 'DISALLOW'
                                       else str(tx.id or ''))
                    _tl_prev[:] = [tx]
                # A tax_date = "trade" project (`year_end_basis`): its
                # year ends before the first row TRADED after year_end,
                # so a sale traded Dec 31 that settles in January (this
                # year's disposition on trade dates) has left the pool
                # (backlog TJ-0343, the mirror of the US settle case).
                if (year_end and _ye_pools is None
                        and _walk_date(tx, year_end_basis == 'trade')
                        > year_end):
                    _ye_pools = _pools_copy(global_pools)
                    _ye_shorts = dict(_msr.short)
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
                if (_msr_legs and not is_other_scope and _msr._is_stock(tx)
                        and tx.action in ('BUYSELL', 'ASSIGN', 'SPLIT',
                                          'OPENING_BALANCE')):
                    _msr_k = event_sort_key(tx, profile='ca_main',
                                            date_of=get_sort_date)
                    while _msr_legs and _msr_legs[0][0] < _msr_k:
                        _msr.walk(_msr_legs.pop(0)[2])

                # Non-capital cash-flow events (dividends, interest, fees,
                # taxes, transfers) don't touch the ACB pool or account
                # balances. They often live on a synthetic "CASH" symbol
                # whose currency varies by row, so skip them before
                # currency/pool initialization. TRANSFER is handled at the
                # CLI layer — for sheltered files it's rewritten to BUYSELL
                # before reaching the engine, and for taxable files the CLI
                # errors out before invoking compute_gains. By the time
                # the engine runs, no TRANSFER rows should be present —
                # but one account's own view of a move between two of
                # your taxable accounts (_own_move_view): its units move;
                # and a native book's journal between two listings.
                if _is_listing_journal(tx):
                    if not is_other_scope:
                        _msr.walk(tx)
                    _jl = _jlegs.get(tx.journal_pair)
                    if (_jl and not is_other_scope
                            and tx.journal_pair not in _jdone):
                        _jdone.add(tx.journal_pair)
                        _jtxt = _journal_units(global_pools, *_jl)
                        if trace:
                            for _js in (_jl[0].symbol, _jl[1].symbol):
                                symbol_acb_traces.setdefault(_js, [
                                    f"# --- ACB CALCULATION TRACE: {_js} ---"
                                ]).append(f"# {tx.date} {_jtxt}")
                    continue
                if _is_lot_move(tx):
                    if not is_other_scope:
                        _msr.walk(tx)
                        _mv_txt = _own_move_units(global_pools,
                                                  _fut_pool_key(tx), tx)
                        if trace:
                            symbol_acb_traces.setdefault(symbol, [
                                f"# --- ACB CALCULATION TRACE: {symbol} ---"
                            ]).append(f"# {tx.date} {_mv_txt}")
                    continue
                if tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE', 'TRANSFER') \
                   or tx.type in ('dividend', 'dividend_in_lieu', 'tax', 'interest', 'fee'):
                    continue

                # A short futures position has its own pool beside the
                # long one (lib/futures.engine_pool_key): a short opened
                # in one account never closes a long held in another,
                # so each close is booked in the year its account
                # closed it (issue #76). Every other row: its symbol.
                _pkey = _fut_pool_key(tx)
                if _pkey == symbol and not is_other_scope:
                    _pkey = _stock_short_key(tx)
                elif _pkey != symbol and not is_other_scope:
                    _msr.walk_futures_short(tx)
                if _pkey not in global_pools:
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
                    global_pools[_pkey] = {'qty': 0.0, 'total_cost': Decimal(0), 'last_acq_date': '1970-01-01', 'currency': '', 'tainted': False, 'position_start_date': None, 'deferred_wash': 0.0, 'grants': []}
                pool = global_pools[_pkey]
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
                        or tx.action not in ('BUYSELL', 'ASSIGN')
                        or is_opening_row(tx)):
                    # Only a stock trade can be an assignment's leg (an
                    # ADJUST / SPLIT / OB row on the underlying used to
                    # pop — and drop — the staged premium).
                    internal_adj = 0.0
                    internal_dw = 0.0
                    internal_tainted = False
                else:
                    # The ledger keeps a premium whose own (paired) leg
                    # is still to come away from this trade.
                    internal_adj = pending_adjustments.take(tx)
                    # The earlier superficial loss in that premium (an
                    # exercised replacement call: backlog TJ-0061).
                    internal_dw = pending_adjustments.last_deferred
                    # An unknown-cost option's premium: these shares'
                    # cost (or this sale's proceeds) is unknown too
                    # (TJ-0947).
                    internal_tainted = pending_adjustments.last_tainted
                    if (internal_adj < 0 and float(tx.quantity or 0.0) > 0
                            and pool.get('wash_cut', 0.0) > 1e-9):
                        # A put's premium arriving after the cut: see
                        # _add_wash_cut.
                        pool['cut_late'] = (pool.get('cut_late', 0.0)
                                            - internal_adj / pool['wash_cut'])
                
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
                        emit_line(f"note: {symbol}: assignment treated as "
                              f"cash-settled ("
                              f"{parse_option_underlying(symbol) or exercise_target(tx) or '?'}"
                              f" never trades as stock in this book) — "
                              f"option P&L realized directly. If a stock "
                              f"leg is missing from your input, add it "
                              f"and re-run.")
                
                if action == 'DISALLOW':
                    note = "DISALLOWANCE"
                    adjustment_shown = tx.net_amount
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        symbol_acb_traces[symbol].append(f"# {tx.date} DISALLOW {tx.net_amount:10.4f} | (Loss disallowed on this date)")
                elif action == 'ADJUST':
                    _applied_adj = float(tx.net_amount)
                    # Flat is the pool's own tolerance (pool_qty_eps):
                    # the 1e-6 share epsilon read a sub-micro coin
                    # position as empty, parked its superficial-loss
                    # deferral in pending_wash, and the closing sale
                    # never recovered it (2026-10 filing audit F2).
                    _aeps = pool_qty_eps(symbol, pool['qty'])
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
                        # gain).
                        _empty_roc = (abs(pool['qty']) <= _aeps
                                      and not str(tx.id or '').startswith('WASH_')
                                      and float(tx.net_amount) < -0.005)
                        # An s.86.1 spin-off distributed after the parent
                        # was sold: s.86.1 reduces the cost of the
                        # original shares even when they were disposed of
                        # before the distribution, so the reduction goes
                        # to the sales that closed the holding — not a
                        # return-of-capital gain dated the distribution
                        # (TJ-0842).
                        _s861_recs = (
                            _s861_prior_sales(
                                pool, symbol, iteration_realized_gains,
                                parent_qty=tx.corp_parent_qty,
                                account=tx.account)
                            if (_empty_roc and tx.id in taxable_ids
                                and tx.corp_election == 'rollover_s_86_1'
                                and not _s861_holding_moved(
                                    current_tx_list,
                                    pool.get('closed_holding'), tx, symbol,
                                    sheltered_ids | affiliated_ids))
                            else None)
                        if _s861_recs:
                            _cut = -float(tx.net_amount)
                            _sq = sum(_w for _r, _w in _s861_recs)
                            for _r, _w in _s861_recs:
                                _part = _cut * _w / _sq
                                _r['cost'] -= _part
                                _r['gain'] += _part
                                _r['taxable_gain'] += _part
                            if iteration == 0:
                                _sold = sorted({r['date']
                                                for r, _w in _s861_recs})
                                emit_line(
                                    f"note: {symbol}: the s.86.1 spin-off "
                                    f"on {tx.date} reduces the cost of "
                                    f"the shares sold before it (sold "
                                    f"{', '.join(_sold)}) by "
                                    f"{_cut:.2f}: their gain rises by "
                                    f"that amount in the year of the "
                                    f"sale (s.86.1). If that year's "
                                    f"return is filed, it needs an "
                                    f"adjustment.")
                            _empty_roc = False
                        elif (_empty_roc and iteration == 0
                                and tx.corp_election == 'rollover_s_86_1'):
                            emit_line(
                                f"warning: {symbol}: the s.86.1 spin-off "
                                f"on {tx.date} reduces the cost of shares "
                                f"sold before it, and taxjson cannot move "
                                f"the reduction to that sale (a loss, a "
                                f"superficial loss, a missing purchase or "
                                f"no taxable sale on these books): booked "
                                f"below as a gain on {tx.date}. Re-date "
                                f"the parent's cost reduction before the "
                                f"sale (a .tt ADJUST) to book it there.")
                        # (an s.86.1 reduction's own line is said above)
                        _s861_said = bool(_s861_recs) or (
                            _empty_roc
                            and tx.corp_election == 'rollover_s_86_1')
                        if (not _s861_said
                                and abs(pool['qty']) <= _aeps
                                and not str(tx.id or '').startswith('WASH_')
                                and abs(float(tx.net_amount)) > 0.005
                                and iteration == 0):
                            # iteration gate: the solver replays the
                            # whole book each pass, so an unconditional
                            # warning printed once PER ITERATION — one
                            # data problem masqueraded as several.
                            if _empty_roc:
                                emit_line(f"warning: {symbol} return of capital "
                                    f"of {-float(tx.net_amount):.2f} on "
                                    f"{tx.date} hits an EMPTY pool — the "
                                    f"position was fully sold before it "
                                    f"posted, so there is no ACB to "
                                    f"reduce: booked as a capital gain in "
                                    f"that year (ITA s.40(3), ACB nil). "
                                    f"If it belongs to the sold position, "
                                    f"re-date the ADJUST before the final "
                                    f"sale instead.")
                            else:
                                emit_line(f"warning: {symbol} ADJUST of "
                                    f"{float(tx.net_amount):.2f} on {tx.date} "
                                    f"hits an EMPTY pool — the position was "
                                    f"fully sold before this ADJUST posted, so "
                                    f"the amount would leak into the NEXT "
                                    f"position's ACB instead of the one that "
                                    f"earned it. Re-date the ADJUST before "
                                    f"the final sale (adjusting that "
                                    f"disposition's gain) or apply it "
                                    f"manually.")
                        _applied_adj = float(tx.net_amount)
                        if _s861_recs:
                            # Moved to the sales: nothing left for the
                            # empty pool (it would reach the next
                            # purchase's cost).
                            _applied_adj = 0.0
                        if (pool['qty'] < -_aeps
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
                                emit_line(f"note: {symbol}: ADJUST of "
                                      f"{float(tx.net_amount):+.2f} on "
                                      f"{tx.date} lands on a SHORT "
                                      f"position — booked as the short "
                                      f"seller's compensation payment "
                                      f"(it changes the cover's gain by "
                                      f"{-float(tx.net_amount):+.2f}).")
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
                            if pool['qty'] > _aeps:
                                _applied_adj = _mag
                            elif pool['qty'] < -_aeps:
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
                        _tc_before = float(pool['total_cost'])
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
                        _ck = (tx.account, tx.corp_event_id)
                        if (_ck in _cev_moves and _applied_adj < 0
                                and _tc_before > 0.005
                                and pool.get('deferred_wash', 0.0) > 1e-9):
                            # The ACB given up carries its share of the
                            # tally to the new shares (TJ-0062).
                            _mv = (pool['deferred_wash']
                                   * min(1.0, -_applied_adj / _tc_before))
                            pool['deferred_wash'] -= _mv
                            if pool.get('wash_cut'):
                                pool['wash_cut'] *= max(
                                    0.0, 1.0 + _applied_adj / _tc_before)
                            _cp = _cev_child.get(_ck)
                            if _cp is not None:
                                _cp['deferred_wash'] = (
                                    _cp.get('deferred_wash', 0.0) + _mv)
                            else:
                                _cev_stash[_ck] = (_cev_stash.get(_ck, 0.0)
                                                   + _mv)
                        if (_applied_adj < 0 and pool['qty'] > _aeps
                                and not str(tx.id or '').startswith('WASH_')
                                and pool.get('deferred_wash', 0.0) > 1e-9):
                            # The s.53(1)(f) additions are part of the
                            # ACB: what a return of capital leaves of it
                            # (nil past it, below) is all of them the
                            # pool still holds, so a later sale does not
                            # count them again (CA-RPT-16; backlog
                            # TJ-1017). What it took is kept apart: a
                            # reversal brings it back, and the sale
                            # reports it (`deferred_loss_cut`), the ACB
                            # having been short by any premium it held
                            # as well (TJ-1217, TJ-1219).
                            _acb_left = max(0.0, float(pool['total_cost']))
                            if pool['deferred_wash'] > _acb_left:
                                _new_cut = pool['deferred_wash'] - _acb_left
                                _had_cut = pool.get('wash_cut', 0.0)
                                # A premium delivered since the last cut
                                # is in the ACB this one reads.
                                _late = max(0.0, pool.get('cut_late', 0.0)
                                            * _had_cut - _new_cut)
                                pool['wash_cut'] = _had_cut + _new_cut
                                pool['cut_late'] = _late / pool['wash_cut']
                                pool['deferred_wash'] = _acb_left
                        if (_applied_adj > 0 and pool['qty'] > _aeps
                                and tx.type == 'roc'
                                and pool.get('wash_cut', 0.0) > 1e-9):
                            # A return of capital reversed: the ACB it
                            # gives back holds the additions it took
                            # (TJ-1219). New cost (type=dist) does not.
                            _back = min(pool['wash_cut'], max(
                                0.0, float(pool['total_cost'])
                                - pool.get('deferred_wash', 0.0)))
                            pool['wash_cut'] -= _back
                            pool['deferred_wash'] = (
                                pool.get('deferred_wash', 0.0) + _back)
                        # ITA s.40(3): return-of-capital that drives ACB
                        # below zero is a deemed capital gain in that
                        # year. Rare enough to warrant human eyes rather
                        # than silent computation — flag, don't book.
                        if (pool['qty'] > _aeps and pool['total_cost'] < D('-0.005')
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
                            emit_line(f"NOTE: {symbol}: return of capital on "
                                f"{tx.date} exceeded the ACB by "
                                f"{_excess:.2f} — booked as a deemed "
                                f"capital gain in that year (ITA s.40(3)); "
                                f"ACB reset to nil.")
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
                        # A marked short's own pool (TJ-0721) splits
                        # and follows a rename with the shares.
                        _tgt = (tx.symbol_new or '').strip() or symbol
                        _msr.split(symbol, _tgt, qty)
                        _ssp = global_pools.pop(symbol + _SHORT_SFX, None)
                        if _ssp is not None:
                            _ssp['qty'] *= qty
                            _sx = global_pools.get(_tgt + _SHORT_SFX)
                            if _sx is None:
                                global_pools[_tgt + _SHORT_SFX] = _ssp
                            else:
                                _sx['qty'] += _ssp['qty']
                                _sx['total_cost'] += _ssp['total_cost']
                                _sx['deferred_wash'] = (
                                    _sx.get('deferred_wash', 0.0)
                                    + _ssp.get('deferred_wash', 0.0))
                    # Rename the pool when symbol_new is set and differs
                    # from the source symbol — that's how mergers and
                    # corporate reorganizations move ACB onto a new
                    # ticker (e.g. ABG.TO → ABH.US 1-for-16 via the
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
                                if pool.get('wash_cut'):
                                    _add_wash_cut(existing, pool['wash_cut'],
                                                  pool.get('cut_late', 0.0))
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
                                # else keep parking. The TARGET's own
                                # parked dollars count too: a flat
                                # target the rename brings shares into
                                # opens here, as a purchase opens a
                                # flat pool — leaving them parked
                                # denied the loss for good (issue #91,
                                # CA-SL-19). The deferred_wash tally
                                # already holds both sides' dollars
                                # (summed above): only the cost moves.
                                _pw = (pool.pop('pending_wash', 0.0)
                                       + existing.pop('pending_wash',
                                                      0.0))
                                if _pw > 1e-9:
                                    _reps = pool_qty_eps(target_symbol, existing['qty'])
                                    if existing['qty'] > _reps:  # cov: a2-1596-rename-pending-wash
                                        existing['total_cost'] += D(_pw)
                                    elif existing['qty'] < -_reps:
                                        existing['total_cost'] += D(-_pw)
                                    else:
                                        existing['pending_wash'] = _pw
                                # (and its cause: an own move's
                                # units, backlog TJ-0279)
                                _taint_from(existing, pool)
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
                                if pool.get('opening_acq'):
                                    existing['opening_acq'] = min(
                                        existing.get('opening_acq')
                                        or pool['opening_acq'],
                                        pool['opening_acq'])
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
                        _taint(pool)
                        if qty < 0 and is_option_symbol(symbol):
                            # Missing-history short contracts hold a FIFO place
                            # (never recognised — their write predates
                            # the data).
                            _short_lot_open(pool, tx, abs(qty), 0.0, False)
                    if trace:
                        if symbol not in symbol_acb_traces:
                            symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                        symbol_acb_traces[symbol].append(f"# {tx.date} OPENING_BALANCE {qty:10.4f} | Missing history (bought before the data) — cost UNKNOWN until the pool drains to zero")
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
                    # A position under the tolerance that still carries
                    # real cost, reversed by this trade, is a holding,
                    # not rounding noise: closed, not read as flat —
                    # the sale opened a short and stranded the cost
                    # (2026-10 audit #101). A true zero still opens.
                    _dust_close = (abs(pool['qty']) <= _qeps
                                   and pool['qty'] != 0
                                   and abs(float(pool['total_cost']))
                                   >= 0.005
                                   and (pool['qty'] > 0) == (qty < 0))
                    is_opening = (pool['qty'] > _qeps and qty > 0) or \
                                 (pool['qty'] < -_qeps and qty < 0) or \
                                 (abs(pool['qty']) <= _qeps
                                  and not _dust_close)

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
                            if internal_tainted:
                                # Delivered by an option held at an
                                # unknown cost: so is this (TJ-0947).
                                _taint(pool)
                            if internal_dw > 1e-9:
                                # The option's deferral rides its
                                # premium into these shares (TJ-0061).
                                pool['deferred_wash'] = (
                                    pool.get('deferred_wash', 0.0)
                                    + internal_dw)
                            _ck = (tx.account, tx.corp_event_id)
                            if qty > 0 and _ck in _cev_moves:
                                # A reallocation's new shares: the
                                # parent's tally share, if it gave its
                                # ACB up first (TJ-0062).
                                _cev_child[_ck] = pool
                                _mv = _cev_stash.pop(_ck, 0.0)
                                if _mv > 1e-9:
                                    pool['deferred_wash'] = (
                                        pool.get('deferred_wash', 0.0)
                                        + _mv)
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
                                pool['position_start_date'] = (
                                    (tx.lot_date or tx.date)
                                    if is_opening_row(tx) else tx.date)
                            pool['qty'] += qty
                            if is_opening_row(tx):
                                # Not a purchase (CA-OPEN-01): the
                                # planning views' last-acquisition date
                                # (the window signal) is not moved; the
                                # days held of a sale from an opening-only
                                # pool count from its lot date, else the
                                # snapshot day.
                                pool['opening_acq'] = max(
                                    pool.get('opening_acq') or '',
                                    tx.lot_date or tx.date)
                            else:
                                pool['last_acq_date'] = tx.date
                                # The s.54 window runs on settle dates
                                # (CA-SL-01): the planning views measure
                                # from this one (A2-0958). last_acq_date
                                # stays the TRADE date (days held).
                                pool['last_acq_settle'] = get_sort_date(tx)

                            if qty < 0 and is_option_symbol(symbol):
                                _open_short_option(pool, tx, abs(qty),
                                                   effective_cost, 1.0)

                            if trace:
                                if symbol not in symbol_acb_traces:
                                    symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                fee_amt = _effective_fee_for_trace(tx)
                                pool_cost_f = float(pool['total_cost'])
                                acb_sh = _per_share(pool_cost_f, pool['qty'], symbol)
                                _lbl = ('OPENING' if is_opening_row(tx)
                                        else tx.action)
                                symbol_acb_traces[symbol].append(f"# {tx.date} {_lbl} {qty:10.4f} @ {tx.price:7.4f} | Fee: {fee_amt:6.4f} | Cost_Added: {effective_cost:10.4f} | Pool_Qty: {pool['qty']:10.4f} | Pool_ACB: {pool_cost_f:10.4f} | ACB/Sh: {acb_sh:7.4f}")
                        else:
                            # SELL (or Short covering) — divide in exact arithmetic.
                            if abs(pool['qty']) > _qeps or _dust_close:
                                # (A sub-tolerance position with real
                                # cost is closed at its cost: #101.)
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
                            _plain_close = float(D(closing_qty)
                                                 * avg_cost_unit_d)
                            if pool['qty'] < 0 and pool.get('grants'):
                                # Short option lots: FIFO by write, the
                                # closing account's first; recognised lots
                                # leave at their own premium (see
                                # _short_lot_close).
                                _lots = pool['grants']
                                # Close timing, one account's lots or lots
                                # that do not hold the whole pool: the
                                # pool's average.
                                _avg_close = (
                                    not _grant_mode
                                    and (len({l.get('account')
                                              for l in _lots}) < 2
                                         or abs(sum(l['units'] for l in _lots)
                                                - abs(pool['qty'])) > 1e-9))
                                (cost_basis, _recognized,
                                 _grant_units_closed, _grant_closed) = \
                                    _short_lot_close(pool, closing_qty,
                                                     is_option_assign,
                                                     tx.account)
                                if _avg_close:
                                    cost_basis = _plain_close
                            else:
                                cost_basis = _plain_close
                            
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
                                _acq_d = pool['last_acq_date']
                                if (pool.get('opening_acq')
                                        and _acq_d == '1970-01-01'):
                                    _acq_d = pool['opening_acq']
                                acq_dt = datetime.strptime(_acq_d, '%Y-%m-%d')
                                disp_dt = datetime.strptime(tx.date, '%Y-%m-%d')
                                raw_days = (disp_dt - acq_dt).days
                                if raw_days < 0:
                                    emit_line(f"warning: negative days_held for {tx.symbol} "
                                        f"on {tx.date} (last_acq={pool['last_acq_date']}); "
                                        f"this usually indicates out-of-order transactions")
                                days_held = max(0, raw_days)
                            except ValueError: days_held = 0
                            
                            # Check for taxable event (if tx is in the main transactions list)
                            is_taxable = tx.id in taxable_ids
                            
                            if is_taxable and not is_option_assign and _suppress_record:
                                # No disposition, but the contract closed
                                # this day: a broker's close-year slip
                                # reports it, at its write's premium
                                # (reconcile-slips, backlog TJ-1091).
                                iteration_grant_nil_closes.append({
                                    'date': tx.date,
                                    'date_settle': tx.date_settle or tx.date,
                                    'symbol': symbol, 'account': tx.account,
                                    'grant_closed': {
                                        y: {'units': round(v['units'], 9),
                                            'premium': round(v['premium'], 6)}
                                        for y, v in _grant_closed.items()}})
                            if is_taxable and not is_option_assign and not _suppress_record:
                                # Check for virtual disallowance for this specific ID
                                disallowance_tx = next(iter(_dis_by_id.get(tx.id, ())), None)
                                if disallowance_tx:
                                    disallowed_amt = disallowance_tx.net_amount
                                
                                realized_pl = _rec_gain
                                
                                rg_trace = []
                                if trace:
                                    if symbol not in symbol_acb_traces:
                                        symbol_acb_traces[symbol] = [f"# --- ACB CALCULATION TRACE: {symbol} ---"]
                                    fee_amt = _effective_fee_for_trace(tx)
                                    # (shared, not copied: SharedTrace)
                                    rg_trace = SharedTrace(symbol_acb_traces[symbol])
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
                                is_tainted = (pool.get('tainted', False)
                                              or internal_tainted)
                                # An in-kind contribution of LONG shares
                                # to a registered plan (lib/in_kind): a
                                # sale at fair market value whose loss
                                # s.40(2)(g)(iv) denies for good — not a
                                # superficial loss, no ACB addition
                                # anywhere (CA-INKIND-03).
                                _ik_type = getattr(tx, 'type', '') or ''
                                _ik = (_ik_type if _ik_type in (
                                    IN_KIND_CONTRIBUTION_TYPE,
                                    IN_KIND_DISPOSITION_TYPE)
                                    and pool['qty'] > 0 else '')
                                _ik_denied = (
                                    -_rec_gain
                                    if (_ik == IN_KIND_CONTRIBUTION_TYPE
                                        and _rec_gain < -0.001
                                        and not is_tainted) else 0.0)
                                # The earlier superficial losses this
                                # disposition's ACB carries (the s.53(1)(f)
                                # additions the pool's deferred_wash
                                # tallies; a short's: taken off its
                                # proceeds), released in the same share
                                # as the drain below releases them. The
                                # views that count economic P/L before
                                # any denial take it back out (CA-RPT-16;
                                # audit 2026-10-25 planning-us#1).
                                _dw_abs = abs(pool['qty'])
                                _dw_out = (
                                    pool.get('deferred_wash', 0.0)
                                    * min(1.0, closing_qty / _dw_abs)
                                    if _dw_abs > _qeps else 0.0)
                                # ...and an exercised option's, in the
                                # premium this sale absorbs (TJ-0061).
                                _dw_out += internal_dw * (closing_qty
                                                          / abs(qty))
                                _dw_cut = (
                                    pool.get('wash_cut', 0.0)
                                    * min(1.0, closing_qty / _dw_abs)
                                    if _dw_abs > _qeps else 0.0)
                                _late_prem = (pool.get('cut_late', 0.0)
                                              * _dw_cut
                                              + max(0.0, -float(chunk_adj)))
                                iteration_realized_gains.append({
                                    'tx_id': tx.id, 'symbol': symbol, 'date': tx.date,
                                    'date_settle': tx.date_settle or tx.date,
                                    'gain': _rec_gain,
                                    'qty': closing_qty, 'cost': _rec_cost, 'proceeds': effective_proceeds,
                                    'disallowed': disallowed_amt, 'taxable_gain': _rec_gain + disallowed_amt + _ik_denied,
                                    **({'in_kind': 'contribution',
                                        'denied_contribution': _ik_denied,
                                        'note': (
                                            'in-kind contribution to a '
                                            'registered plan: a sale at '
                                            'fair market value'
                                            + ('; the loss is denied '
                                               '(s.40(2)(g)(iv)), no ACB '
                                               'addition'
                                               if _ik_denied else '')
                                            if _ik == IN_KIND_CONTRIBUTION_TYPE
                                            else 'in-kind contribution to '
                                            'a plan s.40(2)(g)(iv) does '
                                            'not name: a sale at fair '
                                            'market value')}
                                       if _ik else {}),
                                    'days_held': days_held, 'account': account,
                                    'currency': tx.currency,
                                    'commission': float(tx.commission or 0) * fee_share,
                                    'fee': float(tx.fee or 0) * fee_share,
                                    'direction': 'LONG' if pool['qty'] > 0 else 'SHORT',
                                    'tainted': is_tainted,
                                    # Its unknown cost is an own move's
                                    # only: the accounts they came from
                                    # (backlog TJ-0279).
                                    **({'own_move_from': _own_move_only(pool)}
                                       if is_tainted and not internal_tainted
                                       and _own_move_only(pool)
                                       else {}),
                                    'trace': rg_trace,
                                    **({'deferred_loss_recovered': _dw_out}
                                       if _dw_out > 1e-9 else {}),
                                    **({'deferred_loss_cut': _dw_cut}
                                       if _dw_cut > 1e-9 else {}),
                                    # (a written call's premium in these
                                    # proceeds was in no ACB either)
                                    **({'premium_after_cut':
                                        _late_prem}
                                       if _dw_cut > 1e-9 and _late_prem > 1e-9
                                       else {}),
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
                                if (_rec_gain < -0.001 and not is_tainted
                                        and _wash_eligible and not _ik_denied):
                                    iteration_losses.append({
                                        'tx': tx, 'loss_amount': abs(_rec_gain),
                                        'qty': closing_qty, 'direction': 'LONG' if pool['qty'] > 0 else 'SHORT'
                                    })
                            
                            if is_option_assign:
                                underlying = _assign_underlying(tx)
                                if underlying:
                                    # The contracts' share of the pool's
                                    # deferral tally goes with the
                                    # premium (TJ-0061).
                                    _dw_opt = (
                                        pool.get('deferred_wash', 0.0)
                                        * min(1.0, closing_qty
                                              / abs(pool['qty']))
                                        if abs(pool['qty']) > _qeps
                                        else 0.0)
                                    pending_adjustments.stage(
                                        tx, underlying, -gain, _dw_opt,
                                        bool(pool.get('tainted')))

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
                                if pool.get('wash_cut'):
                                    pool['wash_cut'] *= max(
                                        0.0, 1.0 - closing_qty / _pre_abs)
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
                                pool['deferred_wash'] = (internal_dw
                                                         * leftover_ratio)
                                pool['wash_cut'] = 0.0
                                if internal_tainted:
                                    _taint(pool)
                                pool['last_acq_date'] = tx.date
                                pool['last_acq_settle'] = get_sort_date(tx)
                                # Position flipped direction (long→short
                                # or vice versa via a cross-zero SELL).
                                # New position begins at this trade.
                                pool['position_start_date'] = tx.date
                                pool['grants'] = []
                                if qty < 0 and is_option_symbol(symbol):
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

                # A residue under the tolerance is drained to zero — but
                # not a position this trade OPENED that carries real
                # cost: zeroed right after its buy, its sale found an
                # empty pool and the cost was stranded (2026-10 audit
                # #101; the cash-conservation fuzz). Nor one a row left
                # as it was (a cost adjustment) or scaled whole (a
                # consolidation into a fraction of a share: TJ-0649, the
                # quantity was cleared, its cost stranded and the sale's
                # gain lost), nor the rest after part of such a position
                # is sold (TJ-1062): only a disposal of a position above
                # the tolerance leaves a residue.
                _opened_dust = (
                    (abs(pool['qty']) >= abs(_pre_qty)
                     or action == 'SPLIT'
                     or (abs(_pre_qty) < pool_qty_eps(symbol, _pre_qty, qty)
                         and abs(pool['qty']) > 1e-6 * abs(_pre_qty)))
                    and not pool_is_flat(symbol, pool['qty'],
                                         pool['total_cost']))
                if (abs(pool['qty']) < pool_qty_eps(symbol, _pre_qty, qty)
                        and not _opened_dust):
                    pool['qty'] = 0.0
                    if _pre_qty > 0 and not is_other_scope:
                        # The long holding this sale closed: an s.86.1
                        # spin-off distributed after it reduces the
                        # cost of these sales (_s861_prior_sales).
                        pool['closed_holding'] = (
                            pool['position_start_date'] or '', tx.date,
                            id(tx))
                    # Pool drained — clear position_start_date so the
                    # next open seeds a fresh start. Critical for the
                    # "opened, closed, reopened" pattern: the user
                    # wants the date of the SECOND open, not the first.
                    pool['position_start_date'] = None
                    pool['grants'] = []
                    # Missing-history shares are fully drained — the pool re-cleans.
                    # Subsequent buys form a fresh, fully-known ACB.
                    if pool.get('tainted', False):
                        _untaint(pool)
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
            if _tl is not None and _tl_prev:
                _tl_after(len(_tl_dates) - 1, _tl_prev[0])
                _tl.sweep(len(_tl_dates) - 1)
            if year_end and _ye_pools is None:
                _ye_pools = _pools_copy(global_pools)
                _ye_shorts = dict(_msr.short)

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
            _ov_would = {}
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
            # in name order, issue #31). Every same-moment question below —
            # which loss claims a shared replacement first, whether a rebuy
            # listed after the loss sale is acquired after it, which of two
            # same-moment triggers takes the bump — is answered by this
            # order, never by the rows' content-hash id
            # (audit A2-0059/0551/0058/0193/0192/0961/0965: a one-cent
            # price change used to move a denial).
            _pos = {id(_t): _i for _i, _t in enumerate(current_tx_list)}
            # A class-share option root names its class line (SAMPLD for
            # SAMPLD.B.TO — CA-SL-05; A2-0015/0016).
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
                # A short futures leg is its own position (lib/futures
                # engine_pool_key): a short opened in one account never
                # covers, nor is covered by, a long held in another
                # (TJ-0651). Every other row: its symbol.
                _k = ((_t.account
                       if (_t.id in sheltered_ids
                           or _t.id in affiliated_ids)
                       else '\x00taxable'), _row_pool_key(_t))
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
                if id(t) in _open_memo:      # (this pass's, TJ-0105)
                    return _open_memo[id(t)]
                bal = _bal_before.get(t.id, 0.0)
                q = t.quantity
                covering = min(q, max(0.0, -bal))
                _open_memo[id(t)] = r = max(0.0, q - covering)
                return r
            _open_memo: Dict[int, float] = {}
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
            if iteration_losses_to_check and _sl_trg is None:
                # Each class's candidate replacements (purchases), dated
                # by the sort date the trigger scan tests.
                _sl_trg = {}
                for _c, _rows in _rows_of_cls.items():
                    _cand = [t for t in _rows
                             if t.action in ('BUYSELL', 'ASSIGN')
                             and not not_a_purchase(t)]
                    if _cand:
                        _sl_trg[_c] = _DatedRows(_cand, get_sort_date)
            # This pass's balance rows per class and per symbol, in
            # processing order (the day-30 walks), built on first use.
            _sl_bal: Optional[Dict[str, list]] = None
            _sl_bal_sym: Dict[str, list] = {}
            _sl_views: Dict[str, _ClassBalances] = {}
            _sl_wpos: Optional[Dict[Any, int]] = None
            _adj_idx = _WashAdjustIndex(final_virtual_txs)

            def _class_balances(c) -> Optional[_ClassBalances]:
                nonlocal _sl_bal
                if _sl_bal is None:
                    _sl_bal = {}
                    for t in current_tx_list:
                        # Owned property only: a short futures leg (or
                        # a broker-marked share short, TJ-0721) is an
                        # obligation, never a holding, so it does not
                        # net the long units held at day 30 down
                        # (TJ-0651; any other short stays signed).
                        if (t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                         'OPENING_BALANCE')
                                and _row_pool_key(t) == t.symbol):
                            _sl_bal.setdefault(row_cls(t), []).append(t)
                            _sl_bal_sym.setdefault(t.symbol, []).append(t)
                if c not in _sl_views:
                    _sl_views[c] = _ClassBalances(
                        _sl_bal.get(c, []), get_sort_date, _holder,
                        lambda t: (t.id not in sheltered_ids
                                   and t.id not in affiliated_ids),
                        _split_syms)
                return _sl_views[c]

            # "Owns the substituted property" is measured per holder
            # (see the day-30 walk below): the taxable book, or one
            # registered / affiliated account.
            def _holder(t):
                if t.id in sheltered_ids or t.id in affiliated_ids:
                    return ('other', t.account)
                return ('taxable',)

            def _holder_rank(t):
                return (2 if t.id in affiliated_ids
                        else 1 if t.id in sheltered_ids else 0)
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
                # Bridge SPLIT-renames: a post-rename ABH.US buy in the
                # 30-day window after an ABG.TO loss is a candidate
                # trigger under CRA's substantially-identical rule. Match
                # on equivalence-class representative instead of raw
                # symbol. `alias_of` is identity for symbols never
                # renamed, so this is a no-op for the common case.
                loss_alias = row_cls(tx)
                potential_triggers = []
                # The class's purchases dated inside the window, in book
                # order (the walk of the whole book kept exactly these);
                # the whole book when a date there does not parse.
                _trg_rows = _sl_trg.get(loss_alias)
                _indexed = _trg_rows is None or _trg_rows.ok
                for t in (all_txs if not _indexed
                          else _trg_rows.within(loss_date.toordinal(), 30)
                          if _trg_rows is not None else ()):
                    if not _indexed and row_cls(t) != loss_alias:
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
                    if not _indexed and t.action not in ('BUYSELL',
                                                         'ASSIGN'):
                        continue
                    # A transfer-in at its book value (an arrival) and an
                    # opening balance (a positions report's snapshot) are
                    # not purchases: never a replacement, never "acquired
                    # in the window" — their shares still count as held
                    # at day 30 through the balances (CA-OPEN-01).
                    if not _indexed and not_a_purchase(t):
                        continue
                    if not _indexed:
                        t_date = datetime.strptime(get_sort_date(t),
                                                   '%Y-%m-%d')
                    if _indexed or abs((t_date - loss_date).days) <= 30:
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
                    if _sl_call is None:
                        # The long-call purchases on each class (by the
                        # class of the underlying on the trade date).
                        _calls_of: Dict[str, list] = {}
                        for t in all_txs:
                            if (t.action != 'BUYSELL' or t.quantity <= 0
                                    or not_a_purchase(t)
                                    or parse_option_right(t.symbol) != 'C'):
                                continue
                            _und = _call_und(t.symbol)
                            if not _und or _FUTURES_PREFIX_RE.match(_und):
                                continue
                            _calls_of.setdefault(
                                split_timeline.class_at(_und, t.date),
                                []).append(t)
                        _sl_call = {c: _DatedRows(r, get_sort_date)
                                    for c, r in _calls_of.items()}
                    _call_rows = _sl_call.get(loss_alias)
                    _cindexed = _call_rows is None or _call_rows.ok
                    for t in (all_txs if not _cindexed
                              else _call_rows.within(loss_date.toordinal(),
                                                     30)
                              if _call_rows is not None else ()):
                        if t.id == tx.id:
                            continue
                        if not _cindexed:
                            if (t.action != 'BUYSELL'
                                    or t.quantity <= 0
                                    or not_a_purchase(t)
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

                # A contract that expires by day 30 is not owned at the
                # END of day 30, with or without an expiry row in the
                # export (audit S071-17: a missing EXP row parked a
                # realized long-option loss on a contract that can never
                # be sold). One expiring ON day 30 is gone by its end too
                # (TJ-0725); an exercise into shares is its own row.
                def _gone_by_end(t) -> bool:
                    return (is_option_symbol(t.symbol)
                            and (parse_option_expiry(t.symbol) or '9999')
                            <= end_window_date)

                # The class's balance rows (BUYSELL, ASSIGN, TRANSFER and
                # OPENING_BALANCE — missing-history shares are held: see
                # the running_bal_by_tx walk above) in processing order;
                # the first _k_end are dated by day 30 (None: not in date
                # order, every row is tested). _unit: no split or rename
                # acts on the class, so its quantities are loss units.
                _cb = _class_balances(loss_alias)
                _k_end = _cb.prefix(end_window_date)
                _unit = (_cb.unit_symbol == tx.symbol
                         and _k_end is not None)

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
                # engine audit).
                # (_holder, _holder_rank: defined above the loop.)
                _bal_end_h: Dict[Any, float] = {}
                if _unit:
                    if not _gone_by_end(tx):
                        _bal_end_h = _cb.holder_sums(_k_end)
                else:
                    for t in _cb.rows:
                        if (not _gone_by_end(t)
                                and get_sort_date(t) <= end_window_date):
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
                                  + (_opening_qty(t) * 1.0 if _unit
                                     else _row_loss_units(t,
                                                          _opening_qty(t))))
                _held_h = {h: max(0.0, min(a, _bal_end_h.get(h, 0.0))
                                  - _grp_claim.get(h, 0.0))
                           for h, a in _acq_h.items()}
                # A move of your own between two of the accounts
                # (balance-only TRANSFER rows, pipeline.OWN_MOVE_TYPE)
                # inside the window carries the units it moves: those
                # acquired in the window back a denial in the account
                # that holds them at day 30 (backlog TJ-0654: r1's
                # replacement moved to r2 and held there was lost, the
                # sender having no units and the receiver no purchase),
                # and older units moved in back nothing.
                _held_mv = _own_move_held(
                    loss_date, end_window_date, _cb.rows,
                    potential_triggers, _call_ids, _holder,
                    _opening_qty, _gone_by_end,
                    (lambda t, q: q) if _unit else _row_loss_units,
                    get_sort_date)
                if _held_mv is not None:
                    _held_h = {h: max(0.0, n - _grp_claim.get(h, 0.0))
                               for h, n in _held_mv.items()}
                # Calls back a denial per (holder, contract): units of
                # THAT series opened in the window and still held at day
                # 30 — a contract bought, sold and bought again counts
                # once (A2-0057/0198). Share balances never back a call
                # and vice versa.
                if call_triggers:
                    # A series and the series a SPLIT replaced it with
                    # are one contract here (TJ-0716): keyed by the
                    # lineage's first symbol, every symbol of it walked.
                    _call_acq: Dict[Any, float] = {}
                    for t in call_triggers:
                        _k = ('call', _holder(t), _opt_root(t.symbol))
                        _call_acq[_k] = (_call_acq.get(_k, 0.0)
                                         + _call_units(t, _opening_qty(t)))
                    _call_end: Dict[Any, float] = {}
                    _call_roots = {_opt_root(sym) for sym in _call_syms}
                    _call_walk = sorted(
                        {sym for _r in _call_roots
                         for sym in _opt_lineage.get(_r, (_r,))})
                    # A call that expires by day 30 (on it too: TJ-0725)
                    # is not held at the end of day 30, with or without
                    # an expiry row (S071-15).
                    _expired = {sym for sym in _call_walk
                                if (parse_option_expiry(sym) or '9999')
                                <= end_window_date}
                    for t in (t for _cs in _call_walk
                              for t in _sl_bal_sym.get(_cs, ())):
                        if (t.symbol not in _expired
                                and get_sort_date(t) <= end_window_date):
                            _k = ('call', _holder(t), _opt_root(t.symbol))
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
                    _post_ids = {id(t) for t in post_loss}
                    pre_loss = [t for t in potential_triggers
                                if id(t) not in _post_ids]
                    # Triggers at the SAME moment: the taxpayer's own
                    # (taxable) acquisition first, then registered, then
                    # affiliated accounts; within one rank, the main
                    # pass's processing order (export row order, accounts
                    # in name order — CA-DATE-14) — never the
                    # rows' content-hash id, which
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
                            _h = ('call', _hh, _opt_root(trg.symbol))
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
                    if _unit:
                        # One pool (no rename acts on the symbol): the
                        # taxable rows' running sum.
                        _n_tax, _s_tax = _cb.taxable_sum(_k_end)
                        if _n_tax:
                            _pool_back[tx.symbol] = _s_tax
                            _pool_repr[tx.symbol] = _cb.first_taxable()
                    for t in (() if _unit else _cb.rows):
                        if (get_sort_date(t) <= end_window_date
                                and t.id not in sheltered_ids
                                and t.id not in affiliated_ids):
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

                    if tx.id in _ov_plan.row_of:
                        # A filing position against this denial (CA-SL-18):
                        # the units it would use are used (every other
                        # sale's formula is unchanged), but no DISALLOW
                        # and no ACB addition are booked — what it would
                        # have been is recorded for the reports.
                        _rec = _ov_would.setdefault(tx.id,
                                                    _LO.new_record())
                        _rec['qty'] += loss['qty']
                        _rec['loss'] += loss['loss_amount']
                        _rec['disallowed'] += disallowed_amt
                        _rec['permanent'] += perm_amt
                        for trg, _q, _amt in allocations:
                            _LO.add_replacement(
                                _rec, trg, _q, _amt,
                                'sheltered' if trg.id in sheltered_ids
                                else 'affiliated' if trg.id in affiliated_ids
                                else 'taxable')
                        continue

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
                    existing_disallow = next(iter(_dis_by_id.get(tx.id, ())),
                                             None)
                    if existing_disallow:
                        # A pre-loss bump follows its sale's last losing
                        # fill as it moves (see _disp_last; A2-1596).
                        _moved = False
                        _wa = _disp_last.get(_disp_of.get(id(tx)), tx).id
                        for trg, _q, _amt in allocations:
                            if _pkey(trg) >= _loss_pkey:
                                continue
                            _v = next((v for v in _adj_idx.of(tx.id)
                                       if v.id == f"WASH_{tx.id}__{trg.id}"),
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
                        # (every ADJUST whose id starts WASH_<tx.id>__)
                        _new_amts = {f"WASH_{tx.id}__{trg.id}": amt
                                     for trg, _q, amt in allocations}
                        _seen_adj = set()
                        for v in _adj_idx.of(tx.id):
                            v.net_amount = _new_amts.get(v.id, 0.0)
                            _seen_adj.add(v.id)
                        for trg, _q, amt in allocations:
                            a_id = f"WASH_{tx.id}__{trg.id}"
                            if a_id not in _seen_adj:
                                final_virtual_txs.append(_mk_adjust(trg, amt))
                                _adj_idx.add(final_virtual_txs[-1])
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
                    # The loss symbol's rows dated inside the window and
                    # the call replacements, in book order (the walk of
                    # the whole book kept only these); the whole book
                    # when a date there does not parse. Dated, as the
                    # window and the DAY count are, on SETTLEMENT dates
                    # like the detection above: a trade date counted
                    # from the loss's settlement put the loss sale at
                    # T-1 (backlog TJ-0258).
                    if _sl_sym is None:
                        _sym_rows: Dict[str, list] = {}
                        for t in all_txs:
                            if t.action not in ('DISALLOW', 'ADJUST',
                                                'DIVIDEND', 'TAX',
                                                'INTEREST', 'FEE'):
                                _sym_rows.setdefault(t.symbol,
                                                     []).append(t)
                        _sl_sym = {_s: _DatedRows(_r, get_sort_date)
                                   for _s, _r in _sym_rows.items()}
                        _sl_book_pos = {id(t): i
                                        for i, t in enumerate(all_txs)}
                    _wrows = _sl_sym.get(tx.symbol)
                    if _wrows is not None and not _wrows.ok:
                        _wscan = all_txs
                    else:
                        _wscan = list({id(t): t for t in (
                            (_wrows.within(loss_date.toordinal(), 30)
                             if _wrows is not None else [])
                            + call_triggers)}.values())
                        _wscan.sort(key=lambda t: _sl_book_pos[id(t)])
                    for t in _wscan:
                        if t.id in seen:
                            continue
                        seen.add(t.id)
                        if t.symbol != tx.symbol and t.id not in _call_ids:
                            continue
                        if t.action in ('DISALLOW', 'ADJUST', 'DIVIDEND', 'TAX', 'INTEREST', 'FEE'):
                            continue
                        try:
                            t_dt = datetime.strptime(get_sort_date(t),
                                                     '%Y-%m-%d')
                        except (TypeError, ValueError):
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
                            'date_settle': get_sort_date(t),
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
                    if _sl_wpos is None:
                        _sl_wpos = {t.id: _pos.get(id(t), -1)
                                    for t in all_txs}
                    _wpos = _sl_wpos
                    win_txs.sort(key=lambda r: (r['days_from_loss'],
                                                r['date'],
                                                _wpos.get(r['tx_id'], -1)))
                    wash_windows[tx.id] = {
                        'window_start': window_start_dt.strftime('%Y-%m-%d'),
                        'window_end': end_window_date,
                        'loss_date': tx.date,
                        # The date the window and DAY count from.
                        'loss_settle': get_sort_date(tx),
                        'loss_direction': loss['direction'],
                        'bal_at_end': (
                            (0 if _gone_by_end(tx) else _cb.total(_k_end))
                            if _unit else
                            sum(_row_loss_units(t, t.quantity)
                                for t in _cb.rows
                                if not _gone_by_end(t)
                                and get_sort_date(t) <= end_window_date)),
                        'loss_qty': loss['qty'],
                        'disallowed_qty': disallowed_qty,
                        'transactions': win_txs,
                    }
                    final_virtual_txs.extend([disallow_vtx] + adjust_vtxs)
                    _dis_by_id.setdefault(disallow_vtx.id,
                                          []).append(disallow_vtx)
                    for _a in adjust_vtxs:
                        _adj_idx.add(_a)
                    found_new_wash_sale = True
                else:
                    # Nothing left to back this loss (an earlier fill of
                    # the same sale took the replacement, or a later pass
                    # moved the balance): withdraw a denial an earlier
                    # pass attached.
                    _stale = next((v for v in _dis_by_id.get(tx.id, ())
                                   if abs(float(v.net_amount)) > 0.001),
                                  None)
                    if _stale is not None:
                        _stale.net_amount = 0.0
                        _stale.quantity = 0.0
                        for v in _adj_idx.of(tx.id):
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
                final_grant_nil_closes = iteration_grant_nil_closes
                final_global_pools = global_pools
                final_year_end_pools = _ye_pools
                final_short_units = dict(_msr.short)
                final_ye_short_units = _ye_shorts
                final_timeline = ((_tl, _tl_shorts, _tl_dates)
                                  if _tl is not None else None)
                final_pool_snapshots = pool_snapshots_this_iter

                # Backfill wash-window rows with the final, converged pool
                # state. Earlier iterations may have written different pool
                # values (before later ADJUSTs landed), so this overwrite is
                # important for accuracy.
                for _wl_id, ww in wash_windows.items():
                    # A row's state "after the row" includes this
                    # denial's taxable ACB bump placed right after it
                    # (`_wash_after`: the trigger purchase, or a pre-loss
                    # trigger's loss sale) — the last one processed, as
                    # `list` shows the pool (backlog TJ-0111).
                    _bump_after: Dict[str, Any] = {}
                    for _a in final_virtual_txs:
                        _w = getattr(_a, '_wash_after', None)
                        if (_w and _a.action == 'ADJUST'
                                and _a.id.startswith(f"WASH_{_wl_id}__")
                                and _a.id not in sheltered_ids
                                and _a.id not in affiliated_ids
                                and _a.id in final_pool_snapshots
                                and (_w not in _bump_after
                                     or _pos.get(id(_a), -1)
                                     > _pos.get(id(_bump_after[_w]), -1))):
                            _bump_after[_w] = _a
                    for entry in ww.get('transactions', []):
                        _eid = entry.get('tx_id', '')
                        snap = final_pool_snapshots.get(
                            _bump_after[_eid].id if _eid in _bump_after
                            else _eid)
                        if snap:
                            entry['pool_qty_after'] = snap['pool_qty']
                            entry['pool_acb_after'] = snap['pool_acb']
                            if abs(snap['pool_qty']) > pool_qty_eps(
                                    entry.get('symbol') or '', snap['pool_qty']):
                                entry['acb_per_share_after'] = _per_share(
                                    snap['pool_acb'], snap['pool_qty'],
                                    entry.get('symbol') or '')
                            else:
                                entry['acb_per_share_after'] = 0.0
                final_wash_sales = []
                _adj_idx = _WashAdjustIndex(final_virtual_txs)
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
                        _adjs = [a for a in _adj_idx.of(v.id)
                                 if abs(a.net_amount) > 1e-9
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
                                emit_line(f"warning: skipped wash-trace "
                                      f"context for {v.symbol}@{v.date} "
                                      f"({e})")
                        
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
            emit_line(f"warning: the superficial-loss solver did not converge "
                f"within {solver_iterations_used} iterations — the "
                f"superficial-loss list "
                f"may be incomplete and ACB pools may be inconsistent. "
                f"Check for unusual same-symbol activity in your input.")
            # Fall back to the last iteration's state.
            if not final_realized_gains:
                final_realized_gains = iteration_realized_gains
                final_grant_nil_closes = iteration_grant_nil_closes
                final_global_pools = global_pools
                final_year_end_pools = _ye_pools
                final_short_units = dict(_msr.short)
                final_ye_short_units = _ye_shorts
                final_timeline = ((_tl, _tl_shorts, _tl_dates)
                                  if _tl is not None else None)
        
        # Format results
        processed_gains = []
        by_ticker = {}
        # Each denial's trigger row and ADJUSTs by id (first in list
        # order, as the walks of every row found them: TJ-0105).
        _book_row_of: Dict[Any, Any] = {}
        for t in all_txs:
            _book_row_of.setdefault(t.id, t)
        _adj_out = _WashAdjustIndex(final_virtual_txs)
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
            if g.get('own_move_from'):
                # Units moved in from another of your accounts, their
                # cost in the blended figures (backlog TJ-0279).
                gain_entry['own_move_from'] = list(g['own_move_from'])
            if g.get('in_kind'):
                # CA-INKIND-02/03: the sale of an in-kind contribution;
                # the s.40(2)(g)(iv) denial is its own figure, never the
                # superficial-loss one.
                gain_entry['in_kind'] = g['in_kind']
                gain_entry['denied_contribution'] = round(
                    float(g.get('denied_contribution') or 0.0), 6)
            if g.get('deferred_loss_cut'):
                gain_entry['deferred_loss_cut'] = round(
                    float(g['deferred_loss_cut']), 6)
            if g.get('premium_after_cut'):
                gain_entry['premium_after_cut'] = round(
                    float(g['premium_after_cut']), 6)
            if g.get('deferred_loss_recovered'):
                # The earlier superficial losses (s.53(1)(f) additions)
                # in this disposition's cost — a short's: off its
                # proceeds — so `raw_gain` + this is its P/L before any
                # denial (CA-RPT-16).
                gain_entry['deferred_loss_recovered'] = round(
                    float(g['deferred_loss_recovered']), 6)
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
                    trigger_tx = _book_row_of.get(trigger_id)
                adj_vtx = next(iter(_adj_out.of(g['tx_id'])), None)
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
            emit_line(f"warning: gain disallowance invariant broken — "
                f"taxable-raw={sum_taxable - sum_raw:.4f} vs gains-disallowed={sum_disallowed_on_gains:.4f}")
        if abs(sum_disallowed_on_gains - sum_disallowed_on_washes) > 0.01:
            emit_line(f"warning: wash-sale disallowance invariant broken — "
                f"gains-disallowed={sum_disallowed_on_gains:.4f} vs wash-sales={sum_disallowed_on_washes:.4f}")
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
        option_replacement_warnings += detect_opening_lot_matches(
            _ca_losses, all_txs, date_of=get_sort_date,
            canonical=alias_of, statute_label="ITA s.54")
        if getattr(self, 'emit_replacement_stderr', True):
            # run_gains turns this off and prints the warnings after its
            # year filter (audit S070-04).
            _emit_option_replacement_stderr(option_replacement_warnings,
                                            country='canada')

        # A short pool reports under its symbol, beside a long held in
        # another account.
        final_global_pools = _fold_short_futures_pools(final_global_pools)
        if final_year_end_pools is not None:
            final_year_end_pools = _fold_short_futures_pools(
                final_year_end_pools)
        # Conservation post-conditions (see the helpers' docstrings).
        _pool_qty: Dict[str, float] = {}
        _seen_pool_objs: set = set()
        for _s, _p in final_global_pools.items():
            if id(_p) in _seen_pool_objs:
                continue                # rename aliases share one object
            _seen_pool_objs.add(id(_p))
            # (a short beside a long nets here: this check counts units)
            _s = _s[:-len(_SHORT_SFX)] if _s.endswith(_SHORT_SFX) else _s
            _pool_qty[_s] = _pool_qty.get(_s, 0.0) + _p['qty']
        _verify_share_conservation(
            [t for t in current_tx_list
             if t.id in taxable_ids
             and (t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                               'SPLIT') or _is_lot_move(t)
                  or _is_listing_journal(t))],
            _pool_qty, 'canada',
            zero_ratio_skips=False)     # CA applies a ratio-0 SPLIT blindly
        _warn_stranded_basis(final_global_pools)

        def _recognised_premium(p) -> Dict[str, float]:
            if p['qty'] >= 0:
                return {}
            rp = round(sum(float(l['units']) * float(l['per_unit'])
                           for l in (p.get('grants') or [])
                           if l.get('rec')), 4)
            # Signed: a write whose commission exceeds its premium (a
            # net debit) books a LOSS at the write; dropping it made
            # harvest charge it again in the buy-back (backlog TJ-1012).
            return {'recognised_premium': rp} if abs(rp) > 1e-9 else {}

        def _account_premiums(p) -> Dict[str, Any]:
            # A written option open in several accounts under close
            # timing: what each account's contracts hold, which a close
            # takes (_short_lot_close, CA-OPT-06), for the per-account
            # views (taxjson_split_gains._split_inventory, backlog
            # TJ-1251). Not for a recognised lot, or lots that are not
            # the whole position.
            lots = p.get('grants') or []
            if (p['qty'] >= 0 or any(l.get('rec') for l in lots)
                    or abs(sum(l['units'] for l in lots) - abs(p['qty']))
                    > 1e-6):
                return {}
            out: Dict[str, Dict[str, float]] = {}
            for l in lots:
                e = out.setdefault(str(l.get('account') or ''),
                                   {'units': 0.0, 'premium': 0.0})
                e['units'] = round(e['units'] + l['units'], 10)
                e['premium'] = round(e['premium']
                                     + l['units'] * l['per_unit'], 6)
            return ({'account_premiums': out}
                    if len(out) > 1 and '' not in out else {})

        _ov_items = (_LO.summarize(_ov_plan, _ov_would, country='canada')
                     if _ov_plan else [])
        _LO.stamp_entries(processed_gains, _ov_items)
        return {
            **({'loss_overrides': _ov_items} if _ov_plan else {}),
            **({'grant_nil_closes': final_grant_nil_closes}
               if final_grant_nil_closes else {}),
            'transactions': processed_gains,
            'by_ticker': by_ticker,
            'wash_sales': final_wash_sales,
            'option_replacement_warnings': option_replacement_warnings,
            'inventory': [
                {
                    'symbol': s,
                    # A short beside a long: that account's own
                    # (_pool_rows). `short_pool` tells it from a US lot.
                    **({'account': _ac, 'short_pool': True} if _ac else {}),
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
                    **_account_premiums(p),
                    # Units whose cost these books do not have (a
                    # missing-history opening at an unknown cost; in one
                    # account's own view, units moved in from another of
                    # your accounts, CA-ACB-OWN-MOVE): total_cost leaves
                    # them out, and the holdings export says so (backlog
                    # TJ-0598).
                    **({'unknown_cost': True} if p.get('tainted') else {}),
                }
                for s, p, _ac in _pool_rows(final_global_pools,
                                            final_short_units)
                if not pool_is_flat(s, p['qty'], p['total_cost'])
            ],
            # The pools at the end of year_end (see final_year_end_pools),
            # the inventory's sign convention for shorts.
            **({'inventory_year_end': [
                {'symbol': s,
                 **({'account': _ac, 'short_pool': True} if _ac else {}),
                 'qty': p['qty'],
                 'total_cost': (float(p['total_cost']) if p['qty'] >= 0
                                else -float(p['total_cost'])),
                 'currency': p.get('currency', ''),
                 'deferred_wash': round(
                     float(p.get('deferred_wash', 0.0)), 4)}
                for s, p, _ac in _pool_rows(final_year_end_pools,
                                            final_ye_short_units)
                if not pool_is_flat(s, p['qty'], p['total_cost'])]}
               if final_year_end_pools is not None else {}),
            # The date the year end was read on, when not the walk's
            # settlement date (TJ-0343): edge-cases says which otherwise.
            **({'inventory_year_end_basis': 'trade'}
               if final_year_end_pools is not None
               and year_end_basis == 'trade' else {}),
            # Each pool after every row that changed it (backlog
            # TJ-0417): a day's pools are the rows before the first row
            # dated after it, as final_year_end_pools.
            **({'cost_timeline': _with_shorts(
                *[_r.finish(final_timeline[2], basis=cost_timeline,
                            stamp='max' if cost_timeline != 'trade'
                            else 'pool')
                  for _r in final_timeline[:2]])}
               if final_timeline is not None else {}),
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
    - Section 1256 year-end mark-to-market for futures and broad-based
      index options (SPX, NDX, futures); their P/L is booked FIFO here,
      and the estimate and carryover split the year's net 60/40
      (tax-logic US-FUT-03).
    """

    WASH_WINDOW_DAYS = 30

    def _plan_paid_compensation(self, transactions, sheltered, affiliated,
                                **kw) -> Dict[str, Any]:
        """Where the dividend compensation paid on shorts goes (§263(h);
        US-INC-04, backlogs TJ-1024, TJ-1059): a silent walk of copies of
        the rows finds which short shares each payment fell on and when
        they closed, from the walk's FIFO lots. {} with no such payment.
        A wash match can split a replacement short but moves no share
        between lots, so the plan holds for the walk that books it.
        `per_unit`: {(cover row id, short lot id): amount per share
        covered}; `payments`: {payment row id: {capitalized, on_short,
        open: [...]}}. A walk that raises plans nothing: the booking
        walk raises the same error, with its messages."""
        if not any((t.action == 'DIVIDEND_IN_LIEU'
                    or t.type == 'dividend_in_lieu')
                   and float((t.gross_amount or t.net_amount) or 0.0) < -0.005
                   for t in transactions):
            return {}
        plan: Dict[str, Any] = {'building': True, 'per_unit': {},
                                'payments': {}}
        # The engine's said-once notes are not said here: what this walk
        # discards the booking walk must still say (backlog TJ-1261).
        from taxjson.lib import markets
        from taxjson.lib.corporate_timeline import _SPLIT_DATE_NOTES
        said = ((markets._NOTED, set(markets._NOTED)),
                (_SPLIT_DATE_NOTES, set(_SPLIT_DATE_NOTES)))
        try:
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.compute_gains(
                    [copy.copy(t) for t in transactions],
                    [copy.copy(t) for t in sheltered or []],
                    [copy.copy(t) for t in affiliated or []],
                    _paid_comp=plan, **kw)
        except Exception:
            return {}
        finally:
            for store, before in said:
                store.clear()
                store.update(before)
        return {'per_unit': plan['per_unit'], 'payments': plan['payments']}

    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: List[TaxTransaction] = None, affiliated_transactions: List[TaxTransaction] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, per_account_basis: bool = False, loss_overrides: Sequence[Dict[str, Any]] = (), year_end: Optional[str] = None, year_end_basis: Optional[str] = None, cost_timeline: Optional[str] = None, lots_at: Optional[str] = None, lots_at_basis: Optional[str] = None, _paid_comp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # `cost_timeline` ("trade" or "settle", the date basis its rows
        # are dated on): also return each account's lots (quantity,
        # basis) after every row that changed them (`cost_timeline`,
        # lib/cost_timeline; backlog TJ-0417). `lots_at` (YYYY-MM-DD,
        # read on `lots_at_basis` as year_end on year_end_basis): also
        # return every lot held at the end of that day, each on its own
        # (`lots_at`): the year-end record keeps the FIFO order and each
        # lot's basis, which the pools' totals lose (backlog TJ-0554).
        _check_engine_allowed("usa")  # test-only guard (lib/country)
        # The computation's input contract (issues #14, #16; as Canada).
        require_computable_rows(transactions, sheltered_transactions,
                                affiliated_transactions)
        # Dividend compensation paid on a short (§263(h); US-INC-04) can
        # be a cover's cost: known only once the lots are walked, and
        # needed before that cover's §1091 test (backlog TJ-1059).
        if _paid_comp is None:
            _paid_comp = self._plan_paid_compensation(
                transactions, sheltered_transactions,
                affiliated_transactions, cross_asset=cross_asset,
                detect_wash_sales=detect_wash_sales,
                per_account_basis=per_account_basis,
                loss_overrides=loss_overrides)
        _comp_building = bool(_paid_comp.get('building'))
        _comp_per_unit: Dict[tuple, float] = _paid_comp.get('per_unit') or {}
        _comp_payments: Dict[str, Dict[str, Any]] = (
            _paid_comp.get('payments') or {})
        # (entry, cover row id, short lot id) of each cover, in the
        # planning walk (_plan_paid_pil).
        _short_cover_chunks: List[tuple] = []
        transactions, sheltered_transactions, affiliated_transactions = \
            _by_account_name(transactions, sheltered_transactions,
                             affiliated_transactions)
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
        # A merger's per-account EMPIRICAL ratios (US-CORP-14): with
        # per-account lots each row applies to its own account
        # (_per_account_rename_ratios); with symbol-wide lots the rows
        # fold into one event at the holdings-weighted ratio, as the
        # Canada engine's symbol-global pool does.
        if per_account_basis:
            _acct_ratios = _per_account_rename_ratios(
                list(transactions), list(sheltered_transactions or []),
                list(affiliated_transactions or []))
        else:
            _acct_ratios = {}
            transactions, sheltered_transactions, affiliated_transactions = \
                _fold_per_account_rename_ratios(
                    list(transactions), list(sheltered_transactions or []),
                    list(affiliated_transactions or []),
                    profile='us_main', date_of=lambda t: t.date)
        _seen_splits: set = set()
        transactions = _dedupe_corporate_splits(transactions, _seen_splits)
        sheltered_transactions = _dedupe_corporate_splits(
            sheltered_transactions or [], _seen_splits)
        affiliated_transactions = _dedupe_corporate_splits(
            affiliated_transactions or [], _seen_splits)

        def _split_covers(tx, seen_groups: set):
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
            r = round(float(tx.quantity or 0), 9)
            accts = {a for a, ra in ratio_of.items() if round(ra, 9) == r}
            first = key not in seen_groups
            seen_groups.add(key)
            return lambda a: a in accts or (first and a not in ratio_of)

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
                emit_line(text)
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
        # A context (sheltered/affiliated) book's stock dividends: (row,
        # class, entitlement date, ratio) — the main pass grows the
        # capacity of that account's replacement records by it (TJ-0492).
        _other_stkdivs: List[tuple] = []
        _pre_split_groups: set = set()      # _split_covers, the pre-pass
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

        # Each replacement list's index (TJ-0238): the scans below read
        # the records of a date window, of one row id, or holding one
        # lot, instead of walking (and date-parsing) the whole class per
        # sale. The lists are fixed from here on.
        _rep_ix: Dict[int, _RepIndex] = {}
        _rep_home: Dict[int, _RepIndex] = {}
        for _rl in (list(long_replacements.values())
                    + list(short_replacements.values())):
            _rep_ix[id(_rl)] = _ix_ = _RepIndex(_rl)
            for _r in _rl:
                _rep_home[id(_r)] = _ix_

        def _ix(rep_list) -> _RepIndex:
            ix_ = _rep_ix.get(id(rep_list))
            if ix_ is None or ix_.reps is not rep_list:
                return _RepIndex(rep_list)    # an empty .get() default
            return ix_

        def _set_ref(rep, ref_key, lot) -> None:
            """Point a replacement record at its lot (or None)."""
            rep[ref_key] = lot
            _rep_home[id(rep)].note_ref(rep, ref_key, lot)

        def _grow_rep_capacity(sym, on_date, lots, ratio) -> None:
            """A stock dividend grew `lots` by `ratio`: the purchases they
            stand for hold that many more shares, so their replacement
            records' capacity grows with them, as after a split, and a
            later loss matches share for share in the new units
            (TJ-0492; the dividend itself is never a purchase,
            US-STKDIV-01)."""
            ix_ = _ix(long_replacements.get(_rep_key(sym, on_date), []))
            for lot in lots:
                for r in ix_.holding('lot_ref', lot):
                    r['remaining_qty'] = r['remaining_qty'] * ratio

        def _allot_sold_shares(sold, ratio, ikey_, div_tx, units=None):
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
                        or (e.get('disallowed_amount') or 0.0) > epsilon
                        or e.get('wash_basis_added')):
                    manual.append((e, float(amt)))
                    amt = wd = Decimal(0)
                elif amt or wd:
                    e['cost'] = float(cost_d - amt)
                    e['gain'] = e['gain'] + float(amt)
                    e['raw_gain'] = e['raw_gain'] + float(amt)
                    if wd:
                        e['deferred_loss_recovered'] = float(
                            D(e['deferred_loss_recovered']) - wd)
                    moved += amt
                try:
                    eff = (datetime.strptime(e['date'], '%Y-%m-%d')
                           - timedelta(days=int(e.get('days_held') or 0))
                           ).strftime('%Y-%m-%d')
                except ValueError:
                    eff = e.get('acquired_date') or div_tx.date
                new_lots.append({
                    'qty': (units or {}).get(id(e), e['qty']) * (ratio - 1),
                    'cost_basis': amt,
                    'wash_deferred': wd,
                    'date': e.get('acquired_date') or div_tx.date,
                    'effective_acq_date': eff,
                    'id': div_tx.id,
                    **({'tainted': True} if e.get('tainted') else {}),
                })
            if new_lots:
                _insert_lots(ikey_, new_lots)
            return moved, manual

        def _apply_other_stkdivs(upto) -> None:
            """The context books' stock dividends sorted before `upto`
            (a main-pass row): each grows the capacity of the purchases
            that account made before its entitlement date (TJ-0492)."""
            p = _ev_pos.get(id(upto))
            if p is None:
                return
            while (_other_stkdivs
                   and _ev_pos.get(id(_other_stkdivs[0][0]), -1) < p):
                ev, cls, ent_d, ratio = _other_stkdivs.pop(0)
                for r in long_replacements.get(cls, []):
                    if (r['tx'].account == ev.account
                            and (r['is_sheltered'] or r['is_affiliated'])
                            and r['date'] <= ev.date
                            and (ent_d is None or r['date'] < ent_d)):
                        r['remaining_qty'] = r['remaining_qty'] * ratio

        def _split_rep_refs(sym, on_date, lot, part, ref_key) -> None:
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
            if whole <= epsilon:
                return
            ix_ = _ix((long_replacements if ref_key == 'lot_ref'
                       else short_replacements).get(_rep_key(sym, on_date),
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
                _rep_home[id(child)] = ix_
                _set_ref(child, ref_key, part)

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
                loss_ord = _iso_ordinal(loss_date_str)
            except ValueError:
                return []
            out = []
            # The records dated within the window, in list order (a
            # record whose date does not parse is never one).
            for rep in _ix(rep_list).within(loss_ord, self.WASH_WINDOW_DAYS,
                                            self.WASH_WINDOW_DAYS):
                if rep['remaining_qty'] <= epsilon:
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
                    or lot.get('wash_basis_spent')
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
                loss_ord = _iso_ordinal(loss_tx.date)
            except ValueError:
                return []
            out = []
            # The records bought on the loss date or the 30 days before.
            for rep in _ix(rep_list).within(loss_ord,
                                            self.WASH_WINDOW_DAYS, 0):
                if (rep['is_sheltered'] or rep['is_affiliated']
                        or rep['tx'].account == loss_tx.account
                        or not rep.get('sold')):
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
                      'commission', 'fee', 'deferred_loss_recovered'):
                if k not in entry:
                    continue
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

        # A §1256 contract (a futures contract, an option on one, a
        # broad-based index option) is marked to market, outside §1091.
        # Its loss is never denied; a re-purchase in the window is
        # flagged for a manual check (tax-logic US-WASH-18; audit
        # A2-0053 — a re-bought F:CLG7 had its whole 10,000 loss
        # disallowed with no flag; TJ-0506 — an SPX option's too).
        # Canada's s.54 covers any property and keeps denying.
        _fut_flags: Dict[str, Dict[str, Any]] = {}
        _outside_seen: Dict[str, bool] = {}

        def _outside_1091(sym: str) -> bool:
            hit = _outside_seen.get(sym)
            if hit is None:
                hit = _outside_seen[sym] = outside_1091(sym)
            return hit

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
        # The user's filing positions against §1091 (.tt ALLOWLOSS,
        # lib/loss_overrides; US-WASH-25): the named sale's loss is
        # matched as usual — so the replacement shares it would take are
        # still taken and every other loss's verdict is unchanged — but
        # nothing is disallowed, no basis is added and no holding period
        # is tacked; what §1091 would have disallowed is recorded.
        from taxjson.lib import loss_overrides as _LO
        _ov_plan = _LO.plan(loss_overrides, taxable_sorted)
        _ov_would: Dict[str, Dict[str, Any]] = {}

        def _ov_take(tx_, rep_tx, is_sh, is_aff, q, amt):
            rec = _ov_would.setdefault(tx_.id, _LO.new_record())
            rec['disallowed'] += amt
            if is_sh or is_aff:
                rec['permanent'] += amt
            _LO.add_replacement(rec, rep_tx, q, amt,
                                'sheltered' if is_sh
                                else 'affiliated' if is_aff else 'taxable')

        def _ov_loss(tx_, q, amt):
            rec = _ov_would.setdefault(tx_.id, _LO.new_record())
            rec['qty'] += q
            rec['loss'] += amt
        pending_option_adjustments = _AssignPremiumLedger(
            taxable_sorted,
            lambda _t: (not is_option_symbol(_t.symbol)
                        and not exercise_target(_t)
                        and _t.action in ('BUYSELL', 'ASSIGN')),
            _assign_pairs)

        def _take_option_adj(tx) -> float:
            # The deferral part of what it returns:
            # pending_option_adjustments.last_deferred (backlog TJ-0061).
            if (is_option_symbol(tx.symbol) or exercise_target(tx)
                    or tx.action not in ('BUYSELL', 'ASSIGN')):
                pending_option_adjustments.last_deferred = 0.0
                pending_option_adjustments.last_tainted = False
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
            reps = _ix((long_replacements if ref_key == 'lot_ref'
                        else short_replacements).get(_rep_key(sym, on_date),
                                                     []))
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
                    for r in reps.holding(ref_key, lot):
                        _set_ref(r, ref_key, keep)
                    drawn = keep
                else:
                    drawn = lot
                for r in reps.holding(ref_key, drawn):
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

        # A native book's journals between two listings (TJ-0361):
        # one leg books the pair (_carry_lots).
        _jlegs = _journal_legs(taxable_sorted)
        _jdone: set = set()
        for _jo, _ji in _jlegs.values():
            for _jt in (_jo, _ji):
                if _jt.currency:
                    symbol_currency.setdefault(_jt.symbol, _jt.currency)

        def _carry_lots(inv, src, dst, q, f, field, sym, on_date):
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
            while left > epsilon and lots:
                lot = lots[0]
                if lot['qty'] <= left + epsilon:
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
                _split_rep_refs(sym, on_date, lot, part,
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
                                          _lot_time.get(l.get('id'), '')))
            return sum(l['qty'] for l in moved)

        def _book_journal(out_leg, in_leg):
            """One listing journal: a long's lots move from the out-leg's
            listing to the in-leg's; when the out-leg's holds no long and
            the in-leg's holds a short, the short's lots move the other
            way (audit 2026-10-24 B13). Units beyond what the source
            holds have no purchase in this book: the receiver holds them
            at an unknown cost and the source is short by them, both
            tainted. Returns the trace text."""
            q = abs(out_leg.quantity or 0.0)
            ka = _ikey(out_leg.account, out_leg.symbol)
            kb = _ikey(in_leg.account, in_leg.symbol)
            a_long = sum(l['qty'] for l in inventory_long.get(ka, []))
            b_short = sum(l['qty'] for l in inventory_short.get(kb, []))
            short = a_long <= epsilon and b_short > epsilon
            if short:
                src_leg, dst_leg, inv, src, dst = (in_leg, out_leg,
                                                   inventory_short, kb, ka)
            else:
                src_leg, dst_leg, inv, src, dst = (out_leg, in_leg,
                                                   inventory_long, ka, kb)
            f = _journal_factor(symbol_currency.get(src_leg.symbol, ''),
                                src_leg, dst_leg)
            got = _carry_lots(inv, src, dst, q, f,
                              'proceeds' if short else 'cost_basis',
                              src_leg.symbol, out_leg.date)
            rest = q - got
            if rest > max(epsilon, _dust_noise(q)):
                for k, side in ((dst, inventory_short if short
                                 else inventory_long),
                                (src, inventory_long if short
                                 else inventory_short)):
                    side.setdefault(k, []).append({
                        'qty': rest,
                        ('proceeds' if side is inventory_short
                         else 'cost_basis'): Decimal(0),
                        'wash_deferred': Decimal(0),
                        'date': out_leg.date,
                        ('effective_open_date' if side is inventory_short
                         else 'effective_acq_date'): out_leg.date,
                        'id': out_leg.id, 'tainted': True})
            return (f"LISTING JOURNAL {src_leg.symbol} -> "
                    f"{dst_leg.symbol} {got:10.4f}"
                    f"{' (a short)' if short else ''} | basis "
                    + ("unknown here (no rate)" if f is None else
                       f"carried x{float(f):.6g}")
                    + (f" | {rest:g} with no purchase in this book"
                       if rest > max(epsilon, _dust_noise(q)) else ""))

        def _move_long_lots(src, dst, sym, q, on_date):
            """Hand `q` units of `src`'s FIFO lots (the lot objects
            themselves: basis, purchase dates, wash-sale deferrals and
            replacement links travel with them; a lot split here gives
            its moved part its share of the links, TJ-0490) to `dst`.
            Returns the units moved."""
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
                _split_rep_refs(sym, on_date, lot, part, 'lot_ref')
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
            reps = _ix(long_replacements.get(_rep_key(sym, on_date), []))
            left = q
            while left > epsilon and lots:
                lot = lots[0]
                if lot['qty'] <= left + epsilon:
                    lots.pop(0)
                    out.append(dict(lot))
                    left -= lot['qty']
                    for r in reps.holding('lot_ref', lot):
                        _set_ref(r, 'lot_ref', None)
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
                for r in reps.holding('lot_ref', lot):
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
        _ye_lots: Optional[Tuple[Dict[Any, List[Dict[str, Any]]],
                                 Dict[Any, List[Dict[str, Any]]]]] = None

        def _row_after(day: str, basis: Optional[str]
                       ) -> Optional[TaxTransaction]:
            """The first row after the end of `day`: the lots are taken
            before it (None: no row after the day)."""
            _last = -1
            for _i, _t in enumerate(taxable_sorted):
                if str(get_sort_date(_t) or '')[:10] <= day:
                    _last = _i
            # A tax_date = "settle" project (`year_end_basis`): its year
            # ends before the first row that SETTLES after year_end — a
            # sale traded Dec 31 that settles in January is a next-year
            # disposition, so the lots it sells (and the disallowed
            # losses in their basis) are still held at its year end
            # (backlog TJ-0343).
            if basis == 'settle':
                for _i, _t in enumerate(taxable_sorted[:_last + 1]):
                    if str(_t.date_settle or _t.date or '')[:10] > day:
                        _last = _i - 1
                        break
            if _last + 1 < len(taxable_sorted):
                return taxable_sorted[_last + 1]
            return None

        _ye_next: Optional[TaxTransaction] = (
            _row_after(year_end, year_end_basis) if year_end else None)
        # The lots at the end of `lots_at` (backlog TJ-0554).
        _la_lots: Optional[Tuple[Dict[Any, List[Dict[str, Any]]],
                                 Dict[Any, List[Dict[str, Any]]]]] = None
        _la_next: Optional[TaxTransaction] = (
            _row_after(lots_at, lots_at_basis) if lots_at else None)

        def _lots_copy():
            """The long and short lots now (each lot's own fields: units,
            basis or proceeds, wash_deferred, dates)."""
            return ({k: [dict(l) for l in v]
                     for k, v in inventory_long.items()},
                    {k: [dict(l) for l in v]
                     for k, v in inventory_short.items()})

        # The cost timeline (lib/cost_timeline; backlog TJ-0417): each
        # row's own lots are read after it (at the next row), and every
        # account's lots held at the end of each day — a basis addition
        # can land in another account's lot.
        _tl = None
        _tl_dates: List[Tuple[str, str]] = []
        _tl_prev: List[Any] = []

        def _us_label(acct, sym):
            return (acct or '', sym) if per_account_basis else ('', sym)

        def _us_state(lab):
            k = _ikey(lab[0], lab[1])
            lo = inventory_long.get(k) or []
            sh = inventory_short.get(k) or []
            if not lo and not sh:
                return None
            q = sum(l['qty'] for l in lo) - sum(l['qty'] for l in sh)
            cost = (float(sum(l['cost_basis'] for l in lo))
                    - float(sum(l['proceeds'] for l in sh)))
            dw = float(sum(_lot_wash_held(l) for l in lo)
                       + sum(l.get('wash_deferred', Decimal(0)) for l in sh))
            dates = [l['date'] for l in lo + sh]
            return (q, cost, dw, any(l.get('tainted') for l in lo + sh),
                    symbol_currency.get(lab[1], ''), min(dates),
                    max(dates))

        def _us_held():
            for inv in (inventory_long, inventory_short):
                for k, lots in inv.items():
                    if lots:
                        yield ((k[0], k[1]) if per_account_basis
                               else ('', k))

        if cost_timeline:
            from taxjson.lib import cost_timeline as _CT
            _tl = _CT.Recorder(_us_state)

        _main_split_groups: set = set()     # _split_covers, the main pass
        _ev_pos = {id(_e): _i for _i, _e in enumerate(all_events)}
        for tx in taxable_sorted:
            if _tl is not None:
                _tl_d = str(get_sort_date(tx) or '')[:10]
                if _tl_prev:
                    _tl.touch(len(_tl_dates) - 1,
                              [_us_label(_tl_prev[0].account,
                                         _tl_prev[0].symbol)])
                    if _tl_d != _tl_dates[-1][0]:
                        _tl.sweep(len(_tl_dates) - 1, _us_held())
                _tl_dates.append(
                    (_tl_d, _tl_d if cost_timeline != 'settle'
                     else str(tx.date_settle or _tl_d)[:10]))
                _tl_prev[:] = [tx]
            if tx is _ye_next and _ye_lots is None:
                _ye_lots = _lots_copy()
            if tx is _la_next and _la_lots is None:
                _la_lots = _lots_copy()
            if _other_stkdivs:
                _apply_other_stkdivs(tx)
            symbol = tx.symbol
            ikey = _ikey(tx.account, symbol)
            inventory_long.setdefault(ikey, [])
            inventory_short.setdefault(ikey, [])
            if trace and symbol not in symbol_traces:
                symbol_traces[symbol] = [f"# --- FIFO CALCULATION TRACE: {symbol} ---"]

            if _is_listing_journal(tx):
                _jl = _jlegs.get(tx.journal_pair)
                if _jl and tx.journal_pair not in _jdone:
                    _jdone.add(tx.journal_pair)
                    _jtxt = _book_journal(*_jl)
                    if trace:
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
                        # `moved_from`: a sale of them says their basis
                        # is in the blended figures (backlog TJ-0279).
                        _insert_lots(ikey, [{
                            'qty': tx.quantity,
                            'cost_basis': Decimal(0),
                            'wash_deferred': Decimal(0),
                            'date': tx.date,
                            'effective_acq_date': tx.date,
                            'id': tx.id, 'tainted': True,
                            'moved_from': _peer}])
                    _note(tx.date,
                          f"note: {symbol}: {abs(tx.quantity):g} moved "
                          f"{'to' if tx.quantity < 0 else 'from'} {_peer} "
                          f"on {tx.date} (your own account): this book has "
                          f"only {tx.account}, so "
                          + ("the lots leave it with no sale"
                             if tx.quantity < 0 else
                             "their basis is unknown here")
                          + " — the blended pass of `tjs run` carries "
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
                # A multi-ratio merger's row reaches its own account's
                # lots only (US-CORP-14; _split_covers): each account
                # lands on exactly the shares its broker delivered.
                _covers = _split_covers(tx, _main_split_groups)

                def _split_keys(inv, _sym=symbol, _c=_covers):
                    return [k for k in _inv_keys_for(inv, _sym)
                            if _c is None or not per_account_basis
                            or _c(k[0])]
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
                    for inv in (inventory_long, inventory_short):
                        for _k in _split_keys(inv):
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
            if tx.action == 'ADJUST' and (
                    tx.id in _spin_done
                    or (tx.type == SPINOFF_355_TYPE
                        and abs(tx.net_amount or 0.0) < 0.005)):
                # Booked with its spin-off (US-CORP-07); a $0 one only
                # names the parent (TJ-0723) and moves nothing.
                continue
            if tx.action == 'ADJUST':
                lots = inventory_long.get(ikey, [])
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
                        _note(tx.date,
                              f"warning: ATTENTION: unapplied basis "
                              f"adjustment: {symbol} ADJUST of "
                              f"{float(tx.net_amount):+.2f} on {tx.date} "
                              f"(record date {_rd}) found no open long lot "
                              f"settled by the record date ({len(lots)} "
                              f"open, all settling after it) — the row was "
                              f"NOT applied; check the record date.")
                        continue
                    lots = _settled
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
                # §301(c)(3) excess by (acquired, holding-period start,
                # unknown cost): every lot driven below zero is floored
                # at zero, however small its part — a per-lot half-cent
                # threshold left hundreds of execution lots' sub-cent
                # excesses as negative basis and their sum out of the
                # gain (TJ-0956).
                _xs: Dict[Tuple[str, str, bool], Decimal] = {}
                # ...and by lot, for a row of two lots of one date: the
                # views' premium is the lot's own (backlog TJ-1218).
                _xs_lots: Dict[Tuple[str, str, bool], Dict[str, float]] = {}
                _date_ids: Dict[str, set] = {}
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
                            _xl.get(str(lot.get('id') or ''), 0.0)
                            + float(excess))
                # One deemed row per acquisition; the ones under half a
                # cent are pooled by term (dated their earliest lot).
                _rows = []
                _small: Dict[Tuple[bool, bool], list] = {}
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
                    _x = float(_amt)
                    _by_lot = _xs_lots.get((_lot_date, _acq, _tnt))
                    if (len(_date_ids.get(str(_lot_date), ())) < 2
                            or '' in _date_ids.get(str(_lot_date), ())
                            or not _by_lot
                            or abs(sum(_by_lot.values()) - _x) > 0.005):
                        _by_lot = None
                    realized_gains.append({
                        **({'deemed_lots': _by_lot} if _by_lot else {}),
                        'date': tx.date,
                        'date_settle': tx.date_settle or tx.date,
                        'symbol': symbol, 'qty': 0.0,
                        'cost': 0.0, 'proceeds': _x,
                        'gain': _x, 'raw_gain': _x,
                        'disallowed_amount': 0.0,
                        'permanently_disallowed': 0.0,
                        'replacement_lot_ids': [],
                        'days_held': max(0, _held),
                        'acquired_date': _lot_date,
                        'account': tx.account,
                        'currency': tx.currency,
                        'commission': 0.0, 'fee': 0.0,
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
                if excess_total > D('0.005'):
                    emit_line(f"NOTE: {symbol}: nondividend distribution on "
                        f"{tx.date} exceeded the basis by "
                        f"{float(excess_total):.2f} — booked as capital "
                        f"gain in that year (§301(c)(3)); the basis is "
                        f"zero.")
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
                _cls_now = split_timeline.class_at(symbol, tx.date)
                _sold_ent = ([e for e in realized_gains
                              if e.get('direction') == 'LONG'
                              and e.get('qty')
                              and not (e.get('deemed')
                                       or e.get('reorg_356'))
                              and (e.get('symbol') == symbol
                                   or split_timeline.class_at(
                                       e.get('symbol') or '',
                                       e.get('date') or None)
                                   == _cls_now)
                              and _ikey(e.get('account'), symbol) == ikey
                              and _ent_date <= (e.get('date') or '')
                              <= tx.date
                              and (e.get('acquired_date') or '')
                              < _ent_date]
                             if _ent_date else [])
                _sold_units = {
                    id(e): e['qty'] * _rep_units_factor(
                        e.get('symbol') or symbol, e.get('date') or '',
                        tx.date)
                    for e in _sold_ent}
                _sold_qty = sum(_sold_units.values())
                if (_ent_qty + _sold_qty > epsilon
                        and not inventory_short.get(ikey)):
                    _ratio = ((_ent_qty + _sold_qty + tx.quantity)
                              / (_ent_qty + _sold_qty))
                    # Named before the spread: the lots' own shares.
                    _recent = [l for l in _lots
                               if (l.get('date') or '') <= tx.date
                               and (_day_gap(l.get('date') or '',
                                             tx.date) or 999) <= 90]
                    _recent_qty = sum(l['qty'] for l in _recent)
                    for _lot in _ent:
                        _lot['qty'] = _lot['qty'] * _ratio
                    _grow_rep_capacity(symbol, tx.date, _ent, _ratio)
                    _later = [l for l in _lots if l not in _ent]
                    _moved, _manual = _allot_sold_shares(
                        _sold_ent, _ratio, ikey, tx, _sold_units)
                    _note(tx.date,
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
                             f"{float(_moved):,.2f}, which the "
                             f"{_sold_qty * (_ratio - 1):g} new share(s) "
                             f"paid on them carry"
                             if _sold_ent else "")
                          + (f"; the {sum(l['qty'] for l in _later):g} "
                             f"bought on or after {_ent_date} keep their "
                             f"own basis" if _later else "")
                          + f"; if it was taxable (§305(b), e.g. a cash "
                          f"option), enter it by hand.")
                    for _e, _amt in _manual:
                        _note(tx.date,
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
                            _note(tx.date,
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
                    if trace:
                        symbol_traces[symbol].append(
                            f"# {tx.date} STOCK DIVIDEND +{tx.quantity:g} "
                            f"sh | spread over {len(_ent)} lot(s), "
                            f"{_ent_qty:.4f} sh entitled"
                            + (f" (before {_ent_date})" if _ent_date
                               else ""))
                    continue
                if _held > epsilon and not inventory_short.get(ikey):
                    # Shares held on the pay date, none bought before
                    # the entitlement date: the dividend belongs to
                    # shares the books do not show (history missing, or
                    # sold between the record and pay dates).
                    _note(tx.date,
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
                        _note(tx.date,
                              f"note: {_par}: the §355 spin-off on "
                              f"{tx.date} (entitlement {_ent_d}): the "
                              f"{_lq:g} share(s) "
                              f"bought on or after {_ent_d} are not entitled "
                              f"and keep their own basis.")
                    elif _how == 'due_bill':
                        _note(tx.date,
                              f"note: {_par}: the §355 spin-off on "
                              f"{tx.date} was paid on {_n:g} shares, all "
                              f"those held that day: the {_lq:g} bought on "
                              f"or after its record date {_ent_d} came with "
                              f"a due bill and share the basis.")
                    else:
                        _note(tx.date,
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
                    _note(tx.date,
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
                        and not inventory_short.get(_pkey)):
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
                        _note(tx.date,
                              f"warning: ATTENTION: {_par}: the §355 "
                              f"spin-off on {tx.date} allocates "
                              f"{float(_alloc):,.2f} but the parent's "
                              f"basis held is {float(_pbasis):,.2f} — "
                              f"capped at the basis (a tax-free spin-off "
                              f"books no gain); check the allocated "
                              f"amount (Form 8937 %).")
                        _new_total = Decimal(0)
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
            tx_net = (abs(tx.net_amount)
                      if tx.quantity > 0 and not receives_on_buy(tx)
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
                                tainted=False, raw_acq_date='', deferred=Decimal(0)):
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
                if deferred and float(deferred) > 1e-9:
                    # The earlier wash-sale losses (§1091(d) basis adds)
                    # in this chunk's basis — a short's: off its opening
                    # proceeds — the lot's wash_deferred, so `raw_gain`
                    # + this is its P/L before any denial (US-RPT-12;
                    # audit 2026-10-25 planning-us#1).
                    entry['deferred_loss_recovered'] = float(deferred)
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
                # An exercised replacement option's wash deferral in
                # that premium: these shares' lots tally it (TJ-0061).
                option_dw = pending_option_adjustments.last_deferred
                # An unknown-cost option's premium: the shares' basis
                # (a cover's cost) is unknown too (TJ-0947).
                option_tainted = pending_option_adjustments.last_tainted
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
                        chunk_wd_d = short_lot.get('wash_deferred',
                                                   Decimal(0))
                        inventory_short[ikey].pop(0)
                        # A short_replacement record that pointed at this
                        # lot is consumed: no lot reference (a later match
                        # must not mutate the detached dict) and no
                        # capacity, so find_replacements_in_window skips it
                        # (tax-logic US-WASH-21, re-audit A2-0817).
                        for r in _ix(short_replacements.get(
                                _rep_key(symbol, tx.date), [])).holding(
                                    'short_lot_ref', short_lot):
                            _set_ref(r, 'short_lot_ref', None)
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
                        chunk_wd_d = ((D(chunk_qty)
                                       / D(short_lot['qty'] + chunk_qty))
                                      * _swd if _swd else Decimal(0))
                        if _swd:
                            short_lot['wash_deferred'] = _swd - chunk_wd_d
                        for r in _ix(short_replacements.get(
                                _rep_key(symbol, tx.date), [])).holding(
                                    'short_lot_ref', short_lot):
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
                    # Dividend compensation paid on these shares, closed
                    # within 45 days: part of the cost of covering them,
                    # so in the gain or loss the §1091 test below sees
                    # (§263(h)(1); US-INC-04, backlog TJ-1059).
                    _cap_263h = (0.0 if is_option_assign else
                                 chunk_qty * _comp_per_unit.get(
                                     (tx.id, short_lot.get('id')), 0.0))
                    chunk_close_cost += _cap_263h
                    # Gain on closing a short: opening_proceeds − closing_cost.
                    raw_gain = chunk_open_proceeds - chunk_close_cost

                    if is_option_assign:
                        # Premium rolls into the underlying — accumulate the
                        # would-be gain (with sign flipped to match the
                        # CanadaTaxRules / Pub 550 convention: BUY of stock
                        # subtracts pending_adj from cost; SELL of stock adds
                        # pending_adj to proceeds).
                        pending_option_adjustments.stage(
                            tx, underlying_for_assign, -raw_gain,
                            float(chunk_wd_d),
                            bool(short_lot.get('tainted')))
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
                        _ov_on = tx.id in _ov_plan.row_of
                        if _ov_on:
                            _ov_loss(tx, chunk_qty, chunk_loss)
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
                            if _ov_on:
                                _ov_take(tx, rep['tx'], rep['is_sheltered'],
                                         rep.get('is_affiliated'),
                                         match_qty, match_disallowed)
                                continue

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
                                        _set_ref(rep, 'short_lot_ref',
                                                 _srem)
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
                        tainted=(short_lot.get('tainted', False)
                                 or option_tainted),
                        raw_acq_date=short_lot['date'],
                        deferred=chunk_wd_d + D(option_dw * (chunk_qty
                                                             / tx_qty_abs)),
                    )
                    # A short sale closed at a LOSS is realized when the
                    # shares are delivered to close it (Reg. §1.1233-1
                    # (a)(1); Rev. Rul. 2002-44, Situation 1): a cover
                    # traded in December and settled in January is a loss
                    # of January's year. One closed at a gain stays on
                    # its trade date (§1259, Situation 2). The trade date
                    # is kept in `trade_date` (US-DATE-01; TJ-0953). A
                    # written option delivers nothing to close: its loss
                    # stays on the closing trade (§1234(b)(1), TJ-1160).
                    _loss_day = tx.date
                    _dlv = tx.date_settle or tx.date
                    if (raw_gain < -epsilon and not is_option_assign
                            and not is_option_symbol(symbol)
                            and _dlv[:4] > tx.date[:4]):
                        entry['trade_date'] = tx.date
                        entry['date'] = _dlv
                        _loss_day = _dlv
                    if _cap_263h:
                        entry['capitalized_263h'] = round(_cap_263h, 4)
                    realized_gains.append(entry)
                    if _comp_building:
                        _short_cover_chunks.append(
                            (entry, tx.id, short_lot.get('id')))

                    total_disallowed = disallowed_amt + permanently_disallowed_amt
                    if total_disallowed > epsilon:
                        wash_sale_records.append({
                            'loss_tx_id': tx.id,
                            'symbol': symbol,
                            'date': _loss_day,
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
                        (r for r in _ix(long_replacements.get(
                            _rep_key(symbol, tx.date), [])).of_tx(tx.id)
                         if not r['is_sheltered']),
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
                    _odw = D(option_dw * leftover_share)
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
                    if is_opening_row(tx):
                        # An opening lot (US-OPEN-01): its basis is the
                        # report's cost and its holding period runs from
                        # its own acquisition date (§1223), not the
                        # snapshot day; FIFO places it by that date.
                        if not tx.lot_date:
                            raise ValueError(
                                f"{symbol}: the opening balance dated "
                                f"{tx.date} in {tx.account} has no lot "
                                f"date — a US lot's holding period "
                                f"needs its purchase date (one OPENING "
                                f"line per lot, US-OPEN-01).")
                        _insert_lots(ikey, [{
                            'qty': qty_remaining,
                            'cost_basis': base_cost_d,
                            'wash_deferred': Decimal(0),
                            'date': tx.lot_date,
                            'effective_acq_date': tx.lot_date,
                            'id': tx.id,
                            **({'tainted': True} if option_tainted
                               else {}),
                        }])
                        if trace:
                            symbol_traces[symbol].append(
                                f"# {tx.date} OPENING   {qty_remaining:10.4f} | "
                                f"Lot_Cost: {float(base_cost_d):10.4f} | "
                                f"Acquired: {tx.lot_date}")
                        continue
                    _new_lots = []
                    _alloc = Decimal(0)
                    for _bq, _pb, _pe in _blocks:
                        _c = base_cost_d * D(_bq) / D(qty_remaining)
                        _alloc += _c
                        _new_lots.append({
                            'qty': _bq,
                            'cost_basis': _c + _pb,
                            'wash_deferred': (_pb + _odw * D(_bq)
                                              / D(qty_remaining)),
                            'date': tx.date,
                            # When the lot is the holder of record's
                            # (a 'dist' ADJUST, US-DIST-01).
                            'date_settle': tx.date_settle or tx.date,
                            'effective_acq_date': _pe,
                            'id': tx.id,
                            **({'tainted': True} if option_tainted
                               else {}),
                        })
                    if _left > epsilon:
                        _new_lots.append({
                            'qty': _left,
                            'cost_basis': base_cost_d - _alloc,
                            'wash_deferred': (_odw * D(_left)
                                              / D(qty_remaining)),
                            'date': tx.date,
                            'date_settle': tx.date_settle or tx.date,
                            'effective_acq_date': tx.date,
                            'id': tx.id,
                            **({'tainted': True} if option_tainted
                               else {}),
                        })
                    else:
                        # The last block absorbs the division remainder.
                        _new_lots[-1]['cost_basis'] += base_cost_d - _alloc
                    inventory_long[ikey].extend(_new_lots)
                    lot = _new_lots[0]
                    if rep_record is not None:
                        # The unmatched remainder (or, fully matched,
                        # the last block) answers any later match.
                        _set_ref(rep_record, 'lot_ref', _new_lots[-1])
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
            option_dw_sell = pending_option_adjustments.last_deferred
            option_tainted_sell = pending_option_adjustments.last_tainted
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
                    chunk_wd_d = lot.get('wash_deferred', Decimal(0))
                    inventory_long[ikey].pop(0)
                    # As on the short side: a long_replacement rep that
                    # pointed at this sold lot loses its lot reference and
                    # its capacity, so it never washes a later loss
                    # (US-WASH-21, re-audit A2-0817).
                    for r in _ix(long_replacements.get(
                            _rep_key(symbol, tx.date), [])).holding(
                                'lot_ref', lot):
                        _set_ref(r, 'lot_ref', None)
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
                    chunk_wd_d = ((D(chunk_qty) / D(lot['qty'])) * _wd
                                  if _wd else Decimal(0))
                    if _wd:
                        lot['wash_deferred'] = _wd - chunk_wd_d
                    lot['qty'] -= chunk_qty
                    lot['cost_basis'] -= chunk_cost_d
                    for r in _ix(long_replacements.get(
                            _rep_key(symbol, tx.date), [])).holding(
                                'lot_ref', lot):
                        # Same unit conversion as the short side
                        # above: consume the rep's capacity in ITS
                        # trade-date units, not today's.
                        _cuf = _rep_units_factor(
                            symbol, r['date'], tx.date)
                        r['remaining_qty'] = max(
                            0.0, r['remaining_qty']
                            - chunk_qty / (_cuf or 1.0))
                # A return of capital took what the basis no longer
                # holds of the §1091(d) add (TJ-1017): reported apart, as
                # the basis was also short by any premium it held
                # (TJ-1217).
                chunk_wd_cut = max(Decimal(0), chunk_wd_d - max(
                    Decimal(0), chunk_cost_d))
                if chunk_wd_cut > D('0.000001'):
                    chunk_wd_d -= chunk_wd_cut
                else:
                    chunk_wd_cut = Decimal(0)
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
                        tx, underlying_for_assign, -raw_gain,
                        float(chunk_wd_d), bool(lot.get('tainted')))
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
                            _rank = _ix(_reps_here).pos

                            def _ck(c):
                                r = c[0] if isinstance(c, tuple) else c
                                return (r['date'], r['tx'].time or '',
                                        2 if r['is_affiliated'] else 1
                                        if r['is_sheltered'] else 0,
                                        _rank(r),
                                        1 if isinstance(c, tuple) else 0)
                            candidates = sorted(list(candidates) + _sold,
                                                key=_ck)
                    _ov_on = tx.id in _ov_plan.row_of
                    if _ov_on:
                        _ov_loss(tx, chunk_qty, chunk_loss)
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
                            if _ov_on:
                                _ov_take(tx, _srep['tx'], False, False,
                                         match_qty, float(match_disallowed_d))
                                continue
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
                        if _ov_on:
                            _ov_take(tx, rep['tx'], rep['is_sheltered'],
                                     rep.get('is_affiliated'), match_qty,
                                     match_disallowed)
                            continue

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
                                _set_ref(rep, 'lot_ref', _rem)
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
                    tainted=(lot.get('tainted', False)
                             or option_tainted_sell),
                    deferred=chunk_wd_d + D(option_dw_sell * (chunk_qty
                                                              / tx_qty_abs)),
                )
                if lot.get('tainted') and lot.get('moved_from') is not None:
                    # A lot an own move brought, its basis in the
                    # blended pass (backlog TJ-0279).
                    entry['own_move_from'] = [lot['moved_from']]
                if chunk_wd_cut:
                    entry['deferred_loss_cut'] = float(chunk_wd_cut)
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
                    (r for r in _ix(short_replacements.get(
                        _rep_key(symbol, tx.date), [])).of_tx(tx.id)
                     if not r['is_sheltered']),
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
                                      else Decimal(0))
                    + D(option_dw_sell * leftover_share),
                    **({'tainted': True} if option_tainted_sell else {}),
                }
                inventory_short[ikey].append(short_lot)
                if rep_record is not None:
                    _set_ref(rep_record, 'short_lot_ref', short_lot)
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
                        _set_ref(rep_record, 'short_lot_ref', (
                            _srem if rep_record['remaining_qty'] > epsilon
                            else short_lot))
                    rep_record['pending_proceeds_reduction'] = Decimal(0)
                if trace:
                    symbol_traces[symbol].append(
                        f"# {tx.date} SELL-OPEN {qty_remaining:10.4f} @ {tx.price:10.4f} | "
                        f"Short_Proc: {float(short_lot['proceeds']):10.4f} | "
                        f"Effective_Open: {short_lot['effective_open_date']}"
                    )

        if year_end and _ye_lots is None:
            _ye_lots = _lots_copy()     # no row after the year end
        if lots_at and _la_lots is None:
            _la_lots = _lots_copy()     # no row after the day
        if _tl is not None and _tl_prev:
            _tl.touch(len(_tl_dates) - 1,
                      [_us_label(_tl_prev[0].account, _tl_prev[0].symbol)])
            _tl.sweep(len(_tl_dates) - 1, _us_held())

        def _plan_paid_pil(ptx, paid: float) -> None:
            """Dividend compensation `paid` on a short (a negative
            payment in lieu, `ptx`), in the planning walk: it falls on
            every share of that account's short open on the pay date,
            those covered later and those still open at the end of the
            books (backlog TJ-1024), by quantity in the pay date's units.
            The part on shares of a short sale closed on or before the
            45th day after it was made is no deduction but the cover's
            cost (§263(h)(1); US-INC-04, backlog TJ-0728): per_unit, for
            the booking walk. A still-open short whose 45th day is after
            the last row leaves its part unsettled (`open`; backlog
            TJ-1065). The rest stays paid compensation."""
            pd_ = ptx.date or ''
            cls = _rep_key(ptx.symbol, pd_)
            parts = []          # (cover or None, units on pay date, lot)
            for e, cover_id, lot_id in _short_cover_chunks:
                closed = e.get('trade_date') or e.get('date') or ''
                if (e.get('account') != ptx.account
                        or _rep_key(e.get('symbol') or '', closed) != cls
                        or not (e.get('acquired_date') or '') <= pd_ <= closed):
                    continue
                f = _rep_units_factor(e['symbol'], closed, pd_) or 1.0
                parts.append(((e, cover_id, lot_id, f),
                              float(e['qty']) * f, None))
            for k, lots in inventory_short.items():
                sym = k[1] if per_account_basis else k
                if _rep_key(sym, _books_end) != cls:
                    continue
                f = _rep_units_factor(sym, _books_end, pd_) or 1.0
                for lot in lots:
                    acct = (k[0] if per_account_basis
                            else _acct_of_row.get(lot.get('id')))
                    if (acct == ptx.account and (lot.get('date') or '') <= pd_
                            and lot['qty'] > epsilon):
                        parts.append((None, float(lot['qty']) * f, lot))
            total = sum(q for _c, q, _l in parts)
            rec: Dict[str, Any] = {'capitalized': 0.0,
                                   'on_short': total > epsilon, 'open': []}
            _paid_comp['payments'][ptx.id] = rec
            if total <= epsilon:
                return
            units: Dict[tuple, float] = {}
            for cover, q, lot in parts:
                if cover is None:
                    gap = _day_gap(lot.get('date') or '', _books_end)
                    if gap is not None and gap < 45:
                        rec['open'].append({
                            'date': lot['date'],
                            'covered_by': (
                                datetime.strptime(lot['date'], '%Y-%m-%d')
                                + timedelta(days=45)).strftime('%Y-%m-%d'),
                            'amount': paid * q / total})
                    continue
                e, cover_id, lot_id, f = cover
                closed = e.get('trade_date') or e.get('date') or ''
                gap = _day_gap(e.get('acquired_date') or '', closed)
                if gap is None or gap > 45:
                    continue
                units[(cover_id, lot_id)] = paid * f / total
                rec['capitalized'] += paid * q / total
            per_unit = _paid_comp['per_unit']
            for key, unit in units.items():
                per_unit[key] = per_unit.get(key, 0.0) + unit

        def _paid_pil_treatment(ptx, paid: float) -> Dict[str, Any]:
            """The plan's record of the payment `ptx`, said once."""
            if _comp_building:
                _plan_paid_pil(ptx, paid)
            rec = _comp_payments.get(ptx.id) or {}
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

        _books_end = max((t.date or '' for t in transactions), default='')
        _acct_of_row = {t.id: t.account for t in transactions}

        # Dividends — report the gross dividend (1099-DIV box 1a). Withheld
        # foreign tax flows via separate TAX records into the foreign tax
        # credit, not the dividend income line.
        for tx in transactions:
            # PIL gets its own entry so sum-gains can show it in a dedicated
            # column. Kept out of the eligible-dividend total — IRS treats
            # PIL as a non-qualified substitute payment (ordinary income).
            if tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu':
                pil_amount = tx.gross_amount if tx.gross_amount else tx.net_amount
                _ppil = (_paid_pil_treatment(tx, -float(pil_amount))
                         if float(pil_amount or 0.0) < -0.005 else {})
                _cap = _ppil.get('capitalized') or 0.0
                realized_gains.append({
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'symbol': tx.symbol, 'qty': tx.quantity,
                    'currency': tx.currency, 'gain': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                    'pil': (float(pil_amount) + _cap if _cap
                            else pil_amount),
                    'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND_IN_LIEU',
                    **({'capitalized_263h': round(_cap, 4)} if _cap
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
            emit_line(f"warning: US gain disallowance invariant broken — "
                f"allowed-raw={sum_allowed - sum_raw:.4f} vs disallowed={sum_disallowed:.4f}")
        _warn_undrained_adjustments(pending_option_adjustments.undrained(), "usa")
        if _us_dust:
            _by: Dict[str, List[float]] = {}
            for _t in _us_dust:
                _by.setdefault(_t.symbol, []).append(_t.quantity)
            emit_line("warning: rows smaller than 1e-08 units are not booked "
                  "by the US engine: " + ", ".join(
                      f"{s_} ({len(q)} row(s), net {sum(q):+.3g})"
                      for s_, q in sorted(_by.items()))
                  + " — their units and money are left out of the lots "
                  "and Form 8949.")
            # Money left out is a conservation problem, not dust: said in
            # that channel too, so `sum --json`'s readiness counts it
            # (2026-10 critique, the US twin of audit #101).
            _money: Dict[str, float] = {}
            for _t in _us_dust:
                _money[_t.symbol] = (_money.get(_t.symbol, 0.0)
                                     + abs(float(_t.net_amount or 0.0)))
            _material = {k: v for k, v in _money.items() if v >= 0.01}
            if _material:
                emit_line("warning: conservation: rows under 1e-08 units "
                          "carrying money are left out of the US lots: "
                          + ", ".join(f"{k} {v:,.2f}"
                                      for k, v in sorted(_material.items()))
                          + " (their buy and sale amounts) — enter the "
                          "position with its real units.")
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
                emit_line(f"warning: {_what}: " + ", ".join(
                    f"{s_} ({len(q)} time(s), {sum(q):+.3g} units)"
                    for s_, q in sorted(_agg.items()))
                    + " (US-CRYPTO-08).")

        # Warn-only call-as-replacement scan (the experimental US engine
        # does not enforce it; always on — cross_asset is retired).
        # Losses on the TRADE basis, matching the §1091 window
        # convention. No held-at-end requirement in §1091 (that is a CRA
        # s.54 condition).
        _orw_src = [g for g in sell_entries
                    if g.get('raw_gain', g['gain']) < -0.005
                    and not g.get('tainted')]
        _orw_losses = [
            {'symbol': g['symbol'],
             'date': g['date'],
             'qty': abs(float(g.get('qty') or 0.0)),
             'amount': g.get('raw_gain', g['gain']),
             'id': g.get('id', ''),
             'direction': g.get('direction', 'LONG')}
            for g in _orw_src]

        def _open_qty(g):
            # The loss shares no share purchase replaced: a loss share
            # is matched once (US-WASH-20), so a call can replace only
            # the part still allowed (backlog TJ-0467).
            raw = float(g.get('raw_gain', g['gain']) or 0.0)
            den = float(g.get('disallowed_amount') or 0.0)
            q = abs(float(g.get('qty') or 0.0))
            if den <= 0.005:
                return q
            return q * max(0.0, 1.0 - min(1.0, den / -raw))
        _orw_calls = [dict(l, open_qty=_open_qty(g))
                      for l, g in zip(_orw_losses, _orw_src)]
        option_replacement_warnings = detect_option_replacement_matches(
            _orw_calls, all_events,
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
        option_replacement_warnings += detect_opening_lot_matches(
            _orw_losses, all_events, date_of=lambda t: t.date,
            canonical=split_timeline.canonical,
            statute_label="IRS §1091")
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
             or (t.action == 'TRANSFER' and t.type == LOT_MOVE_TYPE)
             or _is_listing_journal(t)],
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
        # (defensive — taxjson-export tolerates the empty case). One
        # builder for the final lots and the year-end ones (US-RPT-13).
        def _lot_rows(inv_long, inv_short):
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
                            'basis': float(money),
                            'deferred': round(float(
                                _lot_wash_held(l) if sign > 0 else l.get(
                                    'wash_deferred', Decimal(0))), 4)})
            return rows

        def _inventory_rows(inv_long, inv_short):
            rows = []
            for s, lots in inv_long.items():
                _acct = s[0] if per_account_basis else ''
                s = s[1] if per_account_basis else s
                tot_qty = sum(l['qty'] for l in lots)
                if tot_qty > epsilon:
                    rows.append({
                        'symbol': s, 'qty': tot_qty,
                        **({'account': _acct} if _acct else {}),
                        'total_cost': float(sum(l['cost_basis']
                                                for l in lots)),
                        'currency': symbol_currency.get(s, ''),
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
                        'deferred_wash': round(float(sum(
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
                        'total_cost': float(-sum(l['proceeds']
                                                 for l in lots)),
                        'currency': symbol_currency.get(s, ''),
                        'position_start_date': min(l['date']
                                                   for l in lots),
                        'last_acq_date': max(l['date'] for l in lots),
                        'deferred_wash': round(float(sum(
                            l.get('wash_deferred', Decimal(0))
                            for l in lots)), 4),
                        # A lot whose basis these books do not have
                        # (backlog TJ-0598): total_cost leaves it out.
                        **({'unknown_cost': True}
                           if any(l.get('tainted') for l in lots
                                  if l['qty'] > epsilon) else {}),
                    })
            return rows

        inventory_report = _inventory_rows(inventory_long, inventory_short)

        _ov_items = (_LO.summarize(_ov_plan, _ov_would, country='usa')
                     if _ov_plan else [])
        _LO.stamp_entries(realized_gains, _ov_items)
        return {
            **({'loss_overrides': _ov_items} if _ov_plan else {}),
            'transactions': realized_gains,
            'by_ticker': by_ticker,
            'wash_sales': wash_sale_records,
            'option_replacement_warnings': option_replacement_warnings,
            'dated_notes': [list(n) for n in _dated_notes],
            'inventory': inventory_report,
            # The lots at the end of year_end (see _ye_lots), in the
            # inventory's shape.
            **({'inventory_year_end': _inventory_rows(*_ye_lots)}
               if _ye_lots is not None else {}),
            # The date the year end was read on, when not the walk's
            # trade date (TJ-0343): edge-cases says which otherwise.
            **({'inventory_year_end_basis': 'settle'}
               if _ye_lots is not None and year_end_basis == 'settle'
               else {}),
            # Every lot held at the end of lots_at, one by one, in
            # FIFO order (backlog TJ-0554).
            **({'lots_at': _lot_rows(*_la_lots)}
               if _la_lots is not None else {}),
            # Each account's lots after every row that changed them
            # (backlog TJ-0417): a day's lots are every row up to the
            # last one dated on or before it, as _ye_lots.
            **({'cost_timeline': _tl.finish(
                _tl_dates, basis=cost_timeline,
                stamp='min' if cost_timeline != 'settle' else 'pool')}
               if _tl is not None else {}),
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

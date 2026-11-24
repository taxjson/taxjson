"""Base class for brokerage parsers.

Every brokerage parser subclasses BaseBrokerage and fills the parse
template's phases (read, classify, build, finish); parse_file is the
template's, and no parser overrides it.
Shared logic — OCC option symbol reconstruction, currency suffix mapping,
fee back-compute, T+1 settlement, robust date parsing — lives here as
helpers so each parser file stays small and focused on its CSV's quirks.

Designed so an AI-generated parser for a new brokerage can produce a short
subclass that delegates to these helpers instead of re-implementing them.
"""

from taxjson.lib.option_symbols import root_matches_stock
from taxjson.lib.privacy import mask_ids
from taxjson.lib.stage_msg import emit_line
from taxjson.lib.ca_listing import (canonical_ca_listing as canonical_ca_listing,
                                    canonical_ca_root as canonical_ca_root,
                                    is_ca_pref_series as is_ca_pref_series)
from taxjson.lib.symbol_case import SYMBOL_FIELDS as SYMBOL_FIELDS, fold_rows
from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple, Sequence
from taxjson.parsers.toolbox.errors import BrokerageParseError as BrokerageParseError
from taxjson.parsers.toolbox.masking import mask_id
from taxjson.parsers.toolbox.descriptions import ISSUER_ROC_KEY
from taxjson.parsers.toolbox.expiry import (
    clamp_settlement_across, non_option_expiry_booking_date, option_expiry_booking_date)
# A dividend row's share count and rate (PLAN step 14: the toolbox's now),
# importable here for the tests and lib/positions_check (lib reads no
# toolbox).
from taxjson.parsers.toolbox.descriptions import (
    _DIV_QTY_ON_SHS_RE as _DIV_QTY_ON_SHS_RE, _parse_div_qty_rate as _parse_div_qty_rate)
from taxjson.parsers.protocol import KNOWN_NONEVENT_PREFIX, Outcome, REPORTED_PREFIX, RowCounts
from taxjson.model import money as M

from taxjson.parsers.toolbox.numbers import DESC_NUMBER_RE as DESC_NUMBER_RE
from taxjson.parsers.toolbox.numbers import (MINUS_CHARS, check_comma_grouping as check_comma_grouping,
                                             desc_number as desc_number,
                                             parse_strict_number as parse_strict_number)
# The registry's views (taxjson.parsers.registry), here so a lib module
# reads them without importing the detector or the registry: the parsers'
# display names by id (lib/brokerages/detect reads it too), and the parsers
# whose parse builds the export's cash events (`builds_cash`: lib/cash_events).
from taxjson.parsers.registry import (cash_event_brokers as _cash_event_brokers,
                                      display_names as _display_names)
DISPLAY_NAMES: Dict[str, str] = _display_names()
CASH_EVENT_BROKERS: FrozenSet[str] = _cash_event_brokers()

# The Outcome kinds the skip counters keep, and the encoding of each kind's
# class in a counter's key (parsers.protocol: `skip_classes` the one decoding).
_SKIP_PREFIX = {"skip": "", "nonevent": KNOWN_NONEVENT_PREFIX, "reported": REPORTED_PREFIX}
_CONSUMED = Outcome.consumed()


def encode_occ_strike(strike) -> str:
    """Encode an option strike as the 8-digit OCC thousandths field.

    Uses Decimal, NOT float: `int(float("4.02") * 1000)` evaluates to
    4019 (the float 4.02 is actually 4.01999999…), not 4020 — mis-encoding
    roughly half of all sub-$200 cent-precision strikes and fragmenting an
    option's identity / cost basis across two symbols. `ticker_map.py`
    already fixed this on the ticker.map side; this is the same fix for the
    broker parsers. `strike` may be a float-as-string or a number.

    The field holds 0.001 to 99999.999 in steps of 0.001. A strike it
    cannot hold is refused (BrokerageParseError): int() truncated
    10.0009 to 10.000, the identity of another contract, and a strike
    of 0 or a negative one became an OCC symbol too (backlog TJ-0520)."""
    raw = str(strike).strip()
    try:
        thousandths = Decimal(raw) * 1000
    except ArithmeticError:
        thousandths = None
    if (thousandths is None or not thousandths.is_finite()
            or thousandths != thousandths.to_integral_value()
            or not 0 < thousandths < 10 ** 8):
        raise BrokerageParseError(
            f"option strike {raw!r} has no OCC spelling (a strike of "
            f"0.001 to 99999.999, to the 0.001) — refusing to round it "
            f"onto another contract's symbol. Book the contract in a .tt "
            f"file under the symbol of your choice.")
    return f"{int(thousandths):08d}"


# An option strike in a broker description. A strike of 1,000 or more
# may carry a thousands separator ('CALL SPX 12/19/25 5,000.00'); the  # pii-ok
# old `[\d\.]+` stopped at the comma, so 5,000 and 5,025 both became
# strike 5 -- two contracts in one OCC symbol and one ACB pool (audit
# R1-170). The grouped form is tried first; option_strike_text drops
# the separators.
#
# A comma that is NOT a valid thousands group ('2,50', '12,5',
# '1,0000') is captured whole (the second alternative), so
# option_strike_text refuses it instead of the regex stopping at the
# comma and booking strike 2 / 12 / 1000 (audit A2-0632).
OPTION_STRIKE_RE = (r'([1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?(?![\d,])'
                    r'|[\d\.]+(?:,[\d\.]+)*)')
_VALID_GROUPED_STRIKE_RE = re.compile(r'[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?')


# A number inside broker DESCRIPTION text ("ON 1,000 SHS", "BOOK COST
# $5,293.06", "REINV@C$1,234.56"): digits with any commas, so a decimal  # pii-ok
# comma is captured whole and judged by `desc_number` instead of being
# stripped (1,16 read as 116) or cut at the comma (1,234.56 read as 1).  # pii-ok


def option_strike_text(raw: str) -> str:
    """A matched strike with its thousands separators removed. A comma
    that is not a thousands group (a decimal comma '2,50', '1,0000') is
    refused rather than guessed (audit A2-0632)."""
    raw = raw or ''
    if ',' in raw and not _VALID_GROUPED_STRIKE_RE.fullmatch(raw):
        raise BrokerageParseError(
            f"option strike {raw!r} has a comma that is not a thousands "
            f"separator (a decimal comma?) — refusing to guess the strike")
    return raw.replace(',', '')


def option_parts(right: str, base: str, expiry: str,
                 strike: str) -> Dict[str, Optional[str]]:
    """A contract's parts as a description names them, normalised one way
    for every parser: the right 'C' or 'P' (C, CALL, P or PUT in any case;
    None for anything else), the root trimmed of spaces and dots (an inner
    space a dot), the expiry as written, the strike through
    `option_strike_text` (which refuses a decimal comma)."""
    return {
        'right': {'C': 'C', 'CALL': 'C', 'P': 'P', 'PUT': 'P'}.get(
            (right or '').strip().upper()),
        'base': (base or '').strip().strip('.').replace(' ', '.'),
        'expiry': expiry,
        'strike': option_strike_text(strike),
    }


# Return-of-capital marker, shared by every parser so the classification
# can't drift per broker. ROC is NOT dividend income: it reduces the
# position's ACB (emitted as an ADJUST row with negative net_amount via
# `BaseBrokerage.tx_roc_adjust`). Word-boundary so e.g. "RETURNED CAPITAL
# GAINS DISTRIBUTION" phrasing doesn't false-positive; "RETURN OF CAPITAL
# GAINS" and a fund NAME ("... RET OF CAPITAL ETF CASH DIV") are not a
# return of capital, nor is one the text negates ("NOT A RETURN OF
# CAPITAL") — each became an ACB reduction instead of a dividend (audit
# R1-76).
ROC_DESC_RE = re.compile(
    r'\bRET(?:URN)?\s+OF\s+CAPITAL\b(?!\s+(?:GAINS?|ETF|FUND|INCOME)\b)',
    re.IGNORECASE)
_ROC_NEGATED_RE = re.compile(r'\bNOT\s+(?:AN?\s+)?$', re.IGNORECASE)


def is_roc_description(desc: Optional[str]) -> bool:
    for m in ROC_DESC_RE.finditer(desc or ''):
        if not _ROC_NEGATED_RE.search((desc or '')[:m.start()]):
            return True
    return False


# The record date Questrade and RBC print in an income row's description
# ("... DIST ON 100 SHS REC 12/31/24 PAY 01/15/25"), and their word for a
# distribution ("DIST ON"). Neutral facts: which tax year they decide is
# a per-country rule (characterize/income_dating), never the parser's.
_REC_DATE_RE = re.compile(r'\bREC\s+(\d{1,2})/(\d{1,2})/(\d{2}(?:\d{2})?)\b',
                          re.IGNORECASE)
_DIST_LABEL_RE = re.compile(r'\bDIST\s+ON\b', re.IGNORECASE)


def income_facts_from_description(desc: Optional[str],
                                  activity: str = '') -> Dict[str, str]:
    """{record_date, income_label} read from an income row's description
    (and the broker's activity label): only the keys it finds. An
    impossible REC date is left out (the row keeps its pay date)."""
    out: Dict[str, str] = {}
    m = _REC_DATE_RE.search(desc or '')
    if m:
        mm, dd, yy = (int(g) for g in m.groups())
        if yy < 100:
            yy += 2000
        try:
            out['record_date'] = datetime(yy, mm, dd).strftime('%Y-%m-%d')
        except ValueError:
            pass
    if (_DIST_LABEL_RE.search(desc or '')
            or 'distribution' in (activity or '').lower()):
        out['income_label'] = 'distribution'
    return out


def roc_adjust_row(*, symbol: str, currency: str, date: str, desc: str,
                   amount: float, account: str, no_cash: str = '') -> Dict[str, Any]:
    """One shared shape for a return-of-capital row: an ADJUST that
    REDUCES ACB by the cash received (net_amount = -amount), tagged
    type='roc' so `tjs roc`/`roc-sum` can report it. `amount` is
    signed cash received — positive for a normal ROC posting, negative
    for a broker reversal row (which then nets out as a positive
    ADJUST). ROC must NOT be booked as dividend income: that
    double-errs (income overstated now, ACB overstated → gains
    understated at sale).

    The row is dated by its posting (pay) date; a record date the
    description prints ("REC 12/30/24") rides along as the neutral
    `record_date` fact — characterize/income_dating decides, per country,
    which date lowers the cost.

    `no_cash`: why the row lowers the cost without any cash arriving
    with it (RBC's year-end book-cost row); the FX-on-cash ledgers
    then book no receipt (backlog TJ-1033)."""
    tx = {
        'action': 'ADJUST',
        'date': date, 'time': '09:30:00', 'date_settle': date,
        'symbol': symbol, 'quantity': 0.0, 'currency': currency,
        'net_amount': -amount, 'gross_amount': 0.0, 'type': 'roc',
        'account': account,
        'description': desc,
    }
    rec = income_facts_from_description(desc).get('record_date')
    if rec:
        tx['record_date'] = rec
    if no_cash:
        tx['no_cash'] = no_cash
    return tx


# A broker-printed settle date more than this many calendar days after
# the trade is flagged ATTENTION (CA-DATE-03 / US-DATE-04, audit A2-0104).
SETTLE_LAG_FLAG_DAYS = 7

# The fields a row's content id hashes (model.transaction
# `TaxTransaction.compute_id`), with the value a row that lacks one is
# built with.
FILL_IDENTITY_FIELDS: Tuple[Tuple[str, Any], ...] = (
    ('action', ''), ('date', ''), ('time', '09:30:00'), ('symbol', ''),
    ('quantity', 0.0), ('currency', ''), ('price', 0.0), ('net_amount', 0.0),
    ('gross_amount', 0.0), ('account', 'PORTFOLIO'), ('type', ''),
    ('date_settle', ''), ('description', ''))


def fill_identity(tx: Dict[str, Any]) -> tuple:
    """What a parsed row's content id will be made of
    (FILL_IDENTITY_FIELDS): two rows of one file equal in all of it get
    one id, and only they are split fills
    (`BaseBrokerage.disambiguate_split_fills`; TJ-5038). A number is
    compared as a number (10 and 10.0 are one quantity, as the id's
    float has them), the rest as text."""
    out: List[Any] = []
    for name, default in FILL_IDENTITY_FIELDS:
        value = tx.get(name)
        if value is None:
            value = default
        numeric = isinstance(default, float) and isinstance(value, (int, float, Decimal))
        out.append(value if numeric else str(value))
    return tuple(out)


# The project's ticker.map as `taxjson-brokerage --ticker-map` loaded it
# (run passes it): (fixed-point renames incl. TOBASE/JOURNAL, the dated
# RENAME pairs). None = no map given — every identity hint prints.
_TICKER_JOINS: Optional[Tuple[Dict[str, str], frozenset]] = None
# Every symbol a rule of that map names (ticker_map_rules.named_symbols).
_TICKER_NAMED: frozenset = frozenset()
# ... and every symbol any line of it names, a lookup line's included
# (named_symbols(lookups=True)).
_TICKER_MENTIONED: frozenset = frozenset()
# ... and its DISTINCT pairs (frozensets of two symbols).
_TICKER_DISTINCT: frozenset = frozenset()
_TICKER_PATH: Optional[Path] = None
# ... and its case-pair lines ({an export's spelling: SAME or APART},
# lib/symbol_case): how parse_file spells such a symbol.
_TICKER_CASE: Dict[str, str] = {}


def set_ticker_map(path) -> None:
    """Load the ticker.map whose joins make a parser's identity hint
    moot (re-audit A2-1056): a ticker-change or listing hint for a pair
    the map already pools is not printed. None clears it. A map that
    does not parse is ignored here (`tjs run` refuses it up front)."""
    global _TICKER_JOINS, _TICKER_NAMED, _TICKER_MENTIONED, _TICKER_DISTINCT
    global _TICKER_PATH, _TICKER_CASE
    _TICKER_PATH = None if path is None else Path(path)
    if path is None:
        _TICKER_JOINS = None
        _TICKER_NAMED = frozenset()
        _TICKER_MENTIONED = frozenset()
        _TICKER_DISTINCT = frozenset()
        _TICKER_CASE = {}
        return
    from taxjson.lib.ticker_map_rules import merge_renames, named_symbols
    from taxjson.lib.map_file import _parse_map_file
    tmap = _parse_map_file(Path(path))[0]
    _TICKER_NAMED = named_symbols(tmap)
    _TICKER_MENTIONED = named_symbols(tmap, lookups=True)
    _TICKER_DISTINCT = frozenset(tmap.distinct)
    _TICKER_CASE = dict(tmap.case_spellings)
    try:
        ren = merge_renames(tmap, True)
    except ValueError:
        ren = {}
    _TICKER_JOINS = (ren, frozenset((d.old, d.new) for d in tmap.dated))


def ticker_map_distinct_pairs() -> frozenset:
    """The DISTINCT pairs of the loaded ticker.map (empty without one)."""
    return _TICKER_DISTINCT


def ticker_map_file() -> Optional[Path]:
    """The ticker.map set_ticker_map loaded (the parser's --ticker-map,
    which `tjs run` passes), or None."""
    return _TICKER_PATH


def ticker_map_loaded() -> bool:
    return _TICKER_JOINS is not None


def ticker_map_rules():
    """(the undated rename map, the dated RENAME (old, new) pairs) of the
    loaded ticker.map, or None without one — for a check that needs the
    direction of a dated line (ticker_map_joins reads either)."""
    return _TICKER_JOINS


def ticker_map_renames(sym: str) -> bool:
    """True when the loaded ticker.map renames listing `sym` (an
    undated rule or chain, or a dated RENAME of it)."""
    if _TICKER_JOINS is None or not sym:
        return False
    from taxjson.lib.ticker_map_rules import map_symbol
    ren, dated = _TICKER_JOINS
    s = sym.upper()
    return map_symbol(s, ren) != s or any(old == s for old, _n in dated)


def ticker_map_names(sym: str) -> bool:
    """True when a rule of the loaded ticker.map names listing `sym` in
    any way (a rename either side, DELETE, DISTINCT, a dated RENAME):
    the user's rule wins over any inference (lib/symbol_codes)."""
    return bool(sym) and sym.upper() in _TICKER_NAMED


def ticker_map_mentioned() -> frozenset:
    """Every symbol any line of the loaded ticker.map names, a lookup
    line's (QUOTE, T1135, an EXTRACT target ...) included; empty with no
    map loaded."""
    return _TICKER_MENTIONED


def ticker_map_joins(a: str, b: str) -> bool:
    """True when the loaded ticker.map already treats listings `a` and
    `b` as one security: both rename to the same symbol (GLOBAL, TOBASE,
    JOURNAL, undated RENAME, chains included), or a dated RENAME joins
    them (either direction, after the undated renames)."""
    if _TICKER_JOINS is None or not a or not b:
        return False
    from taxjson.lib.ticker_map_rules import map_symbol
    ren, dated = _TICKER_JOINS
    ma, mb = map_symbol(a.upper(), ren), map_symbol(b.upper(), ren)
    if ma == mb:
        return True
    for old, new in dated:
        mo, mn = map_symbol(old, ren), map_symbol(new, ren)
        if {mo, mn} == {ma, mb}:
            return True
    return False


# The ticker changes the project's .tt RENAME lines declare, as `tjs
# run` passes them (taxjson-brokerage --declared-renames): {(OLD, NEW):
# the line's place, inputs/<acct>/<file>.tt:<n>}. Empty = none given.
_DECLARED_RENAMES: Dict[Tuple[str, str], str] = {}


def set_declared_renames(path) -> None:
    """Load the declared ticker changes (one `OLD<TAB>NEW<TAB>WHERE` line
    each, the file `tjs run` writes: work/declared_renames.list)
    that answer a parser's look-alike rename hint. None clears them."""
    _DECLARED_RENAMES.clear()
    if path is None:
        return
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3 and parts[0].strip() and parts[1].strip():
            _DECLARED_RENAMES.setdefault(
                (parts[0].strip().upper(), parts[1].strip().upper()),
                parts[2].strip())


def declared_rename_where(a: str, b: str) -> Optional[str]:
    """Where a declared ticker change (set_declared_renames) joins
    listings `a` and `b` (either direction, read through the loaded
    ticker.map's renames as ticker_map_joins reads them), or None."""
    if not _DECLARED_RENAMES or not a or not b:
        return None
    ren = _TICKER_JOINS[0] if _TICKER_JOINS is not None else {}

    def m(sym: str) -> str:
        if not ren:
            return sym.upper()
        from taxjson.lib.ticker_map_rules import map_symbol
        return map_symbol(sym.upper(), ren)
    want = {m(a), m(b)}
    for (old, new), where in _DECLARED_RENAMES.items():
        if {m(old), m(new)} == want:
            return where
    return None


def rename_reach(old, new) -> Optional[Tuple[float, float, str, float]]:
    """Whether listing `old` looks renamed to `new`, a ticker change booked
    with no corporate-action row (Questrade's and RBC's judgment; audits
    S062-06, S063-12, re-audit A2-0099): `old` stops on or before the day
    `new` starts, with shares still open, and `new` goes short by no more
    than those shares (its first row a sale, or a buy followed by a larger
    sale). `old` and `new` are each a date-ordered timeline of events whose
    first item is the date and last the quantity, in the parser's own
    same-day order. (shares `old` leaves open, `new`'s deepest short, the
    date it is first reached, `new`'s first nonzero quantity), else None.
    Webull's rule is its own (`_warn_ticker_changes`: any short after a
    same-description predecessor; TJ-2322)."""
    if not old or not new or old[-1][0] > new[0][0]:
        return None
    open_old = sum(e[-1] for e in old)
    run = low = first = 0.0
    when = ''
    for e in new:
        q = e[-1]
        if not first and abs(q) > 1e-9:
            first = q
        run += q
        if run < low - 1e-9:
            low, when = run, e[0]
    if open_old <= 1e-9 or low >= -1e-9 or -low > open_old + 1e-6:
        return None
    return open_old, low, when, first


def rename_how(new_first: str, low: float, when: str, first: float) -> str:
    """How a renamed listing goes short (`rename_reach`'s figures), as the
    ATTENTION line says it: its first row a sale, or the short it reaches."""
    if first < 0:
        return f"first appears on {new_first} with a SALE of {-first:g}"
    return f"first appears on {new_first} and goes {-low:g} short on {when}"


def answered_rename_note(where: str, broker: str, old: str, new: str,
                         declared_at: str) -> str:
    """The parse's note in place of the look-alike rename ATTENTION when
    a .tt RENAME line already declares the change (never the hint's
    wording: `tjs renames` reads that as an open hint)."""
    return (f"note: {where}: {broker} symbol {old} stops and {new} starts "
            f"with the same description — the ticker change is answered "
            f"by {declared_at}.")


def combined_accounts_note(where: str, broker: str, masked) -> str:
    """The one-line NOTE that replaces the 'statement spans N accounts'
    ATTENTION when the label declares combined_broker_accounts = true
    (`masked`: the ids, already masked to their first 2 chars + ***)."""
    masked = list(masked)
    return (f"note: {where}: {len(masked)} {broker} accounts "
            f"({', '.join(masked)}) booked together under this account "
            f"label (combined_broker_accounts = true).")


def _warrant_root(symbol) -> str:
    """A listing's root, its dots dropped (`SAMPLZ.WT` -> `SAMPLZWT`)."""
    from taxjson.lib.markets import strip_listing_suffix
    return strip_listing_suffix(symbol or '').replace('.', '')


def pair_warrant_legs(warrants, shares) -> List[tuple]:
    """Each warrant's or right's exercise leg of `warrants` with the share
    leg of `shares` it delivers: `(leg, share leg or None, candidates)`, in
    the legs' order (A2-0274). A paired leg gets the share listing
    (`exercise_of`) and its settlement date; an unpaired one is each
    parser's outcome to give (RBC refuses the file, IB books the leg as
    before with an ATTENTION line), with `warrant_exercise_tt_hint`.

    By root, across all the legs of a date and currency: a share leg whose
    root starts a warrant's (`SAMPLZ` for `SAMPLZ.WT`) is a claim, and the
    closest claims go first, the least of the warrant's root left over
    (`AB.WT` is AB's before `ABC.WT` is; `ABCD.WT` is ABCD's, not ABC's),
    then in the legs' order (two blocks of one warrant into one listing).
    A blank root claims nothing. A warrant no root names takes the share
    leg only when the two are the day's one exercise (a SPAC's warrant
    into the new company's shares): a share leg left over by the others is
    never guessed at (TJ-2537, TJ-2538, TJ-2547 to TJ-2550)."""
    def day(t):
        return (t['date'], t.get('currency'))
    rw = [_warrant_root(w.get('symbol')) for w in warrants]
    rs = [_warrant_root(t.get('symbol')) for t in shares]
    claims = sorted((len(rw[i]) - len(rs[j]), i, j)
                    for i, w in enumerate(warrants)
                    for j, t in enumerate(shares)
                    if rs[j] and rw[i].startswith(rs[j]) and day(t) == day(w))
    of_w: Dict[int, int] = {}
    taken: set = set()
    for _left, i, j in claims:
        if i not in of_w and j not in taken:
            of_w[i] = j
            taken.add(j)
    n_w: Dict[tuple, int] = {}
    s_of_day: Dict[tuple, List[int]] = {}
    for w in warrants:
        n_w[day(w)] = n_w.get(day(w), 0) + 1
    for j, t in enumerate(shares):
        s_of_day.setdefault(day(t), []).append(j)
    out = []
    for i, w in enumerate(warrants):
        if i not in of_w:
            one = s_of_day.get(day(w), [])
            if (n_w[day(w)] == 1 and len(one) == 1 and one[0] not in taken
                    and rs[one[0]]):
                of_w[i] = one[0]
                taken.add(one[0])
        st = shares[of_w[i]] if i in of_w else None
        if st is not None:
            w['exercise_of'] = st['symbol']
            w['date_settle'] = st['date_settle']
        out.append((w, st, [st] if st is not None else []))
    return out


def warrant_exercise_tt_hint(leg: Dict[str, Any], candidates=()) -> str:
    """The `.tt` lines that book a warrant's or a right's exercise leg
    the parser could not pair with one share leg (docs/settings.md, .tt
    ASSIGN; backlog TJ-0590): the leg as an ASSIGN with `exercise_of=`
    the share listing it delivers (no disposition: the warrants' cost
    goes into the shares), and, when no row of the export books the
    shares, their BUYSELL at the exercise price. `candidates`: the share
    listings of the export's same-day exercise rows (several: the leg
    names one of them, whose row stays)."""
    d = leg.get('date') or 'DATE'
    t = leg.get('time') or '16:00:00'
    sym = leg.get('symbol') or 'WARRANT'
    cur = leg.get('currency') or 'CUR'
    try:
        qty = f"{float(leg.get('quantity')):g}"
    except (TypeError, ValueError):
        qty = '-QTY'
    cands = sorted({str(c) for c in candidates if c})
    line = f"ASSIGN {d} {t} {sym} {qty} {cur} 0 0 exercise_of=SHARES"
    if cands:
        return (f"`{line}`, SHARES being the one of {', '.join(cands)} "
                f"the warrants deliver (its exercise row stays in the "
                f"export; docs/settings.md, .tt ASSIGN)")
    return (f"`{line}` (SHARES: the listing the warrants deliver), then, "
            f"unless a row of the export books the shares, `BUYSELL {d} "
            f"{t} SHARES N {cur} PRICE TOTAL` at the exercise price "
            f"(docs/settings.md, .tt ASSIGN)")


def combined_accounts_refusal(where: str, broker: str, masked,
                              why: str) -> 'BrokerageParseError':
    """combined_broker_accounts = true on a SHELTERED label whose
    statement spans several broker accounts that are not provably one
    plan: refused — one registered plan's rows booked in another's
    account would mis-state both (and a taxable account's rows would
    vanish from the return)."""
    masked = list(masked)
    return BrokerageParseError(
        f"{where}: the statement spans {len(masked)} {broker} accounts "
        f"({', '.join(masked)}) and this account is sheltered — "
        f"combined_broker_accounts = true is honoured on a sheltered "
        f"account only when every account is the same registered plan, "
        f"and {why}. Export each plan into its own inputs/<account>/ "
        f"folder (or drop the setting).")


# The registered plans an account type names: the plan check's shelter
# words (`check_account_plans`; Questrade's Account Type, a mapping's
# account_type column).
REGISTERED_PLAN_RE = re.compile(
    r'\b(RRSP|RRIF|TFSA|LIRA|LIF|LRSP|RLSP|RESP|FHSA|RDSP|SRRSP|PRPP)\b',
    re.IGNORECASE)
# A taxable account's type, no plan named: margin or cash (Questrade),
# non-registered (NON_REGISTERED too, TJ-2588).
_TAXABLE_TYPE_RE = re.compile(r'margin|cash|non[-_ ]?registered', re.IGNORECASE)


def plan_shelter_of(plans_taxed, account_type: str) -> Optional[str]:
    """The registered plan an account type names (TFSA, RRSP ...), upper
    case, when the project's country shelters it; None for any other
    type. A plan the country taxes like an ordinary account
    (`plans_taxed`: a TFSA in a US project, tax-logic US-BASIS-09) is no
    shelter (backlog TJ-0613)."""
    m = REGISTERED_PLAN_RE.search(account_type or '')
    if m and m.group(1).lower() not in plans_taxed:
        return m.group(1).upper()
    return None


def plan_taxed_of(plans_taxed, account_type: str) -> Optional[str]:
    """The registered plan an account type names when the project's
    country taxes it like an ordinary account, else None."""
    m = REGISTERED_PLAN_RE.search(account_type or '')
    if m and m.group(1).lower() in plans_taxed:
        return m.group(1).upper()
    return None


def is_taxable_account_type(plans_taxed, account_type: str) -> bool:
    """A margin, cash or non-registered account's type, or one naming a
    plan the project's country taxes (`plan_taxed_of`)."""
    if plan_taxed_of(plans_taxed, account_type):
        return True
    return bool(_TAXABLE_TYPE_RE.search(account_type or '')
                and not REGISTERED_PLAN_RE.search(account_type or ''))


def taxed_plans_note(plans_taxed, types) -> str:
    """' (a US return taxes a TFSA like an ordinary account: ...)' when
    `types` name such a plan, else ''."""
    plans = sorted({p for t in types for p in [plan_taxed_of(plans_taxed, t)]
                    if p})
    if not plans:
        return ''
    return (f" (a US return taxes "
            f"{'a ' + plans[0] if len(plans) == 1 else ', '.join(plans)}"
            f" like an ordinary account: no treaty deferral, tax-logic "
            f"US-BASIS-09)")


def check_account_plans(parser, where: str, accts: Dict[str, set], holds: str,
                        *, broker: str, column: str = "Account Type",
                        plural: Optional[str] = None,
                        spread: bool = False,
                        say_unknown: bool = False) -> None:
    """The taxable-versus-registered check over `accts` ({broker account:
    {its account type}}) of one export, or of all of a taxjson account's
    exports (`spread`): a broker account whose type contradicts the
    taxjson account (`parser.account_taxable`) is an ATTENTION when it is
    the only one, refused beside others; several accounts are an
    ATTENTION unless `combined_broker_accounts` (then a note, or refused
    on a sheltered label whose accounts are not one plan). `broker` and
    `column` name the export's broker and its account-type column in the
    messages (Questrade's Account Type, the generic importer's
    `account_type` column, TJ-2576); an account of no number (a file
    with no account column) is shown as such (TJ-2593). `say_unknown`:
    a type naming neither a plan nor a taxable account is said, since
    the check cannot judge it (the generic importer's, TJ-2588;
    Questrade's `tjs fetch` exports carry "API" there)."""
    plural = plural or column + "s"
    if not accts:
        return
    _no = {a: i for i, a in enumerate(sorted(accts), 1)}

    def _mask(a: str) -> str:
        return f"#{_no[a]} {mask_id(a) if a else '(no account number)'}"
    taxable = parser.account_taxable       # None: the caller did not say
    if say_unknown:
        unknown = sorted({t for types in accts.values() for t in types
                          if t and not plan_shelter_of(parser.plans_taxed, t)
                          and not is_taxable_account_type(parser.plans_taxed, t)})
        if unknown:
            emit_line(
                f"note: {where}: the {column} {', '.join(map(repr, unknown))} "
                f"names no account type taxjson knows (a registered plan such "
                f"as TFSA or RRSP; margin, cash or non-registered), so whether "
                f"the export belongs in this "
                f"{'taxable ' if taxable else 'sheltered ' if taxable is False else ''}"
                f"account is not checked for it.")
    desc = ', '.join(f"{_mask(a)} ({'/'.join(sorted(t - {''})) or '?'})"
                     for a, t in sorted(accts.items()))
    wrong = []
    for a, types in sorted(accts.items()):
        reg = any(plan_shelter_of(parser.plans_taxed, t) for t in types)
        tax = any(is_taxable_account_type(parser.plans_taxed, t) for t in types)
        if (taxable is True and reg) or (taxable is False and tax):
            wrong.append(_mask(a))
    # A plan the country taxes (a US project's TFSA) said so, when it
    # is the reason a type reads as taxable (backlog TJ-0613).
    why = (taxed_plans_note(parser.plans_taxed, {t for types in accts.values()
                                   for t in types})
           if taxable is False and wrong else '')
    if len(accts) < 2:
        if wrong:
            # The folder's type is the user's word about this very
            # account, the Account Type column the broker's: said
            # out loud (`run --strict` stops), never guessed.
            types = '/'.join(sorted(next(iter(accts.values())) - {''}))
            emit_line(
                f"warning: ATTENTION: {where}: the {column} says "
                f"{broker} account {wrong[0]} is "
                f"{'a registered plan' if taxable else 'a taxable account'}"
                f" ({types}){why}, but this taxjson account is "
                f"{'taxable' if taxable else 'sheltered'}, so its "
                f"trades are booked "
                f"{'as taxable' if taxable else 'as sheltered (none of them on the return)'}"
                f". If the {column} is right, move "
                f"{'the exports' if spread else 'the export'} into "
                f"the inputs/<account>/ folder of an account of type = "
                f"\"{'sheltered' if taxable else 'taxable'}\".")
        return
    if not wrong and parser.combined_broker_accounts:
        # combined_broker_accounts = true (owner decision): a NOTE —
        # on a sheltered label only when every account is the SAME
        # registered plan (Questrade's Account Type names it).
        masked = [_mask(a) for a in sorted(accts)]
        if taxable is False:
            plans = {p for types in accts.values() for t in types
                     for p in [plan_shelter_of(parser.plans_taxed, t)] if p}
            untyped = any(not any(plan_shelter_of(parser.plans_taxed, t) for t in types)
                          for types in accts.values())
            if len(plans) != 1 or untyped:
                raise combined_accounts_refusal(
                    where, broker, masked,
                    f"their {plural} differ ({desc})"
                    if len(plans) > 1 else
                    f"the {column} does not name one plan for "
                    f"every account ({desc})")
        emit_line(combined_accounts_note(where, broker, masked))
        return
    if wrong:
        raise BrokerageParseError(
            f"{where}: {holds} rows of {len(accts)} "
            f"{broker} accounts ({desc}); {', '.join(wrong)} "
            f"{'is a registered plan' if taxable else 'is a taxable account'}"
            f"{why} but {'the files sit' if spread else 'the file sits'} in a "
            f"{'taxable' if taxable else 'registered'} taxjson account, "
            f"so its trades would be booked there — refusing. "
            + (f"Move each {broker} account's exports into its own "
               f"inputs/<account>/ folder." if spread else
               f"Export each {broker} account separately into its own "
               f"inputs/<account>/ folder."))
    # A US project's TFSA is no shelter (US-BASIS-09): the plans that
    # still are, named as such.
    plan = parser.law('a registered plan (TFSA/RRSP)',
                    'a retirement plan (RRSP/RRIF)',
                    'a registered plan (TFSA/RRSP)')
    emit_line(f"warning: ATTENTION: {where}: {holds} rows of "
          f"{len(accts)} {broker} accounts ({desc}) — every row is "
          f"booked to ONE account. That is right only when they are one "
          f"tax entity (two taxable accounts of yours); "
          + (f"give {plan} its own inputs/<account>/ folder."
             if spread else f"export {plan} separately."))


def check_account_plans_across(parser, files: Sequence[str],
                               plan_types: Dict[str, set],
                               plan_files: Dict[str, set], *, broker: str,
                               column: str = "Account Type",
                               plural: Optional[str] = None,
                               say_unknown: bool = False) -> None:
    """`check_account_plans` over ALL of a taxjson account's exports
    (`files`; `plan_types` {broker account: {its types}}, `plan_files`
    {broker account: {the files holding it}}): one broker account, or
    several no one export holds all of. An export holding them all is
    checked by its own parse (`check_account_plans` over its rows), so
    the two never say one thing twice. Questrade's exports (backlog
    TJ-0527) and the generic importer's (TJ-2584): a TFSA's file beside
    a margin account's was booked unchecked."""
    if not plan_types:
        return
    held = [{a for a, fs in plan_files.items() if f in fs} for f in files]
    names = sorted({shown_name(Path(f)) for f in files})
    if len(plan_types) == 1 or not any(h >= set(plan_types) for h in held):
        check_account_plans(
            parser, ', '.join(names), plan_types,
            "the export holds" if len(names) == 1 else
            "these exports of one taxjson account hold",
            broker=broker, column=column, plural=plural,
            spread=len(names) > 1, say_unknown=say_unknown)



def extract_words_match(desc_sub: str, desc: str) -> bool:
    """A ticker.map EXTRACT line's description words occur in `desc` as
    whole words, any case (IB's description is the bare ticker, so a
    plain substring test made 'QZ' rewrite QQZX and QZTX: audit
    S012-09)."""
    return re.search(r'(?<![a-z0-9])' + re.escape((desc_sub or '').lower())
                     + r'(?![a-z0-9])', (desc or '').lower()) is not None


def zero_units_refusal(where: str, action: str, qty_raw: Any,
                       cell: str = "Quantity") -> BrokerageParseError:
    """The refusal of a trade row of 0 units, or a blank quantity, that a
    broker parser would book (the owner's ruling of 2026-11-19, backlog
    TJ-4502, TJ-4511, TJ-4536, TJ-4543): it named no file or row before,
    stopping the account at `tjs run`'s schema check with a message that
    asks for a bug report. `where` names the file and row; the caller
    raises it."""
    raw = str(qty_raw if qty_raw is not None else "").strip()
    stated = f"{cell} {raw}" if raw else f"a blank {cell}"
    return BrokerageParseError(
        f"{where}: a {action} row with {stated} — a trade of 0 units has no "
        f"direction (buy or sell is the quantity's sign); refusing to book "
        f"it. Correct the {cell} from the statement, or delete the row and "
        f"enter the trade as a .tt BUYSELL line (a charge with no trade is "
        f"a .tt FEE line).")


# The advice of every "two downloads pasted into one file" refusal.
PASTED_EXPORTS_FIX = ("Save each download as its own file in the folder: "
                      "rows two exports share are then read once.")


def refuse_pasted_overlap(name: str, segments, what: str) -> None:
    """Refuse a file holding two downloads pasted together that share
    rows (backlog TJ-0099). `segments` is one (start line, [(line, row
    content)]) per export in the file: the first, then one from each
    repeated `what` (the header, the statement preamble). A row of one
    content in two segments is the overlap of two downloads: inside one
    file it was booked twice (two identical rows of one file are two
    fills), while two files' overlap is read once (taxjson-sort
    --dedup). Downloads that share no row are booked as they are."""
    first: Dict[tuple, Tuple[int, int]] = {}
    shared: List[Tuple[int, int, int]] = []
    for si, (_start, rows) in enumerate(segments):
        mine: Dict[tuple, Tuple[int, int]] = {}
        for line, key in rows:
            prev = first.get(key)
            if prev is not None and prev[0] != si:
                shared.append((prev[1], line, si))
            mine.setdefault(key, (si, line))
        for key, hit in mine.items():
            first.setdefault(key, hit)
    if shared:
        a, b, si = shared[0]
        raise BrokerageParseError(
            f"{name} line {segments[si][0]}: {what} repeats here — two "
            f"downloads pasted into one file, and {len(shared)} row(s) "
            f"are in both (line {b} repeats line {a}), so they would be "
            f"booked twice. {PASTED_EXPORTS_FIX}")


def shown_name(path) -> str:
    """A file's name as diagnostics print it: account-id-shaped tokens
    masked to their first 2 characters + *** (the parsers' rule for ids
    in the data), so a broker's default download name does not carry
    the id into reports/ (audit S027-02 / S059-10). Control characters
    are escaped (out.one_line): a name holding a newline must not add a
    line to a .diag a program reads back. The grammar of an id inside a
    name is `taxjson.lib.privacy.mask_ids`, every message's (TJ-2352)."""
    from taxjson.lib.out import one_line
    return mask_ids(one_line(Path(str(path)).name))


def source_key(path) -> str:
    """The dedup key beside a row's `source` (its shown_name): '' when
    the name has nothing masked, else sha256 of the real file name,
    first 10 hex — so two files whose names differ only in a masked
    account-number token (manual_55500001.tt / manual_55500002.tt)
    stay two sources, while no id is written out (audit A2-0159)."""
    name = Path(str(path)).name
    if shown_name(path) == name:
        return ''
    import hashlib
    return hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]


def source_identity(source, key) -> str:
    """One string per input file for dedup: the shown name, plus the
    key when the name was masked (the form of metadata.source_accounts
    keys)."""
    source = str(source or '')
    key = str(key or '')
    return f"{source}#{key}" if key and source else source


def decode_broker_text(raw: bytes, name: str = '') -> str:
    """The text of a broker export, decoded the way every parser and
    broker detection read it: a UTF-16 BOM is UTF-16, anything else
    UTF-8 with an optional BOM (any number of them). Any other encoding (a cp1252 re-save)
    raises BrokerageParseError with a one-line remedy instead of a codec
    traceback (audit S059-17)."""
    try:
        if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
            text = raw.decode('utf-16')
        else:
            text = raw.decode('utf-8-sig')
        # A file re-saved by a tool that adds its own BOM in front of one
        # it kept carries two: every one in front is still not text.
        return text.lstrip('\ufeff')
    except UnicodeDecodeError as e:
        where = f"{name}: " if name else ''
        raise BrokerageParseError(
            f"{where}not UTF-8 or UTF-16 text (byte 0x{raw[e.start]:02x} "
            f"at offset {e.start}) — a spreadsheet re-save in a legacy "
            f"encoding? Re-export the file, or save it as CSV UTF-8.")


def read_broker_text(path) -> str:
    """A broker export's text through `decode_broker_text` (UTF-16 with a
    BOM, else UTF-8 with an optional BOM), with line ends folded to
    '\\n' the way a text-mode open() reads them — the drop-in for
    `open(path, encoding='utf-8-sig').read()` in a parser, so a UTF-16
    re-save is read rather than refused as 'not UTF-16' (audit
    A2-0101 / A2-1451)."""
    text = decode_broker_text(Path(path).read_bytes(), shown_name(path))
    return text.replace('\r\n', '\n').replace('\r', '\n')


# The money and quantity columns BaseBrokerage.require_columns names
# when it refuses a missing one (the others get a reason of their own).
_MONEY_COLUMNS = frozenset({'Amount', 'Proceeds', 'Value', 'T. Price',
                            'Comm/Fee', 'Market Value', 'Notional Value',
                            'Quantity', 'Qty'})


# Canadian listing identity (audit S010-05 / S014-07): lib/ca_listing
# (imported above), which the ticker.map reader shares (backlog TJ-4604).
from taxjson.lib.markets import (currency_suffixes as _cur_sfx,  # noqa: E402
                                 strip_listing_suffix,
                                 listing_suffix_re as _sfx_re)


def upper_case_symbols(rows, case=None, seen=None, given=None) -> None:
    """Spell every row's symbols in upper case, as the books do
    (lib/symbol_case.fold_rows: a spelling a ticker.map case-pair line
    keeps apart, `case`, in its class spelling), counting the spellings
    read in `seen` when given. RBC, IB, Webull and Questrade's fee rows
    kept the export cell's case, so a `zyab` cell (an export edited by
    hand) was an ACB pool of its own beside ZYAB.TO, which no ticker.map
    line (read upper case) reached (backlog TJ-4601). No symbol is
    case-sensitive: share listings, OCC option symbols, futures and coin
    tickers are all upper case; two spellings that differ only in case
    stop `tjs run` (backlog TJ-4606)."""
    fold_rows(rows, case, seen, given)


class BaseBrokerage:
    # The STANDARD equity-option contract (100 shares); 1 for
    # equities/crypto. Only IB's export states a contract's size; every
    # other parser ASSUMES this one unless the row's own money shows a
    # mini (option_row_multiplier), and marks the row
    # `contract_size_basis` so the engine says when it relied on it
    # (ticker.map `MULT SYMBOL N` overrides — lib/markets, B10).
    OPTION_MULTIPLIER = 100
    MINI_OPTION_MULTIPLIER = 10

    # Currency → ticker-suffix mapping for the apply_currency_suffix
    # helper: the market data's table (lib/markets), one for every
    # parser. Subclasses can override with a narrower mapping.
    CURRENCY_EXT_MAP: Dict[str, str] = _cur_sfx()

    # Default fallback when CURRENCY_EXT_MAP doesn't contain the currency.
    # Use 'US' to match Webull's behavior; subclasses use this for unknown
    # currencies — most brokerages echo the currency code itself instead.
    CURRENCY_EXT_FALLBACK: Optional[str] = None

    # Default account label used when the source CSV doesn't name an account.
    # The taxjson-brokerage CLI overrides this with --account <name>.
    DEFAULT_ACCOUNT: str = "Unknown"

    # The project's country ("canada" | "usa"), set by taxjson-brokerage
    # from --country; None when the parser runs without one. Parsers
    # emit neutral FACTS whatever it is (partition rule: country gates
    # live in the engine/command layer). It only chooses which law a
    # user-facing message cites, so a US project never reads an ITA
    # section and a Canadian one never reads an IRC one (re-audit
    # A2-0723 / A2-1304 / A2-1308).
    country: Optional[str] = None

    # The project's ticker.map EXTRACT lines [(words, CURRENCY, symbol)]
    # (taxjson-brokerage sets them): a parser's "this row reads as another
    # listing" hint stays quiet when one of them rewrites the row.
    security_overrides: Sequence[tuple] = ()

    def _extract_symbol(self, desc: str, currency: str) -> Optional[str]:
        """The symbol the first matching EXTRACT line gives this row (the
        rule taxjson-brokerage applies after the parse), None when none
        matches."""
        cur = (currency or '').upper()
        for d, c, s in (self.security_overrides or ()):
            if extract_words_match(d, desc) and c in ('*', cur):
                return s
        return None

    def _extract_covers(self, desc: str, currency: str) -> bool:
        return self._extract_symbol(desc, currency) is not None

    def _extract_gave(self, row) -> bool:
        """Whether an EXTRACT line gives parsed `row` its symbol (a
        parser applies the line as it reads, RBC's): the symbol is the
        map's spelling, not the export's (backlog TJ-4606)."""
        if not self.security_overrides:
            return False
        s = row.get('symbol')
        return isinstance(s, str) and s == self._extract_symbol(
            str(row.get('description') or ''), str(row.get('currency') or ''))

    # [accounts.<name>] combined_broker_accounts = true (taxjson-brokerage
    # --combined-broker-accounts): the user declares that every broker
    # account in this label's statements is theirs and taxable together,
    # so a statement spanning several broker accounts is a one-line NOTE,
    # not an ATTENTION. Refused on a sheltered label unless the statement
    # itself shows every account is the same plan (combined_accounts_*).
    combined_broker_accounts: bool = False

    # --account-type); None when the caller did not say.
    account_taxable: Optional[bool] = None
    # Registered plans (lower case: 'tfsa' ...) the project's country
    # taxes like an ordinary account, set by taxjson-brokerage from
    # lib/country `plans_taxed_as_ordinary` (a US project: TFSA, RESP,
    # RDSP, FHSA; tax-logic US-BASIS-09). A broker's account type naming
    # one is a TAXABLE account to the taxable-versus-registered check
    # (backlog TJ-0613). Empty: every plan the broker names is a shelter.
    plans_taxed: frozenset = frozenset()
    # The basis fields (rules/country `parse_basis`: `stablecoins_as_cash`,
    # `plans_taxed`) the parse's rows depend on, its basis inputs. The
    # conformance check (parsers.conformance `country_findings`) parses the
    # fixture under every basis: the rows and counts may differ only in a
    # field named here (Kraken and Coinbase: the stablecoin model).
    # `plans_taxed` decides the taxable-versus-registered verdict alone (a
    # message, or a refusal of an account's mix), never a row: no parser
    # names it.
    BASIS_INPUTS: Tuple[str, ...] = ()
    # The caller keeps the TRANSFER rows aside and decides what an
    # incoming one costs (`tjs run`, lib/transfer_in: covered by a
    # .tt line, the broker's stated book value, or no cost — one
    # ATTENTION there): a parser's own transfer-in caveat stays quiet.
    transfer_costs_checked_downstream: bool = False

    def law(self, canada: str, usa: str, neutral: str = "") -> str:
        """The wording for the project's country: `canada` / `usa`, or
        `neutral` (no statute) when the country is unknown."""
        if self.country == "canada":
            return canada
        if self.country == "usa":
            return usa
        return neutral

    def configure(self, settings, *, account_context=None) -> None:
        """What this parse is told about the project (parsers.settings.
        ParseSettings): each field set as the attribute of the same name
        (a parser reads the ones it needs), and the account context the
        caller prepared from every file of the account, when there is
        one. A parser that decides something of its own from them
        overrides this and calls it first (IB: its deferred messages)."""
        self._settings = settings
        for name in settings.names():
            setattr(self, name, getattr(settings, name))
        if account_context is not None:
            self.account_context = account_context
    # Whether the label is a taxable account (taxjson-brokerage

    # What the tools read of a parse beyond its rows and counts (the account
    # phase, TJ-2321), declared so a tool reads them plainly: the broker a
    # generic mapping names, and why a file books nothing when the parser
    # knows (Kraken, RBC). The lint findings and the rows' instants are the
    # instance's (`__init__`).
    broker_name: Optional[str] = None
    zero_tx_reason: Optional[str] = None
    # The context `prepare_files` built from all of the account's files,
    # which `configure` hands each parse (None: the parser needs none).
    account_context: Any = None
    # The finder of an export's siblings: the other CSVs in its folder that
    # `tjs run` routes to the same parser, by the run's own detection
    # (lib/brokerages/detect `same_broker_siblings`: `(path, broker,
    # by_name=...)`). The caller sets it on the instance (taxjson-brokerage,
    # the conformance check; a function set on the class would bind as a
    # method), not through `configure`: the conformance check's unconfigured
    # parse has no settings to configure. None, a bare parse: the file alone. A
    # parser asks `_same_broker_siblings` and never imports the detector,
    # which asks every parser for its header (step 8 of the plan).
    sibling_finder: Optional[Callable[..., List[Path]]] = None
    # What `configure` was given, so a parse that reads a sibling export
    # configures the sibling the same way (Webull's `_sibling`, TJ-2416).
    _settings: Any = None

    def _same_broker_siblings(self, path: Path, broker: str,
                              by_name: Optional[Callable[[str], bool]] = None
                              ) -> List[Path]:
        """The siblings `sibling_finder` names for `path` (`by_name`: the
        parser's name test, for a folder of more CSVs than the finder
        detects), none on a bare parse."""
        if self.sibling_finder is None:
            return []
        return list(self.sibling_finder(Path(path), broker, by_name=by_name))

    def __init__(self) -> None:
        # Skipped-row accounting. A row the parser cannot classify must be
        # COUNTED, never silently dropped — the 0-transactions safety net
        # only catches total parser failure, not partial drop (a new
        # broker action code losing rows with zero signal is exactly how
        # taxable events go missing). `_rows_seen`/`_rows_consumed` stay
        # None/0 for parsers that don't do row-level accounting; the ones
        # that do let `taxjson-brokerage --lint` reconcile
        # rows_seen == consumed + skipped exactly.
        self._skip_counts: Dict[str, int] = {}
        # The expiries `build` keeps for the template's settlement clamp.
        self._expiries: List[Dict[str, Any]] = []
        self._rows_seen: Optional[int] = None
        self._rows_consumed: int = 0
        # The rows counted, consumed or skipped: the template's guard reads
        # it across `build` (a consumed move leaves it alone).
        self._tally: int = 0
        # The findings `taxjson-brokerage --lint` fails on (each already
        # warned on stderr), and the rows' instants by export line, which
        # order rows of one second across exports (TJ-1227; the crypto
        # parsers fill it).
        self.lint_findings: List[str] = []
        self.instant_of_line: Dict[int, Any] = {}
        # The expiry rows this parser's files kept (`clamp_settlement_to_expiry`),
        # which the tool's clamp over all of an account's files reads.
        self.expiry_rows: List[Dict[str, Any]] = []
        # The rows the file's cash events are built from (`note_cash`), and
        # the events the parser's finish builds from them: what
        # taxjson-brokerage --cash-out writes. None for a parser that builds
        # none (the registry's `builds_cash`: Webull, the generic importer).
        # `cash_problem`: why the finish could not build them
        # (`_cash_events_from`), "" when it did.
        self._cash_rows: List[Any] = []
        self.cash_events: Optional[List[Dict[str, Any]]] = None
        self.cash_problem: str = ""
        # The symbol spellings the parse read ({SYMBOL: {spelling:
        # rows}}, lib/symbol_case.record): what taxjson-brokerage writes
        # for `tjs run`'s check of two spellings of one symbol.
        self.symbol_spellings: Dict[str, Dict[str, int]] = {}

    def row_counts(self) -> RowCounts:
        """What the parse counted (`parsers.protocol.RowCounts`): the one
        reading of the counters the tools and the conformance kit make."""
        return RowCounts(self._rows_seen, self._rows_consumed,
                         sum(self._skip_counts.values()))

    def count_skip(self, category: str) -> None:
        """A row dropped unclassified, outside an Outcome (a row `read`
        drops, a parser off the template): the bare class, listed in the
        summary as unclassified. A label that starts with a class's prefix
        is refused, as for an Outcome (TJ-2420); `count_nonevent` is the
        non-event class."""
        self._count(Outcome.skip(category))

    def note_row_consumed(self) -> None:
        self._count(_CONSUMED)

    def note_cash(self, row: Any) -> None:
        """A row the export's cash events are read from (a deposit, a
        withdrawal, a conversion's leg, a balance line), kept in the order
        noted for the parser's finish, which builds the file's
        `cash_events` from them (pairing what pairs: a conversion's legs).
        `row`: what the finish reads, the parser's row object or a tuple of
        it and what the parser knows of it (a section). It counts nothing:
        the row's Outcome counts it. From classify, or from read for a row
        it drops (RBC's copies of another download's rows) or reads as the
        export holds it (IB's, before its temporary symbols are folded)."""
        self._cash_rows.append(row)

    def _cash_events_from(self, build: Callable[[], List[Dict[str, Any]]]) -> None:
        """The file's `cash_events` from the finish's `build` over the rows
        noted, or, when it raises, none and `cash_problem` (the error as
        `type: message`, as the cash readers worded theirs until PLAN step 16
        moved them into the parsers): a cash cell the parse itself never
        reads (a Deposit's quantity, a stablecoin Buy's Total) is the file's
        problem in the --cash-out sidecar, never a refused parse and never an
        empty list read as no cash (PLAN step 16 commit 8, the readers'
        rule)."""
        try:
            self.cash_events, self.cash_problem = build(), ""
        except Exception as e:                      # noqa: BLE001
            self.cash_events, self.cash_problem = [], f"{type(e).__name__}: {e}"

    def _count(self, outcome: Outcome) -> None:
        """The one writer of a counted row: the consumed count or the
        skip counter of its kind's class, and the tally."""
        self._shift(self._counter_of(outcome), 1)
        self._tally += 1

    def _counter_of(self, outcome: Outcome) -> Optional[str]:
        """The counter an Outcome's rows are in: None the consumed count,
        else the skip counter's key (`_counter_key`)."""
        return None if outcome.kind == "consumed" else self._counter_key(outcome)

    def _shift(self, counter: Optional[str], n: int) -> None:
        """`n` rows more (fewer, for a negative `n`) in `counter`."""
        if counter is None:
            self._rows_consumed += n
            return
        left = self._skip_counts.get(counter, 0) + n
        if left:
            self._skip_counts[counter] = left
        else:
            self._skip_counts.pop(counter, None)

    def note_row_line(self, tx: Dict[str, Any], line: Optional[int]) -> None:
        """Remember the export line a parsed row dict came from, for a
        message about the row after the parse (the schema check names
        `FILE line N`, not a masked content id: backlog TJ-0124). The
        first line given wins; the row is held so its id() is never
        another row's."""
        if line:
            self.__dict__.setdefault('_row_lines', {}).setdefault(
                id(tx), (tx, line))

    def row_line(self, tx: Dict[str, Any]) -> Optional[int]:
        """The export line note_row_line gave for `tx`, or None."""
        hit = self.__dict__.get('_row_lines', {}).get(id(tx))
        return hit[1] if hit is not None and hit[0] is tx else None

    def rows_noting_lines(self, transactions: List[Dict[str, Any]],
                          items, line_of):
        """Iterate a parser's rows (`items`), noting every transaction
        appended to `transactions` while an item is handled as made from
        that item's export line (`line_of(item)`; note_row_line) — a
        `continue` or `break` in the loop included. Rows the parser
        builds after the loop (pairing, grouping) keep no line. The
        IB, RBC, Webull, Coinbase and Kraken loops gave none, so the
        schema check named a masked row id (backlog TJ-0124).

        A row may remove rows built before it (an IB `Ca` undoes its
        original) as well as append: the rows noted are those the loop has
        not seen from the list's old length to its end, then below the old
        length while they are new. An index slice alone missed one appended
        row per row removed (TJ-2481); a walk from the end alone missed a
        new row before a seen one (TJ-2491). A row inserted below the old
        length under a seen one keeps no line. Every row seen is held until
        the loop ends, so a removed row's id() is never a new row's."""
        held = list(transactions)
        seen = {id(t) for t in held}
        start, line = len(transactions), None

        def note_new() -> None:
            i = len(transactions) - 1
            while i >= 0 and (i >= start or id(transactions[i]) not in seen):
                t = transactions[i]
                if id(t) not in seen:
                    seen.add(id(t))
                    held.append(t)
                    self.note_row_line(t, line)
                i -= 1

        try:
            for item in items:
                note_new()
                start, line = len(transactions), line_of(item)
                yield item
        finally:
            note_new()

    # Skip-category prefix for rows the parser RECOGNIZES as non-events
    # (a fiat deposit, an FX conversion, a statement-metadata section).
    # They still count toward the lint reconciliation, but the summary
    # lists them in their own quieter note instead of the "if any of
    # these are trades/income, the parser needs a new branch" call to
    # action — that wording is for rows the parser could NOT classify.
    # The skip labels' classes are parsers.protocol's prefixes, written by
    # `_counter_key` and read by `skip_classes`: the summary and the tool read
    # the classes there.

    def _counter_key(self, outcome: Outcome) -> str:
        """The skip counter's key of a skipped row's Outcome: its label in
        its kind's class (the one encoding; `skip_classes` the decoding)."""
        if outcome.kind not in _SKIP_PREFIX:
            raise TypeError(f"an Outcome of kind {outcome.kind!r} is not a "
                            f"skipped row's (parsers.protocol)")
        if outcome.kind == "skip" and outcome.label.startswith(
                (KNOWN_NONEVENT_PREFIX, REPORTED_PREFIX)):
            # The label would be read back in that class (TJ-2414): a bare
            # skip says its class through its kind, never its label.
            raise TypeError(f"the skip label {outcome.label!r} starts with a class's "
                            f"prefix: answer Outcome.nonevent or Outcome.reported")
        return _SKIP_PREFIX[outcome.kind] + outcome.label

    def recount(self, now: Outcome, n: int = 1, *, was: Outcome = _CONSUMED) -> None:
        """`n` rows counted as `was` (consumed unless given) are `now` after
        all, the one move of the counts: rows the loop consumed into the
        parser's state that the finish finds are non-events, skips or
        reported rows (Kraken's instant-trade legs another export books, an
        orphan leg), or skipped rows a later pass pairs (`now` consumed).
        The rows seen and the tally hold, so the lint reconciliation (seen
        = consumed + skipped) and the template's guard do too. Moving more
        rows than `was` holds is a parser bug (rows another file read
        counted here), refused as the template refuses a wrong Outcome."""
        if n < 0:
            raise TypeError(f"recount of {n} row(s): a parser bug")
        if now.txs or was.txs:
            # Rows reach the books through the parse's answer; a move of the
            # counts would drop them (TJ-2413).
            raise TypeError("recount moves counts: its Outcomes carry no rows "
                            "(book them through build or finish)")
        # Each side's counter, a kind the counters keep checked before any move.
        source, target = self._counter_of(was), self._counter_of(now)
        held = (self._rows_consumed if source is None
                else self._skip_counts.get(source, 0))
        if n > held:
            raise TypeError(f"{n} row(s) moved from {was.kind} {was.label!r}, {held} "
                            f"counted so: a parser bug (rows another file read?)")
        self._shift(source, -n)
        self._shift(target, n)

    def skip_classes(self):
        """The skip counts by class: (recognized non-events, reported, the
        rest), each a dict of the label without its prefix to its count.
        The one reading of the label convention (the summary, the tool's
        quiet path, a parser's zero-transactions reason read it here)."""
        known: Dict[str, int] = {}
        reported: Dict[str, int] = {}
        rest: Dict[str, int] = {}
        for label, n in self._skip_counts.items():
            if label.startswith(KNOWN_NONEVENT_PREFIX):
                known[label[len(KNOWN_NONEVENT_PREFIX):]] = n
            elif label.startswith(REPORTED_PREFIX):
                reported[label[len(REPORTED_PREFIX):]] = n
            else:
                rest[label] = n
        return known, reported, rest

    def count_nonevent(self, category: str) -> None:
        """count_skip for a row the parser RECOGNIZES as a non-event
        (subtotal, FX conversion, fiat deposit, metadata). Still part
        of the lint reconciliation; reported in the calmer note."""
        self._count(Outcome.nonevent(category))

    def emit_skip_summary(self, source_name: str) -> None:
        """One stderr note per parse listing what was dropped and why.
        Quiet when nothing was skipped. Recognized non-events (see
        KNOWN_NONEVENT_PREFIX) get a separate, calmer line; rows the
        parser's own note reports (REPORTED_PREFIX) are counted and not
        listed again."""
        if not self._skip_counts:
            return
        known, _reported, unknown = self.skip_classes()
        if known:
            total = sum(known.values())
            detail = ", ".join(f"{cat}: {n}" for cat, n in
                               sorted(known.items()))
            emit_line(f"note: {source_name}: {total} recognized non-event "
                  f"row(s) not translated — {detail}.")
        if unknown:
            total = sum(unknown.values())
            detail = ", ".join(f"{cat}: {n}" for cat, n in
                               sorted(unknown.items()))
            emit_line(f"note: {source_name}: skipped {total} unclassified "
                  f"row(s) — {detail}. If any of these are trades/income, "
                  f"the parser needs a new branch (see CONTRIBUTING).")

    # ------------------------------------------------ the parse, phase by phase
    # (parsers.protocol): every parser defines read, classify, build and
    # finish, and none overrides parse_file (Interactive Brokers was the
    # last, step 5h (8) of PLAN.md).

    def read(self, path: Path) -> list:
        """The rows of one export, after the parser's own preparation
        (its account context, a pre-pass over paired rows)."""
        raise NotImplementedError

    def classify(self, row) -> Outcome:
        """What the template does with the row (parsers.protocol.Outcome):
        build, by default."""
        return Outcome.build()

    def build(self, row) -> "List[Dict[str, Any]] | Outcome":
        """The schema rows of one consumed row; or, when the parser finds
        building that the row is a skip or a non-event (the generic
        importer's 1-for-1 split), that Outcome, which the template counts
        as it would classify's."""
        raise NotImplementedError

    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """The parser's pairings and notes after the loop; the rows it
        returns are the parse."""
        return transactions

    def new_transactions(self) -> List[Dict[str, Any]]:
        """The list the parse builds its rows into: a new one by default. A
        parser whose row changes rows built before it (an Interactive Brokers
        `Ca` cancellation undoes its original's) hands its statement's own, so
        the template's line notes and its undo work on one list (5h (8d)). It
        is asked once `read` has returned, so a parser that overrides it reads
        its rows whole (a list or another sequence, as IB's `read` does): a
        generator `read` is refused, since its state would not be set up yet
        (TJ-2482, TJ-2490)."""
        return []

    def row_line_of(self, row) -> Optional[int]:
        """The export line a row came from, for the messages
        (rows_noting_lines): the row's `line` attribute (the protocol's
        row contract; a parser whose rows keep it elsewhere overrides
        this). TJ-0124: a message names `FILE line N`, never a masked id."""
        return getattr(row, "line", None)

    def after_marks(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The parser's step after the split-fill marks and before the skip
        summary: what must see the marks, or must not be seen by them (a
        costing the loop did after them, so the ids stay as they were).
        Not the order a newest-first export is read in: that is `finish`'s,
        before the marks, so two exports listing a day in opposite orders
        mark the same row (TJ-5038). Nothing by default; an override says
        what it does and why it belongs here."""
        return transactions

    def release(self) -> None:
        """What the parser kept for the parse goes (RBC's `_rbc`, the
        generic importer's `_gen`). The template calls it whatever
        happened, a refused file included, so a long-lived instance holds
        no export; nothing by default."""

    def parse_rows(self, path: Path) -> List[Dict[str, Any]]:
        """The parse up to the parser's finish: read, then per row classify
        (the template counting the row as seen and a skip or a non-event,
        consuming the rest) and build, then the parser's finish. What a
        parser needs of another file for its hints (Webull's sibling
        exports) without the file's closing steps; `parse_file` runs them
        over this. The expiries `build` kept are on `_expiries` after it,
        and what the parser kept stays until the caller's `release`."""
        self._expiries = []             # this file's alone, whatever a failed parse left
        self._cash_rows = []            # (the same)
        # (the same: a refused file has no events, not the last file's)
        self.cash_events, self.cash_problem = None, ""
        # The lint reconciliation's count: every row the template iterates,
        # after what read adds for rows it drops and counts (footers,
        # duplicates). A read that finds the file is not its export sets
        # it to None (no accounting; the tool warns) and returns no rows.
        self._rows_seen = 0
        rows = self.read(path)
        if (getattr(self.new_transactions, "__func__", None)
                is not BaseBrokerage.new_transactions
                and not isinstance(rows, Sequence)):
            raise TypeError("a parser that hands its own list (new_transactions) "
                            "reads its rows whole: read returns a list, not a "
                            "generator (parsers.protocol; TJ-2482, TJ-2490)")
        transactions = self.new_transactions()
        for row in self.rows_noting_lines(transactions, rows, self.row_line_of):
            self._rows_seen += 1
            outcome = self.classify(row)
            if not isinstance(outcome, Outcome):    # a parser bug, not the file's
                raise TypeError(f"classify returned {outcome!r}, not an "
                                f"Outcome (parsers.protocol)")
            if outcome.kind == "build":
                tally = self._tally
                built = self.build(row)
                if self._tally != tally:
                    # The template counts the row from build's answer; a build
                    # that counts it too counts it twice (TJ-2374, TJ-2391).
                    raise TypeError("build counted the row itself (count_skip, "
                                    "count_nonevent or note_row_consumed): answer "
                                    "an Outcome (parsers.protocol) and the "
                                    "template counts it")
                if not isinstance(built, Outcome):
                    self.note_row_consumed()
                    transactions.extend(built)
                    continue
                outcome = built     # a skip or a non-event found building
            if outcome.kind in _SKIP_PREFIX:
                self._count(outcome)
            elif outcome.kind == "consumed":
                self.note_row_consumed()
                transactions.extend(outcome.txs)
            else:
                raise TypeError(f"build answered {outcome.kind!r}; it "
                                f"builds, skips or finds a non-event")
        return self.finish(transactions, path)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        """The template: `parse_rows`, then the closing steps every parser
        shares: the settlement clamp on the expiries `build` kept
        (`_expiries`), the split fills, the parser's step after them
        (`after_marks`), the skip summary; what the parser kept released
        (`release`) whatever happened. The rows are the export's neutral
        facts: a row carrying ISSUER_ROC_KEY waits on the characterize step
        (`taxjson.characterize.characterize_rows`, which taxjson-brokerage
        runs before anything reads them). Every parser's rows pass here,
        so their symbols are upper-cased here (`upper_case_symbols`), the
        spellings read kept in `symbol_spellings`."""
        path = Path(path)
        try:
            transactions = self.parse_rows(path)
            self.symbol_spellings = {}
            upper_case_symbols(transactions, _TICKER_CASE,
                               self.symbol_spellings, self._extract_gave)
            upper_case_symbols(self._expiries, _TICKER_CASE)
            self.clamp_settlement_to_expiry(transactions, self._expiries)
            # Parser-level disambiguation so a downstream `taxjson-sort
            # --dedup` can't collapse byte-identical split-fill rows; the
            # marks follow the rows in the order the parser outputs them
            # (a newest-first export already put oldest first), so the
            # first of identical rows keeps its stable id.
            # A row with its own id (an exchange's transaction id) needs
            # no fill mark: the rows without one alone, as Coinbase marked
            # them (Kraken's own marker goes with its conversion).
            self.disambiguate_split_fills([t for t in transactions if not t.get('id')])
            transactions = self.after_marks(transactions)
        finally:
            self._expiries, self._cash_rows = [], []
            self.release()
        self.emit_skip_summary(shown_name(path))
        return transactions

    # The account phase (TJ-2321, recommendation 6): what taxjson-brokerage
    # runs once over all of an account's files around the per-file parses.
    # Every parser has the three stages; one with no account-level context
    # keeps the defaults.

    @classmethod
    def prepare_files(cls, paths, *, tax_year: Optional[int] = None,
                      combined: bool = False, taxable: Optional[bool] = None,
                      symbol_codes=None, project=None):
        """Before any file is parsed: the context every parse of the
        account shares (handed to `configure` as `account_context`), read
        from all of `paths`; None, the default, when the parser needs none.
        The keywords are the account's: the tax year, a combined label,
        the account's tax status, the symbol codes file, the project's
        statements; a parser reads the ones it needs."""
        return None

    @staticmethod
    def coverage_messages(context, year: int, *, today=None,
                          country: Optional[str] = None,
                          year_end_posting: Optional[str] = None) -> List[str]:
        """The account's statements against the tax year, from the
        prepared context: the lines to print (RBC's "as of" stamps); none
        by default."""
        return []

    @classmethod
    def reconcile_files(cls, parsed) -> None:
        """After every file is parsed (`parsed`: (path, parser, rows) per
        file): what one file's rows change in another's (Interactive
        Brokers' cross-statement pass); nothing by default."""

    def statement_accounts(self) -> set:
        """The broker account ids the last parsed file covers, when the
        export names them (an IB statement's Account Information);
        empty when it does not. taxjson-brokerage records them HASHED
        per file, so cross-file dedup can tell statements of two
        different broker accounts apart (audit R1-296)."""
        return set()

    # ---------------------------------------------------------------- options

    # The two regex shapes the brokerages produce:
    #   "CALL SAMPLG 06/20/25 150.00"           — Questrade/Webull/RBC variant
    #   "ASN - CALL .QQZ 06/20/25 30 QQZ HOLDINGS"   — RBC option-leg notification
    #   "ASSIGNMENT OF OPTION ... CALL ..."   — RBC stock-leg notification
    # The base regex captures the common skeleton; subclasses can add their
    # own variants by overriding _option_description_patterns.
    _BASE_OPTION_PATTERNS = (
        re.compile(
            r'^(?:EXP\s*-\s*|ASN\s*-\s*)?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+' + OPTION_STRIKE_RE,
            re.IGNORECASE,
        ),
        re.compile(
            r'ASSIGNMENT OF OPTION.*?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+' + OPTION_STRIKE_RE,
            re.IGNORECASE,
        ),
    )

    def _option_description_patterns(self) -> Tuple[re.Pattern, ...]:
        return self._BASE_OPTION_PATTERNS

    def parse_option_from_description(self, desc: str) -> Optional[Dict[str, str]]:
        """Extract (right, base_ticker, expiry, strike) from a brokerage's
        free-text option description. Returns None if nothing looks like an
        option string."""
        if not desc:
            return None
        for pat in self._option_description_patterns():
            m = pat.search(desc)
            if m:
                return option_parts(*m.groups())
        return None

    def _where_name(self) -> str:
        """How this parser names its file in a message: the one being
        parsed (`_fname`), '' when none is; a parser that keeps the name
        elsewhere overrides it."""
        return getattr(self, '_fname', '') or ''

    def strict_option_from_description(self, desc: str):
        """The base class's parse_option_from_description (a parser that
        overrides it calls this from its override), whose shared strike
        reader refuses a strike it can only partly read ('2,50',
        '1,0000'; audit A2-0632 / re-audit A2-1041) — re-raised naming
        the file (`_where_name`) and the description."""
        try:
            return BaseBrokerage.parse_option_from_description(self, desc)
        except BrokerageParseError as e:
            raise BrokerageParseError(
                f"{self._where_name()}: option description "
                f"{(desc or '')[:70]!r}: {e}") from None

    def format_occ_symbol(self, right: str, base: str, expiry: str,
                          strike: str, where: Optional[str] = None) -> str:
        """Build an OCC-style option symbol: BASE{YYMMDD}{C/P}{strike*1000:08d}.

        Accepts the expiry as "MM/DD/YY" (two-digit year). Strike may be a
        float-as-string. An expiry that is no calendar date (02/30/25
        became SYN250230C...) or a strike the OCC field cannot hold is
        refused, naming `where` (the row) when given (backlog TJ-0519,
        TJ-0520)."""
        at = f"{where}: " if where else ""
        what = (f"option {'CALL' if right == 'C' else 'PUT'} {base} "
                f"{expiry} {strike}")
        try:
            m, d, y = expiry.split('/')
            datetime(2000 + int(y), int(m), int(d))
        except ValueError:
            raise BrokerageParseError(
                f"{at}{what}: the expiry {expiry} is not a calendar date "
                f"(MM/DD/YY) — refusing to book a contract that cannot "
                f"exist. Correct the row from the broker's statement, or "
                f"book it in a .tt file.") from None
        expiry_date = f"{y}{int(m):02d}{int(d):02d}"
        try:
            occ_strike = encode_occ_strike(strike)
        except BrokerageParseError as e:
            raise BrokerageParseError(f"{at}{what}: {e}") from None
        return f"{base}{expiry_date}{right}{occ_strike}"

    # ----------------------------------------------------------- currency ext

    # The suffixes CURRENCY_EXT_MAP appends (a currency's listing).
    _CURRENCY_SUFFIX_RE = _sfx_re(set(_cur_sfx().values()))

    def apply_currency_suffix(self, symbol: str, currency: str) -> str:
        """Normalize a symbol and append a currency-derived suffix.

        Strips any existing .TO/.US/.AX/.L, replaces spaces with dots, then
        appends the suffix from CURRENCY_EXT_MAP. Unknown currencies fall
        back to CURRENCY_EXT_FALLBACK if set, otherwise echo the currency
        code itself."""
        if not symbol:
            return symbol
        sym = symbol.replace(' ', '.')
        # A Canadian venue suffix (Questrade's .VN / .CN / .NE, a .V)
        # is one Canadian listing: canonical ROOT.TO, the spelling every
        # other parser emits (see canonical_ca_listing). It used to give
        # ABC.V, CCC.CN.TO and SAMPLE.NE.TO -- three identities IB, RBC and
        # Webull never produce, so pools split across brokers and a
        # cross-account superficial loss was missed (audit S010-05). A
        # .TO listing traded in USD (SAMPLF.U.TO) keeps the currency rule
        # below.
        if not sym.upper().endswith('.TO'):
            ca = canonical_ca_listing(sym, currency)
            if ca is not None:
                return ca
        # A currency code is case-blind: 'usd' made the suffix '.usd'
        # and split the pool from SAMPLE.US (audit S055-17).
        currency = (currency or '').strip().upper()
        ext = self.CURRENCY_EXT_MAP.get(currency, self.CURRENCY_EXT_FALLBACK or currency)
        # (A series letter that IS the row currency's suffix, SYN.PF.L in
        # GBP, is that listing: backlog TJ-1202.)
        if (not is_ca_pref_series(sym)
                or sym.upper().endswith(f'.{ext}'.upper())):
            sym = self._CURRENCY_SUFFIX_RE.sub('', sym)
        if ext == 'TO':
            # FTN.PRA -> FTN.PR.A (audit S014-07).
            sym = canonical_ca_root(sym)
        return f"{sym}.{ext}"

    def tx_roc_adjust(self, *, symbol: str, currency: str, date: str,
                      desc: str, amount: float,
                      account: Optional[str] = None,
                      no_cash: str = '') -> Dict[str, Any]:
        """`roc_adjust_row`, the account defaulting to the parser's."""
        return roc_adjust_row(
            symbol=symbol, currency=currency, date=date, desc=desc,
            amount=amount,
            account=account or getattr(self, 'DEFAULT_ACCOUNT', 'PORTFOLIO'),
            no_cash=no_cash)

    # ----------------------------------------------------------- sign / coerce

    @staticmethod
    def signed_quantity(qty: float, action_is_sell: bool) -> float:
        """Convention: buys positive, sells negative. The cost-basis tracker
        keys off this sign — flipping it silently corrupts gain/loss."""
        return -abs(qty) if action_is_sell else abs(qty)

    @staticmethod
    def clean_number(raw: str, default: float = 0.0) -> float:
        """LEGACY lenient CSV numeric, for OPTIONAL cells. Strips commas
        and currency symbols. Accounting parentheses are NEGATIVE
        (`(1,234.56)` == -1234.56) — the old magnitude reading silently
        flipped the sign of any parenthesized amount whose parser did
        not abs() it; a caller that wants a magnitude (Webull's buy
        proceeds) takes abs() itself. The unicode minus is a minus.

        Returns `default` for a blank cell and, with a stderr warning,
        for unparseable text. A DECIMAL comma (`0,95`, `1.234,56`) raises
        BrokerageParseError: stripping it read the value 100x too large
        (audit R1-93); a thousands comma (`1,234.56`) is fine.
        REQUIRED money/quantity cells must use
        `parse_strict_number` instead: a garbage-to-0 read of a
        required field is how a missing column can inflate a return
        by thousands."""
        if raw is None or raw == '':
            return default
        s = str(raw).strip()
        if not s:
            return default
        for ch in MINUS_CHARS:
            s = s.replace(ch, '-')
        neg = s.startswith('(') and s.endswith(')')
        if neg:
            s = s[1:-1]
        s = s.replace('$', '').replace('€', '').replace('£', '').strip()
        check_comma_grouping(s, raw)
        s = s.replace(',', '')
        try:
            v = float(s)
        except ValueError:
            emit_line(f"warning: numeric cell {raw!r} is not a number — read "
                  f"as {default!r}")
            return default
        return -v if neg else v

    # Strict counterpart for REQUIRED cells (see module-level helper).
    parse_strict_number = staticmethod(parse_strict_number)

    @staticmethod
    def require_columns(header_map: Dict[str, int], required, *,
                        section: str, where: str = '') -> None:
        """Raise BrokerageParseError naming the section and every
        missing column when a required one is absent — the parser must
        not fall back to 0 / a default for money, quantity, price, date
        or currency columns. `required` items may be a tuple of
        alternatives (any one present satisfies it)."""
        missing = []
        gone = set()
        for col in required:
            alts = col if isinstance(col, tuple) else (col,)
            if not any(a in header_map for a in alts):
                missing.append(' or '.join(repr(a) for a in alts))
                gone.update(alts)
        if missing:
            loc = f"{where}: " if where else ''
            # The reason fits the columns missing: a Code column was
            # refused as "a missing money column" (backlog TJ-0053).
            why = []
            if gone & _MONEY_COLUMNS:
                why.append("a missing money column read as 0 corrupts "
                           "the return")
            if 'Code' in gone:
                why.append("without the Code column a cancelled row "
                           "(code Ca) reads as a new event")
            if gone - _MONEY_COLUMNS - {'Code'}:
                why.append("a row's date, security, currency or kind "
                           "cannot be read without it")
            raise BrokerageParseError(
                f"{loc}section {section!r} is missing required "
                f"column(s) {', '.join(missing)} — refusing to guess "
                f"({'; '.join(why)}). Re-export the statement in the "
                f"standard English layout.")

    # ------------------------------------------------------- fee back-compute

    def option_row_multiplier(self, symbol: str, fits) -> tuple:
        """(premium multiplier, contract_size_basis) of an option row
        whose export does not state its contract size. `fits(m)` tells
        whether the row's own money (gross, or net within the
        commission band) is |qty| x price x m. The standard 100 is
        tried first ("assumed": the money fits it, the deliverable is
        not stated); only when it cannot fit and a mini's 10 does is
        the row a mini ("derived", noted once per symbol). When neither
        fits, 100 "assumed" — the parser's own money check refuses the
        row as before."""
        std = float(self.OPTION_MULTIPLIER)
        if fits(std):
            return std, 'assumed'
        mini = float(self.MINI_OPTION_MULTIPLIER)
        if fits(mini):
            from taxjson.lib.markets import note_builtin
            note_builtin(
                "mult-derived", str(symbol),
                f"{symbol}: the row's amount is quantity x price x "
                f"{mini:g}, not x {std:g} — booked as a {mini:g}-share "
                f"(mini) contract; if that is wrong add `MULT ROOT N` "
                f"to ticker.map (ROOT: the option's root).")
            return mini, 'derived'
        return std, 'assumed'

    def back_compute_fee(
        self, qty: float, price: float, net_amount: float, is_option: bool,
        *, min_fee: float = 0.005, sanity_ratio: float = 0.25,
        multiplier: Optional[float] = None,
    ) -> float:
        """For brokerages whose CSV doesn't break out fees, infer the fee
        from the gross (qty * price * multiplier) and the net, SIGNED by
        the trade's direction (quantity sign): a buy's fee is what the
        net costs beyond the gross, a sale's what it falls short of it.

        Display only (fees.rpt and the .sum fee lines; gains use the
        net). Guards: a residual under `min_fee` — or on the wrong side
        (a buy that cost LESS than qty x a rounded price) — is rounding
        noise, not a fee: 0, never sign-flipped into a charge by abs()
        (audit R1-22). A residual beyond `sanity_ratio` of |net| PLUS a
        commission allowance (10 + 2 per option contract) is a units
        artifact (a wrong contract multiplier) and becomes 0; the ratio
        alone zeroed real flat commissions on cheap option fills (a
        1.99 fee on a 6.01 sale, RBC's 11.95 on a 43.95 buy — audit
        R1-22 / R1-88)."""
        if multiplier is None:
            multiplier = self.OPTION_MULTIPLIER if is_option else 1
        theoretical_gross = abs(qty) * price * multiplier
        if qty > 0:
            implicit = abs(net_amount) - theoretical_gross
        elif qty < 0:
            implicit = theoretical_gross - net_amount
        else:
            implicit = abs(theoretical_gross - abs(net_amount))
        if implicit < min_fee:
            return 0.0
        # net == 0 is exempt on purpose: an RBC sale of a few contracts
        # at 0.01 can net $0 when the commission eats the whole gross.
        # A $0 net that is a MISSING cell (audit R1-91) must be refused
        # by the parser before it gets here (Webull does).
        allowance = 10.0 + (2.0 * abs(qty) if is_option else 0.0)
        if (abs(net_amount) > 1e-9
                and implicit > sanity_ratio * abs(net_amount) + allowance):
            return 0.0
        return M.units(implicit, 4)

    def theoretical_gross(self, qty: float, price: float, is_option: bool,
                          multiplier: Optional[float] = None) -> float:
        if multiplier is None:
            multiplier = self.OPTION_MULTIPLIER if is_option else 1
        return M.units(abs(qty) * price * multiplier, 4)

    # -------------------------------------------------------- date / settle

    @staticmethod
    def parse_date(raw: str, *formats: str) -> Optional[datetime]:
        """Try each format in order; return the first that parses. None if
        none match. Strips surrounding whitespace."""
        if not raw:
            return None
        s = raw.strip()
        for fmt in formats:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def newest_first(dates: List[Any]) -> bool:
        """True when an export lists its rows NEWEST FIRST: the row dates
        (file order; None/blank skipped) never increase and there are at
        least two distinct ones. A one-date file, or one whose dates go
        both ways, reads as oldest first (file order kept).

        Rows at one moment (Webull, Questrade and the generic importer
        print no clock time) are taken in the order a parser emits them
        (tax-logic CA-DATE-14 / US-DATE-13; every later stage sorts
        stably), so a parser reads a newest-first export bottom-up —
        otherwise a same-day sell-then-rebuy would replay rebuy first.
        RBC Direct does this with its own per-row times."""
        ds = [d for d in dates if d]
        return (len(set(ds)) > 1
                and all(a >= b for a, b in zip(ds, ds[1:])))

    def content_stem(self, cells, prefix: str) -> str:
        """A stable id stem for a row the broker gave no id (no ID
        column, or a blank cell — issue #93): a hash of the row's cells,
        numbered when the same row repeats in the file. Both legs of a
        swap carry it, so fill-crypto values the exchange once (re-audit
        A2-0998); an identical copy of the same export in another file
        gets the same stems, so the cross-file dedup still folds it. A
        parser resets `self._stems` at the start of each file."""
        stems = self.__dict__.setdefault('_stems', {})
        h = hashlib.sha1('\x1f'.join(str(c or '').strip() for c in cells)
                         .encode('utf-8')).hexdigest()[:16]
        n = stems.get(h, 0) + 1
        stems[h] = n
        return f"{prefix}{h}" + (f"n{n}" if n > 1 else "")

    @staticmethod
    def disambiguate_split_fills(transactions: List[Dict[str, Any]]) -> None:
        """Mark the second+ instance of identical rows in this file as
        split fills. Some brokerages emit multiple CSV rows for one order
        that gets filled in pieces at the same price and time; they're
        physically distinct trades that produce byte-identical CSV rows.
        Without this, the deterministic id() hash collapses them to one
        entry under taxjson-sort --dedup.

        Identical is every field of the row's id (`fill_identity`): rows a
        description, a currency, a settlement date, a type or a gross
        amount already tells apart keep two ids, and a mark on one of them
        made the copy an overlapping export holds (unmarked there, alone)
        a third row (TJ-5038). Called on the rows in the order the parser
        outputs them, so two exports that list one day in opposite orders
        mark the same row.

        Mutates transactions in place. Modifies only the 2nd+ occurrence
        so the first instance keeps its original (stable) id.
        """
        seen: Dict[tuple, int] = {}
        for tx in transactions:
            # A foreign issuer's return of capital (ISSUER_ROC_KEY) is no
            # split fill of an ordinary dividend that looks alike: they
            # pair among themselves alone (TJ-2594).
            key = fill_identity(tx) + (bool(tx.get(ISSUER_ROC_KEY)),)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                existing = tx.get('description') or ''
                tx['description'] = f"{existing} [fill #{seen[key]}]".strip()

    # When an expiry happened (PLAN step 14: the toolbox's now,
    # parsers/toolbox/expiry.py): the template's helpers still.
    option_expiry_booking_date = staticmethod(option_expiry_booking_date)
    non_option_expiry_booking_date = staticmethod(non_option_expiry_booking_date)

    def clamp_settlement_to_expiry(self, transactions: List[Dict[str, Any]],
                                   expiries: List[Dict[str, Any]]) -> None:
        """Clamp this file's trades (see `clamp_settlement_across`) and
        remember the expiry rows: taxjson-brokerage re-runs the clamp over
        ALL of an account's files, so a Dec-31 0DTE trade whose expiry row
        sits in the NEXT yearly export (Questrade/RBC/Webull post it the
        next business day) is clamped too (audit S055-22)."""
        self.expiry_rows = self.expiry_rows + list(expiries)
        self.clamp_settlement_across(transactions, expiries)

    clamp_settlement_across = staticmethod(clamp_settlement_across)

    @staticmethod
    def check_settle_order(date_iso: str, settle_iso: str, *,
                           where: str = '', what: str = '') -> None:
        """A broker-printed settlement date EARLIER than the trade date is
        a garbled cell, not a settlement: the tax year follows the settle
        date, so it moved the disposition into the prior year with no
        warning (audit R1-75 / S065-05). Refused (CA-DATE-03 /
        US-DATE-04)."""
        if date_iso and settle_iso and settle_iso < date_iso:
            loc = f"{where}: " if where else ''
            raise BrokerageParseError(
                f"{loc}Settlement Date {settle_iso} is before the trade "
                f"date {date_iso}{f' ({what})' if what else ''} — a "
                f"settlement never precedes its trade, and the tax year "
                f"follows the settle date; refusing to guess. Fix the "
                f"cell (or blank it for the standard cycle).")
        # A printed settle date far AFTER the trade (a typo a year out)
        # moves the disposition into a later tax year just as silently:
        # trusted, but flagged on the console (audit A2-0104). No cycle
        # with its holidays runs past 7 calendar days.
        if date_iso and settle_iso and settle_iso > date_iso:
            try:
                lag = (datetime.strptime(settle_iso[:10], '%Y-%m-%d')
                       - datetime.strptime(date_iso[:10], '%Y-%m-%d')).days
            except ValueError:
                return
            if lag > SETTLE_LAG_FLAG_DAYS:
                loc = f"{where}: " if where else ''
                emit_line(f"warning: ATTENTION: {loc}Settlement Date "
                      f"{settle_iso} is {lag} days after the trade date "
                      f"{date_iso}{f' ({what})' if what else ''} — no "
                      f"settlement cycle is that long, and the tax year "
                      f"follows the settle date. Booked as printed; check "
                      f"the cell (blank it for the standard cycle).")

    @staticmethod
    def warn_zero_cost_buy(where: str, symbol: str, qty: float,
                           price: float, net: float) -> None:
        """A share BUY at $0 price and $0 cash is almost always a
        transfer or journal row, booked with no cost (the generic
        importer refuses it). Booked as printed, flagged on the console
        (audit A2-0619)."""
        if (qty or 0) > 0 and abs(price or 0) < 1e-9 \
                and abs(net or 0) < 0.005:
            emit_line(f"warning: ATTENTION: {where}: a buy of {qty:g} "
                  f"{symbol} at ZERO cost (price and cash both 0) — "
                  f"booked with no cost basis. If it is a transfer or a "
                  f"journal, book its real cost (a .tt BUYSELL) instead.")

    def settlement_date_t1(self, date_str: str, *formats: str,
                           currency: str = 'USD') -> str:
        """Add one settlement day (holiday-aware for USD and CAD) to the
        given trade date. Used by brokerages whose CSV doesn't carry a
        settlement-date column, for options (T+1 in every era).

        formats lists strptime patterns to try; if none parse, the input
        string is returned unchanged so callers can degrade gracefully on
        malformed dates."""
        dt = self.parse_date(date_str, *formats) if formats else None
        if dt is None:
            return date_str
        from taxjson.lib.market_calendar import add_settlement_days
        return add_settlement_days(dt, 1, currency).isoformat()

    def trade_date_from_settlement(self, settle_str: str,
                                   currency: str = 'USD',
                                   is_option: bool = False,
                                   *formats: str) -> str:
        """BACK-compute the trade date from a settlement date, for brokerages
        whose CSV date column IS the settlement date (Webull, per user
        verification against real statements). Options T+1 in all eras;
        equities T+2 before the T+1 cutover (US 2024-05-28 / CA 2024-05-27),
        T+1 after (T+3 before 2017-09-05, T+5 before 1995-06-07 in USD and
        CAD: lib/dates.settlement_lag_days, tax-logic CA-DATE-04 /
        US-DATE-04). Walks back through weekends and
        the market's settlement holidays to the latest trading day that
        settles on that date (lib/market_calendar); the era is the trade
        date's, so the cutover days round-trip."""
        dt = self.parse_date(settle_str, *formats) if formats else None
        if dt is None:
            return settle_str
        from taxjson.lib.dates import (last_trade_date_settling_by,
                                       settlement_date, settlement_lag_days)
        from taxjson.lib.market_calendar import sub_settlement_days
        iso = dt.strftime("%Y-%m-%d")
        # The latest trading day that settles ON this date. The era is a
        # TRADE-date rule: keying it on the settle date gave a T+1 walk
        # back for the T+2 settles of the cutover days themselves (CAD
        # 2024-05-27, USD 2024-05-28 — audit S056-01).
        trade = last_trade_date_settling_by(iso, currency, is_option)
        if settlement_date(trade, currency, is_option) == iso:
            return trade
        days = settlement_lag_days(iso, currency, is_option)
        return sub_settlement_days(iso, days, currency).isoformat()

    def equity_settlement_date(self, date_str: str, currency: str = 'USD',
                               *formats: str) -> str:
        """Era-aware EQUITY settlement for brokerages whose CSV carries no
        settlement column: T+2 before the T+1 cutover, T+1 after (T+3
        before 2017-09-05, T+5 before 1995-06-07 in USD and CAD:
        lib/dates.settlement_lag_days, tax-logic CA-DATE-04 / US-DATE-04),
        counted in the market's settlement days
        (weekends and USD/CAD settlement holidays skipped).
        The cutover is market-specific: US 2024-05-28; Canada 2024-05-27 (a
        TSX trading day — the US was closed for Memorial Day)."""
        dt = self.parse_date(date_str, *formats) if formats else None
        if dt is None:
            return date_str
        # The era/lag rule lives in lib/dates (shared with the wash
        # radar's rescue-deadline walk-back) — one copy, no drift.
        from taxjson.lib.dates import settlement_date
        return settlement_date(dt.strftime("%Y-%m-%d"), currency)


def names_underlying(root: str, symbol: str) -> bool:
    """Does the Symbol column name the stock the option ROOT is on?
    Exactly, or by the class / adjustment spelling the engine accepts
    (option_symbols.root_matches_stock: SAMPLD for SAMPLD.B, SAMPLCB for SAMPLC.B) — the
    stock leg of an assignment on a class share was refused (re-audit
    A2-1059)."""
    root = (root or '').strip().upper()
    stock = strip_listing_suffix((symbol or '').strip().upper())
    if not root:
        return False
    if root == stock:
        return True
    # Only a listed CLASS share (SAMPLD.B, SAMPLC.B): Questrade's option rows
    # carry 'SAMPLG.OPT'-shaped symbols, which must stay the option.
    return (bool(re.fullmatch(r'[A-Z]+\.[A-Z]{1,2}', stock))
            and root_matches_stock(root, stock))

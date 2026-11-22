from taxjson.lib.stage_msg import emit_line
import csv
from dataclasses import dataclass, field as dc_field
import io
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, NamedTuple, Optional

from taxjson.parsers.protocol import Outcome, Row, builds
from taxjson.parsers.toolbox.assets import QTY_ZERO
from taxjson.parsers.toolbox.money import money_close
# A reward's value from its cells and its legs (PLAN step 14 moved them to
# the toolbox).
from taxjson.parsers.toolbox.staking import reward_value, valued_reward_legs
from taxjson.model import money as M
from taxjson.lib.brokerages.base import (BaseBrokerage, read_broker_text,
                                         shown_name, zero_units_refusal)
from taxjson.lib.brokerages._crypto_common import (FIAT_CURRENCIES,
                                                   USD_STABLECOINS, RowIds,
                                                   same_coin_hint,
                                                   order_within_seconds,
                                                   strict_money, warn_depeg)
from taxjson.lib.timezones import utc_to_local


# Coinbase transaction types we recognize. Matched on substring (case-insensitive)
# so the regex handles both retail ("Buy"/"Sell") and Coinbase Advanced
# ("Advanced Trade Buy"/"Advanced Trade Sell") which the old cb_trades.pl
# accepted via /(Buy|Sell)/i. Without this the parser silently dropped 104
# Advanced trades plus all staking-reward income.
_BUY_RE = re.compile(r'\bbuy\b', re.IGNORECASE)
_SELL_RE = re.compile(r'\bsell\b', re.IGNORECASE)
# Staking-style reward income — the income side. We also pair with a
# zero-cost BUYSELL that adds the rewarded tokens to inventory at FMV
# (cost basis = FMV at receipt; matches CRA/IRS treatment).
# Coinbase ships multiple labels for what is economically the same
# event: "Staking Income", "Rewards Income", "Reward Income" (singular),
# "Inflation Reward", "Coinbase Earn", "Learning Reward" — match the
# whole family rather than only the plural-"Rewards Income" variant.
_STAKING_RE = re.compile(
    r'\b(staking\s+income|reward(s)?\s+income|inflation\s+reward|'
    r'coinbase\s+earn|learning\s+reward|'
    # "Incentives Rewards Payout" (referral/usage incentives): same
    # income-plus-FMV-acquisition semantics — skipping it leaves the
    # rewarded coins off the books (surfaced as a phantom-short dust
    # balance when the account is later emptied).
    r'(incentives?\s+)?rewards?\s+payout)\b',
    re.IGNORECASE,
)

# Logical field → accepted header spellings (lowercased), newest first.
# Coinbase has shipped several export layouts; the older retail format
# uses "Spot Price Currency" / "Spot Price at Transaction". Resolving
# through synonyms keeps one parser for all of them — and validating
# below keeps an UNKNOWN layout from silently parsing as price=0 rows
# that `taxjson-fill-crypto` then "repairs" by overwriting the broker's
# exact totals with daily-close estimates.
_HEADER_SYNONYMS: Dict[str, tuple] = {
    'timestamp': ('timestamp',),
    'transaction type': ('transaction type',),
    'asset': ('asset',),
    'quantity transacted': ('quantity transacted',),
    'price currency': ('price currency', 'spot price currency'),
    'price at transaction': ('price at transaction',
                             'spot price at transaction'),
    'total': ('total (inclusive of fees and/or spread)',
              'total (inclusive of fees)', 'total'),
    'fees': ('fees and/or spread', 'fees'),
    'subtotal': ('subtotal',),
    'notes': ('notes',),
    'id': ('id', 'transaction id'),
}

# "Converted 0.05000000 BTC to 1.20000000 ETH" — the Notes text every
# observed retail Convert row carries. Anything that doesn't match
# still fails hard below (the field layout of an unknown Convert
# variant must not be guessed).
_CONVERT_NOTES_RE = re.compile(
    r'Converted\s+([\d,]+(?:\.\d+)?)\s+([A-Z0-9]{2,10})\s+to\s+'
    r'([\d,]+(?:\.\d+)?)\s+([A-Z0-9]{2,10})',
    re.IGNORECASE,
)
# `price currency` is optional (defaults to USD) and `id` is absent in
# older exports or blank on a row (handled via disambiguate_split_fills
# below).
_REQUIRED_FIELDS = ('timestamp', 'transaction type', 'asset',
                    'quantity transacted', 'price at transaction', 'total')


def is_header_row(cells) -> bool:
    """The row the parser takes as the header: the first one with a
    Timestamp cell (any preamble lines above it are skipped). Broker
    detection asks the same question (lib/brokerages/detect.py)."""
    return bool(cells) and 'timestamp' in [c.strip().lower() for c in cells]


def resolve_header(cells):
    """(logical field -> column index, required fields missing) for a
    header row, through _HEADER_SYNONYMS — the one definition of every
    Coinbase layout the parser reads (detection uses it too)."""
    raw_map = {c.lower().strip(): i for i, c in enumerate(cells)}
    header_map: Dict[str, int] = {}
    for field, names in _HEADER_SYNONYMS.items():
        for n in names:
            if n in raw_map:
                header_map[field] = raw_map[n]
                break
    return header_map, [f for f in _REQUIRED_FIELDS if f not in header_map]

# Transaction types the parser RECOGNIZES as non-events (lowercased):
# internal moves between the trading and staking wallets, the ETH2→ETH
# relabel, fiat funding, and the Coinbase One subscription charge.
# None is a trade or income; listing them keeps the "if any of these
# are trades/income, the parser needs a new branch" call to action for
# types the parser genuinely cannot classify.
# Coin moves between your own Coinbase wallets: a non-event in any asset.
_COIN_NONEVENT_TYPES = frozenset({
    'retail staking transfer', 'retail unstaking transfer',
    'retail eth deprecation',
})
# Cash moves: a non-event only when the asset is fiat (or a stablecoin
# booked as US-dollar cash). A fiat Withdrawal used to be UNBOOKED (a
# false 'moves coins' alarm that failed run --strict, re-audit A2-0237),
# and a COIN Deposit or a Subscription paid in a coin was dropped as a
# non-event with no warning (A2-0566) — a coin leaving for a
# subscription is a disposition.
_FIAT_NONEVENT_TYPES = frozenset({'deposit', 'withdrawal', 'subscription'})
_KNOWN_NONEVENT_TYPES = _COIN_NONEVENT_TYPES | _FIAT_NONEVENT_TYPES

# Money identity (S023-19's crypto twin, re-audit A2-0022 / A2-0080 /
# A2-0250): a row's value must fit |Quantity| x Price within Coinbase's
# spread (real retail sells sit within 1%), and a Buy/Sell Total must
# be Subtotal +/- the fee (real rows agree to the cent). A 10x or
# shifted column used to book silently under run --strict.
_QP_TOL_ABS = 0.02
_QP_TOL_REL = 0.05
_TOTAL_TOL_REL = 0.01

# USD-pegged stablecoins are treated as USD CASH — the same model the
# Kraken parser uses (it folds USDC/USDT/DAI to USD, books a USDC-quoted
# fill as a USD-quoted one, and counts USDC<->fiat trades as forex
# non-events). One model across both exchanges; before this, Coinbase
# booked "Buy USDC" as a USDC position and then never booked the USDC
# leg of an Advanced Trade on ETH-USDC ("Bought X ETH for N USDC"), so
# real exports carried a phantom USDC long forever.
#
# Strictly, CRA treats a stablecoin as a crypto-asset, so every
# USDC<->ETH trade is also a disposition of USDC. Treating it as USD
# cash is an approximation whose gain is ~0 in USD terms (USDC ~ 1.00
# USD); what it leaves out is the USD/CAD movement while the coins are
# held — exactly the FX on USD cash that the tool does not model for
# Kraken's USD balances either (KNOWN_ISSUES "Kraken fiat conversions
# are not modeled"). When USDC is bought and spent within days, at
# about the same BoC rate, that residual is small.
# The crypto side of each Advanced Trade is still booked at the CAD
# value Coinbase states for it (Subtotal / Total).
_STABLECOINS = USD_STABLECOINS

# Fiat currencies a Coinbase pair can be quoted in (or a row priced
# in): the ONE fiat list (lib/markets). Anything else in the quote slot
# of an Advanced Trade pair is a crypto-asset: trading ETH-BTC disposes
# of BTC (or acquires it), a taxable leg of its own. Erring toward
# "crypto" is the safe side — a fiat code missing here books a visible
# phantom position; a crypto coin wrongly treated as cash would drop a
# disposition silently.
_FIAT = FIAT_CURRENCIES

# Advanced Trade rows name the pair only in Notes, e.g. "Bought 3 ETH
# for 0.1 BTC on ETH-BTC at 0.0333 BTC/ETH" — Coinbase emits ONE row
# per fill (the base asset), valued in the account's fiat. The quote
# amount includes the fee on a buy and is net of it on a sell (checked
# against real ETH-USDC fills: "for Y USDC" = qty x rate x (1 + fee)).
_PAIR_RE = re.compile(r'\bon\s+([A-Z0-9]{1,15})-([A-Z0-9]{1,15})\b',
                      re.IGNORECASE)
_NUM = r'([\d,]*\.?\d+(?:[eE][-+]?\d+)?)'
_ADV_NOTES_RE = re.compile(
    r'\b(Bought|Sold)\s+' + _NUM + r'\s+([A-Z0-9]{1,15})\s+for\s+'
    + _NUM + r'\s+([A-Z0-9]{1,15})\s+on\s+([A-Z0-9]{1,15})-'
    r'([A-Z0-9]{1,15})\b', re.IGNORECASE)


def _cb_symbol(asset: str) -> str:
    """The book symbol for a Coinbase asset code: upper-cased (a
    hand-edited `sol` beside `SOL` used to open a second pool, R1-112),
    then the project's ticker.map GLOBAL lines between bare codes
    (lib/markets.crypto_alias). A staked or wrapped code is the same
    property as its coin only when the project says so — `GLOBAL ETH2
    ETH` folds Coinbase's staked-ETH wrap into ETH, so an ETH -> ETH2
    convert is a relabel (R1-110). taxjson ships no such fold (owner,
    2026-10-04); an unfolded 1:1 convert is noted with the line."""
    from taxjson.lib.markets import crypto_alias
    return crypto_alias((asset or '').strip().upper().replace(' ', '.'))


def _record_starts(reader):
    """(first line, cells) for each record of a csv.reader: line_num is
    where a record ENDS, which for a cell that swallowed later lines
    named the wrong row (re-audit A2-0584)."""
    prev = 0
    for row in reader:
        yield prev + 1, row
        prev = reader.line_num


class _CbRow(Row):
    """A record of the export as `read` yields it: its cells, the line it
    starts on and the line it ends on, its place among the file's records
    (`rec`, counting the preamble, the header and blank lines: the cash
    events' `where`, as the cash reader counted them); what `classify`
    learned for `build` (the type and what it is: a buy, a sell, a
    reward)."""
    __slots__ = ("end", "rec", "type_raw", "is_buy", "is_sell", "is_staking")

    def __init__(self, cells: List[str], line: int, end: int, rec: int):
        super().__init__(cells, line)
        self.end, self.rec, self.type_raw = end, rec, None
        self.is_buy = self.is_sell = self.is_staking = None


class _CbFacts(NamedTuple):
    """A Buy, Sell or reward row's facts (`_build_row`): what the reward
    and the trade read of them."""
    cb_id: Any
    crypto_quote: Any
    currency: Any
    date_str: Any
    fee: Any
    price: Any
    qty: Any
    symbol: Any
    time_str: Any
    total: Any
    where: Any


@dataclass
class _CbParse:
    """What `read` hands the loop and the finish of one export: the file
    and its shown name, the row ids checked for duplicates, the header
    once `_rows` finds it (and its column map), the rows of types the
    parser does not book (for the warning). Released by `release`
    whatever happened."""
    path: Path
    shown: str
    ids: RowIds
    cash_or_fiat: frozenset = frozenset()   # the fiat codes and the cash coins, once
    header: Optional[List[str]] = None
    header_map: Dict[str, int] = dc_field(default_factory=dict)
    need: int = 0                           # the last mapped column: a shorter row is cut
    row_start: Optional[int] = None         # the row being built, for `_ts`
    blank_totals: int = 0
    unbooked: List[tuple] = dc_field(default_factory=list)


class CoinbaseBrokerage(BaseBrokerage):
    _cb: Optional[_CbParse] = None      # the parse's state, None between parses

    DEFAULT_ACCOUNT = "Coinbase"
    # USD stablecoins: US-dollar CASH (True — the default, Canada's
    # stated approximation, tax-logic CA-CRYPTO-02) or PROPERTY like any
    # coin (False — a US project, US-CRYPTO-02). taxjson-brokerage sets
    # it from --country; the parser itself makes no country choice.
    stablecoins_as_cash = True
    # The two models book different rows: a basis input (BaseBrokerage
    # `BASIS_INPUTS`, held by the conformance check).
    BASIS_INPUTS = ("stablecoins_as_cash",)

    @property
    def _cash_coins(self):
        """Stablecoins booked as US-dollar cash in this book."""
        return _STABLECOINS if self.stablecoins_as_cash else frozenset()

    def read(self, path: Path):
        """The export decoded; what the later phases need as `_cb` (the
        header found in the stream by `_rows`, the row ids checked for
        duplicates, the unbooked rows for the warning). The rows are a
        generator (`_rows`), so each row's checks come in file order with
        its classification and build, as the one loop had them."""
        # On the instance, what outlives the parse: the lint findings and
        # the rows' instants, which taxjson-brokerage reads after it, and
        # the base's content-stem counters.
        self.lint_findings: List[str] = []
        self._stems: Dict[str, int] = {}
        self.instant_of_line: Dict[int, datetime] = {}
        shown = shown_name(path)
        self._cb = _CbParse(path, shown, RowIds(f"Coinbase CSV {shown}", "ID"),
                            cash_or_fiat=_FIAT | self._cash_coins)
        return self._rows(io.StringIO(read_broker_text(path)))

    def _rows(self, f):
        """The export's records after the header, each with the line it
        starts on and the line it ends on (a cell that swallowed later
        lines ends later; re-audit A2-0584): the rows before the header
        and the blank lines dropped (counted nowhere, as before); a header
        missing a column refused."""
        st = self._cb
        reader = csv.reader(f)
        for rec, (row_start, row) in enumerate(_record_starts(reader), 1):
            if not st.header:
                if is_header_row(row):
                    st.header = row
                    st.header_map, missing = resolve_header(row)
                    st.need = max(st.header_map.values()) if st.header_map else 0
                    if missing:
                        raise ValueError(
                            f"Coinbase CSV {st.shown}: unrecognized "
                            f"export layout — no column found for "
                            f"{', '.join(repr(m) for m in missing)}. "
                            f"Header: {row}. If this is a new "
                            f"Coinbase export variant, add its "
                            f"column names to _HEADER_SYNONYMS."
                        )
                continue
            if not row or not any(c.strip() for c in row):
                continue
            yield _CbRow(row, row_start, reader.line_num, rec)

    def classify(self, r) -> Outcome:
        """The row's shape checked (truncated, a cell spanning lines,
        misaligned), its id checked for a duplicate, its type read: a
        Convert, a Send or Receive and a Buy / Sell / reward row build;
        a type the parser knows as a non-event (a coin's, or fiat's on a
        fiat or cash coin) a non-event; another type a skip, noted as
        unbooked when it moves coins. A row the file's cash events are
        read from (a Convert, a row of a fiat currency or a cash coin) is
        noted for the finish first (`note_cash`, `_cash_events_of_rows`)."""
        st = self._cb
        st.row_start = r.line
        row = r.cells
        header, header_map = st.header, st.header_map
        ids = st.ids
        if len(row) <= st.need:
            # A truncated row: missing cells used to read as ''
            # / 0 (a 2-cell tail vanished uncounted; a 3-4 cell
            # one booked a qty-0 or symbol-less row) — audit
            # R1-115.
            raise ValueError(
                f"Coinbase CSV {st.shown} line {r.end}: "
                f"the row has {len(row)} cells but the header has "
                f"{len(header)} — a truncated row (a cut-off "
                f"export?); refusing to read the missing cells "
                f"as 0. Re-export the file.")
        # A quoted cell that never closes (Notes ending in an
        # unescaped quote) swallows every following row into one
        # cell: those trades vanished with no message (S056-06).
        # Real exports never put a newline in a data cell, and
        # never write more cells than the header.
        if any('\n' in c or '\r' in c for c in row):
            raise ValueError(
                f"Coinbase CSV {st.shown} line {r.line}: "
                f"a cell spans several lines — an unterminated "
                f"quote (e.g. in Notes) opened on this line has "
                f"swallowed lines {r.line + 1}-"
                f"{r.end} after it. Fix the quoting in "
                f"the file.")
        if len(row) > len(header) and any(
                c.strip() for c in row[len(header):]):
            raise ValueError(
                f"Coinbase CSV {st.shown} line {r.end}: "
                f"the row has {len(row)} cells but the header has "
                f"{len(header)} — misaligned columns (an unquoted "
                f"comma?); refusing to read it.")
        ids.check(
            self._col(row, header_map, 'id'), r.line,
            tuple(c.strip() for c in row[:len(header)]),
            " ".join(self._col(row, header_map, k).strip() for k in
                     ('timestamp', 'transaction type',
                      'quantity transacted', 'asset')))
        type_raw = r.type_raw = self._col(row, header_map, 'transaction type')
        is_buy = r.is_buy = bool(_BUY_RE.search(type_raw))
        is_sell = r.is_sell = bool(_SELL_RE.search(type_raw))
        is_staking = r.is_staking = bool(_STAKING_RE.search(type_raw))
        _asset_u = _cb_symbol(self._col(row, header_map, 'asset'))
        if type_raw.strip() and _asset_u and (
                _asset_u in st.cash_or_fiat or ('convert' in type_raw.lower()
                                                and not (is_buy or is_sell or is_staking))):
            self.note_cash(r)           # its events the finish's (cash_events)
        if not (is_buy or is_sell or is_staking):
            if 'convert' in type_raw.lower():
                return r.build(self._build_convert_row)
            _tl = type_raw.strip().lower()
            if _tl in ('send', 'receive'):
                return r.build(self._build_transfer)
            if _tl in _COIN_NONEVENT_TYPES or (
                    _tl in _FIAT_NONEVENT_TYPES
                    and _asset_u in st.cash_or_fiat):
                return Outcome.nonevent(f"type {type_raw.strip()}")
            try:
                _uq = self._num(row, header_map, 'quantity transacted')
            except ValueError:
                _uq = 1.0           # unparseable: treat as live
            if _uq:
                _ud = self._col(row, header_map, 'timestamp')[:10]
                st.unbooked.append((type_raw.strip() or '?', _ud, _asset_u, _uq))
            return Outcome.skip(f"type {type_raw.strip() or '?'!s}")
        return r.build(self._build_row)

    def build(self, r) -> "List[Dict[str, Any]] | Outcome":
        """The builder classify chose, run."""
        return r.builder(r)

    @builds("trade")
    def _build_convert_row(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A Convert row: the two legs (`_build_convert`), or the non-event
        the builder found (a same-coin or stablecoin-to-stablecoin
        convert); one whose Notes the parser cannot read refused."""
        st = self._cb
        row, header_map, type_raw = r.cells, st.header_map, r.type_raw
        legs = self._build_convert(row, header_map, f"{st.shown} line {r.line}")
        if legs is None:
            raise ValueError(
                f"Coinbase 'Convert' row encountered "
                f"(taxable crypto-to-crypto event) but "
                f"its Notes column doesn't carry the "
                f"recognized 'Converted X AAA to Y BBB' "
                f"text — refusing to guess the field "
                f"layout rather than mis-book a "
                f"disposition. Row type={type_raw!r}, "
                f"asset={self._col(row, header_map, 'asset')!r}, "
                f"timestamp={self._col(row, header_map, 'timestamp')!r}, "
                f"notes={self._col(row, header_map, 'notes')!r}. "
                f"Remove the row from the export and enter "
                f"its two legs in a .tt file: a BUYSELL "
                f"selling the coin given up and one buying "
                f"the coin received, both at the "
                f"conversion's value in the row's "
                f"currency (its Subtotal)."
            )
        return legs

    @builds("transfer")
    def _build_transfer(self, r) -> List[Dict[str, Any]]:
        """A Send or Receive row: a TRANSFER leg at the spot price (a
        blank quantity refused)."""
        st = self._cb
        row, header_map, type_raw = r.cells, st.header_map, r.type_raw
        _tl = type_raw.strip().lower()
        _q = abs(self._num(row, header_map, 'quantity transacted'))
        if not _q:
            raise ValueError(
                f"Coinbase {type_raw.strip()} row "
                f"{self._col(row, header_map, 'timestamp')!r} "
                f"({self._col(row, header_map, 'asset')!r}) "
                f"has no Quantity Transacted — refusing "
                f"to drop it. Fill it in from the "
                f"Coinbase statement.")
        _spot = abs(self._num(row, header_map, 'price at transaction'))
        _dt = self._ts(row, header_map, type_raw)
        _xfer = {
            'action': 'TRANSFER',
            'date': _dt.strftime("%Y-%m-%d"),
            'time': _dt.strftime("%H:%M:%S"),
            'date_settle': _dt.strftime("%Y-%m-%d"),
            'symbol': _cb_symbol(self._col(row, header_map, 'asset')),
            'quantity': -_q if _tl == 'send' else _q,
            'currency': self._currency(row, header_map),
            'price': _spot,
            'net_amount': M.units(_q * _spot, 2),
            'account': self.DEFAULT_ACCOUNT,
            'description': type_raw.strip(),
        }
        _xid = self._col(row, header_map, 'id').strip()
        if _xid:
            _xfer['id'] = f'{_xid}-xfer'
        return [_xfer]

    @builds("trade", "dividend")
    def _build_row(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A Buy, Sell or reward row: its facts (the time, the asset, a
        crypto quote from the Notes or the price currency, the money; a
        stablecoin traded against a crypto-asset refused, a stablecoin
        conversion a non-event), then the reward (`_build_staking`) or
        the trade (`_build_trade`)."""
        st = self._cb
        row, type_raw, is_buy = r.cells, r.type_raw, r.is_buy
        is_sell, is_staking = r.is_sell, r.is_staking
        header_map = st.header_map
        dt = self._ts(row, header_map, type_raw)
        asset = self._col(row, header_map, 'asset').strip()
        symbol = _cb_symbol(asset)
        crypto_quote = None
        if (is_buy or is_sell) and not is_staking:
            _pm = _PAIR_RE.search(
                self._col(row, header_map, 'notes') or '')
            if _pm and _cb_symbol(_pm.group(2)) not in (
                    _FIAT | self._cash_coins):
                crypto_quote = _pm.group(2).upper()
                if symbol in self._cash_coins:
                    raise ValueError(
                        f"Coinbase row "
                        f"{self._col(row, header_map, 'timestamp')!r} "
                        f"{type_raw.strip()!r}: stablecoin "
                        f"{asset.upper()} traded against the "
                        f"crypto-asset {crypto_quote} "
                        f"({_pm.group(0).strip()!r}) — that "
                        f"disposes of (or acquires) "
                        f"{crypto_quote}, a shape this parser "
                        f"does not book. Enter the "
                        f"{crypto_quote} leg via a .tt file and "
                        f"remove the row.")
        if (crypto_quote is None and (is_buy or is_sell)
                and not is_staking and not self.stablecoins_as_cash
                and 'price currency' in header_map):
            # Property mode (a US project): a fill PRICED in a
            # stablecoin spends or receives that coin even when
            # Notes carries no 'on BASE-QUOTE' pair. Folding the
            # quote to USD cash dropped the stablecoin's
            # disposition (re-audit A2-0730); it is booked as a
            # crypto-quoted fill (or refused when Notes cannot
            # give its quantity).
            _pc = (self._col(row, header_map, 'price currency')
                   or '').strip().upper()
            if (_cb_symbol(_pc) in _STABLECOINS
                    and _cb_symbol(_pc) != symbol):
                crypto_quote = _pc
        if (symbol in self._cash_coins
                and (is_buy or is_sell) and not is_staking):
            # "Bought 3495.67 USDC for 5000 CAD": fiat -> USD
            # cash under the stablecoin-as-cash model (see
            # _STABLECOINS) — a currency conversion, not an
            # acquisition of property. Counted, not booked —
            # but a fill away from the peg is said (the
            # approximation drops a de-peg gain or loss:
            # partition INPUTS-12).
            # A CAD- or EUR-priced row through the day's rate
            # (re-audit A2-0590).
            warn_depeg(
                symbol,
                self._num(row, header_map,
                          'price at transaction'),
                abs(self._num(row, header_map,
                              'quantity transacted')),
                dt.strftime('%Y-%m-%d'),
                f"Coinbase {type_raw.strip()}",
                currency=(self._col(row, header_map,
                                    'price currency')
                          or 'USD').strip().upper() or 'USD')
            return Outcome.nonevent(
                f"stablecoin conversion {type_raw.strip()} "
                f"{symbol} (USDC treated as USD cash, as "
                f"on Kraken)")
        currency = self._currency(row, header_map)
        qty = self._num(row, header_map, 'quantity transacted')
        price = self._num(row, header_map, 'price at transaction')
        fee = self._num(row, header_map, 'fees')
        total = self._num(row, header_map, 'total')
        if is_buy and not is_sell and qty < 0 and total < 0:
            # Coinbase's own SALE signature (quantity and total
            # both negative) under a Buy label was booked as an
            # acquisition, also under --strict (re-audit A2-1025).
            raise ValueError(
                f"Coinbase CSV {st.shown} line {r.end}: "
                f"a {type_raw.strip()} row with a NEGATIVE "
                f"quantity and total — the sale signature; "
                f"refusing to book it as a purchase.")
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M:%S")
        cb_id = self._col(row, header_map, 'id').strip()
        if not cb_id and crypto_quote:
            # Both legs need one id stem for fill-crypto to value
            # the swap once (re-audit A2-0998's twin) — also for
            # a blank cell in an ID column (issue #93).
            cb_id = self._content_stem(row)
        where = (f"Coinbase {st.shown} line {r.line} "
                 f"({type_raw.strip()} {asset} "
                 f"{self._col(row, header_map, 'timestamp')})")
        if not is_staking:
            self._refuse_trade_shape(r, header_map, where, qty)
        facts = _CbFacts(
            cb_id=cb_id, crypto_quote=crypto_quote, currency=currency,
            date_str=date_str, fee=fee, price=price,
            qty=qty, symbol=symbol, time_str=time_str,
            total=total, where=where,
        )
        if is_staking:
            return self._build_staking(r, facts)
        return self._build_trade(r, facts)

    def _refuse_trade_shape(self, r: Any, header_map: Dict[str, int], where: str,
                            qty: float) -> None:
        """A Buy or Sell the books cannot hold (the owner's ruling of
        2026-11-19): a Buy whose quantity is negative and whose Total is
        blank was booked as a purchase of the magnitude, the sign dropped
        (TJ-4511; with a negative Total it is the sale signature, refused
        above); a trade of 0 units, or a blank quantity, stopped `tjs
        run` at the schema check, naming no row."""
        row, type_raw = r.cells, r.type_raw
        if (r.is_buy and not r.is_sell and qty < 0
                and not self._col(row, header_map, 'total').strip()):
            raise ValueError(
                f"{where}: a {type_raw.strip()} row with a NEGATIVE "
                f"Quantity Transacted {qty:g} and a blank Total — the "
                f"action says purchase, the quantity says sale; refusing "
                f"to guess. Correct the quantity's sign or the "
                f"Transaction Type from the statement.")
        if abs(qty) < QTY_ZERO:
            raise zero_units_refusal(
                where, type_raw.strip(),
                self._col(row, header_map, 'quantity transacted'),
                cell="Quantity Transacted")

    def _build_staking(self, r, f) -> List[Dict[str, Any]]:
        """A reward row: a negative cell refused, the cells read, then
        (parsers/toolbox/staking) the value from the Subtotal, the price or
        the Total less the fee (a cell that does not fit refused,
        `reward_value`), the DIVIDEND with its evidence and, for a coin,
        the acquisition at the same value (`valued_reward_legs`)."""
        st = self._cb
        row = r.cells
        cb_id, currency, date_str, fee = f.cb_id, f.currency, f.date_str, f.fee
        price, qty, symbol, time_str = f.price, f.qty, f.symbol, f.time_str
        total, where = f.total, f.where
        header_map = st.header_map
        _neg = [f"{label} {self._col(row, header_map, key).strip()}"
                for label, key in (
                    ('Quantity Transacted', 'quantity transacted'),
                    ('Subtotal', 'subtotal'), ('Total', 'total'))
                if key in header_map
                and self._num(row, header_map, key) < 0]
        if _neg:
            # A negative reward (a reversal or correction) was
            # booked as new POSITIVE income and coins: every
            # amount below is a magnitude (backlog TJ-0383).
            # Refused, as Kraken refuses one.
            raise ValueError(
                f"{where}: a reward row with a negative "
                f"{', '.join(_neg)} — a reward reversal is not "
                f"modeled; refusing to book it as income. "
                f"Hand-check it, enter the correction via a .tt "
                f"file and remove the row from the export.")
        # The cells the reward's value is chosen from (a blank Total
        # or fee is unknown, not 0); the choice, the refusal of cells
        # that do not fit and the legs are the toolbox's (PLAN step 14).
        fee_known = bool(self._col(row, header_map,
                                   'fees').strip())
        gross = (abs(total) if self._col(
            row, header_map, 'total').strip() else None)
        _sub = abs(self._num(row, header_map, 'subtotal')) \
            if 'subtotal' in header_map else 0.0
        total, net_known = reward_value(
            where, qty, price, fee, subtotal=_sub, gross=gross,
            fee_known=fee_known, check_qp=self._check_qp,
            abs_tol=_QP_TOL_ABS, rel_tol=_QP_TOL_REL,
            total_rel_tol=_TOTAL_TOL_REL)
        evidence = self._staking_evidence(r, f, total, gross, fee_known, net_known)
        return valued_reward_legs(
            symbol, qty, currency, price, total, date_str, time_str, cb_id,
            evidence=evidence, cash_coins=self._cash_coins,
            account=self.DEFAULT_ACCOUNT)

    def _staking_evidence(self, r, f, total, gross, fee_known, net_known) -> Dict[str, Any]:
        """The reward's evidence record (schema 1): its source, the asset,
        the quantities and values known, the commission method, and the
        valuation when the row carries a price."""
        st = self._cb
        row, type_raw = r.cells, r.type_raw
        cb_id, currency, date_str, fee = f.cb_id, f.currency, f.date_str, f.fee
        price, qty, symbol = f.price, f.qty, f.symbol
        header_map = st.header_map
        evidence = {
            'schema_version': 1, 'broker': 'coinbase',
            'kind': ('staking' if re.search(r'\bstaking\b', type_raw, re.I)
                     else 'other_reward'),
            'source_type': type_raw.strip(),
            'source_file': st.shown, 'source_line': r.line,
            'source_timestamp': self._col(row, header_map, 'timestamp').strip(),
            'source_id': cb_id, 'asset': symbol,
            'gross_quantity': None, 'fee_quantity': None,
            'fee_currency': currency, 'net_quantity': abs(qty),
            'valuation_currency': currency,
            'gross_value': gross,
            'fee_value': abs(fee) if fee_known else None,
            'net_value': total if net_known else None,
            'price': price if price > 0 else None,
            'commission_known': fee_known,
            'commission_method': ('withheld_from_reward' if fee_known
                                  else 'unknown'),
        }
        if price > 0:
            evidence['valuation'] = {
                'price': price, 'currency': currency, 'date': date_str,
                'source': 'broker', 'fx_rate': None, 'target_currency': '',
            }
        return evidence

    def _build_trade(self, r, f) -> List[Dict[str, Any]]:
        """A Buy or Sell: the quantity signed, a blank Total derived (and
        counted for the note) or the Total checked, the gross from the
        Subtotal when there is no price, the row; a stablecoin's depeg
        warned; a crypto-quoted trade as its two legs."""
        st = self._cb
        row, type_raw, is_sell = r.cells, r.type_raw, r.is_sell
        cb_id, crypto_quote, currency, date_str = f.cb_id, f.crypto_quote, f.currency, f.date_str
        fee, price, qty, symbol = f.fee, f.price, f.qty, f.symbol
        time_str, total, where = f.time_str, f.total, f.where
        header_map = st.header_map
        qty = self.signed_quantity(qty, action_is_sell=is_sell)

        derived = not self._col(row, header_map, 'total').strip()
        if derived:
            # R1-103: a blank Total read as 0 booked $0 cost /
            # $0 proceeds with no warning. Derive it the way
            # Coinbase builds it, or refuse.
            total = self._derive_blank_total(
                row, header_map, type_raw, is_sell, qty, price,
                fee, where)
            st.blank_totals += 1
        else:
            # A given Total must fit the row (A2-0080), and a
            # sale whose fee exceeds its value nets NEGATIVE
            # whatever sign the cell carries (A2-0565).
            total, fee = self._checked_total(
                where, row, header_map, is_sell, qty, price, fee,
                total)

        gross = price * abs(qty)
        if not price and qty and 'subtotal' in header_map \
                and self._col(row, header_map, 'subtotal').strip():
            # A blank Price cell: the Subtotal states the value
            # (quantity x price). The price and the gross come
            # from it; with price 0 and a net of 0 (a fee that
            # took the whole sale) fill-crypto read the row as
            # unvalued and booked market proceeds over the
            # stated 0 (backlog TJ-0988).
            _sub = abs(self._num(row, header_map, 'subtotal'))
            if _sub:
                gross = _sub
                price = M.units(_sub / abs(qty), 8)
        # Coinbase Advanced Trade Sell rows store both qty and total
        # as negative ("money out, position out"); store the magnitude
        # so the gain engine sees positive proceeds. Matches the old
        # cb_trades.pl convention (abs($total_raw)). A DERIVED
        # total is already signed the engine's way (a sale whose
        # fee exceeds its value is negative).
        tx = {
            'action': 'BUYSELL',
            'date': date_str,
            'time': time_str,
            'date_settle': date_str,
            'symbol': symbol,
            'quantity': qty,
            'currency': currency,
            'price': price,
            'net_amount': total,
            'gross_amount': gross,
            'fee': abs(fee),
            'account': self.DEFAULT_ACCOUNT,
        }
        if cb_id:
            tx['id'] = cb_id
        if self._cash_coins and currency in FIAT_CURRENCIES:
            # "Bought 0.5 ETH for 1000 USDC on ETH-USDC" valued
            # at 880 USD spent USDC at 0.88: off the peg the
            # cash approximation drops a gain or loss — say so,
            # as a Buy/Sell USDC row does (re-audit A2-1003).
            _am = _ADV_NOTES_RE.search(
                self._col(row, header_map, 'notes') or '')
            if _am and _cb_symbol(_am.group(5)) in self._cash_coins:
                _sq = strict_money(_am.group(4), 'Notes quantity',
                                   where)
                if _sq:
                    warn_depeg(_cb_symbol(_am.group(5)),
                               abs(total) / _sq, _sq, date_str,
                               f"Coinbase {type_raw.strip()}",
                               currency=currency)
        if crypto_quote:
            return self._crypto_pair_legs(
                row, header_map, tx, type_raw, is_sell,
                crypto_quote, cb_id)
        return [tx]

    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """After the loop: the blank Totals derived noted, the rows of
        types the parser does not book warned about (a lint finding), the
        file's cash events built from the rows classify noted
        (`_cash_events_of_rows`; a cash cell that cannot be read the
        file's `cash_problem`, `_cash_events_from`)."""
        st = self._cb
        unbooked = st.unbooked
        if st.blank_totals:
            emit_line(f"note: Coinbase {st.shown}: {st.blank_totals} "
                  f"Buy/Sell row(s) had a blank Total — derived from "
                  f"Subtotal ± fee (or quantity × price ± fee when "
                  f"Subtotal is blank too). Check them against the "
                  f"Coinbase statement.")
        if unbooked:
            kinds: Dict[str, int] = {}
            for k, _d, _a, _q in unbooked:
                kinds[k] = kinds.get(k, 0) + 1
            dates = sorted(d for _k, d, _a, _q in unbooked)
            assets = sorted({a for _k, _d, a, _q in unbooked})
            msg = (f"Coinbase {st.shown}: {len(unbooked)} row(s) of "
                   f"type(s) the parser does not book "
                   f"({', '.join(f'{k} x{n}' for k, n in sorted(kinds.items()))}"
                   f"; {dates[0]}..{dates[-1]}; assets "
                   f"{', '.join(assets[:8])}) move coins — an airdrop or "
                   f"reward is income and an acquisition, a payment a "
                   f"disposition. They are NOT in the books: enter each "
                   f"via a .tt file.")
            emit_line(f"warning: UNBOOKED: {msg}")
            self.lint_findings.append(msg)
        # The rows' instants are the build's: `_ts` records none for the
        # cash rows read again here.
        st.row_start = None
        self._cash_events_from(self._cash_events_of_rows)
        return transactions

    def after_marks(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The rows of one second in the order their instants give, after
        the template's fill marks, as the one parse had it."""
        order_within_seconds(transactions, self.row_line,
                             self.instant_of_line)
        return transactions

    def release(self) -> None:
        self._cb = None                 # the export's header, ids and rows released

    def _content_stem(self, row) -> str:
        """The id stem of a row with no ID (base.content_stem)."""
        return self.content_stem(row, 'cb')

    @staticmethod
    def _fits_qp(value, qty, price) -> bool:
        qp = abs(qty) * abs(price)
        return money_close(value, qp, abs_tol=_QP_TOL_ABS, rel_tol=_QP_TOL_REL,
                           additive=True, scale=qp)

    def _check_qp(self, where, what, value, qty, price) -> None:
        """Refuse a value that contradicts |qty| x price (A2-0022,
        A2-0250). Nothing to check without both."""
        if not qty or not price or self._fits_qp(value, qty, price):
            return
        raise ValueError(
            f"{where}: {what} {M.shown(abs(value), 2):,.2f} does not fit |Quantity| "
            f"{abs(qty):g} x Price {abs(price):g} = "
            f"{M.shown(abs(qty) * abs(price), 2):,.2f} — a wrong or shifted column; "
            f"refusing to book it. Correct the row from the Coinbase "
            f"statement.")

    def _checked_total(self, where, row, header_map, is_sell, qty, price,
                       fee, total) -> tuple:
        """(signed net, fee) of a Buy/Sell row whose Total is given: the
        net is a positive cost on a buy, proceeds on a sale (negative
        when the fee exceeds the value). The Total must be Subtotal +/-
        fee, and the Subtotal (or, without one, the Total itself) must
        fit |qty| x price (re-audit A2-0080). An all-zero row is refused
        like a blank Total (A2-1023): fill-crypto would re-price it at
        market, a guess. In a layout with no fee column the fee is the
        gap between the Total and the Subtotal."""
        fee = abs(fee)
        has_sub = ('subtotal' in header_map
                   and self._col(row, header_map, 'subtotal').strip())
        if has_sub:
            sub = abs(self._num(row, header_map, 'subtotal'))
            self._check_qp(where, 'Subtotal', sub, qty, price)
            tol = _QP_TOL_ABS + _TOTAL_TOL_REL * sub
        elif price and qty:
            sub = abs(qty) * abs(price)
            tol = _QP_TOL_ABS + _QP_TOL_REL * sub
        else:
            sub = None
        if not total and not sub and not (price and qty):
            raise ValueError(
                f"{where}: Total, Subtotal and Price are all $0 — refusing "
                f"to book $0 {'proceeds' if is_sell else 'cost'} (a "
                f"market price filled in later would be a guess). Fill "
                f"in the row's value from the Coinbase statement.")
        if sub is None:
            return abs(total), fee
        if 'fees' not in header_map:
            # Older layouts have no fee column: the gap between Total and
            # Subtotal IS the fee, so it must be a charge of fee size
            # (Coinbase's flat minimums are a few dollars).
            implied = sub - abs(total) if is_sell else abs(total) - sub
            if not -tol <= implied <= 3.0 + _QP_TOL_REL * sub:
                raise ValueError(
                    f"{where}: Total {M.shown(abs(total), 2):,.2f} does not fit "
                    f"{'Subtotal' if has_sub else '|Quantity| x Price'} "
                    f"{M.shown(sub, 2):,.2f} (implied fee {M.shown(implied, 2):,.2f}) — a wrong or "
                    f"shifted column; refusing to book it. Correct the "
                    f"row from the Coinbase statement.")
            # Total and Subtotal both given: the gap is the fee exactly,
            # and the fees reports list it as a layout with the column
            # would (backlog TJ-0511). Against quantity x price it also
            # holds the spread: no fee is claimed from it.
            return abs(total), (M.units(implied, 8)
                                if has_sub and implied > 0 else 0.0)
        expected = sub - fee if is_sell else sub + fee
        if abs(abs(total) - abs(expected)) > tol:
            raise ValueError(
                f"{where}: Total {M.shown(abs(total), 2):,.2f} does not fit "
                f"{'Subtotal' if has_sub else '|Quantity| x Price'} "
                f"{M.shown(sub, 2):,.2f} {'-' if is_sell else '+'} fee {M.shown(fee, 2):,.2f} = "
                f"{M.shown(expected, 2):,.2f} — a wrong or shifted column; refusing to "
                f"book it. Correct the row from the Coinbase statement.")
        return (-abs(total) if expected < 0 else abs(total)), fee

    def _build_convert(self, row, header_map, loc='') -> "List[Dict[str, Any]] | Outcome | None":
        """Two-leg SELL (spent) + BUY (received) for a retail Convert row;
        the non-event Outcome for one booking nothing (same coin, stablecoin
        for stablecoin); None when the Notes text doesn't match (the caller fails hard).

        FMV: the retail row carries the conversion's fiat Subtotal
        (pre-fee value); both legs use it — the swap disposes the
        spent crypto at FMV and acquires the received crypto at the
        same FMV (CRA s. 40(1) / IRS Notice 2014-21). A blank Subtotal
        is the Total less the fee (Total = Subtotal + fee), or, when only
        a fee is given, quantity x price. Without a value or a fee the
        legs ship price=0 so taxjson-fill-crypto backfills the FMV, per
        the KNOWN_ISSUES fix template."""
        notes = self._col(row, header_map, 'notes')
        m = _CONVERT_NOTES_RE.search(notes or '')
        if not m:
            return None
        from_qty = strict_money(m.group(1), 'Convert quantity',
                                f"Coinbase notes {notes!r}")
        from_raw = m.group(2).upper()
        to_qty = strict_money(m.group(3), 'Convert quantity',
                              f"Coinbase notes {notes!r}")
        to_raw = m.group(4).upper()
        if from_qty <= 0 or to_qty <= 0 or from_raw == to_raw:
            return None
        # Cross-check the Asset column against the Notes legs — a
        # mismatch means an unknown layout, not a parsing choice.
        asset_col = (self._col(row, header_map, 'asset') or '').strip().upper()
        if asset_col and asset_col not in (from_raw, to_raw):
            return None
        # The file and line first (`loc`), as every refusal names them.
        where = (f"Coinbase {loc + ' ' if loc else ''}Convert "
                 f"{self._col(row, header_map, 'timestamp')!r} "
                 f"(notes {notes!r})")
        # The row's own Quantity Transacted is the Asset leg's quantity:
        # it must agree with Notes, and the row's value with that leg's
        # quantity x price (re-audit A2-0022 — a 10x Subtotal or a
        # quantity of 5 beside Notes' 0.05 booked silently).
        row_qty = abs(self._num(row, header_map, 'quantity transacted'))
        _price = abs(self._num(row, header_map, 'price at transaction'))
        subtotal = abs(self._num(row, header_map, 'subtotal'))
        fee = abs(self._num(row, header_map, 'fees'))
        _tot = abs(self._num(row, header_map, 'total'))
        fee_given = bool(self._col(row, header_map, 'fees').strip())
        if subtotal and _tot and fee_given:
            # Total = Subtotal + fee, as on a Buy (the fee is capitalized
            # into the acquired coin below). A Total of 220 beside a
            # Subtotal of 20 and a fee of 2 was booked as 20 / 22 with no
            # word (backlog TJ-0538).
            if abs(_tot - (subtotal + fee)) > (_QP_TOL_ABS
                                              + _TOTAL_TOL_REL * subtotal):
                raise ValueError(
                    f"{where}: Total {M.shown(_tot, 2):,.2f} does not fit Subtotal "
                    f"{M.shown(subtotal, 2):,.2f} + fee {M.shown(fee, 2):,.2f} = "
                    f"{M.shown(subtotal + fee, 2):,.2f} — a wrong or shifted column; "
                    f"refusing to book it. Correct the row from the "
                    f"Coinbase statement.")
        elif not subtotal and _tot and fee_given:
            # No Subtotal: the conversion's value is the Total less the
            # fee. The legs used to go to fill-crypto unvalued and the
            # fee, in the row's currency, was dropped (backlog TJ-0512).
            if fee >= _tot:
                raise ValueError(
                    f"{where}: fee {M.shown(fee, 2):,.2f} is not less than the Total "
                    f"{M.shown(_tot, 2):,.2f} and the Subtotal is blank — the "
                    f"conversion's value is unknown; refusing to book it. "
                    f"Fill in its Subtotal from the Coinbase statement.")
            subtotal = _tot - fee
        if asset_col and row_qty:
            leg_qty = from_qty if asset_col == from_raw else to_qty
            if abs(row_qty - leg_qty) > 1e-8 + 1e-6 * leg_qty:
                raise ValueError(
                    f"{where}: Quantity Transacted {row_qty:g} {asset_col} "
                    f"disagrees with Notes ({leg_qty:g}) — refusing to "
                    f"guess which to book.")
            _sub = subtotal
            if (_price and (_sub or _tot)
                    and not any(self._fits_qp(v, row_qty, _price)
                                for v in (_sub, _tot) if v)):
                self._check_qp(where, 'Subtotal' if _sub else 'Total',
                               _sub or _tot, row_qty, _price)
        from_asset, to_asset = _cb_symbol(from_raw), _cb_symbol(to_raw)
        same_coin_hint(from_asset, to_asset, from_qty, to_qty,
                       f"Coinbase Convert {from_raw}->{to_raw}")
        if from_asset == to_asset:
            # ETH -> ETH2 under `GLOBAL ETH2 ETH`: two spellings of one
            # property (R1-110). A relabel, not a disposition — unless
            # the quantities differ, which has no modeled booking.
            if abs(from_qty - to_qty) > 1e-8 * max(from_qty, to_qty):
                raise ValueError(
                    f"Coinbase Convert {notes!r}: {from_raw} and {to_raw} "
                    f"are the same property ({from_asset}) but the "
                    f"quantities differ ({from_qty:g} vs {to_qty:g}) — "
                    f"not modeled; enter the difference via a .tt file "
                    f"and remove the row.")
            return Outcome.nonevent(f"{from_raw}->{to_raw} convert (same "
                                f"property {from_asset})")
        if not subtotal and not _tot and fee:
            # Only the fee is given: the value fill-crypto finds is in US
            # dollars and the fee in the row's currency, so the fee was
            # dropped (backlog TJ-0512). Quantity x Price is the value,
            # as for a Buy with a blank Total; without a price the row is
            # refused.
            if not (asset_col and row_qty and _price):
                raise ValueError(
                    f"{where}: the row charges a fee of {M.shown(fee, 2):,.2f} "
                    f"{self._currency(row, header_map)} but its Subtotal, "
                    f"Total and Price are blank — the conversion's value "
                    f"is unknown, so the fee cannot be added to it; "
                    f"refusing to book it. Fill in its Subtotal from the "
                    f"Coinbase statement.")
            subtotal = row_qty * _price
        dt = self._ts(row, header_map, 'Convert')
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M:%S")
        currency = self._currency(row, header_map)
        # Fee convention: Coinbase's Subtotal is the pre-fee FMV of the
        # conversion; Total = Subtotal + Fees. Booking Subtotal as BOTH
        # legs' net_amount excluded the fee entirely, overstating the
        # round-trip gain by the fee on every Convert. The engine reads
        # net_amount fee-inclusively (src/taxjson/engine/: fee-inclusive
        # cost on buys, fee-net proceeds on sells), so the fee must land
        # on exactly ONE leg. We capitalize it into the ACQUIRED leg's
        # basis (buy net = Subtotal + fee) — the same convention as the
        # Kraken fiat instant-trade path (kraken.py: net = quote_amt +
        # fiat_fee on a buy): the fee is part of what it cost to acquire
        # the new asset. The disposed leg keeps proceeds = Subtotal (the
        # FMV Coinbase itself states for the swap). Putting it on one
        # leg only avoids double-deduction; capitalizing (rather than
        # netting the sell) defers the fee's tax benefit until the
        # acquired asset is sold, which is the conservative reading of
        # an acquisition-side charge.
        sell = {
            'action': 'BUYSELL',
            'date': date_str, 'time': time_str, 'date_settle': date_str,
            'symbol': from_asset.replace(' ', '.'),
            'quantity': self.signed_quantity(from_qty,
                                             action_is_sell=True),
            'currency': currency,
            'price': (subtotal / from_qty) if subtotal else 0.0,
            'net_amount': subtotal, 'gross_amount': subtotal,
            'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
            'description': f'Convert (sell leg): {notes}'.strip(),
        }
        buy = {
            'action': 'BUYSELL',
            'date': date_str, 'time': time_str, 'date_settle': date_str,
            'symbol': to_asset.replace(' ', '.'),
            'quantity': self.signed_quantity(to_qty,
                                             action_is_sell=False),
            'currency': currency,
            # No-Subtotal rows ship 0 so taxjson-fill-crypto backfills
            # the FMV — stamping the bare fee there would be misread as
            # the FMV (fill derives price from any nonzero net_amount).
            'price': (subtotal / to_qty) if subtotal else 0.0,
            'net_amount': (subtotal + fee) if subtotal else 0.0,
            'gross_amount': subtotal,
            'fee': fee if subtotal else 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': f'Convert (buy leg): {notes}'.strip(),
        }
        cb_id = self._col(row, header_map, 'id').strip()
        if not cb_id:
            # No ID (an older export without the column, or a blank
            # cell — issue #93): the legs still need one stem or
            # fill-crypto values each from its own close (A2-0998).
            cb_id = self._content_stem(row)
        if cb_id:
            sell['id'] = f'{cb_id}-sell'
            buy['id'] = f'{cb_id}-buy'
        if subtotal and currency in FIAT_CURRENCIES:
            # A USD-valued convert spending or receiving a stablecoin
            # gives its implied price: off the peg the cash
            # approximation drops a gain or loss (A2-1003).
            for _sym, _q in ((from_asset, from_qty), (to_asset, to_qty)):
                if _sym in self._cash_coins:
                    warn_depeg(_sym, subtotal / _q, _q, date_str,
                               "Coinbase Convert", currency=currency)
        # Stablecoin legs are USD cash (see _STABLECOINS): converting
        # USDC into ETH is a cash purchase of ETH, ETH into USDC a cash
        # sale. The fee then lands on the one crypto leg — capitalized
        # into a purchase, netted from a sale's proceeds.
        cash = self._cash_coins
        if from_asset in cash and to_asset in cash:
            return Outcome.nonevent("stablecoin <-> stablecoin convert "
                                "(USD cash)")
        if from_asset in cash:
            return [buy]
        if to_asset in cash:
            if subtotal:
                # A fee above the value (a dust convert with a minimum
                # fee) nets NEGATIVE proceeds — the schema and engine
                # take a negative sale; clamping at 0 dropped the
                # excess fee from the loss silently (S056-18).
                sell['net_amount'] = subtotal - fee
                sell['fee'] = fee
            return [sell]
        return [sell, buy]

    def _derive_blank_total(self, row, header_map, type_raw, is_sell, qty,
                            price, fee, where='') -> float:
        """Total for a Buy/Sell row whose Total cell is blank. Coinbase
        builds Total = Subtotal + fee on a buy and Subtotal - fee on a
        sell; without a Subtotal, quantity x price stands in for it.
        With neither, the row's value is unknown — refuse rather than
        book $0 cost or $0 proceeds. A Subtotal must fit |qty| x price,
        as it must when the Total is given (_checked_total), a stated 0
        too: the blank redundant cell weakened the check (backlog
        TJ-0992, TJ-1188)."""
        fee = abs(fee)
        has_sub = self._col(row, header_map, 'subtotal').strip()
        sub = abs(self._num(row, header_map, 'subtotal')) if has_sub else 0.0
        if has_sub:
            self._check_qp(where or f"Coinbase row "
                           f"{self._col(row, header_map, 'timestamp')!r} "
                           f"{type_raw.strip()!r}", 'Subtotal', sub, qty,
                           price)
        if not sub and price and qty:
            sub = abs(price * qty)
        if not sub:
            raise ValueError(
                f"Coinbase row "
                f"{self._col(row, header_map, 'timestamp')!r} "
                f"{type_raw.strip()!r} "
                f"{self._col(row, header_map, 'asset')!r}: the Total "
                f"cell is blank and neither Subtotal nor quantity x "
                f"price is there to derive it from — refusing to book "
                f"$0 {'proceeds' if is_sell else 'cost'}. Fill in the "
                f"Total from the Coinbase statement.")
        # A sale whose fee exceeds the value nets negative (S056-18).
        return sub - fee if is_sell else sub + fee

    def _crypto_pair_legs(self, row, header_map, tx, type_raw, is_sell,
                          quote, cb_id):
        """Both legs of an Advanced Trade fill on a crypto-quoted pair
        (R1-102): `tx` (the base asset, already built from the row) and
        the quote coin moving the other way — a disposition of it on a
        buy, an acquisition of it on a sell, at the fill's fair value
        (CRA / IRS barter-at-FMV, as _build_convert and Kraken's
        crypto/crypto path book it).

        The quote amount comes from Notes ("Bought 3 ETH for 0.1 BTC on
        ETH-BTC"): fee included on a buy, net of it on a sell, so its
        fair value is the row's Total either way. That puts the fee on
        exactly one leg — capitalised into the bought coin, netted from
        the sold one. When the row is priced in the quote coin itself
        (no fiat value on it), both legs ship price 0 in USD for
        taxjson-fill-crypto, whose failure is a validation ERROR."""
        notes = self._col(row, header_map, 'notes') or ''
        ts = self._col(row, header_map, 'timestamp')
        where = (f"Coinbase row {ts!r} {type_raw.strip()!r} "
                 f"(notes {notes!r})")
        m = _ADV_NOTES_RE.search(notes)
        if not m:
            raise ValueError(
                f"{where}: a fill on a crypto-quoted pair disposes of "
                f"(or acquires) {quote}, but Notes does not carry the "
                f"'Bought|Sold X BASE for Y {quote} on BASE-{quote}' "
                f"text needed to book that leg — refusing rather than "
                f"drop it. Enter the {quote} leg via a .tt file.")
        verb, b_qty, b_sym, q_qty, q_sym, p_base, p_quote = m.groups()
        asset = (self._col(row, header_map, 'asset') or '').strip().upper()
        b_qty = strict_money(b_qty, 'Notes quantity', where)
        q_qty = strict_money(q_qty, 'Notes quantity', where)
        if (b_sym.upper() != asset or p_base.upper() != asset
                or q_sym.upper() != quote or p_quote.upper() != quote
                or (verb.lower() == 'sold') != is_sell
                or b_qty <= 0 or q_qty <= 0
                or abs(b_qty - abs(tx['quantity']))
                > 1e-9 * max(1.0, b_qty)):
            raise ValueError(
                f"{where}: Notes disagree with the row (asset {asset!r}, "
                f"quantity {abs(tx['quantity']):g}, "
                f"{'sell' if is_sell else 'buy'}) — refusing to guess "
                f"which to book.")
        date_str, time_str = tx['date'], tx['time']
        quote_leg = {
            'action': 'BUYSELL',
            'date': date_str, 'time': time_str, 'date_settle': date_str,
            'symbol': _cb_symbol(quote),
            'quantity': self.signed_quantity(q_qty,
                                             action_is_sell=not is_sell),
            'currency': tx['currency'], 'price': 0.0,
            'net_amount': 0.0, 'gross_amount': 0.0, 'fee': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': f"Advanced Trade {p_base.upper()}-{quote} "
                           f"(counter leg of crypto-to-crypto): "
                           f"{notes}".strip(),
        }
        # Both legs say "crypto-to-crypto": no fiat changes hands, and
        # fx-cash keys its non-cash test on that wording (Kraken's and
        # the Convert legs carry it too). The fiat-valued base leg had no
        # description, so fx-cash booked the swap's value as US dollars
        # acquired and disposed (re-audit A2-0079 / A2-0234).
        tx['description'] = (f"Advanced Trade {p_base.upper()}-{quote} "
                             f"({'sell' if is_sell else 'buy'} leg of "
                             f"crypto-to-crypto): {notes}").strip()
        if cb_id:
            quote_leg['id'] = f'{cb_id}-quote'
        if tx['currency'] not in _FIAT:
            # Priced in the quote coin (or another crypto): the row has
            # no fiat value. Both legs go to taxjson-fill-crypto. A buy's
            # fee is in the quote coin and inside the Notes amount spent;
            # a fiat-priced buy's Total counts it in the one value, so
            # the fee rides along for the filler to add (`swap_fee`,
            # backlog TJ-0981). A sell's Notes amount is net of the fee,
            # as its fiat Total is: nothing to add.
            fee_units = abs(tx.get('fee') or 0.0)
            in_quote = tx['currency'] == quote
            for leg in (tx, quote_leg):
                leg.update(currency='USD', price=0.0, net_amount=0.0,
                           gross_amount=0.0, fee=0.0)
                if fee_units and not is_sell and in_quote \
                        and fee_units < q_qty:
                    leg['swap_fee'] = f"{fee_units!r} spent"
            return [tx, quote_leg]
        value = abs(tx['net_amount'])
        if not value:
            raise ValueError(
                f"{where}: the row's Total is $0, so the {quote} leg has "
                f"no fair value to book — refusing. Fill in the Total "
                f"from the Coinbase statement.")
        quote_leg.update(price=M.units(value / q_qty, 8), net_amount=value,
                         gross_amount=value)
        return [tx, quote_leg]

    # ------------------------------------------------------ the cash events
    def _cash_events_of_rows(self) -> List[Dict[str, Any]]:
        """The cash events of the export for the FX-on-cash ledger v2
        (lib/cash_events, tax-logic CA-FX-07), from the rows classify
        noted (`note_cash`), in the file's order: a CASHMOVE per Deposit /
        Withdrawal of a currency, and (cash-mode book: a USD stablecoin is
        US-dollar cash, CA-CRYPTO-02) per Send / Receive of a stablecoin;
        an FXCONV per Buy / Sell of a stablecoin for another currency
        ("Bought 3495.67 USDC for 5000 CAD"); a FLOW per Subscription paid
        in a currency (or a stablecoin), and per stablecoin leg of a
        Convert with a coin ("Converted 100 USDC to 1 ETH": the parser
        books the coin alone, as bought or sold for US dollars). Each
        event carries the row's ID (`_broker_id`, left out of every
        output): two exports' rows alike in every other way are two events
        when their IDs differ (cash_events.dedup_across_files). The export
        has no balance: a CASHBAL .tt line gives the ledger one. The
        stablecoin basis is the parse's (`stablecoins_as_cash`, the
        country's `stablecoin_model`, which the sidecar records). A cell
        the events need that cannot be read raises: the file's problem
        (`_cash_events_from`). Once lib/cash_readers/coinbase.py read the
        export a second time (PLAN step 16 commit 8)."""
        cash_coins = set(self._cash_coins)
        out: List[Dict[str, Any]] = []
        for r in self._cash_rows:
            out.extend(self._cash_events_of_row(r, cash_coins))
        return out

    def _cash_events_of_row(self, r, cash_coins) -> List[Dict[str, Any]]:
        """The cash events of one row noted, each with the row's ID: a
        Convert's stablecoin leg (`_convert_cash_leg`), a fiat or cash-coin
        row's move, fee or conversion (`_currency_row_cash`); none for a
        row with no type or asset."""
        st = self._cb
        row, header_map = r.cells, st.header_map
        typ = self._col(row, header_map, 'transaction type').strip().lower()
        # The book's name for the code (ticker.map GLOBAL lines), as the
        # parser reads it (backlog TJ-0533).
        asset = _cb_symbol(self._col(row, header_map, 'asset'))
        if not typ or not asset:
            return []
        where = f"{st.shown}:{r.rec}"
        # The row's own ID: what tells two exports' identical-looking
        # rows apart (backlog TJ-0865).
        cb_id = self._col(row, header_map, 'id').strip()
        if 'convert' in typ and not (r.is_buy or r.is_sell or r.is_staking):
            # The Convert's stablecoin leg (backlog TJ-0916): the
            # parser books "Converted 100 USDC to 1 ETH" as ETH bought
            # (and the reverse as ETH sold), a row the ledger reads as
            # crypto-for-crypto (cash_legs._non_cash) — the US
            # dollars it spent or received were in no ledger. Read from
            # the Notes, as the parser and the crypto-sends pool read
            # them; whichever leg the Asset column names.
            ev = self._convert_cash_leg(row, header_map, cash_coins, where)
            evs = [ev] if ev is not None else []
        elif asset in _FIAT or asset in cash_coins:
            evs = self._currency_row_cash(r, typ, asset, cash_coins, where)
        else:
            evs = []
        for ev in evs:
            ev['_broker_id'] = cb_id
        return evs

    def _currency_row_cash(self, r, typ, asset, cash_coins, where
                           ) -> List[Dict[str, Any]]:
        """The cash events of a row of a fiat currency or a cash coin: a
        Deposit, Receive, Withdrawal or Send a CASHMOVE; a Subscription a
        FLOW; a cash coin's Buy or Sell an FXCONV, or its fee or spread a
        FLOW when it is bought or sold for US dollars. None for a row that
        moves no quantity or of another type."""
        from taxjson.lib import cash_lines as CL
        row, header_map = r.cells, self._cb.header_map
        type_raw = self._col(row, header_map, 'transaction type').strip()
        is_cash_coin = asset in cash_coins
        cur = 'USD' if is_cash_coin else asset
        qty = abs(self._num(row, header_map, 'quantity transacted'))
        if qty < 1e-9:
            return []
        day = self._ts(row, header_map, typ).date().isoformat()
        if typ in ('deposit', 'receive', 'withdrawal', 'send'):
            sign = 1.0 if typ in ('deposit', 'receive') else -1.0
            ev = CL.move(day, cur, sign * qty, where=self._cb.shown,
                         desc=f"Coinbase {typ} {asset}")
            ev['line_where'] = where
            return [ev]
        if typ == 'subscription':
            # A Coinbase One subscription paid from the account's cash
            # (backlog TJ-0870): the currency spent, at the day's rate.
            # The parser counts the row a non-event (no security moves);
            # the cash it took was in no ledger, so the year's closing
            # balance never reconciled.
            return [CL.flow(day, cur, -qty, where=where,
                            desc=f"Coinbase subscription {asset}")]
        # The row's kind as the position parser reads it (classify's
        # _BUY_RE / _SELL_RE / _STAKING_RE): "Advanced Trade Buy" is a buy.
        # The exact labels 'buy' / 'sell' alone left an Advanced Trade of
        # USDC for CAD out of the cash ledger (2026-10-24 audit C09).
        if not (is_cash_coin and (r.is_buy or r.is_sell) and not r.is_staking):
            return []
        sell = r.is_sell and not r.is_buy
        verb = 'sell' if sell else 'buy'
        pc = (self._col(row, header_map, 'price currency') or 'USD'
              ).strip().upper() or 'USD'
        pc = 'USD' if _cb_symbol(pc) in cash_coins else pc
        total = coinbase_row_total(self, row, header_map, type_raw, sell, where)
        if pc == cur:
            # USDC for USD: the same currency, no conversion — but
            # the fee or spread is US dollars spent (CA-FX-07): it
            # was dropped with the row, leaving the pool that many
            # units too large (2026-10 audit #73). The other way
            # too: 200 USDC bought for 197 USD is 3 US dollars MORE
            # in the folded pool, a FLOW in (at the day's rate; the
            # de-peg is not the coin's gain, CA-CRYPTO-02); only a
            # spread paid was booked (2026-10-24 audit C07).
            spent = (total - qty) if not sell else (qty - total)
            if abs(spent) <= 0.005:
                return []
            return [CL.flow(day, cur, -spent, where=where,
                            desc=f"Coinbase {verb} {asset} "
                                 f"{'fee' if spent > 0 else 'spread'}")]
        if not sell:
            return [CL.conv(day, pc, total, cur, qty, where=where,
                            desc=f"Coinbase {verb} {asset}")]
        return [CL.conv(day, cur, qty, pc, total, where=where,
                        desc=f"Coinbase {verb} {asset}")]

    def _convert_cash_leg(self, row, header_map, cash_coins, where: str
                          ) -> Optional[Dict[str, Any]]:
        """The US-dollar FLOW of a Convert between a stablecoin (cash,
        CA-CRYPTO-02) and a coin: - the stablecoin units spent, + those
        received, at the day's rate (lib/fx_cash_v2). None for a Convert
        with no such leg (coin for coin, stablecoin for stablecoin, a
        property-mode book) or whose Notes the parser refuses."""
        if not cash_coins:
            return None
        notes = self._col(row, header_map, 'notes') or ''
        m = _CONVERT_NOTES_RE.search(notes)
        if not m:
            return None             # the parser refuses the row
        fa, ta = _cb_symbol(m.group(2)), _cb_symbol(m.group(4))
        cash_or_fiat = set(cash_coins) | set(_FIAT)
        ctx = f"Coinbase {where} Notes {notes!r}"
        if fa in cash_coins and ta not in cash_or_fiat:
            amt = -strict_money(m.group(1), 'Convert quantity', ctx)
        elif ta in cash_coins and fa not in cash_or_fiat:
            amt = strict_money(m.group(3), 'Convert quantity', ctx)
        else:
            return None
        if abs(amt) < 1e-9:
            return None
        from taxjson.lib import cash_lines as CL
        day = self._ts(row, header_map, 'Convert').date().isoformat()
        ev = CL.flow(day, 'USD', amt, where=where,
                     desc=f"Coinbase convert {fa} to {ta}")
        ev['leg'] = 'trade'
        return ev

    # ------------------------------------------------------------ helpers
    def _num(self, row, header_map, key) -> float:
        """Strict numeric cell: `$1,234.50`, `CA$4.00`, `US$-3`,
        `(12.00)` parse; anything else raises. clean_number turned
        `CA$4.00` into 0.0 — a $0 basis/proceeds row, silently."""
        return strict_money(
            self._col(row, header_map, key), key,
            f"Coinbase row {self._col(row, header_map, 'timestamp')!r} "
            f"{self._col(row, header_map, 'transaction type')!r}")

    def _currency(self, row, header_map) -> str:
        """Price currency; a stablecoin quote is USD (cash model). Older
        US-retail exports have no currency column and are USD."""
        if 'price currency' not in header_map:
            return 'USD'
        c = (self._col(row, header_map, 'price currency') or '').strip()
        if not c:
            # The column exists but this cell is blank: defaulting to
            # USD booked a CAD row's amounts in USD, converting them a
            # second time (R1-111).
            raise ValueError(
                f"Coinbase row "
                f"{self._col(row, header_map, 'timestamp')!r} "
                f"{self._col(row, header_map, 'transaction type')!r}: "
                f"blank Price Currency cell — refusing to guess the "
                f"currency. Fill it in from the Coinbase statement.")
        c = c.upper()
        return 'USD' if _cb_symbol(c) in _STABLECOINS else c

    def _ts(self, row, header_map, what):
        """Coinbase stamps rows in UTC; converted to local wall-clock
        time (the project's local_timezone — see lib/timezones) so the
        tax year and the BoC rate day are the taxpayer's local date.
        Raises on an unparseable stamp: silently stamping or skipping
        a row distorts holding periods and year filters. If Coinbase
        ships a new timestamp format, add it here."""
        raw = self._col(row, header_map, 'timestamp')
        dt = self.parse_date(
            raw.replace(' UTC', ''),
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
        )
        if dt is None:
            raise ValueError(
                f"Coinbase {str(what).strip()} row has an unparseable "
                f"timestamp: {raw!r} (asset="
                f"{self._col(row, header_map, 'asset')!r}). Add the "
                f"format to parse_date if this is a new Coinbase export "
                f"variant.")
        st = self._cb
        if st is not None and st.row_start is not None:
            self.instant_of_line[st.row_start] = dt
        return utc_to_local(dt)

    @staticmethod
    def _col(row: Any, header_map: Dict[str, int], key: str, default: str = '') -> Any:
        idx = header_map.get(key, -1)
        if idx < 0 or idx >= len(row):
            return default
        return row[idx]


def coinbase_row_total(cb: "CoinbaseBrokerage", row, header_map,
                       type_raw: str, is_sell: bool, where: str) -> float:
    """|Total| of a Buy / Sell row for the readers the parse's rows do not
    give it (its cash events, `_currency_row_cash`, which read it of a
    stablecoin row the parse counts a non-event; the crypto-sends pool,
    lib/crypto_sends). A blank Total is derived
    as the parser derives it (`_derive_blank_total`: Subtotal +/- fee,
    else quantity x price +/- fee), and refused, naming `where`, when
    nothing is there to derive it from: read as 0 it booked a CAD-priced
    stablecoin buy as a zero-cost acquisition (audit finding 74)."""
    if cb._col(row, header_map, 'total').strip():
        return abs(cb._num(row, header_map, 'total'))
    try:
        return abs(cb._derive_blank_total(
            row, header_map, type_raw, is_sell,
            cb._num(row, header_map, 'quantity transacted'),
            cb._num(row, header_map, 'price at transaction'),
            cb._num(row, header_map, 'fees')))
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None

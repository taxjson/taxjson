from taxjson.lib.stage_msg import emit_line
import csv
import hashlib
import io
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import (BaseBrokerage, read_broker_text,
                                         shown_name)
from taxjson.lib.brokerages._crypto_common import (FIAT_CURRENCIES,
                                                   USD_STABLECOINS,
                                                   same_coin_hint,
                                                   strict_money, utc_to_local,
                                                   warn_depeg)


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
# older exports (handled via disambiguate_split_fills below).
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


class CoinbaseBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Coinbase"
    # USD stablecoins: US-dollar CASH (True — the default, Canada's
    # stated approximation, tax-logic CA-CRYPTO-02) or PROPERTY like any
    # coin (False — a US project, US-CRYPTO-02). taxjson-brokerage sets
    # it from --country; the parser itself makes no country choice.
    stablecoins_as_cash = True

    @property
    def _cash_coins(self):
        """Stablecoins booked as US-dollar cash in this book."""
        return _STABLECOINS if self.stablecoins_as_cash else frozenset()

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        self._blank_totals = 0
        # Rows of a type the parser does not book that carry coins
        # (S056-12): (type, date, asset, quantity).
        unbooked: List[tuple] = []
        self.lint_findings: List[str] = []
        self._stems: Dict[str, int] = {}
        # utf-8-sig swallows a BOM if present, plain utf-8 reads it as a
        # data byte and silently breaks the first column match.
        with io.StringIO(read_broker_text(path)) as f:
            reader = csv.reader(f)
            header = None
            header_map: Dict[str, int] = {}
            _prev_line = 0
            for row in reader:
                # The line the record STARTED on: reader.line_num is
                # where it ended, which for a cell that swallowed later
                # lines named the wrong row (re-audit A2-0584).
                row_start, _prev_line = _prev_line + 1, reader.line_num
                if not header:
                    # Stripped like resolve_header: a header written
                    # `ID, Timestamp, ...` used to be missed and the
                    # whole file parsed to 0 rows (R1-109).
                    if is_header_row(row):
                        header = row
                        # Resolve logical fields through the synonym
                        # table; refuse to run against a layout we don't
                        # recognize rather than parse zeros.
                        header_map, missing = resolve_header(header)
                        if missing:
                            raise ValueError(
                                f"Coinbase CSV {shown_name(path)}: unrecognized "
                                f"export layout — no column found for "
                                f"{', '.join(repr(m) for m in missing)}. "
                                f"Header: {header}. If this is a new "
                                f"Coinbase export variant, add its "
                                f"column names to _HEADER_SYNONYMS."
                            )
                    continue
                if not row or not any(c.strip() for c in row):
                    continue
                # Every recognized column, not only the required ones: a
                # row cut inside Total with Fees/Notes missing booked a
                # short Total and a $0 fee (re-audit A2-0249).
                _need = max(header_map.values())
                if len(row) <= _need:
                    # A truncated row: missing cells used to read as ''
                    # / 0 (a 2-cell tail vanished uncounted; a 3-4 cell
                    # one booked a qty-0 or symbol-less row) — audit
                    # R1-115.
                    raise ValueError(
                        f"Coinbase CSV {shown_name(path)} line {reader.line_num}: "
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
                        f"Coinbase CSV {shown_name(path)} line {row_start}: "
                        f"a cell spans several lines — an unterminated "
                        f"quote (e.g. in Notes) opened on this line has "
                        f"swallowed lines {row_start + 1}-"
                        f"{reader.line_num} after it. Fix the quoting in "
                        f"the file.")
                if len(row) > len(header) and any(
                        c.strip() for c in row[len(header):]):
                    raise ValueError(
                        f"Coinbase CSV {shown_name(path)} line {reader.line_num}: "
                        f"the row has {len(row)} cells but the header has "
                        f"{len(header)} — misaligned columns (an unquoted "
                        f"comma?); refusing to read it.")

                type_raw = self._col(row, header_map, 'transaction type')
                is_buy = bool(_BUY_RE.search(type_raw))
                is_sell = bool(_SELL_RE.search(type_raw))
                is_staking = bool(_STAKING_RE.search(type_raw))
                self._rows_seen = (self._rows_seen or 0) + 1
                if not (is_buy or is_sell or is_staking):
                    # A Coinbase "Convert" row is a taxable
                    # crypto-to-crypto disposition plus acquisition.
                    # The standard retail row spells both legs out in
                    # Notes ("Converted 0.05 BTC to 1.2 ETH") — emit
                    # the two-leg SELL+BUY (KNOWN_ISSUES fix template,
                    # mirroring Kraken's _build_instant_trade). A
                    # Convert row whose Notes DON'T match still fails
                    # hard: guessing an unknown layout's field
                    # semantics is how dispositions get silently
                    # mis-booked. Other unrecognized types (deposits,
                    # withdrawals, etc.) fall through to the counted
                    # skip below.
                    if 'convert' in type_raw.lower():
                        legs = self._build_convert(row, header_map)
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
                        if legs:
                            # [] = a counted non-event (ETH <-> ETH2,
                            # stablecoin <-> stablecoin), already
                            # tallied by _build_convert.
                            self.note_row_consumed()
                        transactions.extend(legs)
                        continue
                    # Send/Receive: custody EVIDENCE — emitted as
                    # TRANSFER rows so the sidecar machinery keeps
                    # them queryable (`taxjson transfers crypto`)
                    # instead of dropping them. A Send that left your
                    # ownership (gift/payment) is a taxable
                    # disposition at FMV — the parse-time note says
                    # so; Coinbase supplies the spot price, carried on
                    # the row so declaring the .tt BUYSELL is a
                    # copy-paste. Tax-event semantics still NOT
                    # assumed: only the user knows gift vs
                    # self-custody move.
                    _tl = type_raw.strip().lower()
                    if _tl in ('send', 'receive'):
                        _q = abs(self._num(row, header_map,
                                           'quantity transacted'))
                        if not _q:
                            # A blank/0 quantity used to fall through to
                            # the "unclassified type" skip, losing the
                            # custody evidence of a possible disposition
                            # (S055-06).
                            raise ValueError(
                                f"Coinbase {type_raw.strip()} row "
                                f"{self._col(row, header_map, 'timestamp')!r} "
                                f"({self._col(row, header_map, 'asset')!r}) "
                                f"has no Quantity Transacted — refusing "
                                f"to drop it. Fill it in from the "
                                f"Coinbase statement.")
                        _spot = abs(self._num(row, header_map,
                                              'price at transaction'))
                        # Same policy as every other dated row: never
                        # silently mislabel a disposition candidate as
                        # a type-skip (round-six audit finding 9).
                        _dt = self._ts(row, header_map, type_raw)
                        if _q > 0:
                            transactions.append({
                                'action': 'TRANSFER',
                                'date': _dt.strftime("%Y-%m-%d"),
                                'time': _dt.strftime("%H:%M:%S"),
                                'date_settle': _dt.strftime("%Y-%m-%d"),
                                'symbol': _cb_symbol(self._col(
                                    row, header_map, 'asset')),
                                'quantity': (-_q if _tl == 'send'
                                             else _q),
                                'currency': self._currency(
                                    row, header_map),
                                'price': _spot,
                                'net_amount': round(_q * _spot, 2),
                                'account': self.DEFAULT_ACCOUNT,
                                'description': type_raw.strip(),
                            })
                            self.note_row_consumed()
                            continue
                    _asset_u = self._col(row, header_map,
                                         'asset').strip().upper()
                    if _tl in _COIN_NONEVENT_TYPES or (
                            _tl in _FIAT_NONEVENT_TYPES
                            and _asset_u in (_FIAT | self._cash_coins)):
                        self.count_nonevent(f"type {type_raw.strip()}")
                        continue
                    # Rewards-of-unknown-type etc.: no tax-event
                    # semantics ASSUMED — but never silent. One that
                    # moves coins (an airdrop, a new reward label) is an
                    # UNBOOKED warning: echoed by `taxjson run`, fatal
                    # under --strict, a --lint failure (S056-12).
                    self.count_skip(f"type {type_raw.strip() or '?'!s}")
                    try:
                        _uq = self._num(row, header_map,
                                        'quantity transacted')
                    except ValueError:
                        _uq = 1.0           # unparseable: treat as live
                    if _uq:
                        _ud = self._col(row, header_map, 'timestamp')[:10]
                        unbooked.append((type_raw.strip() or '?', _ud,
                                         _cb_symbol(self._col(
                                             row, header_map, 'asset')),
                                         _uq))
                    continue

                # Refusing to silently stamp the row as today — that
                # would distort holding-period and year filters in ways
                # the user wouldn't catch until tax time.
                dt = self._ts(row, header_map, type_raw)

                asset = self._col(row, header_map, 'asset').strip()
                # The pair an Advanced Trade fill was on (Notes). A
                # crypto quote (ETH-BTC) is a crypto-to-crypto swap:
                # both coins change hands (R1-102 — the quote coin's
                # disposition used to be dropped silently).
                crypto_quote = None
                if (is_buy or is_sell) and not is_staking:
                    _pm = _PAIR_RE.search(
                        self._col(row, header_map, 'notes') or '')
                    if _pm and _pm.group(2).upper() not in (
                            _FIAT | self._cash_coins):
                        crypto_quote = _pm.group(2).upper()
                        if asset.upper() in self._cash_coins:
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
                    if _pc in _STABLECOINS and _pc != asset.upper():
                        crypto_quote = _pc
                if (asset.upper() in self._cash_coins
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
                        asset.upper(),
                        self._num(row, header_map,
                                  'price at transaction'),
                        abs(self._num(row, header_map,
                                      'quantity transacted')),
                        dt.strftime('%Y-%m-%d'),
                        f"Coinbase {type_raw.strip()}",
                        currency=(self._col(row, header_map,
                                            'price currency')
                                  or 'USD').strip().upper() or 'USD')
                    self.count_nonevent(
                        f"stablecoin conversion {type_raw.strip()} "
                        f"{asset.upper()} (USDC treated as USD cash, as "
                        f"on Kraken)")
                    continue
                self.note_row_consumed()
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
                        f"Coinbase CSV {shown_name(path)} line {reader.line_num}: "
                        f"a {type_raw.strip()} row with a NEGATIVE "
                        f"quantity and total — the sale signature; "
                        f"refusing to book it as a purchase.")

                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M:%S")
                symbol = _cb_symbol(asset)
                # Coinbase exports a unique per-event ID (`ID` column) for each
                # row. Preserving it as the transaction id stops the sort-stage
                # dedup from collapsing distinct events that happen to share
                # second-precision timestamp + qty + price.
                cb_id = self._col(row, header_map, 'id').strip()
                if not cb_id and crypto_quote and 'id' not in header_map:
                    # Both legs need one id stem for fill-crypto to value
                    # the swap once (re-audit A2-0998's twin).
                    cb_id = self._content_stem(row)
                where = (f"Coinbase {shown_name(path)} line {row_start} "
                         f"({type_raw.strip()} {asset} "
                         f"{self._col(row, header_map, 'timestamp')})")

                if is_staking:
                    # Two-row pattern matches Kraken's staking handling and
                    # the legacy cb_dividends.pl output: a DIVIDEND for the
                    # income event (qty*FMV) and a zero-net BUYSELL that
                    # acquires the tokens for the inventory pool. Carrying
                    # qty on the DIVIDEND lets fill_crypto_prices compute
                    # the right income value when price isn't already set.
                    #
                    # Some Coinbase staking rows ship a price but a blank
                    # Total — derive total ourselves when missing so the
                    # rewarded tokens don't enter inventory at $0 cost
                    # (fill_crypto_prices only fills when price is also
                    # zero, so a price-but-no-total row would otherwise
                    # silently double-tax on later disposition).
                    if not total and price and qty:
                        total = price * abs(qty)
                    # The reward's value is what you RECEIVED: the Subtotal
                    # (= quantity x price). Coinbase's Total adds back its
                    # staking commission ("Fees and/or Spread", ~25-35% of
                    # the gross reward) — coins you never received. Booking
                    # the Total overstated staking income and the rewarded
                    # coins' ACB by that commission.
                    _sub = abs(self._num(row, header_map, 'subtotal')) \
                        if 'subtotal' in header_map else 0.0
                    if _sub:
                        # A 10x Subtotal was 10x income and ACB with only
                        # a warn-level schema note (re-audit A2-0250).
                        self._check_qp(where, 'Subtotal', _sub, qty, price)
                        total = _sub
                    elif price and qty:
                        total = price * abs(qty)
                    div = {
                        'action': 'DIVIDEND',
                        'date': date_str, 'time': time_str, 'date_settle': date_str,
                        'symbol': symbol, 'quantity': abs(qty), 'currency': currency,
                        'price': price, 'net_amount': total, 'gross_amount': total,
                        'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
                        'description': 'Staking Reward',
                    }
                    # Stake the reward into inventory at FMV (income = cost
                    # basis). Without this the BUYSELL enters at $0 cost,
                    # and the FMV gets taxed twice: once as staking income,
                    # again as inflated capital gain when sold. Old
                    # cb_dividends.pl carried the CSV's `total_price` here
                    # for the same reason.
                    buy = {
                        'action': 'BUYSELL',
                        'date': date_str, 'time': time_str, 'date_settle': date_str,
                        'symbol': symbol, 'quantity': abs(qty), 'currency': currency,
                        'price': price, 'net_amount': total,
                        'gross_amount': total, 'fee': 0.0,
                        'account': self.DEFAULT_ACCOUNT,
                        'description': 'Staking Reward',
                    }
                    if cb_id:
                        div['id'] = f'{cb_id}-div'
                        buy['id'] = f'{cb_id}-buy'
                    transactions.append(div)
                    if asset.upper() not in self._cash_coins:
                        # A stablecoin reward is USD cash income (see
                        # _STABLECOINS): no acquisition leg — the coin is
                        # never sold as an asset, so a position would sit
                        # in the book forever (same as Kraken).
                        transactions.append(buy)
                    continue

                qty = self.signed_quantity(qty, action_is_sell=is_sell)

                derived = not self._col(row, header_map, 'total').strip()
                if derived:
                    # R1-103: a blank Total read as 0 booked $0 cost /
                    # $0 proceeds with no warning. Derive it the way
                    # Coinbase builds it, or refuse.
                    total = self._derive_blank_total(
                        row, header_map, type_raw, is_sell, qty, price,
                        fee)
                    self._blank_totals = (self._blank_totals or 0) + 1
                else:
                    # A given Total must fit the row (A2-0080), and a
                    # sale whose fee exceeds its value nets NEGATIVE
                    # whatever sign the cell carries (A2-0565).
                    total = self._checked_total(
                        where, row, header_map, is_sell, qty, price, fee,
                        total)

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
                    'gross_amount': price * abs(qty),
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
                    if _am and _am.group(5).upper() in self._cash_coins:
                        _sq = strict_money(_am.group(4), 'Notes quantity',
                                           where)
                        if _sq:
                            warn_depeg(_am.group(5).upper(),
                                       abs(total) / _sq, _sq, date_str,
                                       f"Coinbase {type_raw.strip()}",
                                       currency=currency)
                if crypto_quote:
                    transactions.extend(self._crypto_pair_legs(
                        row, header_map, tx, type_raw, is_sell,
                        crypto_quote, cb_id))
                    continue
                transactions.append(tx)
        if self._blank_totals:
            emit_line(f"note: Coinbase {shown_name(path)}: {self._blank_totals} "
                  f"Buy/Sell row(s) had a blank Total — derived from "
                  f"Subtotal ± fee (or quantity × price ± fee when "
                  f"Subtotal is blank too). Check them against the "
                  f"Coinbase statement.")
        # Older Coinbase exports have no ID column, so byte-identical
        # same-second fills would hash to the same content id and
        # `taxjson-sort --dedup` would silently delete real trades —
        # every other parser already guards this. Only needed when the
        # export lacks per-row IDs (with them, ids are already unique).
        if header and 'id' not in header_map:
            self.disambiguate_split_fills(transactions)
        if unbooked:
            kinds: Dict[str, int] = {}
            for k, _d, _a, _q in unbooked:
                kinds[k] = kinds.get(k, 0) + 1
            dates = sorted(d for _k, d, _a, _q in unbooked)
            assets = sorted({a for _k, _d, a, _q in unbooked})
            msg = (f"Coinbase {shown_name(path)}: {len(unbooked)} row(s) of "
                   f"type(s) the parser does not book "
                   f"({', '.join(f'{k} x{n}' for k, n in sorted(kinds.items()))}"
                   f"; {dates[0]}..{dates[-1]}; assets "
                   f"{', '.join(assets[:8])}) move coins — an airdrop or "
                   f"reward is income and an acquisition, a payment a "
                   f"disposition. They are NOT in the books: enter each "
                   f"via a .tt file.")
            emit_line(f"warning: UNBOOKED: {msg}")
            self.lint_findings.append(msg)
        self.emit_skip_summary(shown_name(path))
        return transactions

    def _content_stem(self, row) -> str:
        """A stable id stem for a row of an export with no ID column:
        a hash of the row's cells, numbered when the same row repeats in
        the file. Both legs of a swap carry it, so fill-crypto values the
        exchange once (re-audit A2-0998); an identical copy of the same
        export in another file gets the same stem, so dedup still folds
        it."""
        h = hashlib.sha1('\x1f'.join(c.strip() for c in row)
                         .encode('utf-8')).hexdigest()[:16]
        n = self._stems.get(h, 0) + 1
        self._stems[h] = n
        return f"cb{h}" + (f"n{n}" if n > 1 else "")

    @staticmethod
    def _fits_qp(value, qty, price) -> bool:
        qp = abs(qty) * abs(price)
        return abs(abs(value) - qp) <= _QP_TOL_ABS + _QP_TOL_REL * qp

    def _check_qp(self, where, what, value, qty, price) -> None:
        """Refuse a value that contradicts |qty| x price (A2-0022,
        A2-0250). Nothing to check without both."""
        if not qty or not price or self._fits_qp(value, qty, price):
            return
        raise ValueError(
            f"{where}: {what} {abs(value):,.2f} does not fit |Quantity| "
            f"{abs(qty):g} x Price {abs(price):g} = "
            f"{abs(qty) * abs(price):,.2f} — a wrong or shifted column; "
            f"refusing to book it. Correct the row from the Coinbase "
            f"statement.")

    def _checked_total(self, where, row, header_map, is_sell, qty, price,
                       fee, total) -> float:
        """The signed net of a Buy/Sell row whose Total is given:
        positive cost on a buy, proceeds on a sale (negative when the fee
        exceeds the value). The Total must be Subtotal +/- fee, and the
        Subtotal (or, without one, the Total itself) must fit |qty| x
        price (re-audit A2-0080). An all-zero row is refused like a
        blank Total (A2-1023): fill-crypto would re-price it at market,
        a guess."""
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
            return abs(total)
        if 'fees' not in header_map:
            # Older layouts have no fee column: the gap between Total and
            # Subtotal IS the fee, so it must be a charge of fee size
            # (Coinbase's flat minimums are a few dollars).
            implied = sub - abs(total) if is_sell else abs(total) - sub
            if not -tol <= implied <= 3.0 + _QP_TOL_REL * sub:
                raise ValueError(
                    f"{where}: Total {abs(total):,.2f} does not fit "
                    f"{'Subtotal' if has_sub else '|Quantity| x Price'} "
                    f"{sub:,.2f} (implied fee {implied:,.2f}) — a wrong or "
                    f"shifted column; refusing to book it. Correct the "
                    f"row from the Coinbase statement.")
            return abs(total)
        expected = sub - fee if is_sell else sub + fee
        if abs(abs(total) - abs(expected)) > tol:
            raise ValueError(
                f"{where}: Total {abs(total):,.2f} does not fit "
                f"{'Subtotal' if has_sub else '|Quantity| x Price'} "
                f"{sub:,.2f} {'-' if is_sell else '+'} fee {fee:,.2f} = "
                f"{expected:,.2f} — a wrong or shifted column; refusing to "
                f"book it. Correct the row from the Coinbase statement.")
        return -abs(total) if expected < 0 else abs(total)

    def _build_convert(self, row, header_map):
        """Two-leg SELL (spent) + BUY (received) for a retail Convert
        row, or None when the Notes text doesn't match the recognized
        pattern (the caller then fails hard — never guess a layout).

        FMV: the retail row carries the conversion's fiat Subtotal
        (pre-fee value); both legs use it — the swap disposes the
        spent crypto at FMV and acquires the received crypto at the
        same FMV (CRA s. 40(1) / IRS Notice 2014-21). Without a
        usable Subtotal the legs ship price=0 so taxjson-fill-crypto
        backfills the FMV, per the KNOWN_ISSUES fix template."""
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
        where = (f"Coinbase Convert "
                 f"{self._col(row, header_map, 'timestamp')!r} "
                 f"(notes {notes!r})")
        # The row's own Quantity Transacted is the Asset leg's quantity:
        # it must agree with Notes, and the row's value with that leg's
        # quantity x price (re-audit A2-0022 — a 10x Subtotal or a
        # quantity of 5 beside Notes' 0.05 booked silently).
        row_qty = abs(self._num(row, header_map, 'quantity transacted'))
        if asset_col and row_qty:
            leg_qty = from_qty if asset_col == from_raw else to_qty
            if abs(row_qty - leg_qty) > 1e-8 + 1e-6 * leg_qty:
                raise ValueError(
                    f"{where}: Quantity Transacted {row_qty:g} {asset_col} "
                    f"disagrees with Notes ({leg_qty:g}) — refusing to "
                    f"guess which to book.")
            _price = abs(self._num(row, header_map, 'price at transaction'))
            _sub = abs(self._num(row, header_map, 'subtotal'))
            _tot = abs(self._num(row, header_map, 'total'))
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
            self.count_nonevent(f"{from_raw}->{to_raw} convert (same "
                                f"property {from_asset})")
            return []
        dt = self._ts(row, header_map, 'Convert')
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M:%S")
        currency = self._currency(row, header_map)
        subtotal = abs(self._num(row, header_map, 'subtotal'))
        fee = abs(self._num(row, header_map, 'fees'))
        # Fee convention: Coinbase's Subtotal is the pre-fee FMV of the
        # conversion; Total = Subtotal + Fees. Booking Subtotal as BOTH
        # legs' net_amount excluded the fee entirely, overstating the
        # round-trip gain by the fee on every Convert. The engine reads
        # net_amount fee-inclusively (core.py convention: fee-inclusive
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
        if not cb_id and 'id' not in header_map:
            # No ID column (older exports): the legs still need one stem
            # or fill-crypto values each from its own close (A2-0998).
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
            self.count_nonevent("stablecoin <-> stablecoin convert "
                                "(USD cash)")
            return []
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
                            price, fee) -> float:
        """Total for a Buy/Sell row whose Total cell is blank. Coinbase
        builds Total = Subtotal + fee on a buy and Subtotal - fee on a
        sell; without a Subtotal, quantity x price stands in for it.
        With neither, the row's value is unknown — refuse rather than
        book $0 cost or $0 proceeds."""
        fee = abs(fee)
        sub = (abs(self._num(row, header_map, 'subtotal'))
               if self._col(row, header_map, 'subtotal').strip() else 0.0)
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
            # no fiat value. Both legs go to taxjson-fill-crypto.
            for leg in (tx, quote_leg):
                leg.update(currency='USD', price=0.0, net_amount=0.0,
                           gross_amount=0.0, fee=0.0)
            return [tx, quote_leg]
        value = abs(tx['net_amount'])
        if not value:
            raise ValueError(
                f"{where}: the row's Total is $0, so the {quote} leg has "
                f"no fair value to book — refusing. Fill in the Total "
                f"from the Coinbase statement.")
        quote_leg.update(price=round(value / q_qty, 8), net_amount=value,
                         gross_amount=value)
        return [tx, quote_leg]

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
        return 'USD' if c in _STABLECOINS else c

    def _ts(self, row, header_map, what):
        """Coinbase stamps rows in UTC; converted to local wall-clock
        time (the project's local_timezone — see _crypto_common) so the
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
        return utc_to_local(dt)

    @staticmethod
    def _col(row, header_map, key, default=''):
        idx = header_map.get(key, -1)
        if idx < 0 or idx >= len(row):
            return default
        return row[idx]

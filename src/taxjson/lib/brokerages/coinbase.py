import csv
import re
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import BaseBrokerage


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

# Transaction types the parser RECOGNIZES as non-events (lowercased):
# internal moves between the trading and staking wallets, the ETH2→ETH
# relabel, fiat funding, and the Coinbase One subscription charge.
# None is a trade or income; listing them keeps the "if any of these
# are trades/income, the parser needs a new branch" call to action for
# types the parser genuinely cannot classify.
_KNOWN_NONEVENT_TYPES = frozenset({
    'retail staking transfer', 'retail unstaking transfer',
    'retail eth deprecation', 'deposit', 'subscription',
})


class CoinbaseBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Coinbase"

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        # utf-8-sig swallows a BOM if present, plain utf-8 reads it as a
        # data byte and silently breaks the first column match.
        with open(path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = None
            header_map: Dict[str, int] = {}
            for row in reader:
                if not header:
                    if row and 'timestamp' in [c.lower() for c in row]:
                        header = row
                        raw_map = {c.lower().strip(): i
                                   for i, c in enumerate(header)}
                        # Resolve logical fields through the synonym
                        # table; refuse to run against a layout we don't
                        # recognize rather than parse zeros.
                        header_map = {}
                        for field, names in _HEADER_SYNONYMS.items():
                            for n in names:
                                if n in raw_map:
                                    header_map[field] = raw_map[n]
                                    break
                        missing = [f for f in _REQUIRED_FIELDS
                                   if f not in header_map]
                        if missing:
                            raise ValueError(
                                f"Coinbase CSV {path.name}: unrecognized "
                                f"export layout — no column found for "
                                f"{', '.join(repr(m) for m in missing)}. "
                                f"Header: {header}. If this is a new "
                                f"Coinbase export variant, add its "
                                f"column names to _HEADER_SYNONYMS."
                            )
                    continue
                if not row or len(row) < 3:
                    continue

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
                                f"Workaround: pre-edit the Convert row "
                                f"into a paired Buy+Sell "
                                f"(USD-denominated). See KNOWN_ISSUES.md."
                            )
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
                        _q = abs(self.clean_number(self._col(
                            row, header_map, 'quantity transacted')))
                        _spot = abs(self.clean_number(self._col(
                            row, header_map, 'price at transaction')))
                        _ts = self._col(row, header_map, 'timestamp')
                        _dt = self.parse_date(
                            _ts.replace(' UTC', ''),
                            "%Y-%m-%d %H:%M:%S",
                            "%Y-%m-%dT%H:%M:%SZ")
                        if _dt is None:
                            # Same policy as every other dated row:
                            # never silently mislabel a disposition
                            # candidate as a type-skip (round-six
                            # audit finding 9).
                            raise ValueError(
                                f"Coinbase {type_raw} row has an "
                                f"unparseable timestamp: {_ts!r}. Add "
                                f"the format to parse_date if this is "
                                f"a new export variant.")
                        if _q > 0:
                            transactions.append({
                                'action': 'TRANSFER',
                                'date': _dt.strftime("%Y-%m-%d"),
                                'time': _dt.strftime("%H:%M:%S"),
                                'date_settle': _dt.strftime("%Y-%m-%d"),
                                'symbol': self._col(
                                    row, header_map,
                                    'asset').replace(' ', '.'),
                                'quantity': (-_q if _tl == 'send'
                                             else _q),
                                'currency': (self._col(
                                    row, header_map, 'price currency')
                                    or 'USD'),
                                'price': _spot,
                                'net_amount': round(_q * _spot, 2),
                                'account': self.DEFAULT_ACCOUNT,
                                'description': type_raw.strip(),
                            })
                            self.note_row_consumed()
                            continue
                    if _tl in _KNOWN_NONEVENT_TYPES:
                        self.count_nonevent(f"type {type_raw.strip()}")
                        continue
                    # Rewards-of-unknown-type etc.: no tax-event
                    # semantics ASSUMED — but never silent.
                    self.count_skip(f"type {type_raw.strip() or '?'!s}")
                    continue
                self.note_row_consumed()

                time_raw = self._col(row, header_map, 'timestamp')
                dt = self.parse_date(
                    time_raw.replace(' UTC', ''),
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%SZ",
                )
                if dt is None:
                    # Refusing to silently stamp the row as today — that
                    # would distort holding-period and year filters in
                    # ways the user wouldn't catch until tax time. If
                    # Coinbase ever ships a new timestamp format, add it
                    # to the format list above and re-run.
                    raise ValueError(
                        f"Coinbase row has an unparseable timestamp: "
                        f"{time_raw!r} (type={type_raw!r}, asset="
                        f"{self._col(row, header_map, 'asset')!r}). "
                        f"Add the format to parse_date if this is a "
                        f"new Coinbase export variant."
                    )

                asset = self._col(row, header_map, 'asset')
                currency = self._col(row, header_map, 'price currency') or 'USD'
                qty = self.clean_number(self._col(row, header_map, 'quantity transacted'))
                price = self.clean_number(self._col(row, header_map, 'price at transaction'))
                fee = self.clean_number(self._col(row, header_map, 'fees'))
                total = self.clean_number(self._col(row, header_map, 'total'))

                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M:%S")
                symbol = asset.replace(' ', '.')
                # Coinbase exports a unique per-event ID (`ID` column) for each
                # row. Preserving it as the transaction id stops the sort-stage
                # dedup from collapsing distinct events that happen to share
                # second-precision timestamp + qty + price.
                cb_id = self._col(row, header_map, 'id').strip()

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
                    transactions.append(buy)
                    continue

                qty = self.signed_quantity(qty, action_is_sell=is_sell)

                # Coinbase Advanced Trade Sell rows store both qty and total
                # as negative ("money out, position out"); store the magnitude
                # so the gain engine sees positive proceeds. Matches the old
                # cb_trades.pl convention (abs($total_raw)).
                tx = {
                    'action': 'BUYSELL',
                    'date': date_str,
                    'time': time_str,
                    'date_settle': date_str,
                    'symbol': symbol,
                    'quantity': qty,
                    'currency': currency,
                    'price': price,
                    'net_amount': abs(total),
                    'gross_amount': price * abs(qty),
                    'fee': abs(fee),
                    'account': self.DEFAULT_ACCOUNT,
                }
                if cb_id:
                    tx['id'] = cb_id
                transactions.append(tx)
        # Older Coinbase exports have no ID column, so byte-identical
        # same-second fills would hash to the same content id and
        # `taxjson-sort --dedup` would silently delete real trades —
        # every other parser already guards this. Only needed when the
        # export lacks per-row IDs (with them, ids are already unique).
        if header and 'id' not in header_map:
            self.disambiguate_split_fills(transactions)
        self.emit_skip_summary(path.name)
        return transactions

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
        from_qty = self.clean_number(m.group(1))
        from_asset = m.group(2).upper()
        to_qty = self.clean_number(m.group(3))
        to_asset = m.group(4).upper()
        if from_qty <= 0 or to_qty <= 0 or from_asset == to_asset:
            return None
        # Cross-check the Asset column against the Notes legs — a
        # mismatch means an unknown layout, not a parsing choice.
        asset_col = (self._col(row, header_map, 'asset') or '').strip().upper()
        if asset_col and asset_col not in (from_asset, to_asset):
            return None
        time_raw = self._col(row, header_map, 'timestamp')
        dt = self.parse_date(
            time_raw.replace(' UTC', ''),
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
        )
        if dt is None:
            raise ValueError(
                f"Coinbase Convert row has an unparseable timestamp: "
                f"{time_raw!r} (notes={notes!r}). Add the format to "
                f"parse_date if this is a new Coinbase export variant."
            )
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M:%S")
        currency = self._col(row, header_map, 'price currency') or 'USD'
        subtotal = abs(self.clean_number(
            self._col(row, header_map, 'subtotal')))
        fee = abs(self.clean_number(self._col(row, header_map, 'fees')))
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
        if cb_id:
            sell['id'] = f'{cb_id}-sell'
            buy['id'] = f'{cb_id}-buy'
        return [sell, buy]

    @staticmethod
    def _col(row, header_map, key, default=''):
        idx = header_map.get(key, -1)
        if idx < 0 or idx >= len(row):
            return default
        return row[idx]

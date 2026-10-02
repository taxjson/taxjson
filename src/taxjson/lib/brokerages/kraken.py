import csv
import io
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         read_broker_text)
from taxjson.lib.brokerages._crypto_common import (FIAT_CURRENCIES,
                                                   USD_STABLECOINS,
                                                   strict_money, utc_to_local,
                                                   warn_depeg)


# Appended to every row-level refusal that tells the user to use a .tt
# file: the refusal aborts the whole file, so entering the .tt alone
# never unblocks the run (audit S061-04).
_TT_REMOVE = (' and remove the row from the export (the file is refused until '
      'it is gone)')
# Post-fold fiat currencies (the stablecoins have become USD by the time
# a leg is compared against this). A fill or instant trade whose BOTH
# sides are in here is a currency conversion — cash moving between
# denominations — not a disposition of property. It used to emit a
# BUYSELL of a phantom `USD`/`CAD` asset that corrupted the position
# book; now it is a recognized non-event (KNOWN_ISSUES "Kraken fiat
# conversions are not modeled"). Every fiat currency, the list shared
# with the Coinbase parser: Kraken knew only USD/CAD/EUR/GBP, so AUD,
# JPY and CHF were booked as coins (re-audit A2-0579/0580/0238/0251).
_FIAT_CURRENCIES = FIAT_CURRENCIES
# USD-pegged stablecoins (USDC, USDT, DAI, PYUSD, GUSD). A CASH-mode book
# (Canada, tax-logic CA-CRYPTO-02) folds them into US dollars; a
# PROPERTY-mode book (a US project, US-CRYPTO-02) keeps them as coins
# valued at their 1.00 USD par in a swap, a reward or a fee — all five,
# on every path (PYUSD/GUSD used to be valued by a daily close here and
# at par on Coinbase, and a swap against one by the ledger's amountusd
# on one path and par on the other: re-audit A2-1004 / A2-1020).
_STABLECOINS = tuple(sorted(USD_STABLECOINS))
_CASH_STABLECOINS = _STABLECOINS
# Currencies (not property) in a cash-mode book: fiat + the stablecoins.
_FIAT_ASSETS = FIAT_CURRENCIES | USD_STABLECOINS


# Kraken's wallet-flavour suffixes on ledger asset codes: `.S` staked,
# `.M` bonded/staked (newer), `.F` opt-in/flexible rewards, `.B`/`.P`
# (bonded / parachain variants), `.HOLD` (the USD rewards-holding
# balance). They all name the SAME property as the bare code — a
# `DOT.S` reward is DOT income and later-sold DOT. Left in place they
# were separate "assets" (or, for the legacy `staking` rows, dropped
# outright) and fill-crypto could never price `DOT.S-USD`.
_ASSET_SUFFIX_RE = re.compile(r'\.(S|M|F|B|P|HOLD)$', re.IGNORECASE)
# Legacy (pre-Earn, ~2021-23) BONDED staking codes carry the lock period
# in days before the `.S`: DOT28.S, KSM07.S, ATOM21.S, SOL03.S,
# MATIC04.S, FLOW14.S. Same property as the bare coin (audits S014-01 /
# S061-08: DOT28.S used to become its own "DOT28" pool, so the rewards
# sat in a pool nothing sold and the real coin went short). Only these
# lock periods, and only in front of `.S`, so a real coin whose ticker
# ends in digits (C98, 1INCH, 0G) is never cut.
_BONDED_STAKING_RE = re.compile(r'^([A-Z][A-Z0-9]*?)(?:03|04|07|14|21|28)'
                                r'\.S$')

# Required columns — a missing `fee`/`vol` used to read as 0 on every
# row (DictReader.get), silently booking fee-free trades. Refuse.
_TRADES_REQUIRED = ('txid', 'pair', 'time', 'type', 'price', 'cost',
                    'fee', 'vol')
_LEDGER_REQUIRED = ('txid', 'refid', 'time', 'type', 'asset', 'amount',
                    'fee')

# Ledger types that move your OWN fiat cash to or from Kraken (bank
# funding, cash-outs, wallet transfers): not a tax event. Any other fiat
# row with an amount (a credit, an adjustment) is UNBOOKED (A2-0578).
_FIAT_FUNDING_TYPES = ('deposit', 'withdrawal', 'transfer',
                       'hybridearnwithdrawal')

# Legacy (pre-Earn) moves between the spot and staking wallets: the
# coins never leave the account — a recognised non-event.
_STAKING_WALLET_MOVES = ('spottostaking', 'stakingfromspot',
                         'stakingtospot', 'spotfromstaking',
                         'spottofutures', 'spotfromfutures')


def _normalize_asset(asset: str, fold_stable: bool = True) -> str:
    """Kraken prefixes assets with Z (fiat) and X (crypto) for historical reasons.
    Strip those and treat USD-pegged stablecoins as their USD anchor.
    `fold_stable=False` keeps the stablecoin's own name — the custody-
    evidence rows must say WHAT was sent (a USDC gift is a disposition
    of USDC the property, not a USD cash movement), even though the
    trade books fold it for pricing."""
    # Upper-cased first: a hand-edited or converted export with `dot.s`
    # or `eth` would otherwise open its own pool beside DOT/ETH (R1-112).
    asset = (asset or '').strip().upper()
    asset = _BONDED_STAKING_RE.sub(r'\1', asset)
    asset = _ASSET_SUFFIX_RE.sub('', asset)
    if (len(asset) == 4 and asset.startswith('Z')
            and asset[1:] in _FIAT_CURRENCIES):
        asset = asset[1:]           # ZUSD, ZJPY, ZAUD (never ZEC: Zcash)
    # The X-prefix strip covers every classic X-prefixed Kraken asset
    # (KNOWN_ISSUES enumerated the missing ones: XLM, XMR, ZEC, XDG
    # [Dogecoin], ETC) — an unstripped `XXLM` reached
    # taxjson-fill-crypto as `XXLM-USD`, which Yahoo can't resolve.
    asset = re.sub(r'^X(XBT|ETH|LTC|XRP|XLM|XMR|ZEC|XDG|ETC|MLN|REP)$',
                   r'\1', asset)
    if asset == 'XBT':
        asset = 'BTC'
    if asset == 'XDG':
        asset = 'DOGE'      # Kraken's Dogecoin code; Yahoo wants DOGE
    if asset == 'ETH2':
        # Kraken's staked-ETH ledger code (`ETH2`, `ETH2.S`): a 1:1
        # claim on ETH that Kraken converted back to ETH at the merge
        # unlock — the same property for ACB purposes.
        asset = 'ETH'
    # Fold USD-pegged stablecoins. DAI is included so that a `BTC/DAI`
    # row resolves consistently — otherwise DAI would be flagged as
    # fiat (via `_FIAT_ASSETS`) but pass through to the gain engine
    # as a non-USD currency that downstream FX conversion can't anchor.
    if fold_stable and asset in _CASH_STABLECOINS:
        asset = 'USD'
    return asset


# Stablecoin tickers that end in "USD": a legacy concatenated pair
# ending in one is ambiguous (see _split_pair).
_USD_SUFFIX_STABLES = ('PYUSD', 'RLUSD', 'FDUSD', 'GUSD')


def _split_pair(pair: str, time_raw: str = '') -> tuple:
    """(base, quote) for a Kraken trades-CSV pair.

    Modern exports use the slashed form (`XBT/USD`). Legacy exports
    concatenate (`XXBTZUSD`, `ADAUSD`, `XETHXXBT`); the old fallback
    dumped the whole string into `base` and stamped `quote='USD'`,
    which mis-denominated every legacy row. Recognize the documented
    legacy shapes and refuse loudly on anything else — a wrong quote
    silently corrupts the disposition currency."""
    if '/' in pair:
        # Upper-cased like the legacy branch below: 'eth/usd' used to
        # keep a lower-case quote that missed every fiat check and
        # became a crypto/crypto swap with a phantom 'usd' coin (R1-112).
        base, quote = pair.split('/', 1)
        return base.strip().upper(), quote.strip().upper()
    p = pair.strip().upper()
    m = re.fullmatch(r'X([A-Z]{3,4})Z([A-Z]{3})', p)
    if m and m.group(2) in _FIAT_CURRENCIES:  # XXBTZUSD, XXBTZJPY
        return m.group(1), m.group(2)
    m = re.fullmatch(r'X([A-Z]{3,4})X([A-Z]{3,4})', p)
    if m:                                   # XETHXXBT (crypto/crypto)
        return m.group(1), m.group(2)
    # Z-prefixed fiat QUOTE behind a stablecoin/fiat base (USDTZUSD,
    # ZUSDZCAD — Kraken's actual legacy names): matched BEFORE the
    # generic concatenation, whose greedy base otherwise eats the Z and
    # mints garbage symbols like 'USDTZ'. Kept to the EXPLICIT base
    # list — a permissive Z? base would wrongly split XTZUSD (Tezos)
    # into XT/USD.
    m = re.fullmatch(r'(USDT|USDC|DAI|Z(?:USD|CAD|EUR|GBP))'
                     r'Z(USD|CAD|EUR|GBP)', p)
    if m:
        base = m.group(1)
        if base.startswith('Z') and len(base) == 4:
            base = base[1:]                 # ZUSDZCAD → USD/CAD
        return base, m.group(2)
    m = re.fullmatch(r'([A-Z0-9]{2,8})(USDC|USDT|DAI|USD|CAD|EUR|GBP)', p)
    if m and m.group(2) == 'USD' and p.endswith(_USD_SUFFIX_STABLES):
        # ETHPYUSD is ETH/PYUSD (a crypto quote), not 'ETHPY'/USD cash:
        # a stablecoin ticker that ENDS in USD makes the concatenation
        # ambiguous, and the generic split minted a truncated base and
        # booked the coin side as dollars (audit S061-00). Refused like
        # any unrecognized shape. (TUSD cannot be told apart this way:
        # XBTUSD and DOTUSD end in it too.)
        raise ValueError(
            f"Kraken trades row has an ambiguous legacy pair {pair!r} "
            f"(time={time_raw!r}): it ends in a stablecoin ticker "
            f"({', '.join(s for s in _USD_SUFFIX_STABLES if p.endswith(s))}"
            f"), so the quote may be that coin, not US dollars. "
            f"Re-export the trades with slashed pairs (ETH/PYUSD), or "
            f"write the pair with a slash in the file.")
    if m:                                   # ADAUSD, SOLUSDT
        return m.group(1), m.group(2)
    raise ValueError(
        f"Kraken trades row has an unrecognized pair format: {pair!r} "
        f"(time={time_raw!r}). The slashed form (XBT/USD) and the "
        f"documented legacy concatenations (XXBTZUSD, XETHXXBT, ADAUSD) "
        f"are supported; add the new shape to _split_pair rather than "
        f"letting the row parse with a guessed quote currency.")


def _classify_header(header_line: str) -> str:
    """'trades' | 'ledgers' | '' for a Kraken CSV's first line."""
    h = header_line.lower()
    if 'ordertxid' in h or 'pair' in h:
        return 'trades'
    if 'refid' in h or 'subtype' in h:
        return 'ledgers'
    return ''


def _lower_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {(k or '').strip().lower(): v for k, v in row.items()}


def _require_columns(fieldnames, required, path: Path, kind: str) -> None:
    have = {(h or '').strip().lower() for h in (fieldnames or [])}
    missing = [c for c in required if c not in have]
    if missing:
        raise ValueError(
            f"Kraken {kind} CSV {path.name}: required column(s) missing: "
            f"{', '.join(missing)}. Header: {list(fieldnames or [])}. "
            f"A missing fee/vol/amount column would read as 0 on every "
            f"row — re-export the full {kind} CSV from Kraken.")


def _check_row_width(raw: Dict[Any, Any], path: Path, line: int,
                     kind: str) -> None:
    """A row with fewer cells than the header (csv.DictReader fills the
    missing ones with None) or more (the extras land under the None
    key) is truncated or misaligned. The optional `fee` read a missing
    cell as 0: a reward booked gross and a coin withdrawal fee vanished
    (audit S061-12). Refuse, naming the line."""
    extra = raw.get(None) if None in raw else None
    missing = [k for k, v in raw.items() if k is not None and v is None]
    if extra or missing:
        n_header = len([k for k in raw if k is not None])
        n_row = n_header - len(missing) + len(extra or [])
        raise ValueError(
            f"Kraken {kind} CSV {path.name} line {line}: the row has "
            f"{n_row} cells but the header has {n_header} — a truncated "
            f"or misaligned row (a missing fee would read as 0). "
            f"Re-export the file or fix the row.")


def _refuse_duplicate_columns(fieldnames, path: Path, kind: str) -> None:
    """Two header cells naming one column (`fee` twice, `Cost` beside
    `cost`): csv.DictReader keeps the LAST, so a second `fee` column of
    zeros silently erased every withdrawal fee. Refused, as the generic
    and Webull parsers do (re-audit A2-0246)."""
    seen: Dict[str, int] = {}
    for h in fieldnames or []:
        k = (h or '').strip().lower()
        if k:
            seen[k] = seen.get(k, 0) + 1
    dup = sorted(k for k, n in seen.items() if n > 1)
    if dup:
        raise ValueError(
            f"Kraken {kind} CSV {path.name}: column(s) {', '.join(dup)} "
            f"appear twice in the header — refusing to guess which one "
            f"holds the values. Remove the extra column or re-export the "
            f"file.")


def _dict_rows(f, path: Path, kind: str, required=()):
    """(row, first line) for every data row of a Kraken CSV, with the
    header and each row's shape checked first:

    * a required column missing, or two columns of one name: refused;
    * a cell holding a line break: refused, naming the line the cell
      OPENED on. A stray quote that closes in a later row swallows the
      rows between into one cell — with the same cell count, so the
      width check passed and those fills/rewards vanished (re-audit
      A2-0247/0248); an unterminated quote was reported at the END of
      the span as a "truncated row" (A2-1022);
    * fewer or more cells than the header: refused (_check_row_width).
    """
    reader = csv.DictReader(f)
    if required:
        _require_columns(reader.fieldnames, required, path, kind)
    _refuse_duplicate_columns(reader.fieldnames, path, kind)
    prev_end = reader.line_num          # the header's last line
    for raw in reader:
        if not raw:
            continue
        cells = [v for k, v in raw.items() if k is not None]
        cells += list(raw.get(None) or [])
        cells = [c for c in cells if isinstance(c, str)]
        breaks = sum(c.count('\n') for c in cells)
        end = reader.line_num
        # The row began after the previous one ended (a cell cut off by
        # the end of the file also holds that last line's newline).
        start = max(prev_end + 1, end - breaks)
        prev_end = end
        if breaks or any('\r' in c for c in cells):
            raise ValueError(
                f"Kraken {kind} CSV {path.name} line {start}: a cell holds "
                f"a line break — an unterminated or stray quote on that "
                f"line swallowed the row(s) after it (through line {end}), "
                f"so they would silently be missing from the books. Fix "
                f"the quote or re-export the file.")
        _check_row_width(raw, path, start, kind)
        yield raw, start


# Ledger columns that identify one ledger ENTRY's content. Two exports
# of the same entry share its txid and these values (newer exports add
# columns such as amountusd/feecurrency, so the full row may differ).
_LEDGER_IDENTITY = ('refid', 'time', 'type', 'subtype', 'asset',
                    'amount', 'fee')


def _ledger_identity(row: Dict[str, Any]) -> tuple:
    return tuple(str(row.get(k) or '').strip().lower()
                 for k in _LEDGER_IDENTITY)


def _fee_ccy(row: Dict[str, Any], asset_name: str) -> str:
    """The currency the ledger `fee` is denominated in. Exports since
    2026 carry `feecurrency`; older ones (and blank cells) mean the
    row's own asset."""
    fc = (row.get('feecurrency') or '').strip()
    if not fc:
        return asset_name
    return _normalize_asset(fc, fold_stable=False)


class KrakenBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Kraken"
    # USD stablecoins (USDC/USDT/DAI/PYUSD/GUSD): US-dollar CASH (True — the
    # default, Canada's stated approximation, tax-logic CA-CRYPTO-02) or
    # PROPERTY like any coin (False — a US project: the IRS treats them
    # as digital assets, US-CRYPTO-02). taxjson-brokerage sets it from
    # --country; the parser itself makes no country choice.
    stablecoins_as_cash = True

    def _norm(self, asset: str) -> str:
        """The asset as this book names it: stablecoins folded to USD
        only in cash mode."""
        return _normalize_asset(asset, fold_stable=self.stablecoins_as_cash)

    @property
    def _fiat(self):
        """Currencies (not property) in this book."""
        return (_FIAT_ASSETS if self.stablecoins_as_cash
                else _FIAT_CURRENCIES)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        with io.StringIO(read_broker_text(path)) as f:
            header_line = f.readline()
        kind = _classify_header(header_line)
        if kind == 'trades':
            return self._parse_trades(path)
        if kind == 'ledgers':
            return self._parse_ledgers(path)
        return []

    # ------------------------------------------------------------ helpers
    def _num(self, row, field, context, required=False) -> float:
        raw = row.get(field)
        if required and (raw is None or not str(raw).strip()):
            raise ValueError(
                f"Kraken {context}: empty {field!r} — refusing to read "
                f"it as 0.")
        return strict_money(raw, field, f"Kraken {context}")

    def _local_dt(self, time_raw: str, what: str):
        """Kraken timestamps are UTC (`2025-06-01 12:00:00.1234`).
        Returned as America/Toronto local wall-clock time (see
        _crypto_common): the taxpayer's local DATE decides the tax year
        and the BoC rate day. Raises on an unparseable stamp — stamping
        or skipping distorts holding periods / year filters invisibly;
        a changed Kraken timestamp format must be added, not dropped."""
        dt = self.parse_date((time_raw.split('.')[0] if time_raw else ''),
                             "%Y-%m-%d %H:%M:%S")
        if dt is None:
            raise ValueError(
                f"Kraken {what} has an unparseable timestamp: "
                f"{time_raw!r}. Add the format to parse_date if this is "
                f"a new Kraken export variant.")
        return utc_to_local(dt)

    @staticmethod
    def _sibling_ledger_index(path: Path) -> Optional[Dict[str, list]]:
        """refid -> ledger rows, from every Kraken LEDGER export in the
        trades file's folder (kr_*.csv / *kraken*.csv). None when there
        is none. Several ledgers (one per year) are all indexed: a
        trades export and the ledger covering it need not share a
        filename year."""
        idx: Dict[str, list] = {}
        seen: Dict[str, tuple] = {}
        found = False
        try:
            siblings = sorted(path.parent.iterdir())
        except OSError:
            return None
        for p in siblings:
            if p == path or not p.is_file() or p.suffix.lower() != '.csv':
                continue
            n = p.name.lower()
            if not (n.startswith('kr_') or 'kraken' in n):
                continue
            try:
                _text = read_broker_text(p)
            except BrokerageParseError:
                continue        # a legacy encoding: reported when parsed
            try:
                with io.StringIO(_text) as f:
                    if _classify_header(f.readline()) != 'ledgers':
                        continue
                    f.seek(0)
                    found = True
                    for raw, _line in _dict_rows(f, p, 'ledger',
                                                 _LEDGER_REQUIRED):
                        row = _lower_row(raw)
                        ref = (row.get('refid') or '').strip()
                        if not ref:
                            continue
                        # Overlapping exports (a copy, an all-history
                        # ledger beside the yearly ones) repeat entries:
                        # the same txid is the same ledger entry, so it
                        # is indexed once. Appending it twice made every
                        # joined fill fail with a misleading "wrong
                        # account" error (R1-108 / R1-299).
                        tx_id = (row.get('txid') or '').strip()
                        if tx_id:
                            prev = seen.get(tx_id)
                            if prev is not None:
                                if prev[0] != _ledger_identity(row):
                                    raise ValueError(
                                        f"Kraken ledger exports "
                                        f"{prev[1]} and {p.name} both "
                                        f"carry ledger txid "
                                        f"{tx_id[:2]}*** with DIFFERENT "
                                        f"content — they cannot both be "
                                        f"this account's ledger; remove "
                                        f"the wrong one.")
                                continue
                            seen[tx_id] = (_ledger_identity(row), p.name)
                        idx.setdefault(ref, []).append(row)
            except UnicodeDecodeError:
                continue
        return idx if found else None

    def _ledger_trade_legs(self, row, legs, base, quote, type_, pair,
                           vol, cost, trade_fee):
        """Resolve one trades-CSV fill against its ledger rows (ledger
        refid == trade txid). Returns (base_coins, base_fee_coins,
        quote_fee): the ledger's base-coin amount (the balance
        authority — it can differ from the trades `vol` in the 8th
        decimal), the fee Kraken actually took in the BASE coin, and
        the fee in QUOTE units. Kraken's trades export reports every fee in quote
        units even when the fee was charged in the base coin (a SOL/CAD
        buy that credited vol − 0.018 SOL still says fee=4.13 CAD) —
        the ledger is the only record of which balance paid.

        Any disagreement between the two exports (legs missing, amounts
        not matching vol/cost, direction reversed, a third asset) raises:
        a mis-joined ledger must never silently rewrite a trade."""
        ctx = f"trade {row.get('txid')!r} ({pair}, {row.get('time')!r})"
        by_asset: Dict[str, list] = {}
        kfee_paid = False
        for lg in legs:
            a = self._norm(lg.get('asset') or '')
            if a == 'KFEE':
                # Legacy fee credits (promotional, no tax value): the
                # fee was paid in credits, not in either leg.
                kfee_paid = kfee_paid or bool(
                    abs(self._num(lg, 'fee', ctx))
                    or abs(self._num(lg, 'amount', ctx)))
                continue
            by_asset.setdefault(a, []).append(lg)
        unknown = sorted(set(by_asset) - {base, quote})
        b_rows, q_rows = by_asset.get(base, []), by_asset.get(quote, [])
        if unknown or len(b_rows) != 1 or len(q_rows) != 1:
            raise ValueError(
                f"Kraken {ctx}: the ledger rows with refid == this txid "
                f"do not match the pair (assets found: "
                f"{sorted(by_asset)}; expected one {base} and one "
                f"{quote} row). Refusing to guess which balance paid the "
                f"fee — check that the ledger export belongs to this "
                f"account.")
        bl, ql = b_rows[0], q_rows[0]
        b_amt = self._num(bl, 'amount', ctx, required=True)
        q_amt = self._num(ql, 'amount', ctx, required=True)
        b_fee = abs(self._num(bl, 'fee', ctx))
        q_fee = abs(self._num(ql, 'fee', ctx))
        for lg, a, fee in ((bl, base, b_fee), (ql, quote, q_fee)):
            fc = _fee_ccy(lg, _normalize_asset(lg.get('asset') or '',
                                               fold_stable=False))
            if fee and self._norm(fc) != a:
                raise ValueError(
                    f"Kraken {ctx}: ledger fee on the {a} leg is in "
                    f"{fc} (feecurrency) — a trade fee charged in a third "
                    f"currency is not supported; enter this fill via a "
                    f".tt file{_TT_REMOVE}.")
        is_buy = (type_ == 'buy')
        if (b_amt > 0) != is_buy or (q_amt < 0) != is_buy:
            raise ValueError(
                f"Kraken {ctx}: ledger direction disagrees with the "
                f"trades export (type={type_!r}, ledger {base} "
                f"{b_amt:+g}, {quote} {q_amt:+g}).")
        if abs(abs(b_amt) - abs(vol)) > max(2e-7, 1e-6 * abs(vol)):
            raise ValueError(
                f"Kraken {ctx}: ledger {base} amount {abs(b_amt):.10g} "
                f"!= trades vol {abs(vol):.10g}.")
        if abs(abs(q_amt) - abs(cost)) > max(2e-4, 1e-6 * abs(cost)):
            raise ValueError(
                f"Kraken {ctx}: ledger {quote} amount {abs(q_amt):.10g} "
                f"!= trades cost {abs(cost):.10g}.")
        if b_fee > 0:
            # Fee taken in the traded coin: the quote side paid/received
            # exactly `cost` (plus any quote fee the ledger shows).
            return abs(b_amt), b_fee, q_fee
        if kfee_paid and not q_fee:
            # The whole fee was paid with KFEE credits, which cost you
            # nothing: no fee in the basis or proceeds (the conservative
            # side). It used to be refused as "the ledger charged 0"
            # (re-audit A2-0577).
            self._kfee_fills = getattr(self, '_kfee_fills', 0) + 1
            return abs(b_amt), 0.0, 0.0
        # Fee in quote units: the trades export states it to more
        # decimals than the ledger — keep that value, but only when the
        # two agree (a disagreement means the columns are not what we
        # think they are).
        if abs(trade_fee - q_fee) > max(2e-4, 0.01 * trade_fee):
            raise ValueError(
                f"Kraken {ctx}: trades fee {trade_fee:.10g} {quote} but "
                f"the ledger charged {q_fee:.10g} {quote} and 0 {base}.")
        return abs(b_amt), 0.0, trade_fee

    @staticmethod
    def _check_fill_money(ctx, pair, price, cost, fee, vol,
                          fiat_quote) -> None:
        """Fail-closed money identity for one trades fill (the S023-19
        check the equity parsers make; re-audit A2-0080): `cost` must be
        |vol| x price (Kraken states it to the quote's precision; half a
        percent plus a cent of slack), and the fee must be fee-sized
        (Kraken's highest, the instant-buy fee, is about 1.5%; 5% is
        allowed). A shifted, swapped or 10x column used to book with at
        most a schema warning."""
        gross = abs(vol) * abs(price)
        slack = 0.01 if fiat_quote else 1e-8
        if abs(abs(cost) - gross) > slack + 0.005 * max(gross, abs(cost)):
            raise ValueError(
                f"Kraken {ctx} ({pair}): cost {abs(cost):.10g} does not "
                f"fit vol {abs(vol):.10g} x price {abs(price):.10g} = "
                f"{gross:.10g} — a wrong or shifted column; refusing to "
                f"book it. Re-export the trades or fix the row.")
        if abs(fee) > slack * 5 + 0.05 * abs(cost):
            raise ValueError(
                f"Kraken {ctx} ({pair}): fee {abs(fee):.10g} is "
                f"{abs(fee) / abs(cost) * 100 if cost else float('inf'):.1f}"
                f"% of the cost {abs(cost):.10g} — not a trading fee (a "
                f"wrong or shifted column); refusing to book it. "
                f"Re-export the trades or fix the row.")

    def _same_property_swap(self, qty_out: float, qty_in: float,
                            asset: str, where: str) -> None:
        """A swap whose two sides fold to the same asset (ETH <-> ETH2 /
        ETH2.S) is a counted non-event when the quantities match; any
        other shape (a fee in the coin, a non-1:1 rate) has no modeled
        booking and raises."""
        if abs(qty_out - qty_in) <= 1e-8 * max(qty_out, qty_in, 1e-12):
            self.count_nonevent(f"{asset} relabel swap (e.g. ETH<->ETH2, "
                                f"the same property)")
            return
        raise ValueError(
            f"Kraken {where}: a swap between two spellings of {asset} "
            f"(same property for ACB purposes) with unequal quantities "
            f"({qty_out:.10g} out, {qty_in:.10g} in) — not modeled; enter "
            f"the difference via a .tt file and remove the row.")

    # ---------------------------------------------------------------- trades
    # Kraken's *trades* CSV (the per-fill order log). Fiat-quoted pairs
    # emit one BUYSELL; crypto/crypto pairs emit the same two-leg
    # SELL+BUY pattern as the ledgers path's _build_instant_trade
    # (implemented 2026-08 per the KNOWN_ISSUES fix template).
    def _parse_trades(self, path: Path) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        # Zero-drop policy (base.py): rows we don't translate are
        # counted and summarized, never silently continued past.
        ignored_types: Dict[str, int] = {}
        ledger_idx = self._sibling_ledger_index(path)
        unverified = 0
        coin_fee_fills = 0
        margin_fills = 0
        with io.StringIO(read_broker_text(path)) as f:
            for raw, line in _dict_rows(f, path, 'trades',
                                        _TRADES_REQUIRED):
                row = _lower_row(raw)
                type_ = (row.get('type') or '').strip().lower()
                if type_ not in ('buy', 'sell'):
                    ignored_types[type_ or '?'] = (
                        ignored_types.get(type_ or '?', 0) + 1)
                    continue

                pair = row.get('pair') or ''
                time_raw = row.get('time') or ''
                dt = self._local_dt(time_raw, f"trades row (pair={pair!r})")
                ctx = f"trades {path.name} txid={row.get('txid')!r}"

                price = self._num(row, 'price', ctx, required=True)
                cost = abs(self._num(row, 'cost', ctx, required=True))
                fee = abs(self._num(row, 'fee', ctx))
                vol = self._num(row, 'vol', ctx, required=True)

                base, quote = _split_pair(pair, time_raw)
                # A stablecoin quoted against USD (USDC/USD) away from
                # the peg: the books fold it to USD cash, so say what
                # that drops (partition INPUTS-12).
                _rb = _normalize_asset(base, fold_stable=False)
                _rq = _normalize_asset(quote, fold_stable=False)
                if not self.stablecoins_as_cash:
                    pass            # property: the fill books the price
                elif _rb in _CASH_STABLECOINS and _rq == 'USD':
                    warn_depeg(_rb, price, abs(vol),
                               dt.strftime('%Y-%m-%d'),
                               f"Kraken trades {path.name}")
                elif _rq in _CASH_STABLECOINS and _rb == 'USD' and price:
                    warn_depeg(_rq, 1.0 / price, abs(cost),
                               dt.strftime('%Y-%m-%d'),
                               f"Kraken trades {path.name}")
                base = self._norm(base)
                quote = self._norm(quote)
                self._check_fill_money(ctx, pair, price, cost, fee, vol,
                                       quote in _FIAT_ASSETS)
                if (not self.stablecoins_as_cash
                        and base in _FIAT_CURRENCIES
                        and quote in _STABLECOINS):
                    raise ValueError(
                        f"Kraken trades {path.name}: pair {pair!r} "
                        f"prices dollars in a stablecoin — with "
                        f"stablecoins as property (a US project) that is "
                        f"a sale or purchase of {quote} this parser does "
                        f"not book in that orientation; enter it via a "
                        f".tt file and remove the row.")
                try:
                    _margin = float(str(row.get('margin') or 0)
                                    .replace(',', '') or 0)
                except ValueError:
                    _margin = 0.0
                if _margin:
                    margin_fills += 1


                if base in _FIAT_CURRENCIES:
                    # USD/CAD, USDC/USD (base folds to USD), USDT/CAD:
                    # a currency conversion. Emitting it as a BUYSELL
                    # of a `USD`/`CAD` "asset" put a phantom position
                    # in the book; FX cash gains are not modeled here
                    # (KNOWN_ISSUES) — count it and move on.
                    self.count_nonevent(f"forex conversion {pair} "
                                        f"(not modeled — KNOWN_ISSUES)")
                    continue

                if base == quote:
                    # Two spellings of ONE property (ETH2.S/ETH: Kraken
                    # folds ETH2 into ETH as the same property for ACB
                    # purposes). The wrap/unwrap moves nothing but the
                    # label — booking it as a sale and rebuy realized a
                    # false gain (S061-11).
                    self._same_property_swap(
                        abs(vol), abs(cost), base,
                        f"trades {path.name} line {line} "
                        f"({pair})")
                    continue

                # Which balance paid the fee (M3): join the ledger.
                _txid = (row.get('txid') or '').strip()
                legs = (ledger_idx.get(_txid)
                        if ledger_idx is not None and _txid else None)
                if legs:
                    base_coins, base_fee, quote_fee = (
                        self._ledger_trade_legs(
                            row, legs, base, quote, type_, pair, vol,
                            cost, fee))
                    if (self.stablecoins_as_cash and _rq in _CASH_STABLECOINS
                            and _rb != 'USD'):
                        # ETH/USDC: the joined ledger's amountusd on the
                        # stablecoin leg implies its price (A2-1003).
                        for _lg in legs:
                            _au = _lg.get('amountusd')
                            _am = abs(strict_money(_lg.get('amount'),
                                                   'amount', ctx))
                            if (_normalize_asset(_lg.get('asset') or '',
                                                 fold_stable=False) == _rq
                                    and _au not in (None, '') and _am):
                                warn_depeg(_rq, abs(strict_money(
                                    _au, 'amountusd', ctx)) / _am, _am,
                                    dt.strftime('%Y-%m-%d'),
                                    f"Kraken trades {path.name}")
                else:
                    base_coins, base_fee, quote_fee = abs(vol), 0.0, fee
                    unverified += 1
                if base_fee:
                    coin_fee_fills += 1
                # Coins that actually moved: a buy credits vol − coin
                # fee, a sell debits vol + coin fee.
                base_qty = (base_coins - base_fee if type_ == 'buy'
                            else base_coins + base_fee)
                fee_note = (f" (Kraken fee {base_fee:.10g} {base} taken "
                            f"in coin, per ledger)" if base_fee else '')

                if quote not in self._fiat:
                    # Crypto-to-crypto fill: two USD-denominated legs,
                    # the mirror of the ledgers path's
                    # _build_instant_trade (CRA s. 40(1) / IRS Notice
                    # 2014-21 — the swap disposes the one asset at FMV
                    # and acquires the other at the same FMV). `vol` is
                    # the base-asset quantity, `cost` the quote-asset
                    # quantity; both legs ship price=0 so
                    # taxjson-fill-crypto backfills the FMV. The fee is
                    # crypto-denominated and unconvertible to USD here,
                    # so the USD fee field stays 0 — but the fee COINS
                    # moved, so they are folded into the leg whose
                    # balance paid them rather than left as phantom
                    # units.
                    date_s = dt.strftime("%Y-%m-%d")
                    time_s = dt.strftime("%H:%M:%S")
                    base_leg = {
                        'action': 'BUYSELL',
                        'date': date_s, 'time': time_s,
                        'date_settle': date_s,
                        'symbol': base,
                        'quantity': self.signed_quantity(
                            base_qty, action_is_sell=(type_ == 'sell')),
                        'currency': 'USD', 'price': 0.0,
                        'net_amount': 0.0, 'gross_amount': 0.0,
                        'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                        'description': f'Trade {pair} '
                                       f'({type_} leg of crypto-to-crypto)'
                                       f'{fee_note}',
                    }
                    quote_leg = {
                        'action': 'BUYSELL',
                        'date': date_s, 'time': time_s,
                        'date_settle': date_s,
                        'symbol': quote,
                        # A buy spends cost + quote fee of the quote
                        # coin, a sell receives cost − quote fee.
                        'quantity': self.signed_quantity(
                            (abs(cost) + quote_fee) if type_ == 'buy'
                            else max(abs(cost) - quote_fee, 0.0),
                            action_is_sell=(type_ == 'buy')),
                        'currency': 'USD', 'price': 0.0,
                        'net_amount': 0.0, 'gross_amount': 0.0,
                        'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                        'description': f'Trade {pair} '
                                       f'(counter leg of crypto-to-crypto)',
                    }
                    _usd = None
                    if quote in _STABLECOINS:
                        # A stablecoin quote (property mode): the fill
                        # itself values the exchange — the stablecoin
                        # moved at 1.00 USD a coin (what it is
                        # redeemable for), so both legs carry that
                        # value instead of two daily closes.
                        _usd = abs(quote_leg['quantity'])
                    elif legs:
                        # The joined ledger's own USD value of the fill
                        # (amountusd, exports since 2026): ONE value for
                        # both legs, as the ledgers path books an
                        # instant swap — two daily closes gave the two
                        # sides of one exchange different values
                        # (S060-22). Without it fill-crypto values the
                        # swap once from a daily close.
                        # The received coin's row first (the ledgers
                        # path's choice), else the other leg's.
                        _order = ((base, quote) if type_ == 'buy'
                                  else (quote, base))
                        for _want in _order:
                            for _lg in legs:
                                _au = _lg.get('amountusd')
                                if (self._norm(_lg.get('asset') or '')
                                        == _want
                                        and _au not in (None, '')):
                                    _v = abs(strict_money(
                                        _au, 'amountusd', ctx))
                                    _usd = _usd or (_v if _v > 0
                                                    else None)
                            if _usd:
                                break
                    if _usd:
                        for _leg in (base_leg, quote_leg):
                            _q = abs(_leg['quantity'])
                            _leg['price'] = round(_usd / _q, 8) if _q else 0.0
                            _leg['net_amount'] = round(_usd, 8)
                            _leg['gross_amount'] = round(_usd, 8)
                    if _txid:
                        base_leg['id'] = f'{_txid}-base'
                        quote_leg['id'] = f'{_txid}-quote'
                    transactions.extend([base_leg, quote_leg])
                    continue

                qty = self.signed_quantity(base_qty,
                                           action_is_sell=(type_ == 'sell'))
                # A coin-charged fee is already out of the quantity; the
                # quote side moved exactly `cost` (quote_fee is then 0).
                net = ((cost + quote_fee) if type_ == 'buy'
                       else (cost - quote_fee))

                tx = {
                    'action': 'BUYSELL',
                    'date': dt.strftime("%Y-%m-%d"),
                    'time': dt.strftime("%H:%M:%S"),
                    'date_settle': dt.strftime("%Y-%m-%d"),
                    'symbol': base, 'quantity': qty, 'currency': quote,
                    'price': price, 'net_amount': net,
                    'gross_amount': cost, 'fee': quote_fee,
                    'account': self.DEFAULT_ACCOUNT,
                }
                if fee_note:
                    tx['description'] = f'Trade {pair}{fee_note}'
                # Preserve Kraken's per-fill txid as the transaction id. Split
                # fills land in the same second with identical qty/price/cost,
                # so without a unique id the sort-stage dedup collapses them
                # into one and the inventory loses real fills (e.g. 8 BNB
                # fills on 2025-10-17 within ~1.5s).
                if _txid:
                    tx['id'] = _txid
                transactions.append(tx)
        if ignored_types:
            # A fill whose type is blank or not buy/sell has no booking:
            # an UNBOOKED warning (echoed by `taxjson run`, fatal under
            # --strict) as on the ledger and Coinbase twins — it was a
            # quiet `note:` only the .sum showed (re-audit A2-0245).
            detail = ', '.join(f"{k} x{v}"
                               for k, v in sorted(ignored_types.items()))
            print(f"warning: UNBOOKED: Kraken trades {path.name}: "
                  f"{sum(ignored_types.values())} row(s) whose type is "
                  f"neither buy nor sell ({detail}) are NOT in the books "
                  f"— fix the type cell, or enter each fill via a .tt "
                  f"file{_TT_REMOVE}.", file=sys.stderr)
        if unverified:
            where = ("no Kraken ledgers export (kr_ledgers*.csv) is in "
                     "the same folder" if ledger_idx is None else
                     "the ledgers export(s) beside it do not contain "
                     "them")
            print(f"warning: Kraken trades {path.name}: the fee currency "
                  f"of {unverified} fill(s) can't be verified — {where}. "
                  f"Kraken's trades CSV states every fee in quote units "
                  f"even when Kraken took it in the traded coin, which "
                  f"books phantom coins; add the ledgers export covering "
                  f"these dates beside the trades file.",
                  file=sys.stderr)
        if margin_fills:
            print(f"warning: Kraken trades {path.name}: {margin_fills} "
                  f"fill(s) carry a nonzero `margin` value — they are "
                  f"booked as ordinary SPOT buys/sells. The margin "
                  f"position's rollover (financing) and settlement "
                  f"ledger rows are NOT modeled; hand-check these "
                  f"positions (R1-107).", file=sys.stderr)
        if getattr(self, '_kfee_fills', 0):
            print(f"note: Kraken trades {path.name}: {self._kfee_fills} "
                  f"fill(s) paid their fee with Kraken fee credits (KFEE) "
                  f"— booked with no fee (the credits cost nothing).",
                  file=sys.stderr)
        if coin_fee_fills:
            print(f"note: Kraken trades {path.name}: {coin_fee_fills} "
                  f"fill(s) had the fee taken in the traded coin (per the "
                  f"ledger) — booked as fewer coins received / more "
                  f"coins given, with no quote-currency fee; those fees "
                  f"are therefore NOT in the fee reports (fees.rpt, "
                  f"fees-sum, the .sum FEES line).",
                  file=sys.stderr)
        self.emit_skip_summary(path.name)
        return transactions

    # --------------------------------------------------------------- ledgers
    def _parse_ledgers(self, path: Path) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        # refid -> {'spend': {asset: leg}, 'receive': {asset: leg}}
        instant_trades: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # type -> count of ledger rows we don't translate (deposit,
        # withdrawal, transfer, margin, ...). Summarized once at end of
        # parse — previously silently dropped.
        ignored_types: Dict[str, int] = {}
        # refid -> local date of the ledger's `trade` rows; checked
        # against the trades export after the loop (R1-104).
        trade_refids: Dict[str, str] = {}
        # txid -> identity of the first row seen with it (S013-09).
        seen_txids: Dict[str, tuple] = {}
        repeated = 0
        # Rows of types the parser does not book that MOVE property (or
        # margin P&L): (type/subtype, date, asset, amount).
        unbooked: List[tuple] = []

        with io.StringIO(read_broker_text(path)) as f:
            for raw, line in _dict_rows(f, path, 'ledger',
                                        _LEDGER_REQUIRED):
                row = _lower_row(raw)
                txid = (row.get('txid') or '').strip()
                if txid:
                    # A txid names ONE ledger entry: a repeated row
                    # (overlapping exports pasted into one file) is the
                    # same entry, not a second settlement. Summing it
                    # into an instant trade doubled the trade silently
                    # (S013-09); a genuine split settlement has distinct
                    # txids and still sums.
                    ident = _ledger_identity(row)
                    prev = seen_txids.get(txid)
                    if prev is not None:
                        if prev != ident:
                            raise ValueError(
                                f"Kraken ledger {path.name} line "
                                f"{line}: ledger txid "
                                f"{txid[:2]}*** appears twice with "
                                f"DIFFERENT content — the file is "
                                f"corrupt or two accounts' exports were "
                                f"merged; fix it before parsing.")
                        repeated += 1
                        continue
                    seen_txids[txid] = ident
                refid = (row.get('refid') or '').strip()
                type_raw = (row.get('type') or '').strip().lower()
                subtype = (row.get('subtype') or '').strip().lower()
                asset_raw = row.get('asset') or ''
                time_raw = row.get('time') or ''
                dt = self._local_dt(
                    time_raw, f"ledger row (type={type_raw!r}, "
                              f"asset={asset_raw!r})")
                date = dt.strftime("%Y-%m-%d")
                time = dt.strftime("%H:%M:%S")
                ctx = f"ledger {path.name} txid={txid!r}"
                amount = self._num(row, 'amount', ctx, required=True)
                fee = self._num(row, 'fee', ctx)
                asset = self._norm(asset_raw)
                asset_name = _normalize_asset(asset_raw, fold_stable=False)
                fee_ccy = _fee_ccy(row, asset_name)

                if ((type_raw == 'earn' and subtype == 'reward')
                        or type_raw in ('staking', 'dividend')):
                    # Earn rewards (2023+), the legacy `staking` reward
                    # rows (asset `DOT.S`, `ETH2.S`, …) and `dividend`
                    # rows (opt-in rewards, e.g. on `USD.HOLD`) are all
                    # income at FMV when credited; the coins are
                    # acquired at that FMV. The legacy types used to be
                    # dropped into the "unhandled" note — income lost.
                    if amount <= 0:
                        raise ValueError(
                            f"Kraken {ctx}: {type_raw}/{subtype} row "
                            f"with a non-positive amount ({amount}) — a "
                            f"reward reversal is not modeled; hand-check "
                            f"it, enter the correction via a .tt file"
                            f"{_TT_REMOVE}.")
                    transactions.extend(self._income_row(
                        row, ctx, asset_name, fee_ccy, amount, fee,
                        date, time, txid))
                elif (type_raw == 'earn' and subtype in (
                        'allocation', 'deallocation', 'autoallocation',
                        # A move between two Earn programs (paired rows,
                        # the coins stay yours) — re-audit A2-1017.
                        'migration')
                      ) or (type_raw.startswith('hybridearn')
                            and type_raw != 'hybridearnwithdrawal'
                      ) or (type_raw == 'transfer'
                            and subtype in _STAKING_WALLET_MOVES):
                    # Moves between the spot and Earn/staking wallets
                    # (and the Hybrid Earn product): the coins never
                    # leave your ownership, so no acquisition,
                    # disposition or custody event — a recognised
                    # non-event, counted rather than listed as
                    # "unhandled".
                    self.count_nonevent("Kraken Earn wallet move "
                                        "(allocation/deallocation)")
                    if fee:
                        # A fee on a wallet move: those coins DID leave
                        # (a disposition) — no observed export carries
                        # one, so it has no booking; never silent
                        # (audit S061-15).
                        unbooked.append((f"{type_raw}/{subtype} fee",
                                         date, fee_ccy, -abs(fee)))
                elif ((type_raw in ('withdrawal', 'deposit',
                                    # NOT an Earn-wallet shuffle: in
                                    # real exports it carries a funding
                                    # (FT…) refid like a withdrawal, has
                                    # NO counter-leg in any earn wallet
                                    # (real allocations are paired rows),
                                    # and sweeps the spot balance to
                                    # dust — the coins left this ledger.
                                    # Kept as custody evidence, same as
                                    # a withdrawal, instead of vanishing
                                    # as a non-event.
                                    'hybridearnwithdrawal')
                       # Peer-to-peer transfers (Kraken "send to a
                       # Kraken user") leave custody exactly like a
                       # withdrawal: same evidence row, same send NOTE.
                       or (type_raw == 'transfer'
                           and subtype == 'transferpeertopeer'))
                      and asset_name not in _FIAT_CURRENCIES):
                    # True FIAT movements (bank funding/cash-outs)
                    # fall through to the ignored-types note below —
                    # moving your own cash is neither custody evidence
                    # of property nor a disposition, and the FMV note
                    # would be wrong tax advice for it (round-six
                    # audit finding 4). Stablecoins are PROPERTY and
                    # stay in the evidence branch.
                    # Custody EVIDENCE, not tax events: emitted as
                    # TRANSFER rows so the standard machinery keeps
                    # them queryable (`taxjson transfers crypto` via
                    # the sidecar) instead of silently dropping them.
                    # An off-platform SEND that left your ownership
                    # (gift/payment) is a taxable disposition at FMV —
                    # the parse-time note downstream says so; the
                    # ledger carries no fiat value, so price/net stay
                    # 0 (declare the disposition as a .tt BUYSELL).
                    # Kraken Earn allocation shuffles keep their own
                    # type/subtype (paired rows) and do NOT land here.
                    desc = (f"{type_raw}/{subtype}" if subtype
                            else type_raw)
                    # The fee is in COINS (or feecurrency): it is named
                    # in the description, never put in the money `fee`
                    # field of a row stamped USD — `taxjson fees` read
                    # 25 XRP as 25 USD (audit S061-17). The coins
                    # themselves are disposed of below.
                    if fee:
                        desc += f" (fee {abs(fee):.10g} {fee_ccy})"
                    tx = {
                        'action': 'TRANSFER',
                        'date': date, 'time': time,
                        'date_settle': date,
                        'symbol': asset_name,
                        'quantity': amount,
                        'currency': 'USD', 'price': 0.0,
                        'net_amount': 0.0, 'fee': 0.0,
                        'account': self.DEFAULT_ACCOUNT,
                        'description': desc,
                    }
                    if fee:
                        # The coin fee as data for `taxjson transfers`
                        # (its FEE column was empty, audit A2-0663).
                        tx['fee_qty'] = abs(fee)
                        tx['fee_currency'] = fee_ccy
                    if txid:
                        tx['id'] = f'{txid}-xfer'
                    transactions.append(tx)
                    self.note_row_consumed()
                    transactions.extend(self._fee_coin_sale(
                        row, ctx, fee, fee_ccy, type_raw, date, time, txid))
                elif type_raw in ('spend', 'receive') and refid:
                    if fee and self._norm(fee_ccy) != asset:
                        raise ValueError(
                            f"Kraken {ctx}: instant-trade {type_raw} "
                            f"leg in {asset_name} with its fee in "
                            f"{fee_ccy} (feecurrency) — not supported; "
                            f"enter this trade via a .tt file{_TT_REMOVE}.")
                    if ((type_raw == 'spend' and amount > 0)
                            or (type_raw == 'receive' and amount < 0)):
                        # The leg's sign contradicts its type: the amount
                        # used to be taken as abs(), booking an inverted
                        # trade as an ordinary buy (re-audit A2-1019).
                        raise ValueError(
                            f"Kraken {ctx}: a {type_raw} row with amount "
                            f"{amount:+g} {asset_name} — the sign "
                            f"contradicts the type (a spend is negative, "
                            f"a receive positive). Refusing to guess the "
                            f"direction; fix the row or re-export the "
                            f"ledger.")
                    sides = instant_trades.setdefault(
                        refid, {'spend': {}, 'receive': {}})
                    side = sides[type_raw]
                    usd = row.get('amountusd')
                    usd_v = (abs(strict_money(usd, 'amountusd', ctx))
                             if usd not in (None, '') else None)
                    if (self.stablecoins_as_cash and usd_v and amount
                            and asset_name in _CASH_STABLECOINS):
                        # The ledger's own USD value of a stablecoin leg
                        # implies its price: a swap far off the peg is
                        # said, as a stablecoin/USD fill is (CA-CRYPTO-02;
                        # re-audit A2-1003).
                        warn_depeg(asset_name, usd_v / abs(amount),
                                   abs(amount), date,
                                   f"Kraken ledger {path.name}")
                    if asset in side:
                        # Split settlement: a refid can carry two rows
                        # of the same leg — assignment silently
                        # discarded the earlier amount.
                        leg = side[asset]
                        leg['amount'] += abs(amount)
                        leg['fee'] += abs(fee)
                        if leg['usd'] is not None and usd_v is not None:
                            leg['usd'] += usd_v
                        else:
                            leg['usd'] = None
                    else:
                        side[asset] = {
                            'date': date, 'time': time, 'asset': asset,
                            'amount': abs(amount), 'fee': abs(fee),
                            'usd': usd_v,
                        }
                else:
                    if type_raw == 'trade':
                        trade_refids.setdefault(refid or f'?{txid}', date)
                    elif (asset_name in _FIAT_CURRENCIES
                          and type_raw in _FIAT_FUNDING_TYPES):
                        # Your own cash moving to or from Kraken: not a
                        # tax event. A fee on it charged in a COIN
                        # (feecurrency) is still a sale of those coins
                        # (re-audit A2-1018), as on a coin withdrawal.
                        transactions.extend(self._fee_coin_sale(
                            row, ctx, fee, fee_ccy, type_raw, date, time,
                            txid))
                    elif (abs(amount) > 0 or fee
                          or type_raw in ('margin', 'rollover',
                                          'settled')):
                        # A fiat CREDIT or ADJUSTMENT (a promotion, a
                        # compensation) is not your own cash moving —
                        # income or a rebate is the owner's call
                        # (A2-0578); a coin row that moves nothing but
                        # a fee still took those coins (A2-1002).
                        # Moves PROPERTY (an airdrop, a forced
                        # conversion of a delisted coin, an adjustment,
                        # a spend/receive with no refid) or margin P&L /
                        # financing — a real tax event this parser has
                        # no booking for. It used to hide behind the
                        # "transfers don't affect gains" note (R1-107).
                        _kind = (f"{type_raw}/{subtype}" if subtype
                                 else (type_raw or '?'))
                        if amount:
                            unbooked.append((_kind, date, asset_name,
                                             amount))
                        else:
                            unbooked.append((f"{_kind} fee", date, fee_ccy,
                                             -abs(fee)))
                        continue
                    ignored_types[type_raw or '?'] = ignored_types.get(type_raw or '?', 0) + 1

        # Ledger `trade` rows are NOT parsed here (the trades export is
        # the authoritative per-fill record) — the generic "transfers
        # don't affect gains" wording was actively misleading for a
        # ledgers-only user whose actual trades were being dropped.
        trade_rows = ignored_types.pop('trade', 0)
        if trade_rows:
            self._check_trade_coverage(path, trade_rows, trade_refids)
        if repeated:
            print(f"note: Kraken ledger {path.name}: {repeated} repeated "
                  f"ledger row(s) (same txid and content as an earlier "
                  f"row — overlapping exports pasted together) skipped.",
                  file=sys.stderr)
        if unbooked:
            kinds: Dict[str, int] = {}
            for k, _d, _a, _m in unbooked:
                kinds[k] = kinds.get(k, 0) + 1
            dates = sorted(d for _k, d, _a, _m in unbooked)
            assets = sorted({a for _k, _d, a, _m in unbooked})
            print(f"warning: UNBOOKED: Kraken ledger {path.name}: "
                  f"{len(unbooked)} row(s) of type(s) the parser does not "
                  f"book ({', '.join(f'{k} x{n}' for k, n in sorted(kinds.items()))}"
                  f"; {dates[0]}..{dates[-1]}; assets "
                  f"{', '.join(assets[:8])}) move property or margin "
                  f"P&L — an airdrop is an acquisition, a conversion or "
                  f"sale a disposition. They are NOT in the books: enter "
                  f"each via a .tt file.", file=sys.stderr)
        if ignored_types:
            detail = ', '.join(f"{k} x{v}" for k, v in sorted(ignored_types.items()))
            print(
                f"note: Kraken ledger {path.name}: ignored "
                f"{sum(ignored_types.values())} fiat-cash or zero-amount "
                f"row(s) "
                f"({detail}) — moving your own cash to or from Kraken (a "
                f"bank deposit, withdrawal or transfer), or a row that "
                f"moves nothing, is not a tax event.",
                file=sys.stderr,
            )
            if not unbooked and not getattr(self, 'zero_tx_reason', None):
                # A ledger of only cash moves books nothing, correctly:
                # not the "parsed to 0 transactions" regression warning
                # (and not a `run --strict` refusal) — audit A2-0703.
                self.zero_tx_reason = (
                    f"{sum(ignored_types.values())} fiat-cash or "
                    f"zero-amount row(s): not tax events")
        for refid, sides in instant_trades.items():
            transactions.extend(self._build_instant_trades(sides, refid))
        nonevents = sum(n for c, n in self._skip_counts.items()
                        if c.startswith(self.KNOWN_NONEVENT_PREFIX))
        if (not transactions and not unbooked
                and not getattr(self, 'zero_tx_reason', None)
                and (nonevents or ignored_types)
                and not any(not c.startswith(self.KNOWN_NONEVENT_PREFIX)
                            for c in self._skip_counts)):
            # Every row is a recognized non-event (an ETH<->ETH2 relabel,
            # an Earn wallet move, a fiat deposit): the parser worked,
            # so no "parsed to 0 transactions" warning, which `run
            # --strict` refuses (re-audit A2-0583).
            self.zero_tx_reason = (
                f"its {nonevents + sum(ignored_types.values())} row(s) "
                f"are recognized non-events")
        self.emit_skip_summary(path.name)
        return transactions

    def _fee_coin_sale(self, row, ctx, fee, fee_ccy, type_raw, date, time,
                       txid, fee_usd=None) -> List[Dict[str, Any]]:
        """[the sale of the fee coins] when a ledger fee was taken IN A
        COIN, else []. A network/withdrawal fee Kraken took in a coin
        (0.002 TAO on a 0.1 TAO withdrawal; or, with `feecurrency`, in
        another coin — audit S061-16) left your ownership: a disposition
        at FMV (tax-logic CA-CRYPTO-03 / US-CRYPTO-03). The same for a
        fee in a coin on a fiat withdrawal (A2-1018) or on a reward
        (A2-0582): without this row the fee coins stayed in the book
        forever. Fiat fees, and stablecoin fees in a cash-mode book, are
        cash (see _normalize_asset) and need no row. Valued at 1.00 USD
        for a stablecoin in property mode, else at `fee_usd` / the
        ledger's `feeusd` (the exchange's own figure, S060-22), else
        price 0 for taxjson-fill-crypto."""
        fee_sym = self._norm(fee_ccy)
        if not fee or fee_sym in self._fiat:
            return []
        fee_tx = {
            'action': 'BUYSELL',
            'date': date, 'time': time,
            'date_settle': date,
            'symbol': fee_sym,
            'quantity': -abs(fee),
            'currency': 'USD', 'price': 0.0,
            'net_amount': 0.0, 'gross_amount': 0.0,
            'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
            'description': (f"Kraken {type_raw} fee paid in {fee_ccy} "
                            f"(disposed at FMV)"),
        }
        if fee_sym in _STABLECOINS:
            _v = abs(fee)
        elif fee_usd is not None:
            _v = abs(fee_usd)
        else:
            _fusd = row.get('feeusd')
            _v = (abs(strict_money(_fusd, 'feeusd', ctx))
                  if _fusd not in (None, '') else 0.0)
        if _v > 0:
            fee_tx['price'] = round(_v / abs(fee), 8)
            fee_tx['net_amount'] = round(_v, 8)
            fee_tx['gross_amount'] = round(_v, 8)
        if txid:
            fee_tx['id'] = f'{txid}-fee'
        return [fee_tx]

    def _check_trade_coverage(self, path: Path, trade_rows: int,
                              trade_refids: Dict[str, str]) -> None:
        """Every ledger `trade` refid must be a fill in a trades export
        beside the ledger — the trades export is what books them. One
        that covers a shorter date range used to drop every sale in the
        gap behind the same note a complete run prints (R1-104). Now
        the gap is an UNBOOKED warning (echoed to the console by
        `taxjson run`, fatal under `run --strict`) with counts, dates
        and masked refids."""
        txids = self._sibling_trades_txids(path)
        missing = sorted((d, r) for r, d in trade_refids.items()
                         if txids is None or r not in txids)
        if not missing:
            self.zero_tx_reason = (f"its {trade_rows} trade row(s) are "
                                   f"booked from the trades export")
            print(f"note: Kraken ledger {path.name}: {trade_rows} trade "
                  f"row(s) ({len(trade_refids)} trade(s)) are booked from "
                  f"the trades export beside it — every one matched.",
                  file=sys.stderr)
            return
        dates = [d for d, _ in missing]
        masked = ', '.join(f"{r[:2]}***" for _, r in missing[:5])
        more = f" +{len(missing) - 5} more" if len(missing) > 5 else ''
        why = ("no Kraken trades export (kr_*.csv / *kraken*.csv) is "
               "in the same folder" if txids is None else
               "the trades export(s) beside it do not contain them — "
               "they likely cover a shorter date range")
        print(f"warning: UNBOOKED: Kraken ledger {path.name}: "
              f"{len(missing)} trade(s) of {len(trade_refids)} "
              f"({dates[0]}..{dates[-1]}; refids {masked}{more}) are NOT "
              f"booked — {why}. The ledger's trade rows are not parsed; "
              f"supply the trades export covering the ledger's dates, "
              f"beside it.", file=sys.stderr)

    @staticmethod
    def _sibling_trades_txids(path: Path) -> Optional[set]:
        """Every txid in the Kraken TRADES export(s) in the ledger's
        folder, or None when there is none."""
        txids: set = set()
        found = False
        try:
            siblings = sorted(path.parent.iterdir())
        except OSError:
            return None
        for p in siblings:
            if p == path or not p.is_file() or p.suffix.lower() != '.csv':
                continue
            n = p.name.lower()
            if not (n.startswith('kr_') or 'kraken' in n):
                continue
            try:
                _text = read_broker_text(p)
            except BrokerageParseError:
                continue        # a legacy encoding: reported when parsed
            try:
                with io.StringIO(_text) as f:
                    if _classify_header(f.readline()) != 'trades':
                        continue
                    f.seek(0)
                    found = True
                    for raw, _line in _dict_rows(f, p, 'trades'):
                        t = (_lower_row(raw).get('txid') or '').strip()
                        if t:
                            txids.add(t)
            except UnicodeDecodeError:
                continue
        return txids if found else None

    def _income_row(self, row, ctx, asset_name, fee_ccy, amount, fee,
                    date, time, txid):
        """DIVIDEND (+ acquisition) rows for one reward credit.

        Same-currency fee (every real export so far: feecurrency ==
        asset): Kraken's balance moves by amount − fee — a 0.20 SOL
        reward with a 0.05 SOL commission credits 0.15 SOL.
        Booking the gross minted phantom units and overstated the
        income by the commission; both the income and the acquired
        quantity are the NET coins, the USD value net likewise
        (amountusd − feeusd).

        Fee in ANOTHER currency (`feecurrency` != asset): the coins are
        credited in full (acquired at the full FMV) and the commission —
        valued in USD from the fee itself when it is USD, else from
        `feeusd` — reduces the income only. Without a USD value for the
        fee we refuse rather than guess."""
        gross_qty = abs(amount)
        fee_qty = abs(fee)
        usd_raw = row.get('amountusd')
        usd_value = (abs(strict_money(usd_raw, 'amountusd', ctx))
                     if usd_raw not in (None, '') else None)
        feeusd_raw = row.get('feeusd')
        fee_usd = (abs(strict_money(feeusd_raw, 'feeusd', ctx))
                   if feeusd_raw not in (None, '') else None)
        same_ccy = (not fee_qty) or (fee_ccy == asset_name)
        if same_ccy:
            net_qty = gross_qty - fee_qty
            if net_qty <= 1e-12:
                self.count_nonevent(
                    "Kraken Earn reward fully consumed by its fee")
                return []
            net_usd = None
            if usd_value:
                f_usd = fee_usd or 0.0
                if fee_qty and not f_usd:
                    # No feeusd column: scale the gross value.
                    f_usd = usd_value * fee_qty / gross_qty
                net_usd = usd_value - f_usd
            return self._build_staking_reward(
                asset_name, date, time, net_qty, txid,
                usd_value=net_usd, gross_qty=gross_qty, fee_qty=fee_qty)
        # Fee charged in a different currency.
        if ((self._norm(fee_ccy) == 'USD' or fee_ccy in _STABLECOINS)
                and fee_usd is None):
            fee_usd = fee_qty
        if usd_value is None or fee_usd is None:
            raise ValueError(
                f"Kraken {ctx}: reward in {asset_name} with its "
                f"{fee_qty:g} fee charged in {fee_ccy} (feecurrency) and "
                f"no amountusd/feeusd to value it — refusing to guess the "
                f"net income. Enter this reward via a .tt file{_TT_REMOVE}.")
        # The commission reduces the income, and the fee COINS left the
        # account: a sale of them at that value (A2-0582) — they used to
        # stay in the book.
        return self._build_staking_reward(
            asset_name, date, time, gross_qty, txid,
            usd_value=usd_value, income_usd=usd_value - fee_usd,
            fee_note=f"{fee_qty:.10g} {fee_ccy}") + self._fee_coin_sale(
                row, ctx, fee_qty, fee_ccy, 'reward', date, time, txid,
                fee_usd=fee_usd)

    def _build_staking_reward(self, asset, date, time, qty, txid='',
                              usd_value=None, gross_qty=None, fee_qty=0.0,
                              income_usd=None, fee_note=''):
        # Carry the reward qty on the DIVIDEND record so fill_crypto_prices
        # computes income as qty*FMV. Without it the prices-filler defaults
        # qty to 1.0 and a $4k ETH reward of 0.001 ETH ends up as $4k income.
        # `usd_value` (the ledger's amountusd) prices BOTH legs here — the
        # income and the acquisition cost are the same FMV — and the fill
        # step leaves a priced row alone. `income_usd` overrides the
        # income side only (a commission charged in another currency
        # reduces the income, not the coins' cost).
        # `qty` is the NET coins credited (gross reward − Kraken's in-kind
        # commission); the commission is recorded in the description,
        # never in the USD `fee` field (it is coin-denominated and
        # already out of the quantity).
        desc = 'Staking Reward'
        if fee_qty and gross_qty:
            desc = (f"Staking Reward (net of Kraken commission: gross "
                    f"{gross_qty:.10g} - fee {fee_qty:.10g} {asset})")
        elif fee_note:
            desc = f"Staking Reward (Kraken commission {fee_note})"
        div = {
            'action': 'DIVIDEND',
            'date': date, 'time': time, 'date_settle': date,
            'symbol': asset, 'quantity': qty, 'currency': 'USD',
            'net_amount': 0.0, 'gross_amount': 0.0,
            'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
        buy = {
            'action': 'BUYSELL',
            'date': date, 'time': time, 'date_settle': date,
            'symbol': asset, 'quantity': qty, 'currency': 'USD',
            'price': 0.0, 'net_amount': 0.0, 'fee': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
        if txid:
            # Suffix distinguishes the paired emissions so the sort-stage
            # dedup keeps both.
            div['id'] = f'{txid}-div'
            buy['id'] = f'{txid}-buy'
        if asset in _STABLECOINS and not self.stablecoins_as_cash:
            # Property mode (a US project): income at 1.00 USD a coin,
            # and the coins are acquired at that value.
            inc = qty if income_usd is None else income_usd
            for leg in (div, buy):
                leg['price'] = round(inc / qty, 8) if qty else 1.0
                leg['net_amount'] = inc
                leg['gross_amount'] = inc
            return [div, buy]
        if ((self.stablecoins_as_cash and asset in _CASH_STABLECOINS)
                or asset in _FIAT_CURRENCIES):
            # Worth 1.0/unit by definition: income = qty, priced here.
            # NO acquisition leg: the trade books fold the stablecoins to
            # USD (the coin is later SPENT as a fiat quote, never sold
            # as an asset), so a USDC position would sit in the book
            # forever as a phantom long that nothing ever closes. Fiat
            # rewards (`dividend` on USD.HOLD) are cash income in their
            # own currency.
            inc = qty if income_usd is None else income_usd
            if asset in _FIAT_CURRENCIES:
                div['currency'] = asset
            div['price'] = 1.0
            div['net_amount'] = inc
            div['gross_amount'] = inc
            return [div]
        if usd_value is not None and qty > 1e-12:
            price = round(usd_value / qty, 8)
            for leg in (div, buy):
                leg['price'] = price
                leg['net_amount'] = round(usd_value, 8)
            div['gross_amount'] = round(usd_value, 8)
            if income_usd is not None:
                div['net_amount'] = round(income_usd, 8)
                div['gross_amount'] = round(income_usd, 8)
        return [div, buy]

    def _build_instant_trades(self, sides, refid=''):
        """Instant trades / dust sweeps for one refid. One spend leg and
        one receive leg is the normal shape. A DUST SWEEP spends several
        assets for one receipt (e.g. CAD, ADA, AVAX and BNB dust swept
        into a dollar or two of USD): the received value is split across the
        spend legs by each leg's `amountusd` (equally when the export
        has none) and every disposed asset is booked. The old model
        kept one spend leg and dropped the rest, leaving the swept
        coins in the book forever. Several legs on BOTH sides has no
        defensible split and raises."""
        spends = list(sides['spend'].values())
        recvs = list(sides['receive'].values())
        if not spends or not recvs:
            for leg_type, legs in (('spend', spends), ('receive', recvs)):
                for leg in legs:
                    missing = 'receive' if leg_type == 'spend' else 'spend'
                    print(f"warning: UNBOOKED: Kraken ledger refid "
                          f"{refid[:2]}***: orphan "
                          f"{leg_type} row ({leg['asset']} "
                          f"{leg['amount']:g} on {leg['date']}) has no "
                          f"matching {missing} leg — row SKIPPED. A lone "
                          f"spend is a real taxable disposition (and a lone "
                          f"receive an acquisition with basis); this event "
                          f"is NOT in the output — find the missing "
                          f"counter-leg (truncated export?) or enter the "
                          f"trade manually via a .tt file.",
                          file=sys.stderr)
                    self.count_skip(f"orphan {leg_type} (refid {refid})")
            return []
        if len(spends) == 1 and len(recvs) == 1:
            return self._build_instant_trade(
                {'spend': spends[0], 'receive': recvs[0]}, refid)
        if len(spends) > 1 and len(recvs) > 1:
            raise ValueError(
                f"Kraken ledger refid {refid!r}: {len(spends)} spend and "
                f"{len(recvs)} receive legs — no defensible way to pair "
                f"them. Enter this conversion via a .tt file{_TT_REMOVE}.")
        many, one, many_side = ((spends, recvs[0], 'spend')
                                if len(spends) > 1
                                else (recvs, spends[0], 'receive'))
        weights = [leg['usd'] for leg in many]
        if all(w is not None for w in weights) and sum(weights) > 0:
            total = sum(weights)
            shares = [w / total for w in weights]
            basis = 'amountusd'
        else:
            shares = [1.0 / len(many)] * len(many)
            basis = 'equal shares (no amountusd in this export)'
        print(f"note: Kraken ledger refid {refid!r}: {len(many)} "
              f"{many_side} legs ({', '.join(l['asset'] for l in many)}) "
              f"share one {one['asset']} {'receipt' if many_side == 'spend' else 'payment'} "
              f"— split by {basis}; each asset is booked separately.",
              file=sys.stderr)
        out = []
        for leg, share in zip(many, shares):
            part = dict(one, amount=one['amount'] * share,
                        fee=one['fee'] * share,
                        usd=(one['usd'] * share
                             if one['usd'] is not None else None))
            pair = ({'spend': leg, 'receive': part} if many_side == 'spend'
                    else {'spend': part, 'receive': leg})
            for tx in self._build_instant_trade(pair, refid):
                if 'id' in tx:
                    # A coin-for-coin split keeps its -sell/-buy suffix
                    # LAST (`<refid>-ADA-sell`), the stem fill-crypto
                    # pairs on to value each swap once: `<refid>-sell-ADA`
                    # was never paired, so each leg took its own coin's
                    # daily close — a phantom gain (A2-0581, S013-08).
                    m = re.fullmatch(r'(.+)-(sell|buy)', tx['id'])
                    tx['id'] = (f"{m.group(1)}-{leg['asset']}-{m.group(2)}"
                                if m else f"{tx['id']}-{leg['asset']}")
                tx['description'] = (tx.get('description', '')
                                     + f" [split {share:.4f} of "
                                       f"{one['asset']} {one['amount']:g}]")
                out.append(tx)
        return out

    def _build_instant_trade(self, trade, refid=''):
        """Build the BUYSELL transaction(s) for one Kraken instant trade.

        Returns a list — typically one tx for a fiat-paired swap, two
        for a crypto-to-crypto swap (both legs are taxable events per
        CRA s. 40(1) / IRS Notice 2014-21: the spent crypto is disposed
        at FMV and the received crypto is acquired at the same FMV).
        Returns [] if either leg is missing — WARNED loudly, never
        silently (a lone `spend` is a real taxable disposition the user
        must know was skipped; a lone `receive` is an acquisition whose
        basis would otherwise vanish).
        """
        if 'spend' not in trade or 'receive' not in trade:
            for leg_type, leg in trade.items():
                missing = 'receive' if leg_type == 'spend' else 'spend'
                print(f"warning: UNBOOKED: Kraken ledger refid "
                      f"{refid[:2]}***: orphan "
                      f"{leg_type} row ({leg['asset']} "
                      f"{leg['amount']:g} on {leg['date']}) has no "
                      f"matching {missing} leg — row SKIPPED. A lone "
                      f"spend is a real taxable disposition (and a lone "
                      f"receive an acquisition with basis); this event "
                      f"is NOT in the output — find the missing "
                      f"counter-leg (truncated export?) or enter the "
                      f"trade manually via a .tt file.",
                      file=sys.stderr)
                self.count_skip(f"orphan {leg_type} (refid {refid})")
            return []
        spend = trade['spend']
        recv = trade['receive']
        # Kraken ledger fees are in the ROW's asset units and the balance
        # moves by amount − fee: a crypto leg's fee changes how many
        # coins actually moved — a receive credits amount − fee, a spend
        # debits |amount| + fee. Fold it into the CRYPTO leg's quantity
        # (the fee coins are part of what was given up / never arrived)
        # instead of dropping it, which left phantom units in the book.
        # Fiat-leg fees stay money (see fiat_fee below).
        def _coins(leg, received):
            if leg['asset'] in self._fiat:
                return leg['amount']
            return (leg['amount'] - leg['fee'] if received
                    else leg['amount'] + leg['fee'])

        if spend['asset'] == recv['asset'] and \
                spend['asset'] not in self._fiat:
            # ETH -> ETH2.S (both fold to ETH, the same property): a
            # relabel, not a disposition (S061-11).
            if spend['fee'] or recv['fee']:
                raise ValueError(
                    f"Kraken ledger refid {refid[:2]}***: a swap between "
                    f"two spellings of {spend['asset']} with a fee in the "
                    f"coin — not modeled; enter it via a .tt file{_TT_REMOVE}.")
            self._same_property_swap(spend['amount'], recv['amount'],
                                     spend['asset'],
                                     f"ledger refid {refid[:2]}***")
            return []

        if (spend['asset'] in _FIAT_CURRENCIES
                and recv['asset'] in _FIAT_CURRENCIES):
            # Fiat-for-fiat (USDC dust swept to USD, USD -> CAD): a
            # currency conversion, not a disposition of property. The
            # fiat-spend branch below would have booked a BUYSELL of a
            # phantom `CAD`/`USD` asset. Counted, not modeled
            # (KNOWN_ISSUES "Kraken fiat conversions are not modeled").
            self.count_nonevent(
                f"forex conversion {spend['asset']}->{recv['asset']} "
                f"(instant trade / dust sweep, not modeled — "
                f"KNOWN_ISSUES)")
            return []

        if spend['asset'] in self._fiat:
            is_buy = True
            quote_asset, quote_amt = spend['asset'], spend['amount']
            base_asset, base_amt = recv['asset'], _coins(recv, True)
        elif recv['asset'] in self._fiat:
            is_buy = False
            quote_asset, quote_amt = recv['asset'], recv['amount']
            base_asset, base_amt = spend['asset'], _coins(spend, False)
        else:
            # Crypto-to-crypto: emit TWO transactions — a SELL of the
            # spent asset and a BUY of the received asset — both priced
            # in USD. Per CRA s. 40(1) and IRS Notice 2014-21, the swap
            # is a taxable disposition of the spent crypto at FMV; the
            # received crypto is acquired at the same FMV. The legacy
            # behaviour squashed both legs into one BUYSELL of the
            # received asset denominated in the spent crypto, silently
            # dropping the spent-leg gain. Each leg's USD FMV is filled
            # later by taxjson-fill-crypto (it backfills any BUYSELL
            # with price=0 and a non-fiat symbol).
            # Fee handling: Kraken's ledger `fee` is in the row's asset's
            # units (e.g. a few thousandths of an ETH on an ETH→BTC swap),
            # while the emitted legs are USD-denominated. The parser has
            # no FMV at this stage (taxjson-fill-crypto fills it later),
            # so the USD `fee` field stays 0; the fee COINS are instead
            # folded into each leg's quantity via _coins() (sold |amount|
            # + fee, received amount − fee) so the book's unit count
            # matches Kraken's balance.
            sell_leg = {
                'action': 'BUYSELL',
                'date': spend['date'], 'time': spend['time'],
                'date_settle': spend['date'],
                'symbol': spend['asset'],
                'quantity': self.signed_quantity(_coins(spend, False),
                                                 action_is_sell=True),
                'currency': 'USD', 'price': 0.0,
                'net_amount': 0.0, 'gross_amount': 0.0,
                'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                'description': 'Instant Trade (sell leg of crypto-to-crypto swap)',
            }
            buy_leg = {
                'action': 'BUYSELL',
                'date': recv['date'], 'time': recv['time'],
                'date_settle': recv['date'],
                'symbol': recv['asset'],
                'quantity': self.signed_quantity(_coins(recv, True),
                                                 action_is_sell=False),
                'currency': 'USD', 'price': 0.0,
                'net_amount': 0.0, 'gross_amount': 0.0,
                'fee': 0.0, 'account': self.DEFAULT_ACCOUNT,
                'description': 'Instant Trade (buy leg of crypto-to-crypto swap)',
            }
            # ONE exchange, ONE value (S013-08): with the ledger's
            # amountusd the swap is valued once — the received leg's
            # USD value (else the spent leg's) is both the spent coin's
            # proceeds and the received coin's cost. Pricing each leg
            # from its own coin's daily close gave the two sides of one
            # exchange two different values: a phantom gain or loss.
            usd = None
            # Property mode: a stablecoin leg values the swap at 1.00 USD
            # a coin FIRST, as the trades path does (US-CRYPTO-02) — the
            # exchange's amountusd came first here, so one swap had two
            # values depending on the export (re-audit A2-1004).
            for _l, _rcv in ((spend, False), (recv, True)):
                if _l['asset'] in _STABLECOINS:
                    usd = _coins(_l, _rcv)
                    break
            if not usd:
                usd = recv['usd'] if recv['usd'] else spend['usd']
            if usd:
                for leg in (sell_leg, buy_leg):
                    q = abs(leg['quantity'])
                    leg['price'] = round(usd / q, 8) if q else 0.0
                    leg['net_amount'] = round(usd, 8)
                    leg['gross_amount'] = round(usd, 8)
            if refid:
                sell_leg['id'] = f'{refid}-sell'
                buy_leg['id'] = f'{refid}-buy'
            return [sell_leg, buy_leg]

        price = (round(quote_amt / base_amt, 8)
                 if base_amt > 0 else 0.0)
        qty = self.signed_quantity(base_amt, action_is_sell=not is_buy)
        # Engine convention (core.py: "net_amount ... includes the
        # fee"): Kraken debits the ledger fee IN ADDITION to the
        # amount, so an instant BUY's true cash out is quote_amt + fee
        # and an instant SELL's true proceeds quote_amt − fee — same as
        # the trades path. Booking bare quote_amt understated basis /
        # overstated proceeds by the ~1.5% instant-trade fee.
        # FIAT-side fee only: the crypto leg's fee is denominated in
        # the crypto asset and cannot be summed into a fiat net
        # (re-audit — same rationale as the crypto/crypto path's
        # zeroing; crypto-leg fees are typically 0/pennies).
        fiat_fee = spend['fee'] if is_buy else recv['fee']
        net = quote_amt + fiat_fee if is_buy else quote_amt - fiat_fee
        gross = quote_amt

        tx = {
            'action': 'BUYSELL',
            'date': spend['date'], 'time': spend['time'], 'date_settle': spend['date'],
            'symbol': base_asset, 'quantity': qty, 'currency': quote_asset,
            'price': price, 'net_amount': net, 'gross_amount': gross,
            'fee': fiat_fee, 'account': self.DEFAULT_ACCOUNT,
            'description': 'Instant Trade',
        }
        if refid:
            tx['id'] = refid
        return [tx]

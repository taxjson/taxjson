from taxjson.lib.stage_msg import emit_line
import csv
from dataclasses import dataclass, field
from decimal import Decimal
import io
import re
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from taxjson.parsers.protocol import Outcome, Row, builds
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         read_broker_text,
                                         shown_name)
from taxjson.lib.brokerages.schema import QTY_ZERO
from taxjson.lib.brokerages._crypto_common import (FIAT_CURRENCIES,
                                                   USD_STABLECOINS, RowIds,
                                                   same_coin_hint,
                                                   order_within_seconds,
                                                   strict_money,
                                                   usd_value as _usd_value,
                                                   warn_depeg)
from taxjson.lib.timezones import utc_to_local
from taxjson.parsers.toolbox.money import money_close
from taxjson.parsers.toolbox.masking import mask_id
# The sale of a ledger fee taken in coins, and the wording its refusals
# share with this parser's (PLAN step 14 moved them to the toolbox).
from taxjson.parsers.toolbox.fee_coins import (_DUST_USD, _TT_REMOVE, _units,
                                               _usd_text, fee_coin_sale)
# A reward's DIVIDEND and acquisition, valued from the ledger's USD amounts
# (PLAN step 14 moved them to the toolbox).
from taxjson.parsers.toolbox.staking import ledger_reward_legs
from taxjson.model import money as M


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
# The live list (lib/markets + ticker.map STABLE lines): read at each
# use, never frozen at import.
_STABLECOINS = USD_STABLECOINS
_CASH_STABLECOINS = _STABLECOINS


class _FiatAndStable(AbstractSet):
    """Currencies (not property) in a cash-mode book: fiat + the
    stablecoins (live)."""

    def __contains__(self, item) -> bool:
        return item in FIAT_CURRENCIES or item in USD_STABLECOINS

    def __iter__(self):
        return iter(sorted(set(FIAT_CURRENCIES) | set(USD_STABLECOINS)))

    def __len__(self) -> int:
        return len(set(FIAT_CURRENCIES) | set(USD_STABLECOINS))

    @classmethod
    def _from_iterable(cls, it):
        return frozenset(it)


_FIAT_ASSETS = _FiatAndStable()


# Kraken's wallet-flavour suffixes on ledger asset codes: `.S` staked,
# `.M` bonded/staked (newer), `.F` opt-in/flexible rewards, `.B`/`.P`
# (bonded / parachain variants), `.HOLD` (the USD rewards-holding
# balance). They all name the SAME property as the bare code — a
# `DOT.S` reward is DOT income and later-sold DOT. Left in place they
# were separate "assets" (or, for the legacy `staking` rows, dropped
# outright) and fill-crypto could never price `DOT.S-USD`.
_ASSET_SUFFIX_RE = re.compile(r'\.(S|M|F|B|P|HOLD)$', re.IGNORECASE)
# Legacy (pre-Earn, ~2021-23) BONDED staking codes carry the lock period
# in days, two digits, before the `.S`: <COIN><dd>.S (DOT28.S, KSM07.S).
# Same property as the bare coin (audits S014-01 / S061-08: DOT28.S used
# to become its own "DOT28" pool, so the rewards sat in a pool nothing
# sold and the real coin went short). Derived from the shape, not a list
# of coins or lock periods: folded only when the bare coin itself
# appears in the same export (_KNOWN_ASSETS), so a real coin whose
# ticker ends in digits (C98.S) is never cut. A bonded code whose coin
# never appears is noted, with the ticker.map GLOBAL line that folds it.
_BONDED_STAKING_RE = re.compile(r'^([A-Z][A-Z0-9]*?[A-Z])(\d{2})\.S$')
# The bare asset codes of the export being parsed (set per file by the
# parser's pre-pass; None outside a parse).
_KNOWN_ASSETS: Optional[set] = None

# Required columns — a missing `fee`/`vol` used to read as 0 on every
# row (DictReader.get), silently booking fee-free trades. Refuse.
_TRADES_REQUIRED = ('txid', 'pair', 'time', 'type', 'price', 'cost',
                    'fee', 'vol')
_LEDGER_REQUIRED = ('txid', 'refid', 'time', 'type', 'asset', 'amount',
                    'fee')

# Ledger types that move your OWN fiat cash to or from Kraken (bank
# funding, cash-outs, wallet transfers): not a tax event. Any other fiat
# row with an amount (a credit, an adjustment) is UNBOOKED (A2-0578).
# (The position book's list. A Hybrid Earn row is no cash move of the
# cash events: `_HYBRID_EARN`, `_kr_cash_own`.)
_FIAT_FUNDING_TYPES = ('deposit', 'withdrawal', 'transfer',
                       'hybridearnwithdrawal')

# Kraken Hybrid Earn (`hybridearnwithdrawal` takes an asset out of the
# spot balance into the product, `hybridearndeposit` brings it back; the
# ledger has no row of another wallet for what is parked): every type
# with this prefix is a move within Kraken, read by its sign
# (`_kr_walk`, backlog TJ-4951, TJ-4954).
_HYBRID_EARN = 'hybridearn'

# Legacy (pre-Earn) moves between the spot and staking wallets: the
# coins never leave the account — a recognised non-event.
_STAKING_WALLET_MOVES = ('spottostaking', 'stakingfromspot',
                         'stakingtospot', 'spotfromstaking',
                         'spottofutures', 'spotfromfutures')

# The label of a leg under the books' zero that is not booked (the dust
# branches and the finish's moves share it, TJ-2374).
_DUST_LABEL = f"Kraken dust leg under {QTY_ZERO:g} units (not booked)"

# `earn` subtypes moving coins between the spot and Earn wallets (paired
# rows, the coins stay yours): a recognised non-event. `autoallocate` is
# the spelling Kraken documents for an automatic allocation, beside the
# `autoallocation` seen in exports; it was left out and reported as
# unbooked property (backlog TJ-0750). `migration`: a move between two
# Earn programs (re-audit A2-1017).
_EARN_WALLET_MOVES = ('allocation', 'deallocation', 'autoallocation',
                      'autoallocate', 'migration')


def _normalize_asset(asset: str, fold_stable: bool = True,
                     bonded: bool = True) -> str:
    """Kraken prefixes assets with Z (fiat) and X (crypto) for historical reasons.
    Strip those and treat USD-pegged stablecoins as their USD anchor.
    `fold_stable=False` keeps the stablecoin's own name — the custody-
    evidence rows must say WHAT was sent (a USDC gift is a disposition
    of USDC the property, not a USD cash movement), even though the
    trade books fold it for pricing. The project's ticker.map GLOBAL
    lines between bare codes apply first and last (lib/markets.
    crypto_alias: `GLOBAL ETH2 ETH` folds a staked-coin code into its
    coin — taxjson ships no such fold)."""
    from taxjson.lib.markets import crypto_alias, kraken_assets
    # Upper-cased first: a hand-edited or converted export with `dot.s`
    # or `eth` would otherwise open its own pool beside DOT/ETH (R1-112).
    asset = (asset or '').strip().upper()
    if bonded:
        m = _BONDED_STAKING_RE.match(asset)
        if m:
            coin = _normalize_asset(m.group(1), fold_stable=False,
                                    bonded=False)
            if _KNOWN_ASSETS is not None and coin in _KNOWN_ASSETS:
                asset = m.group(1)
            elif _KNOWN_ASSETS is not None:
                from taxjson.lib.markets import note_builtin
                note_builtin(
                    "kraken-bonded", asset,
                    f"Kraken {asset} looks like a bonded-staking code of "
                    f"{coin} ({m.group(2)}-day lock), but {coin} itself "
                    f"never appears in the export, so it is kept as its "
                    f"own asset {asset[:-2]}; if it is {coin}, add "
                    f"`GLOBAL {asset[:-2]} {coin}` to ticker.map.")
    asset = _ASSET_SUFFIX_RE.sub('', asset)
    asset = crypto_alias(asset)
    if (len(asset) == 4 and asset.startswith('Z')
            and asset[1:] in _FIAT_CURRENCIES):
        asset = asset[1:]           # ZUSD, ZJPY, ZAUD (never ZEC: Zcash)
    # Kraken's legacy codes (XXBT, XETH, XDG ...): the shipped market
    # data (lib/markets.kraken_assets) — an unmapped `XXLM` reached
    # taxjson-fill-crypto as `XXLM-USD`, which Yahoo can't resolve. A
    # code the list lacks: ticker.map `GLOBAL XCODE COIN`.
    asset = kraken_assets().get(asset, asset)
    asset = crypto_alias(asset)
    # Fold USD-pegged stablecoins. DAI is included so that a `BTC/DAI`
    # row resolves consistently — otherwise DAI would be flagged as
    # fiat (via `_FIAT_ASSETS`) but pass through to the gain engine
    # as a non-USD currency that downstream FX conversion can't anchor.
    if fold_stable and asset in _CASH_STABLECOINS:
        asset = 'USD'
    return asset


def known_assets_of(codes) -> set:
    """The bare asset codes among `codes` (raw Kraken ledger assets or
    pair halves), normalised without the bonded-staking fold: the
    _KNOWN_ASSETS a parse folds `<COIN><dd>.S` against."""
    out = set()
    for c in codes:
        if c:
            out.add(_normalize_asset(c, fold_stable=False, bonded=False))
    return out


def _alt(codes) -> str:
    """A regex alternation of `codes`, longest first (USDT before USD)."""
    return '|'.join(re.escape(c) for c in sorted(codes, key=lambda c:
                                                 (-len(c), c)))


def _split_pair(pair: str, time_raw: str = '') -> tuple:
    """(base, quote) for a Kraken trades-CSV pair.

    Modern exports use the slashed form (`XBT/USD`). Legacy exports
    concatenate (`XXBTZUSD`, `ADAUSD`, `XETHXXBT`); the old fallback
    dumped the whole string into `base` and stamped `quote='USD'`,
    which mis-denominated every legacy row. Recognize the documented
    legacy shapes and refuse loudly on anything else — a wrong quote
    silently corrupts the disposition currency. The quote and base
    alternatives come from the fiat and stablecoin lists (lib/markets),
    not from a list of their own."""
    if '/' in pair:
        # Upper-cased like the legacy branch below: 'eth/usd' used to
        # keep a lower-case quote that missed every fiat check and
        # became a crypto/crypto swap with a phantom 'usd' coin (R1-112).
        base, quote = pair.split('/', 1)
        return base.strip().upper(), quote.strip().upper()
    p = pair.strip().upper()
    fiat = set(_FIAT_CURRENCIES)
    stables = set(USD_STABLECOINS)
    m = re.fullmatch(r'X([A-Z]{3,4})Z([A-Z]{3})', p)
    if m and m.group(2) in fiat:            # XXBTZUSD, XXBTZJPY
        return m.group(1), m.group(2)
    m = re.fullmatch(r'X([A-Z]{3,4})X([A-Z]{3,4})', p)
    if m:                                   # XETHXXBT (crypto/crypto)
        return m.group(1), m.group(2)
    # Z-prefixed fiat QUOTE behind a stablecoin or Z-fiat base (USDTZUSD,
    # ZUSDZCAD — Kraken's actual legacy names): matched BEFORE the
    # generic concatenation, whose greedy base otherwise eats the Z and
    # mints garbage symbols like 'USDTZ'. Kept to the stablecoin and
    # Z-fiat bases — a permissive Z? base would wrongly split XTZUSD
    # (Tezos) into XT/USD.
    m = re.fullmatch(rf'({_alt(stables | {"Z" + f for f in fiat})})'
                     rf'Z({_alt(fiat)})', p)
    if m:
        base = m.group(1)
        if base.startswith('Z') and len(base) == 4 and base[1:] in fiat:
            base = base[1:]                 # ZUSDZCAD → USD/CAD
        return base, m.group(2)
    m = re.fullmatch(rf'([A-Z0-9]{{2,8}})({_alt(stables | fiat)})', p)
    if m:
        # ETHPYUSD is ETH/PYUSD (a crypto quote), not 'ETHPY'/USD cash:
        # a stablecoin ticker that ENDS in a fiat code makes the
        # concatenation ambiguous, and the generic split minted a
        # truncated base and booked the coin side as dollars (audit
        # S061-00). Refused like any unrecognized shape.
        quote = m.group(2)
        clash = sorted(s for s in stables
                       if len(s) > len(quote) and s.endswith(quote)
                       and p.endswith(s))
        if quote in fiat and clash:
            raise ValueError(
                f"Kraken trades row has an ambiguous legacy pair {pair!r} "
                f"(time={time_raw!r}): it ends in a stablecoin ticker "
                f"({', '.join(clash)}), so the quote may be that coin, not "
                f"{quote}. Re-export the trades with slashed pairs "
                f"(ETH/PYUSD), or write the pair with a slash in the file.")
        return m.group(1), quote            # ADAUSD, SOLUSDT
    raise ValueError(
        f"Kraken trades row has an unrecognized pair format: {pair!r} "
        f"(time={time_raw!r}). The slashed form (XBT/USD) and the "
        f"documented legacy concatenations (XXBTZUSD, XETHXXBT, ADAUSD) "
        f"are supported; write the pair with a slash (BASE/QUOTE) rather "
        f"than letting the row parse with a guessed quote currency.")


def _classify_header(header_line: str) -> str:
    """'trades' | 'ledgers' | '' for a Kraken CSV's first line."""
    h = header_line.lower()
    if 'ordertxid' in h or 'pair' in h:
        return 'trades'
    if 'refid' in h or 'subtype' in h:
        return 'ledgers'
    return ''


REQUIRED_BY_KIND = {'trades': _TRADES_REQUIRED, 'ledgers': _LEDGER_REQUIRED}


def header_kind(cells) -> tuple:
    """(kind, required columns missing) for a Kraken export's FIRST row
    — the row parse_file classifies (no preamble: Kraken writes the
    header on line 1). kind is 'trades' | 'ledgers' | ''. Broker
    detection asks the parser this question (lib/brokerages/detect.py),
    so the two cannot drift."""
    kind = _classify_header(','.join(c for c in cells if c))
    if not kind:
        return '', []
    have = {(c or '').strip().lower() for c in cells}
    return kind, [c for c in REQUIRED_BY_KIND[kind] if c not in have]


def kraken_named(name: str) -> bool:
    """A Kraken export's file name (lower-cased): kr_* or *kraken*, the
    name test the sibling finder falls back to in a folder of more CSVs
    than it detects (lib/brokerages/detect same_broker_siblings)."""
    return name.startswith('kr_') or 'kraken' in name


def _dec_add(a: float, b: float) -> float:
    """`a + b` on the decimal digits the export printed (repr is the
    shortest decimal of each float), so the sum has no binary tail: a
    quote leg's cost and its fee (TJ-2375)."""
    return float(Decimal(repr(float(a))) + Decimal(repr(float(b))))


def _dec_sub(a: float, b: float) -> float:
    """`a - b` the same way: a net reward quantity."""
    return _dec_add(a, -b)


def _mask(ref: Any) -> str:
    """A Kraken txid/refid as shown in a message (the privacy rule for
    ids, A2-0756/A2-1381); `''` when missing. The raw id stays the
    work-JSON transaction id, which is data, not a message."""
    return mask_id(ref, empty="''")


def _lower_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {(k or '').strip().lower(): v for k, v in row.items()}


def _require_columns(fieldnames, required, path: Path, kind: str) -> None:
    have = {(h or '').strip().lower() for h in (fieldnames or [])}
    missing = [c for c in required if c not in have]
    if missing:
        # The file's name first: a sibling read while another file is
        # parsed (the ledger beside a trades export) is named, not the
        # file being parsed (backlog TJ-0164).
        raise ValueError(
            f"{shown_name(path)}: Kraken {kind} CSV: required column(s) "
            f"missing: {', '.join(missing)}. Header: {list(fieldnames or [])}. "
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
            f"{shown_name(path)} line {line}: the Kraken {kind} row has "
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
            f"{shown_name(path)}: Kraken {kind} CSV: column(s) {', '.join(dup)} "
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
        if (None not in raw and isinstance(cells[0], str)
                and not cells[0].strip()
                and all(c is None for c in cells[1:])):
            # A line of only spaces or tabs is a blank line, skipped as
            # an empty one is (the csv module yields it as one cell):
            # it was refused as a truncated row (backlog TJ-0040).
            prev_end = reader.line_num
            continue
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
                f"{shown_name(path)} line {start}: a Kraken {kind} CSV cell holds "
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


_LEDGER_NUMERIC = ('amount', 'fee')


def _canon_number(text: str) -> str:
    """A numeric cell as one spelling: -1, -1.0 and -1.00000000 are one
    value (two exports print a quantity with different precision: they
    were refused as DIFFERENT content, backlog TJ-0739). A cell that is
    no plain finite number stays as written."""
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    if not d.is_finite():
        return text
    return '0' if d == 0 else format(d.normalize(), 'f')


def _ledger_identity(row: Dict[str, Any]) -> tuple:
    out = []
    for k in _LEDGER_IDENTITY:
        v = str(row.get(k) or '').strip().lower()
        out.append(_canon_number(v) if k in _LEDGER_NUMERIC and v else v)
    return tuple(out)


# A repeated ledger entry's valuation cells: an older export may lack
# them (no column, or a blank cell) while a newer one of the same entry
# has them. They are no part of the entry's identity; a value one copy
# has fills the other's blank (backlog TJ-0738).
_LEDGER_VALUATION = ('amountusd', 'feeusd')


def _fill_valuation(kept: Dict[str, Any], row: Dict[str, Any]) -> None:
    """Fill `kept`'s blank valuation cells from `row`, another copy of
    the same ledger entry. A value `kept` has stays."""
    for k in _LEDGER_VALUATION:
        if (str(kept.get(k) or '').strip() == ''
                and str(row.get(k) or '').strip() != ''):
            kept[k] = row[k]


def _fee_ccy(row: Dict[str, Any], asset_name: str) -> str:
    """The currency the ledger `fee` is denominated in. Exports since
    2026 carry `feecurrency`; older ones (and blank cells) mean the
    row's own asset."""
    fc = (row.get('feecurrency') or '').strip()
    if not fc:
        return asset_name
    return _normalize_asset(fc, fold_stable=False)


# ------------------------------------------------------------ the cash events
# The FX-on-cash ledger v2's events of a ledger export (lib/cash_events,
# tax-logic CA-FX-07): classify notes every ledger row (`note_cash`) and the
# finish builds them (KrakenBrokerage `_finish_cash`, `_kr_cash_of`), with
# the account's other ledger exports read beside it. lib/cash_readers/
# kraken.py read the export a second time for them until PLAN step 16
# commit 11.

def _kr_quiet_stable(code: str) -> bool:
    """`code in USD_STABLECOINS` without the once-per-process note the
    built-in list gives a coin it calls a stablecoin: for the rows of the
    other exports, whose own parse says it (backlog TJ-4461)."""
    if not isinstance(code, str) or not code:
        return False
    from taxjson.lib.markets import is_usd_stablecoin
    return is_usd_stablecoin(code.strip().upper(), note=False)


def _kr_quiet_cash(code: str, stable_cash: bool) -> bool:
    """Whether the bare asset `code` is currency in the book (`_FIAT_ASSETS`
    at the cash basis, `_FIAT_CURRENCIES` at the property basis), without
    the stablecoin note (`_kr_quiet_stable`)."""
    return code in _FIAT_CURRENCIES or (stable_cash and _kr_quiet_stable(code))


def _kr_quiet_norm(raw: str, stable_cash: bool) -> str:
    """`_normalize_asset(raw, fold_stable=stable_cash)` without the
    stablecoin note (`_kr_quiet_stable`)."""
    asset = _normalize_asset(raw, fold_stable=False)
    return 'USD' if stable_cash and _kr_quiet_stable(asset) else asset


def _kr_sibling_ledger_rows(paths) -> List[tuple]:
    """[(name as messages show it, [(row, line)])] of the Kraken LEDGER
    exports among `paths` (the export's siblings, `_kraken_siblings`), in
    name order. One that does not read is left out: its own read reports
    it."""
    out = []
    for p in sorted(paths, key=lambda q: q.name):
        try:
            with io.StringIO(read_broker_text(p)) as f:
                if _classify_header(f.readline()) != 'ledgers':
                    continue
                f.seek(0)
                srows = [(_lower_row(raw), line) for raw, line in
                         _dict_rows(f, p, 'ledger', _LEDGER_REQUIRED)]
        except (BrokerageParseError, OSError, ValueError, csv.Error,
                UnicodeDecodeError):
            continue
        out.append((shown_name(p), srows))
    return out


def _kr_where_key(where: str) -> tuple:
    """`file:line` -> (file, line), for sorting."""
    name, _, line = where.rpartition(':')
    return (name, int(line) if line.isdigit() else 0)


def _kr_when(raw: str, where: str):
    """A ledger row's `time` as the cash events read it (UTC, to the
    second); a ValueError naming the row for one they cannot read."""
    from datetime import datetime
    try:
        return datetime.strptime((raw or '').split('.')[0].strip(),
                                 "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise ValueError(f"{where}: unparseable Kraken time "
                         f"{raw!r}") from None


def _kr_frac(raw: str) -> Optional[int]:
    """The fraction of a second a ledger row's `time` states, in
    microseconds; None when it states none (or one that is no number).
    Rows of one second that all state one are listed, for the walk, in
    the order of those (`_kr_listed`, backlog TJ-5028: the fraction was
    dropped, and a newest-first pair read in the lines' order). The
    events' dates stay `_kr_when`'s."""
    frac = (raw or '').strip().partition('.')[2].strip()
    return int((frac + "000000")[:6]) if frac.isdigit() else None


# Between a wallet's asset code and the name the export gives the wallet
# (`_kr_wallet`): no asset code holds it.
_KR_NAMED = " @ "


def _kr_wallet(raw_asset: str, named: Any = '') -> str:
    """The wallet a row's balance belongs to: the bare asset and its
    wallet suffix (USDC.F, USD.HOLD beside USDC, ZUSD), and the wallet
    the export names for the row (`named`, its `wallet` column: `spot /
    main`, `earn / flexible`) where it names one. Two wallets of one
    asset code keep a balance each: read by the code alone, the last
    wallet's balance replaced the other's (backlog TJ-5027)."""
    sfx = _ASSET_SUFFIX_RE.search(raw_asset)
    code = (_normalize_asset(raw_asset, fold_stable=False)
            + (sfx.group(0).upper() if sfx else ''))
    name = ' '.join(str(named or '').lower().split())
    return f"{code}{_KR_NAMED}{name}" if name else code


def _kr_is_move(r: Dict[str, Any]) -> bool:
    """Whether the ledger row moves its asset between two wallets of the
    account (an `earn` allocation, a legacy staking transfer): a row in
    each wallet, the two adding up to nothing (`_kr_uncovered`). Not a
    transfer to or from futures (`_KR_OUT_OF_BOOK`): that cash leaves
    the book, a CASHMOVE of the cash events, and no wallet of the
    ledger holds its other side."""
    typ = (r.get('type') or '').strip().lower()
    sub = (r.get('subtype') or '').strip().lower()
    return ((typ == 'earn' and sub in _EARN_WALLET_MOVES)
            or (typ == 'transfer' and sub in _STAKING_WALLET_MOVES
                and sub not in _KR_OUT_OF_BOOK))


_KR_OUT_OF_BOOK = ('spottofutures', 'spotfromfutures')
# What the name of the wallet of an asset code's suffix holds, where an
# export names its wallets (`_kr_join_unnamed`): a bare code is the spot
# wallet's, `.F` the flexible Earn wallet's, `.S` / `.M` / `.B` / `.P`
# a bonded or staked one's, `.HOLD` the holding balance's.
_KR_SUFFIX_KIND = {'': ('spot',), '.F': ('flex',), '.S': ('bond', 'stak', 'lock'),
                   '.M': ('bond', 'stak', 'lock'), '.B': ('bond', 'stak', 'lock'),
                   '.P': ('bond', 'stak', 'lock', 'para'), '.HOLD': ('hold',)}


def _kr_leg(r: Dict[str, Any], where: str, stable_cash: bool) -> tuple:
    """(day, currency, amount, fee, where, bare asset, fee currency when
    it is cash, txid) of another export's trade leg, as the groups hold
    it (its currencies read without the stablecoin note, `_kr_quiet_norm`)."""
    raw_asset = (r.get('asset') or '').strip()
    bare = _normalize_asset(raw_asset, fold_stable=False)
    cur = _kr_quiet_norm(raw_asset, stable_cash)
    day = utc_to_local(_kr_when(r.get('time', ''), where)).date().isoformat()
    amt = strict_money(r.get('amount'), 'amount', f"Kraken {where}")
    fee = strict_money(r.get('fee') or '0', 'fee', f"Kraken {where}")
    fc = _fee_ccy(r, bare)
    fee_cur = (_kr_quiet_norm(fc, stable_cash)
               if _kr_quiet_cash(fc, stable_cash) else None)
    return (day, cur, amt, fee, where, bare, fee_cur, (r.get('txid') or '').strip())


def _kr_close(a: float, b: float) -> bool:
    """Two balances of one wallet the same, to the digits the export prints."""
    return abs(a - b) <= 1e-8 * max(1.0, abs(a), abs(b))


# The most steps `_kr_orders` walks, and the most orders it keeps, for one
# second of one wallet: past either the balances are read as giving no
# order (a second of many rows with blank balances).
_KR_ORDER_STEPS = 4000
_KR_ORDER_KEEP = 64


@dataclass(eq=False)
class _KrEntry:
    """A ledger row in its wallet's timeline (`_kr_walk`): `when`, its
    time to the second, and `frac`, the fraction of a second it states
    (`_kr_frac`); `key` its (file, line), `where` that as `file:line`;
    `down`, whether its export lists the wallet's rows newest first, and
    `sure`, whether the export says so (`_kr_listing`: every pair of
    rows of the wallet that follow each other with different times goes
    one way); `seq`, its place in the file's order (the lines of an
    export listed newest first counted from the last up); `own`, a row
    of the export parsed;
    `earlier`, an own row an export of an earlier name holds too; `before`
    and `after`, the wallet's balance around it (None: a blank `balance`);
    `hyb`, a Hybrid Earn row; `delta`, what it added to what is parked
    (less what it brought back of it), and `over`, what it brought back
    beyond that; `unsure`, why a balance read from the row is not sure
    ("" when it is): a row of a second whose order its balances do not
    settle, read in the file's order ("up", "down": the lines from the
    first down, from the last up), in the order of the fractions of a
    second its rows state ("frac"), or with no listing direction known
    ("none"); or a row after a second of Hybrid Earn rows whose order is
    not known, on which what is parked depends ("hybrid"; `taint`, that
    second: its day and the export it is in); `ref`, its refid, and
    `move`, a move between two wallets of the account (`_kr_is_move`);
    `mixed`, a row of an export without a `wallet` column whose wallet
    the exports that have one may name too (`_kr_join_unnamed`: the
    named wallets it may be); `mine`, the export parsed's own row of
    the entry as `file:line` ("" when it holds none); `copies`, the
    entry's row in each export that holds it (the export's name -> that
    copy, with its `key`, `seq`, `down` and `sure`: `_kr_one_listing`);
    `net`, what the row adds to the wallet's balance (its amount less a
    fee in the asset)."""
    when: Any
    key: tuple
    where: str
    own: bool
    bare: str
    wallet: str
    amt: float
    before: Optional[float]
    after: Optional[float]
    hyb: bool
    frac: Optional[int] = None
    seq: tuple = ()
    down: bool = False
    sure: bool = True
    earlier: bool = False
    delta: float = 0.0
    over: float = 0.0
    unsure: str = ""
    taint: str = ""
    ref: str = ""
    move: bool = False
    mixed: str = ""
    mine: str = ""
    net: float = 0.0
    copies: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _KrWalk:
    """The account's ledger rows walked in time order, a wallet at a time
    (`_kr_walk`): `wallets`, each wallet's rows in the order walked; `parked`,
    beside each row of a wallet, what the wallet's Hybrid Earn rows up to
    and with it had parked from its balance and not brought back;
    `excess`, by an own row's `file:line`, the (bare asset, units, day) a
    Hybrid Earn row brought back beyond what the rows before it had
    parked; `earlier`, the own rows another export of an earlier name
    holds too (that export says them); `unordered`, a `_KrUnordered` per
    second holding Hybrid Earn rows whose order the balances do not give
    and whose order matters."""
    wallets: Dict[str, List[_KrEntry]] = field(default_factory=dict)
    parked: Dict[str, List[float]] = field(default_factory=dict)
    excess: Dict[str, tuple] = field(default_factory=dict)
    earlier: set = field(default_factory=set)
    unordered: List["_KrUnordered"] = field(default_factory=list)


@dataclass
class _KrUnordered:
    """One second of one asset whose rows the balances put in no one
    order, read in the file's order (`_kr_place`; `read`: the order it
    was read in, as the parse says it): `rows` and `hybrid`,
    how many rows and how many of them Hybrid Earn rows; `over` and
    `held`, what the file's order finds brought back beyond what was
    parked and left parked; `over_range`, `held_range` and `balance_range`,
    the least and the most another order gives (the balance: the last
    row's with what is parked, None without a balance); `own`, whether
    this export says it."""
    bare: str
    day: str
    rows: int
    hybrid: int
    over: float
    held: float
    balance: Optional[float]
    over_range: tuple
    held_range: tuple
    balance_range: Optional[tuple]
    own: bool
    read: str = "the file's order"


def _kr_is_hybrid(r: Dict[str, Any]) -> bool:
    """Whether the ledger row is a Hybrid Earn row (`_HYBRID_EARN`)."""
    return (r.get('type') or '').strip().lower().startswith(_HYBRID_EARN)


def _kr_hybrid_step(held: float, amt: float) -> tuple:
    """(units parked after a Hybrid Earn row of `amt`, what it brought
    back beyond the `held` units parked before it)."""
    if amt <= 0:
        return _dec_sub(held, amt), 0.0
    return max(0.0, _dec_sub(held, amt)), max(0.0, _dec_sub(amt, held))


def _kr_listing(es: List["_KrEntry"]) -> tuple:
    """Which way an export lists rows (`es`, in the order of its lines):
    (newest first; whether the rows say so; how many pairs of rows that
    follow each other have different times). Every such pair is counted,
    never the first and the last row alone: an export sorted by asset,
    two exports pasted one after the other and a row appended out of its
    place each begin and end at times that say nothing of the order
    between (review round 5). The rows say which way only when every
    pair goes that way; otherwise the way most pairs go is read (oldest
    first on a tie) and is not sure."""
    up = down = 0
    for a, b in zip(es, es[1:]):
        # (to the fraction of a second where both rows state one)
        both = a.frac is not None and b.frac is not None
        ta, tb = (a.when, a.frac if both else 0), (b.when, b.frac if both else 0)
        up, down = up + (ta < tb), down + (ta > tb)
    return down > up, not (up and down), up + down


def _kr_set_listing(mine: List["_KrEntry"]) -> None:
    """Each row of one export (`mine`, in the order of its lines) takes
    the way the export lists the rows of its wallet (`_kr_listing`:
    `down`, `sure`) and its place in that order (`seq`). A wallet is
    judged by its own rows: an export sorted by asset lists each wallet
    one way. One whose rows share one time takes the way the export's
    other wallets are listed, when their rows say one way and all the
    same; else the way of the whole export's rows, when those say one;
    and none where nothing says."""
    by: Dict[str, list] = {}
    for e in mine:
        by.setdefault(e.wallet, []).append(e)
    told = {w: _kr_listing(es) for w, es in by.items()}
    ways = {(down, sure) for down, sure, pairs in told.values() if pairs}
    whole = _kr_listing(mine)
    if len(ways) == 1 and next(iter(ways))[1]:
        rest = next(iter(ways))
    else:
        rest = (whole[0], not ways and whole[1] and whole[2] > 0)
    for w, es in by.items():
        down, sure = told[w][:2] if told[w][2] else rest
        for e in es:
            e.down, e.sure = down, sure
            e.seq = (e.key[0], -e.key[1] if down else e.key[1])


def _kr_entries(rows: List[tuple], siblings: List[tuple], name: str) -> Dict[str, list]:
    """Every row of the export (`rows`: (row, line)) and of the account's
    other ledger exports (`siblings`, `_kr_sibling_ledger_rows`) as a
    `_KrEntry`, by wallet (`_kr_wallet`), an entry of a txid read once
    whichever export is parsed: the union of the exports' rows, each
    marked `own` with the parsed export's row (`mine`) when that export
    holds it, and `earlier` when the copy read is another export's. The
    copy read is the one of the export that lists the most rows of the
    entry's second (`_kr_one_listing`). So every export of the account
    walks the same rows in the same order, and what the walk derives
    (what a Hybrid Earn row brought back beyond what was parked) is the
    same from each: with the parsed export's own copy read, two exports
    that list one second's rows in different orders derived two amounts
    for one row (backlog TJ-5224).
    An export's listing direction is read from all of its rows, the
    copies not kept too (`_kr_set_listing`). A row whose time or amount
    does not read is left out (its own parse reports it; an own Hybrid
    Earn row's raises), and a `balance` that does not read is a blank
    one."""
    seen: Dict[str, Optional[_KrEntry]] = {}
    out: Dict[str, list] = {}
    files = sorted([(name, rows, True)] + [(n, r, False) for n, r in siblings],
                   key=lambda f: f[0])
    for fname, frows, own in files:
        listed: List[_KrEntry] = []
        for r, i in frows:
            txid = (r.get('txid') or '').strip()
            where = f"{fname}:{i}"
            hyb = _kr_is_hybrid(r)
            raw_asset = (r.get('asset') or '').strip()
            bare = _normalize_asset(raw_asset, fold_stable=False)
            try:
                when = _kr_when(r.get('time', ''), where)
                amt = strict_money(r.get('amount'), 'amount', f"Kraken {where}")
                fee = strict_money(r.get('fee') or '0', 'fee', f"Kraken {where}")
            except ValueError:
                if own and hyb:
                    raise
                if txid:
                    seen.setdefault(txid, None)
                continue
            try:
                after = (strict_money(r.get('balance'), 'balance', f"Kraken {where}")
                         if (r.get('balance') or '').strip() else None)
            except ValueError:
                after = None        # the cash events' own read refuses it
            net = amt - (fee if _fee_ccy(r, bare) == bare else 0.0)
            e = _KrEntry(when, (fname, i), where, own, bare,
                         _kr_wallet(raw_asset, r.get('wallet')), amt,
                         None if after is None else after - net, after, hyb,
                         _kr_frac(r.get('time', '')),
                         ref=(r.get('refid') or '').strip(), move=_kr_is_move(r),
                         mine=where if own else "", net=net)
            listed.append(e)
            if txid and txid in seen:
                first = seen[txid]
                if first is not None:
                    first.copies[fname] = e
                    if own:
                        first.own, first.mine = True, where
                continue
            if txid:
                seen[txid] = e
            e.copies[fname] = e
            out.setdefault(e.wallet, []).append(e)
        _kr_set_listing(listed)
    _kr_join_unnamed(out)
    _kr_one_listing(out)
    return out


def _kr_one_listing(out: Dict[str, list]) -> None:
    """The rows of one wallet in one second are read as ONE export lists
    them, where one does: of the exports that hold rows of the second
    (`_KrEntry.copies`), the one holding the most, the earliest name
    among equals; each row that export holds takes that copy's place
    (`key`, `where`, `seq`, `down`, `sure`), in place. Read each from the
    export of the earliest name that holds it, a second two exports
    share in parts was ordered by the exports' names: a return in export
    a and its parking in export b, both in b, were read return first
    though b lists the parking first (review round 2, P2). A row is
    `earlier` when the copy read is not the parsed export's own: the
    export read says it, and its messages name the rows as read."""
    for line in out.values():
        seconds: Dict[Any, list] = {}
        for e in line:
            seconds.setdefault(e.when, []).append(e)
        for es in seconds.values():
            held: Dict[str, int] = {}
            for e in es:
                for fname in e.copies:
                    held[fname] = held.get(fname, 0) + 1
            best = min(held, key=lambda f: (-held[f], f)) if held else ""
            for e in es:
                c = e.copies.get(best)
                if c is not None and c is not e:
                    e.key, e.where, e.seq, e.down, e.sure = c.key, c.where, c.seq, c.down, c.sure
                e.earlier = bool(e.mine) and e.mine != e.where


def _kr_join_unnamed(out: Dict[str, list]) -> None:
    """The rows of exports without a `wallet` column (`out`: the rows by
    wallet, `_kr_wallet`), where other exports of the account name their
    wallets, join the named wallet of their own kind, in place: the
    asset's one named wallet whose name fits the code's suffix
    (`_KR_SUFFIX_KIND`: a bare code the spot wallet, `.F` the flexible
    Earn wallet, ...), whatever code that export writes the asset with.
    Read as a wallet of their own beside it, the wallet was counted
    twice in the account's balance. With no named wallet of their kind
    they are a wallet of their own, added up with the others (an older
    export's spot rows beside a newer one's Earn rows are two wallets).
    Where the kind does not settle it (two named wallets of the kind; a
    suffix this table does not know, beside named wallets of the asset
    that are not spot wallets), they stay a wallet of their own and are
    marked `mixed` with those wallets: a balance read from them says so
    (`_kr_cash_balances`), never counted in silence (backlog TJ-5027)."""
    def named(bare: str) -> List[str]:
        return [w for w, es in out.items() if _KR_NAMED in w and es and es[0].bare == bare]
    for code in [w for w in out if _KR_NAMED not in w]:
        bare = out[code][0].bare
        kind = _KR_SUFFIX_KIND.get(code[len(bare):] if code.startswith(bare) else None)
        if kind is None:
            maybe = [w for w in named(bare)
                     if not w.partition(_KR_NAMED)[2].startswith('spot')]
        else:
            maybe = [w for w in named(bare)
                     if any(k in w.partition(_KR_NAMED)[2] for k in kind)]
            if len(maybe) == 1:
                for e in out[code]:
                    e.wallet = maybe[0]
                out[maybe[0]].extend(out.pop(code))
                continue
        for e in out[code]:
            e.mixed = ", ".join(sorted(w.partition(_KR_NAMED)[2] for w in maybe))


def _kr_uncovered(walk: "_KrWalk") -> Dict[str, List[_KrEntry]]:
    """The rows that move an asset between two wallets of the account
    (`_KrEntry.move`) and whose other side no export read holds, by bare
    asset: a move's rows add up to nothing, those of one refid, or else
    one out and one in of the same units in two wallets. A row left
    alone names a wallet the exports read do not cover (its export was
    not downloaded, or not put in the folder): the account's balance of
    that asset cannot be formed from the wallets seen (backlog TJ-5032)."""
    by: Dict[str, Dict[str, List[_KrEntry]]] = {}
    for line in walk.wallets.values():
        for e in line:
            if e.move and abs(e.amt) > 1e-9:
                by.setdefault(e.bare, {}).setdefault(e.ref, []).append(e)
    out: Dict[str, List[_KrEntry]] = {}
    for bare, refs in by.items():
        left = [e for ref, es in refs.items() for e in es
                if not ref or not _kr_close(1.0 + abs(sum(x.amt for x in es)), 1.0)]
        for e in [x for x in left if x.amt < 0]:
            back = next((x for x in left if x.amt > 0 and x.wallet != e.wallet
                         and _kr_close(x.amt, -e.amt)), None)
            if back is not None:
                left = [x for x in left if x is not e and x is not back]
        if left:
            out[bare] = sorted(left, key=lambda x: (x.when, x.key))
    return out


def _kr_orders(rows: List[_KrEntry], starts: Optional[List[float]],
               ends: Optional[List[float]] = None) -> Optional[List[List[_KrEntry]]]:
    """Every order of the rows of one wallet in one second (`rows`, in the
    file's order, `_KrEntry.seq`) that their balances allow: each row's balance before it
    (its `balance` less its amount plus its fee in the asset) is the
    balance after the row before it, the first row's one of the balances
    `starts` (None: not known, any row may be first), and the last row's
    balance is one of `ends` when those are known. A row with a blank
    balance fits anywhere, and the balance after it is not known. None
    when there are more than can be walked (`_KR_ORDER_STEPS`,
    `_KR_ORDER_KEEP`)."""
    out: List[List[_KrEntry]] = []
    steps = [0]

    def fits(value: Optional[float], known: Optional[List[float]]) -> bool:
        return known is None or value is None or any(_kr_close(value, k) for k in known)

    def go(order: List[_KrEntry], left: List[_KrEntry], cur: Optional[float]) -> bool:
        if not left:
            if fits(cur, ends):
                out.append(list(order))
            return len(out) <= _KR_ORDER_KEEP
        for k, x in enumerate(left):
            steps[0] += 1
            if steps[0] > _KR_ORDER_STEPS:
                return False
            if not fits(x.before, [cur] if cur is not None else starts if not order
                        else None):
                continue
            order.append(x)
            ok = go(order, left[:k] + left[k + 1:], x.after)
            order.pop()
            if not ok:
                return False
        return True

    return out if go([], list(rows), None) else None


def _kr_result(order: List[_KrEntry], held: float, moved: float = 0.0) -> tuple:
    """What one order of a second's rows gives, `held` units parked before
    them (`moved` of them from this wallet's balance): (the wallet's
    balance after the last row, None when that row's is blank; the units
    parked after them; what they brought back beyond what was parked;
    the balance of the last row that has one with what the wallet's rows
    up to it parked, None without such a row; the balance before the
    first row that has one with what the rows before it parked: what a
    balance before the second is read from)."""
    over, shown, first = 0.0, None, None
    for x in order:
        if x.before is not None and first is None:
            first = x.before + moved
        if x.hyb:
            after, more = _kr_hybrid_step(held, x.amt)
            moved = _dec_add(moved, _dec_sub(after, held))
            held, over = after, _dec_add(over, more)
        if x.after is not None:
            shown = x.after + moved
    return (order[-1].after, held, over, shown, first)


def _kr_same(a: tuple, b: tuple) -> bool:
    """Two `_kr_result`s the same."""
    return all((x is None and y is None)
               or (x is not None and y is not None and _kr_close(x, y))
               for x, y in zip(a, b))


def _kr_span(values: List[Optional[float]]) -> tuple:
    """(the least, the most) of the values that are known, (0, 0) without one."""
    known = [v for v in values if v is not None]
    return (min(known), max(known)) if known else (0.0, 0.0)


def _kr_listed(rows: List[_KrEntry]) -> tuple:
    """The rows of one wallet in one second (`rows`, by `seq`) in the
    order the export gives them, and how it gives it: "frac", the
    fractions of the second they state, when each states one and they
    are not all the same; "up" or "down", the export's lines from the
    first down or from the last up, when the export says which way it
    lists the wallet's rows (`_KrEntry.sure`); "none", the lines as they
    are, when it does not (or the rows are of two exports)."""
    fracs = [x.frac for x in rows]
    if None not in fracs and len(set(fracs)) > 1:
        return sorted(rows, key=lambda x: (x.frac, x.seq)), "frac"
    if all(x.sure for x in rows) and len({x.key[0] for x in rows}) == 1:
        return rows, "down" if rows[0].down else "up"
    return rows, "none"


def _kr_taken(listed: List[_KrEntry], how: str, chains: List[List[_KrEntry]],
              found: List[tuple], starts: Optional[List[float]],
              ends: Optional[List[float]], first: Optional[float] = None
              ) -> Optional[tuple]:
    """The order of a second's rows that the chains its balances allow
    (`chains`, their results `found`) settle, and whether it is sure:
    (order, sure), or None when they settle none. `listed` and `how`:
    the order the export gives the rows, `_kr_listed`.

    Where the export says which way it lists the wallet's rows and the
    listed order is a chain, every balance stated, that ends where the
    next second's listed first row begins (`first`; or nothing follows
    it), and the second holds no Hybrid Earn row (their order changes
    what is parked: such a second stays said), it is taken, sure,
    whatever else chains: a second that comes
    back to the balance it began at chains both ways, and the balances
    the next second's rows begin at fit both when that second does the
    same (a deposit and its conversion, every month: review round 6).
    Otherwise the chains give one result (or the rows state fractions of
    the second and their order is a chain, whatever else chains), and
    - the listed order is one of them: the balances and the listing
      agree. Sure, unless the export does not say which way it lists
      the wallet's rows (`how` "none");
    - or, `how` being "none", the reverse of the lines is one of them:
      taken, not sure;
    - or they are anchored on both sides with every balance stated (one
      balance before the second, one the next second begins at: a next
      second of several rows that begin at different balances names
      none): the amounts of such a chain add up to the difference of
      the two balances, which a missing row breaks. Sure, unless the
      rows state fractions of the second that say another order.
    A chain anchored on one side only, or on none, that is not the
    order the export gives the rows is not taken: a row missing inside
    the second leaves rows that chain in another order to a balance
    that reconciles and is wrong (review round 4). That rule holds
    round 3's too, in either listing: a listed order that is a chain of
    its own which only the balance before contradicts (that balance may
    be stale: a row missing before the second) is never replaced by the
    chain from that balance, which one side alone anchors."""
    def among(order: List[_KrEntry]) -> bool:
        return any(all(x is y for x, y in zip(o, order)) for o in chains)
    if how == "frac" and among(listed):
        # (The times the rows state, and a chain: whatever else chains.)
        return listed, True
    if (how in ("up", "down") and among(listed)
            and all(x.after is not None for x in listed)
            and not any(x.hyb for x in listed)
            and (ends is None or (first is not None
                                  and _kr_close(listed[-1].after, first)))):
        # (The export says which way it lists the rows and that order
        # chains, to where the next second's listed first row begins.)
        return listed, True
    if not all(_kr_same(r, found[0]) for r in found[1:]):
        return None
    if among(listed):
        return listed, how != "none"
    if how == "none" and among(listed[::-1]):
        return listed[::-1], False
    both = (starts is not None and len(starts) == 1
            and ends is not None and all(_kr_close(e, ends[0]) for e in ends)
            and all(x.after is not None for x in listed))
    return (chains[0], how != "frac") if both else None


# How the parse says the order a second was read in (`_KrUnordered.read`).
_KR_READ = {"frac": "the order of the fractions of a second they state",
            "none": "the order of the export's lines, the export not saying which way "
                    "it lists them",
            "taken": "the order their balances chain in, the export not saying which "
                     "way it lists them"}


def _kr_said(walked: List[_KrEntry], others: Optional[List[List[_KrEntry]]],
             held: float, moved: float, read: str = "") -> tuple:
    """What another order of a second's rows would give beside the one
    walked (`walked`; `others`, the orders to set beside it, None when
    there are too many to walk: the two orders of the Hybrid Earn rows
    that find the least and the most brought back beyond what was parked
    are taken then): (whether one gives another result; the
    `_KrUnordered` to say, when the second holds a Hybrid Earn row and
    another order changes what those rows give, else None)."""
    here = _kr_result(walked, held, moved)
    hyb = [x for x in walked if x.hyb]
    if others:
        found = [_kr_result(o, held, moved) for o in others]
    else:
        found = [_kr_result(sorted(hyb, key=lambda x: x.amt > 0), held)[:3] + (None, None),
                 _kr_result(sorted(hyb, key=lambda x: x.amt <= 0), held)[:3] + (None, None)
                 ] if hyb else []
        here = here[:3] + (None, None)
    differs = not others or not all(_kr_same(r, here) for r in found)
    if not hyb or all(_kr_same(r[1:4], here[1:4]) for r in found):
        return differs, None
    return differs, _KrUnordered(
        walked[0].bare, utc_to_local(walked[0].when).date().isoformat(), len(walked),
        len(hyb), here[2], here[1], here[3], _kr_span([r[2] for r in found] + [here[2]]),
        _kr_span([r[1] for r in found] + [here[1]]),
        _kr_span([r[3] for r in found] + [here[3]]) if here[3] is not None else None,
        any(x.own and not x.earlier for x in walked), read or "the file's order")


def _kr_place(rows: List[_KrEntry], starts: Optional[List[float]],
              ends: Optional[List[float]], held: float, moved: float = 0.0,
              first: Optional[float] = None) -> tuple:
    """The rows of one wallet in one second (`rows`, by `seq`) in the
    order walked: (the order; the balances the wallet may hold after
    it, None when not known; a `_KrUnordered` to say, or None).

    The order is the one the balance column gives, when it settles one
    (`_kr_orders`, `_kr_result`: the balance after, what is parked, what
    came back beyond it, the balance a statement point reads; `_kr_taken`):
    every row of the second chained from the wallet's balance before it
    (`starts`: the balance after the second before, or, where that
    second's own order is not known, one of its rows' balances) or to
    the balance the next second begins at (`ends`: one of the balances
    before its rows), or, with no balance on either side (the wallet's
    only second; a blank `balance` beside it), among themselves, all
    such chains giving one result, and
    - the chain being the order the export gives the rows (`_kr_listed`:
      its lines, from the last up where it lists the wallet's rows
      newest first; the fractions of the second the rows state): the
      balances and the listing agree; or
    - the chain anchored on both sides, every balance stated: the rows
      account for the whole difference between the balance before and
      the balance after, whatever order the export lists them in.
    The Hybrid Earn rows alone are never chained: a row of another type
    between a return and a new parking is part of the chain (backlog
    TJ-4954). Otherwise (no chain: a row missing; chains that disagree:
    rows that come back to the balance they began at; a chain that is
    not the listed order and that one balance only, or none, anchors: a
    row may be missing inside the second or before it) the order is the
    listed one, as it was before the balances were read at all, and its
    rows are marked `unsure`: when a chain was found and not taken, and
    otherwise when another order the balances allow (every order, when
    they allow none) gives another result, or when the export states no
    direction for the wallet's rows. So are the rows of a chain
    taken that is not sure: one the export's direction would have to
    confirm where the export states none (its rows of the wallet go
    both ways in time, or share one time: review round 5), or one the
    fractions the rows state contradict. A statement balance read
    from them says so (`_kr_cash_balances`), and a second holding a
    Hybrid Earn row is said by the parse (`_KrUnordered`). Never taken
    in silence."""
    if len(rows) == 1:
        return rows, None if rows[0].after is None else [rows[0].after], None
    listed, how = _kr_listed(rows)
    anchors = starts is not None or ends is not None
    chains = _kr_orders(listed, starts, ends) if anchors else _kr_orders(listed, None)
    if chains:
        found = [_kr_result(o, held, moved) for o in chains]
        got = _kr_taken(listed, how, chains, found, starts, ends, first)
        if got is not None:
            after = None if found[0][0] is None else [found[0][0]]
            if got[1]:
                return got[0], after, None
            for x in rows:
                x.unsure = how
            return got[0], after, _kr_said(
                got[0], [listed, listed[::-1]] + chains, held, moved,
                _KR_READ.get("taken" if how == "none" else how, ""))[1]
    # The listed order: the balance after it is one of its rows'.
    stated = [x.after for x in listed if x.after is not None]
    after_all = stated if len(stated) == len(listed) else None
    others = chains or (_kr_orders(listed, None) if anchors else None)
    if not others and len(listed) <= 6:
        from itertools import permutations
        others = [list(p) for p in permutations(listed)]
    differs, said = _kr_said(listed, others, held, moved, _KR_READ.get(how, ""))
    if chains or differs or how == "none":
        for x in rows:
            x.unsure = how
    return listed, after_all, said


def _kr_walk(rows: List[tuple], siblings: List[tuple], name: str) -> _KrWalk:
    """The rows of the export (`rows`: (row, line)) and of the account's
    other ledger exports (`siblings`, `_kr_sibling_ledger_rows`) walked
    in time order, a wallet at a time, the wallets of one asset together
    (`_kr_entries`); the rows of one wallet in one second in the order
    `_kr_place` gives (the balance column's when the wallet's balance
    before or after that second settles it, else the file's: the lines
    of an export listed newest first read from the last up).

    A Hybrid Earn row with a negative amount parks its units, a positive
    one brings back what is parked, and what it brings beyond that is the
    row's excess: a return and a new parking in one second are an excess
    when the return came first. Each row's part in what is parked is kept
    by its wallet (`parked`), so a balance of the wallet adds what its
    own rows up to that balance's row parked, and no more (backlog
    TJ-4951). A second whose order is not given and matters is noted
    (`unordered`): said by the parse (backlog TJ-4954)."""
    out = _KrWalk()
    by_wallet = _kr_entries(rows, siblings, name)
    by_bare: Dict[str, list] = {}
    for wallet, es in by_wallet.items():
        es.sort(key=lambda e: (e.when, e.seq))
        by_bare.setdefault(es[0].bare, []).append(wallet)
    for bare, wallets in by_bare.items():
        held = 0.0
        # Each wallet's seconds in turn, the wallets taken together by
        # time (those of one second in the file's order of their first
        # rows, `_KrEntry.seq`): [(time, wallet, its rows of that second)].
        seconds: List[tuple] = []
        for wallet in sorted(wallets):
            mine: List[tuple] = []
            for e in by_wallet[wallet]:
                if mine and mine[-1][0] == e.when:
                    mine[-1][2].append(e)
                else:
                    mine.append((e.when, wallet, [e]))
            # (the balances the next second may begin at: the end anchor)
            for k, sec in enumerate(mine):
                after = mine[k + 1][2] if k + 1 < len(mine) else []
                nxt = [x.before for x in after]
                seconds.append(sec + (nxt if nxt and None not in nxt else None,
                                      _kr_listed(after)[0][0].before if after else None))
        seconds.sort(key=lambda s: (s[0], min(x.seq for x in s[2]), s[1]))
        known: Dict[str, Optional[List[float]]] = {}
        # (Once a second of Hybrid Earn rows was read in an order that
        # is not known, what is parked after it is not either: the rows
        # after it say so, `_KrEntry.unsure` "hybrid".)
        tainted = ""
        for at, sec in enumerate(seconds):
            when, wallet, group, ends, first = sec
            sums = out.parked.setdefault(wallet, [])
            order, known[wallet], said = _kr_place(group, known.get(wallet), ends, held,
                                                   sums[-1] if sums else 0.0, first)
            if said is None and any(x.hyb for x in group):
                said = _kr_across(seconds, at, held)
            if said is not None:
                out.unordered.append(said)
                tainted = f"on {said.day} in {group[0].key[0]}"
            line = out.wallets.setdefault(wallet, [])
            for x in order:
                if tainted and not x.unsure:
                    x.unsure, x.taint = "hybrid", tainted
                if x.hyb:
                    after, x.over = _kr_hybrid_step(held, x.amt)
                    x.delta, held = _dec_sub(after, held), after
                    if x.own and x.over > QTY_ZERO:
                        out.excess[x.mine] = (bare, x.over,
                                               utc_to_local(when).date().isoformat())
                if x.own and x.earlier:
                    out.earlier.add(x.mine)
                line.append(x)
                sums.append(_dec_add(sums[-1] if sums else 0.0, x.delta))
    return out


def _kr_across(seconds: List[tuple], at: int, held: float) -> Optional[_KrUnordered]:
    """Hybrid Earn rows of two wallets of one asset in one second: no
    balance says which wallet's came first. Said, once (at the first of
    the wallets, `seconds[at]`), when parked first and brought back first
    give another result; walked a wallet at a time, in the order the file
    lists their first rows."""
    when, lo, hi = seconds[at][0], at, at + 1
    while lo > 0 and seconds[lo - 1][0] == when:
        lo -= 1
    while hi < len(seconds) and seconds[hi][0] == when:
        hi += 1
    same = [s for s in seconds[lo:hi] if any(x.hyb for x in s[2])]
    if len(same) < 2 or same[0] is not seconds[at]:
        return None
    hyb = [x for s in same for x in s[2] if x.hyb]
    here = _kr_result(hyb, held)
    found = [_kr_result(sorted(hyb, key=lambda x: x.amt > 0), held),
             _kr_result(sorted(hyb, key=lambda x: x.amt <= 0), held)]
    if all(_kr_same(r[1:3], here[1:3]) for r in found):
        return None
    return _KrUnordered(
        hyb[0].bare, utc_to_local(when).date().isoformat(),
        sum(len(s[2]) for s in same), len(hyb), here[2], here[1], None,
        _kr_span([r[2] for r in found] + [here[2]]),
        _kr_span([r[1] for r in found] + [here[1]]), None,
        any(x.own and not x.earlier for x in hyb))


def _kr_parked_event(day: str, cur: str, amt: float, name: str, where: str,
                     typ: str, bare: str, over: float = 0.0) -> Dict[str, Any]:
    """A Hybrid Earn row of cash as the cash events hold it: a PARKED
    event, no move of the ledger (the cash stays in the book), listed by
    `tjs fx-cash --ledger v2 --cash-events` and read by the ledger for
    one thing: a .tt CASHMOVE line written for the row when it was read
    as a cash move declares no broker move now, and is refused by name
    (lib/cash_events `parked_lines`, backlog TJ-4951). `over`: what the
    row brought back beyond what was parked, the CASHMOVE of the same
    row (`_kr_cash_own`): the event says so, so that the listing does
    not show that part as no move and as a move without a word."""
    more = (f", but for the {M.shown(over, 2):,.2f} beyond what was parked: the "
            f"CASHMOVE of the same row ({where})" if over > 1e-9 else "")
    return {"kind": "PARKED", "date": day, "settle": day, "currency": cur,
            "amount": amt, "where": name, "line_where": where, "account": "",
            "desc": f"Kraken {typ} {bare}", "origin": "broker",
            "text": f"{M.shown(amt, 2):+,.2f} {cur} Kraken {typ} {bare}: "
                    f"{'into' if amt < 0 else 'back from'} Hybrid Earn, the "
                    f"account's cash throughout (no move{more})"}


def _kr_cash_own(rows: List[tuple], name: str, stable_cash: bool,
                 hybrid: Optional[_KrWalk] = None) -> tuple:
    """The export's own rows (`rows`: (row, line), in the file's order):
    (its events in that order: a CASHMOVE per fiat (or, at the cash basis,
    stablecoin) deposit, withdrawal or transfer and its fee a FLOW; its
    trade legs by refid; its balance rows). A row of a txid seen before is
    read once. A Hybrid Earn row is a move within Kraken, in either
    direction no CASHMOVE but a PARKED event (`_kr_parked_event`; its fee
    in cash a FLOW): the cash stays in the book, and the balances add
    what is parked (`_kr_cash_balances`). Only
    what a row brings back beyond what was parked (`hybrid.excess`) is a
    CASHMOVE, undeclared: cash the export does not explain (backlog
    TJ-4951, TJ-4954); a row that brings back nothing that was parked
    is that CASHMOVE alone, with no PARKED event. Each event of a row
    carries the row's txid (`_broker_id`): two deposits alike in day and
    amount that two exports hold are two events, and one row that two
    overlapping exports hold is one (cash_events.dedup_across_files;
    without it the two deposits were read as one, backlog TJ-5033). The
    balance rows: how many rows of cash state a `balance` (each read, a
    cell that is no amount raising); the balances are the account's
    (`_kr_cash_balances`)."""
    from taxjson.lib import cash_lines as CL
    cashlike = _FIAT_ASSETS if stable_cash else _FIAT_CURRENCIES
    out: List[Dict[str, Any]] = []
    groups: Dict[str, list] = {}
    bal_rows = 0
    seen_ids: set = set()
    for r, i in rows:
        where = f"{name}:{i}"
        raw_asset = (r.get('asset') or '').strip()
        bare = _normalize_asset(raw_asset, fold_stable=False)
        if bare not in cashlike:
            continue
        txid = (r.get('txid') or '').strip()
        if txid and txid in seen_ids:
            continue
        seen_ids.add(txid)
        cur = _normalize_asset(raw_asset, fold_stable=stable_cash)
        when = _kr_when(r.get('time', ''), where)
        day = utc_to_local(when).date().isoformat()
        amt = strict_money(r.get('amount'), 'amount', f"Kraken {where}")
        fee = strict_money(r.get('fee') or '0', 'fee', f"Kraken {where}")
        # The fee's own currency (the parser's _fee_ccy): a FLOW in it
        # when it is cash, none when it is a coin (the position book
        # books that fee); the asset's balance before the row adds it
        # back only when the asset paid it (2026-10 audit #78).
        fc = _fee_ccy(r, bare)
        fee_cur = (_normalize_asset(fc, fold_stable=stable_cash)
                   if fc in cashlike else None)
        if (r.get('balance') or '').strip():
            strict_money(r.get('balance'), 'balance', f"Kraken {where}")
            bal_rows += 1
        typ = (r.get('type') or '').strip().lower()
        first = len(out)
        if typ.startswith(_HYBRID_EARN):
            over = (hybrid.excess.get(where) if hybrid is not None else None)
            # (A row that brought back nothing of what was parked, or
            # under two cents of it, is the CASHMOVE below alone: one
            # row, one event, and the line the ledger asks for fits it
            # and no PARKED event of the same row, review round 4.)
            if abs(amt) > 1e-9 and (over is None or amt - over[1] > 0.02):
                out.append(_kr_parked_event(day, cur, amt, name, where, typ, bare,
                                            over[1] if over is not None else 0.0))
            if over is not None:
                ev = CL.move(day, cur, over[1], where=name,
                             desc=f"Kraken {typ} {bare}, over what was parked")
                # (An amount the walk derives, not one the row states:
                # cash_events `_id_conflicts` says so where two exports
                # derive two.)
                ev['line_where'], ev['_derived'] = where, True
                out.append(ev)
            if abs(fee) > 1e-9 and fee_cur:
                out.append(CL.flow(day, fee_cur, -fee, where=where,
                                   desc=f"Kraken {typ} fee"))
        elif typ in _FIAT_FUNDING_TYPES:
            # (A legacy transfer between the spot and the staking wallet
            # is a move within Kraken, as an Earn allocation is: both
            # wallets are the account's, in its balance; no CASHMOVE.
            # It once asked for a declaration of each side: TJ-5227.)
            if abs(amt) > 1e-9 and not _kr_is_move(r):
                ev = CL.move(day, cur, amt, where=name,
                             desc=f"Kraken {typ} {bare}")
                ev['line_where'] = where
                out.append(ev)
            if abs(fee) > 1e-9 and fee_cur:
                out.append(CL.flow(day, fee_cur, -fee, where=where,
                                   desc=f"Kraken {typ} fee"))
        elif typ in ('trade', 'spend', 'receive'):
            groups.setdefault((r.get('refid') or '').strip() or txid,
                              []).append((day, cur, amt, fee, where, bare,
                                          fee_cur, txid))
        if txid:
            for ev in out[first:]:
                ev['_broker_id'] = txid
    return out, groups, bal_rows


def _kr_cash_siblings(rows: List[tuple], siblings: List[tuple],
                      groups: Dict[str, list], stable_cash: bool) -> Dict[str, set]:
    """The account's other ledger exports (`siblings`,
    `_kr_sibling_ledger_rows`) read beside the export's own rows: a leg of
    one of its trades that another export holds joins its group (a
    conversion whose two legs a UTC-midnight export boundary split was a
    one-currency "spread" FLOW in each file, its paid CAD lost: backlog
    TJ-0861). An entry in an overlapping export is read once.
    -> every asset of each trade's refid; the other exports' currencies
    read without the stablecoin note (`_kr_quiet_cash`, `_kr_quiet_norm`:
    their own parse says it, backlog TJ-4461). (The other exports' wallet
    rows are the walk's, `_kr_walk`: the balances are the account's,
    `_kr_cash_balances`.)"""
    own_ids = {(r.get('txid') or '').strip() for r, _i in rows} - {''}
    sib_ids: set = set()
    # A trade whose every leg is currency: a conversion (the position
    # book never sees it — the parser's fiat-for-fiat non-event).
    all_assets_by_ref: Dict[str, set] = {}
    for r, _i in rows:
        ref = (r.get('refid') or '').strip()
        if ref in groups:
            all_assets_by_ref.setdefault(ref, set()).add(
                _normalize_asset((r.get('asset') or '').strip(),
                                 fold_stable=False))
    for sname, srows in siblings:
        for r, i in srows:
            txid = (r.get('txid') or '').strip()
            if txid and (txid in own_ids or txid in sib_ids):
                continue        # the same entry in an overlapping export
            sib_ids.add(txid)
            where = f"{sname}:{i}"
            raw_asset = (r.get('asset') or '').strip()
            bare = _normalize_asset(raw_asset, fold_stable=False)
            ref = (r.get('refid') or '').strip()
            if ref and ref in groups:
                all_assets_by_ref.setdefault(ref, set()).add(bare)
            if not _kr_quiet_cash(bare, stable_cash):
                continue
            try:
                if ref and ref in groups and (r.get('type') or '').strip(
                        ).lower() in ('trade', 'spend', 'receive'):
                    groups[ref].append(_kr_leg(r, where, stable_cash))
            except ValueError:
                continue        # that export's own read reports the row
    return all_assets_by_ref


def _kr_cash_conversions(groups: Dict[str, list], all_assets_by_ref: Dict[str, set],
                         name: str, stable_cash: bool) -> List[Dict[str, Any]]:
    """An FXCONV per trade whose legs are all currency (USD for CAD, USDC
    for EUR; the legs of one refid added up per currency, each leg's cash
    fee a FLOW before it), in the groups' order; a stablecoin folded into
    USD against USD no conversion but its spread off par a FLOW of US
    dollars. A ValueError for a refid trading three currencies. Each
    event carries an id of the trade (`_broker_id`: its refid and what
    the event is of it, `conv`, `spread`, or `fee` with the leg's txid),
    the same in every export that holds a leg of it: kept once, and a
    trade alike in another export a trade of its own (backlog TJ-5033)."""
    from taxjson.lib import cash_lines as CL
    out: List[Dict[str, Any]] = []
    for ref, legs in groups.items():
        # In one order whichever export holds a leg (its file, then its
        # line), so every export gives the same event.
        legs.sort(key=lambda lg: _kr_where_key(lg[4]))
        # (A leg's asset of this export was noted by its parse; one of
        # another export's by that export's: no note here, TJ-4461.)
        if any(not _kr_quiet_cash(a, stable_cash)
               for a in all_assets_by_ref.get(ref, ())):
            continue        # a coin leg: the position book's fill
        fees = []
        for n, lg in enumerate(legs):
            if abs(lg[3]) > 1e-9 and lg[6]:
                fees.append(CL.flow(lg[0], lg[6], -lg[3], where=lg[4],
                                    desc="Kraken conversion fee"))
                if ref:
                    fees[-1]['_broker_id'] = f"{ref}:fee:{lg[7] or n}"
        out.extend(fees)
        # The legs added up per (folded) currency: Kraken can write one
        # side of a trade as several ledger rows under one refid (two
        # receipt legs), which `one debit, one credit` used to skip
        # without a word (2026-10-24 audit C08).
        sides: Dict[str, Dict[str, Any]] = {}
        for day, cur, amt, _fee, where, bare, _fc, _txid in legs:
            s = sides.setdefault(cur, {"amt": 0.0, "day": day,
                                       "where": where, "bare": []})
            s["amt"] += amt
            s["day"] = max(s["day"], day)
            if bare not in s["bare"]:
                s["bare"].append(bare)
        sides = {c: s for c, s in sides.items() if abs(s["amt"]) > 1e-9}
        if not sides:
            continue
        if len(sides) == 1:
            # USDC <-> USD (or USDC <-> USDT) in a cash-mode book: the
            # same currency, so no conversion — but a trade off par
            # moves the folded pool by its spread: 200 USDC sold for
            # 197 USD leaves 3 US dollars fewer (the ledger's own
            # balances say so). The net movement is a FLOW of the
            # currency, as a Coinbase spread is; dropped, it was a
            # reconcile gap the ledger could not explain (2026-10-24
            # audit C06). The de-peg itself is not a gain or loss of
            # the coin (tax-logic CA-CRYPTO-02).
            (cur, s), = sides.items()
            out.append(CL.flow(s["day"], cur, s["amt"], where=s["where"],
                               desc=f"Kraken {'/'.join(s['bare'])} spread"))
            if ref:
                out[-1]['_broker_id'] = f"{ref}:spread"
            continue
        neg = [(c, s) for c, s in sides.items() if s["amt"] < 0]
        pos = [(c, s) for c, s in sides.items() if s["amt"] > 0]
        if len(neg) != 1 or len(pos) != 1:
            # Three or more currencies under one refid: not one
            # currency for another. Refused naming the entry, never
            # left out of the ledger in silence (2026-10-24 audit C08).
            days = sorted(lg[0] for lg in legs)
            amounts = ', '.join(f"{M.shown(s['amt'], 2):+,.2f} {c}"
                                for c, s in sorted(sides.items()))
            raise ValueError(
                f"Kraken ledger {name}: refid {_mask(ref)} ({days[0]}) "
                f"trades {len(sides)} currencies at once ({amounts}) — "
                f"the FX-on-cash ledger books one currency for another. "
                f"Enter it as FXCONV .tt lines and remove its rows from "
                f"the export.")
        (fc, a), = neg
        (tc, b), = pos
        out.append(CL.conv(b["day"], fc, -a["amt"], tc, b["amt"],
                           where=a["where"],
                           desc=f"Kraken {'/'.join(a['bare'])}->"
                                f"{'/'.join(b['bare'])}"))
        if ref:
            out[-1]['_broker_id'] = f"{ref}:conv"
        for ev in fees:
            # The conversion it was charged on: in the base currency it
            # is part of the amount paid or received (lib/fx_cash_v2,
            # backlog TJ-0456).
            ev['_fee_of'] = a["where"]
    return out


# What a CASHBAL's `where` adds when the balance is read from a row
# that is not sure (`_KrEntry.unsure`, the key here): a reconcile gap of
# that day quotes it, and a balance that reconciles is a Warning of the
# ledger (lib/fx_cash_v2 `_unsure`). Each says the order that was read
# and what settles it.
_KR_UNSURE_HEAD = (" (read from rows that share one second and whose balances do not say "
                   "which came last: ")
_KR_UNSURE_FIX = (". Where that is not their true order, listing the second's rows in it "
                  "settles it; where it is, a row is missing from the export before or "
                  "among them)")
_KR_UNSURE = {
    "up": _KR_UNSURE_HEAD + "the file's order was taken, its lines from the first down "
          "(the export lists this wallet's rows oldest first)" + _KR_UNSURE_FIX,
    "down": _KR_UNSURE_HEAD + "the file's order was taken, its lines from the last up "
            "(the export lists this wallet's rows newest first)" + _KR_UNSURE_FIX,
    "frac": _KR_UNSURE_HEAD + "the order of the fractions of a second they state was "
            "taken, which is not the order their balances chain in" + _KR_UNSURE_FIX,
    "none": _KR_UNSURE_HEAD + "the export does not say which way it lists this wallet's "
            "rows (they are in no one order of time, or share one time), so the order "
            "their balances chain in was taken where the lines, read down or up, chain, "
            "and the lines' order where they do not. Sorting the export's rows by time, "
            "oldest first or newest first throughout, settles it; where the second's rows "
            "still do not chain then, a row is missing from the export before or among "
            "them)",
    "hybrid": " (what Hybrid Earn holds of it is counted from Hybrid Earn rows that share "
              "one second {0} and whose order is not known: the ATTENTION line of `tjs "
              "run` for that second says what settles it)",
}


def _kr_mixed_note(e: _KrEntry) -> str:
    """What a CASHBAL's `where` adds when a wallet counted in it is of
    rows without a `wallet` column that may be a wallet the other
    exports name (`_kr_join_unnamed`)."""
    return (f" (the {e.wallet} rows of {e.key[0]} name no wallet, and the account's other "
            f"exports name wallets they may be of ({e.mixed}): they are counted as a "
            f"wallet of their own, twice where they are one of those. One ledger export "
            f"of the account that covers those rows, with its `wallet` column, in place "
            f"of {e.key[0]} settles it)")


def _kr_note(day: str, cur: str, name: str, text: str) -> Dict[str, Any]:
    """A NOTE event of the cash events: the ledger says it."""
    return {"kind": "NOTE", "date": day, "settle": day, "currency": cur,
            "where": name, "origin": "broker", "text": text}


def _kr_wallet_gaps(walk: _KrWalk, name: str, stable_cash: bool) -> tuple:
    """The currencies whose balance the exports read cannot form, each
    with the day from which (`_kr_uncovered`: cash moved to or from a
    wallet no export read holds a row of; the balances before that day
    stand), and a NOTE event for each such row of this export (`name`),
    naming the row and its wallet: the ledger says it, and the currency
    gets no CASHBAL from that day on, never the wallets seen as the
    whole account (backlog TJ-5032)."""
    gone: Dict[str, str] = {}
    notes: List[Dict[str, Any]] = []
    for bare, rows in sorted(_kr_uncovered(walk).items()):
        if not _kr_quiet_cash(bare, stable_cash):
            continue
        cur = 'USD' if stable_cash and _kr_quiet_stable(bare) else bare
        for e in rows:
            day = utc_to_local(e.when).date().isoformat()
            gone[cur] = min(gone.get(cur, day), day)
            if not e.own or e.earlier:
                continue        # (the export that holds the row says it)
            notes.append(_kr_note(day, cur, name, (
                f"{e.where}: {M.shown(abs(e.amt), 2):,.2f} {bare} moved "
                f"{'out of' if e.amt < 0 else 'into'} the wallet "
                f"{e.wallet.replace(_KR_NAMED, ', ')} on {day}, "
                f"{'to' if e.amt < 0 else 'from'} another wallet of the account, "
                f"and no Kraken ledger export read holds that wallet's row — the "
                f"wallets read are not all of the account's {cur} from that day, so no "
                f"ledger balance of {cur} is given from then on (a balance of the wallets "
                f"read would be taken for the account's). Add the ledger export that "
                f"holds the other wallet's rows to the account's folder (one "
                f"export of the whole ledger holds every wallet), or give the "
                f"account's balance as a CASHBAL .tt line")))
    return gone, notes


def _kr_wallet_at(line: List[tuple], parked: List[float], at: str) -> Optional[tuple]:
    """(balance, what its Hybrid Earn rows had parked, the row read, how
    it opens a gap or "") of one wallet at the end of the day `at`, from
    its rows in the walk's order (`line`: (row, day)); None where it is
    not known.

    After the wallet's latest row at or before `at` that states a
    balance, the balance after that row; unless the next row that states
    one, in whichever export, begins at another balance than that one
    and the rows between them (their `balance` blank) give: rows are
    missing between the two, and the balance at a date between them is
    the one the next row begins at, less what the blank rows after the
    date added (the export after the gap opens at it; read from the row
    before, the balance was stale by what the missing rows moved, and
    the gap was never said: review P1). The last element says so, for
    the ledger's gap line. Before its first row that states a balance,
    the balance that row begins at (less the blank rows after the date),
    from the year end just before the wallet's first row: earlier than
    that nothing says the wallet held it (the rows that built it are in
    no export read), unless it begins at nothing."""
    upto = max((i for i, (_e, day) in enumerate(line) if day <= at), default=-1)
    stated = [i for i, (e, _day) in enumerate(line) if e.after is not None]
    last = max((i for i in stated if i <= upto), default=None)
    nxt = min((i for i in stated if i > upto), default=None)
    if nxt is None:
        return (line[last][0].after, parked[last], line[last][0], "") if last is not None else None
    n = line[nxt][0]
    # (what the rows with no balance between the date and the next
    # balance add: the wallet's balance at the date is that much less)
    opening = n.before - sum(line[i][0].net for i in range(upto + 1, nxt))
    units = parked[upto] if upto >= 0 else 0.0
    if last is None:
        if (at < f"{int(line[0][1][:4]) - 1}-12-31"
                and (abs(opening) > 1e-9 or abs(units) > 1e-9)):
            return None
        return opening, units, n, ""
    e = line[last][0]
    expected = e.after + sum(line[i][0].net for i in range(last + 1, nxt))
    if _kr_close(n.before, expected):
        return e.after, parked[last], e, ""
    return opening, units, n, (
        f"{n.where} opens the wallet {n.wallet.replace(_KR_NAMED, ', ')} at "
        f"{M.shown(opening, 2):,.2f} {n.bare} ({line[nxt][1]}), and the wallet's row "
        f"before it, {e.where} ({line[last][1]}), ends at {M.shown(e.after, 2):,.2f}")


def _kr_cash_balances(walk: _KrWalk, name: str, stable_cash: bool) -> List[Dict[str, Any]]:
    """A CASHBAL per currency of the ACCOUNT at the end of every year its
    ledger exports span and before their first row (the opening of the
    first year), from the `balance` column of every export read (`walk`:
    this export's rows and its siblings', `_kr_walk`): each wallet's
    balance at the date (`_kr_wallet_at`: after its LATEST row at or
    before it; before its first row, the balance before that row), the
    wallets that fold into one currency added up. A wallet is its asset
    code and the wallet the export names (`_kr_wallet`), so two wallets
    of one code are two balances (backlog TJ-5027), and every export of
    the account gives the same balances, kept once
    (cash_events.dedup_across_files): each export once gave its own
    wallets' balance as the account's, and wallets exported apart were
    partial balances the ledger checked the whole account against
    (backlog TJ-5032; a balance before a wallet's first row was left out
    when `Another export moves the wallet` in between, backlog TJ-0888:
    that export's rows are read here now). The event's `where` names the
    exports whose rows were read. None of an export without a balance
    row of cash; none of a currency from the day a wallet of it is
    covered by no export read (`_kr_wallet_gaps`: a NOTE instead); and
    none of a currency at a date a wallet of it is not known at (before
    the year end just before the wallet's first row, when that row
    begins at a balance: a NOTE of the export holding the row says so).

    The latest row is by its clock, whatever order the export lists the
    rows in; the rows of one second in the order the walk puts them in
    (the balance column's where it is settled by the wallet's balance
    before or after that second, `_kr_place`; else the file's order,
    which for an export listed newest first is its lines from the last
    up). What the wallet's own Hybrid Earn rows had parked from that
    balance as of the balance row used is added (`_KrWalk.parked`): it
    left the `balance` column and is still the account's cash (backlog
    TJ-4951). As of that row, and of that wallet: a Hybrid Earn row with
    a blank `balance` is after the balance it leaves stale, and a later
    row of another wallet of the coin says nothing about this one's.
    The event's `where` says how much of the balance is parked, and,
    with the event's `unsure`, that it was read from rows of one second
    whose order is not sure, or after such a second of Hybrid Earn rows
    (`_KR_UNSURE`), or from rows whose wallet is not sure
    (`_kr_mixed_note`)."""
    from taxjson.lib import cash_lines as CL
    # wallet -> (currency, its rows in the walk's order: (row, day)),
    # of the wallets that state a balance
    series: Dict[str, tuple] = {}
    for wallet, line in walk.wallets.items():
        if (any(e.after is not None for e in line)
                and _kr_quiet_cash(line[0].bare, stable_cash)):
            bare = line[0].bare
            series[wallet] = ('USD' if stable_cash and _kr_quiet_stable(bare) else bare,
                              [(e, utc_to_local(e.when).date().isoformat()) for e in line])
    days = [d for _cur, rows in series.values() for e, d in rows if e.after is not None]
    if not days:
        return []
    gone, out = _kr_wallet_gaps(walk, name, stable_cash)
    y0, y1 = int(min(days)[:4]), int(max(days)[:4])
    points = [f"{y0 - 1}-12-31"] + [min(f"{y}-12-31", max(days)) for y in range(y0, y1 + 1)]
    late: Dict[str, _KrEntry] = {}      # a wallet not known at a date: its first row
    for at in points:
        tot: Dict[str, float] = {}
        parked: Dict[str, float] = {}
        unsure: Dict[str, str] = {}
        read: Dict[str, set] = {}
        opens: Dict[str, str] = {}
        unknown = {cur for cur, day in gone.items() if at >= day}
        for wallet in sorted(series):
            cur, rows = series[wallet]
            if cur in unknown:
                continue
            got = _kr_wallet_at(rows, walk.parked[wallet], at)
            if got is None:
                unknown.add(cur)
                late.setdefault(wallet, next(e for e, _d in rows if e.after is not None))
                continue
            v, units, e, gap = got
            if gap:
                opens.setdefault(cur, gap)
            tot[cur] = tot.get(cur, 0.0) + v
            read.setdefault(cur, set()).add(e.key[0])
            if e.unsure:
                unsure.setdefault(cur, _KR_UNSURE[e.unsure].format(e.taint))
            if e.mixed:
                unsure[cur] = unsure.get(cur, "") + _kr_mixed_note(e)
            if abs(units) > 1e-9:
                tot[cur] = _dec_add(tot[cur], units)
                parked[cur] = _dec_add(parked.get(cur, 0.0), units)
        for cur, v in sorted(tot.items()):
            if cur in unknown:
                continue
            units = parked.get(cur, 0.0)
            more = (f" + {M.shown(units, 2):,.2f} parked in Hybrid Earn" if units > 1e-9
                    else f" - {M.shown(-units, 2):,.2f} back from Hybrid Earn that a "
                         f"balance not read here parked" if units < -1e-9 else "")
            if cur in unsure:
                # (Read from a second the balances put in no one order.)
                more += unsure[cur]
            files = ", ".join(sorted(read[cur]))
            out.append(CL.balance(at, cur, M.units(v, 8),
                                  where=f"{files}: ledger balance{more}",
                                  statement=files))
            if cur in unsure:       # (the ledger warns when it reconciles)
                out[-1]["unsure"] = True
            if cur in opens:        # (the ledger's gap line says it as that)
                out[-1]["opens"] = opens[cur]
    for wallet, e in sorted(late.items()):
        if e.own and not e.earlier:
            day = utc_to_local(e.when).date().isoformat()
            cur = series[wallet][0]
            out.append(_kr_note(day, cur, name, (
                f"{e.where}: the wallet {wallet.replace(_KR_NAMED, ', ')} holds "
                f"{M.shown(e.before, 2):,.2f} {e.bare} before its first balance ({day}), and "
                f"no Kraken ledger export read holds the rows that brought it: the "
                f"wallet's balance is known from {int(day[:4]) - 1}-12-31 on, and no ledger "
                f"balance of {cur} is given before that date. Add the ledger export of "
                f"the earlier rows to the account's folder, or give the balance as a "
                f"CASHBAL .tt line")))
    return out


def _kr_cash_of(rows: List[tuple], siblings: List[tuple], name: str,
                stable_cash: bool) -> List[Dict[str, Any]]:
    """The cash events of a Kraken LEDGER export for the FX-on-cash ledger
    v2 (lib/cash_events, tax-logic CA-FX-07), from its rows (`rows`: (row,
    line), every row its parse noted, in the file's order) and its account's
    other ledger exports (`siblings`): its moves and their fees
    (`_kr_cash_own`), its conversions (`_kr_cash_conversions`, a leg another
    export holds joined to it: `_kr_cash_siblings`; each export holding a
    leg gives the same FXCONV, kept once by cash_events.dedup_across_files,
    backlog TJ-0861), and, when it holds a balance row of cash, the
    account's balances (`_kr_cash_balances`). A fee is in its
    `feecurrency` (`_fee_ccy`): a coin fee is the position book's. The
    account's rows are walked first (`_kr_walk`): the order of the rows
    of one second, and what its Hybrid Earn rows park, which stays the
    book's cash. A cell it cannot read raises: the
    file's cash problem (`_finish_cash`)."""
    if not rows:
        return []
    walk = _kr_walk(rows, siblings, name)
    out, groups, bal_rows = _kr_cash_own(rows, name, stable_cash, walk)
    all_assets_by_ref = _kr_cash_siblings(rows, siblings, groups, stable_cash)
    out.extend(_kr_cash_conversions(groups, all_assets_by_ref, name, stable_cash))
    if bal_rows:
        out.extend(_kr_cash_balances(walk, name, stable_cash))
    return out


def _hybrid_unordered(u: _KrUnordered) -> str:
    """What the parse says of a second whose rows the balances put in no
    one order (`_kr_place`): the order read, what it finds, and what
    another order would."""
    def span(r: tuple) -> str:
        return (f"{r[0]:.10g}" if _kr_close(r[0], r[1])
                else f"{r[0]:.10g} to {r[1]:.10g}")
    bal = ""
    that = u.read if u.read == "the file's order" else "that order"
    if u.balance_range is not None and not _kr_close(*u.balance_range):
        bal = (f", and the balance after them with what is parked {span(u.balance_range)} "
               f"({u.balance:.10g} in {that})")
    return (f"{u.rows} rows of {u.bare} share one second on {u.day}, {u.hybrid} of them "
            f"Hybrid Earn rows, and their balances do not say which came first — read in "
            f"{u.read}, which finds {u.over:.10g} {u.bare} brought back beyond "
            f"what was parked and leaves {u.held:.10g} parked. Another order gives "
            f"{span(u.over_range)} brought back beyond what was parked, "
            f"{span(u.held_range)} left parked{bal}: where {that} is not the "
            f"true one, the difference is booked nowhere, or booked as an excess that is "
            f"none. Check Kraken's Earn history for that day: listing the second's rows in "
            f"the export in the order they happened in (each beginning at the balance the "
            f"one before it ended at, the export's rows in one order of time) settles it, "
            f"unless a row is missing from the export.")


class KrakenAccountContext:
    """What taxjson-brokerage shares between the parses of one account's
    Kraken exports (KrakenBrokerage.prepare_files): every instant-trade
    refid's ledger legs across all the ledger exports given, as
    (file as messages name it, resolved path, txid, row) in input
    order."""

    def __init__(self) -> None:
        self.instant_legs: Dict[str, list] = {}


class _KrRow(Row):
    """A row of the export as `read` yields it: the reader's dict (`cells`),
    its line, the dict lowered (`row`); what `classify` learned for `build` (a fill's
    type; a ledger row's ids, type, time, amount, fee and asset)."""
    __slots__ = ("row", "type_", "txid", "refid", "type_raw", "subtype", "asset_raw",
                 "time_raw", "dt", "date", "time", "ctx", "amount", "fee", "asset", "asset_name",
                 "fee_ccy")

    def __init__(self, cells: Dict[str, Any], line: int, row: Dict[str, Any]):
        super().__init__(cells, line)
        self.row, self.type_ = row, None
        self.txid = self.refid = self.type_raw = self.subtype = self.asset_raw = None
        self.time_raw = self.dt = self.date = self.time = self.ctx = None
        self.amount = self.fee = self.asset = self.asset_name = self.fee_ccy = None


@dataclass(frozen=True)
class _KrFill:
    """A fill's facts (`_fill_facts`): what the pair's legs and the fiat
    trade read of them (a dataclass: the loop's names begin with an
    underscore where a named tuple's may not)."""
    _txid: Any
    base: Any
    base_fee: Any
    base_qty: Any
    cost: Any
    ctx: Any
    dt: Any
    fee_note: Any
    legs: Any
    pair: Any
    price: Any
    quote: Any
    quote_fee: Any


@dataclass
class _KrParse:
    """What `read` hands the loop and the finish of one export: the file,
    its shown name, its kind (trades or ledgers), the row readers' assets
    before this parse (restored by `release`); a trades export's sibling
    ledger index, row ids, instants, and the fills counted for the notes;
    a ledgers export's instant trades by reference, trade references,
    txids seen, repeated rows and unbooked rows; the types ignored."""
    path: Path
    shown: str
    kind: str
    prev_assets: Optional[set]
    ledger_idx: Optional[Dict[str, list]] = None
    ids: Any = None
    kfee_fills: int = 0             # fills paid with Kraken fee credits
    trade_rows: int = 0             # a ledger's `trade` rows (the trades export books them)
    instant_of_line: Dict[int, Optional[tuple]] = field(default_factory=dict)
    unverified: int = 0
    coin_fee_fills: int = 0
    margin_fills: int = 0
    ignored_types: Dict[str, int] = field(default_factory=dict)
    instant_trades: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    trade_refids: Dict[str, str] = field(default_factory=dict)
    seen_txids: Dict[str, tuple] = field(default_factory=dict)
    repeated: int = 0
    unbooked: List[tuple] = field(default_factory=list)


class KrakenBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Kraken"
    _kr: Optional["_KrParse"] = None    # the parse's state, None between parses
    # Set by taxjson-brokerage (see `prepare_files`); None = this file
    # alone.
    account_context: Optional[KrakenAccountContext] = None

    @classmethod
    def prepare_files(cls, paths, *, tax_year=None, combined=False, taxable=None,
                      symbol_codes=None, project=None) -> KrakenAccountContext:
        """Index the instant-trade legs (ledger `spend` / `receive` rows)
        of every ledger export among `paths` by refid, so a trade whose
        legs a year boundary split into two exports is booked once
        (_join_instant_legs) instead of dropped from both as orphans
        (backlog TJ-0525). A file that does not read is left out here;
        its own parse reports it."""
        ctx = KrakenAccountContext()
        for p in map(Path, paths):
            try:
                text = read_broker_text(p)
                with io.StringIO(text) as f:
                    if _classify_header(f.readline()) != 'ledgers':
                        continue
                    f.seek(0)
                    for raw, _line in _dict_rows(f, p, 'ledger',
                                                 _LEDGER_REQUIRED):
                        row = _lower_row(raw)
                        ref = (row.get('refid') or '').strip()
                        typ = (row.get('type') or '').strip().lower()
                        if ref and typ in ('spend', 'receive'):
                            ctx.instant_legs.setdefault(ref, []).append(
                                (shown_name(p), str(p.resolve()),
                                 (row.get('txid') or '').strip(), row))
            except (BrokerageParseError, OSError, ValueError, csv.Error,
                    UnicodeDecodeError):
                continue
        return ctx
    # USD stablecoins (USDC/USDT/DAI/PYUSD/GUSD): US-dollar CASH (True — the
    # default, Canada's stated approximation, tax-logic CA-CRYPTO-02) or
    # PROPERTY like any coin (False — a US project: the IRS treats them
    # as digital assets, US-CRYPTO-02). taxjson-brokerage sets it from
    # --country; the parser itself makes no country choice.
    stablecoins_as_cash = True
    # The two models book different rows: a basis input (BaseBrokerage
    # `BASIS_INPUTS`, held by the conformance check).
    BASIS_INPUTS = ("stablecoins_as_cash",)

    def _norm(self, asset: str) -> str:
        """The asset as this book names it: stablecoins folded to USD
        only in cash mode."""
        return _normalize_asset(asset, fold_stable=self.stablecoins_as_cash)

    @property
    def _fiat(self):
        """Currencies (not property) in this book."""
        return (_FIAT_ASSETS if self.stablecoins_as_cash
                else _FIAT_CURRENCIES)

    def read(self, path: Path):
        """The export's kind from its header line (a trades or a ledgers
        CSV; another: no rows), the export's assets set for the row
        readers (`_KNOWN_ASSETS`, restored by `release`), the kind's state
        kept as `_kr`; the rows a generator over `_dict_rows`, each with
        its line, so each row's checks come in file order with its
        classification and build, as the one loop had them."""
        global _KNOWN_ASSETS
        with io.StringIO(read_broker_text(path)) as f:
            header_line = f.readline()
        kind = _classify_header(header_line)
        st = self._kr = _KrParse(path, shown_name(path), kind, _KNOWN_ASSETS)
        _KNOWN_ASSETS = self._export_assets(path)
        self._stems = {}                # the base's content-stem counters, per parse
        self.zero_tx_reason = None      # the tool reads it after the parse
        if kind == 'trades':
            st.ledger_idx = self._sibling_ledger_index(path)
            st.ids = RowIds(f"Kraken trades CSV {st.shown}", "txid")
            required = _TRADES_REQUIRED
        elif kind == 'ledgers':
            required = _LEDGER_REQUIRED
        else:
            # Not a Kraken export: no row accounting for it (`_rows_seen`
            # None), so taxjson-brokerage warns about the file as before.
            self._rows_seen = None
            return []
        return self._rows(io.StringIO(read_broker_text(path)), path,
                          'trades' if kind == 'trades' else 'ledger', required)

    @staticmethod
    def _rows(f, path: Path, kind: str, required):
        for raw, line in _dict_rows(f, path, kind, required):
            yield _KrRow(raw, line, _lower_row(raw))

    def classify(self, r) -> Outcome:
        """A trades row: its id checked for a duplicate, a type neither buy
        nor sell a skip kept for the warning, else a fill to build. A
        ledgers row: a repeated row (same txid and content) a non-event,
        the row's time, amount and asset read, then its type: a reward, a
        transfer out or in of a coin and fiat funding build; an Earn
        wallet move a non-event (its fee noted as unbooked); an instant
        trade's leg consumed into the pairing; a trade's ledger row and a
        fiat or zero-amount row non-events (counted for the note); a row
        that moves property or margin P&L a skip noted as unbooked."""
        st = self._kr
        if st.kind == 'trades':
            return self._classify_trade(r)
        return self._classify_ledger(r)

    def _classify_trade(self, r) -> Outcome:
        st = self._kr
        row, line, ids = r.row, r.line, st.ids
        ids.check(
            row.get('txid') or '', line,
            tuple(sorted((str(k), str(v or '').strip())
                         for k, v in row.items() if k)),
            " ".join(str(row.get(k) or '').strip() for k in
                     ('time', 'type', 'vol', 'pair')))
        type_ = r.type_ = (row.get('type') or '').strip().lower()
        if type_ not in ('buy', 'sell'):
            st.ignored_types[type_ or '?'] = (
                st.ignored_types.get(type_ or '?', 0) + 1)
            return Outcome.reported(f"type {type_ or '?'} (neither buy nor sell)")
        return r.build(self._build_fill)

    def _classify_ledger(self, r) -> Outcome:
        st = self._kr
        # Every row, for the cash events (`_finish_cash`): a copy, as the
        # export holds it (a leg's blank valuation cells are filled below).
        self.note_cash((dict(r.row), r.line))
        row = r.row
        txid = r.txid = (row.get('txid') or '').strip()
        if txid:
            ident = _ledger_identity(row)
            prev = st.seen_txids.get(txid)
            if prev is not None:
                if prev != ident:
                    raise ValueError(
                        f"Kraken ledger {st.shown} line "
                        f"{r.line}: ledger txid "
                        f"{_mask(txid)} appears twice with "
                        f"DIFFERENT content — the file is "
                        f"corrupt or two accounts' exports were "
                        f"merged; fix it before parsing.")
                st.repeated += 1
                return Outcome.reported("repeated ledger row")
            st.seen_txids[txid] = ident
        refid = r.refid = (row.get('refid') or '').strip()
        type_raw = r.type_raw = (row.get('type') or '').strip().lower()
        subtype = r.subtype = (row.get('subtype') or '').strip().lower()
        asset_raw = r.asset_raw = row.get('asset') or ''
        time_raw = r.time_raw = row.get('time') or ''
        dt = r.dt = self._local_dt(
            time_raw, f"ledger row (type={type_raw!r}, "
                      f"asset={asset_raw!r})")
        date = r.date = dt.strftime("%Y-%m-%d")
        time = r.time = dt.strftime("%H:%M:%S")
        ctx = r.ctx = f"ledger {st.shown} txid={_mask(txid)}"
        amount = r.amount = self._num(row, 'amount', ctx, required=True)
        fee = r.fee = self._num(row, 'fee', ctx)
        asset = r.asset = self._norm(asset_raw)
        asset_name = r.asset_name = _normalize_asset(asset_raw, fold_stable=False)
        fee_ccy = r.fee_ccy = _fee_ccy(row, asset_name)
        if ((type_raw == 'earn' and subtype == 'reward')
                or type_raw in ('staking', 'dividend')):
            return r.build(self._build_income)
        if (type_raw == 'earn'
              and subtype in _EARN_WALLET_MOVES
              ) or (type_raw.startswith('hybridearn')
                    and type_raw != 'hybridearnwithdrawal'
              ) or (type_raw == 'transfer'
                    and subtype in _STAKING_WALLET_MOVES):
            # (A Hybrid Earn row's fee in cash is a FLOW of the cash
            # events, `_kr_cash_own`, as a funding row's is: not said
            # unbooked here. Its fee in a coin still is.)
            if fee and not (type_raw.startswith(_HYBRID_EARN)
                            and fee_ccy in self._fiat):
                st.unbooked.append((f"{type_raw}/{subtype} fee",
                                    date, fee_ccy, -abs(fee)))
            return Outcome.nonevent("Kraken Earn wallet move "
                                    "(allocation/deallocation)")
        if ((type_raw in ('withdrawal', 'deposit',
                          'hybridearnwithdrawal')
             or (type_raw == 'transfer'
                 and subtype == 'transferpeertopeer'))
                and asset_name not in _FIAT_CURRENCIES):
            return r.build(self._build_ledger_transfer)
        if type_raw in ('spend', 'receive') and refid:
            self._fill_from_copies(row, refid, txid)
            self._add_instant_leg(
                st.instant_trades, row, st.shown, ctx, refid,
                type_raw, amount, fee, asset, asset_name, fee_ccy,
                date, time)
            return Outcome.consumed()           # paired after the loop
        if type_raw == 'trade':
            # Its fills are the trades export's: read into the coverage
            # check, consumed here (moved to a reported skip when the
            # trades export covers every one).
            st.trade_rows += 1
            st.trade_refids.setdefault(refid or f'?{txid}', date)
            return Outcome.consumed()
        if (asset_name in _FIAT_CURRENCIES
                and type_raw in _FIAT_FUNDING_TYPES):
            return r.build(self._build_fiat_funding)
        if (abs(amount) > 0 or fee
                or type_raw in ('margin', 'rollover', 'settled')):
            _kind = (f"{type_raw}/{subtype}" if subtype
                     else (type_raw or '?'))
            if amount:
                st.unbooked.append((_kind, date, asset_name, amount))
            else:
                st.unbooked.append((f"{_kind} fee", date, fee_ccy, -abs(fee)))
            return Outcome.reported(f"type {_kind} (not booked)")
        st.ignored_types[type_raw or '?'] = st.ignored_types.get(type_raw or '?', 0) + 1
        return Outcome.reported(f"type {type_raw or '?'} (fiat cash or nothing moved)")

    def build(self, r) -> "List[Dict[str, Any]] | Outcome":
        """The builder classify chose, run."""
        return r.builder(r)

    @builds("trade")
    def _build_fill(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A buy or sell fill: its facts (`_fill_facts`; a forex conversion
        or a relabel swap the non-event it finds), then the two legs of a
        crypto-to-crypto fill (`_build_pair`) or the fiat-quoted trade."""
        f = self._fill_facts(r)
        if isinstance(f, Outcome):
            return f
        if f.quote not in self._fiat:
            return self._build_pair(r, f)
        return self._build_fiat_trade(r, f)

    def _fill_facts(self, r) -> "_KrFill | Outcome":
        """A fill's cells read and checked: the time, the price, cost, fee
        and volume, the pair's base and quote (a stablecoin's depeg
        warned), the money checked, a margin fill counted, a forex
        conversion a non-event, a relabel swap a non-event, the ledger
        legs that verify the fee's currency when the ledgers export is
        beside the file."""
        st = self._kr
        row, type_ = r.row, r.type_
        path, ledger_idx = st.path, st.ledger_idx
        pair = row.get('pair') or ''
        time_raw = row.get('time') or ''
        dt = self._local_dt(time_raw, f"trades row (pair={pair!r})")
        st.instant_of_line[r.line] = self._instant_key(time_raw)
        ctx = f"trades {shown_name(path)} txid={_mask(row.get('txid'))}"

        price = self._num(row, 'price', ctx, required=True)
        cost = abs(self._num(row, 'cost', ctx, required=True))
        # Signed: a negative fee is a maker rebate credited to
        # the quote balance (backlog TJ-0749); it lowers a
        # buy's cost and raises a sale's proceeds.
        fee = self._num(row, 'fee', ctx)
        vol = self._num(row, 'vol', ctx, required=True)

        base, quote = _split_pair(pair, time_raw)
        # A stablecoin quoted against USD (USDC/USD) away from
        # the peg: the books fold it to USD cash, so say what
        # that drops (partition INPUTS-12).
        _rb = _normalize_asset(base, fold_stable=False)
        _rq = _normalize_asset(quote, fold_stable=False)
        if not self.stablecoins_as_cash:
            pass            # property: the fill books the price
        elif (_rb in _CASH_STABLECOINS
              and _rq in _FIAT_CURRENCIES):
            # USDC/USD, USDC/CAD, USDT/EUR: a CAD or EUR price
            # through the day's rate (re-audit A2-0590).
            warn_depeg(_rb, price, abs(vol),
                       dt.strftime('%Y-%m-%d'),
                       f"Kraken trades {shown_name(path)}",
                       currency=_rq)
        elif (_rq in _CASH_STABLECOINS
              and _rb in _FIAT_CURRENCIES and price):
            warn_depeg(_rq, 1.0 / price, abs(cost),
                       dt.strftime('%Y-%m-%d'),
                       f"Kraken trades {shown_name(path)}",
                       currency=_rb)
        base = self._norm(base)
        quote = self._norm(quote)
        self._check_fill_money(ctx, pair, price, cost, fee, vol,
                               quote in _FIAT_ASSETS)
        if (not self.stablecoins_as_cash
                and base in _FIAT_CURRENCIES
                and quote in _STABLECOINS):
            raise ValueError(
                f"Kraken trades {shown_name(path)}: pair {pair!r} "
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
            st.margin_fills += 1


        if base in _FIAT_CURRENCIES:
            # USD/CAD, USDC/USD (base folds to USD), USDT/CAD:
            # a currency conversion. Emitting it as a BUYSELL
            # of a `USD`/`CAD` "asset" put a phantom position
            # in the book; FX cash gains are not modeled here
            # (KNOWN_ISSUES) — count it and move on.
            return Outcome.nonevent(f"forex conversion {pair} "
                                f"(not modeled — docs/limits.md)")

        if base == quote:
            # Two spellings of ONE property (DOT28.S/DOT, or
            # ETH2.S/ETH under a ticker.map `GLOBAL ETH2 ETH`).
            # The wrap/unwrap moves nothing but the label —
            # booking it as a sale and rebuy realized a false
            # gain (S061-11).
            return Outcome.nonevent(self._same_property_swap(
                abs(vol), abs(cost), base,
                f"trades {shown_name(path)} line {r.line} "
                f"({pair})"))

        same_coin_hint(base, quote, vol, cost,
                       f"Kraken trades {shown_name(path)} "
                       f"line {r.line}")
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
                            f"Kraken trades {shown_name(path)}")
        else:
            base_coins, base_fee, quote_fee = abs(vol), 0.0, fee
            st.unverified += 1
        if base_fee:
            st.coin_fee_fills += 1
        # Coins that actually moved: a buy credits vol − coin
        # fee, a sell debits vol + coin fee.
        base_qty = (base_coins - base_fee if type_ == 'buy'
                    else base_coins + base_fee)
        fee_note = (f" (Kraken fee {base_fee:.10g} {base} taken "
                    f"in coin, per ledger)" if base_fee else '')
        return _KrFill(_txid=_txid, base=base, base_fee=base_fee, base_qty=base_qty, cost=cost, ctx=ctx, dt=dt, fee_note=fee_note, legs=legs, pair=pair, price=price, quote=quote, quote_fee=quote_fee)

    def _build_pair(self, r, f) -> List[Dict[str, Any]]:
        """A crypto-to-crypto fill: the base and the quote legs, valued in
        USD when the quote is a stablecoin or the ledger gives a value,
        else the swap fee kept for the run."""
        raw, type_ = r.cells, r.type_
        _txid, base, base_fee, base_qty = f._txid, f.base, f.base_fee, f.base_qty
        cost, ctx, dt, fee_note = f.cost, f.ctx, f.dt, f.fee_note
        legs, pair, quote, quote_fee = f.legs, f.pair, f.quote, f.quote_fee
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
                _dec_add(abs(cost), quote_fee) if type_ == 'buy'
                else max(_dec_sub(abs(cost), quote_fee), 0.0),
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
                _leg['price'] = M.units(_usd / _q, 8) if _q else 0.0
                _leg['net_amount'] = M.units(_usd, 8)
                _leg['gross_amount'] = M.units(_usd, 8)
        else:
            # Unpriced: taxjson-fill-crypto values the swap
            # from the received coin's units, which leave a
            # fee out; the fee rides along for the filler
            # (`swap_fee`), as the other paths' (backlog
            # TJ-1159). The ledger joined: a fee in the
            # received coin, as _build_instant_trade's
            # (TJ-0991); one in the spent coin is not in
            # amountusd's received value either. No ledger: a
            # buy's fee in the quote coin counts, as a fiat
            # quote's and Coinbase's (TJ-0981); a sell's
            # received amount is net of it, as a fiat quote's.
            _fee, _side = 0.0, ''
            if legs:
                _fee = base_fee if type_ == 'buy' else quote_fee
                _side = 'received'
            elif type_ == 'buy':
                _fee, _side = quote_fee, 'spent'
            if _fee > 0:
                for _leg in (base_leg, quote_leg):
                    _leg['swap_fee'] = f"{_fee!r} {_side}"
        # A blank txid cell still needs one stem for both
        # legs (fill-crypto values the swap once, A2-0998)
        # and one per fill (issue #93).
        _stem = _txid or self.content_stem(
            [v for k, v in raw.items() if k is not None], 'kr')
        base_leg['id'] = f'{_stem}-base'
        quote_leg['id'] = f'{_stem}-quote'
        return [base_leg, quote_leg]

    def _build_fiat_trade(self, r, f) -> List[Dict[str, Any]]:
        """A fiat-quoted fill: the trade row."""
        type_ = r.type_
        _txid, base, base_qty, cost = f._txid, f.base, f.base_qty, f.cost
        dt, fee_note, pair, price = f.dt, f.fee_note, f.pair, f.price
        quote, quote_fee = f.quote, f.quote_fee
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
        # into one and the inventory loses fills (e.g. several
        # partial fills of one order within the same second).
        if _txid:
            tx['id'] = _txid
        return [tx]

    @builds("dividend", "trade")
    def _build_income(self, r) -> Union[List[Dict[str, Any]], Outcome]:
        """A reward row: a non-positive amount refused, the income rows with
        the reward's evidence on each DIVIDEND."""
        st = self._kr
        row, txid, type_raw = r.row, r.txid, r.type_raw
        subtype, date, time = r.subtype, r.date, r.time
        ctx, amount, fee = r.ctx, r.amount, r.fee
        asset_name, fee_ccy = r.asset_name, r.fee_ccy
        if amount <= 0:
            raise ValueError(
                f"Kraken {ctx}: {type_raw}/{subtype} row "
                f"with a non-positive amount ({amount}) — a "
                f"reward reversal is not modeled; hand-check "
                f"it, enter the correction via a .tt file"
                f"{_TT_REMOVE}.")
        income_rows = self._income_row(
            row, ctx, asset_name, fee_ccy, amount, fee,
            date, time, txid)
        if isinstance(income_rows, Outcome):     # a reward its fee consumed: the template counts it
            return income_rows
        for income in income_rows:
            if income['action'] == 'DIVIDEND':
                income['staking_reward'] = self._reward_evidence(
                    row, asset_name, fee_ccy, amount, fee,
                    st.path, r.line, date, txid, income)
        return income_rows

    @builds("transfer", "trade")
    def _build_ledger_transfer(self, r) -> List[Dict[str, Any]]:
        """A coin's withdrawal, deposit or peer-to-peer transfer: the
        TRANSFER leg (its fee in the coin noted) and the fee's coin sale."""
        row, txid, type_raw = r.row, r.txid, r.type_raw
        subtype, date, time = r.subtype, r.date, r.time
        ctx, amount, fee = r.ctx, r.amount, r.fee
        asset_name, fee_ccy = r.asset_name, r.fee_ccy
        desc = (f"{type_raw}/{subtype}" if subtype
                else type_raw)
        # The fee is in COINS (or feecurrency): it is named
        # in the description, never put in the money `fee`
        # field of a row stamped USD — `tjs fees` read
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
            # The coin fee as data for `tjs transfers`
            # (its FEE column was empty, audit A2-0663).
            tx['fee_qty'] = abs(fee)
            tx['fee_currency'] = fee_ccy
        if txid:
            tx['id'] = f'{txid}-xfer'
        return [tx] + fee_coin_sale(
            row, ctx, fee, fee_ccy, type_raw, date, time, txid,
            fee_sym=self._norm(fee_ccy), fiat=self._fiat,
            account=self.DEFAULT_ACCOUNT)

    @builds("trade")
    def _build_fiat_funding(self, r) -> List[Dict[str, Any]]:
        """Fiat funding (a deposit or withdrawal of cash): no event but its
        fee's coin sale, else a reported skip; counted for the note as the
        loop counted it."""
        st = self._kr
        row, ctx, fee, fee_ccy = r.row, r.ctx, r.fee, r.fee_ccy
        type_raw, date, time, txid = r.type_raw, r.date, r.time, r.txid
        st.ignored_types[type_raw or '?'] = st.ignored_types.get(type_raw or '?', 0) + 1
        legs = fee_coin_sale(row, ctx, fee, fee_ccy, type_raw, date, time, txid,
                             fee_sym=self._norm(fee_ccy), fiat=self._fiat,
                             account=self.DEFAULT_ACCOUNT)
        return legs or Outcome.reported(f"type {type_raw} (fiat funding)")

    @builds("trade")
    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """After the loop, by the export's kind (nothing for another kind).
        Trades: the rows of other types, the unverified fee currencies,
        the margin, Kraken-fee and coin-fee fills noted; the rows of one
        second in their instants' order (before the template's marks, as
        the one parse had it). Ledgers: the trade rows' coverage, the
        repeated rows, the unbooked rows and the ignored types noted, the
        instant trades paired by reference (a leg booked with its counter
        leg in another file a non-event, its count moved from consumed),
        the zero-transactions reason. Then the file's cash events
        (`_finish_cash`: a ledgers export's; none of another kind)."""
        st = self._kr
        if st.kind == 'trades':
            transactions = self._finish_trades(transactions, path)
        elif st.kind == 'ledgers':
            transactions = self._finish_ledgers(transactions, path)
        self._finish_cash()
        return transactions

    def _finish_cash(self) -> None:
        """The file's cash events (`cash_events`, or why not, `cash_problem`):
        a ledgers export's built from every row classify noted (`note_cash`)
        with the account's other ledger exports read beside it
        (`_kraken_siblings`: the run's detection, which taxjson-brokerage
        hands the parse as `sibling_finder`; none on a bare parse), by
        `_kr_cash_of`; none of a trades export (its fills are in the
        position book) or another file. The asset codes are read with no
        bonded-staking fold (`_KNOWN_ASSETS` None), as lib/cash_readers/
        kraken.py read them after the parse until PLAN step 16 commit 11."""
        st = self._kr
        rows = list(self._cash_rows)
        if st.kind != 'ledgers' or not rows:
            self._cash_events_from(list)
            return

        def build() -> List[Dict[str, Any]]:
            global _KNOWN_ASSETS
            prev, _KNOWN_ASSETS = _KNOWN_ASSETS, None
            try:
                return _kr_cash_of(rows, _kr_sibling_ledger_rows(self._kraken_siblings(st.path)),
                                   st.shown, self.stablecoins_as_cash)
            finally:
                _KNOWN_ASSETS = prev
        self._cash_events_from(build)

    def _finish_trades(self, transactions, path):
        st = self._kr
        ignored_types, unverified, ledger_idx = st.ignored_types, st.unverified, st.ledger_idx
        margin_fills, coin_fee_fills = st.margin_fills, st.coin_fee_fills
        if ignored_types:
            # A fill whose type is blank or not buy/sell has no booking:
            # an UNBOOKED warning (echoed by `tjs run`, fatal under
            # --strict) as on the ledger and Coinbase twins — it was a
            # quiet `note:` only the .sum showed (re-audit A2-0245).
            detail = ', '.join(f"{k} x{v}"
                               for k, v in sorted(ignored_types.items()))
            emit_line(f"warning: UNBOOKED: Kraken trades {shown_name(path)}: "
                  f"{sum(ignored_types.values())} row(s) whose type is "
                  f"neither buy nor sell ({detail}) are NOT in the books "
                  f"— fix the type cell, or enter each fill via a .tt "
                  f"file and remove the row from the export.")
        if unverified:
            where = ("no Kraken ledgers export (kr_ledgers*.csv) is in "
                     "the same folder" if ledger_idx is None else
                     "the ledgers export(s) beside it do not contain "
                     "them")
            emit_line(f"warning: Kraken trades {shown_name(path)}: the fee currency "
                  f"of {unverified} fill(s) can't be verified — {where}. "
                  f"Kraken's trades CSV states every fee in quote units "
                  f"even when Kraken took it in the traded coin, which "
                  f"books coins that were never received; add the ledgers export covering "
                  f"these dates beside the trades file.")
        if margin_fills:
            emit_line(f"warning: Kraken trades {shown_name(path)}: {margin_fills} "
                  f"fill(s) carry a nonzero `margin` value — they are "
                  f"booked as ordinary SPOT buys/sells. The margin "
                  f"position's rollover (financing) and settlement "
                  f"ledger rows are NOT modeled; hand-check these "
                  f"positions (R1-107).")
        if st.kfee_fills:
            emit_line(f"note: Kraken trades {shown_name(path)}: {st.kfee_fills} "
                  f"fill(s) paid their fee with Kraken fee credits (KFEE) "
                  f"— booked with no fee (the credits cost nothing).")
        if coin_fee_fills:
            emit_line(f"note: Kraken trades {shown_name(path)}: {coin_fee_fills} "
                  f"fill(s) had the fee taken in the traded coin (per the "
                  f"ledger) — booked as fewer coins received / more "
                  f"coins given, with no quote-currency fee; those fees "
                  f"are therefore NOT in the fee reports (fees.rpt, "
                  f"fees-sum, the .sum FEES line).")
        self._order_within_seconds(transactions, st.instant_of_line)
        self.instant_of_line = st.instant_of_line
        return transactions

    def _finish_ledgers(self, transactions, path):
        st = self._kr
        ignored_types, trade_refids, repeated = st.ignored_types, st.trade_refids, st.repeated
        unbooked, instant_trades = st.unbooked, st.instant_trades

        # Ledger `trade` rows are NOT parsed here (the trades export is
        # the authoritative per-fill record) — the generic "transfers
        # don't affect gains" wording was actively misleading for a
        # ledgers-only user whose actual trades were being dropped.
        trade_rows = st.trade_rows
        if trade_rows:
            self._check_trade_coverage(path, trade_rows, trade_refids)
        if repeated:
            emit_line(f"note: Kraken ledger {shown_name(path)}: {repeated} repeated "
                  f"ledger row(s) (same txid and content as an earlier "
                  f"row — overlapping exports pasted together) skipped.")
        if unbooked:
            kinds: Dict[str, int] = {}
            for k, _d, _a, _m in unbooked:
                kinds[k] = kinds.get(k, 0) + 1
            dates = sorted(d for _k, d, _a, _m in unbooked)
            assets = sorted({a for _k, _d, a, _m in unbooked})
            emit_line(f"warning: UNBOOKED: Kraken ledger {shown_name(path)}: "
                  f"{len(unbooked)} row(s) of type(s) the parser does not "
                  f"book ({', '.join(f'{k} x{n}' for k, n in sorted(kinds.items()))}"
                  f"; {dates[0]}..{dates[-1]}; assets "
                  f"{', '.join(assets[:8])}) move property or margin "
                  f"P&L — an airdrop is an acquisition, a conversion or "
                  f"sale a disposition. They are NOT in the books: enter "
                  f"each via a .tt file.")
        if ignored_types:
            detail = ', '.join(f"{k} x{v}" for k, v in sorted(ignored_types.items()))
            emit_line(f"note: Kraken ledger {shown_name(path)}: ignored "
                f"{sum(ignored_types.values())} fiat-cash or zero-amount "
                f"row(s) "
                f"({detail}) — moving your own cash to or from Kraken (a "
                f"bank deposit, withdrawal or transfer), or a row that "
                f"moves nothing, is not a tax event.")
            if not unbooked and not self.zero_tx_reason:
                # A ledger of only cash moves books nothing, correctly:
                # not the "parsed to 0 transactions" regression warning
                # (and not a `run --strict` refusal) — audit A2-0703.
                self.zero_tx_reason = (
                    f"{sum(ignored_types.values())} fiat-cash or "
                    f"zero-amount row(s): not tax events")
        self._finish_hybrid()
        legs_index = self.account_context.instant_legs if self.account_context else {}
        for refid, sides in instant_trades.items():
            # Joined whether or not this export holds both sides: one
            # holding the spend and the first of two receipts booked
            # only those, and the later receipt in the next export was
            # dropped as booked (backlog TJ-0661).
            if refid in legs_index:
                joined = self._join_instant_legs(refid, path,
                                                 legs_index[refid])
                if isinstance(joined, str):
                    # Another export of the account books the trade: the
                    # legs the loop consumed into the pairing are non-events
                    # here (the template moves their count).
                    for _side in sides.values():
                        for _leg in _side.values():
                            self.recount(Outcome.nonevent(
                                f"instant-trade leg booked with its "
                                f"counter-leg in {joined}"), self._own_rows(_leg))
                    continue
                if joined:
                    sides = joined
            transactions.extend(self._build_instant_trades(sides, refid))
        known, _reported, unrecognized = self.skip_classes()
        nonevents = sum(known.values())
        if (not transactions and not unbooked
                and not self.zero_tx_reason
                and (nonevents or ignored_types)
                and not unrecognized):
            # Every row is a recognized non-event (a same-coin relabel,
            # an Earn wallet move, a fiat deposit): the parser worked,
            # so no "parsed to 0 transactions" warning, which `run
            # --strict` refuses (re-audit A2-0583).
            self.zero_tx_reason = (
                f"its {nonevents + sum(ignored_types.values())} row(s) "
                f"are recognized non-events")
        return transactions

    def _finish_hybrid(self) -> None:
        """An ATTENTION line per asset whose Hybrid Earn rows brought back
        more than the account's ledger rows had parked (`_kr_walk` over
        the export's rows and the account's other ledger exports, read
        only when the export has such a row): the excess, which the
        books take as neither income nor coins acquired, and what that
        leaves (`_hybrid_left`). Kraken's export does not say what the
        excess is, so nothing is booked for it (backlog TJ-4954). And one
        per second holding Hybrid Earn rows whose order the balances do
        not give, where the order changes what is found: read in the
        file's order, with what another order gives (`_hybrid_unordered`).
        A row an export of an earlier name holds too is that export's to
        say."""
        st = self._kr
        rows = list(self._cash_rows)
        if not any(_kr_is_hybrid(r) for r, _i in rows):
            return
        walk = _kr_walk(
            rows, _kr_sibling_ledger_rows(self._kraken_siblings(st.path)), st.shown)
        by_asset: Dict[str, list] = {}
        for where, (bare, over, day) in walk.excess.items():
            if where not in walk.earlier:
                by_asset.setdefault(bare, []).append((day, over))
        for bare, found in sorted(by_asset.items()):
            days = sorted(d for d, _o in found)
            total = 0.0
            for _d, over in found:
                total = _dec_add(total, over)
            span = days[0] if days[0] == days[-1] else f"{days[0]}..{days[-1]}"
            emit_line(f"warning: ATTENTION: Kraken ledger {st.shown}: Hybrid Earn brought "
                      f"back {total:.10g} {bare} more than this account's ledger rows had "
                      f"parked ({len(found)} row(s), {span}) — the excess is not booked as "
                      f"income or as coins acquired: the export does not say what it is. "
                      f"Check Kraken's Earn history (a reward, or coins parked before the "
                      f"first export here); {self._hybrid_left(bare, total)}.")
        for u in sorted(walk.unordered, key=lambda u: (u.bare, u.day)):
            if u.own:
                emit_line(f"warning: ATTENTION: Kraken ledger {st.shown}: "
                          f"{_hybrid_unordered(u)}")

    def _hybrid_left(self, bare: str, total: float) -> str:
        """What an excess of `total` units of `bare` leaves, for the
        ATTENTION line: a coin, fewer units in the books than at Kraken;
        the project's own currency, cash with no FX gain to compute; another
        currency (at the cash basis a USD stablecoin too), a cash move of
        the cash events (`_kr_cash_own`), which the FX-cash ledger takes
        at the day's rate under [settings] fx_cash_inflow_cost = "spot"
        and asks about otherwise (the parse is not told that setting, so
        the line says both)."""
        if bare not in self._fiat:
            return (f"the books hold {total:.10g} {bare} fewer than Kraken shows until "
                    f"the excess is entered via a .tt file")
        country = getattr(self, 'country', None)
        if country:
            from taxjson.lib.country import home_currency
            if self._norm(bare) == home_currency(country):
                return (f"{bare} is the project's own currency: no coin is missing, and "
                        f"`tjs fx-cash` computes no gain or loss on it")
        return (f"{bare} is cash in the books, so no coin is missing; `tjs fx-cash "
                f"--ledger v2` takes the excess as cash that came in: at the day's rate "
                f"when [settings] fx_cash_inflow_cost = \"spot\", otherwise undeclared, "
                f"with no figure until a .tt `CASHMOVE` line says what it was")

    def release(self) -> None:
        """The export's state released; the row readers' assets restored."""
        global _KNOWN_ASSETS
        if self._kr is not None:
            _KNOWN_ASSETS = self._kr.prev_assets
        self._kr = None

    def _kraken_siblings(self, path: Path) -> List[Path]:
        """The other CSVs in the folder `tjs run` reads as Kraken
        exports — by the same detection (content first, then the
        kr_/kraken name), not by file name alone: the caller's finder
        (`sibling_finder`); none on a bare parse."""
        return self._same_broker_siblings(Path(path), 'kraken',
                                          by_name=kraken_named)

    def _export_assets(self, path: Path) -> set:
        """Every bare asset code of the Kraken export `path` belongs to
        (the file and its Kraken siblings in the folder): what a bonded
        staking code `<COIN><dd>.S` is folded against. Unreadable rows
        are skipped here (the parse itself reports them)."""
        codes: List[str] = []
        files = [path] + self._kraken_siblings(path)
        for p in files:
            try:
                rows = list(csv.DictReader(io.StringIO(read_broker_text(p))))
            except (BrokerageParseError, OSError, csv.Error,
                    UnicodeDecodeError):
                continue
            for raw in rows:
                row = {str(k or '').strip().lower(): v
                       for k, v in raw.items() if k is not None}
                if row.get('asset'):
                    codes.append(str(row['asset']))
                pair = str(row.get('pair') or '')
                if pair:
                    try:
                        codes.extend(_split_pair(pair))
                    except ValueError:
                        pass
        return known_assets_of(codes)

    def _num(self, row, field, context, required=False) -> float:
        raw = row.get(field)
        if required and (raw is None or not str(raw).strip()):
            raise ValueError(
                f"Kraken {context}: empty {field!r} — refusing to read "
                f"it as 0.")
        try:
            return strict_money(raw, field)
        except ValueError as e:
            # The row (and its file) first, as the other refusals: in
            # brackets after the cell, the file was named again in
            # front of it (backlog TJ-0236).
            raise ValueError(f"Kraken {context}: {e}") from None

    def _local_dt(self, time_raw: str, what: str):
        """Kraken timestamps are UTC (`2025-06-01 12:00:00.1234`).
        Returned as the user's local wall-clock time (see
        lib/timezones): the taxpayer's local DATE decides the tax year
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

    def _instant_key(self, time_raw: str) -> Optional[tuple]:
        """(UTC second, fraction) of a Kraken timestamp, the order of two
        rows in one second (`… 15:00:00.1000` before `… 15:00:00.9000`),
        or None when the stamp does not read."""
        from decimal import Decimal, InvalidOperation
        head, dot, frac = (time_raw or '').strip().partition('.')
        dt = self.parse_date(head, "%Y-%m-%d %H:%M:%S")
        if dt is None:
            return None
        f = Decimal(0)
        if dot and frac.strip().isdigit():
            try:
                f = Decimal('0.' + frac.strip())
            except InvalidOperation:
                pass
        return (dt, f)

    def _order_within_seconds(self, transactions, key_of_line) -> None:
        """Reorder, in place, the rows that share one clock second by the
        fraction of a second the export states: the books keep rows of
        one second in the order the parser gives them, so a descending
        export booked a rebuy at 15:00:00.9 before the sale at
        15:00:00.1 it followed (backlog TJ-0746). Rows of different
        seconds, and rows of one instant, keep their order."""
        order_within_seconds(transactions, self.row_line, key_of_line)

    def _sibling_ledger_index(self, path: Path) -> Optional[Dict[str, list]]:
        """refid -> ledger rows, from every Kraken LEDGER export in the
        trades file's folder (_kraken_siblings). None when there
        is none. Several ledgers (one per year) are all indexed: a
        trades export and the ledger covering it need not share a
        filename year."""
        idx: Dict[str, list] = {}
        seen: Dict[str, tuple] = {}
        found = False
        for p in self._kraken_siblings(path):
            try:
                _text = read_broker_text(p)
            except (BrokerageParseError, OSError):
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
                                _fill_valuation(prev[2], row)
                                if prev[0] != _ledger_identity(row):
                                    raise ValueError(
                                        f"Kraken ledger exports "
                                        f"{prev[1]} and {shown_name(p)} both "
                                        f"carry ledger txid "
                                        f"{_mask(tx_id)} with DIFFERENT "
                                        f"content — they cannot both be "
                                        f"this account's ledger; remove "
                                        f"the wrong one.")
                                continue
                            seen[tx_id] = (_ledger_identity(row),
                                           shown_name(p), row)
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
        ctx = f"trade {_mask(row.get('txid'))} ({pair}, {row.get('time')!r})"
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
        # Signed: Kraken's balance is the prior one + amount − fee, so a
        # negative fee is a credit (a maker rebate). abs() booked a
        # rebate as a charge (backlog TJ-0749).
        b_fee = self._num(bl, 'fee', ctx)
        q_fee = self._num(ql, 'fee', ctx)
        if b_fee < 0:
            raise ValueError(
                f"Kraken {ctx}: the ledger credits a fee of "
                f"{-b_fee:.10g} {base} (a negative fee on the {base} "
                f"leg) — a rebate paid in the traded coin is not "
                f"modeled; enter this fill via a .tt file{_TT_REMOVE}.")
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
        if not money_close(b_amt, vol, abs_tol=2e-7, rel_tol=1e-6, scale=abs(vol)):
            raise ValueError(
                f"Kraken {ctx}: ledger {base} amount {abs(b_amt):.10g} "
                f"!= trades vol {abs(vol):.10g}.")
        if not money_close(q_amt, cost, abs_tol=2e-4, rel_tol=1e-6, scale=abs(cost)):
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
            self._kr.kfee_fills += 1
            return abs(b_amt), 0.0, 0.0
        # Fee in quote units: the trades export states it to more
        # decimals than the ledger — keep that value, but only when the
        # two agree (a disagreement means the columns are not what we
        # think they are).
        if abs(trade_fee - q_fee) > max(2e-4, 0.01 * abs(trade_fee)):
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
        if not money_close(cost, gross, abs_tol=slack, rel_tol=0.005,
                           additive=True):
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
                            asset: str, where: str) -> str:
        """A swap whose two sides fold to the same asset (DOT <-> DOT28.S,
        or a ticker.map GLOBAL fold) is a non-event when the quantities
        match (its label returned for the caller to count); any
        other shape (a fee in the coin, a non-1:1 rate) has no modeled
        booking and raises."""
        if abs(qty_out - qty_in) <= 1e-8 * max(qty_out, qty_in, 1e-12):
            return f"{asset} relabel swap (two codes of the same property)"
        raise ValueError(
            f"Kraken {where}: a swap between two spellings of {asset} "
            f"(same property for ACB purposes) with unequal quantities "
            f"({qty_out:.10g} out, {qty_in:.10g} in) — not modeled; enter "
            f"the difference via a .tt file and remove the row.")


    def _add_instant_leg(self, instant_trades, row, src, ctx, refid,
                         type_raw, amount, fee, asset, asset_name, fee_ccy,
                         date, time) -> None:
        """Add one ledger spend/receive row (`src`: the file as messages
        name it) to its refid's legs in `instant_trades`, refusing a leg
        the books cannot hold."""
        if fee and self._norm(fee_ccy) != asset:
            raise ValueError(
                f"Kraken {ctx}: instant-trade {type_raw} "
                f"leg in {asset_name} with its fee in "
                f"{fee_ccy} (feecurrency) — not supported; "
                f"enter this trade via a .tt file{_TT_REMOVE}.")
        if amount == 0:
            # A leg that moves nothing has no direction: a
            # 0-unit trade the books cannot hold, and never
            # a "dust" leg to leave out (pre-release review
            # H1; v0.20 refused it at the schema check).
            raise ValueError(
                f"Kraken {ctx}: a {type_raw} row of "
                f"{asset_name} on {date} with amount 0 — an "
                f"instant-trade leg that moves no coins has no "
                f"direction and cannot be booked, and leaving "
                f"it out could lose a real trade. This "
                f"account's files are not booked until it is "
                f"fixed: correct the row in the export, or "
                f"enter the trade via a .tt file{_TT_REMOVE}.")
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
                       f"Kraken ledger {src}")
        if asset in side:
            # Split settlement: a refid can carry two rows
            # of the same leg — assignment silently
            # discarded the earlier amount.
            leg = side[asset]
            leg['amount'] += abs(amount)
            leg['fee'] += abs(fee)
            leg['rows'][src] = leg['rows'].get(src, 0) + 1
            if leg['usd'] is not None and usd_v is not None:
                leg['usd'] += usd_v
            else:
                leg['usd'] = None
        else:
            side[asset] = {
                'date': date, 'time': time, 'asset': asset,
                'amount': abs(amount), 'fee': abs(fee),
                'usd': usd_v,
                # The file as messages name it.
                'src': src,
                # The unfolded name (USDC, not USD): the
                # fiat-for-fiat branch names the stablecoin
                # and checks its peg (re-audit A2-1284).
                'raw': asset_name,
                # The ledger rows folded into this leg, by the file holding
                # each (a receipt two exports split is one leg: TJ-4463).
                'rows': {src: 1},
            }

    def _fill_from_copies(self, row, refid, txid) -> None:
        """Fill the instant-trade leg `row`'s blank valuation cells from
        another export's copy of the same ledger entry (prepare_files'
        index): two overlapping exports that each hold the whole trade,
        the older without amountusd, booked it at two values and the run
        stopped on a restated row (backlog TJ-0738)."""
        legs = (self.account_context.instant_legs if self.account_context
                else {}).get(refid, ())
        if not txid:
            return
        ident = None
        for _src, _key, other_txid, other in legs:
            if other_txid != txid:
                continue
            if ident is None:
                ident = _ledger_identity(row)
            if _ledger_identity(other) == ident:
                _fill_valuation(row, other)

    def _instant_leg_from_row(self, instant_trades, row, src) -> None:
        """_add_instant_leg for a raw ledger row of `src` (another export
        of the account: _join_instant_legs)."""
        txid = (row.get('txid') or '').strip()
        refid = (row.get('refid') or '').strip()
        type_raw = (row.get('type') or '').strip().lower()
        asset_raw = row.get('asset') or ''
        dt = self._local_dt(row.get('time') or '',
                            f"ledger row (type={type_raw!r}, "
                            f"asset={asset_raw!r})")
        ctx = f"ledger {src} txid={_mask(txid)}"
        asset_name = _normalize_asset(asset_raw, fold_stable=False)
        self._add_instant_leg(
            instant_trades, row, src, ctx, refid, type_raw,
            self._num(row, 'amount', ctx, required=True),
            self._num(row, 'fee', ctx), self._norm(asset_raw), asset_name,
            _fee_ccy(row, asset_name), dt.strftime("%Y-%m-%d"),
            dt.strftime("%H:%M:%S"))

    def _join_instant_legs(self, refid, path, entries):
        """The legs of instant trade `refid` across the account's ledger
        exports (`entries`: prepare_files' (file, key, txid, row) for the
        refid, in input order), when this file holds only some of them:
        a trade whose spend is in the 2025 export and its receive in the
        2026 one was dropped from both as two orphan legs (backlog
        TJ-0525), and one whose 2025 export holds the spend and the
        first of two receipts booked only those (TJ-0661). Each ledger
        entry counts once (one txid in two overlapping exports is one
        entry; with different content it is refused). The trade is
        booked once: by the first export holding every leg, else by the
        first export holding one. Returns the joined {'spend',
        'receive'} sides when this file books the legs of others, the
        name (as messages give it) of the export that books the trade
        when that is another, None when this file books its own legs
        (it holds them all, or the exports together do not complete
        the trade: an orphan stays an orphan)."""
        here = str(Path(path).resolve())
        files: List[str] = []
        names: Dict[str, str] = {}
        held: Dict[str, set] = {}
        for src, key, txid, _row in entries:
            if key not in files:
                files.append(key)
                names[key] = src
            held.setdefault(key, set()).add(txid)
        if len(files) < 2 or here not in files:
            return None
        every = set().union(*held.values())
        if '' in every:
            return None     # nothing tells a repeat from a second leg
        if held[here] == every:
            return None     # this export holds every leg: its own
        seen: Dict[str, tuple] = {}
        rows = []
        for src, _key, txid, row in entries:
            prev = seen.get(txid)
            if prev is not None:
                # The kept copy takes a valuation only this one has (an
                # older export first lost the newer one's amountusd,
                # the swap then valued at a daily close: TJ-0738).
                _fill_valuation(prev[2], row)
                if prev[1] != _ledger_identity(row):
                    raise ValueError(
                        f"Kraken ledger exports {prev[0]} and {src} both "
                        f"carry ledger txid {_mask(txid)} with DIFFERENT "
                        f"content — two downloads disagree on one ledger "
                        f"entry (a restated row). Keep the newer download "
                        f"only, or correct the row, and run again.")
                continue
            row = dict(row)     # the context's row stays as read
            seen[txid] = (src, _ledger_identity(row), row)
            rows.append((src, row))
        if {(r.get('type') or '').strip().lower()
                for _s, r in rows} != {'spend', 'receive'}:
            return None
        owner = next((k for k in files if held[k] == every), files[0])
        if owner != here:
            return names[owner]
        joined: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for src, row in rows:
            self._instant_leg_from_row(joined, row, src)
        others = sorted({src for src, _row in rows} - {shown_name(path)})
        emit_line(f"note: Kraken ledger {shown_name(path)}: refid "
                  f"{_mask(refid)}: an instant trade whose legs are in "
                  f"{shown_name(path)} and {', '.join(others)} — booked "
                  f"once, here.")
        return joined[refid]

    def _check_trade_coverage(self, path: Path, trade_rows: int,
                              trade_refids: Dict[str, str]) -> None:
        """Every ledger `trade` refid must be a fill in a trades export
        beside the ledger — the trades export is what books them. One
        that covers a shorter date range used to drop every sale in the
        gap behind the same note a complete run prints (R1-104). Now
        the gap is an UNBOOKED warning (echoed to the console by
        `tjs run`, fatal under `run --strict`) with counts, dates
        and masked refids."""
        # The rows the loop consumed into this check are the trades
        # export's, booked there or not: reported here (the note or the
        # warning says which), never this file's.
        self.recount(Outcome.reported("trade row (the trades export's)"), trade_rows)
        txids = self._sibling_trades_txids(path)
        missing = sorted((d, r) for r, d in trade_refids.items()
                         if txids is None or r not in txids)
        if not missing:
            self.zero_tx_reason = (f"its {trade_rows} trade row(s) are "
                                   f"booked from the trades export")
            emit_line(f"note: Kraken ledger {shown_name(path)}: {trade_rows} trade "
                  f"row(s) ({len(trade_refids)} trade(s)) are booked from "
                  f"the trades export beside it — every one matched.")
            return
        dates = [d for d, _ in missing]
        masked = ', '.join(_mask(r) for _, r in missing[:5])
        more = f" +{len(missing) - 5} more" if len(missing) > 5 else ''
        why = ("no Kraken trades export (kr_*.csv / *kraken*.csv) is "
               "in the same folder" if txids is None else
               "the trades export(s) beside it do not contain them — "
               "they likely cover a shorter date range")
        emit_line(f"warning: UNBOOKED: Kraken ledger {shown_name(path)}: "
              f"{len(missing)} trade(s) of {len(trade_refids)} "
              f"({dates[0]}..{dates[-1]}; refids {masked}{more}) are NOT "
              f"booked — {why}. The ledger's trade rows are not parsed; "
              f"supply the trades export covering the ledger's dates, "
              f"beside it.")

    def _sibling_trades_txids(self, path: Path) -> Optional[set]:
        """Every txid in the Kraken TRADES export(s) in the ledger's
        folder, or None when there is none."""
        txids: set = set()
        found = False
        for p in self._kraken_siblings(path):
            try:
                _text = read_broker_text(p)
            except (BrokerageParseError, OSError):
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

    def _reward_evidence(self, row, asset, fee_ccy, amount, fee,
                         path, line, date, txid, income):
        """Source facts for one income event; never an additional tax row.

        Monetary values stay in the stated valuation currency. Old exports
        lack USD valuations: preserve that absence until the pricing stage
        records the actual quote used, rather than calling it a broker price.
        """
        known = bool((row.get('fee') or '').strip())
        gross_qty, fee_qty = abs(amount), abs(fee)
        same = fee_ccy == asset
        net_qty = _dec_sub(gross_qty, fee_qty) if same else gross_qty
        ctx = f"ledger {shown_name(path)} line {line}"
        gross_value = (abs(strict_money(row['amountusd'], 'amountusd', ctx))
                       if row.get('amountusd') not in (None, '') else None)
        fee_value = (abs(strict_money(row['feeusd'], 'feeusd', ctx))
                     if known and row.get('feeusd') not in (None, '') else None)
        valuation_currency = 'USD'
        if gross_value is None and asset in _FIAT_CURRENCIES:
            gross_value = gross_qty
            valuation_currency = asset
        elif gross_value is None and asset in _STABLECOINS:
            gross_value = gross_qty
        if known and fee_qty == 0:
            fee_value = 0.0
        elif known and fee_value is None:
            if same and gross_value is not None and gross_qty:
                fee_value = gross_value * fee_qty / gross_qty
            elif valuation_currency == 'USD' and (
                    fee_ccy == 'USD' or fee_ccy in _STABLECOINS):
                fee_value = fee_qty
        value = {
            'schema_version': 1, 'broker': 'kraken',
            'kind': ('staking' if (row.get('type') or '').strip().lower() == 'staking'
                     or ((row.get('type') or '').strip().lower() == 'earn'
                         and (row.get('subtype') or '').strip().lower() == 'reward'
                         and asset not in _FIAT_CURRENCIES and asset not in _STABLECOINS)
                     else 'other_reward'),
            'source_type': '/'.join(x for x in (row.get('type'), row.get('subtype')) if x),
            'source_file': shown_name(path), 'source_line': line,
            'source_timestamp': (row.get('time') or '').strip(),
            'source_id': txid, 'asset': asset,
            'gross_quantity': gross_qty, 'fee_quantity': fee_qty if known else None,
            'fee_currency': fee_ccy, 'net_quantity': net_qty,
            'valuation_currency': valuation_currency,
            'gross_value': gross_value, 'fee_value': fee_value,
            # The net as the difference of the two figures, exact, and
            # the gross per coin at a price's eight places: the float
            # difference and quotient printed their binary error in
            # --json (9.190000000000001; TJ-4930).
            'net_value': (M.exact_total((gross_value, -fee_value))
                          if gross_value is not None and fee_value is not None else None),
            'price': (M.units(gross_value / gross_qty, 8)
                      if gross_value is not None and gross_qty else None),
            'commission_known': known,
            'commission_method': ('unknown' if not known else
                                  'withheld_from_reward' if same or fee_qty == 0 else
                                  'paid_in_other_asset'),
        }
        # The source's gross value per coin is not always the selected
        # book price (net-value rounding, cash/stablecoin treatment). Keep
        # both facts and identify the price the emitted row actually uses.
        book_price = income.get('price', 0)
        if book_price > 0:
            price_source = 'broker amount / reward quantity'
            if asset in _FIAT_CURRENCIES:
                price_source = 'fiat unit value'
            elif asset in _STABLECOINS and (
                    not self.stablecoins_as_cash or asset in _CASH_STABLECOINS):
                price_source = ('stablecoin par assumption' if book_price == 1
                                else 'stablecoin net income / reward quantity')
            value['valuation'] = {
                'price': book_price, 'currency': income['currency'],
                'date': date, 'source': price_source,
                'fx_rate': None, 'target_currency': '',
            }
        return value

    def _income_row(self, row, ctx, asset_name, fee_ccy, amount, fee,
                    date, time, txid) -> Union[List[Dict[str, Any]], Outcome]:
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
            # In decimal, as the ledger states them: the binary
            # difference of two 8-decimal amounts carries a 17-digit
            # tail (0.0027 - 0.00013116 = 0.0025688399999999998) into
            # the books, every view and the .tt round trip.
            net_qty = _dec_sub(gross_qty, fee_qty)
            if net_qty <= 1e-12:
                return Outcome.nonevent("Kraken Earn reward fully consumed by its fee")
            if net_qty < QTY_ZERO and asset_name not in self._fiat:
                # Under the books' zero: its acquisition cannot be a
                # BUYSELL (0 units, refused by the schema); left out
                # only when its income is nil too (CA-CRYPTO-11).
                self._dust_reward(ctx, date, asset_name, net_qty,
                                  usd_value)
                return Outcome.nonevent(_DUST_LABEL)
            net_usd = None
            if usd_value:
                f_usd = fee_usd or 0.0
                if fee_qty and not f_usd:
                    # No feeusd column: scale the gross value.
                    f_usd = usd_value * fee_qty / gross_qty
                net_usd = usd_value - f_usd
            return ledger_reward_legs(
                asset_name, date, time, net_qty, txid,
                usd_value=net_usd, gross_qty=gross_qty, fee_qty=fee_qty,
                stablecoins_as_cash=self.stablecoins_as_cash,
                account=self.DEFAULT_ACCOUNT)
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
        if gross_qty < QTY_ZERO and asset_name not in self._fiat:
            # A reward under the books' zero is not booked when worth
            # nothing (see the same-currency case); a coin fee on it
            # still left.
            self._dust_reward(ctx, date, asset_name, gross_qty, usd_value)
            return (fee_coin_sale(row, ctx, fee_qty, fee_ccy, 'reward',
                                  date, time, txid, fee_sym=self._norm(fee_ccy),
                                  fiat=self._fiat, account=self.DEFAULT_ACCOUNT,
                                  fee_usd=fee_usd)
                    or Outcome.nonevent(_DUST_LABEL))
        # The commission reduces the income, and the fee COINS left the
        # account: a sale of them at that value (A2-0582) — they used to
        # stay in the book.
        income = usd_value - fee_usd
        if asset_name in _FIAT_CURRENCIES:
            # A fiat reward's income row is in the reward's own currency:
            # the USD net is converted back at the rate the export's
            # amountusd implies (10 CAD at 7.50 USD, a 1 USD fee: 8.67
            # CAD), never booked as that many units of it (backlog
            # TJ-0378).
            if not usd_value:
                raise ValueError(
                    f"Kraken {ctx}: a {asset_name} reward of "
                    f"{gross_qty:g} with amountusd 0 and its {fee_qty:g} "
                    f"fee charged in {fee_ccy} (feecurrency) — the fee "
                    f"cannot be valued in {asset_name}; refusing to guess "
                    f"the net income. Enter this reward via a .tt "
                    f"file{_TT_REMOVE}.")
            income = M.units(gross_qty * income / usd_value, 8)
        return ledger_reward_legs(
            asset_name, date, time, gross_qty, txid,
            usd_value=usd_value, income_usd=income,
            fee_note=f"{fee_qty:.10g} {fee_ccy}",
            stablecoins_as_cash=self.stablecoins_as_cash,
            account=self.DEFAULT_ACCOUNT) + fee_coin_sale(
                row, ctx, fee_qty, fee_ccy, 'reward', date, time, txid,
                fee_sym=self._norm(fee_ccy), fiat=self._fiat,
                account=self.DEFAULT_ACCOUNT, fee_usd=fee_usd)

    def _dust_reward(self, ctx, date, asset, qty, usd_value):
        """A reward under the books' zero: a note when its amountusd is
        missing or at most _DUST_USD (nil income at that size), else
        refused — the income would vanish (pre-release review H1)."""
        if usd_value is not None and usd_value > _DUST_USD:
            raise ValueError(
                f"Kraken {ctx} ({date}): a {asset} reward of {_units(qty)} "
                f"is under the books' zero ({QTY_ZERO:g} units) but its "
                f"amountusd is {_usd_text(usd_value)} — it cannot be booked "
                f"as 0 units, and leaving it out would drop that income (a "
                f"reward that small is left out only when worth at most "
                f"{M.shown(_DUST_USD, 2):.2f} USD). This account's files are not booked "
                f"until it is fixed: correct the row in the export, or "
                f"enter the reward via a .tt file{_TT_REMOVE}.")
        emit_line(f"note: Kraken {ctx}: a {asset} reward of {_units(qty)} "
                  f"({_usd_text(usd_value)}) is under the books' zero "
                  f"({QTY_ZERO:g} units) — not booked.")

    def _build_instant_trades(self, sides, refid=''):
        """Instant trades / dust sweeps for one refid. One spend leg and
        one receive leg is the normal shape. A DUST SWEEP spends several
        assets for one receipt (e.g. CAD, ADA, AVAX and BNB dust swept
        into a dollar or two of USD): the received value is split across the
        spend legs by each leg's `amountusd` (equally when the export
        has none) and every disposed asset is booked. The old model
        kept one spend leg and dropped the rest, leaving the swept
        coins in the book forever. Several legs on BOTH sides has no
        defensible split and raises.

        A coin leg under the books' zero (schema.QTY_ZERO: Kraken writes
        a swept coin's amount to ten decimals, so a leg can be a few
        ten-billionths of a coin) cannot be a BUYSELL (0 units has no
        direction; the schema refuses it). It is left out only when its
        VALUE is negligible too (_check_dust: its own amountusd and the
        share of the other side it would take, each at most _DUST_USD);
        the receipt is then split over the other legs and spent coins
        stay in the holdings as a residue (tax-logic CA-CRYPTO-11 /
        US-CRYPTO-07). One note per refid names the legs left out.
        Otherwise the refid is refused (pre-release review H1)."""
        self._dust_ok: List[Any] = []       # [(leg, received)]
        self._dust_split = False
        self._dust_multi = (len(sides['spend']) > 1
                            or len(sides['receive']) > 1)
        out = self._instant_trades(sides, refid)
        if self._dust_ok:
            self._note_dust(refid)
        return out

    def _leg_coins(self, leg, received):
        """The units a leg moved: Kraken ledger fees are in the row's
        asset and the balance moves by amount − fee — a receive credits
        amount − fee, a spend debits |amount| + fee. A fiat leg is its
        amount (its fee stays money)."""
        if leg['asset'] in self._fiat:
            return leg['amount']
        return (leg['amount'] - leg['fee'] if received
                else leg['amount'] + leg['fee'])

    def _is_dust(self, leg, received):
        """A coin leg under the books' zero (a fiat leg is cash)."""
        return (leg['asset'] not in self._fiat
                and abs(self._leg_coins(leg, received)) < QTY_ZERO)

    def _counter_share(self, leg, legs, others):
        """The USD value of the other side that `leg` (one of `legs`, its
        own side) would take were it booked, or None when unknown: a
        lone leg takes the whole other side; one of several takes the
        other side's single leg split as _instant_trades splits it (by
        amountusd, else equally)."""
        if len(legs) == 1:
            vals = [self._leg_usd(o) for o in others]
            return None if any(v is None for v in vals) else sum(vals)
        one_v = self._leg_usd(others[0])
        if one_v is None:
            return None
        weights = [self._leg_usd(lg) for lg in legs]
        if all(w is not None for w in weights) and sum(weights) > 0:
            return one_v * self._leg_usd(leg) / sum(weights)
        return one_v / len(legs)

    def _check_dust(self, spends, recvs, refid):
        """Every coin leg under the books' zero: left out (recorded in
        _dust_ok) when negligible — its own amountusd (when the export
        has one) and the share of the other side it would take are each
        at most _DUST_USD — else the refid is refused with a readable
        message. A 1:1 trade's dust leg is thus left out only when BOTH
        sides are negligible. A tiny quantity carrying real value is a
        disposal, a purchase or a receipt with a basis from nothing:
        never dropped (pre-release review H1)."""
        for side, legs, others in (('spend', spends, recvs),
                                   ('receive', recvs, spends)):
            received = side == 'receive'
            for leg in legs:
                if not self._is_dust(leg, received):
                    continue
                own = leg['usd']
                share = self._counter_share(leg, legs, others)
                if ((own is None or own <= _DUST_USD)
                        and share is not None and share <= _DUST_USD):
                    self._dust_ok.append((leg, received))
                    continue
                coin = leg.get('raw') or leg['asset']
                what = ('acquisition' if received else 'disposition')
                other = ", ".join(f"{o.get('raw') or o['asset']} "
                                  f"{_units(self._leg_coins(o, not received))}"
                                  for o in others)
                share_txt = ("no USD value to show it is negligible"
                             if share is None else
                             f"{M.shown(share, 2):.2f} USD")
                raise ValueError(
                    f"Kraken ledger {leg.get('src', '')}: refid "
                    f"{_mask(refid)} ({leg['date']}): the {side} leg of "
                    f"{coin} {_units(self._leg_coins(leg, received))} is "
                    f"under the books' zero ({QTY_ZERO:g} units) but not "
                    f"negligible — its amountusd: {_usd_text(own)}; its "
                    f"share of the other side ({other}): {share_txt}. A "
                    f"trade of 0 units cannot be booked, and leaving the "
                    f"leg out would lose a real {what} (a leg that small "
                    f"is left out only when it and its share of the other "
                    f"side are each worth at most {M.shown(_DUST_USD, 2):.2f} USD). "
                    f"This account's files are not booked until it is "
                    f"fixed: correct the rows in the export, or enter this "
                    f"trade via a .tt file{_TT_REMOVE}.")

    def _drop_dust(self, pairs, refid=''):
        """[(tx, leg)] -> the txs at or above the books' zero. A tx under
        it is left out only when its leg (or the leg a split part came
        from) passed _check_dust; any other is refused, never dropped."""
        out = []
        for tx, leg in pairs:
            if abs(tx['quantity']) < QTY_ZERO:
                origin = leg.get('_origin', leg)
                if any(d is origin for d, _r in
                       getattr(self, '_dust_ok', ())):
                    continue
                raise ValueError(
                    f"Kraken ledger {leg.get('src', '')}: refid "
                    f"{_mask(refid)} "
                    f"({tx['date']}): its {tx['symbol']} side comes to "
                    f"{_units(abs(tx['quantity']))} units, under the books' "
                    f"zero ({QTY_ZERO:g} units) — a trade of 0 units cannot "
                    f"be booked and leaving it out could lose a real trade. "
                    f"This account's files are not booked until it is "
                    f"fixed: enter this trade via a .tt file{_TT_REMOVE}.")
            out.append(tx)
        return out

    def _note_dust(self, refid):
        """One note per refid naming each leg left out: the coins moved
        (a receive leg net of its fee) and the USD value. "Dust sweep"
        only for a real multi-leg sweep; a 1:1 trade is an instant trade,
        and a received leg is an acquisition, not a disposition."""
        def _name(leg, received):
            coins = _units(self._leg_coins(leg, received))
            rcv = " received" if received else ""
            return (f"{leg.get('raw') or leg['asset']} {coins}{rcv} "
                    f"({_usd_text(leg.get('usd'))})")
        dust = self._dust_ok
        names = "; ".join(_name(lg, r) for lg, r in dust)
        n = len(dust)
        sides = {r for _lg, r in dust}
        if sides == {False}:
            what = ("a disposition of a negligible amount; the coins stay "
                    "in the holdings as a residue")
        elif sides == {True}:
            what = ("an acquisition of a negligible amount; the coins are "
                    "not added to the holdings")
        else:
            what = ("a trade of negligible amounts; the coins spent stay "
                    "in the holdings as a residue, the coins received are "
                    "not added")
        kind = "dust sweep" if self._dust_multi else "instant trade"
        split = (" The receipt is split over the other legs."
                 if self._dust_split else "")
        emit_line(f"note: Kraken ledger refid {_mask(refid)} "
                  f"({dust[0][0]['date']}): {kind}: {names} — "
                  f"{'a leg' if n == 1 else f'{n} legs'} under the books' "
                  f"zero ({QTY_ZERO:g} units) and worth at most "
                  f"{M.shown(_DUST_USD, 2):.2f} USD, not booked: {what}.{split}")
        self.recount(Outcome.nonevent(_DUST_LABEL),
                                  self._own_rows(*(_lg for _lg, _r in dust)))

    def _own_rows(self, *legs) -> int:
        """The rows of this export the legs fold: a leg joined across files
        counts only the rows this export holds (a receipt two exports split
        is one leg whose first row is here: counting both moved a row this
        export never read, a TypeError, backlog TJ-4463)."""
        shown = self._kr.shown
        return sum(leg['rows'].get(shown, 0) for leg in legs)

    def _instant_trades(self, sides, refid=''):
        spends = list(sides['spend'].values())
        recvs = list(sides['receive'].values())
        if not spends or not recvs:
            for leg_type, legs in (('spend', spends), ('receive', recvs)):
                for leg in legs:
                    missing = 'receive' if leg_type == 'spend' else 'spend'
                    emit_line(f"warning: UNBOOKED: Kraken ledger refid "
                          f"{_mask(refid)}: orphan "
                          f"{leg_type} row ({leg['asset']} "
                          f"{leg['amount']:g} on {leg['date']}) has no "
                          f"matching {missing} leg — row SKIPPED. A lone "
                          f"spend is a real taxable disposition (and a lone "
                          f"receive an acquisition with basis); this event "
                          f"is NOT in the output — find the missing "
                          f"counter-leg (truncated export?) or enter the "
                          f"trade manually via a .tt file.")
                    self.recount(Outcome.skip(f"orphan {leg_type} (refid {_mask(refid)})"), self._own_rows(leg))
            return []
        if len(spends) > 1 and len(recvs) > 1:
            raise ValueError(
                f"Kraken ledger refid {_mask(refid)}: {len(spends)} spend and "
                f"{len(recvs)} receive legs — no defensible way to pair "
                f"them. Enter this conversion via a .tt file{_TT_REMOVE}.")
        self._check_dust(spends, recvs, refid)
        if len(spends) == 1 and len(recvs) == 1:
            return self._build_instant_trade(
                {'spend': spends[0], 'receive': recvs[0]}, refid)
        many, one, many_side = ((spends, recvs[0], 'spend')
                                if len(spends) > 1
                                else (recvs, spends[0], 'receive'))
        # A negligible leg under the books' zero (_check_dust) takes no
        # share: the receipt goes to the other legs (by amountusd), its
        # own share (at most _DUST_USD) with it. All legs dust: the
        # counter-leg still pairs with one of them, so a coin receipt is
        # still acquired; that dust leg's own zero-unit row is left out
        # by _drop_dust.
        received = many_side == 'receive'
        kept = [leg for leg in many if not self._is_dust(leg, received)]
        self._dust_split = bool(kept) and len(kept) < len(many)
        if not kept:
            kept = [many[0]]
        if len(kept) == 1:
            return self._split_legs(kept, [1.0], one, many_side, refid)
        weights = [self._leg_usd(leg) for leg in kept]
        missing = [leg['asset'] for leg, w in zip(kept, weights)
                   if w is None]
        if not missing and sum(weights) > 0:
            total = sum(weights)
            shares = [w / total for w in weights]
            basis = 'amountusd'
        else:
            shares = [1.0 / len(kept)] * len(kept)
            basis = ('equal shares (no amountusd in this export)'
                     if len(missing) == len(kept)
                     else f"equal shares (no amountusd on "
                          f"{', '.join(missing)})" if missing
                     else 'equal shares (amountusd is 0 on every leg)')
        emit_line(f"note: Kraken ledger refid {_mask(refid)}: {len(kept)} "
              f"{many_side} legs ({', '.join(l['asset'] for l in kept)}) "
              f"share one {one['asset']} {'receipt' if many_side == 'spend' else 'payment'} "
              f"— split by {basis}; each asset is booked separately.")
        return self._split_legs(kept, shares, one, many_side, refid)

    @staticmethod
    def _leg_usd(leg):
        """A leg's USD value: the export's amountusd, else a USD leg's
        own amount."""
        if leg['usd'] is not None:
            return leg['usd']
        if leg['asset'] in _FIAT_CURRENCIES:
            # A fiat leg is its own value: USD as is, another currency
            # through the run's rates (None without one).
            return _usd_value(leg['amount'], leg['asset'], leg['date'])
        return None

    def _split_legs(self, many, shares, one, many_side, refid):
        out = []
        for i, (leg, share) in enumerate(zip(many, shares)):
            part = dict(one, amount=one['amount'] * share,
                        fee=one['fee'] * share,
                        _origin=one.get('_origin', one),
                        # the one leg's rows count once, with its first part
                        rows=dict(one['rows']) if i == 0 else {},
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
                emit_line(f"warning: UNBOOKED: Kraken ledger refid "
                      f"{_mask(refid)}: orphan "
                      f"{leg_type} row ({leg['asset']} "
                      f"{leg['amount']:g} on {leg['date']}) has no "
                      f"matching {missing} leg — row SKIPPED. A lone "
                      f"spend is a real taxable disposition (and a lone "
                      f"receive an acquisition with basis); this event "
                      f"is NOT in the output — find the missing "
                      f"counter-leg (truncated export?) or enter the "
                      f"trade manually via a .tt file.")
                self.recount(Outcome.skip(f"orphan {leg_type} (refid {_mask(refid)})"), self._own_rows(leg))
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
        _coins = self._leg_coins

        if spend['asset'] == recv['asset'] and \
                spend['asset'] not in self._fiat:
            # DOT -> DOT28.S, or ETH -> ETH2.S under a ticker.map
            # `GLOBAL ETH2 ETH` (both fold to one property): a relabel,
            # not a disposition (S061-11).
            if spend['fee'] or recv['fee']:
                raise ValueError(
                    f"Kraken ledger refid {_mask(refid)}: a swap between "
                    f"two spellings of {spend['asset']} with a fee in the "
                    f"coin — not modeled; enter it via a .tt file{_TT_REMOVE}.")
            self.recount(Outcome.nonevent(
                self._same_property_swap(spend['amount'], recv['amount'], spend['asset'],
                                         f"ledger refid {_mask(refid)}")), self._own_rows(spend, recv))
            return []
        same_coin_hint(spend['asset'], recv['asset'], spend['amount'],
                       recv['amount'], f"Kraken ledger refid {_mask(refid)}")

        if (spend['asset'] in _FIAT_CURRENCIES
                and recv['asset'] in _FIAT_CURRENCIES):
            # Fiat-for-fiat (USDC dust swept to USD, USD -> CAD): a
            # currency conversion, not a disposition of property. The
            # fiat-spend branch below would have booked a BUYSELL of a
            # phantom `CAD`/`USD` asset. Counted, not modeled
            # (KNOWN_ISSUES "Kraken fiat conversions are not modeled").
            _rs = spend.get('raw') or spend['asset']
            _rr = recv.get('raw') or recv['asset']
            # A stablecoin swapped for USD away from the peg drops a
            # de-peg gain or loss under the cash model: said, as the
            # trades path and Coinbase say it (CA-CRYPTO-02; re-audit
            # A2-1284). A leg with amountusd already warned on its row.
            # A stablecoin swapped for CAD, EUR, ...: through the day's
            # rate (re-audit A2-0590).
            for _coin, _cl, _usd in ((_rs, spend, recv), (_rr, recv, spend)):
                _fiat = _usd.get('raw', _usd['asset'])
                if (self.stablecoins_as_cash and _coin in _CASH_STABLECOINS
                        and _cl.get('usd') is None
                        and _fiat in _FIAT_CURRENCIES
                        and _fiat not in _CASH_STABLECOINS
                        and _cl['amount']):
                    warn_depeg(_coin, _usd['amount'] / _cl['amount'],
                               _cl['amount'], _cl['date'],
                               f"Kraken ledger refid {_mask(refid)}",
                               currency=_fiat)
            self.recount(Outcome.nonevent(
                f"forex conversion {_rs}->{_rr} "
                f"(instant trade / dust sweep, not modeled — "
                f"docs/limits.md)"), self._own_rows(spend, recv))
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
                    leg['price'] = M.units(usd / q, 8) if q else 0.0
                    leg['net_amount'] = M.units(usd, 8)
                    leg['gross_amount'] = M.units(usd, 8)
            elif recv['fee'] and abs(buy_leg['quantity']) > 0:
                # Unpriced: taxjson-fill-crypto values the swap from the
                # received coin's units, which are net of the receive
                # fee. The gross amountusd values the units before it,
                # so the fee rides along for the filler to add
                # (`swap_fee`, backlog TJ-0991). A spend-side fee is
                # not in amountusd's received value either: nothing.
                for leg in (sell_leg, buy_leg):
                    leg['swap_fee'] = f"{abs(recv['fee'])!r} received"
            if refid:
                sell_leg['id'] = f'{refid}-sell'
                buy_leg['id'] = f'{refid}-buy'
            return self._drop_dust([(sell_leg, spend), (buy_leg, recv)],
                                   refid)

        price = (M.units(quote_amt / base_amt, 8)
                 if base_amt > 0 else 0.0)
        qty = self.signed_quantity(base_amt, action_is_sell=not is_buy)
        # Engine convention (engine/ca/walk.py: "net_amount ... includes
        # the fee"): Kraken debits the ledger fee IN ADDITION to the
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
        return self._drop_dust([(tx, recv if is_buy else spend)],
                               refid)

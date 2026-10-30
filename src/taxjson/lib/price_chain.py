"""Current-price resolution chain: IBKR -> yfinance -> on-disk cache.

A small price-source chain, pared down to what taxjson's pricing
tools need (stock snapshots only):

  1. IBKR   — a running TWS / IB Gateway (ib_insync, the [ibkr] extra).
              Frozen market data (real-time while open, prior close when
              shut); qualification retries the space form for class
              shares ('SAMPMY.B' -> 'SAMPMY B'). Missing library / no gateway /
              unqualified symbols degrade silently to the next tier.
  2. yfinance — the [fx] extra, using each symbol's Yahoo
              spelling (caller-provided; see ticker.map QUOTE lines).
  3. cache  — work/.price_cache.json. Every tier-1/2 hit is written back
              with the spelling it was fetched for (an entry for another
              spelling is a miss); a symbol both tiers miss is served
              from the cache with its age in the source label ('cache:3d'), warning when older
              than max_cache_age_days. Offline runs therefore keep
              working with the last known prices.

Every quote carries its source so reports can say where a number came
from instead of implying freshness.
"""

import contextlib
import math
from taxjson.lib.stage_msg import emit_line
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

DEFAULT_MAX_CACHE_AGE_DAYS = 7
DEFAULT_IBKR_HOST = "127.0.0.1"
# Gateway live port; TWS live is 7496 — callers pass what they run.
DEFAULT_IBKR_PORT = 4001
IBKR_CONNECT_TIMEOUT = 5.0
IBKR_SNAPSHOT_WAIT = 8.0

# A quoted (Yahoo) spelling's suffix -> its trading currency: the market
# data's one venue table (lib/markets), every venue Yahoo spells as the
# books do (or bare: .US). Questrade's .VN is Yahoo's .V (Yahoo's own
# .VN is another market), so it is not read here.
from taxjson.lib.markets import (known_suffixes as _known_sfx,  # noqa: E402
                                 suffix_currency as _sfx_cur,
                                 yahoo_suffix as _yh_sfx)
_SUFFIX_CURRENCY = {s: _sfx_cur(s) for s in _known_sfx()
                    if _yh_sfx(s) in (s, "")}


# Yahoo's minor-unit currencies: the quote is in pence / cents / agorot
# (LSE 'GBp', JSE 'ZAc', TASE 'ILA'); the fetcher divides by 100 and
# reports the major currency (audit S077-04 — VOD.L valued at 100x).
_MINOR_UNITS = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ZAC": "ZAR",
                "ILA": "ILS"}
# Currency codes a Yahoo pair spelling can end in ('ETH-CAD'): the one
# fiat list (lib/markets).
from taxjson.lib.markets import fiat_currencies as _fiat  # noqa: E402
_PAIR_CURRENCIES = _fiat()
from taxjson.lib.markets import canadian_suffixes as _ca_sfx  # noqa: E402
_CA_SUFFIXES = _ca_sfx()

# Listings whose quote may come in MINOR units (LSE pence, JSE cents,
# TASE agorot): a quote that does not state its unit cannot be valued —
# 70 could be 70 pence or 70 pounds (audit A2-0379 / A2-0692).
_MINOR_UNIT_SUFFIXES = frozenset({"L", "IL", "JO", "TA"})


def minor_unit_listing(quote_symbol: str) -> bool:
    """True for a listing quoted in pence/cents on its own market (an
    LSE '.L' line): only a source that names the unit can price it."""
    sym = (quote_symbol or "").strip()
    if "." not in sym:
        return False
    return sym.rsplit(".", 1)[1].upper() in _MINOR_UNIT_SUFFIXES


def provider_unit(currency) -> Tuple[float, Optional[str], Optional[str]]:
    """(divisor, major currency, problem) for the unit a price source
    names — THE one minor-unit table, shared by the live quote, the
    day's-close history and the caches (issue #75: the history adapter
    dropped Yahoo's 'GBp' and a 125p close was booked as 125 pounds).

      - None / '' -> (1, None, None): the source did not say;
      - a minor unit ('GBp', 'GBX', 'ZAc', 'ILA') -> (100, major, None);
      - an ISO code in any one case ('usd', 'CAD') -> (1, 'USD', None);
      - anything else -> (1, None, problem): a mixed-case code taxjson
        has no table entry for ('USd') is a minor unit of unknown size,
        a non-string or non-alphabetic value is not a code — never
        guessed (audit A2-1170)."""
    if currency is None:
        return 1.0, None, None
    if not isinstance(currency, str):
        return 1.0, None, f"currency {currency!r} is not a code"
    cur = currency.strip()
    if not cur:
        return 1.0, None, None
    if cur in _MINOR_UNITS:
        return 100.0, _MINOR_UNITS[cur], None
    if len(cur) != 3 or not cur.isalpha():
        return 1.0, None, f"currency {currency!r} is not a code"
    if cur not in (cur.upper(), cur.lower()):
        return 1.0, None, (f"currency {currency!r} looks like a minor unit "
                           f"(pence/cents) taxjson does not know")
    return 1.0, cur.upper(), None


def clean_quote_currency(price: float, currency
                         ) -> Tuple[float, Optional[str], Optional[str]]:
    """(price, currency, problem) for a currency a tier or the cache
    reported: stripped, minor units ('GBp') converted to the major
    currency, upper-cased (provider_unit). A unit that is not a known
    code is dropped (currency None) and `problem` says why — validated
    like the price beside it (audit A2-1170: a list crashed harvest,
    'usd' dropped the holding)."""
    div, cur, problem = provider_unit(currency)
    return price / div, cur, problem


@dataclass
class PriceQuote:
    price: float
    source: str          # 'ibkr' | 'yfinance' | 'cache:<age>d'
    asof: str            # ISO date the price was fetched
    # The currency the SOURCE reported the price in (major units), when
    # it says; None = infer from the quoted symbol (quote_currency).
    currency: Optional[str] = None
    # The spelling the source actually priced: the asked (QUOTE-mapped)
    # one, or the symbol's own listing for an IBKR hit, which never
    # sees a QUOTE remap. A QUOTE line's ratio applies only to a quote
    # of its target (backlog TJ-1038). None: not recorded.
    listing: Optional[str] = None


def is_crypto_symbol(symbol: str) -> bool:
    """A book symbol with no market suffix is a crypto asset (the schema
    convention: equities always carry .US/.TO/...; see
    brokerages/schema.KNOWN_SUFFIXES)."""
    import re
    return bool(symbol) and bool(re.fullmatch(r"[A-Z0-9]{1,15}", symbol))


def yf_symbol_for(symbol: str,
                  crypto_overrides: Optional[Dict[str, str]] = None
                  ) -> Optional[str]:
    """Best-effort Yahoo spelling for a taxjson symbol, used when
    ticker.map carries no QUOTE line for it. THE single home for this
    translation (harvest consumes it — the
    audit found three drifting copies). Returns None for symbols Yahoo
    can't serve (exchange-prefixed 'X:SYM' forms).

    `crypto_overrides`: the coin spellings the books were priced with
    (the project's ticker.map CRYPTO lines — `load_crypto_overrides`;
    there is no built-in table); none when not given, so every coin is
    `<SYMBOL>-USD` (audit A2-0364: FOO mapped to FOO123 in the books
    was quoted as FOO-USD, another asset)."""
    import re
    if is_crypto_symbol(symbol):
        # A coin, not a stock: Yahoo's crypto pair, with the same
        # project CRYPTO ids the crypto price filler uses (audit R1-228
        # — ETH/LINK/SOL went out as equity tickers).
        return f"{(crypto_overrides or {}).get(symbol, symbol)}-USD"
    yf_ticker = symbol
    if symbol.endswith('.US'):
        yf_ticker = symbol[:-3]
        # US class shares: Yahoo spells SAMPMY.B as SAMPMY-B (audit S077-09 —
        # only one class share was handled).
        yf_ticker = re.sub(r'\.([A-Z])$', r'-\1', yf_ticker)
    elif symbol.endswith('.TO'):
        yf_ticker = symbol[:-3]
        yf_ticker = re.sub(r'\.PR\.', '-P', yf_ticker, flags=re.IGNORECASE)
        # Trust units and USD-traded units: SAMPMQ.UN -> SAMPMQ-UN, SAMPLF.U ->
        # SAMPLF-U (audit S077-09 / R1-150 — the old '\.UN\.' pattern ran
        # after the .TO strip and could never match).
        yf_ticker = re.sub(r'\.UN$', '-UN', yf_ticker, flags=re.IGNORECASE)
        yf_ticker = re.sub(r'\.U$', '-U', yf_ticker, flags=re.IGNORECASE)
        # Preferred-share styles (.PR.A / .PR-A / .PR_A / bare .PR)
        # -> Yahoo's -PA / -P forms (e.g. SAMPMH.PR.A.TO -> SAMPMH-PA.TO).
        yf_ticker = re.sub(r'\.PR[.\-_]([A-Z])$', r'-P\1', yf_ticker,
                           flags=re.IGNORECASE)
        yf_ticker = re.sub(r'\.PR$', '-P', yf_ticker, flags=re.IGNORECASE)
        # Any other class or series: the trailing `.X` / `.XX` is
        # Yahoo's `-X` (SAMPLC.B -> SAMPLC-B, a .C or .DB likewise). The
        # old blind '.B'/'.A' replace missed every other class letter
        # and rewrote a '.B' / '.A' anywhere in the symbol.
        yf_ticker = re.sub(r'\.([A-Z]{1,2})$', r'-\1', yf_ticker,
                           flags=re.IGNORECASE)
        yf_ticker = yf_ticker + '.TO'
    if ':' in yf_ticker:
        return None
    return yf_ticker


def load_crypto_overrides(search_dirs) -> Dict[str, str]:
    """fill-crypto's coin spellings: the CRYPTO lines of the first
    ticker.map found in `search_dirs` (the project root's map is the one
    `taxjson run` priced the books with). No built-in ids."""
    from taxjson.lib.ticker_map import side_rules_in
    return dict(side_rules_in(search_dirs).crypto)


def load_yf_map(search_dirs) -> Dict[str, Tuple[str, float]]:
    """The QUOTE lines of the first ticker.map found in `search_dirs`,
    as {SYMBOL: (YF_SYMBOL, QTY_RATIO)}. Lines are `QUOTE SYMBOL
    YF_SYMBOL [QTY_RATIO]`; QTY_RATIO (default 1.0) converts a
    position's quantity into the mapped ticker's units — for tickers
    consumed by a merger, map to the acquirer with the exchange ratio,
    e.g.:

        QUOTE OLDCO.TO  NEWCO  0.25    # 4:1 merger — 4 OLDCO -> 1 NEWCO
        QUOTE ABC.TO    SAMPLE    1.5

    A folder still holding the old yf_ticker.map is refused
    (lib/ticker_map.LegacyMapFileError): `taxjson migrate` moves it."""
    from taxjson.lib.ticker_map import side_rules_in
    return dict(side_rules_in(search_dirs).quote)


def _split_suffix(symbol: str) -> Tuple[str, str]:
    parts = symbol.rsplit(".", 1)
    if len(parts) == 2 and parts[1].upper() in _SUFFIX_CURRENCY:
        return parts[0], parts[1].upper()
    return symbol, ""


def quote_currency(quote_symbol: str) -> Optional[str]:
    """Currency a quote for this symbol is denominated in, from its
    spelling; None when the spelling does not say (callers must then
    omit the row loudly, never guess). Callers comparing quotes against
    BASE-currency book costs must convert — mixing a USD price into CAD
    basis was a real reported bug.

      - a known exchange suffix (.TO -> CAD, .L -> GBP, ...), except a
        Canadian-listed USD unit ('SAMPLF.U.TO' / 'SAMPLF-U.TO') -> USD
        (audit R1-150);
      - a Yahoo pair ('ETH-USD', 'ETH-CAD') -> its quote currency;
      - a bare symbol (Yahoo's US spelling) -> USD;
      - any other suffix ('.DE', '.T', '.JO') -> None (audit S077-00:
        it used to fall back to USD)."""
    import re
    sym = (quote_symbol or "").strip()
    base, suffix = _split_suffix(sym)
    if suffix:
        if suffix in _CA_SUFFIXES and re.search(r"[.\-]U$", base, re.I):
            return "USD"
        return _SUFFIX_CURRENCY[suffix]
    m = re.fullmatch(r"[A-Z0-9]+-([A-Z]{3})", sym.upper())
    if m and m.group(1) in _PAIR_CURRENCIES:
        return m.group(1)
    if "." in sym:
        return None
    return "USD"


def load_fx_history(rates_path, base: str) -> Dict[str, Dict]:
    """{currency: {date: rate}} from the pipeline's to_base.csv (empty
    when absent). Callers must treat 'no rate' as 'omit the row loudly'
    — never mix a native quote unconverted into base-currency numbers."""
    p = Path(rates_path) if rates_path else None
    if p is None or not p.exists():
        return {}
    # Deferred bin import (the loader lives with the converter CLI);
    # price_chain is the shared consumer-side home.
    from taxjson.bin.taxjson_convert_currency import load_exchange_rates
    return load_exchange_rates(p, target_curr=base)


def latest_rate(history: Dict[str, Dict], cur: str,
                on: str, max_age_days: Optional[int] = None
                ) -> Tuple[Optional[float], Optional[str]]:
    """(rate, date) of the most recent rate on/before `on` — rates end at
    the last `taxjson run`, so 'today' usually isn't in the file; a
    slightly aged rate labeled as such beats a silent fallback constant.
    (None, None) when the currency has no usable rate, or when
    `max_age_days` is given and the newest usable rate is older than
    that (a dated event must not be priced at a rate from months
    before — audit R1-151; the converter's own lookback is 5 days)."""
    dates = history.get(cur) or {}
    usable = [d for d in dates if d <= on]
    if not usable:
        return None, None
    d = max(usable)
    if max_age_days is not None:
        try:
            age = (datetime.strptime(on[:10], "%Y-%m-%d")
                   - datetime.strptime(d[:10], "%Y-%m-%d")).days
        except ValueError:
            return None, None
        if age > max_age_days:
            return None, None
    return float(dates[d]), d


def _money(value) -> Optional[float]:
    """THE price-value rule, shared by the live tiers and the caches:
    `value` as a float when it is a finite number above 0, else None.
    NaN (IB's 'no price'), an infinity, zero, a negative or a non-number
    is not a price (audit finding 57: `x == x` let +inf through), and
    neither is a bool — JSON `true` is not a price of 1 (backlog
    TJ-0577: float(True) is 1.0, so a tier or a damaged cache entry
    that said `true` was quoted and cached at 1)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _usable_price(value) -> bool:
    """A live quote is a usable price (`_money`): otherwise the next
    candidate or tier is asked."""
    return _money(value) is not None


def _ibkr_cache_spelling(sym: str) -> str:
    """The Yahoo spelling an IBKR hit for `sym` is cached under: the
    listing IB was asked for is the symbol's own (the IB tier never sees
    a QUOTE remap), so its default Yahoo spelling — not the remapped
    one. Unmapped, that is the spelling a later run wants and the entry
    serves offline; remapped, it is not, and the entry is a miss for the
    remapped spelling (audit finding 69, issue #82)."""
    return yf_symbol_for(sym) or sym


def _ibkr_currency(sym: str) -> Optional[str]:
    """The currency of an IBKR hit for `sym`: the contract's, which is
    the symbol's own listing's (`_ibkr_fetcher` asks for
    Stock(root, 'SMART', quote_currency(sym))). It travels with the
    quote, so a QUOTE line that remaps the Yahoo spelling to another
    listing (SYN.US -> SYN.TO) no longer lends the native USD price the
    remapped listing's CAD (backlog TJ-0578). None for a market that
    quotes in pence/cents (.L/.JO/.TA): IB's figure does not say which,
    and a unit-less quote there is refused (A2-0692)."""
    if minor_unit_listing(sym):
        return None
    return quote_currency(sym)


def _ibkr_fetcher(symbols: List[str], *, host: str, port: int,
                  verbose: bool) -> Dict[str, float]:
    """Snapshot via a running TWS/Gateway. Any failure — library absent,
    nothing listening, symbol unqualified — just leaves the symbol for
    the next tier."""
    try:
        from ib_insync import IB, Stock
    except ImportError:
        if verbose:
            from taxjson.lib.install_hint import extra_hint
            print("price-chain: ib_insync not installed — skipping IBKR "
                  f"tier ({extra_hint('ibkr')})", file=sys.stderr)
        return {}
    ib = IB()
    try:
        ib.connect(host, port, clientId=17, timeout=IBKR_CONNECT_TIMEOUT)
    except Exception as exc:
        if verbose:
            print(f"price-chain: no IBKR gateway at {host}:{port} ({exc}) "
                  f"— falling through to yfinance", file=sys.stderr)
        return {}
    out: Dict[str, float] = {}
    try:
        ib.reqMarketDataType(2)              # frozen: live or prior close
        contracts = []
        # A coin is not a stock (audit R1-228), and an unknown listing
        # has no contract currency to guess (audit S077-00).
        symbols = [s for s in symbols if not is_crypto_symbol(s)
                   and quote_currency(s) is not None]
        for sym in symbols:
            root, suffix = _split_suffix(sym)
            currency = quote_currency(sym)
            contracts.append(Stock(root.replace(" ", "."), "SMART",
                                   currency))
        ib.qualifyContracts(*contracts)
        # Class-share retry: IBKR wants 'SAMPMY B' where taxjson has 'SAMPMY.B'
        # (US names; Canadian class shares keep the dot).
        for c in contracts:
            if not c.conId and "." in c.symbol:
                c.symbol = c.symbol.replace(".", " ")
        retry = [c for c in contracts if not c.conId]
        if retry:
            ib.qualifyContracts(*retry)
        tickers = [ib.reqMktData(c, "", False, False)
                   if c.conId else None for c in contracts]
        ib.sleep(IBKR_SNAPSHOT_WAIT)
        for sym, c, t in zip(symbols, contracts, tickers):
            if t is None:
                continue
            price = None
            for candidate in (t.last, t.close, t.marketPrice()):
                if _usable_price(candidate):
                    price = float(candidate)
                    break
            if price is not None:
                out[sym] = price
            ib.cancelMktData(c)
    except Exception as exc:
        if verbose:
            print(f"price-chain: IBKR snapshot failed ({exc})",
                  file=sys.stderr)
    finally:
        ib.disconnect()
    return out


def _ibkr_option_fetcher(symbols: List[str], *, host: str, port: int,
                         verbose: bool) -> Dict[str, float]:
    """Snapshot OCC option contracts via a running TWS/Gateway — the
    only live tier that can price options (yfinance option chains are
    stale/wide for illiquid strikes; better no mark than a bad one).
    Same failure contract as the stock fetcher: any problem leaves the
    symbol for the cache tier."""
    from taxjson.lib.core import (parse_option_expiry, parse_option_right,
                                  parse_option_strike,
                                  parse_option_underlying)
    try:
        from ib_insync import IB, Option
    except ImportError:
        if verbose:
            from taxjson.lib.install_hint import extra_hint
            print("price-chain: ib_insync not installed — skipping IBKR "
                  f"option tier ({extra_hint('ibkr')})",
                  file=sys.stderr)
        return {}
    ib = IB()
    try:
        ib.connect(host, port, clientId=18, timeout=IBKR_CONNECT_TIMEOUT)
    except Exception as exc:
        if verbose:
            print(f"price-chain: no IBKR gateway at {host}:{port} ({exc}) "
                  f"— option quotes unavailable", file=sys.stderr)
        return {}
    out: Dict[str, float] = {}
    try:
        ib.reqMarketDataType(2)              # frozen: live or prior close
        for sym in symbols:
            underlying = parse_option_underlying(sym) or ""
            root, suffix = _split_suffix(underlying)
            expiry = parse_option_expiry(sym)
            strike = parse_option_strike(sym)
            right = parse_option_right(sym)
            if not (root and expiry and strike and right):
                continue
            currency = quote_currency(underlying)
            if currency is None:
                continue                  # unknown listing: no guess
            c = None
            # Class-share roots: try as written, then the space form
            # (US names want 'SAMPLD B'; Canadian keep the dot).
            for spelled in dict.fromkeys((root, root.replace(".", " "))):
                cand = Option(spelled, expiry.replace("-", ""), strike,
                              right, "SMART", currency=currency)
                try:
                    ib.qualifyContracts(cand)
                except Exception:                           # noqa: BLE001
                    continue
                if cand.conId:
                    c = cand
                    break
            if c is None:
                if verbose:
                    print(f"price-chain: IBKR did not recognize {sym}",
                          file=sys.stderr)
                continue
            t = ib.reqMktData(c, "", False, False)
            ib.sleep(IBKR_SNAPSHOT_WAIT)
            price = None
            for candidate in (t.last, t.close, t.marketPrice()):
                if _usable_price(candidate):
                    price = float(candidate)
                    break
            if price is not None:
                out[sym] = price
            ib.cancelMktData(c)
    except Exception as exc:
        if verbose:
            print(f"price-chain: IBKR option snapshot failed ({exc})",
                  file=sys.stderr)
    finally:
        ib.disconnect()
    return out


def fetch_option_prices(symbols: List[str], *,
                        cache_path: Path,
                        max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
                        use_ibkr: bool = True,
                        ibkr_host: str = DEFAULT_IBKR_HOST,
                        ibkr_port: int = DEFAULT_IBKR_PORT,
                        verbose: bool = False,
                        fetchers: Optional[List[Callable]] = None,
                        ) -> Dict[str, PriceQuote]:
    """Current premiums for OCC option symbols: IBKR -> cache. NO
    yfinance tier — an unpriceable contract is omitted by callers, never
    marked from a bad source. Shares the stock cache file (keys are the
    full OCC symbols) and the fetchers test hook. TAXJSON_OFFLINE holds
    here too: no gateway request, and a cache miss refuses like a stock
    miss (audit R1-343 — the injected tier list disabled the switch)."""
    from taxjson.lib.offline import offline_enabled
    offline = offline_enabled() and fetchers is None
    if offline:
        fetchers = []
    if fetchers is None:
        fetchers = []
        if use_ibkr:
            fetchers.append(lambda rem: {
                s: (p, "ibkr") for s, p in _ibkr_option_fetcher(
                    list(rem), host=ibkr_host, port=ibkr_port,
                    verbose=verbose).items()})
    return fetch_prices({s: s for s in symbols}, cache_path=cache_path,
                        max_cache_age_days=max_cache_age_days,
                        verbose=verbose, fetchers=fetchers,
                        offline=offline)


def _ibkr_history_fetcher(pairs: Dict[str, str], *, start: str, end: str,
                          host: str, port: int,
                          verbose: bool) -> Dict[str, Dict[str, float]]:
    """Daily close history via TWS/Gateway: {sym: {date: close}}.
    IBKR daily bars are split-adjusted — same convention as Yahoo, so
    the two tiers can back one series."""
    try:
        from ib_insync import IB, Stock
    except ImportError:
        if verbose:
            print("price-chain: ib_insync not installed — skipping IBKR "
                  "history tier", file=sys.stderr)
        return {}
    from datetime import date as _date
    try:
        span = (_date.fromisoformat(end) - _date.fromisoformat(start)).days
    except ValueError:
        return {}
    duration = (f"{span + 5} D" if span <= 360
                else f"{span // 365 + 1} Y")
    ib = IB()
    try:
        ib.connect(host, port, clientId=19, timeout=IBKR_CONNECT_TIMEOUT)
    except Exception as exc:
        if verbose:
            print(f"price-chain: no IBKR gateway at {host}:{port} ({exc})",
                  file=sys.stderr)
        return {}
    out: Dict[str, Dict[str, float]] = {}
    try:
        for sym in pairs:
            if is_crypto_symbol(sym) or quote_currency(sym) is None:
                continue                  # not a stock / unknown listing
            root, suffix = _split_suffix(sym)
            c = Stock(root.replace(" ", "."), "SMART", quote_currency(sym))
            try:
                ib.qualifyContracts(c)
                if not c.conId and "." in c.symbol:
                    c.symbol = c.symbol.replace(".", " ")
                    ib.qualifyContracts(c)
                if not c.conId:
                    continue
                bars = ib.reqHistoricalData(
                    c, endDateTime="", durationStr=duration,
                    barSizeSetting="1 day", whatToShow="TRADES",
                    useRTH=True, formatDate=1)
            except Exception:                               # noqa: BLE001
                continue
            closes = {}
            for b in bars or []:
                d = str(b.date)[:10]
                if start <= d <= end and b.close and b.close > 0:
                    closes[d] = float(b.close)
            if closes:
                out[sym] = closes
    finally:
        ib.disconnect()
    return out


@contextlib.contextmanager
def _quiet_yfinance():
    """Silence yfinance while it is asked: it logs every miss as a raw,
    unlabelled line of its own (an HTTP 404 body, '$ZZB.TO: possibly
    delisted', 'Cookie/crumb fetch failed') and older releases print
    some to stdout / stderr. The callers say what matters ('no price
    from any tier for: ...', the in-kind move not booked) — backlog
    TJ-0118, TJ-0161, TJ-0182; `tips --online` quiets the same
    logger."""
    import io
    import logging
    lg = logging.getLogger("yfinance")
    level = lg.level
    lg.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        lg.setLevel(level)


def _yf_history_fetcher(pairs: Dict[str, str], *, start: str, end: str,
                        verbose: bool
                        ) -> Dict[str, Tuple[Dict[str, float], Optional[str]]]:
    """Daily close history via yfinance: {sym: ({date: close},
    currency)}, the closes in the MAJOR unit of the currency Yahoo names
    (None when it names none; a unit it names that taxjson cannot read
    drops the symbol). Yahoo closes are split-adjusted (current share
    terms)."""
    try:
        import yfinance as yf
    except ImportError:
        if verbose:
            print("price-chain: yfinance not installed — skipping "
                  "history tier", file=sys.stderr)
        return {}
    from datetime import date as _date, timedelta as _td
    out: Dict[str, Dict[str, float]] = {}
    # yfinance's `end` is exclusive — push one day past the window.
    try:
        end_x = (_date.fromisoformat(end) + _td(days=1)).isoformat()
    except ValueError:
        return {}
    for sym, ysym in pairs.items():
        try:
            with _quiet_yfinance():
                tk = yf.Ticker(ysym)
                hist = tk.history(start=start, end=end_x, auto_adjust=False)
                yf_cur = _yf_currency(tk)
        except Exception:                                   # noqa: BLE001
            continue
        # The unit Yahoo names, read after the request that fills the
        # metadata: an LSE close is in pence (issue #75 — dropping it
        # booked 125p as 125 pounds).
        div, cur, problem = provider_unit(yf_cur)
        if problem:
            emit_line(f"warning: price-chain: Yahoo history for {sym} "
                      f"({ysym}): {problem} — not used", file=sys.stderr)
            continue
        closes = {}
        try:
            for ts, close in hist["Close"].items():
                if close == close and close > 0:            # NaN guard
                    closes[str(ts)[:10]] = float(close) / div
        except (KeyError, TypeError):
            continue
        if closes:
            out[sym] = (closes, cur)
    return out



def _yf_currency(tk) -> Optional[str]:
    """The currency Yahoo reports for a ticker (history metadata, no
    extra request), or None."""
    try:
        meta = getattr(tk, "history_metadata", None) or {}
        cur = meta.get("currency")
        return str(cur) if cur else None
    except Exception:                                   # noqa: BLE001
        return None


def _yfinance_fetcher(pairs: Dict[str, str], *,
                      verbose: bool) -> Dict[str, Tuple[float, Optional[str]]]:
    """pairs: taxjson symbol -> Yahoo spelling. Returns {sym: (price,
    currency)} with the price in the currency's major units."""
    try:
        import yfinance as yf
    except ImportError:
        if verbose:
            print("price-chain: yfinance not installed — skipping tier",
                  file=sys.stderr)
        return {}
    out: Dict[str, Tuple[float, Optional[str]]] = {}
    for sym, yf_sym in pairs.items():
        try:
            with _quiet_yfinance():
                tk = yf.Ticker(yf_sym)
                hist = tk.history(period="1d", timeout=5)
                yf_cur = _yf_currency(tk)
            if not hist.empty:
                px = float(hist["Close"].iloc[-1])
                # NaN guard (x == x is False for NaN), matching every
                # sibling tier — Yahoo returns NaN closes for halted/
                # newly-delisted symbols, and an unguarded NaN
                # fossilizes into the price cache and surfaces as a
                # bare NaN token in --json output downstream.
                if px == px and px > 0:
                    px, cur, problem = clean_quote_currency(px, yf_cur)
                    if problem:
                        emit_line(f"warning: price-chain: Yahoo quote for "
                                  f"{sym} ({yf_sym}): {problem} — not used",
                                  file=sys.stderr)
                        continue
                    out[sym] = (px, cur)
        except Exception as exc:
            if verbose:
                print(f"price-chain: yfinance miss {sym} ({yf_sym}): {exc}",
                      file=sys.stderr)
    return out


def _load_cache(path: Path) -> Dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError covers JSONDecodeError and a non-UTF-8 byte
        # (UnicodeDecodeError): a corrupt cache degrades to a refetch,
        # never a traceback (audit S055-04).
        return {}
    return data if isinstance(data, dict) else {}


def _cached_price(rec) -> Optional[float]:
    """A cache entry's price, or None when it is not a usable price
    (`_money`) — a missing, null, zero, negative, NaN, text or boolean
    price in a hand-edited or damaged cache is a cache MISS, never a 0.0
    quote (audit R1-156 / R1-244: harvest showed a -100% LOSS) nor a
    1.0 one (TJ-0577)."""
    try:
        return _money(rec.get("price"))
    except AttributeError:
        return None


# How far ahead of today a cached quote's date may be: one day, for a
# cache written on a machine whose date was already tomorrow (a project
# folder shared across time zones). A date further ahead is not a quote
# anyone fetched — a damaged or hand-edited entry — and is a miss
# (backlog TJ-0576: an entry dated 2099 was served as current).
CACHE_CLOCK_SKEW_DAYS = 1


def _iso_day_in(day: str, start: str, end: str) -> bool:
    """`day` is a calendar date (YYYY-MM-DD) with start <= day <= end."""
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return False
    return date.fromisoformat(start) <= d <= date.fromisoformat(end)


def _save_cache(path: Path, fresh: Dict[str, dict]) -> None:
    """Write this run's FRESH entries into the cache file: under the
    cache's lock (safe_write.file_lock, via json_cache), the entries on
    disk are read, `fresh` is merged over them and the result renamed
    into place. Only the fresh entries are passed, never the dict the
    run loaded earlier: two runs that each loaded the cache, fetched a
    symbol of their own and wrote their whole dict back each replaced
    the other's new price with the stale copy they had loaded (audit
    2026-10-24 A33). A fresh entry still wins over the one on disk (the
    stale entry it replaces does not come back). allow_nan=False: a
    NaN/Inf that slipped past a tier guard must fail the cache write
    loudly, not fossilize forever. A unique temp file (re-audit
    A2-0233)."""
    from taxjson.lib.json_cache import save_json_cache
    save_json_cache(path, fresh, merge=True, label="price cache ",
                    indent=1, sort_keys=True, allow_nan=False)


def fetch_prices(pairs: Dict[str, str], *,
                 cache_path: Path,
                 max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
                 use_ibkr: bool = True,
                 ibkr_host: str = DEFAULT_IBKR_HOST,
                 ibkr_port: int = DEFAULT_IBKR_PORT,
                 verbose: bool = False,
                 fetchers: Optional[List[Callable]] = None,
                 offline: Optional[bool] = None,
                 ) -> Dict[str, PriceQuote]:
    """Resolve current prices for `pairs` (taxjson symbol -> Yahoo
    spelling) through IBKR -> yfinance -> cache. Fresh hits are written
    back to the cache with the spelling they were fetched for; an entry
    is served only for that same spelling (issue #82: a remapped QUOTE /
    CRYPTO line served the previous asset's price), with its age.

    `fetchers` overrides the live tiers for testing: a list of callables
    taking the remaining {sym: yahoo_sym} dict and returning
    {sym: (price, source_label)}, {sym: (price, source_label,
    currency)} or {sym: (price, source_label, currency, spelling)} —
    `spelling` is the one the tier actually priced, cached in place of
    the asked one (the IBKR tier prices the symbol's own listing, never
    a QUOTE remap), and whose unit decides a pence/cents market. A price
    that is not a finite number above 0 (or is a bool) is skipped: the
    next tier is asked and nothing is cached. A cache entry is served
    only when its price, its unit and its date (not after today, but for
    CACHE_CLOCK_SKEW_DAYS) are usable. `offline` forces the
    TAXJSON_OFFLINE policy decision (None: the switch applies unless
    test fetchers are injected).
    """
    today = date.today().isoformat()
    quotes: Dict[str, PriceQuote] = {}
    remaining = dict(pairs)

    # TAXJSON_OFFLINE: the same switch that forbids the FX and crypto
    # price downloads (SECURITY.md). The live tiers (IBKR gateway,
    # Yahoo Finance) are skipped; the cache still serves, and a miss
    # fails loudly below, naming what was needed. Injected `fetchers`
    # (tests) are not network tiers and stay as given.
    from taxjson.lib.offline import offline_enabled
    if offline is None:
        offline = offline_enabled() and fetchers is None
    if offline:
        fetchers = []
    if fetchers is None:
        fetchers = []
        if use_ibkr:
            fetchers.append(lambda rem: {
                s: (p, "ibkr", _ibkr_currency(s), _ibkr_cache_spelling(s))
                for s, p in _ibkr_fetcher(
                    list(rem), host=ibkr_host, port=ibkr_port,
                    verbose=verbose).items()})
        fetchers.append(lambda rem: {
            s: (p, "yfinance", cur) for s, (p, cur) in _yfinance_fetcher(
                rem, verbose=verbose).items()})

    unit_less: List[str] = []
    spelled: Dict[str, str] = {}
    for fetcher in fetchers:
        if not remaining:
            break
        try:
            got = fetcher(dict(remaining))
        except Exception as exc:
            if verbose:
                print(f"price-chain: tier failed ({exc})", file=sys.stderr)
            continue
        for sym, hit in got.items():
            if sym not in remaining:
                continue
            price, source = hit[0], hit[1]
            if not _usable_price(price):
                # An infinity, NaN or bool from any tier is not a quote:
                # the next tier is asked, nothing is cached (finding 57,
                # TJ-0577).
                emit_line(f"warning: price-chain: {source} quote for {sym}: "
                          f"{price!r} is not a usable price — ignored",
                          file=sys.stderr)
                continue
            price = _money(price)
            # The listing the tier actually priced: the IBKR tier asks
            # for the symbol's own listing, never a QUOTE remap, so its
            # unit is that listing's (TJ-0578), not the remapped one's.
            listing = (hit[3] if len(hit) > 3 and hit[3]
                       else pairs.get(sym) or sym)
            price, cur, problem = clean_quote_currency(
                price, hit[2] if len(hit) > 2 else None)
            if problem:
                # The source named a unit taxjson cannot read: the price
                # may be in cents, or not a money amount at all. The hit
                # is refused like a non-finite price — the next tier is
                # asked, nothing is cached. It used to be taken with no
                # currency, and the listing's currency was then inferred
                # for it (audit 2026-10-24 A40).
                emit_line(f"warning: price-chain: {source} quote for {sym}: "
                          f"{problem} — not used; the next source is asked",
                          file=sys.stderr)
                continue
            if cur is None and minor_unit_listing(listing):
                # Pence or pounds? A unit-less quote for an LSE line is
                # left for the next tier (Yahoo names the unit) and
                # never cached (audit A2-0692).
                if sym not in unit_less:
                    unit_less.append(sym)
                continue
            quotes[sym] = PriceQuote(price=price, source=source, asof=today,
                                     currency=cur, listing=listing)
            if len(hit) > 3 and hit[3]:
                spelled[sym] = hit[3]
            remaining.pop(sym, None)

    cache = _load_cache(cache_path)
    respelled: List[str] = []
    if remaining:
        stale: List[str] = []
        for sym in list(remaining):
            rec = cache.get(sym)
            if not isinstance(rec, dict):
                continue
            # The entry is the quote of the spelling it was fetched for:
            # after a QUOTE/CRYPTO line remaps the symbol (or for an
            # entry from before the spelling was recorded) it is another
            # asset's price — a miss, never served (issue #82).
            if rec.get("quote_symbol") != remaining[sym]:
                respelled.append(f"{sym} (cached for "
                                 f"{rec.get('quote_symbol') or 'no spelling'}"
                                 f", wanted {remaining[sym]})")
                continue
            price = _cached_price(rec)
            if price is None:
                emit_line(f"warning: price cache entry for {sym} has no "
                          f"usable price ({rec.get('price')!r}) — ignored",
                          file=sys.stderr)
                continue
            asof = str(rec.get("asof") or "")
            try:
                age = (date.today()
                       - datetime.strptime(asof, "%Y-%m-%d").date()).days
            except ValueError:
                continue
            if age < -CACHE_CLOCK_SKEW_DAYS:
                # Dated after today: not a quote anyone fetched (a
                # damaged or hand-edited entry) — a miss, never served
                # as the freshest price there is (TJ-0576).
                emit_line(f"warning: price cache entry for {sym} in "
                          f"{cache_path} is dated {asof}, after today — "
                          f"not used", file=sys.stderr)
                continue
            age = max(age, 0)
            price, cur, problem = clean_quote_currency(
                price, rec.get("currency"))
            if problem:
                # A hand-edited or damaged entry: a miss, as a live hit
                # with such a unit is (A40).
                emit_line(f"warning: price cache entry for {sym}: {problem} "
                          f"— not used", file=sys.stderr)
                continue
            if cur is None and minor_unit_listing(remaining[sym]):
                # Pence or pounds? An entry with no provider-named unit
                # for an LSE/JSE/TASE line is as ambiguous as the live
                # quote the tiers refuse above (and close_on's cache
                # rule): a miss, said below (audit 2026-10-24 A31).
                if sym not in unit_less:
                    unit_less.append(sym)
                continue
            quotes[sym] = PriceQuote(price=price,
                                     source=f"cache:{age}d", asof=asof,
                                     currency=cur, listing=remaining[sym])
            remaining.pop(sym)
            if age > max_cache_age_days:
                stale.append(f"{sym} ({age}d)")
        if respelled:
            emit_line(f"warning: price cache entries not used — fetched for "
                      f"another quote spelling, or before the spelling was "
                      f"recorded: {', '.join(respelled)}. Refresh online.",
                      file=sys.stderr)
        if stale:
            emit_line(f"warning: price cache older than {max_cache_age_days}d "
                      f"for: {', '.join(stale)} — connect IBKR or the network "
                      f"to refresh.", file=sys.stderr)

    for sym in unit_less:
        if sym not in quotes:
            emit_line(f"warning: price-chain: the quote for {sym} did not say "
                      f"its unit — that market quotes in pence/cents as well "
                      f"as pounds, so it was not used (a source that names "
                      f"the unit, e.g. Yahoo, prices it).", file=sys.stderr)

    # Write back every fresh (non-cache) quote: the fresh entries alone,
    # merged into the file under its lock (_save_cache).
    fresh: Dict[str, dict] = {}
    for sym, q in quotes.items():
        if q.source.startswith("cache"):
            continue
        fresh[sym] = {"price": q.price, "asof": q.asof, "source": q.source,
                      "quote_symbol": spelled.get(sym, pairs[sym])}
        if q.currency:
            fresh[sym]["currency"] = q.currency
    if fresh:
        _save_cache(cache_path, fresh)

    if remaining and offline:
        from taxjson.lib import out
        _rem = sorted(remaining)
        out.fail(
            f"TAXJSON_OFFLINE is set and {len(_rem)} symbol(s) have no "
            f"current price in the cache: "
            + ", ".join(_rem[:3]) + (f" +{len(_rem) - 3} more"
                                     if len(_rem) > 3 else ""),
            prog="taxjson",
            details=["Unset TAXJSON_OFFLINE, or run once online to fill "
                     "the cache."])
    if remaining and verbose:
        print(f"price-chain: unpriced after all tiers: "
              f"{', '.join(sorted(remaining))}", file=sys.stderr)
    return quotes


# ------------------------------------------------------------ one day's close

# How far back a close is looked for when the day itself had none (a
# weekend, a holiday): the last close on or before the day, within this
# many days.
CLOSE_LOOKBACK_DAYS = 7


@dataclass
class DayClose:
    """A security's close on (or just before) one day."""
    price: float
    currency: Optional[str]
    day: str            # the day the close is from (<= the asked day)
    source: str         # 'yfinance' | 'cache' | a test fetcher's label


class OfflineCloseMissing(ValueError):
    """TAXJSON_OFFLINE is set and the close cache has no price for the
    asked day: the caller names its own override."""


def close_on(symbol: str, quote_symbol: str, day: str, *,
             cache_path: Path,
             fetchers: Optional[List[Callable]] = None,
             offline: Optional[bool] = None,
             verbose: bool = False) -> Optional[DayClose]:
    """The close of `symbol` (Yahoo spelling `quote_symbol`) on `day`,
    or the last one before it within CLOSE_LOOKBACK_DAYS: from the
    on-disk close cache (work/.close_cache.json, keyed
    SYMBOL|QUOTE_SYMBOL@DAY: two Yahoo spellings of one symbol are two
    closes; an entry of the older SYMBOL@DAY form is a miss), else
    Yahoo's daily history (written back to the cache). None when no
    tier has it. Yahoo's closes are split-adjusted: a split after `day`
    makes the figure wrong — the caller says the value is an estimate.

    `fetchers` replaces the live tier for tests: callables taking
    ({sym: yahoo_sym}, start, end) and returning {sym: {day: close}} or
    {sym: ({day: close}, currency)}. The currency a tier names wins
    (minor units normalised: 125 'GBp' is 1.25 GBP); without one, the
    listing's suffix says — except on a market that quotes in minor
    units (.L/.JO/.TA), where a unit-less close is refused (None) and an
    old cache entry with no provider-named unit is a miss (issue #75).
    TAXJSON_OFFLINE (or offline=True) skips the live tier; a miss then
    raises OfflineCloseMissing."""
    from datetime import date as _date, timedelta as _td
    from taxjson.lib.offline import offline_enabled
    key = f"{symbol}|{quote_symbol}@{day}"
    minor = minor_unit_listing(quote_symbol)
    try:
        start = (_date.fromisoformat(day)
                 - _td(days=CLOSE_LOOKBACK_DAYS)).isoformat()
    except ValueError:
        start = None
    cache = _load_cache(cache_path)
    rec = cache.get(key)
    if isinstance(rec, dict) and start is not None:
        price = _cached_price(rec)
        # A market that quotes in pence/cents: only an entry whose unit
        # the provider named is trusted — an older entry's currency was
        # read off the '.L' suffix, so 125p was cached as 125 GBP
        # (issue #75); it is a miss and is fetched again.
        if minor and rec.get("unit") != "provider":
            price = None
        # The day the entry says its close is from must be one the live
        # tier could have returned for this key: on or before the asked
        # day, within the lookback. A hand-edited or damaged entry that
        # names a later day (a December close under a January key) is a
        # miss, never a value from the future (audit 2026-10-24 A32).
        rec_day = str(rec.get("day") or day)[:10]
        if price is not None and not _iso_day_in(rec_day, start, day):
            emit_line(f"warning: price-chain: the close cache entry for "
                      f"{symbol} ({quote_symbol}) on {day} is dated "
                      f"{rec_day!s}, outside {start}..{day} — not used",
                      file=sys.stderr)
            price = None
        if price is not None:
            price, cur, problem = clean_quote_currency(price,
                                                       rec.get("currency"))
            if problem:
                # The entry names a unit taxjson cannot read ('USd': a
                # minor unit of unknown size): a miss, fetched again, as
                # the live tier below refuses the same unit — never the
                # listing's currency in its place (backlog TJ-0575).
                emit_line(f"warning: price-chain: the close cache entry for "
                          f"{symbol} ({quote_symbol}) on {day} in "
                          f"{cache_path}: {problem} — not used",
                          file=sys.stderr)
            else:
                cur = cur or (None if minor else quote_currency(quote_symbol))
                if cur is not None:
                    return DayClose(price=price, currency=cur,
                                    day=rec_day, source="cache")
    if offline is None:
        offline = offline_enabled() and fetchers is None
    if offline:
        raise OfflineCloseMissing(
            f"TAXJSON_OFFLINE is set and the close of {symbol} on {day} "
            f"is not in {cache_path.name}")
    if start is None:
        return None
    if fetchers is None:
        fetchers = [lambda pairs, s, e: _yf_history_fetcher(
            pairs, start=s, end=e, verbose=verbose)]
    for fetcher in fetchers:
        try:
            got = fetcher({symbol: quote_symbol}, start, day)
        except Exception as exc:                        # noqa: BLE001
            if verbose:
                print(f"price-chain: close tier failed ({exc})",
                      file=sys.stderr)
            continue
        hit = got.get(symbol) if isinstance(got, dict) else None
        cur = None
        if isinstance(hit, tuple):
            hit, cur = hit[0], (hit[1] if len(hit) > 1 else None)
        if not isinstance(hit, dict):
            continue
        days = sorted(d for d, px in hit.items()
                      if start <= str(d)[:10] <= day
                      and isinstance(px, (int, float))
                      and _money(px) is not None)
        if not days:
            continue
        used = days[-1]
        # The provider's unit, normalised (pence -> pounds) by the one
        # table the live quote uses; one it names but taxjson cannot
        # read is refused, never replaced by the listing's currency.
        price, cur, problem = clean_quote_currency(_money(hit[used]), cur)
        if problem:
            emit_line(f"warning: price-chain: the close of {symbol} "
                      f"({quote_symbol}) on {used}: {problem} — not used",
                      file=sys.stderr)
            continue
        unit = "provider" if cur else "listing"
        if cur is None and minor:
            # Pence or pounds: the listing's suffix cannot say (issue
            # #75, audit A2-0379); not guessed, not cached.
            emit_line(f"warning: price-chain: the close of {symbol} "
                      f"({quote_symbol}) on {used} did not say its unit — "
                      f"that market quotes in pence/cents as well as "
                      f"pounds, so it was not used", file=sys.stderr)
            continue
        cur = cur or quote_currency(quote_symbol)
        entry = {"price": price, "day": str(used)[:10],
                 "asof": _date.today().isoformat(),
                 "source": "yfinance", "unit": unit}
        if cur:
            entry["currency"] = cur
        _save_cache(cache_path, {key: entry})
        return DayClose(price=price, currency=cur, day=str(used)[:10],
                        source="yfinance")
    return None

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
  3. cache  — work/.price_cache.json. Every tier-1/2 hit is written back;
              a symbol both tiers miss is served from the cache with its
              age in the source label ('cache:3d'), warning when older
              than max_cache_age_days. Offline runs therefore keep
              working with the last known prices.

Every quote carries its source so reports can say where a number came
from instead of implying freshness.
"""

import math
import json
import os
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


def clean_quote_currency(price: float, currency
                         ) -> Tuple[float, Optional[str], Optional[str]]:
    """(price, currency, problem) for a currency a tier or the cache
    reported: stripped, minor units ('GBp') converted to the major
    currency, upper-cased. A non-string or non-ISO value is dropped
    (currency None) and `problem` says why — validated like the price
    beside it (audit A2-1170: a list crashed harvest, 'usd' dropped the
    holding)."""
    if currency is None:
        return price, None, None
    if not isinstance(currency, str):
        return price, None, f"currency {currency!r} is not a code"
    cur = currency.strip()
    if not cur:
        return price, None, None
    if cur in _MINOR_UNITS:
        return price / 100.0, _MINOR_UNITS[cur], None
    cur = cur.upper()
    if len(cur) != 3 or not cur.isalpha():
        return price, None, f"currency {currency!r} is not a code"
    return price, cur, None


@dataclass
class PriceQuote:
    price: float
    source: str          # 'ibkr' | 'yfinance' | 'cache:<age>d'
    asof: str            # ISO date the price was fetched
    # The currency the SOURCE reported the price in (major units), when
    # it says; None = infer from the quoted symbol (quote_currency).
    currency: Optional[str] = None


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
                if candidate and candidate == candidate and candidate > 0:
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
                if candidate and candidate == candidate and candidate > 0:
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


def _yf_history_fetcher(pairs: Dict[str, str], *, start: str, end: str,
                        verbose: bool) -> Dict[str, Dict[str, float]]:
    """Daily close history via yfinance: {sym: {date: close}}. Yahoo
    closes are split-adjusted (current share terms)."""
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
            hist = yf.Ticker(ysym).history(start=start, end=end_x,
                                           auto_adjust=False)
        except Exception:                                   # noqa: BLE001
            continue
        closes = {}
        try:
            for ts, close in hist["Close"].items():
                if close == close and close > 0:            # NaN guard
                    closes[str(ts)[:10]] = float(close)
        except (KeyError, TypeError):
            continue
        if closes:
            out[sym] = closes
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


def _normalize_units(price: float,
                     currency: Optional[str]) -> Tuple[float, Optional[str]]:
    """(price, currency) in MAJOR units: Yahoo quotes LSE lines in
    pence ('GBp'), JSE in cents — divide by 100 (audit S077-04)."""
    if currency in _MINOR_UNITS:
        return price / 100.0, _MINOR_UNITS[currency]
    return price, currency


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
            tk = yf.Ticker(yf_sym)
            hist = tk.history(period="1d", timeout=5)
            if not hist.empty:
                px = float(hist["Close"].iloc[-1])
                # NaN guard (x == x is False for NaN), matching every
                # sibling tier — Yahoo returns NaN closes for halted/
                # newly-delisted symbols, and an unguarded NaN
                # fossilizes into the price cache and surfaces as a
                # bare NaN token in --json output downstream.
                if px == px and px > 0:
                    out[sym] = _normalize_units(px, _yf_currency(tk))
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
    """A cache entry's price, or None when it is not a finite number
    above 0 — a missing, null, zero, negative, NaN or text price in a
    hand-edited or damaged cache is a cache MISS, never a 0.0 quote
    (audit R1-156 / R1-244: harvest showed a -100% LOSS)."""
    try:
        p = float(rec.get("price"))
    except (TypeError, ValueError, AttributeError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def _save_cache(path: Path, cache: Dict[str, dict]) -> None:
    # allow_nan=False: a NaN/Inf that slipped past a tier guard must
    # fail the cache write loudly, not fossilize forever. A unique temp
    # file under a lock (re-audit A2-0233); not merged — a stale entry
    # this run replaced must not come back.
    from taxjson.lib.json_cache import save_json_cache
    save_json_cache(path, cache, label="price cache ", indent=1,
                    sort_keys=True, allow_nan=False)


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
    back to the cache; cache-served quotes carry their age.

    `fetchers` overrides the live tiers for testing: a list of callables
    taking the remaining {sym: yahoo_sym} dict and returning
    {sym: (price, source_label)} or {sym: (price, source_label,
    currency)}. `offline` forces the TAXJSON_OFFLINE policy decision
    (None: the switch applies unless test fetchers are injected).
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
                s: (p, "ibkr") for s, p in _ibkr_fetcher(
                    list(rem), host=ibkr_host, port=ibkr_port,
                    verbose=verbose).items()})
        fetchers.append(lambda rem: {
            s: (p, "yfinance", cur) for s, (p, cur) in _yfinance_fetcher(
                rem, verbose=verbose).items()})

    unit_less: List[str] = []
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
            price, cur, problem = clean_quote_currency(
                price, hit[2] if len(hit) > 2 else None)
            if problem:
                print(f"warning: price-chain: {source} quote for {sym}: "
                      f"{problem} — ignored", file=sys.stderr)
            if cur is None and minor_unit_listing(pairs.get(sym) or sym):
                # Pence or pounds? A unit-less quote for an LSE line is
                # left for the next tier (Yahoo names the unit) and
                # never cached (audit A2-0692).
                if sym not in unit_less:
                    unit_less.append(sym)
                continue
            quotes[sym] = PriceQuote(price=price, source=source, asof=today,
                                     currency=cur)
            remaining.pop(sym, None)

    cache = _load_cache(cache_path)
    if remaining:
        stale: List[str] = []
        for sym in list(remaining):
            rec = cache.get(sym)
            if not isinstance(rec, dict):
                continue
            price = _cached_price(rec)
            if price is None:
                print(f"warning: price cache entry for {sym} has no "
                      f"usable price ({rec.get('price')!r}) — ignored",
                      file=sys.stderr)
                continue
            asof = str(rec.get("asof") or "")
            try:
                age = (date.today()
                       - datetime.strptime(asof, "%Y-%m-%d").date()).days
            except ValueError:
                continue
            price, cur, problem = clean_quote_currency(
                price, rec.get("currency"))
            if problem:
                print(f"warning: price cache entry for {sym}: {problem} "
                      f"— its currency is ignored", file=sys.stderr)
            quotes[sym] = PriceQuote(price=price,
                                     source=f"cache:{age}d", asof=asof,
                                     currency=cur)
            remaining.pop(sym)
            if age > max_cache_age_days:
                stale.append(f"{sym} ({age}d)")
        if stale:
            print(f"warning: price cache older than {max_cache_age_days}d "
                  f"for: {', '.join(stale)} — connect IBKR or the network "
                  f"to refresh.", file=sys.stderr)

    for sym in unit_less:
        if sym not in quotes:
            print(f"warning: price-chain: the quote for {sym} did not say "
                  f"its unit — that market quotes in pence/cents as well "
                  f"as pounds, so it was not used (a source that names "
                  f"the unit, e.g. Yahoo, prices it).", file=sys.stderr)

    # Write back every fresh (non-cache) quote.
    dirty = False
    for sym, q in quotes.items():
        if q.source.startswith("cache"):
            continue
        cache[sym] = {"price": q.price, "asof": q.asof, "source": q.source}
        if q.currency:
            cache[sym]["currency"] = q.currency
        dirty = True
    if dirty:
        _save_cache(cache_path, cache)

    if remaining and offline:
        from taxjson.lib import out
        out.fail(
            f"TAXJSON_OFFLINE is set but current prices for "
            f"{', '.join(sorted(remaining))} are not in the price cache",
            prog="taxjson",
            details=[f"They would need IBKR / Yahoo Finance; the cache is "
                     f"{cache_path}.",
                     "Unset TAXJSON_OFFLINE to allow the lookup, or run "
                     "once online to fill the cache."])
    if remaining and verbose:
        print(f"price-chain: unpriced after all tiers: "
              f"{', '.join(sorted(remaining))}", file=sys.stderr)
    return quotes

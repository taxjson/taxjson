"""Current-price resolution chain: IBKR -> yfinance -> on-disk cache.

Modeled on portoml-ai's prices.py, pared down to what taxjson's pricing
tools need (stock snapshots only):

  1. IBKR   — a running TWS / IB Gateway (ib_insync, the [ibkr] extra).
              Frozen market data (real-time while open, prior close when
              shut); qualification retries the space form for class
              shares ('BF.B' -> 'BF B'). Missing library / no gateway /
              unqualified symbols degrade silently to the next tier.
  2. yfinance — the [fx] extra, using each symbol's Yahoo
              spelling (caller-provided; see yf_ticker.map).
  3. cache  — work/.price_cache.json. Every tier-1/2 hit is written back;
              a symbol both tiers miss is served from the cache with its
              age in the source label ('cache:3d'), warning when older
              than max_cache_age_days. Offline runs therefore keep
              working with the last known prices.

Every quote carries its source so reports can say where a number came
from instead of implying freshness.
"""

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

_SUFFIX_CURRENCY = {"US": "USD", "TO": "CAD", "V": "CAD", "CN": "CAD",
                    "NE": "CAD", "L": "GBP", "AX": "AUD"}


@dataclass
class PriceQuote:
    price: float
    source: str          # 'ibkr' | 'yfinance' | 'cache:<age>d'
    asof: str            # ISO date the price was fetched


def yf_symbol_for(symbol: str) -> Optional[str]:
    """Best-effort Yahoo spelling for a taxjson symbol, used when
    yf_ticker.map carries no override. THE single home for this
    translation (harvest consumes it — the
    audit found three drifting copies). Returns None for symbols Yahoo
    can't serve (exchange-prefixed 'X:SYM' forms)."""
    import re
    yf_ticker = symbol
    if symbol.endswith('.US'):
        yf_ticker = symbol.replace('.US', '')
    elif symbol.endswith('.TO'):
        yf_ticker = symbol.replace('.TO', '')
        yf_ticker = re.sub(r'\.PR\.', '-P', yf_ticker, flags=re.IGNORECASE)
        yf_ticker = re.sub(r'\.UN\.', '-UN', yf_ticker, flags=re.IGNORECASE)
        yf_ticker = yf_ticker.replace('.B', '-B')
        yf_ticker = yf_ticker.replace('.A', '-A')
        # Preferred-share styles (.PR.A / .PR-A / .PR_A / bare .PR)
        # -> Yahoo's -PA / -P forms (e.g. FFN.PR.A.TO -> FFN-PA.TO).
        for letter in 'ABCDEF':
            yf_ticker = re.sub(rf'\.PR[\.\-_]{letter}', f'-P{letter}',
                               yf_ticker, flags=re.IGNORECASE)
        yf_ticker = re.sub(r'\.PR', '-P', yf_ticker, flags=re.IGNORECASE)
        yf_ticker = yf_ticker + '.TO'
    yf_ticker = yf_ticker.replace('BRK.B', 'BRK-B')
    if ':' in yf_ticker:
        return None
    return yf_ticker


def load_yf_map(search_dirs) -> Dict[str, Tuple[str, float]]:
    """First yf_ticker.map found in `search_dirs`, parsed to
    {SYMBOL: (YF_SYMBOL, QTY_RATIO)}. Lines are `SYMBOL YF_SYMBOL
    [QTY_RATIO]`; QTY_RATIO (default 1.0) converts a position's quantity
    into the mapped ticker's units — for tickers consumed by a merger,
    map to the acquirer with the exchange ratio, e.g.:

        OLDCO.TO  NEWCO  0.25    # 4:1 merger — 4 OLDCO shares -> 1 NEWCO
        ABC.TO    XYZ    1.5
    """
    mapping: Dict[str, Tuple[str, float]] = {}
    for d in search_dirs:
        map_file = Path(d) / "yf_ticker.map"
        if not map_file.exists():
            continue
        try:
            for line in map_file.read_text(encoding='utf-8').splitlines():
                line = line.split('#', 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    ratio = 1.0
                    if len(parts) >= 3:
                        try:
                            ratio = float(parts[2])
                        except ValueError:
                            print(f"warning: bad ratio in {map_file}: "
                                  f"{line!r}", file=sys.stderr)
                    mapping[parts[0]] = (parts[1], ratio)
        except OSError as e:
            print(f"warning: could not read {map_file}: {e}",
                  file=sys.stderr)
        break                              # first map found wins
    return mapping


def _split_suffix(symbol: str) -> Tuple[str, str]:
    parts = symbol.rsplit(".", 1)
    if len(parts) == 2 and parts[1].upper() in _SUFFIX_CURRENCY:
        return parts[0], parts[1].upper()
    return symbol, ""


def quote_currency(quote_symbol: str) -> str:
    """Currency a quote for this symbol is denominated in, from the
    exchange suffix; a bare symbol (Yahoo's US spelling) is USD. Callers
    comparing quotes against BASE-currency book costs must convert —
    mixing a USD price into CAD basis was a real reported bug."""
    _, suffix = _split_suffix(quote_symbol)
    return _SUFFIX_CURRENCY.get(suffix, "USD")


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
                on: str) -> Tuple[Optional[float], Optional[str]]:
    """(rate, date) of the most recent rate on/before `on` — rates end at
    the last `taxjson run`, so 'today' usually isn't in the file; a
    slightly aged rate labeled as such beats a silent fallback constant.
    (None, None) when the currency has no usable rate."""
    dates = history.get(cur) or {}
    usable = [d for d in dates if d <= on]
    if not usable:
        return None, None
    d = max(usable)
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
            print("price-chain: ib_insync not installed — skipping IBKR "
                  "tier (pip install 'taxjson[ibkr]')", file=sys.stderr)
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
        for sym in symbols:
            root, suffix = _split_suffix(sym)
            currency = _SUFFIX_CURRENCY.get(suffix, "USD")
            contracts.append(Stock(root.replace(" ", "."), "SMART",
                                   currency))
        ib.qualifyContracts(*contracts)
        # Class-share retry: IBKR wants 'BF B' where taxjson has 'BF.B'
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
            print("price-chain: ib_insync not installed — skipping IBKR "
                  "option tier (pip install 'taxjson[ibkr]')",
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
            currency = _SUFFIX_CURRENCY.get(suffix, "USD")
            c = None
            # Class-share roots: try as written, then the space form
            # (US names want 'RCI B'; Canadian keep the dot).
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
    full OCC symbols) and the fetchers test hook."""
    if fetchers is None:
        fetchers = []
        if use_ibkr:
            fetchers.append(lambda rem: {
                s: (p, "ibkr") for s, p in _ibkr_option_fetcher(
                    list(rem), host=ibkr_host, port=ibkr_port,
                    verbose=verbose).items()})
    return fetch_prices({s: s for s in symbols}, cache_path=cache_path,
                        max_cache_age_days=max_cache_age_days,
                        verbose=verbose, fetchers=fetchers)


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
            root, suffix = _split_suffix(sym)
            c = Stock(root.replace(" ", "."), "SMART",
                      _SUFFIX_CURRENCY.get(suffix, "USD"))
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



def _yfinance_fetcher(pairs: Dict[str, str], *,
                      verbose: bool) -> Dict[str, float]:
    """pairs: taxjson symbol -> Yahoo spelling."""
    try:
        import yfinance as yf
    except ImportError:
        if verbose:
            print("price-chain: yfinance not installed — skipping tier",
                  file=sys.stderr)
        return {}
    out: Dict[str, float] = {}
    for sym, yf_sym in pairs.items():
        try:
            hist = yf.Ticker(yf_sym).history(period="1d", timeout=5)
            if not hist.empty:
                px = float(hist["Close"].iloc[-1])
                # NaN guard (x == x is False for NaN), matching every
                # sibling tier — Yahoo returns NaN closes for halted/
                # newly-delisted symbols, and an unguarded NaN
                # fossilizes into the price cache and surfaces as a
                # bare NaN token in --json output downstream.
                if px == px and px > 0:
                    out[sym] = px
        except Exception as exc:
            if verbose:
                print(f"price-chain: yfinance miss {sym} ({yf_sym}): {exc}",
                      file=sys.stderr)
    return out


def _load_cache(path: Path) -> Dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, cache: Dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        # allow_nan=False: a NaN/Inf that slipped past a tier guard
        # must fail the cache write loudly, not fossilize forever.
        tmp.write_text(json.dumps(cache, indent=1, sort_keys=True,
                                  allow_nan=False),
                       encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError) as exc:
        print(f"warning: could not write price cache {path}: {exc}",
              file=sys.stderr)


def fetch_prices(pairs: Dict[str, str], *,
                 cache_path: Path,
                 max_cache_age_days: int = DEFAULT_MAX_CACHE_AGE_DAYS,
                 use_ibkr: bool = True,
                 ibkr_host: str = DEFAULT_IBKR_HOST,
                 ibkr_port: int = DEFAULT_IBKR_PORT,
                 verbose: bool = False,
                 fetchers: Optional[List[Callable]] = None,
                 ) -> Dict[str, PriceQuote]:
    """Resolve current prices for `pairs` (taxjson symbol -> Yahoo
    spelling) through IBKR -> yfinance -> cache. Fresh hits are written
    back to the cache; cache-served quotes carry their age.

    `fetchers` overrides the live tiers for testing: a list of callables
    taking the remaining {sym: yahoo_sym} dict and returning
    {sym: (price, source_label)}.
    """
    today = date.today().isoformat()
    quotes: Dict[str, PriceQuote] = {}
    remaining = dict(pairs)

    # TAXJSON_OFFLINE: the same switch that forbids the FX and crypto
    # price downloads (SECURITY.md). The live tiers (IBKR gateway,
    # Yahoo Finance) are skipped; the cache still serves, and a miss
    # fails loudly below, naming what was needed. Injected `fetchers`
    # (tests) are not network tiers and stay as given.
    offline = bool(os.environ.get("TAXJSON_OFFLINE")) and fetchers is None
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
            s: (p, "yfinance") for s, p in _yfinance_fetcher(
                rem, verbose=verbose).items()})

    for fetcher in fetchers:
        if not remaining:
            break
        try:
            got = fetcher(dict(remaining))
        except Exception as exc:
            if verbose:
                print(f"price-chain: tier failed ({exc})", file=sys.stderr)
            continue
        for sym, (price, source) in got.items():
            quotes[sym] = PriceQuote(price=price, source=source, asof=today)
            remaining.pop(sym, None)

    cache = _load_cache(cache_path)
    if remaining:
        stale: List[str] = []
        for sym in list(remaining):
            rec = cache.get(sym)
            if not rec:
                continue
            asof = str(rec.get("asof") or "")
            try:
                age = (date.today()
                       - datetime.strptime(asof, "%Y-%m-%d").date()).days
            except ValueError:
                continue
            quotes[sym] = PriceQuote(price=float(rec.get("price") or 0.0),
                                     source=f"cache:{age}d", asof=asof)
            remaining.pop(sym)
            if age > max_cache_age_days:
                stale.append(f"{sym} ({age}d)")
        if stale:
            print(f"warning: price cache older than {max_cache_age_days}d "
                  f"for: {', '.join(stale)} — connect IBKR or the network "
                  f"to refresh.", file=sys.stderr)

    # Write back every fresh (non-cache) quote.
    dirty = False
    for sym, q in quotes.items():
        if q.source.startswith("cache"):
            continue
        cache[sym] = {"price": q.price, "asof": q.asof, "source": q.source}
        dirty = True
    if dirty:
        _save_cache(cache_path, cache)

    if remaining and offline:
        raise SystemExit(
            f"taxjson: TAXJSON_OFFLINE is set but current prices for "
            f"{', '.join(sorted(remaining))} are not in the price cache "
            f"({cache_path}) and would need IBKR / Yahoo Finance. Unset "
            f"it to allow the lookup, or run once online to fill the "
            f"cache.")
    if remaining and verbose:
        print(f"price-chain: unpriced after all tiers: "
              f"{', '.join(sorted(remaining))}", file=sys.stderr)
    return quotes

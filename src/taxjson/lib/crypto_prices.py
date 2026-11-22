"""A coin's daily close (get_crypto_price: Yahoo Finance's chart under the
project's crypto ids) and the shared price cache in $HOME
(~/.crypto_price_cache.json: load_cache, save_cache, cached_price).
bin/fill_crypto_prices.py prices a book's crypto rows with them; lib/crypto_sends
values a send. A test isolates the cache by patching CACHE_FILE here: the
loaders read it from this module.
"""
from taxjson.lib.out import exit_text
import re
import urllib.request
import urllib.parse
import json
import calendar
import math
import time
import os

from taxjson.lib import cli_diag
from taxjson.lib.offline import offline_enabled


PROG = "taxjson-fill-crypto"

CACHE_FILE = os.path.expanduser("~/.crypto_price_cache.json")

def load_cache():
    # OSError too: an unreadable cache (permissions, dangling symlink)
    # degrades to a refetch, not a traceback. A cache that is valid
    # JSON but not an object ([], 5) degrades the same way, as
    # lib/rate_cache's loader does (re-audit A2-1403 / A2-1446).
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):   # JSON or UTF-8 damage (S055-04)
            pass
    return {}


def _cache_number(v):
    """A cache value as a float when it is a real finite number (an int,
    a float or a numeric string; never a bool, null, list or text),
    else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def cached_price(cache, key):
    """The usable cached price for `key` (a finite number > 0), else
    None. A damaged entry — null, "abc", true, Infinity, a list — is a
    cache MISS, never a traceback and never a price of 1.0 or inf
    (re-audit A2-0772 / A2-0773 / A2-1403 / A2-0464)."""
    f = _cache_number(cache.get(key)) if isinstance(cache, dict) else None
    return f if f is not None and f > 0 else None


def save_cache(cache_data):
    # A unique temp file renamed into place under a lock, merged with
    # what another project's concurrent run saved meanwhile (re-audit
    # A2-0233; a Ctrl-C mid-dump still never truncates the cache).
    from taxjson.lib.json_cache import save_json_cache
    save_json_cache(CACHE_FILE, cache_data, merge=True, prog=PROG, indent=2)

# The project's coin ids for THIS run: {SYMBOL: YAHOO_ID} from the
# ticker.map `CRYPTO SYMBOL YAHOO_ID` lines, filled by bin/fill_crypto_prices's main (and by
# crypto-sends' price lookup) and emptied again afterwards. There is no
# built-in table: a coin is quoted as Yahoo `<SYMBOL>-USD` unless the
# project maps it. Yahoo gives a ticker shared by two assets a number
# (`<SYMBOL><number>-USD`); such a coin needs a CRYPTO line — the
# messages below say which line (_crypto_line_hint).
PROJECT_CRYPTO_IDS: dict = {}


def yahoo_id(symbol: str, ids=None) -> str:
    """The Yahoo id (without `-USD`) a coin is priced under: the
    project's CRYPTO line, else the symbol itself."""
    table = PROJECT_CRYPTO_IDS if ids is None else ids
    return table.get(symbol, symbol)


def _crypto_line_hint(symbol: str) -> str:
    """How to map a coin whose default Yahoo id does not resolve, or
    resolves to another asset."""
    return (f"if Yahoo lists {symbol} under another id (a ticker shared "
            f"with another asset carries a number: `{symbol}<number>-USD`"
            f"), find the id on finance.yahoo.com and add the line "
            f"`CRYPTO {symbol} {symbol}<number>` to the project's "
            f"ticker.map")


def load_symbol_overrides(dirs):
    """The CRYPTO lines (`CRYPTO SYMBOL YAHOO_ID`) of the ticker.map
    found in each of `dirs` (later dirs win), as {SYMBOL: YAHOO_ID} —
    the only coin ids there are (no built-in table). A malformed line
    is skipped with a warning — a typo shouldn't kill a price-fill run
    (`tjs run` refuses the map up front). A folder still holding the
    old crypto_ticker.map stops the run (`tjs migrate` moves it into
    ticker.map)."""
    from taxjson.lib.ticker_map import side_rules_in
    merged = {}
    for d in dirs:
        merged.update(side_rules_in([str(d)]).crypto)
    return merged

def chart_close(data, quote: str, date_str: str) -> float:
    """The USD close of `quote` ("SYN-USD") for the UTC day `date_str`
    in a Yahoo chart response, or ValueError saying why there is none.
    The response must name that quote and USD (when it names them) and
    carry a candle stamped on that UTC day whose close is a real
    positive number: the first positive close of the response was taken
    whatever its day, symbol or currency, and `true` read as 1 — an
    adjacent day's close, cached for good as the asked day's price
    (backlog TJ-0744). HTTP 200 with a null/empty/non-positive close (a
    data gap or an unfinished candle) is a failed lookup like any other
    (audit S000-00)."""
    res = data['chart']['result'][0]
    meta = res.get('meta') or {}
    got = str(meta.get('symbol') or '').strip()
    if got and got.upper() != quote.upper():
        raise ValueError(f"Yahoo answered for {got}, not {quote}")
    ccy = str(meta.get('currency') or '').strip()
    if ccy and ccy.upper() != 'USD':
        raise ValueError(f"Yahoo quoted {quote} in {ccy}, not USD")
    closes = res['indicators']['quote'][0]['close'] or []
    stamps = res.get('timestamp')

    def usable(c):
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            return None
        v = float(c)
        return v if math.isfinite(v) and v > 0 else None
    days = []
    if stamps is None:
        # No timestamps at all: only the one candle the one-day window
        # asked for can be that day's.
        given = [c for c in closes if c is not None]
        if len(given) == 1 and usable(given[0]) is not None:
            return usable(given[0])
    elif len(stamps) != len(closes):
        raise ValueError(f"Yahoo returned {len(closes)} close(s) for "
                         f"{len(stamps)} timestamp(s) of {quote}")
    for ts, c in zip(stamps or [], closes):
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        days.append(day)
        if day == date_str and usable(c) is not None:
            return usable(c)
    raise ValueError(f"Yahoo returned no usable close for {quote} on "
                     f"{date_str} (days {', '.join(days) or 'none'}; "
                     f"close={closes!r})")


def get_crypto_price(symbol, date_str):
    y_symbol = yahoo_id(symbol)
    try:
        # UTC midnight: Yahoo daily candles are UTC-keyed; local
        # mktime made the 1-day window straddle two candles east of
        # UTC, booking the adjacent day's close as FMV.
        dt = int(calendar.timegm(time.strptime(date_str, "%Y-%m-%d")))
        # The symbol is one quoted path segment: a ticker.map CRYPTO id
        # holding '/', '?' or '#' must not reshape the request.
        _seg = urllib.parse.quote(f"{y_symbol}-USD", safe="")
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{_seg}?period1={dt}&period2={dt+86400}&interval=1d"
        if offline_enabled():
            raise SystemExit(
                exit_text(f"taxjson-fill-crypto: TAXJSON_OFFLINE is set but a "
                          f"crypto price for {symbol} on {date_str} is not in "
                          f"the cache and would be fetched from Yahoo Finance. "
                          f"Unset it to allow the lookup, or add the price to "
                          f"the cache / the row."))
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
        # Bounded timeout — Yahoo's unofficial endpoint occasionally hangs;
        # without this the whole pipeline freezes on a single bad symbol.
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode())
        return chart_close(data, f"{y_symbol}-USD", date_str)
    except Exception as e:
        hint = ("" if symbol in PROJECT_CRYPTO_IDS
                else f" — {_crypto_line_hint(symbol)}")
        cli_diag.warn(PROG, f"failed to fetch crypto price for {symbol} "
                            f"on {date_str} (Yahoo {y_symbol}-USD): "
                            f"{e}{hint}")
    return 0.0


def _shown_cache_path() -> str:
    """The cache path as shown in a report: `~/...` under the home
    folder (a .sum never carries the OS user name, S037-18)."""
    home = os.path.expanduser("~")
    if CACHE_FILE.startswith(home + os.sep):
        return "~" + CACHE_FILE[len(home):]
    return os.path.basename(CACHE_FILE)


def ids_seen_in_cache(symbols, cache, ids=None):
    """Messages (without a prefix) for the coins priced under their
    default Yahoo id (no CRYPTO line in `ids`, default the run's
    PROJECT_CRYPTO_IDS) while the price cache holds prices of a numbered
    id of the same ticker (`<SYMBOL><number>`) from earlier runs: the
    project was priced under that id before (once from a built-in
    table taxjson no longer carries), and `<SYMBOL>-USD` may well be
    another asset. Names the exact ticker.map line. crypto-sends'
    price lookup asks too."""
    table = PROJECT_CRYPTO_IDS if ids is None else ids
    out = []
    keys = list(cache) if isinstance(cache, dict) else []
    for sym in sorted(symbols):
        if sym in table:
            continue
        pat = re.compile(re.escape(sym) + r"(\d{3,})-\d{4}-\d{2}-\d{2}$")
        ids = sorted({sym + m.group(1) for k in keys
                      for m in [pat.match(str(k))] if m})
        if not ids:
            continue
        lines = "\n".join(f"    CRYPTO {sym} {i}" for i in ids)
        out.append(
            f"{sym} has no CRYPTO line in ticker.map, "
            f"so it is priced as Yahoo {sym}-USD — but the price cache "
            f"({_shown_cache_path()}) holds prices for Yahoo "
            f"{', '.join(i + '-USD' for i in ids)} from earlier runs "
            f"(taxjson no longer carries a built-in crypto id table). "
            f"{sym}-USD may be another asset. If the numbered id is your "
            f"coin (check on finance.yahoo.com), add to ticker.map:\n"
            f"{lines}")
    return out


def _utc_today() -> str:
    """Today's date in UTC — the Yahoo daily candle's key."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()

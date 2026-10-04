#!/usr/bin/env python3
import sys
import re
import argparse
import urllib.request
import urllib.parse
import json
import calendar
import math
import time
import os

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.core import TaxTransaction, load_transactions
from taxjson.lib import cli_diag
from taxjson.lib.offline import offline_enabled

PROG = "taxjson-fill-crypto"

CACHE_FILE = os.path.expanduser("~/.crypto_price_cache.json")

def load_cache():
    # OSError too: an unreadable cache (permissions, dangling symlink)
    # degrades to a refetch, not a traceback. A cache that is valid
    # JSON but not an object ([], 5) degrades the same way, as
    # to_base_curr's loader does (re-audit A2-1403 / A2-1446).
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


def _damaged_entry(cache, key) -> bool:
    """`key` is in the cache but its value is not a number at all."""
    return key in cache and _cache_number(cache[key]) is None

def save_cache(cache_data):
    # A unique temp file renamed into place under a lock, merged with
    # what another project's concurrent run saved meanwhile (re-audit
    # A2-0233; a Ctrl-C mid-dump still never truncates the cache).
    from taxjson.lib.json_cache import save_json_cache
    save_json_cache(CACHE_FILE, cache_data, merge=True, prog=PROG, indent=2)

# The project's coin ids for THIS run: {SYMBOL: YAHOO_ID} from the
# ticker.map `CRYPTO SYMBOL YAHOO_ID` lines, filled by main() (and by
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


# USD-pegged stablecoins: 1.0/unit by definition — no lookup, no cache,
# no network. A stablecoin staking reward (Kraken `earn/reward` in USDC)
# used to reach this filler folded to the symbol `USD`, which the
# phantom-cash guard below refuses to price, so the income booked at $0.
# All five USD stablecoins (re-audit A2-1000 / A2-0593: PYUSD and GUSD
# were priced from Yahoo here while crypto-sends valued them at par;
# tax-logic US-CRYPTO-02). In a Canada project the parsers fold them
# to USD before this filler sees them.
from taxjson.lib.brokerages._crypto_common import (  # noqa: E402
    USD_STABLECOINS as _STABLE_ONE_TO_ONE)


def load_symbol_overrides(dirs):
    """The CRYPTO lines (`CRYPTO SYMBOL YAHOO_ID`) of the ticker.map
    found in each of `dirs` (later dirs win), as {SYMBOL: YAHOO_ID} —
    the only coin ids there are (no built-in table). A malformed line
    is skipped with a warning — a typo shouldn't kill a price-fill run
    (`taxjson run` refuses the map up front). A folder still holding the
    old crypto_ticker.map stops the run (`taxjson migrate` moves it into
    ticker.map)."""
    from taxjson.lib.ticker_map import side_rules_in
    merged = {}
    for d in dirs:
        merged.update(side_rules_in([str(d)]).crypto)
    return merged

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
                f"taxjson-fill-crypto: TAXJSON_OFFLINE is set but a "
                f"crypto price for {symbol} on {date_str} is not in "
                f"the cache and would be fetched from Yahoo Finance. "
                f"Unset it to allow the lookup, or add the price to "
                f"the cache / the row.")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
        # Bounded timeout — Yahoo's unofficial endpoint occasionally hangs;
        # without this the whole pipeline freezes on a single bad symbol.
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode())
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        for c in closes:
            if c is not None:
                v = float(c)
                if math.isfinite(v) and v > 0:
                    return v
        # HTTP 200 with a null/empty/non-positive close (a data gap or
        # an unfinished candle) is a failed lookup like any other — it
        # used to fall through to `return 0.0` with no warning at all
        # (audit S000-00).
        raise ValueError(f"Yahoo returned no usable close for "
                         f"{y_symbol}-USD (close={closes!r})")
    except Exception as e:
        hint = ("" if symbol in PROJECT_CRYPTO_IDS
                else f" — {_crypto_line_hint(symbol)}")
        cli_diag.warn(PROG, f"failed to fetch crypto price for {symbol} "
                            f"on {date_str} (Yahoo {y_symbol}-USD): "
                            f"{e}{hint}")
    return 0.0

@guard_main("taxjson-fill-crypto")
def main():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Fill in the fair value of crypto rows the export left "
                    "unpriced, from Yahoo Finance daily prices (cached in "
                    "~/.crypto_price_cache.json; TAXJSON_OFFLINE=1 serves "
                    "the cache only). ticker.map CRYPTO lines name a "
                    "coin's Yahoo id.")
    parser.add_argument("input", nargs="?", help="Input JSON file (taxjson schema)")
    parser.add_argument(
        "--project-root", metavar="DIR",
        help="Read the CRYPTO lines of ticker.map from this project root (then the "
             "input file's folder) instead of the current directory. "
             "`taxjson run` always passes it, so the project's own map "
             "applies whatever the cwd and a map in the cwd never "
             "leaks into another project.")
    args = parser.parse_args()

    # User overrides: the project root (or, standalone, the cwd), then
    # the input file's directory (the closer to the data, the higher
    # the precedence).
    _dirs = [args.project_root or "."]
    if args.input:
        _dirs.append(os.path.dirname(os.path.abspath(args.input)) or ".")
    # Applied for THIS run only: `taxjson run` dispatches the tool
    # in-process, and a permanent update of the module-level table
    # carried one project's map into every later run in the process.
    _saved = dict(PROJECT_CRYPTO_IDS)
    PROJECT_CRYPTO_IDS.update(load_symbol_overrides(_dirs))
    try:
        return _fill(args)
    finally:
        PROJECT_CRYPTO_IDS.clear()
        PROJECT_CRYPTO_IDS.update(_saved)


_SWAP_LEG_RE = re.compile(r'^(.+)-(sell|buy|base|quote)$')


def _swap_pairs(loaded):
    """(received, spent) leg pairs of unpriced crypto-to-crypto swaps.
    The parsers give both legs of one exchange one id stem — Kraken
    `<refid>-sell`/`-buy` and `<txid>-base`/`-quote`, Coinbase Convert
    `<id>-sell`/`-buy` and an Advanced Trade's `<id>` + `<id>-quote`.
    A pair is exactly two unpriced BUYSELL rows under one stem with
    opposite-sign quantities and different symbols."""
    groups = {}
    for tx in loaded:
        if (tx.action != 'BUYSELL' or not getattr(tx, 'id', None)
                or abs(tx.price) >= 1e-8 or abs(tx.net_amount) >= 1e-8
                or abs(tx.quantity) <= 1e-12
                or tx.symbol in ('USD', 'CAD')
                or tx.symbol in _STABLE_ONE_TO_ONE):
            continue
        m = _SWAP_LEG_RE.match(str(tx.id))
        groups.setdefault(m.group(1) if m else str(tx.id), []).append(tx)
    pairs = []
    for legs in groups.values():
        if len(legs) != 2 or legs[0].symbol == legs[1].symbol:
            continue
        a, b = legs
        if (a.quantity > 0) == (b.quantity > 0):
            continue
        pairs.append((a, b) if a.quantity > 0 else (b, a))
    return pairs


def _value_swaps_once(pairs, unpriced):
    """One exchange, one value (audit S013-08): each leg priced from its
    own coin's daily close gave the spent coin's proceeds and the
    received coin's cost two different values — a phantom gain or loss
    that stayed in the lifetime total. Both legs take the RECEIVED
    coin's fair value (what the spent coin was exchanged for), else the
    spent coin's when the received coin has no price. Returns the
    number of swaps valued."""
    n = 0

    def _unpriced(leg):
        return any(u is leg for u in unpriced)

    for recv, spent in pairs:
        value = (abs(recv.net_amount) if not _unpriced(recv) else 0.0) \
            or (abs(spent.net_amount) if not _unpriced(spent) else 0.0)
        if not value > 0:
            continue
        for leg in (recv, spent):
            leg.net_amount = value
            leg.gross_amount = value
            leg.price = round(value / abs(leg.quantity), 8)
            leg.currency = 'USD'
        unpriced[:] = [u for u in unpriced
                       if u is not recv and u is not spent]
        n += 1
    return n


# Echoed to the console by `taxjson run` (its ATTENTION channel) and
# kept in the account's .sum DIAGNOSTICS.
ATTENTION_CRYPTO_ID = "warning: ATTENTION: crypto id:"
# Fiat quotes a coin's broker price can be compared with a Yahoo USD
# close (within a factor of a few); a coin-quoted price cannot.
_FIAT_QUOTES = frozenset({'USD', 'CAD', 'EUR', 'GBP', 'AUD', 'NZD', 'CHF'})
# A Yahoo close this many times above or below the coin's own broker
# prices near the date is another asset, not a market move.
_IMPLAUSIBLE_FACTOR = 5.0
_NEAR_DAYS = 7


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


def _implausible_yahoo_prices(yahoo_priced, broker_priced):
    """ATTENTION lines for coins whose Yahoo closes are more than
    _IMPLAUSIBLE_FACTOR times off the coin's own broker-priced rows
    within _NEAR_DAYS days (median over the rows that have such a
    neighbour): the Yahoo id is another asset sharing the ticker."""
    import datetime as _dt

    def _day(s):
        try:
            return _dt.date.fromisoformat(str(s)[:10])
        except ValueError:
            return None

    marks = {}
    for tx in broker_priced:
        d = _day(tx.date)
        cur = str(getattr(tx, 'currency', '') or '').upper()
        if d is None or not (cur in _FIAT_QUOTES
                             or cur in _STABLE_ONE_TO_ONE):
            continue
        p = abs(tx.price) or abs(tx.net_amount) / abs(tx.quantity)
        if p > 0:
            marks.setdefault(tx.symbol, []).append((d, p, cur))
    ratios = {}
    for tx, yid in yahoo_priced:
        d = _day(tx.date)
        near = [m for m in marks.get(tx.symbol, ())
                if d is not None and abs((m[0] - d).days) <= _NEAR_DAYS]
        if not near:
            continue
        m = min(near, key=lambda m: abs((m[0] - d).days))
        ratios.setdefault((tx.symbol, yid), []).append(
            (tx.price / m[1], tx.date, tx.price, m))
    out = []
    for (sym, yid), rs in sorted(ratios.items()):
        rs.sort(key=lambda r: r[0])
        ratio, day, yp, (md, mp, mcur) = rs[len(rs) // 2]
        if 1 / _IMPLAUSIBLE_FACTOR <= ratio <= _IMPLAUSIBLE_FACTOR:
            continue
        fix = (f"the ticker.map line `CRYPTO {sym} "
               f"{PROJECT_CRYPTO_IDS[sym]}` names the wrong Yahoo id — "
               f"find the coin's id on finance.yahoo.com and correct it"
               if sym in PROJECT_CRYPTO_IDS else _crypto_line_hint(sym))
        out.append(
            f"{ATTENTION_CRYPTO_ID} {sym} priced from Yahoo {yid}-USD at "
            f"{yp:g} USD on {day}, but your own {sym} rows are priced at "
            f"{mp:g} {mcur} on {md.isoformat()} ({ratio:.3g}x) — Yahoo "
            f"{yid}-USD looks like another asset, so the {len(rs)} "
            f"row(s) priced from it are likely wrong:\n    {fix}.")
    return out


def _utc_today() -> str:
    """Today's date in UTC — the Yahoo daily candle's key."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _fill(args):
    cache = load_cache()
    cache_dirty = False
    # A close for TODAY (UTC) or later is the still-open candle's
    # intraday price: used for this run, never cached — caching it
    # fixed a provisional FMV for good (audit S025-08; to_base_curr
    # applies the same rule to FX).
    provisional = {}
    today_utc = _utc_today()

    # Shared loader funnel: `#` comments, qty alias, type guards.
    try:
        if args.input:
            loaded = load_transactions(args.input)
        else:
            from taxjson.lib.pipeline import load_stdin_transactions
            loaded = load_stdin_transactions()
    except ValueError as e:
        cli_diag.error(PROG, str(e))
        sys.exit(1)

    transactions = []
    unpriced = []
    # Rows the export priced itself (a price or a total): the yardstick
    # the Yahoo-priced rows of the same coin are checked against.
    broker_priced = [tx for tx in loaded
                     if (abs(tx.price) >= 1e-8 or abs(tx.net_amount) >= 1e-8)
                     and abs(tx.quantity) > 1e-12]
    yahoo_priced = []       # (row, Yahoo id) priced from a daily close
    looked_up = set()       # coins that went to Yahoo at all
    swap_pairs = _swap_pairs(loaded)
    for tx in loaded:
        if tx.action in ('BUYSELL', 'DIVIDEND'):
            # Stablecoin (or a hand-entered `USD`-symbol DIVIDEND, i.e.
            # a reward already folded to its anchor): worth its
            # quantity. Priced BEFORE the phantom-cash guard below,
            # which is what kept a folded reward at $0.
            if (abs(tx.price) < 1e-8 and abs(tx.net_amount) < 1e-8
                    and abs(tx.quantity) > 1e-12
                    and (tx.symbol in _STABLE_ONE_TO_ONE
                         or (tx.symbol == 'USD'
                             and tx.action == 'DIVIDEND'))):
                tx.price = 1.0
                tx.net_amount = abs(tx.quantity)
                tx.gross_amount = tx.net_amount
                tx.currency = 'USD'
                transactions.append(tx)
                continue
            if abs(tx.price) < 1e-8 and tx.symbol not in ('USD', 'CAD'):
                if abs(tx.net_amount) > 1e-8:
                    # The broker supplied an EXACT total (a Coinbase row
                    # with a blank price column but a real Total, or a
                    # hand-entered .tt total). Derive the missing
                    # per-unit price from it — the old unconditional
                    # overwrite replaced the broker's own figure with a
                    # daily-close estimate. Currency stays as labeled:
                    # the total is denominated in the row's currency.
                    # qty=0 rows (cash-only records) keep the total
                    # verbatim with no derivable price — falling
                    # through would book ONE full unit of Yahoo FMV
                    # over a real broker total (re-audit).
                    if abs(tx.quantity) > 1e-12:
                        tx.price = round(abs(tx.net_amount) / abs(tx.quantity), 8)
                    transactions.append(tx)
                    continue
                if abs(tx.quantity) <= 1e-12:
                    # Nothing to value: qty 0, price 0, total 0. The
                    # old path priced it at ONE full unit of FMV
                    # (`qty or 1.0`), booking a phantom cost/income
                    # figure on a row that records no asset at all.
                    cli_diag.warn(
                        PROG,
                        f"{tx.action} {tx.symbol} {tx.date} has quantity "
                        f"0 and no price or total — left unpriced "
                        f"(nothing to value). Fix the row if a real "
                        f"quantity is missing.")
                    transactions.append(tx)
                    continue
                # Key the cache on the RESOLVED Yahoo id, not the raw
                # symbol: the fetch honours the ticker.map CRYPTO
                # lines, so a raw-symbol key kept serving
                # the OLD coin's price after the user remapped the
                # symbol (stage-tools audit).
                y_symbol = yahoo_id(tx.symbol)
                looked_up.add(tx.symbol)
                cache_key = f"{y_symbol}-{tx.date}"
                if _damaged_entry(cache, cache_key):
                    # null / "abc" / true / Infinity: a miss, looked up
                    # again (re-audit A2-0772 / A2-1403).
                    cli_diag.warn(
                        PROG,
                        f"{CACHE_FILE}: entry {cache_key} is "
                        f"{cache[cache_key]!r}, not a price — ignored "
                        f"and looked up again.")
                    del cache[cache_key]
                if cache_key not in cache and cache_key not in provisional:
                    if tx.symbol == 'USD' or tx.symbol in _STABLE_ONE_TO_ONE:
                        p = 1.0
                    else:
                        p = get_crypto_price(tx.symbol, tx.date)
                        time.sleep(0.5)
                    # Only persist a SUCCESSFUL fetch. Caching a 0.0 (the
                    # failure sentinel from get_crypto_price) would poison the
                    # on-disk cache permanently: a transient network/symbol
                    # miss would book this lot at $0 cost basis on every future
                    # run with no retry. Leaving it uncached lets a later run
                    # re-fetch; the row keeps price≈0 and the warning fires.
                    if p > 0 and tx.date >= today_utc:
                        provisional[cache_key] = p
                        cli_diag.note(
                            PROG,
                            f"{tx.symbol} {tx.date}: today's candle is "
                            f"still open — its intraday price {p:g} is "
                            f"used for this run only (not cached); "
                            f"re-run after the UTC day closes for the "
                            f"final FMV.")
                    elif p > 0:
                        cache[cache_key] = p
                        cache_dirty = True

                fetched_price = (cached_price(cache, cache_key)
                                 or provisional.get(cache_key, 0.0))
                if not fetched_price > 0:
                    unpriced.append(tx)
                if fetched_price > 0:
                    yahoo_priced.append((tx, y_symbol))
                    tx.price = fetched_price
                    # DIVIDEND rows from staking carry the reward qty (set by
                    # the parser) so income reports as qty*FMV. (qty=0
                    # rows never reach here — see the guard above.)
                    tx.net_amount = fetched_price * abs(tx.quantity)
                    tx.gross_amount = tx.net_amount
                    # The Yahoo close is a *-USD quote. Stamping it onto
                    # a row labeled CAD (e.g. a Coinbase-Canada Convert
                    # leg) made the FX stage skip the row as already-
                    # converted, booking USD FMV as CAD — basis
                    # understated by the full exchange rate.
                    tx.currency = 'USD'
        transactions.append(tx)

    if cache_dirty:
        save_cache(cache)
    for line in ([f"{ATTENTION_CRYPTO_ID} {m}"
                  for m in ids_seen_in_cache(looked_up, cache)]
                 + _implausible_yahoo_prices(yahoo_priced, broker_priced)):
        sys.stderr.write(line + "\n")
    n_swaps = _value_swaps_once(swap_pairs, unpriced)
    if n_swaps:
        cli_diag.note(
            PROG,
            f"{n_swaps} crypto-to-crypto swap(s) valued ONCE: both legs "
            f"carry the received coin's fair value (the spent coin's "
            f"proceeds = the received coin's cost).")
    if unpriced:
        # Never silently 0 (R1-105): these rows still carry price 0 —
        # $0 income, $0 cost basis, or $0 proceeds. The crypto path's
        # `taxjson-validate --require-prices` turns each into an ERROR
        # (fatal under `taxjson run --strict`).
        shown = ", ".join(f"{t.action} {t.symbol} {t.date}"
                          for t in unpriced[:10])
        more = (f" (+{len(unpriced) - 10} more)"
                if len(unpriced) > 10 else "")
        cli_diag.warn(
            PROG,
            f"{len(unpriced)} row(s) left UNPRICED (price 0 -> $0 "
            f"income/cost/proceeds): {shown}{more}. The price lookup "
            f"failed (see above); re-run when Yahoo is reachable, add "
            f"the price to the row, or map the symbol in "
            f"ticker.map (`CRYPTO SYMBOL YAHOO_ID`).")
        for sym in sorted({t.symbol for t in unpriced}
                          - set(PROJECT_CRYPTO_IDS)):
            cli_diag.warn(PROG, f"{sym}: {_crypto_line_hint(sym)}.")

    output_data = {
        "transactions": [tx.to_dict() for tx in transactions]
    }
    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

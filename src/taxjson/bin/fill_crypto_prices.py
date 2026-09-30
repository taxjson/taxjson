#!/usr/bin/env python3
import sys
import argparse
import urllib.request
import json
import calendar
import math
import time
import os

from taxjson.lib.core import TaxTransaction, load_transactions
from taxjson.lib import cli_diag
from taxjson.lib.offline import offline_enabled

PROG = "taxjson-fill-crypto"

CACHE_FILE = os.path.expanduser("~/.crypto_price_cache.json")

def load_cache():
    # OSError too: an unreadable cache (permissions, dangling symlink)
    # degrades to a refetch, not a traceback.
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_cache(cache_data):
    # tmp + os.replace (repo standard — price_chain._save_cache,
    # corp_actions manifest): a Ctrl-C mid-dump left a truncated file
    # that load_cache silently discarded, refetching every historical
    # price on the next run.
    tmp = CACHE_FILE + ".part"
    try:
        with open(tmp, 'w') as f:
            json.dump(cache_data, f, indent=2)
        os.replace(tmp, CACHE_FILE)
    except OSError as exc:
        print(f"{PROG}: warning: could not write {CACHE_FILE}: {exc}",
              file=sys.stderr)

# Built-in Yahoo ticker-collision disambiguations. Extended (or
# overridden) per project by a `crypto_ticker.map` file — same idea as
# yf_ticker.map: one `SYMBOL YF_ID` pair per line, `#` comments. The
# hardcoded set covers only the coins it lists; any OTHER user with a
# colliding coin needs the map file (KNOWN_ISSUES "SYMBOL_OVERRIDES is
# hardcoded to the maintainer's coins").
SYMBOL_OVERRIDES = {
    'TAO': 'TAO22974',
    'UNI': 'UNI7083',
    'LDO': 'LDO11808',
    'GRT': 'GRT6719',
    'FTM': 'FTM',
}

# USD-pegged stablecoins: 1.0/unit by definition — no lookup, no cache,
# no network. A stablecoin staking reward (Kraken `earn/reward` in USDC)
# used to reach this filler folded to the symbol `USD`, which the
# phantom-cash guard below refuses to price, so the income booked at $0.
_STABLE_ONE_TO_ONE = frozenset({'USDC', 'USDT', 'DAI'})


def load_symbol_overrides(dirs):
    """Merge `crypto_ticker.map` files found in `dirs` (later dirs win)
    over the built-in SYMBOL_OVERRIDES. Malformed lines are skipped
    with a warning — a typo shouldn't kill a price-fill run."""
    merged = dict(SYMBOL_OVERRIDES)
    for d in dirs:
        p = os.path.join(str(d), "crypto_ticker.map")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, 'r', encoding='utf-8-sig') as f:
                for lineno, line in enumerate(f, 1):
                    line = line.split('#', 1)[0].strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) != 2:
                        print(f"{PROG}: warning: {p}:{lineno}: expected "
                              f"'SYMBOL YF_ID', got {line!r} — skipped.",
                              file=sys.stderr)
                        continue
                    merged[parts[0].upper()] = parts[1]
        except OSError as exc:
            print(f"{PROG}: warning: could not read {p}: {exc}",
                  file=sys.stderr)
    return merged

def get_crypto_price(symbol, date_str):
    y_symbol = SYMBOL_OVERRIDES.get(symbol, symbol)
    try:
        # UTC midnight: Yahoo daily candles are UTC-keyed; local
        # mktime made the 1-day window straddle two candles east of
        # UTC, booking the adjacent day's close as FMV.
        dt = int(calendar.timegm(time.strptime(date_str, "%Y-%m-%d")))
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{y_symbol}-USD?period1={dt}&period2={dt+86400}&interval=1d"
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
        res = urllib.request.urlopen(req, timeout=15)
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
        cli_diag.warn(PROG, f"failed to fetch crypto price for {symbol} on {date_str}: {e}")
    return 0.0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", help="Input JSON file (taxjson schema)")
    parser.add_argument(
        "--project-root", metavar="DIR",
        help="Read crypto_ticker.map from this project root (then the "
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
    _saved = dict(SYMBOL_OVERRIDES)
    SYMBOL_OVERRIDES.update(load_symbol_overrides(_dirs))
    try:
        return _fill(args)
    finally:
        SYMBOL_OVERRIDES.clear()
        SYMBOL_OVERRIDES.update(_saved)


def _fill(args):
    cache = load_cache()
    cache_dirty = False

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
                # symbol: the fetch honours SYMBOL_OVERRIDES /
                # crypto_ticker.map, so a raw-symbol key kept serving
                # the OLD coin's price after the user remapped the
                # symbol (stage-tools audit).
                y_symbol = SYMBOL_OVERRIDES.get(tx.symbol, tx.symbol)
                cache_key = f"{y_symbol}-{tx.date}"
                if cache_key not in cache:
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
                    if p > 0:
                        cache[cache_key] = p
                        cache_dirty = True

                fetched_price = cache.get(cache_key, 0.0)
                if not fetched_price > 0:
                    unpriced.append(tx)
                if fetched_price > 0:
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
            f"crypto_ticker.map.")

    output_data = {
        "transactions": [tx.to_dict() for tx in transactions]
    }
    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import sys
try:
    import yfinance as yf
except ImportError:                     # pragma: no cover
    yf = None


def _require_extra():
    """Friendly refusal on a base install instead of a raw traceback."""
    if yf is None:
        import sys as _sys
        print("taxjson-to-base-curr needs the [fx] extra: "
              "pip install 'taxjson[fx]'", file=_sys.stderr)
        raise SystemExit(1)

from datetime import datetime, timedelta
import pandas as pd
import os
import json

CACHE_FILE = os.path.expanduser("~/.currency_price_cache.json")

def load_cache():
    # OSError too: an unreadable cache degrades to a refetch.
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_cache(cache_data):
    # tmp + os.replace (repo standard): a kill mid-dump must not leave
    # a truncated cache that the next run silently discards.
    tmp = CACHE_FILE + ".part"
    try:
        with open(tmp, 'w') as f:
            json.dump(cache_data, f, indent=2)
        os.replace(tmp, CACHE_FILE)
    except OSError as exc:
        print(f"taxjson-to-base-curr: warning: could not write "
              f"{CACHE_FILE}: {exc}", file=sys.stderr)

def main():
    _require_extra()
    parser = argparse.ArgumentParser(
        description=(
            "Fetch historical daily FX rates for a currency pair via "
            "yfinance and print them in the rates-file format used by "
            "taxjson-convert-currency (`YYYY-MM-DD HH:MM:SS FROM TO "
            "RATE` per line). Caches fetched values under "
            "~/.currency_price_cache.json so re-runs are near-instant. "
            "Forward-fills weekends and holidays from the prior "
            "trading day so every calendar date in the lookback "
            "window emits a row, then appends a single intra-day "
            "spot rate stamped with the current time when the market "
            "is open."
        ),
    )
    parser.add_argument(
        "from_currency", nargs="?", default="USD",
        help="ISO source currency (default: USD).",
    )
    parser.add_argument(
        "to_currency", nargs="?", default="CAD",
        help="ISO target currency (default: CAD).",
    )
    args = parser.parse_args()
    from_curr = args.from_currency.upper()
    to_curr = args.to_currency.upper()

    # Define the currency pair for yfinance
    pair = f"{from_curr}{to_curr}"
    ticker_symbol = f"{pair}=X"

    # Get the date range (approx. 5.5 years)
    end_dt = datetime.today()
    start_dt = end_dt - timedelta(days=2000)
    
    cache = load_cache()
    cache_dirty = False

    # Check which dates are missing in the cache
    # We only care about potential trading days, but checking all is safer
    missing_dates = []
    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime('%Y-%m-%d')
        if f"{pair}-{date_str}" not in cache:
            missing_dates.append(current_dt)
        current_dt += timedelta(days=1)

    if missing_dates:
        fetch_start_dt = min(missing_dates)
        fetch_start = fetch_start_dt.strftime('%Y-%m-%d')
        fetch_end = (end_dt + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Fetch historical trading data
        try:
            if os.environ.get("TAXJSON_OFFLINE"):
                raise SystemExit(
                    f"taxjson-to-base-curr: TAXJSON_OFFLINE is set but "
                    f"the FX rate cache needs {ticker_symbol} "
                    f"{fetch_start}..{fetch_end} from Yahoo Finance. "
                    f"Unset it to allow the download, or supply "
                    f"work/to_base.csv yourself.")
            data = yf.download(ticker_symbol, start=fetch_start, end=fetch_end, progress=False)
            
            # We want to fill the cache for ALL dates from fetch_start to end_dt
            # to avoid re-fetching on weekends/holidays.
            
            # First, find the most recent value before fetch_start to start forward-filling
            current_val = None
            lookback_dt = fetch_start_dt - timedelta(days=1)
            for _ in range(10):
                lb_str = lookback_dt.strftime('%Y-%m-%d')
                if f"{pair}-{lb_str}" in cache:
                    current_val = cache[f"{pair}-{lb_str}"]
                    break
                lookback_dt -= timedelta(days=1)

            # Create a series with all calendar dates in the fetched range
            if not data.empty:
                if 'Close' in data.columns:
                    if isinstance(data.columns, pd.MultiIndex):
                        close_series = data['Close'][ticker_symbol]
                    else:
                        close_series = data['Close']
                    
                    # Convert index to date strings for easy lookup
                    data_dict = {d.strftime('%Y-%m-%d'): v for d, v in close_series.items()}
                    
                    fill_dt = fetch_start_dt
                    while fill_dt <= end_dt:
                        d_str = fill_dt.strftime('%Y-%m-%d')
                        if d_str in data_dict:
                            # Extract scalar
                            val = data_dict[d_str]
                            if hasattr(val, 'iloc'): val = val.iloc[0]
                            elif hasattr(val, '__len__') and not isinstance(val, str):
                                # Some yfinance shapes are zero-length /
                                # un-indexable arrays; fall through to
                                # the pd.notnull check rather than crash.
                                try:
                                    val = val[0]
                                except (TypeError, IndexError, KeyError):
                                    pass
                            
                            if pd.notnull(val):
                                current_val = float(val)
                        
                        if current_val is not None:
                            # Never cache TODAY's value: intraday it is a
                            # partial bar (whatever the market showed when
                            # taxjson first ran today), and a permanent cache
                            # entry would freeze that as the historical daily
                            # rate forever. Tomorrow's run re-fetches today
                            # as a completed bar.
                            if d_str != datetime.now().strftime('%Y-%m-%d'):
                                cache[f"{pair}-{d_str}"] = current_val
                                cache_dirty = True

                        fill_dt += timedelta(days=1)
        except Exception as e:
            sys.stderr.write(f"WARNING: Error fetching data for {ticker_symbol}: {e}\n")

    if cache_dirty:
        save_cache(cache)

    # Prepare data for output
    current_dt = start_dt
    last_val = None
    
    # We need to find the most recent value before start_dt to handle initial ffill
    # Look back up to 10 days
    lookback_dt = start_dt - timedelta(days=1)
    for _ in range(10):
        lb_str = lookback_dt.strftime('%Y-%m-%d')
        if f"{pair}-{lb_str}" in cache:
            last_val = cache[f"{pair}-{lb_str}"]
            break
        lookback_dt -= timedelta(days=1)

    while current_dt <= end_dt:
        date_str = current_dt.strftime('%Y-%m-%d')
        val = cache.get(f"{pair}-{date_str}")
        
        if val is not None:
            last_val = val
        
        if last_val is not None:
            # %.6g keeps significant digits for small-magnitude rates —
            # %.4f printed JPY→CAD (~0.00947) as 0.0095 (0.5% systematic
            # error) and sub-1e-4 rates as 0.0001/0.0000.
            print(f"{date_str} 12:00:00 {from_curr} {to_curr} {last_val:.6g}")
        
        current_dt += timedelta(days=1)

    # Fetch latest real-time rate (optional, don't cache this as it's intra-day)
    try:
        ticker = yf.Ticker(ticker_symbol)
        recent_data = ticker.history(period="1d", interval="1m")
        
        if not recent_data.empty:
            latest_price = recent_data['Close'].iloc[-1]
            now_date = datetime.now().strftime('%Y-%m-%d')
            now_time = datetime.now().strftime('%H:%M:%S')
            print(f"{now_date} {now_time} {from_curr} {to_curr} {latest_price:.6g}")
    except Exception:
        # Silently skip real-time update if market is closed or API fails
        pass

if __name__ == "__main__":
    main()

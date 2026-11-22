"""Shared test helpers: fill-crypto's runner.

Shared from: test_fix_x_mut_fill_crypto
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import taxjson.bin.fill_crypto_prices as fc
from taxjson.lib import crypto_prices


class _Run:
    """Run fill-crypto's main() on `rows` with a stub price source, a
    private cache file and no sleeping; restore every module global."""

    def __call__(self, tmp, rows, fetch, *, today="2026-09-30",
                 argv_extra=(), raw_text=None):
        cache = Path(tmp) / "cache.json"
        inp = Path(tmp) / "in.json"
        inp.write_text(raw_text if raw_text is not None
                       else json.dumps({"transactions": rows}),
                       encoding="utf-8")
        calls = []

        def _fetch(sym, day):
            calls.append((sym, day))
            return fetch(sym, day)
        saved = (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv,
                 dict(crypto_prices.PROJECT_CRYPTO_IDS), fc.time.sleep, crypto_prices._utc_today)
        crypto_prices.CACHE_FILE = str(cache)
        crypto_prices.get_crypto_price = _fetch
        fc.time.sleep = lambda s: None
        crypto_prices._utc_today = lambda: today
        sys.argv = ["fill-crypto", str(inp), "--project-root", str(tmp),
                    *argv_extra]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                try:
                    fc.main()
                except SystemExit as e:
                    code = e.code
        finally:
            (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv, overrides,
             fc.time.sleep, crypto_prices._utc_today) = saved
            crypto_prices.PROJECT_CRYPTO_IDS.clear()
            crypto_prices.PROJECT_CRYPTO_IDS.update(overrides)
        txs = (json.loads(out.getvalue())["transactions"]
               if out.getvalue().strip() else None)
        cached = (json.loads(cache.read_text()) if cache.exists() else {})
        return txs, cached, err.getvalue(), calls, code


run = _Run()

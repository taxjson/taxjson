"""Shared test helpers: the US engine's gains, payments and projects.

Shared from: test_fix_1029_r7d_us
"""
import contextlib
import io
import os
import subprocess
from pathlib import Path

from tax_rules.dual import tx
import _paths


def _gains(book, year=2025, **req):
    """pipeline.run_gains on a US book (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), [], [],
                        req=GainsRequest(country="usa", taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _paid(date, sym, amount):
    return tx("DIVIDEND_IN_LIEU", date, sym, 100, amount,
              gross_amount=amount, type="dividend_in_lieu")


def _covers(res):
    return [t for t in res["transactions"]
            if t.get("qty") and t.get("direction") == "SHORT"]


SRC = _paths.REPO / "src"


def _tjs(root, *args, ok=True, **env_more):
    from taxjson.lib.dispatch import python_module_argv
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0", **env_more)
    env.pop("TAXJSON_TICKER_MAP", None)
    env.update(env_more)
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)
    if ok and r.returncode != 0:
        raise AssertionError(f"{args}: {r.stderr[-2000:]}{r.stdout[-500:]}")
    return r


def _project(td, rows, *, year=2024, csv=None, more_toml="", ticker_map=""):
    root = Path(td)
    (root / "taxjson.toml").write_text(
        f'[settings]\ncountry = "usa"\nyear = {year}\n'
        'base_currency = "USD"\nsource_currencies = []\n'
        'local_timezone = "America/New_York"\n\n'
        '[accounts.margin]\ntype = "taxable"\n' + more_toml)
    (root / "ticker.map").write_text(ticker_map)
    d = root / "inputs" / "margin"
    d.mkdir(parents=True)
    (d / "book.tt").write_text("\n".join(rows) + "\n")
    if csv:
        (d / "book.csv").write_text(csv)
    return root


def _pil(res):
    return [t for t in res["transactions"]
            if t.get("action") == "DIVIDEND_IN_LIEU"][0]

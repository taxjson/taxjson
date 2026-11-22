"""Shared test helpers: the Coinbase header, rows and parse, the Kraken ledger header, a crypto-only Canadian project and the CLI.

Shared from: test_fix_crypto
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from _planted_rates import plant
import _paths


REPO_ROOT = _paths.REPO

CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")
KR_LEDGER_H = "txid,refid,time,type,subtype,aclass,subclass,asset,wallet," \
              "amount,fee,balance\n"


def _cb_row(cid, ts, typ, asset, qty, ccy, price, sub, total, fee, notes=""):
    return (f'{cid},{ts},{typ},{asset},{qty},{ccy},"{price}","{sub}",'
            f'"{total}","{fee}","{notes}"\n')


def _parse_cb(text):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb_2026.csv"
        p.write_text(CB_HEADER + text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = CoinbaseBrokerage().parse_file(p)
    return txs, err.getvalue()


def _bs(txs):
    return [t for t in txs if t["action"] == "BUYSELL"]


def _run_cli(root, *args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(cwd or REPO_ROOT), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=env)


def _project(td, name="crypto", source_currencies=("USD",)):
    """A 2026 crypto-only CA project. With USD configured, a fresh
    to_base.csv is pre-seeded (flat 1.40) so no FX download happens,
    and the same rates are in the currency cache of the HOME the tests
    run it under (`td`): a run that rebuilds the file keeps them."""
    root = Path(td) / "proj"
    (root / "inputs" / name).mkdir(parents=True)
    (root / "work").mkdir()
    sc = ", ".join(f'"{c}"' for c in source_currencies)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "canada"\nprovince = "ON"\n'
        f'base_currency = "CAD"\nsource_currencies = [{sc}]\n'
        f'[accounts.{name}]\ntype = "taxable"\ncrypto = true\n')
    if source_currencies:
        d, lines = date(2025, 12, 1), []
        while d <= date.today():
            lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.40 yahoo\n")
            d += timedelta(days=1)
        plant(root / "work" / "to_base.csv", "".join(lines), Path(td))
    return root


def _env(home, **extra):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("TAXJSON_OFFLINE", None)
    env.update(extra)
    return env

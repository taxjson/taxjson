"""Regression pins for the re-audit-2 phantoms list (lib/phantom_holdings,
find-missing-history, the run's phantoms.json handling).

Synthetic data only: fake account numbers (55500001 # pii-ok), all-CAD
Questrade books, no FX fetch.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(action, date, symbol, qty, price=0.0, net=0.0, account="margin",
        time="09:30:00", **kw):
    from taxjson.lib.core import TaxTransaction
    kw.setdefault("currency", "CAD")
    return TaxTransaction(action=action, date=date, time=time, symbol=symbol,
                          quantity=qty, price=price, net_amount=net,
                          account=account, date_settle=kw.pop("settle", date),
                          **kw)


def _run_cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _run_mod(mod, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return subprocess.run([sys.executable, "-m", mod, *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          env=e, stdin=subprocess.DEVNULL)


_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(date, settle, action, sym, qty, price, net):
    gross = -net if action == "Buy" else net
    return (f"{date} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} CORP,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")      # pii-ok


# ZZZ.TO: 100 sold with no buy in the data (bought before it), 100
# bought back — the phantom case. XEI.TO: a clean round trip.
_PHANTOM_CSV = _QT_HEADER + (
    _qt("2025-01-10", "2025-01-13", "Sell", "ZZZ.TO", -100, 30.0, 3000.0)
    + _qt("2025-03-03", "2025-03-04", "Buy", "ZZZ.TO", 100, 20.0, -2000.0)
    + _qt("2025-02-03", "2025-02-04", "Buy", "XEI.TO", 10, 10.0, -100.0)
    + _qt("2025-06-02", "2025-06-03", "Sell", "XEI.TO", -10, 12.0, 120.0))


def _project(tmp, csv=_PHANTOM_CSV, phantoms=None, config=_CONFIG):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(csv)
    if phantoms is not None:
        (root / "phantoms.json").write_text(json.dumps(phantoms))
    return root


# ------------------------------------------------------------------ A2-0313

class TestDanglingProjectMap(unittest.TestCase):
    """A2-0313: a project map that is a dangling symlink is unreadable,
    not absent — the run stops."""

    def test_dangling_symlinks_stop_the_run(self):
        for name in ("ticker.map", "phantoms.json", "distributions.map",
                     "crypto_ticker.map"):
            with self.subTest(name=name), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                (root / name).symlink_to(root / "nowhere" / name)
                r = _run_cli(root, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(f"{name} is a symlink", r.stderr)
                self.assertIn("nothing was run", r.stderr)

    def test_live_symlink_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            shared = root / "shared.map"
            shared.write_text("GLOBAL ZZQ.TO ZZR.TO\n")
            (root / "ticker.map").symlink_to(shared)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

if __name__ == "__main__":
    unittest.main()

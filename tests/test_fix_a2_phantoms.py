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


# ------------------------------------------------------------------ A2-0032

_QT_US_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
                 "Quantity,Price,Gross Amount,Commission,Net Amount,"
                 "Currency,Activity Type,Account #,Account Type\n")
_QA = _QT_US_HEADER + (
    "2026-02-02,2026-02-03,Buy,XYZ,XYZ CORP,100,50.00,-5000.00,0.00,"
    "-5000.00,USD,Trades,55500001,Margin\n"                    # pii-ok
    "2026-03-02,2026-03-02,TF6,XYZ,XYZ CORP TRANSFER OUT,-100,0.00,0.00,"
    "0.00,0.00,USD,Transfers,55500001,Margin\n")               # pii-ok
_QB = _QT_US_HEADER + (
    "2026-03-03,2026-03-03,TF6,XYZ,XYZ CORP TRANSFER BOOK VALUE 5000.00,"
    "100,0.00,0.00,0.00,0.00,USD,Transfers,55500002,Margin\n"  # pii-ok
    "2026-04-01,2026-04-02,Sell,XYZ,XYZ CORP,-100,40.00,4000.00,0.00,"
    "4000.00,USD,Trades,55500002,Margin\n")                    # pii-ok


def _sidecar(cache, acct, rows):
    (cache / f"{acct}_questrade_transfers.json").write_text(json.dumps({
        "metadata": {"account": acct, "brokerage": "questrade",
                     "kind": "transfer_sidecar"},
        "transactions": rows}))


class TestOwnAccountCustodyMove(unittest.TestCase):
    """A2-0032: a US custody move between two of your own taxable
    accounts is not carried by the per-account lots — said ATTENTION,
    --strict stops. Canada pools the ACB across the accounts (s.47)."""

    def _cache(self, tmp):
        cache = Path(tmp)
        _sidecar(cache, "qa", [{"action": "TRANSFER", "symbol": "XYZ.US",
                                "quantity": -100.0, "date": "2026-03-02"}])
        _sidecar(cache, "qb", [{"action": "TRANSFER", "symbol": "XYZ.US",
                                "quantity": 100.0, "date": "2026-03-03"}])
        return cache

    def test_pairs_out_and_in_legs(self):
        from taxjson.bin.taxjson_run import own_account_custody_moves
        with tempfile.TemporaryDirectory() as tmp:
            moves = own_account_custody_moves(["qa", "qb"],
                                              self._cache(tmp))
        self.assertEqual([(m["symbol"], m["from"], m["to"], m["qty"])
                          for m in moves], [("XYZ.US", "qa", "qb", 100.0)])

    def test_unrelated_legs_are_not_a_move(self):
        from taxjson.bin.taxjson_run import own_account_custody_moves
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            _sidecar(cache, "qa", [{"action": "TRANSFER",
                                    "symbol": "XYZ.US", "quantity": -100.0,
                                    "date": "2026-03-02"}])
            _sidecar(cache, "qb", [
                {"action": "TRANSFER", "symbol": "XYZ.US",
                 "quantity": 50.0, "date": "2026-03-03"},
                {"action": "TRANSFER", "symbol": "XYZ.US",
                 "quantity": 100.0, "date": "2026-06-03"}])
            self.assertEqual(own_account_custody_moves(["qa", "qb"], cache),
                             [])

    @rule("US-BASIS-05")
    @rule_absent("US-BASIS-05", country="canada")
    def test_flagged_in_the_us_only(self):
        from taxjson.bin import taxjson_run
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            for country, expect in (("usa", True), ("canada", False)):
                err = io.StringIO()
                with redirect_stderr(err):
                    taxjson_run._check_own_account_moves(
                        ["qa", "qb"], {"country": country}, cache,
                        strict=False)
                self.assertEqual("ATTENTION: XYZ.US: 100 moved from qa"
                                 in err.getvalue(), expect, country)
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                taxjson_run._check_own_account_moves(
                    ["qa", "qb"], {"country": "usa"}, cache, strict=True)
            self.assertIn("--strict", str(cm.exception))
            # Canada never stops on it (s.47 blends the accounts).
            with redirect_stderr(io.StringIO()):
                taxjson_run._check_own_account_moves(
                    ["qa", "qb"], {"country": "canada"}, cache, strict=True)

    @rule("US-BASIS-05")
    def test_us_run_says_so_and_strict_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = []\n'
                '[accounts.qa]\ntype = "taxable"\n'
                '[accounts.qb]\ntype = "taxable"\n')
            for a, csv in (("qa", _QA), ("qb", _QB)):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "questrade.csv").write_text(csv)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ATTENTION: XYZ.US: 100 moved from qa", r.stderr)
            r = _run_cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 1, r.stderr[-2000:])
            self.assertIn("between your own taxable accounts", r.stderr)

if __name__ == "__main__":
    unittest.main()

"""A crypto account's reports/<account>_holdings.toml (backlog TJ-0287).

`taxjson run` skipped the holdings handoff for crypto accounts: every
other account had one. It is now written from the account's own books
(the run's crypto prices and rates), each holding asset_type "crypto",
quantities and per-unit costs kept to significant digits. Synthetic
Kraken trades only (made-up coin ZZC).
"""
import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_export import render_holdings_toml
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,"
       "misc,ledgers\n")
# 0.1 + 0.2 BTC: a float sum of 0.30000000000000004; 1,000,000 ZZC at
# 0.000012345 CAD: a per-unit cost six decimals cut to 0.000012.
KR = (HDR
      + "TXA1,OA1,BTC/CAD,2025-01-15 10:00:00.1234,buy,limit,50000,"
        "5000,0,0.1,,,\n"
      + "TXA2,OA2,BTC/CAD,2025-02-15 10:00:00.1234,buy,limit,60000,"
        "12000,0,0.2,,,\n"
      + "TXA3,OA3,ZZC/CAD,2025-03-15 10:00:00.1234,buy,limit,0.000012345,"
        "12.345,0,1000000,,,\n")


def _project(td):
    root = Path(td)
    (root / "inputs" / "kr1").mkdir(parents=True)
    (root / "inputs" / "kr1" / "kr_trades.csv").write_text(KR)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
    return root


def _cli(root, *a):
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run",
                                             ["-C", str(root), *a]),
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


@unittest.skipIf(tomllib is None, "reading TOML needs Python 3.11+ or tomli")
class TestCryptoHoldingsToml(unittest.TestCase):
    def test_run_writes_the_crypto_accounts_holdings_from_its_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("==> Writing holdings reports/kr1_holdings.toml",
                          r.stdout)
            path = root / "reports" / "kr1_holdings.toml"
            text = path.read_text(encoding="utf-8")
            doc = tomllib.loads(text)
            inventory = json.loads((root / "work" / "kr1_gains.json")
                                   .read_text(encoding="utf-8"))["inventory"]
            tips = _cli(root, "tips")
            path.unlink()
            tips_without = _cli(root, "tips")
        held = {h["symbol"]: h for h in doc["holding"]}
        # Kraken coins carry its venue suffix (backlog TJ-0418).
        held = {s[:-len(".KR")]: h for s, h in held.items()
                if s.endswith(".KR")}
        self.assertEqual(sorted(held), ["BTC", "ZZC"])
        self.assertTrue(all(h["asset_type"] == "crypto"
                            for h in held.values()))
        # The float noise of 0.1 + 0.2 is not a holding's quantity.
        self.assertIn("quantity = 0.3\n", text)
        self.assertEqual(held["ZZC"]["cost_per_share"], 1.2345e-05)
        books = {row["symbol"]: row for row in inventory}
        for sym, h in held.items():
            self.assertEqual(h["currency"], "CAD")
            self.assertAlmostEqual(h["total_cost"],
                                   books[sym]["total_cost"], places=4)
            self.assertEqual(h["base_total_cost"], h["total_cost"])
        self.assertAlmostEqual(held["BTC"]["total_cost"], 17000.0, places=4)
        self.assertTrue(doc["meta"]["base_cost_basis"].startswith(
            "this crypto account's own books"))
        self.assertEqual([t["qty"] for t in held["BTC"]["trades"]],
                         [0.1, 0.2])
        # tips compares listings: a crypto account's file changes nothing.
        self.assertEqual(tips.returncode, 0, tips.stderr)
        self.assertEqual(tips.stdout, tips_without.stdout)

    def test_a_securities_holding_keeps_its_rounding(self):
        args = argparse.Namespace(account_name="margin", inputs=[],
                                  country="canada", base_currency=None)
        agg = {"ZZS.TO": {"qty": 0.30000000000000004, "total_cost": 3.7035,
                          "currency": "CAD"}}
        text = "\n".join(render_holdings_toml(agg, args))
        self.assertIn('asset_type = "equity"', text)
        self.assertIn("quantity = 0.30000000000000004", text)
        self.assertIn("cost_per_share = 12.345", text)
        crypto = "\n".join(render_holdings_toml(
            agg, argparse.Namespace(**vars(args), crypto=True)))
        self.assertIn('asset_type = "crypto"', crypto)
        self.assertIn("quantity = 0.3\n", crypto)


if __name__ == "__main__":
    unittest.main()

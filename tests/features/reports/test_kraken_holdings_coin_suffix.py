"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_export import render_holdings_toml
from taxjson.lib.dispatch import python_module_argv
import tomllib

import _paths

REPO_ROOT = _paths.REPO


def _cli(root, *a, cwd=REPO_ROOT):
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run",
                                             ["-C", str(root), *a]),
                          cwd=cwd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


# ------------------------------------------------------------- TJ-0418

KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
          "margin,misc,ledgers\n")
# ZZC: bought 1.0, sold 0.9999999 (a sale of "all" leaving 1e-07 units:
# rounding residue). ZZD: 2.5 units still held.
KR = (KR_HDR
      + "TXA1,OA1,ZZC/CAD,2025-01-15 10:00:00.1234,buy,limit,40,"
        "40,0,1.0,,,\n"
      + "TXA2,OA2,ZZD/CAD,2025-02-15 10:00:00.1234,buy,limit,10,"
        "25,0,2.5,,,\n"
      + "TXA3,OA3,ZZC/CAD,2025-03-15 10:00:00.1234,sell,limit,50,"
        "49.999995,0,0.9999999,,,\n")


def _kraken_project(td):
    root = Path(td)
    (root / "inputs" / "kr1").mkdir(parents=True)
    (root / "inputs" / "kr1" / "kr_trades.csv").write_text(KR)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
    return root


class TestKrakenHoldingsSymbol(unittest.TestCase):
    """TJ-0418: a Kraken account's holdings export wrote a coin bare
    (`ZZD`) while Kraken's own holdings snapshot writes `ZZD.KR`: a
    reader keyed on the suffix took the coin for a share. A coin's
    rounding residue was a holding too.

    Formerly: test_fix_1029_messages.TestKrakenHoldingsSymbol"""

    def test_kraken_coins_carry_the_venue_suffix_and_dust_is_said(self):
        with tempfile.TemporaryDirectory() as td:
            root = _kraken_project(td)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (root / "reports" / "kr1_holdings.toml").read_text(
                encoding="utf-8")
            doc = tomllib.loads(text)
            # A file the previous release wrote (bare coin): the next
            # run's moved list names no coin closed and opened again.
            prev = text.replace('symbol = "ZZD.KR"', 'symbol = "ZZD"')
            (root / "reports" / "kr1_holdings.toml").write_text(prev)
            again = _cli(root, "run", "--no-input")
        held = {h["symbol"]: h for h in doc["holding"]}
        self.assertEqual(sorted(held), ["ZZD.KR"])
        self.assertEqual(held["ZZD.KR"]["asset_type"], "crypto")
        self.assertEqual(held["ZZD.KR"]["quantity"], 2.5)
        self.assertEqual(doc["meta"]["holdings_count"], 1)
        self.assertIn("# left out as dust (under 1e-06 units and a cost "
                      "under 1): ZZC.KR 1e-07", text)
        self.assertEqual(again.returncode, 0, again.stderr)
        for said in ("changed since the last run",
                     "holdings changes vs prior run"):
            self.assertNotIn(said, again.stdout + again.stderr)

    def test_an_exchange_with_no_suffix_writes_the_bare_coin(self):
        args = argparse.Namespace(account_name="cb1", inputs=[],
                                  country="canada", base_currency=None,
                                  crypto=True, dust_threshold=1e-6)
        agg = {"ZZD": {"qty": 2.5, "total_cost": 25.0, "currency": "CAD"}}
        text = "\n".join(render_holdings_toml(agg, args))
        self.assertIn('symbol = "ZZD"\n', text + "\n")
        kr = "\n".join(render_holdings_toml(
            agg, argparse.Namespace(**vars(args), symbol_suffix="KR")))
        self.assertIn('symbol = "ZZD.KR"', kr)
        # A securities account is never suffixed.
        eq = "\n".join(render_holdings_toml(
            agg, argparse.Namespace(**{**vars(args), "crypto": False},
                                    symbol_suffix="KR")))
        self.assertIn('symbol = "ZZD"\n', eq + "\n")


if __name__ == "__main__":
    unittest.main()

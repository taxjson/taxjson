"""Blended multi-account taxable pass.

The canonical (filed-from) wash-adjusted artifacts are now computed by
ONE combined run over every taxable equity account: Canada's ACB blends
across accounts (ITA s.47) and US §1091 matches cross-account while
FIFO basis stays per account. Per-account `<name>.sum` remains the
isolated pre-blend baseline.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _row(date, action, sym, qty, price, net):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{abs(qty)*price:.2f},0.00,{net:.2f},CAD,1,"
            f"Trades,Individual\n")


def _project(tmp, country, accounts):
    root = Path(tmp)
    cur = "CAD" if country == "canada" else "USD"
    cfg = (f'[settings]\nyear = 2025\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n')
    for name, csv_rows in accounts.items():
        cfg += f'[accounts.{name}]\ntype = "taxable"\n'
        (root / "inputs" / name).mkdir(parents=True)
        body = csv_rows.replace(",CAD,", f",{cur},")
        (root / "inputs" / name / "questrade.csv").write_text(
            _QT_HEADER + body)
    (root / "taxjson.toml").write_text(cfg)
    return root


class TestCanadaAcbBlending(unittest.TestCase):
    def test_blended_acb_across_accounts(self):
        # A buys 100 @ 10; B buys 100 @ 20; A sells 100 @ 16.
        # Isolated A: gain +600. Blended s.47 ACB/sh = 15 → gain +100.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "canada", {
                "rbc": (_row("2025-01-10", "Buy", "XEI.TO", 100, 10.0,
                             -1000.0)
                        + _row("2025-06-01", "Sell", "XEI.TO", -100, 16.0,
                               1600.0)),
                "ibkr": _row("2025-02-10", "Buy", "XEI.TO", 100, 20.0,
                             -2000.0),
            })
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            wash = json.loads(
                (root / "work" / "rbc_gains_wash.json").read_text())
            plain = json.loads(
                (root / "work" / "rbc_gains.json").read_text())
        self.assertAlmostEqual(
            wash["summary"]["total_gain"], 100.0, places=2,
            msg="the canonical artifact must use the s.47 BLENDED ACB "
                "(3000/200 = 15/sh), not the per-account 10/sh")
        self.assertAlmostEqual(
            plain["summary"]["total_gain"], 600.0, places=2,
            msg="the per-account baseline stays isolated by design")
        # The blended inventory apportions ibkr's remaining 100 shares
        # at the blended ACB.
        inv_rows = [x for x in wash.get("inventory", [])
                    if x.get("symbol") == "XEI.TO"]
        self.assertFalse(inv_rows, "rbc holds nothing after the sale")

    def test_single_account_blend_is_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "canada", {
                "solo": (_row("2025-01-10", "Buy", "XEI.TO", 100, 10.0,
                              -1000.0)
                         + _row("2025-06-01", "Sell", "XEI.TO", -100, 16.0,
                                1600.0)),
            })
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            wash = json.loads(
                (root / "work" / "solo_gains_wash.json").read_text())
            plain = json.loads(
                (root / "work" / "solo_gains.json").read_text())
        self.assertAlmostEqual(wash["summary"]["total_gain"],
                               plain["summary"]["total_gain"], places=2)


class TestUsCrossAccountWash(unittest.TestCase):
    def test_wash_matches_across_taxable_accounts(self):
        # Loss in m1; rebuy in m2 within 30 days → disallowed, basis
        # transferred into m2's lot; m1's per-account FIFO untouched.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "usa", {
                "m1": (_row("2025-01-05", "Buy", "AAPL", 100, 100.0,
                            -10000.0)
                       + _row("2025-03-01", "Sell", "AAPL", -100, 95.0,
                              9500.0)),
                "m2": _row("2025-03-10", "Buy", "AAPL", 100, 96.0,
                           -9600.0),
            })
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            m1 = json.loads(
                (root / "work" / "m1_gains_wash.json").read_text())
            m2 = json.loads(
                (root / "work" / "m2_gains_wash.json").read_text())
        loss = next(e for e in m1["transactions"]
                    if e.get("raw_gain", 0) < 0)
        self.assertAlmostEqual(
            loss["disallowed_amount"], 500.0, places=2,
            msg="§1091 must match the rebuy in the OTHER taxable "
                "account (the per-account fan-out was blind to it)")
        self.assertTrue(m1["wash_sales"])
        # m2's surviving lot carries the deferred basis: 9600 + 500.
        inv = next(x for x in m2["inventory"]
                   if x.get("symbol") == "AAPL.US")
        self.assertAlmostEqual(inv["total_cost"], 10100.0, places=2)
        self.assertAlmostEqual(inv.get("deferred_wash", 0.0), 500.0,
                               places=2)


class TestBlendIntegrationRegressions(unittest.TestCase):
    """Fixes for the adversarial pass on the blend feature."""

    def test_filed_year_lock_agrees_with_blended_numbers(self):
        # Multi-account book: close-year snapshots BLENDED aggregates;
        # check-filed must recompute the same way or every closed
        # multi-account year falsely drifts forever (and bricks
        # --strict runs).
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "canada", {
                "rbc": (_row("2025-01-10", "Buy", "XEI.TO", 100, 10.0,
                             -1000.0)
                        + _row("2025-06-01", "Sell", "XEI.TO", -100, 16.0,
                               1600.0)),
                "ibkr": _row("2025-02-10", "Buy", "XEI.TO", 100, 20.0,
                             -2000.0),
            })
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            self.assertEqual(_run_cli(root, "close-year").returncode, 0)
            snap = json.loads(
                (root / "filed" / "2025.json").read_text())
            self.assertAlmostEqual(
                snap["accounts"]["rbc"]["realized"], 100.0, places=2)
            r = _run_cli(root, "check-filed")
        self.assertEqual(
            r.returncode, 0,
            "books untouched, yet check-filed drifted (+500): the "
            "recompute ran per-account instead of blended:\n" + r.stderr)
        self.assertIn("filed 2025: OK", r.stdout)

    def test_premiums_attributed_per_account_in_blended_mode(self):
        # Two accounts each assigned on the SAME contract: each stock
        # leg must consume only ITS account's premium.
        from taxjson.lib.core import TaxTransaction, USATaxRules

        def acct_rows(acct, premium_net):
            return [
                TaxTransaction(action='BUYSELL', date='2025-02-01',
                               time='10:00:00',
                               symbol='AAPL250307P00100000.US',
                               quantity=-1.0, price=premium_net / 100.0,
                               net_amount=premium_net, currency='USD',
                               account=acct),
                TaxTransaction(action='ASSIGN', date='2025-03-07',
                               time='09:00:00',
                               symbol='AAPL250307P00100000.US',
                               quantity=1.0, price=0.0, net_amount=0.0,
                               currency='USD', account=acct),
                TaxTransaction(action='ASSIGN', date='2025-03-07',
                               time='16:00:00', symbol='AAPL.US',
                               quantity=100.0, price=100.0,
                               net_amount=10000.0, currency='USD',
                               account=acct),
            ]
        txs = acct_rows('a1', 200.0) + acct_rows('a2', 300.0)
        r = USATaxRules().compute_gains(txs, per_account_basis=True)
        inv = {row['account']: row['total_cost']
               for row in r['inventory']}
        self.assertAlmostEqual(
            inv['a1'], 9800.0, places=2,
            msg="a1's leg absorbed BOTH premiums (buggy basis 9500)")
        self.assertAlmostEqual(
            inv['a2'], 9700.0, places=2,
            msg="a2's premium was hijacked (buggy basis 10000)")

    def test_find_missing_history_skips_blend_intermediates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "canada", {
                "lng": _row("2025-01-10", "Buy", "XEI.TO", 100, 10.0,
                            -1000.0),
                # naked short: one sale with no prior buy
                "sht": _row("2025-03-01", "Sell", "XEI.TO", -60, 12.0,
                            720.0),
            })
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "find-missing-history")
        line = next((ln for ln in r.stdout.splitlines()
                     if "sht" in ln and "XEI.TO" in ln), "")
        self.assertNotIn(
            "1440", line,
            "the .blend_base.json phantom book doubled the "
            "missing-history proceeds (720 -> 1440): " + line)
        self.assertIn("720", line)


if __name__ == "__main__":
    unittest.main()

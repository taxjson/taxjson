"""Crypto accounts blend across exchanges (2026-09 filing audit).

Two taxable crypto accounts in a Canadian project: kr1 buys 1 BTC at
50,000 and kr2 1 BTC at 90,000 (blended ACB 70,000/BTC); kr1 sells 1 BTC
at 40,000 on 2025-06-02 and kr2 buys 1 BTC on 2025-06-10 and holds it —
the whole 30,000 blended loss is superficial. Computed per exchange the
return showed a -10,000 allowed loss.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,"
       "misc,ledgers\n")
KR1 = (HDR + "TXA1,OA1,BTC/CAD,2025-01-15 10:00:00.1234,buy,limit,50000,"
       "50000,0,1.0,,,\nTXA2,OA2,BTC/CAD,2025-06-02 14:30:00.5678,sell,"
       "limit,40000,40000,0,1.0,,,\n")
KR2 = (HDR + "TXB1,OB1,BTC/CAD,2025-01-16 10:00:00.1234,buy,limit,90000,"
       "90000,0,1.0,,,\nTXB2,OB2,BTC/CAD,2025-06-10 11:00:00.0000,buy,"
       "limit,40000,40000,0,1.0,,,\nTXB3,OB3,BTC/CAD,2026-02-20 11:00:00."
       "0000,sell,limit,60000,120000,0,2.0,,,\n")


def _project(td, accounts, country="canada"):
    root = Path(td)
    cur = "CAD"
    cfg = (f'[settings]\nyear = 2025\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n')
    for name, body in accounts.items():
        cfg += f'[accounts.{name}]\ntype = "taxable"\ncrypto = true\n'
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "kr_trades.csv").write_text(body)
    (root / "taxjson.toml").write_text(cfg)
    return root


def _cli(root, *a):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *a], cwd=REPO_ROOT,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestCryptoBlend(unittest.TestCase):
    def test_two_exchanges_blend_and_everything_ties(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"kr1": KR1, "kr2": KR2})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("blended taxable crypto pass (kr1, kr2)", r.stdout)
            self.assertTrue((root / "work" / "kr1_gains_wash.json").exists())
            # Crypto-only project: no options, no grant-since nag.
            self.assertNotIn("option_grant_timing_since", r.stderr)
            s = _cli(root, "sum", "--json")
            self.assertEqual(s.returncode, 0, s.stderr)
            f = json.loads(s.stdout)["filing"]
            self.assertEqual([ln["line"] for ln in f["lines"]], ["7"])
            self.assertAlmostEqual(f["totals"]["gain"], 0.0, places=2)
            self.assertAlmostEqual(f["totals"]["denied"], 30000.0, places=2)
            a = _cli(root, "audit", "--summary")
            self.assertEqual(a.returncode, 0, a.stdout[-2000:] + a.stderr)
            c = _cli(root, "close-year")
            self.assertEqual(c.returncode, 0, c.stderr)
            k = _cli(root, "check-filed")
            self.assertEqual(k.returncode, 0, k.stdout + k.stderr)

    def test_single_crypto_account_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"kr1": KR1})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("crypto pass", r.stdout)
            self.assertFalse((root / "work" / ".cryptoblend_base.json")
                             .exists())
            s = json.loads(_cli(root, "sum", "--json").stdout)["filing"]
            self.assertAlmostEqual(s["totals"]["gain"], -10000.0, places=2)

    def test_us_crypto_stays_per_account_without_the_false_note(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"kr1": KR1, "kr2": KR2}, country="usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("crypto pass", r.stdout)
            # The note claimed the blended pass covered these accounts.
            self.assertNotIn("traded in more than one TAXABLE", r.stderr)


if __name__ == "__main__":
    unittest.main()

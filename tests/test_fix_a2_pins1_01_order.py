"""Re-audit-2 tests-pins-01: recompute commands merge the taxable books in
the accounts' taxjson.toml order, as the run's blended pass does
(CA-DATE-14: rows of different accounts at one moment follow the accounts'
order in taxjson.toml). A2-0497 (audit), A2-0502 (carryover).

Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

_MAP = ('[columns]\ndate="Date"\nsettle="Settle"\naction="Type"\n'
        'symbol="Ticker"\nquantity="Shares"\nprice="Price"\n'
        'amount="Amount"\ncurrency="Currency"\n'
        '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')


def _cli(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _order_book(root):
    """zeta (listed first) sells 100 @12 (cost 10: +200) at the same
    moment alpha buys 100 @20 — the generic importer prints no clock
    time. In toml order the sale is made from zeta's 100 alone (+200);
    sorted by name alpha's buy lands first, the pool averages 15 and
    the sale is a 300 superficial loss (net 0)."""
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\n\n'
        '[accounts.zeta]\ntype = "taxable"\n\n'
        '[accounts.alpha]\ntype = "taxable"\n')
    rows = {
        "zeta": ["2025-01-06,2025-01-07,BUY,XYZ.TO,100,10,-1000,CAD",
                 "2025-03-03,2025-03-04,SELL,XYZ.TO,-100,12,1200,CAD"],
        "alpha": ["2025-03-03,2025-03-04,BUY,XYZ.TO,100,20,-2000,CAD"],
    }
    for a, rs in rows.items():
        d = root / "inputs" / a
        d.mkdir(parents=True)
        (d / "generic.toml").write_text(_MAP)
        (d / "generic_t.csv").write_text(
            "Date,Settle,Type,Ticker,Shares,Price,Amount,Currency\n"
            + "\n".join(rs) + "\n")


class TestRecomputesUseTomlOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        _order_book(cls.root)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("CA-DATE-14")
    def test_run_books_the_toml_order(self):
        r = _cli(self.root, "form-export")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("Line 13200 (gain/loss): 200.00", r.stdout)

    @rule("CA-DATE-14")
    def test_audit_ties_out_in_toml_order(self):
        # A2-0497: audit merged the blended books in sorted() order,
        # denied 300 the run never denied and failed its tie-out.
        r = _cli(self.root, "audit", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        doc = json.loads(r.stdout)
        self.assertFalse(doc["failed"])
        self.assertAlmostEqual(doc["total_gain"], 200.0, places=2)
        self.assertAlmostEqual(doc["total_disallowed"], 0.0, places=2)

    @rule("CA-DATE-14")
    def test_carryover_net_matches_the_run(self):
        # A2-0502: carryover fed the taxable books in sorted() order
        # and showed a 2025 net of 0 instead of the run's 200.
        r = _cli(self.root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        rows = {x["year"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertAlmostEqual(rows[2025]["net_gain"], 200.0, places=2)


if __name__ == "__main__":
    unittest.main()

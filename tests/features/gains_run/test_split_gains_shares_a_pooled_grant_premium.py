"""The 2026-10-29 fix round 7 (wave C), planning: buy-check and
sell-check with a mapped option alias, harvest's cached radar cohorts,
pooled grant-timed premium split per account, a negative grant-timed
premium in the inventory, and the loss-window review's day-30
explanation of a new short (backlog TJ-1000, TJ-1009, TJ-1010,
TJ-1012, TJ-1014). Synthetic data only: made-up tickers (SYN, ALT) in
throwaway folders.

Formerly: test_fix_1029_r7c_planning"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj


def _grant_year():
    """A year whose January 2 is past: the grant-timed writes' year."""
    t = date.today()
    return t.year if t >= date(t.year, 1, 3) else t.year - 1


def _grant_project(root, accounts, rows):
    """A Canadian project under option grant timing, each account
    writing `rows` (a .tt text), run; its option priced in the cache at
    0.10 a unit (10 a contract)."""
    year = _grant_year()
    sym = _grant_symbol()
    text = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            f'source_currencies = []\noption_grant_timing_since = {year}\n')
    for a in accounts:
        text += f'[accounts.{a}]\ntype = "taxable"\n'
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "m.tt").write_text(rows)
    (root / "taxjson.toml").write_text(text)
    tj(root, "run", "--no-input")
    (root / "work" / ".price_cache.json").write_text(json.dumps(
        {sym: {"price": 0.1, "currency": "CAD", "quote_symbol": sym,
               "asof": date.today().isoformat()}}))
    return sym


def _grant_symbol():
    return f"SYN{(date.today().year + 1) % 100:02d}0115P00001000.TO"


def _inventory(root, account):
    for name in (f"{account}_gains_wash.json", f"{account}_gains.json"):
        f = root / "work" / name
        if f.exists():
            return json.loads(f.read_text())["inventory"]
    raise AssertionError(f"no gains file for {account}")


class TestPooledGrantPremiumSplit(unittest.TestCase):
    """TJ-1010: two taxable accounts each write one put for 20 under
    grant timing; the pool holds two contracts, premium 40 recognised.
    Each account's slice kept the pool's whole recognised_premium (40)
    while its cost was halved, so harvest showed a buy-back loss of 30
    a contract (60) where it is 10 (20).

    Formerly: test_fix_1029_r7c_planning.TestPooledGrantPremiumSplit"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        cls.sym = _grant_project(
            root, ("a", "b"),
            f"BUYSELL {_grant_year()}-01-02 10:00:00 {_grant_symbol()} -1 "
            f"CAD .2 20 0\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj1010_each_slice_carries_its_share(self):
        for a in ("a", "b"):
            inv = [r for r in _inventory(self.root, a)
                   if r["symbol"] == self.sym]
            self.assertEqual(len(inv), 1, inv)
            self.assertAlmostEqual(inv[0]["total_cost"], -20.0, places=4)
            self.assertAlmostEqual(inv[0]["recognised_premium"], 20.0,
                                   places=4, msg=inv[0])

    def test_tj1010_harvest_loss_once(self):
        r = tj(self.root, "harvest", "--options", "--no-ibkr", "--json")
        doc = json.loads(r.stdout)
        self.assertAlmostEqual(doc["totals"]["unrealized"], -20.0,
                               places=2, msg=doc["totals"])


if __name__ == "__main__":
    unittest.main()

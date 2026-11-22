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
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj
from _support.planning_toml import _ago, _toml

from taxjson.lib.json_contract import SCHEMAS

# The --json contract's number of each command's document (lib/json_contract).
_SCHEMA = {"buy-check": SCHEMAS["buy-check"], "sell-check": SCHEMAS["sell-check"]}


def _expiry(days=400):
    """An option expiry a year or so ahead, as YYMMDD (a Friday)."""
    d = date.today() + timedelta(days=days)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d.strftime("%y%m%d")


class TestOptionAliasBuyCheck(unittest.TestCase):
    """TJ-1000: ticker.map declares GLOBAL SYN<put>.TO ALT<put>.TO (one
    contract, two spellings). The put was sold at a loss 9 days ago.
    buy-check / sell-check of the broker's SYN spelling dropped the
    loss (its row is booked as ALT) and answered SAFE; the ALT spelling
    gave UNSAFE with the re-entry date.

    Formerly: test_fix_1029_r7c_planning.TestOptionAliasBuyCheck"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        ex = _expiry()
        cls.syn = f"SYN{ex}P00001000.TO"
        cls.alt = f"ALT{ex}P00001000.TO"
        (root / "taxjson.toml").write_text(_toml())
        (root / "ticker.map").write_text(f"GLOBAL {cls.syn} {cls.alt}\n")
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 {cls.syn} 1 CAD .2 20 0\n"
            f"BUYSELL {_ago(9)} 10:00:00 {cls.syn} -1 CAD .1 10 0\n")
        tj(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _check(self, cmd, sym):
        r = tj(self.root, cmd, sym, "--json", check=False)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema_version"], _SCHEMA[cmd])
        return r.returncode, doc["results"][0]

    def test_tj1000_buy_check_both_spellings_unsafe(self):
        rc_alt, alt = self._check("buy-check", self.alt)
        self.assertEqual((rc_alt, alt["verdict"]), (1, "UNSAFE"), alt)
        rc, syn = self._check("buy-check", self.syn)
        self.assertEqual((rc, syn["verdict"]), (1, "UNSAFE"), syn)
        self.assertEqual(syn["clears_at"], alt["clears_at"])
        self.assertIsNotNone(syn["last_loss"], syn)
        self.assertEqual(syn["last_loss"]["gain"], -10.0)

    def test_tj1000_sell_check_both_spellings_see_the_loss(self):
        _rc, alt = self._check("sell-check", self.alt)
        _rc2, syn = self._check("sell-check", self.syn)
        self.assertIsNotNone(alt["last_loss"], alt)
        self.assertEqual(syn["last_loss"], alt["last_loss"])
        self.assertEqual(syn["verdict"], alt["verdict"])
        self.assertEqual(syn["clears_at"], alt["clears_at"])


if __name__ == "__main__":
    unittest.main()

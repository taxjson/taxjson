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
from _support.planning_toml import _ago, _toml


class TestHarvestRadarOfAnEarlierDay(unittest.TestCase):
    """TJ-1009: 10 taxable SYN.TO at a loss of 100; the RRSP bought 5
    shares 40 days ago and 5 more 24 days ago. The saved radar, newer
    than the books but computed 16 days ago (both buys then inside the
    window), says LOCKED with 10 units at risk; today only 5 are.
    The gaps are 24 and 8 days at the radar, 40 and 24 today: a settlement
    moves a date by at most 5 days (a Christmas week), so no date of the
    year carries a buy across the 30-day window (TJ-1360, TJ-1406).
    harvest took the saved 10 and said nothing was claimable now.

    Formerly: test_fix_1029_r7c_planning.TestHarvestRadarOfAnEarlierDay"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml(sheltered=True))
        for acct, rows in (
                ("margin", f"BUYSELL {_ago(120)} 10:00:00 SYN.TO 10 CAD "
                           f"20 200 0\n"),
                ("rrsp", f"BUYSELL {_ago(40)} 10:00:00 SYN.TO 5 CAD 10 50 0\n"
                         f"BUYSELL {_ago(24)} 10:00:00 SYN.TO 5 CAD 10 50 0\n")):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(rows)
        tj(root, "run", "--no-input")
        old = tj(root, "wash-radar", "--date", _ago(16), "--json")
        cls.old_doc = json.loads(old.stdout)
        sidecars = list((root / "reports").glob("wash_radar_*.json"))
        assert sidecars, "no radar sidecar"
        for f in sidecars:
            f.write_text(json.dumps(cls.old_doc))
        (root / "work" / ".price_cache.json").write_text(json.dumps(
            {"SYN.TO": {"price": 10, "currency": "CAD",
                        "quote_symbol": "SYN.TO",
                        "asof": date.today().isoformat()}}))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj1009_saved_radar_said_ten(self):
        rows = [r for sec in self.old_doc["sections"] for r in sec["rows"]
                if r["ticker"] == "SYN.TO"]
        self.assertEqual((rows[0]["category"], rows[0]["at_risk_qty"]),
                         ("LOCKED", 10.0))

    def test_tj1009_harvest_counts_todays_units(self):
        r = tj(self.root, "harvest", "--no-ibkr", "--json", check=False)
        doc = json.loads(r.stdout)
        self.assertAlmostEqual(doc["totals"]["harvestable"]["now"], 50.0,
                               places=2, msg=doc["totals"])


if __name__ == "__main__":
    unittest.main()

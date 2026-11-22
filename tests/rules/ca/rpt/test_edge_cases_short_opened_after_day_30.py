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
from pathlib import Path

from _qa_project import tj
from _support.planning_toml import _toml


class TestEdgeShortOpenAfterDay30(unittest.TestCase):
    """TJ-1014: the whole SYN.TO position sold at a loss on June 2; flat
    until a new SHORT sale on July 3 (day 31). edge-cases explained that
    sale as "after day 30, so those units were still held when the
    window closed" beside HELD DAY 30 = 0. A control that bought 10 on
    July 1 and sold them July 3 keeps that explanation.

    Formerly: test_fix_1029_r7c_planning.TestEdgeShortOpenAfterDay30"""

    _HEAD = ("BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 20 -200 0\n"
             "BUYSELL 2025-06-02 10:00:00 SYN.TO -10 CAD 10 100 0\n")
    _TAIL = "BUYSELL 2025-07-03 10:00:00 SYN.TO -10 CAD 10 100 0\n"

    def _edges(self, extra):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "taxjson.toml").write_text(_toml(year=2025))
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(
                self._HEAD + extra + self._TAIL)
            tj(root, "run", "--no-input")
            r = tj(root, "edge-cases", "--json")
            text = tj(root, "edge-cases", "--details").stdout
            return json.loads(r.stdout)["window_edges"], text

    def test_tj1014_short_open_is_not_a_day30_holding(self):
        we, text = self._edges("")
        self.assertEqual(len(we), 1, we)
        self.assertEqual(we[0]["held_at_day30"], 0.0)
        items = [i for i in we[0]["items"] if i["date"] == "2025-07-03"]
        self.assertEqual(len(items), 1, items)
        it = items[0]
        self.assertEqual(it["kind"], "short sale", it)
        self.assertNotIn("still held", it["why"])
        self.assertIn("opens a short", it["why"])
        self.assertNotIn("still held when the window closed", text)

    def test_tj1014_control_sale_of_replacements_keeps_its_why(self):
        we, _text = self._edges(
            "BUYSELL 2025-07-01 10:00:00 SYN.TO 10 CAD 10 -100 0\n")
        self.assertEqual(we[0]["held_at_day30"], 10.0)
        sale = [i for i in we[0]["items"] if i["date"] == "2025-07-03"]
        self.assertEqual([i["kind"] for i in sale], ["sale"], sale)
        self.assertEqual(sale[0]["qty"], -10.0)
        self.assertIn("after day 30, so those units were still held",
                      sale[0]["why"])


if __name__ == "__main__":
    unittest.main()

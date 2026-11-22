"""The 2026-10-29 fix round, planning commands: buy-check, sell-check,
rebuy, wash-radar, harvest and wash-sales (backlog TJ-0350, TJ-0351,
TJ-0352, TJ-0243, TJ-0245, TJ-0247, TJ-0244, TJ-0246, TJ-0258,
TJ-0259, TJ-0413, TJ-0157). Synthetic data only: made-up tickers
(SYN.TO, ONE.TO, TWO.TO, ZZU.US) in throwaway projects.

Formerly: test_fix_1029_planning"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar_main, _row


class TestClaimedWashedTitle(unittest.TestCase):
    """TJ-0246: the US project of TJ-0023 (a ZZG.US loss on 2026-10-20,
    30 bought back 10-22, the sale claimed by a .tt ALLOWLOSS line).
    `wash-radar --details` titled its section "WASHED — wash sale
    (§1091): loss disallowed; no sale undoes it" over a row saying the
    books allow the loss.

    Formerly: test_fix_1029_planning.TestClaimedWashedTitle"""

    _BOOK = [_row("2026-08-03", "ZZG.US", 60, 50.0, currency="USD"),
             _row("2026-10-20", "ZZG.US", -60, 45.0, currency="USD"),
             _row("2026-10-22", "ZZG.US", 30, 45.0, currency="USD")]

    def _run(self, claimed, *extra):
        with tempfile.TemporaryDirectory() as d:
            args = list(extra)
            if claimed:
                lo = Path(d) / "loss_overrides.json"
                lo.write_text(json.dumps([{
                    "account": "margin", "date": "2026-10-20",
                    "symbol": "ZZG.US", "reason": "synthetic test",
                    "where": "inputs/margin/al.tt:1"}]))
                args += ["--loss-overrides", str(lo)]
            return _radar_main("usa", self._BOOK, as_of="2026-10-25",
                               extra=args)

    @rule(R.US_WASH_25)
    def test_tj0246_a_claimed_section_says_so(self):
        text = self._run(True)
        self.assertIn("WASHED — wash sale (§1091) you claim as your filing "
                      "position (ALLOWLOSS): the books allow the loss (1)",
                      text)
        self.assertNotIn("loss disallowed", text)
        doc = json.loads(self._run(True, "--json"))
        title = {s["category"]: s["title"] for s in doc["sections"]}
        self.assertIn("you claim as your filing position", title["WASHED"])

    @rule(R.US_WASH_25)
    def test_tj0246_a_washed_section_keeps_its_title(self):
        text = self._run(False)
        self.assertIn("WASHED — wash sale (§1091): loss disallowed; no sale "
                      "undoes it (1)", text)


if __name__ == "__main__":
    unittest.main()

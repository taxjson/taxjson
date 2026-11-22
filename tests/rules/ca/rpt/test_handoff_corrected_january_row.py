"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only.

Formerly: test_fix_backlog_handoff"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_CFG24 = {"settings": {"country": "canada", "year": 2024},
          "accounts": {"margin": {"type": "taxable"}}}


def _base(td: str, rows) -> Path:
    work = Path(td) / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "margin_base.json").write_text(json.dumps(
        {"transactions": rows}))
    return work


def _row(i, date, sym, qty, net, action="BUYSELL", settle=None):
    return {"id": i, "action": action, "date": date,
            "date_settle": settle or date, "time": "10:00:00",
            "symbol": sym, "quantity": qty,
            "price": abs(net / qty) if qty else 0.0, "net_amount": net,
            "currency": "CAD", "account": "margin"}


# ------------------------------------------------------------ TJ-0014
class TestCorrectedJanuaryRow(unittest.TestCase):
    """TJ-0014: the broker corrects an early-January fill the closed
    year's inputs held; the row here (same date and quantity, another
    amount) counts in this year: not 'in neither return'.

    Formerly: test_fix_backlog_handoff.TestCorrectedJanuaryRow"""

    def setUp(self):
        self.rec = {"year": 2023, "boundary_rows": [
            {"account": "margin", "action": "BUYSELL",
             "symbol": "ZZQG.TO", "date": "2024-01-03",
             "date_settle": "2024-01-04", "quantity": 10.0,
             "net": 204.95, "tax_date": "2024-01-04"}]}

    @rule(R.CA_RPT_08)
    def test_corrected_amount_is_the_same_row(self):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            work = _base(td, [_row("b1", "2024-01-03", "ZZQG.TO", 10,
                                   209.95, settle="2024-01-04")])
            self.assertEqual(handoff._boundary_issues(
                work, _CFG24, self.rec, "settle", set()), [])
            notes = []
            handoff._boundary_issues(work, _CFG24, self.rec, "settle",
                                     set(), notes)
            self.assertEqual(len(notes), 1)
            self.assertIn("the export was corrected", notes[0])
            self.assertIn("209.95 here, 204.95 when the 2023 project "
                          "read it", notes[0])

    @rule(R.CA_RPT_08)
    def test_a_missing_row_is_still_in_neither_return(self):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            work = _base(td, [_row("b1", "2024-01-05", "ZZQG.TO", 10,
                                   209.95)])
            out = handoff._boundary_issues(work, _CFG24, self.rec,
                                           "settle", set())
            self.assertEqual(len(out), 1)
            self.assertIn("Add it here", out[0]["why"])


if __name__ == "__main__":
    unittest.main()

"""Export coverage, the checklist's line (owner, 2026-10-09;
lib/export_coverage): a broker whose exports stop while it holds
positions is said as "N open position(s) — `taxjson list <account>
<end>` lists them", not a list of symbols, and, when a .tt line or a
later export of the account records the next activity on those
positions, "next recorded activity: <date> (<file>)". The run's Warning
keeps its symbols and says the same. A data check (both countries).
Synthetic exports only.
"""
import tempfile
import unittest
from datetime import date

from test_fix_newuser_signals import (WB_OPEN_CALLS, cfg_of, console, ctx_of,
                                      flat, make, tj, webull)

from taxjson.lib import checklist as cl
from taxjson.lib import export_coverage as EC

_TT = ("BUYSELL 2026-01-05 10:00:00 ZZBB260116C00045000.US "
       "-14 USD 2 2800\n")


class TestNextActivity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "next", 2025, {
            "margin/webull_2025.csv": webull("September 30 2025",
                                             WB_OPEN_CALLS),
            "margin/wb_2026_manual.tt": _TT})
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_checklist_counts_and_names_the_next_activity(self):
        res = cl.d_export_coverage(ctx_of(self.root, 2025,
                                          date(2026, 3, 1)))
        self.assertEqual(res.status, "attention")
        self.assertIn("Webull exports for margin end 2025-09-30 with 1 "
                      "open position(s) — `taxjson list margin "
                      "2025-09-30` lists them; next recorded activity: "
                      "2026-01-05 (wb_2026_manual.tt)", res.detail)
        self.assertNotIn("ZZBB", res.detail)
        self.assertIn("download the rest of the year", res.detail)

    def test_run_warning_says_the_same(self):
        text = flat(console(self.r))
        self.assertIn("Warning: Webull exports for margin end 2025-09-30 "
                      "with open positions (ZZBB260116C00045000.US 14); "
                      "download the rest of 2025", text)
        self.assertIn("`taxjson list margin 2025-09-30` lists the "
                      "positions at the export's end; the next activity "
                      "the books record on them is 2026-01-05 "
                      "(wb_2026_manual.tt).", text)

    def test_record(self):
        g, = EC.find_gaps(self.root, cfg_of(self.root),
                          today=date(2026, 3, 1))
        rec = g.record()
        self.assertEqual(rec["list_command"], "taxjson list margin 2025-09-30")
        self.assertEqual(rec["next_activity"],
                         {"date": "2026-01-05", "file": "wb_2026_manual.tt"})

    def test_the_list_command_lists_them(self):
        r = tj(self.root, "list", "margin", "2025-09-30", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ZZBB260116C00045000.US", r.stdout)


class TestNoLaterActivity(unittest.TestCase):

    def test_no_next_activity_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "none", 2025, {
                "margin/webull_2025.csv": webull("September 30 2025",
                                                 WB_OPEN_CALLS)})
            tj(root, "run", "--no-input")
            res = cl.d_export_coverage(ctx_of(root, 2025, date(2026, 3, 1)))
            g, = EC.find_gaps(root, cfg_of(root), today=date(2026, 3, 1))
        self.assertIn("with 1 open position(s) — `taxjson list margin "
                      "2025-09-30` lists them — download", res.detail)
        self.assertNotIn("next recorded activity", res.detail)
        self.assertIsNone(g.next_activity)

    def test_next_activity_is_the_first_later_row(self):
        rows = [{"action": "DIVIDEND", "date": "2025-11-01",
                 "symbol": "ZZA.TO", "source": "a.csv"},
                {"action": "BUYSELL", "date": "2026-02-01",
                 "symbol": "ZZA.TO", "quantity": -1, "source": "b.tt"},
                {"action": "BUYSELL", "date": "2025-12-01",
                 "symbol": "ZZA.TO", "quantity": -1, "source": "c.csv"},
                {"action": "BUYSELL", "date": "2025-09-01",
                 "symbol": "ZZA.TO", "quantity": 5, "source": "d.csv"},
                {"action": "BUYSELL", "date": "2025-10-01",
                 "symbol": "ZZB.TO", "quantity": -1, "source": "e.csv"}]
        self.assertEqual(EC.next_activity(rows, date(2025, 9, 30),
                                          ["ZZA.TO"]),
                         ("2025-12-01", "c.csv"))
        self.assertIsNone(EC.next_activity(rows, date(2026, 2, 1),
                                           ["ZZA.TO"]))


if __name__ == "__main__":
    unittest.main()

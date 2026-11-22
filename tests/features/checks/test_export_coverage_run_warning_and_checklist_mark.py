"""What a new user cannot know and taxjson did not say (synthetic data):

1. Option grant timing (CA-OPT-11 / US-OPT-07): a contract written before
   `option_grant_timing_since` and closed in the project year is taxed at
   the close — right only if the write year's return did not report the
   premium. The run warns with the premium at stake, option-boundary
   asks, the checklist needs attention; a DONE mark or the
   setting lowered to the write year settles it. Never asked in a US
   project.
2. The cross-listing loss radar (CA-XLIST-05 / US-XLIST-04) judges the
   names of the loss's and the purchase's own rows, not every name the
   project gives a listing; names that differ only in voting-share
   wording are a "possible" pair — never a receipt, a class letter or two
   companies.
3. Per-broker export coverage (lib/export_coverage): a broker whose
   exports end before the year end (today in the running year) while it
   holds positions is a run Warning and the checklist's export-coverage
   step.
4. A sheltered account's corporate-action election is still asked, and
   says there is no tax in the account (its holdings still count for the
   superficial-loss rule).

Formerly: test_fix_newuser_signals
"""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import console, tj

from taxjson.lib import checklist as cl
from taxjson.lib import out
from taxjson.lib import export_coverage as EC
from _support.config_option_grant_projects import (
    flat, make, cfg_of, ctx_of, QT_HEAD, qt_row, WB_HEAD, webull, WB_OPEN_CALLS,
)


# ------------------------------------------------------- 3. export coverage
WB_ALL_CLOSED = (
    'USD,15-01-2025,BUY,@ZZAA,ZZAA HOLDINGS INC,EQ,100,85.00,"(8,500.00)"\n'
    'USD,20-03-2025,SELL,@ZZAA,ZZAA HOLDINGS INC,EQ,100,90.00,"8,995.05"\n')


class TestExportCoverage(unittest.TestCase):
    """Formerly: test_fix_newuser_signals.TestExportCoverage"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "wb", 2025, {
            "margin/webull_2025.csv": webull("September 30 2025",
                                             WB_OPEN_CALLS)})
        cls.r = tj(cls.root, "run", "--no-input", "--details")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_warns_for_the_short_broker(self):
        text = flat(console(self.r))
        self.assertIn("Warning: Webull exports for margin end 2025-09-30 "
                      "with open positions (ZZBB260116C00045000.US 14); "
                      "download the rest of 2025", text)
        self.assertIn("The export's date range ends 2025-09-30.", text)
        self.assertEqual(out.lint(self.r.stdout), [])

    def test_default_console_is_one_line(self):
        # Essentials first: the default console's one line keeps the
        # action (the module's other runs pass --details).
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        r = tj(d, "run", "--no-input")
        self.assertIn("Warning: Webull exports for margin end 2025-09-30 "
                      "with 1 position(s) open: download the rest of 2025\n",
                      r.stdout)
        self.assertNotIn("The export's date range ends", r.stdout)

    def test_checklist(self):
        res = cl.d_export_coverage(ctx_of(self.root, 2025, date(2026, 3, 1)))
        self.assertEqual(res.status, "attention")
        # A question even for the export's own end (v0.24.1 review: the
        # user may know the broker had no later activity); a DONE mark
        # answers this (account, broker, end) only.
        self.assertTrue(res.question)
        self.assertEqual(res.answers, ["margin|webull|2025-09-30"])
        self.assertIn("Webull exports for margin end 2025-09-30", res.detail)
        res, = cl.evaluate(ctx_of(self.root, 2025, date(2026, 3, 1)),
                           only=["export-coverage"])
        self.assertEqual(res.effective, "attention")
        self.assertIn("download the rest of the year", res.detail)

    def test_no_nag_when_every_position_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "closed", 2025, {
                "margin/webull_2025.csv": webull("September 30 2025",
                                                 WB_ALL_CLOSED)})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("exports for margin end", text)
            self.assertEqual(EC.find_gaps(root, cfg_of(root),
                                          today=date(2026, 3, 1)), [])

    def test_full_year_export_is_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "full", 2025, {
                "margin/webull_2025.csv": webull("December 31 2025",
                                                 WB_OPEN_CALLS)})
            tj(root, "run", "--no-input", "--details")
            self.assertEqual(EC.find_gaps(root, cfg_of(root),
                                          today=date(2026, 3, 1)), [])

    def test_last_row_end_is_a_question_a_mark_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = (qt_row("2025-02-03", "2025-02-04", "Buy", "ZZD.TO", 100,
                           20, "CAD", "ZZDELTA CORP")
                    + qt_row("2025-06-02", "2025-06-03", "Buy", "ZZE.TO", 10,
                             20, "CAD", "ZZECHO CORP"))
            root = make(tmp, "qt", 2025, {"margin/questrade_2025.csv":
                                          QT_HEAD + rows})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("Questrade exports for margin end 2025-06-02 with "
                          "open positions (ZZD.TO 100, ZZE.TO 10)", text)
            self.assertIn("The last row is dated 2025-06-02; the export "
                          "itself names no end date.", text)
            ctx = ctx_of(root, 2025, date(2026, 3, 1))
            res = cl.d_export_coverage(ctx)
            self.assertTrue(res.question)
            tj(root, "checklist", "--done", "export-coverage", check=False)
            res, = cl.evaluate(ctx, only=["export-coverage"])
            self.assertEqual(res.effective, "done")
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("Warning: Questrade exports", text)
            self.assertIn("marked done in checklist.json (export-coverage)",
                          text)

    def _year_summary_project(self, tmp, tt):
        """A 2026 project whose Webull file is the 2025 trading summary
        (no 2026 rows) holding 14 calls and 10 ZZCC shares at its end;
        the account's own .tt books what happened in 2026."""
        summary = ("Account Number / Numero de compte:,,,,,,,,12345678,\n"
                   "Year / Annee:,,,,,,,,2025,\n"
                   "Report / Rapport:,,,,,,,,TRADING SUMMARY,\n\n"
                   + WB_HEAD
                   + 'USD,22-04-2025,BUY,@ZZBB,CALL ZZBB01/15/27 45,OPC,14,'
                     '1.55,"(2,184.75)"\n'
                   + 'USD,23-04-2025,BUY,@ZZCC,ZZCC HOLDINGS INC,EQ,10,'
                     '20.00,"(200.00)"\n')
        files = {"margin/webull_2025.csv": summary}
        if tt:
            files["margin/wb_2026_manual.tt"] = tt
        return make(tmp, "ys", 2026, files)

    def test_later_tt_closes_make_it_an_info(self):
        """Positions open at the export's end that the account's own .tt
        lines close later are not missing: an Info, no Warning, the
        checklist step done."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._year_summary_project(
                tmp, "BUYSELL 2026-06-15 10:00:00 ZZBB270115C00045000.US "
                     "-14 USD 2 2800\n"
                     "BUYSELL 2026-06-16 10:00:00 ZZCC.US -10 USD 25 250\n")
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("Warning: Webull exports", text)
            self.assertIn("Info: Webull exports for margin end 2025-12-31; "
                          "the positions open at the export end "
                          "(ZZBB270115C00045000.US, ZZCC.US) were closed by "
                          ".tt lines — no export needed", text)
            g, = EC.find_gaps(root, cfg_of(root), today=date(2026, 10, 7))
            self.assertTrue(g.info)
            res = cl.d_export_coverage(ctx_of(root, 2026, date(2026, 10, 7)))
            self.assertEqual(res.status, "done", res.detail)

    def test_tt_closes_after_the_gap_do_not_cover_it(self):
        """A finished year: the export ends 2025-09-30 and the calls are
        closed by a .tt line dated 2026 — outside the gap (Oct–Dec 2025),
        so a missing 2025 sale would still be missing: a Warning."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "after", 2025, {
                "margin/webull_2025.csv": webull("September 30 2025",
                                                 WB_OPEN_CALLS),
                "margin/wb_2026_manual.tt":
                    "BUYSELL 2026-01-05 10:00:00 ZZBB260116C00045000.US "
                    "-14 USD 2 2800\n"})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("Warning: Webull exports for margin end 2025-09-30 "
                          "with open positions (ZZBB260116C00045000.US 14); "
                          "download the rest of 2025", text)
            self.assertNotIn("were closed by .tt lines", text)
            g, = EC.find_gaps(root, cfg_of(root), today=date(2026, 3, 1))
            self.assertFalse(g.info)

    def test_only_positions_still_open_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._year_summary_project(
                tmp, "BUYSELL 2026-06-15 10:00:00 ZZBB270115C00045000.US "
                     "-14 USD 2 2800\n")
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("Warning: Webull exports for margin end 2025-12-31 "
                          "with open positions (ZZCC.US 10); download the "
                          "rest of 2026", text)
            self.assertIn("The trading summary covers 2025 only.", text)
            self.assertIn("Closed by later .tt lines, not listed: "
                          "ZZBB270115C00045000.US.", text)

    def test_closed_later(self):
        held = [("ZZG.TO", 20.0), ("ZZH.TO", 5.0), ("ZZI.TO", 3.0)]
        later = [
            {"action": "BUYSELL", "date": "2026-02-01", "symbol": "ZZG.TO",
             "quantity": -20, "source": "fix.tt"},
            {"action": "TRANSFER", "date": "2026-02-01", "symbol": "ZZH.TO",
             "quantity": -5, "source": "U_2026.csv"},
            {"action": "BUYSELL", "date": "2026-02-01", "symbol": "ZZI.TO",
             "quantity": -1, "source": "fix.tt"}]
        self.assertEqual(EC.closed_later(held, later),
                         ([("ZZI.TO", 2.0)], ["ZZG.TO"], ["ZZH.TO"]))

    def test_running_year_compares_with_today(self):
        """The year still running: an IB statement ending more than
        GRACE_DAYS before today is short; one ending last week is not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "inputs" / "rrsp").mkdir(parents=True)
            (root / "inputs" / "rrsp" / "U_2026.csv").write_text(
                "Statement,Header,Field Name,Field Value\n"
                'Statement,Data,Period,"January 1, 2026 - September 4, '
                '2026"\n')
            (root / "work" / "rrsp_ib.json").write_text(json.dumps({
                "metadata": {"input_files": ["/x/U_2026.csv"]},
                "transactions": []}))
            (root / "work" / "rrsp_base.json").write_text(json.dumps({
                "transactions": [{"action": "BUYSELL", "date": "2026-03-02",
                                  "symbol": "ZZF.US", "quantity": 10,
                                  "source": "U_2026.csv"}]}))
            cfg = {"settings": {"year": 2026},
                   "accounts": {"rrsp": {"type": "sheltered"}}}
            g, = EC.find_gaps(root, cfg, today=date(2026, 10, 7))
            self.assertEqual((g.broker, g.end, g.how, g.current_year),
                             ("ib", "2026-09-04", "statement", True))
            self.assertEqual(g.positions, [("ZZF.US", 10.0)])
            self.assertEqual(EC.find_gaps(root, cfg,
                                          today=date(2026, 9, 10)), [])

    def test_open_positions(self):
        rows = [
            {"action": "BUYSELL", "date": "2025-01-02", "symbol": "ZZG.TO",
             "quantity": 10},
            {"action": "SPLIT", "date": "2025-02-02", "symbol": "ZZG.TO",
             "quantity": 2},
            {"action": "BUYSELL", "date": "2025-03-02", "symbol": "ZZH.TO",
             "quantity": -5},               # a sale with no purchase
            {"action": "BUYSELL", "date": "2025-03-02",
             "symbol": "ZZI250321C00010000.US", "quantity": 1},
            {"action": "BUYSELL", "date": "2025-03-02",
             "symbol": "ZZI251219P00010000.US", "quantity": -2},
            {"action": "TRANSFER", "date": "2025-04-02", "symbol": "ZZJ.TO",
             "quantity": 3},
            {"action": "TRANSFER", "date": "2025-05-02", "symbol": "ZZJ.TO",
             "quantity": -3},
        ]
        self.assertEqual(EC.open_positions(rows, date(2025, 6, 30)),
                         [("ZZG.TO", 20.0),
                          ("ZZI251219P00010000.US", -2.0)])


if __name__ == "__main__":
    unittest.main()

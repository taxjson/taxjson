"""Owner-reported noise: an answer the user already gave is not a warning.

1. A spin-off whose election writes `fmv_per_share=0` (a warrant
   distributed at no value) is the user's declared $0 cost: an Info line
   with the event id and how to change it — never the run's "$0 cost"
   Warning, find-missing-history's "$0-COST SHARES STILL HELD" section,
   or the checklist's elections ATTENTION. A $0 the broker
   booked with no value saved stays a Warning. Both countries.
2. RBC's / Questrade's look-alike rename hint is not printed when a .tt
   `RENAME <date> OLD NEW` line (or a dated ticker.map line) already
   declares the change: a note names the line instead.

Formerly: test_fix_quietdeclared
"""
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------- 2. a rename already declared

def _rbc_rename_files():
    """RBC's look-alike ticker change (_support.rbc_rows.RQA_ROWS), in CAD:
    RQA stops with shares open, RQB's first row sells them."""
    from _support.rbc_rows import HDR, row
    name = "NORTHWIND CAPITAL CORPORATION COMMON STOCK"
    rows = [row("September 6, 2023", "Sell", "RQB", name, "-1170", "15",
                "17540.05", "CAD", "NORTHWIND CAPITAL CORP UNSOLICITED CA"),
            row("May 30, 2023", "Buy", "RQA", name, "450", "13",
                "-5859.95", "CAD", "SOUTHWIND CAPITAL CORP UNSOLICITED DA"),
            row("April 11, 2023", "Buy", "RQA", name, "720", "13",
                "-9369.95", "CAD", "SOUTHWIND CAPITAL CORP UNSOLICITED DA")]
    return {"rbc.csv": HDR + "".join(rows)}


class TestDeclaredRenameQuietsLookAlikeHint(unittest.TestCase):
    """RBC's (and Questrade's) look-alike rename hint is answered by a .tt
    RENAME line: the parse says a note naming the line, not the
    ATTENTION: nothing on the run console, a note in the .sum.

    Formerly: test_fix_quietdeclared.TestDeclaredRenameQuietsLookAlikeHint"""

    def _run(self, files, tt=None, ticker_map=""):
        from _support.cli_margin_project import _project, _cli_run
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", 2023, files, ticker_map=ticker_map)
            if tt is not None:
                (p / "inputs" / "margin" / "renames.tt").write_text(tt)
            r = _cli_run(p, "run", "--no-input")
            sums = "".join(f.read_text() for f in
                           (p / "reports").rglob("*.sum"))
            diags = "".join(f.read_text() for f in
                            (p / "work").glob("*.diag"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout + r.stderr, sums, diags

    def test_rbc_hint_answered_by_tt_rename(self):
        files = _rbc_rename_files()
        out, _sums, _d = self._run(files)
        self.assertIn("looks renamed", out)       # the hint, unanswered
        tt = "# the ticker change\nRENAME 2023-08-01 RQA.TO RQB.TO\n"
        out, sums, diags = self._run(files, tt)
        for text in (out, sums, diags):
            self.assertNotIn("looks renamed", text)
            self.assertNotIn("ATTENTION: rbc.csv", text)
        # The .sum keeps the answer as a note; the console stays quiet.
        self.assertNotIn("answered by", out)
        self.assertIn("answered by inputs/margin/renames.tt:2", sums)

    def test_rbc_hint_answered_by_dated_map_line(self):
        out, sums, _d = self._run(
            _rbc_rename_files(),
            ticker_map="RENAME RQA.TO RQB.TO 2023-08-01\n")
        self.assertNotIn("looks renamed", out)
        self.assertNotIn("looks renamed", sums)

    def test_questrade_hint_answered_by_tt_rename(self):
        from _support.parser_qt_rbc_rows import QH, q
        body = (q(td="2023-03-03", sd="2023-03-07", sym="QQOL",
                  desc="QQ HOLDINGS CORP WE ACTED AS AGENT", qty="500",
                  price="10", gross="-5000", comm="0", net="-5000",
                  cur="CAD")
                + q(td="2023-09-11", sd="2023-09-13", action="Sell",
                    sym="QQNW", desc="QQ HOLDINGS CORP WE ACTED AS AGENT",
                    qty="-500", price="20", gross="10000", comm="0",
                    net="10000", cur="CAD"))
        files = {"questrade_2023.csv": QH + body}
        out, _s, _d = self._run(files)
        self.assertIn("looks renamed", out)
        out, sums, _d = self._run(files,
                                  "RENAME 2023-09-01 QQOL.TO QQNW.TO\n")
        self.assertNotIn("looks renamed", out)
        self.assertNotIn("looks renamed", sums)
        self.assertIn("answered by inputs/margin/renames.tt:1", sums)


class TestBrokerageDeclaredRenames(unittest.TestCase):
    """taxjson-brokerage --declared-renames (what `tjs run` passes):
    each parser's look-alike hint for a declared pair is a note naming
    the line; another pair, or --lint, keeps the hint.

    Formerly: test_fix_quietdeclared.TestBrokerageDeclaredRenames"""

    def test_each_parser(self):
        from _support.parser_blank_settle_and_renames import _brokerage, QT_RENAME, WB_RENAME
        from _support.parser_qt_rbc_rows import QH
        cases = (("questrade", {"q.csv": QH + QT_RENAME},
                  "QQOL.US\tQQNW.US", "looks renamed"),
                 ("rbc", _rbc_rename_files(), "RQA.TO\tRQB.TO",
                  "looks renamed"),
                 ("webull", {"wb.csv": WB_RENAME}, "QQOL.US\tQQNW.US",
                  "ticker change Webull"))
        with tempfile.TemporaryDirectory() as d:
            for brk, files, pair, hint in cases:
                with self.subTest(broker=brk):
                    f = Path(d) / f"{brk}.list"
                    f.write_text(f"{pair}\tinputs/margin/renames.tt:4\n")
                    rc, err = _brokerage(brk, files, None,
                                         "--declared-renames", str(f))
                    self.assertEqual(rc, 0, err)
                    self.assertNotIn(hint, err)
                    self.assertIn("answered by inputs/margin/renames.tt:4",
                                  err)
                    rc, err = _brokerage(brk, files, None, "--lint",
                                         "--declared-renames", str(f))
                    self.assertIn(hint, err)
                    f.write_text("QZX.US\tQZY.US\tinputs/margin/r.tt:1\n")
                    rc, err = _brokerage(brk, files, None,
                                         "--declared-renames", str(f))
                    self.assertIn(hint, err)

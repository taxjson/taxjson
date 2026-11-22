"""`tjs run`'s console scoped to the tax year (owner feedback from a
new-user run).

* Positions that go short with no purchase in the files are listed one
  by one only when they bear on the project's year — a row of the year
  draws on the missing purchase or touches the position at all, or
  (Canada) another taxable account of its ACB pool trades the symbol
  that year: the test find-missing-history sorts its sections by
  (lib/missing_history.classify_year_shorts). The rest are ONE `Info:`
  line naming `find-missing-history --write-missing-history
  --outside-year`, which records them; the .diag / .sum keep every
  line.
* An engine message is shown once per run whichever pass says it.
* Whole lines in order through a pipe; the checks before the first
  stage under a step; a failed stage's headline repeated in the run's
  last line; the Questrade codes note one code per line.

Every fixture is synthetic: invented tickers, fake account ids (each
line marked pii-ok), small amounts.

Formerly: test_fix_mhscope"""
import io
import os
import shutil
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
from _support.ib_missing_history import _make, _tj


class TestEngineMessagesOnce(unittest.TestCase):
    """The same engine message from two passes (the account's gains, the
    blended pass reading the sheltered context, a failed stage's echo)
    is shown once; the continuation lines follow their message.

    Formerly: test_fix_mhscope.TestEngineMessagesOnce"""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"})
        p.start()
        self.addCleanup(p.stop)
        from taxjson.pipeline import run_state
        p = mock.patch.object(run_state, "current", run_state.RunState())
        p.start()
        self.addCleanup(p.stop)

    def _out(self, fn, *a, **k):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            fn(*a, **k)
        return o.getvalue() + e.getvalue()

    def test_short_keyed_on_symbol_and_account(self):
        from taxjson.pipeline.tools import _echo_captured, _echo_stage_stderr
        a = ("warning: ATTENTION: short: QRG.TO (rrsp): the broker codes "
             "the sale on 2024-04-01 CLOSING (IB code C), but the data "
             "holds no position to close")
        b = ("taxjson-gains: warning: ATTENTION: short: QRG.TO (rrsp): "
             "registered accounts cannot be short — it sells 5 more")
        first = self._out(_echo_captured, a, once=True)
        self.assertIn("Short position: QRG.TO (rrsp)", first)
        self.assertEqual(self._out(_echo_captured, b, once=True), "")
        stage = "\n".join([a, "  a continuation of it",
                           "warning: something else",
                           "  its own continuation"])
        again = self._out(_echo_stage_stderr, stage)
        self.assertNotIn("QRG.TO", again)
        self.assertNotIn("a continuation of it", again)
        self.assertIn("omething else", again)
        self.assertIn("its own continuation", again)
        self.assertEqual(self._out(_echo_stage_stderr, stage), "")


class TestRunConsoleLines(unittest.TestCase):
    """Formerly: test_fix_mhscope.TestRunConsoleLines"""

    def test_pipe_keeps_order_and_first_step(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        # A file that parses to nothing: a stderr warning in the middle
        # of the stdout steps. (Its row is in a section no parser branch
        # reads: a file with no row at all is an Info, TJ-0483.)
        (root / "inputs" / "margin" / "ib_empty.csv").write_text(
            "Statement,Header,Field Name,Field Value\n"
            "Trades,Header,DataDiscriminator,Asset Category,Currency,"
            "Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,Code\n"
            "Zzz Section,Header,Field Name,Field Value\n"
            "Zzz Section,Data,Note,x\n")
        r = _tj(root, "run", "--no-input", merged=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "==> Checking the project")
        w = next(i for i, ln in enumerate(lines)
                 if "ib_empty.csv parsed to 0 transactions — NONE" in ln)
        # (the account's parse is under its first-pass step when every
        # equity account is read first — lib/cross_listings)
        m = next(i for i, ln in enumerate(lines)
                 if ln.startswith("==> margin  (taxable"))
        self.assertGreater(w, m, r.stdout)
        from taxjson.lib import out
        self.assertEqual(out.console_lint(r.stdout), [], r.stdout)

    def test_failed_stage_headline_in_the_last_line(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        (root / "inputs" / "margin" / "bad.tt").write_text(
            "BUYSELL 2025-13-45 10:00:00 QZZ.TO 10 CAD 5 -50 0\n")
        r = _tj(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        last = " ".join(r.stderr.strip().split("\nError: ")[-1].split())
        self.assertTrue(last.startswith("stopped at converting the .tt "
                                        "file for account margin"), last)
        self.assertIn("date '2025-13-45' is not a valid YYYY-MM-DD date",
                      last)
        self.assertIn("details above", last)

    def test_failure_headline_parts(self):
        from taxjson.cli.main import _failure_headline
        self.assertEqual(_failure_headline(
            "note: x\ntaxjson-gains: error: QZA.US: a TRANSFER-in has no "
            "cost in the books — add the original purchase as a .tt "
            "line\n"), "QZA.US: a TRANSFER-in has no cost in the books")
        self.assertEqual(_failure_headline(
            "Traceback (most recent call last):\n  File x\n"
            "ValueError: bad row 3\n"), "bad row 3")
        self.assertEqual(_failure_headline(""), "")
        self.assertEqual(_failure_headline(None), "")


class TestCodesNoteDisplay(unittest.TestCase):
    """Formerly: test_fix_mhscope.TestCodesNoteDisplay"""
    def test_one_code_per_line_captured_unchanged(self):
        from taxjson.lib.stage_msg import console_lines
        from taxjson.lib.symbol_codes import codes_note
        from taxjson.lib.stage_msg import parse_codes_note
        line = codes_note({
            "X000001": {"symbol": "QZM.TO", "evidence":
                        "paired with the Interactive Brokers transfer out "
                        "of 24 on 2026-09-01, account ibm"},
            "X000004": {"symbol": "QZB.TO", "evidence":
                        "name match: 'QZB BANK CORP' on Interactive "
                        "Brokers rows of account ibm"}})
        shown = console_lines(line, "", width_=100)
        self.assertEqual(shown[0], "Info: Questrade internal symbol codes "
                                   "resolved (2):")
        self.assertEqual(shown[1], "X000001 → QZM.TO (Interactive "
                                   "Brokers transfer out of 24 on "
                                   "2026-09-01, ibm)")
        self.assertEqual(shown[2], "X000004 → QZB.TO (same name as "
                                   "'QZB BANK CORP' in ibm)")
        # Captured: the one parseable line, as symbol_codes wrote it.
        self.assertEqual(console_lines(line, "", width_=0), [line])
        self.assertEqual(len(parse_codes_note(line)), 2)


if __name__ == "__main__":
    unittest.main()

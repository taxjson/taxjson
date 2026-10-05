"""Style smoke tests on the synthetic style projects (tests/_style.py),
and the shared helpers of docs/output-style.md: _die/_die_input,
cli_diag's messages, format_report_table(fit=True), and output captured
for a program staying unwrapped.

A group converting a command adds its case here (or in its own test
file): `project(country).run(...)` then `assert_styled(self, text)`."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _style import assert_styled, project

from taxjson.lib import out


class _Width100(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TAXJSON_WIDTH", None)


# ------------------------------------------------- converted commands
class TestConvertedCommands(unittest.TestCase):
    """Every command converted to the house style, both countries."""

    CASES = [("wash-sales",), ("wash-sales", "--explain"), ("elect",),
             ("elect", "margin"),
             # group D: positions, period views, -sum roll-ups, stats,
             # crypto sends
             ("list",), ("list", "--negative"), ("list", "margin"),
             ("shares",), ("shares", "--options"), ("events",),
             ("events", "all"), ("trades",), ("divs",), ("dil",),
             ("fees",), ("gains",), ("gains", "all"), ("leaps",),
             ("roc",), ("transfers",), ("transfers", "margin"),
             ("ccd-sum",), ("dil-sum",), ("divs-sum",), ("fees-sum",),
             ("fees-sum", "--no-by-account"), ("leaps-sum",),
             ("roc-sum",), ("trades-sum",), ("winners",),
             ("winners", "all"), ("stats",), ("stats", "--all-history"),
             ("crypto-sends",),
             # group C
             ("wash-radar",), ("wash-radar", "--date", "2024-11-25", "--all"),
             ("fx-cash",), ("fx-cash", "--events"),
             # group B (tests/test_style_group_b.py has the rest)
             ("sum",), ("sum", "--verbose"), ("form-export",),
             ("carryover",), ("check-filed",)]
    # group E (the rest, exit 1 by design, in tests/test_style_e.py)
    CASES += [("tax-logic",), ("tax-logic", "--ids"), ("edge-cases",),
              ("find-missing-history",), ("renames",), ("spinoffs",),
              ("splits",), ("audit", "--summary"), ("audit", "QZQ.US"),
              ("help",), ("help", "--all"), ("help", "audit"),
              ("channels", "--offline")]

    def test_converted_commands(self):
        for country in ("canada", "usa"):
            p = project(country)
            for args in self.CASES:
                with self.subTest(country=country, args=args):
                    r = p.run(*args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_styled(self, r.stdout)

    def test_pending_election_listing(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = project(country, pending=True).run("elect", "--pending")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout, allow=("taxjson elect ",))
                self.assertTrue(r.stdout.splitlines()[-1].startswith(
                    "1 election pending: "))


# ----------------------------------------------------- shared helpers
class TestDie(_Width100):
    def test_die_is_an_error_headline_with_details(self):
        from taxjson.bin import taxjson_run as R
        with mock.patch.object(R, "_CURRENT_CMD", "sum"), \
                self.assertRaises(SystemExit) as cm:
            R._die("no gains files in work/", "Run `taxjson run` first. "
                   * 12)
        text = str(cm.exception)
        self.assertTrue(text.startswith(
            f"{R._PROG} sum: error: no gains files in work/\n  Run "), text)
        self.assertEqual(out.lint(text), [])

    def test_die_input_exits_2_on_stderr(self):
        from taxjson.bin import taxjson_run as R
        err = io.StringIO()
        with mock.patch.object(R, "_CURRENT_CMD", "opening"), \
                contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            R._die_input("no such file: x.toml")
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(err.getvalue(),
                         f"{R._PROG} opening: error: no such file: x.toml\n")

    def test_cli_diag_messages_keep_their_marker_line(self):
        from taxjson.lib import cli_diag
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cli_diag.warn("taxjson-x", "word " * 40)
            cli_diag.note("taxjson-x", "short")
        lines = err.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("taxjson-x: warning: word"))
        self.assertTrue(lines[1].startswith("  word"))
        self.assertEqual(lines[-1], "taxjson-x: note: short")
        self.assertEqual(out.lint(err.getvalue()), [])


class TestUnwrappedWhenCaptured(_Width100):
    def test_context(self):
        self.assertEqual(out.width(io.StringIO()), 100)
        with out.unwrapped():
            self.assertEqual(out.width(io.StringIO()), 0)
            self.assertEqual(len(out.message("warning", "w " * 80)), 1)
        self.assertEqual(out.width(io.StringIO()), 100)

    def test_a_captured_stage_keeps_whole_lines(self):
        # What a .diag (and so the .sum DIAGNOSTICS) receives: a tool's
        # long error stays one line under the run's capture.
        from taxjson.lib.dispatch import run_cmd
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text('{"transactions": [')
            r = run_cmd([sys.executable, "-m", "taxjson.bin.taxjson_merge",
                         str(bad)], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        refusal = [ln for ln in r.stderr.splitlines()
                   if "refusing to emit" in ln]
        self.assertEqual(len(refusal), 1, r.stderr)
        self.assertIn("partial merge", refusal[0])


class TestFittedReportTable(_Width100):
    ROWS = ["SYMBOL QTY COST DESCRIPTION",
            "QZQ.TO 100 2,401.00 " + "long_text_" * 12,
            "TOTAL - 2,401.00 -"]

    def test_default_is_unchanged(self):
        from taxjson.lib.report_model import format_report_table
        lines = format_report_table(self.ROWS, rule_before_last=True)
        self.assertIn("   ", lines[0])             # the old 3-space gap
        self.assertGreater(max(map(len, lines)), 100)

    def test_fit_drops_then_records(self):
        from taxjson.lib.report_model import format_report_table
        lines = format_report_table(self.ROWS, rule_before_last=True,
                                    fit=True, drop=(3,))
        self.assertEqual(lines[0].split(), ["SYMBOL", "QTY", "COST"])
        self.assertTrue(lines[2].endswith("2,401.00"))
        self.assertTrue(lines[3].startswith("---"))
        rec = format_report_table(self.ROWS, fit=True, width_=40)
        self.assertEqual(rec[0], "QZQ.TO")
        # (an unbreakable 120-character word stands on its own line)
        self.assertTrue(all(len(ln) <= 40 for ln in rec
                            if "long_text" not in ln), rec)
        self.assertIn("TOTAL", rec)


if __name__ == "__main__":
    unittest.main()

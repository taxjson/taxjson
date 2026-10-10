"""Essentials first (docs/output-style.md): the helpers that keep a
command's default view to its essentials — the `! ` act-on line, the
line classifier the budget counts, and the test assertions built on it."""
import io
import os
import unittest
from unittest import mock

import _style
from taxjson.lib import out


class TestActLine(unittest.TestCase):

    def test_act_names_the_command(self):
        self.assertEqual(out.act("2 sales are not in the totals",
                                 "tjs find-missing-history"),
                         "! 2 sales are not in the totals — "
                         "tjs find-missing-history")
        self.assertEqual(out.act("look"), "! look")

    def test_act_escapes_control_characters(self):
        self.assertNotIn("\x1b", out.act("ABC\x1b[31m"))

    def test_details_hint(self):
        self.assertEqual(out.details_hint("tjs sum --details"),
                         "More: tjs sum --details (notes)")


class TestClassify(unittest.TestCase):
    TEXT = "\n".join([
        "REALIZED-GAINS SUMMARY — CAD, tax year 2024",
        "REALIZED = NON-OPT + OPTION; TOTAL = REALIZED + DIVIDEND.",
        "",
        "TAXABLE ACCOUNTS",
        "ACCOUNT  NON-OPT  OPTION",
        "------------------------",
        "margin    100.00    5.00",
        "------------------------",
        "TOTAL     100.00    5.00",
        "",
        "Total gain:      105.00",
        "==> Checking sanity",
        "! 2 sales are not in the totals — tjs find-missing-history",
        "Warning: something to read",
        "More: tjs sum --details (notes)",
    ])

    def test_kinds(self):
        kinds = dict((ln, k) for k, ln in out.classify(self.TEXT))
        self.assertEqual(kinds["TAXABLE ACCOUNTS"], "heading")
        self.assertEqual(kinds["REALIZED-GAINS SUMMARY — CAD, tax year "
                               "2024"], "heading")
        self.assertEqual(kinds["margin    100.00    5.00"], "table")
        self.assertEqual(kinds["ACCOUNT  NON-OPT  OPTION"], "table")
        self.assertEqual(kinds["Total gain:      105.00"], "figure")
        self.assertEqual(kinds["==> Checking sanity"], "step")

    def test_prose_lines_are_the_budget(self):
        self.assertEqual(out.prose_lines(self.TEXT), [
            "REALIZED = NON-OPT + OPTION; TOTAL = REALIZED + DIVIDEND.",
            "! 2 sales are not in the totals — tjs find-missing-history",
            "Warning: something to read",
            "More: tjs sum --details (notes)"])
        self.assertEqual(len(out.act_lines(self.TEXT)), 1)

    def test_assert_concise(self):
        _style.assert_concise(self, self.TEXT, budget=4,
                              legend="REALIZED = NON-OPT")
        with self.assertRaises(AssertionError):
            _style.assert_concise(self, self.TEXT, budget=3)
        with self.assertRaises(AssertionError):
            # Not directly above a table.
            _style.assert_legend_first(self, self.TEXT, "More: tjs")
        with self.assertRaises(AssertionError):
            _style.assert_concise(self, "! " + "x" * 120, budget=6)


class TestOneLineMessages(unittest.TestCase):
    """A default view's engine and stage messages: one line each, the
    third of a kind folded into a count line (out.concise_show)."""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"})
        p.start()
        self.addCleanup(p.stop)
        out._FOLD.clear()

    def test_truncate(self):
        self.assertEqual(out.truncate("short", 100), "short")
        t = out.truncate("word " * 40, 50)
        self.assertLessEqual(len(t), 50)
        self.assertTrue(t.endswith(" ..."))
        self.assertEqual(out.truncate("x " * 80, 0), "x " * 80)

    def test_fold_after_two_of_a_kind(self):
        f = io.StringIO()
        for sym, amt in (("ABC.TO", "12.50"), ("DEF.TO", "7.00"),
                         ("GHI.TO", "3.25"), ("JKL.TO", "1.00")):
            out.concise_show(f"Warning: Income year: {sym}: distribution "
                             f"{amt} CAD paid 2025-01-03 is income of "
                             f"2024", f)
        out.concise_show("Info: something else entirely", f)
        out.fold_summary(f)
        lines = f.getvalue().splitlines()
        self.assertEqual(len(lines), 4, lines)
        self.assertIn("ABC.TO", lines[0])
        self.assertIn("DEF.TO", lines[1])
        self.assertEqual(lines[2], "Info: something else entirely")
        self.assertTrue(lines[3].startswith('Info: 2 more like "Warning: '
                                            'Income year: ABC.TO'), lines[3])
        self.assertTrue(all(len(ln) <= 100 for ln in lines))
        self.assertEqual(out._FOLD, {})


if __name__ == "__main__":
    unittest.main()

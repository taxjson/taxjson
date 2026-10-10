"""House output style (docs/output-style.md) for audit, checklist,
edge-cases, check-dates, sanity, find-missing-history, renames, spinoffs,
splits, tax-logic, opening, redact, channels and help — the commands whose
exit is not 0 on the synthetic style projects, and the lines `taxjson
checklist` reads from them (they must stay whole and unchanged when the
output is captured, TAXJSON_WIDTH=0)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _style import assert_styled, project

from taxjson.lib import out


def _flat(text):
    return " ".join(text.split())


class TestStyledNonZeroExits(unittest.TestCase):
    """Exit 1 is the finding (an impossible date, a position difference,
    an open step); the layout is checked all the same."""

    def test_check_dates(self):
        r = project("canada").run("check-dates")
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_styled(self, r.stdout)
        self.assertTrue(r.stdout.startswith("CHECK DATES — "))
        self.assertIn("ERRORS — impossible dates (1)", r.stdout)
        self.assertEqual(r.stdout.splitlines()[-1],
                         "1 date(s) cannot be right; fix the source file "
                         "or the parser.")

    def test_sanity_keeps_its_checklist_lines(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = project(country)
                r = p.run("sanity", "margin=holdings.toml")
                self.assertEqual(r.returncode, 1, r.stderr)
                assert_styled(self, r.stdout)
                self.assertNotIn("-> ", r.stdout.split("\n", 3)[2])
                self.assertRegex(r.stdout, r"\n  file: +holdings\.toml\n")
                # Captured (the checklist shows the last line): the
                # `! ` line by default; with --details (what the
                # checklist runs) the last paragraph whole on one line.
                c = p.run("sanity", "margin=holdings.toml",
                          TAXJSON_WIDTH="0")
                self.assertTrue(c.stdout.splitlines()[-1].startswith(
                    "! "), c.stdout)
                c = p.run("sanity", "margin=holdings.toml", "--details",
                          TAXJSON_WIDTH="0")
                last = c.stdout.splitlines()[-1]
                self.assertEqual(last, {
                    "canada": "Fewer shares in taxjson than at the broker "
                              "usually means missing history: purchases "
                              "from before your download starts, or shares "
                              "transferred in. See `taxjson "
                              "find-missing-history`, `taxjson transfers` "
                              "and docs/getting-started.md step 5. A trade "
                              "after your last export is the other usual "
                              "cause.",
                    "usa": "Not compared: QZQ.US (the report states no "
                           "cost)"}[country])

    def test_checklist(self):
        r = project("canada").run("checklist")
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_styled(self, r.stdout)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith(
            "CHECKLIST — tax year 2024 (canada): "))
        self.assertIn("1. SET UP", lines)
        self.assertIn("2. GET YOUR FILES", lines)
        self.assertTrue(lines[-1].startswith("Next (step "), lines[-1])
        # A step's detail wraps under its title, not under the mark.
        i = next(k for k, ln in enumerate(lines)
                 if "missing-history" in ln and "[!]" in ln)
        col = lines[i].index("No position")
        self.assertTrue(lines[i + 1].startswith(" " * col + "2 position"))

    def test_redact_check(self):
        root = Path(__file__).resolve().parent.parent
        r = project("canada").run(
            "redact", "--check", str(root / "examples" /
                                     "questrade_demo.csv"))
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_styled(self, r.stdout)
        self.assertIn("  Info: free-text Description columns", r.stdout)
        self.assertNotIn("NOTE:", r.stdout)

    def test_opening_dry_run(self):
        r = project("usa").run("opening", "margin", "holdings.toml",
                               "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith(
            "Error: holdings.toml does not say which "
            "day"), r.stderr)
        assert_styled(self, r.stderr)


class TestChecklistReadsTheNewLayout(unittest.TestCase):
    """The checklist's parsers of find-missing-history and audit read the
    restyled reports to the same verdicts."""

    def test_missing_history_and_audit_steps(self):
        r = project("canada").run("checklist", "--only", "missing-history",
                                  "--json")
        step = json.loads(r.stdout)["steps"][0]
        self.assertEqual(step["status"], "attention")
        self.assertEqual(step["detail"],
                         "2 position(s) with missing basis affect 2024: "
                         "GHOSTQ.TO (margin), OLDCO.US (margin)")
        r = project("canada").run("checklist", "--only", "audit", "--json")
        step = json.loads(r.stdout)["steps"][0]
        self.assertEqual((step["status"], step["detail"]),
                         ("done", "14 disposition(s) tied"))

    def test_tie_out_line_stays_whole_when_captured(self):
        r = project("usa").run("audit", TAXJSON_WIDTH="0")
        self.assertRegex(r.stdout, r"\n  pipeline tie-out +\d+ tied, 0 "
                                   r"MISMATCHED, 0 not found  ")
        self.assertNotIn("# ===", r.stdout)       # the report layout
        self.assertIn("  Lot history (basis trace)", r.stdout)


class TestRenderers(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TAXJSON_WIDTH", None)

    def test_tax_logic_wraps_at_the_house_width(self):
        from taxjson.lib.tax_logic import render, sections
        text = render("canada", {"year": 2025}, ids=True)
        self.assertEqual(out.lint(text), [])
        self.assertGreater(max(map(len, text.splitlines())), 88)
        # The rule text is unchanged: every statement, flattened.
        flat = _flat(text)
        for _t, rules in sections("canada", {"year": 2025}, ids=True):
            for r in rules:
                self.assertIn(_flat(r), flat)
        self.assertEqual(len(render("canada", {}, width=0).splitlines()),
                         len(render("canada", {}, width=0).split("\n")))

    def test_missing_history_to_fix_is_a_numbered_list(self):
        # The fix steps: --details (docs/output-style.md).
        r = project("usa").run("find-missing-history", "--details")
        self.assertIn("\nTo fix a sale with no purchase in your files, in "
                      "this order:\n  1. Add an older export", r.stdout)
        self.assertIn("\n  3. Only when it cannot be recovered:", r.stdout)
        # The checklist's rows: symbol and account first, under AFFECTS.
        sec = r.stdout.split("AFFECTS 2024", 1)[1].split("\n\n", 1)[0]
        rows = [ln.split()[:2] for ln in sec.splitlines()[3:]
                if ln and not ln[0].isspace()]
        self.assertEqual(rows, [["GHOSTQ.US", "margin"],
                                ["OLDCO.US", "margin"]])

    def test_edge_cases_headings_are_short(self):
        # Every section, the empty ones too: --details.
        r = project("usa").run("edge-cases", "--details")
        heads = [ln for ln in r.stdout.splitlines()
                 if ln and ln == ln.upper() and ln[0].isalpha()]
        self.assertIn("LONG CALLS BOUGHT INSIDE A SHARE LOSS'S WINDOW (0)",
                      heads)
        self.assertTrue(all(len(h) <= 70 for h in heads), heads)
        self.assertNotIn("WARNING", r.stdout)

    def test_channels_cut_the_entry_to_the_width(self):
        from taxjson.lib.channels import render
        st = {"online": True, "source_kind": "", "source": "x",
              "channels_file": None, "channels": {"stable": "v0.1.0"},
              "this_box": {"installed": False, "dir": "/p"},
              "problems": [], "total": 1,
              "releases": [{"tag": "v0.1.0", "date": "2026-01-01",
                            "marks": ["stable"],
                            "subject": "word " * 30 + "(+3 more)"}]}
        text = render(st, width_=80)
        self.assertEqual(out.lint(text, 80), [])
        self.assertTrue(text.splitlines()[-1].endswith("… (+3 more)"))


if __name__ == "__main__":
    unittest.main()

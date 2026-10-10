"""Essentials first (docs/output-style.md) for the check commands:
sanity, find-missing-history, slip-audit, edge-cases, close-year, ...
Each default view keeps the budget on the synthetic style projects
(tests/_style.py) at TAXJSON_WIDTH=100 — at most six non-table lines,
the legend above its table, every `! ` line within 100 columns — and
`--details` still prints what the default view leaves out."""
import shutil
import unittest
from pathlib import Path

from _style import Project, assert_concise, project

W = {"TAXJSON_WIDTH": "100"}


def _run(country, *args):
    return project(country).run(*args, **W)


def _flat(text):
    return " ".join(text.split())


class TestSanity(unittest.TestCase):

    def test_budget_and_details(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = _run(country, "sanity", "margin=holdings.toml")
                self.assertEqual(r.returncode, 1, r.stderr)
                assert_concise(self, r.stdout, r.stderr,
                               legend="ISSUE: MISSING_IN_HOLDINGS")
                self.assertIn("! ", r.stdout)
                self.assertIn("tjs find-missing-history", r.stdout)
                self.assertNotIn("Fewer shares in taxjson", r.stdout)
                d = _run(country, "sanity", "margin=holdings.toml",
                         "--details")
                self.assertEqual(d.returncode, 1, d.stderr)
                self.assertIn("discrepancy(ies).", d.stdout)
                self.assertIn("Fewer shares in taxjson than at the broker",
                              _flat(d.stdout))
                self.assertIn("not included", d.stdout)
        d = _run("usa", "sanity", "margin=holdings.toml", "--details")
        self.assertIn("Not compared: QZQ.US", d.stdout)


class TestFindMissingHistory(unittest.TestCase):

    def test_budget_and_details(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = _run(country, "find-missing-history")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr,
                               legend="PeakShort: the deepest short")
                # The checklist's rows stay: symbol and account first.
                self.assertRegex(r.stdout, r"\nAFFECTS 2024 - ")
                self.assertRegex(r.stdout, r"\nOLDCO\.US +margin ")
                self.assertIn("--write-purchases", r.stdout)
                self.assertNotIn("To fix a sale", r.stdout)
                d = _run(country, "find-missing-history", "--details")
                for frag in ("To fix a sale with no purchase in your files",
                             "broker says closing (IB code C)",
                             "Walk-through: docs/getting-started.md"):
                    self.assertIn(frag, _flat(d.stdout))


class TestSlipAudit(unittest.TestCase):

    def test_budget_and_details(self):
        r = _run("canada", "slip-audit")
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_concise(self, r.stdout, r.stderr)
        self.assertIn("tjs slip-audit --template", r.stdout)
        self.assertIn("margin: Canadian dividends 50.00 CAD", r.stdout)
        d = _run("canada", "slip-audit", "--details")
        for frag in ("type them into inputs/slips/slips.toml",
                     "from (no input file), ib_demo.csv",
                     "explain each difference and mark the checklist's "
                     "t5-t3 step done", "Annual average USD/CAD"):
            self.assertIn(frag, _flat(d.stdout))


class TestEdgeCases(unittest.TestCase):

    def test_budget_and_details(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = _run(country, "edge-cases")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr,
                               legend="DEFERRED: denied losses")
                self.assertNotIn("(0)", r.stdout)
                self.assertIn("tjs edge-cases --details", r.stdout)
                d = _run(country, "edge-cases", "--details")
                for frag in ("HOW THE WINDOW IS COUNTED",
                             "of denied losses sits in the cost",
                             "`taxjson handoff`", "(0)"):
                    self.assertIn(frag, _flat(d.stdout))


class TestCloseYear(unittest.TestCase):

    def _copy(self, country):
        p = project(country)
        w = Project(Path(str(p.root) + "_concise"), country)
        shutil.rmtree(w.root, ignore_errors=True)
        shutil.copytree(p.root, w.root)
        self.addCleanup(shutil.rmtree, w.root, True)
        return w

    def test_budget_and_details(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                w = self._copy(country)
                r = w.run("close-year", "--force", **W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)
                self.assertIn("Realized:", r.stdout)
                self.assertNotIn("check-filed` now guards", r.stdout)
                d = w.run("close-year", "--force", "--details", **W)
                self.assertEqual(d.returncode, 0, d.stderr)
                self.assertIn("`taxjson check-filed` now guards it",
                              _flat(d.stdout))
                self.assertIn("handoff checks its inputs against them",
                              _flat(d.stdout))


if __name__ == "__main__":
    unittest.main()

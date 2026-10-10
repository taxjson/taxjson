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
                r = w.run("close-year", "--yes", "--force", **W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)
                self.assertIn("Realized:", r.stdout)
                self.assertNotIn("check-filed` now guards", r.stdout)
                d = w.run("close-year", "--yes", "--force", "--details", **W)
                self.assertEqual(d.returncode, 0, d.stderr)
                self.assertIn("`taxjson check-filed` now guards it",
                              _flat(d.stdout))
                self.assertIn("handoff checks its inputs against them",
                              _flat(d.stdout))


class TestTickerMapSuggest(unittest.TestCase):

    def test_style_projects(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = _run(country, "ticker-map", "--suggest")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)

    def test_suggestions_and_details(self):
        import tempfile
        import test_fix_cross_listings as XT
        from test_fix_ticker_map_suggest import _tjs
        with tempfile.TemporaryDirectory() as tmp:
            root = XT._projects(
                tmp, tail="GLOBAL SAMPZ.TO SAMPY.TO\n",
                in_desc="SAMPQ ENERGY INC PFD SER 2 TRANSFER")["canada"]
            self.assertEqual(_tjs(root, "run", "--no-input").returncode, 0)
            r = _tjs(root, "ticker-map", "--suggest")
            self.assertEqual(r.returncode, 0, r.stderr)
            assert_concise(self, r.stdout, r.stderr)
            self.assertLess(r.stdout.index("Lines for ticker.map"),
                            r.stdout.index("TOBASE SAMPQ.TO"))
            self.assertIn("\nTOBASE SAMPQ.TO SAMPR.TO\n", r.stdout)
            self.assertIn("tjs ticker-map --suggest --write", r.stdout)
            self.assertNotIn("Add a line only when", r.stdout)
            d = _tjs(root, "ticker-map", "--suggest", "--details")
            for frag in ("Add a line only when it is right for your "
                         "securities", "(stock rows, option roots"):
                self.assertIn(frag, _flat(d.stdout))


class TestYears(unittest.TestCase):

    def test_table_and_details(self):
        import test_fix_multi_year as M
        top = M.multi("canada", years=(2024, 2025))
        for y in (2024, 2025):
            M.run_ok(self, top / str(y))
        r = M.tjs("-C", str(top / "2024"), "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        (top / "2024" / "ticker.map").write_text("GLOBAL ABCX.US ABCX.TO\n")
        import subprocess
        import sys
        from _style import env

        def years(*a):
            return subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "years",
                 *a], cwd=top, capture_output=True, text=True,
                env=env(**W), timeout=900, stdin=subprocess.DEVNULL)
        r = years()
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, r.stderr,
                       legend="INPUTS: changed since")
        self.assertRegex(r.stdout, r"\n2024 +filed ")
        self.assertIn("! Inputs changed since the last run of 2024 — "
                      "tjs -C 2024 run", r.stdout)
        self.assertIn("tjs years --diff 2024 2025", r.stdout)
        d = years("--details")
        self.assertIn("rechecks the filed figures against the lock",
                      _flat(d.stdout))


class TestCheckDates(unittest.TestCase):

    def test_budget_and_details(self):
        r = _run("canada", "check-dates")
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_concise(self, r.stdout, r.stderr)
        self.assertIn("NOTES: 7 (settle-cycle 6, income-weekend 1) — tjs "
                      "check-dates --details", r.stdout)
        self.assertTrue(r.stdout.rstrip().splitlines()[-1].startswith("! "))
        d = _run("canada", "check-dates", "--details")
        self.assertIn("NOTES — information (7)", d.stdout)
        self.assertIn("standard cycle 2024-11-06", d.stdout)


class TestUpdateTobaseMap(unittest.TestCase):

    def test_budget_and_details(self):
        r = _run("canada", "update-tobase-map")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, r.stderr)
        self.assertIn("tjs update-tobase-map --write", r.stdout)
        self.assertNotIn("The installed interlisted master", r.stdout)
        d = _run("canada", "update-tobase-map", "--details")
        self.assertIn("The installed interlisted master", d.stdout)
        self.assertIn("they apply when you trade such a security",
                      _flat(d.stdout))


class TestOptionBoundary(unittest.TestCase):

    def test_budget_and_details(self):
        r = _run("canada", "option-boundary")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, r.stderr, legend="KIND: how it")
        self.assertIn("No amended return is required", r.stdout)
        d = _run("canada", "option-boundary", "--details")
        self.assertIn("=> if assigned in a later year", _flat(d.stdout))
        self.assertIn("No filed-year locks (run `taxjson close-year`",
                      _flat(d.stdout))


class TestSmallViews(unittest.TestCase):
    """The views already within the budget stay there."""

    def test_budget(self):
        for country in ("canada", "usa"):
            for args in (("journals",), ("renames",), ("spinoffs",),
                         ("splits",), ("handoff",), ("years",)):
                with self.subTest(country=country, args=args):
                    r = _run(country, *args)
                    assert_concise(self, r.stdout, r.stderr)


if __name__ == "__main__":
    unittest.main()

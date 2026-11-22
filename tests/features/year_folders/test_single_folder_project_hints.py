"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
manifest.json, compare's exit codes (tests/test_compare.py).

Formerly: test_workflow_audit_fixes"""
import tempfile
import unittest
from datetime import date

from _qa_project import console, project, tj
from _support.qt_trades import QT_ROWS


class TestSingleFolderProject(unittest.TestCase):
    """Formerly: test_workflow_audit_fixes.TestSingleFolderProject"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "single",
                           {"margin/questrade_2024.csv": QT_ROWS})
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_years_and_new_year_point_at_migrate(self):
        r = tj(self.root, "years")
        self.assertIn("single-folder project", r.stdout)
        self.assertIn("tjs migrate --to-years", r.stdout)
        self.assertNotIn("tjs new-year", r.stdout)
        r = tj(self.root, "years", "--details")
        self.assertIn("migrate --to-years", r.stdout)
        r = tj(self.root, "new-year", "2025", check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("single-folder project", r.stderr)
        self.assertIn("migrate --to-years", r.stderr)

    def test_no_empty_manifest_is_written(self):
        self.assertFalse((self.root / "inputs" / "margin"
                          / "manifest.json").exists())
        self.assertEqual(self.r.returncode, 0)

    def test_sanity_without_holdings_names_the_unchecked(self):
        r = tj(self.root, "sanity", check=False)
        self.assertEqual(r.returncode, 0, console(r))
        # The books hold no open position: none is unchecked (positions,
        # as the compare counts them), and a single-folder project has
        # no holdings/README.txt to point at (backlog TJ-0135).
        self.assertIn("No account holds a position in the books, and no "
                      "holdings file — nothing to compare with", r.stdout)
        self.assertNotIn("README", r.stdout)

    def test_audit_is_blocked_on_a_pending_election(self):
        from taxjson.lib import checklist as cl
        import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        pend = self.root / "work" / "margin_pending_elections.json"
        pend.write_text('{"pending": []}')
        try:
            ctx = cl.Ctx(root=self.root, cfg=cfg, year=2024,
                         today=date(2025, 3, 1),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            res = cl.d_audit(ctx)
            self.assertEqual(res.status, "blocked", res.detail)
            self.assertIn("corp-action elections pending for margin", res.detail)
            r = tj(self.root, "audit", "--summary", check=False)
            self.assertIn("corp-action elections pending for margin",
                          console(r))
            self.assertNotIn("MISMATCHED", r.stdout.split("tie-out")[-1][:40]
                             if "tie-out" in r.stdout else "")
        finally:
            pend.unlink()


if __name__ == "__main__":
    unittest.main()

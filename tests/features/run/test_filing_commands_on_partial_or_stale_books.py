"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from _support.filing_run_cli import _cli
from _support.tt_cash_project import _tt_project


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


if __name__ == "__main__":
    unittest.main()


# ----------------------------------------------------- end-to-end books
class TestPartialBooksRefused(unittest.TestCase):
    """S006-03, S045-21, S006-07: a taxable account whose stage failed
    is not silently left out of t1135 / form-export / close-year.

    Formerly: test_fix_filing_b.TestPartialBooksRefused"""

    def test_filing_commands_refuse_partial_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, cash="BUYSELL 2025-13-45 10:00:00 YYY.TO "
                                   "100 CAD 30.00 -3000.00 0.00\n")
            r = _cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((root / "work" / "margin_base.json").exists())
            for cmd in ("t1135", "form-export", "close-year"):
                r = _cli(root, cmd)
                self.assertNotEqual(r.returncode, 0, (cmd, r.stdout))
                self.assertIn("cash", r.stderr, cmd)
                self.assertIn("did not build", " ".join(r.stderr.split()), cmd)
            self.assertFalse((root / "filed").exists())
            # sum refuses too, before any figure (TJ-4347: before, a banner
            # over the books' rows).
            s = _cli(root, "sum")
            self.assertEqual(s.returncode, 1, s.stdout)
            self.assertEqual(s.stdout, "")
            self.assertIn("`tjs run`", s.stderr)


class TestCorruptWorkFile(unittest.TestCase):
    """R1-277, S004-09, S005-05, S007-04, S005-04, S046-19.

    Formerly: test_fix_filing_b.TestCorruptWorkFile"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _tt_project(cls.root, instalments=True)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _truncate(self, name):
        p = self.root / "work" / name
        keep = p.read_bytes()
        p.write_bytes(keep[:len(keep) // 2])
        self.addCleanup(p.write_bytes, keep)

    def test_truncated_gains_file_fails_filing_commands(self):
        self._truncate("margin_gains_wash.json")
        for cmd in ("sum", "estimate", "instalments", "t1135",
                    "form-export", "close-year"):
            r = _cli(self.root, cmd)
            self.assertNotEqual(r.returncode, 0, (cmd, r.stdout[-500:]))
            self.assertIn("margin_gains_wash.json", r.stderr, cmd)
            self.assertNotIn("Traceback", r.stderr, cmd)
        self.assertFalse((self.root / "filed").exists())

    def test_truncated_native_file_fails_fx_cash(self):
        self._truncate("margin_raw.json")
        r = _cli(self.root, "fx-cash")
        self.assertNotEqual(r.returncode, 0)
        s = _cli(self.root, "sum")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("FX-on-cash (line 15300) estimate omitted", s.stderr)


class TestRunStateSurfaced(unittest.TestCase):
    """R1-252, S048-21, S048-23, S049-11: books older than the inputs
    (a failed rerun) are flagged by the report commands and not
    locked.

    Formerly: test_fix_filing_b.TestRunStateSurfaced"""

    def test_changed_inputs_banner_and_lock_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            (root / "inputs" / "margin" / "m2.tt").write_text(
                "BUYSELL 2025-04-10 10:00:00 QQQ.TO 10 CAD 1.00 -10.00 "
                "0.00\n")
            r = _cli(root, "carryover")
            self.assertIn("not the clean result", r.stderr)
            # sum and t1135 refuse them, as the filing commands do, before
            # any figure (owner decision 2026-11-18, TJ-4347: before, the
            # banner over their figures, and `run_state_problems` in --json).
            for argv in (("sum",), ("sum", "--json"), ("t1135",), ("t1135", "--json")):
                r = _cli(root, *argv)
                self.assertEqual(r.returncode, 1, (argv, r.stdout[-500:]))
                self.assertEqual(r.stdout, "", argv)
                self.assertIn("older than the inputs", r.stderr, argv)
                self.assertIn("Run `tjs run` first.", r.stderr, argv)
            r = _cli(root, "close-year", "--yes")
            self.assertEqual(r.returncode, 1)
            self.assertIn("inputs changed", r.stderr)
            self.assertNotIn("--force", r.stderr)          # it cannot help
            # --force does not lock them, and form-export refuses them
            # (backlog TJ-4305; before it, --force locked them).
            for argv in (("close-year", "--yes", "--force"), ("form-export",)):
                r = _cli(root, *argv)
                self.assertEqual(r.returncode, 1, (argv, r.stdout[-500:]))
                self.assertIn("older than the inputs", r.stderr, argv)
            self.assertFalse((root / "filed").exists())

    @rule(R.CA_ACB_03)
    def test_unblended_books_are_not_locked(self):
        """S046-01: only `run --account` ran — no blended pass."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            for a in ("margin", "cash"):
                self.assertEqual(
                    _cli(root, "run", "--account", a,
                         "--no-input").returncode, 0)
            r = _cli(root, "close-year", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unblended", r.stderr)


class TestRunCleanCount(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestRunCleanCount"""
    def test_one_error_is_counted_once(self):
        """R1-252 side note: <acct>.sum and <acct>_wash.sum both carry
        the account's validation line."""
        from datetime import date
        from taxjson.lib.checklist import Ctx, d_run_clean
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reports").mkdir()
            (root / "work").mkdir()
            for n in ("margin.sum", "margin_wash.sum"):
                (root / "reports" / n).write_text(
                    "validation: 1 error(s)\nvalidation: 1 error(s)\n")
            cfg = {"settings": {"year": 2025}, "accounts": {}}
            r = d_run_clean(Ctx(root, cfg, 2025, date.today(),
                                lambda *a, **k: (0, "", "")))
        self.assertIn("1 validation error(s)", r.detail)

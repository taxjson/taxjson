"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import tempfile
import unittest
from datetime import date

from _support.tt_fx_project import _cli, _tt_project
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ---------------------------------------------------------------- A2-0040
class TestConflictingSplitsAreErrors(unittest.TestCase):
    """Two SPLIT rows for one event with different ratios are both
    applied (x6). One of them is false: it is a validation ERROR, so
    the console says so, `run --strict` stops and checklist run-clean
    is not done.

    Formerly: test_fix_a2_pipecmd.TestConflictingSplitsAreErrors"""

    def _proj(self, td):
        root, home = _tt_project(td, [
            "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 10 -1000 0",
            "SPLIT 2026-03-02 09:30:00 XYZ.TO XYZ.TO 2",
            "BUYSELL 2026-06-03 10:00:00 XYZ.TO -200 CAD 6 1200 0"])
        (root / "inputs" / "m" / "manual.tt").write_text(
            "SPLIT 2026-03-02 09:30:00 XYZ.TO XYZ.TO 3\n")
        return root, home

    def test_strict_refuses_and_console_names_it(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
            self.assertIn("validation ERROR", r.stderr + r.stdout)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("validation ERROR", out)
            self.assertIn("conflicting split", out)
            import tomllib
            from taxjson.lib import checklist as cl
            cfg = tomllib.loads((root / "taxjson.toml").read_text())
            ctx = cl.Ctx(root=root, cfg=cfg, year=2026, today=date.today(),
                         run_sub=lambda *a, **k: (0, "", ""))
            res = cl.d_run_clean(ctx)
            self.assertNotEqual(res.status, "done", res.detail)
            self.assertIn("validation error", res.detail)

    def test_same_ratio_twice_is_still_only_a_warning(self):
        from taxjson.bin.taxjson_merge2 import warn_duplicate_splits
        from taxjson.model.transaction import TaxTransaction
        import contextlib, io
        rows = [TaxTransaction(action="SPLIT", date="2026-03-02",
                               time="09:30:00", symbol="XYZ.TO",
                               symbol_new="XYZ.TO", quantity=2.0,
                               account="m", currency="CAD")
                for _ in range(2)]
        conflicts = []
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(warn_duplicate_splits(rows, conflicts), 1)
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()

"""Backlog group `errors` (2026-10-25): error messages, atomic writes and
the smaller commands (elect, fetch --list, slips, t1135, tax-logic).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account 99900001.

Formerly: test_fix_backlog_errors
"""
import tempfile
import unittest
from pathlib import Path


class TestReconcileSlipsNoSlipYet(unittest.TestCase):
    """TJ-0046: with no slip saved the t5008 step is to do, not
    flagged: the refusal names a plain `--done t5008`, no --force and no
    'skip' wording.

    Formerly: test_fix_backlog_errors.TestReconcileSlipsNoSlipYet"""

    def test_message(self):
        import contextlib
        import io
        from taxjson.bin import taxjson_reconcile_slips as RS
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                RS.main([str(Path(d) / "inputs" / "slips" / "*.csv"),
                         "--gains", str(Path(d) / "g.json"),
                         "--country", "canada"])
            self.assertEqual(cm.exception.code, 2)
            text = " ".join(err.getvalue().split())
            self.assertIn("nothing to reconcile yet", text)
            self.assertIn("`tjs checklist --done t5008`", text)
            self.assertNotIn("--force", text)
            self.assertNotIn("skip", text)


if __name__ == "__main__":
    unittest.main()

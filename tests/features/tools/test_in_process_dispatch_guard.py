"""Re-audit-2 fixes: one-line errors and consistent exit codes
(errors-01 / errors-03 / errors-04 lists, errA area).

Synthetic data only.

Formerly: test_fix_a2_errA
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from taxjson.lib import cli_diag
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _gains_doc(rows=None, inv=None):
    return {"transactions": rows if rows is not None else [
        {"symbol": "ZZQ.TO", "date": "2025-03-03", "qty": 10,
         "proceeds": 120.0, "cost": 100.0, "gain": 20.0,
         "currency": "CAD", "account": "margin"}],
        "inventory": inv or []}


class TestDispatchGuard(unittest.TestCase):
    """A2-0161 / A2-1417: `tjs <cmd>` runs a tool's main under the
    console script's guard, in process and out of process.

    Formerly: test_fix_a2_errA.TestDispatchGuard"""

    def _cmd(self, mod, *args):
        return [sys.executable, "-m", f"taxjson.bin.{mod}", *map(str, args)]

    def test_unreadable_input_in_process_is_one_line(self):
        from taxjson.pipeline.dispatch import run_cmd
        mod = types.ModuleType("taxjson.bin._zz_errA_unreadable")

        def main():
            raise cli_diag.InputReadError("ticker.map: not UTF-8 text")
        mod.main = main
        sys.modules[mod.__name__] = mod
        try:
            r = run_cmd(self._cmd("_zz_errA_unreadable"), capture_output=True)
        finally:
            del sys.modules[mod.__name__]
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("error: ticker.map: not UTF-8 text", r.stderr)

    def test_broken_pipe_in_process_is_not_a_traceback(self):
        from taxjson.pipeline.dispatch import run_cmd
        mod = types.ModuleType("taxjson.bin._zz_errA_pipe")

        def main():
            raise BrokenPipeError(32, "Broken pipe")
        mod.main = main
        sys.modules[mod.__name__] = mod
        try:
            r = run_cmd(self._cmd("_zz_errA_pipe"), capture_output=True)
        finally:
            del sys.modules[mod.__name__]
        self.assertEqual(r.returncode, cli_diag.BROKEN_PIPE_EXIT)
        self.assertNotIn("Traceback", r.stderr)

    def test_reconcile_slips_main_is_guarded(self):
        # `tjs reconcile-slips` calls this main directly.
        from taxjson.bin import taxjson_reconcile_slips as RS
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains.json"
            g.write_text(json.dumps(_gains_doc()))
            s = Path(td) / "slips.csv"
            s.write_text("symbol,quantity,proceeds\nZZQ.TO,10,120\n")
            m = Path(td) / "ticker.map"
            m.write_bytes(b"GLOBAL Z\xe9Q.TO ZZQ.TO\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                RS.main([str(s), "--gains", str(g), "--country", "canada",
                         "--ticker-map", str(m)])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("not UTF-8", err.getvalue())

    def test_subprocess_mode_goes_through_the_trampoline(self):
        from taxjson.pipeline.dispatch import run_cmd
        with tempfile.TemporaryDirectory() as td:
            loop = Path(td) / "loop.json"
            loop.symlink_to("loop.json")
            os.environ["TAXJSON_DISPATCH"] = "subprocess"
            try:
                r = run_cmd(self._cmd("taxjson_gains", "--country", "canada",
                                      loop), capture_output=True)
            finally:
                del os.environ["TAXJSON_DISPATCH"]
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("taxjson-gains: error: cannot read", r.stderr)


if __name__ == "__main__":
    unittest.main()

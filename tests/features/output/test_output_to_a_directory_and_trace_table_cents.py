"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support.tt_fx_project import REPO_ROOT, _env
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ---------------------------------------------------------------- A2-0707
class TestOutputToADirectory(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestOutputToADirectory"""

    def test_convert_tt_to_a_directory(self):
        with tempfile.TemporaryDirectory() as td:
            tt = Path(td) / "hand.tt"
            tt.write_text("BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 "
                          "-100 0\n")
            out = Path(td) / "outdir"
            out.mkdir()
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_convert_tt",
                 str(tt), str(out)], cwd=REPO_ROOT, capture_output=True,
                text=True, env=_env(Path(td)), stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(f"cannot write {out}: is a directory", r.stderr)
            self.assertFalse((Path(td) / "outdir.part").exists())

    def test_write_text_atomic_leaves_no_part(self):
        from taxjson.lib.cli_diag import OutputWriteError, write_text_atomic
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing" / "x.json"
            with self.assertRaises(OutputWriteError) as cm:
                write_text_atomic(p, "{}")
            self.assertIn("cannot write", str(cm.exception))
            ok = Path(td) / "y.json"
            write_text_atomic(ok, "{}")
            self.assertEqual(ok.read_text(), "{}")
            self.assertFalse((Path(td) / "y.json.part").exists())


# ---------------------------------------------------------------- A2-0708
class TestTraceAndTableRoundAlike(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestTraceAndTableRoundAlike"""

    def test_recomputed_noise_rounds_like_the_saved_value(self):
        from taxjson.lib.report_fmt import fmt_money
        from taxjson.lib.trace_format import _fmt_money, _fmt_signed_money
        # 530.425 is an exact half at 2 places: shown cuts it up since step
        # 6h(4) (round() gave 530.42), and the trace's formats denoise the
        # recomputed 530.4250000000001 to that half first, so the three
        # still agree.
        self.assertEqual(fmt_money(530.425), "530.43")
        self.assertEqual(_fmt_money(530.4250000000001), "$530.43")
        self.assertEqual(_fmt_signed_money(530.4250000000001), "+$530.43")


if __name__ == "__main__":
    unittest.main()

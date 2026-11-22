"""Fix round 7 (wave F) of 2026-10-30, the closecash group: regressions of
wave E's repairs. A broker sale of units the coverage replay cannot see, a
transit whose arrival settled in the year, a later rebuy at another
broker and an INKIND line check-tt cannot size yet (synthetic data only).

Formerly: test_fix_1029_r7f_closecash"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from _support.qt_close import _QH, _project, _row

from _support.ib_option_head import _SYM, _IB_HEAD


class TestTJ1288InKindSizeNotKnownYet(unittest.TestCase):
    """TJ-1288 (a regression of TJ-1163's fix): check-tt called a valid
    INKIND line refused when only the broker's transfer row, not yet read
    by a run, states the contract size.

    Formerly: test_fix_1029_r7f_closecash.TestTJ1288InKindSizeNotKnownYet"""

    def _ib_project(self, td, value="2 20"):
        root = _project(Path(td), {"margin": "taxable", "plan": "sheltered"})
        (root / "inputs/margin/trades.tt").write_text(
            f"BUYSELL 2025-01-02 09:30:00 {_SYM} 1 CAD 1 10 0 x10\n"
            f"INKIND 2025-06-03 {_SYM} -1 CAD {value} plan=tfsa\n")
        (root / "inputs/margin/ib.csv").write_text(
            _IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
            "SYN 19DEC25 10 C,2025-06-03,ACATS,Out,Other Broker,99900002,-1,0,"
            "-20,0,0,\n")
        (root / "inputs/plan/ib.csv").write_text(
            _IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
            "SYN 19DEC25 10 C,2025-06-03,ACATS,In,Other Broker,99900002,1,0,"
            "20,0,0,\n")
        return root

    def test_not_an_error_before_the_run_reads_the_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("may state another contract size",
                          r.stdout + r.stderr)
            r = tj(root, "checklist", "--run", "--only", "tt-lines", "--json",
                   check=False)
            self.assertEqual(json.loads(r.stdout)["counts"]["attention"], 0)
            tj(root, "run", "--no-input")
            r = tj(root, "check-tt")
            self.assertNotIn("contract size", r.stdout + r.stderr)

    def test_a_line_of_no_size_is_refused_once_the_run_read_the_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._ib_project(td, value="2 77")
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = tj(root, "run", "--no-input", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("price and total disagree", r.stderr)
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_a_broker_that_states_no_size_stays_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable"})
            (root / "inputs/margin/trades.tt").write_text(
                f"INKIND 2025-06-02 {_SYM} -1 CAD 2 2000 plan=tfsa\n")
            (root / "inputs/margin/qt.csv").write_text(
                _QH + _row("2025-06-02", "TF6", _SYM, -1, 0, 0,
                           kind="Transfers", desc="SYN TRANSFER"))
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_a_line_the_run_does_not_read_is_not_refused(self):
        # The run reads the INKIND lines of taxable accounts only.
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered"})
            (root / "inputs/plan/t.tt").write_text(
                f"INKIND 2025-06-03 {_SYM} -1 CAD 2 2000 plan=tfsa\n")
            r = tj(root, "check-tt", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tj(root, "run", "--no-input")


if __name__ == "__main__":
    unittest.main()

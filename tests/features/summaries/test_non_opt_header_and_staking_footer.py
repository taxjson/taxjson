"""Re-audit-2 test pins (tests-pins-04/05/06, reports group): labels and
accumulators that held at the audit but that no test failed on when
reverted. Each test names the finding and the mutant it kills.

  A2-0880 / A2-1585  fees-sum's per-brokerage 'non-option' line, its
                     $/UNIT header and 'Non-option vs option fees:'
                     heading; `tjs sum`'s NON-OPT column; the tx
                     views' 'TOTAL STAKING (crypto, ordinary income):'
  A2-1555            fx-cash's per-currency ACQUIRED column sums
  A2-0533            `tjs sum`'s summarize_gains / summarize_income
                     accumulators (2+ rows per ticker and term, 2
                     withholding rows, an 'other' row, all-zero rows)
  A2-0861 / A2-0906 / A2-0907
                     run_gains: the Canadian stock-dividend line and the
                     Canadian option summary keys stay out of a US book;
                     no note for a plain purchase; fees after a non-trade
                     row; by_ticker after a tainted row and with two
                     dividends; a manual-reporting row has no gain/cost
  A2-0865 / A2-0866 / A2-1532
                     checklist: a stale wash file makes audit and
                     form-export 'attention'; the t5-t3 step names the
                     TAXABLE line; the US slip and margin-interest words

All data is synthetic.

Formerly: test_fix_a2_pins2_reports
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


# --------------------------------------------------- run views and `sum`
def _cli(root, *args):
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               NO_COLOR="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=300)


class TestRunReportLabels(unittest.TestCase):
    """A2-1585: `tjs sum`'s NON-OPT column header and the staking
    footer of the transaction views.

    Formerly: test_fix_a2_pins2_reports.TestRunReportLabels"""

    def test_staking_footer_names_ordinary_income(self):
        from _support.qt_margin_views import _views_project
        with tempfile.TemporaryDirectory() as tmp:
            root = _views_project(tmp)
            ev = _cli(root, "events")
        self.assertEqual(ev.returncode, 0, ev.stderr)
        self.assertRegex(ev.stdout, r"TOTAL STAKING \(crypto, ordinary "
                                    r"income\):\s+7\.00 CAD")

    def test_sum_header_says_non_opt(self):
        tt = ("BUYSELL 2025-01-10 10:00:00 XYZ.TO 100 CAD 10.00 -1000.00 0.00\n"
              "BUYSELL 2025-06-10 10:00:00 XYZ.TO -100 CAD 12.00 1200.00 0.00\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "margin.tt").write_text(tt)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = _cli(root, "sum")
        self.assertEqual(s.returncode, 0, s.stderr[-2000:])
        header = next(ln for ln in s.stdout.splitlines()
                      if ln.split()[:1] == ["ACCOUNT"])
        self.assertEqual(header.split()[:3], ["ACCOUNT", "NON-OPT", "OPTION"])


if __name__ == "__main__":
    unittest.main()

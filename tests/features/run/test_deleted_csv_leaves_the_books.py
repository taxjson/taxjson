"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros).

Formerly: test_fuzz_fixes"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,"
              "Description,Quantity,Price,Gross Amount,Commission,"
              "Net Amount,Currency,Account #,Activity Type,Account Type\n")


def _qt_row(d, action, sym, qty, price, comm, net):
    return (f"{d} 09:30:00 AM,{d} 12:00:00 AM,{action},{sym},DESC,"
            f"{qty},{price},{abs(qty)*price},{comm},{net},CAD,1,"
            f"Trades,Individual\n")


def _pipeline_project(root, margin_rows, rrsp_rows):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "rrsp").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n'
        '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT_HEADER + "".join(margin_rows))
    (root / "inputs" / "rrsp" / "questrade.csv").write_text(
        _QT_HEADER + "".join(rrsp_rows))


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestFuzzJ_FastSeesDeletions(unittest.TestCase):
    """#J: `run --fast` served stale books when an input CSV was
    DELETED (mtime deps only cover files that exist). A sources
    manifest now bumps on membership change.

    Formerly: test_fuzz_fixes.TestFuzzJ_FastSeesDeletions"""

    def test_deleted_csv_drops_from_books_under_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pipeline_project(
                root,
                [_qt_row("2025-01-15", "Buy", "XEI.TO", 100, 10.0,
                         0.00, -1000.00)],
                [_qt_row("2025-02-03", "Buy", "ZAG.TO", 50, 14.0,
                         0.00, -700.00)])
            extra = root / "inputs" / "margin" / "questrade2.csv"
            extra.write_text(_QT_HEADER + _qt_row(
                "2025-03-01", "Buy", "OTHER.TO", 10, 5.0, 0.00, -50.00))
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base1 = (root / "work" / "margin_base.json").read_text()
            self.assertIn("OTHER.TO", base1)
            extra.unlink()                       # delete the CSV
            r2 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            base2 = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn("OTHER.TO", base2)      # trades gone with it


if __name__ == "__main__":
    unittest.main()

"""An account configured in taxjson.toml but not yet populated with CSVs
(e.g. straight after `taxjson init`) must warn-and-skip, not abort the run.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.bin.taxjson_run import stage_account

_SETTINGS = {"base_currency": "CAD", "country": "canada", "year": 2025,
             "tax_date": "settle"}


def _stage(inputs_dir, name, acfg=None):
    """Call stage_account far enough to hit the empty-account guard, capturing
    stderr. The other path args are unused before the guard returns."""
    cache = inputs_dir.parent / ".cache"
    reports = inputs_dir.parent / "reports"
    err = io.StringIO()
    with redirect_stderr(err):
        out = stage_account(name, acfg or {"type": "sheltered"}, _SETTINGS,
                            inputs_dir, cache, reports, Path("rates.csv"),
                            None, None, False)
    return out, err.getvalue()


class TestSkipEmptyAccount(unittest.TestCase):
    def test_dir_with_no_csvs_warns_and_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs"
            (inputs / "rrsp").mkdir(parents=True)
            # An init-style folder: only a README, no CSVs.
            (inputs / "rrsp" / "README.txt").write_text("drop CSVs here\n")
            out, err = _stage(inputs, "rrsp")
            self.assertIsNone(out)
            self.assertIn("warning", err.lower())
            self.assertIn("rrsp", err)

    def test_missing_dir_warns_and_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs"
            inputs.mkdir()
            out, err = _stage(inputs, "tfsa")
            self.assertIsNone(out)
            self.assertIn("warning", err.lower())
            self.assertIn("tfsa", err)

    def test_does_not_raise_systemexit(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs"
            inputs.mkdir()
            try:
                _stage(inputs, "margin", {"type": "taxable"})
            except SystemExit:
                self.fail("empty account must not abort the run with SystemExit")


if __name__ == "__main__":
    unittest.main()

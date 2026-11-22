"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, export,
some parsers and taxjson-sum-income.

Formerly: test_fix_a2_errA_d"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = _paths.REPO
ENV = _hermetic.environ(TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
                        NO_COLOR="1", TAXJSON_WIDTH="0")    # captured output

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')


def tj(root, *args):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=ENV, stdin=subprocess.DEVNULL)


def no_tb(tc, r):
    tc.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
                else:
                    p.chmod(0o644)
            except OSError:
                pass
        self._td.cleanup()


class TestGenericSidecar(unittest.TestCase):
    """A2-0801 (already fixed by A2-1081; pinned), A2-1452.

    Formerly: test_fix_a2_errA_d.TestGenericSidecar"""

    CSV = ("Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"
           "2024-11-04,BUY,XEI,50,20.00,-1009.95,9.95,CAD\n")

    def test_non_string_date_format(self):
        from _support.generic_parse import _parse, _toml
        for bad in ("7", "[1]", "{a=1}", "true"):
            for key in ("date", "settle"):
                with self.assertRaisesRegex(
                        ValueError, rf"\[formats\]\.{key} must be a "
                                    r"quoted string"):
                    _parse(self.CSV, _toml(extra=f"[formats]\n{key} = "
                                                 f"{bad}\n"))

    def test_non_utf8_mapping_named(self):
        from _support.generic_parse import _toml
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "generic_ws.csv"
            c.write_text(self.CSV)
            c.with_name(c.name + ".toml").write_bytes(
                _toml().encode() + b"# \xff\xfe\n")
            with self.assertRaisesRegex(ValueError,
                                        r"generic_ws\.csv\.toml: not UTF-8"):
                GenericBrokerage().parse_file(c)


class TestTtBareEquitySymbol(_Tmp):
    """A2-0777: a suffix-less symbol in an equity account is said.

    Formerly: test_fix_a2_errA_d.TestTtBareEquitySymbol"""

    def make(self, crypto=False):
        (self.root / "taxjson.toml").write_text(
            TOML + ("crypto = true\n" if crypto else ""))
        d = self.root / "inputs" / "margin"
        d.mkdir(parents=True)
        (d / "hand.tt").write_text(
            "BUYSELL 2025-02-03 10:00:00 QZMS.TO 10 CAD 400 4001 1\n"
            "BUYSELL 2025-06-03 10:00:00 QZMS -5 CAD 420 2099 1\n")
        return d / "hand.tt"

    def test_warned_in_equity_account(self):
        tt = self.make()
        r = tj(self.root, "run", "--no-input")
        no_tb(self, r)
        # The channel of the unknown-suffix warning (S028-19): the
        # stage .diag and the .sum DIAGNOSTICS.
        self.assertIn("symbol QZMS has no market suffix",
                      (self.root / "reports" / "margin.sum").read_text())
        self.assertTrue(tt.exists())

    def test_not_warned_in_crypto_account(self):
        import contextlib
        import io
        from taxjson.lib.tt_format import tt_to_json
        tt = self.make(crypto=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tt_to_json(tt, "margin")
        self.assertNotIn("no market suffix", err.getvalue())

    def test_outside_project_not_warned(self):
        import contextlib
        import io
        from taxjson.lib.tt_format import tt_to_json
        tt = self.root / "x.tt"
        tt.write_text("BUYSELL 2025-02-03 10:00:00 QZMS 10 USD 400 4001 1\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tt_to_json(tt, "default")
        self.assertNotIn("no market suffix", err.getvalue())


if __name__ == "__main__":
    unittest.main()

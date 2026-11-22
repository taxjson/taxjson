"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import unittest


# ------------------------------------------------------------- TJ-0242

class _NoisyYahoo:
    """A yfinance stand-in that prints and logs as yfinance does on a
    miss, and returns nothing."""

    def _noise(self):
        import logging
        import sys
        print("[*****100%*****]  1 of 1 completed")
        print("1 Failed download: ['ZZQCAD=X']: possibly delisted",
              file=sys.stderr)
        logging.getLogger("yfinance").error("ZZQCAD=X: possibly delisted")

    def download(self, *a, **k):
        self._noise()
        return None

    def Ticker(self, sym):
        outer = self

        class _T:
            def history(self, *a, **k):
                outer._noise()
                raise RuntimeError("no data")
        return _T()


class TestToBaseCurrQuietsYahoo(unittest.TestCase):
    """TJ-0242: to_base_curr asked yfinance without silencing it, as the
    price chain does: its raw lines reached the console (and stdout,
    where the program prints its rates).

    Formerly: test_fix_1029_messages.TestToBaseCurrQuietsYahoo"""

    def _quiet(self, fn):
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import to_base_curr as TB
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(TB, "yf", _NoisyYahoo()), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            got = fn(TB)
        return got, out.getvalue(), err.getvalue()

    def test_fetch_yahoo_prints_nothing(self):
        got, out, err = self._quiet(
            lambda TB: TB.fetch_yahoo("ZZQCAD=X", "2025-01-02", "2025-01-03"))
        self.assertEqual(got, {})
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_spot_row_prints_nothing(self):
        got, out, err = self._quiet(lambda TB: TB._spot_row("ZZQ", "USD"))
        self.assertIsNone(got)
        self.assertEqual(out, "")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()

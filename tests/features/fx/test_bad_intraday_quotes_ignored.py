"""Fix round 7 (wave B), FX rates and `tjs audit`: a Yahoo rate is not
forward-filled twice (TJ-0843), a bad intraday quote does not poison
the rates file (TJ-0845), futures converted twice keep the composed
rate (TJ-0745); the audit needs no rate for a split that carries no
money (TJ-0765), traces a record-dated return of capital paid later
(TJ-0766) and finds a listing the run joined (TJ-0773).

Every fetcher is stubbed: no network. Synthetic books and rates only.

Formerly: test_fix_1029_r7b_ratesaudit"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.bin import to_base_curr as T
from taxjson.lib.exchange_rates import load_exchange_rates
from taxjson.bin.taxjson_convert_currency import get_rate_for_date


class _Recent:
    empty = False

    def __init__(self, x):
        self.x = x

    def __getitem__(self, key):
        return SimpleNamespace(iloc=[self.x])


class TestBadIntradayQuoteIsIgnored(unittest.TestCase):
    """TJ-0845: a NaN, zero, negative or infinite intraday close was
    appended to the rates file, which the converter then refused as a
    whole.

    Formerly: test_fix_1029_r7b_ratesaudit.TestBadIntradayQuoteIsIgnored"""

    def _main(self, close):
        fake = SimpleNamespace(Ticker=lambda pair: SimpleNamespace(
            history=lambda **kw: _Recent(close)))
        out, err = io.StringIO(), io.StringIO()
        built = ([("2025-06-02", "1.25", "yahoo", "2025-06-02")], [], [])
        with mock.patch.object(T, "yf", fake), \
                mock.patch.object(T, "build_rates", return_value=built), \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = T.main(["GBP", "USD", "--start", "2025-06-02",
                         "--end", "2025-06-02"])
        return rc, out.getvalue()

    def test_invalid_quotes_leave_the_history_usable(self):
        for close in (float("nan"), 0.0, -1.0, float("inf")):
            with self.subTest(close=close):
                rc, text = self._main(close)
                self.assertEqual(rc, 0)
                self.assertEqual(len(text.splitlines()), 1, text)
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "to_base.csv"
                    p.write_text(text, encoding="utf-8")
                    hist = load_exchange_rates(p, "USD")
                self.assertEqual(str(get_rate_for_date(
                    "GBP", "2025-06-02", hist, None)), "1.25")

    def test_a_valid_quote_is_still_appended(self):
        rc, text = self._main(1.3)
        self.assertEqual(rc, 0)
        self.assertEqual(len(text.splitlines()), 2, text)
        self.assertIn(" GBP USD 1.3 yahoo", text.splitlines()[1])


if __name__ == "__main__":
    unittest.main()

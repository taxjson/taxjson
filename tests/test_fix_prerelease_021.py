"""Pre-release review for v0.21.0 (findings L1, L2, L4 and two small
pre-existing items). The Kraken dust finding (H1, L3) is pinned in
test_fix_kraken_dust.py. Synthetic data only."""
import io
import os
import time
import unittest
from contextlib import redirect_stderr


class TestRewordIsLinear(unittest.TestCase):
    """L1: the run console's display regexes matched a file-name group
    `\\s+(?P<f>.+?)` that could also eat the leading whitespace, so a
    long run of spaces cost quadratic time; a line over 2000 characters
    is now shown as is."""

    def test_long_whitespace_is_fast(self):
        from taxjson.lib.stage_msg import reword
        for n in (1999, 50_000, 500_000):
            line = " " * n + "x"
            t = time.perf_counter()
            self.assertEqual(reword(line), [line])
            self.assertLess(time.perf_counter() - t, 0.5, n)

    def test_long_line_is_not_reworded(self):
        from taxjson.lib.stage_msg import reword
        line = "  " + "a" * 2100 + ".csv: 5 tax objects"
        self.assertEqual(reword(line), [line])

    def test_short_lines_still_reworded(self):
        from taxjson.lib.stage_msg import reword
        self.assertEqual(reword("  kr_l.csv: 251 tax objects"),
                         ["note: kr_l.csv: 251 tax objects"])
        self.assertEqual(
            reword("  a b.csv: 3 TRANSFER row(s) kept aside (custody "
                   "evidence, not tax events — view with `taxjson "
                   "transfers`)"),
            ["note: a b.csv: 3 transfer rows kept aside (not tax events; "
             "`taxjson transfers` lists them)"])


class TestOfflineRatesStep(unittest.TestCase):
    """L2: with TAXJSON_OFFLINE=1 the rates helper reads its cache only;
    the run's step said `Downloading USD → CAD rates` all the same."""

    def _steps(self, offline):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from contextlib import redirect_stdout
        from taxjson.bin import taxjson_run as tr
        out = io.StringIO()
        env = {"TAXJSON_OFFLINE": "1" if offline else "0"}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(tr, "run_capture",
                                  return_value=b"2026-01-02 12:00:00 USD "
                                               b"CAD 1.40 boc\n"), \
                redirect_stdout(out):
            tr.stage_currency_rates({"base_currency": "CAD",
                                     "source_currencies": ["USD"]},
                                    Path(td))
        return out.getvalue()

    def test_offline_says_cached(self):
        got = self._steps(offline=True)
        self.assertIn("Loading cached USD → CAD rates", got)
        self.assertNotIn("Downloading", got)

    def test_online_says_downloading(self):
        self.assertIn("Downloading USD → CAD rates",
                      self._steps(offline=False))


if __name__ == "__main__":
    unittest.main()

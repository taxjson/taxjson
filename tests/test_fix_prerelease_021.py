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


if __name__ == "__main__":
    unittest.main()

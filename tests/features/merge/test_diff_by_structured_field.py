"""Batch C regression pins (slips and misc): findings 42, 83, 98, 99 and
112 of the 2026-10 bug review (finding 82 is pinned in the fetch
plugin's own tests, packages/taxjson-fetch/tests/test_fetch_fix_batch_c.py).

Synthetic data only: made-up tickers (SAMP, ZZQ), issuers and broker
account hashes.

Formerly: test_fix_batch_c_slips
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_diff


class TestDiffStructuredKey(unittest.TestCase):
    """83: `taxjson-diff --by` a list- or dict-valued field.

    Formerly: test_fix_batch_c_slips.TestDiffStructuredKey"""

    def test_unhashable_field_is_a_usable_key(self):
        a = {"action": "BUYSELL", "replacements": [{"id": 2}, {"id": 1}]}
        b = {"action": "BUYSELL", "replacements": [{"id": 2}, {"id": 1}]}
        c = {"action": "BUYSELL", "replacements": []}
        ka, kb, kc = (taxjson_diff.make_key(r, ["replacements"])
                      for r in (a, b, c))
        self.assertEqual(len({ka, kb, kc}), 2)   # hashable; equal values
        self.assertEqual(ka, kb)

    def test_cli_runs(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        doc = {"transactions": [
            {"id": 1, "action": "BUYSELL", "symbol": "ZZQ",
             "wash_replacements": [{"date": "2025-01-02", "qty": 1}]}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text(json.dumps(doc))
            argv = sys.argv
            sys.argv = ["taxjson-diff", "--by", "wash_replacements",
                        str(p), str(p)]
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    try:
                        rc = taxjson_diff.main()
                    except SystemExit as e:
                        rc = e.code
            finally:
                sys.argv = argv
        self.assertIn(rc, (0, None))


if __name__ == "__main__":
    unittest.main()

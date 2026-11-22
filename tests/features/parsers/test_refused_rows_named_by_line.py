"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import os
import tempfile
import unittest
from pathlib import Path

import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------------------- TJ-0124

_EXAMPLES = REPO_ROOT / "examples"
# (parser id, demo file, the first transaction's export line)
_DEMO_FIRST_LINE = (("ib", "ib_demo.csv", 9),
                    ("rbc_direct", "rbc_direct_demo.csv", 6),
                    ("webull", "webull_demo.csv", 7),
                    ("coinbase", "coinbase_demo.csv", 2),
                    ("kraken", "kraken_demo.csv", 2))


def _parse_demo(bid, name, td):
    import contextlib
    import io
    import shutil
    from unittest import mock
    from taxjson.parsers.registry import parser_class
    path = Path(td) / name
    shutil.copy(_EXAMPLES / name, path)
    ex = parser_class(bid)()
    with mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "America/Toronto"}), \
            contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        txs = ex.parse_file(path)
    return ex, txs, path


class TestEveryParserNamesTheRowLine(unittest.TestCase):
    """TJ-0124 (left open): only Questrade and the generic importer
    noted each transaction's export line, so a row the schema check
    refused from IB, RBC, Webull, Coinbase or Kraken was named by a
    masked row id ('row id 25***'), which cannot be searched for.

    Formerly: test_fix_1029_messages.TestEveryParserNamesTheRowLine"""

    def test_each_demo_transaction_has_its_line(self):
        for bid, name, first in _DEMO_FIRST_LINE:
            with self.subTest(parser=bid), \
                    tempfile.TemporaryDirectory() as td:
                ex, txs, path = _parse_demo(bid, name, td)
                n = len(path.read_text(encoding="utf-8").splitlines())
                self.assertTrue(txs)
                lines = [ex.row_line(t) for t in txs]
                self.assertEqual(lines[0], first, lines)
                for ln in lines:
                    self.assertIsNotNone(ln, lines)
                    self.assertTrue(1 < ln <= n, lines)

    def test_schema_error_names_the_ib_line(self):
        import contextlib
        import io
        import sys
        from unittest import mock
        from taxjson.bin import taxjson_brokerage as B
        from taxjson.parsers.registry import parser_class
        cls = parser_class("ib")
        real = cls.parse_file

        def _blank_first_symbol(self, path):
            txs = real(self, path)
            txs[0]["symbol"] = ""
            return txs
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ib_demo.csv"
            path.write_bytes((_EXAMPLES / "ib_demo.csv").read_bytes())
            err = io.StringIO()
            with mock.patch.object(cls, "parse_file", _blank_first_symbol), \
                    mock.patch.object(sys, "argv", [
                        "taxjson-brokerage", "--brokerage", "ib",
                        "--account", "x", "--strict", str(path)]), \
                    mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}), \
                    contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                B.main()
        line = next(x for x in err.getvalue().splitlines()
                    if "missing required field" in x)
        self.assertIn("ib_demo.csv line 9: BUYSELL (no symbol) 2024-02-12",
                      line)
        self.assertNotIn("row id", line)


if __name__ == "__main__":
    unittest.main()

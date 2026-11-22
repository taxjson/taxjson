"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------- TJ-0234/0235/0236

_GEN_MAP = """\
[columns]
date = "Date"
action = "Transaction type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"

[formats]
date = "%m/%d/%Y"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"""
_GEN_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
            "01/15/2025,BUY,ZZD,10,{price},100.00,CAD\n")


def _brokerage(files, broker, name):
    """taxjson-brokerage on `name`, `files` alone in a folder of their
    own: the CompletedProcess."""
    with tempfile.TemporaryDirectory() as td:
        for n, text in files.items():
            (Path(td) / n).write_text(text)
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                   TAXJSON_LOCAL_TZ="America/Toronto")
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", broker, "--account", "x",
                                str(Path(td) / name)]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)


def _error_line(r):
    return next(x for x in r.stderr.splitlines() if "error:" in x)


class TestGenericMappingNameMasked(unittest.TestCase):
    """TJ-0235: a mapping sidecar named for the account
    (99900001.csv.toml) was printed whole in its refusals.

    Formerly: test_fix_1029_messages.TestGenericMappingNameMasked"""

    def test_unknown_key_names_the_masked_mapping(self):
        bad = _GEN_MAP.replace('price = "Price"', 'prices = "Price"')
        r = _brokerage({"99900001.csv": _GEN_CSV.format(price="10.00"),
                        "99900001.csv.toml": bad}, "generic",
                       "99900001.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("99900001", r.stdout + r.stderr)
        self.assertIn("99***.csv.toml: unknown [columns].prices",
                      _error_line(r))

    def test_date_format_refusal_names_the_masked_mapping(self):
        csv_text = _GEN_CSV.format(price="10.00").replace(
            "01/15/2025", "2025-01-15")
        r = _brokerage({"99900001.csv": csv_text,
                        "99900001.csv.toml": _GEN_MAP}, "generic",
                       "99900001.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("99900001", r.stdout + r.stderr)


# ------------------------------------------------------------- TJ-0207

class TestTtLineRefusalOnce(unittest.TestCase):
    """TJ-0207: a contract size of 0 (or a stray trailing token) was
    refused inside the row parse and wrapped again: "h.tt:1:
    taxjson-convert-tt: malformed .tt line (h.tt:1: contract size …)"
    named the place twice.

    Formerly: test_fix_1029_messages.TestTtLineRefusalOnce"""

    def _refusal(self, line):
        from taxjson.lib.tt_format import parse_tt_line
        with self.assertRaises(ValueError) as cm:
            parse_tt_line(line, "margin", "h.tt:1")
        return str(cm.exception)

    def test_contract_size(self):
        line = "BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD 1 10 0 x0"
        msg = self._refusal(line)
        self.assertTrue(msg.startswith("h.tt:1: contract size 'x0' must "
                                       "be > 0 ("), msg)
        self.assertEqual(msg.count("h.tt:1"), 1, msg)
        self.assertNotIn("malformed .tt line", msg)
        self.assertTrue(msg.endswith(f": {line!r}"), msg)

    def test_trailing_token(self):
        msg = self._refusal(
            "BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD 1 10 0 x100 zz")
        self.assertTrue(msg.startswith("h.tt:1: BUYSELL row has 1 "
                                       "unexpected trailing token(s)"), msg)
        self.assertEqual(msg.count("h.tt:1"), 1, msg)

    def test_a_bad_number_keeps_the_general_wording(self):
        msg = self._refusal("BUYSELL 2024-03-01 10:00:00 ZZQA.US ten USD "
                            "1 10 0")
        self.assertIn("malformed .tt line", msg)


if __name__ == "__main__":
    unittest.main()

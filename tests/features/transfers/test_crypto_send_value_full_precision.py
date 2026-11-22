"""Batch D (options / crypto sends / ALLOWLOSS): `tjs option-yield`
(lib/option_yield.py), the fair value of a crypto send
(lib/crypto_sends.py) and the sales an ALLOWLOSS line names
(lib/loss_overrides.py).

2026-10 bug review findings 37, 47, 81 and 88. Synthetic rows only.

Formerly: test_fix_batch_d_options"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tt_format import parse_tt_line
from taxjson.lib import crypto_sends as CS
from taxjson.lib import base_rates as BR


class CryptoSendFullPrecisionTest(unittest.TestCase):
    """#47: a send's fair value and .tt proceeds come from the
    full-precision price; six digits only for showing it.

    Formerly: test_fix_batch_d_options.CryptoSendFullPrecisionTest"""

    SEND = dict(symbol="SYNCOIN", date="2025-01-02", time="09:30:00",
                quantity=-100, price=123.456789, currency="CAD",
                exchange="coinbase")

    def test_value_and_line(self):
        fv = CS.fair_value(self.SEND, BR.Rates({}, "CAD"), None)
        self.assertAlmostEqual(fv["value"], 12345.68)
        line = CS.tt_line(self.SEND, fv, "CAD")
        self.assertEqual(line, "BUYSELL 2025-01-02 09:30:00 SYNCOIN -100 "
                               "CAD 123.457 12345.68 0")
        # The .tt reader's typo check (shown price x units, 1%) takes it.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = parse_tt_line(line, "coinbase", "crypto_sends.tt:5")
        self.assertNotIn("differs from", err.getvalue())
        self.assertAlmostEqual(tx["net_amount"], 12345.68)

    def test_line_written_before_is_kept(self):
        fv = CS.fair_value(self.SEND, BR.Rates({}, "CAD"), None)
        new = CS.tt_line(self.SEND, fv, "CAD")
        old = new.replace("12345.68", "12345.70")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / CS.TT_NAME
            p.write_text(CS.GENERATED_MARK + "\n" + old + "\n",
                         encoding="utf-8")
            e = {"id": "cb-1", "tt": new}
            CS.keep_written_lines([e], p)
            self.assertEqual(e["tt"], old)
            # A changed price is a changed line: rewritten.
            other = {"id": "cb-2", "tt": new.replace("123.457", "120")}
            want = other["tt"]
            CS.keep_written_lines([other], p)
            self.assertEqual(other["tt"], want)


if __name__ == "__main__":
    unittest.main()

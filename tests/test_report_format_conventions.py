"""Pins the 2026-07 UI-audit axis-B output-format conventions so they
cannot drift back:

  * fmt_money: thousands separators, exactly 2 decimals, '-' negatives;
  * leaps/ccd reports: money columns are 2dp-with-commas (4 decimals are
    reserved for per-share / quantity columns);
  * banners: single title line, no `===` sandwich rules;
  * totals rows: `TOTAL` (not `TOTALS`), no `>>>` prefix;
  * color: sum_gains honors the NO_COLOR env var (color only when stdout
    is a tty AND neither NO_COLOR nor --no-color is set).
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.lib.report_model import fmt_money


class TestFmtMoney(unittest.TestCase):
    def test_thousands_and_two_decimals(self):
        self.assertEqual(fmt_money(1234.5), "1,234.50")
        self.assertEqual(fmt_money(1234567.891), "1,234,567.89")

    def test_negative(self):
        self.assertEqual(fmt_money(-0.5), "-0.50")
        self.assertEqual(fmt_money(-1234.5), "-1,234.50")

    def test_none_and_zero(self):
        self.assertEqual(fmt_money(None), "0.00")
        self.assertEqual(fmt_money(0), "0.00")


def _option_tx(symbol, direction, gain):
    return {
        "date": "2025-03-17", "symbol": symbol, "qty": 2.0,
        "currency": "USD", "direction": direction,
        "cost_per_share": 15.1234, "proceeds_per_share": 21.6234,
        "gain_per_share": 6.5, "cost": 3024.6801, "proceeds": 4324.6801,
        "gain": 1300.0 if direction == "LONG" else gain,
        "days_held": 400,
    }


def _run_tool_main(module, gains):
    """Write `gains` to a temp file, run module.main() on it, return stdout."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "gains.json"
        path.write_text(json.dumps(gains), encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["prog", str(path)]), \
                contextlib.redirect_stdout(buf):
            module.main()
    return buf.getvalue()


class TestLeapsCcdMoneyPrecision(unittest.TestCase):
    """The leaps/ccd money columns (COST/PROCEEDS/GAIN and the totals) used
    to print at 4 decimals; the convention is 2dp with thousands
    separators. Per-share and QTY columns legitimately keep 4 decimals."""

    def _check(self, out, total_label):
        # Money columns render 2dp with separators…
        self.assertIn("3,024.68", out)      # cost (was 3024.6801)
        self.assertIn("4,324.68", out)      # proceeds
        self.assertIn("1,300.00", out)      # gain
        # …and never leak the 4-decimal raw values.
        self.assertNotIn("3024.6801", out)
        self.assertNotIn(".6801", out)
        # Prose total recap: `TOTAL …:` with 2dp, no `>>>` prefix.
        self.assertNotIn(">>>", out)
        m = re.search(rf"^TOTAL AAPL\.US {total_label}: (\S+)$", out, re.M)
        self.assertIsNotNone(m, out)
        self.assertRegex(m.group(1), r"^-?[\d,]+\.\d{2}$")
        # Summary TOTAL row (not TOTALS), 2dp.
        self.assertRegex(out, r"(?m)^TOTAL\s+[\d,]+\.\d{2}$")
        self.assertNotRegex(out, r"(?m)^TOTALS\b")
        # No `===` banner sandwiches anywhere.
        self.assertNotIn("====", out)
        # Per-share columns may keep their 4-decimal precision.
        self.assertIn("15.1234", out)

    def test_leaps_gains_money_is_2dp(self):
        from taxjson.bin import taxjson_leaps_gains
        gains = {"transactions": [
            _option_tx("AAPL270117C00150000.US", "LONG", 1300.0)]}
        out = _run_tool_main(taxjson_leaps_gains, gains)
        self._check(out, "LONG OPTION GAIN")

    def test_ccd_gains_money_is_2dp(self):
        from taxjson.bin import taxjson_ccd_gains
        gains = {"transactions": [
            _option_tx("AAPL270117C00150000.US", "SHORT", 1300.0)]}
        out = _run_tool_main(taxjson_ccd_gains, gains)
        self._check(out, "COVERED CALL GAIN")


class TestSumGainsConventions(unittest.TestCase):
    GAINS = {"transactions": [{
        "action": "SELL", "symbol": "AAPL.US", "currency": "USD",
        "cost": 1000.0, "proceeds": 2534.5, "gain": 1534.5,
        "days_held": 10, "qty": 10,
    }], "summary": {"year": 2025}}

    def _report(self, no_color=False):
        from taxjson.bin.taxjson_sum_gains import (format_report,
                                                   summarize_gains)
        return format_report(summarize_gains(self.GAINS), "ticker", no_color)

    def test_no_color_env_var_honored(self):
        # Even on a tty, NO_COLOR=1 must disable ANSI escapes.
        with mock.patch.object(sys.stdout, "isatty", return_value=True,
                               create=True), \
                mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = self._report()
        self.assertNotIn("\x1b[", out)

    def test_color_on_tty_without_no_color(self):
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with mock.patch.object(sys.stdout, "isatty", return_value=True,
                               create=True), \
                mock.patch.dict(os.environ, env, clear=True):
            out = self._report()
        self.assertIn("\x1b[", out)

    def test_total_row_headers_and_commas(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = self._report(no_color=True)
        self.assertRegex(out, r"(?m)^TOTAL\s")
        # The totals ROW is TOTAL; "CONSOLIDATED TOTALS" (a block
        # title naming several totals) is allowed.
        self.assertNotRegex(out, r"(?m)^TOTALS\b")
        self.assertIn("SYMBOL", out)            # header renamed from TICKER
        self.assertIn("1,534.50", out)          # money with separators
        self.assertNotIn("====", out)           # no banner sandwich


class TestSumIncomeConventions(unittest.TestCase):
    def test_subtotal_and_banner(self):
        from taxjson.bin.taxjson_sum_income import (format_report,
                                                    summarize_income)
        txs = [{"action": "DIVIDEND", "type": "dividend", "date": "2025-05-01",
                "symbol": "AAPL.US", "currency": "USD",
                "gross_amount": 1234.5}]
        out = format_report(summarize_income(txs, 2025))
        self.assertIn("INCOME SUMMARY — USD", out)
        self.assertIn("SUBTOTAL", out)
        self.assertNotIn("SUBTOTALS", out)
        self.assertIn("TOTAL NET INCOME", out)
        self.assertIn("SYMBOL", out)
        self.assertIn("1,234.50", out)
        self.assertNotIn("====", out)


if __name__ == "__main__":
    unittest.main()

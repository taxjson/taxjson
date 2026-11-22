"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, export,
some parsers and taxjson-sum-income.

Formerly: test_fix_a2_errA_d"""
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------------- parsers
class TestIbIncomeTicker(unittest.TestCase):
    """A2-0780: an IB income row with no TICKER(ISIN) token is refused,
    never booked on UNKNOWN.US or a word of the text.

    Formerly: test_fix_a2_errA_d.TestIbIncomeTicker"""

    def parse(self, body):
        from _support.ib_statement_rows import HEAD, _parse_ib
        return _parse_ib(HEAD + body)

    def test_clean_row_booked(self):
        from _support.ib_statement_rows import DIV_H
        _p, txs, _e = self.parse(
            DIV_H + 'Dividends,Data,CAD,2024-02-01,"QZRY (CA9990000017) '
            'CASH DIVIDEND CAD 1.38 PER SHARE",13.80\n')
        self.assertEqual([t["symbol"] for t in txs
                          if t["action"] == "DIVIDEND"], ["QZRY.TO"])

    def test_no_token_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from _support.ib_statement_rows import DIV_H, WHT_H
        for body in (
                DIV_H + 'Dividends,Data,CAD,2024-02-01,,13.80\n',
                DIV_H + 'Dividends,Data,CAD,2024-02-01,"CASH DIVIDEND '
                'CAD 1.38 PER SHARE",13.80\n',
                DIV_H + 'Dividends,Data,CAD,2024-02-01,"qzry (ca9990000017) '
                'cash dividend",13.80\n',
                WHT_H + 'Withholding Tax,Data,USD,2024-02-01,"CASH '
                'DIVIDEND USD 0.24",-1.20\n'):
            with self.assertRaisesRegex(BrokerageParseError,
                                        r"line \d+ .*TICKER \(ISIN\)"):
                self.parse(body)

    def test_interest_withholding_on_cash(self):
        from _support.ib_statement_rows import WHT_H
        _p, txs, _e = self.parse(
            WHT_H + 'Withholding Tax,Data,USD,2024-06-03,"Withholding @ '
            '20% on Credit Interest for MAY-2024",-1.20\n')
        self.assertEqual([(t["action"], t["symbol"]) for t in txs],
                         [("TAX", "CASH")])


WB = ('Webull Securities (Canada) Ltd.\nSynthetic Demo Statement\n'
      'Date Range: January 1 2024 - December 31 2024\n\n'
      '"Currency","Date","Action Code","Symbol","Security Description",'
      '"Type Code","Quantity","Price","Proceeds"\n'
      'USD,15-01-2024,BUY,@AAPL,APPLE INC,EQ,100,185.00,"(18,500.00)"\n')


def wb_parse(text, name="wb_demo.csv", encoding="utf-8"):
    import contextlib
    import io
    from taxjson.lib.brokerages.webull import WebullBrokerage
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(text.encode(encoding) if encoding != "utf-16"
                      else b"\xff\xfe" + text.encode("utf-16-le"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return WebullBrokerage().parse_file(p)


class TestWebullBlankAction(unittest.TestCase):
    """A2-0788.

    Formerly: test_fix_a2_errA_d.TestWebullBlankAction"""

    def test_blank_action_trade_cells_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaisesRegex(BrokerageParseError,
                                    r"line \d+: .*blank Action Code"):
            wb_parse(WB + 'USD,10-05-2024,,@AAPL,APPLE INC,EQ,75,200.00,'
                          '"14,995.05"\n')

    def test_blank_continuation_row_ok(self):
        txs = wb_parse(WB + ',,,,,,,,\n')
        self.assertEqual(len(txs), 1)

    def test_utf16_webull_read(self):
        """A2-0805 (Webull half)."""
        self.assertEqual(len(wb_parse(WB, encoding="utf-16")), 1)


class TestIbNoTzdata(unittest.TestCase):
    """A2-1447: no tz database -> one-line parse error, not a traceback.

    Formerly: test_fix_a2_errA_d.TestIbNoTzdata"""

    def test_missing_zone_is_parse_error(self):
        from unittest import mock
        import zoneinfo
        from taxjson.lib.brokerages import ib_extractor as ib
        from taxjson.lib.brokerages.base import BrokerageParseError

        def boom(name):
            raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found "
                                                 f"with key {name}")
        with mock.patch.object(zoneinfo, "ZoneInfo", boom):
            with self.assertRaisesRegex(BrokerageParseError, "tzdata"):
                ib._ib_market_trade_date("2025-06-30", "09:30:00",
                                         "Stocks", "AUD", "AX", "QZBHP")

    def test_with_zone_ok(self):
        from taxjson.lib.brokerages import ib_extractor as ib
        self.assertEqual(ib._ib_market_trade_date(
            "2025-06-30", "21:30:00", "Stocks", "AUD", "AX", "QZBHP")[0],
            "2025-07-01")


if __name__ == "__main__":
    unittest.main()

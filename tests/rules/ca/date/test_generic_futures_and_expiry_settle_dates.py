"""Re-audit-2 fixes: the generic column-mapped importer.

A2-0030 (cut-off last record), A2-0103/A2-1074/A2-0634 (futures_settle
= "next_day"), A2-0107/A2-0294/A2-0627 ('/' and '\\' futures), A2-0106
(a cash-in amount under a buy action), A2-0299 (a dangling sidecar
mapping), A2-0626/A2-1079 (commission rebates), A2-0628 (an option
expiring on Dec 31), A2-0629 (an exercise/assignment the mapping cannot
express), A2-1075 (a settle date far after the trade), A2-1076 (payment
in lieu), A2-1081 (non-string [defaults]/[formats] values), A2-1083 and
A2-1080 (UTF-16 exports), A2-1085 (the broker account of each row).
Synthetic data only (synthetic account ids, marked pii-ok).

Formerly: test_fix_a2_generic
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.generic_parse import _toml, _parse, _HDR


_FUT = (_HDR + "2025-12-01,BUY,{s},1,6000,-300009.5,9.5,USD\n"
        "2025-12-31,SELL,{s},-1,6010,300490.5,9.5,USD\n")


class TestFuturesSettle(unittest.TestCase):
    """A2-0107/A2-0294/A2-0627 and A2-0103/A2-1074/A2-0634.

    Formerly: test_fix_a2_generic.TestFuturesSettle"""

    @rule(R.CA_DATE_09)
    def test_slash_and_backslash_futures_settle_on_trade_date_ca(self):
        for s in ("/ESZ5", "\\ESZ5", "F:ESZ5"):
            txs, _, _ = _parse(_FUT.format(s=s), _toml())
            self.assertEqual(txs[1]["date_settle"], "2025-12-31", s)
            # One spelling: the F: prefix every other parser uses.
            self.assertEqual(txs[1]["symbol"], "F:ESZ5.US", s)

    @rule(R.US_DATE_09)
    def test_slash_futures_settle_on_trade_date_us(self):
        txs, _, _ = _parse(_FUT.format(s="/ESZ5"), _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")

    @rule(R.CA_DATE_10)
    def test_next_day_setting_reaches_generic_ca(self):
        for s in ("F:ESZ5", "/ESZ5"):
            txs, _, _ = _parse(_FUT.format(s=s), _toml(),
                               futures_settle="next_day")
            self.assertEqual(txs[1]["date_settle"], "2026-01-02", s)
            self.assertEqual(txs[0]["date_settle"], "2025-12-02", s)

    @rule(R.US_DATE_12)
    def test_next_day_setting_reaches_generic_us(self):
        txs, _, _ = _parse(_FUT.format(s="F:ESZ5"), _toml(),
                           futures_settle="next_day")
        self.assertEqual(txs[1]["date_settle"], "2026-01-02")

    def test_taxjson_brokerage_passes_the_setting(self):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        self.assertTrue(hasattr(GenericBrokerage(), "futures_settle"))
        self.assertEqual(GenericBrokerage().futures_settle, "trade")


class TestDec31Expiry(unittest.TestCase):
    """A2-0628: an option closed at $0 on its expiry day is dated and
    settled that day (no T+1); a $0 close posted after the expiry is
    clamped to it.

    Formerly: test_fix_a2_generic.TestDec31Expiry"""

    _CSV = (_HDR + "2025-12-15,BUY,SPY251231C00700000,1,4.10,-414.23,"
            "4.23,USD\n{d},SELL,SPY251231C00700000,-1,0,0,0,USD\n")

    @rule(R.CA_DATE_08)
    def test_expiry_day_close_settles_that_day_ca(self):
        txs, _, _ = _parse(self._CSV.format(d="2025-12-31"), _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")

    @rule(R.US_DATE_08)
    def test_expiry_day_close_settles_that_day_us(self):
        txs, _, _ = _parse(self._CSV.format(d="2025-12-31"), _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")

    @rule(R.CA_DATE_08)
    def test_expiry_posted_next_business_day_clamped(self):
        txs, err, _ = _parse(self._CSV.format(d="2026-01-02"), _toml())
        self.assertEqual(txs[1]["date"], "2025-12-31")
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")
        self.assertIn("expiry", err)

    def test_priced_close_before_expiry_keeps_t1(self):
        txs, _, _ = _parse(
            _HDR + "2025-12-15,BUY,SPY251231C00700000,1,4.10,-414.23,4.23,"
            "USD\n2025-12-30,SELL,SPY251231C00700000,-1,1.00,95.77,4.23,"
            "USD\n", _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")


class TestLateSettle(unittest.TestCase):
    """A2-1075: a mapped settle date far after the trade.

    Formerly: test_fix_a2_generic.TestLateSettle"""

    _TOML = _toml(extra_cols='settle = "Settle"\n')
    _H = "Date,Settle,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"

    @rule(R.CA_DATE_06)
    @rule(R.US_DATE_06)
    def test_settle_a_year_late_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(self._H + "2025-12-15,2026-12-16,BUY,XYZ,1,10,-10,0,"
                   "CAD\n", self._TOML)
        self.assertIn("2026-12-16", str(cm.exception))

    @rule(R.CA_DATE_06)
    @rule(R.US_DATE_06)
    def test_settle_a_week_and_more_late_is_attention(self):
        txs, err, _ = _parse(self._H + "2025-03-03,2025-03-14,BUY,XYZ,1,10,"
                             "-10,0,CAD\n", self._TOML)
        self.assertEqual(txs[0]["date_settle"], "2025-03-14")
        self.assertIn("warning: ATTENTION:", err)

    def test_normal_settle_silent(self):
        _, err, _ = _parse(self._H + "2025-03-03,2025-03-04,BUY,XYZ,1,10,"
                           "-10,0,CAD\n", self._TOML)
        self.assertNotIn("ATTENTION", err)


if __name__ == "__main__":
    unittest.main()

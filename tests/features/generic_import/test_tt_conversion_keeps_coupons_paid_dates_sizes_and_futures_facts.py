"""Fixes of 2026-10-29, round 7 (wave B), group taxtext: the .tt
conversion keeps a bond coupon's property (backlog TJ-0856), an income
row's paid date on a settle-basis export (TJ-0862), the futures
settlement facts (TJ-0662), and sizes a .tt option line by the
project's ticker.map MULT (TJ-0864); the T1135 counts the gross income
of a foreign property, not a short's compensation paid (TJ-0837), an
intraday foreign custody holding in the threshold test (TJ-0646), a
cash ledger refused with no row to hold the refusal as unsettled
(TJ-0657), and the standalone tool refuses a cash document of another
year or base currency (TJ-0772). One test class per backlog item, each
named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*, ZZ*), invented
account names, small amounts.

Formerly: test_fix_1029_r7b_taxtext
"""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib import tt_format as CT
from taxjson.bin import taxjson_t1135 as T
from taxjson.model.transaction import TaxTransaction


def _parse(line, account="margin"):
    """parse_tt_line, its warnings captured: (row, stderr text)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        row = CT.parse_tt_line(line, account)
    return row, err.getvalue()


# ======================================= TJ-0856 a bond coupon's symbol
class TestTJ0856InterestKeepsItsProperty(unittest.TestCase):
    """TJ-0856: an INTEREST row naming a foreign bond (its coupon, the
    bond's T1135 income, CA-RPT-14) was written to .tt with no symbol
    and read back as CASH: the bond's T1135 income fell from 100 to
    0.

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0856InterestKeepsItsProperty"""

    COUPON = {"action": "INTEREST", "date": "2025-06-02",
              "date_settle": "2025-06-02", "time": "10:00:00",
              "symbol": "SYN.US", "net_amount": 100.0, "currency": "CAD",
              "account": "margin", "type": "interest"}
    BOND = {"action": "BUYSELL", "date": "2024-11-01",
            "date_settle": "2024-11-01", "time": "10:00:00",
            "symbol": "SYN.US", "quantity": 10.0, "net_amount": 500.0,
            "price": 50.0, "fee": 0.0, "currency": "CAD",
            "account": "margin"}

    @rule(R.CA_RPT_14)
    def test_export_and_reimport_keep_the_coupons_property(self):
        line = CT.tx_to_tt_line(self.COUPON)
        self.assertTrue(line.endswith(" symbol=SYN.US"), line)
        row, err = _parse(line)
        self.assertEqual(row["symbol"], "SYN.US")
        self.assertEqual(err, "")
        rows = [dict(self.BOND), row]
        self.assertEqual(T.property_interest(rows, 2025), {"SYN.US": 100.0})

    def test_interest_on_cash_stays_without_a_symbol(self):
        for sym in ("CASH", ""):
            line = CT.tx_to_tt_line(dict(self.COUPON, symbol=sym))
            self.assertEqual(
                line, "INTEREST 2025-06-02 10:00:00 CAD 100.00000")
            self.assertEqual(_parse(line)[0]["symbol"], "CASH")

    def test_the_events_view_line_reads_back_with_its_symbol(self):
        from taxjson.cli.listings import _tx_display_line
        line = _tx_display_line(self.COUPON)
        self.assertIn("symbol=SYN.US", line)
        self.assertEqual(_parse(line)[0]["symbol"], "SYN.US")

    def test_the_symbol_is_upper_cased_and_other_tokens_refused(self):
        row, _ = _parse("INTEREST 2025-06-02 10:00:00 CAD 100 symbol=syn.us")
        self.assertEqual(row["symbol"], "SYN.US")
        for bad in ("INTEREST 2025-06-02 10:00:00 CAD 100 record=2025-06-01",
                    "INTEREST 2025-06-02 10:00:00 CAD 100 symbol=SYN.US "
                    "symbol=ZZB.US",
                    "INTEREST 2025-06-02 10:00:00 CAD 100 SYN.US"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _parse(bad)


# ======================================= TJ-0862 income keeps its paid date
class TestTJ0862IncomeKeepsItsPaidDate(unittest.TestCase):
    """TJ-0862: a settle-basis export wrote a dividend paid 2025-12-31
    (settling 2026-01-02) with its settlement date, moving its 100 into
    2026 on re-import.

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0862IncomeKeepsItsPaidDate"""

    DIV = {"action": "DIVIDEND", "date": "2025-12-31",
           "date_settle": "2026-01-02", "time": "10:00:00",
           "symbol": "SYN.US", "quantity": 10.0, "price": 0.0,
           "gross_amount": 100.0, "net_amount": 100.0, "currency": "CAD",
           "account": "margin", "type": "dividend"}
    SALE = {"action": "BUYSELL", "date": "2025-12-31",
            "date_settle": "2026-01-02", "time": "10:00:00",
            "symbol": "SYN.US", "quantity": -10.0, "price": 10.0,
            "net_amount": 100.0, "fee": 0.0, "currency": "CAD",
            "account": "margin"}

    @rule(R.CA_DATE_11)
    def test_settle_basis_writes_the_paid_date_of_income(self):
        for action in CT.PAID_DATE_ACTIONS:
            row = dict(self.DIV, action=action)
            if action in ("INTEREST", "FEE"):
                row["symbol"] = "CASH"
            with self.subTest(action=action):
                line = CT.tx_to_tt_line(row, date_basis="settle")
                self.assertEqual(line.split()[1], "2025-12-31", line)
                self.assertEqual(_parse(line)[0]["date"], "2025-12-31")

    def test_a_disposition_still_takes_its_settlement_date(self):
        line = CT.tx_to_tt_line(self.SALE, date_basis="settle")
        self.assertEqual(line.split()[1], "2026-01-02")
        line = CT.tx_to_tt_line(self.SALE, date_basis="trade")
        self.assertEqual(line.split()[1], "2025-12-31")

    def test_the_events_view_on_settle_dates_too(self):
        from taxjson.cli.listings import _tx_display_line
        self.assertEqual(_tx_display_line(self.DIV, settle=True).split()[1],
                         "2025-12-31")
        self.assertEqual(_tx_display_line(self.SALE, settle=True).split()[1],
                         "2026-01-02")

    def test_the_two_dates_note_counts_dispositions_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.json"
            p.write_text(json.dumps({"transactions": [self.DIV]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                lines = list(CT.json_to_tt_lines(p, date_basis="settle"))
        self.assertEqual(len(lines), 1)
        self.assertNotIn("SETTLEMENT date", err.getvalue())


# ================================== TJ-0864 the project's MULT sizes a line
class TestTJ0864TickerMapMultSizesTheLine(unittest.TestCase):
    """TJ-0864: with `MULT SYN251219C00005000.TO 10` in ticker.map, a
    one-contract purchase at price 2 / total 20 was a typo at x100
    ("should be 200"), which `run --strict` stops on.

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0864TickerMapMultSizesTheLine"""

    OPT = "SYN251219C00005000.TO"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tm = Path(self._td.name) / "ticker.map"
        tm.write_text(f"MULT {self.OPT} 10\n")
        env = mock.patch.dict(os.environ, {"TAXJSON_TICKER_MAP": str(tm)})
        env.start()
        self.addCleanup(env.stop)

    def test_correct_mini_totals_are_not_typos(self):
        for line in (f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 20 0",
                     f"BUYSELL 2025-06-04 10:00:00 {self.OPT} -1 CAD 3 30 0"):
            with self.subTest(line=line):
                row, err = _parse(line)
                self.assertEqual(err, "")

    def test_a_total_at_100_is_now_the_typo(self):
        _row, err = _parse(
            f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 200 0")
        self.assertIn("qty*price*10+fee = 20.00", err)

    def test_the_root_and_the_underlying_name_the_size_too(self):
        tm = Path(self._td.name) / "ticker.map"
        tm.write_text("MULT SYN 10\n")
        os.utime(tm, (1, 1))           # a new mtime for the map's cache
        _row, err = _parse(
            f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 20 0")
        self.assertEqual(err, "")

    def test_without_a_mult_line_the_standard_100(self):
        with mock.patch.dict(os.environ, {"TAXJSON_TICKER_MAP": ""}):
            _row, err = _parse(
                f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 20 0")
        self.assertIn("qty*price*100+fee = 200.00", err)


# ================================ TJ-0662 futures settlement facts round-trip
class TestTJ0662FuturesSettlementFactsRoundTrip(unittest.TestCase):
    """TJ-0662: a settled futures book exported to .tt lost each part's
    type=futures_settlement and settle_native: re-imported, the parts
    were raw fills and the Canadian pooled gain changed (150 became 200;
    a long-and-short case 250 became 150).

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0662FuturesSettlementFactsRoundTrip"""

    @staticmethod
    def _fill(i, acct, date, q, p):
        return TaxTransaction(id=i, action="BUYSELL", date=date,
                              date_settle=date, symbol="F:SYNH6.US",
                              quantity=q, currency="CAD", price=p,
                              net_amount=abs(q) * p, multiplier=1,
                              account=acct)

    def _gains(self, raw):
        from taxjson.lib.futures import settle_futures, stamp_rate
        from taxjson.lib.pipeline import GainsRequest, run_gains
        settled = []
        for a in dict.fromkeys(t.account for t in raw):
            ts = settle_futures([copy.deepcopy(t) for t in raw
                                 if t.account == a], "average")[0]
            for t in ts:
                stamp_rate(t, 1)
            settled.extend(ts)
        settled.sort(key=lambda t: t.date)
        lines = [CT.tx_to_tt_line(t.to_dict(), "trade") for t in settled]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            back = [TaxTransaction(**CT.parse_tt_line(s, t.account))
                    for s, t in zip(lines, settled)]

            def g(ts):
                return run_gains(copy.deepcopy(ts), req=GainsRequest(
                    country="canada", year=2025, taxable=True,
                    phantom_hint=False))["summary"]["total_gain"]
            return g(settled), g(back), lines, back, err.getvalue()

    @rule(R.CA_FX_04)
    def test_two_accounts_pooled_gain_survives(self):
        orig, back, lines, rows, err = self._gains([
            self._fill("A1", "a", "2025-01-06", 1, 100),
            self._fill("B1", "b", "2025-02-03", 1, 200),
            self._fill("A2", "a", "2025-03-03", -1, 300)])
        self.assertAlmostEqual(orig, 150.0)
        self.assertAlmostEqual(back, orig)
        self.assertTrue(all("type=futures_settlement" in ln
                            for ln in lines), lines)
        self.assertEqual(rows[2].settle_native, "LC 300.0 200.0 CAD 1")
        self.assertNotIn("differs from", err)

    @rule(R.CA_FX_04)
    def test_long_and_short_stay_apart(self):
        orig, back, _lines, _rows, _err = self._gains([
            self._fill("A1", "a", "2025-01-06", 1, 100),
            self._fill("B1", "b", "2025-02-03", -1, 200),
            self._fill("A2", "a", "2025-03-03", -1, 300),
            self._fill("B2", "b", "2025-04-03", 1, 150)])
        self.assertAlmostEqual(back, orig)

    def test_a_loss_close_reads_back_with_its_negative_total(self):
        row, err = _parse("BUYSELL 2025-03-03 09:30:00 F:SYNH6.US -1 CAD "
                          "50 -50 0 x1 type=futures_settlement "
                          "settle=LC,50.0,-50.0,CAD,1", "a")
        self.assertEqual(row["net_amount"], -50.0)
        self.assertEqual(row["type"], "futures_settlement")
        self.assertEqual(err, "")

    def test_malformed_settlement_tokens_are_refused(self):
        for bad in (
                # a share is not a future
                "BUYSELL 2025-03-03 09:30:00 SYN.US 1 CAD 3 3 0 "
                "type=futures_settlement",
                # the leg sells, the line buys
                "BUYSELL 2025-03-03 09:30:00 F:SYNH6.US 1 CAD 3 0 0 x1 "
                "type=futures_settlement settle=LC,3,0,CAD,1",
                # not a leg
                "BUYSELL 2025-03-03 09:30:00 F:SYNH6.US 1 CAD 3 0 0 x1 "
                "type=futures_settlement settle=XX,3,0,CAD",
                # settle= without the type is an unknown token
                "BUYSELL 2025-03-03 09:30:00 F:SYNH6.US 1 CAD 3 0 0 x1 "
                "settle=LO,3,0,CAD"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _parse(bad, "a")


if __name__ == "__main__":
    unittest.main()

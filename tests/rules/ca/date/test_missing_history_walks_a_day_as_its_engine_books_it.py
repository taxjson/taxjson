"""TJ-5264: the history check walks a day's rows as the country's engine books them.

A Questrade export stamps a day's rows midnight. Units a reinvestment
(REI, settled on its day) adds and a sale of the same day settled two
days later: the Canadian engine orders by settlement date (the REI
first: CA-DATE-14 within a date), the US engine by trade date and clock
(the file's order: US-DATE-13); in both the sale is made from the units
held, nothing is sold short. The missing-history walk put a trade that
settles after its day first among the day's rows (the split day's
re-denomination order, S021-00, taken for every day), so it read the
sale before the REI and named a short neither engine books: the run's
note, its closing line and `tjs find-missing-history` (AFFECTS, a
PeakShort of 5) in both countries.

The walk now takes its order of a day from the country (lib/country
`walk_settle_lag`): Canada places such a trade after the day's other
rows (by settlement), the US among them by its clock. A split's day
keeps the order it had (the settle-lag re-denomination: S021-00).

Every security and account is made up (QZRE, QZSP; 99900001).
"""
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.missing_history import detect_missing_history
from taxjson.model.transaction import TaxTransaction
from taxjson.rules import ids as R
from tax_rules import rule
from tax_rules.dual import cli, settings_for
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
        "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
        "Account #,Activity Type,Account Type\n")
MID = "12:00:00 AM"
_CUR = {"canada": "CAD", "usa": "USD"}


def _qt_rows(country, sale=105):
    """A buy of 100 on March 3, a reinvestment of 5 on August 29 (settled
    that day) and a sale of `sale` the same day, settled September 2."""
    cur = _CUR[country]
    mark = "C$" if country == "canada" else "U$"
    return HEAD + "".join([
        f"2025-03-03 {MID},2025-03-04 {MID},Buy,QZRE,QZRE FUND,100,4.00,"
        f"-400.00,0.00,-400.00,{cur},99900001,Trades,Individual\n",
        f"2025-08-29 {MID},2025-08-29 {MID},REI,QZRE,"
        f"QZRE FUND REINV{chr(64)}{mark}4.00,5,4.00,-20.00,0.00,-20.00,"
        f"{cur},99900001,Dividend reinvestment,Individual\n",
        f"2025-08-29 {MID},2025-09-02 {MID},Sell,QZRE,QZRE FUND,-{sale},"
        f"5.00,{5 * sale:.2f},0.00,{5 * sale:.2f},{cur},99900001,Trades,"
        f"Individual\n"])


def _tx(day, qty, settle=None, action="BUYSELL", sym="QZRE.TO", time="00:00:00",
        rid=""):
    return TaxTransaction(action=action, date=day, symbol=sym, quantity=qty,
                          currency="CAD", time=time,
                          date_settle=settle or day, account="margin",
                          id=rid or f"{day}-{qty}")


def _day(sale=-105.0, sym="QZRE.TO"):
    return [_tx("2025-03-03", 100.0, "2025-03-04", sym=sym),
            _tx("2025-08-29", 5.0, sym=sym),
            _tx("2025-08-29", sale, "2025-09-02", sym=sym)]


def _shorts(rows, country):
    return [(c.symbol, c.peak_short)
            for c in detect_missing_history(rows, country=country)]


@rule(R.CA_DATE_14)
class TestCanadaWalksTheDayBySettlement(unittest.TestCase):

    def test_the_reinvestment_before_the_lagged_sale_reads_long(self):
        self.assertEqual(_shorts(_day(), "canada"), [])

    def test_a_sale_beyond_the_day_s_units_still_reads_short(self):
        # The control: 110 sold of 105 held is a short in the engine too.
        self.assertEqual([s for s, _q in _shorts(_day(-110.0), "canada")],
                         ["QZRE.TO"])

    def test_that_short_is_the_engine_s_5_not_10(self):
        # (The sale walked before the REI went 10 short.)
        self.assertEqual(_shorts(_day(-110.0), "canada"), [("QZRE.TO", -5.0)])

    def test_a_split_day_keeps_its_order(self):
        # The control of S021-00: on a split's day a trade settled later
        # is walked with the split by its clock, as before: 100 held, a
        # 2-for-1 split at midnight, 200 sold at 10:00 settled the next
        # day: nothing short.
        rows = [_tx("2025-03-03", 100.0, "2025-03-04", sym="QZSP.TO"),
                _tx("2025-06-10", 2.0, action="SPLIT", sym="QZSP.TO"),
                _tx("2025-06-10", -200.0, "2025-06-11", sym="QZSP.TO",
                    time="10:00:00")]
        self.assertEqual(_shorts(rows, "canada"), [])


@rule(R.US_DATE_13)
class TestTheUsWalksTheDayByClock(unittest.TestCase):

    def test_the_reinvestment_listed_first_reads_long(self):
        self.assertEqual(_shorts(_day(sym="QZRE"), "usa"), [])

    def test_a_sale_beyond_the_day_s_units_still_reads_short(self):
        self.assertEqual([s for s, _q in _shorts(_day(-110.0, sym="QZRE"),
                                                 "usa")], ["QZRE"])

    def test_that_short_is_the_engine_s_5_not_10(self):
        self.assertEqual(_shorts(_day(-110.0, sym="QZRE"), "usa"),
                         [("QZRE", -5.0)])


class _RunCases:
    COUNTRY = ""

    def _project(self, sale=105):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "taxjson.toml").write_text(
            settings_for(self.COUNTRY, year=2025, source_currencies=[])
            + '[accounts.margin]\ntype = "taxable"\n')
        acct = root / "inputs" / "margin"
        acct.mkdir(parents=True)
        (acct / "qt_2025.csv").write_text(_qt_rows(self.COUNTRY, sale))
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return root, r.stdout + r.stderr

    def test_no_short_is_named_by_the_run_or_find_missing_history(self):
        root, out = self._project()
        self.assertNotIn("read short", out)
        self.assertNotIn("go short", out)
        r = cli(root, "find-missing-history")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("No missing-cost-basis issues found", r.stdout)
        self.assertNotIn("AFFECTS", r.stdout)

    def test_a_real_short_is_still_listed_at_the_engine_s_depth(self):
        # The control: 110 sold of the 105 held is listed (on the old walk
        # too), 5 deep (the old walk: 10).
        root, _out = self._project(110)
        r = cli(root, "find-missing-history")
        self.assertIn("AFFECTS", r.stdout)
        with self.subTest("depth"):
            self.assertIn("-5.0000", r.stdout)


@rule(R.CA_DATE_14)
class TestCanadaRun(_RunCases, unittest.TestCase):
    COUNTRY = "canada"


@rule(R.US_DATE_13)
class TestUsRun(_RunCases, unittest.TestCase):
    COUNTRY = "usa"



if __name__ == "__main__":
    unittest.main()

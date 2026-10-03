"""Moved from the core's tests/test_fix_runcore.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Regression pins for the run-core medium findings (2026-09 audit).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import os
import tempfile
import unittest
from pathlib import Path


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,55500001,Trades,Individual\n")


class TestFetchOverlapByHeader(unittest.TestCase):
    """R1-74: --trim-overlap found the date by column position (a
    manual export with Settlement Date first lost a trade the API file
    does not hold) and rewrote the file non-atomically at 0664.
    S046-14: the overlap guard globbed *.csv case-sensitively."""

    _SETTLE_FIRST = ("Settlement Date,Transaction Date,Action,Symbol,"
                     "Description,Quantity,Price,Gross Amount,Commission,"
                     "Net Amount,Currency,Account #,Activity Type,"
                     "Account Type\n"
                     "2025-12-15 12:00:00 AM,2025-12-12 12:00:00 AM,Buy,"
                     "XYZ.TO,D,5,10.00,50.00,0.00,-50.00,CAD,55500001,"
                     "Trades,Ind\n")

    def test_trim_uses_the_transaction_date_column(self):
        from taxjson_fetch.command import (_qt_trim_file,
                                             _qt_window_overlap)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = d / "manual.csv"
            manual.write_text(self._SETTLE_FIRST)
            out = d / "questrade_2026.csv"
            out.write_text(_QT_HEADER)
            self.assertEqual(_qt_window_overlap(d, out, "2025-12-15",
                                                "2026-08-31"), [])
            self.assertEqual(_qt_trim_file(manual, "2025-12-15",
                                           "2026-08-31"), 0)
            self.assertIn("XYZ.TO", manual.read_text())

    def test_trimmed_file_is_private(self):
        from taxjson_fetch.command import _qt_trim_file
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = d / "manual.csv"
            manual.write_text(_MARGIN_CSV)
            os.chmod(manual, 0o664)
            self.assertEqual(_qt_trim_file(manual, "2025-06-01",
                                           "2025-12-31"), 1)
            self.assertEqual(manual.stat().st_mode & 0o777, 0o600)
            self.assertTrue((d / "manual.csv.bak").exists())

    def test_upper_case_sibling_is_an_overlap(self):
        from taxjson_fetch.command import _qt_window_overlap
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            sib = d / "QT_MANUAL.CSV"
            sib.write_text(_MARGIN_CSV)
            out = d / "questrade_2025.csv"
            out.write_text(_QT_HEADER)
            hits = _qt_window_overlap(d, out, "2025-06-01", "2025-12-31")
        self.assertEqual([(p.name, n) for p, n in hits],
                         [("QT_MANUAL.CSV", 1)])


class TestFlexReplaceKeepsTheYear(unittest.TestCase):
    """S007-00: `fetch` overwrote ib_flex.csv with whatever period the
    Flex query returned; a 'Year to date' download in January deleted a
    whole year of activity at exit 0."""

    _OLD = ('"Statement","Data","WhenGenerated","2026-01-02, 10:00:00"\n'
            '"Trades","Data","Order","Stocks","USD","XYZ",'
            '"2025-03-05, 10:00:00","10","5"\n'
            '"Trades","Data","Order","Stocks","USD","XYZ",'
            '"2025-11-05, 10:00:00","-10","6"\n')
    _YTD = ('"Statement","Data","WhenGenerated","2026-01-20, 10:00:00"\n'
            '"Trades","Data","Order","Stocks","USD","ABC",'
            '"2026-01-06, 10:00:00","1","5"\n')

    def test_lost_dates_are_reported(self):
        from taxjson_fetch.command import _flex_lost_dates
        self.assertEqual(_flex_lost_dates(self._OLD, self._YTD, 2025),
                         ["2025-03-05", "2025-11-05"])
        self.assertEqual(_flex_lost_dates(self._OLD, self._OLD, 2025), [])
        self.assertEqual(_flex_lost_dates("", self._YTD, 2025), [])


if __name__ == "__main__":
    unittest.main()

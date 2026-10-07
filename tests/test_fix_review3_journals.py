"""Third pre-release review of the journals (v0.24.0):

- 2: a journal justifies an opposite-trade day near its legs only when
  that day's trades match ITS quantity in ITS direction (the FROM
  listing bought, the TO listing sold), and only one such day; a sale
  with no purchase on one listing and a buy of the other near an
  unrelated journal of another size stays missing history.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: 55500001).
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import projects_both

from test_fix_review2_journals import _USA_CAD, _short_reported


# ------------------------------------------------------------------ 2

_SHORT_A = ("BUYSELL 2025-03-03 09:00:00 QZF.U.TO -100 USD 7.00 700.00 "
            "0.00\n"
            "BUYSELL 2025-03-03 10:00:00 QZF.TO 100 CAD 10.00 1000.00 "
            "0.00\n")
_GAMBIT_A = ("BUYSELL 2025-03-04 10:00:00 QZF.TO 50 CAD 10.00 500.00 0.00\n"
             "JOURNAL 2025-03-05 QZF.TO QZF.U.TO 50\n"
             "BUYSELL 2025-03-06 10:00:00 QZF.U.TO -50 USD 7.20 360.00 "
             "0.00\n")


class TestJournalJustifiesItsOwnTradesOnly(unittest.TestCase):

    def _a(self, country):
        """A .tt journal of 50 units two days after a 100-unit sale with
        no purchase (and a buy of the other listing): still missing
        history; the journal's own gambit is not."""
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for k, body in (("short", _SHORT_A + _GAMBIT_A),
                            ("gambit", _GAMBIT_A)):
                files = {"ticker.map": "TOBASE QZF.U.TO QZF.TO\n",
                         "inputs/margin/m.tt": body}
                root = projects_both(Path(td) / k, files=files,
                                     usa=_USA_CAD)[country]
                got[k] = _short_reported(self, root, "QZF")
            self.assertEqual(got["short"], (True, True, True))
            self.assertEqual(got["gambit"], (False, False, False))

    def _b(self, country):
        """An RBC gambit of 1000 units (trades May 5, J~ legs May 6) and
        a 200-unit sale with no purchase plus a buy of the other listing
        on May 8: the May 8 sale is missing history."""
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for k, extra in (("short", True), ("gambit", False)):
                files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap}
                if extra:
                    files["inputs/margin/m.tt"] = (
                        "BUYSELL 2025-05-08 09:00:00 QZD.U.TO -200 USD "
                        "10.20 2040.00 0.00\n"
                        "BUYSELL 2025-05-08 10:00:00 QZD.TO 200 CAD 13.90 "
                        "2780.00 0.00\n")
                root = projects_both(Path(td) / k, files=files,
                                     usa=_USA_CAD)[country]
                got[k] = _short_reported(self, root, "QZD")
            self.assertEqual(got["short"], (True, True, True))
            self.assertEqual(got["gambit"], (False, False, False))

    @rule("CA-XLIST-04")
    def test_canada_tt_journal_of_another_size_hides_no_short(self):
        self._a("canada")

    @rule("US-XLIST-03")
    def test_usa_tt_journal_of_another_size_hides_no_short(self):
        self._a("usa")

    @rule("CA-XLIST-04")
    def test_canada_rbc_gambit_justifies_only_its_own_day(self):
        self._b("canada")

    @rule("US-XLIST-03")
    def test_usa_rbc_gambit_justifies_only_its_own_day(self):
        self._b("usa")


class TestJournalDayUnit(unittest.TestCase):
    """walk_journal_symbols' day choice, on its inputs."""

    def test_quantity_direction_and_one_day(self):
        from taxjson.lib.missing_history import _journal_trade_day
        legs = ["2025-05-06"]
        trades = {
            ("m", "2025-05-05"): {"QZD.TO": [1000.0, 0.0],
                                  "QZD.U.TO": [0.0, 1000.0]},
            # Same size, a day later: the journal already has its day.
            ("m", "2025-05-07"): {"QZD.TO": [1000.0, 0.0],
                                  "QZD.U.TO": [0.0, 1000.0]},
            # Another size.
            ("m", "2025-05-08"): {"QZD.TO": [200.0, 0.0],
                                  "QZD.U.TO": [0.0, 200.0]},
        }
        day = _journal_trade_day(trades, "m", legs, "QZD.TO", "QZD.U.TO",
                                 1000.0)
        # The nearest; a tie goes to the earlier day (trades settle
        # after they are made).
        self.assertEqual(day, "2025-05-05")
        self.assertIsNone(_journal_trade_day(
            trades, "m", legs, "QZD.TO", "QZD.U.TO", 200.0 + 1))
        # The wrong direction (TO bought, FROM sold) is no gambit.
        self.assertIsNone(_journal_trade_day(
            {("m", "2025-05-05"): {"QZD.TO": [0.0, 1000.0],
                                   "QZD.U.TO": [1000.0, 0.0]}},
            "m", legs, "QZD.TO", "QZD.U.TO", 1000.0))
        # Trades on the legs' own day: that day, no other.
        self.assertEqual("2025-05-06", _journal_trade_day(
            {("m", "2025-05-06"): {"QZD.TO": [1000.0, 0.0],
                                   "QZD.U.TO": [0.0, 1000.0]},
             ("m", "2025-05-05"): {"QZD.TO": [1000.0, 0.0],
                                   "QZD.U.TO": [0.0, 1000.0]}},
            "m", legs, "QZD.TO", "QZD.U.TO", 1000.0))


if __name__ == "__main__":
    unittest.main()

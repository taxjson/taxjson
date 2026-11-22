"""Mutation pins for re-audit-2 test-gap findings (tests-pins-04/05/06):
RBC and Webull parser dates and pairing, the MXN settlement cutover, the
phantom-relevance split de-duplication and the config checks.

Each test fails when the finding's surviving mutant is applied. All data
is synthetic: fake tickers and a fake broker account id.

Formerly: test_fix_a2_pins2_rbc"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.parser_qt_rbc_rows import rbc_parse, rrow


class TestRbcCrossFileCashInLieu(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestRbcCrossFileCashInLieu"""

    def test_a2_0898_merger_cil_in_next_years_file_pairs(self):
        """S064-14: a December MER (ROC + .947213 new per old) whose cash
        in lieu posts in January's export is one event: the stated ratio
        and the fractional sale, no UNMATCHED leg."""
        f25 = (rrow("December 15, 2025", "Reorganization", "QMRG",
                    "QMRG CORP NO PAR", "75", "", "0", "CAD",
                    "MGR - QMRG CORP NO PAR SHRS RECEIVED THRU MERGER")
               + rrow("December 15, 2025", "Reorganization", "Q099003",
                      "QMRG CORP NEW", "-80", "", "421.92", "CAD",
                      "MER - QMRG CORP NEW DEFAULT: ROC OF C$5.2740 + "
                      ".947213 NEW SHS PER 1 OLD")
               + rrow("June 9, 2025", "Buy", "QMRG", "QMRG CORP NEW", "80",
                      "168", "-13449.95", "CAD", "QMRG UNSOLICITED DA"))
        f26 = rrow("January 9, 2026", "Reorganization", "QMRG",
                   "QMRG CORP NO PAR", "", "", "41.30", "CAD",
                   "CIL - QMRG CORP NO PAR CASH IN LIEU OF FRAC SHARES")
        txs, err, _ = rbc_parse(f25, f26)
        self.assertNotIn('UNMATCHED', err)
        split = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(split), 1, txs)
        self.assertAlmostEqual(split[0]['quantity'], 0.947213, places=6)
        cil = [t for t in txs if t['action'] == 'BUYSELL'
               and t['quantity'] < 0]
        self.assertEqual(len(cil), 1, txs)
        self.assertEqual(cil[0]['date'], '2026-01-09')
        self.assertAlmostEqual(cil[0]['quantity'], -0.77704, places=5)
        self.assertAlmostEqual(cil[0]['net_amount'], 41.3, places=2)


class TestRbcMergerNote(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestRbcMergerNote"""

    def test_a2_0899_merger_note_is_country_neutral(self):
        """S064-20: the merger note neither cites Canada nor tells the
        user to enter the shares through a .tt file (the corp-actions
        stage books them — a .tt row double-books)."""
        body = (rrow("March 3, 2025", "Buy", "QZH", "QZH CORPORATION",
                     "15", "100", "-1507.77", "USD", "QZH UNSOLICITED")
                + rrow("July 1, 2025", "Reorganization", "X000004",
                       "QZH CORPORATION", "-15", "", "0", "USD",
                       "MGR - QZH CORPORATION MERGER TO QZC CORPORATION "
                       "1 NEW = 1 OLD")
                + rrow("July 1, 2025", "Reorganization", "QZC",
                       "QZC CORPORATION", "15", "", "0", "USD",
                       "MGR - QZC CORPORATION SHRS RECEIVED THRU MERGER"))
        _, err, _ = rbc_parse(body)
        self.assertIn('merger row(s)', err)
        note = next(ln for ln in err.splitlines() if 'merger row(s)' in ln)
        self.assertNotIn('canada', note.lower())
        self.assertIn('do not also enter the shares in a .tt file', note)
        self.assertNotIn('enter them manually', note)


class TestRbcReinvestReversalDate(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestRbcReinvestReversalDate"""

    @rule(R.CA_DIST_03)
    def test_a2_1583_reversal_cancels_the_drip_on_or_before_it(self):
        """Two identical DRIPs (April and June) and an April reversal:
        the reversal cancels the April purchase, so the June one is
        booked whatever the export's row order."""
        def rei(d, q, v, tail=""):
            return rrow(d, "Dividends", "QRT.UN", "QUARTZ REIT", q, "", v,
                        "CAD", "REI - QUARTZ REIT REINV@C$27.15" + tail)
        apr = rei("April 13, 2022", "3", "-81.45")
        jun = rei("June 15, 2022", "3", "-81.45")
        cxl = rei("April 20, 2022", "-3", "81.45", " CANCEL")
        for body in (apr + cxl + jun, jun + cxl + apr):
            txs, err, _ = rbc_parse(body)
            self.assertEqual([(t['date'], t['action'], t['quantity'])
                              for t in txs],
                             [('2022-06-15', 'BUYSELL', 3.0)], err)


# --------------------------------------------------------------- Webull
class TestWebullAssignmentWindow(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestWebullAssignmentWindow"""

    def _assign_legs(self, settle):
        from _support.wb_summary_2025 import _H25, _PRE, _parse
        txt = (_PRE + _H25 +
               'USD,20-03-2025,BUY,@XYZ,CALL XYZ03/21/25 50,OPC,1,1.00,,'
               '(100.99)\n'
               'USD,21-03-2025,SELL,,,,-1,0.00,,\n'
               f'USD,{settle},BUY,XYZ,XYZ CORP,SHS,100,50.00,,'
               '"(5,001.00)"\n')
        tx, err = _parse(txt)
        return sum(1 for t in tx if t['action'] == 'ASSIGN'), err

    def test_a2_0946_pairing_window_is_seven_days(self):
        """A $1.00-fee trade at the strike settling 7 days after a $0
        option close is its exercise; 8 or more days later it is an
        ordinary trade and the option an expiry."""
        n, err = self._assign_legs('28-03-2025')        # gap 7
        self.assertGreater(n, 0, err)
        for settle in ('29-03-2025', '10-04-2025', '20-04-2025'):
            n, err = self._assign_legs(settle)          # gap 8, 20, 30
            self.assertEqual(n, 0, (settle, err))


if __name__ == "__main__":
    unittest.main()

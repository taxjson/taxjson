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


# ------------------------------------------------------------------ RBC
class TestRbcExpiryAndSettleDates(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestRbcExpiryAndSettleDates"""

    def test_a2_0528_warrant_expiry_posted_in_january_books_dec_31(self):
        """S065-04: RBC posts a right/warrant expiry the next business
        day; the row is booked on the expiry date the description names,
        so a Dec-31 expiry's loss stays in its year."""
        txs, _, _ = rbc_parse(rrow(
            "January 3, 2028", "Reorganization", "QZW.WT", "QZW CORP WTS",
            "-100", "", "0", "CAD",
            "EXP - WTS QZW CORP AS OF 12/31/27 EXPIRED",
            settle="January 3, 2028"))
        self.assertEqual(len(txs), 1, txs)
        self.assertEqual((txs[0]['date'], txs[0]['date_settle']),
                         ('2027-12-31', '2027-12-31'))

    @rule(R.CA_DATE_04)
    def test_a2_0529_blank_settle_option_sale_is_t_plus_1(self):
        """R1-194: an option settles T+1 in every era — a blank-settle
        RBC option sale on 2023-12-28 (equities T+2 then) settles
        2023-12-29, not in 2024."""
        txs, _, _ = rbc_parse(rrow(
            "December 28, 2023", "Sell", "8QZQZQ2", "", "-2", "1.10",
            "218.00", "CAD",
            "CALL .QZB   06/21/24    30 QZB INC CLOSE CONTRACT", settle=""))
        self.assertEqual(len(txs), 1, txs)
        self.assertEqual(txs[0]['date'], '2023-12-28')
        self.assertEqual(txs[0]['date_settle'], '2023-12-29')


class TestRbcOneDateExportOrder(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestRbcOneDateExportOrder"""

    @rule(R.CA_DATE_14)
    def test_a2_0879_single_date_export_is_read_bottom_up(self):
        """A one-day RBC export is newest-first like every RBC export:
        the rebuy listed ABOVE the sell happened after it, so the sale is
        made from the shares held before the rebuy."""
        buy0 = rrow("January 15, 2025", "Buy", "QZK", "QZK CORP", "100",
                    "10", "-1009.95", "CAD", "QZK CORP UNSOLICITED DA")
        sell = rrow("January 15, 2025", "Sell", "QZK", "QZK CORP", "-100",
                    "9", "890.05", "CAD", "QZK CORP UNSOLICITED DA")
        rebuy = rrow("January 15, 2025", "Buy", "QZK", "QZK CORP", "100",
                     "9.50", "-959.95", "CAD", "QZK CORP UNSOLICITED DA")
        # newest-first: rebuy, sell, first buy (bottom = earliest)
        txs, err, _ = rbc_parse(rebuy + sell + buy0)
        seq = [t['quantity'] for t in sorted(
            txs, key=lambda t: (t['date'], t['time']))]
        self.assertEqual(seq, [100.0, -100.0, 100.0], err)
        by_qty_time = sorted((t['time'], t['quantity'], t['price'])
                             for t in txs)
        # the 9.50 rebuy is the LAST row of the day
        self.assertEqual(by_qty_time[-1][1:], (100.0, 9.5))


# ----------------------------------------------------------------- dates
class TestMexicoT1Cutover(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestMexicoT1Cutover"""

    @rule(R.CA_DATE_04)
    def test_a2_1568_mxn_moves_to_t_plus_1_on_2024_05_27(self):
        """Mexico moved to T+1 with Canada on 2024-05-27 (the US a day
        later, after Memorial Day)."""
        from taxjson.lib.dates import settlement_date, settlement_lag_days
        self.assertEqual(settlement_lag_days('2024-05-24', 'MXN'), 2)
        self.assertEqual(settlement_lag_days('2024-05-27', 'MXN'), 1)
        self.assertEqual(settlement_date('2024-05-27', 'MXN'), '2024-05-28')
        self.assertEqual(settlement_lag_days('2024-05-27', 'USD'), 2)


if __name__ == "__main__":
    unittest.main()

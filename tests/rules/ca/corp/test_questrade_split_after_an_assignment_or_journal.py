"""A split scales the holding (CA-CORP-01), and Questrade states it as the
shares received on the shares held (SPLIT ON 50 SHS): the ratio is read
against the account's position on the day. The position plan counted
trades, transfers and splits but not an option assignment's or exercise's
stock leg, nor a BRW journal leg, so a holding those carried across zero
read the wrong side (backlog TJ-4546): a short 50 assigned 100 (long 50)
then split 25 on 50 booked SPLIT 0.5, not 1.5, and the same split stating
+50 on 50 was refused as a ratio of 0. All data synthetic (made-up tickers
SAMPLA, SAMPLF).
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.qt_parse import qrow, qt_parse


def _split(sym, td, qty, held):
    return qrow(td=td, action='DIS', sym=sym, desc=f'{sym} STK SPLIT ON {held} SHS',
                qty=qty, price='0.00', gross='0.00', comm='0.00', net='0.00',
                cur='USD', act='Dividends')


def _splits(rows):
    txs, _ = qt_parse({'99900001.csv': rows})
    return [(t['symbol'], t['quantity']) for t in txs if t['action'] == 'SPLIT']


_SHORT_THEN_ASSIGNED = (
    qrow(td='2025-01-10', action='Sell', sym='SAMPLA', desc='SAMPLA INC WE ACTED AS AGENT',
         qty='-50', price='10.00', gross='500.00', comm='-4.95', net='495.05', cur='USD')
    + qrow(td='2025-03-21', action='ASN', sym='SAMPLA',
           desc='SAMPLA INC ASSIGNMENT OF OPTION PUT SAMPLA 03/21/25 10',
           qty='100', price='10.00', gross='-1000.00', comm='0.00', net='-1000.00',
           cur='USD', act='Other'))


@rule(R.CA_CORP_01)
class TestASplitAfterAnAssignmentCrossedZero(unittest.TestCase):

    def test_25_on_50_held_long_is_3_for_2(self):
        self.assertEqual(
            _splits(_SHORT_THEN_ASSIGNED + _split('SAMPLA', '2025-04-01', '25', 50)),
            [('SAMPLA.US', 1.5)])

    def test_50_on_50_held_long_is_2_for_1(self):
        self.assertEqual(
            _splits(_SHORT_THEN_ASSIGNED + _split('SAMPLA', '2025-04-01', '50', 50)),
            [('SAMPLA.US', 2.0)])

    def test_a_stock_leg_under_activity_type_trades_counts_too(self):
        # Review round 1: an ASN stock leg whose Activity Type is Trades
        # was read as no event (a Trades row not a Buy or Sell): 0.5.
        rows = _SHORT_THEN_ASSIGNED.replace(',Other,', ',Trades,')
        self.assertIn(',ASN,SAMPLA,', rows)
        self.assertNotIn(',Other,', rows)
        self.assertEqual(_splits(rows + _split('SAMPLA', '2025-04-01', '25', 50)),
                         [('SAMPLA.US', 1.5)])

    def test_a_short_holding_still_reads_short(self):
        # With no assignment the 50 held are short: 25 more short on 50
        # short is 3 for 2 as well, read on the short side.
        sell = qrow(td='2025-01-10', action='Sell', sym='SAMPLA',
                    desc='SAMPLA INC WE ACTED AS AGENT', qty='-50', price='10.00',
                    gross='500.00', comm='-4.95', net='495.05', cur='USD')
        self.assertEqual(_splits(sell + _split('SAMPLA', '2025-04-01', '-25', 50)),
                         [('SAMPLA.US', 1.5)])


@rule(R.CA_CORP_01)
class TestASplitAfterAJournalLeg(unittest.TestCase):

    def test_the_journaled_units_count(self):
        name = 'SAMPLF US DOLLAR FUND'
        rows = (
            qrow(td='2025-06-10', action='Buy', sym='SAMPLF.TO',
                 desc=f'{name} WE ACTED AS AGENT', qty='100', price='13.00',
                 gross='-1300.00', comm='-4.95', net='-1304.95', cur='CAD')
            + qrow(td='2025-07-10', action='Sell', sym='SAMPLF.U.TO',
                   desc=f'{name} WE ACTED AS AGENT', qty='-50', price='10.00',
                   gross='500.00', comm='-4.95', net='495.05', cur='USD')
            + qrow(td='2025-09-02', action='BRW', sym='SAMPLF.TO',
                   desc=f'{name} JOURNAL POSITION TO USD', qty='-100', price='0.00',
                   gross='0.00', comm='0.00', net='0.00', cur='CAD', act='Other')
            + qrow(td='2025-09-02', action='BRW', sym='SAMPLF.U.TO',
                   desc=f'{name} JOURNAL POSITION FROM CAD BOOK VALUE: $1038.10 '
                        'CNV@ 1.3000',
                   qty='100', price='0.00', gross='0.00', comm='0.00', net='0.00',
                   cur='USD', act='Other')
            + qrow(td='2025-10-01', action='DIS', sym='SAMPLF.U.TO',
                   desc=f'{name} STK SPLIT ON 50 SHS', qty='25', price='0.00',
                   gross='0.00', comm='0.00', net='0.00', cur='USD', act='Dividends'))
        # Short 50, then 100 journaled in: long 50; 25 on 50 is 3 for 2.
        self.assertEqual(_splits(rows), [('SAMPLF.U.TO', 1.5)])


if __name__ == "__main__":
    unittest.main()

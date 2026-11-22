"""Regression tests for the medium-round `ibparse` audit findings
(IB statement parser, shared broker helpers, taxjson-brokerage, the
transaction schema and broker detection). Every fixture is synthetic:
invented tickers and ISINs, fake account ids marked pii-ok.

Formerly: test_fix_ibparse"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from _support.ib_statement_rows import HEAD, TRADES_H, _trade, _parse_ib


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------ settlement pairing
class TestIbAssignLegsShareASettleDate(unittest.TestCase):
    """S058-01: before the T+1 cutover the option leg settled T+1 and
    the stock leg T+2, so a same-day trade could consume the premium.

    Formerly: test_fix_ibparse.TestIbAssignLegsShareASettleDate"""

    @rule(R.CA_DATE_04)
    @rule(R.US_DATE_04)
    def test_pre_cutover_option_leg_takes_the_stock_leg_settle(self):
        body = (HEAD + TRADES_H
                + _trade('QZX 16JUN23 50 P', '2023-06-16, 16:20:00', 1, 0, 0,
                         0, code='A;C', cat='Equity and Index Options',
                         cur='CAD')
                + _trade('QZX', '2023-06-16, 16:20:00', 100, 50, -5000, 0,
                         code='A;O', cur='CAD'))
        _, txs, _ = _parse_ib(body)
        leg = next(t for t in txs if t['action'] == 'ASSIGN')
        stock = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertEqual(stock['date_settle'], '2023-06-20')
        self.assertEqual(leg['date_settle'], '2023-06-20')


if __name__ == '__main__':
    unittest.main()

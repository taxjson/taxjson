"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers, fake
account ids and made-up amounts.

Formerly: test_fix_def_rest"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from _support.parser_blank_settle_and_renames import _qt_settle, _rbc_settle, _gen_settle
from _support.ib_statement_rows import HEAD, TRADES_H, FII_H, _trade, _parse_ib, _fii_stock


# ------------------------------------------ A2-1052 / A2-1054: settle market
class TestBlankSettleListingMarket(unittest.TestCase):
    """A2-1052 / A2-1054: a blank settlement cell falls back to the
    standard cycle of the LISTING's market in every parser. Questrade and
    RBC keyed it on the row currency, so a US-dollar TSX unit settled on
    the US calendar (2025-06-30 -> 07-01, through Canada Day) and a
    CAD-settled US stock on the Canadian one, while the generic importer
    and IB used the listing.

    Formerly: test_fix_def_rest.TestBlankSettleListingMarket"""

    @rule(R.CA_DATE_05)
    def test_market_of(self):
        from taxjson.lib.dates import market_of
        self.assertEqual(market_of("QZU.U.TO", "USD"), "CAD")
        self.assertEqual(market_of("QZA.US", "CAD"), "USD")
        self.assertEqual(market_of("QZL.L", "USD"), "GBP")
        self.assertEqual(market_of("QZB.AX", "USD"), "AUD")
        self.assertEqual(market_of("QZV.V", "USD"), "CAD")
        self.assertEqual(market_of("QZE", "EUR"), "EUR")
        self.assertEqual(market_of("F:QZES", "CAD"), "USD")

    @rule(R.CA_DATE_05)
    def test_questrade_usd_tsx_unit_settles_on_canadian_calendar(self):
        # Canada Day 2025 (Tue) is a CDS holiday, not a US one.
        self.assertEqual(_qt_settle("2025-06-30", "QZU.U.TO", "USD"),
                         ("QZU.U.TO", "2025-07-02"))
        # Boxing Day: the TSX is shut, the NYSE is open.
        self.assertEqual(_qt_settle("2025-12-24", "QZU.U.TO", "USD")[1],
                         "2025-12-29")

    @rule(R.US_DATE_05)
    def test_questrade_cad_settled_us_stock_settles_on_us_calendar(self):
        sym, settle = _qt_settle(
            "2025-06-30", "QZA",  "CAD",
            desc="QZA INC WE ACTED AS AGENT CROSS CURRENCY TRADE "
                 "EXCHANGE RATE 1.3600", net="-136")
        self.assertEqual((sym, settle), ("QZA.US", "2025-07-01"))

    @rule(R.CA_DATE_05)
    def test_rbc_usd_dlr_unit_settles_on_canadian_calendar(self):
        # The TSX unit is named by the project's ticker.map EXTRACT line
        # (no built-in security, owner 2026-10-04); the parser applies it
        # before the settlement fallback reads the listing's market.
        from unittest import mock
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        with mock.patch.object(RbcBrokerage, "security_overrides",
                               [("qz u.s. dlr currency etf", "USD",
                                 "QZD.U.TO")]):
            self.assertEqual(
                _rbc_settle("June 30, 2025", "QZD.U",
                            "QZ U.S. DLR CURRENCY ETF", "USD"),
                ("QZD.U.TO", "2025-07-02"))

    @rule(R.CA_DATE_05)
    def test_ib_questrade_rbc_and_generic_agree(self):
        ib_text = (HEAD + TRADES_H
                   + _trade('QZU.U', '2025-06-30, 10:00:00', 10, 10, -100)
                   + FII_H + _fii_stock('QZU.U', 'CA9990002001',
                                        exch='TSE'))
        _p, txs, err = _parse_ib(ib_text)
        ib = [(t['symbol'], t['date_settle']) for t in txs
              if t['action'] == 'BUYSELL']
        self.assertEqual(ib, [("QZU.U.TO", "2025-07-02")], err)
        self.assertEqual(_gen_settle("2025-06-30", "QZU.U.TO", "USD"),
                         ("QZU.U.TO", "2025-07-02"))
        self.assertEqual(_qt_settle("2025-06-30", "QZU.U.TO", "USD")[1],
                         "2025-07-02")

    @rule(R.CA_DATE_04)
    def test_generic_usd_line_on_the_lse_uses_the_uk_cycle(self):
        # T+2 in the UK until 2027-10-11; the US T+1 cycle gave 06-03.
        self.assertEqual(_gen_settle("2025-06-02", "QZL.L", "USD")[1],
                         "2025-06-04")


# ------------------------------------------ A2-1056: hints the map answers
import os


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


if __name__ == "__main__":
    unittest.main()

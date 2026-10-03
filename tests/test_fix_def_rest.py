"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers,
fake account ids (55500001, U5550001) and made-up amounts."""  # pii-ok
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from test_fix_rbcqt import q, qt_parse
from test_fix_rbc import row as rbc_row, parse_one as rbc_parse
from test_fix_ibparse import (HEAD, TRADES_H, FII_H, _trade, _parse_ib,
                              _fii_stock)
from test_fix_a2_generic import _parse as gen_parse, _toml as gen_toml


# ------------------------------------------ A2-1052 / A2-1054: settle market
def _qt_settle(td, sym, cur, desc="QZU FUND WE ACTED AS AGENT", net=None):
    body = q(td=td, sd='', sym=sym, desc=desc, qty='10', price='10',
             gross='-100', comm='0', net=net or '-100', cur=cur)
    txs, err, _ = qt_parse(body)
    assert len(txs) == 1, err
    return txs[0]['symbol'], txs[0]['date_settle']


def _rbc_settle(td_long, sym, symdesc, cur):
    txs, err, _ = rbc_parse(rbc_row(td_long, "Buy", sym, symdesc, "10", "10",
                                    "-100", cur, f"{symdesc} UNSOLICITED",
                                    settle=" "))
    assert len(txs) == 1, err
    return txs[0]['symbol'], txs[0]['date_settle']


def _gen_settle(td, sym, cur):
    txs, err, _ = gen_parse(
        "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"
        f"{td},BUY,{sym},10,10,-100,0,{cur}\n", gen_toml())
    assert len(txs) == 1, err
    return txs[0]['symbol'], txs[0]['date_settle']


class TestBlankSettleListingMarket(unittest.TestCase):
    """A2-1052 / A2-1054: a blank settlement cell falls back to the
    standard cycle of the LISTING's market in every parser. Questrade and
    RBC keyed it on the row currency, so a US-dollar TSX unit settled on
    the US calendar (2025-06-30 -> 07-01, through Canada Day) and a
    CAD-settled US stock on the Canadian one, while the generic importer
    and IB used the listing."""

    @rule("CA-DATE-05")
    def test_market_of(self):
        from taxjson.lib.dates import market_of
        self.assertEqual(market_of("QZU.U.TO", "USD"), "CAD")
        self.assertEqual(market_of("QZA.US", "CAD"), "USD")
        self.assertEqual(market_of("QZL.L", "USD"), "GBP")
        self.assertEqual(market_of("QZB.AX", "USD"), "AUD")
        self.assertEqual(market_of("QZV.V", "USD"), "CAD")
        self.assertEqual(market_of("QZE", "EUR"), "EUR")
        self.assertEqual(market_of("F:QZES", "CAD"), "USD")

    @rule("CA-DATE-05")
    def test_questrade_usd_tsx_unit_settles_on_canadian_calendar(self):
        # Canada Day 2025 (Tue) is a CDS holiday, not a US one.
        self.assertEqual(_qt_settle("2025-06-30", "QZU.U.TO", "USD"),
                         ("QZU.U.TO", "2025-07-02"))
        # Boxing Day: the TSX is shut, the NYSE is open.
        self.assertEqual(_qt_settle("2025-12-24", "QZU.U.TO", "USD")[1],
                         "2025-12-29")

    @rule("US-DATE-05")
    def test_questrade_cad_settled_us_stock_settles_on_us_calendar(self):
        sym, settle = _qt_settle(
            "2025-06-30", "QZA",  "CAD",
            desc="QZA INC WE ACTED AS AGENT CROSS CURRENCY TRADE "
                 "EXCHANGE RATE 1.3500", net="-135")
        self.assertEqual((sym, settle), ("QZA.US", "2025-07-01"))

    @rule("CA-DATE-05")
    def test_rbc_usd_dlr_unit_settles_on_canadian_calendar(self):
        self.assertEqual(
            _rbc_settle("June 30, 2025", "DLR.U", "QZ U.S. DLR CURRENCY ETF",
                        "USD"), ("DLR.U.TO", "2025-07-02"))

    @rule("CA-DATE-05")
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

    @rule("CA-DATE-04")
    def test_generic_usd_line_on_the_lse_uses_the_uk_cycle(self):
        # T+2 in the UK until 2027-10-11; the US T+1 cycle gave 06-03.
        self.assertEqual(_gen_settle("2025-06-02", "QZL.L", "USD")[1],
                         "2025-06-04")


if __name__ == "__main__":
    unittest.main()

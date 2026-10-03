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


# ------------------------------------------ A2-1056: hints the map answers
import os
import subprocess
import sys

from test_fix_rbcqt import QH
from test_fix_rbc import HDR as RBC_HDR, ORCX_ROWS
from test_fix_m_parsers2_webull import _PRE as WB_PRE, _H25 as WB_H25

REPO = Path(__file__).resolve().parent.parent

QT_RENAME = (q(sym='QQOL', desc='QQ HOLDINGS CORP WE ACTED AS AGENT',
               qty='500', price='10', gross='-5000', comm='0', net='-5000')
             + q(td='2025-09-10', action='Sell', sym='QQNW',
                 desc='QQ HOLDINGS CORP WE ACTED AS AGENT', qty='-500',
                 price='20', gross='10000', comm='0', net='10000'))
WB_RENAME = (WB_PRE + WB_H25
             + 'USD,10-03-2025,BUY,QQOL,QQ HOLDINGS CORP,SHS,500,10.00,,'
               '"(5,002.99)"\n'
             'USD,12-06-2025,SELL,QQNW,QQ HOLDINGS CORP,SHS,-500,20.00,,'
             '"9,997.01"\n')


def _brokerage(brokerage, files, tmap=None, *extra):
    """taxjson-brokerage over {name: text} (+ a ticker.map text):
    (rc, stderr)."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            paths.append(str(p))
        args = list(extra)
        if tmap is not None:
            m = Path(td) / 'ticker.map'
            m.write_text(tmap)
            args += ['--ticker-map', str(m)]
        env = dict(os.environ, PYTHONPATH=str(REPO / 'src'))
        r = subprocess.run(
            [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
             '--brokerage', brokerage, *args, *paths],
            capture_output=True, text=True, cwd=REPO, env=env,
            stdin=subprocess.DEVNULL)
    return r.returncode, r.stderr


class TestTickerChangeHintMootOnceMapped(unittest.TestCase):
    """A2-1056: the Questrade, RBC and Webull ticker-change hints (and
    RBC's untraded-income listing hint) kept printing after the
    suggested ticker.map line was added: the parsers never saw the map.
    taxjson-brokerage --ticker-map (passed by run) drops a hint whose
    pair the map already joins; --lint keeps it."""

    CASES = (
        ('questrade', {'questrade_2025.csv': QH + QT_RENAME},
         'GLOBAL QQOL.US QQNW.US', 'looks renamed'),
        ('rbc', {'rbc.csv': RBC_HDR + ''.join(ORCX_ROWS)},
         'GLOBAL ORCX.US OBDX.US', 'looks renamed'),
        ('webull', {'wb.csv': WB_RENAME},
         'RENAME QQOL.US QQNW.US 2025-12-06', 'ticker change Webull'),
    )

    def test_hint_without_a_map(self):
        for brk, files, _line, hint in self.CASES:
            rc, err = _brokerage(brk, files)
            self.assertEqual(rc, 0, err)
            self.assertIn(hint, err, brk)

    def test_hint_dropped_once_the_map_joins_the_pair(self):
        for brk, files, line, hint in self.CASES:
            rc, err = _brokerage(brk, files, line + '\n')
            self.assertEqual(rc, 0, err)
            self.assertNotIn(hint, err, brk)

    def test_dated_rename_and_chain_also_join(self):
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'RENAME QQOL.US QQNW.US 2025-09-01\n')
        self.assertNotIn('looks renamed', err)
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'GLOBAL QQOL.US QQMID.US\n'
                             'GLOBAL QQNW.US QQMID.US\n')
        self.assertNotIn('looks renamed', err)

    def test_unrelated_map_line_keeps_the_hint(self):
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'GLOBAL QQOL.US QQOTHER.US\n')
        self.assertIn('looks renamed', err)

    def test_lint_keeps_the_hint(self):
        rc, err = _brokerage('questrade', {'q.csv': QH + QT_RENAME},
                             'GLOBAL QQOL.US QQNW.US\n', '--lint')
        self.assertIn('looks renamed', err)

    def test_rbc_untraded_income_hint_moot_once_mapped(self):
        div = rbc_row("June 2, 2025", "Dividend", "QZT", "QZT CORP", "", "",
                      "12.00", "USD", "QZT CORP RETURN OF CAPITAL")
        rc, err = _brokerage('rbc', {'rbc.csv': RBC_HDR + div})
        self.assertIn('no trade rows for QZT', err)
        rc, err = _brokerage('rbc', {'rbc.csv': RBC_HDR + div},
                             'TOBASE QZT.US QZT.TO\n')
        self.assertNotIn('no trade rows for QZT', err)

    def test_run_passes_the_map(self):
        from test_fix_a2_rbcqt import _project, _cli_run
        with tempfile.TemporaryDirectory() as d:
            body = QT_RENAME.replace(',USD,', ',CAD,')
            p = _project(Path(d) / 'p', 2025,
                         {'questrade_2025.csv': QH + body},
                         ticker_map='GLOBAL QQOL.TO QQNW.TO\n')
            r = _cli_run(p, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('looks renamed', r.stdout + r.stderr)
            sums = ''.join(f.read_text() for f in
                           (p / 'reports').rglob('*.sum'))
            self.assertTrue(sums)
            self.assertNotIn('looks renamed', sums)


if __name__ == "__main__":
    unittest.main()

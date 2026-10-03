"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers,
fake account ids (55500001, U5550001) and made-up amounts."""  # pii-ok
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

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


# --------------------------- A2-1091: side-effect lines of an undone event
from test_fix_ibparse import CA_H, _ca
from test_fix_a2_ib import _stmt, _booked

SD = ('QZT(US9990000501) Stock Dividend US9990000501 1 for 10 '
      '(QZT, QZT CORP, US9990000501)')
CT = 'QZT(US9990000501) Merged(Acquisition) FOR USD 30.00 PER SHARE'


class TestCrossStatementCaUndoSilencesSideEffects(unittest.TestCase):
    """A2-1091: a stock dividend (or cash takeover) in the 2025 statement
    cancelled by a Ca in the 2026 one: the row was removed, but the
    2025 parse had already printed its stock-dividend ATTENTION /
    cash-takeover NOTE. Under taxjson-brokerage the lines wait for the
    cross-statement pass and an undone event's line is not printed."""

    BUY = TRADES_H + _trade('QZT', '2025-01-10, 10:00:00', 100, 30, -3000)

    def _files(self, desc, qty, proceeds=0, value=0):
        a = _stmt('January 1, 2025', 'December 31, 2025', self.BUY, CA_H,
                  _ca(desc, qty, proceeds=proceeds, value=value,
                      when='2025-12-30, 20:25:00'))
        b = _stmt('January 1, 2026', 'March 31, 2026', CA_H,
                  _ca(desc, -qty, proceeds=-proceeds, value=-value,
                      when='2026-01-05, 20:25:00', code='Ca'))
        return a, b

    def test_undone_stock_dividend_prints_no_attention(self):
        a, b = self._files(SD, 10, value=300)
        rows, err = _booked({'a.csv': a})
        self.assertIn('stock dividend of 10', err)        # control
        rows, err = _booked({'a.csv': a, 'b.csv': b})
        self.assertIn('is undone', err)
        self.assertNotIn('stock dividend of 10', err)
        # The Ca row the pass paired is not a skipped row either.
        self.assertNotIn('whose original is not in this statement', err)
        self.assertEqual(_position_of(rows, 'QZT.US'), 100.0)

    def test_undone_cash_takeover_prints_no_note(self):
        a, b = self._files(CT, -100, proceeds=3000)
        rows, err = _booked({'a.csv': a})
        self.assertIn('cash takeover booked as a sale', err)  # control
        rows, err = _booked({'a.csv': a, 'b.csv': b})
        self.assertIn('is undone', err)
        self.assertNotIn('cash takeover booked as a sale', err)

    def test_live_event_still_prints_once(self):
        a, _b = self._files(SD, 10, value=300)
        c = _stmt('January 1, 2026', 'March 31, 2026', self.BUY)
        rows, err = _booked({'a.csv': a, 'c.csv': c})
        self.assertEqual(err.count('stock dividend of 10'), 1, err)


def _position_of(rows, sym):
    return sum(t['quantity'] for t in rows
               if t['symbol'] == sym and t['action'] in ('BUYSELL',
                                                         'ASSIGN'))


# ----------------------------- A2-0590: de-peg check on non-USD-valued fills
from test_fix_a2_coinbase import _parse as cb_parse
from test_fix_a2_kraken import (_parse as kr_parse, _ledger as kr_ledger,
                                _KT_H)

RATES = ("2025-06-02 12:00:00 USD CAD 1.3500 boc\n"
         "2025-06-02 12:00:00 EUR CAD 1.5000 boc\n")


class _WithRates:
    """Install a rates table (the run's work/to_base.csv) the way
    taxjson-brokerage --rates does."""

    def setUp(self):
        from taxjson.lib.brokerages import _crypto_common as cc
        self._td = tempfile.TemporaryDirectory()
        rp = Path(self._td.name) / "to_base.csv"
        rp.write_text(RATES)
        cc.set_depeg_rates(rp, "CAD")
        self.addCleanup(cc.set_depeg_rates, None, None)
        self.addCleanup(self._td.cleanup)


def _cb(typ, qty, price, ccy="CAD"):
    sub = abs(qty) * price
    return (f"c1,2025-06-02 12:00:00 UTC,{typ},USDC,{qty},{ccy},"
            f"${price:.2f},${sub:.2f},${sub:.2f},$0.00,"
            f"{typ} {abs(qty)} USDC\n")


@rule("CA-CRYPTO-02")
class TestDepegNonUsdFills(_WithRates, unittest.TestCase):
    """A2-0590: only USD-valued stablecoin fills were checked against the
    peg. A fill valued in CAD or EUR is now converted through the day's
    rate (the run's rates file, the conversion stage's own) first."""

    def test_coinbase_buy_and_sell_usdc_priced_in_cad(self):
        for typ, qty in (("Buy", 100), ("Sell", -100)):
            _tx, err, _ = cb_parse(_cb(typ, qty, 1.20))
            # 1.20 CAD / 1.35 = 0.8889 USD
            self.assertIn("USDC traded at 0.8889 USD", err, typ)
        _tx, err, _ = cb_parse(_cb("Buy", 100, 1.35))
        self.assertNotIn("traded at", err)

    def test_kraken_stablecoin_fiat_pairs(self):
        def trade(pair, price, vol):
            return _KT_H + (f"T1,O1,{pair},2025-06-02 16:00:00,buy,limit,"
                            f"{price},{price * vol:.2f},0,{vol},,,\n")
        _t, err = kr_parse({"kr_trades.csv": trade("USDC/CAD", 1.20, 100)},
                           "kr_trades.csv")
        self.assertIn("USDC traded at 0.8889 USD", err)
        # 0.70 EUR x 1.50 / 1.35 = 0.7778 USD
        _t, err = kr_parse({"kr_trades.csv": trade("USDT/EUR", 0.70, 100)},
                           "kr_trades.csv")
        self.assertIn("USDT traded at 0.7778 USD", err)
        _t, err = kr_parse({"kr_trades.csv": trade("USDT/EUR", 0.90, 100)},
                           "kr_trades.csv")
        self.assertNotIn("traded at", err)
        # CAD/USDC: the stablecoin is the quote, 1 / price CAD each:
        # 1 / 0.80 = 1.25 CAD = 0.9259 USD; 1 / 0.74 = 1.0010 USD.
        _t, err = kr_parse({"kr_trades.csv": trade("CAD/USDC", 0.80, 100)},
                           "kr_trades.csv")
        self.assertIn("USDC traded at 0.9259 USD", err)
        _t, err = kr_parse({"kr_trades.csv": trade("CAD/USDC", 0.74, 100)},
                           "kr_trades.csv")
        self.assertNotIn("traded at", err)

    def test_kraken_ledger_instant_conversion_to_cad(self):
        led = kr_ledger([
            "L1,R1,2025-06-02 12:00:00,spend,,currency,USDC,spot,-100,0,0",
            "L2,R1,2025-06-02 12:00:00,receive,,currency,ZCAD,spot,120,0,"
            "120"])
        _t, err = kr_parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIn("USDC traded at 0.8889 USD", err)

    def test_no_rate_for_the_currency_is_said_not_guessed(self):
        from taxjson.lib.brokerages import _crypto_common as cc
        cc.set_depeg_rates(None, None)
        _tx, err, _ = cb_parse(_cb("Buy", 100, 1.20, ccy="GBP"))
        self.assertNotIn("traded at", err)
        self.assertIn("not checked for a de-peg", err)


class TestDepegIsCanadasCashModelOnly(_WithRates, unittest.TestCase):
    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    @rule("US-CRYPTO-02")
    def test_us_books_the_cad_priced_usdc_purchase_instead(self):
        _tx, err, _ = cb_parse(_cb("Buy", 100, 1.20))
        self.assertIn("traded at", err)                       # Canada
        tx, err, _ = cb_parse(_cb("Buy", 100, 1.20),
                              stablecoins_as_cash=False)      # USA
        self.assertNotIn("traded at", err)
        self.assertEqual([(t["symbol"], t["quantity"]) for t in tx
                          if t["action"] == "BUYSELL"], [("USDC", 100.0)])


@rule("CA-CRYPTO-02")
class TestDepegRatesThroughTheCli(unittest.TestCase):
    def test_brokerage_rates_flag(self):
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "to_base.csv"
            rp.write_text(RATES)
            from test_fix_a2_coinbase import _HDR
            rc, err = _brokerage('coinbase',
                                 {'cb.csv': "Transactions\n" + _HDR
                                  + _cb("Buy", 100, 1.20)},
                                 None, '--country', 'canada',
                                 '--rates', str(rp))
        self.assertEqual(rc, 0, err)
        self.assertIn("USDC traded at 0.8889 USD", err)

    def test_run_passes_its_rates(self):
        from test_fix_crypto import CB_HEADER, _env, _project, _run_cli
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)          # USD/CAD 1.40 seeded
            row = ("c1,2026-03-02 12:00:00 UTC,Buy,USDC,100,CAD,$1.20,"
                   "$120.00,$120.00,$0.00,Bought 100 USDC\n")
            (root / "inputs" / "crypto" / "cb_2026.csv").write_text(
                "Transactions\n" + CB_HEADER + row)
            r = _run_cli(root, "run", "--no-input",
                         env=_env(Path(td), TAXJSON_OFFLINE="1"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 1.20 / 1.40 = 0.8571 USD, on the console (ATTENTION).
        self.assertIn("USDC traded at 0.8571 USD", r.stdout + r.stderr)


# --------------------------- A2-1014: fx-cash sees a corp action's cash
from taxjson.bin.taxjson_fx_cash import build_ledger
from taxjson.lib.core import coerce_transaction_row
from taxjson.lib import corp_actions as CA_


def _corp_event(**kw):
    base = dict(date='2025-10-22', time='09:30:00', action_type='merger',
                source_symbol='QZOLD.US', source_isin='US9990003001',
                target_symbol='QZNEW.US', target_isin='US9990003002',
                ratio_new=1, ratio_old=10, qty_disposed=1000.0,
                qty_received=100.0, fmv=5000.0, currency='USD',
                target_fmv=5000.0, target_currency='USD',
                account='margin', event_id='ev1')
    base.update(kw)
    return CA_.CorporateAction(**base)


def _usd_acquired(rows):
    """Rows through the books' loader (unknown keys dropped), then the
    fx-cash ledger: the USD the event put in the pool."""
    loaded = [coerce_transaction_row(dict(r, corp_event_id='ev1'), i,
                                     'test').to_dict()
              for i, r in enumerate(rows)]
    doc = build_ledger(loaded, 'CAD', {}, 2025,
                       rate_of=lambda c, d: 1.35)
    return doc['per_currency'].get('USD', {}).get('acquired', 0.0)


@rule("CA-FX-07")
class TestFxCashCountsCorpActionCash(unittest.TestCase):
    """A2-1014: cash a corporate action paid (cash in lieu folded into a
    sale's proceeds, boot, a spin-off's fractional share) was classified
    by description text, so only the s.85.1 path's standalone leg was
    seen. The emitters now put the cash on the row as structured
    evidence (corp_cash) and fx-cash counts it."""

    def test_taxable_merger_cash_in_lieu(self):
        ev = _corp_event(cash_in_lieu=25.0, cash_in_lieu_currency='USD')
        rows = CA_._emit_taxable_exchange(ev, {}, description_base='M')
        self.assertAlmostEqual(_usd_acquired(rows), 25.0)

    def test_all_fractional_taxable_merger_is_all_cash(self):
        ev = _corp_event(qty_disposed=5.0, qty_received=0.5,
                         target_fmv=25.0)
        rows = CA_._emit_taxable_exchange(ev, {}, description_base='M')
        self.assertAlmostEqual(_usd_acquired(rows), 25.0)

    def test_deemed_dividend_spinoff_fraction(self):
        ev = _corp_event(action_type='spinoff', qty_received=10.5,
                         target_fmv=210.0)
        rows = CA_._canada_spinoff_deemed_dividend(ev, 'x', {})
        # 0.5 share x 20.00 paid in cash
        self.assertAlmostEqual(_usd_acquired(rows), 10.0)

    def test_allocated_spinoff_fraction(self):
        ev = _corp_event(action_type='spinoff', qty_received=10.5,
                         target_fmv=210.0)
        rows = CA_._emit_allocated_basis_spinoff(
            ev, {'allocated_acb': 100.0}, description_base='S')
        self.assertAlmostEqual(_usd_acquired(rows), 10.0)


    def test_share_for_share_legs_stay_non_cash(self):
        ev = _corp_event()
        rows = CA_._emit_taxable_exchange(ev, {}, description_base='M')
        self.assertAlmostEqual(_usd_acquired(rows), 0.0)

    def test_rollover_standalone_leg_still_counts_once(self):
        ev = _corp_event(qty_received=99.0, cash_in_lieu=20.0,
                         cash_in_lieu_currency='USD')
        rows = CA_._canada_merger_rollover(ev, 'rollover_s_85_1_5', {})
        self.assertAlmostEqual(_usd_acquired(rows), 20.0)


class TestFxCashCountsBoot(unittest.TestCase):
    @rule("US-FX-03")
    def test_boot_exchange_cash(self):
        ev = _corp_event()
        rows = CA_._emit_boot_exchange(
            ev, {'cash_boot': 1000.0, 'source_basis_total': 4000.0})
        self.assertAlmostEqual(_usd_acquired(rows), 1000.0)


if __name__ == "__main__":
    unittest.main()

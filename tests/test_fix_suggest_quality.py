"""`taxjson ticker-map --suggest` quality: two kinds of invalid suggestion
a new user's run produced. Synthetic data only (invented tickers, conids,
ISINs and names).

1. IB lists one contract under its ticker AND a TEMPORARY symbol (a time
   stamp YYYYMMDDHHMMSS before the ticker, given around a corporate
   action). The old hint ("old symbol first, as first traded") renamed
   the real ticker TO the temporary one. Now the temporary symbol is
   folded onto the ticker automatically (one Info line, no map line), and
   a genuine ticker change under one contract id is suggested as a DATED
   rename (`RENAME OLD NEW YYYY-MM-DD`, renames are events), never toward
   a temporary symbol.
2. One book symbol carrying two different companies' names (a TSX
   currency ETF's US-dollar unit symbolised `.US`, colliding with an NYSE
   stock of the same root held at another broker): no TOBASE / JOURNAL
   suggestion between listings whose names name different companies; the
   collision is a Warning and an EXTRACT line that separates the odd rows
   (plus the JOURNAL pairing the separated unit with its CAD line).
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.ib_extractor import (IbBrokerage,
                                                 ib_temp_symbol_ticker)
from tax_rules import rule

from test_fix_ibparse import (HEAD, TRADES_H, CA_H, FII_H, _trade, _ca,
                              _parse_ib, _parse_account)

STAMP = '20260626175508'


def _fii(syms, conid, isin, name='QZHN CORP', exch='NYSE'):
    return (f'Financial Instrument Information,Data,Stocks,"{syms}",{name},'
            f'{conid},{isin},,{exch},1,,,COMMON,,\n')


class TestIbTemporarySymbol(unittest.TestCase):
    """A contract listed as QZHN and 2026...QZHN: one security."""

    BODY = (HEAD + TRADES_H
            + _trade('QZHN', '2026-06-01, 10:00:00', 20, 50, -1000)
            + _trade(f'{STAMP}QZHN', '2026-06-29, 10:00:00', -5, 55, 275,
                     code='C')
            + FII_H + _fii(f'QZHN, {STAMP}QZHN', '990000777',
                           'US9990007771'))

    def test_the_stamp_is_recognised(self):
        self.assertEqual(ib_temp_symbol_ticker(f'{STAMP}QZHN'), 'QZHN')
        self.assertEqual(ib_temp_symbol_ticker(f'{STAMP}QZB.B'), 'QZB.B')
        for s in ('QZHN', '2026QZHN', '20261399175508QZHN', f'{STAMP}'):
            with self.subTest(s=s):
                self.assertIsNone(ib_temp_symbol_ticker(s))

    @rule("CA-ACB-RENAME")
    def test_folded_onto_the_ticker_with_no_suggestion(self):
        for how in ('account', 'lone'):
            with self.subTest(how=how):
                if how == 'account':
                    txs, err = _parse_account({'ib.csv': self.BODY})
                else:
                    _p, txs, err = _parse_ib(self.BODY)
                self.assertEqual({t['symbol'] for t in txs}, {'QZHN.US'},
                                 err)
                self.assertEqual(sum(t['quantity'] for t in txs), 15)
                self.assertNotIn('several symbols', err)
                self.assertNotIn('`', err)
                self.assertEqual(err.count('temporary symbol'), 1, err)
                self.assertIn(f'{STAMP}QZHN is IB\'s temporary symbol for '
                              f'QZHN', err)

    def test_fold_spans_the_account_statements(self):
        # The instrument list naming both spellings is in another
        # statement than the stamped trade.
        a = (HEAD + TRADES_H
             + _trade('QZHN', '2026-06-01, 10:00:00', 20, 50, -1000)
             + FII_H + _fii(f'QZHN, {STAMP}QZHN', '990000777',
                            'US9990007771'))
        b = (HEAD + TRADES_H
             + _trade(f'{STAMP}QZHN', '2026-06-29, 10:00:00', -5, 55, 275,
                      code='C')
             + FII_H + _fii(f'{STAMP}QZHN', '990000777', 'US9990007771'))
        txs, err = _parse_account({'a.csv': a, 'b.csv': b})
        self.assertEqual({t['symbol'] for t in txs}, {'QZHN.US'}, err)
        self.assertNotIn('several symbols', err)

    def test_stamped_description_is_folded(self):
        body = (self.BODY + CA_H
                + _ca(f'QZHN(US9990007771) Split 2 for 1 ({STAMP}QZHN, '
                      f'QZHN CORP, US9990007771)', 15,
                      when='2026-06-30, 20:25:00'))
        txs, err = _parse_account({'ib.csv': body})
        self.assertEqual({t['symbol'] for t in txs}, {'QZHN.US'}, err)

    def test_stamp_of_another_ticker_is_never_a_target(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2026-06-01, 10:00:00', 20, 50, -1000)
                + _trade(f'{STAMP}QZBB', '2026-06-29, 10:00:00', -5, 55,
                         275, code='C')
                + FII_H + _fii(f'QZAA, {STAMP}QZBB', '990000778',
                               'US9990007781'))
        txs, err = _parse_account({'ib.csv': body})
        self.assertIn(f'{STAMP}QZBB.US',
                      {t['symbol'] for t in txs})
        self.assertIn('several symbols', err)
        self.assertIn('IB temporary symbol', err)
        self.assertNotIn('`', err)            # no ticker.map line offered


class TestIbTickerChangeIsADatedRename(unittest.TestCase):
    """A genuine ticker change under one contract id: `RENAME OLD NEW
    YYYY-MM-DD`, OLD the symbol whose rows end first, the date NEW's
    first row — never GLOBAL."""

    def _err(self, body, tmap=None):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / 'inputs' / 'margin'
            acct.mkdir(parents=True)
            p = acct / 'ib.csv'
            p.write_text(body)
            if tmap:
                (Path(td) / 'ticker.map').write_text(tmap)
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([p])
        return err.getvalue()

    # NEW (QZNB) sorts before OLD (QZOA) and is listed first: the order
    # comes from the rows' dates.
    BODY = (HEAD + TRADES_H
            + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
            + _trade('QZOA', '2025-04-01, 10:00:00', 50, 11, -550)
            + _trade('QZNB', '2025-05-12, 10:00:00', -150, 12, 1800,
                     code='C')
            + FII_H + _fii('QZNB, QZOA', '990000779', 'US9990007791'))

    @rule("CA-ACB-RENAME")
    def test_dated_rename_suggested(self):
        err = self._err(self.BODY)
        self.assertIn('`RENAME QZOA.US QZNB.US 2025-05-12`', err)
        self.assertNotIn('GLOBAL', err)

    def test_quiet_once_ticker_map_has_the_dated_rename(self):
        self.assertNotIn('several symbols', self._err(
            self.BODY, 'RENAME QZOA.US QZNB.US 2025-05-12\n'))

    def test_suggest_reads_the_dated_line(self):
        from taxjson.lib import ticker_map_suggest as TS
        err = self._err(self.BODY)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'work').mkdir()
            (root / 'work' / 'margin_ib.json.diag').write_text(err)
            offer, _ = TS.pending(root)
            self.assertEqual([s.line for s in offer],
                             ['RENAME QZOA.US QZNB.US 2025-05-12'])
            (root / 'ticker.map').write_text(
                'RENAME QZOA.US QZNB.US 2025-05-12\n')
            offer, skipped = TS.pending(root)
            self.assertEqual(offer, [])
            (root / 'ticker.map').write_text(
                'RENAME QZOA.US QZNB.US 2025-05-11\n')
            offer, skipped = TS.pending(root)
            self.assertEqual(offer, [])
            self.assertIn('renames', skipped[0][1])


if __name__ == '__main__':
    unittest.main()

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



# ---------------------------------------------------- symbol collisions
from taxjson.lib import cross_listings as XL               # noqa: E402
from taxjson.lib.brokerages.base import extract_words_match  # noqa: E402
from taxjson.lib.symbol_codes import exact_name            # noqa: E402

# A synthetic TSX currency fund with the shapes the brokers write: an
# older RBC export under the fund's former brand ("U S DLR"), Questrade
# under the new one ("US DLR ... CL A"), the journal in-leg with RBC's
# transfer wording.
RBC_OLD = 'QZOLDBRAND U S DLR CURRENCY ETF UNIT UNSOLICITED DA'
RBC_OUT = 'TFR - QZOLDBRAND U S DLR CURRENCY ETF UNIT TRANSFER TO C$ J~1'
RBC_IN = 'TFR - QZOLDBRAND U S DLR CURRENCY ETF UNIT TRANSFER FROM U$ J~1'
QT_CODE = 'SAMPLEX US DLR CURRENCY ETF UNIT CL A WE ACTED AS AGENT'
QT_UNIT = 'SAMPLEX US DLR CURRENCY ETF UNIT CL A JOURNAL POSITION FROM CAD'
QT_OTHER = 'SAMPLEX US EQUITY ETF UNIT CL A WE ACTED AS AGENT'
IB_NAME = 'QZREALTY TRUST INC'


def _row(acct, broker, sym, cur, desc, name=None, action='BUYSELL'):
    from taxjson.lib.symbol_codes import questrade_name, rbc_name
    if name is None:
        name = (rbc_name(desc, trade=True) if broker == 'rbc_direct'
                else questrade_name(desc) if broker == 'questrade'
                else desc)
    return XL.Row(acct, broker, sym, cur, desc, exact_name(name), action,
                  name)


def _book():
    rows = [_row('margin', 'rbc_direct', 'QZD.US', 'USD', RBC_OLD),
            _row('margin', 'rbc_direct', 'QZD.US', 'USD', RBC_OUT),
            _row('margin', 'rbc_direct', 'QZD.TO', 'CAD', RBC_IN),
            _row('tfsa', 'questrade', 'G012345', 'USD', QT_CODE),
            _row('tfsa', 'questrade', 'QZD.U.TO', 'USD', QT_UNIT),
            _row('tfsa', 'questrade', 'QZE.TO', 'CAD', QT_OTHER),
            _row('rrsp', 'ib', 'QZD.US', 'USD', 'QZD', IB_NAME),
            _row('rrsp', 'ib', 'QZD.US', 'USD', 'QZD', IB_NAME)]
    names, shown = {}, {}
    for r in rows:
        if r.symbol == 'G012345':
            continue
        names.setdefault(r.symbol, set()).add(r.key)
        shown.setdefault(r.key, r.description)
    legs = [XL.Leg('margin', 'rbc_direct', 'QZD.US', '2025-03-05', -500),
            XL.Leg('margin', 'rbc_direct', 'QZD.TO', '2025-03-05', 500)]
    return rows, names, shown, legs


class TestSymbolCollision(unittest.TestCase):
    """One book symbol, two companies: never a TOBASE suggestion; the
    EXTRACT line that separates them, its words read from every row of
    the moved security (any broker) and none of another's."""

    def test_companies_differ(self):
        k = exact_name
        self.assertTrue(XL.companies_differ(k(IB_NAME), k(RBC_OLD)))
        # A rebranded fund shares its other words: inconclusive.
        self.assertFalse(XL.companies_differ(k(RBC_OLD), k(QT_UNIT)))
        self.assertFalse(XL.companies_differ(k('QZCO CORP'),
                                             k('QZCO CORP CL B')))

    @rule("CA-XLIST-01")
    def test_extract_and_journal_are_suggested(self):
        rows, names, shown, legs = _book()
        cs = XL.collisions(rows, names, shown, legs, base_currency='CAD')
        self.assertEqual(len(cs), 1)
        c = cs[0]
        self.assertEqual(c.symbol, 'QZD.US')
        self.assertFalse(c.template)
        self.assertEqual(c.extract, 'EXTRACT DLR CURRENCY ETF | USD | '
                                    'QZD.U.TO')
        self.assertEqual(c.journal, 'JOURNAL QZD.U.TO QZD.TO')
        words = c.extract.split('|')[0][len('EXTRACT '):].strip()
        for d in (RBC_OLD, RBC_OUT, RBC_IN, QT_CODE, QT_UNIT):
            self.assertTrue(extract_words_match(words, d), d)
        for d in (QT_OTHER, 'QZD', IB_NAME):
            self.assertFalse(extract_words_match(words, d), d)
        # The pair is left to the EXTRACT line: no TOBASE suggestion.
        r = XL.analyze(legs, names, shown, base_currency='CAD',
                       collided=[c.symbol])
        self.assertEqual(r, {'joined': [], 'suggested': []})
        head, det = XL.collision_note(c)
        self.assertTrue(head.startswith('QZD.US names two securities: '))
        self.assertTrue(head.endswith('— add the EXTRACT line '
                                      '(`taxjson ticker-map --suggest`)'))
        self.assertIn('  EXTRACT DLR CURRENCY ETF | USD | QZD.U.TO', det)

    @rule("US-XLIST-01")
    def test_usa_collided_symbol_is_never_joined_or_suggested(self):
        rows, names, shown, legs = _book()
        cs = XL.collisions(rows, names, shown, legs, base_currency='USD')
        self.assertEqual([c.symbol for c in cs], ['QZD.US'])
        # A US base currency: the CAD line maps onto the USD unit.
        self.assertEqual(cs[0].journal, 'JOURNAL QZD.TO QZD.U.TO')
        r = XL.analyze(legs, names, shown, base_currency='USD',
                       collided=['QZD.US'])
        self.assertEqual(r, {'joined': [], 'suggested': []})

    def test_names_alone_are_no_collision(self):
        # A company that renamed itself: two names, one security. Without
        # a fund's US-dollar units on one side there is no collision.
        rows = [_row('margin', 'webull', 'QZM.US', 'USD',
                     'QZMICRO STRATEGIES INC CLASS A'),
                _row('rrsp', 'ib', 'QZM.US', 'USD', 'QZM', 'QZSTRAT INC')]
        for r in rows:
            r.action = 'BUYSELL'
        names = {'QZM.US': {r.key for r in rows}}
        shown = {r.key: r.description for r in rows}
        self.assertEqual(XL.collisions(rows, names, shown, []), [])

    def test_words_that_cannot_be_derived_are_a_template(self):
        rows, names, shown, legs = _book()
        # Another security's row carries the fund's whole name.
        rows.append(_row('tfsa', 'questrade', 'QZQ.TO', 'CAD',
                         'SWITCHED VIA QZOLDBRAND U S DLR CURRENCY ETF UNIT '
                         'UNSOLICITED DA', 'SWITCHED VIA QZOTHER'))
        c, = XL.collisions(rows, names, shown, legs, base_currency='CAD')
        self.assertTrue(c.template)
        self.assertEqual(c.extract, 'EXTRACT <words that name it> | USD | '
                                    'QZD.U.TO')
        self.assertIn('replace the placeholder', c.why)

    def test_suggest_lists_collision_lines_and_never_writes_a_template(self):
        from taxjson.lib import ticker_map_suggest as TS
        rows, names, shown, legs = _book()
        cs = XL.collisions(rows, names, shown, legs, base_currency='CAD')
        tmpl = XL.Collision('QZK.US', ['A CORP', 'B FUND'], ['x', 'y'],
                            'B FUND', 'EXTRACT <words that name it> | USD | '
                            'QZK.U.TO', True, '', 'replace the placeholder')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'work').mkdir()
            (root / 'work' / XL.STATE).write_text(XL.state_text(
                {'joined': [], 'suggested': [], 'collisions': cs + [tmpl]}))
            # RBC's own hint for the same rows: one EXTRACT per listing.
            (root / 'work' / 'margin_rbc.json.diag').write_text(
                "warning: ATTENTION: rbc.csv: QZD reads as the US-dollar "
                "class of a TSX-listed fund. If it trades on the TSX, add "
                "to ticker.map:  EXTRACT QZOLDBRAND U S DLR CURRENCY ETF "
                "| USD | QZD.U.TO\n")
            offer, skipped = TS.pending(root)
            self.assertEqual(
                [(s.line, s.template) for s in offer],
                [('EXTRACT DLR CURRENCY ETF | USD | QZD.U.TO', False),
                 ('JOURNAL QZD.U.TO QZD.TO', False),
                 ('EXTRACT <words that name it> | USD | QZK.U.TO', True)])
            self.assertEqual(len(skipped), 1)
            (root / 'ticker.map').write_text(
                'EXTRACT DLR CURRENCY ETF | USD | QZD.U.TO\n'
                'JOURNAL QZD.U.TO QZD.TO\n')
            offer, _ = TS.pending(root)
            self.assertEqual([s.line for s in offer],
                             ['EXTRACT <words that name it> | USD | '
                              'QZK.U.TO'])
            from tax_rules.dual import cli
            (root / 'taxjson.toml').write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n')
            r = cli(root, 'ticker-map', '--suggest', '--write', '--all')
            self.assertIn('Not added (a template', r.stdout + r.stderr)
            self.assertNotIn('<words', (root / 'ticker.map').read_text())


class TestCollisionRun(unittest.TestCase):
    """End to end: an RBC currency-fund journal whose USD leg is booked
    .US, and an IB NYSE stock of the same root (another company)."""

    def _project(self, td, tmap=None):
        from test_fix_rbc import HDR, row
        from tax_rules.dual import projects_both
        n = 'QZOLDBRAND U S DLR CURRENCY ETF UNIT'
        rbc = ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
               + HDR
               + row("March 3, 2025", "Buy", "QZD", n, "500", "10", "-5000",
                     "USD", n + " UNSOLICITED DA")
               + row("March 5, 2025", "Transfers", "QZD", n, "-500", "",
                     "0", "USD", "TFR - " + n + " TRANSFER TO C$  J~1")
               + row("March 5, 2025", "Transfers", "QZD", n, "500", "", "0",
                     "CAD", "TFR - " + n + " TRANSFER FROM U$  J~1")
               + row("March 6, 2025", "Sell", "QZD", n, "-500", "14",
                     "7000", "CAD", n + " UNSOLICITED CA JNL"))
        ib = (HEAD + 'Statement,Data,Period,"January 1, 2025 - December '
              '31, 2025"\n' + TRADES_H
              + _trade('QZD', '2025-02-03, 10:00:00', 10, 180, -1800)
              + _trade('QZD', '2025-08-04, 10:00:00', -10, 190, 1900,
                       code='C')
              + FII_H + _fii('QZD', '990000901', 'US9990009011',
                             name=IB_NAME))
        files = {"inputs/margin/rbc.csv": rbc, "inputs/rrsp/ib.csv": ib}
        if tmap:
            files["ticker.map"] = tmap
        return projects_both(
            Path(td), files=files,
            accounts=('[accounts.margin]\ntype = "taxable"\n'
                      '[accounts.rrsp]\ntype = "sheltered"\n'),
            canada={"source_currencies": []},
            usa={"source_currencies": []})["canada"]

    @rule("CA-XLIST-01")
    def test_collision_is_a_warning_and_an_extract_suggestion(self):
        from tax_rules.dual import cli
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = cli(root, 'run', '--no-input')     # no rates offline
            out = ' '.join((r.stdout + r.stderr).split())
            self.assertIn("QZD.US names two securities: ", out)
            self.assertIn("'QZREALTY TRUST INC' in rrsp at Interactive "
                          "Brokers", out)
            self.assertIn("add the EXTRACT line (`taxjson ticker-map "
                          "--suggest`)", out)
            st = XL.read_state(root / 'work' / XL.STATE)
            self.assertEqual(st['suggested'], [])
            js = json.loads(cli(root, 'ticker-map', '--suggest',
                                '--json').stdout)
            lines = [s['line'] for s in js['suggestions']]
            self.assertEqual(lines, [
                'EXTRACT DLR CURRENCY ETF | USD | QZD.U.TO',
                'JOURNAL QZD.U.TO QZD.TO'])
            self.assertFalse(any(x.startswith('TOBASE') for x in lines))
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, 'EXTRACT DLR CURRENCY ETF | USD | '
                                     'QZD.U.TO\nJOURNAL QZD.U.TO QZD.TO\n')
            r = cli(root, 'run', '--no-input')
            self.assertNotIn('names two securities', r.stdout + r.stderr)
            st = XL.read_state(root / 'work' / XL.STATE)
            self.assertEqual(st['collisions'], [])


if __name__ == '__main__':
    unittest.main()

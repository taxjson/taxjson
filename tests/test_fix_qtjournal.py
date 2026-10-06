"""Questrade BRW currency journals (tax-logic CA-XLIST-03, CA-ACB-CODES).

Owner report (new-user run, Questrade website CSV, an RRSP): a Norbert's-
gambit journal between an ETF's CAD and USD lines arrives as two BRW rows
named by the BARE symbol --

  BRW, <SYM>, <NAME> JOURNAL POSITION FROM CAD BOOK VALUE: $X CNV@ r, +q, USD
  BRW, <SYM>, <NAME> JOURNAL POSITION TO USD,                         -q, CAD

-- and the later sale of the USD units under Questrade's internal code
(Sell, G0..., "<NAME> WE ACTED AS AGENT", USD). The in-leg became
<SYM>.US (a US listing), the code stayed a security of its own and
find-missing-history showed the code short.

Now the parser pairs the two legs (one account, day, name, quantity),
books the USD leg on the security's US-dollar class (the account's own
USD rows, else the TSX convention SYM.U.TO from data/markets.toml), learns
the code from the journal leg of the same name, and `taxjson run` joins
the two lines as a ticker.map JOURNAL line would (Canada only).

Every fixture is SYNTHETIC: invented QZ* names, symbols and codes, fake
account ids (pii-ok: 55500001, 55500002).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib import markets
from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages import base
from taxjson.lib.brokerages.questrade import (QuestradeBrokerage,
                                              journal_codes, scan_code_uses)
from tax_rules import rule, rule_absent

REPO = Path(__file__).resolve().parent.parent
QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
NAME = 'QZ US DLR CURRENCY ETF UNIT CL A'


def q(td, action, sym, desc, qty, price='0.0', gross='0.0', comm='0.0',
      net='0.0', cur='CAD', act='Other', sd=None, acct='55500001',
      atype='Individual RRSP'):
    sd = sd or td
    return (f"{td} 12:00:00 AM,{sd} 12:00:00 AM,{action},{sym},{desc},{qty},"
            f"{price},{gross},{comm},{net},{cur},{acct},{act},{atype}\n")


def buy_cad(acct='55500001', atype='Individual RRSP', qty='300.0',
            gross='-4260.00', net='-4264.95'):
    return q('2026-09-10', 'Buy', 'QZD.TO', f'{NAME} WE ACTED AS AGENT', qty,
             price='14.20', gross=gross, comm='-4.95', net=net, act='Trades',
             sd='2026-09-11', acct=acct, atype=atype)


def journal_to_usd(qty=300, bv='3016.67', rate='1.4138', **kw):
    """The owner's two row shapes (in-leg first, as exported)."""
    return (q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION FROM CAD '
              f'BOOK VALUE: ${bv} CNV@ {rate}', f'{qty:.1f}', cur='USD', **kw)
            + q('2026-09-25', 'BRW', 'QZD',
                f'{NAME} JOURNAL POSITION TO USD', f'{-qty:.1f}', cur='CAD',
                **kw))


def sell_code(qty=300, code='G000901', price='10.085', **kw):
    g = f'{qty * float(price):.2f}'
    return q('2026-09-30', 'Sell', code, f'{NAME} WE ACTED AS AGENT',
             f'{-qty:.1f}', price=price, gross=g, net=g, cur='USD',
             act='Trades', sd='2026-10-01', **kw)


def qt_parse(*bodies):
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"q{i}.csv"
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs = []
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files(paths)
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                txs.extend(par.parse_file(p))
        return txs, err.getvalue()


def of(txs, **kw):
    return [t for t in txs if all(t.get(k) == v for k, v in kw.items())]


class TestConvention(unittest.TestCase):
    def test_usd_class_of_a_canadian_listing(self):
        self.assertEqual(markets.ca_usd_class('QZD.TO'), 'QZD.U.TO')
        self.assertEqual(markets.ca_usd_class('QZD.U.TO'), 'QZD.U.TO')
        self.assertIsNone(markets.ca_usd_class('QZD.US'))
        self.assertEqual(
            markets.data()['conventions']['ca_usd_class_series'], 'U')


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestParser(unittest.TestCase):
    def test_website_pair_is_booked_on_both_lines_and_learns_the_code(self):
        txs, err = qt_parse(buy_cad() + journal_to_usd() + sell_code())
        legs = of(txs, action='TRANSFER')
        self.assertEqual(sorted((t['symbol'], t['quantity']) for t in legs),
                         [('QZD.TO', -300.0), ('QZD.U.TO', 300.0)])
        self.assertEqual(len({t['journal_pair'] for t in legs}), 1)
        # The book value is in the in-leg's currency (USD); the out-leg
        # carries the same cost in CAD at the stated rate.
        self.assertAlmostEqual(of(legs, symbol='QZD.U.TO')[0]['net_amount'],
                               3016.67)
        self.assertAlmostEqual(of(legs, symbol='QZD.TO')[0]['net_amount'],
                               round(3016.67 * 1.4138, 2))
        sale = of(txs, action='BUYSELL', quantity=-300.0)[0]
        self.assertEqual(sale['symbol'], 'QZD.U.TO')
        self.assertNotIn('keeps internal symbol code', err)
        notes = [ln for ln in err.splitlines() if ln.startswith(SC.NOTE_HEAD)]
        self.assertEqual(len(notes), 1, err)
        got = SC.parse_codes_note(notes[0])
        self.assertEqual(got['G000901'][0], 'QZD.U.TO')
        self.assertIn('journal in-leg of 300 on 2026-09-25', got['G000901'][1])
        self.assertNotIn('no partner', err)

    def test_reverse_partial_journal(self):
        """USD -> CAD, 100 of the 300 units; the code sold in CAD is
        the CAD line."""
        body = (q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION TO '
                  'CAD', '-100.0', cur='USD')
                + q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION '
                    'FROM USD BOOK VALUE: $1420.00 CNV@ 0.7072', '100.0',
                    cur='CAD')
                + q('2026-09-29', 'Sell', 'G000902', f'{NAME} WE ACTED AS '
                    'AGENT', '-100.0', price='14.30', gross='1430.00',
                    net='1430.00', cur='CAD', act='Trades'))
        txs, err = qt_parse(body)
        legs = of(txs, action='TRANSFER')
        self.assertEqual(sorted((t['symbol'], t['quantity']) for t in legs),
                         [('QZD.TO', 100.0), ('QZD.U.TO', -100.0)])
        self.assertTrue(legs[0].get('journal_pair'))
        self.assertEqual(of(txs, action='BUYSELL')[0]['symbol'], 'QZD.TO')
        self.assertAlmostEqual(of(legs, symbol='QZD.TO')[0]['net_amount'],
                               1420.00)
        self.assertNotIn('no partner', err)

    def test_two_journals_of_one_day_pair_by_quantity(self):
        body = (journal_to_usd(qty=100, bv='1005.00')
                + journal_to_usd(qty=200, bv='2010.00'))
        txs, _err = qt_parse(body)
        by_pair = {}
        for t in of(txs, action='TRANSFER'):
            by_pair.setdefault(t['journal_pair'], []).append(t['quantity'])
        self.assertEqual(sorted(sorted(v) for v in by_pair.values()),
                         [[-200.0, 200.0], [-100.0, 100.0]])

    def test_lone_leg_is_attention_naming_both_shapes(self):
        body = q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION FROM '
                 'CAD BOOK VALUE: $3016.67 CNV@ 1.4138', '300.0', cur='USD')
        txs, err = qt_parse(body)
        self.assertEqual([(t['symbol'], t.get('journal_pair'))
                          for t in txs], [('QZD.U.TO', None)])
        flat = ' '.join(err.split())
        self.assertIn('warning: ATTENTION:', flat)
        self.assertIn('has no partner', flat)
        self.assertIn(f"'{NAME} JOURNAL POSITION TO USD' (-units, in CAD)",
                      flat)
        self.assertIn(f"'{NAME} JOURNAL POSITION FROM CAD BOOK VALUE: $X "
                      f"CNV@ r' (+units, in USD)", flat)

    def test_legs_of_two_accounts_never_pair(self):
        body = (q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION FROM '
                  'CAD BOOK VALUE: $3016.67 CNV@ 1.4138', '300.0', cur='USD')
                + q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION TO '
                    'USD', '-300.0', acct='55500002'))
        txs, err = qt_parse(body)
        self.assertFalse([t for t in txs if t.get('journal_pair')])
        self.assertEqual(err.count('has no partner'), 2)

    def test_the_accounts_own_usd_line_beats_the_convention(self):
        """An interlisted share whose USD line the account trades under
        its own symbol: the journal moves the units to that line."""
        nm = 'QZ ENERGY CORP'
        body = (q('2026-08-03', 'Buy', 'QZE', f'{nm} WE ACTED AS AGENT',
                  '10.0', price='20.0', gross='-200.00', net='-200.00',
                  cur='USD', act='Trades')
                + q('2026-09-25', 'BRW', 'QZE', f'{nm} JOURNAL POSITION FROM '
                    'CAD BOOK VALUE: $500.00 CNV@ 1.40', '25.0', cur='USD')
                + q('2026-09-25', 'BRW', 'QZE', f'{nm} JOURNAL POSITION TO '
                    'USD', '-25.0'))
        txs, _err = qt_parse(body)
        self.assertEqual(sorted((t['symbol'], t['quantity'])
                                for t in of(txs, action='TRANSFER')),
                         [('QZE.TO', -25.0), ('QZE.US', 25.0)])

    def test_api_shape_keeps_its_listings(self):
        body = (q('2026-09-25', 'BRW', 'QZD.TO', f'{NAME} JOURNAL POSITION '
                  'TO USD', '-300.0')
                + q('2026-09-25', 'BRW', 'QZD.U.TO', f'{NAME} JOURNAL '
                    'POSITION FROM CAD BOOK VALUE: $3016.67 CNV@ 1.4138',
                    '300.0', cur='USD'))
        txs, _err = qt_parse(body)
        self.assertEqual(sorted(t['symbol'] for t in txs),
                         ['QZD.TO', 'QZD.U.TO'])
        self.assertEqual(len({t['journal_pair'] for t in txs}), 1)

    def test_scan_leaves_the_journal_code_to_the_parser(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'q.csv'
            p.write_text(QH + journal_to_usd() + sell_code())
            self.assertEqual(scan_code_uses([p]), [])
            jc = journal_codes([p])
        self.assertEqual(jc['G000901']['symbol'], 'QZD.U.TO')
        self.assertEqual(jc['G000901']['how'], 'journal')

    def test_ticker_map_rule_for_the_code_wins(self):
        with tempfile.TemporaryDirectory() as d:
            tm = Path(d) / 'ticker.map'
            tm.write_text('GLOBAL G000901.US QZOTHER.US\n')
            base.set_ticker_map(tm)
            try:
                txs, err = qt_parse(journal_to_usd() + sell_code())
            finally:
                base.set_ticker_map(None)
        self.assertEqual(of(txs, action='BUYSELL')[0]['symbol'], 'G000901.US')
        self.assertNotIn(SC.NOTE_HEAD, err)


@rule("CA-XLIST-03")
class TestAnalyze(unittest.TestCase):
    def _legs(self):
        out = []
        for acct in ('rrsp', 'marg'):
            out += [XL.Leg(acct, 'questrade', 'QZD.TO', '2026-09-25', -300,
                           pair='2026-09-25#1'),
                    XL.Leg(acct, 'questrade', 'QZD.U.TO', '2026-09-25', 300,
                           pair='2026-09-25#1')]
        return out

    def test_paired_currency_journal_joins_as_journal(self):
        r = XL.analyze(self._legs(), {}, {}, base_currency='CAD',
                       currency_journals=True)
        self.assertEqual(r['suggested'], [])
        self.assertEqual({(p.kind, p.frm, p.to) for p in r['joined']},
                         {('JOURNAL', 'QZD.U.TO', 'QZD.TO')})
        self.assertEqual(XL.map_lines(r['joined'])[0].split('#')[0].strip(),
                         'JOURNAL QZD.U.TO QZD.TO')

    def test_a_map_rule_naming_a_line_wins(self):
        r = XL.analyze(self._legs(), {}, {}, base_currency='CAD',
                       map_named={'QZD.U.TO'}, currency_journals=True)
        self.assertEqual(r, {'joined': [], 'suggested': []})


# ------------------------------------------------------------ the run

def _rates_file(path: Path, pair='USD CAD', rate='1.3500'):
    d, lines = date(2025, 1, 1), []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _run(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
             PYTHONPATH=str(REPO / "src"))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(td, country='canada', tmap=''):
    root = Path(td) / country
    base_cur, src = (('CAD', 'USD') if country == 'canada'
                     else ('USD', 'CAD'))
    (root / 'inputs' / 'rrsp').mkdir(parents=True)
    (root / 'inputs' / 'marg').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n'
        f'base_currency = "{base_cur}"\nsource_currencies = ["{src}"]\n'
        + ('option_grant_timing_since = 2026\n' if country == 'canada'
           else '') + '\n'
        f'[accounts.rrsp]\ntype = "sheltered"\n\n'
        f'[accounts.marg]\ntype = "taxable"\n')
    (root / 'inputs' / 'rrsp' / '55500001.csv').write_text(  # pii-ok
        QH + buy_cad() + journal_to_usd() + sell_code())
    kw = dict(acct='55500002', atype='Individual margin')  # pii-ok
    (root / 'inputs' / 'marg' / '55500002.csv').write_text(  # pii-ok
        QH + buy_cad(**kw) + journal_to_usd(**kw) + sell_code(**kw))
    if tmap:
        (root / 'ticker.map').write_text(tmap)
    (root / 'work').mkdir()
    _rates_file(root / 'work' / 'to_base.csv',
                *(() if country == 'canada' else ('CAD USD', '0.7400')))
    return root


def _gain(root, acct='marg'):
    doc = json.loads((root / 'work' / f'{acct}_gains_wash.json').read_text())
    return doc['summary']['total_gain']


def _symbols(root, acct):
    doc = json.loads((root / 'work' / f'{acct}_base.json').read_text())
    return {(t['action'], t['symbol'], t['quantity'])
            for t in doc['transactions']}


@rule("CA-XLIST-03")
class TestRunCanada(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _project(cls._td.name)
        cls.r = _run(cls.root, 'run', '--no-input')

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_succeeds_without_a_short(self):
        out = self.r.stdout + self.r.stderr
        self.assertEqual(self.r.returncode, 0, out[-3000:])
        self.assertNotIn('G000901', out.replace(
            'G000901 → QZD.U.TO', ''))
        self.assertNotIn('Short position', out)
        self.assertNotIn('no purchase in your files', out)
        m = _run(self.root, 'find-missing-history')
        self.assertNotIn('G000901', m.stdout + m.stderr)
        self.assertNotIn('QZD', m.stdout)

    def test_the_two_lines_are_joined_as_a_journal(self):
        eff = (self.root / 'work' / XL.EFFECTIVE_MAP).read_text()
        self.assertIn('JOURNAL QZD.U.TO QZD.TO', eff)
        out = ' '.join(self.r.stdout.split())
        self.assertEqual(out.count('QZD.TO ↔ QZD.U.TO (currency journal '
                                   '2026-09-25)'), 2, out)
        self.assertIn('add `DISTINCT QZD.TO QZD.U.TO` to ticker.map', out)

    def test_sale_books_against_the_pool_with_the_cad_cost(self):
        """Taxable: bought 300 QZD.TO for 4264.95 CAD (with commission),
        journaled, sold as the USD units for 3025.50 USD at a flat
        1.35: proceeds 4084.425 CAD - ACB 4264.95 = -180.525."""
        self.assertAlmostEqual(_gain(self.root), -180.525, delta=0.011)
        self.assertIn(('BUYSELL', 'QZD.TO', -300.0),
                      _symbols(self.root, 'marg'))
        self.assertIn(('BUYSELL', 'QZD.TO', -300.0),
                      _symbols(self.root, 'rrsp'))

    def test_holdings_are_empty(self):
        for acct in ('marg', 'rrsp'):
            h = (self.root / 'reports' / f'{acct}_holdings.toml').read_text()
            self.assertNotIn('[[holding]]', h, acct)

    def test_transfers_view_lists_the_code(self):
        r = _run(self.root, 'transfers', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        codes = {(c['account'], c['code']): c
                 for c in json.loads(r.stdout)['symbol_codes']}
        for acct in ('marg', 'rrsp'):
            c = codes[(acct, 'G000901')]
            self.assertEqual((c['symbol'], c['how']), ('QZD.U.TO', 'journal'))


@rule("CA-XLIST-03")
class TestRunOverrides(unittest.TestCase):
    def test_extract_line_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tmap=f'EXTRACT {NAME} | USD | QZDU.TO\n')
            r = _run(root, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            eff = (root / 'work' / XL.EFFECTIVE_MAP).read_text()
            self.assertIn('JOURNAL QZDU.TO QZD.TO', eff)
            self.assertNotIn('QZD.U.TO', eff)
            self.assertAlmostEqual(_gain(root), -180.525, delta=0.011)

    def test_users_journal_line_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tmap='JOURNAL QZD.U.TO QZD.TO\n')
            r = _run(root, 'run', '--no-input')
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertNotIn('joined as one security', out)
            self.assertAlmostEqual(_gain(root), -180.525, delta=0.011)
            self.assertNotIn('no purchase in your files', out)


@rule("US-XLIST-01")
@rule_absent("CA-XLIST-03", country="usa")
class TestRunUsa(unittest.TestCase):
    def test_usa_does_not_join_on_the_brokers_pairing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country='usa')
            r = _run(root, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            eff = root / 'work' / XL.EFFECTIVE_MAP
            self.assertFalse(eff.exists() and 'JOURNAL ' in eff.read_text())
            # The parser's facts are the same in both countries.
            side = json.loads((root / 'work' /
                               'marg_questrade_transfers.json').read_text())
            self.assertEqual(sorted(t['symbol'] for t in
                                    side['transactions']),
                             ['QZD.TO', 'QZD.U.TO'])


if __name__ == '__main__':
    unittest.main()

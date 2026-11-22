"""A symbol written in another spelling names the broker's security
(backlog TJ-4600, TJ-4601, TJ-4604; CA-SL-01).

`EXTRACT <description> | CAD | zyab.TO` kept its symbol as typed: the
rows the line matched were booked under `zyab.TO`, an ACB pool of their
own beside the broker's ZYAB.TO, so a loss sold from one pool with the
shares bought back into the other inside the window was allowed. Every
ticker.map symbol is read upper case now, as the rename lines, QUOTE
and SIMILAR already were; every parser's symbols are upper-cased where
its rows leave the parse; an EXTRACT target on a Canadian venue is
spelled ROOT.TO, as the parsers and the .tt rows spell it:

(a) the review's repro: ZYAB bought under one description, sold at a
    loss of 20, bought back two days later under another description
    the EXTRACT line does not match. With `zyab.TO` in the line the
    books are the ones `ZYAB.TO` gives: one pool, the loss denied, the
    repurchase's ACB 100.
(b) an RBC export whose Symbol cell reads `zyab` (RBC, IB, Webull and
    Questrade's fee rows kept the cell's case), with the EXTRACT line
    `zyab.TO`: one pool, ZYAB.TO, the loss denied (TJ-4601).
(c) `EXTRACT ... | CAD | ZYAB.V` books ZYAB.TO: one pool, the loss
    denied. On a line for any currency (`*`) a `.V` may be a class
    letter: `tjs run` refuses the line, naming the line to write
    (TJ-4604).
(d) every keyword's parse gives its symbols upper case: both sides of
    a rename line, each lookup line's symbol, an EXTRACT line's target
    (a duplicate EXTRACT line that differs only in case is one rule,
    not a contradiction).
(e) every parser's rows pass through BaseBrokerage.parse_file, where
    they are upper-cased; an OCC option symbol reads as one in any case.
(f) `tjs migrate` counts an old EXTRACT line that ticker.map already
    holds in another case as already there, and `format-map` keeps one
    of two lines that differ only in case.
(g) the corporate actions are spelled as the trades: the review's repro,
    IB's demo statement with PARNt, SPNCo, INTc and SAMPg throughout,
    gives the books of the statement as shipped (TJ-4605).
(h) two spellings of one symbol that differ only in case stop the run
    (in one file, between the trades and the corporate actions, across
    accounts), naming both and where each is; the GLOBAL line it names
    joins them, the DISTINCT line keeps them apart (class spelling).
(i) a line naming a symbol on another Canadian venue (ZYAB.NE) names the
    books' ZYAB.TO; a `.V` the books spell ROOT.TO is warned about.
(j) a corporate action whose id hashes a symbol (RBC) is one event in
    any spelling: overlapping exports in two spellings ask one election,
    and an election saved under the id a lower-case cell gave it is
    carried over (TJ-4616), whichever export is read first (TJ-4617). A
    run that stops on two spellings saves no election: the manifest's
    bytes are as they were, and once the DISTINCT lines say there are
    two classes, each merger has its own election (TJ-4616). An election
    carried over, or set, for the lower-case spelling keeps the id that
    spelling gave, so a second security spelled in upper case, kept apart
    by a DISTINCT line later, is asked about, and the election stays with
    its spelling's event. `--spellings-from` naming a file it cannot read
    stops the corporate actions' read.

Every fixture is SYNTHETIC: invented ZY* tickers, made-up amounts and
account ids.
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _paths

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.ticker_map import parse_side_line, side_rules_from_text
from taxjson.lib.ticker_map_rules import _parse_map_text
from _support.qt_gambit_transfers import _config, _run

QHDR = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
        'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
        'Activity Type,Account #,Account Type\n')


def _row(day, settle, action, desc, qty, price, gross):
    return (f"{day},{settle},{action},ZYAB,{desc},{qty},{price},{gross},"
            f"0.00,{gross},CAD,Trades,99900090,Margin\n")


QT_CSV = (QHDR
          + _row('2024-02-01', '2024-02-02', 'Buy', 'OLD SAMPLE HOLDINGS',
                 '10', '10.00', '-100.00')
          + _row('2024-02-20', '2024-02-21', 'Sell', 'OLD SAMPLE HOLDINGS',
                 '-10', '8.00', '80.00')
          + _row('2024-02-22', '2024-02-23', 'Buy', 'ZYAB SAMPLE CORP',
                 '10', '8.00', '-80.00'))

# RBC: the first two rows' Symbol cell in lower case; the repurchase
# under another ticker the EXTRACT line moves.
RBC_CSV = (
    'Account Activity Detail Report\n"Account Number: 99900099"\n'
    '"Date Range: January 1, 2024 - December 31, 2024"\n\n'
    'Date,Activity,Symbol,Description,Quantity,Price,Settlement Date,'
    'Currency,Value,Amount\n'
    '02/01/2024,Buy,zyab,ZYAB SAMPLE CORP - Buy,10,10.00,02/02/2024,CAD,'
    '-100.00,-100.00\n'
    '02/20/2024,Sell,zyab,ZYAB SAMPLE CORP - Sell,10,8.00,02/21/2024,CAD,'
    '80.00,80.00\n'
    '02/22/2024,Buy,ZYXX,OLD SAMPLE HOLDINGS - Buy,10,8.00,02/23/2024,CAD,'
    '-80.00,-80.00\n')


def _project(td, name, line, csv=QT_CSV, csv_name='questrade.csv'):
    root = Path(td) / name
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(_config(year=2024))
    (root / 'ticker.map').write_text(line + '\n')
    (root / 'inputs' / 'margin' / csv_name).write_text(csv)
    return root


def _books(td, name, line, **kw):
    """The wash-adjusted gains of a run whose ticker.map is `line`."""
    root = _project(td, name, line, **kw)
    r = _run(root, 'run', '--no-input')
    if r.returncode != 0:
        raise AssertionError((r.stdout + r.stderr)[-3000:])
    return json.loads((root / 'work' / 'margin_gains_wash.json')
                      .read_text())


def _rerun(root, line, account='margin'):
    """The wash-adjusted gains of `root` run again with `line` as its
    ticker.map (the line a refusal named)."""
    (root / 'ticker.map').write_text(line + '\n')
    r = _run(root, 'run', '--no-input')
    if r.returncode != 0:
        raise AssertionError((r.stdout + r.stderr)[-3000:])
    return json.loads((root / 'work' / f'{account}_gains_wash.json')
                      .read_text())


def _named(out, keyword):
    """The ticker.map line a refusal names after `keyword`."""
    m = re.search(rf"`({keyword} [^`]+)`", out)
    if not m:
        raise AssertionError(out[-2000:])
    return m.group(1)


@rule(R.CA_SL_01)
class TestOneSecurityOnePool(unittest.TestCase):
    """(a) to (c): one pool, the loss denied, whatever the spelling."""

    def _one_pool_denied(self, books, sale_day):
        sales = [t for t in books['transactions']
                 if t.get('date') == sale_day]
        self.assertEqual([t['symbol'] for t in sales], ['ZYAB.TO'])
        self.assertAlmostEqual(sales[0]['disallowed_amount'], 20.0)
        self.assertAlmostEqual(sales[0]['gain'], 0.0)
        held = [(i['symbol'], i['qty'], i['total_cost'])
                for i in books['inventory_year_end']]
        self.assertEqual(held, [('ZYAB.TO', 10.0, 100.0)])
        self.assertAlmostEqual(books['summary']['total_disallowed'], 20.0)

    def test_a_lower_case_extract_symbol(self):
        line = 'EXTRACT old sample holdings | CAD | {}'
        with tempfile.TemporaryDirectory() as td:
            lower = _books(td, 'lower', line.format('zyab.TO'))
            upper = _books(td, 'upper', line.format('ZYAB.TO'))
        for books in (lower, upper):
            self._one_pool_denied(books, '2024-02-20')
        self.assertEqual(lower['transactions'], upper['transactions'])

    def test_a_lower_case_broker_cell(self):
        with tempfile.TemporaryDirectory() as td:
            books = _books(td, 'rbc',
                           'EXTRACT old sample holdings | CAD | zyab.TO',
                           csv=RBC_CSV, csv_name='rbc.csv')
        self._one_pool_denied(books, '2024-02-20')

    def test_a_venture_spelling_on_a_cad_line(self):
        with tempfile.TemporaryDirectory() as td:
            books = _books(td, 'dotv',
                           'EXTRACT old sample holdings | CAD | zyab.v')
        self._one_pool_denied(books, '2024-02-20')

    def test_a_venture_spelling_on_a_line_for_any_currency_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 'star',
                            'EXTRACT old sample holdings | * | ZYAB.V')
            r = _run(root, 'run', '--no-input')
            self.assertFalse((root / 'work' / 'margin_gains_wash.json')
                             .exists())
        out = ' '.join((r.stdout + r.stderr).split())
        self.assertNotEqual(r.returncode, 0, out[-2000:])
        self.assertIn('1 ticker.map problem(s)', out)
        self.assertIn('ticker.map:1: EXTRACT symbol', out)
        # The Venture listing on the line's own currency, which selects
        # the rows: the line it names books them as the CAD line does.
        said = 'EXTRACT old sample holdings | * | ZYAB.TO'
        self.assertIn(f'`{said}`', out)
        self.assertIn('`EXTRACT old sample holdings | * | ZYAB.V.US`', out)
        with tempfile.TemporaryDirectory() as td:
            books = _books(td, 'said', said)
        self._one_pool_denied(books, '2024-02-20')


class TestEveryKeywordReadsUpperCase(unittest.TestCase):
    """(d) No ticker.map line keeps a lower-case symbol."""

    def test_lookup_lines(self):
        cases = {
            'EXTRACT old sample holdings | cad | zyab.TO':
                ('extract', ('old sample holdings', 'CAD'), 'ZYAB.TO'),
            'EXTRACT old sample holdings | CAD | zyab.cn':
                ('extract', ('old sample holdings', 'CAD'), 'ZYAB.TO'),
            'EXTRACT old sample holdings | USD | zyab.u.to':
                ('extract', ('old sample holdings', 'USD'), 'ZYAB.U.TO'),
            'EXTRACT old sample holdings | USD | zyab.v.us':
                ('extract', ('old sample holdings', 'USD'), 'ZYAB.V.US'),
            'QUOTE zyab.TO ZYAB.TO': ('quote', 'ZYAB.TO', ('ZYAB.TO', 1.0)),
            'CRYPTO zyq ZYQ12345': ('crypto', 'ZYQ', 'ZYQ12345'),
            'T1135 zyab.US usa': ('t1135', 'ZYAB.US', 'USA'),
            'SIMILAR zyab.TO zycd.TO': ('similar', ('ZYAB.TO', 'ZYCD.TO'),
                                        ('ZYAB.TO', 'ZYCD.TO')),
            'STABLE zyusd usd': ('stable', 'ZYUSD', True),
            'MULT zyab 10': ('mult', 'ZYAB', 10.0),
            'SPLITSHARE zyab.TO': ('splitshare', 'ZYAB', True),
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                kw = line.split()[0]
                self.assertEqual(parse_side_line(kw, line), want)

    def test_a_venture_line_for_another_currency_is_a_map_problem(self):
        for cur in ('*', 'USD'):
            with self.subTest(cur=cur):
                side = side_rules_from_text(
                    f'EXTRACT old sample holdings | {cur} | zyab.V\n')
                self.assertEqual(side.extract, [])
                self.assertEqual(len(side.problems), 1)
                self.assertIn('ticker.map:1:', side.problems[0])
                said = f'EXTRACT old sample holdings | {cur} | ZYAB.TO'
                self.assertIn(f'`{said}`', side.problems[0])
                self.assertEqual(parse_side_line('EXTRACT', said)[2],
                                 'ZYAB.TO')

    def test_an_extract_line_twice_in_two_spellings_is_one_rule(self):
        side = side_rules_from_text(
            'EXTRACT old sample holdings | CAD | zyab.TO\n'
            'EXTRACT old sample holdings | CAD | ZYAB.TO\n'
            'EXTRACT old sample holdings | CAD | ZYAB.V\n')
        self.assertEqual(side.problems, [])
        self.assertEqual(side.extract,
                         [('old sample holdings', 'CAD', 'ZYAB.TO')])

    def test_rename_lines(self):
        tmap, problems, _notes = _parse_map_text(
            'GLOBAL zyab.US zycd.US\n'
            'TOBASE zyef.US zyef.TO\n'
            'JOURNAL zygh.U.TO zygh.TO\n'
            'DELETE zyij.TO\n'
            'DISTINCT zykl.TO zykl.V\n'
            'RENAME zymn.TO zyop.TO 2024-03-01\n')
        self.assertEqual(problems, [])
        self.assertEqual(tmap.glob, {'ZYAB.US': 'ZYCD.US'})
        # (a JOURNAL line is a TOBASE line too)
        self.assertEqual(tmap.tobase, {'ZYEF.US': 'ZYEF.TO',
                                       'ZYGH.U.TO': 'ZYGH.TO'})
        self.assertEqual(tmap.journal, {'ZYGH.U.TO': 'ZYGH.TO'})
        self.assertEqual(set(tmap.delete), {'ZYIJ.TO'})
        self.assertEqual(set(tmap.distinct),
                         {frozenset({'ZYKL.TO', 'ZYKL.V'})})
        self.assertEqual([(d.old, d.new) for d in tmap.dated],
                         [('ZYMN.TO', 'ZYOP.TO')])


class TestParsedSymbolsUpperCase(unittest.TestCase):
    """(e) One place upper-cases every parser's symbols."""

    def test_every_parser_parses_through_the_template(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.parsers.registry import SPECS, parser_class
        for s in SPECS:
            with self.subTest(parser=s.id):
                cls = parser_class(s)
                self.assertTrue(issubclass(cls, BaseBrokerage))
                self.assertIs(cls.parse_file, BaseBrokerage.parse_file)

    def test_the_rows_symbols_are_upper_cased(self):
        from taxjson.lib.brokerages.base import upper_case_symbols
        rows = [{'symbol': 'zyab.TO', 'symbol_new': 'zycd.TO'},
                {'symbol': 'zyab240119c00010000.TO',
                 'exercise_of': 'zyab.TO'},
                {'symbol': 'CASH'}, {'action': 'INTEREST'}]
        upper_case_symbols(rows)
        self.assertEqual(rows, [
            {'symbol': 'ZYAB.TO', 'symbol_new': 'ZYCD.TO'},
            {'symbol': 'ZYAB240119C00010000.TO', 'exercise_of': 'ZYAB.TO'},
            {'symbol': 'CASH'}, {'action': 'INTEREST'}])

    def test_an_option_symbol_in_any_case(self):
        from taxjson.lib.option_symbols import (
            is_option_symbol, parse_option_expiry, parse_option_right,
            parse_option_strike, parse_option_underlying)
        for sym in ('ZYAB240119C00010000.TO', 'zyab240119c00010000.to',
                    'f:zyq240119p00010000.US'):
            with self.subTest(sym=sym):
                self.assertTrue(is_option_symbol(sym))
                self.assertEqual(parse_option_expiry(sym), '2024-01-19')
                self.assertIn(parse_option_right(sym), ('C', 'P'))
                self.assertEqual(parse_option_strike(sym), 10.0)
        self.assertEqual(parse_option_underlying('zyab240119c00010000.to'),
                         'ZYAB.TO')
        self.assertFalse(is_option_symbol('zyab.TO'))


class TestCaseOnlyDuplicateLines(unittest.TestCase):
    """(f) migrate and format-map read a case-only repeat as one rule."""

    def test_migrate_finds_the_line_already_there(self):
        from taxjson.lib import migrate as MG
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'taxjson.toml').write_text(_config(year=2024))
            (root / 'ticker.map').write_text(
                'EXTRACT old sample holdings | CAD | ZYAB.TO\n')
            (root / 'ticker_extraction_overrides.txt').write_text(
                'old sample holdings | CAD | zyab.TO\n')
            pl = MG.plan(root)
        self.assertIn('ticker_extraction_overrides.txt -> ticker.map: 0 '
                      'EXTRACT line(s) (1 already there)', pl.done)

    def test_format_map_keeps_one_line(self):
        from taxjson.lib.ticker_map_format import format_map
        res = format_map('EXTRACT old sample holdings | CAD | zyab.TO\n'
                         'EXTRACT old sample holdings | CAD | ZYAB.TO\n'
                         'GLOBAL zyef.US zygh.US\n'
                         'GLOBAL ZYEF.US ZYGH.US\n')
        self.assertEqual([n for n, _l in res.duplicates], [2, 4])
        self.assertIn('EXTRACT old sample holdings | CAD | zyab.TO',
                      res.text)
        self.assertNotIn('| ZYAB.TO', res.text)
        self.assertEqual(res.problems, [])

    def test_format_map_keeps_a_quote_whose_value_differs_in_case(self):
        from taxjson.lib.ticker_map_format import format_map
        res = format_map('QUOTE ZYAB.TO zyab.to\nQUOTE ZYAB.TO ZYAB.TO\n')
        self.assertEqual(res.duplicates, [])
        self.assertEqual(len(res.problems), 1)

    def test_format_map_keeps_case_pair_lines(self):
        from taxjson.lib.ticker_map_format import format_map
        res = format_map('GLOBAL ZYXa.US ZYXA.US\nGLOBAL zyxa.US ZYXA.US\n'
                         'DISTINCT ZYQb.TO ZYQB.TO\n'
                         'DISTINCT ZYQb.TO ZYQB.TO\n')
        self.assertEqual([n for n, _l in res.duplicates], [4])
        for line in ('GLOBAL ZYXa.US ZYXA.US', 'GLOBAL zyxa.US ZYXA.US',
                     'DISTINCT ZYQb.TO ZYQB.TO'):
            self.assertIn(line, res.text)


DEMO = _paths.SRC / 'taxjson' / 'data' / 'demo' / 'margin'


def _ib_project(td, name, text, line=''):
    root = Path(td) / name
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(_config(year=2024))
    if line:
        (root / 'ticker.map').write_text(line + '\n')
    (root / 'inputs' / 'margin' / 'ib.csv').write_text(text)
    shutil.copy(DEMO / 'manifest.json',
                root / 'inputs' / 'margin' / 'manifest.json')
    return root


def _gains(root, account='margin'):
    return json.loads((root / 'work' / f'{account}_gains_wash.json')
                      .read_text())


def _rbc(rows, acct='99900099'):
    """An RBC activity export of `rows` (day, action, symbol,
    description, quantity, price, settlement day, amount), CAD."""
    out = ('Account Activity Detail Report\n'
           f'"Account Number: {acct}"\n'
           '"Date Range: January 1, 2024 - December 31, 2024"\n\n'
           'Date,Activity,Symbol,Description,Quantity,Price,'
           'Settlement Date,Currency,Value,Amount\n')
    for day, act, sym, desc, qty, price, settle, amt in rows:
        out += (f'{day},{act},{sym},{desc} - {act},{qty},{price},{settle},'
                f'CAD,{amt},{amt}\n')
    return out


# Class A sold at a loss, then shares spelled ZYXa bought two days later:
# one security, the loss denied; two (a lower-case class letter), not.
CLASS_ROWS = [
    ('02/01/2024', 'Buy', 'ZYXA', 'ZYXA CLASS A CORP', '10', '10.00',
     '02/02/2024', '-100.00'),
    ('02/20/2024', 'Sell', 'ZYXA', 'ZYXA CLASS A CORP', '10', '8.00',
     '02/21/2024', '80.00'),
    ('02/22/2024', 'Buy', 'ZYXa', 'ZYX CLASS a SHARES', '10', '8.00',
     '02/23/2024', '-80.00')]


@rule(R.CA_SL_01)
class TestTheCorporateActionsSpellAsTheTrades(unittest.TestCase):
    """(g) The review's repro: one spelling, any case, the same books."""

    def test_a_mixed_case_statement_books_as_shipped(self):
        src = (DEMO / 'ib_scenarios_demo.csv').read_text()
        mixed = (src.replace('PARNT', 'PARNt').replace('SPNCO', 'SPNCo')
                 .replace('INTC', 'INTc').replace('SAMPG', 'SAMPg'))
        books = {}
        with tempfile.TemporaryDirectory() as td:
            for name, text in (('upper', src), ('mixed', mixed)):
                root = _ib_project(td, name, text)
                r = _run(root, 'run', '--no-input')
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
                books[name] = _gains(root)
        self.assertAlmostEqual(books['mixed']['summary']['total_gain'],
                               -126.97, places=2)
        # Every field but a row's id, a hash of the export's own cells.
        self.assertEqual(*[[{k: v for k, v in t.items() if k != 'id'}
                            for t in books[n]['transactions']]
                           for n in ('mixed', 'upper')])
        self.assertEqual(books['mixed']['inventory_year_end'],
                         books['upper']['inventory_year_end'])


@rule(R.CA_SL_01)
class TestTwoSpellingsStopTheRun(unittest.TestCase):
    """(h) A letter's case never joins two securities in silence."""

    def _refused(self, root):
        r = _run(root, 'run', '--no-input')
        out = ' '.join((r.stdout + r.stderr).split())
        self.assertNotEqual(r.returncode, 0, out[-2000:])
        self.assertIn('differing only in letter case', out)
        return out

    def test_trades_and_corporate_actions(self):
        src = (DEMO / 'ib_scenarios_demo.csv').read_text()
        text = ''.join(ln.replace('SPNCO', 'SPNCo')
                       if ln.startswith('Corporate Actions,Data') else ln
                       for ln in src.splitlines(keepends=True))
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, 'ca', text)
            out = self._refused(root)
            self.assertIn('SPNCO.US in inputs/margin/ib.csv;', out)
            self.assertIn('SPNCo.US in inputs/margin/ib.csv (corporate '
                          'actions)', out)
            line = _named(out, 'GLOBAL')
            self.assertEqual(line, 'GLOBAL SPNCo.US SPNCO.US')
            books = _rerun(root, line)
        self.assertAlmostEqual(books['summary']['total_gain'], -126.97,
                               places=2)

    def test_one_file_and_the_lines_that_answer(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 'one', '', csv=_rbc(CLASS_ROWS),
                            csv_name='rbc.csv')
            out = self._refused(root)
            self.assertIn('ZYXA.TO in inputs/margin/rbc.csv; ZYXa.TO in '
                          'inputs/margin/rbc.csv', out)
            same, apart = _named(out, 'GLOBAL'), _named(out, 'DISTINCT')
            self.assertIn('(booked ZYX.A.TO)', out)
            one = _rerun(root, same)
            two = _rerun(root, apart)
        self.assertAlmostEqual(one['summary']['total_disallowed'], 20.0)
        self.assertEqual([(i['symbol'], i['qty'], i['total_cost'])
                          for i in one['inventory_year_end']],
                         [('ZYXA.TO', 10.0, 100.0)])
        self.assertAlmostEqual(two['summary']['total_disallowed'], 0.0)
        self.assertEqual([(i['symbol'], i['qty'], i['total_cost'])
                          for i in two['inventory_year_end']],
                         [('ZYX.A.TO', 10.0, 80.0)])

    def test_two_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 'two', '', csv=_rbc(CLASS_ROWS[:2]),
                            csv_name='rbc.csv')
            (root / 'taxjson.toml').write_text(
                _config(year=2024)
                + '\n[accounts.margin2]\ntype = "taxable"\n')
            (root / 'inputs' / 'margin2').mkdir()
            (root / 'inputs' / 'margin2' / 'rbc.csv').write_text(
                _rbc(CLASS_ROWS[2:], acct='99900088'))
            out = self._refused(root)
        self.assertIn('ZYXA.TO in inputs/margin/rbc.csv; ZYXa.TO in '
                      'inputs/margin2/rbc.csv', out)

    def test_one_spelling_in_lower_case_runs(self):
        rows = [(d, a, s.lower(), n, q, p, sd, v)
                for d, a, s, n, q, p, sd, v in CLASS_ROWS[:2]]
        rows.append(CLASS_ROWS[2][:2] + ('zyxa',) + CLASS_ROWS[2][3:])
        with tempfile.TemporaryDirectory() as td:
            books = _books(td, 'lower', '', csv=_rbc(rows),
                           csv_name='rbc.csv')
        self.assertAlmostEqual(books['summary']['total_disallowed'], 20.0)
        self.assertEqual([i['symbol'] for i in books['inventory_year_end']],
                         ['ZYXA.TO'])


class TestCasePairLines(unittest.TestCase):
    """(h) The GLOBAL and DISTINCT lines whose symbols differ only in
    case, the one place a symbol's case is read as written."""

    def test_the_lines(self):
        tmap, problems, _n = _parse_map_text(
            'GLOBAL ZYXa.US ZYXA.US\n'
            'DISTINCT ZYQb.TO ZYQB.TO\n'
            'DISTINCT zyq.US ZYQ.US\n'
            'GLOBAL ZYQb.TO ZYQB.TO\n')
        self.assertEqual(tmap.case_spellings,
                         {'ZYXa.US': 'same', 'ZYQb.TO': 'distinct'})
        self.assertEqual(tmap.glob, {})
        self.assertEqual(len(problems), 2)
        self.assertIn('ticker.map:3: DISTINCT zyq.US ZYQ.US', problems[0])
        self.assertIn('ticker.map:4: ZYQb.TO is one security', problems[1])

    def test_the_class_spelling(self):
        from taxjson.lib.symbol_case import book_symbol, class_spelling
        self.assertEqual(class_spelling('ZYXa.US'), 'ZYX.A.US')
        self.assertEqual(class_spelling('ZYXab'), 'ZYX.AB')
        for sym in ('zyx.US', 'ZYxA.US', 'ZYX.a.US', 'ZYXA.US'):
            self.assertIsNone(class_spelling(sym), sym)
        case = {'ZYXa.US': 'distinct', 'ZYQb.TO': 'same'}
        self.assertEqual(book_symbol('ZYXa.US', case), 'ZYX.A.US')
        self.assertEqual(book_symbol('ZYQb.TO', case), 'ZYQB.TO')
        self.assertEqual(book_symbol('zyab.TO', case), 'ZYAB.TO')
        # A corporate action's placeholder is no symbol: kept as written.
        self.assertEqual(book_symbol('(unknown parent)'), '(unknown parent)')

    def test_an_event_identity(self):
        from taxjson.lib.symbol_case import book_ident
        case = {'ZYXa.TO': 'distinct'}
        for got, want in (('zyab', 'ZYAB'), ('ZYAb@2025-03-17', 'ZYAB@2025-03-17'),
                          ('ZYXa', 'ZYX.A'), ('ZYXa@2025-03-17', 'ZYX.A@2025-03-17'),
                          ('ZYXa.TO', 'ZYX.A.TO'), ('US9990001017', 'US9990001017'),
                          ('(unknown parent)', '(unknown parent)')):
            self.assertEqual(book_ident(got, case), want, got)

    def test_a_holdings_file_reads_upper_case(self):
        from taxjson.lib.positions_reports import read_positions
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'holdings.toml'
            f.write_text('[[holding]]\nsymbol = "zyab.TO"\nquantity = 10\n')
            rep = read_positions(f)
        self.assertEqual([(r.symbol, r.raw_symbol) for r in rep.rows],
                         [('ZYAB.TO', 'zyab.TO')])


class TestOtherVenueSpellings(unittest.TestCase):
    """(i) Every line names a Canadian listing as the books spell it."""

    def test_the_parse(self):
        self.assertEqual(parse_side_line('QUOTE', 'QUOTE zyab.ne ZYAB.NE'),
                         ('quote', 'ZYAB.TO', ('ZYAB.NE', 1.0)))
        self.assertEqual(parse_side_line('T1135', 'T1135 zyab.cn usa'),
                         ('t1135', 'ZYAB.TO', 'USA'))
        self.assertEqual(parse_side_line('SIMILAR',
                                         'SIMILAR ZYAB.VN ZYQQ.TO')[1],
                         ('ZYAB.TO', 'ZYQQ.TO'))
        tmap, problems, _n = _parse_map_text(
            'GLOBAL zyab.ne zycd.TO\nDISTINCT ZYEF.V ZYEF.TO\n')
        self.assertEqual(problems, [])
        self.assertEqual(tmap.glob, {'ZYAB.TO': 'ZYCD.TO'})
        self.assertEqual(set(tmap.distinct),
                         {frozenset({'ZYEF.V', 'ZYEF.TO'})})

    def _similar(self, td, name, sym):
        csv = QT_CSV.replace('ZYAB,ZYAB SAMPLE CORP',
                             'ZYQQ,ZYQQ SAMPLE FUND')
        root = _project(td, name, f'EXTRACT old sample holdings | CAD | '
                        f'{sym}\nSIMILAR {sym} ZYQQ.TO', csv=csv)
        r = _run(root, 'run', '--no-input')
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        return ' '.join((r.stdout + r.stderr).split())

    def test_a_similar_line_on_another_venue_applies(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._similar(td, 'ne', 'ZYAB.NE')
        self.assertIn("similar fund in a loss's window: ZYAB.TO sold at a "
                      "loss, ZYQQ.TO bought within 30 days", out)

    def test_a_venture_line_the_books_spell_otherwise_warns(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._similar(td, 'v', 'ZYAB.V')
        self.assertIn('ticker.map:2: `SIMILAR ZYAB.V ZYQQ.TO` names '
                      'ZYAB.V, a symbol the books do not hold', out)
        self.assertIn('write ZYAB.TO in the line', out)


EVIDENCE = _paths.TESTS / 'golden' / 'evidence' / 'project' / 'inputs' / 'rbc'
# The merger's id from the RBC exports in upper case (the evidence
# book's saved election), and the id main and 1c8e91dd gave it from the
# same exports with a lower-case letter ending each symbol cell (the
# cells hashed as read).
MERGER_ID = '20250317-sampr-sampt-1b4d'
MERGER_ID_AS_READ = '20250317-sampr-sampt-0daa'
# The merger of the class a DISTINCT line keeps apart (SAMP.R.US).
CLASS_MERGER_ID = '20250317-sampr-sampt-79d8'
LINES = {'GLOBAL': 'GLOBAL SAMPr.US SAMPR.US\nGLOBAL SAMPt.US SAMPT.US\n',
         'DISTINCT': ('DISTINCT SAMPr.US SAMPR.US\n'
                      'DISTINCT SAMPt.US SAMPT.US\n')}


def _seven_more(text, lower=False):
    """`text`'s rows again, without its header, holding 7 shares where it
    held 12: a second holding, its symbol cells as `_lower_cells` spells
    them when `lower`."""
    seven = (text.replace(',12,', ',7,').replace(',-12,', ',-7,')
             .replace(',-120,', ',-70,').replace(',132,', ',77,'))
    return ''.join(ln + '\n' for ln in (
        _lower_cells(seven) if lower else seven).splitlines()[1:])


def _lower_cells(text):
    """An RBC export with each symbol cell's last letter in lower case."""
    out = []
    for i, ln in enumerate(text.splitlines()):
        p = ln.split(',')
        if i and len(p) > 2 and p[2].isalpha():
            p[2] = p[2][:-1] + p[2][-1].lower()
        out.append(','.join(p))
    return '\n'.join(out) + '\n'


@rule(R.CA_CORP_03)
class TestOneEventInAnySpelling(unittest.TestCase):
    """(j) One merger, one election, booked once."""

    def _run_rbc(self, td, name, files, line=''):
        root = Path(td) / name
        (root / 'inputs' / 'margin').mkdir(parents=True)
        (root / 'taxjson.toml').write_text(_config(year=2025))
        if line:
            (root / 'ticker.map').write_text(line)
        for fname, text in files.items():
            (root / 'inputs' / 'margin' / fname).write_text(text)
        r = _run(root, 'run', '--no-input')
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        corp = json.loads((root / 'work' / 'margin_rbc_direct_corp.json')
                          .read_text())['transactions']
        saved = json.loads((root / 'inputs' / 'margin' / 'manifest.json')
                           .read_text())['elections']
        return root, corp, saved

    def test_overlapping_exports_in_two_spellings(self):
        y4, y5 = ((EVIDENCE / f).read_text()
                  for f in ('rbc_2024.csv', 'rbc_2025.csv'))
        files = {'rbc_2024.csv': y4, 'rbc_2025.csv': y5,
                 'rbc_2025b.csv': _lower_cells(y5),
                 'manifest.json': (EVIDENCE / 'manifest.json').read_text()}
        with tempfile.TemporaryDirectory() as td:
            root, corp, saved = self._run_rbc(
                td, 'dbl', files,
                'GLOBAL SAMPr.US SAMPR.US\nGLOBAL SAMPt.US SAMPT.US\n')
            gains = _gains(root)
        self.assertEqual(sorted(saved), [MERGER_ID])
        self.assertEqual([(t['symbol'], t['quantity']) for t in corp],
                         [('SAMPR.US', -12.0), ('SAMPT.US', 12.0)])
        self.assertEqual({t.get('event_id') or t.get('corp_event_id')
                          for t in corp}, {MERGER_ID})
        self.assertEqual(gains['inventory_year_end'], [])

    def test_an_election_saved_under_the_id_as_read_applies(self):
        files = {f: _lower_cells((EVIDENCE / f).read_text())
                 for f in ('rbc_2024.csv', 'rbc_2025.csv')}
        files['manifest.json'] = (EVIDENCE / 'manifest.json').read_text(
            ).replace(MERGER_ID, MERGER_ID_AS_READ)
        with tempfile.TemporaryDirectory() as td:
            root, corp, saved = self._run_rbc(td, 'saved', files)
            note = (root / 'reports' / 'margin.sum').read_text()
        self.assertEqual(sorted(saved), [MERGER_ID])
        self.assertEqual(saved[MERGER_ID]['election'], 'taxable_disposition')
        self.assertEqual(len(corp), 2)
        self.assertIn(f'election {MERGER_ID_AS_READ} (taxable_disposition) '
                      f'was saved under the id the export\'s spelling of a '
                      f'symbol gave it; carried over to {MERGER_ID}', note)

    def test_the_id_as_read_with_the_upper_case_export_first(self):
        y5 = (EVIDENCE / 'rbc_2025.csv').read_text()
        files = {'rbc_2024.csv': (EVIDENCE / 'rbc_2024.csv').read_text(),
                 'rbc_2025.csv': y5, 'rbc_2025b.csv': _lower_cells(y5),
                 'manifest.json': (EVIDENCE / 'manifest.json').read_text(
                     ).replace(MERGER_ID, MERGER_ID_AS_READ)}
        with tempfile.TemporaryDirectory() as td:
            root, corp, saved = self._run_rbc(td, 'first', files,
                                              LINES['GLOBAL'])
            note = (root / 'reports' / 'margin.sum').read_text()
        self.assertEqual(sorted(saved), [MERGER_ID])
        self.assertEqual(len(corp), 2)
        self.assertIn(f'election {MERGER_ID_AS_READ} (taxable_disposition) '
                      f'was saved under the id the export\'s spelling of a '
                      f'symbol gave it', note)

    def _refused_then(self, td, name, files, line):
        """`files` run with the class's merger election saved under the
        id its spelling gave it and no case-pair line: refused, the
        manifest's bytes kept; then with `line`: (the run, the elections,
        the pending ids)."""
        root = Path(td) / name
        (root / 'inputs' / 'margin').mkdir(parents=True)
        (root / 'taxjson.toml').write_text(_config(year=2025))
        for fname, text in files.items():
            (root / 'inputs' / 'margin' / fname).write_text(text)
        manifest = root / 'inputs' / 'margin' / 'manifest.json'
        manifest.write_text((EVIDENCE / 'manifest.json').read_text(
            ).replace(MERGER_ID, MERGER_ID_AS_READ))
        before = manifest.read_bytes()
        r = _run(root, 'run', '--no-input')
        out = ' '.join((r.stdout + r.stderr).split())
        self.assertNotEqual(r.returncode, 0, out[-2000:])
        self.assertIn('differing only in letter case', out)
        self.assertEqual(manifest.read_bytes(), before)
        (root / 'ticker.map').write_text(LINES[line])
        r = _run(root, 'run', '--no-input')
        pending = root / 'work' / 'margin_pending_elections.json'
        return (r, json.loads(manifest.read_text())['elections'],
                [p['event_id'] for p in json.loads(pending.read_text())
                 ['pending']] if pending.exists() else [])

    def test_a_refused_run_saves_no_election(self):
        # Two classes, SAMPR and SAMPr (7 shares), merged the same day at
        # one ratio: refused on the trades' spellings, before the
        # corporate actions are read.
        y4, y5 = ((EVIDENCE / f).read_text()
                  for f in ('rbc_2024.csv', 'rbc_2025.csv'))
        files = {'rbc_2024.csv': y4 + _seven_more(y4, lower=True),
                 'rbc_2025.csv': y5 + _seven_more(y5, lower=True)}
        with tempfile.TemporaryDirectory() as td:
            r, saved, pending = self._refused_then(td, 'classes', files,
                                                   'DISTINCT')
        self.assertEqual(r.returncode, 3, (r.stdout + r.stderr)[-3000:])
        self.assertEqual(sorted(saved), [CLASS_MERGER_ID])
        self.assertEqual(pending, [MERGER_ID])

    def test_a_refusal_at_the_corporate_actions_saves_none(self):
        # The trades spell SAMPR, the merger SAMPr: refused by the
        # corporate actions' read, before an election is read or saved.
        files = {'rbc_2024.csv': (EVIDENCE / 'rbc_2024.csv').read_text(),
                 'rbc_2025.csv': _lower_cells(
                     (EVIDENCE / 'rbc_2025.csv').read_text())}
        with tempfile.TemporaryDirectory() as td:
            r, saved, pending = self._refused_then(td, 'across', files,
                                                   'GLOBAL')
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        self.assertEqual(sorted(saved), [MERGER_ID])
        self.assertEqual(pending, [])

    def _then_a_second_security(self, root):
        """`root` (its exports lower case) given a second security spelled
        in upper case, SAMPR (7 shares), and the DISTINCT lines, run:
        (the run, the elections, the pending ids)."""
        folder = root / 'inputs' / 'margin'
        for f in ('rbc_2024.csv', 'rbc_2025.csv'):
            text = (EVIDENCE / f).read_text()
            (folder / f).write_text(_lower_cells(text) + _seven_more(text))
        (root / 'ticker.map').write_text(LINES['DISTINCT'])
        r = _run(root, 'run', '--no-input')
        pending = root / 'work' / 'margin_pending_elections.json'
        return (r, json.loads((folder / 'manifest.json').read_text())
                ['elections'],
                [p['event_id'] for p in json.loads(pending.read_text())
                 ['pending']] if pending.exists() else [])

    def test_a_carried_election_keeps_its_spelling(self):
        files = {f: _lower_cells((EVIDENCE / f).read_text())
                 for f in ('rbc_2024.csv', 'rbc_2025.csv')}
        files['manifest.json'] = (EVIDENCE / 'manifest.json').read_text(
            ).replace(MERGER_ID, MERGER_ID_AS_READ)
        with tempfile.TemporaryDirectory() as td:
            root, _corp, saved = self._run_rbc(td, 'carried', files)
            self.assertEqual(saved[MERGER_ID]['read_event_id'],
                             MERGER_ID_AS_READ)
            r, saved, pending = self._then_a_second_security(root)
        self.assertEqual(r.returncode, 3, (r.stdout + r.stderr)[-3000:])
        self.assertEqual(sorted(saved), [CLASS_MERGER_ID])
        self.assertEqual(saved[CLASS_MERGER_ID]['hints'],
                         {'fmv_per_share': 10.0})
        self.assertEqual(pending, [MERGER_ID])

    def test_an_election_set_keeps_its_spelling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'set'
            (root / 'inputs' / 'margin').mkdir(parents=True)
            (root / 'taxjson.toml').write_text(_config(year=2025))
            for f in ('rbc_2024.csv', 'rbc_2025.csv'):
                (root / 'inputs' / 'margin' / f).write_text(
                    _lower_cells((EVIDENCE / f).read_text()))
            self.assertEqual(_run(root, 'run', '--no-input').returncode, 3)
            r = _run(root, 'elect', 'margin', '--set',
                     f'{MERGER_ID}=taxable_disposition',
                     '--hint', 'fmv_per_share=10')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            r, saved, pending = self._then_a_second_security(root)
        self.assertEqual(r.returncode, 3, (r.stdout + r.stderr)[-3000:])
        self.assertEqual(sorted(saved), [CLASS_MERGER_ID])
        self.assertEqual(saved[CLASS_MERGER_ID]['read_event_id'],
                         MERGER_ID_AS_READ)
        self.assertEqual(pending, [MERGER_ID])

    def test_spellings_from_a_file_it_cannot_read(self):
        from taxjson.bin import taxjson_corp_actions as CA
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / 'damaged.json').write_text('{')
            (folder / 'list.json').write_text('[]')
            (folder / 'good.json').write_text('{}')
            csv = folder / 'rbc_2025.csv'
            csv.write_text((EVIDENCE / 'rbc_2025.csv').read_text())
            specs = ['margin', f'={folder / "good.json"}'] + [
                f'margin={folder / name}' for name in
                ('gone.json', '', 'damaged.json', 'list.json')]
            for spec in specs:
                err = io.StringIO()
                argv = ['taxjson-corp-actions', '--brokerage', 'rbc_direct',
                        '--country', 'canada', '--account-name', 'margin',
                        '--manifest', str(folder / 'manifest.json'),
                        '--spellings-from', spec, str(csv)]
                with self.subTest(spec=spec):
                    with mock.patch.object(sys, 'argv', argv), \
                            contextlib.redirect_stderr(err), \
                            contextlib.redirect_stdout(io.StringIO()), \
                            self.assertRaises(SystemExit) as cm:
                        CA.main()
                    self.assertEqual(cm.exception.code, 2)
                    self.assertIn(f'error: --spellings-from {spec}: ',
                                  err.getvalue())
                    self.assertFalse((folder / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()

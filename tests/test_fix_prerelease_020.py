"""Pre-release review of the Questrade internal-code inference
(lib/symbol_codes, tax-logic CA-ACB-CODES / US-BASIS-CODES) and of the
console file names / message labels added since v0.19.0.

M1  a name-only match keeps the share designators (class letter, voting
    rights, ADR / ordinary, NEW ...) and must be EXACT and unique; a near
    name is only suggested (the ATTENTION line names the GLOBAL line).
    Transfer pairing refuses a leg whose name is another class.
M2  any ticker.map rule naming the code (DELETE, DISTINCT, dated RENAME,
    GLOBAL ...) wins over the inference, in the run and in the parser.
L1  the record and `taxjson transfers` say "ticker.map rule" then.
L2  a transfer-in that pairs with nothing is not identified by name.
L3  pairing on a subset of words needs two strong words in common.
L4  a damaged record is ignored with one warning, never a traceback.
L5  a file name's control characters are escaped on the console and in
    the .diag.
L6  out.labelled() escapes control characters shown to a person.

Every fixture is SYNTHETIC: invented tickers (QZ*), codes and names,
fake account ids (pii-ok: 55500001, U5550001).
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from tax_rules import rule
from test_fix_qtcodes import (CONFIG, QH, SELL_M, TFI_M, DIV_M, DIV_NAME,
                              _run, ib_statement, q)
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def use(code, name, arrivals=(), cur='USD'):
    return SC.CodeUse(code=code, name=name, currencies=[cur],
                      arrivals=list(arrivals), rows=1)


def entry(sym, name):
    return SC.NameEntry(sym, SC.name_tokens(name), name, 'ibm', 'ib')


def leg(sym, date, qty, name, cur='USD'):
    return SC.OutLeg('ibm', 'ib', sym, date, qty, cur,
                     [SC.name_tokens(name)])


def res(uses, outs=(), names=(), mapped=lambda c: False):
    return SC.resolve(uses, list(outs), list(names), listing_ok=ok_listing,
                      mapped=mapped)


# ------------------------------------------------------------ M1

@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestNameOnlyKeepsDesignators(unittest.TestCase):
    """Name-only: never booked under another share of the company."""

    CASES = [
        # (code description, the only listing known by name)
        ('QZGOOG INC CL A', ('QZGC.US', 'QZGOOG INC-CL C')),
        ('QZBRK HATHAWAY INC CL B NEW', ('QZBRKA.US',
                                         'QZBRK HATHAWAY INC-CL A')),
        ('QZBRK HATHAWAY INC CL B', ('QZBRKA.US', 'QZBRK HATHAWAY INC CL A')),
        ('QZTEL CORP CL B SUB VTG', ('QZTA.US', 'QZTEL CORP CL A VOTING')),
        ('QZTEL CORP SUBORDINATE VOTING SHARES', ('QZTA.US', 'QZTEL CORP')),
        ('QZX PLC ORD SHS', ('QZXY.US', 'QZX PLC SPONSORED ADR')),
        ('QZX PLC SPONSORED ADR', ('QZXO.US', 'QZX PLC ORDINARY SHARES')),
        ('NEW QZY INC', ('QZY.US', 'QZY CORP')),
        ('QZM PFD SER A', ('QZM.US', 'QZM CORP')),
        ('QZM UNITS', ('QZM.US', 'QZM CORP')),
    ]

    def test_another_share_of_the_company_is_never_applied(self):
        for desc, (sym, name) in self.CASES:
            with self.subTest(desc=desc):
                r = res([use('X000012', desc)], names=[entry(sym, name)])
                self.assertEqual(r['resolved'], {})
                d = r['unresolved']['X000012']['detail']
                self.assertIn(f'looks like {sym} by name', d)
                # The ATTENTION names the line to add if it is right.
                self.assertIn(f'GLOBAL X000012.US {sym}', d)

    def test_the_same_class_still_matches_exactly(self):
        names = [entry('QZGA.US', 'QZGOOG INC-CL A'),
                 entry('QZGC.US', 'QZGOOG INC-CL C')]
        r = res([use('X000012', 'QZGOOG INC CLASS A')], names=names)
        self.assertEqual(r['resolved']['X000012']['symbol'], 'QZGA.US')

    def test_corporate_form_alone_is_noise(self):
        # Owner-decided reading: INC / CORP / LTD differ in nothing that
        # names the share, so with every other word equal they match.
        r = res([use('X000012', 'QZY INC')], names=[entry('QZY.US',
                                                          'QZY CORP')])
        self.assertEqual(r['resolved']['X000012']['symbol'], 'QZY.US')

    def test_the_parser_warns_with_the_suggested_line(self):
        st = res([use('X000004', 'QZB BANK CORP NEW', cur='CAD')],
                 names=[entry('QZB.TO', 'QZB BANK CORP')])
        txs, err = _qt_parse([DIV_NAME.replace('QZB BANK CORP',
                                               'QZB BANK CORP NEW')], st)
        self.assertEqual({t['symbol'] for t in txs}, {'X000004.TO'})
        self.assertIn("ATTENTION", err)
        self.assertIn('looks like QZB.TO by name', err)
        self.assertIn('GLOBAL X000004.TO QZB.TO', err)


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestPairingRefusesAnotherClass(unittest.TestCase):
    def test_a_class_c_transfer_never_pairs_a_class_a_arrival(self):
        r = res([use('X000012', 'QZGOOG INC CL A', [('2026-09-06', 15.0)])],
                [leg('QZGC.US', '2026-09-02', 15, 'QZGOOG INC-CL C')])
        self.assertEqual(r['resolved'], {})
        u = r['unresolved']['X000012']
        self.assertEqual(u['candidates'], ['QZGC.US'])
        self.assertIn('another share class or form', u['detail'])
        self.assertIn('GLOBAL X000012.US QZGC.US', u['detail'])

    def test_a_class_sibling_makes_the_pairing_ambiguous(self):
        r = res([use('X000012', 'QZGOOG INC CL A', [('2026-09-06', 15.0)])],
                [leg('QZGA.US', '2026-09-02', 15, 'QZGOOG INC-CL A'),
                 leg('QZGC.US', '2026-09-02', 15, 'QZGOOG INC-CL C')])
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['unresolved']['X000012']['candidates'],
                         ['QZGA.US', 'QZGC.US'])

    def test_ordinary_vs_adr_and_new_do_not_pair(self):
        for code_name, leg_name in (('QZX PLC ORD', 'QZX PLC SPONS ADR'),
                                    ('NEW QZY INC', 'QZY CORP'),
                                    ('QZTEL CORP SUB VTG', 'QZTEL CORP')):
            with self.subTest(code=code_name):
                r = res([use('X000012', code_name, [('2026-09-06', 5.0)])],
                        [leg('QZZ.US', '2026-09-02', 5, leg_name)])
                self.assertEqual(r['resolved'], {})

    def test_voting_words_a_broker_cuts_after_the_letter_still_pair(self):
        # Questrade cuts "CLASS B SUBORDINATE VOTING" to "CL B".
        r = res([use('X000012', 'QZTEL CORP CL B', [('2026-09-06', 5.0)])],
                [leg('QZTB.US', '2026-09-02', 5, 'QZTEL CORP-CL B SUB VTG')])
        self.assertEqual(r['resolved']['X000012']['symbol'], 'QZTB.US')


# ------------------------------------------------------------ L2 / L3

@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestUnpairedArrivalAndSubsetNames(unittest.TestCase):
    def test_an_unpaired_transfer_in_is_not_identified_by_name(self):
        r = res([use('X000012', 'QZB BANK CORP', [('2026-09-06', 50.0)])],
                names=[entry('QZB.US', 'QZB BANK CORP')])
        self.assertEqual(r['resolved'], {})
        d = r['unresolved']['X000012']['detail']
        self.assertIn('looks like QZB.US by name', d)
        self.assertIn('pairs with no transfer out', d)

    def test_the_near_transfer_is_still_named(self):
        r = res([use('X000012', 'QZB BANK CORP', [('2026-09-06', 50.0)])],
                [leg('QZN.US', '2026-09-02', 50, 'QZN NETWORKS INC')],
                names=[entry('QZB.US', 'QZB BANK CORP')])
        self.assertEqual(r['resolved'], {})
        self.assertIn('transfer out of 50 QZN.US on 2026-09-02',
                      r['unresolved']['X000012']['detail'])

    def test_one_word_names_pair_only_with_themselves(self):
        t = SC.name_tokens
        self.assertFalse(SC.names_match(t('BANK'), t('BANK OF QZLAND')))
        self.assertFalse(SC.names_match(t('QZLAND CORP'),
                                        t('QZLAND GOLD CORP')))
        self.assertTrue(SC.names_match(t('QZLAND GOLD CORP'),
                                       t('QZLAND GOLD ROYALTY CORP')))
        r = res([use('X000012', 'BANK', [('2026-09-06', 5.0)])],
                [leg('QZBK.US', '2026-09-02', 5, 'BANK OF QZLAND')])
        self.assertEqual(r['resolved'], {})


# ------------------------------------------------------------ M2 / L1

def _qt_parse(bodies, state, ticker_map=None):
    from taxjson.lib.brokerages import base
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"q{i}.csv"
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        sp = Path(d) / 'qt_symbol_codes.state'
        if isinstance(state, str):
            sp.write_text(state)
        else:
            sp.write_text(SC.state_text('qt', state))
        if ticker_map is not None:
            tm = Path(d) / 'ticker.map'
            tm.write_text(ticker_map)
            base.set_ticker_map(tm)
        err = io.StringIO()
        txs = []
        try:
            with contextlib.redirect_stderr(err):
                ctx = QuestradeBrokerage.prepare_files(paths, symbol_codes=sp)
                for p in paths:
                    par = QuestradeBrokerage()
                    par.account_context = ctx
                    txs.extend(par.parse_file(p))
        finally:
            base.set_ticker_map(None)
        return txs, err.getvalue()


STATE = {'resolved': {'X000001': {
    'symbol': 'QZM.TO', 'currency': 'CAD', 'how': 'transfer',
    'evidence': 'paired with the Interactive Brokers transfer out of 24 '
                'on 2026-09-01, account ibm'}}, 'unresolved': {}}


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestAnyTickerMapRuleWins(unittest.TestCase):
    def test_parser_keeps_the_code_under_any_rule_naming_it(self):
        for line in ('DELETE X000001.TO',
                     'DISTINCT X000001.TO QZM.TO',
                     'RENAME X000001.TO QZM2.TO 2026-12-01',
                     'JOURNAL X000001.TO QZM.TO'):
            with self.subTest(rule=line):
                txs, err = _qt_parse([TFI_M + DIV_M], STATE, line + '\n')
                self.assertEqual({t['symbol'] for t in txs},
                                 {'X000001.TO'})
                self.assertNotIn(SC.NOTE_HEAD, err)
                self.assertNotIn('keeps internal symbol code', err)


@rule("CA-ACB-CODES")
class TestRunWithRulesNamingTheCodes(unittest.TestCase):
    """DELETE X000001.TO leaves the code's rows out (as in v0.19.0: the
    inference used to rebook them as QZM.TO, past the DELETE); a dated
    RENAME of X000004.TO is the record's and the view's answer too."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(CONFIG)
        (root / "ticker.map").write_text(
            "DELETE X000001.TO\nRENAME X000004.TO QZB.TO 2026-12-01\n")
        (root / "inputs" / "ibm").mkdir(parents=True)
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "ibm" / "U5550001_2026.csv").write_text(  # pii-ok
            ib_statement())
        (root / "inputs" / "qt" / "55500001.csv").write_text(  # pii-ok
            QH + TFI_M + DIV_M + SELL_M + DIV_NAME)
        cls.r = _run(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_succeeds(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])

    def test_deleted_code_stays_deleted(self):
        book = json.loads((self.root / "work" / "qt_base.json").read_text())
        syms = {t['symbol'] for t in book['transactions']}
        self.assertFalse({s for s in syms
                          if s.startswith(('QZM', 'X000001'))}, syms)

    def test_record_and_view_name_the_rule(self):
        st = SC.read_state(self.root / "work" / "qt_symbol_codes.state")
        self.assertEqual(st['resolved'], {})
        self.assertEqual(sorted(st['mapped']), ['X000001', 'X000004'])
        r = _run(self.root, "transfers", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        codes = {c['code']: c for c in json.loads(r.stdout)['symbol_codes']}
        self.assertEqual({c: v['how'] for c, v in codes.items()},
                         {'X000001': 'ticker.map', 'X000004': 'ticker.map'})
        r = _run(self.root, "transfers")
        self.assertIn('X000004 → ticker.map rule', r.stdout)


# ------------------------------------------------------------ L4

@rule("CA-ACB-CODES")
class TestDamagedRecord(unittest.TestCase):
    BAD = {
        'deep': '[' * 200000,
        'list': json.dumps({'format': SC.FORMAT, 'resolved': []}),
        'no evidence': json.dumps({'format': SC.FORMAT, 'resolved': {
            'X000001': {'symbol': 'QZM.TO', 'currency': 'CAD'}}}),
        'spaced symbol': json.dumps({'format': SC.FORMAT, 'resolved': {
            'X000001': {'symbol': 'QZM .TO', 'evidence': 'x'}}}),
        'control symbol': json.dumps({'format': SC.FORMAT, 'resolved': {
            'X000001': {'symbol': 'QZM\x1b.TO', 'evidence': 'x'}}}),
        'newline evidence': json.dumps({'format': SC.FORMAT, 'resolved': {
            'X000001': {'symbol': 'QZM.TO', 'evidence': 'a\nnote: b'}}}),
        'not a code': json.dumps({'format': SC.FORMAT, 'resolved': {
            'QZ 1': {'symbol': 'QZM.TO', 'evidence': 'x'}}}),
        'entry not a table': json.dumps({'format': SC.FORMAT,
                                         'unresolved': {'X000001': 3}}),
    }

    def test_ignored_with_one_warning(self):
        for why, text in self.BAD.items():
            with self.subTest(why=why), tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'qt_symbol_codes.state'
                p.write_text(text)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(SC.read_state(p), {})
                    self.assertEqual(SC.read_state(p), {})
                self.assertEqual(err.getvalue().count('ignored'), 1,
                                 err.getvalue())

    def test_the_parse_books_the_code_and_does_not_crash(self):
        txs, err = _qt_parse([TFI_M + DIV_M], self.BAD['no evidence'])
        self.assertEqual({t['symbol'] for t in txs}, {'X000001.TO'})
        self.assertIn('ignored', err)
        self.assertNotIn('Traceback', err)

    def test_a_good_record_reads_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'qt_symbol_codes.state'
            p.write_text(SC.state_text('qt', STATE))
            self.assertEqual(SC.read_state(p)['resolved'], STATE['resolved'])


# ------------------------------------------------------------ L5 / L6

class TestFileNamesStayOneLine(unittest.TestCase):
    def test_shown_name_and_one_line_escape_controls(self):
        from taxjson.lib.brokerages.base import shown_name
        from taxjson.lib.out import one_line
        self.assertEqual(shown_name('/x/qz\nfake\r.csv'),
                         'qz\\nfake\\r.csv')
        self.assertEqual(one_line('a\tb\x1bc'), 'a\\tb\\x1bc')
        self.assertEqual(one_line(one_line('a\nb')), 'a\\nb')

    def test_run_console_and_diag_keep_a_newline_name_on_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(CONFIG.replace(
                "[accounts.ibm]\ntype = \"taxable\"\n\n", ""))
            d = root / "inputs" / "qt"
            d.mkdir(parents=True)
            buy = q('2026-03-02', 'Buy', 'QZM', 'QZM MINING CORP', '10',
                    net='-100.00', act='Trades', price='10', gross='-100')
            (d / "qz\nnote: forged.csv").write_text(QH + buy)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("inputs/qt/qz\\nnote: forged.csv → Questrade",
                          r.stdout)
            for out in (r.stdout, r.stderr):
                self.assertFalse([ln for ln in out.splitlines()
                                  if ln.lstrip().startswith('note: forged')])
            for diag in (root / "work").glob("*.diag"):
                with self.subTest(diag=diag.name):
                    self.assertFalse(
                        [ln for ln in diag.read_text().splitlines()
                         if ln.lstrip().startswith('note: forged')])


class TestLabelledEscapesControls(unittest.TestCase):
    def test_shown_to_a_person(self):
        from taxjson.lib.out import labelled
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"}):
            got = labelled("warning: QZ\x1b[2Jdesc\x07")
        self.assertEqual(got, "Warning: QZ\\x1b[2Jdesc\\x07")

    def test_captured_keeps_its_bytes(self):
        from taxjson.lib.out import labelled
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            self.assertEqual(labelled("warning: a\x1bb"), "warning: a\x1bb")


if __name__ == "__main__":
    unittest.main()

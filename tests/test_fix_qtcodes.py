"""Questrade internal symbol codes resolved from the project's other
exports (lib/symbol_codes; tax-logic CA-ACB-CODES / US-BASIS-CODES), and
the run console naming input files as they are on disk.

Owner report (new-user test with the Questrade WEBSITE export): shares
transferred in from another broker arrive under Questrade's internal code
(TFI / DIV / later rows), one ATTENTION line per code asking for a
ticker.map GLOBAL line. The run now pairs the transfer-in with the other
broker's transfer out (same quantity, a few days earlier, matching
names), else a unique exact name match, and says so in ONE note.

Every fixture is SYNTHETIC: invented tickers (QZ*), codes and names,
fake account ids (pii-ok: 55500001, U5550001).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def q(td, action, sym, desc, qty, net='0', act='Transfers', price='0',
      gross='0', comm='0', cur='CAD'):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},{sym},{desc},{qty},"
            f"{price},{gross},{comm},{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


TFI_M = q('2026-09-05', 'TFI', 'X000001', 'QZM MINING CORP COMMON SHARES '
          'TRANSFER IN INTERACTIVE BROKER', '24')
DIV_M = q('2026-09-30', 'DIV', 'X000001', 'QZM MINING CORP CASH DIV ON 24 '
          'SHS REC 09/20/26 PAY 09/30/26', '0', net='12.00', act='Dividends')
SELL_M = q('2026-11-02', 'Sell', 'X000001', 'QZM MINING CORP COMMON SHARES '
           'WE ACTED AS AGENT', '-24', net='307.05', act='Trades',
           price='13', gross='312', comm='-4.95')
TFI_OTHER = q('2026-09-05', 'TFI', 'X000002', 'OTHERCO INC TRANSFER IN '
              'INTERACTIVE BROKER', '10')
DIV_OTHER = q('2026-10-01', 'DIV', 'X000002', 'OTHERCO INC CASH DIV ON 10 '
              'SHS REC 09/20/26 PAY 10/01/26', '0', net='3.00',
              act='Dividends')
TFI_AMBIG = q('2026-09-06', 'TFI', 'X000003', 'QZALPHA INC CLASS A '
              'TRANSFER IN INTERACTIVE BROKER', '15')
DIV_NAME = q('2026-10-15', 'DIV', 'X000004', 'QZB BANK CORP CASH DIV ON 50 '
             'SHS REC 10/01/26 PAY 10/15/26', '0', net='20.00',
             act='Dividends')
TFI_MAPPED = q('2026-09-06', 'TFI', 'X000005', 'QZD DATA CORP TRANSFER IN '
               'INTERACTIVE BROKER', '7')

IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
           'Financial Instrument Information,Header,Asset Category,Symbol,'
           'Description,Conid,Security ID,Underlying,Listing Exch,'
           'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')
IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
               'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
IB_XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
             'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
             'Market Value,Realized P/L,Cash Amount,Code\n')
# (symbol, instrument name, shares bought, transfer-out date or None)
IB_BOOK = [('QZM', 'QZM MINING CORP', 24, '2026-09-01'),
           ('QZN', 'QZN NETWORKS INC', 10, '2026-09-01'),
           ('QZA', 'QZALPHA INC-CL A', 15, '2026-09-02'),
           ('QZC', 'QZALPHA INC-CL C', 15, '2026-09-02'),
           ('QZB', 'QZB BANK CORP', 50, None),
           ('QZD', 'QZD DATA CORP', 7, '2026-09-02')]


def ib_statement(book=IB_BOOK):
    fii = ''.join(f'Financial Instrument Information,Data,Stocks,{s},'
                  f'"{n}",99900010{i},,,TSE,1,,,COMMON,,\n'
                  for i, (s, n, _q, _d) in enumerate(book))
    trades = ''.join(f'Trades,Data,Order,Stocks,CAD,U5550001,{s},'  # pii-ok
                     f'"2026-03-02, 10:00:00",{n},10,0,{-10 * n},0,0,0,0,O\n'
                     for s, _nm, n, _d in book)
    xfers = ''.join(f'Transfers,Data,Stocks,CAD,{s},{d},ACATS,Out,'
                    f'Other Broker,5550009,{-n},0,{-10 * n},0,0,\n'  # pii-ok
                    for s, _nm, n, d in book if d)
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def use(code, name, arrivals=(), cur='CAD'):
    return SC.CodeUse(code=code, name=name, currencies=[cur],
                      arrivals=list(arrivals), rows=1)


def out(sym, date, qty, name, acct='ibm', broker='ib', cur='CAD'):
    return SC.OutLeg(acct, broker, sym, date, qty, cur,
                     [SC.name_tokens(name)] if name else [])


# ------------------------------------------------------------ unit level

@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestNames(unittest.TestCase):
    def test_noise_words_drop_and_the_class_letter_stays(self):
        # The class letter is a designator (prerelease review M1): class
        # A is not class C. Corporate-form and generic words drop.
        self.assertEqual(SC.name_tokens('QZM PLATFORMS INC-CLASS A'),
                         ('QZM', 'PLATFORMS', '~A'))
        self.assertEqual(SC.name_tokens('QZM PLATFORMS INC CL A COMMON '
                                        'STOCK'), ('QZM', 'PLATFORMS', '~A'))

    def test_match_rules(self):
        t = SC.name_tokens
        self.assertTrue(SC.names_match(t('QZALPHA NETWORKS INC CL C '
                                         'CAPITAL'),
                                       t('QZALPHA NETWORKS INC-CL C')))
        self.assertFalse(SC.names_match(t('OTHERCO INC'),
                                        t('QZN NETWORKS INC')))
        # A shared later word is not enough: the first words differ.
        self.assertFalse(SC.names_match(t('QZA NETWORKS'),
                                        t('QZB NETWORKS')))


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestResolve(unittest.TestCase):
    def res(self, uses, outs=(), names=(), mapped=lambda c: False):
        return SC.resolve(uses, list(outs), list(names),
                          listing_ok=ok_listing, mapped=mapped)

    def test_transfer_pairing(self):
        r = self.res([use('X000001', 'QZM MINING CORP COMMON SHARES',
                          [('2026-09-05', 24.0)])],
                     [out('QZM.TO', '2026-09-01', 24, 'QZM MINING CORP')])
        got = r['resolved']['X000001']
        self.assertEqual((got['symbol'], got['how']), ('QZM.TO', 'transfer'))
        self.assertIn('transfer out of 24 on 2026-09-01', got['evidence'])

    def test_window_quantity_and_names_must_agree(self):
        u = use('X000001', 'QZM MINING CORP', [('2026-09-15', 24.0)])
        for leg in (out('QZM.TO', '2026-09-04', 24, 'QZM MINING CORP'),
                    out('QZM.TO', '2026-09-14', 23, 'QZM MINING CORP'),
                    out('QZN.TO', '2026-09-14', 24, 'QZN NETWORKS INC'),
                    out('QZM.US', '2026-09-14', 24, 'QZM MINING CORP',
                        cur='USD')):
            with self.subTest(leg=leg):
                self.assertEqual(self.res([u], [leg])['resolved'], {})

    def test_two_candidates_are_ambiguous(self):
        r = self.res([use('X000003', 'QZALPHA INC CL A',
                          [('2026-09-06', 15.0)])],
                     [out('QZA.TO', '2026-09-02', 15, 'QZALPHA INC-CL A'),
                      out('QZC.TO', '2026-09-02', 15, 'QZALPHA INC-CL C')])
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['unresolved']['X000003']['candidates'],
                         ['QZA.TO', 'QZC.TO'])

    def test_one_transfer_out_pairs_with_one_code(self):
        leg = out('QZM.TO', '2026-09-01', 24, 'QZM MINING CORP')
        r = self.res([use('X000001', 'QZM MINING CORP',
                          [('2026-09-05', 24.0)]),
                      use('X000002', 'QZM MINING CORP',
                          [('2026-09-06', 24.0)])], [leg])
        self.assertEqual(r['resolved'], {})
        self.assertEqual(set(r['unresolved']), {'X000001', 'X000002'})

    def test_name_only_is_exact_and_unique(self):
        names = [SC.NameEntry('QZB.TO', SC.name_tokens('QZB BANK CORP'),
                              'QZB BANK CORP', 'ibm', 'ib')]
        r = self.res([use('X000004', 'QZB BANK CORP')], names=names)
        self.assertEqual(r['resolved']['X000004']['how'], 'name')
        # Two listings of that name: never guessed.
        names.append(SC.NameEntry('QZB2.TO', SC.name_tokens('QZB BANK'),
                                  'QZB BANK', 'ibm', 'ib'))
        r = self.res([use('X000004', 'QZB BANK CORP')], names=names)
        self.assertEqual(r['resolved'], {})
        # A partial name is not a match.
        r = self.res([use('X000004', 'QZB BANK CORP CANADA')],
                     names=names[:1])
        self.assertEqual(r['resolved'], {})

    def test_mapped_code_is_never_inferred(self):
        r = self.res([use('X000005', 'QZD DATA CORP',
                          [('2026-09-06', 7.0)])],
                     [out('QZD.TO', '2026-09-02', 7, 'QZD DATA CORP')],
                     mapped=lambda c: c == 'X000005')
        self.assertEqual(r['resolved'], {})
        self.assertEqual(r['unresolved'], {})
        self.assertEqual(r['mapped']['X000005']['how'], 'ticker.map')

    def test_note_is_one_parseable_line(self):
        line = SC.codes_note({
            'X000001': {'symbol': 'QZM.TO', 'evidence': 'paired with the '
                        'Interactive Brokers transfer out of 24 on '
                        '2026-09-01, account ibm'},
            'X000004': {'symbol': 'QZB.TO', 'evidence': "name match: "
                        "'QZB BANK CORP (THE)' on Interactive Brokers "
                        "rows of account ibm"}})
        self.assertNotIn('\n', line)
        self.assertTrue(line.startswith(SC.NOTE_HEAD + ' (2): '))
        got = SC.parse_codes_note(line)
        self.assertEqual({c: v[0] for c, v in got.items()},
                         {'X000001': 'QZM.TO', 'X000004': 'QZB.TO'})
        self.assertIn('2026-09-01', got['X000001'][1])


# ------------------------------------------------------------ the parser

def qt_parse(bodies, state=None):
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"q{i}.csv"
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        sp = None
        if state is not None:
            sp = Path(d) / 'codes.state'
            sp.write_text(SC.state_text('qt', state))
        err = io.StringIO()
        txs = []
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files(paths, symbol_codes=sp)
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                txs.extend(par.parse_file(p))
        return txs, err.getvalue()


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestParser(unittest.TestCase):
    STATE = {'resolved': {'X000001': {
        'symbol': 'QZM.TO', 'currency': 'CAD', 'how': 'transfer',
        'evidence': 'paired with the Interactive Brokers transfer out of '
                    '24 on 2026-09-01, account ibm'}}, 'unresolved': {}}

    def test_every_row_of_a_resolved_code_takes_the_ticker(self):
        txs, err = qt_parse([TFI_M + DIV_M, SELL_M], self.STATE)
        self.assertEqual({t['symbol'] for t in txs}, {'QZM.TO'})
        self.assertEqual(sorted(t['action'] for t in txs),
                         ['BUYSELL', 'DIVIDEND', 'TRANSFER'])
        self.assertNotIn('keeps internal symbol code', err)
        self.assertEqual(err.count(SC.NOTE_HEAD), 1)

    def test_scan_finds_the_codes_the_account_cannot_resolve(self):
        from taxjson.lib.brokerages.questrade import scan_code_uses
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'q.csv'
            p.write_text(QH + TFI_M + DIV_M + SELL_M + DIV_NAME)
            uses = {u.code: u for u in scan_code_uses([p])}
        self.assertEqual(sorted(uses), ['X000001', 'X000004'])
        self.assertEqual(uses['X000001'].arrivals, [('2026-09-05', 24.0)])
        self.assertEqual(SC.name_tokens(uses['X000001'].name),
                         ('QZM', 'MINING'))

    def test_unresolved_code_warns_once_per_account(self):
        state = {'resolved': {}, 'unresolved': {'X000002': {
            'reason': 'no_evidence', 'detail': 'the Interactive Brokers '
            'transfer out of 10 QZN.TO on 2026-09-01 pairs by quantity '
            'and date, but the names do not confirm it'}}}
        txs, err = qt_parse([TFI_OTHER + DIV_OTHER, DIV_OTHER.replace(
            '2026-10-01', '2026-12-01')], state)
        self.assertEqual(err.count("keeps internal symbol code 'X000002'"),
                         1, err)
        self.assertIn('names do not confirm it', err)
        self.assertIn('GLOBAL X000002.TO', err)

    def test_ticker_map_wins_over_the_inference(self):
        from taxjson.lib.brokerages import base
        with tempfile.TemporaryDirectory() as d:
            tm = Path(d) / 'ticker.map'
            tm.write_text('GLOBAL X000001.TO QZOTHER.TO\n')
            base.set_ticker_map(tm)
            try:
                txs, err = qt_parse([TFI_M], self.STATE)
            finally:
                base._TICKER_JOINS = None
        self.assertEqual({t['symbol'] for t in txs}, {'X000001.TO'})
        self.assertNotIn(SC.NOTE_HEAD, err)


# ------------------------------------------------------------ the run

def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


CONFIG = """\
[settings]
year = 2026
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2026

[accounts.ibm]
type = "taxable"

[accounts.qt]
type = "taxable"
"""


@rule("CA-ACB-CODES")
class TestRun(unittest.TestCase):
    """One run over a synthetic IB statement (transfers out) and a
    Questrade website export split over two files whose names mask
    alike: paired, name-only, non-matching, ambiguous and mapped codes."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(CONFIG)
        (root / "ticker.map").write_text("GLOBAL X000005.TO QZD.TO\n")
        (root / "inputs" / "ibm").mkdir(parents=True)
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "ibm" / "U5550001_2026.csv").write_text(  # pii-ok
            ib_statement())
        qt = root / "inputs" / "qt"
        (qt / "55500001.csv").write_text(  # pii-ok
            QH + TFI_M + DIV_M + TFI_OTHER + TFI_AMBIG + TFI_MAPPED)
        (qt / "55500001_2.csv").write_text(  # pii-ok
            QH + SELL_M + DIV_NAME + DIV_OTHER)
        cls.r = _run(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_succeeds(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])

    def test_rows_are_booked_under_the_inferred_tickers(self):
        work = self.root / "work"
        book = json.loads((work / "qt_questrade.json").read_text())
        side = json.loads((work / "qt_questrade_transfers.json").read_text())
        syms = {(t['action'], t['symbol']) for t in book['transactions']}
        self.assertIn(('DIVIDEND', 'QZM.TO'), syms)
        self.assertIn(('BUYSELL', 'QZM.TO'), syms)
        self.assertIn(('DIVIDEND', 'QZB.TO'), syms)          # name match
        self.assertIn(('DIVIDEND', 'X000002.TO'), syms)      # unresolved
        moved = {t['symbol'] for t in side['transactions']}
        self.assertEqual(moved, {'QZM.TO', 'X000002.TO', 'X000003.TO',
                                 'X000005.TO'})   # the map renames later
        base = json.loads((work / "qt_base.json").read_text())
        self.assertFalse([t for t in base['transactions']
                          if t['symbol'].startswith('X000005')])

    def test_one_note_and_one_warning_per_unresolved_code(self):
        diag = (self.root / "work" / "qt_questrade.json.diag").read_text()
        notes = [ln for ln in diag.splitlines()
                 if ln.startswith(SC.NOTE_HEAD)]
        self.assertEqual(len(notes), 1, diag)
        got = SC.parse_codes_note(notes[0])
        self.assertEqual({c: v[0] for c, v in got.items()},
                         {'X000001': 'QZM.TO', 'X000004': 'QZB.TO'})
        self.assertIn('transfer out of 24 on 2026-09-01', got['X000001'][1])
        self.assertTrue(got['X000004'][1].startswith('name match'))
        for code, n in (('X000001', 0), ('X000004', 0), ('X000005', 0),
                        ('X000002', 1), ('X000003', 1)):
            with self.subTest(code=code):
                self.assertEqual(
                    diag.count(f"keeps internal symbol code '{code}'"), n)
        self.assertIn('pairs with transfers out of QZA.TO, QZC.TO', diag)
        # The saved diagnostics mask the file names.
        self.assertNotIn('55500001', diag)  # pii-ok
        summ = (self.root / "reports" / "qt.sum").read_text()
        self.assertIn(notes[0], summ)
        # ... and the console shows the note (labelled `Info:` on a
        # terminal, `note:` when captured with TAXJSON_WIDTH=0).
        self.assertRegex(self.r.stdout, r"(Info|note): Questrade internal "
                                        r"symbol codes resolved")

    def test_console_names_files_as_on_disk_and_the_diag_masks(self):
        out = self.r.stdout
        self.assertIn("inputs/qt/55500001.csv → Questrade", out)  # pii-ok
        self.assertIn("inputs/qt/55500001_2.csv → Questrade",  # pii-ok
                      out)
        self.assertIn("inputs/ibm/U5550001_2026.csv → Interactive",  # pii-ok
                      out)
        det = (self.root / "work" / "qt_detect.diag").read_text()
        self.assertIn("inputs/qt/55***.csv → Questrade", det)
        self.assertIn("inputs/qt/55***_2.csv → Questrade", det)
        self.assertNotIn("55500001", det)  # pii-ok

    def test_transfers_view_lists_the_codes(self):
        r = _run(self.root, "transfers", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        codes = {c['code']: c for c in json.loads(r.stdout)['symbol_codes']}
        self.assertEqual(codes['X000001']['symbol'], 'QZM.TO')
        self.assertEqual(codes['X000004']['how'], 'name')
        self.assertEqual(codes['X000003']['how'], 'unresolved')
        # Booked by its ticker.map rule, and said so.
        self.assertEqual(codes['X000005']['how'], 'ticker.map')
        self.assertEqual(codes['X000005']['symbol'], '')

    def test_the_paired_arrival_is_an_own_move(self):
        r = _run(self.root, "transfers", "--json")
        rows = [t for t in json.loads(r.stdout)['transfers']
                if t['account'] == 'qt' and t['symbol'] == 'QZM.TO']
        self.assertEqual([t['arrival'] for t in rows], ['own move'])


class TestCannotDetectNamesTheFile(unittest.TestCase):
    def test_run_names_the_file_as_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(CONFIG)
            d = root / "inputs" / "qt"
            d.mkdir(parents=True)
            (d / "55500001_2.csv").write_text("col_a,col_b\n1,2\n")  # pii-ok
            r = _run(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot detect broker for "
                          "inputs/qt/55500001_2.csv", r.stderr)  # pii-ok
            self.assertIn("55500001_2.csv.toml", r.stderr)  # pii-ok


if __name__ == "__main__":
    unittest.main()

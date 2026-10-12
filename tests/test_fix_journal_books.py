"""A broker journal between a security's two lines needs no ticker.map
JOURNAL line (fix/journal-books).

- A Questrade BRW currency journal keeps its pair id (`journal_pair`) on
  the parsed rows whether the account keeps its transfers in the books
  (`transfers = true`) or aside, so the run joins the two lines as a
  currency journal (Canada, CA-XLIST-03) in both settings.
- The missing-history walks read a detected journal (a join of the run,
  a Questrade pair id, RBC's J~ reference on the two TFR legs) the way a
  JOURNAL line made them: an RBC gambit booked as a buy of one line and
  a sale of the other on one day, its TFR legs dated the settlement day,
  is no short with a TOBASE line or with no line at all.
- The "NOT in `taxjson sum`" warning is said only for sales the gains
  files lack (lib/first_run.engine_booking).

Every fixture is SYNTHETIC: invented QZ* / ZZ* names and symbols, fake
account ids (pii-ok: 55500001).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib import first_run as FR
from taxjson.lib import missing_history as MH
from taxjson.lib.core import TaxTransaction
from tax_rules import rule, rule_absent

REPO = Path(__file__).resolve().parent.parent


def _rates(path: Path, pair='USD CAD', rate='1.3500'):
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


def _config(country='canada', transfers=None, year=2025, source='USD'):
    base = 'CAD' if country == 'canada' else 'USD'
    return (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{source}"]\n'
            + (f'option_grant_timing_since = {year}\n'
               if country == 'canada' else '')
            + '\n[accounts.margin]\ntype = "taxable"\n'
            + ('' if transfers is None
               else f'transfers = {"true" if transfers else "false"}\n'))


def _sum(root):
    r = _run(root, 'sum', '--json')
    return json.loads(r.stdout)


# ------------------------------------------------- 3. Questrade pair id

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
QNAME = 'QZ US DLR CURRENCY ETF UNIT CL A'


def _q(td, action, sym, desc, qty, price='0.0', gross='0.0', net='0.0',
       cur='CAD', act='Other', sd=None):
    return (f"{td} 12:00:00 AM,{sd or td} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price},{gross},0.0,{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


QT_CSV = QH + (
    _q('2025-09-10', 'Buy', 'QZD.TO', f'{QNAME} WE ACTED AS AGENT', '300.0',
       price='14.20', gross='-4260.00', net='-4260.00', act='Trades',
       sd='2025-09-11')
    + _q('2025-09-25', 'BRW', 'QZD', f'{QNAME} JOURNAL POSITION FROM CAD '
         'BOOK VALUE: $3016.67 CNV@ 1.4138', '300.0', cur='USD')
    + _q('2025-09-25', 'BRW', 'QZD', f'{QNAME} JOURNAL POSITION TO USD',
         '-300.0')
    + _q('2025-09-30', 'Sell', 'QZD.U.TO', f'{QNAME} WE ACTED AS AGENT',
         '-300.0', price='10.00', gross='3000.00', net='3000.00', cur='USD',
         act='Trades', sd='2025-10-01'))


def _qt_project(td, transfers, country='canada'):
    root = Path(td) / f"{country}-{transfers}"
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(
        _config(country, transfers,
                source='USD' if country == 'canada' else 'CAD'))
    (root / 'inputs' / 'margin' / 'questrade.csv').write_text(QT_CSV)
    (root / 'work').mkdir()
    _rates(root / 'work' / 'to_base.csv',
           *(() if country == 'canada' else ('CAD USD', '0.7400')))
    return root


def _journal_legs(root):
    """The parsed TRANSFER legs, from the books and the sidecar."""
    out = []
    for name in ('margin_questrade.json', 'margin_questrade_transfers.json'):
        f = root / 'work' / name
        if f.is_file():
            out += [t for t in json.loads(f.read_text())['transactions']
                    if t.get('action') == 'TRANSFER']
    return out


@rule("CA-XLIST-03")
class TestQuestradePairIdKept(unittest.TestCase):
    """The pair id survives the parse with transfers in the books and
    aside, and the run joins the two lines as a currency journal."""

    def _check(self, transfers):
        with tempfile.TemporaryDirectory() as td:
            root = _qt_project(td, transfers)
            r = _run(root, 'run', '--no-input')
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, out[-3000:])
            legs = _journal_legs(root)
            self.assertEqual(sorted(t['symbol'] for t in legs),
                             ['QZD.TO', 'QZD.U.TO'])
            ids = {t.get('journal_pair') for t in legs}
            self.assertEqual(len(ids), 1, legs)
            self.assertTrue(next(iter(ids)))
            st = XL.read_state(root / 'work' / XL.STATE)
            self.assertEqual([(j.get('kind'), j['from'], j['to'])
                              for j in st['joined']],
                             [('JOURNAL', 'QZD.U.TO', 'QZD.TO')])
            self.assertIn('(currency journal 2025-09-25)',
                          ' '.join(out.split()))
            self.assertNotIn('no purchase in your files', out)

    def test_transfers_in_the_books(self):
        self._check(True)

    def test_transfers_aside(self):
        self._check(False)

    def test_book_row_keeps_the_id(self):
        t = TaxTransaction(action='TRANSFER', date='2025-09-25',
                           symbol='QZD.TO', quantity=-300.0,
                           journal_pair='2025-09-25#1')
        self.assertEqual(t.to_dict()['journal_pair'], '2025-09-25#1')
        self.assertNotIn('journal_pair',
                         TaxTransaction(action='TRANSFER',
                                        date='2025-09-25').to_dict())
        self.assertEqual(t.id, TaxTransaction(
            action='TRANSFER', date='2025-09-25', symbol='QZD.TO',
            quantity=-300.0).id)


@rule("US-XLIST-01")
@rule_absent("CA-XLIST-03", country="usa")
class TestQuestradePairIdUsa(unittest.TestCase):
    def test_usa_keeps_the_id_but_never_joins_as_a_currency_journal(self):
        with tempfile.TemporaryDirectory() as td:
            root = _qt_project(td, True, country='usa')
            r = _run(root, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            self.assertEqual(len({t.get('journal_pair')
                                  for t in _journal_legs(root)}), 1)
            st = XL.read_state(root / 'work' / XL.STATE)
            self.assertFalse(any(j.get('kind') == 'JOURNAL'
                                 for j in st['joined']))


# ------------------------------------------ 5. RBC gambit, no JOURNAL line

from test_fix_rbc import HDR, row   # noqa: E402

RNAME = 'QZD US DLR CURRENCY ETF UNIT'
EXTRACT = f'EXTRACT {RNAME} | USD | QZD.U.TO\n'


def _rbc_gambit():
    """Bought on the CAD line and sold on the USD line on one day (RBC's
    "CA JNL" trades; its newest-first row clock puts the sale first),
    the journal's TFR legs dated the settlement day."""
    desc = RNAME + ' UNSOLICITED CA JNL'
    return ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n' + HDR
            + row("May 6, 2025", "Transfers", "QZD", RNAME, "1000", "", "0",
                  "USD", "TFR - " + RNAME + " TRANSFER FROM C$  J~1")
            + row("May 6, 2025", "Transfers", "QZD", RNAME, "-1000", "",
                  "0", "CAD", "TFR - " + RNAME + " TRANSFER TO U$  J~1")
            + row("May 5, 2025", "Buy", "QZD", RNAME, "1000", "13.80",
                  "-13800", "CAD", desc, settle="May 6, 2025")
            + row("May 5, 2025", "Sell", "QZD", RNAME, "-1000", "10.10",
                  "10100", "USD", desc, settle="May 6, 2025"))


def _rbc_project(td, name, tmap, transfers):
    root = Path(td) / name
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(_config(transfers=transfers))
    (root / 'inputs' / 'margin' / 'rbc.csv').write_text(_rbc_gambit())
    (root / 'ticker.map').write_text(tmap)
    (root / 'work').mkdir()
    _rates(root / 'work' / 'to_base.csv')
    return root


class TestRbcGambitNeedsNoJournalLine(unittest.TestCase):
    """The same books with a JOURNAL line, a TOBASE line or no line:
    no short, no warning, the same total (proceeds 10100 USD at 1.35 =
    13635 CAD less 13800 CAD = -165)."""

    CASES = {
        'journal-in-books': (EXTRACT + 'JOURNAL QZD.U.TO QZD.TO\n', True),
        'tobase-aside': (EXTRACT + 'TOBASE QZD.U.TO QZD.TO\n', False),
        'tobase-in-books': (EXTRACT + 'TOBASE QZD.U.TO QZD.TO\n', True),
        'no-line-aside': (EXTRACT, False),
        'no-line-in-books': (EXTRACT, True),
    }

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.got = {}
        for name, (tmap, tr) in cls.CASES.items():
            root = _rbc_project(cls._td.name, name, tmap, tr)
            r = _run(root, 'run', '--no-input')
            cls.got[name] = (root, r)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_no_short_and_no_warning(self):
        for name, (root, r) in self.got.items():
            with self.subTest(name):
                out = ' '.join((r.stdout + r.stderr).split())
                self.assertEqual(r.returncode, 0, out[-3000:])
                self.assertNotIn('go short', out)
                self.assertNotIn('no purchase in your files', out)
                self.assertNotIn('NOT in', out)
                m = _run(root, 'find-missing-history')
                self.assertNotIn('QZD', m.stdout)

    def test_sum_is_the_gambit_and_nothing_is_uncovered(self):
        for name, (root, _r) in self.got.items():
            with self.subTest(name):
                doc = _sum(root)
                self.assertAlmostEqual(doc['totals']['total'], -165.0,
                                       delta=0.011)
                self.assertEqual(doc['no_purchase_uncovered'], [])
                self.assertEqual(doc['no_purchase_in_totals'], [])

    def test_the_walk_knows_the_journal_without_a_journal_line(self):
        root, _r = self.got['tobase-aside']
        # A TOBASE line is no journal (second pre-release review, 1):
        # the legs say it, and name its days — the J~ legs' settlement
        # day and the gambit's trade day — and no other.
        self.assertEqual(MH.journal_targets(root / 'ticker.map'), set())
        js = MH.walk_journal_symbols(root / 'work', root / 'ticker.map')
        self.assertIn('QZD.TO', js)
        self.assertEqual(sorted({d for _a, d, _s in js.days}),
                         ['2025-05-05', '2025-05-06'])


class TestJournalDetection(unittest.TestCase):
    def _leg(self, sym, qty, desc='', pair='', day='2025-05-06', acct='m'):
        return TaxTransaction(action='TRANSFER', date=day, symbol=sym,
                              quantity=qty, account=acct, description=desc,
                              journal_pair=pair)

    def test_rbc_reference_pairs_the_legs_the_renames_fold(self):
        legs = [self._leg('QZD.TO', -1000, 'TFR - X TRANSFER TO U$  J~1'),
                self._leg('QZD.U.TO', 1000, 'TFR - X TRANSFER FROM C$ J~1')]
        rows = [(t.account, t, 'rbc_direct') for t in legs]
        self.assertEqual(MH.detected_journal_symbols(rows), set())
        self.assertEqual(MH.detected_journal_symbols(
            rows, {'QZD.U.TO': 'QZD.TO'}), {'QZD.TO', 'QZD.U.TO'})
        # An overlapping copy of a leg counts once.
        self.assertEqual(MH.detected_journal_symbols(
            rows + rows[:1], {'QZD.U.TO': 'QZD.TO'}),
            {'QZD.TO', 'QZD.U.TO'})
        # RBC's reference is read on RBC's rows only (second pre-release
        # review, 12): another broker's row, or a row of no known file.
        for other in ([(a, t, 'questrade') for a, t, _b in rows],
                      [(a, t) for a, t, _b in rows]):
            self.assertEqual(MH.detected_journal_symbols(
                other, {'QZD.U.TO': 'QZD.TO'}), set())

    def test_parser_pair_id_and_unpaired_legs(self):
        legs = [self._leg('QZD.TO', -300, pair='2025-09-25#1'),
                self._leg('QZD.TO', 300, pair='2025-09-25#1')]
        self.assertEqual(MH.detected_journal_symbols(
            [(t.account, t) for t in legs]), {'QZD.TO'})
        # Legs of two accounts, or of different quantities, never pair.
        other = [self._leg('QZD.TO', -300, pair='p', acct='a'),
                 self._leg('QZD.TO', 300, pair='p', acct='b'),
                 self._leg('ZZQ.TO', -5, 'TFR J~2'),
                 self._leg('ZZQ.TO', 4, 'TFR J~2')]
        self.assertEqual(MH.detected_journal_symbols(
            [(t.account, t) for t in other]), set())

    def _gambit(self, with_legs, pair=''):
        def tx(qty, time, action='BUYSELL', desc='', day='2025-05-05'):
            return TaxTransaction(action=action, date=day, time=time,
                                  date_settle='2025-05-06', symbol='QZD.TO',
                                  quantity=qty, account='m', currency='CAD',
                                  description=desc,
                                  journal_pair=pair if action == 'TRANSFER'
                                  else '')
        rows = [tx(-1000, '09:30:00'), tx(1000, '09:30:01')]
        if with_legs:
            rows += [tx(-1000, '09:30:00', 'TRANSFER',
                        'TFR - X TRANSFER TO U$ J~1', '2025-05-06'),
                     tx(1000, '09:30:01', 'TRANSFER',
                        'TFR - X TRANSFER FROM C$ J~1', '2025-05-06')]
        return rows

    def test_walk_reads_the_books_own_journal(self):
        # The legs in the books (transfers = true) name the journal's
        # own day; its trades' day (one symbol in the books: the two
        # listings are told apart only by the parsed exports) is named
        # by walk_journal_symbols' days (second pre-release review, 1).
        self.assertEqual(len(MH.detect_missing_history(
            self._gambit(True, pair='p1'))), 1)
        days = MH.JournalDays(days=[('m', '2025-05-05', 'QZD.TO')])
        self.assertEqual(MH.detect_missing_history(
            self._gambit(True, pair='p1'), journal_symbols=days), [])
        self.assertEqual(len(MH.detect_missing_history(
            self._gambit(False))), 1)
        self.assertEqual(MH.detect_missing_history(
            self._gambit(False), journal_symbols=days), [])
        # Another day of the symbol keeps the clock.
        other = MH.JournalDays(days=[('m', '2025-05-07', 'QZD.TO')])
        self.assertEqual(len(MH.detect_missing_history(
            self._gambit(False), journal_symbols=other)), 1)
        # A caller's plain set of symbols reads every day of them.
        self.assertEqual(MH.detect_missing_history(
            self._gambit(False), journal_symbols={'QZD.TO'}), [])

    def test_settle_day_legs_read_in_leg_first(self):
        # Paired by their pair id in the books (RBC's J~ reference is read
        # from the parsed exports, where the broker is known).
        rows = self._gambit(True, pair='p1')[2:]
        self.assertEqual(len(MH.detect_missing_history(rows)), 0)
        self.assertEqual(len(MH.detect_missing_history(
            self._gambit(True)[2:])), 1)
        js = MH.JournalDays(days=[('m', '2025-05-06', 'QZD.TO')])
        keys = sorted(rows, key=lambda t: (MH._walk_key(t, js),
                                           0 if t.quantity > 0 else 1))
        self.assertEqual([t.quantity for t in keys], [1000, -1000])

    def test_state_joins_are_journals(self):
        def leg(sym, day='2025-05-06'):
            return {'account': 'm', 'broker': 'rbc_direct', 'date': day,
                    'quantity': 1000.0, 'symbol': sym}
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / 'work'
            cache.mkdir()
            (cache / XL.STATE).write_text(json.dumps({
                'format': XL.FORMAT, 'joined': [
                    {'from': 'QZD.U.TO', 'to': 'QZD.TO',
                     'out': leg('QZD.TO'), 'in': leg('QZD.U.TO')}],
                'refused': [
                    # A broker journal the user's map decided.
                    {'from': 'QZE.U.TO', 'to': 'QZE.TO', 'refused': 'map',
                     'journal': 'rbc_direct', 'out': leg('QZE.TO'),
                     'in': leg('QZE.U.TO', '2025-05-07')},
                    # A coincidence of two transfers the map decided.
                    {'from': 'QZG.U.TO', 'to': 'QZG.TO', 'refused': 'map',
                     'out': leg('QZG.TO'), 'in': leg('QZG.U.TO')}],
                'suggested': [], 'collisions': []}))
            js = MH.walk_journal_symbols(cache)
            self.assertEqual(js, {'QZD.TO', 'QZD.U.TO', 'QZE.TO',
                                  'QZE.U.TO'})
            self.assertTrue(js.on('m', '2025-05-06', 'QZD.U.TO'))
            self.assertTrue(js.on('m', '2025-05-07', 'QZE.TO'))
            self.assertFalse(js.on('m', '2025-05-08', 'QZD.TO'))


# --------------------------------------------- 6. NOT in `taxjson sum`

def _row(sales, sym='ZZQ.TO', acct='margin'):
    c = MH.MissingHistoryCandidate(symbol=sym, account=acct, currency='CAD',
                                   first_negative_date='', peak_short=-10.0,
                                   end_position=-10.0, disposition_count=1,
                                   registered=False)
    return MH.MissingHistoryRow(candidate=c, affects_year=True,
                                in_year_dispositions=len(sales),
                                in_year_proceeds=0.0, last_in_year_date='',
                                in_year_short_sales=tuple(sales))


class TestEngineBooking(unittest.TestCase):
    def _cache(self, td, recs):
        cache = Path(td)
        (cache / 'margin_gains.json').write_text(json.dumps(
            {'transactions': recs, 'inventory': []}))
        return cache

    def test_three_bookings(self):
        with tempfile.TemporaryDirectory() as td:
            cache = self._cache(td, [
                # the sale itself, closed against a long position
                {'id': 's1', 'symbol': 'ZZQ.TO', 'qty': 10,
                 'direction': 'LONG', 'date': '2025-03-03',
                 'date_settle': '2025-03-04'},
                # a short of ZZR.TO closed by a buy of the year
                {'id': 'b2', 'symbol': 'ZZR.TO', 'qty': 10,
                 'direction': 'SHORT', 'date': '2025-06-02',
                 'date_settle': '2025-06-03'}])
            got = FR.engine_booking(cache, [
                _row([('s1', 10.0)]), _row([('s2', 10.0)], 'ZZR.TO'),
                _row([('s3', 10.0)], 'ZZS.TO')], 2025)
            self.assertEqual(got, {('ZZQ.TO', 'margin'): FR.BOOKED_MATCHED,
                                   ('ZZR.TO', 'margin'):
                                   FR.BOOKED_SHORT_COVER,
                                   ('ZZS.TO', 'margin'): FR.BOOKED_OPEN})

    def test_a_cover_of_the_next_year_is_not_this_years(self):
        with tempfile.TemporaryDirectory() as td:
            cache = self._cache(td, [
                {'id': 'b2', 'symbol': 'ZZR.TO', 'qty': 10,
                 'direction': 'SHORT', 'date': '2025-12-31',
                 'date_settle': '2026-01-02'}])
            self.assertEqual(FR.engine_booking(
                cache, [_row([('s2', 10.0)], 'ZZR.TO')], 2025),
                {('ZZR.TO', 'margin'): FR.BOOKED_OPEN})
            self.assertEqual(FR.engine_booking(
                cache, [_row([('s2', 10.0)], 'ZZR.TO')], 2025,
                date_basis='trade'),
                {('ZZR.TO', 'margin'): FR.BOOKED_SHORT_COVER})

    def test_render_claims_not_in_sum_only_for_open(self):
        doc = {'year': 2025,
               'no_purchase': [{'symbol': 'ZZS.TO', 'account': 'm'}],
               'no_purchase_in_sum': [
                   {'symbol': 'ZZR.TO', 'account': 'm',
                    'booked': FR.BOOKED_SHORT_COVER},
                   {'symbol': 'ZZQ.TO', 'account': 'm',
                    'booked': FR.BOOKED_MATCHED}]}
        text = ' '.join(' '.join(FR.render(doc, width_=0)).split())
        self.assertIn('ZZS.TO (m). Those sales are NOT in `taxjson sum`',
                      text)
        self.assertIn('ZZR.TO (m). `taxjson sum` books those sales as short '
                      'sales closed by a later purchase', text)
        self.assertIn('Info: 1 position read short in 2025', text)
        self.assertEqual(text.count('NOT in'), 1)
        doc['no_purchase'] = []
        self.assertNotIn('NOT in', ' '.join(FR.render(doc, width_=0)))


def _qt_trade(td, action, sym, qty, price, net, sd):
    gross = -net if action == 'Buy' else net
    return (f"{td} 09:30:00 AM,{sd} 12:00:00 AM,{action},{sym},{sym} CORP,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,55500001,"  # pii-ok
            f"Trades,Individual\n")


class TestNotInSumEndToEnd(unittest.TestCase):
    """A sale with no purchase and no later buy is NOT in the totals;
    one a later purchase of the year closes is in them."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name)
        (root / 'inputs' / 'margin').mkdir(parents=True)
        (root / 'taxjson.toml').write_text(_config(source='CAD').replace(
            'source_currencies = ["CAD"]', 'source_currencies = []'))
        (root / 'inputs' / 'margin' / 'questrade_2025.csv').write_text(
            QH + _qt_trade('2025-02-03', 'Sell', 'ZZO.TO', -10, 30.0, 300.0,
                           '2025-02-04')
            + _qt_trade('2025-03-03', 'Sell', 'ZZC.TO', -10, 30.0, 300.0,
                        '2025-03-04')
            + _qt_trade('2025-04-01', 'Buy', 'ZZC.TO', 10, 20.0, -200.0,
                        '2025-04-02'))
        cls.root = root
        cls.r = _run(root, 'run', '--no-input')
        cls.sum = _sum(root)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_summary(self):
        out = ' '.join((self.r.stdout + self.r.stderr).split())
        self.assertEqual(self.r.returncode, 0, out[-3000:])
        self.assertIn('ZZO.TO (margin). Those sales are NOT in `taxjson '
                      'sum`', out)
        self.assertIn('ZZC.TO (margin). `taxjson sum` books those sales as '
                      'short sales closed by a later purchase', out)
        doc = json.loads((self.root / 'reports' /
                          FR.SUMMARY_FILE).read_text())
        self.assertEqual([x['symbol'] for x in doc['no_purchase']],
                         ['ZZO.TO'])
        self.assertEqual([(x['symbol'], x['booked'])
                          for x in doc['no_purchase_in_sum']],
                         [('ZZC.TO', FR.BOOKED_SHORT_COVER)])

    def test_sum(self):
        self.assertEqual([x['symbol'] for x in
                          self.sum['no_purchase_uncovered']], ['ZZO.TO'])
        self.assertEqual([(x['symbol'], x['booked']) for x in
                          self.sum['no_purchase_in_totals']],
                         [('ZZC.TO', FR.BOOKED_SHORT_COVER)])
        # The short ZZC.TO closed is in the totals: 300 - 200.
        self.assertAlmostEqual(self.sum['totals']['total'], 100.0,
                               delta=0.011)


if __name__ == '__main__':
    unittest.main()

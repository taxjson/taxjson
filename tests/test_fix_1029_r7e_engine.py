"""Regression tests for fix round 7 (wave E), group engine: US written
options at a year end, a §355 spin-off's entitled lots, futures moved
between broker accounts of one folder, warrant exercises, a .tt round
trip of corp_cash.

Every fixture is synthetic: invented tickers (SYN*, ZZ*), account labels
and amounts.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx


def _gains(book, country, year=2025):
    """pipeline.run_gains under one country: (result, stderr)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), [], [], req=GainsRequest(
            country=country, taxable=True, year=year, phantom_hint=False))
    return res, err.getvalue()


# ---------------------------------------------------------------- TJ-1160
class TestWrittenOptionClosedAtALossOnDec31(unittest.TestCase):
    """A written call sold for 100 and bought back for 300 on 2025-12-31,
    settling 2026-01-02. An option delivers nothing to close: the loss is
    realized on the closing trade, in 2025. A short sale of shares
    covered the same way stays January's (TJ-0953)."""

    OPT = 'SYN260116C00010000.US'

    def _years(self, sym, qty=1, sold=100, bought=300):
        book = [tx('BUYSELL', '2025-06-02', sym, -qty, sold),
                tx('BUYSELL', '2025-12-31', sym, qty, bought,
                   settle='2026-01-02')]
        return {y: [(t['date'], round(t['gain'], 4))
                    for t in _gains(book, 'usa', y)[0]['transactions']]
                for y in (2025, 2026)}

    @rule("US-OPT-01")
    def test_written_option_loss_stays_on_the_closing_trade(self):
        self.assertEqual(self._years(self.OPT),
                         {2025: [('2025-12-31', -200.0)], 2026: []})

    def test_a_shorted_share_still_moves_to_the_delivery_year(self):
        self.assertEqual(self._years('SYN.US', qty=10),
                         {2025: [], 2026: [('2026-01-02', -200.0)]})

    def test_written_option_gain_stays_on_the_closing_trade(self):
        self.assertEqual(self._years(self.OPT, sold=300, bought=100),
                         {2025: [('2025-12-31', 200.0)], 2026: []})


# ---------------------------------------------------------------- TJ-1191
class TestDeliveredSharesMatchedAsReplacement(unittest.TestCase):
    """50 SYN sold at a loss; a written put held at an unknown cost is
    assigned 8 days later into 100 SYN, 50 of them the loss's
    replacement. The premium is unknown for the whole delivery, so all
    100 are reported by hand, none at a definite gain."""

    PUT = 'SYN251219P00010000.US'

    def _book(self):
        return [tx('BUYSELL', '2025-01-06', 'SYN.US', 50, 1000),
                tx('BUYSELL', '2025-06-02', 'SYN.US', -50, 500),
                tx('OPENING_BALANCE', '2024-12-31', self.PUT, -1, 0,
                   time='00:00:00'),
                tx('ASSIGN', '2025-06-10', self.PUT, 1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', 100, 1000,
                   time='10:01:00'),
                tx('BUYSELL', '2025-09-02', 'SYN.US', -100, 1200)]

    @rule("US-BASIS-04")
    def test_all_the_delivered_shares_are_unknown_cost(self):
        res, _ = _gains(self._book(), 'usa')
        self.assertEqual([t for t in res['transactions']
                          if t['date'] == '2025-09-02'], [])
        manual = res.get('manual_reporting_required') or []
        self.assertEqual(sum(m['qty'] for m in manual), 100.0)

    def test_known_premium_keeps_definite_rows(self):
        book = self._book()
        book[2] = tx('BUYSELL', '2025-01-02', self.PUT, -1, 50)
        res, _ = _gains(book, 'usa')
        self.assertFalse(res.get('manual_reporting_required'))
        self.assertEqual(sum(t['qty'] for t in res['transactions']
                             if t['date'] == '2025-09-02'), 100.0)


# ---------------------------------------------------------------- TJ-1189
_QT_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Activity Type,Account #,Account Type\n')


def _qt(date, settle, action, sym, desc, qty, net, cur='USD'):
    return (f'{date},{settle},{action},{sym},{desc},{qty},'
            f'{abs(net / qty) if qty else 0:.4f},{net},0,{net},{cur},'
            f'Trades,99900001,Margin\n')


def _us_project(tmp, name, year, files, extra=''):
    from _qa_project import Path
    root = Path(tmp) / name
    for rel, text in files.items():
        p = root / 'inputs' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / 'taxjson.toml').write_text(
        f'[settings]\nyear = {year}\ncountry = "usa"\n'
        f'base_currency = "USD"\nsource_currencies = ["USD"]\n{extra}'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


class TestEdgeCasesNameTheCoverLossYear(unittest.TestCase):
    """A short covered at a loss on 2025-12-31, delivered 2026-01-02: its
    loss is 2026's. `tjs edge-cases` says so in both years' projects, the
    last-days list does not call it traded in 2026, and the 2025 project
    does not say the sale is in no total."""

    ROWS = {'margin/qt.csv': _QT_HEAD
            + _qt('2025-02-03', '2025-02-04', 'Sell', 'SYN', 'SYN CORP', -10,
                  100)
            + _qt('2025-12-31', '2026-01-02', 'Buy', 'SYN', 'SYN CORP', 10,
                  -120)}

    def _edge(self, year, rows=None, extra=''):
        import json
        import tempfile
        from _qa_project import tj
        with tempfile.TemporaryDirectory() as tmp:
            root = _us_project(tmp, 'p', year, rows or self.ROWS, extra)
            run = tj(root, 'run', '--no-input')
            doc = json.loads(tj(root, 'edge-cases', '--json').stdout)
        return doc, run.stdout + run.stderr

    @rule("US-DATE-01")
    def test_both_years_say_the_delivery_year(self):
        for year in (2025, 2026):
            with self.subTest(year=year):
                doc, _ = self._edge(year)
                st = doc['year_boundary']['straddles']
                self.assertEqual([(r['qty'], r['trade_date'], r['lands_in'],
                                   r['gain']) for r in st],
                                 [(10.0, '2025-12-31', 2026, -20.0)])
                self.assertIn("2026's Form 8949 (-20.00)", st[0]['why'])
                self.assertNotIn("2025's", st[0]['why'])

    def test_last_days_list_does_not_repeat_it_as_traded_in_2026(self):
        doc, _ = self._edge(2026)
        self.assertEqual(doc['year_boundary']['last_days'], [])

    def test_the_2025_project_finds_the_cover(self):
        _, out = self._edge(2025)
        self.assertNotIn('NOT in the totals', out)
        self.assertIn('booked as shorts covered later', out)

    def test_a_cover_at_a_gain_keeps_its_own_year(self):
        rows = {'margin/qt.csv': _QT_HEAD
                + _qt('2025-02-03', '2025-02-04', 'Sell', 'SYN', 'SYN CORP',
                      -10, 100)
                + _qt('2025-12-31', '2026-01-02', 'Buy', 'SYN', 'SYN CORP',
                      10, -80)}
        doc, _ = self._edge(2025, rows)
        st = doc['year_boundary']['straddles']
        self.assertEqual([(r['lands_in'], r['gain']) for r in st],
                         [(2025, 20.0)])

    def test_a_cover_of_two_lots_gain_and_loss_is_two_rows(self):
        """Short 10 at 15 and 10 at 5, covered all at 10: one lot is a
        gain of 2025, the other a loss of 2026."""
        rows = {'margin/qt.csv': _QT_HEAD
                + _qt('2025-02-03', '2025-02-04', 'Sell', 'SYN', 'SYN CORP',
                      -10, 150)
                + _qt('2025-03-03', '2025-03-04', 'Sell', 'SYN', 'SYN CORP',
                      -10, 50)
                + _qt('2025-12-31', '2026-01-02', 'Buy', 'SYN', 'SYN CORP',
                      20, -200)}
        doc, _ = self._edge(2025, rows)
        st = doc['year_boundary']['straddles']
        self.assertEqual(sorted((r['lands_in'], r['qty'], r['gain'])
                                for r in st),
                         [(2025, 10.0, 50.0), (2026, 10.0, -50.0)])

    def test_a_settle_date_project_is_as_before(self):
        doc, _ = self._edge(2026, extra='tax_date = "settle"\n')
        st = doc['year_boundary']['straddles']
        self.assertEqual([r['lands_in'] for r in st], [2026])
        self.assertIn('books it on the settlement date', st[0]['why'])


# ---------------------------------------------------------------- TJ-1190
class TestUnsizedExerciseAndAMarketTradeOfTheDay(unittest.TestCase):
    """100 warrants (cost 100), exercised with no size, delivering 100
    at 10: the warrants' cost stays on the delivery whatever else trades
    that day. Equal to the sized (`x1`) control."""

    def _book(self, *rows, size=0):
        return [tx('BUYSELL', '2024-01-08', 'SYN.WT.US', 100, 100),
                tx('ASSIGN', '2025-06-02', 'SYN.WT.US', -100, 0,
                   exercise_of='SYN.US',
                   **({'multiplier': size} if size else {})),
                *rows]

    def _run(self, country, *rows, size=0):
        res, err = _gains(self._book(*rows, size=size), country)
        inv = [i for i in res['inventory'] if i['symbol'] == 'SYN.US']
        return ([t for t in res['transactions'] if t['symbol'] == 'SYN.US'],
                sum(i.get('total_cost', 0.0) for i in inv), err)

    MARKET = (
        tx('BUYSELL', '2025-06-02', 'SYN.US', 100, 1000, time='10:01:00'),
        tx('BUYSELL', '2025-06-02', 'SYN.US', 900, 18000, time='14:00:00'),
        tx('BUYSELL', '2025-07-02', 'SYN.US', -100, 2500))

    @rule("US-OPT-06")
    def test_us_a_larger_market_buy_keeps_its_cost(self):
        for size in (0, 1):
            with self.subTest(size=size):
                sales, lots, err = self._run('usa', *self.MARKET, size=size)
                self.assertEqual(len(sales), 1)
                self.assertAlmostEqual(sales[0]['cost'], 1100.0, places=4)
                self.assertAlmostEqual(lots, 18000.0, places=4)
                self.assertNotIn('states no shares per warrant', err)

    @rule("CA-OPT-09")
    def test_canada_a_sale_between_keeps_the_rebuy_out(self):
        """Delivered for 500 (+100 warrants), sold for 700, 100 rebought
        at 600: gain 100, pool 600. Stamped with one time too, as an RBC
        export is: the engine may read the buys first."""
        for times in (('10:01:00', '10:02:00', '10:03:00'),
                      ('10:00:00', '10:00:00', '10:00:00')):
            with self.subTest(times=times):
                rows = (tx('BUYSELL', '2025-06-02', 'SYN.US', 100, 500,
                           time=times[0]),
                        tx('BUYSELL', '2025-06-02', 'SYN.US', -100, 700,
                           time=times[1]),
                        tx('BUYSELL', '2025-06-02', 'SYN.US', 100, 600,
                           time=times[2]))
                sales, pool, _ = self._run('canada', *rows)
                self.assertEqual(len(sales), 1)
                self.assertAlmostEqual(sales[0]['gain'], 100.0, places=4)
                self.assertAlmostEqual(pool, 600.0, places=4)

    def test_a_part_and_a_market_buy_at_another_price(self):
        rows = (tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 500,
                   time='10:01:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 1000,
                   time='10:02:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -50, 600))
        sales, _, _ = self._run('usa', *rows)
        self.assertAlmostEqual(sales[0]['cost'], 600.0, places=4)

    def test_three_parts_still_share_it(self):
        """TJ-0954 stands: one delivery in three rows, a small commission
        on one, and a market buy after."""
        rows = (tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 500,
                   time='10:01:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 25, 250,
                   time='10:02:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 25, 251, price=10.0,
                   time='10:03:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 900, 18000,
                   time='14:00:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -50, 600))
        sales, lots, err = self._run('usa', *rows)
        self.assertAlmostEqual(sales[0]['cost'], 550.0, places=4)
        self.assertAlmostEqual(lots, 501 + 18000.0 + 50, places=4)
        self.assertIn('states no shares per warrant', err)

    def test_half_a_share_per_warrant_in_parts_is_shared(self):
        rows = (tx('BUYSELL', '2025-06-02', 'SYN.US', 30, 300,
                   time='10:01:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 20, 200,
                   time='10:02:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -30, 600))
        sales, _, _ = self._run('usa', *rows)
        self.assertAlmostEqual(sales[0]['cost'], 360.0, places=4)

    def test_sized_exercise_unchanged(self):
        rows = (tx('BUYSELL', '2025-06-02', 'SYN.US', 100, 200,
                   time='10:01:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 100, 200,
                   time='10:02:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -100, 900))
        sales, _, _ = self._run('usa', *rows, size=2)
        self.assertAlmostEqual(sales[0]['cost'], 250.0, places=4)


# ---------------------------------------- TJ-1161, TJ-1192, TJ-1193 (§355)
def _spin_rows(on_shares, qty=20.0, alloc=40.0, record='2025-06-02'):
    from taxjson.lib.core import coerce_transaction_row
    from taxjson.lib.corp_actions import CorporateAction, resolve_event
    ev = CorporateAction(
        date='2025-06-16', time='09:30:00', action_type='spinoff',
        source_symbol='ZZP.US', source_isin='ZZP.US',
        target_symbol='ZZS.US', target_isin='ZZS.US',
        ratio_new=qty, ratio_old=on_shares or 1.0, qty_disposed=0.0,
        qty_received=qty, fmv=0.0, currency='USD',
        target_currency='USD', account='margin', record_date=record)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'tax_free_355', 'usa',
                             {'allocated_acb': alloc})
    return [coerce_transaction_row(r, i, 'spin') for i, r in enumerate(rows)]


class TestUs355DueBill(unittest.TestCase):
    """100 ZZP bought 2024 for 100 and 100 bought 2025-06-10 for 300; a
    spin-off with record date 2025-06-02 pays 20 ZZS on 2025-06-16,
    allocated 40. Paid ON 200 SHS: the June purchase came with a due
    bill, so each lot gives up 10%. Paid ON 100 SHS: only the old lot
    (TJ-0951)."""

    def _run(self, on_shares, qty=20.0, alloc=40.0):
        book = [tx('BUYSELL', '2024-01-08', 'ZZP.US', 100, 100),
                tx('BUYSELL', '2025-06-10', 'ZZP.US', 100, 300),
                *_spin_rows(on_shares, qty, alloc),
                tx('BUYSELL', '2025-07-02', 'ZZP.US', -100, 200),
                tx('BUYSELL', '2025-07-03', 'ZZS.US', -qty, 200)]
        return _gains(book, 'usa')

    def _june_lot(self, res):
        return {i['symbol']: i['total_cost'] for i in res['inventory']}[
            'ZZP.US']

    @rule("US-CORP-07")
    def test_paid_on_every_lot_held_each_gives_up_its_part(self):
        res, err = self._run(200)
        self.assertAlmostEqual(self._june_lot(res), 270.0, places=4)
        sale = [t for t in res['transactions'] if t['symbol'] == 'ZZP.US']
        self.assertAlmostEqual(sale[0]['cost'], 90.0, places=4)
        kids = sorted((t['term'], round(t['cost'], 4), t['qty'])
                      for t in res['transactions'] if t['symbol'] == 'ZZS.US')
        self.assertEqual(kids, [('LONG_TERM', 10.0, 10.0),
                                ('SHORT_TERM', 30.0, 10.0)])
        self.assertIn('due bill', err)

    @rule("US-CORP-07")
    def test_paid_on_the_lots_before_the_record_date_is_filtered(self):
        res, err = self._run(100, qty=10.0, alloc=10.0)
        self.assertAlmostEqual(self._june_lot(res), 300.0, places=4)
        self.assertEqual({t['term'] for t in res['transactions']},
                         {'LONG_TERM'})
        self.assertNotIn('due bill', err)
        self.assertNotIn('ATTENTION: ZZP.US', err)

    @rule("US-CORP-07")
    def test_a_count_agreeing_with_neither_keeps_the_lots_and_says_so(self):
        for on in (150, 0):
            with self.subTest(on=on):
                res, err = self._run(on, qty=10.0, alloc=10.0)
                self.assertAlmostEqual(self._june_lot(res), 292.5, places=4)
                self.assertIn('ATTENTION: ZZP.US', err)
                self.assertIn('every lot held', err)

    def test_an_ex_date_is_the_market_s_word(self):
        """An ex-date stated (a JSON book) settles it: a purchase before
        it has the right, due bill or not."""
        rows = _spin_rows(0, qty=10.0, alloc=10.0)
        for r in rows:
            if r.action == 'BUYSELL':
                r.ex_date = '2025-06-02'
        book = [tx('BUYSELL', '2024-01-08', 'ZZP.US', 100, 100),
                tx('BUYSELL', '2025-06-10', 'ZZP.US', 100, 300), *rows]
        res, err = _gains(book, 'usa')
        self.assertAlmostEqual(self._june_lot(res), 390.0, places=4)
        self.assertNotIn('ATTENTION: ZZP.US', err)

    def test_round_trip_keeps_the_count(self):
        from taxjson.bin.taxjson_convert_tt import tt_tail
        adj = [r for r in _spin_rows(200) if r.action == 'ADJUST'][0]
        self.assertEqual(adj.corp_parent_qty, 200.0)
        adj.corp_event_id = 'ev'
        self.assertIn('parent_qty=200', tt_tail(adj.to_dict(), 'ADJUST'))

    def test_no_record_date_leaves_the_row_as_it_was(self):
        for r in _spin_rows(200, record=''):
            self.assertEqual(r.corp_parent_qty, 0.0)


class TestUs355NoRemainingLotBeforeTheRecordDate(unittest.TestCase):
    """TJ-1192: the old lot sold between the record and pay dates; the
    June lot is all that is left. Which lot was entitled cannot be told
    from the books (a due bill goes with the shares too), so the figures
    stay and the run says so."""

    def test_warns_it_cannot_tell(self):
        book = [tx('BUYSELL', '2024-01-08', 'ZZP.US', 100, 100),
                tx('BUYSELL', '2025-06-10', 'ZZP.US', 100, 300),
                tx('BUYSELL', '2025-06-12', 'ZZP.US', -100, 200),
                *_spin_rows(100, qty=10.0, alloc=10.0)]
        res, err = _gains(book, 'usa')
        self.assertIn('ATTENTION: ZZP.US', err)
        self.assertIn('every share held', err)
        inv = {i['symbol']: i['total_cost'] for i in res['inventory']}
        self.assertAlmostEqual(inv['ZZP.US'], 290.0, places=4)

    def test_a_lot_before_it_says_nothing(self):
        book = [tx('BUYSELL', '2024-01-08', 'ZZP.US', 100, 100),
                *_spin_rows(100, qty=10.0, alloc=10.0)]
        _, err = _gains(book, 'usa')
        self.assertNotIn('ATTENTION: ZZP.US', err)


class TestQuestrade355EndToEnd(unittest.TestCase):
    """The items' repros from a Questrade export, `tjs elect` included."""

    def _run(self, on, qty, alloc, *more):
        import json
        import re
        import tempfile
        from _qa_project import tj
        csv = (_QT_HEAD
               + _qt('2024-01-08', '2024-01-08', 'Buy', 'ZZP', 'ZZP PARENT CORP',
                     100, -100)
               + _qt('2025-06-10', '2025-06-10', 'Buy', 'ZZP', 'ZZP PARENT CORP',
                     100, -300)
               + ''.join(more)
               + f'2025-06-16,2025-06-16,DIS,ZZS,ZZS SPINCO INC SPINOFF FROM '
                 f'SEC# X123456 ZZP PARENT CORP REC 06/02/25 PAY 06/16/25 ON '
                 f'{on} SHS,{qty},0,0,0,0,USD,Other,99900001,Margin\n'
               + _qt('2025-07-03', '2025-07-03', 'Sell', 'ZZS', 'ZZS SPINCO',
                     -qty, 100))
        with tempfile.TemporaryDirectory() as tmp:
            root = _us_project(tmp, 'p', 2025, {'margin/qt.csv': csv})
            first = tj(root, 'run', '--no-input', check=False)
            m = re.search(r'\b(\d{8}-[\w-]+-[0-9a-f]{4})\b',
                          first.stdout + first.stderr)
            self.assertTrue(m, (first.stdout + first.stderr)[-1500:])
            tj(root, 'elect', 'margin', '--set',
               f'{m.group(1)}=tax_free_355', '--hint',
               f'allocated_acb={alloc}')
            again = tj(root, 'run', '--no-input', '--details')
            doc = json.loads((root / 'work/margin_gains.json').read_text())
            stats = json.loads(tj(root, 'stats', '2025', '--json').stdout)
        return doc, again.stdout + again.stderr, stats

    @rule("US-CORP-07")
    def test_due_bill(self):
        doc, out, stats = self._run(200, 20, 40)
        inv = {i['symbol']: i['total_cost'] for i in doc['inventory']}
        self.assertAlmostEqual(inv['ZZP.US'], 360.0, places=4)
        self.assertEqual(sorted((t['term'], round(t['cost'], 4))
                                for t in doc['transactions']),
                         [('LONG_TERM', 10.0), ('SHORT_TERM', 30.0)])
        # (the engine's note is in its diagnostics: TestUs355DueBill)
        self.assertNotIn('ATTENTION: ZZP.US', out)
        self.assertEqual(stats['total']['net_pl'],
                         round(doc['summary']['total_gain'], 2))

    def test_the_old_lot_sold_before_the_pay_date_warns(self):
        doc, out, _ = self._run(
            100, 10, 10,
            _qt('2025-06-12', '2025-06-12', 'Sell', 'ZZP', 'ZZP PARENT CORP',
                -100, 200))
        inv = {i['symbol']: i['total_cost'] for i in doc['inventory']}
        self.assertAlmostEqual(inv['ZZP.US'], 290.0, places=4)
        self.assertIn('every share held that day', out)


class TestRbc355RecordDate(unittest.TestCase):
    """TJ-1193: RBC prints the same REC text as Questrade."""

    HEAD = ('Date,Activity,Symbol,Symbol Description,Quantity,Price,'
            'Settlement Date,Account,Value,Currency,Description\n')

    def _csv(self, on=100, qty=10):
        def row(d, act, sym, name, q, val, desc=''):
            return (f'{d},{act},{sym},{name},{q},{abs(val / q):.4f},{d},'
                    f'99900099,{val},USD,{desc or name}\n')
        return (self.HEAD
                + row('2024-01-08', 'Buy', 'ZZP', 'ZZP PARENT CORP', 100,
                      -100)
                + row('2025-06-10', 'Buy', 'ZZP', 'ZZP PARENT CORP', 100,
                      -300)
                + f'2025-06-16,Reorganization,ZZS,ZZS SPINCO INC,{qty},0,'
                  f'2025-06-16,99900099,0,USD,DIS - ZZS SPINCO INC SPINOFF '
                  f'ON {on} SHS FROM SEC# X123456 ZZP PARENT CORP REC '
                  f'06/02/25 PAY 06/16/25\n'
                + row('2025-07-02', 'Sell', 'ZZP', 'ZZP PARENT CORP', -100,
                      200)
                + row('2025-07-03', 'Sell', 'ZZS', 'ZZS SPINCO INC', -qty,
                      100))

    def _run(self, *a, **k):
        import json
        import re
        import tempfile
        from _qa_project import Path, tj
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'p'
            (root / 'inputs/a').mkdir(parents=True)
            (root / 'inputs/a/rbc.csv').write_text(self._csv(*a, **k))
            (root / 'taxjson.toml').write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["USD"]\n'
                '[accounts.a]\ntype = "taxable"\n')
            first = tj(root, 'run', '--no-input', check=False)
            m = re.search(r'\b(\d{8}-[\w-]+-[0-9a-f]{4})\b',
                          first.stdout + first.stderr)
            self.assertTrue(m, (first.stdout + first.stderr)[-1500:])
            tj(root, 'elect', 'a', '--set', f'{m.group(1)}=tax_free_355',
               '--hint', 'allocated_acb=10')
            tj(root, 'run', '--no-input')
            return json.loads((root / 'work/a_gains.json').read_text())

    def test_event_carries_the_record_date(self):
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        import tempfile
        from _qa_project import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'rbc.csv'
            p.write_text(self._csv())
            evs = parse_rbc_corporate_actions(p, 'a')
        self.assertEqual([e.record_date for e in evs], ['2025-06-02'])

    @rule("US-CORP-07")
    def test_end_to_end_as_questrade(self):
        doc = self._run()
        inv = {i['symbol']: i['total_cost'] for i in doc['inventory']}
        self.assertAlmostEqual(inv['ZZP.US'], 300.0, places=4)
        kids = [t for t in doc['transactions'] if t['symbol'] == 'ZZS.US']
        self.assertEqual([(t['term'], round(t['cost'], 4)) for t in kids],
                         [('LONG_TERM', 10.0)])

    @rule("US-CORP-07")
    def test_end_to_end_due_bill(self):
        doc = self._run(on=200, qty=20)
        inv = {i['symbol']: i['total_cost'] for i in doc['inventory']}
        self.assertAlmostEqual(inv['ZZP.US'], 292.5, places=4)


# ------------------------------------------------- TJ-1162, TJ-1194 (futures)
def _fut(i, date, qty, net, src, code='', fee=0.0, sym='F:SYNZ6.TO',
         cur='CAD'):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(id=i, action='BUYSELL', date=date,
                          date_settle=date, time='10:00:00', symbol=sym,
                          quantity=qty, net_amount=net, currency=cur,
                          account='margin', source_account=src,
                          open_close=code, commission=fee)


A, B = 'aaaaaaaaaa', 'bbbbbbbbbb'


def _parts(book, method='average'):
    from taxjson.lib.futures import settle_futures
    rows, _ = settle_futures(book, method)
    return [(r.id, r.quantity, round(r.net_amount, 6),
             r.settle_native.split()[0]) for r in rows]


class TestFuturesMovedBetweenBrokerAccounts(unittest.TestCase):
    """A long bought in one broker account and sold in another, coded C:
    the position moved. 50 realized, nothing left open."""

    def test_a_close_with_its_position_in_the_other_book(self):
        for m in ('average', 'fifo'):
            with self.subTest(method=m):
                self.assertEqual(
                    _parts([_fut('o', '2025-01-06', 1, 100, A, 'O'),
                            _fut('c', '2026-03-02', -1, 150, B, 'C')], m),
                    [('o', 1.0, 0.0, 'LO'), ('c', -1.0, 50.0, 'LC')])

    def test_a_short_moved_the_same(self):
        self.assertEqual(
            _parts([_fut('o', '2025-01-06', -1, 150, A, 'O'),
                    _fut('c', '2026-03-02', 1, 100, B, 'C')]),
            [('o', -1.0, 0.0, 'SO'), ('c', 1.0, 50.0, 'SC')])

    def test_a_close_past_its_own_position(self):
        for m, want in (('average', 90.0), ('fifo', 90.0)):
            with self.subTest(method=m):
                parts = _parts([_fut('a', '2025-01-06', 1, 100, A, 'O'),
                                _fut('b', '2025-01-07', 1, 110, B, 'O'),
                                _fut('c', '2026-03-02', -2, 300, B, 'C')], m)
                self.assertEqual(parts[-1], ('c', -2.0, want, 'LC'))

    def test_the_commissions_move_with_the_position(self):
        from taxjson.lib.futures import _settle
        fees = {}
        parts, _ = _settle([_fut('o', '2025-01-06', 1, 101, A, 'O', fee=1),
                            _fut('c', '2026-03-02', -1, 150, B, 'C')],
                           'average', fees)
        self.assertAlmostEqual(parts[1][0].net_amount, 49.0, places=6)
        self.assertEqual(float(fees[1][0]), 1.0)

    def test_an_opening_short_is_still_its_own_position(self):
        """TJ-0949: coded O, or with no code: a short of its own."""
        for code in ('O', ''):
            with self.subTest(code=code):
                self.assertEqual(
                    _parts([_fut('o', '2025-01-06', 1, 100, A, 'O'),
                            _fut('c', '2025-02-03', -1, 120, B, code)]),
                    [('o', 1.0, 0.0, 'LO'), ('c', -1.0, 0.0, 'SO')])

    def test_a_close_with_no_position_anywhere_stays_short(self):
        self.assertEqual(
            _parts([_fut('c', '2026-03-02', -1, 150, B, 'C'),
                    _fut('o', '2026-03-03', 1, 100, A, 'O')]),
            [('c', -1.0, 0.0, 'SO'), ('o', 1.0, 0.0, 'LO')])

    def test_a_close_of_its_own_position_leaves_the_other(self):
        self.assertEqual(
            _parts([_fut('a', '2025-01-06', 1, 100, A, 'O'),
                    _fut('b', '2025-01-07', 1, 130, B, 'O'),
                    _fut('c', '2026-03-02', -1, 150, B, 'C'),
                    _fut('d', '2026-03-03', -1, 150, A, 'C')]),
            [('a', 1.0, 0.0, 'LO'), ('b', 1.0, 0.0, 'LO'),
             ('c', -1.0, 20.0, 'LC'), ('d', -1.0, 50.0, 'LC')])

    def test_other_contracts_and_currencies_are_not_touched(self):
        self.assertEqual(
            _parts([_fut('o', '2025-01-06', 1, 100, A, 'O'),
                    _fut('x', '2025-01-07', 1, 100, B, 'O', sym='F:SYNH6.TO'),
                    _fut('c', '2026-03-02', -1, 150, B, 'C', cur='USD'),
                    _fut('d', '2026-03-03', 1, 150, A, 'O', cur='USD')])[2],
            ('c', -1.0, 0.0, 'SO'))


class TestIbFuturesInOneFolder(unittest.TestCase):
    """TJ-1162 and TJ-1194 from two IB statements in one folder."""

    def _project(self, tmp, rows_a, rows_b, country='canada'):
        from _qa_project import Path
        cur = 'CAD' if country == 'canada' else 'USD'

        def stmt(acct, rows):
            return (
                'Account Information,Header,Field Name,Field Value\n'
                f'Account Information,Data,Account,{acct}\n'
                'Financial Instrument Information,Header,Asset Category,'
                'Symbol,Description,Conid,Underlying,Listing Exch,Multiplier,'
                'Expiry,Delivery Month,Instrument Type,Issuer\n'
                'Financial Instrument Information,Data,Futures,SYNZ6,SYN '
                'DEC26,999,SYN,CME,1,2026-12-18,2026-12,FUT,\n'
                'Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
                'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
                + ''.join(f'Trades,Data,Order,Futures,{cur},SYNZ6,"{d}, '
                          f'10:00:00",{q},{p},{p},{-q * p},0,0,0,0,{c}\n'
                          for d, q, p, c in rows))
        root = Path(tmp) / 'p'
        (root / 'inputs/margin').mkdir(parents=True)
        (root / 'inputs/margin/ib_a.csv').write_text(stmt('U1234567', rows_a))
        (root / 'inputs/margin/ib_b.csv').write_text(stmt('U9990002', rows_b))
        prov = 'province = "ON"\n' if country == 'canada' else ''
        (root / 'taxjson.toml').write_text(
            f'[settings]\nyear = 2026\ncountry = "{country}"\n{prov}'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        return root

    def _run(self, rows_a, rows_b, country='canada'):
        import json
        import tempfile
        from _qa_project import tj
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, rows_a, rows_b, country)
            r = tj(root, 'run', '--no-input')
            doc = json.loads((root / 'work/margin_gains.json').read_text())
            mh = tj(root, 'find-missing-history')
        return doc, r.stdout + r.stderr, mh.stdout + mh.stderr

    @rule("CA-FX-04")
    def test_moved_position_canada(self):
        doc, out, _ = self._run([('2025-01-06', 1, 100, 'O')],
                                [('2026-03-02', -1, 150, 'C')])
        self.assertAlmostEqual(doc['summary']['total_gain'], 50.0, places=4)
        self.assertEqual(doc['inventory'], [])
        self.assertNotIn('Short position', out)

    @rule("US-FUT-01")
    def test_moved_position_us(self):
        doc, _, _ = self._run([('2025-01-06', 1, 100, 'O')],
                              [('2026-03-02', -1, 150, 'C')], 'usa')
        self.assertAlmostEqual(doc['summary']['total_gain'], 50.0, places=4)
        self.assertEqual(doc['inventory'], [])

    def test_two_positions_read_no_missing_purchase(self):
        for country in ('canada', 'usa'):
            with self.subTest(country=country):
                doc, out, mh = self._run(
                    [('2025-01-06', 1, 100, 'O'),
                     ('2026-01-05', -1, 150, 'C')],
                    [('2025-02-03', -1, 120, 'O'),
                     ('2026-02-02', 1, 110, 'C')], country)
                self.assertAlmostEqual(doc['summary']['total_gain'], 60.0,
                                       places=4)
                self.assertNotIn('Short position', out)
                self.assertNotIn('read short', out)
                self.assertNotIn('IB code C', out)
                self.assertIn('No missing-cost-basis issues', mh)

    def test_a_real_missing_purchase_is_still_said(self):
        """Both statements, and the close's position in neither."""
        _, out, mh = self._run([('2026-02-02', 1, 100, 'O')],
                               [('2026-01-05', -1, 150, 'C')])
        self.assertIn('Short position', out)
        self.assertIn('TRUNCATED HISTORY', mh)

    def test_a_cover_its_own_account_backs_is_not_unbacked(self):
        from taxjson.lib.missing_history import detect_unbacked_covers
        book = [_fut('s', '2025-01-06', -1, 150, B, 'O'),
                _fut('l', '2025-01-07', 1, 100, A, 'O'),
                _fut('m', '2025-01-08', 1, 100, A, 'O'),
                _fut('c', '2026-03-02', 1, 100, B, 'C')]
        self.assertEqual(detect_unbacked_covers(book, include_options=True),
                         [])


# ---------------------------------------------------------------- TJ-1175
class TestCorpCashRoundTrip(unittest.TestCase):
    """A corporate action's cash in the .tt file: its row keeps saying
    what it paid, so `tjs fx-cash` still counts it."""

    def _merger(self, election='rollover_s_85_1_5', hints=None, **kw):
        from taxjson.lib.corp_actions import (CorporateAction,
                                              RULES_BY_COUNTRY)
        ev = CorporateAction(
            date='2025-06-02', time='09:30:00', action_type='merger',
            source_symbol='ZZOLD.US', source_isin='ZZOLD.US',
            target_symbol='ZZNEW.US', target_isin='ZZNEW.US',
            ratio_new=1, ratio_old=2, qty_disposed=11, qty_received=5,
            fmv=0.0, currency='USD', target_currency='USD', account='m',
            cash_in_lieu=7.5, cash_in_lieu_currency='USD', **kw)
        with contextlib.redirect_stderr(io.StringIO()):
            rows = RULES_BY_COUNTRY['canada']['merger'].apply(
                ev, election, hints or {})
        for r in rows:
            r['corp_event_id'] = ev.event_id
            r['corp_election'] = election
        return rows

    def test_the_line_keeps_the_cash(self):
        from taxjson.bin.taxjson_convert_tt import (parse_tt_line,
                                                    tx_to_tt_line)
        rows = [r for r in self._merger() if r.get('corp_cash')]
        self.assertTrue(rows)
        for row in rows:
            line = tx_to_tt_line(row)
            self.assertIn('cash=7.5:USD', line)
            self.assertEqual(parse_tt_line(line, 'm')['corp_cash'],
                             row['corp_cash'])

    def test_several_parts_and_a_later_payment(self):
        from taxjson.bin.taxjson_convert_tt import (parse_tt_line,
                                                    tx_to_tt_line)
        row = dict([r for r in self._merger() if r.get('corp_cash')][0],
                   corp_cash='12 CAD; 7.5 USD @2025-06-05')
        line = tx_to_tt_line(row)
        self.assertIn('cash=12:CAD;7.5:USD:@2025-06-05', line)
        self.assertEqual(parse_tt_line(line, 'm')['corp_cash'],
                         '12 CAD; 7.5 USD @2025-06-05')

    def test_a_row_with_no_cash_writes_no_token(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        row = dict([r for r in self._merger() if r.get('corp_cash')][0],
                   corp_cash='')
        line = tx_to_tt_line(row)
        self.assertIn('corp=', line)
        self.assertNotIn('cash=', line)

    def test_a_bad_token_is_refused(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        base = 'ADJUST 2025-01-02 10:00:00 ZZQ.TO CAD -5.00000 '
        for bad in ('cash=7.5:USD', 'corp=e cash=7.5', 'corp=e cash=x:USD',
                    'corp=e cash=7.5:USD;', 'corp=e cash=7.5:usd'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_tt_line(base + bad, 'm')

    def _fx(self, rows):
        import json
        import tempfile
        from _qa_project import Path, tj
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'p'
            (root / 'inputs/m').mkdir(parents=True)
            (root / 'inputs/m/book.tt').write_text(
                'BUYSELL 2024-01-02 10:00:00 ZZOLD.US 11 USD 10 -110 0\n'
                + ''.join(tx_to_tt_line(r) + '\n' for r in rows))
            (root / 'taxjson.toml').write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                '[accounts.m]\ntype = "taxable"\n')
            tj(root, 'run', '--no-input', check=False)
            r = tj(root, 'fx-cash', '--json', check=False)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            return json.loads(r.stdout), r.stdout

    def test_fx_cash_counts_it_after_the_round_trip(self):
        for election, hints in (
                ('rollover_s_85_1_5', {}),
                ('taxable_exchange', {'fmv': 100, 'target_fmv': 100})):
            with self.subTest(election=election):
                doc, text = self._fx(self._merger(election, hints))
                self.assertEqual(doc['per_currency']['USD']['acquired'], 7.5,
                                 text[:800])


if __name__ == '__main__':
    unittest.main()

"""A row the broker marks CLOSE never opens a position, in any reader
(the owner's ruling on backlog TJ-5349; tax-logic CA-ACB-11, CA-OPT-12).

The units the account's own position backs close it. The rest closed a
position opened before the files: they are reported by hand
(`manual_reporting_required`), out of the totals, and add no long and no
written contract until the opening is supplied. Options and shares, a
purchase and a sale: IB's code C, RBC's "CLOSE CONTRACT". On a day with
no clock (RBC) the day's own OPEN rows of the other direction back the
close first.

One function decides (lib/unbacked_close.close_backing) and every reader
calls it: the Canadian engine, the run's ATTENTION, `tjs option-yield`,
the option boundary, `tjs find-missing-history`, and `tjs t1135` through
the engine's books. The cases and figures are the ruling's (grant timing,
CAD, a commission of 3.00 per option fill and 1.00 per share fill), with
their RBC twins in USD and the 4-row same-day export.

Synthetic data only: the made-up underlyings QZQ and XYZ, the accounts
U1234567 and 99900099.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from _qa_project import flat, tj
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib.unbacked_close import (Backing, CloseWalk, close_backing,
                                        close_marks, unbacked_close)

IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
           'Account Information,Header,Field Name,Field Value\n'
           'Account Information,Data,Account,U1234567\n'
           'Financial Instrument Information,Header,Asset Category,Symbol,'
           'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
           'Expiry,Delivery Month,Type,Strike,Code\n'
           'Financial Instrument Information,Data,Equity and Index Options,'
           'QZQ   251219C00030000,QZQ 19DEC25 30 C,990000101,,QZQ,CDE,100,'
           '2025-12-19,2025-12,C,30,\n'
           'Trades,Header,DataDiscriminator,Asset Category,Currency,'
           'Symbol,Date/Time,Quantity,T. Price,C. Price,'
           'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
OPT = 'QZQ251219C00030000.TO'
TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\n'
        'source_currencies = ["USD"]\noption_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')


def _opt(day, qty, px, code):
    return (f'Trades,Data,Order,Equity and Index Options,CAD,'
            f'QZQ 19DEC25 30 C,"{day}, 10:00:00",{qty},{px},0,'
            f'{-qty * px * 100:.2f},-3,0,0,0,{code}\n')


def _stk(day, qty, px, code):
    return (f'Trades,Data,Order,Stocks,CAD,QZQ,"{day}, 10:00:00",{qty},{px},'
            f'0,{-qty * px:.2f},-1,0,0,0,{code}\n')


# The ruling's cases, as IB rows.
IB_CASES = {
    'a': _opt('2025-04-01', 3, 1.10, 'C'),
    'a2': (_opt('2025-04-01', 3, 1.10, 'C') + _opt('2025-05-01', -3, 1.60, 'O')
           + _opt('2025-06-02', 3, 0.50, 'C')),
    'b': _opt('2025-04-01', -3, 1.40, 'C'),
    'b2': (_opt('2025-04-01', -3, 1.40, 'C') + _opt('2025-05-01', 3, 0.60, 'O')
           + _opt('2025-06-02', -3, 0.90, 'C')),
    'c': _opt('2025-03-03', -1, 1.50, 'O') + _opt('2025-04-01', 3, 1.10, 'C'),
    'd1': _opt('2025-03-03', -1, 1.50, 'O') + _opt('2025-04-01', 3, 1.10, 'C;O'),
    'd2': (_opt('2025-03-03', -1, 1.50, 'O') + _opt('2025-04-01', 1, 1.10, 'C;O')
           + _opt('2025-04-01', 2, 1.10, 'C;O')),
    'd0': _opt('2025-04-01', 3, 1.10, 'C;O'),
    'cover': (_stk('2025-04-01', 10, 50, 'C') + _stk('2025-05-01', 10, 60, 'O')
              + _stk('2025-06-02', -10, 70, 'C')),
    'sale': _stk('2025-04-01', -10, 50, 'C'),
}
# The .tt line that answers a case: the same state, by the user's hand.
LINES = {
    'a': f'OPENING 2025-03-31 {OPT} -3 cost=unknown\n',
    'a2': f'OPENING 2025-03-31 {OPT} -3 cost=unknown\n',
    'b': f'OPENING 2025-03-31 {OPT} 3 cost=unknown\n',
    'b2': f'OPENING 2025-03-31 {OPT} 3 cost=unknown\n',
    'd0': f'OPENING 2025-03-31 {OPT} -3 cost=unknown\n',
    'cover': 'OPENING 2025-03-31 QZQ.TO -10 cost=unknown\n',
    'sale': 'OPENING 2025-03-31 QZQ.TO 10 cost=unknown\n',
}

RBC_HEAD = ('Account Activity Detail Report\n"Account Number: 99900099"\n\n'
            'Date,Activity,Symbol,Description,Quantity,Price,Settlement Date,'
            'Currency,Value,Amount\n')
_INTEREST = ('12/31/2025,Interest,,INTEREST ON CREDIT BALANCE,0,0,12/31/2025,'
             'USD,1.25,1.25\n')
_CALL = '8XYZAA1,CALL XYZ 06/20/25 50 XYZ CORP'
_BUY_0612 = (f'06/12/2025,Buy,{_CALL} DA CLOSE CONTRACT,3,1.10,06/13/2025,USD,'
             f'-330.00,-342.45\n')
_WRITE_0610 = (f'06/10/2025,Sell,{_CALL} CA OPEN CONTRACT,-3,1.60,06/11/2025,'
               f'USD,480.00,467.55\n')
_WRITE_0606 = (f'06/06/2025,Sell,{_CALL} CA OPEN CONTRACT,-3,1.40,06/09/2025,'
               f'USD,420.00,407.55\n')
_BUY_0606 = (f'06/06/2025,Buy,{_CALL} DA CLOSE CONTRACT,3,0.90,06/09/2025,USD,'
             f'-270.00,-282.45\n')
RBC_CASES = {
    # the RBC twin of (a): a CLOSE CONTRACT buy with nothing written
    'rbc_a': (_INTEREST + f'06/06/2025,Buy,{_CALL} DA CLOSE CONTRACT,3,1.10,'
              f'06/09/2025,USD,-330.00,-342.45\n'),
    # the RBC twin of (b): a CLOSE CONTRACT sale with nothing bought
    'rbc_b': (_INTEREST + '06/06/2025,Sell,8XYZAB1,PUT XYZ 06/20/25 45 XYZ '
              'CORP CA CLOSE CONTRACT,-3,1.40,06/09/2025,USD,420.00,407.55\n'),
    # the 4-row export: the day's write listed above its buy-back (the
    # export reads newest first, so the buy-back reads first), the data
    # ending before the 06-20 expiry; and the same rows in the true order
    'day_listed': _BUY_0612 + _WRITE_0610 + _WRITE_0606 + _BUY_0606,
    'day_true': _BUY_0612 + _WRITE_0610 + _BUY_0606 + _WRITE_0606,
}
CALL = 'XYZ250620C00050000.US'


def _row(qty, code='', time='10:00:00', date='2025-04-01', no_clock='',
         description=''):
    return SimpleNamespace(quantity=qty, open_close=code, time=time, date=date,
                           no_clock=no_clock, description=description)


class TestTheOneDecision(unittest.TestCase):
    """lib/unbacked_close: the units backed, the units a no-clock day's
    own OPEN rows back, and the units unbacked."""

    @rule(R.CA_OPT_12)
    def test_a_close_past_the_position_is_unbacked(self):
        # a purchase coded C against 0, 1 and 3 written; the mirror sale
        self.assertEqual(close_backing(_row(3, 'C'), 0.0), Backing(0.0, 0.0, 3.0))
        self.assertEqual(close_backing(_row(3, 'C'), -1.0), Backing(1.0, 0.0, 2.0))
        self.assertEqual(close_backing(_row(3, 'C'), -3.0), Backing(3.0, 0.0, 0.0))
        self.assertEqual(close_backing(_row(-3, 'C'), 0.0), Backing(0.0, 0.0, 3.0))
        self.assertEqual(close_backing(_row(-3, 'C'), 2.0), Backing(2.0, 0.0, 1.0))
        # a long never backs a closing purchase, a written one a closing sale
        self.assertEqual(close_backing(_row(3, 'C'), 5.0).unbacked, 3.0)
        self.assertEqual(close_backing(_row(-3, 'C'), -5.0).unbacked, 3.0)

    def test_a_row_with_no_close_mark_has_no_unbacked_units(self):
        for code in ('', 'O'):
            self.assertEqual(close_backing(_row(3, code), 0.0).unbacked, 0.0)
            self.assertEqual(close_backing(_row(-3, code), 0.0).unbacked, 0.0)
            self.assertFalse(unbacked_close(_row(-3, code), 0.0))
        self.assertEqual(close_backing(_row(3, ''), -1.0), Backing(1.0, 0.0, 0.0))

    @rule(R.CA_OPT_12)
    def test_a_close_and_open_order_goes_by_where_the_order_began(self):
        # C;O: the order began written 1: 1 closes, the rest opens.
        self.assertEqual(close_backing(_row(3, 'C;O'), -1.0), Backing(1.0, 0.0, 0.0))
        # It began with nothing to close: the whole fill is unbacked (the
        # broker does not say how much of it closed).
        self.assertEqual(close_backing(_row(3, 'C;O'), 0.0), Backing(0.0, 0.0, 3.0))
        # The order's second fill reads the position the ORDER began from.
        walk = CloseWalk()
        self.assertEqual(walk.read('k', _row(1, 'C;O'), -1.0).unbacked, 0.0)
        self.assertEqual(walk.read('k', _row(2, 'C;O'), 0.0).unbacked, 0.0)
        # ... and a later order of the same code begins anew.
        self.assertEqual(walk.read('k', _row(2, 'C;O', time='11:00:00'), 0.0).unbacked, 2.0)

    def test_the_brokers_share_words_are_marks_too(self):
        cover = _row(10, description='SYN CORP COVER SHORT. UNSOLICITED')
        short = _row(-10, description='SYN CORP SHORT. UNSOLICITED')
        self.assertEqual(close_marks(cover), ('C',))
        self.assertEqual(close_marks(short), ('O',))
        self.assertEqual(close_marks(_row(10, description='SYN CORP')), ())
        self.assertEqual(close_backing(cover, 0.0).unbacked, 10.0)
        self.assertEqual(close_backing(cover, -10.0).unbacked, 0.0)

    def test_a_no_clock_days_open_rows_are_named_as_what_backs_the_close(self):
        # RBC: the parser put the day's OPEN ahead of the CLOSE; the
        # backed units that rest on it are `day_open`.
        walk = CloseWalk()
        opened = _row(-3, 'O', time='09:30:00', no_clock='file')
        closed = _row(3, 'C', time='09:30:01', no_clock='file')
        self.assertEqual(walk.read('k', opened, 0.0), Backing(0.0, 0.0, 0.0))
        self.assertEqual(walk.read('k', closed, -3.0), Backing(3.0, 3.0, 0.0))
        # a clock's rows (IB), or an OPEN of an earlier day: plainly backed
        walk = CloseWalk()
        walk.read('k', _row(-3, 'O', time='09:30:00'), 0.0)
        self.assertEqual(walk.read('k', _row(3, 'C', time='09:31:00'), -3.0),
                         Backing(3.0, 0.0, 0.0))
        walk = CloseWalk()
        walk.read('k', _row(-3, 'O', date='2025-03-31', no_clock='file'), 0.0)
        self.assertEqual(walk.read('k', closed, -3.0), Backing(3.0, 0.0, 0.0))


def _tt(day, sym, qty, px, fee=1, mult=1):
    """A .tt trade line of the second account."""
    total = abs(qty) * px * mult + (fee if qty > 0 else -fee)
    return f'BUYSELL {day} 10:00:00 {sym} {qty} CAD {px} {total:.2f} {fee}\n'


# A second taxable account, `other`, whose trades are all in the files.
TWO = {
    # other buys 100 and sells 100; margin sells 10 coded C with none held
    'x_shares': (_stk('2025-04-01', -10, 50, 'C'),
                 _tt('2025-02-03', 'QZQ.TO', 100, 10) + _tt('2025-06-02', 'QZQ.TO', -100, 12)),
    # ... other sells 50 + 50, then buys 20 and sells 20
    'x_shares3': (_stk('2025-04-01', -10, 50, 'C'),
                  _tt('2025-02-03', 'QZQ.TO', 100, 10) + _tt('2025-06-02', 'QZQ.TO', -50, 12)
                  + _tt('2025-07-02', 'QZQ.TO', -50, 13) + _tt('2025-08-01', 'QZQ.TO', 20, 11)
                  + _tt('2025-09-02', 'QZQ.TO', -20, 14)),
    # other holds 20 and sold 10 at a loss before margin's unbacked sale of 5
    'x_held': (_stk('2025-04-01', -5, 42, 'C'),
               _tt('2025-01-10', 'QZQ.TO', 20, 50) + _tt('2025-03-20', 'QZQ.TO', -10, 40)),
    # bought contracts: other buys 3 and sells 3; margin sells 3 coded C
    'x_bought': (_opt('2025-04-01', -3, 1.40, 'C'),
                 _tt('2025-03-03', OPT, 3, 0.60, fee=3, mult=100)
                 + _tt('2025-06-02', OPT, -3, 0.90, fee=3, mult=100)),
    # written contracts: other writes 3 and buys them back; margin buys 3 coded C
    'x_written': (_opt('2025-04-01', 3, 1.10, 'C'),
                  _tt('2025-03-03', OPT, -3, 1.60, fee=3, mult=100)
                  + _tt('2025-06-02', OPT, 3, 0.50, fee=3, mult=100)),
}


def _blend(root):
    """The pass across the taxable accounts: (total, dispositions as
    (account, gain), rows by hand as (account, proceeds), positions)."""
    doc = json.loads((root / 'work' / '.blend_gains_wash.json').read_text())
    return (round(doc['summary']['total_gain'], 2),
            [(t['account'], round(t['gain'], 2)) for t in doc['transactions']],
            [(m['account'], round(m['proceeds'], 2))
             for m in doc.get('manual_reporting_required') or []],
            [(i['symbol'], i['qty'], bool(i.get('unknown_cost'))) for i in doc['inventory']])


def _books(root):
    """What the run booked: (the total, the dispositions, the rows
    reported by hand without the opening they name, the positions)."""
    doc = json.loads((root / 'work' / 'margin_gains_wash.json').read_text())
    manual = [{k: v for k, v in m.items() if k != 'unknown_cost_from'}
              for m in doc['manual_reporting_required']]
    return {'total': round(doc['summary']['total_gain'], 2),
            'sales': doc['transactions'], 'manual': manual,
            'held': doc['inventory'], 'doc': doc}


def _by_hand(root):
    return [(m['direction'], m['qty'], round(m['proceeds'], 2))
            for m in _books(root)['doc']['manual_reporting_required']]


def _held(root):
    return [(i['symbol'], i['qty'], round(i['total_cost'], 2))
            for i in _books(root)['held']]


class _Projects(unittest.TestCase):
    """Each case's project, run once."""

    _tmp: tempfile.TemporaryDirectory
    roots: dict
    runs: dict

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.roots, cls.runs = {}, {}

    @classmethod
    def project(cls, name, files, *args, toml=TOML, other=None):
        """The project `name` with `files` in inputs/margin/ (`other`: a
        second taxable account's .tt lines), run (once)."""
        if name not in cls.roots:
            root = Path(cls._tmp.name) / name
            (root / 'inputs' / 'margin').mkdir(parents=True)
            (root / 'taxjson.toml').write_text(
                toml + ('[accounts.other]\ntype = "taxable"\n' if other else ''))
            if other:
                (root / 'inputs' / 'other').mkdir()
                (root / 'inputs' / 'other' / 'trades.tt').write_text(other)
            for fname, text in files.items():
                (root / 'inputs' / 'margin' / fname).write_text(text)
            cls.roots[name] = root
            cls.runs[name] = tj(root, 'run', '--no-input', *args)
        return cls.roots[name]

    @classmethod
    def ib(cls, case, line=False):
        files = {'ib.csv': IB_HEAD + IB_CASES[case]}
        if line:
            files['missing_history.tt'] = LINES[case]
        return cls.project(case + ('_line' if line else ''), files)

    @classmethod
    def rbc(cls, case):
        return cls.project(case, {'rbc_2025.csv': RBC_HEAD + RBC_CASES[case]})

    @classmethod
    def two(cls, case):
        ib, other = TWO[case]
        return cls.project(case, {'ib.csv': IB_HEAD + ib}, '--details', other=other)

    @classmethod
    def rows(cls, name, rows, *args, year=2025):
        """A one-account IB project of `rows`, for tax year `year`."""
        return cls.project(name, {'ib.csv': IB_HEAD.replace(
            'January 1, 2025', 'January 1, 2024') + rows}, *args,
            toml=TOML.replace('year = 2025', f'year = {year}'))

    def said(self, name):
        return flat(self.runs[name].stdout + self.runs[name].stderr)


class TestTheBooks(_Projects):
    """The Canadian engine: the ruling's figures."""

    @rule(R.CA_OPT_12)
    def test_a_a_closing_purchase_with_nothing_written(self):
        root = self.ib('a')
        self.assertEqual(_books(root)['total'], 0.0)
        self.assertEqual(_by_hand(root), [('SHORT', 3.0, -333.0)])
        self.assertEqual(_held(root), [])     # (it was a long of 3 at 333.00)
        src, = _books(root)['doc']['manual_reporting_required'][0]['unknown_cost_from']
        # the close row's own date and units
        self.assertEqual(src, {'account': 'margin', 'symbol': OPT,
                               'date': '2025-04-01', 'quantity': -3.0,
                               'marked_close': True})

    @rule(R.CA_OPT_12)
    def test_a2_the_later_documented_write_is_a_write_again(self):
        # 477.00 less 153.00: it was 144.00, the write read as the sale
        # of the phantom long, and a second phantom long of 3 stayed.
        root = self.ib('a2')
        self.assertEqual(_books(root)['total'], 324.0)
        self.assertEqual(_by_hand(root), [('SHORT', 3.0, -333.0)])
        self.assertEqual(_held(root), [])

    @rule(R.CA_OPT_12)
    def test_b_a_closing_sale_with_nothing_bought_is_no_write(self):
        root = self.ib('b')
        self.assertEqual(_books(root)['total'], 0.0)        # (it was 417.00)
        self.assertEqual(_by_hand(root), [('LONG', 3.0, 417.0)])
        self.assertEqual(_held(root), [])     # (it was 3 written)

    @rule(R.CA_OPT_12)
    def test_b2_the_later_documented_pair(self):
        root = self.ib('b2')
        self.assertEqual(_books(root)['total'], 84.0)       # (it was 501.00)
        self.assertEqual(_by_hand(root), [('LONG', 3.0, 417.0)])
        self.assertEqual(_held(root), [])

    @rule(R.CA_OPT_12)
    def test_c_the_units_the_position_backs_close_it(self):
        # written 1, a closing purchase of 3: 1 closes it (147.00 less
        # 111.00), 2 are reported by hand.
        root = self.ib('c')
        self.assertEqual(_books(root)['total'], 36.0)
        self.assertEqual(_by_hand(root), [('SHORT', 2.0, -222.0)])
        self.assertEqual(_held(root), [])     # (it was a long of 2 at 222.00)

    @rule(R.CA_OPT_12)
    def test_d_a_backed_close_and_open_order_is_as_it_was(self):
        for case, total, cost in (('d1', 36.0, 222.0), ('d2', 34.0, 223.0)):
            with self.subTest(case):
                root = self.ib(case)
                self.assertEqual(_books(root)['total'], total)
                self.assertEqual(_by_hand(root), [])
                self.assertEqual(_held(root), [(OPT, 2.0, cost)])
                self.assertNotIn('unbacked close', self.runs[case].stdout)

    @rule(R.CA_OPT_12)
    def test_d0_a_close_and_open_order_begun_with_nothing(self):
        root = self.ib('d0')
        self.assertEqual(_books(root)['total'], 0.0)
        self.assertEqual(_by_hand(root), [('SHORT', 3.0, -333.0)])
        self.assertEqual(_held(root), [])

    @rule(R.CA_ACB_11)
    def test_a_share_cover_adds_no_long(self):
        # a marked cover of 10 at 50, then 10 bought at 60 and sold at
        # 70: 699.00 less 601.00 (it was 148.00 on an average cost of 551).
        root = self.ib('cover')
        self.assertEqual(_books(root)['total'], 98.0)
        self.assertEqual(_by_hand(root), [('SHORT', 10.0, -501.0)])
        self.assertEqual(_held(root), [])

    @rule(R.CA_ACB_11)
    def test_a_share_sale_marked_closing_is_no_short(self):
        root = self.ib('sale')
        self.assertEqual(_books(root)['total'], 0.0)
        self.assertEqual(_by_hand(root), [('LONG', 10.0, 499.0)])
        self.assertEqual(_held(root), [])     # (it was 10 short)

    @rule(R.CA_OPT_12, R.CA_ACB_11)
    def test_the_users_line_and_the_rule_give_the_same_state(self):
        """An answered project is unchanged: the OPENING cost=unknown line
        and the broker's mark alone book the same dispositions, the same
        rows by hand, the same positions and the same totals."""
        for case in sorted(LINES):
            with self.subTest(case):
                mark, line = _books(self.ib(case)), _books(self.ib(case, line=True))
                for part in ('total', 'sales', 'manual', 'held'):
                    self.assertEqual(mark[part], line[part], part)
                self.assertEqual(mark['doc']['summary'], line['doc']['summary'])
                self.assertEqual(mark['doc'].get('inventory_year_end'),
                                 line['doc'].get('inventory_year_end'))
                # (the answered one names the line, and says no ATTENTION)
                self.assertNotIn('unbacked close',
                                 self.runs[case + '_line'].stdout)

    @rule(R.CA_OPT_12)
    def test_the_rbc_twins(self):
        root = self.rbc('rbc_a')
        self.assertEqual(_books(root)['total'], 0.0)
        self.assertEqual(_by_hand(root), [('SHORT', 3.0, -462.31)])
        self.assertEqual(_held(root), [])
        # T1135 counted the phantom long's 462.31 CAD.
        self.assertIn('Maximum total cost in 2025: 0.00 CAD',
                      tj(root, 't1135').stdout)
        root = self.rbc('rbc_b')
        self.assertEqual(_books(root)['total'], 0.0)        # (it was 550.19)
        self.assertEqual(_by_hand(root), [('LONG', 3.0, 550.19)])
        self.assertEqual(_held(root), [])


class TestTheSameDayClause(_Projects):
    """RBC writes no time: the day's own OPEN row backs the CLOSE first,
    whatever order the export lists them in, and the parse says when no
    export proves it (backlog TJ-5363)."""

    @rule(R.CA_OPT_12)
    def test_one_total_in_either_file_order_and_no_phantom(self):
        for case in ('day_listed', 'day_true'):
            with self.subTest(case):
                root = self.rbc(case)
                self.assertEqual(_books(root)['total'], 337.77)
                self.assertEqual(_by_hand(root), [])
                self.assertEqual(_held(root), [])
                doc = json.loads(tj(root, 'option-yield', 'XYZ', 'all',
                                    '--json').stdout)
                self.assertEqual(doc['open'], [])
                self.assertEqual([(c['opened'], c['closed']) for c in doc['closed']],
                                 [('2025-06-06', '2025-06-06'),
                                  ('2025-06-10', '2025-06-12')])
        self.assertEqual(_books(self.rbc('day_listed'))['sales'],
                         _books(self.rbc('day_true'))['sales'])

    def test_an_attention_when_no_export_covers_the_expiry(self):
        said = [ln for ln in self.runs_of('day_listed').splitlines()
                if 'open/close order' in ln]
        self.assertEqual(len(said), 1, said)
        self.assertIn(f"Warning: rbc_2025.csv: rows of {CALL} on 2025-06-06 "
                      "put in the broker's open/close order", flat(said[0]))
        text = flat((self.rbc('day_listed') / 'reports' / 'margin.sum').read_text())
        for words in ('warning: ATTENTION: rbc_2025.csv: rows of',
                      'the OPEN CONTRACT sale of 3 before the CLOSE CONTRACT '
                      'purchase of 3',
                      "The order rests on that day's own rows",
                      'Add the export covering those dates'):
            self.assertIn(words, text)
        # The true order needs no reading: nothing is said.
        self.assertNotIn('open/close order', self.runs_of('day_true'))
        self.assertNotIn('unbacked close', self.runs_of('day_listed'))

    def runs_of(self, case):
        self.rbc(case)
        return self.runs[case].stdout + self.runs[case].stderr


class TestTheReadersAgree(_Projects):
    """One reading in every tool."""

    def test_the_attention_says_what_is_reported_by_hand(self):
        self.ib('a')
        # the run's one line: the series, the account, the day, the units
        self.assertIn(f'Warning: unbacked close: {OPT} (margin) 2025-04-01, '
                      f'3 bought to close: reported by hand',
                      flat(self.runs['a'].stdout))
        sums = flat((self.ib('a') / 'reports' / 'margin.sum').read_text())
        for words in ('The broker marks the purchase of 3 CLOSING and the '
                      'account holds no written contract: it closed '
                      'contracts written before your files',
                      'Not in `tjs sum`: cost 333.00 CAD, premium unknown; '
                      'no long is added.',
                      'Add the write (the earlier export or a .tt line, or its '
                      '`OPENING ... cost=unknown` line)',
                      'map the symbol it was opened under'):
            self.assertIn(words, sums)
        self.assertNotIn('NEW long option', sums)
        # the mirror, a sale
        sums = flat((self.ib('b') / 'reports' / 'margin.sum').read_text())
        for words in (f'unbacked close: {OPT} (margin) 2025-04-01, 3 sold to '
                      f'close: reported by hand',
                      'The broker marks the sale of 3 CLOSING and the account '
                      'holds no bought contract',
                      'proceeds 417.00 CAD, cost unknown; no written contract '
                      'is added.', 'Add the purchase'):
            self.assertIn(words, sums)
        self.assertNotIn('NEW written option', sums)
        # a part close names the units
        sums = flat((self.ib('c') / 'reports' / 'margin.sum').read_text())
        self.assertIn('2 of 3 bought to close: reported by hand', sums)
        self.assertIn('the account holds 1 written contract: 2 of them closed '
                      'contracts written before your files', sums)
        self.assertIn('cost 222.00 CAD, premium unknown', sums)
        # (no line is offered where the account's own contracts back a
        # part: dated before them it would take their close out too)
        self.assertIn('Add the write (the earlier export or a .tt line), or map', sums)
        # shares: a cover, and a sale (the `short:` line the run scopes to
        # the tax year)
        sums = flat((self.ib('cover') / 'reports' / 'margin.sum').read_text())
        for words in ('unbacked close: QZQ.TO (margin) 2025-04-02, 10 bought '
                      'to cover: reported by hand',
                      'as closing a short (code C)',
                      'cost 501.00, proceeds unknown; no long is added.'):
            self.assertIn(words, sums)
        sums = flat((self.ib('sale') / 'reports' / 'margin.sum').read_text())
        for words in ('short: QZQ.TO (margin): the broker codes the sale on '
                      '2025-04-01 CLOSING (IB code C), but the data holds no '
                      'position to close',
                      'it is reported by hand, out of the totals, and adds no '
                      'short, until the missing purchase is supplied',
                      '`tjs find-missing-history --write-purchases`'):
            self.assertIn(words, sums)
        self.assertNotIn('booked as a new short', sums)
        self.assertNotIn('--write-missing-history', sums)

    def test_option_yield_reads_the_same_trades(self):
        doc = json.loads(tj(self.ib('a2'), 'option-yield', 'QZQ', 'all',
                            '--json').stdout)
        self.assertEqual(doc['open'], [])
        self.assertEqual(
            [(c['opened'], c['closed'], c['contracts'], c['pnl'])
             for c in doc['closed']],
            [('', '2025-04-01', 3.0, None), ('2025-05-01', '2025-06-02', 3.0, 324.0)])
        # the part close: 222.00 of the 333.00, as the books
        doc = json.loads(tj(self.ib('c'), 'option-yield', 'QZQ', 'all',
                            '--json').stdout)
        self.assertEqual(sorted((c['contracts'], c['close_cash'], c['pnl'])
                                for c in doc['closed']),
                         [(1.0, -111.0, 36.0), (2.0, -222.0, None)])

    def test_the_option_boundary_holds_no_write_for_a_closing_sale(self):
        out = tj(self.ib('b'), 'option-boundary').stdout
        self.assertIn('Nothing to amend', out)
        self.assertNotIn('broker-closing', out)

    def test_find_missing_history_lists_them_as_the_run_applies_them(self):
        r = tj(self.ib('c'), 'find-missing-history', '--include-options')
        self.assertEqual(r.returncode, 0)
        out = flat(r.stdout)
        self.assertIn('COVERS OF A SHORT OPENED BEFORE THE DATA — missing '
                      'history, as the run applies it: 1', out)
        # the unbacked units' share of the row's cost (it showed 333.00)
        self.assertIn(f'{OPT} margin 2025-04-02 2.0000 222.00 code C', out)
        # a part close: no line is offered (it would take the documented
        # contract's close out of the totals too)
        self.assertNotIn('cost=unknown', out)
        self.assertNotIn('--write-missing-history', out)
        # (a2) one cover: the documented write's own buy-back is backed;
        # the line that declares the write (--write-missing-history
        # drafts none for a contract, and was the advice)
        out = flat(tj(self.ib('a2'), 'find-missing-history',
                      '--include-options').stdout)
        self.assertIn('as the run applies it: 1', out)
        self.assertNotIn('2025-06-03', out)
        self.assertIn('`OPENING <date> <SYMBOL> -<qty> cost=unknown`', out)
        self.assertNotIn('--write-missing-history', out)
        # a closing sale: the same table, said as applied
        r = tj(self.ib('b'), 'find-missing-history', '--include-options')
        self.assertEqual(r.returncode, 0)
        self.assertIn('TRUNCATED HISTORY', r.stdout)
        self.assertIn('As the run applies it: a sale the broker marks closing',
                      flat(r.stdout))

    def test_readiness_names_the_kinds(self):
        def issues(case):
            doc = json.loads(tj(self.ib(case), 'sum', '--json').stdout)
            return {i['kind']: i for i in doc['readiness']['issues']}
        sale = issues('b')
        self.assertIn('unknown-cost', sale)
        self.assertNotIn('unbacked-cover', sale)
        self.assertNotIn('no-purchase', sale)
        # a purchase: said once, as the cover it is
        buy = issues('a')
        self.assertEqual(sorted(buy), ['unbacked-cover'])
        self.assertNotIn('new long', buy['unbacked-cover']['detail'])
        self.assertEqual(issues('d1'), {})
        for case in ('cover', 'sale'):
            self.assertTrue(issues(case), case)

    def test_strict_stops_on_any_unbacked_close(self):
        for case in ('a', 'b', 'cover', 'sale'):
            with self.subTest(case):
                root = self.ib(case)
                self.assertEqual(self.runs[case].returncode, 0)     # (`tjs run` stays 0)
                r = tj(root, 'run', '--no-input', '--strict', check=False)
                self.assertEqual(r.returncode, 1, r.stdout[-800:])
                # (a share sale: the stop it always had, a position short
                # where none can exist)
                self.assertIn('--strict: margin: 1 position(s) go short where '
                              'none can exist' if case == 'sale' else
                              '--strict: margin: 1 trade(s) the broker marks '
                              'closing', flat(r.stdout + r.stderr))
        r = tj(self.ib('d1'), 'run', '--no-input', '--strict', check=False)
        self.assertNotIn('the broker marks closing', flat(r.stdout + r.stderr))
        self.assertNotIn('go short where none can exist',
                         flat(r.stdout + r.stderr))


class TestAnotherAccountOfThePool(_Projects):
    """A second taxable account whose own trades are all in the files
    (the fix round's ruling on the review's P1-1). Shares and bought
    contracts are one pool across the taxable accounts (CA-ACB-01,
    CA-OPT-12): the units a marked close shows were held before the files
    were in it, so its cost is unknown until it is fully sold, as an
    OPENING cost=unknown line leaves it. A written contract is the
    obligation of the account that wrote it: another account's unknown
    write is no part of it."""

    @rule(R.CA_ACB_11, R.CA_ACB_01)
    def test_shares_the_pools_cost_is_unknown_until_it_is_sold(self):
        total, sales, by_hand, held = _blend(self.two('x_shares'))
        self.assertEqual((total, sales, held), (0.0, [], []))
        self.assertEqual(by_hand, [('margin', 499.0), ('other', 1199.0)])
        # The words: margin's sale is the one the broker marks; other's row
        # only shared the pool, and says whose close is behind it.
        said = self.said('x_shares')
        self.assertIn('The broker marks the sale closing and your files hold '
                      'no purchase of the units', said)
        self.assertIn("the other row shared a pool with units bought before "
                      "your files (margin's close of QZQ.TO on 2025-04-01, "
                      "which the broker marks closing with nothing held)", said)
        self.assertNotIn('The broker marks them closing', said)
        # ... and once the pool has emptied, a round trip is counted.
        total, sales, by_hand, held = _blend(self.two('x_shares3'))
        self.assertEqual((total, sales), (58.0, [('other', 58.0)]))
        self.assertEqual([a for a, _p in by_hand], ['margin', 'other', 'other'])

    @rule(R.CA_ACB_11)
    def test_shares_still_held_are_listed_at_an_unknown_cost_and_say_why(self):
        root = self.two('x_held')
        total, sales, by_hand, held = _blend(root)
        # other's earlier loss stands: the opening is no purchase.
        self.assertEqual((total, sales), (-101.5, [('other', -101.5)]))
        self.assertEqual(by_hand, [('margin', 209.0)])
        self.assertEqual(held, [('QZQ.TO', 10.0, True)])
        out = flat(tj(root, 'list').stdout)
        self.assertIn("1 position(s) hold units of unknown cost (a close the "
                      "broker marks with nothing held, margin's of 2025-04-01: "
                      "its units were in the same pool)", out)
        self.assertNotIn('an opening cost=unknown', out)
        row, = json.loads(tj(root, 'list', '--json').stdout)['rows']
        self.assertEqual([(s['account'], s['date']) for s in row['unknown_cost_from']],
                         [('margin', '2025-04-01')])

    @rule(R.CA_OPT_12)
    def test_bought_contracts_pool_as_shares_do(self):
        total, sales, by_hand, held = _blend(self.two('x_bought'))
        self.assertEqual((total, sales, held), (0.0, [], []))
        self.assertEqual(by_hand, [('margin', 417.0), ('other', 267.0)])

    @rule(R.CA_OPT_12)
    def test_a_written_contract_is_its_own_accounts(self):
        # other's write and buy-back: 477.00 less 153.00, in the totals
        # (the buy-back went by hand, and the total read 477.00); margin's
        # closing purchase alone is reported by hand.
        total, sales, by_hand, held = _blend(self.two('x_written'))
        self.assertEqual(total, 324.0)
        self.assertEqual(sales, [('other', 477.0), ('other', -153.0)])
        self.assertEqual(by_hand, [('margin', -333.0)])
        self.assertEqual(held, [])
        self.assertIn('1 buy closing a short sold before your files', self.said('x_written'))


class TestOneStoryPerRow(_Projects):
    """The fix round's P2 and P3 cases: every reader says of a row what
    the books did with it."""

    @rule(R.CA_OPT_12)
    def test_an_expiry_past_the_accounts_contracts(self):
        # 1 written, an expiry row of 3 the broker marks closing: 1
        # expires (its premium was the write's), 2 expired contracts
        # written before the files, reported by hand, everywhere.
        root = self.rows('exp_part', _opt('2025-03-03', -1, 1.50, 'O').replace('10:00:00', '09:00:00')
                         + 'Trades,Data,Order,Equity and Index Options,CAD,QZQ 19DEC25 30 C,'
                           '"2025-12-19, 16:20:00",3,0,0,0.00,0,0,0,0,C;Ep\n')
        self.assertEqual(_books(root)['total'], 147.0)
        self.assertEqual(_by_hand(root), [('SHORT', 2.0, 0.0)])
        self.assertEqual(_held(root), [])
        said = self.said('exp_part')
        self.assertIn('2 of 3 expired: reported by hand', said)
        self.assertIn('1 buy closing a short sold before your files', said)
        self.assertNotIn('new long', said)
        self.assertNotIn('more assignments than writes', said)
        doc = json.loads(tj(root, 'sum', '--json').stdout)
        self.assertEqual([i['kind'] for i in doc['readiness']['issues']],
                         ['unbacked-cover'])
        out = tj(root, 'find-missing-history', '--include-options').stdout
        self.assertIn('as the run applies it: 1', flat(out))
        self.assertNotIn('$0-COST', out)

    def test_strict_keeps_the_tax_years_scope(self):
        # A purchase to close of 2024 with nothing written; 2025 holds a
        # documented write and its buy-back. Nothing of 2025 is by hand:
        # the run says no unbacked line, and --strict does not stop on it.
        rows = (_opt('2024-06-03', 3, 1.10, 'C') + _opt('2025-05-01', -3, 1.60, 'O')
                + _opt('2025-06-02', 3, 0.50, 'C'))
        root = self.rows('early_opt', rows)
        self.assertEqual(_books(root)['total'], 324.0)
        self.assertNotIn('unbacked close', self.said('early_opt'))
        r = tj(root, 'run', '--no-input', '--strict', check=False)
        self.assertNotIn('the broker marks closing', flat(r.stdout + r.stderr))
        # ... and in its own year it does.
        root = self.rows('early_opt_2024', rows, year=2024)
        r = tj(root, 'run', '--no-input', '--strict', check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('--strict: margin: 1 trade(s) the broker marks closing',
                      flat(r.stdout + r.stderr))

    @rule(R.CA_ACB_11)
    def test_no_line_is_drafted_for_a_partly_backed_close(self):
        # 5 bought, 10 sold coded C: 48.50 in the totals, 249.50 by hand.
        # A line for the 5 would put the documented 5 at an unknown cost
        # too (total 0.00): the writer drafts none, and the run offers none.
        root = self.rows('partial_sh', _stk('2025-03-03', 5, 40, 'O')
                         + _stk('2025-04-01', -10, 50, 'C'))
        self.assertEqual(_books(root)['total'], 48.5)
        self.assertEqual(_by_hand(root), [('LONG', 5.0, 249.5)])
        self.assertNotIn('--write-missing-history', self.said('partial_sh'))
        sums = flat((root / 'reports' / 'margin.sum').read_text())
        # what the account holds, and no promise of a cost the export lacks
        self.assertIn('but the data holds 5 of the 10 units it sells — the rest is '
                      'reported by hand', sums)
        self.assertIn('drafts one if the export carries the broker\'s cost', sums)
        self.assertNotIn('holds no position to close', sums)
        r = tj(root, 'find-missing-history', '--write-missing-history', check=False)
        self.assertIn('not written: 1 symbol(s) with a trade the broker marks '
                      'closing that the account holds only a part of',
                      flat(r.stdout + r.stderr))
        self.assertFalse((root / 'inputs' / 'margin' / 'missing_history.tt').exists())
        tj(root, 'run', '--no-input')
        self.assertEqual(_books(root)['total'], 48.5)

    def test_a_mixed_pair_names_no_line(self):
        # a sale coded C;O with nothing held, then its cover coded C
        self.rows('co_sale', _stk('2025-04-01', -10, 50, 'C;O') + _stk('2025-05-01', 10, 45, 'C'),
                  '--details')
        said = self.said('co_sale')
        self.assertIn('(IB code C;O)', (self.roots['co_sale'] / 'reports' / 'margin.sum').read_text())
        self.assertNotIn('the line OPENING', said)
        self.assertNotIn('the units came by', said)
        self.assertIn('The broker marks the sale closing', said)
        self.assertIn('the broker marks the buy as closing a short', said)
        self.assertIn('2025-04-02 (traded 2025-04-01)', said)

    def test_stats_count_an_unbacked_close_once(self):
        for case in ('a2', 'c'):
            with self.subTest(case):
                doc = json.loads(tj(self.ib(case), 'stats', '--json').stdout)
                self.assertEqual(doc['tainted_skipped'], 1)


class TestTheUsBooksGoByPosition(_Projects):
    """The US engine reads no open/close mark (backlog TJ-5369): its
    figures and its words are as they were."""

    US = ('[settings]\nlocal_timezone = "America/New_York"\nyear = 2025\n'
          'country = "usa"\nbase_currency = "USD"\nsource_currencies = ["CAD"]\n'
          '[accounts.margin]\ntype = "taxable"\n')

    def test_an_rbc_day_no_export_proves_keeps_the_file_order(self):
        body = RBC_HEAD + _INTEREST + _BUY_0612 + _WRITE_0606 + _BUY_0606
        us = self.project('us_day', {'rbc_2025.csv': body}, toml=self.US)
        said = self.said('us_day')
        self.assertNotIn('open/close order', said)
        rows = json.loads((us / 'work' / 'margin_base.json').read_text())['transactions']
        day = sorted((t['time'], t['quantity']) for t in rows if t['date'] == '2025-06-06')
        self.assertEqual([q for _t, q in day], [3.0, -3.0])        # the buy-back first
        # the same export in a Canadian project: the write first, and said
        ca = self.project('ca_day', {'rbc_2025.csv': body})
        rows = json.loads((ca / 'work' / 'margin_base.json').read_text())['transactions']
        day = sorted((t['time'], t['quantity']) for t in rows if t['date'] == '2025-06-06')
        self.assertEqual([q for _t, q in day], [-3.0, 3.0])
        self.assertIn('open/close order', self.said('ca_day'))
        # one CLOSE row: nothing for the file's order to choose
        self.assertNotIn("is the file's order", self.said('ca_day'))

    def test_the_parser_states_the_fact_and_the_country_reads_it(self):
        from taxjson.characterize import characterize_rows
        said = []
        for country, times in (('canada', ['09:30:00', '09:30:01']), (None, ['09:30:01', '09:30:00']),
                               ('usa', ['09:30:01', '09:30:00'])):
            rows = [{'time': '09:30:01', 'open_close_time': '09:30:00'},
                    {'time': '09:30:00', 'open_close_time': '09:30:01'}]
            characterize_rows(rows, country=country, open_close_days=['the day'],
                              attention=lambda w, c=country: said.append((c, w)))
            self.assertEqual([r['time'] for r in rows], times)
            self.assertFalse(any('open_close_time' in r for r in rows))
        self.assertEqual(said, [('canada', 'the day')])      # (no country: nothing is guessed)

    def test_a_phantom_long_past_expiry_is_no_loss_to_book(self):
        from datetime import date
        from taxjson.lib.option_boundary import expired_open
        from taxjson.model.transaction import TaxTransaction
        buy = TaxTransaction(action='BUYSELL', date='2025-04-01', date_settle='2025-04-02',
                             time='10:00:00', symbol='QZQ251219C00030000.US', quantity=3,
                             price=1.1, net_amount=333.0, currency='USD', account='margin',
                             open_close='C')
        us, = expired_open([buy], 2025, today=date(2026, 3, 1), country='usa')
        self.assertEqual((us['side'], us['quantity'], us['broker_closing']), ('long', 3.0, True))
        self.assertEqual(expired_open([buy], 2025, today=date(2026, 3, 1), country='canada'), [])


if __name__ == '__main__':
    unittest.main()

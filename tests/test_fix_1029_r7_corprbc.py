"""Fix round 7 (2026-10-29), group corprbc: corporate actions from RBC
Direct and Questrade exports (backlog TJ-0751, TJ-0755, TJ-0756,
TJ-0757, TJ-0759, TJ-0762, TJ-0763, TJ-0764, TJ-0866). Synthetic data
only: made-up tickers, account 99900001."""
import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import corp_actions as CA

RBC_HEAD = ['Date', 'Activity', 'Symbol', 'Symbol Description', 'Quantity',
            'Price', 'Settlement Date', 'Account', 'Value', 'Currency',
            'Description']


def rbc_row(day, act, sym, name, q, value, desc, price='', cur='CAD',
            settle=None):
    return [day, act, sym, name, q, price, settle or day, '99900001', value,
            cur, desc]


def write_rbc(folder: Path, name: str, rows) -> Path:
    s = io.StringIO()
    csv.writer(s).writerows([RBC_HEAD] + rows)
    p = folder / name
    p.write_text(s.getvalue())
    return p


def parse_all(files, quiet=True):
    """Every file parsed with the others as context, the copies combined
    (as taxjson_corp_actions does)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
        evs = CA.combine_broker_copies(
            [e for f in files
             for e in CA.parse_rbc_corporate_actions(f, 'margin', files)])
    return evs, err.getvalue()


BUY = rbc_row('2025-01-06', 'Buy', 'ALPH', 'ALPHA CORP', 105, -210,
              'ALPHA CORP UNSOLICITED', 2)
SPIN = rbc_row('2025-06-11', 'Reorganization', 'SPNA', 'SPINCO CORP', 10, 0,
               'DIS - SPINCO CORP SPINOFF ON 105 SHS FROM SEC# K000001 '
               'ALPHA CORP REC 06/01/25 PAY 06/11/25')
SPIN_CIL = rbc_row('2025-06-20', 'Reorganization', 'SPNA', 'SPINCO CORP', '',
                   10, 'CIL - SPINCO CORP CASH IN LIEU OF .5 SHS')
DIV = rbc_row('2025-07-02', 'Dividends', 'ALPH', 'ALPHA CORP', '', 1,
              'DIV - ALPHA CORP CASH DIV ON 105 SHS')


class TestSpinoffCilOverlap(unittest.TestCase):
    """TJ-0755: a spin-off's cash in lieu repeated by two overlapping
    exports of the account is one payment, not two."""

    def _cil(self, lists):
        with tempfile.TemporaryDirectory() as td:
            files = [write_rbc(Path(td), f'rbc{i}.csv', rows)
                     for i, rows in enumerate(lists)]
            evs, _ = parse_all(files)
        spins = [e for e in evs if e.action_type == 'spinoff']
        self.assertEqual(len(spins), 1, evs)
        return spins[0].cash_in_lieu, spins[0].fraction_in_lieu

    def test_overlap_counts_one_payment(self):
        self.assertEqual(self._cil([[BUY, SPIN], [SPIN_CIL],
                                    [SPIN_CIL, DIV]]), (10, 0.5))

    def test_controls(self):
        self.assertEqual(self._cil([[BUY, SPIN, SPIN_CIL]]), (10, 0.5))
        self.assertEqual(self._cil([[BUY, SPIN], [SPIN_CIL]]), (10, 0.5))

    def test_two_genuine_payments_in_one_file_kept(self):
        # Two identical rows in ONE export are two postings (multiplicity
        # kept); the overlapping copy of that file adds nothing.
        second = rbc_row('2025-06-20', 'Reorganization', 'SPNA',
                         'SPINCO CORP', '', 10,
                         'CIL - SPINCO CORP CASH IN LIEU OF .5 SHS')
        self.assertEqual(self._cil([[BUY, SPIN], [SPIN_CIL, second],
                                    [SPIN_CIL, second, DIV]]), (20, 1.0))


def parse_one(rows):
    with tempfile.TemporaryDirectory() as td:
        f = write_rbc(Path(td), 'rbc.csv', rows)
        return parse_all([f])


MBUY = rbc_row('2025-01-06', 'Buy', 'ALPH', 'ALPHA CORP', 12, -120,
               'ALPHA CORP UNSOLICITED', 10)
MREM = rbc_row('2025-06-11', 'Reorganization', 'A000001', 'ALPHA CORP', -12, 0,
               'MGR - ALPHA CORP MERGER TO BETA CORP 1.05 NEW = 1 OLD')
MREC = rbc_row('2025-06-11', 'Reorganization', 'BETA', 'BETA CORP', 12, 0,
               'MGR - BETA CORP SHRS RECEIVED THRU MERGER')
MCIL = rbc_row('2025-06-20', 'Reorganization', 'BETA', 'BETA CORP', '', 10,
               'CIL - BETA CORP CASH IN LIEU OF .6 SHS')


class TestMergerCilSeparateExport(unittest.TestCase):
    """TJ-0756: a merger's cash in lieu posted in another export of the
    account joins the merger even when both share legs pair in one file."""

    def _merger(self, lists):
        with tempfile.TemporaryDirectory() as td:
            files = [write_rbc(Path(td), f'rbc{i}.csv', rows)
                     for i, rows in enumerate(lists)]
            evs, _ = parse_all(files)
        ms = [e for e in evs if e.action_type == 'merger']
        self.assertEqual(len(ms), 1, evs)
        return ms[0]

    def test_cil_in_its_own_export(self):
        ev = self._merger([[MBUY, MREM, MREC], [MCIL]])
        self.assertEqual((ev.cash_in_lieu, ev.cash_in_lieu_currency),
                         (10, 'CAD'))

    def test_controls_and_overlap(self):
        for lists in ([[MBUY, MREM, MREC, MCIL]],
                      [[MBUY, MREM], [MREC, MCIL]],
                      [[MBUY, MREM, MREC], [MCIL], [MCIL, DIV]],
                      [[MBUY, MREM, MREC, MCIL], [MREM, MREC, MCIL]]):
            with self.subTest(lists=len(lists)):
                self.assertEqual(self._merger(lists).cash_in_lieu, 10)


class TestMergerCilDate(unittest.TestCase):
    """TJ-0866: a merger's cash in lieu keeps its payment date, and the
    s.85.1 rollover sells the fraction (the cash received) on that day."""

    def test_rollover_sells_fraction_on_payment_date(self):
        evs, _ = parse_one([MBUY, MREM, MREC, MCIL])
        ev = [e for e in evs if e.action_type == 'merger'][0]
        self.assertEqual(ev.cash_in_lieu_date, '2025-06-20')
        rows = CA.resolve_event(ev, 'rollover_s_85_1_5', 'canada', {})
        split = [r for r in rows if r['action'] == 'SPLIT']
        sale = [r for r in rows if r.get('corp_cash')]
        self.assertEqual(split[0]['date'], '2025-06-11')
        self.assertEqual(len(sale), 1, rows)
        self.assertEqual((sale[0]['date'], sale[0]['date_settle']),
                         ('2025-06-20', '2025-06-20'))

    def test_cash_ledger_reconciles(self):
        # USD 30 paid on March 20 for the fraction of a March 17 merger:
        # no USD on March 18, USD 30 at the year end.
        from taxjson.lib import cash_events as CE
        from taxjson.lib import fx_cash_v2 as V2

        def usd(day, *a, **k):
            return rbc_row(day, *a, cur='USD', **k)
        evs, _ = parse_one([
            usd('2025-03-17', 'Reorganization', 'ABC', 'ABC CORPORATION',
                -12, 0, 'MGR - ABC CORPORATION MERGER TO ABDCO CORPORATION '
                '1.05 NEW = 1 OLD'),
            usd('2025-03-17', 'Reorganization', 'ABD', 'ABDCO CORPORATION',
                12, 0, 'MGR - ABDCO CORPORATION SHRS RECEIVED THRU MERGER'),
            usd('2025-03-20', 'Reorganization', 'ABD', 'ABDCO CORPORATION',
                '', 30, 'CIL - ABDCO CORPORATION CASH IN LIEU OF FRAC '
                'SHARES')])
        ev = [e for e in evs if e.action_type == 'merger'][0]
        rows = CA.resolve_event(ev, 'rollover_s_85_1_5', 'canada', {})
        for r in rows:
            r.update(_book='margin/rbc', source='rbc.csv',
                     corp_event_id=ev.event_id)
        lines = []
        for s in ('CASHOPEN 2025-01-01 USD 0 0', 'CASHBAL 2025-03-18 USD 0',
                  'CASHBAL 2025-12-31 USD 30'):
            x = CE.parse_line(s, 'cash.tt:1')
            x.update(label='margin', book='margin/rbc', file='cash.tt',
                     book_problem='')
            lines.append(x)
        v = V2.build(rows, {'events': [], 'lines': lines, 'problems': [],
                            'stablecoins_as_cash': True},
                     'CAD', 2025, lambda c, d: 1.5, country='canada')
        self.assertNotEqual(v['status'], 'not_computed', v['problems'])
        self.assertEqual(v['problems'], [])

    def test_sanity_names_the_fraction_awaiting_its_cash(self):
        # A holdings snapshot between the exchange and the payment shows
        # the whole shares only: the fraction is no difference.
        from taxjson.lib.positions_check import fractions_awaiting_cash
        evs, _ = parse_one([MBUY, MREM, MREC, MCIL])
        ev = [e for e in evs if e.action_type == 'merger'][0]
        rows = CA.resolve_event(ev, 'rollover_s_85_1_5', 'canada', {})
        for r in rows:
            r.update(corp_event_id=ev.event_id,
                     corp_election='rollover_s_85_1_5')
        got = fractions_awaiting_cash(rows, '2025-06-15')
        self.assertEqual(list(got), ['BETA.TO'])
        f = got['BETA.TO'][0]
        self.assertEqual((round(f['quantity'], 6), f['received'], f['paid'],
                          f['event']),
                         (0.6, '2025-06-11', '2025-06-20', 'merger'))
        for day in ('2025-06-10', '2025-06-20'):
            self.assertEqual(fractions_awaiting_cash(rows, day), {}, day)

    def test_same_day_cash_unchanged(self):
        same = rbc_row('2025-06-11', 'Reorganization', 'BETA', 'BETA CORP',
                       '', 10, 'CIL - BETA CORP CASH IN LIEU OF .6 SHS')
        evs, _ = parse_one([MBUY, MREM, MREC, same])
        ev = [e for e in evs if e.action_type == 'merger'][0]
        rows = CA.resolve_event(ev, 'rollover_s_85_1_5', 'canada', {})
        sale = [r for r in rows if r.get('corp_cash')][0]
        self.assertEqual(sale['date'], '2025-06-11')
        self.assertGreater(sale['time'], rows[0]['time'])


def _class_rows(order):
    rem = [rbc_row('2025-06-11', 'Reorganization', 'ALPH.' + c,
                   'ALPHA CORP CLASS ' + c, -100, 0,
                   f'MGR - ALPHA CORP CLASS {c} MERGER TO BETA CORP CLASS '
                   f'{c} 1 NEW = 1 OLD') for c in 'AB']
    rec = [rbc_row('2025-06-11', 'Reorganization', 'BETA.' + c,
                   'BETA CORP CLASS ' + c, 100, 0,
                   f'MGR - BETA CORP CLASS {c} SHRS RECEIVED THRU MERGER')
           for c in order]
    return rem + rec


class TestShareClassPairing(unittest.TestCase):
    """TJ-0757: a reorganization's removal pairs with the receipt of the
    share class its description names, whatever the receipt row order."""

    def test_classes_kept_either_order(self):
        for order in ('AB', 'BA'):
            with self.subTest(order=order):
                evs, _ = parse_one(_class_rows(order))
                got = sorted((e.source_symbol, e.target_symbol) for e in evs)
                self.assertEqual(got, [('ALPH.A.TO', 'BETA.A.TO'),
                                       ('ALPH.B.TO', 'BETA.B.TO')])

    def test_share_class_reader(self):
        from taxjson.lib.corp_actions import _rbc_share_class
        self.assertEqual(_rbc_share_class('BETA CORP CLASS A'), 'A')
        self.assertEqual(_rbc_share_class('BETA CORP CL B SUB VTG'), 'B')
        self.assertEqual(_rbc_share_class('BETA CORP CL-B'), 'B')
        self.assertEqual(_rbc_share_class('BETA CORP'), '')
        self.assertEqual(_rbc_share_class('CLASSIC CORP'), '')


class TestSpinoffCilReversalFraction(unittest.TestCase):
    """TJ-0759: a reversed and re-paid cash in lieu leaves one fraction."""

    def test_correction_sequence(self):
        rev = rbc_row('2025-06-23', 'Reorganization', 'SPNA', 'SPINCO CORP',
                      '', -10, 'CIL - SPINCO CORP CASH IN LIEU OF .5 SHS '
                      'REVERSE ENTRY')
        fixed = rbc_row('2025-06-23', 'Reorganization', 'SPNA',
                        'SPINCO CORP', '', 12,
                        'CIL - SPINCO CORP CASH IN LIEU OF .5 SHS')
        evs, _ = parse_one([BUY, SPIN, SPIN_CIL, rev, fixed])
        ev = [e for e in evs if e.action_type == 'spinoff'][0]
        self.assertEqual((ev.cash_in_lieu, ev.fraction_in_lieu), (12, 0.5))

    def test_fraction_helper(self):
        from types import SimpleNamespace as N
        from taxjson.lib.corp_actions import rbc_spinoff_cil_fraction as f
        pay = N(value=10.0, qty=0.0, desc='CASH IN LIEU OF .5 SHS')
        rev_unstated = N(value=-10.0, qty=0.0, desc='CIL REVERSE ENTRY')
        pay2 = N(value=12.0, qty=0.0, desc='CASH IN LIEU OF .5 SHS')
        self.assertEqual(f([pay]), 0.5)
        self.assertEqual(f([pay, rev_unstated, pay2]), 0.5)
        self.assertEqual(f([pay, rev_unstated]), 0.0)
        # A reversal that matches no payment and states no fraction:
        # the fraction cannot be sized.
        odd = N(value=-3.0, qty=0.0, desc='CIL REVERSE ENTRY')
        self.assertEqual(f([pay, pay2, odd]), 0.0)


def _spin_x(cur, q, rev=False):
    code, name = (('K000001', 'CANADIAN PARENT CORP') if cur == 'CAD'
                  else ('K000002', 'FOREIGN PARENT CORP'))
    child = 'CANADIAN SPINCO CORP' if cur == 'CAD' else 'FOREIGN SPINCO CORP'
    day = '2025-06-12' if rev else '2025-06-11'
    return rbc_row(day, 'Reorganization', 'SPNA', child, q, 0,
                   f'DIS - {child} SPINOFF ON 100 SHS FROM SEC# {code} {name} '
                   f'REC 06/01/25 PAY 06/11/25' + (' REVERSE ENTRY' if rev
                                                    else ''), cur=cur)


class TestSpinoffReversalIdentity(unittest.TestCase):
    """TJ-0762: a spin-off reversal cancels the posting of its own
    listing currency and parent, never a same-ticker one of another."""

    def test_usd_reversal_keeps_cad_spinoff(self):
        buys = [rbc_row('2025-01-06', 'Buy', 'ALPH', 'CANADIAN PARENT CORP',
                        100, -100, 'CANADIAN PARENT CORP UNSOLICITED', 1),
                rbc_row('2025-01-06', 'Buy', 'OMEG', 'FOREIGN PARENT CORP',
                        100, -100, 'FOREIGN PARENT CORP UNSOLICITED', 1,
                        cur='USD')]
        for rs in ([_spin_x('CAD', 10), _spin_x('USD', 10),
                    _spin_x('USD', -10, True)],
                   [_spin_x('USD', 10), _spin_x('CAD', 10),
                    _spin_x('USD', -10, True)]):
            with self.subTest(first=rs[0][9]):
                evs, _ = parse_one(buys + rs)
                got = [(e.source_symbol, e.target_symbol, e.currency)
                       for e in evs]
                self.assertEqual(got, [('ALPH.TO', 'SPNA.TO', 'CAD')])


def _mpair(day, reverse=False):
    sign = -1 if reverse else 1
    tail = ' REVERSE ENTRY' if reverse else ''
    return [rbc_row(day, 'Reorganization', 'ALPH', 'ALPHA CORP', -100 * sign,
                    0, 'MGR - ALPHA CORP MERGER TO BETA CORP 0.5 NEW = 1 OLD'
                    + tail),
            rbc_row(day, 'Reorganization', 'BETA', 'BETA CORP', 50 * sign, 0,
                    'MGR - BETA CORP SHRS RECEIVED THRU MERGER' + tail)]


class TestReorgReversalChronology(unittest.TestCase):
    """TJ-0763: a REVERSE ENTRY cancels an earlier (or same-day) posting,
    never the corrected rebooking that follows it."""

    def test_rebook_survives(self):
        evs, _ = parse_one(_mpair('2025-12-31') + _mpair('2026-01-05', True)
                           + _mpair('2026-01-06'))
        self.assertEqual([(e.date, e.action_type, e.source_symbol,
                           e.target_symbol) for e in evs],
                         [('2026-01-06', 'merger', 'ALPH.TO', 'BETA.TO')])

    def test_rows_newest_first(self):
        rows = (_mpair('2025-12-31') + _mpair('2026-01-05', True)
                + _mpair('2026-01-06'))[::-1]
        evs, _ = parse_one(rows)
        self.assertEqual([e.date for e in evs], ['2026-01-06'])


class TestSpinoffParentAfterSplit(unittest.TestCase):
    """TJ-0764: the parent listing still held is found after a stock
    split (the split's added shares count in the position)."""

    def _rows(self, start):
        def row(d, a, s, q, p, v, c, desc, name='SYN PARENT CORP'):
            return rbc_row(d, a, s, name, q, v, desc, p, cur=c)
        prior_usd = [row('2025-01-06', 'Buy', 'SYN', 1, 5, -5, 'USD',
                         'SYN PARENT CORP BUY'),
                     row('2025-01-07', 'Sell', 'SYN', -1, 5, 5, 'USD',
                         'SYN PARENT CORP SELL')]
        after = [row('2025-04-01', 'Sell', 'SYN', -15, 5, 75, 'CAD',
                     'SYN PARENT CORP SELL'),
                 row('2025-06-11', 'Reorganization', 'SPNA', 1, '', 0, 'CAD',
                     'DIS - SPINCO CORP SPINOFF ON 5 SHS FROM SEC# K000001 '
                     'SYN PARENT CORP REC 06/02/25 PAY 06/11/25',
                     'SPINCO CORP')]
        return prior_usd + start + after

    def test_parent_found_after_split(self):
        split = [rbc_row('2025-02-03', 'Buy', 'SYN', 'SYN PARENT CORP', 10,
                         -100, 'SYN PARENT CORP BUY', 10),
                 rbc_row('2025-03-03', 'Reorganization', 'SYN',
                         'SYN PARENT CORP', 10, 0,
                         'DIS - SYN PARENT CORP STK SPLIT ON 10 SHS')]
        control = [rbc_row('2025-02-03', 'Buy', 'SYN', 'SYN PARENT CORP', 20,
                           -100, 'SYN PARENT CORP BUY', 5)]
        for start in (split, control):
            with self.subTest(n=len(start)):
                evs, err = parse_one(self._rows(start))
                self.assertEqual([e.source_symbol for e in evs], ['SYN.TO'],
                                 err)


QT_HEAD = ['Transaction Date', 'Settlement Date', 'Action', 'Symbol',
           'Description', 'Quantity', 'Price', 'Gross Amount', 'Commission',
           'Net Amount', 'Currency', 'Account #', 'Activity Type',
           'Account Type']


def _qt_spin(parent, child, name, code, n, desc_child=None):
    return [['2025-03-03 12:00:00 AM', '2025-03-04 12:00:00 AM', 'Buy',
             parent, name + ' WE ACTED AS AGENT', 100, 1, -100, 0, -100,
             'CAD', '99900001', 'Trades', 'Cash'],
            ['2025-08-25 12:00:00 AM', '2025-08-25 12:00:00 AM', 'DIS', child,
             f'{desc_child or child} CORP SPINOFF ON 100 SHS FROM SEC# {code} '
             f'{name} REC 08/21/25 PAY 08/25/25', n, 0, 0, 0, 0, 'CAD',
             '99900001', 'Dividends', 'Cash']]


class TestQuestradeSpinoffChainIdentity(unittest.TestCase):
    """TJ-0751: two parents' spin-offs on the same record and pay dates,
    on the same parent share count, are two events."""

    def _parse(self, rows):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'qt.csv'
            s = io.StringIO()
            csv.writer(s).writerows([QT_HEAD] + rows)
            f.write_text(s.getvalue())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                evs = CA.parse_questrade_corporate_actions(f, 'margin')
        return sorted((e.source_symbol, e.target_symbol, e.qty_received)
                      for e in evs)

    def test_two_parents_two_events(self):
        a = _qt_spin('ALPH', 'SPNA', 'ALPHA CORP', 'K000001', 10)
        b = _qt_spin('BETA', 'SPNB', 'BETA CORP', 'K000002', 20)
        want = [('ALPH.TO', 'SPNA.TO', 10), ('BETA.TO', 'SPNB.TO', 20)]
        self.assertEqual(self._parse(a + b), want)
        self.assertEqual(self._parse(b + a), want)

    def test_chain_of_one_parent_still_nets(self):
        # Placeholder (no symbol), its reversal and the repost: one event.
        a = _qt_spin('ALPH', 'SPNA', 'ALPHA CORP', 'K000001', 10)
        hold = _qt_spin('ALPH', '', 'ALPHA CORP', 'K000001', 10,
                        desc_child='PENDING')[1]
        back = _qt_spin('ALPH', 'SPNA', 'ALPHA CORP', 'K000001', -10)[1]
        self.assertEqual(self._parse(a + [hold, back]),
                         [('ALPH.TO', 'SPNA.TO', 10)])


if __name__ == '__main__':
    unittest.main()

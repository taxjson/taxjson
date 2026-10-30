"""Fix round 7 (wave D), RBC Direct and Questrade (backlog TJ-1027,
TJ-1048, TJ-1063, TJ-1064, TJ-1089, TJ-1106, TJ-1107, TJ-1122).
Synthetic data only: made-up tickers, accounts 99900001 and 99900002."""
import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from _qa_project import console, gains, project, tj, toml
from test_fix_rbcqt import QH, RH, of, q, qt_parse, rbc_parse, rrow

from taxjson.bin import taxjson_fx_cash as F
from taxjson.lib import cash_events as CE
from taxjson.lib import corp_actions as CA
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage

# The preamble shape: the account number above the table, no Account
# column (examples/rbc_direct_demo.csv).
BLANK_HEAD = ['Date', 'Activity', 'Symbol', 'Symbol Description', 'Quantity',
              'Price', 'Settlement Date', 'Currency', 'Value', 'Description']
NAMED_HEAD = ['Date', 'Activity', 'Symbol', 'Symbol Description', 'Quantity',
              'Price', 'Settlement Date', 'Account', 'Value', 'Currency',
              'Description']


def blank_row(day, act, sym, name, n, value, desc, price=''):
    return [day, act, sym, name, n, price, day, 'CAD', value, desc]


def write_blank(folder, name, account, rows):
    s = io.StringIO()
    s.write(f'Account Number: {account}\n')
    csv.writer(s).writerows([BLANK_HEAD] + rows)
    p = Path(folder) / name
    p.write_text(s.getvalue())
    return p


def parse_all(files):
    """Every file parsed with the others as context, the copies combined
    (as taxjson_corp_actions does)."""
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return CA.combine_broker_copies(
            [e for f in files
             for e in CA.parse_rbc_corporate_actions(f, 'margin', files)])


def m_buy(n):
    return blank_row('2025-01-06', 'Buy', 'ALPH', 'ALPHA CORP', n, -10 * n,
                     'ALPHA CORP UNSOLICITED', 10)


def m_legs(n):
    return [
        blank_row('2025-06-11', 'Reorganization', 'A000001', 'ALPHA CORP', -n,
                  0, 'MGR - ALPHA CORP MERGER TO BETA CORP 1.05 NEW = 1 OLD'),
        blank_row('2025-06-11', 'Reorganization', 'BETA', 'BETA CORP', n, 0,
                  'MGR - BETA CORP SHRS RECEIVED THRU MERGER')]


def m_cil(value):
    return blank_row('2025-06-20', 'Reorganization', 'BETA', 'BETA CORP', '',
                     value, 'CIL - BETA CORP CASH IN LIEU OF FRAC SHARES')


class MergerCilBlankAccounts(unittest.TestCase):
    """TJ-1027: two broker accounts' exports with no Account column: each
    merger keeps its own account's cash in lieu. TJ-0756's own case (the
    payment in a later export than both legs) still works."""

    def _cash(self, *files_rows):
        with tempfile.TemporaryDirectory() as td:
            files = [write_blank(td, f'rbc_{i}.csv', 9990000 + i, rows)
                     for i, rows in enumerate(files_rows)]
            evs = parse_all(files)
        return [e for e in evs if e.action_type == 'merger']

    def test_two_accounts_each_keep_their_own_payment(self):
        a = [m_buy(12)] + m_legs(12) + [m_cil(10)]
        b = [m_buy(24)] + m_legs(24) + [m_cil(20)]
        for rows in ((a, b), (b, a)):
            evs = self._cash(*rows)
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0].qty_disposed, 36)
            self.assertAlmostEqual(evs[0].cash_in_lieu, 30.0)

    def test_equal_payments_in_two_accounts_are_two_payments(self):
        evs = self._cash([m_buy(12)] + m_legs(12) + [m_cil(10)],
                         [m_buy(24)] + m_legs(24) + [m_cil(10)])
        self.assertAlmostEqual(evs[0].cash_in_lieu, 20.0)

    def test_payment_in_a_later_export_of_one_account(self):
        # TJ-0756 with no Account column.
        evs = self._cash([m_buy(12)] + m_legs(12), [m_cil(10)])
        self.assertAlmostEqual(evs[0].cash_in_lieu, 10.0)

    def test_a_payment_in_no_events_export_counts_once(self):
        # Neither account's merger is in the payment's file: which one it
        # belongs to cannot be read, but every file's view gives it to
        # the same one, so the account's total stays the cash paid.
        evs = self._cash([m_buy(12)] + m_legs(12),
                         [m_buy(24)] + m_legs(24), [m_cil(10)])
        self.assertAlmostEqual(evs[0].cash_in_lieu, 10.0)

    def test_redownload_beside_a_second_account(self):
        # An older download without the payment, its re-download with
        # it, and a second account with no payment of its own.
        old = [m_buy(12)] + m_legs(12)
        evs = self._cash(old, old + [m_cil(10)], [m_buy(24)] + m_legs(24))
        self.assertEqual(sum(e.qty_disposed for e in evs), 36)
        self.assertAlmostEqual(sum(e.cash_in_lieu for e in evs), 10.0)


def s_buy(n):
    return blank_row('2025-01-06', 'Buy', 'ALPH', 'ALPHA CORP', n, -2 * n,
                     'ALPHA CORP UNSOLICITED', 2)


def s_spin(n, on):
    return blank_row('2025-06-11', 'Reorganization', 'SPNA', 'SPINCO CORP', n,
                     0, f'DIS - SPINCO CORP SPINOFF ON {on} SHS FROM SEC# '
                     'K000001 ALPHA CORP REC 06/01/25 PAY 06/11/25')


def s_cil(value, frac):
    return blank_row('2025-06-20', 'Reorganization', 'SPNA', 'SPINCO CORP',
                     '', value, f'CIL - SPINCO CORP CASH IN LIEU OF {frac} SHS')


class SpinoffCilBlankAccounts(unittest.TestCase):
    """TJ-1064: the same, for a spin-off's cash in lieu."""

    def test_each_spinoff_keeps_its_own_payment(self):
        with tempfile.TemporaryDirectory() as td:
            a = write_blank(td, 'rbc_a.csv', 99900001,
                            [s_buy(105), s_spin(10, 105), s_cil(10, '.5')])
            b = write_blank(td, 'rbc_b.csv', 99900002,
                            [s_buy(210), s_spin(21, 210), s_cil(20, '.4')])
            evs = parse_all([a, b])
        got = sorted((e.qty_received, e.cash_in_lieu, e.fraction_in_lieu)
                     for e in evs if e.action_type == 'spinoff')
        self.assertEqual(got, [(10.0, 10.0, 0.5), (21.0, 20.0, 0.4)])


# ------------------------------------------------------ the cash's own date

def usd_row(day, act, sym, name, n, value, desc):
    return [day, act, sym, name, n, '', day, '99900001', value, 'USD', desc]


def merger_event(cil_day='2025-03-20'):
    with tempfile.TemporaryDirectory() as td:
        s = io.StringIO()
        csv.writer(s).writerows([NAMED_HEAD, usd_row(
            '2025-03-17', 'Reorganization', 'ABC', 'ABC CORPORATION', -12, 0,
            'MGR - ABC CORPORATION MERGER TO ABDCO CORPORATION 1.05 NEW = 1 '
            'OLD'), usd_row(
            '2025-03-17', 'Reorganization', 'ABD', 'ABDCO CORPORATION', 12, 0,
            'MGR - ABDCO CORPORATION SHRS RECEIVED THRU MERGER'), usd_row(
            cil_day, 'Reorganization', 'ABD', 'ABDCO CORPORATION', '', 30,
            'CIL - ABDCO CORPORATION CASH IN LIEU OF FRAC SHARES')])
        p = Path(td) / 'rbc.csv'
        p.write_text(s.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            evs = CA.parse_rbc_corporate_actions(p, 'margin', [p])
    return [e for e in evs if e.action_type == 'merger'][0]


def ledger(rows, event_id, *cash_lines, country='canada', base='CAD'):
    for r in rows:
        r.update(_book='margin/rbc', source='rbc.csv', corp_event_id=event_id)
    lines = []
    for s in cash_lines:
        x = CE.parse_line(s, 'cash.tt:1')
        x.update(label='margin', book='margin/rbc', file='cash.tt',
                 book_problem='')
        lines.append(x)
    return V2.build(rows, {'events': [], 'lines': lines, 'problems': [],
                           'stablecoins_as_cash': True},
                    base, 2025, lambda c, d: 1.5, country=country)


LINES = ('CASHOPEN 2025-01-01 USD 0 0', 'CASHBAL 2025-03-18 USD 0',
         'CASHBAL 2025-12-31 USD 30')


class TaxableMergerCashDate(unittest.TestCase):
    """TJ-1063 / TJ-1106: under an election that folds the cash in lieu
    into the disposition, the disposition keeps the exchange date and the
    cash ledger counts the cash the day the broker paid it."""

    def _rows(self, election='taxable_disposition', country='canada',
              **hints):
        ev = merger_event()
        hints = hints or {'fmv_per_share': 10.0}
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            return ev, CA.resolve_event(ev, election, country, hints)

    def test_canada_taxable_disposition(self):
        ev, rows = self._rows()
        sell = [r for r in rows if r['quantity'] < 0][0]
        # The disposition stays on the exchange, with the cash in its
        # proceeds; only when the cash arrives moves.
        self.assertEqual((sell['date'], sell['date_settle']),
                         ('2025-03-17', '2025-03-17'))
        self.assertAlmostEqual(sell['net_amount'], 12 * 10.0 + 30)
        self.assertEqual(F._cash_legs(sell), [('USD', 30.0)])
        v = ledger(rows, ev.event_id, *LINES)
        self.assertNotEqual(v['status'], 'not_computed', v['problems'])
        self.assertEqual(v['problems'], [])

    def test_us_taxable_exchange(self):
        ev, rows = self._rows('taxable_exchange', 'usa')
        v = ledger(rows, ev.event_id, *LINES, country='usa', base='USD')
        self.assertEqual(v['problems'], [])
        ev, rows = self._rows('taxable_exchange', 'usa')
        sell = [r for r in rows if r['quantity'] < 0][0]
        self.assertEqual(sell['date'], '2025-03-17')
        self.assertTrue(sell['corp_cash'].endswith('@2025-03-20'))

    def test_us_boot_keeps_the_boot_on_the_exchange(self):
        _ev, rows = self._rows('reorg_368_boot', 'usa', fmv_per_share=10.0,
                               cash_boot=50.0)
        sell = [r for r in rows if r['quantity'] < 0][0]
        self.assertEqual(sell['corp_cash'], '50 USD; 30 USD @2025-03-20')
        self.assertEqual(F._cash_legs(sell), [('USD', 50.0), ('USD', 30.0)])
        self.assertEqual(F._cash_leg_dates(sell),
                         [('USD', 50.0, ''), ('USD', 30.0, '2025-03-20')])

    def test_paid_on_the_exchange_stays_as_it_was(self):
        ev = merger_event('2025-03-17')
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            rows = CA.resolve_event(ev, 'taxable_disposition', 'canada',
                                    {'fmv_per_share': 10.0})
        self.assertEqual(rows[0]['corp_cash'], '30 USD')

    def test_the_older_ledger_walks_it_on_the_payment_date(self):
        ev, rows = self._rows()
        for r in rows:
            r['corp_event_id'] = ev.event_id
        rate = {'2025-03-17': 1.0, '2025-03-20': 2.0}
        out = F.build_ledger(rows, 'CAD', {}, 2025,
                             rate_of=lambda _c, d: rate[d])
        self.assertEqual(out['pools']['USD'], {'units': 30.0, 'acb': 60.0})


# ------------------------------------------- Questrade: one parent, two kids

def qt_dis(child, n, name=None, sym=None):
    name = name or f'{child} CORP'
    return q(td='2025-08-25', action='DIS', sym=child if sym is None else sym,
             desc=f'{name} SPINOFF ON 100 SHS FROM SEC# K000001 ALPHA CORP '
                  'REC 08/21/25 PAY 08/25/25',
             qty=str(n), price='0', gross='0', comm='0', net='0', cur='CAD',
             act='Dividends', atype='Cash')


QT_BUY = q(td='2025-03-03', action='Buy', sym='ALPH',
           desc='ALPHA CORP WE ACTED AS AGENT', qty='100', price='1',
           gross='-100', comm='0', net='-100', cur='CAD', atype='Cash')


class QtTwoDistributionsOneParent(unittest.TestCase):
    """TJ-1107: shares and warrants of one parent, same dates and count."""

    def _parse(self, *rows):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'qt.csv'
            f.write_text(QH + QT_BUY + ''.join(rows), encoding='utf-8')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                evs = CA.parse_questrade_corporate_actions(f, 'margin')
        return (sorted((e.target_symbol, e.qty_received) for e in evs),
                err.getvalue())

    def test_two_securities_two_events(self):
        got, _ = self._parse(qt_dis('SPNA', 10), qt_dis('SPNW', 5, 'SPNW WT'))
        self.assertEqual(got, [('SPNA.TO', 10), ('SPNW.TO', 5)])

    def test_each_events_own_rows(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'qt.csv'
            f.write_text(QH + QT_BUY + qt_dis('SPNA', 10)
                         + qt_dis('SPNW', 5, 'SPNW WT'), encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                evs = CA.parse_questrade_corporate_actions(f, 'margin')
        self.assertEqual(len({e.event_id for e in evs}), 2)
        self.assertEqual(sorted(e.source_symbol for e in evs),
                         ['ALPH.TO', 'ALPH.TO'])
        self.assertEqual(sorted(len(e.raw_descriptions) for e in evs), [1, 1])

    def test_placeholder_goes_with_its_name(self):
        got, _ = self._parse(
            qt_dis('SPNA', 10), qt_dis('SPNA', -10),
            qt_dis('', 10, 'SPNA CORP'), qt_dis('SPNW', 5, 'SPNW WT'))
        self.assertEqual(got, [('SPNA.TO', 10), ('SPNW.TO', 5)])

    def test_placeholder_fills_the_reversed_security(self):
        # The placeholder's wording names neither, but only one security
        # was reversed by its quantity.
        got, _ = self._parse(
            qt_dis('SPNA', 10), qt_dis('SPNA', -10),
            qt_dis('', 10, 'PENDING'), qt_dis('SPNW', 5, 'SPNW WT'))
        self.assertEqual(got, [('SPNA.TO', 10), ('SPNW.TO', 5)])

    def test_a_placeholder_nothing_explains_is_refused(self):
        got, err = self._parse(
            qt_dis('SPNA', 10), qt_dis('', 7, 'PENDING'),
            qt_dis('SPNW', 5, 'SPNW WT'))
        self.assertEqual(got, [])
        self.assertIn('UNBOOKED', err)
        self.assertIn('SPNA', err)
        self.assertIn('SPNW', err)

    def test_one_security_chain_still_nets(self):
        got, _ = self._parse(qt_dis('SPNA', 10), qt_dis('SPNA', -10),
                             qt_dis('', 10, 'PENDING'))
        self.assertEqual(got, [('SPNA.TO', 10)])

    def test_a_cancelled_code_and_its_repost_stay_one_event(self):
        # A posting under one symbol, reversed and reposted under
        # another: one distribution, as before.
        got, _ = self._parse(qt_dis('SPNA', 10, 'SPN CORP'),
                             qt_dis('SPNA', -10, 'SPN CORP'),
                             qt_dis('SPNB', 10, 'SPN CORP'))
        self.assertEqual(sum(n for _s, n in got), 10)
        self.assertEqual(len(got), 1)

    def test_each_takes_its_own_cash_in_lieu(self):
        cil = q(td='2025-09-02', action='CIL', sym='SPNW',
                desc='SPNW WT CASH IN LIEU OF .5', qty='0', price='0',
                gross='0', comm='0', net='3', cur='CAD', act='Dividends',
                atype='Cash')
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'qt.csv'
            f.write_text(QH + QT_BUY + qt_dis('SPNA', 10)
                         + qt_dis('SPNW', 5, 'SPNW WT') + cil,
                         encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                evs = CA.parse_questrade_corporate_actions(f, 'margin')
        self.assertEqual(sorted((e.target_symbol, e.cash_in_lieu)
                                for e in evs),
                         [('SPNA.TO', 0.0), ('SPNW.TO', 3.0)])


# --------------------------------------- Questrade: overlapping downloads

def qt_line(day, sym, n):
    return q(td=day, action='Buy' if n > 0 else 'Sell', sym=sym,
             desc='SYN CORP WE ACTED AS AGENT', qty=str(n), price='10',
             gross=str(-10 * n), comm='0', net=str(-10 * n), cur='CAD')


class QtRenameDetectionOverlap(unittest.TestCase):
    """TJ-1122: a row in two overlapping downloads is one broker row."""

    SELL = qt_line('2025-03-03', 'NEW.TO', -50)
    BODY = (qt_line('2025-01-02', 'OLD.TO', 100)
            + qt_line('2025-02-03', 'NEW.TO', 50) + SELL)

    def test_a_repeated_sale_is_no_rename(self):
        _txs, err, _ = qt_parse(self.BODY)
        self.assertNotIn('looks renamed', err)
        _txs, err, _ = qt_parse(self.BODY, self.SELL)
        self.assertNotIn('looks renamed', err)

    def test_a_real_rename_still_says_so(self):
        body = (qt_line('2025-01-02', 'OLD.TO', 100)
                + qt_line('2025-03-03', 'NEW.TO', -50))
        _txs, err, _ = qt_parse(body, qt_line('2025-03-03', 'NEW.TO', -50))
        self.assertIn('looks renamed', err)

    def test_two_identical_sales_in_one_file_stay_two(self):
        sell = qt_line('2025-03-03', 'NEW.TO', -50)
        body = (qt_line('2025-01-02', 'OLD.TO', 100)
                + qt_line('2025-02-03', 'NEW.TO', 50) + sell + sell)
        _txs, err, _ = qt_parse(body)
        self.assertIn('looks renamed', err)


# ------------------------------------------------- RBC: the split's position

def ratios(txs):
    return [round(t['quantity'], 9) for t in of(txs, action='SPLIT')]


def ending(txs):
    pos = 0.0
    for t in sorted(txs, key=lambda x: x['date']):
        if t['action'] == 'SPLIT':
            pos *= t['quantity']
        elif t['action'] == 'BUYSELL':
            pos += t['quantity']
    return pos


def named_row(day, act, sym, n, value, desc, acct='99900001'):
    cells = [day, act, sym, 'SYN CORP', n, '', day, acct, value, 'CAD', desc]
    return ','.join(f'"{c}"' for c in cells) + '\n'


NAMED_RH = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency",'
            '"Description"\n')
DEMO_RH = ('Date,Activity,Symbol,Description,Quantity,Price,Settlement Date,'
           'Currency,Value,Amount\n')


def demo_trade(day, settle, act, n):
    return (f'{day},{act},SYN,SYN CORP - {act},{n},10.00,{settle},CAD,'
            f'{abs(n) * 10:.2f},{abs(n) * 10:.2f}\n')


class RbcSplitMixedLayouts(unittest.TestCase):
    """TJ-1048: rows from an export with no Account column are the
    account's own rows; TJ-0672's per-account position stays."""

    def _parse(self, *bodies):
        return rbc_parse(*bodies, raw=True)

    SPLIT = named_row('February 3, 2025', 'Reorganization', 'SYN', -100, 0,
                      'DIS - SYN CORP STK SPLIT ON 100 SHS')
    COVER = named_row('March 3, 2025', 'Buy', 'SYN', 200, -1000,
                      'SYN CORP UNSOLICITED DA')

    def test_short_from_the_other_layout(self):
        older = DEMO_RH + demo_trade('11/04/2024', '11/06/2024', 'Sell', -100)
        newer = NAMED_RH + self.SPLIT + self.COVER
        txs, err, _ = self._parse(older, newer)
        self.assertEqual(ratios(txs), [2.0])
        self.assertAlmostEqual(ending(txs), 0.0)
        self.assertNotIn('UNBOOKED', err)

    def test_the_split_in_the_file_with_no_account_column(self):
        # The reverse mix: the short named, the split not.
        older = NAMED_RH + named_row('November 4, 2024', 'Sell', 'SYN', -100,
                                     1000, 'SYN CORP UNSOLICITED DA')
        newer = (BLANK_RH + blank_split('February 3, 2025', 100, -100))
        txs, _err, _ = self._parse(older, newer)
        self.assertEqual(ratios(txs), [2.0])

    def test_long_from_the_other_layout_is_still_a_reverse_split(self):
        older = DEMO_RH + demo_trade('11/04/2024', '11/06/2024', 'Buy', 100)
        newer = NAMED_RH + named_row(
            'February 3, 2025', 'Reorganization', 'SYN', -90, 0,
            'DIS - SYN CORP STK SPLIT ON 100 SHS')
        txs, _err, _ = self._parse(older, newer)
        self.assertEqual(ratios(txs), [0.1])


BLANK_RH = ('Date,Activity,Symbol,Symbol Description,Quantity,Price,'
            'Settlement Date,Currency,Value,Description\n')


def blank_split(day, on, delta):
    return (f'"{day}",Reorganization,SYN,SYN CORP,{delta},,"{day}",CAD,0,'
            f'DIS - SYN CORP STK SPLIT ON {on} SHS\n')


class RbcSplitUnderTempCode(unittest.TestCase):
    """TJ-1089: a split booked under a temporary code moves the position
    of the ticker it resolves to."""

    @staticmethod
    def _row(day, act, sym, n, desc):
        return rrow(day, act, sym, 'SYN CORP', str(n), '', '0', 'CAD', desc)

    def _body(self, first_split_symbol):
        return (rrow('January 2, 2025', 'Sell', 'SYN', 'SYN CORP', '-100',
                     '10', '1000', 'CAD', 'SYN CORP UNSOLICITED DA')
                + self._row('February 3, 2025', 'Reorganization',
                            first_split_symbol, -100,
                            'DIS - SYN CORP STK SPLIT ON 100 SHS')
                + rrow('March 3, 2025', 'Buy', 'SYN', 'SYN CORP', '150', '5',
                       '-750', 'CAD', 'SYN CORP UNSOLICITED DA')
                + self._row('April 3, 2025', 'Reorganization', 'SYN', -10,
                            'DIS - SYN CORP STK SPLIT ON 50 SHS')
                + rrow('May 5, 2025', 'Buy', 'SYN', 'SYN CORP', '60', '5',
                       '-300', 'CAD', 'SYN CORP UNSOLICITED DA'))

    def test_temp_code(self):
        txs, _err, _ = rbc_parse(self._body('X000004'))
        self.assertEqual(ratios(txs), [2.0, 1.2])
        self.assertEqual([t['symbol'] for t in of(txs, action='SPLIT')],
                         ['SYN.TO', 'SYN.TO'])

    def test_plain_symbol_control(self):
        txs, _err, _ = rbc_parse(self._body('SYN'))
        self.assertEqual(ratios(txs), [2.0, 1.2])

    def test_a_code_no_row_names_makes_no_listing(self):
        # Nothing resolves the code: no listing appears for it.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'rbc.csv'
            p.write_text(RH + rrow(
                'February 3, 2025', 'Reorganization', 'X000004', 'OTHER CORP',
                '-100', '', '0', 'CAD', 'DIS - OTHER CORP STK SPLIT ON 100 '
                'SHS'), encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                ctx = RbcBrokerage.prepare_files([p])
        self.assertEqual(ctx.listings, {})


class EndToEnd(unittest.TestCase):
    """The items' own repros through `tjs run`."""

    @staticmethod
    def _project(tmp, files):
        root = project(tmp, 'p', files)
        (root / 'taxjson.toml').write_text(toml(year=2025))
        return root

    @staticmethod
    def _text(account, rows):
        s = io.StringIO()
        s.write(f'Account Number: {account}\n')
        csv.writer(s).writerows([BLANK_HEAD] + rows)
        return s.getvalue()

    def test_two_accounts_merger_cash_in_the_gain(self):
        """TJ-1027: the fraction sold for 30, not 60."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, {
                'margin/rbc_a.csv': self._text(
                    99900001, [m_buy(12)] + m_legs(12) + [m_cil(10)]),
                'margin/rbc_b.csv': self._text(
                    99900002, [m_buy(24)] + m_legs(24) + [m_cil(20)])})
            evs = parse_all(sorted((root / 'inputs' / 'margin').iterdir()))
            tj(root, 'elect', 'margin', '--set',
               f'{evs[0].event_id}=rollover_s_85_1_5')
            tj(root, 'run')
            sold = [t for t in gains(root) if t.get('symbol') == 'BETA.TO']
            self.assertEqual(len(sold), 1, sold)
            self.assertAlmostEqual(sold[0]['proceeds'], 30.0)

    def test_mixed_layouts_run_strict(self):
        """TJ-1048: the item's repro."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, {
                'margin/rbc_2024.csv': DEMO_RH + demo_trade(
                    '11/04/2024', '11/06/2024', 'Sell', -100),
                'margin/rbc_2025.csv': NAMED_RH
                + RbcSplitMixedLayouts.SPLIT + RbcSplitMixedLayouts.COVER})
            r = tj(root, 'run', '--strict', check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('UNBOOKED', console(r))

    def test_taxable_merger_cash_reaches_the_books_dated(self):
        """TJ-1063: the date survives the run into the ledger's rows."""
        rows = [usd_row('2025-01-06', 'Buy', 'ABC', 'ABC CORPORATION', 12,
                        -120, 'ABC CORPORATION UNSOLICITED'), usd_row(
            '2025-03-17', 'Reorganization', 'ABC', 'ABC CORPORATION', -12, 0,
            'MGR - ABC CORPORATION MERGER TO ABDCO CORPORATION 1.05 NEW = 1 '
            'OLD'), usd_row(
            '2025-03-17', 'Reorganization', 'ABD', 'ABDCO CORPORATION', 12, 0,
            'MGR - ABDCO CORPORATION SHRS RECEIVED THRU MERGER'), usd_row(
            '2025-03-20', 'Reorganization', 'ABD', 'ABDCO CORPORATION', '',
            30, 'CIL - ABDCO CORPORATION CASH IN LIEU OF FRAC SHARES')]
        s = io.StringIO()
        csv.writer(s).writerows([NAMED_HEAD] + rows)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, {'margin/rbc.csv': s.getvalue()})
            evs = parse_all(sorted((root / 'inputs' / 'margin').iterdir()))
            tj(root, 'elect', 'margin', '--set',
               f'{evs[0].event_id}=taxable_disposition',
               '--hint', 'fmv_per_share=10')
            r = tj(root, 'run', check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            found = [p.name for p in (root / 'work').rglob('*.json')
                     if '30 USD @2025-03-20' in p.read_text()]
            self.assertTrue(found, sorted(
                p.name for p in (root / 'work').rglob('*')))


if __name__ == '__main__':
    unittest.main()

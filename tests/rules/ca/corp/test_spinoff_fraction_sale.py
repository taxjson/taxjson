"""Audit finding #107 (batch D): an s.86.1 spin-off's cash in lieu of a
fraction is a sale of the fraction (CA-CORP-05).

Questrade and RBC deliver whole spun-off shares and pay cash for the
fraction. Under the s.86.1 rollover the fraction is received with the
whole shares at its share of the allocated cost and sold for the cash:
the full quantity takes the full allocation, the parent gives up all of
it, and the fraction's gain is the cash less its share. RBC's cash-in-lieu
row was "NOT booked"; Questrade's own $0 pair for it is replaced (sold
once). Stock dividends, IB and the US §355 path keep their booking. A
taxable spin-off's fraction is part of the dividend at the same value,
received with the whole shares and sold for the cash (TJ-0948).

Every fixture is SYNTHETIC: account 99900001, tickers ZZP / ZZS.

Formerly: test_fix_batch_d_spinoff
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

from tax_rules import rule
from taxjson.rules import ids as R
import _paths

REPO = _paths.REPO
ACCT = '99900001'

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
RH = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
      '"Settlement Date","Account","Value","Currency","Description"\n')

SPIN_DAY = '2026-06-15'
CIL_DAY = '2026-06-18'
# 105 ZZP at 10 (cost 1050); s.86.1 allocates 210 CAD to the 10.5 ZZS the
# holding was entitled to (20 a share): the 0.5 fraction costs 10 and is
# paid 12 in cash (gain 2).
ALLOC = 210.0
CASH = '12.00'


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ----------------------------------------------------------------- Questrade

def q(td, action, sym, desc, qty, net='0', price='0', gross='0',
      act='Dividends'):
    """One Questrade export row (the examples/questrade_demo.csv shape)."""
    d = f"{td} 12:00:00 AM"
    return ','.join([d, d, action, sym, desc, qty, price, gross, '0', net,
                     'CAD', ACCT, act, 'Individual margin']) + '\n'


Q_BUY = q('2026-01-05', 'Buy', 'ZZP.TO', 'ZZP PARENT CORP WE ACTED AS AGENT',
          '105', net='-1050', price='10', gross='-1050', act='Trades')


def q_dis(qty='10'):
    return q(SPIN_DAY, 'DIS', 'ZZS.TO',
             'ZZS SPINCO INC SPINOFF FROM SEC# X123456 ZZP PARENT CORP '
             'REC 06/01/26 PAY 06/15/26 ON 105 SHS', qty)


def q_cil(day=CIL_DAY, net=CASH, sym='ZZS.TO', name='ZZS SPINCO INC'):
    return q(day, 'CIL', sym, f'{name} CASH IN LIEU OF .5 SHS', '0',
             net=net, act='Other')


def qt_parse(body):
    """The Questrade parser over one export (taxjson-brokerage's way)."""
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'qt_2026.csv'
        p.write_text(QH + body)
        par = QuestradeBrokerage()
        par.account_context = QuestradeBrokerage.prepare_files([p])
        return _quiet(par.parse_file, p)


def qt_events(body):
    from taxjson.lib.corp_actions import parse_questrade_corporate_actions
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'qt_2026.csv'
        p.write_text(QH + body)
        return _quiet(parse_questrade_corporate_actions, p, 'margin',
                      context_files=[p])


# ----------------------------------------------------------------------- RBC

def r(day, act, sym, symdesc, qty, value, desc, price=''):
    """One RBC export row (the examples/rbc_direct_demo.csv columns)."""
    cells = [day, act, sym, symdesc, qty, price, day, ACCT, value, 'CAD',
             desc]
    return ','.join(f'"{c}"' for c in cells) + '\n'


R_BUY = r('2026-01-05', 'Buy', 'ZZP', 'ZZP PARENT CORP', '105', '-1050',
          'ZZP PARENT CORP', price='10')


def r_dis(qty='10'):
    return r(SPIN_DAY, 'Reorganization', 'ZZS', 'ZZS SPINCO INC', qty, '0',
             'DIS - ZZS SPINCO INC SPINOFF ON 105 SHS FROM SEC# X123456 '
             'ZZP PARENT CORP REC 06/01/26 PAY 06/15/26')


def r_cil(desc='CIL - ZZS SPINCO INC CASH IN LIEU OF 0.5 SHS', qty='0',
          day=CIL_DAY, value=CASH):
    return r(day, 'Reorganization', 'ZZS', 'ZZS SPINCO INC', qty, value, desc)


def rbc_parse(body):
    from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'rbc_2026.csv'
        p.write_text(RH + body)
        par = RbcBrokerage()
        par.account_context = RbcBrokerage.prepare_files([p])
        return _quiet(par.parse_file, p)


def rbc_events(body):
    from taxjson.lib.rbc_corp_actions import parse_rbc_corporate_actions
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'rbc_2026.csv'
        p.write_text(RH + body)
        return _quiet(parse_rbc_corporate_actions, p, 'margin',
                      context_files=[p])


def s861(ev, alloc=ALLOC):
    from taxjson.lib.corp_actions import resolve_event
    return _quiet(resolve_event, ev, 'rollover_s_86_1', 'canada',
                  {'allocated_acb_cad': alloc})


def _rows(rows, action, sign=0):
    return [x for x in rows if x['action'] == action
            and (sign == 0 or (x.get('quantity') or 0) * sign > 0)]


# ------------------------------------------------------- end-to-end projects

def _env(home):
    env = dict(os.environ)
    env['HOME'] = str(home)
    env['TAXJSON_OFFLINE'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, '-m', 'taxjson.bin.taxjson_run', '-C', str(root),
         *args], cwd=str(REPO), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(root.parent / 'home'))


def _project_run(td, fname, text, election='rollover_s_86_1',
                 hint=f'allocated_acb_cad={ALLOC:g}'):
    """A one-account Canada project holding one export: run, elect the
    spin-off, run again. Returns (last run, gains, base rows)."""
    root = Path(td) / 'proj'
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'work').mkdir()
    (Path(td) / 'home').mkdir()
    (root / 'taxjson.toml').write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'option_grant_timing_since = 2026\n'
        '[accounts.margin]\ntype = "taxable"\n')
    d, lines = date(2025, 12, 1), []
    while d <= date(2027, 1, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.40 yahoo\n")
        d += timedelta(days=1)
    (root / 'work' / 'to_base.csv').write_text(''.join(lines))
    (root / 'inputs' / 'margin' / fname).write_text(text)
    res = _cli(root, 'run', '--no-input')
    pend = json.loads((root / 'work' / 'pending_elections.json').read_text())
    eid = pend['accounts']['margin']['pending'][0]['event_id']
    res = _cli(root, 'elections', 'margin', '--set', f'{eid}={election}',
               '--hint', hint)
    assert res.returncode == 0, res.stderr[-2000:]
    res = _cli(root, 'run', '--no-input')
    assert res.returncode == 0, res.stderr[-3000:]
    gains = json.loads((root / 'work' / 'margin_gains.json').read_text())
    base = json.loads((root / 'work' / 'margin_base.json').read_text())
    return res, gains, base['transactions']


class _E2E(unittest.TestCase):

    def assert_fraction_sold_once(self, gains, base, frac=0.5, whole=10.0):
        sales = [t for t in gains['transactions']
                 if t.get('symbol') == 'ZZS.TO' and t.get('qty')
                 and 'proceeds' in t and t.get('action') != 'DIVIDEND']
        self.assertEqual(len(sales), 1, sales)
        sale = sales[0]
        share = ALLOC * frac / (whole + frac)
        self.assertAlmostEqual(sale['qty'], frac, places=9)
        self.assertAlmostEqual(sale['proceeds'], float(CASH), places=2)
        self.assertAlmostEqual(sale['cost'], share, places=2)
        self.assertAlmostEqual(sale['gain'], float(CASH) - share, places=2)
        inv = {h['symbol']: h for h in gains['inventory']}
        # The parent gave up the FULL allocation; total basis conserved.
        self.assertAlmostEqual(inv['ZZP.TO']['total_cost'], 1050 - ALLOC,
                               places=2)
        spun = inv.get('ZZS.TO', {}).get('total_cost', 0.0)
        self.assertAlmostEqual(spun, ALLOC - share, places=2)
        self.assertAlmostEqual(inv['ZZP.TO']['total_cost'] + spun + share,
                               1050, places=2)
        # No $0 acquisition of the fraction is left in the books.
        zero_buys = [t for t in base if t.get('symbol') == 'ZZS.TO'
                     and t['action'] == 'BUYSELL'
                     and float(t.get('quantity') or 0) > 0
                     and abs(float(t.get('net_amount') or 0)) < 0.005]
        self.assertEqual(zero_buys, [])
        self.assertFalse(any(t.get('type') == 'cil_takeover' for t in base))


# ========================================================================
# RBC
# ========================================================================

class TestRbcSpinoffCashInLieu(_E2E):

    def test_cil_row_is_paired_with_the_spinoff_not_warned(self):
        txs, err = rbc_parse(R_BUY + r_dis() + r_cil())
        self.assertNotIn('cash-in-lieu row with no reorganization', err)
        self.assertNotIn('NOT booked', err)
        # Nothing booked by the parser for the CIL: taxjson-corp-actions'.
        self.assertEqual([t for t in txs if t.get('symbol', '')
                          .startswith('ZZS')], [])
        self.assertIn('cash-in-lieu row(s) for their fractions', err)

    def test_event_carries_the_cash_and_the_fraction(self):
        (evs, _err) = rbc_events(R_BUY + r_dis() + r_cil())
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertAlmostEqual(ev.cash_in_lieu, 12.0)
        self.assertEqual(ev.cash_in_lieu_currency, 'CAD')
        self.assertAlmostEqual(ev.fraction_in_lieu, 0.5)
        self.assertEqual(ev.cash_in_lieu_date, CIL_DAY)
        self.assertFalse(ev.cil_broker_booked)
        # The fraction stated in the Quantity column is read too.
        (evs, _err) = rbc_events(R_BUY + r_dis() + r_cil(
            desc='CIL - ZZS SPINCO INC CASH IN LIEU OF FRAC SHARES',
            qty='0.5'))
        self.assertAlmostEqual(evs[0].fraction_in_lieu, 0.5)

    @rule(R.CA_CORP_05)
    def test_s86_1_rows_sell_the_fraction_at_the_cash(self):
        (evs, _err) = rbc_events(R_BUY + r_dis() + r_cil())
        rows, err = s861(evs[0])
        buy, = _rows(rows, 'BUYSELL', +1)
        sale, = _rows(rows, 'BUYSELL', -1)
        adj, = _rows(rows, 'ADJUST')
        self.assertAlmostEqual(buy['quantity'], 10.5)
        self.assertAlmostEqual(buy['net_amount'], ALLOC)
        self.assertNotIn('corp_cash', buy)
        self.assertAlmostEqual(adj['net_amount'], -ALLOC)
        self.assertEqual(adj['symbol'], 'ZZP.TO')
        self.assertAlmostEqual(sale['quantity'], -0.5)
        self.assertAlmostEqual(sale['net_amount'], 12.0)
        self.assertEqual(sale['date'], CIL_DAY)
        self.assertEqual(sale['corp_cash'], '12 CAD')
        self.assertNotIn('type', sale)          # RBC's parser booked nothing
        self.assertNotIn('UNBOOKED', err)

    @rule(R.CA_CORP_05)
    def test_end_to_end_one_sale_at_its_allocated_cost(self):
        with tempfile.TemporaryDirectory() as td:
            res, gains, base = _project_run(
                td, 'rbc_2026.csv', RH + R_BUY + r_dis() + r_cil())
            self.assertNotIn('cash-in-lieu row with no reorganization',
                             res.stdout + res.stderr)
            self.assert_fraction_sold_once(gains, base)

    @rule(R.CA_CORP_05)
    def test_under_one_share(self):
        (evs, _err) = rbc_events(R_BUY + r_dis('0') + r_cil())
        rows, _ = s861(evs[0], alloc=10.0)
        buy, = _rows(rows, 'BUYSELL', +1)
        sale, = _rows(rows, 'BUYSELL', -1)
        adj, = _rows(rows, 'ADJUST')
        self.assertAlmostEqual(buy['quantity'], 0.5)
        self.assertAlmostEqual(buy['net_amount'], 10.0)
        self.assertAlmostEqual(adj['net_amount'], -10.0)
        self.assertAlmostEqual(sale['quantity'], -0.5)
        self.assertAlmostEqual(sale['net_amount'], 12.0)

    @rule(R.CA_CORP_05)
    def test_unstated_fraction_keeps_the_booking_and_warns(self):
        (evs, _err) = rbc_events(R_BUY + r_dis() + r_cil(
            desc='CIL - ZZS SPINCO INC CASH IN LIEU OF FRAC SHARES'))
        self.assertAlmostEqual(evs[0].cash_in_lieu, 12.0)
        self.assertEqual(evs[0].fraction_in_lieu, 0.0)
        rows, err = s861(evs[0])
        self.assertEqual(_rows(rows, 'BUYSELL', -1), [])
        buy, = _rows(rows, 'BUYSELL', +1)
        self.assertAlmostEqual(buy['quantity'], 10.0)
        self.assertAlmostEqual(buy['net_amount'], ALLOC)
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('NOT booked', err)

    @rule(R.CA_CORP_07)
    def test_taxable_spinoff_books_and_sells_the_fraction(self):
        """TJ-0948: the fraction paid in cash is part of the dividend
        (10.5 x 20 = 210), received with the whole shares at that value
        and sold for the cash: no UNBOOKED warning."""
        from taxjson.lib.corp_actions import resolve_event
        (evs, _err) = rbc_events(R_BUY + r_dis() + r_cil())
        (plain, _err) = rbc_events(R_BUY + r_dis())
        hints = {'fmv_per_share': 20.0}
        rows, err = _quiet(resolve_event, evs[0], 'taxable_deemed_dividend',
                           'canada', hints)
        before, err0 = _quiet(resolve_event, plain[0],
                              'taxable_deemed_dividend', 'canada', hints)
        self.assertNotIn('UNBOOKED', err0)
        self.assertNotIn('UNBOOKED', err)
        div, = _rows(rows, 'DIVIDEND')
        buy, = _rows(rows, 'BUYSELL', +1)
        sale, = _rows(rows, 'BUYSELL', -1)
        self.assertAlmostEqual(div['net_amount'], 210.0)
        self.assertAlmostEqual(buy['quantity'], 10.5)
        self.assertAlmostEqual(buy['net_amount'], 210.0)
        self.assertAlmostEqual(sale['quantity'], -0.5)
        self.assertAlmostEqual(sale['net_amount'], 12.0)
        self.assertEqual(sale['date'], CIL_DAY)
        self.assertEqual(sale['corp_cash'], '12 CAD')
        self.assertNotIn('type', sale)          # RBC's parser booked nothing
        # With no fraction paid in cash, the booking is as it was.
        self.assertEqual([(x['action'], x.get('quantity'), x['net_amount'])
                          for x in before],
                         [('DIVIDEND', 0.0, 200.0), ('BUYSELL', 10.0, 200.0)])
        # A fraction sold by hand in a .tt file (the old warning's fix)
        # is left to that sale: never sold twice.
        hand = [{'date': CIL_DAY, 'symbol': 'ZZS.TO', 'quantity': -0.5,
                 'source': 'cil.tt:1'}]
        rows2, err2 = _quiet(resolve_event, evs[0],
                             'taxable_deemed_dividend', 'canada', hints,
                             tt_sales=hand)
        self.assertEqual(_rows(rows2, 'BUYSELL', -1), [])
        self.assertIn('booked by hand (cil.tt:1', err2)

    def test_other_reorganization_cil_still_unmatched(self):
        # A CIL of a security with no event stays the parser's warning.
        txs, err = rbc_parse(R_BUY + r(
            CIL_DAY, 'Reorganization', 'ZZQ', 'ZZQ OTHER CORP', '0', '5',
            'CIL - ZZQ OTHER CORP CASH IN LIEU OF FRAC SHARES'))
        self.assertIn('cash-in-lieu row with no reorganization', err)


# ========================================================================
# Questrade
# ========================================================================

class TestQuestradeSpinoffCashInLieu(_E2E):

    def test_event_carries_the_cash_and_the_fraction(self):
        (evs, _err) = qt_events(Q_BUY + q_dis() + q_cil())
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertAlmostEqual(ev.qty_received, 10.0)
        self.assertAlmostEqual(ev.cash_in_lieu, 12.0)
        self.assertAlmostEqual(ev.fraction_in_lieu, 0.5)
        self.assertEqual(ev.cash_in_lieu_date, CIL_DAY)
        self.assertTrue(ev.cil_broker_booked)
        # The record date it prints (who is entitled: TJ-0951).
        self.assertEqual(ev.record_date, '2026-06-01')

    def test_a_reversed_cil_carries_no_cash(self):
        (evs, _err) = qt_events(Q_BUY + q_dis() + q_cil()
                                + q_cil(day='2026-06-20', net='-12.00'))
        self.assertEqual(evs[0].cash_in_lieu, 0.0)
        self.assertFalse(evs[0].cil_broker_booked)

    @rule(R.CA_CORP_05)
    def test_s86_1_sale_replaces_the_parsers_pair(self):
        from taxjson.lib.corp_actions import drop_broker_cil_pairs
        body = Q_BUY + q_dis() + q_cil()
        txs, _ = qt_parse(body)
        pair = [t for t in txs if t.get('symbol') == 'ZZS.TO']
        self.assertEqual(len(pair), 2)          # the parser's $0 pair
        (evs, _) = qt_events(body)
        rows, _ = s861(evs[0])
        sale, = _rows(rows, 'BUYSELL', -1)
        self.assertEqual(sale['type'], 'cil_takeover')
        for t in txs + rows:
            t['account'] = 'margin'     # the stage's account label
        kept, err = _quiet(drop_broker_cil_pairs, txs + rows)
        zzs = [t for t in kept if t.get('symbol') == 'ZZS.TO']
        self.assertEqual(sorted(t['quantity'] for t in zzs), [-0.5, 10.5])
        self.assertNotIn('type', sale)          # the mark is cleared
        self.assertIn('replaces the broker', err)
        # No parser pair among the inputs: kept, and said.
        rows2, _ = s861(evs[0])
        kept2, err2 = _quiet(drop_broker_cil_pairs, rows2)
        self.assertEqual(len(kept2), len(rows2))
        self.assertIn('not among this account', err2)

    @rule(R.CA_CORP_05)
    def test_end_to_end_fraction_sold_once(self):
        for cil_day in (SPIN_DAY, CIL_DAY):
            with self.subTest(cil_day=cil_day), \
                    tempfile.TemporaryDirectory() as td:
                _res, gains, base = _project_run(
                    td, 'qt_2026.csv', QH + Q_BUY + q_dis()
                    + q_cil(day=cil_day))
                self.assert_fraction_sold_once(gains, base)

    @rule(R.CA_CORP_05)
    def test_under_one_share(self):
        (evs, _err) = qt_events(Q_BUY + q_dis('0') + q_cil())
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].qty_received, 0.0)
        rows, _ = s861(evs[0], alloc=10.0)
        buy, = _rows(rows, 'BUYSELL', +1)
        sale, = _rows(rows, 'BUYSELL', -1)
        self.assertAlmostEqual(buy['quantity'], 0.5)
        self.assertAlmostEqual(buy['net_amount'], 10.0)
        self.assertAlmostEqual(sale['quantity'], -0.5)
        self.assertAlmostEqual(sale['net_amount'], 12.0)
        # A zero-quantity chain with no cash in lieu is still no event.
        (evs, _err) = qt_events(Q_BUY + q_dis('0'))
        self.assertEqual(evs, [])

    @rule(R.CA_CORP_07)
    def test_taxable_spinoff_replaces_the_parsers_pair(self):
        """TJ-0948: the taxable spin-off sells the fraction at its value
        as income; the parser's $0 pair for the cash is dropped (sold
        once, never at a $0 cost diluting the whole shares')."""
        from taxjson.lib.corp_actions import (drop_broker_cil_pairs,
                                              resolve_event)
        body = Q_BUY + q_dis() + q_cil()
        (evs, _) = qt_events(body)
        rows, _ = _quiet(resolve_event, evs[0], 'taxable_deemed_dividend',
                         'canada', {'fmv_per_share': 20.0})
        self.assertEqual([x['action'] for x in rows],
                         ['DIVIDEND', 'BUYSELL', 'BUYSELL'])
        self.assertAlmostEqual(rows[0]['net_amount'], 210.0)
        self.assertNotIn('corp_cash', rows[0])
        sale, = _rows(rows, 'BUYSELL', -1)
        self.assertEqual(sale['type'], 'cil_takeover')
        txs, _ = qt_parse(body)
        for t in txs + rows:
            t['account'] = 'margin'     # the stage's account label
        kept, err = _quiet(drop_broker_cil_pairs, txs + rows)
        zzs = [t for t in kept if t.get('symbol') == 'ZZS.TO']
        self.assertEqual(sorted(t['quantity'] for t in zzs), [-0.5, 10.5])
        self.assertIn('replaces the broker', err)

    @rule(R.CA_CORP_07)
    def test_end_to_end_taxable_fraction(self):
        """TJ-0948, a real run: the dividend is 10.5 x 20 = 210, the
        fraction is sold for 12 on its 10 cost, and the 10 whole shares
        keep 200 (not 200 spread over 10.5 by the parser's $0 buy)."""
        with tempfile.TemporaryDirectory() as td:
            _res, gains, base = _project_run(
                td, 'qt_2026.csv', QH + Q_BUY + q_dis() + q_cil(),
                election='taxable_deemed_dividend', hint='fmv_per_share=20')
            divs = [t for t in base if t['action'] == 'DIVIDEND'
                    and t.get('symbol') == 'ZZP.TO']
            self.assertEqual([round(float(t['net_amount']), 4)
                              for t in divs], [210.0])
            sales = [t for t in gains['transactions']
                     if t.get('symbol') == 'ZZS.TO' and t.get('qty')]
            self.assertEqual(len(sales), 1, sales)
            self.assertAlmostEqual(sales[0]['qty'], 0.5, places=9)
            self.assertAlmostEqual(sales[0]['cost'], 10.0, places=4)
            self.assertAlmostEqual(sales[0]['proceeds'], 12.0, places=4)
            inv = {h['symbol']: h for h in gains['inventory']}
            self.assertAlmostEqual(inv['ZZS.TO']['qty'], 10.0, places=9)
            self.assertAlmostEqual(inv['ZZS.TO']['total_cost'], 200.0,
                                   places=4)

    def test_stock_dividend_cil_keeps_the_parsers_pair(self):
        from taxjson.lib.corp_actions import drop_broker_cil_pairs
        body = (Q_BUY
                + q('2026-03-02', 'DIS', 'ZZP.TO', 'ZZP PARENT CORP STK DIV '
                    'ON 105 SHS REC 02/20/26 PAY 03/02/26', '5')
                + q_cil(day='2026-03-02', sym='ZZP.TO',
                        name='ZZP PARENT CORP'))
        (evs, _) = qt_events(body)
        self.assertEqual(evs, [])
        txs, _ = qt_parse(body)
        pair = [t for t in drop_broker_cil_pairs(txs)
                if 'CASH IN LIEU' in t.get('description', '')]
        self.assertEqual(sorted(t['quantity'] for t in pair), [-0.5, 0.5])
        self.assertEqual(sorted(t['net_amount'] for t in pair), [0.0, 12.0])


# ========================================================================
# Unchanged: no fraction, IB
# ========================================================================

def _event(**kw):
    from taxjson.lib.corp_actions import CorporateAction
    base = dict(date=SPIN_DAY, time='09:30:00', action_type='spinoff',
                source_symbol='ZZP.TO', source_isin='X123456',
                target_symbol='ZZS.TO', target_isin='ZZS.TO',
                ratio_new=10, ratio_old=105, qty_disposed=0.0,
                qty_received=10.0, fmv=0.0, currency='CAD',
                target_currency='CAD', account='margin')
    base.update(kw)
    return CorporateAction(**base)


class TestUnchanged(unittest.TestCase):

    @rule(R.CA_CORP_06)
    def test_no_fraction_books_as_before(self):
        rows, _ = s861(_event())
        self.assertEqual(
            [(x['action'], x['symbol'], x.get('quantity'), x['net_amount'])
             for x in rows],
            [('BUYSELL', 'ZZS.TO', 10.0, ALLOC),
             ('ADJUST', 'ZZP.TO', None, -ALLOC)])
        self.assertFalse(any('corp_cash' in x for x in rows))

    @rule(R.CA_CORP_05)
    def test_ib_fractional_delivery_is_not_sold(self):
        # IB delivers the fraction and its own cash-in-lieu row sells it
        # (the IB parser): the election books no second sale.
        rows, _ = s861(_event(qty_received=10.5, ratio_new=10.5,
                              fractional_delivery=True))
        self.assertEqual(_rows(rows, 'BUYSELL', -1), [])
        buy, = _rows(rows, 'BUYSELL', +1)
        self.assertAlmostEqual(buy['quantity'], 10.5)
        self.assertAlmostEqual(buy['net_amount'], ALLOC)

    @rule(R.CA_CORP_05)
    def test_ib_dust_is_snapped_not_sold(self):
        from taxjson.lib.corp_actions import _emit_allocated_basis_spinoff
        ev = _event(qty_received=10.004, ratio_new=10.004,
                    fractional_delivery=True, target_fmv=200.08)
        rows, err = s861(ev)
        self.assertEqual(_rows(rows, 'BUYSELL', -1), [])
        buy, = _rows(rows, 'BUYSELL', +1)
        self.assertAlmostEqual(buy['quantity'], 10.0)
        self.assertNotIn('UNBOOKED', err)
        # Exactly the rows the shared emitter books without the new path.
        old, _ = _quiet(_emit_allocated_basis_spinoff, ev,
                        {'allocated_acb_cad': ALLOC},
                        description_base='x', allocated_acb=ALLOC,
                        alloc_cur='CAD')
        new, _ = _quiet(_emit_allocated_basis_spinoff, ev,
                        {'allocated_acb_cad': ALLOC},
                        description_base='x', allocated_acb=ALLOC,
                        alloc_cur='CAD', sell_fraction=True)
        self.assertEqual(old, new)

    @rule(R.CA_CORP_05)
    def test_ib_events_carry_no_cash_in_lieu(self):
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        head = ('Statement,Header,Field Name,Field Value\n'
                'Statement,Data,BrokerName,Interactive Brokers\n'
                'Account Information,Header,Field Name,Field Value\n'
                'Account Information,Data,Account,U1234567\n'
                'Account Information,Data,Base Currency,CAD\n'
                'Corporate Actions,Header,Asset Category,Currency,'
                'Report Date,Date/Time,Description,Quantity,Proceeds,'
                'Value,Realized P/L,Code\n')
        spin = ('ZZP(US0000000777) Spinoff  1 for 10 '
                '(ZZS, ZZS SPINCO INC, US0000000778)')
        cil = 'ZZS(US0000000778) Cash in lieu of fractional share'
        body = (f'Corporate Actions,Data,Stocks,USD,2026-06-15,'
                f'"2026-06-15, 20:25:00","{spin}",10.5,0,210,0,\n'
                f'Corporate Actions,Data,Stocks,USD,2026-06-15,'
                f'"2026-06-15, 20:25:00","{cil}",-0.5,12,0,0,\n')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ib_2026.csv'
            p.write_text(head + body)
            evs, _err = _quiet(parse_ib_corporate_actions, p, 'margin')
        spins = [e for e in evs if e.action_type == 'spinoff']
        self.assertEqual(len(spins), 1)
        self.assertEqual((spins[0].cash_in_lieu, spins[0].fraction_in_lieu,
                          spins[0].cil_broker_booked), (0.0, 0.0, False))
        rows, _ = s861(spins[0])
        self.assertEqual(_rows(rows, 'BUYSELL', -1), [])


# ========================================================================
# Cash in lieu paid on two days (backlog TJ-5057)
# ========================================================================

DEC_DAY, JAN_DAY = '2026-12-31', '2027-01-04'


def _sales(rows):
    return [(x['date'], x['symbol'], x['quantity'], x['net_amount'])
            for x in _rows(rows, 'BUYSELL', -1)]


class TestCashInLieuPaidOnTwoDays(unittest.TestCase):
    """One spin-off held in two broker accounts of one taxjson account,
    each paid for its half share, one in December and one in January:
    the combined event (one election) sold one share for all of the cash
    in December. Each day's fraction is sold on its own day."""

    @staticmethod
    def _copies(**kw):
        cil = dict(date='2026-12-20', cash_in_lieu=5.0,
                   cash_in_lieu_currency='CAD', fraction_in_lieu=0.5,
                   qty_received=5.0, ratio_new=1, ratio_old=10,
                   target_fmv=50.0, event_id='EVT1')
        cil.update(kw)
        return (_event(cash_in_lieu_date=DEC_DAY,
                       broker_account='99900011', **cil),
                _event(cash_in_lieu_date=JAN_DAY,
                       broker_account='99900022', **cil))

    @staticmethod
    def _combine(events):
        from taxjson.lib.corp_actions import combine_broker_copies
        err = io.StringIO()
        out = combine_broker_copies(list(events), stream=err)
        return out, err.getvalue()

    def test_the_combined_event_keeps_each_days_cash(self):
        for order in (0, 1):
            a, b = self._copies()
            (ev,), err = self._combine((a, b) if not order else (b, a))
            self.assertIn('their quantities are combined', err)
            self.assertEqual((ev.qty_received, ev.fraction_in_lieu,
                              ev.cash_in_lieu, ev.cash_in_lieu_date),
                             (10.0, 1.0, 10.0, DEC_DAY))
            self.assertEqual(ev.cash_in_lieu_parts,
                             [(DEC_DAY, 0.5, 5.0), (JAN_DAY, 0.5, 5.0)])
        # Paid the same day: one part, so no parts (the booking as before).
        a, b = self._copies()
        b.cash_in_lieu_date = DEC_DAY
        (ev,), _err = self._combine((a, b))
        self.assertEqual(ev.cash_in_lieu_parts, [])
        self.assertEqual(_sales(s861(ev, alloc=100.0)[0]),
                         [(DEC_DAY, 'ZZS.TO', -1.0, 10.0)])
        # A third account paid on the first day joins that day's part.
        a, b = self._copies()
        c = self._copies()[0]
        c.broker_account = '99900033'
        (ev,), _err = self._combine((a, b, c))
        self.assertEqual(ev.cash_in_lieu_parts,
                         [(DEC_DAY, 1.0, 10.0), (JAN_DAY, 0.5, 5.0)])

    @rule(R.CA_CORP_05)
    def test_s86_1_sells_each_days_fraction_on_its_day(self):
        a, b = self._copies()
        (ev,), _err = self._combine((a, b))
        rows, err = s861(ev, alloc=100.0)
        self.assertEqual(_sales(rows), [(DEC_DAY, 'ZZS.TO', -0.5, 5.0),
                                        (JAN_DAY, 'ZZS.TO', -0.5, 5.0)])
        # What the two accounts book apart, each on half the allocation.
        apart = [x for e in self._copies()
                 for x in _sales(s861(e, alloc=50.0)[0])]
        self.assertEqual(_sales(rows), apart)
        # The allocation is the event's, counted once.
        buy, = _rows(rows, 'BUYSELL', +1)
        adj, = _rows(rows, 'ADJUST')
        self.assertAlmostEqual(buy['quantity'], 11.0)
        self.assertAlmostEqual(buy['net_amount'], 100.0)
        self.assertAlmostEqual(adj['net_amount'], -100.0)
        for sale in _rows(rows, 'BUYSELL', -1):
            self.assertEqual(sale['date_settle'], sale['date'])
            self.assertEqual(sale['corp_cash'], '5 CAD')
            self.assertIn('cash in lieu of 0.5 fractional share(s)',
                          sale['description'])
        self.assertNotIn('UNBOOKED', err)

    @rule(R.CA_CORP_05)
    def test_each_sale_is_in_its_own_year(self):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        from taxjson.lib.tx_load import coerce_transaction_row
        from taxjson.model.transaction import TaxTransaction
        a, b = self._copies()
        (ev,), _err = self._combine((a, b))
        rows, _ = s861(ev, alloc=110.0)       # 10 a share over 11 shares
        buy = TaxTransaction(
            action='BUYSELL', date='2026-01-05', date_settle='2026-01-05',
            time='09:30:00', symbol='ZZP.TO', quantity=100, currency='CAD',
            price=10.0, net_amount=-1000.0, account='margin')
        tx = [buy] + [coerce_transaction_row(x, i, 'synthetic')
                      for i, x in enumerate(rows)]
        out = CanadaTaxRules().compute_gains(tx, detect_wash_sales=False)
        sold = sorted((t['date'], round(t['gain'], 2))
                      for t in out['transactions']
                      if t.get('symbol') == 'ZZS.TO' and 'proceeds' in t)
        # 5 cash less the half share's 5 of cost: nothing, in each year.
        self.assertEqual(sold, [(DEC_DAY, 0.0), (JAN_DAY, 0.0)])

    @rule(R.CA_CORP_07)
    def test_taxable_spinoff_sells_each_days_fraction_on_its_day(self):
        from taxjson.lib.corp_actions import resolve_event
        a, b = self._copies()
        (ev,), _err = self._combine((a, b))
        rows, _ = _quiet(resolve_event, ev, 'taxable_deemed_dividend',
                         'canada', {})
        self.assertEqual(_sales(rows), [(DEC_DAY, 'ZZS.TO', -0.5, 5.0),
                                        (JAN_DAY, 'ZZS.TO', -0.5, 5.0)])
        div, = _rows(rows, 'DIVIDEND')
        self.assertAlmostEqual(div['net_amount'], 110.0)

    @rule(R.CA_CORP_05)
    def test_questrade_marks_each_sale_to_replace_its_pair(self):
        a, b = self._copies(cil_broker_booked=True)
        (ev,), _err = self._combine((a, b))
        rows, _ = s861(ev, alloc=100.0)
        self.assertEqual([x.get('type') for x in _rows(rows, 'BUYSELL', -1)],
                         ['cil_takeover', 'cil_takeover'])

    @rule(R.CA_CORP_05)
    def test_an_unsized_copy_splits_the_fraction_by_the_cash(self):
        # One account's export does not size its fraction: the days'
        # shares of what is sold follow the cash (one price a share).
        a, b = self._copies()
        b.fraction_in_lieu = 0.0
        b.cash_in_lieu = 15.0
        (ev,), _err = self._combine((a, b))
        rows, _ = s861(ev, alloc=100.0)
        self.assertEqual(_sales(rows), [(DEC_DAY, 'ZZS.TO', -0.125, 5.0),
                                        (JAN_DAY, 'ZZS.TO', -0.375, 15.0)])

    @rule(R.CA_CORP_04)
    def test_merger_rollover_sells_each_days_fraction_on_its_day(self):
        from taxjson.lib.corp_actions import resolve_event
        kw = dict(action_type='merger', date='2026-12-20',
                  target_isin='CA0000000002', source_isin='CA0000000001',
                  ratio_new=1.05, ratio_old=1.0, qty_disposed=10.0,
                  qty_received=10.0, cash_in_lieu_currency='CAD',
                  event_id='EVT2')
        a = _event(cash_in_lieu=6.0, cash_in_lieu_date=DEC_DAY,
                   broker_account='99900011', **kw)
        b = _event(cash_in_lieu=6.0, cash_in_lieu_date=JAN_DAY,
                   broker_account='99900022', **kw)
        (ev,), _err = self._combine((a, b))
        rows, _ = _quiet(resolve_event, ev, 'rollover_s_85_1_5', 'canada', {})
        got = [(d, sym, round(q, 9), n) for d, sym, q, n in _sales(rows)]
        self.assertEqual(got, [(DEC_DAY, 'ZZS.TO', -0.5, 6.0),
                               (JAN_DAY, 'ZZS.TO', -0.5, 6.0)])
        # The taxable exchange keeps its rows on the exchange's day and
        # names each day's cash for the cash ledger.
        rows, _ = _quiet(resolve_event, ev, 'taxable_disposition', 'canada',
                         {'fmv_per_share': 12.0})
        self.assertEqual(rows[0]['corp_cash'],
                         f'6 CAD @{DEC_DAY}; 6 CAD @{JAN_DAY}')

    @rule(R.CA_CORP_05)
    def test_rbc_one_account_paid_on_two_days(self):
        late = r_cil(desc='CIL - ZZS SPINCO INC CASH IN LIEU OF 0.25 SHS',
                     day='2026-06-29', value='6.00')
        (evs, _err) = rbc_events(R_BUY + r_dis() + r_cil() + late)
        ev, = evs
        self.assertEqual(ev.cash_in_lieu_date, CIL_DAY)
        self.assertEqual(ev.cash_in_lieu_parts,
                         [(CIL_DAY, 0.5, 12.0), ('2026-06-29', 0.25, 6.0)])
        rows, _ = s861(ev)
        self.assertEqual(_sales(rows),
                         [(CIL_DAY, 'ZZS.TO', -0.5, 12.0),
                          ('2026-06-29', 'ZZS.TO', -0.25, 6.0)])
        buy, = _rows(rows, 'BUYSELL', +1)
        self.assertAlmostEqual(buy['quantity'], 10.75)

    # A reversal and the payment made again correct the amount, not the
    # day (owner, 2026-11-23): the payment that replaces a reversed one
    # keeps the reversed payment's day, whatever day or order the broker
    # posts the pair on, and whether the amount changed.
    REVERSALS = (
        ("repaid later", [('2026-06-22', '-12.00'), ('2026-06-25', CASH)],
         12.0),
        ("reversed and repaid the same day",
         [('2026-06-25', '-12.00'), ('2026-06-25', CASH)], 12.0),
        ("the repayment listed before the reversal",
         [('2026-06-25', CASH), ('2026-06-25', '-12.00')], 12.0),
        ("repaid at a corrected amount",
         [('2026-06-25', '-12.00'), ('2026-06-25', '12.50')], 12.5),
        ("reversed and repaid twice",
         [('2026-06-22', '-12.00'), ('2026-06-23', '12.50'),
          ('2026-06-24', '-12.50'), ('2026-06-26', CASH)], 12.0),
    )

    @rule(R.CA_CORP_05)
    def test_rbc_a_payment_made_again_keeps_the_reversed_ones_day(self):
        for name, later, cash in self.REVERSALS:
            body = R_BUY + r_dis() + r_cil() + ''.join(
                r_cil(day=d, value=v) for d, v in later)
            (evs, _err) = rbc_events(body)
            ev, = evs
            self.assertAlmostEqual(ev.cash_in_lieu, cash, msg=name)
            self.assertEqual((ev.cash_in_lieu_date, ev.cash_in_lieu_parts),
                             (CIL_DAY, []), name)
            self.assertEqual(_sales(s861(ev)[0]),
                             [(CIL_DAY, 'ZZS.TO', -0.5, cash)], name)

    @rule(R.CA_CORP_05)
    def test_rbc_a_reversal_that_cancels_no_payment_keeps_the_first_day(self):
        body = (R_BUY + r_dis() + r_cil()
                + r_cil(desc='CIL - ZZS SPINCO INC CASH IN LIEU OF 0.25 SHS',
                        day='2026-06-29', value='6.00')
                + r_cil(desc='CIL - ZZS SPINCO INC CASH IN LIEU OF 0.1 SHS',
                        day='2026-07-02', value='-3.00'))
        (evs, _err) = rbc_events(body)
        ev, = evs
        self.assertEqual((ev.cash_in_lieu_date, ev.cash_in_lieu_parts),
                         (CIL_DAY, []))
        self.assertEqual(_sales(s861(ev)[0]),
                         [(CIL_DAY, 'ZZS.TO', -0.65, 15.0)])

    def test_the_days_payments_count_as_paid(self):
        from types import SimpleNamespace as N
        from taxjson.lib.corp_actions import cil_paid_days

        def days(*rows, accounts=None):
            said = []
            paid = cil_paid_days(
                [N(date=d, value=v, frac=(f[0] if f else 0.0),
                   account=(accounts[i] if accounts else ''))
                 for i, (d, v, *f) in enumerate(rows)],
                lambda c: c.frac, said.append)
            return (None if paid is None
                    else [(d, c.value) for d, c in paid]), len(said)
        self.assertEqual(days(('06-18', 12.0), ('06-29', 6.0)),
                         ([('06-18', 12.0), ('06-29', 6.0)], 0))
        self.assertEqual(days(('06-18', 12.0), ('06-22', -12.0),
                              ('06-25', 12.0)), ([('06-18', 12.0)], 0))
        # A reversal and a payment of its amount on one day cancel each
        # other, whatever the rows' order: the earlier payments stand on
        # their own days.
        for pair in ((('06-25', -12.0), ('06-25', 12.0)),
                     (('06-25', 12.0), ('06-25', -12.0))):
            self.assertEqual(days(('06-18', 12.0), ('06-20', 12.0), *pair),
                             ([('06-18', 12.0), ('06-20', 12.0)], 0))
        # ... and with no earlier payment, the payment made later takes
        # the cancelled one's day (a reversal posted with its payment).
        self.assertEqual(days(('06-17', -12.0), ('06-17', 12.0),
                              ('06-25', 12.0)), ([('06-17', 12.0)], 0))
        # No payment made again: the reversal cancels the duplicate (the
        # later one), the original keeps its day, and a later payment of
        # the event is never re-dated (it was sold a month before it
        # was paid).
        self.assertEqual(days(('06-18', 12.0), ('06-20', 12.0),
                              ('06-25', -12.0), ('07-20', 6.0)),
                         ([('06-18', 12.0), ('07-20', 6.0)], 0))
        # A corrected amount replaces the only payment, reversed, where
        # the books show it: it states the reversed payment's fraction,
        # or neither states one and nothing else stands.
        self.assertEqual(days(('06-18', 12.0), ('06-25', -12.0),
                              ('06-26', 12.5)), ([('06-18', 12.5)], 0))
        self.assertEqual(days(('06-18', 12.0, 0.5), ('06-20', 6.0, 0.25),
                              ('06-25', -12.0), ('07-01', 12.5, 0.5)),
                         ([('06-20', 6.0), ('06-18', 12.5)], 0))
        # Where they do not (another fraction stated, even with nothing
        # else standing; or another payment standing), the later payment
        # keeps its own day, and it is said.
        self.assertEqual(days(('06-18', 12.0, 0.5), ('06-22', -12.0),
                              ('07-20', 6.0, 0.25)), ([('07-20', 6.0)], 1))
        self.assertEqual(days(('06-18', 12.0, 0.5), ('06-20', 6.0, 0.25),
                              ('06-25', -12.0), ('07-01', 5.0, 0.2)),
                         ([('06-20', 6.0), ('07-01', 5.0)], 1))
        self.assertEqual(days(('06-17', 6.0), ('06-18', 12.0),
                              ('06-22', -12.0), ('07-20', 7.0)),
                         ([('06-17', 6.0), ('07-20', 7.0)], 1))
        # A payment in another broker account is no replacement: it
        # keeps its own day, and a reversal reads its own account's
        # payments only.
        self.assertEqual(days(('12-30', 12.0), ('01-04', -12.0),
                              ('01-06', 12.0), accounts='AAB')[0],
                         [('01-06', 12.0)])
        self.assertIsNone(days(('12-30', 12.0), ('01-04', -12.0),
                               accounts='AB')[0])
        self.assertEqual(days(('06-18', 12.0), ('06-20', 6.0),
                              ('06-25', -12.0)), ([('06-20', 6.0)], 0))
        self.assertEqual(days(('06-18', 12.0), ('06-25', -3.0)), (None, 0))
        self.assertEqual(days(), ([], 0))

    @rule(R.CA_CORP_05)
    def test_rbc_a_reversed_duplicate_moves_no_other_payment(self):
        quarter = 'CIL - ZZS SPINCO INC CASH IN LIEU OF 0.25 SHS'
        body = (R_BUY + r_dis() + r_cil() + r_cil(day='2026-06-20')
                + r_cil(day='2026-06-25', value='-12.00')
                + r_cil(desc=quarter, day='2026-07-20', value='6.00'))
        (evs, _err) = rbc_events(body)
        ev, = evs
        self.assertEqual(ev.cash_in_lieu_parts,
                         [(CIL_DAY, 0.5, 12.0), ('2026-07-20', 0.25, 6.0)])
        self.assertEqual(_sales(s861(ev)[0]),
                         [(CIL_DAY, 'ZZS.TO', -0.5, 12.0),
                          ('2026-07-20', 'ZZS.TO', -0.25, 6.0)])
        # A later payment the books do not show to replace the reversed
        # one keeps its own day, and the reader says so.
        fifth = 'CIL - ZZS SPINCO INC CASH IN LIEU OF 0.2 SHS'
        body = (R_BUY + r_dis() + r_cil()
                + r_cil(desc=quarter, day='2026-06-20', value='6.00')
                + r_cil(day='2026-06-25', value='-12.00')
                + r_cil(desc=fifth, day='2026-07-01', value='5.00'))
        (evs, err) = rbc_events(body)
        ev, = evs
        self.assertEqual(ev.cash_in_lieu_parts,
                         [('2026-06-20', 0.25, 6.0), ('2026-07-01', 0.2, 5.0)])
        self.assertIn("the cash in lieu of 5.00 paid on 2026-07-01 follows "
                      "the reversal of 2026-06-25 of the 12.00 paid on "
                      "2026-06-18, and the books do not show it replaces "
                      "that payment", ' '.join(err.split()))

    @rule(R.CA_CORP_05)
    def test_questrade_one_account_paid_on_two_days(self):
        body = Q_BUY + q_dis() + q_cil() + q_cil(day='2026-06-29',
                                                 net='13.00')
        (evs, _err) = qt_events(body)
        ev, = evs
        self.assertAlmostEqual(ev.cash_in_lieu, 25.0)
        self.assertAlmostEqual(ev.fraction_in_lieu, 1.0)
        self.assertEqual(ev.cash_in_lieu_date, CIL_DAY)
        self.assertEqual(ev.cash_in_lieu_parts,
                         [(CIL_DAY, 0.5, 12.0), ('2026-06-29', 0.5, 13.0)])
        rows, _ = s861(ev)
        self.assertEqual(_sales(rows),
                         [(CIL_DAY, 'ZZS.TO', -0.5, 12.0),
                          ('2026-06-29', 'ZZS.TO', -0.5, 13.0)])
        # Each sale replaces the parser's own pair of its day.
        from taxjson.lib.corp_actions import drop_broker_cil_pairs
        (txs, _e) = qt_parse(body)
        for t in txs + rows:
            t['account'] = 'margin'     # the stage's account label
        merged, note = _quiet(drop_broker_cil_pairs, txs + rows)
        self.assertEqual(note.count('replaces the broker'), 2, note)
        left = sorted((t['date'], t['quantity'], t['net_amount'])
                      for t in merged if t.get('symbol') == 'ZZS.TO'
                      and t['action'] == 'BUYSELL' and t['quantity'] < 0)
        self.assertEqual(left, [(CIL_DAY, -0.5, 12.0),
                                ('2026-06-29', -0.5, 13.0)])
        self.assertFalse(any(t.get('type') == 'cil_takeover'
                             for t in merged))

    @rule(R.CA_CORP_05)
    def test_questrade_a_payment_made_again_keeps_the_reversed_ones_day(self):
        for name, later, cash in self.REVERSALS[:3]:
            body = Q_BUY + q_dis() + q_cil() + ''.join(
                q_cil(day=d, net=v) for d, v in later)
            (evs, _err) = qt_events(body)
            ev, = evs
            self.assertAlmostEqual(ev.cash_in_lieu, cash, msg=name)
            self.assertEqual((ev.cash_in_lieu_date, ev.cash_in_lieu_parts),
                             (CIL_DAY, []), name)
            self.assertEqual(_sales(s861(ev)[0]),
                             [(CIL_DAY, 'ZZS.TO', -0.5, cash)], name)

    def test_the_date_is_the_first_parts_day(self):
        # A copy paid before the event (its sale is never before it) or
        # undated, beside one paid later: `cash_in_lieu_date` is the
        # first part's day, as docs/commands.md says.
        for first in ('2026-12-18', ''):
            a, b = self._copies()
            a.cash_in_lieu_date = first
            for order in ((a, b), (b, a)):
                (ev,), _err = self._combine(order)
                self.assertEqual(ev.cash_in_lieu_parts,
                                 [('2026-12-20', 0.5, 5.0),
                                  (JAN_DAY, 0.5, 5.0)], first)
                self.assertEqual(ev.cash_in_lieu_date, '2026-12-20', first)

    def test_spinoffs_json_keeps_the_line_and_adds_the_parts(self):
        # `--json` is the contract: `cash_in_lieu` is the line it was
        # (the total and the first day); the days are a key of their own.
        from taxjson.lib import corp_views as V
        a, b = self._copies()
        (ev,), _err = self._combine((a, b))
        line = V._event_cil_line(ev, ev.fraction_in_lieu, ev.cash_in_lieu,
                                 [{'action': 'BUYSELL', 'quantity': -0.5}])
        self.assertEqual(line, f'1 of the 11 new shares, paid in cash: '
                               f'10.00 CAD on {DEC_DAY} (booked as their '
                               f'sale)')
        parts = V._event_cil_parts(ev)
        self.assertEqual(parts, [
            {'date': DEC_DAY, 'amount': 5.0, 'currency': 'CAD'},
            {'date': JAN_DAY, 'amount': 5.0, 'currency': 'CAD'}])
        self.assertEqual(V._parts_text({'cash_in_lieu_parts': parts}),
                         f'; by day: 5.00 on {DEC_DAY}, 5.00 on {JAN_DAY}')
        one, _ = self._copies()
        self.assertEqual(V._event_cil_parts(one), [])
        self.assertEqual(V._parts_text({'cash_in_lieu_parts': []}), '')

    def test_the_views_name_each_days_cash(self):
        from taxjson.lib.corp_views import cash_in_lieu_line
        a, b = self._copies()
        (ev,), _err = self._combine((a, b))
        self.assertEqual(
            cash_in_lieu_line(ev.qty_received, ev.fraction_in_lieu,
                              ev.cash_in_lieu, 'CAD', ev.cash_in_lieu_date,
                              parts=ev.cash_in_lieu_parts),
            f'1 of the 11 new shares, paid in cash: 10.00 CAD (5.00 on '
            f'{DEC_DAY}, 5.00 on {JAN_DAY})')
        self.assertEqual(
            cash_in_lieu_line(10.0, 0.5, 12.0, 'CAD', CIL_DAY),
            f'0.5 of the 10.5 new shares, paid in cash: 12.00 CAD on '
            f'{CIL_DAY}')


@rule(R.US_CORP_07)
class TestUs355Unchanged(unittest.TestCase):
    """The US §355 path keeps the fraction's basis in the parent and books
    no sale of it (docs/limits.md), whatever the event's cash in lieu."""

    def _rows(self, ev):
        from taxjson.lib.corp_actions import resolve_event
        return _quiet(resolve_event, ev, 'tax_free_355', 'usa',
                      {'allocated_acb': ALLOC})

    def test_no_sale_of_the_fraction(self):
        plain, err = self._rows(_event(currency='USD', target_currency='USD'))
        self.assertNotIn('UNBOOKED', err)
        cil = dict(cash_in_lieu=12.0, cash_in_lieu_currency='USD',
                   fraction_in_lieu=0.5, cash_in_lieu_date=CIL_DAY)
        # Questrade (its parser books the cash) and RBC (nobody does):
        # the same rows; RBC's unbooked cash is said.
        for booked in (True, False):
            rows, err = self._rows(_event(currency='USD',
                                          target_currency='USD',
                                          cil_broker_booked=booked, **cil))
            self.assertEqual(rows, plain)
            self.assertEqual('UNBOOKED' in err, not booked, err)
        self.assertEqual([x['action'] for x in plain], ['BUYSELL', 'ADJUST'])
        self.assertAlmostEqual(plain[0]['quantity'], 10.0)


if __name__ == '__main__':
    unittest.main()

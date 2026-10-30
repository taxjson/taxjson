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

REPO = Path(__file__).resolve().parent.parent
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
    from taxjson.lib.corp_actions import parse_rbc_corporate_actions
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
    res = _cli(root, 'elect', 'margin', '--set', f'{eid}={election}',
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

    @rule("CA-CORP-05")
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

    @rule("CA-CORP-05")
    def test_end_to_end_one_sale_at_its_allocated_cost(self):
        with tempfile.TemporaryDirectory() as td:
            res, gains, base = _project_run(
                td, 'rbc_2026.csv', RH + R_BUY + r_dis() + r_cil())
            self.assertNotIn('cash-in-lieu row with no reorganization',
                             res.stdout + res.stderr)
            self.assert_fraction_sold_once(gains, base)

    @rule("CA-CORP-05")
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

    @rule("CA-CORP-05")
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

    @rule("CA-CORP-07")
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

    @rule("CA-CORP-05")
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

    @rule("CA-CORP-05")
    def test_end_to_end_fraction_sold_once(self):
        for cil_day in (SPIN_DAY, CIL_DAY):
            with self.subTest(cil_day=cil_day), \
                    tempfile.TemporaryDirectory() as td:
                _res, gains, base = _project_run(
                    td, 'qt_2026.csv', QH + Q_BUY + q_dis()
                    + q_cil(day=cil_day))
                self.assert_fraction_sold_once(gains, base)

    @rule("CA-CORP-05")
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

    @rule("CA-CORP-07")
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

    @rule("CA-CORP-07")
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

    @rule("CA-CORP-06")
    def test_no_fraction_books_as_before(self):
        rows, _ = s861(_event())
        self.assertEqual(
            [(x['action'], x['symbol'], x.get('quantity'), x['net_amount'])
             for x in rows],
            [('BUYSELL', 'ZZS.TO', 10.0, ALLOC),
             ('ADJUST', 'ZZP.TO', None, -ALLOC)])
        self.assertFalse(any('corp_cash' in x for x in rows))

    @rule("CA-CORP-05")
    def test_ib_fractional_delivery_is_not_sold(self):
        # IB delivers the fraction and its own cash-in-lieu row sells it
        # (the IB parser): the election books no second sale.
        rows, _ = s861(_event(qty_received=10.5, ratio_new=10.5,
                              fractional_delivery=True))
        self.assertEqual(_rows(rows, 'BUYSELL', -1), [])
        buy, = _rows(rows, 'BUYSELL', +1)
        self.assertAlmostEqual(buy['quantity'], 10.5)
        self.assertAlmostEqual(buy['net_amount'], ALLOC)

    @rule("CA-CORP-05")
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

    @rule("CA-CORP-05")
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


@rule("US-CORP-07")
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

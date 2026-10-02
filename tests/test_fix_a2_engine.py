"""Regression tests for the re-audit-2 engine fixes (fixa2/engine).

Synthetic data only; account labels are fake.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from tax_rules import rule


def _row(d, q, px, acct='margin', sym='XYZ.TO', t='10:00:00', mult=None,
         **kw):
    m = mult if mult is not None else (100 if len(sym) > 12 else 1)
    tx = TaxTransaction(action='BUYSELL', date=d, date_settle=d, time=t,
                        symbol=sym, quantity=float(q), price=float(px),
                        net_amount=round(abs(q * px * m), 2),
                        currency='CAD', account=acct, **kw)
    return tx


def _ca(tax, shel=None, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return CanadaTaxRules().compute_gains(
            tax, sheltered_transactions=shel, **kw)


def _sales(res, sym='XYZ.TO'):
    return [(e['date'], round(e['disallowed_amount'], 2),
             round(e.get('permanently_disallowed', 0.0), 2))
            for e in res['transactions']
            if 'proceeds' in e and e['symbol'] == sym]


class TestCaClaimedUnitsLeaveTheBalance(unittest.TestCase):
    """CA-SL-08: each held replacement unit backs ONE denial."""

    @rule("CA-SL-08")
    def test_registered_units_claimed_on_a_pre_sale_row(self):
        # A2-0012: the TFSA buys 50 before both losses and sells 20: it
        # holds 30 at day 30, so 30 units are denied in total (300).
        tax = [_row('2026-01-05', 200, 50), _row('2026-03-05', -100, 40),
               _row('2026-03-06', -100, 40)]
        tfsa = [_row('2026-03-02', 50, 41, 'tfsa'),
                _row('2026-03-03', -20, 41, 'tfsa')]
        res = _ca(tax, tfsa)
        self.assertEqual(_sales(res), [('2026-03-05', 300.0, 300.0),
                                       ('2026-03-06', 0.0, 0.0)])

    @rule("CA-SL-08", "CA-SL-05")
    def test_one_held_call_backs_one_denial(self):
        # A2-0057: a call bought, sold and bought again in the window;
        # one contract is held at day 30 -> one 100-share loss denied.
        C = 'XYZ261218C00020000.TO'
        base = [_row('2025-01-06', 200, 50), _row('2025-03-03', -100, 40),
                _row('2025-03-05', -100, 40)]
        rep = [_row('2025-03-06', 1, 3, 'rrsp', C),
               _row('2025-03-07', -1, 3, 'rrsp', C),
               _row('2025-03-10', 1, 3, 'rrsp', C)]
        res = _ca(base, rep)
        self.assertEqual(_sales(res), [('2025-03-03', 1000.0, 1000.0),
                                       ('2025-03-05', 0.0, 0.0)])
        self.assertAlmostEqual(res['summary']['total_disallowed'], 1000.0)

    @rule("CA-SL-08", "CA-SL-05")
    def test_call_lots_bought_after_both_losses(self):
        # A2-0198: lots X and Y bought after both losses, one sold:
        # one contract (100 units) is held, so only loss 1 is denied —
        # the same answer as with shares.
        C = 'XYZ251219C00010000.TO'
        base = [_row('2025-01-06', 200, 20), _row('2025-03-03', -100, 10),
                _row('2025-03-05', -100, 10)]
        for sym, n, px in ((C, 1, 3), ('XYZ.TO', 100, 10)):
            rep = [_row('2025-03-10', n, px, sym=sym),
                   _row('2025-03-11', n, px, sym=sym),
                   _row('2025-03-13', -n, px, sym=sym)]
            res = _ca(base + rep)
            got = _sales(res)[:2]
            self.assertEqual(got, [('2025-03-03', 1000.0, 0.0),
                                   ('2025-03-05', 0.0, 0.0)], sym)


class TestCaSameMomentOrder(unittest.TestCase):
    """CA-DATE-14 / CA-SL-10: same-moment rows follow the export order,
    never the rows' content hash."""

    @rule("CA-SL-10", "CA-DATE-14")
    def test_rebuy_listed_after_the_sale_is_post_loss(self):
        # A2-0058: midnight-stamped sale + rebuy (Questrade): the rebuy
        # is the first replacement, so the denial defers into the taxable
        # ACB; the later TFSA buy takes nothing.
        t0 = '00:00:00'
        tax = [_row('2025-01-02', 100, 50, t=t0),
               _row('2025-03-03', -100, 40, t=t0),
               _row('2025-03-03', 100, 40, t=t0)]
        tfsa = [_row('2025-03-10', 100, 40, 'tfsa', t=t0)]
        res = _ca(tax, tfsa)
        self.assertEqual(_sales(res), [('2025-03-03', 1000.0, 0.0)])

    @rule("CA-SL-08", "CA-DATE-14")
    def test_claim_subtraction_at_the_same_moment(self):
        # A2-0193: a rebuy listed after a same-moment loss L2 and claimed
        # by L1 cannot back L2 too: same result as one second later.
        def book(t_rebuy):
            return [_row('2025-01-06', 200, 50), _row('2025-03-03', -100, 40),
                    _row('2025-03-05', -100, 40, t='00:00:00'),
                    _row('2025-03-05', 100, 40, t=t_rebuy),
                    _row('2025-03-06', 100, 40),
                    _row('2025-03-07', -100, 41)]
        same, later = _ca(book('00:00:00')), _ca(book('00:00:01'))
        self.assertEqual(_sales(same), _sales(later))
        self.assertEqual(_sales(same)[1], ('2025-03-05', 0.0, 0.0))

    @rule("CA-SL-08", "CA-DATE-14")
    def test_same_moment_losses_claim_in_export_order(self):
        # A2-0059 / A2-0551: two same-second fills share one rebuy; the
        # first LISTED claims it, whatever the prices (row hashes).
        for pa in ('40.00', '40.01', '40.02', '40.03'):
            a = _row('2025-03-03', -100, float(pa))
            b = _row('2025-03-03', -100, 30)
            book = [_row('2025-01-02', 100, 50), _row('2025-01-03', 100, 45),
                    a, b, _row('2025-03-10', 100, 30)]
            res = _ca(book)
            by = {e['tx_id'] if 'tx_id' in e else None: e
                  for e in res['transactions'] if 'proceeds' in e}
            den = [round(e['disallowed_amount'], 2)
                   for e in res['transactions'] if 'proceeds' in e]
            self.assertGreater(den[0], 0.0, pa)
            self.assertEqual(den[1], 0.0, pa)
            # Listed the other way round, the other fill claims.
            res2 = _ca([book[0], book[1], b, a, book[4]])
            den2 = {round(e['proceeds'], 2): round(e['disallowed_amount'], 2)
                    for e in res2['transactions'] if 'proceeds' in e}
            self.assertGreater(den2[3000.0], 0.0, pa)
            self.assertEqual(den2[round(100 * float(pa), 2)], 0.0, pa)

    @rule("CA-SL-10", "CA-DATE-14")
    def test_same_moment_share_and_call_follow_export_order(self):
        # A2-0192 / A2-0961: one account buys 100 shares and 1 call at the
        # same moment after a loss; the first LISTED takes the bump, and a
        # one-cent change on the call never moves it.
        C = 'XYZ251219C00045000.TO'
        for cpx in (3.00, 3.01, 3.02, 3.03, 3.04):
            for call_first in (False, True):
                sh = _row('2025-03-10', 100, 40)
                cl = _row('2025-03-10', 1, cpx, sym=C)
                rows = [cl, sh] if call_first else [sh, cl]
                book = [_row('2025-01-02', 100, 50),
                        _row('2025-03-03', -100, 40)] + rows
                res = _ca(book)
                inv = {p['symbol']: p['total_cost']
                       for p in res.get('inventory', [])}
                if call_first:
                    self.assertAlmostEqual(inv[C], 100 * cpx + 1000.0,
                                           places=2)
                    self.assertAlmostEqual(inv['XYZ.TO'], 4000.0, places=2)
                else:
                    self.assertAlmostEqual(inv['XYZ.TO'], 5000.0, places=2)
                    self.assertAlmostEqual(inv[C], 100 * cpx, places=2)

    @rule("CA-SL-10", "CA-DATE-14")
    def test_same_moment_accounts_follow_input_order(self):
        # A2-0965: two taxable accounts rebuy at one moment; the trigger
        # cited is the account listed first (taxjson.toml order), not the
        # alphabetically first label, and the window trace lists rows in
        # processing order.
        for first, second in (('zeta', 'alpha'), ('alpha', 'zeta')):
            book = [_row('2025-01-02', 100, 10, first),
                    _row('2025-03-03', -100, 8, first),
                    _row('2025-03-10', 100, 8, first),
                    _row('2025-03-10', 100, 8, second)]
            # Input order: the first account's rows, then the second's.
            res = _ca(book, trace=True)
            loss = next(e for e in res['transactions']
                        if 'proceeds' in e and e['date'] == '2025-03-03')
            self.assertEqual((loss.get('wash_trigger') or {})
                             .get('trigger_account'), first)


class TestCaPostLossBumpPlacement(unittest.TestCase):

    @rule("CA-SL-09", "CA-DATE-14")
    def test_same_moment_sale_after_the_replacement_sees_the_bump(self):
        # A2-0555: buy 200 then sell 100 at one midnight stamp inside the
        # window: the sale uses the bumped ACB (same as one second later).
        def book(t_sale):
            return [_row('2025-01-02', 100, 50),
                    _row('2025-12-01', -100, 40),
                    _row('2025-12-15', 200, 20, t='00:00:00'),
                    _row('2025-12-15', -100, 45, t=t_sale),
                    _row('2026-03-02', -100, 40)]
        def rows(res):
            return [(e['date'], round(e['cost'], 2), round(e['gain'], 2))
                    for e in res['transactions'] if 'proceeds' in e]
        self.assertEqual(rows(_ca(book('00:00:00'))),
                         rows(_ca(book('00:00:01'))))


class TestCaTaintedLossNeverFeedsTheSolver(unittest.TestCase):

    @rule("CA-ACB-11")
    def test_phantom_loss_with_a_clean_rebuy_held_at_day_30(self):
        # A2-0061: the clean rebuy is still held at day 30, so removing
        # the taint gate would deny the phantom loss and bump the rebuy.
        def t(a, d, q, net, i):
            return TaxTransaction(action=a, date=d, date_settle=d,
                                  time='10:00:00', symbol='PHN.TO',
                                  quantity=q,
                                  price=abs(net / q) if net else 0.0,
                                  net_amount=net, currency='CAD',
                                  account='margin', id=i)
        book = [t('OPENING_BALANCE', '2025-01-01', 50, 0.0, 'ob'),
                t('BUYSELL', '2025-02-03', 50, 5000.0, 'b1'),
                t('BUYSELL', '2025-03-03', -100, 4000.0, 's1'),
                t('BUYSELL', '2025-03-10', 100, 4000.0, 'b2'),
                t('BUYSELL', '2025-06-02', -100, 4000.0, 's2')]
        res = _ca(book)
        sales = [e for e in res['transactions'] if 'proceeds' in e]
        self.assertTrue(sales[0].get('tainted'))
        self.assertEqual(sales[0]['disallowed_amount'], 0.0)
        self.assertAlmostEqual(sales[1]['cost'], 4000.0)
        self.assertFalse(res.get('wash_sales'))


class TestCaCoinDustReplacement(unittest.TestCase):

    @rule("CA-SL-13")
    def test_sub_millionth_coin_rebuy_backs_its_share(self):
        # A2-0552: the solver's zero is the pool's (relative for a coin):
        # a 0.0000009 BTC rebuy denies 4.5% of the loss at any scale.
        def tx(d, q, p):
            return TaxTransaction(action='BUYSELL', date=d, date_settle=d,
                                  time='10:00:00', symbol='BTC', quantity=q,
                                  price=p, net_amount=abs(q * p),
                                  currency='CAD', account='coinbase')
        for k in (1000.0, 1.0):
            rows = [tx('2025-01-06', 0.00002 * k, 150000),
                    tx('2025-03-03', -0.00002 * k, 100000),
                    tx('2025-03-10', 0.0000009 * k, 100000)]
            e = [e for e in _ca(rows)['transactions'] if 'proceeds' in e][0]
            self.assertAlmostEqual(e['disallowed_amount'] / -e['raw_gain'],
                                   0.045, places=6, msg=str(k))


def _gen(R, rows, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return R().compute_gains(list(rows), **kw)


def _t(d, sym, q, px, cur='USD', mult=0.0, acct='margin'):
    m = mult or (100 if len(sym) > 12 and not sym.startswith('F:') else 1)
    return TaxTransaction(action='BUYSELL', date=d, date_settle=d,
                          time='10:00:00', symbol=sym, quantity=float(q),
                          price=float(px), net_amount=round(abs(q * px * m), 2),
                          currency=cur, account=acct, multiplier=mult)


class TestFuturesOptionIsFlaggedNotSized(unittest.TestCase):
    """A2-0014 / A2-0056: a call on the loss's own futures contract (same
    spelling) is flagged, never enforced as a 100-unit call."""

    def _book(self, n):
        return [_t('2025-09-02', 'F:CLG6.US', n, 6000),
                _t('2025-10-01', 'F:CLG6.US', -n, 5000),
                _t('2025-10-06', 'F:CLG6260114C00060000.US', 1, 3)]

    @rule("CA-SL-15")
    def test_canada(self):
        from taxjson.lib.core import CanadaTaxRules as R
        for n in (1, 5):
            res = _gen(R, self._book(n))
            self.assertEqual(res['summary']['total_disallowed'], 0)
            self.assertEqual([w['rule'] for w in
                              res.get('option_replacement_warnings') or []],
                             ['futures_option_vs_loss'])

    @rule("US-WASH-15")
    def test_usa(self):
        from taxjson.lib.core import USATaxRules as R
        res = _gen(R, self._book(1))
        self.assertEqual([w['rule'] for w in
                          res.get('option_replacement_warnings') or []],
                         ['futures_option_vs_loss'])


class TestClassShareRootCall(unittest.TestCase):
    """A2-0015 / A2-0016 / A2-0207: a call booked under the option root
    that drops the share class is a call on that class line."""

    @rule("CA-SL-05")
    def test_canada_rci_call_denies_the_rci_b_loss(self):
        for root, stock in (('RCI', 'RCI.B.TO'), ('BCE', 'BCE.TO')):
            rows = [_t('2025-01-02', stock, 100, 50, 'CAD'),
                    _t('2025-05-01', stock, -100, 40, 'CAD'),
                    _t('2025-05-06', f'{root}251219C00045000.TO', 1, 2,
                       'CAD')]
            res = _gen(CanadaTaxRules, rows)
            self.assertAlmostEqual(res['summary']['total_disallowed'],
                                   1000.0, msg=root)

    @rule("CA-SL-05")
    def test_canada_ambiguous_class_root_is_not_resolved(self):
        # RCI names both RCI.A.TO and RCI.B.TO: no guess.
        rows = [_t('2025-01-02', 'RCI.A.TO', 10, 50, 'CAD'),
                _t('2025-01-02', 'RCI.B.TO', 100, 50, 'CAD'),
                _t('2025-05-01', 'RCI.B.TO', -100, 40, 'CAD'),
                _t('2025-05-06', 'RCI251219C00045000.TO', 1, 2, 'CAD')]
        res = _gen(CanadaTaxRules, rows)
        self.assertEqual(res['summary']['total_disallowed'], 0)

    @rule("US-WASH-12")
    def test_usa_brkb_call_warns_on_the_brk_b_loss(self):
        from taxjson.lib.core import USATaxRules
        rows = [_t('2026-01-05', 'BRK.B.US', 100, 50),
                _t('2026-03-02', 'BRK.B.US', -100, 40),
                _t('2026-03-10', 'BRKB270115C00046000.US', 1, 2)]
        res = _gen(USATaxRules, rows)
        ws = res.get('option_replacement_warnings') or []
        self.assertEqual([w['rule'] for w in ws], ['call_vs_share_loss'])
        self.assertEqual(res['summary']['total_disallowed'], 0)


class TestDeclaredContractSize(unittest.TestCase):
    """A2-0049 / A2-0957: a mini call (x10) replaces 10 shares."""

    def _rows(self):
        C = 'AAPL250620C00150000.US'
        return [_t('2025-01-06', 'AAPL.US', 1000, 20),
                _t('2025-03-03', 'AAPL.US', -1000, 10),
                _t('2025-03-20', C, 1, 3, mult=10.0)]

    @rule("CA-SL-05")
    def test_canada_denies_ten_shares(self):
        res = _gen(CanadaTaxRules, self._rows())
        self.assertAlmostEqual(res['summary']['total_disallowed'], 100.0)

    @rule("US-WASH-12")
    def test_usa_warning_sizes_ten_shares(self):
        from taxjson.lib.core import USATaxRules
        res = _gen(USATaxRules, self._rows())
        w = (res.get('option_replacement_warnings') or [])[0]
        self.assertEqual((w['covered_shares'], w['option_qty'],
                          w['at_risk_amount']), (10.0, 1.0, -100.0))


class TestRawPassForeignCurrencyAdjust(unittest.TestCase):
    """A2-0055 / A2-0191 / A2-0204: a cost adjustment in another currency
    than its pool no longer stops `taxjson run` at the native raw pass."""

    def _write(self, td, txs, rates):
        import json
        from pathlib import Path
        raw = Path(td) / 'm_raw.json'
        raw.write_text(json.dumps({'transactions': txs}))
        rp = Path(td) / 'to_base.csv'
        rp.write_text(''.join(f'{d} 12:00:00 {c} CAD {r}\n'
                              for d, c, r in rates))
        return raw, rp

    def _tx(self, action, sym, cur, net, d='2025-07-15'):
        return {'action': action, 'date': d, 'time': '09:30:00',
                'symbol': sym, 'currency': cur, 'quantity': 0,
                'net_amount': net, 'account': 'margin'}

    def test_usd_roc_on_a_cad_listing_is_restated(self):
        import json
        import tempfile
        from taxjson.bin.taxjson_run import (_raw_align_adjust_currency,
                                             _raw_mixed_currency_symbols)
        with tempfile.TemporaryDirectory() as td:
            buy = dict(self._tx('BUYSELL', 'GLDX.TO', 'CAD', 10000.0,
                                '2024-03-04'), quantity=1000)
            ubuy = dict(self._tx('BUYSELL', 'QZU.U.TO', 'USD', 1000.0,
                                 '2024-03-04'), quantity=100)
            raw, rp = self._write(td, [
                buy, self._tx('ADJUST', 'GLDX.TO', 'USD', -2000.0),
                ubuy, self._tx('ADJUST', 'QZU.U.TO', 'CAD', -50.0,
                               '2025-12-31')],
                [('2025-07-15', 'USD', '1.371'),
                 ('2025-12-31', 'USD', '1.25')])
            notes = _raw_align_adjust_currency(raw, rp, 'CAD')
            self.assertEqual(len(notes), 2)
            rows = json.loads(raw.read_text())['transactions']
            self.assertEqual((rows[1]['currency'], rows[1]['net_amount']),
                             ('CAD', -2742.0))
            self.assertEqual(rows[3]['currency'], 'USD')
            self.assertAlmostEqual(rows[3]['net_amount'], -40.0)
            self.assertEqual(_raw_mixed_currency_symbols(raw), [])
            # Idempotent.
            self.assertEqual(_raw_align_adjust_currency(raw, rp, 'CAD'), [])

    def test_no_rate_leaves_it_for_the_detector(self):
        import tempfile
        from taxjson.bin.taxjson_run import (_raw_align_adjust_currency,
                                             _raw_mixed_currency_symbols)
        with tempfile.TemporaryDirectory() as td:
            buy = dict(self._tx('BUYSELL', 'GLDX.TO', 'CAD', 10000.0,
                                '2024-03-04'), quantity=1000)
            raw, rp = self._write(td, [
                buy, self._tx('ADJUST', 'GLDX.TO', 'USD', -2000.0)], [])
            self.assertEqual(_raw_align_adjust_currency(raw, rp, 'CAD'), [])
            # The raw view is skipped with a note, never a dead run.
            self.assertEqual(_raw_mixed_currency_symbols(raw), ['GLDX.TO'])

    def test_engine_error_names_the_row_and_the_fix(self):
        rows = [_row('2024-03-04', 1000, 10),
                TaxTransaction(action='ADJUST', date='2025-07-15',
                               date_settle='2025-07-15', time='09:30:00',
                               symbol='XYZ.TO', quantity=0, price=0,
                               net_amount=-2000.0, currency='USD',
                               account='margin')]
        with self.assertRaises(ValueError) as cm:
            _ca(rows)
        msg = str(cm.exception)
        self.assertIn('ADJUST', msg)
        self.assertIn("pool's currency", msg)
        self.assertNotIn('taxjson_convert_currency', msg)


_TRUST_ROWS = [
    {'action': 'BUYSELL', 'date': '2025-02-03', 'date_settle': '2025-02-04',
     'time': '10:00:00', 'symbol': 'XYZ.UN.TO', 'quantity': 100.0,
     'price': 10.0, 'net_amount': 1000.0, 'currency': 'CAD',
     'account': 'margin', 'id': 'b1'},
    {'action': 'ADJUST', 'type': 'roc', 'date': '2026-01-08',
     'date_settle': '2026-01-08', 'time': '09:30:00',
     'symbol': 'XYZ.UN.TO', 'quantity': 0.0, 'price': 0.0,
     'net_amount': -20.0, 'currency': 'CAD', 'account': 'margin',
     'record_date': '2025-12-30', 'id': 'r1'},
]


class TestAsOfTrustRocRecordDate(unittest.TestCase):
    """A2-0554 / A2-0960 / A2-0202: an as-of cutoff judges a trust ROC by
    the record date the engine books it on (CA-INC-DATE-ROC-TRUST)."""

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_gains_as_of_keeps_a_january_paid_roc(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'm.json'
            f.write_text(json.dumps({'transactions': _TRUST_ROWS}))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_gains',
                 '--country', 'canada', '--option-premium-timing', 'close',
                 '--as-of', '2025-12-31', str(f)],
                capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            inv = {h['symbol']: h for h in json.loads(r.stdout)['inventory']}
            self.assertAlmostEqual(inv['XYZ.UN.TO']['total_cost'], 980.0)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_close_year_snapshot_keeps_a_january_paid_roc(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.lib import handoff
        seen = {}

        def fake_run_gains(args, out):
            src = Path(args[-1])
            seen[src.name] = json.loads(src.read_text())['transactions']
            out.write_text(json.dumps({'transactions': [], 'inventory': [],
                                       'wash_sales': []}))
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / 'margin_base.json').write_text(
                json.dumps({'transactions': _TRUST_ROWS}))
            cfg = {'settings': {'country': 'canada', 'year': 2025},
                   'accounts': {'margin': {'type': 'taxable'}}}
            handoff.snapshot(cache, cfg, '2025-12-31', fake_run_gains, [])
        kept = seen['equity_asof.json']
        self.assertIn('r1', [r.get('id') for r in kept])


class TestSplitGainsSameStampSplitFirst(unittest.TestCase):
    """A2-0013: the blended split walks put a same-stamp SPLIT before the
    trades (CA-DATE-14), as the engine does."""

    ROWS = [
        {'action': 'BUYSELL', 'date': '2025-02-03', 'date_settle': '2025-02-04',
         'time': '09:30:00', 'symbol': 'XYZ.TO', 'quantity': 100.0,
         'net_amount': 1000.0, 'account': 'margin'},
        {'action': 'BUYSELL', 'date': '2025-03-03', 'date_settle': '2025-03-03',
         'time': '09:30:00', 'symbol': 'XYZ.TO', 'quantity': 50.0,
         'net_amount': 250.0, 'account': 'margin'},
        {'action': 'SPLIT', 'date': '2025-03-03', 'date_settle': '2025-03-03',
         'time': '09:30:00', 'symbol': 'XYZ.TO', 'symbol_new': 'XYZ.TO',
         'quantity': 2.0, 'net_amount': 0.0, 'account': 'margin'},
    ]

    @rule("CA-DATE-14")
    def test_apportioned_quantity_matches_the_engine(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        from taxjson.bin.taxjson_split_gains import split_for_account
        self.assertEqual(balance_on(self.ROWS, 'XYZ.TO', '9999-12-31'), 250.0)
        combined = {'transactions': [], 'wash_sales': [],
                    'summary': {'tax_date_basis': 'settle'},
                    'inventory': [{'symbol': 'XYZ.TO', 'qty': 250.0,
                                   'total_cost': 1250.0,
                                   'position_start_date': '2025-02-03'}]}
        out = split_for_account(combined, 'margin', self.ROWS)
        inv = out['inventory'][0]
        self.assertEqual((inv['qty'], inv['total_cost']), (250.0, 1250.0))

    @rule("CA-DATE-14")
    def test_position_start_after_a_same_stamp_split(self):
        # Sell-then-rebuy book: a same-stamp split must not reorder the
        # walk (the account's SINCE follows the engine's order).
        from taxjson.bin.taxjson_split_gains import _position_starts
        rows = [dict(self.ROWS[0]),
                {'action': 'BUYSELL', 'date': '2025-03-03',
                 'date_settle': '2025-03-03', 'time': '09:30:00',
                 'symbol': 'XYZ.TO', 'quantity': -200.0, 'account': 'm'},
                {'action': 'BUYSELL', 'date': '2025-06-02',
                 'date_settle': '2025-06-03', 'time': '09:30:00',
                 'symbol': 'XYZ.TO', 'quantity': 10.0, 'account': 'm'},
                dict(self.ROWS[2])]
        # The split (listed last) applies first: 100 -> 200, the sale
        # empties the position, the June buy opens a new one.
        self.assertEqual(_position_starts(rows, 'settle'),
                         {'XYZ.TO': '2025-06-02'})


class TestShelteredFlagRepeatable(unittest.TestCase):
    """A2-0194: taxjson-explain / taxjson-audit take --sheltered more than
    once, as taxjson-gains does; the first file is never dropped."""

    def _files(self, td):
        import json
        from pathlib import Path
        def w(name, rows):
            p = Path(td) / name
            p.write_text(json.dumps({'transactions': rows}))
            return str(p)
        def r(d, sym, q, net, acct):
            return {'action': 'BUYSELL', 'date': d, 'date_settle': d,
                    'time': '09:30:00', 'symbol': sym, 'quantity': q,
                    'net_amount': net, 'currency': 'CAD', 'account': acct}
        main = w('main.json', [r('2025-01-10', 'AAA.TO', 100, -10000.0, 'm'),
                               r('2025-03-03', 'AAA.TO', -100, 7000.0, 'm')])
        tfsa = w('tfsa.json', [r('2025-03-10', 'AAA.TO', 100, -7000.0, 't')])
        rrsp = w('rrsp.json', [r('2025-02-01', 'ZZZ.TO', 10, -100.0, 'r')])
        return main, tfsa, rrsp

    def _run(self, *argv):
        import subprocess
        import sys
        return subprocess.run([sys.executable, '-m', *argv],
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    @rule("CA-SL-03")
    def test_explain_keeps_both_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            main, tfsa, rrsp = self._files(td)
            r = self._run('taxjson.bin.taxjson_explain', '--country',
                          'canada', '--year', '2025',
                          '--option-premium-timing', 'close',
                          '--sheltered', tfsa, '--sheltered', rrsp, main)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('disallowed +$3,000.00', r.stdout + r.stderr)

    @rule("CA-SL-03")
    def test_audit_keeps_both_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            main, tfsa, rrsp = self._files(td)
            r = self._run('taxjson.bin.taxjson_audit', '--country',
                          'canada', '--year', '2025',
                          '--option-premium-timing', 'close',
                          '--base', main, '--sheltered', tfsa,
                          '--sheltered', rrsp, '--summary')
            self.assertEqual(r.returncode, 0, r.stderr)
            line = next(l for l in r.stdout.splitlines()
                        if 'total disallowed' in l)
            self.assertIn('3,000.00', line)


class TestStandaloneYearFlags(unittest.TestCase):
    """A2-0955: the standalone year flags refuse an implausible year
    (a typo such as 226 for 2026), like --year does."""

    def test_flags_use_the_tax_year_type(self):
        import subprocess
        import sys
        cases = [
            ('taxjson.bin.taxjson_gains', ['--country', 'canada',
                                           '--option-grant-since', '226']),
            ('taxjson.bin.taxjson_brokerage', ['--tax-year', '225']),
            ('taxjson.bin.taxjson_carryover', ['--project-year', '0']),
            ('taxjson.bin.taxjson_audit', ['--check-year', '99999']),
            ('taxjson.bin.taxjson_explain', ['--option-grant-since', '226']),
        ]
        for mod, argv in cases:
            r = subprocess.run([sys.executable, '-m', mod, *argv, 'x.json'],
                               capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, (mod, r.stderr[-300:]))
            self.assertIn('plausible tax year', r.stderr, mod)


class TestJsonSettleBeforeTrade(unittest.TestCase):
    """A2-0959: the JSON path refuses a settlement before its trade."""

    def _rows(self, settle):
        return [{'action': 'BUYSELL', 'date': '2025-01-06',
                 'date_settle': '2025-01-07', 'time': '10:00:00',
                 'symbol': 'XYZ.TO', 'quantity': 10, 'net_amount': 100.0,
                 'currency': 'CAD', 'account': 'm'},
                {'action': 'BUYSELL', 'date': '2025-12-15',
                 'date_settle': settle, 'time': '10:00:00',
                 'symbol': 'XYZ.TO', 'quantity': -10, 'net_amount': 90.0,
                 'currency': 'CAD', 'account': 'm'}]

    @rule("CA-DATE-03")
    def test_loader_refuses(self):
        from taxjson.lib.core import coerce_transaction_row
        rows = self._rows('2025-12-12')
        coerce_transaction_row(rows[0], 0, 'x')
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row(rows[1], 1, 'x')
        self.assertIn('before the trade date', str(cm.exception))

    @rule("US-DATE-04")
    def test_loader_refuses_us(self):
        from taxjson.lib.core import coerce_transaction_row
        with self.assertRaises(ValueError):
            coerce_transaction_row(self._rows('2025-12-12')[1], 1, 'x')

    def test_validate_flags_early_and_late_settles(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        for settle, want, rc in (('2025-12-12', 'before the trade', 1),
                                 ('2026-12-16', 'more than a month', None)):
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / 'm.json'
                f.write_text(json.dumps({'transactions': self._rows(settle)}))
                r = subprocess.run([sys.executable, '-m',
                                    'taxjson.bin.taxjson_validate',
                                    '--warnings', str(f)],
                                   capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
                self.assertIn(want, r.stdout + r.stderr, settle)
                if rc is not None:
                    self.assertNotEqual(r.returncode, 0)


class TestLastAcquisitionSettleDate(unittest.TestCase):
    """A2-0958: Canada's inventory names the latest acquisition's SETTLE
    date, which harvest's TX_ADD measures the s.54 window from."""

    @rule("CA-SL-01")
    def test_canada_inventory_and_harvest(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_harvest import load_inventory_agg
        t = TaxTransaction(action='BUYSELL', date='2025-06-20',
                           date_settle='2025-06-23', time='10:00:00',
                           symbol='XYZ.US', quantity=10.0, price=10.0,
                           net_amount=100.0, currency='CAD', account='m')
        inv = _ca([t])['inventory'][0]
        self.assertEqual((inv['last_acq_date'], inv['last_acq_settle']),
                         ('2025-06-20', '2025-06-23'))
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'g.json'
            f.write_text(json.dumps({'transactions': [], 'inventory': [inv]}))
            agg = load_inventory_agg([f], 'TX_ADD')
        self.assertEqual(agg['XYZ.US']['last_add'], '2025-06-23')


def _reuse_book():
    def T(action, date, symbol, q, net, new='', settle=None):
        return TaxTransaction(action=action, date=date, symbol=symbol,
                              quantity=q, net_amount=net, currency='USD',
                              time='10:00:00', date_settle=settle or date,
                              account='m', symbol_new=new)
    return [T('BUYSELL', '2024-01-10', 'OLD.US', 100, 1000),
            T('SPLIT', '2024-06-01', 'OLD.US', 1.0, 0, 'NEW.US'),
            T('BUYSELL', '2025-03-03', 'NEW.US', -100, 500),
            T('BUYSELL', '2025-03-10', 'OLD.US', 100, 2000)]


class TestTickerReusedAfterRename(unittest.TestCase):
    """A2-0197: a ticker trading after its rename is flagged (it may be
    another company); the identical-property class is unchanged."""

    def _attn(self, R):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            R().compute_gains(_reuse_book())
        return [l for l in err.getvalue().splitlines()
                if 'ATTENTION' in l and 'after its rename' in l]

    @rule("CA-ACB-04")
    def test_canada_flags_the_reuse(self):
        lines = self._attn(CanadaTaxRules)
        self.assertEqual(len(lines), 1)
        self.assertIn('OLD.US trades on 2025-03-10', lines[0])

    @rule("US-BASIS-06")
    def test_usa_flags_the_reuse(self):
        from taxjson.lib.core import USATaxRules
        lines = self._attn(USATaxRules)
        self.assertEqual(len(lines), 1)
        self.assertIn('OLD.US trades on 2025-03-10', lines[0])


if __name__ == '__main__':
    unittest.main()

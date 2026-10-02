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


if __name__ == '__main__':
    unittest.main()

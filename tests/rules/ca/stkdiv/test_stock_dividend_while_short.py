"""Canada: a stock dividend posted while the position is short raises the
short (the shares owed grow, the proceeds stay), never a $0 cover
(backlog TJ-5006: the row covered ten shares for nothing, a gain of 50,
and the 110-share purchase left 20 shares held that were never bought).

Synthetic rows only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from _support.ca_engine_runs import _run


def _t(i, date, qty, net, account='m', **kw):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          symbol='SYQ', quantity=qty, currency='CAD',
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, account=account, id=i, **kw)


def _rows(res):
    return [(e['id'], e['qty'], round(e['gain'], 2))
            for e in res['transactions']]


def _div(qty=10, net=0):
    return _t('d', '2025-04-01', qty, net, type='stock_dividend')


def _figures(book):
    """[(row, units, gain, denied)], the units held, what the run said."""
    res, err = _run(book)
    return ([(e['id'], e['qty'], round(e['gain'], 2),
              round(e.get('disallowed_amount') or 0.0, 2))
             for e in res['transactions']],
            [(i['qty'], i['total_cost']) for i in res['inventory']],
            ' '.join(err.split()))


class TestStockDividendWhileShort(unittest.TestCase):
    @rule(R.CA_STKDIV_02)
    def test_the_short_grows_and_the_cover_closes_it(self):
        res, err = _run([_t('s', '2025-03-03', -100, 500),
                         _t('d', '2025-04-01', 10, 0, type='stock_dividend'),
                         _t('c', '2025-05-05', 110, -440)])
        # 500 received, 440 paid for the 110 owed: 60, and nothing held.
        self.assertEqual(_rows(res), [('c', 110, 60.0)])
        self.assertEqual(res['inventory'], [])
        said = ' '.join(err.split())
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-04-01 while m is short the security: booked as the "
                      "short's to make good. The short owes the lender the new "
                      "shares, so it grows by them with its proceeds unchanged; "
                      "nothing is acquired, nothing is covered", said)
        self.assertIn('It needs no declared amount.', said)
        self.assertEqual(res['stock_dividends_owed'], ['d'])
        self.assertEqual(said.count('while m is short'), 1)
        self.assertNotIn('conservation', said)

    @rule(R.CA_STKDIV_02)
    def test_a_part_cover_takes_its_share_of_the_proceeds(self):
        res, _err = _run([_t('s', '2025-03-03', -100, 500),
                          _t('d', '2025-04-01', 10, 0, type='stock_dividend'),
                          _t('c', '2025-05-05', 55, -220)])
        self.assertEqual(_rows(res), [('c', 55, 30.0)])
        self.assertEqual([(i['symbol'], i['qty'], i['total_cost'])
                          for i in res['inventory']], [('SYQ', -55.0, -250.0)])

    @rule(R.CA_STKDIV_02)
    def test_the_cover_at_a_loss_is_allowed_in_full(self):
        # The superficial-loss pass reads the dividend as the walk does:
        # nothing was acquired, and nothing is held 30 days after the
        # cover. Read as shares received, 20 shares stood as held and
        # 18.18 of the loss was denied onto a position that did not exist.
        rows, held, said = _figures([_t('s', '2025-03-03', -100, 500), _div(),
                                     _t('c', '2025-05-05', 110, -600)])
        self.assertEqual(rows, [('c', 110, -100.0, 0.0)])
        self.assertEqual(held, [])
        self.assertNotIn('still waits', said)

    @rule(R.CA_STKDIV_02)
    def test_a_dividend_larger_than_the_short(self):
        rows, held, said = _figures([_t('s', '2025-03-03', -5, 25), _div(),
                                     _t('c', '2025-05-05', 15, -60)])
        self.assertEqual(rows, [('c', 15, -35.0, 0.0)])
        self.assertEqual(held, [])
        self.assertNotIn('still waits', said)

    @rule(R.CA_STKDIV_02)
    def test_a_cover_long_after_the_dividend(self):
        rows, _held, said = _figures([_t('s', '2025-03-03', -100, 500), _div(),
                                      _t('c', '2026-02-02', 110, -600)])
        self.assertEqual(rows, [('c', 110, -100.0, 0.0)])
        self.assertNotIn('still waits', said)

    @rule(R.CA_STKDIV_02)
    def test_a_later_purchase_sold_at_a_loss_as_without_the_dividend(self):
        def book(div):
            return ([_t('s', '2025-03-03', -100, 500)] + ([_div()] if div else [])
                    + [_t('c', '2025-05-05', 110 if div else 100, -440),
                       _t('b', '2025-06-02', 100, -1000),
                       _t('x', '2025-06-12', -100, 500)])
        with_div, held, said = _figures(book(True))
        without, _h, _s = _figures(book(False))
        self.assertEqual(with_div, [('c', 110, 60.0, 0.0), ('x', 100, -500.0, 0.0)])
        self.assertEqual(without, [('c', 100, 60.0, 0.0), ('x', 100, -500.0, 0.0)])
        self.assertEqual(held, [])
        self.assertNotIn('still waits', said)

    @rule(R.CA_STKDIV_02)
    def test_a_value_on_the_row_is_no_proceeds(self):
        rows, _held, _said = _figures([_t('s', '2025-03-03', -100, 500),
                                       _div(net=-30),
                                       _t('c', '2025-05-05', 110, -440)])
        self.assertEqual(rows, [('c', 110, 60.0, 0.0)])

    @rule(R.CA_STKDIV_02)
    def test_the_account_that_received_the_row_decides(self):
        # L holds 50, m is short 100: the pool is net short 50. L's 5
        # shares are a receipt on its own long shares (in the pooled
        # books, as any purchase of L's while m is short); read by the
        # pool alone they were the short's and L's shares were gone.
        book = [_t('bl', '2025-01-06', 50, -200, account='L'),
                _t('s', '2025-03-03', -100, 500),
                _t('d', '2025-04-01', 5, 0, type='stock_dividend', account='L'),
                _t('c', '2025-05-05', 55, -300)]
        res, err = _run(book)
        # L 55, m short 100 then covered by 55: 10 held.
        self.assertEqual([i['qty'] for i in res['inventory']], [10.0])
        self.assertNotIn('stock_dividends_owed', res)
        self.assertNotIn('is short the security', err)
        self.assertNotIn('conservation', err)
        # Each account's own books say the same of the row.
        own, _e = _run([t for t in book if t.account == 'L'])
        self.assertEqual([(i['qty'], i['total_cost']) for i in own['inventory']],
                         [(55.0, 200.0)])

    @rule(R.CA_STKDIV_02)
    def test_the_short_accounts_row_while_the_pool_is_not_short(self):
        # L holds 100, m is short 100: the pool is flat. m's dividend
        # is its short's all the same: the pooled units go 10 short.
        book = [_t('bl', '2025-01-06', 100, -400, account='L'),
                _t('s', '2025-03-03', -100, 500), _div(),
                _t('c', '2025-05-05', 10, -60)]
        res, err = _run(book)
        self.assertEqual(res['stock_dividends_owed'], ['d'])
        self.assertEqual(res['inventory'], [])          # L 100, m short 100
        self.assertIn('while m is short the security', ' '.join(err.split()))
        self.assertNotIn('conservation', err)
        # ... and a pool the other account leaves long gives the units up
        # at the same cost: no gain or loss on the row.
        long_pool = [_t('bl', '2025-01-06', 500, -2000, account='L'),
                     _t('s', '2025-03-03', -100, 500), _div()]
        res, err = _run(long_pool)
        self.assertEqual(_rows(res), [('s', 100, 100.0)])
        self.assertEqual([(round(i['qty'], 6), i['total_cost'])
                          for i in res['inventory']], [(390.0, 1600.0)])
        self.assertNotIn('conservation', err)

    @rule(R.CA_STKDIV_02)
    def test_a_pool_holding_no_more_than_the_row_owes_books_no_loss(self):
        # L holds 34, 33 or 32 (10 a share); m is short 30 for 450, owes 3
        # more and covers 33 for 400. The pooled books sold m's 30 out of
        # L's shares (a gain of 150) and hold 4, 3 or 2 at the row: the 3
        # owed never leave as a sale at $0 (that booked their cost as a
        # loss of 30 or 20). One total, and the holdings carry the cost.
        for held in (34, 33, 32):
            with self.subTest(held=held):
                res, err = _run([
                    _t('bl', '2025-01-06', held, -10 * held, account='L'),
                    _t('s', '2025-03-03', -30, 450), _div(qty=3),
                    _t('c', '2025-05-05', 33, -400)])
                self.assertEqual(round(sum(e['gain'] for e in res['transactions']), 2),
                                 150.0)
                self.assertNotIn('d', [e['id'] for e in res['transactions']])
                self.assertEqual([(i['qty'], round(i['total_cost'], 2))
                                  for i in res['inventory']],
                                 [(float(held), 10.0 * held + 100)])
                said = ' '.join(err.split())
                self.assertEqual(said.count('stock dividend of 3 share(s) on '
                                            '2025-04-01 while m is short the '
                                            'security'), 1)
                self.assertNotIn('conservation', said)

    @rule(R.CA_STKDIV_02)
    def test_an_account_both_long_and_short_is_said_not_guessed(self):
        # One account holds 100 and has a sale of 30 its broker marked
        # short (a pool of its own): the 13 shares are booked as received
        # on the shares held, and the line names the short.
        res, err = _run([_t('b', '2025-01-06', 100, -1000),
                         _t('s', '2025-03-03', -30, 450, open_close='O'),
                         _div(qty=13),
                         _t('c', '2025-05-05', 33, -400, open_close='C')])
        said = ' '.join(err.split())
        self.assertIn('warning: ATTENTION: SYQ: stock dividend of 13 share(s) on '
                      '2025-04-01: m holds the security and is also short 30 of '
                      'it (a sale the broker marked short). The row is booked as '
                      'shares received on the shares held; the part the short '
                      'owes the lender is not in it: enter that by hand (.tt)',
                      said)
        self.assertNotIn('stock_dividends_owed', res)
        # The cover closes the 30 short and buys 3: 113 + 3 held.
        self.assertEqual([(e['id'], e['qty'], round(e['gain'], 2))
                          for e in res['transactions']], [('c', 30.0, 86.36)])
        self.assertEqual([(i['qty'], round(i['total_cost'], 2))
                          for i in res['inventory']], [(116.0, 1036.36)])

    @rule(R.CA_STKDIV_02)
    def test_a_negative_row_of_the_input_is_booked_as_it_was(self):
        # Only the rows the accounts' own walks named are a short's: a
        # reversal typed with a negative quantity is the sale at $0 it
        # always was.
        res, _err = _run([_t('b', '2025-03-03', 100, -500),
                          _t('d', '2025-04-01', -10, 0, type='stock_dividend')])
        self.assertEqual([i['qty'] for i in res['inventory']], [90.0])
        self.assertNotIn('stock_dividends_owed', res)

    @rule(R.CA_STKDIV_02)
    def test_the_run_asks_no_declared_amount_for_it(self):
        # lib/pipeline run_gains, the run's gains stage: the $0-cost line
        # of CA-STKDIV-01 is for shares received, never a short's.
        import contextlib
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains

        def said(book):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                res = run_gains(book, [], [], req=GainsRequest(
                    country='canada', taxable=True, year=2025))
            return res, ' '.join(err.getvalue().split())
        res, short = said([_t('s', '2025-03-03', -100, 500), _div(),
                           _t('c', '2025-05-05', 110, -440)])
        self.assertNotIn('entered at $0 cost', short)
        self.assertIn('while m is short the security', short)
        # The gains document names the row for the views that replay the
        # books (backlog TJ-5281: run_gains dropped it).
        self.assertEqual(res.get('stock_dividends_owed'), ['d'])
        _res, long_ = said([_t('b', '2025-03-03', 100, -500), _div()])
        self.assertIn('stock dividend of 10 share(s) on 2025-04-01 entered at '
                      '$0 cost', long_)
        self.assertNotIn('is short the security', long_)

    @rule(R.CA_STKDIV_01)
    def test_control_a_long_holding_as_before(self):
        res, _err = _run([_t('b', '2025-03-03', 100, -500),
                          _t('d', '2025-04-01', 10, 0, type='stock_dividend'),
                          _t('x', '2025-05-05', -110, 440)])
        self.assertEqual(_rows(res), [('x', 110, -60.0)])
        self.assertEqual(res['inventory'], [])


class TestTheAccountsAndThePooledBooksAgree(unittest.TestCase):
    """`tjs run` on two taxable accounts: each account's books and the
    pooled books across them read a stock dividend alike, by the account
    that received it."""

    def _run(self, accounts):
        import json
        import tempfile
        from _support.tt_fx_project import _cli, _tt_project
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, None, country='canada', year=2025,
                                     accounts=accounts)
            r = _cli(root, home, 'run', '--no-input')
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

            def held(name):
                doc = json.loads((root / 'work' / name).read_text())
                return {i['symbol']: i['qty'] for i in doc.get('inventory') or []}
            return (r.stdout + r.stderr, held('.blend_gains_wash.json'),
                    {a: held(f'{a}_gains.json') for a in accounts})

    @rule(R.CA_STKDIV_02)
    def test_a_holders_receipt_beside_another_accounts_short(self):
        # L holds 50 and receives 5; m is short 100 and covers 55.
        out, pooled, own = self._run({
            'm': ('taxable', ['BUYSELL 2025-03-03 10:00:00 SYQ.TO -100 CAD 5 500 0',
                              'BUYSELL 2025-05-05 10:00:00 SYQ.TO 55 CAD 5 275 0']),
            'L': ('taxable', ['BUYSELL 2025-01-06 10:00:00 SYQ.TO 50 CAD 4 200 0',
                              'BUYSELL 2025-04-01 10:00:00 SYQ.TO 5 CAD 0 0 0 '
                              'type=stock_dividend'])})
        self.assertEqual(own['L'], {'SYQ.TO': 55.0})
        self.assertEqual(pooled, {'SYQ.TO': 10.0})     # L 55, m still short 45
        self.assertNotIn('is short the security', out)
        self.assertIn('stock dividend of 5 share(s) on 2025-04-01 entered at '
                      '$0 cost', ' '.join(out.split()))
        self.assertNotIn('trustworthy', out)

    @rule(R.CA_STKDIV_02)
    def test_a_short_account_a_holder_and_a_registered_account(self):
        # m: short 100, 10 paid, 110 bought (its short's); and a long
        # holding of another security with its own dividend. L: 30 held, 3
        # received, 50 bought. r (registered): 40 held, 4 received.
        out, pooled, own = self._run({
            'm': ('taxable', ['BUYSELL 2025-03-03 10:00:00 SYQ.TO -100 CAD 5 500 0',
                              'BUYSELL 2025-04-01 10:00:00 SYQ.TO 10 CAD 0 0 0 '
                              'type=stock_dividend',
                              'BUYSELL 2025-05-05 10:00:00 SYQ.TO 110 CAD 5.4545 600 0',
                              'BUYSELL 2025-01-06 10:00:00 QZL.TO 100 CAD 10 1000 0',
                              'BUYSELL 2025-04-01 10:00:00 QZL.TO 10 CAD 0 0 0 '
                              'type=stock_dividend',
                              'BUYSELL 2025-06-02 10:00:00 QZL.TO -110 CAD 8 880 0']),
            'L': ('taxable', ['BUYSELL 2025-01-06 10:00:00 SYQ.TO 30 CAD 4 120 0',
                              'BUYSELL 2025-04-01 10:00:00 SYQ.TO 3 CAD 0 0 0 '
                              'type=stock_dividend',
                              'BUYSELL 2025-05-20 10:00:00 SYQ.TO 50 CAD 5 250 0']),
            'r': ('sheltered', ['BUYSELL 2025-01-06 10:00:00 SYQ.TO 40 CAD 4 160 0',
                                'BUYSELL 2025-04-01 10:00:00 SYQ.TO 4 CAD 0 0 0 '
                                'type=stock_dividend'])})
        self.assertEqual(own, {'m': {}, 'L': {'SYQ.TO': 83.0},
                               'r': {'SYQ.TO': 44.0}})
        # The pooled books hold what the taxable accounts hold: L's 83.
        self.assertEqual(pooled, {'SYQ.TO': 83.0})
        said = ' '.join(out.split())
        # m's row is said once, as its short's; L's and the long
        # holding's ask for their declared amounts; m's short's does not.
        self.assertEqual(said.count('SYQ.TO: stock dividend of 10 share(s) on '
                                    '2025-04-01 while m is short the security'), 1)
        self.assertIn('SYQ.TO: stock dividend of 3 share(s) on 2025-04-01 '
                      'entered at $0 cost', said)
        self.assertIn('QZL.TO: stock dividend of 10 share(s) on 2025-04-01 '
                      'entered at $0 cost', said)
        self.assertNotIn('SYQ.TO: stock dividend of 10 share(s) on 2025-04-01 '
                         'entered at $0 cost', said)
        self.assertNotIn('trustworthy', out)


if __name__ == "__main__":
    unittest.main()

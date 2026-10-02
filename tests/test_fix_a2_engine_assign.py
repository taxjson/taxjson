"""Re-audit-2 fixes: each option assignment's premium goes to ITS OWN stock
leg (A2-0050, A2-0051, A2-0052, A2-0195, A2-0196, A2-0203).

The assignment-premium ledger pairs every option ASSIGN with its stock
leg by identity (same account and underlying, the delivered quantity at
the declared contract size, the strike as the leg's price, a leg dated
up to 3 days before or 7 days after the option row) instead of by
proximity in the sorted stream. Both engines share the ledger, so every
book runs under both countries. All data synthetic.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules
from tax_rules import rule


def _tx(action, date, symbol, qty, price, net, *, time='10:00:00',
        acct='margin', mult=0.0, cur=None):
    return TaxTransaction(
        action=action, date=date, date_settle=date, time=time,
        symbol=symbol, quantity=float(qty), price=float(price),
        net_amount=float(net),
        currency=cur or ('CAD' if symbol.endswith('.TO') else 'USD'),
        account=acct, multiplier=mult)


def _run(engine, book, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = engine().compute_gains(book, **kw)
    return res, err.getvalue()


def _total(res):
    return round(sum(g['gain'] for g in res['transactions']
                     if g.get('raw_gain') is not None), 2)


def _held_cost(res, sym):
    return round(sum(h['total_cost'] for h in res['inventory']
                     if h['symbol'] == sym), 2)


_ENGINES = (('canada', CanadaTaxRules, 'TO'), ('usa', USATaxRules, 'US'))


@rule("CA-OPT-06", "CA-OPT-08")
@rule("US-OPT-02", "US-OPT-05")
class TestAssignLegIdentity(unittest.TestCase):

    def test_plain_assignment_does_not_take_a_later_marked_leg(self):
        # A2-0050: P1 (12P, premium 199) assigned 12-12 with a PLAIN
        # BUYSELL leg; P2 (11P, premium 99) assigned 12-19 with a MARKED
        # ASSIGN leg; 100 sold 12-15 in between. P1's premium belongs
        # to the 12-12 shares, P2's to the 12-19 shares.
        for country, eng, sx in _ENGINES:
            with self.subTest(country=country):
                p1, p2 = f'XYZ251212P00012000.{sx}', f'XYZ251219P00011000.{sx}'
                stk = f'XYZ.{sx}'
                book = [
                    _tx('BUYSELL', '2025-11-03', p1, -1, 1.99, 199),
                    _tx('BUYSELL', '2025-11-04', p2, -1, 0.99, 99),
                    _tx('ASSIGN', '2025-12-12', p1, 1, 0, 0, time='16:00:00'),
                    _tx('BUYSELL', '2025-12-12', stk, 100, 12, 1200,
                        time='16:00:00'),
                    _tx('BUYSELL', '2025-12-15', stk, -100, 10.99, 1099),
                    _tx('ASSIGN', '2025-12-19', p2, 1, 0, 0, time='16:00:00'),
                    _tx('ASSIGN', '2025-12-19', stk, 100, 11, 1100,
                        time='16:00:01'),
                ]
                res, err = _run(eng, book)
                self.assertEqual(_total(res), 98.0,
                                 "P1's premium must reduce its own shares "
                                 "(sale cost 1001; buggy: 1200, gain 0)")
                self.assertEqual(_held_cost(res, stk), 1001.0)
                self.assertNotIn('unconsumed', err)

    def test_same_day_mixed_conventions_keep_their_own_premiums(self):
        # A2-0050 same-day variant: the 11-strike put (premium 199,
        # plain leg) and the 12-strike put (premium 99, marked leg).
        for country, eng, sx in _ENGINES:
            with self.subTest(country=country):
                p11, p12 = (f'XYZ251219P00011000.{sx}',
                            f'XYZ251219P00012000.{sx}')
                stk = f'XYZ.{sx}'
                book = [
                    _tx('BUYSELL', '2025-11-03', p11, -1, 1.99, 199),
                    _tx('BUYSELL', '2025-11-04', p12, -1, 0.99, 99),
                    _tx('ASSIGN', '2025-12-19', p11, 1, 0, 0, time='16:00:00'),
                    _tx('BUYSELL', '2025-12-19', stk, 100, 11, 1100,
                        time='16:00:00'),
                    _tx('ASSIGN', '2025-12-19', p12, 1, 0, 0, time='16:00:00'),
                    _tx('ASSIGN', '2025-12-19', stk, 100, 12, 1200,
                        time='16:00:01'),
                ]
                res, err = _run(eng, book)
                self.assertNotIn('unconsumed', err)
                self.assertEqual(_held_cost(res, stk), 2002.0)
                if country == 'usa':
                    # FIFO: the first lot (the 11-strike shares) is
                    # 1100 - 199.
                    res, _ = _run(eng, book + [
                        _tx('BUYSELL', '2026-02-02', stk, -100, 10, 1000)])
                    sale = [g for g in res['transactions']
                            if g.get('raw_gain') is not None
                            and g['symbol'] == stk]
                    self.assertEqual([round(g['cost'], 2) for g in sale],
                                     [901.0])

    def test_plain_then_marked_same_day_and_four_days_later(self):
        # A2-0052 (R1-33 regression): plain P1 at 16:00 must not claim
        # P2's marked leg; P2's 99 used to vanish (total 499).
        for country, eng, sx in _ENGINES:
            for p2day in ('2025-03-21', '2025-03-25'):
                with self.subTest(country=country, p2day=p2day):
                    p1 = f'XYZ250321P00012000.{sx}'
                    p2 = f'XYZ250417P00011000.{sx}'
                    stk = f'XYZ.{sx}'
                    book = [
                        _tx('BUYSELL', '2025-02-03', p1, -1, 2, 199),
                        _tx('BUYSELL', '2025-02-04', p2, -1, 1, 99),
                        _tx('ASSIGN', '2025-03-21', p1, 1, 0, 0,
                            time='16:00:00'),
                        _tx('BUYSELL', '2025-03-21', stk, 100, 12, 1200,
                            time='16:00:00'),
                        _tx('ASSIGN', p2day, p2, 1, 0, 0, time='16:05:00'),
                        _tx('ASSIGN', p2day, stk, 100, 11, 1100,
                            time='16:05:01'),
                        _tx('BUYSELL', '2025-06-02', stk, -200, 13, 2600),
                    ]
                    res, err = _run(eng, book)
                    self.assertEqual(_total(res), 598.0)
                    self.assertNotIn('unconsumed', err)

    def test_stock_leg_dated_before_the_option_row(self):
        # A2-0051 / A2-0195: the stock leg (marked or plain) is dated the
        # day before the option ASSIGN row; the premium used to be
        # dropped from every year (99 instead of 298).
        for country, eng, sx in _ENGINES:
            for leg_action in ('ASSIGN', 'BUYSELL'):
                kws = ([{}, {'option_premium_timing': 'grant',
                             'option_grant_since': 2025}]
                       if country == 'canada' else [{}])
                for kw in kws:
                    with self.subTest(country=country, leg=leg_action,
                                      timing=kw.get('option_premium_timing',
                                                    'default')):
                        put = f'ABC250321P00012000.{sx}'
                        stk = f'ABC.{sx}'
                        book = [
                            _tx('BUYSELL', '2025-02-03', put, -1, 2, 199),
                            _tx(leg_action, '2025-03-20', stk, 100, 12,
                                1200, time='16:00:00'),
                            _tx('ASSIGN', '2025-03-21', put, 1, 0, 0,
                                time='16:00:00'),
                            _tx('BUYSELL', '2025-06-02', stk, -100, 13, 1299),
                        ]
                        res, err = _run(eng, book, **kw)
                        self.assertEqual(_total(res), 298.0)
                        self.assertNotIn('unconsumed', err)

    def test_mini_contracts_stage_their_declared_size(self):
        # A2-0196: two x10 mini puts assigned the same day, each
        # delivering a 10-share leg; each leg takes its own premium.
        for country, eng, sx in _ENGINES:
            for mult in (10.0, 0.0):
                with self.subTest(country=country, mult=mult):
                    p1 = f'QZX251219P00045000.{sx}'
                    p2 = f'QZX251219P00044000.{sx}'
                    stk = f'QZX.{sx}'
                    book = [
                        _tx('BUYSELL', '2025-11-03', p1, -1, 3, 30,
                            mult=mult),
                        _tx('BUYSELL', '2025-11-03', p2, -1, 1, 10,
                            mult=mult),
                        _tx('ASSIGN', '2025-12-19', p1, 1, 0, 0,
                            time='16:20:00', mult=mult),
                        _tx('ASSIGN', '2025-12-19', p2, 1, 0, 0,
                            time='16:20:00', mult=mult),
                        _tx('BUYSELL', '2025-12-19', stk, 10, 45, 450,
                            time='16:20:00'),
                        _tx('BUYSELL', '2025-12-19', stk, 10, 44, 440,
                            time='16:20:00'),
                    ]
                    res, err = _run(eng, book)
                    # 450 + 440 - 30 - 10 (buggy: one premium unconsumed)
                    self.assertEqual(_held_cost(res, stk), 850.0)
                    self.assertNotIn('unconsumed', err)
                    if country == 'usa':
                        # FIFO: the 45-strike shares carry 450 - 30.
                        res, _ = _run(eng, book + [
                            _tx('BUYSELL', '2026-02-02', stk, -10, 40, 400)])
                        sale = [g for g in res['transactions']
                                if g.get('raw_gain') is not None
                                and g['symbol'] == stk]
                        self.assertEqual([round(g['cost'], 2)
                                          for g in sale], [420.0])

    def test_unpaired_premium_warning_names_the_window(self):
        # The only stock trade is 12 days after the assignment: the
        # premium is not folded, and the warning must not claim the leg
        # "never arrived" when it may simply be dated outside the window.
        for country, eng, sx in _ENGINES:
            with self.subTest(country=country):
                put = f'ABC250321P00012000.{sx}'
                book = [
                    _tx('BUYSELL', '2025-02-03', put, -1, 2, 199),
                    _tx('ASSIGN', '2025-03-21', put, 1, 0, 0,
                        time='16:00:00'),
                    _tx('BUYSELL', '2025-04-02', f'ABC.{sx}', 100, 12, 1200),
                ]
                _res, err = _run(eng, book)
                self.assertIn('unconsumed option-assignment', err)
                self.assertNotIn('never arrived', err)
                self.assertIn('3 days before', err)


@rule("US-OPT-02", "US-OPT-05")
class TestSameMomentPremiumByStrike(unittest.TestCase):

    def test_same_moment_premiums_follow_the_strike(self):
        # A2-0203: puts A (50 strike, premium 500) and B (40 strike,
        # premium 100) assigned at one moment with plain legs at their
        # strikes. Whatever order the option rows are listed in, A's
        # shares carry A's premium (FIFO sells A's lot first).
        A, B = 'XYZ250321P00050000.US', 'XYZ250321P00040000.US'
        rows = {
            'wA': _tx('BUYSELL', '2025-02-03', A, -1, 5, 500),
            'wB': _tx('BUYSELL', '2025-02-03', B, -1, 1, 100,
                      time='10:01:00'),
            'oA': _tx('ASSIGN', '2025-03-21', A, 1, 0, 0, time='16:00:00'),
            'oB': _tx('ASSIGN', '2025-03-21', B, 1, 0, 0, time='16:00:00'),
            'lA': _tx('BUYSELL', '2025-03-21', 'XYZ.US', 100, 50, 5000,
                      time='16:00:00'),
            'lB': _tx('BUYSELL', '2025-03-21', 'XYZ.US', 100, 40, 4000,
                      time='16:00:00'),
            's': _tx('BUYSELL', '2025-06-02', 'XYZ.US', -100, 45, 4500),
        }
        for order in (['wA', 'wB', 'oA', 'oB', 'lA', 'lB', 's'],
                      ['wA', 'wB', 'oB', 'oA', 'lA', 'lB', 's']):
            with self.subTest(order=order):
                res, err = _run(USATaxRules, [rows[k] for k in order])
                sale = [g for g in res['transactions']
                        if g.get('raw_gain') is not None
                        and g.get('symbol') == 'XYZ.US']
                self.assertEqual([round(g['cost'], 2) for g in sale],
                                 [4500.0])
                self.assertEqual(_held_cost(res, 'XYZ.US'), 3900.0)
                self.assertNotIn('unconsumed', err)


if __name__ == '__main__':
    unittest.main()

"""Re-audit-2 fixes: each option assignment's premium goes to ITS OWN stock
leg (A2-0050, A2-0051, A2-0052, A2-0195, A2-0196, A2-0203).

The assignment-premium ledger pairs every option ASSIGN with its stock
leg by identity (same account and underlying, the delivered quantity at
the declared contract size, the strike as the leg's price, a leg dated
up to 3 days before or 7 days after the option row) instead of by
proximity in the sorted stream. Both engines share the ledger, so every
book runs under both countries. All data synthetic.

Formerly: test_fix_a2_engine_assign
"""
import unittest

from taxjson.engine.us.taxrules import USATaxRules
from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_option_assignment import _tx, _run, _held_cost, _ENGINES


def _total(res):
    return round(sum(g['gain'] for g in res['transactions']
                     if g.get('raw_gain') is not None), 2)


@rule(R.US_OPT_05)
class TestTheUSEngineKeepsItsRowOrderPairing(unittest.TestCase):
    """TJ-3173: the pro-rata tie-break for equal strikes of one day is
    Canada's (CA-OPT-08; TJ-2750, `_pair_assign_legs`' `tie_pro_rata`,
    which only the Canadian engine passes). The US engine pairs as it
    did: TJ-2719's book (A, 2 contracts written for 200; B, 1 for 300;
    deliveries of 150 and 150 around a sale of 150 for 1900) gives the
    first delivery to the option row nearer it. A's row first: B's 100
    and A's 50 (350 of premium), 1450 sold for a gain of 450, 1650 held;
    B's first: A's 150, 1650 sold for 250, 1450 held. The US stats and
    rename views say the same (TJ-3177)."""

    A, B, STK = 'SYQ251212P00012000.US', 'SYQ260116P00012000.US', 'SYQ.US'

    def _book(self, a_first):
        opts = [_tx('ASSIGN', '2025-12-12', self.A, 2, 0, 0, time='16:00:00'),
                _tx('ASSIGN', '2025-12-12', self.B, 1, 0, 0, time='16:00:00')]
        if not a_first:
            opts.reverse()
        return ([_tx('BUYSELL', '2025-11-03', self.A, -2, 1.00, 200),
                 _tx('BUYSELL', '2025-11-04', self.B, -1, 3.00, 300)] + opts + [
                _tx('ASSIGN', '2025-12-12', self.STK, 150, 12, 1800,
                    time='16:00:01'),
                _tx('BUYSELL', '2025-12-12', self.STK, -150, 1900 / 150, 1900,
                    time='16:00:02'),
                _tx('ASSIGN', '2025-12-12', self.STK, 150, 12, 1800,
                    time='16:00:03')])

    def _rows(self, book):
        import dataclasses
        from taxjson.lib.option_symbols import is_option_symbol
        out = []
        for n, t in enumerate(book):
            d = dataclasses.asdict(t)
            d['id'], d['_acct'] = f'r{n}', t.account
            if is_option_symbol(t.symbol):
                d['multiplier'] = 100
                d['price'] = abs(t.net_amount) / abs(t.quantity) / 100
            out.append(d)
        return out

    def test_the_nearer_option_row_takes_the_first_delivery(self):
        for a_first, cost, gain, held in ((True, 1450.0, 450.0, 1650.0),
                                          (False, 1650.0, 250.0, 1450.0)):
            with self.subTest(a_first=a_first):
                res, _ = _run(USATaxRules, self._book(a_first))
                sales = [(round(r['cost'], 2), round(r['gain'], 2))
                         for r in res['transactions']
                         if r.get('raw_gain') is not None]
                self.assertEqual(sales, [(cost, gain)])
                self.assertEqual(_held_cost(res, self.STK), held)

    def test_an_unpaired_credit_drains_after_the_legs_own(self):
        # TJ-3219: the three-way book (C, a third put written for 100) with
        # C's row between the first delivery and the sale, seconds apart:
        # the engine gives the 250 B's 100 (its claim), then A's 150 by
        # proximity (A has no leg of its own); the twin folds in that
        # order too: 300 + 150 = 450 on the sale, not A's 200 first (350).
        from taxjson.lib import trade_stats as TS
        C = 'SYQ260220P00012000.US'
        book = [_tx('BUYSELL', '2025-11-03', self.A, -2, 1.00, 200),
                _tx('BUYSELL', '2025-11-04', self.B, -1, 3.00, 300),
                _tx('BUYSELL', '2025-11-05', C, -1, 1.00, 100),
                _tx('ASSIGN', '2025-12-12', self.A, 2, 0, 0, time='16:00:00'),
                _tx('ASSIGN', '2025-12-12', self.B, 1, 0, 0, time='16:00:01'),
                _tx('ASSIGN', '2025-12-12', self.STK, 250, 12, 3000,
                    time='16:00:02'),
                _tx('ASSIGN', '2025-12-12', C, 1, 0, 0, time='16:00:03'),
                _tx('BUYSELL', '2025-12-12', self.STK, -250, 12.4, 3100,
                    time='16:00:04'),
                _tx('ASSIGN', '2025-12-12', self.STK, 150, 12, 1800,
                    time='16:00:05')]
        res, _ = _run(USATaxRules, book)
        sales = [(round(r['cost'], 2), round(r['gain'], 2))
                 for r in res['transactions'] if r.get('raw_gain') is not None]
        self.assertEqual(sales, [(2550.0, 550.0)])
        rows = self._rows(book)
        credits = TS.written_option_trades(rows, lambda x: x['date'])['credits']
        self.assertEqual({k: round(v, 4) for k, v in TS.credits_by_sale(
            rows, credits, fifo=True).items()}, {'r7': 450.0})

    def test_an_absent_peer_pairs_by_distance(self):
        # TJ-3231: B's write is outside the books (only its ASSIGN row).
        # The US engine pairs by the nearest row: A's row first, B takes
        # the first 100 (no premium on the sale: 1200 sold), B's first,
        # A takes it (300 off: 900 sold). The twin counts B among the
        # assignment rows (a member with no premium) and says the same.
        from taxjson.lib import trade_stats as TS
        for a_first, cost, on_sale in ((True, 1200.0, {}),
                                       (False, 900.0, {'r4': 300.0})):
            with self.subTest(a_first=a_first):
                opts = [_tx('ASSIGN', '2025-12-12', self.A, 1, 0, 0,
                            time='16:00:00'),
                        _tx('ASSIGN', '2025-12-12', self.B, 1, 0, 0,
                            time='16:00:00')]
                if not a_first:
                    opts.reverse()
                book = ([_tx('BUYSELL', '2025-11-03', self.A, -1, 3.00, 300)]
                        + opts + [
                        _tx('ASSIGN', '2025-12-12', self.STK, 100, 12, 1200,
                            time='16:00:01'),
                        _tx('BUYSELL', '2025-12-12', self.STK, -100, 13, 1300,
                            time='16:00:02'),
                        _tx('ASSIGN', '2025-12-12', self.STK, 100, 12, 1200,
                            time='16:00:03')])
                res, _ = _run(USATaxRules, book)
                self.assertEqual([round(r['cost'], 2) for r in res['transactions']
                                  if r.get('raw_gain') is not None], [cost])
                rows = self._rows(book)
                credits = TS.written_option_trades(
                    rows, lambda x: x['date'])['credits']
                self.assertEqual({k: round(v, 4) for k, v in TS.credits_by_sale(
                    rows, credits, fifo=True).items()}, on_sale)

    def test_the_us_views_say_the_same(self):
        from taxjson.lib import trade_stats as TS
        from taxjson.lib.renames import _assign_costs
        for a_first, on_sale, legs in ((True, 350.0, (-350.0, -150.0)),
                                       (False, 150.0, (-150.0, -350.0))):
            with self.subTest(a_first=a_first):
                rows = self._rows(self._book(a_first))
                credits = TS.written_option_trades(
                    rows, lambda x: x['date'])['credits']
                by_sale = TS.credits_by_sale(rows, credits, fifo=True)
                self.assertEqual({k: round(v, 4) for k, v in by_sale.items()},
                                 {'r5': on_sale})
                out = _assign_costs(rows, 'usa')
                self.assertEqual({r['id']: round(out[id(r)], 4) for r in rows
                                  if id(r) in out}, {'r4': legs[0], 'r6': legs[1]})


@rule(R.CA_OPT_06, R.CA_OPT_08)
@rule(R.US_OPT_02, R.US_OPT_05)
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


@rule(R.US_OPT_02, R.US_OPT_05)
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

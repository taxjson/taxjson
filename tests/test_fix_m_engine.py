"""Regression tests for the engine-area MEDIUM audit fixes (fixm/engine).

Synthetic data only; account labels and tickers are fake.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


def _tt(content, account='margin'):
    """`ACTION date time symbol qty cur price net [fee] [settle]` rows."""
    out = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        p = line.split()
        kw = dict(action=p[0], date=p[1], time=p[2], symbol=p[3],
                  quantity=float(p[4]), currency=p[5], price=float(p[6]),
                  net_amount=float(p[7]), account=account)
        if len(p) > 8:
            kw['fee'] = float(p[8])
        if len(p) > 9:
            kw['date_settle'] = p[9]
        out.append(TaxTransaction(**kw))
    return out


def _by_year(result):
    out = {}
    for r in result['transactions']:
        y = (r.get('date_settle') or r['date'])[:4]
        out[y] = round(out.get(y, 0.0) + r['gain'], 2)
    return out


def _run(engine, txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = engine.compute_gains(txs, **kw)
    return res, err.getvalue()


def _rows(result, symbol):
    return [r for r in result['transactions'] if r['symbol'] == symbol]


class TestAssignmentPremiumPairing(unittest.TestCase):
    """R1-28 / R1-32 / R1-34 / R1-178 and the US twins S070-18 /
    S070-20 / S071-11: a staged assignment premium goes to ITS OWN
    stock leg (direction, size, date window), never to whichever
    same-symbol trade sorts first."""

    TWO_ASSIGN = """
        BUYSELL 2024-06-03 10:00:00 QZX.US 100 USD 50 5000
        BUYSELL 2025-11-03 10:00:00 QZX251219P00061000.US -1 USD 3 300
        BUYSELL 2025-11-03 10:00:00 QZX251219C00060000.US -1 USD 2 200
        ASSIGN 2025-12-19 16:20:00 QZX251219P00061000.US 1 USD 0 0
        ASSIGN 2025-12-19 16:20:00 QZX251219C00060000.US 1 USD 0 0
        BUYSELL 2025-12-19 16:20:00 QZX.US 100 USD 61 6100
        BUYSELL 2025-12-19 16:20:00 QZX.US -100 USD 60 6000
        BUYSELL 2026-02-02 10:00:00 QZX.US -100 USD 60 6000
    """

    def test_ca_opposite_direction_same_moment(self):
        # R1-28: put premium off the put shares' cost (5800), call
        # premium onto the call shares' proceeds (6200).
        res, _ = _run(CanadaTaxRules(), _tt(self.TWO_ASSIGN))
        sale = [r for r in _rows(res, 'QZX.US')
                if r['date'] == '2025-12-19'][0]
        self.assertAlmostEqual(sale['proceeds'], 6200.0, places=2)
        yrs = _by_year(res)
        self.assertAlmostEqual(yrs['2025'], 800.0, places=2)
        self.assertAlmostEqual(yrs['2026'], 600.0, places=2)

    def test_us_opposite_direction_either_row_order(self):
        # S071-11: 1200 LT / 200 ST whatever the row order.
        base = self.TWO_ASSIGN.strip().splitlines()
        swapped = base[:5] + [base[6], base[5]] + base[7:]
        for rows in (self.TWO_ASSIGN, "\n".join(swapped)):
            res, _ = _run(USATaxRules(), _tt(rows))
            yrs = _by_year(res)
            self.assertAlmostEqual(yrs['2025'], 1200.0, places=2)
            self.assertAlmostEqual(yrs['2026'], 200.0, places=2)

    def test_ca_put_spread_each_leg_its_own_option(self):
        # R1-32: short 45P (359) assigned + long 44P (261) exercised on
        # one day; buy 200 @45 and sell 200 @44 with 500 already held.
        rows = """
            BUYSELL 2025-01-06 10:00:00 QRS.TO 500 CAD 40 20000
            BUYSELL 2025-11-03 10:00:00 QRS251219P00045000.TO -2 CAD 1.8 359
            BUYSELL 2025-11-03 10:00:00 QRS251219P00044000.TO 2 CAD 1.3 261
            ASSIGN 2025-12-19 16:20:00 QRS251219P00045000.TO 2 CAD 0 0
            ASSIGN 2025-12-19 16:20:00 QRS251219P00044000.TO -2 CAD 0 0
            BUYSELL 2025-12-19 16:20:00 QRS.TO 200 CAD 45 9000
            BUYSELL 2025-12-19 16:20:00 QRS.TO -200 CAD 44 8800
            BUYSELL 2026-01-06 10:00:00 QRS.TO -500 CAD 50 25000
        """
        res, _ = _run(CanadaTaxRules(), _tt(rows))
        sale = [r for r in _rows(res, 'QRS.TO')
                if r['date'] == '2025-12-19'][0]
        self.assertAlmostEqual(sale['proceeds'], 8539.0, places=2)
        yrs = _by_year(res)
        self.assertAlmostEqual(yrs['2025'], 355.86, places=2)
        self.assertAlmostEqual(yrs['2026'], 4542.14, places=2)

    def test_us_put_spread(self):
        # S070-20: 2025 = 539 LT, 2026 = 3000 LT + 1359 ST.
        rows = """
            BUYSELL 2024-01-05 10:00:00 QRS.US 500 USD 40 20000
            BUYSELL 2025-11-03 10:00:00 QRS251219P00045000.US -2 USD 1.8 359
            BUYSELL 2025-11-03 10:00:00 QRS251219P00044000.US 2 USD 1.3 261
            ASSIGN 2025-12-19 16:20:00 QRS251219P00045000.US 2 USD 0 0
            ASSIGN 2025-12-19 16:20:00 QRS251219P00044000.US -2 USD 0 0
            BUYSELL 2025-12-19 16:20:00 QRS.US 200 USD 45 9000
            BUYSELL 2025-12-19 16:20:00 QRS.US -200 USD 44 8800
            BUYSELL 2026-01-06 10:00:00 QRS.US -500 USD 50 25000
        """
        res, _ = _run(USATaxRules(), _tt(rows))
        yrs = _by_year(res)
        self.assertAlmostEqual(yrs['2025'], 539.0, places=2)
        self.assertAlmostEqual(yrs['2026'], 4359.0, places=2)

    SPLIT_LEGS = """
        BUYSELL 2026-01-05 10:00:00 ABC.TO 100 CAD 50 5000
        BUYSELL 2026-02-02 10:00:00 ABC260320C00060000.TO -2 CAD 3 600
        ASSIGN 2026-03-20 16:00:00 ABC260320C00060000.TO 2 CAD 0 0
        ASSIGN 2026-03-20 16:00:01 ABC.TO -100 CAD 60 6000
        ASSIGN 2026-03-20 16:00:02 ABC.TO -100 CAD 60 6000
        BUYSELL 2027-02-10 10:00:00 ABC.TO 100 CAD 60 6000
    """

    def test_ca_split_legs_share_the_premium(self):
        # R1-178 E5: 300 per 100-share leg -> {2026: 1300, 2027: 300}.
        res, _ = _run(CanadaTaxRules(), _tt(self.SPLIT_LEGS))
        yrs = _by_year(res)
        self.assertAlmostEqual(yrs['2026'], 1300.0, places=2)
        self.assertAlmostEqual(yrs['2027'], 300.0, places=2)

    def test_us_split_legs_share_the_premium(self):
        # S070-18 U5.
        rows = self.SPLIT_LEGS.replace('.TO', '.US').replace(' CAD ', ' USD ')
        res, _ = _run(USATaxRules(), _tt(rows))
        yrs = _by_year(res)
        self.assertAlmostEqual(yrs['2026'], 1300.0, places=2)
        self.assertAlmostEqual(yrs['2027'], 300.0, places=2)

    MISSING_LEG = """
        BUYSELL 2026-01-05 10:00:00 ABC.TO 100 CAD 45 4500
        BUYSELL 2026-01-10 10:00:00 ABC260116P00050000.TO -1 CAD 3 300
        ASSIGN 2026-01-16 16:00:00 ABC260116P00050000.TO 1 CAD 0 0
        BUYSELL 2026-06-10 10:00:00 ABC.TO 100 CAD 50 5000
    """

    def test_ca_missing_leg_not_folded_into_unrelated_trade(self):
        # R1-34 / R1-178 E2: the June buy is not the assignment's leg;
        # its cost stays 5000 and the run warns the premium is unconsumed.
        res, err = _run(CanadaTaxRules(), _tt(self.MISSING_LEG))
        inv = {r['symbol']: r for r in res['inventory']}
        self.assertAlmostEqual(inv['ABC.TO']['total_cost'], 9500.0, places=2)
        self.assertIn('unconsumed option-assignment', err)

    def test_us_missing_leg_not_folded_into_unrelated_trade(self):
        rows = self.MISSING_LEG.replace('.TO', '.US').replace(' CAD ', ' USD ')
        res, err = _run(USATaxRules(), _tt(rows))
        self.assertIn('unconsumed option-assignment', err)
        lots = [r for r in res['inventory'] if r['symbol'] == 'ABC.US']
        self.assertAlmostEqual(sum(r['total_cost'] for r in lots),
                               9500.0, places=2)


class TestAssignmentRootResolution(unittest.TestCase):
    """R1-35 / S019-01 / S070-17 / R1-176: an option root that differs
    from the delivered line (RCI for RCI.B.TO, BRKB for BRK.B.US, F:CL
    for F:CLG6.US) still rolls the premium into that line."""

    def test_ca_montreal_class_root(self):
        rows = """
            BUYSELL 2025-12-01 10:00:00 RCI260116P00050000.TO -1 CAD 2 199
            ASSIGN 2026-01-16 16:00:00 RCI260116P00050000.TO 1 CAD 0 0
            BUYSELL 2026-01-16 16:00:00 RCI.B.TO 100 CAD 50 5000
            BUYSELL 2026-03-02 10:00:00 RCI.B.TO -100 CAD 52 5200
        """
        res, err = _run(CanadaTaxRules(), _tt(rows))
        self.assertNotIn('cash-settled', err)
        sale = _rows(res, 'RCI.B.TO')[0]
        self.assertAlmostEqual(sale['cost'], 4801.0, places=2)
        self.assertEqual(_rows(res, 'RCI260116P00050000.TO'), [])

    def test_us_brkb_root(self):
        rows = """
            BUYSELL 2025-06-02 10:00:00 BRKB251219C00050000.US 1 USD 5 500
            ASSIGN 2025-12-15 16:00:00 BRKB251219C00050000.US -1 USD 0 0
            ASSIGN 2025-12-15 16:00:01 BRK.B.US 100 USD 50 5000
            BUYSELL 2026-02-02 10:00:00 BRK.B.US -100 USD 51.99 5199
        """
        res, err = _run(USATaxRules(), _tt(rows))
        self.assertNotIn('cash-settled', err)
        yrs = _by_year(res)
        self.assertNotIn('2025', yrs)
        self.assertAlmostEqual(yrs['2026'], -301.0, places=2)

    def test_ca_futures_option_exercise(self):
        rows = """
            BUYSELL 2025-11-03 10:00:00 F:CL260114C00060000.US 1 USD 2000 2000
            ASSIGN 2025-12-30 16:00:00 F:CL260114C00060000.US -1 USD 0 0
            BUYSELL 2025-12-30 16:00:00 F:CLG6.US 1 USD 60000 60000
            BUYSELL 2026-01-09 10:00:00 F:CLG6.US -1 USD 63000 63000
        """
        res, err = _run(CanadaTaxRules(), _tt(rows))
        self.assertNotIn('cash-settled', err)
        self.assertEqual(_by_year(res), {'2026': 1000.0})

    def test_index_option_still_cash_settled(self):
        rows = """
            BUYSELL 2025-06-02 10:00:00 XSP250620P00068500.US -1 USD 2 200
            ASSIGN 2025-06-20 16:00:00 XSP250620P00068500.US 1 USD 0 0
        """
        res, err = _run(CanadaTaxRules(), _tt(rows))
        self.assertIn('cash-settled', err)


if __name__ == '__main__':
    unittest.main()


class TestSameMomentOrdering(unittest.TestCase):
    """S069-16 / S070-12 / R1-31 / S069-14: results must not depend on a
    content hash or a one-second clock gap."""

    def test_ca_balance_walk_ignores_row_hash(self):
        # S069-16: an unrelated byte (the description) of a same-moment
        # short + cover pair used to flip the superficial-loss denial.
        def tx(d, q, p, desc=''):
            return TaxTransaction(action='BUYSELL', date=d, date_settle=d,
                                  time='09:30:00', symbol='XYZ.TO',
                                  quantity=q, currency='CAD', price=p,
                                  net_amount=abs(q * p), account='wb',
                                  description=desc)
        outcomes = set()
        for i in range(24):
            txs = [tx('2025-01-02', 2, 20.0), tx('2025-03-03', -2, 10.0),
                   tx('2025-03-10', -2, 10.0, f'd{i}'),
                   tx('2025-03-10', 1, 10.5, f'd{i}'),
                   tx('2025-03-20', 2, 9.0)]
            res, _ = _run(CanadaTaxRules(), txs)
            s = res['summary']
            outcomes.add((round(s['total_gain'], 2),
                          round(s['total_disallowed'], 2)))
        self.assertEqual(len(outcomes), 1, outcomes)

    def test_us_same_moment_replacement_lots_in_row_order(self):
        # S070-12: the deferral rides the lot listed first (FIFO sells
        # it), whatever the rows' content hashes.
        for i in range(24):
            txs = _tt("""
                BUYSELL 2025-01-02 09:30:00 XYZ.US 100 USD 20 2000
                BUYSELL 2025-03-03 09:30:00 XYZ.US -100 USD 10 1000
                BUYSELL 2025-03-10 09:30:00 XYZ.US 100 USD 10.5 1050
                BUYSELL 2025-03-10 09:30:00 XYZ.US 100 USD 11 1100
                BUYSELL 2025-06-02 09:30:00 XYZ.US -100 USD 12 1200
            """)
            txs[3] = TaxTransaction(**{**txs[3].to_dict(), 'id': None,
                                       'description': f'd{i}'})
            res, _ = _run(USATaxRules(), txs)
            self.assertAlmostEqual(res['summary']['total_gain'], -850.0,
                                   places=2, msg=f"variant {i}")

    def test_ca_pre_loss_bump_reaches_same_second_fill(self):
        # R1-31: fill 2 of the loss order sees the bump whether it is
        # 0, 1 or 2 seconds after fill 1.
        for t2 in ('11:00:00', '11:00:01', '11:00:02', '11:00:30'):
            txs = _tt(f"""
                BUYSELL 2024-12-02 10:00:00 ABC.TO 100 CAD 10 1000
                BUYSELL 2024-12-20 11:00:00 ABC.TO -50 CAD 8 400
                BUYSELL 2024-12-20 {t2} ABC.TO -30 CAD 8 240
                BUYSELL 2025-03-03 10:00:00 ABC.TO -20 CAD 10 200
            """)
            res, _ = _run(CanadaTaxRules(), txs)
            yrs = _by_year(res)
            self.assertAlmostEqual(yrs['2024'], -88.0, places=2, msg=t2)
            self.assertAlmostEqual(yrs['2025'], -72.0, places=2, msg=t2)

    def test_ca_bump_follows_pool_on_rename_day(self):
        # S069-14: a replacement booked under the OLD ticker on the
        # rename's own date stays in the OLD pool (the main pass runs
        # the SPLIT first); the bump must land there at any clock time.
        for tm in ('09:29:59', '09:30:00', '09:30:01', '15:00:00'):
            txs = _tt(f"""
                BUYSELL 2025-02-03 10:00:00 OLD.TO 100 CAD 20 2000
                BUYSELL 2025-03-03 10:00:00 OLD.TO -100 CAD 10 1000
                BUYSELL 2025-03-10 {tm} OLD.TO 100 CAD 11 1100
            """)
            txs.append(TaxTransaction(action='SPLIT', date='2025-03-10',
                                      time='09:30:00', symbol='OLD.TO',
                                      quantity=1.0, symbol_new='NEW.TO',
                                      account='margin', currency='CAD'))
            res, _ = _run(CanadaTaxRules(), txs)
            self.assertAlmostEqual(res['summary']['total_disallowed'], 1000.0,
                                   places=2, msg=tm)
            cost = sum(r['total_cost'] for r in res['inventory'])
            self.assertAlmostEqual(cost, 2100.0, places=2, msg=tm)


class TestSuperficialLossRules(unittest.TestCase):

    def test_cover_that_opens_long_counts_its_own_new_shares(self):
        # R1-29: one row (buy 150 while short 100) == two rows (100 + 50).
        one = _tt("""
            BUYSELL 2024-11-01 10:00:00 XYZ.TO -100 CAD 10 1000
            BUYSELL 2024-12-16 10:00:00 XYZ.TO 150 CAD 12 1800
            BUYSELL 2025-03-03 10:00:00 XYZ.TO -50 CAD 12 600
        """)
        two = _tt("""
            BUYSELL 2024-11-01 10:00:00 XYZ.TO -100 CAD 10 1000
            BUYSELL 2024-12-16 10:00:00 XYZ.TO 100 CAD 12 1200
            BUYSELL 2024-12-16 10:00:01 XYZ.TO 50 CAD 12 600
            BUYSELL 2025-03-03 10:00:00 XYZ.TO -50 CAD 12 600
        """)
        for txs in (one, two):
            res, _ = _run(CanadaTaxRules(), txs)
            yrs = _by_year(res)
            self.assertAlmostEqual(yrs['2024'], -100.0, places=2)
            self.assertAlmostEqual(yrs['2025'], -100.0, places=2)

    def _buyback(self, write_date, timing, since=2025, flag=False):
        txs = _tt(f"""
            BUYSELL {write_date} 10:00:00 ABC260320C00040000.TO -1 CAD 2 200
            BUYSELL 2025-01-10 10:00:00 ABC260320C00040000.TO 1 CAD 3 300
        """)
        rrsp = _tt("""
            BUYSELL 2025-01-13 10:00:00 ABC260320C00040000.TO 1 CAD 3 300
        """, account='rrsp')
        res, _ = _run(CanadaTaxRules(), txs, sheltered_transactions=rrsp,
                      option_premium_timing=timing, option_grant_since=since,
                      option_buyback_loss_superficial=flag)
        return res['summary']['total_disallowed']

    def test_buyback_flag_holds_under_every_timing(self):
        # R1-270 / R1-297: the default exempts a written-option buy-back
        # loss whether the lot is grant-timed, a pre-since transition lot
        # or on close timing.
        self.assertAlmostEqual(self._buyback('2025-01-02', 'grant'), 0.0)
        self.assertAlmostEqual(self._buyback('2024-12-20', 'grant'), 0.0)
        self.assertAlmostEqual(self._buyback('2025-01-02', 'close'), 0.0)
        # The strict reading still applies when opted in.
        self.assertAlmostEqual(
            self._buyback('2025-01-02', 'close', flag=True), 100.0)

    def test_blended_cover_test_uses_the_pooled_balance(self):
        # S069-15: A holds 100; B sells 50 out of the pooled ACB at a
        # loss and rebuys 50 -> denied, exactly as in one account.
        a = _tt("BUYSELL 2025-01-02 10:00:00 XYZ.TO 100 CAD 50 5000",
                account='acctA')
        b = _tt("""
            BUYSELL 2025-03-03 10:00:00 XYZ.TO -50 CAD 40 2000
            BUYSELL 2025-03-10 10:00:00 XYZ.TO 50 CAD 41 2050
        """, account='acctB')
        res, _ = _run(CanadaTaxRules(), a + b)
        self.assertAlmostEqual(res['summary']['total_disallowed'], 500.0,
                               places=2)

    def test_contract_expired_inside_window_is_not_held(self):
        # S071-17: no EXP row; the rebuy expired 12-19 < day 30 (12-31).
        txs = _tt("""
            BUYSELL 2025-11-03 10:00:00 ZZQ251219C00015000.TO 1 CAD 2.01 201
            BUYSELL 2025-12-01 10:00:00 ZZQ251219C00015000.TO -1 CAD 0.49 49
            BUYSELL 2025-12-05 10:00:00 ZZQ251219C00015000.TO 1 CAD 0.41 41
        """)
        res, _ = _run(CanadaTaxRules(), txs)
        self.assertAlmostEqual(res['summary']['total_disallowed'], 0.0)
        self.assertAlmostEqual(res['summary']['total_gain'], -152.0,
                               places=2)

    def test_warrant_bought_in_window_is_named(self):
        # S071-14: a warrant is a right to acquire the shares; the loss
        # is flagged for review (the shares per warrant are unknown).
        txs = _tt("""
            BUYSELL 2025-01-10 10:00:00 SLH.TO 100 CAD 10 1000
            BUYSELL 2025-03-03 10:00:00 SLH.TO -100 CAD 8 800
            BUYSELL 2025-03-10 10:00:00 SLH.WT.TO 100 CAD 1 100
        """)
        res, err = _run(CanadaTaxRules(), txs)
        self.assertIn('right_vs_share_loss', err)
        self.assertIn('SLH.WT.TO', err)
        self.assertEqual([w['option_symbol']
                          for w in res['option_replacement_warnings']],
                         ['SLH.WT.TO'])
        res, err = _run(USATaxRules(), [
            TaxTransaction(**{**t.to_dict(), 'id': None,
                              'symbol': t.symbol.replace('.TO', '.US'),
                              'currency': 'USD'}) for t in txs])
        self.assertIn('right_vs_share_loss', err)

    def test_right_underlying_spellings(self):
        from taxjson.lib.core import right_underlying
        self.assertEqual(right_underlying('SLH.WT.TO'), 'SLH.TO')
        self.assertEqual(right_underlying('ABC.WT.A.TO'), 'ABC.TO')
        self.assertEqual(right_underlying('CSU.RT.TO'), 'CSU.TO')
        self.assertEqual(right_underlying('XYZ.WS.US'), 'XYZ.US')
        self.assertIsNone(right_underlying('RCI.B.TO'))
        self.assertIsNone(right_underlying('BRK.B.US'))
        self.assertIsNone(right_underlying('SNOW.US'))

    def test_us_other_scope_buy_to_close_is_not_a_replacement(self):
        # S070-10 case A: the IRA buys back its own written call.
        txs = _tt("""
            BUYSELL 2025-02-03 10:00:00 XYZ250620C00050000.US 1 USD 5 500
            BUYSELL 2025-03-03 10:00:00 XYZ250620C00050000.US -1 USD 2 200
        """)
        ira = _tt("""
            BUYSELL 2025-01-10 10:00:00 XYZ250620C00050000.US -1 USD 3 300
            BUYSELL 2025-03-10 10:00:00 XYZ250620C00050000.US 1 USD 2 200
        """, account='ira')
        res, _ = _run(USATaxRules(), txs, sheltered_transactions=ira)
        self.assertAlmostEqual(res['summary']['total_gain'], -300.0,
                               places=2)
        # Case B: an affiliated buy-to-cover.
        txs = _tt("""
            BUYSELL 2025-01-02 10:00:00 XYZ.US 100 USD 20 2000
            BUYSELL 2025-03-03 10:00:00 XYZ.US -100 USD 10 1000
        """)
        sp = _tt("""
            BUYSELL 2025-02-20 10:00:00 XYZ.US -100 USD 12 1200
            BUYSELL 2025-03-10 10:00:00 XYZ.US 100 USD 11 1100
        """, account='spouse')
        res, _ = _run(USATaxRules(), txs, affiliated_transactions=sp)
        self.assertAlmostEqual(res['summary']['total_gain'], -1000.0,
                               places=2)


class TestExplainText(unittest.TestCase):

    def test_permanent_denial_is_called_permanent(self):
        # R1-159: an RRSP rebuy's denial is permanent, not an ACB bump;
        # the window states the per-holder test, not the retired
        # class-wide one, and never claims "earliest is chosen".
        from taxjson.lib.trace_format import render_gain_block
        txs = _tt("""
            BUYSELL 2026-01-05 10:00:00 ABC.TO 100 CAD 20 2000
            BUYSELL 2026-02-10 10:00:00 ABC.TO 10 CAD 19 190
            BUYSELL 2026-03-02 10:00:00 ABC.TO -110 CAD 10 1100
        """)
        rrsp = _tt("BUYSELL 2026-03-10 10:00:00 ABC.TO 100 CAD 10 1000",
                   account='rrsp')
        res, _ = _run(CanadaTaxRules(), txs, sheltered_transactions=rrsp,
                      trace=True)
        g = [r for r in res['transactions'] if r['gain'] is not None
             and r.get('disallowed_amount')][0]
        text = "\n".join(render_gain_block(g))
        self.assertIn('PERMANENTLY denied', text)
        self.assertNotIn('forwards the disallowed loss', text)
        self.assertNotIn('across ALL accounts must be zero', text)
        self.assertNotIn('earliest is chosen', text)
        self.assertIn('per holder', text)

    def test_grant_timed_buyback_trace_foots_to_booked_gain(self):
        # S069-10: write @3, buy back @4 under grant timing books -400
        # at cost 0; the trace line must say so.
        txs = _tt("""
            BUYSELL 2025-02-03 10:00:00 ABC250321C00050000.TO -1 CAD 3 300
            BUYSELL 2025-02-20 10:00:00 ABC250321C00050000.TO 1 CAD 4 400
        """)
        res, _ = _run(CanadaTaxRules(), txs, trace=True,
                      option_premium_timing='grant', option_grant_since=2025)
        rec = [r for r in res['transactions']
               if r['date'] == '2025-02-20'][0]
        self.assertAlmostEqual(rec['gain'], -400.0, places=2)
        line = [l for l in rec['trace'] if '2025-02-20' in l][-1]
        self.assertIn('Cost_Basis:     0.0000', line)
        self.assertIn('Gain:  -400.0000', line)
        self.assertIn('Premium_Recognized_At_Write', line)


def _cli(module, *args):
    import subprocess
    import sys
    return subprocess.run([sys.executable, '-m', module, *map(str, args)],
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestPhantomRowsInTracesAndExplain(unittest.TestCase):
    """R1-165 / S029-22: a phantom-backed (tainted) sale is shown as a
    manual-reporting row — never as an ordinary gain with a 1970
    holding period — in the traces file and in taxjson-explain."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        rows = _tt("""
            BUYSELL 2025-03-03 10:00:00 OLD.TO -100 CAD 10 1000
            BUYSELL 2025-04-01 10:00:00 OLD.TO 50 CAD 9 450
            BUYSELL 2025-05-01 10:00:00 OLD.TO -50 CAD 12 600
            BUYSELL 2025-04-01 10:00:00 NEW.TO 10 CAD 5 50
            BUYSELL 2025-05-01 10:00:00 NEW.TO -10 CAD 6 60
        """)
        self.base = self.tmp / 'margin_base.json'
        self.base.write_text(json.dumps([t.to_dict() for t in rows]))
        self.ph = self.tmp / 'phantoms.json'
        self.ph.write_text(json.dumps([{'symbol': 'OLD.TO',
                                        'account': 'margin'}]))

    def test_traces_file_matches_gains_json(self):
        import json
        tr = self.tmp / 'margin.traces'
        p = _cli('taxjson.bin.taxjson_gains', '--country', 'canada',
                 '--year', '2025', '--taxable', '--full-traces', tr,
                 '--incomplete-history', self.ph, self.base)
        self.assertEqual(p.returncode, 0, p.stderr)
        doc = json.loads(p.stdout)
        text = tr.read_text()
        self.assertAlmostEqual(doc['summary']['total_gain'], 160.0)
        self.assertIn('total gain          +$160.00', text)
        self.assertIn('MANUAL REPORTING', text)
        self.assertNotRegex(text, r'days_held=2\d{4}')
        self.assertIsNone(doc['manual_reporting_required'][0]['days_held'])

    def test_explain_marks_phantom_rows(self):
        p = _cli('taxjson.bin.taxjson_explain', '--list',
                 '--incomplete-history', self.ph, self.base)
        self.assertEqual(p.returncode, 0, p.stderr)
        line = [l for l in p.stdout.splitlines()
                if 'OLD.TO' in l and '2025-03-03' in l][0]
        self.assertIn('MANUAL REPORTING', line)
        self.assertNotIn('gain=', line)

    def test_explain_no_wash_for_registered_books(self):
        # S029-21: --no-wash reproduces the pipeline's sheltered books.
        import json
        rows = _tt("""
            BUYSELL 2025-01-06 10:00:00 BBB.TO 100 CAD 20 2000
            BUYSELL 2025-03-03 10:00:00 BBB.TO -100 CAD 10 1000
            BUYSELL 2025-03-10 10:00:00 BBB.TO 100 CAD 10 1000
        """, account='tfsa')
        f = self.tmp / 'tfsa_base.json'
        f.write_text(json.dumps([t.to_dict() for t in rows]))
        p = _cli('taxjson.bin.taxjson_explain', '--list', '--wash-sales', f)
        self.assertIn('WASH+1000.00', p.stdout)
        p = _cli('taxjson.bin.taxjson_explain', '--list', '--wash-sales',
                 '--no-wash', f)
        self.assertNotIn('WASH+', p.stdout)

    def test_affiliated_help_excludes_related_persons(self):
        # S033-15.
        for mod in ('taxjson.bin.taxjson_gains', 'taxjson.bin.taxjson_explain'):
            out = ' '.join(_cli(mod, '--help').stdout.split())
            self.assertNotIn('related person,', out)
            self.assertIn('not affiliated', out.lower())


class TestReturnOfCapital(unittest.TestCase):

    def _adj(self, date, amt, sym='RST.TO'):
        return TaxTransaction(action='ADJUST', date=date, time='09:30:00',
                              symbol=sym, currency='CAD', net_amount=amt,
                              account='margin', type='roc')

    def test_deemed_gain_has_no_proceeds(self):
        # R1-43: s.40(3) deemed gain -> 13199 = 0, 13200 = the gain.
        txs = _tt("BUYSELL 2013-05-01 10:00:00 RST.TO 100 CAD 10 1000")
        txs += [self._adj('2020-06-01', -1000.0),
                self._adj('2025-05-15', -100.0)]
        res, _ = _run(CanadaTaxRules(), txs)
        row = [r for r in res['transactions'] if r.get('deemed')][0]
        self.assertAlmostEqual(row['gain'], 100.0)
        self.assertAlmostEqual(row['proceeds'], 0.0)
        # Empty-pool branch too.
        txs = _tt("""
            BUYSELL 2024-02-01 10:00:00 RST.TO 100 CAD 10 1000
            BUYSELL 2025-02-03 10:00:00 RST.TO -100 CAD 10 1000
        """) + [self._adj('2025-03-31', -80.0)]
        res, _ = _run(CanadaTaxRules(), txs)
        row = [r for r in res['transactions'] if r.get('deemed')][0]
        self.assertAlmostEqual(row['gain'], 80.0)
        self.assertAlmostEqual(row['proceeds'], 0.0)

    def test_roc_on_short_pool_is_a_compensation_payment(self):
        # R1-157: short 100 @20, ROC 300 debited, cover @15 -> +200 (cash).
        txs = _tt("""
            BUYSELL 2025-01-06 10:00:00 TRU.TO -100 CAD 20 2000
            BUYSELL 2025-04-01 10:00:00 TRU.TO 100 CAD 15 1500
        """, account='margin') + [self._adj('2025-02-15', 300.0, 'TRU.TO')]
        res, err = _run(CanadaTaxRules(), txs)
        self.assertAlmostEqual(res['summary']['total_gain'], 200.0,
                               places=2)
        self.assertIn('SHORT position', err)

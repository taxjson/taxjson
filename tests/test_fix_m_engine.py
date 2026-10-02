"""Regression tests for the engine-area MEDIUM audit fixes (fixm/engine).

Synthetic data only; account labels and tickers are fake.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules
from tax_rules import rule


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

    @rule("CA-OPT-08")
    def test_ca_opposite_direction_same_moment(self):
        # R1-28: put premium off the put shares' cost (5800), call
        # premium onto the call shares' proceeds (6200), whichever
        # ASSIGN row is staged first. (These legs sit at their strikes,
        # so the identity pairing settles them; the direction filter
        # itself is pinned by the unpaired-legs tests below.)
        base = self.TWO_ASSIGN.strip().splitlines()
        swapped = base[:3] + [base[4], base[3]] + base[5:]
        for rows in (self.TWO_ASSIGN, "\n".join(swapped)):
            res, _ = _run(CanadaTaxRules(), _tt(rows))
            sale = [r for r in _rows(res, 'QZX.US')
                    if r['date'] == '2025-12-19'][0]
            self.assertAlmostEqual(sale['proceeds'], 6200.0, places=2)
            yrs = _by_year(res)
            self.assertAlmostEqual(yrs['2025'], 800.0, places=2)
            self.assertAlmostEqual(yrs['2026'], 600.0, places=2)

    # Two same-moment assignments whose stock legs are NOT identifiable
    # (filled in 60 + 40 share pieces, off the strike): the identity
    # pairing leaves them to the ledger's proximity rule, where only the
    # share DIRECTION tells the put's buys from the call's sells (core
    # _AssignPremiumLedger.take: `same = [e for e in cands if e['dir']
    # in (None, sign)]`, staged with `'dir': self._direction(opt_tx)`).
    # Re-audit A2-0936: with either line mutated, the put premium (300)
    # went onto the first SELL piece when the put was staged first.
    UNPAIRED_LEGS = """
        BUYSELL 2024-06-03 10:00:00 QZX.US 100 USD 50 5000
        BUYSELL 2025-11-03 10:00:00 QZX251219P00061000.US -1 USD 3 300
        BUYSELL 2025-11-03 10:00:00 QZX251219C00060000.US -1 USD 2 200
        ASSIGN 2025-12-19 16:20:00 QZX251219P00061000.US 1 USD 0 0
        ASSIGN 2025-12-19 16:20:00 QZX251219C00060000.US 1 USD 0 0
        BUYSELL 2025-12-19 16:20:01 QZX.US -60 USD 59.9 3594
        BUYSELL 2025-12-19 16:20:02 QZX.US 60 USD 61.1 3666
        BUYSELL 2025-12-19 16:20:03 QZX.US -40 USD 59.9 2396
        BUYSELL 2025-12-19 16:20:04 QZX.US 40 USD 61.1 2444
        BUYSELL 2026-02-02 10:00:00 QZX.US -100 USD 60 6000
    """

    def _unpaired_orders(self):
        base = self.UNPAIRED_LEGS.strip().splitlines()
        return (self.UNPAIRED_LEGS,
                "\n".join(base[:3] + [base[4], base[3]] + base[5:]))

    @rule("CA-OPT-08")
    def test_ca_unpaired_legs_take_their_own_direction(self):
        # Call premium 200 onto the SELL pieces (5990 + 200 = 6190);
        # put premium 300 off the BUY pieces (6110 - 300 = 5810).
        for rows in self._unpaired_orders():
            res, _ = _run(CanadaTaxRules(), _tt(rows))
            sells = [r for r in _rows(res, 'QZX.US')
                     if r['date'] == '2025-12-19']
            self.assertEqual([r['proceeds'] for r in sells],
                             [3714.0, 2476.0])
            yrs = _by_year(res)
            self.assertAlmostEqual(yrs['2025'], 995.6, places=2)
            self.assertAlmostEqual(yrs['2026'], 384.4, places=2)

    @rule("US-OPT-05")
    def test_us_unpaired_legs_take_their_own_direction(self):
        for rows in self._unpaired_orders():
            res, _ = _run(USATaxRules(), _tt(rows))
            sells = [r for r in _rows(res, 'QZX.US')
                     if r['date'] == '2025-12-19']
            self.assertEqual([r['proceeds'] for r in sells],
                             [3714.0, 2476.0])
            yrs = _by_year(res)
            self.assertAlmostEqual(yrs['2025'], 1190.0, places=2)
            self.assertAlmostEqual(yrs['2026'], 190.0, places=2)

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

    @rule("CA-DISP-05")
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

    @rule("CA-SL-11", "CA-SL-12")
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
        p = _cli('taxjson.bin.taxjson_explain', '--country', 'canada', '--list',
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
        p = _cli('taxjson.bin.taxjson_explain', '--country', 'canada', '--list', '--wash-sales', f)
        self.assertIn('WASH+1000.00', p.stdout)
        p = _cli('taxjson.bin.taxjson_explain', '--country', 'canada', '--list', '--wash-sales',
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


class TestInputAndSigns(unittest.TestCase):

    def test_trade_row_missing_money_or_quantity_is_refused(self):
        # R1-162: taxjson-gains refuses; taxjson-validate reports it.
        import json
        import tempfile
        from pathlib import Path
        for drop in ('net_amount', 'quantity'):
            rows = [t.to_dict() for t in _tt("""
                BUYSELL 2025-01-02 10:00:00 ZZQ.TO 100 CAD 10 1000
                BUYSELL 2025-02-03 10:00:00 ZZQ.TO -100 CAD 12 1200
            """)]
            rows[0].pop(drop)
            p = Path(tempfile.mkdtemp()) / 'b.json'
            p.write_text(json.dumps(rows))
            r = _cli('taxjson.bin.taxjson_gains', '--country', 'canada',
                     '--taxable', p)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn(drop, r.stderr)
            r = _cli('taxjson.bin.taxjson_validate', p)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(f"Missing '{drop}'", r.stdout + r.stderr)

    def test_us_sell_with_net_debit_keeps_its_sign(self):
        # R1-164: close at $0.01 with a 1.25 commission -> proceeds -0.25.
        txs = _tt("""
            BUYSELL 2025-02-03 10:00:00 ABC250321C00010000.US 1 USD 2.2 220
            BUYSELL 2025-03-03 10:00:00 ABC250321C00010000.US -1 USD 0.01 -0.25
        """)
        res, _ = _run(USATaxRules(), txs)
        self.assertAlmostEqual(res['transactions'][0]['proceeds'], -0.25)
        self.assertAlmostEqual(res['transactions'][0]['gain'], -220.25)
        txs = _tt("""
            BUYSELL 2025-02-03 10:00:00 ABC250321C00010000.US -1 USD 0.01 -0.25
            BUYSELL 2025-03-03 10:00:00 ABC250321C00010000.US 1 USD 0.3 30
        """)
        res, _ = _run(USATaxRules(), txs)
        self.assertAlmostEqual(res['summary']['total_gain'], -30.25)


class TestGrantTiming(unittest.TestCase):

    def test_year_end_write_grant_record_lands_in_settle_year(self):
        # R1-304: the s.49(1) grant record of a Dec-31 write settling in
        # January belongs to the settle year (the documented basis).
        txs = _tt("""
            BUYSELL 2025-12-31 10:00:00 ABC260320C00050000.TO -2 CAD 3 599 0 2026-01-02
        """)
        res, _ = _run(CanadaTaxRules(), txs, option_premium_timing='grant',
                      option_grant_since=2025)
        rec = [r for r in res['transactions'] if r.get('grant')][0]
        self.assertEqual(rec['date_settle'], '2026-01-02')
        self.assertEqual(_by_year(res), {'2026': 599.0})

    def test_grant_since_follows_the_tax_date_basis(self):
        # S068-21: tax_date = trade -> a 2024-12-31 write (settling 2025)
        # is a pre-since contract on close timing: 2025 books +30.
        txs = _tt("""
            BUYSELL 2024-12-31 10:00:00 ABC250321C00050000.TO -1 CAD 0.5 50 0 2025-01-02
            BUYSELL 2025-03-03 10:00:00 ABC250321C00050000.TO 1 CAD 0.2 20 0 2025-03-04
        """)
        res, _ = _run(CanadaTaxRules(), txs, option_premium_timing='grant',
                      option_grant_since=2025, option_grant_basis='trade')
        self.assertFalse(any(r.get('grant') for r in res['transactions']))
        by_trade = {}
        for r in res['transactions']:
            by_trade[r['date'][:4]] = round(by_trade.get(r['date'][:4], 0.0)
                                            + r['gain'], 2)
        self.assertEqual(by_trade, {'2025': 30.0})
        # Settle basis (default): the write settles in 2025 -> grant.
        res, _ = _run(CanadaTaxRules(), txs, option_premium_timing='grant',
                      option_grant_since=2025)
        self.assertTrue(any(r.get('grant') for r in res['transactions']))

    def test_quoted_booleans_are_refused_everywhere(self):
        # S021-07 / S076-17.
        from taxjson.lib.config_check import account_type_problems
        from taxjson.lib.pipeline import option_timing_from_settings
        cfg = {'settings': {'option_buyback_loss_superficial': 'false',
                            'country': 'canada'},
               'accounts': {'rbc': {'type': 'taxable', 'crypto': 'false'}}}
        probs = account_type_problems(cfg)
        self.assertEqual(len(probs), 2, probs)
        with self.assertRaises(ValueError):
            option_timing_from_settings(cfg['settings'])
        ok = {'settings': {'option_buyback_loss_superficial': False,
                           'country': 'canada'},
              'accounts': {'rbc': {'type': 'taxable', 'crypto': False}}}
        self.assertEqual(account_type_problems(ok), [])
        self.assertFalse(option_timing_from_settings(ok['settings'])
                         ['option_buyback_loss_superficial'])

    def test_standalone_close_default_is_announced(self):
        # R1-177.
        import json
        import tempfile
        from pathlib import Path
        p = Path(tempfile.mkdtemp()) / 'b.json'
        p.write_text(json.dumps([t.to_dict() for t in _tt(
            "BUYSELL 2025-01-02 10:00:00 ZZQ.TO 100 CAD 10 1000")]))
        r = _cli('taxjson.bin.taxjson_gains', '--country', 'canada', p)
        self.assertIn('--option-premium-timing not given', r.stderr)
        r = _cli('taxjson.bin.taxjson_gains', '--country', 'canada',
                 '--option-premium-timing', 'grant', p)
        self.assertNotIn('--option-premium-timing not given', r.stderr)
        r = _cli('taxjson.bin.taxjson_explain', '--country', 'canada', '--list', p)
        self.assertIn('--option-premium-timing not given', r.stderr)


class TestMergerFold(unittest.TestCase):

    def test_merger_booked_on_two_dates_folds_into_one_event(self):
        # S071-01: IB books 15 OLD -> 15 NEW on 06-11, RBC 40 -> 41 on
        # 06-15; the pool must end at exactly 56 NEW, every share sold.
        def split(acct, d, r):
            return TaxTransaction(action='SPLIT', date=d, time='09:30:00',
                                  symbol='OLD.US', symbol_new='NEW.US',
                                  quantity=r, currency='USD', account=acct)
        for d2 in ('2026-06-11', '2026-06-15'):
            txs = (_tt("""
                BUYSELL 2026-01-05 10:00:00 OLD.US 15 USD 10 150
                BUYSELL 2026-08-03 10:00:00 NEW.US -15 USD 30 450
            """, account='ib') + _tt("""
                BUYSELL 2026-01-06 10:00:00 OLD.US 40 USD 10 400
                BUYSELL 2026-08-04 10:00:00 NEW.US -41 USD 30 1230
            """, account='rbc') + [split('ib', '2026-06-11', 1.0),
                                   split('rbc', d2, 1.025)])
            res, err = _run(CanadaTaxRules(), txs)
            self.assertAlmostEqual(res['summary']['total_gain'], 1130.0,
                                   places=2, msg=d2)
            self.assertIn('holdings-weighted ratio', err)
            self.assertFalse([r for r in res['inventory']
                              if abs(r['qty']) > 1e-6], d2)


class TestDiagnosticsReachTheUser(unittest.TestCase):

    def test_superficial_loss_warnings_printed_and_split(self):
        # R1-325: a clean loss next to a phantom-basis sale.
        import json
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        rows = _tt("""
            BUYSELL 2025-05-02 10:00:00 ABC.TO -100 CAD 10 1000
            BUYSELL 2025-05-12 10:00:00 ABC.TO 100 CAD 12 1200
            BUYSELL 2025-05-22 10:00:00 ABC.TO -100 CAD 10 1000
        """)
        base = tmp / 'margin_base.json'
        base.write_text(json.dumps([t.to_dict() for t in rows]))
        ph = tmp / 'phantoms.json'
        ph.write_text(json.dumps([{'symbol': 'ABC.TO', 'account': 'margin'}]))
        r = _cli('taxjson.bin.taxjson_gains', '--country', 'canada',
                 '--year', '2025', '--taxable', '--incomplete-history', ph,
                 base)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('warning: superficial-loss check (manual)', r.stderr)
        from taxjson.bin.taxjson_split_gains import split_for_account
        doc = json.loads(r.stdout)
        for w in doc['superficial_loss_warnings']:
            w.setdefault('account', 'margin')
        part = split_for_account(doc, 'margin', [t.to_dict() for t in rows])
        self.assertTrue(part.get('superficial_loss_warnings'))

    def test_us_own_account_move_keeps_ira_balance(self):
        # S070-11: an ira -> rothira move netted by the tool itself is not
        # "missing acquisition history".
        main = _tt("""
            BUYSELL 2024-02-01 10:00:00 ABC.US 10 USD 50 500
            BUYSELL 2024-09-02 10:00:00 ABC.US -10 USD 55 550
        """)
        sh = _tt("BUYSELL 2024-01-10 10:00:00 XYZ.US 100 USD 10 1000",
                 account='ira')
        sh.append(TaxTransaction(action='TRANSFER', date='2024-03-01',
                                 time='09:30:00', symbol='XYZ.US',
                                 quantity=-100, currency='USD',
                                 account='ira', type='own_account_move'))
        sh.append(TaxTransaction(action='TRANSFER', date='2024-03-01',
                                 time='09:30:00', symbol='XYZ.US',
                                 quantity=100, currency='USD',
                                 account='rothira', type='own_account_move'))
        sh += _tt("BUYSELL 2024-05-01 10:00:00 XYZ.US -100 USD 12 1200",
                  account='rothira')
        res, err = _run(USATaxRules(), main, sheltered_transactions=sh)
        self.assertNotIn('beyond its recorded balance', err)


class TestPerAccountSplit(unittest.TestCase):
    """taxjson-split-gains apportioning a blended Canada pool."""

    def _row(self, sym, qty, cost, **kw):
        return {'symbol': sym, 'qty': qty, 'total_cost': cost,
                'currency': 'CAD', **kw}

    def _b(self, content, account):
        return [t.to_dict() for t in _tt(content, account=account)]

    def test_deferred_wash_is_apportioned(self):
        # R1-160: 1000 deferred in a 100-share pool held 50/50.
        from taxjson.bin.taxjson_split_gains import split_for_account
        comb = {'inventory': [self._row('XYZ.TO', 100, 2000.0,
                                        deferred_wash=1000.0)]}
        tot = 0.0
        for a in ('a', 'b'):
            base = self._b("BUYSELL 2025-01-02 10:00:00 XYZ.TO 50 CAD 20 1000", a)
            inv = split_for_account(comb, a, base)['inventory']
            self.assertAlmostEqual(inv[0]['deferred_wash'], 500.0)
            tot += inv[0]['deferred_wash']
        self.assertAlmostEqual(tot, 1000.0)

    def test_phantom_openings_reach_the_account(self):
        # R1-275 / R1-322: a phantom opening of 380 is in the pool but
        # not in the base book.
        from taxjson.bin.taxjson_split_gains import split_for_account
        comb = {'inventory': [self._row('SPY.US', 0.6724, 581.35),
                              self._row('BK.TO', 1000, 10766.95)],
                'phantom_application_log': [
                    {'symbol': 'SPY.US', 'account': 'margin',
                     'opening_qty': 380.0, 'inserted': True,
                     'anchor_date': '2024-01-05', 'anchor_symbol': 'SPY.US'},
                    {'symbol': 'BK.TO', 'account': 'margin',
                     'opening_qty': 1000.0, 'inserted': True,
                     'anchor_date': '2024-04-15', 'anchor_symbol': 'BK.TO'}]}
        base = self._b("""
            BUYSELL 2024-01-05 10:00:00 SPY.US -380 CAD 500 190000
            BUYSELL 2024-06-03 10:00:00 SPY.US 0.6724 CAD 864 581.35
        """, 'margin')
        inv = {r['symbol']: r for r in
               split_for_account(comb, 'margin', base)['inventory']}
        self.assertAlmostEqual(inv['SPY.US']['qty'], 0.6724, places=4)
        self.assertAlmostEqual(inv['SPY.US']['total_cost'], 581.35, places=2)
        self.assertAlmostEqual(inv['BK.TO']['qty'], 1000.0)

    def test_settle_lagged_sale_across_a_split(self):
        # S050-18: margX sold 500 pre-split (settling after the split).
        from taxjson.bin.taxjson_split_gains import split_for_account
        comb = {'inventory': [self._row('ABC.TO', 3000, 150000.0)]}

        def book(a, sell):
            rows = self._b("BUYSELL 2025-01-02 10:00:00 ABC.TO 1000 CAD 100 100000", a)
            if sell:
                rows += self._b("BUYSELL 2025-06-10 10:00:00 ABC.TO -500 CAD 110 55000 0 2025-06-11", a)
            rows.append(TaxTransaction(action='SPLIT', date='2025-06-10',
                                       time='20:25:00', symbol='ABC.TO',
                                       quantity=2.0, currency='CAD',
                                       account=a).to_dict())
            return rows
        x = split_for_account(comb, 'margX', book('margX', True))['inventory']
        y = split_for_account(comb, 'margY', book('margY', False))['inventory']
        self.assertAlmostEqual(x[0]['qty'], 1000.0)
        self.assertAlmostEqual(x[0]['total_cost'], 50000.0)
        self.assertAlmostEqual(y[0]['qty'], 2000.0)


class TestPhantomWalks(unittest.TestCase):
    """phantom_holdings: detect / relevance / zero-basis / openings."""

    def _cand(self, sym, acct='margin'):
        from taxjson.lib.phantom_holdings import PhantomCandidate
        return PhantomCandidate(symbol=sym, account=acct, currency='CAD',
                                first_negative_date='2024-01-05',
                                peak_short=-100.0, end_position=0.0,
                                disposition_count=1, registered=False)

    def _split(self, sym, d, tm, ratio, new='', acct='margin'):
        return TaxTransaction(action='SPLIT', date=d, time=tm, symbol=sym,
                              quantity=ratio, symbol_new=new,
                              currency='CAD', account=acct)

    def test_evening_split_after_settle_lagged_buy(self):
        # S021-00: the buy executed before IB's 20:25 split is re-
        # denominated by the engine; the walk must not invent a short.
        from taxjson.lib.phantom_holdings import detect_phantoms
        txs = _tt("""
            BUYSELL 2026-04-01 10:00:00 DEF.TO 100 CAD 10 1000 0 2026-04-02
            BUYSELL 2026-05-01 10:00:00 DEF.TO -200 CAD 6 1200 0 2026-05-02
        """) + [self._split('DEF.TO', '2026-04-01', '20:25:00', 2.0)]
        self.assertEqual(detect_phantoms(txs), [])

    def test_cover_of_carried_short_affects_the_year(self):
        # S021-01 / S076-07.
        from taxjson.lib.phantom_holdings import assess_tax_year_relevance
        txs = _tt("""
            BUYSELL 2024-03-04 10:00:00 SPY.US -300 USD 400 120000
            BUYSELL 2025-02-03 10:00:00 SPY.US 300 USD 500 150000
        """)
        r = assess_tax_year_relevance(txs, [self._cand('SPY.US')], 2025)
        self.assertTrue(r[0].affects_year)

    def test_relevance_ignores_tie_order_and_follows_renames(self):
        # S075-12: a same-moment sell+buy pair; S075-13: a clean sale
        # after a rename.
        from taxjson.lib.phantom_holdings import assess_tax_year_relevance
        base = _tt("""
            BUYSELL 2024-01-05 10:00:00 XYZ.TO -50 CAD 10 500
            BUYSELL 2024-02-01 10:00:00 XYZ.TO 50 CAD 10 500
        """)
        tie = _tt("""
            BUYSELL 2025-03-03 09:30:00 XYZ.TO -100 CAD 11 1100
            BUYSELL 2025-03-03 09:30:00 XYZ.TO 100 CAD 10 1000
        """)
        for rows in (base + tie, base + tie[::-1]):
            r = assess_tax_year_relevance(rows, [self._cand('XYZ.TO')], 2025)
            self.assertFalse(r[0].affects_year)
        ren = _tt("""
            BUYSELL 2024-01-02 10:00:00 OLD.TO 50 CAD 10 500
            BUYSELL 2024-03-01 10:00:00 NEW.TO -150 CAD 7 1050
            BUYSELL 2024-04-01 10:00:00 NEW.TO 150 CAD 7 1050
            BUYSELL 2025-05-01 10:00:00 NEW.TO -60 CAD 7 420
        """) + [self._split('OLD.TO', '2024-02-01', '00:00:01', 2.0,
                            'NEW.TO')]
        r = assess_tax_year_relevance(ren, [self._cand('NEW.TO')], 2025)
        self.assertFalse(r[0].affects_year)

    def test_year_is_the_settle_year(self):
        # S075-16: traded 2024-12-31, settles 2025-01-02.
        from taxjson.lib.phantom_holdings import (
            assess_tax_year_relevance, detect_zero_basis_acquisitions)
        txs = _tt("""
            BUYSELL 2024-12-31 10:00:00 PPPX.US -100 USD 10 1000 0 2025-01-02
        """)
        self.assertTrue(assess_tax_year_relevance(
            txs, [self._cand('PPPX.US')], 2025)[0].affects_year)
        self.assertFalse(assess_tax_year_relevance(
            txs, [self._cand('PPPX.US')], 2024)[0].affects_year)
        z = _tt("""
            BUYSELL 2024-03-01 10:00:00 ZZZX.US 100 USD 0 0
            BUYSELL 2024-12-31 10:00:00 ZZZX.US -100 USD 14 1434.59 0 2025-01-02
        """)
        rows = detect_zero_basis_acquisitions(z, 2025)
        self.assertTrue(rows and rows[0].affects_year)

    def test_zero_basis_follows_a_rename(self):
        # S075-19.
        from taxjson.lib.phantom_holdings import detect_zero_basis_acquisitions
        txs = _tt("""
            BUYSELL 2025-01-10 10:00:00 SPN.TO 100 CAD 0 0
            BUYSELL 2025-06-02 10:00:00 NSPN.TO -100 CAD 5 500
        """) + [self._split('SPN.TO', '2025-03-01', '00:00:01', 1.0,
                            'NSPN.TO')]
        rows = detect_zero_basis_acquisitions(txs, 2025)
        self.assertEqual([r.symbol for r in rows], ['NSPN.TO'])

    def test_phantom_symbol_case_and_unmatched_pair(self):
        # S075-24 / S076-00.
        import json
        import tempfile
        from pathlib import Path
        from taxjson.lib.phantom_holdings import (load_phantoms,
                                                  synthesize_openings)
        f = Path(tempfile.mkdtemp()) / 'phantoms.json'
        f.write_text(json.dumps([{'symbol': 'xyz.to', 'account': 'margin'},
                                 {'symbol': 'NOPE.TO', 'account': 'margin'}]))
        ph = load_phantoms(f)
        self.assertIn(('XYZ.TO', 'margin'), ph)
        txs = _tt("BUYSELL 2025-03-03 10:00:00 XYZ.TO -100 CAD 20 2000")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # warn=True: the pipeline reports the project-level result
            # once (report_phantom_log); a direct caller can ask here.
            _, log = synthesize_openings(txs, ph, warn=True)
        by = {e['symbol']: e for e in log}
        self.assertTrue(by['XYZ.TO']['inserted'])
        self.assertIn('no rows', by['NOPE.TO']['note'])
        self.assertIn('NOPE.TO', err.getvalue())

    def test_opening_size_ignores_tie_order(self):
        # S076-04.
        from taxjson.lib.phantom_holdings import synthesize_openings
        head = _tt("BUYSELL 2025-02-03 10:00:00 PPP.TO -100 CAD 20 2000")
        tie = _tt("""
            BUYSELL 2025-06-02 09:30:00 PPP.TO -50 CAD 22 1100
            BUYSELL 2025-06-02 09:30:00 PPP.TO 50 CAD 21 1050
        """)
        sizes = set()
        for rows in (head + tie, head + tie[::-1]):
            _, log = synthesize_openings(rows, {('PPP.TO', 'margin')})
            sizes.add(log[0]['opening_qty'])
        self.assertEqual(sizes, {100.0})

    def test_rename_chain_both_ends_listed_is_deterministic(self):
        # S021-04: same numbers under every PYTHONHASHSEED.
        import os
        import subprocess
        import sys
        code = (
            "import json,sys\n"
            "sys.path.insert(0, %r)\n"
            "from test_fix_m_engine import _tt\n"
            "from taxjson.lib.core import TaxTransaction\n"
            "from taxjson.lib.phantom_holdings import synthesize_openings\n"
            "t = _tt('''BUYSELL 2025-01-10 10:00:00 OLDX.TO -10 CAD 10 100\n"
            "BUYSELL 2025-02-10 10:00:00 OLDX.TO 20 CAD 10 200\n"
            "BUYSELL 2025-05-10 10:00:00 NEWX.TO -25 CAD 12 300''')\n"
            "t.append(TaxTransaction(action='SPLIT', date='2025-03-10',"
            " time='00:00:01', symbol='OLDX.TO', symbol_new='NEWX.TO',"
            " quantity=1.0, currency='CAD', account='margin'))\n"
            "_, log = synthesize_openings(t, {('NEWX.TO','margin'),"
            " ('OLDX.TO','margin')})\n"
            "print(sum(e['opening_qty'] for e in log))\n"
        ) % os.path.dirname(os.path.abspath(__file__))
        outs = set()
        for seed in ('0', '1', '2', '3', '7'):
            env = {**os.environ, 'PYTHONHASHSEED': seed}
            r = subprocess.run([sys.executable, '-c', code], env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.add(r.stdout.strip())
        self.assertEqual(outs, {'15.0'})

    def test_registered_from_configured_type(self):
        # S076-08.
        from taxjson.lib.phantom_holdings import detect_phantoms
        txs = (_tt("BUYSELL 2025-03-03 10:00:00 XEI.TO -100 CAD 20 2000",
                   account='retireA')
               + _tt("BUYSELL 2025-03-03 10:00:00 XEI.TO -100 CAD 20 2000",
                     account='sunlife'))
        c = {x.account: x.registered for x in detect_phantoms(
            txs, registered_accounts={'retireA': True, 'sunlife': False})}
        self.assertEqual(c, {'retireA': True, 'sunlife': False})

    def test_suggest_phantoms_year_on_settle_basis(self):
        # S033-12: a phantom short covered 2025-12-31, settling 2026.
        import json
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        f = tmp / 'margin_base.json'
        f.write_text(json.dumps([t.to_dict() for t in _tt("""
            BUYSELL 2025-06-02 10:00:00 COVR.TO -50 CAD 29.9 1495
            BUYSELL 2025-12-31 10:00:00 COVR.TO 50 CAD 10.1 505 0 2026-01-02
        """)]))
        out = tmp / 'cand.json'
        r = _cli('taxjson.bin.taxjson_gains', '--country', 'canada',
                 '--taxable', '--year', '2026', '--suggest-phantoms', out, f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([e['symbol'] for e in json.loads(out.read_text())],
                         ['COVR.TO'])


class TestPriceChain(unittest.TestCase):

    def test_yahoo_spellings(self):
        # S077-09 / R1-150 / R1-228.
        from taxjson.lib.price_chain import yf_symbol_for
        self.assertEqual(yf_symbol_for('DLR.U.TO'), 'DLR-U.TO')
        self.assertEqual(yf_symbol_for('DIR.UN.TO'), 'DIR-UN.TO')
        self.assertEqual(yf_symbol_for('BF.B.US'), 'BF-B')
        self.assertEqual(yf_symbol_for('LEN.B.US'), 'LEN-B')
        self.assertEqual(yf_symbol_for('BRK.B.US'), 'BRK-B')
        self.assertEqual(yf_symbol_for('RCI.B.TO'), 'RCI-B.TO')
        self.assertEqual(yf_symbol_for('ETH'), 'ETH-USD')
        self.assertEqual(yf_symbol_for('TAO'), 'TAO22974-USD')

    def test_quote_currency(self):
        # R1-150 / S077-00.
        from taxjson.lib.price_chain import quote_currency
        self.assertEqual(quote_currency('DLR.U.TO'), 'USD')
        self.assertEqual(quote_currency('DLR-U.TO'), 'USD')
        self.assertEqual(quote_currency('DIR-UN.TO'), 'CAD')
        self.assertEqual(quote_currency('ZSP.U.TO'), 'USD')
        self.assertEqual(quote_currency('AAPL'), 'USD')
        self.assertEqual(quote_currency('ETH-CAD'), 'CAD')
        self.assertEqual(quote_currency('ETH-USD'), 'USD')
        self.assertIsNone(quote_currency('EUNL.DE'))
        self.assertIsNone(quote_currency('7203.T'))

    def test_lse_pence_normalized(self):
        # S077-04: a Yahoo 'GBp' quote comes back in pounds.
        import sys
        import types
        import pandas as pd
        from taxjson.lib import price_chain as pc

        class _T:
            def __init__(self, sym):
                self.history_metadata = {'currency': 'GBp'}

            def history(self, **kw):
                return pd.DataFrame({'Close': [70.0]})
        fake = types.SimpleNamespace(Ticker=_T)
        saved = sys.modules.get('yfinance')
        sys.modules['yfinance'] = fake
        try:
            got = pc._yfinance_fetcher({'VOD.L': 'VOD.L'}, verbose=False)
        finally:
            if saved is not None:
                sys.modules['yfinance'] = saved
            else:
                sys.modules.pop('yfinance', None)
        self.assertEqual(got, {'VOD.L': (0.7, 'GBP')})

    def test_offline_option_lookup_refuses(self):
        # R1-343: no gateway request; a cache miss refuses.
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        from taxjson.lib import price_chain as pc
        cache = Path(tempfile.mkdtemp()) / '.price_cache.json'
        with mock.patch.dict(os.environ, {'TAXJSON_OFFLINE': '1'}), \
                mock.patch.object(pc, '_ibkr_option_fetcher',
                                  side_effect=AssertionError('network')):
            with self.assertRaises(SystemExit):
                pc.fetch_option_prices(['XYZ261120C00050000.US'],
                                       cache_path=cache)


class TestFxSources(unittest.TestCase):

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from taxjson.bin import to_base_curr as T
        self.T = T
        td = tempfile.mkdtemp()
        p = mock.patch.object(T, 'CACHE_FILE', str(Path(td) / 'fx.json'))
        p.start()
        self.addCleanup(p.stop)

    def _weekdays(self, a, b):
        from datetime import date
        out, d = [], a
        while d <= b:
            if date.fromisoformat(d).weekday() < 5:
                out.append(d)
            d = self.T._shift(d, 1)
        return out

    @rule("CA-FX-03")
    def test_noon_rate_before_march_2017(self):
        # R1-146: BoC noon for 2007-05-01..2017-02-28, Yahoo before.
        T = self.T
        noon = lambda c, a, b: {d: '1.2914' for d in self._weekdays(a, b)}
        boc = lambda c, a, b: {d: '1.3500' for d in self._weekdays(a, b)}
        yahoo = lambda t, a, b: {d: 1.2 for d in self._weekdays(a, b)}
        rows, errors, _ = T.build_rates(
            'USD', 'CAD', '2006-01-02', '2017-03-10', today='2026-09-28',
            fetch_boc_fn=boc, fetch_yahoo_fn=yahoo, fetch_noon_fn=noon)
        src = {d: (v, s) for d, v, s in rows}
        self.assertEqual(src['2016-06-15'], ('1.2914', 'boc-noon'))
        self.assertEqual(src['2017-02-15'], ('1.2914', 'boc-noon'))
        self.assertEqual(src['2017-03-02'], ('1.3500', 'boc'))
        self.assertEqual(src['2006-06-15'][1], 'yahoo')
        self.assertEqual(errors, [])

    def test_failed_yahoo_download_is_asked_again(self):
        # S055-02: an empty answer with no later data is a failure.
        T = self.T
        cache = {}
        errs = T.refresh_yahoo(cache, 'USDCAD', [('2015-01-01', '2015-12-31')],
                               '2026-09-28', fetch=lambda t, a, b: {})
        self.assertTrue(errs)
        self.assertEqual(T._yahoo_coverage(cache, 'USDCAD'), [])
        errs = T.refresh_yahoo(
            cache, 'USDCAD', [('2015-01-01', '2015-12-31')], '2026-09-28',
            fetch=lambda t, a, b: {d: 1.3 for d in self._weekdays(a, b)})
        self.assertEqual(errs, [])
        self.assertIn('USDCAD-2015-06-15', cache)
        # A range before the source's history: remembered as empty only
        # when the source, asked now, answers for the later dates
        # (re-audit A2-0136: later CACHED data used to be the proof, so a
        # failed download was remembered for good).
        errs = T.refresh_yahoo(cache, 'USDCAD', [('2000-01-01', '2000-12-31')],
                               '2026-09-28', fetch=lambda t, a, b: {})
        self.assertTrue(errs)
        errs = T.refresh_yahoo(
            cache, 'USDCAD', [('2000-01-01', '2000-12-31')], '2026-09-28',
            fetch=lambda t, a, b: {d: 1.3 for d in self._weekdays(
                max(a, '2003-12-01'), b)})
        self.assertEqual(errs, [])

    def test_pre_coverage_fallback_message_names_a_working_remedy(self):
        # S028-13.
        from taxjson.bin import taxjson_convert_currency as C
        C.reset_fallback_tally()
        C._FALLBACK_ROWS.append({'id': 'x', 'date': '2001-02-02',
                                 'currency': 'USD', 'symbol': 'ABC.US',
                                 'action': 'BUYSELL',
                                 'reason': 'date predates rates file '
                                           'start 2003-09-17'})
        msg = list(C.fallback_validation_issues('CAD', 1.35).values())[0][0]
        C.reset_fallback_tally()
        self.assertIn('refreshing cannot help', msg)
        self.assertIn('in CAD', msg)
        self.assertNotIn('pass --default-rate', msg)

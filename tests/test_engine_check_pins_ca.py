"""Canadian engine branches the suite executes but no test checks (TJ-1896,
the mutation triage of 2026-11-09, second pass): each test runs one branch
and pins a figure a mutant of its decision changes. Synthetic data only;
tickers and accounts are made up."""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res, err.getvalue()


def _tx(action, date, qty, net, *, sym='SYQ.TO', account='b', typ='',
        desc='', settle=None, time='10:00:00', mult=1.0, code=''):
    return TaxTransaction(action=action, date=date,
                          date_settle=settle or date, time=time,
                          symbol=sym, quantity=qty,
                          price=abs(net / qty / mult) if qty else 0.0,
                          net_amount=net, currency='CAD', account=account,
                          type=typ, description=desc, open_close=code)


def _trade(date, qty, net, **kw):
    return _tx('BUYSELL', date, qty, net, **kw)


def _rename(date, old, new):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='09:00:00', symbol=old, symbol_new=new,
                          quantity=1.0, net_amount=0.0, currency='CAD',
                          account='b')


def _sales(res, sym=None, places=2):
    """(date, symbol, gain, denied, denied for good) per sale record."""
    return [(r['date'], r['symbol'], round(r['gain'], places),
             round(r.get('disallowed_amount') or 0.0, places),
             round(r.get('permanently_disallowed') or 0.0, places))
            for r in res['transactions']
            if r.get('qty') and (sym is None or r['symbol'] == sym)]


class TestRocReversalGivesBackOnlyWhatTheAcbHolds(unittest.TestCase):
    """`_walk_adjust`, a return of capital reversed (a positive `roc`
    ADJUST) after an earlier one cut the superficial-loss addition: what it
    gives back to the deferred addition is at most the ACB not already
    counted as deferred (`total_cost - deferred_wash`), so the deferral
    never exceeds the ACB (CA-RPT-16; TJ-1219).

    Buy 100 for 1000, sell for 500 (loss 500), rebuy 100 for 500: the loss
    is denied, ACB 1000 of which 500 is the addition (s.53(1)(f)). ROC 800:
    ACB 200, all of it the addition, 300 cut. ROC reversed by 100: ACB 300,
    of which the addition is 300 (100 back), 200 still cut. The sale for
    900 recovers 300 and reports the 200 cut: 300 + 200 = the 500 denied."""

    @rule(R.CA_ACB_06, R.CA_SL_09, R.CA_RPT_16)
    def test_a_part_reversal_returns_part_of_the_cut(self):
        res, _ = _run([_trade('2025-01-02', 100, -1000),
                       _trade('2025-02-03', -100, 500),
                       _trade('2025-02-05', 100, -500),
                       _tx('ADJUST', '2025-04-01', 0.0, -800, typ='roc'),
                       _tx('ADJUST', '2025-05-01', 0.0, 100, typ='roc'),
                       _trade('2025-07-02', -100, 900)])
        sale, = [r for r in res['transactions'] if r['date'] == '2025-07-02']
        self.assertAlmostEqual(sale['cost'], 300.0, 6)
        self.assertAlmostEqual(sale['gain'], 600.0, 6)
        self.assertAlmostEqual(sale['deferred_loss_recovered'], 300.0, 6)
        self.assertAlmostEqual(sale['deferred_loss_cut'], 200.0, 6)


class TestRenameMergeOnTheSameTradeDate(unittest.TestCase):
    """`_walk_split`, a rename onto a ticker that already has a pool
    (`AcbPool._merge_dates`): bought on the same trade date, the merged
    holding shows the later of the two settle dates, the one the planning
    views measure the s.54 window from (CA-SL-01, A2-0958), as one pool
    holding both purchases does (the owner's ruling on backlog TJ-4241;
    the target's own earlier settle date stood: TJ-2722)."""

    @rule(R.CA_ACB_RENAME, R.CA_SL_01)
    def test_the_later_settle_date_shows(self):
        for target, source in (('2025-03-04', '2025-03-05'),
                               ('2025-03-05', '2025-03-04')):
            with self.subTest(target=target, source=source):
                res, _ = _run([
                    _trade('2025-03-03', 10, -500, sym='ZZQ.TO', settle=target),
                    _trade('2025-03-03', 10, -800, sym='SYQ.TO', settle=source),
                    _rename('2025-04-01', 'SYQ.TO', 'ZZQ.TO')])
                held, = res['inventory']
                self.assertEqual((held['symbol'], held['qty'], held['total_cost']),
                                 ('ZZQ.TO', 20.0, 1300.0))
                self.assertEqual((held['last_acq_date'], held['last_acq_settle']),
                                 ('2025-03-03', '2025-03-05'))


class TestHalfACentOfCostIsAHolding(unittest.TestCase):
    """`_walk_rows`' drain and `_walk_trade`'s `_dust_close`: a position
    under the share tolerance (1e-7 shares) that carries exactly
    MATERIAL_COST (half a cent) of cost is a holding, not noise (audit
    #101; pool_is_flat): it is not drained after its purchase, and the
    sale that reverses it closes it at its cost before the rest opens a
    short. Below half a cent it is drained and the sale only opens the
    short."""

    def _book(self, cost):
        return _run([_trade('2025-03-03', 1e-7, -cost),
                     _trade('2025-04-01', -10, 1000),
                     _trade('2025-05-01', 10, -900)])[0]

    @rule(R.CA_CRYPTO_09, R.CA_DISP_01, R.CA_DISP_04)
    def test_half_a_cent_is_closed_at_its_cost(self):
        res = self._book(0.005)
        self.assertEqual(
            [(r['date'], r['direction'], round(r['qty'], 9),
              round(r['cost'], 6), round(r['gain'], 6))
             for r in res['transactions']],
            # (the cover's 1e-7 units past the short are rounding: its whole 900
            # is the cover's cost, where 0.000009 of it was dropped; TJ-4990)
            [('2025-04-01', 'LONG', 1e-7, 0.005, -0.00499),
             ('2025-05-01', 'SHORT', 9.9999999, -999.99999, 99.99999)])

    @rule(R.CA_CRYPTO_09, R.CA_DISP_04)
    def test_less_is_drained(self):
        res = self._book(0.0049)
        self.assertEqual([(r['date'], r['direction'], r['qty'])
                          for r in res['transactions']],
                         [('2025-05-01', 'SHORT', 10)])


class TestADustMarkedShortIsNoPosition(unittest.TestCase):
    """`_ca_stock_short_key`, an ordinary ADJUST (not a superficial-loss
    addition) on a symbol with no long pool beside a marked short: the
    short takes it only while it is a position, MORE than the share
    tolerance, as the short router reads it. A short of exactly 1e-6 shares
    is no position (pool_is_flat): its coded cover is a plain purchase that
    opens a long, and a payment posted before it is the symbol's, posted
    with nothing held (the empty-pool warning), so it waits on the symbol's
    pool and enters the next position's cost, that long's. On a short of
    2e-6 shares it is the short seller's payment: the cover's gain falls
    by it."""

    def _book(self, q):
        return _run([_trade('2025-03-03', -q, 15 * q, code='O'),
                     _tx('ADJUST', '2025-04-01', 0.0, 20, typ='dist'),
                     _trade('2025-05-01', q, 12 * q, code='C')])

    @rule(R.CA_ACB_14, R.CA_DISP_04)
    def test_a_short_at_the_tolerance_takes_nothing(self):
        res, _ = self._book(1e-6)
        self.assertEqual(res['transactions'], [])
        self.assertEqual([(i['symbol'], i['qty'], round(i['total_cost'], 6))
                          for i in res['inventory']],
                         [('SYQ.TO', 1e-6, 20.000012)])

    @rule(R.CA_ACB_14, R.CA_DISP_04)
    def test_a_short_past_it_takes_the_payment(self):
        res, _ = self._book(2e-6)
        self.assertEqual([(r['date'], round(r['gain'], 6), r['qty'])
                          for r in res['transactions']],
                         [('2025-05-01', -19.999994, 2e-6)])


class TestOneSaleOfTwoFillsSharesItsBacking(unittest.TestCase):
    """`_sl_window_walk`, each holder's units held less what an earlier fill
    of the same sale already drew from it (`_grp_claim`, CA-SL-08: one
    sale, one formula), and `_sl_holder_draw`, the sale's draw on each
    holder shared by its fills (TJ-2720). A sale of 200 shares in two fills
    on one day, 4 a share lost, backed by two holders: the sale draws on
    the first replacement's holder up to what it holds at day 30 and on
    the other for the rest, and each fill takes its units' share of both,
    so the split does not depend on which fill is listed first (before, the
    first fill took the first holder whole and the second found it spent).
    Each holder backs the sale with what it holds of its replacements
    (s.53(1)(f) adds the denied loss to the substituted property): the
    taxable part defers, the registered part is lost (CA-SL-09)."""

    C = 'SYQ251219C00010000.TO'

    def _fills(self, sym):
        return [_trade('2025-01-03', 200, -2000, sym=sym),
                _trade('2025-02-03', -100, 600, sym=sym),
                _trade('2025-02-03', -100, 600, sym=sym, time='10:05:00')]

    @rule(R.CA_SL_08, R.CA_SL_05, R.CA_SL_09)
    def test_a_call_then_a_registered_purchase(self):
        # Two calls bought on day 1 (200 units), one sold on day 9: 100
        # units held at day 30 (per holder and contract); the RRSP's 100
        # shares bought on day 3 are held. min(200, 300, 200) = 200 units,
        # 100 on the call and 100 on the RRSP, half of each a fill: each
        # fill adds 200 to the call's ACB and loses 200 (400 and 400 in
        # all). The call sold carried half of the 450: 300 - 225 = 75.
        res, _ = _run(self._fills('SYQ.TO') + [
            _trade('2025-02-04', 2, -50, sym=self.C, mult=100),
            _trade('2025-02-12', -1, 300, sym=self.C, mult=100)],
            sheltered_transactions=[
                _trade('2025-02-06', 100, -600, account='rrsp')])
        self.assertEqual(_sales(res),
                         [('2025-02-03', 'SYQ.TO', 0.0, 400.0, 200.0),
                          ('2025-02-03', 'SYQ.TO', 0.0, 400.0, 200.0),
                          ('2025-02-12', self.C, 75.0, 0.0, 0.0)])
        call, = res['inventory']
        self.assertAlmostEqual(call['total_cost'], 225.0, 6)

    @rule(R.CA_SL_08, R.CA_SL_03, R.CA_SL_09)
    def test_a_taxable_purchase_then_one_moved_between_plans(self):
        # Taxable: 100 bought on day 1, 60 sold on day 9: 40 held. r1: 100
        # bought on day 2 and moved to r2 (an own move: the per-holder walk
        # runs), held. min(200, 200, 140) = 140 units, 40 on the taxable
        # holder and 100 on r1, half of each a fill: each fill defers 80
        # (20 units) and loses 200 (50 units) -- 160 deferred and 400 lost
        # in all.
        res, _ = _run(self._fills('SYN.TO') + [
            _trade('2025-02-04', 100, -600, sym='SYN.TO'),
            _trade('2025-02-12', -60, 600, sym='SYN.TO')],
            sheltered_transactions=[
                _trade('2025-02-05', 100, -600, sym='SYN.TO', account='r1'),
                _tx('TRANSFER', '2025-02-10', -100, 0.0, sym='SYN.TO',
                    account='r1', typ='own_account_move'),
                _tx('TRANSFER', '2025-02-10', 100, 0.0, sym='SYN.TO',
                    account='r2', typ='own_account_move')])
        self.assertEqual(_sales(res),
                         [('2025-02-03', 'SYN.TO', -120.0, 280.0, 200.0),
                          ('2025-02-03', 'SYN.TO', -120.0, 280.0, 200.0),
                          ('2025-02-12', 'SYN.TO', 144.0, 0.0, 0.0)])
        held, = res['inventory']
        self.assertEqual((held['qty'], round(held['total_cost'], 2)),
                         (40.0, 304.0))


class TestACrossingRowKeepsTheOtherBooks(unittest.TestCase):
    """`CanadaTaxRules._prepare`: a buy that covers a marked short and buys
    long with the rest is cut in two rows (`_split_crossing_marked_rows`,
    CA-DISP-04), and the run's row list is rebuilt from the three books, the
    registered and affiliated ones whole; after a split inside a trade's
    settle lag re-denominated a row (CA-CORP-12), from the slices the books
    were cut back into. A registered or affiliated account's purchase in a
    loss's window and held is still there to deny it for good (CA-SL-03,
    CA-SL-04, CA-SL-09), and every taxable row is walked once."""

    CROSS = [_trade('2025-03-03', -10, 150, sym='ZZQ.TO', code='O'),
             _trade('2025-03-10', 20, -280, sym='ZZQ.TO', code='C;O'),
             _trade('2025-05-01', -10, 160, sym='ZZQ.TO')]
    LOSS = [_trade('2025-01-02', 100, -1000),
            _trade('2025-02-03', -100, 600)]
    REP = [_trade('2025-02-05', 100, -600, account='tfsa')]
    # Sold 50 the morning of a 2-for-1 split, settled the day after: 100
    # post-split shares at half the cost (500), then the other 100.
    LAG = [_trade('2025-06-02', 100, -1000, sym='QQA.TO'),
           _trade('2025-06-10', -50, 600, sym='QQA.TO', time='10:30:00',
                  settle='2025-06-11'),
           TaxTransaction(action='SPLIT', date='2025-06-10',
                          date_settle='2025-06-10', time='20:25:00',
                          symbol='QQA.TO', quantity=2.0, net_amount=0.0,
                          currency='CAD', account='b'),
           _trade('2025-07-02', -100, 1300, sym='QQA.TO')]
    WANT = [('2025-02-03', 'SYQ.TO', 0.0, 400.0, 400.0),
            ('2025-03-10', 'ZZQ.TO', 10.0, 0.0, 0.0),
            ('2025-05-01', 'ZZQ.TO', 20.0, 0.0, 0.0)]

    @rule(R.CA_DISP_04, R.CA_SL_03, R.CA_SL_09)
    def test_a_registered_replacement(self):
        res, _ = _run(self.CROSS + self.LOSS, sheltered_transactions=self.REP)
        self.assertEqual(_sales(res), self.WANT)

    @rule(R.CA_DISP_04, R.CA_SL_04, R.CA_SL_09)
    def test_an_affiliated_replacement(self):
        res, _ = _run(self.CROSS + self.LOSS,
                      affiliated_transactions=self.REP)
        self.assertEqual(_sales(res), self.WANT)

    @rule(R.CA_DISP_04, R.CA_CORP_12, R.CA_SL_03)
    def test_after_a_settle_lag_re_denomination(self):
        res, _ = _run(self.CROSS + self.LOSS + self.LAG,
                      sheltered_transactions=self.REP)
        self.assertEqual(_sales(res), self.WANT + [
            ('2025-06-10', 'QQA.TO', 100.0, 0.0, 0.0),
            ('2025-07-02', 'QQA.TO', 800.0, 0.0, 0.0)])
        self.assertEqual(res['inventory'], [])


def _row(action, date, sym, qty, price, net, time='10:00:00',
         account='margin'):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time=time, symbol=sym, quantity=float(qty),
                          price=float(price), net_amount=float(net),
                          currency='CAD', account=account)


class TestAStockRowBeforeItsAssignment(unittest.TestCase):
    """`_pair_assign_legs`, a candidate stock leg that sorts BEFORE the
    option ASSIGN row: it is the assignment's own leg only on strong
    identity, marked, or the delivered quantity AT the strike (both). A
    100-share purchase the day before a 12-strike put's assignment at 10.50
    is a market purchase, not the delivery (bought at the strike, s.49(3.1)):
    the put's premium is not folded into it (the run's end warns it
    unconsumed). At the strike, it is the delivery: 1200 - 199."""

    P = 'SYQ251212P00012000.TO'

    def _cost(self, price):
        res, _ = _run([
            _row('BUYSELL', '2025-11-03', self.P, -1, 1.99, 199),
            _row('BUYSELL', '2025-12-11', 'SYQ.TO', 100, price, 100 * price),
            _row('ASSIGN', '2025-12-12', self.P, 1, 0, 0, time='16:00:00')])
        return [(i['symbol'], i['qty'], round(i['total_cost'], 2))
                for i in res['inventory']]

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_size_alone_is_not_the_delivery(self):
        self.assertEqual(self._cost(10.5), [('SYQ.TO', 100.0, 1050.0)])

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_size_at_the_strike_is(self):
        self.assertEqual(self._cost(12), [('SYQ.TO', 100.0, 1001.0)])


class TestADeliverySharedByTwoAssignments(unittest.TestCase):
    """`_pair_assign_legs`' `_claim`, a leg two assignments the strike
    cannot tell apart share: theirs pro rata by what each delivers, each
    up to what it still needs (TJ-2750). Two 12-strike puts assigned at
    one moment, A of 2 contracts (premium 200, 1 a share) and B of 1
    (premium 300, 3 a share), delivered as two marked rows of 150 shares
    with a sale of the first between them. Each row is A's 100 and B's 50
    (100 + 150 = 250 of premium): each put's premium lowers only the cost
    of shares its assignment delivered (s.49(3.1)). Before TJ-2750 the
    first row was A's 150 and the second B's 100 and A's last 50, because
    A's row stood nearer the first delivery."""

    A, B = 'SYQ251212P00012000.TO', 'SYQ260116P00012000.TO'

    def _book(self, first, second, sold, proceeds):
        rows = [_row('BUYSELL', '2025-11-03', self.A, -2, 1.00, 200),
                _row('BUYSELL', '2025-11-04', self.B, -1, 3.00, 300),
                _row('ASSIGN', '2025-12-12', self.B, 1, 0, 0, time='16:00:00'),
                _row('ASSIGN', '2025-12-12', self.A, 2, 0, 0, time='16:00:00'),
                _row('ASSIGN', '2025-12-12', 'SYQ.TO', first, 12, 12 * first,
                     time='16:00:01'),
                _row('BUYSELL', '2025-12-12', 'SYQ.TO', -sold, proceeds,
                     proceeds, time='16:00:02'),
                _row('ASSIGN', '2025-12-12', 'SYQ.TO', second, 12,
                     12 * second, time='16:00:03')]
        res, _ = _run(rows)
        return ([(round(r['cost'], 2), round(r['gain'], 2))
                 for r in res['transactions']
                 if r.get('raw_gain') is not None],
                [(i['qty'], round(i['total_cost'], 2))
                 for i in res['inventory']])

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_each_row_is_shared_two_to_one(self):
        # TJ-2750: 1800 - 250 = 1550 sold for 1900, and 1550 held (it was
        # 1650 sold and 1450 held while the nearer row took the first).
        self.assertEqual(self._book(150, 150, 150, 1900),
                         ([(1550.0, 350.0)], [(150.0, 1550.0)]))


class TestAMovedOptionRowKeepsItsOwnLaterDate(unittest.TestCase):
    """`_ca_walk_date`, an option ASSIGN row moved in front of its stock leg
    (`_assign_moved_legs`: the leg sorts first on settle dates): the walk
    stands at the LEG's date only when that is earlier (TJ-0839). On the
    trade-date basis the leg can be later: a put assigned December 31 whose
    posting settles January 5, its marked shares traded and settled January
    2. The put is closed at a trade-basis year end (December 31) and the
    shares not yet bought: nothing is held, and the cost timeline dates the
    put's close December 31."""

    P = 'SYQ251231P00012000.TO'

    @rule(R.CA_OPT_06, R.CA_RPT_12)
    def test_the_year_end_is_cut_after_the_assignment(self):
        res, _ = _run([
            _row('BUYSELL', '2025-11-03', self.P, -1, 1.99, 199),
            TaxTransaction(action='ASSIGN', date='2025-12-31',
                           date_settle='2026-01-05', time='16:00:00',
                           symbol=self.P, quantity=1.0, price=0.0,
                           net_amount=0.0, currency='CAD',
                           account='margin'),
            _row('ASSIGN', '2026-01-02', 'SYQ.TO', 100, 12, 1200,
                 time='16:00:00')],
            year_end='2025-12-31', year_end_basis='trade',
            cost_timeline='trade')
        self.assertEqual(res['inventory_year_end'], [])
        self.assertEqual([(i['symbol'], i['qty'], i['total_cost'])
                          for i in res['inventory']],
                         [('SYQ.TO', 100.0, 1001.0)])
        tl = res['cost_timeline']
        date, sym = tl['fields'].index('date'), tl['fields'].index('symbol')
        self.assertEqual([(r[date], r[sym]) for r in tl['rows']],
                         [('2025-11-03', self.P), ('2025-12-31', self.P),
                          ('2026-01-02', 'SYQ.TO')])


class TestABuyBackBesideAGrantTimedLot(unittest.TestCase):
    """`_ca_short_lot_close`, a written option's lots in two accounts under
    grant timing from 2025 (option_grant_timing_since): a 2024 write keeps
    close timing (CA-OPT-02) and a 2025 write's premium is a gain the day it
    is written (CA-OPT-01). Account b's buy-back closes b's own 2024 lot at
    its own premium: 300 - 100 = 200, taxed in the buy-back's year
    (CA-OPT-11); the recognised 2025 premium is no part of it (the residue
    over the plain lots is what the lots do not hold, `total - r_amt - their
    sums`: nil here)."""

    C = 'SYQ260116C00020000.TO'

    @rule(R.CA_OPT_01, R.CA_OPT_02, R.CA_OPT_11)
    def test_the_buy_back_takes_its_own_accounts_premium(self):
        def w(date, qty, price, account):
            return _row('BUYSELL', date, self.C, qty, price,
                        abs(qty) * price * 100, account=account)
        res, _ = _run([w('2024-11-04', -1, 2.0, 'a'),
                       w('2024-12-02', -1, 3.0, 'b'),
                       w('2025-02-03', -1, 4.0, 'a'),
                       w('2025-06-02', 1, 1.0, 'b')],
                      option_premium_timing='grant', option_grant_since=2025)
        self.assertEqual([(r['date'], r['account'], round(r['gain'], 2))
                          for r in res['transactions']],
                         [('2025-02-03', 'a', 400.0),
                          ('2025-06-02', 'b', 200.0)])
        held, = res['inventory']
        self.assertEqual((held['qty'], round(held['total_cost'], 2)),
                         (-2.0, -600.0))


if __name__ == '__main__':
    unittest.main()

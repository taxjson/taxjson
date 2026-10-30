"""Regression tests for fix round 7 (wave A), group coreca: the Canadian
engine (option assignment, superficial-loss ownership, short sales and
futures across accounts).

Synthetic data only; account labels and tickers are made up.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from tax_rules import rule
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res, err.getvalue()


class TestCallExpiringOnDay30(unittest.TestCase):
    """TJ-0725: a call that expires on day 30 is not held at its end."""

    def _loss(self, expiry):
        taxable = _tt("""
            BUYSELL 2025-01-10 10:00:00 SYQ.TO 100 CAD 10 1000
            BUYSELL 2025-08-20 10:00:00 SYQ.TO -100 CAD 6 600
        """)
        rrsp = _tt(f"""
            BUYSELL 2025-08-21 10:00:00 SYQ{expiry}C00010000.TO 1 CAD 1 100
        """, account='rrsp')
        res, _ = _run(taxable, sheltered_transactions=rrsp)
        return res['summary']

    @rule("CA-SL-02", "CA-SL-05")
    def test_call_expiring_on_day_30_does_not_deny(self):
        s = self._loss('250919')
        self.assertAlmostEqual(s['total_disallowed'], 0.0)
        self.assertAlmostEqual(s['total_gain'], -400.0, places=2)

    @rule("CA-SL-02", "CA-SL-05")
    def test_call_expiring_after_day_30_still_denies(self):
        s = self._loss('250920')
        self.assertAlmostEqual(s['total_disallowed'], 400.0, places=2)


def _split(date, old, new, ratio, account):
    return TaxTransaction(action='SPLIT', date=date, time='09:30:00',
                          symbol=old, symbol_new=new, quantity=ratio,
                          account=account, currency='CAD')


class TestCallSplitLineage(unittest.TestCase):
    """TJ-0716: a call replaced by a split series and sold before day 30
    is not held at day 30."""

    def _run_case(self, split_call, sell_successor=True):
        taxable = _tt("""
            BUYSELL 2025-01-06 10:00:00 SYQ.US 100 CAD 10 1000
            BUYSELL 2025-06-02 10:00:00 SYQ.US -100 CAD 6 600
        """)
        if split_call:      # the control has no split at all
            taxable.append(_split('2025-06-05', 'SYQ.US', 'SYQ.US', 2.0,
                                  'margin'))
        rrsp = _tt("""
            BUYSELL 2025-06-03 10:00:00 SYQ251219C00010000.US 1 CAD 1 100
        """, account='rrsp')
        if split_call:
            rrsp.append(_split('2025-06-05', 'SYQ251219C00010000.US',
                               'SYQ251219C00005000.US', 2.0, 'rrsp'))
            if sell_successor:
                rrsp += _tt("""
                    BUYSELL 2025-06-10 10:00:00 SYQ251219C00005000.US -2 CAD 0.5 100
                """, account='rrsp')
        else:
            rrsp += _tt("""
                BUYSELL 2025-06-10 10:00:00 SYQ251219C00010000.US -1 CAD 1 100
            """, account='rrsp')
        res, _ = _run(taxable, sheltered_transactions=rrsp)
        return res['summary']

    @rule("CA-SL-02", "CA-SL-05")
    def test_split_successor_sold_in_window_allows_the_loss(self):
        s = self._run_case(split_call=True)
        self.assertAlmostEqual(s['total_disallowed'], 0.0)
        self.assertAlmostEqual(s['total_gain'], -400.0, places=2)

    @rule("CA-SL-02", "CA-SL-05")
    def test_unsplit_control(self):
        s = self._run_case(split_call=False)
        self.assertAlmostEqual(s['total_disallowed'], 0.0)
        self.assertAlmostEqual(s['total_gain'], -400.0, places=2)

    @rule("CA-SL-02", "CA-SL-05", "CA-SL-09")
    def test_split_successor_still_held_denies(self):
        s = self._run_case(split_call=True, sell_successor=False)
        self.assertAlmostEqual(s['total_disallowed'], 400.0, places=2)

    @rule("CA-SL-05", "CA-SL-09")
    def test_successor_bought_in_window_carries_the_denial(self):
        # The old series was held from before the window; the successor
        # series bought inside it is the replacement, and the denied
        # loss lands on its cost: sold later, the call's loss is 400
        # larger.
        taxable = _tt("""
            BUYSELL 2025-01-06 10:00:00 SYQ.US 100 CAD 10 1000
            BUYSELL 2025-04-01 10:00:00 SYQ251219C00010000.US 1 CAD 1 100
            BUYSELL 2025-06-02 10:00:00 SYQ.US -100 CAD 6 600
            BUYSELL 2025-06-09 10:00:00 SYQ251219C00005000.US 2 CAD 0.5 100
            BUYSELL 2025-08-01 10:00:00 SYQ251219C00005000.US -4 CAD 0.5 200
        """) + [_split('2025-06-05', 'SYQ.US', 'SYQ.US', 2.0, 'margin'),
                _split('2025-06-05', 'SYQ251219C00010000.US',
                       'SYQ251219C00005000.US', 2.0, 'margin')]
        res, _ = _run(taxable)
        s = res['summary']
        self.assertAlmostEqual(s['total_disallowed'], 400.0, places=2)
        # Shares -400 denied (0), calls: 200 proceeds - 200 cost - 400.
        self.assertAlmostEqual(s['total_gain'], -400.0, places=2)


def _fut(i, date, qty, net, account='a', sym='F:SYQH6.US'):
    return TaxTransaction(id=i, action='BUYSELL', date=date,
                          date_settle=date, time='10:00:00', symbol=sym,
                          quantity=qty, net_amount=net, currency='CAD',
                          account=account)


def _settled(book):
    from taxjson.lib.futures import settle_futures
    out = []
    for a in sorted({t.account for t in book}):
        out += settle_futures([t for t in book if t.account == a],
                              'average')[0]
    return out


class TestFuturesLongShortOwnership(unittest.TestCase):
    """TJ-0651: an independent short future in another account does not
    cancel the long replacement held at day 30."""

    BASE = [('old', '2025-01-06', 1, 100), ('loss', '2025-03-03', -1, 50),
            ('rep', '2025-03-05', 1, 100)]

    def _summary(self, short=None):
        book = [_fut(*r) for r in self.BASE]
        if short:
            book.append(_fut('short', short, -1, 100, account='b'))
        res, _ = _run(_settled(book))
        return res['summary']

    @rule("CA-SL-02", "CA-SL-07")
    def test_long_replacement_alone_denies(self):
        s = self._summary()
        self.assertAlmostEqual(s['total_disallowed'], 50.0, places=2)

    @rule("CA-SL-02", "CA-SL-07")
    def test_independent_short_does_not_cancel_the_long(self):
        s = self._summary(short='2025-04-01')
        self.assertAlmostEqual(s['total_disallowed'], 50.0, places=2)
        self.assertAlmostEqual(s['total_gain'], 0.0, places=2)


class TestFuturesLongShortTimeline(unittest.TestCase):
    """TJ-0650: the cost timeline keeps a long contract's deferred-loss
    cost while another account is short the same contract."""

    @rule("CA-RPT-12", "CA-RPT-19")
    def test_t1135_keeps_the_long_contracts_cost(self):
        from taxjson.bin.taxjson_t1135 import walk_costs
        from taxjson.lib import cost_timeline as CT
        from taxjson.lib.pipeline import GainsRequest, run_gains
        book = _settled([_fut('old', '2025-01-06', 1, 100),
                         _fut('loss', '2025-03-03', -1, 50),
                         _fut('rep', '2025-03-05', 1, 100),
                         _fut('short', '2025-04-15', -1, 100,
                              account='b')])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run_gains(list(book), [], [], GainsRequest(
                country='canada', taxable=True, year=2025,
                cost_timeline=True, phantom_hint=False))
            w = walk_costs([t.to_dict() for t in book], 2025, {},
                           timeline=r['cost_timeline'])
        self.assertAlmostEqual(r['summary']['total_disallowed'], 50.0,
                               places=2)
        (held,) = CT.inventory_on(r['cost_timeline'], '2025-12-31')
        self.assertEqual((held['qty'], round(held['total_cost'], 2)),
                         (1.0, 50.0))
        sym = w['per_symbol']['F:SYQH6.US']
        self.assertAlmostEqual(sym['year_end_cost'], 50.0, places=2)
        self.assertAlmostEqual(sym['max_cost'], 50.0, places=2)


def _row(account, date, settle, qty, net, desc='', open_close='',
         sym='SYQ.TO'):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=settle,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=account,
                          description=desc, open_close=open_close)


def _gain_by_year(res):
    out = {}
    for r in res['transactions']:
        y = (r.get('date_settle') or r['date'])[:4]
        out[y] = round(out.get(y, 0.0) + r['gain'], 2)
    return out


class TestShortSaleInAnotherAccount(unittest.TestCase):
    """TJ-0721: a broker-marked short sale in account b while account a
    holds the shares opens a short; it does not sell a's shares."""

    def _book(self, sale_desc='SYQ INC SHORT. UNSOLICITED',
              cover_desc='SYQ INC COVER SHORT. UNSOLICITED',
              sale_oc='', cover_oc=''):
        return [
            _row('a', '2024-03-01', '2024-03-04', 10, 100),
            _row('b', '2025-12-01', '2025-12-02', -10, 150, sale_desc,
                 sale_oc),
            _row('b', '2026-01-20', '2026-01-21', 10, 140, cover_desc,
                 cover_oc),
        ]

    @rule("CA-DISP-04", "CA-ACB-01")
    def test_rbc_marked_short_keeps_the_long(self):
        res, _ = _run(self._book(), year_end='2025-12-31')
        self.assertEqual(_gain_by_year(res), {'2026': 10.0})
        inv = {i['symbol']: i for i in res['inventory']
               if abs(i['qty']) > 1e-9}
        self.assertAlmostEqual(inv['SYQ.TO']['qty'], 10.0)
        self.assertAlmostEqual(inv['SYQ.TO']['total_cost'], 100.0)

    @rule("CA-DISP-04")
    def test_ib_coded_short_keeps_the_long(self):
        res, _ = _run(self._book(sale_desc='', cover_desc='',
                                 sale_oc='O', cover_oc='C'))
        self.assertEqual(_gain_by_year(res), {'2026': 10.0})

    @rule("CA-ACB-01")
    def test_unmarked_sale_draws_on_the_pool(self):
        # No broker marker: a sale in another taxable account sells the
        # pooled shares (s.47), as before.
        res, _ = _run(self._book(sale_desc='', cover_desc=''))
        self.assertEqual(_gain_by_year(res), {'2025': 50.0})

    @rule("CA-DISP-04")
    def test_long_bought_after_the_short_is_not_a_cover(self):
        # The shares bought while b is short (marked) stay a's long; the
        # marked cover closes b's short.
        book = [
            _row('b', '2025-11-03', '2025-11-04', -10, 150,
                 'SYQ INC SHORT. UNSOLICITED'),
            _row('a', '2025-11-10', '2025-11-11', 10, 100),
            _row('b', '2025-11-20', '2025-11-21', -5, 70,
                 'SYQ INC SHORT. UNSOLICITED'),
            _row('b', '2026-01-20', '2026-01-21', 15, 210,
                 'SYQ INC COVER SHORT. UNSOLICITED'),
        ]
        res, _ = _run(book)
        self.assertEqual(_gain_by_year(res), {'2026': 10.0})

    @rule("CA-DISP-04")
    def test_split_while_short_scales_both_positions(self):
        book = self._book()[:2] + [
            _split('2025-12-15', 'SYQ.TO', 'SYQ.TO', 2.0, 'a'),
            _row('b', '2026-01-20', '2026-01-21', 20, 140,
                 'SYQ INC COVER SHORT. UNSOLICITED')]
        res, _ = _run(book)
        self.assertEqual(_gain_by_year(res), {'2026': 10.0})
        inv = {i['symbol']: i for i in res['inventory']
               if abs(i['qty']) > 1e-9}
        self.assertAlmostEqual(inv['SYQ.TO']['qty'], 20.0)
        self.assertAlmostEqual(inv['SYQ.TO']['total_cost'], 100.0)


class TestAssignmentRetractsItsOwnAccountsGrant(unittest.TestCase):
    """TJ-0718: an assignment in account b retracts b's written option,
    not an older one still open in account a."""

    @rule("CA-OPT-01", "CA-OPT-06")
    def test_assigned_accounts_grant_is_retracted(self):
        a = _tt("""
            BUYSELL 2024-12-02 10:00:00 SYQ261218P00010000.US -1 CAD 1 100
        """, account='a')
        b = _tt("""
            BUYSELL 2025-01-06 10:00:00 SYQ261218P00010000.US -1 CAD 3 300
            ASSIGN 2025-01-21 10:00:00 SYQ261218P00010000.US 1 CAD 0 0
            BUYSELL 2025-01-21 10:00:00 SYQ.US 100 CAD 10 1000
        """, account='b')
        res, _ = _run(a + b, option_premium_timing='grant',
                      option_grant_since=2024)
        self.assertEqual(_gain_by_year(res), {'2024': 100.0})
        inv = {i['symbol']: i for i in res['inventory']
               if abs(i['qty']) > 1e-9}
        self.assertAlmostEqual(inv['SYQ.US']['total_cost'], 700.0)
        # a's contract is still open, at its own premium.
        self.assertAlmostEqual(inv['SYQ261218P00010000.US']['qty'], -1.0)
        self.assertAlmostEqual(
            inv['SYQ261218P00010000.US']['total_cost'], -100.0)


class TestOneDeliveryForTwoAssignments(unittest.TestCase):
    """TJ-0838: one 200-share delivery for two assigned puts takes both
    premiums, wherever it sorts."""

    def _gain(self, delivery):
        txs = _tt(f"""
            BUYSELL 2025-01-02 10:00:00 SYQ250620P00010000.US -1 CAD 1 100
            BUYSELL 2025-01-03 10:00:00 SYQ250718P00010000.US -1 CAD 2 200
            {delivery}
            ASSIGN 2025-06-10 16:00:00 SYQ250620P00010000.US 1 CAD 0 0
            ASSIGN 2025-06-10 16:01:00 SYQ250718P00010000.US 1 CAD 0 0
            BUYSELL 2025-09-02 10:00:00 SYQ.US -200 CAD 12 2400
        """)
        res, err = _run(txs)
        self.assertNotIn('unconsumed option-assignment', err)
        return _gain_by_year(res)

    @rule("CA-OPT-06", "CA-OPT-08")
    def test_aggregate_delivery_before_the_option_rows(self):
        self.assertEqual(
            self._gain('ASSIGN 2025-06-10 10:00:00 SYQ.US 200 CAD 10 2000'),
            {'2025': 700.0})

    @rule("CA-OPT-06", "CA-OPT-08")
    def test_aggregate_delivery_after_the_option_rows(self):
        self.assertEqual(
            self._gain('ASSIGN 2025-06-10 17:00:00 SYQ.US 200 CAD 10 2000'),
            {'2025': 700.0})

    @rule("CA-OPT-08")
    def test_deliveries_split_across_assignments_unevenly(self):
        # One contract and two contracts, delivered as 150 + 150 shares:
        # the second leg's remainder completes the two-contract one.
        txs = _tt("""
            BUYSELL 2025-01-02 10:00:00 SYQ250620P00010000.US -1 CAD 1 100
            BUYSELL 2025-01-03 10:00:00 SYQ250718P00010000.US -2 CAD 1 200
            ASSIGN 2025-06-10 10:00:00 SYQ.US 150 CAD 10 1500
            ASSIGN 2025-06-10 10:01:00 SYQ.US 150 CAD 10 1500
            ASSIGN 2025-06-10 16:00:00 SYQ250620P00010000.US 1 CAD 0 0
            ASSIGN 2025-06-10 16:01:00 SYQ250718P00010000.US 2 CAD 0 0
            BUYSELL 2025-09-02 10:00:00 SYQ.US -300 CAD 12 3600
        """)
        res, err = _run(txs)
        self.assertNotIn('unconsumed option-assignment', err)
        self.assertEqual(_gain_by_year(res), {'2025': 900.0})

    @rule("US-OPT-05")
    def test_usa_engine_shares_the_pairing(self):
        from taxjson.lib.core import USATaxRules
        txs = _tt("""
            BUYSELL 2025-01-02 10:00:00 SYQ250620P00010000.US -1 USD 1 100
            BUYSELL 2025-01-03 10:00:00 SYQ250718P00010000.US -1 USD 2 200
            ASSIGN 2025-06-10 10:00:00 SYQ.US 200 USD 10 2000
            ASSIGN 2025-06-10 16:00:00 SYQ250620P00010000.US 1 USD 0 0
            ASSIGN 2025-06-10 16:01:00 SYQ250718P00010000.US 1 USD 0 0
            BUYSELL 2025-09-02 10:00:00 SYQ.US -200 USD 12 2400
        """)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = USATaxRules().compute_gains(txs)
        self.assertNotIn('unconsumed option-assignment', err.getvalue())
        self.assertEqual(_gain_by_year(res), {'2025': 700.0})

    @rule("CA-OPT-06", "CA-OPT-08")
    def test_separate_deliveries_control(self):
        self.assertEqual(
            self._gain('ASSIGN 2025-06-10 10:00:00 SYQ.US 100 CAD 10 1000\n'
                       'ASSIGN 2025-06-10 10:01:00 SYQ.US 100 CAD 10 1000'),
            {'2025': 700.0})


class TestJanuaryOptionCloseForDecemberDelivery(unittest.TestCase):
    """TJ-0839: a call exercised into shares delivered December 31 whose
    option-close posting is dated January 2: the shares are held at the
    year end, at the premium-inclusive cost."""

    def _engine(self, close_date):
        from taxjson.lib.pipeline import GainsRequest, run_gains
        txs = _tt(f"""
            BUYSELL 2025-01-02 09:00:00 OTQ.US 1 CAD 99000 99000
            BUYSELL 2025-06-02 10:00:00 SYQ260116C00010000.US 1 CAD 1 100
            ASSIGN 2025-12-31 16:00:00 SYQ.US 100 CAD 10 1000
            ASSIGN {close_date} 16:01:00 SYQ260116C00010000.US -1 CAD 0 0
            BUYSELL 2026-03-02 10:00:00 SYQ.US -100 CAD 12 1200
        """, account='a')
        with contextlib.redirect_stderr(io.StringIO()):
            return txs, run_gains(list(txs), [], [], GainsRequest(
                country='canada', taxable=True, year=2025,
                cost_timeline=True, phantom_hint=False))

    def _year_end(self, close_date):
        from taxjson.bin.taxjson_t1135 import walk_costs
        from taxjson.lib import cost_timeline as CT
        txs, r = self._engine(close_date)
        ye = {i['symbol']: (i['qty'], round(i['total_cost'], 2))
              for i in r['inventory_year_end'] if abs(i['qty']) > 1e-9}
        tl = {i['symbol']: (i['qty'], round(i['total_cost'], 2))
              for i in CT.inventory_on(r['cost_timeline'], '2025-12-31')}
        with contextlib.redirect_stderr(io.StringIO()):
            w = walk_costs([t.to_dict() for t in txs], 2025, {},
                           timeline=r['cost_timeline'])
        return ye, tl, round(w['max_total_cost'], 2)

    @rule("CA-OPT-06", "CA-RPT-12")
    def test_january_close_keeps_the_december_shares(self):
        want = {'OTQ.US': (1.0, 99000.0), 'SYQ.US': (100.0, 1100.0)}
        ye, tl, mx = self._year_end('2026-01-02')
        self.assertEqual(ye, want)
        self.assertEqual(tl, want)
        self.assertEqual(mx, 100100.0)

    @rule("CA-OPT-06", "CA-RPT-12")
    def test_december_close_control(self):
        want = {'OTQ.US': (1.0, 99000.0), 'SYQ.US': (100.0, 1100.0)}
        ye, tl, mx = self._year_end('2025-12-31')
        self.assertEqual(ye, want)
        self.assertEqual(tl, want)
        self.assertEqual(mx, 100100.0)


if __name__ == '__main__':
    unittest.main()

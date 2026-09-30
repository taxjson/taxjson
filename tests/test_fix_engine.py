"""Regression tests for the engine-area audit fixes (fix/engine).

Synthetic data only; account labels are fake.
"""
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


def _tx(**kw):
    kw.setdefault('currency', 'CAD')
    kw.setdefault('account', 'margin')
    return TaxTransaction(**kw)


class TestUnmarkedLegNotBlockedByLaterMarkedLeg(unittest.TestCase):
    """R1-33: a plain (IB/RBC BUYSELL) assignment stock leg was skipped
    when ANY later marked (Webull ASSIGN) leg existed on the same
    (account, underlying): the premium jumped to that later leg,
    possibly a year later."""

    def _rows(self, sfx, cur):
        und = f'XYZ.{sfx}'
        return [
            # 2025: short put, plain-convention assignment.
            _tx(action='BUYSELL', date='2025-02-03', time='10:00:00',
                symbol=f'XYZ250321P00012000.{sfx}', quantity=-1.0,
                price=2.0, net_amount=199.0, currency=cur),
            _tx(action='ASSIGN', date='2025-03-21', time='16:20:00',
                symbol=f'XYZ250321P00012000.{sfx}', quantity=1.0,
                price=0.0, net_amount=0.0, currency=cur),
            _tx(action='BUYSELL', date='2025-03-21', time='16:20:00',
                symbol=und, quantity=100.0, price=12.0,
                net_amount=1200.0, currency=cur),
            _tx(action='BUYSELL', date='2025-06-02', time='10:00:00',
                symbol=und, quantity=-100.0, price=13.0,
                net_amount=1299.0, currency=cur),
            # 2026: long call exercised, MARKED (Webull) stock leg.
            _tx(action='BUYSELL', date='2026-01-05', time='10:00:00',
                symbol=f'XYZ260320C00012000.{sfx}', quantity=1.0,
                price=1.0, net_amount=101.0, currency=cur),
            _tx(action='ASSIGN', date='2026-03-20', time='16:00:00',
                symbol=f'XYZ260320C00012000.{sfx}', quantity=-1.0,
                price=0.0, net_amount=0.0, currency=cur),
            _tx(action='ASSIGN', date='2026-03-20', time='16:00:01',
                symbol=und, quantity=100.0, price=12.0,
                net_amount=1200.0, currency=cur),
            _tx(action='BUYSELL', date='2026-04-01', time='10:00:00',
                symbol=und, quantity=-100.0, price=14.0,
                net_amount=1399.0, currency=cur),
        ]

    def test_canada(self):
        res = CanadaTaxRules().compute_gains(self._rows('TO', 'CAD'))
        by_year = {}
        for g in res['transactions']:
            if g.get('raw_gain') is None:
                continue
            y = (g.get('date_settle') or g['date'])[:4]
            by_year[y] = by_year.get(y, 0.0) + g['gain']
        self.assertAlmostEqual(by_year.get('2025', 0.0), 298.0, places=2,
                               msg=f"premium left 2025: {by_year}")
        self.assertAlmostEqual(by_year.get('2026', 0.0), 98.0, places=2,
                               msg=f"premium landed on 2026: {by_year}")

    def test_usa(self):
        res = USATaxRules().compute_gains(self._rows('US', 'USD'))
        by_year = {}
        for g in res['transactions']:
            if g.get('raw_gain') is None:
                continue
            y = (g.get('date_sold') or g.get('date'))[:4]
            by_year[y] = by_year.get(y, 0.0) + g['raw_gain']
        self.assertAlmostEqual(by_year.get('2025', 0.0), 298.0, places=2,
                               msg=f"premium left 2025: {by_year}")
        self.assertAlmostEqual(by_year.get('2026', 0.0), 98.0, places=2,
                               msg=f"premium landed on 2026: {by_year}")


def _gains(main, sheltered, **kw):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        r = run_gains(main, sheltered,
                      req=GainsRequest(country='canada', year=2025,
                                       taxable=True, **kw))
    perm = round(sum(g.get('permanently_disallowed', 0.0) or 0.0
                     for g in r['transactions']), 6)
    return r['summary']['total_gain'], r['summary']['total_disallowed'], perm


def _t(action, date, qty, acct, sym='XYZ.TO', price=10.0):
    return _tx(action=action, date=date, time='10:00:00', symbol=sym,
               quantity=float(qty), price=price,
               net_amount=abs(qty) * price, account=acct)


class TestOwnRegisteredMoveKeepsHolderBalances(unittest.TestCase):
    """S018-05 / G2-0: a registered-to-registered move of the owner's
    own shares (rrspA -> rrspB) is netted out of the wash context at the
    symbol level, but the s.54 still-held test runs PER HOLDER — so the
    receiving account looked short (a permanent denial was missed) and
    the sending account looked long (a denial was invented)."""

    MARGIN = [
        _t('BUYSELL', '2025-01-10', 100, 'margin', price=20.0),
        _t('BUYSELL', '2025-06-02', -100, 'margin'),     # loss 1,000
    ]

    def test_case_a_receiving_account_rebuys_and_holds(self):
        moved = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', -100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', 100, 'rrspB'),
            _t('BUYSELL', '2024-05-01', -100, 'rrspB'),
            _t('BUYSELL', '2025-06-10', 100, 'rrspB'),
        ]
        native = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspB'),
            _t('BUYSELL', '2024-05-01', -100, 'rrspB'),
            _t('BUYSELL', '2025-06-10', 100, 'rrspB'),
        ]
        self.assertEqual(_gains(self.MARGIN, native), (0.0, 1000.0, 1000.0))
        self.assertEqual(_gains(self.MARGIN, moved), (0.0, 1000.0, 1000.0),
                         "the netted move left rrspB short, so its "
                         "in-window rebuy was not 'still held'")

    def test_case_b_sending_account_round_trips_in_window(self):
        moved = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', -100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', 100, 'rrspB'),
            _t('BUYSELL', '2025-06-05', 100, 'rrspA'),
            _t('BUYSELL', '2025-06-20', -100, 'rrspA'),
        ]
        native = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspB'),
            _t('BUYSELL', '2025-06-05', 100, 'rrspA'),
            _t('BUYSELL', '2025-06-20', -100, 'rrspA'),
        ]
        self.assertEqual(_gains(self.MARGIN, native), (-1000.0, 0, 0.0))
        self.assertEqual(_gains(self.MARGIN, moved), (-1000.0, 0, 0.0),
                         "the netted move left rrspA holding the moved "
                         "shares, inventing a permanent denial")

    def test_g2_0_partial_sale_after_move(self):
        margin = [
            _t('BUYSELL', '2024-10-01', 100, 'margin', price=20.0),
            _t('BUYSELL', '2025-06-02', -100, 'margin'),
        ]
        moved = [
            _t('BUYSELL', '2024-11-01', 200, 'rrsp'),
            _t('TRANSFER', '2025-02-03', -200, 'rrsp'),
            _t('TRANSFER', '2025-02-03', 200, 'rrsp2'),
            _t('BUYSELL', '2025-03-03', -150, 'rrsp2'),
            _t('BUYSELL', '2025-06-09', 100, 'rrsp2'),
        ]
        g, dis, perm = _gains(margin, moved)
        self.assertAlmostEqual(perm, 1000.0, places=2)
        self.assertAlmostEqual(g, 0.0, places=2)


class TestSameStampTaxableBeforeRegistered(unittest.TestCase):
    """S018-06: a taxable rebuy and a TFSA buy at the SAME timestamp were
    ordered by the rows' content-hash id, so a one-cent change in the
    TFSA price decided whether the superficial loss was deferred into
    the taxable ACB or lost for good. At a tie the taxpayer's own
    (taxable) acquisition now comes first, then registered, then
    affiliated accounts — by account label, never by hash."""

    def _run(self, engine, sfx, cur, p):
        taxable = [
            _tx(action='BUYSELL', date='2025-01-06', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=100.0, price=20.0,
                net_amount=2000.0, currency=cur),
            _tx(action='BUYSELL', date='2025-03-03', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=-100.0, price=10.0,
                net_amount=1000.0, currency=cur),
            _tx(action='BUYSELL', date='2025-03-10', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=100.0, price=10.5,
                net_amount=1050.0, currency=cur),
            _tx(action='BUYSELL', date='2025-06-02', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=-100.0, price=12.0,
                net_amount=1200.0, currency=cur),
        ]
        tfsa = [
            _tx(action='BUYSELL', date='2025-03-10', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=100.0, price=p,
                net_amount=round(100 * p, 2), currency=cur,
                account='tfsa'),
        ]
        res = engine().compute_gains(taxable, sheltered_transactions=tfsa)
        gain = sum((g.get('gain') if g.get('gain') is not None
                    else g.get('raw_gain') or 0.0)
                   for g in res['transactions']
                   if g.get('raw_gain') is not None)
        perm = sum(g.get('permanently_disallowed') or 0.0
                   for g in res['transactions'])
        return round(gain, 2), round(perm, 2)

    def test_canada_same_stamp_is_deferred_whatever_the_price(self):
        seen = {self._run(CanadaTaxRules, 'TO', 'CAD', p / 100)
                for p in range(1045, 1065)}
        self.assertEqual(seen, {(-850.0, 0.0)},
                         "the row hash decided deferral vs permanent")

    def test_usa_same_stamp_is_deferred_whatever_the_price(self):
        seen = {self._run(USATaxRules, 'US', 'USD', p / 100)[1]
                for p in range(1045, 1065)}
        self.assertEqual(seen, {0.0},
                         "the row hash decided deferral vs permanent")


if __name__ == '__main__':
    unittest.main()

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


if __name__ == '__main__':
    unittest.main()

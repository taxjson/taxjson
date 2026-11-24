"""A trade larger than the position it closes, by a part at or under the
quantity tolerance: the part's share of the trade's money is never dropped
(backlog TJ-4990, TJ-1347). Under a half cent it is rounding and stays with
the disposition; material, it is booked as any trade past zero is, a
position the other way.

Synthetic data only; the ticker is made up.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.ca_engine_runs import _run
from _support.engine_ca_pool_books import trade

SYM = 'ZQD.US'


def _book(*rows):
    return [trade(d, q, n, sym=SYM) for d, q, n in rows]


def _sales(res):
    return [(g['qty'], round(float(g['cost']), 6), round(float(g['proceeds']), 6),
             round(float(g['gain']), 6), g['direction']) for g in res['transactions']]


def _held(res):
    return [(i['qty'], round(float(i['total_cost']), 6)) for i in res['inventory']]


def _money(res, book):
    """The proceeds of the book's sales that the result holds: the
    dispositions' and what an open short still carries."""
    return (sum(float(g['proceeds']) for g in res['transactions'])
            - sum(float(i['total_cost']) for i in res['inventory'] if i['qty'] < 0))


class TestASalePastATinyHolding(unittest.TestCase):
    """The entry's repro: 0.0000005 bought for 25, 0.000001 sold for 100."""

    @rule(R.CA_ACB_01)
    def test_the_excess_with_material_proceeds_is_a_short(self):
        book = _book(('2025-01-06', 0.0000005, -25), ('2025-07-03', -0.000001, 100))
        res, err = _run(book)
        self.assertEqual(_sales(res), [(5e-07, 25.0, 50.0, 25.0, 'LONG')])
        # The other half of the sale: a short of the excess at its proceeds.
        self.assertEqual(_held(res), [(-5e-07, -50.0)])
        self.assertAlmostEqual(_money(res, book), 100.0, places=6)
        self.assertEqual(err, '')

    @rule(R.CA_ACB_01)
    def test_the_short_is_covered_like_any_other(self):
        book = _book(('2025-01-06', 0.0000005, -25), ('2025-07-03', -0.000001, 100),
                     ('2025-08-05', 0.0000005, -30))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(5e-07, 25.0, 50.0, 25.0, 'LONG'),
                                       (5e-07, -50.0, -30.0, 20.0, 'SHORT')])
        self.assertEqual(_held(res), [])

    @rule(R.CA_ACB_01)
    def test_an_excess_that_is_rounding_stays_in_the_disposition(self):
        # The excess is 1e-11 units: 0.002 of the 100, under a half cent.
        book = _book(('2025-01-06', 0.0000005, -25), ('2025-07-03', -0.00000050001, 100))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(5e-07, 25.0, 100.0, 75.0, 'LONG')])
        self.assertEqual(_held(res), [])

    @rule(R.CA_ACB_01)
    def test_a_cover_past_a_tiny_short(self):
        book = _book(('2025-01-06', -0.0000005, 25), ('2025-07-03', 0.000001, -100))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(5e-07, -25.0, -50.0, -25.0, 'SHORT')])
        self.assertEqual(_held(res), [(5e-07, 50.0)])


class TestASalePastAnOrdinaryHolding(unittest.TestCase):
    """The same rule for any position: a part past it at or under the
    tolerance."""

    @rule(R.CA_ACB_01)
    def test_rounding_keeps_the_whole_proceeds(self):
        # 5e-7 of 100.0000005 units of a 3000 sale: 0.000015.
        book = _book(('2025-01-06', 100, -2500), ('2025-07-03', -100.0000005, 3000))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(100.0, 2500.0, 3000.0, 500.0, 'LONG')])
        self.assertEqual(_held(res), [])

    @rule(R.CA_ACB_01)
    def test_material_proceeds_are_kept_as_a_short(self):
        # The same part of a 3,000,000 sale is 0.015: over a half cent.
        book = _book(('2025-01-06', 100, -2500), ('2025-07-03', -100.0000005, 3000000))
        res, _err = _run(book)
        (qty, cost, proceeds, gain, side), = _sales(res)
        self.assertEqual((qty, cost, side), (100.0, 2500.0, 'LONG'))
        self.assertAlmostEqual(proceeds, 2999999.985, places=4)
        (short_qty, short_cost), = _held(res)
        self.assertLess(short_qty, 0)
        self.assertAlmostEqual(short_cost, -0.015, places=4)
        self.assertAlmostEqual(_money(res, book), 3000000.0, places=4)


class TestControls(unittest.TestCase):
    def test_a_sale_past_the_tolerance_is_unchanged(self):
        book = _book(('2025-01-06', 100, -2500), ('2025-07-03', -150, 4500))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(100.0, 2500.0, 3000.0, 500.0, 'LONG')])
        self.assertEqual(_held(res), [(-50.0, -1500.0)])

    def test_an_exact_close_is_unchanged(self):
        book = _book(('2025-01-06', 100, -2500), ('2025-07-03', -100, 3000))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(100.0, 2500.0, 3000.0, 500.0, 'LONG')])
        self.assertEqual(_held(res), [])

    def test_a_sale_of_exactly_a_tiny_holding_is_unchanged(self):
        book = _book(('2025-01-06', 0.0000005, -25), ('2025-07-03', -0.0000005, 100))
        res, _err = _run(book)
        self.assertEqual(_sales(res), [(5e-07, 25.0, 100.0, 75.0, 'LONG')])
        self.assertEqual(_held(res), [])


if __name__ == '__main__':
    unittest.main()

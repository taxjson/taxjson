"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.

Formerly: test_fix_a2_pins3_g2
"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"



@rule(R.CA_OPT_08)
@rule(R.US_OPT_05)
class TestAssignLegLagWindow(unittest.TestCase):
    """A2-0538 (core.py _MARKED_LEG_MAX_LAG_DAYS = 7): an assignment's
    marked stock leg dated 7 days after the option row takes its
    premium; 8 days after it is another assignment's leg, and the
    premium is reported unconsumed (both engines share the ledger).

    Formerly: test_fix_a2_pins3_g2.TestAssignLegLagWindow"""

    def _book(self, sx, leg_date):
        from _support.engine_option_assignment import _tx
        put, stk = f'XYZ251212P00012000.{sx}', f'XYZ.{sx}'
        return stk, [
            _tx('BUYSELL', '2025-11-03', put, -1, 1.99, 199),
            _tx('ASSIGN', '2025-12-12', put, 1, 0, 0, time='16:00:00'),
            _tx('ASSIGN', leg_date, stk, 100, 12, 1200, time='16:00:01'),
        ]

    def test_seven_days_pairs_eight_days_does_not(self):
        from _support.engine_option_assignment import _ENGINES, _run, _held_cost
        for country, eng, sx in _ENGINES:
            with self.subTest(country=country):
                stk, book = self._book(sx, '2025-12-19')
                res, err = _run(eng, book)
                self.assertEqual(_held_cost(res, stk), 1001.0)
                self.assertNotIn('unconsumed', err)
                stk, book = self._book(sx, '2025-12-20')
                res, err = _run(eng, book)
                self.assertEqual(_held_cost(res, stk), 1200.0)
                self.assertIn('unconsumed', err)



import os


if __name__ == '__main__':
    unittest.main()

"""The T1135's costs are added exactly and rounded half up to the cent
(backlog TJ-2791, TJ-2795, TJ-2796): a float total, and round() of a
float, decided the cent by the Python version and by the binary value
nearest an exact half cent, and float noise chose between two days
whose totals were equal.

Formerly: test_fix_t1135_exact_cent"""

import unittest

from taxjson.bin.taxjson_t1135 import walk_costs
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

CAD = {'currency': 'CAD'}


class TestT1135ExactCent(unittest.TestCase):

    @rule(R.CA_RPT_12)
    def test_a_half_cent_rounds_up(self):
        # 1.005 as a float is a little under 1.005: round() gave 1.00.
        rows = [tx('BUYSELL', '2024-01-02', 'QZCOIN', 1, 1.005, **CAD).to_dict()]
        f = walk_costs(rows, 2025, {'QZCOIN': 'USA'})
        self.assertEqual(f['max_total_cost'], 1.01)
        self.assertEqual(f['per_symbol']['QZCOIN']['max_cost'], 1.01)
        self.assertEqual(f['per_symbol']['QZCOIN']['year_end_cost'], 1.01)

    @rule(R.CA_RPT_12)
    def test_two_equal_days_show_the_first(self):
        # 0.3 held, then 0.1 + 0.2: the same total, which a float sum
        # made 0.30000000000000004 and the later day's.
        rows = [tx('BUYSELL', '2025-02-03', 'QZC', 1, 0.3, **CAD).to_dict(),
                tx('BUYSELL', '2025-03-03', 'QZC', -1, 0.3, **CAD).to_dict(),
                tx('BUYSELL', '2025-03-03', 'QZA', 1, 0.1, **CAD).to_dict(),
                tx('BUYSELL', '2025-03-03', 'QZB', 1, 0.2, **CAD).to_dict()]
        f = walk_costs(rows, 2025, {'QZA': 'USA', 'QZB': 'USA', 'QZC': 'USA'})
        self.assertEqual(f['max_total_cost'], 0.3)
        self.assertEqual(f['max_total_date'], '2025-02-03')


if __name__ == '__main__':
    unittest.main()

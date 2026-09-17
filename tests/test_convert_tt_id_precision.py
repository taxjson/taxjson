"""Regression: `taxjson-convert-tt`'s id-hash must use the same
precision strategy as `TaxTransaction.compute_id` so a `.tt` ↔ JSON
round-trip preserves transaction ids.

Pre-fix, `parse_tt_line` formatted monetary fields with `f"{x:.8f}"`
while `compute_id` (cycle 5 fix) uses `repr(float(x))`. The
mismatch silently:
  - collided sub-satoshi crypto quantities onto the same id (the
    very bug compute_id was changed to avoid),
  - produced different ids for the same transaction depending on
    whether it was loaded from .tt or directly from JSON, breaking
    dedup and wash-linkage on cross-format pipelines.
"""
import unittest

from taxjson.bin.taxjson_convert_tt import parse_tt_line
from taxjson.lib.core import TaxTransaction


class TestConvertTtIdMatchesComputeId(unittest.TestCase):
    def _tt_id(self, line):
        tx = parse_tt_line(line)
        return tx['id']

    def _tx_id(self, **kw):
        return TaxTransaction(**kw).compute_id()

    def test_round_trip_id_matches_compute_id(self):
        """A `.tt` row and its TaxTransaction equivalent must compute
        the same id. Pre-fix, they diverged because .tt used `:.8f`
        and compute_id uses `repr(float())`."""
        tt_line = ('BUYSELL 2025-01-15 09:30:00 AAPL.US 100.00000000 '
                   'USD 150.00000000 15000.00000 4.95000')
        tt_id = self._tt_id(tt_line)
        ref_id = self._tx_id(
            action='BUYSELL', date='2025-01-15', time='09:30:00',
            date_settle='2025-01-15', symbol='AAPL.US', quantity=100.0,
            currency='USD', price=150.0, net_amount=15000.0, fee=4.95,
            account='default',
        )
        self.assertEqual(tt_id, ref_id)

    def test_sub_satoshi_qty_does_not_collide(self):
        """Two sub-satoshi crypto quantities must produce DISTINCT
        ids. Pre-fix `:.8f` truncated both to `0.00000000` and they
        collided on the same hash — exactly the failure mode
        compute_id was changed to fix."""
        line_a = ('BUYSELL 2025-01-15 09:30:00 ETH 0.000000001 '
                  'USD 3000.00 0.000003 0.0')
        line_b = ('BUYSELL 2025-01-15 09:30:00 ETH 0.000000002 '
                  'USD 3000.00 0.000006 0.0')
        self.assertNotEqual(self._tt_id(line_a), self._tt_id(line_b))


if __name__ == '__main__':
    unittest.main()

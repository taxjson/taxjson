"""Batch C (cash books, the FX-on-cash ledger v2, the generic importer,
ALLOWLOSS): 2026-10 audit findings 51, 52, 53, 70, 87. All data
synthetic.

Formerly: test_fix_batch_c_cash"""
import unittest
from taxjson.lib import loss_override_note as LON


class TestAllowLossQuantity(unittest.TestCase):
    """#87: `1,5` was read as 15 units.

    Formerly: test_fix_batch_c_cash.TestAllowLossQuantity"""

    def _qty(self, tok):
        return LON.parse_line(f'ALLOWLOSS 2025-02-03 SAMP.US {tok} '
                             f'reason="synthetic"')["qty"]

    def test_decimal_comma_refused(self):
        for tok in ("1,5", "1,2345", "12,34", "$5", "(3)"):
            with self.assertRaisesRegex(ValueError,
                                        "is not a number of units sold"):
                self._qty(tok)

    def test_plain_and_grouped_accepted(self):
        self.assertEqual(self._qty("15"), 15.0)
        self.assertEqual(self._qty("2.5"), 2.5)
        self.assertEqual(self._qty("1,000"), 1000.0)
        self.assertEqual(self._qty("-3"), 3.0)


if __name__ == "__main__":
    unittest.main()

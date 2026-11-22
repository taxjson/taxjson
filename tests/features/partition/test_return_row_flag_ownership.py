"""Decisions of 2026-10-26, group `cents`: the figures the return files
(each sale's cents) shown where the engine's unrounded totals were.

- TJ-0208 `tjs carryover`'s NET GAIN(LOSS) for the project year is
  the RETURN row of `tjs sum`, so its carryforward balance is the one
  close-year's lock and the estimate carry forward; a prior year's lock
  gives the RETURN row close-year recorded;
- TJ-0209 the default `sum` view says in one line that SUBTOTAL (TOTAL in
  the one-table layout) adds the account rows and RETURN each sale's
  cents, when the two differ;
- TJ-0212 close-year's CLOSED block and `years --json` show income as
  `sum`'s taxable SUBTOTAL adds it (each account's cents);
- TJ-0226 `estimate --json` carries `losses_carried_back` and
  `losses_carried_forward`; `losses_unused` stays the figure before
  the carry-back.

The project: two taxable accounts in a years layout (inputs/ beside
2023/). margin: a 100.00 loss and one unit sold of each of three 3-unit
lots (each row rounds its loss up by a third of a cent); cash: a 10.00
gain, to the cent. RETURN -93.99, the engine's total -94.00. Each
account a dividend of 10.004 (each account 10.00, the lock's total
20.01); [carryover] claimed carries 50.00 of the loss back to 2022. Synthetic data only (made-up tickers, amounts
under a thousand).

Formerly: test_decisions_2026_10_26_cents
"""
import unittest


class TestOwnership(unittest.TestCase):
    """Formerly: test_decisions_2026_10_26_cents.TestOwnership"""
    def test_tj0208_return_row_flag_is_one_countrys(self):
        """The ledger's --return-row is the net-capital-loss ledger's
        (one figure); the other ledger keeps its term split."""
        from taxjson.lib import country as C
        self.assertEqual(C.FLAG_COUNTRY["--return-row"], C.CANADA)
        self.assertTrue(C.flag_country_problems(
            C.USA, {"--return-row": ["2023=1.00"]}))


if __name__ == "__main__":
    unittest.main()

"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.

Formerly: test_fix_a2_pins3_g2
"""
import unittest


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"



class TestRbcCoverageBoundaries(unittest.TestCase):
    """A2-1569 (rbc_direct.py rbc_coverage_messages): the exact boundary
    days — today == Dec 31 (the year is still open: no finding), an
    export taken ON Dec 31 (the 'taken on Dec 31 itself' note), and an
    export taken exactly on the year-end posting day (June 30 of the
    next year: it holds the book-cost adjustments, no R1-85 note).

    Formerly: test_fix_a2_pins3_g2.TestRbcCoverageBoundaries"""

    @staticmethod
    def _msgs(as_of, today):
        from datetime import date
        from taxjson.lib.brokerages.rbc_direct import rbc_coverage_messages
        return rbc_coverage_messages(
            [('rbc_2025.csv', as_of, ['2025-03-03', '2025-11-03'])],
            2025, listings=None, today=date.fromisoformat(today))

    def test_today_is_dec_31_the_year_is_still_open(self):
        self.assertEqual(self._msgs('2025-12-01', '2025-12-31'), [])
        self.assertTrue(any('ATTENTION' in m for m in
                            self._msgs('2025-12-01', '2026-01-01')))

    def test_export_taken_on_dec_31_gets_the_same_day_note(self):
        msgs = self._msgs('2025-12-31', '2026-07-15')
        self.assertTrue(any('taken on 2025-12-31 itself' in m
                            for m in msgs), msgs)
        self.assertFalse(any('ATTENTION' in m for m in msgs), msgs)

    def test_export_taken_on_the_posting_day_holds_the_adjustments(self):
        on_day = self._msgs('2026-06-30', '2026-07-15')
        self.assertFalse(any('year-end book-cost' in m for m in on_day),
                         on_day)
        day_before = self._msgs('2026-06-29', '2026-07-15')
        self.assertTrue(any('year-end book-cost' in m for m in day_before),
                        day_before)



import os


if __name__ == '__main__':
    unittest.main()

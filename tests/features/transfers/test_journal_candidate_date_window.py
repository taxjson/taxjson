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


class TestJournalCandidateDateWindow(unittest.TestCase):
    """A2-0538 (transfer_windows.py _JOURNAL_CANDIDATE_PAD_DAYS = 7): an out-leg
    of X and an in-leg of Y of equal quantity 7 days apart print the
    unmapped cross-listing journal NOTE; 8 days apart they do not.

    Formerly: test_fix_a2_pins3_g2.TestJournalCandidateDateWindow"""

    NOTE = "possible unmapped cross-listing journal"

    def _err(self, in_date):
        from _support.transfer_shelter_drop import _tx, _drop
        shel = [_tx('TRANSFER', '2026-06-15', 'BTG.US', -500.0),
                _tx('TRANSFER', in_date, 'BTO.TO', 500.0)]
        return _drop(shel, [])[2]

    def test_seven_days_pairs_eight_does_not(self):
        self.assertIn(self.NOTE, self._err('2026-06-22'))
        self.assertNotIn(self.NOTE, self._err('2026-06-23'))



import os


if __name__ == '__main__':
    unittest.main()

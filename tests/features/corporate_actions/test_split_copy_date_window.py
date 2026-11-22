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


class TestSplitCopyDateWindow(unittest.TestCase):
    """A2-0538 (corporate_timeline.py SPLIT_DATE_WINDOW_DAYS = 7): two
    copies of one split (same symbol and ratio) dated up to 7 days apart
    are one event; 8 days apart they are two.

    Formerly: test_fix_a2_pins3_g2.TestSplitCopyDateWindow"""

    def test_seven_days_is_one_event_eight_is_two(self):
        from taxjson.lib.corporate_timeline import split_seen
        seen = set()
        self.assertIsNone(split_seen(seen, 'QZK.US', '2025-06-11', 10, ''))
        self.assertEqual(split_seen(seen, 'QZK.US', '2025-06-18', 10, ''),
                         '2025-06-11')
        seen = set()
        self.assertIsNone(split_seen(seen, 'QZK.US', '2025-06-11', 10, ''))
        self.assertIsNone(split_seen(seen, 'QZK.US', '2025-06-19', 10, ''))


import os


if __name__ == '__main__':
    unittest.main()

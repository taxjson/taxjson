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


import os


class TestUsRadarBlockedDefinition(unittest.TestCase):
    """A2-1597 hunk 3a4e799_332 (taxjson_wash_radar.py _US_DEFINITIONS):
    BLOCKED disallows the loss on as many shares as you buy.

    Formerly: test_fix_a2_pins3_g2.TestUsRadarBlockedDefinition"""

    def test_blocked_definition_is_per_share(self):
        from taxjson.bin.taxjson_wash_radar import _US_DEFINITIONS
        blocked = [d for d in _US_DEFINITIONS if d.startswith('BLOCKED:')]
        self.assertEqual(len(blocked), 1)
        self.assertIn('disallows the loss on as many shares as you buy',
                      blocked[0])


if __name__ == '__main__':
    unittest.main()

"""Re-audit-2 test pins, group G2 (parsers and message fixes).

Each test pins a fix that held on the audited commit but that no test
failed on when reverted (A2-0176, A2-0521, A2-0538, A2-0897, A2-1569,
A2-1582, A2-1612, A2-0894, A2-1597). Every fixture is SYNTHETIC: fake
account ids, invented tickers and amounts.

Formerly: test_fix_a2_pins3_g2
"""
import tempfile
import unittest
from pathlib import Path


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"



# ------------------------------------------------- A2-1597 message fixes
#
# Each fix below changed only a user-facing message, and reverting it
# left the suite green. Hunk ids are the audit's (commit_hunk).

import json
import os


class TestFeesNonOptionLabel(unittest.TestCase):
    """A2-1597 hunk 4c989a2_300 (taxjson_fees.py render_text, the
    converted split section): 'Non-option vs option fees' and the
    'non-option' column — a coin exchange's fees are not 'stocks'.

    Formerly: test_fix_a2_pins3_g2.TestFeesNonOptionLabel"""

    def test_converted_report_labels_non_option(self):
        from _support.view_report_tools import _tool, _row
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / 'work'
            w.mkdir()
            (w / 'c.json').write_text(json.dumps({
                'metadata': {'source_brokerage': 'questrade'},
                'transactions': [_row('2025-03-03', 'ENB.TO', 10, -500.0,
                                      commission=9.95)]}))
            rates = Path(td) / 'rates.txt'
            rates.write_text('2025-03-03 00:00:00 USD CAD 1.40 test\n')
            r = _tool('taxjson_fees', '--cache', w, '--to', 'CAD',
                      '--rates', rates)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Non-option vs option fees\n', r.stdout)
        self.assertRegex(r.stdout, r'\nBROKERAGE +NON-OPTION +OPTIONS\n')
        self.assertNotIn(' stocks ', r.stdout)


if __name__ == '__main__':
    unittest.main()

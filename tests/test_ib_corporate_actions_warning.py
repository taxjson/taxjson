"""Tests for the IB Corporate Actions unhandled-row warning.

The IB extractor parses SPLIT and Spinoff Corporate Action rows; other
event types (Merger/Acquisition, name change, etc.) are too varied to
auto-translate safely. Instead of silently dropping them — which once
caused a $14k gain to vanish from a sheltered-account report — the
extractor now emits a stderr warning naming the affected tickers so
the user can add manual TRANSFER entries.
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib.brokerages.ib_extractor import IbBrokerage


# Minimal IB statement covering a Merger/Acquisition that should trigger
# the warning (not parsed as SPLIT or Spinoff).
_IB_WITH_MERGER = '''\
Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-10-29,"2025-10-28, 20:25:00","RGLD.CAD(10000001) Merged(Acquisition) WITH RGLD 1 for 1 (RGLD.CAD, ROYAL GOLD INC, US0000000002)",-100,0,-25840.0,0
Corporate Actions,Data,Stocks,USD,2025-10-30,"2025-10-29, 20:25:00","RGLD.CAD(10000001) Merged(Acquisition) WITH RGLD 1 for 1 (RGLD, ROYAL GOLD INC, US0000000002)",100,0,18550.0,0
'''

# IB statement with a SPLIT — known-handled, should NOT trigger warning.
_IB_WITH_SPLIT_ONLY = '''\
Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2025-06-15,"2025-06-15, 09:30:00","NVDA (US67066G1040) Split 10 for 1 (NVDA, NVIDIA CORP, US67066G1040)",900,0,0,0
'''


def _run_parser_capture_stderr(content):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    buf = io.StringIO()
    try:
        with patch.object(sys, 'stderr', buf):
            txs = IbBrokerage().parse_file(Path(f.name))
        return txs, buf.getvalue()
    finally:
        os.remove(f.name)


class TestUnhandledCorporateActionWarning(unittest.TestCase):
    def test_merger_emits_warning(self):
        _, stderr = _run_parser_capture_stderr(_IB_WITH_MERGER)
        self.assertIn('unhandled Corporate Action', stderr)
        self.assertIn('RGLD', stderr)
        # Hint at the manual workaround.
        self.assertIn('TRANSFER', stderr)
        self.assertIn('*_in.tt', stderr)

    def test_split_only_no_warning(self):
        txs, stderr = _run_parser_capture_stderr(_IB_WITH_SPLIT_ONLY)
        # SPLIT was parsed.
        splits = [t for t in txs if t.get('action') == 'SPLIT']
        self.assertEqual(len(splits), 1)
        # No warning.
        self.assertNotIn('unhandled', stderr)

    def test_warning_counts_rows(self):
        """If multiple unhandled rows are present, the warning includes
        the total row count and a deduplicated ticker list."""
        _, stderr = _run_parser_capture_stderr(_IB_WITH_MERGER)
        # Two rows in the fixture: one CAD, one USD side of the same merger.
        self.assertIn('2 unhandled', stderr)


if __name__ == '__main__':
    unittest.main()
